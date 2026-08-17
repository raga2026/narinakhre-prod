from utils.suggestion_engine import compute_watchlist_nns_scores

GOOD_ROW = {
    'id': 1, 'watchlist_id': 1, 'universe_id': 101, 'industry': 'Chemicals',
    'symbol': 'GOODCO', 'snapshot_date': '2026-08-01',
    'cross_status': 'golden_cross', 'volume_trend': 'confirming', 'rsi_14': 50,
    'pe_ratio': 18, 'peg_ratio': 1.0, 'opm_pct': 25, 'roce_pct': 28, 'roa_pct': 18,
    'quarterly_profit_growth_pct': 20, 'quarterly_revenue_growth_pct': 20,
    'price_to_book': 3, 'promoter_holding_pct': 60, 'fii_holding_pct': 10,
}


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeSnapshotDB:
    def __init__(self, snapshot_rows=None):
        self.snapshot_rows = snapshot_rows or []

    def execute(self, sql, params=None):
        normalized = ' '.join(sql.split())
        if normalized.startswith('SELECT universe_id, promoter_holding_pct, fii_holding_pct, snapshot_date'):
            return FakeCursor(self.snapshot_rows)
        raise AssertionError(f'Unexpected SQL in test: {sql}')

    def commit(self):
        pass


def test_every_row_gets_a_score_and_tier_regardless_of_hard_filters():
    # A row that fails the hard filter (death_cross) still gets scored --
    # unlike score_candidates, this is NOT restricted to hard-filter passers.
    bad_row = {**GOOD_ROW, 'id': 2, 'watchlist_id': 2, 'universe_id': 102, 'cross_status': 'death_cross'}
    db = FakeSnapshotDB()

    rows = compute_watchlist_nns_scores(db, [GOOD_ROW, bad_row])

    assert all(r['nns_score'] is not None for r in rows)
    assert all('nns_tier' in r for r in rows)
    # The good row should score meaningfully higher than the death-cross one
    # (RSI sub-score differs since bad_row keeps rsi_14=50, but cross_status
    # itself isn't part of NNS Score directly -- what matters here is just
    # that both rows get scored independently, not that one beats the other).


def test_does_not_mutate_input_rows():
    db = FakeSnapshotDB()
    row = dict(GOOD_ROW)
    original_keys = set(row.keys())

    compute_watchlist_nns_scores(db, [row])

    assert set(row.keys()) == original_keys  # nns_score/nns_tier not leaked into the original dict


def test_missing_universe_id_still_scores_with_holding_trend_at_zero():
    row = {**GOOD_ROW, 'universe_id': None, 'industry': None}
    db = FakeSnapshotDB()

    rows = compute_watchlist_nns_scores(db, [row])

    assert rows[0]['nns_score'] is not None


def test_falls_back_to_id_key_when_watchlist_id_is_absent():
    # /stocks/watchlist's own query selects w.id, not w.id AS watchlist_id --
    # compute_watchlist_nns_scores must still work from 'id' alone.
    row = {k: v for k, v in GOOD_ROW.items() if k != 'watchlist_id'}
    db = FakeSnapshotDB()

    rows = compute_watchlist_nns_scores(db, [row])

    assert rows[0]['nns_score'] is not None
