# -*- coding: utf-8 -*-
"""Migration DRONES_WORKS_OCT2025_OPERATOR_LINK_001 -- книги октября обретают
хозяев.

ЧТО ПОЧИНЕНО. В октябре 2025 года 6 строк книг на 335.50 га не привязаны ни к
одному оператору. Из-за этого экран «Работы» показывает Холмуродову Шахзоду
НОЛЬ при 386.05 га телеметрии, а Анварову Усмону 7.00 вместо 27.50.

ПОЧЕМУ ТАК ВЫШЛО. Ровно та же причина, что и в сентябре
(DRONES_WORKS_SEPT2025_OPERATOR_LINK_001): импортёр берёт оператора из
колонки «Дрон бошқарувчи оператор», а в книге Ғиждувона там стоит сокращение
-- «Холмуродов.Ш», «Анваров.У», -- которого нет в справочнике. У строк блока
«Справка» этой колонки нет вовсе, и оператор берётся из имени листа: «Усмон»
разрешился в Анварова, «Шахзод» -- ни во что.

ЧЕМ ОПОЗНАН ХОЗЯИН. Не похожестью букв, а ПРОИСХОЖДЕНИЕМ СТРОКИ: лист и есть
книга одного человека. Каждая группа подтверждена подписанным ОБЩИМ СВОДОМ
(«Обший_сумма_Дрон_маълумот_ОКТЯБРЬ.xlsx», лист «ОБШИЙ СВОД  Зокир ака»),
строки «Ғиждувон ПТЗ»:

    лист-источник        строк      га   кому                свод
    свод ичи (Шахзод)        2  315.00   Холмуродов Шахзод   296 справка + 19 нақд
    свод ичи (Усмон)         4   20.50   Анваров Усмон       нақд 20.5 (справка 7 уже привязана)

ЛИСТ-ТЁЗКА. Имя «свод ичи (Шахзод)» есть НЕ ТОЛЬКО в книге Ғиждувона: в
апреле и мае 2026 такой же лист приходит из книги Сервиса, и это другая
книга. Поэтому условие правки включает ИМЯ ФАЙЛА, а не только имя листа, и
сверх того месяц и «оператора нет вовсе». Три условия вместо одного стоят
ничего, а в сентябре правка по одному имени листа чуть не увела 154.70 га
чужой книги.

ЧТО ЭТО ДАЁТ (книга -> телеметрия октября, %):

    Холмуродов Шахзод       0.00 ->  315.00   против 386.05    81.6 %
    Анваров Усмон           7.00 ->   27.50   против  31.66    86.9 %

[REASON]: обе доли НИЖЕ полосы доверия ±9 %, и это НЕ повод не привязывать.
Гектары книги ставит подписанный свод -- 315.00 и 27.50, -- а телеметрия
говорит, сколько налетала МАШИНА, назначенная оператору. Расхождение здесь
означает вопрос к НАЗНАЧЕНИЯМ МАШИН октября, а не к этим строкам: без
привязки те же гектары просто не показаны никому и расхождение всё равно
никуда не девается, только становится невидимым. Вопрос назначений оставлен
открытым в docs/DRONES_OCT2025_RECONCILIATION.md и в трековом файле.

Деньги не трогаются: сумма, расходы и приход остаются на своих строках, у
строки меняется только владелец. Итог месяца по гектарам и по деньгам не
двигается -- это проверяется постусловием.

ПОРЯДОК ОТНОСИТЕЛЬНО ПЕРЕИМПОРТА КНИГИ СЕРВИСА. Не важен. Переимпорт
возвращает в октябрь 15 строк на 118.40 га, у которых оператор определяется
именем листа и проставляется сразу; на эти шесть строк он не влияет, и ни
одно предусловие здесь не пришпилено к итогу месяца.

Безопасность / идемпотентность:
  - отказ с кодом 2 при отсутствии instance/transport.db, файл НЕ создаётся;
  - ПРЕДУСЛОВИЕ: в каждой из двух групп ровно то число строк и та площадь,
    что стоят выше, и ровно 6 строк / 335.50 га без оператора за месяц.
    Одна цифра иначе -- отказ с кодом 1 и полный откат;
  - ПОСТУСЛОВИЕ: дельты по двум операторам, ноль строк без оператора в обеих
    группах, неизменные итоги месяца по гектарам и по сумме -- проверяются
    ДО записи в реестр;
  - одна транзакция; повторный запуск печатает «already applied»;
  - stdlib sqlite3 без Flask; вывод в консоль только ASCII.

Запуск (production, служба ОСТАНОВЛЕНА):
  cd C:\\transport-report
  .\\nssm.exe stop TransportReport
  copy instance\\transport.db instance\\transport.db.backup_20260822a
  & "C:\\Program Files\\Python314\\python.exe" migrate_drones_works_oct2025_operator_link_001.py
  & "C:\\Program Files\\Python314\\python.exe" migrate_drones_works_oct2025_operator_link_001.py --apply
  .\\nssm.exe start TransportReport

Откат КОДА: git revert коммита.
Откат ДАННЫХ: печатается готовым к вставке блоком вместе с
  DELETE FROM schema_migrations WHERE name = 'DRONES_WORKS_OCT2025_OPERATOR_LINK_001';
"""

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import migration_utils  # noqa: E402

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       'instance', 'transport.db')

