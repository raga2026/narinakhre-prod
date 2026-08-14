from flask import Flask, session

from utils.stock_auth import stocks_login_required


def _mirrors_admin_required(view_func):
    """Reimplements app.py's admin_required (session.get('is_admin') is
    True) exactly, since that decorator lives in app.py itself and
    importing app.py in a unit test would require live Supabase/env vars
    and run every startup DB call -- this suite deliberately never imports
    app.py, same as test_stock_ingestion.py and test_kite_session.py. Full
    end-to-end coverage of the real storefront login lives in
    tests/conftest.py's Selenium E2E tests instead."""
    from functools import wraps
    from flask import redirect, url_for

    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if session.get('is_admin') is not True:
            return redirect(url_for('admin_login'))
        return view_func(*args, **kwargs)
    return wrapped


def _build_test_app():
    app = Flask(__name__)
    app.secret_key = 'test-secret-key'

    @app.route('/admin/login')
    def admin_login():
        return 'storefront login page', 200

    @app.route('/admin/stocks/login')
    def stocks_admin_login():
        return 'stocks login page', 200

    @app.route('/admin/dashboard')
    @_mirrors_admin_required
    def admin_dashboard():
        return 'storefront admin content', 200

    @app.route('/admin/stocks/dashboard')
    @stocks_login_required
    def stocks_admin_dashboard():
        return 'stocks admin content', 200

    return app


def test_storefront_session_cannot_reach_stocks_routes():
    app = _build_test_app()
    client = app.test_client()

    with client.session_transaction() as sess:
        sess['is_admin'] = True  # storefront-authenticated, no stocks keys at all

    response = client.get('/admin/stocks/dashboard', follow_redirects=True)
    assert response.status_code == 200
    assert b'stocks admin content' not in response.data
    assert b'stocks login page' in response.data


def test_stocks_session_cannot_reach_storefront_routes():
    app = _build_test_app()
    client = app.test_client()

    with client.session_transaction() as sess:
        sess['stocks_admin_id'] = 1  # stocks-authenticated, no is_admin at all
        sess['stocks_admin_role'] = 'child_admin'

    response = client.get('/admin/dashboard', follow_redirects=True)
    assert response.status_code == 200
    assert b'storefront admin content' not in response.data
    assert b'storefront login page' in response.data
