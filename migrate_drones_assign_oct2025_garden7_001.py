# -*- coding: utf-8 -*-
"""Migration DRONES_ASSIGN_OCT2025_GARDEN7_001 -- гарденская машина №7 в
октябре была у Файзуллаева Шохруха.

ЧТО ПОЧИНЕНО. За октябрь-2025 машина №7 (ник `7 Garden`) налетала **11.97 га
за 24 вылета**, 08--12 октября, и ни один из них не отнесён ни к кому:
октябрьского окна у неё в `drone_operator_assignments` нет вовсе.

ЧЕМ ОПОЗНАН ХОЗЯИН. **Решением владельца 2026-08-24**, дословно: «№7 Garden --
11.97 га (08--12.10) - запиши на оператора Файзуллаева Шохруха».

[REASON]: из данных этого вывести было НЕЛЬЗЯ, и миграция не притворяется,
что вывела. Гарденская книга октября -- лист `свод ичи (Нурали)` -- содержит
одну строку на 4.60 га, тогда как два гарденских борта (№7 и №8) налетали
43.16 га. Книги Файзуллаева Шохруха за октябрь нет вовсе, поэтому ни итог, ни
дневная раскладка, ни имена контуров опознать оператора не могли: сравнивать
телеметрию не с чем. Это знание владельца, и в `note` оно помечено `[OWNER]`,
а не `[TELEMETRY]` -- ровно как в загрузчике назначений сентября.

ЧТО ЭТО ДАЁТ. Файзуллаев Шохрух получает 11.97 га телеметрии при НУЛЕ гектаров
книги: книги за октябрь у него нет. Полосу доверия +-9 % к нему применять
нечего, и отчёт покажет его строкой «книга 0.00 / телеметрия 11.97» -- это
правда, а не дефект.

ПОРЯДОК. Миграция идёт ВТОРОЙ, после DRONES_ASSIGN_OCT2025_SERVIS15_001, и
проверяет это по реестру. [REASON]: обе пришпилены к числу вылетов без
оператора, и первая меняет его с 97 на 30. Запуск в обратном порядке дал бы
отказ по предусловию, а сообщение об этом было бы про цифры, а не про
порядок; здесь порядок назван прямо.

ЧЕГО ЭТА МИГРАЦИЯ НЕ ДЕЛАЕТ. Не трогает оставшиеся 1.27 га -- хвост машины
№8 за 14--15.10 и единственный нулевой вылет машины №10 за 06.10. Владелец
решил их «не засчитывать и выкинуть из мониторинга»; **выбросить отдельные
вылеты программа не умеет** -- исключение (`is_excluded` / `excluded_from`)
устроено для МАШИНЫ целиком с даты, а №8 -- рабочий гарденский борт, и
исключать её нельзя. Оставленные без оператора, эти 1.27 га в отчёт по
операторам и не попадают: «не засчитано на оператора» -- это ровно то
состояние, в котором они уже находятся.

Безопасность / идемпотентность:
  - отказ с кодом 2 при отсутствии instance/transport.db, файл НЕ создаётся;
  - ПРЕДУСЛОВИЕ: SERVIS15 применена; машина №7 и оператор существуют;
    октябрьских вылетов у №7 ровно 24 на 11.97 га; ни одно существующее
    назначение №7 не пересекает окно; без оператора за месяц ровно 30
    вылетов на 13.24 га. Одна цифра иначе -- отказ с кодом 1;
  - ПОСТУСЛОВИЕ: Файзуллаеву отнесено 11.97 га, без оператора осталось
    1.27, итог месяца 1478.12 не изменился, и НИ ОДИН вылет октября не
    получил двух операторов -- проверяется ДО записи в реестр;
  - одна транзакция; повторный запуск печатает «already applied»;
  - stdlib sqlite3 без Flask; вывод в консоль только ASCII.

Запуск (production, служба ОСТАНОВЛЕНА, ПОСЛЕ SERVIS15):
  cd C:\\transport-report
  & "C:\\Program Files\\Python314\\python.exe" migrate_drones_assign_oct2025_garden7_001.py
  & "C:\\Program Files\\Python314\\python.exe" migrate_drones_assign_oct2025_garden7_001.py --apply

Откат КОДА: git revert коммита.
Откат ДАННЫХ: печатается готовым к вставке блоком вместе с
  DELETE FROM schema_migrations WHERE name = 'DRONES_ASSIGN_OCT2025_GARDEN7_001';
"""

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import migration_utils  # noqa: E402

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       'instance', 'transport.db')

