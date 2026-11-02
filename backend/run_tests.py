"""Прогон backend-тестов против локального PostgreSQL из docker compose.

    python run_tests.py
    python run_tests.py -v tests/test_tasks.py

Берёт DB_USER / DB_PASS / DB_PORT из ``../.env``, подставляет ``127.0.0.1``
(Docker Desktop слушает IPv4, а Windows резолвит ``localhost`` в IPv6 первым)
и запускает pytest текущим интерпретатором. Если ``TEST_DATABASE_URL`` уже
задан, используется он.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent
DEFAULT_USER = "vector"
DEFAULT_PORT = "5432"
TEST_DB = "vectordb_test"


def read_env(path: Path) -> dict[str, str]:
    """Простые KEY=VALUE из .env; комментарии и пустые строки пропускаются."""
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def build_url() -> str:
    env_file = ROOT.parent / ".env"
    if not env_file.exists():
        raise SystemExit(
            f"Нет {env_file}. Скопируйте .env.example и заполните DB_*."
        )
    cfg = read_env(env_file)
    password = cfg.get("DB_PASS")
    if not password:
        raise SystemExit("В .env не задан DB_PASS.")
    user = quote(cfg.get("DB_USER") or DEFAULT_USER, safe="")
    port = cfg.get("DB_PORT") or DEFAULT_PORT
    return (
        f"postgresql://{user}:{quote(password, safe='')}"
        f"@127.0.0.1:{port}/{TEST_DB}?connect_timeout=5"
    )


def main() -> int:
    if not os.environ.get("TEST_DATABASE_URL"):
        os.environ["TEST_DATABASE_URL"] = build_url()
    return subprocess.call([sys.executable, "-m", "pytest", *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
