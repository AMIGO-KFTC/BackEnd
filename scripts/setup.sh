#!/usr/bin/env bash
# AMIGO 개발 환경 준비 (macOS / Linux) — 처음 한 번만 실행
#
#   폴더 구조:  <작업폴더>/{RAG, AI, BackEnd, FrontEnd}
#   실행:       cd BackEnd && ./scripts/setup.sh
#
#   1) RAG·AI 저장소가 옆에 있는지 확인
#   2) BackEnd/.venv 가상환경을 만들고 RAG·AI(수정 즉시 반영되는 editable 설치) + 백엔드 패키지 설치
#   3) .env 가 없으면 .env.example 을 복사
#   4) FrontEnd 저장소가 있으면 npm install
#
#   다른 파이썬을 쓰려면:  PYTHON=python3.12 ./scripts/setup.sh
set -euo pipefail
cd "$(dirname "$0")/.."
BACKEND="$(pwd)"
ROOT="$(cd .. && pwd)"

step() { printf '\n\033[1;34m▶ %s\033[0m\n' "$1"; }
fail() { printf '\n\033[1;31m✘ %s\033[0m\n' "$1"; exit 1; }

step "저장소 확인 ($ROOT)"
for repo in RAG AI; do
  if [ ! -f "$ROOT/$repo/pyproject.toml" ]; then
    fail "$ROOT/$repo 가 없습니다. BackEnd 가 있는 폴더($ROOT)에서 다음을 실행하세요:
    git clone https://github.com/AMIGO-KFTC/$repo.git"
  fi
  echo "  ✔ $repo"
done
[ -f "$ROOT/FrontEnd/package.json" ] && echo "  ✔ FrontEnd" || echo "  - FrontEnd 없음(화면 없이 API 만 실행 가능)"

step "파이썬 찾기"
PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
  for candidate in python3.12 python3.13 python3.11 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then PYTHON="$candidate"; break; fi
  done
fi
[ -n "$PYTHON" ] || fail "파이썬을 찾을 수 없습니다. Python 3.12 를 설치하세요(3.11~3.13 사용 가능)."
VERSION="$("$PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])')" \
  || fail "$PYTHON 을(를) 실행할 수 없습니다. Python 3.12 를 설치하세요(3.11~3.13 사용 가능)."
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || fail "Python $VERSION 은(는) 지원하지 않습니다. Python 3.12 를 설치한 뒤
    PYTHON=python3.12 ./scripts/setup.sh  처럼 지정해서 다시 실행하세요."
"$PYTHON" -c 'import sys; sys.exit(0 if (3, 11) <= sys.version_info[:2] <= (3, 13) else 1)' \
  || echo "  ! Python $VERSION 은 검증하지 않은 버전입니다(3.11~3.13 권장). 계속 진행합니다."
echo "  ✔ $PYTHON ($VERSION)"

step "가상환경 만들기 (BackEnd/.venv)"
if [ -x .venv/bin/python ] && ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
  echo "  ! 망가진 .venv 를 지우고 다시 만듭니다"
  rm -rf .venv
fi
if [ ! -x .venv/bin/python ]; then
  "$PYTHON" -m venv .venv || {
    rm -rf .venv
    fail "가상환경을 만들지 못했습니다. Ubuntu/Debian 이면 venv 패키지를 설치한 뒤 다시 실행하세요:
    sudo apt install python3-venv     (python3.12 를 쓰면 python3.12-venv)"
  }
fi
.venv/bin/python -m pip install --upgrade pip --quiet

step "파이썬 패키지 설치 — 처음에는 몇 분 걸립니다(약 600MB)"
.venv/bin/python -m pip install -r requirements-dev.txt

step "설치 확인"
.venv/bin/python -c "import amigo_rag, amigo_agent, app.main; print('  ✔ amigo_rag, amigo_agent, app 불러오기 성공')"

if [ ! -f .env ]; then
  cp .env.example .env
  echo "  ✔ .env 생성(.env.example 복사) — Claude 를 쓰려면 ANTHROPIC_API_KEY 를 입력하세요"
fi

if [ -f "$ROOT/FrontEnd/package.json" ]; then
  step "프론트엔드 패키지 설치 (npm install)"
  if command -v npm >/dev/null 2>&1; then
    (cd "$ROOT/FrontEnd" && npm install --no-audit --no-fund)
  else
    echo "  ! npm 이 없어 건너뜁니다. Node.js 20.19 이상(또는 22.12 이상)을 설치한 뒤 FrontEnd 에서 'npm install' 하세요."
  fi
fi

printf '\n\033[1;32m✔ 준비 완료\033[0m\n'
cat <<EOF

  백엔드 실행      : cd $BACKEND && ./scripts/dev.sh          → http://localhost:8000/docs
  프론트엔드 실행  : cd $ROOT/FrontEnd && npm run dev        → http://localhost:5173
  둘 다 한 번에    : cd $BACKEND && ./scripts/start-all.sh
EOF
