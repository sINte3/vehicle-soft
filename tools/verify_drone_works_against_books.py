#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/verify_drone_works_against_books.py -- построчная сверка таблицы
drone_works с книгами диспетчеров. ЧИТАЕТ И НЕ ПИШЕТ НИЧЕГО.

Зачем. Сверка архивов 2026-08-11 показала расхождение, которое нельзя
объяснить дублями: по 28 книгам разбор даёт 913 строк на 2 551 392 505.33
сум, а в базе лежит 904 работы на 2 657 997 448.68 -- при МЕНЬШЕМ числе
строк сумма БОЛЬШЕ на 106 604 943.35. Гипотеза «в базе досчитали суммы
пустым строкам» проверена и отвергнута: таких строк 37, из них с ценой 3,
и они дали бы лишь 6 996 216. Значит расходятся конкретные строки, и найти
их можно только сличением каждой работы с её собственной клеткой в книге.

Как сверяется. У каждой работы есть происхождение: source_file,
source_sheet, source_row. Книга разбирается ТЕМ ЖЕ парсером, которым её
разбирал импорт (tools/import_drone_works.parse_sheet), строки
сопоставляются по этой тройке, и сравниваются пять величин: площадь, цена,
сумма, получено и затраты. Свой парсер здесь не заводится намеренно: он
разошёлся бы с импортом и показал бы расхождения, которых нет.

Что печатается:
  * работы, чьей строки в книге нет вовсе (книга изменилась или переименована);
  * строки книги, которых нет в базе (не доехали при импорте);
  * работы, где хоть одна из пяти величин отличается -- с обоими значениями;
  * итог: сколько сошлось, сколько разошлось, и суммарная разница по каждой
    величине -- та самая, которую надо объяснить.

Сравнение денег и площадей идёт с допуском 0.005: это пыль плавающей точки
(книги хранят 8.699999999999999), а не порог. Величина, которой нет ни там,
ни там, расхождением не считается; величина, которая есть в базе и пуста в
книге (или наоборот), считается -- это и есть искомый случай.

Запуск (сервер, из каталога установки):
  & "C:\\Program Files\\Python314\\python.exe" tools\\verify_drone_works_against_books.py --db instance\\transport.db --dir C:\\qa\\books --manifest tools\\drone_works_manifest.txt

--dir -- каталог с книгами, --manifest -- тот же файл, которым шёл импорт.
Отчёт UTF-8 (--report, по умолчанию рядом с базой) содержит полный список
расхождений; консоль -- только ASCII.

