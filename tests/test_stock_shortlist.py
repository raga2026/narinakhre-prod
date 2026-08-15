from utils.stock_shortlist import SHORTLIST_SOURCE, run_fundamental_shortlist


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

    def __init__(self, universe_rows, fundamentals_rows, watchlist_rows):
        self.universe = universe_rows
        self.fundamentals = fundamentals_rows
        self.watchlist = {(w['symbol'], w['exchange']): w for w in watchlist_rows}

    def execute(self, sql, params=None):
        params = params or ()
        normalized = ' '.join(sql.split())

        if normalized.startswith('UPDATE stock_watchlist SET is_active=0'):
            (source,) = params
            for w in self.watchlist.values():
                if w['source'] == source and w['is_active'] == 1:
                    w['is_active'] = 0
            return FakeCursor([])

        if normalized.startswith('SELECT u.id AS universe_id, u.symbol, u.exchange, u.company_name'):
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
                    'company_name': u['company_name'], **latest,
                })
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
