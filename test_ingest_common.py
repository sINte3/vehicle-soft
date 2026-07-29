# -*- coding: utf-8 -*-
"""Tests for ingest_common (PHASE1 shared foundation).

Run:
  python test_ingest_common.py
"""

import unittest

from ingest_common import IngestCounters, extract_token, verify_api_token


class VerifyApiTokenTests(unittest.TestCase):

    def test_correct_token(self):
        self.assertTrue(verify_api_token('secret-1', 'secret-1'))

    def test_wrong_token(self):
        self.assertFalse(verify_api_token('secret-2', 'secret-1'))

    def test_empty_expected_denies(self):
        # A missing environment variable must deny, never accept.
        self.assertFalse(verify_api_token('anything', ''))

    def test_none_expected_denies(self):
        self.assertFalse(verify_api_token('anything', None))

    def test_empty_provided_against_empty_expected_denies(self):
        self.assertFalse(verify_api_token('', ''))

    def test_none_provided_denies(self):
        self.assertFalse(verify_api_token(None, 'secret-1'))

    def test_bytes_and_str_compare_equal(self):
        self.assertTrue(verify_api_token(b'secret-1', 'secret-1'))
        self.assertTrue(verify_api_token('secret-1', b'secret-1'))

    def test_non_string_provided_is_stringified(self):
        self.assertTrue(verify_api_token(123456, '123456'))
        self.assertFalse(verify_api_token(123457, '123456'))


class IngestCountersTests(unittest.TestCase):

    def test_starts_at_zero(self):
        counters = IngestCounters()
        self.assertEqual(counters.as_dict(),
                         {'new': 0, 'duplicates': 0, 'errors': 0})

    def test_as_dict_reports_three_separate_outcomes(self):
        counters = IngestCounters(new=5, duplicates=2, errors=1)
        self.assertEqual(counters.as_dict(),
                         {'new': 5, 'duplicates': 2, 'errors': 1})

    def test_merge_accumulates(self):
        total = IngestCounters(new=1, duplicates=1, errors=0)
        total.merge(IngestCounters(new=2, duplicates=0, errors=3))
        self.assertEqual(total.as_dict(),
                         {'new': 3, 'duplicates': 1, 'errors': 3})

    def test_merge_returns_self_for_chaining(self):
        total = IngestCounters()
        result = total.merge(IngestCounters(new=1)).merge(
            IngestCounters(duplicates=2))
        self.assertIs(result, total)
        self.assertEqual(total.as_dict(),
                         {'new': 1, 'duplicates': 2, 'errors': 0})


class ExtractTokenTests(unittest.TestCase):

    def test_reads_token_from_body_dict(self):
        self.assertEqual(extract_token({'token': 'abc', 'rows': []}), 'abc')

    def test_missing_token_returns_none(self):
        self.assertIsNone(extract_token({'rows': []}))

    def test_non_dict_payload_returns_none(self):
        self.assertIsNone(extract_token(None))
        self.assertIsNone(extract_token([('token', 'abc')]))
        self.assertIsNone(extract_token('token=abc'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
