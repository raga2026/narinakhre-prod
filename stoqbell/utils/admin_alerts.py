"""Raghav-only stock alerts (raga2020@gmail.com, utils.auto_trader.STOP_LOSS_ALERT_EMAIL
-- the same address his other auto-trader alerts already use), separate
from anything customer-facing:

1. record_and_send_highly_recommended_alerts -- once a day (called from
   app.py's /stocks/suggestions/send-daily-email, the same job that
   generates the customer Pick of the Day), emails Raghav about EVERY
   golden/silver-tier ("Highly Recommended") candidate that day, uncapped --
   not just the single stock that becomes the customer Pick of the Day.
   Stored in this module's own stock_admin_alerts table, deliberately
   separate from stock_suggestions, so being alerted to Raghav never counts
   against a stock's customer-facing repeat-window/cooldown (see
   utils.suggestion_engine.get_all_highly_recommended_today's own docstring).

2. find_and_notify_intraday_target_hits -- the every-5-minutes,
   market-hours-only intraday check (app.py's
   /stocks/notifications/check-intraday-hits). Unlike every other
   price/target check in this codebase (all explicitly once-a-day against
   yesterday's synced close -- see e.g. suggestion_engine.find_pending_target_hit_suggestions's
   own docstring), this one fetches a LIVE Kite quote and checks it against
   both stock_suggestions (customer recommendations) and stock_admin_alerts
   (Raghav's own alerts above) for a target hit, entirely independent of
   the daily stock_daily_data sync -- see its own docstring for why it
   marks a stock_suggestions hit differently than a stock_admin_alerts one.
"""
from datetime import date

from stoqbell.utils.auto_trader import STOP_LOSS_ALERT_EMAIL
from stoqbell.utils.kite_client import KiteClient
from stoqbell.utils.suggestion_email import send_highly_recommended_alert_email, send_intraday_target_hit_alert_email
from stoqbell.utils.suggestion_engine import get_all_highly_recommended_today

ADMIN_ALERTS_TABLE_SQL = [
    '''CREATE TABLE IF NOT EXISTS stock_admin_alerts (
        id BIGSERIAL PRIMARY KEY,
        watchlist_id BIGINT NOT NULL REFERENCES stock_watchlist(id),
        alert_date DATE NOT NULL,
        buy_price NUMERIC(12,2),
        target_sell_price NUMERIC(12,2),
        stop_loss_price NUMERIC(12,2),
        nns_score NUMERIC(6,4),
        nns_tier TEXT CHECK (nns_tier IS NULL OR nns_tier IN ('golden', 'silver', 'bronze')),
        pattern_name TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(watchlist_id, alert_date)
    )'''
]


def initialize_admin_alerts_table_if_needed(client):
    for sql in ADMIN_ALERTS_TABLE_SQL:
        try:
            client.rpc('execute_sql', {'query': sql}).execute()
        except Exception as e:
            print(f'Admin alerts table init warning (may already exist): {e}')


def record_and_send_highly_recommended_alerts(db):
    """Called once a day, right after generate_daily_suggestions, from the
    same job that emails the customer Pick of the Day (see app.py's
    /stocks/suggestions/send-daily-email). Every golden/silver candidate
    from get_all_highly_recommended_today gets upserted into
    stock_admin_alerts AND its own email to Raghav -- no cap, and a re-run
    of this same day's job re-sends rather than silently suppressing
    (upsert-then-always-email, not upsert-only-if-new), matching "no matter
    how many times it comes."

    Returns {'alerted': [...]} -- each entry the same dict shape
    get_all_highly_recommended_today returns, for the caller to log."""
    candidates = get_all_highly_recommended_today(db)
    today = date.today().isoformat()

    alerted = []
    for c in candidates:
        db.execute(
            '''INSERT INTO stock_admin_alerts
                   (watchlist_id, alert_date, buy_price, target_sell_price, stop_loss_price,
                    nns_score, nns_tier, pattern_name, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')
               ON CONFLICT (watchlist_id, alert_date) DO UPDATE SET
                   buy_price = EXCLUDED.buy_price,
                   target_sell_price = EXCLUDED.target_sell_price,
                   stop_loss_price = EXCLUDED.stop_loss_price,
                   nns_score = EXCLUDED.nns_score,
                   nns_tier = EXCLUDED.nns_tier,
                   pattern_name = EXCLUDED.pattern_name''',
            (c['watchlist_id'], today, c['buy_price'], c['target_sell_price'], c['stop_loss_price'],
             c['score'], c['nns_tier'], c['pattern_name'])
        )
        db.commit()
        send_highly_recommended_alert_email(STOP_LOSS_ALERT_EMAIL, c)
        alerted.append(c)

    return {'alerted': alerted}


