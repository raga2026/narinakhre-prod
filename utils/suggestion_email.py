from datetime import date

from utils.stock_alerting import send_zeptomail_stocks_email
from utils.suggestion_engine import get_suggestions

DISCLAIMER = (
    "This is personal market analysis shared informally among friends, not "
    "professional investment advice. Please do your own research before "
    "making any investment decision."
)

STOCKS_LOGIN_URL = 'https://narinakhre.com/stocks/login'


def send_viewer_welcome_email(email, name, password):
    """Sent immediately when a viewer account is created (see app.py's
    /stocks/users POST handler and the recipient-migration route) -- there's
    no separate password-reset/invite-link flow in this codebase, so this
    is the only way a new viewer ever finds out their login credentials.
    Includes the real password in plaintext (this is a small trusted
    personal circle, not a public product -- see the module this belongs
    to), the login link, and a short explanation of what they signed up
    for. Returns (success, detail) -- same shape as
    send_zeptomail_stocks_email, never raises -- detail is the actual
    reason on failure (missing Zeptomail config, Zeptomail's own HTTP
    error, or a network error), not just a generic "it failed"."""
    greeting = name or email
    subject = 'Nari Nakhre Stocks — your login'
    text_body = (
        f'Hi {greeting},\n\n'
        f"You've been added as a viewer on Nari Nakhre Stocks. On days a stock "
        f"clears the screening bar, you'll get a single Pick of the Day by email -- "
        f"NNS Score, buy price, target sell price, stop-loss, and timing. The same "
        f"stock isn't repeated for 30 days unless the recommendation genuinely "
        f"changes. You can also log in anytime to see today's pick and your full "
        f"history.\n\n"
        f'Login: {STOCKS_LOGIN_URL}\n'
        f'Username: {email}\n'
        f'Password: {password}\n\n'
        f"This is a temporary password -- you'll be asked to set your own the "
        f"first time you log in.\n\n"
        f'{DISCLAIMER}\n'
    )
    html_body = (
        f'<p>Hi {greeting},</p>'
        f"<p>You've been added as a viewer on Nari Nakhre Stocks. On days a stock "
        f"clears the screening bar, you'll get a single Pick of the Day by email -- "
        f"NNS Score, buy price, target sell price, stop-loss, and timing. The same "
        f"stock isn't repeated for 30 days unless the recommendation genuinely "
        f"changes. You can also log in anytime to see today's pick and your full "
        f"history.</p>"
        f'<p><a href="{STOCKS_LOGIN_URL}">{STOCKS_LOGIN_URL}</a><br>'
        f'Username: {email}<br>'
        f'Password: {password}</p>'
        f"<p>This is a temporary password -- you'll be asked to set your own the "
        f"first time you log in.</p>"
        f'<p style="color:#64748b;font-size:0.85em;margin-top:16px;">{DISCLAIMER}</p>'
    )
    return send_zeptomail_stocks_email(
        to_email=email, to_name=greeting, subject=subject,
        textbody=text_body, htmlbody=html_body, sender_name='Nari Nakhre Stocks',
    )

def send_stop_loss_review_email(to_email, trade, pnl_amount, pnl_pct):
    """Sent once per stop-loss trigger, to the fixed auto-trader alert
    address (see utils.auto_trader.STOP_LOSS_ALERT_EMAIL and app.py's
    /stocks/auto-trader/reconcile) -- unlike a target hit, which the
    auto-trader closes on its own, a stop-loss hit is deliberately never
    closed automatically; this email is the notification half of that
    manual review, the actual Proceed/Cancel decision happens on the
    /stocks/auto-trader dashboard (not via a link in this email -- a
    financial decision shouldn't be one unauthenticated click away).
    trade is a dict with at least symbol, exchange, buy_price, quantity,
    stop_loss_triggered_price."""
    auto_trader_url = 'https://narinakhre.com/stocks/auto-trader'
    subject = f'Nari Nakhre Stocks -- stop-loss hit on {trade["symbol"]}, review needed'
    body_text = (
        f'{trade["symbol"]} ({trade["exchange"]}) has hit its stop-loss in the dry-run auto-trader.\n\n'
        f'Bought {trade["quantity"]} shares at Rs {trade["buy_price"]}.\n'
        f'Stop-loss triggered at Rs {trade["stop_loss_triggered_price"]} '
        f'(would book {"+" if pnl_amount >= 0 else ""}Rs {pnl_amount:.2f}, {pnl_pct:+.2f}%).\n\n'
        f"This position has NOT been sold -- it's waiting on your decision. Log in and go to "
        f'{auto_trader_url} to either Proceed (book the loss at this price) or Cancel (keep holding; '
        f"you'll get this email again if it dips to the stop-loss level another time).\n\n"
        f'{DISCLAIMER}\n'
    )
    body_html = (
        f'<p><strong>{trade["symbol"]} ({trade["exchange"]})</strong> has hit its stop-loss in the dry-run auto-trader.</p>'
        f'<p>Bought {trade["quantity"]} shares at Rs {trade["buy_price"]}.<br>'
        f'Stop-loss triggered at Rs {trade["stop_loss_triggered_price"]} '
        f'(would book {"+" if pnl_amount >= 0 else ""}Rs {pnl_amount:.2f}, {pnl_pct:+.2f}%).</p>'
        f"<p>This position has <strong>NOT</strong> been sold -- it's waiting on your decision. "
        f'<a href="{auto_trader_url}">Log in and go to the Auto-Trader dashboard</a> to either Proceed '
        f"(book the loss at this price) or Cancel (keep holding; you'll get this email again if it dips "
        f'to the stop-loss level another time).</p>'
        f'<p style="color:#64748b;font-size:0.85em;margin-top:16px;">{DISCLAIMER}</p>'
    )
    return send_zeptomail_stocks_email(
        to_email=to_email, to_name=to_email, subject=subject,
        textbody=body_text, htmlbody=body_html, sender_name='Nari Nakhre Stocks Auto-Trader',
    )


