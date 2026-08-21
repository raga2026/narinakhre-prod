import re
import time

import requests
from bs4 import BeautifulSoup

SCREENER_BASE_URL = 'https://www.screener.in/company'
# Screener.in is a public page, not an official API. This now backs a daily
# rotation of up to 300 symbols (see fundamentals_ingestion.sync_fundamentals_rotation)
# rather than the original small weekly watchlist batch -- keep this delay
# in place and reassess before tightening the schedule further.
REQUEST_DELAY_SECONDS = 2
USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/120.0 Safari/537.36 StoqBell/1.0 (personal use)'
)


class ScreenerParseError(Exception):
    pass


def _parse_number(text):
    """Screener formats numbers like '1,234.56', '12.3 %', '₹45,230 Cr.' --
    strips everything except digits/./- and returns a float, or None if
    nothing numeric is left."""
    if not text:
        return None
    cleaned = re.sub(r'[^0-9.\-]', '', text)
    if not cleaned or cleaned in ('-', '.', '-.'):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_top_ratios(soup):
    """Screener's company page has a <ul id="top-ratios"> list of
    <li><span class="name">Label</span> ... <span class="number">Value</span></li>
    items -- the most stable part of their layout historically. Returns a
    dict of {label: value_text}; a label that's missing or renamed just
    won't be in the dict rather than raising."""
    ratios = {}
    ratios_list = soup.find('ul', id='top-ratios')
    if not ratios_list:
        return ratios
    for item in ratios_list.find_all('li'):
        name_el = item.find('span', class_='name')
        value_el = item.find('span', class_='number')
        if not name_el or not value_el:
            continue
        ratios[name_el.get_text(strip=True)] = value_el.get_text(strip=True)
    return ratios


def _find_ratio(raw_ratios, *labels):
    wanted = {label.lower() for label in labels}
    for key, value in raw_ratios.items():
        if key.strip().lower() in wanted:
            return _parse_number(value)
    return None


def _parse_compounded_profit_growth_ttm(soup):
    """Screener shows growth trend tables (Compounded Sales/Profit Growth,
    Stock Price CAGR, Return on Equity) as <table class="ranges-table">
    elements whose FIRST row is a <th colspan="2">Label</th>, not preceded
    by a normal <h2>/<h3> heading -- an earlier version of this function
    assumed a heading-based structure that turned out not to match any real
    page at all (confirmed while extending this parser: 'Compounded Profit
    Growth' never appears as an h2/h3, only inside this table's own header
    cell). There's also no '1 Year' row -- the shortest period shown is
    'TTM' (trailing twelve months), which is what's used here as the
    earnings_growth_pct input for PEG; TTM is arguably more current than a
    calendar year-end figure anyway. Returns None if the table isn't found
    or doesn't have a TTM row -- PEG is left null in that case, per
    fundamentals_ingestion's fallback rule, rather than raising."""
    for table in soup.find_all('table', class_='ranges-table'):
        header = table.find('th')
        if not header or 'compounded profit growth' not in header.get_text(strip=True).lower():
            continue
        for row in table.find_all('tr')[1:]:
            cells = row.find_all('td')
            if len(cells) >= 2 and cells[0].get_text(strip=True).lower().startswith('ttm'):
                return _parse_number(cells[1].get_text(strip=True))
    return None


def _get_section_tables(soup, section_id):
    """Screener lays out Quarterly Results, Profit & Loss, Balance Sheet,
    Cash Flow, and Shareholding Pattern each as <section id="..."> with one
    or more <table>s inside -- header row of period columns (oldest first,
    most recent last), body rows of <td class="text">Label</td><td>value
    </td>.... Shareholding Pattern specifically has TWO tables (a
    'quarterly' view and a 'yearly' view); the quarterly one omits the
    Promoters and Government rows entirely (confirmed against a real page),
    they only appear in the yearly table -- hence this returns a list of
    every table in the section rather than just the first, so _row_values
    can search across all of them. Returns [] if the section isn't present
    for this company (some sections vary by company type)."""
    section = soup.find('section', id=section_id)
    if not section:
        return []
    return section.find_all('table')


