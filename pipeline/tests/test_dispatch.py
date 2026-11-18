"""Shot windows, budget tiers, dedupe, and key hygiene."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

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
    minutes of its window, so none of them is a catch-up either.
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
    assert expected_calls == len(windows) * 2 + 3
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


def _run(games, now, remaining, d1=None, transport=None, price=-110, dry_run=False):
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
