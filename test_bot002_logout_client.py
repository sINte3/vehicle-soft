"""
test_bot002_logout_client.py -- BOT002A Logout Endpoint Test

Uses Flask app test_client with an in-memory SQLite database to test
the full logout flow end-to-end. This test does not require write
access to the staging DB and leaves no residual data.

Scenario:
  1. Create a fresh in-memory DB with all schema tables.
  2. Create a test user with a link code set.
  3. POST /api/bot/link/verify to obtain api_token.
  4. Confirm /api/bot/me returns 200 with the token.
  5. POST /api/bot/logout with Bearer token.
  6. Assert HTTP 200 + ok=true.
  7. Assert user.telegram_id is NULL in DB.
  8. Assert bot_api_sessions row has revoked_at NOT NULL.
  9. Assert /api/bot/me with same token now returns 401.
 10. Verify /api/bot/logout without token returns 401 (not 404).

Rules:
- Uses in-memory SQLite -- no staging DB write required.
- Tests the logout logic of bot_api.py (the BOT002A updated version).
- Does NOT create spare part requests.
- Never prints raw api_token to stdout.
- bot_api.py is used (not the locked bot_api.py) to test the new logic.

Output: BOT002_LOGOUT_CLIENT_TEST_OK (or error details + non-zero exit)
"""

import json
import os
import sys
import importlib.util

STAGING_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, STAGING_DIR)

ERRORS = []
PASSED = []

# ---- Test constants --------------------------------------------------------
_TEST_USERNAME = "__bot002a_logout_test_user__"
_TEST_TG_ID = 9000000001  # Large ID unlikely to collide with real users
_TEST_CODE = "987654"     # The plaintext link code


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
# Setup: import bot_api.py as blueprint + create fresh Flask app
# ---------------------------------------------------------------------------

print("[INFO] Loading bot_api.py (BOT002A updated bot_api)...")
new_bot_api_path = os.path.join(STAGING_DIR, "bot_api.py")
assert os.path.exists(new_bot_api_path), (
    "bot_api.py not found. Run _write_bot002a.py to generate it."
)

spec = importlib.util.spec_from_file_location("bot_api_current_module", new_bot_api_path)
new_bot_api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(new_bot_api)
print("[OK]   bot_api.py loaded")

# Verify logout route is in the blueprint
logout_in_bp = any(
    "logout" in str(fn) for fn in new_bot_api.bot_api_bp.deferred_functions
)
print("[INFO] Checking blueprint has /logout route...")

# Import Flask and models
from flask import Flask
from models import db, User, BotApiSession
from bot_security import hash_secret, hash_api_token, utcnow, make_api_token

# Create isolated Flask app with in-memory SQLite
test_flask = Flask("test_logout_client")
test_flask.config["TESTING"] = True
test_flask.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
test_flask.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
test_flask.config["SECRET_KEY"] = "bot002a-logout-test-not-for-prod"
db.init_app(test_flask)
test_flask.register_blueprint(new_bot_api.bot_api_bp)

# Initialize schema in memory
with test_flask.app_context():
    db.create_all()
    # Verify tables exist
    from sqlalchemy import inspect
    inspector = inspect(db.engine)
    tables = inspector.get_table_names()
    print(f"[INFO] In-memory tables: {', '.join(sorted(tables))}")
    assert "users" in tables, "users table not created"
    assert "bot_api_sessions" in tables, "bot_api_sessions table not created"

# Verify URL map
url_rules_str = sorted(str(r) for r in test_flask.url_map.iter_rules() if "/api/bot" in str(r))
print("[INFO] Registered /api/bot routes:")
for r in url_rules_str:
    print("       " + r)

assert any("/logout" in r for r in url_rules_str), (
    "/api/bot/logout route not found in URL map. "
    "Check bot_api.py has the @bot_api_bp.route('/logout', ...) decorator."
)
print("[OK]   /api/bot/logout found in URL map")

