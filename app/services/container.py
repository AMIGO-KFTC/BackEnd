"""서비스 컨테이너: DB, 파일 저장소, 지식베이스(RAG), 에이전트(AI), 작업 실행기를 한곳에서 만든다."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from amigo_agent import HandoverAgent
from amigo_rag import KnowledgeBase, RAGSettings, supported_extensions
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from ..models import Base
from .checkpointer import DBCheckpointSaver, import_sqlite_checkpoints
from .storage import FileStorage


class Services:
    def __init__(self, settings: Settings) -> None:
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

        self.allowed_extensions = supported_extensions()
        self.storage = FileStorage(settings.upload_dir, settings.max_upload_mb * 1024 * 1024, self.allowed_extensions)
        self.rag_settings = RAGSettings(data_dir=settings.rag_dir)
        # 에이전트 대화 상태(어느 단계·어느 질문에서 멈췄는지)도 같은 DB 에 저장한다. 예전 SQLite 파일이 있으면 한 번 옮겨 온다.
        import_sqlite_checkpoints(settings.checkpoint_path, self.SessionLocal)
        self.agent = HandoverAgent(checkpointer=DBCheckpointSaver(self.SessionLocal))
        self._kbs: dict[str, KnowledgeBase] = {}
        self._kb_lock = threading.Lock()

        from .runner import JobRunner  # 순환 import 방지

        self.runner = JobRunner(self, workers=settings.workers)
        self.runner.recover()

    @contextmanager
    def db(self) -> Iterator[Session]:
        session = self.SessionLocal()
        try:
            yield session
        finally:
            session.close()

    def kb(self, session_id: str) -> KnowledgeBase:
        """세션별 지식베이스(ChromaDB 컬렉션 kb_<세션ID>)."""
        with self._kb_lock:
            if session_id not in self._kbs:
                self._kbs[session_id] = KnowledgeBase(session_id, settings=self.rag_settings)
            return self._kbs[session_id]

    def drop_kb(self, session_id: str) -> None:
        self.kb(session_id).clear()
        with self._kb_lock:
            self._kbs.pop(session_id, None)

    def shutdown(self) -> None:
        self.runner.shutdown()
        self.engine.dispose()


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
                if default is not None:
                    ddl += f" DEFAULT {default!r}" if isinstance(default, str) else f" DEFAULT {default}"
                conn.execute(text(ddl))

