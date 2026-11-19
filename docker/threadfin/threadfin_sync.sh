#!/usr/bin/env bash
# HomeLab Threadfin Sync wrapper — serialised, logged, safe.
#   --full     probe every stream, rebuild EPG, publish   (daily)
#   --publish  re-apply curated.* -> threadfin.* if drifted (guard)
set -euo pipefail

BASE="/home/mrdavid/HomeLab/docker/threadfin"
LOG="$BASE/logs/sync.log"
mkdir -p "$BASE/logs"

exec 9>"$BASE/.sync.lock"
if ! flock -n 9; then
    echo "[$(date '+%F %T')] sync already running, skipping" >>"$LOG"
    exit 0
fi

# Guard mode runs every minute: stay silent unless it actually repairs a file.
if [ "${1:-}" = "--publish" ]; then
    out=$(nice -n 10 python3 "$BASE/threadfin_sync.py" --publish 2>&1 || true)
    [ -n "$out" ] && echo "[$(date '+%F %T')] $out" >>"$LOG"
    exit 0
fi

echo "[$(date '+%F %T')] ===== start $* =====" >>"$LOG"
nice -n 10 python3 "$BASE/threadfin_sync.py" "$@" >>"$LOG" 2>&1
rc=$?
echo "[$(date '+%F %T')] ===== end rc=$rc =====" >>"$LOG"

# keep the log bounded (last ~4000 lines)
if [ "$(wc -l <"$LOG")" -gt 5000 ]; then
    tail -n 4000 "$LOG" >"$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi
exit $rc
