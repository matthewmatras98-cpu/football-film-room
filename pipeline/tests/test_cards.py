"""Card generator: lines, injuries, weather, venues, and golden bytes."""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from football_pipeline.cards import (
    BOOK_OR_DOLLAR,
    _injury_sides,
    _weather_block,
    completed_through,
    line_fields,
    select_open_weeks,
)
from football_pipeline.cards import InjuryRow
from football_pipeline.close import Game
from football_pipeline.venues import format_coord, load_venues, lookup_venue
from football_pipeline.weather import parse_wind_mph, period_covering
from tests.conftest import FIXTURES, run_cli

ROOT = Path(__file__).resolve().parents[2]
CARDS = FIXTURES / "cards"
AS_OF = "2099-09-18T16:00:00Z"
UTC = timezone.utc
BOOK_SCAN = re.compile(
    r"\$|draftkings|fanduel|betmgm|betrivers|bovada|betonline|lowvig|mybookie|betus",
    re.IGNORECASE,
)


def test_generator_matches_golden_cards_byte_for_byte():
    script = ROOT / "scripts" / "check_generated_cards.py"
    result = subprocess.run(
        [sys.executable, str(script), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "generated cards match" in result.stdout


def test_line_history_joins_markets_and_sorts():
    history_open, history_latest, history = line_fields(
        [
            {"snapshot_ts": "2099-09-18T12:00:00Z", "market": "spread", "line": -2.5, "n_books": 5},
            {"snapshot_ts": "2099-09-16T12:00:00Z", "market": "spreads", "line": -3.5, "n_books": 6},
            {"snapshot_ts": "2099-09-16T12:00:00Z", "market": "totals", "line": 45.5, "n_books": 4},
        ]
    )
    assert [point["ts"] for point in history] == [
        "2099-09-16T12:00:00Z",
        "2099-09-18T12:00:00Z",
    ]
    assert history[0] == {
        "ts": "2099-09-16T12:00:00Z",
        "home_spread": -3.5,
        "total": 45.5,
        "n_books": 4,
    }
    assert history[1]["home_spread"] == -2.5
    assert history[1]["total"] is None
    assert history[1]["n_books"] == 5
    assert history_open == history[0]
    assert history_latest == history[1]
    assert "min" not in history[0]
    assert "max" not in history[0]
    assert line_fields([]) == (None, None, [])


def test_open_weeks_skip_the_completed_week():
    final = _game("2099_02_CHI_DET", datetime(2099, 9, 14, 17, 0, tzinfo=UTC), week=2, scores=(20, 17))
    open_game = _game("2099_03_CHI_DET", datetime(2099, 9, 21, 17, 0, tzinfo=UTC), week=3)
    far = _game("2099_08_KC_DEN", datetime(2099, 11, 1, 17, 0, tzinfo=UTC), week=8, home="DEN", away="KC")
    as_of = datetime(2099, 9, 18, 16, 0, tzinfo=UTC)
    assert select_open_weeks([final, open_game, far], as_of) == {(2099, 3)}
    assert completed_through([final, open_game], 2099) == 2
    assert completed_through([open_game], 2099) == 0


def test_injuries_do_not_fall_back_and_pending_is_per_game():
    rows = [
        InjuryRow(2099, 2, "REG", "DEN", "E. Old", "QB", "Out", "Shoulder"),
        InjuryRow(2099, 3, "REG", "CHI", "A. Tackle", "T", "Questionable", "Ankle"),
    ]
    status, home, away = _injury_sides(rows, 2099, 3, "DET", "CHI")
    assert status == "reported"
    assert home == []
    assert away[0]["player"] == "A. Tackle"
    pending, home_empty, away_empty = _injury_sides(rows, 2099, 3, "DEN", "KC")
    assert pending == "pending"
    assert home_empty == []
    assert away_empty == []
    assert all(row["player"] != "E. Old" for row in home_empty + away_empty)


def test_weather_modes_and_wind_parse(capsys, tmp_path: Path):
    kickoff = datetime(2099, 9, 21, 18, 0, tzinfo=UTC)
    indoor, used = _weather_block("dome", "US", 42.34, -83.04556, kickoff, Path("/no/such/dir"), None)
    assert indoor["mode"] == "indoor"
    assert indoor["temp_f"] is None
    assert used is False
    assert capsys.readouterr().out == ""

    abroad, _used = _weather_block("outdoors", "GB", 51.5556, -0.2794, kickoff, None, None)
    assert abroad["mode"] == "unavailable"
    assert abroad["temp_f"]["value"] is None
    assert abroad["temp_f"]["source"] == "nws"

    unknown, _used = _weather_block("unknown", "US", None, None, kickoff, None, None)
    assert unknown["mode"] == "unavailable"

    failed, used = _weather_block("outdoors", "US", 39.74389, -105.02, kickoff, tmp_path, None)
    warned = capsys.readouterr().out
    assert failed["mode"] == "outdoor"
    assert failed["temp_f"]["value"] is None
    assert failed["temp_f"]["source"] == "nws"
    assert used is False
    assert "::warning::" in warned
    assert "39.74389,-105.02" in warned

    assert parse_wind_mph("10 mph") == 10
    assert parse_wind_mph("7 to 11 mph") == 9
    period = period_covering(
        [
            {"startTime": "2099-09-21T17:00:00Z", "endTime": "2099-09-21T18:00:00Z"},
            {"startTime": "2099-09-21T18:00:00Z", "endTime": "2099-09-21T19:00:00Z"},
        ],
        kickoff,
    )
    assert period is not None
    assert period["startTime"] == "2099-09-21T18:00:00Z"
    assert period_covering([{"startTime": "2099-09-21T18:00:00Z", "endTime": "2099-09-21T19:00:00Z"}], kickoff.replace(hour=20)) is None


def test_2026_venue_names_do_not_trip_the_card_scan():
    table = load_venues()
    assert len(table.by_id) == 38
    for venue in table.by_id.values():
        assert venue.roof_class in {"outdoors", "dome", "retractable"}
        assert -90 <= venue.lat <= 90
        assert -180 <= venue.lon <= 180
        assert BOOK_SCAN.search(venue.stadium) is None
        assert "$" not in venue.stadium
    assert lookup_venue("DET00", "Ford Field").roof_class == "dome"
    assert lookup_venue("HOU00", "Reliant Stadium").roof_class == "retractable"
    assert lookup_venue("LAX01", "SoFi Stadium").roof_class == "dome"
    tottenham = lookup_venue("JAX00", "Tottenham Hotspur Stadium")
    assert tottenham.stadium_id == "LON02"
    assert tottenham.country == "GB"
    assert tottenham.roof_class == "outdoors"
    assert lookup_venue("JAX00", "EverBank Stadium").country == "US"
    assert lookup_venue("NOPE", "Not a stadium") is None
    assert format_coord(lookup_venue("DEN00", "Empower Field at Mile High").lon) == "-105.02"


def test_cards_leave_other_weeks_and_ignore_the_dump(tmp_path: Path):
    root = tmp_path / "repo"
    games = root / "src" / "content" / "games"
    games.mkdir(parents=True)
    keep = games / "2099_01_KEEP.json"
    keep.write_text("keep\n", encoding="utf-8")
    fixtures = tmp_path / "fixtures"
    _copy_tree(CARDS, fixtures)
    dump = fixtures / "week.d1dump.csv"
    dump.write_text(
        "game_id,market,book,point,snapshot_ts\n"
        "2099_03_CHI_DET,spread,BOOKSENTINEL,-3.5,2099-09-16T12:00:00Z\n",
        encoding="utf-8",
    )
    database = tmp_path / "football.duckdb"
    built = run_cli(
        [
            "build-db",
            "--root",
            str(root),
            "--season",
            "2099",
            "--out",
            str(database),
            "--fixtures",
            str(fixtures),
        ]
    )
    assert built.returncode == 0, built.stderr
    assert "snapshots_dump=" in built.stdout
    written = run_cli(
        [
            "cards",
            "--root",
            str(root),
            "--db",
            str(database),
            "--as-of",
            AS_OF,
            "--nws-fixtures",
            str(fixtures / "nws"),
        ]
    )
    assert written.returncode == 0, written.stderr + written.stdout
    assert "injuries 2099 wk03: 6 row(s)" in written.stdout
    assert keep.read_text(encoding="utf-8") == "keep\n"
    names = sorted(path.name for path in games.glob("*.json"))
    assert names == [
        "2099_01_KEEP.json",
        "2099_03_CHI_DET.json",
        "2099_03_KC_DEN.json",
        "2099_03_MIN_GB.json",
        "2099_03_TEN_HOU.json",
    ]
    for path in games.glob("2099_03_*.json"):
        text = path.read_text(encoding="utf-8")
        assert "BOOKSENTINEL" not in text
        assert "91.25" not in text
        assert BOOK_OR_DOLLAR.search(text) is None
    again = run_cli(
        [
            "cards",
            "--root",
            str(root),
            "--db",
            str(database),
            "--as-of",
            AS_OF,
            "--nws-fixtures",
            str(fixtures / "nws"),
        ]
    )
    assert again.returncode == 0, again.stderr
    for name in names:
        if name.startswith("2099_03_"):
            assert (games / name).read_bytes() == (ROOT / "tests" / "fixtures" / "generated-cards" / name).read_bytes()


def test_build_db_fixture_counts_and_skips_a_missing_dump(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    database = tmp_path / "football.duckdb"
    result = run_cli(
        [
            "build-db",
            "--root",
            str(root),
            "--season",
            "2099",
            "--out",
            str(database),
            "--fixtures",
            str(CARDS),
        ]
    )
    assert result.returncode == 0, result.stderr
    assert "schedules=12" in result.stdout
    assert "pbp=66" in result.stdout
    assert "injuries=7" in result.stdout
    assert "ngs_passing=14" in result.stdout
    assert "lines=9" in result.stdout
    assert "snapshots_dump" not in result.stdout
    import duckdb

    connection = duckdb.connect(str(database), read_only=True)
    try:
        tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
    finally:
        connection.close()
    assert tables == {"injuries", "lines", "ngs_passing", "pbp", "schedules"}


def _copy_tree(source: Path, dest: Path) -> None:
    import shutil

    shutil.copytree(source, dest)


def _game(game_id: str, kickoff: datetime, week: int, home: str = "DET", away: str = "CHI", scores=None) -> Game:
    away_score, home_score = scores if scores else (None, None)
    return Game(
        game_id=game_id,
        season=2099,
        week=week,
        away_team=away,
        home_team=home,
        kickoff_utc=kickoff,
        away_score=away_score,
        home_score=home_score,
        spread_line=None,
        total_line=None,
    )
