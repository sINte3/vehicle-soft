# -*- coding: utf-8 -*-
"""Migration DRONES_ASSIGN_SEPT2025_M5_SPLIT_001 -- машина 5, 05-06 сентября.

ЧТО ПОЧИНЕНО. После DRONES_ASSIGN_SEPT2025_FIX_001 машина 5 числится за
Кобиловым Фаррухом с 05.09. На самом деле 05 и 06 сентября на ней работал
Холмуродов Шахзод, а Кобилов сел на неё с 07.09.

ЧЕМ ДОКАЗАНО. Четыре независимых источника, и ни один из них не телеметрия
одна.

  1. КНИГА КОБИЛОВА ПУСТА 05 и 06 сентября -- ни одной строки. Первая его
     запись за месяц -- 07.09, «Очил Барот, 5.10 га». Человек, за которым
     числится машина, в эти два дня не работал вовсе.
  2. КНИГА ХОЛМУРОДОВА на эти дни описывает ровно те поля, над которыми
     машина 5 и летала: «Розия Бекова фх, 34.5 га, 05-06.09» и «Зухро Агро
     фх, 15.0 га, 06-07.09».
  3. КАРТА DJI, которую владелец открыл 2026-08-19 по машине «5 Shofirko» за
     05-06.09: два участка, 15.1 га и 5.5 га. Телеметрия даёт 15.11 и 5.47.
  4. СПРАВОЧНИК КОНТУРОВ DJI (снимок DRONE-LANDS-001): одиннадцать контуров с
     именем roziya дают 34.39 га против 34.5 га книги -- 99.7 %.

Телеметрия сходится с этим до сотых. В эти два дня машины 3 и 5 работали БОК
О БОК, в одни и те же часы, на одних и тех же полях -- то есть Холмуродов вёл
две машины сразу, а не передавал одну:

    05.09  Темирчи (G'ijduvon-Shofirkon)  N3 3.68  N5 6.83
    05.09  Gijduvan                       N3 2.15  N5 1.28
    06.09  Bukhara Region 500200          N3 8.50  N5 5.71
    06.09  Gijduvan                       N3 4.91  N5 1.29
    06.09  Gishty (Зухро Агро)            N3 4.34  N5 5.47

Разбивка по заказчикам совпадает с книгой Холмуродова:

    Розия Бекова = всё, кроме Gishty  = 34.36 га   книга 34.5   99.6 %
    из них на машине 5                = 15.11 га   карта DJI: 15.1
    Зухро Агро   = Gishty на машине 5 =  5.47 га   карта DJI: 5.5

ЧТО ЭТО МЕНЯЕТ В ЧИСЛАХ (книга Холмуродова -- 803.50 га после
DRONES_WORKS_SEPT2025_REDATE_001, книга Кобилова -- 285.90 га):

    Холмуродов  795.06 -> 815.64 га    98.9 % -> 101.5 %
    Кобилов     311.27 -> 290.68 га   108.9 % -> 101.7 %

Сумма отклонений пары от книги падает с 10.0 до 3.2 процентного пункта.
После миграции 12 операторов из 13 лежат между 93 % и 102 %; тринадцатый --
Анваров, 87.4 % -- зависит от решения владельца по справке «Ғиждувон ПТЗ
(Сарвари-Зарангари), 43.80 га, 16-18.09»: машина 4 шестнадцатого не летала
вовсе, а 17-18.09 её 31.91 га почти целиком разобраны наличными строками
самого Анварова.

ПОЧЕМУ ГРАНИЦА ИМЕННО 07.09, А НЕ 08.09. Проверены все границы с 05 по 11
сентября. По сумме отклонений пары от книг лучшие две -- 07.09 (3.2 п.п.) и
08.09 (6.0 п.п.); остальные хуже вдвое и больше. Выбрана 07.09, потому что
именно её подтверждает прямое свидетельство -- карта DJI за 05-06.09 и
пустая книга Кобилова за те же два дня, -- а 08.09 подтверждается только
подгонкой чисел. Седьмое сентября остаётся днём смешанным: на машине 5 в
этот день есть и 0.88 га в Gishty (поле Зухро Агро из книги Холмуродова), и
первая строка Кобилова. Модель не умеет делить машину внутри дня, и день
целиком отдан Кобилову -- расхождение названо, а не спрятано.

ОДИН ОПЕРАТОР НА ДВУХ МАШИНАХ -- это нормально, и модель это допускает.
Запрещено обратное: одна машина у двух операторов в один день. Постусловие
требует, чтобы корзина «спорное» осталась пустой.

Безопасность / идемпотентность:
  - отказ с кодом 2 при отсутствии instance/transport.db, файл НЕ создаётся;
  - ПРЕДУСЛОВИЕ: все сентябрьские назначения обязаны быть ровно теми, какими
    их оставила DRONES_ASSIGN_SEPT2025_FIX_001 -- машина, оператор, обе даты.
    Одна строка иначе -- значит назначения кто-то правил после, и миграция
    отказывается с кодом 1 и полным откатом, а не затирает чужую работу;
  - ПОСТУСЛОВИЕ: гектары сентября по операторам, выведенные через назначения
    ровно так, как их выводит приложение, обязаны совпасть со сверкой с
    точностью 0.05 га, сумма месяца -- 5572.18 га, корзина «спорное» -- пуста;
  - одна транзакция; постусловия проверяются ДО записи в реестр;
  - повторный запуск печатает «already applied» и ничего не делает;
  - stdlib sqlite3 без Flask; вывод в консоль только ASCII.

Запуск (production, из каталога установки; службу сначала ОСТАНОВИТЬ):
  cd C:\\transport-report
  .\\nssm.exe stop TransportReport
  copy instance\\transport.db instance\\transport.db.backup_20260819
  & "C:\\Program Files\\Python314\\python.exe" migrate_drones_assign_sept2025_m5_split_001.py
  & "C:\\Program Files\\Python314\\python.exe" migrate_drones_assign_sept2025_m5_split_001.py --apply
  .\\nssm.exe start TransportReport

Откат КОДА: git revert коммита.
Откат ДАННЫХ: и сухой прогон, и боевой печатают готовый к вставке SQL --
DELETE вставленной строки и UPDATE даты назад на 2025-09-05, а затем
  DELETE FROM schema_migrations WHERE name = 'DRONES_ASSIGN_SEPT2025_M5_SPLIT_001';
"""

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import migration_utils  # noqa: E402

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       'instance', 'transport.db')

