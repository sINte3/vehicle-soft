# -*- coding: utf-8 -*-
"""tools/dji_field_census.py -- перепись привязок вылетов к полям DJI.

DRONE-FIELD-PASSPORT-001. Отвечает на вопрос, от которого зависит решение
владельца о полном суточном сборе карточек (пока НЕ одобрен): у какой доли
вылетов периода поле DJI определено доказательно, у какой нет и ПОЧЕМУ --
карточка вылета не собрана, в карточке нет ключа поля, ключа нет в каталоге. Плюс состояние каталога
полей: записи, ревизии, байты границ, общие границы у нескольких записей и
свежесть снимков каталога за последние 30 суток.

Классификация -- `dji_area.field_view.classify`, та же, что у экранов
«Поля» и паспорта вылета; запросы -- `dji_area.field_store`. Отдельной
копии правил здесь нет.

Только чтение: база открывается `mode=ro`, отсутствующая база -- отказ, файл
не создаётся. К DJI инструмент не обращается. Вывод в консоль только ASCII.

Период -- местные даты UTC+5 включительно (как во всех отчётах модуля):
вылет входит, если его начало попадает в [from 00:00, to 24:00) по UTC+5.

Запуск (рабочий каталог -- корень репозитория):

  & "C:\\Program Files\\Python314\\python.exe" tools\\dji_field_census.py --db instance\\transport.db --from 2026-09-01 --to 2026-09-30
  & "C:\\Program Files\\Python314\\python.exe" tools\\dji_field_census.py --db instance\\transport.db --from 2026-08-01 --to 2026-08-31 --json C:\\VehicleSoft_Field_Census\\census_2026_08.json

Коды возврата: 0 -- перепись выполнена; 1 -- ошибка аргументов; 2 -- база
не найдена (файл НЕ создаётся); 3 -- в базе нет таблиц слоя доказательств
(миграция `DJI_AREA_EVIDENCE_001` не применена).

Откат кода: удалить файл, его никто не импортирует. Данных не пишет вовсе,
кроме необязательного JSON по пути `--json`.
"""

import argparse
import json
import os
import sqlite3
import sys

from datetime import date, datetime, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from dji_area import field_store as fs  # noqa: E402
from dji_area import field_view as fv  # noqa: E402

CENSUS_ID = 'DJI_FIELD_CENSUS_001'

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_NO_DATABASE = 2
EXIT_NO_TABLES = 3

LOCAL_OFFSET = timedelta(hours=5)


class UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    # [REASON]: argparse при ошибке выходит с кодом 2, а 2 у инструментов
    # проекта -- «базы нет». Один код на две разные беды владелец различить
    # не смог бы.
    def error(self, message):
        raise UsageError(message)


def parse_args(argv):
    parser = _Parser(description='DJI field attribution census (read-only).')
    parser.add_argument('--db', required=True,
                        help='path to transport.db (opened read-only)')
    parser.add_argument('--from', dest='date_from', required=True,
                        help='first local day (UTC+5), YYYY-MM-DD')
    parser.add_argument('--to', dest='date_to', required=True,
                        help='last local day (UTC+5), YYYY-MM-DD, inclusive')
    parser.add_argument('--json', dest='json_path',
                        help='also write the census as JSON to this file')
    parser.add_argument('--snapshot-days', type=int, default=30,
                        help='window for land snapshot freshness (days)')
    parser.add_argument('--now', dest='now_utc',
                        help='UTC moment for the snapshot window, '
                             'YYYY-MM-DDTHH:MM (default: now)')
    args = parser.parse_args(argv)
    try:
        args.date_from = datetime.strptime(args.date_from, '%Y-%m-%d').date()
        args.date_to = datetime.strptime(args.date_to, '%Y-%m-%d').date()
    except ValueError:
        raise UsageError('dates must be YYYY-MM-DD')
    if args.date_to < args.date_from:
        raise UsageError('--to is earlier than --from')
    if args.snapshot_days < 1:
        raise UsageError('--snapshot-days must be positive')
    if args.now_utc:
        try:
            args.now_utc = datetime.strptime(args.now_utc, '%Y-%m-%dT%H:%M')
        except ValueError:
            raise UsageError('--now must be YYYY-MM-DDTHH:MM')
    return args


