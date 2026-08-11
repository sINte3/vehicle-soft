#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backfill_drone_march_amount_fix.py -- DRONE-MARCH-AMOUNT-FIX-001: одна
мартовская строка книги Когона, где сумма счёта записана в колонку затрат.

ЧТО СЛУЧИЛОСЬ. Сверка архивов 2026-08-11 нашла единственную книгу,
изменённую по существу после загрузки: «Когон ПТЗ Шохрух Хамроев МАРТ.xlsx»,
лист «свод ичи (Шохрух)», блок «Справка (Март ойи)». В строке 8 (Зарафшон
Нурафшон фх, 60 га) диспетчер поднял цену 85 633 -> 200 000 и вписал
12 000 000 (= 60 га x 200 000) в колонку «Бошка харажатлар»; запись 800 000
в строке 6 при этом снял (итог G9 сменился 800 000 -> 12 000 000). Владелец,
дословно: «Мартовские 12 000 000 в колонке затрат - ошибка диспетчера, надо
в „Жами сумма"».

ЧТО ДЕЛАЕТ СКРИПТ. Три исправления, каждое с ожидаемым текущим значением:

  строка 8: amount        NULL      -> 12 000 000   (сумма счёта, слова владельца)
  строка 8: price_per_ha  85 633    -> 200 000      (исправленная книга)
  строка 6: other_costs   800 000   -> NULL         (снято диспетчером в
                                                     исправленной книге)

Правка применяется ТОЛЬКО если текущее значение в базе совпало с ожидаемым
(допуск 0.005 — пыль плавающей точки, не порог): чужую правку молча не
затирают, несовпадение называется и пропускается.

Строки лежат в блоке «Справка», поэтому 12 000 000 уходят в канал
перечисления с пустым «получено» — и на экране долгов встанут в строку
«ждём банковский реестр», а не в дебиторку.

КНИГА ОСТАЁТСЯ ИСТОЧНИКОМ ИСТИНЫ. В исправленной книге 12 000 000
по-прежнему стоят в «Бошка харажатлар» (G8): диспетчер перенёс число между
строками, но не между колонками. Пока владелец не перенесёт его в «Жами
сумма» (E8), построчная сверка verify_drone_works_against_books будет
показывать это расхождение — скрипт печатает нужную правку книги отдельным
разделом.

--dry-run — РЕЖИМ ПО УМОЛЧАНИЮ: база открывается read-only через file:-URI,
не пишется ничего. --apply пишет одной транзакцией. Повторный --apply
обязан напечатать «0 rows to change».

Запуск (сервер, из каталога установки):
  & "C:\\Program Files\\Python314\\python.exe" backfill_drone_march_amount_fix.py --db instance\\transport.db
  & "C:\\Program Files\\Python314\\python.exe" backfill_drone_march_amount_fix.py --db instance\\transport.db --apply

ОТКАТ ДАННЫХ печатается в отчёт готовыми операторами с прежними значениями.
Откат кода — git revert; схема не меняется.

