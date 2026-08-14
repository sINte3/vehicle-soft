#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""load_drone_assignments_oct2025_aug2026.py -- DRONE-FLEET-ASSIGN-001, часть 2.

Ставит операторов на машины за октябрь-2025 .. август-2026 -- 24 назначения,
выведенные из листа v3, который заполнили диспетчеры и принял владелец
2026-08-12, с поправками владельца от 2026-08-14. Сентябрь-2025 загружен
отдельным скриптом (load_drone_assignments_sept2025.py) и здесь не трогается:
ни одна строка этого файла не начинается раньше 2025-10-01.

НИЧЕГО НЕ ПИШЕТ БЕЗ --apply. Режим по умолчанию -- сухой прогон: база
открывается read-only через file:-URI, скрипт печатает, ЧТО было бы записано,
и кладёт полный отчёт в UTF-8 файл. Запись -- только с флагом --apply, одной
транзакцией, с постусловием до коммита и списком id для отката.

ОТКУДА ВЗЯТА КАЖДАЯ СТРОКА. Метка источника хранится префиксом в note,
машинно отделяемым, как у сентябрьского загрузчика:

  [SHEET]     -- строка листа назначений v3: заполнили диспетчеры, принял
                 владелец 2026-08-12. Взята как есть (даты, начинавшиеся в
                 сентябре, обрезаны до 01.10.2025 -- сентябрь уже загружен).
  [TELEMETRY] -- граница или имя подтверждены данными: гектары книги сходятся
                 с гектарами машины, либо дата пересадки видна в вылетах.
  [OWNER]     -- назвал владелец 2026-08-14, телеметрия не противоречит,
                 но самостоятельно не подтверждает.

РАЗБИВКА МАШИНЫ 2 -- единственное место, где строка v3 ЗАМЕНЕНА, и вот почему.
v3 давал №2 Рухиллоеву Сайфулло на весь период 01.09.2025-23.07.2026. Владелец
2026-08-14 поправил: «Сайфулло пересел с №1 на №2 в марте 2026; дроном №2
пользовался Хамроев Шохрух». Дату пересадки называет телеметрия: в марте №2
не летала НИ РАЗУ до 23.03.2026, а с 23.03 сделала все свои мартовские
162.02 га; №10, куда по v3 в тот же день 23.03 пришёл Хамроев, начинает
летать тогда же. Книга Агрокластера за март (Сайфулло, 179.2 га) сходится с
суммой №1 за 27.03 + №2 с 23.03 = 182.76 га в пределах 2 %. Три источника --
слова владельца, вылеты, книга -- дают одну и ту же границу: Хамроев на №2
по 22.03.2026, Сайфулло на №2 с 23.03.2026.

ЧТО СОЗНАТЕЛЬНО ОСТАЁТСЯ БЕЗ ОПЕРАТОРА (окна перечислены в UNCOVERED и
печатаются в отчёте с живыми гектарами из drone_flights):
  - март-2026 (№1, №8, №11, №15) и №10 20.06-06.07.2026 -- решение владельца
    2026-08-14: «пусть числятся как не определённые оператором»;
  - №15 08-30.10.2025 -- после починки привязки эти вылеты переехали с №13
    на №15; в v3 этого периода не существовало, оператор не спрошен;
  - №8 14-15.10.2025 -- вопрос v3 («Нурали до 13.10, кто дальше?») остался
    без ответа;
  - микрополёты №9 (февраль-апрель 2026, 0.29 га) и №12 13-15.07.2026.

[REASON]: загрузчик НЕ читает вылеты для назначений и НЕ выводит операторов
сам -- он записывает решения людей и помечает, чьи они. Вылеты читаются
только для справки в отчёте (гектары непокрытых окон), чтобы владелец видел
цену каждого открытого вопроса в гектарах, а не в абстракциях.

Гвардия наложений: строка, чей период пересекается с уже лежащим назначением
той же машины, пропускается и перечисляется в отчёте. Поэтому повторный
--apply вставляет ноль, а уже загруженный сентябрь (и №14 до 31.10.2025)
не задваивается.

