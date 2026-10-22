# -*- coding: utf-8 -*-
"""GPS: приёмка правила K1 кратности прохода. Только чтение.

ЗАЧЕМ
Владелец 13.10.2026: «надо научиться определять двойные проходы»; двойной
проход в одни сутки -- площадь ×2. Правило K1 и условия его приёмки
записаны до прогона -- дорожная карта, §2.14, «Предрегистрация правила K»
(22.10.2026). Этот инструмент делает ровно то, что там записано, и
печатает вердикт. Пороги здесь не выбираются и не подгоняются: T1 считается
по записанной процедуре на сутках калибровки, остальное -- постоянные
`gps/multipass.py`.

ЧТО ДЕЛАЕТ -- один прогон:
  1. калибровка: сутки стенда 13.10 групп B, M, L, C
     (`docs/gps_multipass_bench_set_2026-10-13.csv`) с точками и с
     пройденным свидетельством; T1 -- 5-й процентиль меры `rows`;
  2. правило K1 на опубликованных машино-сутках окна приёмки (не «не наша»,
     не спецтехника) -- кратность и причина;
  3. P1: до 100 суток с причиной `odin_prohod` (первые по sha1
     «ГГГГ-ММ-ДД:id») -- синтетический двойной проход σ = 1,0 м (условие)
     и σ = 2,0 м (справочно);
  4. N1: сутки заявок agro-work «гектары» без «2 марта», с окном в одни
     сутки, «работа была», посчитанным светофором и объёмом к гектарам GPS
     окна 0,80-1,25; справочно -- кандидаты с отношением от 1,6;
  5. вердикт строкой `VERDICT K1: MET | NOT MET | NOT EVALUATED`.

Окно приёмки -- не раньше 14.10.2026 (всё до него разведка уже видела) и
не позже вчерашнего дня: сегодняшние сутки ещё не посчитаны.

Код выхода: 0 -- вердикт MET или NOT MET; 4 -- NOT EVALUATED (мала выборка,
окно продлевается, условия те же); 3 -- вердикт напечатан, но на части
суток расчёт упал (они названы); 2 -- неверный ввод, ничего не считалось;
1 -- инструмент упал.

[REASON]: база и файлы точек открываются `mode=ro`; пишется только
названный --csv. Решения здесь нет: кратность в расчёт не входит, гектары и
площадь A7 не меняются. Оговорка та же, что у прогона кратности: SQLite,
открывая файл в режиме WAL только на чтение, может создать рядом пустые
служебные `-wal` и `-shm`; данные не меняются. Вывод в консоль -- ASCII.

Запуск -- окружением расчёта (нужна геометрия), PowerShell, из C:\\gps-tools:

    & C:\\gps_venv\\Scripts\\python.exe -u tools\\gps_multipass_acceptance.py --db C:\\transport-report\\instance\\transport.db --dir C:\\transport-report\\instance --since 2026-10-14 --until 2026-10-28 --csv C:\\gps-tools\\check\\multipass_k1.csv *> C:\\gps-tools\\check\\multipass_k1.log
"""

import argparse
import csv
import hashlib
import os
import sys
import time
from datetime import date, datetime, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from agro_work import methods, store                                # noqa: E402
from agro_work import reconcile as rc                               # noqa: E402
from gps import multipass                                           # noqa: E402
from gps.area import work_sites                                     # noqa: E402
from gps_collector import config as collector_config                # noqa: E402
from tools.gps_alpha_replay import published_days                   # noqa: E402
from tools.gps_implausible_sites import kinds_of, read_day_readonly  # noqa: E402
from tools.gps_track_only_days import open_readonly                 # noqa: E402

# [REASON]: числа -- предрегистрация 22.10.2026 (§2.14). Окно приёмки не
# раньше 14.10: всё по 13.10 видели разведка и прогон 01.09-13.10, и правило,
# выбранное с оглядкой на эти сутки, на них же не проверяется.
ACCEPTANCE_FROM = '2026-10-14'
BENCH = os.path.join(REPO_ROOT, 'docs', 'gps_multipass_bench_set_2026-10-13.csv')
SYNTH_MAX = 100
SYNTH_MIN = 30
SYNTH_SIGMA = 1.0
SYNTH_SIGMA_REFERENCE = 2.0
P1_MIN_SHARE = 0.90
N1_MIN_DAYS = 40
N1_MAX_SHARE = 0.05
CLAIM_LOW, CLAIM_HIGH = 0.80, 1.25
CANDIDATE_FROM = 1.6
# [REASON]: у видов «… 2 марта утиш» объём -- площадь поля, один проход --
# не нарушение (владелец 14.10): заявка о числе проходов не говорит.
TWO_PASS_KIND = '2 марта'
SKIPPED_KINDS = ('excluded', 'special')
PROGRESS_EVERY = 200
LISTED_MAX = 30

