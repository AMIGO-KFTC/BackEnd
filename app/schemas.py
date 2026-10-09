"""API 요청/응답 스키마."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

LOGIN_ID_PATTERN = r"^[A-Za-z0-9._@-]{3,50}$"


class _Stripped(BaseModel):
    """문자열 필드 앞뒤 공백 제거(비밀번호는 입력 그대로 비교하므로 제외)."""

    @field_validator("*", mode="before")
    @classmethod
    def _strip(cls, value, info: ValidationInfo):
        return value.strip() if isinstance(value, str) and info.field_name != "password" else value


# ---------------------------------------------------------------- 로그인 · 프로필 · 단위업무


class Result(BaseModel):
    """성공/실패를 화면에 바로 띄울 수 있는 공통 응답. 실패(4xx)도 같은 모양으로 온다(success=false, message, detail)."""

    success: bool = True
    message: str = ""


class SignupIn(_Stripped):
    login_id: str = Field(pattern=LOGIN_ID_PATTERN, description="아이디(영문·숫자·._@- 3~50자)")
    password: str = Field(min_length=8, max_length=128, description="비밀번호(8자 이상)")
    name: str = Field(min_length=1, max_length=50, description="이름")


class LoginIn(_Stripped):
    login_id: str = Field(min_length=1, max_length=50, description="아이디")
    password: str = Field(min_length=1, max_length=128, description="비밀번호")


class ProfileIn(_Stripped):
    department: str = Field(min_length=1, max_length=100, description="부서")
    position: str = Field(min_length=1, max_length=50, description="직위")
    team: str = Field(default="", max_length=100, description="팀")
    name: str = Field(min_length=1, max_length=50, description="이름")


class ProfileOut(BaseModel):
    department: str
    position: str
    team: str
    name: str


class UserOut(BaseModel):
    id: int
    login_id: str
    name: str
    profile: ProfileOut | None


class LoginOut(Result):
    name: str
    user: UserOut
    token: str = Field(description="로그인 세션 토큰. 쿠키(amigo_session)로도 내려가며, 쿠키를 못 쓰는 클라이언트는 Authorization: Bearer 로 보낸다")
    expires_at: datetime


class MeOut(Result):
    user: UserOut


class ProfileResult(Result):
    profile: ProfileOut


class UnitTaskIn(_Stripped):
    name: str = Field(min_length=1, max_length=100, description="단위업무명")
    description: str = Field(default="", max_length=2000, description="업무 설명(선택)")


class UnitTasksIn(BaseModel):
    tasks: list[UnitTaskIn] = Field(min_length=1, max_length=30, description="등록할 단위업무 목록")


class UnitTaskUpdate(_Stripped):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)


class UnitTaskOut(BaseModel):
    id: str
    name: str
    description: str
    sort_order: int
    session_id: str = Field(description="이 단위업무의 인수인계 세션 ID. 질의응답·문서는 /api/sessions/{session_id}/... 를 그대로 쓴다")
    stage: str
    status: str
    file_count: int
    document_version: int
    created_at: datetime


class UnitTaskResult(Result):
    task: UnitTaskOut


class UnitTasksResult(Result):
    tasks: list[UnitTaskOut]




class SessionCreate(BaseModel):
    owner_name: str = Field(min_length=1, max_length=50, description="인계자 성명")
    organization: str = Field(default="", max_length=100, description="소속 조직")
    position: str = Field(default="", max_length=50, description="직책")
    duties: str = Field(default="", max_length=2000, description="담당 업무 개요")
    successor: str = Field(default="", max_length=100, description="인수자")
    handover_date: str = Field(default="", max_length=20, description="인계 예정일(YYYY-MM-DD)")
    title: str = Field(default="", max_length=200)

    @field_validator("owner_name", "organization", "position", "duties", "successor", "handover_date", "title")
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
    stage: str
    status: str
    document_version: int
    created_at: datetime
    updated_at: datetime


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


class StateOut(BaseModel):
    """폴링용 응답: 세션 상태 + 커서 이후의 새 메시지."""

    session: SessionDetail
    messages: list[MessageOut]


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


class FileUploadResult(Result):
    task_id: str
    session_id: str
    files: list[SourceOut]
    analysis_scheduled: bool = Field(description="적재가 끝나면 AI 분석(또는 재분석)이 자동으로 시작되는지")
