"""Whether a given date is an NSE/BSE trading day -- used to gate the daily
Pick of the Day suggestion generation/email (see app.py's
stocks_suggestions_send_daily_email) so it doesn't run on weekends or
exchange holidays, when there's no new price/indicator data to act on.

NSE_HOLIDAYS below is a hardcoded, year-keyed list of holiday dates (source:
NSE/BSE holiday calendars as published by exchange member sites such as
Zerodha's holiday-calendar page -- not the exchanges' own circulars
directly, so treat these as best-effort, not gospel). NSE publishes the
following year's holiday list a few months ahead each year -- add each new
year's dates here when they're published; a year missing from this dict
just falls back to weekend-only checking (see is_trading_day) rather than
silently refusing to ever send a Pick of the Day again."""
from datetime import date

NSE_HOLIDAYS = {
    2026: {
        date(2026, 1, 15),   # Municipal Corporation elections (Maharashtra) -- exchange-observed
        date(2026, 1, 26),   # Republic Day
        date(2026, 3, 3),    # Holi
        date(2026, 3, 26),   # Shri Ram Navami
        date(2026, 3, 31),   # Shri Mahavir Jayanti
        date(2026, 4, 3),    # Good Friday
        date(2026, 4, 14),   # Dr. Baba Saheb Ambedkar Jayanti
        date(2026, 5, 1),    # Maharashtra Day
        date(2026, 5, 28),   # Bakri Eid
        date(2026, 6, 26),   # Moharram
        date(2026, 9, 14),   # Ganesh Chaturthi
        date(2026, 10, 2),   # Mahatma Gandhi Jayanti
        date(2026, 10, 20),  # Dussehra
        date(2026, 11, 10),  # Diwali-Balipratipada
        date(2026, 11, 24),  # Prakash Gurpurb Sri Guru Nanak Dev
        date(2026, 12, 25),  # Christmas
    },
}


def is_trading_day(check_date=None):
    """True unless check_date (defaults to today) is a Saturday/Sunday or a
    listed NSE holiday. A year with no entry in NSE_HOLIDAYS is treated as
    weekend-only (fails open) -- see the module docstring."""
    if check_date is None:
        check_date = date.today()
    if check_date.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    return check_date not in NSE_HOLIDAYS.get(check_date.year, frozenset())
