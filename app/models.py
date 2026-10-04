"""DB 모델: 인수인계 세션 · 등록 자료 · 대화 메시지."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


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
