"""직제규정 시행세칙(업무분장): 본부·부 → 팀 → 단위업무 목록. 인계자 정보 입력 화면이 팀의 단위업무를 고르는 데 쓴다.

실제 기관의 직제규정 시행세칙은 저장소에 넣을 수 없으므로, 기본값은 목업 데이터의 본부·팀에 맞춰 새로 쓴 가상 예시(app/resources/org_duties.json)다.
실제 업무분장을 쓰려면 같은 모양의 JSON 파일을 만들어 환경변수 AMIGO_ORG_DUTIES_FILE 에 경로를 적는다(저장소 밖에 둔다).
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

DEFAULT_FILE = Path(__file__).resolve().parents[1] / "resources" / "org_duties.json"


def _names(values: Any) -> list[str]:
    """문자열 목록만 남긴다(앞뒤 공백 제거, 빈 값·중복 제외, 순서 유지)."""
    if not isinstance(values, list):
        return []
    out: list[str] = []
    for value in values:
        if isinstance(value, str) and value.strip() and value.strip() not in out:
            out.append(value.strip())
    return out


@lru_cache(maxsize=4)
def _load(path: str) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    divisions = []
    for division in data.get("divisions") or []:
        if not isinstance(division, dict) or not str(division.get("name") or "").strip():
            continue
        teams = [
            {"name": str(team["name"]).strip(), "duties": _names(team.get("duties"))}
            for team in division.get("teams") or []
            if isinstance(team, dict) and str(team.get("name") or "").strip()
        ]
        divisions.append({"name": str(division["name"]).strip(), "teams": teams})
    return {"title": str(data.get("title") or ""), "note": str(data.get("note") or ""), "divisions": divisions}


def org_duties(path: str | None = None) -> dict[str, Any]:
    """{title, note, divisions: [{name, teams: [{name, duties: [단위업무]}]}]}. 파일이 없거나 깨졌으면 divisions 가 빈 목록이다."""
    return _load(path or os.getenv("AMIGO_ORG_DUTIES_FILE") or str(DEFAULT_FILE))


def team_duties(division: str, team: str, path: str | None = None) -> list[str]:
    """본부·부와 팀의 단위업무 목록(없으면 빈 목록)."""
    for row in org_duties(path)["divisions"]:
        if row["name"] == division.strip():
            return next((t["duties"] for t in row["teams"] if t["name"] == team.strip()), [])
    return []
