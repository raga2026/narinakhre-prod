"""Per-company news, sourced from Google News RSS -- free, no API key,
same "fetch an external source per company" shape as screener_client.py's
own scraping, just against a different source. Ranked by recency only --
"prominent" on the home page (see get_prominent_news) means "freshest
across the watchlist," not "most market-moving."

Stored headlines here now do feed into scoring too, as a supplementary
signal: see utils/news_sentiment.py's compute_company_sentiment (rule-based
keyword scoring, not a claim of real relevance ranking on top of the free
RSS feed) and utils.nns_score.compute_nns_score's own news_sentiment_bonus
-- suggestion_engine.py's _fetch_news_sentiment_by_watchlist reads straight
from the stock_news table this module owns, rather than going through
get_recent_news, since it needs every candidate's headlines batched in one
query, not one company at a time.

Scoped to the stock_watchlist set (~80 companies), not the full
scrape-eligible universe (~1,067) -- news needs to be fetched daily to be
useful at all, unlike fundamentals' own 15-day rotation, and the watchlist
is the actively-curated set this product actually recommends from. A
universe-only company simply has no news stored for it -- get_recent_news
returns an empty list, same as any other watchlist-only field elsewhere in
this codebase (is_recommended, StoqBell Score, etc.)."""
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import requests

GOOGLE_NEWS_RSS_URL = 'https://news.google.com/rss/search'
# Same politeness/rate-limit reasoning as screener_client.REQUEST_DELAY_SECONDS
# -- Google News is a public feed, not an official API either.
REQUEST_DELAY_SECONDS = 2
USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/120.0 Safari/537.36 StoqBell/1.0 (personal use)'
)
# How many of the most recent headlines are kept per company -- both what
# fetch_news_for_company returns per fetch and what sync_stock_news prunes
# stock_news down to afterward.
HEADLINES_PER_COMPANY = 5

STOCK_NEWS_TABLE_SQL = [
    '''CREATE TABLE IF NOT EXISTS stock_news (
        id BIGSERIAL PRIMARY KEY,
        watchlist_id BIGINT NOT NULL REFERENCES stock_watchlist(id),
        headline TEXT NOT NULL,
        link TEXT NOT NULL,
        source TEXT,
        published_at TIMESTAMPTZ,
        fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(watchlist_id, link)
    )'''
]


def initialize_stock_news_table_if_needed(client):
    for sql in STOCK_NEWS_TABLE_SQL:
        try:
            client.rpc('execute_sql', {'query': sql}).execute()
        except Exception as e:
            print(f'Stock news table init warning (may already exist): {e}')


class StockNewsFetchError(Exception):
    pass


def _parse_pub_date(value):
    """Google News RSS <pubDate> is RFC 822 (e.g. 'Mon, 17 Aug 2026
    09:00:00 GMT') -- email.utils already has a correct parser for exactly
    this format, no need to hand-roll one. Returns None (not raise) for a
    missing/unparseable date -- a headline with an unknown date still gets
    stored, it just sorts last."""
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def fetch_news_for_company(company_name, symbol):
    """Fetches and parses Google News RSS results for one company, newest
    first, capped at HEADLINES_PER_COMPANY. Queries by company_name (a
    real company name searches far more precisely than a bare ticker
    symbol, which Google News often has no idea about) falling back to
    symbol only when no name is on record at all.

    Returns [{'headline', 'link', 'source', 'published_at'}, ...] --
    published_at is a timezone-aware datetime or None (see _parse_pub_date).
    Raises StockNewsFetchError for outright failures (network error,
    non-200, unparseable XML) -- the caller (sync_stock_news) logs and
    continues past those, same as screener_client.fetch_fundamentals'
    ScreenerParseError."""
    query = company_name or symbol
    if not query:
        raise StockNewsFetchError('No company name or symbol to search for')

    url = f'{GOOGLE_NEWS_RSS_URL}?q={quote(query)}&hl=en-IN&gl=IN&ceid=IN:en'
    try:
        response = requests.get(url, headers={'User-Agent': USER_AGENT}, timeout=15)
    except requests.RequestException as e:
        raise StockNewsFetchError(f'Request to Google News failed for {query}: {e}')

    if response.status_code != 200:
        raise StockNewsFetchError(f'Google News returned {response.status_code} for {query}')

    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as e:
        raise StockNewsFetchError(f'Could not parse Google News RSS for {query}: {e}')

    items = root.findall('./channel/item')
    results = []
    for item in items[:HEADLINES_PER_COMPANY]:
        headline = (item.findtext('title') or '').strip()
        link = (item.findtext('link') or '').strip()
        if not headline or not link:
            continue  # a malformed item is skipped, not fatal to the whole fetch
        source_el = item.find('source')
        source = source_el.text.strip() if source_el is not None and source_el.text else None
        results.append({
            'headline': headline,
            'link': link,
            'source': source,
            'published_at': _parse_pub_date(item.findtext('pubDate')),
        })
    return results


