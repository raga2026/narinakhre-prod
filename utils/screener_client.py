import re
import time

import requests
from bs4 import BeautifulSoup

SCREENER_BASE_URL = 'https://www.screener.in/company'
# Screener.in is a public page, not an official API -- this is a weekly
# batch over a small watchlist, and this delay keeps request volume low.
# Reassess before scaling this up to more symbols or a tighter schedule.
REQUEST_DELAY_SECONDS = 2
USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/120.0 Safari/537.36 NariNakhreStocks/1.0 (personal use)'
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


def _parse_compounded_profit_growth_1y(soup):
    """Best-effort: Screener shows a 'Compounded Profit Growth' table
    elsewhere on the page with rows like '1 Year: 22%'. Used as the
    earnings_growth_pct input for PEG. Returns None if the section isn't
    found or doesn't match this shape -- PEG is left null in that case, per
    fundamentals_ingestion's fallback rule, rather than raising."""
    for heading in soup.find_all(['h2', 'h3']):
        if 'compounded profit growth' in heading.get_text(strip=True).lower():
            table = heading.find_next('table')
            if not table:
                return None
            for row in table.find_all('tr'):
                cells = row.find_all('td')
                if len(cells) >= 2 and '1 year' in cells[0].get_text(strip=True).lower():
                    return _parse_number(cells[1].get_text(strip=True))
    return None


def fetch_fundamentals(symbol):
    """Fetches and parses one company's Screener.in page. Returns a dict
    with pe_ratio/eps/roe/debt_to_equity/market_cap/earnings_growth_pct --
    any ratio Screener didn't show (or this parser couldn't find) is None
    rather than raising, so one missing field doesn't sink the whole symbol.
    Raises ScreenerParseError for outright failures (404, timeout, layout
    changed enough that no ratios section was found at all) -- the caller
    (sync_fundamentals) decides to log-and-skip those."""
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

    def find_ratio(*labels):
        wanted = {label.lower() for label in labels}
        for key, value in raw_ratios.items():
            if key.strip().lower() in wanted:
                return _parse_number(value)
        return None

    return {
        'pe_ratio': find_ratio('Stock P/E', 'P/E'),
        'eps': find_ratio('EPS'),
        'roe': find_ratio('ROE'),
        'debt_to_equity': find_ratio('Debt to equity', 'Debt to Equity'),
        'market_cap': find_ratio('Market Cap'),
        'earnings_growth_pct': _parse_compounded_profit_growth_1y(soup),
    }
