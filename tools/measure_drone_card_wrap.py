#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/measure_drone_card_wrap.py -- DRONE-UI-CARD-WRAP-001 acceptance A-3.

Renders the REAL /drones/works/reports page in a headless Chromium at six
viewport widths and measures, for every summary card, whether the number
inside it stayed on one line and stayed inside its card.

[REASON]: this is the only check in the task that can tell the fix from the
defect. A test that reads CSS text proves the rule was typed, not that the
number stopped breaking; a screenshot proves nothing at all. The break this
task exists to remove happens INSIDE a group of three digits -- «2 657 997 44»
on one line and «9» on the next -- and the only artifact that distinguishes it
is the count of client rects the text node produces. Two rects is two lines.

The page is served by the real application against a disposable SQLite file
(tests.harness), so the stylesheet, the sidebar width, the content padding and
the vs_num separator are all the real ones. Nothing here touches
instance/transport.db.

Two measurements, and the second is what makes the first mean anything:

  --mode after    (default) expects one rect per card at every width.
  --mode before   the negative control. Run this against a checkout that does
                  NOT carry the fix; it must report FAIL at 1440, the width of
                  the production screenshot. If it passes there, the
                  measurement is measuring nothing.

[MEASURED, and it contradicts what DRONE-UI-FIX-002 predicted] the task says
the control must fail at 1440 AND at 1280. It does not fail at 1280, and the
reason is in this stylesheet: `@media (max-width: 1280px)` matches AT exactly
1280 and drops .vs-stat-grid from seven columns to four, which widens the card
from 121px of text box to 205px and the number fits on one line again. The
defect zone measured on the pre-fix tree is:

    <=1279   PASS   four- or three-column grid, cards wide enough
    1281     FAIL   seven columns resume, 99px of text box
    1300     FAIL
    1440     FAIL   <- the production screenshot
    1500     FAIL
    1700     PASS   seven columns, but the viewport carries them
    1920     PASS
    380      FAIL   three-column grid at 77px of text box; the task did not
                    predict this one either, and the fix covers it

So the boundary is not 1280 but 1281, and the upper edge of the zone is near
1700. 1280 is the one width in the task's list where the defect cannot be
reproduced, because it is exactly the width at which the guard already fires.

playwright is NOT a dependency of this application and is deliberately absent
from requirements.txt: it is a development measuring instrument, not something
the Windows service installs. Install it where you run this:

  pip install playwright
  python tools/measure_drone_card_wrap.py --mode after

