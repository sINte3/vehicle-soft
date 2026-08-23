# -*- coding: utf-8 -*-
"""Migration DRONES_WORKS_OCT2025_INTERNAL_PRICE_001 -- цена внутреннего
контура октября.

ЧТО ПОЧИНЕНО. Четыре работы октября-2025 на 476.50 га стоят без ставки, и
из-за этого тождество «сумма = гектары x ставка» их не проверяет вовсе: они
лежат в корзине «проверить нечем».

РЕШЕНИЕ ВЛАДЕЛЬЦА 2026-08-23, дословно:

  «Четвёртая отличается от первых трёх - считать по ставке 0 сум, это
   задание по указу руководства. По остальным трем - прими тариф как в
   сентябре для земель где Заказчик подведомственные предприятия
   Агрокластера.»

Отсюда две разные вещи, и они сделаны по-разному.

**Четвёртая строка -- ставка 0.** «Сервис ерлари (Топшириқ Ғаниев ташрифи
учун)», 3.10 га. В книге у неё в клетке цены прочерк, «Жами сумма» ноль и
пометка «Топшириқ … учун» -- работа по поручению. Ноль здесь взят не из
данных, а из решения владельца, и записан числом, а не пустотой: пустая
клетка означает «цена неизвестна», ноль означает «цены нет по решению». Это
разные факты, и отчёт различает их.

**Первые три -- ставка ЧИТАЕТСЯ ИЗ СЕНТЯБРЯ, а не вписывается сюда.**
Правило владельца называет источник, а не число, поэтому миграция идёт за
числом в базу: по каждой строке берутся сентябрьские работы ТОГО ЖЕ
ЗАКАЗЧИКА и их ставки.

[REASON]: тариф внутреннего контура НЕПОСТОЯНЕН -- в листах сентября
встречаются 75 040, 76 458, 85 633 и 86 000 сум/га, а разброс между крайними
вариантами на 473.40 га составляет почти 60 млн сум. Вписать сюда число,
выбранное по «похоже на правду», значило бы сделать придуманную цену
неотличимой от согласованной. Поэтому: **ровно одна ставка у заказчика в
сентябре -- берём её; ноль или несколько -- ОТКАЗ кодом 1 с перечислением
найденного.** Число, которого в данных нет, эта миграция не выдумывает.

Если сентябрь ответа не даёт, владелец называет ставку сам: `--rate 85633`.
Такая ставка помечается в выводе как OWNER-SUPPLIED и не притворяется
вычитанной.

СУММА пересчитывается как `гектары x ставка` -- иначе строка получит ставку
и провалит то самое тождество, ради которого всё и делается.

Безопасность / идемпотентность:
  - отказ с кодом 2 при отсутствии instance/transport.db, файл НЕ создаётся;
  - ПРЕДУСЛОВИЕ: все четыре строки на месте по (файл, лист, номер строки), с
    точной площадью, и у всех четырёх ставка ПУСТА. Одна цифра иначе --
    отказ с кодом 1;
  - ПОСТУСЛОВИЕ: у каждой строки ставка и сумма стоят и сходятся между
    собой; гектары месяца не изменились; сумма месяца выросла ровно на
    рассчитанное; ни одна ЧУЖАЯ строка не тронута -- проверяется ДО записи
    в реестр;
  - одна транзакция; повторный запуск печатает «already applied»;
  - stdlib sqlite3 без Flask; вывод в консоль только ASCII.

Запуск (production, служба ОСТАНОВЛЕНА):
  cd C:\\transport-report
  .\\nssm.exe stop TransportReport
  copy instance\\transport.db instance\\transport.db.backup_20260823b
  & "C:\\Program Files\\Python314\\python.exe" migrate_drones_works_oct2025_internal_price_001.py
  & "C:\\Program Files\\Python314\\python.exe" migrate_drones_works_oct2025_internal_price_001.py --apply
  .\\nssm.exe start TransportReport

Откат КОДА: git revert коммита.
Откат ДАННЫХ: печатается готовым к вставке блоком вместе с
  DELETE FROM schema_migrations WHERE name = 'DRONES_WORKS_OCT2025_INTERNAL_PRICE_001';
"""

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import migration_utils  # noqa: E402

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       'instance', 'transport.db')

MIGRATION_ID = 'DRONES_WORKS_OCT2025_INTERNAL_PRICE_001'
DESCRIPTION = ('October 2025: the four priceless internal-land jobs '
               '(476.50 ha) get a price -- three read from September for the '
               'same customer, one set to zero by the owner.')

MONTH = '2025-10'
SEPTEMBER = '2025-09'
MONTH_WHERE = ("COALESCE(strftime('%Y-%m', w.work_date_from), "
               "w.period_month) = ?")

