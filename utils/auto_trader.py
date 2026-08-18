"""Auto-trading for Nari Nakhre Stocks' Pick of the Day -- super_admin only
(see app.py's /stocks/auto-trader routes). Buys a fixed rupee budget of
every new Pick of the Day at its recorded buy_price, in either dry_run
(paper simulation) or live (real Kite orders) mode -- see
utils.auto_trader_settings.mode, changed from /stocks/auto-trader.

The two exits are NOT symmetric, by explicit instruction, in EITHER mode:
  - Reaching target_sell_price auto-sells immediately (see
    reconcile_open_trades) -- a win needs no human judgment call. In live
    mode this places a real SELL market order.
  - Dropping to/through stop_loss_price does NOT auto-sell anything, ever
    -- it moves the trade to 'stop_loss_pending' and the caller (app.py's
    reconcile route) emails STOP_LOSS_ALERT_EMAIL, and a super_admin has
    to explicitly confirm_stop_loss_sell (place the real sell now, in live
    mode) or cancel_stop_loss_sell (keep holding) from the dashboard. This
    was a deliberate, explicit choice even for live trading: no real
    protective stop-loss/GTT order is ever placed on the exchange, so a
    live position IS exposed to further loss between reconciliation runs
    once its stop-loss level is touched and before a human acts on it --
    that risk was accepted knowingly, not an oversight.

Order lifecycle in live mode (see place_market_order/get_order_fill in
utils/kite_client.py): a market order for a liquid NSE/BSE equity during
market hours almost always fills within the same synchronous round trip,
so the common path is place -> immediately check fill -> done. The
'pending_buy'/'pending_sell' statuses below exist only for the rare case
where it hasn't filled by that immediate check (illiquid scrip, circuit
limit, timing at market open/close) -- reconcile_pending_buys/
reconcile_pending_sells pick those back up on the next run rather than
assuming success or leaving them stuck.

Every symbol traded live is resolved through Kite's OWN tradingsymbol
first (utils.kite_instrument_map.get_cached_kite_tradingsymbol) -- never
this app's internal stock_watchlist.symbol directly, which for many BSE
listings is a numeric scrip code Kite's order API doesn't recognize.
Un-mapped symbols are skipped for live trading entirely, not guessed at.

Also see utils/kite_client.py's place_market_order docstring for the
unresolved SEBI/exchange algo-order-tagging question -- no algo_id is
passed anywhere in this module."""
from utils.kite_instrument_map import get_cached_kite_tradingsymbol

