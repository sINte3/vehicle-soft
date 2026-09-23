# -*- coding: utf-8 -*-
"""tools/dji_area_daily.py -- ежедневный цикл контроля площади DJI одной командой.

DJI-AREA-PRODUCTIONIZATION-001, DRONE-AREA-CONTROL-V2-MEGA (блоки D и E).
Один вход вместо семи несвязанных команд:

  1. FLIGHTS   сборщик обходит свежие вылеты и кладёт их списочное
               доказательство (обращение к DJI);
  2. MANIFEST  Vehicle Soft отвечает, кому нужен адресный V4: все кандидаты
               экрана без V4 плюс малая контрольная выборка (DJI не трогается);
  3. SOURCES   сборщик забирает источники ТОЛЬКО этих вылетов и шлёт их
               приёмнику (обращение к DJI; пропускается, если манифест пуст);
     VERIFY    только если SOURCES вернул 18 («собрано не всё»): тот же
               манифест ещё раз -- кто из названных так и остался без V4;
  4. RECALC    пересчёт того же скользящего окна поверх сохранённых улик.

Порядок определяется зависимостями, а не привычкой: кандидата называет
замороженный экран по СПИСОЧНЫМ скалярам, поэтому манифесту нужен только шаг
1, а предварительный пересчёт не нужен. Пересчёт стоит последним, потому что
именно он превращает привезённый V4 в исправленную площадь.

КАНДИДАТ И КОНТРОЛЬ -- РАЗНЫЕ ПОТЕРИ (блок E). Манифест называет два сорта
вылетов: кандидатов замороженного экрана (без V4 их нельзя ни исправить, ни
оправдать) и контрольную выборку (V4 нужен, чтобы заметить новый класс
аномалии, а не чтобы принять решение). Если после SOURCES хоть один КАНДИДАТ
остался без V4 -- цикл FAILED, код 5: решение по нему не принято, и «успех»
был бы ложью. Если без V4 остался только КОНТРОЛЬ -- цикл
SUCCESS_WITH_WARNINGS, код 0: ни одна корректировка не заблокирована.
Контроль, отрезанный лимитом манифеста ещё ДО сбора, потерей не считается --
его никто и не запрашивал. Пересчёт идёт во всех этих случаях: он работает по
уже сохранённым доказательствам и ничего не обнуляет.

Окно -- три отчётных дня UTC+5: цепочка может пересечь полночь, доказательство
приходит с задержкой, и завтрашний прогон обязан лечить вчерашнюю запись.

ДВЕ ТОПОЛОГИИ. На production сборщик и база стоят на одном сервере, и цикл
идёт одной командой целиком. На площадке сборщик живёт на рабочей машине (там
сохранена сессия DJI), а база -- на сервере: рабочая машина выполняет шаги 1-3
с `--skip-recalc`, сервер -- шаг 4 с `--recalc-only`. Окно в обеих половинах
считает один и тот же код, поэтому даты в PowerShell никто не вычисляет.

ОДИН ЦИКЛ ЗА РАЗ. Весь цикл держит блокировку цикла
(`<каталог базы>\\dji_area_cycle.lock`, без `--db` -- в рабочем каталоге):
расписание, кнопка «Обновить данные DJI» (`--run-queued`) и исторический
backfill ходят через неё же. Занято -- код 7, ни один шаг не запускается.

ЖУРНАЛ ПРОГОНОВ. Если `--db` указывает на базу с таблицами миграции
DRONE_AREA_CONTROL_V2_001, полный цикл записывает строку в
`drone_area_cycle_runs` (окно, текущий шаг, итог, код, сводку). Нет таблиц --
цикл идёт без журнала и говорит об этом одной строкой. Частичные режимы
(`--no-dji`, `--skip-recalc`, `--recalc-only`) в журнал не пишутся: свежих
данных DJI они не приносят, и «последнее успешное обновление» было бы ложью.
`--run-queued` -- исполнитель кнопки: берёт блокировку, забирает прогон из
очереди и ведёт его строку до конца. Итог каждого прогона лежит ещё и в
`<рабочий каталог>\\last_cycle.json`.

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
  --flights-kind backfill  -- метка обхода вылетов (для исторического прогона)
  --lock-wait SECONDS      -- сколько ждать занятую блокировку цикла
                              (по умолчанию 0; с --run-queued -- 3600)
  --run-queued             -- выполнить прогон, поставленный кнопкой

Коды возврата: 0 цикл выполнен (итог SUCCESS или SUCCESS_WITH_WARNINGS);
1 ошибка аргументов; 2 база не найдена; 3 шаг упал (после FLIGHTS и MANIFEST
цикл остановлен, после SOURCES пересчёт по уже сохранённым доказательствам
выполнен); 4 манифест больше `--stop-above` -- цикл остановлен ДО обращения к
DJI; 5 кандидат остался без доказательства V4 (или полноту не удалось
проверить) -- пересчёт выполнен, следующий прогон доберёт; 6 пересчёт записал
строки при `--expect-unchanged`; 7 другой цикл держит блокировку -- ничего не
запускалось. Вывод в консоль только ASCII.
"""

import argparse
import io
import json
import os
import sqlite3
import subprocess
import sys
import traceback
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from drone_collector import runlock  # noqa: E402  (stdlib only)

