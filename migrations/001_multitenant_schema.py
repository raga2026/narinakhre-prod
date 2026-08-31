"""
Migration 001 -- multi-tenant SaaS data model (Phase 1, structure only).

WHAT THIS DOES
  1. Creates 6 new tables:  tenants, platform_admins, tenant_admins,
     subscriptions, addons, tenant_addons  (plus their indexes, two guard
     indexes, and one partial-unique index on tenant_admins.google_sub).
  2. Adds a NULLABLE `tenant_id uuid` column (FK -> tenants.id, ON DELETE
     RESTRICT) plus a btree index to each of the 17 existing storefront
     tables listed in TENANT_SCOPED_TABLES.

WHAT THIS DOES NOT DO
  - No data is written or changed. No row gets a tenant_id value here.
    `tenant_id` stays NULL on every existing row. The backfill is a
    separate migration (002), run later, only after this one is approved.
  - No DEFAULT is put on any tenant_id column.
  - No NOT NULL is applied to any tenant_id column.
  - Nothing in app.py / routes / templates / models / queries is touched.
    This file is the only thing that changes.
  - `tenant_admins` is DATA-MODEL ONLY. No login route, no OAuth callback,
    no session logic, no domain.com/admin routing is added here. The
    env-var admin login (ADMIN_USERNAME/PASSWORD/TOTP_SECRET) stays exactly
    as-is and untouched. Wiring the table into an actual login flow is
    Phase 2 work that touches request routing, which this phase avoids.
  - The StoqBell / stocks_* tables are deliberately untouched.

SAFETY / IDEMPOTENCY
  - Every statement is IF [NOT] EXISTS, so a re-run is a no-op and a
    partially-applied run can simply be re-run or reversed.
  - Each statement is sent to Supabase's execute_sql RPC on its own, and
    each RPC call is its own autocommit transaction (the RPC cannot do
    BEGIN/COMMIT). So if statement N fails, statements 1..N-1 are already
    committed -- re-run to finish, or run --down to roll everything back.
  - The down migration (--down) drops all 17 tenant_id columns (which also
    drops their FKs and indexes) and then drops the 6 new tables in
    reverse dependency order. It restores the schema to exactly its
    pre-migration shape.

SHARED SUPABASE WARNING
  This repo has ONE Supabase project for local, test and production
  (PostgreSQL 17). Running this with --apply from any machine hits that
  same live database. There is no separate local/staging Postgres unless
  you stand one up yourself. Do NOT --apply against the shared project
  until Raghavendran has explicitly approved it in writing.

USAGE
  python migrations/001_multitenant_schema.py            # dry run (prints SQL, executes nothing)
  python migrations/001_multitenant_schema.py --apply    # run the UP migration
  python migrations/001_multitenant_schema.py --down     # dry run of the DOWN migration
  python migrations/001_multitenant_schema.py --down --apply   # run the DOWN migration
"""
import argparse
import os
import sys

from dotenv import load_dotenv

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)
load_dotenv(os.path.join(_REPO_ROOT, ".env"))


# --------------------------------------------------------------------------
# New tables (created in this order: tenants first, addons before
# tenant_addons, tenants before everything that references it).
# --------------------------------------------------------------------------
NEW_TABLES = [
    # tenants -- one row per storefront business. narinakhre.com/<slug> is
    # that tenant's storefront. custom_domain is reserved for a future
    # add-on and unused today. status is the admin kill switch, separate
    # from billing.
    """
    CREATE TABLE IF NOT EXISTS tenants (
        id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        name          text NOT NULL,
        slug          text NOT NULL UNIQUE
                          CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
        custom_domain text UNIQUE,
        theme         text NOT NULL DEFAULT 'default',
        status        text NOT NULL DEFAULT 'active'
                          CHECK (status IN ('active', 'suspended')),
        created_at    timestamptz NOT NULL DEFAULT now()
    )
    """,
    # platform_admins -- SaaS operator logins. Shares no table, session, or
    # login route with any tenant's admin auth (which today is env-vars
    # only) or with the storefront `users` table. password_hash is
    # nullable so the seed row can exist before a password is set.
    """
    CREATE TABLE IF NOT EXISTS platform_admins (
        id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        email         text NOT NULL UNIQUE,
        password_hash text,
        created_at    timestamptz NOT NULL DEFAULT now(),
        last_login    timestamptz
    )
    """,
    # tenant_admins -- per-tenant admin accounts. Replaces the current
    # env-var admin login (ADMIN_USERNAME/PASSWORD/TOTP_SECRET) once Phase 2
    # wires up the login flow; this phase only creates the table. A given
    # email or Google account may administer several tenants, one row per
    # tenant. `role` is intentionally unconstrained (default 'owner') so
    # future per-tenant roles need no schema change. google_sub, the two
    # secret columns, and last_login are nullable.
    #   UNIQUE (tenant_id, email)                          -- inline below
    #   UNIQUE (tenant_id, google_sub) WHERE NOT NULL      -- see NEW_INDEXES
    """
    CREATE TABLE IF NOT EXISTS tenant_admins (
        id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id     uuid NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
        email         text NOT NULL,
        google_sub    text,
        password_hash text,
        totp_secret   text,
        role          text NOT NULL DEFAULT 'owner',
        status        text NOT NULL DEFAULT 'active'
                          CHECK (status IN ('active', 'disabled')),
        created_at    timestamptz NOT NULL DEFAULT now(),
        last_login    timestamptz,
        CONSTRAINT uq_tenant_admins_tenant_email UNIQUE (tenant_id, email)
    )
    """,
    # subscriptions -- what a tenant owes the PLATFORM for its base plan.
    # Separate from the tenant's own linked Razorpay (their customers'
    # payments).
    """
    CREATE TABLE IF NOT EXISTS subscriptions (
        id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id          uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        plan               text NOT NULL,
        status             text NOT NULL
                               CHECK (status IN ('trialing', 'active', 'past_due', 'cancelled')),
        current_period_end timestamptz,
        created_at         timestamptz NOT NULL DEFAULT now()
    )
    """,
    # addons -- global priced catalogue of optional features. NOT
    # tenant-scoped. Stays empty this phase. setup_fee / recurring_fee are
    # both nullable (an add-on can have one, both, or neither).
    """
    CREATE TABLE IF NOT EXISTS addons (
        id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        key           text NOT NULL UNIQUE,
        name          text NOT NULL,
        description   text,
        setup_fee     numeric(10, 2),
        recurring_fee numeric(10, 2),
        is_active     boolean NOT NULL DEFAULT true,
        created_at    timestamptz NOT NULL DEFAULT now()
    )
    """,
    # tenant_addons -- which add-ons a tenant has actually bought and
    # whether they are enabled. Stays empty this phase.
    """
    CREATE TABLE IF NOT EXISTS tenant_addons (
        id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id    uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        addon_id     uuid NOT NULL REFERENCES addons(id) ON DELETE RESTRICT,
        status       text NOT NULL
                         CHECK (status IN ('pending_setup', 'active', 'cancelled')),
        purchased_at timestamptz NOT NULL DEFAULT now(),
        activated_at timestamptz
    )
    """,
]

