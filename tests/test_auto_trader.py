from utils.auto_trader import (
    cancel_stop_loss_sell,
    compute_available_funds,
    compute_pnl,
    compute_quantity,
    confirm_stop_loss_sell,
    determine_exit,
    get_auto_trade_settings,
    get_deployed_capital,
    list_auto_trades,
    open_auto_trade_if_enabled,
    reconcile_open_trades,
    reconcile_pending_buys,
    reconcile_pending_sells,
    set_auto_trade_settings,
)


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeKiteOrders:
    """Stands in for the auto_trader-facing surface of KiteClient --
    place_market_order()/get_order_fill() -- with scripted per-call
    responses so a test can force an immediate fill, a pending fill that
    resolves later, or a rejection."""

    def __init__(self, kite_tradingsymbols=None):
        self.kite_tradingsymbols = kite_tradingsymbols or {}  # (symbol, exchange) -> tradingsymbol
        self.orders = {}  # order_id -> {'status', 'average_price', 'filled_quantity'}
        self.placed = []  # [(tradingsymbol, exchange, transaction_type, quantity)]
        self._next_order_id = 1

    def place_market_order(self, tradingsymbol, exchange, transaction_type, quantity):
        order_id = f'ORDER{self._next_order_id}'
        self._next_order_id += 1
        self.placed.append((tradingsymbol, exchange, transaction_type, quantity))
        # Defaults to an immediate COMPLETE fill at a nominal price unless
        # the test pre-registers a different outcome for this order_id via
        # set_next_fill (called right before triggering the action, since
        # order ids are only known once placed -- see the tests below).
        self.orders[order_id] = self._next_fill or {'status': 'COMPLETE', 'average_price': 100.0, 'filled_quantity': quantity}
        self._next_fill = None
        return order_id

    def get_order_fill(self, order_id):
        return self.orders.get(order_id)

    _next_fill = None

    def set_next_fill(self, status, average_price=None, filled_quantity=None):
        self._next_fill = {'status': status, 'average_price': average_price, 'filled_quantity': filled_quantity}

    def update_fill(self, order_id, status, average_price=None, filled_quantity=None):
        self.orders[order_id] = {'status': status, 'average_price': average_price, 'filled_quantity': filled_quantity}


