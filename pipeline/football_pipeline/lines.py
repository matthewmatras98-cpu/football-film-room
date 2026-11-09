"""Sign convention for spreads.

Every line is stored as home_spread: the home team's handicap in betting
notation (-3.0 means the home team is favored by 3). nflverse's spread_line
uses the opposite sign (positive means home is favored). The Odds API point
on the home team's spread outcome is already home_spread.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal


def home_spread_from_nflverse(spread_line: float | Decimal) -> Decimal:
    """home_spread = −spread_line."""
    return -Decimal(str(spread_line))


def home_spread_from_odds_api_point(home_point: float | Decimal) -> Decimal:
    """Odds API point on the home team's spread outcome.

    This is a pure function. It does not call the Odds API.
    """
    return Decimal(str(home_point))


def home_spread_from_odds_outcomes(
    outcomes: Mapping[str, float | Decimal],
    home: str,
) -> Decimal:
    """Pick the home team's point out of a spreads market.

    `outcomes` maps a team abbreviation to that team's point. Whichever team
    is home, home_spread is that team's point. The away point is its negative
    and is not averaged in.
    """
    if home not in outcomes:
        raise KeyError(f"no spread point for home team {home}")
    return home_spread_from_odds_api_point(outcomes[home])


def picked_side_line(home_spread: Decimal | float, *, side: str, home: str) -> Decimal:
    """Home pick uses home_spread. Away pick uses −home_spread."""
    line = Decimal(str(home_spread))
    if side == home:
        return line
    return -line


def cover_result(margin: Decimal | float, line: Decimal | float) -> str:
    """W if margin + line > 0, L if < 0, P if = 0.

    margin is picked points minus opponent points. line is the picked side's
    handicap (negative when that side is favored).
    """
    value = Decimal(str(margin)) + Decimal(str(line))
    if value > 0:
        return "W"
    if value < 0:
        return "L"
    return "P"
