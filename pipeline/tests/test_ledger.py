"""Server push time, append-only hashes, and the late-lock rules."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from football_pipeline.ledger import find_push_timestamp, load_ledger, poll_push_timestamp
from football_pipeline.schema import canonical_row, format_ts, row_sha
from tests.conftest import FIXTURES, run_cli

UTC = timezone.utc
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


def test_ignores_git_author_and_committer_dates():
    stamped = find_push_timestamp(
        [
            {
                "activity_type": "pr_merge",
                "after": "abc",
                "timestamp": "2099-09-13T18:00:00Z",
            },
            {
                "activity_type": "push",
                "after": "abc",
                "timestamp": "2099-09-13T12:00:00Z",
                "author_date": "2098-01-01T00:00:00Z",
                "committer_date": "2098-01-01T00:00:00Z",
            },
        ],
        "abc",
    )
    assert format_ts(stamped) == "2099-09-13T12:00:00Z"


def test_missing_timestamp_does_not_fall_back_to_commit_dates():
    try:
        find_push_timestamp(
            [
                {
                    "activity_type": "push",
                    "after": "abc",
                    "author_date": "2099-01-01T00:00:00Z",
                    "committer_date": "2099-01-01T00:00:00Z",
                }
            ],
            "abc",
        )
    except Exception as exc:
        assert "author" in str(exc)
    else:
        raise AssertionError("expected a missing timestamp to fail")


def test_retries_then_finds_the_push_and_gives_up_without_git_dates():
    calls = {"n": 0}

    def fetch_pages():
        calls["n"] += 1
        if calls["n"] < 3:
            return []
        return [{"activity_type": "push", "after": "abc", "timestamp": "2099-09-13T12:00:00Z"}]

    clock = {"t": 0.0}

    def monotonic():
        return clock["t"]

    def sleep(seconds):
        clock["t"] += seconds

    found = poll_push_timestamp("abc", fetch_pages, sleep, monotonic, timeout_s=120)
    assert format_ts(found) == "2099-09-13T12:00:00Z"
    assert calls["n"] == 3

    def never():
        return [{"activity_type": "push", "after": "other", "timestamp": "2099-09-13T12:00:00Z"}]

    clock["t"] = 0.0
    try:
        poll_push_timestamp("abc", never, sleep, monotonic, timeout_s=120)
    except Exception as exc:
        assert "refusing to use git author or committer dates" in str(exc)
    else:
        raise AssertionError("expected the poll to time out")
    assert clock["t"] >= 120


def test_activity_request_matches_sha_in_the_body_not_the_query(monkeypatch):
    seen = {"urls": []}

    def fake(url, headers):
        seen["urls"].append(url)
        seen["headers"] = headers
        if "page=2" in url:
            payload = [{"activity_type": "push", "after": "abc", "timestamp": "2099-09-13T12:00:00Z"}]
            return payload, {}, 200
        return [], {'Link': '<https://api.github.com/repos/o/r/activity?page=2>; rel="next"'}, 200

    monkeypatch.setattr("football_pipeline.ledger.http_json", fake)
    from football_pipeline.ledger import fetch_push_timestamp_from_github

    found = fetch_push_timestamp_from_github("o", "r", "abc", "secret-token", timeout_s=5)
    assert format_ts(found) == "2099-09-13T12:00:00Z"
    assert "activity_type=push" in seen["urls"][0]
    assert all("after=abc" not in url for url in seen["urls"])
    assert all("secret-token" not in url for url in seen["urls"])
    assert seen["headers"]["Authorization"] == "Bearer secret-token"


def test_edited_row_gets_a_new_hash_and_is_rejected_late(tmp_path: Path):
    root = _repo(tmp_path)
    original = _pick(line="-3.0")
    _write(root, [original])
    _ledger(root, "sha-early", "2099-09-13T12:00:00Z")
    edited = _pick(line="-7.0")
    _write(root, [edited])
    _ledger(root, "sha-late", "2099-09-13T17:03:00Z")

    rows = load_ledger(root / "data" / "pick_ledger.csv")
    assert [row.row_sha for row in rows] == [row_sha(original), row_sha(edited)]
    assert row_sha(original) != row_sha(edited)
    assert format_ts(rows[0].first_pushed_at) == "2099-09-13T12:00:00Z"
    assert format_ts(rows[1].first_pushed_at) == "2099-09-13T17:03:00Z"
    assert rows[1].first_pushed_at >= datetime(2099, 9, 13, 17, 0, tzinfo=UTC)

    _grade(root, "2099-09-20T00:00:00Z")
    graded = _graded_by_id(root)
    assert graded["2099_01_DET_PHI-spread"]["status"] == "rejected_late"
    assert graded["2099_01_DET_PHI-spread"]["result"] == ""


def test_missing_ledger_after_kickoff_is_rejected_and_before_is_not(tmp_path: Path):
    root = _repo(tmp_path)
    _write(root, [_pick(line="-3.0")])
    _grade(root, "2099-09-20T00:00:00Z")
    assert _graded_by_id(root)["2099_01_DET_PHI-spread"]["status"] == "rejected_late"
    assert not (root / "data" / "pick_ledger.csv").exists()

    early = _repo(tmp_path / "early")
    _write(early, [_pick(line="-3.0", picked_at="2099-09-13T15:00:00Z")])
    _grade(early, "2099-09-13T16:00:00Z")
    assert _graded_by_id(early)["2099_01_DET_PHI-spread"]["status"] == "graded"


def test_picked_at_equal_to_kickoff_is_rejected_even_with_an_early_push(tmp_path: Path):
    root = _repo(tmp_path)
    _write(root, [_pick(line="-3.0", picked_at="2099-09-13T13:00:00-04:00")])
    _ledger(root, "sha-early", "2099-09-13T12:00:00Z")
    _grade(root, "2099-09-13T16:00:00Z")
    row = _graded_by_id(root)["2099_01_DET_PHI-spread"]
    assert row["status"] == "rejected_late"


def test_canonical_row_quotes_commas_and_changes_when_edited():
    base = _pick(line="-3.0")
    base["note"] = 'say "hi", pal'
    encoded = canonical_row(base)
    assert '"say ""hi"", pal"' in encoded
    changed = dict(base)
    changed["note"] = "say hi pal"
    assert row_sha(base) != row_sha(changed)


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "data").mkdir(parents=True)
    (root / "src" / "data").mkdir(parents=True)
    schedules = root / "data" / "schedules.csv"
    schedules.write_bytes((FIXTURES / "schedules.csv").read_bytes())
    return root


def _pick(line: str, picked_at: str = "2099-09-13T15:00:00Z") -> dict[str, str]:
    return {
        "pick_id": "2099_01_DET_PHI-spread",
        "game_id": "2099_01_DET_PHI",
        "season": "2099",
        "week": "1",
        "market": "spread",
        "side": "DET",
        "line_at_pick": line,
        "picked_at": picked_at,
        "units": "1.0",
        "confidence": "2",
        "tags": "",
        "note": "lock",
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


def _grade(root: Path, as_of: str) -> None:
    result = run_cli(
        [
            "grade",
            "--root",
            str(root),
            "--as-of",
            as_of,
            "--schedules",
            str(root / "data" / "schedules.csv"),
        ]
    )
    assert result.returncode == 0, result.stderr + result.stdout


def _graded_by_id(root: Path) -> dict[str, dict[str, str]]:
    with (root / "data" / "graded.csv").open(encoding="utf-8", newline="") as handle:
        return {row["pick_id"]: row for row in csv.DictReader(handle)}
