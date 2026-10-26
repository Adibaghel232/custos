# Custos — project instructions for Claude sessions and agents

AEGIS 2026 finals (Infosys × IISc × NVIDIA), PS-04 "Agent Memory Governance Evaluation", team Charlies.
**Deadline: Tue 27 Oct 2026, end of day IST.** Read [PLAN.md](PLAN.md) first, then [STATUS.md](STATUS.md), then the
section of [docs/SPEC.md](docs/SPEC.md) for your task. Demo/deck/video design: [docs/DEMO_DECK_PLAN.md](docs/DEMO_DECK_PLAN.md).

## Hard rules
- **NVIDIA models only.** Every LLM call goes through `custos.llm.get_llm().chat(role, ...)`, and every embedding goes through
  `custos.embed.get_embedder()`. Model ids live ONLY in `configs/settings.yaml` (roles: agent, extractor, judge, summariser,
  baseline, attacker, evaluator, embed, ...). `custos/compliance.py` rejects non-NVIDIA / non-Nemotron ids. Never import another
  provider's SDK or model.
- **GPUs: A100 or L40S only** (hackathon rule). We use Brev A100-80GB with vLLM serving `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-BF16`
  under `--served-model-name nvidia/nemotron-3.5-lightning-30b-a3b`. Route roles to it with `CUSTOS_ROUTE_<ROLE>=vllm` + `VLLM_BASE_URL`.
- **Frozen interfaces:** `src/custos/types.py`, `src/custos/llm.py`, `src/custos/embed.py`, `src/custos/settings.py`. Don't change their
  public signatures; if you must, say so explicitly in your final report.
- **Tests run offline:** use `custos.llm.FakeLLM` + `custos.embed.HashEmbedder` (`set_llm`, `set_embedder`), never the network.
  Run: `uv run pytest -q` (Windows laptop: `.venv/Scripts/python -m pytest -q`).
- **Every reported number comes from code output** (`runs/<run>/metrics.json` → `results/final/`). Never type numbers into the deck by hand.
- **Secrets:** `.env` holds `NVIDIA_API_KEY`/`NGC_API_KEY`; it is git-ignored. Never print, log or commit keys.
- Honest framing: if something is cut or partial, the deck says so (see SPEC §1 "Deck line if cut").

## Layout
```
configs/settings.yaml      models, endpoints, routing, thresholds
src/custos/                gateway: types, settings, compliance, llm, embed, ledger, tiers, extract, checks/, policy/,
                           write, read, gate, recover, store/, api/
src/agent/                 LangGraph agent, finance world + tools, memory interface, summariser, user simulator
src/custos_bench/          attacks (f01–f11 + laundering), defences (D0–D3 + ablations), harness, metrics, charts
ui/                        React demo console (Vite + TS)
infra/brev/                Brev / vLLM scripts
tests/unit                 offline tests
docs/                      SPEC.md, DEMO_DECK_PLAN.md, round1/ (original deck + FAQ)
```

## Machines
- **Laptop (Windows)**: where the code was first written. Git Bash + PowerShell; python at `.venv/Scripts/python.exe`.
- **Lab PC `10.81.30.68`** (user rohitr2, Ubuntu 26.04, no sudo/docker): control node. Tools in `~/.local/bin` (uv, node 22, opa, brev).
  Repo at `~/custos`. Long jobs run in tmux.
- **Brev A100-80GB** (via `brev` CLI on the lab PC): vLLM Nemotron + bench workers. Stop instances when idle (credits).

## Style
Python 3.12, pydantic v2, async where it touches LLMs. Match the surrounding code: short docstrings, type hints, no needless
abstraction. Keep modules small and testable. Log with `rich`/stdlib logging, not print, in library code.
