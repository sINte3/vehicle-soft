# -*- coding: utf-8 -*-
"""Migration DRONES_WORKS_SEPT2025_REDATE_002 -- даты, и ТОЛЬКО даты.

ЗАМЕНЯЕТ DRONES_WORKS_SEPT2025_REDATE_001, который делал лишнее.

ЧТО ПОЧИНЕНО. В книге «Ғиждувон, свод ичи (Шахзод)» четыре строки на
587.50 га проставлены ОКТЯБРЬСКИМИ датами, хотя работа сентябрьская.
Программа группирует работу по её собственной дате
(_drone_work_month_expr в drones.py), поэтому строки падают в октябрь, и
сентябрьский отчёт недосчитывает 587.50 га. Книги по решению владельца не
правятся -- значит, даты правятся здесь.

ЧТО ИСПРАВЛЕНО ПРОТИВ _001. Та миграция, кроме дат, передавала три строки
(76.00 га) Анварову: телеметрия показывала, что в эти дни машина №3 стояла в
Қоровулбозоре, а названные поля летала машина №4. Вывод был мой, а не
владельца, и владелец его отменил 2026-08-19:

    «Гектары писали на операторов. У Холмуродова по книге 879,5.»

Оператор в книге -- это тот, за кем числится работа и через кого прошли
деньги («Кирим қилинган» в своде), а не обязательно тот, кто держал пульт.
Поэтому строки остаются за Холмуродовым, и правятся только даты.

ЧЕМ ДОКАЗАНО, ЧТО РАБОТА СЕНТЯБРЬСКАЯ. Имена контуров, которые оператор
набирает на пульте DJI, датированы днём создания -- и они ложатся ровно на
сентябрьские дни этих строк:

    строка книги               га    дата в книге   контуры DJI
    Анвар Ота замини          27.5   23.10.2025     anvar ota 1..4, 1.1, 1.2
                                                    созданы 23.09.2025, 30.92 га
    Умарбобо Усмонов          23.5   27.10.2025     umarbobo usmonov 1..7
                                                    созданы 27.09.2025, 23.17 га
    Розия Бекова              25.0   20.10.2025     roziya bekova 1..5, 1.1
                                                    созданы 21.09.2025, 20.44 га
                                                    + roziya5 07.09, 4.62 га
    Бухоро Агрокластер Зам.  511.5   19-30.10.2025  Қоровулбозор, где и стояла
                                                    №3: 488.90 га за 19-30.09
                                                    против 511.50 книги, 95.6 %

Ни одного контура с этими именами в ОКТЯБРЕ 2025 года не создано.

ЧТО МИГРАЦИЯ УМЕЕТ. Она приводит четыре строки к правильному виду из ЛЮБОГО
из двух состояний, потому что _001 могла быть уже применена:

  состояние A -- _001 НЕ применялась: даты октябрьские, оператор Холмуродов.
                 Правятся даты. Сентябрь +587.50, октябрь -587.50.
  состояние B -- _001 применена: даты уже сентябрьские, три строки у
                 Анварова. Возвращается оператор. Холмуродов +76.00,
                 Анваров -76.00, итоги месяцев не меняются.

Любое третье состояние -- отказ с кодом 1 и полным откатом.

Безопасность / идемпотентность:
  - отказ с кодом 2 при отсутствии instance/transport.db, файл НЕ создаётся;
  - строка опознаётся по заказчику И гектарам, а затем её текущий вид обязан
    в точности совпасть с одним из двух состояний выше;
    [REASON]: «Розия Бекова» есть в книге дважды -- 34.5 га наличными за
    05-06.09 и 25.0 га справкой; правка по одному имени задела бы обе;
  - `date_raw` и `customer_raw` НЕ трогаются -- это исходные клетки книги;
  - назначения машин не трогаются вовсе;
  - постусловия считаются ДЕЛЬТАМИ и проверяются ДО записи в реестр;
  - одна транзакция; повторный запуск печатает «Already applied»;
  - stdlib sqlite3 без Flask; вывод в консоль только ASCII.

Запуск (production, служба ОСТАНОВЛЕНА):
  cd C:\\transport-report
  .\\nssm.exe stop TransportReport
  copy instance\\transport.db instance\\transport.db.backup_20260819
  & "C:\\Program Files\\Python314\\python.exe" migrate_drones_works_sept2025_redate_002.py
  & "C:\\Program Files\\Python314\\python.exe" migrate_drones_works_sept2025_redate_002.py --apply
  .\\nssm.exe start TransportReport

Откат КОДА: git revert коммита.
Откат ДАННЫХ: печатается при --apply готовым к вставке блоком, вместе с
  DELETE FROM schema_migrations WHERE name = 'DRONES_WORKS_SEPT2025_REDATE_002';
"""

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import migration_utils  # noqa: E402
from migration_utils import (  # noqa: E402
    ensure_schema_migrations_table,
    is_migration_applied,
    record_migration,
    migration_checksum,
)

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, 'instance', 'transport.db')
MIGRATION_ID = 'DRONES_WORKS_SEPT2025_REDATE_002'
SUPERSEDED_ID = 'DRONES_WORKS_SEPT2025_REDATE_001'
DESCRIPTION = ('DRONE-SEPT2025-REDATE-002: четыре строки книги Холмуродова '
               'переведены с октябрьских дат на сентябрьские (587.50 га). '
               'Оператор НЕ меняется -- решение владельца 2026-08-19: '
               'гектары писались на операторов, у Холмуродова 879.50 га. '
               'Заменяет REDATE_001, который передавал 76.00 га Анварову.')

