"""Shared fixtures: disposable PostgreSQL databases with all migrations applied.

Set TEST_DATABASE_URL to a superuser URL of a throwaway server, e.g.
    docker run -d --name bettory-pg -e POSTGRES_PASSWORD=test -p 127.0.0.1:54329:5432 postgres:17
    TEST_DATABASE_URL=postgresql://postgres:test@127.0.0.1:54329/postgres pytest tests/integration
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

ADMIN_URL = os.environ.get("TEST_DATABASE_URL")
MIGRATIONS = Path(__file__).resolve().parents[3] / "supabase" / "migrations"


def _create_database() -> tuple[str, str]:
    assert ADMIN_URL, "TEST_DATABASE_URL not set"
    name = f"bettory_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'create database "{name}"')
    url = ADMIN_URL.rsplit("/", 1)[0] + f"/{name}"
    with psycopg.connect(url, autocommit=True) as conn:
        for sql_file in sorted(MIGRATIONS.glob("*.sql")):
            conn.execute(sql_file.read_text(encoding="utf-8"))
    return name, url


def _drop_database(name: str) -> None:
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'drop database "{name}" with (force)')


@pytest.fixture(scope="session")
def db_url() -> Iterator[str]:
    """One database for the session (tests roll back their own transactions)."""
    name, url = _create_database()
    yield url
    _drop_database(name)


@pytest.fixture
def fresh_db_url() -> Iterator[str]:
    """A brand-new database per test (for tests that commit)."""
    name, url = _create_database()
    yield url
    _drop_database(name)
