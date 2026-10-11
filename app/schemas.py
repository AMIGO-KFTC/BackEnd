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


class FileUploadResult(Result):
    task_id: str
    session_id: str
    files: list[SourceOut]
    analysis_scheduled: bool = Field(description="적재가 끝나면 AI 분석(또는 재분석)이 자동으로 시작되는지")


# ---------------------------------------------------------------- 대화 진행 상태 · 기록


class CurrentQuestion(BaseModel):
    message_id: int
    kind: str = Field(description="question(질문) | confirm(정리한 답변 확인)")
    content: str
    gap_id: str = ""
    slot: str = ""
    number: int = 0
    quick_replies: list[str] = []
    asked_at: datetime


class QuestionStats(BaseModel):
    asked: int = Field(description="지금까지 던진 질문 수")
    answered: int = Field(description="답변을 받아 정리한 빈 항목 수(확인 대기 포함)")
    skipped: int
    remaining: int = Field(description="아직 묻지 않은 빈 항목 수")
    total: int = Field(description="분석에서 찾은 빈 항목 수")


class ConversationProgress(BaseModel):
    """사용자가 이 대화를 어디까지 진행했는지."""

    session_id: str
    task_id: str | None = Field(description="단위업무 ID(단위업무로 만든 대화일 때)")
    title: str
    stage: str = Field(description="setup → analyzing → summary → qna → composing → review")
    stage_number: int = Field(description="화면의 STAGE 번호(0=자료 준비, 1~4)")
    stage_label: str
    status: str = Field(description="idle | running(AI 처리 중) | waiting(사용자 입력 대기) | error")
    next_action: str = Field(
        description="upload_files | processing_files | start_analysis | ai_working | answer_question | confirm_answer | review_document | retry"
    )
    next_action_label: str = Field(description="화면에 그대로 띄울 다음 할 일 안내")
    progress_message: str
    current_question: CurrentQuestion | None
    questions: QuestionStats
    file_count: int
    document_version: int
    message_count: int
    last_message: MessageOut | None
    last_read_message_id: int
    unread_count: int = Field(description="마지막으로 읽은 뒤 AI·시스템이 보낸 메시지 수")
    started_at: datetime
    last_activity_at: datetime


class ConversationsResult(Result):
    conversations: list[ConversationProgress]


class MessagesPage(BaseModel):
    messages: list[MessageOut] = Field(description="오래된 것부터 순서대로")
    has_more_before: bool = Field(description="이보다 오래된 메시지가 더 있는지(before=messages[0].id 로 이어서 조회)")


class ConversationResult(Result):
    progress: ConversationProgress
    history: MessagesPage


class ReadIn(BaseModel):
    message_id: int = Field(ge=0, description="여기까지 읽음(이 ID 이하의 메시지)")
