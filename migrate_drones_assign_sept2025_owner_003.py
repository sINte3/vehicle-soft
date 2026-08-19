# -*- coding: utf-8 -*-
"""Migration DRONES_ASSIGN_SEPT2025_OWNER_003 -- три решения владельца.

Владелец разобрал 2026-08-19 оставшиеся неопознанными вылеты и вынес три
решения. Все три -- о том, кто на какой машине летал; книги и деньги не
трогаются вовсе.

  1. МАШИНА 9, 27.09, 4.64 га -> КОБИЛОВ ФАРРУХ.
     «№9 налетала 4.64 - пиши на Кобилова Фарруха, раз в книге диспетчера это
     есть. Значит пользовался этим дроном.» В книге Кобилова за 27.09 стоит
     «Фатулло Обод фх, 5.5 га», а его собственная машина 5 в этот день дала
     0.09 га. Ник машины 9 -- Garden№5, вылет в Улугхужа (Вобкент).

  2. МАШИНА 9, 05.09, 6.02 га -> КОДИРОВ НУРАЛИ.
     «№9 за 05.09 - 6,02 га: напиши на Кодирова Нурали, у него как раз почти
     столько в плюсе.» Машина 9 -- гарденская, и Кодиров держит гарденскую 8
     весь месяц. В его книге 324.30 га строк вообще без дат, так что
     непокрытые гектары у него есть.

  3. МАШИНА 4, 23 и 27.09 -> ХОЛМУРОДОВ ШАХЗОД.
     «машина №4 за 23 и 27.09 - да, если у нас все нашлось и сходится.»
     Нашлось: в книге Кобилова этих дней нет, а в книге ХОЛМУРОДОВА они
     описаны -- «Анвар Ота замини 27.5 га» и «Умарбобо Усмонов 23.5 га».
     Контуры DJI, которые оператор набирает на пульте, созданы ровно в эти
     дни: anvar ota 1..4, 1.1, 1.2 -- 23.09.2025, 30.92 га; umarbobo usmonov
     1..7 -- 27.09.2025, 23.17 га. У Анварова на эти дни строк нет.

     Правило владельца: гектары пишутся на оператора, за которым числится
     работа и через которого прошли деньги. Поэтому машина на эти два дня
     переходит к тому, чья книга их описывает.

ЧАСТЫЕ ПЕРЕСАДКИ -- НОРМА. После этой миграции Холмуродов ведёт три машины
(3 весь месяц, 5 за 05-06.09, 4 за 23 и 27.09), Кобилов две, Кодиров две.
Модель это допускает: запрещено обратное -- одна машина у двух операторов в
один день, и постусловие требует, чтобы корзина «спорное» осталась пустой.
Именно поэтому строка Анварова на машине 4 не просто урезается, а режется на
три куска встык к двум дням Холмуродова: любой зазор оставил бы день без
оператора, любое нахлёстывание -- спорным.

ЧТО ЭТО ДАЁТ. Все 13 операторов ложатся между 93 % и 104 % своей книги, а
без оператора остаётся 1.76 га -- только машина 2 (0.06 га 27.09 и 1.69 га
29.09), про которую решения ещё нет:

    оператор              книга    было    стало
    Кодиров Нурали       884.50  890.22   896.24   100.6 % -> 101.3 %
    Холмуродов Шахзод    879.50  815.64   864.12    92.7 % ->  98.3 %
    Кобилов Фаррух       285.90  290.68   295.33   101.7 % -> 103.3 %
    Анваров Усмон        172.80  217.50   169.02   125.9 % ->  97.8 %
    без оператора            --   12.41     1.76

Безопасность / идемпотентность:
  - отказ с кодом 2 при отсутствии instance/transport.db, файл НЕ создаётся;
  - ПРЕДУСЛОВИЕ: все 17 сентябрьских назначений обязаны быть ровно теми,
    какими их оставила DRONES_ASSIGN_SEPT2025_M5_SPLIT_001. Одна строка иначе
    -- отказ с кодом 1 и полный откат, а не затирание чужой работы;
  - ПОСТУСЛОВИЕ: гектары сентября по операторам, выведенные через назначения
    ровно так, как их выводит приложение, обязаны совпасть со сверкой с
    точностью 0.05 га; сумма месяца 5572.18; без оператора 1.76; корзина
    «спорное» ПУСТА;
  - одна транзакция; постусловия проверяются ДО записи в реестр;
  - повторный запуск печатает «already applied» и ничего не делает;
  - stdlib sqlite3 без Flask; вывод в консоль только ASCII.

Запуск (production, служба ОСТАНОВЛЕНА; после REDATE_002 и M5_SPLIT_001):
  cd C:\\transport-report
  .\\nssm.exe stop TransportReport
  copy instance\\transport.db instance\\transport.db.backup_20260819
  & "C:\\Program Files\\Python314\\python.exe" migrate_drones_assign_sept2025_owner_003.py
  & "C:\\Program Files\\Python314\\python.exe" migrate_drones_assign_sept2025_owner_003.py --apply
  .\\nssm.exe start TransportReport

Откат КОДА: git revert коммита.
Откат ДАННЫХ: печатается и сухим прогоном, и боевым, вместе с
  DELETE FROM schema_migrations WHERE name = 'DRONES_ASSIGN_SEPT2025_OWNER_003';
"""

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import migration_utils  # noqa: E402

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       'instance', 'transport.db')

