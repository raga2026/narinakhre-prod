from utils.stock_shortlist import (
    SHORTLIST_SOURCE,
    _compute_industry_benchmarks,
    get_golden_cross_not_qualified,
    run_fundamental_shortlist,
)


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


PASSING_FUNDAMENTALS = {
    'pe_ratio': 20, 'peg_ratio': 0.8, 'eps': 5, 'opm_pct': 30,
    'roce_pct': 18, 'roa_pct': 10, 'price_to_book': 4,
    'promoter_holding_pct': 55, 'fii_holding_pct': 20,
    'quarterly_profit_growth_pct': 12, 'quarterly_revenue_growth_pct': 11,
}

FAILING_FUNDAMENTALS = {**PASSING_FUNDAMENTALS, 'roce_pct': -1}  # fails ROCE -- excluded outright, not silver-eligible
SILVER_FUNDAMENTALS = {**PASSING_FUNDAMENTALS, 'pe_ratio': 45}  # fails PE range only -- silver-eligible


class FakeShortlistDB:
    """Minimal stand-in for app.py's SupabaseDB, just enough to run the
    exact SQL run_fundamental_shortlist() issues, matching the FakeDB
    pattern used elsewhere in this suite (e.g. test_market_cap_filter.py)."""

    def __init__(self, universe_rows, fundamentals_rows, watchlist_rows, kite_map=None, kite_names=None):
        self.universe = universe_rows
        self.fundamentals = fundamentals_rows
        self.watchlist = {(w['symbol'], w['exchange']): w for w in watchlist_rows}
        self.kite_map = kite_map or {}  # (symbol, exchange) -> instrument_token
        self.kite_names = kite_names or {}  # (symbol, exchange) -> Kite's own company name
        self.previous_snapshot_query_count = 0

    def execute(self, sql, params=None):
        params = params or ()
        normalized = ' '.join(sql.split())

        if normalized.startswith('SELECT universe_id, promoter_holding_pct, fii_holding_pct, snapshot_date'):
            self.previous_snapshot_query_count += 1

        if normalized.startswith('UPDATE stock_watchlist SET is_active=0'):
            (source,) = params
            for w in self.watchlist.values():
                if w['source'] == source and w['is_active'] == 1:
                    w['is_active'] = 0
            return FakeCursor([])

        if normalized.startswith('SELECT u.id AS universe_id, u.symbol, u.exchange, u.company_name, u.isin'):
            (cutoff,) = params
            rows = []
            for u in self.universe:
                if not u['is_scrape_eligible']:
                    continue
                snaps = [f for f in self.fundamentals if f['universe_id'] == u['id']]
                if not snaps:
                    continue
                latest = max(snaps, key=lambda f: f['snapshot_date'])
                if latest['snapshot_date'] < cutoff:
                    continue
                rows.append({
                    'universe_id': u['id'], 'symbol': u['symbol'], 'exchange': u['exchange'],
                    'company_name': u['company_name'], 'isin': u.get('isin'),
                    'industry': u.get('industry'), **latest,
                })
            return FakeCursor(rows)

        if normalized.startswith('SELECT kite_instrument_token FROM stock_kite_instrument_map'):
            symbol, exchange = params
            token = self.kite_map.get((symbol, exchange))
            return FakeCursor([{'kite_instrument_token': token}] if token else [])

        if normalized.startswith('SELECT matched_name FROM stock_kite_instrument_map'):
            symbol, exchange = params
            name = self.kite_names.get((symbol, exchange))
            return FakeCursor([{'matched_name': name}] if name else [])

        if normalized.startswith('SELECT universe_id, promoter_holding_pct, fii_holding_pct, snapshot_date'):
            # Batched replacement for the old one-query-per-candidate lookup
            # -- the real query embeds the universe_id list directly in the
            # SQL text (not as ? params), so the fake just hands back every
            # snapshot it has; run_fundamental_shortlist filters by date in
            # Python, same as the real code path does.
            rows = sorted(self.fundamentals, key=lambda f: (f['universe_id'], f['snapshot_date']), reverse=True)
            return FakeCursor(rows)

        if normalized.startswith('SELECT promoter_holding_pct, fii_holding_pct'):
            universe_id, before_date = params
            snaps = [f for f in self.fundamentals
                     if f['universe_id'] == universe_id and f['snapshot_date'] < before_date]
            if not snaps:
                return FakeCursor([])
            prev = max(snaps, key=lambda f: f['snapshot_date'])
            return FakeCursor([{
                'promoter_holding_pct': prev.get('promoter_holding_pct'),
                'fii_holding_pct': prev.get('fii_holding_pct'),
            }])

        if normalized.startswith('INSERT INTO stock_watchlist'):
            (symbol, exchange, name, insert_source, insert_tier,
             update_source, update_tier, guard_source) = params
            key = (symbol, exchange)
            existing = self.watchlist.get(key)
            if existing is None:
                self.watchlist[key] = {
                    'symbol': symbol, 'exchange': exchange, 'name': name,
                    'is_active': 1, 'source': insert_source, 'fundamental_tier': insert_tier,
                }
            elif existing['source'] == guard_source or existing['source'] is None:
                existing['is_active'] = 1
                existing['source'] = update_source
                existing['name'] = name
                existing['fundamental_tier'] = update_tier
            # else: a different source (e.g. 'manual') -- left untouched.
            return FakeCursor([])

        raise AssertionError(f'Unexpected SQL in test: {sql}')

    def commit(self):
        pass