def send_subscription_welcome_email(email, name, current_period_end_label):
    """Sent right after a self-serve Nari Nakhre Stocks signup's first
    Razorpay payment is verified (see app.py's /stocks/subscribe/verify) --
    unlike send_viewer_welcome_email above, there's no password to disclose
    here: a self-serve account either set its own password at signup, or
    has none at all (a Google-only signup, see utils/stock_auth.py's
    create_pending_google_subscriber). current_period_end_label is a
    pre-formatted date string (see app.py), not a raw datetime -- keeps
    this module free of date-formatting/timezone concerns."""
    greeting = name or email
    subject = 'Welcome to Nari Nakhre Stocks -- payment confirmed'
    text_body = (
        f'Hi {greeting},\n\n'
        f"Your Rs 299/month subscription is active. On days a stock clears our "
        f"screening bar, you'll get a single Pick of the Day by email -- NNS "
        f"Score, buy price, target sell price, stop-loss, and the reasoning "
        f"behind it. The same stock isn't repeated for 30 days unless the "
        f"recommendation genuinely changes.\n\n"
        f'Your subscription renews automatically on {current_period_end_label} -- '
        f"we'll email you a reminder a few days before, and you can cancel "
        f"anytime from your account.\n\n"
        f'Login: {STOCKS_LOGIN_URL}\n\n'
        f'{DISCLAIMER}\n'
    )
    html_body = (
        f'<p>Hi {greeting},</p>'
        f"<p>Your Rs 299/month subscription is active. On days a stock clears our "
        f"screening bar, you'll get a single Pick of the Day by email -- NNS "
        f"Score, buy price, target sell price, stop-loss, and the reasoning "
        f"behind it. The same stock isn't repeated for 30 days unless the "
        f"recommendation genuinely changes.</p>"
        f'<p>Your subscription renews automatically on <strong>{current_period_end_label}</strong> -- '
        f"we'll email you a reminder a few days before, and you can cancel "
        f"anytime from your account.</p>"
        f'<p><a href="{STOCKS_LOGIN_URL}">{STOCKS_LOGIN_URL}</a></p>'
        f'<p style="color:#64748b;font-size:0.85em;margin-top:16px;">{DISCLAIMER}</p>'
    )
    return send_zeptomail_stocks_email(
        to_email=email, to_name=greeting, subject=subject,
        textbody=text_body, htmlbody=html_body, sender_name='Nari Nakhre Stocks',
    )