MIGRATION_ID = 'DRONES_ASSIGN_OCT2025_GARDEN7_001'
REQUIRES = 'DRONES_ASSIGN_OCT2025_SERVIS15_001'
DESCRIPTION = ('October 2025: machine No 7 (11.97 ha over 24 flights, '
               "unattributed) is bound to Fayzullaev Shohruh for 08-12 "
               "October by the owner's decision.")

MONTH_FROM = '2025-10-01'
MONTH_TO = '2025-10-31'
UNIT_NUMBER = 7
OPERATOR = 'Файзуллаев Шохрух'
DATE_FROM = '2025-10-08'
DATE_TO = '2025-10-12'
NOTE = ('[OWNER] DRONES_ASSIGN_OCT2025_GARDEN7_001: решение владельца '
        '2026-08-24 -- «№7 Garden 11.97 га (08-12.10) записать на оператора '
        'Файзуллаева Шохруха». Из данных не выводится и не выводилось: книги '
        'Файзуллаева за октябрь нет, сравнивать телеметрию не с чем. '
        'Гарденская книга октября записала 4.60 га при 43.16 га, налетанных '
        'двумя гарденскими бортами. Окно взято по вылетам машины: первый '
        '08.10, последний 12.10.')

# Числа сняты с выгрузки вылетов за 2025-10 (боевой экспорт 2026-08-23).
EXPECTED_UNIT_FLIGHTS = 24
EXPECTED_UNIT_HA = 11.9728
EXPECTED_ORPHAN_FLIGHTS = 30
EXPECTED_ORPHAN_HA = 13.2400
EXPECTED_ORPHAN_HA_AFTER = 1.2672
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
        # [REASON]: порядок назван прямо. Обе миграции пришпилены к числу
        # вылетов без оператора, и первая меняет его с 97 на 30; запуск в
        # обратном порядке дал бы отказ про цифры, а не про порядок.
        if not migration_utils.is_migration_applied(REQUIRES):
            print('PRECONDITION FAILED -- nothing changed.')
            print('  %s must be applied FIRST: it binds machine No 15, and '
                  'both migrations are' % REQUIRES)
            print('  pinned to the number of unattributed October flights '
                  '(97 before it, 30 after).')
            return 1

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
    # [REASON]: числа берутся из констант модуля, а не переписаны заново
    # текстом. Прежняя строка была буквально скопирована из
    # migrate_drones_assign_oct2025_servis15_001.py (тот же Zhumaev,
    # 47.92 га) и напечатала чужую сводку под каждым прогоном этой
    # миграции -- проверка постусловий это не ловит, потому что она
    # сверяет данные в базе, а не текст сообщения. Имя -- ручная
    # транслитерация, как и строкой ниже: _ascii(OPERATOR) даёт только
    # '?????????? ??????', это замена не-ASCII на '?', а не транслитератор.
    print('Postconditions: Fayzullaev Shohruh gets %.2f ha of October; '
          '%.4f ha are left without an operator (machines No 8 tail, '
          'No 10); the month total %.2f ha is unchanged; no flight has '
          'two operators.'
          % (EXPECTED_UNIT_HA, EXPECTED_ORPHAN_HA_AFTER, EXPECTED_MONTH_HA))
    print('')
    print('After this the October books read (book -> telemetry):')
    print('  Fayzullaev Shohruh      0.00 ha vs  11.97   -- he has NO October '
          'book at all,')
    print('  so the +-9% band does not apply to him and the report shows the '
          'plain fact.')
    print('')
    print('STILL WITHOUT AN OPERATOR, by the owner\'s decision:')
    print('  machine No 8    5 flights    1.27 ha  14-15 Oct  (Garden tail)')
    print('  machine No 10   1 flight     0.00 ha  06 Oct')
    print('  Individual flights CANNOT be dropped from reporting: exclusion '
          'is per MACHINE from a')
    print('  date, and No 8 is a working Garden drone. Left unattributed, '
          'they stay out of the')
    print('  per-operator report already -- which is what "not counted to an '
          'operator" means.')
    print('')
    print('ROLLBACK OF DATA (run in this order):')
    for line in rollback:
        print('  ' + line)
    print("  DELETE FROM schema_migrations WHERE name = '%s';" % MIGRATION_ID)
    return 0


if __name__ == '__main__':
    sys.exit(main())
