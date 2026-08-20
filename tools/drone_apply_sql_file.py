#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""drone_apply_sql_file.py -- выполнить то, что владелец РАСКОММЕНТИРОВАЛ.

Отчёт tools/drone_import_duplicates.py кладёт рядом с базой файл
instance/drone_import_duplicates.sql, где по каждому кандидату два варианта,
и оба DELETE выписаны закомментированными. Владелец снимает «-- » с того
варианта, который подтверждён подписанным сводом, -- и вот этим скриптом его
выполняет.

ПОЧЕМУ ОТДЕЛЬНЫЙ СКРИПТ. Ни sqlite3.exe, ни DB Browser на сервере нет, а
вставить кириллический SQL в PowerShell нельзя: консоль калечит имена листов
в «?????», запрос не находит ни строки и рапортует «удалено 0» -- выглядит
благополучно, а не сделано ничего. Именно это и случилось 2026-08-20.

ЧТО ЭТО НЕ АВТОМАТИЧЕСКОЕ УДАЛЕНИЕ. Устав запрещает удалять продовые данные
автоматически, и это правило соблюдено: скрипт НИЧЕГО НЕ ВЫБИРАЕТ. Он
выполняет ровно те строки, которые человек раскомментировал своей рукой,
и только по явному --apply. Без него -- сухой прогон.

ЧТО ПРИНИМАЕТСЯ. Только удаление строк книг по происхождению:

    DELETE FROM drone_works WHERE source_file = '...' AND source_sheet = '...'
    AND COALESCE(strftime('%Y-%m', work_date_from), period_month) = '...';

[REASON]: белый список, а не чёрный. Скрипт, выполняющий «любой SQL из
файла», -- это заряженное ружьё в каталоге инструментов: одна опечатка в
файле, и удалится не то. Здесь всё, что не совпало с образцом дословно,
приводит к отказу, и ни один запрос не выполняется.

Строки SELECT из файла игнорируются: они там для чтения глазами.

ЗАЩИТЫ:
  - без --apply не пишется ничего, база открывается read-only;
  - с --apply рядом с базой обязана лежать копия transport.db.backup_*,
    иначе отказ: откатывать удаление больше нечем;
  - всё в одной транзакции; печатаются гектары и сумма книг ДО и ПОСЛЕ;
  - если хоть один запрос удалил не то число строк, что показал сухой
    прогон, -- полный откат;
  - вывод в консоль только ASCII: имена файлов и листов кириллические и
    печатаются как число строк, а не как текст.

Запуск (служба может работать, но лучше остановить):
  cd C:\\transport-report
  copy instance\\transport.db instance\\transport.db.backup_20260820
  & "C:\\Program Files\\Python314\\python.exe" tools\\drone_apply_sql_file.py --db instance\\transport.db --file instance\\drone_import_duplicates.sql
  & "C:\\Program Files\\Python314\\python.exe" tools\\drone_apply_sql_file.py --db instance\\transport.db --file instance\\drone_import_duplicates.sql --apply
