# -*- coding: utf-8 -*-
"""tools/dji_area_backfill.py -- исторический прогон контроля площади DJI по окнам.

DRONE-AREA-CONTROL-V2-MEGA, блок F. Идёт от `--from` к `--to` окнами по дню
(или по N дней, но НИКОГДА через границу месяца) и в каждом окне исполняет
ТОТ ЖЕ ежедневный цикл `tools/dji_area_daily.py` -- FLIGHTS -> MANIFEST ->
SOURCES (-> VERIFY) -> RECALC, -- под той же блокировкой цикла. Второго
алгоритма здесь нет: этот файл только нарезает окна, помнит, какие из них
закончены, и складывает итог.

ЧТО ОН НЕ ДЕЛАЕТ. Не пишет ни `drone_flights`, ни одной таблицы `dji_*`
сам: всё, что попадает в базу, приходит через шаги цикла (приёмник вылетов,
приёмник источников, пересчёт -- append-only). RAW (`drone_flights.area_ha`)
не читает и не пишет. Журнал прогонов (`drone_area_cycle_runs`) не трогает:
«последнее обновление данных DJI» на экране -- про свежий день, а не про март.

ЗАКОНЧЕННОЕ НЕ ТРОГАЕТ. Окно со статусом DONE при повторном запуске
пропускается. Единственная причина пройти его снова без `--force` -- смена
версии алгоритма площади (`dji_area.AREA_ALGORITHM_VERSION`): расчёты окна
посчитаны старым кодом.

ЧЕСТНЫЙ ИТОГ. Если DJI больше не хранит V4 старого вылета, запись остаётся
по RAW со статусом «недостаточно доказательств» (INSUFFICIENT_EVIDENCE), а не
превращается в ноль. Такие записи, записи в ожидании доказательства и
записи «требует проверки» перечисляются поимённо в
`backfill_unresolved.csv` -- это список для ручной проверки.

Запуск (на хосте приложения, рабочий каталог -- корень репозитория; сначала
план, потом порциями):

  & "C:\\Program Files\\Python314\\python.exe" tools\\dji_area_backfill.py --from 2026-03-01 --to 2026-09-23 --plan
  & "C:\\Program Files\\Python314\\python.exe" tools\\dji_area_backfill.py --db instance\\transport.db --from 2026-03-01 --to 2026-09-23 --max-windows 7

Тот же запуск без `--max-windows` идёт до конца; прерванный (Ctrl+C,
перезагрузка, код 7/8) продолжается той же командой с тем же `--from`/`--to`.
`--to` лучше задавать явно (дата перехода на production-цикл): без него это
«сегодня», и завтрашнее продолжение не совпадёт с контрольной точкой.

  --window-days N        -- длина окна 1..7 дней (по умолчанию 1)
  --checkpoint PATH      -- контрольная точка (по умолчанию
                            drone_collector\\data\\area_backfill\\checkpoint.json)
  --plan / --dry-run     -- показать окна и их статус; ничего не запускать и
                            не писать, база не нужна
  --max-windows N        -- остановиться после N выполненных окон (код 8)
  --max-attempts N       -- после N неудач окно получает GAVE_UP (по умолчанию 3)
  --retry-gave-up        -- дать окнам GAVE_UP ещё одну попытку
  --force                -- пройти и законченные окна
  --lock-wait SECONDS    -- ждать занятую блокировку цикла (по умолчанию 0)
  --stop-above N         -- порог манифеста на окно (по умолчанию 60 x дней)
  --collector-python PATH, --app-python PATH -- как у ежедневного цикла

Артефакты рядом с контрольной точкой: `checkpoint.json` (переписывается
атомарно после КАЖДОГО окна), `backfill_summary.json` (по месяцам: окна
всего/готово/ошибка/сдались/ожидают; записи в ожидании/на проверку/без
доказательств), `backfill_unresolved.csv` (month, window, flight_id, class,
reason) и рабочий каталог цикла `work\\`.

Коды возврата: 0 все окна DONE; 1 ошибка аргументов или контрольная точка
другого диапазона; 2 база не найдена (ничего не создано); 3 проход закончен,
но есть окна FAILED/GAVE_UP; 7 остановлено: блокировку цикла (или эту
контрольную точку) держит другой процесс -- продолжить позже; 8 остановлено
по `--max-windows`, окна ещё остались. Вывод в консоль только ASCII.
"""

