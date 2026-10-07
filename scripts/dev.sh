#!/usr/bin/env bash
# 백엔드 개발 서버 실행 (macOS / Linux). AI·RAG 코드를 고쳐도 자동으로 재시작된다.
#   ./scripts/dev.sh              → http://localhost:8000
#   PORT=8001 ./scripts/dev.sh    → 다른 포트
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -x .venv/bin/python ]; then
  echo "✘ 가상환경이 없습니다. 먼저 ./scripts/setup.sh 를 실행하세요." >&2
  exit 1
fi

RELOAD_DIRS=(--reload-dir app)
for dir in ../AI/src ../RAG/src; do
  [ -d "$dir" ] && RELOAD_DIRS+=(--reload-dir "$dir")
done

exec .venv/bin/python -m uvicorn app.main:app --reload --port "${PORT:-8000}" "${RELOAD_DIRS[@]}"