КОДЫ ВОЗВРАТА (те же, что у сентябрьского):
  0 -- загружено всё, что было к загрузке;
  1 -- предусловие не выполнено, не записано НИЧЕГО (нет машины в парке;
       имя, под которым в справочнике ДВОЕ);
  2 -- файла базы нет, база не создавалась;
  3 -- загружено, но не всё: чьё-то имя отсутствует в справочнике операторов.

Запуск (сервер, из каталога установки; сухой прогон -- служба может работать):
  & "C:\\Program Files\\Python314\\python.exe" load_drone_assignments_oct2025_aug2026.py --db instance\\transport.db
Запись (служба ОСТАНОВЛЕНА):
  & "C:\\Program Files\\Python314\\python.exe" load_drone_assignments_oct2025_aug2026.py --db instance\\transport.db --apply

ОТКАТ ДАННЫХ. Отчёт печатает id каждой вставленной строки; вставка --
единственное изменение:
  DELETE FROM drone_operator_assignments WHERE id IN (<id из отчёта>);
Откат кода -- git revert коммита; схема не меняется.

Консоль -- только ASCII; читаемый отчёт с кириллицей уходит в UTF-8 файл.
"""

import argparse
import datetime
import os
import sqlite3
import sys

# [REASON]: normalize/справочник/коды один в один как у сентябрьской загрузки.
# Два определения гомоглифной нормализации разъехались бы молча -- и один и
# тот же человек грузился бы одним скриптом и терялся другим.
from load_drone_assignments_sept2025 import (
    OWNER, TELEMETRY, connect, die, normalize, resolve_operators,
    resolve_units, suggest)

SHEET = 'SHEET'

V3_NOTE = ('лист v3: заполнили диспетчеры, принял владелец 2026-08-12')

# (машина, оператор, дата с, дата по (None = по сей день), источник, почему)
ASSIGNMENTS = (
    (1, 'Рухиллоев Сайфулло', '2025-10-01', '2025-10-12', OWNER,
     'владелец 2026-08-14: на №2 Сайфулло пересел только в марте, значит '
     'октябрь №1 (03-12.10, 187.45 га) его; книга Достон АКА даёт за октябрь '
     '246.9 га -- расхождение остаётся вопросом, машине оно не мешает'),
    (1, 'Садуллаев Шахбоз', '2026-04-13', '2026-05-17', SHEET, V3_NOTE),
    (2, 'Хамроев Шохрух', '2025-10-04', '2026-03-22', OWNER,
     'владелец 2026-08-14: «дроном №2 пользовался Хамроев Шохрух»; границы '
     'из телеметрии -- первый полёт №2 04.10.2025, последний день до '
     'пересадки Сайфулло 22.03.2026. Заменяет строку v3, дававшую №2 '
     'Сайфулло с 01.09.2025'),
    (2, 'Рухиллоев Сайфулло', '2026-03-23', '2026-07-23', TELEMETRY,
     'дата пересадки видна в вылетах: в марте №2 молчала до 23.03, с 23.03 '
     'все её 162.02 га; книга Агрокластера за март 179.2 га против '
     '№1 27.03 + №2 с 23.03 = 182.76 га, 2 %; апрель 686.8 против 691.8, '
     '0.7 %. Конец 23.07.2026 -- из v3'),
    (3, 'Холмуродов Шахзод', '2025-10-01', None, SHEET,
     V3_NOTE + '; начало 05.09 обрезано до 01.10 -- сентябрь загружен'),
    (4, 'Анваров Усмон', '2025-10-01', '2026-04-13', SHEET,
     V3_NOTE + '; начало 01.09 обрезано до 01.10 -- сентябрь загружен'),
    (5, 'Кобилов Фаррух', '2025-10-01', None, SHEET,
     V3_NOTE + '; начало обрезано до 01.10 -- сентябрь загружен'),
    (6, 'Жураев Туйгун', '2025-10-01', None, SHEET,
     V3_NOTE + '; начало обрезано до 01.10 -- сентябрь загружен'),
    (7, 'Файзуллаев Шохрух', '2026-04-13', '2026-06-22', SHEET, V3_NOTE),
    (8, 'Кодиров Нурали', '2025-10-01', '2025-10-13', SHEET,
     V3_NOTE + '; №8 летала ещё 14-15.10 (1.27 га) -- эти два дня остаются '
     'без оператора, вопрос v3 без ответа'),
    (8, 'Файзуллаев Шохрух', '2026-04-09', '2026-04-13', SHEET, V3_NOTE),
    (8, 'Анваров Усмон', '2026-04-14', None, SHEET, V3_NOTE),
    (9, 'Туробов Жахонгир', '2026-07-08', None, SHEET, V3_NOTE),
    (10, 'Хамроев Шохрух', '2026-03-23', '2026-06-09', SHEET,
     V3_NOTE + '; согласуется с уходом с №2 22.03 и с мартовской книгой '
     'Когона 108.1 га против №10 97.6 га'),
    (10, 'Рухиллоев Сайфулло', '2026-07-29', None, SHEET, V3_NOTE),
    (11, 'Шабанов Дилшод', '2026-04-13', '2026-05-11', SHEET, V3_NOTE),
    (11, 'Акрамов Амирбек', '2026-07-29', None, SHEET, V3_NOTE),
    (12, 'Кудратов Мухриддин', '2025-10-01', '2026-03-31', SHEET,
     V3_NOTE + '; начало 06.09 обрезано до 01.10 -- сентябрь загружен. '
     'После 31.03 машина не летала до июля -- согласуется'),
    (12, 'Садуллаев Шахбоз', '2026-07-17', '2026-07-19', SHEET, V3_NOTE),
    (13, 'Имомов Бехзод', '2025-10-01', '2026-05-08', SHEET,
     V3_NOTE + '; начало 01.09 обрезано до 01.10 -- сентябрь загружен'),
    (13, 'Ибодуллаев Хасанбой', '2026-06-09', '2026-07-14', SHEET,
     V3_NOTE + '; конец 14.07.2026 подтверждён владельцем 2026-08-14'),
    (13, 'Хамроев Шохрух', '2026-08-09', None, SHEET, V3_NOTE),
    (14, 'Болтаев Шахзод', '2026-04-11', None, SHEET,
     V3_NOTE + '; предыдущий оператор №14 (Файзуллаев Шоди, до 31.10.2025) '
     'загружен сентябрьским скриптом, машина между ними не летала'),
    (15, 'Жумаев Фуркат', '2026-04-04', None, SHEET, V3_NOTE),
)

# Окна, которые СОЗНАТЕЛЬНО остаются без оператора. Печатаются в отчёте с
# живыми гектарами из drone_flights, чтобы у каждого открытого вопроса была
# видна цена.
UNCOVERED = (
    (1, '2026-03-27', '2026-04-12',
     'решение владельца 2026-08-14: не определено'),
    (7, '2025-10-01', '2025-10-12',
     'октябрьский хвост строки НОМАЪЛУМ v3; вопрос остался без ответа'),
    (8, '2025-10-14', '2025-10-15',
     'вопрос v3 (Нурали до 13.10 -- кто эти два дня?) без ответа'),
    (8, '2026-03-16', '2026-03-23',
     'решение владельца 2026-08-14: не определено'),
    (10, '2026-06-20', '2026-07-06',
     'решение владельца 2026-08-14: не определено'),
    (11, '2026-03-16', '2026-03-20',
     'решение владельца 2026-08-14: не определено'),
    (15, '2025-10-08', '2025-10-30',
     'вылеты переехали с №13 на №15 починкой привязки; в v3 периода не '
     'существовало, оператор никем не назван'),
    (15, '2026-03-16', '2026-03-20',
     'решение владельца 2026-08-14: не определено'),
    (9, '2026-02-01', '2026-04-12', 'микрополёты, оператор неизвестен'),
    (12, '2026-07-13', '2026-07-15',
     'до строки Садуллаева 17-19.07; кто -- неизвестно'),
)

FIRST_ALLOWED_DAY = '2025-10-01'
INF = '9999-12-31'


def overlaps(conn, unit_id, date_from, date_to):
    """Назначения этой машины, пересекающие [date_from, date_to]."""
    return conn.execute(
        'SELECT id, operator_id, date_from, date_to '
        'FROM drone_operator_assignments '
        'WHERE drone_unit_id = ? AND date_from <= ? '
        '  AND COALESCE(date_to, ?) >= ?',
        (unit_id, date_to or INF, INF, date_from)).fetchall()


def window_flights(conn, unit_id, date_from, date_to):
    """Вылеты машины в окне, день считается в UTC+5. Только чтение."""
    try:
        row = conn.execute(
            "SELECT COUNT(*), COALESCE(ROUND(SUM(area_ha), 2), 0) "
            "FROM drone_flights WHERE drone_unit_id = ? "
            "  AND strftime('%Y-%m-%d', datetime(started_at, '+300 minutes')) "
            "      BETWEEN ? AND ?",
            (unit_id, date_from, date_to)).fetchone()
    except sqlite3.OperationalError:
        return (0, 0.0)          # таблицы вылетов нет -- окно без справки
    return row


def plan(conn):
    """Что вставить, что пропустить и почему. Ничего не пишет.

    Контракт сентябрьского загрузчика: ненайденный оператор откладывает СВОЮ
    строку, а не всю загрузку; двое под одним именем или отсутствующая
    машина -- отказ целиком, потому что это сомнение в самой базе.
    """
    by_norm = resolve_operators(conn)
    units = resolve_units(conn)
    to_insert, skipped, unresolved, problems = [], [], [], []

    for number, operator, date_from, date_to, source, why in ASSIGNMENTS:
        if number not in units:
            problems.append('unit %d is absent from drone_units' % number)
            continue
        found = by_norm.get(normalize(operator), [])
        if len(found) > 1:
            problems.append(
                'operator %r resolves to %d rows in drone_operators - '
                'ambiguous, refusing' % (operator, len(found)))
            continue
        if not found:
            unresolved.append((number, operator, suggest(by_norm, operator)))
            continue
        operator_id, stored_name = found[0]
        clash = overlaps(conn, units[number], date_from, date_to)
        if clash:
            skipped.append((number, operator, date_from, date_to, clash))
            continue
        to_insert.append((units[number], operator_id, number, stored_name,
                          date_from, date_to, source, why))
    return to_insert, skipped, unresolved, problems


def write_report(path, conn, to_insert, skipped, unresolved, problems,
                 inserted_ids, directory=None):
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write('DRONE-FLEET-ASSIGN-001, часть 2: октябрь-2025 .. '
                 'август-2026\n')
        fh.write('=' * 72 + '\n\n')
        if problems:
            fh.write('ПРОБЛЕМЫ (ничего не записано):\n')
            for p in problems:
                fh.write('  - %s\n' % p)
            fh.write('\n')
        if unresolved:
            fh.write('ОПЕРАТОР НЕ НАЙДЕН В СПРАВОЧНИКЕ -- строка отложена, '
                     'остальные загружены:\n')
            for number, operator, hints in unresolved:
                fh.write('  машина %-3d ищем «%s»\n' % (number, operator))
                if hints:
                    fh.write('      похожие в справочнике: %s\n'
                             % '; '.join(hints))
                else:
                    fh.write('      похожих в справочнике нет вовсе\n')
            if directory:
                fh.write('\n  СПРАВОЧНИК ОПЕРАТОРОВ ЦЕЛИКОМ (%d):\n'
                         % len(directory))
                for name in directory:
                    fh.write('      %s\n' % name)
            fh.write('\n')

        fh.write('К ВСТАВКЕ: %d\n' % len(to_insert))
        for i, item in enumerate(to_insert):
            (_uid, _oid, number, stored, date_from, date_to, source,
             why) = item
            new_id = inserted_ids[i] if i < len(inserted_ids) else None
            fh.write('  машина %-3d %-24s %s..%s  [%s]%s\n'
                     % (number, stored, date_from,
                        date_to or 'по сей день', source,
                        '  id=%s' % new_id if new_id else ''))
            fh.write('      %s\n' % why)

        fh.write('\nПРОПУЩЕНО (уже есть пересекающееся назначение): %d\n'
                 % len(skipped))
        for number, operator, date_from, date_to, clash in skipped:
            fh.write('  машина %-3d %s %s..%s -- в базе уже: %s\n'
                     % (number, operator, date_from, date_to or '...',
                        '; '.join('id=%d %s..%s' % (c[0], c[2], c[3] or '')
                                  for c in clash)))

        fh.write('\nСОЗНАТЕЛЬНО БЕЗ ОПЕРАТОРА (цена вопроса -- живые числа '
                 'из вылетов):\n')
        units = resolve_units(conn)
        total_f = total_ha = 0
        for number, date_from, date_to, why in UNCOVERED:
            flights, ha = window_flights(conn, units.get(number),
                                         date_from, date_to)
            total_f += flights
            total_ha += ha
            fh.write('  машина %-3d %s..%s  %4d вылетов  %9.2f га\n'
                     % (number, date_from, date_to, flights, ha))
            fh.write('      %s\n' % why)
        fh.write('  ---- ИТОГО сознательно открыто: %d вылетов, %.2f га\n'
                 % (total_f, total_ha))

        if inserted_ids:
            fh.write('\nОТКАТ:\n  DELETE FROM drone_operator_assignments '
                     'WHERE id IN (%s);\n'
                     % ', '.join(str(i) for i in inserted_ids))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', default=os.path.join('instance',
                                                     'transport.db'))
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--report', default=None)
    args = parser.parse_args()

    conn = connect(args.db, writable=args.apply)
    unresolved = []
    try:
        to_insert, skipped, unresolved, problems = plan(conn)
        if problems:
            for p in problems:
                print('  problem: %s' % p)
            die('preconditions failed - nothing was written.', 1)

        print('Rows to insert: %d' % len(to_insert))
        print('Skipped (an assignment already overlaps): %d' % len(skipped))
        for number, _op, date_from, date_to, _c in skipped:
            print('  unit %d %s..%s already covered'
                  % (number, date_from, date_to or '...'))
        if unresolved:
            print('Operators absent from drone_operators: %d '
                  '(these rows are POSTPONED, the rest still load)'
                  % len(unresolved))
            for number, _operator, hints in unresolved:
                print('  unit %d: not found; %d similar name(s) - see the '
                      'report' % (number, len(hints)))

        inserted_ids = []
        if args.apply and to_insert:
            now = datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
            try:
                cur = conn.cursor()
                for (unit_id, operator_id, _n, _s, date_from, date_to,
                     source, why) in to_insert:
                    cur.execute(
                        'INSERT INTO drone_operator_assignments '
                        '(operator_id, drone_unit_id, date_from, date_to, '
                        ' note, created_at) VALUES (?, ?, ?, ?, ?, ?)',
                        (operator_id, unit_id, date_from, date_to,
                         '[%s] %s' % (source, why), now))
                    inserted_ids.append(cur.lastrowid)
                # Постусловие ДО коммита: пере-планирование обязано вернуть
                # ноль строк к вставке -- всё легло и закрыто гвардией.
                still, _sk, _un, _pr = plan(conn)
                if still:
                    raise RuntimeError(
                        'post-check failed: %d row(s) still plan to insert '
                        'after the insert' % len(still))
                conn.commit()
            except Exception as exc:
                conn.rollback()
                die('insert failed and was rolled back: %s' % exc, 1)

        directory = None
        if unresolved:
            directory = sorted(
                name for (name,) in conn.execute(
                    'SELECT full_name FROM drone_operators'))
        report = args.report or os.path.join(
            os.path.dirname(os.path.abspath(args.db)) or '.',
            'drone_assignments_oct2025_aug2026_report.txt')
        write_report(report, conn, to_insert, skipped, unresolved, problems,
                     inserted_ids, directory)
        print('Report written to %s' % report)
        if not args.apply:
            print('DRY RUN - nothing was written. Re-run with --apply.')
        else:
            print('Done. %d assignment(s) inserted.' % len(inserted_ids))
    finally:
        conn.close()

    if unresolved:
        sys.exit(3)


if __name__ == '__main__':
    main()