import argparse
import csv
import io
import json
import os
import sqlite3
import sys
import traceback
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import dji_area  # noqa: E402
from dji_area import accounting as acc  # noqa: E402
from drone_collector import runlock  # noqa: E402
from tools import dji_area_daily as daily  # noqa: E402

CHECKPOINT_VERSION = 1
DEFAULT_FROM = '2026-03-01'
DEFAULT_WINDOW_DAYS = 1
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_CHECKPOINT = os.path.join(ROOT, 'drone_collector', 'data',
                                  'area_backfill', 'checkpoint.json')
SUMMARY_FILE = 'backfill_summary.json'
UNRESOLVED_FILE = 'backfill_unresolved.csv'
WORK_DIR_NAME = 'work'
LOCK_PURPOSE = 'dji-area-backfill'
# [REASON]: порог манифеста -- на окно, а не на сутки. Исторический день ещё
# не посещался вовсе, и его манифест -- все кандидаты без V4 плюс контроль;
# суточный порог 50 рассчитан на установившийся режим, где непосещён только
# свежий день.
STOP_ABOVE_PER_DAY = 60

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_NO_DATABASE = 2
EXIT_INCOMPLETE = 3
EXIT_BUSY = 7
EXIT_STOPPED = 8

STATUS_DONE = 'DONE'
STATUS_FAILED = 'FAILED'
STATUS_GAVE_UP = 'GAVE_UP'
STATUS_BUSY = 'BUSY'
STATUS_PENDING = 'PENDING'  # только для показа: в контрольную точку не пишется

CLASS_PENDING = 'PENDING_EVIDENCE'
CLASS_REVIEW = 'REVIEW'
CLASS_INSUFFICIENT = 'INSUFFICIENT_EVIDENCE'
NO_V4_AT_SOURCE_FLAG = 'NO_V4_AT_SOURCE'


class Refusal(Exception):

    def __init__(self, code, message):
        Exception.__init__(self, message)
        self.code = code
        self.message = message


def say(line):
    print(line, flush=True)


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def algorithm_version():
    return dji_area.AREA_ALGORITHM_VERSION


# ─── Окна ────────────────────────────────────────────────────────────────────

