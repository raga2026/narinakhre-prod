"""One-time script to create GST-inclusive Razorpay Plan objects for
StoqBell's three subscription tiers (Standard Pro, Starters, referral/
coupon first-month price), so GST is actually included in what customers
are charged -- the existing RAZORPAY_STOCKS_PLAN_ID/
RAZORPAY_STOCKS_STARTERS_PLAN_ID/RAZORPAY_STOCKS_REFERRAL_PLAN_ID plans
have no GST added at all.

Investment advisory / stock research subscription services fall under GST
SAC code 9971 ("Financial and related services"), taxed at 18% -- the same
rate SEBI-registered Investment Advisers and stock-broking/research
services charge on their fees. Confirm this with your CA before relying on
it for filing; this script doesn't verify current GST law, it just applies
the rate given below.

Razorpay Plan objects are immutable once created -- there is no "edit the
amount" API. This creates three NEW plans rather than editing the existing
ones, which is deliberate: a Razorpay subscription references the plan_id
it was created against, not a live lookup, so any customer already
subscribed stays on their existing plan/price untouched -- swapping the
env vars below to the new plan ids only changes the price for NEW signups
going forward, never an existing paying customer's billing without them
re-subscribing. See routes.py's own RAZORPAY_STOCKS_PLAN_ID comment for
why this is a manual one-time script rather than something run
automatically on every app start (no natural idempotency check -- running
this twice creates a second, duplicate set of plans, it does not update
the first).

Run manually, once:

    python create_gst_inclusive_stocks_plans.py

Then set RAZORPAY_STOCKS_PLAN_ID / RAZORPAY_STOCKS_STARTERS_PLAN_ID /
RAZORPAY_STOCKS_REFERRAL_PLAN_ID on Render (both narinakhre-production and
narinakhre-test, if Stocks checkout is ever exercised there too) to the
three plan ids this prints -- set directly in the Render dashboard, never
committed to git, same as every other Razorpay/Supabase credential in this
project.

Requires RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET in the environment (.env
is loaded automatically, same as the rest of this project) -- the SAME
Razorpay account/keys the rest of the app already uses (see
razorpay_shared.py), so no separate credentials to set up first.
"""
from dotenv import load_dotenv

from razorpay_shared import get_razorpay_client

load_dotenv()

# SAC code 9971 (financial/investment-advisory services) -- see this
# script's own module docstring.
GST_RATE_PCT = 18

# (env var name this plan's id should be set to, display name, base rupees
# BEFORE GST -- the Rs 299/Rs 99/Rs 199 prices already shown throughout
# the app, description).
PLANS = [
    ('RAZORPAY_STOCKS_PLAN_ID', 'StoqBell Standard Pro', 299,
     'StoqBell Standard Pro monthly subscription'),
    ('RAZORPAY_STOCKS_STARTERS_PLAN_ID', 'StoqBell Starters', 99,
     'StoqBell Starters monthly subscription'),
    ('RAZORPAY_STOCKS_REFERRAL_PLAN_ID', 'StoqBell Standard Pro (referral price)', 199,
     "StoqBell Standard Pro, first month at a referred subscriber's discounted price"),
]


def run():
    client = get_razorpay_client()
    for env_var_name, name, base_rupees, description in PLANS:
        gst_inclusive_rupees = round(base_rupees * (1 + GST_RATE_PCT / 100), 2)
        amount_paise = round(gst_inclusive_rupees * 100)
        plan = client.plan.create({
            'period': 'monthly',
            'interval': 1,
            'item': {
                'name': name,
                'amount': amount_paise,
                'currency': 'INR',
                'description': f'{description} -- Rs {base_rupees} + {GST_RATE_PCT}% GST = Rs {gst_inclusive_rupees}',
            },
            'notes': {'base_amount_inr': str(base_rupees), 'gst_rate_pct': str(GST_RATE_PCT)},
        })
        print(
            f"OK   {name}: Rs {base_rupees} + {GST_RATE_PCT}% GST = Rs {gst_inclusive_rupees}/month "
            f"-> plan_id={plan['id']}  (set {env_var_name} to this on Render)"
        )


if __name__ == '__main__':
    run()
