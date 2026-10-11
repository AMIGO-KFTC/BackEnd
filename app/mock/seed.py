"""가상 응답 모드의 데모 데이터: 서버를 켤 때마다 같은 상태로 다시 만든다.

데모 계정 demo / demo1234 (김민수 · 디지털전략부 웹서비스팀 과장)에 진행 단계가 다른 단위업무 3건을 넣어 두어,
화면이 각 상태(문서 완성 / 질문에 답할 차례 / 자료 올리기 전)를 바로 그려 볼 수 있게 한다.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from ..models import ConversationRead, FileBlob, Message, Source, UnitTask, User, UserProfile
from ..security import hash_password
from . import scenario

if TYPE_CHECKING:
    from ..services.container import Services

DEMO_LOGIN, DEMO_PASSWORD = "demo", "demo1234"
DEMO_FILES = ["업무정의서_웹서비스팀.pdf", "홈페이지_운영매뉴얼.hwpx", "주간회의록_2026-09-22.docx", "시스템_계정목록.xlsx", "재무팀_유지보수대금_요청.eml", "웹접근성_개선계획.pptx"]


def seed_demo(services: Services) -> None:
    from ..routers.sessions import new_handover_session
    from ..services.runner import EventRecorder

    agent = services.agent
    agent_delay, agent.delay = agent.delay, 0  # 시드는 기다리지 않고 바로 만든다
    try:
        with services.db() as db:
            user = User(login_id=DEMO_LOGIN, password_hash=hash_password(DEMO_PASSWORD), name="김민수")
            user.profile = UserProfile(department="디지털전략부", team="웹서비스팀", position="과장")
            db.add(user)
            db.flush()
            plans = [
                ("기관 홈페이지 운영", "홈페이지 콘텐츠 게시·승인, 메뉴 관리, 유지보수 업체 관리", DEMO_FILES, "document"),
                ("웹 접근성 관리", "분기별 자체 점검, 연 1회 품질인증 갱신", DEMO_FILES[1:4], "question"),
                ("유지보수 계약 관리", "(주)한빛웹솔루션 유지보수 계약·검수·대금 지급", [], "setup"),
            ]
            created = []
            for order, (name, description, files, _) in enumerate(plans, start=1):
                row = new_handover_session(
                    db, user_id=user.owner_key, owner_name=user.name, organization="디지털전략부 웹서비스팀", position="과장",
                    duties=f"{name}: {description}", title=f"{name} 인수인계", task_name=name, handover_date="2026-10-30",
                    welcome=f"'{name}' 업무의 자료를 올려 주세요. 자료 처리가 끝나면 바로 분석을 시작할게요.",
                )
                db.add(UnitTask(id=uuid.uuid4().hex, user_id=user.id, session=row, name=name, description=description, sort_order=order))
                for file_name in files:
                    content = f"(가상 응답 모드 데모 자료) {file_name}\n".encode()
                    source = Source(id=uuid.uuid4().hex[:12], session_id=row.id, kind="file", name=file_name, extension="." + file_name.rsplit(".", 1)[-1],
                                    size=len(content), status="ready", chunk_count=6)
                    db.add(source)
                    db.flush()
                    db.add(FileBlob(source_id=source.id, sha256=hashlib.sha256(content).hexdigest(), content=content))
                    services.kb(row.id)._register(source.id, file_name, source.extension.lstrip("."), 6000)
                created.append(row)
            db.commit()
            sessions = [(row.id, row.profile()) for row in created]

        for (session_id, profile), (_, _, files, goal) in zip(sessions, plans):
            if goal == "setup":
                continue
            record = EventRecorder(services, session_id)
            kb = services.kb(session_id)
            agent.start(session_id, profile, kb, on_event=record)
            if goal == "document":
                for answer in scenario.DEMO_ANSWERS:
                    _user(services, session_id, answer)
                    agent.send_message(session_id, answer, kb, on_event=record)
                    _user(services, session_id, "네, 맞아요")
                    agent.send_message(session_id, "네, 맞아요", kb, on_event=record)
                _user(services, session_id, "지금까지 내용으로 인수인계서를 만들어 주세요.", kind="command")
                agent.finish(session_id, kb, on_event=record)
            services.runner._sync_snapshot(session_id)
            if goal == "document":  # 문서까지 본 대화는 모두 읽은 것으로
                with services.db() as db:
                    last = db.scalar(select(func.max(Message.id)).where(Message.session_id == session_id))
                    user_id = db.scalar(select(User.id).where(User.login_id == DEMO_LOGIN))
                    db.add(ConversationRead(session_id=session_id, user_id=user_id, last_read_message_id=last))
                    db.commit()
    finally:
        agent.delay = agent_delay


def _user(services: Services, session_id: str, text: str, kind: str = "answer") -> None:
    with services.db() as db:
        db.add(Message(session_id=session_id, role="user", kind=kind, content=text, meta_json=json.dumps({})))
        db.commit()