REPORT_UTC_OFFSET_HOURS = 5
DEFAULT_DAYS = 3
MAX_DAYS = 7
DEFAULT_STOP_ABOVE = 50

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_NO_DATABASE = 2
EXIT_STEP_FAILED = 3
EXIT_MANIFEST_TOO_LARGE = 4
# [REASON]: 5 сохранён за прежним именем: блоки ранбука и планировщик
# сравнивают $LASTEXITCODE с ЧИСЛОМ. Смысл уточнён блоком E: не «источники
# собраны не полностью», а «КАНДИДАТ остался без доказательства». Потеря
# одного контроля больше не даёт 5.
EXIT_SOURCES_INCOMPLETE = 5
EXIT_CANDIDATE_EVIDENCE_MISSING = EXIT_SOURCES_INCOMPLETE
EXIT_NOT_IDEMPOTENT = 6
EXIT_BUSY = 7

COLLECTOR_SOURCES_INCOMPLETE = 18
COLLECTOR_MANIFEST_TOO_LARGE = 22
COLLECTOR_BUSY = 24

STEP_FLIGHTS = 'FLIGHTS'
STEP_MANIFEST = 'MANIFEST'
STEP_SOURCES = 'SOURCES'
STEP_VERIFY = 'VERIFY'
STEP_RECALC = 'RECALC'

OUTCOME_SUCCESS = 'SUCCESS'
OUTCOME_WARNINGS = 'SUCCESS_WITH_WARNINGS'
OUTCOME_FAILED = 'FAILED'
OUTCOME_BUSY = 'BUSY'

FAILURE_USAGE = 'USAGE'
FAILURE_NO_DATABASE = 'NO_DATABASE'
FAILURE_STEP = 'STEP_FAILED'
FAILURE_COLLECTOR_BUSY = 'COLLECTOR_BUSY'
FAILURE_MANIFEST_TOO_LARGE = 'MANIFEST_TOO_LARGE'
FAILURE_CANDIDATE_EVIDENCE = 'CANDIDATE_EVIDENCE_MISSING'
FAILURE_VERIFY_UNAVAILABLE = 'VERIFY_UNAVAILABLE'
FAILURE_NOT_IDEMPOTENT = 'NOT_IDEMPOTENT'
FAILURE_CYCLE_BUSY = 'CYCLE_BUSY'
FAILURE_UNEXPECTED = 'UNEXPECTED_ERROR'
WARNING_CONTROL_EVIDENCE = 'CONTROL_EVIDENCE_MISSING'

# Причина записи манифеста -- `dji_area.capture_manifest.REASON_CONTROL`.
# Записана числом-строкой, а не импортом: цикл не тянет конвейер площади ради
# одного слова. Совпадение держит тест.
MANIFEST_REASON_CONTROL = 'CONTROL'

LAST_CYCLE_FILE = 'last_cycle.json'
CYCLE_LOCK_PURPOSE = 'dji-area-cycle'
RUN_QUEUED_LOCK_WAIT_S = 3600
LOCK_POLL_S = 2.0

# [REASON]: когда в одном прогоне случилось несколько бед, код возврата
# называет самую тяжёлую: упавший шаг (3) важнее неидемпотентного пересчёта
# (6), а тот -- недобранного кандидата (5). 4 останавливает цикл сразу и
# с другими не встречается.
_SEVERITY = {EXIT_OK: 0, EXIT_CANDIDATE_EVIDENCE_MISSING: 1,
             EXIT_NOT_IDEMPOTENT: 2, EXIT_STEP_FAILED: 3,
             EXIT_MANIFEST_TOO_LARGE: 4}


class Refusal(Exception):
    """Командная строка или окружение не позволяют начать цикл."""

    def __init__(self, code, failure, message):
        Exception.__init__(self, message)
        self.code = code
        self.failure = failure
        self.message = message


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


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
        days = DEFAULT_DAYS if args.days is None else args.days
        if days < 1:
            raise ValueError('--days must be positive')
        date_to = today
        date_from = today - timedelta(days=days - 1)
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


def ascii_line(text):
    return str(text).encode('ascii', 'replace').decode('ascii')


def run_command(command, cwd):
    """Исполнить шаг; вернуть код возврата. Вывод шага идёт прямо в консоль."""
    return subprocess.call(command, cwd=cwd)


def modes_of(args):
    return [flag for flag, on in (('--no-dji', args.no_dji),
                                  ('--skip-recalc', args.skip_recalc),
                                  ('--recalc-only', args.recalc_only)) if on]


def validate(args, today):
    """(date_from, date_to, modes) либо `Refusal` -- ничего не создаёт."""
    try:
        date_from, date_to = resolve_window(args, today)
    except ValueError as exc:
        raise Refusal(EXIT_USAGE, FAILURE_USAGE, str(exc))
    modes = modes_of(args)
    if len(modes) > 1:
        # [REASON]: каждая пара либо противоречива, либо оставляет от цикла
        # один манифест. Молча выбрать одно из двух значило бы исполнить не
        # то, что просили.
        raise Refusal(EXIT_USAGE, FAILURE_USAGE,
                      '%s are mutually exclusive' % ' and '.join(modes))
    if not args.skip_recalc:
        if not args.db_path:
            raise Refusal(EXIT_USAGE, FAILURE_USAGE,
                          '--db is required unless --skip-recalc is given')
        if not os.path.exists(args.db_path):
            # [REASON]: пересчёт по отсутствующей базе создал бы пустой файл
            # и отчитался нулём записей -- «всё хорошо» на месте «ничего нет».
            raise Refusal(EXIT_NO_DATABASE, FAILURE_NO_DATABASE,
                          'database not found at %s - refusing to run.'
                          % args.db_path)
    return date_from, date_to, modes


