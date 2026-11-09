"""Derived lines stay free of book names and prices."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from football_pipeline.close import BookLine, Game
from football_pipeline.lines_export import LINES_COLUMNS, book_lines_from_snapshots, export_lines
from football_pipeline.schema import format_ts
from football_pipeline.snapshot import SnapshotRow
from tests.conftest import FIXTURES, run_cli

UTC = timezone.utc


def test_lower_middle_and_week_selection(tmp_path: Path):
    graded = _game("2099_01_GB_CHI", datetime(2099, 9, 6, 17, 0, tzinfo=UTC), week=1, scores=(24, 21))
    also_final = _game("2099_03_KC_BUF", datetime(2099, 9, 20, 17, 0, tzinfo=UTC), week=3, home="BUF", away="KC", scores=(21, 17))
    open_week = _game("2099_04_DET_PHI", datetime(2099, 9, 27, 17, 0, tzinfo=UTC), week=4, home="PHI", away="DET")
    far = _game("2099_08_SF_DAL", datetime(2099, 10, 25, 17, 0, tzinfo=UTC), week=8, home="DAL", away="SF")
    games = [graded, also_final, open_week, far]
    as_of = datetime(2099, 9, 22, 0, 0, tzinfo=UTC)
    books = [
        *_books(graded, [Decimal("-3"), Decimal("-2.5"), Decimal("-2.5"), Decimal("-2")]),
        *_books(also_final, [Decimal("-7"), Decimal("-3"), Decimal("-3"), Decimal("-2.5"), Decimal("-2")]),
        *_books(open_week, [Decimal("3"), Decimal("3.5")]),
        *_books(far, [Decimal("-10"), Decimal("-10"), Decimal("-10")]),
    ]
    written = export_lines(tmp_path, games, books, as_of)
    names = sorted(path.name for path in written)
    assert names == ["2099_w03.csv", "2099_w04.csv"]
    assert not (tmp_path / "data" / "lines" / "2099_w01.csv").exists()
    assert not (tmp_path / "data" / "lines" / "2099_w08.csv").exists()

    week3 = (tmp_path / "data" / "lines" / "2099_w03.csv").read_text(encoding="utf-8")
    assert week3.splitlines()[0] == ",".join(LINES_COLUMNS)
    assert "book_" not in week3
    assert "-110" not in week3
    # floor((5 - 1) / 2) = 2 of [-7, -3, -3, -2.5, -2] is -3.
    assert f"{format_ts(also_final.kickoff_utc - timedelta(minutes=30))},2099_03_KC_BUF,spread,-3,5,-7,-2" in week3

    week4 = (tmp_path / "data" / "lines" / "2099_w04.csv").read_text(encoding="utf-8")
    # Two books: lower of 3 and 3.5.
    assert ",2099_04_DET_PHI,spread,3,2,3,3.5" in week4


def test_close_table_keeps_home_spread_and_the_over(tmp_path: Path):
    game = _game("2099_01_GB_CHI", datetime(2099, 9, 13, 17, 0, tzinfo=UTC), week=1, scores=(24, 21))
    stamp = game.kickoff_utc - timedelta(minutes=30)
    rows = [
        _snap(game, "spread", "CHI", Decimal("-3.5"), stamp),
        _snap(game, "spread", "GB", Decimal("3.5"), stamp),
        _snap(game, "total", "over", Decimal("47.5"), stamp),
        _snap(game, "total", "under", Decimal("47.5"), stamp),
    ]
    books = book_lines_from_snapshots(rows, [game])
    assert [(row.market, row.point) for row in books] == [
        ("spread", Decimal("-3.5")),
        ("total", Decimal("47.5")),
    ]
    path = tmp_path / "close.csv"
    from football_pipeline.lines_export import write_close_file

    write_close_file(path, books)
    text = path.read_text(encoding="utf-8")
    assert text.splitlines()[0] == "game_id,market,book,point,snapshot_ts"
    assert ",spread,draftkings,-3.5," in text
    assert ",total,draftkings,47.5," in text
    assert "draftkings,3.5," not in text
    assert text.count("draftkings") == 2


def test_lines_export_cli(tmp_path: Path):
    root = tmp_path / "repo"
    (root / "data").mkdir(parents=True)
    snap = root / "data" / "closes.csv"
    snap.write_text(
        "game_id,market,book,point,snapshot_ts\n"
        "2099_01_GB_CHI,spread,secretbook,-3,2099-09-13T16:30:00Z\n"
        "2099_01_GB_CHI,spread,otherbook,-2.5,2099-09-13T16:30:00Z\n",
        encoding="utf-8",
    )
    result = run_cli(
        [
            "lines-export",
            "--root",
            str(root),
            "--schedules",
            str(FIXTURES / "schedules.csv"),
            "--snapshots",
            str(snap),
            "--as-of",
            "2099-09-20T00:00:00Z",
        ]
    )
    assert result.returncode == 0, result.stderr + result.stdout
    text = (root / "data" / "lines" / "2099_w01.csv").read_text(encoding="utf-8")
    assert "secretbook" not in text
    assert "otherbook" not in text
    # Sorted [-3, -2.5], floor((2 - 1) / 2) = 0, so -3.
    assert "2099_01_GB_CHI,spread,-3,2,-3,-2.5" in text


def _books(game: Game, points: list[Decimal]) -> list[BookLine]:
    stamp = game.kickoff_utc - timedelta(minutes=30)
    return [
        BookLine(game.game_id, "spread", f"book_{index}", point, stamp)
        for index, point in enumerate(points)
    ]


def _snap(game: Game, market: str, side: str, point: Decimal, stamp: datetime) -> SnapshotRow:
    return SnapshotRow(
        snapshot_ts=stamp,
        game_id=game.game_id,
        event_id="evt",
        book="draftkings",
        market=market,
        side=side,
        point=point,
        price=-110,
        last_update=stamp - timedelta(hours=1),
        shot_kind="A",
    )


def _game(game_id, kickoff, week, home="CHI", away="GB", scores=None) -> Game:
    return Game(
        game_id=game_id,
        season=2099,
        week=week,
        away_team=away,
        home_team=home,
        kickoff_utc=kickoff,
        away_score=None if scores is None else scores[0],
        home_score=None if scores is None else scores[1],
        spread_line=Decimal("3"),
        total_line=Decimal("45"),
    )
