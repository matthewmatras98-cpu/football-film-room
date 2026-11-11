"""Pull stat claims out of an MDX draft and check them against the agent DB.

Frontmatter, fenced code, import lines, and JSX lines are skipped. A claim
is a team abbreviation plus a known stat phrase plus a number, or a W-L
record. Percentages are compared both as written and as fractions. ± matches
either sign. Display rounding is 6 decimal places.

status is confirmed, corrected, or unverifiable. corrected carries the
database value. Anything unmapped or missing is unverifiable and still has
a source, "no matching view".
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import duckdb

from football_pipeline.agent_eval import NumberToken, extract_numbers
from football_pipeline.cards import json_number
from football_pipeline.schema import PipelineError
from football_pipeline.teams import ABBR_TO_ODDS

_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_RECORD_WORDS = re.compile(r"(?i)\b(?:record|w-l)\b")
_RECORD_VALUE = re.compile(r"\b(\d+)-(\d+)(?:-(\d+))?\b")
_SCOPE_RE = re.compile(r"(?i)\b(?:last\s*4|last\s+four|l4)\b")
_ALLOWED_RE = re.compile(r"(?i)\b(?:allowed|allowing|against)\b")
_TEAM_RE = re.compile(r"\b(" + "|".join(sorted(ABBR_TO_ODDS, key=len, reverse=True)) + r")\b")
# Longest phrases first so "neutral pass rate" wins over a shorter fragment.
PHRASES: tuple[tuple[str, str, str, bool], ...] = (
    ("time to throw", "v_qb", "time_to_throw", False),
    ("neutral pass rate", "v_pass_rate_pace", "neutral_ed_pass_rate", False),
    ("epa per play", "v_team_epa", "epa_per_play", True),
    ("success rate", "v_team_epa", "success_rate", True),
    ("sack rate", "v_pass_rush", "sack_rate", True),
    ("explosive", "v_explosives", "explosive_rate", True),
    ("red zone", "v_red_zone_downs", "rz_td_rate", True),
    ("third down", "v_red_zone_downs", "third_down_rate", True),
    ("cpoe", "v_qb", "cpoe", False),
)
NO_VIEW = "no matching view"


def factcheck(database: Path, mdx: Path) -> list[dict]:
    if not database.is_file():
        raise PipelineError(f"duckdb not found: {database}")
    if not mdx.is_file():
        raise PipelineError(f"mdx not found: {mdx}")
    lines = body_lines(mdx.read_text(encoding="utf-8"))
    connection = duckdb.connect(str(database), read_only=True)
    try:
        return [_check_line(connection, line) for line in lines if _check_line_keep(line)]
    finally:
        connection.close()


def body_lines(text: str) -> list[str]:
    """MDX body with frontmatter, fences, imports, and JSX removed."""
    body = text.lstrip("\ufeff")
    if body.startswith("---"):
        end = body.find("\n---", 3)
        if end != -1:
            body = body[end + 4 :]
    body = _FENCE_RE.sub("", body)
    kept: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("import ") or line.startswith("export "):
            continue
        if re.match(r"^</?[A-Za-z]", line):
            continue
        kept.append(line)
    return kept


def _check_line_keep(line: str) -> bool:
    if _RECORD_WORDS.search(line) and _RECORD_VALUE.search(line):
        return True
    if _phrase(line) is not None and (_TEAM_RE.search(line) or extract_numbers(line)):
        return True
    if _TEAM_RE.search(line) and extract_numbers(line):
        return True
    return False


def _check_line(connection: duckdb.DuckDBPyConnection, line: str) -> dict:
    if _RECORD_WORDS.search(line) and _RECORD_VALUE.search(line):
        return _check_record(connection, line)
    team_match = _TEAM_RE.search(line)
    team = team_match.group(1) if team_match else None
    phrase = _phrase(line)
    numbers = extract_numbers(line)
    number = numbers[0] if numbers else None
    if phrase is None or number is None:
        return _unverifiable(
            line,
            team,
            None,
            None if number is None else _claimed_number(number),
            "no mapped stat phrase" if phrase is None else "no number",
        )
    label, view, metric, sided = phrase
    del label
    scope = "l4" if _SCOPE_RE.search(line) else "season"
    side = "allowed" if sided and _ALLOWED_RE.search(line) else "for"
    row = _query(connection, view, team, scope, side if sided else None, metric)
    if row is None:
        return _unverifiable(line, team, metric, _claimed_number(number), "no row for this team")
    actual, source, sample, span = row
    shown = _shown(actual)
    if _close_enough(number, actual):
        status = "confirmed"
    else:
        status = "corrected"
    return {
        "text": line,
        "team": team,
        "stat": metric,
        "claimed": _claimed_number(number),
        "status": status,
        "actual": shown,
        "source": source,
        "sample": None if sample is None else int(sample),
        "range": None if span is None else str(span),
        "reason": None,
    }


def _check_record(connection: duckdb.DuckDBPyConnection, line: str) -> dict:
    match = _RECORD_VALUE.search(line)
    claimed = match.group(0) if match else None
    try:
        rows = connection.execute(
            'SELECT w, l, p, source, sample, "range" FROM v_record'
        ).fetchall()
    except duckdb.Error as exc:
        raise PipelineError(f"v_record query failed: {exc}") from exc
    if len(rows) != 1:
        return _unverifiable(line, None, "record", claimed, "no row for this team")
    wins, losses, pushes, source, sample, span = rows[0]
    actual = f"{int(wins)}-{int(losses)}-{int(pushes)}"
    return {
        "text": line,
        "team": None,
        "stat": "record",
        "claimed": claimed,
        "status": "confirmed" if claimed == actual else "corrected",
        "actual": actual,
        "source": None if source is None else str(source),
        "sample": None if sample is None else int(sample),
        "range": None if span is None else str(span),
        "reason": None,
    }


def _phrase(line: str) -> tuple[str, str, str, bool] | None:
    lowered = line.lower()
    for label, view, metric, sided in PHRASES:
        if label in lowered:
            return label, view, metric, sided
    return None


def _query(connection, view: str, team: str | None, scope: str, side: str | None, metric: str):
    if team is None:
        return None
    if side is None:
        sql = (
            f'SELECT value, source, sample, "range" FROM {view} '
            "WHERE team = ? AND scope = ? AND metric = ?"
        )
        params = [team, scope, metric]
    else:
        sql = (
            f'SELECT value, source, sample, "range" FROM {view} '
            "WHERE team = ? AND scope = ? AND side = ? AND metric = ?"
        )
        params = [team, scope, side, metric]
    try:
        rows = connection.execute(sql, params).fetchall()
    except duckdb.Error as exc:
        raise PipelineError(f"{view} query failed: {exc}") from exc
    if len(rows) != 1:
        return None
    return rows[0]


def _close_enough(token: NumberToken, actual) -> bool:
    if actual is None:
        return False
    options = [token.value, -token.value] if token.plusminus else [token.value]
    if token.percent:
        options = [*options, *(value / 100.0 for value in options)]
    try:
        target = round(float(actual), 6)
    except (TypeError, ValueError):
        return False
    return any(round(option, 6) == target for option in options)


def _claimed_number(token: NumberToken):
    value = token.value
    rounded = round(abs(value) if token.plusminus else value, 6)
    if rounded == int(rounded):
        return int(rounded) if not token.percent else rounded
    return rounded


def _shown(value):
    if value is None:
        return None
    return json_number(float(value))


def _unverifiable(line: str, team: str | None, stat: str | None, claimed, reason: str) -> dict:
    return {
        "text": line,
        "team": team,
        "stat": stat,
        "claimed": claimed,
        "status": "unverifiable",
        "actual": None,
        "source": NO_VIEW,
        "sample": None,
        "range": None,
        "reason": reason,
    }


def dump_factcheck(rows: list[dict]) -> str:
    return json.dumps(rows, indent=2, ensure_ascii=False) + "\n"
