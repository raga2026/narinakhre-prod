# migrations/

Numbered, hand-run schema migrations for the multi-tenant SaaS work.

Each file is a standalone script. It talks to Supabase through the same
`execute_sql` RPC the app uses (via `db.get_supabase()`), one statement at
a time. Nothing here runs automatically on app boot.

## Rules

- Every statement is `IF [NOT] EXISTS`, so a re-run is a no-op and a
  partial run is safe to re-run or reverse.
- Each RPC call is its own autocommit transaction. A mid-run failure
  leaves earlier statements committed. Fix and re-run, or run `--down`.
- Structure migrations never write or change row data. Data changes live
  in their own separate backfill migration.

## Shared database

Local, test and production all point at ONE Supabase project
(PostgreSQL 17). `--apply` from any machine hits that live database.
There is no separate staging Postgres unless you start one yourself.

## Usage

```
python migrations/001_multitenant_schema.py            # dry run, prints SQL, executes nothing
python migrations/001_multitenant_schema.py --apply    # run the forward migration
python migrations/001_multitenant_schema.py --down     # dry run of the rollback
python migrations/001_multitenant_schema.py --down --apply   # run the rollback
```

## Files

- `001_multitenant_schema.py` — creates `tenants`, `platform_admins`,
  `tenant_admins`, `subscriptions`, `addons`, `tenant_addons`; adds a
  nullable `tenant_id uuid` column + index to the 17 existing storefront
  tables. No backfill. No login/routing code — `tenant_admins` is
  data-model only. Fully reversible with `--down`.
