# STATUS (update after every milestone)

**Now:** Tue 27 Oct ~13:55 IST (earlier note: 12:20). Code frozen at `1da789f` (+ tooling/deck commits after it, no gateway changes). Final grids
running on custos-gpu. Target: deliverables ready ~19:00-20:00 IST, upload by the team 20:00-23:00.

## 13:55 IST update
- Main re-run done (all 6,120 planned episodes on frozen code; config_hash fe3c09103c9f). Old-hash rows are dropped by
  scripts/drop_stale_rows.py in the final metrics step. Interim main ASR: D0 27.2% · D1 9.4% · D2 24.5% · D3 0.2% (2/1088, f10).
- Ablation done (n=25/cell plan + earlier rows). Adaptive still running (tmux par_adaptive; hosted Ultra attacker is rate-limited).
- Gap-fill for main/ablation/utility runs in tmux par_gap; tmux gap_watch touches ~/autopilot/par_gap.done when they finish
  (par_gap's own wait loop matches itself and never ends: ignore it). The autopilot then resumes adaptive, runs access +
  contradiction, drop_stale_rows, metrics, report.
- Utility (LongMemEval): D0 24.7% vs D3 13.5%; reproduced on healthy infra; D3-claimlwm 12.9%. Cause: ~half of summary
  claims rejected by grounding rule R1 (summary-laundering defence). Disclosed in deck (limits, Q&A) and README.
- Then on the laptop: `powershell -ExecutionPolicy Bypass -File scriptsinalize.ps1 -DemoNote "<note>"`.

## What runs where (custos-gpu, reach via `ssh 10.81.30.68` then `ssh custos-gpu`)
| tmux | what | output |
|---|---|---|
| `autopilot` | infra/brev/QUEUE: main D3 re-run → waits for par_* done markers → gap-fill re-run (main, ablation, utility) → access → `metrics runs && report runs` | runs/{main,...}, runs/metrics.json, runs/final.json, runs/figures |
| `par_ablation`, `par_utility`, `par_adaptive` | started by infra/brev/par_grids.sh, touch ~/autopilot/par_<g>.done when they exit | runs/<g>/episodes.jsonl, ~/par_<g>.log |
| `hotswap` | infra/brev/hotswap.sh: replaces OMP-spinning vLLM engines with capped ones (vllm1x on :801x); old ports 8000-8006 proxied by socat (tmux px<i>) | ~/hotswap.log |
| `embed` (docker) | llama-nemotron-embed-1b-v2 on GPU 7, :8100 | |

## Results so far (code output)
- PoisonedRAG NQ (public, held out; final code): trusted-record ASR D0 74.0% · D1 74.0% · D2 37.0% · **D3 3.0% [1.4-6.4]**, acc 96%; poison-only D3 76.5%. `runs/external_poisonedrag/summary.json`.
- AgentDojo banking v1.2.1 (public): ASR none 52.1% · spotlight 48.6% · PI filter 0.0% (utility 31.2%) · **Custos gate 0.0% (utility 52.8%)**; gate asked 613×, blocked 0×, 0 asks on benign; approve-all user 52.8%. `runs/external_agentdojo/summary.json`.
- Main grid (old code, f11 excluded): D0 24.5% · D1 5.1% · D2 21.7% · D3 0.0% (n=1072). D3 rows being re-run on final code.

## Fixes made 27 Oct (all re-run on final code)
- f11 leak check counted the plant session's own remember() (100% ASR for every defence) → scoped to trigger sessions.
- Unextractable user/agent notes were dropped (D3 recalled 6/26 preferences) → kept as other:note.
- Tier-aware conflict prefilter: contradictions of higher-tier records now reach NLI (09aa3a5); legend states tier precedence (705802d).
- AgentDojo D1 detector cross-event-loop bug → shared loop; D1 re-run.
- vLLM OpenMP spinning starved the 240-core host → OMP_NUM_THREADS=8, hot-swapped.

## Final assembly (laptop)
`powershell -ExecutionPolicy Bypass -File scripts\finalize.ps1 -DemoNote "<golden-path note>"` → pulls runs/ into
results/final, writes results/final/ui (console Bench), rewrites S11 narration (scripts/video/s11_text.py), builds
deliverables/Custos_AEGIS2026_Final.pptx (+ deliverables/deck_png/), copies the public-benchmarks slide to
scripts/video/cards/public.png, re-shoots S7-S11 and re-assembles the video. Then fill README RESULTS block, push.
Golden-path note (from autopilot logs): 7 live runs: undefended paid attacker 2; Custos 0, quarantined 7/7, paid genuine ERP 4.

## Sync path
laptop `git push lab main` → lab PC `~/custos` (`git pull origin main`) → `git push gpu main` → custos-gpu `~/custos` (`git pull`).
GitHub origin https://github.com/Adibaghel232/custos is PRIVATE; ask before making it public.
