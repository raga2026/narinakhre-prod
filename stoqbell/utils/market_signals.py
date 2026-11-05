"""Market-drivers layer: macro/commodity driver prices, sector-level news,
a per-sector "pressure" score, and driver-shock alerts. DB + network side of
the layer whose pure math lives in utils/sector_drivers.py.

What it adds over utils/stock_news.py (company headlines only): the things
that move a stock WITHOUT showing up in its own candles or headlines -- a
crude-oil spike hitting autos/paints/airlines, a rupee move hitting IT, a
VIX jump hitting banks, a sector-wide sell-off.

Data sources (all free, no API key -- same "public feed, not an official
API" caveat as screener_client.py and stock_news.py, so every fetch fails
soft and the daily job reports failures rather than crashing):
  * Driver prices: Yahoo Finance chart endpoint (commodities, USDINR,
    India VIX, US 10Y).
  * Sector/driver news: Google News RSS via stock_news.fetch_news_for_company
    (it just searches by a query string, so a sector query works the same
    as a company name), scored with news_sentiment's keyword lexicon.
  * Realised sector move: industry_growth.compute_industry_changes, built
    from price data this app already syncs.

Downstream use (all fail-open -- missing/stale pressure data never blocks or
changes anything):
  * suggestion_engine attaches 'sector_pressure' to each candidate;
    nns_score applies a capped malus/bonus and is_suggestion_eligible
    suppresses NEW buys in a sector under heavy pressure.
  * sync_market_signals emails the site owner when a driver shocks (|z| >=
    SHOCK_ZSCORE), listing exposed watchlist companies.
  * Stocks home page shows a "Market Drivers" card.

Not covered (phase 2): FII/DII flows and block/bulk deals -- NSE's
endpoints block automated clients and need a more fragile scrape."""
import math
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

import requests

from stoqbell.utils.industry_growth import compute_industry_changes
from stoqbell.utils.news_sentiment import compute_company_sentiment
from stoqbell.utils.sector_drivers import (
    DRIVERS,
    GATE_PRESSURE,
    SHOCK_ZSCORE,
    all_rules,
    compute_driver_shock,
    compute_sector_pressure,
    estimate_move_probabilities,
    group_for_industry,
    rule_for_group,
)
from stoqbell.utils.stock_news import HEADLINES_PER_COMPANY, fetch_news_for_company

YAHOO_CHART_URL = 'https://query1.finance.yahoo.com/v8/finance/chart/'
YAHOO_RANGE = '4mo'
USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/120.0 Safari/537.36 StoqBell/1.0 (personal use)'
)
REQUEST_DELAY_SECONDS = 2
# Stored sector pressure older than this is ignored (treated as "no data")
# -- a job that silently stopped must not keep gating buys forever.
PRESSURE_MAX_AGE_DAYS = 4
IST = timezone(timedelta(hours=5, minutes=30))
# A driver counts as 'affecting' a sector for alert purposes at/above this
# |sensitivity| (see sector_drivers.SECTOR_RULES).
ALERT_MIN_SENSITIVITY = 0.3

MARKET_SIGNALS_TABLES_SQL = [
    '''CREATE TABLE IF NOT EXISTS market_driver_signals (
        driver_key TEXT NOT NULL,
        as_of_date DATE NOT NULL,
        value NUMERIC,
        change_1d_pct NUMERIC,
        change_5d_pct NUMERIC,
        zscore NUMERIC,
        fetched_at TIMESTAMPTZ DEFAULT NOW(),
        PRIMARY KEY (driver_key, as_of_date)
    )''',
    '''CREATE TABLE IF NOT EXISTS sector_news (
        id BIGSERIAL PRIMARY KEY,
        scope_key TEXT NOT NULL,
        headline TEXT NOT NULL,
        link TEXT NOT NULL,
        source TEXT,
        published_at TIMESTAMPTZ,
        fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(scope_key, link)
    )''',
    '''CREATE TABLE IF NOT EXISTS sector_pressure (
        group_key TEXT NOT NULL,
        as_of_date DATE NOT NULL,
        pressure NUMERIC,
        label TEXT,
        driver_component NUMERIC,
        news_component NUMERIC,
        move_component NUMERIC,
        top_driver TEXT,
        top_driver_impact NUMERIC,
        computed_at TIMESTAMPTZ DEFAULT NOW(),
        PRIMARY KEY (group_key, as_of_date)
    )''',
    '''CREATE TABLE IF NOT EXISTS market_event_alerts (
        id BIGSERIAL PRIMARY KEY,
        alert_key TEXT NOT NULL UNIQUE,
        detail TEXT,
        sent_at TIMESTAMPTZ DEFAULT NOW()
    )''',
]


