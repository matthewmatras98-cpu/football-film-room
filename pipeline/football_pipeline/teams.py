"""Odds API full names and nflverse abbreviations.

Matching is (home, away, kickoff within 36 hours). An unknown name, or an
event that does not match exactly one game, fails the run. LA, LAC, LV, and
WAS are the nflverse abbreviations, not the older LAR / SD / OAK / WSH codes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from football_pipeline.close import Game
from football_pipeline.schema import PipelineError

# Odds API display name -> nflverse team abbreviation.
ODDS_TO_ABBR: dict[str, str] = {
    "Arizona Cardinals": "ARI",
    "Atlanta Falcons": "ATL",
    "Baltimore Ravens": "BAL",
    "Buffalo Bills": "BUF",
    "Carolina Panthers": "CAR",
    "Chicago Bears": "CHI",
    "Cincinnati Bengals": "CIN",
    "Cleveland Browns": "CLE",
    "Dallas Cowboys": "DAL",
    "Denver Broncos": "DEN",
    "Detroit Lions": "DET",
    "Green Bay Packers": "GB",
    "Houston Texans": "HOU",
    "Indianapolis Colts": "IND",
    "Jacksonville Jaguars": "JAX",
    "Kansas City Chiefs": "KC",
    "Las Vegas Raiders": "LV",
    "Los Angeles Chargers": "LAC",
    "Los Angeles Rams": "LA",
    "Miami Dolphins": "MIA",
    "Minnesota Vikings": "MIN",
    "New England Patriots": "NE",
    "New Orleans Saints": "NO",
    "New York Giants": "NYG",
    "New York Jets": "NYJ",
    "Philadelphia Eagles": "PHI",
    "Pittsburgh Steelers": "PIT",
    "San Francisco 49ers": "SF",
    "Seattle Seahawks": "SEA",
    "Tampa Bay Buccaneers": "TB",
    "Tennessee Titans": "TEN",
    "Washington Commanders": "WAS",
}

ABBR_TO_ODDS: dict[str, str] = {abbr: name for name, abbr in ODDS_TO_ABBR.items()}

MATCH_WINDOW = timedelta(hours=36)


@dataclass(frozen=True)
class Event:
    event_id: str
    home_team: str
    away_team: str
    commence_time: datetime


def abbr_for(name: str) -> str:
    try:
        return ODDS_TO_ABBR[name]
    except KeyError:
        raise PipelineError(f"unmapped team: {name}") from None


def name_for(abbr: str) -> str:
    try:
        return ABBR_TO_ODDS[abbr]
    except KeyError:
        raise PipelineError(f"unmapped team: {abbr}") from None


def match_events(events: list[Event], games: list[Game]) -> dict[str, Game]:
    """Map each event to one nflverse game. Zero or many matches fail the run."""
    matched: dict[str, Game] = {}
    used: dict[str, str] = {}
    for event in events:
        home = abbr_for(event.home_team)
        away = abbr_for(event.away_team)
        candidates = [
            game
            for game in games
            if game.home_team == home
            and game.away_team == away
            and game.kickoff_utc is not None
            and abs(event.commence_time - game.kickoff_utc) <= MATCH_WINDOW
        ]
        if len(candidates) != 1:
            raise PipelineError(
                f"event {event.event_id} ({away} at {home}) matched {len(candidates)} games"
            )
        game = candidates[0]
        previous = used.get(game.game_id)
        if previous is not None:
            raise PipelineError(
                f"{game.game_id} matched by events {previous} and {event.event_id}"
            )
        used[game.game_id] = event.event_id
        matched[event.event_id] = game
    return matched
