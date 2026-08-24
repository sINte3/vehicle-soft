# -*- coding: utf-8 -*-
"""Migration DRONES_WORKS_OCT2025_OWNER_ROWS_001 -- три строки работ по
решениям владельца 2026-08-24.

Все три -- РЕШЕНИЯ ВЛАДЕЛЬЦА, а не выводы из данных, и помечены в `note`
префиксом `[OWNER]`. Разбор, из которого они выросли, --
`docs/DRONES_OCT2025_RECONCILIATION.md`, §11.

**1. 68.00 га «Пешку Сервис ери» переезжают от Рухиллоева к Холмуродову.**
Строка стоит в книге Достон АКА справкой за 14.10 по ставке 75 040. Машина №1
(Рухиллоева) после 12.10 не летала вовсе, а 14.10 летала №3 (Холмуродова) --
65.40 га, и этого дня его книга не называет. Владелец: «ошибка диспетчера,
который написал эти гектары на Рухиллоева Сайфулло с комментарием Пешку ери.
Дрон не работал в Пешку на полях Бухара Сервис Агрокластер».

[REASON]: строка ПЕРЕНОСИТСЯ, а не удаляется и не заводится заново. Деньги
5 102 720 сум едут вместе с ней -- владелец подтвердил дословно. Завести
Холмуродову отдельную строку на 65.40 га, оставив эту у Рухиллоева, значило бы
посчитать одну работу дважды; удалить -- уничтожить продовые данные, что устав
запрещает. `customer_raw` не переписывается: это улика происхождения строки, и
модель прямо требует её не трогать. Что работа была на землях Агрокластера, а
не в Пешку, сказано в `note`.

**2. Новая строка: Холмуродов, 17.10, 13.70 га, справка.** 17.10 машина №3
налетала 13.70 га, и этого дня книга тоже не называет. Владелец: «Записываем
обе на Холмуродова, оплата Справка. Работал на полях Бухоро Агрокластер
Заминлари МЧЖ».

[REASON]: СТАВКА НЕ ВПИСАНА ЧИСЛОМ. Она берётся с уже существующей строки
того же заказчика того же месяца -- справки на 296.00 га, которой ставку
проставила `DRONES_WORKS_OCT2025_INTERNAL_PRICE_001`, прочитав её из сентября.
Владелец назвал 75 040 с оговоркой «кажется»; вписать «кажется» в учёт нельзя,
а две строки одного заказчика в одном месяце по разным ставкам -- готовое
расхождение. Отсюда и требование порядка: без INTERNAL_PRICE брать ставку
неоткуда, и миграция отказывается.

**3. Новая строка: Хамроев, 04.10, 24.00 га, наличка, получено 0.** 04.10
машина №2 налетала 24.00 га, и этого дня в книге Хамроева нет. По данным DJI
работа шла на двух контурах: один подписан `radian juma`, второй без имени.
Владелец: «Если у контура второго нет имени пиши оба на Радиан Жума»;
«пусть выйдет не полученная сумма наличкой... Пусть будет отвечать диспетчер
или оператор по поводу работы и денег».

[REASON]: ставка 200 000 не принята на веру, а СВЕРЕНА: предусловие требует,
чтобы все наличные строки Хамроева за октябрь стояли по 200 000, и отказывает,
если это не так. `received_amount = 0` при `received_kind = 'received'` -- это
и есть «работа сделана, деньги не собраны»: отчёт положит строку в корзину
«Не собрано», а не в расхождения.

У всех трёх новых и перенесённых строк происхождение (`source_file`,
`source_sheet`, `source_row`) ПУСТОЕ -- их нет ни в одной книге. Так же
устроены строки, набранные руками на экране работ; отчёт о двойных загрузках
такие строки исключает из сравнения намеренно.

Безопасность / идемпотентность:
  - отказ с кодом 2 при отсутствии instance/transport.db, файл НЕ создаётся;
  - ПОРЯДОК: требует `DRONES_WORKS_OCT2025_OPERATOR_LINK_001` (без неё у
    Холмуродова нет его 315.00 га) и `DRONES_WORKS_OCT2025_INTERNAL_PRICE_001`
    (без неё неоткуда взять ставку). Проверяется по реестру, отказ называет
    порядок словами, а не цифрами;
  - ПРЕДУСЛОВИЕ: строка 68.00 га одна, у Рухиллоева, с суммой 5 102 720;
    строка 296.00 га одна, со ставкой; наличные строки Хамроева по 200 000;
    ни одной строки с меткой этой миграции ещё нет;
  - ПОСТУСЛОВИЕ: итоги трёх операторов равны обещанным; строк октября ровно
    на две больше; гектары октября выросли ровно на 37.70; на обеих новых
    строках сходится «сумма = гектары x ставка»;
  - одна транзакция; повторный запуск печатает «already applied»;
  - stdlib sqlite3 без Flask; вывод в консоль только ASCII.

Запуск (production, служба ОСТАНОВЛЕНА, ПОСЛЕ OPERATOR_LINK и INTERNAL_PRICE):
  cd C:\\transport-report
  & "C:\\Program Files\\Python314\\python.exe" migrate_drones_works_oct2025_owner_rows_001.py
  & "C:\\Program Files\\Python314\\python.exe" migrate_drones_works_oct2025_owner_rows_001.py --apply

Откат КОДА: git revert коммита.
Откат ДАННЫХ: печатается готовым к вставке блоком вместе с
  DELETE FROM schema_migrations WHERE name = 'DRONES_WORKS_OCT2025_OWNER_ROWS_001';
"""

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import migration_utils  # noqa: E402

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       'instance', 'transport.db')

