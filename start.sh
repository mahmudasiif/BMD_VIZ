#!/bin/bash
# BMD Weather Visualization — startup script
# Run from the project root: bash start.sh

set -e
cd "$(dirname "$0")"

# Install dependencies if needed
~/.local/bin/pip install -r requirements.txt --quiet

# Process the latest NC file if output doesn't exist yet
NC_FILE=$(ls bangladesh_atmos_*.nc 2>/dev/null | sort | tail -1)
if [ -n "$NC_FILE" ]; then
  RUN_DATE=$(echo "$NC_FILE" | grep -oP '\d{8}')
  RUN_HOUR=$(echo "$NC_FILE" | grep -oP '(?<=_)\d{2}(?=\.nc)')z
  if [ ! -f "data/viz/$RUN_DATE/${RUN_HOUR}/manifest.json" ]; then
    echo "Processing $NC_FILE ..."
    python3 scripts/process_ecmwf.py --input "$NC_FILE"
  else
    echo "Data already processed for $RUN_DATE $RUN_HOUR."
  fi
else
  echo "Warning: No NC file found. API will start without pre-processed data."
fi

echo ""
echo "Starting FastAPI server at http://localhost:8000"
~/.local/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
