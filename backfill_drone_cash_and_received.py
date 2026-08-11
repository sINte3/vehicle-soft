#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backfill_drone_cash_and_received.py -- DRONE-CASH-DEFAULT-001: применить к
уже лежащим работам два решения владельца от 2026-08-11.

ПОЧЕМУ. Руководство увидело в отчётах около 750 млн сум долга и прочитало их
как дебиторку. Разбор 28 книг показал, что долг слеплен из трёх разных
вещей, и две из них — не долг вовсе. Этот скрипт убирает две причины из
базы; третью (харажат и перечисление) разводят отчёты, а не данные.

ЧАСТЬ 1. ТИП ОПЛАТЫ. Решение владельца, приведено дословно: «во всех книгах
диспетчеров, если тип оплаты не стоит Справка или перечисление или иное - то
это считается наличка». 383 работы из 913 пришли из листов без блоков
«Справка»/«Нақд» и лежат с payment_type = 'unknown' — под ярлыком, по
которому нельзя ни позвонить, ни свести кассу. Все они становятся 'cash'.
Импортёр с того же дня ставит наличку сам (payment_when_no_block), поэтому
повторный импорт этих же книг тип больше не испортит.

ЧАСТЬ 2. ПОЛУЧЕНО. Владелец выверил 383 строки и заполнил «получено» там,
где его не было: 14 строк получили сумму, 5 — ноль с пояснением «ПУЛ
ОЛИНМАГАН» / «Топшириқ пули берилмаган», то есть деньги действительно не
пришли. Правки лежат в tools/drone_received_corrections_20260811.csv рядом с
происхождением каждой строки.

  Контроль, который стоит того, чтобы его назвать: во всех 14 ненулевых
  строках сумма - получено = харажат ТОЧНО, до копейки. Это ровно то
  правило, по которому оператор сдаёт наличность за минусом
  подтверждённых затрат, — то есть цифры владельца согласуются с книгой,
  а не заменяют её.

  Ноль здесь — это ЗАПИСАННЫЙ ноль, а не пустота: «деньги не получены» и
  «получение не записано» — разные утверждения, и DRONE-ZERO-VS-UNKNOWN-001
  требует их различать. Поэтому пять строк получают 0.0, а не остаются
  пустыми.

КНИГИ ОСТАЮТСЯ ИСТОЧНИКОМ ИСТИНЫ. Владелец правил присланный ему файл, а не
книги, поэтому в самих книгах «получено» по этим 19 строкам по-прежнему
пусто. Скрипт печатает их отдельным списком «вписать в книги» — пока это не
сделано, следующий импорт этих книг вернёт пустоту обратно.

--dry-run — РЕЖИМ ПО УМОЛЧАНИЮ: база открывается read-only через file:-URI,
не пишется ничего. --apply пишет ОДНОЙ транзакцией и откатывает всё при
любой ошибке. Повторный --apply обязан напечатать «0 rows to change».

Запуск (сервер, из каталога установки):
  & "C:\\Program Files\\Python314\\python.exe" backfill_drone_cash_and_received.py --db instance\\transport.db
  & "C:\\Program Files\\Python314\\python.exe" backfill_drone_cash_and_received.py --db instance\\transport.db --apply

ОТКАТ ДАННЫХ. Отчёт (UTF-8, --report, по умолчанию рядом с базой) содержит
готовые операторы отката с перечислением id и ПРЕЖНИХ значений:
  UPDATE drone_works SET payment_type = 'unknown' WHERE id IN (<ids>);
  UPDATE drone_works SET received_amount = <старое> WHERE id = <id>;   -- построчно
Изменение данных без записанного отката законченным не считается.
Откат кода — git revert; схема не меняется.

