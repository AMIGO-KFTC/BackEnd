"""부서・팀(반, 사무소)별 인수인계 목록(부팀명 / 목록 / 비고): 소속(부 + 팀)에 맞는 목록을 AI 에이전트가 읽는 모양으로 돌려준다.

사내 '부서・팀(반, 사무소)별 인수인계 목록'은 보안상 저장소에 넣을 수 없으므로, 기본값은 목업 데이터의 부·팀에 맞춰 새로 쓴 가상 예시(app/resources/department_notes.json)다.
실제 목록을 쓰려면 같은 모양의 JSON 파일을 만들어 환경변수 AMIGO_DEPT_NOTES_FILE 에 경로를 적는다(저장소 밖에 둔다).

파일은 원본 표처럼 부 아래에 팀(반, 사무소)을 둔다: {"부서": [{"부팀명": "ooo부", "목록": [], "비고": "", "팀": [{"부팀명": "ㅁㅁㅁ팀", "목록": [...], "비고": "..."}]}]}
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


def _entries(row: dict[str, Any]) -> dict[str, Any]:
    """파일의 한글 키(목록·비고)를 에이전트가 읽는 영문 키로 바꾼다."""
    items = row.get("목록")
    return {"items": [v for v in items if isinstance(v, str)] if isinstance(items, list) else [], "note": str(row.get("비고") or "")}


@lru_cache(maxsize=4)
def _load(path: str) -> list[tuple[str, dict[str, Any]]]:
    """(소속 이름, 목록·비고) 를 표의 순서대로 펼친다. 팀은 '부 팀', 부 자체는 '부' 가 소속 이름이다."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []
    divisions = data.get("부서") if isinstance(data, dict) else None
    units: list[tuple[str, dict[str, Any]]] = []
    for division in divisions if isinstance(divisions, list) else []:
        if not isinstance(division, dict) or not str(division.get("부팀명") or "").strip():
            continue
        name = str(division["부팀명"]).strip()
        units.append((name, _entries(division)))
        teams = division.get("팀")
        for team in teams if isinstance(teams, list) else []:
            if isinstance(team, dict) and str(team.get("부팀명") or "").strip():
                units.append((f"{name} {str(team['부팀명']).strip()}", _entries(team)))
    return units


def department_notes_for(organization: str, path: str | None = None) -> dict[str, Any] | None:
    """소속(부 + 팀)의 인수인계 목록과 다른 부·팀의 목록. 소속 비교는 공백·가운뎃점을 같게 본다. 목록·비고가 모두 비어 있거나 없으면 None."""
    key = organization_key(organization)
    if not key:
        return None
    units = _load(path or os.getenv("AMIGO_DEPT_NOTES_FILE") or str(DEFAULT_FILE))
    own = next((entries for name, entries in units if organization_key(name) == key), None)
    if own is None:
        return None
    others = {name: entries for name, entries in units if organization_key(name) != key and (entries["items"] or entries["note"])}
    info = clean_dept_info({"department": key, **own, "others": others})
    return info if has_info(info) else None
