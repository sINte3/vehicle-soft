# -*- coding: utf-8 -*-
"""Migration DRONES_WORKS_SEPT2025_OPERATOR_LINK_001 -- книги обретают хозяев.

ЧТО ПОЧИНЕНО. В сентябре 2025 года 103 строки книг на 1 130.60 га не
привязаны ни к одному оператору, и ещё 13 строк на 132.70 га привязаны не к
тому. Из-за первого экран «Работы» показывает Холмуродову 587.50 га вместо
879.50 -- не хватает ровно его наличных 292.00.

ПОЧЕМУ ТАК ВЫШЛО. Импортёр берёт оператора из колонки «Дрон бошқарувчи
оператор», а в книгах там стоит сокращение -- «Холмуродов.Ш», «Анваров.У»,
«Файзуллоев.Ш», -- либо колонки нет вовсе, и тогда имя надо брать из шапки
листа, где оно тоже написано не как в справочнике («ДРОН Ибодуллаев Хасан»
против «Ибодуллаев Хасанбой», а у Шоди шапка пустая -- просто «ДРОН »).

ЧЕМ ОПОЗНАН ХОЗЯИН. Не похожестью букв, а ПРОИСХОЖДЕНИЕМ СТРОКИ: лист и есть
книга одного человека. Каждая группа сверх того подтверждена площадью из
подписанного ОБЩЕГО СВОДА своего подразделения:

    лист-источник         строк      га   кому              свод
    свод ичи (Хасан)         39  427.20   Ибодуллаев        121.7 + 302.5
    свод ичи (Шахзод)        22  292.00   Холмуродов        наличка 292.0
    свод ичи (Шоди)          17  174.50   Файзуллаев Шоди   --
    свод ичи (Шохрух)        14  154.70   Файзуллаев Шохрух  24.1 + 130.6
    свод ичи (Усмон)         11   82.20   Анваров           наличка 88.0 - 5.8

[REASON]: имя листа «свод ичи (Шохрух)» есть В ДВУХ книгах -- у Гардена это
Файзуллаев Шохрух, у Когона Хамроев Шохрух. Поэтому правятся ТОЛЬКО строки,
у которых оператора нет вовсе: строки Хамроева уже привязаны и под условие
не попадают. Плюс предусловие требует точного числа строк и точной площади в
каждой группе -- если в базе окажется что-то другое, миграция откажется.

ВТОРАЯ ПРАВКА: 132.70 га ЖУРАЕВА ЗАПИСАНЫ НА КОДИРОВА. В листе
«свод ичи (Туйғун)» книги Шофиркона колонка оператора на 13 строках говорит
«Кодиров Нурали». Два независимых источника говорят обратное:

  * подписанный ОБЩИЙ СВОД той же книги: «Жураев Туйғун, Сентябрь, справка
    67, накд 132.7» -- вместе 199.70;
  * телеметрия: у Жураева 193.94 га, это 97.1 % от 199.70. С чужими 132.70 у
    Кодирова выходит 1017.20 против 896.24 телеметрии (88.1 %), а у Жураева
    67.00 против 193.94 (289 %) -- обе цифры далеко вне полосы.

ЧТО ЭТО ДАЁТ (книга -> телеметрия, %):

    Ибодуллаев Хасанбой     0.00 ->  427.20   против 418.44   97.9 %
    Холмуродов Шахзод     587.50 ->  879.50   против 864.12   98.3 %
    Файзуллаев Шоди         0.00 ->  174.50   против 168.80   96.7 %
    Файзуллаев Шохрух       0.00 ->  154.70   против 143.97   93.1 %
    Анваров Усмон          90.60 ->  172.80   против 169.02   97.8 %
    Кодиров Нурали       1017.20 ->  884.50   против 896.24  101.3 %
    Жураев Туйгун          67.00 ->  199.70   против 193.94   97.1 %

Деньги не трогаются: сумма, расходы и приход остаются на своих строках, у
строки меняется только владелец. Итог месяца по гектарам и по деньгам не
двигается -- это проверяется постусловием.

Безопасность / идемпотентность:
  - отказ с кодом 2 при отсутствии instance/transport.db, файл НЕ создаётся;
  - ПРЕДУСЛОВИЕ: в каждой из шести групп ровно то число строк и та площадь,
    что стоят ниже. Одна цифра иначе -- отказ с кодом 1 и полный откат;
  - ПОСТУСЛОВИЕ: дельты по операторам, ноль строк без оператора в сентябре,
    неизменные итоги месяца по гектарам и по сумме -- проверяются ДО записи
    в реестр;
  - одна транзакция; повторный запуск печатает «Already applied»;
  - stdlib sqlite3 без Flask; вывод в консоль только ASCII.

Запуск (production, служба ОСТАНОВЛЕНА):
  cd C:\\transport-report
  .\\nssm.exe stop TransportReport
  copy instance\\transport.db instance\\transport.db.backup_20260819b
  & "C:\\Program Files\\Python314\\python.exe" migrate_drones_works_sept2025_operator_link_001.py
  & "C:\\Program Files\\Python314\\python.exe" migrate_drones_works_sept2025_operator_link_001.py --apply
  .\\nssm.exe start TransportReport

Откат КОДА: git revert коммита.
Откат ДАННЫХ: печатается готовым к вставке блоком вместе с
  DELETE FROM schema_migrations WHERE name = 'DRONES_WORKS_SEPT2025_OPERATOR_LINK_001';
"""

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import migration_utils  # noqa: E402

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       'instance', 'transport.db')

