from flask import Flask, session

from stoqbell.utils.stock_auth import stocks_login_required, stocks_role_required, stocks_watchlist_access_required


def _build_test_app():
    """Standalone Flask app reusing the real decorators from
    utils/stock_auth.py, same pattern as test_stocks_auth_separation.py --
    never imports app.py (would need live Supabase/env vars at startup).

    Note: /stocks/settings/trading-mode and any execute-suggestion route
    don't exist anywhere in this codebase (grepped app.py to confirm), so
    they can't be tested here -- /stocks/users is staff-only;
    /stocks/watchlist uses stocks_watchlist_access_required (not a plain
    stocks_role_required), matching the real route in app.py, but that
    decorator now admits any logged-in viewer regardless of
    can_view_watchlist (that flag no longer gates anything -- see
    stocks_watchlist_access_required's own docstring; the staff-vs-viewer
    difference lives entirely in the template now)."""
    app = Flask(__name__)
    app.secret_key = 'test-secret-key'

    # endpoint= matches stoqbell/routes.py's real 'stocks' Blueprint
    # namespace, since the decorators under test call url_for() against it.
    @app.route('/stocks/login', endpoint='stocks.stocks_admin_login')
    def stocks_admin_login():
        return 'stocks login page', 200

    @app.route('/stocks/users')
    @stocks_role_required('super_admin')
    def stocks_users_manage():
        return 'viewer user management', 200

    @app.route('/stocks/watchlist')
    @stocks_watchlist_access_required
    def stocks_watchlist():
        return 'watchlist content', 200

    @app.route('/stocks/my/suggestions')
    @stocks_login_required
    def stocks_my_suggestions():
        return 'my suggestions content', 200

    return app


def test_viewer_cannot_access_stocks_users():
    app = _build_test_app()
    client = app.test_client()

    with client.session_transaction() as sess:
        sess['stocks_admin_id'] = 1
        sess['stocks_admin_role'] = 'viewer'

    response = client.get('/stocks/users')
    assert response.status_code == 403


def test_viewer_without_watchlist_flag_can_still_access_stocks_watchlist():
    # can_view_watchlist no longer gates anything -- every viewer can reach
    # the watchlist now, regardless of this flag.
    app = _build_test_app()
    client = app.test_client()

    with client.session_transaction() as sess:
        sess['stocks_admin_id'] = 1
        sess['stocks_admin_role'] = 'viewer'
        sess['stocks_can_view_watchlist'] = False

    response = client.get('/stocks/watchlist')
    assert response.status_code == 200


def test_viewer_with_watchlist_flag_granted_can_access_stocks_watchlist():
    app = _build_test_app()
    client = app.test_client()

    with client.session_transaction() as sess:
        sess['stocks_admin_id'] = 1
        sess['stocks_admin_role'] = 'viewer'
        sess['stocks_can_view_watchlist'] = True

    response = client.get('/stocks/watchlist')
    assert response.status_code == 200


def test_viewer_can_access_my_suggestions():
    app = _build_test_app()
    client = app.test_client()

    with client.session_transaction() as sess:
        sess['stocks_admin_id'] = 1
        sess['stocks_admin_role'] = 'viewer'

    response = client.get('/stocks/my/suggestions')
    assert response.status_code == 200
    assert b'my suggestions content' in response.data


def test_child_admin_can_still_access_watchlist():
    """Confirms the multi-role extension didn't break existing child_admin
    access to a route it always had -- this phase must not touch
    super_admin/child_admin behavior."""
    app = _build_test_app()
    client = app.test_client()

    with client.session_transaction() as sess:
        sess['stocks_admin_id'] = 2
        sess['stocks_admin_role'] = 'child_admin'

    response = client.get('/stocks/watchlist')
    assert response.status_code == 200


def test_logged_out_session_redirects_to_login_not_403():
    app = _build_test_app()
    client = app.test_client()

    response = client.get('/stocks/users', follow_redirects=True)
    assert response.status_code == 200
    assert b'stocks login page' in response.data
