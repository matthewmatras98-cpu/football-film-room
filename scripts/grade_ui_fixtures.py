#!/usr/bin/env python3
"""Build the picks-page fixtures by running T1's grader.

`tests/fixtures/summary.json` and `tests/fixtures/picks_view.json` are the
grader's own bytes. The inputs are `pipeline/tests/fixtures/` (the 12-pick
fixture) plus one B2 row under `tests/fixtures/b2/` so `odds_api_stale`
appears in the view. That extra row is rejected late, and this script fails
if adding it changes the graded totals.

Nothing under `pipeline/` or `data/` is modified. Grading runs in a temp
repo via `python -m football_pipeline`.

  python3 scripts/grade_ui_fixtures.py --check
  python3 scripts/grade_ui_fixtures.py --write
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PIPELINE = REPO / "pipeline"
T1 = PIPELINE / "tests" / "fixtures"
B2 = REPO / "tests" / "fixtures" / "b2"
OUT_SUMMARY = REPO / "tests" / "fixtures" / "summary.json"
OUT_VIEW = REPO / "tests" / "fixtures" / "picks_view.json"

EARLY_SHA = "a" * 40
LATE_SHA = "b" * 40
EARLY_TS = "2099-09-13T12:00:00Z"
LATE_TS = "2099-09-13T17:03:00Z"
AS_OF = "2099-09-20T00:00:00Z"
LATE_PREFIX = "2099_01_CIN_CLE-spread,"

# Graded-record fields. The rejected stale row must not move these.
TOTAL_KEYS = (
    "season",
    "w",
    "l",
    "p",
    "win_pct",
    "units",
    "avg_clv",
    "pct_clv_pos",
    "ungraded_count",
    "generated_at",
)

STATUSES = {"graded", "pending", "ungraded", "rejected_late"}
SOURCES = {"odds_api", "odds_api_thin", "odds_api_stale", "nflverse", None}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="fail if committed fixtures differ")
    mode.add_argument("--write", action="store_true", help="rewrite the committed fixtures")
    args = parser.parse_args()

    pure = grade_tree(include_b2=False)
    extended = grade_tree(include_b2=True)
    assert_totals_unchanged(pure["summary"], extended["summary"])
    assert_coverage(extended["view"])

    if args.write:
        OUT_SUMMARY.write_bytes(extended["summary_bytes"])
        OUT_VIEW.write_bytes(extended["view_bytes"])
        print(f"wrote {OUT_SUMMARY.relative_to(REPO)}")
        print(f"wrote {OUT_VIEW.relative_to(REPO)}")
        return 0

    failed = False
    failed |= report_diff(OUT_SUMMARY, extended["summary_bytes"])
    failed |= report_diff(OUT_VIEW, extended["view_bytes"])
    if failed:
        print(
            "pick fixtures are stale. Regenerate with: python3 scripts/grade_ui_fixtures.py --write",
            file=sys.stderr,
        )
        return 1
    print("pick fixtures match the grader")
    return 0


def grade_tree(*, include_b2: bool) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "repo"
        schedules, picks_text = _materialize(root, include_b2)
        _ledger(root, schedules, picks_text, EARLY_SHA, EARLY_TS, include_late=False)
        _ledger(root, schedules, picks_text, LATE_SHA, LATE_TS, include_late=True)
        _run(
            [
                "grade",
                "--root",
                str(root),
                "--as-of",
                AS_OF,
                "--schedules",
                str(schedules),
                "--snapshots",
                str(root / "data" / "snapshot_closes.csv"),
            ],
            {},
        )
        summary_path = root / "src" / "data" / "summary.json"
        view_path = root / "src" / "data" / "picks_view.json"
        summary_bytes = summary_path.read_bytes()
        view_bytes = view_path.read_bytes()
        return {
            "summary": json.loads(summary_bytes.decode("utf-8")),
            "view": json.loads(view_bytes.decode("utf-8")),
            "summary_bytes": summary_bytes,
            "view_bytes": view_bytes,
        }


def _materialize(root: Path, include_b2: bool) -> tuple[Path, str]:
    data = root / "data"
    (root / "src" / "data").mkdir(parents=True)
    data.mkdir(parents=True)
    picks = _concat(T1 / "picks.csv", B2 / "picks.csv" if include_b2 else None)
    schedules = _concat(T1 / "schedules.csv", B2 / "schedules.csv" if include_b2 else None)
    snapshots = _concat(
        T1 / "snapshot_closes.csv",
        B2 / "snapshot_closes.csv" if include_b2 else None,
    )
    schedules_path = data / "schedules.csv"
    schedules_path.write_text(schedules, encoding="utf-8")
    (data / "snapshot_closes.csv").write_text(snapshots, encoding="utf-8")
    return schedules_path, picks


def _concat(base: Path, extra: Path | None) -> str:
    text = base.read_text(encoding="utf-8")
    if not text.endswith("\n"):
        text += "\n"
    if extra is None:
        return text
    rows = [line for line in extra.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(rows) < 2:
        raise SystemExit(f"{extra} needs a header and at least one row")
    return text + "\n".join(rows[1:]) + "\n"


def _ledger(
    root: Path,
    schedules: Path,
    picks_text: str,
    sha: str,
    timestamp: str,
    *,
    include_late: bool,
) -> None:
    picks_path = root / "data" / "picks.csv"
    header, early, late = _split_picks(picks_text)
    body = early + late if include_late else early
    picks_path.write_text(header + "\n" + "\n".join(body) + "\n", encoding="utf-8")
    activity = root / f"activity-{sha[:8]}.json"
    activity.write_text(
        json.dumps(
            [
                {
                    "activity_type": "push",
                    "after": sha,
                    "timestamp": timestamp,
                    "ref": "refs/heads/main",
                    "author_date": "2098-01-01T00:00:00Z",
                    "committer_date": "2098-01-01T00:00:00Z",
                }
            ]
        ),
        encoding="utf-8",
    )
    _run(
        [
            "ledger",
            "--root",
            str(root),
            "--schedules",
            str(schedules),
            "--activity",
            str(activity),
        ],
        {
            "GITHUB_SHA": sha,
            "GITHUB_REPOSITORY": "octocat/football-film-room",
            "GITHUB_TOKEN": "test-token",
        },
    )


def _split_picks(text: str) -> tuple[str, list[str], list[str]]:
    lines = [line for line in text.splitlines() if line.strip()]
    header, *rows = lines
    early = [row for row in rows if not row.startswith(LATE_PREFIX)]
    late = [row for row in rows if row.startswith(LATE_PREFIX)]
    if len(late) != 1:
        raise SystemExit(f"expected one late ledger row, found {len(late)}")
    return header, early, late


def _run(args: list[str], github: dict[str, str]) -> None:
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("uv is required (0.13.7, the version pinned in ci.yml)")
    env = os.environ.copy()
    env["NFLREADPY_CACHE"] = "off"
    env.pop("GITHUB_TOKEN", None)
    env.pop("GITHUB_SHA", None)
    env.pop("GITHUB_REPOSITORY", None)
    env.update(github)
    result = subprocess.run(
        [uv, "run", "--directory", str(PIPELINE), "--frozen", "python", "-m", "football_pipeline", *args],
        cwd=PIPELINE,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(result.stderr + result.stdout)


def assert_totals_unchanged(pure: dict, extended: dict) -> None:
    for key in TOTAL_KEYS:
        if pure.get(key) != extended.get(key):
            raise SystemExit(
                f"B2 stale row changed grader total {key}: {pure.get(key)!r} -> {extended.get(key)!r}"
            )
    if extended["rejected_count"] != pure["rejected_count"] + 1:
        raise SystemExit(
            "B2 stale row should add exactly one rejected pick: "
            f"{pure['rejected_count']} -> {extended['rejected_count']}"
        )


def assert_coverage(view: list[dict]) -> None:
    statuses = {row["status"] for row in view}
    sources = {row["close_source"] for row in view}
    if statuses != STATUSES:
        raise SystemExit(f"fixture statuses are {statuses}, expected {STATUSES}")
    if sources != SOURCES:
        raise SystemExit(f"fixture close sources are {sources}, expected {SOURCES}")
    markets = {row["market"] for row in view}
    sides = {row["side"] for row in view}
    if markets != {"spread", "total"}:
        raise SystemExit(f"fixture markets are {markets}")
    if "over" not in sides or "under" not in sides:
        raise SystemExit(f"fixture sides are missing a total side: {sides}")
    if not any(row["result"] == "P" for row in view):
        raise SystemExit("fixture has no push")
    clvs = [row["clv_pts"] for row in view if row["clv_pts"] is not None]
    if not any(value > 0 for value in clvs) or not any(value < 0 for value in clvs):
        raise SystemExit(f"fixture CLV does not cover both signs: {clvs}")
    stale = [row for row in view if row["close_source"] == "odds_api_stale"]
    if len(stale) != 1 or stale[0]["status"] != "rejected_late":
        raise SystemExit("the B2 stale row must be the only stale pick and must be rejected_late")


def report_diff(path: Path, actual: bytes) -> bool:
    if not path.is_file():
        print(f"missing {path.relative_to(REPO)}", file=sys.stderr)
        return True
    expected = path.read_bytes()
    if expected == actual:
        return False
    diff = difflib.unified_diff(
        expected.decode("utf-8").splitlines(True),
        actual.decode("utf-8").splitlines(True),
        fromfile=str(path.relative_to(REPO)),
        tofile="grader",
    )
    sys.stderr.writelines(diff)
    return True


if __name__ == "__main__":
    raise SystemExit(main())