# (файл, лист, номер строки, гектары, как получить ставку).
# 'september' -- прочитать у того же заказчика за сентябрь; иначе число.
GIJDUVON = 'Ғиждувон_ПТЗ_Дрон_маълумот_Октябрь.xlsx'
SHOFIRKON = 'Шофиркон ПТЗ Дрон Октябрь.xlsx'
SERVIS = 'Сервис Дрон Маълумот.xlsx'
ROWS = (
    (GIJDUVON, 'свод ичи (Шахзод)', 11, 296.00, 'september'),
    (SHOFIRKON, 'свод ичи (Туйғун)', 15, 174.00, 'september'),
    (SERVIS, 'свод ичи (Мухриддин)', 14, 3.40, 'september'),
    # [REASON]: НОЛЬ ПО РЕШЕНИЮ, а не отсутствие цены. «Топшириқ» -- работа
    # по поручению руководства, в книге прочерк и сумма ноль.
    (SERVIS, 'свод ичи (Фурқат)', 17, 3.10, 0.0),
)

TOLERANCE_HA = 0.005
TOLERANCE_SUM = 1.0


def _ascii(text):
    return str(text).encode('ascii', 'replace').decode('ascii')


def find_row(conn, source_file, sheet, source_row):
    return conn.execute(
        'SELECT w.id, w.area_ha, w.price_per_ha, w.amount, w.customer_raw, '
        '       w.drone_customer_id FROM drone_works w '
        'WHERE w.source_file = ? AND w.source_sheet = ? AND w.source_row = ? '
        'AND ' + MONTH_WHERE,
        (source_file, sheet, source_row, MONTH)).fetchall()


def september_rates(conn, customer_id, customer_raw):
    """Ставки того же заказчика за сентябрь. Список (ставка, строк, га).

    [REASON]: заказчик ищется по drone_customer_id, а когда его нет -- по
    ТОЧНОМУ написанию customer_raw. Похожесть букв тут не годится: «Бухоро
    Агрокластер Заминлари» и «Бухоро Агрокластер Заминлари МЧЖ» могут быть
    как одним хозяйством, так и двумя, и решать это подстрокой -- значит
    однажды взять чужую цену.
    """
    if customer_id is not None:
        where, params = 'w.drone_customer_id = ?', (customer_id, SEPTEMBER)
    else:
        where, params = 'w.customer_raw = ?', (customer_raw, SEPTEMBER)
    return conn.execute(
        'SELECT w.price_per_ha, COUNT(*), COALESCE(SUM(w.area_ha), 0) '
        'FROM drone_works w WHERE ' + where + ' AND ' + MONTH_WHERE +
        ' AND w.price_per_ha IS NOT NULL GROUP BY w.price_per_ha '
        'ORDER BY 2 DESC', params).fetchall()


def resolve_rates(conn, owner_rate=None):
    """[(строка книги, ставка, откуда)], список проблем."""
    plan, problems = [], []
    for source_file, sheet, source_row, area, how in ROWS:
        found = find_row(conn, source_file, sheet, source_row)
        if len(found) != 1:
            problems.append('  %s | %s | row %d: expected 1 row in %s, found '
                            '%d' % (_ascii(source_file)[-28:], _ascii(sheet),
                                    source_row, MONTH, len(found)))
            continue
        work_id, got_area, price, _amount, customer_raw, customer_id = found[0]
        if abs(float(got_area or 0) - area) > TOLERANCE_HA:
            problems.append('  %s | row %d: expected %.2f ha, found %.2f'
                            % (_ascii(sheet), source_row, area,
                               float(got_area or 0)))
            continue
        if price is not None:
            problems.append('  %s | row %d: already has a price (%.2f) -- '
                            'refusing to overwrite'
                            % (_ascii(sheet), source_row, float(price)))
            continue
        if how != 'september':
            plan.append((work_id, sheet, source_row, area, float(how),
                         'owner decision'))
            continue
        rates = september_rates(conn, customer_id, customer_raw)
        if len(rates) == 1:
            plan.append((work_id, sheet, source_row, area, float(rates[0][0]),
                         'September, same customer, %d row(s) / %.2f ha'
                         % (rates[0][1], float(rates[0][2]))))
        elif owner_rate is not None:
            plan.append((work_id, sheet, source_row, area, float(owner_rate),
                         'OWNER-SUPPLIED --rate (September gave %d distinct '
                         'rate(s))' % len(rates)))
        else:
            problems.append(
                '  %s | row %d (%s): September gives %d distinct rate(s) for '
                'the same customer -- %s'
                % (_ascii(sheet), source_row, _ascii(customer_raw)[:34],
                   len(rates),
                   ', '.join('%.2f (%d rows)' % (float(r[0]), r[1])
                             for r in rates) or 'none at all'))
    if problems and not any('already has a price' in p for p in problems):
        problems.append('  The rule is the owner\'s ("same as September for '
                        'holding lands"), the NUMBER must come from the data. '
                        'Supply it explicitly with --rate if September cannot '
                        'answer.')
    return plan, problems


