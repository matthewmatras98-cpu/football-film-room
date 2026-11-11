"""Grade picks against the closing line and write the public outputs.

Statuses:
  rejected_late  picked_at >= kickoff, or the ledger time for the current
                 row_sha >= kickoff, or there is no ledger row and kickoff
                 has passed relative to --as-of
  pending        no final score
  ungraded       final score but no closing line
  graded         a final and a close, and the pick was locked in time
  void           a void row whose picked_at and first_pushed_at are both
                 before kickoff. A later void is ignored and the pick stands.

Only graded picks count toward W-L-P, units, and CLV. Pushes count in CLV.
Voided picks are excluded from those figures and from rejected_count and
ungraded_count. generated_at is the --as-of instant. Nothing here reads the
wall clock.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from football_pipeline.close import BookLine, Close, Game, select_close
from football_pipeline.ledger import LedgerRow, lookup_push
from football_pipeline.lines import cover_result, picked_side_line
from football_pipeline.schema import (
    GRADED_COLUMNS,
    PickRow,
    format_ts,
    normalize_action,
    parse_ts,
    write_csv,
)

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
        graded.append(item)
    graded.sort(key=lambda item: item.pick.pick_id)
    return graded


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
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, cls=_Encoder)
    text = _NUM_PATTERN.sub(r"\1", text)
    if not text.endswith("\n"):
        text += "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(text.encode("utf-8"))
    temporary.replace(path)


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
