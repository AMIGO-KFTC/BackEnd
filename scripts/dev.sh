#!/usr/bin/env bash
# 개발 서버 실행(AI·RAG 코드를 고쳐도 자동 재시작)
set -euo pipefail
cd "$(dirname "$0")/.."
[ -d .venv ] && . .venv/bin/activate
exec uvicorn app.main:app --reload --port "${PORT:-8000}" --reload-dir app --reload-dir ../AI/src --reload-dir ../RAG/src