MIGRATION_ID = 'DRONES_ASSIGN_SEPT2025_OWNER_003'
DESCRIPTION = ('September 2025: unit 4 on 23 and 27.09 -> Kholmurodov, '
               'unit 9 on 05.09 -> Kodirov and on 27.09 -> Kobilov. '
               'Three decisions of the owner, 2026-08-19.')

MONTH_FROM = '2025-09-01'
MONTH_TO = '2025-09-30'

# [REASON]: тот же сдвиг, что и в _drone_flight_month_expr() в drones.py.
# Эти дроны работают по ночам: вылет, сохранённый в 20:30 UTC, для людей
# относится к следующему дню, и группировка по UTC перебросила бы целые дни
# между операторами прямо на границе раздела.
UTC_OFFSET_MINUTES = 300

# Ровно то, что оставила DRONES_ASSIGN_SEPT2025_M5_SPLIT_001.
BASELINE = (
    (1, 'Рухиллоев Сайфулло', '2025-09-02', '2025-09-30'),
    (3, 'Холмуродов Шахзод', '2025-09-04', '2025-09-30'),
    (4, 'Анваров Усмон', '2025-09-17', '2025-09-29'),
    (5, 'Холмуродов Шахзод', '2025-09-05', '2025-09-06'),
    (5, 'Кобилов Фаррух', '2025-09-07', '2025-09-29'),
    (6, 'Жураев Туйгун', '2025-09-17', '2025-09-28'),
    (7, 'Файзуллаев Шохрух', '2025-09-17', '2025-09-30'),
    (8, 'Кодиров Нурали', '2025-09-04', '2025-09-30'),
    (10, 'Хамроев Шохрух', '2025-09-07', '2025-09-22'),
    (11, 'Хамроев Шохрух', '2025-09-06', '2025-09-10'),
    (11, 'Ибодуллаев Хасанбой', '2025-09-11', '2025-09-30'),
    (12, 'Имомов Бехзод', '2025-09-06', '2025-09-16'),
    (12, 'Кудратов Мухриддин', '2025-09-17', '2025-09-30'),
    (13, 'Имомов Бехзод', '2025-09-17', '2025-09-30'),
    (14, 'Файзуллаев Шоди', '2025-09-17', '2025-10-31'),
    (15, 'Жураев Туйгун', '2025-09-06', '2025-09-07'),
    (15, 'Жумаев Фуркат', '2025-09-08', '2025-09-30'),
)

# Строка Анварова на машине 4 режется: ей остаётся первый кусок.
SPLIT_UNIT = 4
SPLIT_OP = 'Анваров Усмон'
SPLIT_OLD_FROM = '2025-09-17'
SPLIT_OLD_TO = '2025-09-29'
SPLIT_NEW_TO = '2025-09-22'

# Новые строки: (машина, оператор, с, по, основание в note).
INSERTS = (
    (4, 'Холмуродов Шахзод', '2025-09-23', '2025-09-23',
     'решение владельца 2026-08-19: день описан в книге Холмуродова -- '
     '"Анвар Ота замини 27.5 га"; контуры DJI anvar ota 1..4, 1.1, 1.2 '
     'созданы 23.09.2025 на 30.92 га; у Анварова строк на этот день нет'),
    (4, 'Анваров Усмон', '2025-09-24', '2025-09-26',
     'встык между двумя днями Холмуродова -- иначе день остался бы без '
     'оператора или попал бы в спорное'),
    (4, 'Холмуродов Шахзод', '2025-09-27', '2025-09-27',
     'решение владельца 2026-08-19: день описан в книге Холмуродова -- '
     '"Умарбобо Усмонов 23.5 га"; контуры DJI umarbobo usmonov 1..7 созданы '
     '27.09.2025 на 23.17 га; у Анварова строк на этот день нет'),
    (4, 'Анваров Усмон', '2025-09-28', '2025-09-29',
     'хвост исходной строки Анварова после дня Холмуродова'),
    (9, 'Кодиров Нурали', '2025-09-05', '2025-09-05',
     'решение владельца 2026-08-19: "напиши на Кодирова Нурали, у него как '
     'раз почти столько в плюсе". Машина 9 гарденская (ник Garden№5), '
     'Кодиров держит гарденскую 8 весь месяц; в его книге 324.30 га строк '
     'без дат'),
    (9, 'Кобилов Фаррух', '2025-09-27', '2025-09-27',
     'решение владельца 2026-08-19: "раз в книге диспетчера это есть, '
     'значит пользовался этим дроном". В книге Кобилова 27.09 -- '
     '"Фатулло Обод фх 5.5 га", а его машина 5 дала в этот день 0.09 га'),
)

