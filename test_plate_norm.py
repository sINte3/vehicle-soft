# -*- coding: utf-8 -*-
"""Tests for plate_norm.normalize_plate (PLATE-NORM-001).

Run:
  python test_plate_norm.py
"""

import unittest

from plate_norm import normalize_plate


class NormalizePlateTests(unittest.TestCase):

    def test_latin_input(self):
        self.assertEqual(normalize_plate('80 265 EA'), '80265EA')

    def test_cyrillic_input_folds_to_latin(self):
        # 'ЕА' here is Cyrillic; the result must be the Latin form.
        self.assertEqual(normalize_plate('80 265 ЕА'), '80265EA')

    def test_homoglyph_spellings_become_equal(self):
        # The core requirement: the two visually identical spellings from
        # adjacent equipment reference rows must compare equal.
        self.assertEqual(normalize_plate('80 265 EA'),
                         normalize_plate('80 265 ЕА'))

    def test_mixed_alphabets_in_one_plate(self):
        # Latin 'E' + Cyrillic 'А' in the same value.
        self.assertEqual(normalize_plate('80 265 EА'), '80265EA')

    def test_full_homoglyph_set_uppercase(self):
        self.assertEqual(normalize_plate('АВЕКМНОРСТУХ'), 'ABEKMHOPCTYX')

    def test_full_homoglyph_set_lowercase(self):
        # Lowercase Cyrillic upper-cases first, then folds.
        self.assertEqual(normalize_plate('авекмнорстух'), 'ABEKMHOPCTYX')

    def test_lowercase_latin_is_uppercased(self):
        self.assertEqual(normalize_plate('80 265 ea'), '80265EA')

    def test_spacing_variants(self):
        self.assertEqual(normalize_plate('  80  265\tEA '), '80265EA')
        self.assertEqual(normalize_plate('80 265 EA'), '80265EA')

    def test_punctuation_is_stripped(self):
        self.assertEqual(normalize_plate('80-265.EA'), '80265EA')
        self.assertEqual(normalize_plate('80/265_EA'), '80265EA')

    def test_digits_only_plate(self):
        self.assertEqual(normalize_plate(' 01 234 '), '01234')

    def test_empty_string_returns_empty(self):
        self.assertEqual(normalize_plate(''), '')

    def test_none_returns_empty(self):
        self.assertEqual(normalize_plate(None), '')

    def test_whitespace_only_returns_empty(self):
        self.assertEqual(normalize_plate('   '), '')

    def test_non_homoglyph_letters_pass_through(self):
        # Letters outside the twelve pairs are not guessed at.
        self.assertEqual(normalize_plate('80 265 QZ'), '80265QZ')


if __name__ == '__main__':
    unittest.main(verbosity=2)
