#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""drone_import_duplicates.py -- DRONE-IMPORT-DUP-001, только чтение.

Одна и та же книга диспетчера, загруженная ДВАЖДЫ, удваивает гектары и
деньги -- и импортёр этого не замечает.

КАК ЭТО ВЫГЛЯДИТ. Уникальность строки в drone_works -- это тройка
(source_file, source_sheet, source_row). Если ту же книгу загрузить вторым
файлом, у которого браузер приписал к имени « (2)», тройка становится другой,
и обе загрузки живут рядом. В сентябре 2025 это дало +118.40 га:

    Сервис Дрон Маълумот (2).xlsx  свод ичи (Мухриддин)  11 строк  205.00
    Сервис Дрон Маълумот.xlsx      свод ичи (Мухриддин)   6 строк   93.60  <-
    Сервис Дрон Маълумот (2).xlsx  свод ичи (Фурқат)     37 строк  398.90
    Сервис Дрон Маълумот.xlsx      свод ичи (Фурқат)      9 строк   24.80  <-

Подписанные ОБЩИЕ СВОДЫ дают Кудратову 205.00 и Жумаеву 398.90 -- ровно то,
что лежит в файле «(2)». Строки из файла без суффикса -- остаток первой,
неполной загрузки.

ЧТО ДЕЛАЕТ ОТЧЁТ. По каждому месяцу ищет ЛИСТЫ С ОДНИМ ИМЕНЕМ, пришедшие из
РАЗНЫХ файлов. Это и есть след двойной загрузки: один и тот же лист книги не
может законно прийти из двух файлов.

[REASON]: сравнение идёт по ЛИСТУ, а не по имени файла. Имена файлов у
подразделений разные и меняются от месяца к месяцу, а имя листа -- «свод ичи
(Мухриддин)» -- это название книги одного человека, и оно устойчиво. Сравнение
по «похожим именам файлов» пропустило бы переименованную копию и подняло бы
ложную тревогу на честно разных книгах.

ЧЕГО ОТЧЁТ НЕ ДЕЛАЕТ. Он НИЧЕГО НЕ УДАЛЯЕТ и не может: база открывается
read-only. Устав проекта запрещает удалять продовые данные автоматически.
Отчёт печатает готовый SELECT, чтобы владелец посмотрел строки глазами, и
готовый DELETE, чтобы он выполнил его сам, своей рукой, после резервной копии.

Запуск (сервер, служба может работать):
  & "C:\\Program Files\\Python314\\python.exe" tools\\drone_import_duplicates.py --db instance\\transport.db

