import pytest
from unittest.mock import patch

from stoqbell.utils.suggestion_email import (
    DISCLAIMER,
    STOCKS_LOGIN_URL,
    send_activation_reminder_email,
    send_admin_new_subscriber_email,
    send_admin_subscription_cancelled_email,
    send_daily_suggestions_email,
    send_rebrand_announcement_email,
    send_rebrand_announcement_to_all_viewers,
    send_subscription_welcome_email,
    send_target_achieved_email,
    send_target_hit_email,
    send_trading_alert_email,
    send_viewer_welcome_email,
    send_weekly_starters_email,
    send_large_cap_bonus_email,
)
from stoqbell.utils.price_pattern import compute_projection_targets


@pytest.fixture(autouse=True)
def _no_real_supabase_uploads():
    """Every stock card in this suite calls build_prediction_chart_image_url,
    which now uploads to Supabase Storage (see suggestion_chart.py) instead
    of returning a base64 data: URI -- autouse so no test in this file ever
    makes a real network call by accident. Individual tests that care about
    the resulting URL can still patch this themselves with a specific
    return_value; this fixture just sets a safe default."""
    with patch('stoqbell.utils.suggestion_chart.upload_bytes_to_supabase',
               return_value='https://example.supabase.co/storage/v1/object/public/products/stoqbell/charts/test.png'):
        yield


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
        self.last_query_params = None
        self.last_recipient_sql = None

    def execute(self, sql, params=None):
        normalized = ' '.join(sql.split())

        if (normalized.startswith('SELECT DISTINCT ON (s.suggestion_date) s.id AS suggestion_id,')
                and 'FROM stock_suggestions s' in normalized):
            self.last_query_params = params
            return FakeCursor(self.suggestion_rows)

        if normalized.startswith("SELECT id, username AS email, name FROM stocks_admin_users WHERE role='viewer'"):
            self.last_recipient_sql = normalized
            if 'AND id IN (' in normalized:
                wanted = set(params)
                return FakeCursor([r for r in self.recipient_rows if r.get('id') in wanted])
            return FakeCursor(self.recipient_rows)

        # get_or_create_referral_code's queries -- looks up/persists against
        # the same recipient_rows fixture. Most tests' recipient_rows don't
        # set a 'referral_code' or even an 'id' at all, in which case this
        # always falls through to "no code yet"/"not found", which
        # send_daily_suggestions_email's own try/except already treats as
        # "skip the footer for this recipient" rather than an error.
        if normalized.startswith('SELECT referral_code FROM stocks_admin_users WHERE id=?'):
            admin_id, = params
            matches = [r for r in self.recipient_rows if r.get('id') == admin_id and r.get('referral_code')]
            return FakeCursor(matches[:1])
        if normalized.startswith('SELECT id FROM stocks_admin_users WHERE referral_code=?'):
            code, = params
            matches = [r for r in self.recipient_rows if r.get('referral_code') == code]
            return FakeCursor(matches[:1])
        if normalized.startswith('UPDATE stocks_admin_users SET referral_code=?'):
            code, admin_id = params
            for r in self.recipient_rows:
                if r.get('id') == admin_id:
                    r['referral_code'] = code
            return FakeCursor([])

        if normalized.startswith('INSERT INTO stock_email_deliveries'):
            return FakeCursor([])

        raise AssertionError(f'Unexpected SQL in test: {sql}')

    def commit(self):
        pass


def test_no_suggestions_today_still_sends_an_email_not_nothing():
    db = FakeEmailDB(
        suggestion_rows=[],
        recipient_rows=[{'email': 'friend@example.com', 'name': 'A Friend'}],
    )

    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
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

    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        summary = send_daily_suggestions_email(db)

    assert mock_send.call_count == 2
    sent_to = {call.kwargs['to_email'] for call in mock_send.call_args_list}
    assert sent_to == {'a@example.com', 'b@example.com'}

    for call in mock_send.call_args_list:
        assert 'ABC' in call.kwargs['textbody']
        assert DISCLAIMER in call.kwargs['textbody']

    assert summary['suggestion_count'] == 1
    assert summary['sent'] == 2


def test_daily_email_excludes_accounts_with_a_pending_first_password_change():
    # A viewer created with start_trial=True whose trial hasn't started yet
    # (they haven't logged in and set their own password -- see
    # create_viewer_account/change_own_password) must not receive the
    # recommendation blast; asserting on the SQL text is what proves the
    # production query actually excludes them, not just "no rows happened
    # to match" in this particular fixture.
    db = FakeEmailDB(
        suggestion_rows=[{'symbol': 'ABC', 'exchange': 'NSE', 'buy_price': 100.0, 'target_sell_price': 105.0, 'stop_loss_price': 97.0, 'holding_period_days': 10, 'rationale': 'Golden cross with confirming volume'}],
        recipient_rows=[{'email': 'a@example.com', 'name': 'A'}],
    )
    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')):
        send_daily_suggestions_email(db)
    assert 'trial_pending_password_change' in db.last_recipient_sql


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

    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        send_daily_suggestions_email(db)

    textbody = mock_send.call_args.kwargs['textbody']
    htmlbody = mock_send.call_args.kwargs['htmlbody']
    assert 'GLD (NSE) — Highly Recommended' in textbody
    assert '>Highly Recommended<' in htmlbody
    # The raw NNS score number must never be shown to customers.
    assert '8.7' not in textbody
    assert '8.7' not in htmlbody
    # Golden (fundamental_tier) suggestions don't need PE/OPM called out --
    # that's specifically what distinguishes a silver watchlist pick.
    assert 'PE 20' not in textbody
    assert 'OPM 30' not in textbody


def test_company_name_is_shown_when_known_falls_back_to_symbol_when_not():
    db = FakeEmailDB(
        suggestion_rows=[
            {'symbol': 'GLD', 'exchange': 'NSE', 'company_name': 'Golden Traders Ltd', 'buy_price': 100.0,
             'target_sell_price': 105.0, 'stop_loss_price': 97.0, 'holding_period_days': 10,
             'rationale': 'Golden cross with confirming volume'},
            {'symbol': 'NONAME', 'exchange': 'NSE', 'buy_price': 10.0, 'target_sell_price': 10.5,
             'stop_loss_price': 9.7, 'holding_period_days': 10, 'rationale': 'Golden cross with confirming volume'},
        ],
        recipient_rows=[{'email': 'a@example.com', 'name': 'A'}],
    )

    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        send_daily_suggestions_email(db)

    textbody = mock_send.call_args.kwargs['textbody']
    htmlbody = mock_send.call_args.kwargs['htmlbody']
    # A known company name is shown up front, with symbol/exchange alongside it.
    assert 'Golden Traders Ltd (GLD · NSE)' in textbody
    assert '>Golden Traders Ltd<' in htmlbody
    # No company name on file -- falls back to the symbol, not shown twice.
    assert 'NONAME (NSE)' in textbody
    assert 'NONAME (NONAME · NSE)' not in textbody


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

    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        send_daily_suggestions_email(db)

    textbody = mock_send.call_args.kwargs['textbody']
    htmlbody = mock_send.call_args.kwargs['htmlbody']
    assert 'SLV (NSE) — Highly Recommended' in textbody
    assert 'Watchlisted on the silver criteria: PE 32.10, OPM 18%' in textbody
    assert 'Watchlisted on the silver criteria: PE 32.10, OPM 18%' in htmlbody
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

    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        send_daily_suggestions_email(db)

    textbody = mock_send.call_args.kwargs['textbody']
    assert 'OLD (NSE) — Recommended' in textbody