OWNER = 'Холмуродов Шахзод'
WRONG_OWNER = 'Анваров Усмон'

# (заказчик, га, октябрьские даты, сентябрьские даты, менял ли _001 оператора)
ROWS = (
    ('Розия Бекова', 25.0, '2025-10-20', '2025-10-20',
     '2025-09-20', '2025-09-20', True,
     'контуры roziya bekova 1..5, 1.1 созданы 21.09.2025, 20.44 га; '
     'roziya5 07.09, 4.62 га -- вместе 25.06 га против 25.0 книги'),
    ('Анвар Ота замини', 27.5, '2025-10-23', '2025-10-23',
     '2025-09-23', '2025-09-23', True,
     'контуры anvar ota 1..4, 1.1, 1.2 созданы 23.09.2025, 30.92 га; '
     'машина №4 в этот день 26.03 га в Вобкенте'),
    ('Умарбобо Усмонов', 23.5, '2025-10-27', '2025-10-27',
     '2025-09-27', '2025-09-27', True,
     'контуры umarbobo usmonov 1..7 созданы 27.09.2025, 23.17 га против '
     '23.5 книги -- 98.6 %; машина №4 в этот день 22.44 га'),
    ('Бухоро Агрокластер Заминлари', 511.5, '2025-10-19', '2025-10-30',
     '2025-09-19', '2025-09-30', False,
     'Қоровулбозор -- там и стояла №3: 488.90 га телеметрии за 19-30.09 '
     'против 511.50 га книги, 95.6 %'),
)

REDATED_HA = 587.5     # переезжает из октября в сентябрь (состояние A)
RESTORED_HA = 76.0     # возвращается Холмуродову (состояние B)
TOLERANCE = 0.01

STATE_OCTOBER = 'A'    # _001 не применялась
STATE_MOVED = 'B'      # _001 применена


def _ascii(text):
    return str(text).encode('ascii', 'backslashreplace').decode('ascii')


def month_hectares(cur, month):
    """Гектары месяца ТЕМ ЖЕ правилом, что и отчёт: по дате работы, а при её
    отсутствии -- по колонке периода."""
    cur.execute(
        "SELECT COALESCE(SUM(area_ha), 0) FROM drone_works "
        "WHERE COALESCE(strftime('%Y-%m', work_date_from), period_month) = ?",
        (month,))
    return float(cur.fetchone()[0] or 0)


def operator_hectares(cur, month, full_name):
    cur.execute(
        "SELECT COALESCE(SUM(w.area_ha), 0) FROM drone_works w "
        "JOIN drone_operators o ON o.id = w.drone_operator_id "
        "WHERE o.full_name = ? "
        "AND COALESCE(strftime('%Y-%m', w.work_date_from), w.period_month) = ?",
        (full_name, month))
    return float(cur.fetchone()[0] or 0)


