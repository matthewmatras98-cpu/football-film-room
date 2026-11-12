"""Write one schema-valid card per game in each open week.

Open weeks follow lines_export.select_export_weeks, minus the latest fully
final week. generated_at is --as-of. The same inputs rewrite the same bytes.
Cards for other weeks are left in place.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from football_pipeline.close import Game
from football_pipeline.lines_export import select_export_weeks
from football_pipeline.nflv import kickoff_utc_from_parts
from football_pipeline.schema import PipelineError, format_ts, parse_ts
from football_pipeline.venues import format_coord, lookup_venue
from football_pipeline.weather import (
    NWSError,
    forecast_period,
    precip_percent,
    temperature_f,
    warn,
    wind_mph,
)

EN_DASH = "\u2013"
PBP_SOURCE = "nflverse_pbp"
NGS_SOURCE = "ngs_via_nflverse"
NWS_SOURCE = "nws"
SOURCE_ORDER = (
    "nflverse_pbp",
    "nflverse_schedules",
    "nflverse_injuries",
    "ngs_via_nflverse",
    "odds_api",
    "nws",
)
SPREAD_MARKETS = frozenset({"spread", "spreads"})
TOTAL_MARKETS = frozenset({"total", "totals"})
BOOK_OR_DOLLAR = re.compile(
    r"\$|draftkings|fanduel|betmgm|betrivers|bovada|betonline|lowvig|mybookie|betus",
    re.IGNORECASE,
)
PROMPT_BAN = re.compile(
    r"\b(?:lock|locks|locked|guaranteed|wager|wagers|wagering|parlay|parlays|"
    r"sportsbook|sportsbooks|bet|cover|units|odds|pick)\b|ftn",
    re.IGNORECASE,
)
RATE_KEYS = {
    ("epa", "off_sr_season"),
    ("epa", "def_sr_season"),
    ("pass_rate_pace", "neutral_ed_pass_rate"),
    ("explosives", "explosive_rate_for"),
    ("explosives", "explosive_rate_allowed"),
    ("sacks", "sack_rate_for"),
    ("sacks", "sack_rate_allowed"),
    ("red_zone_downs", "rz_td_rate_for"),
    ("red_zone_downs", "rz_td_rate_allowed"),
    ("red_zone_downs", "third_down_rate_for"),
    ("red_zone_downs", "third_down_rate_allowed"),
    ("red_zone_downs", "fourth_down_rate_for"),
    ("red_zone_downs", "fourth_down_rate_allowed"),
}


@dataclass(frozen=True)
class ScheduleRow:
    game: Game
    stadium_id: str | None
    stadium: str | None
    surface: str | None
    home_rest: int | None
    away_rest: int | None
    game_type: str | None


@dataclass(frozen=True)
class Play:
    game_id: str
    season: int
    week: int
    season_type: str | None
    play_id: int
    qtr: int | None
    half_seconds_remaining: float | None
    down: int | None
    wp: float | None
    posteam: str | None
    defteam: str | None
    pass_play: int
    rush: int
    epa: float | None
    success: float | None
    yards_gained: float | None
    sack: int
    qb_dropback: int
    passer_player_id: str | None
    passer_player_name: str | None
    cpoe: float | None
    touchdown: int
    yardline_100: float | None
    fixed_drive: int | None
    third_down_converted: int
    third_down_failed: int
    fourth_down_converted: int
    fourth_down_failed: int


@dataclass(frozen=True)
class InjuryRow:
    season: int
    week: int
    season_type: str | None
    team: str
    player: str
    position: str
    status: str
    detail: str | None


@dataclass(frozen=True)
class NgsRow:
    season: int
    week: int
    season_type: str | None
    team: str
    player_id: str | None
    display_name: str | None
    short_name: str | None
    avg_time_to_throw: float | None
    attempts: int | None


def write_cards(
    root: Path,
    database: Path,
    as_of: datetime,
    *,
    nws_fixtures: Path | None = None,
    transport=None,
) -> list[Path]:
    if as_of.tzinfo is None:
        raise PipelineError("cards --as-of must include a timezone")
    as_of = as_of.astimezone(timezone.utc).replace(microsecond=0)
    if not database.is_file():
        raise PipelineError(f"duckdb not found: {database}")
    connection = duckdb.connect(str(database), read_only=True)
    try:
        schedules = _schedules(connection)
        plays = _plays(connection)
        injuries = _injuries(connection)
        ngs = _ngs(connection)
        lines = _lines(connection)
    finally:
        connection.close()

    games = [row.game for row in schedules]
    by_id = {row.game.game_id: row for row in schedules}
    open_weeks = select_open_weeks(games, as_of)
    targets = [row for row in schedules if (row.game.season, row.game.week) in open_weeks]
    targets.sort(key=lambda row: row.game.game_id)

    logged: set[tuple[int, int]] = set()
    for row in targets:
        key = (row.game.season, row.game.week)
        if key in logged:
            continue
        logged.add(key)
        count = _injury_count(injuries, row.game.season, row.game.week)
        print(f"injuries {row.game.season} wk{row.game.week:02d}: {count} row(s)")

    out_dir = root / "src" / "content" / "games"
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    generated_at = format_ts(as_of)
    for row in targets:
        through = completed_through(games, row.game.season)
        card = build_card(
            row,
            games=games,
            plays=plays,
            injuries=injuries,
            ngs=ngs,
            lines=lines,
            through=through,
            generated_at=generated_at,
            nws_fixtures=nws_fixtures,
            transport=transport,
        )
        _assert_publishable(card)
        path = out_dir / f"{row.game.game_id}.json"
        path.write_bytes(dump_card(card))
        written.append(path)
    print(f"cards wrote {len(written)} file(s)")
    return written


def select_open_weeks(games: list[Game], as_of: datetime) -> set[tuple[int, int]]:
    """Export weeks, without the latest fully final week."""
    chosen = set(select_export_weeks(games, as_of))
    by_week: dict[tuple[int, int], list[Game]] = {}
    for game in games:
        by_week.setdefault((game.season, game.week), []).append(game)
    completed = [
        key
        for key, group in by_week.items()
        if group and all(game.has_final_score for game in group)
    ]
    if completed:
        chosen.discard(max(completed))
    return chosen


def completed_through(games: list[Game], season: int) -> int:
    """Highest week in this season whose games all have a final score."""
    by_week: dict[int, list[Game]] = {}
    for game in games:
        if game.season != season:
            continue
        by_week.setdefault(game.week, []).append(game)
    final = [
        week
        for week, group in by_week.items()
        if group and all(game.has_final_score for game in group)
    ]
    if not final:
        return 0
    return max(final)


def build_card(
    row: ScheduleRow,
    *,
    games: list[Game],
    plays: list[Play],
    injuries: list[InjuryRow],
    ngs: list[NgsRow],
    lines: list[dict],
    through: int,
    generated_at: str,
    nws_fixtures: Path | None,
    transport,
) -> dict:
    game = row.game
    if game.kickoff_utc is None:
        raise PipelineError(f"{game.game_id} has no kickoff")
    venue = lookup_venue(row.stadium_id, row.stadium)
    roof = venue.roof_class if venue is not None else "unknown"
    history_rows = [item for item in lines if item["game_id"] == game.game_id]
    line_open, line_latest, line_history = line_fields(history_rows)
    season_ids = _team_game_ids(games, game.season, through)
    sections_epa = {
        "home": _epa_side(plays, game.home_team, game.season, through, season_ids),
        "away": _epa_side(plays, game.away_team, game.season, through, season_ids),
    }
    sections_pass = {
        "home": _pass_side(plays, game.home_team, game.season, through, season_ids),
        "away": _pass_side(plays, game.away_team, game.season, through, season_ids),
    }
    sections_explosive = {
        "home": _explosive_side(plays, game.home_team, game.season, through, season_ids),
        "away": _explosive_side(plays, game.away_team, game.season, through, season_ids),
    }
    sections_qb = {
        "home": _qb_side(plays, ngs, game.home_team, game.season, through, season_ids),
        "away": _qb_side(plays, ngs, game.away_team, game.season, through, season_ids),
    }
    sections_sacks = {
        "home": _sack_side(plays, game.home_team, game.season, through, season_ids),
        "away": _sack_side(plays, game.away_team, game.season, through, season_ids),
    }
    sections_downs = {
        "home": _down_side(plays, game.home_team, game.season, through, season_ids),
        "away": _down_side(plays, game.away_team, game.season, through, season_ids),
    }
    status, home_injuries, away_injuries = _injury_sides(
        injuries, game.season, game.week, game.home_team, game.away_team
    )
    weather, _nws_used = _weather_block(
        roof,
        None if venue is None else venue.country,
        None if venue is None else venue.lat,
        None if venue is None else venue.lon,
        game.kickoff_utc,
        nws_fixtures,
        transport,
    )
    sections = {
        "epa": sections_epa,
        "pass_rate_pace": sections_pass,
        "explosives": sections_explosive,
        "qb": sections_qb,
        "sacks": sections_sacks,
        "red_zone_downs": sections_downs,
        "injuries": {"home": home_injuries, "away": away_injuries},
        "weather": weather,
        "story_prompts": [],
    }
    sections["story_prompts"] = story_prompts(
        away=game.away_team,
        home=game.home_team,
        week=game.week,
        roof=roof,
        generated_at=generated_at,
        sections=sections,
        line_history=line_history,
    )
    card = {
        "schema_version": 1,
        "game_id": game.game_id,
        "season": game.season,
        "week": game.week,
        "kickoff_utc": format_ts(game.kickoff_utc),
        "home": game.home_team,
        "away": game.away_team,
        "venue": row.stadium,
        "roof": roof,
        "surface": row.surface,
        "home_rest": row.home_rest,
        "away_rest": row.away_rest,
        "line_open": line_open,
        "line_latest": line_latest,
        "line_history": line_history,
        "generated_at": generated_at,
        "data_through_week": through,
        "sources": _sources(sections, status, line_history),
        "injuries_status": status,
        "sections": sections,
    }
    return card


def line_fields(rows: list[dict]) -> tuple[dict | None, dict | None, list[dict]]:
    """Join spread and total on snapshot_ts. n_books is the min when both exist."""
    grouped: dict[str, dict[str, dict | None]] = {}
    for row in rows:
        market = str(row.get("market") or "").strip().lower()
        if market in SPREAD_MARKETS:
            slot = "spread"
        elif market in TOTAL_MARKETS:
            slot = "total"
        else:
            raise PipelineError(f"unknown line market {market}")
        if row.get("line") is None or row.get("n_books") is None or not row.get("snapshot_ts"):
            continue
        stamp = format_ts(parse_ts(str(row["snapshot_ts"])))
        bucket = grouped.setdefault(stamp, {"spread": None, "total": None})
        if bucket[slot] is not None:
            raise PipelineError(f"duplicate {slot} line at {stamp}")
        bucket[slot] = {"line": float(row["line"]), "n_books": int(row["n_books"])}
    points: list[dict] = []
    for stamp in sorted(grouped, key=parse_ts):
        bucket = grouped[stamp]
        spread = bucket["spread"]
        total = bucket["total"]
        present = [item for item in (spread, total) if item is not None]
        if not present:
            continue
        n_books = min(int(item["n_books"]) for item in present)
        points.append(
            {
                "ts": stamp,
                "home_spread": None if spread is None else json_number(spread["line"]),
                "total": None if total is None else json_number(total["line"]),
                "n_books": n_books,
            }
        )
    if not points:
        return None, None, []
    return dict(points[0]), dict(points[-1]), points


def story_prompts(
    *,
    away: str,
    home: str,
    week: int,
    roof: str,
    generated_at: str,
    sections: dict,
    line_history: list[dict],
) -> list[dict]:
    """Exactly three prompts from this card's numbers, then neutral fillers."""
    found: list[dict] = []

    def add(title: str, body: str) -> None:
        if len(found) >= 3:
            return
        if BOOK_OR_DOLLAR.search(title) or BOOK_OR_DOLLAR.search(body):
            return
        if PROMPT_BAN.search(title) or PROMPT_BAN.search(body):
            return
        if not title.strip() or not body.strip():
            return
        found.append({"title": title, "body": body})

    def both(section: str, key: str) -> tuple[dict, dict] | None:
        left = sections[section]["away"][key]
        right = sections[section]["home"][key]
        if left["value"] is None or right["value"] is None:
            return None
        return left, right

    pair = both("epa", "off_epa_season")
    if pair:
        span = pair[0]["range"] or pair[1]["range"] or "the recorded weeks"
        add(
            "Offense EPA per play",
            f"{away} is {fmt_num(pair[0]['value'])} and {home} is {fmt_num(pair[1]['value'])} over {span}.",
        )
    pair = both("pass_rate_pace", "neutral_ed_pass_rate")
    if pair:
        add(
            "Neutral early-down pass rate",
            f"{away} is {fmt_num(pair[0]['value'])} and {home} is {fmt_num(pair[1]['value'])}.",
        )
    pair = both("sacks", "sack_rate_for")
    if pair:
        add(
            "Sack rate for",
            f"{away} defense is {fmt_num(pair[0]['value'])} and {home} defense is {fmt_num(pair[1]['value'])} per opponent dropback.",
        )
    pair = both("pass_rate_pace", "pace_sec_per_play")
    if pair:
        add(
            "Seconds between snaps",
            f"{away} averages {fmt_num(pair[0]['value'])} and {home} averages {fmt_num(pair[1]['value'])}.",
        )
    if line_history:
        first = line_history[0]
        last = line_history[-1]
        if first["home_spread"] is not None and last["home_spread"] is not None:
            start = first["home_spread"]
            end = last["home_spread"]
            if start == end:
                add("Home spread", f"The home spread is {fmt_num(start)} at the first and latest snapshot.")
            else:
                add(
                    "Home spread",
                    f"The home spread moved from {fmt_num(start)} to {fmt_num(end)}, "
                    f"a change of {fmt_num(abs(end - start))} points.",
                )
    pair = both("explosives", "explosive_rate_for")
    if pair:
        add(
            "Explosive play rate",
            f"{away} is {fmt_num(pair[0]['value'])} and {home} is {fmt_num(pair[1]['value'])}.",
        )
    pair = both("red_zone_downs", "rz_td_rate_for")
    if pair:
        add(
            "Red-zone touchdown rate",
            f"{away} is {fmt_num(pair[0]['value'])} and {home} is {fmt_num(pair[1]['value'])}.",
        )
    for title, body in (
        ("Teams", f"{away} at {home}, week {week}."),
        ("Roof class", f"The roof class is {roof}."),
        ("Generated", f"Numbers on this card were generated at {generated_at}."),
    ):
        add(title, body)
    if len(found) != 3:
        raise PipelineError(f"expected 3 story prompts, built {len(found)}")
    return found


