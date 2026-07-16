# -*- coding: utf-8 -*-
"""SPARE-PARTS-CYCLE-2-3 Part 7: bilingual canonical part names."""
import os
import sqlite3
import tempfile
import unittest

from tests.harness import app, db, reset_db, create_admin, login, CSRF
from models import SparePart

import migrate_spare_parts_name_uz as name_uz_mig


class NameUzMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='name_uz_mig_')
        self.db_path = os.path.join(self.tmp, 'db.sqlite')
        self._orig = name_uz_mig.DB_PATH
        name_uz_mig.DB_PATH = self.db_path

    def tearDown(self):
        name_uz_mig.DB_PATH = self._orig

    def _make_db(self, with_table=True):
        con = sqlite3.connect(self.db_path)
        if with_table:
            con.execute("CREATE TABLE spare_parts ("
                        "id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
            con.execute("INSERT INTO spare_parts (name) VALUES ('Фильтр')")
        con.commit()
        con.close()

    def _cols(self):
        con = sqlite3.connect(self.db_path)
        try:
            return {r[1] for r in con.execute("PRAGMA table_info(spare_parts)")}
        finally:
            con.close()

    def _registered(self):
        con = sqlite3.connect(self.db_path)
        try:
            return con.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE name=?",
                (name_uz_mig.MIGRATION_ID,)).fetchone()[0]
        finally:
            con.close()

    def test_adds_column_and_registers(self):
        self._make_db()
        name_uz_mig.run()
        self.assertIn('name_uz', self._cols())
        self.assertEqual(self._registered(), 1)
        con = sqlite3.connect(self.db_path)
        # Existing data untouched, new column NULL.
        row = con.execute("SELECT name, name_uz FROM spare_parts").fetchone()
        con.close()
        self.assertEqual(row, ('Фильтр', None))

    def test_idempotent_rerun_skips(self):
        self._make_db()
        name_uz_mig.run()
        name_uz_mig.run()  # must not raise or duplicate
        self.assertEqual(self._registered(), 1)

    def test_missing_prerequisite_fails_and_records_nothing(self):
        self._make_db(with_table=False)
        with self.assertRaises(SystemExit):
            name_uz_mig.run()
        con = sqlite3.connect(self.db_path)
        n = con.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
        con.close()
        self.assertEqual(n, 0)


class NameUzDisplayTests(unittest.TestCase):
    def setUp(self):
        reset_db()
        self.admin_id = create_admin()
        with app.app_context():
            p1 = SparePart(name='Фильтр масляный', name_uz='Мой фильтри',
                           status='active')
            p2 = SparePart(name='Ремень привода', status='active')
            db.session.add_all([p1, p2])
            db.session.commit()
            self.p1_id, self.p2_id = p1.id, p2.id

    def _call(self, fn, lang, *args):
        import spare_parts
        with app.test_request_context():
            from flask import g
            g.lang = lang
            return getattr(spare_parts, fn)(*args)

    def test_display_name_uses_alias_only_in_uz(self):
        with app.app_context():
            p1 = SparePart.query.get(self.p1_id)
            p2 = SparePart.query.get(self.p2_id)
            import spare_parts
            with app.test_request_context():
                from flask import g
                g.lang = 'uz'
                self.assertEqual(spare_parts._part_display_name(p1), 'Мой фильтри')
                self.assertEqual(spare_parts._part_display_name(p2), 'Ремень привода')
            with app.test_request_context():
                from flask import g
                g.lang = 'ru'
                self.assertEqual(spare_parts._part_display_name(p1), 'Фильтр масляный')

    def test_snapshot_override_is_uz_only_and_never_blank(self):
        with app.app_context():
            p1 = SparePart.query.get(self.p1_id)
            p2 = SparePart.query.get(self.p2_id)
            import spare_parts
            self.assertEqual(
                spare_parts._snapshot_display_name('как ввёл оператор', p1, 'ru'),
                'как ввёл оператор')
            self.assertEqual(
                spare_parts._snapshot_display_name('как ввёл оператор', p1, 'uz'),
                'Мой фильтри')
            self.assertEqual(
                spare_parts._snapshot_display_name('как ввёл оператор', p2, 'uz'),
                'как ввёл оператор')
            self.assertEqual(
                spare_parts._snapshot_display_name('свободный текст', None, 'uz'),
                'свободный текст')

    def test_fuzzy_search_matches_alias_and_resolves_same_part(self):
        client = app.test_client()
        login(client, self.admin_id)
        resp = client.get('/spare-parts/api/catalog-search?q=Мой фильтри')
        data = resp.get_json()
        self.assertTrue(data, 'alias search returned nothing')
        self.assertEqual(data[0]['id'], self.p1_id)
        # Canonical name still matches too.
        resp = client.get('/spare-parts/api/catalog-search?q=Фильтр масляный')
        self.assertEqual(resp.get_json()[0]['id'], self.p1_id)

    def test_catalog_save_and_inline_route_store_alias(self):
        client = app.test_client()
        login(client, self.admin_id)
        resp = client.post('/spare-parts/catalog/save', data={
            'csrf_token': CSRF, 'id': str(self.p2_id),
            'name': 'Ремень привода', 'name_uz': 'Юритма тасмаси',
            'part_number': '', 'unit': 'dona'})
        self.assertEqual(resp.status_code, 302)
        with app.app_context():
            self.assertEqual(SparePart.query.get(self.p2_id).name_uz,
                             'Юритма тасмаси')
        # Inline single-field route; empty value clears back to NULL.
        resp = client.post('/spare-parts/catalog/{}/name-uz'.format(self.p2_id),
                           data={'csrf_token': CSRF, 'name_uz': '  '})
        self.assertEqual(resp.status_code, 302)
        with app.app_context():
            self.assertIsNone(SparePart.query.get(self.p2_id).name_uz)


if __name__ == '__main__':
    unittest.main()
