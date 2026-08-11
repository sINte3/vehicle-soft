#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backfill_drone_works_subdivision.py -- DRONE-CLOSE-SUBDIV-001, историческая
часть: проставить подразделение работам, у которых нет ни оператора, ни
подразделения, выводя его из данных той же книги.

Зачем. Экран закрытия /drones/reports/closing называет, кто не сдал
ведомость. 105 работ на production (2 445.32 га) не несут ни
drone_operator_id, ни subdivision_name, лежат в корзине «Подразделение не
определено» -- и сданная книга читается как несданная. Ридер (шаги 1-2,
оператор и COALESCE) уже реализован в drones.py и здесь не трогается; этот
скрипт применяет только шаги 3-4 правила к уже лежащим строкам:

  шаг 3: у остальных работ ТОЙ ЖЕ книги (source_file) собираются
         подразделения их опознанных операторов; ровно одно различное --
         книга принадлежит ему, и сироты наследуют. Два и более -- отказ.
  шаг 4: только когда в книге нет НИ ОДНОГО опознанного оператора --
         имя файла через Directory.subdivision_for_file() (целое имя парка,
         затем различающие слова, выведенные из справочника; ничего руками).

Никакая ступень не гадает: не решила -- строка остаётся как есть и
перечисляется с причиной.

--dry-run -- РЕЖИМ ПО УМОЛЧАНИЮ: база открывается read-only через file:-URI,
не пишется ничего. Первый настоящий импорт этого трека спас именно dry-run
по умолчанию. --apply пишет ОДНОЙ транзакцией и откатывает всё при любой
ошибке. Повторный --apply обязан напечатать «0 rows to change» -- строки,
получившие подразделение, под критерий сирот больше не подпадают.

Запуск (сервер, из каталога установки):
  & "C:\\Program Files\\Python314\\python.exe" backfill_drone_works_subdivision.py --db instance\\transport.db
  & "C:\\Program Files\\Python314\\python.exe" backfill_drone_works_subdivision.py --db instance\\transport.db --apply

ОТКАТ ДАННЫХ. Отчёт (UTF-8, --report, по умолчанию рядом с базой) перечисляет
id КАЖДОЙ изменённой работы. Изменение только одно -- запись значения в
пустой subdivision_name, поэтому откат механический:
  UPDATE drone_works SET subdivision_name = NULL WHERE id IN (<ids из отчёта>);
Изменение данных без записанного отката не считается законченным -- список id
и есть этот откат. Откат кода -- git revert коммита; схема не меняется.