def manifest_command(collector, ids_file, summary, date_from, date_to,
                     stop_above):
    return [collector, '-m', 'drone_collector.area_manifest', '--out',
            ids_file, '--summary', summary, '--from', date_from.isoformat(),
            '--to', date_to.isoformat(), '--stop-above', str(stop_above)]


def plan(args, date_from, date_to, work_dir):
    """Команды цикла по шагам. Чистая функция: её и проверяют тесты."""
    collector = args.collector_python or sys.executable
    app_python = args.app_python or sys.executable
    ids_file = os.path.join(work_dir, 'area_ids.txt')
    manifest_json = os.path.join(work_dir, 'area_manifest.json')
    recalc_json = os.path.join(work_dir, 'area_recalc.json')
    verify_ids = os.path.join(work_dir, 'area_ids_after.txt')
    verify_json = os.path.join(work_dir, 'area_manifest_after.json')
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
             'recalc_json': recalc_json, 'verify_ids': verify_ids,
             'verify_json': verify_json,
             'last_cycle': os.path.join(work_dir, LAST_CYCLE_FILE),
             # VERIFY -- тот же манифест, что и шаг 2, в другие файлы: иначе
             # «до» и «после» нечем было бы сравнить.
             'verify_command': manifest_command(
                 collector, verify_ids, verify_json, date_from, date_to,
                 args.stop_above)}
    if args.recalc_only:
        return [recalc_step], paths
    steps = []
    if not args.no_dji:
        steps.append((STEP_FLIGHTS, [
            collector, '-m', 'drone_collector.main', '--from', walk_from,
            '--to', walk_to, '--kind',
            getattr(args, 'flights_kind', None) or 'incremental']))
    steps.append((STEP_MANIFEST, manifest_command(
        collector, ids_file, manifest_json, date_from, date_to,
        args.stop_above)))
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


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def write_json_atomic(path, document):
    tmp = path + '.tmp'
    with io.open(tmp, 'w', encoding='utf-8', newline='\n') as handle:
        json.dump(document, handle, ensure_ascii=True, indent=1,
                  sort_keys=True, default=str)
    os.replace(tmp, path)


def capture_entries(document):
    """Список `capture` манифеста либо None, если манифест не прочитан."""
    if not isinstance(document, dict):
        return None
    capture = document.get('capture')
    if not isinstance(capture, list):
        return None
    return [entry if isinstance(entry, dict) else {'flight_id': entry}
            for entry in capture]


def _flight_id(entry):
    try:
        return int(entry.get('flight_id'))
    except (TypeError, ValueError):
        return entry.get('flight_id')


def manifest_counts(document):
    readable = isinstance(document, dict)
    capture = capture_entries(document)
    counts = (document.get('counts') if readable else None) or {}
    candidates = counts.get('candidates_need_capture')
    return {
        'ids': len(capture) if capture is not None else None,
        'candidates': candidates,
        'controls': ((counts.get('capture_total') or 0) - (candidates or 0)
                     if counts else None),
        'no_v4_at_source': counts.get('candidates_no_v4_at_source'),
        'over_cap': bool(document.get('over_cap')) if readable else None,
    }


def evidence_misses(before, after):
    """Кто из названных манифестом так и остался без V4 после SOURCES.

    ``before`` -- записи `capture` манифеста шага 2 (None, если его не
    прочитали), ``after`` -- записи повторного манифеста VERIFY. Манифест
    называет только вылеты БЕЗ V4, поэтому запись, оставшаяся в ``after``, --
    это вылет, по которому доказательство так и не пришло.

    [REASON]: осторожно в сторону кандидата. Запись без причины, с чужой
    причиной или без читаемого id считается кандидатом: ложный провал хуже
    ложного успеха только на первый взгляд -- ложный успех оставляет
    корректировку неразрешённой, и никто об этом не узнаёт.

    [REASON]: контроль считается потерей, только если его запрашивали. Лимит
    манифеста режет контроль ДО сбора, а после сбора освободившееся место
    занимают контрольные записи, которых в первом списке не было; назвать их
    потерей значило бы ругать сбор за то, чего у него не просили.
    """
    asked = None
    if before is not None:
        asked = {_flight_id(entry) for entry in before}
    candidates, controls = [], []
    for entry in after:
        flight_id = _flight_id(entry)
        if entry.get('reason') == MANIFEST_REASON_CONTROL:
            if asked is None or flight_id in asked:
                controls.append(flight_id)
        else:
            candidates.append(flight_id)
    return {'candidates': candidates, 'controls': controls}


