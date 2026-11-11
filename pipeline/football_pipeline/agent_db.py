"""Read-only DuckDB for the stats teammate, from public inputs only.

The file is written to a temp path in the same directory, checkpointed,
closed, and renamed onto --out. The finished file is mode 444. Open it
with duckdb.connect(path, read_only=True). Rebuild when is_stale says the
file is missing or older than 24 hours.

nflverse tables come from build_database with include_dump=False, so the
gitignored D1 dump is never loaded. Repo files are the derived lines (via
that loader), data/graded.csv, src/data/summary.json, src/data/picks_view.json,
and src/content/games/*.json. No Odds API, no D1, no data/.cache, no secrets.
FTN charting is not loaded. The credits table still names it.
"""

from __future__ import annotations

import contextlib
import csv
import io
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import duckdb

from football_pipeline.build_db import build_database
from football_pipeline.cards import EN_DASH, NGS_SOURCE, PBP_SOURCE, _span, stat_number
from football_pipeline.schema import PipelineError, format_ts

MAX_AGE = timedelta(hours=24)
GRADED_SOURCE = "graded.csv"
SUMMARY_SOURCE = "summary.json"
INJURY_SOURCE = "nflverse_injuries"
LINES_SOURCE = "median of {n} US books (data/lines)"
PUBLIC_VIEWS = (
    "v_team_epa",
    "v_pass_rate_pace",
    "v_explosives",
    "v_qb",
    "v_pass_rush",
    "v_red_zone_downs",
    "v_injuries",
    "v_lines",
    "v_record",
    "v_cards",
    "v_catalog",
)
CREDIT_LINES = (
    "Data: nflverse (nflfastR / nflreadr) — code MIT, data CC-BY 4.0. "
    "NFL data belongs to its respective owners.",
    "Charting data: FTN Data via nflverse, CC-BY-SA 4.0 (not loaded in v1).",
)


def is_stale(path: Path, now: datetime, max_age: timedelta = MAX_AGE) -> bool:
    """True when the file is missing or strictly older than max_age.

    Age equal to max_age is still fresh. The bot rebuilds only when this
    returns True. `now` must carry a timezone.
    """
    if now.tzinfo is None:
        raise PipelineError("is_stale now must include a timezone")
    if max_age.total_seconds() < 0:
        raise PipelineError("is_stale max_age must not be negative")
    if not path.is_file():
        return True
    modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    return now - modified > max_age


def build_agent_db(root: Path, season: int, out: Path, fixtures: Path | None = None) -> None:
    """Build the agent database at `out` and print row counts only."""
    root = root.resolve()
    out = out.resolve()
    if out.exists() and out.is_dir():
        raise PipelineError(f"agent db path is a directory: {out}")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.parent / f".{out.name}.{os.getpid()}.partial"
    _unlink(tmp)
    _unlink(Path(str(tmp) + ".wal"))
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            build_database(root, season, tmp, fixtures=fixtures, include_dump=False)
        connection = duckdb.connect(str(tmp))
        try:
            _drop_line_bounds(connection)
            inputs = _input_rows(connection, root, fixtures)
            _load_repo(connection, root, season, inputs)
            _create_views(connection)
            _write_meta(connection, root, season, inputs)
            _write_credits(connection)
            _write_catalog(connection)
            connection.execute("CHECKPOINT")
        finally:
            connection.close()
        os.replace(tmp, out)
        os.chmod(out, 0o444)
    finally:
        _unlink(tmp)
        _unlink(Path(str(tmp) + ".wal"))
        _unlink(Path(str(out) + ".wal"))
    counts = _counts(out)
    summary = ", ".join(f"{name}={count}" for name, count in counts)
    print(f"agent-db season {season}: {summary}")


def _unlink(path: Path) -> None:
    if path.is_file():
        path.unlink()


def _run(connection: duckdb.DuckDBPyConnection, sql: str) -> None:
    try:
        connection.execute(sql)
    except duckdb.Error as exc:
        preview = " ".join(sql.split())[:160]
        raise PipelineError(f"agent sql failed ({preview}): {exc}") from exc


def _columns(connection: duckdb.DuckDBPyConnection, table: str) -> set[str]:
    rows = connection.execute(f"DESCRIBE {table}").fetchall()
    return {str(row[0]) for row in rows}


def _drop_line_bounds(connection: duckdb.DuckDBPyConnection) -> None:
    """Keep the median line and the book count. Drop min and max."""
    _run(
        connection,
        """
        CREATE TABLE lines_derived AS
        SELECT snapshot_ts, game_id, market, line, n_books FROM lines
        """,
    )
    _run(connection, "DROP TABLE lines")
    _run(connection, "ALTER TABLE lines_derived RENAME TO lines")


def _input_rows(
    connection: duckdb.DuckDBPyConnection,
    root: Path,
    fixtures: Path | None,
) -> list[dict]:
    inputs: list[dict] = []
    for name in ("schedules", "pbp", "injuries", "ngs_passing"):
        count = connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()
        label = f"fixture:{name}" if fixtures is not None else f"nflverse:{name}"
        inputs.append({"path": label, "rows": int(count[0] if count else 0)})
    line_dir = (fixtures / "lines") if fixtures is not None else (root / "data" / "lines")
    paths = sorted(line_dir.glob("*.csv")) if line_dir.is_dir() else []
    if paths:
        for path in paths:
            inputs.append({"path": _display_path(path, root), "rows": _text_rows(path)})
    else:
        count = connection.execute("SELECT COUNT(*) FROM lines").fetchone()
        inputs.append({"path": "data/lines", "rows": int(count[0] if count else 0)})
    return inputs


def _display_path(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root))
    except ValueError:
        return path.name


def _text_rows(path: Path) -> int:
    if path.suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return len(payload)
        return 1 if payload is not None else 0
    with path.open(encoding="utf-8") as handle:
        count = sum(1 for line in handle if line.strip())
    return max(count - 1, 0)


def _load_repo(
    connection: duckdb.DuckDBPyConnection,
    root: Path,
    season: int,
    inputs: list[dict],
) -> None:
    _load_picks_view(connection, root, inputs)
    _load_summary(connection, root, inputs)
    _load_record(connection, root, season, inputs)
    _load_cards(connection, root, inputs)


def _load_picks_view(connection: duckdb.DuckDBPyConnection, root: Path, inputs: list[dict]) -> None:
    path = root / "src" / "data" / "picks_view.json"
    if path.is_file():
        try:
            connection.execute(
                "CREATE TABLE picks_view AS SELECT * FROM read_json_auto(?)",
                [str(path)],
            )
        except duckdb.Error as exc:
            raise PipelineError(f"could not read {path.name}: {exc}") from exc
        inputs.append({"path": "src/data/picks_view.json", "rows": _text_rows(path)})
        return
    _run(connection, "CREATE TABLE picks_view (pick_id VARCHAR)")
    inputs.append({"path": "src/data/picks_view.json", "rows": 0})


