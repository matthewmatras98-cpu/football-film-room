"""Malformed picks fail validation with GitHub annotations."""

from __future__ import annotations

import csv
from pathlib import Path

from tests.conftest import FIXTURES, run_cli

HEADER = [
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
]


def test_fixture_picks_pass(tmp_path: Path):
    root = _root(tmp_path)
    _write_rows(root, [_valid_spread(), _valid_total()])
    result = _validate(root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "::error" not in result.stdout


def test_malformed_rows_fail(tmp_path: Path):
    cases = {
        "bad pick_id": {**_valid_spread(), "pick_id": "nope"},
        "unknown game": {**_valid_spread(), "pick_id": "2099_01_NO_SUCH-spread", "game_id": "2099_01_NO_SUCH"},
        "season mismatch": {**_valid_spread(), "season": "2098"},
        "week mismatch": {**_valid_spread(), "week": "3"},
        "bad market": {**_valid_spread(), "pick_id": "2099_01_DET_PHI-moneyline", "market": "moneyline"},
        "spread side": {**_valid_spread(), "side": "det"},
        "spread over": {**_valid_spread(), "side": "over"},
        "total side": {**_valid_total(), "side": "DET"},
        "units": {**_valid_spread(), "units": "2"},
        "confidence high": {**_valid_spread(), "confidence": "4"},
        "confidence zero": {**_valid_spread(), "confidence": "0"},
        "confidence decimal": {**_valid_spread(), "confidence": "1.5"},
        "naive picked_at": {**_valid_spread(), "picked_at": "2099-09-13T15:00:00"},
        "garbage picked_at": {**_valid_spread(), "picked_at": "yesterday"},
        "blank line": {**_valid_spread(), "line_at_pick": ""},
    }
    for name, row in cases.items():
        root = _root(tmp_path / name)
        _write_rows(root, [row])
        result = _validate(root)
        assert result.returncode != 0, name
        assert "::error file=data/picks.csv,line=2::" in result.stdout, name


def test_duplicate_pick_id(tmp_path: Path):
    root = _root(tmp_path)
    _write_rows(root, [_valid_spread(), _valid_spread()])
    result = _validate(root)
    assert result.returncode != 0
    assert "duplicate pick_id" in result.stdout
    assert "line=3" in result.stdout


def test_header_must_be_exact(tmp_path: Path):
    root = _root(tmp_path)
    path = root / "data" / "picks.csv"
    reordered = HEADER[1:] + HEADER[:1]
    path.write_text(",".join(reordered) + "\n", encoding="utf-8")
    result = _validate(root)
    assert result.returncode != 0
    assert "::error file=data/picks.csv,line=1::" in result.stdout

    path.write_text(",".join(HEADER + ["extra"]) + "\n", encoding="utf-8")
    assert _validate(root).returncode != 0

    path.write_text(",".join(HEADER[:-1]) + "\n", encoding="utf-8")
    assert _validate(root).returncode != 0


def test_short_row_and_offset_timestamp(tmp_path: Path):
    root = _root(tmp_path)
    path = root / "data" / "picks.csv"
    path.write_text(",".join(HEADER) + "\nonly,three,cells\n", encoding="utf-8")
    result = _validate(root)
    assert result.returncode != 0
    assert "columns" in result.stdout

    row = _valid_spread()
    row["picked_at"] = "2099-09-13T11:00:00-04:00"
    _write_rows(root, [row])
    ok = _validate(root)
    assert ok.returncode == 0, ok.stdout + ok.stderr


def test_units_numeric_one_is_accepted(tmp_path: Path):
    root = _root(tmp_path)
    for token in ("1", "1.0", "1.00", "1.000"):
        row = _valid_spread()
        row["units"] = token
        _write_rows(root, [row])
        result = _validate(root)
        assert result.returncode == 0, token


def _validate(root: Path):
    return run_cli(
        ["validate", "--root", str(root), "--schedules", str(FIXTURES / "schedules.csv")]
    )


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "data").mkdir(parents=True, exist_ok=True)
    return root


def _write_rows(root: Path, rows: list[dict[str, str]]) -> None:
    path = root / "data" / "picks.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADER, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _valid_spread() -> dict[str, str]:
    return {
        "pick_id": "2099_01_DET_PHI-spread",
        "game_id": "2099_01_DET_PHI",
        "season": "2099",
        "week": "1",
        "market": "spread",
        "side": "DET",
        "line_at_pick": "2.5",
        "picked_at": "2099-09-13T15:00:00Z",
        "units": "1.0",
        "confidence": "2",
        "tags": "pass-rush",
        "note": "ok",
    }


def _valid_total() -> dict[str, str]:
    return {
        "pick_id": "2099_01_DET_PHI-total",
        "game_id": "2099_01_DET_PHI",
        "season": "2099",
        "week": "1",
        "market": "total",
        "side": "over",
        "line_at_pick": "46.5",
        "picked_at": "2099-09-13T15:00:00Z",
        "units": "1.0",
        "confidence": "1",
        "tags": "",
        "note": "over",
    }
