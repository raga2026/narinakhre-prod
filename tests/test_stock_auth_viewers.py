from flask import Flask, session

from stoqbell.utils.stock_auth import (
    PASSWORD_LENGTH,
    _generate_simple_password,
    change_own_password,
    create_viewer_account,
    delete_viewer_account,
    list_viewers,
    safe_stocks_next_url,
    set_viewer_plan,
    stocks_login_required,
    stocks_role_required,
    stocks_watchlist_access_required,
    toggle_viewer_pro,
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

        if normalized.startswith(
            'INSERT INTO stocks_admin_users (username, password_hash, role, name, created_by, can_view_watchlist, '
            'must_change_password, is_pro, trial_pending_password_change)'
        ):
            username, password_hash, name, created_by, can_view_watchlist, is_pro, trial_pending = params
            self.rows.append({
                'id': self._next_id, 'username': username, 'password_hash': password_hash,
                'role': 'viewer', 'name': name, 'created_by': created_by,
                'is_active': 1, 'can_view_watchlist': can_view_watchlist, 'must_change_password': 1,
                'is_pro': is_pro, 'subscription_status': 'none', 'subscription_current_period_end': None,
                'trial_ends_at': None, 'trial_pending_password_change': trial_pending,
                'created_at': '2026-08-17',
            })
            self._next_id += 1
            return FakeCursor([])

        if normalized.startswith('SELECT id, username, name, role, is_active, can_view_watchlist, must_change_password, created_at'):
            username, = params
            matches = [r for r in self.rows if r['username'] == username]
            return FakeCursor(matches[:1])

        if normalized.startswith("SELECT id, username, name, is_active, can_view_watchlist, must_change_password, is_pro, "
                                  "subscription_status, subscription_current_period_end, trial_ends_at, "
                                  "trial_pending_password_change, stocks_plan, created_at FROM stocks_admin_users WHERE role='viewer'"):
            matches = [r for r in self.rows if r['role'] == 'viewer']
            matches.sort(key=lambda r: r['created_at'], reverse=True)
            return FakeCursor(matches)

        if normalized.startswith('SELECT trial_pending_password_change FROM stocks_admin_users WHERE id=?'):
            admin_id, = params
            matches = [r for r in self.rows if r['id'] == admin_id]
            return FakeCursor(matches[:1])

        if normalized.startswith("UPDATE stocks_admin_users SET trial_pending_password_change=0"):
            admin_id, = params
            for r in self.rows:
                if r['id'] == admin_id:
                    r['trial_pending_password_change'] = 0
            return FakeCursor([])

        if normalized.startswith(
            "UPDATE stocks_admin_users SET is_active=1, subscription_status='trialing', "
            "trial_ends_at=NOW() + INTERVAL '7 days'"
        ):
            admin_id, = params
            for r in self.rows:
                if r['id'] == admin_id:
                    r['is_active'] = 1
                    r['subscription_status'] = 'trialing'
                    r['trial_ends_at'] = 'fake-trial-end'
            return FakeCursor([])

        if normalized.startswith('SELECT id, role, is_pro FROM stocks_admin_users WHERE id=?'):
            admin_id, = params
            matches = [r for r in self.rows if r['id'] == admin_id]
            return FakeCursor(matches[:1])

        if normalized.startswith('SELECT id, role FROM stocks_admin_users WHERE id=?'):
            admin_id, = params
            matches = [r for r in self.rows if r['id'] == admin_id]
            return FakeCursor(matches[:1])

        if normalized.startswith('DELETE FROM stocks_admin_users WHERE id=? AND role=?'):
            admin_id, role = params
            self.rows = [r for r in self.rows if not (r['id'] == admin_id and r['role'] == role)]
            return FakeCursor([])

        if normalized.startswith("UPDATE stocks_admin_users SET password_hash=?, must_change_password=0"):
            password_hash, admin_id = params
            for r in self.rows:
                if r['id'] == admin_id:
                    r['password_hash'] = password_hash
                    r['must_change_password'] = 0
            return FakeCursor([])

        if normalized.startswith('UPDATE stocks_admin_users SET is_pro=?, updated_at=NOW() WHERE id=?'):
            new_status, admin_id = params
            for r in self.rows:
                if r['id'] == admin_id:
                    r['is_pro'] = new_status
            return FakeCursor([])

        if normalized.startswith('UPDATE stocks_admin_users SET stocks_plan=?, updated_at=NOW() WHERE id=?'):
            plan, admin_id = params
            for r in self.rows:
                if r['id'] == admin_id:
                    r['stocks_plan'] = plan
            return FakeCursor([])

        raise AssertionError(f'Unexpected SQL in test: {sql}')

    def commit(self):
        pass


def test_generated_password_has_no_ambiguous_characters_or_symbols():
    # Emailed in plaintext and sometimes read aloud/typed by hand -- must
    # never contain 0/O/1/l/I (easily confused) or any symbol.
    for _ in range(200):
        password = _generate_simple_password()
        assert len(password) == PASSWORD_LENGTH
        assert password.isalnum()
        assert not any(c in password for c in '0O1lI')


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


def test_create_viewer_account_is_pro_by_default():
    # Manually-added viewers get full free access -- only self-serve/Google
    # signups (utils/stocks_subscription.create_pending_subscriber,
    # utils/stock_auth.create_pending_google_subscriber) start as not-pro.
    db = FakeViewerDB()
    row, _, _ = create_viewer_account(db, 'a@example.com', 'A', created_by_id=1)
    assert db.rows[0]['is_pro'] == 1


def test_create_viewer_account_with_start_trial_is_not_pro_and_flags_pending_trial():
    db = FakeViewerDB()
    row, _, error = create_viewer_account(db, 'a@example.com', 'A', created_by_id=1, start_trial=True)

    assert error is None
    assert db.rows[0]['is_pro'] == 0
    assert db.rows[0]['trial_pending_password_change'] == 1
    assert db.rows[0]['subscription_status'] == 'none'  # not trialing yet -- only on password change


def test_password_change_activates_the_pending_trial():
    db = FakeViewerDB()
    create_viewer_account(db, 'a@example.com', 'A', created_by_id=1, start_trial=True)
    admin_id = db.rows[0]['id']

    ok, error, trial_started = change_own_password(db, admin_id, 'newpassword123')

    assert ok is True
    assert trial_started is True
    assert db.rows[0]['subscription_status'] == 'trialing'
    assert db.rows[0]['trial_ends_at'] is not None
    assert db.rows[0]['trial_pending_password_change'] == 0


def test_password_change_without_pending_trial_does_not_start_one():
    db = FakeViewerDB()
    create_viewer_account(db, 'a@example.com', 'A', created_by_id=1)  # no start_trial -- normal Pro viewer
    admin_id = db.rows[0]['id']

    ok, error, trial_started = change_own_password(db, admin_id, 'newpassword123')

    assert ok is True
    assert trial_started is False
    assert db.rows[0]['subscription_status'] == 'none'


def test_toggle_viewer_pro_flips_the_flag():
    db = FakeViewerDB()
    create_viewer_account(db, 'a@example.com', 'A', created_by_id=1)
    admin_id = db.rows[0]['id']

    assert toggle_viewer_pro(db, admin_id) is True
    assert db.rows[0]['is_pro'] == 0

    assert toggle_viewer_pro(db, admin_id) is True
    assert db.rows[0]['is_pro'] == 1


def test_toggle_viewer_pro_returns_false_for_nonexistent_id():
    db = FakeViewerDB()
    assert toggle_viewer_pro(db, 999) is False


def test_toggle_viewer_pro_never_touches_a_non_viewer_role():
    db = FakeViewerDB(rows=[{
        'id': 1, 'username': 'boss', 'password_hash': 'x', 'role': 'super_admin',
        'name': None, 'created_by': None, 'is_active': 1, 'can_view_watchlist': 0,
        'is_pro': 0, 'created_at': '2026-01-01',
    }])
    assert toggle_viewer_pro(db, 1) is False
    assert db.rows[0]['is_pro'] == 0


def test_set_viewer_plan_switches_to_starters():
    db = FakeViewerDB()
    create_viewer_account(db, 'a@example.com', 'A', created_by_id=1)
    admin_id = db.rows[0]['id']

    assert set_viewer_plan(db, admin_id, 'starters') is True
    assert db.rows[0]['stocks_plan'] == 'starters'

    assert set_viewer_plan(db, admin_id, 'standard') is True
    assert db.rows[0]['stocks_plan'] == 'standard'


def test_set_viewer_plan_rejects_an_unrecognized_plan():
    db = FakeViewerDB()
    create_viewer_account(db, 'a@example.com', 'A', created_by_id=1)
    admin_id = db.rows[0]['id']
    assert set_viewer_plan(db, admin_id, 'deluxe') is False


def test_set_viewer_plan_returns_false_for_nonexistent_id():
    db = FakeViewerDB()
    assert set_viewer_plan(db, 999, 'starters') is False


def test_set_viewer_plan_never_touches_a_non_viewer_role():
    db = FakeViewerDB(rows=[{
        'id': 1, 'username': 'boss', 'password_hash': 'x', 'role': 'super_admin',
        'name': None, 'created_by': None, 'is_active': 1, 'can_view_watchlist': 0,
        'is_pro': 0, 'stocks_plan': 'standard', 'created_at': '2026-01-01',
    }])
    assert set_viewer_plan(db, 1, 'starters') is False
    assert db.rows[0]['stocks_plan'] == 'standard'


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

    # endpoint= matches the real app's Blueprint-namespaced names (Nari
    # Nakhre Stocks moved onto a 'stocks' Blueprint -- see stoqbell/routes.py
    # -- so stock_auth.py's url_for() calls now target 'stocks.stocks_x',
    # not bare 'stocks_x').
    @app.route('/stocks/login', endpoint='stocks.stocks_admin_login')
    def stocks_admin_login():
        return 'stocks login page', 200

    @app.route('/stocks/change-password', endpoint='stocks.stocks_change_password')
    @stocks_login_required
    def stocks_change_password():
        return 'change password page', 200

    @app.route('/stocks/watchlist', endpoint='stocks.stocks_watchlist')
    @stocks_watchlist_access_required
    def stocks_watchlist():
        return 'watchlist content', 200

    @app.route('/stocks/users', endpoint='stocks.stocks_users_manage')
    @stocks_role_required('super_admin')
    def stocks_users_manage():
        return 'viewer user management', 200

    @app.route('/stocks/my/suggestions', endpoint='stocks.stocks_my_suggestions')
    @stocks_login_required
    def stocks_my_suggestions():
        return 'my suggestions content', 200

    @app.route('/stocks/universe/<int:universe_id>', endpoint='stocks.stocks_universe_detail')
    @stocks_login_required
    def stocks_universe_detail(universe_id):
        return f'universe detail {universe_id}', 200

    return app


def test_watchlist_access_staff_always_allowed():
    app = _build_test_app()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['stocks_admin_id'] = 1
        sess['stocks_admin_role'] = 'child_admin'

    response = client.get('/stocks/watchlist')
    assert response.status_code == 200


def test_watchlist_access_any_viewer_allowed_regardless_of_flag():
    # can_view_watchlist no longer gates anything -- every viewer can reach
    # the watchlist now (the template, not this decorator, is what hides
    # the Recommended column from them).
    app = _build_test_app()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['stocks_admin_id'] = 2
        sess['stocks_admin_role'] = 'viewer'
        sess['stocks_can_view_watchlist'] = True

    response = client.get('/stocks/watchlist')
    assert response.status_code == 200


def test_watchlist_access_viewer_without_flag_also_allowed():
    app = _build_test_app()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['stocks_admin_id'] = 3
        sess['stocks_admin_role'] = 'viewer'
        sess['stocks_can_view_watchlist'] = False

    response = client.get('/stocks/watchlist')
    assert response.status_code == 200


def test_watchlist_access_logged_out_redirects_to_login():
    app = _build_test_app()
    client = app.test_client()

    response = client.get('/stocks/watchlist', follow_redirects=True)
    assert response.status_code == 200
    assert b'stocks login page' in response.data


def test_change_own_password_success_clears_the_flag():
    db = FakeViewerDB(rows=[{
        'id': 1, 'username': 'a@example.com', 'password_hash': 'old-hash', 'role': 'viewer',
        'name': None, 'created_by': None, 'is_active': 1, 'can_view_watchlist': 0,
        'must_change_password': 1, 'created_at': '2026-01-01',
    }])

    ok, error, trial_started = change_own_password(db, 1, 'newpassword123')

    assert ok is True
    assert error is None
    assert trial_started is False
    assert db.rows[0]['must_change_password'] == 0
    assert db.rows[0]['password_hash'] != 'old-hash'


def test_change_own_password_rejects_too_short_password():
    db = FakeViewerDB(rows=[{
        'id': 1, 'username': 'a@example.com', 'password_hash': 'old-hash', 'role': 'viewer',
        'name': None, 'created_by': None, 'is_active': 1, 'can_view_watchlist': 0,
        'must_change_password': 1, 'created_at': '2026-01-01',
    }])

    ok, error, trial_started = change_own_password(db, 1, 'short')

    assert ok is False
    assert '8 characters' in error
    assert trial_started is False
    # Row must be untouched -- the DB call is never even made.
    assert db.rows[0]['password_hash'] == 'old-hash'
    assert db.rows[0]['must_change_password'] == 1


# --- forced password change gate, checked by every access decorator -----

def test_viewer_who_must_change_password_is_redirected_from_watchlist():
    app = _build_test_app()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['stocks_admin_id'] = 1
        sess['stocks_admin_role'] = 'viewer'
        sess['stocks_can_view_watchlist'] = True  # would otherwise be allowed
        sess['stocks_must_change_password'] = True

    response = client.get('/stocks/watchlist', follow_redirects=True)
    assert response.status_code == 200
    assert b'change password page' in response.data


def test_super_admin_who_must_change_password_is_redirected_from_users_page():
    # The gate applies regardless of role -- stocks_role_required enforces
    # it too, not just stocks_watchlist_access_required.
    app = _build_test_app()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['stocks_admin_id'] = 1
        sess['stocks_admin_role'] = 'super_admin'
        sess['stocks_must_change_password'] = True

    response = client.get('/stocks/users', follow_redirects=True)
    assert response.status_code == 200
    assert b'change password page' in response.data


def test_must_change_password_redirects_from_stocks_login_required_routes_too():
    app = _build_test_app()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['stocks_admin_id'] = 1
        sess['stocks_admin_role'] = 'viewer'
        sess['stocks_must_change_password'] = True

    response = client.get('/stocks/my/suggestions', follow_redirects=True)
    assert response.status_code == 200
    assert b'change password page' in response.data


def test_change_password_page_itself_does_not_redirect_loop():
    # Regression test: _must_change_password_redirect() used to compare
    # request.endpoint against the bare 'stocks_change_password', but a
    # route registered on stocks_bp always reports the Blueprint-qualified
    # 'stocks.stocks_change_password' -- that comparison never matched, so
    # visiting the change-password page itself while must_change_password
    # was set redirected back to the exact same page forever (an
    # ERR_TOO_MANY_REDIRECTS loop), for every account with a temporary
    # password -- precisely the one moment they actually need this page.
    app = _build_test_app()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['stocks_admin_id'] = 1
        sess['stocks_admin_role'] = 'viewer'
        sess['stocks_must_change_password'] = True

    response = client.get('/stocks/change-password', follow_redirects=False)
    assert response.status_code == 200
    assert b'change password page' in response.data


def test_once_flag_is_cleared_normal_access_resumes():
    app = _build_test_app()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['stocks_admin_id'] = 1
        sess['stocks_admin_role'] = 'viewer'
        sess['stocks_must_change_password'] = False

    response = client.get('/stocks/my/suggestions')
    assert response.status_code == 200
    assert b'my suggestions content' in response.data


def test_safe_stocks_next_url_accepts_only_same_site_stocks_paths():
    assert safe_stocks_next_url('/stocks/universe/5') == '/stocks/universe/5'
    assert safe_stocks_next_url('') is None
    assert safe_stocks_next_url(None) is None
    assert safe_stocks_next_url('/admin/dashboard') is None  # not a /stocks/ path
    assert safe_stocks_next_url('https://evil.example.com/stocks/x') is None  # absolute URL
    assert safe_stocks_next_url('//evil.example.com/stocks/x') is None  # protocol-relative


def test_logged_out_request_redirects_to_login_with_next_pointing_back():
    # Regression: a "View full analysis" email link clicked while logged
    # out must come back to that same page after login, not just the
    # generic default -- stocks_login_required is what carries the
    # originally-requested path through as ?next=.
    app = _build_test_app()
    client = app.test_client()

    response = client.get('/stocks/universe/42')
    assert response.status_code == 302
    assert response.headers['Location'] == '/stocks/login?next=/stocks/universe/42'


def test_role_required_also_carries_next_through_to_login():
    # Same fix as stocks_login_required, extended to stocks_role_required --
    # matters for e.g. a trading-alert email linking straight to a
    # super_admin-only page (buy confirmation, auto-trader dashboard).
    app = _build_test_app()
    client = app.test_client()

    response = client.get('/stocks/users')
    assert response.status_code == 302
    assert response.headers['Location'] == '/stocks/login?next=/stocks/users'
