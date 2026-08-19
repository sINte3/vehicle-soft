#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""drone_books_month_drift.py -- DRONE-BOOKS-DRIFT-001, только чтение.

Показывает работы, у которых МЕСЯЦ КНИГИ и МЕСЯЦ РАБОТЫ -- разные месяцы.

Зачем. 2026-08-14 когонская книга за сентябрь оказалась подписана «Октябрь»:
все её 75 строк (839.80 га) датированы сентябрём, а месяц в шапке стоял
чужой. Из-за подписи сентябрь читался как месяц, в котором когонского объёма
нет вовсе, и по нему был задан вопрос диспетчерам. В той же партии книг
нашлись четыре ғиждувонские строки на 587.50 га с октябрьскими датами внутри
сентябрьской книги -- в том числе справка «Бухоро Агрокластер Заминлари,
19-30, 511.50 га», которую телеметрия относит к сентябрю (машина 3 за 19-30
сентября сделала 489.06 га против 153.60 га за те же числа октября).

Это не единичная описка, а класс ошибки: месяц книги диспетчер проставляет
руками -- в шапке листа и в форме загрузки, -- а дата работы стоит в самой
строке. Расходятся они молча, и молча же переносят сотни гектаров между
месяцами в любом отчёте, который группирует по периоду книги.

ЧТО СЧИТАЕТСЯ ДРЕЙФОМ. period_month работы против месяца её work_date_from.
Отдельно перечисляются работы без читаемой даты: они не попадают ни в один
месяц по дате и держатся только на period_month.

[REASON]: РАСХОЖДЕНИЕ САМО ПО СЕБЕ НЕ ОШИБКА. Одна книга законно накрывает
край месяца: работа 30 апреля попадает в майскую книгу, если её сдали
позже. Инструмент поэтому НИЧЕГО не чинит и не выносит вердиктов -- он
сортирует по гектарам и даёт человеку посмотреть на крупное. Порога здесь
нет намеренно: порог превратил бы «посмотрите» в «всё хорошо».

Ничего не пишет: база открывается read-only через file:-URI, служба может
работать. Ни одного INSERT/UPDATE в файле нет.

Запуск (сервер, из каталога установки):
  & "C:\\Program Files\\Python314\\python.exe" tools\\drone_books_month_drift.py --db instance\\transport.db

