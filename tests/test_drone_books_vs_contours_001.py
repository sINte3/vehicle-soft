# -*- coding: utf-8 -*-
"""DRONE-LANDS-002: мост «заказчик книги <-> контур DJI» по имени.

Почему по имени, а не по координатам: привязка вылета к полю геометрией
провалилась, и причина измерена. Координата вылета -- это место взлёта, а не
поле: в четверти групп «день x машина» все вылеты стоят в одной точке с
разбросом 0 метров, а контур площадью 1.68 га получил 45 га работы. Полигон
этого не чинит.

Пары ниже -- НАСТОЯЩИЕ, из книг диспетчеров и справочника DJI за сентябрь
2025. Они и есть проверка: правило, придуманное в вакууме, на них рассыпется.
У каждой группы совпадений есть группа НЕсовпадений на тех же данных, потому
что схлопывание букв (x/h, q/k, j/i) умеет склеить два разных хозяйства, и
проверка, которая ловит только совпадения, этого не заметит.

Отдельно проверяется средняя полоса. «Норов Фахри фх» против «Norov Bek 2» --
общее слово есть, различающего нет; выдать такую пару за совпадение значит
записать работу чужому хозяйству. Она обязана попасть в кандидаты, а не в
совпадения.
"""

import os
import sqlite3
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tools import drone_books_vs_contours as report  # noqa: E402
from tools.drone_name_match import (  # noqa: E402
    SCORE_CANDIDATE,
    SCORE_CONFIDENT,
    levenshtein,
    name_key,
    name_tokens,
    score,
    translit,
)

# Настоящие пары из данных сентября 2025.
REAL_MATCHES = (
    ('Аваз Исматов фх', 'avaz ismatov 1'),
    ('Аваз Исматов фх', 'avazismatov3'),
    ('Чинор фх', 'Chinor fermer xojaligi 3.8'),
    ('Абдукодиробод фх', 'abduqodir obod'),
    ('Баронхужа 2022 фх', "baronho'ja 2022"),
    ('Бехруз Т фх', 'behruzT1,3'),
    ('Шерзод Сохиб фх', 'sherzodsohib2,5'),
    ('Рустам Карим фх', 'Rustam Karim 3'),
    ('Ғиждувон ПТЗ', "G'ijduvon ptz"),
    ('Сарвари ПТЗ', 'ptz sarvari 5'),
    ('Умид Агро Имконият фх', 'Umid Agro Imkoniyat (8.9 ga)'),
    ('Когон Порлок Келажак', 'Qogon porloq kelajak'),
    ('Нурхур фх', 'nurxur2'),
)

REAL_NON_MATCHES = (
    ('Аваз Исматов фх', 'Rustam Karim 3'),
    ('Чинор фх', 'zaminlari'),
    ('Бехруз Т фх', 'behzod 5'),
    ('Замур агро фх', 'Umid Agro Imkoniyat (8.9 ga)'),
    ('Зарафшон Жайрони фх', 'Zarangari 2'),
)

# Общее слово есть, различающего нет -- полоса «нужен глаз».
NEEDS_A_HUMAN = (
    ('Норов Фахри фх', 'Norov Bek 2'),
    ('Пешку Сервис ери', 'Servis 4'),
    ('Бухоро Агрокластер Заминлари', 'zaminlari2'),
)


class Transliteration(unittest.TestCase):
    """Проверка 1: кириллица приводится к латинице узбекскими правилами."""

    def test_1_uzbek_letters(self):
        self.assertEqual(translit('Ғиждувон'), "g'ijduvon")
        self.assertEqual(translit('Қўшработ'), "qo'shrabot")
        self.assertEqual(translit('Ҳамро'), 'hamro')
        self.assertEqual(translit('Жумаев'), 'jumaev')

    def test_1a_latin_passes_through_unchanged(self):
        self.assertEqual(translit('avaz ismatov 1'), 'avaz ismatov 1')

    def test_1b_negative_an_unmapped_letter_is_not_dropped_silently(self):
        """Буква, которой нет в таблице, обязана дойти до ключа как есть --
        иначе незнакомое написание тихо превратится в чужое."""
        self.assertIn('ђ', translit('ђaba'))


