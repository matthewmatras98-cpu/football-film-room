"""Append one pick or one void from a workflow_dispatch.

The runner clock and server_received_at both have to be more than 10 minutes
before kickoff. A new pick is accepted only when that game and market has no
active pick. Inputs that fail validation leave the tree untouched.
"""

from __future__ import annotations

import csv
import io
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from football_pipeline.cards import select_open_weeks
from football_pipeline.lines import picked_side_line
from football_pipeline.nflv import load_games
from football_pipeline.schema import (
    PICK_COLUMNS,
    PickRow,
    PipelineError,
    format_ts,
    normalize_action,
    parse_pick_file,
    parse_ts,
)
from football_pipeline.snapshot import format_point

CUSHION = timedelta(minutes=10)
FUTURE_SLACK = timedelta(minutes=5)
WRITEUP_LIMIT = 50_000

GAME_ID_RE = re.compile(r"^\d{4}_\d{2}_[A-Z]{2,3}_[A-Z]{2,3}$")
PICK_ID_RE = re.compile(
    r"^\d{4}_\d{2}_[A-Z]{2,3}_[A-Z]{2,3}-(spread|total)(-r[2-9])?$"
)
UUID_V4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
LINE_RE = re.compile(r"[+-]?\d+(?:\.\d+)?")

_ENV_KEYS = {
    "action": "ACTION",
    "request_id": "REQUEST_ID",
    "game_id": "GAME_ID",
    "market": "MARKET",
    "side": "SIDE",
    "line": "LINE",
    "confidence": "CONFIDENCE",
    "writeup": "WRITEUP",
    "server_received_at": "SERVER_RECEIVED_AT",
    "void_pick_id": "VOID_PICK_ID",
}


class IntakeError(PipelineError):
    """A dispatch input failed. The picks file and write-up are unchanged."""


@dataclass(frozen=True)
class IntakeFields:
    action: str
    request_id: str
    game_id: str
    market: str
    side: str
    line: str
    confidence: str
    writeup: str
    server_received_at: str
    void_pick_id: str

    def stripped(self) -> IntakeFields:
        """Strip every field except the write-up, which is stored verbatim."""

        def clean(value: str) -> str:
            return value.strip()

        return IntakeFields(
            action=clean(self.action),
            request_id=clean(self.request_id),
            game_id=clean(self.game_id),
            market=clean(self.market),
            side=clean(self.side),
            line=clean(self.line),
            confidence=clean(self.confidence),
            writeup=self.writeup,
            server_received_at=clean(self.server_received_at),
            void_pick_id=clean(self.void_pick_id),
        )


def fields_from_env(env: Mapping[str, str]) -> IntakeFields:
    values = {name: env.get(key, "") for name, key in _ENV_KEYS.items()}
    return IntakeFields(**values).stripped()


def apply_intake(
    *,
    root: Path,
    schedules: Path | None,
    now: datetime,
    fields: IntakeFields,
) -> str:
    """Validate, then append the row. Returns the pick id. Writes nothing on failure."""
    fields = fields.stripped()
    if now.tzinfo is None:
        raise IntakeError("runner clock has no offset")
    now = now.astimezone(timezone.utc)
    if fields.action not in {"pick", "void"}:
        raise IntakeError("action must be pick or void")
    if not UUID_V4_RE.fullmatch(fields.request_id):
        raise IntakeError("request_id is not a lowercase uuid v4")
    server = _parse_server_time(fields.server_received_at)
    if server > now + FUTURE_SLACK:
        raise IntakeError("server_received_at is more than 5 minutes ahead of the runner clock")
    try:
        games, _source = load_games(fixture=schedules)
    except PipelineError as exc:
        raise IntakeError("schedule could not be read") from exc
    by_id = {game.game_id: game for game in games}
    picks_path = root / "data" / "picks.csv"
    rows, raw = _read_picks(picks_path)
    if fields.action == "void":
        return _apply_void(picks_path, raw, rows, by_id, now, server, fields)
    return _apply_pick(root, picks_path, raw, rows, games, by_id, now, server, fields)


