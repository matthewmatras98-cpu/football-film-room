"""Grade picks against the closing line and write the public outputs.

Statuses:
  rejected_late  picked_at >= kickoff, or the ledger time for the current
                 row_sha >= kickoff, or there is no ledger row and kickoff
                 has passed relative to --as-of, or this is a replacement
                 (-rN) and any earlier pick_id for the same game and market
                 lacks a counted void
  pending        no final score
  ungraded       final score but no closing line
  graded         a final and a close, and the pick was locked in time
  void           a void row whose picked_at and first_pushed_at are both
                 before kickoff. A later void is ignored and the pick stands.
                 Each pick_id is graded on its own.

Only graded picks count toward W-L-P, units, and CLV. Pushes count in CLV.
Voided picks are excluded from those figures and from rejected_count and
ungraded_count. generated_at is the --as-of instant. Nothing here reads the
wall clock.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from football_pipeline.close import BookLine, Close, Game, select_close
from football_pipeline.ledger import LedgerRow, load_ledger, lookup_push
from football_pipeline.lines import cover_result, picked_side_line
from football_pipeline.schema import (
    GRADED_COLUMNS,
    PickRow,
    PipelineError,
    format_ts,
    normalize_action,
    parse_pick_file,
    parse_ts,
    previous_pick_id,
    write_csv,
)
from football_pipeline.validate import Issue, emit_annotations, validate_rows

UNIT_WIN = Decimal("0.909")
UNIT_LOSS = Decimal("-1.000")
UNIT_PUSH = Decimal("0.000")
_NUM_MARKER = "__DECIMAL__"
_NUM_PATTERN = re.compile(r'"__DECIMAL__(-?\d+(?:\.\d+)?)"')


@dataclass(frozen=True)
class GradedPick:
    pick: PickRow
    status: str
    closing_line: Decimal | None
    close_source: str | None
    close_ts: str
    close_books: int | None
    close_age_min: Decimal | None
    final_home: int | None
    final_away: int | None
    result: str | None
    units_pl: Decimal | None
    clv_pts: Decimal | None
    first_pushed_at: datetime | None
    voided_at: datetime | None = None


def grade_picks(
    picks: list[PickRow],
    games: dict[str, Game],
    ledger: list[LedgerRow],
    snapshot_closes: list[BookLine] | None,
    as_of: datetime,
) -> list[GradedPick]:
    voids: dict[str, list[PickRow]] = {}
    for pick in picks:
        if normalize_action(pick.cells["action"]) == "void":
            voids.setdefault(pick.pick_id, []).append(pick)
    graded: list[GradedPick] = []
    for pick in picks:
        if normalize_action(pick.cells["action"]) == "void":
            continue
        game = games[pick.cells["game_id"]]
        item = _grade_one(pick, game, ledger, snapshot_closes or [], as_of)
        void_row = _counting_void(voids.get(pick.pick_id, []), game, ledger)
        if void_row is not None:
            item = replace(
                item,
                status="void",
                result=None,
                units_pl=None,
                clv_pts=None,
                voided_at=parse_ts(void_row.cells["picked_at"]),
            )
        # A hand edit can append -rN without a counted void on every earlier id.
        earlier = _earlier_pick_ids(pick.pick_id, pick.cells["game_id"], pick.cells["market"])
        if any(
            _counting_void(voids.get(earlier_id, []), game, ledger) is None for earlier_id in earlier
        ):
            item = replace(
                item,
                status="rejected_late",
                result=None,
                units_pl=None,
                clv_pts=None,
                voided_at=None,
            )
        graded.append(item)
    graded.sort(key=lambda item: item.pick.pick_id)
    return graded


def _earlier_pick_ids(pick_id: str, game_id: str, market: str) -> list[str]:
    """Every pick id this replacement supersedes, oldest first.

    Empty when this id is not an `-rN`. A counted void has to exist for
    each of these ids, not only the immediately previous one.
    """
    chain: list[str] = []
    current = pick_id
    while True:
        previous = previous_pick_id(current, game_id, market)
        if previous is None:
            break
        chain.append(previous)
        current = previous
    chain.reverse()
    return chain


def _counting_void(voids: list[PickRow], game: Game, ledger: list[LedgerRow]) -> PickRow | None:
    """A void counts only when its own picked_at and push time are before kickoff."""
    if game.kickoff_utc is None:
        return None
    chosen: PickRow | None = None
    chosen_at: datetime | None = None
    for void in voids:
        picked_at = parse_ts(void.cells["picked_at"])
        pushed = lookup_push(ledger, void.pick_id, void.row_sha)
        if picked_at >= game.kickoff_utc:
            continue
        if pushed is None or pushed >= game.kickoff_utc:
            continue
        if chosen_at is None or picked_at < chosen_at:
            chosen = void
            chosen_at = picked_at
    return chosen


def summary_document(graded: list[GradedPick], as_of: datetime) -> dict:
    seasons = [int(item.pick.cells["season"]) for item in graded]
    counted = [item for item in graded if item.status == "graded"]
    wins = sum(1 for item in counted if item.result == "W")
    losses = sum(1 for item in counted if item.result == "L")
    pushes = sum(1 for item in counted if item.result == "P")
    units = sum(((item.units_pl or Decimal(0)) for item in counted), start=Decimal(0))
    clvs = [item.clv_pts for item in counted if item.clv_pts is not None]
    if wins + losses:
        win_pct: Decimal | None = Decimal(wins) / Decimal(wins + losses)
    else:
        win_pct = None
    if clvs:
        avg_clv: Decimal | None = sum(clvs, start=Decimal(0)) / Decimal(len(clvs))
        positive = sum(1 for value in clvs if value > 0)
        pct_positive: Decimal | None = Decimal(positive) / Decimal(len(clvs))
    else:
        avg_clv = None
        pct_positive = None
    return {
        "season": max(seasons) if seasons else None,
        "w": wins,
        "l": losses,
        "p": pushes,
        "win_pct": _q3(win_pct),
        "units": _q3(units),
        "avg_clv": _q3(avg_clv),
        "pct_clv_pos": _q3(pct_positive),
        "rejected_count": sum(1 for item in graded if item.status == "rejected_late"),
        "ungraded_count": sum(1 for item in graded if item.status == "ungraded"),
        "void_count": sum(1 for item in graded if item.status == "void"),
        "generated_at": format_ts(as_of),
    }


def picks_view(graded: list[GradedPick]) -> list[dict]:
    return [_view_row(item) for item in graded]


def write_outputs(
    root: Path,
    graded: list[GradedPick],
    as_of: datetime,
) -> tuple[Path, Path, Path]:
    graded_path = root / "data" / "graded.csv"
    summary_path = root / "src" / "data" / "summary.json"
    view_path = root / "src" / "data" / "picks_view.json"
    write_csv(graded_path, GRADED_COLUMNS, [_csv_fields(item) for item in graded])
    _write_json(summary_path, summary_document(graded, as_of))
    _write_json(view_path, picks_view(graded))
    return graded_path, summary_path, view_path


_GAME_ID = re.compile(r"^(?P<season>\d{4})_(?P<week>\d{2})_(?P<away>[A-Z]{2,3})_(?P<home>[A-Z]{2,3})$")


def resolve_offline_as_of(root: Path, explicit: str | None) -> datetime:
    """Prefer an explicit --as-of. Otherwise keep the published generated_at.

    A wall-clock stamp would change summary.json on every rerun. The last
    published instant stays until weekly grades again.
    """
    if explicit:
        return parse_ts(explicit)
    path = root / "src" / "data" / "summary.json"
    if path.is_file():
        payload = json.loads(path.read_text(encoding="utf-8"))
        stamp = payload.get("generated_at") if isinstance(payload, dict) else None
        if isinstance(stamp, str) and stamp:
            return parse_ts(stamp)
    raise PipelineError("grade --no-fetch needs --as-of or summary.json generated_at")


def republish(root: Path, as_of: datetime, schedules: Path | None = None) -> int:
    """Regenerate the public view from files already in the repo.

    Same grade_picks and JSON encoder as `grade`. No nflverse read and no
    snapshot pull. Picks already in picks_view.json keep their closes and
    scores. A new pick is graded from the game card when the repo has one.
    A void that is not in the ledger yet still counts: intake refreshes the
    view in the same push, before pick-ledger records the push, and the
    void's picked_at is already before kickoff.
    """
    picks_path = root / "data" / "picks.csv"
    rows, structural = parse_pick_file(picks_path)
    issues = [Issue(line, message) for line, message in structural]
    if issues:
        emit_annotations(root, picks_path, issues)
        print(f"error: {len(issues)} validation problem(s)", file=sys.stderr)
        return 1

    voids: dict[str, list[PickRow]] = {}
    picks: list[PickRow] = []
    for row in rows:
        if normalize_action(row.cells["action"]) == "void":
            voids.setdefault(row.pick_id, []).append(row)
        else:
            picks.append(row)

    published = _load_published_view(root)
    by_id = {row["pick_id"]: row for row in published}
    fresh_ids = {
        pick.pick_id
        for pick in picks
        if pick.pick_id not in by_id or _void_changed(by_id[pick.pick_id], voids.get(pick.pick_id, []))
    }
    needed: set[str] = set()
    by_pick_id = {pick.pick_id: pick for pick in picks}
    for pick_id in fresh_ids:
        pick = by_pick_id[pick_id]
        needed.add(pick_id)
        needed.update(_earlier_pick_ids(pick.pick_id, pick.cells["game_id"], pick.cells["market"]))

    ledger = load_ledger(root / "data" / "pick_ledger.csv")
    fresh_by_id: dict[str, GradedPick] = {}
    if needed:
        grade_rows = [row for row in rows if row.pick_id in needed]
        games = _games_for(root, grade_rows, as_of, schedules)
        game_issues = validate_rows(grade_rows, games)
        if game_issues:
            emit_annotations(root, picks_path, game_issues)
            print(f"error: {len(game_issues)} validation problem(s)", file=sys.stderr)
            return 1
        graded = grade_picks(
            grade_rows,
            games,
            _ledger_with_unpushed_voids(ledger, grade_rows),
            [],
            as_of,
        )
        fresh_by_id = {item.pick.pick_id: item for item in graded}

    merged: list[GradedPick] = []
    for pick in picks:
        published_row = by_id.get(pick.pick_id)
        fresh = fresh_by_id.get(pick.pick_id) if pick.pick_id in fresh_ids else None
        if fresh is None:
            if published_row is None:
                raise PipelineError(f"missing published row for {pick.pick_id}")
            merged.append(_graded_from_published(pick, published_row, _pushed_at(ledger, pick, published_row)))
            continue
        applied = _apply_fresh(fresh, published_row, bool(voids.get(pick.pick_id)))
        if applied is None:
            if published_row is None:
                raise PipelineError(f"missing published row for {pick.pick_id}")
            merged.append(_graded_from_published(pick, published_row, _pushed_at(ledger, pick, published_row)))
        else:
            merged.append(applied)
    merged.sort(key=lambda item: item.pick.pick_id)
    _publish_view(root, merged, as_of)
    print(f"graded {len(merged)} pick(s) as of {format_ts(as_of)}")
    return 0


def total_result(final_total: Decimal, close: Decimal, side: str) -> str:
    if side == "over":
        diff = final_total - close
    elif side == "under":
        diff = close - final_total
    else:
        raise ValueError(f"total side must be over or under, got {side}")
    if diff > 0:
        return "W"
    if diff < 0:
        return "L"
    return "P"


def clv_points(market: str, side: str, line_at_pick: Decimal, closing_line: Decimal) -> Decimal:
    if market == "spread":
        return line_at_pick - closing_line
    if side == "over":
        return closing_line - line_at_pick
    if side == "under":
        return line_at_pick - closing_line
    raise ValueError(f"unknown market {market}")


def is_rejected_late(
    *,
    picked_at: datetime,
    kickoff: datetime,
    first_pushed_at: datetime | None,
    as_of: datetime,
) -> bool:
    if picked_at >= kickoff:
        return True
    if first_pushed_at is not None and first_pushed_at >= kickoff:
        return True
    if first_pushed_at is None and kickoff <= as_of:
        return True
    return False


def _grade_one(
    pick: PickRow,
    game: Game,
    ledger: list[LedgerRow],
    snapshots: list[BookLine],
    as_of: datetime,
) -> GradedPick:
    if game.kickoff_utc is None:
        raise ValueError(f"{game.game_id} has no kickoff (gameday/gametime missing)")
    market = pick.cells["market"]
    side = pick.cells["side"]
    picked_at = parse_ts(pick.cells["picked_at"])
    pushed = lookup_push(ledger, pick.pick_id, pick.row_sha)
    close = select_close(game, snapshots, market)
    closing = _picked_close(close, market, side, game.home_team)
    final_home = game.home_score if game.has_final_score else None
    final_away = game.away_score if game.has_final_score else None
    rejected = is_rejected_late(
        picked_at=picked_at,
        kickoff=game.kickoff_utc,
        first_pushed_at=pushed,
        as_of=as_of,
    )
    if rejected:
        status = "rejected_late"
    elif not game.has_final_score:
        status = "pending"
    elif closing is None:
        status = "ungraded"
    else:
        status = "graded"

    result: str | None = None
    units: Decimal | None = None
    clv: Decimal | None = None
    if status == "graded" and closing is not None:
        if market == "spread":
            assert final_home is not None and final_away is not None
            if side == game.home_team:
                margin = Decimal(final_home) - Decimal(final_away)
            else:
                margin = Decimal(final_away) - Decimal(final_home)
            result = cover_result(margin, closing)
        else:
            assert final_home is not None and final_away is not None
            result = total_result(Decimal(final_home + final_away), closing, side)
        units = {"W": UNIT_WIN, "L": UNIT_LOSS, "P": UNIT_PUSH}[result]
        clv = clv_points(market, side, Decimal(pick.cells["line_at_pick"]), closing)

    return GradedPick(
        pick=pick,
        status=status,
        closing_line=closing,
        close_source=close.source,
        close_ts=close.close_ts,
        close_books=close.close_books,
        close_age_min=_whole_minutes(close.close_age_min),
        final_home=final_home,
        final_away=final_away,
        result=result,
        units_pl=units,
        clv_pts=clv,
        first_pushed_at=pushed,
    )


def _picked_close(close: Close, market: str, side: str, home: str) -> Decimal | None:
    if close.line is None:
        return None
    if market == "total":
        return close.line
    return picked_side_line(close.line, side=side, home=home)


def _whole_minutes(age: Decimal | None) -> Decimal | None:
    if age is None:
        return None
    nearest = age.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if age == nearest:
        return nearest
    return age


def _csv_fields(item: GradedPick) -> list[str]:
    return [
        item.pick.pick_id,
        _fmt_line(item.closing_line),
        item.close_source or "",
        item.close_ts,
        "" if item.close_books is None else str(item.close_books),
        _fmt_age(item.close_age_min),
        "" if item.final_home is None else str(item.final_home),
        "" if item.final_away is None else str(item.final_away),
        item.result or "",
        _fmt_units(item.units_pl),
        _fmt_line(item.clv_pts),
        item.status,
    ]


def _view_row(item: GradedPick) -> dict:
    cells = item.pick.cells
    return {
        "pick_id": cells["pick_id"],
        "game_id": cells["game_id"],
        "season": int(cells["season"]),
        "week": int(cells["week"]),
        "market": cells["market"],
        "side": cells["side"],
        "line_at_pick": Decimal(cells["line_at_pick"]),
        "picked_at": cells["picked_at"],
        "units": Decimal(cells["units"]),
        "confidence": int(cells["confidence"]) if cells["confidence"] else None,
        "tags": cells["tags"],
        "note": cells["note"],
        "closing_line": item.closing_line,
        "close_source": item.close_source,
        "close_ts": item.close_ts or None,
        "close_books": item.close_books,
        "close_age_min": _json_age(item.close_age_min),
        "final_home": item.final_home,
        "final_away": item.final_away,
        "result": item.result,
        "units_pl": item.units_pl,
        "clv_pts": item.clv_pts,
        "status": item.status,
        "first_pushed_at": format_ts(item.first_pushed_at) if item.first_pushed_at else None,
        "voided_at": format_ts(item.voided_at) if item.voided_at else None,
    }


def _fmt_line(value: Decimal | None) -> str:
    if value is None:
        return ""
    quantized = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if quantized == 0:
        quantized = Decimal("0.00")
    return f"{quantized:.2f}"


def _fmt_units(value: Decimal | None) -> str:
    if value is None:
        return ""
    quantized = value.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    if quantized == 0:
        quantized = Decimal("0.000")
    return f"{quantized:.3f}"


def _fmt_age(value: Decimal | None) -> str:
    if value is None:
        return ""
    if value == value.to_integral_value():
        return str(int(value))
    return f"{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):.2f}"


def _json_age(value: Decimal | None) -> int | Decimal | None:
    if value is None:
        return None
    if value == value.to_integral_value():
        return int(value)
    return value


def _q3(value: Decimal | None) -> _Fixed3 | None:
    if value is None:
        return None
    return _Fixed3(value.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))


def _write_json(path: Path, payload: object) -> None:
    _write_text(path, _json_text(payload))


def _json_text(payload: object) -> str:
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, cls=_Encoder)
    text = _NUM_PATTERN.sub(r"\1", text)
    if not text.endswith("\n"):
        text += "\n"
    return text


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(text.encode("utf-8"))
    temporary.replace(path)


def _load_published_view(root: Path) -> list[dict]:
    path = root / "src" / "data" / "picks_view.json"
    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
    if not isinstance(payload, list):
        raise PipelineError("picks_view.json must be a list")
    return payload


def _void_changed(published: dict, voids: list[PickRow]) -> bool:
    published_at = published.get("voided_at")
    current = [row.cells["picked_at"] for row in voids]
    if published_at is None:
        return bool(current)
    return published_at not in current or len(current) != 1


def _pushed_at(ledger: list[LedgerRow], pick: PickRow, published: dict) -> datetime | None:
    pushed = lookup_push(ledger, pick.pick_id, pick.row_sha)
    if pushed is not None:
        return pushed
    stamp = published.get("first_pushed_at")
    if isinstance(stamp, str) and stamp:
        return parse_ts(stamp)
    return None


def _ledger_with_unpushed_voids(ledger: list[LedgerRow], rows: list[PickRow]) -> list[LedgerRow]:
    """Treat a not-yet-pushed void as locked at its picked_at.

    The refresh runs in the intake push, so pick-ledger has not appended the
    void. picked_at is the time intake already checked against kickoff.
    _counting_void still drops it when that time is not before kickoff.
    """
    extra: list[LedgerRow] = []
    for row in rows:
        if normalize_action(row.cells["action"]) != "void":
            continue
        if lookup_push(ledger, row.pick_id, row.row_sha) is not None:
            continue
        extra.append(LedgerRow(row.pick_id, "void", row.row_sha, parse_ts(row.cells["picked_at"])))
    if not extra:
        return ledger
    return [*ledger, *extra]


def _games_for(
    root: Path,
    rows: list[PickRow],
    as_of: datetime,
    schedules: Path | None,
) -> dict[str, Game]:
    schedule_games: dict[str, Game] | None = None
    games: dict[str, Game] = {}
    by_game: dict[str, list[PickRow]] = {}
    for row in rows:
        by_game.setdefault(row.cells["game_id"], []).append(row)
    for game_id, game_rows in by_game.items():
        card = _card_game(root, game_id)
        if card is not None:
            games[game_id] = card
            continue
        if schedules is not None:
            if schedule_games is None:
                schedule_games = _games_from_schedules(schedules)
            if game_id in schedule_games:
                games[game_id] = schedule_games[game_id]
                continue
        games[game_id] = _synthetic_game(game_id, game_rows, as_of)
    return games


def _games_from_schedules(path: Path) -> dict[str, Game]:
    from football_pipeline.nflv import load_games

    loaded, _source = load_games(fixture=path)
    return {game.game_id: game for game in loaded}


def _card_game(root: Path, game_id: str) -> Game | None:
    path = root / "src" / "content" / "games" / f"{game_id}.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PipelineError(f"{path.name} is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise PipelineError(f"{game_id} card is missing schedule fields")
    kickoff = payload.get("kickoff_utc")
    if not isinstance(kickoff, str) or not kickoff:
        raise PipelineError(f"{game_id} card has no kickoff")
    try:
        return Game(
            game_id=str(payload.get("game_id") or game_id),
            season=int(payload["season"]),
            week=int(payload["week"]),
            away_team=str(payload["away"]),
            home_team=str(payload["home"]),
            kickoff_utc=parse_ts(kickoff),
            away_score=None,
            home_score=None,
            spread_line=None,
            total_line=None,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise PipelineError(f"{game_id} card is missing schedule fields") from exc


def _synthetic_game(game_id: str, rows: list[PickRow], as_of: datetime) -> Game:
    match = _GAME_ID.fullmatch(game_id)
    if match is None:
        raise PipelineError(f"unknown game_id {game_id}")
    latest = as_of
    for row in rows:
        picked_at = parse_ts(row.cells["picked_at"])
        if picked_at > latest:
            latest = picked_at
    return Game(
        game_id=game_id,
        season=int(match.group("season")),
        week=int(match.group("week")),
        away_team=match.group("away"),
        home_team=match.group("home"),
        kickoff_utc=latest + timedelta(hours=1),
        away_score=None,
        home_score=None,
        spread_line=None,
        total_line=None,
    )


def _apply_fresh(item: GradedPick, published: dict | None, voids_present: bool) -> GradedPick | None:
    if published is None:
        return item
    if item.status == "void":
        return _fill_from_published(item, published)
    # A void that does not count, or a grade we cannot reproduce offline,
    # keeps the row weekly already published.
    if voids_present or item.status != published.get("status"):
        return None
    return item


def _fill_from_published(item: GradedPick, published: dict) -> GradedPick:
    closing = item.closing_line
    source = item.close_source
    close_ts = item.close_ts
    books = item.close_books
    age = item.close_age_min
    final_home = item.final_home
    final_away = item.final_away
    if closing is None and published.get("closing_line") is not None:
        closing = _as_decimal(published["closing_line"])
        source = published.get("close_source")
        close_ts = published.get("close_ts") or ""
        books = published.get("close_books")
        raw_age = published.get("close_age_min")
        age = _as_decimal(raw_age) if raw_age is not None else None
    if final_home is None and published.get("final_home") is not None:
        final_home = published.get("final_home")
        final_away = published.get("final_away")
    return replace(
        item,
        closing_line=closing,
        close_source=source,
        close_ts=close_ts,
        close_books=books,
        close_age_min=age,
        final_home=final_home,
        final_away=final_away,
    )


def _graded_from_published(pick: PickRow, published: dict, pushed: datetime | None) -> GradedPick:
    voided = published.get("voided_at")
    age = published.get("close_age_min")
    return GradedPick(
        pick=pick,
        status=str(published["status"]),
        closing_line=_as_decimal(published["closing_line"]) if published.get("closing_line") is not None else None,
        close_source=published.get("close_source"),
        close_ts=published.get("close_ts") or "",
        close_books=published.get("close_books"),
        close_age_min=_as_decimal(age) if age is not None else None,
        final_home=published.get("final_home"),
        final_away=published.get("final_away"),
        result=published.get("result"),
        units_pl=_as_decimal(published["units_pl"]) if published.get("units_pl") is not None else None,
        clv_pts=_as_decimal(published["clv_pts"]) if published.get("clv_pts") is not None else None,
        first_pushed_at=pushed,
        voided_at=parse_ts(voided) if isinstance(voided, str) and voided else None,
    )


def _as_decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _publish_view(root: Path, graded: list[GradedPick], as_of: datetime) -> None:
    """Write summary.json and picks_view.json when the bytes change.

    graded.csv stays as weekly left it. These two files are what the site reads.
    """
    summary_path = root / "src" / "data" / "summary.json"
    view_path = root / "src" / "data" / "picks_view.json"
    summary_text = _json_text(summary_document(graded, as_of))
    view_text = _json_text(picks_view(graded))
    if not _same_text(summary_path, summary_text):
        _write_text(summary_path, summary_text)
    if not _same_text(view_path, view_text):
        _write_text(view_path, view_text)


def _same_text(path: Path, text: str) -> bool:
    if not path.is_file():
        return False
    return path.read_bytes() == text.encode("utf-8")


class _Fixed3(Decimal):
    """Summary figure written with exactly three decimal places."""


class _Encoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, _Fixed3):
            quantized = Decimal(o).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
            return f"{_NUM_MARKER}{quantized:.3f}"
        if isinstance(o, Decimal):
            quantized = o.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
            text = format(quantized, "f")
            if "." in text:
                text = text.rstrip("0").rstrip(".")
            if text in {"", "-0", "-"}:
                text = "0"
            return f"{_NUM_MARKER}{text}"
        return super().default(o)
