# Custos — finals execution plan (AEGIS 2026 · PS 04 · Team Charlies)

**Deadline:** Tue 27 Oct 2026, end of day IST · **target ready-to-upload: Tue 21:00 IST** (3 h buffer)
**Owner of execution:** Claude (autonomous). Humans: Aditya (busy with exams; needed only for logins and the final upload), Karan.
**Detailed engineering spec:** [docs/SPEC.md](docs/SPEC.md) · **Demo/deck/video plan:** [docs/DEMO_DECK_PLAN.md](docs/DEMO_DECK_PLAN.md) · **Live status:** [STATUS.md](STATUS.md)

---

## 1. How we win

We finished top-3 with a pitch. The finals are judged on a presentation plus a screen-recorded demo, so the decisive things are:

1. **A demo that lands in 30 seconds.** An Indian accounts-payable agent. In session 3 a forged email (T0) changes vendor *Acme Supplies*' bank account and IFSC code. In session 9, "pay Acme's March invoice" makes the **undefended agent send ₹4.2 lakh to the attacker**. With Custos, the email's claim is **quarantined** against the ERP (T3). The **payment gate blocks** and names the email as the decisive memory. Then **one-click rollback** shows the taint closure and the sessions that were exposed.
2. **Real numbers with confidence intervals.** We run Custos-Bench on NVIDIA GPUs and report attack success (ASR), injection success (ISR), false quarantines, utility, leakage, erasure and latency. Each one compares Custos with undefended memory, a prompt-injection filter and A-MemGuard-lite, plus ablations. Every number in the deck comes from `results/final/metrics.json`.
3. **Genuine NVIDIA stack.** The stack is NVIDIA end to end:
   - Nemotron 3.5 Lightning, self-hosted with vLLM on Brev A100s, for the agent, claim extraction and the NLI/grounding judge.
   - Nemotron 3 Super (hosted) as the adaptive attacker and the independent judge.
   - Nemotron 3.5 embeddings.
   - NemoGuard and Nemotron content-safety as baselines.
   - NeMo Guardrails rail, garak probe and cuVS are stretch goals.
4. **Rigor and honesty.** We include the threat model, ablations, an adaptive attacker and fixed citations. We also show that we pre-empt the closest competitor, MEM-INV-Bench (summary laundering, echo, manufactured corroboration).
5. **Framing for these judges.** For Infosys: *AI3S — Scan (Custos-Bench) / Shield (gateway) / Steer (ledger + rollback)*. Compliance anchors are OWASP **ASI06 Memory & Context Poisoning**, MITRE ATLAS **AML.T0080 AI Agent Context Poisoning**, and NIST AI 600-1 §2.8 (*"clear chain of custody"*). DPDP §12/§8(7) erasure is presented as **"ready for 13 May 2027"**.

## 2. Constraints

| Constraint | Consequence |
|---|---|
| NVIDIA open models only. Main LLM is Nemotron; others come from build.nvidia.com | DeBERTa-v3 NLI is **dropped**. NLI and grounding run on a Nemotron judge. `custos/compliance.py` refuses non-NVIDIA model ids |
| GPUs: **A100 or L40S only** (Brev, about $1000 credits) | **A100 80 GB** (crusoe, about $1.98/h, can be stopped) is NVIDIA's documented 1-GPU BF16 config for Nemotron 3.5 Lightning. The bench burst uses 2–4× A100. L40S is only a fallback (needs FP8) |
| Claude usage limits (we lost 11:30–15:20 on Mon) | Few, focused agents. Sonnet does the bulk coding against frozen interfaces; Opus does integration. Work is queued so each 5-h window is used fully |
| Laptop: 16 GB free disk, can sleep | **Lab PC `10.81.30.68`** (Ubuntu 26.04, 24 cores, 61 GB RAM, 552 GB disk, always on, no docker/sudo) is the control node. **Brev A100** runs the GPU work |

## 3. What we build (architecture as built)

```
 User / inbox(T0) / ERP(T3) / tools(T1)
        │
 LangGraph agent (Nemotron 3.5 Lightning, tool calling)   ← same agent for every defence
        │ remember/recall/summary           │ tool calls (pay/send/share/grant)
        ▼                                   ▼
 ┌──────────────────── CUSTOS GATEWAY (FastAPI + in-process SDK) ─────────────────────┐
 │ WRITE: stamp(source,principal,parents,raw-hash,tier) → sign → hash-chained ledger    │
 │        → extract claims (Nemotron, JSON schema) → checks: key conflict · embedding   │
 │        conflict · NLI (Nemotron) · grounding · CUSUM drift · instruction detector    │
 │        → policy (Python + Rego/OPA parity): commit | version | quarantine | reject   │
 │ READ:  tenant/owner/purpose/sensitivity filter BEFORE vector search (per-tenant idx) │
 │        T0/T1 → quoted data blocks; standing instructions only from T2+ confirmed     │
 │ GATE:  per-argument taint from memory tiers → leave-one-out re-plan (≤3 calls)       │
 │        → allow | ask | block (+ NeMo Guardrails rail calls the same function)        │
 │ RECOVER: taint closure over provenance graph → revert/erase → re-derive → exposure   │
 └──────────────────────────────────────────────────────────────────────────────────────┘
        │ store adapters: Custos-native (SQLite + per-tenant vector index) · LangGraph store · Mem0
 Custos-Bench: 11 attack families (+3 laundering variants) · adaptive attacker (Nemotron 3 Super)
               · D0 undefended · D1 PI-filter · D2 A-MemGuard-lite · D3 Custos · ablations
               · 14 metrics + Memory Governance Score · Wilson 95% CIs
 Demo console (React): chat · custody graph · ledger · quarantine · gate card · rollback · results
```