class FakeAutoTraderDB:
    def __init__(self, settings=None, trades=None, daily_data=None, kite_tradingsymbols=None):
        self.settings = settings  # None means "never configured"
        self.trades = trades or []
        self.daily_data = daily_data or {}  # watchlist_id -> latest close
        self.kite_tradingsymbols = kite_tradingsymbols or {}  # (symbol, exchange) -> tradingsymbol
        self._next_id = 1

    def execute(self, sql, params=None):
        params = params or ()
        normalized = ' '.join(sql.split())

        # --- settings ---
        if normalized.startswith('SELECT enabled, mode, budget_per_trade, total_capital FROM stock_auto_trade_settings'):
            return FakeCursor([self.settings] if self.settings else [])

        if normalized.startswith('INSERT INTO stock_auto_trade_settings'):
            enabled, mode, budget, total_capital = params
            self.settings = {'enabled': enabled, 'mode': mode, 'budget_per_trade': budget, 'total_capital': total_capital}
            return FakeCursor([])

        # --- kite instrument map ---
        if normalized.startswith('SELECT kite_tradingsymbol FROM stock_kite_instrument_map'):
            symbol, exchange = params
            ts = self.kite_tradingsymbols.get((symbol, exchange))
            return FakeCursor([{'kite_tradingsymbol': ts}] if ts else [])

        # --- deployed capital ---
        if normalized.startswith('SELECT COALESCE(SUM(budget_amount), 0) AS deployed FROM stock_auto_trades'):
            mode, = params
            deployed = sum(
                t['budget_amount'] for t in self.trades
                if t['mode'] == mode and t['status'] in ('pending_buy', 'open', 'stop_loss_pending', 'pending_sell')
            )
            return FakeCursor([{'deployed': deployed}])

        # --- inserts (dry_run open / live open / live pending_buy) ---
        if normalized.startswith("INSERT INTO stock_auto_trades") and "'dry_run', 'open'" in normalized:
            (suggestion_id, watchlist_id, symbol, exchange, budget_amount, quantity,
             buy_price, target_sell_price, stop_loss_price) = params
            self._insert_trade(suggestion_id, watchlist_id, symbol, exchange, 'dry_run', 'open',
                                budget_amount, quantity, buy_price, target_sell_price, stop_loss_price)
            return FakeCursor([])

        if normalized.startswith("INSERT INTO stock_auto_trades") and "'live', 'open'" in normalized:
            (suggestion_id, watchlist_id, symbol, exchange, budget_amount, quantity,
             buy_price, target_sell_price, stop_loss_price, kite_buy_order_id) = params
            self._insert_trade(suggestion_id, watchlist_id, symbol, exchange, 'live', 'open',
                                budget_amount, quantity, buy_price, target_sell_price, stop_loss_price,
                                kite_buy_order_id=kite_buy_order_id)
            return FakeCursor([])

        if normalized.startswith("INSERT INTO stock_auto_trades") and "'live', 'pending_buy'" in normalized:
            (suggestion_id, watchlist_id, symbol, exchange, budget_amount, quantity,
             buy_price, target_sell_price, stop_loss_price, kite_buy_order_id) = params
            self._insert_trade(suggestion_id, watchlist_id, symbol, exchange, 'live', 'pending_buy',
                                budget_amount, quantity, buy_price, target_sell_price, stop_loss_price,
                                kite_buy_order_id=kite_buy_order_id)
            return FakeCursor([])

        # --- reconcile_open_trades: fetch open trades ---
        if normalized.startswith("SELECT t.id, t.symbol, t.exchange, t.mode, t.watchlist_id, t.buy_price"):
            open_trades = [t for t in self.trades if t['status'] == 'open']
            rows = [{**t, 'latest_price': self.daily_data.get(t['watchlist_id'])} for t in open_trades]
            return FakeCursor(rows)

        if normalized.startswith("UPDATE stock_auto_trades SET status='target_hit', exit_price=?"):
            exit_price, pnl_amount, pnl_pct, trade_id = params
            self._update(trade_id, status='target_hit', exit_price=exit_price, pnl_amount=pnl_amount,
                         pnl_pct=pnl_pct, closed_at='2026-08-18')
            return FakeCursor([])

        if normalized.startswith("UPDATE stock_auto_trades SET status='stop_loss_pending'"):
            triggered_price, trade_id = params
            self._update(trade_id, status='stop_loss_pending', stop_loss_triggered_price=triggered_price,
                         stop_loss_triggered_at='2026-08-18')
            return FakeCursor([])

        # --- confirm/cancel stop loss ---
        if normalized.startswith("SELECT id, mode, symbol, exchange, buy_price, quantity, stop_loss_triggered_price"):
            trade_id, = params
            matches = [t for t in self.trades if t['id'] == trade_id and t['status'] == 'stop_loss_pending']
            return FakeCursor(matches[:1])

        if normalized.startswith("UPDATE stock_auto_trades SET status='stopped_out', exit_price=?"):
            exit_price, pnl_amount, pnl_pct, trade_id = params
            self._update(trade_id, status='stopped_out', exit_price=exit_price, pnl_amount=pnl_amount,
                         pnl_pct=pnl_pct, closed_at='2026-08-19')
            return FakeCursor([])

        if normalized.startswith("SELECT id FROM stock_auto_trades WHERE id=? AND status='stop_loss_pending'"):
            trade_id, = params
            matches = [t for t in self.trades if t['id'] == trade_id and t['status'] == 'stop_loss_pending']
            return FakeCursor(matches[:1])

        if normalized.startswith("UPDATE stock_auto_trades SET status='open', stop_loss_triggered_price=NULL"):
            trade_id, = params
            self._update(trade_id, status='open', stop_loss_triggered_price=None, stop_loss_triggered_at=None)
            return FakeCursor([])

        # --- live sell placement (target-hit or confirmed stop-loss) ---
        if normalized.startswith("UPDATE stock_auto_trades SET status=?, exit_price=?, pnl_amount=?, pnl_pct=?, kite_sell_order_id=?"):
            status, exit_price, pnl_amount, pnl_pct, kite_sell_order_id, trade_id = params
            self._update(trade_id, status=status, exit_price=exit_price, pnl_amount=pnl_amount, pnl_pct=pnl_pct,
                         kite_sell_order_id=kite_sell_order_id, closed_at='2026-08-19')
            return FakeCursor([])

        if normalized.startswith("UPDATE stock_auto_trades SET status='pending_sell', pending_sell_reason=?"):
            reason, kite_sell_order_id, trade_id = params
            self._update(trade_id, status='pending_sell', pending_sell_reason=reason, kite_sell_order_id=kite_sell_order_id)
            return FakeCursor([])

        # --- reconcile_pending_buys ---
        if normalized.startswith("SELECT id, buy_price, kite_buy_order_id FROM stock_auto_trades"):
            matches = [t for t in self.trades if t['mode'] == 'live' and t['status'] == 'pending_buy']
            return FakeCursor(matches)

        if normalized.startswith("UPDATE stock_auto_trades SET status='open', buy_price=?"):
            buy_price, trade_id = params
            self._update(trade_id, status='open', buy_price=buy_price)
            return FakeCursor([])

        if normalized.startswith("UPDATE stock_auto_trades SET status='buy_failed'"):
            trade_id, = params
            self._update(trade_id, status='buy_failed')
            return FakeCursor([])

        # --- reconcile_pending_sells ---
        if normalized.startswith("SELECT id, buy_price, quantity, kite_sell_order_id, pending_sell_reason"):
            matches = [t for t in self.trades if t['mode'] == 'live' and t['status'] == 'pending_sell']
            return FakeCursor(matches)

        if normalized.startswith("UPDATE stock_auto_trades SET status=?, exit_price=?, pnl_amount=?, pnl_pct=?, closed_at=NOW()"):
            status, exit_price, pnl_amount, pnl_pct, trade_id = params
            self._update(trade_id, status=status, exit_price=exit_price, pnl_amount=pnl_amount, pnl_pct=pnl_pct,
                         closed_at='2026-08-19')
            return FakeCursor([])

        # --- list ---
        if normalized.startswith('SELECT t.id, t.symbol, t.exchange, t.mode, t.status'):
            rows = sorted(self.trades, key=lambda t: t['opened_at'], reverse=True)
            return FakeCursor(rows)

        raise AssertionError(f'Unexpected SQL in test: {sql}')

    def _insert_trade(self, suggestion_id, watchlist_id, symbol, exchange, mode, status,
                       budget_amount, quantity, buy_price, target_sell_price, stop_loss_price,
                       kite_buy_order_id=None):
        if any(t['suggestion_id'] == suggestion_id for t in self.trades):
            return  # ON CONFLICT DO NOTHING
        self.trades.append({
            'id': self._next_id, 'suggestion_id': suggestion_id, 'watchlist_id': watchlist_id,
            'symbol': symbol, 'exchange': exchange, 'mode': mode, 'status': status,
            'budget_amount': budget_amount, 'quantity': quantity, 'buy_price': buy_price,
            'target_sell_price': target_sell_price, 'stop_loss_price': stop_loss_price,
            'stop_loss_triggered_price': None, 'stop_loss_triggered_at': None,
            'exit_price': None, 'pnl_amount': None, 'pnl_pct': None,
            'kite_buy_order_id': kite_buy_order_id, 'kite_sell_order_id': None, 'pending_sell_reason': None,
            'opened_at': '2026-08-17', 'closed_at': None,
        })
        self._next_id += 1

    def _update(self, trade_id, **fields):
        for t in self.trades:
            if t['id'] == trade_id:
                t.update(fields)

    def commit(self):
        pass


