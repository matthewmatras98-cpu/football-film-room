"""Hand-calculated season-2099 fixture. Every summary field is asserted."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from football_pipeline.lines import cover_result, home_spread_from_nflverse, picked_side_line
from football_pipeline.nflv import GAMES_PARQUET_URL, load_games, read_schedule_file
from football_pipeline.schema import format_ts
from tests.conftest import FIXTURES, grade_args, prepare_repo, run_cli

EARLY = "2099-09-13T12:00:00Z"
LATE = "2099-09-13T17:03:00Z"
SNAPSHOT_TS = "2099-09-13T16:30:00Z"

# pick_id -> graded fields. Derived by hand from the fixture, not from the grader.
EXPECTED = {
    "2099_01_BAL_PIT-spread": ["3.00", "nflverse", "", "", "", "13", "10", "", "", "", "rejected_late"],
    "2099_01_CIN_CLE-spread": ["2.50", "nflverse", "", "", "", "17", "21", "", "", "", "rejected_late"],
    "2099_01_DET_PHI-spread": ["3.00", "nflverse", "", "", "", "20", "27", "W", "0.909", "-0.50", "graded"],
    "2099_01_DET_PHI-total": ["47.50", "nflverse", "", "", "", "20", "27", "L", "-1.000", "1.00", "graded"],
    "2099_01_GB_CHI-spread": ["2.50", "odds_api", SNAPSHOT_TS, "4", "30", "24", "21", "L", "-1.000", "1.00", "graded"],
    "2099_01_HOU_IND-spread": ["-3.00", "odds_api", SNAPSHOT_TS, "3", "30", "23", "20", "P", "0.000", "0.50", "graded"],
    "2099_01_KC_BUF-spread": ["-3.00", "nflverse", "", "", "", "21", "24", "P", "0.000", "0.50", "graded"],
    "2099_01_KC_BUF-total": ["46.50", "nflverse", "", "", "", "21", "24", "W", "0.909", "0.50", "graded"],
    "2099_01_LAR_SEA-spread": ["-3.50", "odds_api_thin", SNAPSHOT_TS, "2", "30", "23", "20", "L", "-1.000", "1.00", "graded"],
    "2099_01_LV_DEN-spread": ["", "", "", "", "", "27", "14", "", "", "", "ungraded"],
    "2099_01_MIA_NYJ-spread": ["1.50", "nflverse", "", "", "", "20", "23", "L", "-1.000", "1.00", "graded"],
    "2099_01_SF_DAL-spread": ["-6.50", "nflverse", "", "", "", "31", "17", "W", "0.909", "-0.50", "graded"],
    "2099_02_NE_MIA-spread": ["", "", "", "", "", "", "", "", "", "", "pending"],
}

SIDES = {
    "2099_01_BAL_PIT-spread": ("BAL", "-3.0", EARLY),
    "2099_01_CIN_CLE-spread": ("CIN", "-2.5", LATE),
    "2099_01_DET_PHI-spread": ("DET", "2.5", EARLY),
    "2099_01_DET_PHI-total": ("over", "46.5", EARLY),
    "2099_01_GB_CHI-spread": ("GB", "3.5", EARLY),
    "2099_01_HOU_IND-spread": ("IND", "-2.5", EARLY),
    "2099_01_KC_BUF-spread": ("KC", "-2.5", EARLY),
    "2099_01_KC_BUF-total": ("under", "47.0", EARLY),
    "2099_01_LAR_SEA-spread": ("SEA", "-2.5", EARLY),
    "2099_01_LV_DEN-spread": ("LV", "3.0", EARLY),
    "2099_01_MIA_NYJ-spread": ("NYJ", "2.5", EARLY),
    "2099_01_SF_DAL-spread": ("DAL", "-7.0", EARLY),
    "2099_02_NE_MIA-spread": ("NE", "3.0", EARLY),
}


def test_fixture_grades_and_summary(tmp_path: Path):
    root = prepare_repo(tmp_path)
    result = run_cli(grade_args(root, "2099-09-20T00:00:00Z"))
    assert result.returncode == 0, result.stderr + result.stdout

    graded_path = root / "data" / "graded.csv"
    raw = graded_path.read_bytes()
    assert b"\r" not in raw
    assert raw.endswith(b"\n")
    text = raw.decode("utf-8")
    assert "2099-09-20" not in text
    header, *lines = [line for line in text.splitlines() if line]
    assert header == (
        "pick_id,closing_line,close_source,close_ts,close_books,close_age_min,"
        "final_home,final_away,result,units_pl,clv_pts,status"
    )
    assert lines == [
        ",".join([pick_id, *EXPECTED[pick_id]]) for pick_id in sorted(EXPECTED)
    ]

    summary_text = (root / "src" / "data" / "summary.json").read_text(encoding="utf-8")
    assert '"win_pct": 0.429' in summary_text
    assert '"units": -1.273' in summary_text
    assert '"avg_clv": 0.500' in summary_text
    assert '"pct_clv_pos": 0.778' in summary_text
    summary = json.loads(summary_text)
    _assert_sorted_keys(summary)
    assert summary == {
        "avg_clv": 0.5,
        "generated_at": "2099-09-20T00:00:00Z",
        "l": 4,
        "p": 2,
        "pct_clv_pos": 0.778,
        "rejected_count": 2,
        "season": 2099,
        "ungraded_count": 1,
        "units": -1.273,
        "void_count": 0,
        "w": 3,
        "win_pct": 0.429,
    }
    assert _summary_from_rows() == {
        "w": 3,
        "l": 4,
        "p": 2,
        "win_pct": Decimal("0.429"),
        "units": Decimal("-1.273"),
        "avg_clv": Decimal("0.500"),
        "pct_clv_pos": Decimal("0.778"),
        "rejected_count": 2,
        "ungraded_count": 1,
    }

    view = json.loads((root / "src" / "data" / "picks_view.json").read_text(encoding="utf-8"))
    assert [row["pick_id"] for row in view] == sorted(EXPECTED)
    for row in view:
        _assert_sorted_keys(row)
        pick_id = row["pick_id"]
        fields = EXPECTED[pick_id]
        side, line_at_pick, pushed = SIDES[pick_id]
        assert row["side"] == side
        assert Decimal(str(row["line_at_pick"])) == Decimal(line_at_pick)
        assert row["status"] == fields[-1]
        assert row["first_pushed_at"] == pushed
        assert (row["result"] or "") == fields[7]
        if fields[0] == "":
            assert row["closing_line"] is None
        else:
            assert Decimal(str(row["closing_line"])) == Decimal(fields[0])
        assert (row["close_source"] or "") == fields[1]
        assert row["close_books"] == (int(fields[3]) if fields[3] else None)
        assert row["close_age_min"] == (int(fields[4]) if fields[4] else None)
        if fields[8] == "":
            assert row["units_pl"] is None
            assert row["clv_pts"] is None
        else:
            assert f"{Decimal(str(row['units_pl'])):.3f}" == fields[8]
            assert f"{Decimal(str(row['clv_pts'])):.2f}" == fields[9]

    # The nflverse line on GB would push. The snapshot close is what grades the loss.
    gb_home = home_spread_from_nflverse(Decimal("3.0"))
    gb_line = picked_side_line(gb_home, side="GB", home="CHI")
    assert cover_result(Decimal(21) - Decimal(24), gb_line) == "P"
    assert EXPECTED["2099_01_GB_CHI-spread"][7] == "L"
    assert EXPECTED["2099_01_GB_CHI-spread"][1] == "odds_api"


def test_kickoff_conversion_and_parquet_fallback(monkeypatch, tmp_path: Path):
    games, source = load_games(fixture=FIXTURES / "schedules.csv")
    assert source.startswith("fixture:")
    by_id = {game.game_id: game for game in games}
    assert format_ts(by_id["2099_01_DET_PHI"].kickoff_utc) == "2099-09-13T17:00:00Z"
    assert format_ts(by_id["2099_01_BAL_PIT"].kickoff_utc) == "2099-09-14T00:20:00Z"
    assert format_ts(by_id["2099_02_NE_MIA"].kickoff_utc) == "2099-09-20T17:00:00Z"
    assert by_id["2099_01_LV_DEN"].spread_line is None
    assert by_id["2099_02_NE_MIA"].home_score is None

    frame = read_schedule_file(FIXTURES / "schedules.csv")
    blob = tmp_path / "games.parquet"
    frame.write_parquet(blob)
    payload = blob.read_bytes()
    seen = {}

    def downloader(url: str) -> bytes:
        seen["url"] = url
        return payload

    import nflreadpy

    monkeypatch.setattr(nflreadpy, "load_schedules", lambda seasons=True: (_ for _ in ()).throw(RuntimeError("down")))
    loaded, fallback_source = load_games(downloader=downloader)
    assert seen["url"] == GAMES_PARQUET_URL
    assert fallback_source == "nflverse-data:schedules/games.parquet"
    assert format_ts(next(g.kickoff_utc for g in loaded if g.game_id == "2099_01_DET_PHI")) == "2099-09-13T17:00:00Z"

    calls = {"n": 0}

    def succeed(seasons=True):
        calls["n"] += 1
        return frame

    monkeypatch.setattr(nflreadpy, "load_schedules", succeed)

    def explode(url: str) -> bytes:
        raise AssertionError(url)

    preferred, label = load_games(downloader=explode)
    load_games(downloader=explode)
    assert label == "nflreadpy"
    assert len(preferred) == 11
    assert calls["n"] == 2


def _summary_from_rows() -> dict:
    graded = [fields for fields in EXPECTED.values() if fields[-1] == "graded"]
    wins = sum(1 for fields in graded if fields[7] == "W")
    losses = sum(1 for fields in graded if fields[7] == "L")
    pushes = sum(1 for fields in graded if fields[7] == "P")
    units = sum((Decimal(fields[8]) for fields in graded), start=Decimal(0))
    clvs = [Decimal(fields[9]) for fields in graded]
    positive = sum(1 for value in clvs if value > 0)
    return {
        "w": wins,
        "l": losses,
        "p": pushes,
        "win_pct": (Decimal(wins) / Decimal(wins + losses)).quantize(Decimal("0.001")),
        "units": units.quantize(Decimal("0.001")),
        "avg_clv": (sum(clvs, start=Decimal(0)) / Decimal(len(clvs))).quantize(Decimal("0.001")),
        "pct_clv_pos": (Decimal(positive) / Decimal(len(clvs))).quantize(Decimal("0.001")),
        "rejected_count": sum(1 for fields in EXPECTED.values() if fields[-1] == "rejected_late"),
        "ungraded_count": sum(1 for fields in EXPECTED.values() if fields[-1] == "ungraded"),
    }


def _assert_sorted_keys(value) -> None:
    if isinstance(value, dict):
        assert list(value) == sorted(value)
        for child in value.values():
            _assert_sorted_keys(child)
    elif isinstance(value, list):
        for child in value:
            _assert_sorted_keys(child)