Консоль — только ASCII; полный читаемый вывод — в отчёте UTF-8.
"""

import argparse
import os
import sys

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(REPO_ROOT, 'tools'))

import import_drone_works as importer  # noqa: E402  [REASON]: _connect с
# отказом при отсутствии базы и mode=ro на сухом прогоне, _require_tables и
# _ascii — те же, что у остальных бэкфиллов трека.

BOOK = 'Когон ПТЗ Шохрух Хамроев МАРТ.xlsx'
SHEET = 'свод ичи (Шохрух)'

# Ожидаемые значения сняты из разбора книги тем же парсером, которым шёл
# импорт (parsed 2026-08-11), а не из памяти.
FIXES = (
    {'source_row': 8, 'field': 'amount',
     'expected': None, 'new': 12000000.0,
     'why': '60 га x 200 000 — сумма счёта, вписанная в колонку затрат'},
    {'source_row': 8, 'field': 'price_per_ha',
     'expected': 85633.0, 'new': 200000.0,
     'why': 'цена поднята диспетчером в исправленной книге'},
    {'source_row': 6, 'field': 'other_costs',
     'expected': 800000.0, 'new': None,
     'why': 'запись снята диспетчером: итог G9 сменился 800 000 -> 12 000 000'},
)

ALLOWED_FIELDS = ('amount', 'price_per_ha', 'other_costs')
EPS = 0.005


def same(a, b):
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) <= EPS


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Fix the March Kogon row whose bill was written into '
                    'the expenses column. Dry-run by default.')
    parser.add_argument('--db', default=importer.DEFAULT_DB_PATH,
                        help='SQLite database (default instance/transport.db)')
    parser.add_argument('--dry-run', action='store_true', default=True,
                        help='DEFAULT. Read-only, report, write nothing.')
    parser.add_argument('--apply', action='store_true', help='actually write')
    parser.add_argument('--report', default=None,
                        help='UTF-8 report file (default next to the db)')
    args = parser.parse_args(argv)
    if args.apply:
        args.dry_run = False
    if args.report is None:
        args.report = os.path.join(
            os.path.dirname(os.path.abspath(args.db)) or '.',
            'backfill_drone_march_amount_fix_report.txt')

    try:
        con = importer._connect(args.db, read_only=not args.apply)
    except importer.ImportPreconditionError as exc:
        print(str(exc))
        return 2

    try:
        try:
            importer._require_tables(con, ('drone_works',))
        except importer.ImportPreconditionError as exc:
            print(str(exc))
            return 2

        lines = []
        add = lines.append
        add('backfill_drone_march_amount_fix — %s'
            % ('ЗАПИСЬ (--apply)' if args.apply else 'СУХОЙ ПРОГОН'))
        add('=' * 74)
        add('база : %s' % os.path.abspath(args.db))
        add('книга: %s | лист %s' % (BOOK, SHEET))
        add('')

        planned, refused, absent = [], [], []
        for fix in FIXES:
            assert fix['field'] in ALLOWED_FIELDS
            found = con.execute(
                'SELECT id, %s, customer_raw FROM drone_works '
                'WHERE source_file = ? AND source_sheet = ? '
                'AND source_row = ?' % fix['field'],
                (BOOK, SHEET, fix['source_row'])).fetchall()
            if not found:
                absent.append(fix)
                continue
            for work_id, current, customer in found:
                current = float(current) if current is not None else None
                if same(current, fix['expected']):
                    planned.append(dict(fix, id=work_id, old=current,
                                        customer=customer))
                else:
                    refused.append(dict(fix, id=work_id, old=current))

        for p in planned:
            add('  id %-6s стр %-3s %-13s %s -> %s   [%s]'
                % (p['id'], p['source_row'], p['field'], p['old'], p['new'],
                   p['why']))
        for r in refused:
            add('  ОТКАЗ id %-6s стр %-3s %-13s: в базе %s, ожидалось %s'
                % (r['id'], r['source_row'], r['field'], r['old'],
                   r['expected']))
        for a in absent:
            add('  НЕТ В БАЗЕ: строка %s (%s)'
                % (a['source_row'], a['field']))
        add('')

        written = 0
        if args.apply:
            cur = con.cursor()
            cur.execute('BEGIN')
            for p in planned:
                # Ожидание повторено в WHERE самого UPDATE: между выборкой и
                # записью значение могла сменить чужая рука.
                if p['old'] is None:
                    cur.execute(
                        'UPDATE drone_works SET %s = ? WHERE id = ? '
                        'AND %s IS NULL' % (p['field'], p['field']),
                        (p['new'], p['id']))
                else:
                    cur.execute(
                        'UPDATE drone_works SET %s = ? WHERE id = ? '
                        'AND %s = ?' % (p['field'], p['field']),
                        (p['new'], p['id'], p['old']))
                written += cur.rowcount
            con.commit()
            add('ЗАПИСАНО: %d исправлений' % written)
            add('')
            add('ОТКАТ, построчно:')
            for p in planned:
                add('  UPDATE drone_works SET %s = %s WHERE id = %d;'
                    % (p['field'],
                       'NULL' if p['old'] is None else repr(p['old']),
                       p['id']))
            if written == 0:
                add('')
                add('0 rows to change.')
        else:
            add('СУХОЙ ПРОГОН: не записано ничего. Было бы исправлений: %d.'
                % len(planned))

        add('')
        add('ВПИСАТЬ В САМУ КНИГУ (иначе построчная сверка будет показывать')
        add('расхождение, а переимпорт вернёт ошибку):')
        add('  %s | лист «%s»:' % (BOOK, SHEET))
        add('  перенести 12 000 000 из «Бошка харажатлар» (G8) в '
            '«Жами сумма» (E8).')

        with open(args.report, 'w', encoding='utf-8') as fh:
            fh.write('\n'.join(lines) + '\n')
        for line in lines:
            print(importer._ascii(line))
        print('report: %s' % importer._ascii(os.path.abspath(args.report)))
        return 0
    except Exception:
        if args.apply:
            try:
                con.rollback()
            except Exception:
                pass
        raise
    finally:
        con.close()


if __name__ == '__main__':
    sys.exit(main())
