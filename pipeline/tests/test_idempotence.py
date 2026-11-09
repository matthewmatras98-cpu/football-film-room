"""Re-running grade does not change a byte. A second ledger appends nothing."""

from __future__ import annotations

import hashlib
import random
from pathlib import Path

from football_pipeline.ledger import load_ledger
from tests.conftest import LATE_SHA, grade_args, prepare_repo, run_cli


def test_grade_is_byte_identical_across_reruns_shuffles_and_directories(tmp_path: Path):
    root = prepare_repo(tmp_path / "first")
    first = _grade(root, "2099-09-20T00:00Z")
    second = _grade(root, "2099-09-20T00:00:00Z")
    assert second == first

    picks = root / "data" / "picks.csv"
    header, *rows = picks.read_text(encoding="utf-8").splitlines()
    shuffled = rows.copy()
    random.Random(0).shuffle(shuffled)
    assert shuffled != rows
    picks.write_text(header + "\n" + "\n".join(shuffled) + "\n", encoding="utf-8")
    assert _grade(root) == first

    fresh = prepare_repo(tmp_path / "fresh")
    assert _grade(fresh) == first

    for path in (
        root / "data" / "graded.csv",
        root / "src" / "data" / "summary.json",
        root / "src" / "data" / "picks_view.json",
    ):
        assert b"\r" not in path.read_bytes()


def test_second_ledger_run_appends_zero_rows(tmp_path: Path):
    root = prepare_repo(tmp_path)
    ledger = root / "data" / "pick_ledger.csv"
    before = ledger.read_bytes()
    count = len(load_ledger(ledger))
    activity = root / "activity-late.json"
    again = run_cli(
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
            "GITHUB_SHA": LATE_SHA,
            "GITHUB_REPOSITORY": "octocat/football-film-room",
            "GITHUB_TOKEN": "test-token",
        },
    )
    assert again.returncode == 0, again.stderr + again.stdout
    assert "0 rows appended" in again.stdout
    assert ledger.read_bytes() == before
    assert len(load_ledger(ledger)) == count


def _grade(root: Path, as_of: str = "2099-09-20T00:00Z") -> dict[str, str]:
    result = run_cli(grade_args(root, as_of))
    assert result.returncode == 0, result.stderr + result.stdout
    paths = [
        root / "data" / "graded.csv",
        root / "src" / "data" / "summary.json",
        root / "src" / "data" / "picks_view.json",
    ]
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
