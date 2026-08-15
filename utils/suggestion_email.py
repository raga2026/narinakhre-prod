from datetime import date

from utils.stock_alerting import send_zeptomail_stocks_email

DISCLAIMER = (
    "This is personal market analysis shared informally among friends, not "
    "professional investment advice. Please do your own research before "
    "making any investment decision."
)

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
    return db.execute(
        'SELECT id, email, name, is_active, created_at FROM stocks_email_recipients ORDER BY created_at DESC'
    ).fetchall()


def add_recipient(db, email, name, added_by_id):
    """Admin-only, manual list -- no self-serve signup. Returns
    (created: bool, error_message_or_None), matching create_child_admin's
    pattern in utils/stock_auth.py: checks for an existing email first
    because SupabaseCursor swallows SQL errors (e.g. the UNIQUE constraint)
    instead of raising."""
    email = (email or '').strip()
    if not email:
        return False, 'Email is required.'

    existing = db.execute('SELECT id FROM stocks_email_recipients WHERE email=?', (email,)).fetchone()
    if existing:
        return False, 'That email is already on the list.'

    db.execute(
        'INSERT INTO stocks_email_recipients (email, name, added_by) VALUES (?, ?, ?)',
        (email, (name or '').strip() or None, added_by_id)
    )
    db.commit()
    return True, None


def toggle_recipient_active(db, recipient_id):
    row = db.execute('SELECT id, is_active FROM stocks_email_recipients WHERE id=?', (recipient_id,)).fetchone()
    if not row:
        return False
    new_status = 0 if row['is_active'] else 1
    db.execute('UPDATE stocks_email_recipients SET is_active=? WHERE id=?', (new_status, recipient_id))
    db.commit()
    return True


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
        text_lines.append(
            f"{s['symbol']} ({s['exchange']}): Buy {s['buy_price']}, "
            f"Target {s['target_sell_price']}, Stop-loss {s['stop_loss_price']}, "
            f"Hold {s['holding_period_days']} days. {s['rationale']}"
        )
        text_lines.append('')
        html_rows.append(
            f"<tr><td>{s['symbol']} ({s['exchange']})</td>"
            f"<td>{s['buy_price']}</td><td>{s['target_sell_price']}</td>"
            f"<td>{s['stop_loss_price']}</td><td>{s['holding_period_days']} days</td>"
            f"<td>{s['rationale']}</td></tr>"
        )
    text_lines.append(DISCLAIMER)
    text_body = '\n'.join(text_lines)

    html_body = (
        '<p>Today\'s suggestions:</p>'
        '<table border="1" cellpadding="6" cellspacing="0">'
        '<tr><th>Symbol</th><th>Buy</th><th>Target</th><th>Stop-loss</th><th>Hold</th><th>Rationale</th></tr>'
        + ''.join(html_rows) +
        '</table>'
        f'<p style="color:#64748b;font-size:0.85em;margin-top:16px;">{DISCLAIMER}</p>'
    )
    return subject, text_body, html_body


def send_daily_suggestions_email(db):
    """Fetches today's stock_suggestions rows (symbol/exchange joined from
    stock_watchlist) and emails every is_active stocks_email_recipients row
    -- one send per recipient. Always sends something, even with zero
    suggestions today (see _build_email_content). Every email includes the
    fixed disclaimer line, unmodified, per DISCLAIMER above."""
    today = date.today().isoformat()
    today_label = date.today().strftime('%d %b %Y')

    suggestions = db.execute(
        '''SELECT w.symbol, w.exchange, s.buy_price, s.target_sell_price,
                  s.stop_loss_price, s.holding_period_days, s.rationale
           FROM stock_suggestions s
           JOIN stock_watchlist w ON w.id = s.watchlist_id
           WHERE s.suggestion_date = ?
           ORDER BY s.score DESC''',
        (today,)
    ).fetchall()

    subject, text_body, html_body = _build_email_content(suggestions, today_label)

    recipients = db.execute(
        'SELECT email, name FROM stocks_email_recipients WHERE is_active=1'
    ).fetchall()

    sent = 0
    failed = 0
    for r in recipients:
        ok = send_zeptomail_stocks_email(
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

    return {
        'suggestion_count': len(suggestions),
        'recipient_count': len(recipients),
        'sent': sent,
        'failed': failed,
    }