MIGRATION_ID = 'DRONES_WORKS_OCT2025_OWNER_ROWS_001'
REQUIRES = ('DRONES_WORKS_OCT2025_OPERATOR_LINK_001',
            'DRONES_WORKS_OCT2025_INTERNAL_PRICE_001')
DESCRIPTION = ("October 2025: the owner's three decisions of 2026-08-24 -- "
               '68.00 ha move from Ruhilloev to Kholmurodov, and two new '
               'rows (13.70 ha on 17 Oct, 24.00 ha on 04 Oct).')

MONTH = '2025-10'
MONTH_WHERE = ("COALESCE(strftime('%Y-%m', w.work_date_from), "
               "w.period_month) = ?")
MARK = '[OWNER] DRONES_WORKS_OCT2025_OWNER_ROWS_001'

# --- 1. переезд -----------------------------------------------------------
MOVE_SHEET = 'СВОДКА октябрь'
MOVE_AREA = 68.00
MOVE_AMOUNT = 5102720.0
MOVE_FROM = 'Рухиллоев Сайфулло'
MOVE_TO = 'Холмуродов Шахзод'
MOVE_NOTE = (MARK + ': строка перенесена от Рухиллоева Сайфулло. Решение '
             'владельца 2026-08-24 -- «ошибка диспетчера, который написал эти '
             'гектары на Рухиллоева с комментарием Пешку ери. Дрон не работал '
             'в Пешку на полях Бухара Сервис Агрокластер». 14.10 машина №1 '
             '(Рухиллоева) не летала -- её последний вылет 12.10; летала №3 '
             '(Холмуродова), 65.40 га, и этого дня его книга не называет. '
             'Работа на землях Бухоро Агрокластер Заминлари МЧЖ; '
             'customer_raw не переписан -- это улика происхождения строки.')

# --- ставка новой справочной строки берётся ОТСЮДА ------------------------
RATE_SOURCE_FILE = 'Ғиждувон_ПТЗ_Дрон_маълумот_Октябрь.xlsx'
RATE_SOURCE_SHEET = 'свод ичи (Шахзод)'
RATE_SOURCE_ROW = 11
RATE_SOURCE_AREA = 296.00

# --- 2 и 3. новые строки --------------------------------------------------
CASH_RATE = 200000.0
NEW_ROWS = (
    {'operator': MOVE_TO, 'date': '2025-10-17', 'area': 13.70,
     'customer': 'Бухоро Агрокластер Заминлари МЧЖ', 'payment': 'transfer',
     'rate': None,           # None = взять со строки-источника
     'received': None, 'received_kind': None,
     'why': ('17.10 машина №3 налетала 13.70 га, и этого дня книга '
             'Холмуродова не называет. Решение владельца 2026-08-24: '
             '«Записываем обе на Холмуродова, оплата Справка. Работал на '
             'полях Бухоро Агрокластер Заминлари МЧЖ». Ставка взята со '
             'строки того же заказчика того же месяца (справка 296.00 га), '
             'а не вписана числом.')},
    {'operator': 'Хамроев Шохрух', 'date': '2025-10-04', 'area': 24.00,
     'customer': 'Радиан Жума', 'payment': 'cash',
     'rate': CASH_RATE,
     'received': 0.0, 'received_kind': 'received',
     'why': ('04.10 машина №2 налетала 24.00 га, и этого дня в книге '
             'Хамроева нет. По данным DJI работа шла на двух контурах: один '
             'подписан «radian juma», второй без имени. Решение владельца '
             '2026-08-24: «Если у контура второго нет имени пиши оба на '
             'Радиан Жума»; «пусть выйдет не полученная сумма наличкой». '
             'Ставка 200 000 сверена с наличными строками Хамроева за '
             'октябрь, а не принята на веру.')},
)