def flight_stats(db_path, started, finished, kind=None):
    """Сумма строк `drone_sync_logs`, открытых за время шага FLIGHTS.

    Только чтение (`mode=ro`: файл не создаётся и не пишется). None, если
    базы нет, это не SQLite или журнала синхронизации в ней нет.

    [REASON]: строки сравниваются 19-символьными префиксами. SQLAlchemy
    хранит `started_at` 26 символами с микросекундами, и сравнение целой
    строки с границей без них отрезало бы строку, открытую в последнюю
    секунду шага.
    """
    if not db_path or not os.path.exists(db_path):
        return None
    uri = 'file:%s?mode=ro' % os.path.abspath(db_path).replace(
        '\\', '/').replace('?', '%3f').replace('#', '%23')
    lower = started.replace(microsecond=0).strftime('%Y-%m-%d %H:%M:%S')
    upper = (finished.replace(microsecond=0)
             + timedelta(seconds=1)).strftime('%Y-%m-%d %H:%M:%S')
    sql = ("SELECT COUNT(*), COALESCE(SUM(records_seen), 0), "
           "COALESCE(SUM(records_new), 0), "
           "COALESCE(SUM(records_duplicate), 0), "
           "COALESCE(SUM(records_unresolved), 0), "
           "COALESCE(SUM(records_error), 0) FROM drone_sync_logs "
           "WHERE substr(replace(started_at, 'T', ' '), 1, 19) >= ? "
           "AND substr(replace(started_at, 'T', ' '), 1, 19) <= ?")
    params = [lower, upper]
    if kind:
        # [REASON]: обход вылетов шлёт ровно этот `kind`. Строка другого
        # вида, открытая в ту же минуту чужим сборщиком (площадка пишет в
        # ту же базу), к этому циклу не относится.
        sql += ' AND kind = ?'
        params.append(kind)
    try:
        con = sqlite3.connect(uri, uri=True, timeout=30)
    except sqlite3.Error:
        return None
    try:
        row = con.execute(sql, params).fetchone()
    except sqlite3.Error:
        return None
    finally:
        con.close()
    runs, seen, new, duplicates, unresolved, errors = row
    return {'seen': seen, 'new': new, 'duplicates': duplicates,
            'unresolved': unresolved, 'errors': errors, 'sync_runs': runs}


def new_result(date_from=None, date_to=None):
    """Итог прогона: схема, которую рисует экран журнала (ключи не менять)."""
    return {
        'window': {'from': date_from.isoformat() if date_from else None,
                   'to': date_to.isoformat() if date_to else None},
        'outcome': None,
        'exit_code': None,
        'steps': {},
        'failed_step': None,
        'flights': None,
        'manifest': None,
        'evidence_misses': {'candidates': [], 'controls': []},
        'recalc': None,
        'warnings': [],
        'failure': None,
    }


def outcome_for(code, warnings):
    if code == EXIT_OK:
        return OUTCOME_WARNINGS if warnings else OUTCOME_SUCCESS
    if code == EXIT_BUSY:
        return OUTCOME_BUSY
    return OUTCOME_FAILED


class _NoLedger(object):

    def window(self, date_from, date_to):
        pass

    def step(self, name):
        pass


