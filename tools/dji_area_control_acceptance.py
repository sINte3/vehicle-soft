# -*- coding: utf-8 -*-
"""tools/dji_area_control_acceptance.py -- приёмка отчёта контроля площади.

DJI-AREA-PRODUCTIONIZATION-001. Сентябрь 01-18.09.2026 -- готовый эталон: его
числа получены слепым holdout и подтверждены живым блоком R на площадке.
Инструмент строит отчёт ТЕМ ЖЕ кодом, что страница и книга
(`dji_area.control_report`), и сверяет его с оракулом: не только итог, но и
разрез по дронам, число корректировок, ожидающих и требующих проверки, показ
цепочек A -> B -> C, ссылки DJI и видимость известного пропуска правила.

Только чтение: база открывается `mode=ro`, SHA-256 снимается до и после.
К DJI инструмент не обращается.

[REASON]: четыре записи с применением при плоском счётчике владелец визуально
признал фантомами, но оракул требует, чтобы машина оставила их на ПРОВЕРКЕ.
Приёмка, которая прошла бы при их автоматическом обнулении, проверяла бы
подгонку под вердикт, а не правило. Идентификаторов в коде нет -- только счёт.

Запуск (служба остановлена либо база -- копия):

  & "C:\\Program Files\\Python314\\python.exe" tools\\dji_area_control_acceptance.py --db instance\\transport.db --oracle docs\\DJI_AREA_SEPTEMBER_2026_ORACLE.json --out C:\\VehicleSoft_Area_Acceptance

  --recalc-summary PATH  -- сводка `dji_area_recalc.py --dry-run --json` того же
                            периода: нынешний код обязан не хотеть записать
                            ни одной строки

Коды возврата: 0 -- PASS; 1 -- ошибка аргументов или данных; 2 -- база не
найдена (файл НЕ создаётся); 3 -- FAIL, расхождения напечатаны. Вывод в
консоль только ASCII.
"""

import argparse
import hashlib
import io
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
from dji_area import control_report as cr  # noqa: E402

EXIT_PASS = 0
EXIT_USAGE = 1
EXIT_NO_DATABASE = 2
EXIT_FAIL = 3

HA_TOLERANCE = 0.00005


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def load_rows(con, date_from, date_to):
    """Текущие строки расчёта периода + борт по нику вылета."""
    nick = {int(r[0]): r[1] for r in con.execute(
        'SELECT dji_flight_id, nickname_raw FROM drone_flights')}
    rows = []
    for record in con.execute(
            'SELECT * FROM dji_area_calculations WHERE superseded_at IS NULL '
            'AND area_algorithm_version = ? AND report_start_date BETWEEN ? '
            'AND ? ORDER BY flight_id',
            (dji_area.AREA_ALGORITHM_VERSION, date_from.isoformat(),
             date_to.isoformat())):
        row = dict(record)
        row['report_start_date'] = date.fromisoformat(
            str(row['report_start_date'])[:10])
        label = nick.get(int(row['flight_id'])) or 'UNKNOWN'
        row['machine_key'] = row['machine_label'] = label
        rows.append(row)
    return rows


def _bridges_excluded(register, proven):
    """Мостики, попавшие в корректировки НЕ как самостоятельные цели.

    [REASON]: мостик -- любая запись цепочки, кроме «mode 4 с шириной», и он
    бывает сам целью собственной цепочки (mode 4 без ширины). Такой мостик
    исправляется как C, и это законно. Незаконно другое: исключить запись
    только за то, что она стоит между базой и целью.
    """
    proven_by_id = {r['flight_id']: r for r in proven}
    bridge_ids = {b for r in register for b in r['bridge_flight_ids']}
    return sum(1 for b in bridge_ids
               if b in proven_by_id and not proven_by_id[b]['base_flight_id'])