ОТКАТА НЕ ТРЕБУЕТСЯ: база открывается через file:-URI в режиме mode=ro,
ни одного INSERT/UPDATE/DELETE в файле нет.
"""

import argparse
import collections
import os
import sqlite3
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, 'tools'))

import import_drone_works as imp  # noqa: E402

# Пыль плавающей точки, не порог: тот же допуск, что на экране закрытия.
EPS = 0.005

FIELDS = (
    ('area_ha', 'площадь'),
    ('price_per_ha', 'цена за га'),
    ('amount', 'сумма'),
    ('received_amount', 'получено'),
    ('other_costs', 'затраты'),
)


def open_readonly(db_path):
    if not os.path.exists(db_path):
        sys.stderr.write('ERROR: database not found: %s\n' % db_path)
        sys.exit(2)
    uri = 'file:%s?mode=ro' % db_path.replace('?', '%3f').replace('#', '%23')
    return sqlite3.connect(uri, uri=True)


def read_manifest(path):
    manifest = {}
    for line in open(path, encoding='utf-8'):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        name, period = line.rsplit(None, 1)
        manifest[name.strip()] = period.strip()
    return manifest


def parse_books(directory, manifest):
    """{(файл, лист, строка): значения} -- книги глазами импортёра."""
    from openpyxl import load_workbook

    book_rows = {}
    problems = []
    for file_name, period in sorted(manifest.items()):
        full = os.path.join(directory, file_name)
        if not os.path.exists(full):
            problems.append('нет файла: %s' % file_name)
            continue
        try:
            formulas = imp._formula_cells(full)
            wb = load_workbook(full, data_only=True, read_only=True)
        except Exception as exc:                          # noqa: BLE001
            problems.append('%s: %s' % (file_name, exc))
            continue
        try:
            for ws in wb.worksheets:
                if not imp.is_detail_sheet(ws.title):
                    continue
                sheet_formulas = {(r, c) for (title, r, c) in formulas
                                  if title == ws.title}
                rows, _report = imp.parse_sheet(
                    ws, file_name, ws.title, period, None,
                    default_payment=None, formula_cells=sheet_formulas)
                for row in rows:
                    key = (row['source_file'], row['source_sheet'],
                           row['source_row'])
                    book_rows[key] = row
        finally:
            wb.close()
    return book_rows, problems


def read_works(con):
    """Работы, у которых есть происхождение. Ручной ввод сверять не с чем."""
    sql = ('SELECT id, source_file, source_sheet, source_row, area_ha, '
           'price_per_ha, amount, received_amount, other_costs, customer_raw '
           'FROM drone_works WHERE source_file IS NOT NULL')
    out = {}
    collisions = []
    for row in con.execute(sql):
        key = (row[1], row[2], row[3])
        record = {'id': row[0], 'area_ha': row[4], 'price_per_ha': row[5],
                  'amount': row[6], 'received_amount': row[7],
                  'other_costs': row[8], 'customer_raw': row[9]}
        if key in out:
            # [REASON]: происхождение не уникально в схеме, и импорт
            # намеренно пропускает дубли. Две работы на одну клетку книги --
            # находка сама по себе, поэтому она называется, а не молчит.
            collisions.append((key, out[key]['id'], record['id']))
        out[key] = record
    return out, collisions


def differs(a, b):
    if a is None and b is None:
        return False
    if a is None or b is None:
        return True
    return abs(float(a) - float(b)) > EPS


def main():
    parser = argparse.ArgumentParser(
        description='Row-by-row check of drone_works against the books. '
                    'Read-only: writes nothing.')
    parser.add_argument('--db', required=True, help='path to transport.db')
    parser.add_argument('--dir', required=True, help='directory with books')
    parser.add_argument('--manifest', required=True, help='import manifest')
    parser.add_argument('--report', help='UTF-8 report (default: next to db)')
    args = parser.parse_args()

    manifest = read_manifest(args.manifest)
    book_rows, problems = parse_books(args.dir, manifest)

    con = open_readonly(args.db)
    try:
        works, collisions = read_works(con)
    finally:
        con.close()

    lines = []
    add = lines.append
    add('Сверка drone_works с книгами диспетчеров -- ТОЛЬКО ЧТЕНИЕ')
    add('=' * 78)
    add('база   : %s' % os.path.abspath(args.db))
    add('книги  : %s (по манифесту %d файлов)' % (os.path.abspath(args.dir),
                                                  len(manifest)))
    add('строк в книгах: %d | работ с происхождением в базе: %d'
        % (len(book_rows), len(works)))
    for p in problems:
        add('ПРОБЛЕМА РАЗБОРА: %s' % p)
    for key, first, second in collisions:
        add('ДВЕ РАБОТЫ НА ОДНУ СТРОКУ КНИГИ: %s -> id %s и id %s'
            % (' | '.join(str(k) for k in key), first, second))
    add('')

    only_db = sorted(set(works) - set(book_rows))
    only_book = sorted(set(book_rows) - set(works))
    both = sorted(set(works) & set(book_rows))

    mismatches = collections.defaultdict(list)
    delta = collections.Counter()
    equal_rows = 0
    for key in both:
        w, b = works[key], book_rows[key]
        row_bad = False
        for field, label in FIELDS:
            if differs(w[field], b.get(field)):
                row_bad = True
                mismatches[field].append((key, w, b))
                delta[field] += (float(w[field] or 0.0)
                                 - float(b.get(field) or 0.0))
        if not row_bad:
            equal_rows += 1

    add('СОВПАЛИ ПОЛНОСТЬЮ: %d работ' % equal_rows)
    add('РАСХОДЯТСЯ       : %d работ'
        % len({k for f, _ in FIELDS for k, _w, _b in mismatches[f]}))
    add('ТОЛЬКО В БАЗЕ    : %d (строки книги не найдено)' % len(only_db))
    add('ТОЛЬКО В КНИГАХ  : %d (в базу не попало)' % len(only_book))
    add('')
    add('РАЗНИЦА БАЗА МИНУС КНИГА, по величинам:')
    for field, label in FIELDS:
        add('  %-12s %+18.2f   (расходящихся строк: %d)'
            % (label, delta[field], len(mismatches[field])))
    add('')

    for field, label in FIELDS:
        rows = mismatches[field]
        if not rows:
            continue
        add('-' * 78)
        add('РАСХОЖДЕНИЯ ПО «%s» (%d)' % (label.upper(), len(rows)))
        rows.sort(key=lambda item: -abs(float(item[1][field] or 0.0)
                                        - float(item[2].get(field) or 0.0)))
        for key, w, b in rows:
            add('  %s | лист %s | строка %s' % key)
            add('      заказчик: %s' % (w['customer_raw'] or b.get('customer_raw')))
            add('      в базе: %s   в книге: %s   разница: %+.2f'
                % (w[field], b.get(field),
                   float(w[field] or 0.0) - float(b.get(field) or 0.0)))

    if only_db:
        add('-' * 78)
        add('РАБОТЫ, ЧЬЕЙ СТРОКИ В КНИГЕ НЕТ (%d)' % len(only_db))
        for key in only_db:
            # [REASON]: имя заказчика печатается здесь обязательно. Без него
            # строка «id 914, файл, лист, строка 999» не находится ни в
            # книге (её там нет), ни глазами в реестре работ, и находка
            # превращается в загадку.
            add('  id %-6s %s | лист %s | строка %s | %s'
                % (works[key]['id'], key[0], key[1], key[2],
                   works[key]['customer_raw']))
    if only_book:
        add('-' * 78)
        add('СТРОКИ КНИГ, КОТОРЫХ НЕТ В БАЗЕ (%d)' % len(only_book))
        for key in only_book:
            b = book_rows[key]
            add('  %s | лист %s | строка %s | %s | га %s | сумма %s'
                % (key[0], key[1], key[2], b.get('customer_raw'),
                   b.get('area_ha'), b.get('amount')))

    add('')
    add('Скрипт ничего не изменил: база открыта только на чтение.')

    report_path = args.report or os.path.join(
        os.path.dirname(os.path.abspath(args.db)),
        'drone_works_vs_books.txt')
    with open(report_path, 'w', encoding='utf-8') as handle:
        handle.write('\n'.join(lines) + '\n')
    for line in lines:
        print(line.encode('ascii', 'backslashreplace').decode('ascii'))
    print('report written: %s' % report_path)
    return 0


if __name__ == '__main__':
    sys.exit(main())
