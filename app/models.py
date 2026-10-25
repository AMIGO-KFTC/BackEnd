"""DB 모델: 사용자(관리자 권한) · 프로필 · 팀 인수인계서 지침 · 팀 담당 업무 · 로그인 세션 · 비밀번호 재설정 · 단위업무 · 인수인계 세션 · 등록 자료(파일 원본) · 대화 메시지 · 대화 진행 상태 · 인수인계서 버전 · 인수인계서 전송 · 캘린더 일정·할 일."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    """로그인 계정. 비밀번호는 해시만 저장한다(app/security.py)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    login_id: Mapped[str] = mapped_column(String(50), unique=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(50))
    email: Mapped[str] = mapped_column(String(254), default="", index=True)  # 비밀번호 찾기 본인 확인·안내 메일 주소(소문자)
    employee_no: Mapped[str] = mapped_column(String(20), default="", index=True)  # 사번. 로그인 아이디(login_id)와는 별개 값이고 프로필에서 고친다
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)  # 관리자 권한(관리자 페이지 · /api/admin)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(default=None)
    password_changed_at: Mapped[datetime | None] = mapped_column(default=None)

    profile: Mapped[UserProfile | None] = relationship(back_populates="user", cascade="all, delete-orphan", uselist=False)

    @property
    def owner_key(self) -> str:
        """인수인계 세션(sessions.user_id)에 기록하는 소유자 값. 브라우저가 보내는 X-User-Id 와 겹치지 않는 접두어를 쓴다."""
        return f"user:{self.id}"


class UserProfile(Base):
    """인계자 프로필(부서·팀·직위). 이름은 users.name 에 둔다."""

    __tablename__ = "user_profiles"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    department: Mapped[str] = mapped_column(String(100))
    team: Mapped[str] = mapped_column(String(100), default="")
    position: Mapped[str] = mapped_column(String(50))
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship(back_populates="profile")

    @property
    def organization(self) -> str:
        return " ".join(x for x in (self.department, self.team) if x)