def run_cycle(args, runner=run_command, today=None, out=say, ledger=None,
              clock=utcnow):
    """Весь цикл. Возвращает (код возврата, итог-словарь).

    ``ledger`` -- объект с методами ``window(date_from, date_to)`` и
    ``step(name)``: журнал прогонов узнаёт окно и текущий шаг по мере
    движения. Без него цикл тот же.
    """
    ledger = ledger or _NoLedger()
    today = today or report_today()
    try:
        date_from, date_to, modes = validate(args, today)
    except Refusal as exc:
        out('ERROR: %s' % exc.message)
        result = new_result()
        result.update(outcome=OUTCOME_FAILED, exit_code=exc.code,
                      failure=exc.failure)
        return exc.code, result

    result = new_result(date_from, date_to)
    ledger.window(date_from, date_to)
    work_dir = os.path.abspath(args.work_dir)
    os.makedirs(work_dir, exist_ok=True)
    steps, paths = plan(args, date_from, date_to, work_dir)
    names = [name for name, _command in steps]
    # Вчерашний манифест не должен пережить сегодняшний отказ шага 2, а
    # вчерашняя сводка пересчёта -- сегодняшний отказ шага 4.
    if STEP_MANIFEST in names:
        for key in ('ids_file', 'manifest_json', 'verify_ids', 'verify_json'):
            _remove(paths[key])
    if STEP_RECALC in names:
        _remove(paths['recalc_json'])
    _remove(paths['last_cycle'])

    out('DJI AREA DAILY CYCLE %s .. %s%s'
        % (date_from, date_to, ' (%s)' % modes[0].lstrip('-') if modes
           else ''))

    verdict = {'code': EXIT_OK, 'failure': None, 'step': None}

    def note(code, failure, step):
        if _SEVERITY[code] > _SEVERITY[verdict['code']]:
            verdict.update(code=code, failure=failure, step=step)

    def run_step(name, command):
        ledger.step(name)
        started = clock()
        code = runner(command, ROOT)
        finished = clock()
        result['steps'][name] = code
        out('  %-9s exit %d' % (name, code))
        return code, started, finished

    def step_failure(code):
        if code == COLLECTOR_BUSY:
            return FAILURE_COLLECTOR_BUSY, (' (another collector run holds '
                                            'the collector lock)')
        return FAILURE_STEP, ''

    def done():
        result['exit_code'] = verdict['code']
        result['failure'] = verdict['failure']
        result['failed_step'] = verdict['step']
        result['outcome'] = outcome_for(verdict['code'], result['warnings'])
        out('  outcome   %s exit %d%s%s'
            % (result['outcome'], verdict['code'],
               ' failure=%s' % verdict['failure'] if verdict['failure']
               else '',
               ' warnings=%s' % ','.join(result['warnings'])
               if result['warnings'] else ''))
        try:
            write_json_atomic(paths['last_cycle'], result)
        except (IOError, OSError) as exc:
            out('WARNING: %s was not written (%s)'
                % (LAST_CYCLE_FILE, type(exc).__name__))
        return verdict['code'], result

    before = None
    ids = None
    recalc_ran = False
    for name, command in steps:
        if name == STEP_SOURCES and ids == 0:
            out('  %-9s skipped: the manifest names no flight' % name)
            continue
        code, started, finished = run_step(name, command)

        if name == STEP_FLIGHTS:
            stats = flight_stats(args.db_path, started, finished,
                                 command[command.index('--kind') + 1])
            result['flights'] = stats
            if stats is not None:
                out('  flights   seen=%(seen)s new=%(new)s duplicates='
                    '%(duplicates)s unresolved=%(unresolved)s errors='
                    '%(errors)s sync_runs=%(sync_runs)s' % stats)
            if code != 0:
                failure, hint = step_failure(code)
                out('STOP: step %s failed with exit %d%s' % (name, code, hint))
                note(EXIT_STEP_FAILED, failure, name)
                return done()

        elif name == STEP_MANIFEST:
            document = _read_json(paths['manifest_json'])
            counts = manifest_counts(document)
            result['manifest'] = counts
            before = capture_entries(document)
            ids = counts['ids']
            out('  manifest  ids=%s candidates=%s controls=%s '
                'no_v4_at_source=%s'
                % (ids, counts['candidates'], counts['controls'] or 0,
                   counts['no_v4_at_source']))
            if code == COLLECTOR_MANIFEST_TOO_LARGE:
                out('STOP: the manifest is larger than --stop-above %d; DJI '
                    'was NOT contacted for sources.' % args.stop_above)
                note(EXIT_MANIFEST_TOO_LARGE, FAILURE_MANIFEST_TOO_LARGE,
                     name)
                return done()
            if code != 0:
                out('STOP: step %s failed with exit %d' % (name, code))
                note(EXIT_STEP_FAILED, FAILURE_STEP, name)
                return done()

        elif name == STEP_SOURCES:
            if code == COLLECTOR_SOURCES_INCOMPLETE:
                out('  sources are incomplete; asking the manifest again which '
                    'flights still lack V4')
                _verify(paths, before, result, run_step, note, out)
            elif code != 0:
                # [REASON]: блок E -- пересчёт идёт по уже сохранённым
                # доказательствам. Упавший сбор ничего не испортил в базе, а
                # вчерашние V4, пришедшие позже, иначе ждали бы ещё сутки.
                failure, hint = step_failure(code)
                out('  step %s failed with exit %d%s; the recalculation still '
                    'runs over the evidence already stored'
                    % (name, code, hint))
                note(EXIT_STEP_FAILED, failure, name)

        elif name == STEP_RECALC:
            recalc_ran = code == 0
            if code != 0:
                out('STOP: step %s failed with exit %d' % (name, code))
                note(EXIT_STEP_FAILED, FAILURE_STEP, name)

    if args.skip_recalc:
        out('  recalc    skipped: run it on the host that holds the database')
        return done()
    if not recalc_ran:
        return done()
    recalc = _read_json(paths['recalc_json']) or {}
    writes = recalc.get('calc_writes') or {}
    result['recalc'] = {
        'flights_in_period': recalc.get('flights_in_period'),
        'structural_candidates': recalc.get('structural_candidates'),
        'calc_writes': writes,
    }
    out('  recalc    flights=%s candidates=%s calc_writes=%s'
        % (recalc.get('flights_in_period'),
           recalc.get('structural_candidates'),
           json.dumps(writes, sort_keys=True)))
    if args.expect_unchanged and set(writes) - {'unchanged'}:
        out('NOT IDEMPOTENT: the recalculation wrote %s'
            % json.dumps(writes, sort_keys=True))
        note(EXIT_NOT_IDEMPOTENT, FAILURE_NOT_IDEMPOTENT, STEP_RECALC)
    return done()


def _verify(paths, before, result, run_step, note, out):
    """VERIFY: повторный манифест и разбор потерь (см. evidence_misses)."""
    _remove(paths['verify_ids'])
    _remove(paths['verify_json'])
    code, _started, _finished = run_step(STEP_VERIFY, paths['verify_command'])
    # 22 -- «идентификаторов больше порога»: файл при этом записан, а в DJI
    # VERIFY не ходит, так что порог здесь ничего не охраняет.
    after = capture_entries(_read_json(paths['verify_json'])) \
        if code in (0, COLLECTOR_MANIFEST_TOO_LARGE) else None
    if after is None:
        # [REASON]: не проверили -- значит не знаем, дошли ли кандидаты. Код
        # 5, а не 0: «успех», объявленный без проверки, и есть ложный PASS.
        out('  verify    unavailable (exit %d): whether every candidate got '
            'its V4 is unknown' % code)
        note(EXIT_CANDIDATE_EVIDENCE_MISSING, FAILURE_VERIFY_UNAVAILABLE,
             STEP_VERIFY)
        return
    misses = evidence_misses(before, after)
    result['evidence_misses'] = misses
    out('  verify    candidates_missing=%d controls_missing=%d'
        % (len(misses['candidates']), len(misses['controls'])))
    if misses['controls']:
        result['warnings'].append(WARNING_CONTROL_EVIDENCE)
    if misses['candidates']:
        out('  %d candidate(s) are still without V4; their areas stay '
            'unresolved until a later run brings the evidence'
            % len(misses['candidates']))
        note(EXIT_CANDIDATE_EVIDENCE_MISSING, FAILURE_CANDIDATE_EVIDENCE,
             STEP_SOURCES)
    elif misses['controls']:
        out('  only control samples are without V4: no correction is '
            'blocked (warning, not a failure)')


