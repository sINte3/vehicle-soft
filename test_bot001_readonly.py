"""
test_bot001_readonly.py -- BOT001A Telegram Foundation (corrected)
Read-only test: does NOT create or modify any data in the database.

Prerequisites (must be met before running this test):
  1. TransportReportStaging service is STOPPED.
  2. migrate_bot001_telegram_foundation.py has been run successfully.
  3. Run from C:\\transport-report-staging directory.

Checks:
  1.  app import OK (create_app() must succeed without DB write errors)
  2.  /api/bot/health exists in url_map
  3.  /api/bot/me exists in url_map
  4.  /api/bot/requests exists in url_map
  5.  /api/bot/equipment exists in url_map
  6.  /api/bot/catalog exists in url_map
  7.  New models importable (SparePartStatusHistory, BotApiSession, BotNotificationQueue)
  8.  User model has all Telegram fields
  9.  bot_security functions work correctly
  10. PRAGMA integrity_check on staging DB passes
  11. No real Telegram secrets in config.py
  13. bot_api_bp is imported and registered in app.py
  14. bot_api.py contains the inactive-user guard in link_verify

Run from staging directory (service MUST be stopped):
  "C:\\Program Files\\Python314\\python.exe" test_bot001_readonly.py
"""

import os
import sys

STAGING_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, STAGING_DIR)

# Minimal env so app imports cleanly
os.environ.setdefault('FLASK_ENV', 'sqlite_prod')
os.environ.setdefault('SECRET_KEY', 'test-readonly-key-bot001a-not-for-production')

ERRORS = []
PASSED = []


def check(label, fn):
    try:
        fn()
        PASSED.append(label)
        print("[OK]   " + label)
    except Exception as exc:
        ERRORS.append((label, str(exc)))
        print("[FAIL] " + label)
        print("       " + str(exc)[:300])


# ------------------------------------------------------------------
# 1. Import app (requires service stopped + migration already run)
# ------------------------------------------------------------------
app_module = None

def _import_app():
    global app_module
    import app as _app_module
    app_module = _app_module
    assert hasattr(_app_module, 'app'), "app object not found in app module"
    # Verify the app was actually created (create_app() returned successfully)
    assert _app_module.app is not None, "create_app() returned None"

check("app import OK", _import_app)


# ------------------------------------------------------------------
# 2-6. Check all /api/bot/* routes exist in url_map
# ------------------------------------------------------------------

def _get_rules():
    assert app_module is not None and hasattr(app_module, 'app'), \
        "app not imported -- fix issue 1 first"
    return [str(rule) for rule in app_module.app.url_map.iter_rules()]


def _check_route(path):
    rules = _get_rules()
    assert path in rules, \
        "{} not in url_map. Bot routes found: {}".format(
            path, [r for r in rules if '/bot' in r]
        )


check("/api/bot/health in url_map",    lambda: _check_route('/api/bot/health'))
check("/api/bot/me in url_map",        lambda: _check_route('/api/bot/me'))
check("/api/bot/requests in url_map",  lambda: _check_route('/api/bot/requests'))
check("/api/bot/equipment in url_map", lambda: _check_route('/api/bot/equipment'))
check("/api/bot/catalog in url_map",   lambda: _check_route('/api/bot/catalog'))


# ------------------------------------------------------------------
# 7. New models importable
# ------------------------------------------------------------------
def _import_models():
    from models import SparePartStatusHistory, BotApiSession, BotNotificationQueue
    assert SparePartStatusHistory.__tablename__ == 'spare_part_status_history', \
        "Wrong tablename: " + SparePartStatusHistory.__tablename__
    assert BotApiSession.__tablename__ == 'bot_api_sessions', \
        "Wrong tablename: " + BotApiSession.__tablename__
    assert BotNotificationQueue.__tablename__ == 'bot_notification_queue', \
        "Wrong tablename: " + BotNotificationQueue.__tablename__

check("New models importable", _import_models)


# ------------------------------------------------------------------
# 8. User model has all required Telegram fields
# ------------------------------------------------------------------
def _user_tg_fields():
    from models import User
    required = [
        'telegram_id', 'tg_notifications', 'tg_link_code_hash',
        'tg_link_code_expires_at', 'tg_link_code_created_at', 'tg_quiet_hours',
    ]
    for field in required:
        assert hasattr(User, field), "User model missing field: " + field

check("User model has Telegram fields", _user_tg_fields)


# ------------------------------------------------------------------
# 9. bot_security functions work correctly
# ------------------------------------------------------------------
def _bot_security():
    from bot_security import (
        generate_link_code, hash_secret, verify_secret,
        utcnow, make_api_token, hash_api_token, parse_datetime_safe,
    )
    # generate_link_code
    code = generate_link_code(6)
    assert len(code) == 6 and code.isdigit(), \
        "generate_link_code(6) returned bad value: " + repr(code)

    # hash_secret / verify_secret
    h = hash_secret("test-secret-value-123")
    assert len(h) == 64, "hash_secret returned wrong length: " + str(len(h))
    assert verify_secret("test-secret-value-123", h), "verify_secret returned False for correct value"
    assert not verify_secret("wrong-value", h), "verify_secret returned True for wrong value"
    assert not verify_secret("", h), "verify_secret returned True for empty string"
    assert not verify_secret("test-secret-value-123", ""), "verify_secret returned True for empty hash"

    # make_api_token / hash_api_token
    tok = make_api_token()
    assert len(tok) > 20, "make_api_token too short: " + str(len(tok))
    th = hash_api_token(tok)
    assert len(th) == 64, "hash_api_token wrong length: " + str(len(th))

    # utcnow
    now = utcnow()
    assert now.tzinfo is not None, "utcnow() returned naive datetime"

    # parse_datetime_safe
    dt = parse_datetime_safe("2026-06-06T12:00:00")
    assert dt is not None, "parse_datetime_safe returned None for valid ISO string"
    assert parse_datetime_safe(None) is None, "parse_datetime_safe(None) should return None"
    assert parse_datetime_safe("") is None, "parse_datetime_safe('') should return None"