def month_totals(conn):
    row = conn.execute(
        'SELECT COALESCE(SUM(w.area_ha), 0), COALESCE(SUM(w.amount), 0), '
        'COUNT(*) FROM drone_works w WHERE ' + MONTH_WHERE,
        (MONTH,)).fetchone()
    return float(row[0] or 0), float(row[1] or 0), int(row[2])


def apply_changes(conn, plan):
    report, rollback = [], []
    for work_id, sheet, source_row, area, rate, why in plan:
        amount = round(area * rate, 2)
        conn.execute('UPDATE drone_works SET price_per_ha = ?, amount = ? '
                     'WHERE id = ?', (rate, amount, work_id))
        report.append('  %-22s row %2d  %8.2f ha x %10.2f = %14.2f   %s'
                      % (_ascii(sheet), source_row, area, rate, amount,
                         _ascii(why)))
        # [REASON]: откат возвращает ИМЕННО пустоту, а не ноль: до миграции
        # цены не было, и «0» на её месте означало бы совсем другое.
        rollback.append('UPDATE drone_works SET price_per_ha = NULL, '
                        'amount = NULL WHERE id = %d;' % work_id)
    return report, rollback


def check_postcondition(conn, plan, before):
    problems = []
    expected_amount = 0.0
    for work_id, sheet, source_row, area, rate, _why in plan:
        row = conn.execute('SELECT area_ha, price_per_ha, amount '
                           'FROM drone_works WHERE id = ?',
                           (work_id,)).fetchone()
        if row is None:
            problems.append('  row %d vanished' % work_id)
            continue
        got_area, got_rate, got_amount = (float(row[0] or 0),
                                          row[1], float(row[2] or 0))
        if got_rate is None:
            problems.append('  %s row %d: price still empty'
                            % (_ascii(sheet), source_row))
            continue
        if abs(float(got_rate) - rate) > 0.005:
            problems.append('  %s row %d: price %.2f, expected %.2f'
                            % (_ascii(sheet), source_row, float(got_rate),
                               rate))
        # [REASON]: тождество «сумма = гектары x ставка» проверяется ЗДЕСЬ, а
        # не оставляется отчёту. Строка со ставкой и несогласованной суммой
        # выходит из корзины «проверить нечем» прямо в «расходится» -- то
        # есть миграция своими руками создала бы расхождение.
        if abs(got_amount - round(got_area * float(got_rate), 2)) > 0.01:
            problems.append('  %s row %d: amount %.2f != %.2f x %.2f'
                            % (_ascii(sheet), source_row, got_amount,
                               got_area, float(got_rate)))
        expected_amount += round(area * rate, 2)
    hectares, amount, rows = month_totals(conn)
    if abs(hectares - before[0]) > TOLERANCE_HA:
        problems.append('  month hectares moved: %.2f -> %.2f'
                        % (before[0], hectares))
    if rows != before[2]:
        problems.append('  month row count moved: %d -> %d'
                        % (before[2], rows))
    if abs((amount - before[1]) - expected_amount) > TOLERANCE_SUM:
        problems.append('  month amount grew by %.2f, expected %.2f'
                        % (amount - before[1], expected_amount))
    return problems


def main():
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument('--db', default=DB_PATH,
                        help='override only for testing on a synthetic copy')
    parser.add_argument('--apply', action='store_true',
                        help='write the changes; without it only a dry run')
    parser.add_argument('--rate', type=float, default=None,
                        help="owner's rate, used ONLY where September cannot "
                             'answer; reported as OWNER-SUPPLIED')
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

        plan, problems = resolve_rates(conn, args.rate)
        if problems:
            print('PRECONDITION FAILED -- nothing changed.')
            for line in problems:
                print(line)
            return 1

        before = month_totals(conn)
        conn.execute('BEGIN')
        try:
            report, rollback = apply_changes(conn, plan)
            problems = check_postcondition(conn, plan, before)
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

    total = sum(round(area * rate, 2) for _i, _s, _r, area, rate, _w in plan)
    print('Changes:')
    for line in report:
        print(line)
    print('  %-22s %36s %14.2f' % ('TOTAL', '', total))
    print('Postconditions: every priced row satisfies amount = ha x rate; '
          'month hectares and row count are unchanged; the month amount grew '
          'by exactly the figure above.')
    print('')
    print('ROLLBACK OF DATA (run in this order):')
    for line in rollback:
        print('  ' + line)
    print("  DELETE FROM schema_migrations WHERE name = '%s';" % MIGRATION_ID)
    return 0


if __name__ == '__main__':
    sys.exit(main())
