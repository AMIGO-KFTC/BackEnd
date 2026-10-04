#!/usr/bin/env bash
# 백엔드(8000)와 프론트엔드(5173)를 한 터미널에서 함께 실행 (macOS / Linux). Ctrl+C 로 둘 다 종료.
#   ./scripts/start-all.sh
#   PORT=8001 ./scripts/start-all.sh    → 백엔드 포트 변경(프론트 프록시도 자동으로 맞춤)
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(cd .. && pwd)"
PORT="${PORT:-8000}"

if [ ! -x .venv/bin/python ]; then
  echo "✘ 가상환경이 없습니다. 먼저 ./scripts/setup.sh 를 실행하세요." >&2
  exit 1
fi
if [ ! -d "$ROOT/FrontEnd/node_modules" ]; then
  echo "✘ $ROOT/FrontEnd 에 npm 패키지가 없습니다. FrontEnd 를 받은 뒤 ./scripts/setup.sh 를 다시 실행하세요." >&2
  exit 1
fi

PORT="$PORT" ./scripts/dev.sh &
BACKEND_PID=$!
trap 'kill "$BACKEND_PID" 2>/dev/null || true; wait "$BACKEND_PID" 2>/dev/null || true' EXIT INT TERM

echo "백엔드가 뜨기를 기다리는 중…"
for _ in $(seq 1 60); do
  if .venv/bin/python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:$PORT/api/health', timeout=1)" 2>/dev/null; then
    break
  fi
  sleep 1
done

echo
echo "  화면     : http://localhost:5173"
echo "  API 문서 : http://localhost:$PORT/docs"
echo
cd "$ROOT/FrontEnd"
AMIGO_API="http://localhost:$PORT" npm run dev
