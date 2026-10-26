#!/usr/bin/env bash
# Serve Nemotron 3.5 Lightning (30B-A3B, BF16) with vLLM on Brev A100-80GB GPUs (hackathon rule: A100 or L40S).
# One data-parallel replica per GPU. Idempotent: re-running replaces the container.
# Recipe: NVIDIA model card, "1x H100/A100 (vLLM)"; --served-model-name matches the build.nvidia.com id so the
# same configs/settings.yaml works against hosted and self-hosted endpoints.
set -euo pipefail

MODEL=${MODEL:-nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-BF16}
SERVED=${SERVED:-nvidia/nemotron-3.5-lightning-30b-a3b}
IMAGE=${IMAGE:-vllm/vllm-openai:v0.27.1}
PORT=${PORT:-8000}
MAX_LEN=${MAX_LEN:-32768}          # our prompts are short; smaller context = more KV cache = more concurrency
NGPU=$(nvidia-smi -L | wc -l)

echo "[vllm_up] GPUs: $NGPU  model: $MODEL  image: $IMAGE"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
sudo docker pull "$IMAGE" >/dev/null
sudo docker rm -f vllm >/dev/null 2>&1 || true
mkdir -p "$HOME/.cache/huggingface"

sudo docker run -d --name vllm --restart unless-stopped --gpus all --ipc=host -p 127.0.0.1:${PORT}:8000 \
  -v "$HOME/.cache/huggingface:/root/.cache/huggingface" \
  ${HF_TOKEN:+-e HF_TOKEN=$HF_TOKEN} \
  "$IMAGE" \
  --model "$MODEL" --served-model-name "$SERVED" \
  --data-parallel-size "$NGPU" \
  --max-model-len "$MAX_LEN" --max-num-seqs 128 \
  --enable-prefix-caching --async-scheduling \
  --mamba-backend flashinfer --mamba-ssm-cache-dtype float16 \
  --enable-mamba-cache-stochastic-rounding --mamba-cache-philox-rounds 5 \
  --reasoning-parser nemotron_v3 --tool-call-parser qwen3_coder --enable-auto-tool-choice

echo "[vllm_up] container started; waiting for http://127.0.0.1:${PORT}/v1/models (first start downloads ~60 GB)"
for i in $(seq 1 360); do
  if curl -sf "http://127.0.0.1:${PORT}/v1/models" >/dev/null; then echo "[vllm_up] READY after ~$((i*10))s"; exit 0; fi
  if ! sudo docker ps --format '{{.Names}}' | grep -q '^vllm$'; then echo "[vllm_up] container exited:"; sudo docker logs --tail 50 vllm; exit 1; fi
  sleep 10
done
echo "[vllm_up] TIMEOUT (1h). Last logs:"; sudo docker logs --tail 50 vllm; exit 1