def _load_summary(connection: duckdb.DuckDBPyConnection, root: Path, inputs: list[dict]) -> None:
    path = root / "src" / "data" / "summary.json"
    if path.is_file():
        try:
            connection.execute(
                "CREATE TABLE summary_file AS SELECT * FROM read_json_auto(?)",
                [str(path)],
            )
        except duckdb.Error as exc:
            raise PipelineError(f"could not read {path.name}: {exc}") from exc
        inputs.append({"path": "src/data/summary.json", "rows": 1})
        return
    _run(connection, "CREATE TABLE summary_file (season INTEGER)")
    inputs.append({"path": "src/data/summary.json", "rows": 0})


def _load_record(
    connection: duckdb.DuckDBPyConnection,
    root: Path,
    season: int,
    inputs: list[dict],
) -> None:
    _run(
        connection,
        """
        CREATE TABLE agent_record (
          season INTEGER,
          w INTEGER,
          l INTEGER,
          p INTEGER,
          units DOUBLE,
          avg_clv DOUBLE,
          win_pct DOUBLE,
          pct_clv_pos DOUBLE,
          source VARCHAR,
          sample BIGINT,
          "range" VARCHAR
        )
        """,
    )
    graded_path = root / "data" / "graded.csv"
    summary_path = root / "src" / "data" / "summary.json"
    row: dict | None = None
    if graded_path.is_file():
        inputs.append({"path": "data/graded.csv", "rows": _text_rows(graded_path)})
        row = _record_from_graded(graded_path, season)
    else:
        inputs.append({"path": "data/graded.csv", "rows": 0})
    if row is None and summary_path.is_file():
        row = _record_from_summary(summary_path, season)
    if row is None:
        return
    connection.execute(
        """
        INSERT INTO agent_record VALUES (
          ?, ?, ?, ?,
          CAST(? AS DOUBLE), CAST(? AS DOUBLE), CAST(? AS DOUBLE), CAST(? AS DOUBLE),
          ?, ?, ?
        )
        """,
        [
            row["season"],
            row["w"],
            row["l"],
            row["p"],
            row["units"],
            row["avg_clv"],
            row["win_pct"],
            row["pct_clv_pos"],
            row["source"],
            row["sample"],
            row["range"],
        ],
    )


def _record_from_graded(path: Path, season: int) -> dict | None:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    counted = [row for row in rows if (row.get("status") or "").strip() == "graded"]
    if not counted:
        return None
    wins = sum(1 for row in counted if (row.get("result") or "").strip() == "W")
    losses = sum(1 for row in counted if (row.get("result") or "").strip() == "L")
    pushes = sum(1 for row in counted if (row.get("result") or "").strip() == "P")
    units = sum((_decimal(row.get("units_pl")) for row in counted), start=Decimal(0))
    clvs = [_decimal(row.get("clv_pts")) for row in counted if (row.get("clv_pts") or "").strip()]
    sample = wins + losses + pushes
    win_pct = (Decimal(wins) / Decimal(wins + losses)) if wins + losses else None
    if clvs:
        avg_clv: Decimal | None = sum(clvs, start=Decimal(0)) / Decimal(len(clvs))
        positive = sum(1 for value in clvs if value > 0)
        pct_positive: Decimal | None = Decimal(positive) / Decimal(len(clvs))
    else:
        avg_clv = None
        pct_positive = None
    return {
        "season": season,
        "w": wins,
        "l": losses,
        "p": pushes,
        "units": _decimal_text(units),
        "avg_clv": _decimal_text(avg_clv),
        "win_pct": _decimal_text(win_pct),
        "pct_clv_pos": _decimal_text(pct_positive),
        "source": GRADED_SOURCE,
        "sample": sample,
        "range": str(season),
    }


def _record_from_summary(path: Path, season: int) -> dict | None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("w") is None:
        return None
    wins = int(payload.get("w") or 0)
    losses = int(payload.get("l") or 0)
    pushes = int(payload.get("p") or 0)
    sample = wins + losses + pushes
    if sample == 0:
        return None
    file_season = payload.get("season")
    return {
        "season": int(file_season) if file_season is not None else season,
        "w": wins,
        "l": losses,
        "p": pushes,
        "units": _decimal_text(_maybe_decimal(payload.get("units"))),
        "avg_clv": _decimal_text(_maybe_decimal(payload.get("avg_clv"))),
        "win_pct": _decimal_text(_maybe_decimal(payload.get("win_pct"))),
        "pct_clv_pos": _decimal_text(_maybe_decimal(payload.get("pct_clv_pos"))),
        "source": SUMMARY_SOURCE,
        "sample": sample,
        "range": str(int(file_season) if file_season is not None else season),
    }


def _decimal(value: str | None) -> Decimal:
    text = (value or "").strip()
    if not text:
        return Decimal(0)
    return Decimal(text)


def _maybe_decimal(value) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def _load_cards(connection: duckdb.DuckDBPyConnection, root: Path, inputs: list[dict]) -> None:
    _run(
        connection,
        """
        CREATE TABLE card_stat (
          game_id VARCHAR,
          field VARCHAR,
          value DOUBLE,
          source VARCHAR,
          sample BIGINT,
          "range" VARCHAR
        )
        """,
    )
    directory = root / "src" / "content" / "games"
    paths = sorted(directory.glob("*.json")) if directory.is_dir() else []
    rows: list[tuple] = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise PipelineError(f"{path.name} is not a card object")
        game_id = str(payload.get("game_id") or path.stem)
        for field, cited in _flatten_card(payload):
            rows.append(
                (game_id, field, cited["value"], cited["source"], cited["sample"], cited["range"])
            )
        inputs.append({"path": _display_path(path, root), "rows": 1})
    if not paths:
        inputs.append({"path": "src/content/games", "rows": 0})
    if rows:
        connection.executemany(
            'INSERT INTO card_stat VALUES (?, ?, ?, ?, ?, ?)',
            rows,
        )


def _flatten_card(card: dict) -> list[tuple[str, dict]]:
    found: list[tuple[str, dict]] = []

    def walk(node, path: str) -> None:
        if isinstance(node, dict) and {"value", "source", "sample", "range"} <= set(node):
            source = str(node.get("source") or "")
            if node.get("value") is None or node.get("sample") is None:
                cited = stat_number(None, source, None, None)
            else:
                cited = stat_number(
                    float(node["value"]),
                    source,
                    int(node["sample"]),
                    None if node.get("range") is None else str(node["range"]),
                )
            found.append((path, cited))
            return
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}" if path else str(key))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")

    walk(card, "")
    return found


def _create_views(connection: duckdb.DuckDBPyConnection) -> None:
    sample = _span(2099, [1, 2])
    if sample != f"2099 wk1{EN_DASH}2":
        raise PipelineError("cards._span format changed; agent spans must follow it")
    _create_play(connection)
    _create_games(connection)
    _create_macros(connection)
    _create_stat_views(connection)
    _create_other_views(connection)


