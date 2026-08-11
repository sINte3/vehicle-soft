# -*- coding: utf-8 -*-
"""DRONE-REATTACH-001: улики по вылетам без машины считаются, а не выдумываются.

Проверяется три вещи и к каждой — отрицательный контроль, потому что проверка,
дающая одинаковый результат при верном и неверном своде, проверкой не является:

  1. Свод гомоглифов сводит РОВНО двойников. «8 Gаrden» с кириллической «а» и
     «8 Garden» — одно написание в двух раскладках; «8 Garden» и «9 Garden» —
     разные, и никакой свод не имеет права их слить. `ў`, `ғ`, `қ` латинских
     двойников не имеют, поэтому «Мирзачўл» никогда не сводится к «Mirzachul».
  2. Классы: HOMOGLYPH называет машину, AMBIGUOUS отказывается называть,
     UNKNOWN оставляет решение владельцу. Отключённый алиас не участвует —
     тот же фильтр, что у приёма, иначе инструмент обещал бы то, чего приём
     не сделает.
  3. Инструмент НЕ ПИШЕТ. База открыта read-only, файл побайтно тот же после
     прогона, и попытка записи через то же соединение падает.

Run:
  python -m unittest tests.test_drone_reattach_evidence_001 -v
"""
import hashlib
import os
import sqlite3
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, 'tools'))
import drone_reattach_evidence as evidence  # noqa: E402


