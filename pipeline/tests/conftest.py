"""Shared fixture setup. Tests never call nflverse or the Odds API."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PIPELINE = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
EARLY_SHA = "a" * 40
LATE_SHA = "b" * 40
EARLY_TS = "2099-09-13T12:00:00Z"
LATE_TS = "2099-09-13T17:03:00Z"
P10_PREFIX = "2099_01_CIN_CLE-spread,"


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    import urllib.request

    block_dir = str(Path(__file__).resolve().parent)
    prior = os.environ.get("PYTHONPATH", "")
    monkeypatch.setenv("PYTHONPATH", block_dir + (os.pathsep + prior if prior else ""))
    monkeypatch.setenv("FOOTBALL_BLOCK_NETWORK", "1")

    def blocked(*args, **kwargs):
        raise AssertionError(f"unexpected network call: {args!r}")

    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    try:
        import requests
    except ImportError:
        return

    def blocked_request(self, *args, **kwargs):
        raise AssertionError(f"unexpected HTTP request: {args!r}")

    monkeypatch.setattr(requests.sessions.Session, "request", blocked_request)


def run_cli(args: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    merged["NFLREADPY_CACHE"] = "off"
    block_dir = str(Path(__file__).resolve().parent)
    prior = merged.get("PYTHONPATH", "")
    merged["PYTHONPATH"] = block_dir + (os.pathsep + prior if prior else "")
    merged["FOOTBALL_BLOCK_NETWORK"] = "1"
    merged.pop("GITHUB_TOKEN", None)
    merged.pop("GITHUB_SHA", None)
    merged.pop("GITHUB_REPOSITORY", None)
    if env:
        merged.update(env)
    return subprocess.run(
        [sys.executable, "-m", "football_pipeline", *args],
        cwd=PIPELINE,
        env=merged,
        capture_output=True,
        text=True,
        check=False,
    )


def write_activity(path: Path, sha: str, timestamp: str) -> None:
    path.write_text(
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


def prepare_repo(tmp_path: Path) -> Path:
    """Copy the season-2099 fixture and ledger every row except P10 early.

    P10 is appended on a second push whose server time is after kickoff.
    """
    root = tmp_path / "repo"
    (root / "data").mkdir(parents=True)
    (root / "src" / "data").mkdir(parents=True)
    schedules = FIXTURES / "schedules.csv"
    snapshots = FIXTURES / "snapshot_closes.csv"
    (root / "data" / "schedules.csv").write_bytes(schedules.read_bytes())
    (root / "data" / "snapshot_closes.csv").write_bytes(snapshots.read_bytes())

    picks_text = (FIXTURES / "picks.csv").read_text(encoding="utf-8")
    header, *rows = [line for line in picks_text.splitlines() if line.strip()]
    early = [row for row in rows if not row.startswith(P10_PREFIX)]
    late = [row for row in rows if row.startswith(P10_PREFIX)]
    assert len(late) == 1
    (root / "data" / "picks.csv").write_text(
        header + "\n" + "\n".join(early) + "\n",
        encoding="utf-8",
    )
    early_activity = root / "activity-early.json"
    write_activity(early_activity, EARLY_SHA, EARLY_TS)
    first = run_cli(
        ["ledger", "--root", str(root), "--schedules", str(schedules), "--activity", str(early_activity)],
        env=_github_env(EARLY_SHA),
    )
    assert first.returncode == 0, first.stderr + first.stdout

    (root / "data" / "picks.csv").write_text(picks_text if picks_text.endswith("\n") else picks_text + "\n", encoding="utf-8")
    late_activity = root / "activity-late.json"
    write_activity(late_activity, LATE_SHA, LATE_TS)
    second = run_cli(
        ["ledger", "--root", str(root), "--schedules", str(schedules), "--activity", str(late_activity)],
        env=_github_env(LATE_SHA),
    )
    assert second.returncode == 0, second.stderr + second.stdout
    return root


def grade_args(root: Path, as_of: str = "2099-09-20T00:00:00Z") -> list[str]:
    return [
        "grade",
        "--root",
        str(root),
        "--as-of",
        as_of,
        "--schedules",
        str(root / "data" / "schedules.csv"),
        "--snapshots",
        str(root / "data" / "snapshot_closes.csv"),
    ]


def _github_env(sha: str) -> dict[str, str]:
    return {
        "GITHUB_SHA": sha,
        "GITHUB_REPOSITORY": "octocat/football-film-room",
        "GITHUB_TOKEN": "test-token",
    }
