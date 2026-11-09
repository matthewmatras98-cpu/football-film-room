"""32-team map and a 1:1 match against the 2026 regular-season schedule."""

from __future__ import annotations

from datetime import timedelta

import pytest

from football_pipeline.nflv import load_games
from football_pipeline.schema import PipelineError, format_ts
from football_pipeline.teams import (
    ABBR_TO_ODDS,
    ODDS_TO_ABBR,
    Event,
    abbr_for,
    match_events,
    name_for,
)
from tests.conftest import FIXTURES


def test_map_covers_the_thirty_two_nflverse_abbreviations():
    assert len(ODDS_TO_ABBR) == 32
    assert len(set(ODDS_TO_ABBR.values())) == 32
    assert set(ABBR_TO_ODDS) == set(ODDS_TO_ABBR.values())
    assert ODDS_TO_ABBR["Los Angeles Rams"] == "LA"
    assert ODDS_TO_ABBR["Los Angeles Chargers"] == "LAC"
    assert ODDS_TO_ABBR["Las Vegas Raiders"] == "LV"
    assert ODDS_TO_ABBR["Washington Commanders"] == "WAS"
    for retired in ("LAR", "OAK", "SD", "WSH", "JAC"):
        assert retired not in ABBR_TO_ODDS
    assert name_for("LA") == "Los Angeles Rams"
    assert abbr_for("Washington Commanders") == "WAS"


def test_unmapped_team_fails_loudly():
    with pytest.raises(PipelineError, match="unmapped team: Oakland Raiders"):
        abbr_for("Oakland Raiders")


def test_every_2026_reg_game_matches_one_event_including_a_flex():
    games, source = load_games(fixture=FIXTURES / "schedules_2026_reg.csv")
    assert source.startswith("fixture:")
    assert len(games) == 272
    for game in games:
        assert game.home_team in ABBR_TO_ODDS
        assert game.away_team in ABBR_TO_ODDS
        assert game.kickoff_utc is not None

    events = [
        Event(
            game.game_id,
            name_for(game.home_team),
            name_for(game.away_team),
            game.kickoff_utc,
        )
        for game in games
    ]
    matched = match_events(events, games)
    assert {event_id: game.game_id for event_id, game in matched.items()} == {
        game.game_id: game.game_id for game in games
    }

    sample = next(game for game in games if game.game_id == "2026_01_ARI_LAC")
    flexed = Event(
        "flex",
        name_for(sample.home_team),
        name_for(sample.away_team),
        sample.kickoff_utc + timedelta(hours=36),
    )
    assert match_events([flexed], games)["flex"].game_id == sample.game_id
    assert format_ts(sample.kickoff_utc) != format_ts(flexed.commence_time)

    too_far = Event(
        "too-far",
        name_for(sample.home_team),
        name_for(sample.away_team),
        sample.kickoff_utc + timedelta(hours=36, seconds=1),
    )
    with pytest.raises(PipelineError, match="matched 0 games"):
        match_events([too_far], games)


def test_two_events_for_one_game_fail():
    games, _source = load_games(fixture=FIXTURES / "schedules_2026_reg.csv")
    game = games[0]
    first = Event("one", name_for(game.home_team), name_for(game.away_team), game.kickoff_utc)
    second = Event("two", name_for(game.home_team), name_for(game.away_team), game.kickoff_utc)
    with pytest.raises(PipelineError, match="matched by events"):
        match_events([first, second], games)


def test_one_event_matching_two_games_fails():
    games, _source = load_games(fixture=FIXTURES / "schedules.csv")
    game = next(item for item in games if item.game_id == "2099_01_DET_PHI")
    twin = game.__class__(
        game_id="2099_01_DET_PHI_B",
        season=game.season,
        week=game.week,
        away_team=game.away_team,
        home_team=game.home_team,
        kickoff_utc=game.kickoff_utc + timedelta(hours=1),
        away_score=None,
        home_score=None,
        spread_line=None,
        total_line=None,
    )
    event = Event("both", name_for(game.home_team), name_for(game.away_team), game.kickoff_utc)
    with pytest.raises(PipelineError, match="matched 2 games"):
        match_events([event], [game, twin])
