# -*- coding: utf-8 -*-
"""DRONE-UI-FIX-003 -- the alert that shredded its own sentence.

  C1  .vs-alert is `display: flex`, so every child of an alert -- every
      element AND every text node between elements -- is a flex item. An
      alert whose content is one sentence carrying nine <b> numbers laid out
      as roughly twenty items in a row with 8px gaps, wrapped into columns,
      and was unreadable on screen. It read correctly in the DOM, which is
      why DOM-only QA never saw it.

What the assertions here can and cannot prove, stated plainly because the
distinction is the whole point of the acceptance:

  * C1.1 is STRUCTURAL and runs over the TEMPLATE SOURCE. It proves every
    .vs-alert in templates/drones/ resolves to at most two flex items. It
    does NOT prove the sentence stopped wrapping into columns -- no assertion
    over markup can, because flex layout is decided by the engine against a
    font and a viewport. What it does prove is the precondition that makes
    the defect impossible: an alert with ONE child has nothing to shred.
  * C1.2 is END TO END. It renders the real spray page through the real
    application in RU and in UZ and asserts the median sentence sits inside a
    single wrapper element, so a fix that lived only in the template source
    but did not survive rendering would be caught.
  * The negative control for both is a REAL MUTATION of the shipped template
    (C1.3), recorded in the PR body rather than here: a control that
    recomputes the broken rule inside itself is a simulation and proves
    nothing. Removing the wrapper from spray_usage.html makes C1.1 fail with
    the real item count.

[REASON]: TWO items is the limit, not one. Two is the deliberate
«label + paragraph» shape -- `<b>Обозначения:</b> текст…` -- used by the
calendar legend and the assignment-hints screen. Those two render correctly
and must not be «fixed»: a rule of «exactly one child» would force a wrapper
into markup that never had the defect, and the next reader would delete it as
pointless. The rule is «no alert may exceed two», and the two screens that sit
at the limit are pinned by name below so that widening the limit is a visible
edit rather than a drift.

Run:
  python -m unittest tests.test_drone_ui_fix_003 -v
  python tests/test_drone_ui_fix_003.py --report   # the per-file count table
"""
import io
import os
import re
import sys
import unittest
from datetime import datetime
from html.parser import HTMLParser

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# [REASON]: lets `python tests/test_drone_ui_fix_003.py --report` run from the
# project root, which is how the C1.1 count table is produced for the PR body.
# `python -m unittest` already puts the root on the path and is unaffected.
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tests.harness import app, reset_db, create_admin, login  # noqa: E402
from models import db, DroneFlight, DroneUnit, Organization, User  # noqa: E402

DRONE_TEMPLATES = os.path.join(REPO_ROOT, 'templates', 'drones')

# [REASON]: the CSS rule this whole increment is about. It is asserted, not
# assumed: if a later release stops laying alerts out as flex, the wrappers
# become dead weight and the reader deserves to be told by a failing test
# rather than to guess. .vs-alert itself is shared by the whole application
# and is NOT modified by this increment -- see C1.4 in the PR body.
CSS_PATH = os.path.join(REPO_ROOT, 'static', 'css', 'design-system.css')
FLEX_ANCHOR = '.vs-alert {\n  display: flex;'

MAX_FLEX_ITEMS = 2

# The two alerts that legitimately sit AT the limit, by file and by shape.
# Listed so that a third one appearing is a visible edit to this tuple.
DELIBERATE_TWO_ITEM_ALERTS = (
    'flight_calendar.html',
    'works_assignment_hints.html',
)

VOID_ELEMENTS = frozenset((
    'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
    'link', 'meta', 'param', 'source', 'track', 'wbr'))

JINJA_COMMENT = re.compile(r'\{#.*?#\}', re.S)
JINJA_STATEMENT = re.compile(r'\{%.*?%\}', re.S)
JINJA_EXPRESSION = re.compile(r'\{\{.*?\}\}', re.S)

ALERT_OPEN = re.compile(r'<div\b[^>]*\bclass\s*=\s*"([^"]*)"[^>]*>')
DIV_OPEN = re.compile(r'<div\b')
DIV_CLOSE = re.compile(r'</div\s*>')


