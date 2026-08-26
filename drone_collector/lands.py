# -*- coding: utf-8 -*-
"""drone_collector/lands.py -- snapshot of the DJI Field Management directory.

WHY THIS EXISTS
---------------
Every flight DJI reports carries `plot_name` empty -- on all 10 385 rows in
the database. The reverse-geocoded address is not a substitute: two different
farms came back as «Bukhara Region, 500200» on consecutive days, which is how
a whole day's work was attributed to the wrong customer. The one place the
customer's name exists is the Field Management directory, where the operator
typed it on the controller. 5 489 contours today.

The cabinet offers no export. This module takes the snapshot.

HOW IT WORKS, AND WHY NOT THE OBVIOUS WAY
-----------------------------------------
The page fetches its list from

    POST https://kr-ag2-api.dji.com/ag-plot/api/graphql?name=lands

signing every request with a `signature` header computed over the body: the
`content-md5` of the payload goes into the signed string, so changing one
character of the GraphQL query invalidates the signature and the server
answers a rejection code. Replaying the request from outside the browser with
a modified `after:` cursor therefore does NOT work, and reproducing DJI's
signing algorithm would be a reverse-engineering project that breaks on their
next deploy.

So this module does what the flight collector already does: it runs a real
browser with the saved session, lets the PAGE sign its own requests, and
listens. Pagination is driven by scrolling the list, because that is what
makes the page ask for the next twenty.

    first: 20, after: "0" -> "20" -> "40" ...   ~275 requests for 5 489 rows

At roughly a second and a half per request that is about seven minutes for
the whole directory, once.

WHAT IS DELIBERATELY NOT DONE HERE
----------------------------------
The polygon of each field is NOT downloaded. It sits behind
`geometry.storage.signedURL`, a pre-authenticated link that expires six hours
after it is issued -- 5 489 of them would be thousands of expiring bearer
credentials with a six-hour shelf life. The LIST payload already carries the
bounding box and the centre of every field, and a point-in-box test on a
flight's lat/lng is what the reconciliation actually needs. The stable
`contentMd5` of the geometry is kept, so a later task can tell which polygons
changed and fetch only those.
"""

import logging

from urllib.parse import parse_qs, urlparse

log = logging.getLogger(__name__)


# ─── The API we listen to ─────────────────────────────────────────────────────

# The GraphQL gateway. Every operation goes through the same path and is
# distinguished by the `name` query parameter.
LANDS_PATH_MARKER = '/graphql'

# [REASON]: the operation name is matched EXACTLY, on the parsed query string,
# never as a substring. The same page also fires `name=landsCluster` -- the
# map's circle counts -- whose body has the same envelope and a completely
# different shape. A substring test on 'name=lands' matches it too, and the
# walk would then mistake cluster responses for pages of the directory. This
# is the same trap the flight list had with `flight_records?` versus
# `/flight_records/`, and it is guarded by a test that asserts the cluster URL
# is REJECTED, not only that the list URL is accepted.
LANDS_OPERATION = 'lands'

# In-body status of a SUCCESSFUL response. NOT the HTTP status, and NOT 200 --
# the same envelope the flight endpoints use, verified on the live cabinet
# response of 2026-08-18: {"code": 0, "message": "OK", "data": {...}}.
BODY_CODE_OK = 0

# Where the directory lives.
DEFAULT_FIELDS_URL = 'https://www.djiag.com/mission'

NAVIGATION_WAIT_UNTIL = 'domcontentloaded'

# How long to wait for the first page of the directory after navigating.
FIRST_CAPTURE_TIMEOUT_MS = 60000

# How long to wait for the next twenty after a scroll. Shorter than the first
# wait: by then the session is warm and the site has answered at least once.
SCROLL_CAPTURE_TIMEOUT_MS = 20000

CAPTURE_POLL_MS = 250

# [REASON]: consecutive scrolls that produce no new page end the walk. Five,
# not two: an infinite list re-renders while it fetches and can legitimately
# swallow a scroll, and ending a 275-page walk early is expensive -- the whole
# point of the run is that it is complete.
MAX_IDLE_SCROLLS = 5

# Runaway guard. 5 489 rows at 20 per page is 275; a thousand is far above any
# real directory and still terminates.
DEFAULT_MAX_LAND_PAGES = 1000


class LandsError(Exception):
    """The lands walk failed."""