def initialize_market_signals_tables_if_needed(client):
    for sql in MARKET_SIGNALS_TABLES_SQL:
        try:
            client.rpc('execute_sql', {'query': sql}).execute()
        except Exception as e:
            print(f'Market signals table init warning (may already exist): {e}')


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

class MarketSignalFetchError(Exception):
    pass


def fetch_price_series(yahoo_symbol):
    """Daily closes for one Yahoo symbol, OLDEST first, as
    [(date_str 'YYYY-MM-DD', close), ...] with null closes dropped.
    Raises MarketSignalFetchError on network error, non-200, or an
    unparseable/empty payload."""
    url = f'{YAHOO_CHART_URL}{quote(yahoo_symbol)}?range={YAHOO_RANGE}&interval=1d'
    try:
        response = requests.get(url, headers={'User-Agent': USER_AGENT}, timeout=15)
    except requests.RequestException as e:
        raise MarketSignalFetchError(f'Request to Yahoo failed for {yahoo_symbol}: {e}')
    if response.status_code != 200:
        raise MarketSignalFetchError(f'Yahoo returned {response.status_code} for {yahoo_symbol}')
    try:
        result = response.json()['chart']['result'][0]
        timestamps = result['timestamp']
        closes = result['indicators']['quote'][0]['close']
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise MarketSignalFetchError(f'Could not parse Yahoo response for {yahoo_symbol}: {e}')

    series = []
    for ts, close in zip(timestamps, closes):
        if close is None or not math.isfinite(close) or close <= 0:
            continue
        series.append((datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(IST).date().isoformat(), float(close)))
    if len(series) < 2:
        raise MarketSignalFetchError(f'Not enough price history from Yahoo for {yahoo_symbol}')
    return series


def _num(value):
    """Finite float or None -- NaN/inf would be formatted into the SQL
    string as 'nan'/'inf' by SupabaseCursor and break the statement."""
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _store_driver_signal(db, driver_key, as_of_date, shock):
    db.execute(
        '''INSERT INTO market_driver_signals
               (driver_key, as_of_date, value, change_1d_pct, change_5d_pct, zscore)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT (driver_key, as_of_date) DO UPDATE SET
               value = EXCLUDED.value, change_1d_pct = EXCLUDED.change_1d_pct,
               change_5d_pct = EXCLUDED.change_5d_pct, zscore = EXCLUDED.zscore,
               fetched_at = NOW()''',
        (driver_key, as_of_date, _num(shock['value']), _num(shock['change_1d_pct']),
         _num(shock['change_5d_pct']), _num(shock['zscore']))
    )
    db.commit()


def _store_scope_news(db, scope_key, articles):
    for article in articles:
        db.execute(
            '''INSERT INTO sector_news (scope_key, headline, link, source, published_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT (scope_key, link) DO NOTHING''',
            (scope_key, article['headline'], article['link'], article['source'], article['published_at'])
        )
    db.execute(
        '''DELETE FROM sector_news WHERE scope_key = ? AND id NOT IN (
               SELECT id FROM sector_news WHERE scope_key = ?
               ORDER BY published_at DESC NULLS LAST LIMIT ?
           )''',
        (scope_key, scope_key, HEADLINES_PER_COMPANY)
    )
    db.commit()


