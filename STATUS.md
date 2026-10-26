# STATUS (update after every milestone)

**Now:** Tue 27 Oct 01:20 IST — lost 17:30–01:07 to usage limit; agents resumed; GPUs serving.

| Area | State | Notes |
|---|---|---|
| Plan / spec / contracts | ✅ PLAN.md · docs/SPEC.md · docs/CONTRACTS.md | |
| W1 core (types, llm, embed, ledger, tiers, provenance, store) | ✅ 17 offline + 3 live tests | hosted Lightning: JSON schema ✅ tool calls ✅ embed ✅ |
| Keys / logins | ✅ NVIDIA key (.env, laptop + lab + GPU box) · ✅ Brev CLI on lab PC | |
| GPU box `custos-gpu` | ✅ 8× A100-SXM4-80GB, 1.7 TB, docker | `ssh custos-gpu` from lab PC |
| vLLM Lightning ×8 (ports 8000–8007) | ✅ 60 req/s burst, JSON+tools OK | tmux `vllm` on custos-gpu; log ~/vllm_up.log; urls → ~/vllm_urls.env |
| W2 write path | ⏳ agent | |
| W3 read/gate/recover/API | ⏳ agent | |
| W4 agent + finance world + golden path | ⏳ agent | |
| W5 bench | ⏳ agent | |
| UI | ⏳ agent | |
| W6 data (LongMemEval, legit/contradiction sets) | todo (parent) | |
| Results / deck / video | — | |

## Sync path
laptop `git push lab main` → lab PC `~/custos` (`git pull origin main`) → `git push gpu main` → custos-gpu `~/custos` (`git pull`).

## Open questions for humans
- 26 Oct 12:00 format update: none sent (Aditya, 17:10). Proceed with round-1 format + criteria.
- Exact upload deadline time ("end of day" = 23:59 IST?).

## Resume notes (02:20 Tue)
- Claude paused (usage). GPU autopilot queue: golden-path seed runs (demo/runs/* on custos-gpu); tmux `seeds` sweep -> ~/seed_sweep.log.
- W5 bench agent was mid-run when paused: resume it (SendMessage), commit src/custos_bench + configs/bench, run smoke on GPU box, then queue main/ablation/adaptive/utility grids in infra/brev/QUEUE.
- Live golden path on self-hosted: Act 2 D0 pays attacker, D3 quarantines + gate blocks. Act 1 D0 paid ERP at seed 7 (pick seed from sweep).
