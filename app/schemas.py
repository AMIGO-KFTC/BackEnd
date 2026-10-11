"""API 요청/응답 스키마."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SessionCreate(BaseModel):
    owner_name: str = Field(min_length=1, max_length=50, description="인계자 성명")
    organization: str = Field(default="", max_length=100, description="소속 조직")
    position: str = Field(default="", max_length=50, description="직책")
    duties: str = Field(default="", max_length=2000, description="담당 업무 개요")
    successor: str = Field(default="", max_length=100, description="인수자")
    handover_date: str = Field(default="", max_length=20, description="인계 예정일(YYYY-MM-DD)")
    title: str = Field(default="", max_length=200)
    mode: Literal["reassignment", "leave"] = Field(default="reassignment", description="인수인계 유형: reassignment=인사발령, leave=장기휴가(Light)")
    task_name: str = Field(default="", max_length=200, description="단위 업무명(업무 단위별로 인수인계서 1건)")

    @field_validator("owner_name", "organization", "position", "duties", "successor", "handover_date", "title", "task_name")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    name: str
    url: str
    link_type: str
    extension: str
    size: int
    status: str
    error: str
    chunk_count: int
    warnings: list[str]
    added_stage: str
    created_at: datetime


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    kind: str
    content: str
    meta: dict[str, Any]
    created_at: datetime


class Progress(BaseModel):
    message: str
    current: int
    total: int


class SessionSummary(BaseModel):
    id: str
    title: str
    owner_name: str
    organization: str
    position: str
    mode: str = "reassignment"
    task_name: str = ""
    stage: str
    status: str
    document_version: int
    created_at: datetime
    updated_at: datetime


class Usage(BaseModel):
    """Claude API 사용량. cost_usd 는 모델 단가로 계산한 추정치(실제 청구액은 Anthropic Console 에서 확인)."""

    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0


class UsageTotal(Usage):
    """서버 전체(모든 세션) 누적 사용량과 예산."""

    sessions: int = 0
    budget_usd: float = 0.0  # 0 이면 제한 없음
    remaining_usd: float | None = None


class SessionDetail(SessionSummary):
    duties: str
    successor: str
    handover_date: str
    progress: Progress
    error: str
    engine: str
    slots: dict[str, Any]
    gaps: list[dict[str, Any]]
    open_gap_count: int
    question_count: int
    sources: list[SourceOut]
    last_message_id: int
    usage: Usage


class StateOut(BaseModel):
    """폴링용 응답: 세션 상태 + 커서 이후의 새 메시지."""

    session: SessionDetail
    messages: list[MessageOut]
    usage_total: UsageTotal


class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class LinksIn(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=20)
    link_type: Literal["auto", "confluence", "nanumi", "web"] = "auto"


class DocumentOut(BaseModel):
    markdown: str
    version: int


class Accepted(BaseModel):
    ok: bool = True
    message: MessageOut | None = None
