#!/usr/bin/env python3
"""Exit 0 when every staged diff is a generated_at timestamp.

JSON objects drop keys named generated_at at any depth. CSV files drop a
generated_at column when the header has one. Added, deleted, and renamed
files are real changes.

Checked against this repo: src/data/summary.json and each
src/content/games/*.json card have the key. data/graded.csv,
src/data/picks_view.json, and data/lines/*.csv do not. Lines columns are
snapshot_ts, game_id, market, line, n_books.
"""

from __future__ import annotations

import csv
import io
import json
import subprocess
import sys

_KEY = "generated_at"


def main() -> int:
    for path, status in _staged():
        if not _only_timestamp(path, status):
            return 1
    return 0


def _staged() -> list[tuple[str, str]]:
    raw = subprocess.check_output(["git", "diff", "--cached", "--name-status", "-z"])
    parts = raw.split(b"\0")
    if parts and parts[-1] == b"":
        parts.pop()
    entries: list[tuple[str, str]] = []
    index = 0
    while index < len(parts):
        status = parts[index].decode("utf-8")
        index += 1
        if status[:1] in {"R", "C"}:
            index += 2
            entries.append(("", status))
            continue
        path = parts[index].decode("utf-8")
        index += 1
        entries.append((path, status))
    return entries


def _only_timestamp(path: str, status: str) -> bool:
    if status != "M" or not path:
        return False
    old = subprocess.check_output(["git", "show", f"HEAD:{path}"])
    new = subprocess.check_output(["git", "show", f":0:{path}"])
    if old == new:
        return True
    if path.endswith(".json"):
        return _json_only(old, new)
    if path.endswith(".csv"):
        return _csv_only(old, new)
    return False


def _json_only(old: bytes, new: bytes) -> bool:
    try:
        old_value = json.loads(old)
        new_value = json.loads(new)
    except json.JSONDecodeError:
        return False
    if _strip(old_value) != _strip(new_value):
        return False
    # A file with no generated_at key keeps grade-only's byte check: a
    # formatting-only diff in picks_view.json is still a commit.
    if not _has_key(old_value) and not _has_key(new_value):
        return False
    return True


def _strip(value: object) -> object:
    if isinstance(value, dict):
        return {key: _strip(item) for key, item in value.items() if key != _KEY}
    if isinstance(value, list):
        return [_strip(item) for item in value]
    return value


def _has_key(value: object) -> bool:
    if isinstance(value, dict):
        return _KEY in value or any(_has_key(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_key(item) for item in value)
    return False


def _csv_only(old: bytes, new: bytes) -> bool:
    old_rows = _csv_rows(old)
    new_rows = _csv_rows(new)
    if old_rows is None or new_rows is None:
        return False
    if not _has_column(old_rows) and not _has_column(new_rows):
        return False
    return _drop_column(old_rows) == _drop_column(new_rows)


def _csv_rows(payload: bytes) -> list[list[str]] | None:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return list(csv.reader(io.StringIO(text, newline="")))


def _has_column(rows: list[list[str]]) -> bool:
    return bool(rows) and _KEY in rows[0]


def _drop_column(rows: list[list[str]]) -> list[list[str]]:
    if not _has_column(rows):
        return rows
    indexes = {index for index, name in enumerate(rows[0]) if name == _KEY}
    return [[cell for index, cell in enumerate(row) if index not in indexes] for row in rows]


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError:
        sys.exit(1)