NEW_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_tenant_admins_tenant_id ON tenant_admins (tenant_id)",
    "CREATE INDEX IF NOT EXISTS idx_subscriptions_tenant_id ON subscriptions (tenant_id)",
    "CREATE INDEX IF NOT EXISTS idx_tenant_addons_tenant_id ON tenant_addons (tenant_id)",
    "CREATE INDEX IF NOT EXISTS idx_tenant_addons_addon_id ON tenant_addons (addon_id)",
    # Guard 1: at most one non-cancelled subscription per tenant.
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_subscriptions_active_per_tenant "
    "ON subscriptions (tenant_id) WHERE status <> 'cancelled'",
    # Guard 2: at most one non-cancelled tenant_addons row per tenant+addon.
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_tenant_addons_active_per_pair "
    "ON tenant_addons (tenant_id, addon_id) WHERE status <> 'cancelled'",
    # tenant_admins: same Google account may administer multiple tenants
    # (one row each), but not twice within one tenant. Partial because
    # google_sub is nullable. The (tenant_id, email) pair is enforced by
    # the inline UNIQUE constraint in the CREATE TABLE above.
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_tenant_admins_tenant_google_sub "
    "ON tenant_admins (tenant_id, google_sub) WHERE google_sub IS NOT NULL",
]

# --------------------------------------------------------------------------
# The 17 existing storefront tables that get a nullable tenant_id column.
# Group A from the inventory. The StoqBell / stocks_* tables are NOT here
# on purpose.
# --------------------------------------------------------------------------
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


def up_statements():
    stmts = [s.strip() for s in NEW_TABLES]
    stmts += NEW_INDEXES
    for t in TENANT_SCOPED_TABLES:
        stmts.append(
            "ALTER TABLE {t} ADD COLUMN IF NOT EXISTS tenant_id uuid "
            "REFERENCES tenants(id) ON DELETE RESTRICT".format(t=t)
        )
        stmts.append(
            "CREATE INDEX IF NOT EXISTS idx_{t}_tenant_id ON {t} (tenant_id)".format(t=t)
        )
    return stmts


def down_statements():
    stmts = []
    # Dropping the column also drops its FK constraint and its index.
    for t in TENANT_SCOPED_TABLES:
        stmts.append("ALTER TABLE {t} DROP COLUMN IF EXISTS tenant_id".format(t=t))
    # Reverse dependency order. Guard/FK indexes drop with their tables.
    for tbl in [
        "tenant_addons",
        "addons",
        "subscriptions",
        "tenant_admins",
        "platform_admins",
        "tenants",
    ]:
        stmts.append("DROP TABLE IF EXISTS {tbl}".format(tbl=tbl))
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
                "Fix the cause and re-run (idempotent), or run --down to roll back."
            )
            sys.exit(1)
    if apply:
        print("\nDone.")
    else:
        print("\nDry run only. Re-run with --apply to execute.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--down",
        action="store_true",
        help="use the reverse (drop) statements instead of the forward ones",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="actually execute against Supabase (without this it is a dry run)",
    )
    args = parser.parse_args()
    stmts = down_statements() if args.down else up_statements()
    run(stmts, args.apply)


if __name__ == "__main__":
    main()
