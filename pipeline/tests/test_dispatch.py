"""Shot windows, budget tiers, dedupe, and key hygiene."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from football_pipeline.close import Game
from football_pipeline.d1 import D1Client
from football_pipeline.dispatch import (
    close_kind,
    due_fixed_slots,
    group_windows,
    is_catchup,
    missed_window_messages,
    plan_shots,
    run_dispatch,
    shot_allowed,
)
from football_pipeline.lines_export import book_lines_from_snapshots, export_lines
from football_pipeline.nflv import load_games
from football_pipeline.odds import OddsClient
from football_pipeline.schema import format_ts
from football_pipeline.teams import name_for
from tests.conftest import FIXTURES
from tests.fakes import SqliteD1

UTC = timezone.utc
SENTINEL = "SENTINEL_KEY_do_not_leak_9f3a"
TUESDAY = datetime(2026, 11, 10, 14, 7, tzinfo=UTC)
DISPATCH_YML = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "odds-dispatch.yml"
WAIT_CAP = timedelta(minutes=45)


def test_window_edge_and_shot_boundaries():
    now = datetime(2026, 11, 15, 17, 0, tzinfo=UTC)
    kickoff = now + timedelta(minutes=45)
    grouped = plan_shots(
        [_game("2026_11_GB_CHI", kickoff), _game("2026_11_DET_PHI", kickoff + timedelta(minutes=25), "PHI", "DET")],
        now,
        set(),
    )
    assert [shot.kind for shot in grouped] == ["A"]
    assert grouped[0].window_kickoff == kickoff

    apart = plan_shots(
        [
            _game("2026_11_GB_CHI", kickoff),
            _game("2026_11_DET_PHI", kickoff + timedelta(minutes=25, seconds=1), "PHI", "DET"),
        ],
        now,
        set(),
    )
    assert len(apart) == 2
    assert {shot.window_kickoff for shot in apart} == {
        kickoff,
        kickoff + timedelta(minutes=25, seconds=1),
    }

    anchor = datetime(2026, 11, 15, 18, 0, tzinfo=UTC)
    assert close_kind(anchor - timedelta(minutes=75), anchor, False) == "A"
    assert close_kind(anchor - timedelta(minutes=75, seconds=1), anchor, False) is None
    assert close_kind(anchor - timedelta(minutes=40), anchor, False) == "B"
    assert close_kind(anchor - timedelta(minutes=40, seconds=1), anchor, False) == "A"
    assert close_kind(anchor - timedelta(minutes=5), anchor, False) == "B"
    assert close_kind(anchor - timedelta(minutes=5), anchor, True) is None
    assert close_kind(anchor - timedelta(minutes=4), anchor, False) == "B"
    assert close_kind(anchor, anchor, False) is None
    assert close_kind(anchor + timedelta(seconds=1), anchor, False) is None

    game = _game("2026_11_GB_CHI", anchor)
    late = anchor - timedelta(minutes=4)
    assert [shot.kind for shot in plan_shots([game], late, set())] == ["B"]
    assert plan_shots([game], late, {f"B:{format_ts(anchor)}"}) == []
    assert plan_shots([game], anchor, set()) == []


def test_fixed_slots_and_dedupe():
    assert [name for name, _slot in due_fixed_slots(TUESDAY)] == ["tue"]
    assert [name for name, _slot in due_fixed_slots(TUESDAY + timedelta(minutes=30))] == ["tue"]
    assert due_fixed_slots(TUESDAY + timedelta(minutes=30, seconds=1)) == []
    assert due_fixed_slots(datetime(2026, 11, 11, 16, 7, tzinfo=UTC))[0][0] == "wed"
    assert due_fixed_slots(datetime(2026, 11, 14, 16, 7, tzinfo=UTC))[0][0] == "sat"

    game = _game("2026_11_GB_CHI", TUESDAY + timedelta(days=3))
    shots = plan_shots([game], TUESDAY, set())
    assert [shot.shot_key for shot in shots] == ["fixed:2026:11:tue"]
    assert plan_shots([game], TUESDAY, {"fixed:2026:11:tue"}) == []
    assert plan_shots([_game("2026_11_GB_CHI", TUESDAY - timedelta(hours=1))], TUESDAY, set()) == []


def test_missed_window_warning():
    kickoff = datetime(2026, 11, 11, 18, 0, tzinfo=UTC)
    game = _game("2026_11_GB_CHI", kickoff)
    assert missed_window_messages([game], [], kickoff) == [f"window {format_ts(kickoff)} got no shot"]
    assert missed_window_messages([game], [], kickoff + timedelta(hours=19)) == []


@pytest.mark.parametrize(
    ("remaining", "fixed", "shot_a", "shot_b"),
    [
        (150, "taken", "taken", "taken"),
        (149, "skipped", "taken", "taken"),
        (50, "skipped", "taken", "taken"),
        (49, "skipped", "skipped", "taken"),
        (20, "skipped", "skipped", "taken"),
        (19, "skipped", "skipped", "skipped"),
    ],
)
def test_budget_tiers(remaining, fixed, shot_a, shot_b, tmp_path, capsys, monkeypatch):
    for value, kind in ((150, "fixed"), (149, "fixed"), (50, "A"), (49, "A"), (20, "B"), (19, "B")):
        assert shot_allowed(value, "B") is (value >= 20)
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    now = TUESDAY
    games = [
        _game("2026_11_GB_CHI", now + timedelta(minutes=20)),
        _game("2026_11_DET_PHI", now + timedelta(minutes=60), "PHI", "DET"),
    ]
    code, transport, d1 = _run(games, now, remaining)
    captured = capsys.readouterr()
    assert code == 0
    assert SENTINEL not in captured.out
    assert SENTINEL not in captured.err
    summary = (tmp_path / "summary.md").read_text(encoding="utf-8") if (tmp_path / "summary.md").exists() else ""
    assert SENTINEL not in summary
    assert transport.calls[0] == "events"
    statuses = {row.kind: row.status for row in d1.fetch_shots()}
    assert statuses == {"fixed": fixed, "A": shot_a, "B": shot_b}
    if "taken" in statuses.values():
        assert transport.calls.count("odds") == 1
    else:
        assert "odds" not in transport.calls
    skipped = [row for row in d1.fetch_shots() if row.status == "skipped"]
    if skipped:
        assert "::notice::" in captured.out
        for row in skipped:
            assert row.shot_key in captured.out
            assert row.shot_key in summary
            assert row.credits_remaining == remaining
    else:
        assert "::notice::" not in captured.out
    for call in d1_calls(d1):
        blob = call["url"] + json.dumps(call["headers"]) + call["body"].decode()
        assert SENTINEL not in blob
        assert "apiKey" not in blob


def test_fixed_shot_dedupes_and_one_odds_call_covers_the_slate(capsys):
    game = _game("2026_11_GB_CHI", TUESDAY + timedelta(days=3))
    code, transport, d1 = _run([game], TUESDAY, 400)
    assert code == 0
    assert transport.calls == ["events", "odds"]
    again, transport_again, _d1 = _run([game], TUESDAY, 400, d1=d1, transport=transport)
    assert again == 0
    assert transport_again.calls == ["events", "odds"]
    assert [row.status for row in d1.fetch_shots()] == ["taken"]
    capsys.readouterr()


def test_taken_shot_hides_the_key_from_lines_and_logs(tmp_path, capsys):
    now = TUESDAY
    games = [_game("2026_11_GB_CHI", now + timedelta(days=2))]
    code, _transport, d1 = _run(games, now, 400, price=98765)
    assert code == 0
    captured = capsys.readouterr()
    assert SENTINEL not in captured.out + captured.err
    assert "x-requests-remaining=400" in captured.out
    assert "x-requests-used=10" in captured.out
    assert "x-requests-last=0" in captured.out
    books = book_lines_from_snapshots(d1.fetch_snapshots(), games)
    export_lines(tmp_path, games, books, now)
    text = (tmp_path / "data" / "lines" / "2026_w11.csv").read_text(encoding="utf-8")
    assert text.splitlines()[0] == "snapshot_ts,game_id,market,line,n_books"
    assert "draftkings" not in text
    assert "98765" not in text
    assert SENTINEL not in text
    assert ",spread,-3.5,1\n" in text
    assert ",total,47.5,1\n" in text
    assert "price" not in text.splitlines()[0]
    stored = " ".join(call["body"].decode() for call in d1_calls(d1))
    assert "draftkings" in stored
    assert "98765" in stored
    assert SENTINEL not in stored


def test_budget_uses_the_last_stored_remaining_when_the_header_is_missing(capsys):
    game = _game("2026_11_GB_CHI", TUESDAY + timedelta(days=2))
    _code, _transport, d1 = _run([game], TUESDAY, 80)
    capsys.readouterr()
    later = datetime(2026, 11, 11, 16, 7, tzinfo=UTC)
    upcoming = _game("2026_11_KC_BUF", later + timedelta(days=2), "BUF", "KC", week=11)
    code, transport, _same = _run(
        [game, upcoming],
        later,
        remaining=None,
        d1=d1,
    )
    captured = capsys.readouterr()
    assert code == 0
    assert transport.calls == ["events"]
    assert "::notice::" in captured.out
    skipped = [row for row in d1.fetch_shots() if row.shot_key.startswith("fixed:2026:11:wed")]
    assert skipped[0].status == "skipped"
    assert skipped[0].credits_remaining == 80


def test_d1_logs_are_counts_only_and_dry_run_is_silent(capsys):
    game = _game("2026_11_GB_CHI", TUESDAY + timedelta(days=2))
    code, _transport, _d1 = _run([game], TUESDAY, 400)
    captured = capsys.readouterr()
    assert code == 0
    assert "d1 snapshots: wrote 4 row(s), total 4" in captured.out
    assert "d1 shots: wrote 1 row(s)" in captured.out
    blob = captured.out + captured.err
    for secret in (SENTINEL, "acct-1", "db-1", "cf-token", "draftkings"):
        assert secret not in blob

    dry, _transport, _d1 = _run([game], TUESDAY, 400, dry_run=True)
    dry_out = capsys.readouterr().out
    assert dry == 0
    assert "dry_run ok" in dry_out
    assert "d1 snapshots:" not in dry_out
    assert "d1 shots:" not in dry_out

    skipped, _transport, _d1 = _run([game], TUESDAY, 19)
    skipped_out = capsys.readouterr().out
    assert skipped == 0
    assert "d1 snapshots:" not in skipped_out
    assert "d1 shots:" not in skipped_out


def test_dry_run_checks_mapping_and_does_not_spend_a_shot(capsys):
    game = _game("2026_11_GB_CHI", TUESDAY + timedelta(hours=2))
    code, transport, d1 = _run([game], TUESDAY, 400, dry_run=True)
    assert code == 0
    assert transport.calls == ["events"]
    assert d1.fetch_shots() == []
    assert "dry_run ok" in capsys.readouterr().out
    created = " ".join(json.loads(call["body"])["sql"] for call in d1_calls(d1))
    assert "CREATE TABLE IF NOT EXISTS snapshots" in created
    assert "CREATE TABLE IF NOT EXISTS shots" in created


def test_catchup_is_a_window_older_than_30_minutes():
    kickoff = datetime(2026, 11, 15, 18, 0, tzinfo=UTC)
    # A starts at K-75, so K-45 is exactly 30 minutes in and still on time.
    assert is_catchup(kickoff - timedelta(minutes=45), kickoff, "A") is False
    assert is_catchup(kickoff - timedelta(minutes=45) + timedelta(seconds=1), kickoff, "A") is True
    assert is_catchup(kickoff - timedelta(minutes=41), kickoff, "A") is True
    assert is_catchup(kickoff - timedelta(minutes=60), kickoff, "A") is False
    # B starts at K-40, so K-10 is the last on-time moment.
    assert is_catchup(kickoff - timedelta(minutes=10), kickoff, "B") is False
    assert is_catchup(kickoff - timedelta(minutes=10) + timedelta(seconds=1), kickoff, "B") is True
    assert is_catchup(kickoff - timedelta(minutes=4), kickoff, "B") is True
    assert is_catchup(kickoff - timedelta(minutes=28), kickoff, "B") is False
    assert is_catchup(kickoff - timedelta(minutes=41), kickoff, "fixed") is False


def test_delayed_a_stays_a_until_b_slot_then_b_only(capsys):
    kickoff = datetime(2026, 11, 15, 18, 0, tzinfo=UTC)
    game = _game("2026_11_GB_CHI", kickoff)
    stamp = format_ts(kickoff)

    code, transport, d1 = _run([game], kickoff - timedelta(minutes=41), 400)
    captured = capsys.readouterr()
    assert code == 0
    assert transport.calls == ["events", "odds"]
    assert [(row.shot_key, row.status) for row in d1.fetch_shots()] == [(f"A:{stamp}", "taken")]
    assert f"::notice::catch-up A:{stamp}" in captured.out

    delayed = kickoff - timedelta(minutes=28)
    code, transport, d1 = _run([game], delayed, 400)
    captured = capsys.readouterr()
    assert code == 0
    assert transport.calls == ["events", "odds"]
    assert [(row.shot_key, row.status) for row in d1.fetch_shots()] == [(f"B:{stamp}", "taken")]
    assert f"A:{stamp}" not in captured.out
    assert "catch-up" not in captured.out
    for later in (
        kickoff - timedelta(minutes=10),
        kickoff - timedelta(minutes=4),
        kickoff + timedelta(minutes=1),
    ):
        code, transport, d1 = _run([game], later, 400, d1=d1, transport=transport)
        assert code == 0
    assert transport.calls.count("odds") == 1
    assert [row.shot_key for row in d1.fetch_shots()] == [f"B:{stamp}"]
    assert f"::warning::window {stamp} got no shot" not in capsys.readouterr().out


def test_b_delayed_past_kickoff_warns_and_does_not_call_odds(capsys):
    kickoff = datetime(2026, 11, 15, 18, 0, tzinfo=UTC)
    code, transport, d1 = _run(
        [_game("2026_11_GB_CHI", kickoff)],
        kickoff + timedelta(minutes=1),
        400,
    )
    captured = capsys.readouterr()
    assert code == 0
    assert transport.calls == []
    assert d1.fetch_shots() == []
    assert f"::warning::window {format_ts(kickoff)} got no shot" in captured.out
    assert "catch-up" not in captured.out


def test_two_ticks_in_one_close_shot_do_not_double(capsys):
    kickoff = datetime(2026, 11, 15, 18, 20, tzinfo=UTC)
    game = _game("2026_11_GB_CHI", kickoff)
    first = kickoff - timedelta(minutes=73)
    second = kickoff - timedelta(minutes=43)
    assert close_kind(first, kickoff, False) == "A"
    assert close_kind(second, kickoff, False) == "A"
    code, transport, d1 = _run([game], first, 400)
    assert code == 0
    assert transport.calls.count("odds") == 1
    code, transport, d1 = _run([game], second, 400, d1=d1, transport=transport)
    captured = capsys.readouterr()
    assert code == 0
    assert transport.calls.count("odds") == 1
    assert [row.shot_key for row in d1.fetch_shots()] == [f"A:{format_ts(kickoff)}"]
    assert "catch-up" not in captured.out
    assert "no shot due" in captured.out


def test_catchup_a_uses_the_same_credit_guard(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    kickoff = datetime(2026, 11, 15, 18, 0, tzinfo=UTC)
    now = kickoff - timedelta(minutes=41)
    game = _game("2026_11_GB_CHI", kickoff)
    stamp = format_ts(kickoff)
    summary = tmp_path / "summary.md"

    code, transport, d1 = _run([game], now, 49)
    captured = capsys.readouterr()
    assert code == 0
    assert transport.calls == ["events"]
    row = d1.fetch_shots()[0]
    assert (row.shot_key, row.status, row.credits_remaining) == (f"A:{stamp}", "skipped", 49)
    assert f"::notice::skipped A:{stamp} x-requests-remaining=49" in captured.out
    assert f"::notice::catch-up A:{stamp}" in captured.out
    assert f"catch-up A:{stamp}" in summary.read_text(encoding="utf-8")
    later = kickoff - timedelta(minutes=40, seconds=1)
    code, transport, d1 = _run([game], later, 49, d1=d1, transport=transport)
    assert code == 0
    assert transport.calls == ["events"]
    assert len(d1.fetch_shots()) == 1

    stopped, stopped_transport, stopped_d1 = _run(
        [game, _game("2026_11_DET_PHI", now + timedelta(minutes=10), "PHI", "DET")],
        now,
        19,
    )
    stopped_out = capsys.readouterr().out
    assert stopped == 0
    assert "odds" not in stopped_transport.calls
    assert {row.kind: row.status for row in stopped_d1.fetch_shots()} == {
        "A": "skipped",
        "B": "skipped",
    }
    assert "::notice::" in stopped_out
    assert f"::notice::catch-up A:{stamp}" in stopped_out

    taken, taken_transport, taken_d1 = _run([game], now, 150)
    taken_out = capsys.readouterr().out
    assert taken == 0
    assert taken_transport.calls == ["events", "odds"]
    assert [(row.shot_key, row.status) for row in taken_d1.fetch_shots()] == [(f"A:{stamp}", "taken")]
    assert f"::notice::catch-up A:{stamp}" in taken_out
    assert "::notice::skipped" not in taken_out


def test_normal_week_replay_uses_no_extra_odds_calls(capsys):
    """On-time :07/:37 ticks over the week 11 fixture: one A, one B, fixed shots.

    Catch-up does not add a call. Each of those shots is inside the first 30
    minutes of its window, so none of them is a catch-up either. The one L
    for the 21:05/21:25 window is inside its first 30 minutes too.
    """
    games, _source = load_games(fixture=FIXTURES / "schedules_2026_reg.csv")
    week = [game for game in games if game.season == 2026 and game.week == 11]
    windows = group_windows(week)
    ticks = _cron_ticks(
        datetime(2026, 11, 17, 0, 7, tzinfo=UTC),
        datetime(2026, 11, 24, 3, 37, tzinfo=UTC),
    )
    expected_calls, expected_keys = _on_time_shots(week, ticks)
    transport = None
    d1 = None
    for tick in ticks:
        code, transport, d1 = _run(week, tick, 5000, d1=d1, transport=transport)
        assert code == 0
    captured = capsys.readouterr()
    taken = [row.shot_key for row in d1.fetch_shots() if row.status == "taken"]
    assert taken == expected_keys
    assert transport.calls.count("odds") == expected_calls
    late_keys = [key for key in expected_keys if key.startswith("L:")]
    assert late_keys == ["L:2026-11-22T21:25:00Z"]
    assert expected_calls == len(windows) * 2 + 3 + len(late_keys)
    assert expected_calls == len(expected_keys)
    stamps = [format_ts(window.kickoff) for window in windows]
    assert [key for key in expected_keys if key.startswith("A:")] == [f"A:{stamp}" for stamp in stamps]
    assert [key for key in expected_keys if key.startswith("B:")] == [f"B:{stamp}" for stamp in stamps]
    assert [key for key in expected_keys if key.startswith("fixed:")] == [
        "fixed:2026:11:tue",
        "fixed:2026:11:wed",
        "fixed:2026:11:sat",
    ]
    assert "catch-up" not in captured.out
    assert "::warning::" not in captured.out
    assert all(row.status == "taken" for row in d1.fetch_shots())


def test_split_window_takes_b_then_one_late_close_on_cron_ticks(capsys):
    """2:05 and 2:25 PM Phoenix, on the real :07/:37 cron.

    B is the tick about 28 minutes before 2:05. L is the :07 tick about 18
    minutes before 2:25. Both are stored once. The window spends three
    /odds calls: A, B, and L.
    """
    phoenix = ZoneInfo("America/Phoenix")
    anchor = datetime(2026, 11, 22, 14, 5, tzinfo=phoenix)
    late = datetime(2026, 11, 22, 14, 25, tzinfo=phoenix)
    assert anchor.astimezone(UTC) == datetime(2026, 11, 22, 21, 5, tzinfo=UTC)
    assert late.astimezone(UTC) == datetime(2026, 11, 22, 21, 25, tzinfo=UTC)
    games = [
        _game("2026_11_NYJ_LAC", anchor, "LAC", "NYJ"),
        _game("2026_11_LV_DEN", late, "DEN", "LV"),
        _game("2026_11_PIT_PHI", late, "PHI", "PIT"),
    ]
    assert len(group_windows(games)) == 1
    ticks = _cron_ticks(
        datetime(2026, 11, 22, 12, 7, tzinfo=UTC),
        datetime(2026, 11, 23, 3, 37, tzinfo=UTC),
    )
    transport = None
    d1 = None
    taken_at: dict[str, datetime] = {}
    for tick in ticks:
        before = {row.shot_key for row in d1.fetch_shots()} if d1 is not None else set()
        code, transport, d1 = _run(games, tick, 5000, d1=d1, transport=transport)
        assert code == 0
        for row in d1.fetch_shots():
            if row.status == "taken" and row.shot_key not in before:
                taken_at[row.shot_key] = tick
    capsys.readouterr()
    anchor_stamp = format_ts(anchor)
    late_stamp = format_ts(late)
    b_tick = datetime(2026, 11, 22, 20, 37, tzinfo=UTC)
    l_tick = datetime(2026, 11, 22, 21, 7, tzinfo=UTC)
    assert taken_at == {
        f"A:{anchor_stamp}": datetime(2026, 11, 22, 20, 7, tzinfo=UTC),
        f"B:{anchor_stamp}": b_tick,
        f"L:{late_stamp}": l_tick,
    }
    assert anchor.astimezone(UTC) - b_tick == timedelta(minutes=28)
    assert late.astimezone(UTC) - l_tick == timedelta(minutes=18)
    assert transport.calls.count("odds") == 3
    assert [row.shot_key for row in d1.fetch_shots()] == list(taken_at)


def test_short_windows_get_no_late_close():
    anchor = datetime(2026, 11, 22, 21, 5, tzinfo=UTC)
    # 20:37 is 28 min before the anchor and 38 min before a +10 kickoff, so
    # an L would be due here if the span rule allowed it.
    now = datetime(2026, 11, 22, 20, 37, tzinfo=UTC)
    single = plan_shots([_game("2026_11_NYJ_LAC", anchor, "LAC", "NYJ")], now, set())
    assert [shot.kind for shot in single] == ["B"]

    same_kick = plan_shots(
        [
            _game("2026_11_NYJ_LAC", anchor, "LAC", "NYJ"),
            _game("2026_11_LV_DEN", anchor, "DEN", "LV"),
        ],
        now,
        set(),
    )
    assert [shot.kind for shot in same_kick] == ["B"]

    ten = anchor + timedelta(minutes=10)
    assert close_kind(now, ten, False) == "B"
    spanned = plan_shots(
        [
            _game("2026_11_NYJ_LAC", anchor, "LAC", "NYJ"),
            _game("2026_11_LV_DEN", ten, "DEN", "LV"),
        ],
        now,
        set(),
    )
    assert [shot.kind for shot in spanned] == ["B"]

    just_over = anchor + timedelta(minutes=10, seconds=1)
    longer = plan_shots(
        [
            _game("2026_11_NYJ_LAC", anchor, "LAC", "NYJ"),
            _game("2026_11_LV_DEN", just_over, "DEN", "LV"),
        ],
        now,
        set(),
    )
    assert [(shot.kind, shot.shot_key) for shot in longer] == [
        ("B", f"B:{format_ts(anchor)}"),
        ("L", f"L:{format_ts(just_over)}"),
    ]

    # Three distinct kickoffs still produce one L, keyed to the latest.
    mid = anchor + timedelta(minutes=12)
    latest = anchor + timedelta(minutes=22)
    late_now = latest - timedelta(minutes=18)
    assert close_kind(late_now, mid, False) == "B"
    triple = plan_shots(
        [
            _game("2026_11_NYJ_LAC", anchor, "LAC", "NYJ"),
            _game("2026_11_LV_DEN", mid, "DEN", "LV"),
            _game("2026_11_PIT_PHI", latest, "PHI", "PIT"),
        ],
        late_now,
        set(),
    )
    late_key = f"L:{format_ts(latest)}"
    assert [shot.shot_key for shot in triple] == [late_key]
    assert plan_shots(
        [
            _game("2026_11_NYJ_LAC", anchor, "LAC", "NYJ"),
            _game("2026_11_LV_DEN", mid, "DEN", "LV"),
            _game("2026_11_PIT_PHI", latest, "PHI", "PIT"),
        ],
        late_now,
        {late_key},
    ) == []


def test_late_close_is_skipped_below_150(tmp_path, capsys, monkeypatch):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    now = datetime(2026, 11, 22, 18, 0, tzinfo=UTC)
    anchor = now + timedelta(minutes=15)
    late = anchor + timedelta(minutes=20)
    other = now + timedelta(minutes=60)
    games = [
        _game("2026_11_NYJ_LAC", anchor, "LAC", "NYJ"),
        _game("2026_11_LV_DEN", late, "DEN", "LV"),
        _game("2026_11_GB_CHI", other),
    ]
    assert {shot.kind for shot in plan_shots(games, now, set())} == {"A", "B", "L"}
    assert shot_allowed(150, "L") is True
    assert shot_allowed(149, "L") is False
    assert shot_allowed(149, "B") is True
    assert shot_allowed(149, "A") is True
    assert shot_allowed(49, "A") is False
    assert shot_allowed(49, "B") is True
    assert shot_allowed(149, "fixed") is False
    late_key = f"L:{format_ts(late)}"

    code, transport, d1 = _run(games, now, 149)
    captured = capsys.readouterr()
    assert code == 0
    assert {row.kind: row.status for row in d1.fetch_shots()} == {
        "A": "taken",
        "B": "taken",
        "L": "skipped",
    }
    skipped = next(row for row in d1.fetch_shots() if row.kind == "L")
    assert skipped.shot_key == late_key
    assert skipped.credits_remaining == 149
    assert f"::notice::skipped {late_key} x-requests-remaining=149" in captured.out
    assert f"skipped {late_key} x-requests-remaining=149" in summary.read_text(encoding="utf-8")
    assert transport.calls.count("odds") == 1

    code, transport, d1 = _run(games, now, 49)
    captured = capsys.readouterr()
    assert code == 0
    assert {row.kind: row.status for row in d1.fetch_shots()} == {
        "A": "skipped",
        "B": "taken",
        "L": "skipped",
    }
    assert f"::notice::skipped {late_key} x-requests-remaining=49" in captured.out
    assert f"::notice::skipped A:{format_ts(other)} x-requests-remaining=49" in captured.out
    text = summary.read_text(encoding="utf-8")
    assert f"skipped {late_key} x-requests-remaining=49" in text
    assert f"skipped A:{format_ts(other)} x-requests-remaining=49" in text
    assert transport.calls.count("odds") == 1
    assert next(row for row in d1.fetch_shots() if row.kind == "B").status == "taken"


def test_delayed_late_close_emits_catchup_and_a_miss_does_not_fail(tmp_path, capsys, monkeypatch):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    anchor = datetime(2026, 11, 22, 21, 5, tzinfo=UTC)
    late = datetime(2026, 11, 22, 21, 25, tzinfo=UTC)
    games = [
        _game("2026_11_NYJ_LAC", anchor, "LAC", "NYJ"),
        _game("2026_11_LV_DEN", late, "DEN", "LV"),
    ]
    stamp = format_ts(late)
    assert is_catchup(late - timedelta(minutes=18), late, "L") is False
    assert is_catchup(late - timedelta(minutes=10), late, "L") is False
    assert is_catchup(late - timedelta(minutes=10) + timedelta(seconds=1), late, "L") is True
    delayed = late - timedelta(minutes=4)
    assert is_catchup(delayed, late, "L") is True

    b_tick = anchor - timedelta(minutes=28)
    code, transport, d1 = _run(games, b_tick, 400)
    assert code == 0
    code, transport, d1 = _run(games, late + timedelta(minutes=1), 400, d1=d1, transport=transport)
    missed_out = capsys.readouterr().out
    assert code == 0
    assert transport.calls.count("odds") == 1
    assert [row.kind for row in d1.fetch_shots()] == ["B"]
    assert "::warning::" not in missed_out
    assert "::error::" not in missed_out
    assert missed_window_messages(games, d1.fetch_shots(), late + timedelta(minutes=1)) == []

    code, transport, d1 = _run(games, b_tick, 400)
    assert code == 0
    code, transport, d1 = _run(games, delayed, 400, d1=d1, transport=transport)
    caught = capsys.readouterr().out
    assert code == 0
    assert f"::notice::catch-up L:{stamp}" in caught
    assert f"catch-up L:{stamp}" in summary.read_text(encoding="utf-8")
    assert "::warning::" not in caught
    assert [(row.shot_key, row.status) for row in d1.fetch_shots()] == [
        (f"B:{format_ts(anchor)}", "taken"),
        (f"L:{stamp}", "taken"),
    ]
    assert transport.calls.count("odds") == 2
    still_open = delayed + timedelta(minutes=2)
    assert timedelta(0) < late - still_open <= timedelta(minutes=5)
    code, transport, d1 = _run(games, still_open, 400, d1=d1, transport=transport)
    assert code == 0
    assert transport.calls.count("odds") == 2
    assert capsys.readouterr().out.count(f"catch-up L:{stamp}") == 0


def _cron_ticks(start: datetime, end: datetime) -> list[datetime]:
    ticks: list[datetime] = []
    cursor = start
    while cursor <= end:
        if cursor.minute in (7, 37) and (cursor.hour <= 3 or cursor.hour >= 12):
            ticks.append(cursor)
        cursor += timedelta(minutes=30)
    return ticks


def _on_time_shots(games, ticks: list[datetime]) -> tuple[int, list[str]]:
    """Shots today's planner takes when every cron tick lands on time."""
    seen: set[str] = set()
    calls = 0
    keys: list[str] = []
    for tick in ticks:
        due = [shot.shot_key for shot in plan_shots(games, tick, seen)]
        if not due:
            continue
        calls += 1
        seen.update(due)
        keys.extend(due)
    return calls, keys