def _optional(connection: duckdb.DuckDBPyConnection, table: str, column: str, cast: str) -> str:
    if column in _columns(connection, table):
        return f"TRY_CAST({column} AS {cast})"
    return f"CAST(NULL AS {cast})"


def _create_play(connection: duckdb.DuckDBPyConnection) -> None:
    season_type = _optional(connection, "pbp", "season_type", "VARCHAR")
    _run(
        connection,
        f"""
        CREATE TABLE agent_play AS
        SELECT
          game_id,
          TRY_CAST(season AS INTEGER) AS season,
          TRY_CAST(week AS INTEGER) AS week,
          NULLIF(TRIM({season_type}), '') AS season_type,
          TRY_CAST(play_id AS INTEGER) AS play_id,
          TRY_CAST(qtr AS INTEGER) AS qtr,
          TRY_CAST(half_seconds_remaining AS DOUBLE) AS half_seconds_remaining,
          TRY_CAST(down AS INTEGER) AS down,
          TRY_CAST(wp AS DOUBLE) AS wp,
          NULLIF(TRIM(CAST(posteam AS VARCHAR)), '') AS posteam,
          NULLIF(TRIM(CAST(defteam AS VARCHAR)), '') AS defteam,
          CASE WHEN COALESCE(TRY_CAST("pass" AS INTEGER), 0) <> 0 THEN 1 ELSE 0 END AS pass_play,
          CASE WHEN COALESCE(TRY_CAST(rush AS INTEGER), 0) <> 0 THEN 1 ELSE 0 END AS rush_play,
          TRY_CAST(epa AS DOUBLE) AS epa,
          TRY_CAST(success AS DOUBLE) AS success,
          TRY_CAST(yards_gained AS DOUBLE) AS yards_gained,
          CASE WHEN COALESCE(TRY_CAST(sack AS INTEGER), 0) <> 0 THEN 1 ELSE 0 END AS sack,
          CASE WHEN COALESCE(TRY_CAST(qb_dropback AS INTEGER), 0) <> 0 THEN 1 ELSE 0 END AS qb_dropback,
          NULLIF(TRIM(CAST(passer_player_id AS VARCHAR)), '') AS passer_player_id,
          NULLIF(TRIM(CAST(passer_player_name AS VARCHAR)), '') AS passer_player_name,
          TRY_CAST(cpoe AS DOUBLE) AS cpoe,
          CASE WHEN COALESCE(TRY_CAST(touchdown AS INTEGER), 0) <> 0 THEN 1 ELSE 0 END AS touchdown,
          TRY_CAST(yardline_100 AS DOUBLE) AS yardline_100,
          TRY_CAST(fixed_drive AS INTEGER) AS fixed_drive,
          CASE WHEN COALESCE(TRY_CAST(third_down_converted AS INTEGER), 0) <> 0 THEN 1 ELSE 0 END AS third_down_converted,
          CASE WHEN COALESCE(TRY_CAST(third_down_failed AS INTEGER), 0) <> 0 THEN 1 ELSE 0 END AS third_down_failed,
          CASE WHEN COALESCE(TRY_CAST(fourth_down_converted AS INTEGER), 0) <> 0 THEN 1 ELSE 0 END AS fourth_down_converted,
          CASE WHEN COALESCE(TRY_CAST(fourth_down_failed AS INTEGER), 0) <> 0 THEN 1 ELSE 0 END AS fourth_down_failed
        FROM pbp
        """,
    )
    _run(
        connection,
        """
        CREATE TABLE agent_play_2 AS
        SELECT
          *,
          CASE
            WHEN posteam IS NOT NULL AND (pass_play = 1 OR rush_play = 1) THEN 1
            ELSE 0
          END AS is_scrimmage
        FROM agent_play
        """,
    )
    _run(connection, "DROP TABLE agent_play")
    _run(
        connection,
        f"""
        CREATE TABLE agent_play AS
        SELECT
          *,
          CASE
            WHEN is_scrimmage = 1
             AND down IN (1, 2)
             AND wp IS NOT NULL
             AND wp >= 0.20 AND wp <= 0.80
             AND half_seconds_remaining IS NOT NULL
             AND half_seconds_remaining > 120
            THEN 1 ELSE 0
          END AS is_neutral,
          CASE
            WHEN yards_gained IS NULL THEN 0
            WHEN rush_play = 1 AND pass_play = 0 AND yards_gained >= 10 THEN 1
            WHEN pass_play = 1 AND yards_gained >= 20 THEN 1
            ELSE 0
          END AS is_explosive,
          CASE
            WHEN qtr IS NULL THEN NULL
            WHEN qtr <= 2 THEN 1
            WHEN qtr <= 4 THEN 2
            ELSE qtr
          END AS half
        FROM agent_play_2
        """,
    )
    _run(connection, "DROP TABLE agent_play_2")


