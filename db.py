"""Shared Supabase database access layer.

Used by both the storefront (app.py) and the Nari Nakhre Stocks module
(stoqbell/) -- pure Supabase REST plumbing with zero business logic, so it
lives at the repo root rather than under either side. If Stocks is ever
split onto its own server/repo, this file is one of the few things that
needs to be copied over as-is (along with utils/credential_crypto.py and
auth_providers/) rather than being specific to one side.
"""
import json
import logging
import os

from flask import g
from supabase import create_client, Client as SupabaseClient

logger = logging.getLogger(__name__)


def normalize_supabase_url(raw_url):
    """Strip /rest/v1 suffix if accidentally included in env var."""
    base = (raw_url or '').strip().rstrip('/')
    for suffix in ['/rest/v1', '/rest/v1/']:
        if base.endswith(suffix.rstrip('/')):
            base = base[:-len(suffix.rstrip('/'))]
    return base.rstrip('/')


# Supabase client for database operations
_supabase_client: SupabaseClient = None


def get_supabase():
    """Lazily builds and caches the Supabase client. Reads SUPABASE_URL/KEY
    from the environment on first call rather than at module import time --
    app.py's .env loader (load_env_file) runs after its own imports, so
    reading these eagerly at import time (like the pre-extraction code in
    app.py used to, further down the same file) would see them unset."""
    global _supabase_client
    if _supabase_client is None:
        url = normalize_supabase_url(os.environ.get('SUPABASE_URL', ''))
        key = os.environ.get('SUPABASE_KEY', '')
        _supabase_client = create_client(url, key)
    return _supabase_client


class SupabaseDB:
    """
    Wrapper around the Supabase REST API that mimics the sqlite3
    connection interface used throughout the app.
    Uses Supabase PostgREST for SELECT queries and
    direct SQL execution via the rpc/sql endpoint for
    INSERT, UPDATE, DELETE, CREATE TABLE operations.
    """

    def __init__(self, client):
        self._client = client
        self._pending = []

    def execute(self, sql, params=None):
        return SupabaseCursor(self._client, sql, params)

    def commit(self):
        pass  # Supabase REST is auto-commit

    def rollback(self):
        pass

    def close(self):
        pass


class SupabaseCursor:
    """
    Executes SQL via Supabase REST API.
    Uses the execute_sql RPC and correctly unwraps the JSONB response.
    """

    def __init__(self, client, sql, params=None):
        self._client = client
        self._rows = []
        self._rowcount = 0
        self._execute(sql.strip(), params or ())

    def _format_sql(self, sql, params):
        if not params:
            return sql
        parts = sql.split('?')
        if len(parts) - 1 != len(params):
            return sql
        result = ''
        for i, part in enumerate(parts):
            result += part
            if i < len(params):
                val = params[i]
                if val is None:
                    result += 'NULL'
                elif isinstance(val, bool):
                    result += '1' if val else '0'
                elif isinstance(val, (int, float)):
                    result += str(val)
                else:
                    escaped = str(val).replace("'", "''")
                    result += f"'{escaped}'"
        return result

    def _execute(self, sql, params):
        formatted = self._format_sql(sql, params)
        sql_upper = formatted.strip().upper()

        try:
            # For non-SELECT statements use execute_sql RPC
            if not sql_upper.startswith('SELECT') and not sql_upper.startswith('WITH'):
                self._client.rpc('execute_sql', {'query': formatted}).execute()
                self._rows = []
                return

            # For SELECT use execute_sql and parse the JSONB response
            result = self._client.rpc('execute_sql', {'query': formatted}).execute()
            raw = result.data

            if raw is None:
                self._rows = []
                return

            # Supabase returns: [{'execute_sql': '[{row1}, {row2}]'}]
            if isinstance(raw, list) and len(raw) > 0:
                first = raw[0]
                if isinstance(first, dict) and 'execute_sql' in first:
                    inner = first['execute_sql']
                    if inner is None:
                        self._rows = []
                    elif isinstance(inner, list):
                        self._rows = inner
                    elif isinstance(inner, str):
                        try:
                            parsed = json.loads(inner)
                            self._rows = parsed if isinstance(parsed, list) else []
                        except Exception:
                            self._rows = []
                    else:
                        self._rows = []
                    return

            # Fallback: raw is already a list of rows
            if isinstance(raw, list):
                self._rows = raw
            else:
                self._rows = []

        except Exception as e:
            logger.error(f'SupabaseCursor error: {e} | SQL: {formatted[:300]}')
            self._rows = []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows

    def __iter__(self):
        return iter(self._rows)


def get_db():
    if 'db' not in g:
        g.db = SupabaseDB(get_supabase())
    return g.db


def close_db(error=None):
    g.pop('db', None)