def test_missed_window_is_a_warning_and_exits_zero(capsys):
    kickoff = datetime(2026, 11, 11, 18, 0, tzinfo=UTC)
    code, transport, _d1 = _run([_game("2026_11_GB_CHI", kickoff)], kickoff, 400)
    captured = capsys.readouterr()
    assert code == 0
    assert transport.calls == []
    assert f"::warning::window {format_ts(kickoff)} got no shot" in captured.out


def _run(
    games,
    now,
    remaining,
    d1=None,
    transport=None,
    price=-110,
    dry_run=False,
    wait_max=None,
    clock=None,
    sleep=None,
):
    if d1 is None:
        fake = SqliteD1()
        d1 = D1Client("acct-1", "db-1", "cf-token", transport=fake)
    if transport is None:
        transport = _Odds(games, remaining, price)
    code = run_dispatch(
        now=now,
        games=games,
        odds=OddsClient(SENTINEL, transport=transport),
        d1=d1,
        dry_run=dry_run,
        wait_max=wait_max,
        clock=clock,
        sleep=sleep,
    )
    return code, transport, d1


def d1_calls(d1: D1Client):
    return d1._transport.calls


class _Odds:
    def __init__(self, games, remaining, price):
        self.games = games
        self.remaining = remaining
        self.price = price
        self.calls: list[str] = []

    def __call__(self, endpoint, path, params):
        self.calls.append(endpoint)
        assert params["apiKey"] == SENTINEL
        if endpoint == "events":
            body = [_event(game) for game in self.games]
            last = "0"
            remaining = "" if self.remaining is None else str(self.remaining)
        else:
            body = [_event(game, priced=True, price=self.price) for game in self.games]
            last = "2"
            remaining = str(self.remaining - 2)
        headers = {
            "x-requests-remaining": remaining,
            "x-requests-used": "10",
            "x-requests-last": last,
        }
        return 200, headers, json.dumps(body).encode()


