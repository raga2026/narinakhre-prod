"""Admin-managed one-off announcements to every Stocks viewer -- a small
table of {name, subject, body} rows plus a batch send. The send itself
(rendering + Zeptomail) lives in utils/suggestion_email.py
(send_announcement_email / send_announcement_to_all_viewers); this module
is just the CRUD + table.

Replaces the old single hard-coded send_rebrand_announcement route -- now
any announcement is a row an admin edits and sends from
/stocks/announcements.
"""

ANNOUNCEMENTS_TABLE_SQL = [
    '''CREATE TABLE IF NOT EXISTS stocks_announcements (
        id BIGSERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        subject TEXT NOT NULL,
        body TEXT NOT NULL,
        include_referral INTEGER NOT NULL DEFAULT 1,
        created_by BIGINT REFERENCES stocks_admin_users(id),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_sent_at TIMESTAMP,
        last_sent_count INTEGER NOT NULL DEFAULT 0
    )''',
]

# Seeded once (guarded by NOT EXISTS on name) so the 2026-09 free/pro
# change already has a ready-to-send row the first time the page loads.
_FREE_PRO_BODY = (
    "Big change to how StoqBell works:\n"
    "\n"
    "- The daily Pick of the Day is now FREE for every account. Your login and your daily "
    "email carry on exactly as before -- no payment, nothing to do.\n"
    "- The Rs 99/month Starters plan has been retired.\n"
    "- There is a new paid tier: StoqBell Pro, Rs 299 + GST (Rs 352.82)/month. Pro adds the "
    "full Highly Recommended list every trading day (every stock clearing our top quality bar, "
    "not just the one daily pick), real-time intraday alerts, and target-hit notifications. "
    "There is a 7-day free trial.\n"
    "\n"
    "Log in any time: https://www.stoqbell.com/stocks/login"
)

ANNOUNCEMENTS_SEED_SQL = [
    (
        "INSERT INTO stocks_announcements (name, subject, body, include_referral) "
        "SELECT 'Free / Pro launch', 'StoqBell is now free -- the daily pick, for everyone', ?, 1 "
        "WHERE NOT EXISTS (SELECT 1 FROM stocks_announcements WHERE name = 'Free / Pro launch')",
        (_FREE_PRO_BODY,),
    ),
]


def initialize_announcements_table_if_needed(client):
    for sql in ANNOUNCEMENTS_TABLE_SQL:
        try:
            client.rpc('execute_sql', {'query': sql}).execute()
        except Exception as e:
            print(f'Announcements table init warning (may already exist): {e}')
    for sql, params in ANNOUNCEMENTS_SEED_SQL:
        try:
            # execute_sql takes a single string; inline the one text param
            # (body) the same escaped way the rest of this codebase does.
            escaped = params[0].replace("'", "''")
            client.rpc('execute_sql', {'query': sql.replace('?', f"'{escaped}'", 1)}).execute()
        except Exception as e:
            print(f'Announcements seed warning: {e}')


def list_announcements(db):
    return db.execute(
        "SELECT id, name, subject, body, include_referral, last_sent_at, last_sent_count, "
        "created_at, updated_at FROM stocks_announcements ORDER BY created_at DESC"
    ).fetchall()


def get_announcement(db, announcement_id):
    return db.execute(
        "SELECT id, name, subject, body, include_referral, last_sent_at, last_sent_count "
        "FROM stocks_announcements WHERE id = ?", (announcement_id,)
    ).fetchone()


def create_announcement(db, name, subject, body, include_referral=True, created_by=None):
    db.execute(
        "INSERT INTO stocks_announcements (name, subject, body, include_referral, created_by) "
        "VALUES (?, ?, ?, ?, ?)",
        (name.strip(), subject.strip(), body.strip(), 1 if include_referral else 0, created_by),
    )
    db.commit()


def update_announcement(db, announcement_id, name, subject, body, include_referral=True):
    db.execute(
        "UPDATE stocks_announcements SET name=?, subject=?, body=?, include_referral=?, updated_at=NOW() "
        "WHERE id=?",
        (name.strip(), subject.strip(), body.strip(), 1 if include_referral else 0, announcement_id),
    )
    db.commit()


def delete_announcement(db, announcement_id):
    db.execute("DELETE FROM stocks_announcements WHERE id=?", (announcement_id,))
    db.commit()


def mark_announcement_sent(db, announcement_id, sent_count):
    db.execute(
        "UPDATE stocks_announcements SET last_sent_at=NOW(), last_sent_count=? WHERE id=?",
        (sent_count, announcement_id),
    )
    db.commit()
