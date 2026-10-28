"""인수인계서 버전 기록: AI 초안과 사용자가 직접 고친 문서를 document_versions 에 쌓고, sessions 에 현재 문서를 둔다.

- AI 가 문서를 만들면(에이전트 'document' 이벤트, 작업 뒤 스냅숏) 새 버전으로 기록한다. 같은 초안을 두 번 기록하지 않도록
  AI 가 마지막으로 내놓은 문서(sessions.ai_document_md)와 내용이 같으면 건너뛴다(분석을 처음부터 다시 하면 AI 번호가 1부터
  다시 시작하므로 번호로는 비교하지 않는다).
- 사용자가 고친 문서·이전 버전 복원도 같은 번호 줄에 이어 붙인다. 그래서 AI 스냅숏이 사용자가 고친 문서를 덮어쓰지 않는다.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from ..models import DocumentVersion, HandoverSession, User

logger = logging.getLogger(__name__)

ORIGIN_LABELS = {"ai": "AI 작성", "user": "직접 수정", "restore": "이전 버전 복원"}


def latest_version(db: Session, session_id: str) -> DocumentVersion | None:
    query = select(DocumentVersion).where(DocumentVersion.session_id == session_id).order_by(DocumentVersion.version.desc()).limit(1)
    return db.scalar(query)


def latest_ai_version(db: Session, session_id: str) -> DocumentVersion | None:
    query = (
        select(DocumentVersion)
        .where(DocumentVersion.session_id == session_id, DocumentVersion.origin == "ai")
        .order_by(DocumentVersion.version.desc())
        .limit(1)
    )
    return db.scalar(query)


def add_version(
    db: Session,
    row: HandoverSession,
    markdown: str,
    *,
    origin: str,
    ai_version: int = 0,
    based_on: int = 0,
    note: str = "",
    author: User | None = None,
) -> DocumentVersion:
    """새 버전을 만들고 세션의 현재 문서로 삼는다(커밋은 호출한 쪽에서)."""
    last = db.scalar(select(func.max(DocumentVersion.version)).where(DocumentVersion.session_id == row.id)) or 0
    number = max(last, row.document_version or 0) + 1
    version = DocumentVersion(
        session_id=row.id,
        version=number,
        origin=origin,
        ai_version=ai_version,
        based_on=based_on,
        markdown=markdown,
        note=note[:300],
        author_id=author.id if author is not None else None,
        author_name=author.name if author is not None else "",
    )
    db.add(version)
    row.document_md = markdown
    row.document_version = number
    return version


def record_ai_document(db: Session, row: HandoverSession, markdown: str, ai_version: int) -> DocumentVersion | None:
    """AI 가 만든 문서를 기록한다. AI 가 마지막으로 내놓은 문서와 같으면(이미 기록함) 아무것도 하지 않는다."""
    if not markdown:
        return None
    seen = row.ai_document_md
    if not seen:  # 이 컬럼이 생기기 전에 기록한 세션
        last_ai = latest_ai_version(db, row.id)
        seen = last_ai.markdown if last_ai is not None else ""
    if seen == markdown:
        row.ai_document_md = markdown
        return None
    row.ai_document_md = markdown
    note = f"AI 초안 v{ai_version}" if ai_version else "AI 초안"
    return add_version(db, row, markdown, origin="ai", ai_version=ai_version, note=note)


# 월별 일정표 한 행: <tr><td>이름</td>1~12월 칸<td>진행상황 요약</td></tr> (amigo_agent.render._render_gantt)
_GANTT_ROW = re.compile(r'^(<tr><td>(.*?)</td>)((?:<td></td>|<td class="bar"[^>]*>&nbsp;</td>)+)(<td>[^<]*</td></tr>)\s*$', re.M)


def merge_gantt_rows(edited: str, rendered: str) -> str:
    """직접 고친 문서에 AI 가 다시 그린 월별 막대(1~12월 칸)만 옮긴다. 이름·진행상황 요약 등 직접 고친 글은 그대로 둔다.

    같은 이름의 행은 나온 순서대로 짝짓는다. 직접 고친 문서에서 지웠거나 이름을 바꾼 행은 건드리지 않는다.
    """
    bars: dict[tuple[str, int], str] = {}
    seen: dict[str, int] = {}
    for m in _GANTT_ROW.finditer(rendered):
        k = seen.get(m.group(2), 0)
        seen[m.group(2)] = k + 1
        bars[(m.group(2), k)] = m.group(3)
    used: dict[str, int] = {}

    def swap(m: re.Match) -> str:
        k = used.get(m.group(2), 0)
        used[m.group(2)] = k + 1
        return m.group(1) + bars.get((m.group(2), k), m.group(3)) + m.group(4)

    return _GANTT_ROW.sub(swap, edited)


DEPT_FILES_HEADING = "## 16. 부서·팀(반, 사무소)별 추가자료"


def _section_span(lines: list[str], heading: str) -> tuple[int, int] | None:
    """heading 줄부터 다음 '## ' 제목 앞(또는 끝)까지의 줄 범위. 제목 글을 고쳤어도 같은 장 번호('## 16.')면 같은 장으로 본다."""
    number = heading.split(" ", 2)[1]  # "16."
    start = next((i for i, line in enumerate(lines) if line.rstrip() == heading), None)
    if start is None:
        start = next((i for i, line in enumerate(lines) if line.startswith(f"## {number}")), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return start, end


def replace_section(edited: str, rendered: str, heading: str = DEPT_FILES_HEADING) -> str:
    """직접 고친 문서의 한 장(heading 부터 다음 '## ' 제목 앞까지)만 AI 가 다시 그린 문서의 같은 장으로 바꾼다. 다른 장의 직접 고친 글은 그대로 둔다.

    다시 그린 문서에 그 장이 없으면 고치지 않고, 직접 고친 문서에서 그 장을 지웠으면 문서 끝에 붙인다.
    """
    new_lines = rendered.splitlines()
    new_span = _section_span(new_lines, heading)
    if new_span is None:
        return edited
    section = new_lines[new_span[0] : new_span[1]]
    lines = edited.splitlines()
    span = _section_span(lines, heading)
    if span is None:
        return "\n".join([*lines, "", *section]).strip("\n") + "\n"
    if span[1] < len(lines) and section and section[-1].strip():  # 다음 장과 붙지 않게 빈 줄을 둔다
        section = [*section, ""]
    merged = "\n".join([*lines[: span[0]], *section, *lines[span[1] :]])
    return merged + "\n" if edited.endswith("\n") and not merged.endswith("\n") else merged


def backfill_versions(session_factory: sessionmaker) -> int:
    """버전 기록이 생기기 전에 만든 문서(sessions.document_md)를 첫 버전으로 옮긴다. 옮긴 세션 수를 돌려준다."""
    with session_factory() as db:
        has_versions = select(DocumentVersion.id).where(DocumentVersion.session_id == HandoverSession.id).exists()
        rows = list(db.scalars(select(HandoverSession).where(HandoverSession.document_md != "", ~has_versions)))
        for row in rows:
            number = row.document_version or 1
            db.add(
                DocumentVersion(
                    session_id=row.id, version=number, origin="ai", ai_version=number, markdown=row.document_md, note="기존 문서", created_at=row.updated_at
                )
            )
            row.document_version = number
        db.commit()
    if rows:
        logger.info("인수인계서 %d건을 버전 기록으로 옮겼습니다.", len(rows))
    return len(rows)
