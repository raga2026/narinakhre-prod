"""
One-time (safely re-runnable) seed script for stock_universe -- populates it
from NSE's and BSE's official bulk equity lists. Run manually:

    python seed_stock_universe.py

Does not touch stock_watchlist, stock_daily_data, or stock_fundamentals.

NSE: fetched from nsearchives.nseindia.com's official EQUITY_L.csv.
shares_outstanding and market cap aren't in that file at all -- both stay
NULL for NSE rows and need a later per-company backfill (not built here).

BSE: the URL originally used for this (bseindia.com/downloads1/List_of_companies.csv)
turned out to serve a different dataset entirely -- BSE's "GSM" regulatory
watchlist (~850 flagged companies), not the general equity universe. Fetched
instead from BSE's scrip-master API (api.bseindia.com/BseIndiaAPI/api/ListofScripData/w),
which needs a Referer header or it returns an HTML page instead of data.
That source does include a market cap figure, unlike NSE's file, so BSE rows
get last_market_cap/last_market_cap_date filled in where available.
shares_outstanding still isn't available from either source.

A company listed on both exchanges gets two separate stock_universe rows
(one per exchange) -- BSE and NSE use different symbols for the same
company, and this never tries to merge/de-duplicate them into one row.

Requires SUPABASE_URL and SUPABASE_KEY in the environment (.env is loaded
automatically, same as the rest of this project).
"""
import os
import sys

import requests
from dotenv import load_dotenv
from supabase import create_client

from utils.stock_universe import (
    initialize_stock_universe_table_if_needed,
    parse_nse_equity_csv,
    parse_bse_equity_json,
    seed_stock_universe,
    seed_bse_universe,
)

NSE_EQUITY_LIST_URL = 'https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv'
BSE_SCRIP_LIST_URL = (
    'https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w'
    '?Group=&Scripcode=&industry=&segment=Equity&status=Active'
)
BSE_REQUEST_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
    'Referer': 'https://www.bseindia.com/corporates/List_Scrips.aspx',
    'Accept': 'application/json',
}


def seed_nse(client):
    print(f'Fetching {NSE_EQUITY_LIST_URL} ...')
    response = requests.get(NSE_EQUITY_LIST_URL, headers={'User-Agent': 'Mozilla/5.0'}, timeout=30)
    response.raise_for_status()

    rows = parse_nse_equity_csv(response.text)
    print(f'Parsed {len(rows)} rows from the NSE equity list.')
    if len(rows) < 500:
        print('WARNING: NSE row count looks unexpectedly low -- verify the source before trusting this seed.', file=sys.stderr)
        return 0

    inserted = seed_stock_universe(client, rows)
    print(f'Upserted {inserted} NSE rows into stock_universe.')
    return inserted


def seed_bse(client):
    print(f'Fetching {BSE_SCRIP_LIST_URL} ...')
    response = requests.get(BSE_SCRIP_LIST_URL, headers=BSE_REQUEST_HEADERS, timeout=30)
    response.raise_for_status()

    rows = parse_bse_equity_json(response.text)
    print(f'Parsed {len(rows)} rows from the BSE equity list.')
    if len(rows) < 500:
        print('WARNING: BSE row count looks unexpectedly low -- verify the source before trusting this seed.', file=sys.stderr)
        return 0

    inserted = seed_bse_universe(client, rows)
    print(f'Upserted {inserted} BSE rows into stock_universe.')
    return inserted


def report_total_count(client):
    result = client.rpc('execute_sql', {'query': 'SELECT COUNT(*) AS total FROM stock_universe'}).execute()
    rows = getattr(result, 'data', result)
    total = rows[0]['total'] if rows else '?'
    print(f'stock_universe now has {total} total rows (NSE + BSE combined).')


def main():
    load_dotenv()
    supabase_url = os.environ.get('SUPABASE_URL')
    supabase_key = os.environ.get('SUPABASE_KEY')
    if not supabase_url or not supabase_key:
        print('SUPABASE_URL and SUPABASE_KEY must be set (check .env)', file=sys.stderr)
        sys.exit(1)

    client = create_client(supabase_url, supabase_key)
    initialize_stock_universe_table_if_needed(client)

    seed_nse(client)
    seed_bse(client)
    report_total_count(client)


if __name__ == '__main__':
    main()
