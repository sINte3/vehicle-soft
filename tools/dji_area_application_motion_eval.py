# -*- coding: utf-8 -*-
"""tools/dji_area_application_motion_eval.py -- сухая оценка правила движения.

DJI-AREA-APPLICATION-MOTION-001. Отвечает на один вопрос: что НЫНЕШНИЙ код
сделает с записями, которые сейчас стоят на проверке с причиной
`APPLICATION_WITH_FLAT_COUNTER`, -- и не меняет при этом ничего.

Только чтение. База открывается `mode=ro` и для чтения строк, и для сухого
пересчёта (`pipeline.recalculate(read_only=True)`); SHA-256 файла снимается
до и после. К DJI инструмент не обращается. Пишет только файлы в `--out`.

Что считает:

1. текущие строки расчёта (`AREA_ALGORITHM_VERSION`, `superseded_at IS NULL`)
   и их учётный класс -- «до»;
2. цели: REVIEW с причиной `APPLICATION_WITH_FLAT_COUNTER` плюс названные
   `--flight-id` (например, уже решённые администратором);
3. сухой пересчёт целей нынешним кодом -- «после»: класс, причина, RAW и
   отпечаток строки, которую пересчёт записал бы;
4. движение при применении по телу V4 каждой цели (`v4.application_motion`);
5. инварианты, нарушение которых даёт код 3:
   * RAW ни одной цели не изменился;
   * `billable_area_m2` пуст во всех текущих строках и в каждой строке,
     которую пересчёт записал бы;
   * запись, где правило сработало, пересчёт обязан переписать (иначе
     правило до базы не дойдёт -- ловушка `unchanged`);
   * файл базы побайтно тот же.
   Запись, которую пересчёт переписал бы БЕЗ срабатывания правила, --
   предупреждение, а не отказ: её вход изменился с прошлого пересчёта по
   другой причине, и это надо увидеть, но правило тут ни при чём.
6. `motion_eval.csv`, `motion_eval.json` и две готовые команды адресного
   пересчёта (`recalc_dry_run.txt`, `recalc_apply.txt`) -- с настоящими id,
   датами и путями, без заполнителей.

Запуск (на хосте приложения, рабочий каталог -- корень репозитория; служба
может работать -- база только читается):

  & "C:\\Program Files\\Python314\\python.exe" tools\\dji_area_application_motion_eval.py --db instance\\transport.db --out C:\\VehicleSoft_Area_Motion_Eval --flight-id 679813767 --flight-id 693319955 --flight-id 698068932 --flight-id 687610350

  --from / --to YYYY-MM-DD  -- ограничить строки периодом (по умолчанию все)

Коды возврата: 0 -- оценка выполнена, инварианты держатся; 1 -- ошибка
аргументов или данных; 2 -- база не найдена (файл НЕ создаётся); 3 --
нарушен инвариант, причины напечатаны. Вывод в консоль только ASCII.

ОТКАТ. Кода: удалить файл, его никто не импортирует. Данных: в базу не пишет.
"""

import argparse
import csv
import hashlib
import json
import os
import sqlite3
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import dji_area  # noqa: E402
from dji_area import accounting as acc  # noqa: E402
from dji_area import control_store  # noqa: E402
from dji_area import pipeline  # noqa: E402
from dji_area import resolver as rs  # noqa: E402
from dji_area import store  # noqa: E402
from dji_area import v4 as v4mod  # noqa: E402

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_NO_DATABASE = 2
EXIT_INVARIANT = 3

PYTHON = r'C:\Program Files\Python314\python.exe'
M2_PER_HA = 10000.0

