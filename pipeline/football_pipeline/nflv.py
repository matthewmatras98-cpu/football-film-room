"""Load nflverse schedules and turn gameday + gametime into kickoff_utc.

Every call re-reads the source. nflreadpy's cache is turned off first. If
that load raises, the same release file is read from
schedules/games.parquet. kickoff_utc is gameday + gametime in
America/New_York, converted to UTC.
"""

from __future__ import annotations

import io
import tempfile
import urllib.request
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import polars as pl

from football_pipeline.close import Game
from football_pipeline.schema import PipelineError

GAMES_PARQUET_URL = (
    "https://github.com/nflverse/nflverse-data/releases/download/schedules/games.parquet"
)

EASTERN = ZoneInfo("America/New_York")

REQUIRED_COLUMNS: tuple[str, ...] = (
    "game_id",
    "season",
    "week",
    "gameday",
    "gametime",
    "away_team",
    "home_team",
    "away_score",
    "home_score",
    "spread_line",
    "total_line",
)

_CSV_SCHEMA: dict[str, pl.DataType] = {
    "game_id": pl.Utf8,
    "season": pl.Int64,
    "week": pl.Int64,
    "gameday": pl.Utf8,
    "gametime": pl.Utf8,
    "away_team": pl.Utf8,
    "home_team": pl.Utf8,
    "away_score": pl.Int64,
    "home_score": pl.Int64,
    "spread_line": pl.Float64,
    "total_line": pl.Float64,
}


def load_games(
    *,
    fixture: Path | None = None,
    seasons: list[int] | None = None,
    downloader=None,
) -> tuple[list[Game], str]:
    """Return games and a short source label. Always reads; never a stale cache."""
    frame, source = load_schedule_frame(fixture=fixture, downloader=downloader)
    missing = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        raise PipelineError(f"schedules are missing columns: {', '.join(missing)}")
    if seasons:
        frame = frame.filter(pl.col("season").is_in(seasons))
    return games_from_frame(frame), source


def load_schedule_frame(*, fixture: Path | None = None, downloader=None) -> tuple[pl.DataFrame, str]:
    if fixture is not None:
        return read_schedule_file(fixture), f"fixture:{fixture.name}"
    try:
        frame = _load_via_nflreadpy()
        return frame, "nflreadpy"
    except Exception as first_error:
        try:
            frame = _download_games_parquet(downloader or _urlopen_bytes)
        except Exception as second_error:
            raise PipelineError(
                f"nflreadpy failed ({first_error}); parquet fallback failed ({second_error})"
            ) from second_error
        return frame, "nflverse-data:schedules/games.parquet"


def read_schedule_file(path: Path) -> pl.DataFrame:
    if not path.is_file():
        raise PipelineError(f"schedules fixture not found: {path}")
    if path.suffix == ".parquet":
        return pl.read_parquet(path)
    if path.suffix == ".csv":
        return pl.read_csv(
            path,
            schema_overrides=_CSV_SCHEMA,
            null_values=["", "NA", "NULL"],
        )
    raise PipelineError(f"unsupported schedules file: {path}")


def games_from_frame(frame: pl.DataFrame) -> list[Game]:
    """Project schedule columns with DuckDB, then attach kickoff_utc.

    DuckDB reads the parquet Polars just wrote. That keeps the query in SQL
    without pulling pyarrow in to register the frame.
    """
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "schedules.parquet"
        frame.select(list(REQUIRED_COLUMNS)).write_parquet(path)
        connection = duckdb.connect(database=":memory:")
        try:
            rows = connection.execute(
                """
                SELECT
                  CAST(game_id AS VARCHAR) AS game_id,
                  CAST(season AS INTEGER) AS season,
                  CAST(week AS INTEGER) AS week,
                  CAST(away_team AS VARCHAR) AS away_team,
                  CAST(home_team AS VARCHAR) AS home_team,
                  CAST(gameday AS VARCHAR) AS gameday,
                  CAST(gametime AS VARCHAR) AS gametime,
                  TRY_CAST(away_score AS INTEGER) AS away_score,
                  TRY_CAST(home_score AS INTEGER) AS home_score,
                  TRY_CAST(spread_line AS DOUBLE) AS spread_line,
                  TRY_CAST(total_line AS DOUBLE) AS total_line
                FROM read_parquet(?)
                ORDER BY game_id
                """,
                [str(path)],
            ).fetchall()
        finally:
            connection.close()

    games: list[Game] = []
    seen: set[str] = set()
    for row in rows:
        (
            game_id,
            season,
            week,
            away_team,
            home_team,
            gameday,
            gametime,
            away_score,
            home_score,
            spread_line,
            total_line,
        ) = row
        if game_id in seen:
            raise PipelineError(f"duplicate game_id in schedules: {game_id}")
        seen.add(game_id)
        kickoff = kickoff_utc_from_parts(gameday, gametime)
        games.append(
            Game(
                game_id=str(game_id),
                season=int(season),
                week=int(week),
                away_team=str(away_team),
                home_team=str(home_team),
                kickoff_utc=kickoff,
                away_score=_as_int(away_score),
                home_score=_as_int(home_score),
                spread_line=_as_decimal(spread_line),
                total_line=_as_decimal(total_line),
            )
        )
    return games


def kickoff_utc_from_parts(gameday: str | None, gametime: str | None) -> datetime | None:
    """Interpret gameday + gametime as America/New_York and convert to UTC.

    A missing kickoff is returned as None. Callers that need a lock time fail
    instead of guessing.
    """
    day = _parse_gameday(gameday)
    clock = _parse_gametime(gametime)
    if day is None or clock is None:
        return None
    local = datetime.combine(day, clock, tzinfo=EASTERN)
    return local.astimezone(timezone.utc)


def write_cache(frame: pl.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(path)


def _load_via_nflreadpy() -> pl.DataFrame:
    import nflreadpy
    from nflreadpy.config import update_config

    # Memory cache would reuse the previous call in this process. Off forces
    # a fresh read on every run. cache_duration 0 is the same instruction.
    update_config(cache_mode="off", cache_duration=0)
    if hasattr(nflreadpy, "clear_cache"):
        nflreadpy.clear_cache()
    return nflreadpy.load_schedules(True)


def _download_games_parquet(downloader) -> pl.DataFrame:
    payload = downloader(GAMES_PARQUET_URL)
    return pl.read_parquet(io.BytesIO(payload))


def _urlopen_bytes(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "football-pipeline",
            "Accept": "application/octet-stream",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def _parse_gameday(value: str | None) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "nat", "null"}:
        return None
    return date.fromisoformat(text[:10])


def _parse_gametime(value: str | None) -> time | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "nat", "null"}:
        return None
    parts = text.split(":")
    if len(parts) < 2:
        return None
    hour = int(parts[0])
    minute = int(parts[1])
    second = int(float(parts[2])) if len(parts) > 2 else 0
    return time(hour, minute, second)


def _as_int(value) -> int | None:
    if value is None:
        return None
    return int(value)


def _as_decimal(value) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))
