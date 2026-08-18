# -*- coding: utf-8 -*-
"""Migration DRONES_ASSIGN_SEPT2025_FIX_001 -- September 2025 operators, fixed.

Three machines changed hands INSIDE September, and the assignments loaded on
2026-08-14 did not know it: each machine carried one operator for the whole
month. The reconciliation of 2026-08-17 against the dispatchers' complete
books established the boundaries, and the owner confirmed it. This migration
moves the boundaries.

WHAT CHANGES AND WHY IT IS NOT A GUESS. Each split is confirmed by two
independent sides -- the dispatcher's own book and DJI telemetry -- and by the
dispatchers' own signed monthly summary sheet, which matched the parse of the
books for 12 operators out of 13 to 0.00 ha:

  * MACHINE 11, 6-10 September -> Hamroev Shohruh (116.11 ha). Ibodullaev's
    book starts on 10 September and gives 7.20 ha for 6-10; Hamroev's book
    gives 175.40 ha for the same days against 170.60 ha flown by BOTH Kogon
    machines. From 11 September the match is near exact: his book gives
    396.90 ha for 12-29 against 396.80 ha flown.
  * MACHINE 12, 6-16 September -> Imomov Behzod (134.50 ha). His own machine
    13 did not fly before 17 September, machine 12 carries the spelling
    `Peshku№9`, and all 6.12 ha of addresses containing "Peshku" in the whole
    month sit here. Day by day: 08 book 7.6 against 8.4, 11 book 19.9 against
    19.4, 14 book 22.4 against 22.6, 16 book 6.1 against 5.8.
  * MACHINE 15, 6-7 September -> Zhuraev Tuygun (63.45 ha). His transfer note
    "Buxoro Servis Agroklaster 67 ha, 06-09.09" is the only record on those
    days; his own machine 6 did not fly before 18 September; Zhumaev's notes
    on the same fields start on 8 September.
  * MACHINE 9 loses its assignment (10.66 ha). The spellings are `№5` and
    `Garden№5` and every flight is in Vobkent -- this is a Garden machine, not
    a Kogon one, so Hamroev cannot have flown it. THIS ONE REVERSES AN EARLIER
    VERBAL ANSWER of the owner (row loaded 2026-08-14 with note [OWNER]), and
    the dry-run prints it under its own heading for exactly that reason. No
    book in Garden carries a row for 05 or 26-27 September, so the flights are
    left WITHOUT an operator rather than moved to someone else.

After the change 12 of 13 operators fall inside the track's +-9 % trust band
against their books; before it, four were outside.

Safe / idempotent (same contract as migrate_drones_reattach_sept2025_001.py):
  - refuses to run and exits with code 2 when instance/transport.db is absent;
  - PRECONDITION: all 14 September rows must be EXACTLY as the loader of
    2026-08-14 left them -- machine, operator, both dates. One row different
    means somebody has edited assignments since, and the migration refuses
    with code 1 and a full rollback rather than overwriting that work;
  - POSTCONDITION: September hectares per operator, derived through the
    assignments exactly as the application derives them, must equal the
    figures of the confirmed reconciliation within 0.05 ha, and the totals of
    both sides must equal the month's 5 572.18 ha;
  - registered through migration_utils, skips itself on a re-run;
  - single transaction; postconditions verified BEFORE recording;
  - stdlib sqlite3 only, no Flask app context.

Run (production, from the install directory):
  cd C:\\transport-report
  & "C:\\Program Files\\Python314\\python.exe" migrate_drones_assign_sept2025_fix_001.py
  & "C:\\Program Files\\Python314\\python.exe" migrate_drones_assign_sept2025_fix_001.py --apply

Rollback of CODE: git revert of the commit.
Rollback of DATA: the dry-run and the apply both print the exact SQL that
undoes the change -- three UPDATEs back to 2025-09-05/06/06, a DELETE of the
three inserted ids, and an INSERT restoring machine 9. Copy it from the report
and run it, then
  DELETE FROM schema_migrations WHERE name = 'DRONES_ASSIGN_SEPT2025_FIX_001';
"""

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import migration_utils  # noqa: E402

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       'instance', 'transport.db')

MIGRATION_ID = 'DRONES_ASSIGN_SEPT2025_FIX_001'
DESCRIPTION = ('September 2025: three machines split between two operators '
               'each, machine 9 unassigned')

MONTH_FROM = '2025-09-01'
MONTH_TO = '2025-09-30'

# [REASON]: same offset as _drone_flight_month_expr() in drones.py. These
# drones spray at night: a flight stored at 20:30 UTC belongs to the next day
# for the people who flew it, and grouping by UTC would move whole days
# between operators at the boundary of a split.
UTC_OFFSET_MINUTES = 300