def is_lands_url(url):
    """True only for the directory LIST operation.

    Exact match on the parsed `name` parameter -- see LANDS_OPERATION.
    """
    if not url:
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if LANDS_PATH_MARKER not in parsed.path:
        return False
    return parse_qs(parsed.query).get('name') == [LANDS_OPERATION]


def body_code(body):
    """The in-body `code`, or None when the body is not a usable object."""
    if not isinstance(body, dict):
        return None
    value = body.get('code')
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def lands_block(body):
    """body['data']['lands'] when it is an object, else None."""
    if not isinstance(body, dict):
        return None
    data = body.get('data')
    if not isinstance(data, dict):
        return None
    block = data.get('lands')
    return block if isinstance(block, dict) else None


def classify_response(url, body):
    """(accepted, reason) for one response.

    Reasons are stable strings so the run summary can count them:
      'ok'            -- captured
      'not-lands'     -- another operation (landsCluster, userProfile, ...)
      'code-N'        -- the server rejected the request; 101 is a bad
                         signature, 408 a stale timestamp
      'code-none'     -- no usable `code` in the body
      'no-edges'      -- code 0 but no data.lands.edges list
    """
    if not is_lands_url(url):
        return False, 'not-lands'
    code = body_code(body)
    if code != BODY_CODE_OK:
        return False, 'code-%s' % ('none' if code is None else code)
    block = lands_block(body)
    if block is None or not isinstance(block.get('edges'), list):
        return False, 'no-edges'
    return True, 'ok'


class CapturedLands(object):
    """One accepted directory response."""

    __slots__ = ('url', 'body')

    def __init__(self, url, body):
        self.url = url
        self.body = body

    @property
    def block(self):
        return lands_block(self.body) or {}

    @property
    def total_count(self):
        return _as_int(self.block.get('totalCount'))

    @property
    def page_info(self):
        info = self.block.get('pageInfo')
        return info if isinstance(info, dict) else {}

    @property
    def has_next_page(self):
        """True / False / None -- None when the page did not say."""
        value = self.page_info.get('hasNextPage')
        return value if isinstance(value, bool) else None

    @property
    def end_cursor(self):
        cursor = self.page_info.get('endCursor')
        return cursor if isinstance(cursor, str) else None

    @property
    def nodes(self):
        out = []
        for edge in self.block.get('edges') or []:
            if isinstance(edge, dict) and isinstance(edge.get('node'), dict):
                out.append(edge['node'])
        return out

    def __repr__(self):
        return ('CapturedLands(nodes=%d, total=%s, next=%s, cursor=%s)'
                % (len(self.nodes), self.total_count, self.has_next_page,
                   self.end_cursor))


