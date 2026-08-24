# -*- coding: utf-8 -*-
"""Migration DRONES_ASSIGN_OCT2025_SERVIS15_001 -- машина №15 в октябре была
у Жумаева Фурқата.

ЧТО ПОЧИНЕНО. За октябрь-2025 машина №15 (ник `13 Servis`) налетала
**47.92 га за 67 вылетов**, и НИ ОДИН из них не отнесён ни к кому: в
`drone_operator_assignments` её октябрьского окна нет вовсе. Одновременно
Жумаев Фурқат числится в книгах на 47.10 га при НУЛЕ гектаров телеметрии --
единственный оператор месяца с таким разрывом.

ТРИ НЕЗАВИСИМЫХ ДОВОДА, и они сходятся на одном человеке.

1. **Итог месяца.** Книга Жумаева 47.10 га против 47.92 га машины №15 --
   **98.3 %**. Второй такой пары в октябре нет: остальные машины уже заняты
   операторами, чьи книги с ними сходятся.

2. **ИМЕНА КОНТУРОВ DJI -- главный довод.** Имя контура набирает оператор на
   пульте. Из одиннадцати строк книги Жумаева **десять** имеют контур,
   созданный в октябре-2025, и все девять его наличных строк -- в их числе:

       Муяссар Қобил      1.9   ->  «Muyassar qobil 1.9»       10.10
       Исом Саид          2.5   ->  «Isom said 2.5»            10.10
       Тўлқин Тўймурод    3.6   ->  «tulqin tuymurod  3.6»     10.10
       Абдулло Салон      3.0   ->  «Abdullo salon 4.2»        11.10
       Достон Рустам      1.3   ->  «Downton Rustam 1.3»       12.10
       Махфура Муҳаррам   1.5   ->  «Maxfura muharram1.5»      12.10
       Сайфуллобобо       4.3   ->  «SayfulloBoboziroati»      14.10
       Саид Даврон Нодир  2.0   ->  «said davron nodir»        15.10
       Агро Савдо Чорва   4.7   ->  «Agro Savdo cho»           30.10
       Учқарағай (справка) 19.2 ->  «uch qaragay agro 5.3»     11.10

   Гектары стоят прямо в именах, и они сходятся построчно. Каждая из этих
   дат -- день, в который летала машина №15.

3. **30 ОКТЯБРЯ ЛЕТАЛА ТОЛЬКО ОНА.** В этот день во всём парке единственный
   борт с вылетами -- №15, 5.49 га за 11 вылетов. Ровно в этот день создан
   контур «Agro Savdo cho», а в книге Жумаева стоит «Агро Савдо Чорва фх
   4.70 га». Совпадение имени, даты и единственной летавшей машины
   объяснить чем-то другим нельзя.

ОКНО 08.10 -- 30.10 взято по САМИМ ВЫЛЕТАМ машины: первый 08.10, последний
30.10, между ними 08, 09, 10, 11, 12, 13, 14, 15, 21 и 30 октября. Границы
не выдуманы и не округлены до месяца: за пределами окна вылетов у №15 нет.

[REASON]: окно закрыто датой `date_to`, а не оставлено открытым. Открытое
окно молча забрало бы Жумаеву ноябрь и всё, что дальше, а про ноябрь эта
миграция не знает ничего. Устав трека: назначение без срока действия
бесполезно и однажды уже увело сотни гектаров не тому человеку.

ЧЕГО ЭТА МИГРАЦИЯ НЕ ДЕЛАЕТ. Она не трогает остальные 13.24 га октября без
оператора -- машину №7 (11.97 га, 08--12.10), хвост машины №8 (1.27 га,
14--15.10) и единственный нулевой вылет машины №10 06.10. Кому они
принадлежат, из данных НЕ выводится: гарденская книга за октябрь записала
4.60 га при 43.16 га телеметрии двух гарденских бортов, и назвать хозяина
недостающего может только владелец. Это прямой аналог 1.76 га машины №2 за
сентябрь.

Безопасность / идемпотентность:
  - отказ с кодом 2 при отсутствии instance/transport.db, файл НЕ создаётся;
  - ПРЕДУСЛОВИЕ: машина №15 и оператор существуют; октябрьских вылетов у
    №15 ровно 67 на 47.92 га; ни одно существующее назначение №15 не
    пересекает окно; всего в октябре без оператора 97 вылетов на 61.16 га.
    Одна цифра иначе -- отказ с кодом 1;
  - ПОСТУСЛОВИЕ: Жумаеву отнесено 47.92 га, без оператора осталось 13.24,
    итог месяца 1478.12 не изменился, и НИ ОДИН вылет октября не получил
    двух операторов -- проверяется ДО записи в реестр;
  - одна транзакция; повторный запуск печатает «already applied»;
  - stdlib sqlite3 без Flask; вывод в консоль только ASCII.

Запуск (production, служба ОСТАНОВЛЕНА):
  cd C:\\transport-report
  .\\nssm.exe stop TransportReport
  copy instance\\transport.db instance\\transport.db.backup_20260823a
  & "C:\\Program Files\\Python314\\python.exe" migrate_drones_assign_oct2025_servis15_001.py
  & "C:\\Program Files\\Python314\\python.exe" migrate_drones_assign_oct2025_servis15_001.py --apply
  .\\nssm.exe start TransportReport

Откат КОДА: git revert коммита.
Откат ДАННЫХ: печатается готовым к вставке блоком вместе с
  DELETE FROM schema_migrations WHERE name = 'DRONES_ASSIGN_OCT2025_SERVIS15_001';
"""

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import migration_utils  # noqa: E402

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       'instance', 'transport.db')