def _row_values(tables, label_prefix):
    """Finds the first row (searching across every table passed in, in
    order) whose label starts with label_prefix (Screener appends a '+'
    expand icon to some labels, e.g. 'Sales+', hence prefix match rather
    than exact) and returns its data cells as raw text, in the same
    left-to-right (oldest-to-newest) order as that table's header. Returns
    None if no matching row is found in any of the tables. tables may be a
    single table (for sections known to have just one) or a list."""
    if tables is None:
        return None
    if not isinstance(tables, list):
        tables = [tables]
    for table in tables:
        if not table:
            continue
        for tr in table.find_all('tr'):
            label_cell = tr.find('td', class_='text')
            if not label_cell:
                continue
            if label_cell.get_text(strip=True).startswith(label_prefix):
                cells = tr.find_all('td')
                return [c.get_text(strip=True) for c in cells[1:]]
    return None


def _latest_numeric(values):
    if not values:
        return None
    return _parse_number(values[-1])


def _yoy_growth_pct(values):
    """% change between the latest quarter and the same quarter a year
    earlier (4 columns back), not quarter-over-quarter -- quarterly figures
    are seasonal enough that QoQ swings are noisy on their own; YoY is the
    standard comparison for a screen like this. Needs at least 5 quarterly
    columns; returns None otherwise, or if the year-ago value is zero/blank
    (can't compute a meaningful % change from zero)."""
    if not values or len(values) < 5:
        return None
    latest = _parse_number(values[-1])
    year_ago = _parse_number(values[-5])
    if latest is None or year_ago is None or year_ago == 0:
        return None
    return round((latest - year_ago) / abs(year_ago) * 100, 2)


def _parse_quarterly_metrics(soup):
    """opm_pct/eps are the latest quarter's values; the two growth figures
    are YoY (see _yoy_growth_pct). All from the #quarters section's table,
    confirmed against a real fetched page while building this (Sales+, OPM
    %, Net Profit+, EPS in Rs rows)."""
    tables = _get_section_tables(soup, 'quarters')
    return {
        'opm_pct': _latest_numeric(_row_values(tables, 'OPM')),
        'eps': _latest_numeric(_row_values(tables, 'EPS')),
        'quarterly_revenue_growth_pct': _yoy_growth_pct(_row_values(tables, 'Sales')),
        'quarterly_profit_growth_pct': _yoy_growth_pct(_row_values(tables, 'Net Profit')),
    }


def _parse_balance_sheet_derived(soup):
    """debt_to_equity and tol_by_tnw aren't shown as ready-made ratios for
    every company (confirmed: absent from top-ratios for a real debt-light
    company while building this) -- both are computed here from Screener's
    simplified Balance Sheet (#balance-sheet: Equity Capital, Reserves,
    Borrowings, Total Assets rows). net_worth = Equity Capital + Reserves;
    TOL (total outside liabilities) is approximated as Total Assets minus
    net_worth, since in this presentation Total Assets == Total Liabilities
    by construction (it's a balance sheet).

    current_ratio is deliberately NOT computed or approximated here at all:
    Screener's simplified balance sheet has no current-assets/
    current-liabilities split (confirmed against a real page), so there's
    no sound way to derive it from this data source. It stays None."""
    tables = _get_section_tables(soup, 'balance-sheet')
    result = {'debt_to_equity': None, 'tol_by_tnw': None, 'total_assets': None}
    if not tables:
        return result

    equity = _latest_numeric(_row_values(tables, 'Equity Capital'))
    reserves = _latest_numeric(_row_values(tables, 'Reserves'))
    borrowings = _latest_numeric(_row_values(tables, 'Borrowings'))
    total_assets = _latest_numeric(_row_values(tables, 'Total Assets'))
    result['total_assets'] = total_assets

    if equity is not None and reserves is not None:
        net_worth = equity + reserves
        if net_worth:
            if borrowings is not None:
                result['debt_to_equity'] = round(borrowings / net_worth, 3)
            if total_assets is not None:
                result['tol_by_tnw'] = round((total_assets - net_worth) / net_worth, 3)
    return result


