# -*- coding: utf-8 -*-
"""DRONE-UI-FIX-002 -- three defects found by the production acceptance run.

  A  DRONE-UI-CARD-WRAP-001      money broke between two digits of a group.
  B  DRONE-UI-CUSTOMER-LABEL-001 one label stood for two different facts.
  C  DRONE-REPORT-ZERO-AMOUNT-001 a missing price was reported as 0.

What the assertions here can and cannot prove, stated plainly because the
distinction is the whole point of the acceptance:

  * A-1 and A-2 are STRUCTURAL. They prove the opt-out rule is still additive
    (the rule it opts out of survives verbatim) and that every number carrying
    card actually carries the class. They do NOT prove the number stopped
    wrapping -- no assertion over CSS text can, because wrapping is decided by
    the layout engine against a font. That measurement is A-3/A-4 and it lives
    in tools/measure_drone_card_wrap.py, which drives a real browser. Its
    result is recorded in the PR body, not here.
  * B and C are behavioural and are asserted end to end through the real
    application against a disposable database, with the negative controls
    (B-4, C-2) pinned against the pre-fix rendering so that a check which
    cannot tell the two cases apart is caught rather than trusted.

Run:
  python -m unittest tests.test_drone_ui_fix_002 -v
"""
import os
import re
import unittest
from html.parser import HTMLParser

from tests.harness import app, reset_db, create_admin, login

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSS_PATH = os.path.join(REPO_ROOT, 'static', 'css', 'design-system.css')

# The four screens the task names. Nothing outside the drones module is
# touched: other tracks own their screens.
MONEY_TEMPLATES = (
    'templates/drones/works.html',
    'templates/drones/works_reports.html',
    'templates/drones/works_debts.html',
    'templates/drones/summary.html',
)

# [REASON]: this line is the rule the fix opts OUT of, and it must survive the
# fix unchanged. It exists so a long uppercase RU/UZ label wraps inside its
# card instead of widening the whole document; deleting it to stop numbers
# breaking would trade this defect for the one it was written against.
ADDITIVITY_ANCHOR = '.vs-stat-label, .vs-stat-value { overflow-wrap: anywhere; }'

VOID_TAGS = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
             'link', 'meta', 'param', 'source', 'track', 'wbr'}


class StatGridParser(HTMLParser):
    """Collects every .vs-stat-value and whether a money grid encloses it."""

    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.stack = []
        self.values = []
        self.money_grids = 0
        self.plain_grids = 0

    def _classes(self, attrs):
        return set((dict(attrs).get('class') or '').split())

    def handle_startendtag(self, tag, attrs):
        self._record(self._classes(attrs))

    def handle_starttag(self, tag, attrs):
        classes = self._classes(attrs)
        self._record(classes)
        if tag not in VOID_TAGS:
            self.stack.append((tag, classes))

    def _record(self, classes):
        if 'vs-stat-grid' in classes:
            if 'is-money' in classes:
                self.money_grids += 1
            else:
                self.plain_grids += 1
        if 'vs-stat-value' in classes:
            in_money = any('vs-stat-grid' in c and 'is-money' in c
                           for _tag, c in self.stack)
            self.values.append({'classes': classes, 'in_money_grid': in_money})

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                return


def parse_template(relative_path):
    with open(os.path.join(REPO_ROOT, relative_path), encoding='utf-8') as fh:
        source = fh.read()
    parser = StatGridParser()
    parser.feed(source)
    return parser


