"""백그라운드 작업 실행기.

- 에이전트 작업(분석·답변 처리·문서 생성)은 수십 초가 걸릴 수 있어 요청 스레드에서 돌리지 않고 스레드 풀에서 실행한다.
  프론트엔드는 /state 를 폴링해 진행 상황과 새 메시지를 받는다.
- 세션당 에이전트 작업은 한 번에 하나만 실행한다(동시에 두 답변이 처리되는 일 방지).
- 에이전트가 흘려보내는 이벤트(stage/progress/slot/message/document)를 그대로 DB 에 기록한다.
- 파일·링크 적재(파싱→청킹→ChromaDB)는 별도 작업으로 돌리고, 분석이 끝난 세션이면 에이전트에 '새 자료' 를 알려 재분석한다.
- 단위업무 업로드처럼 auto_analyze 로 요청한 적재는 모든 자료 처리가 끝나면 AI 분석(STAGE 1)을 스스로 시작한다.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any

from amigo_agent import AgentStateError, LLMError
from amigo_rag import ParseError

from sqlalchemy import select

from ..models import FileBlob, HandoverSession, Message, Source, UnitTask, utcnow
from .documents import record_ai_document
from .department_notes import department_notes_for
from .guidelines import guidelines_for

if TYPE_CHECKING:
    from .container import Services

logger = logging.getLogger(__name__)

AgentCall = Callable[[Callable[[dict[str, Any]], None]], Any]
REANALYZE_STAGES = ("qna", "review")
BUDGET_SKIPPED = "Claude API 예산을 모두 써서 자동 분석을 시작하지 않았어요. 예산을 늘린 뒤 '분석 시작'을 눌러 주세요."
INTERRUPTED = "서버가 다시 시작되면서 진행 중이던 AI 작업이 멈췄습니다. '다시 시도'를 누르면 멈춘 곳부터 이어서 진행합니다."


class SessionBusy(Exception):
    pass


class JobRunner:
    def __init__(self, services: Services, workers: int = 4) -> None:
        self.services = services
        self.pool = ThreadPoolExecutor(max_workers=max(2, workers), thread_name_prefix="amigo-job")
        self._active: set[str] = set()
        self._reindexing: set[str] = set()  # 지식베이스를 다시 적재하는 중인 세션
        self._lock = threading.Lock()
        self._auto_lock = threading.Lock()

    # ------------------------------------------------------------------ 상태
    def is_busy(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._active or session_id in self._reindexing

    def shutdown(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)

    def recover(self) -> None:
        """서버가 작업 도중 꺼졌다 켜졌을 때 DB 에 남은 '처리 중' 상태를 정리한다.

        - AI 작업 중(running)이던 세션: 다시 시도할 수 있는 오류로 바꾼다(재시도하면 마지막 체크포인트부터 이어 감).
        - 적재 중(pending/processing)이던 자료: 다시 적재한다(디스크 사본이 없으면 DB 원본으로 복원).
        """
        with self.services.db() as db:
            for row in db.scalars(select(HandoverSession).where(HandoverSession.status == "running")):
                row.status, row.error, row.progress_message = "error", INTERRUPTED, ""
                db.add(Message(session_id=row.id, role="system", kind="error", content=INTERRUPTED, meta_json=json.dumps({"retry": True})))
            stuck: dict[str, list[str]] = {}
            for source in db.scalars(select(Source).where(Source.status.in_(("pending", "processing")))):
                source.status = "pending"
                stuck.setdefault(source.session_id, []).append(source.id)
            unit_sessions = set(db.scalars(select(UnitTask.session_id).where(UnitTask.session_id.in_(stuck)))) if stuck else set()
            db.commit()
        for session_id, source_ids in stuck.items():
            auto = self.services.settings.auto_analyze and session_id in unit_sessions
            self.run_ingest(session_id, source_ids, auto_analyze=auto)

    # ------------------------------------------------------------------ 에이전트 작업
    def run_agent(self, session_id: str, action: str, call: AgentCall, *, progress: str = "") -> None:
        with self._lock:
            if session_id in self._active:
                raise SessionBusy()
            self._active.add(session_id)
        with self.services.db() as db:
            row = db.get(HandoverSession, session_id)
            row.status = "running"
            row.error = ""
            row.progress_message = progress or "AI가 처리하고 있어요."
            db.commit()
        self.pool.submit(self._agent_job, session_id, action, call)

    def start_analysis(self, session_id: str, *, restart: bool = False) -> None:
        """STAGE 1 분석을 처음부터 시작한다(이전 분석 결과는 지운다). 처리 중이면 SessionBusy."""
        with self.services.db() as db:
            row = db.get(HandoverSession, session_id)
            if restart:
                db.add(Message(session_id=session_id, role="system", kind="info", content="등록된 자료로 분석을 처음부터 다시 시작합니다."))
            row.stage = "analyzing"
            row.slots_json, row.gaps_json, row.pending_files_json = "{}", "[]", "[]"
            row.progress_current, row.progress_total = 0, 6
            db.commit()
            profile = row.profile()
            guidelines = guidelines_for(db, row.organization)  # 팀 관리자가 정한 인수인계서 지침(없으면 None)
            if guidelines:
                profile["team_guidelines"] = guidelines
            dept_notes = department_notes_for(row.organization)  # 부·팀 인수인계 목록(없으면 None): 분석·질문·대화에 반영한다
            if dept_notes:
                profile["dept_notes"] = dept_notes
        kb, agent = self.services.kb(session_id), self.services.agent
        self.run_agent(session_id, "analyze", lambda h: agent.start(session_id, profile, kb, on_event=h), progress="자료를 분석하고 있어요.")

    def _agent_job(self, session_id: str, action: str, call: AgentCall) -> None:
        recorder = EventRecorder(self.services, session_id)
        try:
            if action != "analyze":  # 팀 관리자가 분석 시작 뒤에 지침·프롬프트를 고쳤어도 이번 턴부터 반영한다(분석 시작은 최신 값을 새로 읽는다)
                self._refresh_agent_profile(session_id)
            call(recorder)
            self._sync_snapshot(session_id)
        except (LLMError, AgentStateError) as exc:
            self._record_error(session_id, str(exc))
        except Exception as exc:  # 예상 못 한 오류도 세션을 'error' 로 남겨 재시도할 수 있게 한다
            logger.exception("agent job %s failed for %s", action, session_id)
            self._record_error(session_id, f"처리 중 오류가 발생했습니다: {exc.__class__.__name__}")
        finally:
            with self._lock:
                self._active.discard(session_id)
        self._drain_pending_files(session_id)

    def _refresh_agent_profile(self, session_id: str) -> None:
        """진행 중인 대화의 인계자 정보에 팀 지침(팀장 프롬프트 포함)과 부·팀 인수인계 목록의 최신 값을 반영한다. 지침을 지웠으면 None 으로 지운다.

        인수인계서 양식(form)도 함께 넣는다: 양식 컬럼이 생기기 전에 분석을 시작한 세션도 다음 턴부터 세션에 저장된 양식을 따른다.
        """
        with self.services.db() as db:
            row = db.get(HandoverSession, session_id)
            if row is None:
                return
            patch = {
                "team_guidelines": guidelines_for(db, row.organization),
                "dept_notes": department_notes_for(row.organization),
                "form": row.form or "standard",
            }
        try:
            self.services.agent.update_profile(session_id, patch)
        except Exception:  # 갱신 실패가 답변 처리를 막지 않게 한다(이전 값으로 계속)
            logger.warning("profile refresh failed for %s", session_id, exc_info=True)

    def _sync_snapshot(self, session_id: str) -> None:
        snap = self.services.agent.snapshot(session_id)
        with self.services.db() as db:
            row = db.get(HandoverSession, session_id)
            if row is None:
                return
            row.stage = snap["stage"] or row.stage
            row.slots_json = json.dumps(snap["slots"], ensure_ascii=False)
            row.gaps_json = json.dumps(snap["gaps"], ensure_ascii=False)
            row.question_count = snap["question_count"]
            # 이벤트를 놓친 경우만 기록된다(같은 초안은 건너뜀). 사용자가 직접 고친 문서는 덮어쓰지 않는다
            record_ai_document(db, row, snap["document"] or "", int(snap["document_version"] or 0))
            row.engine = snap["engine"]
            row.status = "waiting" if snap["waiting"] else "idle"
            row.progress_message = ""
            db.commit()

    def _record_error(self, session_id: str, message: str) -> None:
        with self.services.db() as db:
            row = db.get(HandoverSession, session_id)
            if row is None:
                return
            row.status = "error"
            row.error = message
            row.progress_message = ""
            db.add(Message(session_id=session_id, role="system", kind="error", content=message, meta_json=json.dumps({"retry": True})))
            db.commit()

    # ------------------------------------------------------------------ 자료 적재
    def run_ingest(self, session_id: str, source_ids: list[str], *, auto_analyze: bool = False, notify: bool = True) -> None:
        """자료를 지식베이스에 적재한다. notify=False 면 적재가 끝나도 에이전트에 '새 자료'를 알리지 않는다(재적재)."""
        if source_ids:
            self.pool.submit(self._ingest_job, session_id, source_ids, auto_analyze, notify)

    def reindex(self, session_id: str, source_ids: list[str] | None = None, *, rebuild: bool | None = None) -> int:
        """이미 처리된 자료를 원본(디스크 사본 또는 DB 원본)으로 다시 적재한다. 다시 적재하는 자료 수를 돌려준다.

        - 같은 source_id 로 다시 넣으면 RAG 가 그 자료의 조각만 교체하므로, 질의응답 중 지식베이스에 쌓인 인계자 답변은 남는다.
        - 임베딩 설정이 바뀐 지식베이스는 조각을 추가할 수 없으므로(RAG 가 거절) 컬렉션을 지우고 처음부터 만든다(rebuild).
          rebuild=None 이면 지식베이스 상태를 보고 정한다.
        - 다시 적재하는 동안 이 세션은 '처리 중'으로 보아 AI 작업 요청을 409 로 막는다(빈 지식베이스로 답하지 않도록).
        - 링크 자료는 주소에서 다시 가져온다. 접속할 수 없으면 그 자료는 실패로 바뀐다(링크 원문은 따로 보관하지 않음).
        """
        if rebuild is None:
            rebuild = self.services.kb_state(session_id)["state"] == "mismatch"
        with self.services.db() as db:
            query = select(Source).where(Source.session_id == session_id, Source.status == "ready")
            if source_ids is not None and not rebuild:
                query = query.where(Source.id.in_(source_ids))
            sources = list(db.scalars(query))
            for source in sources:
                source.status = "pending"
            db.commit()
            ids = [s.id for s in sources]
        if not ids:
            return 0
        with self._lock:
            self._reindexing.add(session_id)
        if rebuild:
            self.services.drop_kb(session_id)
        self.pool.submit(self._ingest_job, session_id, ids, False, False)
        return len(ids)

    def check_knowledge_bases(self) -> None:
        """서버 시작 때: 처리 완료(ready)된 자료가 지식베이스에 없거나, 임베딩 설정이 바뀐 지식베이스를 다시 적재한다."""
        with self.services.db() as db:
            rows = db.execute(select(Source.session_id, Source.id).where(Source.status == "ready")).all()
        by_session: dict[str, list[str]] = {}
        for session_id, source_id in rows:
            by_session.setdefault(session_id, []).append(source_id)
        total = 0
        for session_id, source_ids in by_session.items():
            try:
                state = self.services.kb_state(session_id)
            except Exception:
                logger.exception("knowledge base check failed: %s", session_id)
                continue
            if state["state"] == "mismatch":
                total += self.reindex(session_id, rebuild=True)
            else:
                missing = [sid for sid in source_ids if sid not in state["source_ids"]]
                if missing:
                    total += self.reindex(session_id, missing, rebuild=False)
        if total:
            logger.warning("지식베이스에 없거나 임베딩이 바뀐 자료 %d건을 다시 적재합니다.", total)

    def _ingest_job(self, session_id: str, source_ids: list[str], auto_analyze: bool = False, notify: bool = True) -> None:
        try:
            self._ingest(session_id, source_ids, auto_analyze, notify)
        finally:
            if not notify:  # 재적재가 끝나면 다시 AI 작업을 받을 수 있게
                with self._lock:
                    self._reindexing.discard(session_id)

    def _ingest(self, session_id: str, source_ids: list[str], auto_analyze: bool, notify: bool) -> None:
        kb = self.services.kb(session_id)
        ready_names: list[str] = []
        for source_id in source_ids:
            with self.services.db() as db:
                source = db.get(Source, source_id)
                if source is None:
                    continue
                source.status = "processing"
                db.commit()
                kind, path, url, name, link_type = source.kind, source.stored_path, source.url, source.name, source.link_type
            try:
                if kind == "file":
                    self._restore_file(source_id, path)
                    result = kb.add_file(path, source_id=source_id, display_name=name, metadata={"session_id": session_id})
                else:
                    result = kb.add_url(url, source_id=source_id, link_type=link_type or None, metadata={"session_id": session_id})
                status, error = ("ready", "") if result.chunk_count else ("failed", "텍스트를 추출하지 못했습니다(스캔 이미지이거나 내용이 비어 있음).")
            except ParseError as exc:
                result, status, error = None, "failed", str(exc)
            except Exception as exc:
                logger.exception("ingest failed: %s", source_id)
                result, status, error = None, "failed", f"자료 처리 중 오류가 발생했습니다: {exc.__class__.__name__}"
            if status == "failed" and not notify:
                error = f"다시 적재하지 못했습니다: {error}"
            with self.services.db() as db:
                source = db.get(Source, source_id)
                if source is None:
                    continue
                source.status = status
                source.error = error
                if result is not None:
                    source.chunk_count = result.chunk_count
                    source.warnings_json = json.dumps(result.warnings, ensure_ascii=False)
                    if kind == "link" and result.source_name:
                        source.name = result.source_name[:300]
                db.commit()
                if status == "ready":
                    ready_names.append(source.name)
        if ready_names and notify:
            self._queue_reanalysis(session_id, ready_names)
        if auto_analyze:
            self._auto_analyze(session_id)

    def _auto_analyze(self, session_id: str) -> None:
        """아직 분석 전(setup)이고 처리 중인 자료가 없으면 분석을 시작한다. 여러 적재 작업 중 마지막 것만 시작하게 잠근다."""
        with self._auto_lock:
            if self.is_busy(session_id):
                return
            with self.services.db() as db:
                row = db.get(HandoverSession, session_id)
                if row is None or row.stage != "setup":
                    return
                statuses = {s.status for s in row.sources}
                if statuses & {"pending", "processing"} or "ready" not in statuses:
                    return
            if self.services.budget_exhausted() is not None:  # 예산을 다 썼으면 자동으로 Claude 작업을 시작하지 않는다
                with self.services.db() as db:
                    db.add(Message(session_id=session_id, role="system", kind="info", content=BUDGET_SKIPPED))
                    db.commit()
                return
            try:
                self.start_analysis(session_id)
            except SessionBusy:
                pass

    def _restore_file(self, source_id: str, path: str) -> None:
        """RAG 파싱용 디스크 사본이 없으면(서버 이전·디스크 정리) DB 에 저장한 원본으로 다시 만든다."""
        target = Path(path)
        if not path or target.is_file():
            return
        with self.services.db() as db:
            blob = db.get(FileBlob, source_id)
            if blob is None:
                return
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob.content)

    def _queue_reanalysis(self, session_id: str, names: list[str]) -> None:
        """분석이 끝난 세션이면 새 자료를 에이전트에 알린다. AI 가 처리 중이면 끝난 뒤 처리하도록 쌓아 둔다."""
        with self.services.db() as db:
            row = db.get(HandoverSession, session_id)
            if row is None or row.stage not in REANALYZE_STAGES + ("analyzing", "summary", "composing"):
                return
            pending = json.loads(row.pending_files_json or "[]") + names
            row.pending_files_json = json.dumps(pending, ensure_ascii=False)
            db.commit()
        self._drain_pending_files(session_id)

    def _drain_pending_files(self, session_id: str) -> None:
        with self.services.db() as db:
            row = db.get(HandoverSession, session_id)
            if row is None or row.stage not in REANALYZE_STAGES or row.status == "error":
                return
            names = json.loads(row.pending_files_json or "[]")
            if not names or not self.services.agent.is_waiting(session_id):
                return
            row.pending_files_json = "[]"
            db.commit()
        kb = self.services.kb(session_id)
        agent = self.services.agent
        try:
            self.run_agent(
                session_id,
                "files",
                lambda h: agent.notify_files(session_id, kb, names, on_event=h),
                progress=f"새 자료 {len(names)}건을 분석에 반영하고 있어요.",
            )
        except SessionBusy:  # 다른 작업이 먼저 시작됐으면 다음 기회에 처리
            with self.services.db() as db:
                row = db.get(HandoverSession, session_id)
                if row is not None:
                    row.pending_files_json = json.dumps(json.loads(row.pending_files_json or "[]") + names, ensure_ascii=False)
                    db.commit()


class EventRecorder:
    """에이전트 이벤트를 DB 에 기록하는 콜백(백그라운드 스레드에서 호출됨)."""

    def __init__(self, services: Services, session_id: str) -> None:
        self.services = services
        self.session_id = session_id

    def __call__(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        with self.services.db() as db:
            row = db.get(HandoverSession, self.session_id)
            if row is None:
                return
            if kind == "stage":
                row.stage = event.get("stage", row.stage)
            elif kind == "progress":
                row.progress_message = str(event.get("message", ""))[:300]
                if "total" in event:
                    row.progress_current = int(event.get("current", 0))
                    row.progress_total = int(event.get("total", 0))
                if event.get("status") == "done" and row.progress_total:
                    row.progress_current = min(row.progress_total, row.progress_current + 1)
            elif kind == "slot":
                slots = json.loads(row.slots_json or "{}")
                slots[event["key"]] = event["slot"]
                row.slots_json = json.dumps(slots, ensure_ascii=False)
            elif kind == "message":
                db.add(
                    Message(
                        session_id=self.session_id,
                        role=event.get("role", "assistant"),
                        kind=event.get("kind", "info"),
                        content=event.get("content", ""),
                        meta_json=json.dumps(event.get("meta") or {}, ensure_ascii=False),
                    )
                )
            elif kind == "usage":
                row.llm_requests = (row.llm_requests or 0) + int(event.get("requests", 1))
                row.input_tokens = (row.input_tokens or 0) + int(event.get("input_tokens", 0))
                row.output_tokens = (row.output_tokens or 0) + int(event.get("output_tokens", 0))
                row.cache_read_tokens = (row.cache_read_tokens or 0) + int(event.get("cache_read_input_tokens", 0))
                row.cache_write_tokens = (row.cache_write_tokens or 0) + int(event.get("cache_creation_input_tokens", 0))
                row.cost_usd = (row.cost_usd or 0.0) + float(event.get("cost_usd", 0.0))
            elif kind == "document":  # AI 초안은 버전 기록에 새 버전으로 쌓는다(번호는 BackEnd 기준)
                record_ai_document(db, row, str(event.get("markdown") or ""), int(event.get("version") or 0))
            row.updated_at = utcnow()
            db.commit()
