"""업로드 파일을 임시 저장소(data/uploads/<세션>/)에 안전하게 저장한다.

- 사용자가 보낸 파일명은 화면 표시용으로만 쓰고, 디스크에는 '<source_id><확장자>' 로 저장한다(경로 조작 차단).
- 확장자 허용 목록(RAG 가 읽을 수 있는 형식)과 크기 제한을 스트리밍 저장 중에 검사한다.
"""

from __future__ import annotations

import re
import shutil
import unicodedata
from pathlib import Path, PurePosixPath, PureWindowsPath

from fastapi import UploadFile

CHUNK = 1024 * 1024
_UNSAFE = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]+')


class UploadRejected(Exception):
    """사용자에게 보여줄 거절 사유를 담는다."""


def display_name(filename: str | None) -> str:
    """브라우저가 보낸 파일명에서 경로를 떼고 위험한 문자를 정리한다(한글은 유지)."""
    raw = unicodedata.normalize("NFC", filename or "")
    name = PureWindowsPath(PurePosixPath(raw).name).name  # a/b.pdf, C:\\x\\b.pdf 모두 처리
    name = _UNSAFE.sub("_", name).strip(" .")
    if not name:
        return "file"
    if len(name) > 150:
        stem, dot, ext = name.rpartition(".")
        name = (stem[: 150 - len(ext) - 1] + "." + ext) if dot else name[:150]
    return name


def extension_of(name: str) -> str:
    return PurePosixPath(name.lower()).suffix


class FileStorage:
    def __init__(self, root: Path, max_bytes: int, allowed_extensions: set[str]) -> None:
        self.root = root
        self.max_bytes = max_bytes
        self.allowed = allowed_extensions
        self.root.mkdir(parents=True, exist_ok=True)

    def session_dir(self, session_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{8,32}", session_id):
            raise ValueError("invalid session id")
        return self.root / session_id

    async def save(self, session_id: str, source_id: str, upload: UploadFile) -> tuple[Path, str, int]:
        """업로드를 저장하고 (경로, 표시 이름, 크기) 를 돌려준다. 거절되면 UploadRejected."""
        name = display_name(upload.filename)
        ext = extension_of(name)
        if ext not in self.allowed:
            raise UploadRejected(f"지원하지 않는 형식입니다({ext or '확장자 없음'}).")
        target_dir = self.session_dir(session_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{source_id}{ext}"
        size = 0
        try:
            with open(target, "wb") as fh:
                while True:
                    chunk = await upload.read(CHUNK)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise UploadRejected(f"파일이 너무 큽니다(최대 {self.max_bytes // (1024 * 1024)}MB).")
                    fh.write(chunk)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        if size == 0:
            target.unlink(missing_ok=True)
            raise UploadRejected("빈 파일입니다.")
        return target, name, size

    def remove(self, path: str) -> None:
        if path:
            candidate = Path(path)
            if self.root.resolve() in candidate.resolve().parents:
                candidate.unlink(missing_ok=True)

    def remove_session(self, session_id: str) -> None:
        shutil.rmtree(self.session_dir(session_id), ignore_errors=True)