"""

import argparse
import glob
import os
import re
import sqlite3
import sys

ALLOWED = re.compile(
    r"^DELETE\s+FROM\s+drone_works\s+WHERE\s+source_file\s*=\s*'(?:[^']|'')*'"
    r"\s+AND\s+source_sheet\s*=\s*'(?:[^']|'')*'"
    r"\s+AND\s+COALESCE\(strftime\('%Y-%m',\s*work_date_from\),\s*"
    r"period_month\)\s*=\s*'\d{4}-\d{2}'$",
    re.IGNORECASE)


def _ascii(text):
    return str(text).encode('ascii', 'replace').decode('ascii')


def connect_ro(db_path):
    uri = 'file:%s?mode=ro' % db_path.replace('?', '%3f').replace('#', '%23')
    return sqlite3.connect(uri, uri=True)


def statements(text):
    """Незакомментированные запросы файла, по одному.

    [REASON]: комментарий обрезается ПОСТРОЧНО, до склейки. Файл написан так,
    что закомментированный DELETE лежит прямо под своим SELECT; если срезать
    комментарии после склейки, «-- DELETE ...» приклеится к предыдущему
    запросу и уедет на исполнение.
    """
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('--'):
            continue
        lines.append(stripped)
    out = []
    for chunk in ' '.join(lines).split(';'):
        chunk = ' '.join(chunk.split())
        if chunk:
            out.append(chunk)
    return out


def classify(items):
    """Возвращает (к исполнению, пропущенные SELECT, непонятные)."""
    deletes, selects, refused = [], [], []
    for item in items:
        if item.upper().startswith('SELECT'):
            selects.append(item)
        elif ALLOWED.match(item):
            deletes.append(item)
        else:
            refused.append(item)
    return deletes, selects, refused


def backups_beside(db_path):
    """Копии рядом с базой, СВЕЖАЯ ПОСЛЕДНЕЙ.

    [REASON]: сортировка по имени врёт. На production 2026-08-20 рядом лежали
    transport.db.backup_20260820 и transport.db.backup_subdiv, и по алфавиту
    последней оказалась вторая -- скрипт назвал «путём назад» копию, снятую
    неизвестно когда. Имя копии произвольно, время изменения -- нет.
    """
    found = glob.glob(os.path.abspath(db_path) + '.backup_*')
    return sorted(found, key=os.path.getmtime)


def totals(conn):
    row = conn.execute('SELECT COUNT(*), COALESCE(SUM(area_ha), 0), '
                       'COALESCE(SUM(amount), 0) FROM drone_works'
                       ).fetchone()
    return int(row[0]), float(row[1] or 0), float(row[2] or 0)


def count_targets(conn, delete_sql):
    """Сколько строк заденет запрос. Тот же WHERE, но SELECT COUNT(*)."""
    return int(conn.execute(
        'SELECT COUNT(*)' + delete_sql[len('DELETE'):].replace(
            'FROM drone_works', 'FROM drone_works', 1)).fetchone()[0])


def main():
    parser = argparse.ArgumentParser(
        description='Run the DELETE statements a person uncommented.')
    parser.add_argument('--db', default=os.path.join('instance',
                                                     'transport.db'))
    parser.add_argument('--file', required=True)
    parser.add_argument('--apply', action='store_true',
                        help='write the changes; without it only a dry run')
    args = parser.parse_args()

    if not os.path.isfile(args.db):
        print('ERROR: database not found: %s' % _ascii(args.db))
        return 2
    if not os.path.isfile(args.file):
        print('ERROR: sql file not found: %s' % _ascii(args.file))
        return 2

    with open(args.file, encoding='utf-8') as handle:
        deletes, selects, refused = classify(statements(handle.read()))

    print('File                : %s' % _ascii(args.file))
    print('Uncommented DELETEs : %d' % len(deletes))
    print('SELECTs (ignored)   : %d' % len(selects))
    if refused:
        print('')
        print('REFUSING TO RUN: %d statement(s) do not match the one allowed '
              'shape' % len(refused))
        print('(DELETE FROM drone_works WHERE source_file = ... AND '
              'source_sheet = ... AND COALESCE(...) = ...).')
        print('Nothing was executed. Fix the file or delete those lines.')
        return 1
    if not deletes:
        print('')
        print('Nothing is uncommented. Open the file, remove the leading '
              '"-- " from the ONE variant confirmed by the signed summary, '
              'and run this again.')
        return 0

    conn = connect_ro(args.db)
    try:
        before = totals(conn)
        planned = [count_targets(conn, sql) for sql in deletes]
    finally:
        conn.close()

    print('')
    print('Rows in drone_works : %d' % before[0])
    print('Hectares            : %.2f' % before[1])
    print('Amount              : %.0f' % before[2])
    print('')
    for index, rows in enumerate(planned, 1):
        print('  statement %d would remove %d row(s)' % (index, rows))
    print('  TOTAL %d row(s)' % sum(planned))

    if not args.apply:
        print('')
        print('DRY RUN, nothing written. Add --apply to execute.')
        return 0

    # [REASON]: удаление в этой базе необратимо -- журнала загрузок с откатом
    # нет. Копия рядом с базой -- единственный путь назад, и без неё скрипт
    # не работает, даже когда его просят.
    if not backups_beside(args.db):
        print('')
        print('REFUSING: no backup found next to the database.')
        print('Run first:  copy instance\\transport.db '
              'instance\\transport.db.backup_YYYYMMDD')
        return 1

    conn = sqlite3.connect(args.db)
    try:
        conn.execute('BEGIN')
        touched = []
        for sql in deletes:
            touched.append(conn.execute(sql).rowcount)
        if touched != planned:
            conn.rollback()
            print('')
            print('ROLLED BACK: a statement touched a different number of '
                  'rows than the dry run showed (%s vs %s).'
                  % (touched, planned))
            return 1
        after = totals(conn)
        conn.commit()
    except Exception as exc:
        conn.rollback()
        print('ERROR, rolled back: %s' % _ascii(exc))
        return 1
    finally:
        conn.close()

    print('')
    print('APPLIED. %d row(s) removed.' % sum(touched))
    print('Rows     %8d -> %8d' % (before[0], after[0]))
    print('Hectares %8.2f -> %8.2f  (%+.2f)'
          % (before[1], after[1], after[1] - before[1]))
    print('Amount   %8.0f -> %8.0f  (%+.0f)'
          % (before[2], after[2], after[2] - before[2]))
    print('')
    print('The backup used as the way back: %s'
          % _ascii(os.path.basename(backups_beside(args.db)[-1])))
    return 0


if __name__ == '__main__':
    sys.exit(main())
