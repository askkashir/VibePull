#!/usr/bin/env bash
set -euo pipefail

PYTHON="${PYTHON:-python}"
URL="http://localhost:8766"

if [[ ! -f indexes/text_index.faiss || ! -f indexes/text_metadata.json ]]; then
  "$PYTHON" pipeline/retrieval.py
fi

if [[ ! -f indexes/bm25_index.pkl ]]; then
  "$PYTHON" pipeline/bm25_retrieval.py
fi

if command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$URL" >/dev/null 2>&1 || true
elif command -v open >/dev/null 2>&1; then
  open "$URL" >/dev/null 2>&1 || true
fi

"$PYTHON" backend/server.py