def _parse_roa_pct(soup, total_assets):
    """ROA = latest annual Net Profit / Total Assets. Deliberately pulls
    Net Profit from #profit-loss (annual figures), not #quarters -- mixing
    a quarterly profit figure with an annual Total Assets figure would
    misstate the ratio by roughly 4x. total_assets comes from
    _parse_balance_sheet_derived so both are read once, not twice."""
    if not total_assets:
        return None
    tables = _get_section_tables(soup, 'profit-loss')
    net_profit = _latest_numeric(_row_values(tables, 'Net Profit')) if tables else None
    if net_profit is None:
        return None
    return round(net_profit / total_assets * 100, 2)


def _parse_free_cash_flow(soup):
    """Direct row in #cash-flow -- 'Free Cash Flow', confirmed present as
    its own labeled row against a real fetched page."""
    tables = _get_section_tables(soup, 'cash-flow')
    return _latest_numeric(_row_values(tables, 'Free Cash Flow')) if tables else None


def _parse_shareholding(soup):
    """Latest Promoters/FIIs/DIIs/Public holding %, from #shareholding --
    that section actually has TWO tables (quarterly view and yearly view);
    the quarterly one omits Promoters/Government entirely (confirmed
    against a real page), so this searches both via _row_values' list
    support rather than just the first table. The yearly table also
    carries several years of history in one fetch -- only the latest
    column is captured here, matching what stock_fundamentals stores per
    snapshot; the promoter/FII trend checks in fundamental_screen.py
    compare across our own accumulated stock_fundamentals snapshots over
    time instead of reaching back into this table's own history.

    DIIs is a plain row in the same table as Promoters/FIIs/Public --
    confirmed against a real fetched page (screener.in/company/RPOWER/,
    quarterly shareholding table: Promoters, FIIs, DIIs, Government,
    Public, No. of Shareholders, in that order) -- captured the exact
    same way as the other three, no new parsing approach needed.

    promoter_pledge_pct is deliberately NOT parsed here -- confirmed
    against that same real page (a full raw-HTML search for "pledge",
    case-insensitive, across the entire fetched page) that Screener's
    public company page doesn't contain any promoter pledge data at all,
    for a company (Reliance Power) with a well-documented history of very
    high promoter pledge -- if it were on this page in any form, it should
    have shown up there. The stock_fundamentals.promoter_pledge_pct column
    exists (see STOCK_FUNDAMENTALS_ALTER_SQL) ready for whenever a real,
    confirmed source is found; it's simply never set by this function, so
    it stays NULL rather than guessing a selector that was never actually
    verified."""
    tables = _get_section_tables(soup, 'shareholding')
    result = {
        'promoter_holding_pct': None, 'fii_holding_pct': None,
        'dii_holding_pct': None, 'public_holding_pct': None,
    }
    if not tables:
        return result
    result['promoter_holding_pct'] = _latest_numeric(_row_values(tables, 'Promoters'))
    result['fii_holding_pct'] = _latest_numeric(_row_values(tables, 'FIIs'))
    result['dii_holding_pct'] = _latest_numeric(_row_values(tables, 'DIIs'))
    result['public_holding_pct'] = _latest_numeric(_row_values(tables, 'Public'))
    return result


def _parse_industry_classification(soup):
    """Screener shows a sector/industry breadcrumb near the top of every
    company page as a run of <a title="..."> tags -- title is one of
    'Broad Sector', 'Sector', 'Broad Industry', 'Industry' (broadest to most
    specific), confirmed against a real fetched page while building this.
    Returns the most granular level actually present -- 'Industry' if it's
    there, else 'Broad Industry', else 'Sector', else 'Broad Sector', else
    None if the breadcrumb is missing entirely (some company types may not
    have one). Deliberately the finest available level rather than always
    the broadest: two companies sharing a Sector can still trade at very
    different typical valuations at the Industry level, which is what this
    feeds into utils/fundamental_screen.py's industry-relative
    price-to-book check for."""
    levels = {}
    for a in soup.find_all('a', title=True):
        title = a.get('title')
        if title in ('Broad Sector', 'Sector', 'Broad Industry', 'Industry'):
            levels[title] = a.get_text(strip=True)
    for level in ('Industry', 'Broad Industry', 'Sector', 'Broad Sector'):
        if levels.get(level):
            return levels[level]
    return None