MIGRATION_ID = 'DRONES_WORKS_SEPT2025_OPERATOR_LINK_001'
DESCRIPTION = ('September 2025: 103 book rows (1130.60 ha) linked to their '
               'operator by the sheet they came from, and 13 rows (132.70 ha) '
               'returned from Kodirov to Zhuraev.')

MONTH = '2025-09'

# (лист-источник, кому, строк, гектаров). Лист -- книга одного человека.
# [REASON]: пробел в конце «свод ичи (Усмон) » -- он есть в самой книге, и
# без него строка не найдётся. Значения сняты с production 2026-08-19.
LINKS = (
    ('свод ичи (Хасан)', 'Ибодуллаев Хасанбой', 39, 427.20),
    ('свод ичи (Шахзод)', 'Холмуродов Шахзод', 22, 292.00),
    ('свод ичи (Шоди)', 'Файзуллаев Шоди', 17, 174.50),
    ('свод ичи (Шохрух)', 'Файзуллаев Шохрух', 14, 154.70),
    ('свод ичи (Усмон) ', 'Анваров Усмон', 11, 82.20),
)

# Лист, у кого забрать, кому отдать, строк, гектаров.
REASSIGN_SHEET = 'свод ичи (Туйғун)'
REASSIGN_FROM = 'Кодиров Нурали'
REASSIGN_TO = 'Жураев Туйгун'
REASSIGN_ROWS = 13
REASSIGN_HA = 132.70

EXPECTED_ORPHAN_ROWS = 103
EXPECTED_ORPHAN_HA = 1130.60
TOLERANCE_HA = 0.005
TOLERANCE_SUM = 1.0


def normalize(name):
    """Имена в справочнике и здесь могут быть набраны узбекскими буквами."""
    table = {ord('қ'): 'к', ord('ғ'): 'г', ord('ў'): 'у', ord('ҳ'): 'х',
             ord('ҷ'): 'ж', ord('ъ'): '', ord('ь'): '', ord('ё'): 'е'}
    return ' '.join(str(name).lower().translate(table).split())


def _ascii(text):
    return str(text).encode('ascii', 'replace').decode('ascii')


MONTH_WHERE = ("COALESCE(strftime('%Y-%m', w.work_date_from), "
               "w.period_month) = ?")


def operator_id(conn, full_name):
    found = [op_id for op_id, name in
             conn.execute('SELECT id, full_name FROM drone_operators')
             if normalize(name) == normalize(full_name)]
    if len(found) != 1:
        raise LookupError('operator %s resolves to %d rows in drone_operators'
                          % (_ascii(full_name), len(found)))
    return found[0]


def group_stats(conn, sheet, operator_id_or_none):
    """Строк и гектаров в группе. None -- строки вообще без оператора."""
    if operator_id_or_none is None:
        clause = 'w.drone_operator_id IS NULL'
        params = (sheet, MONTH)
    else:
        clause = 'w.drone_operator_id = ?'
        params = (sheet, operator_id_or_none, MONTH)
    row = conn.execute(
        'SELECT COUNT(*), COALESCE(SUM(w.area_ha), 0) FROM drone_works w '
        'WHERE w.source_sheet = ? AND ' + clause + ' AND ' + MONTH_WHERE,
        params).fetchone()
    return int(row[0]), float(row[1] or 0)


