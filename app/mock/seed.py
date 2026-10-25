"""가상 응답 모드의 데모 데이터: 서버를 켤 때마다 같은 상태로 다시 만든다.

데모 계정 demo / demo1234 (김민수 · 디지털전략부 웹서비스팀 과장, 관리자는 admin / admin1234)에 진행 단계가 다른 단위업무 3건을 넣어 두어,
화면이 각 상태(문서 완성 / 질문에 답할 차례 / 자료 올리기 전)를 바로 그려 볼 수 있게 한다.
같은 팀에 팀원 2명(seoyeon.lee, junho.park · 비밀번호 demo1234)을 더 넣어 관리자 페이지의 팀 인수인계 현황을 확인할 수 있다.
이서연이 완성한 '모바일 웹 운영' 인수인계서를 김민수에게 보내 두어 '인수받은 내용'도 바로 확인할 수 있다.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from ..services.department_notes import department_notes_for
from ..models import ConversationRead, FileBlob, HandoverSession, HandoverTransfer, Message, Source, UnitTask, User, UserProfile
from ..security import hash_password
from . import scenario

if TYPE_CHECKING:
    from ..services.container import Services

DEMO_LOGIN, DEMO_PASSWORD = "demo", "demo1234"
ADMIN_LOGIN, ADMIN_PASSWORD = "admin", "admin1234"  # 관리자 페이지 확인용
DEMO_FILES = ["업무정의서_웹서비스팀.pdf", "홈페이지_운영매뉴얼.hwpx", "주간회의록_2026-09-22.docx", "시스템_계정목록.xlsx", "재무팀_유지보수대금_요청.eml", "웹접근성_개선계획.pptx"]


# 같은 팀(디지털전략부 웹서비스팀) 팀원과 단위업무. 관리자 페이지의 팀 인수인계 현황에서 진행 차이가 보이도록 단계를 섞었다.
# goal: document(문서 완성) · question(첫 질문에 답할 차례) · partial(답 1건·건너뛰기 1건 뒤 다음 질문) · setup(자료 올리기 전)
TEAM = [
    (
        {"login_id": DEMO_LOGIN, "name": "김민수", "email": "minsu.kim@amigo.example", "position": "과장"},
        [
            ("기관 홈페이지 운영", "홈페이지 콘텐츠 게시·승인, 메뉴 관리, 유지보수 업체 관리", DEMO_FILES, "document"),
            ("웹 접근성 관리", "분기별 자체 점검, 연 1회 품질인증 갱신", DEMO_FILES[1:4], "question"),
            ("유지보수 계약 관리", "(주)한빛웹솔루션 유지보수 계약·검수·대금 지급", [], "setup"),
        ],
    ),
    (
        {"login_id": "seoyeon.lee", "name": "이서연", "email": "seoyeon.lee@amigo.example", "position": "대리"},
        [
            ("모바일 웹 운영", "모바일 화면 콘텐츠 반영과 앱 연계 점검", DEMO_FILES[:3], "document"),
            ("SNS 채널 관리", "기관 SNS 게시 일정과 댓글 대응", DEMO_FILES[2:4], "partial"),
        ],
    ),
    (
        {"login_id": "junho.park", "name": "박준호", "email": "junho.park@amigo.example", "position": "주임"},
        [
            ("웹 서버 점검", "월간 서버 자원·로그 점검과 백업 확인", DEMO_FILES[3:5], "question"),
            ("홈페이지 통계 보고", "월간 방문 통계 집계와 보고", [], "setup"),
        ],
    ),
]
TEAM_PASSWORD = DEMO_PASSWORD  # 팀원 계정도 같은 비밀번호(가상 응답 모드 전용)


def seed_demo(services: Services) -> None:
    from ..routers.sessions import new_handover_session
    from ..services.runner import EventRecorder

    agent = services.agent
    agent_delay, agent.delay = agent.delay, 0  # 시드는 기다리지 않고 바로 만든다
    try:
        work: list[tuple[str, dict, str, int]] = []  # (세션 ID, 인계자 정보, 목표 단계, 사용자 ID)
        with services.db() as db:
            admin = User(login_id=ADMIN_LOGIN, password_hash=hash_password(ADMIN_PASSWORD), name="관리자", email="admin@amigo.example", is_admin=True)
            admin.profile = UserProfile(department="디지털전략부", team="IT운영팀", position="차장")
            users = []
            for spec, plans in TEAM:
                user = User(login_id=spec["login_id"], password_hash=hash_password(TEAM_PASSWORD), name=spec["name"], email=spec["email"])
                user.profile = UserProfile(department="디지털전략부", team="웹서비스팀", position=spec["position"])
                db.add(user)
                users.append((user, plans))
            db.add(admin)
            db.flush()
            for user, plans in users:
                for order, (name, description, files, goal) in enumerate(plans, start=1):
                    row = new_handover_session(
                        db, user_id=user.owner_key, owner_name=user.name, organization="디지털전략부 웹서비스팀", position=user.profile.position,
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
                    db.flush()
                    work.append((row.id, row.profile(), goal, user.id))
            db.commit()

        for session_id, profile, goal, user_id in work:
            if goal == "setup":
                continue
            record = EventRecorder(services, session_id)
            kb = services.kb(session_id)
            notes = department_notes_for(profile.get("organization", ""))  # 실제 분석 시작(runner.start_analysis)처럼 부·팀 인수인계 목록을 넘긴다
            if notes:
                profile = {**profile, "dept_notes": notes}
            agent.start(session_id, profile, kb, on_event=record)
            if goal == "document":
                for answer in scenario.DEMO_ANSWERS:
                    _user(services, session_id, answer)
                    agent.send_message(session_id, answer, kb, on_event=record)
                    _user(services, session_id, "네, 맞아요")
                    agent.send_message(session_id, "네, 맞아요", kb, on_event=record)
                _user(services, session_id, "지금까지 내용으로 인수인계서를 만들어 주세요.", kind="command")
                agent.finish(session_id, kb, on_event=record)
            elif goal == "partial":
                answer = scenario.DEMO_ANSWERS[0]
                _user(services, session_id, answer)
                agent.send_message(session_id, answer, kb, on_event=record)
                _user(services, session_id, "네, 맞아요")
                agent.send_message(session_id, "네, 맞아요", kb, on_event=record)
                _user(services, session_id, "이 질문은 건너뛸게요.", kind="skip")
                agent.skip(session_id, kb, on_event=record)
            services.runner._sync_snapshot(session_id)
            if goal == "document":  # 문서까지 본 대화는 모두 읽은 것으로
                with services.db() as db:
                    last = db.scalar(select(func.max(Message.id)).where(Message.session_id == session_id))
                    db.add(ConversationRead(session_id=session_id, user_id=user_id, last_read_message_id=last))
                    db.commit()
        _seed_transfer(services)
    finally:
        agent.delay = agent_delay


def _seed_transfer(services: Services) -> None:
    """이서연이 완성한 '모바일 웹 운영' 인수인계서를 김민수(demo)에게 보내 둔다(인수받은 내용 화면 확인용)."""
    with services.db() as db:
        sender = db.scalar(select(User).where(User.login_id == "seoyeon.lee"))
        recipient = db.scalar(select(User).where(User.login_id == DEMO_LOGIN))
        row = db.scalar(select(HandoverSession).where(HandoverSession.user_id == sender.owner_key, HandoverSession.task_name == "모바일 웹 운영"))
        if row is None or not row.document_md:
            return
        db.add(
            HandoverTransfer(
                id=uuid.uuid4().hex, session_id=row.id, sender_id=sender.id, recipient_id=recipient.id, title=row.title, task_name=row.task_name,
                mode=row.mode, sender_name=row.owner_name, sender_organization=row.organization, sender_position=row.position,
                handover_date=row.handover_date, document_version=row.document_version, markdown=row.document_md,
                message="모바일 웹 운영 업무 인수인계서입니다. 앱 연계 점검 일정은 3장 정기업무를 꼭 확인해 주세요.",
            )
        )
        row.successor = recipient.name
        db.commit()


def _user(services: Services, session_id: str, text: str, kind: str = "answer") -> None:
    with services.db() as db:
        db.add(Message(session_id=session_id, role="user", kind=kind, content=text, meta_json=json.dumps({})))
        db.commit()
