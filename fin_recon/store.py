# -*- coding: utf-8 -*-
"""fin_recon/store.py -- снимок <-> строки таблиц `fin_recon_*`.

Оба направления лежат рядом намеренно: `rows_for_snapshot` раскладывает
проверенный снимок по таблицам для импортёра, `load_snapshot` собирает его
обратно из строк базы. Импортёр в той же транзакции сверяет хеш собранного
обратно снимка с хешем пакета, а проверка `verify` -- то же на готовой базе:
хранение без потерь доказывается, а не предполагается.

Экран и проверка читают базу только через `connect_ro` (`mode=ro`): запрет
SQLite, а не обещание кода.
"""

import json
import os
import sqlite3

from decimal import Decimal

from urllib.request import pathname2url

import fin_recon as fr
from fin_recon import contract
from fin_recon import schema


def _json(value):
    return contract.canonical_bytes(value).decode('utf-8')


def _parse(text):
    return None if text is None else json.loads(text)


def ro_uri(path):
    """URI SQLite только на чтение. Пробелы и решётки путей экранируются."""
    return 'file:%s?mode=ro' % pathname2url(os.path.abspath(path))


def connect_ro(path):
    con = sqlite3.connect(ro_uri(path), uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def tables_ready(con):
    return not schema.missing_tables(con)


# ── снимок -> строки ──────────────────────────────────────────────────────

def rows_for_snapshot(snapshot):
    """{таблица: [{колонка: значение}]} для всех дочерних таблиц снимка."""
    out = {name: [] for name in schema.CHILD_TABLES}
    for i, src in enumerate(snapshot['sources']):
        out['fin_recon_source'].append({
            'position': i, 'source_key': src['key'], 'role': src['role'],
            'name': src['name'], 'sha256': src['sha256'],
            'bytes': src['bytes'], 'container_key': src['container'],
            'member': src['member'], 'in_package': 1 if src['in_package'] else 0,
            'note': src['note']})
    for i, m in enumerate(snapshot['metrics']):
        out['fin_recon_metric'].append({
            'position': i, 'metric_key': m['key'], 'group_key': m['group'],
            'label': m['label'], 'semantic': m['semantic'], 'unit': m['unit'],
            'value': m['value'], 'evidence': m['evidence'],
            'check_rule': m['check'],
            'identity_json': None if m['identity'] is None
            else _json(m['identity']),
            'note': m['note']})
    for i, op in enumerate(snapshot['operators']):
        acc = op['accrued']
        out['fin_recon_operator_row'].append({
            'position': i, 'operator_key': op['key'],
            'display_name': op['name'], 'organization': op['organization'],
            'area_total_ha': op['area']['total_ha'],
            'area_breakdown_json': _json(op['area']['breakdown']),
            'accrued_amount': acc['amount'],
            'accrued_evidence': acc['evidence'],
            'accrued_source_value': acc['source_value'],
            'accrued_ref_json': None if acc['ref'] is None
            else _json(acc['ref']),
            'recognized_total': op['recognized_total'],
            'difference_amount': op['difference'], 'basis': op['basis'],
            'limitations': op['limitations']})
    for i, p in enumerate(snapshot['payouts']):
        out['fin_recon_payout'].append({
            'position': i, 'payout_key': p['key'], 'operator_key': p['operator'],
            'kind': p['kind'], 'amount': p['amount'], 'evidence': p['evidence'],
            'record_date': p['record_date'], 'text': p['text'],
            'ref_json': None if p['ref'] is None else _json(p['ref']),
            'decision_key': p['decision'], 'note': p['note']})
    for i, w in enumerate(snapshot['works']):
        dates = w['work_dates']
        out['fin_recon_work_row'].append({
            'position': i, 'work_key': w['key'], 'operator_key': w['operator'],
            'source_key': w['source'], 'sheet': w['sheet'],
            'row_number': w['row'], 'part': w['part'],
            'refs_json': _json(w['refs']), 'farm': w['farm'],
            'block': w['block'], 'block_label': w['block_label'],
            'area_ha': w['area_ha'], 'area_evidence': w['area_evidence'],
            'area_source_value': w['area_source_value'],
            'date_text': w['date_text'], 'work_dates_json': _json(dates),
            'work_date_from': dates[0] if dates else None,
            'work_date_to': dates[-1] if dates else None,
            'work_month': w['work_month'], 'ledger_month': w['ledger_month'],
            'salary_status': w['salary_status'],
            'vs_verdict': w['vs_verdict'],
            'vs_verdict_label': w['vs_verdict_label'],
            'vs_db_rows': w['vs_db_rows'], 'vs_db_area_ha': w['vs_db_area_ha'],
            'vs_note': w['vs_note'], 'anomalies_json': _json(w['anomalies'])})
    for i, a in enumerate(snapshot['anomalies']):
        out['fin_recon_anomaly'].append({
            'position': i, 'anomaly_key': a['id'], 'priority': a['priority'],
            'status': a['status'],
            'status_code': fr.ANOMALY_STATUSES[a['status']],
            'group_label': a['group'], 'finding': a['finding'],
            'rule': a['rule'], 'operators_json': _json(a['operators']),
            'source_address': a['source_address']})
    for i, d in enumerate(snapshot['decisions']):
        out['fin_recon_owner_decision'].append({
            'position': i, 'decision_key': d['key'], 'kind': d['kind'],
            'topic': d['topic'], 'rule': d['rule'],
            'confirmation': d['confirmation'], 'decided_on': d['decided_on'],
            'detail': d['detail'], 'source_key': d['source'],
            'anomalies_json': _json(d['anomalies']),
            'values_json': _json(d['values'])})
    for i, x in enumerate(snapshot['cross_period_links']):
        out['fin_recon_cross_period_link'].append({
            'position': i, 'link_key': x['key'], 'work_key': x['work'],
            'work_month': x['work_month'], 'ledger_month': x['ledger_month'],
            'accrual_month': x['accrual_month'], 'area_ha': x['area_ha'],
            'salary_amount': x['salary_amount'],
            'salary_evidence': x['salary_evidence'],
            'payout_record_date': x['payout_record_date'],
            'anomalies_json': _json(x['anomalies']), 'policy': x['policy'],
            'note': x['note']})
    d = snapshot['dji']
    out['fin_recon_dji_snapshot'].append({
        'period_from': d['period_from'], 'period_to': d['period_to'],
        'captured_at': d['captured_at'], 'source_key': d['source'],
        'flights': d['flights'], 'raw_ha': d['raw_ha'],
        'accepted_ha': d['accepted_ha'], 'excluded_ha': d['excluded_ha'],
        'excluded_auto_ha': d['excluded_auto_ha'],
        'excluded_admin_ha': d['excluded_admin_ha'],
        'open_review': d['open_review'], 'pending_v4': d['pending_v4'],
        'unattributed_ha': d['unattributed_ha'],
        'unattributed_flights': d['unattributed_flights'],
        'single_candidate_ha': d['single_candidate_ha'],
        'single_candidate_flights': d['single_candidate_flights'],
        'multi_candidate_ha': d['multi_candidate_ha'],
        'multi_candidate_flights': d['multi_candidate_flights'],
        'attribution_source_key': d['attribution_source'],
        'algorithm_versions_json': _json(d['algorithm_versions']),
        'decisions_json': _json(d['decisions']),
        'semantics_note': d['semantics_note'], 'note': d['note']})
    return out


def header_row(snapshot):
    """Колонки строки снимка, которые берутся из самого снимка."""
    h = snapshot['header']
    sup = h['supersedes']
    return {
        'period': h['period'], 'version': h['version'],
        'supersedes_content_sha256': None if sup is None
        else sup['content_sha256'],
        'revision_reason': h['revision_reason'], 'status': h['status'],
        'owner_approved_at': h['owner_approved_at'],
        'financially_closed_at': h['financially_closed_at'],
        'title': h['title'], 'currency': h['currency'],
        'compiled_at': h['compiled_at'], 'producer': h['producer'],
        'normalization_version': h['normalization_version'],
        'schema_version': snapshot['schema'],
        'header_json': _json(h),
        'vs_json': None if snapshot['vs'] is None else _json(snapshot['vs']),
        'snapshot_json': _json(snapshot),
    }


# ── строки -> снимок ──────────────────────────────────────────────────────

def _fetch(con, table, snapshot_id):
    con.row_factory = sqlite3.Row
    return con.execute('SELECT * FROM %s WHERE snapshot_id = ? ORDER BY '
                       'position, id' % table, (snapshot_id,)).fetchall()


def load_snapshot(con, snapshot_id):
    """Снимок, собранный обратно ИЗ СТРОК таблиц (не из snapshot_json)."""
    con.row_factory = sqlite3.Row
    head = con.execute('SELECT * FROM fin_recon_period_snapshot WHERE id = ?',
                       (snapshot_id,)).fetchone()
    if head is None:
        return None
    snapshot = {'schema': head['schema_version'],
                'header': _parse(head['header_json']),
                'vs': _parse(head['vs_json'])}
    snapshot['sources'] = [{
        'key': r['source_key'], 'role': r['role'], 'name': r['name'],
        'sha256': r['sha256'], 'bytes': r['bytes'],
        'container': r['container_key'], 'member': r['member'],
        'in_package': bool(r['in_package']), 'note': r['note']}
        for r in _fetch(con, 'fin_recon_source', snapshot_id)]
    snapshot['metrics'] = [{
        'key': r['metric_key'], 'group': r['group_key'], 'label': r['label'],
        'semantic': r['semantic'], 'unit': r['unit'], 'value': r['value'],
        'evidence': r['evidence'], 'check': r['check_rule'],
        'identity': _parse(r['identity_json']), 'note': r['note']}
        for r in _fetch(con, 'fin_recon_metric', snapshot_id)]
    snapshot['operators'] = [{
        'key': r['operator_key'], 'name': r['display_name'],
        'organization': r['organization'],
        'area': {'total_ha': r['area_total_ha'],
                 'breakdown': _parse(r['area_breakdown_json'])},
        'accrued': {'amount': r['accrued_amount'],
                    'evidence': r['accrued_evidence'],
                    'source_value': r['accrued_source_value'],
                    'ref': _parse(r['accrued_ref_json'])},
        'recognized_total': r['recognized_total'],
        'difference': r['difference_amount'], 'basis': r['basis'],
        'limitations': r['limitations']}
        for r in _fetch(con, 'fin_recon_operator_row', snapshot_id)]
    snapshot['payouts'] = [{
        'key': r['payout_key'], 'operator': r['operator_key'],
        'kind': r['kind'], 'amount': r['amount'], 'evidence': r['evidence'],
        'record_date': r['record_date'], 'text': r['text'],
        'ref': _parse(r['ref_json']), 'decision': r['decision_key'],
        'note': r['note']}
        for r in _fetch(con, 'fin_recon_payout', snapshot_id)]
    snapshot['works'] = [{
        'key': r['work_key'], 'operator': r['operator_key'],
        'source': r['source_key'], 'sheet': r['sheet'], 'row': r['row_number'],
        'part': r['part'], 'refs': _parse(r['refs_json']), 'farm': r['farm'],
        'block': r['block'], 'block_label': r['block_label'],
        'area_ha': r['area_ha'], 'area_evidence': r['area_evidence'],
        'area_source_value': r['area_source_value'],
        'date_text': r['date_text'], 'work_dates': _parse(r['work_dates_json']),
        'work_month': r['work_month'], 'ledger_month': r['ledger_month'],
        'salary_status': r['salary_status'], 'vs_verdict': r['vs_verdict'],
        'vs_verdict_label': r['vs_verdict_label'],
        'vs_db_rows': r['vs_db_rows'], 'vs_db_area_ha': r['vs_db_area_ha'],
        'vs_note': r['vs_note'], 'anomalies': _parse(r['anomalies_json'])}
        for r in _fetch(con, 'fin_recon_work_row', snapshot_id)]
    snapshot['anomalies'] = [{
        'id': r['anomaly_key'], 'priority': r['priority'],
        'status': r['status'], 'group': r['group_label'],
        'finding': r['finding'], 'rule': r['rule'],
        'operators': _parse(r['operators_json']),
        'source_address': r['source_address']}
        for r in _fetch(con, 'fin_recon_anomaly', snapshot_id)]
    snapshot['decisions'] = [{
        'key': r['decision_key'], 'kind': r['kind'], 'topic': r['topic'],
        'rule': r['rule'], 'confirmation': r['confirmation'],
        'decided_on': r['decided_on'], 'detail': r['detail'],
        'source': r['source_key'], 'anomalies': _parse(r['anomalies_json']),
        'values': _parse(r['values_json'])}
        for r in _fetch(con, 'fin_recon_owner_decision', snapshot_id)]
    snapshot['cross_period_links'] = [{
        'key': r['link_key'], 'work': r['work_key'],
        'work_month': r['work_month'], 'ledger_month': r['ledger_month'],
        'accrual_month': r['accrual_month'], 'area_ha': r['area_ha'],
        'salary_amount': r['salary_amount'],
        'salary_evidence': r['salary_evidence'],
        'payout_record_date': r['payout_record_date'],
        'anomalies': _parse(r['anomalies_json']), 'policy': r['policy'],
        'note': r['note']}
        for r in _fetch(con, 'fin_recon_cross_period_link', snapshot_id)]
    dji_rows = con.execute('SELECT * FROM fin_recon_dji_snapshot WHERE '
                           'snapshot_id = ?', (snapshot_id,)).fetchall()
    if len(dji_rows) != 1:
        snapshot['dji'] = None
    else:
        r = dji_rows[0]
        snapshot['dji'] = {
            'period_from': r['period_from'], 'period_to': r['period_to'],
            'captured_at': r['captured_at'], 'source': r['source_key'],
            'flights': r['flights'], 'raw_ha': r['raw_ha'],
            'accepted_ha': r['accepted_ha'], 'excluded_ha': r['excluded_ha'],
            'excluded_auto_ha': r['excluded_auto_ha'],
            'excluded_admin_ha': r['excluded_admin_ha'],
            'open_review': r['open_review'], 'pending_v4': r['pending_v4'],
            'unattributed_ha': r['unattributed_ha'],
            'unattributed_flights': r['unattributed_flights'],
            'single_candidate_ha': r['single_candidate_ha'],
            'single_candidate_flights': r['single_candidate_flights'],
            'multi_candidate_ha': r['multi_candidate_ha'],
            'multi_candidate_flights': r['multi_candidate_flights'],
            'attribution_source': r['attribution_source_key'],
            'algorithm_versions': _parse(r['algorithm_versions_json']),
            'decisions': _parse(r['decisions_json']),
            'semantics_note': r['semantics_note'], 'note': r['note']}
    return snapshot


def row_counts(con, snapshot_id):
    return {table: con.execute('SELECT COUNT(*) FROM %s WHERE snapshot_id = ?'
                               % table, (snapshot_id,)).fetchone()[0]
            for table in schema.CHILD_TABLES}


def expected_row_counts(snapshot):
    return {table: len(rows)
            for table, rows in rows_for_snapshot(snapshot).items()}


# ── чтение для экрана и проверки ───────────────────────────────────────────

SNAPSHOT_META = ('id', 'period', 'version', 'supersedes_id',
                 'supersedes_content_sha256', 'revision_reason', 'status',
                 'owner_approved_at', 'financially_closed_at', 'title',
                 'currency', 'compiled_at', 'producer',
                 'normalization_version', 'schema_version', 'content_sha256',
                 'snapshot_file_sha256', 'manifest_sha256', 'package_id',
                 'imported_at', 'imported_by', 'importer_version')


def list_snapshots(con):
    """Все снимки: месяц, версия, статус, печать, кем заменён."""
    con.row_factory = sqlite3.Row
    rows = con.execute(
        'SELECT %s, s.sealed_at AS sealed_at, s.row_counts_json AS counts '
        'FROM fin_recon_period_snapshot p LEFT JOIN fin_recon_snapshot_seal s '
        'ON s.snapshot_id = p.id ORDER BY p.period DESC, p.version DESC'
        % ', '.join('p.' + c for c in SNAPSHOT_META)).fetchall()
    out = []
    superseded = {r['supersedes_id']: r['id'] for r in rows
                  if r['supersedes_id'] is not None}
    for r in rows:
        item = {c: r[c] for c in SNAPSHOT_META}
        item['sealed_at'] = r['sealed_at']
        item['row_counts'] = _parse(r['counts']) or {}
        item['superseded_by'] = superseded.get(r['id'])
        out.append(item)
    return out


def find_snapshot(con, period, version=None):
    """Мета снимка месяца: заданной версии или последней."""
    con.row_factory = sqlite3.Row
    if version is None:
        row = con.execute('SELECT %s FROM fin_recon_period_snapshot WHERE '
                          'period = ? ORDER BY version DESC LIMIT 1'
                          % ', '.join(SNAPSHOT_META), (period,)).fetchone()
    else:
        row = con.execute('SELECT %s FROM fin_recon_period_snapshot WHERE '
                          'period = ? AND version = ?'
                          % ', '.join(SNAPSHOT_META),
                          (period, version)).fetchone()
    return None if row is None else {c: row[c] for c in SNAPSHOT_META}


def versions_of(con, period):
    con.row_factory = sqlite3.Row
    return [dict(r) for r in con.execute(
        'SELECT id, version, content_sha256, imported_at, revision_reason, '
        'supersedes_id FROM fin_recon_period_snapshot WHERE period = ? '
        'ORDER BY version', (period,)).fetchall()]


def seal_of(con, snapshot_id):
    con.row_factory = sqlite3.Row
    row = con.execute('SELECT * FROM fin_recon_snapshot_seal WHERE '
                      'snapshot_id = ?', (snapshot_id,)).fetchone()
    if row is None:
        return None
    return {'sealed_at': row['sealed_at'],
            'content_sha256': row['content_sha256'],
            'row_counts': _parse(row['row_counts_json'])}


def stored_snapshot_json(con, snapshot_id):
    row = con.execute('SELECT snapshot_json FROM fin_recon_period_snapshot '
                      'WHERE id = ?', (snapshot_id,)).fetchone()
    return None if row is None else json.loads(row[0])


def _link_rows(con, snapshot_ids):
    if not snapshot_ids:
        return []
    marks = ','.join('?' * len(snapshot_ids))
    return con.execute(
        'SELECT p.period, p.version, l.snapshot_id, l.link_key, l.work_key, '
        'l.work_month, w.area_ha, w.work_dates_json, w.farm, o.display_name '
        'FROM fin_recon_cross_period_link l JOIN fin_recon_period_snapshot p '
        'ON p.id = l.snapshot_id JOIN fin_recon_work_row w ON w.snapshot_id = '
        'l.snapshot_id AND w.work_key = l.work_key LEFT JOIN '
        'fin_recon_operator_row o ON o.snapshot_id = w.snapshot_id AND '
        'o.operator_key = w.operator_key WHERE l.snapshot_id IN (%s) '
        'ORDER BY p.period, l.position' % marks, snapshot_ids).fetchall()


def _work_rows(con, snapshot_ids):
    if not snapshot_ids:
        return []
    marks = ','.join('?' * len(snapshot_ids))
    return con.execute(
        'SELECT p.period, p.version, w.snapshot_id, w.work_key, w.area_ha, '
        'w.work_dates_json, w.farm, o.display_name FROM fin_recon_work_row w '
        'JOIN fin_recon_period_snapshot p ON p.id = w.snapshot_id LEFT JOIN '
        'fin_recon_operator_row o ON o.snapshot_id = w.snapshot_id AND '
        'o.operator_key = w.operator_key WHERE w.snapshot_id IN (%s) '
        'ORDER BY p.period, w.position' % marks, snapshot_ids).fetchall()


def _pairs(links, works, direction):
    out = []
    for link in links:
        dates = set(json.loads(link['work_dates_json']))
        area = Decimal(link['area_ha'])
        for w in works:
            if w['snapshot_id'] == link['snapshot_id']:
                continue
            common = dates & set(json.loads(w['work_dates_json']))
            if common and Decimal(w['area_ha']) == area:
                out.append({
                    'direction': direction,
                    'link_period': link['period'],
                    'link_version': link['version'],
                    'link_key': link['link_key'],
                    'link_work_key': link['work_key'],
                    'work_month': link['work_month'],
                    'area_ha': link['area_ha'],
                    'link_farm': link['farm'],
                    'link_operator': link['display_name'],
                    'other_period': w['period'],
                    'other_version': w['version'],
                    'other_work_key': w['work_key'],
                    'other_farm': w['farm'],
                    'other_operator': w['display_name'],
                    'common_dates': sorted(common)})
    return out


def cross_period_candidates(con, snapshot_id):
    """Кандидаты в межмесячный дубль -- для показа, а не для исключения.

    [REASON]: межмесячная связь снимка говорит «эта работа другого месяца
    уже сидит в книге этого месяца». Когда загружен снимок соседнего месяца,
    его работа с той же площадью и пересекающимися датами -- КАНДИДАТ в
    повтор той же работы. Решает человек: оператор может быть записан в
    книгах разными фамилиями, а на одном поле бывают две разные работы.
    Ничего не исключается и не пересчитывается. Сравниваются только
    последние версии месяцев.

    Два направления: связи ЭТОГО снимка против работ других месяцев
    (`outgoing`) и связи других месяцев против работ этого (`incoming`) --
    экран любого из двух месяцев показывает одну и ту же пару.
    """
    con.row_factory = sqlite3.Row
    me = con.execute('SELECT period FROM fin_recon_period_snapshot WHERE '
                     'id = ?', (snapshot_id,)).fetchone()
    if me is None:
        return []
    others = [r['id'] for r in con.execute(
        'SELECT p.id FROM fin_recon_period_snapshot p WHERE p.period <> ? '
        'AND p.version = (SELECT MAX(q.version) FROM fin_recon_period_snapshot '
        'q WHERE q.period = p.period)', (me['period'],)).fetchall()]
    if not others:
        return []
    return (_pairs(_link_rows(con, [snapshot_id]), _work_rows(con, others),
                   'outgoing') +
            _pairs(_link_rows(con, others), _work_rows(con, [snapshot_id]),
                   'incoming'))