def orphan_stats(conn):
    row = conn.execute(
        'SELECT COUNT(*), COALESCE(SUM(w.area_ha), 0) FROM drone_works w '
        'WHERE w.drone_operator_id IS NULL AND ' + MONTH_WHERE,
        (MONTH,)).fetchone()
    return int(row[0]), float(row[1] or 0)


def month_totals(conn):
    row = conn.execute(
        'SELECT COALESCE(SUM(w.area_ha), 0), COALESCE(SUM(w.amount), 0) '
        'FROM drone_works w WHERE ' + MONTH_WHERE, (MONTH,)).fetchone()
    return float(row[0] or 0), float(row[1] or 0)


def operator_hectares(conn, full_name):
    op_id = operator_id(conn, full_name)
    row = conn.execute(
        'SELECT COALESCE(SUM(w.area_ha), 0) FROM drone_works w '
        'WHERE w.drone_operator_id = ? AND ' + MONTH_WHERE,
        (op_id, MONTH)).fetchone()
    return float(row[0] or 0)


def check_precondition(conn):
    """Каждая группа обязана быть ровно той, что снята с production."""
    problems = []
    rows, hectares = orphan_stats(conn)
    if rows != EXPECTED_ORPHAN_ROWS or abs(hectares - EXPECTED_ORPHAN_HA) > \
            TOLERANCE_HA:
        problems.append('  rows with no operator: expected %d / %.2f ha, '
                        'found %d / %.2f'
                        % (EXPECTED_ORPHAN_ROWS, EXPECTED_ORPHAN_HA, rows,
                           hectares))
    for sheet, name, want_rows, want_ha in LINKS:
        got_rows, got_ha = group_stats(conn, sheet, None)
        if got_rows != want_rows or abs(got_ha - want_ha) > TOLERANCE_HA:
            problems.append('  %-22s -> %-22s expected %d / %.2f ha, found '
                            '%d / %.2f' % (_ascii(sheet), _ascii(name),
                                           want_rows, want_ha, got_rows,
                                           got_ha))
    got_rows, got_ha = group_stats(conn, REASSIGN_SHEET,
                                   operator_id(conn, REASSIGN_FROM))
    if got_rows != REASSIGN_ROWS or abs(got_ha - REASSIGN_HA) > TOLERANCE_HA:
        problems.append('  %-22s on %-19s expected %d / %.2f ha, found '
                        '%d / %.2f' % (_ascii(REASSIGN_SHEET),
                                       _ascii(REASSIGN_FROM), REASSIGN_ROWS,
                                       REASSIGN_HA, got_rows, got_ha))
    if problems:
        problems.append('  These figures were measured on production '
                        '2026-08-19. A difference means the books were '
                        're-imported or edited since; refusing to guess.')
    return problems


def _ids(conn, sheet, operator_id_or_none):
    """Идентификаторы строк группы -- ДО правки."""
    if operator_id_or_none is None:
        clause, params = 'w.drone_operator_id IS NULL', (sheet, MONTH)
    else:
        clause = 'w.drone_operator_id = ?'
        params = (sheet, operator_id_or_none, MONTH)
    return [row[0] for row in conn.execute(
        'SELECT w.id FROM drone_works w WHERE w.source_sheet = ? AND '
        + clause + ' AND ' + MONTH_WHERE + ' ORDER BY w.id', params)]


