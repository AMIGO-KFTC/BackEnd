"""백엔드 설정. 환경변수(AMIGO_ 접두어) 또는 BackEnd/.env 파일로 바꾼다."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
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
    session_ttl_hours: int = 12  # 로그인 세션 유효 시간
    allow_signup: bool = True  # 회원가입 API 허용(사내 계정을 미리 넣어 두는 경우 false)
    cookie_secure: bool = False  # HTTPS 로 서비스할 때 true(로그인 쿠키에 Secure 속성)
    auto_analyze: bool = True  # 단위업무 파일 적재가 끝나면 AI 분석을 자동으로 시작
    llm_budget_usd: float = 0.0  # Claude API 추정 비용 상한(USD, 모든 세션 합계). 0 이면 제한 없음

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
        """예전 버전이 에이전트 대화 상태를 따로 저장하던 SQLite 파일. 있으면 시작할 때 DB 로 옮긴다."""
        return self.data_dir / "agent_checkpoints.sqlite"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


def load_env_file(path: Path) -> None:
    """.env 파일을 os.environ 에 반영한다(RAG·AI 모듈과 Anthropic SDK 도 환경변수를 읽으므로).

    이미 설정된 환경변수는 덮어쓰지 않는다. 따옴표·줄 끝 주석(`KEY=value  # 설명`)은 python-dotenv 규칙을 따른다.
    """
    if path.is_file():
        load_dotenv(path, override=False, encoding="utf-8-sig")  # -sig: 메모장이 붙인 BOM 무시


@lru_cache
def get_settings() -> Settings:
    load_env_file(Path(__file__).resolve().parents[1] / ".env")
    return Settings()