check("bot_security functions work", _bot_security)


# ------------------------------------------------------------------
# 10. PRAGMA integrity_check on staging DB
# ------------------------------------------------------------------
def _integrity_check():
    import sqlite3
    db_path = os.path.join(STAGING_DIR, 'instance', 'transport.db')
    if not os.path.exists(db_path):
        raise FileNotFoundError("Staging DB not found at: " + db_path)
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.execute("PRAGMA integrity_check")
    result = [r[0] for r in cur.fetchall()]
    con.close()
    assert result == ["ok"], "PRAGMA integrity_check returned: " + str(result)

check("PRAGMA integrity_check on staging DB", _integrity_check)


# ------------------------------------------------------------------
# 11. Migration tables exist in staging DB
# ------------------------------------------------------------------
def _migration_tables():
    import sqlite3
    db_path = os.path.join(STAGING_DIR, 'instance', 'transport.db')
    if not os.path.exists(db_path):
        raise FileNotFoundError("Staging DB not found at: " + db_path)
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    expected_tables = [
        'spare_part_status_history',
        'bot_api_sessions',
        'bot_notification_queue',
    ]
    for table in expected_tables:
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,)
        )
        row = cur.fetchone()
        assert row is not None, \
            "Table '{}' missing -- run migrate_bot001_telegram_foundation.py first".format(table)
    # Check telegram_id column in users
    cur.execute("PRAGMA table_info(users)")
    cols = {row[1].lower() for row in cur.fetchall()}
    assert 'telegram_id' in cols, "users.telegram_id missing -- run migration first"
    # Check unique partial index
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_users_telegram_id'"
    )
    assert cur.fetchone() is not None, \
        "idx_users_telegram_id index missing -- run migrate_bot001_telegram_foundation.py first"
    con.close()

check("Migration tables and indexes in staging DB", _migration_tables)


# ------------------------------------------------------------------
# 12. No real Telegram secrets in config.py
# ------------------------------------------------------------------
def _no_secrets_in_config():
    config_path = os.path.join(STAGING_DIR, 'config.py')
    if not os.path.exists(config_path):
        raise FileNotFoundError("config.py not found at: " + config_path)
    with open(config_path, 'r', encoding='utf-8') as f:
        content = f.read()
    suspicious = [
        'BOT_TOKEN', 'TG_BOT_TOKEN', 'bot_token =',
        'telegram_bot_token', 'TELEGRAM_TOKEN',
    ]
    for s in suspicious:
        assert s.lower() not in content.lower(), \
            "Possible secret found in config.py: " + s

check("No real Telegram secrets in config.py", _no_secrets_in_config)


# ------------------------------------------------------------------
# 13. bot_api_bp is imported and registered in app.py (source check)
# ------------------------------------------------------------------
def _blueprint_registered():
    app_py = os.path.join(STAGING_DIR, 'app.py')
    with open(app_py, 'r', encoding='utf-8') as f:
        content = f.read()
    assert 'from bot_api import bot_api_bp' in content, \
        "bot_api_bp import not found in app.py"
    assert 'app.register_blueprint(bot_api_bp)' in content, \
        "app.register_blueprint(bot_api_bp) not found in app.py"

check("bot_api_bp imported and registered in app.py", _blueprint_registered)


# ------------------------------------------------------------------
# 14. bot_api.py contains the inactive-user guard in link_verify
# ------------------------------------------------------------------
def _inactive_guard_in_source():
    bot_api_path = os.path.join(STAGING_DIR, 'bot_api.py')
    with open(bot_api_path, 'r', encoding='utf-8') as f:
        content = f.read()
    assert 'not matching_user.is_active' in content, \
        'Inactive-user guard not found in bot_api.py link_verify'
    assert '403' in content, \
        '403 status code not found in bot_api.py'
    assert 'User is inactive' in content, \
        'Inactive user error message not found in bot_api.py'

check("bot_api.py contains inactive-user guard in link_verify", _inactive_guard_in_source)


# ------------------------------------------------------------------
# Summary
# ------------------------------------------------------------------
print()
print("=" * 55)
print("Passed: {}/{}".format(len(PASSED), len(PASSED) + len(ERRORS)))
if ERRORS:
    print()
    print("FAILED checks:")
    for label, err in ERRORS:
        print("  [FAIL] " + label)
        print("         " + str(err)[:200])
    print()
    print("BOT001_READONLY_TEST_FAILED")
    sys.exit(1)
else:
    print()
    print("BOT001_READONLY_TEST_OK")
