from datetime import date, timedelta
from unittest.mock import MagicMock

from utils.stock_ingestion import (
    BACKFILL_DAYS,
    MIN_HISTORY_DAYS_BEFORE_INCREMENTAL_SYNC,
    sync_daily_data,
    sync_daily_data_universe,
)
from utils import job_progress


def teardown_function(_fn):
    job_progress.clear()


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

    def __init__(self, watchlist_rows, instrument_map=None, existing_daily_data=None):
        self.watchlist_rows = watchlist_rows
        self.daily_data = dict(existing_daily_data or {})
        self.instrument_map = instrument_map or {}  # {(symbol, exchange): token}

    def execute(self, sql, params=None):
        params = params or ()
        normalized = ' '.join(sql.split())

        if normalized.startswith('SELECT id, symbol, exchange FROM stock_watchlist'):
            rows = [r for r in self.watchlist_rows if r['is_active'] == 1]
            return FakeCursor(rows)

        if normalized.startswith('SELECT MAX(trade_date) AS last_date, COUNT(*) AS row_count FROM stock_daily_data'):
            watchlist_id = params[0]
            dates = [d for (wid, d) in self.daily_data if wid == watchlist_id]
            return FakeCursor([{'last_date': max(dates) if dates else None, 'row_count': len(dates)}])

        if normalized.startswith('SELECT kite_instrument_token FROM stock_kite_instrument_map'):
            symbol, exchange = params
            token = self.instrument_map.get((symbol, exchange))
            return FakeCursor([{'kite_instrument_token': token}] if token else [])

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


def test_sync_daily_data_reports_progress_per_symbol():
    watchlist = [
        {'id': 1, 'symbol': 'RELIANCE', 'exchange': 'NSE', 'is_active': 1},
        {'id': 2, 'symbol': 'TCS', 'exchange': 'NSE', 'is_active': 1},
    ]
    db = FakeDB(watchlist)
    mock_kite = MagicMock()
    mock_kite.fetch_daily_candles.return_value = []

    reports = []
    job_progress.bind(lambda current, total, label: reports.append((current, total, label)))

    sync_daily_data(db, kite_client=mock_kite)

    assert reports == [(1, 2, None), (2, 2, None)]


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


def test_symbol_returning_no_candles_is_tracked_separately_from_failures():
    # Kite can respond successfully with an empty candle list (no exception)
    # -- that must not be silently indistinguishable from "already synced".
    watchlist = [{'id': 1, 'symbol': 'THINLYTRADED', 'exchange': 'BSE', 'is_active': 1}]
    db = FakeDB(watchlist)

    mock_kite = MagicMock()
    mock_kite.fetch_daily_candles.return_value = []

    summary = sync_daily_data(db, kite_client=mock_kite)

    expected_from_date = (date.today() - timedelta(days=BACKFILL_DAYS)).isoformat()
    assert summary['inserted'] == 0
    assert summary['failed'] == 0
    assert summary['zero_candles'] == [{
        'symbol': 'THINLYTRADED', 'exchange': 'BSE',
        'from_date': expected_from_date,
        'to_date': date.today().isoformat(),
    }]


def test_cached_kite_instrument_token_is_passed_through_to_the_client():
    # This is the whole point of the mapping table: a symbol matched by
    # utils/kite_instrument_map.py must reach KiteClient.fetch_daily_candles
    # as instrument_token=, not force it to fall back to ltp().
    watchlist = [{'id': 1, 'symbol': '532835', 'exchange': 'BSE', 'is_active': 1}]
    db = FakeDB(watchlist, instrument_map={('532835', 'BSE'): 99999})

    mock_kite = MagicMock()
    mock_kite.fetch_daily_candles.return_value = []

    sync_daily_data(db, kite_client=mock_kite)

    _, kwargs = mock_kite.fetch_daily_candles.call_args
    assert kwargs['instrument_token'] == 99999


def test_unmapped_symbol_passes_none_instrument_token():
    watchlist = [{'id': 1, 'symbol': 'RELIANCE', 'exchange': 'NSE', 'is_active': 1}]
    db = FakeDB(watchlist)  # no instrument_map entries

    mock_kite = MagicMock()
    mock_kite.fetch_daily_candles.return_value = []

    sync_daily_data(db, kite_client=mock_kite)

    _, kwargs = mock_kite.fetch_daily_candles.call_args
    assert kwargs['instrument_token'] is None