def send_subscription_expiry_reminder_email(email, name, current_period_end_label, is_renewing):
    """Sent a few days before subscription_current_period_end (see app.py's
    /stocks/subscriptions/send-expiry-reminders and
    utils/stocks_subscription.find_expiring_subscribers). is_renewing tells
    an upcoming auto-charge (subscription_status='active') apart from
    access that's actually ending because it was already cancelled
    (subscription_status='cancelled') -- same trigger, opposite news, so
    the copy has to say something different in each case rather than one
    generic 'your subscription is expiring' line that would be misleading
    for whichever case it doesn't match."""
    greeting = name or email
    if is_renewing:
        subject = f'Nari Nakhre Stocks -- renews on {current_period_end_label}'
        headline = (
            f"Your Nari Nakhre Stocks subscription will auto-renew on "
            f"{current_period_end_label} for Rs 299. No action needed if you'd "
            f"like to continue -- this is just a heads-up before the charge."
        )
    else:
        subject = f'Nari Nakhre Stocks -- access ends {current_period_end_label}'
        headline = (
            f"Your Nari Nakhre Stocks access ends on {current_period_end_label} "
            f"(your subscription was cancelled and won't auto-renew). "
            f"Resubscribe anytime before then to keep it uninterrupted."
        )
    text_body = f'Hi {greeting},\n\n{headline}\n\nLogin: {STOCKS_LOGIN_URL}\n\n{DISCLAIMER}\n'
    html_body = (
        f'<p>Hi {greeting},</p><p>{headline}</p>'
        f'<p><a href="{STOCKS_LOGIN_URL}">{STOCKS_LOGIN_URL}</a></p>'
        f'<p style="color:#64748b;font-size:0.85em;margin-top:16px;">{DISCLAIMER}</p>'
    )
    return send_zeptomail_stocks_email(
        to_email=email, to_name=greeting, subject=subject,
        textbody=text_body, htmlbody=html_body, sender_name='Nari Nakhre Stocks',
    )


# DEPRECATED as of the viewer-role migration -- recipients now live as
# role='viewer' rows in stocks_admin_users (utils/stock_auth.py), which
# supports real login, not just an email address. This table and the three
# functions below are kept, unused by send_daily_suggestions_email, purely
# so the pre-migration data isn't lost and a rollback stays possible. Don't
# build new features against this table -- use stocks_admin_users instead.
STOCKS_EMAIL_RECIPIENTS_TABLE_SQL = [
    '''CREATE TABLE IF NOT EXISTS stocks_email_recipients (
        id BIGSERIAL PRIMARY KEY,
        email TEXT NOT NULL UNIQUE,
        name TEXT,
        is_active INTEGER DEFAULT 1,
        added_by BIGINT REFERENCES stocks_admin_users(id),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )'''
]


def initialize_stocks_email_recipients_table_if_needed(client):
    for sql in STOCKS_EMAIL_RECIPIENTS_TABLE_SQL:
        try:
            client.rpc('execute_sql', {'query': sql}).execute()
        except Exception as e:
            print(f'Stocks email recipients table init warning (may already exist): {e}')


def list_recipients(db):
    """DEPRECATED -- see the module docstring above. Kept only so the
    pre-migration data is still readable if needed for a rollback."""
    return db.execute(
        'SELECT id, email, name, is_active, created_at FROM stocks_email_recipients ORDER BY created_at DESC'
    ).fetchall()


def _nns_badge(s):
    """'Golden 8.3' / 'Silver 6.4' / 'Bronze 4.2' -- the NNS Score (see
    utils/nns_score.py) and its tier, the PRIMARY ranking shown for each
    suggestion. '—' for suggestions that predate the NNS Score column."""
    tier = s.get('nns_tier')
    score = s.get('nns_score')
    if not tier or score is None:
        return '—'
    return f'{tier.capitalize()} {score}'


def _fundamentals_note(s):
    """Supplementary '(PE x, OPM y%)' context for a silver-tier watchlist
    stock -- those are exactly the two metrics that let it onto the
    watchlist via the softer second-level filter (see
    fundamental_screen.classify_fundamental_tier), separate from (and a
    different scale than) the NNS Score/tier above. None for golden-tier
    or suggestions that predate the fundamental_tier column."""
    if s.get('fundamental_tier') != 'silver':
        return None
    pe = s.get('pe_at_suggestion')
    opm = s.get('opm_at_suggestion')
    pe_str = f'{pe:.2f}' if pe is not None else '—'
    opm_str = f'{opm:.0f}%' if opm is not None else '—'
    return f'watchlisted on the silver criteria: PE {pe_str}, OPM {opm_str}'


