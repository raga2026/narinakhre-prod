from flask import Flask, jsonify, redirect, request, session, url_for

from utils.stock_auth import has_valid_cron_secret

TEST_SECRET = 'test-cron-secret-value'


def _build_test_app():
    """A minimal standalone app, not app.py -- importing app.py in a unit
    test requires live Supabase/env vars and runs every startup DB call,
    which this test suite deliberately avoids (see test_stock_ingestion.py,
    test_kite_session.py, test_stocks_auth_separation.py). The auth check
    below mirrors admin_stocks_sync's real one in app.py line for line, but
    calls the real has_valid_cron_secret() from utils.stock_auth rather than
    reimplementing it, so the actual hmac check is genuinely exercised."""
    app = Flask(__name__)
    app.secret_key = 'test-secret-key'

    @app.route('/admin/stocks/login')
    def stocks_admin_login():
        return 'stocks login page', 200

    @app.route('/admin/stocks/sync', methods=['POST'])
    def admin_stocks_sync():
        if not has_valid_cron_secret(request.headers, TEST_SECRET) \
                and not session.get('stocks_admin_id'):
            return redirect(url_for('stocks_admin_login'))
        return jsonify({'status': 'ok'}), 200

    return app


def test_valid_cron_secret_header_without_session_succeeds():
    app = _build_test_app()
    client = app.test_client()

    response = client.post('/admin/stocks/sync', headers={'X-Cron-Secret': TEST_SECRET})

    assert response.status_code == 200
    assert response.get_json()['status'] == 'ok'


def test_no_session_and_no_valid_header_is_rejected():
    app = _build_test_app()
    client = app.test_client()

    response = client.post('/admin/stocks/sync', follow_redirects=True)

    assert response.status_code == 200  # followed the redirect to the login page
    assert b'stocks login page' in response.data


def test_wrong_header_value_without_session_is_also_rejected():
    app = _build_test_app()
    client = app.test_client()

    response = client.post('/admin/stocks/sync', headers={'X-Cron-Secret': 'not-the-real-secret'}, follow_redirects=True)

    assert b'stocks login page' in response.data


def test_existing_valid_browser_session_still_works_unchanged():
    app = _build_test_app()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['stocks_admin_id'] = 1

    response = client.post('/admin/stocks/sync')

    assert response.status_code == 200
    assert response.get_json()['status'] == 'ok'
