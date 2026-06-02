"""TASK-SEC-003A migration: user security fields and audit_logs table."""

from sqlalchemy import text
from app import app
from models import db


def columns(table_name):
    rows = db.session.execute(text(f"PRAGMA table_info({table_name})")).mappings().all()
    return {r['name'] for r in rows}


def add_column_if_missing(table, name, ddl):
    if name not in columns(table):
        db.session.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))
        print(f"Added {table}.{name}")
    else:
        print(f"Exists {table}.{name}")


def main():
    with app.app_context():
        add_column_if_missing('users', 'must_change_password', 'must_change_password INTEGER NOT NULL DEFAULT 0')
        add_column_if_missing('users', 'password_changed_at', 'password_changed_at TEXT')
        add_column_if_missing('users', 'last_login_ip', 'last_login_ip TEXT')
        add_column_if_missing('users', 'failed_login_count', 'failed_login_count INTEGER NOT NULL DEFAULT 0')
        add_column_if_missing('users', 'locked_until', 'locked_until TEXT')
        add_column_if_missing('users', 'updated_at', 'updated_at TEXT')
        add_column_if_missing('users', 'created_by_id', 'created_by_id INTEGER')

        db.session.execute(text("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                user_id INTEGER,
                username_snapshot TEXT,
                full_name_snapshot TEXT,
                role_snapshot TEXT,
                action TEXT NOT NULL,
                entity_type TEXT,
                entity_id INTEGER,
                entity_label TEXT,
                module TEXT,
                route TEXT,
                method TEXT,
                ip_address TEXT,
                user_agent TEXT,
                before_json TEXT,
                after_json TEXT,
                changes_json TEXT,
                status TEXT DEFAULT 'ok',
                description TEXT
            )
        """))
        db.session.execute(text('CREATE INDEX IF NOT EXISTS ix_audit_logs_created_at ON audit_logs(created_at)'))
        db.session.execute(text('CREATE INDEX IF NOT EXISTS ix_audit_logs_user_id ON audit_logs(user_id)'))
        db.session.execute(text('CREATE INDEX IF NOT EXISTS ix_audit_logs_action ON audit_logs(action)'))
        db.session.execute(text('CREATE INDEX IF NOT EXISTS ix_audit_logs_module ON audit_logs(module)'))
        db.session.commit()
        print('TASK-SEC-003A migration completed successfully.')


if __name__ == '__main__':
    main()
