"""Named, reusable filter presets for /stocks/universe (see app.py's
stocks_universe_list, stocks_universe_filters_save, stocks_universe_filters_delete)
-- lets any logged-in Stocks user (any role) save the current combination of
search/industry/crossover/PE/PEG/OPM/ROCE/ROA/RSI filters under a name, and
re-apply it later with one click instead of rebuilding it by hand each time.

Deliberately dumb storage: the entire filter combination is kept as the raw
querystring it came from (e.g. "pe_max=20&roce_min=15&cross=golden_cross"),
not decomposed into individual columns -- re-applying a saved filter is just
a redirect to /stocks/universe?<that querystring>, and adding a new filter
field to the universe page later needs no migration here at all, since this
table never needed to know the individual field names in the first place."""

STOCKS_SAVED_FILTERS_TABLES_SQL = [
    '''CREATE TABLE IF NOT EXISTS stocks_saved_filters (
        id BIGSERIAL PRIMARY KEY,
        admin_id BIGINT NOT NULL REFERENCES stocks_admin_users(id),
        name TEXT NOT NULL,
        query_string TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )'''
]

MAX_SAVED_FILTERS_PER_USER = 20


def initialize_saved_filters_table_if_needed(client):
    for sql in STOCKS_SAVED_FILTERS_TABLES_SQL:
        try:
            client.rpc('execute_sql', {'query': sql}).execute()
        except Exception as e:
            print(f'Saved filters table init warning (may already exist): {e}')


def list_saved_stock_filters(db, admin_id):
    if not admin_id:
        return []
    return db.execute(
        'SELECT id, name, query_string, created_at FROM stocks_saved_filters '
        'WHERE admin_id=? ORDER BY created_at DESC',
        (admin_id,)
    ).fetchall()


def save_stock_filter(db, admin_id, name, query_string):
    """Returns (row, error_message) -- error_message is None on success.
    Caps at MAX_SAVED_FILTERS_PER_USER per admin_id, oldest-first, so one
    person clicking "save" repeatedly can't grow this table without bound
    -- this is a convenience list, not an archive."""
    name = (name or '').strip()
    if not name:
        return None, 'Name is required.'
    query_string = (query_string or '').strip()
    if not query_string:
        return None, 'Nothing to save -- apply at least one filter first.'

    existing_count = db.execute(
        'SELECT COUNT(*) AS c FROM stocks_saved_filters WHERE admin_id=?', (admin_id,)
    ).fetchone()['c']
    if existing_count >= MAX_SAVED_FILTERS_PER_USER:
        return None, f'You already have {MAX_SAVED_FILTERS_PER_USER} saved filters -- delete one first.'

    db.execute(
        'INSERT INTO stocks_saved_filters (admin_id, name, query_string) VALUES (?, ?, ?)',
        (admin_id, name, query_string)
    )
    db.commit()

    row = db.execute(
        'SELECT id, name, query_string, created_at FROM stocks_saved_filters '
        'WHERE admin_id=? ORDER BY id DESC LIMIT 1',
        (admin_id,)
    ).fetchone()
    return row, None


def delete_saved_stock_filter(db, admin_id, filter_id):
    """Only ever deletes a row actually owned by admin_id -- returns True if
    a row was deleted, False if not found or owned by someone else (so one
    user can never delete another's saved filter just by guessing/incrementing
    an id)."""
    row = db.execute(
        'SELECT id FROM stocks_saved_filters WHERE id=? AND admin_id=?', (filter_id, admin_id)
    ).fetchone()
    if not row:
        return False
    db.execute('DELETE FROM stocks_saved_filters WHERE id=? AND admin_id=?', (filter_id, admin_id))
    db.commit()
    return True
