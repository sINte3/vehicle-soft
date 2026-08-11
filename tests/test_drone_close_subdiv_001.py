# -*- coding: utf-8 -*-
"""DRONE-CLOSE-SUBDIV-001: подразделение выводится из книги, а не выдумывается.

Три слоя. Токены: различающие слова считаются из справочника парка, слово из
двух названий не различает никого — «Бухоро Агрокластер» не даёт ни одного
токена, и книга «Агрокластер …» правильно остаётся без ответа. Бэкфилл: шаг 3
(единственное подразделение опознанных операторов книги) сильнее шага 4 (имя
файла), который включается только при полном отсутствии опознанных операторов;
существующее значение неперезаписываемо. Инвариант: после бэкфилла каждый
месяц экрана закрытия по-прежнему равен сверке по всем четырём числам —
переезд работы между корзинами не имеет права сдвинуть итог. Это тест,
который ловит бэкфилл, теряющий строки.

Run:
  python -m unittest tests.test_drone_close_subdiv_001 -v
"""
import datetime
import os
import sys
import tempfile
import unittest

from tests.harness import app, create_org, reset_db, TEST_DB_PATH
from models import db, DroneFlight, DroneOperator, DroneUnit, DroneWork

import drones

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, 'tools'))
import import_drone_works as importer  # noqa: E402
import backfill_drone_works_subdivision as backfill  # noqa: E402

# Семь названий парка — фикстура теста, единственное место, где имена
# подразделений набраны руками (промптом это разрешено только здесь).
FLEET7 = [
    'Бухоро Агрокластер',
    'Бухоро Сервис Агрокластер',
    'Гарден Бухоро Агрокластер',
    'Когон ПТЗ',
    'Пешку ПТЗ',
    'Шофиркон ПТЗ',
    'Ғиждувон ПТЗ',
]
SERVIS = 'Бухоро Сервис Агрокластер'
GARDEN = 'Гарден Бухоро Агрокластер'
KOGON = 'Когон ПТЗ'

BOOK_STEP3 = 'Сервис Дрон маълумот АПРЕЛЬ.xlsx'
BOOK_SPAN2 = 'Сервис Дрон Маълумот (2).xlsx'
BOOK_STEP4 = 'Гарден Дрон маълумот Апрель.xlsx'
BOOK_NEVER = 'Агрокластер Дрон маълумот МАЙ.xlsx'


def directory_of(subdivisions):
    """Directory без базы: только множество подразделений, как у токенов."""
    d = importer.Directory.__new__(importer.Directory)
    d.subdivisions = set(subdivisions)
    return d


# ─── 1. Токены ───────────────────────────────────────────────────────────────

class TokenDerivationTests(unittest.TestCase):

    def test_fleet_of_seven_yields_exactly_six_tokens(self):
        tokens = importer.Directory.discriminating_tokens(FLEET7)
        expected = {importer.uz_fold(w) for w in
                    ('Сервис', 'Гарден', 'Когон', 'Пешку',
                     'Шофиркон', 'Ғиждувон')}
        self.assertEqual(set(tokens), expected)

    def test_bukhoro_agrocluster_yields_no_token(self):
        tokens = importer.Directory.discriminating_tokens(FLEET7)
        words = set(importer.Directory._split_words(
            importer.uz_fold('Бухоро Агрокластер')))
        self.assertTrue(words, 'контроль: слова вообще нарезались')
        self.assertFalse(words & set(tokens))

    def test_every_token_points_at_its_own_subdivision(self):
        tokens = importer.Directory.discriminating_tokens(FLEET7)
        for word, name in tokens.items():
            self.assertIn(word,
                          importer.Directory._split_words(
                              importer.uz_fold(name)),
                          '%s не из %s' % (word, name))

    def test_garden_agrocluster_book_resolves_by_garden_alone(self):
        # «Агрокластер» в имени НЕ свидетельство: правило, считающее его
        # таковым, ошиблось бы в другую сторону (§7 промпта).
        d = directory_of(FLEET7)
        self.assertEqual(
            d.subdivision_for_file('Гарден Агрокластер Дрон Октябрь.xlsx'),
            GARDEN)

    def test_agrocluster_book_stays_unresolved(self):
        d = directory_of(FLEET7)
        self.assertIsNone(d.subdivision_for_file(BOOK_NEVER))

    def test_tokens_of_two_subdivisions_refuse(self):
        d = directory_of(FLEET7)
        self.assertIsNone(
            d.subdivision_for_file('Гарден ва Сервис Дрон маълумот.xlsx'))

    def test_whole_name_pass_still_runs_first_and_wins(self):
        d = directory_of(FLEET7)
        self.assertEqual(
            d.subdivision_for_file('Дрон Ғиждувон ПТЗ Апрель.xlsx'),
            'Ғиждувон ПТЗ')

    def test_underscored_names_split_into_words(self):
        d = directory_of(FLEET7)
        self.assertEqual(
            d.subdivision_for_file('Ғиждувон_ПТЗ_Дрон_маълумот_Октябрь.xlsx'),
            'Ғиждувон ПТЗ')

    def test_a_new_subdivision_changes_the_tokens_with_no_code(self):
        grown = FLEET7 + ['Мирзачўл Агро']
        tokens = importer.Directory.discriminating_tokens(grown)
        self.assertIn(importer.uz_fold('Мирзачўл'), tokens)


