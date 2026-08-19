# -*- coding: utf-8 -*-
"""Migration DRONES_WORKS_SEPT2025_REDATE_001 -- четыре строки книги Холмуродова.

ЧТО ПОЧИНЕНО. В книге «Ғиждувон, свод ичи Холмуродов Шахзод» четыре строки
на 587.50 га проставлены ОКТЯБРЬСКИМИ датами, хотя работа сентябрьская.
Владелец подтвердил ошибку диспетчера 2026-08-17. Программа группирует работу
по её собственной дате (_drone_work_month_expr в drones.py), поэтому эти
строки падают в октябрь, и сентябрьский отчёт по работам недосчитывает 587.50
га. Книги мы не правим по решению владельца от 2026-08-18 -- значит, даты
правятся здесь.

Телеметрия подтверждает: машина №3 (Холмуродов) за 19-30 СЕНТЯБРЯ налетала
488.90 га, и ни одна другая строка книги эти дни не покрывает; за 19-30
октября -- только 153.60 га. Работа сентябрьская.

ТРИ ИЗ ЧЕТЫРЁХ СТРОК -- НЕ ЕГО. Три строки на 76.00 га записаны на
Холмуродова, но в эти дни его машина №3 стояла в Қоровулбозоре, за 200 км от
названных полей, а на них летала машина №4 -- Анваров Усмон:

    строка книги            га     день   №3 (Холмуродов)      №4 (Анваров)
    Розия Бекова          25.0   20.09   Қоровулбозор 49.00   Темирчи 23.47
    Анвар Ота замини      27.5   23.09   Қоровулбозор 37.59   Вобкент 26.04
    Умарбобо Усмонов      23.5   27.09   Қоровулбозор 80.75   Вобкент 22.44

Машина физически не могла быть в двух местах. Поля Розии Бековой опознаны
владельцем по карте DJI как Темирчи -- там летала №4. Справочник контуров DJI
подтверждает и площади: Розия Бекова 25.06 га, Умарбобо 23.17 га.

Проверка переносом: в книге Анварова 20, 23 и 27 сентября были ПУСТЫМИ днями,
а телеметрия №4 в эти дни даёт 34.31, 26.03 и 22.44 га. После возврата строк
совпадение 98 %, 106 % и 105 %.

Четвёртая строка -- «Бухоро Агрокластер Заминлари 511.5 га, 19-30» -- это
Қоровулбозор, где и стояла №3: 488.90 га телеметрии против 511.50 га книги,
95.6 %. Она остаётся за Холмуродовым, у неё меняется только дата.

ЧТО ЭТА МИГРАЦИЯ НЕ ДЕЛАЕТ:
  - не трогает `date_raw`: это исходная клетка книги, вещественное
    доказательство разбора. Меняются только разобранные work_date_from/to;
  - не трогает `customer_raw` и назначения операторов на машины
    (drone_operator_assignments). Раскладка машин выведена заново, независимым
    методом, и совпала с той, что уже стоит в базе -- менять там нечего;
  - не создаёт и не удаляет ни одной строки работ. Незаписанные 20.60 га
    Қобилова (05-06.09) и неподтверждённая справка Анварова на 43.80 га
    (16-18.09) остаются как есть: у первых неизвестен заказчик, вторая --
    решение владельца, а выдумывать учётные записи нельзя.

Безопасность / идемпотентность:
  - отказ с кодом 2 при отсутствии instance/transport.db;
  - предусловие пришпиливает все четыре строки поимённо -- заказчик, гектары,
    дата и оператор; при любом расхождении откат и код 1;
  - постусловие проверяется ДО записи в реестр и считает ДЕЛЬТЫ, а не
    абсолютные суммы: база живёт, и абсолютное число устареет;
  - одна транзакция; повторный запуск печатает «Already applied»;
  - stdlib sqlite3 без Flask (`from app import app` вызывает db.create_all()
    на импорте и превращает читателя в писателя);
  - вывод в консоль только ASCII (кодировка консоли Windows).

Запуск (службу сначала ОСТАНОВИТЬ):

  cd C:\\transport-report
  .\\nssm.exe stop TransportReport
  copy instance\\transport.db instance\\transport.db.backup_20260819
  & "C:\\Program Files\\Python314\\python.exe" migrate_drones_works_sept2025_redate_001.py --dry-run
  & "C:\\Program Files\\Python314\\python.exe" migrate_drones_works_sept2025_redate_001.py --apply
  .\\nssm.exe start TransportReport

Откат:
  Откат кода и откат данных -- разные вещи. Данные откатываются печатаемым
  SQL: миграция выводит его при --apply готовым к вставке, вместе с
  DELETE FROM schema_migrations WHERE name = 'DRONES_WORKS_SEPT2025_REDATE_001'.
"""

