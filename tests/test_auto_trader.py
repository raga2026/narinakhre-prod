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
    set_auto_trade_settings,
)


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeAutoTraderDB:
    def __init__(self, settings=None, trades=None, daily_data=None):
        self.settings = settings  # None means "never configured"
        self.trades = trades or []
        self.daily_data = daily_data or {}  # watchlist_id -> latest close
        self._next_id = 1

    def execute(self, sql, params=None):
        params = params or ()
        normalized = ' '.join(sql.split())

        if normalized.startswith('SELECT enabled, budget_per_trade, total_capital FROM stock_auto_trade_settings'):
            return FakeCursor([self.settings] if self.settings else [])

        if normalized.startswith('INSERT INTO stock_auto_trade_settings'):
            enabled, budget, total_capital = params
            self.settings = {'enabled': enabled, 'budget_per_trade': budget, 'total_capital': total_capital}
            return FakeCursor([])

        if normalized.startswith('SELECT COALESCE(SUM(budget_amount), 0) AS deployed FROM stock_auto_trades'):
            deployed = sum(t['budget_amount'] for t in self.trades if t['status'] in ('open', 'stop_loss_pending'))
            return FakeCursor([{'deployed': deployed}])

        if normalized.startswith('INSERT INTO stock_auto_trades'):
            (suggestion_id, watchlist_id, symbol, exchange, budget_amount, quantity,
             buy_price, target_sell_price, stop_loss_price) = params
            if any(t['suggestion_id'] == suggestion_id for t in self.trades):
                return FakeCursor([])  # ON CONFLICT DO NOTHING
            self.trades.append({
                'id': self._next_id, 'suggestion_id': suggestion_id, 'watchlist_id': watchlist_id,
                'symbol': symbol, 'exchange': exchange, 'mode': 'dry_run', 'status': 'open',
                'budget_amount': budget_amount, 'quantity': quantity, 'buy_price': buy_price,
                'target_sell_price': target_sell_price, 'stop_loss_price': stop_loss_price,
                'stop_loss_triggered_price': None, 'stop_loss_triggered_at': None,
                'exit_price': None, 'pnl_amount': None, 'pnl_pct': None,
                'opened_at': '2026-08-17', 'closed_at': None,
            })
            self._next_id += 1
            return FakeCursor([])

        if normalized.startswith("SELECT t.id, t.symbol, t.exchange, t.watchlist_id, t.buy_price, t.target_sell_price"):
            open_trades = [t for t in self.trades if t['status'] == 'open' and t['mode'] == 'dry_run']
            rows = [{**t, 'latest_price': self.daily_data.get(t['watchlist_id'])} for t in open_trades]
            return FakeCursor(rows)

        if normalized.startswith("UPDATE stock_auto_trades SET status='target_hit'"):
            exit_price, pnl_amount, pnl_pct, trade_id = params
            for t in self.trades:
                if t['id'] == trade_id:
                    t['status'] = 'target_hit'
                    t['exit_price'] = exit_price
                    t['pnl_amount'] = pnl_amount
                    t['pnl_pct'] = pnl_pct
                    t['closed_at'] = '2026-08-18'
            return FakeCursor([])

        if normalized.startswith("UPDATE stock_auto_trades SET status='stop_loss_pending'"):
            triggered_price, trade_id = params
            for t in self.trades:
                if t['id'] == trade_id:
                    t['status'] = 'stop_loss_pending'
                    t['stop_loss_triggered_price'] = triggered_price
                    t['stop_loss_triggered_at'] = '2026-08-18'
            return FakeCursor([])

        if normalized.startswith("SELECT id, buy_price, quantity, stop_loss_triggered_price FROM stock_auto_trades WHERE id=? AND status='stop_loss_pending'"):
            trade_id, = params
            matches = [t for t in self.trades if t['id'] == trade_id and t['status'] == 'stop_loss_pending']
            return FakeCursor(matches[:1])

        if normalized.startswith("UPDATE stock_auto_trades SET status='stopped_out'"):
            exit_price, pnl_amount, pnl_pct, trade_id = params
            for t in self.trades:
                if t['id'] == trade_id:
                    t['status'] = 'stopped_out'
                    t['exit_price'] = exit_price
                    t['pnl_amount'] = pnl_amount
                    t['pnl_pct'] = pnl_pct
                    t['closed_at'] = '2026-08-19'
            return FakeCursor([])

        if normalized.startswith("SELECT id FROM stock_auto_trades WHERE id=? AND status='stop_loss_pending'"):
            trade_id, = params
            matches = [t for t in self.trades if t['id'] == trade_id and t['status'] == 'stop_loss_pending']
            return FakeCursor(matches[:1])

        if normalized.startswith("UPDATE stock_auto_trades SET status='open', stop_loss_triggered_price=NULL"):
            trade_id, = params
            for t in self.trades:
                if t['id'] == trade_id:
                    t['status'] = 'open'
                    t['stop_loss_triggered_price'] = None
                    t['stop_loss_triggered_at'] = None
            return FakeCursor([])

        if normalized.startswith('SELECT t.id, t.symbol, t.exchange, t.mode, t.status'):
            rows = sorted(self.trades, key=lambda t: t['opened_at'], reverse=True)
            return FakeCursor(rows)

        raise AssertionError(f'Unexpected SQL in test: {sql}')

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
    assert get_auto_trade_settings(db) == {'enabled': False, 'budget_per_trade': 25000, 'total_capital': 200000}