def _instrument_key(exchange, symbol):
    return f'{exchange}:{symbol}'


def find_and_notify_intraday_target_hits(db, kite_client=None):
    """The every-5-minutes, market-hours-only intraday check (see app.py's
    /stocks/notifications/check-intraday-hits, gated there by
    is_trading_day()/is_within_trading_hours()). Checks two independent
    sources of still-open target prices against a LIVE Kite quote (never
    stock_daily_data -- that stays a pure end-of-day close, see this
    module's own docstring):

    - stock_suggestions rows with status='pending' AND
      intraday_alert_sent_at IS NULL -- on a hit, sets ONLY
      intraday_alert_sent_at=NOW(), never `status`. `status` is owned
      exclusively by the existing once-daily customer-facing check
      (suggestion_engine.find_pending_target_hit_suggestions, which only
      looks at status='pending') -- if this intraday check flipped `status`
      itself, that once-daily job would silently stop ever notifying
      customers about this stock, since it would no longer see it as
      pending. Using a separate column means Raghav finds out promptly
      AND the customer pipeline keeps working completely unaware this ran.
    - stock_admin_alerts rows with status='pending' -- on a hit, sets
      status='target_hit' directly; nothing else reads this table, so no
      collision risk here.

    Both checked in ONE batched Kite ltp() call (see KiteClient.fetch_ltp_batch)
    across every distinct symbol from both sources, not one quote per stock.
    Sends ONE email (send_intraday_target_hit_alert_email) bundling every
    hit found this run, only if at least one was found -- a stock already
    marked (intraday_alert_sent_at set, or status != 'pending') is never
    re-detected, so this never repeats for the same stock on a later
    5-minute check.

    Returns {'checked': N, 'hits': [...]}."""
    kite_client = kite_client or KiteClient(db=db)

    pending_suggestions = db.execute(
        '''SELECT s.id, w.id AS watchlist_id, w.symbol, w.exchange, w.name AS company_name,
                  s.buy_price, s.target_sell_price
           FROM stock_suggestions s
           JOIN stock_watchlist w ON w.id = s.watchlist_id
           WHERE s.status = 'pending' AND s.intraday_alert_sent_at IS NULL'''
    ).fetchall()

    pending_admin_alerts = db.execute(
        '''SELECT a.id, w.id AS watchlist_id, w.symbol, w.exchange, w.name AS company_name,
                  a.buy_price, a.target_sell_price
           FROM stock_admin_alerts a
           JOIN stock_watchlist w ON w.id = a.watchlist_id
           WHERE a.status = 'pending' '''
    ).fetchall()

    instrument_keys = sorted({
        _instrument_key(row['exchange'], row['symbol'])
        for row in list(pending_suggestions) + list(pending_admin_alerts)
    })
    live_prices = kite_client.fetch_ltp_batch(instrument_keys) if instrument_keys else {}

    hits = []

    for row in pending_suggestions:
        live_price = live_prices.get(_instrument_key(row['exchange'], row['symbol']))
        if live_price is None or row['target_sell_price'] is None or live_price < row['target_sell_price']:
            continue
        db.execute(
            'UPDATE stock_suggestions SET intraday_alert_sent_at = NOW() WHERE id = ?',
            (row['id'],)
        )
        db.commit()
        hits.append({
            'source': 'suggestion', 'symbol': row['symbol'], 'exchange': row['exchange'],
            'company_name': row['company_name'], 'buy_price': row['buy_price'],
            'target_sell_price': row['target_sell_price'], 'live_price': live_price,
        })

    for row in pending_admin_alerts:
        live_price = live_prices.get(_instrument_key(row['exchange'], row['symbol']))
        if live_price is None or row['target_sell_price'] is None or live_price < row['target_sell_price']:
            continue
        db.execute(
            "UPDATE stock_admin_alerts SET status='target_hit' WHERE id = ?",
            (row['id'],)
        )
        db.commit()
        hits.append({
            'source': 'admin_alert', 'symbol': row['symbol'], 'exchange': row['exchange'],
            'company_name': row['company_name'], 'buy_price': row['buy_price'],
            'target_sell_price': row['target_sell_price'], 'live_price': live_price,
        })

    if hits:
        send_intraday_target_hit_alert_email(STOP_LOSS_ALERT_EMAIL, hits)

    return {'checked': len(pending_suggestions) + len(pending_admin_alerts), 'hits': hits}