def test_suggestion_with_no_pattern_shows_generic_extrapolated_projection_and_a_chart():
    db = FakeEmailDB(
        suggestion_rows=[
            {'symbol': 'GLD', 'exchange': 'NSE', 'buy_price': 100.0, 'target_sell_price': 105.0,
             'stop_loss_price': 97.0, 'holding_period_days': 10, 'rationale': 'Golden cross with confirming volume'},
        ],
        recipient_rows=[{'email': 'a@example.com', 'name': 'A'}],
    )

    fake_chart_url = 'https://example.supabase.co/storage/v1/object/public/products/stoqbell/charts/abc123.png'
    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send, \
         patch('stoqbell.utils.suggestion_chart.upload_bytes_to_supabase', return_value=fake_chart_url):
        send_daily_suggestions_email(db)

    textbody = mock_send.call_args.kwargs['textbody']
    htmlbody = mock_send.call_args.kwargs['htmlbody']
    # No confirmed pattern -- generic ~6 month / ~1 year fallback, clearly labeled.
    assert '~6 months' in textbody and '~6 months' in htmlbody
    assert '~1 year' in textbody and '~1 year' in htmlbody
    assert 'extrapolated -- no confirmed chart pattern' in textbody
    # A chart image is embedded via its Supabase-hosted public URL, not a
    # base64 data: URI (many mail clients, Outlook desktop included, strip
    # data: URIs -- see suggestion_chart.py).
    assert f'<img src="{fake_chart_url}"' in htmlbody
    # The disclaimer explaining the two methods appears once, not per-stock.
    assert 'not a fixed calendar point' in textbody
    assert textbody.count('not a fixed calendar point') == 1


def test_suggestion_with_confirmed_pattern_shows_its_own_pattern_specific_projection():
    # A pattern-based suggestion's own duration comes from that SPECIFIC
    # pattern's published research (see PATTERN_RESEARCH_CONTEXT), not the
    # generic ~6 month/~1 year fallback -- and the long-term figure lands
    # exactly on the pattern's own measured-move target.
    db = FakeEmailDB(
        suggestion_rows=[
            {'symbol': 'HNS', 'exchange': 'NSE', 'buy_price': 100.0, 'target_sell_price': 130.0,
             'stop_loss_price': 95.0, 'holding_period_days': 10, 'rationale': 'Reverse head-and-shoulders confirmed',
             'pattern_name': 'head_and_shoulders_bottom', 'pattern_note': 'Target and stop-loss are based on a reverse head-and-shoulders pattern...'},
        ],
        recipient_rows=[{'email': 'a@example.com', 'name': 'A'}],
    )

    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        send_daily_suggestions_email(db)

    textbody = mock_send.call_args.kwargs['textbody']
    htmlbody = mock_send.call_args.kwargs['htmlbody']
    assert '~2.5 months' in textbody
    assert '~3 months' in textbody
    assert 'Rs 130' in textbody  # long-term figure is the pattern's own target, unchanged
    assert 'based on the detected reverse head-and-shoulders pattern' in textbody
    assert 'Based on the detected reverse head-and-shoulders pattern' in htmlbody
    # This stock's own card isn't labeled as the generic extrapolated fallback.
    assert 'extrapolated -- no confirmed chart pattern' not in textbody


def test_rounding_bottom_suggestion_gets_a_different_period_than_head_and_shoulders():
    db = FakeEmailDB(
        suggestion_rows=[
            {'symbol': 'RND', 'exchange': 'NSE', 'buy_price': 100.0, 'target_sell_price': 150.0,
             'stop_loss_price': 92.0, 'holding_period_days': 10, 'rationale': 'Rounding bottom confirmed',
             'pattern_name': 'rounding_bottom', 'pattern_note': 'Target and stop-loss are based on a rounding-bottom pattern...'},
        ],
        recipient_rows=[{'email': 'a@example.com', 'name': 'A'}],
    )

    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        send_daily_suggestions_email(db)

    textbody = mock_send.call_args.kwargs['textbody']
    assert '~7.6 months' in textbody
    assert '~1 year' in textbody
    assert 'based on the detected rounding-bottom pattern' in textbody


def test_suggestion_missing_prices_gets_no_projection_or_chart():
    # A row with no target_sell_price at all (defensive: shouldn't happen in
    # practice, but compute_projection_targets/the chart builder must
    # degrade gracefully rather than crash the whole email).
    db = FakeEmailDB(
        suggestion_rows=[
            {'symbol': 'BAD', 'exchange': 'NSE', 'buy_price': 100.0, 'target_sell_price': None,
             'stop_loss_price': 97.0, 'holding_period_days': 10, 'rationale': 'Golden cross with confirming volume'},
        ],
        recipient_rows=[{'email': 'a@example.com', 'name': 'A'}],
    )

    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send, \
         patch('stoqbell.utils.suggestion_chart.upload_bytes_to_supabase') as mock_upload:
        send_daily_suggestions_email(db)

    htmlbody = mock_send.call_args.kwargs['htmlbody']
    # No target_sell_price -- no projection to draw, so the chart builder
    # never even gets called (nothing to upload), and no chart block
    # renders in the email.
    mock_upload.assert_not_called()
    assert 'Price Projection' not in htmlbody


