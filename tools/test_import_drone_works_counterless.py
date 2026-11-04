#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FIN-RECON-003 (03.11.2026): a job without «№», fail-closed reading, and
the «Когон Фукаро» hold.

The July 2026 book «Дрон Ғиждувон ПТЗ Июль.xlsx / свод ичи (Усмон)» carries
nineteen jobs with an empty «№» cell. The importer demanded the counter and
dropped them with a bare `continue` -- 189.25 ha, and «rows rejected: 0».
Pinned here:

  PARSING  a counterless row is a job on positive evidence only (farm, area
           in range, and a date, price or amount); a totals line, a wage
           block, a header, the template and the junk under the totals are
           never jobs; the counter is not part of any identity.
  FAIL-CLOSED  a row with job data and no rule is UNPARSED_CANDIDATE, every
           non-empty row lands in exactly one class, and apply_rows()
           refuses while one exists -- nothing written.
  SCHEMA   a database without a column: the dry run reads the books and says
           APPLY_PERMITTED: NO; --apply refuses as before; the file is
           unchanged.
  PAYER    «Когон Фукаро» never becomes a card by itself; an alias resolves
           it; «Ахмад (фукаро)» is still a named citizen with a card; the
           owner's decision is simulated, never applied.

Negative controls: the old acceptance rule loses the nineteen rows again; an
empty PAYER_DECISION_PENDING_KEYS makes the card again; the reconciliation
of the five real books fails when the nineteen are removed.

The five real July books are read only when FIN_RECON_003_JULY_BOOKS points
at a folder holding them; their SHA-256 is checked first.