# ─── 2. Бэкфилл ─────────────────────────────────────────────────────────────

def build_backfill_fixture():
    """Четыре книги, каждая — свой исход; плюс неприкосновенная строка."""
    reset_db()
    ids = {}
    org = create_org('Бухоро Агрокластер')
    with app.app_context():
        # Подразделения попадают в Directory через операторов и машины.
        db.session.add_all(
            [DroneUnit(number=i, organization_id=org, subdivision_name=s)
             for i, s in enumerate(FLEET7, 1)])
        op_servis = DroneOperator(full_name='Оператор Сервис',
                                  subdivision_name=SERVIS)
        op_kogon = DroneOperator(full_name='Оператор Когон',
                                 subdivision_name=KOGON)
        db.session.add_all([op_servis, op_kogon])
        db.session.commit()

        counter = [0]

        def work(book, operator=None, subdivision=None, area=10.0,
                 month='2026-04'):
            counter[0] += 1
            row = DroneWork(period_month=month, drone_operator_id=operator,
                            subdivision_name=subdivision,
                            customer_raw='ФХ %d' % counter[0], area_ha=area,
                            payment_type='cash', source_file=book,
                            source_sheet='свод ичи', source_row=counter[0])
            db.session.add(row)
            db.session.flush()
            return row.id

        # Книга step 3: операторы одного подразделения + две сироты.
        work(BOOK_STEP3, operator=op_servis.id)
        ids['step3_a'] = work(BOOK_STEP3, area=7.0)
        ids['step3_b'] = work(BOOK_STEP3, area=8.0)
        # Книга span-2: операторы ДВУХ подразделений + сирота. «Сервис» в
        # имени есть, но шаг 4 включаться не имеет права.
        work(BOOK_SPAN2, operator=op_servis.id)
        work(BOOK_SPAN2, operator=op_kogon.id)
        ids['span2'] = work(BOOK_SPAN2, area=5.0)
        # Книга step 4: ни одного оператора.
        ids['step4'] = work(BOOK_STEP4, area=6.0)
        # Книга-неответ: ни операторов, ни различающего слова.
        ids['never'] = work(BOOK_NEVER, area=9.0)
        # Уже заполненное подразделение — трогать нельзя.
        ids['prefilled'] = work(BOOK_STEP4, subdivision=KOGON, area=4.0)
        db.session.commit()
    return ids


def run_backfill(*extra):
    report = os.path.join(tempfile.gettempdir(),
                          'test_close_subdiv_report.txt')
    code = backfill.main(['--db', TEST_DB_PATH, '--report', report]
                         + list(extra))
    with open(report, encoding='utf-8') as fh:
        return code, fh.read()


def subdivision_of(work_id):
    with app.app_context():
        db.session.remove()
        return db.session.get(DroneWork, work_id).subdivision_name


class BackfillStepTests(unittest.TestCase):

    def setUp(self):
        self.ids = build_backfill_fixture()

    def test_dry_run_is_the_default_and_writes_nothing(self):
        code, report = run_backfill()
        self.assertEqual(code, 0)
        self.assertIn('DRY RUN', report)
        for key in ('step3_a', 'step3_b', 'step4', 'span2', 'never'):
            self.assertIsNone(subdivision_of(self.ids[key]), key)

    def test_apply_inherits_the_single_subdivision_of_the_book(self):
        code, _ = run_backfill('--apply')
        self.assertEqual(code, 0)
        self.assertEqual(subdivision_of(self.ids['step3_a']), SERVIS)
        self.assertEqual(subdivision_of(self.ids['step3_b']), SERVIS)

    def test_step4_fires_only_when_step3_found_nothing(self):
        run_backfill('--apply')
        # Ни одного оператора в книге -> имя файла отвечает.
        self.assertEqual(subdivision_of(self.ids['step4']), GARDEN)
        # Операторы из двух подразделений -> отказ, БЕЗ падения в шаг 4.
        self.assertIsNone(subdivision_of(self.ids['span2']))

    def test_the_span2_refusal_is_stated_in_the_report(self):
        _, report = run_backfill('--apply')
        self.assertIn('operators span 2 subdivisions', report)

    def test_undecidable_file_name_stays_unresolved(self):
        run_backfill('--apply')
        self.assertIsNone(subdivision_of(self.ids['never']))

    def test_a_work_with_a_subdivision_is_never_touched(self):
        run_backfill('--apply')
        self.assertEqual(subdivision_of(self.ids['prefilled']), KOGON)

    def test_changed_ids_are_listed_for_mechanical_reversal(self):
        _, report = run_backfill('--apply')
        for key in ('step3_a', 'step3_b', 'step4'):
            self.assertIn('%d -> ' % self.ids[key], report)

    def test_second_apply_changes_zero_rows(self):
        run_backfill('--apply')
        _, report = run_backfill('--apply')
        self.assertIn('ЗАПИСАНО: 0', report)


