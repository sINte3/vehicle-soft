# -*- coding: utf-8 -*-
# ===========================================================================
# OBSOLETE -- DO NOT RUN THIS SCRIPT
#
# Status    : NOT_APPLIED (confirmed by production DB inspection, 2026-05-23,
#             TASK-OPS-002A).
# Superseded: migrate_tasks_abc3.py was applied instead and produced a
#             DIFFERENT schema:
#               app_modules          has column  is_active  (not icon/sort_order)
#               user_module_permissions has column  has_access (not can_view/can_edit)
#             Running this script would silently seed wrong module codes
#             (entry, reports, deficiency, refs) that the application does
#             not recognise, causing subtle admin-UI permission bugs.
# Action    : Never run. Do not backfill in schema_migrations. Keep for audit.
# ===========================================================================
"""
migrate_v47.py — Module permissions (Task 3) + language column (Task 6).
Run: "C:\\Program Files\\Python314\\python.exe" migrate_v47.py
"""
import sqlite3, os

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                  'instance', 'transport.db')

def col_exists(cur, table, col):
    cur.execute("PRAGMA table_info({})".format(table))
    return any(r[1] == col for r in cur.fetchall())

def run():
    if not os.path.exists(DB):
        print('ERROR: DB not found:', DB); return
    con = sqlite3.connect(DB)
    cur = con.cursor()

    # app_modules table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS app_modules (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            code       VARCHAR(50) UNIQUE NOT NULL,
            name_uz    VARCHAR(100) DEFAULT '',
            name_ru    VARCHAR(100) DEFAULT '',
            icon       VARCHAR(20)  DEFAULT '',
            sort_order INTEGER DEFAULT 0
        )
    """)
    print("OK: app_modules")

    # Seed initial modules
    modules = [
        ('entry',      'Маълумот киритиш', 'Ввод данных',    '📝', 1),
        ('wialon',     'Виалон',            'Wialon',          '🛰', 2),
        ('fuel',       'АЗС',               'АЗС',             '⛽', 3),
        ('reports',    'Ҳисобот',           'Отчёты',          '📊', 4),
        ('deficiency', 'Камчиликлар',       'Замечания',       '⚠', 5),
        ('refs',       'Справочниклар',     'Справочники',     '📁', 6),
    ]
    for code, uz, ru, icon, order in modules:
        cur.execute("""
            INSERT OR IGNORE INTO app_modules(code,name_uz,name_ru,icon,sort_order)
            VALUES(?,?,?,?,?)
        """, (code, uz, ru, icon, order))

    # user_module_permissions table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_module_permissions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            module_code VARCHAR(50) NOT NULL,
            can_view    INTEGER DEFAULT 1,
            can_edit    INTEGER DEFAULT 0,
            UNIQUE(user_id, module_code)
        )
    """)
    print("OK: user_module_permissions")

    # Task 6: language column in users
    if not col_exists(cur, 'users', 'language'):
        cur.execute("ALTER TABLE users ADD COLUMN language VARCHAR(5) DEFAULT 'uz'")
        print("OK: added language column to users")
    else:
        print("OK: language column already exists")

    con.commit(); con.close()
    print("\nMigration v4.7 done.")
    input("Press Enter...")

if __name__ == '__main__':
    run()
