"""
bot_http_client.py -- BOT002 HTTP Client

Provides simple HTTP GET/POST functions using only stdlib urllib.
No external dependencies.

Security rules:
- Never log Bearer tokens.
- Raise BotApiError for non-2xx responses with meaningful messages.
- Handle connection errors gracefully.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------

class BotApiError(Exception):
    """Raised when the API returns a non-2xx response or a connection error.

    Attributes:
        status_code: HTTP status code (int), or 0 for connection errors.
        message:     Human-readable error description.
    """

    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code
        self.message = message

    def __str__(self) -> str:
        if self.status_code:
            return f"HTTP {self.status_code}: {self.message}"
        return f"Connection error: {self.message}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_url(base_url: str, path: str, params: dict | None = None) -> str:
    """Combine base_url + path, appending query params if provided."""
    base_url = base_url.rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    url = base_url + path
    if params:
        query_string = urllib.parse.urlencode(
            {k: v for k, v in params.items() if v is not None}
        )
        if query_string:
            url = url + "?" + query_string
    return url


def _make_headers(token: str | None = None) -> dict:
    """Build request headers. Never include a log-safe representation of the token."""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    return headers


def _parse_response(response) -> Any:
    """Read and JSON-parse a urllib response object."""
    raw = response.read()
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BotApiError(f"Invalid JSON response from API: {exc}", status_code=0)


def _handle_http_error(exc: urllib.error.HTTPError) -> None:
    """Convert urllib.error.HTTPError to BotApiError with a clear message."""
    status = exc.code
    try:
        raw = exc.read()
        body = json.loads(raw.decode("utf-8")) if raw else {}
        msg = body.get("error") or body.get("message") or exc.reason or str(exc)
    except Exception:
        msg = exc.reason or str(exc)

    if status == 400:
        raise BotApiError(f"Bad request: {msg}", status_code=400)
    elif status == 401:
        raise BotApiError(f"Unauthorized: {msg}", status_code=401)
    elif status == 403:
        raise BotApiError(f"Forbidden: {msg}", status_code=403)
    elif status == 404:
        raise BotApiError(f"Not found: {msg}", status_code=404)
    elif status == 409:
        raise BotApiError(f"Conflict: {msg}", status_code=409)
    elif status >= 500:
        raise BotApiError(f"Server error ({status}): {msg}", status_code=status)
    else:
        raise BotApiError(f"HTTP {status}: {msg}", status_code=status)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def api_get(
    base_url: str,
    path: str,
    token: str | None = None,
    params: dict | None = None,
    timeout: int = 10,
) -> Any:
    """Perform a GET request to the Flask API.

    Args:
        base_url: API base URL (e.g. http://127.0.0.1:5051).
        path:     Endpoint path (e.g. /api/bot/me).
        token:    Bearer token string. Never logged.
        params:   Optional query parameters dict.
        timeout:  Request timeout in seconds.

    Returns:
        Parsed JSON response body.

    Raises:
        BotApiError: On non-2xx response or connection error.
    """
    url = _build_url(base_url, path, params)
    headers = _make_headers(token)
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return _parse_response(resp)
    except urllib.error.HTTPError as exc:
        _handle_http_error(exc)
    except urllib.error.URLError as exc:
        raise BotApiError(
            f"Cannot connect to API at {base_url}: {exc.reason}",
            status_code=0,
        )
    except TimeoutError:
        raise BotApiError(
            f"Request timed out after {timeout}s: GET {path}",
            status_code=0,
        )


def api_post(
    base_url: str,
    path: str,
    token: str | None = None,
    payload: dict | None = None,
    timeout: int = 10,
) -> Any:
    """Perform a POST request to the Flask API.

    Args:
        base_url: API base URL.
        path:     Endpoint path.
        token:    Bearer token string. Never logged.
        payload:  Optional request body dict (serialized as JSON).
        timeout:  Request timeout in seconds.

    Returns:
        Parsed JSON response body.

    Raises:
        BotApiError: On non-2xx response or connection error.
    """
    url = _build_url(base_url, path)
    headers = _make_headers(token)
    body = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return _parse_response(resp)
    except urllib.error.HTTPError as exc:
        _handle_http_error(exc)
    except urllib.error.URLError as exc:
        raise BotApiError(
            f"Cannot connect to API at {base_url}: {exc.reason}",
            status_code=0,
        )
    except TimeoutError:
        raise BotApiError(
            f"Request timed out after {timeout}s: POST {path}",
            status_code=0,
        )
