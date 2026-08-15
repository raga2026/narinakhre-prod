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

        if normalized.startswith('SELECT w.symbol, w.exchange, s.suggestion_date, s.buy_price'):
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

    with patch('utils.suggestion_email.send_zeptomail_stocks_email', return_value=True) as mock_send:
        summary = send_daily_suggestions_email(db)

    mock_send.assert_called_once()
    kwargs = mock_send.call_args.kwargs
    assert kwargs['to_email'] == 'friend@example.com'
    assert 'no suggestions' in kwargs['subject'].lower()
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

    with patch('utils.suggestion_email.send_zeptomail_stocks_email', return_value=True) as mock_send:
        summary = send_daily_suggestions_email(db)

    assert mock_send.call_count == 2
    sent_to = {call.kwargs['to_email'] for call in mock_send.call_args_list}
    assert sent_to == {'a@example.com', 'b@example.com'}

    for call in mock_send.call_args_list:
        assert 'ABC' in call.kwargs['textbody']
        assert DISCLAIMER in call.kwargs['textbody']

    assert summary['suggestion_count'] == 1
    assert summary['sent'] == 2


def test_golden_suggestion_shows_golden_label_without_pe_or_opm():
    db = FakeEmailDB(
        suggestion_rows=[
            {'symbol': 'GLD', 'exchange': 'NSE', 'buy_price': 100.0, 'target_sell_price': 105.0,
             'stop_loss_price': 97.0, 'holding_period_days': 10, 'rationale': 'Golden cross with confirming volume',
             'fundamental_tier': 'golden', 'pe_at_suggestion': 20.0, 'opm_at_suggestion': 30.0},
        ],
        recipient_rows=[{'email': 'a@example.com', 'name': 'A'}],
    )

    with patch('utils.suggestion_email.send_zeptomail_stocks_email', return_value=True) as mock_send:
        send_daily_suggestions_email(db)

    textbody = mock_send.call_args.kwargs['textbody']
    htmlbody = mock_send.call_args.kwargs['htmlbody']
    assert 'GLD (NSE) — Golden:' in textbody
    assert '<td>Golden</td>' in htmlbody
    # Golden suggestions don't need their PE/OPM called out -- that's what
    # distinguishes them from silver ones.
    assert 'PE 20' not in textbody
    assert 'OPM 30' not in textbody


def test_silver_suggestion_shows_silver_label_with_pe_and_opm_values():
    db = FakeEmailDB(
        suggestion_rows=[
            {'symbol': 'SLV', 'exchange': 'NSE', 'buy_price': 50.0, 'target_sell_price': 52.5,
             'stop_loss_price': 48.5, 'holding_period_days': 10, 'rationale': 'Golden cross with confirming volume',
             'fundamental_tier': 'silver', 'pe_at_suggestion': 32.1, 'opm_at_suggestion': 18.0},
        ],
        recipient_rows=[{'email': 'a@example.com', 'name': 'A'}],
    )

    with patch('utils.suggestion_email.send_zeptomail_stocks_email', return_value=True) as mock_send:
        send_daily_suggestions_email(db)

    textbody = mock_send.call_args.kwargs['textbody']
    htmlbody = mock_send.call_args.kwargs['htmlbody']
    assert 'SLV (NSE) — Silver (PE 32.10, OPM 18%):' in textbody
    assert 'Silver (PE 32.10, OPM 18%)' in htmlbody


def test_suggestion_predating_tier_column_shows_no_tier_label():
    # An old suggestion row from before fundamental_tier existed -- no key
    # at all, not just None. Must not crash, and shows no tier text.
    db = FakeEmailDB(
        suggestion_rows=[
            {'symbol': 'OLD', 'exchange': 'NSE', 'buy_price': 10.0, 'target_sell_price': 10.5,
             'stop_loss_price': 9.7, 'holding_period_days': 10, 'rationale': 'Golden cross with confirming volume'},
        ],
        recipient_rows=[{'email': 'a@example.com', 'name': 'A'}],
    )

    with patch('utils.suggestion_email.send_zeptomail_stocks_email', return_value=True) as mock_send:
        send_daily_suggestions_email(db)

    textbody = mock_send.call_args.kwargs['textbody']
    assert 'OLD (NSE):' in textbody  # no " — ..." tier suffix at all


def test_a_failed_send_is_counted_without_stopping_the_rest():
    db = FakeEmailDB(
        suggestion_rows=[],
        recipient_rows=[
            {'email': 'good@example.com', 'name': 'Good'},
            {'email': 'bad@example.com', 'name': 'Bad'},
        ],
    )

    with patch('utils.suggestion_email.send_zeptomail_stocks_email', side_effect=[False, True]):
        summary = send_daily_suggestions_email(db)

    assert summary['sent'] == 1
    assert summary['failed'] == 1


def test_viewer_welcome_email_includes_login_link_username_and_password():
    with patch('utils.suggestion_email.send_zeptomail_stocks_email', return_value=True) as mock_send:
        result = send_viewer_welcome_email('new@example.com', 'New Viewer', 'r4nd0mPass123')

    assert result is True
    mock_send.assert_called_once()
    kwargs = mock_send.call_args.kwargs
    assert kwargs['to_email'] == 'new@example.com'
    assert kwargs['to_name'] == 'New Viewer'
    assert 'new@example.com' in kwargs['textbody']
    assert 'r4nd0mPass123' in kwargs['textbody']
    assert 'r4nd0mPass123' in kwargs['htmlbody']
    assert '/stocks/login' in kwargs['textbody']
    assert DISCLAIMER in kwargs['textbody']


def test_viewer_welcome_email_falls_back_to_email_as_greeting_when_no_name():
    with patch('utils.suggestion_email.send_zeptomail_stocks_email', return_value=True) as mock_send:
        send_viewer_welcome_email('noname@example.com', '', 'somepass')

    kwargs = mock_send.call_args.kwargs
    assert kwargs['to_name'] == 'noname@example.com'
