from stoqbell.utils.stock_universe import rebucket_large_cap_eligibility, refresh_market_cap_filter


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeUniverseDB:
    """Minimal stand-in for app.py's SupabaseDB, just enough to run the
    exact SQL refresh_market_cap_filter() issues. Simulates the self-join
    UPDATEs directly against an in-memory list of dict rows rather than
    parsing SQL, same approach as the other *_ingestion FakeDB tests in
    this suite."""

    def __init__(self, rows):
        self.rows = rows

    def execute(self, sql, params=None):
        normalized = ' '.join(sql.split())

        # propagate_bse_market_cap_to_nse() calls this same simple count
        # twice (before and after the UPDATE) rather than a separate
        # JOIN-based pre-check -- see its docstring for why. Recomputing
        # live from self.rows each call naturally gives the right answer
        # both times, before and after _apply_propagation() mutates it.
        if normalized.startswith("SELECT COUNT(*) AS count FROM stock_universe WHERE exchange='NSE' AND last_market_cap IS NULL"):
            count = sum(1 for r in self.rows if r['exchange'] == 'NSE' and r['last_market_cap'] is None)
            return FakeCursor([{'count': count}])

        if normalized.startswith('UPDATE stock_universe AS nse SET last_market_cap = bse.last_market_cap'):
            self._apply_propagation()
            return FakeCursor([])

        if normalized.startswith('UPDATE stock_universe SET market_cap_band = CASE'):
            self._rebucket()
            return FakeCursor([])

        if normalized.startswith('SELECT COUNT(*) AS count FROM stock_universe WHERE is_scrape_eligible = true'):
            count = sum(1 for r in self.rows if r.get('is_scrape_eligible'))
            return FakeCursor([{'count': count}])

        if normalized.startswith('UPDATE stock_universe SET is_large_cap_eligible ='):
            for r in self.rows:
                r['is_large_cap_eligible'] = r['last_market_cap'] is not None and r['last_market_cap'] > 30000
            return FakeCursor([])

        if normalized.startswith('SELECT COUNT(*) AS count FROM stock_universe WHERE is_large_cap_eligible = true'):
            count = sum(1 for r in self.rows if r.get('is_large_cap_eligible'))
            return FakeCursor([{'count': count}])

        raise AssertionError(f'Unexpected SQL in test: {sql}')

    def _apply_propagation(self):
        for nse in self.rows:
            if nse['exchange'] != 'NSE' or not nse.get('isin') or nse['last_market_cap'] is not None:
                continue
            match = next(
                (b for b in self.rows if b['exchange'] == 'BSE' and b.get('isin') == nse['isin']
                 and b['last_market_cap'] is not None),
                None,
            )
            if match:
                nse['last_market_cap'] = match['last_market_cap']
                nse['last_market_cap_date'] = match['last_market_cap_date']

    def _rebucket(self):
        for r in self.rows:
            cap = r['last_market_cap']
            if cap is None:
                r['market_cap_band'] = 'unknown'
                r['is_scrape_eligible'] = False
            elif cap < 5000:
                r['market_cap_band'] = 'below_5000cr'
                r['is_scrape_eligible'] = False
            elif cap <= 30000:
                r['market_cap_band'] = '5000_to_30000cr'
                r['is_scrape_eligible'] = True
            else:
                r['market_cap_band'] = 'above_30000cr'
                r['is_scrape_eligible'] = False

    def commit(self):
        pass


def test_matching_isin_gets_market_cap_copied_from_bse():
    rows = [
        {'id': 1, 'symbol': 'ABC', 'exchange': 'NSE', 'isin': 'INE001',
         'last_market_cap': None, 'last_market_cap_date': None},
        {'id': 2, 'symbol': '500001', 'exchange': 'BSE', 'isin': 'INE001',
         'last_market_cap': 15000.0, 'last_market_cap_date': '2026-08-15'},
    ]
    db = FakeUniverseDB(rows)

    refresh_market_cap_filter(db)

    abc = next(r for r in rows if r['symbol'] == 'ABC')
    assert abc['last_market_cap'] == 15000.0
    assert abc['last_market_cap_date'] == '2026-08-15'


def test_nse_only_company_with_no_bse_match_stays_unknown_band():
    rows = [
        {'id': 3, 'symbol': 'XYZ', 'exchange': 'NSE', 'isin': 'INE002',
         'last_market_cap': None, 'last_market_cap_date': None},
    ]
    db = FakeUniverseDB(rows)

    refresh_market_cap_filter(db)

    xyz = next(r for r in rows if r['symbol'] == 'XYZ')
    assert xyz['last_market_cap'] is None
    assert xyz['market_cap_band'] == 'unknown'
    assert xyz['is_scrape_eligible'] is False