def test_shortlist_deactivates_no_longer_passing_and_protects_manual_rows():
    universe = [
        {'id': 1, 'symbol': 'STILLGOOD', 'exchange': 'NSE', 'company_name': 'Still Good Ltd', 'is_scrape_eligible': True},
        {'id': 2, 'symbol': 'NOWBAD', 'exchange': 'NSE', 'company_name': 'Now Bad Ltd', 'is_scrape_eligible': True},
        {'id': 3, 'symbol': 'MANUALCO', 'exchange': 'NSE', 'company_name': 'Manual Co Ltd', 'is_scrape_eligible': True},
    ]
    fundamentals = [
        {'universe_id': 1, 'snapshot_date': '2026-08-10', **PASSING_FUNDAMENTALS},
        {'universe_id': 2, 'snapshot_date': '2026-08-10', **FAILING_FUNDAMENTALS},
        {'universe_id': 3, 'snapshot_date': '2026-08-10', **PASSING_FUNDAMENTALS},
    ]
    watchlist = [
        {'symbol': 'STILLGOOD', 'exchange': 'NSE', 'name': 'Still Good Ltd', 'is_active': 1, 'source': SHORTLIST_SOURCE},
        {'symbol': 'NOWBAD', 'exchange': 'NSE', 'name': 'Now Bad Ltd', 'is_active': 1, 'source': SHORTLIST_SOURCE},
        {'symbol': 'MANUALCO', 'exchange': 'NSE', 'name': 'Manual Co Ltd', 'is_active': 1, 'source': 'manual'},
    ]
    db = FakeShortlistDB(universe, fundamentals, watchlist)

    summary = run_fundamental_shortlist(db)

    assert summary['evaluated'] == 3
    assert summary['passed'] == 2   # STILLGOOD, MANUALCO
    assert summary['golden'] == 2
    assert summary['silver'] == 0
    assert summary['failed'] == 1   # NOWBAD
    assert summary['failed_criteria_counts'] == {'ROCE': 1}

    # Still passing -> stays active, still auto-shortlisted, golden tier.
    still_good = db.watchlist[('STILLGOOD', 'NSE')]
    assert still_good['is_active'] == 1
    assert still_good['source'] == SHORTLIST_SOURCE
    assert still_good['fundamental_tier'] == 'golden'

    # No longer passing -> deactivated, not deleted, source unchanged.
    now_bad = db.watchlist[('NOWBAD', 'NSE')]
    assert now_bad['is_active'] == 0
    assert now_bad['source'] == SHORTLIST_SOURCE

    # Manually-added row -> completely untouched even though its
    # fundamentals would otherwise pass the screen.
    manual = db.watchlist[('MANUALCO', 'NSE')]
    assert manual['is_active'] == 1
    assert manual['source'] == 'manual'


def test_previous_snapshot_lookup_is_batched_not_one_query_per_candidate():
    # Regression guard: this used to be one query PER candidate (~3+
    # minutes against live Supabase at real universe scale -- see the live
    # incident this was found from). Must stay a single batched query
    # regardless of how many candidates there are.
    universe = [
        {'id': i, 'symbol': f'SYM{i}', 'exchange': 'NSE', 'company_name': f'Company {i} Ltd',
         'is_scrape_eligible': True, 'isin': f'INE{i:06d}'}
        for i in range(1, 11)
    ]
    fundamentals = [
        {'universe_id': i, 'snapshot_date': '2026-08-10', **PASSING_FUNDAMENTALS}
        for i in range(1, 11)
    ]
    db = FakeShortlistDB(universe, fundamentals, watchlist_rows=[])

    run_fundamental_shortlist(db)

    assert db.previous_snapshot_query_count == 1


