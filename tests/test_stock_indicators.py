from utils.stock_indicators import run_indicator_calculation


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

        if normalized.startswith('INSERT INTO stock_indicators'):
            (watchlist_id, calc_date, ma21, ma50, ma200, rsi14,
             volume_avg_20d, volume_trend, cross_status) = params
            self.indicators[(watchlist_id, calc_date)] = {
                'ma_21': ma21, 'ma_50': ma50, 'ma_200': ma200, 'rsi_14': rsi14,
                'volume_avg_20d': volume_avg_20d, 'volume_trend': volume_trend,
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

    # The long-history symbol got a fully computed row.
    calculated_row = next(v for k, v in db.indicators.items() if k[0] == 2)
    assert calculated_row['ma_21'] is not None
    assert calculated_row['ma_50'] is not None
    assert calculated_row['ma_200'] is not None
    assert calculated_row['rsi_14'] is not None
    assert calculated_row['cross_status'] in ('golden_cross', 'death_cross', 'no_clear_trend')
