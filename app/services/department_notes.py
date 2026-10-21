"""부서별 특이사항(부서 / 중요문서 위치 / 관리 가맹점 / 라이센스 업체): 소속(부서 + 팀)에 맞는 정보를 AI 에이전트가 읽는 모양으로 돌려준다.

사내 '부서별 특이사항' 문서는 보안상 저장소에 넣을 수 없으므로, 기본값은 목업 데이터의 부서에 맞춰 새로 쓴 가상 예시(app/resources/department_notes.json)다.
실제 요약본을 쓰려면 같은 모양의 JSON 파일을 만들어 환경변수 AMIGO_DEPT_NOTES_FILE 에 경로를 적는다(저장소 밖에 둔다).

AI 는 인계자 정보(profile)의 dept_notes 키로 받아, 분석(자료에 어떻게 나오는지 확인)과 질문(자료에 없는 것을 인계자에게 확인)과 대화(답변 정리)에 반영한다.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from amigo_agent.dept_info import clean_dept_info, has_info

from .guidelines import organization_key

DEFAULT_FILE = Path(__file__).resolve().parents[1] / "resources" / "department_notes.json"


@lru_cache(maxsize=4)
def _load(path: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []
    rows = data.get("departments") if isinstance(data, dict) else None
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _entries(row: dict[str, Any]) -> dict[str, list]:
    """파일의 한글 키(중요문서 위치·관리 가맹점·라이센스 업체)를 에이전트가 읽는 영문 키로 바꾼다."""
    return {
        "documents": [{"name": d.get("문서"), "location": d.get("위치")} for d in row.get("중요문서 위치") or [] if isinstance(d, dict)],
        "franchises": [f for f in row.get("관리 가맹점") or [] if isinstance(f, str)],
        "licenses": [{"vendor": l.get("업체"), "product": l.get("라이센스")} for l in row.get("라이센스 업체") or [] if isinstance(l, dict)],
    }


def department_notes_for(organization: str, path: str | None = None) -> dict[str, Any] | None:
    """소속(부서 + 팀)의 특이사항(중요문서 위치·관리 가맹점·라이센스 업체)과 다른 부서의 정보. 소속 비교는 공백·가운뎃점을 같게 본다. 없으면 None."""
    key = organization_key(organization)
    if not key:
        return None
    rows = _load(path or os.getenv("AMIGO_DEPT_NOTES_FILE") or str(DEFAULT_FILE))
    own = next((r for r in rows if organization_key(str(r.get("부서") or "")) == key), None)
    if own is None:
        return None
    others = {str(r.get("부서")): _entries(r) for r in rows if r is not own and r.get("부서")}
    info = clean_dept_info({"department": key, **_entries(own), "others": others})
    return info if has_info(info) else None