def clean_writeup(text: str) -> str:
    """Normalize newlines and drop control characters. Over the cap is an error."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    kept: list[str] = []
    for char in normalized:
        if char in "\n\t":
            kept.append(char)
            continue
        if unicodedata.category(char).startswith("C"):
            continue
        kept.append(char)
    cleaned = "".join(kept)
    if len(cleaned) > WRITEUP_LIMIT:
        raise IntakeError("writeup is longer than 50000 characters")
    return cleaned


def render_writeup(pick_id: str, game_id: str, submitted_at: str, body: str) -> str:
    """YAML frontmatter with double-quoted scalars. The body is not rewritten."""
    return (
        "---\n"
        f"pick_id: {json.dumps(pick_id, ensure_ascii=False)}\n"
        f"game_id: {json.dumps(game_id, ensure_ascii=False)}\n"
        f"submitted_at: {json.dumps(submitted_at, ensure_ascii=False)}\n"
        "---\n"
        f"{body}"
    )


def _apply_pick(root, picks_path, raw, rows, games, by_id, now, server, fields: IntakeFields) -> str:
    if fields.void_pick_id:
        raise IntakeError("void_pick_id must be blank on a pick")
    if not GAME_ID_RE.fullmatch(fields.game_id):
        raise IntakeError("game_id does not match the game id pattern")
    if fields.market not in {"spread", "total"}:
        raise IntakeError("market must be spread or total")
    game = by_id.get(fields.game_id)
    if game is None:
        raise IntakeError(f"game_id {fields.game_id} is not in the schedule")
    if (game.season, game.week) not in select_open_weeks(games, now):
        raise IntakeError(f"game_id {fields.game_id} is not in the open week")
    _check_side(game, fields.market, fields.side)
    if game.kickoff_utc is None:
        raise IntakeError(f"game_id {fields.game_id} has no kickoff")
    _check_cushion(now, server, game)
    line = _resolve_line(root, game, fields.market, fields.side, fields.line)
    confidence = _check_confidence(fields.confidence)
    body = clean_writeup(fields.writeup)
    active = _active_pick_id(rows, game.game_id, fields.market)
    if active is not None:
        raise IntakeError(f"active pick exists: {active}")
    # n = existing pick rows + 1. Version 1 has no suffix; 2-9 are -rN; 10 fails.
    version = _pick_count(rows, game.game_id, fields.market) + 1
    if version > 9:
        raise IntakeError("a 10th version is not allowed")
    base = f"{game.game_id}-{fields.market}"
    pick_id = base if version == 1 else f"{base}-r{version}"
    if not PICK_ID_RE.fullmatch(pick_id):
        raise IntakeError("pick id does not match the pick id pattern")
    submitted = format_ts(server)
    cells = {
        "pick_id": pick_id,
        "game_id": game.game_id,
        "season": str(game.season),
        "week": str(game.week),
        "market": fields.market,
        "side": fields.side,
        "line_at_pick": format_point(line),
        "picked_at": submitted,
        "units": "1.0",
        "confidence": confidence,
        "tags": "",
        "note": "",
        "action": "pick",
    }
    md_path = root / "src" / "content" / "picks" / f"{pick_id}.md"
    _atomic_write(md_path, render_writeup(pick_id, game.game_id, submitted, body).encode("utf-8"))
    try:
        _append_row(picks_path, raw, cells)
    except Exception:
        md_path.unlink(missing_ok=True)
        raise
    return pick_id


def _apply_void(picks_path, raw, rows: list[PickRow], by_id, now, server, fields: IntakeFields) -> str:
    if not PICK_ID_RE.fullmatch(fields.void_pick_id):
        raise IntakeError("void_pick_id is not a pick id")
    pick_id = fields.void_pick_id
    voided = [
        row
        for row in rows
        if row.pick_id == pick_id and normalize_action(row.cells["action"]) == "void"
    ]
    picks = [
        row
        for row in rows
        if row.pick_id == pick_id and normalize_action(row.cells["action"]) == "pick"
    ]
    if voided:
        raise IntakeError(f"pick {pick_id} is already voided")
    if not picks:
        raise IntakeError(f"pick {pick_id} does not exist")
    source = picks[0]
    active = _active_pick_id(rows, source.cells["game_id"], source.cells["market"])
    if active != pick_id:
        raise IntakeError(f"void_pick_id {pick_id} is not the active pick")
    game = by_id.get(source.cells["game_id"])
    if game is None or game.kickoff_utc is None:
        raise IntakeError(f"pick {pick_id} is not in the schedule")
    _check_cushion(now, server, game)
    cells = {column: source.cells[column] for column in PICK_COLUMNS}
    cells["picked_at"] = format_ts(server)
    cells["action"] = "void"
    _append_row(picks_path, raw, cells)
    return pick_id


def _market_rows(rows: list[PickRow], game_id: str, market: str) -> list[PickRow]:
    return [
        row
        for row in rows
        if row.cells["game_id"] == game_id and row.cells["market"] == market
    ]


def _pick_count(rows: list[PickRow], game_id: str, market: str) -> int:
    return sum(
        1
        for row in _market_rows(rows, game_id, market)
        if normalize_action(row.cells["action"]) == "pick"
    )


def _active_pick_id(rows: list[PickRow], game_id: str, market: str) -> str | None:
    """The highest active version. Active means a pick row and no void row."""
    voided = {
        row.pick_id
        for row in _market_rows(rows, game_id, market)
        if normalize_action(row.cells["action"]) == "void"
    }
    active: list[str] = []
    for row in _market_rows(rows, game_id, market):
        if normalize_action(row.cells["action"]) != "pick" or row.pick_id in voided:
            continue
        if row.pick_id not in active:
            active.append(row.pick_id)
    if not active:
        return None
    base = f"{game_id}-{market}"

    def version(pick_id: str) -> int:
        if pick_id == base:
            return 1
        prefix = f"{base}-r"
        if pick_id.startswith(prefix) and pick_id[len(prefix):].isdigit():
            return int(pick_id[len(prefix):])
        return 0

    return max(active, key=version)


def _check_side(game, market: str, side: str) -> None:
    if market == "spread":
        if side not in {game.away_team, game.home_team}:
            raise IntakeError(f"side is not valid for {game.game_id} spread")
        return
    if side not in {"over", "under"}:
        raise IntakeError(f"side is not valid for {game.game_id} total")


def _check_cushion(now: datetime, server: datetime, game) -> None:
    lock = game.kickoff_utc - CUSHION
    if now >= lock:
        raise IntakeError(f"runner clock is at or after the 10-minute lock for {game.game_id}")
    if server >= lock:
        raise IntakeError(
            f"server_received_at is at or after the 10-minute lock for {game.game_id}"
        )


def _resolve_line(root: Path, game, market: str, side: str, raw: str) -> Decimal:
    if raw == "":
        value = _latest_picked_line(root, game, market, side)
        if value is None:
            raise IntakeError(f"no snapshot line for {game.game_id}-{market}")
    else:
        value = _parse_line(raw)
    _check_range(market, value)
    return value


def _parse_line(raw: str) -> Decimal:
    if not LINE_RE.fullmatch(raw):
        raise IntakeError("line must be a number on a .0 or .5 step")
    try:
        value = Decimal(raw)
    except ArithmeticError as exc:
        raise IntakeError("line must be a number on a .0 or .5 step") from exc
    doubled = value * 2
    if doubled != doubled.to_integral_value():
        raise IntakeError("line must be a number on a .0 or .5 step")
    return value


def _check_range(market: str, value: Decimal) -> None:
    if market == "spread":
        if abs(value) > 30:
            raise IntakeError("line is outside the spread range")
        return
    if value < 20 or value > 80:
        raise IntakeError("line is outside the total range")


def _check_confidence(value: str) -> str:
    if value == "":
        return ""
    if value not in {"1", "2", "3"}:
        raise IntakeError("confidence must be blank or 1, 2, or 3")
    return value


def _parse_server_time(value: str) -> datetime:
    if not value.endswith("Z"):
        raise IntakeError("server_received_at must be an ISO-8601 UTC timestamp ending in Z")
    try:
        return parse_ts(value)
    except ValueError as exc:
        raise IntakeError(
            "server_received_at must be an ISO-8601 UTC timestamp ending in Z"
        ) from exc


def _latest_picked_line(root: Path, game, market: str, side: str) -> Decimal | None:
    directory = root / "data" / "lines"
    if not directory.is_dir():
        return None
    best: tuple[datetime, Decimal] | None = None
    for path in sorted(directory.glob("*.csv")):
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or not {"snapshot_ts", "game_id", "market", "line"}.issubset(
                reader.fieldnames
            ):
                continue
            for row in reader:
                if (row.get("game_id") or "").strip() != game.game_id:
                    continue
                if (row.get("market") or "").strip() != market:
                    continue
                try:
                    stamp = parse_ts((row.get("snapshot_ts") or "").strip())
                    point = Decimal((row.get("line") or "").strip())
                except (ValueError, ArithmeticError):
                    continue
                if game.kickoff_utc is not None and stamp >= game.kickoff_utc:
                    continue
                if best is None or stamp > best[0]:
                    best = (stamp, point)
    if best is None:
        return None
    point = best[1]
    if market == "total":
        return point
    return picked_side_line(point, side=side, home=game.home_team)


def _read_picks(path: Path) -> tuple[list[PickRow], bytes]:
    if not path.is_file() or path.stat().st_size == 0:
        return [], b""
    rows, issues = parse_pick_file(path)
    if issues:
        line, _message = issues[0]
        raise IntakeError(f"picks.csv line {line} does not match the pick schema")
    return rows, path.read_bytes()


def _append_row(path: Path, existing: bytes, cells: Mapping[str, str]) -> None:
    """Append one row. Existing bytes, including a blank action cell, are not rewritten."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow([cells[column] for column in PICK_COLUMNS])
    line = buffer.getvalue().encode("utf-8")
    if not existing:
        payload = (",".join(PICK_COLUMNS) + "\n").encode("utf-8") + line
    else:
        payload = existing if existing.endswith(b"\n") else existing + b"\n"
        payload += line
    _atomic_write(path, payload)


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)
