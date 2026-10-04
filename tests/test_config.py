import os

from app.config import load_env_file

KEYS = ("AMIGO_T_PLAIN", "AMIGO_T_COMMENT", "AMIGO_T_QUOTED", "AMIGO_T_EMPTY", "AMIGO_T_DISABLED", "AMIGO_T_KEEP")


def test_env_file_follows_dotenv_rules(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "﻿# 설명 줄\n"
        "AMIGO_T_PLAIN=./data\n"
        "AMIGO_T_COMMENT=offline        # auto | claude | offline\n"
        "AMIGO_T_QUOTED='값 # 주석 아님'\n"
        "AMIGO_T_EMPTY=\n"
        "# AMIGO_T_DISABLED=1\n"
        "AMIGO_T_KEEP=from-file\n",
        encoding="utf-8",
    )
    os.environ["AMIGO_T_KEEP"] = "from-shell"
    try:
        load_env_file(env)
        assert os.environ["AMIGO_T_PLAIN"] == "./data"
        assert os.environ["AMIGO_T_COMMENT"] == "offline"
        assert os.environ["AMIGO_T_QUOTED"] == "값 # 주석 아님"
        assert os.environ["AMIGO_T_EMPTY"] == ""
        assert "AMIGO_T_DISABLED" not in os.environ
        assert os.environ["AMIGO_T_KEEP"] == "from-shell"  # 이미 설정된 환경변수는 그대로
    finally:
        for key in KEYS:
            os.environ.pop(key, None)


def test_env_example_has_clean_values(tmp_path):
    """.env.example 을 그대로 복사해 써도 값에 주석·공백이 섞이지 않아야 한다."""
    example = os.path.join(os.path.dirname(__file__), "..", ".env.example")
    keys = []
    with open(example, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                assert "#" not in value and value == value.strip(), line
                keys.append(key)
    assert "ANTHROPIC_API_KEY" in keys and "AMIGO_DATA_DIR" in keys
