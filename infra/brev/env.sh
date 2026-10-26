# Source on custos-gpu before any bench/demo run: every Lightning role -> self-hosted vLLM (GPUs 0-6),
# embeddings -> self-hosted llama-nemotron-embed-1b-v2 (GPU 7); attacker/evaluator stay on hosted Nemotron 3 Ultra.
set -a
. "$HOME/vllm_urls.env"
CUSTOS_ROUTE_AGENT=vllm; CUSTOS_ROUTE_EXTRACTOR=vllm; CUSTOS_ROUTE_JUDGE=vllm; CUSTOS_ROUTE_SUMMARISER=vllm; CUSTOS_ROUTE_BASELINE=vllm
CUSTOS_ROUTE_EMBED=embedsrv; CUSTOS_MODEL_EMBED=nvidia/llama-nemotron-embed-1b-v2; EMBED_BASE_URL=http://127.0.0.1:8100/v1
set +a