CSV_COLUMNS = (
    'flight_id', 'report_start_date', 'raw_m2', 'class_before',
    'reason_before', 'class_after', 'reason_after', 'rule_fired',
    'would_write', 'application_frames', 'displaced_frames',
    'unobserved_frames', 'application_path_m', 'max_speed_mps',
    'velocity_complete_frames', 'step_observed_frames',
    'structural_match', 'v4_sha256', 'active_decision',
    'decision_becomes_stale',
)


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def current_rows(con, date_from, date_to):
    sql = ('SELECT * FROM dji_area_calculations WHERE superseded_at IS NULL '
           'AND area_algorithm_version = ?')
    args = [dji_area.AREA_ALGORITHM_VERSION]
    if date_from is not None:
        sql += ' AND report_start_date >= ?'
        args.append(date_from.isoformat())
    if date_to is not None:
        sql += ' AND report_start_date <= ?'
        args.append(date_to.isoformat())
    return [dict(r) for r in con.execute(sql + ' ORDER BY flight_id', args)]


def motion_of(con, root, v4_revision_id):
    """(движение, sha256 тела) по ревизии V4 либо (None, None)."""
    if not v4_revision_id:
        return None, None
    rev = store.revision_by_id(con, v4_revision_id)
    if rev is None:
        return None, None
    try:
        body = store.read_body(root, rev)
        return v4mod.application_motion(v4mod.decode_v4(body)), rev['sha256']
    except (store.StoreError, OSError, v4mod.V4DecodeError):
        return None, rev['sha256']


def _day(value):
    return date.fromisoformat(str(value)[:10])


def evaluate(db_path, date_from=None, date_to=None, named=()):
    """Оценка целиком; возвращает (отчёт, список нарушений)."""
    root = store.source_root(os.path.abspath(db_path))
    con = store.connect(db_path, read_only=True)
    try:
        rows = current_rows(con, date_from, date_to)
        by_id = {int(r['flight_id']): r for r in rows}
        before = {fid: acc.classify(r) for fid, r in by_id.items()}
        billable_rows = sum(1 for r in rows
                            if r.get('billable_area_m2') is not None)
        flat_app = sorted(fid for fid, d in before.items()
                          if d['reason'] == acc.R_APPLICATION_WITH_FLAT_COUNTER)
        missing_named = sorted(int(f) for f in named if int(f) not in by_id)
        targets = sorted(set(flat_app) | {int(f) for f in named
                                          if int(f) in by_id})
        decisions = {}
        if targets and control_store.tables_present(con):
            decisions = control_store.active_decisions(con, targets)
        motions = {}
        for fid in targets:
            motions[fid] = motion_of(con, root, by_id[fid].get('v4_revision_id'))
    finally:
        con.close()

    report = {
        'database': os.path.abspath(db_path),
        'area_algorithm_version': dji_area.AREA_ALGORITHM_VERSION,
        'application_motion_rule_version':
            dji_area.APPLICATION_MOTION_RULE_VERSION,
        'current_rows': len(rows),
        'billable_nonnull_current_rows': billable_rows,
        'named_without_current_row': missing_named,
        'targets': [],
        'totals': {},
    }
    problems = []
    if billable_rows:
        problems.append('billable_area_m2 is filled in %d current row(s)'
                        % billable_rows)
    if not targets:
        return report, problems

    days = [_day(by_id[fid]['report_start_date']) for fid in targets]
    summary = pipeline.recalculate(db_path, min(days), max(days), apply=False,
                                   flight_ids=targets, collect_rows=True,
                                   read_only=True)
    after_by_id = {int(line['flight_id']): line
                   for line in summary.get('flights') or []}

    totals = {'targets': len(targets), 'flat_app_before': len(flat_app),
              'flat_app_before_raw_m2': 0.0, 'fired': 0, 'fired_raw_m2': 0.0,
              'review_after': 0, 'review_after_raw_m2': 0.0,
              'would_write': 0, 'would_write_without_rule': 0}
    for fid in targets:
        stored = by_id[fid]
        was = before[fid]
        line = after_by_id.get(fid)
        if line is None:
            problems.append('%d: the dry run produced no line' % fid)
            continue
        now = acc.classify(line)
        fired = rs.F_APPLICATION_WITHOUT_MOVING_WORK in (
            line.get('anomaly_flags') or [])
        would_write = (line.get('calculation_input_hash')
                       != stored.get('calculation_input_hash'))
        raw = was['raw_area_m2']
        motion, v4_sha = motions.get(fid, (None, None))
        decision = decisions.get(fid)
        item = {
            'flight_id': fid,
            'report_start_date': str(stored.get('report_start_date'))[:10],
            'raw_m2': raw,
            'class_before': was['accounting_class'],
            'reason_before': was['reason'],
            'class_after': now['accounting_class'],
            'reason_after': now['reason'],
            'rule_fired': fired,
            'would_write': would_write,
            'structural_match': was['structural_match'],
            'v4_sha256': v4_sha,
            'active_decision': decision['decision_type'] if decision else None,
            'decision_becomes_stale': bool(decision) and would_write,
        }
        for key in ('application_frames', 'displaced_frames',
                    'unobserved_frames', 'application_path_m', 'max_speed_mps',
                    'velocity_complete_frames', 'step_observed_frames'):
            item[key] = motion.get(key) if motion else None
        report['targets'].append(item)

        if now['raw_area_m2'] != raw:
            problems.append('%d: RAW changed %r -> %r'
                            % (fid, raw, now['raw_area_m2']))
        if line.get('billable_area_m2') is not None:
            problems.append('%d: the recalculated row would fill '
                            'billable_area_m2' % fid)
        if fired and not would_write:
            problems.append('%d: the rule fired but the recalculation would '
                            'answer unchanged' % fid)
        if fid in flat_app:
            totals['flat_app_before_raw_m2'] += raw or 0.0
        if fired:
            totals['fired'] += 1
            totals['fired_raw_m2'] += raw or 0.0
        if now['reason'] == acc.R_APPLICATION_WITH_FLAT_COUNTER:
            totals['review_after'] += 1
            totals['review_after_raw_m2'] += raw or 0.0
        if would_write:
            totals['would_write'] += 1
            if not fired:
                totals['would_write_without_rule'] += 1
    report['totals'] = totals
    report['recalc_period'] = [min(days).isoformat(), max(days).isoformat()]
    report['recalc_dry_run_calc_writes'] = summary.get('calc_writes')
    return report, problems