EXPECTED_AFTER = {MOVE_TO: 396.70, MOVE_FROM: 178.90,
                  'Хамроев Шохрух': 196.60}
EXPECTED_NEW_ROWS = 2
EXPECTED_NEW_HA = 37.70
TOLERANCE_HA = 0.005
TOLERANCE_SUM = 1.0


def normalize(name):
    table = {ord('қ'): 'к', ord('ғ'): 'г', ord('ў'): 'у', ord('ҳ'): 'х',
             ord('ҷ'): 'ж', ord('ъ'): '', ord('ь'): '', ord('ё'): 'е'}
    return ' '.join(str(name).lower().translate(table).split())


def _ascii(text):
    return str(text).encode('ascii', 'replace').decode('ascii')


def operator_id(conn, full_name):
    found = [op_id for op_id, name in
             conn.execute('SELECT id, full_name FROM drone_operators')
             if normalize(name) == normalize(full_name)]
    if len(found) != 1:
        raise LookupError('operator %s resolves to %d rows'
                          % (_ascii(full_name), len(found)))
    return found[0]


def operator_hectares(conn, full_name):
    row = conn.execute(
        'SELECT COALESCE(SUM(w.area_ha), 0) FROM drone_works w '
        'WHERE w.drone_operator_id = ? AND ' + MONTH_WHERE,
        (operator_id(conn, full_name), MONTH)).fetchone()
    return float(row[0] or 0)


def month_totals(conn):
    row = conn.execute(
        'SELECT COUNT(*), COALESCE(SUM(w.area_ha), 0), '
        'COALESCE(SUM(w.amount), 0) FROM drone_works w WHERE ' + MONTH_WHERE,
        (MONTH,)).fetchone()
    return int(row[0]), float(row[1] or 0), float(row[2] or 0)


def find_move_row(conn):
    return conn.execute(
        'SELECT w.id, w.area_ha, w.amount, w.drone_operator_id, '
        '       w.customer_raw, w.subdivision_name FROM drone_works w '
        'WHERE w.source_sheet = ? AND ABS(w.area_ha - ?) < ? AND '
        + MONTH_WHERE, (MOVE_SHEET, MOVE_AREA, TOLERANCE_HA,
                        MONTH)).fetchall()


def find_rate_row(conn):
    return conn.execute(
        'SELECT w.id, w.area_ha, w.price_per_ha FROM drone_works w '
        'WHERE w.source_file = ? AND w.source_sheet = ? AND w.source_row = ? '
        'AND ' + MONTH_WHERE,
        (RATE_SOURCE_FILE, RATE_SOURCE_SHEET, RATE_SOURCE_ROW,
         MONTH)).fetchall()


def cash_rates_of(conn, full_name):
    """Ставки наличных строк оператора за месяц -- для сверки 200 000."""
    return conn.execute(
        'SELECT DISTINCT w.price_per_ha FROM drone_works w '
        "WHERE w.drone_operator_id = ? AND w.payment_type = 'cash' "
        'AND w.price_per_ha IS NOT NULL AND ' + MONTH_WHERE,
        (operator_id(conn, full_name), MONTH)).fetchall()


def subdivision_of(conn, full_name):
    """Подразделение берётся с СУЩЕСТВУЮЩИХ строк оператора, не выдумывается."""
    rows = conn.execute(
        'SELECT DISTINCT w.subdivision_name FROM drone_works w '
        'WHERE w.drone_operator_id = ? AND w.subdivision_name IS NOT NULL '
        'AND ' + MONTH_WHERE,
        (operator_id(conn, full_name), MONTH)).fetchall()
    return rows[0][0] if len(rows) == 1 else None


def already_marked(conn):
    return conn.execute(
        'SELECT COUNT(*) FROM drone_works WHERE note LIKE ?',
        ('%' + MIGRATION_ID + '%',)).fetchone()[0]