EXIT_EVALUATED = 0
EXIT_CRASHED = 1
EXIT_BAD_INPUT = 2
EXIT_PARTIAL = 3
EXIT_NOT_EVALUATED = 4

VERDICT_MET = 'MET'
VERDICT_NOT_MET = 'NOT MET'
VERDICT_NOT_EVALUATED = 'NOT EVALUATED'

CSV_COLUMNS = ('day', 'wialon_id', 'kind', 'a7_ha', 'largest_ha', 'largest_km',
               'rows', 'repeat', 'two_dirs', 'k', 'reason', 'claim_ratio',
               'synthetic_k_1m', 'synthetic_k_2m')


class BadInput(Exception):
    """Ключи нельзя выполнить -- код выхода 2, ничего не считалось."""


def sha_key(day, unit):
    return hashlib.sha1(('%s:%d' % (day, unit)).encode('ascii')).hexdigest()


def bench_days(path):
    """[(день, объект, группа)] набора стенда."""
    with open(path, encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        missing = [name for name in ('day', 'wialon_id', 'group')
                   if name not in (reader.fieldnames or [])]
        if missing:
            raise BadInput('the bench set has no column(s) %s' % ', '.join(missing))
        return [((row['day'] or '').strip(), int(row['wialon_id']),
                 (row['group'] or '').strip()) for row in reader]


def measure(folder, day, unit):
    """Признаки одних суток; None -- точек на диске нет."""
    points = read_day_readonly(folder, unit, day)
    if not points:
        return None
    points = sorted(points)
    sites, _ = work_sites(points)
    return multipass.measure_day(points, sites)


def judged(measured):
    """Прошли ли сутки свидетельство (п. 2 правила K1)."""
    return (measured['largest_ha'] >= multipass.EVIDENCE_MIN_HA
            and measured['largest_km'] >= multipass.EVIDENCE_MIN_KM)


def calibrate(folder, bench, out=print):
    """(T1 или None, [значения rows], сутки калибровки без точек)."""
    values, missing = [], 0
    for day, unit, group in bench:
        if group not in multipass.CALIBRATION_GROUPS:
            continue
        measured = measure(folder, day, unit)
        if measured is None:
            missing += 1
            continue
        if judged(measured) and measured['rows'] is not None:
            values.append(measured['rows'])
    values.sort()
    t1 = rc.percentile(values, multipass.ROWS_PERCENTILE) if values else None
    return t1, values, missing


def judge_window(con, folder, since, until, t1, out=print):
    """Сутки окна: (строки, без точек, пропущено по виду, [(день, объект, ошибка)])."""
    days = published_days(con, since, until)
    kinds = kinds_of(con, sorted({unit for _day, unit, _v in days}))
    rows, missing, skipped, failed = [], 0, 0, []
    started = time.time()
    for index, (day, unit, _version) in enumerate(days, 1):
        if kinds.get(unit) in SKIPPED_KINDS:
            skipped += 1
            continue
        try:
            measured = measure(folder, day, unit)
        except Exception as error:                          # noqa: BLE001
            failed.append((day, unit, '%s: %s' % (type(error).__name__, error)))
            continue
        if measured is None:
            missing += 1
            continue
        k, reason = multipass.multiplicity(measured, t1)
        row = dict(measured, day=day, wialon_id=unit, kind=kinds.get(unit),
                   k=k, reason=reason)
        rows.append(row)
        if index % PROGRESS_EVERY == 0:
            out('  %d of %d machine-days, %.0f s' % (index, len(days),
                                                     time.time() - started))
    return rows, missing, skipped, failed


def synthetic(folder, rows, t1, out=print):
    """P1: синтетические двойные из суток `odin_prohod`.

    Ставит строкам synthetic_k_1m и synthetic_k_2m; возвращает те строки,
    на которых построены двойные, в порядке sha1.
    """
    single = sorted((row for row in rows if row['reason'] == multipass.K_ONE),
                    key=lambda row: sha_key(row['day'], row['wialon_id']))
    used = []
    for row in single[:SYNTH_MAX]:
        points = read_day_readonly(folder, row['wialon_id'], row['day'])
        if not points:
            continue
        seed = int(sha_key(row['day'], row['wialon_id'])[:8], 16)
        for sigma, key in ((SYNTH_SIGMA, 'synthetic_k_1m'),
                           (SYNTH_SIGMA_REFERENCE, 'synthetic_k_2m')):
            doubled = multipass.synthetic_double(points, sigma, seed)
            row[key] = multipass.multiplicity(multipass.measure_day(doubled), t1)[0]
        used.append(row)
    return used


def claims(con, since, until):
    """{(объект, день): объём / гектары GPS} заявок, годных для N1 и кандидатов.

    Заявка: метод «гектары», вид без «2 марта», окно -- одни сутки внутри
    окна приёмки, «работа была», светофор объёма посчитан.
    """
    if store.missing_tables(con):
        return None
    first, last = date.fromisoformat(since), date.fromisoformat(until)
    ctx = rc.Reconciliation(con, first, last)
    out = {}
    for row in ctx.forward_rows():
        app = row['app']
        if row['method'] != methods.METHOD_GA or row['verdict'] != rc.V_WORK:
            continue
        if row['volume_verdict'] is None or not row['gps_ha']:
            continue
        start, end = row['window']
        if start != end or not (first <= start <= last):
            continue
        name = (ctx.work_type_names.get(app.work_type_id)
                or app.row.get('work_type_name') or '')
        if TWO_PASS_KIND in name:
            continue
        out[(row['wialon_id'], start.isoformat())] = row['volume'] / row['gps_ha']
    return out


def share(hit, total):
    return None if not total else hit / total


def verdict(t1_days, p1_days, p1_two, n1_days, n1_two):
    """(вердикт, [почему не оценено])."""
    short = []
    if t1_days < multipass.CALIBRATION_MIN_DAYS:
        short.append('calibration days %d < %d' % (t1_days,
                                                   multipass.CALIBRATION_MIN_DAYS))
    if p1_days < SYNTH_MIN:
        short.append('P1 days %d < %d' % (p1_days, SYNTH_MIN))
    if n1_days < N1_MIN_DAYS:
        short.append('N1 days %d < %d' % (n1_days, N1_MIN_DAYS))
    if short:
        return VERDICT_NOT_EVALUATED, short
    met = (share(p1_two, p1_days) >= P1_MIN_SHARE
           and share(n1_two, n1_days) <= N1_MAX_SHARE)
    return (VERDICT_MET if met else VERDICT_NOT_MET), []


def _fmt(value):
    return '-' if value is None else '%.3f' % value


def write_csv(path, rows):
    with open(path, 'w', encoding='utf-8', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_COLUMNS)
        for row in rows:
            writer.writerow(['' if row.get(column) is None else row[column]
                             for column in CSV_COLUMNS])


def _date(option, value):
    try:
        return datetime.strptime(value, '%Y-%m-%d').date().isoformat()
    except (TypeError, ValueError):
        raise BadInput('%s must be YYYY-MM-DD' % option)


def check_inputs(args, today):
    since = _date('--since', args.since)
    until = _date('--until', args.until)
    if since < ACCEPTANCE_FROM:
        raise BadInput('--since %s is before %s: those days were seen while '
                       'the rule was being chosen' % (since, ACCEPTANCE_FROM))
    if until < since:
        raise BadInput('--until is before --since')
    if until >= today.isoformat():
        raise BadInput('--until must be before today (%s): the day is not '
                       'computed yet' % today.isoformat())
    for path, name in ((args.db, '--db'), (args.bench, '--bench')):
        if not os.path.isfile(path):
            raise BadInput('%s: no such file' % name)
    if not os.path.isdir(args.dir):
        raise BadInput('--dir: no such folder')
    if args.csv and not os.path.isdir(os.path.dirname(os.path.abspath(args.csv))):
        raise BadInput('--csv: the folder does not exist')
    return since, until


def run(args, today, out=print):
    since, until = check_inputs(args, today)
    bench = bench_days(args.bench)
    out('rule %s | acceptance %s .. %s' % (multipass.RULE_K1, since, until))
    t1, values, bench_missing = calibrate(args.dir, bench, out)
    calibration_rows = sum(1 for _d, _u, g in bench
                           if g in multipass.CALIBRATION_GROUPS)
    out('calibration: bench days %d | no points %d | judged %d | T1 (%d-th '
        'percentile of rows) = %s' % (calibration_rows, bench_missing,
                                      len(values),
                                      int(multipass.ROWS_PERCENTILE * 100),
                                      _fmt(t1)))
    # [REASON]: без калибровки порога нет, и первую ступень не проходит
    # ничего: вердикт всё равно «не оценено», а «рядами» без порога сутки
    # выглядели бы оценёнными.
    threshold = float('inf') if t1 is None else t1
    con = open_readonly(args.db)
    try:
        rows, missing, skipped, failed = judge_window(
            con, args.dir, since, until, threshold, out)
        claimed = claims(con, since, until)
    finally:
        con.close()
    out('machine-days: measured %d | published, no points %d | not ours or '
        'special %d | failed %d' % (len(rows), missing, skipped, len(failed)))
    for day, unit, error in failed[:LISTED_MAX]:
        out('  FAILED %s %d: %s' % (day, unit, error))
    reasons = {}
    for row in rows:
        reasons[row['reason']] = reasons.get(row['reason'], 0) + 1
    out('reasons: ' + ' | '.join('%s %d' % (name, reasons.get(name, 0)) for name in (
        multipass.K_NOT_JUDGED, multipass.K_NOT_ROWS, multipass.K_ONE,
        multipass.K_REPEAT, multipass.K_TWO_DIRS)))

    used = synthetic(args.dir, rows, threshold, out)
    p1_two = sum(1 for row in used if row['synthetic_k_1m'] == 2)
    p2_two = sum(1 for row in used if row['synthetic_k_2m'] == 2)
    out('P1 synthetic doubles, sigma %.1f m: %d of %d two passes = %s (need >= %.2f)'
        % (SYNTH_SIGMA, p1_two, len(used), _fmt(share(p1_two, len(used))),
           P1_MIN_SHARE))
    out('   sigma %.1f m (reference): %d of %d = %s'
        % (SYNTH_SIGMA_REFERENCE, p2_two, len(used), _fmt(share(p2_two, len(used)))))

    by_key = {(row['wialon_id'], row['day']): row for row in rows}
    n1_days = n1_two = cand_days = cand_two = 0
    if claimed is None:
        out('N1: agro-work tables are missing - no applications to compare')
    else:
        for key, ratio in claimed.items():
            row = by_key.get(key)
            if row is None:
                continue
            row['claim_ratio'] = round(ratio, 3)
            if row['reason'] == multipass.K_NOT_JUDGED:
                continue
            if CLAIM_LOW <= ratio <= CLAIM_HIGH:
                n1_days += 1
                n1_two += row['k'] == 2
            elif ratio >= CANDIDATE_FROM:
                cand_days += 1
                cand_two += row['k'] == 2
        out('N1 one-pass claims (volume / GPS ha %.2f-%.2f): %d of %d two passes '
            '= %s (need <= %.2f)' % (CLAIM_LOW, CLAIM_HIGH, n1_two, n1_days,
                                     _fmt(share(n1_two, n1_days)), N1_MAX_SHARE))
        out('candidates (volume / GPS ha >= %.1f, reference): %d of %d two passes = %s'
            % (CANDIDATE_FROM, cand_two, cand_days, _fmt(share(cand_two, cand_days))))

    doubled = sorted((row for row in rows if row['k'] == 2),
                     key=lambda row: (row['day'], row['wialon_id']))
    out('two-pass days: %d' % len(doubled))
    for row in doubled[:LISTED_MAX]:
        out('  %s %-6d %-17s repeat %s two_dirs %s rows %s'
            % (row['day'], row['wialon_id'], row['reason'], _fmt(row['repeat']),
               _fmt(row['two_dirs']), _fmt(row['rows'])))
    if args.csv:
        write_csv(args.csv, rows)
        out('csv: %s' % args.csv)

    result, short = verdict(len(values), len(used), p1_two, n1_days, n1_two)
    for line in short:
        out('NOT EVALUATED: %s' % line)
    out('VERDICT K1: %s' % result)
    if failed:
        return EXIT_PARTIAL
    return EXIT_NOT_EVALUATED if result == VERDICT_NOT_EVALUATED else EXIT_EVALUATED


def main(argv=None, today=None, out=print):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--db', required=True)
    parser.add_argument('--dir', required=True)
    parser.add_argument('--since', default=ACCEPTANCE_FROM)
    parser.add_argument('--until', required=True)
    parser.add_argument('--bench', default=BENCH)
    parser.add_argument('--csv')
    args = parser.parse_args(argv)
    today = today or datetime.now(collector_config.TZ).date()
    try:
        return run(args, today, out)
    except BadInput as error:
        out('ERROR: %s' % error)
        return EXIT_BAD_INPUT


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:                                       # noqa: BLE001
        import traceback
        traceback.print_exc()
        sys.exit(EXIT_CRASHED)
