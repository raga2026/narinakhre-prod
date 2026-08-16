from utils.stock_shortlist import SHORTLIST_SOURCE, get_golden_cross_not_qualified, run_fundamental_shortlist


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

    def __init__(self, universe_rows, fundamentals_rows, watchlist_rows, kite_map=None):
        self.universe = universe_rows
        self.fundamentals = fundamentals_rows
        self.watchlist = {(w['symbol'], w['exchange']): w for w in watchlist_rows}
        self.kite_map = kite_map or {}  # (symbol, exchange) -> instrument_token

    def execute(self, sql, params=None):
        params = params or ()
        normalized = ' '.join(sql.split())

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
                    'company_name': u['company_name'], 'isin': u.get('isin'), **latest,
                })
            return FakeCursor(rows)

        if normalized.startswith('SELECT kite_instrument_token FROM stock_kite_instrument_map'):
            symbol, exchange = params
            token = self.kite_map.get((symbol, exchange))
            return FakeCursor([{'kite_instrument_token': token}] if token else [])

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


# --- get_golden_cross_not_qualified ---------------------------------------

class FakeGoldenCrossDB:
    """Minimal stand-in for app.py's SupabaseDB, just enough to run the
    exact SQL get_golden_cross_not_qualified() issues."""

    def __init__(self, rows, kite_map=None):
        self.rows = rows  # pre-joined rows, as if from the real query
        self.kite_map = kite_map or {}

    def execute(self, sql, params=None):
        params = params or ()
        normalized = ' '.join(sql.split())

        if normalized.startswith('SELECT u.id AS universe_id, u.symbol, u.exchange, u.company_name, u.isin, i.cross_status'):
            return FakeCursor(self.rows)

        if normalized.startswith('SELECT kite_instrument_token FROM stock_kite_instrument_map'):
            symbol, exchange = params
            token = self.kite_map.get((symbol, exchange))
            return FakeCursor([{'kite_instrument_token': token}] if token else [])

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