def _event(game: Game, priced: bool = False, price: int = -110) -> dict:
    updated = format_ts(game.kickoff_utc - timedelta(hours=3))
    payload = {
        "id": f"evt-{game.game_id}",
        "sport_key": "americanfootball_nfl",
        "sport_title": "NFL",
        "commence_time": format_ts(game.kickoff_utc),
        "home_team": name_for(game.home_team),
        "away_team": name_for(game.away_team),
    }
    if not priced:
        return payload
    payload["bookmakers"] = [
        {
            "key": "draftkings",
            "title": "DraftKings",
            "last_update": updated,
            "markets": [
                {
                    "key": "spreads",
                    "last_update": updated,
                    "outcomes": [
                        {"name": name_for(game.home_team), "price": price, "point": -3.5},
                        {"name": name_for(game.away_team), "price": price, "point": 3.5},
                    ],
                },
                {
                    "key": "totals",
                    "last_update": updated,
                    "outcomes": [
                        {"name": "Over", "price": price, "point": 47.5},
                        {"name": "Under", "price": price, "point": 47.5},
                    ],
                },
            ],
        }
    ]
    return payload


def _game(game_id, kickoff, home="CHI", away="GB", week=11) -> Game:
    return Game(
        game_id=game_id,
        season=2026,
        week=week,
        away_team=away,
        home_team=home,
        kickoff_utc=kickoff,
        away_score=None,
        home_score=None,
        spread_line=Decimal("3"),
        total_line=Decimal("47"),
    )


