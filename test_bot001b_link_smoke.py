"""
test_bot001b_link_smoke.py -- BOT001B Inactive-User Guard
Link smoke test for staging.

Tests both:
  A) Inactive user link attempt => 403, no token, no link written
  B) Active user link attempt => 200, token, /api/bot/me => 200,
     /api/bot/requests => 200, then cleanup

Prerequisites:
  - TransportReportStaging service must be STOPPED before running.
  - migrate_bot001_telegram_foundation.py must have been run (BOT001A migration done).
  - Run from C:\\transport-report-staging directory.

The test uses Flask test_client (not HTTP to the running service).
The service must be stopped so the DB is not locked.

Run from staging directory (SERVICE MUST BE STOPPED):
  "C:\\Program Files\\Python314\\python.exe" test_bot001b_link_smoke.py

Expected output:
  BOT001B_INACTIVE_LINK_GUARD_OK
  BOT001B_ACTIVE_LINK_SMOKE_OK
  BOT001B_LINK_SMOKE_OK
"""

import os
import sys
import json

STAGING_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, STAGING_DIR)

os.environ.setdefault('FLASK_ENV', 'sqlite_prod')
os.environ.setdefault('SECRET_KEY', 'test-bot001b-smoke-not-for-production')

ERRORS = []
PASSED = []


def check(label, fn):
    try:
        fn()
        PASSED.append(label)
        print('[OK]   ' + label)
    except Exception as exc:
        ERRORS.append((label, str(exc)))
        print('[FAIL] ' + label)
        print('       ' + str(exc)[:300])


# ------------------------------------------------------------------
# Setup Flask test client (service must be stopped)
# ------------------------------------------------------------------
from app import app as flask_app
from models import db, User, BotApiSession
from bot_security import hash_secret, generate_link_code, utcnow

flask_app.config['TESTING'] = True
flask_app.config['WTF_CSRF_ENABLED'] = False
client = flask_app.test_client()
print('[INFO] Flask test_client ready.')
print()

# ==================================================================================
# SCENARIO A: Inactive user link attempt => 403
# ==================================================================================
print('--- SCENARIO A: Inactive user link attempt ---')

_inactive_user_id = None
_created_inactive_user = False
_test_tg_id_inactive = 7777777701
_inactive_plain_code = None


def _setup_inactive_user():
    """Find or create inactive user; set valid link code via SQLAlchemy (app context)."""
    global _inactive_user_id, _created_inactive_user, _inactive_plain_code
    from datetime import timedelta

    with flask_app.app_context():
        # Find existing inactive user without telegram_id
        user = User.query.filter(
            User.is_active_user == False,
            User.telegram_id == None
        ).first()

        if user is None:
            # Create temporary inactive test user
            user = User(
                username='bot001b_smoke_inactive',
                full_name='BOT001B Smoke Inactive',
                role='operator',
                is_active_user=False,
            )
            user.set_password('temporary_test_password_not_used')
            db.session.add(user)
            db.session.flush()
            _created_inactive_user = True

        _inactive_user_id = user.id

        # Set a valid link code using bot_security helpers
        plain_code = generate_link_code(6)
        _inactive_plain_code = plain_code
        now = utcnow()
        user.tg_link_code_hash = hash_secret(plain_code)
        user.tg_link_code_expires_at = now + timedelta(minutes=10)
        user.tg_link_code_created_at = now
        db.session.commit()

check("Setup: inactive user with valid link code (via SQLAlchemy)", _setup_inactive_user)


def _inactive_verify_403():
    """Attempt link/verify for inactive user; expect 403, no token, no writes."""
    assert _inactive_plain_code is not None, 'Setup failed -- no code available'
    resp = client.post(
        '/api/bot/link/verify',
        data=json.dumps({'telegram_id': _test_tg_id_inactive, 'code': _inactive_plain_code}),
        content_type='application/json'
    )
    assert resp.status_code == 403, \
        'Expected 403 for inactive user, got {}: {}'.format(
            resp.status_code, resp.data.decode()[:300])
    data = json.loads(resp.data)
    assert data.get('ok') is False, 'Expected ok=false, got: ' + str(data)
    assert 'api_token' not in data, \
        'api_token must NOT appear in 403 response! Got keys: ' + str(list(data.keys()))
    assert 'inactive' in data.get('error', '').lower(), \
        'Expected error message to mention inactive, got: ' + str(data.get('error'))