def _store_pressure(db, group_key, as_of_date, result):
    db.execute(
        '''INSERT INTO sector_pressure
               (group_key, as_of_date, pressure, label, driver_component, news_component,
                move_component, top_driver, top_driver_impact)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (group_key, as_of_date) DO UPDATE SET
               pressure = EXCLUDED.pressure, label = EXCLUDED.label,
               driver_component = EXCLUDED.driver_component,
               news_component = EXCLUDED.news_component,
               move_component = EXCLUDED.move_component,
               top_driver = EXCLUDED.top_driver,
               top_driver_impact = EXCLUDED.top_driver_impact,
               computed_at = NOW()''',
        (group_key, as_of_date, result['pressure'], result['label'], result['driver_component'],
         result['news_component'], result['move_component'], result['top_driver'],
         result['top_driver_impact'])
    )
    db.commit()


def get_latest_driver_signals(db):
    """{driver_key: {'value', 'change_1d_pct', 'change_5d_pct', 'zscore',
    'as_of_date'}} -- the most recent stored row per driver. Numeric
    columns are coerced to float (or None)."""
    rows = db.execute(
        '''SELECT s.driver_key, s.as_of_date, s.value, s.change_1d_pct, s.change_5d_pct, s.zscore
           FROM market_driver_signals s
           WHERE s.as_of_date = (
               SELECT MAX(s2.as_of_date) FROM market_driver_signals s2 WHERE s2.driver_key = s.driver_key
           )'''
    ).fetchall()
    signals = {}
    for row in rows:
        signals[row['driver_key']] = {
            'as_of_date': str(row['as_of_date'])[:10],
            'value': _num(row.get('value')),
            'change_1d_pct': _num(row.get('change_1d_pct')),
            'change_5d_pct': _num(row.get('change_5d_pct')),
            'zscore': _num(row.get('zscore')),
        }
    return signals


def get_sector_headlines(db, scope_key, limit=HEADLINES_PER_COMPANY):
    return db.execute(
        '''SELECT headline, link, source, published_at FROM sector_news
           WHERE scope_key = ? ORDER BY published_at DESC NULLS LAST LIMIT ?''',
        (scope_key, limit)
    ).fetchall()


def get_sector_pressure_by_group(db, today=None):
    """{group_key: {'pressure', 'label', 'top_driver', 'as_of_date', ...}}
    -- the latest stored row per group, EXCLUDING rows older than
    PRESSURE_MAX_AGE_DAYS (a stalled job must not keep gating/penalising
    forever). {} when nothing usable exists."""
    today = today or datetime.now(IST).date()
    cutoff = (today - timedelta(days=PRESSURE_MAX_AGE_DAYS)).isoformat()
    rows = db.execute(
        '''SELECT p.group_key, p.as_of_date, p.pressure, p.label, p.driver_component,
                  p.news_component, p.move_component, p.top_driver, p.top_driver_impact
           FROM sector_pressure p
           WHERE p.as_of_date >= ?
             AND p.as_of_date = (
                 SELECT MAX(p2.as_of_date) FROM sector_pressure p2 WHERE p2.group_key = p.group_key
             )''',
        (cutoff,)
    ).fetchall()
    result = {}
    for row in rows:
        pressure = _num(row.get('pressure'))
        if pressure is None:
            continue
        result[row['group_key']] = {
            'group_key': row['group_key'],
            'as_of_date': str(row['as_of_date'])[:10],
            'pressure': pressure,
            'label': row.get('label'),
            'driver_component': _num(row.get('driver_component')),
            'news_component': _num(row.get('news_component')),
            'move_component': _num(row.get('move_component')),
            'top_driver': row.get('top_driver'),
            'top_driver_impact': _num(row.get('top_driver_impact')),
        }
    return result


def attach_sector_pressure(db, candidates):
    """Returns NEW dicts (inputs are not mutated) with 'sector_group' and
    'sector_pressure' (float -1..1, or None when there is no fresh pressure
    for the candidate's group) added -- read by nns_score.compute_nns_score
    and suggestion_engine.is_suggestion_eligible. Fail-open: any error
    reading the table (not yet created, DB hiccup) returns the candidates
    unchanged apart from sector_pressure=None, never raises -- this layer
    is supplementary and must not be able to break suggestion generation."""
    rows = [dict(c) for c in candidates]
    try:
        pressures = get_sector_pressure_by_group(db)
    except Exception as e:
        print(f'attach_sector_pressure failed (continuing without it): {e}')
        pressures = {}
    for row in rows:
        group = group_for_industry(row.get('industry'))
        row['sector_group'] = group
        entry = pressures.get(group)
        row['sector_pressure'] = entry['pressure'] if entry else None
    return rows