class Keys(unittest.TestCase):
    """Проверка 2: ключ гасит различия, которые не значат ничего."""

    def test_2_spaces_digits_and_apostrophes_do_not_matter(self):
        self.assertEqual(name_key('avaz ismatov 1'), name_key('avazismatov3'))
        self.assertEqual(name_key("baronho'ja"), name_key('baronhoja'))

    def test_2a_noise_words_are_dropped(self):
        self.assertEqual(name_key('Чинор фх'),
                         name_key('Chinor fermer xojaligi 3.8'))

    def test_2b_negative_different_farms_keep_different_keys(self):
        """Иначе проверки выше прошли бы и на ключе, равном пустой строке."""
        self.assertNotEqual(name_key('Аваз Исматов'), name_key('Рустам Карим'))
        self.assertNotEqual(name_key('Чинор'), name_key('Заминлари'))

    def test_2c_the_key_is_not_empty_for_a_real_name(self):
        self.assertTrue(name_key('Аваз Исматов фх'))

    def test_2d_a_name_of_only_noise_has_no_key(self):
        self.assertEqual(name_key('фх'), '')
        self.assertEqual(name_tokens('фх'), [])


class Levenshtein(unittest.TestCase):
    """Проверка 3: расстояние считается верно и обрывается на пороге."""

    def test_3_known_distances(self):
        self.assertEqual(levenshtein('zaminlari', 'zaminlari'), 0)
        self.assertEqual(levenshtein('zaminlari', 'zamjnlari'), 1)
        self.assertEqual(levenshtein('kot', 'skat'), 2)

    def test_3a_negative_far_strings_report_above_the_cap(self):
        self.assertGreater(levenshtein('avazismatov', 'rustamkarim', cap=2), 2)

    def test_3b_a_length_gap_beyond_the_cap_exits_early(self):
        self.assertGreater(levenshtein('ab', 'abcdefgh', cap=2), 2)


class Scoring(unittest.TestCase):
    """Проверка 4: настоящие пары сентября 2025."""

    def test_4_every_real_pair_is_a_confident_match(self):
        for book, contour in REAL_MATCHES:
            value, why = score(book, contour)
            self.assertGreaterEqual(
                value, SCORE_CONFIDENT,
                '%r <-> %r дало %.2f (%s)' % (book, contour, value, why))

    def test_4a_negative_no_unrelated_pair_is_a_match(self):
        for book, contour in REAL_NON_MATCHES:
            value, why = score(book, contour)
            self.assertLess(
                value, SCORE_CONFIDENT,
                '%r <-> %r ложно совпало: %.2f (%s)'
                % (book, contour, value, why))

    def test_4b_the_middle_band_is_neither_accepted_nor_hidden(self):
        """[REASON]: у этих пар есть общее слово и нет различающего. И тихо
        принять, и тихо выбросить -- одинаково неверно: первое пишет работу
        чужому хозяйству, второе прячет её от глаз."""
        for book, contour in NEEDS_A_HUMAN:
            value, _why = score(book, contour)
            self.assertGreaterEqual(value, SCORE_CANDIDATE, (book, contour))
            self.assertLess(value, SCORE_CONFIDENT, (book, contour))

    def test_4c_every_verdict_carries_a_reason(self):
        for book, contour in REAL_MATCHES + REAL_NON_MATCHES + NEEDS_A_HUMAN:
            _value, why = score(book, contour)
            self.assertTrue(why.strip(), (book, contour))

    def test_4d_an_empty_name_matches_nothing(self):
        self.assertEqual(score('', 'avaz ismatov')[0], 0.0)
        self.assertEqual(score('Аваз Исматов', '')[0], 0.0)

    def test_4e_negative_a_short_common_word_alone_is_not_a_match(self):
        """«ПТЗ» есть у половины контуров Ғиждувона и не доказывает ничего."""
        value, _why = score('ПТЗ', 'ptz sarvari 5')
        self.assertLess(value, SCORE_CONFIDENT)