def test_set_and_get_settings():
    db = FakeAutoTraderDB()
    set_auto_trade_settings(db, enabled=True, budget_per_trade=25000, total_capital=200000)
    assert get_auto_trade_settings(db) == {'enabled': True, 'budget_per_trade': 25000, 'total_capital': 200000}


# --- opening a trade -------------------------------------------------------

def test_open_auto_trade_does_nothing_when_disabled():
    db = FakeAutoTraderDB(settings={'enabled': False, 'budget_per_trade': 25000, 'total_capital': 200000})
    result = open_auto_trade_if_enabled(db, {
        'suggestion_id': 1, 'watchlist_id': 10, 'symbol': 'GOODCO', 'exchange': 'NSE',
        'buy_price': 412, 'target_sell_price': 450, 'stop_loss_price': 396,
    })
    assert result is None
    assert db.trades == []


def test_open_auto_trade_creates_a_dry_run_position_when_enabled():
    db = FakeAutoTraderDB(settings={'enabled': True, 'budget_per_trade': 25000, 'total_capital': 200000})
    quantity = open_auto_trade_if_enabled(db, {
        'suggestion_id': 1, 'watchlist_id': 10, 'symbol': 'GOODCO', 'exchange': 'NSE',
        'buy_price': 412, 'target_sell_price': 450, 'stop_loss_price': 396,
    })
    assert quantity == 60
    assert len(db.trades) == 1
    assert db.trades[0]['mode'] == 'dry_run'
    assert db.trades[0]['status'] == 'open'


def test_open_auto_trade_is_idempotent_per_suggestion():
    db = FakeAutoTraderDB(settings={'enabled': True, 'budget_per_trade': 25000, 'total_capital': 200000})
    candidate = {
        'suggestion_id': 1, 'watchlist_id': 10, 'symbol': 'GOODCO', 'exchange': 'NSE',
        'buy_price': 412, 'target_sell_price': 450, 'stop_loss_price': 396,
    }
    open_auto_trade_if_enabled(db, candidate)
    open_auto_trade_if_enabled(db, candidate)
    assert len(db.trades) == 1


def test_open_auto_trade_skips_when_quantity_would_be_zero():
    db = FakeAutoTraderDB(settings={'enabled': True, 'budget_per_trade': 25000, 'total_capital': 200000})
    result = open_auto_trade_if_enabled(db, {
        'suggestion_id': 1, 'watchlist_id': 10, 'symbol': 'EXPENSIVECO', 'exchange': 'NSE',
        'buy_price': 999999, 'target_sell_price': 1050000, 'stop_loss_price': 950000,
    })
    assert result is None
    assert db.trades == []


# --- available funds gating -------------------------------------------------

def test_compute_available_funds_subtracts_deployed_from_total():
    assert compute_available_funds(total_capital=200000, deployed_capital=150000) == 50000


