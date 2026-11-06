"""Run backend tests against the local PostgreSQL from docker compose.

    python run_tests.py
    python run_tests.py -v tests/test_tasks.py

Reads DB_USER / DB_PASS / DB_PORT from ``../.env``, forces ``127.0.0.1``
(Docker Desktop listens on IPv4, Windows resolves localhost to IPv6 first)
and runs pytest with the current interpreter.

If ``TEST_DATABASE_URL`` is already set, it wins. The script checks the
connection first and prints an actionable error instead of hundreds of
identical pytest ERROR lines.

Messages are English on purpose: Windows PowerShell may use a non-UTF-8
code page and would fail to print Russian text.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent
DEFAULT_USER = "timing"
DEFAULT_PORT = "5432"
TEST_DB = "timingdb_test"


def read_env(path: Path) -> dict[str, str]:
    """Simple KEY=VALUE lines from .env; comments and blanks are skipped."""
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def url_from_env_file() -> str:
    env_file = ROOT.parent / ".env"
    if not env_file.exists():
        raise SystemExit(
            f"No {env_file}. Copy .env.example and set DB_* values."
        )
    cfg = read_env(env_file)
    password = cfg.get("DB_PASS")
    if not password:
        raise SystemExit("DB_PASS is not set in .env.")
    user = quote(cfg.get("DB_USER") or DEFAULT_USER, safe="")
    port = cfg.get("DB_PORT") or DEFAULT_PORT
    return (
        f"postgresql://{user}:{quote(password, safe='')}"
        f"@127.0.0.1:{port}/{TEST_DB}?connect_timeout=5"
    )


def mask(url: str) -> str:
    """Hide the password: postgresql://timing:***@127.0.0.1:5432/..."""
    return re.sub(r"(://[^:/@]+:)[^@]*@", r"\1***@", url)


def resolve_url() -> tuple[str, str]:
    """Returns (URL, source); the environment variable wins over .env."""
    from_env = os.environ.get("TEST_DATABASE_URL")
    if from_env:
        return from_env, "environment variable TEST_DATABASE_URL"
    return url_from_env_file(), ".env"


def check_connection(url: str) -> str | None:
    """Tries to connect; returns an error text or None."""
    try:
        import psycopg
    except ImportError:  # pytest will report the missing dependency
        return None
    try:
        with psycopg.connect(url, connect_timeout=5):
            return None
    except Exception as exc:  # noqa: BLE001
        return str(exc).strip()


def main() -> int:
    url, source = resolve_url()
    os.environ["TEST_DATABASE_URL"] = url
    print(f"TEST_DATABASE_URL ({source}): {mask(url)}", flush=True)

    error = check_connection(url)
    if error:
        lines = [
            "",
            "Could not connect to the test database:",
            f"  {error}",
            "",
            "Check:",
            "  1. docker compose up -d db",
            '  2. docker exec timing_db psql -U timing -d timingdb '
            '-c "CREATE DATABASE timingdb_test;"',
        ]
        if source.startswith("environment"):
            lines += [
                "  3. The variable may be left over from an earlier attempt "
                "with another password.",
                "     Remove it and rerun: "
                "Remove-Item Env:TEST_DATABASE_URL; python run_tests.py",
            ]
        else:
            lines.append(
                "  3. Compare DB_USER/DB_PASS in .env with docker compose config."
            )
        print("\n".join(lines), file=sys.stderr)
        return 1

    return subprocess.call([sys.executable, "-m", "pytest", *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