def test_passing_company_not_previously_in_watchlist_gets_inserted():
    universe = [
        {'id': 1, 'symbol': 'NEWCO', 'exchange': 'NSE', 'company_name': 'New Co Ltd', 'is_scrape_eligible': True},
    ]
    fundamentals = [
        {'universe_id': 1, 'snapshot_date': '2026-08-10', **PASSING_FUNDAMENTALS},
    ]
    db = FakeShortlistDB(universe, fundamentals, watchlist_rows=[])

    summary = run_fundamental_shortlist(db)

    assert summary['passed'] == 1
    assert summary['golden'] == 1
    newco = db.watchlist[('NEWCO', 'NSE')]
    assert newco['is_active'] == 1
    assert newco['source'] == SHORTLIST_SOURCE
    assert newco['name'] == 'New Co Ltd'


def test_pe_only_failure_gets_included_as_silver_not_excluded():
    # The whole point of the silver tier: a company failing only PE range
    # (or OPM, or both, per fundamental_screen.classify_fundamental_tier)
    # gets watchlisted, not excluded like before this tiering existed.
    universe = [
        {'id': 1, 'symbol': 'SILVERCO', 'exchange': 'NSE', 'company_name': 'Silver Co Ltd', 'is_scrape_eligible': True},
    ]
    fundamentals = [
        {'universe_id': 1, 'snapshot_date': '2026-08-10', **SILVER_FUNDAMENTALS},
    ]
    db = FakeShortlistDB(universe, fundamentals, watchlist_rows=[])

    summary = run_fundamental_shortlist(db)

    assert summary['evaluated'] == 1
    assert summary['passed'] == 1
    assert summary['golden'] == 0
    assert summary['silver'] == 1
    assert summary['failed'] == 0
    # A silver company's PE "failure" didn't cause exclusion, so it must not
    # show up in the exclusion-reason breakdown.
    assert summary['failed_criteria_counts'] == {}

    silverco = db.watchlist[('SILVERCO', 'NSE')]
    assert silverco['is_active'] == 1
    assert silverco['fundamental_tier'] == 'silver'


def test_opm_below_silver_floor_stays_excluded_not_silver():
    universe = [
        {'id': 1, 'symbol': 'TOOLOWOPM', 'exchange': 'NSE', 'company_name': 'Too Low OPM Ltd', 'is_scrape_eligible': True},
    ]
    fundamentals = [
        {'universe_id': 1, 'snapshot_date': '2026-08-10', **{**PASSING_FUNDAMENTALS, 'opm_pct': 10}},
    ]
    db = FakeShortlistDB(universe, fundamentals, watchlist_rows=[])

    summary = run_fundamental_shortlist(db)

    assert summary['passed'] == 0
    assert summary['silver'] == 0
    assert summary['failed'] == 1
    assert summary['failed_criteria_counts'] == {'OPM': 1}
    assert ('TOOLOWOPM', 'NSE') not in db.watchlist


# --- ISIN-based NSE/BSE dedup ---------------------------------------------

def test_same_company_on_nse_and_bse_only_watchlists_one_listing():
    # Same ISIN, both qualify -- must not end up watchlisted twice under
    # two different symbols for what's really one company.
    universe = [
        {'id': 1, 'symbol': 'ACME', 'exchange': 'NSE', 'company_name': 'Acme Ltd', 'is_scrape_eligible': True, 'isin': 'INE000000001'},
        {'id': 2, 'symbol': '500001', 'exchange': 'BSE', 'company_name': 'Acme Limited', 'is_scrape_eligible': True, 'isin': 'INE000000001'},
    ]
    fundamentals = [
        {'universe_id': 1, 'snapshot_date': '2026-08-10', **PASSING_FUNDAMENTALS},
        {'universe_id': 2, 'snapshot_date': '2026-08-10', **PASSING_FUNDAMENTALS},
    ]
    db = FakeShortlistDB(universe, fundamentals, watchlist_rows=[])

    summary = run_fundamental_shortlist(db)

    assert summary['evaluated'] == 2
    assert summary['golden'] == 2  # both individually qualified fundamentally
    assert summary['passed'] == 2  # 'passed' tracks fundamental qualification, same as golden+silver
    assert summary['deduped'] == 1  # ...but only one of those two actually gets watchlisted
    active_rows = [w for w in db.watchlist.values() if w['is_active'] == 1]
    assert len(active_rows) == 1