check("POST /api/bot/link/verify inactive user => 403, ok=false, no api_token", _inactive_verify_403)


def _inactive_no_session_row():
    """Verify no bot_api_sessions row was created for the inactive test tg_id."""
    with flask_app.app_context():
        session = BotApiSession.query.filter_by(telegram_id=_test_tg_id_inactive).first()
    assert session is None, \
        'bot_api_sessions row was created for inactive user -- must NOT happen'

check("No bot_api_sessions row created for inactive user", _inactive_no_session_row)


def _inactive_no_tg_id_written():
    """Verify telegram_id was NOT written to users table for inactive user."""
    assert _inactive_user_id is not None, 'Setup failed'
    with flask_app.app_context():
        user = User.query.get(_inactive_user_id)
    assert user is not None, 'Test user not found in DB'
    assert user.telegram_id is None, \
        'telegram_id was written for inactive user -- must NOT happen. Got: ' + str(user.telegram_id)

check("telegram_id NOT written for inactive user", _inactive_no_tg_id_written)


def _cleanup_inactive_user():
    """Clean up inactive user test data."""
    with flask_app.app_context():
        if _inactive_user_id:
            user = User.query.get(_inactive_user_id)
            if user:
                user.tg_link_code_hash = None
                user.tg_link_code_expires_at = None
                user.tg_link_code_created_at = None
                user.telegram_id = None
                if _created_inactive_user:
                    db.session.delete(user)
                db.session.commit()
        # Clean any stray sessions
        BotApiSession.query.filter_by(telegram_id=_test_tg_id_inactive).delete()
        db.session.commit()

check("Cleanup: inactive user test data removed", _cleanup_inactive_user)


# Report scenario A
a_errors = [(l, e) for l, e in ERRORS]
if not a_errors:
    print()
    print('BOT001B_INACTIVE_LINK_GUARD_OK')
    print()
else:
    print()
    print('BOT001B_INACTIVE_LINK_GUARD_FAILED -- see errors above')
    print()


# ==================================================================================
# SCENARIO B: Active user link attempt => 200, token, /me and /requests work
# ==================================================================================
print('--- SCENARIO B: Active user link attempt (full smoke) ---')

# Reset error tracking for scenario B assessment
_b_errors_before = len(ERRORS)

_active_user_id = None
_active_raw_token = None
_test_tg_id_active = 7777777702
_active_plain_code = None


def _setup_active_user():
    """Find an active user with no telegram_id; set a valid link code."""
    global _active_user_id, _active_plain_code
    from datetime import timedelta

    with flask_app.app_context():
        user = User.query.filter(
            User.is_active_user == True,
            User.telegram_id == None
        ).first()

        if user is None:
            raise RuntimeError(
                'No active user with telegram_id IS NULL found in staging DB. '
                'Cannot run active link smoke test.'
            )

        _active_user_id = user.id
        plain_code = generate_link_code(6)
        _active_plain_code = plain_code
        now = utcnow()
        user.tg_link_code_hash = hash_secret(plain_code)
        user.tg_link_code_expires_at = now + timedelta(minutes=10)
        user.tg_link_code_created_at = now
        db.session.commit()

check("Setup: active user with valid link code (via SQLAlchemy)", _setup_active_user)