def test_get_deployed_capital_counts_open_and_pending_not_closed():
    db = FakeAutoTraderDB(trades=[
        _open_trade(id=1, budget_amount=25000, status='open'),
        _open_trade(id=2, budget_amount=25000, status='stop_loss_pending'),
        _open_trade(id=3, budget_amount=25000, status='target_hit'),
        _open_trade(id=4, budget_amount=25000, status='stopped_out'),
    ])
    assert get_deployed_capital(db) == 50000  # only the open + pending ones


def test_open_auto_trade_stops_once_capital_is_fully_deployed():
    # Exactly one budget's worth of room left -- one more buy should still
    # fit, but a second should not.
    db = FakeAutoTraderDB(
        settings={'enabled': True, 'budget_per_trade': 25000, 'total_capital': 50000},
        trades=[_open_trade(id=1, suggestion_id=1, budget_amount=25000, status='open')],
    )
    result = open_auto_trade_if_enabled(db, {
        'suggestion_id': 2, 'watchlist_id': 11, 'symbol': 'SECOND', 'exchange': 'NSE',
        'buy_price': 100, 'target_sell_price': 110, 'stop_loss_price': 95,
    })
    assert result is not None  # fits exactly (25000 deployed, 25000 room left)

    result = open_auto_trade_if_enabled(db, {
        'suggestion_id': 3, 'watchlist_id': 12, 'symbol': 'THIRD', 'exchange': 'NSE',
        'buy_price': 100, 'target_sell_price': 110, 'stop_loss_price': 95,
    })
    assert result is None  # capital now fully deployed, nothing left for a third
    assert len(db.trades) == 2


def test_open_auto_trade_resumes_automatically_once_a_position_closes():
    # Fully deployed -- a new pick is skipped...
    db = FakeAutoTraderDB(
        settings={'enabled': True, 'budget_per_trade': 25000, 'total_capital': 25000},
        trades=[_open_trade(id=1, suggestion_id=1, budget_amount=25000, status='open')],
    )
    blocked = open_auto_trade_if_enabled(db, {
        'suggestion_id': 2, 'watchlist_id': 11, 'symbol': 'SECOND', 'exchange': 'NSE',
        'buy_price': 100, 'target_sell_price': 110, 'stop_loss_price': 95,
    })
    assert blocked is None

    # ...but once the existing position closes (freeing its budget back to
    # the pool), the very next call succeeds -- no separate "resume" step.
    db.trades[0]['status'] = 'target_hit'
    resumed = open_auto_trade_if_enabled(db, {
        'suggestion_id': 2, 'watchlist_id': 11, 'symbol': 'SECOND', 'exchange': 'NSE',
        'buy_price': 100, 'target_sell_price': 110, 'stop_loss_price': 95,
    })
    assert resumed is not None
    assert len(db.trades) == 2


# --- reconciliation --------------------------------------------------------

def _open_trade(**overrides):
    trade = {
        'id': 1, 'suggestion_id': 1, 'watchlist_id': 10, 'symbol': 'GOODCO', 'exchange': 'NSE',
        'mode': 'dry_run', 'status': 'open', 'budget_amount': 25000, 'quantity': 60,
        'buy_price': 412, 'target_sell_price': 450, 'stop_loss_price': 396,
        'stop_loss_triggered_price': None, 'stop_loss_triggered_at': None,
        'exit_price': None, 'pnl_amount': None, 'pnl_pct': None, 'opened_at': '2026-08-17', 'closed_at': None,
    }
    trade.update(overrides)
    return trade


def test_reconcile_auto_closes_target_hit_trade_with_correct_pnl():
    db = FakeAutoTraderDB(trades=[_open_trade()], daily_data={10: 452})
    summary = reconcile_open_trades(db)
    assert summary['checked'] == 1
    assert summary['target_hit'] == 1
    assert summary['stop_loss_pending'] == []
    assert db.trades[0]['status'] == 'target_hit'
    assert db.trades[0]['exit_price'] == 452
    assert db.trades[0]['pnl_amount'] == round((452 - 412) * 60, 2)