def _create_games(connection: duckdb.DuckDBPyConnection) -> None:
    game_type = _optional(connection, "schedules", "game_type", "VARCHAR")
    dash = EN_DASH
    _run(
        connection,
        f"""
        CREATE TABLE agent_played AS
        WITH sched AS (
          SELECT
            TRY_CAST(season AS INTEGER) AS season,
            TRY_CAST(week AS INTEGER) AS week,
            game_id,
            NULLIF(TRIM(CAST(away_team AS VARCHAR)), '') AS away_team,
            NULLIF(TRIM(CAST(home_team AS VARCHAR)), '') AS home_team,
            COALESCE(CAST(gameday AS VARCHAR), '') AS gameday,
            COALESCE(CAST(gametime AS VARCHAR), '') AS gametime,
            TRY_CAST(away_score AS DOUBLE) AS away_score,
            TRY_CAST(home_score AS DOUBLE) AS home_score,
            NULLIF(TRIM({game_type}), '') AS game_type
          FROM schedules
        ),
        kept AS (
          SELECT * FROM sched
          WHERE game_type IS NULL OR UPPER(game_type) <> 'PRE'
        ),
        week_final AS (
          SELECT season, week
          FROM kept
          GROUP BY season, week
          HAVING COUNT(*) > 0
             AND COUNT(away_score) = COUNT(*)
             AND COUNT(home_score) = COUNT(*)
             AND week >= 1
        ),
        thru AS (
          SELECT season, MAX(week) AS through_week
          FROM week_final
          GROUP BY season
        ),
        finals AS (
          SELECT s.season, s.week, s.game_id, s.away_team, s.home_team,
                 s.gameday, s.gametime, t.through_week
          FROM kept s
          JOIN thru t ON s.season = t.season
          WHERE s.week BETWEEN 1 AND t.through_week
            AND s.away_score IS NOT NULL
            AND s.home_score IS NOT NULL
        )
        SELECT season, week, game_id, away_team AS team, gameday, gametime, through_week FROM finals
        UNION ALL
        SELECT season, week, game_id, home_team AS team, gameday, gametime, through_week FROM finals
        """,
    )
    _run(
        connection,
        """
        CREATE TABLE agent_scope_game AS
        WITH ranked AS (
          SELECT
            *,
            ROW_NUMBER() OVER (
              PARTITION BY season, team
              ORDER BY gameday, gametime, game_id
            ) AS ord,
            COUNT(*) OVER (PARTITION BY season, team) AS n_games
          FROM agent_played
        )
        SELECT season, team, 'season' AS scope, game_id, week, through_week FROM ranked
        UNION ALL
        SELECT season, team, 'l4' AS scope, game_id, week, through_week
        FROM ranked
        WHERE ord > n_games - 4
        """,
    )
    _run(
        connection,
        """
        CREATE TABLE agent_scope_play AS
        SELECT
          g.season,
          g.team,
          g.scope,
          p.game_id,
          p.week,
          p.play_id,
          p.qtr,
          p.half_seconds_remaining,
          p.down,
          p.wp,
          p.posteam,
          p.defteam,
          p.pass_play,
          p.rush_play,
          p.epa,
          p.success,
          p.yards_gained,
          p.sack,
          p.qb_dropback,
          p.passer_player_id,
          p.passer_player_name,
          p.cpoe,
          p.touchdown,
          p.yardline_100,
          p.fixed_drive,
          p.third_down_converted,
          p.third_down_failed,
          p.fourth_down_converted,
          p.fourth_down_failed,
          p.is_scrimmage,
          p.is_neutral,
          p.is_explosive,
          p.half,
          p.season_type
        FROM agent_scope_game g
        JOIN agent_play p
          ON p.game_id = g.game_id
         AND p.season = g.season
         AND p.week BETWEEN 1 AND g.through_week
         AND (p.season_type IS NULL OR p.season_type = 'REG')
        """,
    )
    _run(
        connection,
        f"""
        CREATE TABLE agent_scope_span AS
        SELECT
          g.season,
          g.team,
          g.scope,
          CASE
            WHEN MAX(g.through_week) >= 1
            THEN CAST(g.season AS VARCHAR) || ' wk1{dash}' || CAST(MAX(g.through_week) AS VARCHAR)
            ELSE NULL
          END AS season_span,
          CASE
            WHEN COUNT(p.play_id) > 0
            THEN CAST(g.season AS VARCHAR) || ' wk' || CAST(MIN(p.week) AS VARCHAR)
                 || '{dash}' || CAST(MAX(p.week) AS VARCHAR)
            ELSE NULL
          END AS played_span
        FROM agent_scope_game g
        LEFT JOIN agent_scope_play p
          ON p.season = g.season AND p.team = g.team AND p.scope = g.scope AND p.game_id = g.game_id
        GROUP BY g.season, g.team, g.scope
        """,
    )


def _create_macros(connection: duckdb.DuckDBPyConnection) -> None:
    _run(
        connection,
        """
        CREATE MACRO agent_value(raw, sample_n, span) AS
          CASE
            WHEN sample_n > 0 AND span IS NOT NULL THEN CAST(raw AS DOUBLE)
            ELSE NULL
          END
        """,
    )
    _run(
        connection,
        """
        CREATE MACRO agent_sample(sample_n, span) AS
          CASE
            WHEN sample_n > 0 AND span IS NOT NULL THEN CAST(sample_n AS BIGINT)
            ELSE NULL
          END
        """,
    )
    _run(
        connection,
        """
        CREATE MACRO agent_range(sample_n, span) AS
          CASE
            WHEN sample_n > 0 AND span IS NOT NULL THEN span
            ELSE NULL
          END
        """,
    )


def _create_stat_views(connection: duckdb.DuckDBPyConnection) -> None:
    _run(
        connection,
        """
        CREATE TABLE agent_metric AS
        SELECT season, team, scope, 'for' AS side, 'epa_per_play' AS metric,
               AVG(epa) AS raw, COUNT(*) AS sample_n
        FROM agent_scope_play
        WHERE is_scrimmage = 1 AND epa IS NOT NULL AND posteam = team
        GROUP BY season, team, scope
        UNION ALL
        SELECT season, team, scope, 'allowed', 'epa_per_play', AVG(epa), COUNT(*)
        FROM agent_scope_play
        WHERE is_scrimmage = 1 AND epa IS NOT NULL AND defteam = team
        GROUP BY season, team, scope
        UNION ALL
        SELECT season, team, scope, 'for', 'success_rate', AVG(success), COUNT(*)
        FROM agent_scope_play
        WHERE is_scrimmage = 1 AND success IS NOT NULL AND posteam = team
        GROUP BY season, team, scope
        UNION ALL
        SELECT season, team, scope, 'allowed', 'success_rate', AVG(success), COUNT(*)
        FROM agent_scope_play
        WHERE is_scrimmage = 1 AND success IS NOT NULL AND defteam = team
        GROUP BY season, team, scope
        UNION ALL
        SELECT season, team, scope, 'for', 'neutral_ed_pass_rate',
               AVG(CASE WHEN pass_play = 1 THEN 1.0 ELSE 0.0 END), COUNT(*)
        FROM agent_scope_play
        WHERE posteam = team AND is_neutral = 1
        GROUP BY season, team, scope
        UNION ALL
        SELECT season, team, scope, 'for', 'explosive_rate',
               AVG(CASE WHEN is_explosive = 1 THEN 1.0 ELSE 0.0 END), COUNT(*)
        FROM agent_scope_play
        WHERE is_scrimmage = 1 AND yards_gained IS NOT NULL AND posteam = team
        GROUP BY season, team, scope
        UNION ALL
        SELECT season, team, scope, 'allowed', 'explosive_rate',
               AVG(CASE WHEN is_explosive = 1 THEN 1.0 ELSE 0.0 END), COUNT(*)
        FROM agent_scope_play
        WHERE is_scrimmage = 1 AND yards_gained IS NOT NULL AND defteam = team
        GROUP BY season, team, scope
        UNION ALL
        SELECT season, team, scope, 'for', 'sack_rate',
               AVG(CASE WHEN sack = 1 THEN 1.0 ELSE 0.0 END), COUNT(*)
        FROM agent_scope_play
        WHERE qb_dropback = 1 AND defteam = team
        GROUP BY season, team, scope
        UNION ALL
        SELECT season, team, scope, 'allowed', 'sack_rate',
               AVG(CASE WHEN sack = 1 THEN 1.0 ELSE 0.0 END), COUNT(*)
        FROM agent_scope_play
        WHERE qb_dropback = 1 AND posteam = team
        GROUP BY season, team, scope
        """,
    )
    _pace(connection)
    _downs(connection)
    _qb(connection)
    _finish_metric_views(connection)


