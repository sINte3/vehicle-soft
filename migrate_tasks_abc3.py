import sqlite3, os

DB_PATH = os.path.join(os.path.dirname(__file__), 'instance', 'transport.db')

def col_exists(cur, table, col):
    cur.execute(f"PRAGMA table_info({table})")
    return any(row[1] == col for row in cur.fetchall())

def table_exists(cur, table):
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cur.fetchone() is not None

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

# Task 6: add language column to users
if not col_exists(cur, 'users', 'language'):
    cur.execute("ALTER TABLE users ADD COLUMN language VARCHAR(5) DEFAULT 'uz'")
    print('Added users.language')

# Task 3: app_modules table
if not table_exists(cur, 'app_modules'):
    cur.execute('''CREATE TABLE app_modules (
        id INTEGER PRIMARY KEY,
        code VARCHAR(50) UNIQUE NOT NULL,
        name_uz VARCHAR(200) NOT NULL,
        name_ru VARCHAR(200) NOT NULL,
        is_active BOOLEAN DEFAULT 1
    )''')
    print('Created app_modules')

# Task 3: user_module_permissions table
if not table_exists(cur, 'user_module_permissions'):
    cur.execute('''CREATE TABLE user_module_permissions (
        id INTEGER PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        module_code VARCHAR(50) NOT NULL,
        has_access BOOLEAN DEFAULT 1,
        UNIQUE(user_id, module_code)
    )''')
    print('Created user_module_permissions')

# Task P3: spare_parts table
if not table_exists(cur, 'spare_parts'):
    cur.execute('''CREATE TABLE spare_parts (
        id INTEGER PRIMARY KEY,
        name VARCHAR(300) NOT NULL,
        part_number VARCHAR(100) DEFAULT '',
        unit VARCHAR(30) DEFAULT 'dona',
        category VARCHAR(100) DEFAULT '',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')
    print('Created spare_parts')

# Task P3: spare_part_requests table
if not table_exists(cur, 'spare_part_requests'):
    cur.execute('''CREATE TABLE spare_part_requests (
        id INTEGER PRIMARY KEY,
        request_date DATE NOT NULL,
        organization_id INTEGER NOT NULL REFERENCES organizations(id),
        equipment_id INTEGER REFERENCES equipment(id),
        status VARCHAR(20) DEFAULT 'draft',
        note TEXT DEFAULT '',
        created_by INTEGER REFERENCES users(id),
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        reviewed_by INTEGER REFERENCES users(id),
        reviewed_at DATETIME,
        review_comment TEXT DEFAULT ''
    )''')
    print('Created spare_part_requests')

# Task P3: spare_part_request_items table
if not table_exists(cur, 'spare_part_request_items'):
    cur.execute('''CREATE TABLE spare_part_request_items (
        id INTEGER PRIMARY KEY,
        request_id INTEGER NOT NULL REFERENCES spare_part_requests(id),
        spare_part_id INTEGER REFERENCES spare_parts(id),
        name VARCHAR(300) NOT NULL,
        part_number VARCHAR(100) DEFAULT '',
        quantity REAL NOT NULL DEFAULT 1,
        unit VARCHAR(30) DEFAULT 'dona',
        note VARCHAR(300) DEFAULT ''
    )''')
    print('Created spare_part_request_items')

conn.commit()
conn.close()
print('Migration complete.')