class TeamGuideline(Base):
    """팀 인수인계서 지침. 소속(프로필의 부서 + 팀) 하나에 한 행. 관리자가 적은 항목과 팀 특징·작성 지침을 AI 프롬프트에 반영하는 데 쓴다."""

    __tablename__ = "team_guidelines"

    organization: Mapped[str] = mapped_column(String(100), primary_key=True)
    items_json: Mapped[str] = mapped_column(Text, default="[]")  # [{id, name, instruction}]
    notes: Mapped[str] = mapped_column(Text, default="")
    prompt: Mapped[str] = mapped_column(Text, default="")  # 팀 전용 프롬프트(AI 가 항상 따라야 하는 역할과 판단 기준)
    updated_by: Mapped[str] = mapped_column(String(50), default="")
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class TeamDuty(Base):
    """팀 담당 업무 목록. 소속(프로필의 부서 + 팀)마다 팀장이 관리자 화면에서 정해 두고, 구성원은 인수인계 1단계에서 이 목록에서 담당 업무를 고른다."""

    __tablename__ = "team_duties"
    __table_args__ = (UniqueConstraint("organization", "name", name="uq_team_duties_org_name"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    organization: Mapped[str] = mapped_column(String(100), index=True)  # organization_key 로 맞춘 소속
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    updated_by: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class AuthSession(Base):
    """로그인 세션. 토큰 원문은 쿠키/응답으로만 내보내고 DB 에는 SHA-256 만 저장한다."""

    __tablename__ = "auth_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime] = mapped_column()

    user: Mapped[User] = relationship()


class PasswordReset(Base):
    """비밀번호 재설정 토큰. 토큰 원문은 안내 메일로만 보내고 DB 에는 SHA-256 만 저장한다. 한 번 쓰면 used_at 이 찍힌다."""

    __tablename__ = "password_resets"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime] = mapped_column()
    used_at: Mapped[datetime | None] = mapped_column(default=None)
    requested_by: Mapped[str] = mapped_column(String(10), default="self")  # self(비밀번호 찾기) | admin(관리자가 발급)

    user: Mapped[User] = relationship()


class UnitTask(Base):
    """단위업무. 단위업무마다 인수인계 세션(sessions)을 하나씩 두어 자료·지식베이스·질의응답·인수인계서를 따로 관리한다."""

    __tablename__ = "unit_tasks"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_unit_tasks_user_name"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    session: Mapped[HandoverSession] = relationship()


class HandoverSession(Base):
    """인수인계 세션 1건 = 인계자 1명의 인수인계서 작성 과정. 대화가 어디까지 진행됐는지 저장한다."""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    title: Mapped[str] = mapped_column(String(200))
    owner_name: Mapped[str] = mapped_column(String(50))
    organization: Mapped[str] = mapped_column(String(100), default="")
    position: Mapped[str] = mapped_column(String(50), default="")
    duties: Mapped[str] = mapped_column(Text, default="")
    successor: Mapped[str] = mapped_column(String(100), default="")
    successor_login_id: Mapped[str] = mapped_column(String(50), default="")  # 인수자가 이 시스템 계정이면 그 아이디(받은 인수인계 목록에 쓴다)
    handover_date: Mapped[str] = mapped_column(String(20), default="")
    leave_start: Mapped[str] = mapped_column(String(10), default="")  # 장기휴가 시작일(YYYY-MM-DD)
    leave_end: Mapped[str] = mapped_column(String(10), default="")  # 장기휴가 종료일(YYYY-MM-DD)
    mode: Mapped[str] = mapped_column(String(20), default="reassignment")  # reassignment(인사발령) | leave(장기휴가)
    task_name: Mapped[str] = mapped_column(String(200), default="")  # 단위 업무명

    # 진행 상태: stage 는 에이전트 단계(setup → analyzing → summary → qna → composing → review)
    stage: Mapped[str] = mapped_column(String(20), default="setup")
    # status: idle(대기) | running(AI 처리 중) | waiting(사용자 입력 대기) | error
    status: Mapped[str] = mapped_column(String(20), default="idle")
    progress_message: Mapped[str] = mapped_column(String(300), default="")
    progress_current: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    engine: Mapped[str] = mapped_column(String(20), default="")

    slots_json: Mapped[str] = mapped_column(Text, default="{}")
    gaps_json: Mapped[str] = mapped_column(Text, default="[]")
    question_count: Mapped[int] = mapped_column(Integer, default=0)
    pending_files_json: Mapped[str] = mapped_column(Text, default="[]")
    document_md: Mapped[str] = mapped_column(Text, default="")
    document_version: Mapped[int] = mapped_column(Integer, default=0)
    # AI 가 마지막으로 내놓은 문서. 같은 초안을 버전 기록에 두 번 쌓지 않도록 비교하는 데 쓴다(현재 문서는 직접 고친 문서일 수 있음)
    ai_document_md: Mapped[str] = mapped_column(Text, default="")
    # Claude API 사용량(이 세션에서 쓴 만큼 누적). 비용은 모델 단가로 계산한 추정치(USD)
    llm_requests: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)

    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    sources: Mapped[list[Source]] = relationship(back_populates="session", cascade="all, delete-orphan", order_by="Source.created_at")
    messages: Mapped[list[Message]] = relationship(back_populates="session", cascade="all, delete-orphan", order_by="Message.id")
    document_versions: Mapped[list[DocumentVersion]] = relationship(
        back_populates="session", cascade="all, delete-orphan", passive_deletes=True, order_by="DocumentVersion.version"
    )

    def profile(self) -> dict[str, Any]:
        return {
            "name": self.owner_name,
            "organization": self.organization,
            "position": self.position,
            "duties": self.duties,
            "successor": self.successor,
            "handover_date": self.handover_date,
            "leave_start": self.leave_start,
            "leave_end": self.leave_end,
            "mode": self.mode or "reassignment",
            "task_name": self.task_name,
        }

    @property
    def slots(self) -> dict[str, Any]:
        return json.loads(self.slots_json or "{}")

    @property
    def gaps(self) -> list[dict[str, Any]]:
        return json.loads(self.gaps_json or "[]")


class Source(Base):
    """등록 자료(업로드 파일 또는 링크). id 는 RAG 지식베이스의 source_id 로도 쓰인다."""

    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(10))  # file | link
    name: Mapped[str] = mapped_column(String(300))
    stored_path: Mapped[str] = mapped_column(String(500), default="")
    url: Mapped[str] = mapped_column(Text, default="")
    link_type: Mapped[str] = mapped_column(String(20), default="")
    extension: Mapped[str] = mapped_column(String(20), default="")
    size: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | processing | ready | failed
    error: Mapped[str] = mapped_column(Text, default="")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    warnings_json: Mapped[str] = mapped_column(Text, default="[]")
    added_stage: Mapped[str] = mapped_column(String(20), default="setup")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    session: Mapped[HandoverSession] = relationship(back_populates="sources")

    @property
    def warnings(self) -> list[str]:
        return json.loads(self.warnings_json or "[]")


class Message(Base):
    """대화 기록. id 는 증가하는 정수라 프론트엔드가 '이후 메시지만' 폴링할 때 커서로 쓴다."""

    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_session_id_id", "session_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(20))  # assistant | user | system
    kind: Mapped[str] = mapped_column(String(20), default="info")
    content: Mapped[str] = mapped_column(Text)
    meta_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    session: Mapped[HandoverSession] = relationship(back_populates="messages")

    @property
    def meta(self) -> dict[str, Any]:
        return json.loads(self.meta_json or "{}")