def _pace(connection: duckdb.DuckDBPyConnection) -> None:
    _run(
        connection,
        """
        INSERT INTO agent_metric
        WITH ordered AS (
          SELECT
            season, team, scope, game_id, play_id, half, half_seconds_remaining, is_neutral,
            LAG(half_seconds_remaining) OVER (
              PARTITION BY season, team, scope, game_id ORDER BY play_id
            ) AS prev_hsr,
            LAG(half) OVER (
              PARTITION BY season, team, scope, game_id ORDER BY play_id
            ) AS prev_half,
            LAG(is_neutral) OVER (
              PARTITION BY season, team, scope, game_id ORDER BY play_id
            ) AS prev_neutral
          FROM agent_scope_play
          WHERE posteam = team AND is_scrimmage = 1
        ),
        gaps AS (
          SELECT season, team, scope, prev_hsr - half_seconds_remaining AS gap
          FROM ordered
          WHERE is_neutral = 1
            AND prev_neutral = 1
            AND half IS NOT NULL
            AND half = prev_half
            AND prev_hsr IS NOT NULL
            AND half_seconds_remaining IS NOT NULL
            AND prev_hsr - half_seconds_remaining > 0
        )
        SELECT season, team, scope, 'for' AS side, 'pace_sec_per_play' AS metric,
               AVG(gap) AS raw, COUNT(*) AS sample_n
        FROM gaps
        GROUP BY season, team, scope
        """,
    )


def _downs(connection: duckdb.DuckDBPyConnection) -> None:
    _run(
        connection,
        """
        INSERT INTO agent_metric
        WITH flags AS (
          SELECT season, team, scope, 'for' AS side,
                 third_down_converted AS third_made,
                 CASE WHEN third_down_converted = 0 AND third_down_failed = 1 THEN 1 ELSE 0 END AS third_miss,
                 fourth_down_converted AS fourth_made,
                 CASE WHEN fourth_down_converted = 0 AND fourth_down_failed = 1 THEN 1 ELSE 0 END AS fourth_miss
          FROM agent_scope_play
          WHERE posteam = team
          UNION ALL
          SELECT season, team, scope, 'allowed',
                 third_down_converted,
                 CASE WHEN third_down_converted = 0 AND third_down_failed = 1 THEN 1 ELSE 0 END,
                 fourth_down_converted,
                 CASE WHEN fourth_down_converted = 0 AND fourth_down_failed = 1 THEN 1 ELSE 0 END
          FROM agent_scope_play
          WHERE defteam = team
        )
        SELECT season, team, scope, side, 'third_down_rate',
               SUM(third_made)::DOUBLE / NULLIF(SUM(third_made) + SUM(third_miss), 0),
               SUM(third_made) + SUM(third_miss)
        FROM flags
        GROUP BY season, team, scope, side
        HAVING SUM(third_made) + SUM(third_miss) > 0
        UNION ALL
        SELECT season, team, scope, side, 'fourth_down_rate',
               SUM(fourth_made)::DOUBLE / NULLIF(SUM(fourth_made) + SUM(fourth_miss), 0),
               SUM(fourth_made) + SUM(fourth_miss)
        FROM flags
        GROUP BY season, team, scope, side
        HAVING SUM(fourth_made) + SUM(fourth_miss) > 0
        """,
    )
    _run(
        connection,
        """
        INSERT INTO agent_metric
        WITH drives AS (
          SELECT season, team, scope, 'for' AS side, game_id, posteam, fixed_drive,
                 MAX(CASE WHEN yardline_100 > 0 AND yardline_100 <= 20 THEN 1 ELSE 0 END) AS in_rz,
                 MAX(CASE
                       WHEN yardline_100 > 0 AND yardline_100 <= 20 AND touchdown = 1 THEN 1
                       ELSE 0
                     END) AS rz_td
          FROM agent_scope_play
          WHERE fixed_drive IS NOT NULL AND posteam IS NOT NULL AND posteam = team
          GROUP BY season, team, scope, game_id, posteam, fixed_drive
          UNION ALL
          SELECT season, team, scope, 'allowed', game_id, posteam, fixed_drive,
                 MAX(CASE WHEN yardline_100 > 0 AND yardline_100 <= 20 THEN 1 ELSE 0 END),
                 MAX(CASE
                       WHEN yardline_100 > 0 AND yardline_100 <= 20 AND touchdown = 1 THEN 1
                       ELSE 0
                     END)
          FROM agent_scope_play
          WHERE fixed_drive IS NOT NULL AND posteam IS NOT NULL AND defteam = team
          GROUP BY season, team, scope, game_id, posteam, fixed_drive
        )
        SELECT season, team, scope, side, 'rz_td_rate',
               SUM(CASE WHEN in_rz = 1 THEN rz_td ELSE 0 END)::DOUBLE / NULLIF(SUM(in_rz), 0),
               SUM(in_rz)
        FROM drives
        GROUP BY season, team, scope, side
        HAVING SUM(in_rz) > 0
        """,
    )


