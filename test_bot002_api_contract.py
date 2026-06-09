"""
test_bot002_api_contract.py -- BOT002A Staging API Contract Tests

Tests the real staging API endpoints at http://10.103.25.14:5051
without creating any sessions, data, or Telegram tokens.

Checks:
  1. /api/bot/health             => 200
  2. /api/bot/logout without token => exactly 401 (NOT 404)
  3. /api/bot/me without token   => 401
  4. /api/bot/requests without token => 401
  5. /api/bot/equipment without token => 401
  6. /api/bot/catalog without token   => 401

NOTE: 404 for /api/bot/logout is FAILURE.
      If the live API returns 404, the endpoint is not deployed.
      Apply BOT002A files (bot_api.py -> bot_api.py) and restart
      TransportReportStaging before running this test against the live API.

NOTE: This test requires the TransportReportStaging service to be running.
      It does NOT stop or restart the service.
      It does NOT create real sessions.
      It does NOT create spare part requests.

Fallback: If the live API is not reachable, Flask test_client is used.
          The Flask test_client test ALWAYS requires exactly 401 for /api/bot/logout
          since it imports the local bot_api.py directly.

Output: BOT002_API_CONTRACT_TEST_OK (or error details + non-zero exit)
"""

import json
import os
import sys
import urllib.request
import urllib.error

STAGING_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, STAGING_DIR)

# Use Flask test_client if service not available; try live URL first.
STAGING_API_BASE = os.environ.get("BOT_API_BASE_URL", "http://10.103.25.14:5051")

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
        try:
            print("       " + str(exc)[:400])
        except (UnicodeEncodeError, UnicodeDecodeError):
            print("       " + str(exc).encode("ascii", errors="replace").decode("ascii")[:400])


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _do_get(path, token=None, timeout=10):
    """Perform a GET; return (status_code, body_dict)."""
    url = STAGING_API_BASE.rstrip("/") + path
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            body = json.loads(raw.decode("utf-8")) if raw else {}
            return resp.status, body
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        body = json.loads(raw.decode("utf-8")) if raw else {}
        return exc.code, body


def _do_post(path, token=None, payload=None, timeout=10):
    """Perform a POST; return (status_code, body_dict)."""
    url = STAGING_API_BASE.rstrip("/") + path
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    body = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            try:
                body_dict = json.loads(raw.decode("utf-8")) if raw else {}
            except (json.JSONDecodeError, UnicodeDecodeError):
                body_dict = {"_raw": raw[:200].decode("utf-8", errors="replace")}
            return resp.status, body_dict
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            body_dict = json.loads(raw.decode("utf-8")) if raw else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            body_dict = {"_raw": raw[:200].decode("utf-8", errors="replace")}
        return exc.code, body_dict


# ---------------------------------------------------------------------------
# Connectivity check
# ---------------------------------------------------------------------------

_api_reachable = False

def _check_connectivity():
    global _api_reachable
    try:
        status, body = _do_get("/api/bot/health", timeout=5)
        if status == 200:
            _api_reachable = True
            print("       [INFO] API reachable at: " + STAGING_API_BASE)
        else:
            print("       [WARN] API health returned status " + str(status))
    except Exception as exc:
        print("       [WARN] API not reachable: " + str(exc))
        _api_reachable = False


# ---------------------------------------------------------------------------
# Tests (live API)
# ---------------------------------------------------------------------------

def test_health_200():
    if not _api_reachable:
        raise AssertionError("API not reachable -- run with TransportReportStaging running")
    status, body = _do_get("/api/bot/health")
    assert status == 200, f"Expected 200, got {status}. Body: {body}"
    assert body.get("ok") is True, f"Expected ok=True, got: {body}"


def test_logout_without_token_401():
    """POST /api/bot/logout without token MUST return exactly 401.

    404 is NOT acceptable. If 404 is returned, the endpoint has not been
    deployed to the running service. Stop the service, apply bot_api.py
    as bot_api.py, and restart before running this test.
    """
    if not _api_reachable:
        raise AssertionError("API not reachable")
    status, body = _do_post("/api/bot/logout")
    # 404 = endpoint not deployed. This is a FAILURE per BOT002A requirements.
    # 401 = endpoint exists and correctly requires auth.
    assert status == 401, (
        f"Expected exactly 401 for /api/bot/logout without token, got {status}. "
        f"Body: {body}. "
        f"If status is 404: /api/bot/logout is not deployed. "
        f"Stop TransportReportStaging, copy bot_api.py to bot_api.py, restart service."
    )
    # Verify error response shape
    assert body.get("ok") is False or "error" in body or "Unauthorized" in str(body), \
        f"Expected error response on 401, got: {body}"
    print("       [INFO] /api/bot/logout endpoint is LIVE and returns 401 as required")


def test_me_without_token_401():
    if not _api_reachable:
        raise AssertionError("API not reachable")
    status, body = _do_get("/api/bot/me")
    assert status == 401, (
        f"Expected 401 for /api/bot/me without token, got {status}. Body: {body}"
    )


def test_requests_without_token_401():
    if not _api_reachable:
        raise AssertionError("API not reachable")
    status, body = _do_get("/api/bot/requests")
    assert status == 401, (
        f"Expected 401 for /api/bot/requests without token, got {status}. Body: {body}"
    )


