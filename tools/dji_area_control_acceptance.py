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
Это требование ИСТОРИЧЕСКОГО оракула `DJI_AREA_SEPTEMBER_2026_ORACLE.json`
(алгоритм до правила DJI-AREA-RETAINED-FOOTPRINT-001); файл не меняется.

Запуск (служба остановлена либо база -- копия):

  & "C:\\Program Files\\Python314\\python.exe" tools\\dji_area_control_acceptance.py --db instance\\transport.db --oracle docs\\DJI_AREA_SEPTEMBER_2026_ORACLE.json --out C:\\VehicleSoft_Area_Acceptance

  --recalc-summary PATH  -- сводка `dji_area_recalc.py --dry-run --json` того же
                            периода: нынешний код обязан не хотеть записать
                            ни одной строки

ПЕРЕХОД (DJI-AREA-RETAINED-FOOTPRINT-001). Оракул с разделом `transition`
описывает не устойчивое состояние, а смену модели: выборочное правило
законно хочет переписать ровно названные строки -- и только их. Такой оракул
принимается только с явной фазой:

  --phase pre-apply   база ещё со строками прежнего кода. Нужны
                      `--recalc-summary` и `--recalc-rows` ОДНОГО сухого
                      прогона периода (`dji_area_recalc.py --dry-run --json
                      --rows`). Отчёт строится из строк базы, в которых
                      решение заменено решением прогона, -- тем же
                      `control_report` -- и сверяется с оракулом. Переписаны
                      обязаны быть ровно `expected_rewrites`, каждая -- этим
                      правилом и из «применение при плоском счётчике»;
                      остальные строки сохраняют отпечаток и решение; RAW не
                      меняется; billable пуст; `must_stay_review` остаётся на
                      проверке; отрицательные контрольные не становятся
                      фантомами;
  --phase post-apply  после применения: отчёт по строкам базы совпадает с
                      оракулом, `--recalc-summary` ВТОРОГО сухого прогона --
                      `unchanged` по всем строкам периода и ничего больше;
                      те же проверки состояния; `--apply-summary` (если
                      передан) -- применение записало ровно
                      `expected_rewrites` новыми строками.

[REASON]: `would_write > 0` само по себе ничего не доказывает -- так же
выглядела бы и любая регрессия. Доказательство перехода -- совпадение
МНОЖЕСТВА переписываемых строк с названным в оракуле и их причина.
Идентификаторы по-прежнему живут в оракуле, а не в коде.

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
from dji_area import footprint as fp  # noqa: E402

EXIT_PASS = 0
EXIT_USAGE = 1
EXIT_NO_DATABASE = 2
EXIT_FAIL = 3

HA_TOLERANCE = 0.00005
M2_PER_HA = 10000.0

PHASE_PRE_APPLY = 'pre-apply'
PHASE_POST_APPLY = 'post-apply'
PHASES = (PHASE_PRE_APPLY, PHASE_POST_APPLY)

# Решение строки, как его отдаёт построчный сухой прогон
# (`pipeline._flight_line`). Проекция ставит эти поля на место решения
# сохранённой строки; всё прочее (день, борт, мостики, ревизии) остаётся от
# строки базы -- правило его не меняет.
DRY_DECISION_FIELDS = (
    'raw_area_m2', 'corrected_recorded_area_m2', 'controller_delta_area_m2',
    'counter_observed_delta_m2', 'counter_zero_default_delta_m2',
    'area_status', 'evidence_status', 'area_method', 'area_confidence',
    'counter_baseline_status', 'counter_window_quality',
    'application_activity', 'application_channel_quality',
    'application_evidence_kind', 'application_without_area',
    'structural_candidate', 'candidate_base_flight_id',
    'scalar_source_check', 'overlap_group_id', 'aggregation_eligibility',
    'calculation_input_hash', 'billable_area_m2',
)
# Что обязано совпасть у строки, чей отпечаток прогон сохранил.
SAME_WHEN_KEPT = ('raw_area_m2', 'corrected_recorded_area_m2', 'area_status',
                  'aggregation_eligibility', 'candidate_base_flight_id')
PHANTOM_CLASSES = (acc.PHANTOM_PROVEN, acc.PHANTOM_STRUCTURAL)


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


# ─── Переход модели (DJI-AREA-RETAINED-FOOTPRINT-001) ────────────────────────

def _ids(values, at_most=12):
    values = sorted(values)
    text = ', '.join(str(v) for v in values[:at_most])
    return text + (' and %d more' % (len(values) - at_most)
                   if len(values) > at_most else '')


