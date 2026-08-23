from unittest.mock import patch

from stoqbell.utils.admin_alerts import (
    find_and_notify_intraday_target_hits,
    record_and_send_highly_recommended_alerts,
)

# Same fixture shape as test_suggestion_engine.py's _golden_scoring_candidate
# -- duplicated here rather than imported, matching this codebase's existing
# convention of not doing test-module-to-test-module imports for one small
# fixture (see that file's own comment on _golden_scoring_candidate).
GOOD_INDICATORS = {'cross_status': 'golden_cross', 'volume_trend': 'confirming', 'rsi_14': 52.5}


def _golden_candidate(watchlist_id, symbol, **overrides):
    row = {
        'watchlist_id': watchlist_id, 'symbol': symbol, 'exchange': 'NSE', 'company_name': symbol,
        'latest_close': 100.0, 'fundamental_tier': 'golden',
        'peg_ratio': 0.1, 'quarterly_profit_growth_pct': 35, 'quarterly_revenue_growth_pct': 30,
        'opm_pct': 45, 'roce_pct': 30, 'roa_pct': 20, 'pe_ratio': 12, 'price_to_book': 2,
        **GOOD_INDICATORS,
    }
    row.update(overrides)
    return row


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeAdminAlertsDB:
    """Stands in for SupabaseDB across get_all_highly_recommended_today's
    candidate fetch, record_and_send_highly_recommended_alerts' upsert into
    stock_admin_alerts, and find_and_notify_intraday_target_hits' pending
    lookups/updates on both stock_suggestions and stock_admin_alerts."""

    def __init__(self, candidate_rows=None, admin_alerts=None, suggestions=None, recipients=None):
        self.candidate_rows = candidate_rows or []
        self.admin_alerts = admin_alerts or []
        self.suggestions = suggestions or []
        self.recipients = recipients or []
        self.marked_target_hit_ids = []
        self._next_admin_alert_id = max([a['id'] for a in self.admin_alerts], default=0) + 1

    def execute(self, sql, params=None):
        params = params or ()
        normalized = ' '.join(sql.split())

        if normalized.startswith('SELECT w.id AS watchlist_id, w.symbol, w.exchange'):
            return FakeCursor(self.candidate_rows)

        if normalized.startswith('SELECT close FROM stock_daily_data WHERE watchlist_id=?'):
            return FakeCursor([])

        if normalized.startswith('SELECT universe_id, promoter_holding_pct, fii_holding_pct, snapshot_date'):
            return FakeCursor([])

        if normalized.startswith('SELECT watchlist_id, headline FROM stock_news'):
            return FakeCursor([])

        if normalized.startswith('INSERT INTO stock_admin_alerts'):
            (watchlist_id, alert_date, buy_price, target_sell_price, stop_loss_price,
             nns_score, nns_tier, pattern_name) = params
            fields = {
                'buy_price': buy_price, 'target_sell_price': target_sell_price,
                'stop_loss_price': stop_loss_price, 'nns_score': nns_score,
                'nns_tier': nns_tier, 'pattern_name': pattern_name,
            }
            existing = next(
                (a for a in self.admin_alerts
                 if a['watchlist_id'] == watchlist_id and a['alert_date'] == alert_date), None
            )
            if existing:
                existing.update(fields)
            else:
                self.admin_alerts.append({
                    'id': self._next_admin_alert_id, 'watchlist_id': watchlist_id,
                    'alert_date': alert_date, 'status': 'pending', **fields,
                })
                self._next_admin_alert_id += 1
            return FakeCursor([])

        if normalized.startswith('SELECT s.id, w.id AS watchlist_id, w.symbol, w.exchange, w.name AS company_name'):
            return FakeCursor([
                dict(s) for s in self.suggestions
                if s.get('status') == 'pending' and s.get('intraday_alert_sent_at') is None
            ])

        if normalized.startswith('SELECT a.id, w.id AS watchlist_id, w.symbol, w.exchange, w.name AS company_name'):
            return FakeCursor([dict(a) for a in self.admin_alerts if a.get('status') == 'pending'])

        if normalized.startswith('UPDATE stock_suggestions SET intraday_alert_sent_at'):
            (row_id,) = params
            for s in self.suggestions:
                if s['id'] == row_id:
                    s['intraday_alert_sent_at'] = 'set'
            return FakeCursor([])

        if normalized.startswith("UPDATE stock_admin_alerts SET status='target_hit'"):
            (row_id,) = params
            for a in self.admin_alerts:
                if a['id'] == row_id:
                    a['status'] = 'target_hit'
            return FakeCursor([])

        if normalized.startswith(
            "SELECT id, username AS email, name, is_pro, subscription_status, "
            "subscription_current_period_end, trial_ends_at FROM stocks_admin_users"
        ):
            return FakeCursor(self.recipients)

        if normalized.startswith("UPDATE stock_suggestions SET status='target_hit'"):
            ids = params
            self.marked_target_hit_ids.extend(ids)
            for s in self.suggestions:
                if s['id'] in ids:
                    s['status'] = 'target_hit'
            return FakeCursor([])

        raise AssertionError(f'Unexpected SQL in test: {sql}')

    def commit(self):
        pass