MIGRATION_ID = 'DRONES_WORKS_OCT2025_OPERATOR_LINK_001'
DESCRIPTION = ('October 2025: 6 book rows (335.50 ha) of the Gijduvon book '
               'linked to their operator by the sheet they came from, '
               'confirmed by the signed general summary.')

MONTH = '2025-10'

# (файл-источник, лист-источник, кому, строк, гектаров).
# [REASON]: файл в ключе -- защита от листа-тёзки, см. шапку. Пробел в конце
# «свод ичи (Усмон) » есть в самой книге, и без него строка не найдётся.
# Значения сняты с production 2026-08-20 (drone_money_audit_202510.xlsx,
# листы «Без оператора» и «Источники книг»).
SOURCE_FILE = 'Ғиждувон_ПТЗ_Дрон_маълумот_Октябрь.xlsx'
LINKS = (
    (SOURCE_FILE, 'свод ичи (Шахзод)', 'Холмуродов Шахзод', 2, 315.00),
    (SOURCE_FILE, 'свод ичи (Усмон) ', 'Анваров Усмон', 4, 20.50),
)

EXPECTED_ORPHAN_ROWS = 6
EXPECTED_ORPHAN_HA = 335.50
TOLERANCE_HA = 0.005
TOLERANCE_SUM = 1.0

MONTH_WHERE = ("COALESCE(strftime('%Y-%m', w.work_date_from), "
               "w.period_month) = ?")


def normalize(name):
    """Имена в справочнике и здесь могут быть набраны узбекскими буквами."""
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
        raise LookupError('operator %s resolves to %d rows in drone_operators'
                          % (_ascii(full_name), len(found)))
    return found[0]


def group_stats(conn, source_file, sheet):
    """Строк и гектаров в группе БЕЗ ОПЕРАТОРА."""
    row = conn.execute(
        'SELECT COUNT(*), COALESCE(SUM(w.area_ha), 0) FROM drone_works w '
        'WHERE w.source_file = ? AND w.source_sheet = ? '
        'AND w.drone_operator_id IS NULL AND ' + MONTH_WHERE,
        (source_file, sheet, MONTH)).fetchone()
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
    for source_file, sheet, name, want_rows, want_ha in LINKS:
        got_rows, got_ha = group_stats(conn, source_file, sheet)
        if got_rows != want_rows or abs(got_ha - want_ha) > TOLERANCE_HA:
            problems.append('  %-22s -> %-22s expected %d / %.2f ha, found '
                            '%d / %.2f' % (_ascii(sheet), _ascii(name),
                                           want_rows, want_ha, got_rows,
                                           got_ha))
    if problems:
        problems.append('  These figures were measured on production '
                        '2026-08-20. A difference means the books were '
                        're-imported or edited since; refusing to guess.')
    return problems