def execute(args, runner=run_command, today=None, out=say, ledger=None,
            result=None):
    """Прежний вход: код возврата. ``result`` (dict) получает итог прогона."""
    code, outcome = run_cycle(args, runner=runner, today=today, out=out,
                              ledger=ledger)
    if result is not None:
        result.update(outcome)
    return code


# ─── Блокировка цикла и журнал прогонов ─────────────────────────────────────

def describe_owner(path):
    info = runlock.owner(path) or {}
    if not info:
        return 'owner unknown'
    return ascii_line('pid=%s host=%s purpose=%s since=%s UTC' % (
        info.get('pid', '-'), info.get('host', '-'), info.get('purpose', '-'),
        info.get('since_utc', '-')))


def take_cycle_lock(lock, wait_s, out):
    if lock.acquire(wait_s=0):
        return True
    if wait_s > 0:
        out('  another DJI area cycle holds the cycle lock (%s); waiting up '
            'to %d s' % (describe_owner(lock.path), wait_s))
        if lock.acquire(wait_s=wait_s, poll_s=min(LOCK_POLL_S, wait_s)):
            return True
    return False


def busy_message(lock_path):
    return ('another DJI area cycle holds %s (%s); nothing was run'
            % (ascii_line(lock_path), describe_owner(lock_path)))


def redact(text):
    """ASCII без значений секретов (см. dji_area.control_store.redact)."""
    try:
        from dji_area import control_store
    except Exception:  # pragma: no cover - the package is part of the repo
        return ascii_line(text)[:2000]
    return control_store.redact(text)


class Ledger(object):
    """Окно и шаг прогона -> строка `drone_area_cycle_runs`.

    [REASON]: сбой записи в журнал не останавливает цикл. Журнал -- для
    экрана; сам цикл -- это данные площади, и потерять их из-за занятой на
    30 секунд базы было бы хуже, чем показать шаг с опозданием.
    """

    def __init__(self, con, run_id, out):
        self.con = con
        self.run_id = run_id
        self.out = out
        self.current_step = None

    def _safe(self, fn, *args):
        try:
            fn(self.con, self.run_id, *args)
        except sqlite3.Error as exc:
            self.out('WARNING: the run ledger was not updated (%s)'
                     % type(exc).__name__)

    def window(self, date_from, date_to):
        from dji_area import control_store
        self._safe(control_store.set_window, date_from, date_to)

    def step(self, name):
        from dji_area import control_store
        self.current_step = name
        self._safe(control_store.set_step, name)


def open_ledger(db_path, out):
    """Соединение с журналом прогонов либо None (и строка -- почему)."""
    if not db_path or not os.path.exists(db_path):
        return None
    try:
        from dji_area import control_store, store
    except ImportError as exc:  # pragma: no cover - the package is ours
        out('  ledger    not recorded: %s' % ascii_line(exc))
        return None
    try:
        con = store.connect(db_path)
    except Exception as exc:
        out('  ledger    not recorded: the database could not be opened (%s)'
            % type(exc).__name__)
        return None
    try:
        present = control_store.tables_present(con)
    except sqlite3.Error as exc:
        con.close()
        out('  ledger    not recorded: the database could not be read (%s)'
            % type(exc).__name__)
        return None
    if not present:
        con.close()
        out('  ledger    not recorded: the run ledger tables are missing '
            '(migrate_drone_area_control_v2_001.py)')
        return None
    return con


def summary_message(code, result):
    """Одна строка ASCII для журнала: итог, шаги, потери."""
    parts = ['exit %d %s' % (code, result.get('outcome'))]
    if result.get('failure'):
        parts.append('%s at %s' % (result['failure'],
                                   result.get('failed_step') or '-'))
    steps = result.get('steps') or {}
    if steps:
        order = [STEP_FLIGHTS, STEP_MANIFEST, STEP_SOURCES, STEP_VERIFY,
                 STEP_RECALC]
        parts.append('steps ' + ' '.join('%s=%s' % (name, steps[name])
                                         for name in order if name in steps))
    misses = result.get('evidence_misses') or {}
    if STEP_VERIFY in steps:
        parts.append('candidates_missing=%d controls_missing=%d'
                     % (len(misses.get('candidates') or []),
                        len(misses.get('controls') or [])))
    if result.get('warnings'):
        parts.append('warnings ' + ','.join(result['warnings']))
    return '; '.join(parts)


def status_for(outcome):
    from dji_area import control_store as cs
    return {OUTCOME_SUCCESS: cs.STATUS_SUCCESS,
            OUTCOME_WARNINGS: cs.STATUS_WARNINGS,
            OUTCOME_FAILED: cs.STATUS_FAILED,
            OUTCOME_BUSY: cs.STATUS_BUSY}[outcome]