def test_dedup_prefers_the_listing_with_a_resolved_kite_token():
    universe = [
        {'id': 1, 'symbol': 'ACME', 'exchange': 'NSE', 'company_name': 'Acme Ltd', 'is_scrape_eligible': True, 'isin': 'INE000000001'},
        {'id': 2, 'symbol': '500001', 'exchange': 'BSE', 'company_name': 'Acme Limited', 'is_scrape_eligible': True, 'isin': 'INE000000001'},
    ]
    fundamentals = [
        {'universe_id': 1, 'snapshot_date': '2026-08-10', **PASSING_FUNDAMENTALS},
        {'universe_id': 2, 'snapshot_date': '2026-08-10', **PASSING_FUNDAMENTALS},
    ]
    # Only the BSE listing has a working Kite price link -- it should win
    # even though NSE would otherwise be the default tiebreak.
    db = FakeShortlistDB(universe, fundamentals, watchlist_rows=[], kite_map={('500001', 'BSE'): 999})

    run_fundamental_shortlist(db)

    assert ('500001', 'BSE') in db.watchlist and db.watchlist[('500001', 'BSE')]['is_active'] == 1
    assert ('ACME', 'NSE') not in db.watchlist


def test_dedup_defaults_to_nse_when_neither_or_both_have_a_kite_token():
    universe = [
        {'id': 1, 'symbol': 'ACME', 'exchange': 'NSE', 'company_name': 'Acme Ltd', 'is_scrape_eligible': True, 'isin': 'INE000000001'},
        {'id': 2, 'symbol': '500001', 'exchange': 'BSE', 'company_name': 'Acme Limited', 'is_scrape_eligible': True, 'isin': 'INE000000001'},
    ]
    fundamentals = [
        {'universe_id': 1, 'snapshot_date': '2026-08-10', **PASSING_FUNDAMENTALS},
        {'universe_id': 2, 'snapshot_date': '2026-08-10', **PASSING_FUNDAMENTALS},
    ]
    db = FakeShortlistDB(universe, fundamentals, watchlist_rows=[])  # no kite_map entries at all

    run_fundamental_shortlist(db)

    assert ('ACME', 'NSE') in db.watchlist and db.watchlist[('ACME', 'NSE')]['is_active'] == 1
    assert ('500001', 'BSE') not in db.watchlist


def test_companies_without_an_isin_are_never_grouped_together():
    # Two genuinely different companies that both happen to have no ISIN
    # on record must not be treated as duplicates of each other.
    universe = [
        {'id': 1, 'symbol': 'ONE', 'exchange': 'NSE', 'company_name': 'One Ltd', 'is_scrape_eligible': True, 'isin': None},
        {'id': 2, 'symbol': 'TWO', 'exchange': 'NSE', 'company_name': 'Two Ltd', 'is_scrape_eligible': True, 'isin': ''},
    ]
    fundamentals = [
        {'universe_id': 1, 'snapshot_date': '2026-08-10', **PASSING_FUNDAMENTALS},
        {'universe_id': 2, 'snapshot_date': '2026-08-10', **PASSING_FUNDAMENTALS},
    ]
    db = FakeShortlistDB(universe, fundamentals, watchlist_rows=[])

    summary = run_fundamental_shortlist(db)

    assert summary['deduped'] == 0
    assert summary['passed'] == 2
    assert ('ONE', 'NSE') in db.watchlist and db.watchlist[('ONE', 'NSE')]['is_active'] == 1
    assert ('TWO', 'NSE') in db.watchlist and db.watchlist[('TWO', 'NSE')]['is_active'] == 1


