"""Choose one closing line for a game and market.

Order: snapshot close, then the nflverse fallback, then no close (ungraded).
Snapshot rows are per-book lines already expressed as home_spread (spreads)
or the total number (totals). They are fixtures in T1. T2 will supply real
snapshots later.

The consensus line is never an average. Sort the book lines ascending and
take element floor((n - 1) / 2): the lower-middle real book line. Whole
numbers stay whole, so a pick can push.

Prefer the last pre-kick snapshot whose age is in [5, 75] minutes. If none
sit in that window, use the latest pre-kick snapshot. n >= 3 → odds_api.
n of 1 or 2 → odds_api_thin. Age from 76 through 180 minutes keeps those
labels. Only age above 180 minutes is odds_api_stale. nflverse closes record
an empty close_ts and null book count and age.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from football_pipeline.lines import home_spread_from_nflverse
from football_pipeline.schema import PipelineError, format_ts, parse_ts

SNAPSHOT_COLUMNS: tuple[str, ...] = (
    "game_id",
    "market",
    "book",
    "point",
    "snapshot_ts",
)

STALE_AFTER_MINUTES = Decimal(180)


@dataclass(frozen=True)
class Game:
    game_id: str
    season: int
    week: int
    away_team: str
    home_team: str
    kickoff_utc: datetime | None
    away_score: int | None
    home_score: int | None
    spread_line: Decimal | None
    total_line: Decimal | None

    @property
    def has_final_score(self) -> bool:
        return self.away_score is not None and self.home_score is not None


@dataclass(frozen=True)
class BookLine:
    game_id: str
    market: str
    book: str
    point: Decimal
    snapshot_ts: datetime


@dataclass(frozen=True)
class Close:
    """line is home_spread for a spread and the total for a total."""

    line: Decimal | None
    source: str | None
    close_ts: str
    close_books: int | None
    close_age_min: Decimal | None


def lower_middle(points: list[Decimal]) -> Decimal:
    """Element floor((n - 1) / 2) of the ascending book lines.

    The result is always one of the inputs. It is the median when n is odd
    and the lower of the two middle lines when n is even.
    """
    if not points:
        raise ValueError("lower_middle requires at least one line")
    ordered = sorted(points)
    index = (len(ordered) - 1) // 2
    return ordered[index]


def select_close(
    game: Game,
    snapshot_closes: list[BookLine] | None = None,
    market: str = "spread",
) -> Close:
    """Snapshot close, else nflverse, else an empty close."""
    snapshot = _snapshot_close(game, snapshot_closes or [], market)
    if snapshot is not None:
        return snapshot
    return _nflverse_close(game, market)


def read_snapshot_closes(path: Path) -> list[BookLine]:
    if not path.is_file():
        raise PipelineError(f"snapshot file not found: {path}")
    lines: list[BookLine] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise PipelineError(f"{path} is empty") from exc
        if tuple(header) != SNAPSHOT_COLUMNS:
            raise PipelineError(
                "snapshot header must be exactly: " + ",".join(SNAPSHOT_COLUMNS)
            )
        for line_no, raw in enumerate(reader, start=2):
            if not any(cell.strip() for cell in raw):
                continue
            if len(raw) != len(SNAPSHOT_COLUMNS):
                raise PipelineError(
                    f"{path}:{line_no}: expected {len(SNAPSHOT_COLUMNS)} columns, found {len(raw)}"
                )
            game_id, market, book, point, snapshot_ts = (cell.strip() for cell in raw)
            if market not in {"spread", "total"}:
                raise PipelineError(f"{path}:{line_no}: market must be spread or total")
            if not book:
                raise PipelineError(f"{path}:{line_no}: book is required")
            try:
                parsed_point = Decimal(point)
                parsed_ts = parse_ts(snapshot_ts)
            except (ValueError, ArithmeticError) as exc:
                raise PipelineError(f"{path}:{line_no}: {exc}") from exc
            lines.append(
                BookLine(
                    game_id=game_id,
                    market=market,
                    book=book,
                    point=parsed_point,
                    snapshot_ts=parsed_ts,
                )
            )
    return lines


def _snapshot_close(game: Game, rows: list[BookLine], market: str) -> Close | None:
    """Last snapshot in the [5, 75] minute window, else the latest pre-kick.

    Rows at or after kickoff are in-play and are dropped. Books that share a
    snapshot_ts are one snapshot. A newer snapshot inside five minutes of
    kickoff does not replace one that still sits in the window.
    """
    if game.kickoff_utc is None:
        return None
    kickoff = game.kickoff_utc
    eligible = [
        row
        for row in rows
        if row.game_id == game.game_id and row.market == market and row.snapshot_ts < kickoff
    ]
    if not eligible:
        return None

    def age_minutes(stamp: datetime) -> Decimal:
        seconds = Decimal(str((kickoff - stamp).total_seconds()))
        return seconds / Decimal(60)

    by_ts: dict[datetime, list[BookLine]] = {}
    for row in eligible:
        by_ts.setdefault(row.snapshot_ts, []).append(row)
    in_window = [
        stamp
        for stamp in by_ts
        if Decimal(5) <= age_minutes(stamp) <= Decimal(75)
    ]
    chosen_ts = max(in_window) if in_window else max(by_ts)
    # One point per book. A repeated book keeps the last row in the file.
    by_book: dict[str, Decimal] = {}
    for row in by_ts[chosen_ts]:
        by_book[row.book] = row.point
    points = list(by_book.values())
    if not points:
        return None
    chosen = lower_middle(points)
    age_min = age_minutes(chosen_ts)
    count = len(points)
    if age_min > STALE_AFTER_MINUTES:
        source = "odds_api_stale"
    elif count >= 3:
        source = "odds_api"
    else:
        source = "odds_api_thin"
    return Close(
        line=chosen,
        source=source,
        close_ts=format_ts(chosen_ts),
        close_books=count,
        close_age_min=age_min,
    )


def _nflverse_close(game: Game, market: str) -> Close:
    if market == "spread" and game.spread_line is not None:
        line = home_spread_from_nflverse(game.spread_line)
    elif market == "total" and game.total_line is not None:
        line = game.total_line
    else:
        line = None
    if line is None:
        return Close(line=None, source=None, close_ts="", close_books=None, close_age_min=None)
    return Close(
        line=line,
        source="nflverse",
        close_ts="",
        close_books=None,
        close_age_min=None,
    )