# ─── 3. Инвариант сверки после бэкфилла ─────────────────────────────────────

class InvariantAfterBackfillTests(unittest.TestCase):
    """Переезд работ между корзинами не имеет права сдвинуть итог месяца."""

    def setUp(self):
        self.ids = build_backfill_fixture()
        with app.app_context():
            unit = DroneUnit.query.filter_by(number=4).one()  # Когон ПТЗ
            db.session.add_all([
                DroneFlight(dji_flight_id=910001, drone_unit_id=unit.id,
                            nickname_raw='fixture',
                            started_at=datetime.datetime(2026, 4, 5, 3),
                            area_ha=30.0, raw_json='{}'),
                DroneFlight(dji_flight_id=910002, drone_unit_id=None,
                            nickname_raw='fixture',
                            started_at=datetime.datetime(2026, 4, 6, 3),
                            area_ha=11.0, raw_json='{}'),
            ])
            db.session.commit()

    def closing(self):
        with app.app_context():
            db.session.remove()
            with app.test_request_context('/drones/reports/closing'):
                return drones._drone_closing_data(0)

    def reconcile(self):
        with app.app_context():
            db.session.remove()
            with app.test_request_context('/drones/reports/reconcile'):
                return drones._drone_works_flights_reconcile_data()

    def assert_invariant(self, closing):
        reference = dict((row['month'], row)
                         for row in self.reconcile()['rows'])
        self.assertEqual(sorted(closing['months']), sorted(reference))
        for month in closing['months']:
            mine = closing['month_totals'][month]
            theirs = reference[month]
            self.assertEqual(mine['ledger_jobs'], theirs['ledger_jobs'])
            self.assertAlmostEqual(mine['ledger_area'],
                                   theirs['ledger_area'], places=6)
            self.assertEqual(mine['flights'], theirs['flights'])
            self.assertAlmostEqual(mine['flight_area'],
                                   theirs['flight_area'], places=6)

    def test_invariant_holds_before_and_after_and_totals_do_not_move(self):
        before = self.closing()
        self.assert_invariant(before)
        run_backfill('--apply')
        after = self.closing()
        self.assert_invariant(after)
        self.assertEqual(sorted(before['months']), sorted(after['months']))
        for month in before['months']:
            for field in ('ledger_jobs', 'ledger_area', 'flights',
                          'flight_area'):
                self.assertAlmostEqual(before['month_totals'][month][field],
                                       after['month_totals'][month][field],
                                       places=6,
                                       msg='%s %s сдвинулся' % (month, field))

    def test_bucket_rows_carry_the_explanatory_note_and_others_do_not(self):
        """Обе корзины несут строку-пояснение; обычная строка — нет.

        [REASON]: «НЕ СДАНА» у корзины прочиталась как обвинение
        подразделения, чья книга сдана. Подпись — часть смысла экрана.
        """
        data = self.closing()
        by_key = {row['key']: row for row in data['rows']}
        self.assertTrue(by_key[drones.DRONE_CLOSING_SUBDIVISION_UNKNOWN]
                        ['bucket_note'])
        self.assertTrue(by_key[drones.DRONE_CLOSING_NO_MACHINE]
                        ['bucket_note'])
        self.assertIn(KOGON, by_key, 'контроль: обычная строка есть')
        self.assertIsNone(by_key[KOGON]['bucket_note'])

    def test_the_moved_works_left_the_bucket_differentially(self):
        """Не «корзина пуста», а «строки переехали, счёт сошёлся».

        Поиск по row['key'], не по метке: метка двуязычна и зависит от
        языка контекста, а ключ — нет.
        """
        bucket_key = drones.DRONE_CLOSING_SUBDIVISION_UNKNOWN

        def bucket_and_servis(data):
            take = {bucket_key: 0.0, SERVIS: 0.0}
            for row in data['rows']:
                if row['key'] in take:
                    take[row['key']] += row['total']['ledger_area']
            return take

        before = bucket_and_servis(self.closing())
        # Контроль: корзина ДО бэкфилла не пуста — иначе разность 0 - 0
        # прошла бы и при неработающем поиске строки.
        self.assertGreater(before[bucket_key], 20.0)
        run_backfill('--apply')
        after = bucket_and_servis(self.closing())
        moved_to_servis = 15.0   # step3_a 7.0 + step3_b 8.0
        moved_total = 21.0       # + step4 6.0 в Гарден
        self.assertAlmostEqual(before[bucket_key] - after[bucket_key],
                               moved_total, places=6)
        self.assertAlmostEqual(after[SERVIS] - before[SERVIS],
                               moved_to_servis, places=6)


if __name__ == '__main__':
    unittest.main(verbosity=2)
