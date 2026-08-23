# -*- coding: utf-8 -*-
"""Migration DRONES_WORKS_OCT2025_USMON_ROW_FIX_001 -- строка со сбитыми
колонками книги приводится к тому, что теперь написано в книге.

ЧТО ПОЧИНЕНО. Лист `свод ичи (Усмон) ` книги Ғиждувона, справка, строка 17,
«Амиршох Бехруз иқболи фх», 7.00 га. У неё были ПЕРЕПУТАНЫ ПОДПИСИ КОЛОНОК:
шапка говорила «санаси | суммаси», а в клетках стояло «сумма | дата».
Импортёр читает по шапке и поэтому положил в `date_raw` строку
«178571.42857142855», а ставку не увидел вовсе -- работа висит в корзине
«проверить нечем» и в посуточную сверку не входит.

ВЛАДЕЛЕЦ ИСПРАВИЛ КНИГУ 2026-08-23, поменяв местами подписи колонок. Прогон
импортёра по обеим редакциям файла:

    до правки    ставка None                дата не разобрана
    после правки ставка 178571.42857142855  дата 2025-10-11

**Но живую строку это не чинит.** Уникальность строки -- тройка (файл, лист,
номер строки); при переимпорте существующая тройка ПРОПУСКАЕТСЯ, а не
обновляется, поэтому исправленная книга сама по себе базу не меняет. Владелец
поручил починить строку самому (2026-08-23: «если моя правка не устраивает,
произведи сам правку как требуется» -- правка книги как раз устраивает,
чинить надо базу).

ЧТО СТАВИТСЯ. Ровно то, что даёт импортёр на ИСПРАВЛЕННОЙ книге: ставка,
обе даты и `date_raw` в том виде, в каком его пишет импортёр из клетки-даты.

[REASON]: `amount` НЕ трогается. В базе стоит 1 250 000, а 7.00 x
178 571.42857142855 = 1 249 999.9999999998 -- то же число с точностью до
2e-10. Переписать сумму значило бы поменять деловое содержание строки ради
округления; постусловие вместо этого требует, чтобы тождество «сумма =
гектары x ставка» на ней сошлось.

Безопасность / идемпотентность:
  - отказ с кодом 2 при отсутствии instance/transport.db, файл НЕ создаётся;
  - ПРЕДУСЛОВИЕ: строка одна, 7.00 га, ставка ПУСТА, даты ПУСТЫ, и в
    `date_raw` лежит ровно «178571.42857142855» -- отпечаток самого дефекта.
    Что-то иначе -- отказ кодом 1: значит книгу уже переимпортировали или
    строку правили руками;
  - ПОСТУСЛОВИЕ: ставка и даты стоят, тождество сходится, гектары и сумма
    месяца не изменились, число строк октября то же;
  - одна транзакция; повторный запуск печатает «already applied»;
  - stdlib sqlite3 без Flask; вывод в консоль только ASCII.

Запуск (production, служба ОСТАНОВЛЕНА):
  cd C:\\transport-report
  .\\nssm.exe stop TransportReport
  copy instance\\transport.db instance\\transport.db.backup_20260823c
  & "C:\\Program Files\\Python314\\python.exe" migrate_drones_works_oct2025_usmon_row_fix_001.py
  & "C:\\Program Files\\Python314\\python.exe" migrate_drones_works_oct2025_usmon_row_fix_001.py --apply
  .\\nssm.exe start TransportReport

Откат КОДА: git revert коммита.
Откат ДАННЫХ: печатается готовым к вставке блоком вместе с
  DELETE FROM schema_migrations WHERE name = 'DRONES_WORKS_OCT2025_USMON_ROW_FIX_001';
"""

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import migration_utils  # noqa: E402

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       'instance', 'transport.db')

MIGRATION_ID = 'DRONES_WORKS_OCT2025_USMON_ROW_FIX_001'
DESCRIPTION = ('October 2025: the Gijduvon transfer row whose book columns '
               'were swapped gets its price and its date, as the corrected '
               'book now reads them.')

MONTH = '2025-10'
MONTH_WHERE = ("COALESCE(strftime('%Y-%m', w.work_date_from), "
               "w.period_month) = ?")