MIGRATION_ID = 'DRONES_ASSIGN_OCT2025_SERVIS15_001'
DESCRIPTION = ('October 2025: machine No 15 (47.92 ha over 67 flights, '
               'unattributed) is bound to Zhumaev Furqat for 08-30 October.')

MONTH_FROM = '2025-10-01'
MONTH_TO = '2025-10-31'
UNIT_NUMBER = 15
OPERATOR = 'Жумаев Фуркат'
DATE_FROM = '2025-10-08'
DATE_TO = '2025-10-30'
NOTE = ('[TELEMETRY] DRONES_ASSIGN_OCT2025_SERVIS15_001: машина без '
        'оператора за октябрь-2025, 67 вылетов / 47.92 га. Опознан по трём '
        'доводам: итог книги 47.10 га (98.3 %); десять из одиннадцати строк '
        'книги имеют контур DJI, созданный в октябре, с гектарами в самом '
        'имени; 30.10 во всём парке летала только эта машина, и ровно в этот '
        'день создан контур «Agro Savdo cho» -- строка книги «Агро Савдо '
        'Чорва фх 4.70 га». Окно взято по вылетам: первый 08.10, последний '
        '30.10.')

# Числа сняты с выгрузки вылетов за 2025-10 (боевой экспорт 2026-08-23).
EXPECTED_UNIT_FLIGHTS = 67
EXPECTED_UNIT_HA = 47.9174
EXPECTED_ORPHAN_FLIGHTS = 97
EXPECTED_ORPHAN_HA = 61.1574
EXPECTED_ORPHAN_HA_AFTER = 13.2400
EXPECTED_MONTH_HA = 1478.1176
TOLERANCE_HA = 0.05

# [REASON]: та же формула, что у приложения (_drone_flight_operator_subquery):
# вылет принадлежит оператору, чьё назначение покрывает ЕГО машину в ЕГО
# МЕСТНУЮ дату UTC+5. date() с обеих сторон -- у DATE-колонок в SQLite
# affinity NUMERIC, а date(...) даёт TEXT, и полагаться на приведение типов
# значило бы молча зависеть от формата хранения.
LOCAL_DATE = "date(f.started_at, '+300 minutes')"
COVERS = (
    'SELECT f.id AS fid, f.area_ha AS ha, '
    '       COUNT(DISTINCT a.operator_id) AS covers, '
    '       MIN(a.operator_id) AS op '
    'FROM drone_flights f '
    'LEFT JOIN drone_operator_assignments a '
    '  ON a.drone_unit_id = f.drone_unit_id '
    ' AND date(a.date_from) <= ' + LOCAL_DATE +
    ' AND (a.date_to IS NULL OR date(a.date_to) >= ' + LOCAL_DATE + ') '
    'WHERE ' + LOCAL_DATE + ' BETWEEN ? AND ? '
    'GROUP BY f.id'
)


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