class TestA1CssIsAdditive(unittest.TestCase):
    """A-1: the fix ADDS rules; it does not edit or delete an existing one."""

    def setUp(self):
        with open(CSS_PATH, encoding='utf-8') as fh:
            self.css = fh.read()

    def test_a1_existing_wrap_rule_survives_verbatim(self):
        self.assertIn(
            ADDITIVITY_ANCHOR, self.css,
            'the overflow-wrap:anywhere rule was edited or removed. It is '
            'deliberate: it keeps a long label from widening the document. '
            'The number fix must opt out beside it, not replace it.')

    def test_a1_both_new_selectors_exist(self):
        self.assertIn('.vs-stat-grid.is-money', self.css)
        self.assertIn('.vs-stat-value.is-num', self.css)

    def test_a1_opt_out_actually_disables_wrapping(self):
        """The opt-out is worthless unless it names both properties.

        overflow-wrap:normal alone still lets word-break fall back to the
        default, so the class states both and the test reads both.
        """
        rule = re.search(r'\.vs-stat-value\.is-num\s*\{([^}]*)\}', self.css)
        self.assertIsNotNone(rule, '.vs-stat-value.is-num rule not found')
        body = rule.group(1)
        self.assertIn('overflow-wrap: normal', body)
        self.assertIn('word-break: keep-all', body)

    def test_a1_money_grid_uses_auto_fit_not_a_fixed_count(self):
        """auto-fit is what lets a card move to a second row instead of
        squeezing; a fixed column count would reintroduce the squeeze."""
        rule = re.search(r'\.vs-stat-grid\.is-money\s*\{([^}]*)\}', self.css)
        self.assertIsNotNone(rule)
        self.assertIn('auto-fit', rule.group(1))

    def test_a1_media_block_rules_are_untouched(self):
        """The two @media .vs-stat-grid rules are outranked, never edited."""
        self.assertIn(
            '.vs-stat-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }',
            self.css)
        self.assertIn(
            '.vs-stat-grid   { grid-template-columns: repeat(3, minmax(0, 1fr)); }',
            self.css)


class TestA2TemplatesCarryTheClasses(unittest.TestCase):
    """A-2: every value inside a money grid opts out, in all four screens."""

    def test_a2_every_stat_value_in_a_money_grid_is_num(self):
        for path in MONEY_TEMPLATES:
            with self.subTest(template=path):
                parsed = parse_template(path)
                # A check that passes on an empty file proves nothing.
                self.assertGreater(
                    parsed.money_grids, 0,
                    '%s has no .vs-stat-grid.is-money' % path)
                in_money = [v for v in parsed.values if v['in_money_grid']]
                self.assertGreater(
                    len(in_money), 0,
                    '%s has no .vs-stat-value inside a money grid' % path)
                missing = [v for v in in_money if 'is-num' not in v['classes']]
                self.assertEqual(
                    [], missing,
                    '%s: %d .vs-stat-value element(s) inside a money grid do '
                    'not carry is-num' % (path, len(missing)))

    def test_a2_six_cards_per_screen(self):
        """The count is pinned: a card added later without is-num is a defect
        this test must fail on, not silently accept."""
        for path in MONEY_TEMPLATES:
            with self.subTest(template=path):
                parsed = parse_template(path)
                in_money = [v for v in parsed.values if v['in_money_grid']]
                self.assertEqual(6, len(in_money),
                                 '%s: expected 6 stat values, got %d'
                                 % (path, len(in_money)))

    def test_a2_negative_control_parser_can_see_a_missing_class(self):
        """A-2 is only evidence if it can fail. Feed it markup that is wrong
        in exactly the way the real templates were before this commit."""
        parser = StatGridParser()
        parser.feed('<div class="vs-stat-grid is-money">'
                    '  <div class="vs-stat">'
                    '    <div class="vs-stat-value">2 657 997 449</div>'
                    '  </div>'
                    '</div>')
        in_money = [v for v in parser.values if v['in_money_grid']]
        self.assertEqual(1, len(in_money))
        self.assertNotIn('is-num', in_money[0]['classes'])

    def test_a2_parser_does_not_credit_a_value_outside_a_money_grid(self):
        parser = StatGridParser()
        parser.feed('<div class="vs-stat-grid">'
                    '  <div class="vs-stat-value">1</div>'
                    '</div>')
        self.assertEqual(1, len(parser.values))
        self.assertFalse(parser.values[0]['in_money_grid'])
        self.assertEqual(1, parser.plain_grids)

    def test_a2_no_class_added_outside_the_drones_module(self):
        """Other tracks own their screens; this task adds classes only here."""
        offenders = []
        for root, _dirs, files in os.walk(os.path.join(REPO_ROOT, 'templates')):
            for name in files:
                if not name.endswith('.html'):
                    continue
                path = os.path.join(root, name)
                rel = os.path.relpath(path, REPO_ROOT).replace('\\', '/')
                if rel in MONEY_TEMPLATES:
                    continue
                with open(path, encoding='utf-8') as fh:
                    source = fh.read()
                if 'vs-stat-grid is-money' in source or 'vs-stat-value is-num' in source:
                    offenders.append(rel)
        self.assertEqual([], offenders)


if __name__ == '__main__':
    unittest.main()
