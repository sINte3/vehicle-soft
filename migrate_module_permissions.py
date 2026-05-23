# -*- coding: utf-8 -*-
"""
migrate_module_permissions.py -- TASK-SEC-001
Ensure all existing non-admin users have explicit has_access=True records
for every active module in app_modules.

Purpose:
  Before this migration, module permissions were configurable in the admin UI
  but never enforced by route guards. Adding enforcement (TASK-SEC-001) would
  block existing operators who never had explicit permission records. This
  migration grants all active modules to all non-admin users WHERE no record
  already exists, so production behavior is preserved after enforcement goes live.

Safety:
  - Idempotent: uses INSERT OR IGNORE, never updates existing records.
  - Never deletes or revokes permissions.
  - Only touches non-admin users (role != 'admin').
  - Does not change app_modules or users tables.

Run (service must be STOPPED first to avoid SQLite write conflicts):
  cd C:\\transport-report
  .\\nssm.exe stop TransportReport
  copy instance\\transport.db instance\\transport.db.backup_before_sec001
  "C:\\Program Files\\Python314\\python.exe" migrate_module_permissions.py
  .\\nssm.exe start TransportReport

Rollback:
  To undo: delete the inserted rows from user_module_permissions.
  Safest rollback: restore the backup copy of transport.db.
    .\\nssm.exe stop TransportReport
    copy /Y instance\\transport.db.backup_before_sec001 instance\\transport.db
    .\\nssm.exe start TransportReport
"""

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       'instance', 'transport.db')


def run():
    if not os.path.exists(DB_PATH):
        print('ERROR: Database not found at', DB_PATH)
        return

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    # Collect all non-admin user IDs
    cur.execute("SELECT id, username FROM users WHERE role != 'admin'")
    users = cur.fetchall()
    if not users:
        print('No non-admin users found. Nothing to do.')
        con.close()
        return
    print('Non-admin users found: {}'.format(len(users)))
    for uid, uname in users:
        print('  id={} username={}'.format(uid, uname))

    # Collect all active module codes
    cur.execute("SELECT code FROM app_modules WHERE is_active = 1")
    modules = [row[0] for row in cur.fetchall()]
    if not modules:
        print('No active modules found in app_modules. Nothing to do.')
        con.close()
        return
    print('Active modules: {}'.format(modules))

    # Grant each module to each non-admin user where no record exists
    inserted = 0
    skipped = 0
    for uid, uname in users:
        for code in modules:
            cur.execute(
                "SELECT id, has_access FROM user_module_permissions "
                "WHERE user_id = ? AND module_code = ?",
                (uid, code)
            )
            existing = cur.fetchone()
            if existing is None:
                cur.execute(
                    "INSERT INTO user_module_permissions (user_id, module_code, has_access) "
                    "VALUES (?, ?, 1)",
                    (uid, code)
                )
                inserted += 1
                print('  INSERTED: user_id={} ({}) module={}'.format(uid, uname, code))
            else:
                skipped += 1
                print('  SKIP (exists): user_id={} ({}) module={} has_access={}'.format(
                    uid, uname, code, existing[1]))

    con.commit()
    con.close()

    print()
    print('Done. Inserted: {}  Skipped (already existed): {}'.format(inserted, skipped))


if __name__ == '__main__':
    run()
