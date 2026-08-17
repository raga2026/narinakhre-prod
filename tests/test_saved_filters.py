from utils.saved_filters import (
    MAX_SAVED_FILTERS_PER_USER,
    delete_saved_stock_filter,
    list_saved_stock_filters,
    save_stock_filter,
)


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeFiltersDB:
    def __init__(self, rows=None):
        self.rows = rows or []
        self._next_id = 1

    def execute(self, sql, params=None):
        params = params or ()
        normalized = ' '.join(sql.split())

        if normalized.startswith('SELECT id, name, query_string, created_at FROM stocks_saved_filters WHERE admin_id=? ORDER BY created_at DESC'):
            admin_id, = params
            matches = [r for r in self.rows if r['admin_id'] == admin_id]
            matches.sort(key=lambda r: r['id'], reverse=True)
            return FakeCursor(matches)

        if normalized.startswith('SELECT COUNT(*) AS c FROM stocks_saved_filters WHERE admin_id=?'):
            admin_id, = params
            count = len([r for r in self.rows if r['admin_id'] == admin_id])
            return FakeCursor([{'c': count}])

        if normalized.startswith('INSERT INTO stocks_saved_filters'):
            admin_id, name, query_string = params
            self.rows.append({
                'id': self._next_id, 'admin_id': admin_id, 'name': name,
                'query_string': query_string, 'created_at': f'2026-08-1{self._next_id}',
            })
            self._next_id += 1
            return FakeCursor([])

        if normalized.startswith('SELECT id, name, query_string, created_at FROM stocks_saved_filters WHERE admin_id=? ORDER BY id DESC LIMIT 1'):
            admin_id, = params
            matches = [r for r in self.rows if r['admin_id'] == admin_id]
            matches.sort(key=lambda r: r['id'], reverse=True)
            return FakeCursor(matches[:1])

        if normalized.startswith('SELECT id FROM stocks_saved_filters WHERE id=? AND admin_id=?'):
            filter_id, admin_id = params
            matches = [r for r in self.rows if r['id'] == filter_id and r['admin_id'] == admin_id]
            return FakeCursor(matches[:1])

        if normalized.startswith('DELETE FROM stocks_saved_filters WHERE id=? AND admin_id=?'):
            filter_id, admin_id = params
            self.rows = [r for r in self.rows if not (r['id'] == filter_id and r['admin_id'] == admin_id)]
            return FakeCursor([])

        raise AssertionError(f'Unexpected SQL in test: {sql}')

    def commit(self):
        pass


def test_save_and_list_a_filter():
    db = FakeFiltersDB()
    row, error = save_stock_filter(db, admin_id=1, name='Cheap growth', query_string='pe_max=20&roce_min=15')

    assert error is None
    assert row['name'] == 'Cheap growth'

    listed = list_saved_stock_filters(db, admin_id=1)
    assert len(listed) == 1
    assert listed[0]['query_string'] == 'pe_max=20&roce_min=15'


def test_save_rejects_empty_name():
    db = FakeFiltersDB()
    row, error = save_stock_filter(db, admin_id=1, name='', query_string='pe_max=20')
    assert row is None
    assert 'Name' in error


def test_save_rejects_empty_query_string():
    db = FakeFiltersDB()
    row, error = save_stock_filter(db, admin_id=1, name='Nothing', query_string='')
    assert row is None
    assert 'Nothing to save' in error


def test_list_is_scoped_to_the_admin_id():
    db = FakeFiltersDB()
    save_stock_filter(db, admin_id=1, name='Mine', query_string='pe_max=20')
    save_stock_filter(db, admin_id=2, name='Theirs', query_string='pe_max=30')

    assert [f['name'] for f in list_saved_stock_filters(db, admin_id=1)] == ['Mine']
    assert [f['name'] for f in list_saved_stock_filters(db, admin_id=2)] == ['Theirs']


def test_list_returns_empty_for_no_admin_id():
    db = FakeFiltersDB()
    assert list_saved_stock_filters(db, admin_id=None) == []


def test_save_caps_at_max_per_user():
    db = FakeFiltersDB()
    for i in range(MAX_SAVED_FILTERS_PER_USER):
        row, error = save_stock_filter(db, admin_id=1, name=f'F{i}', query_string='pe_max=20')
        assert error is None

    row, error = save_stock_filter(db, admin_id=1, name='One too many', query_string='pe_max=20')
    assert row is None
    assert 'delete one first' in error


def test_delete_removes_only_the_owners_filter():
    db = FakeFiltersDB()
    save_stock_filter(db, admin_id=1, name='Mine', query_string='pe_max=20')
    filter_id = db.rows[0]['id']

    # Someone else's admin_id can't delete it.
    assert delete_saved_stock_filter(db, admin_id=2, filter_id=filter_id) is False
    assert len(db.rows) == 1

    assert delete_saved_stock_filter(db, admin_id=1, filter_id=filter_id) is True
    assert db.rows == []


def test_delete_returns_false_for_nonexistent_id():
    db = FakeFiltersDB()
    assert delete_saved_stock_filter(db, admin_id=1, filter_id=999) is False