def dump_card(card: dict) -> bytes:
    text = json.dumps(card, indent=2, ensure_ascii=False)
    if not text.endswith("\n"):
        text += "\n"
    return text.encode("utf-8")


def json_number(value: float) -> int | float:
    rounded = round(float(value), 6)
    if rounded == int(rounded):
        return int(rounded)
    return rounded


def fmt_num(value: float) -> str:
    number = json_number(value)
    return str(number)


def stat_number(value: float | None, source: str, sample: int | None, span: str | None) -> dict:
    if value is None or sample is None:
        return {"value": None, "source": source, "sample": None, "range": None}
    return {
        "value": json_number(value),
        "source": source,
        "sample": int(sample),
        "range": span,
    }


def _sources(sections: dict, injuries_status: str, line_history: list[dict]) -> list[str]:
    used = {"nflverse_schedules"}
    for node in _walk_stats(sections):
        if node["value"] is None:
            continue
        if node["source"] == PBP_SOURCE:
            used.add(PBP_SOURCE)
        elif node["source"] == NGS_SOURCE:
            used.add(NGS_SOURCE)
        elif node["source"] == NWS_SOURCE:
            used.add(NWS_SOURCE)
    summary = sections["weather"].get("summary")
    if isinstance(summary, str) and summary:
        used.add(NWS_SOURCE)
    if injuries_status == "reported":
        used.add("nflverse_injuries")
    if line_history:
        used.add("odds_api")
    return [name for name in SOURCE_ORDER if name in used]