# ---------------------------------------------------------------------------
# Daily job
# ---------------------------------------------------------------------------

def _compute_group_changes(industry_changes):
    """{group_key: mean of its industries' avg 1-day % change}."""
    buckets = {}
    for industry, change in industry_changes.items():
        buckets.setdefault(group_for_industry(industry), []).append(change)
    return {group: sum(vals) / len(vals) for group, vals in buckets.items()}


def sync_market_signals(db, fetch_prices_fn=None, fetch_news_fn=None, sleep_seconds=REQUEST_DELAY_SECONDS,
                        send_alerts=True, today=None):
    """Daily job (see routes.py /stocks/signals/sync). Steps, each
    isolated so one failure doesn't stop the rest:
      1. fetch + store each driver's price series -> shock stats;
      2. fetch + store driver news and per-sector news;
      3. compute and store a pressure score per sector group;
      4. email the owner about any driver shock.
    Returns {'drivers_synced', 'news_scopes_synced', 'groups_scored',
    'alerts_sent', 'failures': [...]}."""
    fetch_prices = fetch_prices_fn or fetch_price_series
    fetch_news = fetch_news_fn or (lambda query: fetch_news_for_company(query, None))
    today = today or datetime.now(IST).date()
    failures = []

    # 1. Driver prices
    fresh_signals = {}
    for key, driver in DRIVERS.items():
        try:
            series = fetch_prices(driver['yahoo'])
            shock = compute_driver_shock([close for _date, close in series])
            if shock is None:
                raise MarketSignalFetchError(f'No usable closes for {driver["yahoo"]}')
            _store_driver_signal(db, key, series[-1][0], shock)
            fresh_signals[key] = shock
        except Exception as exc:
            failures.append({'scope': f'driver:{key}', 'error': str(exc)})
            print(f'Market driver sync failed for {key}: {exc}')
        if sleep_seconds:
            time.sleep(sleep_seconds)

    # Drivers whose fetch failed fall back to their last stored reading.
    try:
        signals = get_latest_driver_signals(db)
    except Exception:
        signals = {}
    signals.update(fresh_signals)

    # 2. News: per sector group (scored) and per driver (display only).
    fresh_articles = {}
    news_scopes = [(rule['group'], rule['query']) for rule in all_rules()]
    news_scopes += [(f'driver:{key}', driver['query']) for key, driver in DRIVERS.items()]
    news_scopes_synced = 0
    for scope_key, query in news_scopes:
        try:
            articles = fetch_news(query)
            _store_scope_news(db, scope_key, articles)
            fresh_articles[scope_key] = articles
            news_scopes_synced += 1
        except Exception as exc:
            failures.append({'scope': f'news:{scope_key}', 'error': str(exc)})
            print(f'Sector news sync failed for {scope_key}: {exc}')
        if sleep_seconds:
            time.sleep(sleep_seconds)

    # 3. Pressure per group
    try:
        group_changes = _compute_group_changes(compute_industry_changes(db))
    except Exception as exc:
        group_changes = {}
        failures.append({'scope': 'industry_changes', 'error': str(exc)})

    as_of = today.isoformat()
    pressures = {}
    for rule in all_rules():
        group = rule['group']
        try:
            articles = fresh_articles.get(group)
            if articles is None:
                articles = get_sector_headlines(db, group)
            sentiment = compute_company_sentiment(articles)
            sentiment_score = sentiment['score'] if sentiment['headlines_scored'] else None
            result = compute_sector_pressure(rule, signals, sentiment_score, group_changes.get(group))
            _store_pressure(db, group, as_of, result)
            pressures[group] = result
        except Exception as exc:
            failures.append({'scope': f'pressure:{group}', 'error': str(exc)})
            print(f'Sector pressure compute failed for {group}: {exc}')

    # 4. Shock alerts
    alerts_sent = 0
    if send_alerts:
        try:
            alerts_sent = send_driver_shock_alerts(db, signals, pressures, today=today)
        except Exception as exc:
            failures.append({'scope': 'alerts', 'error': str(exc)})
            print(f'Driver shock alerting failed: {exc}')

    return {
        'drivers_synced': len(fresh_signals),
        'news_scopes_synced': news_scopes_synced,
        'groups_scored': len(pressures),
        'alerts_sent': alerts_sent,
        'failures': failures,
    }


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