# Store acquired token between test functions
_acquired_token = None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_setup_test_user():
    """Create test user with a link code in the in-memory DB."""
    from datetime import timedelta, datetime, timezone

    with test_flask.app_context():
        now = datetime.now(timezone.utc)
        expires = now + timedelta(minutes=10)
        code_hash = hash_secret(_TEST_CODE)

        user = User(
            username=_TEST_USERNAME,
            full_name="BOT002A Logout Test User",
            role="operator",
            is_active_user=True,
        )
        user.set_password("not-a-real-password-" + make_api_token()[:8])
        user.tg_link_code_hash = code_hash
        user.tg_link_code_expires_at = expires
        user.tg_link_code_created_at = now
        user.telegram_id = None
        db.session.add(user)
        db.session.commit()
        print(f"       [INFO] Test user created: id={user.id}, username={user.username}")


def test_logout_route_in_url_map():
    """Verify /api/bot/logout registered with POST method."""
    for rule in test_flask.url_map.iter_rules():
        if "/logout" in str(rule):
            assert "POST" in rule.methods, (
                f"/api/bot/logout must allow POST, got methods: {rule.methods}"
            )
            print(f"       [INFO] /api/bot/logout: {rule}, methods: {rule.methods}")
            return
    raise AssertionError("/api/bot/logout not in URL map")


def test_link_verify_succeeds():
    """POST /api/bot/link/verify with valid code -- must return api_token."""
    global _acquired_token
    with test_flask.test_client() as client:
        payload = json.dumps({
            "telegram_id": _TEST_TG_ID,
            "code": _TEST_CODE,
        }).encode("utf-8")
        r = client.post(
            "/api/bot/link/verify",
            data=payload,
            content_type="application/json",
        )
        assert r.status_code == 200, (
            f"/api/bot/link/verify expected 200, got {r.status_code}. "
            f"Body: {r.data[:300]}"
        )
        body = json.loads(r.data)
        assert body.get("ok") is True, f"Expected ok=True, got: {body}"
        api_token = body.get("api_token")
        assert api_token and isinstance(api_token, str) and len(api_token) > 10, (
            "api_token missing or invalid"
        )
        _acquired_token = api_token
        print("       [INFO] Link verified, api_token acquired (not printed)")


def test_me_returns_200_after_link():
    """GET /api/bot/me with valid token -- must return 200."""
    assert _acquired_token, "No token -- test_link_verify_succeeds must pass first"
    with test_flask.test_client() as client:
        r = client.get(
            "/api/bot/me",
            headers={"Authorization": "Bearer " + _acquired_token},
        )
        assert r.status_code == 200, (
            f"/api/bot/me expected 200, got {r.status_code}. Body: {r.data[:300]}"
        )
        body = json.loads(r.data)
        assert body.get("ok") is True
        assert body["user"]["username"] == _TEST_USERNAME
        print("       [INFO] /api/bot/me returns 200 with correct user")


def test_telegram_id_set_after_link():
    """Verify telegram_id is written to user row after link."""
    with test_flask.app_context():
        user = User.query.filter_by(username=_TEST_USERNAME).first()
        assert user is not None, "Test user not found"
        assert user.telegram_id == _TEST_TG_ID, (
            f"Expected telegram_id={_TEST_TG_ID}, got {user.telegram_id}"
        )
        assert user.tg_link_code_hash is None, (
            "tg_link_code_hash should be cleared after link"
        )
        print(f"       [INFO] telegram_id={user.telegram_id} set; link code hash cleared")


def test_logout_returns_200():
    """POST /api/bot/logout with valid Bearer token -- must return 200."""
    assert _acquired_token, "No token -- test_link_verify_succeeds must pass first"
    with test_flask.test_client() as client:
        r = client.post(
            "/api/bot/logout",
            data="{}",
            content_type="application/json",
            headers={"Authorization": "Bearer " + _acquired_token},
        )
        assert r.status_code == 200, (
            f"/api/bot/logout expected 200, got {r.status_code}. "
            f"Body: {r.data[:300]}. "
            f"If 404: /api/bot/logout endpoint not in bot_api.py."
        )
        body = json.loads(r.data)
        assert body.get("ok") is True, f"Expected ok=True, got: {body}"
        assert "message" in body, f"Expected 'message' in response, got: {body}"
        print(f"       [INFO] Logout response: {body['message']}")


