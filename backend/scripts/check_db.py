"""Check the Supabase database connection.

Usage (from backend/, venv active):  python -m scripts.check_db
Prints what it connected to. Never prints the password.
"""

import sys

from sqlalchemy.exc import SQLAlchemyError

from app.core.settings import get_settings
from app.storage.db import DatabaseConfigError, check_connection, create_db_engine


def main() -> int:
    settings = get_settings()
    print(
        f"Connecting to {settings.database_host or '<missing host>'}:{settings.database_port} "
        f"as {settings.resolved_database_user} ..."
    )
    try:
        report = check_connection(create_db_engine(settings))
    except DatabaseConfigError as exc:
        print(f"CONFIG ERROR: {exc}")
        return 2
    except SQLAlchemyError as exc:
        print(f"CONNECTION FAILED: {exc.__class__.__name__}: {exc.orig if hasattr(exc, 'orig') else exc}")
        return 1

    print("CONNECTED")
    print(f"  server   : {report.server_version}")
    print(f"  database : {report.database}")
    print(f"  user     : {report.user}")
    print(f"  ssl      : {report.ssl}")
    if not report.ssl:
        print("WARNING: connection is not encrypted")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