def test_short_term_target_price_is_shown_alongside_buy_and_stop_loss():
    # Regression: the near-term target_sell_price (what the suggestion's
    # holding_period_days is actually based on) must be visible as its own
    # labeled figure, not just implicitly folded into the mid-period/
    # long-term projection block.
    db = FakeEmailDB(
        suggestion_rows=[
            {'symbol': 'GLD', 'exchange': 'NSE', 'buy_price': 100.0, 'target_sell_price': 105.0,
             'stop_loss_price': 97.0, 'holding_period_days': 10, 'rationale': 'Golden cross with confirming volume'},
        ],
        recipient_rows=[{'email': 'a@example.com', 'name': 'A'}],
    )

    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        send_daily_suggestions_email(db)

    textbody = mock_send.call_args.kwargs['textbody']
    htmlbody = mock_send.call_args.kwargs['htmlbody']
    assert 'Target (10-day): Rs 105.0' in textbody
    assert '>Target (10-day)<' in htmlbody
    assert '>Rs 105.0' in htmlbody
    # % increase from buy price (100.0 -> 105.0 is +5.0%) shown beside the target.
    assert '(+5.0%)' in textbody
    assert '(+5.0%)' in htmlbody


def test_projection_prices_show_pct_increase_from_buy_price():
    # Regression: mid-period/long-term projected prices must each show how
    # much higher they are than the buy price, not just the bare Rs figure.
    buy_price, target_price = 100.0, 105.0
    db = FakeEmailDB(
        suggestion_rows=[
            {'symbol': 'GLD', 'exchange': 'NSE', 'buy_price': buy_price, 'target_sell_price': target_price,
             'stop_loss_price': 97.0, 'holding_period_days': 10, 'rationale': 'Golden cross with confirming volume'},
        ],
        recipient_rows=[{'email': 'a@example.com', 'name': 'A'}],
    )
    projection = compute_projection_targets(buy_price, target_price, None)
    mid_pct = round((projection['mid_period']['price'] - buy_price) / buy_price * 100, 1)
    long_pct = round((projection['long_term']['price'] - buy_price) / buy_price * 100, 1)

    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        send_daily_suggestions_email(db)

    textbody = mock_send.call_args.kwargs['textbody']
    htmlbody = mock_send.call_args.kwargs['htmlbody']
    assert f'(+{mid_pct}%)' in textbody
    assert f'(+{long_pct}%)' in textbody
    assert f'(+{mid_pct}%)' in htmlbody
    assert f'(+{long_pct}%)' in htmlbody


def test_pattern_based_suggestion_target_label_has_no_day_count_claim():
    db = FakeEmailDB(
        suggestion_rows=[
            {'symbol': 'PTN', 'exchange': 'NSE', 'buy_price': 100.0, 'target_sell_price': 130.0,
             'stop_loss_price': 95.0, 'holding_period_days': 10, 'rationale': 'Reverse head-and-shoulders confirmed',
             'pattern_name': 'head_and_shoulders_bottom',
             'pattern_note': 'Target and stop-loss are based on a reverse head-and-shoulders pattern...'},
        ],
        recipient_rows=[{'email': 'a@example.com', 'name': 'A'}],
    )

    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        send_daily_suggestions_email(db)

    textbody = mock_send.call_args.kwargs['textbody']
    assert 'Target: Rs 130.0' in textbody
    assert '10-day' not in textbody.split('Why:')[0]  # no day-count claim near the target itself


def test_html_is_a_real_responsive_document_not_a_bare_fragment():
    # The original bug report: htmlbody went out with no <head>/viewport at
    # all, which is exactly what makes an email render tiny/unzoomed on a
    # phone -- this locks in the fix.
    db = FakeEmailDB(
        suggestion_rows=[
            {'symbol': 'GLD', 'exchange': 'NSE', 'buy_price': 100.0, 'target_sell_price': 105.0,
             'stop_loss_price': 97.0, 'holding_period_days': 10, 'rationale': 'Golden cross with confirming volume'},
        ],
        recipient_rows=[{'email': 'a@example.com', 'name': 'A'}],
    )

    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        send_daily_suggestions_email(db)

    htmlbody = mock_send.call_args.kwargs['htmlbody']
    assert htmlbody.strip().startswith('<!doctype html>')
    assert 'name="viewport" content="width=device-width' in htmlbody
    assert '@media only screen and (max-width' in htmlbody
    # No-suggestions path must be wrapped the same way, not just the main one.
    empty_db = FakeEmailDB(suggestion_rows=[], recipient_rows=[{'email': 'a@example.com', 'name': 'A'}])
    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send2:
        send_daily_suggestions_email(empty_db)
    empty_htmlbody = mock_send2.call_args.kwargs['htmlbody']
    assert empty_htmlbody.strip().startswith('<!doctype html>')
    assert 'name="viewport"' in empty_htmlbody


def test_chart_image_is_fluid_width_not_a_fixed_pixel_size():
    db = FakeEmailDB(
        suggestion_rows=[
            {'symbol': 'GLD', 'exchange': 'NSE', 'buy_price': 100.0, 'target_sell_price': 105.0,
             'stop_loss_price': 97.0, 'holding_period_days': 10, 'rationale': 'Golden cross with confirming volume'},
        ],
        recipient_rows=[{'email': 'a@example.com', 'name': 'A'}],
    )

    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        send_daily_suggestions_email(db)

    htmlbody = mock_send.call_args.kwargs['htmlbody']
    assert 'width="100%"' in htmlbody
    assert 'height:auto' in htmlbody
    # A fixed pixel width attribute would prevent the image from shrinking
    # to fit a narrow phone screen -- must not be present on the chart img.
    assert 'width="520"' not in htmlbody
    # The chart sits inside its own bordered/padded container, not bare.
    assert 'Price Projection' in htmlbody


def test_highlights_show_available_technical_and_fundamental_figures():
    db = FakeEmailDB(
        suggestion_rows=[
            {'symbol': 'GLD', 'exchange': 'NSE', 'buy_price': 100.0, 'target_sell_price': 105.0,
             'stop_loss_price': 97.0, 'holding_period_days': 10, 'rationale': 'Golden cross with confirming volume',
             'rsi_at_suggestion': 58.234, 'pe_at_suggestion': 22.567, 'peg_at_suggestion': 1.234,
             'opm_at_suggestion': 19.8},
        ],
        recipient_rows=[{'email': 'a@example.com', 'name': 'A'}],
    )

    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        send_daily_suggestions_email(db)

    textbody = mock_send.call_args.kwargs['textbody']
    htmlbody = mock_send.call_args.kwargs['htmlbody']
    assert 'RSI: 58.2' in textbody
    assert 'PE: 22.57' in textbody
    assert 'PEG: 1.23' in textbody
    assert 'OPM: 20%' in textbody
    assert 'RSI</span> 58.2' in htmlbody


