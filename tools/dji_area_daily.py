# -*- coding: utf-8 -*-
"""tools/dji_area_daily.py -- ежедневный цикл контроля площади DJI одной командой.

DJI-AREA-PRODUCTIONIZATION-001. Один вход вместо семи несвязанных команд:

  1. FLIGHTS   сборщик обходит свежие вылеты и кладёт их списочное
               доказательство (обращение к DJI);
  2. MANIFEST  Vehicle Soft отвечает, кому нужен адресный V4: все кандидаты
               экрана без V4 плюс малая контрольная выборка (DJI не трогается);
  3. SOURCES   сборщик забирает источники ТОЛЬКО этих вылетов и шлёт их
               приёмнику (обращение к DJI; пропускается, если манифест пуст);
  4. RECALC    пересчёт того же скользящего окна поверх сохранённых улик.

Порядок определяется зависимостями, а не привычкой: кандидата называет
замороженный экран по СПИСОЧНЫМ скалярам, поэтому манифесту нужен только шаг
1, а предварительный пересчёт не нужен. Пересчёт стоит последним, потому что
именно он превращает привезённый V4 в исправленную площадь.

Окно -- три отчётных дня UTC+5: цепочка может пересечь полночь, доказательство
приходит с задержкой, и завтрашний прогон обязан лечить вчерашнюю запись.

ДВЕ ТОПОЛОГИИ. На production сборщик и база стоят на одном сервере, и цикл
идёт одной командой целиком. На площадке сборщик живёт на рабочей машине (там
сохранена сессия DJI), а база -- на сервере: рабочая машина выполняет шаги 1-3
с `--skip-recalc`, сервер -- шаг 4 с `--recalc-only`. Окно в обеих половинах
считает один и тот же код, поэтому даты в PowerShell никто не вычисляет.

Повторный запуск безопасен. Уже захваченный вылет манифест больше не называет,
`--sources` уже поставленное в очередь не открывает, пересчёт на том же входе
отвечает `unchanged`. RAW не переписывается: `drone_flights.area_ha` этот цикл
не читает и не пишет.

Запуск (на хосте приложения, рабочий каталог -- корень репозитория):

  & "C:\\Program Files\\Python314\\python.exe" tools\\dji_area_daily.py --db instance\\transport.db

  --days 3                 -- длина окна (по умолчанию 3)
  --from D --to D          -- явное окно вместо скользящего
  --no-dji                 -- шаги 1 и 3 пропустить: только манифест и пересчёт
  --skip-recalc            -- шаг 4 пропустить: сборщик стоит НЕ на хосте базы
  --recalc-only            -- только шаг 4: хост базы, на котором сборщика нет
  --stop-above 50          -- не идти в DJI, если манифест назвал больше
  --expect-unchanged       -- код 6, если пересчёт что-то записал
  --collector-python PATH  -- интерпретатор с Playwright, если он отдельный

Коды возврата: 0 цикл выполнен; 1 ошибка аргументов; 2 база не найдена;
3 шаг упал -- цикл остановлен; 4 манифест больше `--stop-above` -- цикл
остановлен ДО обращения к DJI; 5 источники собраны не полностью (код 18
сборщика) -- пересчёт выполнен, следующий прогон доберёт; 6 пересчёт записал
строки при `--expect-unchanged`. Вывод в консоль только ASCII.
"""

import argparse
import io
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REPORT_UTC_OFFSET_HOURS = 5
DEFAULT_DAYS = 3
MAX_DAYS = 7
DEFAULT_STOP_ABOVE = 50

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_NO_DATABASE = 2
EXIT_STEP_FAILED = 3
EXIT_MANIFEST_TOO_LARGE = 4
EXIT_SOURCES_INCOMPLETE = 5
EXIT_NOT_IDEMPOTENT = 6

COLLECTOR_SOURCES_INCOMPLETE = 18
COLLECTOR_MANIFEST_TOO_LARGE = 22

STEP_FLIGHTS = 'FLIGHTS'
STEP_MANIFEST = 'MANIFEST'
STEP_SOURCES = 'SOURCES'
STEP_RECALC = 'RECALC'


def report_today(now=None):
    now = now or datetime.now(timezone.utc)
    return (now + timedelta(hours=REPORT_UTC_OFFSET_HOURS)).date()


