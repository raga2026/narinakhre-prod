"""Shared Google reCAPTCHA v3 verification -- used by both the storefront's
contact forms (app.py) and Stocks' signup/login forms (stoqbell/routes.py).
Generic, no coupling to either side's business logic, so it lives at the
repo root like db.py/razorpay_shared.py/utils/credential_crypto.py.

Two SEPARATE key pairs, not one shared pair -- reCAPTCHA site keys are
registered against specific domains in Google's admin console, and the
storefront (narinakhre.com) and Stocks (stoqbell.com) are different
domains. Reusing one key pair for both would mean whichever domain wasn't
registered for it gets every submission silently rejected ("Please try
again", regardless of correct credentials) -- confirmed live 2026-08-21 on
stocks login before this split existed.
"""
import os

import requests
from flask import current_app

# Storefront's own pair (narinakhre.com contact forms). If unset, the check
# is skipped entirely (so forms still work before you've generated keys)
# and a one-time warning is logged.
RECAPTCHA_SITE_KEY = os.environ.get('RECAPTCHA_SITE_KEY', '')
RECAPTCHA_SECRET_KEY = os.environ.get('RECAPTCHA_SECRET_KEY', '')
# Stocks' own pair (stoqbell.com signup/login) -- a separate reCAPTCHA site
# registered for stoqbell.com/www.stoqbell.com, not the storefront's.
STOCKS_RECAPTCHA_SITE_KEY = os.environ.get('STOCKS_RECAPTCHA_SITE_KEY', '')
STOCKS_RECAPTCHA_SECRET_KEY = os.environ.get('STOCKS_RECAPTCHA_SECRET_KEY', '')

RECAPTCHA_MIN_SCORE = 0.5
_recaptcha_unconfigured_warned = False


def verify_recaptcha(token, remote_ip=None, expected_action=None, secret_key=None):
    """Returns True if the submission should be allowed through.
    Fails OPEN (allows the submission) if reCAPTCHA isn't configured yet, or if
    Google's API can't be reached — the timing/IP/honeypot checks still apply
    either way, so this is a defense layer, not the only one.

    secret_key defaults to the storefront's own RECAPTCHA_SECRET_KEY (every
    existing app.py call site keeps working unchanged) -- pass
    STOCKS_RECAPTCHA_SECRET_KEY explicitly for a Stocks form, since that's a
    different registered reCAPTCHA site than the storefront's."""
    if secret_key is None:
        secret_key = RECAPTCHA_SECRET_KEY

    global _recaptcha_unconfigured_warned
    if not secret_key:
        if not _recaptcha_unconfigured_warned:
            current_app.logger.warning('RECAPTCHA secret key not set — skipping reCAPTCHA checks')
            _recaptcha_unconfigured_warned = True
        return True

    if not token:
        current_app.logger.warning('reCAPTCHA rejected: no token submitted')
        return False

    try:
        resp = requests.post(
            'https://www.google.com/recaptcha/api/siteverify',
            data={'secret': secret_key, 'response': token, 'remoteip': remote_ip},
            timeout=5,
        )
        result = resp.json()
    except Exception as e:
        current_app.logger.error(f'reCAPTCHA verify request failed, allowing through: {type(e).__name__}: {e}')
        return True

    if not result.get('success'):
        current_app.logger.warning(f'reCAPTCHA rejected: {result.get("error-codes")}')
        return False
    if expected_action and result.get('action') != expected_action:
        current_app.logger.warning(f'reCAPTCHA action mismatch: expected {expected_action}, got {result.get("action")}')
        return False
    score = result.get('score', 0)
    if score < RECAPTCHA_MIN_SCORE:
        current_app.logger.warning(f'reCAPTCHA score too low: {score}')
        return False
    return True
