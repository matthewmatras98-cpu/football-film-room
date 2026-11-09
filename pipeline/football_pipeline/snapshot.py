"""Turn an Odds API /odds payload into snapshot rows.

In-play lines are dropped two ways: an event whose commence_time is at or
before the snapshot, and a book-market whose last_update is at or after
kickoff. Spreads keep the home team's point as home_spread and drop the book
when the away point is not its negative. Totals keep the Over point and drop
the book when Over and Under disagree.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from football_pipeline.close import Game
from football_pipeline.schema import PipelineError, parse_ts
from football_pipeline.teams import Event, match_events, name_for


@dataclass(frozen=True)
class SnapshotRow:
    snapshot_ts: datetime
    game_id: str
    event_id: str
    book: str
    market: str
    side: str
    point: Decimal
    price: int
    last_update: datetime
    shot_kind: str


def format_point(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in {"", "-0"}:
        return "0"
    return text


def events_from_payload(payload: object) -> list[Event]:
    if not isinstance(payload, list):
        raise PipelineError("odds response must be a list of events")
    events: list[Event] = []
    for item in payload:
        if not isinstance(item, dict):
            raise PipelineError("odds response must be a list of events")
        event_id = str(item.get("id") or "").strip()
        if not event_id:
            raise PipelineError("event is missing id")
        home = item.get("home_team")
        away = item.get("away_team")
        if not home or not away:
            raise PipelineError(f"event {event_id} is missing teams")
        try:
            commence = parse_ts(str(item.get("commence_time")))
        except (TypeError, ValueError):
            raise PipelineError(f"event {event_id} has no commence_time") from None
        events.append(Event(event_id, str(home), str(away), commence))
    return events


def rows_from_odds(payload: object, games: list[Game], snapshot_ts: datetime, shot_kind_for) -> list[SnapshotRow]:
    events = events_from_payload(payload)
    matched = match_events(events, games)
    assert isinstance(payload, list)
    rows: list[SnapshotRow] = []
    for item, event in zip(payload, events, strict=True):
        if event.commence_time <= snapshot_ts:
            continue
        game = matched[event.event_id]
        if game.kickoff_utc is None:
            raise PipelineError(f"{game.game_id} has no kickoff")
        kind = shot_kind_for(game)
        for book in item.get("bookmakers") or []:
            book_key = str(book.get("key") or "").strip()
            if not book_key:
                continue
            book_updated = _optional_ts(book.get("last_update"))
            for market in book.get("markets") or []:
                market_key = market.get("key")
                if market_key not in {"spreads", "totals"}:
                    continue
                updated = _optional_ts(market.get("last_update")) or book_updated
                if updated is None or updated >= game.kickoff_utc:
                    continue
                outcomes = market.get("outcomes") or []
                if market_key == "spreads":
                    built = _spread_rows(game, event.event_id, book_key, outcomes, snapshot_ts, updated, kind)
                else:
                    built = _total_rows(game, event.event_id, book_key, outcomes, snapshot_ts, updated, kind)
                rows.extend(built)
    return rows


def _spread_rows(game, event_id, book, outcomes, snapshot_ts, updated, kind) -> list[SnapshotRow]:
    by_name = _outcomes_by_name(outcomes)
    if by_name is None:
        return []
    home_name = name_for(game.home_team)
    away_name = name_for(game.away_team)
    home = by_name.get(home_name)
    away = by_name.get(away_name)
    if home is None or away is None:
        return []
    home_point = Decimal(str(home["point"]))
    away_point = Decimal(str(away["point"]))
    if home_point + away_point != 0:
        return []
    return [
        _row(snapshot_ts, game.game_id, event_id, book, "spread", game.home_team, home_point, home["price"], updated, kind),
        _row(snapshot_ts, game.game_id, event_id, book, "spread", game.away_team, away_point, away["price"], updated, kind),
    ]


def _total_rows(game, event_id, book, outcomes, snapshot_ts, updated, kind) -> list[SnapshotRow]:
    by_name = _outcomes_by_name(outcomes)
    if by_name is None:
        return []
    over = by_name.get("Over")
    under = by_name.get("Under")
    if over is None or under is None:
        return []
    over_point = Decimal(str(over["point"]))
    under_point = Decimal(str(under["point"]))
    if over_point != under_point:
        return []
    return [
        _row(snapshot_ts, game.game_id, event_id, book, "total", "over", over_point, over["price"], updated, kind),
        _row(snapshot_ts, game.game_id, event_id, book, "total", "under", under_point, under["price"], updated, kind),
    ]


def _outcomes_by_name(outcomes) -> dict | None:
    by_name: dict = {}
    for outcome in outcomes:
        if not isinstance(outcome, dict):
            return None
        name = outcome.get("name")
        if not name or name in by_name:
            return None
        if outcome.get("point") is None or outcome.get("price") is None:
            return None
        by_name[str(name)] = outcome
    return by_name


def _row(snapshot_ts, game_id, event_id, book, market, side, point, price, updated, kind) -> SnapshotRow:
    return SnapshotRow(
        snapshot_ts=snapshot_ts,
        game_id=game_id,
        event_id=event_id,
        book=book,
        market=market,
        side=side,
        point=point,
        price=int(price),
        last_update=updated,
        shot_kind=kind,
    )


def _optional_ts(value) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        return parse_ts(str(value))
    except ValueError:
        return None
