#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMMIT_MSG="${1:-deploy: $(date '+%Y-%m-%d %H:%M:%S')}"

cd "$ROOT_DIR"

echo "==> Checking repository"
git rev-parse --is-inside-work-tree >/dev/null

echo "==> Verifying Python files"
python3 -m py_compile \
  analysis.py \
  api.py \
  embeddings.py \
  eval_retrieval.py \
  ingest.py \
  models.py \
  prompts.py \
  query.py \
  uploads.py

echo "==> Building frontend"
npm --prefix frontend run build

echo "==> Staging changes"
git add .

if git diff --cached --quiet; then
  echo "No changes to commit."
  exit 0
fi

echo "==> Committing"
git commit -m "$COMMIT_MSG"

echo "==> Pushing to origin/main"
git push origin main

echo
echo "Release complete."
echo "GitHub push complete."
echo "If you also want to update the Alibaba Cloud server, run:"
echo "./scripts/deploy_aliyun.sh"