def unit_id(conn, number):
    found = [row[0] for row in
             conn.execute('SELECT id FROM drone_units WHERE number = ?',
                          (number,))]
    if len(found) != 1:
        raise LookupError('machine No %d resolves to %d rows' % (number,
                                                                 len(found)))
    return found[0]


def unit_month(conn, uid):
    """Вылеты и гектары машины за месяц, по местной дате."""
    row = conn.execute(
        'SELECT COUNT(*), COALESCE(SUM(f.area_ha), 0) FROM drone_flights f '
        'WHERE f.drone_unit_id = ? AND ' + LOCAL_DATE + ' BETWEEN ? AND ?',
        (uid, MONTH_FROM, MONTH_TO)).fetchone()
    return int(row[0]), float(row[1] or 0)


def attribution(conn):
    """(без оператора: вылетов, га), (у нескольких: вылетов), {оператор: га},
    (всего: вылетов, га) -- по правилу приложения."""
    orphan_n, orphan_ha, many_n, total_n, total_ha = 0, 0.0, 0, 0, 0.0
    by_op = {}
    for _fid, ha, covers, op in conn.execute(COVERS, (MONTH_FROM, MONTH_TO)):
        ha = float(ha or 0)
        total_n += 1
        total_ha += ha
        if not covers:
            orphan_n += 1
            orphan_ha += ha
        elif covers == 1:
            by_op[op] = by_op.get(op, 0.0) + ha
        else:
            many_n += 1
    return (orphan_n, orphan_ha), many_n, by_op, (total_n, total_ha)


def overlapping(conn, uid):
    """Существующие назначения этой машины, пересекающие наше окно."""
    return conn.execute(
        'SELECT id, operator_id, date_from, date_to '
        'FROM drone_operator_assignments WHERE drone_unit_id = ? '
        'AND date(date_from) <= ? '
        'AND (date_to IS NULL OR date(date_to) >= ?)',
        (uid, DATE_TO, DATE_FROM)).fetchall()


def check_precondition(conn):
    problems = []
    try:
        uid = unit_id(conn, UNIT_NUMBER)
        op_id = operator_id(conn, OPERATOR)
    except LookupError as exc:
        return ['  %s' % _ascii(exc)]

    flights, hectares = unit_month(conn, uid)
    if flights != EXPECTED_UNIT_FLIGHTS or \
            abs(hectares - EXPECTED_UNIT_HA) > TOLERANCE_HA:
        problems.append('  machine No %d in October: expected %d flights / '
                        '%.2f ha, found %d / %.2f'
                        % (UNIT_NUMBER, EXPECTED_UNIT_FLIGHTS,
                           EXPECTED_UNIT_HA, flights, hectares))
    clash = overlapping(conn, uid)
    if clash:
        problems.append('  machine No %d already has %d assignment(s) '
                        'overlapping %s..%s -- refusing to add a second '
                        'candidate' % (UNIT_NUMBER, len(clash), DATE_FROM,
                                       DATE_TO))
    (orphan_n, orphan_ha), many_n, by_op, _total = attribution(conn)
    if orphan_n != EXPECTED_ORPHAN_FLIGHTS or \
            abs(orphan_ha - EXPECTED_ORPHAN_HA) > TOLERANCE_HA:
        problems.append('  October without an operator: expected %d flights '
                        '/ %.2f ha, found %d / %.2f'
                        % (EXPECTED_ORPHAN_FLIGHTS, EXPECTED_ORPHAN_HA,
                           orphan_n, orphan_ha))
    if many_n:
        problems.append('  %d October flight(s) already have MORE THAN ONE '
                        'operator; this migration must not add to that'
                        % many_n)
    if by_op.get(op_id):
        problems.append('  %s already has %.2f ha in October -- the machine '
                        'may already be bound'
                        % (_ascii(OPERATOR), by_op[op_id]))
    if problems:
        problems.append('  These figures come from the October flights '
                        'export of 2026-08-23. A difference means flights or '
                        'assignments changed since; refusing to guess.')
    return problems