def operator_id(cur, full_name):
    cur.execute('SELECT id FROM drone_operators WHERE full_name = ?',
                (full_name,))
    row = cur.fetchone()
    if row is None:
        raise RuntimeError('operator not found: %s' % _ascii(full_name))
    return row[0]


def find_row(cur, customer, area):
    """Единственная строка книги по заказчику и гектарам."""
    cur.execute(
        'SELECT id, work_date_from, work_date_to, drone_operator_id '
        'FROM drone_works '
        'WHERE customer_raw = ? AND ABS(area_ha - ?) < 0.005',
        (customer, area))
    found = cur.fetchall()
    if len(found) != 1:
        raise RuntimeError(
            'precondition failed: expected exactly 1 row for "%s" %.1f ha, '
            'found %d' % (_ascii(customer), area, len(found)))
    return found[0]


def detect_state(cur, id_owner, id_wrong):
    """Одно из двух известных состояний -- или отказ.

    [REASON]: миграция обязана быть безопасной и до, и после _001, потому что
    владелец мог успеть её применить. Гадать по одной строке нельзя: если
    строки окажутся в разных состояниях, значит база правилась вручную, и
    тогда единственный правильный ответ -- не трогать её вовсе.
    """
    seen = set()
    targets = []
    for (customer, area, old_from, old_to, new_from, new_to, moved,
         why) in ROWS:
        work_id, date_from, date_to, op_id = find_row(cur, customer, area)
        if (date_from, date_to) == (old_from, old_to) and op_id == id_owner:
            state = STATE_OCTOBER
        elif ((date_from, date_to) == (new_from, new_to)
              and op_id == (id_wrong if moved else id_owner)):
            state = STATE_MOVED
        else:
            raise RuntimeError(
                'precondition failed: "%s" %.1f ha is in neither known state '
                '-- it now reads %s..%s, operator id %s. Somebody edited it '
                'by hand; refusing to overwrite that.'
                % (_ascii(customer), area, date_from, date_to, op_id))
        seen.add(state)
        targets.append((work_id, customer, area, new_from, new_to, moved, why))
    if len(seen) != 1:
        raise RuntimeError(
            'precondition failed: the four rows are in MIXED states %s -- '
            'that cannot happen from a clean run of %s. Refusing.'
            % (sorted(seen), SUPERSEDED_ID))
    return seen.pop(), targets


