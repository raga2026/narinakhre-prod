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

_client = None


def get_razorpay_client():
    global _client
    if _client is None:
        _client = razorpay.Client(
            auth=(os.environ.get('RAZORPAY_KEY_ID'), os.environ.get('RAZORPAY_KEY_SECRET'))
        )
    return _client