def find_driver_shocks(signals):
    """[(driver_key, signal), ...] for drivers whose |zscore| >=
    SHOCK_ZSCORE, largest shock first."""
    shocks = [
        (key, sig) for key, sig in signals.items()
        if key in DRIVERS and sig.get('zscore') is not None and abs(sig['zscore']) >= SHOCK_ZSCORE
    ]
    shocks.sort(key=lambda pair: abs(pair[1]['zscore']), reverse=True)
    return shocks


def groups_affected_by_driver(driver_key, direction):
    """[(group_key, sensitivity, hurt: bool), ...] for sector groups with
    |sensitivity| >= ALERT_MIN_SENSITIVITY to this driver. direction is +1
    (driver rose) or -1 (fell); hurt = sensitivity*direction < 0."""
    affected = []
    for rule in all_rules():
        sensitivity = rule['drivers'].get(driver_key)
        if sensitivity is not None and abs(sensitivity) >= ALERT_MIN_SENSITIVITY:
            affected.append((rule['group'], sensitivity, sensitivity * direction < 0))
    return affected


def _exposed_companies(db, group_keys):
    """{group_key: [company label, ...]} for active watchlist companies
    whose industry maps to one of group_keys."""
    wanted = set(group_keys)
    rows = db.execute(
        '''SELECT w.symbol, w.name AS company_name, u.industry
           FROM stock_watchlist w
           LEFT JOIN stock_universe u ON u.symbol = w.symbol AND u.exchange = w.exchange
           WHERE w.is_active = 1'''
    ).fetchall()
    exposed = {}
    for row in rows:
        group = group_for_industry(row.get('industry'))
        if group in wanted:
            exposed.setdefault(group, []).append(row.get('company_name') or row['symbol'])
    return exposed


def build_shock_alert(driver_key, signal, affected, exposed, pressures):
    """(subject, body) for one driver shock. affected: groups_affected_by_driver
    output; exposed: _exposed_companies output; pressures: {group: result}."""
    driver = DRIVERS[driver_key]
    change = signal.get('change_1d_pct')
    z = signal['zscore']
    direction_word = 'up' if z > 0 else 'down'
    change_text = f'{change:+.2f}%' if change is not None else 'n/a'
    subject = f'[StoqBell] {driver["label"]} shock: {direction_word} {change_text} (z={z:+.1f})'

    lines = [
        f'{driver["label"]} moved {change_text} in a day ({z:+.1f} standard deviations vs its recent volatility).',
        '',
    ]
    hurt = [a for a in affected if a[2]]
    helped = [a for a in affected if not a[2]]
    for title, entries in (('Likely HURT', hurt), ('Likely HELPED', helped)):
        if not entries:
            continue
        lines.append(f'{title}:')
        for group, _sensitivity, _hurt in entries:
            pressure = pressures.get(group)
            tag = f' (sector pressure {pressure["pressure"]:+.2f}, {pressure["label"]})' if pressure else ''
            if pressure and pressure['pressure'] <= GATE_PRESSURE:
                tag += ' -- new buy suggestions suppressed'
            names = exposed.get(group, [])
            names_text = ', '.join(names[:8]) + (f' +{len(names) - 8} more' if len(names) > 8 else '') if names else 'no watchlist names'
            lines.append(f'  - {group.replace("_", " ")}{tag}: {names_text}')
        lines.append('')
    lines.append('This is a risk flag from the market-drivers layer, not a trade signal.')
    return subject, '\n'.join(lines)