def _qb(connection: duckdb.DuckDBPyConnection) -> None:
    ngs_type = _optional(connection, "ngs_passing", "season_type", "VARCHAR")
    _run(
        connection,
        f"""
        CREATE TABLE agent_ngs AS
        SELECT
          TRY_CAST(season AS INTEGER) AS season,
          TRY_CAST(week AS INTEGER) AS week,
          NULLIF(TRIM({ngs_type}), '') AS season_type,
          NULLIF(TRIM(CAST(team_abbr AS VARCHAR)), '') AS team,
          NULLIF(TRIM(CAST(player_gsis_id AS VARCHAR)), '') AS player_id,
          NULLIF(TRIM(CAST(player_display_name AS VARCHAR)), '') AS display_name,
          NULLIF(TRIM(CAST(player_short_name AS VARCHAR)), '') AS short_name,
          TRY_CAST(avg_time_to_throw AS DOUBLE) AS avg_time_to_throw,
          TRY_CAST(attempts AS INTEGER) AS attempts
        FROM ngs_passing
        """,
    )
    _run(
        connection,
        """
        CREATE TABLE agent_qb AS
        WITH latest AS (
          SELECT season, team, game_id
          FROM (
            SELECT season, team, game_id,
                   ROW_NUMBER() OVER (
                     PARTITION BY season, team
                     ORDER BY gameday DESC, gametime DESC, game_id DESC
                   ) AS rn
            FROM agent_played
          )
          WHERE rn = 1
        ),
        counts AS (
          SELECT l.season, l.team, p.passer_player_id AS player_id, COUNT(*) AS n
          FROM latest l
          JOIN agent_play p
            ON p.game_id = l.game_id
           AND p.posteam = l.team
           AND p.qb_dropback = 1
           AND p.passer_player_id IS NOT NULL
           AND (p.season_type IS NULL OR p.season_type = 'REG')
          GROUP BY l.season, l.team, p.passer_player_id
        ),
        leader AS (
          SELECT season, team, player_id
          FROM counts
          QUALIFY ROW_NUMBER() OVER (
            PARTITION BY season, team ORDER BY n DESC, player_id ASC
          ) = 1
        ),
        names AS (
          SELECT l.season, l.team, l.player_id, p.passer_player_name AS player_name, COUNT(*) AS n
          FROM leader l
          JOIN latest g ON g.season = l.season AND g.team = l.team
          JOIN agent_play p
            ON p.game_id = g.game_id
           AND p.posteam = l.team
           AND p.passer_player_id = l.player_id
           AND p.qb_dropback = 1
           AND p.passer_player_name IS NOT NULL
          GROUP BY l.season, l.team, l.player_id, p.passer_player_name
        ),
        name_pick AS (
          SELECT season, team, player_id, player_name
          FROM names
          QUALIFY ROW_NUMBER() OVER (
            PARTITION BY season, team ORDER BY n DESC, player_name ASC
          ) = 1
        ),
        ngs_name AS (
          SELECT l.season, l.team, n.display_name
          FROM leader l
          JOIN agent_ngs n
            ON n.player_id = l.player_id AND n.team = l.team AND n.display_name IS NOT NULL
          QUALIFY ROW_NUMBER() OVER (PARTITION BY l.season, l.team ORDER BY n.week) = 1
        )
        SELECT
          l.season,
          l.team,
          l.player_id,
          COALESCE(np.player_name, nn.display_name, l.player_id) AS player
        FROM leader l
        LEFT JOIN name_pick np USING (season, team, player_id)
        LEFT JOIN ngs_name nn USING (season, team)
        """,
    )
    _run(
        connection,
        """
        INSERT INTO agent_metric
        SELECT g.season, g.team, g.scope, 'for', 'epa_per_dropback', AVG(p.epa), COUNT(*)
        FROM agent_scope_game g
        JOIN agent_qb q ON q.season = g.season AND q.team = g.team
        JOIN agent_play p
          ON p.game_id = g.game_id
         AND p.season = g.season
         AND p.week BETWEEN 1 AND g.through_week
         AND (p.season_type IS NULL OR p.season_type = 'REG')
         AND p.posteam = g.team
         AND p.qb_dropback = 1
         AND p.passer_player_id = q.player_id
         AND p.epa IS NOT NULL
        GROUP BY g.season, g.team, g.scope
        UNION ALL
        SELECT g.season, g.team, g.scope, 'for', 'cpoe', AVG(p.cpoe), COUNT(*)
        FROM agent_scope_game g
        JOIN agent_qb q ON q.season = g.season AND q.team = g.team
        JOIN agent_play p
          ON p.game_id = g.game_id
         AND p.season = g.season
         AND p.week BETWEEN 1 AND g.through_week
         AND (p.season_type IS NULL OR p.season_type = 'REG')
         AND p.posteam = g.team
         AND p.qb_dropback = 1
         AND p.passer_player_id = q.player_id
         AND p.cpoe IS NOT NULL
        GROUP BY g.season, g.team, g.scope
        """,
    )
    _run(
        connection,
        """
        INSERT INTO agent_metric
        WITH season_weeks AS (
          SELECT p.season, p.team, 'season' AS scope, s.week::INTEGER AS week
          FROM (SELECT DISTINCT season, team, through_week FROM agent_played) p,
          LATERAL generate_series(1, p.through_week) AS s(week)
        ),
        l4_weeks AS (
          SELECT DISTINCT season, team, 'l4' AS scope, week FROM agent_scope_game WHERE scope = 'l4'
        ),
        weeks AS (
          SELECT * FROM season_weeks
          UNION ALL
          SELECT * FROM l4_weeks
        ),
        candidates AS (
          SELECT w.season, w.team, w.scope, n.avg_time_to_throw, n.attempts,
                 CASE WHEN n.player_id = q.player_id THEN 1 ELSE 0 END AS id_match
          FROM weeks w
          JOIN agent_qb q ON q.season = w.season AND q.team = w.team
          JOIN agent_ngs n
            ON n.season = w.season
           AND n.team = w.team
           AND n.week = w.week
           AND (n.season_type IS NULL OR n.season_type = 'REG')
           AND (
                n.player_id = q.player_id
                OR (q.player IS NOT NULL AND n.short_name = q.player)
                OR (q.player IS NOT NULL AND n.display_name = q.player)
           )
        ),
        chosen AS (
          SELECT * FROM candidates c
          WHERE c.id_match = 1
             OR NOT EXISTS (
               SELECT 1 FROM candidates c2
               WHERE c2.season = c.season AND c2.team = c.team AND c2.scope = c.scope
                 AND c2.id_match = 1
             )
        )
        SELECT season, team, scope, 'for', 'time_to_throw',
               SUM(avg_time_to_throw * attempts) / SUM(attempts),
               SUM(attempts)
        FROM chosen
        WHERE attempts IS NOT NULL AND attempts > 0 AND avg_time_to_throw IS NOT NULL
        GROUP BY season, team, scope
        """,
    )