def apply_changes(conn):
    uid = unit_id(conn, UNIT_NUMBER)
    op_id = operator_id(conn, OPERATOR)
    cur = conn.execute(
        'INSERT INTO drone_operator_assignments '
        '(operator_id, drone_unit_id, date_from, date_to, note, created_at) '
        "VALUES (?, ?, ?, ?, ?, datetime('now'))",
        (op_id, uid, DATE_FROM, DATE_TO, NOTE))
    new_id = cur.lastrowid
    report = ['  machine No %d -> %-22s %s..%s  (%d flights, %.2f ha)'
              % (UNIT_NUMBER, _ascii(OPERATOR), DATE_FROM, DATE_TO,
                 EXPECTED_UNIT_FLIGHTS, EXPECTED_UNIT_HA)]
    # [REASON]: откат по ИДЕНТИФИКАТОРУ вставленной строки, а не по
    # «удали назначения этой машины». У №15 могут быть законные окна других
    # месяцев -- широкий откат снёс бы и их.
    rollback = ['DELETE FROM drone_operator_assignments WHERE id = %d;'
                % new_id]
    return report, rollback


def check_postcondition(conn):
    problems = []
    op_id = operator_id(conn, OPERATOR)
    (orphan_n, orphan_ha), many_n, by_op, (total_n, total_ha) = \
        attribution(conn)
    got = by_op.get(op_id, 0.0)
    if abs(got - EXPECTED_UNIT_HA) > TOLERANCE_HA:
        problems.append('  %s: expected %.2f ha in October, got %.2f'
                        % (_ascii(OPERATOR), EXPECTED_UNIT_HA, got))
    if abs(orphan_ha - EXPECTED_ORPHAN_HA_AFTER) > TOLERANCE_HA:
        problems.append('  without an operator: expected %.2f ha left, got '
                        '%.2f' % (EXPECTED_ORPHAN_HA_AFTER, orphan_ha))
    # [REASON]: САМАЯ ВАЖНАЯ СЕТЬ. Перекрывающее назначение не запрещено
    # моделью -- оно допущено намеренно, -- и вылет, накрытый двумя
    # операторами, уходит в корзину «Несколько операторов». Тогда гектары не
    # исчезнут из итога, но пропадут у обоих людей, и разрыв книги против
    # телеметрии станет ХУЖЕ, чем был, без единого сообщения об ошибке.
    if many_n:
        problems.append('  %d October flight(s) now have MORE THAN ONE '
                        'operator -- the new window overlaps an existing one'
                        % many_n)
    if abs(total_ha - EXPECTED_MONTH_HA) > TOLERANCE_HA:
        problems.append('  October total moved: expected %.2f ha, got %.2f'
                        % (EXPECTED_MONTH_HA, total_ha))
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

        problems = check_precondition(conn)
        if problems:
            print('PRECONDITION FAILED -- nothing changed.')
            for line in problems:
                print(line)
            return 1

        conn.execute('BEGIN')
        try:
            report, rollback = apply_changes(conn)
            problems = check_postcondition(conn)
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
    print('Postconditions: Zhumaev gets 47.92 ha of October; 13.24 ha are '
          'left without an operator (machines No 7, No 8 tail, No 10); the '
          'month total 1478.12 ha is unchanged; no flight has two operators.')
    print('')
    print('After this the October books read (book -> telemetry):')
    print('  Zhumaev Furqat         47.10 ha vs  47.92   98.3%')
    print('')
    print('STILL WITHOUT AN OPERATOR, and not decidable from the data:')
    print('  machine No 7   24 flights   11.97 ha  08-12 Oct  (Garden)')
    print('  machine No 8    5 flights    1.27 ha  14-15 Oct  (Garden tail)')
    print('  machine No 10   1 flight     0.00 ha  06 Oct')
    print('  The Garden book of October records 4.60 ha against 43.16 ha '
          'flown by the two Garden machines.')
    print('')
    print('ROLLBACK OF DATA (run in this order):')
    for line in rollback:
        print('  ' + line)
    print("  DELETE FROM schema_migrations WHERE name = '%s';" % MIGRATION_ID)
    return 0


if __name__ == '__main__':
    sys.exit(main())