class _Clock:
    """Injectable clock. sleep moves the clock and never blocks."""

    def __init__(self, start: datetime):
        self.now = start
        self.slept = 0.0
        self.naps: list[float] = []

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.naps.append(seconds)
        self.slept += seconds
        self.now += timedelta(seconds=seconds)


@pytest.mark.parametrize(
    ("before", "taken", "sleep_min", "catchup"),
    [
        (90, (("A", 60),), 30, False),
        (50, (("A", 50), ("B", 15)), 35, False),
        (20, (("B", 15),), 5, False),
        (6, (("B", 6),), 0, True),
        (2, (("B", 2),), 0, True),
        (-5, (), 0, False),
    ],
)
def test_single_window_waits_for_the_aim_or_takes_the_open_shot(before, taken, sleep_min, catchup, capsys):
    """K-90 sleeps to A at K-60. A start already past the aim takes that shot now.

    K-20 is inside B and the aim K-15 is still ahead, so the cap reaches it.
    K-6 and K-2 are past the aim (catch-up). After kickoff there is nothing to take.
    """
    kickoff = datetime(2026, 11, 15, 18, 0, tzinfo=UTC)
    start = kickoff - timedelta(minutes=before)
    clock = _Clock(start)
    code, transport, d1 = _run(
        [_game("2026_11_GB_CHI", kickoff)],
        start,
        400,
        wait_max=WAIT_CAP,
        clock=clock,
        sleep=clock.sleep,
    )
    captured = capsys.readouterr()
    assert code == 0
    rows = [(row.kind, row.snapshot_ts, row.status) for row in d1.fetch_shots()]
    assert rows == [
        (kind, format_ts(kickoff - timedelta(minutes=mins)), "taken") for kind, mins in taken
    ]
    assert transport.calls.count("odds") == len(taken)
    assert captured.out.count("x-requests-last=2") == len(taken)
    assert clock.slept == sleep_min * 60
    if not taken:
        assert clock.naps == []
        assert transport.calls == []
        assert f"::warning::window {format_ts(kickoff)} got no shot" in captured.out
        assert "catch-up" not in captured.out
    elif catchup:
        assert "::notice::catch-up B:" in captured.out
    else:
        assert "catch-up" not in captured.out