MODE_DRY_RUN = 'dry_run'
MODE_LIVE = 'live'
DEFAULT_BUDGET_PER_TRADE = 50000
# Total capital (virtual in dry_run, real risk budget in live) the strategy
# is allowed to have deployed at once (see get_deployed_capital/
# compute_available_funds) -- 4x the default budget per trade, room for 4
# concurrent positions before a new buy has to wait for one to close and
# free its budget back up.
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
            CHECK (status IN ('pending_buy', 'buy_failed', 'open', 'stop_loss_pending',
                               'pending_sell', 'target_hit', 'stopped_out')),
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
        kite_buy_order_id TEXT,
        kite_sell_order_id TEXT,
        pending_sell_reason TEXT CHECK (pending_sell_reason IS NULL OR pending_sell_reason IN ('target_hit', 'stopped_out')),
        opened_at TIMESTAMPTZ DEFAULT NOW(),
        closed_at TIMESTAMPTZ,
        UNIQUE(suggestion_id)
    )''',
    '''CREATE TABLE IF NOT EXISTS stock_auto_trade_settings (
        id INTEGER PRIMARY KEY DEFAULT 1,
        enabled INTEGER NOT NULL DEFAULT 0,
        mode TEXT NOT NULL DEFAULT 'dry_run' CHECK (mode IN ('dry_run', 'live')),
        budget_per_trade NUMERIC(12,2) NOT NULL DEFAULT 50000,
        total_capital NUMERIC(14,2) NOT NULL DEFAULT 200000,
        CONSTRAINT single_row CHECK (id = 1)
    )''',
]

# Additive-only, same convention as every other Stocks table in this
# codebase -- covers the case where these tables were already created by
# an earlier version of the CREATE TABLE statements above.
STOCK_AUTO_TRADE_ALTER_SQL = [
    'ALTER TABLE stock_auto_trade_settings ADD COLUMN IF NOT EXISTS total_capital NUMERIC(14,2) NOT NULL DEFAULT 200000',
    "ALTER TABLE stock_auto_trade_settings ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'dry_run'",
    "ALTER TABLE stock_auto_trade_settings DROP CONSTRAINT IF EXISTS stock_auto_trade_settings_mode_check",
    "ALTER TABLE stock_auto_trade_settings ADD CONSTRAINT stock_auto_trade_settings_mode_check "
    "CHECK (mode IN ('dry_run', 'live'))",
    'ALTER TABLE stock_auto_trades ADD COLUMN IF NOT EXISTS kite_buy_order_id TEXT',
    'ALTER TABLE stock_auto_trades ADD COLUMN IF NOT EXISTS kite_sell_order_id TEXT',
    'ALTER TABLE stock_auto_trades ADD COLUMN IF NOT EXISTS pending_sell_reason TEXT',
    # 'manual_exit' -- a discretionary "Sell now" on an open position (see
    # manual_close_trade), not tied to hitting either the target or the
    # stop-loss. Added to both CHECK constraints below: the trade's own
    # final status, and pending_sell_reason (_place_live_sell's `reason`
    # param becomes one or the other depending on which call site used it).
    "ALTER TABLE stock_auto_trades DROP CONSTRAINT IF EXISTS stock_auto_trades_status_check",
    "ALTER TABLE stock_auto_trades ADD CONSTRAINT stock_auto_trades_status_check "
    "CHECK (status IN ('pending_buy', 'buy_failed', 'open', 'stop_loss_pending', "
    "'pending_sell', 'target_hit', 'stopped_out', 'manual_exit'))",
    "ALTER TABLE stock_auto_trades DROP CONSTRAINT IF EXISTS stock_auto_trades_pending_sell_reason_check",
    "ALTER TABLE stock_auto_trades ADD CONSTRAINT stock_auto_trades_pending_sell_reason_check "
    "CHECK (pending_sell_reason IS NULL OR pending_sell_reason IN ('target_hit', 'stopped_out', 'manual_exit'))",
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
    directly, in both modes. This function only reports which price level
    was reached, not what should happen as a result."""
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
        'SELECT enabled, mode, budget_per_trade, total_capital FROM stock_auto_trade_settings WHERE id=1'
    ).fetchone()
    if not row:
        return {
            'enabled': False, 'mode': MODE_DRY_RUN,
            'budget_per_trade': DEFAULT_BUDGET_PER_TRADE, 'total_capital': DEFAULT_TOTAL_CAPITAL,
        }
    return {
        'enabled': bool(row['enabled']),
        'mode': row['mode'],
        'budget_per_trade': float(row['budget_per_trade']),
        'total_capital': float(row['total_capital']),
    }


def set_auto_trade_settings(db, enabled, budget_per_trade, total_capital, mode=MODE_DRY_RUN):
    db.execute(
        '''INSERT INTO stock_auto_trade_settings (id, enabled, mode, budget_per_trade, total_capital)
           VALUES (1, ?, ?, ?, ?)
           ON CONFLICT (id) DO UPDATE SET
               enabled = EXCLUDED.enabled,
               mode = EXCLUDED.mode,
               budget_per_trade = EXCLUDED.budget_per_trade,
               total_capital = EXCLUDED.total_capital''',
        (1 if enabled else 0, mode, budget_per_trade, total_capital)
    )
    db.commit()


def get_deployed_capital(db, mode):
    """Sum of budget_amount across every trade of this mode currently
    tying up capital -- 'pending_buy'/'open' (bought or buying, not yet
    exited), 'stop_loss_pending' and 'pending_sell' (still holding the
    position either way, exit just not finalized yet). 'buy_failed',
    'target_hit', 'stopped_out' are excluded -- their capital was either
    never actually spent or has been returned to the pool, which is what
    lets compute_available_funds recover on its own once a position
    resolves, with no separate 'paused'/'resumed' flag to manage anywhere.
    Scoped to one mode at a time -- dry_run's virtual capital and live's
    real capital are tracked against the same settings numbers but never
    pooled together, so switching mode doesn't let one bleed into the
    other's budget."""
    row = db.execute(
        "SELECT COALESCE(SUM(budget_amount), 0) AS deployed FROM stock_auto_trades "
        "WHERE mode=? AND status IN ('pending_buy', 'open', 'stop_loss_pending', 'pending_sell')",
        (mode,)
    ).fetchone()
    return float(row['deployed']) if row else 0.0


def compute_available_funds(total_capital, deployed_capital):
    return round(total_capital - deployed_capital, 2)


def _open_trade(db, suggestion, budget_amount, mode, kite_client=None):
    """Shared by open_auto_trade_if_enabled (budget/mode read from the
    global settings row) and open_manual_trade (budget/mode passed in
    explicitly, bypassing the enabled toggle) -- everything from quantity
    sizing through the actual insert/live-order-placement is identical
    between an automatic and a manual buy; only how budget_amount/mode
    were decided differs, which is entirely the caller's job.

    kite_client is required when mode == 'live' (raises ValueError if
    missing -- a live-mode call with no client is a caller bug, not a
    condition to silently skip) and unused/optional otherwise. In live
    mode this places a REAL market BUY order -- see
    utils.kite_client.KiteClient.place_market_order. If Kite fills it
    within the same round trip (the common case for a liquid NSE/BSE
    equity during market hours), the row is inserted already 'open' with
    the real average fill price; otherwise it's inserted 'pending_buy' for
    reconcile_pending_buys to pick up.

    suggestion needs 'suggestion_id' (or 'id'), 'watchlist_id', 'symbol',
    'exchange', 'buy_price', and optionally 'target_sell_price'/
    'stop_loss_price'.

    Idempotent: stock_auto_trades has a UNIQUE constraint on suggestion_id,
    so calling this twice for the same suggestion is a silent no-op the
    second time, same ON CONFLICT DO NOTHING pattern used elsewhere in this
    codebase for exactly this reason. Returns the quantity bought, or None
    if nothing was opened (budget doesn't stretch to even one share, or --
    live only -- the symbol was never matched to a real Kite tradingsymbol,
    never guessed at)."""
    buy_price = suggestion.get('buy_price')
    quantity = compute_quantity(budget_amount, buy_price)
    if quantity <= 0:
        return None

    symbol = suggestion['symbol']
    exchange = suggestion.get('exchange')
    suggestion_id = suggestion.get('suggestion_id', suggestion.get('id'))
    watchlist_id = suggestion['watchlist_id']
    target_sell_price = suggestion.get('target_sell_price')
    stop_loss_price = suggestion.get('stop_loss_price')

    if mode == MODE_DRY_RUN:
        db.execute(
            '''INSERT INTO stock_auto_trades
                   (suggestion_id, watchlist_id, symbol, exchange, mode, status,
                    budget_amount, quantity, buy_price, target_sell_price, stop_loss_price)
               VALUES (?, ?, ?, ?, 'dry_run', 'open', ?, ?, ?, ?, ?)
               ON CONFLICT (suggestion_id) DO NOTHING''',
            (suggestion_id, watchlist_id, symbol, exchange,
             budget_amount, quantity, buy_price, target_sell_price, stop_loss_price)
        )
        db.commit()
        return quantity

    # Live mode from here.
    if kite_client is None:
        raise ValueError('_open_trade: kite_client is required when mode is live.')

    kite_tradingsymbol = get_cached_kite_tradingsymbol(db, symbol, exchange)
    if kite_tradingsymbol is None:
        return None  # never matched to a real Kite symbol -- don't guess, don't trade

    order_id = kite_client.place_market_order(kite_tradingsymbol, exchange, 'BUY', quantity)
    fill = kite_client.get_order_fill(order_id)

    if fill and fill.get('status') == 'COMPLETE':
        actual_buy_price = fill.get('average_price') or buy_price
        db.execute(
            '''INSERT INTO stock_auto_trades
                   (suggestion_id, watchlist_id, symbol, exchange, mode, status,
                    budget_amount, quantity, buy_price, target_sell_price, stop_loss_price, kite_buy_order_id)
               VALUES (?, ?, ?, ?, 'live', 'open', ?, ?, ?, ?, ?, ?)
               ON CONFLICT (suggestion_id) DO NOTHING''',
            (suggestion_id, watchlist_id, symbol, exchange,
             budget_amount, quantity, actual_buy_price, target_sell_price, stop_loss_price, order_id)
        )
    else:
        db.execute(
            '''INSERT INTO stock_auto_trades
                   (suggestion_id, watchlist_id, symbol, exchange, mode, status,
                    budget_amount, quantity, buy_price, target_sell_price, stop_loss_price, kite_buy_order_id)
               VALUES (?, ?, ?, ?, 'live', 'pending_buy', ?, ?, ?, ?, ?, ?)
               ON CONFLICT (suggestion_id) DO NOTHING''',
            (suggestion_id, watchlist_id, symbol, exchange,
             budget_amount, quantity, buy_price, target_sell_price, stop_loss_price, order_id)
        )
    db.commit()
    return quantity


def open_auto_trade_if_enabled(db, created_suggestion, kite_client=None):
    """Called once per newly-created suggestion (see
    generate_daily_suggestions's 'created' list) -- opens a position sized
    to the configured budget, unless:
      - auto-trading is currently disabled, or
      - this suggestion doesn't have enough of a price/target/stop to size
        and track a trade against, or
      - there isn't enough capital left (see get_deployed_capital/
        compute_available_funds) to cover another budget_per_trade-sized
        position right now, or
      - (live mode only) this symbol has never been matched to a Kite
        tradingsymbol (see utils.kite_instrument_map.get_cached_kite_tradingsymbol)
        -- never traded live on a guessed symbol.
    None of these are errors -- buying simply pauses itself whenever any
    of the above blocks it, and resumes on its own the next time this is
    called once an earlier position has closed and freed its budget back
    into the pool (compute_available_funds is recomputed fresh on every
    call, not read from some stored 'paused' flag).

    Delegates the actual sizing/insert/live-order-placement to
    _open_trade -- see its docstring for the kite_client contract and the
    ON CONFLICT (suggestion_id) idempotency guarantee. Returns the
    quantity bought, or None if nothing was opened."""
    settings = get_auto_trade_settings(db)
    if not settings['enabled']:
        return None

    available = compute_available_funds(settings['total_capital'], get_deployed_capital(db, settings['mode']))
    if settings['budget_per_trade'] > available:
        return None

    return _open_trade(db, created_suggestion, settings['budget_per_trade'], settings['mode'], kite_client=kite_client)


def open_manual_trade(db, suggestion, budget_amount, mode, kite_client=None):
    """Discretionary buy triggered by a super_admin clicking "Buy" on a
    specific recommendation (see the /stocks/suggestions/<id>/buy route),
    as opposed to open_auto_trade_if_enabled's automatic one-per-suggestion
    behaviour. Deliberately NOT gated by settings['enabled'] -- a manual
    click is its own trigger regardless of whether auto-trading is on.

    budget_amount and mode are explicit (not read from the global auto-
    trade settings) so a specific trade can use a custom amount, though
    callers typically default budget_amount to
    get_auto_trade_settings(db)['budget_per_trade'] and mode to the
    current global mode. Still enforces the same capital-limit guard as
    the automatic path, against the global total_capital setting.

    Returns the quantity bought, or None if nothing was opened (budget
    doesn't stretch to a share, insufficient capital left, a trade already
    exists for this suggestion, or -- live only -- no Kite tradingsymbol
    match)."""
    settings = get_auto_trade_settings(db)
    available = compute_available_funds(settings['total_capital'], get_deployed_capital(db, mode))
    if budget_amount > available:
        return None

    return _open_trade(db, suggestion, budget_amount, mode, kite_client=kite_client)


def manual_close_trade(db, trade_id, kite_client=None):
    """Discretionary "Sell now" on an open position, triggered by a
    super_admin -- unlike reconcile_open_trades, this doesn't check
    whether the target or stop-loss was actually reached; exiting early
    (or late) for any reason is the whole point. Only acts on a trade
    whose status is 'open' (returns False for anything else -- already
    closed, or awaiting a stop-loss decision via
    confirm_stop_loss_sell/cancel_stop_loss_sell instead).

    dry_run closes immediately at the latest synced daily close (the same
    price source reconcile_open_trades itself reads -- this app doesn't
    poll intraday quotes). live places a REAL sell order right now via the
    shared _place_live_sell helper (kite_client required, raises
    ValueError if missing), with the same immediate-fill-or-pending_sell
    handling as every other live sell path in this module. Final status is
    'manual_exit' either way. Returns True if a trade was found and acted
    on, False otherwise."""
    trade = db.execute(
        '''SELECT t.id, t.mode, t.symbol, t.exchange, t.buy_price, t.quantity, t.watchlist_id,
                  d.close AS latest_price
           FROM stock_auto_trades t
           LEFT JOIN stock_daily_data d ON d.watchlist_id = t.watchlist_id
               AND d.trade_date = (SELECT MAX(d2.trade_date) FROM stock_daily_data d2 WHERE d2.watchlist_id = t.watchlist_id)
           WHERE t.id=? AND t.status='open' ''',
        (trade_id,)
    ).fetchone()
    if not trade:
        return False

    if trade['mode'] == MODE_LIVE:
        if kite_client is None:
            raise ValueError('manual_close_trade: kite_client is required for a live trade.')
        _place_live_sell(db, kite_client, trade, 'manual_exit')
        return True

    exit_price = trade['latest_price'] if trade['latest_price'] is not None else trade['buy_price']
    pnl_amount, pnl_pct = compute_pnl(trade['buy_price'], exit_price, trade['quantity'])
    db.execute(
        '''UPDATE stock_auto_trades
           SET status='manual_exit', exit_price=?, pnl_amount=?, pnl_pct=?, closed_at=NOW()
           WHERE id=?''',
        (exit_price, pnl_amount, pnl_pct, trade_id)
    )
    db.commit()
    return True


def reconcile_pending_buys(db, kite_client):
    """Follow-up for any live buy order that hadn't filled by the time
    open_auto_trade_if_enabled checked it -- see that function's
    docstring. Moves each 'pending_buy' row to 'open' (with the real
    average fill price) once Kite confirms COMPLETE, or 'buy_failed' if
    Kite reports it REJECTED/CANCELLED (its budget then falls out of
    get_deployed_capital automatically, freeing it back to the pool -- no
    separate cleanup needed). Still-pending orders are left alone for the
    next run. Returns {'checked', 'filled', 'failed'}."""
    pending = db.execute(
        "SELECT id, buy_price, kite_buy_order_id FROM stock_auto_trades "
        "WHERE mode='live' AND status='pending_buy'"
    ).fetchall()

    filled = 0
    failed = 0
    for trade in pending:
        fill = kite_client.get_order_fill(trade['kite_buy_order_id'])
        if not fill:
            continue
        if fill.get('status') == 'COMPLETE':
            db.execute(
                "UPDATE stock_auto_trades SET status='open', buy_price=? WHERE id=?",
                (fill.get('average_price') or trade['buy_price'], trade['id'])
            )
            db.commit()
            filled += 1
        elif fill.get('status') in ('REJECTED', 'CANCELLED'):
            db.execute("UPDATE stock_auto_trades SET status='buy_failed' WHERE id=?", (trade['id'],))
            db.commit()
            failed += 1
        # else still genuinely pending on the exchange -- leave as-is.

    return {'checked': len(pending), 'filled': filled, 'failed': failed}


def _place_live_sell(db, kite_client, trade, reason):
    """Shared by reconcile_open_trades' target-hit path and
    confirm_stop_loss_sell's live path -- places a real SELL market order
    and either closes the trade immediately (fill confirmed within the
    same round trip, the common case) or parks it as 'pending_sell' with
    pending_sell_reason recorded, for reconcile_pending_sells to finish
    once Kite confirms. reason is 'target_hit' or 'stopped_out' -- which
    outcome this sell represents once/if it completes."""
    kite_tradingsymbol = get_cached_kite_tradingsymbol(db, trade['symbol'], trade['exchange'])
    if kite_tradingsymbol is None:
        # Can't sell what was never resolvable to a real Kite symbol in
        # the first place -- this should be unreachable (the buy itself
        # would have been skipped), but never silently guess a symbol for
        # a sell order either.
        return False

    order_id = kite_client.place_market_order(kite_tradingsymbol, trade['exchange'], 'SELL', trade['quantity'])
    fill = kite_client.get_order_fill(order_id)

    if fill and fill.get('status') == 'COMPLETE':
        exit_price = fill.get('average_price') or trade['buy_price']
        pnl_amount, pnl_pct = compute_pnl(trade['buy_price'], exit_price, trade['quantity'])
        db.execute(
            '''UPDATE stock_auto_trades
               SET status=?, exit_price=?, pnl_amount=?, pnl_pct=?, kite_sell_order_id=?, closed_at=NOW()
               WHERE id=?''',
            (reason, exit_price, pnl_amount, pnl_pct, order_id, trade['id'])
        )
    else:
        db.execute(
            '''UPDATE stock_auto_trades
               SET status='pending_sell', pending_sell_reason=?, kite_sell_order_id=?
               WHERE id=?''',
            (reason, order_id, trade['id'])
        )
    db.commit()
    return True


def reconcile_pending_sells(db, kite_client):
    """Follow-up for any live sell order (target-hit or a confirmed
    stop-loss) that hadn't filled by the time it was placed -- mirrors
    reconcile_pending_buys. Closes each 'pending_sell' row with its
    recorded pending_sell_reason ('target_hit' or 'stopped_out') once
    Kite confirms COMPLETE. A REJECTED/CANCELLED sell is left as
    'pending_sell' rather than silently reverted to 'open' -- a failed
    exchange sell needs a human look, not an automatic retry loop.
    Returns {'checked', 'closed'}."""
    pending = db.execute(
        "SELECT id, buy_price, quantity, kite_sell_order_id, pending_sell_reason FROM stock_auto_trades "
        "WHERE mode='live' AND status='pending_sell'"
    ).fetchall()

    closed = 0
    for trade in pending:
        fill = kite_client.get_order_fill(trade['kite_sell_order_id'])
        if not fill or fill.get('status') != 'COMPLETE':
            continue
        exit_price = fill.get('average_price') or trade['buy_price']
        pnl_amount, pnl_pct = compute_pnl(trade['buy_price'], exit_price, trade['quantity'])
        db.execute(
            '''UPDATE stock_auto_trades
               SET status=?, exit_price=?, pnl_amount=?, pnl_pct=?, closed_at=NOW()
               WHERE id=?''',
            (trade['pending_sell_reason'], exit_price, pnl_amount, pnl_pct, trade['id'])
        )
        db.commit()
        closed += 1

    return {'checked': len(pending), 'closed': closed}


def reconcile_open_trades(db, kite_client=None):
    """Checks every 'open' trade (both modes) against its watchlist
    company's latest synced close (see determine_exit):
      - target_hit -> dry_run closes immediately with the synced close as
        the exit price, same as before. live places a real SELL order
        (see _place_live_sell) -- kite_client is required when any 'open'
        live trades exist (raises ValueError if missing).
      - stopped_out -> in EITHER mode, does NOT sell -- moves to
        'stop_loss_pending' with the triggering price/time recorded, and
        is excluded from further checks here (the WHERE clause only ever
        looks at 'open' rows) until a super_admin calls
        confirm_stop_loss_sell or cancel_stop_loss_sell. Cancelling puts
        it back to 'open', so a later dip can trigger this again -- it
        isn't a one-shot dismissal.

    Meant to run daily, after that day's price sync, alongside the rest of
    the Stocks cron pipeline -- note this means target/stop levels are
    checked once a day against the daily close, not continuously; a live
    target-hit sell can execute at a materially different intraday price
    than whatever moment actually crossed the target during the day.

    Returns {'checked', 'target_hit': [trade dicts], 'stop_loss_pending':
    [trade dicts]} -- the caller (app.py's reconcile route) is responsible
    for emailing about each entry in both lists, since this module doesn't
    send email itself (same DB-orchestration-only split as
    generate_daily_suggestions, whose caller sends the actual email too).
    A live target-hit sell that doesn't fill within the same round trip is
    parked as 'pending_sell' same as ever (see reconcile_pending_sells) and
    is NOT included in 'target_hit' here -- its exit_price/pnl aren't known
    yet, so there's nothing to report until it actually closes."""
    open_trades = db.execute(
        '''SELECT t.id, t.symbol, t.exchange, t.mode, t.watchlist_id, t.buy_price, t.target_sell_price,
                  t.stop_loss_price, t.quantity, d.close AS latest_price
           FROM stock_auto_trades t
           LEFT JOIN stock_daily_data d ON d.watchlist_id = t.watchlist_id
               AND d.trade_date = (SELECT MAX(d2.trade_date) FROM stock_daily_data d2 WHERE d2.watchlist_id = t.watchlist_id)
           WHERE t.status = 'open' '''
    ).fetchall()

    closed_targets = []
    pending_stop_losses = []
    for trade in open_trades:
        outcome = determine_exit(trade.get('latest_price'), trade.get('target_sell_price'), trade.get('stop_loss_price'))
        if outcome is None:
            continue
        if outcome == 'target_hit':
            if trade['mode'] == MODE_LIVE:
                if kite_client is None:
                    raise ValueError('reconcile_open_trades: kite_client is required to sell an open live trade.')
                _place_live_sell(db, kite_client, trade, 'target_hit')
                updated = db.execute(
                    "SELECT status, exit_price, pnl_amount, pnl_pct FROM stock_auto_trades WHERE id=?",
                    (trade['id'],)
                ).fetchone()
                if updated and updated['status'] == 'target_hit':
                    closed_targets.append({**trade, **updated})
                # else parked as 'pending_sell' -- reconcile_pending_sells will finish it later.
            else:
                exit_price = trade['latest_price']
                pnl_amount, pnl_pct = compute_pnl(trade['buy_price'], exit_price, trade['quantity'])
                db.execute(
                    '''UPDATE stock_auto_trades
                       SET status='target_hit', exit_price=?, pnl_amount=?, pnl_pct=?, closed_at=NOW()
                       WHERE id=?''',
                    (exit_price, pnl_amount, pnl_pct, trade['id'])
                )
                db.commit()
                closed_targets.append({**trade, 'exit_price': exit_price, 'pnl_amount': pnl_amount, 'pnl_pct': pnl_pct})
        else:  # 'stopped_out' -- needs manual confirmation in every mode, never auto-sold
            db.execute(
                '''UPDATE stock_auto_trades
                   SET status='stop_loss_pending', stop_loss_triggered_price=?, stop_loss_triggered_at=NOW()
                   WHERE id=?''',
                (trade['latest_price'], trade['id'])
            )
            db.commit()
            pending_stop_losses.append({**trade, 'stop_loss_triggered_price': trade['latest_price']})

    return {'checked': len(open_trades), 'target_hit': closed_targets, 'stop_loss_pending': pending_stop_losses}


def confirm_stop_loss_sell(db, trade_id, kite_client=None):
    """Books the loss. dry_run closes at the price that originally
    triggered it (not a freshly re-fetched price, so the outcome matches
    exactly what was flagged for review, regardless of how long the
    decision took). live places a REAL sell order right now instead --
    kite_client is required (raises ValueError if missing) -- since this
    IS the human-authorized moment to act, unlike the automatic reconcile
    loop; if Kite doesn't fill it within the same round trip, the trade is
    parked as 'pending_sell' for reconcile_pending_sells to finish.
    Returns True if a pending trade was found and acted on, False
    otherwise (already resolved, or never existed)."""
    trade = db.execute(
        'SELECT id, mode, symbol, exchange, buy_price, quantity, stop_loss_triggered_price FROM stock_auto_trades '
        "WHERE id=? AND status='stop_loss_pending'",
        (trade_id,)
    ).fetchone()
    if not trade:
        return False

    if trade['mode'] == MODE_LIVE:
        if kite_client is None:
            raise ValueError('confirm_stop_loss_sell: kite_client is required for a live trade.')
        _place_live_sell(db, kite_client, trade, 'stopped_out')
        return True

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
    permanent dismissal). Same in both modes -- no order was ever placed
    to cancel, this only ever touched our own bookkeeping. Returns True if
    a pending trade was found and reverted, False otherwise."""
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
                  t.exit_price, t.pnl_amount, t.pnl_pct, t.opened_at, t.closed_at, t.watchlist_id,
                  t.kite_buy_order_id, t.kite_sell_order_id
           FROM stock_auto_trades t
           ORDER BY t.opened_at DESC'''
    ).fetchall()
