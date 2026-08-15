from utils.watchlist_view import enrich_and_sort_watchlist_rows

GOOD_INDICATORS = {'cross_status': 'golden_cross', 'volume_trend': 'confirming', 'rsi_14': 50}


def _row(symbol, name=None, **overrides):
    row = {
        'id': 1, 'symbol': symbol, 'exchange': 'NSE', 'name': name,
        'pe_ratio': 20, 'opm_pct': 30,
        **GOOD_INDICATORS,
    }
    row.update(overrides)
    return row


def test_recommended_row_is_flagged_using_the_real_hard_filters():
    row = _row('GOOD')  # golden_cross + confirming + RSI in range -> recommended
    result = enrich_and_sort_watchlist_rows([row])
    assert result[0]['is_recommended'] is True


def test_non_recommended_row_is_flagged_false():
    row = _row('BAD', cross_status='death_cross')
    result = enrich_and_sort_watchlist_rows([row])
    assert result[0]['is_recommended'] is False


def test_golden_filter_keeps_only_golden_cross_rows():
    rows = [
        _row('GOLD', cross_status='golden_cross'),
        _row('DEATH', cross_status='death_cross'),
        _row('FLAT', cross_status='no_clear_trend'),
    ]
    result = enrich_and_sort_watchlist_rows(rows, cross_filter='golden')
    assert {r['symbol'] for r in result} == {'GOLD'}


def test_no_filter_keeps_every_row():
    rows = [
        _row('GOLD', cross_status='golden_cross'),
        _row('DEATH', cross_status='death_cross'),
    ]
    result = enrich_and_sort_watchlist_rows(rows, cross_filter=None)
    assert len(result) == 2


def test_sort_puts_recommended_rows_first():
    recommended = _row('AAA', name='Zebra Corp')  # sorts last alphabetically, but recommended
    not_recommended = _row('BBB', name='Alpha Corp', cross_status='death_cross')
    result = enrich_and_sort_watchlist_rows([not_recommended, recommended])
    assert [r['symbol'] for r in result] == ['AAA', 'BBB']


def test_sort_puts_golden_cross_ahead_of_others_when_neither_is_recommended():
    # Neither passes the full hard filter (RSI out of range for both), but
    # golden_cross should still sort ahead of no_clear_trend.
    golden_not_recommended = _row('GLD', name='Zebra Corp', rsi_14=90)
    plain = _row('PLN', name='Alpha Corp', cross_status='no_clear_trend', rsi_14=90)
    result = enrich_and_sort_watchlist_rows([plain, golden_not_recommended])
    assert [r['symbol'] for r in result] == ['GLD', 'PLN']
    assert all(not r['is_recommended'] for r in result)


def test_sort_falls_back_to_symbol_when_name_missing():
    a = _row('AAA', name=None)
    b = _row('BBB', name=None)
    result = enrich_and_sort_watchlist_rows([b, a])
    assert [r['symbol'] for r in result] == ['AAA', 'BBB']


def test_metric_notes_are_attached_per_row():
    row = _row('OUT', pe_ratio=45)  # outside 15-25 ideal band
    result = enrich_and_sort_watchlist_rows([row])
    assert result[0]['pe_note'] is not None
    assert result[0]['opm_note'] is None  # opm_pct=30 is within ideal band


def test_does_not_mutate_input_rows():
    row = _row('ORIG')
    original_keys = set(row.keys())
    enrich_and_sort_watchlist_rows([row])
    assert set(row.keys()) == original_keys  # no is_recommended/pe_note leaked into the original dict
