"""Lower-middle consensus. Never an average, including whole-number lines."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from football_pipeline.close import BookLine, Game, lower_middle, select_close
from football_pipeline.lines import cover_result

UTC = timezone.utc


def test_lower_middle_index_for_one_through_five_books():
    cases = [
        ([Decimal("-3")], Decimal("-3")),
        ([Decimal("-3"), Decimal("-2")], Decimal("-3")),
        ([Decimal("-3"), Decimal("-3"), Decimal("-1")], Decimal("-3")),
        (
            [Decimal("-3"), Decimal("-2.5"), Decimal("-2.5"), Decimal("-2")],
            Decimal("-2.5"),
        ),
        (
            [Decimal("-7"), Decimal("-3"), Decimal("-3"), Decimal("-2.5"), Decimal("-2")],
            Decimal("-3"),
        ),
    ]
    for points, expected in cases:
        chosen = lower_middle(points)
        assert chosen == expected
        assert chosen in points
        average = sum(points, Decimal(0)) / Decimal(len(points))
        if average not in points:
            assert chosen != average


def test_lower_middle_sorts_before_indexing():
    scrambled = [Decimal("-2"), Decimal("-3"), Decimal("-2.5"), Decimal("-2.5")]
    assert lower_middle(scrambled) == Decimal("-2.5")
    assert (len(scrambled) - 1) // 2 == 1


def test_whole_number_close_is_a_real_book_and_can_push():
    points = [Decimal("-3"), Decimal("-3"), Decimal("-2.5")]
    chosen = lower_middle(points)
    assert chosen == Decimal("-3")
    assert chosen in points
    assert chosen != sum(points, Decimal(0)) / Decimal(len(points))
    # Home wins by 3 against a -3 home line. The whole number pushes.
    assert cover_result(Decimal(3), chosen) == "P"


def test_two_books_take_the_lower_line_not_the_average():
    game = _game()
    close = select_close(
        game,
        [
            _book("book_b", Decimal("-2"), minutes_before=30),
            _book("book_a", Decimal("-3"), minutes_before=30),
        ],
    )
    assert close.line == Decimal("-3")
    assert close.line != Decimal("-2.5")
    assert close.source == "odds_api_thin"
    assert close.close_books == 2
    assert close.close_age_min == Decimal(30)
    assert close.close_ts == "2099-09-13T16:30:00Z"


def test_three_books_are_odds_api_and_one_book_is_thin():
    game = _game()
    three = select_close(
        game,
        [
            _book("a", Decimal("-3")),
            _book("b", Decimal("-3")),
            _book("c", Decimal("-2.5")),
        ],
    )
    assert three.line == Decimal("-3")
    assert three.source == "odds_api"
    assert three.close_books == 3

    one = select_close(game, [_book("only", Decimal("7"))])
    assert one.line == Decimal("7")
    assert one.source == "odds_api_thin"
    assert one.close_books == 1


def test_snapshot_beats_nflverse_and_missing_close_is_empty():
    game = _game(spread_line=Decimal("3.0"), total_line=Decimal("47.5"))
    snapshot = select_close(game, [_book("a", Decimal("-2.5")), _book("b", Decimal("-2")), _book("c", Decimal("-2"))])
    assert snapshot.source == "odds_api"
    assert snapshot.line == Decimal("-2")

    fallback = select_close(game, None, market="spread")
    assert fallback.source == "nflverse"
    assert fallback.line == Decimal("-3.0")
    assert fallback.close_ts == ""
    assert fallback.close_books is None
    assert fallback.close_age_min is None

    total = select_close(game, None, market="total")
    assert total.source == "nflverse"
    assert total.line == Decimal("47.5")

    empty = select_close(_game(spread_line=None, total_line=None), None)
    assert empty.line is None
    assert empty.source is None


def test_latest_pre_kick_snapshot_wins_and_in_play_lines_drop():
    game = _game(spread_line=Decimal("9"))
    close = select_close(
        game,
        [
            _book("a", Decimal("-1"), minutes_before=90),
            _book("b", Decimal("-1"), minutes_before=90),
            _book("c", Decimal("-1"), minutes_before=90),
            _book("a", Decimal("-4"), minutes_before=20),
            _book("b", Decimal("-3"), minutes_before=20),
            _book("c", Decimal("-3"), minutes_before=20),
            _book("a", Decimal("15"), minutes_before=-5),
        ],
    )
    assert close.line == Decimal("-3")
    assert close.source == "odds_api"
    assert close.close_books == 3
    assert close.close_age_min == Decimal(20)


def test_close_prefers_the_5_to_75_window_over_a_later_snapshot():
    game = _game(spread_line=Decimal("9"))
    close = select_close(
        game,
        [
            _book("a", Decimal("-1"), minutes_before=90),
            _book("b", Decimal("-1"), minutes_before=90),
            _book("c", Decimal("-1"), minutes_before=90),
            _book("a", Decimal("-3"), minutes_before=30),
            _book("b", Decimal("-3"), minutes_before=30),
            _book("c", Decimal("-2.5"), minutes_before=30),
            _book("a", Decimal("9"), minutes_before=2),
            _book("b", Decimal("9"), minutes_before=2),
            _book("c", Decimal("9"), minutes_before=2),
        ],
    )
    assert close.line == Decimal("-3")
    assert close.close_age_min == Decimal(30)
    assert close.source == "odds_api"
    assert close.close_books == 3


def test_age_boundaries_of_the_preferred_window():
    game = _game()
    at_five = select_close(
        game,
        [
            _book("a", Decimal("-6"), minutes_before=5),
            _book("a", Decimal("-1"), minutes_before=4),
        ],
    )
    assert at_five.line == Decimal("-6")
    assert at_five.close_age_min == Decimal(5)

    at_seventy_five = select_close(
        game,
        [
            _book("a", Decimal("-6"), minutes_before=75),
            _book("a", Decimal("-1"), minutes_before=76),
        ],
    )
    assert at_seventy_five.line == Decimal("-6")
    assert at_seventy_five.close_age_min == Decimal(75)


def test_snapshot_between_76_and_180_is_a_normal_close():
    game = _game()
    thin = select_close(game, [_book("only", Decimal("-2"), minutes_before=100)])
    assert thin.source == "odds_api_thin"
    assert thin.line == Decimal("-2")
    assert thin.close_age_min == Decimal(100)

    fresh = select_close(
        game,
        [
            _book("a", Decimal("-3"), minutes_before=76),
            _book("b", Decimal("-3"), minutes_before=76),
            _book("c", Decimal("-2.5"), minutes_before=76),
        ],
    )
    assert fresh.source == "odds_api"
    assert fresh.line == Decimal("-3")
    assert fresh.close_age_min == Decimal(76)


def test_only_a_sub_five_minute_snapshot_is_still_used():
    game = _game()
    close = select_close(game, [_book("only", Decimal("-2"), minutes_before=4)])
    assert close.line == Decimal("-2")
    assert close.source == "odds_api_thin"
    assert close.close_age_min == Decimal(4)


def test_four_books_past_180_are_stale_not_odds_api():
    game = _game()
    close = select_close(
        game,
        [
            _book("a", Decimal("-3"), minutes_before=181),
            _book("b", Decimal("-3"), minutes_before=181),
            _book("c", Decimal("-2.5"), minutes_before=181),
            _book("d", Decimal("-2"), minutes_before=181),
        ],
    )
    assert close.source == "odds_api_stale"
    assert close.close_books == 4
    assert close.line == Decimal("-3")


def test_stale_snapshot_is_labeled_and_exact_180_is_not():
    game = _game(spread_line=Decimal("3"))
    stale = select_close(game, [_book("a", Decimal("-2"), minutes_before=181)])
    assert stale.source == "odds_api_stale"
    assert stale.line == Decimal("-2")
    assert stale.close_books == 1

    fresh_enough = select_close(game, [_book("a", Decimal("-2"), minutes_before=180)])
    assert fresh_enough.source == "odds_api_thin"


def _game(spread_line: Decimal | None = Decimal("3"), total_line: Decimal | None = None) -> Game:
    return Game(
        game_id="2099_01_GB_CHI",
        season=2099,
        week=1,
        away_team="GB",
        home_team="CHI",
        kickoff_utc=datetime(2099, 9, 13, 17, 0, tzinfo=UTC),
        away_score=21,
        home_score=24,
        spread_line=spread_line,
        total_line=total_line,
    )


def _book(book: str, point: Decimal, minutes_before: int = 30) -> BookLine:
    kickoff = datetime(2099, 9, 13, 17, 0, tzinfo=UTC)
    return BookLine(
        game_id="2099_01_GB_CHI",
        market="spread",
        book=book,
        point=point,
        snapshot_ts=kickoff - timedelta(minutes=minutes_before),
    )