def _finish_metric_views(connection: duckdb.DuckDBPyConnection) -> None:
    span = """
      CASE WHEN d.scope = 'season' THEN s.season_span ELSE s.played_span END
    """
    _run(
        connection,
        f"""
        CREATE VIEW v_team_epa AS
        WITH dims AS (
          SELECT DISTINCT g.season, g.team, g.scope, side.side, metric.metric
          FROM agent_scope_game g
          CROSS JOIN (SELECT 'for' AS side UNION ALL SELECT 'allowed') side
          CROSS JOIN (
            SELECT 'epa_per_play' AS metric
            UNION ALL SELECT 'success_rate'
          ) metric
        )
        SELECT
          d.season, d.team, d.scope, d.side, d.metric,
          agent_value(a.raw, a.sample_n, {span}) AS value,
          '{PBP_SOURCE}' AS source,
          agent_sample(a.sample_n, {span}) AS sample,
          agent_range(a.sample_n, {span}) AS "range"
        FROM dims d
        LEFT JOIN agent_metric a
          ON a.season = d.season AND a.team = d.team AND a.scope = d.scope
         AND a.side = d.side AND a.metric = d.metric
        LEFT JOIN agent_scope_span s
          ON s.season = d.season AND s.team = d.team AND s.scope = d.scope
        """,
    )
    _run(
        connection,
        f"""
        CREATE VIEW v_pass_rate_pace AS
        WITH dims AS (
          SELECT DISTINCT g.season, g.team, g.scope, metric.metric
          FROM agent_scope_game g
          CROSS JOIN (
            SELECT 'neutral_ed_pass_rate' AS metric
            UNION ALL SELECT 'pace_sec_per_play'
          ) metric
        )
        SELECT
          d.season, d.team, d.scope, d.metric,
          agent_value(a.raw, a.sample_n, {span}) AS value,
          '{PBP_SOURCE}' AS source,
          agent_sample(a.sample_n, {span}) AS sample,
          agent_range(a.sample_n, {span}) AS "range"
        FROM dims d
        LEFT JOIN agent_metric a
          ON a.season = d.season AND a.team = d.team AND a.scope = d.scope
         AND a.side = 'for' AND a.metric = d.metric
        LEFT JOIN agent_scope_span s
          ON s.season = d.season AND s.team = d.team AND s.scope = d.scope
        """,
    )
    _run(
        connection,
        f"""
        CREATE VIEW v_explosives AS
        WITH dims AS (
          SELECT DISTINCT g.season, g.team, g.scope, side.side
          FROM agent_scope_game g
          CROSS JOIN (SELECT 'for' AS side UNION ALL SELECT 'allowed') side
        )
        SELECT
          d.season, d.team, d.scope, d.side, 'explosive_rate' AS metric,
          agent_value(a.raw, a.sample_n, {span}) AS value,
          '{PBP_SOURCE}' AS source,
          agent_sample(a.sample_n, {span}) AS sample,
          agent_range(a.sample_n, {span}) AS "range"
        FROM dims d
        LEFT JOIN agent_metric a
          ON a.season = d.season AND a.team = d.team AND a.scope = d.scope
         AND a.side = d.side AND a.metric = 'explosive_rate'
        LEFT JOIN agent_scope_span s
          ON s.season = d.season AND s.team = d.team AND s.scope = d.scope
        """,
    )
    _run(
        connection,
        f"""
        CREATE VIEW v_pass_rush AS
        WITH dims AS (
          SELECT DISTINCT g.season, g.team, g.scope, side.side
          FROM agent_scope_game g
          CROSS JOIN (SELECT 'for' AS side UNION ALL SELECT 'allowed') side
        )
        SELECT
          d.season, d.team, d.scope, d.side, 'sack_rate' AS metric,
          agent_value(a.raw, a.sample_n, {span}) AS value,
          '{PBP_SOURCE}' AS source,
          agent_sample(a.sample_n, {span}) AS sample,
          agent_range(a.sample_n, {span}) AS "range"
        FROM dims d
        LEFT JOIN agent_metric a
          ON a.season = d.season AND a.team = d.team AND a.scope = d.scope
         AND a.side = d.side AND a.metric = 'sack_rate'
        LEFT JOIN agent_scope_span s
          ON s.season = d.season AND s.team = d.team AND s.scope = d.scope
        """,
    )
    _run(
        connection,
        f"""
        CREATE VIEW v_red_zone_downs AS
        WITH dims AS (
          SELECT DISTINCT g.season, g.team, g.scope, side.side, metric.metric
          FROM agent_scope_game g
          CROSS JOIN (SELECT 'for' AS side UNION ALL SELECT 'allowed') side
          CROSS JOIN (
            SELECT 'rz_td_rate' AS metric
            UNION ALL SELECT 'third_down_rate'
            UNION ALL SELECT 'fourth_down_rate'
          ) metric
        )
        SELECT
          d.season, d.team, d.scope, d.side, d.metric,
          agent_value(a.raw, a.sample_n, {span}) AS value,
          '{PBP_SOURCE}' AS source,
          agent_sample(a.sample_n, {span}) AS sample,
          agent_range(a.sample_n, {span}) AS "range"
        FROM dims d
        LEFT JOIN agent_metric a
          ON a.season = d.season AND a.team = d.team AND a.scope = d.scope
         AND a.side = d.side AND a.metric = d.metric
        LEFT JOIN agent_scope_span s
          ON s.season = d.season AND s.team = d.team AND s.scope = d.scope
        """,
    )
    _run(
        connection,
        f"""
        CREATE VIEW v_qb AS
        WITH dims AS (
          SELECT DISTINCT g.season, g.team, g.scope, metric.metric
          FROM agent_scope_game g
          CROSS JOIN (
            SELECT 'epa_per_dropback' AS metric
            UNION ALL SELECT 'cpoe'
            UNION ALL SELECT 'time_to_throw'
          ) metric
        )
        SELECT
          d.season,
          d.team,
          d.scope,
          q.player,
          d.metric,
          agent_value(a.raw, a.sample_n, {span}) AS value,
          CASE
            WHEN d.metric = 'time_to_throw' THEN '{NGS_SOURCE}'
            ELSE '{PBP_SOURCE}'
          END AS source,
          agent_sample(a.sample_n, {span}) AS sample,
          agent_range(a.sample_n, {span}) AS "range"
        FROM dims d
        LEFT JOIN agent_qb q ON q.season = d.season AND q.team = d.team
        LEFT JOIN agent_metric a
          ON a.season = d.season AND a.team = d.team AND a.scope = d.scope
         AND a.side = 'for' AND a.metric = d.metric
        LEFT JOIN agent_scope_span s
          ON s.season = d.season AND s.team = d.team AND s.scope = d.scope
        """,
    )


def _create_other_views(connection: duckdb.DuckDBPyConnection) -> None:
    dash = EN_DASH
    line_prefix, line_suffix = LINES_SOURCE.split("{n}")
    injury_type = _optional(connection, "injuries", "season_type", "VARCHAR")
    _run(
        connection,
        f"""
        CREATE VIEW v_injuries AS
        WITH clean AS (
          SELECT
            TRY_CAST(season AS INTEGER) AS season,
            TRY_CAST(week AS INTEGER) AS week,
            NULLIF(TRIM(CAST(team AS VARCHAR)), '') AS team,
            NULLIF(TRIM(CAST(full_name AS VARCHAR)), '') AS player,
            NULLIF(TRIM(CAST(position AS VARCHAR)), '') AS position,
            NULLIF(TRIM(CAST(report_status AS VARCHAR)), '') AS status,
            NULLIF(TRIM(CAST(report_primary_injury AS VARCHAR)), '') AS detail,
            NULLIF(TRIM({injury_type}), '') AS season_type
          FROM injuries
        ),
        kept AS (
          SELECT * FROM clean
          WHERE player IS NOT NULL AND position IS NOT NULL AND status IS NOT NULL
            AND team IS NOT NULL AND season IS NOT NULL AND week IS NOT NULL
            AND (season_type IS NULL OR season_type = 'REG')
        ),
        latest AS (
          SELECT season, MAX(week) AS week FROM kept GROUP BY season
        )
        SELECT
          k.season,
          k.week,
          k.team,
          k.player,
          k.position,
          k.status,
          k.detail,
          '{INJURY_SOURCE}' AS source,
          COUNT(*) OVER (PARTITION BY k.season, k.team) AS sample,
          CAST(k.season AS VARCHAR) || ' wk' || CAST(k.week AS VARCHAR)
            || '{dash}' || CAST(k.week AS VARCHAR) AS "range"
        FROM kept k
        JOIN latest l ON k.season = l.season AND k.week = l.week
        """,
    )
    _run(
        connection,
        f"""
        CREATE VIEW v_lines AS
        SELECT
          game_id,
          snapshot_ts,
          CASE
            WHEN lower(CAST(market AS VARCHAR)) IN ('spread', 'spreads') THEN 'spread'
            WHEN lower(CAST(market AS VARCHAR)) IN ('total', 'totals') THEN 'total'
            ELSE lower(CAST(market AS VARCHAR))
          END AS market,
          line,
          CAST(n_books AS INTEGER) AS n_books,
          '{line_prefix}' || CAST(CAST(n_books AS INTEGER) AS VARCHAR)
            || '{line_suffix}' AS source,
          CAST(n_books AS BIGINT) AS sample,
          snapshot_ts AS "range"
        FROM lines
        WHERE line IS NOT NULL
          AND n_books IS NOT NULL
          AND snapshot_ts IS NOT NULL
          AND game_id IS NOT NULL
        """,
    )
    _run(connection, "CREATE VIEW v_record AS SELECT * FROM agent_record")
    _run(connection, "CREATE VIEW v_cards AS SELECT * FROM card_stat")


