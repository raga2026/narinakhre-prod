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
        f"You've been added as a viewer on Nari Nakhre Stocks. Each day (when the "
        f"screening criteria are met), you'll get up to a few stock suggestions by "
        f"email -- buy price, target sell price, stop-loss, and how long to hold. "
        f"You can also log in anytime to see today's suggestions and your full "
        f"history.\n\n"
        f'Login: {STOCKS_LOGIN_URL}\n'
        f'Username: {email}\n'
        f'Password: {password}\n\n'
        f'{DISCLAIMER}\n'
    )
    html_body = (
        f'<p>Hi {greeting},</p>'
        f"<p>You've been added as a viewer on Nari Nakhre Stocks. Each day (when the "
        f"screening criteria are met), you'll get up to a few stock suggestions by "
        f"email -- buy price, target sell price, stop-loss, and how long to hold. "
        f"You can also log in anytime to see today's suggestions and your full "
        f"history.</p>"
        f'<p><a href="{STOCKS_LOGIN_URL}">{STOCKS_LOGIN_URL}</a><br>'
        f'Username: {email}<br>'
        f'Password: {password}</p>'
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


def _tier_label(s):
    """'Golden' or 'Silver (PE x, OPM y%)' beside a suggestion, or None for
    suggestions that predate the fundamental_tier column. Silver always
    shows its PE/OPM values inline -- those are exactly the two metrics that
    let it in on the softer second-level filter (see
    fundamental_screen.classify_fundamental_tier), so the reader can see
    why, not just that."""
    tier = s.get('fundamental_tier')
    if tier == 'golden':
        return 'Golden'
    if tier == 'silver':
        pe = s.get('pe_at_suggestion')
        opm = s.get('opm_at_suggestion')
        pe_str = f'{pe:.2f}' if pe is not None else '—'
        opm_str = f'{opm:.0f}%' if opm is not None else '—'
        return f'Silver (PE {pe_str}, OPM {opm_str})'
    return None


def _build_email_content(suggestions, today_label):
    """Returns (subject, textbody, htmlbody). Always produces a real email,
    even with zero suggestions -- a silent "nothing sent" looks identical
    to the job having crashed, from a recipient's side."""
    if not suggestions:
        subject = f'Nari Nakhre Stocks — No suggestions today ({today_label})'
        text_body = (
            f'No stocks met the suggestion criteria today ({today_label}).\n\n'
            f'{DISCLAIMER}\n'
        )
        html_body = (
            f'<p>No stocks met the suggestion criteria today ({today_label}).</p>'
            f'<p style="color:#64748b;font-size:0.85em;">{DISCLAIMER}</p>'
        )
        return subject, text_body, html_body

    subject = f"Nari Nakhre Stocks — {len(suggestions)} suggestion{'s' if len(suggestions) != 1 else ''} for {today_label}"

    text_lines = [f'Today\'s suggestions ({today_label}):', '']
    html_rows = []
    for s in suggestions:
        tier_label = _tier_label(s)
        tier_suffix = f' — {tier_label}' if tier_label else ''
        text_lines.append(
            f"{s['symbol']} ({s['exchange']}){tier_suffix}: Buy {s['buy_price']}, "
            f"Target {s['target_sell_price']}, Stop-loss {s['stop_loss_price']}, "
            f"Hold {s['holding_period_days']} days. {s['rationale']}"
        )
        text_lines.append('')
        html_rows.append(
            f"<tr><td>{s['symbol']} ({s['exchange']})</td>"
            f"<td>{tier_label or '—'}</td>"
            f"<td>{s['buy_price']}</td><td>{s['target_sell_price']}</td>"
            f"<td>{s['stop_loss_price']}</td><td>{s['holding_period_days']} days</td>"
            f"<td>{s['rationale']}</td></tr>"
        )
    text_lines.append(DISCLAIMER)
    text_body = '\n'.join(text_lines)

    html_body = (
        '<p>Today\'s suggestions:</p>'
        '<table border="1" cellpadding="6" cellspacing="0">'
        '<tr><th>Symbol</th><th>Tier</th><th>Buy</th><th>Target</th><th>Stop-loss</th><th>Hold</th><th>Rationale</th></tr>'
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