SCHEMA = """
CREATE TABLE field_contours (
    id INTEGER PRIMARY KEY AUTOINCREMENT, source VARCHAR(20) NOT NULL,
    external_id VARCHAR(100), name VARCHAR(300) NOT NULL, area_ha FLOAT,
    serial_number VARCHAR(50), source_created_at DATETIME);
CREATE TABLE drone_customers (id INTEGER PRIMARY KEY, name VARCHAR(300));
CREATE TABLE drone_operators (id INTEGER PRIMARY KEY, full_name VARCHAR(200));
CREATE TABLE drone_works (
    id INTEGER PRIMARY KEY, period_month VARCHAR(7) NOT NULL,
    work_date_from DATE, work_date_to DATE, date_raw VARCHAR(100),
    drone_operator_id INTEGER, operator_raw VARCHAR(200),
    drone_customer_id INTEGER, customer_raw VARCHAR(300) NOT NULL,
    area_ha NUMERIC NOT NULL, source_file VARCHAR(200),
    source_sheet VARCHAR(200));
"""


class Fixture(object):

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix='bvc_')
        self.path = os.path.join(self.dir, 'transport.db')
        self.con = sqlite3.connect(self.path)
        self.con.executescript(SCHEMA)
        self.con.execute("INSERT INTO drone_operators (id, full_name) "
                         "VALUES (1, 'Холмуродов Шахзод')")
        self.con.commit()
        self._cid = 0

    def contour(self, name, area, created, serial='P1'):
        self._cid += 1
        self.con.execute(
            'INSERT INTO field_contours (source, external_id, name, area_ha, '
            "serial_number, source_created_at) VALUES ('dji', ?, ?, ?, ?, ?)",
            ('u%d' % self._cid, name, area, serial, created))
        self.con.commit()

    def work(self, customer, area, month='2025-09', date_from='2025-09-10',
             wid=None):
        self.con.execute(
            'INSERT INTO drone_works (id, period_month, work_date_from, '
            'work_date_to, drone_operator_id, customer_raw, area_ha, '
            'source_file, source_sheet) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)',
            (wid, month, date_from, date_from, customer, area,
             'книга.xlsx', 'свод ичи'))
        self.con.commit()

    def run(self, month='2025-09'):
        conn = report.open_readonly(self.path)
        try:
            return report.build(conn, month)
        finally:
            conn.close()

    def close(self):
        self.con.close()
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)


