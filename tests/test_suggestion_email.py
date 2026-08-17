from unittest.mock import patch

from utils.suggestion_email import DISCLAIMER, send_daily_suggestions_email, send_viewer_welcome_email


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeEmailDB:
    def __init__(self, suggestion_rows, recipient_rows):
        self.suggestion_rows = suggestion_rows
        self.recipient_rows = recipient_rows

    def execute(self, sql, params=None):
        normalized = ' '.join(sql.split())

        if normalized.startswith('SELECT w.id AS watchlist_id, w.symbol, w.exchange, s.suggestion_date, s.buy_price'):
            return FakeCursor(self.suggestion_rows)

        if normalized.startswith("SELECT username AS email, name FROM stocks_admin_users WHERE role='viewer'"):
            return FakeCursor(self.recipient_rows)

        raise AssertionError(f'Unexpected SQL in test: {sql}')

    def commit(self):
        pass


def test_no_suggestions_today_still_sends_an_email_not_nothing():
    db = FakeEmailDB(
        suggestion_rows=[],
        recipient_rows=[{'email': 'friend@example.com', 'name': 'A Friend'}],
    )

    with patch('utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        summary = send_daily_suggestions_email(db)

    mock_send.assert_called_once()
    kwargs = mock_send.call_args.kwargs
    assert kwargs['to_email'] == 'friend@example.com'
    assert 'no recommendations today' in kwargs['subject'].lower()
    assert DISCLAIMER in kwargs['textbody']
    assert DISCLAIMER in kwargs['htmlbody']

    assert summary['suggestion_count'] == 0
    assert summary['recipient_count'] == 1
    assert summary['sent'] == 1
    assert summary['failed'] == 0


def test_email_sent_to_every_active_recipient_and_includes_disclaimer():
    db = FakeEmailDB(
        suggestion_rows=[
            {'symbol': 'ABC', 'exchange': 'NSE', 'buy_price': 100.0, 'target_sell_price': 105.0,
             'stop_loss_price': 97.0, 'holding_period_days': 10, 'rationale': 'Golden cross with confirming volume'},
        ],
        recipient_rows=[
            {'email': 'a@example.com', 'name': 'A'},
            {'email': 'b@example.com', 'name': 'B'},
        ],
    )

    with patch('utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        summary = send_daily_suggestions_email(db)

    assert mock_send.call_count == 2
    sent_to = {call.kwargs['to_email'] for call in mock_send.call_args_list}
    assert sent_to == {'a@example.com', 'b@example.com'}

    for call in mock_send.call_args_list:
        assert 'ABC' in call.kwargs['textbody']
        assert DISCLAIMER in call.kwargs['textbody']

    assert summary['suggestion_count'] == 1
    assert summary['sent'] == 2


def test_golden_nns_suggestion_shows_highly_recommended_without_pe_or_opm():
    db = FakeEmailDB(
        suggestion_rows=[
            {'symbol': 'GLD', 'exchange': 'NSE', 'buy_price': 100.0, 'target_sell_price': 105.0,
             'stop_loss_price': 97.0, 'holding_period_days': 10, 'rationale': 'Golden cross with confirming volume',
             'fundamental_tier': 'golden', 'pe_at_suggestion': 20.0, 'opm_at_suggestion': 30.0,
             'nns_score': 8.7, 'nns_tier': 'golden'},
        ],
        recipient_rows=[{'email': 'a@example.com', 'name': 'A'}],
    )

    with patch('utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        send_daily_suggestions_email(db)

    textbody = mock_send.call_args.kwargs['textbody']
    htmlbody = mock_send.call_args.kwargs['htmlbody']
    assert 'GLD (NSE) — Highly Recommended:' in textbody
    assert '<td>Highly Recommended</td>' in htmlbody
    # The raw NNS score number must never be shown to customers.
    assert '8.7' not in textbody
    assert '8.7' not in htmlbody
    # Golden (fundamental_tier) suggestions don't need PE/OPM called out --
    # that's specifically what distinguishes a silver watchlist pick.
    assert 'PE 20' not in textbody
    assert 'OPM 30' not in textbody


def test_silver_nns_suggestion_shows_fundamentals_note_with_pe_and_opm_values_and_no_score():
    db = FakeEmailDB(
        suggestion_rows=[
            {'symbol': 'SLV', 'exchange': 'NSE', 'buy_price': 50.0, 'target_sell_price': 52.5,
             'stop_loss_price': 48.5, 'holding_period_days': 10, 'rationale': 'Golden cross with confirming volume',
             'fundamental_tier': 'silver', 'pe_at_suggestion': 32.1, 'opm_at_suggestion': 18.0,
             'nns_score': 6.4, 'nns_tier': 'silver'},
        ],
        recipient_rows=[{'email': 'a@example.com', 'name': 'A'}],
    )

    with patch('utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        send_daily_suggestions_email(db)

    textbody = mock_send.call_args.kwargs['textbody']
    htmlbody = mock_send.call_args.kwargs['htmlbody']
    assert 'SLV (NSE) — Highly Recommended (watchlisted on the silver criteria: PE 32.10, OPM 18%):' in textbody
    assert 'watchlisted on the silver criteria: PE 32.10, OPM 18%' in htmlbody
    assert '6.4' not in textbody
    assert '6.4' not in htmlbody


def test_suggestion_predating_nns_score_falls_back_to_plain_recommended_not_a_crash():
    # An old suggestion row from before the NNS Score columns existed -- no
    # keys at all, not just None. Must not crash, and falls back to a plain
    # "Recommended" trend label instead of fabricating a tier.
    db = FakeEmailDB(
        suggestion_rows=[
            {'symbol': 'OLD', 'exchange': 'NSE', 'buy_price': 10.0, 'target_sell_price': 10.5,
             'stop_loss_price': 9.7, 'holding_period_days': 10, 'rationale': 'Golden cross with confirming volume'},
        ],
        recipient_rows=[{'email': 'a@example.com', 'name': 'A'}],
    )

    with patch('utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        send_daily_suggestions_email(db)

    textbody = mock_send.call_args.kwargs['textbody']
    assert 'OLD (NSE) — Recommended:' in textbody


def test_pattern_based_suggestion_shows_pattern_note_instead_of_hold_days():
    db = FakeEmailDB(
        suggestion_rows=[
            {'symbol': 'PTN', 'exchange': 'NSE', 'buy_price': 100.0, 'target_sell_price': 135.4,
             'stop_loss_price': 92.4, 'holding_period_days': 10, 'rationale': 'Golden cross with confirming volume',
             'pattern_name': 'head_and_shoulders_bottom',
             'pattern_note': 'Target and stop-loss are based on a reverse head-and-shoulders pattern... '
                              'There is no reliable way to predict exact timing from chart shape alone.'},
        ],
        recipient_rows=[{'email': 'a@example.com', 'name': 'A'}],
    )

    with patch('utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        send_daily_suggestions_email(db)

    textbody = mock_send.call_args.kwargs['textbody']
    htmlbody = mock_send.call_args.kwargs['htmlbody']
    assert 'no reliable way to predict exact timing' in textbody
    assert 'no reliable way to predict exact timing' in htmlbody
    assert 'Hold 10 days' not in textbody


def test_a_failed_send_is_counted_without_stopping_the_rest():
    db = FakeEmailDB(
        suggestion_rows=[],
        recipient_rows=[
            {'email': 'good@example.com', 'name': 'Good'},
            {'email': 'bad@example.com', 'name': 'Bad'},
        ],
    )

    with patch('utils.suggestion_email.send_zeptomail_stocks_email',
               side_effect=[(False, 'Zeptomail HTTP 500: server error'), (True, 'ok')]):
        summary = send_daily_suggestions_email(db)

    assert summary['sent'] == 1
    assert summary['failed'] == 1
    assert summary['failures'] == [{'email': 'good@example.com', 'error': 'Zeptomail HTTP 500: server error'}]


def test_viewer_welcome_email_includes_login_link_username_and_password():
    with patch('utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        sent, detail = send_viewer_welcome_email('new@example.com', 'New Viewer', 'r4nd0mPass123')

    assert sent is True
    assert detail == 'ok'
    mock_send.assert_called_once()
    kwargs = mock_send.call_args.kwargs
    assert kwargs['to_email'] == 'new@example.com'
    assert kwargs['to_name'] == 'New Viewer'
    assert 'new@example.com' in kwargs['textbody']
    assert 'r4nd0mPass123' in kwargs['textbody']
    assert 'r4nd0mPass123' in kwargs['htmlbody']
    assert '/stocks/login' in kwargs['textbody']
    assert DISCLAIMER in kwargs['textbody']


def test_viewer_welcome_email_propagates_the_real_failure_reason():
    # This is the exact bug the "check the Zeptomail config" flash message
    # used to hide -- send_viewer_welcome_email must hand back the actual
    # reason (missing config, Zeptomail's own error, etc.), not just a bool.
    with patch('utils.suggestion_email.send_zeptomail_stocks_email',
               return_value=(False, 'Missing: STOCKS_ZEPTOMAIL_API_KEY.')):
        sent, detail = send_viewer_welcome_email('new@example.com', 'New Viewer', 'pass123')

    assert sent is False
    assert detail == 'Missing: STOCKS_ZEPTOMAIL_API_KEY.'


def test_viewer_welcome_email_falls_back_to_email_as_greeting_when_no_name():
    with patch('utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        send_viewer_welcome_email('noname@example.com', '', 'somepass')

    kwargs = mock_send.call_args.kwargs
    assert kwargs['to_name'] == 'noname@example.com'
