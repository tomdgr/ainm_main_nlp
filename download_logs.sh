#!/bin/bash
set -e

BUCKET="${LOG_BUCKET:-tripletex-ai-agent-logs}"
HOST="${1:-tripletex-agent}"
DEST="example_runs"

echo "Downloading logs from gs://$BUCKET/runs/$HOST/ ..."
gsutil -m cp -r "gs://$BUCKET/runs/$HOST/" "$DEST/"

echo ""
echo "Downloaded to $DEST/:"
find "$DEST" -type f | head -30