def test_highlights_omit_missing_fields_without_crashing():
    db = FakeEmailDB(
        suggestion_rows=[
            {'symbol': 'GLD', 'exchange': 'NSE', 'buy_price': 100.0, 'target_sell_price': 105.0,
             'stop_loss_price': 97.0, 'holding_period_days': 10, 'rationale': 'Golden cross with confirming volume'},
        ],
        recipient_rows=[{'email': 'a@example.com', 'name': 'A'}],
    )

    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        send_daily_suggestions_email(db)

    textbody = mock_send.call_args.kwargs['textbody']
    assert 'RSI:' not in textbody
    assert 'PE:' not in textbody


def test_stock_link_points_to_the_any_viewer_accessible_universe_page():
    # /stocks/company/<watchlist_id> is staff/can_view_watchlist-only and
    # would 403 for a plain self-serve viewer -- the email must link to
    # /stocks/universe/<universe_id> instead, which any logged-in role can
    # open (see get_suggestions' docstring).
    db = FakeEmailDB(
        suggestion_rows=[
            {'symbol': 'GLD', 'exchange': 'NSE', 'buy_price': 100.0, 'target_sell_price': 105.0,
             'stop_loss_price': 97.0, 'holding_period_days': 10, 'rationale': 'Golden cross with confirming volume',
             'universe_id': 77},
        ],
        recipient_rows=[{'email': 'a@example.com', 'name': 'A'}],
    )

    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        send_daily_suggestions_email(db)

    textbody = mock_send.call_args.kwargs['textbody']
    htmlbody = mock_send.call_args.kwargs['htmlbody']
    assert 'https://www.stoqbell.com/stocks/universe/77' in textbody
    assert 'https://www.stoqbell.com/stocks/universe/77' in htmlbody
    assert '/stocks/company/' not in htmlbody


def test_no_stock_link_when_universe_id_unresolved():
    # A company that's since been removed from stock_universe -- universe_id
    # comes back None from the LEFT JOIN. Must not build a broken link.
    db = FakeEmailDB(
        suggestion_rows=[
            {'symbol': 'GLD', 'exchange': 'NSE', 'buy_price': 100.0, 'target_sell_price': 105.0,
             'stop_loss_price': 97.0, 'holding_period_days': 10, 'rationale': 'Golden cross with confirming volume'},
        ],
        recipient_rows=[{'email': 'a@example.com', 'name': 'A'}],
    )

    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        send_daily_suggestions_email(db)

    htmlbody = mock_send.call_args.kwargs['htmlbody']
    assert 'stocks/universe/' not in htmlbody


def test_resend_uses_the_given_date_instead_of_today():
    db = FakeEmailDB(
        suggestion_rows=[
            {'symbol': 'OLDPICK', 'exchange': 'NSE', 'buy_price': 40.0, 'target_sell_price': 42.0,
             'stop_loss_price': 38.8, 'holding_period_days': 10, 'rationale': 'Golden cross with confirming volume'},
        ],
        recipient_rows=[{'email': 'a@example.com', 'name': 'A'}],
    )

    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        summary = send_daily_suggestions_email(db, target_date='2026-07-01')

    assert summary['suggestion_count'] == 1
    kwargs = mock_send.call_args.kwargs
    assert '01 Jul 2026' in kwargs['subject']
    assert db.last_query_params == ('2026-07-01', '2026-07-01')


def test_recipient_ids_restricts_the_send_to_just_those_accounts():
    db = FakeEmailDB(
        suggestion_rows=[
            {'symbol': 'GLD', 'exchange': 'NSE', 'buy_price': 100.0, 'target_sell_price': 105.0,
             'stop_loss_price': 97.0, 'holding_period_days': 10, 'rationale': 'Golden cross with confirming volume'},
        ],
        recipient_rows=[
            {'id': 1, 'email': 'a@example.com', 'name': 'A'},
            {'id': 2, 'email': 'b@example.com', 'name': 'B'},
            {'id': 3, 'email': 'c@example.com', 'name': 'C'},
        ],
    )

    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        summary = send_daily_suggestions_email(db, recipient_ids=[1, 3])

    assert summary['recipient_count'] == 2
    sent_to = {call.kwargs['to_email'] for call in mock_send.call_args_list}
    assert sent_to == {'a@example.com', 'c@example.com'}


def test_recipient_ids_none_still_means_every_active_viewer():
    db = FakeEmailDB(
        suggestion_rows=[],
        recipient_rows=[
            {'id': 1, 'email': 'a@example.com', 'name': 'A'},
            {'id': 2, 'email': 'b@example.com', 'name': 'B'},
        ],
    )

    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        summary = send_daily_suggestions_email(db, recipient_ids=None)

    assert summary['recipient_count'] == 2
    assert mock_send.call_count == 2


def test_recipient_ids_empty_list_sends_to_nobody():
    # Distinguishes "no filter" (None) from "filtered down to nothing"
    # ([]) -- an empty selection must never silently fall back to everyone.
    db = FakeEmailDB(
        suggestion_rows=[],
        recipient_rows=[{'id': 1, 'email': 'a@example.com', 'name': 'A'}],
    )

    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        summary = send_daily_suggestions_email(db, recipient_ids=[])

    assert summary['recipient_count'] == 0
    assert summary['sent'] == 0
    mock_send.assert_not_called()


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

    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
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

    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email',
               side_effect=[(False, 'Zeptomail HTTP 500: server error'), (True, 'ok')]):
        summary = send_daily_suggestions_email(db)

    assert summary['sent'] == 1
    assert summary['failed'] == 1
    assert summary['failures'] == [{'email': 'good@example.com', 'error': 'Zeptomail HTTP 500: server error'}]


def test_viewer_welcome_email_includes_login_link_username_and_password():
    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
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
    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email',
               return_value=(False, 'Missing: STOCKS_ZEPTOMAIL_API_KEY.')):
        sent, detail = send_viewer_welcome_email('new@example.com', 'New Viewer', 'pass123')

    assert sent is False
    assert detail == 'Missing: STOCKS_ZEPTOMAIL_API_KEY.'


def test_viewer_welcome_email_falls_back_to_email_as_greeting_when_no_name():
    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        send_viewer_welcome_email('noname@example.com', '', 'somepass')

    kwargs = mock_send.call_args.kwargs
    assert kwargs['to_name'] == 'noname@example.com'


def test_activation_reminder_email_includes_login_link_username_and_fresh_password():
    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        sent, detail = send_activation_reminder_email('pending@example.com', 'Pending Viewer', 'fr35hPass123')

    assert sent is True
    assert detail == 'ok'
    kwargs = mock_send.call_args.kwargs
    assert kwargs['to_email'] == 'pending@example.com'
    assert kwargs['to_name'] == 'Pending Viewer'
    assert 'pending@example.com' in kwargs['textbody']
    assert 'fr35hPass123' in kwargs['textbody']
    assert 'fr35hPass123' in kwargs['htmlbody']
    assert '/stocks/login' in kwargs['textbody']
    assert 'trial' in kwargs['textbody'].lower()
    assert DISCLAIMER in kwargs['textbody']