class FakeKiteClient:
    def __init__(self, prices=None):
        self._prices = prices or {}
        self.fetch_ltp_batch_calls = []

    def fetch_ltp_batch(self, instrument_keys):
        self.fetch_ltp_batch_calls.append(list(instrument_keys))
        return {k: v for k, v in self._prices.items() if k in instrument_keys}


# --- record_and_send_highly_recommended_alerts ---

def test_records_and_emails_every_golden_silver_candidate_with_no_cap():
    candidates = [_golden_candidate(1, 'GOLDCO'), _golden_candidate(2, 'GOLDCO2')]
    db = FakeAdminAlertsDB(candidate_rows=candidates)

    with patch('stoqbell.utils.admin_alerts.send_highly_recommended_alert_email') as mock_send:
        summary = record_and_send_highly_recommended_alerts(db)

    assert len(summary['alerted']) == 2
    assert mock_send.call_count == 2
    sent_to = {call.args[0] for call in mock_send.call_args_list}
    assert sent_to == {'raga2020@gmail.com'}
    assert len(db.admin_alerts) == 2
    assert {a['watchlist_id'] for a in db.admin_alerts} == {1, 2}


def test_rerunning_the_same_day_resends_rather_than_suppressing():
    candidates = [_golden_candidate(1, 'GOLDCO')]
    db = FakeAdminAlertsDB(candidate_rows=candidates)

    with patch('stoqbell.utils.admin_alerts.send_highly_recommended_alert_email') as mock_send:
        record_and_send_highly_recommended_alerts(db)
        record_and_send_highly_recommended_alerts(db)

    assert mock_send.call_count == 2  # sent again, not suppressed
    assert len(db.admin_alerts) == 1  # same day's row upserted, not duplicated


def test_no_alerts_when_nothing_clears_silver():
    weak = _golden_candidate(1, 'WEAKCO', peg_ratio=1.5, quarterly_profit_growth_pct=1,
                              quarterly_revenue_growth_pct=1, opm_pct=5, roce_pct=2, roa_pct=1)
    db = FakeAdminAlertsDB(candidate_rows=[weak])

    with patch('stoqbell.utils.admin_alerts.send_highly_recommended_alert_email') as mock_send:
        summary = record_and_send_highly_recommended_alerts(db)

    assert summary['alerted'] == []
    assert mock_send.call_count == 0


# --- find_and_notify_intraday_target_hits ---

def test_suggestion_hit_notifies_customers_and_marks_target_hit_when_no_recipients():
    # No current Standard-plan recipients -- nothing to send, but nothing
    # to retry for either, so it's still marked handled (same "no one to
    # tell" rule as the once-daily fallback job).
    suggestions = [{
        'id': 10, 'watchlist_id': 1, 'symbol': 'HITCO', 'exchange': 'NSE', 'company_name': 'Hit Co',
        'suggestion_date': '2026-08-01', 'buy_price': 100.0, 'target_sell_price': 110.0,
        'status': 'pending', 'intraday_alert_sent_at': None,
    }]
    db = FakeAdminAlertsDB(suggestions=suggestions, recipients=[])
    kite = FakeKiteClient(prices={'NSE:HITCO': 111.0})

    with patch('stoqbell.utils.admin_alerts.send_intraday_target_hit_alert_email') as mock_send:
        summary = find_and_notify_intraday_target_hits(db, kite_client=kite)

    assert len(summary['hits']) == 1
    assert summary['hits'][0]['source'] == 'suggestion'
    assert summary['hits'][0]['live_price'] == 111.0
    assert summary['customers_notified'] == 0
    assert db.suggestions[0]['intraday_alert_sent_at'] == 'set'
    assert db.suggestions[0]['status'] == 'target_hit'  # no recipients -- nothing to retry for
    mock_send.assert_called_once_with('raga2020@gmail.com', summary['hits'])


