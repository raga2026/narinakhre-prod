from datetime import date
from unittest.mock import MagicMock

from utils.stock_ingestion import sync_daily_data


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeDB:
    """Minimal stand-in for app.py's SupabaseDB, just enough to run the
    exact SQL sync_daily_data() issues. daily_data is keyed by
    (watchlist_id, trade_date) so a second INSERT for the same key overwrites
    in place instead of adding a row -- the same guarantee ON CONFLICT DO
    UPDATE gives against the real Postgres table."""

    def __init__(self, watchlist_rows):
        self.watchlist_rows = watchlist_rows
        self.daily_data = {}

    def execute(self, sql, params=None):
        params = params or ()
        normalized = ' '.join(sql.split())

        if normalized.startswith('SELECT id, symbol, exchange FROM stock_watchlist'):
            rows = [r for r in self.watchlist_rows if r['is_active'] == 1]
            return FakeCursor(rows)

        if normalized.startswith('SELECT MAX(trade_date) AS last_date FROM stock_daily_data'):
            watchlist_id = params[0]
            dates = [d for (wid, d) in self.daily_data if wid == watchlist_id]
            return FakeCursor([{'last_date': max(dates) if dates else None}])

        if normalized.startswith('INSERT INTO stock_daily_data'):
            watchlist_id, trade_date, o, h, l, c, v = params
            self.daily_data[(watchlist_id, trade_date)] = {
                'open': o, 'high': h, 'low': l, 'close': c, 'volume': v,
            }
            return FakeCursor([])

        raise AssertionError(f'Unexpected SQL in test: {sql}')

    def commit(self):
        pass


def test_sync_daily_data_upserts_without_duplicating_rows():
    watchlist = [{'id': 1, 'symbol': 'RELIANCE', 'exchange': 'NSE', 'is_active': 1}]
    db = FakeDB(watchlist)

    candle_date = date(2026, 8, 10)
    mock_kite = MagicMock()
    mock_kite.fetch_daily_candles.return_value = [
        {'trade_date': candle_date, 'open': 100.0, 'high': 105.0, 'low': 99.0, 'close': 102.0, 'volume': 123456},
    ]

    first = sync_daily_data(db, kite_client=mock_kite)
    assert first['inserted'] == 1
    assert first['failed'] == 0
    assert len(db.daily_data) == 1

    # Second run against the same (watchlist_id, trade_date): the mock still
    # returns a candle for the same day (e.g. a re-trigger later the same
    # day, or a corrected close price). It must update in place, not add a
    # second row.
    mock_kite.fetch_daily_candles.return_value = [
        {'trade_date': candle_date, 'open': 100.0, 'high': 106.0, 'low': 99.0, 'close': 104.5, 'volume': 130000},
    ]
    second = sync_daily_data(db, kite_client=mock_kite)

    assert second['inserted'] == 1
    assert second['failed'] == 0
    assert len(db.daily_data) == 1
    assert db.daily_data[(1, candle_date.isoformat())]['close'] == 104.5


def test_sync_daily_data_records_per_symbol_failure_without_stopping_batch():
    watchlist = [
        {'id': 1, 'symbol': 'RELIANCE', 'exchange': 'NSE', 'is_active': 1},
        {'id': 2, 'symbol': 'TCS', 'exchange': 'NSE', 'is_active': 1},
    ]
    db = FakeDB(watchlist)

    candle_date = date(2026, 8, 10)
    good_candles = [
        {'trade_date': candle_date, 'open': 1.0, 'high': 2.0, 'low': 0.5, 'close': 1.5, 'volume': 1000},
    ]

    mock_kite = MagicMock()
    mock_kite.fetch_daily_candles.side_effect = [
        RuntimeError('Kite API error for RELIANCE'),
        good_candles,
    ]

    summary = sync_daily_data(db, kite_client=mock_kite)

    assert summary['watchlist_count'] == 2
    assert summary['inserted'] == 1
    assert summary['failed'] == 1
    assert summary['failures'][0]['symbol'] == 'RELIANCE'
    assert len(db.daily_data) == 1
