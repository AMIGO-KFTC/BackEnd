"""주요 사업계획(경영정보시스템 주요 업무보고 단위): 본부·부 → 팀 → 사업계획 목록. 팀장(원내 양식) 인수인계서에서 인계할 사업계획을 고르는 데 쓴다.

실제 기관의 주요 업무보고는 저장소에 넣을 수 없으므로, 기본값은 목업 데이터의 본부·팀에 맞춰 새로 쓴 가상 예시(app/resources/business_plans.json)다.
실제 목록을 쓰려면 같은 모양의 JSON 파일을 만들어 환경변수 AMIGO_BUSINESS_PLANS_FILE 에 경로를 적는다(저장소 밖에 둔다).
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from .org_duties import _names

DEFAULT_FILE = Path(__file__).resolve().parents[1] / "resources" / "business_plans.json"


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
            {"name": str(team["name"]).strip(), "plans": _names(team.get("plans"))}
            for team in division.get("teams") or []
            if isinstance(team, dict) and str(team.get("name") or "").strip()
        ]
        divisions.append({"name": str(division["name"]).strip(), "teams": teams})
    return {"title": str(data.get("title") or ""), "note": str(data.get("note") or ""), "divisions": divisions}


def business_plans(path: str | None = None) -> dict[str, Any]:
    """{title, note, divisions: [{name, teams: [{name, plans: [사업계획]}]}]}. 파일이 없거나 깨졌으면 divisions 가 빈 목록이다."""
    return _load(path or os.getenv("AMIGO_BUSINESS_PLANS_FILE") or str(DEFAULT_FILE))


def team_plans(division: str, team: str, path: str | None = None) -> list[str]:
    """본부·부와 팀의 주요 사업계획 목록(없으면 빈 목록)."""
    for row in business_plans(path)["divisions"]:
        if row["name"] == division.strip():
            return next((t["plans"] for t in row["teams"] if t["name"] == team.strip()), [])
    return []
