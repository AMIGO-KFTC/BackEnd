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

if TYPE_CHECKING:
    from .container import Services

logger = logging.getLogger(__name__)

AgentCall = Callable[[Callable[[dict[str, Any]], None]], Any]
REANALYZE_STAGES = ("qna", "review")
INTERRUPTED = "서버가 다시 시작되면서 진행 중이던 AI 작업이 멈췄습니다. '다시 시도'를 누르면 멈춘 곳부터 이어서 진행합니다."


class SessionBusy(Exception):
    pass


class JobRunner:
    def __init__(self, services: Services, workers: int = 4) -> None:
        self.services = services
        self.pool = ThreadPoolExecutor(max_workers=max(2, workers), thread_name_prefix="amigo-job")
        self._active: set[str] = set()
        self._lock = threading.Lock()
        self._auto_lock = threading.Lock()

    # ------------------------------------------------------------------ 상태
    def is_busy(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._active

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
        kb, agent = self.services.kb(session_id), self.services.agent
        self.run_agent(session_id, "analyze", lambda h: agent.start(session_id, profile, kb, on_event=h), progress="자료를 분석하고 있어요.")

    def _agent_job(self, session_id: str, action: str, call: AgentCall) -> None:
        recorder = EventRecorder(self.services, session_id)
        try:
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
            row.document_md = snap["document"] or row.document_md
            row.document_version = snap["document_version"] or row.document_version
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
    def run_ingest(self, session_id: str, source_ids: list[str], *, auto_analyze: bool = False) -> None:
        if source_ids:
            self.pool.submit(self._ingest_job, session_id, source_ids, auto_analyze)

    def _ingest_job(self, session_id: str, source_ids: list[str], auto_analyze: bool = False) -> None:
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
        if ready_names:
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
            elif kind == "document":
                row.document_md = event.get("markdown", "")
                row.document_version = int(event.get("version", row.document_version))
            row.updated_at = utcnow()
            db.commit()
