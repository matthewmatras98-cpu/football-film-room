"""Decide which odds shots are due, then call /events and maybe /odds.

Fixed shots are Tuesday 14:07, Wednesday 16:07, and Saturday 16:07 UTC.
Each is due for 30 minutes and only when a game in that week has not kicked
off. Close shots group kickoffs within 25 minutes. Shot A is [K-75, K-40)
and shot B is [K-40, K-5). A run that lands in [K-5, K) still takes B when
B has not been stored yet. One /odds call covers every game in the response.

A window whose latest kickoff (K_late) is more than 10 minutes after the
anchor gets one extra close shot, kind L, keyed L:<K_late>. It uses B's
clock measured from K_late: [K_late-40, K_late-5), and [K_late-5, K_late)
when that key is not stored yet. Windows that span 10 minutes or less,
including a single kickoff, get no L. Three or more distinct kickoffs still
get one L, keyed to the latest kickoff only. L is skipped below 150, the
same floor as a fixed shot. A, B, and fixed stay on 150/50/20.

Any tick can take a close shot whose kickoff is still ahead, at most once
per shot_key. Once B's slot has arrived (until <= 40 min) a missed A is not
taken, and one tick never takes both A and B for the same window. After
kickoff the window is left alone. L follows that same once-per-key rule
against K_late.

A close shot is a catch-up when its window started more than 30 minutes
before now. A's window starts at K-75 and B's at K-40. L's window starts at
K_late-40, the same as B. The normal scheduled tick is the one in the first
30 minutes of that window. Catch-up shots keep the same shot_key and the
same credit guard, and they emit ::notice::catch-up <shot_key>.

A scheduled run may wait, under a cap, for the next untaken close aim:
A at K-60, B at K-15, and L at K_late-15. A shot whose aim is already
behind us and still inside its window is taken on this tick. Fixed shots
stay on their own clock. The windows above, the credit gates, the catch-up
rule, and the one-row-per-key dedupe do not change. A dry run does not wait.

Budget skips write a shots row, emit ::notice::, append a step-summary line,
and exit 0. HTTP, D1, and mapping errors propagate and exit non-zero.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
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
# Latest kickoff must be past this gap before the window earns an L shot.
LATE_SPAN = timedelta(minutes=10)
MISSED_FOR = timedelta(hours=18)
# A close shot taken after this, measured from its own window start, is a
# catch-up: the cron tick that belongs to the shot is the one in the first
# 30 minutes. A starts at K-75; B starts at K-40; L starts at K_late-40.
CATCHUP_AFTER = timedelta(minutes=30)
# On-time aims. A at K-60 is the middle of A's first 30 minutes. B at K-15
# and L at K_late-15 are inside (K-40, K-5] and still before the catch-up
# line at K-10. Fixed shots have no aim.
CLOSE_AIM_BEFORE = {"A": timedelta(minutes=60), "B": timedelta(minutes=15), "L": timedelta(minutes=15)}


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


def is_catchup(now: datetime, kickoff: datetime, kind: str) -> bool:
    """True when this close shot is past the normal 30-minute tick.

    The window start is K-75 for A and K-40 for B and L. L is measured from
    its own kickoff, K_late. A shot taken more than 30 minutes after that
    start is a catch-up (for example A at K-41, or B or L inside the last
    10 minutes). Fixed shots are never catch-ups. Exactly 30 minutes is
    still the normal tick.
    """
    if kind == "A":
        start = kickoff - timedelta(minutes=75)
    elif kind in {"B", "L"}:
        start = kickoff - timedelta(minutes=40)
    else:
        return False
    return now - start > CATCHUP_AFTER


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
    """Tiered floors: >=150 all, 50-149 skip fixed and L, 20-49 also skip A, <20 stop."""
    if remaining < 20:
        return False
    if kind == "fixed" and remaining < 150:
        return False
    if kind == "L" and remaining < 150:
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
        if kind is not None:
            key = f"{kind}:{stamp}"
            if key not in existing_keys:
                planned.append(PlannedShot(key, kind, window.kickoff))
        late = _late_kickoff(window)
        if late is None:
            continue
        late_key = f"L:{format_ts(late)}"
        if late_key in existing_keys:
            continue
        # The key is new, so the (0, 5] late slot is still open. "B" is that
        # clock: (5, 40] before K_late, or (0, 5] when the shot is untaken.
        if close_kind(now, late, False) != "B":
            continue
        planned.append(PlannedShot(late_key, "L", late))
    return planned


def _late_kickoff(window: Window) -> datetime | None:
    """Latest kickoff when it is more than 10 minutes after the anchor.

    One kickoff, or a span of 10 minutes or less, has no late shot. Three
    or more distinct kickoffs still return only the latest.
    """
    kickoffs = [game.kickoff_utc for game in window.games if game.kickoff_utc is not None]
    if not kickoffs:
        return None
    latest = max(kickoffs)
    if latest - window.kickoff <= LATE_SPAN:
        return None
    return latest


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
    wait_max: timedelta | None = None,
    clock: Callable[[], datetime] | None = None,
    sleep: Callable[[float], None] | None = None,
    ready_keys: set[str] | None = None,
) -> int:
    if now.tzinfo is None:
        raise PipelineError("dispatch now must include a timezone")
    if wait_max is not None and wait_max > timedelta(0) and not dry_run:
        return _dispatch_waiting(
            now=now,
            games=games,
            odds=odds,
            d1=d1,
            wait_max=wait_max,
            clock=clock,
            sleep=sleep,
        )
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
    if ready_keys is not None:
        due = [shot for shot in due if shot.shot_key in ready_keys]
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
        _catchup_notice(now, shot)
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
        _catchup_notice(now, shot)
    print(f"d1 shots: wrote {len(take)} row(s)")
    print(f"dispatch took {len(take)} shot(s), skipped {len(skip)}")
    return 0


# GitHub's docs say scheduled workflows can be delayed or dropped under high
# load. On Nov 19, odds-dispatch ran about once an hour (13:16, 14:33,
# 15:33, ... 23:45, 01:05 UTC) instead of at :07/:37. TNF IND@HOU (K=01:15
# UTC) missed Shot A entirely, and Shot B fired as a catch-up at K-8.7m.
# Cron alone can't hit a 35-minute window, so waiting inside the job removes
# the dependency on cron timing.
def _dispatch_waiting(
    *,
    now: datetime,
    games: list[Game],
    odds: OddsClient,
    d1: D1Client,
    wait_max: timedelta,
    clock: Callable[[], datetime] | None,
    sleep: Callable[[float], None] | None,
) -> int:
    """Sleep to close-shot aims that fall inside ``wait_max``, then dispatch.

    Aims are K-60, K-15, and K_late-15. A shot whose aim is already behind
    us is taken on this tick. A due shot whose aim is still ahead is taken
    now only when it sits past the cap; otherwise this run sleeps to it, or
    leaves it for a later run that still has room. Each wake re-reads D1
    and calls the normal dispatch. Fixed slots inside the cap are wakes too,
    so a sleep does not skip Tuesday, Wednesday, or Saturday.
    """
    current = now.astimezone(timezone.utc).replace(microsecond=0)
    deadline = current + wait_max
    clock_fn = clock or _wall_clock
    sleep_fn = sleep or time.sleep
    dispatched_at: set[datetime] = set()
    d1.apply_schema()
    while True:
        keys = _shot_keys(d1)
        ready = _ready_shot_keys(games, current, keys, deadline, wait_max)
        if ready and current not in dispatched_at:
            dispatched_at.add(current)
            code = run_dispatch(
                now=current,
                games=games,
                odds=odds,
                d1=d1,
                ready_keys=ready,
            )
            if code != 0:
                return code
            keys = _shot_keys(d1)
        wake = _next_wake(games, current, keys, deadline)
        if wake is None:
            # A due shot left unready is being held for a later run. Do not
            # fall through to an unfiltered dispatch and take it early.
            if current in dispatched_at or plan_shots(games, current, keys):
                return 0
            return run_dispatch(now=current, games=games, odds=odds, d1=d1)
        delay = (wake - current).total_seconds()
        if delay > 0:
            print(f"wait {int(delay)}s until {format_ts(wake)}")
            sleep_fn(delay)
        landed = clock_fn().astimezone(timezone.utc).replace(microsecond=0)
        current = landed if landed >= wake else wake


def _wall_clock() -> datetime:
    return datetime.now(timezone.utc)


def _shot_keys(d1: D1Client) -> set[str]:
    return {shot.shot_key for shot in d1.fetch_shots()}


def _aim_at(shot: PlannedShot) -> datetime | None:
    offset = CLOSE_AIM_BEFORE.get(shot.kind)
    if offset is None:
        return None
    return shot.window_kickoff - offset


def _ready_shot_keys(
    games: list[Game],
    now: datetime,
    keys: set[str],
    deadline: datetime,
    wait_max: timedelta,
) -> set[str]:
    """Shot keys the normal dispatch should take at ``now``.

    A close shot already inside its window is held when its aim is still
    ahead and either this run or a fresh run under the same cap can reach
    it. Holding L at B's aim keeps the second /odds call. Fixed shots are
    ready whenever they are due.
    """
    ready: set[str] = set()
    for shot in plan_shots(games, now, keys):
        aim = _aim_at(shot)
        if aim is None or aim <= now:
            ready.add(shot.shot_key)
            continue
        if aim > deadline and aim - now > wait_max:
            ready.add(shot.shot_key)
    return ready


def _next_wake(games: list[Game], now: datetime, keys: set[str], deadline: datetime) -> datetime | None:
    candidates = [
        moment
        for moment in (*_future_close_aims(games, now, keys), *_future_fixed_slots(games, now, keys, deadline))
        if now < moment <= deadline
    ]
    if not candidates:
        return None
    return min(candidates)


def _future_close_aims(games: list[Game], now: datetime, keys: set[str]) -> list[datetime]:
    aims: list[datetime] = []
    for window in group_windows(games):
        stamp = format_ts(window.kickoff)
        if window.kickoff > now and f"A:{stamp}" not in keys:
            aim = window.kickoff - CLOSE_AIM_BEFORE["A"]
            if aim > now:
                aims.append(aim)
        if window.kickoff > now and f"B:{stamp}" not in keys:
            aim = window.kickoff - CLOSE_AIM_BEFORE["B"]
            if aim > now:
                aims.append(aim)
        late = _late_kickoff(window)
        if late is None or late <= now:
            continue
        late_key = f"L:{format_ts(late)}"
        if late_key in keys:
            continue
        aim = late - CLOSE_AIM_BEFORE["L"]
        if aim > now:
            aims.append(aim)
    return aims


def _future_fixed_slots(
    games: list[Game],
    now: datetime,
    keys: set[str],
    deadline: datetime,
) -> list[datetime]:
    slots: list[datetime] = []
    day = now.date()
    while day <= deadline.date():
        for name, weekday, hour, minute in FIXED_SLOTS:
            if day.weekday() != weekday:
                continue
            slot = datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc)
            if not (now < slot <= deadline):
                continue
            target = _target_week(games, slot)
            if target is None:
                continue
            season, week = target
            if f"fixed:{season}:{week}:{name}" in keys:
                continue
            if _earliest_remaining(games, season, week, slot) is None:
                continue
            slots.append(slot)
        day += timedelta(days=1)
    return slots


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


def _catchup_notice(now: datetime, shot: PlannedShot) -> None:
    if is_catchup(now, shot.window_kickoff, shot.kind):
        _notice(f"catch-up {shot.shot_key}")


def _notice(message: str) -> None:
    print(f"::notice::{message}")
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(message + "\n")
