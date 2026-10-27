# Custos: chain of custody for agent memory

**AEGIS 2026 finals · PS-04 Agent Memory Governance Evaluation · Team Charlies (Aditya Baghel, Karan Singh)**

AI agents now remember things between sessions. An attacker no longer needs to break the agent: one false memory,
planted by an email or a web page, can trigger a wrong payment many sessions later, when no single step looks wrong
(OWASP ASI06 *Memory & Context Poisoning*, MITRE ATLAS AML.T0080).

**Custos** is a memory gateway that treats every memory as evidence with a chain of custody:

| Stage | What Custos does |
|---|---|
| **Write** | Stamps provenance on every memory (source, principal, parents, raw-content hash) and a trust tier from the *channel* it came through: T0 outside content (web, email), T1 tools/docs, T2 the user, T3 systems of record (ERP), T4 policy. Signs it into a hash-chained Ed25519 ledger. Extracts claims with Nemotron and checks them (key conflict, tier-aware embedding conflict → Nemotron NLI, grounding, drift, instruction detection, SoR verification, value fingerprinting). Policy (Python + Rego parity) decides commit / version / quarantine / reject. A lower tier can never overrule a higher one. |
| **Read** | Tenant / owner / purpose / sensitivity filter **before** vector search. Low-tier memories are rendered as quoted data, never as instructions; the agent is told which tier wins on conflict. |
| **Gate** | Before a high-risk tool call (pay, send, share, grant), per-argument taint: where did each argument value come from? Low-tier or quarantined origins → *ask* (with the source named) or *block*; leave-one-out re-planning finds the decisive memory. |
| **Recover** | One-click taint-closure rollback over the provenance graph, re-derivation, an exposure report from the read log, and DPDP-style erasure through derived data. |

The same gate and memory interface sit in front of an unchanged LangGraph agent, so every defence is compared on the
same agent, model, tools and attacks.

## Results

All numbers come from code output in `results/final/` and `runs/external_*/summary.json` (see `docs/CLAIMS.md` for the
map from every deck number to its source). Agent and checkers: **Nemotron 3.5 Lightning** (30B-A3B, vLLM on Brev
**A100-80GB**); adaptive attacker and independent judge: **Nemotron 3 Ultra**; embeddings: **llama-nemotron-embed**.

<!-- RESULTS:BEGIN -->
**Custos-Bench** (11 attack families + 2 laundering variants, finance agent, Nemotron 3.5 Lightning; 95% Wilson CIs):

| Defence | Attack success (ASR) | Injection success (ISR) | Benign task success |
|---|---|---|---|
| Undefended (LangGraph store) | 27.2% [24.9%–29.7%] (n=1,300) | 80.9% [78.7%–83.0%] (n=1,300) | 95.7% [92.2%–97.6%] (n=230) |
| PI filter (NemoGuard + content safety + PromptArmor-style) | 9.4% [7.9%–11.1%] (n=1,293) | 50.0% [47.2%–52.7%] (n=1,293) | 87.4% [82.5%–91.1%] (n=230) |
| A-MemGuard-style | 24.5% [22.3%–27.0%] (n=1,300) | 78.0% [75.7%–80.2%] (n=1,300) | 88.7% [84.0%–92.2%] (n=230) |
| **Custos** | 0.2% [0.0%–0.6%] (n=1,228) | 4.2% [3.2%–5.4%] (n=1,228) | 92.6% [88.4%–95.3%] (n=229) |

Custos also: adaptive attacker (Nemotron 3 Ultra, up to K rewrites) ASR 0.0% [0.0%–4.6%] (n=80); cross-tenant leakage 0/10,000 probes; false quarantine of legitimate updates 11.5% [6.7%–19.1%] (n=104); LongMemEval utility change -11.2 pts; LoCoMo accuracy 21.3% vs 21.3% undefended (n=150); stored claims unsupported by their source (independent judge) 13.1% [10.1%–16.9%] (n=374) vs 0.8% for plain summaries.

**AgentDojo** v1.2.1 banking (public; official tasks, `important_instructions` attack and scoring):

| Defence | Attack success | Utility under attack | Benign utility |
|---|---|---|---|
| None | 52.1% (n=144) | 53.5% | 56.2% |
| Spotlighting | 48.6% (n=144) | 50.7% | 50.0% |
| PI filter (NVIDIA detectors) | 0.0% (n=144) | 31.2% | 50.0% |
| **Custos gate** (cautious user) | 0.0% (n=144) | 52.8% | 56.2% |
| Custos gate, user approves every ask | 52.8% (n=144) | 54.9% | 56.2% |