def test_existing_duplicate_pair_self_heals_on_rerun():
    # Mirrors the real bug found live: both listings were already active
    # in the watchlist from a previous (pre-dedup) run. A fresh run must
    # collapse them down to one, deactivating the loser -- not leave both
    # active just because they were already there.
    universe = [
        {'id': 1, 'symbol': 'ACME', 'exchange': 'NSE', 'company_name': 'Acme Ltd', 'is_scrape_eligible': True, 'isin': 'INE000000001'},
        {'id': 2, 'symbol': '500001', 'exchange': 'BSE', 'company_name': 'Acme Limited', 'is_scrape_eligible': True, 'isin': 'INE000000001'},
    ]
    fundamentals = [
        {'universe_id': 1, 'snapshot_date': '2026-08-10', **PASSING_FUNDAMENTALS},
        {'universe_id': 2, 'snapshot_date': '2026-08-10', **PASSING_FUNDAMENTALS},
    ]
    watchlist = [
        {'symbol': 'ACME', 'exchange': 'NSE', 'name': 'Acme Ltd', 'is_active': 1, 'source': SHORTLIST_SOURCE},
        {'symbol': '500001', 'exchange': 'BSE', 'name': 'Acme Limited', 'is_active': 1, 'source': SHORTLIST_SOURCE},
    ]
    db = FakeShortlistDB(universe, fundamentals, watchlist)

    run_fundamental_shortlist(db)

    active = [key for key, w in db.watchlist.items() if w['is_active'] == 1]
    assert len(active) == 1
    assert active[0] == ('ACME', 'NSE')  # NSE wins by default (no kite_map set)


def test_dedup_winner_display_name_comes_from_kite_not_the_source_list():
    # Universe rows disagree ('Ltd' vs 'Limited') -- the winner's name in
    # the watchlist should come from Kite's own naming, not whichever
    # source list happened to win the exchange tiebreak.
    universe = [
        {'id': 1, 'symbol': 'ACME', 'exchange': 'NSE', 'company_name': 'Acme Ltd', 'is_scrape_eligible': True, 'isin': 'INE000000001'},
        {'id': 2, 'symbol': '500001', 'exchange': 'BSE', 'company_name': 'Acme Limited', 'is_scrape_eligible': True, 'isin': 'INE000000001'},
    ]
    fundamentals = [
        {'universe_id': 1, 'snapshot_date': '2026-08-10', **PASSING_FUNDAMENTALS},
        {'universe_id': 2, 'snapshot_date': '2026-08-10', **PASSING_FUNDAMENTALS},
    ]
    db = FakeShortlistDB(
        universe, fundamentals, watchlist_rows=[],
        kite_names={('ACME', 'NSE'): 'ACME India Ltd'},  # Kite's canonical spelling
    )

    run_fundamental_shortlist(db)

    assert db.watchlist[('ACME', 'NSE')]['name'] == 'ACME India Ltd'


def test_single_listing_name_also_uses_kite_name_when_available():
    # Not a duplicate at all -- still gets the Kite-sourced name, so naming
    # is consistent across the whole watchlist, not just for dedup cases.
    universe = [
        {'id': 1, 'symbol': 'SOLO', 'exchange': 'NSE', 'company_name': 'Solo Co Ltd', 'is_scrape_eligible': True, 'isin': None},
    ]
    fundamentals = [
        {'universe_id': 1, 'snapshot_date': '2026-08-10', **PASSING_FUNDAMENTALS},
    ]
    db = FakeShortlistDB(
        universe, fundamentals, watchlist_rows=[],
        kite_names={('SOLO', 'NSE'): 'Solo Company Limited'},
    )

    run_fundamental_shortlist(db)

    assert db.watchlist[('SOLO', 'NSE')]['name'] == 'Solo Company Limited'


def test_name_falls_back_to_universe_name_without_a_kite_match():
    universe = [
        {'id': 1, 'symbol': 'UNMATCHED', 'exchange': 'NSE', 'company_name': 'Unmatched Co Ltd', 'is_scrape_eligible': True, 'isin': None},
    ]
    fundamentals = [
        {'universe_id': 1, 'snapshot_date': '2026-08-10', **PASSING_FUNDAMENTALS},
    ]
    db = FakeShortlistDB(universe, fundamentals, watchlist_rows=[])  # no kite_names at all

    run_fundamental_shortlist(db)

    assert db.watchlist[('UNMATCHED', 'NSE')]['name'] == 'Unmatched Co Ltd'


# --- _compute_industry_benchmarks (pure function) --------------------------

