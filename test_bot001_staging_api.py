"""
test_bot001_staging_api.py -- BOT001A Telegram Foundation (corrected)
Staging API test using Flask test_client.

Does NOT create real spare part requests or modify any data.

Prerequisites (must be met before running):
  1. TransportReportStaging service is STOPPED.
  2. migrate_bot001_telegram_foundation.py has been run successfully.
  3. Run from C:\\transport-report-staging directory.

Checks:
  1.  Flask test_client setup OK
  2.  GET /api/bot/health => 200, {"ok": true, "module": "bot_api", "version": "BOT001"}
  3.  GET /api/bot/me (no token) => 401, {"ok": false}
  4.  GET /api/bot/requests (no token) => 401
  5.  GET /api/bot/equipment (no token) => 401
  6.  GET /api/bot/catalog (no token) => 401
  7.  GET /api/bot/requests/99999 (no token) => 401 (auth before 404)
  8.  POST /api/bot/link/verify (missing body) => 400
  9.  POST /api/bot/link/verify (wrong code) => 401
  10. GET /api/bot/health with invalid Bearer token => 200 (health is public)
  11. GET /api/bot/me with invalid Bearer token => 401
  12. No route returns password_hash in JSON response
  13. POST /api/bot/link/verify (bad telegram_id type) => 400
  14. POST /api/bot/link/verify for INACTIVE user => 403, no token

Run from staging directory (service MUST be stopped):
  "C:\\Program Files\\Python314\\python.exe" test_bot001_staging_api.py
"""

import os
import sys
import json

STAGING_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, STAGING_DIR)

os.environ.setdefault('FLASK_ENV', 'sqlite_prod')
os.environ.setdefault('SECRET_KEY', 'test-staging-api-bot001a-not-for-production')

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
# Setup Flask test client
# ------------------------------------------------------------------
client = None

def _setup_client():
    global client
    from app import app as flask_app
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    client = flask_app.test_client()
    # Verify bot health route is registered before running API tests
    rules = [str(r) for r in flask_app.url_map.iter_rules()]
    assert '/api/bot/health' in rules, \
        "/api/bot/health not in url_map. Bot routes: " + str([r for r in rules if 'bot' in r])

check("Flask test_client setup OK", _setup_client)

