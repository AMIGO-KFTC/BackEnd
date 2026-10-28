"""팀 인수인계서 지침: 저장된 값을 AI 에이전트가 읽는 모양으로 바꾼다.

팀 관리자가 관리자 화면에서 적은 '우리 팀 인수인계서에 꼭 들어가야 하는 항목'과 '팀 특징·작성 지침'은 소속(부서 + 팀) 단위로 team_guidelines 테이블에 저장된다.
AI 에이전트는 인계자 정보(profile)의 team_guidelines 키로 이 값을 받아, 장별 분석 프롬프트와 질의응답 프롬프트에 끼워 넣는다(AI/src/amigo_agent/prompts.py).
"""

from __future__ import annotations

import json
import re
from typing import Any

from amigo_agent.prompts import clean_team_guidelines, team_guidelines_block
from sqlalchemy.orm import Session

from ..models import TeamGuideline


_SEPARATORS = re.compile(r"[\s·ㆍ・]+")


def organization_key(organization: str) -> str:
    """소속 문자열을 비교용 키로 만든다. 프로필은 '디지털전략부 웹서비스팀', 화면에서 만든 세션은 '디지털전략부 · 웹서비스팀' 처럼 구분 문자가 달라서 맞춘다."""
    return _SEPARATORS.sub(" ", organization or "").strip()


def stored_items(row: TeamGuideline | None) -> list[dict[str, Any]]:
    """저장된 항목 목록(화면용: id 포함). 깨진 값은 빈 목록으로 본다."""
    if row is None:
        return []
    try:
        items = json.loads(row.items_json or "[]")
    except ValueError:
        return []
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def stored_glossary(row: TeamGuideline | None) -> list[dict[str, Any]]:
    """저장된 업무 용어집(화면용). 깨진 값은 빈 목록으로 본다."""
    if row is None:
        return []
    try:
        terms = json.loads(row.glossary_json or "[]")
    except ValueError:
        return []
    return [t for t in terms if isinstance(t, dict)] if isinstance(terms, list) else []


def agent_guidelines(row: TeamGuideline | None) -> dict[str, Any] | None:
    """에이전트에 넘길 지침(profile["team_guidelines"]). 비어 있으면 None."""
    if row is None:
        return None
    cleaned = clean_team_guidelines(
        {"items": stored_items(row), "notes": row.notes, "prompt": row.prompt or "", "intro": row.intro or "", "glossary": stored_glossary(row)}
    )
    return cleaned if any(cleaned[key] for key in ("items", "notes", "prompt", "intro", "glossary")) else None


def guidelines_for(db: Session, organization: str) -> dict[str, Any] | None:
    """소속(부서 + 팀)의 지침을 에이전트가 읽는 모양으로. 소속이 없거나 지침이 비어 있으면 None."""
    key = organization_key(organization)
    if not key:
        return None
    return agent_guidelines(db.get(TeamGuideline, key))


def prompt_preview(row: TeamGuideline | None) -> str:
    """관리자 화면에서 'AI 에 이렇게 전달돼요' 로 보여 주는 프롬프트 구간(대화 프롬프트 기준 문구)."""
    return team_guidelines_block(agent_guidelines(row))