# Постусловие: гектары сентября по оператору, выведенные через назначения.
EXPECTED_HA = (
    ('Рухиллоев Сайфулло', 1009.73),
    ('Кодиров Нурали', 896.24),
    ('Холмуродов Шахзод', 864.12),
    ('Ибодуллаев Хасанбой', 418.44),
    ('Хамроев Шохрух', 415.55),
    ('Имомов Бехзод', 407.70),
    ('Жумаев Фуркат', 388.40),
    ('Кобилов Фаррух', 295.33),
    ('Кудратов Мухриддин', 199.19),
    ('Жураев Туйгун', 193.94),
    ('Анваров Усмон', 169.02),
    ('Файзуллаев Шоди', 168.80),
    ('Файзуллаев Шохрух', 143.97),
)
EXPECTED_UNASSIGNED_HA = 1.76    # только машина 2: 0.06 (27.09) + 1.69 (29.09)
EXPECTED_MONTH_HA = 5572.18
TOLERANCE = 0.05


def normalize(name):
    """Имена в справочнике и здесь могут быть набраны узбекскими буквами."""
    table = {ord('қ'): 'к', ord('ғ'): 'г', ord('ў'): 'у', ord('ҳ'): 'х',
             ord('ҷ'): 'ж', ord('ъ'): '', ord('ь'): '', ord('ё'): 'е'}
    return ' '.join(str(name).lower().translate(table).split())


def _ascii(text):
    return str(text).encode('ascii', 'replace').decode('ascii')


def september_rows(conn):
    return conn.execute(
        'SELECT a.id, u.number, o.full_name, a.date_from, a.date_to '
        'FROM drone_operator_assignments a '
        'JOIN drone_units u ON u.id = a.drone_unit_id '
        'JOIN drone_operators o ON o.id = a.operator_id '
        'WHERE a.date_from <= ? AND (a.date_to IS NULL OR a.date_to >= ?) '
        'ORDER BY u.number, a.date_from', (MONTH_TO, MONTH_FROM)).fetchall()


def check_precondition(conn):
    """Назначения обязаны быть ровно те, что оставила M5_SPLIT_001."""
    got = [(int(number), normalize(name), date_from, date_to)
           for _id, number, name, date_from, date_to in september_rows(conn)]
    want = [(number, normalize(name), date_from, date_to)
            for number, name, date_from, date_to in BASELINE]
    if got == want:
        return []
    problems = ['  September assignments differ from what '
                'DRONES_ASSIGN_SEPT2025_M5_SPLIT_001 left behind:']
    for row in want:
        if row not in got:
            problems.append('    missing: unit %-2d %-24s %s..%s'
                            % (row[0], _ascii(row[1]), row[2], row[3]))
    for row in got:
        if row not in want:
            problems.append('    extra:   unit %-2d %-24s %s..%s'
                            % (row[0], _ascii(row[1]), row[2], row[3]))
    problems.append('  Apply the earlier September migrations first, or find '
                    'out who edited assignments after them.')
    return problems