def check_precondition(conn):
    problems = []
    try:
        for name in (MOVE_FROM, MOVE_TO, 'Хамроев Шохрух'):
            operator_id(conn, name)
    except LookupError as exc:
        return ['  %s' % _ascii(exc)], None

    found = find_move_row(conn)
    if len(found) != 1:
        problems.append('  the %.2f ha row in "%s": expected 1, found %d'
                        % (MOVE_AREA, _ascii(MOVE_SHEET), len(found)))
    else:
        work_id, _area, amount, op_id, _cust, _sub = found[0]
        if op_id != operator_id(conn, MOVE_FROM):
            problems.append('  the %.2f ha row does not belong to %s -- '
                            'refusing to move a row that already moved'
                            % (MOVE_AREA, _ascii(MOVE_FROM)))
        if abs(float(amount or 0) - MOVE_AMOUNT) > TOLERANCE_SUM:
            problems.append('  the %.2f ha row carries %.2f, expected %.2f'
                            % (MOVE_AREA, float(amount or 0), MOVE_AMOUNT))

    rate_rows = find_rate_row(conn)
    if len(rate_rows) != 1:
        problems.append('  the %.2f ha rate-source row: expected 1, found %d'
                        % (RATE_SOURCE_AREA, len(rate_rows)))
    elif rate_rows[0][2] is None:
        problems.append('  the rate-source row has NO price yet -- run '
                        'DRONES_WORKS_OCT2025_INTERNAL_PRICE_001 first')

    rates = [float(r[0]) for r in cash_rates_of(conn, 'Хамроев Шохрух')]
    if rates != [CASH_RATE]:
        problems.append('  Hamroev cash rows quote %s, expected only %.0f -- '
                        'refusing to price the new row on an assumption'
                        % (', '.join('%.0f' % r for r in rates) or 'nothing',
                           CASH_RATE))
    if already_marked(conn):
        problems.append('  %d row(s) already carry this migration mark'
                        % already_marked(conn))
    if problems:
        problems.append('  These figures were measured on the October books '
                        'and the flights export. A difference means the data '
                        'changed since; refusing to guess.')
    return problems, (find_move_row(conn)[0][0] if len(found) == 1 else None)


def apply_changes(conn, move_id):
    report, rollback = [], []
    rate = float(find_rate_row(conn)[0][2])

    was = conn.execute('SELECT note FROM drone_works WHERE id = ?',
                       (move_id,)).fetchone()[0]
    conn.execute('UPDATE drone_works SET drone_operator_id = ?, note = '
                 "COALESCE(note || ' | ', '') || ? WHERE id = ?",
                 (operator_id(conn, MOVE_TO), MOVE_NOTE, move_id))
    report.append('  move  %.2f ha  %-20s -> %-20s  (%.2f sum travel with it)'
                  % (MOVE_AREA, _ascii(MOVE_FROM), _ascii(MOVE_TO),
                     MOVE_AMOUNT))
    # [REASON]: откат возвращает и оператора, и ПРИСТАВЛЕННУЮ заметку --
    # оставить её значило бы, что откат отменил не всё, и строка после него
    # врала бы про свою историю.
    # [REASON]: и он ОБРЕЗАЕТ заметку по длине, а не вырезает её текст. Текст
    # заметки кириллический, а печатается откат в консоль Windows, которая
    # кириллицу калечит в «?????»: скопированный оттуда запрос не нашёл бы
    # ничего и отрапортовал бы «обновлено 0» -- благополучно на вид и не
    # сделав ничего. На этом проект уже обжигался с DELETE по именам листов.
    rollback.append('UPDATE drone_works SET drone_operator_id = %d, '
                    'note = %s WHERE id = %d;'
                    % (operator_id(conn, MOVE_FROM),
                       'NULL' if was is None else 'substr(note, 1, %d)'
                       % len(was), move_id))

    for spec in NEW_ROWS:
        price = rate if spec['rate'] is None else spec['rate']
        amount = round(spec['area'] * price, 2)
        cur = conn.execute(
            'INSERT INTO drone_works (period_month, work_date_from, '
            'work_date_to, date_raw, drone_operator_id, operator_raw, '
            'customer_raw, area_ha, price_per_ha, amount, received_amount, '
            'received_kind, payment_type, subdivision_name, note, '
            "created_at) VALUES (?, ?, ?, NULL, ?, NULL, ?, ?, ?, ?, ?, ?, "
            "?, ?, ?, datetime('now'))",
            (MONTH, spec['date'], spec['date'],
             operator_id(conn, spec['operator']), spec['customer'],
             spec['area'], price, amount, spec['received'],
             spec['received_kind'], spec['payment'],
             subdivision_of(conn, spec['operator']),
             MARK + ': ' + spec['why']))
        report.append('  new   %.2f ha  %-20s  %s  %-8s %10.2f x %.2f = %.2f'
                      % (spec['area'], _ascii(spec['operator']),
                         spec['date'], spec['payment'], spec['area'], price,
                         amount))
        rollback.append('DELETE FROM drone_works WHERE id = %d;'
                        % cur.lastrowid)
    return report, rollback