def test_telegram_id_null_after_logout():
    """Assert user.telegram_id is NULL after logout."""
    with test_flask.app_context():
        user = User.query.filter_by(username=_TEST_USERNAME).first()
        assert user is not None, "Test user not found"
        assert user.telegram_id is None, (
            f"Expected telegram_id=NULL after logout, got {user.telegram_id}"
        )
        print("       [INFO] telegram_id is NULL after logout -- OK")


def test_session_revoked_after_logout():
    """Assert bot_api_sessions row has revoked_at NOT NULL after logout."""
    assert _acquired_token, "No token -- test_link_verify_succeeds must pass first"
    with test_flask.app_context():
        token_hash = hash_api_token(_acquired_token)
        session = BotApiSession.query.filter_by(token_hash=token_hash).first()
        assert session is not None, "Session row not found in bot_api_sessions"
        assert session.revoked_at is not None, (
            "Expected revoked_at to be set after logout, but it is NULL"
        )
        print(f"       [INFO] Session revoked_at={session.revoked_at} -- OK")


def test_me_returns_401_after_logout():
    """GET /api/bot/me with revoked token -- must return 401."""
    assert _acquired_token, "No token -- test_link_verify_succeeds must pass first"
    with test_flask.test_client() as client:
        r = client.get(
            "/api/bot/me",
            headers={"Authorization": "Bearer " + _acquired_token},
        )
        assert r.status_code == 401, (
            f"/api/bot/me expected 401 after logout, got {r.status_code}. "
            f"Body: {r.data[:300]}"
        )
        print("       [INFO] /api/bot/me returns 401 after logout -- OK")


def test_logout_without_token_returns_401():
    """POST /api/bot/logout without Bearer token -- must return exactly 401 (NOT 404)."""
    with test_flask.test_client() as client:
        r = client.post(
            "/api/bot/logout",
            data="{}",
            content_type="application/json",
        )
        assert r.status_code == 401, (
            f"/api/bot/logout without token expected exactly 401, got {r.status_code}. "
            f"If 404: /api/bot/logout endpoint is NOT in the active bot_api. "
            f"This test uses bot_api.py which must have the /logout route."
        )
        print("       [INFO] /api/bot/logout without token correctly returns 401")


def test_logout_with_bad_token_returns_401():
    """POST /api/bot/logout with invalid token -- must return 401."""
    with test_flask.test_client() as client:
        r = client.post(
            "/api/bot/logout",
            data="{}",
            content_type="application/json",
            headers={"Authorization": "Bearer totally-fake-bad-token-000"},
        )
        assert r.status_code == 401, (
            f"Expected 401 for invalid token, got {r.status_code}. Body: {r.data[:200]}"
        )
        print("       [INFO] /api/bot/logout with bad token returns 401 -- OK")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

check("/api/bot/logout in URL map with POST", test_logout_route_in_url_map)
check("setup test user in in-memory DB", test_setup_test_user)
check("link/verify with valid code returns api_token", test_link_verify_succeeds)
check("/api/bot/me returns 200 after link", test_me_returns_200_after_link)
check("telegram_id set after link verify", test_telegram_id_set_after_link)
check("/api/bot/logout returns 200 with valid token", test_logout_returns_200)
check("telegram_id is NULL after logout", test_telegram_id_null_after_logout)
check("session revoked_at is NOT NULL after logout", test_session_revoked_after_logout)
check("/api/bot/me returns 401 after logout", test_me_returns_401_after_logout)
check("/api/bot/logout without token returns 401 (NOT 404)", test_logout_without_token_returns_401)
check("/api/bot/logout with invalid token returns 401", test_logout_with_bad_token_returns_401)

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
    print("BOT002_LOGOUT_CLIENT_TEST_FAILED")
    sys.exit(1)
else:
    print()
    print("BOT002_LOGOUT_CLIENT_TEST_OK")
