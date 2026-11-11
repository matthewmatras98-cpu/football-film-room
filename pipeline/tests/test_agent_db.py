"""Agent database: public inputs, read-only file, and card parity."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pytest

from football_pipeline.agent_db import CREDIT_LINES, PUBLIC_VIEWS, build_agent_db, is_stale
from football_pipeline.cards import (
    _injuries,
    _ngs,
    _plays,
    _schedules,
    _span,
    build_card,
    completed_through,
    json_number,
)
from football_pipeline.schema import PipelineError
from tests.agent_support import CARDS, prepare_agent_root
from tests.conftest import run_cli

AS_OF = "2099-09-18T16:00:00Z"
TEAMS = {"CHI", "DET", "KC", "DEN", "MIN", "GB", "TEN", "HOU"}
BOOK_NAMES = (
    "draftkings",
    "fanduel",
    "betmgm",
    "betrivers",
    "bovada",
    "betonline",
    "lowvig",
    "mybookie",
    "betus",
    "booksentinel",
)
EPA_FIELDS = (
    ("off_epa_season", "season", "for", "epa_per_play"),
    ("off_epa_l4", "l4", "for", "epa_per_play"),
    ("def_epa_season", "season", "allowed", "epa_per_play"),
    ("def_epa_l4", "l4", "allowed", "epa_per_play"),
    ("off_sr_season", "season", "for", "success_rate"),
    ("def_sr_season", "season", "allowed", "success_rate"),
)


def test_is_stale_boundaries(tmp_path: Path):
    path = tmp_path / "football.duckdb"
    now = datetime(2099, 6, 2, 12, 0, tzinfo=timezone.utc)
    assert is_stale(path, now) is True
    path.write_bytes(b"")
    _age(path, now - timedelta(hours=24))
    assert is_stale(path, now) is False
    _age(path, now - timedelta(hours=24, seconds=1))
    assert is_stale(path, now) is True
    _age(path, now - timedelta(hours=23, minutes=59))
    assert is_stale(path, now) is False
    with pytest.raises(PipelineError):
        is_stale(path, datetime(2099, 6, 2, 12, 0))


def test_agent_db_is_read_only_and_ignores_the_dump(tmp_path: Path):
    root, fixtures = prepare_agent_root(tmp_path)
    database = tmp_path / "football.duckdb"
    first = run_cli(
        [
            "agent-db",
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
    assert first.returncode == 0, first.stderr + first.stdout
    assert "agent-db season 2099:" in first.stdout
    assert "schedules=12" in first.stdout
    assert "pbp=66" in first.stdout
    assert "snapshots_dump" not in first.stdout
    assert "BOOKSENTINEL" not in first.stdout
    assert "91.25" not in first.stdout
    assert (database.stat().st_mode & 0o777) == 0o444
    assert not list(database.parent.glob("*.partial*"))
    assert not list(database.parent.glob("*.wal"))

    connection = duckdb.connect(str(database), read_only=True)
    try:
        tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
        assert "snapshots_dump" not in tables
        for name in ("meta", "credits", *PUBLIC_VIEWS):
            assert name in tables
        meta = connection.execute("SELECT built_at, season, repo_sha FROM meta").fetchone()
        assert meta[0].endswith("Z")
        assert meta[1] == 2099
        assert meta[2] is None
        credits = [row[0] for row in connection.execute("SELECT line FROM credits").fetchall()]
        assert credits == list(CREDIT_LINES)
        line_columns = {row[0] for row in connection.execute("DESCRIBE v_lines").fetchall()}
        assert "min" not in line_columns
        assert "max" not in line_columns
        blob = _blob(connection).lower()
        assert "91.25" not in blob
        for name in BOOK_NAMES:
            assert name not in blob
        latest = connection.execute(
            """
            SELECT line, n_books, source, sample, "range"
            FROM v_lines
            WHERE game_id = '2099_03_CHI_DET' AND market = 'spread'
            ORDER BY snapshot_ts DESC
            LIMIT 1
            """
        ).fetchone()
        assert latest[0] == -2.5
        assert latest[1] == 5
        assert latest[2] == "median of 5 US books (data/lines)"
        assert latest[3] == 5
        assert latest[4] == "2099-09-18T12:00:00Z"
        players = {row[0] for row in connection.execute("SELECT player FROM v_injuries").fetchall()}
        assert "A. Tackle" in players
        assert "E. Old" not in players
        injury = connection.execute(
            'SELECT source, sample, "range" FROM v_injuries WHERE player = ?',
            ["A. Tackle"],
        ).fetchone()
        assert injury[0] == "nflverse_injuries"
        assert injury[1] == 1
        assert injury[2] == _span(2099, [3])
        card = connection.execute(
            """
            SELECT value, source, sample, "range"
            FROM v_cards
            WHERE game_id = '2099_03_CHI_DET' AND field = 'sections.epa.away.off_epa_season'
            """
        ).fetchone()
        assert json_number(card[0]) == 0.56
        assert card[1:] == ("nflverse_pbp", 10, "2099 wk1\u20132")
        catalog = {row[0] for row in connection.execute("SELECT view_name FROM v_catalog").fetchall()}
        assert catalog == set(PUBLIC_VIEWS)
        with pytest.raises(duckdb.Error):
            connection.execute("CREATE TABLE hack(x INTEGER)")
    finally:
        connection.close()
    with pytest.raises(duckdb.Error):
        duckdb.connect(str(database))

    second = run_cli(
        [
            "agent-db",
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
    assert second.returncode == 0, second.stderr + second.stdout
    assert (database.stat().st_mode & 0o777) == 0o444


def test_view_values_match_build_card(tmp_path: Path):
    root, fixtures = prepare_agent_root(tmp_path)
    database = tmp_path / "football.duckdb"
    build_agent_db(root, 2099, database, fixtures=fixtures)
    connection = duckdb.connect(str(database), read_only=True)
    try:
        schedules = _schedules(connection)
        plays = _plays(connection)
        injuries = _injuries(connection)
        ngs = _ngs(connection)
        lines = [
            {
                "snapshot_ts": row[0],
                "game_id": row[1],
                "market": row[2],
                "line": row[3],
                "n_books": row[4],
            }
            for row in connection.execute(
                "SELECT snapshot_ts, game_id, market, line, n_books FROM lines"
            ).fetchall()
        ]
        games = [row.game for row in schedules]
        through = completed_through(games, 2099)
        seen: set[str] = set()
        for row in schedules:
            if row.game.week != 3:
                continue
            card = build_card(
                row,
                games=games,
                plays=plays,
                injuries=injuries,
                ngs=ngs,
                lines=lines,
                through=through,
                generated_at=AS_OF,
                nws_fixtures=CARDS / "nws",
                transport=None,
            )
            for team, side_name in ((card["home"], "home"), (card["away"], "away")):
                seen.add(team)
                _compare_side(connection, team, card["sections"], side_name)
        assert seen == TEAMS
    finally:
        connection.close()


def test_agent_sources_do_not_name_secrets():
    root = Path(__file__).resolve().parents[1] / "football_pipeline"
    banned = (
        "football_pipeline.odds",
        "football_pipeline.dispatch",
        "football_pipeline.d1",
        "ODDS_API_KEY",
        "CF_API_TOKEN",
        "CF_ACCOUNT_ID",
        "D1_DATABASE_ID",
        "GITHUB_TOKEN",
        "apiKey",
        "os.environ",
    )
    for name in ("agent_db.py", "agent_eval.py", "factcheck.py"):
        text = (root / name).read_text(encoding="utf-8")
        for token in banned:
            assert token not in text, name
    assert "include_dump=False" in (root / "agent_db.py").read_text(encoding="utf-8")


def _compare_side(connection, team: str, sections: dict, side_name: str) -> None:
    epa = sections["epa"][side_name]
    for key, scope, side, metric in EPA_FIELDS:
        _expect(
            connection,
            "v_team_epa",
            "team = ? AND scope = ? AND side = ? AND metric = ?",
            [team, scope, side, metric],
            epa[key],
        )
    pace = sections["pass_rate_pace"][side_name]
    for key, metric in (
        ("neutral_ed_pass_rate", "neutral_ed_pass_rate"),
        ("pace_sec_per_play", "pace_sec_per_play"),
    ):
        _expect(
            connection,
            "v_pass_rate_pace",
            "team = ? AND scope = 'season' AND metric = ?",
            [team, metric],
            pace[key],
        )
    explosive = sections["explosives"][side_name]
    for key, side in (("explosive_rate_for", "for"), ("explosive_rate_allowed", "allowed")):
        _expect(
            connection,
            "v_explosives",
            "team = ? AND scope = 'season' AND side = ? AND metric = 'explosive_rate'",
            [team, side],
            explosive[key],
        )
    qb = sections["qb"][side_name]
    player = connection.execute(
        "SELECT player FROM v_qb WHERE team = ? AND scope = 'season' AND metric = 'cpoe'",
        [team],
    ).fetchone()
    assert player is not None
    assert player[0] == qb["player"]
    for key, metric in (
        ("epa_per_db", "epa_per_dropback"),
        ("cpoe", "cpoe"),
        ("time_to_throw", "time_to_throw"),
    ):
        _expect(
            connection,
            "v_qb",
            "team = ? AND scope = 'season' AND metric = ?",
            [team, metric],
            qb[key],
        )
    sacks = sections["sacks"][side_name]
    for key, side in (("sack_rate_for", "for"), ("sack_rate_allowed", "allowed")):
        _expect(
            connection,
            "v_pass_rush",
            "team = ? AND scope = 'season' AND side = ? AND metric = 'sack_rate'",
            [team, side],
            sacks[key],
        )
    downs = sections["red_zone_downs"][side_name]
    for key, side, metric in (
        ("rz_td_rate_for", "for", "rz_td_rate"),
        ("rz_td_rate_allowed", "allowed", "rz_td_rate"),
        ("third_down_rate_for", "for", "third_down_rate"),
        ("third_down_rate_allowed", "allowed", "third_down_rate"),
        ("fourth_down_rate_for", "for", "fourth_down_rate"),
        ("fourth_down_rate_allowed", "allowed", "fourth_down_rate"),
    ):
        _expect(
            connection,
            "v_red_zone_downs",
            "team = ? AND scope = 'season' AND side = ? AND metric = ?",
            [team, side, metric],
            downs[key],
        )


def _expect(connection, view: str, where: str, params: list, stat: dict) -> None:
    rows = connection.execute(
        f'SELECT value, sample, "range", source FROM {view} WHERE {where}',
        params,
    ).fetchall()
    assert len(rows) == 1, (view, where, params, rows)
    value, sample, span, source = rows[0]
    if stat["value"] is None:
        assert value is None and sample is None and span is None, (view, params, rows[0], stat)
    else:
        assert json_number(value) == stat["value"], (view, params, value, stat)
        assert int(sample) == stat["sample"]
        assert span == stat["range"]
    assert source == stat["source"]


def _blob(connection) -> str:
    """Text of every cell, cast in SQL so a timestamp never needs pytz."""
    chunks: list[str] = []
    names = [row[0] for row in connection.execute("SHOW TABLES").fetchall()]
    for name in names:
        if not str(name).isidentifier():
            continue
        described = connection.execute(f"DESCRIBE {name}").fetchall()
        if not described:
            continue
        quoted = ", ".join(f'CAST("{column[0]}" AS VARCHAR)' for column in described)
        for row in connection.execute(f"SELECT {quoted} FROM {name}").fetchall():
            chunks.append(" ".join("" if cell is None else str(cell) for cell in row))
    return "\n".join(chunks)


def _age(path: Path, when: datetime) -> None:
    stamp = when.timestamp()
    os.utime(path, (stamp, stamp))