class Report(unittest.TestCase):
    """Проверка 5: сведение месяца, все четыре корзины."""

    def setUp(self):
        self.fx = Fixture()

    def tearDown(self):
        self.fx.close()

    def test_5_a_matching_pair_lands_in_matched(self):
        self.fx.contour('avaz ismatov 1', 4.94, '2025-09-10T05:00:00')
        self.fx.work('Аваз Исматов фх', 18.0)
        stats, matched, candidates, book_only, contour_only = self.fx.run()
        self.assertEqual(stats['matched'], 1)
        self.assertEqual((len(candidates), len(book_only), len(contour_only)),
                         (0, 0, 0))
        self.assertEqual(matched[0][0]['customer_raw'], 'Аваз Исматов фх')

    def test_5a_negative_an_unrelated_contour_does_not_match(self):
        self.fx.contour('Rustam Karim 3', 3.04, '2025-09-10T05:00:00')
        self.fx.work('Аваз Исматов фх', 18.0)
        stats, _m, _c, book_only, contour_only = self.fx.run()
        self.assertEqual(stats['matched'], 0)
        self.assertEqual(len(book_only), 1)
        self.assertEqual(len(contour_only), 1)

    def test_5b_several_contours_of_one_customer_are_all_kept(self):
        """У Аваза Исматова их пять; «лучший один» потерял бы четыре."""
        for index, area in enumerate((8.21, 3.67, 2.27), start=1):
            self.fx.contour('avazismatov%d' % index, area,
                            '2025-09-10T05:00:00', serial='P%d' % index)
        self.fx.work('Аваз Исматов фх', 18.0)
        _stats, matched, _c, _b, contour_only = self.fx.run()
        self.assertEqual(len(matched[0][1]), 3)
        self.assertAlmostEqual(matched[0][2], 14.15, places=2)
        self.assertEqual(contour_only, [])

    def test_5c_the_middle_band_goes_to_candidates_not_to_matched(self):
        self.fx.contour('Norov Bek 2', 5.0, '2025-09-10T05:00:00')
        self.fx.work('Норов Фахри фх', 6.0)
        stats, matched, candidates, book_only, contour_only = self.fx.run()
        self.assertEqual((stats['matched'], len(matched)), (0, 0))
        self.assertEqual(len(candidates), 1)
        self.assertEqual(book_only, [])
        # Контур не был отдан книге, значит остаётся в «без книги».
        self.assertEqual(len(contour_only), 1)

    def test_5d_a_contour_with_no_book_row_is_reported(self):
        """Это и есть работа, которую диспетчер не записал."""
        self.fx.contour('nurxur2', 7.5, '2025-09-12T05:00:00')
        _stats, _m, _c, _b, contour_only = self.fx.run()
        self.assertEqual([c['name'] for c in contour_only], ['nurxur2'])

    def test_5e_contours_of_another_month_are_out_of_scope(self):
        self.fx.contour('avaz ismatov 1', 4.94, '2025-10-10T05:00:00')
        self.fx.work('Аваз Исматов фх', 18.0)
        stats, _m, _c, book_only, contour_only = self.fx.run('2025-09')
        self.assertEqual(stats['contours_month'], 0)
        self.assertEqual(len(book_only), 1)
        self.assertEqual(contour_only, [])

    def test_5f_the_day_is_read_in_utc_plus_five(self):
        """[REASON]: контур, созданный 30.09 в 20:00 UTC, по Ташкенту уже
        октябрьский. Без сдвига он попал бы в сентябрь и потянул за собой
        чужую работу."""
        self.fx.contour('поздний', 1.0, '2025-09-30T20:00:00')
        stats_sept, _m, _c, _b, _co = self.fx.run('2025-09')
        stats_oct, _m2, _c2, _b2, _co2 = self.fx.run('2025-10')
        self.assertEqual(stats_sept['contours_month'], 0)
        self.assertEqual(stats_oct['contours_month'], 1)

    def test_5g_negative_an_early_contour_stays_in_its_own_month(self):
        """Обратный контроль к предыдущей: сдвиг не тащит всё подряд вперёд."""
        self.fx.contour('ранний', 1.0, '2025-09-30T10:00:00')
        stats_sept, _m, _c, _b, _co = self.fx.run('2025-09')
        self.assertEqual(stats_sept['contours_month'], 1)

    def test_5h_counters_partition_the_book_rows(self):
        self.fx.contour('avaz ismatov 1', 4.94, '2025-09-10T05:00:00')
        self.fx.contour('Norov Bek 2', 5.0, '2025-09-11T05:00:00')
        self.fx.work('Аваз Исматов фх', 18.0, wid=1)
        self.fx.work('Норов Фахри фх', 6.0, wid=2)
        self.fx.work('Совсем Другое фх', 3.0, wid=3)
        stats, _m, _c, _b, _co = self.fx.run()
        self.assertEqual(stats['books'],
                         stats['matched'] + stats['candidates']
                         + stats['book_only'])


class ReadOnly(unittest.TestCase):
    """Проверка 6: инструмент не может изменить базу."""

    def setUp(self):
        self.fx = Fixture()

    def tearDown(self):
        self.fx.close()

    def test_6_the_connection_refuses_a_write(self):
        conn = report.open_readonly(self.fx.path)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("INSERT INTO drone_operators (id, full_name) "
                             "VALUES (99, 'x')")
        finally:
            conn.close()

    def test_6a_negative_the_same_write_works_on_a_writable_handle(self):
        con = sqlite3.connect(self.fx.path)
        try:
            con.execute("INSERT INTO drone_operators (id, full_name) "
                        "VALUES (99, 'x')")
            con.commit()
        finally:
            con.close()

    def test_6b_a_missing_database_exits_2_and_creates_no_file(self):
        missing = os.path.join(self.fx.dir, 'nope.db')
        with self.assertRaises(SystemExit) as caught:
            report.open_readonly(missing)
        self.assertEqual(caught.exception.code, 2)
        self.assertFalse(os.path.exists(missing))


if __name__ == '__main__':
    unittest.main()
