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
        "       COALESCE(SUM(w.amount), 0), COALESCE(o.full_name, '') "
        'FROM drone_works w '
        'LEFT JOIN drone_operators o ON o.id = w.drone_operator_id ' + where +
        'GROUP BY 1, 2, 3, 7 ORDER BY 1, 2, 3', params).fetchall()


NAMESAKE = 'namesake'
CANDIDATE = 'candidate'
UNKNOWN = 'unknown'


def find_duplicates(rows):
    """Листы с одним именем из разных файлов -- и КТО за ними стоит.

    Возвращает {(месяц, лист): {'files': [...], 'kind': NAMESAKE|CANDIDATE}}.
    """
    by_sheet = defaultdict(lambda: defaultdict(
        lambda: {'rows': 0, 'ha': 0.0, 'amount': 0.0, 'operators': set()}))
    for (month, sheet, source_file, count, hectares, amount,
         operator) in rows:
        # [REASON]: строка, набранная руками, не имеет ни файла, ни листа.
        # Она законно «ниоткуда», и складывать такие в одну группу значит
        # объявить двойной загрузкой всю ручную правку месяца.
        if not sheet or not source_file:
            continue
        agg = by_sheet[(month, sheet)][source_file]
        agg['rows'] += count
        agg['ha'] += float(hectares)
        agg['amount'] += float(amount)
        if operator:
            agg['operators'].add(operator)

    out = {}
    for key, files in by_sheet.items():
        if len(files) < 2:
            continue
        # [REASON]: ЛИСТ-ТЁЗКА, А НЕ ДУБЛЬ. «свод ичи (Шохрух)» есть и в
        # книге Гардена (Файзуллаев Шохрух), и в книге Когона (Хамроев
        # Шохрух) -- это книги ДВУХ РАЗНЫХ ЛЮДЕЙ с одинаковым именем листа.
        # Первая редакция отчёта объявила гарденскую книгу лишней и
        # предложила удалить 154.70 га настоящих работ. Отличает их
        # оператор: у двойной загрузки он один и тот же по обе стороны.
        operators = [tuple(sorted(agg['operators'])) for agg in files.values()]
        # [REASON]: сторона БЕЗ ОПЕРАТОРА не свидетельствует ни за, ни против.
        # В апреле и мае 2026 «свод ичи (Шахзод)» приходит из книги Ғиждувона
        # (Холмуродов) и из книги Сервиса, где оператор не привязан вовсе.
        # Первая редакция считала «нет оператора» за «оператор тот же» и
        # предлагала удалить 492.15 га как дубль. Молчание -- не согласие:
        # такие группы идут в UNKNOWN, и DELETE по ним не печатается.
        if any(not op for op in operators):
            kind = UNKNOWN
        elif len(set(operators)) <= 1:
            kind = CANDIDATE
        else:
            kind = NAMESAKE
        out[key] = {'files': sorted(
            (name, agg['rows'], agg['ha'], agg['amount'],
             ', '.join(sorted(agg['operators'])) or '(без оператора)')
            for name, agg in files.items()),
            'kind': kind}
    return out


def report_lines(duplicates):
    lines = []
    for (month, sheet), group in sorted(duplicates.items()):
        tag = {CANDIDATE: 'CANDIDATE double upload',
               NAMESAKE: 'NAMESAKE sheets -- DIFFERENT PEOPLE, not a '
                         'duplicate',
               UNKNOWN: 'UNKNOWN -- one side has no operator, cannot tell; '
                        'no SQL offered'}[group['kind']]
        lines.append('%s  %s   [%s]' % (month, _ascii(sheet), tag))
        for source_file, count, hectares, amount, operator in group['files']:
            lines.append('    %-40s %4d rows %9.2f ha %14.0f  %s'
                         % (_ascii(source_file)[-40:], count, hectares,
                            amount, _ascii(operator)[:26]))
    return lines


def candidates(duplicates):
    """Только группы, где по обе стороны ОДИН И ТОТ ЖЕ оператор.

    [REASON]: отчёт НЕ выбирает, какой из двух файлов лишний, и не пытается.
    Первая редакция считала полной загрузку с наибольшим числом строк -- на
    апреле 2026 это назвало бы лишним файл с 361.30 га в пользу файла с
    492.15, а на мае -- файл с 548.10 га в пользу файла с 226.51. Число
    строк не говорит, какая загрузка верна; говорит подписанный ОБЩИЙ СВОД,
    и смотрит в него человек. Поэтому печатаются ОБА варианта как
    альтернативы, и выбрать надо ровно один.
    """
    out = []
    for (month, sheet), group in sorted(duplicates.items()):
        if group['kind'] != CANDIDATE:
            continue
        for source_file, count, hectares, amount, operator in group['files']:
            out.append((month, sheet, source_file, count, hectares, amount,
                        operator))
    return out


def of_kind(duplicates, kind):
    """Группы одного вида: тёзки или неопознанные."""
    return [(month, sheet, duplicates[(month, sheet)]['files'])
            for (month, sheet) in sorted(duplicates)
            if duplicates[(month, sheet)]['kind'] == kind]


def namesakes(duplicates):
    """Группы, где за одинаковым именем листа стоят РАЗНЫЕ люди."""
    return of_kind(duplicates, NAMESAKE)


