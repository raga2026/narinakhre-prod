import os

import requests
from authlib.integrations.flask_client import OAuth

from .base import BaseAuthProvider


class GoogleAuthProvider(BaseAuthProvider):
    """Google OAuth 2.0 / OpenID Connect, via Authlib.

    Authlib performs the actual handshake -- authorization URL construction,
    CSRF state + OIDC nonce generation and storage in the Flask session, the
    code-for-token POST to Google, and ID-token signature verification -- so
    it isn't hand-rolled here. This class only adapts that handshake to the
    BaseAuthProvider interface so app.py's routes don't know which library
    or provider is behind them.

    This talks to Google directly and is unrelated to Supabase Auth --
    Supabase is used elsewhere in this app purely as a Postgres database.
    """

    def __init__(self, flask_app):
        oauth = OAuth(flask_app)
        self._client = oauth.register(
            name='google',
            client_id=os.environ.get('GOOGLE_CLIENT_ID', ''),
            client_secret=os.environ.get('GOOGLE_CLIENT_SECRET', ''),
            server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
            client_kwargs={'scope': 'openid email profile'},
        )

    def get_auth_url(self, redirect_uri):
        rv = self._client.create_authorization_url(redirect_uri)
        self._client.save_authorize_data(redirect_uri=redirect_uri, **rv)
        return rv['url']

    def exchange_code(self):
        # Reads back the state/nonce that get_auth_url() saved to the
        # session, validates the callback's `state` query param against it,
        # and exchanges `code` for tokens. Raises (e.g. Authlib's
        # MismatchingStateError or OAuthError) on any failure.
        return self._client.authorize_access_token()

    def get_user_info(self, token):
        userinfo = token.get('userinfo')
        if userinfo:
            return dict(userinfo)
        # Fallback for the rare case a token comes back without an inline
        # ID token's userinfo claims -- fetch it explicitly instead.
        resp = requests.get(
            'https://openidconnect.googleapis.com/v1/userinfo',
            headers={'Authorization': f"Bearer {token.get('access_token', '')}"},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