def test_reconcile_does_not_auto_close_on_stop_loss_hit():
    # Explicit requirement: hitting stop-loss must NOT close the trade on
    # its own -- it needs a human confirm/cancel decision.
    db = FakeAutoTraderDB(trades=[_open_trade()], daily_data={10: 390})
    summary = reconcile_open_trades(db)
    assert summary['checked'] == 1
    assert summary['target_hit'] == 0
    assert len(summary['stop_loss_pending']) == 1
    assert summary['stop_loss_pending'][0]['id'] == 1

    assert db.trades[0]['status'] == 'stop_loss_pending'
    assert db.trades[0]['stop_loss_triggered_price'] == 390
    assert db.trades[0]['exit_price'] is None  # not closed, not booked yet
    assert db.trades[0]['pnl_amount'] is None


def test_reconcile_leaves_still_open_trades_alone():
    db = FakeAutoTraderDB(trades=[_open_trade()], daily_data={10: 420})
    summary = reconcile_open_trades(db)
    assert summary == {'checked': 1, 'target_hit': 0, 'stop_loss_pending': []}
    assert db.trades[0]['status'] == 'open'


def test_reconcile_skips_a_trade_already_pending_stop_loss_review():
    # Once pending, it's no longer selected by the 'open' WHERE clause --
    # this is what stops the alert email from being re-sent every day it
    # sits unresolved.
    db = FakeAutoTraderDB(trades=[_open_trade(status='stop_loss_pending')], daily_data={10: 380})
    summary = reconcile_open_trades(db)
    assert summary == {'checked': 0, 'target_hit': 0, 'stop_loss_pending': []}


# --- manual stop-loss confirm/cancel ---------------------------------------

def test_confirm_stop_loss_sell_books_the_loss_at_the_triggered_price():
    db = FakeAutoTraderDB(trades=[_open_trade(
        status='stop_loss_pending', stop_loss_triggered_price=390, stop_loss_triggered_at='2026-08-18'
    )])
    ok = confirm_stop_loss_sell(db, 1)
    assert ok is True
    assert db.trades[0]['status'] == 'stopped_out'
    assert db.trades[0]['exit_price'] == 390
    assert db.trades[0]['pnl_amount'] == round((390 - 412) * 60, 2)


def test_confirm_stop_loss_sell_uses_the_triggered_price_not_a_later_one():
    # Even if the market has moved further since the trigger, the recorded
    # trade must close at the price that actually flagged it -- not
    # whatever the price happens to be when the decision is finally made.
    db = FakeAutoTraderDB(trades=[_open_trade(
        status='stop_loss_pending', stop_loss_triggered_price=390
    )], daily_data={10: 350})  # price has since fallen further
    confirm_stop_loss_sell(db, 1)
    assert db.trades[0]['exit_price'] == 390


def test_confirm_stop_loss_sell_false_when_not_pending():
    db = FakeAutoTraderDB(trades=[_open_trade(status='open')])
    assert confirm_stop_loss_sell(db, 1) is False
    assert db.trades[0]['status'] == 'open'


def test_cancel_stop_loss_sell_reopens_the_trade():
    db = FakeAutoTraderDB(trades=[_open_trade(
        status='stop_loss_pending', stop_loss_triggered_price=390, stop_loss_triggered_at='2026-08-18'
    )])
    ok = cancel_stop_loss_sell(db, 1)
    assert ok is True
    assert db.trades[0]['status'] == 'open'
    assert db.trades[0]['stop_loss_triggered_price'] is None
    assert db.trades[0]['stop_loss_triggered_at'] is None


def test_cancel_stop_loss_sell_can_retrigger_on_a_later_dip():
    db = FakeAutoTraderDB(trades=[_open_trade(
        status='stop_loss_pending', stop_loss_triggered_price=390
    )])
    cancel_stop_loss_sell(db, 1)

    db.daily_data = {10: 385}
    summary = reconcile_open_trades(db)
    assert len(summary['stop_loss_pending']) == 1
    assert db.trades[0]['status'] == 'stop_loss_pending'


def test_cancel_stop_loss_sell_false_when_not_pending():
    db = FakeAutoTraderDB(trades=[_open_trade(status='open')])
    assert cancel_stop_loss_sell(db, 1) is False


def test_list_auto_trades_newest_first():
    db = FakeAutoTraderDB(settings={'enabled': True, 'budget_per_trade': 25000, 'total_capital': 200000})
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