def test_activation_reminder_email_falls_back_to_email_as_greeting_when_no_name():
    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        send_activation_reminder_email('noname@example.com', '', 'somepass')

    kwargs = mock_send.call_args.kwargs
    assert kwargs['to_name'] == 'noname@example.com'


def test_subscription_welcome_email_without_suggestions_is_the_plain_welcome():
    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        send_subscription_welcome_email('new@example.com', 'New Sub', '17 Sep 2026')

    kwargs = mock_send.call_args.kwargs
    assert 'payment confirmed' in kwargs['subject'].lower()
    assert "today's recommendation" not in kwargs['subject'].lower()
    assert 'no pick to show yet' in kwargs['textbody'].lower()
    assert '17 Sep 2026' in kwargs['textbody']


def test_subscription_welcome_email_with_suggestions_includes_todays_pick():
    suggestions = [
        {'symbol': 'GLD', 'exchange': 'NSE', 'company_name': 'Golden Co Ltd', 'buy_price': 100.0,
         'target_sell_price': 105.0, 'stop_loss_price': 97.0, 'holding_period_days': 10,
         'rationale': 'Golden cross with confirming volume'},
    ]
    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        send_subscription_welcome_email('new@example.com', 'New Sub', '17 Sep 2026', suggestions=suggestions)

    kwargs = mock_send.call_args.kwargs
    assert "today's recommendation" in kwargs['subject'].lower()
    assert 'Golden Co Ltd' in kwargs['textbody']
    assert 'Golden Co Ltd' in kwargs['htmlbody']
    assert kwargs['htmlbody'].strip().startswith('<!doctype html>')
    assert DISCLAIMER in kwargs['textbody']
    assert 'Rs 352.82/month' in kwargs['textbody']
    assert 'Rs 116.82/month' not in kwargs['textbody']


def test_subscription_welcome_email_defaults_to_standard_plan_pricing():
    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        send_subscription_welcome_email('new@example.com', 'New Sub', '17 Sep 2026')

    kwargs = mock_send.call_args.kwargs
    assert 'Rs 352.82/month' in kwargs['textbody']
    assert 'Each day we pick' in kwargs['textbody']


def test_subscription_welcome_email_starters_plan_shows_the_correct_price_and_cadence():
    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        send_subscription_welcome_email('new@example.com', 'New Sub', '17 Sep 2026', plan='starters')

    kwargs = mock_send.call_args.kwargs
    assert 'Rs 116.82/month' in kwargs['textbody']
    assert 'Rs 352.82/month' not in kwargs['textbody']
    assert 'Starters' in kwargs['textbody']
    assert 'no pick to show yet' in kwargs['textbody'].lower()


def test_subscription_welcome_email_starters_plan_with_suggestions_uses_weekly_heading():
    suggestions = [
        {'symbol': 'GLD', 'exchange': 'NSE', 'company_name': 'Golden Co Ltd', 'buy_price': 100.0,
         'target_sell_price': 105.0, 'stop_loss_price': 97.0, 'holding_period_days': 10,
         'rationale': 'Golden cross with confirming volume'},
    ]
    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        send_subscription_welcome_email(
            'new@example.com', 'New Sub', '17 Sep 2026', suggestions=suggestions, plan='starters',
        )

    kwargs = mock_send.call_args.kwargs
    assert "this week's pick" in kwargs['subject'].lower()
    assert "today's recommendation" not in kwargs['subject'].lower()
    assert 'Golden Co Ltd' in kwargs['textbody']
    assert 'Rs 116.82/month' in kwargs['textbody']


def test_admin_new_subscriber_email_goes_to_the_fixed_admin_address():
    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        send_admin_new_subscriber_email('newsub@example.com', 'New Sub')

    kwargs = mock_send.call_args.kwargs
    assert kwargs['to_email'] == 'narinakhre@gmail.com'
    assert 'New Sub' in kwargs['subject']
    assert 'newsub@example.com' in kwargs['textbody']
    assert 'completed payment' in kwargs['textbody']


def test_admin_cancellation_email_goes_to_the_fixed_admin_address():
    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        send_admin_subscription_cancelled_email('leaving@example.com', 'Leaving User')

    kwargs = mock_send.call_args.kwargs
    assert kwargs['to_email'] == 'narinakhre@gmail.com'
    assert 'Leaving User' in kwargs['subject']
    assert 'cancelled' in kwargs['textbody'].lower()


def test_rebrand_announcement_mentions_new_name_domain_sender_and_tagline():
    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        send_rebrand_announcement_email('viewer@example.com', 'A Viewer')

    kwargs = mock_send.call_args.kwargs
    assert kwargs['to_email'] == 'viewer@example.com'
    assert 'StoqBell' in kwargs['subject']
    for body in (kwargs['textbody'], kwargs['htmlbody']):
        assert 'Nari Nakhre Stocks' in body
        assert 'StoqBell' in body
        assert 'Trade the swings, own the future.' in body
        assert 'www.stoqbell.com' in body
        assert 'support-noreply@stoqbell.com' in body
    assert DISCLAIMER in kwargs['textbody']


def test_rebrand_announcement_batch_sends_to_every_active_viewer():
    db = FakeEmailDB(
        suggestion_rows=[],
        recipient_rows=[
            {'id': 1, 'email': 'a@example.com', 'name': 'A'},
            {'id': 2, 'email': 'b@example.com', 'name': 'B'},
        ],
    )

    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        summary = send_rebrand_announcement_to_all_viewers(db)

    assert mock_send.call_count == 2
    sent_to = {call.kwargs['to_email'] for call in mock_send.call_args_list}
    assert sent_to == {'a@example.com', 'b@example.com'}
    assert summary == {'recipient_count': 2, 'sent': 2, 'failed': 0, 'failures': []}


def test_rebrand_announcement_batch_can_target_specific_recipients():
    db = FakeEmailDB(
        suggestion_rows=[],
        recipient_rows=[
            {'id': 1, 'email': 'a@example.com', 'name': 'A'},
            {'id': 2, 'email': 'b@example.com', 'name': 'B'},
        ],
    )

    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        summary = send_rebrand_announcement_to_all_viewers(db, recipient_ids=[2])

    assert mock_send.call_count == 1
    assert mock_send.call_args.kwargs['to_email'] == 'b@example.com'
    assert summary['recipient_count'] == 1


