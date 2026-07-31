# -*- coding: utf-8 -*-
"""drone_collector/session.py -- the saved browser session.

The collector never types credentials. A human signs in once, by hand, in a
real browser window; the resulting cookies and local storage are written to
storage_state.json and every later run reuses them.

[REASON]: this is a deliberate narrowing of the previous collector, which read
DJI_LOGIN and DJI_PASSWORD from the environment and drove the SSO form itself.
That code had no path for an e-mail or SMS confirmation code and none for a
captcha, so it broke the moment DJI asked for either -- and it kept a live
account password in a .env file on the server for the privilege. Signing in by
hand costs a few minutes per session and removes both problems. Neither this
module nor any other in the package accepts credentials as an argument or
reads them from the environment.

The saved file is a live session. It is ignored by drone_collector/.gitignore
and must never be committed, copied into a ticket, or logged.
"""

import logging

from pathlib import Path

from urllib.parse import urlsplit, urlunsplit

log = logging.getLogger(__name__)

# Path on the SmartFarm host that shows the sign-in page. Derived from
# DJI_RECORDS_URL so that a changed host only has to be changed in one place.
LOGIN_PATH = '/login'

# Playwright's own wording for the two states we care about after navigation.
NAVIGATION_WAIT_UNTIL = 'domcontentloaded'


class SessionMissing(Exception):
    """No usable storage_state.json. main() turns this into exit code 2."""


def session_exists(path):
    """True when the session file is present and non-empty.

    An empty file is treated as missing: a crashed or interrupted save leaves
    a zero-byte file behind, and Playwright fails on it with a JSON error that
    says nothing about what to do next.
    """
    target = Path(path)
    try:
        return target.is_file() and target.stat().st_size > 0
    except OSError:
        return False


def require_session(path):
    """Return the session path or raise SessionMissing naming the file."""
    if not session_exists(path):
        raise SessionMissing(
            'no saved DJI session at %s -- run '
            '`python -m drone_collector.main --save-session` and sign in once '
            'by hand' % Path(path))
    return Path(path)


def login_url(records_url):
    """The sign-in URL on the same host as the records page."""
    parts = urlsplit(records_url)
    return urlunsplit((parts.scheme, parts.netloc, LOGIN_PATH, '', ''))


def save_session_interactive(cfg, input_fn=None, print_fn=None):
    """Open a real browser window, wait for a manual sign-in, save the session.

    The operator signals completion by pressing Enter in the console. The
    browser is always headful here regardless of DJI_HEADLESS: a human has to
    see the page to sign in.

    input_fn/print_fn are injectable so the flow can be exercised without a
    console; the default is the real input()/print().
    """
    from playwright.sync_api import sync_playwright  # lazy: see module docstring

    ask = input_fn or input
    say = print_fn or print

    target = Path(cfg.storage_state)
    target.parent.mkdir(parents=True, exist_ok=True)
    url = login_url(cfg.records_url)

    say('')
    say('A browser window is opening on %s' % url)
    say('')
    say('  1. Sign in with the DJI account by hand.')
    say('  2. Wait until the records page loads and shows flights.')
    say('  3. Check the region selector: an accidental click on "Other')
    say('     Regions" switches the account to an empty country and the')
    say('     collector then quietly returns zero flights.')
    say('  4. Come back here and press Enter.')
    say('')
    say('Nothing you type in the browser is read, stored or logged by this')
    say('program. Only the resulting session cookies are saved, to %s'
        % target)
    say('')

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context(locale='en-US')
        page = context.new_page()
        page.set_default_timeout(cfg.page_timeout_ms)
        page.goto(url, wait_until=NAVIGATION_WAIT_UNTIL,
                  timeout=cfg.page_timeout_ms)

        ask('Press Enter once you are signed in and the records page is open: ')

        # Saved from the context, not the page: cookies set on the SSO host
        # during the redirect dance belong to the context too.
        context.storage_state(path=str(target))
        browser.close()

    if not session_exists(target):
        raise SessionMissing('the session file %s was not written -- was the '
                             'browser closed before pressing Enter?' % target)

    log.info('Session saved to %s (%d bytes)', target, target.stat().st_size)
    say('')
    say('Session saved to %s' % target)
    say('Keep it out of the repository and out of tickets: it is a live login.')
    return target
