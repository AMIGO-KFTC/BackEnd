import os
import sys
import time
from pathlib import Path

import pytest

os.environ["AMIGO_LLM_MODE"] = "offline"  # 테스트는 API 키 없이 규칙 기반 엔진으로

WORKSPACE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORKSPACE / "RAG" / "samples"))

from fastapi.testclient import TestClient  # noqa: E402

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

PROFILE = {
    "owner_name": "김민수",
    "organization": "디지털전략부 웹서비스팀",
    "position": "과장",
    "duties": "기관 홈페이지 운영 및 웹 접근성 관리",
    "successor": "이서연 대리",
    "handover_date": "2026-10-15",
    "task_name": "홈페이지 운영",
    "mode": "reassignment",
}


@pytest.fixture(scope="session")
def sample_dir(tmp_path_factory) -> Path:
    import make_samples

    out = tmp_path_factory.mktemp("samples")
    make_samples.make_all(out)
    return out


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path / "data", max_upload_mb=5, workers=4)


@pytest.fixture()
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


def wait_for(client, session_id: str, predicate, timeout: float = 60.0) -> dict:
    """백그라운드 작업이 끝날 때까지 /state 를 폴링한다."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = client.get(f"/api/sessions/{session_id}/state").json()
        if predicate(last["session"]):
            return last
        time.sleep(0.1)
    raise AssertionError(f"timeout: {last and last['session']['status']} / {last and last['session']['stage']}")


def idle(session: dict) -> bool:
    return session["status"] in ("waiting", "idle", "error") and all(s["status"] in ("ready", "failed") for s in session["sources"])


def upload(client, session_id: str, paths: list[Path]):
    files = [("files", (p.name, p.read_bytes(), "application/octet-stream")) for p in paths]
    return client.post(f"/api/sessions/{session_id}/upload", files=files)