def _flag_set(row):
    flags = row.get('anomaly_flags')
    if flags is None:
        try:
            flags = json.loads(row.get('anomaly_flags_json') or '[]')
        except ValueError:
            flags = []
    return set(flags or [])


def transition_ids(transition, key):
    return sorted(int(f) for f in (transition.get(key) or []))


def load_dry_rows(path):
    """Строки `dji_area_recalc.py --rows`: список словарей с flight_id."""
    with io.open(path, encoding='utf-8') as handle:
        rows = json.load(handle)
    if not isinstance(rows, list) or not all(
            isinstance(r, dict) and 'flight_id' in r for r in rows):
        raise ValueError('not a list of rows with flight_id')
    return rows


def project(stored, dry_rows):
    """Строки, которые запишет пересчёт: строка базы с решением прогона.

    Возвращает (строки проекции, переписываемые flight_id, расхождения).

    [REASON]: проекция строится ТЕМ ЖЕ отчётом, что страница, из строк базы, а
    не второй реализацией расчёта. У строки, чей отпечаток прогон сохранил,
    решение обязано совпасть с записанным: иначе «отпечаток тот же» перестал
    бы значить «результат тот же», и переход прятал бы чужую правку.
    """
    by_id = {int(r['flight_id']): r for r in stored}
    dry, problems = {}, []
    for line in dry_rows:
        fid = int(line['flight_id'])
        if fid in dry:
            problems.append('dry-run rows: flight %d appears twice' % fid)
        dry[fid] = line
    uncovered = sorted(set(by_id) - set(dry))
    if uncovered:
        problems.append('dry-run rows do not cover %d stored row(s): %s'
                        % (len(uncovered), _ids(uncovered)))
    projected, rewrites = [], []
    for fid in sorted(set(by_id) | set(dry)):
        row, line = by_id.get(fid), dry.get(fid)
        if line is None:
            projected.append(row)
            continue
        if row is None:
            rewrites.append(fid)
            problems.append('dry-run: flight %d has no stored row -- the '
                            'recalculation would create it' % fid)
            continue
        new = dict(row)
        for key in DRY_DECISION_FIELDS:
            new[key] = line.get(key)
        flags = list(line.get('anomaly_flags') or [])
        new['anomaly_flags'] = flags
        new['anomaly_flags_json'] = json.dumps(flags)
        if line.get('calculation_input_hash') \
                != row.get('calculation_input_hash'):
            rewrites.append(fid)
        else:
            differ = [key for key in SAME_WHEN_KEPT
                      if new.get(key) != row.get(key)]
            if bool(new.get('structural_candidate')) \
                    != bool(row.get('structural_candidate')):
                differ.append('structural_candidate')
            if _flag_set(new) != _flag_set(row):
                differ.append('anomaly_flags')
            if differ:
                problems.append('flight %d keeps its fingerprint but its '
                                'decision would change: %s'
                                % (fid, ', '.join(differ)))
        projected.append(new)
    return projected, rewrites, problems


def rule_problems(oracle):
    """Оракул перехода описывает ИМЕННО это правило с этими параметрами."""
    rule = oracle.get('rule') or {}
    problems = []
    for key, running in (
            ('area_algorithm', dji_area.AREA_ALGORITHM_VERSION),
            ('rule_version', dji_area.RETAINED_FOOTPRINT_RULE_VERSION),
            ('footprint_to_raw_max', fp.FOOTPRINT_TO_RAW_MAX),
            ('width_envelope_m', fp.FOOTPRINT_WIDTH_ENVELOPE_M)):
        if rule.get(key) != running:
            problems.append('rule: the oracle names %s %r, the code runs %r'
                            % (key, rule.get(key), running))
    flag = (oracle.get('transition') or {}).get('rule_flag')
    if flag != acc.R_RETAINED_NEGLIGIBLE_FOOTPRINT:
        problems.append('rule: the oracle names the flag %r, the code writes '
                        '%r' % (flag, acc.R_RETAINED_NEGLIGIBLE_FOOTPRINT))
    return problems


