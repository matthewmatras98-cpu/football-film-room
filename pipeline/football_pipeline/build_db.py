"""Build the run-time DuckDB used by the card generator.

Live loads call nflreadpy 0.1.5 with the cache off, then the same release
parquet nflreadpy requests:

- pbp/play_by_play_{season}.parquet via load_pbp
- schedules/games.parquet via load_schedules, filtered to the season
- injuries/injuries_{season}.parquet via load_injuries
- nextgen_stats/ngs_passing.parquet via load_nextgen_stats(..., "passing")

`lines` is every data/lines/*.csv. `snapshots_dump` is the gitignored
week.d1dump.csv when that file is present. It is not written back out.
`include_dump` defaults to True so build-db and weekly keep that load.
The agent database passes False and never reads the dump.
Tests pass --fixtures and never call the network.
"""

from __future__ import annotations

import io
from pathlib import Path

import duckdb
import polars as pl

from football_pipeline.nflv import load_schedule_frame
from football_pipeline.schema import PipelineError

RELEASE_BASE = "https://github.com/nflverse/nflverse-data/releases/download"
PBP_URL = RELEASE_BASE + "/pbp/play_by_play_{season}.parquet"
INJURIES_URL = RELEASE_BASE + "/injuries/injuries_{season}.parquet"
NGS_PASSING_URL = RELEASE_BASE + "/nextgen_stats/ngs_passing.parquet"

LINES_COLUMNS: tuple[str, ...] = (
    "snapshot_ts",
    "game_id",
    "market",
    "line",
    "n_books",
    "min",
    "max",
)


def build_database(
    root: Path,
    season: int,
    out: Path,
    fixtures: Path | None = None,
    *,
    include_dump: bool = True,
) -> None:
    if season < 1999:
        raise PipelineError(f"season must be 1999 or later, got {season}")
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    connection = duckdb.connect(str(out))
    try:
        if fixtures is not None:
            _load_fixtures(connection, fixtures, season)
            dump_path = fixtures / "week.d1dump.csv"
            line_paths = sorted((fixtures / "lines").glob("*.csv")) if (fixtures / "lines").is_dir() else []
        else:
            _load_live(connection, season)
            dump_path = root / "data" / ".cache" / "week.d1dump.csv"
            line_dir = root / "data" / "lines"
            line_paths = sorted(line_dir.glob("*.csv")) if line_dir.is_dir() else []
        _load_lines(connection, line_paths)
        if include_dump and dump_path.is_file():
            _load_dump(connection, dump_path)
        counts = _counts(connection)
    finally:
        connection.close()
    summary = ", ".join(f"{name}={count}" for name, count in counts)
    print(f"build-db season {season}: {summary}")


def _load_fixtures(connection: duckdb.DuckDBPyConnection, fixtures: Path, season: int) -> None:
    if not fixtures.is_dir():
        raise PipelineError(f"fixtures directory not found: {fixtures}")
    for name in ("schedules", "pbp", "injuries", "ngs_passing"):
        frame = _read_fixture(fixtures, name)
        frame = _filter_season(frame, season)
        _store_frame(connection, name, frame)


def _load_live(connection: duckdb.DuckDBPyConnection, season: int) -> None:
    schedules, _source = load_schedule_frame()
    schedules = _filter_season(schedules, season)
    _store_frame(connection, "schedules", schedules)
    _store_frame(connection, "pbp", _filter_season(_load_pbp(season), season))
    _store_frame(connection, "injuries", _filter_season(_load_injuries(season), season))
    _store_frame(connection, "ngs_passing", _filter_season(_load_ngs_passing(season), season))


def _load_pbp(season: int) -> pl.DataFrame:
    def via_package() -> pl.DataFrame:
        import nflreadpy
        from nflreadpy.config import update_config

        update_config(cache_mode="off", cache_duration=0)
        if hasattr(nflreadpy, "clear_cache"):
            nflreadpy.clear_cache()
        return nflreadpy.load_pbp([season])

    return _with_fallback("pbp", via_package, PBP_URL.format(season=season))


