"""Dry-run auto-trading for Nari Nakhre Stocks' Pick of the Day -- super_admin
only (see app.py's /stocks/auto-trader routes). Simulates buying a fixed
rupee budget of every new Pick of the Day at its recorded buy_price.

The two exits are NOT symmetric, by explicit instruction:
  - Reaching target_sell_price auto-closes the position immediately (see
    reconcile_open_trades) -- a win needs no human judgment call.
  - Dropping to/through stop_loss_price does NOT auto-close anything --
    it moves the trade to 'stop_loss_pending' and the caller (app.py's
    reconcile route) emails a notification, and a super_admin has to
    explicitly confirm_stop_loss_sell (book the loss at the price that
    triggered it) or cancel_stop_loss_sell (put it back to 'open' and keep
    holding) from the dashboard. This is deliberate risk-management
    friction: an automatic profit-take is fine to trust, an automatic
    loss-realization is not.

Deliberately does NOT place any real Kite order anywhere in this module --
going live needs its own separate, careful pass (bracket/GTT order
placement, fill confirmation via the Kite postback, handling rejected
orders/insufficient margin/circuit limits), none of which exists yet. Every
trade this module creates has mode='dry_run' hardcoded; there is currently
no code path that ever sets mode='live'."""

MODE_DRY_RUN = 'dry_run'
DEFAULT_BUDGET_PER_TRADE = 25000
# Total virtual capital the simulation is allowed to have deployed at once
# (see get_deployed_capital/compute_available_funds) -- 8x the default
# budget per trade, room for 8 concurrent positions before a new buy has
# to wait for one to close and free its budget back up.
DEFAULT_TOTAL_CAPITAL = 200000
STOP_LOSS_ALERT_EMAIL = 'raga2020@gmail.com'

STOCK_AUTO_TRADE_TABLES_SQL = [
    '''CREATE TABLE IF NOT EXISTS stock_auto_trades (
        id BIGSERIAL PRIMARY KEY,
        suggestion_id BIGINT NOT NULL REFERENCES stock_suggestions(id),
        watchlist_id BIGINT NOT NULL REFERENCES stock_watchlist(id),
        symbol TEXT NOT NULL,
        exchange TEXT NOT NULL,
        mode TEXT NOT NULL DEFAULT 'dry_run' CHECK (mode IN ('dry_run', 'live')),
        status TEXT NOT NULL DEFAULT 'open'
            CHECK (status IN ('open', 'target_hit', 'stop_loss_pending', 'stopped_out')),
        budget_amount NUMERIC(12,2) NOT NULL,
        quantity INTEGER NOT NULL,
        buy_price NUMERIC(12,2) NOT NULL,
        target_sell_price NUMERIC(12,2),
        stop_loss_price NUMERIC(12,2),
        stop_loss_triggered_price NUMERIC(12,2),
        stop_loss_triggered_at TIMESTAMPTZ,
        exit_price NUMERIC(12,2),
        pnl_amount NUMERIC(12,2),
        pnl_pct NUMERIC(8,2),
        opened_at TIMESTAMPTZ DEFAULT NOW(),
        closed_at TIMESTAMPTZ,
        UNIQUE(suggestion_id)
    )''',
    '''CREATE TABLE IF NOT EXISTS stock_auto_trade_settings (
        id INTEGER PRIMARY KEY DEFAULT 1,
        enabled INTEGER NOT NULL DEFAULT 0,
        budget_per_trade NUMERIC(12,2) NOT NULL DEFAULT 25000,
        total_capital NUMERIC(14,2) NOT NULL DEFAULT 200000,
        CONSTRAINT single_row CHECK (id = 1)
    )''',
]

# Additive-only, same convention as every other Stocks table in this
# codebase -- covers the case where stock_auto_trade_settings was already
# created (by an earlier version of the CREATE TABLE above) before
# total_capital existed.
STOCK_AUTO_TRADE_ALTER_SQL = [
    'ALTER TABLE stock_auto_trade_settings ADD COLUMN IF NOT EXISTS total_capital NUMERIC(14,2) NOT NULL DEFAULT 200000',
]


