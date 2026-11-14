"""Database connection to Supabase PostgreSQL through the Supabase connection pooler.

- Session mode (port 5432): long-running worker; normal prepared statements.
- Transaction mode (port 6543): API; prepared statements disabled (required by the pooler).

The URL is built from separate settings so that special characters in the password are
always encoded correctly. Connections always use TLS (sslmode=require or stricter).
"""

from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy import URL, Engine, create_engine, text

from app.core.settings import Settings, get_settings

TRANSACTION_MODE_PORT = 6543


class DatabaseConfigError(RuntimeError):
    """Raised when required database settings are missing."""


def build_url(settings: Settings) -> URL:
    missing = [
        name
        for name, value in (
            ("DATABASE_HOST", settings.database_host),
            ("DATABASE_PASSWORD", settings.database_password.get_secret_value()),
        )
        if not value
    ]
    if missing:
        raise DatabaseConfigError(f"Missing database settings: {', '.join(missing)}")

    return URL.create(
        drivername="postgresql+psycopg",
        username=settings.resolved_database_user,
        password=settings.database_password.get_secret_value(),
        host=settings.database_host,
        port=settings.database_port,
        database=settings.database_name,
        query={"sslmode": settings.database_sslmode},
    )


def create_db_engine(settings: Settings) -> Engine:
    connect_args: dict[str, object] = {
        "options": f"-c statement_timeout={settings.database_statement_timeout_ms}",
        "connect_timeout": 10,
        "application_name": f"bettory-{settings.environment}",
    }
    if settings.database_port == TRANSACTION_MODE_PORT:
        # The transaction-mode pooler cannot keep prepared statements between transactions.
        connect_args["prepare_threshold"] = None

    return create_engine(
        build_url(settings),
        connect_args=connect_args,
        pool_size=5,
        max_overflow=2,
        pool_pre_ping=True,
        pool_recycle=1800,
    )


@lru_cache
def get_engine() -> Engine:
    return create_db_engine(get_settings())


@dataclass(frozen=True)
class ConnectionReport:
    server_version: str
    database: str
    user: str
    ssl: bool


def check_connection(engine: Engine) -> ConnectionReport:
    """Run a read-only probe and report what we are connected to."""
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "select version(), current_database(), current_user, "
                "coalesce((select ssl from pg_stat_ssl where pid = pg_backend_pid()), false)"
            )
        ).one()
    return ConnectionReport(
        server_version=str(row[0]).split(",")[0],
        database=str(row[1]),
        user=str(row[2]),
        ssl=bool(row[3]),
    )
