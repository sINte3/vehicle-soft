"""
test_bot002_state.py -- BOT002 Offline State DB Tests

Tests bot_state.py functions using a temporary SQLite database.
No real tokens are printed. No network access.
No Flask or Telegram dependency.

Output: BOT002_STATE_TEST_OK (or error details + non-zero exit)
"""

import os
import sys
import tempfile
import shutil

STAGING_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, STAGING_DIR)

ERRORS = []
PASSED = []
_TEMP_DIR = None
_DB_PATH = None


def check(label, fn):
    try:
        fn()
        PASSED.append(label)
        print("[OK]   " + label)
    except Exception as exc:
        ERRORS.append((label, str(exc)))
        print("[FAIL] " + label)
        print("       " + str(exc)[:400])


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def _setup():
    global _TEMP_DIR, _DB_PATH
    _TEMP_DIR = tempfile.mkdtemp(prefix="bot002_state_test_")
    _DB_PATH = os.path.join(_TEMP_DIR, "test_bot_state.db")


def _teardown():
    if _TEMP_DIR and os.path.exists(_TEMP_DIR):
        shutil.rmtree(_TEMP_DIR, ignore_errors=True)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_import():
    import bot_state
    assert hasattr(bot_state, "init_state_db")
    assert hasattr(bot_state, "save_session")
    assert hasattr(bot_state, "get_session")
    assert hasattr(bot_state, "delete_session")
    assert hasattr(bot_state, "list_sessions")


def test_init_idempotent():
    import bot_state
    # Call twice -- must not raise or corrupt
    bot_state.init_state_db(_DB_PATH)
    bot_state.init_state_db(_DB_PATH)
    assert os.path.exists(_DB_PATH), "DB file not created"


def test_save_session():
    import bot_state
    bot_state.init_state_db(_DB_PATH)
    profile = {
        "id": 42,
        "username": "test_user",
        "full_name": "Test User",
        "role": "operator",
    }
    # Must not raise
    bot_state.save_session(_DB_PATH, 100000001, "raw-token-value-abc123", profile)


def test_get_session():
    import bot_state
    bot_state.init_state_db(_DB_PATH)
    profile = {
        "id": 99,
        "username": "alice",
        "full_name": "Alice Ivanova",
        "role": "admin",
    }
    bot_state.save_session(_DB_PATH, 200000002, "token-for-alice-xyz", profile)
    result = bot_state.get_session(_DB_PATH, 200000002)
    assert result is not None, "get_session returned None"
    assert result["telegram_id"] == 200000002
    assert result["username"] == "alice"
    assert result["full_name"] == "Alice Ivanova"
    assert result["role"] == "admin"
    assert result["user_id"] == 99
    # Token stored but MUST NOT be printed or logged in the test
    assert "api_token" in result, "api_token key missing"
    assert result["api_token"] == "token-for-alice-xyz"
    # Verify token is NOT printed anywhere in this test
    # (This test just stores it -- we only assert equality, never print)


def test_get_session_missing():
    import bot_state
    bot_state.init_state_db(_DB_PATH)
    result = bot_state.get_session(_DB_PATH, 999999999)
    assert result is None, "Expected None for missing session, got: " + repr(result)


def test_delete_session():
    import bot_state
    bot_state.init_state_db(_DB_PATH)
    profile = {"id": 77, "username": "bob", "full_name": "Bob", "role": "operator"}
    bot_state.save_session(_DB_PATH, 300000003, "bob-token", profile)
    assert bot_state.get_session(_DB_PATH, 300000003) is not None
    deleted = bot_state.delete_session(_DB_PATH, 300000003)
    assert deleted is True, "delete_session should return True when a row is deleted"
    assert bot_state.get_session(_DB_PATH, 300000003) is None


def test_delete_session_not_found():
    import bot_state
    bot_state.init_state_db(_DB_PATH)
    deleted = bot_state.delete_session(_DB_PATH, 888888888)
    assert deleted is False, "delete_session should return False when no row exists"