SOURCE_FILE = 'Ғиждувон_ПТЗ_Дрон_маълумот_Октябрь.xlsx'
SOURCE_SHEET = 'свод ичи (Усмон) '
SOURCE_ROW = 17
EXPECTED_AREA = 7.00
# Отпечаток дефекта: ставка, попавшая в графу даты.
BROKEN_DATE_RAW = '178571.42857142855'
# Что даёт импортёр на исправленной книге.
NEW_PRICE = 178571.42857142855
NEW_DATE = '2025-10-11'
NEW_DATE_RAW = '2025-10-11 00:00:00'

TOLERANCE_HA = 0.005
TOLERANCE_SUM = 1.0


def _ascii(text):
    return str(text).encode('ascii', 'replace').decode('ascii')


def find_row(conn):
    return conn.execute(
        'SELECT w.id, w.area_ha, w.price_per_ha, w.amount, w.work_date_from, '
        '       w.work_date_to, w.date_raw FROM drone_works w '
        'WHERE w.source_file = ? AND w.source_sheet = ? AND w.source_row = ?',
        (SOURCE_FILE, SOURCE_SHEET, SOURCE_ROW)).fetchall()


def month_totals(conn):
    row = conn.execute(
        'SELECT COALESCE(SUM(w.area_ha), 0), COALESCE(SUM(w.amount), 0), '
        'COUNT(*) FROM drone_works w WHERE ' + MONTH_WHERE,
        (MONTH,)).fetchone()
    return float(row[0] or 0), float(row[1] or 0), int(row[2])


def check_precondition(conn):
    found = find_row(conn)
    if len(found) != 1:
        return ['  expected exactly 1 row for %s | %s | row %d, found %d'
                % (_ascii(SOURCE_FILE)[-30:], _ascii(SOURCE_SHEET),
                   SOURCE_ROW, len(found))], None
    (work_id, area, price, _amount, date_from, date_to, date_raw) = found[0]
    problems = []
    if abs(float(area or 0) - EXPECTED_AREA) > TOLERANCE_HA:
        problems.append('  area: expected %.2f ha, found %.2f'
                        % (EXPECTED_AREA, float(area or 0)))
    if price is not None:
        problems.append('  price is already set (%s) -- the row was fixed by '
                        'hand or re-imported' % _ascii(price))
    if date_from is not None or date_to is not None:
        problems.append('  the dates are already set (%s .. %s)'
                        % (_ascii(date_from), _ascii(date_to)))
    # [REASON]: САМАЯ ВАЖНАЯ ЧАСТЬ ПРЕДУСЛОВИЯ. Пустые ставка и дата бывают у
    # десятков честных строк; «ставка, лежащая в графе даты» бывает только у
    # этой. Без этой сверки миграция согласилась бы починить любую другую
    # строку, которой просто не заполнили дату.
    if (date_raw or '') != BROKEN_DATE_RAW:
        problems.append('  date_raw is %r, expected the defect fingerprint %r'
                        % (_ascii(date_raw), BROKEN_DATE_RAW))
    if problems:
        problems.append('  This row is identified by the defect itself. A '
                        'difference means it is no longer broken; refusing to '
                        'overwrite.')
    return problems, work_id


def apply_changes(conn, work_id):
    conn.execute('UPDATE drone_works SET price_per_ha = ?, work_date_from = ?,'
                 ' work_date_to = ?, date_raw = ? WHERE id = ?',
                 (NEW_PRICE, NEW_DATE, NEW_DATE, NEW_DATE_RAW, work_id))
    report = ['  %s row %d: price -> %.5f, date -> %s'
              % (_ascii(SOURCE_SHEET), SOURCE_ROW, NEW_PRICE, NEW_DATE)]
    rollback = ['UPDATE drone_works SET price_per_ha = NULL, '
                'work_date_from = NULL, work_date_to = NULL, '
                "date_raw = '%s' WHERE id = %d;"
                % (BROKEN_DATE_RAW, work_id)]
    return report, rollback


