import threading

from utils.background_jobs import get_job_status, start_background_job


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeJobsDB:
    """Minimal stand-in for app.py's SupabaseDB, just enough to run the
    exact SQL background_jobs.py issues. Thread-safe with a plain lock --
    real usage has each thread build its own DB handle, but the fake
    in-memory store here is shared across the main thread and the
    background thread within a single test, so it needs one."""

    def __init__(self):
        self.rows = []  # list of dicts, 'id' assigned on insert
        self._next_id = 1
        self._lock = threading.Lock()

    def execute(self, sql, params=None):
        params = params or ()
        normalized = ' '.join(sql.split())

        with self._lock:
            if normalized.startswith("SELECT id FROM stock_background_jobs WHERE job_name=? AND status='running'"):
                job_name, = params
                matches = [r for r in self.rows if r['job_name'] == job_name and r['status'] == 'running']
                return FakeCursor(matches[:1])

            if normalized.startswith('INSERT INTO stock_background_jobs'):
                job_name, status, triggered_by = params
                row = {
                    'id': self._next_id, 'job_name': job_name, 'status': status,
                    'result_json': None, 'error_message': None,
                    'started_at': 'now', 'finished_at': None, 'triggered_by': triggered_by,
                }
                self._next_id += 1
                self.rows.append(row)
                return FakeCursor([])

            if normalized.startswith("SELECT id FROM stock_background_jobs WHERE job_name=? AND status='running' ORDER BY id DESC"):
                job_name, = params
                matches = [r for r in self.rows if r['job_name'] == job_name and r['status'] == 'running']
                matches.sort(key=lambda r: r['id'], reverse=True)
                return FakeCursor(matches[:1])

            if normalized.startswith("UPDATE stock_background_jobs SET status='done'"):
                result_json, job_id = params
                for r in self.rows:
                    if r['id'] == job_id:
                        r['status'] = 'done'
                        r['result_json'] = result_json
                        r['finished_at'] = 'now'
                return FakeCursor([])

            if normalized.startswith("UPDATE stock_background_jobs SET status='error'"):
                error_message, job_id = params
                for r in self.rows:
                    if r['id'] == job_id:
                        r['status'] = 'error'
                        r['error_message'] = error_message
                        r['finished_at'] = 'now'
                return FakeCursor([])

            if normalized.startswith('SELECT status, result_json, error_message, started_at, finished_at'):
                job_name, = params
                matches = [r for r in self.rows if r['job_name'] == job_name]
                matches.sort(key=lambda r: r['id'], reverse=True)
                return FakeCursor(matches[:1])

            raise AssertionError(f'Unexpected SQL in test: {sql}')

    def commit(self):
        pass


def test_never_run_job_reports_never_run():
    db = FakeJobsDB()
    assert get_job_status(db, 'price_sync') == {
        'status': 'never_run', 'result': None, 'error': None,
        'started_at': None, 'finished_at': None,
    }


def test_successful_job_reports_done_with_parsed_result():
    db = FakeJobsDB()

    def target_fn(job_db):
        return {'inserted': 5, 'failed': 0}

    result = start_background_job(db, build_db=lambda: db, job_name='price_sync',
                                    target_fn=target_fn, triggered_by_id=1)
    assert result['started'] is True
    result['thread'].join(timeout=5)

    status = get_job_status(db, 'price_sync')
    assert status['status'] == 'done'
    assert status['result'] == {'inserted': 5, 'failed': 0}
    assert status['error'] is None


def test_failing_job_reports_error_status_and_message():
    db = FakeJobsDB()

    def target_fn(job_db):
        raise RuntimeError('Kite API is down')

    result = start_background_job(db, build_db=lambda: db, job_name='price_sync',
                                    target_fn=target_fn, triggered_by_id=1)
    result['thread'].join(timeout=5)

    status = get_job_status(db, 'price_sync')
    assert status['status'] == 'error'
    assert status['error'] == 'Kite API is down'
    assert status['result'] is None


def test_second_start_while_one_is_running_does_not_spawn_a_duplicate():
    db = FakeJobsDB()
    release = threading.Event()

    def slow_target_fn(job_db):
        release.wait(timeout=5)
        return {'ok': True}

    first = start_background_job(db, build_db=lambda: db, job_name='price_sync',
                                   target_fn=slow_target_fn, triggered_by_id=1)
    assert first['started'] is True

    second = start_background_job(db, build_db=lambda: db, job_name='price_sync',
                                    target_fn=slow_target_fn, triggered_by_id=1)
    assert second['started'] is False
    assert second['job_id'] == first['job_id']

    release.set()
    first['thread'].join(timeout=5)


def test_different_job_names_run_independently():
    db = FakeJobsDB()

    a = start_background_job(db, build_db=lambda: db, job_name='price_sync',
                              target_fn=lambda d: {'a': 1}, triggered_by_id=1)
    b = start_background_job(db, build_db=lambda: db, job_name='fundamentals_sync',
                              target_fn=lambda d: {'b': 2}, triggered_by_id=1)

    assert a['started'] is True
    assert b['started'] is True
    a['thread'].join(timeout=5)
    b['thread'].join(timeout=5)

    assert get_job_status(db, 'price_sync')['result'] == {'a': 1}
    assert get_job_status(db, 'fundamentals_sync')['result'] == {'b': 2}
