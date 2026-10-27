#!/usr/bin/env bash
# Start the ablation, utility and adaptive grids in parallel (each resumable); each touches ~/autopilot/par_<grid>.done
# when it exits, which the autopilot QUEUE waits for before its gap-filling re-run. Usage: bash infra/brev/par_grids.sh
cd ~/custos || exit 1
for g in ablation:64 utility:32 adaptive:48; do
  name=${g%%:*}; c=${g##*:}
  tmux kill-session -t "par_$name" 2>/dev/null
  tmux new -d -s "par_$name" "source infra/brev/env.sh; .venv/bin/python -m custos_bench.cli run configs/bench/$name.yaml --out runs/$name --concurrency $c > ~/par_$name.log 2>&1; touch ~/autopilot/par_$name.done"
done
tmux ls | grep par_