def _parse_price_to_book(raw_ratios):
    """Current Price / Book Value -- both are top-ratios entries; Screener
    doesn't show a ready-made 'Price to Book' label there."""
    price = _find_ratio(raw_ratios, 'Current Price')
    book_value = _find_ratio(raw_ratios, 'Book Value')
    if price is None or not book_value:
        return None
    return round(price / book_value, 2)


def fetch_fundamentals(symbol):
    """Fetches and parses one company's Screener.in page. Returns a dict
    covering every stock_fundamentals column this app tracks. Any field
    Screener didn't show (or this parser couldn't find/derive) is None
    rather than raising, so one missing field doesn't sink the whole
    symbol -- sector_avg_pe and current_ratio are always None (see their
    parsing functions' docstrings for why: the peers table is JS/AJAX-loaded
    and never appears in a plain HTTP fetch, and current ratio isn't
    derivable from Screener's simplified balance sheet at all).

    Raises ScreenerParseError for outright failures (404, timeout, layout
    changed enough that no ratios section was found at all) -- the caller
    (sync_fundamentals / sync_fundamentals_rotation) decides to log-and-skip
    those."""
    url = f'{SCREENER_BASE_URL}/{symbol}/'
    try:
        response = requests.get(url, headers={'User-Agent': USER_AGENT}, timeout=15)
    except requests.RequestException as e:
        raise ScreenerParseError(f'Request to Screener failed for {symbol}: {e}')
    finally:
        time.sleep(REQUEST_DELAY_SECONDS)

    if response.status_code == 404:
        raise ScreenerParseError(f'Screener has no page for {symbol} (404)')
    if response.status_code != 200:
        raise ScreenerParseError(f'Screener returned {response.status_code} for {symbol}')

    soup = BeautifulSoup(response.text, 'html.parser')
    raw_ratios = _parse_top_ratios(soup)
    if not raw_ratios:
        raise ScreenerParseError(f"Could not find the ratios section for {symbol} -- Screener's layout may have changed")

    quarterly = _parse_quarterly_metrics(soup)
    balance_sheet = _parse_balance_sheet_derived(soup)

    debt_to_equity = _find_ratio(raw_ratios, 'Debt to equity', 'Debt to Equity')
    if debt_to_equity is None:
        debt_to_equity = balance_sheet['debt_to_equity']

    return {
        'pe_ratio': _find_ratio(raw_ratios, 'Stock P/E', 'P/E'),
        'eps': quarterly['eps'] if quarterly['eps'] is not None else _find_ratio(raw_ratios, 'EPS'),
        'roe': _find_ratio(raw_ratios, 'ROE'),
        'roce_pct': _find_ratio(raw_ratios, 'ROCE'),
        'debt_to_equity': debt_to_equity,
        'market_cap': _find_ratio(raw_ratios, 'Market Cap'),
        'earnings_growth_pct': _parse_compounded_profit_growth_ttm(soup),
        'price_to_book': _parse_price_to_book(raw_ratios),
        'opm_pct': quarterly['opm_pct'],
        'quarterly_profit_growth_pct': quarterly['quarterly_profit_growth_pct'],
        'quarterly_revenue_growth_pct': quarterly['quarterly_revenue_growth_pct'],
        'tol_by_tnw': balance_sheet['tol_by_tnw'],
        'roa_pct': _parse_roa_pct(soup, balance_sheet['total_assets']),
        'free_cash_flow': _parse_free_cash_flow(soup),
        'sector_avg_pe': None,   # peers table is JS/AJAX-loaded, not in the static HTML
        'current_ratio': None,  # not derivable from Screener's simplified balance sheet
        'industry': _parse_industry_classification(soup),
        **_parse_shareholding(soup),
    }