def run(apply_changes):
    if not os.path.exists(DB_PATH):
        # [REASON]: sqlite3.connect(path) СОЗДАЛ бы пустую базу.
        print('ERROR: database not found at %s - refusing to run.' % DB_PATH)
        sys.exit(2)

    ensure_schema_migrations_table()
    if is_migration_applied(MIGRATION_ID):
        print('Already applied. Nothing to do.')
        return

    con = sqlite3.connect(DB_PATH)
    try:
        cur = con.cursor()
        cur.execute('BEGIN')

        id_owner = operator_id(cur, OWNER)
        id_wrong = operator_id(cur, WRONG_OWNER)

        before_sept = month_hectares(cur, '2025-09')
        before_oct = month_hectares(cur, '2025-10')
        before_owner = operator_hectares(cur, '2025-09', OWNER)
        before_wrong = operator_hectares(cur, '2025-09', WRONG_OWNER)

        state, targets = detect_state(cur, id_owner, id_wrong)
        print('state %s: %s' % (state, {
            STATE_OCTOBER: '%s was NOT applied, dates are still October'
                           % SUPERSEDED_ID,
            STATE_MOVED: '%s was applied, 3 rows sit on Anvarov'
                         % SUPERSEDED_ID}[state]))
        for work_id, customer, area, new_from, new_to, moved, _why in targets:
            print('  found id=%-5d %-32s %6.1f ha -> %s..%s%s'
                  % (work_id, _ascii(customer), area, new_from, new_to,
                     '  operator restored' if (moved and state == STATE_MOVED)
                     else ''))

        if not apply_changes:
            con.rollback()
            print('')
            print('DRY RUN: all 4 rows found and matched, nothing written.')
            print('September now %.2f ha, October now %.2f ha.'
                  % (before_sept, before_oct))
            print('Kholmurodov September now %.2f ha, Anvarov %.2f ha.'
                  % (before_owner, before_wrong))
            print('Run again with --apply to write.')
            return

        for work_id, _customer, _area, new_from, new_to, _moved, _why in \
                targets:
            cur.execute(
                'UPDATE drone_works SET work_date_from = ?, work_date_to = ?, '
                'drone_operator_id = ? WHERE id = ?',
                (new_from, new_to, id_owner, work_id))

        after_sept = month_hectares(cur, '2025-09')
        after_oct = month_hectares(cur, '2025-10')
        after_owner = operator_hectares(cur, '2025-09', OWNER)
        after_wrong = operator_hectares(cur, '2025-09', WRONG_OWNER)

        if state == STATE_OCTOBER:
            checks = (
                ('September gained the redated hectares',
                 after_sept - before_sept, REDATED_HA),
                ('October lost them', before_oct - after_oct, REDATED_HA),
                ('Kholmurodov gained all of them',
                 after_owner - before_owner, REDATED_HA),
                ('Anvarov is untouched', after_wrong - before_wrong, 0.0),
            )
        else:
            checks = (
                ('September total is unchanged',
                 after_sept - before_sept, 0.0),
                ('October total is unchanged', after_oct - before_oct, 0.0),
                ('Kholmurodov got his rows back',
                 after_owner - before_owner, RESTORED_HA),
                ('Anvarov gave them up',
                 before_wrong - after_wrong, RESTORED_HA),
            )
        for label, got, expected in checks:
            if abs(got - expected) > TOLERANCE:
                raise RuntimeError(
                    'postcondition failed: %s -- expected %+.2f, got %+.2f'
                    % (label, expected, got))
            print('OK: %-42s %+8.2f ha' % (label, got))

        con.commit()
    except Exception as exc:
        con.rollback()
        print('ERROR: migration failed and was rolled back: %s' % _ascii(exc))
        sys.exit(1)
    finally:
        con.close()

    record_migration(MIGRATION_ID, description=DESCRIPTION,
                     checksum=migration_checksum(os.path.abspath(__file__)))
    print('')
    print('Done. 4 row(s) now carry September dates and belong to '
          'Kholmurodov.')
    print('')
    print('ROLLBACK OF DATA (paste as one block, service stopped):')
    for (customer, area, old_from, old_to, new_from, new_to, moved,
         _why) in ROWS:
        if state == STATE_OCTOBER:
            back_from, back_to, back_op = old_from, old_to, OWNER
        else:
            back_from, back_to = new_from, new_to
            back_op = WRONG_OWNER if moved else OWNER
        print("  UPDATE drone_works SET work_date_from = '%s', "
              "work_date_to = '%s', drone_operator_id = (SELECT id FROM "
              "drone_operators WHERE full_name = '%s') WHERE customer_raw = "
              "'%s' AND ABS(area_ha - %.1f) < 0.005;"
              % (back_from, back_to, back_op, customer.replace("'", "''"),
                 area))
    print("  DELETE FROM schema_migrations WHERE name = '%s';" % MIGRATION_ID)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Redate four September rows filed with October dates; '
                    'the operator stays Kholmurodov.')
    parser.add_argument('--apply', action='store_true',
                        help='write the changes; without it nothing is saved')
    parser.add_argument('--dry-run', action='store_true',
                        help='check the preconditions only (the default)')
    parser.add_argument('--db', help='database path (tests only)')
    args = parser.parse_args(argv)

    if args.db:
        # [REASON]: migration_utils открывает базу по СВОЕЙ константе модуля,
        # а не по переданному соединению. Без этой строки тестовый прогон
        # записал бы реестр в живую базу.
        global DB_PATH
        DB_PATH = args.db
        migration_utils.DB_PATH = args.db

    run(apply_changes=args.apply)
    return 0


if __name__ == '__main__':
    sys.exit(main())
