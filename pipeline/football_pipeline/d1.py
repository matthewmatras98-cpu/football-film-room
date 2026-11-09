"""Cloudflare D1 HTTP client.

POST /accounts/{account}/d1/database/{database}/query with a Bearer token.
Values go in the params list. The schema is applied on every run.
Inserts are batched so a statement stays under D1's 100 bound parameters.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from football_pipeline.schema import PipelineError, format_ts, parse_ts
from football_pipeline.snapshot import SnapshotRow, format_point

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "d1" / "schema.sql"
# 10 columns * 8 rows = 80 parameters. D1 allows 100 per statement.
SNAPSHOT_BATCH = 8
SNAPSHOT_COLUMNS: tuple[str, ...] = (
    "snapshot_ts",
    "game_id",
    "event_id",
    "book",
    "market",
    "side",
    "point",
    "price",
    "last_update",
    "shot_kind",
)


@dataclass(frozen=True)
class ShotRecord:
    shot_key: str
    kind: str
    window_kickoff: str | None
    snapshot_ts: str | None
    credits_remaining: int | None
    status: str


class D1Client:
    def __init__(self, account_id: str, database_id: str, token: str, transport=None):
        self._account_id = account_id
        self._database_id = database_id
        self._token = token
        self._transport = transport

    def __repr__(self) -> str:
        return "D1Client(token='***')"

    def query(self, sql: str, params: list | None = None) -> list[dict]:
        body = json.dumps({"sql": sql, "params": list(params or [])}).encode("utf-8")
        url = (
            "https://api.cloudflare.com/client/v4/accounts/"
            f"{self._account_id}/d1/database/{self._database_id}/query"
        )
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "User-Agent": "football-pipeline",
        }
        status, raw = self._post(url, headers, body)
        if status is None:
            raise PipelineError("request failed for d1")
        if status != 200:
            raise PipelineError(f"HTTP {status} from d1")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            raise PipelineError("invalid response from d1") from None
        if not isinstance(payload, dict) or not payload.get("success"):
            raise PipelineError("D1 query failed")
        result = payload.get("result") or []
        if not result or not isinstance(result, list) or not result[0].get("success", True):
            raise PipelineError("D1 query failed")
        rows = result[0].get("results") or []
        if not isinstance(rows, list):
            raise PipelineError("D1 query failed")
        return rows

    def apply_schema(self) -> None:
        text = SCHEMA_PATH.read_text(encoding="utf-8")
        for statement in _statements(text):
            self.query(statement, [])

    def insert_snapshots(self, rows: list[SnapshotRow]) -> None:
        if not rows:
            return
        columns = ", ".join(SNAPSHOT_COLUMNS)
        for batch in _chunks(rows, SNAPSHOT_BATCH):
            placeholder = "(" + ", ".join(["?"] * len(SNAPSHOT_COLUMNS)) + ")"
            values = ", ".join([placeholder] * len(batch))
            sql = f"INSERT OR REPLACE INTO snapshots ({columns}) VALUES {values}"
            params: list = []
            for row in batch:
                params.extend(_snapshot_params(row))
            self.query(sql, params)

    def insert_shot(self, shot: ShotRecord) -> None:
        self.query(
            "INSERT OR REPLACE INTO shots "
            "(shot_key, kind, window_kickoff, snapshot_ts, credits_remaining, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                shot.shot_key,
                shot.kind,
                shot.window_kickoff,
                shot.snapshot_ts,
                shot.credits_remaining,
                shot.status,
            ],
        )

    def fetch_shots(self) -> list[ShotRecord]:
        rows = self.query(
            "SELECT shot_key, kind, window_kickoff, snapshot_ts, credits_remaining, status "
            "FROM shots ORDER BY rowid",
            [],
        )
        return [_shot(row) for row in rows]

    def fetch_snapshots(self) -> list[SnapshotRow]:
        rows = self.query(
            "SELECT snapshot_ts, game_id, event_id, book, market, side, point, price, "
            "last_update, shot_kind FROM snapshots "
            "ORDER BY snapshot_ts, game_id, book, market, side",
            [],
        )
        return [_snapshot(row) for row in rows]

    def _post(self, url: str, headers: dict[str, str], body: bytes) -> tuple[int | None, bytes]:
        if self._transport is not None:
            status, _response_headers, raw = self._transport("POST", url, headers, body)
            return status, raw
        return _urlopen_post(url, headers, body)


def _snapshot_params(row: SnapshotRow) -> list:
    return [
        format_ts(row.snapshot_ts),
        row.game_id,
        row.event_id,
        row.book,
        row.market,
        row.side,
        format_point(row.point),
        int(row.price),
        format_ts(row.last_update),
        row.shot_kind,
    ]


def _shot(row: dict) -> ShotRecord:
    credits = row.get("credits_remaining")
    return ShotRecord(
        shot_key=str(row["shot_key"]),
        kind=str(row["kind"]),
        window_kickoff=_text_or_none(row.get("window_kickoff")),
        snapshot_ts=_text_or_none(row.get("snapshot_ts")),
        credits_remaining=None if credits is None else int(credits),
        status=str(row["status"]),
    )


def _snapshot(row: dict) -> SnapshotRow:
    return SnapshotRow(
        snapshot_ts=parse_ts(str(row["snapshot_ts"])),
        game_id=str(row["game_id"]),
        event_id=str(row["event_id"]),
        book=str(row["book"]),
        market=str(row["market"]),
        side=str(row["side"]),
        point=Decimal(str(row["point"])),
        price=int(row["price"]),
        last_update=parse_ts(str(row["last_update"])),
        shot_kind=str(row["shot_kind"]),
    )


def _text_or_none(value) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _statements(sql: str) -> list[str]:
    return [part.strip() for part in sql.split(";") if part.strip()]


def _chunks(rows: list, size: int):
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


def _urlopen_post(url: str, headers: dict[str, str], body: bytes) -> tuple[int | None, bytes]:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    outcome = _perform(request)
    if outcome[0] == "http":
        raise PipelineError(f"HTTP {outcome[1]} from d1")
    if outcome[0] == "network":
        raise PipelineError("request failed for d1")
    return outcome[1]


def _perform(request):
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
            status = getattr(response, "status", 200)
            return ("ok", (status, raw))
    except urllib.error.HTTPError as exc:
        return ("http", exc.code)
    except urllib.error.URLError:
        return ("network", None)
