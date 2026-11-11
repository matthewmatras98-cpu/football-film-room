"""Fact-check helper: confirmed, corrected, and unverifiable claims."""

from __future__ import annotations

import json

from football_pipeline.agent_db import build_agent_db
from football_pipeline.cards import EN_DASH
from football_pipeline.factcheck import factcheck
from tests.agent_support import AGENT, prepare_agent_root
from tests.conftest import run_cli

SPAN = f"2099 wk1{EN_DASH}2"


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
