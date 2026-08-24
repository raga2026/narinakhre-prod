from pathlib import Path

from stoqbell.utils.fundamentals_ingestion import STOCK_FUNDAMENTALS_ALTER_SQL
from stoqbell.utils.stock_universe import parse_nse_equity_csv, sync_live_prices
import stoqbell.utils.stock_universe as stock_universe_module

# A real snapshot of NSE's official EQUITY_L.csv, fetched while building
# this -- not a synthetic fixture -- so the "thousands of rows" sanity
# check reflects the real file's actual shape, not a guess at it.
FIXTURE_PATH = Path(__file__).resolve().parent / 'fixtures' / 'nse_equity_sample.csv'

NEW_NULLABLE_FUNDAMENTALS_COLUMNS = [
    'sector_avg_pe', 'price_to_book', 'opm_pct', 'roce_pct', 'roa_pct',
    'current_ratio', 'tol_by_tnw', 'promoter_holding_pct', 'fii_holding_pct',
    'public_holding_pct', 'quarterly_profit_growth_pct',
    'quarterly_revenue_growth_pct', 'free_cash_flow',
]


def test_parsing_real_nse_csv_yields_a_reasonable_row_count():
    csv_text = FIXTURE_PATH.read_text(encoding='utf-8')

    rows = parse_nse_equity_csv(csv_text)

    # Sanity range, not an exact count -- NSE's list changes over time as
    # companies list/delist. Thousands, not zero and not absurdly high.
    assert 1000 < len(rows) < 10000


def test_parsed_rows_have_the_expected_shape():
    csv_text = FIXTURE_PATH.read_text(encoding='utf-8')

    rows = parse_nse_equity_csv(csv_text)
    first = rows[0]

    assert first['exchange'] == 'NSE'
    assert first['symbol']
    assert first['company_name']
    assert first['isin'].startswith('INE') or first['isin'] == ''


def test_parser_handles_apostrophes_in_company_names():
    csv_text = (
        'SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE\n'
        "DRREDDY,Dr. Reddy's Laboratories Limited,EQ,01-JAN-1995,5,1,INE089A01023,5\n"
    )

    rows = parse_nse_equity_csv(csv_text)

    assert len(rows) == 1
    assert rows[0]['company_name'] == "Dr. Reddy's Laboratories Limited"
    assert rows[0]['symbol'] == 'DRREDDY'


def test_parser_skips_rows_with_no_symbol():
    csv_text = (
        'SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE\n'
        ',Some Blank Row,EQ,01-JAN-1995,5,1,INE000000000,5\n'
        'REALCO,Real Company Limited,EQ,01-JAN-1995,5,1,INE000000001,5\n'
    )

    rows = parse_nse_equity_csv(csv_text)

    assert len(rows) == 1
    assert rows[0]['symbol'] == 'REALCO'


def test_new_fundamentals_columns_are_added_as_nullable():
    alter_sql_text = ' '.join(STOCK_FUNDAMENTALS_ALTER_SQL)

    for column in NEW_NULLABLE_FUNDAMENTALS_COLUMNS:
        assert f'ADD COLUMN IF NOT EXISTS {column} ' in alter_sql_text, \
            f'{column} missing from the additive migration'
        # None of these should be declared NOT NULL -- they populate gradually.
        column_statement = next(s for s in STOCK_FUNDAMENTALS_ALTER_SQL if f' {column} ' in s)
        assert 'NOT NULL' not in column_statement.upper()


# --- sync_live_prices -------------------------------------------------------

class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeLivePriceDB:
    """Minimal stand-in -- just enough to run sync_live_prices' own SELECT
    and capture whatever UPDATE it issues, without needing a real Postgres
    UPDATE...FROM (VALUES ...) to actually execute (same "no fake DB for
    the actual bulk-SQL orchestration functions in this file" gap
    propagate_bse_market_cap_to_nse/rebucket_market_cap_bands already
    have -- this covers the Python-side chunking/mapping logic around it,
    not the SQL engine's own semantics)."""

    def __init__(self, universe_rows):
        self.universe_rows = universe_rows
        self.executed_sql = []

    def execute(self, sql, params=None):
        normalized = ' '.join(sql.split())
        self.executed_sql.append(normalized)
        if normalized.startswith('SELECT id, symbol, exchange FROM stock_universe WHERE is_scrape_eligible'):
            return FakeCursor(self.universe_rows)
        return FakeCursor([])

    def commit(self):
        pass