def _as_int(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def dedupe_lands(nodes):
    """(unique nodes in first-seen order, duplicates dropped).

    [REASON]: the directory is paginated by a numeric offset while the list is
    sorted by updatedAt. If anything in the cabinet is touched mid-walk the
    rows shift by one and the same contour is served on two consecutive pages.
    Deduplicating here rather than at the ingest keeps the collector's own
    counters honest -- and the ingest deduplicates again, because two runs can
    overlap.
    """
    seen = set()
    unique = []
    duplicates = 0
    for node in nodes:
        if not isinstance(node, dict):
            continue
        key = node.get('uuid')
        if key is None:
            # A row with no uuid cannot be deduplicated and must not be
            # dropped either: it goes through and the ingest rejects it, where
            # the rejection is counted and visible.
            unique.append(node)
            continue
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        unique.append(node)
    return unique, duplicates


# ─── Scrolling ────────────────────────────────────────────────────────────────
#
# [REASON]: the panel is scrolled by finding the scrollable element in the
# page rather than by a CSS selector. Every class name on this page is a
# build-hashed CSS-module name (the flight collector already lost its region
# indicator to exactly that), so a selector written today is wrong after the
# next deploy. Geometry and overflow are structural: the list is the tall
# scrollable box on the left. The chosen element is REPORTED, so a wrong pick
# is visible in the log instead of looking like an empty directory.

FIND_AND_SCROLL_JS = """
(fraction) => {
  const w = window.innerWidth || document.documentElement.clientWidth;
  let best = null, bestScroll = -1;
  const all = document.querySelectorAll('*');
  for (const el of all) {
    if (el.scrollHeight <= el.clientHeight + 80) continue;
    if (el.clientHeight < 200) continue;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    if (r.left > w * fraction) continue;
    if (r.width > w * 0.6) continue;
    if (el.scrollHeight > bestScroll) { best = el; bestScroll = el.scrollHeight; }
  }
  if (!best) return null;
  const before = best.scrollTop;
  best.scrollTop = best.scrollHeight;
  return {
    tag: best.tagName,
    cls: String(best.className || '').slice(0, 60),
    clientHeight: best.clientHeight,
    scrollHeight: best.scrollHeight,
    scrollTopBefore: before,
    scrollTopAfter: best.scrollTop
  };
}
"""

# How far right the list panel may start, as a fraction of the viewport width.
LIST_PANEL_MAX_LEFT_FRACTION = 0.45


class LandCollector(object):
    """Opens Field Management with a saved session and captures its pages.

        with LandCollector(cfg) as collector:
            result = collector.collect()
    """

    def __init__(self, cfg, logger=None):
        self.cfg = cfg
        self.log = logger or log
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._captured = []
        self._rejected = {}
        self._scroll_target_logged = False

    # -- lifecycle ------------------------------------------------------------

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_exc):
        self.close()

    def start(self):
        """Launch Chromium with the saved session and attach the listener.

        The listener is attached BEFORE the first navigation: the page fetches
        its first twenty contours during load, and a listener attached after
        goto() misses them.
        """
        from playwright.sync_api import sync_playwright

        from drone_collector.session import require_session

        state = require_session(self.cfg.storage_state)

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=self.cfg.headless)
        self._context = self._browser.new_context(
            storage_state=str(state),
            locale='en-US',
        )
        self._page = self._context.new_page()
        self._page.set_default_timeout(self.cfg.page_timeout_ms)
        self._page.on('response', self._on_response)
        return self

    def close(self):
        for closer in (self._context, self._browser):
            try:
                if closer is not None:
                    closer.close()
            except Exception:  # pragma: no cover -- teardown must not mask
                pass
        try:
            if self._playwright is not None:
                self._playwright.stop()
        except Exception:  # pragma: no cover
            pass
        self._context = self._browser = self._playwright = self._page = None

    # -- capture --------------------------------------------------------------

    def _on_response(self, response):
        """Response listener. Never raises: an exception here would surface
        inside Playwright's event loop and abort the navigation."""
        try:
            url = response.url
        except Exception:  # pragma: no cover
            return
        if not is_lands_url(url):
            return
        try:
            body = response.json()
        except Exception:
            self._note_rejected('unparsable-body', url)
            return
        accepted, reason = classify_response(url, body)
        if not accepted:
            self._note_rejected(reason, url)
            return
        page = CapturedLands(url, body)
        self._captured.append(page)
        self.log.info('Captured %d contour(s); cursor=%s, hasNext=%s, '
                      'totalCount=%s', len(page.nodes), page.end_cursor,
                      page.has_next_page, page.total_count)

    def _note_rejected(self, reason, url):
        self._rejected[reason] = self._rejected.get(reason, 0) + 1
        # The URL carries only the operation name -- no token, no signature,
        # no cookie. Those travel in headers and in the body.
        self.log.warning('Rejected lands response (%s): %s', reason, url)

    @property
    def captured(self):
        return list(self._captured)

    @property
    def context(self):
        """The live Playwright browser context, read-only.

        [REASON]: added for DRONE-COVERAGE-001 stage B. The full contour
        polygons are downloaded through THIS context, so the request carries
        the same session the directory walk used and the signed link never
        leaves the process. Nothing in the directory walk itself changed.
        """
        return self._context

    @property
    def rejected(self):
        return dict(self._rejected)

    def _wait_for_capture(self, previous_count, timeout_ms):
        """Block until a new page arrives. Uses the page's own wait so that
        Playwright keeps pumping its event loop."""
        import time

        deadline = time.time() + (timeout_ms / 1000.0)
        while time.time() < deadline:
            if len(self._captured) > previous_count:
                return True
            self._page.wait_for_timeout(CAPTURE_POLL_MS)
        return len(self._captured) > previous_count

    # -- the walk -------------------------------------------------------------

    def open_fields(self):
        """Navigate to Field Management and wait for the first page."""
        url = self.cfg.fields_url
        self.log.info('Opening %s', url)
        self._page.goto(url, wait_until=NAVIGATION_WAIT_UNTIL)
        if self.cfg.settle_ms:
            self._page.wait_for_timeout(self.cfg.settle_ms)
        if not self._wait_for_capture(0, FIRST_CAPTURE_TIMEOUT_MS):
            raise LandsError(
                'no directory response after %d ms at %s. Either the session '
                'has expired, or the page no longer fetches '
                'graphql?name=lands -- rejected responses so far: %s'
                % (FIRST_CAPTURE_TIMEOUT_MS, url, self.rejected or 'none'))
        return self._captured[0]

    def _scroll_list(self):
        """One scroll of the list panel. Returns True when something scrolled."""
        try:
            info = self._page.evaluate(FIND_AND_SCROLL_JS,
                                       LIST_PANEL_MAX_LEFT_FRACTION)
        except Exception as exc:  # pragma: no cover -- browser-side failure
            self.log.warning('Scroll probe failed: %s', exc)
            info = None

        if info:
            if not self._scroll_target_logged:
                self._scroll_target_logged = True
                self.log.info('Scrolling <%s class=%r> (%spx visible of %spx). '
                              'If the directory does not advance, this is the '
                              'wrong element.', info.get('tag'),
                              info.get('cls'), info.get('clientHeight'),
                              info.get('scrollHeight'))
            return True

        # [REASON]: fallback for a layout where the list is not its own
        # scroll box -- the wheel is delivered to whatever is under the
        # pointer, which needs no element to be identified at all.
        try:
            box = self._page.viewport_size or {'width': 1280, 'height': 800}
            self._page.mouse.move(box['width'] * 0.25, box['height'] * 0.6)
            self._page.mouse.wheel(0, 2000)
            if not self._scroll_target_logged:
                self._scroll_target_logged = True
                self.log.warning('No scrollable list panel found; falling back '
                                 'to a mouse wheel over the left of the page.')
            return True
        except Exception as exc:  # pragma: no cover
            self.log.warning('Wheel fallback failed: %s', exc)
            return False

    def collect(self):
        """Walk the whole directory. Returns a LandResult."""
        first = self.open_fields()
        total_count = first.total_count
        self.log.info('Directory reports %s contour(s) in total',
                      total_count if total_count is not None else 'an unknown '
                      'number of')

        idle = 0
        while len(self._captured) < self.cfg.max_land_pages:
            latest = self._captured[-1]
            if latest.has_next_page is False:
                self.log.info('The directory says there is no next page.')
                break

            before = len(self._captured)
            if not self._scroll_list():
                self.log.error('Could not scroll the list at all; stopping.')
                break
            if self._wait_for_capture(before, SCROLL_CAPTURE_TIMEOUT_MS):
                idle = 0
                continue
            idle += 1
            self.log.info('Scroll %d produced no new page (%d/%d idle)',
                          before, idle, MAX_IDLE_SCROLLS)
            if idle >= MAX_IDLE_SCROLLS:
                self.log.warning('%d consecutive scrolls produced nothing new; '
                                 'ending the walk.', idle)
                break
        else:
            self.log.warning('Reached the page cap of %d; ending the walk.',
                             self.cfg.max_land_pages)

        captured_nodes = []
        for page in self._captured:
            captured_nodes.extend(page.nodes)
        lands, duplicates = dedupe_lands(captured_nodes)

        # [REASON]: the directory states its own size in every response, so
        # completeness is CHECKED rather than assumed. A short walk that
        # reported success is exactly how a half-collected snapshot would be
        # mistaken for the whole directory -- and unlike flights, a missing
        # contour is invisible afterwards: it simply never matches anything.
        complete = (total_count is not None and len(lands) >= total_count)

        return LandResult(
            lands=lands,
            pages_captured=len(self._captured),
            total_count=total_count,
            nodes_captured=len(captured_nodes),
            self_duplicates=duplicates,
            rejected=self.rejected,
            complete=complete,
        )


class LandResult(object):
    """One directory walk."""

    __slots__ = ('lands', 'pages_captured', 'total_count', 'nodes_captured',
                 'self_duplicates', 'rejected', 'complete')

    def __init__(self, lands, pages_captured, total_count, nodes_captured,
                 self_duplicates, rejected, complete):
        self.lands = lands
        self.pages_captured = pages_captured
        self.total_count = total_count
        self.nodes_captured = nodes_captured
        self.self_duplicates = self_duplicates
        self.rejected = rejected
        self.complete = complete

    def __repr__(self):
        return ('LandResult(lands=%d/%s, pages=%d, duplicates=%d, '
                'complete=%s)' % (len(self.lands), self.total_count,
                                  self.pages_captured, self.self_duplicates,
                                  self.complete))