Run: python -X utf8 tools/test_import_drone_works_counterless.py -v
"""
import contextlib
import datetime
import hashlib
import io
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for directory in (HERE, ROOT):
    if directory not in sys.path:
        sys.path.insert(0, directory)

import drone_works_fixtures as fx  # noqa: E402
import import_drone_works as imp  # noqa: E402

# The July layout, exactly as «свод ичи (Усмон)» writes it.
HEADER = ('№', 'ФХ номи ', 'Майдон (га)', 'Хизмат кўрсатиш суммаси',
          'Жами сумма\n(обьём)', 'Бошка харажатлар ',
          'Оператор топшириши керак ', 'Дрон \nишлаган сана', '*Изоҳ')
BOOK = 'Дрон Синов ПТЗ Июль.xlsx'
SHEET = 'свод ичи (Усмон)'


def d(day):
    return datetime.datetime(2026, 7, day)


def job(counter, farm, area, price, day, note=None):
    amount = None if price is None else round(area * price, 2)
    return (counter, farm, area, price, amount, None, None,
            d(day) if day else None, note)


class SyntheticSheet(object):
    def __init__(self, rows, title=SHEET):
        self.rows = rows
        self.title = title

    def iter_rows(self, values_only=False):
        return iter(self.rows)


def parse(rows, title=SHEET, formula_cells=None):
    return imp.parse_sheet(SyntheticSheet(rows, title), BOOK, title,
                           '2026-07', None, formula_cells=formula_cells)


def usmon_like():
    """The Usmon sheet in miniature: both defects, both totals, the tail."""
    return [
        ('"Ғиждувон пахта тозалаш" АЖга қарашли Дронлар',),
        (None, None, None, None, None, None, None, None,
         'Синов Оператор +998 00 000 00 00'),
        ('Справка (Июнь ойи)',),
        HEADER,
        job(1, 'Заминлари', 18.7, 85633, 4),
        job(2, 'Гарден', 13.53, 85633, 10),
        job(None, 'Гарден', 13.53, 85633, 11),          # row 7: no «№»
        job(3, 'Сарвари х.', 3.26, 85633, 16),
        (None, 'Жами сумма:  ', 49.02, None, 4197729.0, 0, 0),
        ('Нақд (Июль ойи)',),
        HEADER,
        job(1, 'Темурбек порлок', 3.27, 200000, 1),
        job(2, 'Окил омад рамз', 11.9, 200000, 3),
        job(None, 'Кулодина тонги', 19.9, 200000, 19),  # row 14
        job(None, 'Фукаро', 3.0, 200000, 30),           # row 15
        job(None, 'Жамила фх', 5.0, 200000, 31),        # row 16: a farm
        (None, 'Жами сумма:', 43.07, None, None, None, None),
        (None, None, None, None, None, None, 3110000),  # junk under it
        (None, 'Жами га:', 92.09),
        (None, 'Жами иш хаки:', 'справка'),
        (None, None, 'накд'),
        (None, None, 'ИТОГО:', 0),
    ]


# ─── Parsing ─────────────────────────────────────────────────────────────────

class CounterlessParseTests(unittest.TestCase):

    def setUp(self):
        self.rows, self.report = parse(usmon_like())
        self.by_row = {r['source_row']: r for r in self.rows}

    def test_blank_counter_on_a_valid_job_is_accepted_and_flagged(self):
        row = self.by_row[7]
        self.assertEqual(row['customer_raw'], 'Гарден')
        self.assertEqual(row['area_ha'], 13.53)
        self.assertEqual(row['work_date_from'], datetime.date(2026, 7, 11))
        self.assertTrue(row['counter_missing'])
        self.assertIsNone(row['row_counter'])
        self.assertFalse(self.by_row[6]['counter_missing'])
        self.assertEqual(self.by_row[6]['row_counter'], 2)
        self.assertEqual(self.report.counterless_rows, 4)
        self.assertEqual(self.report.row_classes['job_no_number'], 4)
        self.assertEqual(self.report.row_classes['job'], 5)

    def test_blank_counter_on_a_totals_row_is_not_a_job(self):
        self.assertNotIn(9, self.by_row)
        self.assertNotIn(17, self.by_row)
        self.assertNotIn(19, self.by_row)
        self.assertNotIn(22, self.by_row)            # «ИТОГО:» in the area column
        self.assertEqual(self.report.row_classes['totals'], 4)
        checks = {c[0]: c for c in self.report.total_checks}
        self.assertEqual(checks[9][-1], 'block')
        self.assertEqual(checks[17][-1], 'block')
        self.assertEqual(checks[19][-1], 'sheet')
        self.assertAlmostEqual(checks[19][4], 92.09, places=2)

    def test_a_farm_whose_name_starts_like_a_total_is_a_farm(self):
        self.assertEqual(self.by_row[16]['customer_raw'], 'Жамила фх')
        self.assertEqual(imp.total_label(
            (None, 'Жамила фх', 5.0), imp.map_columns(HEADER)), '')
        self.assertTrue(imp.total_label(
            (None, 'ЖАМИ:', 5.0), imp.map_columns(HEADER)))

    def test_rows_after_a_totals_row_are_read_in_the_next_section(self):
        self.assertEqual(self.by_row[14]['payment_type'], imp.PAYMENT_CASH)
        self.assertEqual(self.by_row[7]['payment_type'], imp.PAYMENT_TRANSFER)
        self.assertEqual(sorted(self.by_row),
                         [5, 6, 7, 8, 12, 13, 14, 15, 16])

    def test_the_wage_tail_and_the_junk_under_the_totals_are_not_jobs(self):
        self.assertEqual(self.report.row_classes['no_job_data'], 1)  # 3110000
        self.assertEqual(self.report.row_classes['block_marker'], 4)
        self.assertEqual(self.report.unparsed, [])
        self.assertEqual(self.report.rejections, [])

    def test_every_non_empty_row_lands_in_exactly_one_class(self):
        non_empty = sum(1 for cells in usmon_like()
                        if any(imp._text(c) for c in cells))
        self.assertEqual(sum(self.report.row_classes.values()), non_empty)

    def test_the_counter_is_not_part_of_the_identity(self):
        a, b = self.by_row[6], self.by_row[7]
        # Same farm, area, price; the day differs -- two jobs.
        self.assertNotEqual(imp.duplicate_key(a), imp.duplicate_key(b))
        same_day = dict(b, row_counter=2, counter_missing=False)
        self.assertEqual(imp.duplicate_key(same_day), imp.duplicate_key(b))
        # Provenance is the physical row and nothing else.
        self.assertEqual((b['source_file'], b['source_sheet'],
                          b['source_row']), (BOOK, SHEET, 7))

    def test_negative_control_the_old_rule_loses_the_rows_again(self):
        def old_rule(cells, mapping, area):
            return False, 'counter required (the rule before 03.11.2026)'
        with mock.patch.object(imp, 'counterless_job_evidence', old_rule):
            rows, report = parse(usmon_like())
        self.assertEqual(len(rows), 5)
        self.assertNotIn(7, {r['source_row'] for r in rows})
        # ... and they do not vanish: the bridge names each one.
        self.assertEqual(len(report.unparsed), 4)
        self.assertEqual({u[0] for u in report.unparsed}, {7, 14, 15, 16})


class FailClosedParseTests(unittest.TestCase):

    def test_farm_and_area_only_is_unparsed(self):
        rows, report = parse([
            ('Нақд (Июль ойи)',), HEADER,
            job(1, 'Бор фх', 4.0, 200000, 2),
            (None, 'Номаълум фх', 7.0),
        ])
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(report.unparsed), 1)
        excel_row, reason, _cells = report.unparsed[0]
        self.assertEqual(excel_row, 4)
        self.assertIn('farm and area only', reason)

    def test_a_numbered_row_without_an_area_is_unparsed(self):
        rows, report = parse([
            ('Нақд (Июль ойи)',), HEADER,
            (5, 'Фх бир', None, 200000, 1000000, None, None, d(3)),
            (6, 'Фх икки', 'ўн уч', 200000, 2700000, None, None, d(4)),
        ])
        self.assertEqual(rows, [])
        reasons = dict((r, why) for r, why, _c in report.unparsed)
        self.assertEqual(reasons[3], 'numbered row without an area')
        self.assertIn('area cell is not a number (ўн уч)', reasons[4])

    def test_the_empty_template_is_not_a_candidate(self):
        rows, report = parse([
            ('Нақд  (Июль ойи)',), HEADER,
            (1,), (2,), (3,),
            (None, 'Жами сумма:  ', 0, None, 0, 0, 0),
        ])
        self.assertEqual(rows, [])
        self.assertEqual(report.unparsed, [])
        self.assertEqual(report.row_classes['empty_numbered'], 3)
        self.assertEqual(report.total_checks[0][-1], 'block')

    def test_trap_b_junk_is_not_a_candidate(self):
        rows, report = parse([
            ('Нақд (Июль ойи)',), HEADER,
            job(1, 'Бор фх', 4.0, 200000, 2),
            ('Доллар', 12220),
            (9, 'Курс', 12220),
        ])
        self.assertEqual(len(rows), 1)
        self.assertEqual(report.unparsed, [])
        self.assertEqual([r[1] for r in report.rejections],
                         ['area out of range (12220.00)'])

    def test_a_note_in_the_area_column_is_listed_not_blocking(self):
        # «Имомов Бехзод Пешку ПТЗ.xlsx»: a cash note under the totals.
        rows, report = parse([
            ('Нақд (Июль ойи)',), HEADER,
            job(1, 'Бор фх', 4.0, 200000, 2),
            (None, 'Жами сумма:', 4.0),
            (None, None, 'Кеча топширган', 36191000, 9720000),
        ])
        self.assertEqual(len(rows), 1)
        self.assertEqual(report.unparsed, [])
        self.assertEqual([(r[0], r[1]) for r in report.rejections],
                         [(5, 'note in the area column (no farm, no row '
                              'number)')])
        # With a farm next to it the same word is a job nobody can read.
        _rows, report = parse([
            ('Нақд (Июль ойи)',), HEADER,
            (None, 'Сўзли фх', 'ўн уч', 200000, None, None, None, d(4)),
        ])
        self.assertEqual(len(report.unparsed), 1)

    def test_a_repeated_header_remaps_the_columns(self):
        moved = ('№', 'Дрон ишлаган сана', 'ФХ номи', 'Майдон (га)',
                 'Хизмат кўрсатиш суммаси', 'Жами сумма')
        rows, report = parse([
            ('Справка (Июль ойи)',), HEADER,
            job(1, 'Биринчи', 10.0, 85633, 2),
            ('Нақд (Июль ойи)',), moved,
            (1, d(5), 'Иккинчи', 6.0, 200000, 1200000),
            (None, d(6), 'Учинчи', 2.5, 200000, 500000),
        ])
        got = {r['customer_raw']: r for r in rows}
        self.assertEqual(set(got), {'Биринчи', 'Иккинчи', 'Учинчи'})
        self.assertEqual(got['Учинчи']['area_ha'], 2.5)
        self.assertEqual(got['Учинчи']['work_date_from'],
                         datetime.date(2026, 7, 6))
        self.assertTrue(got['Учинчи']['counter_missing'])
        self.assertEqual(report.row_classes['header'], 2)

    def test_a_wage_block_is_never_a_job_with_or_without_a_number(self):
        rows, report = parse([
            ('Нақд (Июль ойи)',), HEADER,
            job(1, 'Бор фх', 4.0, 200000, 2),
            ('Иш хаки (Июль ойи)',), HEADER,
            job(1, 'Синов Оператор', 30.0, 50000, 31),
            job(None, 'Синов Оператор', 12.0, 50000, 31),
            ('Справка (Июль ойи)',), HEADER,
            job(None, 'Кейинги фх', 8.0, 85633, 20),
        ])
        self.assertEqual([r['customer_raw'] for r in rows],
                         ['Бор фх', 'Кейинги фх'])
        self.assertEqual(rows[1]['payment_type'], imp.PAYMENT_TRANSFER)
        self.assertEqual(report.wage_rows, 2)
        self.assertEqual(report.row_classes['wage'], 2)

    def test_several_payment_sections_keep_their_own_type(self):
        rows, _report = parse([
            ('Справка (Июль ойи)',), HEADER, job(None, 'А фх', 1.0, 85633, 1),
            (None, 'Жами сумма:', 1.0),
            ('Нақд (Июль ойи)',), HEADER, job(None, 'Б фх', 2.0, 200000, 2),
            (None, 'Жами сумма:', 2.0),
            ('Справка (Июль ойи)',), HEADER, job(None, 'В фх', 3.0, 85633, 3),
        ])
        self.assertEqual([(r['customer_raw'], r['payment_type']) for r in rows],
                         [('А фх', 'transfer'), ('Б фх', 'cash'),
                          ('В фх', 'transfer')])

    def test_a_formula_without_cache(self):
        # A numbered row keeps the old behaviour: accepted and flagged. A
        # counterless row whose only evidence is that formula is NOT taken on
        # faith: farm and area only -> UNPARSED_CANDIDATE.
        rows, report = parse([
            ('Нақд (Июль ойи)',), HEADER,
            (1, 'Формула фх', 5.0, None, None, None, None, None),
            (None, 'Кэшсиз фх', 6.0, None, None, None, None, None),
        ], formula_cells={(3, 4), (4, 4)})
        self.assertEqual([r['customer_raw'] for r in rows], ['Формула фх'])
        self.assertTrue(rows[0]['formula_no_cache'])
        self.assertEqual([u[0] for u in report.unparsed], [4])

    def test_a_detail_sheet_without_a_recognised_header(self):
        latin_n = ('N',) + HEADER[1:]
        rows, report = parse([('Нақд (Июль ойи)',), latin_n,
                              job(1, 'Бор фх', 4.0, 200000, 2),
                              job(2, 'Йўқ фх', 5.0, 200000, 3)])
        self.assertEqual(rows, [])
        self.assertEqual([(u[0], u[1]) for u in report.unparsed],
                         [(3, 'detail sheet without a recognised header'),
                          (4, 'detail sheet without a recognised header')])

    def test_repeated_counters_keep_both_physical_rows(self):
        rows, _report = parse([
            ('Нақд (Июль ойи)',), HEADER,
            job(2, 'Такрор фх', 5.0, 200000, 7),
            job(2, 'Такрор фх', 5.0, 200000, 7),
            job(2, 'Бошқа фх', 9.0, 200000, 8),
        ])
        self.assertEqual([r['source_row'] for r in rows], [3, 4, 5])
        self.assertEqual({r['row_counter'] for r in rows}, {2})


# ─── The run: dedup, apply refusal, schema, CSV ──────────────────────────────

def _book(name, sheets, period='2026-07'):
    return (name, period, sheets)


class RunTestBase(unittest.TestCase):
    books = ()

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='fr3_counterless_')
        self.dir, self.manifest = fx.build(os.path.join(self.tmp, 'books'),
                                           books=self.books)
        self.db = fx.build_database(os.path.join(self.tmp, 'synth.db'))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def collect(self, db=None):
        manifest, _problems = imp.read_manifest(self.manifest)
        files = sorted(f for f in os.listdir(self.dir) if f.endswith('.xlsx'))
        con = imp._connect(db or self.db, read_only=True)
        try:
            dir_obj = imp.Directory(con)
            result = imp.collect(self.dir, manifest, files, dir_obj, None)
            imp.resolve(result, dir_obj)
        finally:
            con.close()
        return result

    def main(self, *extra, db=None):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = imp.main(['--dir', self.dir, '--manifest', self.manifest,
                             '--db', db or self.db,
                             '--report', os.path.join(self.tmp, 'r.txt')]
                            + list(extra))
        return code, out.getvalue()

    def sha(self, path=None):
        with open(path or self.db, 'rb') as fh:
            return hashlib.sha256(fh.read()).hexdigest()

    def count(self, table, db=None):
        con = sqlite3.connect(db or self.db)
        try:
            return con.execute('SELECT COUNT(*) FROM %s' % table).fetchone()[0]
        finally:
            con.close()


class DuplicateAcrossSheetsTests(RunTestBase):
    """A mirrored sheet: the counterless copy is a proven duplicate too."""
    books = (
        _book('Дрон А ПТЗ Июль.xlsx', [(SHEET, [
            ('Нақд (Июль ойи)',), HEADER,
            job(None, 'Кўзгу фх', 5.5, 200000, 9)])]),
        _book('Дрон Б ПТЗ Июль.xlsx', [(SHEET, [
            ('Нақд (Июль ойи)',), HEADER,
            job(4, 'Кўзгу фх', 5.5, 200000, 9)])]),
    )

    def test_the_mirror_is_skipped_whichever_side_lacks_the_counter(self):
        result = self.collect()
        self.assertEqual(len(result.rows), 1)
        self.assertEqual(len(result.duplicates), 1)
        bridge = imp.row_bridge(result)
        self.assertEqual((bridge['parsed'], bridge['duplicates'],
                          bridge['rows'], bridge['balanced']), (2, 1, 1, True))
        csv_path = os.path.join(self.tmp, 'rows.csv')
        imp.write_rows_csv(csv_path, result)
        with open(csv_path, encoding='utf-8-sig') as fh:
            text = fh.read()
        self.assertIn('DUPLICATE_SKIPPED', text)
        self.assertEqual(text.count('\nДрон'), 2)


class ApplyRefusalTests(RunTestBase):
    books = (
        _book('Дрон В ПТЗ Июль.xlsx', [(SHEET, [
            ('Нақд (Июль ойи)',), HEADER,
            job(1, 'Бор фх', 4.0, 200000, 2),
            (None, 'Номаълум фх', 7.0)])]),
    )

    def test_apply_refuses_and_writes_nothing(self):
        before = self.sha()
        code, out = self.main('--apply', '--batch', 't')
        self.assertEqual(code, 2)
        self.assertIn('ERROR APPLY_REFUSED: UNPARSED_CANDIDATE: 1 row(s)', out)
        self.assertIn('Nothing written.', out)
        self.assertEqual(self.count('drone_works'), 0)
        self.assertEqual(self.count('drone_customers'), 0)

        code, out = self.main()
        self.assertEqual(code, 0)
        self.assertIn('UNPARSED_CANDIDATE rows          : 1', out)
        self.assertIn('APPLY_PERMITTED: NO', out)
        self.assertEqual(self.sha(), before)

    def test_the_upload_path_refuses_through_the_same_function(self):
        result = self.collect()
        con = sqlite3.connect(self.db)
        try:
            with self.assertRaises(imp.ImportPreconditionError):
                imp.apply_rows(con, result, 'web')
        finally:
            con.close()
        self.assertEqual(self.count('drone_works'), 0)

    def test_the_csv_names_the_candidate(self):
        result = self.collect()
        path = os.path.join(self.tmp, 'rows.csv')
        self.assertEqual(imp.write_rows_csv(path, result), 2)
        with open(path, encoding='utf-8-sig') as fh:
            lines = fh.read().splitlines()
        self.assertTrue(lines[0].startswith('source_key,source_file,'))
        self.assertNotIn('note', lines[0].split(','))
        self.assertIn('Дрон В ПТЗ Июль.xlsx!свод ичи (Усмон)!4,', lines[2])
        self.assertIn('UNPARSED_CANDIDATE', lines[2])


class CleanRunTests(RunTestBase):
    books = (
        _book('Дрон Г ПТЗ Июль.xlsx', [(SHEET, usmon_like())]),
    )

    def test_apply_writes_the_counterless_rows_once(self):
        code, out = self.main()
        self.assertEqual(code, 0)
        self.assertIn('APPLY_PERMITTED: YES', out)
        self.assertIn('parsed as jobs                 : 9 (without "No": 4,'
                      ' 41.43 ha)', out)
        code, out = self.main('--apply', '--batch', 'a1')
        self.assertEqual(code, 0, out)
        self.assertEqual(self.count('drone_works'), 9)
        con = sqlite3.connect(self.db)
        try:
            flagged = con.execute(
                'SELECT source_row, payer_unidentified, drone_customer_id '
                'FROM drone_works WHERE customer_raw = ?',
                ('Фукаро',)).fetchall()
        finally:
            con.close()
        self.assertEqual(flagged, [(15, 1, None)])
        code, out = self.main('--apply', '--batch', 'a2')
        self.assertIn('WRITTEN: 0 customers created, 0 works inserted, '
                      '9 already present and skipped.', out)

    def test_the_report_lists_the_rows_without_a_number(self):
        self.main()
        with open(os.path.join(self.tmp, 'r.txt'), encoding='utf-8') as fh:
            text = fh.read()
        self.assertIn('РАБОТЫ, ПРИНЯТЫЕ БЕЗ НОМЕРА «№»: 4 строк, 41.43 га',
                      text)
        self.assertIn('свод ичи (Усмон) / строка 7: «Гарден» 13.53 га, '
                      '2026-07-11, сумма 1158614.49', text)
        self.assertIn('мост сходится: ДА', text)
        self.assertNotIn('+998', text)


class SchemaWarningTests(RunTestBase):
    books = CleanRunTests.books

    def setUp(self):
        super().setUp()
        con = sqlite3.connect(self.db)
        try:
            con.execute('ALTER TABLE drone_works DROP COLUMN '
                        'payer_unidentified')
            con.commit()
        finally:
            con.close()

    def test_the_dry_run_reads_and_says_no(self):
        before = self.sha()
        code, out = self.main()
        self.assertEqual(code, 0)
        self.assertIn('rows accepted            : 9', out)
        self.assertIn('SCHEMA_WARNING: ERROR: drone_works.payer_unidentified '
                      'is missing.', out)
        self.assertIn('APPLY_PERMITTED: NO', out)
        self.assertEqual(self.sha(), before)

    def test_apply_still_refuses_with_the_old_text_and_code(self):
        code, out = self.main('--apply')
        self.assertEqual(code, 2)
        self.assertEqual(out.strip(),
                         'ERROR: drone_works.payer_unidentified is missing. '
                         'Run migrate_drones_works_payer_unidentified_001.py '
                         'first.')
        self.assertEqual(self.count('drone_works'), 0)


# ─── «Когон Фукаро» ──────────────────────────────────────────────────────────

class KogonFukaroTests(RunTestBase):
    books = (
        _book('Дрон Д ПТЗ Июль.xlsx', [(SHEET, [
            ('Нақд (Июль ойи)',), HEADER,
            job(1, 'Зина фх', 5.9, 200000, 1),
            job(3, 'Когон Фукаро', 6.0, 200000, 23),
            job(4, 'Ахмад (фукаро)', 2.0, 200000, 24),
            job(5, 'Когон Фукаро (Халимов Ж)', 1.5, 200000, 25),
            job(6, 'Фукаро', 3.0, 200000, 26)])]),
    )

    def by_name(self, result):
        return {r['customer_raw']: r for r in result.rows}

    def test_it_is_held_not_carded_and_apply_refuses(self):
        result = self.collect()
        rows = self.by_name(result)
        kogon = rows['Когон Фукаро']
        self.assertTrue(kogon['payer_decision_pending'])
        self.assertFalse(kogon['payer_unidentified'])
        self.assertIsNone(kogon['drone_customer_id'])
        self.assertEqual(imp.customer_class(kogon), 'PAYER_DECISION_REQUIRED')
        self.assertEqual(result.payer_decision_pending_rows, 1)
        before = self.sha()
        code, out = self.main('--apply', '--batch', 'k')
        self.assertEqual(code, 2)
        self.assertIn('PAYER_DECISION_REQUIRED: 1 spelling(s)', out)
        self.assertEqual(self.count('drone_customers'), 0)
        self.assertEqual(self.sha(), before)

    def test_named_citizens_are_still_ordinary_new_spellings(self):
        rows = self.by_name(self.collect())
        for name in ('Ахмад (фукаро)', 'Когон Фукаро (Халимов Ж)'):
            self.assertTrue(rows[name]['customer_is_new'], name)
            self.assertFalse(rows[name]['payer_decision_pending'], name)
            self.assertFalse(rows[name]['payer_unidentified'], name)
        self.assertTrue(rows['Фукаро']['payer_unidentified'])
        self.assertFalse(imp.is_generic_payer('Когон Фукаро'))
        # Held after the Uzbek fold and the case fold, not only as typed.
        for spelling in ('Когон Фуқаро', 'КОГОН  ФУКАРО'):
            self.assertTrue(imp.is_payer_decision_pending(
                imp.customer_key(spelling)), spelling)
        self.assertFalse(imp.is_payer_decision_pending(
            imp.customer_key('Когон Фукаро (Халимов Ж)')))
        self.assertTrue(imp.has_generic_payer_word('Когон Фукаро'))
        self.assertFalse(imp.has_generic_payer_word('Фукарою'))

    def test_an_alias_the_owner_gave_is_followed(self):
        con = sqlite3.connect(self.db)
        try:
            con.execute("INSERT INTO drone_customers (name, customer_type, "
                        "is_active) VALUES ('Когон т. фуқаролари', "
                        "'external', 1)")
            con.execute("INSERT INTO drone_customer_aliases (raw_name, "
                        "normalized_key, drone_customer_id, is_active) "
                        "VALUES ('Когон Фукаро', 'когон фукаро', 1, 1)")
            con.commit()
        finally:
            con.close()
        rows = self.by_name(self.collect())
        self.assertEqual(rows['Когон Фукаро']['drone_customer_id'], 1)
        self.assertFalse(rows['Когон Фукаро']['payer_decision_pending'])
        code, out = self.main('--apply', '--batch', 'k')
        self.assertEqual(code, 0, out)

    def test_the_owners_generic_decision_simulated_not_applied(self):
        decided = imp.GENERIC_PAYER_KEYS | {'когон фукаро'}
        with mock.patch.object(imp, 'GENERIC_PAYER_KEYS', decided):
            rows = self.by_name(self.collect())
            code, out = self.main('--apply', '--batch', 'k')
        kogon = rows['Когон Фукаро']
        self.assertTrue(kogon['payer_unidentified'])
        self.assertFalse(kogon['payer_decision_pending'])
        self.assertEqual(code, 0, out)
        con = sqlite3.connect(self.db)
        try:
            names = [r[0] for r in con.execute(
                'SELECT name FROM drone_customers ORDER BY name')]
        finally:
            con.close()
        self.assertNotIn('Когон Фукаро', names)
        self.assertIn('Ахмад (фукаро)', names)
        # The code as committed is untouched by the simulation.
        self.assertNotIn('когон фукаро', imp.GENERIC_PAYER_KEYS)

    def test_negative_control_without_the_hold_the_card_is_made(self):
        with mock.patch.object(imp, 'PAYER_DECISION_PENDING_KEYS',
                               frozenset()):
            code, out = self.main('--apply', '--batch', 'k')
        self.assertEqual(code, 0, out)
        con = sqlite3.connect(self.db)
        try:
            names = [r[0] for r in con.execute(
                'SELECT name FROM drone_customers')]
        finally:
            con.close()
        self.assertIn('Когон Фукаро', names)

    def test_existing_history_is_not_touched(self):
        con = sqlite3.connect(self.db)
        try:
            con.execute(
                "INSERT INTO drone_works (period_month, customer_raw, area_ha,"
                " payment_type, source_file, source_sheet, source_row) VALUES"
                " ('2026-06', 'Когон Фукаро', 4.0, 'cash', 'Июнь.xlsx', "
                "'свод ичи (Усмон)', 9)")
            con.commit()
        finally:
            con.close()
        before = self.sha()
        self.main()
        self.main('--apply', '--batch', 'k')     # refused
        self.assertEqual(self.sha(), before)


# ─── The five real July books (optional) ─────────────────────────────────────

JULY_SHA256 = {
    'Дрон Ғиждувон ПТЗ Июль.xlsx':
        '7ea4867901a522761ffbc6ccf93c36249ee7d685658bb67e02d599fb029c2349',
    'ИЮЛЬ Агрокластер.xlsx':
        '7ffd1e5ebeb55469292374725838d6b4c02706c5a93339ea2b6bb05078fa2510',
    'Когон ПТЗ ИЮЛЬ.xlsx':
        'e609ecc74ff7d68d38d3b605c069b79693a322d9a11becd5356fbb2f05887194',
    'Сервис Дрон маълумот Июль.xlsx':
        '5740e08d3f5dddd4ab3bb8d5f856d492ad444bc3f75cd2e36064bfcbc4f29fb3',
    'Шофиркон ПТЗ Дрон маълумот Июль.xlsx':
        'ca547ab75525d70ada9bbcc946bfa466d953193c3a5112c4c295152ec0e2bd9e',
}
USMON = 'Дрон Ғиждувон ПТЗ Июль.xlsx!свод ичи (Усмон)!%d'
NAMED_19 = {USMON % r for r in [11] + list(range(35, 53))}
JULY_DIR = os.environ.get('FIN_RECON_003_JULY_BOOKS', '')


@unittest.skipUnless(JULY_DIR and os.path.isdir(JULY_DIR),
                     'set FIN_RECON_003_JULY_BOOKS to the five July books')
class RealJulyBooksTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        for name, digest in JULY_SHA256.items():
            with open(os.path.join(JULY_DIR, name), 'rb') as fh:
                if hashlib.sha256(fh.read()).hexdigest() != digest:
                    raise AssertionError('not the July book: %s' % name)
        cls.tmp = tempfile.mkdtemp(prefix='fr3_july_')
        cls.db = fx.build_database(os.path.join(cls.tmp, 'synth.db'))
        manifest = {name: '2026-07' for name in JULY_SHA256}
        con = imp._connect(cls.db, read_only=True)
        try:
            dir_obj = imp.Directory(con)
            cls.result = imp.collect(JULY_DIR, manifest, sorted(JULY_SHA256),
                                     dir_obj, None)
            imp.resolve(cls.result, dir_obj)
        finally:
            con.close()
        cls.stats = imp.summarize(cls.result)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def key(self, row):
        return '%s!%s!%d' % (row['source_file'], row['source_sheet'],
                             row['source_row'])

    def test_the_control_figures(self):
        s = self.stats
        self.assertEqual(s['rows'], 328)
        self.assertEqual(round(s['area'], 2), 5144.66)
        self.assertEqual(round(s['amount'], 2), 775357370.99)
        self.assertEqual((s['payments']['cash'],
                          round(s['payment_area']['cash'], 2)), (236, 2887.73))
        self.assertEqual((s['payments']['transfer'],
                          round(s['payment_area']['transfer'], 2)),
                         (92, 2256.93))
        usmon = [r for r in self.result.rows
                 if r['source_sheet'] == 'свод ичи (Усмон)']
        self.assertEqual((len(usmon), round(sum(r['area_ha'] for r in usmon),
                                            2)), (43, 466.12))

    def test_nothing_is_lost_and_the_nineteen_are_named(self):
        counterless = {self.key(r) for r in self.result.rows
                       if r['counter_missing']}
        self.assertEqual(counterless, NAMED_19)
        self.assertEqual(self.result.unparsed, [])
        self.assertEqual(self.result.rejections, [])
        self.assertTrue(imp.row_bridge(self.result)['balanced'])
        self.assertEqual(imp.row_bridge(self.result)['candidates'], 328)

    def test_every_book_total_matches_what_was_read(self):
        self.assertEqual(len(self.result.total_checks), 22)
        self.assertEqual([c for c in self.result.total_checks
                          if c[-1] == 'MISMATCH'], [])

    def test_kogon_fukaro_is_the_one_held_row(self):
        held = [self.key(r) for r in self.result.rows
                if r['payer_decision_pending']]
        self.assertEqual(held, ['Дрон Ғиждувон ПТЗ Июль.xlsx!'
                                'свод ичи (Шахзод)!20'])
        self.assertEqual(self.result.payer_unidentified_rows, 13)
        self.assertEqual(imp.apply_blockers(self.result),
                         ['PAYER_DECISION_REQUIRED: 1 spelling(s) of '
                          'PAYER_DECISION_PENDING_KEYS would become a new '
                          'card',
                          'SOURCE_KEY_GUARD: JULY_ALLOWLIST_REQUIRED'])

    def test_the_month_comes_from_the_july_dates(self):
        self.assertEqual({r['period_month'] for r in self.result.rows},
                         {'2026-07'})
        dated = [r for r in self.result.rows if r['work_date_from']]
        self.assertEqual(len(dated), 326)
        self.assertEqual({r['work_date_from'].strftime('%Y-%m')
                          for r in dated}, {'2026-07'})


if __name__ == '__main__':
    unittest.main()
