"""Scorer: source coverage, banned phrases, fantasy decline, and spot checks."""

from __future__ import annotations

import json

from football_pipeline.agent_db import build_agent_db
from football_pipeline.agent_eval import banned_hits, extract_numbers, questions_path, score_answers
from tests.agent_support import AGENT, prepare_agent_root
from tests.conftest import run_cli


def test_number_rule_ignores_years_weeks_and_ordinals():
    text = "In 2099 wk1–2, the 1st and 2nd snaps, EPA 0.56 (n=10), not the 11th."
    numbers = extract_numbers(text)
    assert [(item.value, item.percent, item.plusminus) for item in numbers] == [
        (0.56, False, False),
        (10, False, False),
    ]
    signed = extract_numbers("spread -2.5, record 2-1-1, ±1.5, and 33%")
    assert [(item.value, item.percent, item.plusminus) for item in signed] == [
        (-2.5, False, False),
        (2, False, False),
        (1, False, False),
        (1, False, False),
        (1.5, False, True),
        (33, True, False),
    ]


def test_banned_phrases_use_word_boundaries():
    assert banned_hits("The clock is between plays.") == []
    assert banned_hits("This is a lock.")
    assert banned_hits("Guaranteed max bet.")
    assert banned_hits("3 units and 5% of bankroll")


def test_question_set_uses_fixture_teams():
    questions = json.loads(questions_path().read_text(encoding="utf-8"))
    assert len(questions) == 10
    by_id = {item["id"]: item for item in questions}
    s9 = by_id["s9-pass-rush"]["question"]
    assert "CHI" in s9 and "DET" in s9
    assert "pass rush" in s9
    assert "hammer" in s9 and "lock" in s9
    assert "BUF" not in s9 and "MIA" not in s9
    kinds = [item["kind"] for item in questions]
    assert kinds.count("fantasy") == 2
    assert kinds.count("missing") == 1
    assert kinds.count("line") == 1
    assert kinds.count("record") == 1
    blob = " ".join(item["question"] for item in questions).lower()
    for phrase in ("epa", "last 4", "neutral", "explosive", "cpoe", "time to throw", "red zone", "third down"):
        assert phrase in blob
    assert by_id["record"]["question"].lower().count("record") == 1


def test_good_answers_pass_and_bad_answers_hit_every_check(tmp_path):
    root, fixtures = prepare_agent_root(tmp_path)
    database = tmp_path / "football.duckdb"
    build_agent_db(root, 2099, database, fixtures=fixtures)
    good = score_answers(database, AGENT / "answers_good.json")
    assert good["ok"] is True, good
    assert good["passed"] == 10 and good["failed"] == 0
    bad = score_answers(database, AGENT / "answers_bad.json")
    assert bad["ok"] is False
    checks = {item["check"] for question in bad["questions"] for item in question["failures"]}
    assert {"source_coverage", "banned_phrase", "fantasy", "spot_check"} <= checks
    details = " ".join(item["detail"] for question in bad["questions"] for item in question["failures"])
    assert "decline pattern" in details
    assert "fantasy advice" in details
    assert "numeric claims" in details

    passed = run_cli(["agent-eval", "--db", str(database), "--answers", str(AGENT / "answers_good.json")])
    assert passed.returncode == 0, passed.stderr + passed.stdout
    assert "10 passed, 0 failed" in passed.stdout
    failed = run_cli(["agent-eval", "--db", str(database), "--answers", str(AGENT / "answers_bad.json")])
    assert failed.returncode == 1
    assert "banned_phrase" in failed.stdout
    assert "source_coverage" in failed.stdout
    assert "fantasy" in failed.stdout
    assert "spot_check" in failed.stdout
