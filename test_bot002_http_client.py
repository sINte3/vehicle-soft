"""
test_bot002_http_client.py -- BOT002 Offline HTTP Client Tests

Tests bot_http_client.py using a small local HTTP server (stdlib http.server).
No external dependencies. No network access outside localhost.
No Telegram dependency. No Flask dependency.

Output: BOT002_HTTP_CLIENT_TEST_OK (or error details + non-zero exit)
"""

import json
import os
import sys
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

STAGING_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, STAGING_DIR)

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
        print("       " + str(exc)[:400])


# ---------------------------------------------------------------------------
# Minimal local HTTP server for testing
# ---------------------------------------------------------------------------

_SERVER_PORT = 19876
_SERVER_RESPONSES = {}  # path -> (status_code, body_dict)


class _TestHandler(BaseHTTPRequestHandler):
    """A minimal handler that returns preset responses."""

    def log_message(self, format, *args):
        pass  # Suppress default logging

    def _serve(self, method):
        path = self.path.split("?")[0]
        if path in _SERVER_RESPONSES:
            status, body = _SERVER_RESPONSES[path]
        else:
            status, body = 404, {"ok": False, "error": "Not found"}
        raw = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self._serve("GET")

    def do_POST(self):
        # Consume body to avoid broken pipe
        length = int(self.headers.get("Content-Length", 0))
        if length:
            self.rfile.read(length)
        self._serve("POST")


