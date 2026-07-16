# -*- coding: utf-8 -*-
"""SPARE-PARTS-CYCLE-2-3 Part 1 (RE-SP-010): localized unit labels in output.

Uses the shared disposable-SQLite harness — never touches real data.
"""
import unittest

from tests.harness import app, db, reset_db, create_admin, login
from models import SparePartUnit


class UnitLabelTests(unittest.TestCase):
    def setUp(self):
        reset_db()
        self.admin_id = create_admin()
        with app.app_context():
            db.session.add(SparePartUnit(code='dona', name_ru='шт',
                                         name_uz='дона', is_active=True))
            db.session.add(SparePartUnit(code='litr', name_ru='л',
                                         name_uz='литр', is_active=False))
            db.session.commit()

    def _label(self, code, lang):
        import spare_parts
        with app.test_request_context():
            from flask import g
            g.lang = lang
            return spare_parts._unit_label(code)

    def test_known_code_resolves_per_language(self):
        self.assertEqual(self._label('dona', 'ru'), 'шт')
        self.assertEqual(self._label('dona', 'uz'), 'дона')

    def test_inactive_directory_row_still_resolves(self):
        # Historical rows may carry a deactivated code; it must stay readable.
        self.assertEqual(self._label('litr', 'ru'), 'л')

    def test_unknown_legacy_code_passes_through_raw(self):
        self.assertEqual(self._label('metr', 'ru'), 'metr')

    def test_empty_value_stays_empty_never_raises(self):
        self.assertEqual(self._label('', 'ru'), '')
        self.assertEqual(self._label(None, 'ru'), '')

    def test_pdf_accepts_unit_labels_map(self):
        # Signature-level regression: the optional map must not break the
        # default (no-map) path.
        import inspect
        from spare_parts_pdf import generate_write_off_act_pdf
        params = inspect.signature(generate_write_off_act_pdf).parameters
        self.assertIn('unit_labels', params)
        self.assertIsNone(params['unit_labels'].default)


if __name__ == '__main__':
    unittest.main()
