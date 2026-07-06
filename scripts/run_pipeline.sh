#!/usr/bin/env bash
#
# CivicCache pipeline wrapper for launchd / cron.
#
# Usage:
#   bash scripts/run_pipeline.sh daily
#   bash scripts/run_pipeline.sh weekly
#   bash scripts/run_pipeline.sh monthly
#
set -euo pipefail

TIER="${1:-daily}"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$PROJECT_DIR/data/logs"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
LOG_FILE="$LOG_DIR/${TIER}-${TIMESTAMP}.log"

mkdir -p "$LOG_DIR"

# --- single-run lock -------------------------------------------------------
# SQLite is single-writer; two overlapping pipeline runs (launchd + a manual
# or CacheStatus-button run) produce `database is locked` failures. mkdir is
# atomic, so it serves as a portable lock; a dead pid means a stale lock.
LOCK_DIR="$PROJECT_DIR/data/.pipeline.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    OTHER_PID="$(cat "$LOCK_DIR/pid" 2>/dev/null || echo "")"
    # ps -p (not kill -0): kill -0 returns EPERM for live processes we can't
    # signal, which would misclassify them as stale.
    if [ -n "$OTHER_PID" ] && ps -p "$OTHER_PID" > /dev/null 2>&1; then
        echo "Another pipeline run (pid $OTHER_PID) is in progress -- exiting."
        exit 75  # EX_TEMPFAIL
    fi
    rm -rf "$LOCK_DIR"
    mkdir "$LOCK_DIR"
fi
echo $$ > "$LOCK_DIR/pid"
trap 'rm -rf "$LOCK_DIR"' EXIT

# Source .env for API keys
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    source "$PROJECT_DIR/.env"
    set +a
fi

echo "=== CivicCache $TIER pipeline  $(date) ===" | tee "$LOG_FILE"
echo "Project: $PROJECT_DIR" | tee -a "$LOG_FILE"
echo "Log: $LOG_FILE" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"

# Run the pipeline
EXIT_CODE=0
# --workers 1 serializes the writers: intra-run lock contention (7 parallel
# stages against single-writer SQLite) caused intermittent failures, and an
# unattended overnight run does not care about wall-clock time.
python3 "$PROJECT_DIR/scripts/update.py" "$TIER" --json --workers 1 >> "$LOG_FILE" 2>&1 || EXIT_CODE=$?

if [ "$EXIT_CODE" -ne 0 ]; then
    echo "Pipeline failed with exit code $EXIT_CODE" | tee -a "$LOG_FILE"
else
    echo "Pipeline completed successfully" >> "$LOG_FILE"
fi

# Material-change digest: writes status.json for the CacheStatus menu-bar app
# and pushes ntfy/Notification Center alerts (incl. pipeline failure). Runs
# even when the pipeline failed -- that failure is itself the notification.
python3 "$PROJECT_DIR/scripts/digest.py" --tier "$TIER" --pipeline-exit "$EXIT_CODE" >> "$LOG_FILE" 2>&1 \
    || echo "digest failed (non-fatal)" | tee -a "$LOG_FILE"

# Prune logs older than 30 days
find "$LOG_DIR" -name "*.log" -mtime +30 -delete 2>/dev/null || true

exit "$EXIT_CODE"
