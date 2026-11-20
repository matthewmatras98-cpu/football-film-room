"""Fact-check helper: confirmed, corrected, and unverifiable claims."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from football_pipeline.agent_db import build_agent_db
from football_pipeline.cards import EN_DASH
from football_pipeline.factcheck import factcheck
from tests.agent_support import AGENT, prepare_agent_root
from tests.conftest import run_cli

SPAN = f"2099 wk1{EN_DASH}2"
FACTCHECK = AGENT / "factcheck"
DRAFTS = sorted(path.name for path in FACTCHECK.glob("draft_*.mdx"))


def _expected() -> list[dict]:
    return [
        {
            "text": "CHI EPA per play is \u00b10.56 this season.",
            "team": "CHI",
            "stat": "epa_per_play",
            "claimed": 0.56,
            "status": "confirmed",
            "actual": 0.56,
            "source": "nflverse_pbp",
            "sample": 10,
            "range": SPAN,
            "reason": None,
        },
        {
            "text": "DET sack rate is 33.3333%.",
            "team": "DET",
            "stat": "sack_rate",
            "claimed": 33.3333,
            "status": "confirmed",
            "actual": 0.333333,
            "source": "nflverse_pbp",
            "sample": 6,
            "range": SPAN,
            "reason": None,
        },
        {
            "text": "CHI EPA per play is 0.99 this season.",
            "team": "CHI",
            "stat": "epa_per_play",
            "claimed": 0.99,
            "status": "corrected",
            "actual": 0.56,
            "source": "nflverse_pbp",
            "sample": 10,
            "range": SPAN,
            "reason": None,
        },
        {
            "text": "CHI explosive rate is 0.1.",
            "team": "CHI",
            "stat": "explosive_rate",
            "claimed": 0.1,
            "status": "corrected",
            "actual": 0.4,
            "source": "nflverse_pbp",
            "sample": 10,
            "range": SPAN,
            "reason": None,
        },
        {
            "text": "The record is 2-1-1.",
            "team": None,
            "stat": "record",
            "claimed": "2-1-1",
            "status": "confirmed",
            "actual": "2-1-1",
            "source": "graded.csv",
            "sample": 4,
            "range": "2099",
            "reason": None,
        },
        {
            "text": "BUF sack rate is 0.2.",
            "team": "BUF",
            "stat": "sack_rate",
            "claimed": 0.2,
            "status": "unverifiable",
            "actual": None,
            "source": "no matching view",
            "sample": None,
            "range": None,
            "reason": "no row for this team",
        },
        {
            "text": "CHI special teams rate is 0.5.",
            "team": "CHI",
            "stat": None,
            "claimed": 0.5,
            "status": "unverifiable",
            "actual": None,
            "source": "no matching view",
            "sample": None,
            "range": None,
            "reason": "no mapped stat phrase",
        },
    ]


def test_factcheck_exact_list(tmp_path):
    root, fixtures = prepare_agent_root(tmp_path)
    database = tmp_path / "football.duckdb"
    build_agent_db(root, 2099, database, fixtures=fixtures)
    found = factcheck(database, AGENT / "draft.mdx")
    assert found == _expected()
    statuses = [item["status"] for item in found]
    assert statuses.count("confirmed") >= 2
    assert statuses.count("corrected") >= 2
    assert statuses.count("unverifiable") >= 2
    assert all(item["source"] for item in found)
    cli = run_cli(
        ["agent-factcheck", "--db", str(database), "--mdx", str(AGENT / "draft.mdx")]
    )
    assert cli.returncode == 0, cli.stderr + cli.stdout
    assert json.loads(cli.stdout) == _expected()


def _fixture_expected(draft_name: str) -> list[dict]:
    name = draft_name.removeprefix("draft_").removesuffix(".mdx")
    path = FACTCHECK / f"expected_{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("draft_name", DRAFTS)
def test_factcheck_fixtures(tmp_path, draft_name):
    root, fixtures = prepare_agent_root(tmp_path)
    database = tmp_path / "football.duckdb"
    build_agent_db(root, 2099, database, fixtures=fixtures)
    found = factcheck(database, FACTCHECK / draft_name)
    assert found == _fixture_expected(draft_name)


def test_factcheck_fixture_cli(tmp_path):
    root, fixtures = prepare_agent_root(tmp_path)
    database = tmp_path / "football.duckdb"
    build_agent_db(root, 2099, database, fixtures=fixtures)
    draft = FACTCHECK / "draft_confirmed.mdx"
    cli = run_cli(["agent-factcheck", "--db", str(database), "--mdx", str(draft)])
    assert cli.returncode == 0, cli.stderr + cli.stdout
    assert json.loads(cli.stdout) == _fixture_expected(draft.name)


def test_null_view_row_is_unverifiable(tmp_path):
    root, fixtures = prepare_agent_root(tmp_path)
    database = tmp_path / "football.duckdb"
    build_agent_db(root, 2099, database, fixtures=fixtures)
    connection = duckdb.connect(str(database), read_only=True)
    try:
        stored = connection.execute(
            """
            SELECT value FROM v_pass_rush
            WHERE team = 'KC' AND scope = 'season'
              AND side = 'for' AND metric = 'sack_rate'
            """
        ).fetchone()
    finally:
        connection.close()
    assert stored == (None,)
    row = factcheck(database, FACTCHECK / "draft_null.mdx")[0]
    assert row["team"] == "KC"
    assert row["stat"] == "sack_rate"
    assert row["status"] == "unverifiable"
    assert row["source"] == "no matching view"
    assert row["actual"] is None


def _write_record_db(path: Path, wins: int, losses: int, pushes: int) -> None:
    sample = wins + losses + pushes
    connection = duckdb.connect(str(path))
    try:
        connection.execute(
            f"""
            CREATE VIEW v_record AS
            SELECT
              {wins} AS w,
              {losses} AS l,
              {pushes} AS p,
              'graded.csv' AS source,
              {sample} AS sample,
              '2099' AS "range"
            """
        )
    finally:
        connection.close()


def _stored_record(database: Path) -> dict:
    connection = duckdb.connect(str(database), read_only=True)
    try:
        wins, losses, pushes, source, sample, span = connection.execute(
            'SELECT w, l, p, source, sample, "range" FROM v_record'
        ).fetchone()
    finally:
        connection.close()
    return {
        "actual": f"{int(wins)}-{int(losses)}-{int(pushes)}",
        "source": None if source is None else str(source),
        "sample": None if sample is None else int(sample),
        "range": None if span is None else str(span),
    }


def _draft_records(path: Path, claims: list[str]) -> None:
    lines = [f"The record is {claim}." for claim in claims]
    path.write_text("\n\n".join(lines) + "\n", encoding="utf-8")


def test_record_with_a_push_rejects_wl(tmp_path):
    root, fixtures = prepare_agent_root(tmp_path)
    database = tmp_path / "football.duckdb"
    build_agent_db(root, 2099, database, fixtures=fixtures)
    stored = _stored_record(database)
    assert stored["actual"] == "2-1-1"
    draft = tmp_path / "mismatch.mdx"
    _draft_records(draft, ["2-1", "3-1"])
    found = factcheck(database, draft)
    assert [row["claimed"] for row in found] == ["2-1", "3-1"]
    assert [row["status"] for row in found] == ["corrected", "corrected"]
    for row in found:
        assert row["actual"] == stored["actual"]
        assert row["source"] == stored["source"]
        assert row["sample"] == stored["sample"]
        assert row["range"] == stored["range"]


def test_record_without_pushes_confirms_wl(tmp_path):
    database = tmp_path / "zero.duckdb"
    _write_record_db(database, 2, 1, 0)
    stored = _stored_record(database)
    assert stored["actual"] == "2-1-0"
    draft = tmp_path / "zero.mdx"
    _draft_records(draft, ["2-1", "2-1-0"])
    found = factcheck(database, draft)
    assert [row["claimed"] for row in found] == ["2-1", "2-1-0"]
    assert [row["status"] for row in found] == ["confirmed", "confirmed"]
    for row in found:
        assert row["actual"] == stored["actual"]
        assert row["source"] == stored["source"]
        assert row["sample"] == stored["sample"]
        assert row["range"] == stored["range"]