def state_problems(transition, rows):
    """Состояние периода после перехода (проекция либо база после apply).

    Возвращает (расхождения, заметки для печати).
    """
    by_id = {int(r['flight_id']): r for r in rows}
    flag = transition.get('rule_flag')
    problems, notes = [], []
    rewrite_raw = 0.0
    for fid in transition_ids(transition, 'expected_rewrites'):
        row = by_id.get(fid)
        if row is None:
            problems.append('transition: expected rewrite %d is not in the '
                            'period' % fid)
            continue
        out = acc.classify(row)
        if flag not in _flag_set(row) \
                or out['accounting_class'] != acc.PHANTOM_PROVEN \
                or out['reason'] != flag:
            problems.append('transition: %d is not proven by the rule: %s / '
                            '%s' % (fid, out['accounting_class'],
                                    out['reason']))
        rewrite_raw += row.get('raw_area_m2') or 0.0
    want = transition.get('expected_rewrites_raw_ha')
    if want is not None and abs(rewrite_raw / M2_PER_HA - want) \
            > HA_TOLERANCE:
        problems.append('transition: RAW of the expected rewrites is %.4f ha, '
                        'the oracle names %.4f' % (rewrite_raw / M2_PER_HA,
                                                   want))
    # [REASON]: запись с крошечным следом, но без структурного совпадения, --
    # доказательство того, что следа НЕДОСТАТОЧНО. Стань она фантомом, пропал
    # бы структурный вентиль правила, а итог сдвинулся бы всего на её RAW.
    for fid in transition_ids(transition, 'must_stay_review'):
        row = by_id.get(fid)
        if row is None:
            problems.append('transition: %d must stay in review but is not in '
                            'the period' % fid)
            continue
        out = acc.classify(row)
        if out['accounting_class'] != acc.REVIEW or flag in _flag_set(row):
            problems.append('transition: %d must stay REVIEW, it is %s / %s'
                            % (fid, out['accounting_class'], out['reason']))
        else:
            notes.append('must stay REVIEW %d: %s' % (fid, out['reason']))
    for fid in transition_ids(transition, 'negative_controls'):
        row = by_id.get(fid)
        if row is None:
            notes.append('negative control %d: outside the period' % fid)
            continue
        out = acc.classify(row)
        if out['accounting_class'] in PHANTOM_CLASSES \
                or flag in _flag_set(row):
            problems.append('transition: negative control %d becomes %s / %s'
                            % (fid, out['accounting_class'], out['reason']))
        else:
            notes.append('negative control %d: %s' % (
                fid, out['accounting_class']))
    billable = sorted(fid for fid, r in by_id.items()
                      if r.get('billable_area_m2') is not None)
    if billable:
        problems.append('billable: %d row(s) carry billable_area_m2: %s'
                        % (len(billable), _ids(billable)))
    return problems, notes


def pre_apply_problems(transition, stored, projected, rewrites, summary,
                       dry_rows, records):
    """Переход до применения: переписать хотят ровно названные строки."""
    problems = []
    expected = set(transition_ids(transition, 'expected_rewrites'))
    got = set(rewrites)
    extra, missing = sorted(got - expected), sorted(expected - got)
    if extra:
        problems.append('transition: %d unexpected would_write: %s'
                        % (len(extra), _ids(extra)))
    if missing:
        problems.append('transition: %d expected rewrite(s) would not be '
                        'written: %s' % (len(missing), _ids(missing)))
    before = {int(r['flight_id']): r for r in stored}
    after = {int(r['flight_id']): r for r in projected}
    for fid in sorted(got & expected):
        reason = acc.classify(before[fid])['reason']
        if reason != acc.R_APPLICATION_WITH_FLAT_COUNTER:
            problems.append('transition: %d was %s before the rule, not a '
                            'flat counter waiting for a human' % (fid, reason))
    raw_changed = sorted(fid for fid, row in after.items() if fid in before
                         and row.get('raw_area_m2')
                         != before[fid].get('raw_area_m2'))
    if raw_changed:
        problems.append('RAW: the recalculation would change RAW of %d '
                        'row(s): %s' % (len(raw_changed), _ids(raw_changed)))
    billable = sorted(fid for fid, r in before.items()
                      if r.get('billable_area_m2') is not None)
    if billable:
        problems.append('billable: %d stored row(s) carry billable_area_m2: '
                        '%s' % (len(billable), _ids(billable)))
    if not isinstance(summary, dict):
        return problems + ['recalc dry-run: the summary is not a JSON object']
    flights = summary.get('flights_in_period')
    if flights != records:
        problems.append('recalc dry-run: flights_in_period is %r, the oracle '
                        'names %r' % (flights, records))
    if flights != len(dry_rows):
        problems.append('recalc dry-run: the summary counts %r flights and '
                        'the rows %d -- not one run' % (flights, len(dry_rows)))
    writes = summary.get('calc_writes')
    if not isinstance(writes, dict):
        return problems + ['recalc dry-run: calc_writes is missing']
    other = dict((key, count) for key, count in writes.items()
                 if key not in ('unchanged', 'would_write') and count)
    if other:
        problems.append('recalc dry-run: the code would write %s'
                        % json.dumps(other, sort_keys=True))
    if writes.get('would_write', 0) != len(expected):
        problems.append('recalc dry-run: would_write is %r, the transition '
                        'names exactly %d' % (writes.get('would_write', 0),
                                              len(expected)))
    if isinstance(flights, int) \
            and writes.get('unchanged') != flights - len(expected):
        problems.append('recalc dry-run: unchanged is %r of %r'
                        % (writes.get('unchanged'), flights))
    return problems