def send_driver_shock_alerts(db, signals, pressures, today=None):
    """Emails the owner once per driver per direction per day for each
    shock (dedup via market_event_alerts.alert_key). Returns the number of
    emails sent. A send that fails (e.g. email not configured) is NOT
    recorded, so a later run retries it."""
    from stoqbell.utils.stock_alerting import send_alert_email

    today = today or datetime.now(IST).date()
    sent = 0
    for key, signal in find_driver_shocks(signals):
        direction = 1 if signal['zscore'] > 0 else -1
        alert_key = f'{today.isoformat()}:{key}:{"up" if direction > 0 else "down"}'
        existing = db.execute('SELECT id FROM market_event_alerts WHERE alert_key = ?', (alert_key,)).fetchone()
        if existing:
            continue
        affected = groups_affected_by_driver(key, direction)
        if not affected:
            continue
        exposed = _exposed_companies(db, [a[0] for a in affected])
        subject, body = build_shock_alert(key, signal, affected, exposed, pressures)
        if send_alert_email(subject, body):
            db.execute(
                'INSERT INTO market_event_alerts (alert_key, detail) VALUES (?, ?) ON CONFLICT (alert_key) DO NOTHING',
                (alert_key, subject)
            )
            db.commit()
            sent += 1
    return sent


# ---------------------------------------------------------------------------
# UI summary
# ---------------------------------------------------------------------------

def get_market_drivers_summary(db, limit_sectors=6):
    """Data for the Stocks home "Market Drivers" card. Fail-open: any error
    -> {'available': False}.

    {'available': True, 'drivers': [{'key','label','value','change_1d_pct',
    'zscore','is_shock'}], 'headwinds': [...], 'tailwinds': [...]} where the
    sector lists hold {'group','title','pressure','top_driver_label'} for up
    to limit_sectors groups with a non-neutral label, most extreme first."""
    try:
        signals = get_latest_driver_signals(db)
        pressures = get_sector_pressure_by_group(db)
    except Exception as e:
        print(f'get_market_drivers_summary failed: {e}')
        return {'available': False}
    if not signals and not pressures:
        return {'available': False}

    drivers = []
    for key, driver in DRIVERS.items():
        sig = signals.get(key)
        if not sig:
            continue
        z = sig.get('zscore')
        drivers.append({
            'key': key, 'label': driver['label'], 'value': sig['value'],
            'change_1d_pct': sig['change_1d_pct'], 'zscore': z,
            'is_shock': z is not None and abs(z) >= SHOCK_ZSCORE,
        })

    def _entry(p):
        rule = rule_for_group(p['group_key'])
        top = DRIVERS.get(p['top_driver'] or '', {}).get('label')
        return {
            'group': p['group_key'],
            'title': p['group_key'].replace('_', ' ').title(),
            'pressure': p['pressure'],
            'top_driver_label': top,
            'gated': p['pressure'] <= GATE_PRESSURE,
            'known': rule is not None,
        }

    headwinds = sorted((p for p in pressures.values() if p['label'] == 'headwind'), key=lambda p: p['pressure'])
    tailwinds = sorted((p for p in pressures.values() if p['label'] == 'tailwind'), key=lambda p: -p['pressure'])
    return {
        'available': True,
        'drivers': drivers,
        'headwinds': [_entry(p) for p in headwinds[:limit_sectors]],
        'tailwinds': [_entry(p) for p in tailwinds[:limit_sectors]],
    }


# ---------------------------------------------------------------------------
# Super-admin report
# ---------------------------------------------------------------------------