# Ровно то, что оставил загрузчик 2026-08-14. Предусловие сверяется с этим
# списком: машина, оператор, обе даты.
BASELINE = (
    (1, 'Рухиллоев Сайфулло', '2025-09-02', '2025-09-30'),
    (3, 'Холмуродов Шахзод', '2025-09-04', '2025-09-30'),
    (4, 'Анваров Усмон', '2025-09-17', '2025-09-29'),
    (5, 'Кобилов Фаррух', '2025-09-05', '2025-09-29'),
    (6, 'Жураев Туйгун', '2025-09-17', '2025-09-28'),
    (7, 'Файзуллаев Шохрух', '2025-09-17', '2025-09-30'),
    (8, 'Кодиров Нурали', '2025-09-04', '2025-09-30'),
    (9, 'Хамроев Шохрух', '2025-09-05', '2025-09-27'),
    (10, 'Хамроев Шохрух', '2025-09-07', '2025-09-22'),
    (11, 'Ибодуллаев Хасанбой', '2025-09-05', '2025-09-30'),
    (12, 'Кудратов Мухриддин', '2025-09-06', '2025-09-30'),
    (13, 'Имомов Бехзод', '2025-09-17', '2025-09-30'),
    (14, 'Файзуллаев Шоди', '2025-09-17', '2025-10-31'),
    (15, 'Жумаев Фуркат', '2025-09-06', '2025-09-30'),
)

# Машине сдвигается начало: (машина, оператор, было, стало).
SHIFTS = (
    (11, 'Ибодуллаев Хасанбой', '2025-09-05', '2025-09-11'),
    (12, 'Кудратов Мухриддин', '2025-09-06', '2025-09-17'),
    (15, 'Жумаев Фуркат', '2025-09-06', '2025-09-08'),
)

# Новые строки: (машина, оператор, с, по, почему).
INSERTS = (
    (11, 'Хамроев Шохрух', '2025-09-06', '2025-09-10',
     'сверка 2026-08-17: книга Хасанбоя начинается 10.09 и даёт 7.20 га за '
     '06-10, книга Хамроева -- 175.40 га при 170.60 га обеих машин Когона'),
    (12, 'Имомов Бехзод', '2025-09-06', '2025-09-16',
     'сверка 2026-08-17: машина 13 взлетела 17.09; ник машины 12 -- Peshku№9, '
     'и все 6.12 га адресов со словом Peshku за месяц лежат здесь'),
    (15, 'Жураев Туйгун', '2025-09-06', '2025-09-07',
     'сверка 2026-08-17: справка "Бухоро Сервис Агрокластер 67 га, '
     '06-09.09" -- единственная запись на эти дни, машина 6 взлетела 18.09'),
)

# Строка, которая снимается целиком.
UNASSIGN = (9, 'Хамроев Шохрух', '2025-09-05', '2025-09-27',
            'сверка 2026-08-17: ники №5 / Garden№5, все вылеты в Вобкенте -- '
            'машина гарденская; в книгах Гардена строк на 05 и 26-27.09 нет')

# Постусловие: гектары сентября по оператору, выведенные через назначения.
# Числа -- из подтверждённой владельцем сверки 2026-08-17.
EXPECTED_HA = (
    ('Рухиллоев Сайфулло', 1009.73),
    ('Кодиров Нурали', 890.22),
    ('Холмуродов Шахзод', 795.06),
    ('Ибодуллаев Хасанбой', 418.44),
    ('Хамроев Шохрух', 415.55),
    ('Имомов Бехзод', 407.70),
    ('Жумаев Фуркат', 388.40),
    ('Кобилов Фаррух', 311.27),
    ('Анваров Усмон', 217.50),
    ('Кудратов Мухриддин', 199.19),
    ('Жураев Туйгун', 193.94),
    ('Файзуллаев Шоди', 168.80),
    ('Файзуллаев Шохрух', 143.97),
)
EXPECTED_UNASSIGNED_HA = 12.41   # машины 2 и 9
EXPECTED_MONTH_HA = 5572.18
TOLERANCE = 0.05


def normalize(name):
    """Имена в справочнике и здесь могут быть набраны узбекскими буквами."""
    table = {ord('қ'): 'к', ord('ғ'): 'г', ord('ў'): 'у', ord('ҳ'): 'х',
             ord('ҷ'): 'ж', ord('ъ'): '', ord('ь'): '', ord('ё'): 'е'}
    return ' '.join(str(name).lower().translate(table).split())


def _ascii(text):
    return str(text).encode('ascii', 'replace').decode('ascii')