def sync_stock_news(db, fetch_fn=None):
    """Daily job (see app.py's /stocks/news/sync) -- fetches news for
    every active stock_watchlist row (all ~80 at once, no rotation/staleness
    batching needed at this scale, unlike the 1,067-company fundamentals
    rotation) and upserts each headline into stock_news. ON CONFLICT
    (watchlist_id, link) DO NOTHING -- an article already stored from an
    earlier run never changes, only whether it's still there; re-fetching
    it again is a no-op, not an update. After each company's fetch, prunes
    that company's stored rows down to the HEADLINES_PER_COMPANY most
    recent by published_at, so the table doesn't grow unbounded as old
    headlines age out of the top-N window over time.

    Per-symbol failures log and continue -- one bad company's fetch
    (network hiccup, no results at all) doesn't stop the rest of the
    batch, same handling as sync_fundamentals_rotation.

    Returns {'companies_checked', 'articles_stored', 'failures': [...]}."""
    fetch = fetch_fn or fetch_news_for_company

    rows = db.execute(
        "SELECT id AS watchlist_id, symbol, name AS company_name FROM stock_watchlist WHERE is_active = 1"
    ).fetchall()

    companies_checked = 0
    articles_stored = 0
    failures = []

    for i, row in enumerate(rows):
        watchlist_id = row['watchlist_id']
        symbol = row['symbol']

        try:
            articles = fetch(row.get('company_name'), symbol)
            companies_checked += 1
            for article in articles:
                db.execute(
                    '''INSERT INTO stock_news (watchlist_id, headline, link, source, published_at)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT (watchlist_id, link) DO NOTHING''',
                    (watchlist_id, article['headline'], article['link'], article['source'], article['published_at'])
                )
                articles_stored += 1
            db.execute(
                '''DELETE FROM stock_news WHERE watchlist_id = ? AND id NOT IN (
                       SELECT id FROM stock_news WHERE watchlist_id = ?
                       ORDER BY published_at DESC NULLS LAST LIMIT ?
                   )''',
                (watchlist_id, watchlist_id, HEADLINES_PER_COMPANY)
            )
            db.commit()
        except Exception as exc:
            failures.append({'symbol': symbol, 'error': str(exc)})
            print(f'Stock news sync failed for {symbol}: {exc}')

        if i < len(rows) - 1:
            time.sleep(REQUEST_DELAY_SECONDS)

    return {'companies_checked': companies_checked, 'articles_stored': articles_stored, 'failures': failures}


def get_recent_news(db, watchlist_id, limit=HEADLINES_PER_COMPANY):
    """Most recent stored headlines for one company, newest first --
    powers the "Recent news" section on /stocks/company/<id> and
    /stocks/universe/<id>. Returns [] for a watchlist_id with nothing
    stored (never synced yet, or a universe-only company with no
    watchlist_id at all -- callers pass None/skip the query in that case,
    see app.py's stocks_universe_detail)."""
    if not watchlist_id:
        return []
    return db.execute(
        '''SELECT headline, link, source, published_at FROM stock_news
           WHERE watchlist_id = ? ORDER BY published_at DESC NULLS LAST LIMIT ?''',
        (watchlist_id, limit)
    ).fetchall()


def get_prominent_news(db, limit=6):
    """Freshest headlines across the ENTIRE watchlist at once, newest
    first -- powers the "In the news" section on the Stocks home page.
    "Prominent" here means "most recent," not "most market-moving" (see
    this module's own docstring on why -- no sentiment/relevance scoring
    exists for a free RSS feed)."""
    return db.execute(
        '''SELECT n.headline, n.link, n.source, n.published_at, w.symbol, w.name AS company_name
           FROM stock_news n
           JOIN stock_watchlist w ON w.id = n.watchlist_id
           ORDER BY n.published_at DESC NULLS LAST LIMIT ?''',
        (limit,)
    ).fetchall()


IST = timezone(timedelta(hours=5, minutes=30))


def with_published_at_ist(headlines):
    """Returns NEW dicts (does not mutate the input rows) with published_at
    replaced by an IST-formatted display string, or None where it was
    missing/unparseable -- get_recent_news/get_prominent_news return it as a
    raw ISO8601 string (see db.py's SupabaseCursor, which doesn't coerce
    Supabase's JSON response into a native datetime), and every other
    timestamp shown in Stocks is IST, not raw UTC (see stocks_admin_dashboard's
    own kite_expires_at_ist/fundamentals_last_synced_ist for the same
    pattern) -- callers pass the result straight to a template rather than
    formatting published_at themselves."""
    result = []
    for headline in headlines:
        headline = dict(headline)
        raw = headline.get('published_at')
        if raw:
            try:
                dt = raw if isinstance(raw, datetime) else datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                headline['published_at'] = dt.astimezone(IST).strftime('%d %b %Y, %I:%M %p IST')
            except ValueError:
                headline['published_at'] = None
        result.append(headline)
    return result