def test_compute_industry_benchmarks_averages_within_each_industry():
    rows = [
        {'industry': 'Software', 'pe_ratio': 30, 'price_to_book': 10},
        {'industry': 'Software', 'pe_ratio': 40, 'price_to_book': 20},
        {'industry': 'Banking', 'pe_ratio': 10, 'price_to_book': 2},
    ]
    benchmarks = _compute_industry_benchmarks(rows)

    assert benchmarks['Software']['pe_ratio'] == {'avg': 35, 'count': 2}
    assert benchmarks['Software']['price_to_book'] == {'avg': 15, 'count': 2}
    assert benchmarks['Banking']['pe_ratio'] == {'avg': 10, 'count': 1}


def test_compute_industry_benchmarks_skips_rows_with_no_industry():
    rows = [
        {'industry': None, 'pe_ratio': 30, 'price_to_book': 10},
        {'pe_ratio': 40, 'price_to_book': 20},  # 'industry' key missing entirely
    ]
    assert _compute_industry_benchmarks(rows) == {}


def test_compute_industry_benchmarks_a_missing_metric_does_not_pollute_the_other():
    # One company has PE but no price_to_book; the other has both -- PE
    # average shouldn't be dragged by a missing price_to_book value, or
    # vice versa. Each metric's average only counts rows that actually
    # have it.
    rows = [
        {'industry': 'Software', 'pe_ratio': 30, 'price_to_book': None},
        {'industry': 'Software', 'pe_ratio': None, 'price_to_book': 10},
    ]
    benchmarks = _compute_industry_benchmarks(rows)

    assert benchmarks['Software']['pe_ratio'] == {'avg': 30, 'count': 1}
    assert benchmarks['Software']['price_to_book'] == {'avg': 10, 'count': 1}


def test_compute_industry_benchmarks_industry_absent_when_nobody_has_either_metric():
    # An industry with zero usable data for BOTH metrics never gets an
    # entry at all -- callers do industry_benchmarks.get(industry), which
    # returns None either way (same as an entry with both sub-keys None).
    rows = [{'industry': 'Software', 'pe_ratio': None, 'price_to_book': None}]
    benchmarks = _compute_industry_benchmarks(rows)

    assert benchmarks.get('Software') is None


def test_compute_industry_benchmarks_metric_is_none_when_only_that_one_is_missing():
    # One company gives this industry a usable PE but no price_to_book --
    # the industry entry exists (PE has data), but its price_to_book key
    # is specifically None (no data for that metric alone).
    rows = [{'industry': 'Software', 'pe_ratio': 30, 'price_to_book': None}]
    benchmarks = _compute_industry_benchmarks(rows)

    assert benchmarks['Software']['pe_ratio'] == {'avg': 30, 'count': 1}
    assert benchmarks['Software']['price_to_book'] is None


# --- industry-relative PE / price-to-book ----------------------------------

def test_industry_benchmark_computed_from_this_runs_own_candidates_lets_a_company_pass():
    # PE 35 fails the flat fallback (15-25) on its own, but four peers in
    # the same industry (scraped this same run) average PE 30 -- 1.5x that
    # is 45, so 35 passes once there's enough same-industry data to trust.
    universe = [
        {'id': 1, 'symbol': 'RICH', 'exchange': 'NSE', 'company_name': 'Rich Co Ltd',
         'is_scrape_eligible': True, 'isin': None, 'industry': 'Software Services'},
        {'id': 2, 'symbol': 'PEER1', 'exchange': 'NSE', 'company_name': 'Peer One Ltd',
         'is_scrape_eligible': True, 'isin': None, 'industry': 'Software Services'},
        {'id': 3, 'symbol': 'PEER2', 'exchange': 'NSE', 'company_name': 'Peer Two Ltd',
         'is_scrape_eligible': True, 'isin': None, 'industry': 'Software Services'},
        {'id': 4, 'symbol': 'PEER3', 'exchange': 'NSE', 'company_name': 'Peer Three Ltd',
         'is_scrape_eligible': True, 'isin': None, 'industry': 'Software Services'},
    ]
    fundamentals = [
        {'universe_id': 1, 'snapshot_date': '2026-08-10', **{**PASSING_FUNDAMENTALS, 'pe_ratio': 35}},
        {'universe_id': 2, 'snapshot_date': '2026-08-10', **{**PASSING_FUNDAMENTALS, 'pe_ratio': 30}},
        {'universe_id': 3, 'snapshot_date': '2026-08-10', **{**PASSING_FUNDAMENTALS, 'pe_ratio': 30}},
        {'universe_id': 4, 'snapshot_date': '2026-08-10', **{**PASSING_FUNDAMENTALS, 'pe_ratio': 30}},
    ]
    db = FakeShortlistDB(universe, fundamentals, watchlist_rows=[])

    summary = run_fundamental_shortlist(db)

    assert summary['golden'] == 4  # RICH included, not merely silver-eligible for PE
    rich = db.watchlist[('RICH', 'NSE')]
    assert rich['is_active'] == 1
    assert rich['fundamental_tier'] == 'golden'