def check_postcondition(conn, work_id, before):
    problems = []
    row = conn.execute('SELECT area_ha, price_per_ha, amount, '
                       'work_date_from, work_date_to, date_raw '
                       'FROM drone_works WHERE id = ?', (work_id,)).fetchone()
    if row is None:
        return ['  the row vanished']
    area, price, amount, date_from, date_to, date_raw = row
    if price is None or abs(float(price) - NEW_PRICE) > 0.00001:
        problems.append('  price is %s, expected %.5f'
                        % (_ascii(price), NEW_PRICE))
    if str(date_from)[:10] != NEW_DATE or str(date_to)[:10] != NEW_DATE:
        problems.append('  dates are %s .. %s, expected %s'
                        % (_ascii(date_from), _ascii(date_to), NEW_DATE))
    if (date_raw or '') != NEW_DATE_RAW:
        problems.append('  date_raw is %r, expected %r'
                        % (_ascii(date_raw), NEW_DATE_RAW))
    # [REASON]: строка выходит из корзины «проверить нечем» -- и обязана выйти
    # в «сошлось», а не в «расходится». Ставка без согласованной суммы сделала
    # бы расхождение там, где его не было.
    if price is not None and abs(float(amount or 0)
                                 - float(area or 0) * float(price)) > 0.01:
        problems.append('  amount %.2f != %.2f x %.5f'
                        % (float(amount or 0), float(area or 0), float(price)))
    hectares, money, rows = month_totals(conn)
    if abs(hectares - before[0]) > TOLERANCE_HA:
        problems.append('  month hectares moved: %.2f -> %.2f'
                        % (before[0], hectares))
    if abs(money - before[1]) > TOLERANCE_SUM:
        problems.append('  month amount moved: %.2f -> %.2f'
                        % (before[1], money))
    # [REASON]: дата ставится ВНУТРИ того же месяца, и число строк октября
    # обязано остаться прежним. Дата другого месяца молча унесла бы работу из
    # октябрьского отчёта -- ровно то, из-за чего пропали 118.40 га.
    if rows != before[2]:
        problems.append('  October row count moved: %d -> %d'
                        % (before[2], rows))
    return problems


def main():
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument('--db', default=DB_PATH,
                        help='override only for testing on a synthetic copy')
    parser.add_argument('--apply', action='store_true',
                        help='write the change; without it only a dry run')
    args = parser.parse_args()

    if not os.path.isfile(args.db):
        print('ERROR: database not found: %s' % _ascii(args.db))
        print('Nothing was created. Run this from the install directory.')
        return 2

    migration_utils.DB_PATH = os.path.abspath(args.db)
    conn = sqlite3.connect(args.db)
    try:
        conn.execute('PRAGMA foreign_keys = ON')
        migration_utils.ensure_schema_migrations_table()
        if migration_utils.is_migration_applied(MIGRATION_ID):
            print('%s: already applied, nothing to do.' % MIGRATION_ID)
            return 0

        problems, work_id = check_precondition(conn)
        if problems:
            print('PRECONDITION FAILED -- nothing changed.')
            for line in problems:
                print(line)
            return 1

        before = month_totals(conn)
        conn.execute('BEGIN')
        try:
            report, rollback = apply_changes(conn, work_id)
            problems = check_postcondition(conn, work_id, before)
            if problems:
                conn.rollback()
                print('POSTCONDITION FAILED -- rolled back, nothing changed.')
                for line in problems:
                    print(line)
                return 1
            if not args.apply:
                conn.rollback()
                print('%s: DRY RUN, nothing written.' % MIGRATION_ID)
            else:
                conn.commit()
                migration_utils.record_migration(
                    MIGRATION_ID, description=DESCRIPTION,
                    checksum=migration_utils.migration_checksum(
                        os.path.abspath(__file__)))
                print('%s: APPLIED.' % MIGRATION_ID)
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()

    print('Changes:')
    for line in report:
        print(line)
    print('Postconditions: the row now satisfies amount = ha x rate; October '
          'hectares, amount and row count are unchanged; the date stays '
          'inside October.')
    print('')
    print('ROLLBACK OF DATA (run in this order):')
    for line in rollback:
        print('  ' + line)
    print("  DELETE FROM schema_migrations WHERE name = '%s';" % MIGRATION_ID)
    return 0


if __name__ == '__main__':
    sys.exit(main())