def recalc_command(report, mode, out_dir):
    ids = ' '.join('--flight-id %d' % t['flight_id']
                   for t in report['targets'])
    tag = 'dry_run' if mode == '--dry-run' else 'apply'
    return ('& "%s" "%s" --db "%s" --from %s --to %s %s %s --json "%s" '
            '--rows "%s"' % (
                PYTHON, os.path.join(ROOT, 'tools', 'dji_area_recalc.py'),
                report['database'], report['recalc_period'][0],
                report['recalc_period'][1], mode, ids,
                os.path.join(out_dir, 'recalc_%s.json' % tag),
                os.path.join(out_dir, 'recalc_%s_rows.json' % tag)))


def write_outputs(report, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, 'motion_eval.json'), 'w',
              encoding='utf-8') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2,
                  sort_keys=True, default=str)
    with open(os.path.join(out_dir, 'motion_eval.csv'), 'w', newline='',
              encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for item in report['targets']:
            writer.writerow({k: item.get(k) for k in CSV_COLUMNS})
    if report['targets']:
        for mode, name in (('--dry-run', 'recalc_dry_run.txt'),
                           ('--apply', 'recalc_apply.txt')):
            with open(os.path.join(out_dir, name), 'w',
                      encoding='utf-8') as handle:
                handle.write(recalc_command(report, mode, out_dir) + '\n')


def _ha(m2):
    return (m2 or 0.0) / M2_PER_HA


def print_report(report, problems):
    totals = report['totals']
    print('DJI-AREA-APPLICATION-MOTION-001 dry evaluation (read-only)')
    print('  algorithm         : %s' % report['area_algorithm_version'])
    print('  motion rule       : %s'
          % report['application_motion_rule_version'])
    print('  current rows      : %d, billable filled: %d'
          % (report['current_rows'], report['billable_nonnull_current_rows']))
    if report['named_without_current_row']:
        print('  named, no row     : %s' % ' '.join(
            str(f) for f in report['named_without_current_row']))
    if not report['targets']:
        print('  no APPLICATION_WITH_FLAT_COUNTER records and no named ids')
    else:
        print('  flat+application  : %d records, RAW %.4f ha'
              % (totals['flat_app_before'],
                 _ha(totals['flat_app_before_raw_m2'])))
        print('  rule fired        : %d records, RAW %.4f ha -> proven zero'
              % (totals['fired'], _ha(totals['fired_raw_m2'])))
        print('  still in review   : %d records, RAW %.4f ha'
              % (totals['review_after'], _ha(totals['review_after_raw_m2'])))
        print('  would write       : %d (without the rule firing: %d)'
              % (totals['would_write'], totals['would_write_without_rule']))
        print('  %-10s %-8s %-10s %-10s %5s %5s %5s %8s %6s %s' % (
            'flight', 'raw_ha', 'before', 'after', 'app', 'moved', 'unobs',
            'path_m', 'maxv', 'decision'))

        def cell(value, fmt='%s'):
            return '-' if value is None else fmt % value

        for t in report['targets']:
            print('  %-10d %-8.4f %-10s %-10s %5s %5s %5s %8s %6s %s' % (
                t['flight_id'], _ha(t['raw_m2']), t['class_before'][:10],
                t['class_after'][:10], cell(t['application_frames']),
                cell(t['displaced_frames']), cell(t['unobserved_frames']),
                cell(t['application_path_m'], '%.3f'),
                cell(t['max_speed_mps'], '%.2f'),
                (t['active_decision'] or '-')
                + (' (stale)' if t['decision_becomes_stale'] else '')))
    for problem in problems:
        print('  INVARIANT: %s' % problem.encode('ascii', 'replace').decode())
    print('  verdict           : %s' % ('FAIL' if problems else 'OK'))


def build_parser():
    parser = argparse.ArgumentParser(
        prog='dji_area_application_motion_eval.py',
        description='Read-only evaluation of the flat-counter application '
                    'motion rule on the current calculations.')
    parser.add_argument('--db', dest='db_path',
                        default=os.path.join(ROOT, 'instance', 'transport.db'))
    parser.add_argument('--from', dest='date_from', metavar='YYYY-MM-DD')
    parser.add_argument('--to', dest='date_to', metavar='YYYY-MM-DD')
    parser.add_argument('--flight-id', dest='flight_ids', action='append',
                        type=int, default=[], metavar='ID')
    parser.add_argument('--out', dest='out_dir')
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not os.path.exists(args.db_path):
        print('database not found at %s - refusing to run'
              % args.db_path.encode('ascii', 'replace').decode())
        return EXIT_NO_DATABASE
    try:
        date_from = date.fromisoformat(args.date_from) \
            if args.date_from else None
        date_to = date.fromisoformat(args.date_to) if args.date_to else None
    except ValueError as exc:
        print('bad date: %s' % exc)
        return EXIT_USAGE
    before = file_sha256(args.db_path)
    try:
        report, problems = evaluate(args.db_path, date_from, date_to,
                                    args.flight_ids)
    except (store.StoreError, pipeline.PipelineError,
            sqlite3.Error) as exc:
        print('evaluation failed: %s: %s' % (
            type(exc).__name__, str(exc).encode('ascii', 'replace').decode()))
        return EXIT_USAGE
    after = file_sha256(args.db_path)
    report['database_sha256_before'] = before
    report['database_sha256_after'] = after
    if before != after:
        problems.append('the database file changed during a read-only run')
    report['problems'] = problems
    out_dir = os.path.abspath(args.out_dir) if args.out_dir else None
    if out_dir:
        write_outputs(report, out_dir)
    print_report(report, problems)
    if out_dir:
        print('  out               : %s'
              % out_dir.encode('ascii', 'replace').decode())
    return EXIT_INVARIANT if problems else EXIT_OK


if __name__ == '__main__':
    sys.exit(main())
