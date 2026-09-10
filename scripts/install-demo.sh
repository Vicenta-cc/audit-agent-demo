#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
for tool in git uv node pnpm; do
  command -v "$tool" >/dev/null || { echo "Missing $tool; see README.md prerequisites." >&2; exit 1; }
done
git submodule update --init --recursive
uv venv --python 3.12 --allow-existing .venv
uv pip install --python .venv/bin/python -r requirements.txt -r requirements-hermes.txt
uv venv --python 3.12 --allow-existing external/MediaCrawler/.venv
uv pip install --python external/MediaCrawler/.venv/bin/python -r external/MediaCrawler/requirements.txt
external/MediaCrawler/.venv/bin/python -m playwright install chromium
pnpm --dir Audit_assistant install --frozen-lockfile
pnpm --dir Audit_assistant build
if [ ! -f .env.demo.local ]; then
  cp demo/demo.env.example .env.demo.local
  chmod 600 .env.demo.local
fi
.venv/bin/python scripts/demo.py init
printf '%s\n' 'Installation complete. Set DASHSCOPE_API_KEY in .env.demo.local, then run:' '.venv/bin/python scripts/demo.py check' '.venv/bin/python scripts/demo.py start'
