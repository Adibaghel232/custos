#!/usr/bin/env bash
# Serve Nemotron 3.5 Lightning (30B-A3B, BF16) with vLLM on Brev A100-80GB GPUs (hackathon rule: A100 or L40S).
# One independent server per GPU (ports 8000+i) — a crash on one GPU never takes down the others; the Custos client
# round-robins over VLLM_BASE_URL="http://127.0.0.1:8000/v1,http://127.0.0.1:8001/v1,...".
# Recipe: NVIDIA model card "1x H100/A100 (vLLM)". --served-model-name matches the build.nvidia.com id so the same
# configs/settings.yaml works against hosted and self-hosted endpoints.
# Usage: GPUS="0 1 2 3 4 5 6 7" bash infra/brev/vllm_up.sh
set -euo pipefail

MODEL=${MODEL:-nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-BF16}
SERVED=${SERVED:-nvidia/nemotron-3.5-lightning-30b-a3b}
IMAGE=${IMAGE:-vllm/vllm-openai:v0.27.1}
MAX_LEN=${MAX_LEN:-32768}          # our prompts are short; smaller context = more KV cache = more concurrency
GPUS=${GPUS:-$(nvidia-smi --query-gpu=index --format=csv,noheader | tr '\n' ' ')}
HF_CACHE=${HF_CACHE:-$HOME/.cache/huggingface}
DOCKER="docker"; docker ps >/dev/null 2>&1 || DOCKER="sudo docker"

echo "[vllm_up] GPUs: $GPUS  model: $MODEL  image: $IMAGE"
$DOCKER pull -q "$IMAGE"
mkdir -p "$HF_CACHE"

# 1) download weights once (all servers share the cache)
$DOCKER run --rm -v "$HF_CACHE:/root/.cache/huggingface" ${HF_TOKEN:+-e HF_TOKEN=$HF_TOKEN} --entrypoint huggingface-cli \
  "$IMAGE" download "$MODEL" >/tmp/vllm_download.log 2>&1 || { echo "[vllm_up] download failed"; tail -20 /tmp/vllm_download.log; exit 1; }
echo "[vllm_up] weights cached"

# 2) one server per GPU
URLS=()
for i in $GPUS; do
  PORT=$((8000 + i))
  $DOCKER rm -f "vllm$i" >/dev/null 2>&1 || true
  $DOCKER run -d --name "vllm$i" --restart unless-stopped --gpus "\"device=$i\"" --ipc=host -p 127.0.0.1:${PORT}:8000 \
    -v "$HF_CACHE:/root/.cache/huggingface" -e HF_HUB_OFFLINE=1 \
    "$IMAGE" \
    --model "$MODEL" --served-model-name "$SERVED" \
    --max-model-len "$MAX_LEN" --max-num-seqs 128 \
    --enable-prefix-caching --async-scheduling \
    --mamba-backend flashinfer --mamba-ssm-cache-dtype float16 \
    --enable-mamba-cache-stochastic-rounding --mamba-cache-philox-rounds 5 \
    --reasoning-parser nemotron_v3 --tool-call-parser qwen3_coder --enable-auto-tool-choice >/dev/null
  URLS+=("http://127.0.0.1:${PORT}/v1")
done

# 3) wait for all
for i in $GPUS; do
  PORT=$((8000 + i))
  for t in $(seq 1 180); do
    if curl -sf "http://127.0.0.1:${PORT}/v1/models" >/dev/null; then echo "[vllm_up] GPU $i ready on :$PORT"; break; fi
    if ! $DOCKER ps --format '{{.Names}}' | grep -qx "vllm$i"; then echo "[vllm_up] vllm$i exited:"; $DOCKER logs --tail 40 "vllm$i"; exit 1; fi
    sleep 10
  done
done
IFS=,; echo "VLLM_BASE_URL=${URLS[*]}" | tee "$HOME/vllm_urls.env"