def test_rebrand_announcement_batch_reports_per_recipient_failures():
    db = FakeEmailDB(
        suggestion_rows=[],
        recipient_rows=[{'id': 1, 'email': 'a@example.com', 'name': 'A'}],
    )

    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email',
               return_value=(False, 'Zeptomail HTTP 401: unauthorized')):
        summary = send_rebrand_announcement_to_all_viewers(db)

    assert summary['sent'] == 0
    assert summary['failed'] == 1
    assert summary['failures'] == [{'email': 'a@example.com', 'error': 'Zeptomail HTTP 401: unauthorized'}]


def test_trading_alert_email_includes_the_stock_card_and_buy_link():
    suggestion = {
        'symbol': 'GLD', 'exchange': 'NSE', 'company_name': 'Golden Co Ltd', 'buy_price': 100.0,
        'target_sell_price': 105.0, 'stop_loss_price': 97.0, 'holding_period_days': 10,
        'nns_tier': 'golden', 'rationale': 'Golden cross with confirming volume',
    }
    buy_link = 'https://narinakhre.com/stocks/suggestions/42/buy'
    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        send_trading_alert_email('raga2020@gmail.com', suggestion, buy_link)

    kwargs = mock_send.call_args.kwargs
    assert kwargs['to_email'] == 'raga2020@gmail.com'
    assert 'Golden Co Ltd' in kwargs['subject']
    assert 'Golden Co Ltd' in kwargs['textbody']
    assert buy_link in kwargs['textbody']
    assert buy_link in kwargs['htmlbody']
    assert kwargs['htmlbody'].strip().startswith('<!doctype html>')
    assert DISCLAIMER in kwargs['textbody']


def test_target_hit_email_reports_the_pnl_and_links_to_the_dashboard():
    trade = {'symbol': 'GLD', 'exchange': 'NSE', 'buy_price': 100.0, 'quantity': 60, 'exit_price': 105.0}
    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        send_target_hit_email('raga2020@gmail.com', trade, pnl_amount=300.0, pnl_pct=5.0)

    kwargs = mock_send.call_args.kwargs
    assert kwargs['to_email'] == 'raga2020@gmail.com'
    assert 'GLD' in kwargs['subject']
    assert 'target hit' in kwargs['subject'].lower()
    assert 'sold automatically' in kwargs['textbody']
    assert '+Rs 300.00' in kwargs['textbody']
    assert '/stocks/auto-trader' in kwargs['textbody']


def test_target_achieved_email_single_achievement():
    achievement = {
        'symbol': 'GLD', 'exchange': 'NSE', 'company_name': 'Golden Co Ltd',
        'suggestion_date': '2026-08-01', 'buy_price': 100.0, 'target_sell_price': 110.0,
        'latest_price': 112.0, 'latest_price_date': '2026-08-11',
    }
    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        send_target_achieved_email('sub@example.com', 'A Subscriber', [achievement])

    kwargs = mock_send.call_args.kwargs
    assert kwargs['to_email'] == 'sub@example.com'
    assert 'Golden Co Ltd' in kwargs['subject']
    assert 'hit its target' in kwargs['subject'].lower()
    assert 'Golden Co Ltd' in kwargs['textbody']
    assert 'Rs 100.0' in kwargs['textbody']  # recommended price
    assert 'Rs 110.0' in kwargs['textbody']  # target price
    assert 'Rs 112.0' in kwargs['textbody']  # achieved/latest price
    assert '10 days' in kwargs['textbody']   # Aug 1 -> Aug 11
    assert '+12.0%' in kwargs['textbody']    # (112-100)/100
    assert 'booking profit' in kwargs['textbody'].lower()
    assert kwargs['htmlbody'].strip().startswith('<!doctype html>')
    assert 'Golden Co Ltd' in kwargs['htmlbody']
    assert DISCLAIMER in kwargs['textbody']


def test_target_achieved_email_bundles_multiple_achievements_in_one_email():
    achievements = [
        {'symbol': 'GLD', 'exchange': 'NSE', 'company_name': 'Golden Co Ltd',
         'suggestion_date': '2026-08-01', 'buy_price': 100.0, 'target_sell_price': 110.0,
         'latest_price': 111.0, 'latest_price_date': '2026-08-08'},
        {'symbol': 'SLV', 'exchange': 'NSE', 'company_name': 'Silver Star Ltd',
         'suggestion_date': '2026-07-20', 'buy_price': 50.0, 'target_sell_price': 55.0,
         'latest_price': 56.0, 'latest_price_date': '2026-08-08'},
    ]
    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        send_target_achieved_email('sub@example.com', 'A Subscriber', achievements)

    kwargs = mock_send.call_args.kwargs
    assert '2 of our picks' in kwargs['subject']
    assert 'Golden Co Ltd' in kwargs['textbody']
    assert 'Silver Star Ltd' in kwargs['textbody']
    assert 'Golden Co Ltd' in kwargs['htmlbody']
    assert 'Silver Star Ltd' in kwargs['htmlbody']


def test_target_achieved_email_falls_back_to_symbol_when_no_company_name():
    achievement = {
        'symbol': 'GLD', 'exchange': 'NSE', 'company_name': None,
        'suggestion_date': '2026-08-01', 'buy_price': 100.0, 'target_sell_price': 110.0,
        'latest_price': 112.0, 'latest_price_date': '2026-08-11',
    }
    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        send_target_achieved_email('sub@example.com', None, [achievement])

    kwargs = mock_send.call_args.kwargs
    assert 'GLD' in kwargs['subject']
    assert kwargs['to_name'] == 'sub@example.com'  # falls back to email when no name given


def test_target_achieved_email_omits_resubscribe_nudge_for_current_subscriber():
    achievement = {
        'symbol': 'GLD', 'exchange': 'NSE', 'company_name': 'Golden Co Ltd',
        'suggestion_date': '2026-08-01', 'buy_price': 100.0, 'target_sell_price': 110.0,
        'latest_price': 112.0, 'latest_price_date': '2026-08-11',
    }
    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        send_target_achieved_email('sub@example.com', 'A Subscriber', [achievement], currently_subscribed=True)

    kwargs = mock_send.call_args.kwargs
    assert 'jump back in' not in kwargs['textbody'].lower()
    assert 'jump back in' not in kwargs['htmlbody'].lower()


