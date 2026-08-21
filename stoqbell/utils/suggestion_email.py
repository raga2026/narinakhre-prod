from datetime import date

from supabase_storage import public_url as _supabase_public_url
from stoqbell.utils.stock_alerting import send_zeptomail_stocks_email
from stoqbell.utils.suggestion_chart import build_prediction_chart_image_url
from stoqbell.utils.suggestion_engine import get_suggestions
from stoqbell.utils.starters_engine import get_starters_suggestions
from stoqbell.utils.large_cap_engine import get_large_cap_bonus_suggestions
from stoqbell.utils.price_pattern import compute_projection_targets
from stoqbell.utils.stocks_referrals import REFERRALS_PER_FREE_MONTH, get_or_create_referral_code

DISCLAIMER = (
    "This is independent market research and analysis, not professional "
    "investment advice. Please do your own research before making any "
    "investment decision."
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

STOCKS_LOGIN_URL = 'https://www.stoqbell.com/stocks/login'
STOCKS_BASE_URL = 'https://www.stoqbell.com'

# Where the site owner is notified of new paid signups/cancellations (see
# send_admin_new_subscriber_email/send_admin_subscription_cancelled_email
# below) -- a fixed address, not configurable per-account, since there's
# only one owner. Sent via send_zeptomail_stocks_email, same as every other
# Stocks email, so the "from" address is whatever STOCKS_SMTP_FROM_EMAIL
# resolves to on Render (see utils/stock_alerting.py), defaulting to
# support-noreply@stoqbell.com.
STOCKS_ADMIN_NOTIFY_EMAIL = 'narinakhre@gmail.com'

_TREND_BADGE_COLORS = {
    'Highly Recommended': ('#dcfce7', '#15803d'),
    'Recommended -- extra caution': ('#ffedd5', '#9a3412'),
    'Recommended': ('#e2e8f0', '#334155'),
}

_PATTERN_DISPLAY_NAMES = {
    'head_and_shoulders_bottom': 'reverse head-and-shoulders pattern',
    'rounding_bottom': 'rounding-bottom pattern',
}


def _pct_increase(buy_price, target_price):
    """'+12.3%' style figure for how much higher a target/projection price
    is than the buy price -- shown beside each of the near-term/mid-period/
    long-term target values so a reader can gauge the size of the move
    without doing the arithmetic themselves. Returns None when buy_price
    isn't a usable positive number (mirrors compute_projection_targets'
    own guard) rather than raising or dividing by zero."""
    if not buy_price or buy_price <= 0 or target_price is None:
        return None
    return round((target_price - buy_price) / buy_price * 100, 1)


def _projection_method_note(projection):
    """'based on the detected reverse head-and-shoulders pattern' or
    'extrapolated -- no confirmed chart pattern', shown right under a
    suggestion's mid-period/long-term figures so it's always clear which
    of the two this particular stock's projection actually is."""
    if projection.get('method') == 'pattern':
        name = _PATTERN_DISPLAY_NAMES.get(projection.get('pattern_name'), 'confirmed chart pattern')
        return f'based on the detected {name}'
    return 'extrapolated -- no confirmed chart pattern for this stock'


def send_viewer_welcome_email(email, name, password, db=None, admin_id=None):
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
    error, or a network error), not just a generic "it failed".

    db/admin_id (both required together), when given, append this
    viewer's own referral footer (see _referral_footer_text/_html) --
    optional/omittable since not every call site has a fresh admin_id
    handy (see the recipient-migration route, which skips this)."""
    greeting = name or email
    subject = 'StoqBell — your login'
    text_body = (
        f'Hi {greeting},\n\n'
        f"You've been added as a viewer on StoqBell. Each day we pick a single stock that clears "
        f"our screening bar and email it to you -- buy price, target sell price, and timing, "
        f"marked Highly Recommended or Recommended so you know at a glance how strong the pick is. Some "
        f"days nothing clears the bar, so there's no email that day. You can also log in anytime to see "
        f"today's recommendation and your full history.\n\n"
        f'Login: {STOCKS_LOGIN_URL}\n'
        f'Username: {email}\n'
        f'Password: {password}\n\n'
        f"This is a temporary password -- you'll be asked to set your own the "
        f"first time you log in.\n\n"
        f'{DISCLAIMER}\n'
    )
    html_body = (
        f'{_stoqbell_logo_header_html()}'
        f'<p>Hi {greeting},</p>'
        f"<p>You've been added as a viewer on StoqBell. Each day we pick a single stock that "
        f"clears our screening bar and email it to you -- buy price, target sell price, and "
        f"timing, marked Highly Recommended or Recommended so you know at a glance how strong the pick "
        f"is. Some days nothing clears the bar, so there's no email that day. You can also log in "
        f"anytime to see today's recommendation and your full history.</p>"
        f'<p><a href="{STOCKS_LOGIN_URL}">{STOCKS_LOGIN_URL}</a><br>'
        f'Username: {email}<br>'
        f'Password: {password}</p>'
        f"<p>This is a temporary password -- you'll be asked to set your own the "
        f"first time you log in.</p>"
        f'<p style="color:#64748b;font-size:0.85em;margin-top:16px;">{DISCLAIMER}</p>'
    )
    if db is not None and admin_id is not None:
        try:
            referral_code = get_or_create_referral_code(db, admin_id)
            text_body += f'\n{_referral_footer_text(referral_code)}\n'
            html_body += _referral_footer_html(referral_code)
        except Exception:
            pass
    return send_zeptomail_stocks_email(
        to_email=email, to_name=greeting, subject=subject,
        textbody=text_body, htmlbody=html_body, sender_name='StoqBell',
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
    auto_trader_url = 'https://www.stoqbell.com/stocks/auto-trader'
    subject = f'StoqBell -- stop-loss hit on {trade["symbol"]}, review needed'
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
        f'{_stoqbell_logo_header_html()}'
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
        textbody=body_text, htmlbody=body_html, sender_name='StoqBell Auto-Trader',
    )


def send_trading_alert_email(to_email, suggestion, buy_link):
    """Sent right after generate_daily_suggestions produces a golden- or
    silver-tier ('Highly Recommended', see _trend_label) Pick of the Day --
    app.py's /stocks/suggestions/send-daily-email job, alongside (not
    instead of) the regular daily suggestions email every recipient gets.
    Goes only to STOP_LOSS_ALERT_EMAIL (utils.auto_trader), the same
    single address that already gets the stop-loss/target-hit alerts --
    this is a trading alert for whoever actually places manual orders, not
    a second copy of the customer-facing daily email. Reuses the same
    stock-card rendering as the daily email (_render_stock_card_html/
    _render_stock_card_text) so this looks and reads identically, just
    with a prominent Buy Now button up top linking to buy_link (see
    app.py's /stocks/suggestions/<id>/buy -- a super_admin-only
    confirmation page, never a one-click purchase straight from the
    email)."""
    subject = f'StoqBell -- Highly Recommended pick: {_company_display_name(suggestion)}'
    text_body = (
        f"Today's pick, {_company_display_name(suggestion)}, is Highly Recommended.\n\n"
        f'{_render_stock_card_text(suggestion)}\n\n'
        f'Buy now: {buy_link}\n\n'
        f'{DISCLAIMER}\n'
    )
    html_inner = (
        '<p style="font-family:Arial,Helvetica,sans-serif;font-size:16px;color:#0f172a;">'
        f"Today's pick, <strong>{_company_display_name(suggestion)}</strong>, is Highly Recommended.</p>"
        '<div style="margin:0 0 16px 0;">'
        f'<a href="{buy_link}" style="display:inline-block;background:#15803d;color:#ffffff;'
        'text-decoration:none;font-size:14px;font-weight:bold;padding:12px 22px;border-radius:8px;">'
        'Buy Now &rarr;</a>'
        '</div>'
        f'{_render_stock_card_html(suggestion)}'
        f'<p style="color:#64748b;font-size:0.85em;margin-top:16px;">{DISCLAIMER}</p>'
    )
    return send_zeptomail_stocks_email(
        to_email=to_email, to_name=to_email, subject=subject,
        textbody=text_body, htmlbody=_wrap_email_html(html_inner), sender_name='StoqBell',
    )


def send_target_hit_email(to_email, trade, pnl_amount, pnl_pct):
    """Sent once per target-hit close -- app.py's
    /stocks/auto-trader/reconcile job, for each entry in
    reconcile_open_trades' 'target_hit' list. Unlike
    send_stop_loss_review_email, this is purely informational: reaching
    target_sell_price auto-sells immediately in EITHER mode (see
    utils.auto_trader's module docstring), so by the time this email goes
    out the position is already closed -- there's no Proceed/Cancel
    decision here, just a notification of what already happened. trade is
    a dict with at least symbol, exchange, buy_price, quantity,
    exit_price."""
    auto_trader_url = 'https://www.stoqbell.com/stocks/auto-trader'
    subject = f'StoqBell -- target hit on {trade["symbol"]}, sold'
    text_body = (
        f'{trade["symbol"]} ({trade["exchange"]}) hit its target and was sold automatically.\n\n'
        f'Bought {trade["quantity"]} shares at Rs {trade["buy_price"]}, sold at Rs {trade["exit_price"]} '
        f'({"+" if pnl_amount >= 0 else ""}Rs {pnl_amount:.2f}, {pnl_pct:+.2f}%).\n\n'
        f'See it on the Auto-Trader dashboard: {auto_trader_url}\n\n'
        f'{DISCLAIMER}\n'
    )
    body_html = (
        f'{_stoqbell_logo_header_html()}'
        f'<p><strong>{trade["symbol"]} ({trade["exchange"]})</strong> hit its target and was sold automatically.</p>'
        f'<p>Bought {trade["quantity"]} shares at Rs {trade["buy_price"]}, sold at Rs {trade["exit_price"]} '
        f'({"+" if pnl_amount >= 0 else ""}Rs {pnl_amount:.2f}, {pnl_pct:+.2f}%).</p>'
        f'<p><a href="{auto_trader_url}">See it on the Auto-Trader dashboard</a>.</p>'
        f'<p style="color:#64748b;font-size:0.85em;margin-top:16px;">{DISCLAIMER}</p>'
    )
    return send_zeptomail_stocks_email(
        to_email=to_email, to_name=to_email, subject=subject,
        textbody=text_body, htmlbody=body_html, sender_name='StoqBell Auto-Trader',
    )


def send_target_achieved_email(to_email, to_name, achievements):
    """Sent to every CURRENT Standard-plan (Rs 299/month) subscriber when
    one or more daily recommendations reaches its target price -- app.py's
    /stocks/suggestions/notify-target-hits job, one email per recipient
    per day, bundling every entry from
    suggestion_engine.find_pending_target_hit_suggestions that landed that
    day (in the rare case more than one hits the same day) rather than
    sending a separate email per stock. Deliberately goes to every current
    Standard subscriber, not just whoever happened to receive the original
    pick's email -- broadcasting a win to the whole current plan
    population, per instruction.

    Unlike send_target_hit_email above (the auto-trader's OWN position,
    sold automatically the moment target is reached), this is purely
    informational -- it doesn't buy or sell anything on the subscriber's
    behalf. It's telling them a recommendation played out and prompting
    them to decide for themselves whether to book profit.

    achievements: list of dicts with company_name, symbol, exchange,
    suggestion_date, buy_price, target_sell_price, latest_price,
    latest_price_date (see find_pending_target_hit_suggestions). Days
    taken and profit % are computed here from suggestion_date/
    latest_price_date and buy_price/latest_price -- the actual latest
    close, not target_sell_price itself, since the close that triggered
    this is usually at or slightly past target, and showing what a
    subscriber would actually book today is more honest than the
    theoretical target figure."""
    greeting = to_name or to_email
    count = len(achievements)
    if count == 1:
        name = achievements[0].get('company_name') or achievements[0]['symbol']
        subject = f'StoqBell -- {name} hit its target'
    else:
        subject = f'StoqBell -- {count} of your recommendations hit target'

    cards_text = []
    cards_html = []
    for a in achievements:
        name = a.get('company_name') or a['symbol']
        suggestion_date = a['suggestion_date']
        if isinstance(suggestion_date, str):
            suggestion_date = date.fromisoformat(suggestion_date[:10])
        achieved_date = a['latest_price_date']
        if isinstance(achieved_date, str):
            achieved_date = date.fromisoformat(achieved_date[:10])
        days_taken = max(0, (achieved_date - suggestion_date).days)
        buy_price = a['buy_price']
        latest_price = a['latest_price']
        profit_pct = round((latest_price - buy_price) / buy_price * 100, 2) if buy_price else None
        profit_str = f"{'+' if profit_pct is not None and profit_pct >= 0 else ''}{profit_pct}%" if profit_pct is not None else 'n/a'
        day_word = 'day' if days_taken == 1 else 'days'

        cards_text.append(
            f"{name} ({a['symbol']} · {a['exchange']})\n"
            f"  Recommended: {suggestion_date.strftime('%d %b %Y')} at Rs {buy_price}\n"
            f"  Target: Rs {a['target_sell_price']}\n"
            f"  Achieved: {achieved_date.strftime('%d %b %Y')} at Rs {latest_price} "
            f"({days_taken} {day_word}, {profit_str} profit)"
        )
        cards_html.append(
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            'style="max-width:600px;width:100%;margin:0 0 16px 0;border:1px solid #e2e8f0;border-radius:12px;'
            'overflow:hidden;font-family:Arial,Helvetica,sans-serif;">'
            '<tr><td style="background:#14532d;padding:12px 18px;">'
            f'<div style="color:#f8fafc;font-size:16px;font-weight:bold;">{name}</div>'
            f'<div style="color:#bbf7d0;font-size:12px;margin-top:2px;">{a["symbol"]} · {a["exchange"]} -- target reached</div>'
            '</td></tr>'
            '<tr><td style="padding:14px 18px;">'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
            '<td style="width:33%;"><div style="color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:.03em;">Recommended</div>'
            f'<div style="font-size:15px;font-weight:bold;color:#0f172a;">Rs {buy_price}</div>'
            f'<div style="color:#94a3b8;font-size:11px;">{suggestion_date.strftime("%d %b %Y")}</div></td>'
            '<td style="width:34%;"><div style="color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:.03em;">Target</div>'
            f'<div style="font-size:15px;font-weight:bold;color:#0f172a;">Rs {a["target_sell_price"]}</div></td>'
            '<td style="width:33%;"><div style="color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:.03em;">Achieved</div>'
            f'<div style="font-size:15px;font-weight:bold;color:#15803d;">Rs {latest_price}</div>'
            f'<div style="color:#94a3b8;font-size:11px;">{achieved_date.strftime("%d %b %Y")}</div></td>'
            '</tr></table>'
            f'<div style="margin-top:10px;color:#334155;font-size:13px;">'
            f'Took {days_taken} {day_word} — {profit_str} profit at today\'s price.</div>'
            '</td></tr></table>'
        )

    intro_line = (
        'Your recommendation has reached its target:' if count == 1
        else f'{count} of your recommendations have reached target:'
    )
    intro_text = f'Hi {greeting},\n\n{intro_line}\n\n'
    intro_html = (
        f'<p style="font-family:Arial,Helvetica,sans-serif;font-size:16px;color:#0f172a;">Hi {greeting},</p>'
        f'<p style="font-family:Arial,Helvetica,sans-serif;font-size:16px;color:#0f172a;">{intro_line}</p>'
    )

    cta_line = (
        "Worth considering booking profit on this now -- reaching target doesn't guarantee the price holds "
        "from here; this is a point to review and decide for yourself whether to sell, hold, or book partial "
        "profit."
    )
    cta_html = (
        f'<p style="font-family:Arial,Helvetica,sans-serif;color:#334155;font-size:13px;line-height:1.5;'
        f'background:#f0fdf4;border-left:3px solid #15803d;border-radius:4px;padding:10px 14px;margin-top:8px;">'
        f'{cta_line}</p>'
    )

    text_body = intro_text + '\n\n'.join(cards_text) + f'\n\n{cta_line}\n\n{DISCLAIMER}\n'
    html_inner = (
        intro_html
        + ''.join(cards_html)
        + cta_html
        + f'<p style="color:#64748b;font-size:0.85em;margin-top:16px;font-family:Arial,Helvetica,sans-serif;">{DISCLAIMER}</p>'
    )

    return send_zeptomail_stocks_email(
        to_email=to_email, to_name=greeting, subject=subject,
        textbody=text_body, htmlbody=_wrap_email_html(html_inner), sender_name='StoqBell',
    )


def send_subscription_welcome_email(email, name, current_period_end_label, suggestions=None, db=None, admin_id=None,
                                     plan='standard'):
    """Sent right after a self-serve StoqBell signup's first
    Razorpay payment is verified (see app.py's /stocks/subscribe/verify) --
    unlike send_viewer_welcome_email above, there's no password to disclose
    here: a self-serve account either set its own password at signup, or
    has none at all (a Google-only signup, see utils/stock_auth.py's
    create_pending_google_subscriber). current_period_end_label is a
    pre-formatted date string (see app.py), not a raw datetime -- keeps
    this module free of date-formatting/timezone concerns.

    plan ('standard' or 'starters', see STOCKS_AUTH_ALTER_SQL's stocks_plan
    column) picks the right price/cadence copy -- Rs 299/mo daily for
    'standard', Rs 99/mo up to two picks a week for 'starters'. Matters
    here specifically because this text used to hardcode "Rs 299/month"
    unconditionally, which was wrong for a Starters signup.

    suggestions, when given a non-empty list (today's stock_suggestions
    rows for 'standard', or the most recent stock_starters_suggestions
    rows for 'starters' -- see get_suggestions/get_starters_suggestions),
    appends the actual recommendation card(s) (see
    _render_stock_card_html/_render_stock_card_text) right in this same
    email -- a brand-new subscriber sees their pick(s) immediately, in the
    welcome email itself, rather than only after separately logging in.
    Omitted/empty (the default) sends the plain welcome text alone -- e.g.
    a signup completed after that period's suggestions already went out,
    or with nothing to show yet.

    db/admin_id (both required together), when given, append this new
    subscriber's own referral footer (see _referral_footer_text/_html) --
    introducing referrals right at the moment they've just paid is when
    it's most likely to land."""
    is_starters = plan == 'starters'
    greeting = name or email
    has_suggestions = bool(suggestions)
    recommendation_noun = "this week's pick" if is_starters else "today's recommendation"
    subject = (
        f'Welcome to StoqBell -- {recommendation_noun} inside'
        if has_suggestions else 'Welcome to StoqBell -- payment confirmed'
    )
    if is_starters:
        price_cadence_text = (
            "Your Rs 99/month Starters subscription is active. Up to twice a week (Mondays), we send a "
            "separately-curated stock that clears our strictest, golden-tier quality bar -- buy price, "
            "target sell price, and the reasoning behind it. Some weeks nothing clears that bar, so there's "
            "no email that week."
        )
    else:
        price_cadence_text = (
            "Your Rs 299/month subscription is active. Each day we pick a single stock that clears our "
            "screening bar and email it to you -- buy price, target sell price, and the reasoning "
            "behind it, marked Highly Recommended or Recommended so you know at a glance how strong the pick "
            "is. Some days nothing clears the bar, so there's no email that day."
        )
    intro_text = (
        f'Hi {greeting},\n\n'
        f'{price_cadence_text}\n\n'
        f'Your subscription renews automatically on {current_period_end_label} -- '
        f"we'll email you a reminder a few days before, and you can cancel "
        f"anytime from your account.\n\n"
    )
    intro_html = (
        f'<p style="font-family:Arial,Helvetica,sans-serif;color:#0f172a;">Hi {greeting},</p>'
        f'<p style="font-family:Arial,Helvetica,sans-serif;color:#0f172a;">{price_cadence_text}</p>'
        f'<p style="font-family:Arial,Helvetica,sans-serif;color:#0f172a;">Your subscription renews automatically on '
        f"<strong>{current_period_end_label}</strong> -- we'll email you a reminder a few days before, and you can "
        f"cancel anytime from your account.</p>"
    )

    if has_suggestions:
        if is_starters:
            heading = "This Week's Pick" if len(suggestions) == 1 else "This Week's Picks"
        else:
            heading = "Today's Recommendation" if len(suggestions) == 1 else "Today's Recommendations"
        intro_text += f'{heading}:\n\n'
        for s in suggestions:
            intro_text += _render_stock_card_text(s) + '\n\n'
        any_horizon_targets = any(
            compute_projection_targets(s.get('buy_price'), s.get('target_sell_price'), s.get('pattern_name'))
            for s in suggestions
        )
        if any_horizon_targets:
            intro_text += HORIZON_DISCLAIMER + '\n\n'

        intro_html += f'<p style="font-family:Arial,Helvetica,sans-serif;font-size:16px;color:#0f172a;">{heading}:</p>'
        intro_html += ''.join(_render_stock_card_html(s) for s in suggestions)
        if any_horizon_targets:
            intro_html += f'<p style="color:#64748b;font-size:0.8em;line-height:1.5;font-family:Arial,Helvetica,sans-serif;">{HORIZON_DISCLAIMER}</p>'
    else:
        no_pick_text = (
            "Nothing has cleared our screening bar yet, so there's no pick to show yet -- log in anytime to see "
            "the latest."
        ) if is_starters else (
            "Nothing cleared our screening bar today, so there's no pick to show yet -- log in anytime to see "
            "the latest."
        )
        intro_text += f'{no_pick_text}\n\n'
        intro_html += f'<p style="font-family:Arial,Helvetica,sans-serif;color:#0f172a;">{no_pick_text}</p>'

    referral_footer_text = ''
    referral_footer_html = ''
    if db is not None and admin_id is not None:
        try:
            referral_code = get_or_create_referral_code(db, admin_id)
            referral_footer_text = f'\n{_referral_footer_text(referral_code)}\n'
            referral_footer_html = _referral_footer_html(referral_code)
        except Exception:
            pass

    text_body = intro_text + f'Login: {STOCKS_LOGIN_URL}\n\n{DISCLAIMER}\n' + referral_footer_text
    html_body = _wrap_email_html(
        intro_html
        + f'<p style="font-family:Arial,Helvetica,sans-serif;"><a href="{STOCKS_LOGIN_URL}">{STOCKS_LOGIN_URL}</a></p>'
        + f'<p style="color:#64748b;font-size:0.85em;margin-top:16px;font-family:Arial,Helvetica,sans-serif;">{DISCLAIMER}</p>'
        + referral_footer_html
    )
    return send_zeptomail_stocks_email(
        to_email=email, to_name=greeting, subject=subject,
        textbody=text_body, htmlbody=html_body, sender_name='StoqBell',
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
        subject = f'StoqBell -- renews on {current_period_end_label}'
        headline = (
            f"Your StoqBell subscription will auto-renew on "
            f"{current_period_end_label} for Rs 299. No action needed if you'd "
            f"like to continue -- this is just a heads-up before the charge."
        )
    else:
        subject = f'StoqBell -- access ends {current_period_end_label}'
        headline = (
            f"Your StoqBell access ends on {current_period_end_label} "
            f"(your subscription was cancelled and won't auto-renew). "
            f"Resubscribe anytime before then to keep it uninterrupted."
        )
    text_body = f'Hi {greeting},\n\n{headline}\n\nLogin: {STOCKS_LOGIN_URL}\n\n{DISCLAIMER}\n'
    html_body = (
        f'{_stoqbell_logo_header_html()}'
        f'<p>Hi {greeting},</p><p>{headline}</p>'
        f'<p><a href="{STOCKS_LOGIN_URL}">{STOCKS_LOGIN_URL}</a></p>'
        f'<p style="color:#64748b;font-size:0.85em;margin-top:16px;">{DISCLAIMER}</p>'
    )
    return send_zeptomail_stocks_email(
        to_email=email, to_name=greeting, subject=subject,
        textbody=text_body, htmlbody=html_body, sender_name='StoqBell',
    )


def send_admin_new_subscriber_email(email, name):
    """Notifies the site owner (STOCKS_ADMIN_NOTIFY_EMAIL) every time a
    self-serve signup completes its FIRST payment -- see app.py's
    /stocks/subscribe/verify, called right after activate_subscription.
    Registering alone (still subscription_status='pending', checkout never
    completed) does NOT trigger this -- only an actually-activated paid
    account does, since "registers and makes payment" is one combined
    event, not two. Renewals (the recurring 'subscription.charged' webhook
    event) don't trigger this either -- this is specifically about a new
    subscriber, not every monthly charge on an existing one."""
    greeting = name or email
    subject = f'New StoqBell subscriber: {greeting}'
    text_body = (
        f'{greeting} ({email}) just signed up and completed payment for the '
        f'Rs 299/month StoqBell subscription.\n'
    )
    html_body = (
        f'{_stoqbell_logo_header_html()}'
        f'<p><strong>{greeting}</strong> ({email}) just signed up and completed payment for the '
        f'Rs 299/month StoqBell subscription.</p>'
    )
    return send_zeptomail_stocks_email(
        to_email=STOCKS_ADMIN_NOTIFY_EMAIL, to_name='Nari Nakhre', subject=subject,
        textbody=text_body, htmlbody=html_body, sender_name='StoqBell',
    )


def send_admin_subscription_cancelled_email(email, name):
    """Notifies the site owner (STOCKS_ADMIN_NOTIFY_EMAIL) whenever a
    subscriber cancels -- see app.py's /stocks/razorpay/webhook, the
    'subscription.cancelled'/'subscription.completed' branch, right after
    mark_subscription_cancelled. Access itself keeps working through the
    already-paid period end (see subscription_is_current) -- this is just
    the owner's heads-up that it won't auto-renew, not a claim that access
    already ended."""
    greeting = name or email
    subject = f'StoqBell subscription cancelled: {greeting}'
    text_body = (
        f'{greeting} ({email}) just cancelled their Rs 299/month StoqBell subscription. '
        f'Their access continues through the end of the period already paid for.\n'
    )
    html_body = (
        f'{_stoqbell_logo_header_html()}'
        f'<p><strong>{greeting}</strong> ({email}) just cancelled their Rs 299/month StoqBell '
        f'subscription. Their access continues through the end of the period already paid for.</p>'
    )
    return send_zeptomail_stocks_email(
        to_email=STOCKS_ADMIN_NOTIFY_EMAIL, to_name='Nari Nakhre', subject=subject,
        textbody=text_body, htmlbody=html_body, sender_name='StoqBell',
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


def _stock_url(s):
    """Link to this stock's full analysis page -- /stocks/universe/<id>,
    NOT /stocks/company/<watchlist_id> (that one is staff/can_view_watchlist
    -only and 403s for a plain self-serve viewer, see get_suggestions'
    docstring on why universe_id is joined in specifically for this).
    Returns None if universe_id wasn't resolved (the company was removed
    from stock_universe after this suggestion was made) -- callers must
    skip the link in that rare case rather than build a broken URL."""
    universe_id = s.get('universe_id')
    if not universe_id:
        return None
    return f'{STOCKS_BASE_URL}/stocks/universe/{universe_id}'


def _highlights(s):
    """The handful of basic technical/fundamental numbers worth putting
    right on the card -- RSI at suggestion time, PE, PEG, OPM -- whichever
    of these actually have a value (not gated to silver-tier the way
    _fundamentals_note's PE/OPM callout is; this is a plain factual
    snapshot, shown regardless of tier). Returns a list of (label, value)
    pairs, e.g. [('RSI', '54.2'), ('PE', '18.20')] -- empty list if none of
    the four are available (an old suggestion predating these columns)."""
    fields = [
        ('RSI', s.get('rsi_at_suggestion'), '{:.1f}'),
        ('PE', s.get('pe_at_suggestion'), '{:.2f}'),
        ('PEG', s.get('peg_at_suggestion'), '{:.2f}'),
        ('OPM', s.get('opm_at_suggestion'), '{:.0f}%'),
    ]
    return [(label, fmt.format(value)) for label, value, fmt in fields if value is not None]


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
    target_label = 'Target' if s.get('pattern_name') else f"Target ({s['holding_period_days']}-day)"
    target_pct = _pct_increase(s.get('buy_price'), s.get('target_sell_price'))
    target_pct_text = f' (+{target_pct}%)' if target_pct is not None else ''
    lines.append(
        f"  Buy: Rs {s['buy_price']}   {target_label}: Rs {s['target_sell_price']}{target_pct_text}"
    )
    highlights = _highlights(s)
    if highlights:
        lines.append('  ' + '   '.join(f'{label}: {value}' for label, value in highlights))
    if projection:
        mid, long_term = projection['mid_period'], projection['long_term']
        mid_pct = _pct_increase(s.get('buy_price'), mid['price'])
        long_pct = _pct_increase(s.get('buy_price'), long_term['price'])
        mid_pct_text = f' (+{mid_pct}%)' if mid_pct is not None else ''
        long_pct_text = f' (+{long_pct}%)' if long_pct is not None else ''
        lines.append(
            f"  Projected price -- {mid['label']}: Rs {mid['price']:g}{mid_pct_text} | "
            f"{long_term['label']}: Rs {long_term['price']:g}{long_pct_text} ({_projection_method_note(projection)})"
        )
    lines.append(f'  {_timing_text(s)}')
    lines.append(f'  Why: {s["rationale"]}')
    stock_url = _stock_url(s)
    if stock_url:
        lines.append(f'  Full analysis: {stock_url}')
    return '\n'.join(lines)


def _render_stock_card_html(s):
    """Styled HTML rendering of one suggestion -- company name front and
    center (not just the ticker symbol), a colored recommendation-strength
    pill, buy/target called out as the numbers that matter most
    for acting today, this stock's own mid-period/long-term projection (see
    utils.price_pattern.compute_projection_targets -- NOT the same fixed
    calendar point for every stock), an embedded projection chart (see
    suggestion_chart.build_prediction_chart_image_url) when there's enough
    data to draw one, and the reasoning set apart in its own highlighted
    paragraph instead of packed into a table cell. Built with inline
    styles and nested tables (not CSS classes/flex/grid) -- the only
    layout approach that renders consistently across email clients,
    Outlook's Word-based renderer included.

    Deliberately never shows stop_loss_price -- these are investment picks
    to research and decide on yourself, not trading calls with a defined
    exit level. stop_loss_price is still computed and stored (see
    utils.price_pattern.compute_suggestion_pricing) and still drives the
    auto-trader's own internal safety flow (utils/auto_trader.py) for
    positions bought through it -- only the subscriber-facing display of it
    is gone."""
    trend = _trend_label(s)
    badge_bg, badge_fg = _TREND_BADGE_COLORS.get(trend, _TREND_BADGE_COLORS['Recommended'])
    fundamentals_note = _fundamentals_note(s)
    target_label = 'Target' if s.get('pattern_name') else f"Target ({s['holding_period_days']}-day)"
    target_pct = _pct_increase(s.get('buy_price'), s.get('target_sell_price'))
    target_pct_html = f' <span style="color:#15803d;font-size:13px;font-weight:normal;">(+{target_pct}%)</span>' if target_pct is not None else ''
    projection = compute_projection_targets(s.get('buy_price'), s.get('target_sell_price'), s.get('pattern_name'))
    chart_uri = build_prediction_chart_image_url(s.get('buy_price'), projection)

    horizon_block = ''
    if projection:
        mid, long_term = projection['mid_period'], projection['long_term']
        mid_pct = _pct_increase(s.get('buy_price'), mid['price'])
        long_pct = _pct_increase(s.get('buy_price'), long_term['price'])
        mid_pct_html = f' <span style="color:#94a3b8;font-weight:normal;">(+{mid_pct}%)</span>' if mid_pct is not None else ''
        long_pct_html = f' <span style="color:#94a3b8;font-weight:normal;">(+{long_pct}%)</span>' if long_pct is not None else ''
        horizon_block = (
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            'style="margin-top:14px;border-top:1px solid #e2e8f0;">'
            '<tr style="border-top:1px solid #e2e8f0;">'
            f'<td style="padding:6px 10px;color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:.03em;">{mid["label"]}</td>'
            f'<td style="padding:6px 10px;color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:.03em;">{long_term["label"]}</td>'
            '</tr><tr>'
            f'<td style="padding:2px 10px 0;color:#0f172a;font-size:15px;font-weight:bold;">Rs {mid["price"]:g}{mid_pct_html}</td>'
            f'<td style="padding:2px 10px 0;color:#0f172a;font-size:15px;font-weight:bold;">Rs {long_term["price"]:g}{long_pct_html}</td>'
            '</tr></table>'
            f'<div style="margin-top:4px;color:#94a3b8;font-size:11px;">{_capitalize_first(_projection_method_note(projection))}</div>'
        )

    chart_block = ''
    if chart_uri:
        chart_block = (
            '<div style="margin-top:14px;padding:12px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;">'
            '<div style="color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:.03em;margin-bottom:8px;">Price Projection</div>'
            f'<img src="{chart_uri}" width="100%" alt="'
            f"Projected price chart for {_company_display_name(s)}: buy Rs {s.get('buy_price')} through its "
            'mid-period and long-term projected price" '
            'style="width:100%;max-width:496px;height:auto;display:block;border-radius:6px;">'
            '</div>'
        )

    highlights = _highlights(s)
    highlights_block = ''
    if highlights:
        chips = ''.join(
            f'<span style="display:inline-block;background:#f1f5f9;color:#334155;border-radius:6px;'
            f'padding:4px 10px;margin:0 6px 6px 0;font-size:12px;white-space:nowrap;">'
            f'<span style="color:#64748b;">{label}</span> {value}</span>'
            for label, value in highlights
        )
        highlights_block = f'<div style="margin-top:12px;">{chips}</div>'

    fundamentals_html = f'<div style="margin-top:6px;color:#64748b;font-size:12.5px;">{_capitalize_first(fundamentals_note)}</div>' if fundamentals_note else ''

    stock_url = _stock_url(s)
    link_block = ''
    if stock_url:
        link_block = (
            '<div style="margin-top:16px;">'
            f'<a href="{stock_url}" style="display:inline-block;background:#0ea5e9;color:#ffffff;'
            'text-decoration:none;font-size:13px;font-weight:bold;padding:10px 18px;border-radius:8px;">'
            f'View full analysis for {s["symbol"]} &rarr;</a>'
            '</div>'
        )

    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="stock-card" '
        'style="max-width:600px;width:100%;margin:0 0 20px 0;border:1px solid #e2e8f0;border-radius:12px;'
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
        f'<td style="width:50%;"><div style="color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:.03em;">{target_label}</div>'
        f'<div style="font-size:19px;font-weight:bold;color:#15803d;">Rs {s["target_sell_price"]}{target_pct_html}</div></td>'
        '</tr></table>'
        f'{highlights_block}'
        f'{horizon_block}'
        f'{chart_block}'
        f'<div style="margin-top:14px;padding:12px 14px;background:#f8fafc;border-left:3px solid #0ea5e9;'
        f'border-radius:4px;color:#334155;font-size:13px;line-height:1.5;">{s["rationale"]}</div>'
        f'{fundamentals_html}'
        f'<div style="margin-top:10px;color:#64748b;font-size:12.5px;">{_timing_text(s)}</div>'
        f'{link_block}'
        '</td></tr></table>'
    )


def _stoqbell_logo_header_html():
    """Returns an <img>-based logo header, or '' if Supabase isn't
    configured (public_url returns None) -- computed fresh on every call,
    not cached at module import time, since SUPABASE_URL is only populated
    partway through app.py's own startup (see supabase_storage.py). The
    logo itself is uploaded once at app startup by
    stoqbell/routes.py's _ensure_stoqbell_logo_assets_uploaded(); this
    just references its (deterministic) public URL."""
    url = _supabase_public_url('stoqbell/stoqbell-logo.png')
    if not url:
        return ''
    return (
        '<div style="text-align:center;padding:18px 0 6px;">'
        f'<img src="{url}" alt="StoqBell" height="36" '
        'style="height:36px;width:auto;border:0;">'
        '</div>'
    )


def _wrap_email_html(inner_html):
    """Wraps inner_html (a fragment of <p>/<table> markup) in a real,
    minimal HTML document with a viewport meta tag and a small responsive
    <style> block -- without this, send_zeptomail_stocks_email's htmlbody
    goes out as a bare fragment with no <head> at all, which is exactly
    why the email didn't fit properly on mobile: no viewport meta means
    phone mail clients (iOS Mail in particular) render it at a fixed
    desktop width and shrink the whole thing to fit, making everything
    tiny until the reader pinch-zooms. table/img max-width:100% below is
    the actual fix that lets each stock card and its chart shrink to the
    screen instead of overflowing it; the @media block is a belt-and-
    braces refinement for the mail clients that do honor it (most
    webmail/mobile clients; Outlook desktop ignores <style> media queries
    but still benefits from the width:100%/max-width rules above, which
    it does honor)."""
    return (
        '<!doctype html>'
        '<html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
        '<title>StoqBell</title>'
        '<style>'
        'body,table,td,a{-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%;}'
        'table{border-collapse:collapse !important;}'
        'img{border:0;line-height:100%;outline:none;text-decoration:none;}'
        'body{margin:0;padding:0;width:100% !important;background:#eef2f6;}'
        '@media only screen and (max-width:600px){'
        '.email-container{width:100% !important;padding:0 12px !important;}'
        '.stock-card{max-width:100% !important;}'
        '}'
        '</style></head>'
        '<body style="margin:0;padding:0;background:#eef2f6;">'
        '<div style="display:none;max-height:0;overflow:hidden;">'
        "Today's stock recommendations from StoqBell"
        '</div>'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#eef2f6;">'
        '<tr><td align="center" style="padding:20px 0;">'
        '<div class="email-container" style="max-width:600px;width:100%;padding:0 16px;">'
        f'{_stoqbell_logo_header_html()}'
        f'{inner_html}'
        '</div>'
        '</td></tr></table>'
        '</body></html>'
    )


def _referral_footer_text(referral_code):
    """Personalized referral pitch appended per-recipient (see
    send_daily_suggestions_email) -- one line telling this specific
    recipient their own link/code, not a generic pitch, since a shared
    code shared by mistake can't be attributed to the right referrer."""
    link = f'{STOCKS_BASE_URL}/stocks/signup?ref={referral_code}'
    return (
        f'Refer {REFERRALS_PER_FREE_MONTH} people who subscribe and get a month free -- they get their first '
        f'month for Rs 199 instead of Rs 299 with your link: {link} (or share code {referral_code}).'
    )


def _referral_footer_html(referral_code):
    link = f'{STOCKS_BASE_URL}/stocks/signup?ref={referral_code}'
    return (
        '<p style="color:#64748b;font-size:0.8em;margin-top:16px;font-family:Arial,Helvetica,sans-serif;'
        'border-top:1px solid #e2e8f0;padding-top:12px;">'
        f'Refer {REFERRALS_PER_FREE_MONTH} people who subscribe and get a month free -- they get their first month '
        f'for Rs 199 instead of Rs 299 with <a href="{link}" style="color:#0ea5e9;">your referral link</a> '
        f'(or share code <strong>{referral_code}</strong>).</p>'
    )


def _build_email_content(suggestions, today_label):
    """Returns (subject, textbody, html_inner) -- html_inner is the
    UNWRAPPED body fragment (not yet run through _wrap_email_html), since
    send_daily_suggestions_email appends a per-recipient referral footer
    (see get_or_create_referral_code) before wrapping and sending; nothing
    else should treat this return value as a complete document. Always
    produces real content, even with zero suggestions -- a silent
    "nothing sent" looks identical to the job having crashed, from a
    recipient's side.

    Normally exactly one stock -- the single Pick of the Day (see
    suggestion_engine.generate_daily_suggestions's module docstring) --
    but written to handle any count gracefully (zero on a day nothing
    clears the bar; more than one only if this is ever called with a
    differently-generated suggestions list). Rows are already tier-ranked
    highest first when there's more than one (see
    suggestion_engine.get_suggestions' ORDER BY s.score DESC), which
    naturally groups every Highly Recommended (silver/golden NNS tier)
    stock ahead of any Recommended (bronze tier) one -- no separate sort
    needed here to get that grouping. The raw NNS Score number itself is
    deliberately never shown here -- see _trend_label -- only the buy/
    target levels (the actual, concrete numbers that matter) and
    a plain-language recommendation strength. Each stock is rendered as its
    own card (see _render_stock_card_html/_render_stock_card_text) with the
    company name up front, its own mid-period/long-term projection, and an embedded
    projection chart when there's data to draw one."""
    if not suggestions:
        subject = f'StoqBell — No Recommendations Today ({today_label})'
        text_body = (
            f'No stock met our screening criteria today ({today_label}).\n\n'
            f'{DISCLAIMER}\n'
        )
        html_inner = (
            f'<p style="font-family:Arial,Helvetica,sans-serif;color:#0f172a;">No stock met our screening criteria today ({today_label}).</p>'
            f'<p style="color:#64748b;font-size:0.85em;font-family:Arial,Helvetica,sans-serif;">{DISCLAIMER}</p>'
        )
        return subject, text_body, html_inner

    if len(suggestions) == 1:
        subject = f"StoqBell — Today's Recommendation: {_company_display_name(suggestions[0])} ({today_label})"
    else:
        subject = f"StoqBell — {len(suggestions)} Recommendations for {today_label}"

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
        f'<p style="color:#64748b;font-size:0.8em;line-height:1.5;font-family:Arial,Helvetica,sans-serif;">{HORIZON_DISCLAIMER}</p>'
        if any_horizon_targets else ''
    )
    html_inner = (
        f'<p style="font-family:Arial,Helvetica,sans-serif;font-size:16px;color:#0f172a;">{heading}:</p>'
        + ''.join(_render_stock_card_html(s) for s in suggestions)
        + horizon_note_html
        + f'<p style="color:#64748b;font-size:0.85em;margin-top:16px;font-family:Arial,Helvetica,sans-serif;">{DISCLAIMER}</p>'
    )
    return subject, text_body, html_inner


def send_daily_suggestions_email(db, target_date=None, recipient_ids=None):
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
    manual resend page).

    recipient_ids=None (the normal daily-cron path) sends to every active
    viewer, same as before this param existed. Pass a list/tuple of
    stocks_admin_users.id values to restrict the send to just those
    accounts instead -- the resend page's recipient checklist (deselect
    all, then pick specific people) uses this so a resend doesn't have to
    go to every subscriber every time. An empty list sends to nobody
    (recipient_count=0, sent=0) rather than silently falling back to
    "everyone" -- the caller is expected to stop a genuinely-empty
    selection before calling this (see the resend route's own validation),
    but this function itself never reinterprets "nobody chosen" as "no
    filter"."""
    if target_date is None:
        target_date = date.today()
    elif isinstance(target_date, str):
        target_date = date.fromisoformat(target_date[:10])
    date_iso = target_date.isoformat()
    date_label = target_date.strftime('%d %b %Y')

    suggestions = get_suggestions(db, start_date=date_iso, end_date=date_iso)

    subject, text_body_base, html_inner_base = _build_email_content(suggestions, date_label)

    if recipient_ids is not None:
        recipient_ids = list(recipient_ids)
        if recipient_ids:
            placeholders = ','.join('?' * len(recipient_ids))
            recipients = db.execute(
                f"SELECT id, username AS email, name FROM stocks_admin_users "
                f"WHERE role='viewer' AND is_active=1 AND id IN ({placeholders})",
                tuple(recipient_ids)
            ).fetchall()
        else:
            recipients = []
    else:
        recipients = db.execute(
            "SELECT id, username AS email, name FROM stocks_admin_users WHERE role='viewer' AND is_active=1"
        ).fetchall()

    sent = 0
    failed = 0
    failures = []
    for r in recipients:
        # Personalized per-recipient (see _referral_footer_text/_html) --
        # each person's own link/code, not a shared one -- so the shared
        # body built once above (charts etc. are expensive to recompute)
        # gets its footer appended and only THEN gets wrapped into a full
        # document, per recipient. A code-generation hiccup for one
        # recipient falls back to the plain body rather than skipping
        # their send entirely or crashing the whole batch.
        try:
            referral_code = get_or_create_referral_code(db, r['id'])
        except Exception:
            referral_code = None
        if referral_code:
            text_body = f'{text_body_base}\n\n{_referral_footer_text(referral_code)}\n'
            html_body = _wrap_email_html(html_inner_base + _referral_footer_html(referral_code))
        else:
            text_body = text_body_base
            html_body = _wrap_email_html(html_inner_base)

        ok, detail = send_zeptomail_stocks_email(
            to_email=r['email'],
            to_name=r.get('name') or r['email'],
            subject=subject,
            textbody=text_body,
            htmlbody=html_body,
            sender_name='StoqBell',
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


def _build_starters_email_content(suggestions, week_label):
    """Same rendering as _build_email_content's non-empty branch (stock
    card, horizon disclaimer, DISCLAIMER footer) but Starters-flavored
    copy -- "This Week's Pick" rather than "Today's Recommendation". Only
    ever called with a non-empty suggestions list (see
    send_weekly_starters_email, which sends nothing at all on a week with
    no pick, unlike the daily email's always-send-something behavior --
    so there's no empty-suggestions branch here to mirror)."""
    if len(suggestions) == 1:
        subject = f"StoqBell Starters — This Week's Pick: {_company_display_name(suggestions[0])} ({week_label})"
    else:
        subject = f"StoqBell Starters — {len(suggestions)} Picks for the Week of {week_label}"

    heading = "This Week's Pick" if len(suggestions) == 1 else "This Week's Picks"
    any_horizon_targets = any(
        compute_projection_targets(s.get('buy_price'), s.get('target_sell_price'), s.get('pattern_name'))
        for s in suggestions
    )

    text_lines = [f'{heading} (week of {week_label}):', '']
    for s in suggestions:
        text_lines.append(_render_stock_card_text(s))
        text_lines.append('')
    if any_horizon_targets:
        text_lines.append(HORIZON_DISCLAIMER)
        text_lines.append('')
    text_lines.append(DISCLAIMER)
    text_body = '\n'.join(text_lines)

    horizon_note_html = (
        f'<p style="color:#64748b;font-size:0.8em;line-height:1.5;font-family:Arial,Helvetica,sans-serif;">{HORIZON_DISCLAIMER}</p>'
        if any_horizon_targets else ''
    )
    html_inner = (
        f'<p style="font-family:Arial,Helvetica,sans-serif;font-size:16px;color:#0f172a;">{heading}:</p>'
        + ''.join(_render_stock_card_html(s) for s in suggestions)
        + horizon_note_html
        + f'<p style="color:#64748b;font-size:0.85em;margin-top:16px;font-family:Arial,Helvetica,sans-serif;">{DISCLAIMER}</p>'
    )
    return subject, text_body, html_inner


def send_weekly_starters_email(db, target_date=None, recipient_ids=None):
    """The Starters-tier (Rs 99/mo) equivalent of send_daily_suggestions_email
    -- fetches target_date's week via utils.starters_engine.get_starters_suggestions
    (a genuinely separate, stricter-bar pick, see that module) and emails
    every active stocks_admin_users row with stocks_plan='starters'.

    Diverges from the daily email on purpose: when there's nothing to send
    (no golden-tier candidate cleared the bar that week -- the normal case
    most weeks), this sends NOTHING at all and returns early, rather than
    the daily email's always-send-something "nothing cleared the bar"
    notice. A low-frequency, low-price tier shouldn't get a "sorry,
    nothing this week" email on top of already getting only ~4 a month.

    target_date/recipient_ids have the exact same meaning as
    send_daily_suggestions_email's own params (target_date defaults to
    today -- pass the Monday being generated for; recipient_ids restricts
    to specific accounts, for a future resend feature, same
    empty-list-means-nobody semantics)."""
    if target_date is None:
        target_date = date.today()
    elif isinstance(target_date, str):
        target_date = date.fromisoformat(target_date[:10])
    date_iso = target_date.isoformat()
    date_label = target_date.strftime('%d %b %Y')

    suggestions = get_starters_suggestions(db, start_date=date_iso, end_date=date_iso)
    if not suggestions:
        return {'suggestion_count': 0, 'recipient_count': 0, 'sent': 0, 'failed': 0, 'failures': []}

    subject, text_body_base, html_inner_base = _build_starters_email_content(suggestions, date_label)

    if recipient_ids is not None:
        recipient_ids = list(recipient_ids)
        if recipient_ids:
            placeholders = ','.join('?' * len(recipient_ids))
            recipients = db.execute(
                f"SELECT id, username AS email, name FROM stocks_admin_users "
                f"WHERE role='viewer' AND is_active=1 AND stocks_plan='starters' AND id IN ({placeholders})",
                tuple(recipient_ids)
            ).fetchall()
        else:
            recipients = []
    else:
        recipients = db.execute(
            "SELECT id, username AS email, name FROM stocks_admin_users "
            "WHERE role='viewer' AND is_active=1 AND stocks_plan='starters'"
        ).fetchall()

    sent = 0
    failed = 0
    failures = []
    for r in recipients:
        try:
            referral_code = get_or_create_referral_code(db, r['id'])
        except Exception:
            referral_code = None
        if referral_code:
            text_body = f'{text_body_base}\n\n{_referral_footer_text(referral_code)}\n'
            html_body = _wrap_email_html(html_inner_base + _referral_footer_html(referral_code))
        else:
            text_body = text_body_base
            html_body = _wrap_email_html(html_inner_base)

        ok, detail = send_zeptomail_stocks_email(
            to_email=r['email'],
            to_name=r.get('name') or r['email'],
            subject=subject,
            textbody=text_body,
            htmlbody=html_body,
            sender_name='StoqBell',
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


def _build_large_cap_bonus_email_content(suggestions, date_label):
    """Same rendering as _build_email_content's non-empty branch, with
    copy that makes clear this is a BONUS pick on top of the day's regular
    Pick of the Day -- drawn only from the large-cap (>Rs 30,000cr) tier
    (see utils.large_cap_engine) -- not a replacement for it. Only ever
    called with a non-empty suggestions list (see send_large_cap_bonus_email,
    which sends nothing at all on a run with no qualifying large-cap pick,
    same silent-no-op precedent as the Starters weekly email)."""
    company = _company_display_name(suggestions[0])
    subject = f"StoqBell — Bonus Large-Cap Pick: {company} ({date_label})"

    intro_text = (
        'A bonus pick, exclusive to Standard-plan subscribers, on top of your regular Pick of the Day -- '
        'drawn only from large, established companies (market cap above Rs 30,000 crore).'
    )
    any_horizon_targets = any(
        compute_projection_targets(s.get('buy_price'), s.get('target_sell_price'), s.get('pattern_name'))
        for s in suggestions
    )

    text_lines = [f'Bonus Large-Cap Pick ({date_label}):', intro_text, '']
    for s in suggestions:
        text_lines.append(_render_stock_card_text(s))
        text_lines.append('')
    if any_horizon_targets:
        text_lines.append(HORIZON_DISCLAIMER)
        text_lines.append('')
    text_lines.append(DISCLAIMER)
    text_body = '\n'.join(text_lines)

    horizon_note_html = (
        f'<p style="color:#64748b;font-size:0.8em;line-height:1.5;font-family:Arial,Helvetica,sans-serif;">{HORIZON_DISCLAIMER}</p>'
        if any_horizon_targets else ''
    )
    html_inner = (
        f'<p style="font-family:Arial,Helvetica,sans-serif;font-size:16px;color:#0f172a;">Bonus Large-Cap Pick:</p>'
        f'<p style="font-family:Arial,Helvetica,sans-serif;font-size:13px;color:#64748b;margin-top:-6px;">{intro_text}</p>'
        + ''.join(_render_stock_card_html(s) for s in suggestions)
        + horizon_note_html
        + f'<p style="color:#64748b;font-size:0.85em;margin-top:16px;font-family:Arial,Helvetica,sans-serif;">{DISCLAIMER}</p>'
    )
    return subject, text_body, html_inner


def send_large_cap_bonus_email(db, target_date=None, recipient_ids=None):
    """The Standard-tier (Rs 299/mo) bonus large-cap pick email -- fetches
    target_date's pick via utils.large_cap_engine.get_large_cap_bonus_suggestions
    and emails every active stocks_admin_users row with
    stocks_plan='standard'. Sent twice a week (see app.py's
    /stocks/large-cap-bonus/send-email), IN ADDITION TO the regular daily
    Pick of the Day -- never replaces it.

    Diverges from the daily email on purpose, same as the Starters weekly
    email: when there's nothing to send (no large-cap candidate cleared
    the bar this run), this sends NOTHING at all rather than the daily
    email's always-send-something "nothing cleared the bar" notice -- a
    bonus extra shouldn't arrive as a "sorry, nothing" email.

    target_date/recipient_ids have the exact same meaning as
    send_daily_suggestions_email's own params."""
    if target_date is None:
        target_date = date.today()
    elif isinstance(target_date, str):
        target_date = date.fromisoformat(target_date[:10])
    date_iso = target_date.isoformat()
    date_label = target_date.strftime('%d %b %Y')

    suggestions = get_large_cap_bonus_suggestions(db, start_date=date_iso, end_date=date_iso)
    if not suggestions:
        return {'suggestion_count': 0, 'recipient_count': 0, 'sent': 0, 'failed': 0, 'failures': []}

    subject, text_body_base, html_inner_base = _build_large_cap_bonus_email_content(suggestions, date_label)

    if recipient_ids is not None:
        recipient_ids = list(recipient_ids)
        if recipient_ids:
            placeholders = ','.join('?' * len(recipient_ids))
            recipients = db.execute(
                f"SELECT id, username AS email, name FROM stocks_admin_users "
                f"WHERE role='viewer' AND is_active=1 AND stocks_plan='standard' AND id IN ({placeholders})",
                tuple(recipient_ids)
            ).fetchall()
        else:
            recipients = []
    else:
        recipients = db.execute(
            "SELECT id, username AS email, name FROM stocks_admin_users "
            "WHERE role='viewer' AND is_active=1 AND stocks_plan='standard'"
        ).fetchall()

    sent = 0
    failed = 0
    failures = []
    for r in recipients:
        try:
            referral_code = get_or_create_referral_code(db, r['id'])
        except Exception:
            referral_code = None
        if referral_code:
            text_body = f'{text_body_base}\n\n{_referral_footer_text(referral_code)}\n'
            html_body = _wrap_email_html(html_inner_base + _referral_footer_html(referral_code))
        else:
            text_body = text_body_base
            html_body = _wrap_email_html(html_inner_base)

        ok, detail = send_zeptomail_stocks_email(
            to_email=r['email'],
            to_name=r.get('name') or r['email'],
            subject=subject,
            textbody=text_body,
            htmlbody=html_body,
            sender_name='StoqBell',
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
