from unittest.mock import MagicMock, patch

from utils.super_sync import STEP_LABELS, SUPER_SYNC_STEPS, run_super_sync
from utils import job_progress


def test_all_steps_run_in_the_documented_order():
    db = MagicMock()
    call_order = []

    def _tracked(name, return_value):
        def _fn(*args, **kwargs):
            call_order.append(name)
            return return_value
        return _fn

    with patch('utils.super_sync.KiteClient', return_value=MagicMock()), \
         patch('utils.super_sync.sync_kite_instrument_map', side_effect=_tracked('kite_instrument_map_sync', {})), \
         patch('utils.super_sync.sync_fundamentals', side_effect=_tracked('fundamentals_sync', {})), \
         patch('utils.super_sync.sync_fundamentals_rotation', side_effect=_tracked('fundamentals_rotation', {})), \
         patch('utils.super_sync.refresh_market_cap_filter', side_effect=_tracked('market_cap_filter', {})), \
         patch('utils.super_sync.run_fundamental_shortlist', side_effect=_tracked('shortlist_refresh', {})), \
         patch('utils.super_sync.sync_daily_data', side_effect=_tracked('price_sync', {})), \
         patch('utils.super_sync.run_indicator_calculation', side_effect=_tracked('indicator_calc', {})), \
         patch('utils.super_sync.sync_daily_data_universe', side_effect=_tracked('price_sync_universe', {})), \
         patch('utils.super_sync.run_indicator_calculation_universe', side_effect=_tracked('indicator_calc_universe', {})):
        result = run_super_sync(db, access_token='fake-token')

    assert call_order == SUPER_SYNC_STEPS
    assert result['ok_count'] == len(SUPER_SYNC_STEPS)
    assert result['error_count'] == 0
    assert [s['step'] for s in result['steps']] == SUPER_SYNC_STEPS
    assert all(s['status'] == 'ok' for s in result['steps'])


def test_a_failing_step_does_not_stop_the_rest():
    db = MagicMock()

    with patch('utils.super_sync.KiteClient', return_value=MagicMock()), \
         patch('utils.super_sync.sync_kite_instrument_map', side_effect=RuntimeError('Kite session expired')), \
         patch('utils.super_sync.sync_fundamentals', return_value={'inserted': 3}), \
         patch('utils.super_sync.sync_fundamentals_rotation', return_value={'scraped': 5}), \
         patch('utils.super_sync.refresh_market_cap_filter', return_value={}), \
         patch('utils.super_sync.run_fundamental_shortlist', return_value={'passed': 2}), \
         patch('utils.super_sync.sync_daily_data', side_effect=RuntimeError('No Kite session')), \
         patch('utils.super_sync.run_indicator_calculation', return_value={}), \
         patch('utils.super_sync.sync_daily_data_universe', return_value={}), \
         patch('utils.super_sync.run_indicator_calculation_universe', return_value={}):
        result = run_super_sync(db, access_token='fake-token')

    assert result['ok_count'] == len(SUPER_SYNC_STEPS) - 2
    assert result['error_count'] == 2

    by_step = {s['step']: s for s in result['steps']}
    assert by_step['kite_instrument_map_sync']['status'] == 'error'
    assert by_step['kite_instrument_map_sync']['error'] == 'Kite session expired'
    assert by_step['price_sync']['status'] == 'error'
    # Everything after the failures still ran.
    assert by_step['indicator_calc_universe']['status'] == 'ok'
    assert by_step['fundamentals_sync']['status'] == 'ok'
    assert by_step['fundamentals_sync']['summary'] == {'inserted': 3}


def test_all_nine_steps_are_present():
    assert len(SUPER_SYNC_STEPS) == 9
    assert len(set(SUPER_SYNC_STEPS)) == 9  # no accidental duplicates


def test_every_step_has_a_human_readable_label():
    assert set(STEP_LABELS.keys()) == set(SUPER_SYNC_STEPS)


def test_progress_reports_a_step_label_before_each_step_runs():
    db = MagicMock()
    reports = []
    job_progress.bind(lambda current, total, label: reports.append((current, total, label)))

    try:
        with patch('utils.super_sync.KiteClient', return_value=MagicMock()), \
             patch('utils.super_sync.sync_kite_instrument_map', return_value={}), \
             patch('utils.super_sync.sync_fundamentals', return_value={}), \
             patch('utils.super_sync.sync_fundamentals_rotation', return_value={}), \
             patch('utils.super_sync.refresh_market_cap_filter', return_value={}), \
             patch('utils.super_sync.run_fundamental_shortlist', return_value={}), \
             patch('utils.super_sync.sync_daily_data', return_value={}), \
             patch('utils.super_sync.run_indicator_calculation', return_value={}), \
             patch('utils.super_sync.sync_daily_data_universe', return_value={}), \
             patch('utils.super_sync.run_indicator_calculation_universe', return_value={}):
            run_super_sync(db, access_token='fake-token')
    finally:
        job_progress.clear()

    assert len(reports) == len(SUPER_SYNC_STEPS)
    assert reports[0] == (0, 9, 'Step 1 of 9: Syncing Kite instrument map')
    assert reports[-1] == (8, 9, 'Step 9 of 9: Calculating indicators (universe)')