def resolve_window(args, today):
    if (args.date_from is None) != (args.date_to is None):
        raise ValueError('--from and --to are given together or not at all')
    if args.date_from:
        date_from = date.fromisoformat(args.date_from)
        date_to = date.fromisoformat(args.date_to)
    else:
        if args.days < 1:
            raise ValueError('--days must be positive')
        date_to = today
        date_from = today - timedelta(days=args.days - 1)
    if date_from > date_to:
        raise ValueError('--from is after --to')
    if (date_to - date_from).days + 1 > MAX_DAYS:
        raise ValueError('the window is longer than %d days' % MAX_DAYS)
    return date_from, date_to


def say(line):
    # [REASON]: со сбросом. Шаги пишут в тот же поток напрямую, и в файле
    # журнала планировщика (stdout -- не консоль, буфер блочный) заголовок
    # цикла иначе оказывался ПОСЛЕ вывода шага, который он объявляет.
    print(line, flush=True)


def run_command(command, cwd):
    """Исполнить шаг; вернуть код возврата. Вывод шага идёт прямо в консоль."""
    return subprocess.call(command, cwd=cwd)


def plan(args, date_from, date_to, work_dir):
    """Команды цикла по шагам. Чистая функция: её и проверяют тесты."""
    collector = args.collector_python or sys.executable
    app_python = args.app_python or sys.executable
    ids_file = os.path.join(work_dir, 'area_ids.txt')
    manifest_json = os.path.join(work_dir, 'area_manifest.json')
    recalc_json = os.path.join(work_dir, 'area_recalc.json')
    # [REASON]: обход вылетов берётся на сутки шире окна с обеих сторон.
    # Отчётный день считается по UTC+5, а кабинет отдаёт вылеты со своей
    # границей суток; без запаса запись у полуночи выпала бы из окна.
    walk_from = (date_from - timedelta(days=1)).isoformat()
    walk_to = min(date_to + timedelta(days=1), report_today()).isoformat()
    recalc_step = (STEP_RECALC, [
        app_python, os.path.join('tools', 'dji_area_recalc.py'), '--db',
        args.db_path, '--from', date_from.isoformat(), '--to',
        date_to.isoformat(), '--apply', '--json', recalc_json])
    paths = {'ids_file': ids_file, 'manifest_json': manifest_json,
             'recalc_json': recalc_json}
    if args.recalc_only:
        return [recalc_step], paths
    steps = []
    if not args.no_dji:
        steps.append((STEP_FLIGHTS, [
            collector, '-m', 'drone_collector.main', '--from', walk_from,
            '--to', walk_to, '--kind', 'incremental']))
    steps.append((STEP_MANIFEST, [
        collector, '-m', 'drone_collector.area_manifest', '--out', ids_file,
        '--summary', manifest_json, '--from', date_from.isoformat(), '--to',
        date_to.isoformat(), '--stop-above', str(args.stop_above)]))
    if not args.no_dji:
        steps.append((STEP_SOURCES, [
            collector, '-m', 'drone_collector.main', '--sources',
            '--ids-file', ids_file, '--send-sources']))
    if not args.skip_recalc:
        steps.append(recalc_step)
    return steps, paths


def _read_json(path):
    try:
        with io.open(path, encoding='utf-8') as handle:
            return json.load(handle)
    except (IOError, OSError, ValueError):
        return None


def _manifest_ids(path):
    document = _read_json(path) or {}
    capture = document.get('capture')
    return len(capture) if isinstance(capture, list) else None


