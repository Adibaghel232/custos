#!/usr/bin/env bash
# Zero-downtime replacement of the running vLLM servers with OMP-capped ones.
# Why: on a 240-core host every vLLM EngineCore spawned a full-size OpenMP pool whose threads spin (~50 cores per
# engine), starving the bench harness. Each step starts a capped server on a free GPU (port 8010+gpu), then stops one
# old server and forwards its port (8000+i, which the running harness uses) to the new one with socat (~1 s gap; the
# client retries). The GPU it frees hosts the next new server. The last freed GPU gets an extra server.
# Usage: bash infra/brev/hotswap.sh "4 5 6 0 1 2 3" 7
set -uo pipefail
ORDER=${1:-"4 5 6 0 1 2 3"}
FREE=${2:-7}
IMAGE=vllm/vllm-openai:v0.27.1
MODEL=nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-BF16
SERVED=nvidia/nemotron-3.5-lightning-30b-a3b
HF_CACHE=$HOME/.cache/huggingface
log() { echo "[$(date '+%T')] $*" | tee -a "$HOME/hotswap.log"; }

start_new() {   # gpu -> starts vllm<10+gpu> on port 8010+gpu and waits until it answers a chat request
  local g=$1 port=$((8010 + $1)) name=vllm$((10 + $1)) util=0.90
  [ "$g" = 7 ] && util=0.85                       # GPU 7 also hosts the embedding server
  docker rm -f "$name" >/dev/null 2>&1
  docker run -d --name "$name" --restart unless-stopped --gpus "\"device=$g\"" --ipc=host -p 127.0.0.1:$port:8000 \
    -v "$HF_CACHE:/root/.cache/huggingface" -e HF_HUB_OFFLINE=1 \
    -e OMP_NUM_THREADS=8 -e MKL_NUM_THREADS=8 -e OPENBLAS_NUM_THREADS=8 \
    "$IMAGE" --model "$MODEL" --served-model-name "$SERVED" --max-model-len 32768 --max-num-seqs 128 \
    --gpu-memory-utilization $util --enable-prefix-caching --async-scheduling \
    --mamba-backend flashinfer --mamba-ssm-cache-dtype float16 \
    --enable-mamba-cache-stochastic-rounding --mamba-cache-philox-rounds 5 \
    --reasoning-parser nemotron_v3 --tool-call-parser qwen3_coder --enable-auto-tool-choice >/dev/null
  for t in $(seq 1 120); do
    if curl -sf -m 60 "http://127.0.0.1:$port/v1/chat/completions" -H 'Content-Type: application/json' \
         -d "{\"model\":\"$SERVED\",\"messages\":[{\"role\":\"user\",\"content\":\"Say ok.\"}],\"max_tokens\":5}" >/dev/null; then
      log "$name ready on :$port (GPU $g)"; return 0; fi
    if ! docker ps --format '{{.Names}}' | grep -qx "$name"; then log "$name died"; docker logs --tail 30 "$name"; return 1; fi
    sleep 10
  done
  log "$name not ready after 20 min"; return 1
}

for old in $ORDER; do
  start_new "$FREE" || exit 1
  newport=$((8010 + FREE))
  docker update --restart=no "vllm$old" >/dev/null
  docker stop -t 5 "vllm$old" >/dev/null && docker rename "vllm$old" "retired_vllm$old"
  tmux kill-session -t "px$old" 2>/dev/null
  tmux new -d -s "px$old" "socat TCP-LISTEN:$((8000 + old)),bind=127.0.0.1,reuseaddr,fork TCP:127.0.0.1:$newport"
  sleep 2
  if curl -sf -m 30 "http://127.0.0.1:$((8000 + old))/v1/models" >/dev/null; then log "port $((8000 + old)) -> $newport OK"
  else log "port $((8000 + old)) proxy FAILED"; fi
  FREE=$old
done
start_new "$FREE" && log "extra server on GPU $FREE at :$((8010 + FREE))"
log "done"