class FakeLtpKiteClient:
    """quotes: {'EXCHANGE:SYMBOL': price} -- a key simply absent models a
    symbol Kite doesn't recognize (see fetch_ltp_batch's own tolerance)."""

    def __init__(self, quotes):
        self.quotes = quotes
        self.calls = []

    def fetch_ltp_batch(self, instrument_keys):
        self.calls.append(list(instrument_keys))
        return {k: self.quotes[k] for k in instrument_keys if k in self.quotes}


def test_sync_live_prices_updates_every_matched_row():
    db = FakeLivePriceDB(universe_rows=[
        {'id': 1, 'symbol': 'AAA', 'exchange': 'NSE'},
        {'id': 2, 'symbol': 'BBB', 'exchange': 'NSE'},
    ])
    kite = FakeLtpKiteClient({'NSE:AAA': 101.5, 'NSE:BBB': 202.75})

    summary = sync_live_prices(db, kite_client=kite)

    assert summary == {'checked': 2, 'updated': 2}
    update_sql = next(s for s in db.executed_sql if s.startswith('UPDATE stock_universe'))
    assert '(1, 101.5)' in update_sql
    assert '(2, 202.75)' in update_sql


def test_sync_live_prices_tolerates_a_symbol_kite_does_not_recognize():
    # A BSE listing whose stored symbol doesn't match Kite's own
    # tradingsymbol -- known gap, see fetch_daily_candles' own comment.
    # Should not fail the whole sync, just skip that one row.
    db = FakeLivePriceDB(universe_rows=[
        {'id': 1, 'symbol': 'AAA', 'exchange': 'NSE'},
        {'id': 2, 'symbol': 'UNKNOWN', 'exchange': 'BSE'},
    ])
    kite = FakeLtpKiteClient({'NSE:AAA': 101.5})

    summary = sync_live_prices(db, kite_client=kite)

    assert summary == {'checked': 2, 'updated': 1}
    update_sql = next(s for s in db.executed_sql if s.startswith('UPDATE stock_universe'))
    assert '(1, 101.5)' in update_sql
    assert '2,' not in update_sql


def test_sync_live_prices_chunks_the_ltp_calls(monkeypatch):
    monkeypatch.setattr(stock_universe_module, 'LIVE_PRICE_LTP_CHUNK_SIZE', 2)
    db = FakeLivePriceDB(universe_rows=[
        {'id': i, 'symbol': f'SYM{i}', 'exchange': 'NSE'} for i in range(1, 6)
    ])
    kite = FakeLtpKiteClient({f'NSE:SYM{i}': float(i) for i in range(1, 6)})

    summary = sync_live_prices(db, kite_client=kite)

    assert summary == {'checked': 5, 'updated': 5}
    # 5 instruments at a chunk size of 2 -> 3 calls (2, 2, 1), never all at once.
    assert len(kite.calls) == 3
    assert all(len(call) <= 2 for call in kite.calls)


def test_sync_live_prices_returns_early_with_no_universe_rows():
    db = FakeLivePriceDB(universe_rows=[])
    kite = FakeLtpKiteClient({})

    summary = sync_live_prices(db, kite_client=kite)

    assert summary == {'checked': 0, 'updated': 0}
    assert kite.calls == []


def test_sync_live_prices_issues_no_update_when_nothing_matched():
    db = FakeLivePriceDB(universe_rows=[{'id': 1, 'symbol': 'AAA', 'exchange': 'NSE'}])
    kite = FakeLtpKiteClient({})  # Kite has no quote for anything

    summary = sync_live_prices(db, kite_client=kite)

    assert summary == {'checked': 1, 'updated': 0}
    assert not any(s.startswith('UPDATE stock_universe') for s in db.executed_sql)
