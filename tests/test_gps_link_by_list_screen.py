# -*- coding: utf-8 -*-
"""GPS-17 `svyazat_stroku` против настоящего приложения.

Зачем отдельно от `tests/test_gps_link_by_list.py`: тот файл -- stdlib, без
Flask, как и сам инструмент. Здесь проверяется то, ради чего строку
переименовывают, -- самим кодом приложения, а не его пересказом:

1. Импорт моточасов (`wialon_import.apply_mappings`) по имени объекта машину
   до прогона не находит, а после -- находит; по прежнему имени -- нет.
2. Откат, описанный в docstring инструмента, выполним на экране: корзина у
   строки и «+ Добавить» с прежним именем и той же машиной возвращают строку
   под старым именем, с машиной и без id.
3. «+ Добавить» с новым именем после прогона обновляет ту же строку, а не
   заводит вторую.
"""
import io
import os
import sys
import tempfile
import unittest

from tests.harness import app, db, reset_db, create_admin, create_org, login, CSRF
from models import Equipment, VialonMapping

import tools.gps_link_by_list as link
from gps_collector import storage
from wialon_import import apply_mappings

OLD_NAME = 'МТЗ 269 ЕА'     # кириллица целиком
NEW_NAME = 'МТЗ 269 EA'               # объект: латинские EA
UNIT = 3446


class AttachAgainstTheApp(unittest.TestCase):

    def setUp(self):
        reset_db()
        self.admin = create_admin()
        self.client = app.test_client()
        login(self.client, self.admin)
        organization = create_org()
        with app.app_context():
            machine = Equipment(name='МТЗ-80Х', plate='80 269 ЕА',
                                category='mtz', organization_id=organization,
                                is_active=True)
            db.session.add(machine)
            db.session.commit()
            self.machine = machine.id
            row = VialonMapping(vialon_name=OLD_NAME, equipment_id=self.machine,
                                skip=False, created_by=self.admin)
            db.session.add(row)
            db.session.commit()
            self.row = row.id
            self.db_path = db.engine.url.database
        self.folder = tempfile.mkdtemp()
        con = storage.open_units(self.folder)
        try:
            con.execute('INSERT INTO collector_units (unit_id, name, seen_at) '
                        "VALUES (?, ?, '2026-10-14 06:00:00')", (UNIT, NEW_NAME))
            con.commit()
        finally:
            con.close()
        self.list = os.path.join(self.folder, 'list.csv')
        with open(self.list, 'w', encoding='utf-8', newline='') as handle:
            handle.write('wialon_id,equipment_id,deystvie,osnovanie\n')
            handle.write('%d,%d,svyazat_stroku,nomer\n' % (UNIT, self.machine))

    def run_tool(self):
        out = io.StringIO()
        saved = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = out
        try:
            with app.app_context():
                db.session.remove()
                db.engine.dispose()
            code = link.main(['--db', self.db_path, '--dir', self.folder,
                              '--list', self.list, '--apply',
                              '--out', os.path.join(self.folder, 'out.csv')])
        finally:
            sys.stdout, sys.stderr = saved
        self.assertEqual(code, 0, out.getvalue())

    def import_rows(self, name):
        with app.app_context():
            matched, unknown, skipped = apply_mappings(
                [{'vialon_name': name, 'engine_hours': 5, 'hours_moving': 3,
                  'hours_idle': 2}])
            return ([row['equipment'].id for row in matched], len(unknown),
                    len(skipped))

    def rows(self):
        with app.app_context():
            return [(m.id, m.vialon_name, m.equipment_id, bool(m.skip),
                     m.wialon_id)
                    for m in VialonMapping.query.order_by(VialonMapping.id)]

    def post(self, path, **fields):
        data = {'csrf_token': CSRF}
        data.update(fields)
        response = self.client.post(path, data=data, follow_redirects=True)
        self.assertEqual(response.status_code, 200)

    def test_the_import_finds_the_machine_only_after_the_rename(self):
        self.assertEqual(self.import_rows(NEW_NAME), ([], 1, 0))
        self.run_tool()
        self.assertEqual(self.import_rows(NEW_NAME), ([self.machine], 0, 0))
        self.assertEqual(self.import_rows(OLD_NAME), ([], 1, 0))

    def test_the_documented_rollback_works_on_the_screen(self):
        self.run_tool()
        self.post('/wialon/mapping/delete/%d' % self.row)
        self.post('/wialon/mapping/save', vialon_name=OLD_NAME,
                  equipment_id=str(self.machine), skip='0')
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1:], (OLD_NAME, self.machine, False, None))
        self.assertEqual(self.import_rows(OLD_NAME), ([self.machine], 0, 0))

    def test_adding_the_new_name_on_the_screen_updates_the_same_row(self):
        self.run_tool()
        self.post('/wialon/mapping/save', vialon_name=NEW_NAME,
                  equipment_id=str(self.machine), skip='0')
        self.assertEqual(self.rows(),
                         [(self.row, NEW_NAME, self.machine, False, UNIT)])


if __name__ == '__main__':
    unittest.main()
