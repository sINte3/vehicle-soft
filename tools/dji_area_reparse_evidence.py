# -*- coding: utf-8 -*-
"""tools/dji_area_reparse_evidence.py -- пересобрать `dji_flight_evidence` из
УЖЕ СОХРАНЁННЫХ тел источников.

Зачем. Приёмник площадки развёрнут на ревизии 2982396 (08.09.2026), а она
разбирает ревизию списка функцией `parse_list_record`, то есть как ОДНУ
запись. Живой захват кладёт целую СТРАНИЦУ ответа DJI, поэтому разбор падает
на каждом вылете: строка улик получает `list_revision_id`, а все скаляры
`list_*` остаются NULL. Пересчёт, запущенный более новым кодом, честно
отказывается доверять названной, но неразобранной ревизии и сообщает
«площадь неизвестна». В сентябре так вышло у 3980 записей из 4623; уцелели
ровно те 643, у которых была ещё и карточка.

Тела при этом целы и лежат в неизменяемом хранилище. Их НЕ НАДО собирать
заново: достаточно перечитать их нынешним `store.refresh_flight_evidence`,
который понимает обе формы тела (`dji_area.evidence.select_list_record`).

Чего этот инструмент НЕ делает: не ходит в кабинет DJI, не трогает
`drone_flights`, не считает площадь, не пишет `dji_area_calculations`.
Он перечитывает уже имеющиеся байты и обновляет ровно одну таблицу улик.
Пересчёт после него -- отдельный шаг (`tools/dji_area_recalc.py`).

Идемпотентен: второй прогон на том же входе не меняет ни одной строки.

Запуск (служба площадки остановлена либо база -- копия):

  cd C:\\transport-report-staging
  & "C:\\Program Files\\Python314\\python.exe" tools\\dji_area_reparse_evidence.py --db instance\\transport.db --dry-run
  & "C:\\Program Files\\Python314\\python.exe" tools\\dji_area_reparse_evidence.py --db instance\\transport.db --apply

  --from 2026-09-01 --to 2026-09-18   -- только дни отчёта этого периода (UTC+5)
  --only-unparsed                     -- только строки с названной, но неразобранной ревизией списка

Коды возврата: 0 выполнено; 1 ошибка командной строки или данных; 2 база не
найдена (файл НЕ создаётся); 3 хотя бы одна строка ПОТЕРЯЛА уже известные
скаляры списка -- тело перестало читаться, и прогон успехом не считается ни в
сухом виде, ни с `--apply`. Вывод в консоль только ASCII.

Про код 3 отдельно. Восстановление и потеря -- разные события, и складывать их
в один итог нельзя: прогон, стерший восстановленное, выглядел бы успешным. В
блоке R сухой прогон стоит перед `--apply` именно как ворота: он перебирает те
же тела и откатывает транзакцию, поэтому видит ровно тот же набор потерь, что
дал бы `--apply`. Ненулевой код останавливает цепочку ранбука до первой записи.
"""

import argparse
import os
import sys
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dji_area import REPORT_UTC_OFFSET_HOURS  # noqa: E402
from dji_area import store  # noqa: E402

DEFAULT_DB = os.path.join(ROOT, 'instance', 'transport.db')

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_NO_DATABASE = 2
# [REASON]: потеря уже известных скаляров списка -- НЕ успех, и код возврата
# обязан это сказать. Иначе сухой прогон в блоке R заканчивается нулём, за ним
# стартует `--apply`, и тот же обвал повторяется уже с записью в базу.
# Цепочка ранбука ловит только ненулевой код; предупреждения в тексте она не
# читает.
EXIT_LOST_SCALARS = 3

SCALARS = ('list_raw_area_m2', 'list_start_ts', 'list_end_ts',
           'list_mode_name', 'list_manual_mode', 'list_spray_width',
           'list_nickname')


def build_parser():
    parser = argparse.ArgumentParser(
        prog='dji_area_reparse_evidence.py',
        description='Rebuild dji_flight_evidence from source bodies that are '
                    'already stored. Reaches no DJI endpoint.')
    parser.add_argument('--db', dest='db_path', default=DEFAULT_DB,
                        metavar='PATH')
    parser.add_argument('--from', dest='date_from', metavar='YYYY-MM-DD')
    parser.add_argument('--to', dest='date_to', metavar='YYYY-MM-DD')
    parser.add_argument('--only-unparsed', action='store_true',
                        help='only rows whose list revision is named but '
                             'whose list scalars are all NULL')
    parser.add_argument('--dry-run', action='store_true',
                        help='report what would change, write nothing')
    parser.add_argument('--apply', action='store_true',
                        help='rewrite the evidence rows')
    parser.add_argument('--batch-size', type=int, default=500)
    parser.add_argument('--quiet', action='store_true')
    return parser


def check_usage(args):
    if args.dry_run and args.apply:
        raise ValueError('--dry-run and --apply are mutually exclusive')
    if not args.dry_run and not args.apply:
        raise ValueError('choose a mode explicitly: --dry-run writes nothing, '
                         '--apply rewrites the evidence rows')
    if args.batch_size <= 0:
        raise ValueError('--batch-size must be positive')
    if (args.date_from is None) != (args.date_to is None):
        raise ValueError('--from and --to are given together or not at all')


