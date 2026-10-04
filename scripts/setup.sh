#!/usr/bin/env bash
# 개발 환경 준비. 작업 폴더 구조: <workspace>/{RAG,AI,BackEnd,FrontEnd}
set -euo pipefail
cd "$(dirname "$0")/.."
for repo in ../RAG ../AI; do
  [ -d "$repo" ] || { echo "필요한 저장소가 없습니다: $repo (같은 폴더에 RAG, AI 저장소를 clone 하세요)"; exit 1; }
done
python3 -m venv .venv
. .venv/bin/activate
pip install -U pip
pip install -r requirements-dev.txt
[ -f .env ] || cp .env.example .env
echo
echo "준비 완료. 서버 실행: scripts/dev.sh  (API 문서: http://localhost:8000/docs)"
