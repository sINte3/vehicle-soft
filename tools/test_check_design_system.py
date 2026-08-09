#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Samotest tools/check_design_system.py.

Glavnoe trebovanie proekta k proverke: ona dolzhna RAZLICHAT vernyy i nevernyy
sluchay. Proverka, dayushchaya odinakovyy rezultat na oboikh, proverkoy ne
yavlyaetsya. Poetomu kazhdyy test zdes idet paroy: chistyy obrazec dolzhen dat
nol, gryaznyy -- imenno stolko, skolko v nem narusheniy.

Zapusk:
  python tools/test_check_design_system.py
  python -m unittest tools.test_check_design_system -v
"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools import check_design_system as cds  # noqa: E402


CLEAN = """{% extends 'base_next.html' %}
{% block content %}
<div class="vs-card">
  <table class="vs-table is-compact vs-num">
    <tr><td>{{ row.name }}</td></tr>
  </table>
</div>
{% endblock %}
"""

DIRTY = """{% extends 'base_next.html' %}
{% block content %}
<style>
  .fuel009-table { border-color: #e6ecf4; }
  .fuel009-table td { color: rgb(90, 90, 90); }
  @media (max-width: 760px) { .fuel009-table { font-size: 11px; } }
  @media (max-width: 1024px) { .fuel009-table { padding: 4px; } }
</style>
<table class="fuel009-table">
  <tr><td>Сохранить 💾</td><td>Удалить 🗑</td></tr>
</table>
<table>
  <tr><td>без класса</td></tr>
</table>
{% endblock %}
"""


class ScanTemplates(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='cds_test_')
        self.addCleanup(shutil.rmtree, self.dir, True)

    def write(self, name, body):
        path = os.path.join(self.dir, name)
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write(body)
        return path

    def counts_for(self, body):
        path = self.write('t.html', body)
        counts, _detail = cds.scan_templates([path])
        return {check: sum(files.values()) for check, files in counts.items()}

    def test_clean_template_scores_zero_everywhere(self):
        got = self.counts_for(CLEAN)
        for check in cds.CHECKS:
            self.assertEqual(got.get(check, 0), 0,
                             'clean template must not trip %s' % check)

    def test_dirty_template_is_counted_exactly(self):
        got = self.counts_for(DIRTY)
        # dva narusheniya: fuel009-table i tablica bez klassa
        self.assertEqual(got['table-class'], 2)
        # #e6ecf4 i rgb(90,90,90)
        self.assertEqual(got['hardcoded-color'], 2)
        # 760 vne shkaly, 1024 -- vnutri
        self.assertEqual(got['breakpoint'], 1)
        # dva emodzi
        self.assertEqual(got['glyph'], 2)
        # blok <style> neputoy
        self.assertGreater(got['inline-style'], 0)

    def test_clean_and_dirty_differ_on_every_check(self):
        """Otricatelnyy kontrol celikom: ni odna proverka ne slepa."""
        clean, dirty = self.counts_for(CLEAN), self.counts_for(DIRTY)
        for check in ('table-class', 'hardcoded-color', 'breakpoint',
                      'inline-style', 'glyph'):
            self.assertLess(clean.get(check, 0), dirty.get(check, 0),
                            'check %s gives the same result on clean and '
                            'dirty input -- it is not a check' % check)

    def test_allowed_breakpoints_pass_and_others_do_not(self):
        allowed = '<style>@media (max-width: 768px) { .a { color: var(--vs-text); } }</style>'
        denied = '<style>@media (max-width: 767px) { .a { color: var(--vs-text); } }</style>'
        self.assertEqual(self.counts_for(allowed)['breakpoint'], 0)
        self.assertEqual(self.counts_for(denied)['breakpoint'], 1)

    def test_vs_table_modifiers_are_allowed_but_foreign_class_is_not(self):
        for cls in ('vs-table', 'vs-table is-compact', 'vs-table is-compact vs-num'):
            body = '<table class="%s"><tr><td>x</td></tr></table>' % cls
            self.assertEqual(self.counts_for(body)['table-class'], 0, cls)
        for cls in ('vs-table extra', 'report-table', 'table table-sm'):
            body = '<table class="%s"><tr><td>x</td></tr></table>' % cls
            self.assertEqual(self.counts_for(body)['table-class'], 1, cls)

    def test_jinja_comments_are_ignored(self):
        body = '{# <table class="report-table"> #}\n<table class="vs-table"><tr><td>x</td></tr></table>'
        self.assertEqual(self.counts_for(body)['table-class'], 0)


