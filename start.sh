#!/usr/bin/env bash
# From a fresh clone to Converge at http://localhost:8000 in one command: installs what is
# missing, asks for your keys once (they stay in .env on this machine), builds the web app,
# loads the demo teams on first run, and starts the server.
set -euo pipefail
cd "$(dirname "$0")"

have() { command -v "$1" >/dev/null 2>&1; }

if ! have uv; then
  echo "Installing uv, the Python package manager (https://docs.astral.sh/uv/)…"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
if ! have npm; then
  echo "Converge needs Node.js 20 or newer to build its web app: https://nodejs.org" >&2
  exit 1
fi

if [ ! -f .env ]; then
  echo "First run: Converge needs your keys. They are saved only to .env in this folder."
  read -rp "Meta Model API key (https://dev.meta.ai/api-keys): " model_key
  read -rp "Notion integration token (https://www.notion.so/my-integrations), or Enter to skip: " notion_token
  umask 077
  printf 'MODEL_API_KEY=%s\nNOTION_TOKEN=%s\n' "$model_key" "$notion_token" > .env
fi

echo "Installing and building…"
(cd backend && uv sync --quiet)
(cd frontend && npm ci --silent && npm run build --silent >/dev/null)
if [ ! -f data/converge.db ]; then
  (cd backend && uv run python -m app.ingest demo)
fi

cat <<MSG

Converge is running at http://localhost:8000  (Ctrl+C stops it)
Beside Notion: open chrome://extensions, turn on Developer mode, click "Load unpacked",
and pick $(pwd)/extension. Then click the Converge icon on any Notion page.

MSG
cd backend && exec uv run uvicorn app.main:app --port 8000