def select_flights(con, date_from, date_to, only_unparsed):
    """Идентификаторы вылетов, у которых ЕСТЬ сохранённые тела источников."""
    where = ['r.flight_id IS NOT NULL']
    params = []
    if date_from is not None:
        # started_at хранится в UTC; день отчёта -- +5 часов.
        lo = datetime.combine(date_from, datetime.min.time()) - timedelta(
            hours=REPORT_UTC_OFFSET_HOURS)
        hi = datetime.combine(date_to + timedelta(days=1),
                              datetime.min.time()) - timedelta(
            hours=REPORT_UTC_OFFSET_HOURS)
        where.append('f.started_at >= ? AND f.started_at < ?')
        params.extend([lo.strftime('%Y-%m-%d %H:%M:%S'),
                       hi.strftime('%Y-%m-%d %H:%M:%S')])
    if only_unparsed:
        where.append('e.list_revision_id IS NOT NULL AND '
                     + ' AND '.join('e.%s IS NULL' % c for c in SCALARS))
    sql = ('SELECT DISTINCT r.flight_id AS flight_id '
           'FROM dji_source_revisions r '
           'JOIN drone_flights f ON f.dji_flight_id = r.flight_id '
           'LEFT JOIN dji_flight_evidence e ON e.flight_id = r.flight_id '
           'WHERE ' + ' AND '.join(where) + ' ORDER BY r.flight_id')
    return [int(row['flight_id']) for row in con.execute(sql, params)]


def snapshot(con, flight_ids):
    """{flight_id: кортеж скаляров} -- чтобы увидеть, что именно изменилось."""
    out = {}
    cols = ', '.join(SCALARS)
    for pos in range(0, len(flight_ids), 500):
        chunk = flight_ids[pos:pos + 500]
        marks = ','.join('?' * len(chunk))
        for row in con.execute(
                'SELECT flight_id, %s FROM dji_flight_evidence '
                'WHERE flight_id IN (%s)' % (cols, marks), chunk):
            out[int(row['flight_id'])] = tuple(row[c] for c in SCALARS)
    return out


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        check_usage(args)
        date_from = date.fromisoformat(args.date_from) if args.date_from \
            else None
        date_to = date.fromisoformat(args.date_to) if args.date_to else None
    except ValueError as exc:
        print('ERROR: %s' % exc)
        return EXIT_USAGE
    if date_from is not None and date_from > date_to:
        print('ERROR: --from is after --to')
        return EXIT_USAGE
    if not os.path.exists(args.db_path):
        # [REASON]: sqlite3.connect would create an empty file and report zero.
        print('ERROR: database not found at %s - refusing to run.'
              % args.db_path)
        return EXIT_NO_DATABASE

    con = store.connect(args.db_path)
    root = store.source_root(os.path.abspath(args.db_path))
    lost = []
    try:
        store.require_tables(con)
        flights = select_flights(con, date_from, date_to, args.only_unparsed)
        before = snapshot(con, flights)
        print('DJI AREA EVIDENCE REPARSE %s'
              % ('APPLY' if args.apply else 'DRY-RUN'))
        print('  flights with stored sources : %d' % len(flights))
        unparsed_before = sum(1 for f in flights
                              if before.get(f) and not any(before[f]))
        print('  list scalars missing before : %d' % unparsed_before)
        if not flights:
            print('Nothing to do.')
            return EXIT_OK

        # [REASON]: пересборка идёт ВСЕГДА (и в dry-run тоже), иначе нечего
        # сравнивать; в dry-run транзакция откатывается целиком, и база
        # остаётся байт в байт прежней.
        store.begin_immediate(con)
        pending = 0
        for n, flight_id in enumerate(flights, 1):
            store.refresh_flight_evidence(con, root, flight_id)
            pending += 1
            if args.apply and pending >= args.batch_size:
                con.execute('COMMIT')
                store.begin_immediate(con)
                pending = 0
            if not args.quiet and n % 1000 == 0:
                print('  ... %d/%d' % (n, len(flights)))
        after = snapshot(con, flights)
        changed = [f for f in flights if before.get(f) != after.get(f)]
        recovered = [f for f in changed
                     if not any(before.get(f) or ()) and any(after.get(f) or ())]
        lost = [f for f in changed
                if any(before.get(f) or ()) and not any(after.get(f) or ())]
        if args.apply:
            con.execute('COMMIT')
        else:
            con.execute('ROLLBACK')
        print('  rows that would change      : %d' % len(changed)
              if not args.apply else '  rows changed                : %d'
              % len(changed))
        print('  list scalars recovered      : %d' % len(recovered))
        print('  list scalars lost           : %d' % len(lost))
        if lost:
            # Потеря скаляров -- находка, а не успех: тело перестало читаться.
            print('  FAILED: %d row(s) lost their list scalars; the stored '
                  'body no longer reads. First: %s'
                  % (len(lost), lost[:5]))
    except (store.StoreError, ValueError) as exc:
        con.close()
        print('ERROR: %s' % exc)
        return EXIT_USAGE
    finally:
        try:
            con.close()
        except Exception:  # noqa: BLE001 -- уже закрыт в ветке ошибки
            pass
    if not args.apply:
        print('Nothing was written. Re-run with --apply to store the result.')
    if lost:
        # Fail-closed: сухой прогон обязан остановить блок R до `--apply`, а
        # `--apply` -- до пересчёта. Потерянные скаляры не «предупреждение», а
        # причина не продолжать.
        print('EXIT %d: list scalars were lost; this run is NOT a success.'
              % EXIT_LOST_SCALARS)
        return EXIT_LOST_SCALARS
    return EXIT_OK


if __name__ == '__main__':
    sys.exit(main())
