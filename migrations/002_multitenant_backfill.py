"""
Migration 002 -- multi-tenant SaaS backfill (Phase 1, data only).

RUN THIS ONLY AFTER 001 HAS BEEN APPLIED.

WHAT THIS DOES
  1. Inserts exactly ONE row into `tenants` for the current live business:
     name 'Nari Nakhre', slug 'narinakhre', custom_domain 'narinakhre.com',
     theme 'default', status 'active'.
  2. Inserts ONE row into `subscriptions` for that tenant: plan 'legacy',
     status 'active', current_period_end far in the future (2099-12-31),
     so the live business is never treated as unpaid by later checks.
  3. Sets `tenant_id` = that tenant's id on EVERY existing row of the 17
     storefront tables (only where it is currently NULL).

WHAT THIS DOES NOT DO
  - Does NOT seed `platform_admins`. Original item 4 asked for a seed row
    (email from ADMIN_EMAIL, no password). That is deferred here because
    (a) your later step-4 checklist says platform_admins must be empty,
    and (b) ADMIN_EMAIL is not set in .env, and you asked to fail loudly
    rather than fall back to a default. Handle platform_admins as its own
    explicit step once ADMIN_EMAIL is set or you give the address.
  - Leaves `addons`, `tenant_addons`, `tenant_admins` empty.
  - Applies no NOT NULL constraint. That is a later step; item 5 only asks
    whether it is SAFE to do (this migration is what makes it safe).
  - Touches nothing in app.py / routes / templates / models / queries.
  - Touches none of the StoqBell / stocks_* tables.

VALUES YOU MAY WANT TO CHANGE (say so before this is applied)
  - tenants.name  = 'Nari Nakhre'
  - subscriptions.plan = 'legacy'   (no real plan catalogue exists yet)
  - subscriptions.current_period_end = 2099-12-31

SAFETY / IDEMPOTENCY
  - The tenant insert is guarded by NOT EXISTS on slug.
  - The subscription insert is guarded by NOT EXISTS on a non-cancelled
    subscription for that tenant (matches uq_subscriptions_active_per_tenant).
  - Each of the 17 updates is `WHERE tenant_id IS NULL`, so a re-run is a
    no-op and a partial run just needs re-running.
  - Each statement is its own autocommit transaction via the execute_sql
    RPC. A mid-run failure is safe to re-run or reverse.
  - `--down` nulls the 17 tenant_id columns back out for this tenant,
    deletes the subscription (and any tenant_admins / tenant_addons rows
    for it), then deletes the tenant row -- restoring the post-001 state.

USAGE
  python migrations/002_multitenant_backfill.py            # dry run, prints SQL
  python migrations/002_multitenant_backfill.py --apply    # run the backfill
  python migrations/002_multitenant_backfill.py --down     # dry run of the undo
  python migrations/002_multitenant_backfill.py --down --apply   # run the undo
"""
import argparse
import os
import sys

from dotenv import load_dotenv

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)
load_dotenv(os.path.join(_REPO_ROOT, ".env"))

# Same 17 tables as migration 001. Kept in sync by hand.
TENANT_SCOPED_TABLES = [
    "products",
    "categories",
    "product_variants",
    "coupons",
    "quotes",
    "order_shipping",
    "users",
    "user_addresses",
    "credit_transactions",
    "email_campaigns",
    "email_campaign_recipients",
    "admin_events",
    "page_views",
    "product_events",
    "delivery_partners",
    "delivery_partner_credentials",
    "site_settings",
]

_TENANT_ID = "(SELECT id FROM tenants WHERE slug = 'narinakhre')"

TENANT_INSERT = """
INSERT INTO tenants (name, slug, custom_domain, theme, status)
SELECT 'Nari Nakhre', 'narinakhre', 'narinakhre.com', 'default', 'active'
WHERE NOT EXISTS (SELECT 1 FROM tenants WHERE slug = 'narinakhre')
""".strip()

SUBSCRIPTION_INSERT = """
INSERT INTO subscriptions (tenant_id, plan, status, current_period_end)
SELECT t.id, 'legacy', 'active', TIMESTAMPTZ '2099-12-31 00:00:00+00'
FROM tenants t
WHERE t.slug = 'narinakhre'
  AND NOT EXISTS (
      SELECT 1 FROM subscriptions s
      WHERE s.tenant_id = t.id AND s.status <> 'cancelled'
  )
""".strip()


def up_statements():
    stmts = [TENANT_INSERT, SUBSCRIPTION_INSERT]
    for t in TENANT_SCOPED_TABLES:
        stmts.append(
            "UPDATE {t} SET tenant_id = {tid} WHERE tenant_id IS NULL".format(
                t=t, tid=_TENANT_ID
            )
        )
    return stmts


def down_statements():
    stmts = []
    for t in TENANT_SCOPED_TABLES:
        stmts.append(
            "UPDATE {t} SET tenant_id = NULL WHERE tenant_id = {tid}".format(
                t=t, tid=_TENANT_ID
            )
        )
    stmts.append("DELETE FROM tenant_addons WHERE tenant_id = {tid}".format(tid=_TENANT_ID))
    stmts.append("DELETE FROM tenant_admins WHERE tenant_id = {tid}".format(tid=_TENANT_ID))
    stmts.append("DELETE FROM subscriptions WHERE tenant_id = {tid}".format(tid=_TENANT_ID))
    stmts.append("DELETE FROM tenants WHERE slug = 'narinakhre'")
    return stmts


def _client():
    from db import get_supabase  # shared Supabase plumbing, zero business logic

    return get_supabase()


def run(stmts, apply):
    target = os.environ.get("SUPABASE_URL", "<SUPABASE_URL unset>")
    mode = "APPLY (live execute)" if apply else "DRY RUN (nothing executed)"
    print("Target Supabase: {}".format(target))
    print("Mode: {}".format(mode))
    print("{} statement(s):\n".format(len(stmts)))
    for i, stmt in enumerate(stmts, 1):
        one_line = " ".join(stmt.split())
        print("[{}/{}] {}".format(i, len(stmts), one_line))
        if not apply:
            continue
        try:
            _client().rpc("execute_sql", {"query": stmt}).execute()
        except Exception as e:  # noqa: BLE001 -- want a loud abort on any failure
            print("\n*** FAILED on statement {} ***\n{}\n\nError: {}".format(i, stmt, e))
            print(
                "\nEarlier statements in this run are already committed. "
                "Re-run (idempotent) to finish, or run --down to roll back."
            )
            sys.exit(1)
    print("\nDone." if apply else "\nDry run only. Re-run with --apply to execute.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--down", action="store_true", help="use the undo statements")
    parser.add_argument(
        "--apply", action="store_true", help="actually execute against Supabase"
    )
    args = parser.parse_args()
    stmts = down_statements() if args.down else up_statements()
    run(stmts, args.apply)


if __name__ == "__main__":
    main()
