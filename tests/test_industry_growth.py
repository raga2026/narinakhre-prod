"""Tests for utils/industry_growth.py -- same FakeCursor/matching-by-SQL-
prefix pattern as the other Stocks engine tests (no real database)."""
from utils.industry_growth import INDUSTRY_GROWTH_MIN_SAMPLE_SIZE, compute_industry_growth


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeIndustryGrowthDB:
    def __init__(self, dates, close_rows):
        """dates: list of trade_date strings, most recent first (what the
        DISTINCT-trade_date query would return). close_rows: rows for the
        latest_date/previous_date comparison query, each
        {'industry', 'latest_close', 'prev_close'}."""
        self.dates = dates
        self.close_rows = close_rows

    def execute(self, sql, params=None):
        normalized = ' '.join(sql.split())
        if normalized.startswith('SELECT DISTINCT trade_date FROM stock_daily_data'):
            return FakeCursor([{'trade_date': d} for d in self.dates])
        if normalized.startswith('SELECT u.industry, d1.close AS latest_close, d0.close AS prev_close'):
            return FakeCursor(self.close_rows)
        raise AssertionError(f'Unexpected SQL in test: {sql}')


def _row(industry, latest_close, prev_close):
    return {'industry': industry, 'latest_close': latest_close, 'prev_close': prev_close}


def test_fewer_than_two_trade_dates_reports_unavailable():
    db = FakeIndustryGrowthDB(dates=['2026-08-20'], close_rows=[])
    result = compute_industry_growth(db)
    assert result == {'available': False}


def test_zero_trade_dates_reports_unavailable():
    db = FakeIndustryGrowthDB(dates=[], close_rows=[])
    result = compute_industry_growth(db)
    assert result == {'available': False}


def test_industry_below_min_sample_size_is_excluded():
    assert INDUSTRY_GROWTH_MIN_SAMPLE_SIZE == 3
    rows = [_row('Tiny Industry', 110, 100), _row('Tiny Industry', 105, 100)]  # only 2, below the floor
    db = FakeIndustryGrowthDB(dates=['2026-08-20', '2026-08-19'], close_rows=rows)

    result = compute_industry_growth(db)

    assert result['available'] is True
    assert result['gainers'] == []
    assert result['losers'] == []
    # Still counted in the overall average, though -- just not broken out
    # as its own industry line.
    assert result['overall_sample_size'] == 2


def test_gainers_and_losers_split_correctly():
    rows = (
        [_row('Banking', 110, 100)] * 3   # +10%
        + [_row('IT', 102, 100)] * 3       # +2%
        + [_row('Textiles', 90, 100)] * 3  # -10%
    )
    db = FakeIndustryGrowthDB(dates=['2026-08-20', '2026-08-19'], close_rows=rows)

    result = compute_industry_growth(db, top_n=5)

    assert result['available'] is True
    assert result['latest_date'] == '2026-08-20'
    assert result['previous_date'] == '2026-08-19'
    gainer_names = [g['industry'] for g in result['gainers']]
    assert gainer_names == ['Banking', 'IT']  # sorted best-first, Textiles is a loser not a gainer
    assert result['gainers'][0]['avg_change_pct'] == 10.0
    loser_names = [l['industry'] for l in result['losers']]
    assert loser_names == ['Textiles']
    assert result['losers'][0]['avg_change_pct'] == -10.0


def test_all_positive_day_leaves_losers_empty():
    # Only 2 qualifying industries total, both positive -- gainers/losers
    # split strictly by sign, so 'losers' stays empty rather than padding
    # itself out with the weaker of two still-positive industries.
    rows = [_row('Banking', 110, 100)] * 3 + [_row('IT', 105, 100)] * 3
    db = FakeIndustryGrowthDB(dates=['2026-08-20', '2026-08-19'], close_rows=rows)

    result = compute_industry_growth(db, top_n=5)

    assert [g['industry'] for g in result['gainers']] == ['Banking', 'IT']
    assert result['losers'] == []


def test_overall_average_blends_every_qualifying_and_non_qualifying_row():
    rows = [_row('Banking', 110, 100)] * 3 + [_row('IT', 90, 100)] * 3
    db = FakeIndustryGrowthDB(dates=['2026-08-20', '2026-08-19'], close_rows=rows)

    result = compute_industry_growth(db)

    assert result['overall_sample_size'] == 6
    assert result['overall_avg_change_pct'] == 0.0  # +10% and -10%, evenly split


def test_top_n_caps_the_number_of_gainers_and_losers_shown():
    rows = []
    for i in range(8):
        rows += [_row(f'Industry{i}', 100 + i, 100)] * 3  # ascending gains, all positive
    db = FakeIndustryGrowthDB(dates=['2026-08-20', '2026-08-19'], close_rows=rows)

    result = compute_industry_growth(db, top_n=3)

    assert len(result['gainers']) == 3
    assert result['gainers'][0]['industry'] == 'Industry7'  # biggest gain first