def build_db(path):
    """Синтетическая база ровно из трёх таблиц, которые читает инструмент."""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE drone_units (
            id INTEGER PRIMARY KEY, number INTEGER NOT NULL);
        CREATE TABLE drone_nicknames (
            id INTEGER PRIMARY KEY, drone_unit_id INTEGER NOT NULL,
            nickname TEXT NOT NULL, normalized TEXT NOT NULL,
            is_active INTEGER DEFAULT 1);
        CREATE TABLE drone_flights (
            id INTEGER PRIMARY KEY, drone_unit_id INTEGER,
            nickname_raw TEXT, started_at TEXT NOT NULL,
            area_ha REAL NOT NULL DEFAULT 0, region TEXT);
    """)
    conn.executemany('INSERT INTO drone_units (id, number) VALUES (?, ?)',
                     [(1, 8), (2, 9), (3, 12)])
    conn.commit()
    return conn


def add_alias(conn, unit_id, nickname, is_active=1):
    conn.execute(
        'INSERT INTO drone_nicknames (drone_unit_id, nickname, normalized, '
        'is_active) VALUES (?, ?, ?, ?)',
        (unit_id, nickname, evidence.normalize_nickname(nickname), is_active))
    conn.commit()


def add_flight(conn, nickname, started_at='2025-09-14 06:00:00', area=1.5,
               region='Bukhara Region', unit_id=None):
    conn.execute(
        'INSERT INTO drone_flights (drone_unit_id, nickname_raw, started_at, '
        'area_ha, region) VALUES (?, ?, ?, ?, ?)',
        (unit_id, nickname, started_at, area, region))
    conn.commit()


class Fold(unittest.TestCase):
    """Свод гомоглифов: сводит двойников и только их."""

    def test_cyrillic_lookalike_folds_to_the_latin_spelling(self):
        # «а», «е», «о», «с», «р» — кириллические, остальное латиница.
        typed_in_cyrillic = '8 Gаrden'
        self.assertEqual(evidence.evidence_key(typed_in_cyrillic),
                         evidence.evidence_key('8 Garden'))

    def test_uppercase_folds_because_folding_precedes_lowercasing(self):
        """Прописная «К» сводится до понижения регистра, иначе потерялась бы.

        Строчной «к» в консервативном своде нет намеренно; если бы порядок был
        обратным, «КOGON» и «KOGON» разъехались бы.
        """
        self.assertEqual(evidence.evidence_key('КOGON'),
                         evidence.evidence_key('KOGON'))

    def test_different_digit_never_folds(self):
        """Отрицательный контроль: свод, сливающий всё, — не свод."""
        self.assertNotEqual(evidence.evidence_key('8 Garden'),
                            evidence.evidence_key('9 Garden'))

    def test_letters_without_a_latin_twin_never_fold(self):
        """«ў», «ғ», «қ», «ҳ» двойников не имеют: Мирзачўл != Mirzachul."""
        self.assertNotEqual(evidence.evidence_key('Мирзачџл'),
                            evidence.evidence_key('Mirzachul'))
        self.assertNotEqual(evidence.evidence_key('Ғиждувон'),
                            evidence.evidence_key('Gijduvon'))

    def test_the_map_is_about_shape_and_not_about_sound(self):
        """Граница, на которой я сам ошибся, когда писал фикстуру.

        Кириллическая «С» звучит как «S», но ВЫГЛЯДИТ как «C» — сводится она
        в «C». Свод по звучанию был бы транслитерацией, а транслитерация
        сливает разные имена: «Сервис» и «Servis» — не одно написание в двух
        раскладках, а два разных слова, и решать по ним обязан владелец.
        """
        self.assertEqual(evidence.fold_homoglyphs('Сervis'), 'Cervis')
        self.assertNotEqual(evidence.evidence_key('Сervis'),
                            evidence.evidence_key('Servis'))

    def test_lowercase_k_m_n_t_are_deliberately_out_of_the_map(self):
        """Строчные различимы в шрифте — сводить их значит рисковать машиной."""
        for cyr, lat in (('к', 'k'), ('м', 'm'), ('н', 'h'),
                         ('т', 't'), ('в', 'b')):
            self.assertNotEqual(evidence.fold_homoglyphs(cyr), lat,
                                'строчная %r не должна сводиться' % cyr)

    def test_normalization_repeats_the_ingest_rule(self):
        """lower + удаление ВСЕХ пробелов — дословно правило приёма."""
        self.assertEqual(evidence.normalize_nickname('  8   GARDEN '),
                         '8garden')


class Classification(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='reattach_ev_')
        self.db_path = os.path.join(self.tmp, 'transport.db')
        self.conn = build_db(self.db_path)

    def tearDown(self):
        self.conn.close()

    def _run(self):
        alias_keys, spellings = evidence.load_aliases(self.conn)
        groups = evidence.load_unattributed(self.conn)
        evidence.classify(groups, alias_keys)
        return {g['nickname']: g for g in groups}, spellings

    def test_homoglyph_names_the_machine_of_the_matching_alias(self):
        add_alias(self.conn, 1, '8 Garden')
        add_flight(self.conn, '8 Gаrden')          # кириллическая «а»
        groups, _ = self._run()
        row = groups['8 Gаrden']
        self.assertEqual(row['verdict'], 'HOMOGLYPH')
        self.assertEqual(row['unit_number'], 8)

    def test_unknown_when_no_alias_matches(self):
        add_alias(self.conn, 1, '8 Garden')
        add_flight(self.conn, 'Q.Nurali')
        groups, _ = self._run()
        self.assertEqual(groups['Q.Nurali']['verdict'], 'UNKNOWN')
        self.assertIsNone(groups['Q.Nurali']['unit_number'])

    def test_ambiguous_key_names_no_machine(self):
        """Два алиаса с одним ключом на разных машинах — отказ, не выбор."""
        add_alias(self.conn, 1, 'GardenN6')
        add_alias(self.conn, 2, 'GаrdenN6')        # кириллическая «а»
        add_flight(self.conn, 'gardenn6')
        groups, _ = self._run()
        row = groups['gardenn6']
        self.assertEqual(row['verdict'], 'AMBIGUOUS')
        self.assertIsNone(row['unit_number'])
        self.assertEqual(row['candidates'], [8, 9])

    def test_inactive_alias_does_not_resolve_anything(self):
        """Тот же фильтр, что у приёма: выключенный алиас не обещает ничего."""
        add_alias(self.conn, 1, '8 Garden', is_active=0)
        add_flight(self.conn, '8 Gаrden')
        groups, _ = self._run()
        self.assertEqual(groups['8 Gаrden']['verdict'], 'UNKNOWN')

    def test_null_is_active_counts_as_active_like_the_ingest(self):
        """NULL в старой строке — активен: `is_active.isnot(False)` в drones.py."""
        self.conn.execute(
            'INSERT INTO drone_nicknames (drone_unit_id, nickname, '
            'normalized, is_active) VALUES (?, ?, ?, NULL)',
            (3, '12 Peshku', evidence.normalize_nickname('12 Peshku')))
        self.conn.commit()
        add_flight(self.conn, '12 Peshku')
        groups, _ = self._run()
        self.assertEqual(groups['12 Peshku']['verdict'], 'HOMOGLYPH')
        self.assertEqual(groups['12 Peshku']['unit_number'], 12)

    def test_unresolved_spellings_that_fold_together_are_one_family(self):
        """Сорок написаний — меньше сорока имён; семья это показывает."""
        add_flight(self.conn, 'GаrdenN6')          # кириллическая «а»
        add_flight(self.conn, 'GardenN6')
        groups, _ = self._run()
        self.assertEqual(groups['GardenN6']['family'], ['GаrdenN6'])
        self.assertEqual(groups['GаrdenN6']['family'], ['GardenN6'])

    def test_attributed_flights_are_not_in_the_report(self):
        """Отрицательный контроль: инструмент смотрит только на NULL-строки."""
        add_alias(self.conn, 1, '8 Garden')
        add_flight(self.conn, '8 Garden', unit_id=1)
        groups, _ = self._run()
        self.assertEqual(groups, {})

    def test_totals_come_from_the_rows_not_from_a_constant(self):
        add_flight(self.conn, 'Q.Nurali', area=2.25)
        add_flight(self.conn, 'Q.Nurali', area=1.75)
        groups, _ = self._run()
        self.assertEqual(groups['Q.Nurali']['flights'], 2)
        self.assertAlmostEqual(groups['Q.Nurali']['area'], 4.0, places=6)


class WritesNothing(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='reattach_ev_ro_')
        self.db_path = os.path.join(self.tmp, 'transport.db')
        conn = build_db(self.db_path)
        add_alias(conn, 1, '8 Garden')
        add_flight(conn, 'Q.Nurali')
        conn.close()

    def _digest(self):
        with open(self.db_path, 'rb') as handle:
            return hashlib.sha256(handle.read()).hexdigest()

    def test_read_only_connection_refuses_a_write(self):
        """Положительный контроль режима: mode=ro обязан ОТКАЗАТЬ в записи."""
        conn = evidence.open_readonly(self.db_path)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute('UPDATE drone_flights SET area_ha = 99')
        finally:
            conn.close()

    def test_full_run_leaves_the_file_byte_for_byte_identical(self):
        before = self._digest()
        report = os.path.join(self.tmp, 'evidence.txt')
        argv = sys.argv
        sys.argv = ['drone_reattach_evidence.py', '--db', self.db_path,
                    '--report', report]
        try:
            self.assertEqual(evidence.main(), 0)
        finally:
            sys.argv = argv
        self.assertEqual(self._digest(), before)
        self.assertTrue(os.path.exists(report))
        with open(report, encoding='utf-8') as handle:
            text = handle.read()
        self.assertIn('Q.Nurali', text)
        self.assertIn('NOTHING WAS WRITTEN', text)

    def test_missing_database_exits_with_code_two_and_creates_nothing(self):
        missing = os.path.join(self.tmp, 'absent.db')
        with self.assertRaises(SystemExit) as caught:
            evidence.open_readonly(missing)
        self.assertEqual(caught.exception.code, 2)
        self.assertFalse(os.path.exists(missing))


if __name__ == '__main__':
    unittest.main()