**PoisonedRAG** NQ (public; 100 targets × 5 attacker passages; held out while tuning):

| Defence | ASR, trusted record in memory | ASR, poison only |
|---|---|---|
| Undefended (LangGraph store) | 74.0% (n=200) | 99.0% (n=200) |
| PI filter (NemoGuard + content safety + PromptArmor-style) | 74.0% (n=200) | 99.0% (n=200) |
| A-MemGuard-style | 37.0% (n=200) | 83.0% (n=200) |
| **Custos** | 3.0% (n=200) | 76.5% (n=200) |
<!-- RESULTS:END -->

**Limits we state up front.** Provenance is not fact-checking: with no trusted record to compare against, Custos
cannot tell which outside fact is true (PoisonedRAG "poison only" stays high for every defence). Low-trust updates that
contradict a trusted record wait for confirmation (review load). On long personal chats (LongMemEval) about half of the
summary claims fail the strict grounding rule that defends against summary laundering, so they are not stored and
recall accuracy drops; cite-aware summarisation is the next step. The gate escalates; a user who approves every request
gets no protection from it. Our own benchmark is synthetic (one finance domain); the two public benchmarks are there so
our numbers do not rest only on attacks we wrote.

## Repository

```
configs/                 settings.yaml (models, endpoints, thresholds), policy/custos.rego, tool registry, bench grids
src/custos/              the gateway: provenance, tiers, ledger, extract, checks/, policy/, write, read, gate, recover, api/
src/agent/               LangGraph finance agent, finance world + tools, memory interface (undefended vs Custos)
src/custos_bench/        Custos-Bench: 11 attack families + laundering variants, adaptive attacker, D0-D3 + ablations,
                         harness, 14 metrics with Wilson CIs, Memory Governance Score
scripts/                 eval_poisonedrag.py, eval_agentdojo.py (public benchmarks), build_deck.py, demo + video tooling
ui/                      React demo console (Arena, Inspector: custody graph, gate card, rollback, ledger; Bench)
infra/brev/              vLLM serving on Brev A100s, GPU autopilot queue
tests/unit/              offline tests (fake LLM + hash embedder)
```

## Quick start

```bash
uv sync --extra dev                      # Python 3.12
uv run pytest -q                         # offline unit tests
```

Models are NVIDIA-only (`custos/compliance.py` refuses anything else). Put `NVIDIA_API_KEY` in `.env` for
build.nvidia.com, or serve Nemotron yourself:

```bash
GPUS="0 1 2 3" bash infra/brev/vllm_up.sh             # one vLLM server per A100, writes ~/vllm_urls.env
source infra/brev/env.sh                               # route agent/extractor/judge to the local servers
python scripts/demo_golden_path.py --llm live --gateway real --acts 2 --sides D0 D3 --seed 7
```

Demo console on the recorded live run (no GPU needed):

```bash
cd ui && npm ci && npm run build && cd ..
python scripts/video/serve_demo.py --act 2 --port 8080   # open http://127.0.0.1:8080
```

Benchmarks:

```bash
custos-bench snapshot build --defence D0 && custos-bench snapshot build --defence D3
custos-bench run configs/bench/main.yaml --out runs/main --concurrency 64      # also ablation / utility / adaptive
custos-bench metrics runs && custos-bench report runs
python scripts/eval_poisonedrag.py --out runs/external_poisonedrag             # data/raw/nq.json from PoisonedRAG
python scripts/eval_agentdojo.py --out runs/external_agentdojo                 # needs `pip install agentdojo`
python scripts/build_deck.py --results results/final
```

## Standards map

OWASP ASI06 Memory & Context Poisoning · MITRE ATLAS AML.T0080 AI Agent Context Poisoning · NIST AI 600-1 §2.8
("clear chain of custody") · India DPDP Act §12 / §8(7) erasure through derived data.

## Acknowledgements

PoisonedRAG (Zou et al., USENIX Security 2025) and AgentDojo (Debenedetti et al., NeurIPS 2024) for their public
attack sets; LongMemEval for the utility subset; NVIDIA for Nemotron models and Brev A100 credits.
