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
python3 "$PROJECT_DIR/scripts/update.py" "$TIER" --json >> "$LOG_FILE" 2>&1 || EXIT_CODE=$?

if [ "$EXIT_CODE" -ne 0 ]; then
    echo "Pipeline failed with exit code $EXIT_CODE" | tee -a "$LOG_FILE"
    # macOS notification on failure
    osascript -e "display notification \"$TIER pipeline failed (exit $EXIT_CODE)\" with title \"CivicCache\" sound name \"Basso\"" 2>/dev/null || true
else
    echo "Pipeline completed successfully" >> "$LOG_FILE"
fi

# Prune logs older than 30 days
find "$LOG_DIR" -name "*.log" -mtime +30 -delete 2>/dev/null || true

exit "$EXIT_CODE"