def _blank_out(match):
    """Replace a Jinja tag with spaces, keeping newlines.

    [REASON]: the substitution preserves line AND column positions, so a
    failure reports the line number of the real template rather than of a
    shortened copy of it. A plain '' substitution shifted every reported line
    by the number of {% %} tags above it, which sent the first reader of a
    failure to the wrong part of the file.
    """
    return re.sub(r'[^\n]', ' ', match.group(0))


def _expression_to_text(match):
    """Replace {{ ... }} with a text token of its own, padded to length."""
    text = match.group(0)
    return 'EXPR' + re.sub(r'[^\n]', ' ', text[4:] if len(text) > 4 else '')


def flatten_template(source):
    """Template source -> HTML-ish text with Jinja removed.

    [REASON]: {% if %}/{% else %} branches are BOTH left in place, so an
    alert whose two branches each hold one sentence counts as two items
    rather than one. That over-counts by design: the check is an upper bound
    on what any branch can produce, and an upper bound that passes is a
    guarantee for every branch. Rendering each branch separately would need a
    fixture per screen and would still miss the branch nobody thought of.
    """
    source = JINJA_COMMENT.sub(_blank_out, source)
    source = JINJA_STATEMENT.sub(_blank_out, source)
    source = JINJA_EXPRESSION.sub(_expression_to_text, source)
    return source


def iter_alerts(html):
    """Yield (line, class_attr, inner_html) for every .vs-alert in `html`.

    The matching </div> is found by walking div depth, which is sound here
    because tools/check_templates.py already blocks any template whose divs
    do not balance.
    """
    for match in ALERT_OPEN.finditer(html):
        if 'vs-alert' not in match.group(1).split():
            continue
        depth = 1
        cursor = match.end()
        while depth and cursor < len(html):
            opener = DIV_OPEN.search(html, cursor)
            closer = DIV_CLOSE.search(html, cursor)
            if closer is None:
                break
            if opener is not None and opener.start() < closer.start():
                depth += 1
                cursor = opener.end()
                continue
            depth -= 1
            cursor = closer.end()
            if not depth:
                yield (html.count('\n', 0, match.start()) + 1,
                       match.group(1),
                       html[match.end():closer.start()])
                break