def load_maps(conn):
    """Справочники: номер машины -> id, нормализованное имя -> id."""
    units = {}
    for unit_id, number in conn.execute(
            'SELECT id, number FROM drone_units'):
        units[int(number)] = unit_id
    operators = {}
    for op_id, full_name in conn.execute(
            'SELECT id, full_name FROM drone_operators'):
        operators.setdefault(normalize(full_name), []).append(op_id)
    return units, operators


def september_rows(conn):
    """Назначения, покрывающие сентябрь-2025, с номером машины и именем."""
    return conn.execute(
        'SELECT a.id, u.number, o.full_name, a.date_from, a.date_to '
        'FROM drone_operator_assignments a '
        'JOIN drone_units u ON u.id = a.drone_unit_id '
        'JOIN drone_operators o ON o.id = a.operator_id '
        'WHERE a.date_from <= ? AND (a.date_to IS NULL OR a.date_to >= ?) '
        'ORDER BY u.number, a.date_from', (MONTH_TO, MONTH_FROM)).fetchall()


def check_precondition(conn):
    """Сентябрьские назначения обязаны быть ровно те, что оставил загрузчик."""
    got = [(int(number), normalize(name), date_from, date_to)
           for _id, number, name, date_from, date_to in september_rows(conn)]
    want = [(number, normalize(name), date_from, date_to)
            for number, name, date_from, date_to in BASELINE]
    if got == want:
        return []
    problems = ['September assignments differ from the 2026-08-14 baseline:']
    for row in want:
        if row not in got:
            problems.append('  missing: unit %-3d %-24s %s..%s'
                            % (row[0], _ascii(row[1]), row[2], row[3]))
    for row in got:
        if row not in want:
            problems.append('  unexpected: unit %-3d %-24s %s..%s'
                            % (row[0], _ascii(row[1]), row[2], row[3]))
    return problems


def hectares_by_operator(conn):
    """Гектары сентября по оператору -- тем же правилом, что и приложение.

    [REASON]: вылет, покрытый БОЛЕЕ ЧЕМ ОДНИМ назначением, не отдаётся молча
    одному из них: модель это прямо запрещает. Такой вылет попадает в корзину
    'ambiguous', и постусловие требует, чтобы она была пуста -- перекрытие
    внутри месяца означало бы, что границы раздела поставлены неверно.
    """
    modifier = '+%d minutes' % UTC_OFFSET_MINUTES
    rows = conn.execute(
        "SELECT f.drone_unit_id, "
        "       date(datetime(f.started_at, ?)) AS day, "
        "       f.area_ha "
        "FROM drone_flights f "
        "WHERE strftime('%Y-%m', datetime(f.started_at, ?)) = '2025-09'",
        (modifier, modifier)).fetchall()
    covers = conn.execute(
        'SELECT a.drone_unit_id, o.full_name, a.date_from, a.date_to '
        'FROM drone_operator_assignments a '
        'JOIN drone_operators o ON o.id = a.operator_id').fetchall()

    by_operator = {}
    unassigned = 0.0
    ambiguous = 0.0
    total = 0.0
    for unit_id, day, area in rows:
        area = float(area or 0)
        total += area
        names = [name for cover_unit, name, date_from, date_to in covers
                 if cover_unit == unit_id and date_from <= day
                 and (date_to is None or date_to >= day)]
        if not names:
            unassigned += area
        elif len(set(names)) > 1:
            ambiguous += area
        else:
            key = normalize(names[0])
            by_operator[key] = by_operator.get(key, 0.0) + area
    return by_operator, unassigned, ambiguous, total


def check_postcondition(conn):
    by_operator, unassigned, ambiguous, total = hectares_by_operator(conn)
    problems = []
    for name, expected in EXPECTED_HA:
        got = by_operator.get(normalize(name), 0.0)
        if abs(got - expected) > TOLERANCE:
            problems.append('  %-24s expected %8.2f ha, got %8.2f'
                            % (_ascii(name), expected, got))
    if abs(unassigned - EXPECTED_UNASSIGNED_HA) > TOLERANCE:
        problems.append('  flights without an operator: expected %.2f ha, '
                        'got %.2f' % (EXPECTED_UNASSIGNED_HA, unassigned))
    if ambiguous > TOLERANCE:
        problems.append('  %.2f ha covered by two operators at once -- the '
                        'split boundaries overlap' % ambiguous)
    if abs(total - EXPECTED_MONTH_HA) > TOLERANCE:
        problems.append('  month total: expected %.2f ha, got %.2f'
                        % (EXPECTED_MONTH_HA, total))
    extra = sorted(key for key in by_operator
                   if key not in {normalize(n) for n, _ in EXPECTED_HA})
    for key in extra:
        problems.append('  unexpected operator in September: %s (%.2f ha)'
                        % (_ascii(key), by_operator[key]))
    return problems