def utc_bounds(date_from, date_to):
    """[from 00:00, to+1 00:00) по UTC+5 -> UTC."""
    start = datetime.combine(date_from, datetime.min.time()) - LOCAL_OFFSET
    end = (datetime.combine(date_to + timedelta(days=1),
                            datetime.min.time()) - LOCAL_OFFSET)
    return start, end


def connect_read_only(path):
    con = sqlite3.connect('file:%s?mode=ro' % os.path.abspath(path).replace(
        '\\', '/'), uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def _pct(part, whole):
    return '%5.1f%%' % (100.0 * part / whole) if whole else '    -'


def bucket_lines(bucket, indent='  '):
    n = bucket['flights']
    s, r = bucket['states'], bucket['reasons']
    confirmed = s[fv.STATE_EXACT] + s[fv.STATE_IDENTIFIED]
    lines = [
        'flights total                    %8d' % n,
        '  with area calculation          %8d  %s' % (
            bucket['with_calculation'], _pct(bucket['with_calculation'], n)),
        '  with current field attribution %8d  %s' % (
            bucket['with_attribution'], _pct(bucket['with_attribution'], n)),
        'CONFIRMED (EXACT + IDENTIFIED)   %8d  %s' % (confirmed,
                                                     _pct(confirmed, n)),
        '  EXACT (boundary bytes saved)   %8d  %s' % (
            s[fv.STATE_EXACT], _pct(s[fv.STATE_EXACT], n)),
        '  IDENTIFIED (no boundary bytes) %8d  %s' % (
            s[fv.STATE_IDENTIFIED], _pct(s[fv.STATE_IDENTIFIED], n)),
        'PROBABLE (lineage, not counted)  %8d  %s' % (
            s[fv.STATE_PROBABLE], _pct(s[fv.STATE_PROBABLE], n)),
        'CANDIDATE (TIER4, diagnostic)    %8d  %s' % (
            s[fv.STATE_CANDIDATE], _pct(s[fv.STATE_CANDIDATE], n)),
        'AMBIGUOUS (lineage conflict)     %8d  %s' % (
            s[fv.STATE_AMBIGUOUS], _pct(s[fv.STATE_AMBIGUOUS], n)),
        'UNRESOLVED                       %8d  %s' % (
            s[fv.STATE_UNRESOLVED], _pct(s[fv.STATE_UNRESOLVED], n)),
        '  NO_CARD (card not collected)   %8d  %s' % (
            r[fv.REASON_NO_CARD], _pct(r[fv.REASON_NO_CARD], n)),
        '  NO_KEY (card has no field key) %8d  %s' % (
            r[fv.REASON_NO_KEY], _pct(r[fv.REASON_NO_KEY], n)),
        '  KEY_AFTER_RESOLUTION           %8d  %s' % (
            r[fv.REASON_KEY_AFTER_RESOLUTION],
            _pct(r[fv.REASON_KEY_AFTER_RESOLUTION], n)),
        '  NOT_IN_CATALOG                 %8d  %s' % (
            r[fv.REASON_NOT_IN_CATALOG], _pct(r[fv.REASON_NOT_IN_CATALOG], n)),
        '  UNPARSED                       %8d  %s' % (
            r[fv.REASON_UNPARSED], _pct(r[fv.REASON_UNPARSED], n)),
        '  OTHER                          %8d  %s' % (
            r[fv.REASON_OTHER], _pct(r[fv.REASON_OTHER], n)),
        'NOT_RESOLVED (no attribution)    %8d  %s' % (
            s[fv.STATE_NOT_RESOLVED], _pct(s[fv.STATE_NOT_RESOLVED], n)),
        'with historical boundary bytes   %8d  %s' % (
            bucket['with_historical_bytes'],
            _pct(bucket['with_historical_bytes'], n)),
        'with boundary md5 but no bytes   %8d  %s' % (
            bucket['md5_without_bytes'],
            _pct(bucket['md5_without_bytes'], n)),
    ]
    return [indent + line for line in lines]


def report_lines(result, args):
    lines = [
        '%s: DJI field attribution census (read-only)' % CENSUS_ID,
        'period (UTC+5): %s .. %s' % (args.date_from.isoformat(),
                                      args.date_to.isoformat()),
        'field resolver version: %s' % result['versions']['field_resolver'],
        'area algorithm version: %s' % result['versions']['area_algorithm'],
        '',
        '== period ==',
    ]
    lines.extend(bucket_lines(result['total']))
    for month in sorted(result['months']):
        lines.append('')
        lines.append('== month %s ==' % month)
        lines.extend(bucket_lines(result['months'][month]))
    cat = result['catalog']
    lines += [
        '',
        '== DJI field catalog ==',
        '  land records (uuid)            %8d' % cat['land_records'],
        '  land revisions                 %8d' % cat['land_revisions'],
        '  distinct boundary md5          %8d' % cat['boundary_md5s'],
        '  boundary bodies stored         %8d' % cat['geometry_bodies'],
        '  boundary bodies md5-verified   %8d' % cat[
            'geometry_bodies_verified'],
        '  boundary md5 without body      %8d' % cat['md5_without_body'],
        '  md5 shared by >1 record        %8d (records involved: %d)' % (
            cat['shared_md5'], cat['shared_md5_records']),
    ]
    snap = result['snapshots']
    lines += [
        '',
        '== land snapshots, last %d days (since %s UTC) ==' % (
            snap['window_days'], snap['since_utc']),
        '  snapshots                      %8d' % snap['count'],
        '  complete                       %8d' % snap['complete'],
        '  local days with a snapshot     %8d of %d' % (
            snap['days_with_snapshot'], snap['window_days']),
        '  last snapshot (UTC)            %s' % (snap['last_utc'] or '-'),
        '  last complete snapshot (UTC)   %s' % (
            snap['last_complete_utc'] or '-'),
    ]
    return lines


def ascii_safe(text):
    return text.encode('ascii', 'replace').decode('ascii')


def main(argv=None, out=None):
    out = out or sys.stdout
    try:
        args = parse_args(sys.argv[1:] if argv is None else argv)
    except UsageError as exc:
        out.write('ERROR: %s\n' % ascii_safe(str(exc)))
        return EXIT_USAGE
    if not os.path.exists(args.db):
        out.write('ERROR: database not found at %s - nothing created\n'
                  % ascii_safe(args.db))
        return EXIT_NO_DATABASE
    con = connect_read_only(args.db)
    try:
        missing = fs.missing_tables(con)
        if missing:
            out.write('ERROR: missing tables: %s (migration '
                      'DJI_AREA_EVIDENCE_001 not applied?)\n'
                      % ', '.join(missing))
            return EXIT_NO_TABLES
        start, end = utc_bounds(args.date_from, args.date_to)
        result = fs.census(con, start, end, now_utc=args.now_utc,
                           snapshot_days=args.snapshot_days)
    finally:
        con.close()
    for line in report_lines(result, args):
        out.write(ascii_safe(line) + '\n')
    if args.json_path:
        payload = {
            'census_id': CENSUS_ID,
            'period_local': [args.date_from.isoformat(),
                             args.date_to.isoformat()],
            'period_utc': [start.isoformat(sep=' '), end.isoformat(sep=' ')],
            'result': result,
        }
        with open(args.json_path, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle, ensure_ascii=True, indent=2,
                      sort_keys=True)
        out.write('json written: %s\n' % ascii_safe(args.json_path))
    return EXIT_OK


if __name__ == '__main__':
    sys.exit(main())