Console output is ASCII only.
"""
import argparse
import json
import os
import socket
import sys
import threading

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

WIDTHS = (1920, 1600, 1440, 1280, 1100, 380)

# The number from the production screenshot in the task, to the kopeck. It is
# the widest figure the page has ever had to render, so it is the one measured.
FIXTURE_AMOUNT = 2657997448.68
FIXTURE_RECEIVED = 1904558439.0
FIXTURE_OTHER_COSTS = 1234567890.0
FIXTURE_AREA = 15893.64

# Chromium ships with the image, and the playwright package version here does
# not necessarily match its build number, so the binary is named outright
# rather than resolved through playwright's own download registry.
CHROMIUM_CANDIDATES = (
    os.environ.get('VS_CHROMIUM_PATH') or '',
    '/opt/pw-browsers/chromium-1194/chrome-linux/chrome',
    '/opt/pw-browsers/chromium/chrome-linux/chrome',
)


def _chromium_path():
    for path in CHROMIUM_CANDIDATES:
        if path and os.path.exists(path):
            return path
    return None


def _free_port():
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _build_fixture():
    """One admin and four jobs whose totals are the production figures."""
    from tests.harness import app, reset_db, create_admin
    from models import db, DroneCustomer, DroneOperator, DroneWork

    reset_db()
    create_admin()
    with app.app_context():
        customer = DroneCustomer(name='Fixture Farm')
        operator = DroneOperator(full_name='Fixture Operator')
        db.session.add_all([customer, operator])
        db.session.commit()
        db.session.add(DroneWork(
            period_month='2026-04',
            customer_raw='Fixture Farm',
            operator_raw='Fixture Operator',
            drone_customer_id=customer.id,
            drone_operator_id=operator.id,
            area_ha=FIXTURE_AREA,
            amount=FIXTURE_AMOUNT,
            received_amount=FIXTURE_RECEIVED,
            other_costs=FIXTURE_OTHER_COSTS,
            payment_type='transfer'))
        db.session.commit()
    return app


def _serve(app, port):
    from werkzeug.serving import make_server
    server = make_server('127.0.0.1', port, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


# Measured in the page, because only the layout engine knows where the line
# boxes fell. One rect per text node means one line; the card's content box is
# recomputed from its own padding rather than assumed.
MEASURE_JS = """
() => {
  const grid = document.querySelector('.vs-stat-grid');
  if (!grid) return {error: 'no .vs-stat-grid on the page'};
  const cards = Array.from(grid.querySelectorAll('.vs-stat'));
  return {
    gridClass: grid.className,
    cards: cards.map(card => {
      const value = card.querySelector('.vs-stat-value');
      const label = card.querySelector('.vs-stat-label');
      const cs = getComputedStyle(card);
      const box = card.getBoundingClientRect();
      const left = box.left + parseFloat(cs.paddingLeft) + parseFloat(cs.borderLeftWidth);
      const right = box.right - parseFloat(cs.paddingRight) - parseFloat(cs.borderRightWidth);
      const range = document.createRange();
      range.selectNodeContents(value);
      const rects = Array.from(range.getClientRects());
      return {
        label: (label ? label.textContent : '').trim(),
        text: value.textContent.trim(),
        valueClass: value.className,
        rects: rects.length,
        overflowRight: Math.max(0, ...rects.map(r => r.right - right)),
        overflowLeft: Math.max(0, ...rects.map(r => left - r.left)),
        contentWidth: right - left,
        textWidth: Math.max(0, ...rects.map(r => r.width))
      };
    })
  };
}
"""


def measure(port, widths, chromium):
    from playwright.sync_api import sync_playwright

    results = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=chromium,
                                     args=['--no-sandbox'])
        context = browser.new_context(viewport={'width': widths[0],
                                                'height': 1000})
        page = context.new_page()
        base = 'http://127.0.0.1:%d' % port
        page.goto(base + '/login')
        # The login screen carries a language switcher whose buttons are also
        # type=submit and come FIRST in the document, so the form is named.
        page.fill('form.vs-login-form input[name="username"]', 'admin')
        page.fill('form.vs-login-form input[name="password"]', 'test-password')
        page.click('form.vs-login-form button[type="submit"]')
        page.wait_for_load_state('networkidle')
        if '/login' in page.url:
            raise RuntimeError('login failed, still on %s' % page.url)
        for width in widths:
            page.set_viewport_size({'width': width, 'height': 1000})
            page.goto(base + '/drones/works/reports')
            page.wait_for_load_state('networkidle')
            results[width] = page.evaluate(MEASURE_JS)
        browser.close()
    return results


def report(results, mode):
    """Print the measurement and return the number of failing (width, card)."""
    failures = 0
    failed_widths = []
    for width in sorted(results, reverse=True):
        data = results[width]
        if data.get('error'):
            print('  %-5d ERROR: %s' % (width, data['error']))
            failures += 1
            failed_widths.append(width)
            continue
        bad = []
        for card in data['cards']:
            if card['rects'] != 1:
                bad.append('%s wrapped into %d lines'
                           % (_ascii(card['label']), card['rects']))
            elif card['overflowRight'] > 1.0 or card['overflowLeft'] > 1.0:
                bad.append('%s overflows its card by %.1fpx'
                           % (_ascii(card['label']),
                              max(card['overflowRight'], card['overflowLeft'])))
        widest = max(c['textWidth'] for c in data['cards'])
        narrowest = min(c['contentWidth'] for c in data['cards'])
        status = 'PASS' if not bad else 'FAIL'
        print('  %-5d %s  grid="%s"  content>=%.0fpx  widest text=%.0fpx'
              % (width, status, data['gridClass'], narrowest, widest))
        for line in bad:
            print('          %s' % line)
        if bad:
            failures += 1
            failed_widths.append(width)
    print('')
    if mode == 'after':
        print('RESULT: %s (%d width(s) failing)'
              % ('PASS' if not failures else 'FAIL', failures))
    else:
        # [REASON]: the control is valid when it fails at 1440 -- the width of
        # the production screenshot. It is deliberately NOT asserted at 1280:
        # measurement showed the defect cannot be reproduced there, because
        # @media (max-width: 1280px) already widens the cards at exactly that
        # width. Asserting a width where the defect does not exist would make
        # this control fail for a reason that has nothing to do with the fix.
        ok = 1440 in failed_widths
        print('RESULT: negative control %s -- failing widths: %s'
              % ('VALID' if ok else 'INVALID',
                 ', '.join(str(w) for w in sorted(failed_widths, reverse=True))
                 or 'none'))
        if not ok:
            print('        expected 1440 to fail before the fix; a control '
                  'that passes there measures nothing.')
        return 0 if ok else 1
    return 0 if not failures else 1


def _ascii(text):
    return text.encode('ascii', 'backslashreplace').decode('ascii')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('after', 'before'), default='after')
    parser.add_argument('--json', help='write the raw measurement here')
    parser.add_argument('--widths', help='comma-separated override')
    args = parser.parse_args()

    chromium = _chromium_path()
    if chromium is None:
        print('ERROR: no chromium binary found. Set VS_CHROMIUM_PATH.')
        return 2
    try:
        import playwright  # noqa: F401
    except ImportError:
        print('ERROR: playwright is not installed (pip install playwright).')
        return 2

    widths = WIDTHS
    if args.widths:
        widths = tuple(int(w) for w in args.widths.split(','))

    app = _build_fixture()
    port = _free_port()
    server = _serve(app, port)
    try:
        print('DRONE-UI-CARD-WRAP-001 / A-3  mode=%s  amount=%.2f'
              % (args.mode, FIXTURE_AMOUNT))
        results = measure(port, widths, chromium)
    finally:
        server.shutdown()

    code = report(results, args.mode)
    if args.json:
        with open(args.json, 'w', encoding='utf-8') as handle:
            json.dump(results, handle, ensure_ascii=False, indent=2)
    return code


if __name__ == '__main__':
    sys.exit(main())
