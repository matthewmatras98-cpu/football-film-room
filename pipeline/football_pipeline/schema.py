"""Shared pick schema, canonical row hash, and timestamp formatting."""

from __future__ import annotations

import csv
import hashlib
import io
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

PICK_COLUMNS: tuple[str, ...] = (
    "pick_id",
    "game_id",
    "season",
    "week",
    "market",
    "side",
    "line_at_pick",
    "picked_at",
    "units",
    "confidence",
    "tags",
    "note",
    "action",
)

LEDGER_COLUMNS: tuple[str, ...] = ("pick_id", "action", "row_sha", "first_pushed_at")

ACTIONS = frozenset({"pick", "void"})

GRADED_COLUMNS: tuple[str, ...] = (
    "pick_id",
    "closing_line",
    "close_source",
    "close_ts",
    "close_books",
    "close_age_min",
    "final_home",
    "final_away",
    "result",
    "units_pl",
    "clv_pts",
    "status",
)


class PipelineError(Exception):
    """A grader or ledger failure that should exit non-zero."""


@dataclass(frozen=True)
class PickRow:
    line_no: int
    cells: dict[str, str]
    row_sha: str

    @property
    def pick_id(self) -> str:
        return self.cells["pick_id"]


def parse_ts(value: str) -> datetime:
    """Parse an ISO-8601 timestamp that includes a numeric offset or Z."""
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"not an ISO-8601 timestamp: {value}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp has no offset: {value}")
    return parsed.astimezone(timezone.utc)


def format_ts(value: datetime) -> str:
    """UTC timestamp with second precision and a Z suffix."""
    if value.tzinfo is None:
        raise ValueError("refusing to format a naive timestamp")
    utc = value.astimezone(timezone.utc).replace(microsecond=0)
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_action(value: str) -> str:
    """Blank action is a pick, so rows written before the column stay valid."""
    text = value.strip()
    if not text:
        return "pick"
    return text


def canonical_row(cells: Mapping[str, str]) -> str:
    """CSV encoding of one pick in schema order.

    Surrounding whitespace is stripped. csv's default quoting is used, so a
    comma or quote inside a note changes the hash and is escaped the same way
    on every machine.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="")
    writer.writerow([cells[column].strip() for column in PICK_COLUMNS])
    return buffer.getvalue()


def row_sha(cells: Mapping[str, str]) -> str:
    """sha256 hex of the canonical CSV row. Git dates are not an input."""
    return hashlib.sha256(canonical_row(cells).encode("utf-8")).hexdigest()


def parse_pick_file(path: Path) -> tuple[list[PickRow], list[tuple[int, str]]]:
    """Read picks.csv. Structural problems are returned, not raised.

    A bad header stops the parse. A short or long row is reported and skipped
    so later rows are still checked. Semantic checks live in validate.py.
    """
    if not path.is_file():
        return [], [(1, f"picks file not found: {path}")]
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            return [], [(1, f"{path} is empty")]
        if tuple(header) != PICK_COLUMNS:
            return [], [
                (1, "picks.csv header must be exactly: " + ",".join(PICK_COLUMNS))
            ]
        rows: list[PickRow] = []
        issues: list[tuple[int, str]] = []
        for line_no, raw in enumerate(reader, start=2):
            if not any(cell.strip() for cell in raw):
                continue
            if len(raw) != len(PICK_COLUMNS):
                issues.append(
                    (
                        line_no,
                        f"expected {len(PICK_COLUMNS)} columns, found {len(raw)}",
                    )
                )
                continue
            cells = {
                column: value.strip()
                for column, value in zip(PICK_COLUMNS, raw, strict=True)
            }
            rows.append(PickRow(line_no=line_no, cells=cells, row_sha=row_sha(cells)))
    return rows, issues


def load_pick_rows(path: Path) -> list[PickRow]:
    """Read a schema-valid picks.csv. The first structural problem raises."""
    rows, issues = parse_pick_file(path)
    if issues:
        line, message = issues[0]
        raise PipelineError(f"{path}:{line}: {message}")
    return rows


def write_csv(path: Path, header: Sequence[str], records: Sequence[Sequence[str]]) -> None:
    """Write a UTF-8 CSV with LF endings and a trailing newline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(list(header))
    for record in records:
        writer.writerow(list(record))
    text = buffer.getvalue()
    if not text.endswith("\n"):
        text += "\n"
    _atomic_write(path, text.encode("utf-8"))


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)
