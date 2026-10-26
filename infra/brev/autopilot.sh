#!/usr/bin/env bash
# GPU autopilot: keeps the A100s working even when no Claude session is active.
# Every 2 min: git pull, health-check the vLLM servers (restart dead ones), then run the first job in
# infra/brev/QUEUE that has not succeeded yet. Jobs must be resumable (custos-bench resume semantics).
# Success markers live in ~/autopilot/done/<sha1-of-line>; logs in ~/autopilot/logs/.
# Start once:  tmux new -d -s autopilot "bash ~/custos/infra/brev/autopilot.sh"
set -u
REPO=$HOME/custos
STATE=$HOME/autopilot
mkdir -p "$STATE/done" "$STATE/logs"
export PATH=$HOME/.local/bin:$PATH
. "$REPO/infra/brev/env.sh"

log() { echo "[$(date '+%F %T')] $*" | tee -a "$STATE/autopilot.log"; }

health() {
  for c in $(docker ps -a --format '{{.Names}}' | grep -E '^vllm[0-9]+$'); do
    i=${c#vllm}; port=$((8000 + i))
    if ! curl -sf --max-time 10 "http://127.0.0.1:$port/v1/models" >/dev/null; then
      log "vLLM $c unhealthy -> restart"; docker restart "$c" >/dev/null
    fi
  done
}

while true; do
  (cd "$REPO" && git pull -q 2>>"$STATE/autopilot.log" && uv sync -q --extra dev >/dev/null 2>&1)
  health
  ran=0
  if [ -f "$REPO/infra/brev/QUEUE" ]; then
    while IFS= read -r line; do
      line="${line%%#*}"; [ -z "${line// }" ] && continue
      id=$(echo -n "$line" | sha1sum | cut -c1-12)
      [ -f "$STATE/done/$id" ] && continue
      fails=$(cat "$STATE/fail_$id" 2>/dev/null || echo 0)
      if [ "$fails" -ge 3 ]; then
        # retry a thrice-failed job only after the code changes
        head=$(cd "$REPO" && git rev-parse HEAD); [ "$(cat "$STATE/failhead_$id" 2>/dev/null)" = "$head" ] && continue
        echo 0 > "$STATE/fail_$id"
      fi
      log "RUN [$id] $line"
      (cd "$REPO" && timeout 6h bash -c "$line") >"$STATE/logs/$id.log" 2>&1
      rc=$?
      if [ $rc -eq 0 ]; then touch "$STATE/done/$id"; log "OK  [$id]"
      else echo $((fails + 1)) > "$STATE/fail_$id"; (cd "$REPO" && git rev-parse HEAD) > "$STATE/failhead_$id"
           log "FAIL[$id] rc=$rc (attempt $((fails + 1))) tail: $(tail -2 "$STATE/logs/$id.log" | tr '\n' ' ' | cut -c1-200)"; fi
      ran=1; break   # re-pull after every job so new code/queue edits are picked up
    done < "$REPO/infra/brev/QUEUE"
  fi
  [ $ran -eq 0 ] && sleep 120
done
