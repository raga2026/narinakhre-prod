from datetime import date

from utils.stock_alerting import send_zeptomail_stocks_email
from utils.suggestion_chart import build_prediction_chart_data_uri
from utils.suggestion_engine import get_suggestions
from utils.price_pattern import compute_projection_targets

DISCLAIMER = (
    "This is personal market analysis shared informally among friends, not "
    "professional investment advice. Please do your own research before "
    "making any investment decision."
)

# Shown once per email whenever any suggestion has a projection attached
# (see utils.price_pattern.compute_projection_targets) -- each stock's own
# mid-period/long-term figures come from ITS OWN detected chart pattern
# when one was confirmed (a genuinely different duration per pattern type),
# or a generic extrapolation otherwise; this note explains that once rather
# than repeating on every stock card.
HORIZON_DISCLAIMER = (
    "The mid-period and long-term figures on each stock are its OWN projection, "
    "not a fixed calendar point shared by every stock: when a chart pattern "
    "(head-and-shoulders, reverse head-and-shoulders, or rounding) was confirmed "
    "for that stock, both figures come from that pattern's own published typical "
    "time-to-target (see the note under each chart); otherwise they fall back to "
    "a generic mathematical extrapolation (~6 months / ~1 year), marked "
    "'extrapolated' and more speculative than a pattern-grounded projection."
)

STOCKS_LOGIN_URL = 'https://narinakhre.com/stocks/login'

_TREND_BADGE_COLORS = {
    'Highly Recommended': ('#dcfce7', '#15803d'),
    'Recommended -- extra caution': ('#ffedd5', '#9a3412'),
    'Recommended': ('#e2e8f0', '#334155'),
}

_PATTERN_DISPLAY_NAMES = {
    'head_and_shoulders_bottom': 'reverse head-and-shoulders pattern',
    'rounding_bottom': 'rounding-bottom pattern',
}


