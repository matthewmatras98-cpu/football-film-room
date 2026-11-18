"""Pin the picks_view.json void contract documented in docs/pick-intake.md.

The ledger, fixture, and offline tests already cover the grade outcomes this
file reuses. What they do not pin is the emitted field set, the -r2 link a
consumer follows, and the per-week slate array built from the view alone.
"""

from __future__ import annotations

import csv
import json
import re
from decimal import Decimal
from pathlib import Path

from football_pipeline.schema import previous_pick_id
from tests.conftest import FIXTURES, grade_args, prepare_repo, run_cli

Z_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
GAME = "2099_01_DET_PHI"
BASE = f"{GAME}-spread"
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
    "action",
]
COUNT_KEYS = ("w", "l", "p", "units", "win_pct", "avg_clv", "pct_clv_pos", "void_count")


def test_counted_void_row_has_status_void_and_no_result(tmp_path: Path):
    root = _repo(tmp_path)
    pick = _pick(picked_at="2099-09-13T15:00:00Z", note="counted void")
    void = _pick(picked_at="2099-09-13T16:00:00Z", note="void row", action="void")
    _write(root, [pick, void])
    _ledger(root, "sha-early", "2099-09-13T12:00:00Z")
    _grade(root)

    view = _view(root)
    assert [row["pick_id"] for row in view] == [BASE]
    row = view[0]
    assert row == {
        "close_age_min": None,
        "close_books": None,
        "close_source": "nflverse",
        "close_ts": None,
        "closing_line": 3,
        "clv_pts": None,
        "confidence": 2,
        "final_away": 27,
        "final_home": 20,
        "first_pushed_at": "2099-09-13T12:00:00Z",
        "game_id": GAME,
        "line_at_pick": 2.5,
        "market": "spread",
        "note": "counted void",
        "pick_id": BASE,
        "picked_at": "2099-09-13T15:00:00Z",
        "result": None,
        "season": 2099,
        "side": "DET",
        "status": "void",
        "tags": "",
        "units": 1,
        "units_pl": None,
        "voided_at": "2099-09-13T16:00:00Z",
        "week": 1,
    }
    assert Z_TS.fullmatch(row["voided_at"])
    assert row["voided_at"] != row["first_pushed_at"]
    assert isinstance(row["season"], int)
    assert isinstance(row["week"], int)
    assert isinstance(row["line_at_pick"], float)
    assert isinstance(row["units"], int)
    assert isinstance(row["closing_line"], int)
    assert isinstance(row["confidence"], int)

    summary = _summary(root)
    assert summary["w"] == 0
    assert summary["l"] == 0
    assert summary["p"] == 0
    assert summary["units"] == 0
    assert summary["win_pct"] is None
    assert summary["avg_clv"] is None
    assert summary["pct_clv_pos"] is None
    assert summary["rejected_count"] == 0
    assert summary["ungraded_count"] == 0
    assert summary["void_count"] == 1
    assert _voids_by_week(view) == {
        1: [
            {
                "game_id": GAME,
                "week": 1,
                "pick": {
                    "side": "DET",
                    "market": "spread",
                    "line_at_pick": 2.5,
                    "picked_at": "2099-09-13T15:00:00Z",
                },
                "voided_at": "2099-09-13T16:00:00Z",
                "repick": None,
            }
        ]
    }