def test_target_achieved_email_adds_resubscribe_nudge_for_lapsed_recipient():
    # A trial that expired without subscribing (or a lapsed paid account)
    # still gets this email -- see app.py's stocks_suggestions_notify_target_hits,
    # which threads has_stocks_access through as currently_subscribed.
    achievement = {
        'symbol': 'GLD', 'exchange': 'NSE', 'company_name': 'Golden Co Ltd',
        'suggestion_date': '2026-08-01', 'buy_price': 100.0, 'target_sell_price': 110.0,
        'latest_price': 112.0, 'latest_price_date': '2026-08-11',
    }
    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        send_target_achieved_email('lapsed@example.com', 'A Lapsed User', [achievement], currently_subscribed=False)

    kwargs = mock_send.call_args.kwargs
    assert 'jump back in' in kwargs['textbody'].lower()
    assert 'jump back in' in kwargs['htmlbody'].lower()
    assert STOCKS_LOGIN_URL in kwargs['textbody']


def test_daily_email_includes_each_recipients_own_referral_footer():
    db = FakeEmailDB(
        suggestion_rows=[
            {'symbol': 'GLD', 'exchange': 'NSE', 'buy_price': 100.0, 'target_sell_price': 105.0,
             'stop_loss_price': 97.0, 'holding_period_days': 10, 'rationale': 'Golden cross with confirming volume'},
        ],
        recipient_rows=[
            {'id': 1, 'email': 'a@example.com', 'name': 'A', 'referral_code': 'AAACODE1'},
            {'id': 2, 'email': 'b@example.com', 'name': 'B', 'referral_code': 'BBBCODE2'},
        ],
    )

    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        send_daily_suggestions_email(db)

    calls_by_email = {c.kwargs['to_email']: c.kwargs for c in mock_send.call_args_list}
    assert 'ref=AAACODE1' in calls_by_email['a@example.com']['textbody']
    assert 'ref=BBBCODE2' in calls_by_email['b@example.com']['textbody']
    # Each recipient's own code, not the other's.
    assert 'BBBCODE2' not in calls_by_email['a@example.com']['textbody']
    assert 'AAACODE1' not in calls_by_email['b@example.com']['textbody']
    assert 'ref=AAACODE1' in calls_by_email['a@example.com']['htmlbody']
    assert 'month free' in calls_by_email['a@example.com']['textbody'].lower()


def test_daily_email_generates_a_referral_code_for_a_recipient_who_has_none_yet():
    db = FakeEmailDB(
        suggestion_rows=[
            {'symbol': 'GLD', 'exchange': 'NSE', 'buy_price': 100.0, 'target_sell_price': 105.0,
             'stop_loss_price': 97.0, 'holding_period_days': 10, 'rationale': 'Golden cross with confirming volume'},
        ],
        recipient_rows=[{'id': 1, 'email': 'a@example.com', 'name': 'A'}],  # no referral_code yet
    )

    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        send_daily_suggestions_email(db)

    assert db.recipient_rows[0]['referral_code']  # lazily generated and persisted
    assert f"ref={db.recipient_rows[0]['referral_code']}" in mock_send.call_args.kwargs['textbody']


class FakeStartersEmailDB:
    """Same shape as FakeEmailDB above, but for send_weekly_starters_email --
    a separate class (not a shared one) because the two match different SQL
    text entirely: get_starters_suggestions' own top-TOP_N_STARTERS-per-week
    query, and a recipient query that must literally filter on
    stocks_plan='starters' (asserting on the exact SQL text is what proves
    the production query actually scopes to Starters accounts, not just
    every active viewer)."""

    def __init__(self, suggestion_rows, recipient_rows):
        self.suggestion_rows = suggestion_rows
        self.recipient_rows = recipient_rows
        self.last_recipient_sql = None

    def execute(self, sql, params=None):
        normalized = ' '.join(sql.split())

        if normalized.startswith('SELECT suggestion_id, watchlist_id, symbol, exchange, company_name, universe_id,'):
            return FakeCursor(self.suggestion_rows)

        if "stocks_plan='starters'" in normalized and normalized.startswith("SELECT id, username AS email, name FROM stocks_admin_users WHERE role='viewer'"):
            self.last_recipient_sql = normalized
            if 'AND id IN (' in normalized:
                wanted = set(params)
                return FakeCursor([r for r in self.recipient_rows if r.get('id') in wanted])
            return FakeCursor(self.recipient_rows)

        if normalized.startswith('SELECT referral_code FROM stocks_admin_users WHERE id=?'):
            admin_id, = params
            matches = [r for r in self.recipient_rows if r.get('id') == admin_id and r.get('referral_code')]
            return FakeCursor(matches[:1])
        if normalized.startswith('SELECT id FROM stocks_admin_users WHERE referral_code=?'):
            code, = params
            matches = [r for r in self.recipient_rows if r.get('referral_code') == code]
            return FakeCursor(matches[:1])
        if normalized.startswith('UPDATE stocks_admin_users SET referral_code=?'):
            code, admin_id = params
            for r in self.recipient_rows:
                if r.get('id') == admin_id:
                    r['referral_code'] = code
            return FakeCursor([])

        if normalized.startswith('INSERT INTO stock_email_deliveries'):
            return FakeCursor([])

        raise AssertionError(f'Unexpected SQL in test: {sql}')

    def commit(self):
        pass


def test_weekly_starters_email_sends_nothing_when_no_pick_cleared_the_bar():
    db = FakeStartersEmailDB(suggestion_rows=[], recipient_rows=[{'id': 1, 'email': 'a@example.com', 'name': 'A'}])

    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        summary = send_weekly_starters_email(db)

    mock_send.assert_not_called()
    assert summary == {'suggestion_count': 0, 'recipient_count': 0, 'sent': 0, 'failed': 0, 'failures': []}


def test_weekly_starters_email_sends_only_to_starters_recipients_with_the_weekly_pick():
    db = FakeStartersEmailDB(
        suggestion_rows=[
            {'symbol': 'GLD', 'exchange': 'NSE', 'company_name': 'Golden Co Ltd', 'buy_price': 100.0,
             'target_sell_price': 105.0, 'stop_loss_price': 97.0, 'holding_period_days': 10,
             'nns_tier': 'golden', 'rationale': 'Golden cross with confirming volume'},
        ],
        recipient_rows=[{'id': 1, 'email': 'starter@example.com', 'name': 'Starter'}],
    )

    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        summary = send_weekly_starters_email(db)

    assert summary['sent'] == 1
    kwargs = mock_send.call_args.kwargs
    assert kwargs['to_email'] == 'starter@example.com'
    assert 'Golden Co Ltd' in kwargs['subject']
    assert "This Week's Pick" in kwargs['subject']
    assert 'Golden Co Ltd' in kwargs['textbody']
    assert kwargs['htmlbody'].strip().startswith('<!doctype html>')
    assert DISCLAIMER in kwargs['textbody']


