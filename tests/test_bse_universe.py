from pathlib import Path

from stoqbell.utils.stock_universe import parse_bse_equity_json

# A real snapshot of BSE's scrip-master API response, fetched while building
# this -- not synthetic -- so the "thousands of rows" sanity check reflects
# the real source's actual shape.
FIXTURE_PATH = Path(__file__).resolve().parent / 'fixtures' / 'bse_equity_sample.json'


def test_parsing_real_bse_json_yields_a_reasonable_row_count():
    json_text = FIXTURE_PATH.read_text(encoding='utf-8')

    rows = parse_bse_equity_json(json_text)

    assert 1000 < len(rows) < 20000


def test_bse_rows_get_exchange_bse():
    json_text = FIXTURE_PATH.read_text(encoding='utf-8')

    rows = parse_bse_equity_json(json_text)

    assert len(rows) > 0
    assert all(r['exchange'] == 'BSE' for r in rows)


def test_parsed_rows_have_the_expected_shape():
    json_text = FIXTURE_PATH.read_text(encoding='utf-8')

    rows = parse_bse_equity_json(json_text)
    first = rows[0]

    assert first['symbol']
    assert first['company_name']
    assert first['isin'].startswith('INE') or first['isin'] == ''


def test_spot_check_company_name_with_special_characters_parses_correctly():
    json_text = FIXTURE_PATH.read_text(encoding='utf-8')

    rows = parse_bse_equity_json(json_text)
    special_names = [r for r in rows if "'" in r['company_name'] or '&' in r['company_name']]

    assert len(special_names) > 0, 'expected at least one real company name with an apostrophe or ampersand in the fixture'
    for row in special_names[:5]:
        assert row['symbol']
        assert row['company_name'].strip() == row['company_name']  # no stray whitespace from parsing


def test_parser_skips_inactive_and_non_equity_rows():
    json_text = (
        '[{"SCRIP_CD":"500001","Scrip_Name":"Active Equity Co","Status":"Active",'
        '"Segment":"Equity","ISIN_NUMBER":"INE000000001","Mktcap":"1000.50"},'
        '{"SCRIP_CD":"500002","Scrip_Name":"Suspended Co","Status":"Suspended",'
        '"Segment":"Equity","ISIN_NUMBER":"INE000000002","Mktcap":"500.00"},'
        '{"SCRIP_CD":"500003","Scrip_Name":"Debt Instrument Co","Status":"Active",'
        '"Segment":"Debt","ISIN_NUMBER":"INE000000003","Mktcap":null},'
        '{"SCRIP_CD":"","Scrip_Name":"No Scrip Code Co","Status":"Active",'
        '"Segment":"Equity","ISIN_NUMBER":"INE000000004","Mktcap":"200.00"}]'
    )

    rows = parse_bse_equity_json(json_text)

    assert len(rows) == 1
    assert rows[0]['symbol'] == '500001'
    assert rows[0]['market_cap'] == 1000.50


def test_parser_handles_missing_market_cap_gracefully():
    json_text = (
        '[{"SCRIP_CD":"500001","Scrip_Name":"No Mktcap Co","Status":"Active",'
        '"Segment":"Equity","ISIN_NUMBER":"INE000000001","Mktcap":null}]'
    )

    rows = parse_bse_equity_json(json_text)

    assert len(rows) == 1
    assert rows[0]['market_cap'] is None
