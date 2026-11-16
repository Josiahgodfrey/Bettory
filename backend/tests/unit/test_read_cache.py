"""ReadCache: keys carry as_of, so a read made for one moment is never served to another."""

from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import Connection

from app.storage.point_in_time import PointInTimeReader, ReadCache

T1, T2 = datetime(2026, 1, 1, 12, tzinfo=UTC), datetime(2026, 1, 2, 12, tzinfo=UTC)


def test_same_moment_shares_other_moment_does_not() -> None:
    cache, calls = ReadCache(), []
    conn = cast(Connection, None)

    def load(tag: str) -> Any:
        calls.append(tag)
        return (tag,)

    a, b, c = PointInTimeReader(conn, T1, cache), PointInTimeReader(conn, T1, cache), PointInTimeReader(conn, T2, cache)
    assert a.memo("x", (1,), lambda: load("a")) == ("a",)
    assert b.memo("x", (1,), lambda: load("b")) == ("a",)                # same as_of: shared
    assert c.memo("x", (1,), lambda: load("c")) == ("c",)                # other as_of: loaded again
    assert a.memo("x", (2,), lambda: load("d")) == ("d",)                # other arguments: loaded again
    assert calls == ["a", "c", "d"] and (cache.hits, cache.misses, len(cache)) == (1, 3, 3)


def test_without_a_shared_cache_each_reader_has_its_own() -> None:
    conn = cast(Connection, None)
    a, b = PointInTimeReader(conn, T1), PointInTimeReader(conn, T1)
    a.memo("x", (), lambda: 1)
    assert b.memo("x", (), lambda: 2) == 2 and a.cache is not b.cache
