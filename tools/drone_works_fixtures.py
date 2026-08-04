#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/drone_works_fixtures.py -- synthetic books for DRONE-WORKS-001.

The dispatchers' real files are not in the repository and will not be, so the
import tool is proved against these instead. Every trap listed in the original
task (2.2 a..h) has a file here that reproduces it, and every root cause found
on the real books on 2026-08-04 (DRONE-WORKS-IMPORT-FIX-001) has another. The
tests run each one twice: once with the guard and once with the guard replaced
by the naive version, asserting that the naive version produces a DIFFERENT
and WRONG number. A guard with no failing negative control is not covered.

**The headers are the real ones.** The first version of this file invented
plausible headers («Сумма», «1 га нархи») and the tool passed every test while
mapping the price column into `amount` on all twenty-four real layouts. A
fixture that does not carry the real header text proves nothing about a parser
whose whole job is reading header text.

The numbers are small and hand-checkable on purpose -- 21 rows and 671.09 ha
across the eight original books, not a scaled-down copy of 15 738 ha. The point
is that a human can add them up in the report and see the same figure.

The workbooks are BUILT, not committed as binaries: a .xlsx in git is an
opaque zip that no diff can review, and a fixture nobody can read stops being
evidence the first time somebody doubts it.

Run standalone to look at them:

  python tools/drone_works_fixtures.py --out /tmp/drone_fixtures
