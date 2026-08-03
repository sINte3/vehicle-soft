#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/drone_works_fixtures.py -- synthetic books for DRONE-WORKS-001.

The dispatchers' real files are not in the repository and will not be, so the
import tool is proved against these instead. Every trap listed in the task
(2.2 a..h) has a file here that reproduces it, and
tools/test_import_drone_works.py runs each trap twice: once with the guard and
once with the guard replaced by the naive version, asserting that the naive
version produces a DIFFERENT and WRONG number. A guard with no failing
negative control is not covered.

The numbers are small and hand-checkable on purpose -- 21 rows and 671.09 ha
across eight books, not a scaled-down copy of 12 767 ha. The point is that a
human can add them up in the report and see the same figure.

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

# The header the real books use, including the «Мадон» typo variant which
# lives in FILE_TYPO below.
HEADER = ['№', 'Сана', 'ФХ номи', 'Майдон (га)', '1 га нархи', 'Сумма',
          'Бошка харажатлар', 'Кирим қилинган', 'Изоҳ']
HEADER_TYPO = ['№', 'Сана', 'ФХ номи', 'Мадон (га)', '1 га нархи', 'Сумма',
               'Бошка харажатлар', 'Кирим қилинган', 'Изоҳ']


def _d(year, month, day):
    return datetime.datetime(year, month, day)


# ─── The eight books ─────────────────────────────────────────────────────────

# (a) a data row whose «Изоҳ» mentions «майдон»; (b) junk below the totals;
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

# Rows that appear ABOVE any block marker. Rejected and listed rather than
# given a payment type nobody wrote down; --default-payment is the override.
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
EXPECTED = {
    'rows': 21,
    'area': 671.09,
    'duplicates': 4,
    'rejections': 2,
    'wage_rows': 2,
    'files_skipped_no_manifest': 1,
    'distinct_customers': 21,
    'payments': {'cash': 11, 'transfer': 8, 'internal': 2},
    'payment_area': {'cash': 383.59, 'transfer': 202.5, 'internal': 85.0},
    'kinds': {'date': 12, 'span': 5, 'none': 2, 'unparsed': 2},
    'operators_matched': 18,
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
)


def build(out_dir):
    """Write the workbooks and the manifest. Returns (dir, manifest path)."""
    from openpyxl import Workbook

    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    manifest_lines = []
    for file_name, period, sheets in BOOKS:
        wb = Workbook()
        wb.remove(wb.active)
        for sheet_name, rows in sheets:
            ws = wb.create_sheet(sheet_name)
            for row in rows:
                ws.append(row)
        wb.save(os.path.join(out_dir, file_name))
        if period:
            manifest_lines.append('%s  %s' % (file_name, period))

    manifest_path = os.path.join(out_dir, 'manifest.txt')
    with open(manifest_path, 'w', encoding='utf-8') as fh:
        fh.write('# synthetic manifest -- tools/drone_works_fixtures.py\n')
        fh.write('\n'.join(manifest_lines) + '\n')
    return out_dir, manifest_path


def build_database(path):
    """A throwaway SQLite database with the three tables and the operators.

    The DDL is taken from migrate_drones_works_001.py itself rather than
    re-typed, so a fixture cannot drift away from the migration.
    """
    import sqlite3

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    import migrate_drones_works_001 as works_mig

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
    parser.add_argument('--db', default=None,
                        help='also build a throwaway database at this path')
    args = parser.parse_args(argv)
    out_dir, manifest = build(args.out)
    print('books written to %s' % out_dir.encode('ascii', 'backslashreplace')
          .decode('ascii'))
    print('manifest: %s' % manifest.encode('ascii', 'backslashreplace')
          .decode('ascii'))
    if args.db:
        build_database(args.db)
        print('database: %s' % args.db)
    print('expected: %d rows, %.2f ha, %d duplicates, %d rejections'
          % (EXPECTED['rows'], EXPECTED['area'], EXPECTED['duplicates'],
             EXPECTED['rejections']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