def test_starters_email_excludes_accounts_with_a_pending_first_password_change():
    db = FakeStartersEmailDB(
        suggestion_rows=[{'symbol': 'GLD', 'exchange': 'NSE', 'buy_price': 100.0, 'target_sell_price': 105.0, 'stop_loss_price': 97.0, 'holding_period_days': 10, 'nns_tier': 'golden', 'rationale': 'Golden cross with confirming volume'}],
        recipient_rows=[{'id': 1, 'email': 'starter@example.com', 'name': 'Starter'}],
    )
    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')):
        send_weekly_starters_email(db)
    assert 'trial_pending_password_change' in db.last_recipient_sql


def test_weekly_starters_email_includes_both_picks_when_two_cleared_the_bar():
    db = FakeStartersEmailDB(
        suggestion_rows=[
            {'symbol': 'GLD', 'exchange': 'NSE', 'company_name': 'Golden Co Ltd', 'buy_price': 100.0,
             'target_sell_price': 105.0, 'stop_loss_price': 97.0, 'holding_period_days': 10,
             'nns_tier': 'golden', 'rationale': 'Golden cross with confirming volume'},
            {'symbol': 'SLV', 'exchange': 'NSE', 'company_name': 'Silver Star Ltd', 'buy_price': 50.0,
             'target_sell_price': 55.0, 'stop_loss_price': 48.0, 'holding_period_days': 10,
             'nns_tier': 'golden', 'rationale': 'Golden cross with strong fundamentals'},
        ],
        recipient_rows=[{'id': 1, 'email': 'starter@example.com', 'name': 'Starter'}],
    )

    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        summary = send_weekly_starters_email(db)

    assert summary['suggestion_count'] == 2
    kwargs = mock_send.call_args.kwargs
    assert '2 Picks' in kwargs['subject']
    assert 'Golden Co Ltd' in kwargs['textbody']
    assert 'Silver Star Ltd' in kwargs['textbody']


class FakeLargeCapBonusEmailDB:
    """Same shape as FakeStartersEmailDB, but for send_large_cap_bonus_email
    -- get_large_cap_bonus_suggestions' SELECT is textually IDENTICAL to
    get_suggestions' up through the same prefix (both mirror the same
    column list), so matching also requires 'stock_large_cap_bonus_suggestions'
    to appear in the SQL text -- proving this reads from the bonus table,
    not stock_suggestions. Likewise the recipient query must literally
    filter on stocks_plan='standard', not just 'every active viewer'."""

    def __init__(self, suggestion_rows, recipient_rows):
        self.suggestion_rows = suggestion_rows
        self.recipient_rows = recipient_rows
        self.last_recipient_sql = None

    def execute(self, sql, params=None):
        normalized = ' '.join(sql.split())

        if (normalized.startswith('SELECT DISTINCT ON (s.suggestion_date) s.id AS suggestion_id,')
                and 'stock_large_cap_bonus_suggestions' in normalized):
            return FakeCursor(self.suggestion_rows)

        if "stocks_plan='standard'" in normalized and normalized.startswith("SELECT id, username AS email, name FROM stocks_admin_users WHERE role='viewer'"):
            self.last_recipient_sql = normalized
            if 'AND id IN (' in normalized:
                wanted = set(params)
                return FakeCursor([r for r in self.recipient_rows if r.get('id') in wanted])
            return FakeCursor(self.recipient_rows)

        if normalized.startswith('SELECT referral_code FROM stocks_admin_users WHERE id=?'):
            admin_id, = params
            matches = [r for r in self.recipient_rows if r.get('id') == admin_id and r.get('referral_code')]
            return FakeCursor(matches[:1])
        if normalized.startswith('SELECT id FROM stocks_admin_users WHERE referral_code=?'):
            code, = params
            matches = [r for r in self.recipient_rows if r.get('referral_code') == code]
            return FakeCursor(matches[:1])
        if normalized.startswith('UPDATE stocks_admin_users SET referral_code=?'):
            code, admin_id = params
            for r in self.recipient_rows:
                if r.get('id') == admin_id:
                    r['referral_code'] = code
            return FakeCursor([])

        if normalized.startswith('INSERT INTO stock_email_deliveries'):
            return FakeCursor([])

        raise AssertionError(f'Unexpected SQL in test: {sql}')

    def commit(self):
        pass


def test_large_cap_bonus_email_sends_nothing_when_no_pick_cleared_the_bar():
    db = FakeLargeCapBonusEmailDB(suggestion_rows=[], recipient_rows=[{'id': 1, 'email': 'a@example.com', 'name': 'A'}])

    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        summary = send_large_cap_bonus_email(db)

    mock_send.assert_not_called()
    assert summary == {'suggestion_count': 0, 'recipient_count': 0, 'sent': 0, 'failed': 0, 'failures': []}


def test_large_cap_bonus_email_sends_only_to_standard_recipients():
    db = FakeLargeCapBonusEmailDB(
        suggestion_rows=[
            {'symbol': 'BIGC', 'exchange': 'NSE', 'company_name': 'Big Cap Ltd', 'buy_price': 200.0,
             'target_sell_price': 210.0, 'stop_loss_price': 194.0, 'holding_period_days': 10,
             'nns_tier': 'golden', 'rationale': 'Golden cross with confirming volume'},
        ],
        recipient_rows=[{'id': 1, 'email': 'standard@example.com', 'name': 'Standard Sub'}],
    )

    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')) as mock_send:
        summary = send_large_cap_bonus_email(db)

    assert summary['sent'] == 1
    kwargs = mock_send.call_args.kwargs
    assert kwargs['to_email'] == 'standard@example.com'
    assert 'Bonus Large-Cap Pick' in kwargs['subject']
    assert 'Big Cap Ltd' in kwargs['subject']
    assert 'Big Cap Ltd' in kwargs['textbody']
    assert 'Bonus Large-Cap Pick' in kwargs['textbody']
    assert kwargs['htmlbody'].strip().startswith('<!doctype html>')
    assert DISCLAIMER in kwargs['textbody']


def test_large_cap_bonus_email_excludes_accounts_with_a_pending_first_password_change():
    db = FakeLargeCapBonusEmailDB(
        suggestion_rows=[{'symbol': 'BIGC', 'exchange': 'NSE', 'buy_price': 200.0, 'target_sell_price': 210.0, 'stop_loss_price': 194.0, 'holding_period_days': 10, 'nns_tier': 'golden', 'rationale': 'Golden cross with confirming volume'}],
        recipient_rows=[{'id': 1, 'email': 'standard@example.com', 'name': 'Standard Sub'}],
    )
    with patch('stoqbell.utils.suggestion_email.send_zeptomail_stocks_email', return_value=(True, 'ok')):
        send_large_cap_bonus_email(db)
    assert 'trial_pending_password_change' in db.last_recipient_sql