import argparse
import os
import sqlite3
import sys

import migration_utils
from migration_utils import (
    ensure_schema_migrations_table,
    is_migration_applied,
    record_migration,
    migration_checksum,
)

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, 'instance', 'transport.db')
MIGRATION_ID = 'DRONES_WORKS_SEPT2025_REDATE_001'
DESCRIPTION = ('DRONE-SEPT2025-REDATE-001: четыре строки книги Холмуродова '
               'переведены с октябрьских дат на сентябрьские (587.50 га); три '
               'из них (76.00 га) переданы Анварову -- машина №3 в эти дни '
               'была в Коровулбозоре, поля летала машина №4. date_raw и '
               'customer_raw не тронуты, назначения машин не тронуты.')

OP_FROM = 'Холмуродов Шахзод'
OP_TO = 'Анваров Усмон'

# (заказчик, га, было_с, было_по, стало_с, стало_по, новый оператор или None)
#
# [REASON]: строка опознаётся по ТРЁМ признакам сразу -- написание заказчика,
# гектары и дата. Одного мало: «Розия Бекова» есть в книге дважды (34.5 га на
# 05.09 и 25.0 га здесь), и правка по имени задела бы обе.
ROWS = (
    ('Розия Бекова', 25.0, '2025-10-20', '2025-10-20',
     '2025-09-20', '2025-09-20', OP_TO,
     'поле Темирчи, опознано владельцем по карте DJI; 20.09 №3 в '
     'Коровулбозоре 49.00 га, №4 в Темирчи 23.47 га; контуры DJI 25.06 га'),
    ('Анвар Ота замини', 27.5, '2025-10-23', '2025-10-23',
     '2025-09-23', '2025-09-23', OP_TO,
     '23.09 №3 в Коровулбозоре 37.59 га, №4 в Вобкенте 26.04 га'),
    ('Умарбобо Усмонов', 23.5, '2025-10-27', '2025-10-27',
     '2025-09-27', '2025-09-27', OP_TO,
     '27.09 №3 в Коровулбозоре 80.75 га, №4 в Вобкенте 22.44 га; '
     'контуры DJI 23.17 га'),
    ('Бухоро Агрокластер Заминлари', 511.5, '2025-10-19', '2025-10-30',
     '2025-09-19', '2025-09-30', None,
     'Коровулбозор -- там и стояла №3: 488.90 га телеметрии за 19-30.09 '
     'против 511.50 га книги, 95.6 %'),
)

# Дельты, которые обязана дать миграция. Считаются на самой базе ДО и ПОСЛЕ,
# поэтому не устаревают от того, что в drone_works прибавилось строк.
MOVED_HA = 76.0        # Холмуродов -> Анваров
REDATED_HA = 587.5     # всего переехало из октября в сентябрь
TOLERANCE = 0.01


def month_hectares(cur, month):
    """Гектары месяца ТЕМ ЖЕ правилом, что и отчёт: по дате работы,
    а при её отсутствии -- по колонке периода."""
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
        raise RuntimeError('operator not found: %s' % full_name.encode(
            'ascii', 'backslashreplace').decode('ascii'))
    return row[0]