def apply_changes(conn):
    """Правит строки ПОИМЁННО, по id, и такой же откат печатает.

    [REASON]: откат «верни всех этого оператора в этом листе обратно в NULL»
    выглядит естественно и НЕВЕРЕН. В листе «свод ичи (Шахзод)» у
    Холмуродова уже лежат четыре справочные строки на 587.50 га, привязанные
    задолго до этой миграции, -- такой откат обнулил бы и их. То же со второй
    правкой: у Жураева в листе «свод ичи (Туйғун)» есть своя строка на 67.00
    га, и откат по оператору увёл бы её Кодирову. Поймано исполнением
    напечатанного отката в тесте, а не чтением его глазами.
    """
    report = []
    rollback = []
    for sheet, name, want_rows, want_ha in LINKS:
        op_id = operator_id(conn, name)
        ids = _ids(conn, sheet, None)
        if len(ids) != want_rows:
            raise LookupError('%s: expected %d rows to link, found %d'
                              % (_ascii(sheet), want_rows, len(ids)))
        placeholders = ', '.join('?' * len(ids))
        conn.execute('UPDATE drone_works SET drone_operator_id = ? '
                     'WHERE id IN (%s)' % placeholders, [op_id] + ids)
        report.append('  %-22s -> %-22s %3d rows, %8.2f ha'
                      % (_ascii(sheet), _ascii(name), want_rows, want_ha))
        rollback.append('UPDATE drone_works SET drone_operator_id = NULL '
                        'WHERE id IN (%s);'
                        % ', '.join(str(i) for i in ids))

    from_id = operator_id(conn, REASSIGN_FROM)
    to_id = operator_id(conn, REASSIGN_TO)
    ids = _ids(conn, REASSIGN_SHEET, from_id)
    if len(ids) != REASSIGN_ROWS:
        raise LookupError('%s: expected %d rows to move, found %d'
                          % (_ascii(REASSIGN_SHEET), REASSIGN_ROWS, len(ids)))
    conn.execute('UPDATE drone_works SET drone_operator_id = ? '
                 'WHERE id IN (%s)' % ', '.join('?' * len(ids)),
                 [to_id] + ids)
    report.append('  %-22s    %-22s %3d rows, %8.2f ha  (was %s)'
                  % (_ascii(REASSIGN_SHEET), _ascii(REASSIGN_TO),
                     REASSIGN_ROWS, REASSIGN_HA, _ascii(REASSIGN_FROM)))
    rollback.append('UPDATE drone_works SET drone_operator_id = %d '
                    'WHERE id IN (%s);'
                    % (from_id, ', '.join(str(i) for i in ids)))
    return report, rollback


def check_postcondition(conn, before_operator, before_month):
    problems = []
    expected = [(name, want_ha) for _sheet, name, _rows, want_ha in LINKS]
    expected.append((REASSIGN_TO, REASSIGN_HA))
    expected.append((REASSIGN_FROM, -REASSIGN_HA))
    for name, delta in expected:
        got = operator_hectares(conn, name) - before_operator[normalize(name)]
        if abs(got - delta) > TOLERANCE_HA:
            problems.append('  %-22s expected %+8.2f ha, got %+8.2f'
                            % (_ascii(name), delta, got))
    rows, hectares = orphan_stats(conn)
    if rows or abs(hectares) > TOLERANCE_HA:
        problems.append('  %d row(s) / %.2f ha still have no operator'
                        % (rows, hectares))
    total_ha, total_amount = month_totals(conn)
    if abs(total_ha - before_month[0]) > TOLERANCE_HA:
        problems.append('  month hectares moved: %.2f -> %.2f'
                        % (before_month[0], total_ha))
    if abs(total_amount - before_month[1]) > TOLERANCE_SUM:
        problems.append('  month amount moved: %.2f -> %.2f'
                        % (before_month[1], total_amount))
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

    # [REASON]: migration_utils открывает СВОЁ соединение по своей константе.
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

        names = [name for _s, name, _r, _h in LINKS] + [REASSIGN_TO,
                                                        REASSIGN_FROM]
        before_operator = {normalize(n): operator_hectares(conn, n)
                           for n in names}
        before_month = month_totals(conn)

        conn.execute('BEGIN')
        try:
            report, rollback = apply_changes(conn)
            problems = check_postcondition(conn, before_operator, before_month)
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
    print('Postconditions: no row of September is left without an operator; '
          'month hectares and month amount are unchanged; every delta matches '
          'the reconciliation of 2026-08-19.')
    print('')
    print('After this the books read (book -> telemetry):')
    print('  Ibodullaev      427.20 ha vs 418.44   97.9%')
    print('  Kholmurodov     879.50 ha vs 864.12   98.3%')
    print('  Fayzullaev Shodi 174.50 ha vs 168.80  96.7%')
    print('  Fayzullaev Shohruh 154.70 vs 143.97   93.1%')
    print('  Anvarov         172.80 ha vs 169.02   97.8%')
    print('  Kodirov         884.50 ha vs 896.24  101.3%')
    print('  Zhuraev         199.70 ha vs 193.94   97.1%')
    print('')
    print('ROLLBACK OF DATA (run in this order):')
    for line in rollback:
        print('  ' + line)
    print("  DELETE FROM schema_migrations WHERE name = '%s';" % MIGRATION_ID)
    return 0


if __name__ == '__main__':
    sys.exit(main())
