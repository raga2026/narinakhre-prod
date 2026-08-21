from datetime import date

from stoqbell.utils.trading_calendar import is_trading_day


def test_ordinary_weekday_is_a_trading_day():
    assert is_trading_day(date(2026, 8, 18)) is True  # a Tuesday, no holiday


def test_saturday_is_not_a_trading_day():
    assert is_trading_day(date(2026, 8, 15)) is False  # a Saturday


def test_sunday_is_not_a_trading_day():
    assert is_trading_day(date(2026, 8, 16)) is False  # a Sunday


def test_listed_nse_holiday_on_a_weekday_is_not_a_trading_day():
    assert is_trading_day(date(2026, 1, 26)) is False  # Republic Day, a Monday


def test_christmas_2026_is_not_a_trading_day():
    assert is_trading_day(date(2026, 12, 25)) is False  # a Friday


def test_day_after_a_holiday_is_a_trading_day_again():
    assert is_trading_day(date(2026, 1, 27)) is True


def test_year_with_no_holiday_list_falls_back_to_weekend_only():
    # 2031 isn't in NSE_HOLIDAYS -- must not raise, and must not treat every
    # day as a holiday just because the year is unlisted.
    assert is_trading_day(date(2031, 6, 3)) is True   # a Tuesday
    assert is_trading_day(date(2031, 6, 7)) is False  # a Saturday


def test_defaults_to_today_when_no_date_given():
    # Just needs to not raise and return a bool -- today's actual
    # weekday/holiday status will vary by when the suite runs.
    assert isinstance(is_trading_day(), bool)