# --- pure functions ----------------------------------------------------

def test_compute_quantity_floors_to_whole_shares():
    assert compute_quantity(25000, 412) == 60  # 25000/412 = 60.67


def test_compute_quantity_zero_when_budget_too_small():
    assert compute_quantity(100, 412) == 0


def test_compute_quantity_zero_for_missing_or_zero_price():
    assert compute_quantity(25000, None) == 0
    assert compute_quantity(25000, 0) == 0
    assert compute_quantity(0, 412) == 0


def test_compute_pnl_profit_and_loss():
    pnl_amount, pnl_pct = compute_pnl(100, 110, 60)
    assert pnl_amount == 600
    assert pnl_pct == 10.0

    pnl_amount, pnl_pct = compute_pnl(100, 90, 60)
    assert pnl_amount == -600
    assert pnl_pct == -10.0


def test_determine_exit_target_hit():
    assert determine_exit(110, target_sell_price=110, stop_loss_price=95) == 'target_hit'


def test_determine_exit_stopped_out():
    assert determine_exit(94, target_sell_price=110, stop_loss_price=95) == 'stopped_out'


def test_determine_exit_still_open():
    assert determine_exit(102, target_sell_price=110, stop_loss_price=95) is None


def test_determine_exit_none_price_is_open():
    assert determine_exit(None, target_sell_price=110, stop_loss_price=95) is None


def test_determine_exit_target_checked_before_stop():
    assert determine_exit(92, target_sell_price=90, stop_loss_price=95) == 'target_hit'


# --- settings ------------------------------------------------------------

def test_settings_default_when_never_configured():
    db = FakeAutoTraderDB()
    assert get_auto_trade_settings(db) == {
        'enabled': False, 'mode': 'dry_run', 'budget_per_trade': 50000, 'total_capital': 200000,
    }