def test_equipment_without_token_401():
    if not _api_reachable:
        raise AssertionError("API not reachable")
    status, body = _do_get("/api/bot/equipment")
    assert status == 401, (
        f"Expected 401 for /api/bot/equipment without token, got {status}. Body: {body}"
    )


def test_catalog_without_token_401():
    if not _api_reachable:
        raise AssertionError("API not reachable")
    status, body = _do_get("/api/bot/catalog")
    assert status == 401, (
        f"Expected 401 for /api/bot/catalog without token, got {status}. Body: {body}"
    )


def test_link_verify_invalid_json_400():
    """POST /api/bot/link/verify with empty body should return 400."""
    if not _api_reachable:
        raise AssertionError("API not reachable")
    url = STAGING_API_BASE.rstrip("/") + "/api/bot/link/verify"
    req = urllib.request.Request(
        url,
        data=b"",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            status = resp.status
            raw = resp.read()
            body = json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        status = exc.code
        raw = exc.read()
        body = json.loads(raw) if raw else {}
    assert status == 400, f"Expected 400 for empty body, got {status}. Body: {body}"


# ---------------------------------------------------------------------------
# Fallback: Flask test_client if API not reachable
# ---------------------------------------------------------------------------

def _run_flask_client_tests():
    """Use Flask test_client with bot_api.py blueprint when live API is not available.

    Loads bot_api.py (the BOT002A updated version) as a Flask Blueprint
    and tests it against an in-memory SQLite DB.

    CRITICAL: /api/bot/logout MUST return 401, not 404.
    If this test fails with 404, bot_api.py does not contain the /logout route.
    """
    print()
    print("       [INFO] Using Flask test_client with bot_api.py (live API not reachable)")

    import importlib.util
    from flask import Flask
    from models import db

    # Load bot_api.py
    new_bot_api_path = os.path.join(STAGING_DIR, "bot_api.py")
    assert os.path.exists(new_bot_api_path), (
        "bot_api.py not found. Run _write_bot002a.py to generate it."
    )
    spec = importlib.util.spec_from_file_location("bot_api_current_module_contract", new_bot_api_path)
    new_bot_api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(new_bot_api)

    # Create isolated Flask app with in-memory DB
    test_flask = Flask("test_contract_fallback")
    test_flask.config["TESTING"] = True
    test_flask.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
    test_flask.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    test_flask.config["SECRET_KEY"] = "bot002a-contract-fallback-not-for-prod"
    db.init_app(test_flask)
    test_flask.register_blueprint(new_bot_api.bot_api_bp)

    with test_flask.app_context():
        db.create_all()

    with test_flask.test_client() as client:
        # health
        r = client.get("/api/bot/health")
        assert r.status_code == 200, f"/api/bot/health expected 200, got {r.status_code}"
        body = json.loads(r.data)
        assert body.get("ok") is True
        assert body.get("version") in ("BOT001", "BOT002A"), f"Expected version BOT001 or BOT002A, got {body.get('version')}"

        # logout without token -- MUST be 401, not 404
        r = client.post("/api/bot/logout", content_type="application/json", data="{}")
        assert r.status_code == 401, (
            f"/api/bot/logout expected exactly 401, got {r.status_code}. "
            f"If 404: /logout route not in bot_api.py."
        )

        # me without token
        r = client.get("/api/bot/me")
        assert r.status_code == 401, f"/api/bot/me expected 401, got {r.status_code}"

        # requests without token
        r = client.get("/api/bot/requests")
        assert r.status_code == 401, f"/api/bot/requests expected 401, got {r.status_code}"

        # equipment without token
        r = client.get("/api/bot/equipment")
        assert r.status_code == 401, f"/api/bot/equipment expected 401, got {r.status_code}"

        # catalog without token
        r = client.get("/api/bot/catalog")
        assert r.status_code == 401, f"/api/bot/catalog expected 401, got {r.status_code}"

    print("       [INFO] Flask test_client (bot_api.py) checks passed")



# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

print("API base URL: " + STAGING_API_BASE)
_check_connectivity()

if _api_reachable:
    check("/api/bot/health => 200", test_health_200)
    check("/api/bot/logout without token => 401 (not 404)", test_logout_without_token_401)
    check("/api/bot/me without token => 401", test_me_without_token_401)
    check("/api/bot/requests without token => 401", test_requests_without_token_401)
    check("/api/bot/equipment without token => 401", test_equipment_without_token_401)
    check("/api/bot/catalog without token => 401", test_catalog_without_token_401)
    check("/api/bot/link/verify empty body => 400", test_link_verify_invalid_json_400)
    # Also run Flask client tests to verify bot_api.py is correct
    # (regardless of whether the live service has been updated yet)
    print()
    print("       [INFO] Also running Flask client checks (bot_api.py verification)...")
    check("Flask test_client (bot_api.py) contract checks", _run_flask_client_tests)
else:
    # Fallback to Flask test_client
    check("Flask test_client API contract checks (live API not reachable)", _run_flask_client_tests)

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
    print("BOT002_API_CONTRACT_TEST_FAILED")
    sys.exit(1)
else:
    print()
    print("BOT002_API_CONTRACT_TEST_OK")
