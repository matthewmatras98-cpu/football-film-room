"""Derived line tables. No book names, prices, or per-book rows.

data/lines/{season}_w{NN}.csv is one row per snapshot, game, and market.
`line` is the lower-middle book line, element floor((n - 1) / 2).
The file is snapshot_ts, game_id, market, line, n_books. It does not
include min or max. The week being graded is the latest fully final week.
An open week is any week that is not final and starts within seven days
of --as-of.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from football_pipeline.close import SNAPSHOT_COLUMNS, BookLine, Game, lower_middle
from football_pipeline.schema import PipelineError, format_ts, write_csv
from football_pipeline.snapshot import SnapshotRow, format_point

LINES_COLUMNS: tuple[str, ...] = (
    "snapshot_ts",
    "game_id",
    "market",
    "line",
    "n_books",
)


def select_export_weeks(games: list[Game], as_of: datetime) -> set[tuple[int, int]]:
    by_week: dict[tuple[int, int], list[Game]] = {}
    for game in games:
        by_week.setdefault((game.season, game.week), []).append(game)
    completed: list[tuple[int, int]] = []
    opens: list[tuple[int, int]] = []
    horizon = as_of + timedelta(days=7)
    for key, group in by_week.items():
        if all(game.has_final_score for game in group):
            completed.append(key)
            continue
        kickoffs = [game.kickoff_utc for game in group if game.kickoff_utc is not None]
        if kickoffs and min(kickoffs) <= horizon:
            opens.append(key)
    chosen = set(opens)
    if completed:
        chosen.add(max(completed))
    return chosen


def book_lines_from_snapshots(rows: list[SnapshotRow], games: list[Game]) -> list[BookLine]:
    """One close-table row per book: home_spread, or the Over total."""
    by_id = {game.game_id: game for game in games}
    lines: list[BookLine] = []
    for row in rows:
        game = by_id.get(row.game_id)
        if game is None:
            raise PipelineError(f"snapshot game_id not in schedule: {row.game_id}")
        if row.market == "spread" and row.side == game.home_team:
            point = row.point
        elif row.market == "total" and row.side == "over":
            point = row.point
        else:
            continue
        lines.append(
            BookLine(
                game_id=row.game_id,
                market=row.market,
                book=row.book,
                point=point,
                snapshot_ts=row.snapshot_ts,
            )
        )
    lines.sort(key=lambda row: (format_ts(row.snapshot_ts), row.game_id, row.market, row.book))
    return lines


def write_close_file(path: Path, books: list[BookLine]) -> None:
    records = [
        [row.game_id, row.market, row.book, format_point(row.point), format_ts(row.snapshot_ts)]
        for row in books
    ]
    write_csv(path, SNAPSHOT_COLUMNS, records)


def export_lines(root: Path, games: list[Game], books: list[BookLine], as_of: datetime) -> list[Path]:
    targets = select_export_weeks(games, as_of)
    by_id = {game.game_id: game for game in games}
    grouped: dict[tuple, dict[str, Decimal]] = {}
    for row in books:
        game = by_id.get(row.game_id)
        if game is None:
            raise PipelineError(f"snapshot game_id not in schedule: {row.game_id}")
        key_week = (game.season, game.week)
        if key_week not in targets:
            continue
        bucket = (game.season, game.week, row.snapshot_ts, row.game_id, row.market)
        grouped.setdefault(bucket, {})[row.book] = row.point
    by_week: dict[tuple[int, int], list[list[str]]] = {}
    for (season, week, snapshot_ts, game_id, market), points_by_book in grouped.items():
        points = list(points_by_book.values())
        if not points:
            continue
        by_week.setdefault((season, week), []).append(
            [
                format_ts(snapshot_ts),
                game_id,
                market,
                format_point(lower_middle(points)),
                str(len(points)),
            ]
        )
    written: list[Path] = []
    for (season, week), records in sorted(by_week.items()):
        records.sort()
        path = root / "data" / "lines" / f"{season}_w{week:02d}.csv"
        write_csv(path, LINES_COLUMNS, records)
        written.append(path)
    return written