def _active_verify_200():
    """Verify active user link; expect 200, token, user.is_active=true."""
    global _active_raw_token
    assert _active_plain_code is not None, 'Setup failed -- no code available'
    resp = client.post(
        '/api/bot/link/verify',
        data=json.dumps({'telegram_id': _test_tg_id_active, 'code': _active_plain_code}),
        content_type='application/json'
    )
    assert resp.status_code == 200, \
        'Expected 200 for active user, got {}: {}'.format(
            resp.status_code, resp.data.decode()[:300])
    data = json.loads(resp.data)
    assert data.get('ok') is True, 'Expected ok=true, got: ' + str(data)
    assert 'api_token' in data, 'api_token missing from 200 response'
    assert data.get('user', {}).get('is_active') is True, \
        'user.is_active must be true in response, got: ' + str(data.get('user'))
    _active_raw_token = data['api_token']

check("POST /api/bot/link/verify active user => 200, ok=true, api_token present", _active_verify_200)


def _active_me_200():
    """Use the token to call /api/bot/me; expect 200."""
    assert _active_raw_token, 'No token from previous step'
    resp = client.get(
        '/api/bot/me',
        headers={'Authorization': 'Bearer ' + _active_raw_token}
    )
    assert resp.status_code == 200, \
        'Expected 200 from /api/bot/me, got {}: {}'.format(
            resp.status_code, resp.data.decode()[:200])
    data = json.loads(resp.data)
    assert data.get('ok') is True, 'Expected ok=true from /me, got: ' + str(data)
    assert 'user' in data, 'user missing from /me response'
    body = resp.data.decode('utf-8', errors='replace')
    for field in ['password_hash', 'tg_link_code_hash', 'tg_link_code_expires_at']:
        assert field not in body, 'Sensitive field in /me response: ' + field

check("GET /api/bot/me with valid token => 200, ok=true, no sensitive fields", _active_me_200)


def _active_requests_200():
    """Use the token to call /api/bot/requests; expect 200."""
    assert _active_raw_token, 'No token from previous step'
    resp = client.get(
        '/api/bot/requests',
        headers={'Authorization': 'Bearer ' + _active_raw_token}
    )
    assert resp.status_code == 200, \
        'Expected 200 from /api/bot/requests, got {}: {}'.format(
            resp.status_code, resp.data.decode()[:200])
    data = json.loads(resp.data)
    assert data.get('ok') is True, 'Expected ok=true from /requests, got: ' + str(data)
    assert 'requests' in data, 'requests key missing from response'

check("GET /api/bot/requests with valid token => 200, ok=true", _active_requests_200)


def _cleanup_active_user():
    """Cleanup: remove bot_api_sessions, reset telegram_id and link code fields."""
    with flask_app.app_context():
        if _active_user_id:
            user = User.query.get(_active_user_id)
            if user:
                user.tg_link_code_hash = None
                user.tg_link_code_expires_at = None
                user.tg_link_code_created_at = None
                user.telegram_id = None
                db.session.commit()
        # Remove all sessions for the test telegram_id
        BotApiSession.query.filter_by(telegram_id=_test_tg_id_active).delete()
        db.session.commit()

        # Verify cleanup
        if _active_user_id:
            user = User.query.get(_active_user_id)
            assert user is not None and user.telegram_id is None, \
                'telegram_id not reset to NULL after cleanup'
        remaining = BotApiSession.query.filter_by(telegram_id=_test_tg_id_active).count()
        assert remaining == 0, 'bot_api_sessions not fully cleaned up'

check("Cleanup: active user test data removed, telegram_id reset to NULL", _cleanup_active_user)


# Report scenario B
b_new_errors = ERRORS[_b_errors_before:]
if not b_new_errors:
    print()
    print('BOT001B_ACTIVE_LINK_SMOKE_OK')
    print()
else:
    print()
    print('BOT001B_ACTIVE_LINK_SMOKE_FAILED -- see errors above')
    print()


# ==================================================================================
# Final summary
# ==================================================================================
print('=' * 60)
print('Passed: {}/{}'.format(len(PASSED), len(PASSED) + len(ERRORS)))
if ERRORS:
    print()
    print('FAILED checks:')
    for label, err in ERRORS:
        print('  [FAIL] ' + label)
        print('         ' + str(err)[:200])
    print()
    print('BOT001B_LINK_SMOKE_FAILED')
    sys.exit(1)
else:
    print()
    print('BOT001B_LINK_SMOKE_OK')
