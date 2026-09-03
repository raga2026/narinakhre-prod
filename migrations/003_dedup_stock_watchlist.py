"""
Migration 003 -- collapse the duplicate NSE/BSE stock_watchlist rows.

WHY
  Every company sits in stock_watchlist twice: once for its NSE listing,
  once for its BSE listing, each its own row with its own id. Only one is
  is_active=1 at a time, and run_fundamental_shortlist._pick_canonical_listing
  can flip which one that is between refreshes. Everything downstream
  (stock_suggestions, stock_daily_data, stock_indicators, the 15-day repeat
  cooldown, the recommendation tracker, target-hit checks, the auto-trader)
  joins by watchlist_id, so a flip orphans a company's whole history on the
  row that just went inactive. Live symptom Sep 2026: Jayaswal Neco,
  Kajaria, Kingfa, Supreme Petrochem re-recommended days after their last
  send because the cooldown looked at the wrong row.

  Migration 5e7de48 made the daily-engine cooldown match by ISIN as a
  runtime backstop. This migration fixes the data itself so every other
  by-watchlist_id join is correct too, and so the pipeline stops
  accumulating orphans.

WHAT IT DOES  (per ISIN that has >1 watchlist row -- 50 such groups as of
2026-09-03)
  1. Pick the CANONICAL row: the is_active=1 one; if none or more than one
     is active, prefer the NSE listing; tie-break on lowest id.
  2. For each non-canonical ("loser") row and each of the 9 tables that
     carry watchlist_id -- stock_suggestions, stock_daily_data,
     stock_indicators, stock_fundamentals, stock_news, stock_admin_alerts,
     stock_auto_trades, stock_starters_suggestions,
     stock_large_cap_bonus_suggestions:
       a. repoint the loser's child rows to the canonical id, BUT only
          where the canonical row doesn't already have a row for that same
          (watchlist_id, <date/link>) unique key -- so history the
          canonical row is missing is preserved;
       b. delete whatever loser child rows are left (a real collision on a
          date the canonical already covers -- keep the canonical's copy,
          the two listings' daily price/indicator values for one date are
          effectively the same company).
     stock_auto_trades has no such unique key, so its rows are repointed
     unconditionally.
  3. Delete the loser stock_watchlist row.

REVERSIBILITY
  One-way. Repointed child rows no longer record which listing they came
  from, and collision-losers are deleted. There is NO down migration. A
  full backup MUST exist before --apply is run. Do not run --apply until
  Raghavendran has confirmed a backup and explicitly approved it.

SHARED SUPABASE
  local / test / production are one project. --apply from anywhere hits
  production.

USAGE
  python migrations/003_dedup_stock_watchlist.py           # dry run: prints the full plan, changes nothing
  python migrations/003_dedup_stock_watchlist.py --apply   # execute
"""
import argparse
import json
import os
import sys

from dotenv import load_dotenv

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)
load_dotenv(os.path.join(_REPO_ROOT, ".env"))

# table -> the column that, together with watchlist_id, forms its UNIQUE
# key (None = no such key; repoint unconditionally).
CHILD_TABLES = {
    "stock_suggestions": "suggestion_date",
    "stock_daily_data": "trade_date",
    "stock_indicators": "calc_date",
    "stock_fundamentals": "snapshot_date",
    "stock_news": "link",
    "stock_admin_alerts": "alert_date",
    "stock_starters_suggestions": "week_start_date",
    "stock_large_cap_bonus_suggestions": "suggestion_date",
    "stock_auto_trades": None,
}


def _client():
    from db import get_supabase

    return get_supabase()


def _q(sql):
    # execute_sql's own trim() only strips spaces, not newlines -- a query
    # with a leading newline fails its `ILIKE 'SELECT%'` check and is run
    # as a statement (results discarded, returns '[]'). Strip here.
    res = _client().rpc("execute_sql", {"query": sql.strip()}).execute()
    raw = res.data
    if isinstance(raw, list) and raw and isinstance(raw[0], dict) and "execute_sql" in raw[0]:
        inner = raw[0]["execute_sql"]
        return json.loads(inner) if isinstance(inner, str) else (inner or [])
    return raw or []


def _exec(sql):
    _client().rpc("execute_sql", {"query": sql.strip()}).execute()