Консоль -- только ASCII (кириллица уходит в \\uXXXX, как у импортёра);
полный читаемый вывод -- в отчёте UTF-8.
"""

import argparse
import os
import sys

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(REPO_ROOT, 'tools'))

import import_drone_works as importer  # noqa: E402  [REASON]: Directory,
# _connect (отказ при отсутствии файла базы, mode=ro на dry-run),
# _require_tables и _ascii переиспользуются, а не переписываются: второй
# нормализатор и вторая логика токенов разошлись бы с импортёром молча.

ORPHAN_WHERE = ("drone_operator_id IS NULL "
                "AND (subdivision_name IS NULL OR subdivision_name = '')")


def collect_books(con):
    """Книги с сиротами: source_file -> [(id, area_ha), ...].

    Работы без source_file (ручной ввод с экрана) выводимого источника не
    имеют -- они перечисляются отдельной строкой и не трогаются.
    """
    books = {}
    no_source = []
    for row in con.execute(
            'SELECT id, source_file, area_ha FROM drone_works '
            'WHERE %s ORDER BY source_file, id' % ORPHAN_WHERE):
        if row[1]:
            books.setdefault(row[1], []).append((row[0], row[2] or 0.0))
        else:
            no_source.append((row[0], row[2] or 0.0))
    return books, no_source


def decide_book(con, directory, source_file):
    """(subdivision | None, 'step 3'/'step 4'/None, причина отказа | None)."""
    resolved_ops = con.execute(
        'SELECT COUNT(*) FROM drone_works '
        'WHERE source_file = ? AND drone_operator_id IS NOT NULL',
        (source_file,)).fetchone()[0]
    if resolved_ops == 0:
        # [REASON]: шаг 4 разрешён только при полном отсутствии опознанных
        # операторов в книге. Книга, чьи операторы из двух подразделений
        # («Сервис Дрон Маълумот (2).xlsx»), обязана остаться нерешённой на
        # шаге 3 и НЕ провалиться в шаг 4, хотя слово «Сервис» в её имени
        # есть: операторы -- более сильное свидетельство, чем имя файла.
        by_name = directory.subdivision_for_file(source_file)
        if by_name:
            return by_name, 'step 4', None
        return None, None, 'no resolved operators; file name decides nothing'
    subs = set()
    for row in con.execute(
            'SELECT DISTINCT o.subdivision_name FROM drone_works w '
            'JOIN drone_operators o ON o.id = w.drone_operator_id '
            'WHERE w.source_file = ?', (source_file,)):
        if row[0]:
            subs.add(row[0])
    if len(subs) == 1:
        return next(iter(subs)), 'step 3', None
    if not subs:
        return None, None, ('%d resolved operators, none carries a subdivision'
                            % resolved_ops)
    return None, None, 'operators span %d subdivisions' % len(subs)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Backfill drone_works.subdivision_name from the book '
                    '(steps 3-4 of DRONE-CLOSE-SUBDIV-001).')
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
            'backfill_drone_works_subdivision_report.txt')

    try:
        con = importer._connect(args.db, read_only=not args.apply)
    except importer.ImportPreconditionError as exc:
        print(str(exc))
        return 2
    try:
        try:
            importer._require_tables(
                con, ('drone_works', 'drone_operators', 'drone_units',
                      'drone_customers', 'drone_customer_aliases'))
        except importer.ImportPreconditionError as exc:
            print(str(exc))
            return 2

        directory = importer.Directory(con)
        books, no_source = collect_books(con)

        lines = []          # отчёт UTF-8
        changed = []        # (id, subdivision) -- механический откат
        planned = []        # (source_file, ids, area, step, subdivision)
        unresolved = []     # (source_file, n, area, причина)

        for source_file in sorted(
                books, key=lambda f: -sum(a for _, a in books[f])):
            rows = books[source_file]
            area = sum(a for _, a in rows)
            sub, step, why = decide_book(con, directory, source_file)
            if sub:
                planned.append((source_file, [i for i, _ in rows], area,
                                step, sub))
            else:
                unresolved.append((source_file, len(rows), area, why))

        mode = 'APPLY' if args.apply else 'DRY RUN'
        lines.append('backfill_drone_works_subdivision -- %s' % mode)
        lines.append('база: %s' % os.path.abspath(args.db))
        lines.append('сирот (без оператора и подразделения): %d работ в %d '
                     'книгах, %d без source_file'
                     % (sum(len(v) for v in books.values()), len(books),
                        len(no_source)))
        lines.append('')
        lines.append('КНИГА | СИРОТ | ГА | СТУПЕНЬ | ПОДРАЗДЕЛЕНИЕ')
        for source_file, ids, area, step, sub in planned:
            lines.append('  %s | %d | %.2f | %s | %s'
                         % (source_file, len(ids), area, step, sub))
        for source_file, n, area, why in unresolved:
            lines.append('  %s | %d | %.2f | unresolved | %s'
                         % (source_file, n, area, why))
        for wid, area in no_source:
            lines.append('  (без source_file) id=%d | 1 | %.2f | unresolved | '
                         'manual entry, nothing to derive from' % (wid, area))

        written = 0
        if args.apply:
            cur = con.cursor()
            cur.execute('BEGIN')
            for source_file, ids, area, step, sub in planned:
                for wid in ids:
                    # [REASON]: критерий сироты повторён в WHERE самого
                    # UPDATE: между выборкой и записью строка могла получить
                    # значение чужой рукой, и перезаписывать его нельзя --
                    # «никогда не перезаписывает существующее» из промпта.
                    cur.execute(
                        'UPDATE drone_works SET subdivision_name = ? '
                        'WHERE id = ? AND %s' % ORPHAN_WHERE, (sub, wid))
                    if cur.rowcount == 1:
                        written += 1
                        changed.append((wid, sub))
            con.commit()

        lines.append('')
        if args.apply:
            lines.append('ЗАПИСАНО: %d строк' % written)
            lines.append('')
            lines.append('ИЗМЕНЁННЫЕ ID (откат: UPDATE drone_works '
                         'SET subdivision_name = NULL WHERE id IN (...)):')
            for wid, sub in changed:
                lines.append('  %d -> %s' % (wid, sub))
        else:
            would = sum(len(ids) for _, ids, _, _, _ in planned)
            lines.append('DRY RUN: записей не сделано. Было бы изменено: %d '
                         'строк. Повторить с --apply.' % would)

        with open(args.report, 'w', encoding='utf-8') as fh:
            fh.write('\n'.join(lines) + '\n')

        for line in lines:
            print(importer._ascii(line))
        print('report: %s' % importer._ascii(os.path.abspath(args.report)))
        if args.apply and written == 0:
            print('0 rows to change.')
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