def test_sleeper_and_queued_run_take_one_a_and_one_b(capsys):
    """The sleeper holds the lock. The queued run starts when it finishes."""
    kickoff = datetime(2026, 11, 15, 18, 0, tzinfo=UTC)
    game = _game("2026_11_GB_CHI", kickoff)
    stamp = format_ts(kickoff)
    clock = _Clock(kickoff - timedelta(minutes=90))
    code, transport, d1 = _run([game], clock.now, 400, wait_max=WAIT_CAP, clock=clock, sleep=clock.sleep)
    assert code == 0
    assert clock.slept == 30 * 60
    assert [(row.shot_key, row.snapshot_ts) for row in d1.fetch_shots()] == [
        (f"A:{stamp}", format_ts(kickoff - timedelta(minutes=60)))
    ]
    assert transport.calls.count("odds") == 1

    follow = _Clock(clock.now)
    code, transport, d1 = _run(
        [game],
        follow.now,
        400,
        d1=d1,
        transport=transport,
        wait_max=WAIT_CAP,
        clock=follow,
        sleep=follow.sleep,
    )
    assert code == 0
    assert follow.slept == 45 * 60
    assert [(row.shot_key, row.snapshot_ts, row.status) for row in d1.fetch_shots()] == [
        (f"A:{stamp}", format_ts(kickoff - timedelta(minutes=60)), "taken"),
        (f"B:{stamp}", format_ts(kickoff - timedelta(minutes=15)), "taken"),
    ]
    assert transport.calls.count("odds") == 2

    queued = _Clock(follow.now)
    code, transport, d1 = _run(
        [game],
        queued.now,
        400,
        d1=d1,
        transport=transport,
        wait_max=WAIT_CAP,
        clock=queued,
        sleep=queued.sleep,
    )
    captured = capsys.readouterr()
    assert code == 0
    assert queued.naps == []
    assert transport.calls.count("odds") == 2
    assert [row.shot_key for row in d1.fetch_shots()] == [f"A:{stamp}", f"B:{stamp}"]
    assert "catch-up" not in captured.out
    assert "::warning::" not in captured.out


