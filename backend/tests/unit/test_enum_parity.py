"""Python enums must match the PostgreSQL enums in the migrations exactly (same values, same order)."""

import re
from pathlib import Path

import pytest

from app.domain.enums import DB_ENUMS

MIGRATIONS = Path(__file__).resolve().parents[3] / "supabase" / "migrations"
ENUM_RE = re.compile(r"create type\s+([\w.]+)\s+as enum\s*\(([^)]*)\)", re.IGNORECASE)
ADD_VALUE_RE = re.compile(r"alter type\s+([\w.]+)\s+add value\s+(?:if not exists\s+)?'([^']*)'", re.IGNORECASE)


def _db_enums() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for sql_file in sorted(MIGRATIONS.glob("*.sql")):
        for name, body in ENUM_RE.findall(sql_file.read_text(encoding="utf-8")):
            found[name.lower()] = re.findall(r"'([^']*)'", body)
        for name, value in ADD_VALUE_RE.findall(sql_file.read_text(encoding="utf-8")):  # appended in file order
            if value not in found[name.lower()]:
                found[name.lower()].append(value)
    return found


DB = _db_enums()


@pytest.mark.parametrize("py_enum,db_name", list(DB_ENUMS.items()), ids=lambda x: getattr(x, "__name__", x))
def test_enum_matches_database(py_enum: type, db_name: str) -> None:
    assert db_name in DB, f"{db_name} not found in migrations"
    assert [m.value for m in py_enum] == DB[db_name]  # type: ignore[attr-defined]


def test_every_database_enum_is_mirrored() -> None:
    assert set(DB) == set(DB_ENUMS.values())
