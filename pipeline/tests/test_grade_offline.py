"""Offline republish of picks_view.json and summary.json.

grade --no-fetch uses the same grader as weekly and does not touch the network.
A rerun on an unchanged ledger leaves the published bytes alone.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from tests.conftest import grade_args, prepare_repo, run_cli

ROOT = Path(__file__).resolve().parents[2]
COUNT_KEYS = ("w", "l", "p", "units")


def test_unchanged_ledger_republish_is_byte_identical(tmp_path: Path):
    root = prepare_repo(tmp_path)
    graded = run_cli(grade_args(root))
    assert graded.returncode == 0, graded.stderr + graded.stdout
    before = _bytes(root)

    first = run_cli(["grade", "--root", str(root), "--no-fetch"])
    assert first.returncode == 0, first.stderr + first.stdout
    assert _bytes(root) == before

    second = run_cli(["grade", "--root", str(root), "--no-fetch"])
    assert second.returncode == 0, second.stderr + second.stdout
    assert _bytes(root) == before


def test_pending_pick_keeps_summary_counts_and_a_void_updates_the_view(tmp_path: Path):
    root = prepare_repo(tmp_path)
    graded = run_cli(grade_args(root))
    assert graded.returncode == 0, graded.stderr + graded.stdout
    before_summary = _summary(root)
    before_counts = {key: before_summary[key] for key in COUNT_KEYS}
    before_status = {row["pick_id"]: row["status"] for row in _view(root)}
    graded_csv = (root / "data" / "graded.csv").read_bytes()

    _card(
        root,
        "2099_02_GB_CHI",
        kickoff="2099-09-21T17:00:00Z",
        away="GB",
        home="CHI",
        season=2099,
        week=2,
    )
    _append(
        root / "data" / "picks.csv",
        "2099_02_GB_CHI-spread,2099_02_GB_CHI,2099,2,spread,GB,3.0,2099-09-20T12:00:00Z,1.0,2,,offline pending,pick",
    )
    pending = run_cli(["grade", "--root", str(root), "--no-fetch"])
    assert pending.returncode == 0, pending.stderr + pending.stdout

    view = {row["pick_id"]: row for row in _view(root)}
    row = view["2099_02_GB_CHI-spread"]
    assert row["status"] == "pending"
    assert row["result"] is None
    assert row["first_pushed_at"] is None
    assert row["voided_at"] is None
    assert row["side"] == "GB"
    for pick_id, status in before_status.items():
        assert view[pick_id]["status"] == status
    summary = _summary(root)
    assert {key: summary[key] for key in COUNT_KEYS} == before_counts
    assert summary["generated_at"] == before_summary["generated_at"]
    assert summary["void_count"] == before_summary["void_count"]
    assert (root / "data" / "graded.csv").read_bytes() == graded_csv

    _append(
        root / "data" / "picks.csv",
        "2099_02_GB_CHI-spread,2099_02_GB_CHI,2099,2,spread,GB,3.0,2099-09-20T15:00:00Z,1.0,2,,offline void,void",
    )
    voided = run_cli(["grade", "--root", str(root), "--no-fetch"])
    assert voided.returncode == 0, voided.stderr + voided.stdout
    after = {row["pick_id"]: row for row in _view(root)}
    assert after["2099_02_GB_CHI-spread"]["status"] == "void"
    assert after["2099_02_GB_CHI-spread"]["voided_at"] == "2099-09-20T15:00:00Z"
    assert after["2099_02_GB_CHI-spread"]["result"] is None
    for pick_id, status in before_status.items():
        assert after[pick_id]["status"] == status
    summary = _summary(root)
    assert {key: summary[key] for key in COUNT_KEYS} == before_counts
    assert summary["generated_at"] == before_summary["generated_at"]
    assert summary["void_count"] == before_summary["void_count"] + 1
    assert (root / "data" / "graded.csv").read_bytes() == graded_csv

    published = _bytes(root)
    rerun = run_cli(["grade", "--root", str(root), "--no-fetch"])
    assert rerun.returncode == 0, rerun.stderr + rerun.stdout
    assert _bytes(root) == published


def test_card_kickoff_rejects_a_late_pick_without_a_fetch(tmp_path: Path):
    root = prepare_repo(tmp_path)
    graded = run_cli(grade_args(root))
    assert graded.returncode == 0, graded.stderr + graded.stdout
    _card(
        root,
        "2099_03_GB_CHI",
        kickoff="2099-09-01T17:00:00Z",
        away="GB",
        home="CHI",
        season=2099,
        week=3,
    )
    _append(
        root / "data" / "picks.csv",
        "2099_03_GB_CHI-spread,2099_03_GB_CHI,2099,3,spread,CHI,-3.0,2099-09-01T12:00:00Z,1.0,1,,before a past kickoff,pick",
    )
    result = run_cli(["grade", "--root", str(root), "--no-fetch"])
    assert result.returncode == 0, result.stderr + result.stdout
    row = next(item for item in _view(root) if item["pick_id"] == "2099_03_GB_CHI-spread")
    assert row["status"] == "rejected_late"
    assert row["first_pushed_at"] is None


def test_bad_card_exits_without_touching_published_bytes(tmp_path: Path):
    root = prepare_repo(tmp_path)
    graded = run_cli(grade_args(root))
    assert graded.returncode == 0, graded.stderr + graded.stdout
    before = _bytes(root)
    games = root / "src" / "content" / "games"
    games.mkdir(parents=True)
    (games / "2099_05_GB_CHI.json").write_text(
        json.dumps({"game_id": "2099_05_GB_CHI", "season": 2099, "week": 5, "away": "GB", "home": "CHI"}),
        encoding="utf-8",
    )
    _append(
        root / "data" / "picks.csv",
        "2099_05_GB_CHI-spread,2099_05_GB_CHI,2099,5,spread,GB,3.0,2099-09-20T12:00:00Z,1.0,1,,bad card,pick",
    )
    result = run_cli(["grade", "--root", str(root), "--no-fetch"])
    assert result.returncode == 1
    assert "card has no kickoff" in result.stderr
    assert _bytes(root) == before


def test_missing_card_still_publishes_a_pending_pick(tmp_path: Path):
    root = prepare_repo(tmp_path)
    graded = run_cli(grade_args(root))
    assert graded.returncode == 0, graded.stderr + graded.stdout
    _append(
        root / "data" / "picks.csv",
        "2099_04_DAL_ARI-total,2099_04_DAL_ARI,2099,4,total,under,44.5,2099-09-20T12:00:00Z,1.0,1,,no card,pick",
    )
    result = run_cli(["grade", "--root", str(root), "--no-fetch"])
    assert result.returncode == 0, result.stderr + result.stdout
    row = next(item for item in _view(root) if item["pick_id"] == "2099_04_DAL_ARI-total")
    assert row["status"] == "pending"
    assert "network" not in (result.stderr + result.stdout).lower()


def test_committed_public_view_survives_a_rerun(tmp_path: Path):
    """The files weekly already published must not churn, including fractional ages."""
    root = tmp_path / "repo"
    for relative in (
        "data/picks.csv",
        "data/pick_ledger.csv",
        "data/graded.csv",
        "src/data/summary.json",
        "src/data/picks_view.json",
    ):
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / relative, destination)
    games = ROOT / "src" / "content" / "games"
    if games.is_dir():
        shutil.copytree(games, root / "src" / "content" / "games")
    before = _bytes(root)
    before_summary = _summary(root)

    first = run_cli(["grade", "--root", str(root), "--no-fetch"])
    assert first.returncode == 0, first.stderr + first.stdout
    assert _bytes(root) == before

    pending = next((row for row in _view(root) if row["status"] == "pending"), None)
    if pending is None:
        return
    card_path = root / "src" / "content" / "games" / f"{pending['game_id']}.json"
    if not card_path.is_file():
        return
    kickoff = json.loads(card_path.read_text(encoding="utf-8"))["kickoff_utc"]
    void_at = _before(kickoff)
    _append(
        root / "data" / "picks.csv",
        ",".join(
            [
                pending["pick_id"],
                pending["game_id"],
                str(pending["season"]),
                str(pending["week"]),
                pending["market"],
                pending["side"],
                str(pending["line_at_pick"]),
                void_at,
                "1.0",
                "" if pending["confidence"] is None else str(pending["confidence"]),
                pending["tags"],
                "offline void",
                "void",
            ]
        ),
    )
    voided = run_cli(["grade", "--root", str(root), "--no-fetch"])
    assert voided.returncode == 0, voided.stderr + voided.stdout
    row = next(item for item in _view(root) if item["pick_id"] == pending["pick_id"])
    assert row["status"] == "void"
    assert row["voided_at"] == void_at
    assert row["closing_line"] == pending["closing_line"]
    assert row["close_source"] == pending["close_source"]
    assert row["close_age_min"] == pending["close_age_min"]
    summary = _summary(root)
    assert {key: summary[key] for key in COUNT_KEYS} == {key: before_summary[key] for key in COUNT_KEYS}
    assert summary["generated_at"] == before_summary["generated_at"]
    assert summary["void_count"] == before_summary["void_count"] + 1

    published = _bytes(root)
    rerun = run_cli(["grade", "--root", str(root), "--no-fetch"])
    assert rerun.returncode == 0, rerun.stderr + rerun.stdout
    assert _bytes(root) == published


def _before(kickoff: str) -> str:
    """An hour before kickoff, as a Z timestamp the grader will accept."""
    from datetime import datetime, timedelta, timezone

    parsed = datetime.fromisoformat(kickoff.replace("Z", "+00:00")).astimezone(timezone.utc)
    return (parsed - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _card(
    root: Path,
    game_id: str,
    *,
    kickoff: str,
    away: str,
    home: str,
    season: int,
    week: int,
) -> None:
    path = root / "src" / "content" / "games" / f"{game_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "game_id": game_id,
                "season": season,
                "week": week,
                "kickoff_utc": kickoff,
                "away": away,
                "home": home,
            }
        ),
        encoding="utf-8",
    )


def _append(path: Path, line: str) -> None:
    text = path.read_text(encoding="utf-8")
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(text + line + "\n", encoding="utf-8")


def _summary(root: Path) -> dict:
    return json.loads((root / "src" / "data" / "summary.json").read_text(encoding="utf-8"))


def _view(root: Path) -> list[dict]:
    return json.loads((root / "src" / "data" / "picks_view.json").read_text(encoding="utf-8"))


def _bytes(root: Path) -> dict[str, bytes]:
    paths = [
        root / "data" / "graded.csv",
        root / "src" / "data" / "summary.json",
        root / "src" / "data" / "picks_view.json",
    ]
    return {str(path.relative_to(root)): path.read_bytes() for path in paths}
