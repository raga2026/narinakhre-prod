"""Tests for utils/market_signals.py -- fake DB matched by normalized SQL
prefix (same pattern as the other Stocks engine tests), injected fetchers,
no network."""
import random
from datetime import date

import stoqbell.utils.stock_alerting as stock_alerting
from stoqbell.utils.market_signals import (
    attach_sector_pressure,
    build_shock_alert,
    find_driver_shocks,
    get_sector_pressure_by_group,
    groups_affected_by_driver,
    sync_market_signals,
)


class FakeCursor:
    def __init__(self, rows=None):
        self._rows = rows or []

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeDB:
    def __init__(self, pressure_rows=None, watchlist_rows=None, industry_rows=None):
        self.pressure_rows = pressure_rows or []
        self.watchlist_rows = watchlist_rows or []
        self.industry_rows = industry_rows or []
        self.stored_signals = []
        self.stored_pressures = []
        self.stored_news_scopes = []
        self.alert_keys = set()

    def execute(self, sql, params=None):
        sql = ' '.join(sql.split())
        if sql.startswith('INSERT INTO market_driver_signals'):
            self.stored_signals.append(params)
        elif sql.startswith('INSERT INTO sector_news'):
            self.stored_news_scopes.append(params[0])
        elif sql.startswith('DELETE FROM sector_news'):
            pass
        elif sql.startswith('INSERT INTO sector_pressure'):
            self.stored_pressures.append(params)
        elif sql.startswith('INSERT INTO market_event_alerts'):
            self.alert_keys.add(params[0])
        elif sql.startswith('SELECT id FROM market_event_alerts'):
            return FakeCursor([{'id': 1}] if params[0] in self.alert_keys else [])
        elif sql.startswith('SELECT p.group_key'):
            return FakeCursor(self.pressure_rows)
        elif sql.startswith('SELECT s.driver_key'):
            return FakeCursor([])
        elif sql.startswith('SELECT headline, link, source, published_at FROM sector_news'):
            return FakeCursor([])
        elif sql.startswith('SELECT DISTINCT trade_date'):
            return FakeCursor([])
        elif sql.startswith('SELECT w.symbol, w.name AS company_name, u.industry'):
            return FakeCursor(self.watchlist_rows)
        else:
            raise AssertionError(f'Unexpected SQL in test: {sql}')
        return FakeCursor()

    def commit(self):
        pass


def _series(last_move, n=60, seed=3):
    rng = random.Random(seed)
    closes = [100.0]
    for _ in range(n - 1):
        closes.append(closes[-1] * (1 + rng.uniform(-0.01, 0.01)))
    closes.append(closes[-1] * (1 + last_move))
    return [(f'2026-09-{(i % 28) + 1:02d}', c) for i, c in enumerate(closes)]


def _articles(*headlines):
    return [{'headline': h, 'link': f'https://x/{i}', 'source': 'S', 'published_at': None} for i, h in enumerate(headlines)]


TODAY = date(2026, 11, 5)


def test_sync_stores_signals_news_and_pressure_and_isolates_failures():
    db = FakeDB()

    def prices(symbol):
        if symbol == 'GC=F':
            raise RuntimeError('yahoo down')
        return _series(0.05 if symbol == 'BZ=F' else 0.0)

    def news(query):
        return _articles('Auto sales slump as costs rise')

    summary = sync_market_signals(db, fetch_prices_fn=prices, fetch_news_fn=news, sleep_seconds=0,
                                  send_alerts=False, today=TODAY)
    assert summary['drivers_synced'] == 6
    assert any(f['scope'] == 'driver:gold' for f in summary['failures'])
    assert summary['groups_scored'] == len(db.stored_pressures) > 0
    assert 'auto' in db.stored_news_scopes
    pressures = {p[0]: p for p in db.stored_pressures}
    assert pressures['paints'][2] < 0   # crude spike -> paints under pressure
    assert pressures['oil_gas'][2] > 0  # ... and oil producers helped


def test_find_driver_shocks_orders_by_magnitude_and_skips_unknown():
    signals = {'brent': {'zscore': 3.1}, 'usdinr': {'zscore': -2.4}, 'gold': {'zscore': 0.5}, 'bogus': {'zscore': 9}}
    assert [k for k, _ in find_driver_shocks(signals)] == ['brent', 'usdinr']


def test_groups_affected_by_driver_direction():
    up = {g: hurt for g, _s, hurt in groups_affected_by_driver('brent', +1)}
    assert up['paints'] is True and up['oil_gas'] is False
    down = {g: hurt for g, _s, hurt in groups_affected_by_driver('brent', -1)}
    assert down['paints'] is False and down['oil_gas'] is True


def test_build_shock_alert_lists_exposed_companies():
    signal = {'zscore': 3.4, 'change_1d_pct': 4.2}
    affected = groups_affected_by_driver('brent', +1)
    exposed = {'auto': ['Ashok Leyland'], 'paints': ['Asian Paints']}
    pressures = {'auto': {'pressure': -0.6, 'label': 'headwind'}}
    subject, body = build_shock_alert('brent', signal, affected, exposed, pressures)
    assert 'Brent crude' in subject and '+4.20%' in subject
    assert 'Ashok Leyland' in body and 'Asian Paints' in body
    assert 'suppressed' in body  # auto pressure -0.6 <= gate


