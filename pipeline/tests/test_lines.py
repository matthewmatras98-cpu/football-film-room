"""Q3 sign tests. No network: these are pure functions."""

from decimal import Decimal

from football_pipeline.lines import (
    cover_result,
    home_spread_from_nflverse,
    home_spread_from_odds_api_point,
    home_spread_from_odds_outcomes,
    picked_side_line,
)


def test_positive_nflverse_spread_flips_to_home_favorite():
    home = home_spread_from_nflverse(Decimal("3.0"))
    assert home == Decimal("-3.0")
    assert picked_side_line(home, side="HOME", home="HOME") == Decimal("-3.0")
    assert picked_side_line(home, side="AWAY", home="HOME") == Decimal("3.0")


def test_negative_nflverse_spread_flips_to_away_favorite():
    home = home_spread_from_nflverse(Decimal("-3.0"))
    assert home == Decimal("3.0")
    assert picked_side_line(home, side="HOME", home="HOME") == Decimal("3.0")
    assert picked_side_line(home, side="AWAY", home="HOME") == Decimal("-3.0")


def test_jax_bal_2026_week_9_covers_from_the_nflverse_sign():
    # 2026_09_JAX_BAL: away JAX, home BAL. spread_line -8.5 means JAX was
    # favored. result = home - away = -23, so JAX won by 23.
    spread_line = Decimal("-8.5")
    result = Decimal("-23")
    home_spread = home_spread_from_nflverse(spread_line)
    assert home_spread == Decimal("8.5")
    jax_line = picked_side_line(home_spread, side="JAX", home="BAL")
    bal_line = picked_side_line(home_spread, side="BAL", home="BAL")
    assert jax_line == Decimal("-8.5")
    assert bal_line == Decimal("8.5")
    assert cover_result(-result, jax_line) == "W"
    assert cover_result(result, bal_line) == "L"


def test_odds_api_docs_example_whichever_team_is_home():
    # Docs example: TB -6.5, DAL +6.5. home_spread is the home team's point.
    outcomes = {"TB": Decimal("-6.5"), "DAL": Decimal("6.5")}
    assert home_spread_from_odds_api_point(outcomes["TB"]) == Decimal("-6.5")

    tb_home = home_spread_from_odds_outcomes(outcomes, "TB")
    assert tb_home == Decimal("-6.5")
    assert picked_side_line(tb_home, side="TB", home="TB") == Decimal("-6.5")
    assert picked_side_line(tb_home, side="DAL", home="TB") == Decimal("6.5")

    dal_home = home_spread_from_odds_outcomes(outcomes, "DAL")
    assert dal_home == Decimal("6.5")
    assert picked_side_line(dal_home, side="DAL", home="DAL") == Decimal("6.5")
    assert picked_side_line(dal_home, side="TB", home="DAL") == Decimal("-6.5")