Выход -- в консоль (ASCII) и, при --out, в xlsx.
Код возврата 1, если найдена хотя бы одна двойная загрузка.
"""

import argparse
import os
import sqlite3
import sys
from collections import defaultdict


def _ascii(text):
    return str(text).encode('ascii', 'replace').decode('ascii')


def connect_ro(db_path):
    uri = 'file:%s?mode=ro' % db_path.replace('?', '%3f').replace('#', '%23')
    return sqlite3.connect(uri, uri=True)


def load(conn, month=None):
    """Строки по (месяц, лист, файл). Месяц -- по правилу отчётов."""
    where = ''
    params = ()
    if month:
        where = ("WHERE COALESCE(strftime('%Y-%m', w.work_date_from), "
                 'w.period_month) = ? ')
        params = (month,)
    return conn.execute(
        "SELECT COALESCE(strftime('%Y-%m', w.work_date_from), "
        "                w.period_month) AS m, "
        "       COALESCE(w.source_sheet, ''), COALESCE(w.source_file, ''), "
        '       COUNT(*), COALESCE(SUM(w.area_ha), 0), '
        '       COALESCE(SUM(w.amount), 0) '
        'FROM drone_works w ' + where +
        'GROUP BY 1, 2, 3 ORDER BY 1, 2, 3', params).fetchall()


def find_duplicates(rows):
    """Листы с одним именем, пришедшие из разных файлов, внутри месяца."""
    by_sheet = defaultdict(list)
    for month, sheet, source_file, count, hectares, amount in rows:
        # [REASON]: строка, набранная руками, не имеет ни файла, ни листа.
        # Она законно «ниоткуда», и складывать такие в одну группу значит
        # объявить двойной загрузкой всю ручную правку месяца.
        if not sheet or not source_file:
            continue
        by_sheet[(month, sheet)].append(
            (source_file, count, float(hectares), float(amount)))
    return {key: sorted(files) for key, files in by_sheet.items()
            if len(files) > 1}


def report_lines(duplicates):
    lines = []
    for (month, sheet), files in sorted(duplicates.items()):
        biggest = max(files, key=lambda f: f[1])
        lines.append('%s  %s' % (month, _ascii(sheet)))
        for source_file, count, hectares, amount in files:
            mark = '  KEEP (most rows)' if source_file == biggest[0] \
                else '  <-- SURPLUS'
            lines.append('    %-44s %4d rows %9.2f ha %14.0f%s'
                         % (_ascii(source_file)[-44:], count, hectares,
                            amount, mark))
    return lines


def surplus(duplicates):
    """Строки лишней загрузки: всё, кроме файла с наибольшим числом строк.

    [REASON]: «лишний» здесь -- ТЕХНИЧЕСКАЯ подсказка, а не вердикт. Полной
    считается загрузка с наибольшим числом строк, потому что вторая попытка
    делается ради недостающих строк. Решает человек, сверив с подписанным
    сводом: отчёт печатает SELECT, чтобы он посмотрел строки глазами.
    """
    out = []
    for (month, sheet), files in sorted(duplicates.items()):
        biggest = max(files, key=lambda f: f[1])
        for source_file, count, hectares, amount in files:
            if source_file != biggest[0]:
                out.append((month, sheet, source_file, count, hectares,
                            amount))
    return out


def sql_lines(items):
    lines = []
    for month, sheet, source_file, _count, _ha, _amount in items:
        where = ("WHERE source_file = '%s' AND source_sheet = '%s' "
                 "AND COALESCE(strftime('%%Y-%%m', work_date_from), "
                 "period_month) = '%s'"
                 % (source_file.replace("'", "''"),
                    sheet.replace("'", "''"), month))
        lines.append(('SELECT id, customer_raw, area_ha, amount, source_row '
                      'FROM drone_works %s;' % where,
                      'DELETE FROM drone_works %s;' % where))
    return lines


def write_xlsx(path, rows, duplicates):
    import openpyxl
    from openpyxl.styles import Font
    wb = openpyxl.Workbook()
    bold = Font(bold=True)
    ws = wb.active
    ws.title = 'Двойные загрузки'
    ws.append(['Месяц', 'Лист-источник', 'Файл-источник', 'Строк', 'Га',
               'Сумма', 'Вердикт'])
    for cell in ws[1]:
        cell.font = bold
    for (month, sheet), files in sorted(duplicates.items()):
        biggest = max(files, key=lambda f: f[1])
        for source_file, count, hectares, amount in files:
            ws.append([month, sheet, source_file, count, round(hectares, 2),
                       round(amount, 2),
                       'полная' if source_file == biggest[0] else 'лишняя'])
    ws = wb.create_sheet('Все источники')
    ws.append(['Месяц', 'Лист-источник', 'Файл-источник', 'Строк', 'Га',
               'Сумма'])
    for cell in ws[1]:
        cell.font = bold
    for month, sheet, source_file, count, hectares, amount in rows:
        ws.append([month, sheet, source_file, count, round(float(hectares), 2),
                   round(float(amount), 2)])
    for sheet_obj in wb.worksheets:
        for column, width in (('A', 10), ('B', 26), ('C', 46), ('D', 8),
                              ('E', 11), ('F', 15), ('G', 10)):
            sheet_obj.column_dimensions[column].width = width
    wb.save(path)


def main():
    parser = argparse.ArgumentParser(
        description='Find dispatcher books imported twice. Read-only.')
    parser.add_argument('--db', default=os.path.join('instance',
                                                     'transport.db'))
    parser.add_argument('--month', default=None,
                        help='YYYY-MM; omit to scan every month')
    parser.add_argument('--out', default=None)
    args = parser.parse_args()

    if not os.path.isfile(args.db):
        print('ERROR: database not found: %s' % _ascii(args.db))
        return 2

    conn = connect_ro(args.db)
    try:
        rows = load(conn, args.month)
    finally:
        conn.close()

    duplicates = find_duplicates(rows)
    print('Months scanned      : %s' % (args.month or 'all'))
    print('Source groups       : %d' % len(rows))
    print('Sheets from 2+ files: %d' % len(duplicates))
    if not duplicates:
        print('')
        print('No book looks imported twice.')
        if args.out:
            write_xlsx(args.out, rows, duplicates)
            print('Written: %s' % _ascii(args.out))
        return 0

    items = surplus(duplicates)
    print('Surplus rows        : %d' % sum(i[3] for i in items))
    print('Surplus hectares    : %.2f' % sum(i[4] for i in items))
    print('Surplus amount      : %.0f' % sum(i[5] for i in items))
    print('')
    for line in report_lines(duplicates):
        print(line)
    # [REASON]: имена листов и файлов -- кириллица, а консоль Windows её
    # калечит в «?????». Скопированный оттуда SQL не нашёл бы НИ ОДНОЙ строки
    # и выглядел бы при этом безобидно: «удалено 0 строк». Поэтому SQL пишется
    # в UTF-8 файл, а в консоль идёт только путь к нему -- ровно то, что
    # предписывает устав про скрипты с кириллическим выводом.
    sql_path = os.path.join(os.path.dirname(os.path.abspath(args.db)),
                            'drone_import_duplicates.sql')
    with open(sql_path, 'w', encoding='utf-8') as handle:
        handle.write('-- Сформировано tools/drone_import_duplicates.py\n')
        handle.write('-- СНАЧАЛА посмотреть строки глазами, ПОТОМ удалять,\n')
        handle.write('-- и только после копии instance\\transport.db.\n\n')
        handle.write('-- 1. ПОСМОТРЕТЬ\n')
        for select, _delete in sql_lines(items):
            handle.write(select + '\n')
        handle.write('\n-- 2. УДАЛИТЬ, если это действительно лишняя '
                     'загрузка\n')
        for _select, delete in sql_lines(items):
            handle.write(delete + '\n')

    print('')
    print('NOTHING WAS DELETED -- this report cannot write to the database.')
    print('The SQL is in a UTF-8 file (sheet names are Cyrillic and the '
          'console would mangle them):')
    print('  %s' % _ascii(sql_path))
    print('Look at the SELECT rows first, copy instance\\transport.db aside, '
          'then run the DELETE by hand.')
    if args.out:
        write_xlsx(args.out, rows, duplicates)
        print('')
        print('Written: %s' % _ascii(args.out))
    return 1


if __name__ == '__main__':
    sys.exit(main())
