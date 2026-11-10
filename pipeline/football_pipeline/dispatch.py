"""Decide which odds shots are due, then call /events and maybe /odds.

Fixed shots are Tuesday 14:07, Wednesday 16:07, and Saturday 16:07 UTC.
Each is due for 30 minutes and only when a game in that week has not kicked
off. Close shots group kickoffs within 25 minutes. Shot A is [K-75, K-40)
and shot B is [K-40, K-5). A run that lands in [K-5, K) still takes B when
B has not been stored yet. One /odds call covers every game in the response.

Budget skips write a shots row, emit ::notice::, append a step-summary line,
and exit 0. HTTP, D1, and mapping errors propagate and exit non-zero.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from football_pipeline.close import Game
from football_pipeline.d1 import D1Client, ShotRecord
from football_pipeline.odds import OddsClient
from football_pipeline.schema import PipelineError, format_ts
from football_pipeline.snapshot import events_from_payload, rows_from_odds
from football_pipeline.teams import match_events

FIXED_SLOTS: tuple[tuple[str, int, int, int], ...] = (
    ("tue", 1, 14, 7),
    ("wed", 2, 16, 7),
    ("sat", 5, 16, 7),
)
FIXED_DUE = timedelta(minutes=30)
WINDOW_GAP = timedelta(minutes=25)
MISSED_FOR = timedelta(hours=18)


@dataclass(frozen=True)
class Window:
    kickoff: datetime
    games: tuple[Game, ...]


@dataclass(frozen=True)
class PlannedShot:
    shot_key: str
    kind: str
    window_kickoff: datetime


def group_windows(games: list[Game]) -> list[Window]:
    ordered = sorted(
        (game for game in games if game.kickoff_utc is not None),
        key=lambda game: (game.kickoff_utc, game.game_id),
    )
    groups: list[list[Game]] = []
    anchor: datetime | None = None
    for game in ordered:
        kickoff = game.kickoff_utc
        assert kickoff is not None
        if anchor is None or kickoff - anchor > WINDOW_GAP:
            groups.append([game])
            anchor = kickoff
        else:
            groups[-1].append(game)
    return [
        Window(kickoff=group[0].kickoff_utc, games=tuple(group))
        for group in groups
        if group[0].kickoff_utc is not None
    ]


def close_kind(now: datetime, kickoff: datetime, b_taken: bool) -> str | None:
    """A in [K-75, K-40), B in [K-40, K-5), late B in [K-5, K) if B is new."""
    until = kickoff - now
    if timedelta(minutes=40) < until <= timedelta(minutes=75):
        return "A"
    if timedelta(minutes=5) < until <= timedelta(minutes=40):
        return "B"
    if timedelta(0) < until <= timedelta(minutes=5) and not b_taken:
        return "B"
    return None


def due_fixed_slots(now: datetime) -> list[tuple[str, datetime]]:
    now = now.astimezone(timezone.utc)
    found: list[tuple[str, datetime]] = []
    start = (now - FIXED_DUE).date()
    for offset in range(2):
        day = start + timedelta(days=offset)
        for name, weekday, hour, minute in FIXED_SLOTS:
            if day.weekday() != weekday:
                continue
            slot = datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc)
            if slot <= now <= slot + FIXED_DUE:
                found.append((name, slot))
    return found


def shot_allowed(remaining: int, kind: str) -> bool:
    """Tiered floors: >=150 all, 50-149 skip fixed, 20-49 skip fixed and A, <20 stop."""
    if remaining < 20:
        return False
    if kind == "fixed" and remaining < 150:
        return False
    if kind == "A" and remaining < 50:
        return False
    return True


def plan_shots(games: list[Game], now: datetime, existing_keys: set[str]) -> list[PlannedShot]:
    now = now.astimezone(timezone.utc)
    planned: list[PlannedShot] = []
    target = _target_week(games, now)
    if target is not None:
        season, week = target
        for name, _slot in due_fixed_slots(now):
            key = f"fixed:{season}:{week}:{name}"
            if key in existing_keys:
                continue
            kickoff = _earliest_remaining(games, season, week, now)
            if kickoff is None:
                continue
            planned.append(PlannedShot(key, "fixed", kickoff))
    for window in group_windows(games):
        stamp = format_ts(window.kickoff)
        b_key = f"B:{stamp}"
        kind = close_kind(now, window.kickoff, b_key in existing_keys)
        if kind is None:
            continue
        key = f"{kind}:{stamp}"
        if key in existing_keys:
            continue
        planned.append(PlannedShot(key, kind, window.kickoff))
    return planned


def missed_window_messages(games: list[Game], shots: list[ShotRecord], now: datetime) -> list[str]:
    now = now.astimezone(timezone.utc)
    taken = {
        shot.shot_key
        for shot in shots
        if shot.status == "taken" and shot.kind in {"A", "B"}
    }
    messages: list[str] = []
    for window in group_windows(games):
        age = now - window.kickoff
        if not (timedelta(0) <= age <= MISSED_FOR):
            continue
        stamp = format_ts(window.kickoff)
        if f"A:{stamp}" in taken or f"B:{stamp}" in taken:
            continue
        messages.append(f"window {stamp} got no shot")
    return messages


def run_dispatch(
    *,
    now: datetime,
    games: list[Game],
    odds: OddsClient,
    d1: D1Client,
    dry_run: bool = False,
) -> int:
    if now.tzinfo is None:
        raise PipelineError("dispatch now must include a timezone")
    now = now.astimezone(timezone.utc).replace(microsecond=0)
    d1.apply_schema()
    stored = d1.fetch_shots()
    for message in missed_window_messages(games, stored, now):
        print(f"::warning::{message}")
    if dry_run:
        payload, _headers = odds.get_events()
        match_events(events_from_payload(payload), games)
        print("dry_run ok")
        return 0
    due = plan_shots(games, now, {shot.shot_key for shot in stored})
    if not due:
        print("no shot due")
        return 0
    payload, headers = odds.get_events()
    match_events(events_from_payload(payload), games)
    remaining = _remaining(headers, stored)
    take = [shot for shot in due if shot_allowed(remaining, shot.kind)]
    skip = [shot for shot in due if shot not in take]
    for shot in skip:
        d1.insert_shot(
            ShotRecord(
                shot_key=shot.shot_key,
                kind=shot.kind,
                window_kickoff=format_ts(shot.window_kickoff),
                snapshot_ts=None,
                credits_remaining=remaining,
                status="skipped",
            )
        )
        _notice(f"skipped {shot.shot_key} x-requests-remaining={remaining}")
    if not take:
        return 0
    odds_payload, odds_headers = odds.get_odds()
    after = _remaining(odds_headers, stored, fallback=remaining)
    rows = rows_from_odds(odds_payload, games, now, _kind_for(games, take))
    d1.insert_snapshots(rows)
    total = d1.query("SELECT COUNT(*) AS n FROM snapshots", [])
    stored_rows = int(total[0]["n"])
    print(f"d1 snapshots: wrote {len(rows)} row(s), total {stored_rows}")
    taken_at = format_ts(now)
    for shot in take:
        d1.insert_shot(
            ShotRecord(
                shot_key=shot.shot_key,
                kind=shot.kind,
                window_kickoff=format_ts(shot.window_kickoff),
                snapshot_ts=taken_at,
                credits_remaining=after,
                status="taken",
            )
        )
    print(f"d1 shots: wrote {len(take)} row(s)")
    print(f"dispatch took {len(take)} shot(s), skipped {len(skip)}")
    return 0


def _target_week(games: list[Game], now: datetime) -> tuple[int, int] | None:
    remaining = [
        game for game in games if game.kickoff_utc is not None and game.kickoff_utc > now
    ]
    if not remaining:
        return None
    return min((game.season, game.week) for game in remaining)


def _earliest_remaining(games: list[Game], season: int, week: int, now: datetime) -> datetime | None:
    kickoffs = [
        game.kickoff_utc
        for game in games
        if game.season == season
        and game.week == week
        and game.kickoff_utc is not None
        and game.kickoff_utc > now
    ]
    if not kickoffs:
        return None
    return min(kickoffs)


def _remaining(headers: dict[str, str], shots: list[ShotRecord], fallback: int | None = None) -> int:
    raw = headers.get("x-requests-remaining", "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError:
            raise PipelineError("invalid x-requests-remaining header") from None
    if fallback is not None:
        return fallback
    stored = [shot.credits_remaining for shot in shots if shot.credits_remaining is not None]
    if not stored:
        raise PipelineError("no x-requests-remaining header and no stored credits")
    return int(stored[-1])


def _kind_for(games: list[Game], take: list[PlannedShot]):
    by_game: dict[str, datetime] = {}
    for window in group_windows(games):
        for game in window.games:
            by_game[game.game_id] = window.kickoff
    close_by_kickoff = {
        shot.window_kickoff: shot.kind for shot in take if shot.kind in {"A", "B"}
    }
    fixed = any(shot.kind == "fixed" for shot in take)
    default = "fixed" if fixed else take[0].kind

    def kind_for(game: Game) -> str:
        kickoff = by_game.get(game.game_id)
        if kickoff is not None and kickoff in close_by_kickoff:
            return close_by_kickoff[kickoff]
        return default

    return kind_for


def _notice(message: str) -> None:
    print(f"::notice::{message}")
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(message + "\n")