def _month_end(day):
    first_next = (day.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first_next - timedelta(days=1)


def build_windows(date_from, date_to, window_days):
    """Подряд, от старого к новому, не длиннее N дней и НЕ через месяц.

    [REASON]: граница месяца -- граница отчёта и граница свидетельства
    канала (оно собирается по полному календарному месяцу). Окно, которое её
    пересекает, путало бы помесячный итог и помесячное продолжение.
    """
    windows = []
    start = date_from
    while start <= date_to:
        end = min(start + timedelta(days=window_days - 1), date_to,
                  _month_end(start))
        windows.append((start, end))
        start = end + timedelta(days=1)
    return windows


def window_key(start, end):
    return '%s..%s' % (start.isoformat(), end.isoformat())


def month_of(key):
    return key[:7]


# ─── Контрольная точка ───────────────────────────────────────────────────────

def new_checkpoint(date_from, date_to, window_days):
    return {'version': CHECKPOINT_VERSION,
            'range': {'from': date_from.isoformat(),
                      'to': date_to.isoformat()},
            'window_days': window_days,
            'windows': {}}


def load_checkpoint(path):
    """Словарь либо None (файла нет). `Refusal`, если он нечитаем."""
    if not os.path.exists(path):
        return None
    try:
        with io.open(path, encoding='utf-8') as handle:
            document = json.load(handle)
    except (IOError, OSError, ValueError) as exc:
        raise Refusal(EXIT_USAGE, 'the checkpoint %s cannot be read (%s); '
                      'it was left as it is - move it aside to start over'
                      % (path, type(exc).__name__))
    if not isinstance(document, dict) \
            or document.get('version') != CHECKPOINT_VERSION \
            or not isinstance(document.get('windows'), dict):
        raise Refusal(EXIT_USAGE, 'the checkpoint %s is not a version %d '
                      'backfill checkpoint; it was left as it is'
                      % (path, CHECKPOINT_VERSION))
    return document


def check_matches(checkpoint, date_from, date_to, window_days, path):
    """[REASON]: чужой диапазон -- отказ, а не молчаливое смешение. Окна
    другой длины или другого конца не совпадают ключами, и «продолжение»
    тихо прошло бы весь период заново либо пропустило бы его часть."""
    wanted = {'from': date_from.isoformat(), 'to': date_to.isoformat()}
    if checkpoint.get('range') == wanted \
            and checkpoint.get('window_days') == window_days:
        return
    have = checkpoint.get('range') or {}
    raise Refusal(EXIT_USAGE,
                  'the checkpoint %s belongs to --from %s --to %s '
                  '--window-days %s, not to --from %s --to %s --window-days '
                  '%d. Resume with its own range, or give another '
                  '--checkpoint to start a new backfill.'
                  % (path, have.get('from'), have.get('to'),
                     checkpoint.get('window_days'), wanted['from'],
                     wanted['to'], window_days))


def save_checkpoint(path, checkpoint):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    tmp = path + '.tmp'
    with io.open(tmp, 'w', encoding='utf-8', newline='\n') as handle:
        json.dump(checkpoint, handle, ensure_ascii=True, indent=1,
                  sort_keys=True)
    os.replace(tmp, path)


def decide(entry, force, retry_gave_up, version):
    """(run?, почему) для окна с записью ``entry`` (None -- не начиналось)."""
    if entry is None:
        return True, 'pending'
    status = entry.get('status')
    if status == STATUS_DONE:
        if force:
            return True, 'forced'
        if entry.get('area_algorithm_version') != version:
            return True, 'algorithm changed'
        return False, 'done'
    if status == STATUS_GAVE_UP:
        if retry_gave_up:
            return True, 'retry after giving up'
        return False, 'gave up (--retry-gave-up to try again)'
    if status == STATUS_BUSY:
        return True, 'was busy'
    return True, 'retry after failure'


def shown_status(entry, version):
    if entry is None:
        return STATUS_PENDING
    if entry.get('status') == STATUS_DONE \
            and entry.get('area_algorithm_version') != version:
        return STATUS_PENDING
    if entry.get('status') == STATUS_BUSY:
        return STATUS_PENDING
    return entry.get('status') or STATUS_PENDING


# ─── Что осталось нерешённым в окне ─────────────────────────────────────────

def _flags(row):
    try:
        flags = json.loads(row.get('anomaly_flags_json') or '[]')
    except ValueError:
        flags = []
    return [str(flag) for flag in flags or []]


def read_unresolved(db_path, start, end, version):
    """Нерешённые записи окна по текущим расчётам. Только чтение (mode=ro).

    PHANTOM_STRUCTURAL без V4 -- ожидает доказательства; тот же класс, но DJI
    V4 не хранит (`NO_V4_AT_SOURCE`) -- недостаточно доказательств, запись
    остаётся по RAW; REVIEW -- требует решения человека. Остальные классы
    решены автоматически и в список не попадают.
    """
    out = {'pending': None, 'review': None, 'no_v4_at_source': None,
           'flight_ids': [], 'records': []}
    uri = 'file:%s?mode=ro' % os.path.abspath(db_path).replace(
        '\\', '/').replace('?', '%3f').replace('#', '%23')
    try:
        con = sqlite3.connect(uri, uri=True, timeout=30)
    except sqlite3.Error as exc:
        out['error'] = type(exc).__name__
        return out
    con.row_factory = sqlite3.Row
    try:
        rows = [dict(row) for row in con.execute(
            'SELECT * FROM dji_area_calculations WHERE superseded_at IS NULL '
            'AND area_algorithm_version = ? AND report_start_date BETWEEN ? '
            'AND ? ORDER BY report_start_date, flight_id',
            (version, start.isoformat(), end.isoformat()))]
    except sqlite3.Error as exc:
        out['error'] = type(exc).__name__
        return out
    finally:
        con.close()
    counts = {CLASS_PENDING: 0, CLASS_REVIEW: 0, CLASS_INSUFFICIENT: 0}
    for row in rows:
        decision = acc.classify(row)
        cls = decision['accounting_class']
        if cls == acc.REVIEW:
            kind, reason = CLASS_REVIEW, decision['reason']
        elif cls == acc.PHANTOM_STRUCTURAL:
            if NO_V4_AT_SOURCE_FLAG in _flags(row):
                kind, reason = CLASS_INSUFFICIENT, NO_V4_AT_SOURCE_FLAG
            else:
                kind, reason = CLASS_PENDING, decision['reason']
        else:
            continue
        counts[kind] += 1
        out['records'].append({'flight_id': int(row['flight_id']),
                               'class': kind, 'reason': reason})
    out['pending'] = counts[CLASS_PENDING]
    out['review'] = counts[CLASS_REVIEW]
    out['no_v4_at_source'] = counts[CLASS_INSUFFICIENT]
    out['flight_ids'] = [r['flight_id'] for r in out['records']]
    return out


# ─── Итог ───────────────────────────────────────────────────────────────────

def _empty_month():
    return {'windows_total': 0, 'done': 0, 'failed': 0, 'gave_up': 0,
            'pending': 0, 'records_pending': 0, 'records_review': 0,
            'records_insufficient': 0}


def build_summary(checkpoint, windows, version, checkpoint_path):
    months = {}
    for start, end in windows:
        key = window_key(start, end)
        bucket = months.setdefault(month_of(key), _empty_month())
        entry = checkpoint['windows'].get(key)
        status = shown_status(entry, version)
        bucket['windows_total'] += 1
        bucket[{STATUS_DONE: 'done', STATUS_FAILED: 'failed',
                STATUS_GAVE_UP: 'gave_up'}.get(status, 'pending')] += 1
        if entry and entry.get('area_algorithm_version') == version:
            unresolved = entry.get('unresolved') or {}
            for field, name in (('records_pending', 'pending'),
                                ('records_review', 'review'),
                                ('records_insufficient', 'no_v4_at_source')):
                bucket[field] += unresolved.get(name) or 0
    totals = _empty_month()
    for bucket in months.values():
        for field in totals:
            totals[field] += bucket[field]
    return {'version': CHECKPOINT_VERSION,
            'generated_at_utc': utcnow().isoformat(),
            'range': dict(checkpoint['range']),
            'window_days': checkpoint['window_days'],
            'area_algorithm_version': version,
            'checkpoint': os.path.abspath(checkpoint_path),
            'months': months,
            'totals': totals}


def unresolved_rows(checkpoint, windows, version):
    """(month, window, flight_id, class, reason) -- список ручной проверки."""
    rows = []
    for start, end in windows:
        key = window_key(start, end)
        entry = checkpoint['windows'].get(key)
        if not entry:
            continue
        month = month_of(key)
        if entry.get('status') in (STATUS_FAILED, STATUS_GAVE_UP):
            rows.append((month, key, '', 'WINDOW_' + entry['status'],
                         '%s exit %s' % (entry.get('failure')
                                         or entry.get('outcome'),
                                         entry.get('last_exit'))))
        if entry.get('area_algorithm_version') != version:
            continue
        for record in (entry.get('unresolved') or {}).get('records') or []:
            rows.append((month, key, record['flight_id'], record['class'],
                         record['reason']))
    return rows


def write_reports(checkpoint, windows, version, checkpoint_path, out):
    directory = os.path.dirname(os.path.abspath(checkpoint_path))
    summary = build_summary(checkpoint, windows, version, checkpoint_path)
    os.makedirs(directory, exist_ok=True)
    summary_path = os.path.join(directory, SUMMARY_FILE)
    csv_path = os.path.join(directory, UNRESOLVED_FILE)
    daily.write_json_atomic(summary_path, summary)
    tmp = csv_path + '.tmp'
    with io.open(tmp, 'w', encoding='utf-8', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(('month', 'window', 'flight_id', 'class', 'reason'))
        writer.writerows(unresolved_rows(checkpoint, windows, version))
    os.replace(tmp, csv_path)
    out('BACKFILL SUMMARY %s .. %s (window %d day(s), algorithm %s)'
        % (summary['range']['from'], summary['range']['to'],
           summary['window_days'], version))
    for month in sorted(summary['months']):
        out('  ' + _summary_line(month, summary['months'][month]))
    out('  ' + _summary_line('total  ', summary['totals']))
    out('  written: %s' % daily.ascii_line(summary_path))
    out('  written: %s' % daily.ascii_line(csv_path))
    return summary


def _summary_line(label, bucket):
    return ('%s  windows %d: done %d failed %d gave_up %d pending %d | '
            'records pending %d review %d insufficient %d'
            % (label, bucket['windows_total'], bucket['done'],
               bucket['failed'], bucket['gave_up'], bucket['pending'],
               bucket['records_pending'], bucket['records_review'],
               bucket['records_insufficient']))


# ─── Прогон ──────────────────────────────────────────────────────────────────

def cycle_args(args, start, end, work_dir):
    """Тот же разбор командной строки, что и у ежедневного цикла."""
    argv = ['--db', args.db_path, '--from', start.isoformat(), '--to',
            end.isoformat(), '--work-dir', work_dir, '--flights-kind',
            'backfill', '--stop-above', str(args.stop_above)]
    if args.collector_python:
        argv += ['--collector-python', args.collector_python]
    if args.app_python:
        argv += ['--app-python', args.app_python]
    return daily.build_parser().parse_args(argv)


def build_parser():
    parser = argparse.ArgumentParser(
        description='Historical DJI area backfill: the daily cycle, window '
                    'by window, with a resumable checkpoint.')
    parser.add_argument('--db', dest='db_path')
    parser.add_argument('--from', dest='date_from', default=DEFAULT_FROM,
                        metavar='YYYY-MM-DD')
    parser.add_argument('--to', dest='date_to', metavar='YYYY-MM-DD')
    parser.add_argument('--window-days', dest='window_days', type=int,
                        default=DEFAULT_WINDOW_DAYS)
    parser.add_argument('--checkpoint', default=DEFAULT_CHECKPOINT)
    parser.add_argument('--plan', '--dry-run', dest='plan',
                        action='store_true',
                        help='print the windows and their checkpoint status; '
                             'run and write nothing')
    parser.add_argument('--max-windows', dest='max_windows', type=int)
    parser.add_argument('--max-attempts', dest='max_attempts', type=int,
                        default=DEFAULT_MAX_ATTEMPTS)
    parser.add_argument('--retry-gave-up', dest='retry_gave_up',
                        action='store_true')
    parser.add_argument('--force', action='store_true',
                        help='run DONE windows again as well')
    parser.add_argument('--collector-python', dest='collector_python')
    parser.add_argument('--app-python', dest='app_python')
    parser.add_argument('--stop-above', dest='stop_above', type=int)
    parser.add_argument('--lock-wait', dest='lock_wait', type=float,
                        default=0.0)
    return parser


def resolve(args, today):
    """(date_from, date_to) после всех проверок командной строки."""
    try:
        date_from = date.fromisoformat(args.date_from)
        date_to = date.fromisoformat(args.date_to) if args.date_to \
            else today
    except ValueError as exc:
        raise Refusal(EXIT_USAGE, 'bad date: %s' % exc)
    if date_from > date_to:
        raise Refusal(EXIT_USAGE, '--from %s is after --to %s'
                      % (date_from, date_to))
    if date_to > today:
        raise Refusal(EXIT_USAGE, '--to %s is in the future (today is %s '
                      'UTC+5); the backfill covers days that have happened'
                      % (date_to, today))
    if not 1 <= args.window_days <= daily.MAX_DAYS:
        raise Refusal(EXIT_USAGE, '--window-days must be 1..%d'
                      % daily.MAX_DAYS)
    if args.max_attempts < 1:
        raise Refusal(EXIT_USAGE, '--max-attempts must be at least 1')
    if args.max_windows is not None and args.max_windows < 1:
        raise Refusal(EXIT_USAGE, '--max-windows must be at least 1')
    if not 0 <= args.lock_wait < float('inf'):
        raise Refusal(EXIT_USAGE, '--lock-wait must be a finite number of '
                      'seconds, not negative')
    if args.stop_above is None:
        args.stop_above = STOP_ABOVE_PER_DAY * args.window_days
    if args.stop_above < 1:
        raise Refusal(EXIT_USAGE, '--stop-above must be positive')
    return date_from, date_to


def print_plan(windows, checkpoint, args, version, out):
    out('BACKFILL PLAN %s .. %s: %d window(s) of up to %d day(s); algorithm '
        '%s; nothing is run, nothing is written'
        % (windows[0][0], windows[-1][1], len(windows), args.window_days,
           version))
    to_run = 0
    for start, end in windows:
        key = window_key(start, end)
        entry = (checkpoint or {}).get('windows', {}).get(key)
        run, why = decide(entry, args.force, args.retry_gave_up, version)
        to_run += 1 if run else 0
        out('  %s  %-8s attempts=%s  %s (%s)'
            % (key, entry.get('status') if entry else STATUS_PENDING,
               entry.get('attempts', 0) if entry else 0,
               'run' if run else 'skip', why))
    out('  %d window(s) would run%s'
        % (to_run, '; this invocation stops after %d (--max-windows)'
           % args.max_windows if args.max_windows
           and args.max_windows < to_run else ''))
    return EXIT_OK


def main(argv=None, runner=daily.run_command, out=say, today=None):
    args = build_parser().parse_args(argv)
    today = today or daily.report_today()
    version = algorithm_version()
    checkpoint_path = os.path.abspath(args.checkpoint)
    try:
        date_from, date_to = resolve(args, today)
        windows = build_windows(date_from, date_to, args.window_days)
        checkpoint = load_checkpoint(checkpoint_path)
        if checkpoint is not None:
            check_matches(checkpoint, date_from, date_to, args.window_days,
                          checkpoint_path)
        if args.plan:
            return print_plan(windows, checkpoint, args, version, out)
        if not args.db_path:
            raise Refusal(EXIT_USAGE, '--db is required (only --plan runs '
                          'without a database)')
        if not os.path.exists(args.db_path):
            raise Refusal(EXIT_NO_DATABASE, 'database not found at %s - '
                          'refusing to run; nothing was created'
                          % args.db_path)
    except Refusal as exc:
        out('ERROR: %s' % daily.ascii_line(exc.message))
        return exc.code

    # [REASON]: вторая копия бэкфилла на той же контрольной точке -- это
    # два писателя одного файла: каждый переписывал бы прогресс другого
    # своим устаревшим видом. Отдельная блокировка на весь прогон; блокировка
    # цикла берётся на каждое окно отдельно.
    guard = runlock.RunLock(checkpoint_path + '.lock',
                            purpose=LOCK_PURPOSE + '-checkpoint')
    if not guard.acquire(wait_s=0):
        out('BUSY: another backfill holds the checkpoint %s (%s). Exit %d.'
            % (daily.ascii_line(checkpoint_path),
               daily.describe_owner(guard.path), EXIT_BUSY))
        return EXIT_BUSY
    try:
        return _run(args, windows, checkpoint, checkpoint_path, version,
                    runner, out, today)
    finally:
        guard.release()


def _run(args, windows, checkpoint, checkpoint_path, version, runner, out,
         today):
    if checkpoint is None:
        checkpoint = new_checkpoint(windows[0][0], windows[-1][1],
                                    args.window_days)
    work_dir = os.path.join(os.path.dirname(checkpoint_path), WORK_DIR_NAME)
    lock_path = runlock.cycle_lock_path(args.db_path)
    executed = 0
    stopped = None
    out('BACKFILL %s .. %s: %d window(s); checkpoint %s'
        % (windows[0][0], windows[-1][1], len(windows),
           daily.ascii_line(checkpoint_path)))
    for index, (start, end) in enumerate(windows):
        key = window_key(start, end)
        entry = checkpoint['windows'].get(key)
        run, why = decide(entry, args.force, args.retry_gave_up, version)
        if not run:
            continue
        if args.max_windows is not None and executed >= args.max_windows:
            stopped = EXIT_STOPPED
            break
        entry = dict(entry or {})
        if entry.get('status') == STATUS_DONE:
            # Законченное окно проходится заново -- счёт неудач с нуля.
            entry['attempts'] = 0
        lock = runlock.RunLock(lock_path, purpose=LOCK_PURPOSE)
        if not daily.take_cycle_lock(lock, args.lock_wait, out):
            entry.update(status=STATUS_BUSY, last_exit=EXIT_BUSY,
                         outcome=daily.OUTCOME_BUSY,
                         failure=daily.FAILURE_CYCLE_BUSY,
                         finished_at_utc=utcnow().isoformat())
            entry.setdefault('attempts', 0)
            checkpoint['windows'][key] = entry
            save_checkpoint(checkpoint_path, checkpoint)
            out('BUSY: %s. The backfill stopped at %s; run the same command '
                'later to resume. Exit %d.'
                % (daily.busy_message(lock_path), key, EXIT_BUSY))
            stopped = EXIT_BUSY
            break
        attempt = entry.get('attempts', 0) + 1
        out('WINDOW %s (month %s, %d of %d) attempt %d: %s'
            % (key, month_of(key), index + 1, len(windows), attempt, why))
        try:
            try:
                code, result = daily.run_cycle(
                    cycle_args(args, start, end, work_dir), runner=runner,
                    today=today, out=out)
            except Exception as exc:
                for line in daily.redact(traceback.format_exc()).splitlines():
                    out('  ' + line)
                code = daily.EXIT_STEP_FAILED
                result = daily.new_result(start, end)
                result.update(outcome=daily.OUTCOME_FAILED, exit_code=code,
                              failure=daily.FAILURE_UNEXPECTED)
                out('ERROR: %s' % daily.redact('%s: %s'
                                               % (type(exc).__name__, exc)))
        finally:
            lock.release()
        executed += 1
        if code in (daily.EXIT_USAGE, daily.EXIT_NO_DATABASE):
            # Не вина окна: база исчезла либо сломан сам вызов. Дальше идти
            # бессмысленно, контрольная точка окна не трогается.
            out('STOP: the daily cycle refused to start (exit %d)' % code)
            stopped = code
            break
        done = result.get('outcome') in (daily.OUTCOME_SUCCESS,
                                         daily.OUTCOME_WARNINGS)
        if done:
            status = STATUS_DONE
        elif attempt >= args.max_attempts:
            status = STATUS_GAVE_UP
        else:
            status = STATUS_FAILED
        entry.update(
            status=status, attempts=attempt, last_exit=code,
            outcome=result.get('outcome'), failure=result.get('failure'),
            warnings=result.get('warnings') or [],
            finished_at_utc=utcnow().isoformat(),
            area_algorithm_version=version,
            manifest=result.get('manifest'),
            evidence_misses=result.get('evidence_misses'),
            unresolved=read_unresolved(args.db_path, start, end, version))
        checkpoint['windows'][key] = entry
        save_checkpoint(checkpoint_path, checkpoint)
        unresolved = entry['unresolved']
        out('WINDOW %s %s exit %d outcome %s | pending %s review %s '
            'insufficient %s'
            % (key, status, code, entry['outcome'], unresolved['pending'],
               unresolved['review'], unresolved['no_v4_at_source']))

    if stopped == EXIT_STOPPED:
        out('STOPPED: --max-windows %d reached; run the same command to '
            'continue' % args.max_windows)
    write_reports(checkpoint, windows, version, checkpoint_path, out)
    if stopped is not None:
        return stopped
    incomplete = [key for key in (window_key(s, e) for s, e in windows)
                  if shown_status(checkpoint['windows'].get(key), version)
                  != STATUS_DONE]
    if incomplete:
        out('INCOMPLETE: %d window(s) are not DONE: %s'
            % (len(incomplete), ', '.join(incomplete[:10])
               + (' ...' if len(incomplete) > 10 else '')))
        return EXIT_INCOMPLETE
    out('ALL DONE: every window of the range is DONE')
    return EXIT_OK


if __name__ == '__main__':
    sys.exit(main())
