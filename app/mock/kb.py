"""가상 지식베이스: 파일을 실제로 파싱하지 않고 '읽은 것처럼' 결과만 돌려준다.

파일 이름에 '실패' 또는 'fail' 이 들어 있으면 처리 실패(ParseError)를 흉내 내서 화면의 실패 표시를 확인할 수 있다.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

from amigo_rag import ParseError
from amigo_rag.models import IngestResult, SearchResult


class MockKnowledgeBase:
    def __init__(self, kb_id: str, delay: float = 0.0) -> None:
        self.kb_id = kb_id
        self.delay = delay
        self._sources: dict[str, dict[str, Any]] = {}
        self._chunks: dict[str, list[SearchResult]] = {}  # 적재로 만든 조각
        self._extra: dict[str, list[SearchResult]] = {}  # 가상 AI 가 근거로 넣은 조각(다시 적재해도 유지)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ 적재
    def add_file(self, path: str | Path, *, source_id: str, display_name: str = "", metadata: dict | None = None) -> IngestResult:
        name = display_name or Path(path).name
        time.sleep(self.delay)
        if "실패" in name or "fail" in name.lower():
            raise ParseError(f"(가상 응답) '{name}' 에서 텍스트를 추출하지 못했습니다.")
        size = Path(path).stat().st_size if path and Path(path).is_file() else 4000
        return self._register(source_id, name, PurePosixPath(name.lower()).suffix.lstrip(".") or "file", size)

    def add_url(self, url: str, *, source_id: str, link_type: str | None = None, metadata: dict | None = None) -> IngestResult:
        time.sleep(self.delay)
        parsed = urlparse(url)
        tail = PurePosixPath(parsed.path).name or parsed.netloc
        label = {"confluence": "컨플루언스", "nanumi": "나누미"}.get(link_type or "", "웹")
        return self._register(source_id, f"{label} · {tail}"[:300], link_type or "web", 6000)

    def _register(self, source_id: str, name: str, source_type: str, size: int) -> IngestResult:
        count = max(1, min(40, size // 1500 + 2))
        with self._lock:
            self._sources[source_id] = {"source_id": source_id, "name": name, "type": source_type, "chunk_count": count}
            self._chunks[source_id] = [
                self._chunk(source_id, name, i, f"(가상 응답) '{name}' 의 {i + 1}번째 조각입니다. 실제 모드에서는 자료에서 읽은 원문이 들어갑니다.")
                for i in range(count)
            ]
        return IngestResult(source_id=source_id, source_name=name, source_type=source_type, chunk_count=count, char_count=size)

    def add_chunk(self, source_id: str, text: str) -> str:
        """가상 AI 가 근거로 쓸 조각을 넣고 chunk_id 를 돌려준다(같은 내용이면 기존 조각을 다시 쓴다)."""
        with self._lock:
            extra = self._extra.setdefault(source_id, [])
            existing = next((c for c in extra if c.text == text), None)
            if existing is not None:
                return existing.chunk_id
            name = self._sources.get(source_id, {}).get("name", "등록 자료")
            chunk = self._chunk(source_id, name, 900 + len(extra), text)
            extra.append(chunk)
            return chunk.chunk_id

    @staticmethod
    def _chunk(source_id: str, name: str, index: int, text: str) -> SearchResult:
        meta = {"source_id": source_id, "source_name": name, "file_name": name, "chunk_index": index}
        if name.lower().endswith(".pdf"):
            meta["page"] = index // 2 + 1
        return SearchResult(chunk_id=f"{source_id}-{index:03d}", text=text, score=0.0, metadata=meta)

    # ------------------------------------------------------------------ 조회·삭제
    @property
    def sources(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._sources.values())

    def list_sources(self) -> list[dict[str, Any]]:
        return self.sources

    def search(self, query: str, top_k: int = 5, **kwargs: Any) -> list[Any]:
        return []

    def get_chunks(self, source_id: str | None = None, limit: int | None = None) -> list[SearchResult]:
        with self._lock:
            ids = [source_id] if source_id else list(dict.fromkeys([*self._chunks, *self._extra]))
            rows = [c for sid in ids for c in [*self._chunks.get(sid, []), *self._extra.get(sid, [])]]
        return rows[:limit] if limit else rows

    def stats(self) -> dict[str, Any]:
        return {"kb_id": self.kb_id, "chunk_count": len(self.get_chunks()), "source_count": len(self.sources), "embedder": "mock"}

    def delete_source(self, source_id: str) -> int:
        with self._lock:
            self._chunks.pop(source_id, None)
            self._extra.pop(source_id, None)
            return 1 if self._sources.pop(source_id, None) else 0

    def clear(self) -> None:
        with self._lock:
            self._sources.clear()
            self._chunks.clear()
            self._extra.clear()