def observe(rows):
    """Наблюдаемые величины в гектарах -- то же, что показывает экран."""
    report = cr.build(rows, 'ru')
    total = report['total']
    register = report['register']
    proven = [r for r in register
              if r['accounting_class'] == acc.PHANTOM_PROVEN]
    by_drone = {}
    for drone in report['drones']:
        by_drone[drone['machine_label']] = {
            'records': drone['records'],
            'raw_ha': round(cr.ha(drone['raw_m2']), 4),
            'excluded_ha': round(cr.ha(drone['excluded_m2']), 4),
            'after_ha': round(cr.ha(drone['after_m2']), 4),
            'pending_ha': round(cr.ha(drone['pending_m2']), 4),
            'review_ha': round(cr.ha(drone['review_m2']), 4),
        }
    return {
        'records': total['records'],
        'raw_missing_records': total['raw_missing_records'],
        'raw_ha': round(cr.ha(total['raw_m2']), 4),
        'excluded_ha': round(cr.ha(total['excluded_m2']), 4),
        'excluded_records': total['excluded_records'],
        'after_ha': round(cr.ha(total['after_m2']), 4),
        'pending_ha': round(cr.ha(total['pending_m2']), 4),
        'pending_records': total['pending_records'],
        'review_ha': round(cr.ha(total['review_m2']), 4),
        'review_records': total['review_records'],
        'structural_candidates': sum(
            1 for r in rows if r.get('structural_candidate') in (True, 1)),
        'chains_shown': sum(1 for r in register if r['base_flight_id']),
        'proven_structural': sum(
            1 for r in proven if r['reason_code'] in (cr.EXPLAIN_REPEAT,
                                                      cr.EXPLAIN_PARTIAL)),
        'proven_by_control_only': sum(
            1 for r in proven if r['reason_code'] in (
                cr.EXPLAIN_CONTROL, cr.EXPLAIN_CONTROL_PARTIAL)),
        'review_application_with_flat_counter': sum(
            1 for r in register
            if r['reason_code'] == acc.R_APPLICATION_WITH_FLAT_COUNTER),
        'bridges_excluded': _bridges_excluded(register, proven),
        'links_ok': all(r['dji_url'] == cr.DJI_RECORD_URL % r['flight_id']
                        for r in register),
        'partition_holds': total['partition_holds'],
        'complete': total['complete'],
        'by_drone': by_drone,
    }, report


def compare(expected, observed, prefix=''):
    """Список расхождений. Числа с плавающей точкой -- с допуском."""
    problems = []
    for key, want in expected.items():
        name = prefix + key
        if key not in observed:
            problems.append('%s: missing in the observed report' % name)
            continue
        got = observed[key]
        if isinstance(want, dict):
            if set(want) != set(got):
                problems.append('%s: keys differ: only expected %s, only '
                                'observed %s' % (name,
                                                 sorted(set(want) - set(got)),
                                                 sorted(set(got) - set(want))))
            for sub in sorted(set(want) & set(got)):
                problems += compare(want[sub], got[sub],
                                    '%s[%s].' % (name, sub))
        elif isinstance(want, float):
            if got is None or abs(float(got) - want) > HA_TOLERANCE:
                problems.append('%s: expected %.4f, observed %s'
                                % (name, want, got))
        elif got != want:
            problems.append('%s: expected %r, observed %r' % (name, want, got))
    return problems


def formula_problems(observed):
    """Арифметика отчёта сходится сама с собой -- без оракула."""
    problems = []
    if abs(observed['raw_ha'] - observed['excluded_ha']
           - observed['after_ha']) > 2 * HA_TOLERANCE:
        problems.append('formula: RAW - excluded != after')
    for key in ('raw_ha', 'excluded_ha', 'pending_ha', 'review_ha'):
        drones = sum(d[key] for d in observed['by_drone'].values())
        if abs(drones - observed[key]) > 0.0005 * max(
                1, len(observed['by_drone'])):
            problems.append('formula: drones do not add up to %s (%.4f vs '
                            '%.4f)' % (key, drones, observed[key]))
    if not observed['partition_holds']:
        problems.append('formula: accounting partition does not hold')
    if not observed['links_ok']:
        problems.append('links: a register row does not link to its own '
                        'DJI record')
    if observed['bridges_excluded']:
        problems.append('bridge: %d bridge record(s) appear among the '
                        'corrections' % observed['bridges_excluded'])
    return problems