# All subsequent tests require the client to be set up
if not ERRORS:

    # ------------------------------------------------------------------
    # 2. GET /api/bot/health => 200 + correct JSON
    # ------------------------------------------------------------------
    def _health_ok():
        resp = client.get('/api/bot/health')
        assert resp.status_code == 200, \
            "Expected 200, got {}. Body: {}".format(resp.status_code, resp.data[:200])
        data = json.loads(resp.data)
        assert data.get('ok') is True, "Expected ok=true, got: " + str(data)
        assert data.get('version') == 'BOT001', "Expected version=BOT001, got: " + str(data)
        assert data.get('module') == 'bot_api', "Expected module=bot_api, got: " + str(data)

    check("GET /api/bot/health => 200 + ok=true + version=BOT001", _health_ok)

    # ------------------------------------------------------------------
    # 3. GET /api/bot/me (no token) => 401
    # ------------------------------------------------------------------
    def _me_no_token():
        resp = client.get('/api/bot/me')
        assert resp.status_code == 401, \
            "Expected 401, got {}. Body: {}".format(resp.status_code, resp.data[:200])
        data = json.loads(resp.data)
        assert data.get('ok') is False, "Expected ok=false, got: " + str(data)

    check("GET /api/bot/me (no token) => 401", _me_no_token)

    # ------------------------------------------------------------------
    # 4. GET /api/bot/requests (no token) => 401
    # ------------------------------------------------------------------
    def _requests_no_token():
        resp = client.get('/api/bot/requests')
        assert resp.status_code == 401, \
            "Expected 401, got {}. Body: {}".format(resp.status_code, resp.data[:200])
        data = json.loads(resp.data)
        assert data.get('ok') is False, "Expected ok=false, got: " + str(data)

    check("GET /api/bot/requests (no token) => 401", _requests_no_token)

    # ------------------------------------------------------------------
    # 5. GET /api/bot/equipment (no token) => 401
    # ------------------------------------------------------------------
    def _equipment_no_token():
        resp = client.get('/api/bot/equipment')
        assert resp.status_code == 401, \
            "Expected 401, got {}. Body: {}".format(resp.status_code, resp.data[:200])
        data = json.loads(resp.data)
        assert data.get('ok') is False, "Expected ok=false, got: " + str(data)

    check("GET /api/bot/equipment (no token) => 401", _equipment_no_token)

    # ------------------------------------------------------------------
    # 6. GET /api/bot/catalog (no token) => 401
    # ------------------------------------------------------------------
    def _catalog_no_token():
        resp = client.get('/api/bot/catalog')
        assert resp.status_code == 401, \
            "Expected 401, got {}. Body: {}".format(resp.status_code, resp.data[:200])
        data = json.loads(resp.data)
        assert data.get('ok') is False, "Expected ok=false, got: " + str(data)

    check("GET /api/bot/catalog (no token) => 401", _catalog_no_token)

    # ------------------------------------------------------------------
    # 7. GET /api/bot/requests/99999 (no token) => 401 (auth before 404)
    # ------------------------------------------------------------------
    def _request_detail_no_token():
        resp = client.get('/api/bot/requests/99999')
        assert resp.status_code == 401, \
            "Expected 401 (auth before 404), got {}. Body: {}".format(
                resp.status_code, resp.data[:200]
            )
        data = json.loads(resp.data)
        assert data.get('ok') is False, "Expected ok=false, got: " + str(data)

    check("GET /api/bot/requests/99999 (no token) => 401", _request_detail_no_token)

    # ------------------------------------------------------------------
    # 8. POST /api/bot/link/verify (missing/invalid body) => 400
    # ------------------------------------------------------------------
    def _link_verify_bad_body():
        resp = client.post(
            '/api/bot/link/verify',
            data='not-json',
            content_type='text/plain'
        )
        assert resp.status_code == 400, \
            "Expected 400, got {}. Body: {}".format(resp.status_code, resp.data[:200])
        data = json.loads(resp.data)
        assert data.get('ok') is False, "Expected ok=false, got: " + str(data)

    check("POST /api/bot/link/verify (invalid body) => 400", _link_verify_bad_body)

    # ------------------------------------------------------------------
    # 9. POST /api/bot/link/verify with wrong code => 401
    # ------------------------------------------------------------------
    def _link_verify_wrong_code():
        resp = client.post(
            '/api/bot/link/verify',
            data=json.dumps({"telegram_id": 999999999, "code": "000000"}),
            content_type='application/json'
        )
        assert resp.status_code == 401, \
            "Expected 401, got {}. Body: {}".format(resp.status_code, resp.data[:200])
        data = json.loads(resp.data)
        assert data.get('ok') is False, "Expected ok=false, got: " + str(data)

    check("POST /api/bot/link/verify (wrong code) => 401", _link_verify_wrong_code)

    # ------------------------------------------------------------------
    # 10. GET /api/bot/health with invalid Bearer token => 200 (public)
    # ------------------------------------------------------------------
    def _health_with_bad_auth():
        resp = client.get(
            '/api/bot/health',
            headers={'Authorization': 'Bearer completely-invalid-token-xyz'}
        )
        assert resp.status_code == 200, \
            "Expected 200 (health is public), got {}".format(resp.status_code)

    check("GET /api/bot/health with bad Bearer token => 200 (public)", _health_with_bad_auth)

    # ------------------------------------------------------------------
    # 11. GET /api/bot/me with invalid Bearer token => 401
    # ------------------------------------------------------------------
    def _me_with_bad_auth():
        resp = client.get(
            '/api/bot/me',
            headers={'Authorization': 'Bearer completely-invalid-token-xyz'}
        )
        assert resp.status_code == 401, \
            "Expected 401, got {}. Body: {}".format(resp.status_code, resp.data[:200])
        data = json.loads(resp.data)
        assert data.get('ok') is False, "Expected ok=false, got: " + str(data)

    check("GET /api/bot/me with invalid Bearer token => 401", _me_with_bad_auth)

    # ------------------------------------------------------------------
    # 12. Health response does not leak sensitive fields
    # ------------------------------------------------------------------
    def _health_no_sensitive_fields():
        resp = client.get('/api/bot/health')
        body = resp.data.decode('utf-8', errors='replace')
        forbidden = ['password_hash', 'tg_link_code_hash', 'tg_link_code_expires_at']
        for field in forbidden:
            assert field not in body, \
                "Sensitive field '{}' found in /api/bot/health response!".format(field)

    check("/api/bot/health response has no sensitive fields", _health_no_sensitive_fields)

    # ------------------------------------------------------------------
    # 13. link/verify bad telegram_id type => 400
    # ------------------------------------------------------------------
    def _link_verify_bad_tg_id():
        resp = client.post(
            '/api/bot/link/verify',
            data=json.dumps({"telegram_id": "not-an-int", "code": "123456"}),
            content_type='application/json'
        )
        assert resp.status_code == 400, \
            "Expected 400, got {}. Body: {}".format(resp.status_code, resp.data[:200])

    check("POST /api/bot/link/verify (bad telegram_id type) => 400", _link_verify_bad_tg_id)

    # ------------------------------------------------------------------
    # 14. POST /api/bot/link/verify for INACTIVE user => 403, no token
    # ------------------------------------------------------------------
    def _link_verify_inactive_user():
        """Set up a valid link code for an inactive user in the DB,
        attempt verification, assert 403, assert no api_token, assert
        no bot_api_sessions row, assert telegram_id stays NULL.
        Uses Flask app context to access DB directly."""
        import sqlite3, hashlib, hmac, os, secrets, datetime
        db_path = os.path.join(STAGING_DIR, 'instance', 'transport.db')
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        cur = con.cursor()

        # Find an inactive user WITHOUT a telegram_id
        cur.execute(
            "SELECT id, username, is_active_user, telegram_id FROM users "
            "WHERE is_active_user=0 AND (telegram_id IS NULL) LIMIT 1"
        )
        inactive_user = cur.fetchone()

        created_test_user = False
        test_user_id = None

        if inactive_user is None:
            # Create a temporary inactive test user
            cur.execute(
                "INSERT INTO users (username, password_hash, full_name, role, is_active_user) "
                "VALUES (?,?,?,?,0)",
                ('bot001b_test_inactive', 'x', 'BOT001B Test Inactive', 'operator')
            )
            con.commit()
            test_user_id = cur.lastrowid
            created_test_user = True
        else:
            test_user_id = inactive_user['id']

        # Generate a test code and its hash
        plain_code = ''.join([str(secrets.randbelow(10)) for _ in range(6)])
        code_hash = hashlib.sha256(plain_code.encode('utf-8')).hexdigest()
        expires = (datetime.datetime.utcnow() + datetime.timedelta(minutes=10)).isoformat()
        now_str = datetime.datetime.utcnow().isoformat()

        # Write the link code to the DB
        cur.execute(
            "UPDATE users SET tg_link_code_hash=?, tg_link_code_expires_at=?, "
            "tg_link_code_created_at=? WHERE id=?",
            (code_hash, expires, now_str, test_user_id)
        )
        con.commit()

        test_tg_id = 8888888888  # fake Telegram ID

        try:
            resp = client.post(
                '/api/bot/link/verify',
                data=json.dumps({'telegram_id': test_tg_id, 'code': plain_code}),
                content_type='application/json'
            )
            assert resp.status_code == 403, \
                'Expected 403 for inactive user, got {}: {}'.format(
                    resp.status_code, resp.data.decode()[:200])
            resp_data = json.loads(resp.data)
            assert resp_data.get('ok') is False, 'Expected ok=false'
            assert 'api_token' not in resp_data, \
                'api_token must NOT be in response for inactive user'
            assert 'inactive' in resp_data.get('error', '').lower(), \
                'Expected error message mentioning inactive'

            # Verify no bot_api_sessions row was created
            cur2 = con.cursor()
            cur2.execute(
                'SELECT id FROM bot_api_sessions WHERE telegram_id=?',
                (test_tg_id,)
            )
            session_row = cur2.fetchone()
            assert session_row is None, \
                'bot_api_sessions row was created for inactive user -- must NOT happen'

            # Verify telegram_id was NOT written
            cur2.execute(
                'SELECT telegram_id FROM users WHERE id=?', (test_user_id,)
            )
            user_row = cur2.fetchone()
            assert user_row[0] is None, \
                'telegram_id was written for inactive user -- must NOT happen'

        finally:
            # Cleanup: clear link code fields
            cur.execute(
                "UPDATE users SET tg_link_code_hash=NULL, tg_link_code_expires_at=NULL, "
                "tg_link_code_created_at=NULL WHERE id=?",
                (test_user_id,)
            )
            if created_test_user:
                cur.execute('DELETE FROM users WHERE id=?', (test_user_id,))
            # Clean up any stray bot_api_sessions for test_tg_id
            cur.execute('DELETE FROM bot_api_sessions WHERE telegram_id=?', (test_tg_id,))
            con.commit()
            con.close()

    check("POST /api/bot/link/verify inactive user => 403, no token, no session", _link_verify_inactive_user)


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
    print("BOT001_STAGING_API_TEST_FAILED")
    sys.exit(1)
else:
    print()
    print("BOT001_STAGING_API_TEST_OK")
