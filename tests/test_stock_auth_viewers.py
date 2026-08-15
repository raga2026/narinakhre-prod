from flask import Flask, session

from utils.stock_auth import (
    create_viewer_account,
    delete_viewer_account,
    list_viewers,
    stocks_watchlist_access_required,
)


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeViewerDB:
    """Minimal stand-in for app.py's SupabaseDB, just enough to run the
    exact SQL the viewer account functions in utils/stock_auth.py issue."""

    def __init__(self, rows=None):
        self.rows = rows or []  # list of dicts, 'id' assigned on insert
        self._next_id = 1

    def execute(self, sql, params=None):
        params = params or ()
        normalized = ' '.join(sql.split())

        if normalized.startswith('SELECT id FROM stocks_admin_users WHERE username=?'):
            username, = params
            matches = [r for r in self.rows if r['username'] == username]
            return FakeCursor(matches[:1])

        if normalized.startswith('INSERT INTO stocks_admin_users (username, password_hash, role, name, created_by, can_view_watchlist)'):
            username, password_hash, name, created_by, can_view_watchlist = params
            self.rows.append({
                'id': self._next_id, 'username': username, 'password_hash': password_hash,
                'role': 'viewer', 'name': name, 'created_by': created_by,
                'is_active': 1, 'can_view_watchlist': can_view_watchlist,
                'created_at': '2026-08-17',
            })
            self._next_id += 1
            return FakeCursor([])

        if normalized.startswith('SELECT id, username, name, role, is_active, can_view_watchlist, created_at'):
            username, = params
            matches = [r for r in self.rows if r['username'] == username]
            return FakeCursor(matches[:1])

        if normalized.startswith("SELECT id, username, name, is_active, can_view_watchlist, created_at FROM stocks_admin_users WHERE role='viewer'"):
            matches = [r for r in self.rows if r['role'] == 'viewer']
            matches.sort(key=lambda r: r['created_at'], reverse=True)
            return FakeCursor(matches)

        if normalized.startswith('SELECT id, role FROM stocks_admin_users WHERE id=?'):
            admin_id, = params
            matches = [r for r in self.rows if r['id'] == admin_id]
            return FakeCursor(matches[:1])

        if normalized.startswith('DELETE FROM stocks_admin_users WHERE id=? AND role=?'):
            admin_id, role = params
            self.rows = [r for r in self.rows if not (r['id'] == admin_id and r['role'] == role)]
            return FakeCursor([])

        raise AssertionError(f'Unexpected SQL in test: {sql}')

    def commit(self):
        pass


def test_create_viewer_account_defaults_can_view_watchlist_to_false():
    db = FakeViewerDB()
    row, password, error = create_viewer_account(db, 'a@example.com', 'A', created_by_id=1)

    assert error is None
    assert password is not None
    assert row['can_view_watchlist'] == 0


def test_create_viewer_account_can_grant_watchlist_access():
    db = FakeViewerDB()
    row, password, error = create_viewer_account(
        db, 'a@example.com', 'A', created_by_id=1, can_view_watchlist=True
    )

    assert error is None
    assert row['can_view_watchlist'] == 1


def test_list_viewers_includes_can_view_watchlist():
    db = FakeViewerDB()
    create_viewer_account(db, 'a@example.com', 'A', created_by_id=1, can_view_watchlist=True)
    create_viewer_account(db, 'b@example.com', 'B', created_by_id=1)

    viewers = {v['username']: v['can_view_watchlist'] for v in list_viewers(db)}
    assert viewers == {'a@example.com': 1, 'b@example.com': 0}


def test_delete_viewer_account_removes_the_row():
    db = FakeViewerDB()
    row, _, _ = create_viewer_account(db, 'a@example.com', 'A', created_by_id=1)

    deleted = delete_viewer_account(db, row['id'])

    assert deleted is True
    assert list_viewers(db) == []


def test_delete_viewer_account_returns_false_for_nonexistent_id():
    db = FakeViewerDB()
    assert delete_viewer_account(db, 999) is False


def test_delete_viewer_account_never_deletes_a_non_viewer_role():
    # Same safety pattern as toggle_viewer_active/toggle_child_admin_active
    # -- a super_admin/child_admin row must never be touched by this,
    # regardless of what id is passed in.
    db = FakeViewerDB(rows=[{
        'id': 1, 'username': 'boss', 'password_hash': 'x', 'role': 'super_admin',
        'name': None, 'created_by': None, 'is_active': 1, 'can_view_watchlist': 0,
        'created_at': '2026-01-01',
    }])

    deleted = delete_viewer_account(db, 1)

    assert deleted is False
    assert len(db.rows) == 1


def _build_test_app():
    app = Flask(__name__)
    app.secret_key = 'test-secret-key'

    @app.route('/stocks/login')
    def stocks_admin_login():
        return 'stocks login page', 200

    @app.route('/stocks/watchlist')
    @stocks_watchlist_access_required
    def stocks_watchlist():
        return 'watchlist content', 200

    return app


def test_watchlist_access_staff_always_allowed():
    app = _build_test_app()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['stocks_admin_id'] = 1
        sess['stocks_admin_role'] = 'child_admin'

    response = client.get('/stocks/watchlist')
    assert response.status_code == 200


def test_watchlist_access_viewer_with_flag_allowed():
    app = _build_test_app()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['stocks_admin_id'] = 2
        sess['stocks_admin_role'] = 'viewer'
        sess['stocks_can_view_watchlist'] = True

    response = client.get('/stocks/watchlist')
    assert response.status_code == 200


def test_watchlist_access_viewer_without_flag_blocked():
    app = _build_test_app()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['stocks_admin_id'] = 3
        sess['stocks_admin_role'] = 'viewer'
        sess['stocks_can_view_watchlist'] = False

    response = client.get('/stocks/watchlist')
    assert response.status_code == 403


def test_watchlist_access_logged_out_redirects_to_login():
    app = _build_test_app()
    client = app.test_client()

    response = client.get('/stocks/watchlist', follow_redirects=True)
    assert response.status_code == 200
    assert b'stocks login page' in response.data