class DocumentVersion(Base):
    """인수인계서 버전 기록. AI 가 작성한 초안과 사용자가 화면에서 직접 고친 문서를 모두 쌓아 둔다.

    version 은 세션 안에서 1부터 늘어나는 BackEnd 기준 번호다(AI 초안·직접 수정·복원이 한 줄로 이어짐).
    sessions.document_md / document_version 은 항상 마지막 버전(현재 문서)을 가리킨다.
    """

    __tablename__ = "document_versions"
    __table_args__ = (UniqueConstraint("session_id", "version", name="uq_document_versions_session_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    origin: Mapped[str] = mapped_column(String(10))  # ai(AI 작성) | user(직접 수정) | restore(이전 버전 복원)
    ai_version: Mapped[int] = mapped_column(Integer, default=0)  # AI 가 매긴 초안 번호(origin=ai 일 때)
    based_on: Mapped[int] = mapped_column(Integer, default=0)  # 직접 수정·복원의 기준이 된 버전
    markdown: Mapped[str] = mapped_column(Text)
    note: Mapped[str] = mapped_column(String(300), default="")
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), default=None)
    author_name: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    session: Mapped[HandoverSession] = relationship(back_populates="document_versions")


class HandoverTransfer(Base):
    """인수인계서 전송: 인계자가 인수자(회원)에게 보낸 산출물.

    인수자는 인계자의 **최신 인수인계서**(직접 수정한 버전 포함)를 본다. 보낸 시점의 문서(markdown·버전)도 복사해 두어
    무엇을 보냈는지 남기고, 인계 세션이 지워져도(session_id → NULL) 보낸 시점 문서로 볼 수 있게 한다.
    """

    __tablename__ = "handover_transfers"
    __table_args__ = (Index("ix_handover_transfers_recipient", "recipient_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    session_id: Mapped[str | None] = mapped_column(ForeignKey("sessions.id", ondelete="SET NULL"), index=True, default=None)
    sender_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    recipient_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200))
    task_name: Mapped[str] = mapped_column(String(200), default="")
    mode: Mapped[str] = mapped_column(String(20), default="reassignment")
    sender_name: Mapped[str] = mapped_column(String(50))
    sender_organization: Mapped[str] = mapped_column(String(100), default="")
    sender_position: Mapped[str] = mapped_column(String(50), default="")
    handover_date: Mapped[str] = mapped_column(String(20), default="")
    document_version: Mapped[int] = mapped_column(Integer, default=0)
    markdown: Mapped[str] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text, default="")  # 인계자가 남긴 말
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    read_at: Mapped[datetime | None] = mapped_column(default=None)  # 인수자가 처음 열어 본 시각

    sender: Mapped[User] = relationship(foreign_keys=[sender_id])
    recipient: Mapped[User] = relationship(foreign_keys=[recipient_id])
    session: Mapped[HandoverSession | None] = relationship()


class PlannerEvent(Base):
    """캘린더 일정(계정별). 홈 대시보드의 작은 캘린더와 '캘린더' 탭이 같이 쓴다.

    key 는 화면에 내보내는 id 다. 화면이 만들어 보내면 그대로 쓰므로(계정 안에서만 유일) 같은 요청을 다시 보내도 두 번 쌓이지 않는다.
    """

    __tablename__ = "planner_events"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_planner_events_user_key"), Index("ix_planner_events_user_date", "user_id", "date"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)  # 내부 번호(추가한 순서)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(40))
    date: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD
    text: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class PlannerTodo(Base):
    """할 일(투두, 계정별). date 가 비어 있으면 날짜 없는 할 일이다. key 는 PlannerEvent 와 같은 규칙."""

    __tablename__ = "planner_todos"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_planner_todos_user_key"), Index("ix_planner_todos_user_id", "user_id"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(40))
    text: Mapped[str] = mapped_column(String(200))
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    date: Mapped[str | None] = mapped_column(String(10), default=None)  # YYYY-MM-DD, None 이면 날짜 없음
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)