class TokenDuplicates(unittest.TestCase):
    def test_same_value_under_two_names_is_found_by_value_not_by_name(self):
        css = ':root { --vs-bg: #eef2f7; --vs-border-3: #eef2f7; --vs-text: #182230; }'
        counts, detail = cds.scan_css(css)
        self.assertEqual(sum(counts.get('token-dup', {}).values()), 1)
        self.assertIn('--vs-bg', detail['token-dup'][0][1])
        self.assertIn('--vs-border-3', detail['token-dup'][0][1])

    def test_distinct_values_are_not_reported(self):
        css = ':root { --vs-bg: #eef2f7; --vs-border-3: #e2e8f1; }'
        counts, _ = cds.scan_css(css)
        self.assertEqual(sum(counts.get('token-dup', {}).values()), 0)

    def test_short_hex_is_compared_expanded(self):
        """#fff i #ffffff -- odin cvet; sravnenie po syroy stroke etogo ne vidit."""
        css = ':root { --a: #fff; --b: #ffffff; }'
        counts, _ = cds.scan_css(css)
        self.assertEqual(sum(counts.get('token-dup', {}).values()), 1)

    def test_duplicates_are_found_through_the_palette_layer(self):
        """Posle DD-023 roli ssylayutsya na palitru. Bez razresheniya var()
        proverka perestala by videt dubli voobshche -- i imenno tak
        --vs-bg = --vs-border-3 proshel by mimo."""
        css = (':root { --p-a: #eef2f7; --p-b: #eef2f7;'
               ' --vs-bg: var(--p-a); --vs-border-3: var(--p-b); }')
        counts, detail = cds.scan_css(css)
        self.assertEqual(sum(counts.get('token-dup', {}).values()), 1)
        self.assertIn('--vs-bg', detail['token-dup'][0][1])
        self.assertIn('--vs-border-3', detail['token-dup'][0][1])

    def test_palette_entries_sharing_a_value_are_not_reported(self):
        """Sovpadenie VNUTRI palitry -- ne defekt: palitra ne neset smysla."""
        css = ':root { --p-a: #eef2f7; --p-b: #eef2f7; }'
        counts, _ = cds.scan_css(css)
        self.assertEqual(sum(counts.get('token-dup', {}).values()), 0)

    def test_compat_aliases_are_not_reported(self):
        """--info: var(--vs-info) sovpadaet po postroeniyu; soobshchat o nem --
        shum, kotoryy pryachet nastoyashchie sovpadeniya."""
        css = (':root { --p-teal: #0e7490; --vs-info: var(--p-teal);'
               ' --info: var(--vs-info); }')
        counts, _ = cds.scan_css(css)
        self.assertEqual(sum(counts.get('token-dup', {}).values()), 0)

    def test_alias_exclusion_does_not_hide_a_real_duplicate(self):
        """Otricatelnyy kontrol k predydushchemu: otsev psevdonimov ne dolzhen
        zaglushat sluchay, kogda dve roli nezavisimo dayut odin cvet."""
        css = (':root { --p-teal: #0e7490; --vs-info: var(--p-teal);'
               ' --info: var(--vs-info); --vs-accent: #0e7490; }')
        counts, detail = cds.scan_css(css)
        self.assertEqual(sum(counts.get('token-dup', {}).values()), 1)
        self.assertIn('--vs-accent', detail['token-dup'][0][1])


class Ratchet(unittest.TestCase):
    def test_growth_is_a_regression_and_shrink_is_not(self):
        base = {'table-class': {'templates/a.html': 2}}
        same = {'table-class': {'templates/a.html': 2}}
        more = {'table-class': {'templates/a.html': 3}}
        less = {'table-class': {'templates/a.html': 1}}

        self.assertEqual(cds.compare(same, base)[0], [])
        self.assertEqual(cds.compare(less, base)[0], [])
        self.assertEqual(len(cds.compare(more, base)[0]), 1)
        self.assertEqual(len(cds.compare(less, base)[1]), 1)

    def test_baseline_is_per_file_so_moving_debt_around_is_caught(self):
        """Ubrat dva narusheniya v odnom fayle i dobavit dva v drugom --
        summa ta zhe, no eto ne uluchshenie i dolzhno byt zamecheno."""
        base = {'table-class': {'templates/a.html': 2, 'templates/b.html': 0}}
        moved = {'table-class': {'templates/a.html': 0, 'templates/b.html': 2}}
        regressions, _ = cds.compare(moved, base)
        self.assertEqual(len(regressions), 1)
        self.assertEqual(regressions[0][1], 'templates/b.html')

    def test_new_file_with_violations_is_a_regression(self):
        base = {'table-class': {}}
        added = {'table-class': {'templates/new.html': 1}}
        self.assertEqual(len(cds.compare(added, base)[0]), 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