def _projection_method_note(projection):
    """'based on the detected reverse head-and-shoulders pattern' or
    'extrapolated -- no confirmed chart pattern', shown right under a
    suggestion's mid-period/long-term figures so it's always clear which
    of the two this particular stock's projection actually is."""
    if projection.get('method') == 'pattern':
        name = _PATTERN_DISPLAY_NAMES.get(projection.get('pattern_name'), 'confirmed chart pattern')
        return f'based on the detected {name}'
    return 'extrapolated -- no confirmed chart pattern for this stock'


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
        f"You've been added as a viewer on Nari Nakhre Stocks. Every day, every golden-cross stock that "
        f"clears our screening bar gets emailed to you -- buy price, target sell price, stop-loss, and "
        f"timing for each, marked Highly Recommended or Recommended so you can see at a glance which are "
        f"strongest. You can also log in anytime to see today's recommendations and your full "
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
        f"<p>You've been added as a viewer on Nari Nakhre Stocks. Every day, every golden-cross stock that "
        f"clears our screening bar gets emailed to you -- buy price, target sell price, stop-loss, and "
        f"timing for each, marked Highly Recommended or Recommended so you can see at a glance which are "
        f"strongest. You can also log in anytime to see today's recommendations and your full "
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
        f"Your Rs 299/month subscription is active. Every day, every golden-cross stock that clears our "
        f"screening bar gets emailed to you -- buy price, target sell price, stop-loss, and the reasoning "
        f"behind each one, marked Highly Recommended or Recommended so you can see at a glance which are "
        f"strongest.\n\n"
        f'Your subscription renews automatically on {current_period_end_label} -- '
        f"we'll email you a reminder a few days before, and you can cancel "
        f"anytime from your account.\n\n"
        f'Login: {STOCKS_LOGIN_URL}\n\n'
        f'{DISCLAIMER}\n'
    )
    html_body = (
        f'<p>Hi {greeting},</p>'
        f"<p>Your Rs 299/month subscription is active. Every day, every golden-cross stock that clears our "
        f"screening bar gets emailed to you -- buy price, target sell price, stop-loss, and the reasoning "
        f"behind each one, marked Highly Recommended or Recommended so you can see at a glance which are "
        f"strongest.</p>"
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


def _trend_label(s):
    """A plain-language recommendation strength instead of the raw NNS
    Score number -- the score/tier still drive internal ranking (see
    suggestion_engine.get_suggestions' own ORDER BY), but a customer-facing
    email shouldn't need to interpret an abstract 0-10 figure. Every
    suggestion is already golden-cross (see is_suggestion_eligible), so:
      - 'Highly Recommended': NNS tier is silver or golden -- golden cross
        AND strong fundamentals both agree.
      - 'Recommended -- extra caution': NNS tier is bronze -- still
        golden-cross and clears the quality floor, but on a weaker
        fundamental footing; see the rationale for specifics.
      - 'Recommended' (no tier stored -- a pre-NNS-Score row)."""
    tier = s.get('nns_tier')
    if tier in ('silver', 'golden'):
        return 'Highly Recommended'
    if tier == 'bronze':
        return 'Recommended -- extra caution'
    return 'Recommended'


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


def _capitalize_first(text):
    """Uppercases just the first character, unlike str.capitalize() which
    also lowercases the rest of the string -- fundamentals_note contains
    'PE'/'OPM' abbreviations that must stay upper-case."""
    return text[:1].upper() + text[1:] if text else text


def _company_display_name(s):
    """Company name if we have one, else the symbol -- stock_watchlist.name
    isn't always populated (see get_suggestions' company_name column,
    sourced from it), so this must never show blank/None in place of a
    company that's still perfectly identifiable by its symbol."""
    return s.get('company_name') or s['symbol']


def _identity_suffix(s):
    """The '(...)' identity shown next to the company name -- 'SYMBOL ·
    EXCHANGE' normally, or just 'EXCHANGE' when _company_display_name
    already fell back to the symbol itself, so the symbol isn't shown
    twice back to back (e.g. 'GLD (GLD · NSE)')."""
    if _company_display_name(s) == s['symbol']:
        return s['exchange']
    return f"{s['symbol']} · {s['exchange']}"


def _timing_text(s):
    """A pattern-based suggestion (see suggestion_engine.generate_daily_suggestions
    / utils.price_pattern.compute_suggestion_pricing) shows the cited
    pattern_note INSTEAD OF a "hold N days" figure -- chart-pattern shape
    doesn't reliably predict timing the way a specific day-count would
    misleadingly imply. holding_period_days is only ever a fixed internal
    default in that case, not shown here."""
    return s['pattern_note'] if s.get('pattern_name') else f"Hold {s['holding_period_days']} days."


def _render_stock_card_text(s):
    """Plain-text rendering of one suggestion -- a readable block rather
    than a cramped single line, for text-only mail clients (the HTML
    version below is what most recipients actually see)."""
    trend = _trend_label(s)
    fundamentals_note = _fundamentals_note(s)
    projection = compute_projection_targets(s.get('buy_price'), s.get('target_sell_price'), s.get('pattern_name'))

    lines = [
        f"{_company_display_name(s)} ({_identity_suffix(s)}) — {trend}",
    ]
    if fundamentals_note:
        lines.append(f'  {_capitalize_first(fundamentals_note)}')
    lines.append(f"  Buy: Rs {s['buy_price']}   Stop-loss: Rs {s['stop_loss_price']}")
    if projection:
        mid, long_term = projection['mid_period'], projection['long_term']
        lines.append(
            f"  Projected price -- {mid['label']}: Rs {mid['price']:g} | "
            f"{long_term['label']}: Rs {long_term['price']:g} ({_projection_method_note(projection)})"
        )
    lines.append(f'  {_timing_text(s)}')
    lines.append(f'  Why: {s["rationale"]}')
    return '\n'.join(lines)


def _render_stock_card_html(s):
    """Styled HTML rendering of one suggestion -- company name front and
    center (not just the ticker symbol), a colored recommendation-strength
    pill, buy/stop-loss called out as the two numbers that matter most for
    acting today, this stock's own mid-period/long-term projection (see
    utils.price_pattern.compute_projection_targets -- NOT the same fixed
    calendar point for every stock), an embedded projection chart (see
    suggestion_chart.build_prediction_chart_data_uri) when there's enough
    data to draw one, and the reasoning set apart in its own highlighted
    paragraph instead of packed into a table cell. Built with inline
    styles and nested tables (not CSS classes/flex/grid) -- the only
    layout approach that renders consistently across email clients,
    Outlook's Word-based renderer included."""
    trend = _trend_label(s)
    badge_bg, badge_fg = _TREND_BADGE_COLORS.get(trend, _TREND_BADGE_COLORS['Recommended'])
    fundamentals_note = _fundamentals_note(s)
    projection = compute_projection_targets(s.get('buy_price'), s.get('target_sell_price'), s.get('pattern_name'))
    chart_uri = build_prediction_chart_data_uri(s.get('buy_price'), projection, s.get('stop_loss_price'))

    horizon_block = ''
    if projection:
        mid, long_term = projection['mid_period'], projection['long_term']
        horizon_block = (
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            'style="margin-top:14px;border-top:1px solid #e2e8f0;">'
            '<tr style="border-top:1px solid #e2e8f0;">'
            f'<td style="padding:6px 10px;color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:.03em;">{mid["label"]}</td>'
            f'<td style="padding:6px 10px;color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:.03em;">{long_term["label"]}</td>'
            '</tr><tr>'
            f'<td style="padding:2px 10px 0;color:#0f172a;font-size:15px;font-weight:bold;">Rs {mid["price"]:g}</td>'
            f'<td style="padding:2px 10px 0;color:#0f172a;font-size:15px;font-weight:bold;">Rs {long_term["price"]:g}</td>'
            '</tr></table>'
            f'<div style="margin-top:4px;color:#94a3b8;font-size:11px;">{_capitalize_first(_projection_method_note(projection))}</div>'
        )

    chart_block = ''
    if chart_uri:
        chart_block = (
            f'<div style="margin-top:14px;"><img src="{chart_uri}" width="520" height="230" alt="'
            f"Projected price chart for {_company_display_name(s)}: buy Rs {s.get('buy_price')} through its "
            'mid-period and long-term projected price" '
            'style="max-width:100%;height:auto;display:block;border:1px solid #e2e8f0;border-radius:8px;"></div>'
        )

    fundamentals_html = f'<div style="margin-top:6px;color:#64748b;font-size:12.5px;">{_capitalize_first(fundamentals_note)}</div>' if fundamentals_note else ''

    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="max-width:600px;margin:0 0 20px 0;border:1px solid #e2e8f0;border-radius:12px;'
        'overflow:hidden;font-family:Arial,Helvetica,sans-serif;">'
        '<tr><td style="background:#0f172a;padding:14px 18px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
        f'<td style="color:#f8fafc;font-size:17px;font-weight:bold;">{_company_display_name(s)}</td>'
        '<td align="right">'
        f'<span style="display:inline-block;padding:4px 12px;border-radius:999px;font-size:12px;'
        f'font-weight:bold;background:{badge_bg};color:{badge_fg};white-space:nowrap;">{trend}</span>'
        '</td></tr></table>'
        f'<div style="color:#94a3b8;font-size:12px;margin-top:2px;">{_identity_suffix(s)}</div>'
        '</td></tr>'
        '<tr><td style="padding:16px 18px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
        '<td style="width:50%;"><div style="color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:.03em;">Buy</div>'
        f'<div style="font-size:19px;font-weight:bold;color:#0f172a;">Rs {s["buy_price"]}</div></td>'
        '<td style="width:50%;"><div style="color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:.03em;">Stop-loss</div>'
        f'<div style="font-size:19px;font-weight:bold;color:#b91c1c;">Rs {s["stop_loss_price"]}</div></td>'
        '</tr></table>'
        f'{horizon_block}'
        f'{chart_block}'
        f'<div style="margin-top:14px;padding:12px 14px;background:#f8fafc;border-left:3px solid #0ea5e9;'
        f'border-radius:4px;color:#334155;font-size:13px;line-height:1.5;">{s["rationale"]}</div>'
        f'{fundamentals_html}'
        f'<div style="margin-top:10px;color:#64748b;font-size:12.5px;">{_timing_text(s)}</div>'
        '</td></tr></table>'
    )


