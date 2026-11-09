"""D1 request shape. The HTTP call is the in-memory stand-in, not Cloudflare."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

from football_pipeline.d1 import SNAPSHOT_BATCH, D1Client, ShotRecord
from football_pipeline.snapshot import SnapshotRow
from tests.fakes import SqliteD1

UTC = timezone.utc


def test_queries_are_parameterized_posts_under_the_bind_limit():
    fake = SqliteD1()
    client = D1Client("acct-1", "db-1", "cf-token", transport=fake)
    client.apply_schema()
    client.apply_schema()
    hostile = "'); DROP TABLE snapshots;--"
    rows = [
        _row(index, hostile if index == 0 else f"book-{index}")
        for index in range(SNAPSHOT_BATCH + 1)
    ]
    client.insert_snapshots(rows)
    client.insert_shot(
        ShotRecord("fixed:2026:11:tue", "fixed", "2026-11-15T18:00:00Z", None, 80, "skipped")
    )
    stored = client.fetch_snapshots()
    assert len(stored) == SNAPSHOT_BATCH + 1
    assert stored[0].book == hostile
    assert stored[0].point == Decimal("-3.5")
    inserts = []
    for call in fake.calls:
        assert call["method"] == "POST"
        assert call["url"] == (
            "https://api.cloudflare.com/client/v4/accounts/acct-1/d1/database/db-1/query"
        )
        assert call["headers"]["Authorization"] == "Bearer cf-token"
        assert "cf-token" not in call["url"]
        body = json.loads(call["body"])
        assert set(body) == {"sql", "params"}
        sql = body["sql"]
        assert "DROP TABLE" not in sql
        assert hostile not in sql
        if sql.startswith("INSERT OR REPLACE INTO snapshots"):
            assert sql.count("?") <= 100
            inserts.append(body["params"])
    assert len(inserts) == 2
    assert inserts[0].count(hostile) == 1
    assert len(inserts[0]) == SNAPSHOT_BATCH * 10
    created = " ".join(json.loads(call["body"])["sql"] for call in fake.calls)
    assert created.count("CREATE TABLE IF NOT EXISTS snapshots") == 2
    assert "shot_key" in created


def _row(index: int, book: str) -> SnapshotRow:
    stamp = datetime(2026, 11, 10, 14, 7, tzinfo=UTC)
    return SnapshotRow(
        snapshot_ts=stamp,
        game_id=f"2026_11_G{index}_H{index}",
        event_id=f"evt-{index}",
        book=book,
        market="spread",
        side="H",
        point=Decimal("-3.5"),
        price=-110,
        last_update=stamp,
        shot_kind="fixed",
    )
