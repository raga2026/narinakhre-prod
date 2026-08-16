from utils.stock_indicators import run_indicator_calculation, run_indicator_calculation_universe


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeIndicatorsDB:
    def __init__(self, watchlist_rows, price_rows_by_watchlist_id):
        self.watchlist_rows = watchlist_rows
        self.price_rows_by_watchlist_id = price_rows_by_watchlist_id
        self.indicators = {}  # (watchlist_id, calc_date) -> row dict

    def execute(self, sql, params=None):
        params = params or ()
        normalized = ' '.join(sql.split())

        if normalized.startswith('SELECT id, symbol, exchange FROM stock_watchlist'):
            rows = [r for r in self.watchlist_rows if r['is_active'] == 1]
            return FakeCursor(rows)

        if normalized.startswith('SELECT close, volume FROM stock_daily_data'):
            (watchlist_id,) = params
            return FakeCursor(self.price_rows_by_watchlist_id.get(watchlist_id, []))

        if normalized.startswith('INSERT INTO stock_indicators (watchlist_id, universe_id, calc_date'):
            (watchlist_id, universe_id, calc_date, ma5, ma21, ma50, ma200, rsi14,
             volume_avg_20d, volume_trend, cross_status) = params
            self.indicators[(watchlist_id, calc_date)] = {
                'universe_id': universe_id, 'ma_5': ma5, 'ma_21': ma21, 'ma_50': ma50, 'ma_200': ma200,
                'rsi_14': rsi14, 'volume_avg_20d': volume_avg_20d, 'volume_trend': volume_trend,
                'cross_status': cross_status,
            }
            return FakeCursor([])

        raise AssertionError(f'Unexpected SQL in test: {sql}')

    def commit(self):
        pass


def test_symbol_with_fewer_than_21_days_is_skipped_without_halting_batch():
    watchlist = [
        {'id': 1, 'symbol': 'TOOSHORT', 'exchange': 'NSE', 'is_active': 1},
        {'id': 2, 'symbol': 'ENOUGH', 'exchange': 'NSE', 'is_active': 1},
    ]
    # TOOSHORT: only 10 days -- must be skipped, not crash the batch.
    short_history = [{'close': 100.0 + i, 'volume': 1000} for i in range(10)]
    # ENOUGH: 250 days of gently rising prices -- comfortably above every
    # window this task cares about verifying gets exercised end-to-end.
    long_history = [{'close': 100.0 + i * 0.1, 'volume': 1000 + i} for i in range(250)]

    db = FakeIndicatorsDB(
        watchlist,
        price_rows_by_watchlist_id={1: short_history, 2: long_history},
    )

    summary = run_indicator_calculation(db)

    assert summary['watchlist_count'] == 2
    assert summary['skipped'] == 1
    assert summary['calculated'] == 1
    assert summary['failed'] == 0

    # The short-history symbol never got a row written at all.
    assert not any(key[0] == 1 for key in db.indicators)

    # The long-history symbol got a fully computed row, including the new ma_5.
    calculated_row = next(v for k, v in db.indicators.items() if k[0] == 2)
    assert calculated_row['ma_5'] is not None
    assert calculated_row['ma_21'] is not None
    assert calculated_row['ma_50'] is not None
    assert calculated_row['ma_200'] is not None
    assert calculated_row['rsi_14'] is not None
    assert calculated_row['cross_status'] in ('golden_cross', 'death_cross', 'no_clear_trend')


class FakeUniverseIndicatorsDB:
    def __init__(self, universe_rows, price_rows_by_universe_id):
        self.universe_rows = universe_rows  # each: {universe_id, symbol, exchange, watchlist_id (optional)}
        self.price_rows_by_universe_id = price_rows_by_universe_id
        self.indicators = {}  # (universe_id, calc_date) -> row dict, or (watchlist_id, ...) if dual-keyed

    def execute(self, sql, params=None):
        params = params or ()
        normalized = ' '.join(sql.split())

        if normalized.startswith('SELECT u.id AS universe_id, u.symbol, u.exchange, w.id AS watchlist_id'):
            return FakeCursor(self.universe_rows)

        if normalized.startswith('SELECT close, volume FROM stock_daily_data WHERE universe_id=?'):
            (universe_id,) = params
            return FakeCursor(self.price_rows_by_universe_id.get(universe_id, []))

        if normalized.startswith('INSERT INTO stock_indicators (watchlist_id, universe_id, calc_date'):
            (watchlist_id, universe_id, calc_date, ma5, ma21, ma50, ma200, rsi14,
             volume_avg_20d, volume_trend, cross_status) = params
            self.indicators[(universe_id, calc_date)] = {
                'watchlist_id': watchlist_id, 'cross_status': cross_status, 'ma_5': ma5,
            }
            return FakeCursor([])

        if normalized.startswith('INSERT INTO stock_indicators (universe_id, calc_date'):
            (universe_id, calc_date, ma5, ma21, ma50, ma200, rsi14,
             volume_avg_20d, volume_trend, cross_status) = params
            self.indicators[(universe_id, calc_date)] = {
                'watchlist_id': None, 'cross_status': cross_status, 'ma_5': ma5,
            }
            return FakeCursor([])

        raise AssertionError(f'Unexpected SQL in test: {sql}')

    def commit(self):
        pass


def test_universe_calculation_covers_companies_not_on_the_watchlist():
    universe = [
        {'universe_id': 1, 'symbol': 'ONWATCHLIST', 'exchange': 'NSE', 'watchlist_id': 10},
        {'universe_id': 2, 'symbol': 'NOTWATCHLISTED', 'exchange': 'BSE', 'watchlist_id': None},
    ]
    history = [{'close': 100.0 + i * 0.1, 'volume': 1000 + i} for i in range(250)]
    db = FakeUniverseIndicatorsDB(universe, price_rows_by_universe_id={1: history, 2: history})

    summary = run_indicator_calculation_universe(db)

    assert summary['universe_count'] == 2
    assert summary['calculated'] == 2
    on_watchlist = next(v for k, v in db.indicators.items() if k[0] == 1)
    not_watchlisted = next(v for k, v in db.indicators.items() if k[0] == 2)
    assert on_watchlist['watchlist_id'] == 10
    assert not_watchlisted['watchlist_id'] is None
    assert on_watchlist['ma_5'] is not None


def test_universe_calculation_skips_thin_history_without_crashing():
    universe = [{'universe_id': 1, 'symbol': 'THIN', 'exchange': 'NSE', 'watchlist_id': None}]
    db = FakeUniverseIndicatorsDB(universe, price_rows_by_universe_id={1: [{'close': 10.0, 'volume': 100}]})

    summary = run_indicator_calculation_universe(db)

    assert summary['calculated'] == 0
    assert summary['skipped'] == 1
    assert db.indicators == {}
