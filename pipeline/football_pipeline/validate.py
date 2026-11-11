"""Validate data/picks.csv.

The header and every cell are checked. A failing file exits non-zero and
prints one GitHub annotation per problem. The kickoff lock is not applied
here: a schema-valid late pick is still recorded so the grader can mark it
rejected_late and the page can show it.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from football_pipeline.close import Game
from football_pipeline.schema import (
    ACTIONS,
    PICK_COLUMNS,
    PickRow,
    normalize_action,
    parse_pick_file,
    parse_ts,
    pick_version,
)

MARKETS = {"spread", "total"}
TOTAL_SIDES = {"over", "under"}


@dataclass(frozen=True)
class Issue:
    line: int
    message: str


def validate_rows(rows: list[PickRow], games: dict[str, Game]) -> list[Issue]:
    issues: list[Issue] = []
    seen: dict[tuple[str, str], int] = {}
    for row in rows:
        issues.extend(_row_issues(row, games))
        # A void shares pick_id with its pick. The pair is (pick_id, action).
        key = (row.pick_id, normalize_action(row.cells["action"]))
        previous = seen.get(key)
        if previous is not None:
            issues.append(
                Issue(row.line_no, f"duplicate pick_id {row.pick_id} (first seen on line {previous})")
            )
        else:
            seen[key] = row.line_no
    return issues


def validate_file(path: Path, games: dict[str, Game]) -> tuple[list[PickRow], list[Issue]]:
    rows, structural = parse_pick_file(path)
    issues = [Issue(line, message) for line, message in structural]
    issues.extend(validate_rows(rows, games))
    return rows, issues


def emit_annotations(root: Path, picks_path: Path, issues: list[Issue]) -> None:
    rel = _relative(root, picks_path)
    for issue in issues:
        message = issue.message.replace("\n", " ").replace("\r", " ")
        print(f"::error file={rel},line={issue.line}::{message}")


def _row_issues(row: PickRow, games: dict[str, Game]) -> list[Issue]:
    cells = row.cells
    line = row.line_no
    issues: list[Issue] = []
    for column in PICK_COLUMNS:
        if "\n" in cells[column] or "\r" in cells[column]:
            issues.append(Issue(line, f"{column} must be a single line"))

    game_id = cells["game_id"]
    market = cells["market"]
    if pick_version(cells["pick_id"], game_id, market) is None:
        issues.append(Issue(line, f"pick_id must be {game_id}-{market}"))
    if market not in MARKETS:
        issues.append(Issue(line, "market must be spread or total"))

    season = _require_int(cells["season"], "season", line, issues)
    week = _require_int(cells["week"], "week", line, issues)
    if market == "total" and cells["side"] not in TOTAL_SIDES:
        issues.append(Issue(line, "total side must be over or under"))
    game = games.get(game_id)
    if game is None:
        issues.append(Issue(line, f"unknown game_id {game_id}"))
    else:
        if season is not None and season != game.season:
            issues.append(
                Issue(line, f"season {season} does not match {game_id} season {game.season}")
            )
        if week is not None and week != game.week:
            issues.append(Issue(line, f"week {week} does not match {game_id} week {game.week}"))
        if market == "spread":
            allowed = {game.away_team, game.home_team}
            if cells["side"] not in allowed:
                issues.append(
                    Issue(
                        line,
                        f"spread side must be {' or '.join(sorted(allowed))}",
                    )
                )

    if not _is_decimal_token(cells["line_at_pick"]):
        issues.append(Issue(line, "line_at_pick must be a number"))
    if not _units_are_one(cells["units"]):
        issues.append(Issue(line, "units must be 1.0"))
    if cells["confidence"] == "":
        confidence = None
    else:
        confidence = _require_int(cells["confidence"], "confidence", line, issues)
        if confidence is not None and confidence not in {1, 2, 3}:
            issues.append(Issue(line, "confidence must be an integer from 1 to 3"))
    action = cells["action"]
    if action not in ACTIONS and action != "":
        issues.append(Issue(line, "action must be pick or void"))
    try:
        parse_ts(cells["picked_at"])
    except ValueError as exc:
        issues.append(Issue(line, f"picked_at must be ISO-8601 with an offset ({exc})"))
    return issues


def _require_int(token: str, name: str, line: int, issues: list[Issue]) -> int | None:
    if not token or not _is_int_token(token):
        issues.append(Issue(line, f"{name} must be an integer"))
        return None
    return int(token)


def _is_int_token(token: str) -> bool:
    if token.startswith(("+", "-")):
        token = token[1:]
    return token.isdigit() and token != ""


def _is_decimal_token(token: str) -> bool:
    if not token:
        return False
    try:
        Decimal(token)
    except ArithmeticError:
        return False
    return True


def _units_are_one(token: str) -> bool:
    try:
        return Decimal(token) == Decimal("1.0")
    except ArithmeticError:
        return False


def _relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()