def run_with_ledger(args, con, run_id, runner, today, out):
    """Провести цикл, ведя строку журнала; строка закрывается ВСЕГДА."""
    from dji_area import control_store as cs

    ledger = Ledger(con, run_id, out)
    code = EXIT_STEP_FAILED
    status = cs.STATUS_INTERRUPTED
    failed_step = None
    message = 'the cycle was interrupted before it finished'
    result = None
    try:
        code, result = run_cycle(args, runner=runner, today=today, out=out,
                                 ledger=ledger)
        status = status_for(result['outcome'])
        failed_step = result.get('failed_step')
        message = summary_message(code, result)
    except Exception as exc:
        # [REASON]: текст исключения мы не контролируем -- он может нести
        # что угодно из окружения. На экран журнала и в консоль он уходит
        # только через redact(), трассировка -- тоже.
        code = EXIT_STEP_FAILED
        status = cs.STATUS_FAILED
        failed_step = ledger.current_step
        message = redact('unexpected failure at %s: %s: %s'
                         % (failed_step or '-', type(exc).__name__, exc))
        for line in redact(traceback.format_exc()).splitlines():
            out('  ' + line)
        out('ERROR: %s' % message)
        result = new_result()
        result.update(outcome=OUTCOME_FAILED, exit_code=code,
                      failed_step=failed_step, failure=FAILURE_UNEXPECTED)
    finally:
        try:
            cs.finish(con, run_id, status, exit_code=code,
                      failed_step=failed_step, message=message,
                      result=result)
        except sqlite3.Error as exc:
            out('WARNING: the run ledger row %s was not closed (%s)'
                % (run_id, type(exc).__name__))
    return code


def close_dead_runs(con, keep_run_id, out):
    """Закрыть строки RUNNING, чей процесс умер. Только под блокировкой цикла.

    [REASON]: под блокировкой цикла ЧУЖОЙ живой прогон невозможен -- значит
    любая другая строка RUNNING принадлежит умершему процессу. Для
    `reconcile` это «блокировку не держит никто другой» (lock_held=False).
    Но если в очереди стоит ручной запрос, его исполнитель может как раз
    ждать эту блокировку, и тогда `reconcile` назвал бы его «несостоявшимся
    запуском» по одному возрасту. Поэтому при живой очереди здесь ничего не
    закрывается: показ (`effective_status`) и так видит мёртвые строки, а
    закроет их исполнитель очереди, забрав свой прогон.
    """
    from dji_area import control_store as cs
    try:
        if any(run['status'] == cs.STATUS_QUEUED
               for run in cs.active_runs(con)):
            return []
        closed = cs.reconcile(con, lock_held=False, keep_run_id=keep_run_id)
    except sqlite3.Error as exc:
        out('WARNING: stale ledger rows were not closed (%s)'
            % type(exc).__name__)
        return []
    if closed:
        out('  ledger    closed %d stale run(s): %s'
            % (len(closed), ', '.join(str(i) for i in closed)))
    return closed


def _has_queued(con):
    return con.execute("SELECT 1 FROM drone_area_cycle_runs "
                       "WHERE status='QUEUED' LIMIT 1").fetchone() is not None


def run_queued(args, con, lock_path, wait_s, runner, today, out):
    """`--run-queued`: исполнитель кнопки «Обновить данные DJI»."""
    from dji_area import control_store as cs

    if not _has_queued(con):
        out('nothing queued')
        return EXIT_OK
    lock = runlock.RunLock(lock_path, purpose=CYCLE_LOCK_PURPOSE)
    if not take_cycle_lock(lock, wait_s, out):
        message = busy_message(lock_path)
        out('BUSY: %s. Exit %d.' % (message, EXIT_BUSY))
        # [REASON]: запрос закрывается, а не остаётся в очереди. Ждали мы
        # уже `--lock-wait` секунд; висящий «ожидает» через четверть часа
        # стал бы «запуск не состоялся», хотя запуск был и упёрся в чужой
        # цикл -- человеку нужен именно этот ответ.
        try:
            run = cs.claim_queued(con)
            if run is not None:
                result = new_result()
                result.update(outcome=OUTCOME_BUSY, exit_code=EXIT_BUSY,
                              failure=FAILURE_CYCLE_BUSY)
                cs.finish(con, run['id'], cs.STATUS_BUSY,
                          exit_code=EXIT_BUSY, message=message,
                          result=result)
                out('  ledger    queued run %d closed as BUSY' % run['id'])
        except sqlite3.Error as exc:
            out('WARNING: the run ledger was not updated (%s)'
                % type(exc).__name__)
        return EXIT_BUSY
    try:
        # [REASON]: сперва забрать, потом чистить. Запрос мог ждать эту
        # блокировку дольше пятнадцати минут (за плановым циклом), и
        # `reconcile` до забора назвал бы его несостоявшимся запуском.
        run = cs.claim_queued(con)
        close_dead_runs(con, run['id'] if run else None, out)
        if run is None:
            out('nothing queued')
            return EXIT_OK
        out('  ledger    queued run %d claimed' % run['id'])
        return run_with_ledger(args, con, run['id'], runner, today, out)
    finally:
        lock.release()