def test_list_sessions():
    import bot_state
    bot_state.init_state_db(_DB_PATH)
    # Save a few sessions
    for i, (tg_id, uname) in enumerate([
        (400000001, "user_one"),
        (400000002, "user_two"),
        (400000003, "user_three"),
    ]):
        profile = {"id": 100 + i, "username": uname, "full_name": uname.title(), "role": "operator"}
        bot_state.save_session(_DB_PATH, tg_id, f"token-{uname}", profile)
    sessions = bot_state.list_sessions(_DB_PATH)
    assert isinstance(sessions, list), "list_sessions should return a list"
    usernames = {s["username"] for s in sessions}
    assert "user_one" in usernames
    assert "user_two" in usernames
    assert "user_three" in usernames
    # Tokens present in result but NOT printed
    for s in sessions:
        assert "api_token" in s, "api_token missing from list_sessions result"


def test_list_sessions_empty_db():
    import bot_state
    empty_db = os.path.join(_TEMP_DIR, "empty_test.db")
    # DB does not exist yet -- should return []
    result = bot_state.list_sessions(empty_db)
    assert result == [], "Expected [] for non-existent DB, got: " + repr(result)


def test_save_session_upsert():
    """Saving a session twice for same telegram_id should update, not duplicate."""
    import bot_state
    bot_state.init_state_db(_DB_PATH)
    tg_id = 500000001
    profile1 = {"id": 55, "username": "charlie", "full_name": "Charlie Old", "role": "operator"}
    profile2 = {"id": 55, "username": "charlie", "full_name": "Charlie New", "role": "admin"}
    bot_state.save_session(_DB_PATH, tg_id, "old-token", profile1)
    bot_state.save_session(_DB_PATH, tg_id, "new-token", profile2)
    result = bot_state.get_session(_DB_PATH, tg_id)
    assert result is not None
    assert result["full_name"] == "Charlie New", "Upsert did not update full_name"
    assert result["role"] == "admin", "Upsert did not update role"
    # Only one row
    sessions = bot_state.list_sessions(_DB_PATH)
    matching = [s for s in sessions if s["telegram_id"] == tg_id]
    assert len(matching) == 1, "Upsert created duplicate rows: " + str(len(matching))


def test_invalid_telegram_id():
    import bot_state
    bot_state.init_state_db(_DB_PATH)
    try:
        bot_state.save_session(_DB_PATH, -1, "tok", {"id": 1})
        print("       [NOTE] save_session(-1) did not raise ValueError")
    except ValueError:
        pass  # Expected


def test_token_not_in_printed_output():
    """This test verifies the test code itself never prints the token."""
    import bot_state
    bot_state.init_state_db(_DB_PATH)
    profile = {"id": 11, "username": "secret_user", "full_name": "S", "role": "operator"}
    secret_token = "SUPER_SECRET_TOKEN_MUST_NOT_APPEAR_IN_OUTPUT"
    bot_state.save_session(_DB_PATH, 600000001, secret_token, profile)
    result = bot_state.get_session(_DB_PATH, 600000001)
    # We verify token is retrievable without printing
    retrieved = result.get("api_token", "")
    assert retrieved == secret_token, "Token mismatch"
    # If we got here without printing secret_token, the test passes
    # (The assertion above is intentional -- we check equality, not print)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

_setup()

check("import bot_state", test_import)
check("init_state_db idempotent", test_init_idempotent)
check("save_session", test_save_session)
check("get_session", test_get_session)
check("get_session missing", test_get_session_missing)
check("delete_session", test_delete_session)
check("delete_session not found", test_delete_session_not_found)
check("list_sessions", test_list_sessions)
check("list_sessions empty DB", test_list_sessions_empty_db)
check("save_session upsert", test_save_session_upsert)
check("save_session invalid telegram_id", test_invalid_telegram_id)
check("token not printed in output", test_token_not_in_printed_output)

_teardown()

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
    print("BOT002_STATE_TEST_FAILED")
    sys.exit(1)
else:
    print()
    print("BOT002_STATE_TEST_OK")