def test_counted_void_links_the_r2_repick(tmp_path: Path):
    """The first re-pick is -r2. Version 1 is the unsuffixed id. There is no -r1."""
    root = _repo(tmp_path)
    pick = _pick(picked_at="2099-09-13T15:00:00Z", note="counted void")
    void = _pick(picked_at="2099-09-13T16:00:00Z", action="void")
    repick = _pick(picked_at="2099-09-13T15:30:00Z", note="replacement", confidence="3")
    repick["pick_id"] = f"{BASE}-r2"
    _write(root, [pick, void, repick])
    _ledger(root, "sha-early", "2099-09-13T12:00:00Z")
    _grade(root)

    view = _view(root)
    by_id = {row["pick_id"]: row for row in view}
    assert list(by_id) == [BASE, f"{BASE}-r2"]
    assert all(not pick_id.endswith("-r1") for pick_id in by_id)
    original = by_id[BASE]
    replacement = by_id[f"{BASE}-r2"]
    assert original["status"] == "void"
    assert original["result"] is None
    assert original["units_pl"] is None
    assert replacement["game_id"] == original["game_id"]
    assert replacement["market"] == original["market"]
    assert replacement["week"] == original["week"]
    assert previous_pick_id(replacement["pick_id"], replacement["game_id"], replacement["market"]) == BASE
    assert replacement["status"] == "graded"
    assert replacement["result"] == "W"
    assert replacement["units_pl"] == 0.909
    assert replacement["clv_pts"] == -0.5
    assert replacement["voided_at"] is None
    assert replacement["picked_at"] == "2099-09-13T15:30:00Z"
    assert replacement["side"] == "DET"
    assert replacement["line_at_pick"] == 2.5

    summary = _summary(root)
    assert summary["w"] == 1
    assert summary["l"] == 0
    assert summary["units"] == 0.909
    assert summary["void_count"] == 1
    assert summary["rejected_count"] == 0
    assert _voids_by_week(view)[1][0]["repick"] == {
        "pick_id": f"{BASE}-r2",
        "side": "DET",
        "line_at_pick": 2.5,
        "picked_at": "2099-09-13T15:30:00Z",
    }


def test_late_void_leaves_the_original_graded(tmp_path: Path):
    picked_late = _repo(tmp_path / "picked-late")
    pick = _pick(picked_at="2099-09-13T15:00:00Z")
    _write(picked_late, [pick])
    _ledger(picked_late, "sha-early", "2099-09-13T12:00:00Z")
    _grade(picked_late)
    before = {key: _summary(picked_late)[key] for key in COUNT_KEYS}

    _write(
        picked_late,
        [pick, _pick(picked_at="2099-09-13T17:00:00Z", action="void")],
    )
    _ledger(picked_late, "sha-void", "2099-09-13T16:00:00Z")
    _grade(picked_late)
    _assert_stood(picked_late, before)

    pushed_late = _repo(tmp_path / "pushed-late")
    _write(pushed_late, [pick])
    _ledger(pushed_late, "sha-early", "2099-09-13T12:00:00Z")
    _grade(pushed_late)
    before_push = {key: _summary(pushed_late)[key] for key in COUNT_KEYS}
    _write(
        pushed_late,
        [pick, _pick(picked_at="2099-09-13T16:00:00Z", action="void")],
    )
    _ledger(pushed_late, "sha-late", "2099-09-13T17:03:00Z")
    _grade(pushed_late)
    _assert_stood(pushed_late, before_push)


def test_no_voids_leave_summary_totals_unchanged(tmp_path: Path):
    """The season-2099 fixture has no void action.

    Absolute W-L, units, and CLV are hand-calculated in
    test_grade_fixture.test_fixture_grades_and_summary. Here the summary
    totals are exactly the graded rows, and a second grade does not move them.
    """
    root = prepare_repo(tmp_path)
    graded = run_cli(grade_args(root))
    assert graded.returncode == 0, graded.stderr + graded.stdout
    view = _view(root)
    assert view
    assert all(row["status"] != "void" for row in view)
    assert all(row["voided_at"] is None for row in view)
    assert _voids_by_week(view) == {}

    summary = _summary(root)
    counted = [row for row in view if row["status"] == "graded"]
    assert summary["void_count"] == 0
    assert summary["w"] == sum(row["result"] == "W" for row in counted)
    assert summary["l"] == sum(row["result"] == "L" for row in counted)
    assert summary["p"] == sum(row["result"] == "P" for row in counted)
    units = sum((Decimal(str(row["units_pl"])) for row in counted), start=Decimal(0))
    assert Decimal(str(summary["units"])) == units.quantize(Decimal("0.001"))
    clvs = [Decimal(str(row["clv_pts"])) for row in counted if row["clv_pts"] is not None]
    assert Decimal(str(summary["avg_clv"])) == (sum(clvs, start=Decimal(0)) / Decimal(len(clvs))).quantize(
        Decimal("0.001")
    )

    before = (root / "src" / "data" / "summary.json").read_bytes()
    again = run_cli(grade_args(root))
    assert again.returncode == 0, again.stderr + again.stdout
    assert (root / "src" / "data" / "summary.json").read_bytes() == before
    assert all(row["status"] != "void" for row in _view(root))