def _load_injuries(season: int) -> pl.DataFrame:
    def via_package() -> pl.DataFrame:
        import nflreadpy
        from nflreadpy.config import update_config

        update_config(cache_mode="off", cache_duration=0)
        if hasattr(nflreadpy, "clear_cache"):
            nflreadpy.clear_cache()
        return nflreadpy.load_injuries([season])

    return _with_fallback("injuries", via_package, INJURIES_URL.format(season=season))


def _load_ngs_passing(season: int) -> pl.DataFrame:
    def via_package() -> pl.DataFrame:
        import nflreadpy
        from nflreadpy.config import update_config

        update_config(cache_mode="off", cache_duration=0)
        if hasattr(nflreadpy, "clear_cache"):
            nflreadpy.clear_cache()
        return nflreadpy.load_nextgen_stats([season], stat_type="passing")

    frame = _with_fallback("ngs_passing", via_package, NGS_PASSING_URL)
    return _filter_season(frame, season)


def _with_fallback(label: str, loader, url: str) -> pl.DataFrame:
    try:
        return loader()
    except Exception as first_error:
        try:
            return _download_parquet(url)
        except Exception as second_error:
            raise PipelineError(
                f"nflreadpy {label} failed ({first_error}); parquet fallback failed ({second_error})"
            ) from second_error


def _download_parquet(url: str) -> pl.DataFrame:
    import urllib.request

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "football-pipeline",
            "Accept": "application/octet-stream",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = response.read()
    return pl.read_parquet(io.BytesIO(payload))


def _read_fixture(directory: Path, stem: str) -> pl.DataFrame:
    parquet = directory / f"{stem}.parquet"
    csv_path = directory / f"{stem}.csv"
    if parquet.is_file():
        return pl.read_parquet(parquet)
    if csv_path.is_file():
        return pl.read_csv(csv_path, null_values=["", "NA", "NULL"])
    raise PipelineError(f"missing fixture {stem}.csv or {stem}.parquet")


def _filter_season(frame: pl.DataFrame, season: int) -> pl.DataFrame:
    if "season" not in frame.columns:
        return frame
    return frame.filter(pl.col("season") == season)


def _store_frame(connection: duckdb.DuckDBPyConnection, name: str, frame: pl.DataFrame) -> None:
    if not name.isidentifier():
        raise PipelineError(f"refusing table name {name}")
    # DuckDB reads the parquet Polars just wrote. pyarrow is not installed.
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / f"{name}.parquet"
        frame.write_parquet(path)
        connection.execute(f"CREATE TABLE {name} AS SELECT * FROM read_parquet(?)", [str(path)])


def _load_lines(connection: duckdb.DuckDBPyConnection, paths: list[Path]) -> None:
    frames: list[pl.DataFrame] = []
    schema = {
        "snapshot_ts": pl.Utf8,
        "game_id": pl.Utf8,
        "market": pl.Utf8,
        "line": pl.Float64,
        "n_books": pl.Int64,
        "min": pl.Float64,
        "max": pl.Float64,
    }
    for path in paths:
        frame = pl.read_csv(path, schema=schema, null_values=["", "NA", "NULL"])
        missing = [column for column in LINES_COLUMNS if column not in frame.columns]
        if missing:
            raise PipelineError(f"{path.name} is missing columns: {', '.join(missing)}")
        frames.append(frame.select(list(LINES_COLUMNS)))
    if frames:
        combined = pl.concat(frames, how="vertical")
    else:
        combined = pl.DataFrame(schema=schema)
    _store_frame(connection, "lines", combined)


def _load_dump(connection: duckdb.DuckDBPyConnection, path: Path) -> None:
    connection.execute(
        "CREATE TABLE snapshots_dump AS SELECT * FROM read_csv(?, header=true, all_varchar=true)",
        [str(path)],
    )


def _counts(connection: duckdb.DuckDBPyConnection) -> list[tuple[str, int]]:
    names = [row[0] for row in connection.execute("SHOW TABLES").fetchall()]
    counts: list[tuple[str, int]] = []
    for name in sorted(names):
        count = connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()
        counts.append((name, int(count[0] if count else 0)))
    return counts