def test_set_and_get_settings():
    db = FakeAutoTraderDB()
    set_auto_trade_settings(db, enabled=True, budget_per_trade=50000, total_capital=200000, mode='live')
    assert get_auto_trade_settings(db) == {
        'enabled': True, 'mode': 'live', 'budget_per_trade': 50000, 'total_capital': 200000,
    }


def test_set_auto_trade_settings_defaults_to_dry_run_mode():
    db = FakeAutoTraderDB()
    set_auto_trade_settings(db, enabled=True, budget_per_trade=50000, total_capital=200000)
    assert get_auto_trade_settings(db)['mode'] == 'dry_run'


# --- opening a trade (dry_run) ----------------------------------------------

def test_open_auto_trade_does_nothing_when_disabled():
    db = FakeAutoTraderDB(settings={'enabled': False, 'mode': 'dry_run', 'budget_per_trade': 50000, 'total_capital': 200000})
    result = open_auto_trade_if_enabled(db, {
        'suggestion_id': 1, 'watchlist_id': 10, 'symbol': 'GOODCO', 'exchange': 'NSE',
        'buy_price': 412, 'target_sell_price': 450, 'stop_loss_price': 396,
    })
    assert result is None
    assert db.trades == []


def test_open_auto_trade_creates_a_dry_run_position_when_enabled():
    db = FakeAutoTraderDB(settings={'enabled': True, 'mode': 'dry_run', 'budget_per_trade': 50000, 'total_capital': 200000})
    quantity = open_auto_trade_if_enabled(db, {
        'suggestion_id': 1, 'watchlist_id': 10, 'symbol': 'GOODCO', 'exchange': 'NSE',
        'buy_price': 412, 'target_sell_price': 450, 'stop_loss_price': 396,
    })
    assert quantity == 121  # 50000 // 412
    assert len(db.trades) == 1
    assert db.trades[0]['mode'] == 'dry_run'
    assert db.trades[0]['status'] == 'open'


def test_open_auto_trade_is_idempotent_per_suggestion():
    db = FakeAutoTraderDB(settings={'enabled': True, 'mode': 'dry_run', 'budget_per_trade': 50000, 'total_capital': 200000})
    candidate = {
        'suggestion_id': 1, 'watchlist_id': 10, 'symbol': 'GOODCO', 'exchange': 'NSE',
        'buy_price': 412, 'target_sell_price': 450, 'stop_loss_price': 396,
    }
    open_auto_trade_if_enabled(db, candidate)
    open_auto_trade_if_enabled(db, candidate)
    assert len(db.trades) == 1


def test_open_auto_trade_skips_when_quantity_would_be_zero():
    db = FakeAutoTraderDB(settings={'enabled': True, 'mode': 'dry_run', 'budget_per_trade': 50000, 'total_capital': 200000})
    result = open_auto_trade_if_enabled(db, {
        'suggestion_id': 1, 'watchlist_id': 10, 'symbol': 'EXPENSIVECO', 'exchange': 'NSE',
        'buy_price': 9999999, 'target_sell_price': 10500000, 'stop_loss_price': 9500000,
    })
    assert result is None
    assert db.trades == []


# --- available funds gating -------------------------------------------------

def test_compute_available_funds_subtracts_deployed_from_total():
    assert compute_available_funds(total_capital=200000, deployed_capital=150000) == 50000


def test_get_deployed_capital_counts_in_flight_not_closed():
    db = FakeAutoTraderDB(trades=[
        _trade(id=1, mode='dry_run', budget_amount=25000, status='open'),
        _trade(id=2, mode='dry_run', budget_amount=25000, status='stop_loss_pending'),
        _trade(id=3, mode='dry_run', budget_amount=25000, status='pending_sell'),
        _trade(id=4, mode='dry_run', budget_amount=25000, status='target_hit'),
        _trade(id=5, mode='dry_run', budget_amount=25000, status='stopped_out'),
        _trade(id=6, mode='live', budget_amount=99999, status='open'),  # different mode -- not counted
    ])
    assert get_deployed_capital(db, 'dry_run') == 75000  # open + stop_loss_pending + pending_sell


def test_get_deployed_capital_scoped_per_mode():
    db = FakeAutoTraderDB(trades=[
        _trade(id=1, mode='dry_run', budget_amount=50000, status='open'),
        _trade(id=2, mode='live', budget_amount=75000, status='open'),
    ])
    assert get_deployed_capital(db, 'dry_run') == 50000
    assert get_deployed_capital(db, 'live') == 75000


