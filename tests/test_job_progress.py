from stoqbell.utils import job_progress


def teardown_function(_fn):
    # bind()/clear() touch threading.local state -- since pytest runs every
    # test in the same thread, leftover state from one test would otherwise
    # leak into the next.
    job_progress.clear()


def test_report_is_a_no_op_when_nothing_is_bound():
    calls = []
    # No bind() call -- report() must not raise, and must not call anything.
    job_progress.report(1, 10)
    assert calls == []


def test_bound_reporter_receives_current_total_and_label():
    calls = []
    job_progress.bind(lambda current, total, label: calls.append((current, total, label)))

    job_progress.report(5, 10, label='Syncing prices')

    assert calls == [(5, 10, 'Syncing prices')]


def test_zero_total_never_reports_to_avoid_a_division_by_zero_downstream():
    calls = []
    job_progress.bind(lambda current, total, label: calls.append((current, total, label)))

    job_progress.report(0, 0)

    assert calls == []


def test_rapid_non_final_reports_are_throttled():
    calls = []
    job_progress.bind(lambda current, total, label: calls.append((current, total, label)))

    job_progress.report(1, 100)  # first report always goes through
    job_progress.report(2, 100)  # immediately after -- throttled, skipped
    job_progress.report(3, 100)  # still within the throttle window -- skipped

    assert calls == [(1, 100, None)]


def test_final_report_always_goes_through_even_inside_the_throttle_window():
    calls = []
    job_progress.bind(lambda current, total, label: calls.append((current, total, label)))

    job_progress.report(1, 3)
    job_progress.report(2, 3)   # throttled
    job_progress.report(3, 3)   # current == total -- always written

    assert calls == [(1, 3, None), (3, 3, None)]


def test_an_explicit_label_bypasses_the_throttle():
    # Rapid step-boundary markers (e.g. Super Sync moving through its 9
    # steps in a fast test, or a fast real run) must never get silently
    # dropped just because they land inside the same throttle window --
    # unlike a plain numeric tick, a label change is itself the signal.
    calls = []
    job_progress.bind(lambda current, total, label: calls.append((current, total, label)))

    job_progress.report(0, 9, label='Step 1 of 9: fundamentals sync')
    job_progress.report(1, 9, label='Step 2 of 9: price sync')
    job_progress.report(2, 9, label='Step 3 of 9: indicators')

    assert calls == [
        (0, 9, 'Step 1 of 9: fundamentals sync'),
        (1, 9, 'Step 2 of 9: price sync'),
        (2, 9, 'Step 3 of 9: indicators'),
    ]


def test_label_is_sticky_across_calls_that_omit_it():
    calls = []
    job_progress.bind(lambda current, total, label: calls.append((current, total, label)))

    job_progress.report(0, 9, label='Step 1 of 9: fundamentals sync')
    job_progress.report(9, 9)  # no label passed -- keeps the sticky one, not None

    assert calls[-1] == (9, 9, 'Step 1 of 9: fundamentals sync')


def test_new_explicit_label_overrides_the_sticky_one():
    calls = []
    job_progress.bind(lambda current, total, label: calls.append((current, total, label)))

    job_progress.report(0, 9, label='Step 1 of 9: fundamentals sync')
    job_progress.report(1, 1, label='Step 2 of 9: price sync')

    assert calls[-1] == (1, 1, 'Step 2 of 9: price sync')


def test_clear_stops_further_reporting():
    calls = []
    job_progress.bind(lambda current, total, label: calls.append((current, total, label)))
    job_progress.clear()

    job_progress.report(1, 10)

    assert calls == []


def test_bind_resets_sticky_label_from_a_previous_job():
    calls = []
    job_progress.bind(lambda current, total, label: calls.append((current, total, label)))
    job_progress.report(1, 1, label='Leftover label from job A')

    job_progress.bind(lambda current, total, label: calls.append((current, total, label)))
    job_progress.report(1, 1)  # a fresh job, no label passed -- must not inherit job A's

    assert calls[-1] == (1, 1, None)