def _write_meta(
    connection: duckdb.DuckDBPyConnection,
    root: Path,
    season: int,
    inputs: list[dict],
) -> None:
    _run(
        connection,
        """
        CREATE TABLE meta (
          built_at VARCHAR,
          season INTEGER,
          repo_sha VARCHAR,
          inputs VARCHAR
        )
        """,
    )
    ordered = sorted(inputs, key=lambda item: item["path"])
    connection.execute(
        "INSERT INTO meta VALUES (?, ?, ?, ?)",
        [format_ts(datetime.now(timezone.utc)), season, _repo_sha(root), json.dumps(ordered)],
    )


def _repo_sha(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    if len(sha) < 7 or any(char not in "0123456789abcdef" for char in sha.lower()):
        return None
    return sha


def _write_credits(connection: duckdb.DuckDBPyConnection) -> None:
    _run(connection, "CREATE TABLE credits (line VARCHAR)")
    connection.executemany("INSERT INTO credits VALUES (?)", [(line,) for line in CREDIT_LINES])


def _write_catalog(connection: duckdb.DuckDBPyConnection) -> None:
    _run(
        connection,
        """
        CREATE TABLE agent_catalog (
          view_name VARCHAR,
          description VARCHAR,
          columns VARCHAR,
          definition_note VARCHAR
        )
        """,
    )
    rows = [
        (
            "v_team_epa",
            "Offensive and defensive EPA per play and success rate.",
            "season, team, scope, side, metric, value, source, sample, range",
            "Scrimmage plays only (posteam set, pass or rush). "
            "side for is offense; side allowed is defense, where lower is better. "
            "metric is epa_per_play or success_rate. "
            "scope season uses weeks 1 through the last fully final week. "
            "scope l4 is the last 4 games the team played, ordered by kickoff then game_id. "
            f"source {PBP_SOURCE}. A null value has a null sample and range.",
        ),
        (
            "v_pass_rate_pace",
            "Neutral early-down pass rate and seconds between offensive snaps.",
            "season, team, scope, metric, value, source, sample, range",
            "Neutral means scrimmage, down 1 or 2, win probability 0.20 to 0.80, "
            "and half_seconds_remaining greater than 120. "
            "neutral_ed_pass_rate is passes over those plays. "
            "pace_sec_per_play is the mean positive gap between consecutive neutral "
            "offensive snaps in the same half. Rates are fractions from 0 to 1. "
            f"Pace is seconds. source {PBP_SOURCE}.",
        ),
        (
            "v_explosives",
            "Explosive play rate for and allowed.",
            "season, team, scope, side, metric, value, source, sample, range",
            "Explosive is a rush of 10 or more yards, or a pass of 20 or more, on a scrimmage play. "
            "The rate is explosives over scrimmage plays with a recorded gain. "
            f"metric is explosive_rate. source {PBP_SOURCE}.",
        ),
        (
            "v_qb",
            "Starter EPA per dropback, CPOE, and time to throw.",
            "season, team, scope, player, metric, value, source, sample, range",
            "The starter is the dropback leader in the team's most recent played game. "
            "epa_per_dropback and cpoe cover that player's dropbacks in the scope. "
            "cpoe is in percentage points. time_to_throw is seconds, weighted by NGS attempts, "
            f"source {NGS_SOURCE}. The other two metrics use {PBP_SOURCE}. "
            "A missing starter leaves player and the numbers null.",
        ),
        (
            "v_pass_rush",
            "Sack rate for the defense and sack rate allowed by the offense.",
            "season, team, scope, side, metric, value, source, sample, range",
            "sack_rate for is defense sacks per opponent dropback. "
            "sack_rate allowed is sacks taken per own dropback. "
            f"This is the pass-rush versus offensive-line comparison. source {PBP_SOURCE}.",
        ),
        (
            "v_red_zone_downs",
            "Red-zone touchdown rate and third-down and fourth-down conversion rate.",
            "season, team, scope, side, metric, value, source, sample, range",
            "rz_td_rate is red-zone trips (a drive with a play inside the 20) that score a touchdown, "
            "over trips. third_down_rate and fourth_down_rate are conversions over attempts. "
            f"side for is the offense. source {PBP_SOURCE}.",
        ),
        (
            "v_injuries",
            "Latest injury-report rows for the season.",
            "season, week, team, player, position, status, detail, source, sample, range",
            f"The latest week present on the report. source {INJURY_SOURCE}. "
            "sample is the number of listed players for that team. "
            "detail is the reported body part, or null.",
        ),
        (
            "v_lines",
            "Derived median line for each stored snapshot.",
            "game_id, snapshot_ts, market, line, n_books, source, sample, range",
            "market is spread or total. line is the median already stored in data/lines. "
            "source is median of N US books (data/lines). sample is n_books. "
            "range is the snapshot timestamp. Minimum, maximum, and sportsbook names are not in this view.",
        ),
        (
            "v_record",
            "Graded W-L-P, result, and closing-line value.",
            "season, w, l, p, units, avg_clv, win_pct, pct_clv_pos, source, sample, range",
            "Counted rows are status graded in data/graded.csv. "
            "Pushes count in the record and in CLV, and not in win percentage. "
            "When graded.csv has no graded row, the view uses summary.json and says so in source. "
            "sample is W+L+P. range is the season. This is a result, not a recommendation.",
        ),
        (
            "v_cards",
            "Flattened StatNumber leaves from committed game cards.",
            "game_id, field, value, source, sample, range",
            "field is the JSON path, such as sections.epa.home.off_epa_season. "
            "A null value has a null sample and range. Cards are the public JSON, not a new calculation.",
        ),
        (
            "v_catalog",
            "View names, columns, and the definition each number uses.",
            "view_name, description, columns, definition_note",
            "Read this before choosing a view. Every numeric answer needs source, sample, and range.",
        ),
    ]
    connection.executemany("INSERT INTO agent_catalog VALUES (?, ?, ?, ?)", rows)
    _run(connection, "CREATE VIEW v_catalog AS SELECT * FROM agent_catalog")


def _counts(path: Path) -> list[tuple[str, int]]:
    connection = duckdb.connect(str(path), read_only=True)
    try:
        names = [row[0] for row in connection.execute("SHOW TABLES").fetchall()]
        counts: list[tuple[str, int]] = []
        for name in sorted(names):
            if not str(name).replace("_", "").isalnum():
                continue
            count = connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()
            counts.append((str(name), int(count[0] if count else 0)))
    finally:
        connection.close()
    return counts