def test_open_auto_trade_stops_once_capital_is_fully_deployed():
    db = FakeAutoTraderDB(
        settings={'enabled': True, 'mode': 'dry_run', 'budget_per_trade': 50000, 'total_capital': 100000},
        trades=[_trade(id=1, suggestion_id=1, mode='dry_run', budget_amount=50000, status='open')],
    )
    result = open_auto_trade_if_enabled(db, {
        'suggestion_id': 2, 'watchlist_id': 11, 'symbol': 'SECOND', 'exchange': 'NSE',
        'buy_price': 100, 'target_sell_price': 110, 'stop_loss_price': 95,
    })
    assert result is not None  # fits exactly (50000 deployed, 50000 room left)

    result = open_auto_trade_if_enabled(db, {
        'suggestion_id': 3, 'watchlist_id': 12, 'symbol': 'THIRD', 'exchange': 'NSE',
        'buy_price': 100, 'target_sell_price': 110, 'stop_loss_price': 95,
    })
    assert result is None  # capital now fully deployed
    assert len(db.trades) == 2


def test_open_auto_trade_resumes_automatically_once_a_position_closes():
    db = FakeAutoTraderDB(
        settings={'enabled': True, 'mode': 'dry_run', 'budget_per_trade': 50000, 'total_capital': 50000},
        trades=[_trade(id=1, suggestion_id=1, mode='dry_run', budget_amount=50000, status='open')],
    )
    blocked = open_auto_trade_if_enabled(db, {
        'suggestion_id': 2, 'watchlist_id': 11, 'symbol': 'SECOND', 'exchange': 'NSE',
        'buy_price': 100, 'target_sell_price': 110, 'stop_loss_price': 95,
    })
    assert blocked is None

    db.trades[0]['status'] = 'target_hit'
    resumed = open_auto_trade_if_enabled(db, {
        'suggestion_id': 2, 'watchlist_id': 11, 'symbol': 'SECOND', 'exchange': 'NSE',
        'buy_price': 100, 'target_sell_price': 110, 'stop_loss_price': 95,
    })
    assert resumed is not None
    assert len(db.trades) == 2


# --- reconciliation (dry_run) -----------------------------------------------

def _trade(**overrides):
    trade = {
        'id': 1, 'suggestion_id': 1, 'watchlist_id': 10, 'symbol': 'GOODCO', 'exchange': 'NSE',
        'mode': 'dry_run', 'status': 'open', 'budget_amount': 50000, 'quantity': 60,
        'buy_price': 412, 'target_sell_price': 450, 'stop_loss_price': 396,
        'stop_loss_triggered_price': None, 'stop_loss_triggered_at': None,
        'exit_price': None, 'pnl_amount': None, 'pnl_pct': None,
        'kite_buy_order_id': None, 'kite_sell_order_id': None, 'pending_sell_reason': None,
        'opened_at': '2026-08-17', 'closed_at': None,
    }
    trade.update(overrides)
    return trade


def test_reconcile_auto_closes_target_hit_dry_run_trade_with_correct_pnl():
    db = FakeAutoTraderDB(trades=[_trade()], daily_data={10: 452})
    summary = reconcile_open_trades(db)
    assert summary['checked'] == 1
    assert summary['target_hit'] == 1
    assert summary['stop_loss_pending'] == []
    assert db.trades[0]['status'] == 'target_hit'
    assert db.trades[0]['exit_price'] == 452
    assert db.trades[0]['pnl_amount'] == round((452 - 412) * 60, 2)


def test_reconcile_does_not_auto_close_on_stop_loss_hit():
    db = FakeAutoTraderDB(trades=[_trade()], daily_data={10: 390})
    summary = reconcile_open_trades(db)
    assert summary['target_hit'] == 0
    assert len(summary['stop_loss_pending']) == 1
    assert db.trades[0]['status'] == 'stop_loss_pending'
    assert db.trades[0]['stop_loss_triggered_price'] == 390
    assert db.trades[0]['exit_price'] is None


def test_reconcile_leaves_still_open_trades_alone():
    db = FakeAutoTraderDB(trades=[_trade()], daily_data={10: 420})
    summary = reconcile_open_trades(db)
    assert summary == {'checked': 1, 'target_hit': 0, 'stop_loss_pending': []}
    assert db.trades[0]['status'] == 'open'


def test_reconcile_skips_a_trade_already_pending_stop_loss_review():
    db = FakeAutoTraderDB(trades=[_trade(status='stop_loss_pending')], daily_data={10: 380})
    summary = reconcile_open_trades(db)
    assert summary == {'checked': 0, 'target_hit': 0, 'stop_loss_pending': []}


def test_reconcile_open_trades_does_not_require_kite_client_when_only_dry_run_trades_exist():
    db = FakeAutoTraderDB(trades=[_trade()], daily_data={10: 452})
    reconcile_open_trades(db, kite_client=None)  # must not raise