Консоль — только ASCII; полный читаемый вывод — в отчёте UTF-8.
"""

import argparse
import csv
import os
import sys

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(REPO_ROOT, 'tools'))

import import_drone_works as importer  # noqa: E402  [REASON]: _connect с
# отказом при отсутствии базы и mode=ro на сухом прогоне, _require_tables и
# _ascii — те же, что у импортёра и у бэкфилла подразделений. Третья копия
# этих правил разошлась бы с первыми двумя молча.

DEFAULT_CSV = os.path.join(REPO_ROOT, 'tools',
                           'drone_received_corrections_20260811.csv')

# [REASON]: пыль плавающей точки, а не порог. Книга хранит
# 1605999.9999999998 там, где человек видит 1 606 000, и сравнение на
# равенство здесь врало бы.
EPS = 0.005


def read_corrections(path):
    """Строки правок «получено» из CSV с происхождением и ожиданием."""
    out = []
    with open(path, encoding='utf-8') as fh:
        rows = [line for line in fh if not line.lstrip().startswith('#')]
    for row in csv.DictReader(rows, delimiter=';'):
        def num(name):
            value = (row.get(name) or '').strip()
            return float(value) if value else None
        out.append({
            'file': row['source_file'].strip(),
            'sheet': row['source_sheet'],
            'row': int(row['source_row']),
            'expected': num('expected_received'),
            'new': num('new_received'),
            'note': (row.get('note') or '').strip(),
        })
    return out


def same(a, b):
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(a - b) <= EPS


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Apply the 2026-08-11 owner decisions to drone_works: '
                    'unknown payment type becomes cash, and the verified '
                    'received amounts are filled in.')
    parser.add_argument('--db', default=importer.DEFAULT_DB_PATH,
                        help='SQLite database (default instance/transport.db)')
    parser.add_argument('--corrections', default=DEFAULT_CSV,
                        help='CSV with the verified received amounts')
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
            'backfill_drone_cash_and_received_report.txt')

    if not os.path.exists(args.corrections):
        print('ERROR: corrections file not found: %s'
              % importer._ascii(args.corrections))
        return 2
    corrections = read_corrections(args.corrections)

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
        mode = 'ЗАПИСЬ (--apply)' if args.apply else 'СУХОЙ ПРОГОН'
        add('backfill_drone_cash_and_received — %s' % mode)
        add('=' * 74)
        add('база: %s' % os.path.abspath(args.db))
        add('')

        # ── Часть 1: тип оплаты ──────────────────────────────────────────
        unknown_rows = con.execute(
            "SELECT id, source_file, source_sheet, source_row, area_ha, "
            "amount, customer_raw FROM drone_works "
            "WHERE payment_type = 'unknown' ORDER BY id").fetchall()
        area = sum((r[4] or 0.0) for r in unknown_rows)
        amount = sum((r[5] or 0.0) for r in unknown_rows)
        add('ЧАСТЬ 1. ТИП ОПЛАТЫ «unknown» -> «cash»')
        add('  работ: %d, гектаров: %.2f, сумма: %.2f'
            % (len(unknown_rows), area, amount))
        by_file = {}
        for r in unknown_rows:
            by_file[r[1]] = by_file.get(r[1], 0) + 1
        for name, count in sorted(by_file.items(), key=lambda kv: -kv[1]):
            add('    %-52s %4d' % ((name or '(ручной ввод)')[:52], count))
        add('')

        # ── Часть 2: получено ────────────────────────────────────────────
        planned, refused, absent = [], [], []
        for c in corrections:
            found = con.execute(
                'SELECT id, received_amount, amount, other_costs, '
                'customer_raw FROM drone_works WHERE source_file = ? '
                'AND source_sheet = ? AND source_row = ?',
                (c['file'], c['sheet'], c['row'])).fetchall()
            if not found:
                absent.append(c)
                continue
            for work_id, received, amount_db, other, customer in found:
                current = float(received) if received is not None else None
                if not same(current, c['expected']):
                    # [REASON]: правка применяется только к тому, что мы
                    # видели при разборе. Если в базе уже другое значение,
                    # его вписал человек или другой прогон, и затирать его
                    # молча нельзя — строка называется и пропускается.
                    refused.append((c, work_id, current))
                    continue
                planned.append({'c': c, 'id': work_id, 'old': current,
                                'amount': amount_db, 'other': other,
                                'customer': customer})

        add('ЧАСТЬ 2. ПОЛУЧЕНО ПО ВЫВЕРЕННЫМ СТРОКАМ')
        add('  правок в файле: %d, к применению: %d, отказано: %d, '
            'строки нет в базе: %d'
            % (len(corrections), len(planned), len(refused), len(absent)))
        for p in planned:
            add('    id %-6s %-34s стр %-4s  %s -> %s%s'
                % (p['id'], (p['c']['file'] or '')[:34], p['c']['row'],
                   p['old'], p['c']['new'],
                   ('   [%s]' % p['c']['note']) if p['c']['note'] else ''))
        for c, work_id, current in refused:
            add('    ОТКАЗ id %-6s %-34s стр %-4s: в базе %s, ожидалось %s'
                % (work_id, c['file'][:34], c['row'], current, c['expected']))
        for c in absent:
            add('    НЕТ В БАЗЕ %-34s лист %s стр %s'
                % (c['file'][:34], c['sheet'], c['row']))
        add('')

        written_payment = written_received = 0
        changed_ids = []
        if args.apply:
            cur = con.cursor()
            cur.execute('BEGIN')
            for r in unknown_rows:
                # Критерий повторён в WHERE: между выборкой и записью тип мог
                # поменять человек, и перезаписывать его нельзя.
                cur.execute("UPDATE drone_works SET payment_type = 'cash' "
                            "WHERE id = ? AND payment_type = 'unknown'",
                            (r[0],))
                if cur.rowcount == 1:
                    written_payment += 1
                    changed_ids.append(r[0])
            for p in planned:
                if p['old'] is None:
                    cur.execute('UPDATE drone_works SET received_amount = ? '
                                'WHERE id = ? AND received_amount IS NULL',
                                (p['c']['new'], p['id']))
                else:
                    cur.execute('UPDATE drone_works SET received_amount = ? '
                                'WHERE id = ? AND received_amount = ?',
                                (p['c']['new'], p['id'], p['old']))
                if cur.rowcount == 1:
                    written_received += 1
            con.commit()

        if args.apply:
            add('ЗАПИСАНО: тип оплаты %d строк, получено %d строк'
                % (written_payment, written_received))
            add('')
            add('ОТКАТ. Тип оплаты:')
            add('  UPDATE drone_works SET payment_type = \'unknown\' '
                'WHERE id IN (%s);' % ', '.join(str(i) for i in changed_ids))
            add('ОТКАТ. Получено, построчно:')
            for p in planned:
                add('  UPDATE drone_works SET received_amount = %s WHERE id = %d;'
                    % ('NULL' if p['old'] is None else repr(p['old']), p['id']))
            if written_payment == 0 and written_received == 0:
                add('')
                add('0 rows to change.')
        else:
            add('СУХОЙ ПРОГОН: не записано ничего. Было бы изменено: '
                'тип оплаты %d строк, получено %d строк.'
                % (len(unknown_rows), len(planned)))

        add('')
        add('ВПИСАТЬ В САМИ КНИГИ (иначе следующий импорт вернёт пустоту):')
        for p in planned:
            add('  %s | лист %s | строка %s | «Кирим қилинган» = %s'
                % (p['c']['file'], p['c']['sheet'], p['c']['row'],
                   p['c']['new']))

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