def test_industry_with_too_few_companies_falls_back_to_flat_pe_band():
    # Only 2 companies share this industry -- below MIN_INDUSTRY_SAMPLE_SIZE
    # (3), so PE 35 must fall back to the flat 15-25 band and fail, even
    # though the (untrusted, small-sample) industry average would allow it.
    universe = [
        {'id': 1, 'symbol': 'RICH', 'exchange': 'NSE', 'company_name': 'Rich Co Ltd',
         'is_scrape_eligible': True, 'isin': None, 'industry': 'Niche Industry'},
        {'id': 2, 'symbol': 'PEER1', 'exchange': 'NSE', 'company_name': 'Peer One Ltd',
         'is_scrape_eligible': True, 'isin': None, 'industry': 'Niche Industry'},
    ]
    fundamentals = [
        {'universe_id': 1, 'snapshot_date': '2026-08-10', **{**PASSING_FUNDAMENTALS, 'pe_ratio': 35}},
        {'universe_id': 2, 'snapshot_date': '2026-08-10', **PASSING_FUNDAMENTALS},  # pe_ratio 20, within flat fallback
    ]
    db = FakeShortlistDB(universe, fundamentals, watchlist_rows=[])

    summary = run_fundamental_shortlist(db)

    assert summary['golden'] == 1  # only PEER1
    # RICH's PE 35 fails the flat fallback band (untrusted small-sample
    # industry average doesn't apply) -- but PE range alone is
    # silver-eligible, not an outright exclusion, so it's still watchlisted.
    assert db.watchlist[('RICH', 'NSE')]['fundamental_tier'] == 'silver'
    assert db.watchlist[('PEER1', 'NSE')]['fundamental_tier'] == 'golden'


def test_company_with_no_industry_on_record_uses_flat_fallback_band():
    universe = [
        {'id': 1, 'symbol': 'NOIND', 'exchange': 'NSE', 'company_name': 'No Industry Ltd',
         'is_scrape_eligible': True, 'isin': None},  # no 'industry' key at all -- never scraped
    ]
    fundamentals = [
        {'universe_id': 1, 'snapshot_date': '2026-08-10', **PASSING_FUNDAMENTALS},  # pe_ratio 20, within flat band
    ]
    db = FakeShortlistDB(universe, fundamentals, watchlist_rows=[])

    summary = run_fundamental_shortlist(db)

    assert summary['golden'] == 1
    assert db.watchlist[('NOIND', 'NSE')]['is_active'] == 1


# --- get_golden_cross_not_qualified ---------------------------------------

class FakeGoldenCrossDB:
    """Minimal stand-in for app.py's SupabaseDB, just enough to run the
    exact SQL get_golden_cross_not_qualified() issues."""

    def __init__(self, rows, kite_map=None, kite_names=None):
        self.rows = rows  # pre-joined rows, as if from the real query
        self.kite_map = kite_map or {}
        self.kite_names = kite_names or {}

    def execute(self, sql, params=None):
        params = params or ()
        normalized = ' '.join(sql.split())

        if normalized.startswith('SELECT u.id AS universe_id, u.symbol, u.exchange, u.company_name, u.isin, u.industry, i.cross_status'):
            return FakeCursor(self.rows)

        if normalized.startswith('SELECT kite_instrument_token FROM stock_kite_instrument_map'):
            symbol, exchange = params
            token = self.kite_map.get((symbol, exchange))
            return FakeCursor([{'kite_instrument_token': token}] if token else [])

        if normalized.startswith('SELECT matched_name FROM stock_kite_instrument_map'):
            symbol, exchange = params
            name = self.kite_names.get((symbol, exchange))
            return FakeCursor([{'matched_name': name}] if name else [])

        raise AssertionError(f'Unexpected SQL in test: {sql}')

    def commit(self):
        pass


GOLDEN_CROSS_INDICATORS = {'cross_status': 'golden_cross', 'rsi_14': 55, 'ma_5': 101, 'ma_21': 100, 'ma_50': 95, 'ma_200': 90}