def test_reconcile_open_trades_raises_without_kite_client_for_a_live_target_hit():
    db = FakeAutoTraderDB(trades=[_trade(mode='live')], daily_data={10: 452})
    try:
        reconcile_open_trades(db, kite_client=None)
        assert False, 'expected ValueError'
    except ValueError as e:
        assert 'kite_client' in str(e)


# --- manual stop-loss confirm/cancel (dry_run) ------------------------------

def test_confirm_stop_loss_sell_books_the_loss_at_the_triggered_price():
    db = FakeAutoTraderDB(trades=[_trade(
        status='stop_loss_pending', stop_loss_triggered_price=390, stop_loss_triggered_at='2026-08-18'
    )])
    ok = confirm_stop_loss_sell(db, 1)
    assert ok is True
    assert db.trades[0]['status'] == 'stopped_out'
    assert db.trades[0]['exit_price'] == 390
    assert db.trades[0]['pnl_amount'] == round((390 - 412) * 60, 2)


def test_confirm_stop_loss_sell_uses_the_triggered_price_not_a_later_one():
    db = FakeAutoTraderDB(trades=[_trade(
        status='stop_loss_pending', stop_loss_triggered_price=390
    )], daily_data={10: 350})
    confirm_stop_loss_sell(db, 1)
    assert db.trades[0]['exit_price'] == 390


def test_confirm_stop_loss_sell_false_when_not_pending():
    db = FakeAutoTraderDB(trades=[_trade(status='open')])
    assert confirm_stop_loss_sell(db, 1) is False
    assert db.trades[0]['status'] == 'open'


def test_confirm_stop_loss_sell_raises_without_kite_client_for_a_live_trade():
    db = FakeAutoTraderDB(trades=[_trade(mode='live', status='stop_loss_pending', stop_loss_triggered_price=390)])
    try:
        confirm_stop_loss_sell(db, 1, kite_client=None)
        assert False, 'expected ValueError'
    except ValueError as e:
        assert 'kite_client' in str(e)


def test_cancel_stop_loss_sell_reopens_the_trade():
    db = FakeAutoTraderDB(trades=[_trade(
        status='stop_loss_pending', stop_loss_triggered_price=390, stop_loss_triggered_at='2026-08-18'
    )])
    ok = cancel_stop_loss_sell(db, 1)
    assert ok is True
    assert db.trades[0]['status'] == 'open'
    assert db.trades[0]['stop_loss_triggered_price'] is None


def test_cancel_stop_loss_sell_can_retrigger_on_a_later_dip():
    db = FakeAutoTraderDB(trades=[_trade(status='stop_loss_pending', stop_loss_triggered_price=390)])
    cancel_stop_loss_sell(db, 1)

    db.daily_data = {10: 385}
    summary = reconcile_open_trades(db)
    assert len(summary['stop_loss_pending']) == 1
    assert db.trades[0]['status'] == 'stop_loss_pending'


def test_cancel_stop_loss_sell_false_when_not_pending():
    db = FakeAutoTraderDB(trades=[_trade(status='open')])
    assert cancel_stop_loss_sell(db, 1) is False


def test_list_auto_trades_newest_first():
    db = FakeAutoTraderDB(settings={'enabled': True, 'mode': 'dry_run', 'budget_per_trade': 50000, 'total_capital': 200000})
    open_auto_trade_if_enabled(db, {
        'suggestion_id': 1, 'watchlist_id': 10, 'symbol': 'FIRST', 'exchange': 'NSE',
        'buy_price': 100, 'target_sell_price': 110, 'stop_loss_price': 95,
    })
    db.trades[0]['opened_at'] = '2026-08-15'
    open_auto_trade_if_enabled(db, {
        'suggestion_id': 2, 'watchlist_id': 11, 'symbol': 'SECOND', 'exchange': 'NSE',
        'buy_price': 100, 'target_sell_price': 110, 'stop_loss_price': 95,
    })
    db.trades[1]['opened_at'] = '2026-08-17'

    listed = list_auto_trades(db)
    assert [t['symbol'] for t in listed] == ['SECOND', 'FIRST']


# --- live mode: buying ----------------------------------------------------

def test_live_open_requires_kite_client():
    db = FakeAutoTraderDB(settings={'enabled': True, 'mode': 'live', 'budget_per_trade': 50000, 'total_capital': 200000})
    try:
        open_auto_trade_if_enabled(db, {
            'suggestion_id': 1, 'watchlist_id': 10, 'symbol': 'GOODCO', 'exchange': 'NSE',
            'buy_price': 412, 'target_sell_price': 450, 'stop_loss_price': 396,
        }, kite_client=None)
        assert False, 'expected ValueError'
    except ValueError as e:
        assert 'kite_client' in str(e)


