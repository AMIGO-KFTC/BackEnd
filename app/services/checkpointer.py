"""AI 에이전트(LangGraph) 체크포인트를 백엔드 DB(SQLite 또는 PostgreSQL)에 저장한다.

에이전트는 질문을 던진 뒤 사용자 답변을 기다리는 지점에서 멈추는데, 그 지점(단계·현재 질문·확인 대기 중인 답변 등)이
체크포인트다. 대화 메시지(messages)·세션 상태(sessions)와 같은 DB 에 두어, DB 를 바꾸거나 서버를 옮겨도
사용자가 멈춘 곳에서 대화를 이어 갈 수 있게 한다.

저장 형식은 langgraph-checkpoint-sqlite(SqliteSaver)와 같다. 예전 버전이 쓰던 data/agent_checkpoints.sqlite 는
import_sqlite_checkpoints() 로 한 번 옮겨 온다.
"""

from __future__ import annotations

import json
import logging
import random
import sqlite3
from collections.abc import Callable, Iterator, Sequence
from contextlib import closing
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..models import AgentCheckpoint, AgentCheckpointWrite

logger = logging.getLogger(__name__)


class DBCheckpointSaver(BaseCheckpointSaver[str]):
    """SQLAlchemy 로 체크포인트를 읽고 쓰는 LangGraph 체크포인터(동기 API 만 지원)."""

    def __init__(self, session_factory: Callable[[], Session], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._session = session_factory

    # ------------------------------------------------------------------ 읽기
    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        conf = config["configurable"]
        query = select(AgentCheckpoint).where(
            AgentCheckpoint.thread_id == str(conf["thread_id"]),
            AgentCheckpoint.checkpoint_ns == conf.get("checkpoint_ns", ""),
        )
        if checkpoint_id := get_checkpoint_id(config):
            query = query.where(AgentCheckpoint.checkpoint_id == checkpoint_id)
        else:
            query = query.order_by(AgentCheckpoint.checkpoint_id.desc()).limit(1)
        with self._session() as db:
            row = db.scalars(query).first()
            return self._to_tuple(db, row) if row is not None else None

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        query = select(AgentCheckpoint)
        if config is not None:
            conf = config["configurable"]
            query = query.where(AgentCheckpoint.thread_id == str(conf["thread_id"]))
            if "checkpoint_ns" in conf:
                query = query.where(AgentCheckpoint.checkpoint_ns == conf["checkpoint_ns"])
        if before is not None and (before_id := get_checkpoint_id(before)):
            query = query.where(AgentCheckpoint.checkpoint_id < before_id)
        query = query.order_by(AgentCheckpoint.checkpoint_id.desc())
        found: list[CheckpointTuple] = []
        with self._session() as db:
            for row in db.scalars(query):
                metadata = _load_metadata(row.meta_json)
                if filter and any(metadata.get(k) != v for k, v in filter.items()):
                    continue
                found.append(self._to_tuple(db, row))
                if limit is not None and len(found) >= limit:
                    break
        yield from found

    def _to_tuple(self, db: Session, row: AgentCheckpoint) -> CheckpointTuple:
        writes = db.execute(
            select(AgentCheckpointWrite.task_id, AgentCheckpointWrite.channel, AgentCheckpointWrite.type, AgentCheckpointWrite.value)
            .where(
                AgentCheckpointWrite.thread_id == row.thread_id,
                AgentCheckpointWrite.checkpoint_ns == row.checkpoint_ns,
                AgentCheckpointWrite.checkpoint_id == row.checkpoint_id,
            )
            .order_by(AgentCheckpointWrite.task_id, AgentCheckpointWrite.idx)
        ).all()
        ids = {"thread_id": row.thread_id, "checkpoint_ns": row.checkpoint_ns}
        return CheckpointTuple(
            config={"configurable": {**ids, "checkpoint_id": row.checkpoint_id}},
            checkpoint=self.serde.loads_typed((row.type, row.checkpoint)),
            metadata=_load_metadata(row.meta_json),
            parent_config={"configurable": {**ids, "checkpoint_id": row.parent_checkpoint_id}} if row.parent_checkpoint_id else None,
            pending_writes=[(task_id, channel, self.serde.loads_typed((type_, value))) for task_id, channel, type_, value in writes],
        )

    # ------------------------------------------------------------------ 쓰기
    def put(self, config: RunnableConfig, checkpoint: Checkpoint, metadata: CheckpointMetadata, new_versions: ChannelVersions) -> RunnableConfig:
        conf = config["configurable"]
        thread_id, checkpoint_ns = str(conf["thread_id"]), conf.get("checkpoint_ns", "")
        type_, blob = self.serde.dumps_typed(checkpoint)
        with self._session() as db:
            db.merge(
                AgentCheckpoint(
                    thread_id=thread_id,
                    checkpoint_ns=checkpoint_ns,
                    checkpoint_id=checkpoint["id"],
                    parent_checkpoint_id=conf.get("checkpoint_id"),
                    type=type_,
                    checkpoint=blob,
                    meta_json=json.dumps(get_checkpoint_metadata(config, metadata), ensure_ascii=False),
                )
            )
            db.commit()
        return {"configurable": {"thread_id": thread_id, "checkpoint_ns": checkpoint_ns, "checkpoint_id": checkpoint["id"]}}

    def put_writes(self, config: RunnableConfig, writes: Sequence[tuple[str, Any]], task_id: str, task_path: str = "") -> None:
        conf = config["configurable"]
        key = {"thread_id": str(conf["thread_id"]), "checkpoint_ns": str(conf.get("checkpoint_ns", "")), "checkpoint_id": str(conf["checkpoint_id"])}
        # 오류·중단 같은 특수 채널은 덮어쓰고(INSERT OR REPLACE), 일반 결과는 처음 것만 남긴다(INSERT OR IGNORE) — SqliteSaver 와 같은 규칙
        replace = all(channel in WRITES_IDX_MAP for channel, _ in writes)
        with self._session() as db:
            for idx, (channel, value) in enumerate(writes):
                pk = {**key, "task_id": task_id, "idx": WRITES_IDX_MAP.get(channel, idx)}
                if not replace and db.get(AgentCheckpointWrite, pk) is not None:
                    continue
                type_, blob = self.serde.dumps_typed(value)
                db.merge(AgentCheckpointWrite(**pk, channel=channel, type=type_, value=blob))
            db.commit()

    def delete_thread(self, thread_id: str) -> None:
        with self._session() as db:
            db.execute(delete(AgentCheckpointWrite).where(AgentCheckpointWrite.thread_id == str(thread_id)))
            db.execute(delete(AgentCheckpoint).where(AgentCheckpoint.thread_id == str(thread_id)))
            db.commit()

    def get_next_version(self, current: str | None, channel: None) -> str:
        """SqliteSaver 와 같은 버전 형식(옮겨 온 체크포인트와 이어지도록)."""
        if current is None:
            current_v = 0
        elif isinstance(current, int):
            current_v = current
        else:
            current_v = int(current.split(".")[0])
        return f"{current_v + 1:032}.{random.random():016}"


def _load_metadata(raw: str | None) -> dict[str, Any]:
    return json.loads(raw) if raw else {}


def import_sqlite_checkpoints(path: Path, session_factory: Callable[[], Session]) -> int:
    """예전 버전의 SQLite 체크포인트 파일을 DB 로 옮기고 파일 이름을 *.imported 로 바꾼다. 옮긴 체크포인트 수를 돌려준다.

    DB 에 이미 있는 대화(thread)는 건너뛴다. 파일이 없거나 테이블이 없으면 아무것도 하지 않는다.
    """
    if not path.is_file():
        return 0
    with closing(sqlite3.connect(str(path))) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"checkpoints", "writes"} <= tables:
            return 0
        checkpoints = conn.execute(
            "SELECT thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, type, checkpoint, metadata FROM checkpoints"
        ).fetchall()
        writes = conn.execute("SELECT thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, value FROM writes").fetchall()
    with session_factory() as db:
        existing = set(db.scalars(select(AgentCheckpoint.thread_id).group_by(AgentCheckpoint.thread_id)))
        imported = 0
        for thread_id, ns, cid, parent, type_, blob, metadata in checkpoints:
            if thread_id in existing:
                continue
            meta = metadata.decode("utf-8", "ignore") if isinstance(metadata, bytes) else (metadata or "{}")
            db.add(AgentCheckpoint(thread_id=thread_id, checkpoint_ns=ns, checkpoint_id=cid, parent_checkpoint_id=parent, type=type_, checkpoint=blob, meta_json=meta))
            imported += 1
        for thread_id, ns, cid, task_id, idx, channel, type_, value in writes:
            if thread_id in existing:
                continue
            db.add(AgentCheckpointWrite(thread_id=thread_id, checkpoint_ns=ns, checkpoint_id=cid, task_id=task_id, idx=idx, channel=channel, type=type_, value=value))
        db.commit()
        total = db.scalar(select(func.count()).select_from(AgentCheckpoint))
    path.rename(path.with_name(path.name + ".imported"))
    for suffix in ("-wal", "-shm"):
        path.with_name(path.name + suffix).unlink(missing_ok=True)
    logger.info("예전 체크포인트 %d건을 DB 로 옮겼습니다(DB 전체 %d건): %s", imported, total, path)
    return imported