"""

import argparse
import datetime
import os
import sys

# The real header rows, verbatim from the dispatchers' books.
#
# «Хизмат кўрсатиш суммаси» is the PRICE PER HECTARE and «Жами сумма (обьём)»
# is the amount -- the pair that a fragment matcher swaps, because «суммаси»
# is a substring of the first and reaches it before the second is considered.
HEADER = ['№', 'Дрон ишлаган сана', 'ФХ номи', 'Майдон (га)',
          'Хизмат кўрсатиш суммаси', 'Жами сумма (обьём)',
          'Бошка харажатлар', 'Кирим қилинган', '*Изоҳ']
# The «Мадон» typo is in the real file.
HEADER_TYPO = ['№', 'Дрон ишлаган сана', 'ФХ номи', 'Мадон (га)',
               'Хизмат кўрсатиш суммаси', 'Жами сумма (обьём)',
               'Бошка харажатлар', 'Кирим қилинган', '*Изоҳ']


def _d(year, month, day):
    return datetime.datetime(year, month, day)


# ─── The eight original books ────────────────────────────────────────────────

# (a) a data row whose «*Изоҳ» mentions «майдон»; (b) junk below the totals;
# (f) an empty price cell; (h) an «Иш хаки» block.
FILE_GARDEN_APRIL = ('Гарден Дрон маълумот Апрель.xlsx', '2026-04', [
    ('свод ичи (Фурқат)', [
        ['Гарден Агрокластер дрон маълумот'],
        ['Справка (апрель ойи)'],
        HEADER,
        [1, _d(2026, 4, 5), 'Миробод АМТ', 12.5, 200000, 2500000, None,
         2500000, None],
        # TRAP (a): the note contains «майдон» and every naive header detector
        # swallows this row -- and, with it, the rest of the sheet.
        [2, _d(2026, 4, 6), 'Ғиждувон ПТЗ ФХ', 10.0, 200000, 2000000, 50000,
         0, 'Ғиждувон ПТЗ- ФХ даласидан 10 га майдон ишланди'],
        [3, '23-24.04.2026', 'Хуррам бобо фермер', 20.0, 150000, 3000000,
         None, 1000000, None],
        # TRAP (f): the price cell is empty. It stays NULL -- never
        # amount / area, which would invent 140 000.
        [4, None, 'Фукаро', 5.0, None, 700000, None, 0, 'нархи келишилмаган'],
        ['Жами сумма', '', '', 47.5, '', 8200000, '', '', ''],
        # TRAP (b), both halves: a row with no counter, and a row with a
        # counter whose «area» is a currency rate.
        ['Доллар', 12220],
        [9, None, 'Курс', 12220, None, None, None, None, None],
        # TRAP (h): the operator's wage. Deferred by the owner, not imported.
        ['Иш хаки (апрель ойи)'],
        [1, _d(2026, 4, 30), 'Файзуллаев Фурқат', 30.0, 20000, 600000, None,
         600000, None],
        [2, _d(2026, 4, 30), 'Ёрдамчи', 15.0, 20000, 300000, None, 300000,
         None],
    ]),
])

# (c) two payment blocks, cash FIRST; (e) the whole date zoo.
FILE_KOGON_MARCH = ('Когон ПТЗ Шохрух Хамроев МАРТ.xlsx', '2026-03', [
    ('свод ичи Шохрух', [
        ['Нақд (март ойи)'],
        HEADER,
        [1, _d(2026, 3, 11), 'Бахор фермер', 8.0, 200000, 1600000, None,
         1600000, None],
        [2, '23-24.03.2026', 'Дўстлик ФХ', 15.5, 200000, 3100000, None, 0,
         None],
        [3, '13,16,17.03.2026', 'Янги ҳаёт ФХ', 22.0, 250000, 5500000, None,
         2000000, None],
        [4, '27,30.03.2026', 'Олтин водий', 9.5, 200000, 1900000, None,
         1900000, None],
        # Unparseable: kept with date_raw and both dates NULL, and listed.
        [5, 'с 5 по 7 марта', 'Барака ФХ', 6.0, 200000, 1200000, None, 0,
         None],
        # Descending range -- the span crosses a month boundary and min/max
        # would read it as the whole month. Refused, not guessed.
        [6, '30-01.03.2026', 'Зафар ФХ', 4.0, 200000, 800000, None, 0, None],
        ['Жами сумма', '', '', 65.0, '', 14100000, '', '', ''],
        ['Справка (март ойи)'],
        [1, _d(2026, 3, 20), 'Пахтакор АМТ', 30.0, 300000, 9000000, None, 0,
         None],
    ]),
])

# (d) two sheets with identical content inside one file; (g) a sheet titled
# «свод ичи » with no name in it; plus dated rows outside the manifest month.
_PESHKU_ROWS = [
    ['Нақд (март ойи)'],
    HEADER,
    [1, _d(2026, 3, 17), 'Пешку АМТ', 100.0, 200000, 20000000, None,
     20000000, None],
    [2, _d(2026, 4, 25), 'Пешку Гулистон', 92.59, 200000, 18518000, None, 0,
     None],
    [3, None, 'Пешку номаълум', 100.0, 200000, 20000000, None, 0, None],
]
FILE_PESHKU = ('Имомов Бехзод Пешку ПТЗ.xlsx', '2026-03', [
    ('свод ичи (Беҳзод)', list(_PESHKU_ROWS)),
    ('свод ичи ', list(_PESHKU_ROWS)),
])

# (d) across FILES: one row of the pair appears in both Servis books.
FILE_SERVIS_1 = ('Сервис Дрон Маълумот.xlsx', '2025-09', [
    ('свод ичи (Шахзод)', [
        ['Справка (сентябрь ойи)'],
        HEADER,
        [1, _d(2025, 9, 3), 'Сервис ФХ 1', 40.0, 200000, 8000000, None,
         8000000, None],
        [2, _d(2025, 9, 4), 'Сервис ФХ 2', 60.0, 200000, 12000000, None, 0,
         None],
    ]),
])
FILE_SERVIS_2 = ('Сервис Дрон Маълумот (2).xlsx', '2025-09', [
    ('свод ичи (Шахзод)', [
        ['Справка (сентябрь ойи)'],
        HEADER,
        [1, _d(2025, 9, 4), 'Сервис ФХ 2', 60.0, 200000, 12000000, None, 0,
         None],
        [2, _d(2025, 9, 5), 'Сервис ФХ 3', 25.0, 200000, 5000000, None, 0,
         None],
    ]),
])

# An internal block, an internal customer, and an operator short form that
# stays ambiguous because the file name names no subdivision.
FILE_INTERNAL = ('Агрокластер Дрон маълумот Март.xlsx', '2026-03', [
    ('свод ичи (Шахзод)', [
        ['Тизим корхонаси (март ойи)'],
        HEADER,
        [1, _d(2026, 3, 10), 'Бухоро Агрокластер бош ер', 50.0, 85633,
         4281650, None, 0, None],
        [2, _d(2026, 3, 12), 'Тизим корхонаси Ромитан', 35.0, 76458, 2676030,
         None, 0, None],
    ]),
])

# (e) the missing dot, (g) an empty sheet title, and the «Мадон» typo header.
FILE_TYPO = ('Шофиркон ПТЗ Дрон маълумот.xlsx', '2025-09', [
    ('свод ичи ', [
        ['Нақд (сентябрь ойи)'],
        HEADER_TYPO,
        [1, '08-09.092025', 'Шофиркон ФХ 1', 18.0, 200000, 3600000, None,
         3600000, None],
    ]),
])

# Rows that appear ABOVE any block marker. Since 2026-08-04 they are imported
# with payment_type = 'unknown' rather than rejected: the September and
# October detail sheets carry no markers at all and 360 real rows were being
# thrown away over the one field that was missing.
FILE_NO_BLOCK = ('Гарден Дрон маълумот Март.xlsx', '2026-03', [
    ('свод ичи (Фурқат)', [
        HEADER,
        [1, _d(2026, 3, 5), 'Гарден ФХ 1', 7.0, 200000, 1400000, None, 0,
         None],
        ['Нақд (март ойи)'],
        [2, _d(2026, 3, 6), 'Гарден ФХ 2', 8.0, 200000, 1600000, None, 0,
         None],
    ]),
])

# Deliberately absent from the manifest: the tool must refuse to process it.
FILE_UNLISTED = ('Ноаниқ файл.xlsx', None, [
    ('свод ичи (Ким)', [
        ['Нақд (март ойи)'],
        HEADER,
        [1, _d(2026, 3, 1), 'Ҳеч ким', 999.0, 200000, 199800000, None, 0,
         None],
    ]),
])

BOOKS = (FILE_GARDEN_APRIL, FILE_KOGON_MARCH, FILE_PESHKU, FILE_SERVIS_1,
         FILE_SERVIS_2, FILE_INTERNAL, FILE_TYPO, FILE_NO_BLOCK,
         FILE_UNLISTED)

# What a correct parser must produce over the eight manifest-listed books.
# Hand-computed from the tables above; the tests assert against these, and
# the negative controls assert they move.
# The pre-block row of FILE_NO_BLOCK is the 22nd: since 2026-08-04 it is
# imported as 'unknown' instead of being rejected, so the corpus is one row
# and 7.00 ha larger than it was under DRONE-WORKS-001.
EXPECTED = {
    'rows': 22,
    'area': 678.09,
    'duplicates': 4,
    # The pre-block row is no longer rejected, so only the currency rate is.
    'rejections': 1,
    'wage_rows': 2,
    'files_skipped_no_manifest': 1,
    'distinct_customers': 22,
    'payments': {'cash': 11, 'transfer': 8, 'internal': 2, 'unknown': 1},
    'payment_area': {'cash': 383.59, 'transfer': 202.5, 'internal': 85.0,
                     'unknown': 7.0},
    'kinds': {'date': 13, 'span': 5, 'none': 2, 'unparsed': 2},
    'operators_matched': 19,
    'operators_unresolved': 3,
    # 12 220 ha in one row -- what trap (b) costs when the guard is removed.
    'junk_area': 12220.0,
}

# The fleet directory the resolution rules are tested against. Two genuine
# ambiguities, exactly the ones the task names: «Шахзод» is Kholmurodov in
# the Ghijduvon books and Boltaev in the Servis books; «Шохрух» is Khamroev
# in Kogon and Fayzullaev in Garden.
OPERATORS = (
    ('Файзуллаев Фурқат', 'Гарден'),
    ('Хамроев Шохрух', 'Когон'),
    ('Файзуллаев Шохрух', 'Гарден'),
    ('Холмуродов Шахзод', 'Ғиждувон'),
    ('Болтаев Шахзод', 'Сервис'),
    ('Имомов Беҳзод', 'Пешку'),
    ('Жўраев Туйғун', 'Шофиркон'),
    ('Қодиров Нурали', 'Гарден'),
)


# ─── The nine cases of DRONE-WORKS-IMPORT-FIX-001 ────────────────────────────
#
# One book per case, in their own corpus so the eight originals keep their
# hand-checked numbers. Each is built from a REAL header layout.

# Case 1: price and amount in adjacent columns, both containing «сумма».
# Case 2: a sheet carrying «Хизмат кўрсатиш санаси» AND «Хизмат кўрсатиш
#         суммаси» -- two headers that differ by two letters, one a date and
#         one a price.
FIX_PRICE_AMOUNT = ('Когон ПТЗ Дрон маълумот.xlsx', '2025-10', [
    ('свод ичи (Нурали)', [
        ['Нақд (октябрь ойи)'],
        ['№', 'Хизмат кўрсатиш санаси', 'ФХ номи', 'Майдон (га)',
         'Хизмат кўрсатиш суммаси', 'Жами сумма (обьём)', 'Кирим қилинган'],
        [1, _d(2025, 10, 3), 'Когон ФХ 1', 10.0, 200000, 2000000, 2000000],
        [2, _d(2025, 10, 4), 'Когон ФХ 2', 5.0, 250000, 1250000, 0],
    ]),
])

# Case 3: a sheet with NO payment block at all -- header, then data. This is
# the September/October shape that produced 360 rejections.
# Case 9: «Ишлаган контур рақами» is a known-ignored header; «Бригада» matches
#         nothing and must be listed rather than guessed at.
FIX_NO_BLOCK = ('Шофиркон ПТЗ Дрон Октябрь.xlsx', '2025-10', [
    ('свод ичи (Туйғун)', [
        ['№', 'Дрон ишлаган сана', 'ФХ номи', 'Майдон (га)',
         'Хизмат кўрсатиш суммаси', 'Жами сумма (обьём)',
         'Ишлаган контур рақами', 'Бригада'],
        [1, _d(2025, 10, 6), 'Шофиркон ФХ А', 12.0, 200000, 2400000, 17,
         'Бригада 1'],
        [2, _d(2025, 10, 7), 'Шофиркон ФХ Б', 8.0, 200000, 1600000, 18,
         'Бригада 1'],
    ]),
])

# Case 4: a row with an empty customer, at the internal tariff. All nine real
# ones look exactly like this.
FIX_EMPTY_CUSTOMER = ('Агрокластер Дрон маълумот МАЙ.xlsx', '2026-05', [
    ('свод ичи (Сайфулло)', [
        ['Тизим корхонаси (май ойи)'],
        HEADER,
        [1, _d(2026, 5, 4), None, 24.0, 85632.96, 2055191.04, None, 0, None],
        [2, _d(2026, 5, 5), 'Ромитан бош ер', 16.0, 85632.96, 1370127.36,
         None, 0, None],
    ]),
])

# Case 5: a detail sheet and an «олинмаган пуллар» sheet sharing a row, plus a
# «ЖАМИ:» total line in the second. Only the detail sheet is imported.
FIX_SUMMARY_SHEETS = ('Ғиждувон ПТЗ Дрон маълумот.xlsx', '2025-09', [
    ('свод ичи (Шахзод)', [
        ['Нақд (сентябрь ойи)'],
        HEADER,
        [1, _d(2025, 9, 8), 'Бахром Хайрулло фх', 33.5, 200000, 6700000, None,
         6700000, None],
    ]),
    ('олинмаган пуллар', [
        HEADER,
        [1, _d(2025, 9, 8), 'Бахром Хайрулло фх', 33.5, 200000, 6700000, None,
         0, None],
        ['ЖАМИ:', '', '', 33.5, '', 6700000, '', '', ''],
    ]),
    ('ОБШИЙ СВОД Шохрух1', [
        HEADER,
        [1, _d(2025, 9, 8), 'Свод хужалиги', 99.0, 200000, 19800000, None, 0,
         None],
    ]),
    ('свод фх', [
        HEADER,
        [1, _d(2025, 9, 9), 'Свод ФХ хужалиги', 77.0, 200000, 15400000, None,
         0, None],
    ]),
])

# Case 6: two expense columns in one sheet. other_costs is their SUM.
FIX_TWO_EXPENSES = ('Сервис Дрон маълумот МАЙ.xlsx', '2026-05', [
    ('свод ичи (Шахбоз)', [
        ['Справка (май ойи)'],
        ['№', 'Дрон ишлаган сана', 'ФХ номи', 'Майдон (га)',
         'Хизмат кўрсатиш суммаси', 'Жами сумма (обьём)', 'ЁММ харажати',
         'Транспорт харажати (Мерс 80 L 422 MA)', 'Кирим қилинган'],
        [1, _d(2026, 5, 11), 'Сервис Май ФХ', 20.0, 200000, 4000000, 120000,
         80000, 0],
    ]),
])

# Case 7: «Оператор топшириши керак» is what the operator still owes -- it is
# not «Кирим қилинган», and the two sit in the same position.
FIX_OPERATOR_DUE = ('Гарден Дрон маълумот Май.xlsx', '2026-05', [
    ('свод ичи (Фурқат)', [
        ['Нақд (май ойи)'],
        ['№', 'Дрон ишлаган сана', 'ФХ номи', 'Майдон (га)',
         'Хизмат кўрсатиш суммаси', 'Жами сумма (обьём)',
         'Оператор топшириши керак'],
        [1, _d(2026, 5, 14), 'Гарден Май ФХ', 15.0, 200000, 3000000, 3000000],
    ]),
])

# Case 8: a per-row «Дрон бошқарувчи оператор» in full, on a sheet whose title
# says only «Шахзод» -- which is ambiguous between two people.
FIX_ROW_OPERATOR = ('Гарден Агрокластер Дрон Октябрь.xlsx', '2025-10', [
    ('свод ичи (Шахзод)', [
        ['Нақд (октябрь ойи)'],
        ['№', 'Дрон ишлаган сана', 'ФХ номи', 'Майдон (га)',
         'Хизмат кўрсатиш суммаси', 'Жами сумма (обьём)',
         'Дрон бошқарувчи оператор'],
        [1, _d(2025, 10, 12), 'Гарден Окт ФХ 1', 11.0, 200000, 2200000,
         'Қодиров Нурали'],
        # The cell is empty -> falls back to the sheet title, which stays
        # ambiguous. Both paths in one sheet.
        [2, _d(2025, 10, 13), 'Гарден Окт ФХ 2', 9.0, 200000, 1800000, None],
    ]),
])

FIX_BOOKS = (FIX_PRICE_AMOUNT, FIX_NO_BLOCK, FIX_EMPTY_CUSTOMER,
             FIX_SUMMARY_SHEETS, FIX_TWO_EXPENSES, FIX_OPERATOR_DUE,
             FIX_ROW_OPERATOR)

# Hand-computed over the seven fix books.
#   price/amount 10.0 + 5.0                      = 15.0
#   no block     12.0 + 8.0                      = 20.0
#   empty cust.  24.0 + 16.0                     = 40.0
#   summaries    33.5 (detail sheet only)        = 33.5
#   two expenses 20.0                            = 20.0
#   operator due 15.0                            = 15.0
#   row operator 11.0 + 9.0                      = 20.0
FIX_EXPECTED = {
    'rows': 11,
    'area': 163.5,
    'skipped_sheets': 3,          # олинмаган пуллар, ОБШИЙ СВОД …, свод фх
    'unknown_payment_rows': 2,    # the no-block sheet
    'empty_customer_rows': 1,
    'operator_from_row': 1,
    'operator_from_sheet': 10,
    'duplicates': 0,
    'rejections': 0,
    'unmatched_headers': 1,       # «Бригада»
    'received_kinds': {'received': 6, 'operator_due': 1},
    # What the three skipped sheets would have added if the tool still read
    # every sheet: 99.0 + 77.0 from the two summaries, and 33.5 duplicated
    # out of «олинмаган пуллар».
    'summary_sheet_area': 209.5,
}


def build(out_dir, books=BOOKS, manifest_name='manifest.txt'):
    """Write the workbooks and the manifest. Returns (dir, manifest path)."""
    from openpyxl import Workbook

    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    manifest_lines = []
    for file_name, period, sheets in books:
        wb = Workbook()
        wb.remove(wb.active)
        for sheet_name, rows in sheets:
            ws = wb.create_sheet(sheet_name)
            for row in rows:
                ws.append(row)
        wb.save(os.path.join(out_dir, file_name))
        if period:
            manifest_lines.append('%s  %s' % (file_name, period))

    manifest_path = os.path.join(out_dir, manifest_name)
    with open(manifest_path, 'w', encoding='utf-8') as fh:
        fh.write('# synthetic manifest -- tools/drone_works_fixtures.py\n')
        fh.write('\n'.join(manifest_lines) + '\n')
    return out_dir, manifest_path


def build_fix(out_dir):
    """The seven books of DRONE-WORKS-IMPORT-FIX-001, in their own corpus."""
    return build(out_dir, books=FIX_BOOKS)


def build_database(path):
    """A throwaway SQLite database with the three tables and the operators.

    The DDL is taken from migrate_drones_works_001.py itself rather than
    re-typed, so a fixture cannot drift away from the migration, and
    migrate_drones_works_002.py is then applied on top of it for the same
    reason -- received_kind must exist exactly as production will have it.
    """
    import sqlite3

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    import migrate_drones_works_001 as works_mig
    import migrate_drones_works_002 as kind_mig

    con = sqlite3.connect(path)
    try:
        con.execute('CREATE TABLE users (id INTEGER PRIMARY KEY)')
        con.execute('CREATE TABLE drone_operators (id INTEGER PRIMARY KEY, '
                    'full_name TEXT NOT NULL, subdivision_name TEXT)')
        con.execute('CREATE TABLE drone_units (id INTEGER PRIMARY KEY, '
                    'number INTEGER, subdivision_name TEXT)')
        con.execute(works_mig.CREATE_DRONE_CUSTOMERS)
        con.execute(works_mig.CREATE_DRONE_CUSTOMER_ALIASES)
        con.execute(works_mig.CREATE_DRONE_WORKS)
        for statement in works_mig.CREATE_INDEXES:
            con.execute(statement)
        for column, ddl_type in kind_mig.NEW_WORK_COLUMNS:
            con.execute('ALTER TABLE drone_works ADD COLUMN %s %s'
                        % (column, ddl_type))
        for full_name, subdivision in OPERATORS:
            con.execute('INSERT INTO drone_operators (full_name, '
                        'subdivision_name) VALUES (?, ?)',
                        (full_name, subdivision))
        con.commit()
    finally:
        con.close()
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--out', required=True, help='output directory')
    parser.add_argument('--fix', action='store_true',
                        help='build the DRONE-WORKS-IMPORT-FIX-001 corpus')
    parser.add_argument('--db', default=None,
                        help='also build a throwaway database at this path')
    args = parser.parse_args(argv)
    if args.fix:
        out_dir, manifest = build_fix(args.out)
        expected = FIX_EXPECTED
    else:
        out_dir, manifest = build(args.out)
        expected = EXPECTED
    print('books written to %s' % out_dir.encode('ascii', 'backslashreplace')
          .decode('ascii'))
    print('manifest: %s' % manifest.encode('ascii', 'backslashreplace')
          .decode('ascii'))
    if args.db:
        build_database(args.db)
        print('database: %s' % args.db)
    print('expected: %d rows, %.2f ha'
          % (expected['rows'], expected['area']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