def test_nothing_inside_the_wait_cap_sleeps_zero_and_calls_no_odds(capsys):
    kickoff = datetime(2026, 11, 15, 18, 0, tzinfo=UTC)
    aim = kickoff - timedelta(minutes=60)
    on_cap = _Clock(aim - WAIT_CAP)
    code, transport, d1 = _run(
        [_game("2026_11_GB_CHI", kickoff)],
        on_cap.now,
        400,
        wait_max=WAIT_CAP,
        clock=on_cap,
        sleep=on_cap.sleep,
    )
    assert code == 0
    assert on_cap.slept == WAIT_CAP.total_seconds()
    assert [row.snapshot_ts for row in d1.fetch_shots()] == [format_ts(aim)]
    assert transport.calls.count("odds") == 1
    capsys.readouterr()

    outside = _Clock(aim - WAIT_CAP - timedelta(minutes=1))
    code, transport, d1 = _run(
        [_game("2026_11_GB_CHI", kickoff)],
        outside.now,
        400,
        wait_max=WAIT_CAP,
        clock=outside,
        sleep=outside.sleep,
    )
    captured = capsys.readouterr()
    assert code == 0
    assert outside.naps == []
    assert transport.calls == []
    assert d1.fetch_shots() == []
    assert "no shot due" in captured.out
    assert "x-requests-last=2" not in captured.out


