"""Shared Razorpay client -- the same Razorpay account/keys are used by
both the storefront's one-time-order checkout (Orders API, app.py) and
Nari Nakhre Stocks' subscription checkout (Subscriptions API, stoqbell/),
so this lives at the repo root rather than under either side.

Lazily constructed (like db.get_supabase()) so import order doesn't
matter relative to app.py's .env loader (load_env_file), which runs after
app.py's own top-of-file imports.
"""
import os

import razorpay

# RAZORPAY_KEY_ID (public half) -- safe to expose client-side, it's what
# Razorpay's own Checkout.js widget needs embedded in the page to open the
# payment modal. Read here (not just inside get_razorpay_client()) so
# stoqbell/routes.py can pass it straight into its checkout template
# context, the same way app.py's own storefront checkout template already
# does with its own app.config['RAZORPAY_KEY_ID'].
RAZORPAY_KEY_ID = os.environ.get('RAZORPAY_KEY_ID', '')
# RAZORPAY_KEY_SECRET (private half) -- also exposed as a constant, not just
# used inside get_razorpay_client(), since Stocks' own subscription-payment
# signature verification (utils/stocks_subscription.verify_subscription_payment_signature)
# needs the raw secret directly for an HMAC check, not a razorpay.Client
# instance.
RAZORPAY_KEY_SECRET = os.environ.get('RAZORPAY_KEY_SECRET', '')

_client = None


def get_razorpay_client():
    global _client
    if _client is None:
        _client = razorpay.Client(
            auth=(os.environ.get('RAZORPAY_KEY_ID'), os.environ.get('RAZORPAY_KEY_SECRET'))
        )
    return _client