def test_shock_alert_sent_once_per_day(monkeypatch):
    sent = []
    monkeypatch.setattr(stock_alerting, 'send_alert_email', lambda subject, body: sent.append(subject) or True)
    db = FakeDB(watchlist_rows=[{'symbol': 'ASHOKLEY', 'company_name': 'Ashok Leyland', 'industry': 'Automobiles - Trucks'}])

    def prices(symbol):
        return _series(0.06 if symbol == 'BZ=F' else 0.0)

    for _ in range(2):
        sync_market_signals(db, fetch_prices_fn=prices, fetch_news_fn=lambda q: [], sleep_seconds=0, today=TODAY)
    assert len(sent) == 1
    assert '2026-11-05:brent:up' in db.alert_keys


def test_failed_email_is_not_recorded_so_it_retries(monkeypatch):
    monkeypatch.setattr(stock_alerting, 'send_alert_email', lambda subject, body: False)
    db = FakeDB()
    sync_market_signals(db, fetch_prices_fn=lambda s: _series(0.06 if s == 'BZ=F' else 0.0),
                        fetch_news_fn=lambda q: [], sleep_seconds=0, today=TODAY)
    assert db.alert_keys == set()


def test_attach_sector_pressure_maps_group_and_is_fail_open():
    db = FakeDB(pressure_rows=[{'group_key': 'auto', 'as_of_date': '2026-11-05', 'pressure': '-0.62',
                                 'label': 'headwind', 'top_driver': 'brent'}])
    rows = attach_sector_pressure(db, [{'industry': 'Automobiles'}, {'industry': 'Paints'}, {'industry': None}])
    assert rows[0]['sector_group'] == 'auto' and rows[0]['sector_pressure'] == -0.62
    assert rows[1]['sector_pressure'] is None
    assert rows[2]['sector_group'] == 'general'

    class BrokenDB:
        def execute(self, *a, **k):
            raise RuntimeError('table missing')

    rows = attach_sector_pressure(BrokenDB(), [{'industry': 'Automobiles'}])
    assert rows[0]['sector_pressure'] is None


def test_get_sector_pressure_skips_null_pressure():
    db = FakeDB(pressure_rows=[{'group_key': 'it', 'as_of_date': '2026-11-05', 'pressure': None}])
    assert get_sector_pressure_by_group(db, today=TODAY) == {}


class ReportDB:
    """Fake DB for get_market_drivers_report: one driver, one sector under
    pressure, two watchlist stocks (one in the sector, one not)."""
    def execute(self, sql, params=None):
        sql = ' '.join(sql.split())
        if sql.startswith('SELECT last_success_at FROM stock_job_runs'):
            return FakeCursor([{'last_success_at': '2026-11-05T11:03:00+00:00'}])
        if sql.startswith('SELECT s.driver_key'):
            return FakeCursor([{'driver_key': 'brent', 'as_of_date': '2026-11-05', 'value': 90.0,
                                'change_1d_pct': 5.0, 'change_5d_pct': 8.0, 'zscore': 3.1}])
        if sql.startswith('SELECT p.group_key'):
            return FakeCursor([{'group_key': 'auto', 'as_of_date': date.today().isoformat(), 'pressure': -0.6,
                                'label': 'headwind', 'driver_component': -0.7, 'news_component': -0.2,
                                'move_component': -0.3, 'top_driver': 'brent', 'top_driver_impact': -0.7}])
        if sql.startswith('SELECT headline, link, source, published_at FROM sector_news'):
            return FakeCursor([{'headline': 'Crude jumps', 'link': 'x', 'source': 's', 'published_at': None}])
        if sql.startswith('SELECT w.id AS watchlist_id'):
            return FakeCursor([
                {'watchlist_id': 1, 'symbol': 'ASHOKLEY', 'company_name': 'Ashok Leyland', 'industry': 'Auto - Commercial Vehicles',
                 'live_price': 200.0, 'cross_status': 'death_cross', 'latest_close': 199.0},
                {'watchlist_id': 2, 'symbol': 'UNKNOWN', 'company_name': 'No Sector', 'industry': None,
                 'live_price': None, 'cross_status': None, 'latest_close': None},
            ])
        return FakeCursor([])


def test_report_lists_driver_sector_and_exposed_stock_with_probabilities():
    from stoqbell.utils.market_signals import get_market_drivers_report
    report = get_market_drivers_report(ReportDB())
    assert report['last_sync'].startswith('2026-11-05')
    assert report['drivers'][0]['key'] == 'brent' and report['drivers'][0]['is_shock']
    assert report['sectors'][0]['group'] == 'auto' and report['sectors'][0]['gated']
    assert [s['symbol'] for s in report['stocks']] == ['ASHOKLEY']  # no-sector stock excluded
    stock = report['stocks'][0]
    assert stock['p_decline'] > stock['p_rise'] and stock['p_rise'] + stock['p_decline'] == 100


def test_report_never_raises_when_queries_fail():
    from stoqbell.utils.market_signals import get_market_drivers_report

    class BrokenDB:
        def execute(self, *a, **k):
            raise RuntimeError('db down')
    report = get_market_drivers_report(BrokenDB())
    assert report == {'last_sync': None, 'drivers': [], 'sectors': [], 'stocks': []}