def test_brand_new_symbol_backfills_the_full_window():
    watchlist = [{'id': 1, 'symbol': 'NEWCO', 'exchange': 'NSE', 'is_active': 1}]
    db = FakeDB(watchlist)  # no existing rows at all
    mock_kite = MagicMock()
    mock_kite.fetch_daily_candles.return_value = []

    sync_daily_data(db, kite_client=mock_kite)

    args, _ = mock_kite.fetch_daily_candles.call_args
    from_date_requested = args[2]
    assert from_date_requested == date.today() - timedelta(days=BACKFILL_DAYS)


def test_symbol_with_thin_history_gets_backfilled_again_not_just_one_day():
    # This is the exact bug found live: a symbol synced back when
    # BACKFILL_DAYS was 30 has ~30 rows -- nowhere near enough for a
    # 200-day moving average -- and last_date+1 would only ever add one
    # day per run, never catching up. Must re-request the full window.
    watchlist = [{'id': 1, 'symbol': 'THIN', 'exchange': 'NSE', 'is_active': 1}]
    existing = {(1, (date.today() - timedelta(days=i)).isoformat()): {} for i in range(1, 31)}
    db = FakeDB(watchlist, existing_daily_data=existing)
    assert len(db.daily_data) < MIN_HISTORY_DAYS_BEFORE_INCREMENTAL_SYNC

    mock_kite = MagicMock()
    mock_kite.fetch_daily_candles.return_value = []

    sync_daily_data(db, kite_client=mock_kite)

    args, _ = mock_kite.fetch_daily_candles.call_args
    from_date_requested = args[2]
    assert from_date_requested == date.today() - timedelta(days=BACKFILL_DAYS)


def test_symbol_with_enough_history_only_asks_for_days_since_last_sync():
    watchlist = [{'id': 1, 'symbol': 'ESTABLISHED', 'exchange': 'NSE', 'is_active': 1}]
    # Most recent existing row is yesterday (i=1) -- from_date should be
    # last_date + 1 day = today, not a full re-backfill.
    existing = {
        (1, (date.today() - timedelta(days=i)).isoformat()): {}
        for i in range(1, MIN_HISTORY_DAYS_BEFORE_INCREMENTAL_SYNC + 20)
    }
    db = FakeDB(watchlist, existing_daily_data=existing)
    assert len(db.daily_data) >= MIN_HISTORY_DAYS_BEFORE_INCREMENTAL_SYNC

    mock_kite = MagicMock()
    mock_kite.fetch_daily_candles.return_value = []

    sync_daily_data(db, kite_client=mock_kite)

    args, _ = mock_kite.fetch_daily_candles.call_args
    from_date_requested = args[2]
    assert from_date_requested == date.today()


class FakeUniverseDB:
    """Minimal stand-in for app.py's SupabaseDB, just enough to run the
    exact SQL sync_daily_data_universe() issues. daily_data is keyed by
    (universe_id, trade_date) or (watchlist_id, trade_date) matching
    whichever identity the real ON CONFLICT target would be."""

    def __init__(self, universe_rows, instrument_map=None, existing_daily_data=None):
        self.universe_rows = universe_rows  # each: {universe_id, symbol, exchange, watchlist_id (optional)}
        self.daily_data = dict(existing_daily_data or {})
        self.instrument_map = instrument_map or {}

    def execute(self, sql, params=None):
        params = params or ()
        normalized = ' '.join(sql.split())

        if normalized.startswith('SELECT u.id AS universe_id, u.symbol, u.exchange, w.id AS watchlist_id'):
            return FakeCursor(self.universe_rows)

        if normalized.startswith('SELECT MAX(trade_date) AS last_date, COUNT(*) AS row_count FROM stock_daily_data WHERE universe_id=?'):
            universe_id = params[0]
            dates = [d for (uid, d) in self.daily_data if uid == universe_id]
            return FakeCursor([{'last_date': max(dates) if dates else None, 'row_count': len(dates)}])

        if normalized.startswith('SELECT kite_instrument_token FROM stock_kite_instrument_map'):
            symbol, exchange = params
            token = self.instrument_map.get((symbol, exchange))
            return FakeCursor([{'kite_instrument_token': token}] if token else [])

        if normalized.startswith('INSERT INTO stock_daily_data (watchlist_id, universe_id, trade_date'):
            watchlist_id, universe_id, trade_date, o, h, l, c, v = params
            self.daily_data[(universe_id, trade_date)] = {
                'watchlist_id': watchlist_id, 'open': o, 'high': h, 'low': l, 'close': c, 'volume': v,
            }
            return FakeCursor([])

        if normalized.startswith('INSERT INTO stock_daily_data (universe_id, trade_date'):
            universe_id, trade_date, o, h, l, c, v = params
            self.daily_data[(universe_id, trade_date)] = {
                'watchlist_id': None, 'open': o, 'high': h, 'low': l, 'close': c, 'volume': v,
            }
            return FakeCursor([])

        raise AssertionError(f'Unexpected SQL in test: {sql}')

    def commit(self):
        pass