def apply_summary_problems(summary, expected_writes):
    """Применение записало ровно названные строки -- новыми, не иными."""
    if not isinstance(summary, dict):
        return ['apply: the summary is not a JSON object']
    writes = summary.get('calc_writes')
    if not isinstance(writes, dict):
        return ['apply: calc_writes is missing']
    problems = []
    if writes.get('new', 0) != expected_writes:
        problems.append('apply: %r new row(s), the transition names exactly '
                        '%d' % (writes.get('new', 0), expected_writes))
    other = dict((key, count) for key, count in writes.items()
                 if key not in ('new', 'unchanged') and count)
    if other:
        problems.append('apply: unexpected writes %s'
                        % json.dumps(other, sort_keys=True))
    flights = summary.get('flights_in_period')
    total = sum(count for count in writes.values()
                if isinstance(count, int))
    if not isinstance(flights, int) or total != flights:
        problems.append('apply: the writes %s do not add up to '
                        'flights_in_period %r'
                        % (json.dumps(writes, sort_keys=True), flights))
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
                             'period: it must want to write nothing (or, in '
                             'the pre-apply phase, exactly the transition)')
    parser.add_argument('--phase', choices=PHASES,
                        help='required for an oracle with a transition: '
                             'pre-apply projects the dry run, post-apply '
                             'accepts the steady state after the apply')
    parser.add_argument('--recalc-rows', dest='recalc_rows', metavar='PATH',
                        help='--rows of the same dry run (pre-apply only)')
    parser.add_argument('--apply-summary', dest='apply_summary',
                        metavar='PATH',
                        help='--json of the apply (post-apply only): exactly '
                             'the expected rewrites, as new rows')
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
    transition = oracle.get('transition')
    # [REASON]: фаза выбирается явно и никогда не угадывается по данным.
    # Оракул перехода без фазы и фаза без оракула перехода -- ошибка
    # командной строки: «принять переход» и «принять покой» -- разные
    # утверждения, и одно не должно тихо подменять другое.
    if transition is not None and not args.phase:
        print('ERROR: the oracle describes a transition -- choose --phase '
              'pre-apply or --phase post-apply')
        return EXIT_USAGE
    if args.phase and not isinstance(transition, dict):
        print('ERROR: --phase needs an oracle with a transition section')
        return EXIT_USAGE
    if args.phase == PHASE_PRE_APPLY and not (args.recalc_summary
                                              and args.recalc_rows):
        print('ERROR: --phase pre-apply needs --recalc-summary and '
              '--recalc-rows of one dry run')
        return EXIT_USAGE
    if args.phase == PHASE_POST_APPLY and not args.recalc_summary:
        print('ERROR: --phase post-apply needs --recalc-summary of the second '
              'dry run')
        return EXIT_USAGE
    if args.recalc_rows and args.phase != PHASE_PRE_APPLY:
        print('ERROR: --recalc-rows belongs to --phase pre-apply')
        return EXIT_USAGE
    if args.apply_summary and args.phase != PHASE_POST_APPLY:
        print('ERROR: --apply-summary belongs to --phase post-apply')
        return EXIT_USAGE
    if not os.path.exists(args.db_path):
        print('ERROR: database not found at %s - refusing to run.'
              % args.db_path)
        return EXIT_NO_DATABASE
    recalc_summary = dry_rows = apply_summary = None
    try:
        if args.recalc_summary:
            with io.open(args.recalc_summary, encoding='utf-8') as handle:
                recalc_summary = json.load(handle)
        if args.recalc_rows:
            dry_rows = load_dry_rows(args.recalc_rows)
        if args.apply_summary:
            with io.open(args.apply_summary, encoding='utf-8') as handle:
                apply_summary = json.load(handle)
    except (IOError, OSError, ValueError) as exc:
        print('ERROR: cannot read the recalc summary or rows: %s' % exc)
        return EXIT_USAGE

    before = file_sha256(args.db_path)
    con = sqlite3.connect('file:%s?mode=ro' % os.path.abspath(
        args.db_path).replace('\\', '/'), uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        stored = load_rows(con, date_from, date_to)
    except sqlite3.Error as exc:
        print('ERROR: %s' % exc)
        return EXIT_USAGE
    finally:
        con.close()
    after = file_sha256(args.db_path)

    problems, notes, rewrites = [], [], []
    rows = stored
    if args.phase == PHASE_PRE_APPLY:
        rows, rewrites, found = project(stored, dry_rows)
        problems += found
    observed, report = observe(rows)
    problems += compare(expected, observed) + formula_problems(observed)
    if args.phase:
        problems += rule_problems(oracle)
        found, notes = state_problems(transition, rows)
        problems += found
    if args.phase == PHASE_PRE_APPLY:
        problems += pre_apply_problems(transition, stored, rows, rewrites,
                                       recalc_summary, dry_rows,
                                       expected.get('records'))
    elif args.recalc_summary:
        problems += recalc_summary_problems(recalc_summary,
                                            expected.get('records'))
    if args.apply_summary:
        problems += apply_summary_problems(
            apply_summary,
            len(transition_ids(transition, 'expected_rewrites')))
    if before != after:
        problems.append('read-only: the database changed while it was read')

    print('DJI AREA CONTROL ACCEPTANCE %s .. %s' % (date_from, date_to))
    print('  algorithm          : %s' % dji_area.AREA_ALGORITHM_VERSION)
    print('  structural rule    : %s' % dji_area.STRUCTURAL_RULE_VERSION)
    if args.phase:
        print('  phase              : %s' % args.phase)
        print('  footprint rule     : %s'
              % dji_area.RETAINED_FOOTPRINT_RULE_VERSION)
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
    if args.phase == PHASE_PRE_APPLY:
        wanted = transition_ids(transition, 'expected_rewrites')
        print('  %-38s %d  [%s]' % ('expected rewrites', len(wanted),
                                     _ids(wanted)))
        print('  %-38s %d  [%s]' % ('rewrites of the dry run', len(rewrites),
                                     _ids(rewrites)))
    if args.apply_summary:
        print('  %-38s %s' % ('apply calc_writes', json.dumps(
            (apply_summary or {}).get('calc_writes')
            if isinstance(apply_summary, dict) else None, sort_keys=True)))
    for note in notes:
        print('  %s' % note)
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
                       'oracle': oracle.get('oracle'),
                       'phase': args.phase,
                       'transition': (
                           {'expected_rewrites': transition_ids(
                               transition, 'expected_rewrites'),
                            'rewrites': sorted(rewrites), 'notes': notes}
                           if args.phase else None),
                       'recalc_dry_run': (
                           {'flights_in_period':
                            recalc_summary.get('flights_in_period'),
                            'calc_writes': recalc_summary.get('calc_writes')}
                           if isinstance(recalc_summary, dict) else None),
                       'apply': (
                           {'flights_in_period':
                            apply_summary.get('flights_in_period'),
                            'calc_writes': apply_summary.get('calc_writes')}
                           if isinstance(apply_summary, dict) else None),
                       'algorithm': dji_area.AREA_ALGORITHM_VERSION,
                       'structural_rule': dji_area.STRUCTURAL_RULE_VERSION},
                      handle, ensure_ascii=False, indent=2, sort_keys=True)
        book = cr.build_workbook(
            report, 'ru', period=(date_from.isoformat(), date_to.isoformat()),
            versions={'area_algorithm': dji_area.AREA_ALGORITHM_VERSION,
                      'structural_rule': dji_area.STRUCTURAL_RULE_VERSION,
                      'accounting_classes': acc.ACCOUNTING_CLASSES_VERSION,
                      'report': cr.REPORT_VERSION})
        # Проекция -- ещё не состояние базы: книга названа так, чтобы её не
        # приняли за отчёт по записанным строкам.
        book.save(os.path.join(args.out_dir,
                               'area_control_report_projected.xlsx'
                               if args.phase == PHASE_PRE_APPLY
                               else 'area_control_report.xlsx'))
    return EXIT_PASS if not problems else EXIT_FAIL


if __name__ == '__main__':
    sys.exit(main())
