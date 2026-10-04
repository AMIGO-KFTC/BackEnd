"""백엔드 설정. 환경변수(AMIGO_ 접두어) 또는 BackEnd/.env 파일로 바꾼다."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AMIGO_", extra="ignore")

    data_dir: Path = Path("./data")
    database_url: str = ""  # 비우면 data_dir/app.db (SQLite). PostgreSQL 예: postgresql+psycopg://user:pw@host/db
    max_upload_mb: int = 50
    max_files_per_upload: int = 20
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    frontend_dist: str = ""  # 빌드된 FrontEnd/dist 경로를 주면 같은 서버에서 화면도 제공
    workers: int = 4

    @property
    def db_url(self) -> str:
        return self.database_url or f"sqlite:///{(self.data_dir / 'app.db').resolve()}"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def rag_dir(self) -> Path:
        return self.data_dir / "rag"

    @property
    def checkpoint_path(self) -> Path:
        return self.data_dir / "agent_checkpoints.sqlite"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


def load_env_file(path: Path) -> None:
    """.env 파일을 os.environ 에 반영한다(RAG·AI 모듈과 Anthropic SDK 도 환경변수를 읽으므로).

    이미 설정된 환경변수는 덮어쓰지 않는다.
    """
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@lru_cache
def get_settings() -> Settings:
    load_env_file(Path(__file__).resolve().parents[1] / ".env")
    return Settings()