def _build_email_content(suggestions, today_label):
    """Returns (subject, textbody, htmlbody). Always produces a real email,
    even with zero suggestions -- a silent "nothing sent" looks identical
    to the job having crashed, from a recipient's side.

    Every currently golden-cross, NNS_BRONZE_MIN-or-better stock goes out
    -- see suggestion_engine.generate_daily_suggestions's module docstring
    -- so this can be anywhere from zero rows (nothing clears the bar
    today) to several. Rows are already tier-ranked highest first (see
    suggestion_engine.get_suggestions' ORDER BY s.score DESC), which
    naturally groups every Highly Recommended (silver/golden NNS tier)
    stock ahead of any Recommended (bronze tier) one -- no separate sort
    needed here to get that grouping. The raw NNS Score number itself is
    deliberately never shown here -- see _trend_label -- only the buy/
    target/stop-loss levels (the actual, concrete numbers that matter) and
    a plain-language recommendation strength. Each stock is rendered as its
    own card (see _render_stock_card_html/_render_stock_card_text) with the
    company name up front, its own mid-period/long-term projection, and an embedded
    projection chart when there's data to draw one."""
    if not suggestions:
        subject = f'Nari Nakhre Stocks — No Recommendations Today ({today_label})'
        text_body = (
            f'No stock met our screening criteria today ({today_label}).\n\n'
            f'{DISCLAIMER}\n'
        )
        html_body = (
            f'<p>No stock met our screening criteria today ({today_label}).</p>'
            f'<p style="color:#64748b;font-size:0.85em;">{DISCLAIMER}</p>'
        )
        return subject, text_body, html_body

    if len(suggestions) == 1:
        subject = f"Nari Nakhre Stocks — Today's Recommendation: {_company_display_name(suggestions[0])} ({today_label})"
    else:
        subject = f"Nari Nakhre Stocks — {len(suggestions)} Recommendations for {today_label}"

    heading = "Today's Recommendation" if len(suggestions) == 1 else "Today's Recommendations"
    any_horizon_targets = any(
        compute_projection_targets(s.get('buy_price'), s.get('target_sell_price'), s.get('pattern_name'))
        for s in suggestions
    )

    text_lines = [f'{heading} ({today_label}):', '']
    for s in suggestions:
        text_lines.append(_render_stock_card_text(s))
        text_lines.append('')
    if any_horizon_targets:
        text_lines.append(HORIZON_DISCLAIMER)
        text_lines.append('')
    text_lines.append(DISCLAIMER)
    text_body = '\n'.join(text_lines)

    horizon_note_html = (
        f'<p style="color:#64748b;font-size:0.8em;line-height:1.5;">{HORIZON_DISCLAIMER}</p>'
        if any_horizon_targets else ''
    )
    html_body = (
        f'<p style="font-family:Arial,Helvetica,sans-serif;font-size:16px;color:#0f172a;">{heading}:</p>'
        + ''.join(_render_stock_card_html(s) for s in suggestions)
        + horizon_note_html
        + f'<p style="color:#64748b;font-size:0.85em;margin-top:16px;font-family:Arial,Helvetica,sans-serif;">{DISCLAIMER}</p>'
    )
    return subject, text_body, html_body