def recalc_summary_problems(summary, expected_records):
    """Сухой пересчёт НЫНЕШНИМ кодом обязан не хотеть записать ничего.

    [REASON]: строки расчёта на площадке записаны ревизией `reviewed-4`, а
    разворачивается код с исправленным замороженным `pipeline.py`. Утверждение
    «результаты сентября от правки не изменились» должно воспроизводиться там
    же, где лежат данные, и только чтением. Ворота идемпотентности для этого
    не годятся: они требуют `field_writes`, а сухой прогон их не считает.
    """
    problems = []
    if not isinstance(summary, dict):
        return ['recalc dry-run: the summary is not a JSON object']
    flights = summary.get('flights_in_period')
    if flights != expected_records:
        problems.append('recalc dry-run: flights_in_period is %r, the oracle '
                        'names %r' % (flights, expected_records))
    writes = summary.get('calc_writes')
    if not isinstance(writes, dict):
        problems.append('recalc dry-run: calc_writes is missing')
        return problems
    other = dict((key, count) for key, count in writes.items()
                 if key != 'unchanged' and count)
    if other:
        problems.append('recalc dry-run: the current code would write %s'
                        % json.dumps(other, sort_keys=True))
    if writes.get('unchanged') != flights:
        problems.append('recalc dry-run: unchanged is %r of %r'
                        % (writes.get('unchanged'), flights))
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Read-only acceptance of the DJI area control report.')
    parser.add_argument('--db', dest='db_path', required=True)
    parser.add_argument('--oracle', dest='oracle_path', required=True)
    parser.add_argument('--out', dest='out_dir')
    parser.add_argument('--recalc-summary', dest='recalc_summary',
                        metavar='PATH',
                        help='--json of a --dry-run recalculation of the same '
                             'period: it must want to write nothing')
    args = parser.parse_args(argv)

    try:
        with io.open(args.oracle_path, encoding='utf-8') as handle:
            oracle = json.load(handle)
        date_from = date.fromisoformat(oracle['period'][0])
        date_to = date.fromisoformat(oracle['period'][1])
        expected = oracle['expected']
    except (IOError, OSError, ValueError, KeyError) as exc:
        print('ERROR: cannot read the oracle: %s' % exc)
        return EXIT_USAGE
    if not os.path.exists(args.db_path):
        print('ERROR: database not found at %s - refusing to run.'
              % args.db_path)
        return EXIT_NO_DATABASE
    recalc_summary = None
    if args.recalc_summary:
        try:
            with io.open(args.recalc_summary, encoding='utf-8') as handle:
                recalc_summary = json.load(handle)
        except (IOError, OSError, ValueError) as exc:
            print('ERROR: cannot read the recalc summary: %s' % exc)
            return EXIT_USAGE

    before = file_sha256(args.db_path)
    con = sqlite3.connect('file:%s?mode=ro' % os.path.abspath(
        args.db_path).replace('\\', '/'), uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        rows = load_rows(con, date_from, date_to)
        observed, report = observe(rows)
    except sqlite3.Error as exc:
        print('ERROR: %s' % exc)
        return EXIT_USAGE
    finally:
        con.close()
    after = file_sha256(args.db_path)

    problems = compare(expected, observed) + formula_problems(observed)
    if args.recalc_summary:
        problems += recalc_summary_problems(recalc_summary,
                                            expected.get('records'))
    if before != after:
        problems.append('read-only: the database changed while it was read')

    print('DJI AREA CONTROL ACCEPTANCE %s .. %s' % (date_from, date_to))
    print('  algorithm          : %s' % dji_area.AREA_ALGORITHM_VERSION)
    print('  structural rule    : %s' % dji_area.STRUCTURAL_RULE_VERSION)
    for key in ('records', 'raw_missing_records', 'raw_ha', 'excluded_ha',
                'excluded_records', 'after_ha', 'pending_ha',
                'pending_records', 'review_ha', 'review_records',
                'structural_candidates', 'chains_shown', 'proven_structural',
                'proven_by_control_only',
                'review_application_with_flat_counter', 'bridges_excluded'):
        print('  %-38s %s' % (key, observed[key]))
    print('  %-14s %7s %10s %10s %10s %8s' % ('drone', 'flights', 'raw',
                                              'excluded', 'after', 'review'))
    for name in sorted(observed['by_drone'],
                       key=lambda n: -observed['by_drone'][n]['excluded_ha']):
        d = observed['by_drone'][name]
        print('  %-14s %7d %10.4f %10.4f %10.4f %8.4f'
              % (name.encode('ascii', 'replace').decode('ascii'),
                 d['records'], d['raw_ha'], d['excluded_ha'], d['after_ha'],
                 d['review_ha']))
    if args.recalc_summary:
        print('  %-38s %s' % ('recalc dry-run calc_writes', json.dumps(
            (recalc_summary or {}).get('calc_writes')
            if isinstance(recalc_summary, dict) else None, sort_keys=True)))
    for problem in problems:
        print('  MISMATCH: %s' % problem)
    print('  database sha256 unchanged: %s' % ('yes' if before == after
                                               else 'NO'))
    print('  VERDICT: %s' % ('PASS' if not problems else 'FAIL'))

    if args.out_dir:
        os.makedirs(args.out_dir, exist_ok=True)
        with io.open(os.path.join(args.out_dir, 'area_control_acceptance.json'),
                     'w', encoding='utf-8') as handle:
            json.dump({'verdict': 'PASS' if not problems else 'FAIL',
                       'problems': problems, 'observed': observed,
                       'db_sha256': before,
                       'recalc_dry_run': (
                           {'flights_in_period':
                            recalc_summary.get('flights_in_period'),
                            'calc_writes': recalc_summary.get('calc_writes')}
                           if isinstance(recalc_summary, dict) else None),
                       'algorithm': dji_area.AREA_ALGORITHM_VERSION,
                       'structural_rule': dji_area.STRUCTURAL_RULE_VERSION},
                      handle, ensure_ascii=False, indent=2, sort_keys=True)
        book = cr.build_workbook(
            report, 'ru', period=(date_from.isoformat(), date_to.isoformat()),
            versions={'area_algorithm': dji_area.AREA_ALGORITHM_VERSION,
                      'structural_rule': dji_area.STRUCTURAL_RULE_VERSION,
                      'accounting_classes': acc.ACCOUNTING_CLASSES_VERSION,
                      'report': cr.REPORT_VERSION})
        book.save(os.path.join(args.out_dir, 'area_control_report.xlsx'))
    return EXIT_PASS if not problems else EXIT_FAIL


if __name__ == '__main__':
    sys.exit(main())
