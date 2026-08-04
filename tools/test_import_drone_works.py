#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for tools/import_drone_works.py -- DRONE-WORKS-001.

Every trap of the task's section 2.2 is tested TWICE: once with the guard in
place, and once with the guard replaced by the naive version somebody would
plausibly have written instead. The second half is the point. A check that
returns the same answer with and without the guard is not a check, and the
real files punished exactly that: a header detector searching only for
«майдон» made whole sheets parse as empty and understated September by
1 548 ha while every test still passed.

The naive versions are injected by swapping a module global, so the shipped
tool carries no test-only switch.

Nothing here touches instance/transport.db: the books and the database are
built into a temp directory by tools/drone_works_fixtures.py.

Run:
  python tools/test_import_drone_works.py
  python -m unittest tools.test_import_drone_works -v
"""

import contextlib
import datetime
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
for path in (HERE, REPO_ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)

import drone_works_fixtures as fx  # noqa: E402
import import_drone_works as imp  # noqa: E402

EXPECTED = fx.EXPECTED


@contextlib.contextmanager
def patched(**replacements):
    """Swap module globals of import_drone_works for the duration of a block."""
    originals = {name: getattr(imp, name) for name in replacements}
    try:
        for name, value in replacements.items():
            setattr(imp, name, value)
        yield
    finally:
        for name, value in originals.items():
            setattr(imp, name, value)


# ─── The naive versions. Each one is a guard somebody could have skipped. ────

def naive_is_header_row(cells):
    """Trap (a): 'a header is a row that mentions Майдон'."""
    return any('майдон' in imp.uz_fold(c) for c in cells)


def naive_area_in_range(area):
    """Trap (b): 'any number in the area column is an area'."""
    return area is not None


def naive_payment_marker(cells):
    """Trap (c): 'the only block marker is Справка'."""
    folded = ' '.join(imp.uz_fold(c) for c in cells if imp._text(c))
    return imp.PAYMENT_TRANSFER if 'справка' in folded else None


def naive_duplicate_key(row):
    """Trap (d): 'every row of every sheet is a distinct job'."""
    return None


def naive_parse_date_cell(value):
    """Trap (e): 'a date is dd.mm.yyyy, anything else is an empty cell'."""
    if value is None:
        return None, None, imp.DATE_KIND_NONE
    if isinstance(value, datetime.datetime):
        return value.date(), value.date(), imp.DATE_KIND_EXACT
    if isinstance(value, datetime.date):
        return value, value, imp.DATE_KIND_EXACT
    try:
        parsed = datetime.datetime.strptime(imp._text(value), '%d.%m.%Y').date()
    except ValueError:
        return None, None, imp.DATE_KIND_NONE
    return parsed, parsed, imp.DATE_KIND_EXACT


def naive_resolve_price(price_cell, amount, area):
    """Trap (f): 'an empty price is obviously amount / area'."""
    value = imp._number(price_cell)
    if value is not None:
        return value
    if amount and area:
        return amount / area
    return None


def naive_operator_from_sheet_title(title, file_stem):
    """Trap (g): 'the sheet title is the operator'."""
    return imp._text(title), False


def naive_is_wage_marker(cells):
    """Trap (h): 'Иш хаки is just another line'."""
    return False


# ─── Harness ─────────────────────────────────────────────────────────────────

class ImportTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix='drone_works_fx_')
        cls.books, cls.manifest = fx.build(os.path.join(cls.tmp, 'books'))
        cls.db = fx.build_database(os.path.join(cls.tmp, 'fixture.db'))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_import(self, db=None, default_payment=None):
        """Parse + resolve, exactly as main() does, without writing."""
        manifest, problems = imp.read_manifest(self.manifest)
        files = sorted(f for f in os.listdir(self.books)
                       if f.lower().endswith('.xlsx'))
        con = imp._connect(db or self.db, read_only=True)
        try:
            dir_obj = imp.Directory(con)
            result = imp.collect(self.books, manifest, files, dir_obj,
                                 default_payment)
            result.manifest_problems = problems
            imp.resolve(result, dir_obj)
            stats = imp.summarize(result)
        finally:
            con.close()
        return result, stats

    def fresh_db(self, name):
        return fx.build_database(os.path.join(self.tmp, name))

    def sheet(self, result, file_name, sheet_name):
        for report in result.sheets:
            if (report.file_name == file_name
                    and report.sheet_name == sheet_name):
                return report
        self.fail('sheet not found: %s / %s' % (file_name, sheet_name))


# ─── The baseline everything else is measured against ────────────────────────

class BaselineTests(ImportTestBase):
    def test_the_whole_run_matches_the_hand_computed_prediction(self):
        result, stats = self.run_import()
        self.assertEqual(stats['rows'], EXPECTED['rows'])
        self.assertAlmostEqual(stats['area'], EXPECTED['area'], places=2)
        self.assertEqual(len(result.duplicates), EXPECTED['duplicates'])
        self.assertEqual(len(result.rejections), EXPECTED['rejections'])
        self.assertEqual(result.wage_rows, EXPECTED['wage_rows'])
        self.assertEqual(stats['customers'], EXPECTED['distinct_customers'])
        for payment, count in EXPECTED['payments'].items():
            self.assertEqual(stats['payments'][payment], count, payment)
            self.assertAlmostEqual(stats['payment_area'][payment],
                                   EXPECTED['payment_area'][payment], places=2)
        for kind, count in EXPECTED['kinds'].items():
            self.assertEqual(stats['kinds'][kind], count, kind)

    def test_every_cut_reconciles_to_the_same_hectares(self):
        _result, stats = self.run_import()
        self.assertAlmostEqual(sum(stats['payment_area'].values()),
                               stats['area'], places=2)
        self.assertAlmostEqual(sum(stats['kind_area'].values()),
                               stats['area'], places=2)
        self.assertAlmostEqual(sum(a for _c, a in stats['months'].values()),
                               stats['area'], places=2)
        self.assertEqual(sum(stats['payments'].values()), stats['rows'])
        self.assertEqual(sum(stats['kinds'].values()), stats['rows'])


# ─── Trap (a): a data row can look like a header ─────────────────────────────

class TrapAHeaderDetectionTests(ImportTestBase):
    FILE = 'Гарден Дрон маълумот Апрель.xlsx'
    SHEET = 'свод ичи (Фурқат)'

    def test_guard_keeps_the_whole_sheet(self):
        result, stats = self.run_import()
        sheet = self.sheet(result, self.FILE, self.SHEET)
        self.assertEqual(sheet.rows, 4)
        self.assertAlmostEqual(sheet.area, 47.5, places=2)
        self.assertEqual(stats['rows'], EXPECTED['rows'])

    def test_without_the_guard_the_sheet_silently_loses_three_rows(self):
        with patched(is_header_row=naive_is_header_row):
            result, stats = self.run_import()
        sheet = self.sheet(result, self.FILE, self.SHEET)
        # The note mentioning «майдон» is taken for a header; the columns are
        # remapped from that row and everything below it stops being data.
        self.assertEqual(sheet.rows, 1)
        self.assertAlmostEqual(sheet.area, 12.5, places=2)
        # The same naive detector ALSO misses the «Мадон» typo header, so a
        # second sheet parses as empty. Both halves of the trap in one run:
        # 4 rows and 53.0 ha vanish and nothing reports a loss.
        typo = self.sheet(result, 'Шофиркон ПТЗ Дрон маълумот.xlsx',
                          'свод ичи ')
        self.assertEqual(typo.rows, 0)
        self.assertIsNone(typo.header_row)
        self.assertEqual(stats['rows'], EXPECTED['rows'] - 4)
        self.assertAlmostEqual(stats['area'], EXPECTED['area'] - 53.0,
                               places=2)

    def test_the_typo_header_is_recognised_by_the_guard(self):
        result, _stats = self.run_import()
        typo = self.sheet(result, 'Шофиркон ПТЗ Дрон маълумот.xlsx',
                          'свод ичи ')
        self.assertEqual(typo.rows, 1)
        self.assertIn('Мадон (га)', typo.header_cells)

    def test_the_three_conditions_are_all_required(self):
        good = ['№', 'Сана', 'ФХ номи', 'Майдон (га)']
        self.assertTrue(imp.is_header_row(good))
        self.assertTrue(imp.is_header_row(['№', 'Сана', 'ФХ номи',
                                           'Мадон (га)']))
        # first cell not exactly '№'
        self.assertFalse(imp.is_header_row(['N', 'Сана', 'ФХ номи',
                                            'Майдон']))
        # no «ФХ номи» in the first four cells
        self.assertFalse(imp.is_header_row(['№', 'Сана', 'Ном', 'Майдон']))
        # no «Майдон»/«Мадон» in the first six
        self.assertFalse(imp.is_header_row(['№', 'Сана', 'ФХ номи', 'Ер']))
        # the real data row that broke the naive detector
        self.assertFalse(imp.is_header_row(
            [2, None, 'Ғиждувон ПТЗ ФХ', 10.0, 200000, 2000000, 50000, 0,
             'Ғиждувон ПТЗ- ФХ даласидан 10 га майдон ишланди']))


# ─── Trap (b): junk below the totals row ─────────────────────────────────────

class TrapBAreaRangeTests(ImportTestBase):
    def test_guard_rejects_the_currency_rate_and_says_why(self):
        result, stats = self.run_import()
        reasons = [r[3] for r in result.rejections]
        self.assertIn('area out of range (12220.00)', reasons)
        self.assertAlmostEqual(stats['area'], EXPECTED['area'], places=2)

    def test_without_the_guard_one_row_adds_12220_hectares(self):
        with patched(area_in_range=naive_area_in_range):
            _result, stats = self.run_import()
        self.assertEqual(stats['rows'], EXPECTED['rows'] + 1)
        self.assertAlmostEqual(stats['area'],
                               EXPECTED['area'] + EXPECTED['junk_area'],
                               places=2)

    def test_the_bounds_themselves(self):
        self.assertFalse(imp.area_in_range(None))
        self.assertFalse(imp.area_in_range(0))
        self.assertFalse(imp.area_in_range(-1))
        self.assertTrue(imp.area_in_range(0.01))
        self.assertTrue(imp.area_in_range(1000.0))
        self.assertFalse(imp.area_in_range(1000.01))


# ─── Trap (c): two payment blocks per sheet ──────────────────────────────────

class TrapCPaymentBlockTests(ImportTestBase):
    def test_guard_reads_both_blocks_in_either_order(self):
        result, stats = self.run_import()
        kogon = self.sheet(result, 'Когон ПТЗ Шохрух Хамроев МАРТ.xlsx',
                           'свод ичи Шохрух')
        # cash block first, transfer block second -- the sheet may start with
        # either, so the reader may not assume an order.
        self.assertEqual([b[1] for b in kogon.blocks], ['cash', 'transfer'])
        self.assertEqual(stats['payments']['cash'], 11)
        self.assertEqual(stats['payments']['transfer'], 8)
        self.assertEqual(stats['payments']['internal'], 2)

    def test_a_parser_that_only_knows_spravka_loses_every_cash_row(self):
        with patched(payment_marker=naive_payment_marker):
            result, stats = self.run_import()
        self.assertEqual(stats['payments']['cash'], 0)
        self.assertEqual(stats['rows'], 9)
        self.assertGreater(len(result.rejections), EXPECTED['rejections'])
        self.assertTrue(any('no payment block' in r[3]
                            for r in result.rejections))

    def test_a_row_before_any_block_is_rejected_not_guessed(self):
        result, _stats = self.run_import()
        matching = [r for r in result.rejections
                    if r[0] == 'Гарден Дрон маълумот Март.xlsx'
                    and 'no payment block' in r[3]]
        self.assertEqual(len(matching), 1)

    def test_default_payment_is_an_explicit_owner_override(self):
        result, stats = self.run_import(default_payment=imp.PAYMENT_CASH)
        self.assertEqual(stats['rows'], EXPECTED['rows'] + 1)
        self.assertFalse(any('no payment block' in r[3]
                             for r in result.rejections))

    def test_an_internal_customer_overrides_the_block(self):
        result, _stats = self.run_import()
        rows = [r for r in result.rows
                if r['customer_raw'] == 'Тизим корхонаси Ромитан']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['payment_type'], imp.PAYMENT_INTERNAL)


# ─── Trap (d): duplicate sheets and duplicate files ──────────────────────────

class TrapDDuplicateTests(ImportTestBase):
    def test_guard_skips_four_rows_and_names_both_provenances(self):
        result, stats = self.run_import()
        self.assertEqual(len(result.duplicates), EXPECTED['duplicates'])
        for first, second in result.duplicates:
            self.assertNotEqual(
                (first['source_file'], first['source_sheet'],
                 first['source_row']),
                (second['source_file'], second['source_sheet'],
                 second['source_row']))
        # three inside one file, one across two files
        same_file = [p for p in result.duplicates
                     if p[0]['source_file'] == p[1]['source_file']]
        self.assertEqual(len(same_file), 3)
        self.assertEqual(len(result.duplicates) - len(same_file), 1)
        self.assertAlmostEqual(stats['area'], EXPECTED['area'], places=2)

    def test_without_the_guard_the_ledger_double_counts_352_hectares(self):
        with patched(duplicate_key=naive_duplicate_key):
            result, stats = self.run_import()
        self.assertEqual(len(result.duplicates), 0)
        self.assertEqual(stats['rows'], EXPECTED['rows'] + 4)
        self.assertAlmostEqual(stats['area'],
                               EXPECTED['area'] + 292.59 + 60.0, places=2)


# ─── Trap (e): date formats ──────────────────────────────────────────────────

class TrapEDateTests(ImportTestBase):
    def test_every_observed_form(self):
        cases = [
            (datetime.datetime(2026, 4, 11), datetime.date(2026, 4, 11),
             datetime.date(2026, 4, 11), imp.DATE_KIND_EXACT),
            ('11.04.2026', datetime.date(2026, 4, 11),
             datetime.date(2026, 4, 11), imp.DATE_KIND_EXACT),
            ('23-24.03.2026', datetime.date(2026, 3, 23),
             datetime.date(2026, 3, 24), imp.DATE_KIND_SPAN),
            ('13,16,17.05.2026', datetime.date(2026, 5, 13),
             datetime.date(2026, 5, 17), imp.DATE_KIND_SPAN),
            ('27,30.03.2026', datetime.date(2026, 3, 27),
             datetime.date(2026, 3, 30), imp.DATE_KIND_SPAN),
            ('16,30.03.2026', datetime.date(2026, 3, 16),
             datetime.date(2026, 3, 30), imp.DATE_KIND_SPAN),
            # the dot between month and year is genuinely missing here
            ('08-09.092025', datetime.date(2025, 9, 8),
             datetime.date(2025, 9, 9), imp.DATE_KIND_SPAN),
            (None, None, None, imp.DATE_KIND_NONE),
            ('', None, None, imp.DATE_KIND_NONE),
            ('   ', None, None, imp.DATE_KIND_NONE),
        ]
        for value, want_from, want_to, want_kind in cases:
            got_from, got_to, got_kind = imp.parse_date_cell(value)
            self.assertEqual((got_from, got_to, got_kind),
                             (want_from, want_to, want_kind), repr(value))

    def test_forms_it_refuses_rather_than_guesses(self):
        for value in ('с 5 по 7 марта', '30-01.03.2026', '31.02.2026',
                      'апрель', '2026', '13/16/17.05.2026'):
            got_from, got_to, kind = imp.parse_date_cell(value)
            self.assertIsNone(got_from, repr(value))
            self.assertIsNone(got_to, repr(value))
            self.assertEqual(kind, imp.DATE_KIND_UNPARSED, repr(value))

    def test_an_unparsed_date_keeps_the_row_and_is_listed(self):
        result, stats = self.run_import()
        unparsed = [r for r in result.rows
                    if r['date_kind'] == imp.DATE_KIND_UNPARSED]
        self.assertEqual(len(unparsed), EXPECTED['kinds']['unparsed'])
        self.assertEqual({r['date_raw'] for r in unparsed},
                         {'с 5 по 7 марта', '30-01.03.2026'})
        for row in unparsed:
            self.assertIsNone(row['work_date_from'])
            self.assertIsNone(row['work_date_to'])
        self.assertEqual(stats['rows'], EXPECTED['rows'])

    def test_without_the_guard_spans_and_junk_look_like_empty_cells(self):
        with patched(parse_date_cell=naive_parse_date_cell):
            _result, stats = self.run_import()
        self.assertEqual(stats['kinds']['span'], 0)
        self.assertEqual(stats['kinds']['unparsed'], 0)
        # 5 spans + 2 unparsed silently join the 2 genuinely empty cells and
        # nothing in the report says a single date was lost.
        self.assertEqual(stats['kinds']['none'], 9)

    def test_date_raw_is_kept_even_when_the_dates_parsed(self):
        result, _stats = self.run_import()
        spans = [r for r in result.rows
                 if r['date_kind'] == imp.DATE_KIND_SPAN]
        self.assertTrue(spans)
        for row in spans:
            self.assertTrue(row['date_raw'])


# ─── Trap (f): prices are never substituted ──────────────────────────────────

class TrapFPriceTests(ImportTestBase):
    def test_an_empty_price_cell_stays_null(self):
        result, stats = self.run_import()
        rows = [r for r in result.rows if r['customer_raw'] == 'Фукаро']
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]['price_per_ha'])
        self.assertEqual(rows[0]['amount'], 700000)
        self.assertEqual(stats['priced'], EXPECTED['rows'] - 1)

    def test_without_the_guard_the_import_invents_a_price(self):
        with patched(resolve_price=naive_resolve_price):
            result, stats = self.run_import()
        rows = [r for r in result.rows if r['customer_raw'] == 'Фукаро']
        self.assertAlmostEqual(rows[0]['price_per_ha'], 140000.0, places=2)
        self.assertEqual(stats['priced'], EXPECTED['rows'])

    def test_the_prices_that_are_written_are_stored_verbatim(self):
        result, _stats = self.run_import()
        prices = {r['price_per_ha'] for r in result.rows
                  if r['price_per_ha'] is not None}
        self.assertIn(200000.0, prices)
        self.assertIn(150000.0, prices)
        self.assertIn(250000.0, prices)
        self.assertIn(300000.0, prices)
        # the two different internal tariffs both survive
        self.assertIn(85633.0, prices)
        self.assertIn(76458.0, prices)


# ─── Trap (g): sheet titles ──────────────────────────────────────────────────

class TrapGSheetTitleTests(ImportTestBase):
    def test_the_three_title_shapes(self):
        self.assertEqual(
            imp.operator_from_sheet_title('свод ичи (Фурқат)', 'file'),
            ('Фурқат', False))
        self.assertEqual(
            imp.operator_from_sheet_title('свод ичи Шохрух', 'file'),
            ('Шохрух', False))
        self.assertEqual(
            imp.operator_from_sheet_title('свод ичи ', 'Имомов Бехзод Пешку'),
            ('Имомов Бехзод Пешку', True))

    def test_the_fallback_is_flagged_on_every_sheet_that_used_it(self):
        result, _stats = self.run_import()
        fallbacks = [s for s in result.sheets if s.operator_from_file_name]
        self.assertEqual(len(fallbacks), 2)
        self.assertEqual(
            {s.operator_raw for s in fallbacks},
            {'Имомов Бехзод Пешку ПТЗ', 'Шофиркон ПТЗ Дрон маълумот'})

    def test_without_the_guard_the_operator_becomes_the_word_svod(self):
        with patched(
                operator_from_sheet_title=naive_operator_from_sheet_title):
            result, _stats = self.run_import()
        self.assertEqual([s for s in result.sheets
                          if s.operator_from_file_name], [])
        self.assertIn('свод ичи',
                      {s.operator_raw for s in result.sheets})
        # ... and every row of those sheets loses its operator
        self.assertGreater(sum(result.unresolved_operators.values()),
                           EXPECTED['operators_unresolved'])


# ─── Trap (h): «Иш хаки» is not imported ─────────────────────────────────────

class TrapHWageTests(ImportTestBase):
    def test_the_wage_block_is_counted_and_skipped(self):
        result, stats = self.run_import()
        self.assertEqual(result.wage_rows, EXPECTED['wage_rows'])
        self.assertNotIn('Файзуллаев Фурқат',
                         {r['customer_raw'] for r in result.rows})
        self.assertEqual(stats['rows'], EXPECTED['rows'])

    def test_without_the_guard_the_operator_is_billed_as_a_farm(self):
        with patched(is_wage_marker=naive_is_wage_marker):
            result, stats = self.run_import()
        self.assertEqual(result.wage_rows, 0)
        self.assertEqual(stats['rows'], EXPECTED['rows'] + 2)
        self.assertAlmostEqual(stats['area'], EXPECTED['area'] + 45.0,
                               places=2)
        self.assertIn('Файзуллаев Фурқат',
                      {r['customer_raw'] for r in result.rows})


# ─── The manifest ────────────────────────────────────────────────────────────

class ManifestTests(ImportTestBase):
    def test_a_file_absent_from_the_manifest_is_refused_and_listed(self):
        result, stats = self.run_import()
        self.assertEqual(result.files_skipped_no_manifest,
                         ['Ноаниқ файл.xlsx'])
        self.assertEqual(stats['files'], 8)
        self.assertNotIn('Ҳеч ким', {r['customer_raw'] for r in result.rows})

    def test_the_shipped_manifest_parses_and_covers_every_listed_book(self):
        path = os.path.join(HERE, 'drone_works_manifest.txt')
        manifest, problems = imp.read_manifest(path)
        self.assertEqual(problems, [])
        self.assertEqual(len(manifest), 26)
        self.assertEqual(manifest['Имомов Бехзод Пешку ПТЗ.xlsx'], '2026-03')
        self.assertEqual(manifest['01.07.2026 Июль.xlsx'], '2026-07')
        self.assertEqual(manifest['Ғиждувон ПТЗ Дрон маълумот Март.xlsx'],
                         '2026-03')
        for period in manifest.values():
            self.assertRegex(period, r'^\d{4}-\d{2}$')

    def test_dated_rows_outside_the_manifest_month_are_reported(self):
        result, _stats = self.run_import()
        self.assertEqual(set(result.month_outliers),
                         {'Имомов Бехзод Пешку ПТЗ.xlsx'})
        self.assertEqual(result.month_outliers['Имомов Бехзод Пешку ПТЗ.xlsx'],
                         {'2026-04'})

    def test_an_undated_row_inherits_the_manifest_month(self):
        result, _stats = self.run_import()
        rows = [r for r in result.rows
                if r['customer_raw'] == 'Пешку номаълум']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['period_month'], '2026-03')
        self.assertIsNone(rows[0]['work_date_from'])

    def test_a_dated_row_keeps_its_own_date(self):
        result, _stats = self.run_import()
        rows = [r for r in result.rows
                if r['customer_raw'] == 'Пешку Гулистон']
        self.assertEqual(rows[0]['work_date_from'], datetime.date(2026, 4, 25))
        self.assertEqual(rows[0]['period_month'], '2026-03')


# ─── Resolution ──────────────────────────────────────────────────────────────

class ResolutionTests(ImportTestBase):
    def _by_customer(self, result, customer):
        rows = [r for r in result.rows if r['customer_raw'] == customer]
        self.assertEqual(len(rows), 1, customer)
        return rows[0]

    def test_a_short_form_matches_a_full_name(self):
        result, _stats = self.run_import()
        row = self._by_customer(result, 'Миробод АМТ')
        self.assertIsNotNone(row['drone_operator_id'])
        self.assertEqual(row['operator_raw'], 'Фурқат')

    def test_the_uzbek_letters_fold(self):
        # «Фурқат» and «Фуркат», «Беҳзод» and «Бехзод» are one man each.
        self.assertEqual(imp.name_key('Фурқат'), imp.name_key('Фуркат'))
        self.assertEqual(imp.name_key('Беҳзод'), imp.name_key('Бехзод'))
        self.assertEqual(imp.name_key('Туйғун'), imp.name_key('Туйгун'))
        self.assertEqual(imp.name_key('фАРРУХ'), imp.name_key('Фаррух'))
        self.assertNotEqual(imp.name_key('Шохрух'), imp.name_key('Шахзод'))

    def test_an_ambiguous_short_form_is_split_by_the_file_subdivision(self):
        """«Шохрух» is Khamroev in Kogon and Fayzullaev in Garden."""
        result, _stats = self.run_import()
        row = self._by_customer(result, 'Бахор фермер')
        con = sqlite3.connect(self.db)
        try:
            name = con.execute('SELECT full_name FROM drone_operators '
                               'WHERE id=?',
                               (row['drone_operator_id'],)).fetchone()[0]
        finally:
            con.close()
        self.assertEqual(name, 'Хамроев Шохрух')
        self.assertEqual(row['subdivision_name'], 'Когон')

    def test_an_ambiguity_the_subdivision_cannot_split_stays_null(self):
        """«Шахзод» in a file naming no subdivision. Never guessed."""
        result, _stats = self.run_import()
        row = self._by_customer(result, 'Бухоро Агрокластер бош ер')
        self.assertIsNone(row['drone_operator_id'])
        self.assertIsNone(row['subdivision_name'])
        reasons = {reason for (_raw, reason) in result.unresolved_operators}
        self.assertTrue(any('ambiguous' in r for r in reasons))

    def test_an_unresolved_operator_never_rejects_the_row(self):
        result, stats = self.run_import()
        unresolved = [r for r in result.rows
                      if r['drone_operator_id'] is None]
        self.assertEqual(len(unresolved),
                         EXPECTED['operators_unresolved'])
        self.assertEqual(result.matched_operators,
                         EXPECTED['operators_matched'])
        self.assertEqual(stats['rows'], EXPECTED['rows'])

    def test_a_customer_alias_is_matched_and_no_duplicate_is_created(self):
        db = self.fresh_db('alias_hit.db')
        con = sqlite3.connect(db)
        con.execute("INSERT INTO drone_customers (name) VALUES ('Миробод')")
        con.execute('INSERT INTO drone_customer_aliases (raw_name, '
                    'normalized_key, drone_customer_id, is_active) '
                    'VALUES (?, ?, 1, 1)',
                    ('Миробод АМТ', imp.customer_key('Миробод АМТ')))
        con.commit()
        con.close()
        result, _stats = self.run_import(db=db)
        row = self._by_customer(result, 'Миробод АМТ')
        self.assertEqual(row['drone_customer_id'], 1)
        self.assertFalse(row['customer_is_new'])
        self.assertEqual(result.matched_customers, 1)
        self.assertEqual(len(result.created_customers),
                         EXPECTED['distinct_customers'] - 1)

    def test_a_null_is_active_alias_still_resolves(self):
        """isnot(False), never == True -- the bug already caught once."""
        db = self.fresh_db('alias_null.db')
        con = sqlite3.connect(db)
        con.execute("INSERT INTO drone_customers (name) VALUES ('Миробод')")
        con.execute('INSERT INTO drone_customer_aliases (raw_name, '
                    'normalized_key, drone_customer_id, is_active) '
                    'VALUES (?, ?, 1, NULL)',
                    ('Миробод АМТ', imp.customer_key('Миробод АМТ')))
        con.commit()
        con.close()
        result, _stats = self.run_import(db=db)
        self.assertEqual(self._by_customer(result, 'Миробод АМТ')
                         ['drone_customer_id'], 1)

    def test_a_deactivated_alias_does_not_resolve(self):
        """The differential half: is_active = 0 really does take it out."""
        db = self.fresh_db('alias_off.db')
        con = sqlite3.connect(db)
        con.execute("INSERT INTO drone_customers (name) VALUES ('Миробод')")
        con.execute('INSERT INTO drone_customer_aliases (raw_name, '
                    'normalized_key, drone_customer_id, is_active) '
                    'VALUES (?, ?, 1, 0)',
                    ('Миробод АМТ', imp.customer_key('Миробод АМТ')))
        con.commit()
        con.close()
        result, _stats = self.run_import(db=db)
        self.assertIsNone(self._by_customer(result, 'Миробод АМТ')
                          ['drone_customer_id'])
        self.assertEqual(result.matched_customers, 0)

    def test_the_customer_key_is_case_and_whitespace_only(self):
        self.assertEqual(imp.customer_key('  Миробод   АМТ '),
                         imp.customer_key('миробод амт'))
        self.assertNotEqual(imp.customer_key('Миробод АМТ'),
                            imp.customer_key('Мироbод АМТ'))


# ─── Writing ─────────────────────────────────────────────────────────────────

class ApplyTests(ImportTestBase):
    def _main(self, db, extra=()):
        argv = ['--dir', self.books, '--manifest', self.manifest,
                '--db', db, '--report', os.path.join(self.tmp, 'report.txt')]
        argv.extend(extra)
        return imp.main(argv)

    def test_dry_run_writes_nothing(self):
        db = self.fresh_db('dry.db')
        self.assertEqual(self._main(db), 0)
        con = sqlite3.connect(db)
        try:
            self.assertEqual(
                con.execute('SELECT COUNT(*) FROM drone_works').fetchone()[0],
                0)
            self.assertEqual(
                con.execute('SELECT COUNT(*) FROM drone_customers')
                .fetchone()[0], 0)
        finally:
            con.close()

    def test_apply_writes_the_ledger_and_the_directory(self):
        db = self.fresh_db('apply.db')
        self.assertEqual(self._main(db, ['--apply', '--batch', 'b1']), 0)
        con = sqlite3.connect(db)
        try:
            count, area = con.execute(
                'SELECT COUNT(*), ROUND(SUM(area_ha), 2) '
                'FROM drone_works').fetchone()
            self.assertEqual(count, EXPECTED['rows'])
            self.assertAlmostEqual(area, EXPECTED['area'], places=2)
            self.assertEqual(
                con.execute('SELECT COUNT(*) FROM drone_customers')
                .fetchone()[0], EXPECTED['distinct_customers'])
            self.assertEqual(
                con.execute('SELECT COUNT(*) FROM drone_customer_aliases')
                .fetchone()[0], EXPECTED['distinct_customers'])
            self.assertEqual(
                con.execute('SELECT COUNT(*) FROM drone_works '
                            'WHERE drone_customer_id IS NULL').fetchone()[0],
                0)
            self.assertEqual(
                con.execute("SELECT COUNT(*) FROM drone_works "
                            "WHERE import_batch='b1'").fetchone()[0],
                EXPECTED['rows'])
        finally:
            con.close()

    def test_a_second_apply_is_a_no_op_not_a_doubling(self):
        db = self.fresh_db('twice.db')
        self._main(db, ['--apply', '--batch', 'b1'])
        self._main(db, ['--apply', '--batch', 'b2'])
        con = sqlite3.connect(db)
        try:
            self.assertEqual(
                con.execute('SELECT COUNT(*) FROM drone_works').fetchone()[0],
                EXPECTED['rows'])
            self.assertEqual(
                con.execute('SELECT COUNT(*) FROM drone_customers')
                .fetchone()[0], EXPECTED['distinct_customers'])
            self.assertEqual(
                con.execute("SELECT COUNT(*) FROM drone_works "
                            "WHERE import_batch='b2'").fetchone()[0], 0)
        finally:
            con.close()

    def test_a_missing_database_exits_2_and_creates_no_file(self):
        missing = os.path.join(self.tmp, 'nope.db')
        with self.assertRaises(SystemExit) as raised:
            self._main(missing)
        self.assertEqual(raised.exception.code, 2)
        self.assertFalse(os.path.exists(missing))

    def test_the_report_file_carries_the_cyrillic_detail(self):
        db = self.fresh_db('report.db')
        self._main(db)
        with open(os.path.join(self.tmp, 'report.txt'), encoding='utf-8') as fh:
            text = fh.read()
        self.assertIn('Ноаниқ файл.xlsx', text)
        self.assertIn('с 5 по 7 марта', text)
        self.assertIn('ВЗЯТ ИЗ ИМЕНИ ФАЙЛА', text)
        self.assertIn('Имомов Бехзод Пешку ПТЗ.xlsx / свод ичи ', text)
        # every duplicate names both sides
        self.assertIn('принято:', text)
        self.assertIn('пропущено:', text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