def _walk_stats(node):
    if isinstance(node, dict) and {"value", "source", "sample", "range"} <= set(node):
        yield node
        return
    if isinstance(node, dict):
        for value in node.values():
            yield from _walk_stats(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk_stats(value)


def _assert_publishable(card: dict) -> None:
    history = card["line_history"]
    for previous, current in zip(history, history[1:], strict=False):
        if not parse_ts(current["ts"]) > parse_ts(previous["ts"]):
            raise PipelineError(f"{card['game_id']} line_history is not strictly ascending")
    if not history:
        if card["line_open"] is not None or card["line_latest"] is not None:
            raise PipelineError(f"{card['game_id']} empty history must have null open and latest")
    else:
        if card["line_open"] != history[0] or card["line_latest"] != history[-1]:
            raise PipelineError(f"{card['game_id']} open/latest must match the history ends")
    if card["injuries_status"] == "pending":
        injuries = card["sections"]["injuries"]
        if injuries["home"] or injuries["away"]:
            raise PipelineError(f"{card['game_id']} pending injuries must be empty")
    if len(card["sections"]["story_prompts"]) != 3:
        raise PipelineError(f"{card['game_id']} needs 3 story prompts")
    if len(card["sources"]) != len(set(card["sources"])):
        raise PipelineError(f"{card['game_id']} sources are duplicated")
    for text in _strings(card):
        if BOOK_OR_DOLLAR.search(text):
            raise PipelineError(f"{card['game_id']} text failed the public-card scan")
    for prompt in card["sections"]["story_prompts"]:
        blob = f"{prompt['title']} {prompt['body']}"
        if PROMPT_BAN.search(blob):
            raise PipelineError(f"{card['game_id']} story prompt failed the public-card scan")
    for side_name in ("home", "away"):
        for section, key in RATE_KEYS:
            value = card["sections"][section][side_name][key]["value"]
            if value is not None and not 0 <= value <= 1:
                raise PipelineError(f"{card['game_id']} {section}.{key} is outside 0 to 1")
        for key in ("sack_rate_for", "sack_rate_allowed"):
            source = card["sections"]["sacks"][side_name][key]["source"]
            if source != PBP_SOURCE:
                raise PipelineError(f"{card['game_id']} sack source must be nflverse_pbp")
        source = card["sections"]["qb"][side_name]["time_to_throw"]["source"]
        if source != NGS_SOURCE:
            raise PipelineError(f"{card['game_id']} time_to_throw source must be ngs_via_nflverse")


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        found: list[str] = []
        for item in value:
            found.extend(_strings(item))
        return found
    if isinstance(value, dict):
        found = []
        for item in value.values():
            found.extend(_strings(item))
        return found
    return []


def _team_game_ids(games: list[Game], season: int, through: int) -> dict[str, list[str]]:
    """Played game ids per team, oldest first, limited to weeks 1..through."""
    found: dict[str, list[Game]] = {}
    for game in games:
        if game.season != season or game.week > through or game.week < 1 or not game.has_final_score:
            continue
        for team in (game.home_team, game.away_team):
            found.setdefault(team, []).append(game)
    ordered: dict[str, list[str]] = {}
    for team, group in found.items():
        group.sort(key=lambda item: (_kick(item), item.game_id))
        ordered[team] = [item.game_id for item in group]
    return ordered


def _kick(game: Game) -> datetime:
    if game.kickoff_utc is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return game.kickoff_utc


def _plays_for(plays: list[Play], team_ids: list[str], season: int, through: int) -> list[Play]:
    chosen = set(team_ids)
    return [
        play
        for play in plays
        if play.game_id in chosen
        and play.season == season
        and 1 <= play.week <= through
        and play.season_type in {None, "REG"}
    ]


def _span(season: int, weeks: list[int]) -> str | None:
    if not weeks:
        return None
    return f"{season} wk{min(weeks)}{EN_DASH}{max(weeks)}"


def _weeks_for(plays: list[Play]) -> list[int]:
    return sorted({play.week for play in plays})


def _epa_side(plays: list[Play], team: str, season: int, through: int, season_ids: dict[str, list[str]]) -> dict:
    ids = season_ids.get(team, [])
    season_plays = _plays_for(plays, ids, season, through)
    l4_plays = _plays_for(plays, ids[-4:], season, through)
    season_span = f"{season} wk1{EN_DASH}{through}" if through >= 1 else None
    l4_span = _span(season, _weeks_for(l4_plays))
    return {
        "off_epa_season": _epa_stat(season_plays, team, offense=True, span=season_span),
        "off_epa_l4": _epa_stat(l4_plays, team, offense=True, span=l4_span),
        "def_epa_season": _epa_stat(season_plays, team, offense=False, span=season_span),
        "def_epa_l4": _epa_stat(l4_plays, team, offense=False, span=l4_span),
        "off_sr_season": _sr_stat(season_plays, team, offense=True, span=season_span),
        "def_sr_season": _sr_stat(season_plays, team, offense=False, span=season_span),
    }


def _epa_stat(plays: list[Play], team: str, *, offense: bool, span: str | None) -> dict:
    values = [
        play.epa
        for play in plays
        if play.epa is not None and _is_scrimmage(play) and _side(play, team, offense)
    ]
    if not values or span is None:
        return stat_number(None, PBP_SOURCE, None, None)
    return stat_number(sum(values) / len(values), PBP_SOURCE, len(values), span)


def _sr_stat(plays: list[Play], team: str, *, offense: bool, span: str | None) -> dict:
    values = [
        play.success
        for play in plays
        if play.success is not None and _is_scrimmage(play) and _side(play, team, offense)
    ]
    if not values or span is None:
        return stat_number(None, PBP_SOURCE, None, None)
    return stat_number(sum(values) / len(values), PBP_SOURCE, len(values), span)


def _pass_side(plays: list[Play], team: str, season: int, through: int, season_ids: dict[str, list[str]]) -> dict:
    ids = season_ids.get(team, [])
    chosen = _plays_for(plays, ids, season, through)
    span = f"{season} wk1{EN_DASH}{through}" if through >= 1 and chosen else None
    neutral = [play for play in chosen if play.posteam == team and _is_neutral(play)]
    if not neutral or span is None:
        pass_rate = stat_number(None, PBP_SOURCE, None, None)
    else:
        passes = sum(1 for play in neutral if play.pass_play == 1)
        pass_rate = stat_number(passes / len(neutral), PBP_SOURCE, len(neutral), span)
    pace_value, pace_sample = _pace(chosen, team)
    pace = stat_number(pace_value, PBP_SOURCE, pace_sample, span if pace_value is not None else None)
    return {"neutral_ed_pass_rate": pass_rate, "pace_sec_per_play": pace}


def _explosive_side(plays: list[Play], team: str, season: int, through: int, season_ids: dict[str, list[str]]) -> dict:
    ids = season_ids.get(team, [])
    chosen = _plays_for(plays, ids, season, through)
    span = f"{season} wk1{EN_DASH}{through}" if through >= 1 and chosen else None
    return {
        "explosive_rate_for": _explosive_stat(chosen, team, offense=True, span=span),
        "explosive_rate_allowed": _explosive_stat(chosen, team, offense=False, span=span),
    }


def _explosive_stat(plays: list[Play], team: str, *, offense: bool, span: str | None) -> dict:
    sample = [
        play
        for play in plays
        if _is_scrimmage(play) and play.yards_gained is not None and _side(play, team, offense)
    ]
    if not sample or span is None:
        return stat_number(None, PBP_SOURCE, None, None)
    hits = sum(1 for play in sample if _is_explosive(play))
    return stat_number(hits / len(sample), PBP_SOURCE, len(sample), span)


def _qb_side(
    plays: list[Play],
    ngs: list[NgsRow],
    team: str,
    season: int,
    through: int,
    season_ids: dict[str, list[str]],
) -> dict:
    ids = season_ids.get(team, [])
    chosen = _plays_for(plays, ids, season, through)
    span = f"{season} wk1{EN_DASH}{through}" if through >= 1 and chosen else None
    recent = ids[-1] if ids else None
    player_id, player_name = (None, None)
    if recent is not None:
        player_id, player_name = _dropback_leader(chosen, recent, team)
    if player_name is None and player_id is not None:
        player_name = _ngs_name(ngs, player_id, team) or player_id
    dropbacks = [
        play
        for play in chosen
        if play.posteam == team and play.qb_dropback == 1 and play.passer_player_id == player_id
    ] if player_id else []
    epa_values = [play.epa for play in dropbacks if play.epa is not None]
    cpoe_values = [play.cpoe for play in dropbacks if play.cpoe is not None]
    if player_id is None or not epa_values or span is None:
        epa = stat_number(None, PBP_SOURCE, None, None)
    else:
        epa = stat_number(sum(epa_values) / len(epa_values), PBP_SOURCE, len(epa_values), span)
    if player_id is None or not cpoe_values or span is None:
        cpoe = stat_number(None, PBP_SOURCE, None, None)
    else:
        cpoe = stat_number(sum(cpoe_values) / len(cpoe_values), PBP_SOURCE, len(cpoe_values), span)
    ttt, attempts = (None, None)
    if player_id is not None:
        ttt, attempts = _time_to_throw(ngs, player_id, player_name, team, season, through)
    return {
        "player": player_name if player_id is not None else None,
        "epa_per_db": epa,
        "cpoe": cpoe,
        "time_to_throw": stat_number(ttt, NGS_SOURCE, attempts, span if ttt is not None else None),
    }


def _sack_side(plays: list[Play], team: str, season: int, through: int, season_ids: dict[str, list[str]]) -> dict:
    ids = season_ids.get(team, [])
    chosen = _plays_for(plays, ids, season, through)
    span = f"{season} wk1{EN_DASH}{through}" if through >= 1 and chosen else None
    return {
        "sack_rate_for": _sack_stat(chosen, team, defense=True, span=span),
        "sack_rate_allowed": _sack_stat(chosen, team, defense=False, span=span),
    }


def _sack_stat(plays: list[Play], team: str, *, defense: bool, span: str | None) -> dict:
    sample = [
        play
        for play in plays
        if play.qb_dropback == 1 and (play.defteam == team if defense else play.posteam == team)
    ]
    if not sample or span is None:
        return stat_number(None, PBP_SOURCE, None, None)
    sacks = sum(1 for play in sample if play.sack == 1)
    return stat_number(sacks / len(sample), PBP_SOURCE, len(sample), span)


def _down_side(plays: list[Play], team: str, season: int, through: int, season_ids: dict[str, list[str]]) -> dict:
    ids = season_ids.get(team, [])
    chosen = _plays_for(plays, ids, season, through)
    span = f"{season} wk1{EN_DASH}{through}" if through >= 1 and chosen else None
    return {
        "rz_td_rate_for": _rz_stat(chosen, team, allowed=False, span=span),
        "rz_td_rate_allowed": _rz_stat(chosen, team, allowed=True, span=span),
        "third_down_rate_for": _down_stat(chosen, team, kind="third", allowed=False, span=span),
        "third_down_rate_allowed": _down_stat(chosen, team, kind="third", allowed=True, span=span),
        "fourth_down_rate_for": _down_stat(chosen, team, kind="fourth", allowed=False, span=span),
        "fourth_down_rate_allowed": _down_stat(chosen, team, kind="fourth", allowed=True, span=span),
    }


def _rz_stat(plays: list[Play], team: str, *, allowed: bool, span: str | None) -> dict:
    drives: dict[tuple, list[Play]] = {}
    for play in plays:
        if play.fixed_drive is None or not play.posteam:
            continue
        if allowed:
            if play.defteam != team:
                continue
        elif play.posteam != team:
            continue
        drives.setdefault((play.game_id, play.posteam, play.fixed_drive), []).append(play)
    trips = 0
    touchdowns = 0
    for group in drives.values():
        red = [
            play
            for play in group
            if play.yardline_100 is not None and 0 < play.yardline_100 <= 20
        ]
        if not red:
            continue
        trips += 1
        if any(play.touchdown == 1 for play in red):
            touchdowns += 1
    if trips == 0 or span is None:
        return stat_number(None, PBP_SOURCE, None, None)
    return stat_number(touchdowns / trips, PBP_SOURCE, trips, span)


def _down_stat(plays: list[Play], team: str, *, kind: str, allowed: bool, span: str | None) -> dict:
    converted = 0
    failed = 0
    for play in plays:
        if allowed:
            if play.defteam != team:
                continue
        elif play.posteam != team:
            continue
        if kind == "third":
            made, missed = play.third_down_converted, play.third_down_failed
        else:
            made, missed = play.fourth_down_converted, play.fourth_down_failed
        if made:
            converted += 1
        elif missed:
            failed += 1
    total = converted + failed
    if total == 0 or span is None:
        return stat_number(None, PBP_SOURCE, None, None)
    return stat_number(converted / total, PBP_SOURCE, total, span)


def _pace(plays: list[Play], team: str) -> tuple[float | None, int | None]:
    by_game: dict[str, list[Play]] = {}
    for play in plays:
        if play.posteam != team or not _is_scrimmage(play):
            continue
        by_game.setdefault(play.game_id, []).append(play)
    gaps: list[float] = []
    for group in by_game.values():
        group.sort(key=lambda play: play.play_id)
        for previous, current in zip(group, group[1:], strict=False):
            if not (_is_neutral(previous) and _is_neutral(current)):
                continue
            if _half(previous.qtr) != _half(current.qtr):
                continue
            if previous.half_seconds_remaining is None or current.half_seconds_remaining is None:
                continue
            gap = previous.half_seconds_remaining - current.half_seconds_remaining
            if gap > 0:
                gaps.append(gap)
    if not gaps:
        return None, None
    return sum(gaps) / len(gaps), len(gaps)


def _dropback_leader(plays: list[Play], game_id: str, team: str) -> tuple[str | None, str | None]:
    counts: dict[str, int] = {}
    names: dict[str, dict[str, int]] = {}
    for play in plays:
        if play.game_id != game_id or play.posteam != team or play.qb_dropback != 1:
            continue
        if not play.passer_player_id:
            continue
        counts[play.passer_player_id] = counts.get(play.passer_player_id, 0) + 1
        if play.passer_player_name:
            bucket = names.setdefault(play.passer_player_id, {})
            bucket[play.passer_player_name] = bucket.get(play.passer_player_name, 0) + 1
    if not counts:
        return None, None
    player_id = sorted(counts, key=lambda item: (-counts[item], item))[0]
    name_counts = names.get(player_id) or {}
    if not name_counts:
        return player_id, None
    player_name = sorted(name_counts, key=lambda item: (-name_counts[item], item))[0]
    return player_id, player_name


def _time_to_throw(
    rows: list[NgsRow],
    player_id: str,
    player_name: str | None,
    team: str,
    season: int,
    through: int,
) -> tuple[float | None, int | None]:
    matched = [
        row
        for row in rows
        if row.season == season
        and row.team == team
        and row.week is not None
        and 1 <= row.week <= through
        and row.season_type in {None, "REG"}
        and (
            row.player_id == player_id
            or (player_name is not None and row.short_name == player_name)
            or (player_name is not None and row.display_name == player_name)
        )
    ]
    by_id = [row for row in matched if row.player_id == player_id]
    chosen = by_id or matched
    weighted = 0.0
    attempts = 0
    for row in chosen:
        if row.avg_time_to_throw is None or row.attempts is None or row.attempts <= 0:
            continue
        weighted += row.avg_time_to_throw * row.attempts
        attempts += row.attempts
    if attempts == 0:
        return None, None
    return weighted / attempts, attempts


def _ngs_name(rows: list[NgsRow], player_id: str, team: str) -> str | None:
    for row in rows:
        if row.player_id == player_id and row.team == team and row.display_name:
            return row.display_name
    return None


def _injury_sides(
    rows: list[InjuryRow], season: int, week: int, home: str, away: str
) -> tuple[str, list[dict], list[dict]]:
    def side(team: str) -> list[dict]:
        matched = [
            row
            for row in rows
            if row.season == season
            and row.week == week
            and row.team == team
            and row.season_type in {None, "REG"}
        ]
        matched.sort(key=lambda row: (row.player, row.position, row.status, row.detail or ""))
        return [
            {
                "player": row.player,
                "position": row.position,
                "status": row.status,
                "detail": row.detail,
            }
            for row in matched
        ]

    # Reported when this game's teams have any row on that week's report.
    # A league-wide flag cannot be both reported (CHI/DET) and pending (KC/DEN)
    # inside one open week, which the golden cards require. A team with no row
    # keeps an empty list while its opponent is listed.
    home_rows = side(home)
    away_rows = side(away)
    if not home_rows and not away_rows:
        return "pending", [], []
    return "reported", home_rows, away_rows


def _injury_count(rows: list[InjuryRow], season: int, week: int) -> int:
    return sum(
        1
        for row in rows
        if row.season == season and row.week == week and row.season_type in {None, "REG"}
    )


def _weather_block(roof, country, lat, lon, kickoff, fixtures, transport) -> tuple[dict, bool]:
    if roof == "dome":
        return (
            {
                "mode": "indoor",
                "temp_f": None,
                "wind_mph": None,
                "precip_prob_pct": None,
                "summary": None,
            },
            False,
        )
    if roof not in {"outdoors", "retractable"} or country != "US" or lat is None or lon is None:
        return _null_weather("unavailable"), False
    mode = "outdoor" if roof == "outdoors" else "retractable"
    try:
        period = forecast_period(lat, lon, kickoff, fixtures=fixtures, transport=transport)
    except NWSError as exc:
        warn(f"NWS forecast failed for {format_coord(lat)},{format_coord(lon)}: {exc}")
        return _null_weather(mode), False
    if period is None:
        return _null_weather(mode), False
    temp = temperature_f(period)
    wind = wind_mph(period)
    precip = precip_percent(period)
    summary = period.get("shortForecast")
    if not isinstance(summary, str) or not summary.strip():
        summary = None
    else:
        summary = summary.strip()
    span = f"{period.get('startTime')}/{period.get('endTime')}"
    block = {
        "mode": mode,
        "temp_f": _nws_stat(temp, span),
        "wind_mph": _nws_stat(wind, span),
        "precip_prob_pct": _nws_stat(precip, span),
        "summary": summary,
    }
    used = summary is not None or any(
        block[key]["value"] is not None for key in ("temp_f", "wind_mph", "precip_prob_pct")
    )
    return block, used


def _null_weather(mode: str) -> dict:
    return {
        "mode": mode,
        "temp_f": _nws_stat(None, None),
        "wind_mph": _nws_stat(None, None),
        "precip_prob_pct": _nws_stat(None, None),
        "summary": None,
    }


def _nws_stat(value: float | None, span: str | None) -> dict:
    if value is None:
        return {"value": None, "source": NWS_SOURCE, "sample": None, "range": None}
    return {"value": json_number(value), "source": NWS_SOURCE, "sample": None, "range": span}


def _is_scrimmage(play: Play) -> bool:
    return play.posteam is not None and (play.pass_play == 1 or play.rush == 1)


def _is_neutral(play: Play) -> bool:
    return (
        _is_scrimmage(play)
        and play.down in {1, 2}
        and play.wp is not None
        and 0.20 <= play.wp <= 0.80
        and play.half_seconds_remaining is not None
        and play.half_seconds_remaining > 120
    )


def _is_explosive(play: Play) -> bool:
    if play.yards_gained is None:
        return False
    if play.rush == 1 and play.pass_play != 1:
        return play.yards_gained >= 10
    if play.pass_play == 1:
        return play.yards_gained >= 20
    return False


def _side(play: Play, team: str, offense: bool) -> bool:
    return play.posteam == team if offense else play.defteam == team


def _half(quarter: int | None) -> int | None:
    if quarter is None:
        return None
    if quarter <= 2:
        return 1
    if quarter <= 4:
        return 2
    return quarter


def _schedules(connection: duckdb.DuckDBPyConnection) -> list[ScheduleRow]:
    required = [
        "game_id",
        "season",
        "week",
        "gameday",
        "gametime",
        "away_team",
        "home_team",
        "away_score",
        "home_score",
        "stadium_id",
        "stadium",
        "surface",
        "home_rest",
        "away_rest",
    ]
    _require_columns(connection, "schedules", required)
    optional = ", game_type" if _has_column(connection, "schedules", "game_type") else ""
    rows = _fetch(
        connection,
        "SELECT game_id, season, week, gameday, gametime, away_team, home_team, "
        "away_score, home_score, stadium_id, stadium, surface, home_rest, away_rest"
        f"{optional} FROM schedules ORDER BY game_id",
    )
    schedules: list[ScheduleRow] = []
    seen: set[str] = set()
    for row in rows:
        game_type = _text(row.get("game_type"))
        if game_type == "PRE":
            continue
        game_id = _text(row.get("game_id"))
        if not game_id:
            raise PipelineError("schedules row is missing game_id")
        if game_id in seen:
            raise PipelineError(f"duplicate game_id in schedules: {game_id}")
        seen.add(game_id)
        kickoff = kickoff_utc_from_parts(_text(row.get("gameday")), _text(row.get("gametime")))
        schedules.append(
            ScheduleRow(
                game=Game(
                    game_id=game_id,
                    season=int(row["season"]),
                    week=int(row["week"]),
                    away_team=str(row["away_team"]),
                    home_team=str(row["home_team"]),
                    kickoff_utc=kickoff,
                    away_score=_int(row.get("away_score")),
                    home_score=_int(row.get("home_score")),
                    spread_line=None,
                    total_line=None,
                ),
                stadium_id=_text(row.get("stadium_id")),
                stadium=_text(row.get("stadium")),
                surface=_text(row.get("surface")),
                home_rest=_int(row.get("home_rest")),
                away_rest=_int(row.get("away_rest")),
                game_type=game_type,
            )
        )
    return schedules


def _plays(connection: duckdb.DuckDBPyConnection) -> list[Play]:
    columns = [
        "game_id",
        "season",
        "week",
        "play_id",
        "qtr",
        "half_seconds_remaining",
        "down",
        "wp",
        "posteam",
        "defteam",
        "pass",
        "rush",
        "epa",
        "success",
        "yards_gained",
        "sack",
        "qb_dropback",
        "passer_player_id",
        "passer_player_name",
        "cpoe",
        "touchdown",
        "yardline_100",
        "fixed_drive",
        "third_down_converted",
        "third_down_failed",
        "fourth_down_converted",
        "fourth_down_failed",
    ]
    _require_columns(connection, "pbp", columns)
    season_type = "season_type" if _has_column(connection, "pbp", "season_type") else "NULL AS season_type"
    quoted = ", ".join(f'"{column}"' if column == "pass" else column for column in columns)
    rows = _fetch(connection, f"SELECT {quoted}, {season_type} FROM pbp")
    plays: list[Play] = []
    for row in rows:
        plays.append(
            Play(
                game_id=str(row["game_id"]),
                season=int(row["season"]),
                week=int(row["week"]),
                season_type=_text(row.get("season_type")),
                play_id=int(row["play_id"]),
                qtr=_int(row.get("qtr")),
                half_seconds_remaining=_float(row.get("half_seconds_remaining")),
                down=_int(row.get("down")),
                wp=_float(row.get("wp")),
                posteam=_text(row.get("posteam")),
                defteam=_text(row.get("defteam")),
                pass_play=_flag(row.get("pass")),
                rush=_flag(row.get("rush")),
                epa=_float(row.get("epa")),
                success=_float(row.get("success")),
                yards_gained=_float(row.get("yards_gained")),
                sack=_flag(row.get("sack")),
                qb_dropback=_flag(row.get("qb_dropback")),
                passer_player_id=_text(row.get("passer_player_id")),
                passer_player_name=_text(row.get("passer_player_name")),
                cpoe=_float(row.get("cpoe")),
                touchdown=_flag(row.get("touchdown")),
                yardline_100=_float(row.get("yardline_100")),
                fixed_drive=_int(row.get("fixed_drive")),
                third_down_converted=_flag(row.get("third_down_converted")),
                third_down_failed=_flag(row.get("third_down_failed")),
                fourth_down_converted=_flag(row.get("fourth_down_converted")),
                fourth_down_failed=_flag(row.get("fourth_down_failed")),
            )
        )
    return plays


def _injuries(connection: duckdb.DuckDBPyConnection) -> list[InjuryRow]:
    required = ["season", "week", "team", "full_name", "position", "report_status", "report_primary_injury"]
    _require_columns(connection, "injuries", required)
    season_type = "season_type" if _has_column(connection, "injuries", "season_type") else "NULL AS season_type"
    rows = _fetch(
        connection,
        "SELECT season, week, team, full_name, position, report_status, "
        f"report_primary_injury, {season_type} FROM injuries",
    )
    injuries: list[InjuryRow] = []
    for row in rows:
        player = _text(row.get("full_name"))
        position = _text(row.get("position"))
        status = _text(row.get("report_status"))
        team = _text(row.get("team"))
        if not player or not position or not status or not team:
            continue
        if row.get("season") is None or row.get("week") is None:
            continue
        injuries.append(
            InjuryRow(
                season=int(row["season"]),
                week=int(row["week"]),
                season_type=_text(row.get("season_type")),
                team=team,
                player=player,
                position=position,
                status=status,
                detail=_text(row.get("report_primary_injury")),
            )
        )
    return injuries


def _ngs(connection: duckdb.DuckDBPyConnection) -> list[NgsRow]:
    required = [
        "season",
        "week",
        "team_abbr",
        "player_gsis_id",
        "player_display_name",
        "player_short_name",
        "avg_time_to_throw",
        "attempts",
    ]
    _require_columns(connection, "ngs_passing", required)
    season_type = "season_type" if _has_column(connection, "ngs_passing", "season_type") else "NULL AS season_type"
    rows = _fetch(
        connection,
        "SELECT season, week, team_abbr, player_gsis_id, player_display_name, "
        f"player_short_name, avg_time_to_throw, attempts, {season_type} FROM ngs_passing",
    )
    parsed: list[NgsRow] = []
    for row in rows:
        team = _text(row.get("team_abbr"))
        if not team or row.get("season") is None or row.get("week") is None:
            continue
        parsed.append(
            NgsRow(
                season=int(row["season"]),
                week=int(row["week"]),
                season_type=_text(row.get("season_type")),
                team=team,
                player_id=_text(row.get("player_gsis_id")),
                display_name=_text(row.get("player_display_name")),
                short_name=_text(row.get("player_short_name")),
                avg_time_to_throw=_float(row.get("avg_time_to_throw")),
                attempts=_int(row.get("attempts")),
            )
        )
    return parsed


def _lines(connection: duckdb.DuckDBPyConnection) -> list[dict]:
    _require_columns(
        connection,
        "lines",
        ["snapshot_ts", "game_id", "market", "line", "n_books"],
    )
    return _fetch(
        connection,
        "SELECT snapshot_ts, game_id, market, line, n_books FROM lines",
    )


def _require_columns(connection: duckdb.DuckDBPyConnection, table: str, columns: list[str]) -> None:
    have = _columns(connection, table)
    missing = [column for column in columns if column not in have]
    if missing:
        raise PipelineError(f"{table} is missing columns: {', '.join(missing)}")


def _has_column(connection: duckdb.DuckDBPyConnection, table: str, column: str) -> bool:
    return column in _columns(connection, table)


def _columns(connection: duckdb.DuckDBPyConnection, table: str) -> set[str]:
    try:
        rows = connection.execute(f"DESCRIBE {table}").fetchall()
    except duckdb.CatalogException as exc:
        raise PipelineError(f"duckdb is missing table {table}") from exc
    return {str(row[0]) for row in rows}


def _fetch(connection: duckdb.DuckDBPyConnection, sql: str) -> list[dict]:
    cursor = connection.execute(sql)
    names = [item[0] for item in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def _text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "nan", "null"}:
        return None
    return text


def _int(value) -> int | None:
    number = _float(value)
    if number is None:
        return None
    return int(number)


def _float(value) -> float | None:
    if value is None or value == "":
        return None
    number = float(value)
    if number != number:
        return None
    return number


def _flag(value) -> int:
    number = _int(value)
    return 1 if number else 0