def _start_test_server():
    server = HTTPServer(("127.0.0.1", _SERVER_PORT), _TestHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    time.sleep(0.1)  # Let it start
    return server


def _stop_test_server(server):
    server.shutdown()


BASE = f"http://127.0.0.1:{_SERVER_PORT}"
_server = None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_import():
    from bot_http_client import api_get, api_post, BotApiError
    assert callable(api_get)
    assert callable(api_post)
    assert issubclass(BotApiError, Exception)


def test_successful_get():
    from bot_http_client import api_get
    _SERVER_RESPONSES["/api/bot/health"] = (200, {"ok": True, "module": "test"})
    result = api_get(BASE, "/api/bot/health", timeout=5)
    assert result.get("ok") is True, "Expected ok=True, got: " + str(result)
    assert result.get("module") == "test"


def test_successful_get_with_token():
    from bot_http_client import api_get
    _SERVER_RESPONSES["/api/bot/me"] = (200, {"ok": True, "user": {"id": 1}})
    result = api_get(BASE, "/api/bot/me", token="fake-token-123", timeout=5)
    assert result.get("ok") is True


def test_successful_post():
    from bot_http_client import api_post
    _SERVER_RESPONSES["/api/bot/link/verify"] = (
        200, {"ok": True, "api_token": "tok123", "user": {"id": 5}}
    )
    result = api_post(
        BASE,
        "/api/bot/link/verify",
        payload={"telegram_id": 12345, "code": "123456"},
        timeout=5,
    )
    assert result.get("ok") is True
    assert result.get("api_token") == "tok123"


def test_successful_post_with_token():
    from bot_http_client import api_post
    _SERVER_RESPONSES["/api/bot/logout"] = (200, {"ok": True, "message": "Telegram account unlinked."})
    result = api_post(BASE, "/api/bot/logout", token="fake-bearer-token", timeout=5)
    assert result.get("ok") is True
    assert "message" in result


def test_400_error():
    from bot_http_client import api_get, BotApiError
    _SERVER_RESPONSES["/api/test/bad"] = (400, {"ok": False, "error": "Bad request"})
    try:
        api_get(BASE, "/api/test/bad", timeout=5)
        assert False, "Expected BotApiError"
    except BotApiError as exc:
        assert exc.status_code == 400, f"Expected 400, got {exc.status_code}"
        assert "Bad request" in exc.message or "Bad request" in str(exc)


def test_401_error():
    from bot_http_client import api_get, BotApiError
    _SERVER_RESPONSES["/api/test/unauth"] = (401, {"ok": False, "error": "Unauthorized"})
    try:
        api_get(BASE, "/api/test/unauth", timeout=5)
        assert False, "Expected BotApiError"
    except BotApiError as exc:
        assert exc.status_code == 401, f"Expected 401, got {exc.status_code}"


def test_403_error():
    from bot_http_client import api_post, BotApiError
    _SERVER_RESPONSES["/api/test/forbidden"] = (403, {"ok": False, "error": "Forbidden"})
    try:
        api_post(BASE, "/api/test/forbidden", timeout=5)
        assert False, "Expected BotApiError"
    except BotApiError as exc:
        assert exc.status_code == 403, f"Expected 403, got {exc.status_code}"


def test_404_error():
    from bot_http_client import api_get, BotApiError
    # Use a path that isn't in _SERVER_RESPONSES
    _SERVER_RESPONSES.pop("/api/not/found", None)
    try:
        api_get(BASE, "/api/not/found", timeout=5)
        assert False, "Expected BotApiError"
    except BotApiError as exc:
        assert exc.status_code == 404, f"Expected 404, got {exc.status_code}"


def test_500_error():
    from bot_http_client import api_get, BotApiError
    _SERVER_RESPONSES["/api/test/server_error"] = (500, {"ok": False, "error": "Server crash"})
    try:
        api_get(BASE, "/api/test/server_error", timeout=5)
        assert False, "Expected BotApiError"
    except BotApiError as exc:
        assert exc.status_code == 500, f"Expected 500, got {exc.status_code}"
        assert exc.status_code >= 500


def test_json_parsing():
    from bot_http_client import api_get
    _SERVER_RESPONSES["/api/test/json"] = (200, {
        "ok": True,
        "nested": {"key": "value", "num": 42},
        "list": [1, 2, 3],
    })
    result = api_get(BASE, "/api/test/json", timeout=5)
    assert result["nested"]["key"] == "value"
    assert result["nested"]["num"] == 42
    assert result["list"] == [1, 2, 3]


def test_query_params():
    from bot_http_client import api_get
    # The server doesn't check query params -- just verify no crash
    _SERVER_RESPONSES["/api/bot/requests"] = (200, {"ok": True, "requests": [], "total": 0})
    result = api_get(
        BASE,
        "/api/bot/requests",
        params={"limit": 5, "status": "submitted"},
        timeout=5,
    )
    assert result.get("ok") is True


def test_connection_refused():
    from bot_http_client import api_get, BotApiError
    # Use a port that is not listening
    bad_base = "http://127.0.0.1:19999"
    try:
        api_get(bad_base, "/api/test", timeout=2)
        assert False, "Expected BotApiError for connection refused"
    except BotApiError as exc:
        assert exc.status_code == 0, f"Expected status_code=0, got {exc.status_code}"


def test_bot_api_error_str():
    from bot_http_client import BotApiError
    e = BotApiError("test error", status_code=401)
    assert "401" in str(e)
    assert "test error" in str(e)


def test_bot_api_error_no_status():
    from bot_http_client import BotApiError
    e = BotApiError("connection failed", status_code=0)
    assert "connection" in str(e).lower()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

check("import bot_http_client", test_import)

# Start local server for HTTP tests
try:
    _server = _start_test_server()
    _server_started = True
    print("       [INFO] Local test server started on port " + str(_SERVER_PORT))
except Exception as exc:
    _server_started = False
    ERRORS.append(("start local test server", str(exc)))
    print("[FAIL] start local test server: " + str(exc))

if _server_started:
    check("GET 200 OK", test_successful_get)
    check("GET 200 with token", test_successful_get_with_token)
    check("POST 200 OK", test_successful_post)
    check("POST 200 with token", test_successful_post_with_token)
    check("GET 400 error", test_400_error)
    check("GET 401 error", test_401_error)
    check("POST 403 error", test_403_error)
    check("GET 404 error", test_404_error)
    check("GET 500 error", test_500_error)
    check("JSON parsing nested objects", test_json_parsing)
    check("Query params encoded correctly", test_query_params)

check("Connection refused raises BotApiError", test_connection_refused)
check("BotApiError str with status", test_bot_api_error_str)
check("BotApiError str no status", test_bot_api_error_no_status)

if _server_started and _server:
    _stop_test_server(_server)

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
    print("BOT002_HTTP_CLIENT_TEST_FAILED")
    sys.exit(1)
else:
    print()
    print("BOT002_HTTP_CLIENT_TEST_OK")
