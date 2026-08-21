from pathlib import Path

from stoqbell.utils.fundamentals_ingestion import STOCK_FUNDAMENTALS_ALTER_SQL
from stoqbell.utils.stock_universe import parse_nse_equity_csv

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