def resolve(units, operators, number, name):
    if number not in units:
        raise LookupError('unit %d is absent from drone_units' % number)
    found = operators.get(normalize(name), [])
    if len(found) != 1:
        raise LookupError('operator %r resolves to %d rows in '
                          'drone_operators' % (_ascii(name), len(found)))
    return units[number], found[0]


def apply_changes(conn, units, operators):
    """Выполняет правки. Возвращает строки отчёта и SQL отката."""
    report = []
    rollback = []

    for number, name, was, now in SHIFTS:
        unit_id, operator_id = resolve(units, operators, number, name)
        cursor = conn.execute(
            'UPDATE drone_operator_assignments SET date_from = ? '
            'WHERE drone_unit_id = ? AND operator_id = ? AND date_from = ?',
            (now, unit_id, operator_id, was))
        if cursor.rowcount != 1:
            raise RuntimeError('unit %d: expected 1 row to shift, touched %d'
                               % (number, cursor.rowcount))
        report.append('  unit %-3d %-24s date_from %s -> %s'
                      % (number, _ascii(name), was, now))
        rollback.append(
            "UPDATE drone_operator_assignments SET date_from = '%s' "
            "WHERE drone_unit_id = %d AND operator_id = %d "
            "AND date_from = '%s';" % (was, unit_id, operator_id, now))

    inserted = []
    for number, name, date_from, date_to, why in INSERTS:
        unit_id, operator_id = resolve(units, operators, number, name)
        cursor = conn.execute(
            'INSERT INTO drone_operator_assignments '
            '(operator_id, drone_unit_id, date_from, date_to, note, '
            ' created_at) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)',
            (operator_id, unit_id, date_from, date_to,
             '[RECON-2026-08-17] ' + why))
        inserted.append(cursor.lastrowid)
        report.append('  unit %-3d %-24s %s..%s  NEW id=%d'
                      % (number, _ascii(name), date_from, date_to,
                         cursor.lastrowid))
    if inserted:
        rollback.append('DELETE FROM drone_operator_assignments WHERE id IN '
                        '(%s);' % ', '.join(str(i) for i in inserted))

    number, name, date_from, date_to, why = UNASSIGN
    unit_id, operator_id = resolve(units, operators, number, name)
    row = conn.execute(
        'SELECT id, note FROM drone_operator_assignments '
        'WHERE drone_unit_id = ? AND operator_id = ? AND date_from = ?',
        (unit_id, operator_id, date_from)).fetchone()
    if row is None:
        raise RuntimeError('unit %d: the row to remove is not there' % number)
    old_id, old_note = row
    cursor = conn.execute(
        'DELETE FROM drone_operator_assignments WHERE id = ?', (old_id,))
    if cursor.rowcount != 1:
        raise RuntimeError('unit %d: expected 1 row to delete, deleted %d'
                           % (number, cursor.rowcount))
    report.append('  unit %-3d %-24s %s..%s  REMOVED id=%d  (%s)'
                  % (number, _ascii(name), date_from, date_to, old_id, why))
    rollback.append(
        "INSERT INTO drone_operator_assignments "
        "(id, operator_id, drone_unit_id, date_from, date_to, note) "
        "VALUES (%d, %d, %d, '%s', '%s', %s);"
        % (old_id, operator_id, unit_id, date_from, date_to,
           'NULL' if old_note is None
           else "'%s'" % str(old_note).replace("'", "''")))
    return report, rollback


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

    # [REASON]: migration_utils открывает СВОЁ соединение по своей константе.
    # Без этой строки реестр миграций писался бы в боевую базу, даже когда
    # скрипт проверяют на синтетической копии через --db -- и «применено»
    # оказалось бы записано там, где ничего не менялось.
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

        units, operators = load_maps(conn)
        conn.execute('BEGIN')
        try:
            report, rollback = apply_changes(conn, units, operators)
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
    print('Postconditions: hectares per operator match the reconciliation '
          'of 2026-08-17 within %.2f ha; month total %.2f ha.'
          % (TOLERANCE, EXPECTED_MONTH_HA))
    print('')
    print('NOTE: machine 9 loses its operator. That row was loaded on '
          '2026-08-14 from a VERBAL answer of the owner; the books and the '
          'telemetry disagree with it. If the owner keeps his earlier answer, '
          'do not apply this migration.')
    print('')
    print('ROLLBACK OF DATA (run in this order):')
    for line in rollback:
        print('  ' + line)
    print("  DELETE FROM schema_migrations WHERE name = '%s';" % MIGRATION_ID)
    return 0


if __name__ == '__main__':
    sys.exit(main())