def send_daily_suggestions_email(db, target_date=None):
    """Fetches target_date's stock_suggestions rows via the shared
    suggestion_engine.get_suggestions() query and emails every active
    role='viewer' account in stocks_admin_users -- one send per recipient.
    Recipients now come from stocks_admin_users, NOT the deprecated
    stocks_email_recipients table (see that table's docstring above) --
    a viewer account is a real login, not just an address on a list.
    stocks_admin_users has no separate email column, so the login
    "username" doubles as the recipient address (see create_viewer_account
    in utils/stock_auth.py). Always sends something, even with zero
    suggestions on target_date (see _build_email_content). Every email
    includes the fixed disclaimer line, unmodified, per DISCLAIMER above.

    target_date defaults to today (the normal daily-cron call site, see
    app.py's /stocks/suggestions/send-daily-email) -- pass a date object or
    an ISO 'YYYY-MM-DD' string to resend a past day's recommendations
    instead (see app.py's /stocks/suggestions/resend, the super_admin-only
    manual resend page)."""
    if target_date is None:
        target_date = date.today()
    elif isinstance(target_date, str):
        target_date = date.fromisoformat(target_date[:10])
    date_iso = target_date.isoformat()
    date_label = target_date.strftime('%d %b %Y')

    suggestions = get_suggestions(db, start_date=date_iso, end_date=date_iso)

    subject, text_body, html_body = _build_email_content(suggestions, date_label)

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