def test_suggestion_hit_emails_current_standard_subscribers_and_marks_target_hit():
    suggestions = [{
        'id': 10, 'watchlist_id': 1, 'symbol': 'HITCO', 'exchange': 'NSE', 'company_name': 'Hit Co',
        'suggestion_date': '2026-08-01', 'buy_price': 100.0, 'target_sell_price': 110.0,
        'status': 'pending', 'intraday_alert_sent_at': None,
    }]
    recipients = [
        {'id': 1, 'email': 'a@example.com', 'name': 'A', 'is_pro': 0, 'subscription_status': 'active',
         'subscription_current_period_end': None, 'trial_ends_at': None},
    ]
    db = FakeAdminAlertsDB(suggestions=suggestions, recipients=recipients)
    kite = FakeKiteClient(prices={'NSE:HITCO': 111.0})

    with patch('stoqbell.utils.admin_alerts.send_intraday_target_hit_alert_email'), \
         patch('stoqbell.utils.admin_alerts.send_target_achieved_email', return_value=(True, 'ok')) as mock_customer_send:
        summary = find_and_notify_intraday_target_hits(db, kite_client=kite)

    assert summary['customers_notified'] == 1
    mock_customer_send.assert_called_once()
    call_kwargs = mock_customer_send.call_args
    assert call_kwargs.args[0] == 'a@example.com'
    achievements = call_kwargs.args[2]
    assert achievements[0]['symbol'] == 'HITCO'
    assert achievements[0]['latest_price'] == 111.0
    assert db.suggestions[0]['status'] == 'target_hit'
    assert db.marked_target_hit_ids == [10]


def test_suggestion_hit_leaves_status_pending_when_every_customer_send_fails():
    # Recipients existed but nobody actually got the email -- must NOT mark
    # as notified, so the once-daily fallback job retries it tomorrow.
    suggestions = [{
        'id': 10, 'watchlist_id': 1, 'symbol': 'HITCO', 'exchange': 'NSE', 'company_name': 'Hit Co',
        'suggestion_date': '2026-08-01', 'buy_price': 100.0, 'target_sell_price': 110.0,
        'status': 'pending', 'intraday_alert_sent_at': None,
    }]
    recipients = [
        {'id': 1, 'email': 'a@example.com', 'name': 'A', 'is_pro': 0, 'subscription_status': 'active',
         'subscription_current_period_end': None, 'trial_ends_at': None},
    ]
    db = FakeAdminAlertsDB(suggestions=suggestions, recipients=recipients)
    kite = FakeKiteClient(prices={'NSE:HITCO': 111.0})

    with patch('stoqbell.utils.admin_alerts.send_intraday_target_hit_alert_email'), \
         patch('stoqbell.utils.admin_alerts.send_target_achieved_email', return_value=(False, 'smtp error')):
        summary = find_and_notify_intraday_target_hits(db, kite_client=kite)

    assert summary['customers_notified'] == 0
    assert db.suggestions[0]['intraday_alert_sent_at'] == 'set'  # this check won't retry it again
    assert db.suggestions[0]['status'] == 'pending'  # but the daily fallback job still will
    assert db.marked_target_hit_ids == []


def test_admin_alert_hit_sets_status_target_hit():
    admin_alerts = [{
        'id': 20, 'watchlist_id': 2, 'symbol': 'ALERTCO', 'exchange': 'NSE', 'company_name': 'Alert Co',
        'buy_price': 50.0, 'target_sell_price': 55.0, 'status': 'pending',
    }]
    db = FakeAdminAlertsDB(admin_alerts=admin_alerts)
    kite = FakeKiteClient(prices={'NSE:ALERTCO': 56.0})

    with patch('stoqbell.utils.admin_alerts.send_intraday_target_hit_alert_email'):
        summary = find_and_notify_intraday_target_hits(db, kite_client=kite)

    assert len(summary['hits']) == 1
    assert summary['hits'][0]['source'] == 'admin_alert'
    assert db.admin_alerts[0]['status'] == 'target_hit'