def build_plan():
    """Returns [{isin, name, canonical_id, canonical_desc, losers:[{id,desc}]}].

    Grouped and ranked in Python -- the ranking (active first, then NSE,
    then lowest id) is trivial here and keeps the SQL a plain flat SELECT.
    """
    rows = _q(
        """
        SELECT w.id, w.name, w.exchange, w.is_active, u.isin
        FROM stock_watchlist w
        JOIN stock_universe u ON u.symbol = w.symbol AND u.exchange = w.exchange
        WHERE u.isin IS NOT NULL AND u.isin <> ''
        """
    )
    by_isin = {}
    for r in rows:
        by_isin.setdefault(r["isin"], []).append(r)

    plan = []
    for isin, grp in sorted(by_isin.items()):
        if len(grp) < 2:
            continue
        grp.sort(key=lambda r: (0 if r["is_active"] == 1 else 1,
                                0 if r["exchange"] == "NSE" else 1,
                                r["id"]))
        canonical, losers = grp[0], grp[1:]
        plan.append(
            {
                "isin": isin,
                "name": canonical["name"],
                "canonical_id": canonical["id"],
                "canonical_desc": f"id{canonical['id']} {canonical['exchange']} active={canonical['is_active']}",
                "losers": [
                    {"id": r["id"], "desc": f"id{r['id']} {r['exchange']} active={r['is_active']}"}
                    for r in losers
                ],
            }
        )
    return plan


def statements_for_group(canonical_id, loser_id):
    """Every SQL statement to fold loser_id into canonical_id, in order."""
    out = []
    for table, uniq_col in CHILD_TABLES.items():
        if uniq_col is None:
            out.append(
                f"UPDATE {table} SET watchlist_id = {canonical_id} "
                f"WHERE watchlist_id = {loser_id}"
            )
            continue
        out.append(
            f"UPDATE {table} c SET watchlist_id = {canonical_id} "
            f"WHERE c.watchlist_id = {loser_id} "
            f"AND NOT EXISTS (SELECT 1 FROM {table} k "
            f"WHERE k.watchlist_id = {canonical_id} AND k.{uniq_col} = c.{uniq_col})"
        )
        out.append(f"DELETE FROM {table} WHERE watchlist_id = {loser_id}")
    out.append(f"DELETE FROM stock_watchlist WHERE id = {loser_id}")
    return out


def dry_run(plan):
    print(f"{len(plan)} ISIN groups to collapse.\n")
    grand = {t: {"repoint": 0, "collide": 0} for t in CHILD_TABLES}
    for g in plan:
        print(f"[{g['isin']}] {g['name']}")
        print(f"    canonical: {g['canonical_desc']}")
        for loser in g["losers"]:
            print(f"    loser:     {loser['desc']}")
            for table, uniq_col in CHILD_TABLES.items():
                total = _q(
                    f"SELECT count(*) n FROM {table} WHERE watchlist_id = {loser['id']}"
                )[0]["n"]
                if not total:
                    continue
                if uniq_col is None:
                    print(f"        {table}: repoint {total}")
                    grand[table]["repoint"] += total
                    continue
                collide = _q(
                    f"SELECT count(*) n FROM {table} c WHERE c.watchlist_id = {loser['id']} "
                    f"AND EXISTS (SELECT 1 FROM {table} k WHERE k.watchlist_id = {g['canonical_id']} "
                    f"AND k.{uniq_col} = c.{uniq_col})"
                )[0]["n"]
                print(
                    f"        {table}: repoint {total - collide}, delete {collide} colliding"
                )
                grand[table]["repoint"] += total - collide
                grand[table]["collide"] += collide
        print()
    print("TOTALS across all groups:")
    for t, d in grand.items():
        if d["repoint"] or d["collide"]:
            print(f"    {t}: repoint {d['repoint']}, delete {d['collide']} colliding")
    watchlist_deletes = sum(len(g["losers"]) for g in plan)
    print(f"    stock_watchlist rows deleted: {watchlist_deletes}")
    print("\nDry run only. Nothing executed. Re-run with --apply.")


def apply(plan):
    print(f"Target: {os.environ.get('SUPABASE_URL')}")
    print(f"Collapsing {len(plan)} ISIN groups. This is NOT reversible.\n")
    n_stmt = 0
    for g in plan:
        for loser in g["losers"]:
            for sql in statements_for_group(g["canonical_id"], loser["id"]):
                n_stmt += 1
                one_line = " ".join(sql.split())
                print(f"[{n_stmt}] {one_line}")
                try:
                    _exec(sql)
                except Exception as e:  # noqa: BLE001
                    print(f"\n*** FAILED ***\n{sql}\n\n{e}")
                    print(
                        "\nPartial state. Re-running is safe (loser ids already "
                        "folded are gone, their UPDATEs/DELETEs become no-ops)."
                    )
                    sys.exit(1)
    print(f"\nDone. {n_stmt} statements executed.")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="execute (default is a dry run)")
    args = ap.parse_args()
    plan = build_plan()
    if not plan:
        print("No duplicate watchlist groups found. Nothing to do.")
        return
    (apply if args.apply else dry_run)(plan)


if __name__ == "__main__":
    main()
