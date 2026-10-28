"""API 요청/응답 스키마."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

LOGIN_ID_PATTERN = r"^[A-Za-z0-9._@-]{3,50}$"
PASSWORD_RULE = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)(?=.*[^A-Za-z\d]).{9,20}$")  # 회원가입 비밀번호: 9~20자, 영문·숫자·특수문자 모두 포함
EMAIL_PATTERN = r"^([^@\s]{1,64}@[^@\s]+\.[^@\s]{2,})?$"  # 비우거나(선택) 형식만 확인
EMPLOYEE_NO_PATTERN = r"^[A-Za-z0-9]{0,20}$"  # 사번: 비우거나 영문·숫자 20자 이하


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
    password: str = Field(max_length=128, description="비밀번호(9~20자, 영문·숫자·특수문자 모두 포함)")
    name: str = Field(min_length=1, max_length=50, description="이름")
    email: str = Field(default="", max_length=254, pattern=EMAIL_PATTERN, description="이메일(선택). 비밀번호 찾기에 쓴다")
    employee_no: str = Field(default="", pattern=EMPLOYEE_NO_PATTERN, description="사번(선택). 로그인 아이디와는 별개 값")
    department: str = Field(default="", max_length=100, description="본부·부(선택). 직제규정 시행세칙(GET /api/auth/org-teams)의 본부·부 이름. team 과 함께 보낸다")
    team: str = Field(default="", max_length=100, description="팀(선택). 보내면 가입과 동시에 이 팀으로 배정돼, 내 정보를 따로 고치지 않아도 인수인계에서 팀의 단위업무가 나온다")

    @field_validator("password")
    @classmethod
    def _password_rule(cls, value: str) -> str:
        if not PASSWORD_RULE.fullmatch(value):
            raise ValueError("비밀번호는 9~20자의 영문, 숫자, 특수문자를 모두 포함해야 합니다.")
        return value


class OrgTeamOptionOut(BaseModel):
    name: str = Field(description="본부·부 이름")
    teams: list[str] = Field(description="그 아래 팀 이름")


class OrgTeamsOut(Result):
    divisions: list[OrgTeamOptionOut]


class LoginIn(_Stripped):
    login_id: str = Field(min_length=1, max_length=50, description="아이디")
    password: str = Field(min_length=1, max_length=128, description="비밀번호")


class ProfileIn(_Stripped):
    department: str = Field(min_length=1, max_length=100, description="부서")
    position: str = Field(min_length=1, max_length=50, description="직위")
    team: str = Field(default="", max_length=100, description="팀")
    name: str = Field(min_length=1, max_length=50, description="이름")
    employee_no: str | None = Field(default=None, pattern=EMPLOYEE_NO_PATTERN, description="사번. 보내지 않으면 그대로 두고, 빈 문자열이면 비운다")


class ProfileOut(BaseModel):
    department: str
    position: str
    team: str
    name: str
    employee_no: str = ""


class UserOut(BaseModel):
    id: int
    login_id: str
    name: str
    email: str = ""
    employee_no: str = Field(default="", description="사번(로그인 아이디와 별개)")
    is_admin: bool = Field(default=False, description="관리자 권한. true 면 관리자 페이지(/api/admin)를 쓸 수 있다")
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


# ---------------------------------------------------------------- 마이페이지 · 비밀번호


class MyInfoUpdate(_Stripped):
    """내 정보 수정. 보낸 항목만 바꾼다(빠진 항목은 그대로)."""

    name: str | None = Field(default=None, min_length=1, max_length=50, description="이름")
    department: str | None = Field(default=None, min_length=1, max_length=100, description="부서")
    team: str | None = Field(default=None, max_length=100, description="팀명(비우려면 빈 문자열)")
    position: str | None = Field(default=None, min_length=1, max_length=50, description="직위")
    email: str | None = Field(default=None, max_length=254, pattern=EMAIL_PATTERN, description="이메일(비우려면 빈 문자열)")
    employee_no: str | None = Field(default=None, pattern=EMPLOYEE_NO_PATTERN, description="사번(비우려면 빈 문자열)")


class MyStats(BaseModel):
    unit_tasks: int = Field(description="등록한 단위업무 수")
    documents: int = Field(description="인수인계서가 만들어진 업무 수")
    document_versions: int = Field(description="저장된 인수인계서 버전 수(AI 초안·직접 수정 포함)")
    sent_transfers: int = Field(default=0, description="인수자에게 보낸 인수인계서 수")
    received_transfers: int = Field(default=0, description="인수받은 인수인계서 수")
    received_unread: int = Field(default=0, description="인수받았지만 아직 열어 보지 않은 수")


class MyPageOut(Result):
    user: UserOut
    created_at: datetime
    last_login_at: datetime | None
    password_changed_at: datetime | None
    stats: MyStats


class PasswordChangeIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=128, description="지금 비밀번호")
    new_password: str = Field(min_length=8, max_length=128, description="새 비밀번호(8자 이상)")


class PasswordForgotIn(_Stripped):
    login_id: str = Field(min_length=1, max_length=50, description="아이디")
    email: str = Field(min_length=3, max_length=254, description="가입할 때(또는 내 정보에) 등록한 이메일")


class PasswordForgotOut(Result):
    expires_in_minutes: int
    reset_token: str | None = Field(default=None, description="개발용(AMIGO_EXPOSE_RESET_TOKEN·가상 응답 모드)에서만 온다. 운영에서는 메일로만 전달")


class PasswordResetIn(BaseModel):
    token: str = Field(min_length=10, max_length=200, description="재설정 메일의 토큰")
    new_password: str = Field(min_length=8, max_length=128, description="새 비밀번호(8자 이상)")


# ---------------------------------------------------------------- 관리자


class AdminUserOut(UserOut):
    created_at: datetime
    last_login_at: datetime | None
    unit_task_count: int
    document_count: int


class AdminUsersOut(Result):
    users: list[AdminUserOut]
    total: int


class AdminRoleIn(BaseModel):
    is_admin: bool = Field(description="true 면 관리자 권한을 주고 false 면 뺀다")


class AdminUserResult(Result):
    user: AdminUserOut


class AdminResetOut(Result):
    reset_token: str = Field(description="사용자에게 전달할 재설정 토큰(한 번만 보여 줌)")
    reset_url: str
    expires_at: datetime


HandoverMode = Literal["reassignment", "leave"]
DATE_PATTERN = r"^(\d{4}-\d{2}-\d{2})?$"


class UnitTaskIn(_Stripped):
    name: str = Field(min_length=1, max_length=100, description="단위업무명")
    description: str = Field(default="", max_length=2000, description="업무 설명(선택)")
    mode: HandoverMode = Field(default="reassignment", description="인계 유형: reassignment=인사발령(6개 장), leave=장기휴가(3개 장)")
    successor: str = Field(default="", max_length=100, description="인수자(선택)")
    successor_login_id: str = Field(default="", max_length=50, description="인수자의 로그인 아이디(선택). 적으면 그 계정의 '받은 인수인계'에 나타난다")
    handover_date: str = Field(default="", pattern=DATE_PATTERN, description="인계 예정일 YYYY-MM-DD(선택)")


class UnitTasksIn(BaseModel):
    tasks: list[UnitTaskIn] = Field(min_length=1, max_length=30, description="등록할 단위업무 목록")


class UnitTaskUpdate(_Stripped):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    mode: HandoverMode | None = None
    successor: str | None = Field(default=None, max_length=100)
    successor_login_id: str | None = Field(default=None, max_length=50)
    handover_date: str | None = Field(default=None, pattern=DATE_PATTERN)


class UnitTaskOut(BaseModel):
    id: str
    name: str
    description: str
    sort_order: int
    session_id: str = Field(description="이 단위업무의 인수인계 세션 ID. 질의응답·문서는 /api/sessions/{session_id}/... 를 그대로 쓴다")
    mode: str = Field(description="인계 유형: reassignment(인사발령) | leave(장기휴가)")
    successor: str
    successor_login_id: str = ""
    handover_date: str
    stage: str
    status: str
    file_count: int
    document_version: int
    created_at: datetime


class UnitTaskResult(Result):
    task: UnitTaskOut


class UnitTasksResult(Result):
    tasks: list[UnitTaskOut]




class UnitIn(BaseModel):
    """인계자 정보의 단위업무 1건과 그 세부업무(정기·비정기). 장기휴가는 단위업무 없이 이름을 비운 1건에 세부업무만 담는다."""

    name: str = Field(default="", max_length=200, description="단위업무명(직제규정 시행세칙의 업무분장 또는 직접 추가). 장기휴가는 빈 값")
    regular: list[str] = Field(default_factory=list, max_length=50, description="정기 세부업무")
    irregular: list[str] = Field(default_factory=list, max_length=50, description="비정기(프로젝트성) 세부업무")

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        return value.strip()

    @field_validator("regular", "irregular")
    @classmethod
    def _clean_items(cls, values: list[str]) -> list[str]:
        items = [v.strip() for v in values if v.strip()]
        if any(len(v) > 200 for v in items):
            raise ValueError("세부업무는 200자 이하로 입력해 주세요.")
        return items


class SessionCreate(BaseModel):
    owner_name: str = Field(min_length=1, max_length=50, description="인계자 성명")
    organization: str = Field(default="", max_length=100, description="소속 조직")
    position: str = Field(default="", max_length=50, description="직책")
    duties: str = Field(default="", max_length=8000, description="담당 업무 개요(단위업무별 정기·비정기 세부업무를 풀어 쓴 글)")
    successor: str = Field(default="", max_length=100, description="인수자")
    successor_login_id: str = Field(default="", max_length=50, description="인수자의 로그인 아이디(이 시스템 계정이면). 그 계정의 '받은 인수인계'에 나타난다")
    handover_date: str = Field(default="", max_length=20, description="인계 예정일(YYYY-MM-DD)")
    leave_start: str = Field(default="", pattern=DATE_PATTERN, description="장기휴가 시작일(YYYY-MM-DD). 장기휴가 유형에서 쓴다")
    leave_end: str = Field(default="", pattern=DATE_PATTERN, description="장기휴가 종료일(YYYY-MM-DD). 시작일보다 앞설 수 없다")
    title: str = Field(default="", max_length=200)
    mode: Literal["reassignment", "leave"] = Field(default="reassignment", description="인수인계 유형: reassignment=인사발령, leave=장기휴가")
    task_name: str = Field(default="", max_length=200, description="단위 업무명(업무 단위별로 인수인계서 1건)")
    units: list[UnitIn] = Field(
        default_factory=list,
        max_length=30,
        description="단위업무와 세부업무(정기·비정기). 인사발령은 고른 단위업무마다 1건, 장기휴가는 이름을 비운 1건. 이름도 세부업무도 없는 건은 버린다",
    )

    @field_validator("owner_name", "organization", "position", "duties", "successor", "successor_login_id", "handover_date", "leave_start", "leave_end", "title", "task_name")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @field_validator("units")
    @classmethod
    def _drop_empty_units(cls, units: list[UnitIn]) -> list[UnitIn]:
        return [u for u in units if u.name or u.regular or u.irregular]

    @model_validator(mode="after")
    def _leave_period(self) -> "SessionCreate":
        if self.leave_start and self.leave_end and self.leave_end < self.leave_start:
            raise ValueError("휴가 종료일은 시작일보다 앞설 수 없습니다.")
        return self


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
    successor: str = ""
    successor_login_id: str = ""
    handover_date: str = ""
    leave_start: str = ""
    leave_end: str = ""
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


class UnitOut(BaseModel):
    name: str = ""
    regular: list[str] = Field(default_factory=list)
    irregular: list[str] = Field(default_factory=list)


class SessionDetail(SessionSummary):
    duties: str
    units: list[UnitOut] = Field(default_factory=list, description="인계자 정보에서 고른 단위업무와 세부업무(정기·비정기). 예전 세션은 빈 목록")
    owner_login_id: str = Field(default="", description="인계자(세션을 만든 로그인 계정)의 아이디. 로그인 없이 만든 세션이면 빈 값")
    form: str = Field(default="standard", description="인수인계서 양식: standard(기본 양식) | office(원내 양식, 팀장이 만든 세션)")
    viewer: Literal["owner", "recipient", "team", ""] = Field(
        default="", description="지금 보는 계정과 이 세션의 관계: owner(인계자) | recipient(전송받은 사람) | team(같은 팀 팀장) | 빈 값(로그인 없이 만든 세션)"
    )
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


class ReopenIn(BaseModel):
    gap_id: str = Field(min_length=1, max_length=100, description="다시 답할 질문(gap)의 id. 이미 답했거나 건너뛴 질문만 된다")


class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000, description="인계자 답변(질의응답) 또는 검토 의견(문서 검토 단계)")
    reply_to: int | None = Field(
        default=None,
        ge=1,
        description="답하는 질문의 메시지 ID(화면에 띄운 질문). 주면 그 질문이 아직 답을 기다리는지 확인하고, 그사이 질문이 바뀌었으면 409",
    )


class LinksIn(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=20)
    link_type: Literal["auto", "confluence", "nanumi", "web"] = "auto"


class MattermostImportIn(BaseModel):
    """가상 Mattermost 에서 고른 대화를 인수인계 자료로 등록한다."""

    conversation_ids: list[str] = Field(min_length=1, max_length=20)


class JiraMoveIn(BaseModel):
    status: str = Field(description="할 일 | 진행 중 | 검토 중 | 완료")


class JiraImportIn(BaseModel):
    """가상 Jira 에서 고른 이슈(키) · 에픽 · 프로젝트 진행 현황('project:<키>')을 인수인계 자료로 등록한다."""

    item_ids: list[str] = Field(min_length=1, max_length=30)


class ConfluenceImportIn(BaseModel):
    """가상 Confluence 검색 결과에서 고른 페이지를 인수인계 자료로 등록한다."""

    page_ids: list[str] = Field(min_length=1, max_length=20)


class IntegrationImportResult(BaseModel):
    message: str
    sources: list[SourceOut]  # 이번에 새로 등록한 자료
    skipped: list[str] = Field(default_factory=list)  # 이미 등록돼 있어 건너뛴 대화·페이지 이름


class DocumentOut(BaseModel):
    """현재 인수인계서(마지막 버전). 사용자가 직접 고쳤으면 고친 문서가 온다."""

    markdown: str
    version: int
    origin: str = Field(default="ai", description="ai(AI 작성) | user(직접 수정) | restore(이전 버전 복원)")
    origin_label: str = "AI 작성"
    author_name: str = Field(default="", description="직접 수정·복원한 사람(AI 작성이면 빈 값)")
    updated_at: datetime | None = None
    version_count: int = Field(default=0, description="지금까지 저장된 버전 수")


class DocumentSaveIn(BaseModel):
    markdown: str = Field(min_length=1, max_length=500_000, description="화면에서 고친 인수인계서 전체(Markdown)")
    base_version: int = Field(ge=0, description="수정을 시작할 때 불러온 version. 그사이 새 버전이 생겼으면 409")
    note: str = Field(default="", max_length=300, description="수정 메모(선택)")

    @field_validator("markdown")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("빈 문서는 저장할 수 없습니다.")
        return value


class DocumentRestoreIn(BaseModel):
    base_version: int | None = Field(default=None, ge=0, description="복원을 누를 때 보고 있던 현재 version(주면 그사이 바뀌었는지 검사)")
    note: str = Field(default="", max_length=300)


class DocumentVersionSummary(BaseModel):
    version: int
    origin: str = Field(description="ai | user | restore")
    origin_label: str
    ai_version: int = Field(description="AI 가 매긴 초안 번호(origin=ai 일 때, 아니면 0)")
    based_on: int = Field(description="직접 수정·복원의 기준 버전(origin=ai 면 0)")
    note: str
    author_name: str
    char_count: int
    is_current: bool
    created_at: datetime


class DocumentVersionOut(DocumentVersionSummary):
    markdown: str


class DocumentVersionsOut(Result):
    session_id: str
    current_version: int
    versions: list[DocumentVersionSummary] = Field(description="최신 버전부터")


class DocumentSaved(Result):
    saved: bool = Field(description="새 버전을 만들었는지(내용이 같으면 false)")
    document: DocumentVersionOut


class MyDocument(BaseModel):
    session_id: str
    task_id: str | None = Field(description="단위업무로 만든 문서면 단위업무 ID")
    title: str
    task_name: str
    stage: str
    version: int
    origin: str
    origin_label: str
    version_count: int
    updated_at: datetime


class MyDocumentsOut(Result):
    documents: list[MyDocument]


class GanttEdit(BaseModel):
    name: str = Field(min_length=1, max_length=300, description="월별 일정표의 단위 업무명(첫 열)")
    nth: int = Field(default=0, ge=0, le=100, description="이름이 같은 행이 여럿일 때 몇 번째 행인지(0부터)")
    months: list[int] = Field(max_length=36, description="막대가 칠해진 달. 1~12월 표는 1~12, 해를 넘기는 표는 YYYYMM(예: 202612)")

    @field_validator("months")
    @classmethod
    def _valid_months(cls, value: list[int]) -> list[int]:
        plain = all(1 <= m <= 12 for m in value)
        dated = all(190001 <= m <= 299912 and 1 <= m % 100 <= 12 for m in value)
        if not (plain or dated):
            raise ValueError("월은 1~12(또는 해를 넘기는 표는 YYYYMM) 값이어야 합니다.")
        return sorted(set(value))


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


# ---------------------------------------------------------------- 근거 원문


class ChunkOut(BaseModel):
    chunk_id: str
    index: int = Field(description="자료 안에서의 순서(0부터)")
    text: str
    citation: str = Field(description="사람이 읽는 출처 표기. 예: 업무정의서.pdf p.3")
    page: int | None = None
    sheet: str = ""
    section: str = ""


class ChunksOut(BaseModel):
    source_id: str
    source_name: str
    chunks: list[ChunkOut]
    total: int = Field(description="이 자료의 전체 조각 수")


class CitationOut(BaseModel):
    number: int = Field(description="인수인계서 본문의 [번호]")
    label: str
    quote: str = Field(description="근거로 쓴 문장 일부")
    kind: Literal["document", "answer"] = Field(description="document=올린 자료, answer=인계자 답변")
    source_id: str
    source_name: str = Field(description="올린 자료 이름(답변이거나 자료가 삭제됐으면 빈 값)")
    chunk_id: str
    text: str = Field(description="근거가 나온 조각 원문(찾지 못하면 quote 와 같음)")
    slot: str = Field(description="이 근거가 처음 쓰인 장(slot 키)")
    item_title: str


class CitationsOut(BaseModel):
    document_version: int
    citations: list[CitationOut]


class ReindexResult(Result):
    source_count: int = Field(description="다시 적재하는 자료 수")


# ---------------------------------------------------------------- 구성원 · 받은 인수인계 · 관리자


class MemberOut(BaseModel):
    """프로필을 입력한 계정(인수자 선택 목록·관리자 구성원 목록)."""

    id: int
    login_id: str
    name: str
    is_admin: bool = Field(default=False, description="관리자 권한(users.is_admin)")
    department: str
    team: str
    position: str
    organization: str = Field(description="부서 + 팀")


class MembersResult(Result):
    members: list[MemberOut]


class ReceivedHandoverOut(BaseModel):
    """내가 인수자로 지정된 인수인계. 읽기(상태·문서·내려받기)만 할 수 있다."""

    id: str = Field(description="세션 ID. 문서는 GET /api/sessions/{id}/document, PDF 는 /download 로 읽는다")
    title: str
    task_name: str
    duties: list[str] = Field(default_factory=list, description="이 인수인계서에 담긴 담당 업무 이름(한 건에 여러 업무가 들어갈 수 있다). 받은 인수인계 화면의 업무별 검색에 쓴다")
    mode: str = Field(description="reassignment(정기인사 발령) | leave(장기휴가)")
    leave_start: str = Field(default="", description="장기휴가 시작일(YYYY-MM-DD, 장기휴가일 때만)")
    leave_end: str = Field(default="", description="장기휴가 종료일")
    owner_name: str
    owner_login_id: str = ""
    organization: str
    position: str
    handover_date: str
    stage: str
    status: str
    document_version: int
    file_count: int
    document_origin: str = Field(default="", description="최신 인수인계서의 출처: ai(AI 작성) | user(직접 수정) | restore(복원), 문서가 없으면 빈 값")
    document_origin_label: str = ""
    document_updated_at: datetime | None = Field(default=None, description="최신 인수인계서가 만들어진(수정된) 시각")
    created_at: datetime
    updated_at: datetime


class ReceivedHandoversResult(Result):
    handovers: list[ReceivedHandoverOut]


class AdminMemberOut(MemberOut):
    handover_count: int = Field(description="이 구성원이 인계자인 인수인계 수")
    completed_count: int = Field(description="그중 문서가 만들어진 수")
    last_login_at: datetime | None = None


class AdminMembersResult(Result):
    organization: str = Field(description="관리자의 소속(부서 + 팀). 이 소속의 구성원만 보인다")
    members: list[AdminMemberOut]


class AdminHandoverOut(ReceivedHandoverOut):
    successor: str
    successor_login_id: str
    question_count: int
    open_gap_count: int


class AdminHandoversResult(Result):
    organization: str
    handovers: list[AdminHandoverOut]


# ---------------------------------------------------------------- 메일 조회(가상 메일 조회 API)


class MailAddressOut(BaseModel):
    name: str
    email: str


class MailAttachmentOut(BaseModel):
    name: str
    size: int
    content_type: str


class MailSummary(BaseModel):
    id: str
    folder: str = Field(description="inbox(받은 메일함) | sent(보낸 메일함)")
    subject: str
    sender: MailAddressOut
    to: list[MailAddressOut]
    sent_at: datetime
    snippet: str = Field(description="본문 앞부분(목록 미리보기)")
    has_attachments: bool
    attachment_count: int
    important: bool
    relevance: int = Field(description="단위업무와의 관련도 점수(task_id·session_id 를 주었을 때, 높을수록 관련)")
    recommended: bool = Field(description="단위업무와 관련 있어 보이는 메일")
    imported: bool = Field(description="이미 이 업무 자료로 가져온 메일")


class MailDetail(MailSummary):
    cc: list[MailAddressOut]
    body: str
    attachments: list[MailAttachmentOut]


class MailListOut(Result):
    provider: str = Field(description="연결된 메일 조회 API(mock = 가상 메일함)")
    mailbox: MailAddressOut
    messages: list[MailSummary]
    total: int
    page: int
    size: int
    has_next: bool
    keywords: list[str] = Field(description="관련도 정렬에 쓴 단위업무 낱말")


class MailImportIn(BaseModel):
    message_ids: list[str] = Field(min_length=1, max_length=20, description="가져올 메일 ID(목록의 id)")


class MailImportResult(Result):
    session_id: str
    task_id: str | None = None
    files: list[SourceOut] = Field(description="새로 등록한 자료(.eml)")
    skipped: list[str] = Field(description="이미 가져와서 건너뛴 메일 제목")
    analysis_scheduled: bool


# ---------------------------------------------------------------- 관리자: 팀 인수인계 현황


class TeamOut(BaseModel):
    department: str
    team: str
    label: str = Field(description="화면 표시용 '부서 팀명'")
    member_count: int
    task_count: int
    document_count: int
    completion_rate: float | None = Field(description="인수인계서가 만들어진 단위업무 비율(0~1, 단위업무가 없으면 null)")


class TeamsOut(Result):
    teams: list[TeamOut]


class StageCounts(BaseModel):
    preparing: int = Field(description="자료 준비(분석 전)")
    analyzing: int = Field(description="AI 분석 중")
    answering: int = Field(description="질의응답·문서 작성 중")
    completed: int = Field(description="인수인계서 완성(검토 단계)")


class MemberPerformance(BaseModel):
    user_id: int
    login_id: str
    name: str
    position: str
    is_admin: bool
    last_login_at: datetime | None
    last_activity_at: datetime | None = Field(description="마지막 작업 시각(자료 등록·답변·문서 저장)")
    task_count: int = Field(description="단위업무 수")
    document_count: int = Field(description="인수인계서가 만들어진 단위업무 수")
    completion_rate: float | None = Field(description="작성 완료율 = document_count / task_count")
    stages: StageCounts
    source_count: int = Field(description="등록한 자료 수")
    answer_count: int = Field(description="AI 에 보낸 답변·요청 수")
    questions_answered: int
    questions_skipped: int
    questions_open: int = Field(description="아직 답하지 않은 질문")
    answer_rate: float | None = Field(description="질문 응답률 = 답함 / (답함 + 건너뜀)")
    coverage_rate: float | None = Field(description="항목 충족률(장별 충분 1 · 부분 0.5 · 부족 0 의 평균)")
    ai_versions: int = Field(description="AI 가 쓴 인수인계서 버전 수")
    user_versions: int = Field(description="직접 수정·복원한 버전 수")
    avg_hours_to_first_document: float | None = Field(description="단위업무 등록부터 첫 인수인계서까지 평균 시간")
    cost_usd: float = Field(description="Claude API 추정 비용(USD)")


class TeamSummary(BaseModel):
    member_count: int
    active_member_count: int = Field(description="단위업무를 하나 이상 등록한 팀원 수")
    task_count: int
    document_count: int
    completion_rate: float | None
    answer_rate: float | None
    coverage_rate: float | None
    avg_hours_to_first_document: float | None
    user_versions: int
    cost_usd: float


class TeamReportOut(Result):
    department: str
    team: str
    label: str
    date_from: date | None
    date_to: date | None
    summary: TeamSummary
    members: list[MemberPerformance]


class MemberTaskOut(BaseModel):
    task_id: str
    session_id: str
    name: str
    mode: str
    stage: str
    stage_group: str = Field(description="preparing | analyzing | answering | completed")
    status: str
    document_version: int
    has_document: bool
    source_count: int
    answer_count: int
    questions_answered: int
    questions_skipped: int
    questions_open: int
    coverage_rate: float | None
    ai_versions: int
    user_versions: int
    hours_to_first_document: float | None
    cost_usd: float
    created_at: datetime
    last_activity_at: datetime | None


class ActivityOut(BaseModel):
    kind: str = Field(description="task | source | answer | skip | command | document_ai | document_user | document_restore")
    label: str
    at: datetime | None
    task_id: str
    task_name: str
    detail: str = Field(description="자료 이름, 문서 버전 등(답변 내용은 담지 않음)")


class MemberReportOut(Result):
    member: MemberPerformance
    department: str
    team: str
    tasks: list[MemberTaskOut]
    activity: list[ActivityOut] = Field(description="최근 수행 기록(최근순, 최대 50건)")


# ---------------------------------------------------------------- 팀 인수인계서 지침(관리자)
class TeamGuidelineItem(_Stripped):
    id: str = Field(default="", max_length=32, description="항목 id(비우면 서버가 만든다)")
    name: str = Field(min_length=1, max_length=100, description="항목 이름. 예: 외주 관리")
    instruction: str = Field(default="", max_length=500, description="작성 안내: 이 항목에 무엇을 적어야 하는지")


class TeamGlossaryTerm(_Stripped):
    term: str = Field(min_length=1, max_length=60, description="용어. 예: 개설기관")
    meaning: str = Field(default="", max_length=300, description="뜻. 예: 오픈뱅킹 서비스를 제공하기 위해 개설된 기관")


class TeamDocumentOut(_Stripped):
    id: str
    name: str
    size: int
    uploaded_by: str = ""
    uploaded_at: datetime | None = None


class TeamGuidelineIn(_Stripped):
    # 보낸 필드만 바꾼다(보내지 않은 필드는 그대로). 항목 화면은 items·notes 를, AI 맞춤 설정 화면은 prompt 만 보낸다.
    items: list[TeamGuidelineItem] | None = Field(default=None, max_length=30, description="우리 팀 인수인계서에 담는 항목(자료에 있으면 이 이름으로 정리)")
    notes: str | None = Field(default=None, max_length=2000, description="팀 특징·작성 지침(자유 글)")
    prompt: str | None = Field(default=None, max_length=2000, description="팀 전용 프롬프트: AI 가 항상 따라야 하는 역할과 판단 기준")
    intro: str | None = Field(default=None, max_length=300, description="STEP1 팀 소개(3줄 이내)")
    glossary: list[TeamGlossaryTerm] | None = Field(default=None, max_length=50, description="STEP2 업무 용어집")


class TeamGuidelineOut(Result):
    organization: str = Field(description="지침이 적용되는 소속(부서 + 팀)")
    items: list[TeamGuidelineItem]
    notes: str
    prompt: str
    intro: str = ""
    glossary: list[TeamGlossaryTerm] = []
    documents: list[TeamDocumentOut] = Field(default=[], description="STEP3 팀 공통 업무 자료(최신순)")
    updated_at: datetime | None = None
    updated_by: str = ""
    editable: bool = Field(description="이 계정이 지침을 저장할 수 있는지")
    prompt_preview: str = Field(description="AI 프롬프트에 들어가는 문구 미리보기(지침이 비어 있으면 빈 문자열)")


# ---------------------------------------------------------------- 팀 담당 업무(팀장이 관리, 인수인계 1단계에서 고른다)


class TeamDutyIn(_Stripped):
    name: str = Field(min_length=1, max_length=100, description="업무 이름. 예: 홈페이지 운영")
    description: str = Field(default="", max_length=500, description="업무 설명(선택)")


class TeamDutyUpdate(_Stripped):
    # 보낸 필드만 바꾼다
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    sort_order: int | None = Field(default=None, ge=0, description="목록 순서(작을수록 앞)")


class TeamDutyOut(BaseModel):
    id: str
    name: str
    description: str
    sort_order: int
    updated_by: str = ""
    updated_at: datetime | None = None


class TeamDutiesOut(Result):
    organization: str = Field(description="업무 목록이 속한 소속(부서 + 팀)")
    duties: list[TeamDutyOut]
    editable: bool = Field(description="이 계정이 목록을 고칠 수 있는지(팀 관리자)")


# ---------------------------------------------------------------- 인수인계서 전송(인계자 → 인수자)


class RecipientCandidate(BaseModel):
    user_id: int
    name: str
    login_id: str
    department: str
    team: str
    position: str


class RecipientSearchOut(Result):
    candidates: list[RecipientCandidate]


class TransferIn(_Stripped):
    recipient_name: str = Field(default="", max_length=50, description="인수자 이름. 같은 이름이 여럿이면 409 와 함께 후보가 온다")
    recipient_id: int | None = Field(default=None, ge=1, description="인수자 회원 ID(이름이 겹칠 때 후보에서 고른 값). 주면 이름보다 우선")
    message: str = Field(default="", max_length=1000, description="인수자에게 남길 말(선택)")


class TransferSummary(BaseModel):
    id: str
    title: str
    task_name: str
    mode: str
    document_version: int = Field(description="지금 보이는 인수인계서 버전(인계자가 보낸 뒤 직접 고쳤으면 고친 최신 버전)")
    sent_version: int = Field(default=0, description="보낸 시점의 버전")
    updated_since_sent: bool = Field(default=False, description="보낸 뒤 인계자가 문서를 고쳤는지(최신 버전이 보낸 버전과 다름)")
    updated_since_read: bool = Field(default=False, description="인수자가 열어 본 뒤 새 버전이 생겼는지")
    document_origin: str = Field(default="ai", description="최신 버전의 출처: ai(AI 작성) | user(직접 수정) | restore(이전 버전 복원)")
    document_origin_label: str = "AI 작성"
    document_updated_at: datetime | None = Field(default=None, description="최신 버전이 만들어진 시각")
    sender: RecipientCandidate = Field(description="보낸 사람(인계자)")
    recipient: RecipientCandidate = Field(description="받는 사람(인수자)")
    handover_date: str
    message: str
    created_at: datetime = Field(description="보낸 시각")
    read_at: datetime | None = Field(description="인수자가 처음 열어 본 시각(안 읽었으면 null)")
    is_read: bool


class TransferDetail(TransferSummary):
    markdown: str = Field(description="인수인계서 전체(Markdown). 기본은 최신 버전(직접 수정 포함), ?sent=true 면 보낸 시점 문서. 인계자가 업무를 지웠으면 보낸 시점 문서")
    session_id: str | None = Field(description="원래 인계 세션(인계자가 지웠으면 null)")


class TransferResult(Result):
    transfer: TransferSummary
    calendar_events: int = Field(default=0, description="인수자 캘린더에 새로 넣은 일정 수(마감일·시작·종료일)")
    calendar_routines: int = Field(default=0, description="정기 업무 칸에 넣은 수(매일·매주·매월·분기·매년·수시)")
    calendar_message: str = Field(default="", description="캘린더에 넣은 결과 안내")


class HandoverCalendarResult(Result):
    events: int = Field(description="새로 넣은 일정 수")
    routines: int = Field(description="새로 넣은 정기 업무 수")
    skipped: int = Field(description="이미 캘린더에 있어 건너뛴 수")


class OnetelEventOut(BaseModel):
    id: str
    date: str = Field(description="YYYY-MM-DD")
    text: str
    imported: bool = Field(description="이미 내 캘린더에 있는지")


class OnetelEventsOut(Result):
    events: list[OnetelEventOut]


class OnetelImportIn(BaseModel):
    ids: list[str] | None = Field(default=None, description="가져올 일정 id(GET /api/planner/onetel/events 의 id). 비우면(null) 모두")


class OnetelImportResult(Result):
    events: int = Field(description="원텔에서 새로 가져와 넣은 일정 수")
    skipped: int = Field(description="이미 캘린더에 있어 건너뛴 수")


class TransfersOut(Result):
    transfers: list[TransferSummary]
    total: int
    unread_count: int = Field(default=0, description="아직 열어 보지 않은 받은 인수인계서 수")


class HandoverChatScope(BaseModel):
    id: str = Field(description="질문 범위 ID. 내 업무와 전달받은 업무는 세션 ID, 인계자가 지워 보낸 시점 문서만 남은 것은 'transfer:<ID>'")
    title: str
    task_name: str
    owner_name: str = Field(description="인계자")
    kind: Literal["mine", "received", "team"] = Field(
        description="mine: 내가 만든 업무, received: 인계자가 나에게 전송한 인수인계서(원본 자료는 공유되지 않아 인수인계서만 근거가 된다), "
        "team: 팀장(관리자)이 검토하는 같은 팀 구성원의 인수인계(자료·인수인계서 모두 근거)"
    )
    version: int = Field(description="인수인계서 버전(아직 없으면 0)")
    has_document: bool
    source_count: int = Field(description="질문 근거가 되는 올린 자료 수(분석이 끝난 파일·링크·메일). 받은 인수인계는 항상 0")
    session_id: str | None = Field(description="화면에서 열 수 있는 세션 ID(지워졌으면 null)")
    updated_at: datetime
    duties: list[str] = Field(default_factory=list, description="인수인계서에 담긴 담당 업무 이름(챗봇 화면의 업무 검색에 쓴다)")
    recipients: list[str] = Field(default_factory=list, description="내 업무(mine)를 전송한 인수자 이름. 받은 인수인계는 빈 목록")


class HandoverChatScopesOut(BaseModel):
    scopes: list[HandoverChatScope]


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=6000)


class HandoverChatIn(_Stripped):
    question: str = Field(min_length=1, max_length=2000, description="인수인계에 대한 질문")
    history: list[ChatTurn] = Field(default_factory=list, max_length=40, description="앞선 대화(꼬리 질문을 이해하는 데 쓴다. 최근 8개만 쓴다)")
    scope_ids: list[str] | None = Field(default=None, max_length=50, description="질문할 업무 ID(GET /api/handover-chat/scopes 의 id). 비우면 볼 수 있는 모든 업무")


class HandoverChatSource(BaseModel):
    scope_id: str
    scope_title: str
    scope_task: str = Field(default="", description="참조한 업무 이름(단위업무명). 없으면 빈 문자열")
    scope_kind: Literal["mine", "received", "team"] = Field(default="mine", description="mine: 내 업무, received: 전달받은 인수인계, team: 팀원 인수인계(팀장)")
    scope_owner: str = Field(default="", description="그 업무의 인계자 이름")
    session_id: str | None = Field(description="인수인계서를 열 수 있는 세션 ID(지워졌으면 null)")
    kind: Literal["document", "answer", "mail", "link", "file", "note"] = Field(description="document: 인수인계서, answer: 인계자 답변, mail: 메일, link: 링크, file: 올린 파일, note: 메모")
    label: str = Field(description="출처 표기(파일명 p.3, 메일 제목, 인수인계서 v4 › 장 제목 …)")
    source_id: str | None = Field(default=None, description="올린 자료(파일·메일)의 ID. 내 업무면 GET /api/sessions/{session_id}/sources/{source_id}/file 로 원본을 받을 수 있다")


class HandoverChatOut(BaseModel):
    answer: str = Field(description="답변(Markdown)")
    grounded: bool = Field(description="자료에 적힌 내용으로 답했는지(false 면 자료에서 못 찾았거나 관련 없는 질문)")
    sources: list[HandoverChatSource] = Field(description="답에 근거로 쓴 자료")
    searched: int = Field(description="질문과 관련 있다고 찾은 근거 조각 수")
    engine: str = Field(description="답한 엔진: claude | offline | mock")


# ---------------------------------------------------------------- 직제규정 시행세칙(업무분장)


class OrgTeamOut(BaseModel):
    name: str
    duties: list[str] = Field(description="이 팀의 단위업무(업무분장상 단위업무)")


class OrgDivisionOut(BaseModel):
    name: str = Field(description="본부·부")
    teams: list[OrgTeamOut]


class OrgDutiesResult(Result):
    title: str = Field(description="자료 이름(예: 직제규정 시행세칙(가상 예시))")
    note: str = ""
    divisions: list[OrgDivisionOut]


# ---------------------------------------------------------------- 캘린더 일정 · 할 일(계정별)

PLANNER_KEY_PATTERN = r"^[A-Za-z0-9_-]{1,40}$"  # 화면이 만든 id(비우면 서버가 만든다)


def _planner_date(value: str | None) -> str | None:
    """YYYY-MM-DD 이고 실제 있는 날짜인지 확인한다."""
    if value is None:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("날짜는 YYYY-MM-DD 형식이어야 합니다.")
    date.fromisoformat(value)
    return value


class PlannerEventIn(_Stripped):
    id: str | None = Field(default=None, pattern=PLANNER_KEY_PATTERN, description="일정 id(선택). 같은 id 로 다시 보내면 새로 만들지 않고 있던 일정을 돌려준다")
    date: str = Field(description="날짜(YYYY-MM-DD)")
    text: str = Field(min_length=1, max_length=200, description="일정 내용")

    @field_validator("date")
    @classmethod
    def _check_date(cls, value):
        return _planner_date(value)


class PlannerEventOut(BaseModel):
    id: str
    date: str
    text: str
    memo: str = ""
    done: bool = False


class PlannerItemUpdate(_Stripped):
    """일정·주간 업무 수정(보낸 필드만): 내용 · 메모 · 다 했는지(체크리스트). 메모를 빈 문자열로 보내면 지운다."""

    text: str | None = Field(default=None, min_length=1, max_length=200)
    memo: str | None = Field(default=None, max_length=500)
    done: bool | None = None


class PlannerTodoIn(_Stripped):
    id: str | None = Field(default=None, pattern=PLANNER_KEY_PATTERN, description="할 일 id(선택). 같은 id 로 다시 보내면 있던 할 일을 돌려준다")
    text: str = Field(min_length=1, max_length=200, description="할 일 내용")
    done: bool = False
    date: str | None = Field(default=None, description="날짜(YYYY-MM-DD). 비우면(null) 날짜 없는 할 일")

    @field_validator("date")
    @classmethod
    def _check_date(cls, value):
        return _planner_date(value)


class PlannerTodoUpdate(_Stripped):
    # 보낸 필드만 바꾼다. date 를 null 로 보내면 날짜 없는 할 일이 된다.
    text: str | None = Field(default=None, min_length=1, max_length=200)
    done: bool | None = None
    date: str | None = Field(default=None, description="날짜(YYYY-MM-DD) 또는 null")
    memo: str | None = Field(default=None, max_length=500, description="짧은 메모(빈 문자열이면 지움)")

    @field_validator("date")
    @classmethod
    def _check_date(cls, value):
        return _planner_date(value)


class PlannerTodoOut(BaseModel):
    id: str
    text: str
    done: bool
    date: str | None
    memo: str = ""


class PlannerWeekTaskIn(_Stripped):
    id: str | None = Field(default=None, pattern=PLANNER_KEY_PATTERN, description="주간 업무 id(선택). 같은 id 로 다시 보내면 있던 값을 돌려준다")
    week: str = Field(description="그 주 일요일(YYYY-MM-DD). 다른 요일을 보내면 그 주 일요일로 맞춘다")
    text: str = Field(min_length=1, max_length=200, description="주간 업무 내용")

    @field_validator("week")
    @classmethod
    def _check_week(cls, value):
        day = date.fromisoformat(_planner_date(value))
        return (day - timedelta(days=(day.weekday() + 1) % 7)).isoformat()


class PlannerWeekTaskOut(BaseModel):
    id: str
    week: str = Field(description="그 주 일요일")
    text: str
    memo: str = ""
    done: bool = False


class PlannerWeekTaskResult(Result):
    task: PlannerWeekTaskOut


class PlannerRoutineOut(BaseModel):
    id: str
    cadence: str = Field(description="주기: daily | weekly | monthly | quarterly | yearly | other")
    name: str = Field(description="업무명")
    timing: str = Field(description="인수인계서에 적힌 수행 시기")
    months: list[int] = Field(default_factory=list, description="분기·반기·매년 업무의 해당 월(비면 매달 또는 시기를 모름)")
    source: str = Field(description="가져온 인수인계서")


class PlannerOut(Result):
    events: list[PlannerEventOut] = Field(description="일정(추가한 순서)")
    todos: list[PlannerTodoOut] = Field(description="할 일(추가한 순서)")
    routines: list[PlannerRoutineOut] = Field(default_factory=list, description="받은 인수인계서에서 가져온 정기 업무(주기별)")
    week_tasks: list[PlannerWeekTaskOut] = Field(default_factory=list, description="주간 업무(주마다 직접 적은 일, 추가한 순서)")


class PlannerEventResult(Result):
    event: PlannerEventOut


class PlannerTodoResult(Result):
    todo: PlannerTodoOut


class PlannerImportIn(BaseModel):
    """예전 화면이 브라우저(localStorage)에 저장해 둔 일정·할 일을 한 번에 옮긴다. id 가 이미 있으면 건너뛴다."""

    events: list[PlannerEventIn] = Field(default_factory=list, max_length=2000)
    todos: list[PlannerTodoIn] = Field(default_factory=list, max_length=2000)


class PlannerCleared(Result):
    removed: int = Field(description="지운 할 일 수")


# ---------------------------------------------------------------- 정기인사 일정(팀장이 등록)


def _iso_date(value: str | None, *, required: bool) -> str | None:
    value = (value or "").strip()
    if not value:
        if required:
            raise ValueError("날짜를 입력해 주세요.")
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError("날짜는 YYYY-MM-DD 형식으로 입력해 주세요.") from exc


class PersonnelScheduleIn(_Stripped):
    name: str = Field(min_length=1, max_length=100, description="일정 이름(예: 2027 상반기 정기인사)")
    announce_date: str | None = Field(default=None, description="발표일 YYYY-MM-DD(비워도 됨)")
    appoint_date: str = Field(description="발령일 YYYY-MM-DD")

    @field_validator("announce_date")
    @classmethod
    def _check_announce(cls, value):
        return _iso_date(value, required=False)

    @field_validator("appoint_date")
    @classmethod
    def _check_appoint(cls, value):
        return _iso_date(value, required=True)

    @model_validator(mode="after")
    def _check_order(self):
        if self.announce_date and self.announce_date > self.appoint_date:
            raise ValueError("발표일은 발령일보다 늦을 수 없습니다.")
        return self


class PersonnelScheduleOut(BaseModel):
    id: int
    name: str
    announce_date: str | None = Field(description="발표일(없으면 null)")
    appoint_date: str = Field(description="발령일")
    organization: str = Field(description="일정이 적용되는 소속(부서 팀)")
    created_by: str = Field(description="등록한 사람")


class PersonnelSchedulesOut(Result):
    schedules: list[PersonnelScheduleOut] = Field(description="내 소속 일정(발령일 순)")
    organization: str = Field(description="내 소속")
    editable: bool = Field(description="관리자라서 추가·수정·삭제할 수 있는지")


class PersonnelScheduleResult(Result):
    schedule: PersonnelScheduleOut


class TeamScheduleIn(_Stripped):
    title: str = Field(min_length=1, max_length=100, description="일정 이름(예: 하반기 팀 워크숍)")
    start_date: str = Field(description="시작일 YYYY-MM-DD")
    end_date: str | None = Field(default=None, description="끝나는 날 YYYY-MM-DD(하루짜리면 비워 둠)")
    memo: str = Field(default="", max_length=300, description="팀원에게 보여 줄 메모(장소·준비물 등)")

    @field_validator("start_date")
    @classmethod
    def _check_start(cls, value):
        return _iso_date(value, required=True)

    @field_validator("end_date")
    @classmethod
    def _check_end(cls, value):
        return _iso_date(value, required=False)

    @model_validator(mode="after")
    def _check_order(self):
        if self.end_date and self.end_date < self.start_date:
            raise ValueError("끝나는 날은 시작일보다 빠를 수 없습니다.")
        if self.end_date == self.start_date:
            self.end_date = None
        return self


class TeamScheduleOut(BaseModel):
    id: int
    title: str
    start_date: str = Field(description="시작일")
    end_date: str | None = Field(description="끝나는 날(하루짜리면 null)")
    memo: str
    organization: str = Field(description="일정이 적용되는 소속(부서 팀)")
    created_by: str = Field(description="등록한 사람")


class TeamSchedulesOut(Result):
    schedules: list[TeamScheduleOut] = Field(description="내 소속 공유 일정(시작일 순)")
    organization: str = Field(description="내 소속")
    editable: bool = Field(description="관리자라서 추가·수정·삭제할 수 있는지")


class TeamScheduleResult(Result):
    schedule: TeamScheduleOut


class NotificationOut(BaseModel):
    id: str = Field(description="알림 ID('transfer:<전송 ID>:v<문서 버전>', 마감 임박은 'due:event|todo:<ID>:<날짜>')")
    kind: Literal["received", "updated", "due"] = Field(description="received: 새 인수인계서 도착, updated: 인계자가 보낸 뒤 문서를 고침, due: 캘린더 일정·할 일 마감이 일주일 안으로 다가옴")
    session_id: str | None = Field(default=None, description="문서를 열 세션(인계자가 지웠으면 null)")
    title: str
    text: str
    sender_name: str = ""
    task_name: str = ""
    created_at: datetime
    read: bool


class NotificationsOut(Result):
    notifications: list[NotificationOut]
    unread: int = 0


class NotificationIdsIn(BaseModel):
    ids: list[str] | None = Field(default=None, max_length=200, description="대상 알림 ID. null 이면 모든 알림")