def test_eligible_count_only_includes_the_5000_to_30000cr_band():
    rows = [
        {'id': 1, 'symbol': 'ABC', 'exchange': 'NSE', 'isin': 'INE001',
         'last_market_cap': None, 'last_market_cap_date': None},
        {'id': 2, 'symbol': '500001', 'exchange': 'BSE', 'isin': 'INE001',
         'last_market_cap': 15000.0, 'last_market_cap_date': '2026-08-15'},
        {'id': 3, 'symbol': 'XYZ', 'exchange': 'NSE', 'isin': 'INE002',
         'last_market_cap': None, 'last_market_cap_date': None},
        {'id': 4, 'symbol': 'SMALL', 'exchange': 'NSE', 'isin': 'INE003',
         'last_market_cap': 2000.0, 'last_market_cap_date': '2026-08-15'},
        {'id': 5, 'symbol': 'BIG', 'exchange': 'NSE', 'isin': 'INE004',
         'last_market_cap': 50000.0, 'last_market_cap_date': '2026-08-15'},
    ]
    db = FakeUniverseDB(rows)

    summary = refresh_market_cap_filter(db)

    assert summary['propagated'] == 1       # ABC only
    assert summary['remaining_without_market_cap'] == 1  # XYZ only
    # Re-bucketing runs over every stock_universe row, not just NSE -- so
    # both ABC (NSE, 15000 after propagation) and its BSE counterpart
    # 500001 (already 15000) land in the eligible band; SMALL and BIG don't.
    assert summary['scrape_eligible_count'] == 2

    bands = {r['symbol']: r['market_cap_band'] for r in rows}
    assert bands['ABC'] == '5000_to_30000cr'
    assert bands['500001'] == '5000_to_30000cr'
    assert bands['XYZ'] == 'unknown'
    assert bands['SMALL'] == 'below_5000cr'
    assert bands['BIG'] == 'above_30000cr'


# --- Large-cap eligibility (entirely parallel to the bucketing above) ------

def test_rebucket_large_cap_eligibility_has_no_upper_bound():
    rows = [
        {'id': 1, 'symbol': 'SMALL', 'exchange': 'NSE', 'isin': 'INE003',
         'last_market_cap': 2000.0, 'last_market_cap_date': '2026-08-15'},
        {'id': 2, 'symbol': 'MID', 'exchange': 'NSE', 'isin': 'INE004',
         'last_market_cap': 15000.0, 'last_market_cap_date': '2026-08-15'},
        {'id': 3, 'symbol': 'BIG', 'exchange': 'NSE', 'isin': 'INE005',
         'last_market_cap': 30001.0, 'last_market_cap_date': '2026-08-15'},
        {'id': 4, 'symbol': 'HUGE', 'exchange': 'NSE', 'isin': 'INE006',
         'last_market_cap': 1000000.0, 'last_market_cap_date': '2026-08-15'},
        {'id': 5, 'symbol': 'UNKNOWN', 'exchange': 'NSE', 'isin': 'INE007',
         'last_market_cap': None, 'last_market_cap_date': None},
    ]
    db = FakeUniverseDB(rows)

    count = rebucket_large_cap_eligibility(db)

    assert count == 2  # BIG, HUGE
    eligible = {r['symbol']: r['is_large_cap_eligible'] for r in rows}
    assert eligible == {
        'SMALL': False, 'MID': False, 'BIG': True, 'HUGE': True, 'UNKNOWN': False,
    }


def test_rebucket_large_cap_eligibility_boundary_exactly_30000_is_not_eligible():
    # Matches market_cap_band's own boundary -- exactly 30000 is
    # '5000_to_30000cr', not 'above_30000cr'.
    rows = [{'id': 1, 'symbol': 'EDGE', 'exchange': 'NSE', 'isin': 'INE008',
             'last_market_cap': 30000.0, 'last_market_cap_date': '2026-08-15'}]
    db = FakeUniverseDB(rows)

    rebucket_large_cap_eligibility(db)

    assert rows[0]['is_large_cap_eligible'] is False


def test_rebucket_large_cap_eligibility_is_independent_of_market_cap_band_rebucketing():
    # rebucket_large_cap_eligibility must give the right answer even if
    # rebucket_market_cap_bands (via refresh_market_cap_filter) never ran
    # at all -- it derives straight from last_market_cap, not from
    # market_cap_band.
    rows = [{'id': 1, 'symbol': 'BIG', 'exchange': 'NSE', 'isin': 'INE009',
             'last_market_cap': 50000.0, 'last_market_cap_date': '2026-08-15',
             'market_cap_band': 'unknown'}]  # never rebucketed
    db = FakeUniverseDB(rows)

    rebucket_large_cap_eligibility(db)

    assert rows[0]['is_large_cap_eligible'] is True
    assert rows[0]['market_cap_band'] == 'unknown'  # untouched by this function