**Models** (verified against the live build.nvidia.com catalog, 26 Oct):

| Role | Model | Where |
|---|---|---|
| Agent planner, claim extractor, NLI/grounding judge | `nvidia/nemotron-3.5-lightning-30b-a3b` (thinking off) | Brev A100 via vLLM v0.27.1 for the bench; hosted for dev |
| Adaptive attacker, independent judge | `nvidia/nemotron-3-super-120b-a12b` | hosted |
| Embeddings | `nvidia/nemotron-3.5-embed-1b` | hosted, cached |
| Baseline detectors | NemoGuard JailbreakDetect, `nvidia/nemotron-3.5-content-safety`, PromptArmor-style prompt on Lightning | hosted |

## 4. Scope (MUST / SHOULD / STRETCH)

- **MUST (demo + headline metrics):**
  - Gateway write/read/gate/recover, ledger and tiers.
  - Finance scenario with all 11 attack families.
  - Defences D0, D1, D3 and ablations (−tiers, −consistency, −gate).
  - Metrics M1–M14 at n≈100 per main cell.
  - LongMemEval utility subset (178 Q).
  - Demo console, final deck and video.
- **SHOULD:**
  - D2 A-MemGuard-lite.
  - Mem0 backend (D0 infer=True, D3 adapter).
  - Support scenario (≥6 families).
  - Adaptive attacker at K=10 on 4 families.
  - OPA/Rego parity.
  - NeMo Guardrails rail.
  - garak probe.
  - OpenTelemetry.
- **STRETCH:**
  - Graphiti (Kuzu) and Letta backends.
  - Email and multi-agent scenarios.
  - cuVS retrieval microbenchmark.
  - NeMo Agent Toolkit wrapper.
  - Fine-tuned fast NLI classifier.

**Cut order if late:** Letta → Graphiti → email/multi-agent → garak → Guardrails rail → Mem0 → support scenario → adaptive K beyond 10.
**Never cut:** the demo path, D0 vs D3 on all 11 families, false quarantine rate, utility, leakage, erasure.

## 5. Timeline (IST)

| When | What | Gate to pass |
|---|---|---|
| Mon 15:30–17:00 | Plan, spec, frozen interfaces (`types.py`, protocols), repo skeleton; lab PC env; Brev login | Interfaces frozen |
| Mon 17:00–22:00 | Parallel build: W1 gateway core · W2 checks/LLM · W3 agent + finance world + gate · W4 bench harness + attacks + defences + metrics · W5 UI | Unit tests green |
| Mon 22:00–Tue 02:00 | Integration on hosted Nemotron; golden demo path works end to end; bench smoke (n=5) | Demo path works |
| Tue 02:00–08:00 | Brev A100s with vLLM Lightning: main grid, ablations, utility, access/erasure/tamper, adaptive | `metrics.json` v1 |
| Tue 08:00–14:00 | Figures; UI polish; SHOULD items; README; re-runs for weak cells | Results frozen 14:00 |
| Tue 14:00–19:00 | Final deck (AEGIS template) + demo video (scripted Playwright capture + voiceover + captions) | Draft deck + video |
| Tue 19:00–21:00 | QA, packaging, upload kit for Aditya | **Upload** |

## 6. GPU budget (Brev, ≤ $1000)

| Use | Instance | Hours | Cost |
|---|---|---|---|
| Dev/serving box (vLLM Lightning + harness) | 1× A100 80 GB (crusoe) | ~28 | ~$55 |
| Bench burst (main grid + ablations + utility + adaptive) | +3× A100 80 GB | ~6 | ~$36 |
| Buffer / re-runs | — | — | ~$100 |
| **Total planned** | | | **≈ $200** (stop instances when idle) |

## 7. Fixes to the round-1 deck (citation audit — all 16 papers exist)

- **[4] MPBench:**
  - It is an **ICML 2026 Workshop** paper (Agents in the Wild).
  - "Best detector catches 42.5% of weak-signal poisons" should read: "**PromptArmor**, the strongest detector, catches 42.5% of weak-signal poisons".
  - "Misses 32%" must be labelled as *derived from its 67.67% TPR*.
- **[5] A-MemGuard:** ">95%" applies only to the retrieval stage. Say "up to >95% at retrieval; ~60% on indirect injection".
- **[1] MINJA** is a **NeurIPS 2025** paper; **[3] PoisonedRAG** is arXiv:2402.07867.
- **Add to related work:** MEM-INV-Bench/TMA-NM (2606.24322), *Utility Under Attack* (2608.21230), memshield-bench, MemSecBench, ASB, Spotlighting.

## 8. What we need from humans (minimal)

1. **Now:** NVIDIA API key (the setup window saves it to `.env`, never into chat) and **Brev login** (link approval).
2. **When convenient:** forward/paste the **26 Oct 12:00 format update and judging criteria** emails, and confirm what "end of day" means for upload.
3. **Tue evening:** upload the final deck and video from `deliverables/` (Claude prepares everything). Optionally record the voiceover yourselves from the script (a human voice beats TTS).