class _ChildCounter(HTMLParser):
    """Direct children of one element: elements plus non-empty text nodes."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.items = []

    def handle_starttag(self, tag, attrs):
        if not self.depth:
            self.items.append('<%s>' % tag)
        if tag not in VOID_ELEMENTS:
            self.depth += 1

    def handle_startendtag(self, tag, attrs):
        if not self.depth:
            self.items.append('<%s/>' % tag)

    def handle_endtag(self, tag):
        if tag not in VOID_ELEMENTS and self.depth:
            self.depth -= 1

    def handle_data(self, data):
        if not self.depth and data.strip():
            self.items.append('text(%d)' % len(data.strip()))


def flex_items(inner_html):
    """The flex items one .vs-alert produces from its inner HTML."""
    parser = _ChildCounter()
    parser.feed(inner_html)
    parser.close()
    return parser.items


def audit_drone_templates():
    """[(template, line, [items])] for every .vs-alert under templates/drones."""
    rows = []
    for name in sorted(os.listdir(DRONE_TEMPLATES)):
        if not name.endswith('.html'):
            continue
        with io.open(os.path.join(DRONE_TEMPLATES, name),
                     encoding='utf-8') as handle:
            source = handle.read()
        for line, _classes, inner in iter_alerts(flatten_template(source)):
            rows.append((name, line, flex_items(inner)))
    return rows


# ══ C1.1  every alert in templates/drones/ ═══════════════════════════════════

class TestC11AlertsNeverExceedTwoFlexItems(unittest.TestCase):
    """No .vs-alert in the drones module may lay out as more than two items."""

    def test_c1_1_the_css_still_lays_alerts_out_as_flex(self):
        """The premise. Without it every wrapper in this increment is dead."""
        with io.open(CSS_PATH, encoding='utf-8') as handle:
            css = handle.read()
        self.assertIn(
            FLEX_ANCHOR, css,
            '.vs-alert is no longer display:flex -- the wrappers added by '
            'DRONE-UI-FIX-003 are now pointless and this rule should be '
            'reconsidered rather than silently kept.')

    def test_c1_1_no_alert_exceeds_two_flex_items(self):
        rows = audit_drone_templates()
        self.assertTrue(rows, 'no .vs-alert found at all -- the parser broke')
        offenders = [(name, line, len(items), items)
                     for name, line, items in rows
                     if len(items) > MAX_FLEX_ITEMS]
        self.assertEqual(
            [], offenders,
            'these .vs-alert blocks lay out as more than %d flex items, so '
            'the browser spreads their sentence across a row of gapped '
            'columns: %s' % (MAX_FLEX_ITEMS, offenders))

    def test_c1_1_the_two_item_alerts_are_the_two_known_screens(self):
        """[REASON]: pins WHICH screens sit at the limit, not just how many.

        Without this, a regression that pushed a third screen from one item to
        two would pass the rule above while quietly reintroducing the shape
        the wrap exists to prevent on a screen nobody re-checked.
        """
        at_limit = sorted({name for name, _line, items in audit_drone_templates()
                           if len(items) == MAX_FLEX_ITEMS})
        self.assertEqual(sorted(DELIBERATE_TWO_ITEM_ALERTS), at_limit)

    def test_c1_1_the_three_wrapped_alerts_have_exactly_one_child(self):
        """The three alerts this increment wrapped, pinned by file and shape."""
        wrapped = {'spray_usage.html': 1, 'customers.html': 1,
                   'works_debts.html': 1}
        for name, line, items in audit_drone_templates():
            if name in wrapped and items == ['<div>']:
                wrapped[name] -= 1
        self.assertEqual({'spray_usage.html': 0, 'customers.html': 0,
                          'works_debts.html': 0}, wrapped,
                         'a wrapper <div> added by DRONE-UI-FIX-003 is gone')


# ══ C1.2  the spray page, rendered, in both languages ════════════════════════

MEDIAN_MARKER = {'ru': 'Медиана расхода', 'uz': 'Медиана сарфи'}

# [REASON]: DRONE-SPRAY-MEDIAN-SCOPE-001 added a SECOND sentence to this same
# alert, rendered only when the screen is filtered to one machine. It is the
# reason the wrapper must enclose the whole {% if %}/{% else %} block rather
# than just the first sentence: a wrapper that closed after the median text
# would leave this one outside as a second flex item, and two items still lay
# out as two gapped columns. The filtered render is asserted separately below,
# because the unfiltered page never produces this sentence at all and a test
# that only ever renders unfiltered cannot see the regression.
SCOPE_MARKER = {'ru': 'НЕ этой машины', 'uz': 'бу МАШИНАНИКИ эмас'}


def _set_language(user_id, lang):
    """[REASON]: the page language comes from users.language through g.lang,
    NOT from a session key, exactly as test_drone_analytics_001 records. A
    test that plants sess['lang'] renders Uzbek under both settings and its
    «both languages» assertion measures nothing.
    """
    with app.app_context():
        user = User.query.get(user_id)
        user.language = lang
        db.session.commit()


class TestC12MedianSentenceSitsInOneWrapper(unittest.TestCase):
    """The real page, the real Jinja, both languages."""

    fragments = {}

    @classmethod
    def setUpClass(cls):
        reset_db()
        cls.user_id = create_admin('uifix003admin')
        with app.app_context():
            org = Organization.query.first()
            if org is None:
                org = Organization(name='Fixture Holding')
                db.session.add(org)
                db.session.flush()
            unit = DroneUnit(number=1, organization_id=org.id)
            db.session.add(unit)
            db.session.flush()
            cls.unit_id = unit.id
            # Two spray flights, 100 ha and 2 000 l, so the median branch of
            # the alert is the one that renders: the empty branch carries no
            # <b> at all and would pass this test without proving anything.
            for day, area, liters in ((5, 60.0, 1200.0), (6, 40.0, 800.0)):
                db.session.add(DroneFlight(
                    dji_flight_id=970000 + day,
                    drone_unit_id=unit.id,
                    nickname_raw='fixture',
                    started_at=datetime(2026, 4, day, 6),
                    work_seconds=3600,
                    area_ha=area,
                    spray_liters=liters,
                    usage_type=0,
                    raw_json='{}'))
            db.session.commit()

    def _median_alert(self, lang, unit_id=None):
        """The rendered median alert. unit_id filters the screen to one machine,
        which is the only way DRONE-SPRAY-MEDIAN-SCOPE-001's second sentence
        renders at all.
        """
        _set_language(self.user_id, lang)
        query = '/drones/reports/spray?date_from=2026-04-01&date_to=2026-04-30'
        if unit_id is not None:
            query += '&unit_id=%d' % unit_id
        with app.test_client() as client:
            login(client, self.user_id)
            response = client.get(query)
            self.assertEqual(200, response.status_code)
            html = response.get_data(as_text=True)
        self.assertIn(MEDIAN_MARKER[lang], html,
                      'the median branch did not render in %s -- the fixture, '
                      'not the layout, is what failed' % lang)
        for _line, classes, inner in iter_alerts(html):
            if MEDIAN_MARKER[lang] in inner:
                return classes, inner
        self.fail('the median sentence rendered outside any .vs-alert (%s)'
                  % lang)

    def test_c1_2_median_sentence_is_one_wrapper_in_both_languages(self):
        for lang in ('ru', 'uz'):
            with self.subTest(lang=lang):
                classes, inner = self._median_alert(lang)
                items = flex_items(inner)
                type(self).fragments[lang] = (classes, inner)
                self.assertEqual(
                    ['<div>'], items,
                    'the median alert renders %d flex items in %s: %s'
                    % (len(items), lang, items))

    def test_c1_2_the_numbers_are_inside_the_wrapper_not_beside_it(self):
        """[REASON]: the <b> numbers are the reason the defect was visible.

        A wrapper that held only the leading text while the numbers stayed
        direct children of the alert would satisfy a naive «one child» count
        on the first item and still shred the sentence. This asserts the <b>
        elements moved INSIDE the wrapper.
        """
        for lang in ('ru', 'uz'):
            with self.subTest(lang=lang):
                _classes, inner = self._median_alert(lang)
                outer = flex_items(inner)
                self.assertEqual(['<div>'], outer)
                wrapper_inner = inner[inner.index('>') + 1:inner.rindex('</div>')]
                self.assertGreaterEqual(
                    wrapper_inner.count('<b>'), 8,
                    'the median sentence should carry its nine <b> numbers '
                    'inside the wrapper; found %d'
                    % wrapper_inner.count('<b>'))

    def test_c1_2_the_filtered_screen_keeps_both_sentences_in_one_wrapper(self):
        """DRONE-SPRAY-MEDIAN-SCOPE-001's second sentence, RU and UZ.

        [REASON]: this is the case the unfiltered render cannot see. Filtering
        to one machine adds a second sentence to the SAME alert. A wrapper that
        closed after the median text would leave it outside as a second flex
        item -- and two items still lay out as two gapped columns, which is the
        defect, not a smaller version of it.
        """
        for lang in ('ru', 'uz'):
            with self.subTest(lang=lang):
                classes, inner = self._median_alert(lang,
                                                    unit_id=self.unit_id)
                self.assertIn(
                    SCOPE_MARKER[lang], inner,
                    'the fleet-scope sentence did not render in %s -- the '
                    'fixture, not the layout, is what failed' % lang)
                items = flex_items(inner)
                type(self).fragments['%s-filtered' % lang] = (classes, inner)
                self.assertEqual(
                    ['<div>'], items,
                    'the filtered median alert renders %d flex items in %s: %s'
                    % (len(items), lang, items))
                # both sentences inside the SAME wrapper, not one in and one out
                wrapper_inner = inner[inner.index('>') + 1:
                                      inner.rindex('</div>')]
                self.assertIn(MEDIAN_MARKER[lang], wrapper_inner)
                self.assertIn(SCOPE_MARKER[lang], wrapper_inner)


def report():
    """Print the per-file flex-item table (the C1.1 artifact)."""
    worst = 0
    for name, line, items in audit_drone_templates():
        worst = max(worst, len(items))
        print('%-4s %-30s line %-5s items=%-3d %s'
              % ('OVER' if len(items) > MAX_FLEX_ITEMS else 'ok',
                 name, line, len(items), items))
    print('\nlimit = %d, worst = %d' % (MAX_FLEX_ITEMS, worst))
    return 0 if worst <= MAX_FLEX_ITEMS else 1


if __name__ == '__main__':
    if '--report' in sys.argv:
        sys.exit(report())
    unittest.main()
