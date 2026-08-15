from pathlib import Path
from unittest.mock import MagicMock, patch

from utils.fundamentals_ingestion import sync_fundamentals
from utils.screener_client import ScreenerParseError, fetch_fundamentals

FIXTURE_PATH = Path(__file__).resolve().parent / 'fixtures' / 'screener_sample.html'


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeDB:
    def __init__(self, watchlist_rows):
        self.watchlist_rows = watchlist_rows
        self.fundamentals = {}  # (watchlist_id, snapshot_date) -> row dict

    def execute(self, sql, params=None):
        params = params or ()
        normalized = ' '.join(sql.split())

        if normalized.startswith('SELECT id, symbol, exchange FROM stock_watchlist'):
            rows = [r for r in self.watchlist_rows if r['is_active'] == 1]
            return FakeCursor(rows)

        if normalized.startswith('INSERT INTO stock_fundamentals'):
            (watchlist_id, snapshot_date, pe_ratio, peg_ratio, eps,
             market_cap, roe, debt_to_equity, earnings_growth_pct) = params
            self.fundamentals[(watchlist_id, snapshot_date)] = {
                'pe_ratio': pe_ratio, 'peg_ratio': peg_ratio, 'eps': eps,
                'market_cap': market_cap, 'roe': roe,
                'debt_to_equity': debt_to_equity,
                'earnings_growth_pct': earnings_growth_pct,
            }
            return FakeCursor([])

        raise AssertionError(f'Unexpected SQL in test: {sql}')

    def commit(self):
        pass


def test_parser_extracts_pe_eps_roe_debt_to_equity_from_sample_page():
    html = FIXTURE_PATH.read_text(encoding='utf-8')
    fake_response = MagicMock(status_code=200, text=html)

    with patch('utils.screener_client.requests.get', return_value=fake_response), \
         patch('utils.screener_client.time.sleep'):
        data = fetch_fundamentals('TESTCO')

    assert data['pe_ratio'] == 28.4
    assert data['eps'] == 42.1
    assert data['roe'] == 18.2
    assert data['debt_to_equity'] == 0.35
    assert data['market_cap'] == 45230.0
    assert data['earnings_growth_pct'] == 22.0


def test_fetch_fundamentals_raises_on_404_instead_of_returning_garbage():
    fake_response = MagicMock(status_code=404, text='Not Found')

    with patch('utils.screener_client.requests.get', return_value=fake_response), \
         patch('utils.screener_client.time.sleep'):
        try:
            fetch_fundamentals('NOSUCHSYMBOL')
            assert False, 'expected ScreenerParseError'
        except ScreenerParseError:
            pass


def test_sync_fundamentals_skips_one_parse_failure_without_stopping_batch():
    watchlist = [
        {'id': 1, 'symbol': 'BADCO', 'exchange': 'NSE', 'is_active': 1},
        {'id': 2, 'symbol': 'GOODCO', 'exchange': 'NSE', 'is_active': 1},
    ]
    db = FakeDB(watchlist)

    good_data = {
        'pe_ratio': 20.0, 'eps': 10.0, 'roe': 15.0,
        'debt_to_equity': 0.5, 'market_cap': 1000.0,
        'earnings_growth_pct': 10.0,
    }

    fetch_fn = MagicMock(side_effect=[
        ScreenerParseError('Screener has no page for BADCO (404)'),
        good_data,
    ])

    summary = sync_fundamentals(db, fetch_fn=fetch_fn)

    assert summary['watchlist_count'] == 2
    assert summary['inserted'] == 1
    assert summary['failed'] == 1
    assert summary['failures'][0]['symbol'] == 'BADCO'
    assert len(db.fundamentals) == 1