def test_multiple_simultaneous_hits_combine_into_one_email():
    suggestions = [{
        'id': 10, 'watchlist_id': 1, 'symbol': 'ONECO', 'exchange': 'NSE', 'company_name': 'One Co',
        'suggestion_date': '2026-08-01', 'buy_price': 100.0, 'target_sell_price': 105.0,
        'status': 'pending', 'intraday_alert_sent_at': None,
    }]
    admin_alerts = [{
        'id': 20, 'watchlist_id': 2, 'symbol': 'TWOCO', 'exchange': 'NSE', 'company_name': 'Two Co',
        'buy_price': 50.0, 'target_sell_price': 52.0, 'status': 'pending',
    }]
    db = FakeAdminAlertsDB(suggestions=suggestions, admin_alerts=admin_alerts)
    kite = FakeKiteClient(prices={'NSE:ONECO': 106.0, 'NSE:TWOCO': 53.0})

    with patch('stoqbell.utils.admin_alerts.send_intraday_target_hit_alert_email') as mock_send:
        summary = find_and_notify_intraday_target_hits(db, kite_client=kite)

    assert len(summary['hits']) == 2
    assert mock_send.call_count == 1  # one combined email, not one per hit


def test_no_email_sent_when_nothing_hits():
    suggestions = [{
        'id': 10, 'watchlist_id': 1, 'symbol': 'NOHIT', 'exchange': 'NSE', 'company_name': 'No Hit Co',
        'buy_price': 100.0, 'target_sell_price': 200.0, 'status': 'pending', 'intraday_alert_sent_at': None,
    }]
    db = FakeAdminAlertsDB(suggestions=suggestions)
    kite = FakeKiteClient(prices={'NSE:NOHIT': 105.0})  # nowhere near target

    with patch('stoqbell.utils.admin_alerts.send_intraday_target_hit_alert_email') as mock_send:
        summary = find_and_notify_intraday_target_hits(db, kite_client=kite)

    assert summary['hits'] == []
    mock_send.assert_not_called()


def test_already_alerted_suggestion_is_never_re_detected():
    suggestions = [{
        'id': 10, 'watchlist_id': 1, 'symbol': 'ALREADY', 'exchange': 'NSE', 'company_name': 'Already Co',
        'buy_price': 100.0, 'target_sell_price': 105.0, 'status': 'pending', 'intraday_alert_sent_at': 'set',
    }]
    db = FakeAdminAlertsDB(suggestions=suggestions)
    kite = FakeKiteClient(prices={'NSE:ALREADY': 110.0})

    summary = find_and_notify_intraday_target_hits(db, kite_client=kite)

    assert summary['hits'] == []
    assert summary['checked'] == 0  # already-alerted row isn't even fetched as pending


def test_missing_live_quote_is_not_treated_as_a_hit():
    suggestions = [{
        'id': 10, 'watchlist_id': 1, 'symbol': 'NOQUOTE', 'exchange': 'NSE', 'company_name': 'No Quote Co',
        'buy_price': 100.0, 'target_sell_price': 105.0, 'status': 'pending', 'intraday_alert_sent_at': None,
    }]
    db = FakeAdminAlertsDB(suggestions=suggestions)
    kite = FakeKiteClient(prices={})  # Kite has no quote for this symbol

    summary = find_and_notify_intraday_target_hits(db, kite_client=kite)

    assert summary['hits'] == []


def test_batches_all_distinct_symbols_into_one_kite_call():
    suggestions = [{
        'id': 10, 'watchlist_id': 1, 'symbol': 'AAACO', 'exchange': 'NSE', 'company_name': 'AAA Co',
        'buy_price': 100.0, 'target_sell_price': 200.0, 'status': 'pending', 'intraday_alert_sent_at': None,
    }]
    admin_alerts = [{
        'id': 20, 'watchlist_id': 2, 'symbol': 'BBBCO', 'exchange': 'BSE', 'company_name': 'BBB Co',
        'buy_price': 50.0, 'target_sell_price': 100.0, 'status': 'pending',
    }]
    db = FakeAdminAlertsDB(suggestions=suggestions, admin_alerts=admin_alerts)
    kite = FakeKiteClient(prices={})

    find_and_notify_intraday_target_hits(db, kite_client=kite)

    assert len(kite.fetch_ltp_batch_calls) == 1
    assert set(kite.fetch_ltp_batch_calls[0]) == {'NSE:AAACO', 'BSE:BBBCO'}
