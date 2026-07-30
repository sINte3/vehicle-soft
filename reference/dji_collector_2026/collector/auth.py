"""
DJI SmartFarm authentication.

Strategy:
  1. Launch Chromium via Playwright (headful on first run — easier to debug;
     flip HEADLESS=True later).
  2. Fill credentials, submit, wait for navigation away from /login.
  3. Intercept the first XHR to kr-ag2-api.dji.com and grab X-Auth-Token
     from its request headers.
  4. Decode JWT payload (HS384, payload is base64url, no verification needed
     — we only want the `exp` claim).
  5. Save token + exp to data/token.json.

Token file format:
    {"token": "eyJhbGci...", "exp": 1775999999, "saved_at": 1775886000}
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
TOKEN_FILE = DATA_DIR / "token.json"
STORAGE_STATE = DATA_DIR / "storage_state.json"

LOGIN_URL = os.getenv("DJI_LOGIN_URL", "https://www.djiag.com/login")
USERNAME = os.getenv("DJI_LOGIN")
PASSWORD = os.getenv("DJI_PASSWORD")

HEADLESS = os.getenv("DJI_HEADLESS", "false").lower() == "true"

log = logging.getLogger(__name__)


def _decode_jwt_exp(token: str) -> int | None:
    """Extract `exp` from a JWT payload without verifying the signature."""
    try:
        payload_b64 = token.split(".")[1]
        # JWT uses base64url without padding
        padding = "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
        return int(payload.get("exp", 0)) or None
    except Exception as e:
        log.warning("Failed to decode JWT exp: %s", e)
        return None


def _do_login(page) -> None:
    """
    DJI SmartFarm login flow:
      1. Dismiss cookie banner if present.
      2. Tick the Privacy Policy consent checkbox.
      3. Click "Log in with DJI account" -> redirects to DJI SSO.
      4. On SSO page, fill email + password and submit.
      5. Wait for redirect back to djiag.com.
    """
    # 1. Cookie banner
    try:
        accept = page.locator(
            "button:has-text('Accept All Cookies'), "
            "button:has-text('Accept all'), "
            "button:has-text('Принять')"
        ).first
        if accept.is_visible(timeout=3000):
            log.info("Accepting cookies")
            accept.click()
            page.wait_for_timeout(500)
    except Exception:
        log.info("No cookie banner found, continuing")

    # 2. Consent checkbox — must be ticked before the login button activates
    try:
        consent = page.locator("input[type='checkbox']").first
        consent.wait_for(state="visible", timeout=5000)
        if not consent.is_checked():
            log.info("Ticking consent checkbox")
            # Checkbox can be visually hidden behind a custom UI element;
            # clicking the <label> is more reliable than the input itself.
            try:
                consent.check()
            except Exception:
                page.locator("label:has-text('I have read')").first.click()
    except PWTimeout:
        log.warning("Consent checkbox not found — page layout may have changed")

    # 3. Click "Log in with DJI account"
    log.info("Clicking 'Log in with DJI account'")
    page.locator(
        "button:has-text('Log in with DJI account'), "
        "button:has-text('Log in'), a:has-text('Log in with DJI account')"
    ).first.click()

    # 4. Wait for redirect to DJI SSO domain
    log.info("Waiting for SSO page")
    try:
        page.wait_for_url(
            lambda u: "djiag.com" not in u or "account.dji.com" in u,
            timeout=15000,
        )
    except PWTimeout:
        log.warning("No redirect detected, trying to fill on current page anyway")
    log.info("Current URL after SSO redirect: %s", page.url)

    # 5. Fill credentials on the SSO page
    pwd = page.locator("input[type='password']").first
    pwd.wait_for(state="visible", timeout=20000)

    all_inputs = page.locator("input:visible").all()
    username_field = None
    for inp in all_inputs:
        try:
            t = (inp.get_attribute("type") or "text").lower()
        except Exception:
            t = "text"
        if t not in ("password", "checkbox", "radio", "hidden", "submit", "button"):
            username_field = inp
            break

    if username_field is None:
        raise RuntimeError(
            f"On SSO page found password but no username input among {len(all_inputs)} inputs"
        )

    log.info("Filling SSO credentials")
    username_field.click()
    username_field.fill(USERNAME)
    pwd.click()
    pwd.fill(PASSWORD)

    # Some SSO pages have their own consent checkbox — tick any unchecked ones
    try:
        for cb in page.locator("input[type='checkbox']:visible").all():
            if not cb.is_checked():
                try:
                    cb.check()
                except Exception:
                    pass
    except Exception:
        pass

    # 6. Submit
    # NOTE: we must check hostname, not substring — the SSO URL contains
    # ?backUrl=https%3A%2F%2Fwww.djiag.com which would make a substring match
    # return True immediately.
    from urllib.parse import urlparse
    def _on_djiag(u: str) -> bool:
        try:
            return urlparse(u).hostname == "www.djiag.com"
        except Exception:
            return False

    pwd.press("Enter")
    try:
        page.wait_for_url(_on_djiag, timeout=25000)
        return
    except PWTimeout:
        log.info("Enter did not submit, trying submit button")

    btn = page.locator(
        "button[type='submit'], "
        "button:has-text('Log In'), button:has-text('Log in'), "
        "button:has-text('Sign in'), button:has-text('Login'), "
        "button:has-text('登录')"
    ).first
    btn.click(timeout=5000)
    page.wait_for_url(_on_djiag, timeout=25000)


def login_and_capture_token() -> dict:
    """Run a full login flow and return {token, exp, saved_at}."""
    if not USERNAME or not PASSWORD:
        raise RuntimeError("DJI_LOGIN / DJI_PASSWORD not set in .env")

    captured: dict[str, str] = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(
            locale="ru-RU",
            storage_state=str(STORAGE_STATE) if STORAGE_STATE.exists() else None,
        )
        page = context.new_page()

        # Intercept any request carrying X-Auth-Token
        def on_request(req):
            if "kr-ag2-api.dji.com" in req.url:
                tok = req.headers.get("x-auth-token")
                if tok and "token" not in captured:
                    captured["token"] = tok
                    log.info("Captured X-Auth-Token from %s", req.url[:80])

        page.on("request", on_request)

        page.goto(LOGIN_URL, wait_until="domcontentloaded")

        # If storage_state brought us in already, we may not see /login at all
        if "/login" in page.url:
            log.info("Filling login form")
            try:
                _do_login(page)
            except Exception:
                # Dump page state so we can see exactly what went wrong
                shot = DATA_DIR / "login_failure.png"
                html = DATA_DIR / "login_failure.html"
                try:
                    page.screenshot(path=str(shot), full_page=True)
                    html.write_text(page.content(), encoding="utf-8")
                    log.error("Saved %s and %s", shot, html)
                except Exception as dump_err:
                    log.error("Could not save debug dump: %s", dump_err)
                raise

        log.info("Logged in, current URL: %s", page.url)

        # Navigate to /records to force an API call that carries the token
        if "token" not in captured:
            page.goto("https://www.djiag.com/records", wait_until="domcontentloaded")
            # Wait up to 15s for an API request
            deadline = time.time() + 15
            while "token" not in captured and time.time() < deadline:
                page.wait_for_timeout(300)

        context.storage_state(path=str(STORAGE_STATE))
        browser.close()

    if "token" not in captured:
        raise RuntimeError("Login succeeded but no X-Auth-Token captured")

    token = captured["token"]
    exp = _decode_jwt_exp(token)
    record = {"token": token, "exp": exp, "saved_at": int(time.time())}
    TOKEN_FILE.write_text(json.dumps(record, indent=2))
    log.info("Token saved. exp=%s (in %s sec)", exp, exp - int(time.time()) if exp else "?")
    return record


def load_token() -> dict | None:
    if not TOKEN_FILE.exists():
        return None
    rec = json.loads(TOKEN_FILE.read_text())
    # 60s safety margin
    if rec.get("exp") and rec["exp"] - 60 < int(time.time()):
        log.info("Token expired or about to expire")
        return None
    return rec


def get_token(force_refresh: bool = False) -> str:
    if not force_refresh:
        rec = load_token()
        if rec:
            return rec["token"]
    return login_and_capture_token()["token"]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    tok = get_token(force_refresh=True)
    print(f"OK, token length={len(tok)}")
