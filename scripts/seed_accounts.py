r"""시연용 계정·팀 담당 업무·인수인계서를 실제 DB(data/app.db 또는 AMIGO_DATABASE_URL)에 넣는다. 여러 번 실행해도 같은 상태가 된다.

    .venv\Scripts\python.exe scripts\seed_accounts.py            # 계정·프로필·역할·팀 업무·인수인계·캘린더를 맞춘다
    .venv\Scripts\python.exe scripts\seed_accounts.py --clean    # + 시드 계정의 빈 인수인계(자료·문서 없음, 시드가 만든 것 제외)를 지운다

조직(디지털본부) · 아이디 / 비밀번호 · 사번
  IT아미고팀  팀장 김미고   amigoteamjang / amigoteamjang1234   1100101 (관리자)
              대리 이도윤   doyun / doyun1234                   1210604
              계장 김민준   demo / demo1234                     1111111 (시연용)
              계장 최환     hwan / hwan1234                     1251023 (시연용: 완성 문서·받은 인수인계가 가장 많음)
              계장 박서준   seojun / seojun1234                 1240315
              사원 정하은   haeun / haeun1234                   1260108
  AI개발팀    팀장 최고미   aiteamjang / aiteamjang1234         1100202 (관리자)
              대리 한태양   taeyang / taeyang1234               1220911
              계장 임윤희   yunhui / yunhui1234                 1231207
              사원 송지민   jimin / jimin1234                   1260212
  정보보호팀  팀장 남궁현   secteamjang / secteamjang1234       1100303 (관리자)
              대리 이수진   sujin / sujin1234                   1190827

인수인계서와 전송 관계는 scripts/seed_documents.py 의 HANDOVERS 에 있다(완성 문서는 근거 자료 파일·버전 기록·대화 기록까지 만든다).
이미 문서가 있는 인수인계는 다시 만들지 않으므로 화면에서 고친 내용은 그대로 남는다. 근거 자료는 서버를 다시 켜면 지식베이스에 적재된다.
팀장(관리자)도 자기 인수인계서를 쓰고 보낼 수 있다. 팀원 인수인계서는 읽기(검토)만 한다.
예전 시드가 만든 팀장 인수인계(김미고 → 최고미)는 실행할 때 지운다.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy import create_engine, func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.models import (  # noqa: E402
    Base, ConversationRead, DocumentVersion, FileBlob, HandoverSession, HandoverTransfer, Message, PlannerEvent, PlannerTodo, Source, TeamDuty, UnitTask, User, UserProfile,
)
from app.security import hash_password  # noqa: E402
from app.services.guidelines import organization_key  # noqa: E402
from seed_documents import HANDOVERS, HWAN_EVENTS, HWAN_TODOS, TEAM_DUTIES  # noqa: E402

DIVISION = "디지털본부"  # 부서(본부)

# (아이디, 비밀번호, 이름, 부서, 팀, 직위, 역할: admin = 관리자(users.is_admin) / member, 사번: 아이디와는 별개)
ACCOUNTS = [
    ("amigoteamjang", "amigoteamjang1234", "김미고", DIVISION, "IT아미고팀", "팀장", "admin", "1100101"),
    ("doyun", "doyun1234", "이도윤", DIVISION, "IT아미고팀", "대리", "member", "1210604"),
    ("demo", "demo1234", "김민준", DIVISION, "IT아미고팀", "계장", "member", "1111111"),
    ("hwan", "hwan1234", "최환", DIVISION, "IT아미고팀", "계장", "member", "1251023"),
    ("seojun", "seojun1234", "박서준", DIVISION, "IT아미고팀", "계장", "member", "1240315"),
    ("haeun", "haeun1234", "정하은", DIVISION, "IT아미고팀", "사원", "member", "1260108"),
    ("aiteamjang", "aiteamjang1234", "최고미", DIVISION, "AI개발팀", "팀장", "admin", "1100202"),
    ("taeyang", "taeyang1234", "한태양", DIVISION, "AI개발팀", "대리", "member", "1220911"),
    ("yunhui", "yunhui1234", "임윤희", DIVISION, "AI개발팀", "계장", "member", "1231207"),
    ("jimin", "jimin1234", "송지민", DIVISION, "AI개발팀", "사원", "member", "1260212"),
    ("secteamjang", "secteamjang1234", "남궁현", DIVISION, "정보보호팀", "팀장", "admin", "1100303"),
    ("sujin", "sujin1234", "이수진", DIVISION, "정보보호팀", "대리", "member", "1190827"),
]

# 예전 시드가 팀장 계정에 만든 빈 인수인계(인계자 아이디, 단위업무명). 시연 데이터에서 빼려고 지운다.
RETIRED_HANDOVERS = [
    ("amigoteamjang", "IT아미고팀 운영 총괄"),
    ("amigoteamjang", "연간 정보화 예산 관리"),
]

NOW = datetime.now(timezone.utc)


def upsert_accounts(db: Session) -> dict[str, User]:
    users: dict[str, User] = {}
    for login_id, password, name, department, team, position, role, employee_no in ACCOUNTS:
        user = db.scalar(select(User).where(User.login_id == login_id))
        if user is None:
            user = User(login_id=login_id, password_hash=hash_password(password), name=name, email=f"{login_id}@amigo.example")
            db.add(user)
            print(f"계정 생성  {login_id:14} {name}")
        else:
            user.password_hash = hash_password(password)
            user.name = name
            print(f"계정 갱신  {login_id:14} {name}")
        user.is_admin = role == "admin"  # 관리자 권한(users.is_admin): 팀장
        user.employee_no = employee_no
        profile = user.profile or UserProfile(user_id=user.id)
        profile.department, profile.team, profile.position = department, team, position
        user.profile = profile
        db.flush()
        users[login_id] = user
    return users


def upsert_team_duties(db: Session, users: dict[str, User]) -> None:
    """팀장이 관리하는 '우리 팀 업무' 목록. 이름이 같은 업무는 설명·순서만 맞춘다(팀장이 화면에서 더한 업무는 그대로 둔다)."""
    leaders = {u.profile.team: u.name for u in users.values() if u.is_admin and u.profile}
    for team, duties in TEAM_DUTIES.items():
        organization = organization_key(f"{DIVISION} {team}")
        existing = {r.name: r for r in db.scalars(select(TeamDuty).where(TeamDuty.organization == organization))}
        for order, (name, description) in enumerate(duties, start=1):
            row = existing.get(name)
            if row is None:
                row = TeamDuty(id=uuid.uuid4().hex, organization=organization, name=name)
                db.add(row)
            row.description, row.sort_order, row.updated_by = description, order, leaders.get(team, "")
        print(f"팀 업무      {team}: {len(duties)}개")
    db.flush()


def clean_empty_sessions(db: Session, users: dict[str, User]) -> None:
    """시드 계정의 빈 인수인계(자료도 문서도 없는 것)를 지운다. Swagger 로 만든 테스트 껍데기를 치우는 용도."""
    seeded = {(h["owner"], h["duties"][0]) for h in HANDOVERS}
    for user in users.values():
        rows = db.scalars(select(HandoverSession).where(HandoverSession.user_id == user.owner_key)).all()
        for row in rows:
            if row.sources or row.document_version > 0:
                continue
            task = db.scalar(select(UnitTask).where(UnitTask.session_id == row.id))
            if task is not None and (user.login_id, task.name) in seeded:
                continue  # 이 스크립트가 만든 단위업무는 둔다
            print(f"빈 인수인계 삭제  {user.name}: {row.title}")
            if task is not None:
                db.delete(task)
            db.delete(row)
    db.flush()


def remove_retired_handovers(db: Session, users: dict[str, User]) -> None:
    """팀장(관리자) 계정이 가진 예전 시드 인수인계를 지운다(자료·대화·문서 버전은 ORM cascade 로 함께)."""
    for owner_id, name in RETIRED_HANDOVERS:
        owner = users[owner_id]
        task = db.scalar(select(UnitTask).where(UnitTask.user_id == owner.id, UnitTask.name == name))
        if task is None:
            continue
        row = task.session
        db.delete(task)
        if row is not None:
            db.delete(row)
        print(f"팀장 인수인계 삭제  {owner.name}: {name}")
    db.flush()


# ---------------------------------------------------------------- 근거 자료 파일
def _field_lines(slot_key: str, item: dict) -> list[tuple[str, str]]:
    from amigo_agent.template import SLOT_BY_KEY

    labels = {f.key: f.label for f in SLOT_BY_KEY[slot_key].fields}
    return [(labels.get(k, k), v) for k, v in item.items() if v]


def _file_bytes(name: str, owner: User, row: HandoverSession, entries: list[tuple[str, str, dict]], when: datetime) -> tuple[bytes, str]:
    """파일 이름의 확장자에 맞춰, 이 파일을 근거로 단 항목 내용을 담은 예시 자료를 만든다."""
    stem, ext = name.rsplit(".", 1)
    title = stem.replace("_", " ")
    if ext == "csv":
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["구분", "항목", "내용"])
        for slot_title, item_title, item in entries:
            for label, value in item:
                writer.writerow([slot_title, item_title, f"{label}: {value}"])
        return ("﻿" + out.getvalue()).encode("utf-8"), "text/csv"
    body = [f"{title}", f"작성: {owner.name} {row.position} ({row.organization}) · {when:%Y-%m-%d}", ""]
    for slot_title, item_title, item in entries:
        body.append(f"## {item_title}" if ext == "md" else f"[{item_title}]")
        body += [f"- {label}: {value}" for label, value in item]
        body.append("")
    if ext == "md":
        body[0] = f"# {title}"
    text = "\n".join(body)
    if ext == "eml":
        msg = EmailMessage()
        msg["From"] = f"{owner.name} <{owner.email or owner.login_id + '@amigo.example'}>"
        msg["To"] = "IT아미고팀 <it-amigo@amigo.example>"
        msg["Subject"] = title.removeprefix("메일 ").strip()
        msg["Date"] = format_datetime(when.replace(tzinfo=timezone.utc))
        msg.set_content(text.split("\n", 2)[-1] + "\n감사합니다.\n" + owner.name + " 드림\n")
        return msg.as_bytes(), "message/rfc822"
    return text.encode("utf-8"), "text/markdown" if ext == "md" else "text/plain"


def _build_document(db: Session, spec: dict, row: HandoverSession, owner: User, users: dict[str, User], settings, created: datetime) -> None:
    """장별 항목 → 근거 자료 파일·슬롯 상태·인수인계서(v1, 필요하면 직접 수정 v2)·대화 기록·전송을 만든다."""
    from amigo_agent.render import render_document
    from amigo_agent.state import evaluate_coverage, item_label
    from amigo_agent.template import SLOT_BY_KEY, slots_for

    files = spec.get("files") or []
    sources: list[Source] = []
    for i, name in enumerate(files):
        source = Source(id=uuid.uuid4().hex[:12], session_id=row.id, kind="file", name=name, extension="." + name.rsplit(".", 1)[-1],
                        status="ready", added_stage="setup", created_at=created + timedelta(minutes=2 + i))
        source.stored_path = str(Path(settings.data_dir) / "uploads" / row.id / f"{source.id}{source.extension}")
        sources.append(source)

    # 항목마다 근거 파일을 돌아가며 단다
    slots: dict[str, dict] = {}
    cited: dict[str, list[tuple[str, str, list[tuple[str, str]]]]] = {s.id: [] for s in sources}
    counter = 0
    for spec_slot in slots_for(row.profile()):
        key = spec_slot.key
        items = []
        for i, fields in enumerate(spec["slots"].get(key, []), start=1):
            item = {"id": f"{key}-{i:02d}", "title": fields.get(spec_slot.title_field, ""), "fields": dict(fields), "origin": "document", "confirmed": True, "citations": []}
            if sources:
                source = sources[counter % len(sources)]
                counter += 1
                quote = max((v for v in fields.values() if v), key=len, default="")[:80]
                item["citations"] = [{"source_id": source.id, "label": source.name, "quote": quote}]
                cited[source.id].append((spec_slot.title, item["title"], _field_lines(key, fields)))
            items.append(item)
        labels = [item_label(SLOT_BY_KEY[key], it) for it in items]
        slots[key] = {"key": key, "items": items, "coverage": evaluate_coverage(spec_slot, items), "note": "",
                      "summary": ", ".join(labels[:3]) + (f" 외 {len(labels) - 3}건" if len(labels) > 3 else "")}

    for source in sources:
        content, content_type = _file_bytes(source.name, owner, row, cited[source.id], created)
        source.size, source.chunk_count = len(content), 0
        path = Path(source.stored_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        db.add(source)
        db.flush()
        db.add(FileBlob(source_id=source.id, content_type=content_type, sha256=hashlib.sha256(content).hexdigest(), content=content))

    render_sources = [{"source_id": s.id, "name": s.name, "type": s.extension.lstrip(".")} for s in sources]
    markdown = render_document(profile=row.profile(), slots=slots, gaps=[], sources=render_sources, version=1, today=created.date() + timedelta(days=1))
    composed = created + timedelta(hours=3)
    row.slots_json, row.gaps_json = json.dumps(slots, ensure_ascii=False), "[]"
    row.stage, row.status, row.engine = "review", "waiting", "claude"
    row.progress_message, row.question_count = "인수인계서 초안(v1)을 작성했어요.", 3
    row.document_md = row.ai_document_md = markdown
    row.document_version = 1
    db.add(DocumentVersion(session_id=row.id, version=1, origin="ai", ai_version=1, markdown=markdown, note="AI 초안", created_at=composed))

    db.add(Message(session_id=row.id, role="assistant", kind="info", created_at=created + timedelta(minutes=20),
                   content=f"올려 주신 자료 {len(sources)}건을 분석했어요. 빠진 내용 3가지를 여쭤볼게요."))
    db.add(Message(session_id=row.id, role="user", kind="command", content="지금까지 내용으로 인수인계서를 만들어 주세요.", created_at=composed - timedelta(minutes=5)))
    db.add(Message(session_id=row.id, role="assistant", kind="document", meta_json=json.dumps({"version": 1}), created_at=composed,
                   content="인수인계서 초안(v1)을 작성했어요. 문서 탭에서 확인해 주세요.\n고칠 내용이나 빠진 내용을 채팅으로 알려 주시면 반영해서 다시 작성할게요."))

    sent_at = composed + timedelta(hours=20)
    v1_markdown = markdown
    if spec.get("edit"):  # 보낸 뒤 인계자가 '직접 수정하기'로 고친 버전(받은 인수인계에도 최신본이 뜬다)
        edited = markdown.rstrip() + f"\n\n> 인계자 메모({(sent_at + timedelta(days=1)):%Y-%m-%d}): {spec['edit']}\n"
        db.add(DocumentVersion(session_id=row.id, version=2, origin="user", based_on=1, markdown=edited, note=spec["edit"],
                               author_id=owner.id, author_name=owner.name, created_at=sent_at + timedelta(days=1)))
        row.document_md, row.document_version = edited, 2

    read_by = set(spec.get("read") or [])
    for login_id, message in spec.get("send") or []:
        recipient = users[login_id]
        db.add(HandoverTransfer(
            id=uuid.uuid4().hex, session_id=row.id, sender_id=owner.id, recipient_id=recipient.id, title=row.title, task_name=row.task_name,
            mode=row.mode, sender_name=row.owner_name, sender_organization=row.organization, sender_position=row.position,
            handover_date=row.handover_date, document_version=1, markdown=v1_markdown, message=message, created_at=sent_at,
            read_at=sent_at + timedelta(hours=2) if login_id in read_by else None,
        ))
        print(f"    전송  {owner.name} → {recipient.name}")
    db.flush()
    last = db.scalar(select(func.max(Message.id)).where(Message.session_id == row.id)) or 0
    db.merge(ConversationRead(session_id=row.id, user_id=owner.id, last_read_message_id=last))


def upsert_handovers(db: Session, users: dict[str, User], settings) -> None:
    from app.routers.sessions import new_handover_session
    from app.routers.unit_tasks import _welcome

    orders: dict[str, int] = {}
    for spec in HANDOVERS:
        owner = users[spec["owner"]]
        profile = owner.profile
        name, extra = spec["duties"][0], spec["duties"][1:]
        description = spec.get("description", "")
        duties = "\n".join([f"{name}: {description}" if description else name, *extra])  # 한 줄에 업무 하나(첫 줄에 설명)
        successor = users.get(spec.get("successor") or "")
        created = NOW - timedelta(days=spec.get("days_ago", 0), hours=6)
        leave_start, leave_end = spec.get("leave", ("", ""))
        orders[owner.login_id] = orders.get(owner.login_id, 0) + 1
        task = db.scalar(select(UnitTask).where(UnitTask.user_id == owner.id, UnitTask.name == name))
        if task is None:
            row = new_handover_session(
                db, user_id=owner.owner_key, owner_name=owner.name, organization=profile.organization, position=profile.position,
                duties=duties, title=f"{name} 인수인계", task_name=name, mode=spec.get("mode", "reassignment"),
                successor=f"{successor.name} {successor.profile.position}" if successor else "", successor_login_id=successor.login_id if successor else "",
                handover_date=spec.get("date", ""), leave_start=leave_start, leave_end=leave_end, welcome=_welcome(name),
            )
            row.created_at = created
            task = UnitTask(id=uuid.uuid4().hex, user_id=owner.id, session=row, name=name, description=description, sort_order=orders[owner.login_id])
            db.add(task)
            db.flush()
            for message in row.messages:
                message.created_at = created
            print(f"인수인계 생성  {owner.name}: {name}{' 외 ' + str(len(extra)) + '건' if extra else ''}")
        else:
            row = task.session
            print(f"인수인계 갱신  {owner.name}: {name}")
        row.owner_name, row.organization, row.position = owner.name, profile.organization, profile.position
        row.duties, task.description = duties, description
        row.mode, row.leave_start, row.leave_end, row.handover_date = spec.get("mode", "reassignment"), leave_start, leave_end, spec.get("date", "")
        if successor:
            row.successor, row.successor_login_id = f"{successor.name} {successor.profile.position}", successor.login_id
        if spec.get("slots") and not row.document_version:  # 문서는 한 번만 만든다(화면에서 고친 내용 보존)
            _build_document(db, spec, row, owner, users, settings, created)
        db.flush()
        row.updated_at = created + timedelta(days=1 + (1 if spec.get("edit") else 0)) if row.document_version else created


def upsert_planner(db: Session, user: User) -> None:
    """최환의 캘린더 일정·할 일(키가 같으면 다시 만들지 않는다)."""
    for i, (date, text) in enumerate(HWAN_EVENTS, start=1):
        key = f"seed-e{i:02d}"
        if db.scalar(select(PlannerEvent.id).where(PlannerEvent.user_id == user.id, PlannerEvent.key == key)) is None:
            db.add(PlannerEvent(user_id=user.id, key=key, date=date, text=text))
    for i, (text, done, date) in enumerate(HWAN_TODOS, start=1):
        key = f"seed-t{i:02d}"
        if db.scalar(select(PlannerTodo.id).where(PlannerTodo.user_id == user.id, PlannerTodo.key == key)) is None:
            db.add(PlannerTodo(user_id=user.id, key=key, text=text, done=done, date=date))
    print(f"캘린더      {user.name}: 일정 {len(HWAN_EVENTS)}개 · 할 일 {len(HWAN_TODOS)}개")


def main() -> None:
    settings = get_settings()
    engine = create_engine(settings.db_url)
    Base.metadata.create_all(engine)
    from app.services.container import _add_missing_columns

    _add_missing_columns(engine)
    with Session(engine) as db:
        users = upsert_accounts(db)
        upsert_team_duties(db, users)
        if "--clean" in sys.argv:
            clean_empty_sessions(db, users)
        remove_retired_handovers(db, users)
        upsert_handovers(db, users, settings)
        upsert_planner(db, users["hwan"])
        db.commit()
    print("완료")


if __name__ == "__main__":
    main()
