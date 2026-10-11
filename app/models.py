"""DB 모델: 사용자 · 프로필 · 로그인 세션 · 단위업무 · 인수인계 세션 · 등록 자료(파일 원본) · 대화 메시지 · 대화 진행 상태."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Float, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint
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
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(default=None)

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


class AuthSession(Base):
    """로그인 세션. 토큰 원문은 쿠키/응답으로만 내보내고 DB 에는 SHA-256 만 저장한다."""

    __tablename__ = "auth_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime] = mapped_column()

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
    handover_date: Mapped[str] = mapped_column(String(20), default="")

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

    def profile(self) -> dict[str, Any]:
        return {
            "name": self.owner_name,
            "organization": self.organization,
            "position": self.position,
            "duties": self.duties,
            "successor": self.successor,
            "handover_date": self.handover_date,
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
