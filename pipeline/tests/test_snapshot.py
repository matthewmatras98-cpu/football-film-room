"""In-play filter and spread/total consistency, from hand-built v4 JSON."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from football_pipeline.close import Game
from football_pipeline.schema import format_ts
from football_pipeline.snapshot import rows_from_odds
from football_pipeline.teams import name_for

UTC = timezone.utc
KICK = datetime(2099, 9, 13, 17, 0, tzinfo=UTC)


def test_in_play_events_and_markets_are_dropped():
    snapshot_ts = KICK - timedelta(minutes=30)
    kept_update = format_ts(KICK - timedelta(hours=2))
    early = _game("2099_01_GB_CHI")
    later = _game("2099_01_DET_PHI", home="PHI", away="DET", kickoff=KICK + timedelta(hours=3))
    rows = rows_from_odds(
        [
            _event(early, snapshot_ts, "boundary", kept_update, home_point=-8, total=41),
            _event(later, later.kickoff_utc, "ok", format_ts(later.kickoff_utc), home_point=-3, total=47),
        ],
        [early, later],
        snapshot_ts,
        lambda _game: "A",
    )
    assert rows == []

    fresh = rows_from_odds(
        [_event(later, later.kickoff_utc, "fresh", kept_update, home_point=-3.5, total=47.5)],
        [early, later],
        snapshot_ts,
        lambda _game: "B",
    )
    assert {(row.market, row.side, row.point) for row in fresh} == {
        ("spread", "PHI", Decimal("-3.5")),
        ("spread", "DET", Decimal("3.5")),
        ("total", "over", Decimal("47.5")),
        ("total", "under", Decimal("47.5")),
    }
    assert all(row.shot_kind == "B" for row in fresh)


def test_inconsistent_spread_and_total_books_are_dropped():
    game = _game()
    snapshot_ts = KICK - timedelta(minutes=40)
    updated = format_ts(KICK - timedelta(hours=1))
    payload = _event(game, KICK, "evt", updated, home_point=-3, total=47)
    payload["bookmakers"][0]["markets"][0]["outcomes"][1]["point"] = 2.5
    payload["bookmakers"].append(
        {
            "key": "fanduel",
            "title": "FanDuel",
            "last_update": updated,
            "markets": [
                {
                    "key": "spreads",
                    "last_update": updated,
                    "outcomes": [
                        {"name": name_for("CHI"), "price": -110, "point": -3.0},
                        {"name": name_for("GB"), "price": -110, "point": 3.0},
                    ],
                },
                {
                    "key": "totals",
                    "last_update": updated,
                    "outcomes": [
                        {"name": "Over", "price": -105, "point": 47.5},
                        {"name": "Under", "price": -115, "point": 48.0},
                    ],
                },
            ],
        }
    )
    rows = rows_from_odds([payload], [game], snapshot_ts, lambda _game: "fixed")
    # The bad spread drops that book-market only. The other book's bad total
    # does the same. The consistent market on each book stays.
    assert {(row.book, row.market, row.side, row.point) for row in rows} == {
        ("draftkings", "total", "over", Decimal("47")),
        ("draftkings", "total", "under", Decimal("47")),
        ("fanduel", "spread", "CHI", Decimal("-3")),
        ("fanduel", "spread", "GB", Decimal("3")),
    }


def test_last_update_equal_to_kickoff_drops_only_that_market():
    game = _game()
    snapshot_ts = KICK - timedelta(minutes=20)
    before = format_ts(KICK - timedelta(minutes=90))
    payload = _event(game, KICK, "evt", before, home_point=-2.5, total=44)
    payload["bookmakers"][0]["markets"][0]["last_update"] = format_ts(KICK)
    rows = rows_from_odds([payload], [game], snapshot_ts, lambda _game: "A")
    assert {row.market for row in rows} == {"total"}
    assert all(row.point == Decimal("44") for row in rows)


def _game(game_id="2099_01_GB_CHI", home="CHI", away="GB", kickoff=KICK) -> Game:
    return Game(
        game_id=game_id,
        season=2099,
        week=1,
        away_team=away,
        home_team=home,
        kickoff_utc=kickoff,
        away_score=None,
        home_score=None,
        spread_line=Decimal("3"),
        total_line=Decimal("47"),
    )


def _event(game: Game, commence, event_id: str, updated: str, home_point, total: float) -> dict:
    return {
        "id": event_id,
        "sport_key": "americanfootball_nfl",
        "sport_title": "NFL",
        "commence_time": format_ts(commence),
        "home_team": name_for(game.home_team),
        "away_team": name_for(game.away_team),
        "bookmakers": [
            {
                "key": "draftkings",
                "title": "DraftKings",
                "last_update": updated,
                "markets": [
                    {
                        "key": "spreads",
                        "last_update": updated,
                        "outcomes": [
                            {"name": name_for(game.home_team), "price": -110, "point": home_point},
                            {"name": name_for(game.away_team), "price": -110, "point": -home_point},
                        ],
                    },
                    {
                        "key": "totals",
                        "last_update": updated,
                        "outcomes": [
                            {"name": "Over", "price": -105, "point": total},
                            {"name": "Under", "price": -115, "point": total},
                        ],
                    },
                ],
            }
        ],
    }
