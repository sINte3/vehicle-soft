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

WHY THE CHECK IS STRUCTURAL AND NOT "THE FILE IS NOT EMPTY"
----------------------------------------------------------

The first live pilot of stage B (2026-08-27) ran `--save-session`, was told the
session had been saved, got exit code 0 -- and the file was thirty bytes:

    {"cookies": [], "origins": []}

That is what Playwright writes when the context holds nothing: a syntactically
perfect storage state with no session in it. The old check was `is_file() and
st_size > 0`, so thirty bytes of emptiness passed as a working login, and the
failure surfaced much later as a puzzling redirect to /login.

So the file is now judged by what it CARRIES: at least one cookie, or at least
one localStorage item. A real session of this cabinet, measured on that same
machine, was ~83 KB with 14 cookies, 2 origins and 13 localStorage items.

NOTHING IN THIS MODULE PRINTS A VALUE
-------------------------------------

Counts, sizes and names of the failure -- never a cookie value, never a
localStorage value, never an origin. The whole file is one long credential.
"""

import json
import logging
import os
import tempfile

from pathlib import Path

from urllib.parse import urlsplit, urlunsplit

log = logging.getLogger(__name__)

# Path on the SmartFarm host that shows the sign-in page. Derived from
# DJI_RECORDS_URL so that a changed host only has to be changed in one place.
LOGIN_PATH = '/login'

# Playwright's own wording for the two states we care about after navigation.
NAVIGATION_WAIT_UNTIL = 'domcontentloaded'

# Ceiling on the storage state we are willing to read.
#
# [REASON]: the real session of this cabinet measured 82 919 bytes. Eight
# megabytes is two orders of magnitude above that and still small enough that
# a wrong --DJI_STORAGE_STATE pointing at, say, a database file is refused
# instead of being parsed as JSON for a minute.
MAX_SESSION_BYTES = 8 * 1024 * 1024

# Suffix of the half-written state. Not `.json`, so nothing mistakes it for a
# session, and it lives beside the target so os.replace stays atomic.
TEMP_SUFFIX = '.partial'


class SessionMissing(Exception):
    """No usable storage_state.json. main() turns this into exit code 2."""


class SessionState(object):
    """What a storage-state file carries. Values are never held here."""

    __slots__ = ('usable', 'reason', 'bytes', 'cookies', 'origins',
                 'local_storage_items')

    def __init__(self, usable, reason='', size=0, cookies=0, origins=0,
                 local_storage_items=0):
        self.usable = usable
        self.reason = reason
        self.bytes = size
        self.cookies = cookies
        self.origins = origins
        self.local_storage_items = local_storage_items

    def describe(self):
        """Safe one-liner for a log. Counts only."""
        return ('bytes=%d cookies=%d origins=%d local_storage_items=%d'
                % (self.bytes, self.cookies, self.origins,
                   self.local_storage_items))

    def __repr__(self):
        return '<SessionState usable=%s %s>' % (self.usable, self.describe())


def _count_local_storage(origins):
    """How many localStorage entries the origins carry, in total."""
    total = 0
    for origin in origins:
        if not isinstance(origin, dict):
            continue
        items = origin.get('localStorage')
        if isinstance(items, list):
            total += len(items)
    return total


def inspect_session(path):
    """Structural verdict on a Playwright storage state. Never raises.

    Returns a SessionState. `reason` names the defect and never quotes the
    content: a message that echoed a cookie to explain why it was malformed
    would put the credential in the log it was trying to protect.
    """
    target = Path(path)
    try:
        if not target.is_file():
            return SessionState(False, 'no such file')
        size = target.stat().st_size
    except OSError as exc:
        return SessionState(False, 'could not be read (%s)'
                            % type(exc).__name__)

    if size == 0:
        return SessionState(False, 'the file is empty', size=size)
    if size > MAX_SESSION_BYTES:
        return SessionState(False,
                            'the file is %d bytes, the cap is %d'
                            % (size, MAX_SESSION_BYTES), size=size)

    try:
        with open(str(target), encoding='utf-8') as handle:
            document = json.load(handle)
    except (ValueError, UnicodeDecodeError) as exc:
        return SessionState(False, 'not readable JSON (%s)'
                            % type(exc).__name__, size=size)
    except OSError as exc:
        return SessionState(False, 'could not be read (%s)'
                            % type(exc).__name__, size=size)
    except RecursionError:
        # [REASON]: deeply nested JSON raises RecursionError, which is a
        # RuntimeError and not a ValueError. Without this the check would
        # crash instead of refusing.
        return SessionState(False, 'nests deeper than this reader walks',
                            size=size)

    if not isinstance(document, dict):
        return SessionState(False, 'decodes to %s, not to an object'
                            % type(document).__name__, size=size)

    cookies = document.get('cookies')
    if cookies is None:
        cookies = []
    if not isinstance(cookies, list):
        return SessionState(False, '"cookies" is %s, not a list'
                            % type(cookies).__name__, size=size)

    origins = document.get('origins')
    if origins is None:
        origins = []
    if not isinstance(origins, list):
        return SessionState(False, '"origins" is %s, not a list'
                            % type(origins).__name__, size=size)

    items = _count_local_storage(origins)
    state = SessionState(False, '', size=size, cookies=len(cookies),
                         origins=len(origins), local_storage_items=items)

    # [REASON]: a storage state with neither a cookie nor a localStorage item
    # is the exact shape Playwright writes for a context that was never signed
    # in. It is well-formed and useless, and treating it as a session is what
    # cost the first pilot its run.
    if not cookies and not items:
        state.reason = ('the state carries no cookie and no localStorage item '
                        '-- the browser was never signed in')
        return state

    state.usable = True
    return state


def session_exists(path):
    """True when the file carries an actual session.

    Kept under its old name because every caller in the package asks this
    question; what changed is the answer's basis -- content, not size.
    """
    return inspect_session(path).usable


def require_session(path):
    """Return the session path or raise SessionMissing naming the defect."""
    state = inspect_session(path)
    if state.usable:
        return Path(path)
    raise SessionMissing(
        'no usable DJI session at %s: %s (%s) -- run '
        '`python -m drone_collector.main --save-session` and sign in once by '
        'hand' % (Path(path), state.reason, state.describe()))


def login_url(records_url):
    """The sign-in URL on the same host as the records page."""
    parts = urlsplit(records_url)
    return urlunsplit((parts.scheme, parts.netloc, LOGIN_PATH, '', ''))


def expected_host(records_url):
    """Host the browser must be on when the operator presses Enter."""
    return urlsplit(records_url).netloc


def landed_where_expected(current_url, records_url):
    """(ok, reason). The browser must be on the cabinet, not on /login.

    Neither check replaces the other, and BOTH must hold before a save.
    `inspect_session` catches the empty context on the right page;
    this catches the populated context on the WRONG page -- `/login` sets its
    own cookies and localStorage, so a sign-in form left open would sail
    through the structural check and overwrite a working session.
    """
    if not current_url:
        return False, 'the browser reported no URL'
    parts = urlsplit(current_url)
    wanted = urlsplit(records_url)

    # [REASON]: the scheme is checked, and checked against the configured one.
    # `http://` on the right host is a different security posture entirely --
    # cookies of a session saved off a plaintext page are cookies that
    # travelled in the clear -- and the old check looked only at the host.
    if parts.scheme != wanted.scheme:
        return False, ('the browser is on %s://, the records page is %s://'
                       % (parts.scheme or '(no scheme)',
                          wanted.scheme or '(no scheme)'))
    if parts.scheme != 'https':
        return False, 'the page is not served over https'
    if parts.netloc != wanted.netloc:
        # The host is printed; it is a public address, not a credential.
        return False, ('the browser is on %s, not on %s'
                       % (parts.netloc or '(no host)',
                          wanted.netloc or '(no host)'))

    # [REASON]: the PATH is checked too, not just "it is not /login". The
    # cabinet has other pages -- `/mission`, the root -- and each of them sets
    # its own cookies, so "populated context on some page of the right host"
    # was never enough. A trailing slash and a query string are allowed: the
    # records page carries filters in the query by design.
    landed = (parts.path or '/').rstrip('/') or '/'
    expected = (wanted.path or '/').rstrip('/') or '/'
    if landed != expected:
        return False, ('the browser is on %s, not on the records page %s'
                       % (landed, expected))
    return True, ''


def _remove_quietly(path):
    try:
        os.remove(str(path))
        return True
    except OSError:
        return False


def save_state_atomically(context, target, writer=None):
    """Write the context's state beside `target`, check it, then replace.

    Returns the SessionState of what was installed.

    [REASON]: Playwright used to be pointed straight at `storage_state.json`.
    That makes every save destructive: an empty context overwrites a working
    session with thirty bytes, and the operator finds out on the next run. Now
    the new state is written to a `.partial` beside the target, judged there,
    and only a usable state replaces the file. A useless one is deleted and
    the previous session survives untouched.
    """
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)

    handle = tempfile.NamedTemporaryFile(
        prefix=target.name + '.', suffix=TEMP_SUFFIX, dir=str(target.parent),
        delete=False)
    temp_name = handle.name
    handle.close()

    write = writer or (lambda path: context.storage_state(path=path))
    try:
        write(temp_name)
    except BaseException:
        _remove_quietly(temp_name)
        raise

    state = inspect_session(temp_name)
    if not state.usable:
        _remove_quietly(temp_name)
        previous = inspect_session(target)
        note = ('the previous session at %s was left untouched (%s)'
                % (target, previous.describe()) if previous.usable
                else 'there is no previous usable session to fall back on')
        raise SessionMissing(
            'the browser produced no usable session: %s (%s). Nothing was '
            'overwritten; %s' % (state.reason, state.describe(), note))

    try:
        os.replace(temp_name, str(target))
    except OSError:
        # [REASON]: без этого `.partial` оставался лежать рядом с сессией
        # после каждого отказа замены -- на Windows её умеет отклонить и
        # антивирус, и открытый чужим процессом файл. Каталог зарастал
        # полусохранёнными состояниями, которые никто не читает и никто не
        # убирает, а прежняя сессия при этом цела и работает.
        _remove_quietly(temp_name)
        raise
    return state


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

        landed = None
        try:
            landed = page.url
        except Exception:  # pragma: no cover -- a closed page has no url
            landed = None
        ok, why = landed_where_expected(landed, cfg.records_url)
        if not ok:
            # [REASON]: this REFUSES, it no longer warns. A warning let the
            # save go ahead, and /login is not an empty page: it sets its own
            # cookies and localStorage, so the structural check would pass it
            # and a sign-in form would overwrite a working session. Two
            # guards, and both must hold before anything is written.
            browser.close()
            raise SessionMissing(
                'the browser is not where a signed-in session would be: %s. '
                'Nothing was saved and nothing was overwritten -- sign in, '
                'wait for the records page, and run --save-session again.'
                % why)

        # Saved from the context, not the page: cookies set on the SSO host
        # during the redirect dance belong to the context too.
        try:
            state = save_state_atomically(context, target)
        finally:
            browser.close()

    log.info('Session saved to %s (%s)', target, state.describe())
    say('')
    say('Session saved to %s' % target)
    say('  %s' % state.describe())
    say('Keep it out of the repository and out of tickets: it is a live login.')
    return target
