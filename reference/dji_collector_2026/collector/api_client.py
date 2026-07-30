"""
Response-interception DJI SmartFarm client.

We can't call the API ourselves — DJI requires an HMAC Signature that only
its own axios interceptor generates. Solution: open the /records page and
capture flight_records JSON responses via page.on('response') while we
drive pagination through the UI.

Flow:
  1. Ensure storage_state.json (login) is fresh.
  2. Open headful Chromium on /records.
  3. Attach a response listener that keeps every flight_records payload.
  4. Wait for the first batch (the site auto-fetches on load).
  5. Click the Ant Design "next page" button repeatedly until we've covered
     all pages reported by the first batch's meta_data.total_pages.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from collector import auth

load_dotenv()

HEADLESS = os.getenv("DJI_HEADLESS", "false").lower() == "true"

ROOT = Path(__file__).resolve().parent.parent
STORAGE_STATE = ROOT / "data" / "storage_state.json"

log = logging.getLogger(__name__)


class DjiApiError(Exception):
    pass


class DjiClient:
    def __init__(self):
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._responses: list[dict] = []  # all captured flight_records payloads

    def __enter__(self):
        self._start()
        return self

    def __exit__(self, *_):
        self.close()

    def _start(self):
        auth.get_token()
        if not STORAGE_STATE.exists():
            raise RuntimeError("storage_state.json missing")
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=HEADLESS)
        self._context = self._browser.new_context(
            storage_state=str(STORAGE_STATE),
            locale="en-US",
        )
        self._page = self._context.new_page()

        def on_response(resp):
            try:
                if "/flight_records" in resp.url and "/flight_records/" not in resp.url:
                    if resp.status == 200:
                        body = resp.json()
                        self._responses.append(body)
                        meta = body.get("meta_data", {}) or {}
                        log.info("Captured page %s/%s (%s records)",
                                 meta.get("current_page"),
                                 meta.get("total_pages"),
                                 len(body.get("data", [])))
            except Exception as e:
                log.debug("Response parse skipped: %s", e)

        self._page.on("response", on_response)

    def close(self):
        try:
            if self._context: self._context.close()
            if self._browser: self._browser.close()
            if self._pw: self._pw.stop()
        except Exception:
            pass

    def fetch_all_flights(self) -> list[dict]:
        """Navigate /records, paginate UI, return flat list of flight records."""
        self._responses.clear()
        all_urls: list[str] = []

        # Log EVERY response URL for diagnostics
        def log_all(resp):
            all_urls.append(f"{resp.status} {resp.url[:140]}")

        self._page.on("response", log_all)

        log.info("Opening /records")
        self._page.goto("https://www.djiag.com/records", wait_until="domcontentloaded")
        log.info("Page URL after goto: %s", self._page.url)

        # Dismiss the SmartFarm cookie banner if present — it overlays the
        # bottom of the page and blocks clicks on the pagination control.
        try:
            cc_accept = self._page.locator(
                "button.cc-consent-accept, button:has-text('Accept All Cookies')"
            ).first
            if cc_accept.is_visible(timeout=3000):
                log.info("Dismissing SmartFarm cookie banner")
                cc_accept.click()
                self._page.wait_for_timeout(500)
        except Exception:
            log.info("No cookie banner to dismiss")

        # Page opens in Map mode by default — that only hits /flight_records/overview.
        # Click the "List" toggle to make it fetch the actual /flight_records list.
        log.info("Switching to List view")
        try:
            list_btn = self._page.locator(
                "button:has-text('List'), "
                "[role='tab']:has-text('List'), "
                "div:has-text('List'):not(:has(*)), "
                "span:has-text('List'):not(:has(*))"
            ).first
            list_btn.wait_for(state="visible", timeout=15000)
            list_btn.click()
            log.info("Clicked List")
        except PWTimeout:
            log.warning("List button not found — dumping state")

        # Wait for first batch to arrive — 60s window
        deadline = time.time() + 60
        while not self._responses and time.time() < deadline:
            self._page.wait_for_timeout(500)

        if not self._responses:
            # Dump diagnostics
            shot = ROOT / "data" / "records_failure.png"
            html = ROOT / "data" / "records_failure.html"
            urls_file = ROOT / "data" / "records_failure_urls.txt"
            try:
                self._page.screenshot(path=str(shot), full_page=True)
                html.write_text(self._page.content(), encoding="utf-8")
                urls_file.write_text("\n".join(all_urls), encoding="utf-8")
                log.error("Dumped %s, %s, %s", shot, html, urls_file)
                log.error("Final URL: %s", self._page.url)
                log.error("Total network requests seen: %s", len(all_urls))
                for u in all_urls[-20:]:
                    log.error("  %s", u)
            except Exception as e:
                log.error("Dump failed: %s", e)
            raise DjiApiError("Site did not fetch /flight_records within 60s")

        first = self._responses[0]
        meta = first.get("meta_data", {}) or {}
        total_pages = meta.get("total_pages") or 1
        log.info("Total pages reported: %s", total_pages)

        # Drive Ant Design pagination
        clicks = 0
        while len({(r.get("meta_data") or {}).get("current_page") for r in self._responses}) < total_pages:
            before = len(self._responses)
            next_btn = self._page.locator(
                "li.ant-pagination-next[aria-disabled='false']"
            ).first
            try:
                # JS click — bypasses any overlays that might intercept a real click
                next_btn.evaluate("el => el.click()")
            except Exception as e:
                log.warning("Next-page click failed: %s", e)
                break
            clicks += 1
            # Wait for a new response
            wait_until = time.time() + 15
            while len(self._responses) == before and time.time() < wait_until:
                self._page.wait_for_timeout(200)
            if len(self._responses) == before:
                log.warning("No new response after click, stopping")
                break
            if clicks > total_pages + 5:
                break

        # Deduplicate by DJI flight id, keep the latest occurrence
        flights: dict[int, dict] = {}
        for payload in self._responses:
            for rec in payload.get("data", []) or []:
                flights[int(rec["id"])] = rec
        log.info("Collected %s unique flights", len(flights))
        return list(flights.values())
