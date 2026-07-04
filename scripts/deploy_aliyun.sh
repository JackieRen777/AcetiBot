#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REMOTE_HOST="${REMOTE_HOST:-root@8.136.8.223}"
REMOTE_DIR="${REMOTE_DIR:-/www/wwwroot/AcetiBot}"
REMOTE_FRONTEND_DIR="${REMOTE_FRONTEND_DIR:-$REMOTE_DIR/frontend-dist}"
PUBLIC_URL="${PUBLIC_URL:-http://8.136.8.223:8080}"

cd "$ROOT_DIR"

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1" >&2
    exit 1
  }
}

require_cmd python3
require_cmd npm
require_cmd rsync
require_cmd ssh
require_cmd curl

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
VITE_API_URL=/api npm --prefix frontend run build

echo "==> Syncing repository"
rsync -avz \
  --delete \
  --exclude '.git/' \
  --exclude '.env' \
  --exclude 'venv/' \
  --exclude '__pycache__/' \
  --exclude 'frontend/node_modules/' \
  --exclude 'frontend/dist/' \
  --exclude 'frontend-dist/' \
  --exclude 'chroma_db/' \
  "$ROOT_DIR/" "$REMOTE_HOST:$REMOTE_DIR/"

echo "==> Ensuring remote frontend directory exists"
ssh "$REMOTE_HOST" "mkdir -p '$REMOTE_FRONTEND_DIR'"

echo "==> Syncing frontend build"
rsync -avz --delete "$ROOT_DIR/frontend/dist/" "$REMOTE_HOST:$REMOTE_FRONTEND_DIR/"

echo "==> Rebuilding remote knowledge base and restarting service"
ssh "$REMOTE_HOST" "
  set -e
  cd '$REMOTE_DIR'
  test -d venv
  source venv/bin/activate
  python ingest.py
  systemctl restart acetibot
  systemctl --no-pager --full status acetibot | sed -n '1,20p'
  for i in \$(seq 1 20); do
    if curl -fsS http://127.0.0.1:8013/health >/dev/null 2>&1 && curl -fsS http://127.0.0.1:8080/api/health >/dev/null 2>&1; then
      exit 0
    fi
    sleep 1
  done
  echo 'Service health check timed out.' >&2
  exit 1
"

echo "==> Verifying public endpoint"
curl -fsS --max-time 10 "$PUBLIC_URL/api/health" >/dev/null

echo
echo "Deploy complete."
echo "Public URL: $PUBLIC_URL"
