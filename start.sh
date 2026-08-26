#!/bin/bash
# BMD Weather Visualization — server startup.
# Run from the project root: bash start.sh
#
# Data ingestion is handled separately by CRON (see below), so this script only
# starts the API/frontend server. New ECMWF runs are dropped into data/incoming/
# by your pipeline; cron runs scripts/watch_ingest.py to process them; the
# frontend auto-refreshes to the newest run within ~5 min.
#
# Cron entry (every 15 min):
#   */15 * * * * cd /home/mics02/bmd_viz && python3 scripts/watch_ingest.py --keep 6 >> data/ingest.log 2>&1

set -e
cd "$(dirname "$0")"

~/.local/bin/pip install -r requirements.txt --quiet
mkdir -p data/incoming

# Process anything already waiting in data/incoming/ before serving.
python3 scripts/watch_ingest.py --keep 6 || true

echo ""
echo "Starting FastAPI server at http://localhost:8000"
~/.local/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