class PlannerWeekTask(Base):
    """주간 업무(계정별): 캘린더 화면에서 주(달력 한 줄)를 골라 적는 그 주에만 할 일. week 는 그 주 일요일 날짜다. key 는 PlannerEvent 와 같은 규칙."""

    __tablename__ = "planner_week_tasks"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_planner_week_tasks_user_key"), Index("ix_planner_week_tasks_user_week", "user_id", "week"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(40))
    week: Mapped[str] = mapped_column(String(10))  # 그 주 일요일 YYYY-MM-DD
    text: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class PlannerRoutine(Base):
    """정기 업무(계정별): 받은 인수인계서에서 가져온 매일·매주·매월·분기·반기·매년·수시로 하는 일.

    날짜로 풀지 않고 캘린더 화면의 '정기 업무' 칸에 주기(cadence)별로 보여 준다. key 는 PlannerEvent 와 같은 규칙.
    """

    __tablename__ = "planner_routines"
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_planner_routines_user_key"), Index("ix_planner_routines_user_id", "user_id"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(40))
    cadence: Mapped[str] = mapped_column(String(20))  # daily | weekly | monthly | quarterly | yearly | other
    name: Mapped[str] = mapped_column(String(200))  # 업무명
    timing: Mapped[str] = mapped_column(String(200), default="")  # 인수인계서에 적힌 수행 시기 그대로
    months: Mapped[str] = mapped_column(String(40), default="")  # 분기·반기·매년 업무의 해당 월("3,6,9,12"), 비면 매달 또는 모름
    source: Mapped[str] = mapped_column(String(200), default="")  # 가져온 인수인계서(단위업무명)
    session_id: Mapped[str | None] = mapped_column(String(32), default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

class FileBlob(Base):
    """업로드 파일 원본(바이트). 목록 조회 때 큰 데이터를 읽지 않도록 sources 와 분리했다."""

    __tablename__ = "file_blobs"

    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True)
    content_type: Mapped[str] = mapped_column(String(200), default="application/octet-stream")
    sha256: Mapped[str] = mapped_column(String(64))
    content: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class ConversationRead(Base):
    """사용자가 대화를 어디까지 읽었는지(읽음 위치). 다시 접속했을 때 그 사이 AI 가 보낸 새 메시지를 구분한다."""

    __tablename__ = "conversation_reads"

    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    last_read_message_id: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class AgentCheckpoint(Base):
    """AI 에이전트(LangGraph) 체크포인트: 대화가 어느 단계·어느 질문에서 멈춰 있는지. thread_id = 세션 ID.

    langgraph-checkpoint-sqlite 의 checkpoints 테이블과 같은 구조라 예전 SQLite 파일을 그대로 옮겨 올 수 있다.
    """

    __tablename__ = "agent_checkpoints"

    thread_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(String(), primary_key=True, default="")
    checkpoint_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    parent_checkpoint_id: Mapped[str | None] = mapped_column(String(64), default=None)
    type: Mapped[str | None] = mapped_column(String(50), default=None)
    checkpoint: Mapped[bytes] = mapped_column(LargeBinary)
    meta_json: Mapped[str] = mapped_column("metadata", Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class AgentCheckpointWrite(Base):
    """체크포인트에 딸린 중간 기록(병렬 노드 결과, 사용자 입력 대기 등)."""

    __tablename__ = "agent_checkpoint_writes"

    thread_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(String(), primary_key=True, default="")
    checkpoint_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(), primary_key=True)
    idx: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    channel: Mapped[str] = mapped_column(String())
    type: Mapped[str | None] = mapped_column(String(50), default=None)
    value: Mapped[bytes | None] = mapped_column(LargeBinary, default=None)


class PersonnelSchedule(Base):
    """정기인사 일정(팀장이 등록). 1년에 여러 번 있을 수 있다. 등록한 팀장의 소속('부서 팀') 단위(organization 이 빈 값인 예전 전사 일정은 더 보이지 않는다)."""

    __tablename__ = "personnel_schedules"
    __table_args__ = (Index("ix_personnel_schedules_org_date", "organization", "appoint_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100))  # 예: 2027 상반기 정기인사
    announce_date: Mapped[str] = mapped_column(String(10), default="")  # 발표일 YYYY-MM-DD(비워도 됨)
    appoint_date: Mapped[str] = mapped_column(String(10))  # 발령일 YYYY-MM-DD
    organization: Mapped[str] = mapped_column(String(100), default="")  # 소속 '부서 팀'
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), default=None)
    created_by_name: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class TeamSchedule(Base):
    """팀 공유 일정(팀장이 등록, 워크숍·회의·마감 등). 같은 소속('부서 팀') 구성원의 홈·캘린더에 함께 나온다."""

    __tablename__ = "team_schedules"
    __table_args__ = (Index("ix_team_schedules_org_date", "organization", "start_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(100))  # 예: 하반기 팀 워크숍
    start_date: Mapped[str] = mapped_column(String(10))  # 시작일 YYYY-MM-DD
    end_date: Mapped[str] = mapped_column(String(10), default="")  # 끝나는 날 YYYY-MM-DD(하루짜리면 빈 값)
    memo: Mapped[str] = mapped_column(String(300), default="")
    organization: Mapped[str] = mapped_column(String(100), default="")  # 소속 '부서 팀'
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), default=None)
    created_by_name: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