def _ids(conn, source_file, sheet):
    """Идентификаторы строк группы -- ДО правки."""
    return [row[0] for row in conn.execute(
        'SELECT w.id FROM drone_works w WHERE w.source_file = ? '
        'AND w.source_sheet = ? AND w.drone_operator_id IS NULL AND '
        + MONTH_WHERE + ' ORDER BY w.id', (source_file, sheet, MONTH))]


def apply_changes(conn):
    """Правит строки ПОИМЁННО, по id, и такой же откат печатает.

    [REASON]: откат «верни всех этого оператора в этом листе обратно в NULL»
    выглядит естественно и НЕВЕРЕН -- в сентябре он обнулил бы 587.50 га
    справок Холмуродова, лежащих в том же листе и привязанных задолго до
    миграции. В октябре в листе «свод ичи (Усмон) » ровно тот же случай:
    справочная строка на 7.00 га уже стоит за Анваровым. Откат по списку id
    трогает только то, что тронула миграция.
    """
    report = []
    rollback = []
    for source_file, sheet, name, want_rows, want_ha in LINKS:
        op_id = operator_id(conn, name)
        ids = _ids(conn, source_file, sheet)
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
    return report, rollback


def check_postcondition(conn, before_operator, before_month):
    problems = []
    for _source_file, _sheet, name, _rows, want_ha in LINKS:
        got = operator_hectares(conn, name) - before_operator[normalize(name)]
        if abs(got - want_ha) > TOLERANCE_HA:
            problems.append('  %-22s expected %+8.2f ha, got %+8.2f'
                            % (_ascii(name), want_ha, got))
    for source_file, sheet, _name, _rows, _ha in LINKS:
        rows, hectares = group_stats(conn, source_file, sheet)
        if rows or abs(hectares) > TOLERANCE_HA:
            problems.append('  %-22s still has %d row(s) / %.2f ha without '
                            'an operator' % (_ascii(sheet), rows, hectares))
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

        names = [name for _f, _s, name, _r, _h in LINKS]
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
            # [REASON]: считается ЗДЕСЬ, внутри открытой транзакции, а не
            # после commit/rollback. Соединение видит свои же незафиксиро-
            # ванные записи (read-your-own-writes), поэтому число верно и в
            # сухом прогоне, и в боевом. Смещение этой строки после rollback
            # заставляло сухой прогон откатывать привязку и тут же считать
            # эти же шесть строк «чужими из другой книги» -- ложная тревога
            # на каждом dry run, воспроизведена на чистой фикстуре без
            # единой посторонней сироты.
            left_rows, left_ha = orphan_stats(conn)
            leftovers = (left_rows, left_ha) if left_rows else None
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
    print('Postconditions: both groups are left with no unlinked row; month '
          'hectares and month amount are unchanged; both deltas match the '
          'signed general summary of October 2025.')
    print('')
    print('After this the books read (book -> telemetry):')
    print('  Kholmurodov Shahzod   315.00 ha vs 386.05   81.6%')
    print('  Anvarov Usmon          27.50 ha vs  31.66   86.9%')
    print('  Both are BELOW the +-9% band. That is a question about the '
          'MACHINE ASSIGNMENTS of October, not about these rows: the book '
          'figure is what the signed summary says.')
    if leftovers:
        print('')
        print('NOTE: %d other row(s) / %.2f ha of October still have no '
              'operator. Not touched by this migration -- they come from '
              'another book.' % leftovers)
    print('')
    print('ROLLBACK OF DATA (run in this order):')
    for line in rollback:
        print('  ' + line)
    print("  DELETE FROM schema_migrations WHERE name = '%s';" % MIGRATION_ID)
    return 0


if __name__ == '__main__':
    sys.exit(main())