Консоль -- только ASCII; читаемый отчёт с кириллицей уходит в UTF-8 файл
(--report, по умолчанию рядом с базой).
"""

import argparse
import os
import sqlite3
import sys
from collections import defaultdict

MONTH_OF_WORK = "strftime('%Y-%m', work_date_from)"


def connect_ro(db_path):
    if not os.path.exists(db_path):
        # [REASON]: sqlite3.connect СОЗДАЁТ пустую базу, если файла нет.
        print('ERROR: database not found at %s - refusing to run.' % db_path)
        sys.exit(2)
    uri = 'file:%s?mode=ro' % db_path.replace('?', '%3f').replace('#', '%23')
    return sqlite3.connect(uri, uri=True)


def drifted(conn):
    """(книга, месяц книги, месяц работы) -> [строк, гектаров]."""
    rows = conn.execute(
        'SELECT source_file, period_month, %s AS real_month, '
        '       COUNT(*), COALESCE(SUM(area_ha), 0) '
        'FROM drone_works '
        'WHERE work_date_from IS NOT NULL AND work_date_from <> \'\' '
        '  AND %s <> period_month '
        'GROUP BY source_file, period_month, real_month' % (MONTH_OF_WORK,
                                                            MONTH_OF_WORK)
    ).fetchall()
    return rows


def undated(conn):
    rows = conn.execute(
        'SELECT source_file, period_month, COUNT(*), '
        '       COALESCE(SUM(area_ha), 0) '
        'FROM drone_works '
        'WHERE work_date_from IS NULL OR work_date_from = \'\' '
        'GROUP BY source_file, period_month').fetchall()
    return rows


def month_totals(conn):
    """Итог месяца по книге против итога того же месяца по датам работ."""
    by_period = dict(conn.execute(
        'SELECT period_month, COALESCE(SUM(area_ha), 0) '
        'FROM drone_works GROUP BY period_month').fetchall())
    by_date = dict(conn.execute(
        'SELECT %s, COALESCE(SUM(area_ha), 0) FROM drone_works '
        'WHERE work_date_from IS NOT NULL AND work_date_from <> \'\' '
        'GROUP BY 1' % MONTH_OF_WORK).fetchall())
    months = sorted(set(list(by_period) + list(by_date)))
    return [(m, by_period.get(m, 0.0), by_date.get(m, 0.0)) for m in months]


def write_report(path, drift_rows, undated_rows, totals):
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write('DRONE-BOOKS-DRIFT-001 -- месяц книги против месяца работы\n')
        fh.write('=' * 78 + '\n\n')

        fh.write('1. РАБОТЫ, ЛЕЖАЩИЕ НЕ В СВОЁМ МЕСЯЦЕ (по убыванию гектаров)\n')
        fh.write('-' * 78 + '\n')
        if not drift_rows:
            fh.write('   Расхождений нет.\n')
        total_ha = 0.0
        for src, period, real, count, ha in sorted(drift_rows,
                                                   key=lambda r: -r[4]):
            total_ha += ha
            fh.write('   %9.2f га  %3d стр   книга подписана %s, '
                     'работы датированы %s\n' % (ha, count, period, real))
            fh.write('                        %s\n' % (src or '(без файла)'))
        if drift_rows:
            fh.write('   ---- ВСЕГО не в своём месяце: %.2f га\n' % total_ha)
        fh.write('\n')

        fh.write('2. РАБОТЫ БЕЗ ЧИТАЕМОЙ ДАТЫ (держатся только на месяце '
                 'книги)\n')
        fh.write('-' * 78 + '\n')
        if not undated_rows:
            fh.write('   Таких нет.\n')
        undated_ha = 0.0
        for src, period, count, ha in sorted(undated_rows,
                                             key=lambda r: -r[3]):
            undated_ha += ha
            fh.write('   %9.2f га  %3d стр   месяц книги %s\n'
                     % (ha, count, period))
            fh.write('                        %s\n' % (src or '(без файла)'))
        if undated_rows:
            fh.write('   ---- ВСЕГО без даты: %.2f га\n' % undated_ha)
        fh.write('\n')

        fh.write('3. ИТОГ МЕСЯЦА: по книге против по датам работ\n')
        fh.write('-' * 78 + '\n')
        fh.write('   месяц     по месяцу книги   по датам работ    разница\n')
        for month, by_period, by_date in totals:
            fh.write('   %s   %13.2f %16.2f %11.2f\n'
                     % (month, by_period, by_date, by_date - by_period))
        fh.write('\nЧто с этим делать. Расхождение само по себе не ошибка: '
                 'книга законно накрывает край месяца. Смотреть надо на '
                 'крупное -- сотни гектаров означают, что книгу подписали или '
                 'загрузили не тем месяцем, и тогда её перезагружают с верным '
                 'периодом. Отчёты, считающие по дате работы, при этом уже '
                 'правы: врут только те, что группируют по периоду книги.\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', default=os.path.join('instance',
                                                     'transport.db'))
    parser.add_argument('--report', default=None)
    args = parser.parse_args()

    conn = connect_ro(args.db)
    try:
        drift_rows = drifted(conn)
        undated_rows = undated(conn)
        totals = month_totals(conn)
    finally:
        conn.close()

    drift_ha = sum(r[4] for r in drift_rows)
    drift_count = sum(r[3] for r in drift_rows)
    undated_ha = sum(r[3] for r in undated_rows)
    undated_count = sum(r[2] for r in undated_rows)

    print('Works filed under a month other than their own work date: '
          '%d row(s), %.2f ha' % (drift_count, drift_ha))
    print('Works with no readable date: %d row(s), %.2f ha'
          % (undated_count, undated_ha))
    biggest = sorted(drift_rows, key=lambda r: -r[4])[:5]
    for _src, period, real, count, ha in biggest:
        print('  %9.2f ha  %3d row(s)  book says %s, work dates say %s'
              % (ha, count, period, real))

    report = args.report or os.path.join(
        os.path.dirname(os.path.abspath(args.db)) or '.',
        'drone_books_month_drift.txt')
    write_report(report, drift_rows, undated_rows, totals)
    print('Report written to %s' % report)


if __name__ == '__main__':
    main()