def test_live_open_skips_when_symbol_never_matched_to_a_kite_tradingsymbol():
    db = FakeAutoTraderDB(
        settings={'enabled': True, 'mode': 'live', 'budget_per_trade': 50000, 'total_capital': 200000},
        kite_tradingsymbols={},  # GOODCO/NSE never matched
    )
    kite = FakeKiteOrders()
    result = open_auto_trade_if_enabled(db, {
        'suggestion_id': 1, 'watchlist_id': 10, 'symbol': 'GOODCO', 'exchange': 'NSE',
        'buy_price': 412, 'target_sell_price': 450, 'stop_loss_price': 396,
    }, kite_client=kite)
    assert result is None
    assert db.trades == []
    assert kite.placed == []  # never even attempted


def test_live_open_places_a_real_buy_order_using_the_kite_tradingsymbol():
    db = FakeAutoTraderDB(
        settings={'enabled': True, 'mode': 'live', 'budget_per_trade': 50000, 'total_capital': 200000},
        kite_tradingsymbols={('GOODCO', 'NSE'): 'GOODCOMPANY'},
    )
    kite = FakeKiteOrders()
    kite.set_next_fill('COMPLETE', average_price=413.5, filled_quantity=121)

    quantity = open_auto_trade_if_enabled(db, {
        'suggestion_id': 1, 'watchlist_id': 10, 'symbol': 'GOODCO', 'exchange': 'NSE',
        'buy_price': 412, 'target_sell_price': 450, 'stop_loss_price': 396,
    }, kite_client=kite)

    assert quantity == 121
    assert kite.placed == [('GOODCOMPANY', 'NSE', 'BUY', 121)]
    trade = db.trades[0]
    assert trade['mode'] == 'live'
    assert trade['status'] == 'open'
    assert trade['buy_price'] == 413.5  # real fill price, not the theoretical 412
    assert trade['kite_buy_order_id'] is not None


def test_live_open_parks_as_pending_buy_when_not_immediately_filled():
    db = FakeAutoTraderDB(
        settings={'enabled': True, 'mode': 'live', 'budget_per_trade': 50000, 'total_capital': 200000},
        kite_tradingsymbols={('GOODCO', 'NSE'): 'GOODCOMPANY'},
    )
    kite = FakeKiteOrders()
    kite.set_next_fill('OPEN')  # not yet filled

    open_auto_trade_if_enabled(db, {
        'suggestion_id': 1, 'watchlist_id': 10, 'symbol': 'GOODCO', 'exchange': 'NSE',
        'buy_price': 412, 'target_sell_price': 450, 'stop_loss_price': 396,
    }, kite_client=kite)

    trade = db.trades[0]
    assert trade['status'] == 'pending_buy'
    assert trade['buy_price'] == 412  # theoretical price, corrected once confirmed


def test_reconcile_pending_buys_confirms_a_now_filled_order():
    db = FakeAutoTraderDB(trades=[_trade(mode='live', status='pending_buy', kite_buy_order_id='ORDER1')])
    kite = FakeKiteOrders()
    kite.update_fill('ORDER1', 'COMPLETE', average_price=413.5, filled_quantity=60)

    summary = reconcile_pending_buys(db, kite)

    assert summary == {'checked': 1, 'filled': 1, 'failed': 0}
    assert db.trades[0]['status'] == 'open'
    assert db.trades[0]['buy_price'] == 413.5


def test_reconcile_pending_buys_marks_a_rejected_order_as_buy_failed():
    db = FakeAutoTraderDB(trades=[_trade(mode='live', status='pending_buy', kite_buy_order_id='ORDER1')])
    kite = FakeKiteOrders()
    kite.update_fill('ORDER1', 'REJECTED')

    summary = reconcile_pending_buys(db, kite)

    assert summary == {'checked': 1, 'filled': 0, 'failed': 1}
    assert db.trades[0]['status'] == 'buy_failed'


def test_reconcile_pending_buys_leaves_a_still_pending_order_alone():
    db = FakeAutoTraderDB(trades=[_trade(mode='live', status='pending_buy', kite_buy_order_id='ORDER1')])
    kite = FakeKiteOrders()
    kite.update_fill('ORDER1', 'OPEN')

    summary = reconcile_pending_buys(db, kite)

    assert summary == {'checked': 1, 'filled': 0, 'failed': 0}
    assert db.trades[0]['status'] == 'pending_buy'


def test_buy_failed_trade_no_longer_counts_toward_deployed_capital():
    db = FakeAutoTraderDB(trades=[_trade(mode='live', status='buy_failed', budget_amount=50000)])
    assert get_deployed_capital(db, 'live') == 0