def test_short_cap_inside_the_window_takes_the_shot_now(capsys):
    kickoff = datetime(2026, 11, 15, 18, 0, tzinfo=UTC)
    start = kickoff - timedelta(minutes=20)
    clock = _Clock(start)
    code, transport, d1 = _run(
        [_game("2026_11_GB_CHI", kickoff)],
        start,
        400,
        wait_max=timedelta(minutes=3),
        clock=clock,
        sleep=clock.sleep,
    )
    assert code == 0
    assert clock.naps == []
    assert [(row.kind, row.snapshot_ts) for row in d1.fetch_shots()] == [
        ("B", format_ts(start))
    ]
    assert transport.calls.count("odds") == 1
    assert "catch-up" not in capsys.readouterr().out


def test_wait_keeps_the_credit_gate_and_the_catchup_notice(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    kickoff = datetime(2026, 11, 15, 18, 0, tzinfo=UTC)
    stamp = format_ts(kickoff)
    start = kickoff - timedelta(minutes=90)
    clock = _Clock(start)
    code, transport, d1 = _run(
        [_game("2026_11_GB_CHI", kickoff)],
        start,
        49,
        wait_max=WAIT_CAP,
        clock=clock,
        sleep=clock.sleep,
    )
    captured = capsys.readouterr()
    assert code == 0
    assert clock.slept == 30 * 60
    assert transport.calls == ["events"]
    row = d1.fetch_shots()[0]
    assert (row.shot_key, row.status, row.credits_remaining) == (f"A:{stamp}", "skipped", 49)
    assert f"::notice::skipped A:{stamp} x-requests-remaining=49" in captured.out
    assert "catch-up" not in captured.out
    assert "x-requests-last=2" not in captured.out

    late = kickoff - timedelta(minutes=6)
    late_clock = _Clock(late)
    code, transport, _d1 = _run(
        [_game("2026_11_GB_CHI", kickoff)],
        late,
        19,
        wait_max=WAIT_CAP,
        clock=late_clock,
        sleep=late_clock.sleep,
    )
    captured = capsys.readouterr()
    assert code == 0
    assert late_clock.naps == []
    assert transport.calls == ["events"]
    skipped = _d1.fetch_shots()[0]
    assert (skipped.shot_key, skipped.status) == (f"B:{stamp}", "skipped")
    assert f"::notice::catch-up B:{stamp}" in captured.out
    assert f"catch-up B:{stamp}" in (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert "x-requests-last=2" not in captured.out


def test_dry_run_does_not_wait(capsys):
    kickoff = datetime(2026, 11, 15, 18, 0, tzinfo=UTC)
    clock = _Clock(kickoff - timedelta(minutes=90))
    code, transport, d1 = _run(
        [_game("2026_11_GB_CHI", kickoff)],
        clock.now,
        400,
        dry_run=True,
        wait_max=WAIT_CAP,
        clock=clock,
        sleep=clock.sleep,
    )
    assert code == 0
    assert clock.naps == []
    assert transport.calls == ["events"]
    assert d1.fetch_shots() == []
    assert "dry_run ok" in capsys.readouterr().out


def test_grouped_window_waits_for_a_then_b_then_l(capsys):
    anchor = datetime(2026, 11, 22, 21, 5, tzinfo=UTC)
    late = anchor + timedelta(minutes=20)
    games = [
        _game("2026_11_NYJ_LAC", anchor, "LAC", "NYJ"),
        _game("2026_11_LV_DEN", late, "DEN", "LV"),
    ]
    anchor_stamp = format_ts(anchor)
    late_stamp = format_ts(late)
    clock = _Clock(anchor - timedelta(minutes=90))
    code, transport, d1 = _run(games, clock.now, 400, wait_max=WAIT_CAP, clock=clock, sleep=clock.sleep)
    assert code == 0
    assert [(row.shot_key, row.snapshot_ts) for row in d1.fetch_shots()] == [
        (f"A:{anchor_stamp}", format_ts(anchor - timedelta(minutes=60)))
    ]

    follow = _Clock(clock.now)
    code, transport, d1 = _run(
        games,
        follow.now,
        400,
        d1=d1,
        transport=transport,
        wait_max=WAIT_CAP,
        clock=follow,
        sleep=follow.sleep,
    )
    assert code == 0
    assert [(row.shot_key, row.snapshot_ts) for row in d1.fetch_shots()] == [
        (f"A:{anchor_stamp}", format_ts(anchor - timedelta(minutes=60))),
        (f"B:{anchor_stamp}", format_ts(anchor - timedelta(minutes=15))),
    ]

    later = _Clock(follow.now)
    code, transport, d1 = _run(
        games,
        later.now,
        400,
        d1=d1,
        transport=transport,
        wait_max=WAIT_CAP,
        clock=later,
        sleep=later.sleep,
    )
    captured = capsys.readouterr()
    assert code == 0
    assert [(row.shot_key, row.snapshot_ts, row.status) for row in d1.fetch_shots()] == [
        (f"A:{anchor_stamp}", format_ts(anchor - timedelta(minutes=60)), "taken"),
        (f"B:{anchor_stamp}", format_ts(anchor - timedelta(minutes=15)), "taken"),
        (f"L:{late_stamp}", format_ts(late - timedelta(minutes=15)), "taken"),
    ]
    assert transport.calls.count("odds") == 3
    assert captured.out.count("x-requests-last=2") == 3
    assert "catch-up" not in captured.out

    done = _Clock(later.now)
    code, transport, d1 = _run(
        games,
        done.now,
        400,
        d1=d1,
        transport=transport,
        wait_max=WAIT_CAP,
        clock=done,
        sleep=done.sleep,
    )
    assert code == 0
    assert done.naps == []
    assert transport.calls.count("odds") == 3
    assert len(d1.fetch_shots()) == 3


def test_fixed_slot_stays_on_its_clock_when_a_close_aim_is_later(capsys):
    kickoff = datetime(2026, 11, 10, 15, 30, tzinfo=UTC)
    start = TUESDAY - timedelta(minutes=17)
    clock = _Clock(start)
    code, transport, d1 = _run(
        [_game("2026_11_GB_CHI", kickoff)],
        start,
        400,
        wait_max=WAIT_CAP,
        clock=clock,
        sleep=clock.sleep,
    )
    capsys.readouterr()
    assert code == 0
    assert [(row.shot_key, row.snapshot_ts) for row in d1.fetch_shots()] == [
        ("fixed:2026:11:tue", format_ts(TUESDAY)),
        (f"A:{format_ts(kickoff)}", format_ts(kickoff - timedelta(minutes=60))),
    ]
    assert transport.calls.count("odds") == 2
    assert clock.slept == 40 * 60


def test_week_with_wait_takes_each_on_time_shot_once(capsys):
    games, _source = load_games(fixture=FIXTURES / "schedules_2026_reg.csv")
    week = [game for game in games if game.season == 2026 and game.week == 11]
    ticks = _cron_ticks(
        datetime(2026, 11, 17, 0, 7, tzinfo=UTC),
        datetime(2026, 11, 24, 3, 37, tzinfo=UTC),
    )
    _expected_calls, expected_keys = _on_time_shots(week, ticks)
    fires = _fires_from_workflow(
        DISPATCH_YML.read_text(encoding="utf-8"),
        datetime(2026, 11, 17, 0, 0, tzinfo=UTC),
        datetime(2026, 11, 24, 3, 37, tzinfo=UTC),
    )
    transport = _Odds(week, 5000, -110)
    d1 = None
    busy: datetime | None = None
    for scheduled in fires:
        start = scheduled if busy is None else max(scheduled, busy)
        clock = _Clock(start)
        code, transport, d1 = _run(
            week,
            start,
            5000,
            d1=d1,
            transport=transport,
            wait_max=WAIT_CAP,
            clock=clock,
            sleep=clock.sleep,
        )
        assert code == 0
        busy = clock.now
    captured = capsys.readouterr()
    taken = [row.shot_key for row in d1.fetch_shots() if row.status == "taken"]
    assert sorted(taken) == sorted(expected_keys)
    assert len(taken) == len(expected_keys)
    assert transport.calls.count("odds") == len(expected_keys)
    assert captured.out.count("x-requests-last=2") == len(expected_keys)
    assert all(row.status == "taken" for row in d1.fetch_shots())
    assert "catch-up" not in captured.out
    assert "::warning::" not in captured.out


def test_odds_dispatch_workflow_waits_and_keeps_the_crons():
    text = DISPATCH_YML.read_text(encoding="utf-8")
    old = [
        'cron: "7,37 12-23 * * *"',
        'cron: "7,37 0-3 * * *"',
    ]
    added = [
        'cron: "3,13,23,33,43,53 23 * * 4"',
        'cron: "3,13,23,33,43,53 0-1 * * 5"',
        'cron: "3,13,23,33,43,53 12-23 * * 0"',
        'cron: "3,13,23,33,43,53 0-1 * * 1"',
        'cron: "3,13,23,33,43,53 23 * * 1"',
        'cron: "3,13,23,33,43,53 0-1 * * 2"',
        'cron: "3,13,23,33,43,53 18-23 * * 6"',
        'cron: "3,13,23,33,43,53 0-1 * * 0"',
        'cron: "3,13,23,33,43,53 23 * * 3"',
        'cron: "3,13,23,33,43,53 0-1 * * 4"',
        'cron: "3,13,23,33,43,53 16-21 * * 4"',
        'cron: "3,13,23,33,43,53 18-20 * * 5"',
        'cron: "3,13,23,33,43,53 16-17,21,23 25 12 *"',
        'cron: "3,13,23,33,43,53 0-1 26 12 *"',
    ]
    for line in old + added:
        assert line in text
    assert "timeout-minutes: 60" in text
    assert "group: odds-dispatch" in text
    assert "cancel-in-progress: false" in text
    assert "Phoenix does not observe DST" in text
    assert "--wait-max-minutes 45" in text
    assert "dispatch --dry-run" in text
    for line in text.splitlines():
        if "--dry-run" in line:
            assert "--wait-max-minutes" not in line
    assert text.count("--wait-max-minutes 45") == 1
    assert "${{ secrets.ODDS_API_KEY }}" in text
    assert "echo" not in text


def _fires_from_workflow(text: str, start: datetime, end: datetime) -> list[datetime]:
    crons = re.findall(r'cron: "([^"]+)"', text)
    fires: list[datetime] = []
    cursor = start.replace(second=0, microsecond=0)
    while cursor <= end:
        if any(_cron_matches(cron, cursor) for cron in crons):
            fires.append(cursor)
        cursor += timedelta(minutes=1)
    return fires


def _cron_matches(cron: str, moment: datetime) -> bool:
    minute, hour, day, month, dow = cron.split()
    # GitHub's day-of-week is 0 on Sunday. Python's weekday is 0 on Monday.
    cron_dow = (moment.weekday() + 1) % 7
    return (
        _cron_field(minute, moment.minute)
        and _cron_field(hour, moment.hour)
        and _cron_field(day, moment.day)
        and _cron_field(month, moment.month)
        and _cron_field(dow, cron_dow)
    )


def _cron_field(field: str, value: int) -> bool:
    if field == "*":
        return True
    for part in field.split(","):
        if "-" in part:
            lo, hi = (int(piece) for piece in part.split("-"))
            if lo <= value <= hi:
                return True
        elif int(part) == value:
            return True
    return False


def test_christmas_2026_close_aims_have_dense_ticks():
    """Week 16 Christmas kicks: 18:00 and 21:30 UTC Dec 25, 01:15 UTC Dec 26.

    Every A (K-60) and B (K-15) aim needs at least four 10-minute ticks in
    the 45-minute wait before it, so dropped or late cron ticks still land.
    """
    text = DISPATCH_YML.read_text(encoding="utf-8")
    dense = [cron for cron in re.findall(r'cron: "([^"]+)"', text) if cron.startswith("3,13,")]
    kickoffs = [
        datetime(2026, 12, 25, 18, 0, tzinfo=UTC),
        datetime(2026, 12, 25, 21, 30, tzinfo=UTC),
        datetime(2026, 12, 26, 1, 15, tzinfo=UTC),
    ]
    for kickoff in kickoffs:
        for before in (60, 15):
            aim = kickoff - timedelta(minutes=before)
            ticks = [
                aim - timedelta(minutes=offset)
                for offset in range(46)
                if any(_cron_matches(cron, aim - timedelta(minutes=offset)) for cron in dense)
            ]
            assert len(ticks) >= 4, (kickoff, before, ticks)