def _assert_stood(root: Path, before: dict) -> None:
    view = _view(root)
    assert [row["pick_id"] for row in view] == [BASE]
    row = view[0]
    assert row["status"] == "graded"
    assert row["result"] == "W"
    assert row["voided_at"] is None
    assert row["units_pl"] == 0.909
    assert _summary(root)["void_count"] == 0
    assert {key: _summary(root)[key] for key in COUNT_KEYS} == before
    assert _voids_by_week(view) == {}


def _voids_by_week(rows: list[dict]) -> dict[int, list[dict]]:
    """The slate array from the view only, grouped by week. See pick-intake.md."""
    by_id = {row["pick_id"]: row for row in rows}
    grouped: dict[int, list[dict]] = {}
    for row in rows:
        if row["status"] != "void":
            continue
        nxt = _next_pick_id(row["pick_id"], row["game_id"], row["market"])
        repick = by_id.get(nxt) if nxt else None
        if repick is None or repick["status"] == "rejected_late":
            attached = None
        else:
            attached = {
                "pick_id": repick["pick_id"],
                "side": repick["side"],
                "line_at_pick": repick["line_at_pick"],
                "picked_at": repick["picked_at"],
            }
        grouped.setdefault(row["week"], []).append(
            {
                "game_id": row["game_id"],
                "week": row["week"],
                "pick": {
                    "side": row["side"],
                    "market": row["market"],
                    "line_at_pick": row["line_at_pick"],
                    "picked_at": row["picked_at"],
                },
                "voided_at": row["voided_at"],
                "repick": attached,
            }
        )
    return grouped


def _next_pick_id(pick_id: str, game_id: str, market: str) -> str | None:
    base = f"{game_id}-{market}"
    if pick_id == base:
        return f"{base}-r2"
    prefix = f"{base}-r"
    if len(pick_id) != len(prefix) + 1 or not pick_id.startswith(prefix):
        return None
    digit = pick_id[-1]
    if digit not in "2345678":
        return None
    return f"{base}-r{int(digit) + 1}"


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "data").mkdir(parents=True)
    (root / "src" / "data").mkdir(parents=True)
    (root / "data" / "schedules.csv").write_bytes((FIXTURES / "schedules.csv").read_bytes())
    return root


def _pick(
    *,
    picked_at: str,
    note: str = "lock",
    action: str = "pick",
    confidence: str = "2",
) -> dict[str, str]:
    return {
        "pick_id": BASE,
        "game_id": GAME,
        "season": "2099",
        "week": "1",
        "market": "spread",
        "side": "DET",
        "line_at_pick": "2.5",
        "picked_at": picked_at,
        "units": "1.0",
        "confidence": confidence,
        "tags": "",
        "note": note,
        "action": action,
    }


def _write(root: Path, rows: list[dict[str, str]]) -> None:
    path = root / "data" / "picks.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADER, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _ledger(root: Path, sha: str, timestamp: str) -> None:
    activity = root / f"activity-{sha}.json"
    activity.write_text(
        json.dumps(
            [
                {
                    "activity_type": "push",
                    "after": sha,
                    "timestamp": timestamp,
                    "committer_date": "2098-01-01T00:00:00Z",
                }
            ]
        ),
        encoding="utf-8",
    )
    result = run_cli(
        [
            "ledger",
            "--root",
            str(root),
            "--schedules",
            str(root / "data" / "schedules.csv"),
            "--activity",
            str(activity),
        ],
        env={
            "GITHUB_SHA": sha,
            "GITHUB_REPOSITORY": "octocat/football-film-room",
            "GITHUB_TOKEN": "test-token",
        },
    )
    assert result.returncode == 0, result.stderr + result.stdout


def _grade(root: Path) -> None:
    result = run_cli(
        [
            "grade",
            "--root",
            str(root),
            "--as-of",
            "2099-09-20T00:00:00Z",
            "--schedules",
            str(root / "data" / "schedules.csv"),
        ]
    )
    assert result.returncode == 0, result.stderr + result.stdout


def _summary(root: Path) -> dict:
    return json.loads((root / "src" / "data" / "summary.json").read_text(encoding="utf-8"))


def _view(root: Path) -> list[dict]:
    return json.loads((root / "src" / "data" / "picks_view.json").read_text(encoding="utf-8"))