def initialize_auto_trade_tables_if_needed(client):
    for sql in STOCK_AUTO_TRADE_TABLES_SQL + STOCK_AUTO_TRADE_ALTER_SQL:
        try:
            client.rpc('execute_sql', {'query': sql}).execute()
        except Exception as e:
            print(f'Auto-trade table init warning (may already exist): {e}')


# --- pure logic --------------------------------------------------------

def compute_quantity(budget_amount, buy_price):
    """Whole shares affordable within budget_amount at buy_price -- floor
    division, so a budget that doesn't stretch to even one share (or a
    missing/zero buy_price) returns 0, not a fractional or negative
    quantity."""
    if not budget_amount or not buy_price or buy_price <= 0:
        return 0
    return int(budget_amount // buy_price)


def compute_pnl(buy_price, exit_price, quantity):
    """(pnl_amount, pnl_pct) for closing quantity shares bought at
    buy_price when exit_price is reached. pnl_pct is the price move itself
    (exit vs. buy), independent of quantity -- matches how target/stop are
    already expressed as prices, not amounts."""
    pnl_amount = round((exit_price - buy_price) * quantity, 2)
    pnl_pct = round((exit_price - buy_price) / buy_price * 100, 2) if buy_price else None
    return pnl_amount, pnl_pct


def determine_exit(latest_price, target_sell_price, stop_loss_price):
    """'target_hit' if latest_price has reached target_sell_price,
    'stopped_out' if it's dropped to/through stop_loss_price (checked
    AFTER target -- reaching target first always reads as the good outcome
    even in the degenerate case where both conditions are somehow true at
    once), else None (still open). None if latest_price itself is missing
    -- nothing to compare yet. Note: despite the name, a 'stopped_out'
    result from this function does NOT mean the trade actually closes --
    see reconcile_open_trades, which routes 'stopped_out' to
    'stop_loss_pending' for manual confirmation instead of closing it
    directly. This function only reports which price level was reached,
    not what should happen as a result."""
    if latest_price is None:
        return None
    if target_sell_price is not None and latest_price >= target_sell_price:
        return 'target_hit'
    if stop_loss_price is not None and latest_price <= stop_loss_price:
        return 'stopped_out'
    return None


# --- DB orchestration ----------------------------------------------------

def get_auto_trade_settings(db):
    row = db.execute(
        'SELECT enabled, budget_per_trade, total_capital FROM stock_auto_trade_settings WHERE id=1'
    ).fetchone()
    if not row:
        return {'enabled': False, 'budget_per_trade': DEFAULT_BUDGET_PER_TRADE, 'total_capital': DEFAULT_TOTAL_CAPITAL}
    return {
        'enabled': bool(row['enabled']),
        'budget_per_trade': float(row['budget_per_trade']),
        'total_capital': float(row['total_capital']),
    }


def set_auto_trade_settings(db, enabled, budget_per_trade, total_capital):
    db.execute(
        '''INSERT INTO stock_auto_trade_settings (id, enabled, budget_per_trade, total_capital)
           VALUES (1, ?, ?, ?)
           ON CONFLICT (id) DO UPDATE SET
               enabled = EXCLUDED.enabled,
               budget_per_trade = EXCLUDED.budget_per_trade,
               total_capital = EXCLUDED.total_capital''',
        (1 if enabled else 0, budget_per_trade, total_capital)
    )
    db.commit()


def get_deployed_capital(db):
    """Sum of budget_amount across every trade currently tying up virtual
    capital -- 'open' (bought, not yet exited) and 'stop_loss_pending'
    (bought, stop-loss triggered but not yet confirmed/cancelled -- still
    holding the position either way, so its budget is still deployed).
    Closed trades ('target_hit', 'stopped_out') are excluded -- their
    capital has been returned to the pool, which is exactly what lets
    compute_available_funds recover on its own once a position closes,
    with no separate 'paused'/'resumed' flag to manage anywhere."""
    row = db.execute(
        "SELECT COALESCE(SUM(budget_amount), 0) AS deployed FROM stock_auto_trades "
        "WHERE mode='dry_run' AND status IN ('open', 'stop_loss_pending')"
    ).fetchone()
    return float(row['deployed']) if row else 0.0


def compute_available_funds(total_capital, deployed_capital):
    return round(total_capital - deployed_capital, 2)


def open_auto_trade_if_enabled(db, created_suggestion):
    """Called once per newly-created suggestion (see
    generate_daily_suggestions's 'created' list) -- opens a dry-run
    simulated position sized to the configured budget, unless:
      - auto-trading is currently disabled, or
      - this suggestion doesn't have enough of a price/target/stop to size
        and track a trade against, or
      - there isn't enough virtual capital left (see get_deployed_capital/
        compute_available_funds) to cover another budget_per_trade-sized
        position right now.
    None of these are errors -- buying simply pauses itself whenever funds
    are insufficient, and resumes on its own the next time this is called
    once an earlier position has closed and freed its budget back into the
    pool (compute_available_funds is recomputed fresh on every call, not
    read from some stored 'paused' flag, so there's nothing to explicitly
    "resume" -- it just naturally allows the next buy through once the
    numbers allow it again).

    Idempotent: stock_auto_trades has a UNIQUE constraint on suggestion_id,
    so calling this twice for the same suggestion is a silent no-op the
    second time, same ON CONFLICT DO NOTHING pattern used elsewhere in this
    codebase for exactly this reason."""
    settings = get_auto_trade_settings(db)
    if not settings['enabled']:
        return None

    buy_price = created_suggestion.get('buy_price')
    quantity = compute_quantity(settings['budget_per_trade'], buy_price)
    if quantity <= 0:
        return None

    available = compute_available_funds(settings['total_capital'], get_deployed_capital(db))
    if settings['budget_per_trade'] > available:
        return None

    db.execute(
        '''INSERT INTO stock_auto_trades
               (suggestion_id, watchlist_id, symbol, exchange, mode, status,
                budget_amount, quantity, buy_price, target_sell_price, stop_loss_price)
           VALUES (?, ?, ?, ?, 'dry_run', 'open', ?, ?, ?, ?, ?)
           ON CONFLICT (suggestion_id) DO NOTHING''',
        (created_suggestion['suggestion_id'], created_suggestion['watchlist_id'],
         created_suggestion['symbol'], created_suggestion.get('exchange'),
         settings['budget_per_trade'], quantity, buy_price,
         created_suggestion.get('target_sell_price'), created_suggestion.get('stop_loss_price'))
    )
    db.commit()
    return quantity


def reconcile_open_trades(db):
    """Checks every 'open' dry-run trade against its watchlist company's
    latest synced close (see determine_exit):
      - target_hit -> closes immediately, P&L computed and recorded.
      - stopped_out -> does NOT close -- moves to 'stop_loss_pending' with
        the triggering price/time recorded, and is excluded from further
        checks here (the WHERE clause below only ever looks at 'open'
        rows) until a super_admin calls confirm_stop_loss_sell or
        cancel_stop_loss_sell. Cancelling puts it back to 'open', so a
        later dip can trigger this again -- it isn't a one-shot dismissal.

    Meant to run daily, after that day's price sync, alongside the rest of
    the Stocks cron pipeline. Returns {'checked', 'target_hit',
    'stop_loss_pending': [trade dicts]} -- the caller (app.py's reconcile
    route) is responsible for emailing STOP_LOSS_ALERT_EMAIL for each entry
    in 'stop_loss_pending', since this module doesn't send email itself
    (same DB-orchestration-only split as generate_daily_suggestions, whose
    caller sends the actual email too)."""
    open_trades = db.execute(
        '''SELECT t.id, t.symbol, t.exchange, t.watchlist_id, t.buy_price, t.target_sell_price,
                  t.stop_loss_price, t.quantity, d.close AS latest_price
           FROM stock_auto_trades t
           LEFT JOIN stock_daily_data d ON d.watchlist_id = t.watchlist_id
               AND d.trade_date = (SELECT MAX(d2.trade_date) FROM stock_daily_data d2 WHERE d2.watchlist_id = t.watchlist_id)
           WHERE t.status = 'open' AND t.mode = 'dry_run' '''
    ).fetchall()

    target_hit_count = 0
    pending_stop_losses = []
    for trade in open_trades:
        outcome = determine_exit(trade.get('latest_price'), trade.get('target_sell_price'), trade.get('stop_loss_price'))
        if outcome is None:
            continue
        if outcome == 'target_hit':
            exit_price = trade['latest_price']
            pnl_amount, pnl_pct = compute_pnl(trade['buy_price'], exit_price, trade['quantity'])
            db.execute(
                '''UPDATE stock_auto_trades
                   SET status='target_hit', exit_price=?, pnl_amount=?, pnl_pct=?, closed_at=NOW()
                   WHERE id=?''',
                (exit_price, pnl_amount, pnl_pct, trade['id'])
            )
            db.commit()
            target_hit_count += 1
        else:  # 'stopped_out' -- needs manual confirmation, does not close
            db.execute(
                '''UPDATE stock_auto_trades
                   SET status='stop_loss_pending', stop_loss_triggered_price=?, stop_loss_triggered_at=NOW()
                   WHERE id=?''',
                (trade['latest_price'], trade['id'])
            )
            db.commit()
            pending_stop_losses.append({**trade, 'stop_loss_triggered_price': trade['latest_price']})

    return {'checked': len(open_trades), 'target_hit': target_hit_count, 'stop_loss_pending': pending_stop_losses}


def confirm_stop_loss_sell(db, trade_id):
    """Books the loss -- closes a 'stop_loss_pending' trade at the price
    that originally triggered it (not a freshly re-fetched price, so the
    outcome matches exactly what was flagged for review, regardless of how
    long the decision took). Returns True if a pending trade was found and
    closed, False otherwise (already resolved, or never existed)."""
    trade = db.execute(
        'SELECT id, buy_price, quantity, stop_loss_triggered_price FROM stock_auto_trades '
        "WHERE id=? AND status='stop_loss_pending'",
        (trade_id,)
    ).fetchone()
    if not trade:
        return False
    exit_price = trade['stop_loss_triggered_price']
    pnl_amount, pnl_pct = compute_pnl(trade['buy_price'], exit_price, trade['quantity'])
    db.execute(
        '''UPDATE stock_auto_trades
           SET status='stopped_out', exit_price=?, pnl_amount=?, pnl_pct=?, closed_at=NOW()
           WHERE id=?''',
        (exit_price, pnl_amount, pnl_pct, trade_id)
    )
    db.commit()
    return True


def cancel_stop_loss_sell(db, trade_id):
    """Keeps holding -- puts a 'stop_loss_pending' trade back to 'open' and
    clears the trigger fields, so reconcile_open_trades will pick it up
    fresh next run (including re-triggering the pending/email flow again
    if it dips to the stop-loss level again -- cancelling once is not a
    permanent dismissal). Returns True if a pending trade was found and
    reverted, False otherwise."""
    trade = db.execute(
        "SELECT id FROM stock_auto_trades WHERE id=? AND status='stop_loss_pending'",
        (trade_id,)
    ).fetchone()
    if not trade:
        return False
    db.execute(
        '''UPDATE stock_auto_trades
           SET status='open', stop_loss_triggered_price=NULL, stop_loss_triggered_at=NULL
           WHERE id=?''',
        (trade_id,)
    )
    db.commit()
    return True


def list_auto_trades(db):
    return db.execute(
        '''SELECT t.id, t.symbol, t.exchange, t.mode, t.status, t.budget_amount, t.quantity,
                  t.buy_price, t.target_sell_price, t.stop_loss_price,
                  t.stop_loss_triggered_price, t.stop_loss_triggered_at,
                  t.exit_price, t.pnl_amount, t.pnl_pct, t.opened_at, t.closed_at, t.watchlist_id
           FROM stock_auto_trades t
           ORDER BY t.opened_at DESC'''
    ).fetchall()
