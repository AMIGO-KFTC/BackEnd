"""서비스 컨테이너: DB, 파일 저장소, 지식베이스(RAG), 에이전트(AI), 작업 실행기를 한곳에서 만든다."""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from amigo_agent import HandoverAgent
from amigo_rag import KnowledgeBase, RAGSettings, supported_extensions
from sqlalchemy import create_engine, event, func, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from ..models import Base, HandoverSession
from .accounts import ResetNotifier
from .checkpointer import DBCheckpointSaver, import_sqlite_checkpoints
from .documents import backfill_versions
from .mail import MockMailProvider
from .storage import FileStorage


class Services:
    def __init__(self, settings: Settings) -> None:
        self.mock_dir: Path | None = None
        if settings.mock_mode:  # 가상 응답 모드는 실제 DB·업로드 폴더를 건드리지 않도록 임시 폴더에서 새로 시작한다
            self.mock_dir = Path(tempfile.mkdtemp(prefix="amigo-mock-"))
            settings = settings.model_copy(update={"data_dir": self.mock_dir, "database_url": "", "expose_reset_token": True})
        self.settings = settings
        settings.data_dir.mkdir(parents=True, exist_ok=True)

        is_sqlite = settings.db_url.startswith("sqlite")
        self.engine = create_engine(
            settings.db_url,
            connect_args={"check_same_thread": False, "timeout": 30} if is_sqlite else {},
            pool_pre_ping=True,
        )
        if is_sqlite:

            @event.listens_for(self.engine, "connect")
            def _sqlite_pragmas(dbapi_conn, _record):  # 백그라운드 작업과 API 요청이 동시에 읽고 쓰도록 WAL
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA foreign_keys=ON")
                cur.execute("PRAGMA busy_timeout=30000")
                cur.close()

        Base.metadata.create_all(self.engine)
        _add_missing_columns(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False)
        backfill_versions(self.SessionLocal)
        self.reset_notifier = ResetNotifier(settings.password_reset_url, log_token=settings.expose_reset_token)

        self.allowed_extensions = supported_extensions()
        self.mail = MockMailProvider()  # 메일 조회 API(지금은 가상 메일함). 실제 메일 시스템을 붙이면 여기만 바꾼다
        self.storage = FileStorage(settings.upload_dir, settings.max_upload_mb * 1024 * 1024, self.allowed_extensions)
        self.rag_settings = RAGSettings(data_dir=settings.rag_dir)
        if settings.mock_mode:
            from ..mock.agent import MockAgent

            self.agent = MockAgent(delay=settings.mock_delay)
        else:
            # 에이전트 대화 상태(어느 단계·어느 질문에서 멈췄는지)도 같은 DB 에 저장한다. 예전 SQLite 파일이 있으면 한 번 옮겨 온다.
            import_sqlite_checkpoints(settings.checkpoint_path, self.SessionLocal)
            self.agent = HandoverAgent(checkpointer=DBCheckpointSaver(self.SessionLocal))
        self._kbs: dict[str, KnowledgeBase] = {}
        self._probe = None  # kb_state 용 Chroma 접근(지식베이스 객체를 세션마다 만들지 않도록)
        self._kb_lock = threading.Lock()

        from .runner import JobRunner  # 순환 import 방지

        self.runner = JobRunner(self, workers=settings.workers)
        self.runner.recover()
        if settings.reindex_on_start and not settings.mock_mode:  # 시작을 늦추지 않도록 백그라운드에서 점검
            self.runner.pool.submit(self.runner.check_knowledge_bases)
        if settings.mock_mode and settings.mock_seed:
            from ..mock.seed import seed_demo

            seed_demo(self)

    @contextmanager
    def db(self) -> Iterator[Session]:
        session = self.SessionLocal()
        try:
            yield session
        finally:
            session.close()

    def budget_exhausted(self) -> tuple[float, float] | None:
        """Claude 엔진이고 예산(AMIGO_LLM_BUDGET_USD)을 다 썼으면 (추정 사용액, 예산), 아니면 None."""
        budget = self.settings.llm_budget_usd
        if budget <= 0 or self.agent.describe().get("engine") != "claude":
            return None
        with self.db() as db:
            spent = round(float(db.scalar(select(func.coalesce(func.sum(HandoverSession.cost_usd), 0.0))) or 0.0), 4)
        return (spent, budget) if spent >= budget else None

    def health(self) -> dict:
        """연동 상태 점검: DB 연결, RAG 저장소(쓰기 권한·임베딩 방식), AI 엔진(모델·예산). 로그인 없이 부르므로 최소한만 알려 준다."""
        db_ok = True
        try:
            with self.db() as db:
                db.execute(text("SELECT 1"))
        except Exception:
            db_ok = False
        rag_dir = self.settings.rag_dir
        try:
            rag_dir.mkdir(parents=True, exist_ok=True)
            rag_ok = os.access(rag_dir, os.W_OK)
        except OSError:
            rag_ok = False
        try:
            budget_exhausted = self.budget_exhausted() is not None
        except Exception:  # DB 가 죽어도 점검 결과는 돌려준다
            budget_exhausted = False
        agent = self.agent.describe()
        checks = {
            "db": {"ok": db_ok},
            "rag": {"ok": rag_ok, "mock": self.settings.mock_mode, "embedding": "mock" if self.settings.mock_mode else self.rag_settings.embedding},
            "ai": {"ok": not budget_exhausted, "engine": agent.get("engine"), "model": agent.get("model"), "budget_exhausted": budget_exhausted},
        }
        return {"status": "ok" if db_ok and rag_ok else "degraded", "checks": checks}

    def kb_state(self, session_id: str) -> dict:
        """지식베이스 상태를 조각 원문을 읽지 않고 본다: state 는 missing(없음) · mismatch(임베딩 설정이 바뀜) · ok.

        RAG 의 공개 API 에 이 기능이 없어 ChromaStore 를 직접 쓴다(RAG 에 같은 기능이 생기면 교체).
        """
        if self.settings.mock_mode:
            ids = {s["source_id"] for s in self.kb(session_id).sources}
            return {"state": "ok" if ids else "missing", "source_ids": ids}
        if self._probe is None:
            from amigo_rag.embeddings import get_embedder
            from amigo_rag.store import ChromaStore

            self._probe = ChromaStore(self.rag_settings.chroma_dir, get_embedder(self.rag_settings.embedding).name)
        col = self._probe.collection(session_id, create=False)
        if col is None:
            return {"state": "missing", "source_ids": set()}
        stored = (col.metadata or {}).get("embedder")
        if stored and stored != self._probe.embedder_name:
            return {"state": "mismatch", "source_ids": set()}
        metas = col.get(include=["metadatas"])["metadatas"] or []
        ids = {str(m.get("source_id")) for m in metas if m}
        return {"state": "ok" if ids else "missing", "source_ids": ids}

    def kb(self, session_id: str) -> KnowledgeBase:
        """세션별 지식베이스(ChromaDB 컬렉션 kb_<세션ID>). 가상 응답 모드면 파싱하지 않는 가짜 지식베이스."""
        with self._kb_lock:
            if session_id not in self._kbs:
                if self.settings.mock_mode:
                    from ..mock.kb import MockKnowledgeBase

                    self._kbs[session_id] = MockKnowledgeBase(session_id, delay=self.settings.mock_delay)
                else:
                    self._kbs[session_id] = KnowledgeBase(session_id, settings=self.rag_settings)
            return self._kbs[session_id]

    def drop_kb(self, session_id: str) -> None:
        self.kb(session_id).clear()
        with self._kb_lock:
            self._kbs.pop(session_id, None)

    def shutdown(self) -> None:
        self.runner.shutdown()
        self.engine.dispose()
        if self.mock_dir is not None:
            shutil.rmtree(self.mock_dir, ignore_errors=True)


def _add_missing_columns(engine) -> None:
    """예전 버전으로 만든 DB 에 새 컬럼(예: 사용량)을 추가한다. 데이터는 그대로 두는 가벼운 마이그레이션."""
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                default = column.default.arg if column.default is not None and not callable(column.default.arg) else None
                ddl = f"ALTER TABLE {table.name} ADD COLUMN {column.name} {column.type.compile(engine.dialect)}"
                if isinstance(default, bool):
                    ddl += " DEFAULT TRUE" if default else " DEFAULT FALSE"
                elif default is not None:
                    ddl += f" DEFAULT {default!r}" if isinstance(default, str) else f" DEFAULT {default}"
                conn.execute(text(ddl))
            for index in table.indexes:  # 새 컬럼에 건 인덱스(예: users.email)
                index.create(conn, checkfirst=True)

