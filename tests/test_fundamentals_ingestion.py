from pathlib import Path
from unittest.mock import MagicMock, patch

from utils.fundamentals_ingestion import FUNDAMENTALS_COLUMNS, sync_fundamentals_rotation
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
    """Minimal stand-in for the exact SQL sync_fundamentals_rotation()
    issues -- rotation is the only Screener.in scraping this app does now
    (see ROTATION_BATCH_SIZE's comment), so this is the sole coverage for
    the scraping loop's failure handling."""

    def __init__(self, universe_rows):
        self.universe_rows = universe_rows
        self.fundamentals = {}  # (universe_id, snapshot_date) -> row dict
        self.last_fetch_stamped = set()  # universe_ids that got last_fundamentals_fetch updated
        self.industry_stamped = {}  # universe_id -> industry value written

    def execute(self, sql, params=None):
        params = params or ()
        normalized = ' '.join(sql.split())

        if normalized.startswith('SELECT u.id AS universe_id, u.symbol, u.exchange, w.id AS watchlist_id'):
            return FakeCursor(self.universe_rows)

        if normalized.startswith('INSERT INTO stock_fundamentals'):
            # universe_id-only path (no watchlist_id in these fixtures), so
            # params are (universe_id, snapshot_date, *FUNDAMENTALS_COLUMNS
            # values) -- see _upsert_fundamentals_snapshot().
            universe_id, snapshot_date = params[:2]
            data_values = params[2:]
            self.fundamentals[(universe_id, snapshot_date)] = dict(zip(FUNDAMENTALS_COLUMNS, data_values))
            return FakeCursor([])

        if normalized.startswith('UPDATE stock_universe SET last_fundamentals_fetch'):
            industry, universe_id = params
            self.last_fetch_stamped.add(universe_id)
            self.industry_stamped[universe_id] = industry
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
    assert data['price_to_book'] == 4.0
    assert data['industry'] == 'Refineries & Marketing'


def test_parser_falls_back_to_broader_classification_when_industry_missing():
    import re
    html = FIXTURE_PATH.read_text(encoding='utf-8')
    # Drop the two most granular breadcrumb <a> levels, keep Sector/Broad Sector.
    trimmed = re.sub(r'<a [^>]*title="(Broad Industry|Industry)"[^>]*>[^<]*</a>', '', html)
    fake_response = MagicMock(status_code=200, text=trimmed)

    with patch('utils.screener_client.requests.get', return_value=fake_response), \
         patch('utils.screener_client.time.sleep'):
        data = fetch_fundamentals('TESTCO')

    assert data['industry'] == 'Oil, Gas & Consumable Fuels'  # Sector, since Industry/Broad Industry are gone


def test_parser_industry_is_none_when_breadcrumb_entirely_absent():
    html = '<html><body><ul id="top-ratios"><li class="flex flex-space-between"><span class="name">Stock P/E</span><span class="nowrap value"><span class="number">10</span></span></li></ul></body></html>'
    fake_response = MagicMock(status_code=200, text=html)

    with patch('utils.screener_client.requests.get', return_value=fake_response), \
         patch('utils.screener_client.time.sleep'):
        data = fetch_fundamentals('TESTCO')

    assert data['industry'] is None


def test_fetch_fundamentals_raises_on_404_instead_of_returning_garbage():
    fake_response = MagicMock(status_code=404, text='Not Found')

    with patch('utils.screener_client.requests.get', return_value=fake_response), \
         patch('utils.screener_client.time.sleep'):
        try:
            fetch_fundamentals('NOSUCHSYMBOL')
            assert False, 'expected ScreenerParseError'
        except ScreenerParseError:
            pass


def test_sync_fundamentals_rotation_skips_one_parse_failure_without_stopping_batch():
    universe = [
        {'universe_id': 1, 'symbol': 'BADCO', 'exchange': 'NSE', 'watchlist_id': None},
        {'universe_id': 2, 'symbol': 'GOODCO', 'exchange': 'NSE', 'watchlist_id': None},
    ]
    db = FakeDB(universe)

    good_data = {
        'pe_ratio': 20.0, 'eps': 10.0, 'roe': 15.0,
        'debt_to_equity': 0.5, 'market_cap': 1000.0,
        'earnings_growth_pct': 10.0, 'industry': 'Refineries & Marketing',
    }

    fetch_fn = MagicMock(side_effect=[
        ScreenerParseError('Screener has no page for BADCO (404)'),
        good_data,
    ])

    summary = sync_fundamentals_rotation(db, fetch_fn=fetch_fn)

    assert summary['batch_size'] == 2
    assert summary['scraped'] == 1
    assert summary['failed'] == 1
    assert summary['failures'][0]['symbol'] == 'BADCO'
    assert len(db.fundamentals) == 1
    # The failed symbol's last_fundamentals_fetch must NOT be stamped, so
    # it stays near the front of the next run's stalest-first queue.
    assert db.last_fetch_stamped == {2}
    # Scraped industry gets stamped onto stock_universe alongside the fetch
    # timestamp, feeding fundamental_screen.py's industry-relative PE/P-B.
    assert db.industry_stamped[2] == 'Refineries & Marketing'
