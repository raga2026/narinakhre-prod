from stoqbell.utils.email_delivery_log import get_delivery_log, list_delivery_dates, record_delivery


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeDeliveryDB:
    """Minimal in-memory stand-in -- stores every INSERT as a row dict and
    answers list_delivery_dates/get_delivery_log's own SELECTs by
    replicating their SQL in Python, same "fake mirrors the real query's
    semantics, not its exact text" approach test_stock_alerting.py and
    friends already use for aggregate/GROUP BY queries."""

    def __init__(self, users=None):
        self.rows = []  # each: {id, source, suggestion_date, recipient_id, email, status, error_detail, sent_at}
        self.users = users or {}  # {id: name}
        self._next_id = 1
        self._next_sent_at = 1  # monotonic stand-in for NOW() ordering

    def execute(self, sql, params=None):
        normalized = ' '.join(sql.split())
        params = params or ()

        if normalized.startswith('INSERT INTO stock_email_deliveries'):
            source, suggestion_date, recipient_id, email, status, error_detail = params
            self.rows.append({
                'id': self._next_id, 'source': source, 'suggestion_date': suggestion_date,
                'recipient_id': recipient_id, 'email': email, 'status': status,
                'error_detail': error_detail, 'sent_at': self._next_sent_at,
            })
            self._next_id += 1
            self._next_sent_at += 1
            return FakeCursor([])

        if normalized.startswith('SELECT source, suggestion_date,'):
            groups = {}
            for r in self.rows:
                key = (r['source'], r['suggestion_date'])
                g = groups.setdefault(key, {'sent': 0, 'failed': 0, 'last_attempt_at': 0})
                g['sent'] += 1 if r['status'] == 'sent' else 0
                g['failed'] += 1 if r['status'] == 'failed' else 0
                g['last_attempt_at'] = max(g['last_attempt_at'], r['sent_at'])
            ordered = sorted(groups.items(), key=lambda kv: (kv[0][1], kv[1]['last_attempt_at']), reverse=True)
            return FakeCursor([
                {
                    'source': k[0], 'suggestion_date': k[1], 'sent_count': v['sent'],
                    'failed_count': v['failed'], 'last_attempt_at': v['last_attempt_at'],
                }
                for k, v in ordered
            ])

        if normalized.startswith('SELECT DISTINCT ON (d.recipient_id, d.email)'):
            source, suggestion_date = params
            matches = [r for r in self.rows if r['source'] == source and r['suggestion_date'] == suggestion_date]
            latest_by_key = {}
            for r in matches:
                key = (r['recipient_id'], r['email'])
                if key not in latest_by_key or r['sent_at'] > latest_by_key[key]['sent_at']:
                    latest_by_key[key] = r
            return FakeCursor([
                {**r, 'name': self.users.get(r['recipient_id'])}
                for r in latest_by_key.values()
            ])

        raise AssertionError(f'Unexpected SQL in test: {sql}')

    def commit(self):
        pass


def test_record_delivery_then_list_delivery_dates_shows_counts():
    db = FakeDeliveryDB()
    record_delivery(db, 'daily', '2026-08-24', 1, 'a@example.com', 'sent')
    record_delivery(db, 'daily', '2026-08-24', 2, 'b@example.com', 'failed', 'Missing: API key')

    dates = list_delivery_dates(db)
    assert dates == [{
        'source': 'daily', 'suggestion_date': '2026-08-24', 'sent': 1, 'failed': 1,
        'last_attempt_at': 2,
    }]


def test_list_delivery_dates_orders_newest_suggestion_date_first():
    db = FakeDeliveryDB()
    record_delivery(db, 'daily', '2026-08-20', 1, 'a@example.com', 'sent')
    record_delivery(db, 'daily', '2026-08-24', 1, 'a@example.com', 'sent')
    record_delivery(db, 'starters', '2026-08-22', 1, 'a@example.com', 'sent')

    dates = list_delivery_dates(db)
    assert [d['suggestion_date'] for d in dates] == ['2026-08-24', '2026-08-22', '2026-08-20']


def test_get_delivery_log_uses_the_latest_attempt_per_recipient():
    # A resend after an earlier failure -- the log should show the resend's
    # outcome (sent), not the original failure, for this recipient.
    db = FakeDeliveryDB(users={1: 'A Recipient'})
    record_delivery(db, 'daily', '2026-08-24', 1, 'a@example.com', 'failed', 'Zeptomail timeout')
    record_delivery(db, 'daily', '2026-08-24', 1, 'a@example.com', 'sent')

    log = get_delivery_log(db, 'daily', '2026-08-24')
    assert len(log) == 1
    assert log[0]['status'] == 'sent'
    assert log[0]['name'] == 'A Recipient'


def test_get_delivery_log_lists_failed_recipients_first():
    db = FakeDeliveryDB()
    record_delivery(db, 'daily', '2026-08-24', 1, 'zzz@example.com', 'sent')
    record_delivery(db, 'daily', '2026-08-24', 2, 'aaa@example.com', 'failed', 'Missing: API key')

    log = get_delivery_log(db, 'daily', '2026-08-24')
    assert [d['email'] for d in log] == ['aaa@example.com', 'zzz@example.com']


def test_get_delivery_log_sorts_alphabetically_within_the_same_status():
    db = FakeDeliveryDB()
    record_delivery(db, 'daily', '2026-08-24', 1, 'zzz@example.com', 'sent')
    record_delivery(db, 'daily', '2026-08-24', 2, 'aaa@example.com', 'sent')

    log = get_delivery_log(db, 'daily', '2026-08-24')
    assert [d['email'] for d in log] == ['aaa@example.com', 'zzz@example.com']


def test_get_delivery_log_is_scoped_to_the_requested_source_and_date():
    db = FakeDeliveryDB()
    record_delivery(db, 'daily', '2026-08-24', 1, 'a@example.com', 'sent')
    record_delivery(db, 'starters', '2026-08-24', 1, 'a@example.com', 'sent')
    record_delivery(db, 'daily', '2026-08-21', 1, 'a@example.com', 'sent')

    log = get_delivery_log(db, 'daily', '2026-08-24')
    assert len(log) == 1