def find_row(cur, customer, area, date_from, date_to, op_id):
    """Единственная строка книги по четырём признакам. Иначе -- ошибка."""
    cur.execute(
        'SELECT id, work_date_from, work_date_to, drone_operator_id '
        'FROM drone_works '
        'WHERE customer_raw = ? AND ABS(area_ha - ?) < 0.005 '
        'AND work_date_from = ? AND work_date_to = ? '
        'AND drone_operator_id = ?',
        (customer, area, date_from, date_to, op_id))
    found = cur.fetchall()
    if len(found) != 1:
        raise RuntimeError(
            'precondition failed: expected exactly 1 row for %.1f ha on %s, '
            'found %d' % (area, date_from, len(found)))
    return found[0][0]


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

        id_from = operator_id(cur, OP_FROM)
        id_to = operator_id(cur, OP_TO)

        before_sept = month_hectares(cur, '2025-09')
        before_oct = month_hectares(cur, '2025-10')
        before_from = operator_hectares(cur, '2025-09', OP_FROM)
        before_to = operator_hectares(cur, '2025-09', OP_TO)

        targets = []
        for (customer, area, old_from, old_to, new_from, new_to, new_op,
             why) in ROWS:
            work_id = find_row(cur, customer, area, old_from, old_to, id_from)
            targets.append((work_id, area, new_from, new_to, new_op, why))
            print('found id=%d  %.1f ha  %s..%s -> %s..%s  %s'
                  % (work_id, area, old_from, old_to, new_from, new_to,
                     'operator -> Anvarov' if new_op else 'operator kept'))

        if not apply_changes:
            con.rollback()
            print('')
            print('DRY RUN: all 4 rows found and matched, nothing written.')
            print('September now %.2f ha, October now %.2f ha'
                  % (before_sept, before_oct))
            print('Run again with --apply to write.')
            return

        for work_id, _area, new_from, new_to, new_op, _why in targets:
            if new_op:
                cur.execute(
                    'UPDATE drone_works SET work_date_from = ?, '
                    'work_date_to = ?, drone_operator_id = ? WHERE id = ?',
                    (new_from, new_to, id_to, work_id))
            else:
                cur.execute(
                    'UPDATE drone_works SET work_date_from = ?, '
                    'work_date_to = ? WHERE id = ?',
                    (new_from, new_to, work_id))

        # Постусловия -- ДО записи в реестр, и по дельтам, а не по абсолютам.
        after_sept = month_hectares(cur, '2025-09')
        after_oct = month_hectares(cur, '2025-10')
        after_from = operator_hectares(cur, '2025-09', OP_FROM)
        after_to = operator_hectares(cur, '2025-09', OP_TO)

        checks = (
            ('September gained the redated hectares',
             after_sept - before_sept, REDATED_HA),
            ('October lost them', before_oct - after_oct, REDATED_HA),
            ('Kholmurodov gained redated minus moved',
             after_from - before_from, REDATED_HA - MOVED_HA),
            ('Anvarov gained the moved rows', after_to - before_to, MOVED_HA),
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
        print('ERROR: migration failed and was rolled back: %s'
              % str(exc).encode('ascii', 'backslashreplace').decode('ascii'))
        sys.exit(1)
    finally:
        con.close()

    record_migration(MIGRATION_ID, description=DESCRIPTION,
                     checksum=migration_checksum(__file__))
    print('')
    print('Done. 4 row(s) redated, %.1f ha of them reassigned to Anvarov.'
          % MOVED_HA)
    print('')
    print('ROLLBACK (paste as one block, service stopped):')
    for (customer, area, old_from, old_to, new_from, new_to, new_op,
         _why) in ROWS:
        print("  UPDATE drone_works SET work_date_from = '%s', "
              "work_date_to = '%s'%s WHERE customer_raw = '%s' "
              "AND ABS(area_ha - %.1f) < 0.005 AND work_date_from = '%s';"
              % (old_from, old_to,
                 (", drone_operator_id = (SELECT id FROM drone_operators "
                  "WHERE full_name = '%s')" % OP_FROM) if new_op else '',
                 customer.replace("'", "''"), area, new_from))
    print("  DELETE FROM schema_migrations WHERE name = '%s';" % MIGRATION_ID)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Redate four September rows filed with October dates.')
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