def test_golden_cross_company_failing_fundamentals_reports_why():
    row = {
        'universe_id': 1, 'symbol': 'FAILSFUND', 'exchange': 'NSE', 'company_name': 'Fails Fund Ltd', 'isin': 'INE1',
        **GOLDEN_CROSS_INDICATORS,
        **{**PASSING_FUNDAMENTALS, 'roce_pct': -1},  # fails ROCE
    }
    db = FakeGoldenCrossDB([row])

    results = get_golden_cross_not_qualified(db)

    assert len(results) == 1
    assert results[0]['symbol'] == 'FAILSFUND'
    assert 'ROCE' in results[0]['failed_criteria']


def test_golden_cross_company_missing_fundamentals_data_reports_all_as_failed():
    # LEFT JOINed fundamentals -- a company with no fundamentals snapshot
    # at all still shows up (it IS golden cross), just with every
    # fundamental field None/missing.
    row = {
        'universe_id': 1, 'symbol': 'NOFUND', 'exchange': 'NSE', 'company_name': 'No Fund Ltd', 'isin': None,
        **GOLDEN_CROSS_INDICATORS,
        'pe_ratio': None, 'peg_ratio': None, 'eps': None, 'opm_pct': None, 'roce_pct': None, 'roa_pct': None,
        'price_to_book': None, 'promoter_holding_pct': None, 'fii_holding_pct': None,
        'quarterly_profit_growth_pct': None, 'quarterly_revenue_growth_pct': None,
    }
    db = FakeGoldenCrossDB([row])

    results = get_golden_cross_not_qualified(db)

    assert len(results) == 1
    assert len(results[0]['failed_criteria']) > 0


def test_golden_cross_dedup_by_isin_keeps_only_one_listing():
    rows = [
        {'universe_id': 1, 'symbol': 'ACME', 'exchange': 'NSE', 'company_name': 'Acme Ltd', 'isin': 'INE1',
         **GOLDEN_CROSS_INDICATORS, **{**PASSING_FUNDAMENTALS, 'roce_pct': -1}},
        {'universe_id': 2, 'symbol': '500001', 'exchange': 'BSE', 'company_name': 'Acme Limited', 'isin': 'INE1',
         **GOLDEN_CROSS_INDICATORS, **{**PASSING_FUNDAMENTALS, 'roce_pct': -1}},
    ]
    db = FakeGoldenCrossDB(rows)

    results = get_golden_cross_not_qualified(db)

    assert len(results) == 1
    assert results[0]['symbol'] == 'ACME'  # NSE wins by default, no kite_map set


def test_golden_cross_dedup_winner_name_comes_from_kite():
    rows = [
        {'universe_id': 1, 'symbol': 'ACME', 'exchange': 'NSE', 'company_name': 'Acme Ltd', 'isin': 'INE1',
         **GOLDEN_CROSS_INDICATORS, **{**PASSING_FUNDAMENTALS, 'roce_pct': -1}},
        {'universe_id': 2, 'symbol': '500001', 'exchange': 'BSE', 'company_name': 'Acme Limited', 'isin': 'INE1',
         **GOLDEN_CROSS_INDICATORS, **{**PASSING_FUNDAMENTALS, 'roce_pct': -1}},
    ]
    db = FakeGoldenCrossDB(rows, kite_names={('ACME', 'NSE'): 'ACME India Ltd'})

    results = get_golden_cross_not_qualified(db)

    assert results[0]['company_name'] == 'ACME India Ltd'


def test_results_sorted_alphabetically_by_company_name():
    rows = [
        {'universe_id': 1, 'symbol': 'ZED', 'exchange': 'NSE', 'company_name': 'Zebra Ltd', 'isin': 'INE1',
         **GOLDEN_CROSS_INDICATORS, **{**PASSING_FUNDAMENTALS, 'roce_pct': -1}},
        {'universe_id': 2, 'symbol': 'ALP', 'exchange': 'NSE', 'company_name': 'Alpha Ltd', 'isin': 'INE2',
         **GOLDEN_CROSS_INDICATORS, **{**PASSING_FUNDAMENTALS, 'roce_pct': -1}},
    ]
    db = FakeGoldenCrossDB(rows)

    results = get_golden_cross_not_qualified(db)

    assert [r['symbol'] for r in results] == ['ALP', 'ZED']
