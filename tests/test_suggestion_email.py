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
