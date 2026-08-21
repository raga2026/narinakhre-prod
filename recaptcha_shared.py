"""Shared Google reCAPTCHA v3 verification -- used by both the storefront's
contact forms (app.py) and Stocks' signup/login forms (stoqbell/routes.py).
Generic, no coupling to either side's business logic, so it lives at the
repo root like db.py/razorpay_shared.py/utils/credential_crypto.py.
"""
import os

import requests
from flask import current_app

# If unset, the check is skipped entirely (so forms still work before
# you've generated keys) and a one-time warning is logged.
RECAPTCHA_SITE_KEY = os.environ.get('RECAPTCHA_SITE_KEY', '')
RECAPTCHA_SECRET_KEY = os.environ.get('RECAPTCHA_SECRET_KEY', '')
RECAPTCHA_MIN_SCORE = 0.5
_recaptcha_unconfigured_warned = False


def verify_recaptcha(token, remote_ip=None, expected_action=None):
    """Returns True if the submission should be allowed through.
    Fails OPEN (allows the submission) if reCAPTCHA isn't configured yet, or if
    Google's API can't be reached — the timing/IP/honeypot checks still apply
    either way, so this is a defense layer, not the only one."""
    global _recaptcha_unconfigured_warned
    if not RECAPTCHA_SECRET_KEY:
        if not _recaptcha_unconfigured_warned:
            current_app.logger.warning('RECAPTCHA_SECRET_KEY not set — skipping reCAPTCHA checks on contact forms')
            _recaptcha_unconfigured_warned = True
        return True

    if not token:
        current_app.logger.warning('reCAPTCHA rejected: no token submitted')
        return False

    try:
        resp = requests.post(
            'https://www.google.com/recaptcha/api/siteverify',
            data={'secret': RECAPTCHA_SECRET_KEY, 'response': token, 'remoteip': remote_ip},
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