def unknowns(duplicates):
    """Группы, где хотя бы у одной стороны оператор не привязан."""
    return of_kind(duplicates, UNKNOWN)


def sql_lines(items):
    lines = []
    for month, sheet, source_file, _count, _ha, _amount, _op in items:
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
    ws.append(['Месяц', 'Лист-источник', 'Файл-источник', 'Оператор',
               'Строк', 'Га', 'Сумма', 'Вердикт'])
    for cell in ws[1]:
        cell.font = bold
    for (month, sheet), group in sorted(duplicates.items()):
        verdict = ('двойная загрузка?' if group['kind'] == CANDIDATE
                   else 'ТЁЗКИ, РАЗНЫЕ ЛЮДИ -- не дубль')
        for source_file, count, hectares, amount, operator in group['files']:
            ws.append([month, sheet, source_file, operator, count,
                       round(hectares, 2), round(amount, 2), verdict])
    ws = wb.create_sheet('Все источники')
    ws.append(['Месяц', 'Лист-источник', 'Файл-источник', 'Оператор', 'Строк',
               'Га', 'Сумма'])
    for cell in ws[1]:
        cell.font = bold
    for (month, sheet, source_file, count, hectares, amount,
         operator) in rows:
        ws.append([month, sheet, source_file, operator, count,
                   round(float(hectares), 2), round(float(amount), 2)])
    for sheet_obj in wb.worksheets:
        for column, width in (('A', 10), ('B', 26), ('C', 46), ('D', 24),
                              ('E', 8), ('F', 11), ('G', 15), ('H', 30)):
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

    items = candidates(duplicates)
    twins = namesakes(duplicates)
    murky = unknowns(duplicates)
    print('Candidate duplicates: %d sheet(s), %d file(s)'
          % (len({(i[0], i[1]) for i in items}), len(items)))
    print('Namesake sheets     : %d  (different people -- NOT duplicates)'
          % len(twins))
    print('Undecidable         : %d  (one side has no operator -- no SQL)'
          % len(murky))
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
        handle.write('-- ОТЧЁТ НЕ ЗНАЕТ, какая из двух загрузок верна: это\n')
        handle.write('-- говорит подписанный ОБЩИЙ СВОД. По каждому листу\n')
        handle.write('-- ниже ДВА варианта -- выполнить надо РОВНО ОДИН,\n')
        handle.write('-- и только после копии instance\\transport.db.\n')
        for month, sheet, group in [(m, sh, duplicates[(m, sh)])
                                    for (m, sh) in sorted(duplicates)
                                    if duplicates[(m, sh)]['kind']
                                    == CANDIDATE]:
            handle.write('\n\n-- ================================\n')
            handle.write('-- %s  %s\n' % (month, sheet))
            for source_file, count, hectares, amount, operator in \
                    group['files']:
                handle.write('--   %s : %d строк, %.2f га, %.0f сум, %s\n'
                             % (source_file, count, hectares, amount,
                                operator))
            pairs = sql_lines([(month, sheet, f[0], f[1], f[2], f[3], f[4])
                               for f in group['files']])
            for (source_file, count, hectares, _a, _o), (select, delete) in \
                    zip(group['files'], pairs):
                handle.write('\n-- ВАРИАНТ: убрать %s (%d строк, %.2f га)\n'
                             % (source_file, count, hectares))
                handle.write('-- сначала посмотреть:\n')
                handle.write(select + '\n')
                handle.write('-- и только потом, если это лишняя загрузка:\n')
                handle.write('-- ' + delete + '\n')
        for month, sheet, files in twins:
            handle.write('\n\n-- ================================\n')
            handle.write('-- %s  %s -- ТЁЗКИ, НЕ ДУБЛЬ. Ничего не удалять.\n'
                         % (month, sheet))
            for source_file, count, hectares, _amount, operator in files:
                handle.write('--   %s : %d строк, %.2f га, %s\n'
                             % (source_file, count, hectares, operator))
        for month, sheet, files in murky:
            handle.write('\n\n-- ================================\n')
            handle.write('-- %s  %s -- НЕ ОПОЗНАНО. У одной стороны оператор '
                         'не привязан,\n-- и отличить двойную загрузку от '
                         'листа-тёзки нечем. SQL не предлагается:\n'
                         '-- сначала привязать оператора, потом запустить '
                         'отчёт заново.\n' % (month, sheet))
            for source_file, count, hectares, _amount, operator in files:
                handle.write('--   %s : %d строк, %.2f га, %s\n'
                             % (source_file, count, hectares, operator))

    print('')
    print('NOTHING WAS DELETED -- this report cannot write to the database.')
    print('IT ALSO DOES NOT DECIDE which upload is the surplus one: the row '
          'count does not say that, the signed summary does.')
    print('The SQL is in a UTF-8 file (sheet names are Cyrillic and the '
          'console would mangle them):')
    print('  %s' % _ascii(sql_path))
    print('For each candidate it offers TWO alternatives -- run exactly one, '
          'after copying instance\\transport.db aside.')
    print('Every DELETE is written out COMMENTED. Uncomment the one you '
          'chose.')
    if args.out:
        write_xlsx(args.out, rows, duplicates)
        print('')
        print('Written: %s' % _ascii(args.out))
    return 1


if __name__ == '__main__':
    sys.exit(main())