def _build_email_content(suggestions, today_label):
    """Returns (subject, textbody, htmlbody). Always produces a real email,
    even with zero suggestions -- a silent "nothing sent" looks identical
    to the job having crashed, from a recipient's side.

    "Pick of the Day" -- suggestion_engine.generate_daily_suggestions()
    sends at most TOP_N_SUGGESTIONS (1) per day, and never repeats the same
    stock within SUGGESTION_REPEAT_WINDOW_DAYS (30) unless the
    recommendation has genuinely changed since (see _is_genuine_change) --
    so most days this is exactly one pick, occasionally zero (every
    eligible candidate is on cooldown with nothing new to say, or none
    clear the NNS Score floor at all). Still iterates over `suggestions`
    as a list rather than hard-assuming exactly one, so this doesn't break
    if that policy ever changes."""
    if not suggestions:
        subject = f'Nari Nakhre Stocks — No Pick of the Day ({today_label})'
        text_body = (
            f'No stock met the Pick of the Day criteria today ({today_label}).\n\n'
            f'{DISCLAIMER}\n'
        )
        html_body = (
            f'<p>No stock met the Pick of the Day criteria today ({today_label}).</p>'
            f'<p style="color:#64748b;font-size:0.85em;">{DISCLAIMER}</p>'
        )
        return subject, text_body, html_body

    if len(suggestions) == 1:
        subject = f"Nari Nakhre Stocks — Pick of the Day: {suggestions[0]['symbol']} ({today_label})"
    else:
        subject = f"Nari Nakhre Stocks — {len(suggestions)} Picks of the Day for {today_label}"

    # Already NNS Score-ranked highest first -- see
    # suggestion_engine.get_suggestions' ORDER BY s.score DESC.
    heading = "Today's Pick of the Day" if len(suggestions) == 1 else "Today's Picks of the Day"
    text_lines = [f'{heading} ({today_label}):', '']
    html_rows = []
    for s in suggestions:
        badge = _nns_badge(s)
        fundamentals_note = _fundamentals_note(s)
        fundamentals_suffix = f' ({fundamentals_note})' if fundamentals_note else ''
        # A pattern-based suggestion (see suggestion_engine.generate_daily_suggestions
        # / utils.price_pattern.compute_suggestion_pricing) shows the cited
        # pattern_note INSTEAD OF a "hold N days" figure -- chart-pattern
        # shape doesn't reliably predict timing the way a specific
        # day-count would misleadingly imply. holding_period_days is only
        # ever a fixed internal default in that case, not shown here.
        timing_text = s['pattern_note'] if s.get('pattern_name') else f"Hold {s['holding_period_days']} days."
        text_lines.append(
            f"{s['symbol']} ({s['exchange']}) — NNS Score {badge}{fundamentals_suffix}: Buy {s['buy_price']}, "
            f"Target {s['target_sell_price']}, Stop-loss {s['stop_loss_price']}. "
            f"{timing_text} {s['rationale']}"
        )
        text_lines.append('')
        html_rows.append(
            f"<tr><td>{s['symbol']} ({s['exchange']})</td>"
            f"<td>{badge}</td>"
            f"<td>{s['buy_price']}</td><td>{s['target_sell_price']}</td>"
            f"<td>{s['stop_loss_price']}</td><td>{timing_text}</td>"
            f"<td>{s['rationale']}{' — ' + fundamentals_note if fundamentals_note else ''}</td></tr>"
        )
    text_lines.append(DISCLAIMER)
    text_body = '\n'.join(text_lines)

    html_body = (
        f'<p>{heading}:</p>'
        '<table border="1" cellpadding="6" cellspacing="0">'
        '<tr><th>Symbol</th><th>NNS Score</th><th>Buy</th><th>Target</th><th>Stop-loss</th><th>Timing</th><th>Rationale</th></tr>'
        + ''.join(html_rows) +
        '</table>'
        f'<p style="color:#64748b;font-size:0.85em;margin-top:16px;">{DISCLAIMER}</p>'
    )
    return subject, text_body, html_body


def send_daily_suggestions_email(db):
    """Fetches today's stock_suggestions rows via the shared
    suggestion_engine.get_suggestions() query and emails every active
    role='viewer' account in stocks_admin_users -- one send per recipient.
    Recipients now come from stocks_admin_users, NOT the deprecated
    stocks_email_recipients table (see that table's docstring above) --
    a viewer account is a real login, not just an address on a list.
    stocks_admin_users has no separate email column, so the login
    "username" doubles as the recipient address (see create_viewer_account
    in utils/stock_auth.py). Always sends something, even with zero
    suggestions today (see _build_email_content). Every email includes the
    fixed disclaimer line, unmodified, per DISCLAIMER above."""
    today = date.today().isoformat()
    today_label = date.today().strftime('%d %b %Y')

    suggestions = get_suggestions(db, start_date=today, end_date=today)

    subject, text_body, html_body = _build_email_content(suggestions, today_label)

    recipients = db.execute(
        "SELECT username AS email, name FROM stocks_admin_users WHERE role='viewer' AND is_active=1"
    ).fetchall()

    sent = 0
    failed = 0
    failures = []
    for r in recipients:
        ok, detail = send_zeptomail_stocks_email(
            to_email=r['email'],
            to_name=r.get('name') or r['email'],
            subject=subject,
            textbody=text_body,
            htmlbody=html_body,
            sender_name='Nari Nakhre Stocks',
        )
        if ok:
            sent += 1
        else:
            failed += 1
            failures.append({'email': r['email'], 'error': detail})

    return {
        'suggestion_count': len(suggestions),
        'recipient_count': len(recipients),
        'sent': sent,
        'failed': failed,
        'failures': failures,
    }
