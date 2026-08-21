from flask import Flask, jsonify, request

from stoqbell.utils.stock_auth import legacy_stocks_redirect, stocks_login_required

# A minimal standalone app, not app.py -- importing app.py in a unit test
# requires live Supabase/env vars and runs every startup DB call, which this
# test suite deliberately avoids (see test_stock_ingestion.py,
# test_kite_session.py, test_stocks_auth_separation.py,
# test_admin_stocks_sync_auth.py). Registers the real
# legacy_stocks_redirect() and stocks_login_required() from
# utils.stock_auth, wired up the same way app.py wires them, so the actual
# redirect/auth code is genuinely exercised, not reimplemented.


def _build_test_app():
    app = Flask(__name__)
    app.secret_key = 'test-secret-key'

    # endpoint= matches stoqbell/routes.py's real 'stocks' Blueprint
    # namespace, since stocks_login_required's url_for() targets that.
    @app.route('/stocks/login', endpoint='stocks.stocks_admin_login')
    def stocks_admin_login():
        return 'stocks login page', 200

    @app.route('/stocks/dashboard')
    @stocks_login_required
    def stocks_admin_dashboard():
        return 'stocks dashboard content', 200

    @app.route('/stocks/kite/callback')
    def stocks_kite_callback():
        return f'callback received: {request.query_string.decode()}', 200

    @app.route('/stocks/sync', methods=['POST'])
    def admin_stocks_sync():
        return jsonify({'method_received': request.method}), 200

    app.add_url_rule(
        '/admin/stocks/dashboard',
        endpoint='legacy_stocks_admin_dashboard',
        view_func=legacy_stocks_redirect('stocks_admin_dashboard', code=301),
        methods=['GET'],
    )
    app.add_url_rule(
        '/admin/stocks/kite/callback',
        endpoint='legacy_stocks_kite_callback',
        view_func=legacy_stocks_redirect('stocks_kite_callback', code=301),
        methods=['GET'],
    )
    app.add_url_rule(
        '/admin/stocks/sync',
        endpoint='legacy_admin_stocks_sync',
        view_func=legacy_stocks_redirect('admin_stocks_sync', code=308),
        methods=['POST'],
    )

    return app


def test_old_dashboard_path_redirects_to_new_path():
    app = _build_test_app()
    client = app.test_client()

    response = client.get('/admin/stocks/dashboard')

    assert response.status_code == 301
    assert response.headers['Location'].endswith('/stocks/dashboard')


def test_old_dashboard_redirect_still_enforces_login_when_followed():
    app = _build_test_app()
    client = app.test_client()

    response = client.get('/admin/stocks/dashboard', follow_redirects=True)

    assert response.status_code == 200
    assert b'stocks login page' in response.data
    assert b'stocks dashboard content' not in response.data


def test_new_dashboard_path_works_with_a_valid_session():
    app = _build_test_app()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['stocks_admin_id'] = 1

    response = client.get('/stocks/dashboard')

    assert response.status_code == 200
    assert b'stocks dashboard content' in response.data


def test_legacy_kite_callback_redirect_preserves_query_string():
    app = _build_test_app()
    client = app.test_client()

    response = client.get('/admin/stocks/kite/callback?request_token=abc123&status=success')

    assert response.status_code == 301
    assert 'request_token=abc123' in response.headers['Location']
    assert 'status=success' in response.headers['Location']


def test_legacy_sync_redirect_preserves_post_method_via_308():
    app = _build_test_app()
    client = app.test_client()

    response = client.post('/admin/stocks/sync', follow_redirects=True)

    assert response.status_code == 200
    assert response.get_json()['method_received'] == 'POST'
