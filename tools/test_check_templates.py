#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for tools/check_templates.py against the planted fixtures in
tools/test_check_templates/. Each fixture carries exactly one defect of one
class (plus one clean file); the checker must find exactly the planted
defects and nothing else.

Run:
  python tools/test_check_templates.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import check_templates as ct  # noqa: E402  (path set up just above)

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'test_check_templates')


def scan(name):
    return ct.check_file(os.path.join(FIXTURES, name))


class CheckTemplatesFixtureTests(unittest.TestCase):

    def test_clean_file_has_no_findings(self):
        self.assertEqual(scan('clean.html'), [])

    def test_parse_failure_is_blocking(self):
        findings = scan('bad_parse.html')
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding.check, 'jinja-parse')
        self.assertEqual(finding.severity, ct.BLOCKING)

    def test_unbalanced_div_is_blocking_and_ignores_comments(self):
        findings = scan('bad_div.html')
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding.check, 'div-balance')
        self.assertEqual(finding.severity, ct.BLOCKING)
        # 2 real opens vs 1 close; the <div> inside the comment is ignored.
        self.assertIn('2 opening vs 1 closing', finding.message)

    def test_tojson_in_double_quoted_attribute_is_blocking(self):
        findings = scan('bad_tojson.html')
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding.check, 'tojson-attr')
        self.assertEqual(finding.severity, ct.BLOCKING)
        self.assertEqual(finding.line, 2)

    def test_set_before_use_is_warning_only_with_exclusions(self):
        findings = scan('bad_set_before_use.html')
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding.check, 'set-before-use')
        self.assertEqual(finding.severity, ct.WARNING)
        self.assertEqual(finding.line, 4)
        self.assertIn("'total'", finding.message)

    def test_tojson_in_single_quoted_attribute_is_allowed(self):
        # clean.html carries data-rows='{{ rows|tojson }}' -- covered by
        # test_clean_file_has_no_findings, restated here for intent.
        checks = {f.check for f in scan('clean.html')}
        self.assertNotIn('tojson-attr', checks)

    def test_exit_semantics_warning_never_blocks(self):
        findings = scan('bad_set_before_use.html')
        self.assertTrue(all(f.severity == ct.WARNING for f in findings))


if __name__ == '__main__':
    unittest.main(verbosity=2)