def run_plain(args, con, lock_path, wait_s, window, runner, today, out):
    """Обычный прогон (расписание, консоль). Журнал -- если он есть."""
    from_to = window
    lock = runlock.RunLock(lock_path, purpose=CYCLE_LOCK_PURPOSE)
    if not take_cycle_lock(lock, wait_s, out):
        message = busy_message(lock_path)
        out('BUSY: %s. Exit %d.' % (message, EXIT_BUSY))
        if con is not None:
            from dji_area import control_store as cs
            result = new_result(*from_to)
            result.update(outcome=OUTCOME_BUSY, exit_code=EXIT_BUSY,
                          failure=FAILURE_CYCLE_BUSY)
            try:
                run = cs.start_scheduled(con, from_to[0], from_to[1])
                cs.finish(con, run['id'], cs.STATUS_BUSY,
                          exit_code=EXIT_BUSY, message=message,
                          result=result)
            except sqlite3.Error as exc:
                out('WARNING: the run ledger was not updated (%s)'
                    % type(exc).__name__)
        return EXIT_BUSY
    try:
        run = None
        if con is not None:
            from dji_area import control_store as cs
            try:
                run = cs.start_scheduled(con, from_to[0], from_to[1])
            except sqlite3.Error as exc:
                # Журнал -- для экрана; суточный цикл из-за него не пропадает.
                out('WARNING: the run ledger row was not opened (%s); the '
                    'cycle runs without it' % type(exc).__name__)
        if run is None:
            code, _result = run_cycle(args, runner=runner, today=today,
                                      out=out)
            return code
        close_dead_runs(con, run['id'], out)
        return run_with_ledger(args, con, run['id'], runner, today, out)
    finally:
        lock.release()


def build_parser():
    parser = argparse.ArgumentParser(
        description='The daily DJI area control cycle in one command.')
    parser.add_argument('--db', dest='db_path')
    parser.add_argument('--days', type=int, default=None,
                        help='window length in report days (default %d)'
                        % DEFAULT_DAYS)
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
    parser.add_argument('--flights-kind', dest='flights_kind',
                        choices=('incremental', 'backfill'),
                        default='incremental',
                        help='the --kind the flight walk is sent with')
    parser.add_argument('--lock-wait', dest='lock_wait', type=float,
                        metavar='SECONDS',
                        help='wait this long for another cycle (default 0; '
                             '%d with --run-queued)' % RUN_QUEUED_LOCK_WAIT_S)
    parser.add_argument('--run-queued', dest='run_queued',
                        action='store_true',
                        help='run the cycle the "refresh DJI data" button '
                             'queued, over the default rolling window')
    return parser


def check_invocation(args):
    """Сочетания, которые не имеют смысла при любом окне. Строка -- отказ."""
    if args.lock_wait is not None and not 0 <= args.lock_wait < float('inf'):
        return '--lock-wait must be a finite number of seconds, not negative'
    if args.run_queued:
        # [REASON]: запрос из браузера не несёт ни одного аргумента -- окно,
        # режим и интерпретаторы задаёт командная строка исполнителя. Полный
        # цикл по скользящему окну: иначе «обновить сейчас» могло бы
        # оказаться пересчётом без обращения к DJI.
        conflicting = [flag for flag, on in (
            ('--from/--to', args.date_from or args.date_to),
            ('--no-dji', args.no_dji), ('--skip-recalc', args.skip_recalc),
            ('--recalc-only', args.recalc_only)) if on]
        if conflicting:
            return ('--run-queued runs the full cycle over the rolling '
                    'window; it does not take %s' % ', '.join(conflicting))
        if not args.db_path:
            return '--run-queued needs --db: the queue lives in the database'
    return None


def main(argv=None, runner=run_command, today=None, out=say):
    args = build_parser().parse_args(argv)
    problem = check_invocation(args)
    if problem:
        out('ERROR: %s' % problem)
        return EXIT_USAGE
    today = today or report_today()
    try:
        date_from, date_to, modes = validate(args, today)
    except Refusal as exc:
        out('ERROR: %s' % exc.message)
        return exc.code
    work_dir = os.path.abspath(args.work_dir)
    lock_path = runlock.cycle_lock_path(args.db_path or None, work_dir)
    wait_s = args.lock_wait
    if wait_s is None:
        # [REASON]: плановый прогон не ждёт: занятая блокировка значит, что
        # цикл уже идёт, и второй, начатый следом, ничего не добавит. Кнопка
        # ждёт: человек нажал «обновить», и после текущего цикла его запрос
        # должен выполниться, а не пропасть.
        wait_s = RUN_QUEUED_LOCK_WAIT_S if args.run_queued else 0
    con = None
    try:
        if args.run_queued or not modes:
            con = open_ledger(args.db_path, out)
        if args.run_queued:
            if con is None:
                out('nothing queued: the run ledger is not available')
                return EXIT_OK
            return run_queued(args, con, lock_path, wait_s, runner, today,
                              out)
        return run_plain(args, con, lock_path, wait_s, (date_from, date_to),
                         runner, today, out)
    except Exception as exc:
        for line in redact(traceback.format_exc()).splitlines():
            out('  ' + line)
        out('ERROR: unexpected failure: %s'
            % redact('%s: %s' % (type(exc).__name__, exc)))
        return EXIT_STEP_FAILED
    finally:
        if con is not None:
            con.close()


if __name__ == '__main__':
    sys.exit(main())