def hectares_by_operator(conn):
    """Гектары сентября по оператору -- тем же правилом, что и приложение.

    [REASON]: вылет, покрытый БОЛЕЕ ЧЕМ ОДНИМ назначением, не отдаётся молча
    одному из них -- он попадает в корзину 'ambiguous', и постусловие требует,
    чтобы она была пуста. Эта миграция режет одну строку на три куска и
    вставляет между ними чужие дни; ошибка в один день на любой из четырёх
    границ дала бы либо дыру, либо нахлёст.
    """
    modifier = '+%d minutes' % UTC_OFFSET_MINUTES
    rows = conn.execute(
        "SELECT f.drone_unit_id, date(datetime(f.started_at, ?)) AS day, "
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
        elif len({normalize(name) for name in names}) > 1:
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
        problems.append('  %.2f ha covered by two operators at once -- a '
                        'split boundary overlaps' % ambiguous)
    if abs(total - EXPECTED_MONTH_HA) > TOLERANCE:
        problems.append('  month total: expected %.2f ha, got %.2f'
                        % (EXPECTED_MONTH_HA, total))
    extra = sorted(key for key in by_operator
                   if key not in {normalize(n) for n, _ in EXPECTED_HA})
    for key in extra:
        problems.append('  unexpected operator in September: %s (%.2f ha)'
                        % (_ascii(key), by_operator[key]))
    return problems


def resolve(conn, number, name):
    row = conn.execute('SELECT id FROM drone_units WHERE number = ?',
                       (number,)).fetchone()
    if row is None:
        raise LookupError('unit %d is absent from drone_units' % number)
    found = [op_id for op_id, full_name in
             conn.execute('SELECT id, full_name FROM drone_operators')
             if normalize(full_name) == normalize(name)]
    if len(found) != 1:
        raise LookupError('operator %s resolves to %d rows in drone_operators'
                          % (_ascii(name), len(found)))
    return row[0], found[0]


def apply_changes(conn):
    """Выполняет правку. Возвращает строки отчёта и SQL отката."""
    report = []
    rollback = []

    unit_id, operator_id = resolve(conn, SPLIT_UNIT, SPLIT_OP)
    cursor = conn.execute(
        'UPDATE drone_operator_assignments SET date_to = ? '
        'WHERE drone_unit_id = ? AND operator_id = ? '
        'AND date_from = ? AND date_to = ?',
        (SPLIT_NEW_TO, unit_id, operator_id, SPLIT_OLD_FROM, SPLIT_OLD_TO))
    if cursor.rowcount != 1:
        raise LookupError('unit %d %s %s..%s: expected 1 row to shorten, '
                          'touched %d' % (SPLIT_UNIT, _ascii(SPLIT_OP),
                                          SPLIT_OLD_FROM, SPLIT_OLD_TO,
                                          cursor.rowcount))
    report.append('  unit %d  %-24s %s..%s -> %s..%s'
                  % (SPLIT_UNIT, _ascii(SPLIT_OP), SPLIT_OLD_FROM,
                     SPLIT_OLD_TO, SPLIT_OLD_FROM, SPLIT_NEW_TO))
    rollback.append("UPDATE drone_operator_assignments SET date_to = '%s' "
                    "WHERE drone_unit_id = %d AND operator_id = %d "
                    "AND date_from = '%s' AND date_to = '%s';"
                    % (SPLIT_OLD_TO, unit_id, operator_id, SPLIT_OLD_FROM,
                       SPLIT_NEW_TO))

    new_ids = []
    for number, name, date_from, date_to, why in INSERTS:
        insert_unit, insert_operator = resolve(conn, number, name)
        cursor = conn.execute(
            'INSERT INTO drone_operator_assignments '
            '(operator_id, drone_unit_id, date_from, date_to, note) '
            'VALUES (?, ?, ?, ?, ?)',
            (insert_operator, insert_unit, date_from, date_to, why))
        new_ids.append(cursor.lastrowid)
        report.append('  unit %d  %-24s %s..%s  INSERTED (id %d)'
                      % (number, _ascii(name), date_from, date_to,
                         cursor.lastrowid))
    rollback.append('DELETE FROM drone_operator_assignments WHERE id IN (%s);'
                    % ', '.join(str(i) for i in new_ids))
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
    # скрипт проверяют на синтетической копии через --db.
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
    print('Postconditions: hectares per operator match the owner decisions of '
          '2026-08-19 within %.2f ha; month total %.2f ha; %.2f ha without an '
          'operator (unit 2 only); nothing covered by two operators at once.'
          % (TOLERANCE, EXPECTED_MONTH_HA, EXPECTED_UNASSIGNED_HA))
    print('  Kodirov     890.22 -> 896.24 ha  (book 884.50, 100.6% -> 101.3%)')
    print('  Kholmurodov 815.64 -> 864.12 ha  (book 879.50,  92.7% ->  98.3%)')
    print('  Kobilov     290.68 -> 295.33 ha  (book 285.90, 101.7% -> 103.3%)')
    print('  Anvarov     217.50 -> 169.02 ha  (book 172.80, 125.9% ->  97.8%)')
    print('')
    print('NOTE: unit 2 keeps 1.76 ha without an operator -- 0.06 ha on 27.09 '
          'at Qorakol and 1.69 ha on 29.09 at Yangi-Khayat (Kogon). No '
          'decision has been taken on those two yet.')
    print('')
    print('ROLLBACK OF DATA (run in this order):')
    for line in rollback:
        print('  ' + line)
    print("  DELETE FROM schema_migrations WHERE name = '%s';" % MIGRATION_ID)
    return 0


if __name__ == '__main__':
    sys.exit(main())
