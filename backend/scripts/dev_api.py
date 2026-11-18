"""Start the API for frontend development, safely offline.

    venv\\Scripts\\python scripts\\dev_api.py            (from backend/)

- database: the local copy (LOCAL_DATABASE_URL in BETTORY/.env), not Supabase
- login: the loopback bypass (ENVIRONMENT=local, AUTH_LOCAL_BYPASS=true; answers this machine only)
- OFF: Telegram, API-Football, football-data.org, Supabase Storage — nothing can be sent or spent

Why a script: on Windows, setting an environment variable to "" (Python os.environ, PowerShell $env:X="") DELETES
it, so a child process would read the real value from .env again. Here the environment is passed to the server
explicitly (empty values survive), and the settings are checked before it starts. Secrets are never printed.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

OFF = {
    "TELEGRAM_BOT_TOKEN": "",
    "TELEGRAM_CHAT_ID": "",
    "API_FOOTBALL_KEY": "",
    "FOOTBALL_DATA_ORG_KEY": "",
    "SUPABASE_SERVICE_ROLE_KEY": "",
    "DATABASE_HOST": "",
    "DATABASE_PASSWORD": "",
}


def local_database_url() -> str:
    for line in (BACKEND.parent / ".env").read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "LOCAL_DATABASE_URL":
            return value.strip().strip('"').strip("'")
    raise SystemExit("LOCAL_DATABASE_URL is missing in BETTORY/.env")


def main() -> int:
    env = {
        **os.environ,
        **OFF,
        "ENVIRONMENT": "local",
        "AUTH_LOCAL_BYPASS": "true",
        "DATABASE_URL": local_database_url(),
        "RAW_STORE": "local",
        "CORS_ORIGINS": '["http://localhost:5173","http://127.0.0.1:5173"]',
    }
    # Check with the exact environment the server will get (os.environ keeps the empty values in this process).
    os.environ.clear()
    os.environ.update(env)
    from app.core.settings import Settings
    from app.notifications import outbox

    s = Settings()
    problems = [name for name, live in (
        ("Telegram", outbox.configured(s)),
        ("API-Football key", bool(s.api_football_key.get_secret_value())),
        ("football-data.org key", bool(s.football_data_org_key.get_secret_value())),
        ("Supabase database", bool(s.database_host)),
    ) if live]
    if problems:
        raise SystemExit(f"refusing to start: still configured: {', '.join(problems)}")
    print("dev API: local database, login bypass, Telegram and data APIs OFF -> http://127.0.0.1:8000")
    return subprocess.call([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"],
                           cwd=BACKEND, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