def test_sync_daily_data_universe_covers_the_full_universe_not_just_watchlist():
    universe = [
        {'universe_id': 1, 'symbol': 'RELIANCE', 'exchange': 'NSE', 'watchlist_id': 10},  # watchlisted too
        {'universe_id': 2, 'symbol': 'OBSCURE', 'exchange': 'BSE', 'watchlist_id': None},  # not watchlisted
    ]
    db = FakeUniverseDB(universe)

    candle_date = date(2026, 8, 10)
    mock_kite = MagicMock()
    mock_kite.fetch_daily_candles.return_value = [
        {'trade_date': candle_date, 'open': 1.0, 'high': 2.0, 'low': 0.5, 'close': 1.5, 'volume': 1000},
    ]

    summary = sync_daily_data_universe(db, kite_client=mock_kite, sleep_seconds=0)

    assert summary['universe_count'] == 2
    assert summary['inserted'] == 2
    # Watchlisted company's row keeps both identities stamped.
    assert db.daily_data[(1, candle_date.isoformat())]['watchlist_id'] == 10
    # Universe-only company's row has no watchlist_id at all.
    assert db.daily_data[(2, candle_date.isoformat())]['watchlist_id'] is None


def test_sync_daily_data_universe_paces_calls_with_a_sleep(monkeypatch):
    universe = [
        {'universe_id': 1, 'symbol': 'A', 'exchange': 'NSE', 'watchlist_id': None},
        {'universe_id': 2, 'symbol': 'B', 'exchange': 'NSE', 'watchlist_id': None},
    ]
    db = FakeUniverseDB(universe)
    mock_kite = MagicMock()
    mock_kite.fetch_daily_candles.return_value = []

    sleeps = []
    monkeypatch.setattr('utils.stock_ingestion.time.sleep', lambda s: sleeps.append(s))

    sync_daily_data_universe(db, kite_client=mock_kite, sleep_seconds=0.35)

    # One sleep between the two calls, not after the last one.
    assert sleeps == [0.35]


def test_sync_daily_data_universe_records_per_symbol_failure_without_stopping_batch():
    universe = [
        {'universe_id': 1, 'symbol': 'BAD', 'exchange': 'NSE', 'watchlist_id': None},
        {'universe_id': 2, 'symbol': 'GOOD', 'exchange': 'NSE', 'watchlist_id': None},
    ]
    db = FakeUniverseDB(universe)

    candle_date = date(2026, 8, 10)
    mock_kite = MagicMock()
    mock_kite.fetch_daily_candles.side_effect = [
        RuntimeError('Kite API error'),
        [{'trade_date': candle_date, 'open': 1.0, 'high': 2.0, 'low': 0.5, 'close': 1.5, 'volume': 1000}],
    ]

    summary = sync_daily_data_universe(db, kite_client=mock_kite, sleep_seconds=0)

    assert summary['inserted'] == 1
    assert summary['failed'] == 1
    assert summary['failures'][0]['symbol'] == 'BAD'