def execute(args, runner=run_command, today=None, out=say):
    today = today or report_today()
    try:
        date_from, date_to = resolve_window(args, today)
    except ValueError as exc:
        out('ERROR: %s' % exc)
        return EXIT_USAGE
    modes = [flag for flag, on in (('--no-dji', args.no_dji),
                                   ('--skip-recalc', args.skip_recalc),
                                   ('--recalc-only', args.recalc_only)) if on]
    if len(modes) > 1:
        # [REASON]: каждая пара либо противоречива, либо оставляет от цикла
        # один манифест. Молча выбрать одно из двух значило бы исполнить не
        # то, что просили.
        out('ERROR: %s are mutually exclusive' % ' and '.join(modes))
        return EXIT_USAGE
    if not args.skip_recalc:
        if not args.db_path:
            out('ERROR: --db is required unless --skip-recalc is given')
            return EXIT_USAGE
        if not os.path.exists(args.db_path):
            # [REASON]: пересчёт по отсутствующей базе создал бы пустой файл и
            # отчитался нулём записей -- «всё хорошо» на месте «ничего нет».
            out('ERROR: database not found at %s - refusing to run.'
                % args.db_path)
            return EXIT_NO_DATABASE

    work_dir = os.path.abspath(args.work_dir)
    os.makedirs(work_dir, exist_ok=True)
    steps, paths = plan(args, date_from, date_to, work_dir)
    # Вчерашний манифест не должен пережить сегодняшний отказ шага 2.
    if any(name == STEP_MANIFEST for name, _command in steps):
        for stale in (paths['ids_file'], paths['manifest_json']):
            if os.path.exists(stale):
                os.remove(stale)

    out('DJI AREA DAILY CYCLE %s .. %s%s'
        % (date_from, date_to, ' (%s)' % modes[0].lstrip('-') if modes
           else ''))
    result = EXIT_OK
    ids = None
    for name, command in steps:
        if name == STEP_SOURCES and ids == 0:
            out('  %-9s skipped: the manifest names no flight' % name)
            continue
        code = runner(command, ROOT)
        out('  %-9s exit %d' % (name, code))
        if name == STEP_MANIFEST:
            ids = _manifest_ids(paths['manifest_json'])
            summary = _read_json(paths['manifest_json']) or {}
            counts = summary.get('counts') or {}
            out('  manifest  ids=%s candidates=%s controls=%s '
                'no_v4_at_source=%s'
                % (ids, counts.get('candidates_need_capture'),
                   (counts.get('capture_total') or 0)
                   - (counts.get('candidates_need_capture') or 0),
                   counts.get('candidates_no_v4_at_source')))
            if code == COLLECTOR_MANIFEST_TOO_LARGE:
                out('STOP: the manifest is larger than --stop-above %d; DJI '
                    'was NOT contacted for sources.' % args.stop_above)
                return EXIT_MANIFEST_TOO_LARGE
        if name == STEP_SOURCES and code == COLLECTOR_SOURCES_INCOMPLETE:
            out('  sources are incomplete; the next run visits only what is '
                'missing')
            result = EXIT_SOURCES_INCOMPLETE
            continue
        if code != 0:
            out('STOP: step %s failed with exit %d' % (name, code))
            return EXIT_STEP_FAILED

    if args.skip_recalc:
        out('  recalc    skipped: run it on the host that holds the database')
        return result
    recalc = _read_json(paths['recalc_json']) or {}
    writes = recalc.get('calc_writes') or {}
    out('  recalc    flights=%s candidates=%s calc_writes=%s'
        % (recalc.get('flights_in_period'),
           recalc.get('structural_candidates'),
           json.dumps(writes, sort_keys=True)))
    if args.expect_unchanged and set(writes) - {'unchanged'}:
        out('NOT IDEMPOTENT: the recalculation wrote %s'
            % json.dumps(writes, sort_keys=True))
        return EXIT_NOT_IDEMPOTENT
    return result


def build_parser():
    parser = argparse.ArgumentParser(
        description='The daily DJI area control cycle in one command.')
    parser.add_argument('--db', dest='db_path')
    parser.add_argument('--days', type=int, default=DEFAULT_DAYS)
    parser.add_argument('--from', dest='date_from', metavar='YYYY-MM-DD')
    parser.add_argument('--to', dest='date_to', metavar='YYYY-MM-DD')
    parser.add_argument('--no-dji', dest='no_dji', action='store_true',
                        help='skip the two steps that contact DJI')
    parser.add_argument('--skip-recalc', dest='skip_recalc',
                        action='store_true',
                        help='the collector host does not hold the database')
    parser.add_argument('--recalc-only', dest='recalc_only',
                        action='store_true',
                        help='the database host carries no collector')
    parser.add_argument('--stop-above', dest='stop_above', type=int,
                        default=DEFAULT_STOP_ABOVE)
    parser.add_argument('--expect-unchanged', dest='expect_unchanged',
                        action='store_true')
    parser.add_argument('--collector-python', dest='collector_python')
    parser.add_argument('--app-python', dest='app_python')
    parser.add_argument('--work-dir', dest='work_dir',
                        default=os.path.join(ROOT, 'drone_collector', 'data',
                                             'area_daily'))
    return parser


def main(argv=None):
    return execute(build_parser().parse_args(argv))


if __name__ == '__main__':
    sys.exit(main())