# --- live mode: selling ------------------------------------------------------

def test_live_target_hit_places_a_real_sell_order_and_closes_immediately():
    db = FakeAutoTraderDB(
        trades=[_trade(mode='live')], daily_data={10: 452},
        kite_tradingsymbols={('GOODCO', 'NSE'): 'GOODCOMPANY'},
    )
    kite = FakeKiteOrders()
    kite.set_next_fill('COMPLETE', average_price=451.75, filled_quantity=60)

    summary = reconcile_open_trades(db, kite_client=kite)

    assert summary['target_hit'] == 1
    assert kite.placed == [('GOODCOMPANY', 'NSE', 'SELL', 60)]
    trade = db.trades[0]
    assert trade['status'] == 'target_hit'
    assert trade['exit_price'] == 451.75
    assert trade['pnl_amount'] == round((451.75 - 412) * 60, 2)


def test_live_target_hit_parks_as_pending_sell_when_not_immediately_filled():
    db = FakeAutoTraderDB(
        trades=[_trade(mode='live')], daily_data={10: 452},
        kite_tradingsymbols={('GOODCO', 'NSE'): 'GOODCOMPANY'},
    )
    kite = FakeKiteOrders()
    kite.set_next_fill('OPEN')

    reconcile_open_trades(db, kite_client=kite)

    trade = db.trades[0]
    assert trade['status'] == 'pending_sell'
    assert trade['pending_sell_reason'] == 'target_hit'
    assert trade['exit_price'] is None


def test_live_stop_loss_still_goes_to_pending_review_not_auto_sold():
    # Explicit requirement, unchanged for live mode: a stop-loss hit never
    # places an order on its own, even live -- only a human Proceed does.
    db = FakeAutoTraderDB(trades=[_trade(mode='live')], daily_data={10: 390})
    kite = FakeKiteOrders()

    summary = reconcile_open_trades(db, kite_client=kite)

    assert len(summary['stop_loss_pending']) == 1
    assert db.trades[0]['status'] == 'stop_loss_pending'
    assert kite.placed == []  # no order placed just from detecting the trigger


def test_confirm_stop_loss_sell_live_places_a_real_sell_order():
    db = FakeAutoTraderDB(
        trades=[_trade(mode='live', status='stop_loss_pending', stop_loss_triggered_price=390)],
        kite_tradingsymbols={('GOODCO', 'NSE'): 'GOODCOMPANY'},
    )
    kite = FakeKiteOrders()
    kite.set_next_fill('COMPLETE', average_price=389.5, filled_quantity=60)

    ok = confirm_stop_loss_sell(db, 1, kite_client=kite)

    assert ok is True
    assert kite.placed == [('GOODCOMPANY', 'NSE', 'SELL', 60)]
    trade = db.trades[0]
    assert trade['status'] == 'stopped_out'
    assert trade['exit_price'] == 389.5  # the real fill, not the recorded trigger price


def test_reconcile_pending_sells_closes_a_now_filled_target_hit_order():
    db = FakeAutoTraderDB(trades=[_trade(
        mode='live', status='pending_sell', pending_sell_reason='target_hit', kite_sell_order_id='ORDER2',
    )])
    kite = FakeKiteOrders()
    kite.update_fill('ORDER2', 'COMPLETE', average_price=451.0, filled_quantity=60)

    summary = reconcile_pending_sells(db, kite)

    assert summary == {'checked': 1, 'closed': 1}
    assert db.trades[0]['status'] == 'target_hit'
    assert db.trades[0]['exit_price'] == 451.0


def test_reconcile_pending_sells_closes_a_now_filled_stop_loss_order():
    db = FakeAutoTraderDB(trades=[_trade(
        mode='live', status='pending_sell', pending_sell_reason='stopped_out', kite_sell_order_id='ORDER3',
    )])
    kite = FakeKiteOrders()
    kite.update_fill('ORDER3', 'COMPLETE', average_price=388.0, filled_quantity=60)

    summary = reconcile_pending_sells(db, kite)

    assert db.trades[0]['status'] == 'stopped_out'
    assert db.trades[0]['exit_price'] == 388.0


def test_reconcile_pending_sells_leaves_a_still_pending_order_alone():
    db = FakeAutoTraderDB(trades=[_trade(
        mode='live', status='pending_sell', pending_sell_reason='target_hit', kite_sell_order_id='ORDER2',
    )])
    kite = FakeKiteOrders()
    kite.update_fill('ORDER2', 'OPEN')

    summary = reconcile_pending_sells(db, kite)

    assert summary == {'checked': 1, 'closed': 0}
    assert db.trades[0]['status'] == 'pending_sell'