def get_market_drivers_report(db, max_stocks=80):
    """Everything the super-admin Market Drivers page shows. Fail-open per
    section: a failing query empties that section, never the page.

    {'last_sync': ISO str|None, 'drivers': [...], 'sectors': [...],
     'stocks': [...]}
      drivers: {'key','label','value','change_1d_pct','change_5d_pct','zscore','is_shock','as_of_date','headlines'}
      sectors: {'group','title','pressure','label','driver_component','news_component',
                'move_component','top_driver_label','gated','headlines','as_of_date'}
      stocks:  {'watchlist_id','symbol','company_name','industry','group','pressure','sector_label',
                'cross_status','price','p_rise','p_decline'} -- the curated watchlist,
               most-exposed sector first, capped at max_stocks. p_rise/p_decline come
               from sector_drivers.estimate_move_probabilities (heuristic, display only)."""
    report = {'last_sync': None, 'drivers': [], 'sectors': [], 'stocks': []}

    try:
        row = db.execute(
            "SELECT last_success_at FROM stock_job_runs WHERE source = 'market_signals_sync'"
        ).fetchone()
        if row and row.get('last_success_at'):
            report['last_sync'] = str(row['last_success_at'])
    except Exception as e:
        print(f'market drivers report: last_sync failed: {e}')

    def _headlines(scope_key, limit=3):
        try:
            return [dict(h) for h in get_sector_headlines(db, scope_key, limit)]
        except Exception:
            return []

    try:
        signals = get_latest_driver_signals(db)
    except Exception as e:
        print(f'market drivers report: drivers failed: {e}')
        signals = {}
    for key, driver in DRIVERS.items():
        sig = signals.get(key)
        if not sig:
            continue
        z = sig.get('zscore')
        report['drivers'].append({
            'key': key, 'label': driver['label'], 'value': sig['value'],
            'change_1d_pct': sig['change_1d_pct'], 'change_5d_pct': sig['change_5d_pct'],
            'zscore': z, 'is_shock': z is not None and abs(z) >= SHOCK_ZSCORE,
            'as_of_date': sig['as_of_date'], 'headlines': _headlines(f'driver:{key}', 2),
        })

    try:
        pressures = get_sector_pressure_by_group(db)
    except Exception as e:
        print(f'market drivers report: sectors failed: {e}')
        pressures = {}
    for p in sorted(pressures.values(), key=lambda p: p['pressure']):
        report['sectors'].append({
            'group': p['group_key'], 'title': p['group_key'].replace('_', ' ').title(),
            'pressure': p['pressure'], 'label': p.get('label') or 'neutral',
            'driver_component': p['driver_component'], 'news_component': p['news_component'],
            'move_component': p['move_component'],
            'top_driver_label': DRIVERS.get(p['top_driver'] or '', {}).get('label'),
            'gated': p['pressure'] <= GATE_PRESSURE, 'as_of_date': p['as_of_date'],
            'headlines': _headlines(p['group_key']),
        })

    try:
        rows = db.execute(
            '''SELECT w.id AS watchlist_id, w.symbol, w.company_name, u.industry,
                      u.live_price, i.cross_status,
                      (SELECT d.close FROM stock_daily_data d WHERE d.watchlist_id = w.id
                        ORDER BY d.trade_date DESC LIMIT 1) AS latest_close
               FROM stock_watchlist w
               LEFT JOIN stock_universe u ON u.symbol = w.symbol AND u.exchange = w.exchange
               LEFT JOIN stock_indicators i ON i.watchlist_id = w.id
                   AND i.calc_date = (SELECT MAX(i2.calc_date) FROM stock_indicators i2 WHERE i2.watchlist_id = w.id)'''
        ).fetchall()
    except Exception as e:
        print(f'market drivers report: stocks failed: {e}')
        rows = []
    stocks = []
    for r in rows:
        group = group_for_industry(r.get('industry'))
        entry = pressures.get(group)
        if not entry:
            continue
        p_rise, p_decline = estimate_move_probabilities(entry['pressure'], r.get('cross_status'))
        stocks.append({
            'watchlist_id': r['watchlist_id'], 'symbol': r['symbol'], 'company_name': r.get('company_name'),
            'industry': r.get('industry'), 'group': group,
            'pressure': entry['pressure'], 'sector_label': entry.get('label') or 'neutral',
            'cross_status': r.get('cross_status'),
            'price': _num(r.get('live_price')) or _num(r.get('latest_close')),
            'p_rise': p_rise, 'p_decline': p_decline,
        })
    stocks.sort(key=lambda s: -abs(s['pressure']))
    report['stocks'] = stocks[:max_stocks]
    return report
