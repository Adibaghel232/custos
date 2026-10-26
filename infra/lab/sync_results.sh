#!/usr/bin/env bash
# Lab PC: every 10 min copy runs/ and the autopilot log back from the GPU box (survives Brev hiccups).
# Start once:  tmux new -d -s sync "bash ~/custos/infra/lab/sync_results.sh"
mkdir -p "$HOME/custos/runs" "$HOME/custos/autopilot"
while true; do
  ssh -o BatchMode=yes custos-gpu "cd ~/custos && tar czf - runs 2>/dev/null" 2>/dev/null | tar xzf - -C "$HOME/custos" 2>/dev/null
  scp -q custos-gpu:autopilot/autopilot.log "$HOME/custos/autopilot/" 2>/dev/null
  echo "[$(date '+%F %T')] synced $(du -sh "$HOME/custos/runs" 2>/dev/null | cut -f1)" >> "$HOME/custos/autopilot/sync.log"
  sleep 600
done