def check_postcondition(conn, before):
    problems = []
    for name, want in EXPECTED_AFTER.items():
        got = operator_hectares(conn, name)
        if abs(got - want) > TOLERANCE_HA:
            problems.append('  %-20s expected %8.2f ha, got %8.2f'
                            % (_ascii(name), want, got))
    rows, hectares, amount = month_totals(conn)
    if rows != before[0] + EXPECTED_NEW_ROWS:
        problems.append('  October rows: %d -> %d, expected +%d'
                        % (before[0], rows, EXPECTED_NEW_ROWS))
    if abs((hectares - before[1]) - EXPECTED_NEW_HA) > TOLERANCE_HA:
        problems.append('  October hectares grew by %.2f, expected %.2f'
                        % (hectares - before[1], EXPECTED_NEW_HA))
    if amount <= before[2]:
        problems.append('  October amount did not grow: %.2f -> %.2f'
                        % (before[2], amount))
    # [REASON]: новые строки выходят в отчёт со ставкой, и тождество «сумма =
    # гектары x ставка» обязано на них сойтись СРАЗУ. Строка со ставкой и
    # несогласованной суммой создала бы расхождение там, где его не было.
    bad = conn.execute(
        'SELECT COUNT(*) FROM drone_works WHERE note LIKE ? '
        'AND price_per_ha IS NOT NULL '
        'AND ABS(amount - area_ha * price_per_ha) > 0.01',
        ('%' + MIGRATION_ID + '%',)).fetchone()[0]
    if bad:
        problems.append('  %d touched row(s) break amount = ha x rate' % bad)
    return problems


def main():
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument('--db', default=DB_PATH,
                        help='override only for testing on a synthetic copy')
    parser.add_argument('--apply', action='store_true',
                        help='write the changes; without it only a dry run')
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
        # [REASON]: порядок назван СЛОВАМИ. Без OPERATOR_LINK у Холмуродова
        # нет его 315.00 га и постусловие свалилось бы на числе; без
        # INTERNAL_PRICE неоткуда взять ставку новой справочной строки.
        missing = [name for name in REQUIRES
                   if not migration_utils.is_migration_applied(name)]
        if missing:
            print('PRECONDITION FAILED -- nothing changed.')
            for name in missing:
                print('  %s must be applied FIRST.' % name)
            print('  Without OPERATOR_LINK Kholmurodov has no October '
                  'hectares at all; without INTERNAL_PRICE there is no rate '
                  'to copy.')
            return 1

        problems, move_id = check_precondition(conn)
        if problems:
            print('PRECONDITION FAILED -- nothing changed.')
            for line in problems:
                print(line)
            return 1

        before = month_totals(conn)
        conn.execute('BEGIN')
        try:
            report, rollback = apply_changes(conn, move_id)
            problems = check_postcondition(conn, before)
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
    print('Postconditions: Kholmurodov 396.70 ha, Ruhilloev 178.90, Hamroev '
          '196.60; October gains exactly 2 rows and 37.70 ha; every touched '
          'row satisfies amount = ha x rate.')
    print('')
    print('After this the October books read (book -> telemetry):')
    print('  Kholmurodov Shahzod   396.70 ha vs 386.05   102.8%')
    print('  Ruhilloev Sayfullo    178.90 ha vs 187.45    95.4%')
    print('  Hamroev Shohruh       196.60 ha vs 221.71    88.7%')
    print('')
    print('ROLLBACK OF DATA (run in this order):')
    for line in rollback:
        print('  ' + line)
    print("  DELETE FROM schema_migrations WHERE name = '%s';" % MIGRATION_ID)
    return 0


if __name__ == '__main__':
    sys.exit(main())
