# -*- coding: utf-8 -*-
"""DRONES_WORKS_SEPT2025_REDATE_001 отменена: она обязана отказываться работать.

Миграция, кроме дат, передавала 76.00 га Анварову. Владелец отменил это
2026-08-19: «Гектары писали на операторов. У Холмуродова по книге 879,5.»
Файл оставлен в репозитории ради истории (PR #88, гейт, трековый файл), но
выполняться не должен -- даты чинит REDATE_002.

Проверяется ровно то, что нужно от отменённой миграции: она отказывается,
называет замену и НЕ ТРОГАЕТ базу. Отрицательный контроль -- база сверяется
побайтово до и после, иначе «ничего не изменилось» было бы утверждением, а
не проверкой.
"""

import hashlib
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import migration_utils  # noqa: E402
import migrate_drones_works_sept2025_redate_001 as mig  # noqa: E402

SCHEMA = """
CREATE TABLE drone_operators (id INTEGER PRIMARY KEY, full_name VARCHAR(200));
CREATE TABLE drone_works (
    id INTEGER PRIMARY KEY AUTOINCREMENT, period_month VARCHAR(7) NOT NULL,
    work_date_from DATE, work_date_to DATE, date_raw VARCHAR(100),
    drone_operator_id INTEGER, customer_raw VARCHAR(300) NOT NULL,
    area_ha NUMERIC NOT NULL);
"""

TARGETS = (
    ('Розия Бекова', 25.0, '2025-10-20', '2025-10-20', '2025-10-20'),
    ('Анвар Ота замини', 27.5, '2025-10-23', '2025-10-23', '2025-10-23'),
    ('Умарбобо Усмонов', 23.5, '2025-10-27', '2025-10-27', '2025-10-27'),
    ('Бухоро Агрокластер Заминлари', 511.5, '2025-10-19', '2025-10-30',
     '19-30.10.2025'),
)


class WithdrawnTest(unittest.TestCase):

    def setUp(self):
        self._real_db = migration_utils.DB_PATH
        self._real_mig_db = mig.DB_PATH
        self.dir = tempfile.mkdtemp(prefix='redate001_')
        self.path = os.path.join(self.dir, 'transport.db')
        con = sqlite3.connect(self.path)
        con.executescript(SCHEMA)
        con.execute("INSERT INTO drone_operators (id, full_name) VALUES "
                    "(1, 'Холмуродов Шахзод'), (2, 'Анваров Усмон')")
        for customer, area, dfrom, dto, raw in TARGETS:
            con.execute(
                'INSERT INTO drone_works (period_month, work_date_from, '
                'work_date_to, date_raw, drone_operator_id, customer_raw, '
                'area_ha) VALUES (?, ?, ?, ?, 1, ?, ?)',
                ('2025-09', dfrom, dto, raw, customer, area))
        con.commit()
        con.close()

    def tearDown(self):
        migration_utils.DB_PATH = self._real_db
        mig.DB_PATH = self._real_mig_db
        shutil.rmtree(self.dir, ignore_errors=True)

    def digest(self):
        with open(self.path, 'rb') as handle:
            return hashlib.sha256(handle.read()).hexdigest()

    def test_apply_refuses_and_changes_nothing(self):
        before = self.digest()
        self.assertEqual(3, mig.main(['--db', self.path, '--apply']))
        self.assertEqual(before, self.digest())

    def test_dry_run_refuses_too(self):
        before = self.digest()
        self.assertEqual(3, mig.main(['--db', self.path]))
        self.assertEqual(before, self.digest())

    def test_the_refusal_names_the_replacement(self):
        self.assertEqual('migrate_drones_works_sept2025_redate_002.py',
                         mig.SUPERSEDED_BY)
        replacement = os.path.join(REPO_ROOT, mig.SUPERSEDED_BY)
        self.assertTrue(os.path.isfile(replacement),
                        'замена названа, но файла нет: %s' % replacement)

    def test_the_body_is_kept_for_the_record(self):
        """Тело не удалено -- на него ссылается история PR #88.

        [REASON]: без этой проверки заслон можно «починить», удалив тело, и
        описание в гейте начнёт ссылаться в пустоту.
        """
        self.assertTrue(callable(getattr(mig, '_withdrawn_main', None)))
        self.assertEqual(4, len(mig.ROWS))
        self.assertEqual(76.0, mig.MOVED_HA)
        self.assertEqual(587.5, mig.REDATED_HA)


if __name__ == '__main__':
    unittest.main()