MIGRATION_ID = 'DRONES_ASSIGN_SEPT2025_M5_SPLIT_001'
DESCRIPTION = ('September 2025: unit 5 split -- Kholmurodov 05-06.09, '
               'Kobilov from 07.09')

MONTH_FROM = '2025-09-01'
MONTH_TO = '2025-09-30'

# [REASON]: тот же сдвиг, что и в _drone_flight_month_expr() в drones.py. Эти
# дроны работают по ночам: вылет, сохранённый в 20:30 UTC, для людей, которые
# его выполняли, относится к следующему дню, и группировка по UTC перебросила
# бы целые дни между операторами прямо на границе раздела.
UTC_OFFSET_MINUTES = 300

# Ровно то, что оставила DRONES_ASSIGN_SEPT2025_FIX_001. Предусловие сверяется
# с этим списком: машина, оператор, обе даты.
BASELINE = (
    (1, 'Рухиллоев Сайфулло', '2025-09-02', '2025-09-30'),
    (3, 'Холмуродов Шахзод', '2025-09-04', '2025-09-30'),
    (4, 'Анваров Усмон', '2025-09-17', '2025-09-29'),
    (5, 'Кобилов Фаррух', '2025-09-05', '2025-09-29'),
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

UNIT = 5
OP_KEEP = 'Кобилов Фаррух'
OP_ADD = 'Холмуродов Шахзод'

# Строке Кобилова сдвигается начало.
OLD_FROM = '2025-09-05'
OLD_TO = '2025-09-29'
NEW_FROM = '2025-09-07'

# Новая строка Холмуродова на те же два дня.
ADD_FROM = '2025-09-05'
ADD_TO = '2025-09-06'
ADD_NOTE = ('сверка 2026-08-19: карта DJI машины "5 Shofirko" за 05-06.09 -- '
            'Розия Бекова 15.1 га и Зухро Агро 5.5 га; обе работы записаны в '
            'книге Холмуродова (34.5 и 15.0 га), в книге Кобилова на эти дни '
            'строк нет, его первая запись месяца -- 07.09')

# Постусловие: гектары сентября по оператору, выведенные через назначения.
# Числа пересчитаны из телеметрии сентября 2026-08-19; от FIX_001 отличаются
# только две строки -- Холмуродов и Кобилов.
EXPECTED_HA = (
    ('Рухиллоев Сайфулло', 1009.73),
    ('Кодиров Нурали', 890.22),
    ('Холмуродов Шахзод', 815.64),
    ('Ибодуллаев Хасанбой', 418.44),
    ('Хамроев Шохрух', 415.55),
    ('Имомов Бехзод', 407.70),
    ('Жумаев Фуркат', 388.40),
    ('Кобилов Фаррух', 290.68),
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
    """Сентябрьские назначения обязаны быть ровно те, что оставила FIX_001."""
    got = [(int(number), normalize(name), date_from, date_to)
           for _id, number, name, date_from, date_to in september_rows(conn)]
    want = [(number, normalize(name), date_from, date_to)
            for number, name, date_from, date_to in BASELINE]
    if got == want:
        return []
    problems = ['  September assignments differ from what '
                'DRONES_ASSIGN_SEPT2025_FIX_001 left behind:']
    for row in want:
        if row not in got:
            problems.append('    missing: unit %-2d %-24s %s..%s'
                            % (row[0], _ascii(row[1]), row[2], row[3]))
    for row in got:
        if row not in want:
            problems.append('    extra:   unit %-2d %-24s %s..%s'
                            % (row[0], _ascii(row[1]), row[2], row[3]))
    problems.append('  Apply DRONES_ASSIGN_SEPT2025_FIX_001 first, or find '
                    'out who edited assignments after it.')
    return problems


def hectares_by_operator(conn):
    """Гектары сентября по оператору -- тем же правилом, что и приложение.

    [REASON]: вылет, покрытый БОЛЕЕ ЧЕМ ОДНИМ назначением, не отдаётся молча
    одному из них -- он попадает в корзину 'ambiguous', и постусловие требует,
    чтобы она была пуста. Здесь это особенно важно: миграция добавляет второго
    оператора на ту же машину, и ошибка в один день на границе дала бы
    перекрытие, которое иначе прошло бы незамеченным.
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
        problems.append('  %.2f ha covered by two operators at once -- the '
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
    """Номер машины и имя оператора -> их id. Обе стороны обязаны быть одни."""
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
    unit_id, keep_id = resolve(conn, UNIT, OP_KEEP)
    _unit_id, add_id = resolve(conn, UNIT, OP_ADD)

    # [REASON]: строки Холмуродова на этой машине быть не должно -- иначе
    # после вставки 05-06.09 окажутся покрыты ДВАЖДЫ, и «спорное» перестанет
    # быть пустым. Ловится и постусловием, но отказать до записи честнее.
    existing = conn.execute(
        'SELECT COUNT(*) FROM drone_operator_assignments '
        'WHERE drone_unit_id = ? AND operator_id = ?',
        (unit_id, add_id)).fetchone()[0]
    if existing:
        raise LookupError('%s already has %d assignment(s) on unit %d'
                          % (_ascii(OP_ADD), existing, UNIT))

    cursor = conn.execute(
        'UPDATE drone_operator_assignments SET date_from = ? '
        'WHERE drone_unit_id = ? AND operator_id = ? '
        'AND date_from = ? AND date_to = ?',
        (NEW_FROM, unit_id, keep_id, OLD_FROM, OLD_TO))
    if cursor.rowcount != 1:
        raise LookupError('unit %d %s %s..%s: expected 1 row to shift, '
                          'touched %d' % (UNIT, _ascii(OP_KEEP), OLD_FROM,
                                          OLD_TO, cursor.rowcount))

    cursor = conn.execute(
        'INSERT INTO drone_operator_assignments '
        '(operator_id, drone_unit_id, date_from, date_to, note) '
        'VALUES (?, ?, ?, ?, ?)',
        (add_id, unit_id, ADD_FROM, ADD_TO, ADD_NOTE))
    new_id = cursor.lastrowid

    report = [
        '  unit %d  %-24s %s..%s -> %s..%s'
        % (UNIT, _ascii(OP_KEEP), OLD_FROM, OLD_TO, NEW_FROM, OLD_TO),
        '  unit %d  %-24s %s..%s  INSERTED (id %d)'
        % (UNIT, _ascii(OP_ADD), ADD_FROM, ADD_TO, new_id),
    ]
    rollback = [
        'DELETE FROM drone_operator_assignments WHERE id = %d;' % new_id,
        "UPDATE drone_operator_assignments SET date_from = '%s' "
        "WHERE drone_unit_id = %d AND operator_id = %d AND date_from = '%s' "
        "AND date_to = '%s';"
        % (OLD_FROM, unit_id, keep_id, NEW_FROM, OLD_TO),
    ]
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
    print('Postconditions: hectares per operator match the reconciliation of '
          '2026-08-19 within %.2f ha; month total %.2f ha; nothing covered '
          'by two operators at once.' % (TOLERANCE, EXPECTED_MONTH_HA))
    print('  Kholmurodov  795.06 -> 815.64 ha  (book 803.50, 98.9%% -> 101.5%%)')
    print('  Kobilov      311.27 -> 290.68 ha  (book 285.90, 108.9%% -> 101.7%%)')
    print('')
    print('NOTE: 07.09 is a mixed day. Unit 5 flew 0.88 ha at Gishty that day '
          '-- the Zuhro Agro field from Kholmurodov book -- and it is the '
          'first day of Kobilov own book. The model cannot split a machine '
          'inside a day, so the whole day goes to Kobilov.')
    print('')
    print('ROLLBACK OF DATA (run in this order):')
    for line in rollback:
        print('  ' + line)
    print("  DELETE FROM schema_migrations WHERE name = '%s';" % MIGRATION_ID)
    return 0


if __name__ == '__main__':
    sys.exit(main())
