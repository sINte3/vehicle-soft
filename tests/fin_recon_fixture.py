# -*- coding: utf-8 -*-
"""Синтетический пакет снимка FIN-RECON-STORAGE-001 для тестов.

ВСЕ ДАННЫЕ ВЫДУМАНЫ: имена, хозяйства, суммы, гектары, номера вылетов.
Форма -- как у настоящего месяца, чтобы тесты держали именно те правила,
на которых месяц мог сломаться: 7 операторов, 38 работ, 39 аномалий с
пропусками в номерах, работа декабря и работа апреля внутри книги марта,
три работы справки без начисления зарплаты (NULL, не 0), выплата по
трактовке владельца без текста книги, 4 технических решения DJI, одна
строка, которой нет в базе Vehicle Soft.

Месяц -- 2031-03, чтобы синтетику нельзя было спутать с настоящим месяцем.

    snap = build_snapshot()                 # снимок (dict), уже с хешами блобов
    paths = write_package(tmpdir)           # каталог пакета целиком
"""

import copy
import hashlib
import io
import json
import os
import zipfile

from decimal import Decimal

from fin_recon import contract

PERIOD = '2031-03'
PREV_MONTH = '2030-12'
NEXT_MONTH = '2031-04'

OPERATORS = (
    # key, имя, организация, число работ
    ('OP1', 'Синтетик Оператор Бир', 'Синтетик ташкилот А', 3),
    ('OP2', 'Синтетик Оператор Икки', 'Синтетик ташкилот Б', 1),
    ('OP3', 'Синтетик Оператор Уч', 'Синтетик ташкилот Б', 1),
    ('OP4', 'Синтетик Оператор Тўрт', 'Синтетик ташкилот В', 8),
    ('OP5', 'Синтетик Оператор Беш', 'Синтетик ташкилот Г', 7),
    ('OP6', 'Синтетик Оператор Олти', 'Синтетик ташкилот Д', 6),
    ('OP7', 'Синтетик Оператор Етти', 'Синтетик ташкилот Д', 12),
)

ACCRUED = {'OP1': '1500000.00', 'OP2': '120000.00', 'OP3': '900000.00',
           'OP4': '2400000.00', 'OP5': '300000.00', 'OP6': '1650000.57',
           'OP7': '1300000.00'}
PAID_TEXT = {'OP1': ('1499900.00', '2031-05-06'),
             'OP3': ('900000.00', '2031-05-06'),
             'OP4': ('2399800.00', '2031-04-08'),
             'OP5': ('300000.00', '2031-04-15'),
             'OP6': ('1650000.00', '2031-04-08'),
             'OP7': ('1299500.00', '2031-04-08')}
OWNER_PAID = {'OP2': '120000.00'}

# Отсутствующие номера аномалий, как у настоящего реестра: номера не
# перенумеровываются после слияния записей.
ANOMALY_IDS = tuple('A%02d' % n for n in range(1, 45)
                    if n not in (16, 28, 29, 30, 34))
STATUS_CYCLE = ('ОТКРЫТО', 'УЧТЕНО', 'НА КОНТРОЛЕ', 'УЧТЕНО', 'НА КОНТРОЛЕ',
                'ОТКРЫТО', 'УЧТЕНО')

BOOK_NAMES = ('Синтетик китоб А.xlsx', 'Синтетик китоб Б.xlsx',
              'Синтетик китоб В.xlsx', 'Синтетик китоб Г.xlsx',
              'Синтетик китоб Д.xlsx')
BOOK_OF = {'OP1': 0, 'OP2': 1, 'OP3': 1, 'OP4': 2, 'OP5': 3, 'OP6': 4,
           'OP7': 4}

# Хитрые строки: текст из книг приходит извне, экран обязан его экранировать,
# а выгрузка -- не превращать в формулу.
HOSTILE_FARM = '<script>alert("x")</script> =HYPERLINK("http://evil")'
HOSTILE_RULE = '=1+1 <b>bold</b> & "quotes"'


def _area(i):
    """Детерминированная площадь i-й работы, два знака."""
    return Decimal(500 + (i * 7919) % 4300) / Decimal(100)


def _blob(content, ext, role, name, container=None, member=None):
    return {'bytes': content, 'ext': ext, 'role': role, 'name': name,
            'container': container, 'member': member,
            'sha256': hashlib.sha256(content).hexdigest()}


def _zip(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for name, data in members:
            info = zipfile.ZipInfo(name, date_time=(2031, 4, 1, 12, 0, 0))
            zf.writestr(info, data)
    return buf.getvalue()


def make_blobs():
    """{source key: blob} -- файлы-источники синтетического месяца."""
    blobs = {}
    books = []
    for i, name in enumerate(BOOK_NAMES):
        data = ('SYNTHETIC BOOK %d -- not a real workbook\n' % i).encode('ascii')
        books.append((name, data))
    secondary = ('Синтетик умумий свод.xlsx',
                 b'SYNTHETIC SECONDARY SUMMARY\n')
    lock = ('~$Синтетик китоб В.xlsx', b'SYNTHETIC LOCK FILE\n')
    members = [('MART/' + n, d) for n, d in books] + [
        ('MART/' + secondary[0], secondary[1]), ('MART/' + lock[0], lock[1])]
    archive = _zip(members)
    blobs['S01'] = _blob(archive, '.zip', 'BOOKS_ARCHIVE',
                         'Синтетик МАРТ 2031.zip')
    for i, (name, data) in enumerate(books):
        blobs['S%02d' % (i + 2)] = _blob(data, '.xlsx', 'BOOK_PRIMARY', name,
                                         'S01', 'MART/' + name)
    blobs['S07'] = _blob(secondary[1], '.xlsx', 'BOOK_SECONDARY',
                         secondary[0], 'S01', 'MART/' + secondary[0])
    blobs['S08'] = _blob(lock[1], '.xlsx', 'BOOK_LOCK_FILE', lock[0], 'S01',
                         'MART/' + lock[0])
    blobs['S09'] = _blob(b'SYNTHETIC REGISTER XLSX\n', '.xlsx',
                         'REGISTER_XLSX', 'synthetic_register.xlsx')
    blobs['S10'] = _blob('# Синтетик реестр\n'.encode('utf-8'), '.md',
                         'REGISTER_MD', 'synthetic_register.md')
    blobs['S11'] = _blob('# Синтетик қарорлар\n'.encode('utf-8'), '.md',
                         'OWNER_CLARIFICATIONS', 'synthetic_owner.md')
    blobs['S12'] = _blob(b'SYNTHETIC DJI BEFORE\n', '.xlsx',
                         'DJI_EXPORT_BEFORE', 'synthetic_dji_before.xlsx')
    blobs['S13'] = _blob(b'SYNTHETIC DJI AFTER\n', '.xlsx',
                         'DJI_EXPORT_AFTER', 'synthetic_dji_after.xlsx')
    blobs['S14'] = _blob(_zip([('cmp/vs_summary.json', b'{}')]), '.zip',
                         'VS_EXPORT', 'synthetic_vs_export.zip')
    blobs['S15'] = _blob(b'SYNTHETIC CONSOLE\n', '.txt', 'VS_CONSOLE',
                         'synthetic_step3_console.txt')
    blobs['S16'] = _blob('# Синтетик handoff\n'.encode('utf-8'), '.md',
                         'HANDOFF', 'synthetic_handoff.md')
    return blobs


def build_snapshot(blobs=None):
    blobs = blobs or make_blobs()
    sources = []
    for key in sorted(blobs):
        b = blobs[key]
        sources.append({'key': key, 'role': b['role'], 'name': b['name'],
                        'sha256': b['sha256'], 'bytes': len(b['bytes']),
                        'container': b['container'], 'member': b['member'],
                        'in_package': True, 'note': None})
    sources.append({'key': 'S90', 'role': 'EXTERNAL',
                    'name': 'synthetic_large_original.xlsx',
                    'sha256': hashlib.sha256(b'not provided').hexdigest(),
                    'bytes': 123456, 'container': None, 'member': None,
                    'in_package': False,
                    'note': 'big original kept by the owner only'})

    works = []
    n = 0
    for op, _name, _org, count in OPERATORS:
        for j in range(count):
            n += 1
            key = 'W%02d' % n
            area = _area(n)
            block = 'SPRAVKA' if (op == 'OP5' and j < 3) or (
                op == 'OP7' and j < 6) or op == 'OP3' else 'CASH'
            day = 1 + (n * 3) % 27
            dates = ['%s-%02d' % (PERIOD, day), '%s-%02d' % (PERIOD, day + 1)]
            month = PERIOD
            if key == 'W10':
                dates, month = ['%s-23' % NEXT_MONTH, '%s-25' % NEXT_MONTH], \
                    NEXT_MONTH
            if key == 'W27':
                dates, month = ['%s-28' % PREV_MONTH], PREV_MONTH
            salary = 'NOT_ACCRUED_UNKNOWN' if op == 'OP5' and j < 3 \
                else 'INCLUDED_IN_BOOK_ACCRUAL'
            verdict, label, rows, note = 'MATCH', 'совпадает', 1, None
            if key in ('W04', 'W14', 'W16', 'W26', 'W30'):
                verdict, label, note = 'EXPECTED', 'ожидаемо', \
                    'синтетик кутилган фарқ'
            if key == 'W27':
                verdict, label, rows, note = 'EXPECTED', 'ожидаемо', 0, \
                    'нет в базе -- синтетик'
            works.append({
                'key': key, 'operator': op,
                'source': 'S%02d' % (BOOK_OF[op] + 2),
                'sheet': 'свод ичи (%s)' % op, 'row': 5 + j, 'part': None,
                'refs': {'area': 'C%d' % (5 + j), 'date': 'I%d' % (5 + j)},
                'farm': HOSTILE_FARM if key == 'W02' else
                'Синтетик хўжалик %02d' % n,
                'block': block,
                'block_label': 'Справка' if block == 'SPRAVKA' else 'Нақд',
                'area_ha': format(area, 'f'),
                'area_evidence': 'BOOK_FORMULA' if n % 5 == 0 else
                'BOOK_NUMBER',
                'area_source_value': '=%s+0' % format(area, 'f')
                if n % 5 == 0 else None,
                'date_text': '%s..%s' % (dates[0], dates[-1]),
                'work_dates': dates, 'work_month': month,
                'ledger_month': PERIOD, 'salary_status': salary,
                'vs_verdict': verdict, 'vs_verdict_label': label,
                'vs_db_rows': rows,
                'vs_db_area_ha': None if rows == 0 else format(area, 'f'),
                'vs_note': note,
                'anomalies': ['A01'] if key == 'W10' else
                (['A02'] if key == 'W27' else [])})

    def op_works(op):
        return [w for w in works if w['operator'] == op]

    payouts = []
    pn = 0
    for op, _name, _org, _count in OPERATORS:
        if op in PAID_TEXT:
            pn += 1
            amount, when = PAID_TEXT[op]
            payouts.append({
                'key': 'P%02d' % pn, 'operator': op,
                'kind': 'BOOK_TEXT_RECORD', 'amount': amount,
                'evidence': 'BOOK_TEXT', 'record_date': when,
                'text': 'синтетик ёзув: %s сум берилди (%s)' % (amount, when),
                'ref': {'source': 'S%02d' % (BOOK_OF[op] + 2),
                        'sheet': 'ОБШИЙ СВОД ', 'cell': 'B12',
                        'formula': None},
                'decision': None, 'note': None})
        if op in OWNER_PAID:
            pn += 1
            payouts.append({
                'key': 'P%02d' % pn, 'operator': op,
                'kind': 'OWNER_RECOGNIZED', 'amount': OWNER_PAID[op],
                'evidence': 'OWNER_INTERPRETATION', 'record_date': None,
                'text': None, 'ref': {'source': 'S03', 'sheet': 'свод ичи (OP2)',
                                      'cell': 'D12', 'formula': '=800000*0.15'},
                'decision': 'R3', 'note': 'без отдельной даты выдачи'})

    operators = []
    for op, name, org, _count in OPERATORS:
        own = op_works(op)
        slots = {}
        for w in own:
            slot = (w['work_month'], w['block'])
            slots[slot] = slots.get(slot, Decimal(0)) + Decimal(w['area_ha'])
        breakdown = [{'work_month': m, 'block': b, 'area_ha': format(v, 'f')}
                     for (m, b), v in sorted(slots.items())]
        total = sum((Decimal(w['area_ha']) for w in own), Decimal(0))
        paid = sum((Decimal(p['amount']) for p in payouts
                    if p['operator'] == op), Decimal(0))
        accrued = Decimal(ACCRUED[op])
        operators.append({
            'key': op, 'name': name, 'organization': org,
            'area': {'total_ha': format(total, 'f'), 'breakdown': breakdown},
            'accrued': {'amount': ACCRUED[op], 'evidence': 'BOOK_FORMULA',
                        'source_value': ACCRUED[op] + '0001'
                        if op == 'OP6' else ACCRUED[op],
                        'ref': {'source': 'S%02d' % (BOOK_OF[op] + 2),
                                'sheet': 'свод ичи (%s)' % op, 'cell': 'D30',
                                'formula': '=SUM(D29:D29)'}},
            'recognized_total': format(paid, 'f'),
            'difference': format(accrued - paid, 'f'),
            'basis': 'по решению владельца' if op in OWNER_PAID
            else 'по тексту книги',
            'limitations': 'синтетик чеклов' if op == 'OP5' else None})

    anomalies = []
    for i, aid in enumerate(ANOMALY_IDS):
        anomalies.append({
            'id': aid, 'priority': ('P0', 'P1', 'P2')[i % 3],
            'status': 'РЕШЕНИЕ ВЛАДЕЛЬЦА' if aid in ('A12', 'A37')
            else STATUS_CYCLE[i % len(STATUS_CYCLE)],
            'group': 'Синтетик гуруҳ %d' % (i % 5),
            'finding': 'Синтетик топилма %s' % aid,
            'rule': HOSTILE_RULE if aid == 'A03' else
            'Синтетик қоида %s' % aid,
            'operators': ['OP%d' % (1 + i % 7)] if i % 4 else [],
            'source_address': 'Синтетик китоб | лист | B%d' % (i + 2)})

    decisions = []
    for i in range(1, 6):
        decisions.append({
            'key': 'R%d' % i, 'kind': 'OWNER_DECISION',
            'topic': 'Синтетик қарор %d' % i,
            'rule': 'Синтетик қоида %d' % i,
            'confirmation': 'Синтетик тасдиқ', 'decided_on': '2031-10-12',
            'detail': 'Синтетик тафсилот %d' % i, 'source': 'S11',
            'anomalies': ['A15'] if i == 2 else (['A12'] if i == 3 else []),
            'values': [{'label': 'синтетик хизмат қиймати, иш ҳақи эмас',
                        'semantic': 'SERVICE_AMOUNT', 'unit': 'UZS',
                        'value': '4567890.10', 'evidence': 'REGISTER_CALC',
                        'note': None},
                       {'label': 'синтетик иш ҳақи', 'semantic':
                        'SALARY_ACCRUAL', 'unit': 'UZS', 'value': None,
                        'evidence': 'UNKNOWN', 'note': 'номаълум, 0 эмас'}]
            if i == 2 else []})
    for i in range(1, 6):
        decisions.append({
            'key': 'B%d' % i, 'kind': 'CONTROL_NOTE',
            'topic': 'Синтетик назорат %d' % i,
            'rule': 'Синтетик назорат қоидаси %d' % i,
            'confirmation': None, 'decided_on': None, 'detail': None,
            'source': 'S09', 'anomalies': [], 'values': []})

    links = [
        {'key': 'L1', 'work': 'W10', 'work_month': NEXT_MONTH,
         'ledger_month': PERIOD, 'accrual_month': PERIOD,
         'area_ha': next(w['area_ha'] for w in works if w['key'] == 'W10'),
         'salary_amount': '96000.00', 'salary_evidence': 'REGISTER_CALC',
         'payout_record_date': '2031-04-08', 'anomalies': ['A01'],
         'policy': 'синтетик: келгуси ойни импорт қилишда номзод', 'note': None},
        {'key': 'L2', 'work': 'W27', 'work_month': PREV_MONTH,
         'ledger_month': PERIOD, 'accrual_month': PERIOD,
         'area_ha': next(w['area_ha'] for w in works if w['key'] == 'W27'),
         'salary_amount': '150000.00', 'salary_evidence': 'BOOK_FORMULA',
         'payout_record_date': '2031-04-08', 'anomalies': ['A02'],
         'policy': 'синтетик: иккинчи марта ҳисобланмайди', 'note': None},
    ]

    dji = {
        'period_from': '%s-01' % PERIOD, 'period_to': '%s-31' % PERIOD,
        'captured_at': '2031-10-12T16:20:00+05:00', 'source': 'S13',
        'flights': 900, 'raw_ha': '900.1234', 'accepted_ha': '880.00340000',
        'excluded_ha': '20.12000000', 'excluded_auto_ha': '20.10000000',
        'excluded_admin_ha': '0.02000000', 'open_review': 0, 'pending_v4': 0,
        'unattributed_ha': '200.1000', 'unattributed_flights': 250,
        'single_candidate_ha': '700.0234', 'single_candidate_flights': 650,
        'multi_candidate_ha': '0.0000', 'multi_candidate_flights': 0,
        'attribution_source': 'S14',
        'algorithm_versions': {'area': 'synthetic-area-1',
                               'decisions': 'synthetic-decisions-1'},
        'decisions': [
            {'flight_id': '900000001', 'flight_date': '%s-04' % PERIOD,
             'drone': 'No 9', 'raw_ha': '0.0120', 'action': 'Полный фантом',
             'accepted_after_ha': '0', 'comment': 'синтетик',
             'decided_at': '12.10.2031 16:14', 'decided_by': 'Администратор'},
            {'flight_id': '900000002', 'flight_date': '%s-20' % PERIOD,
             'drone': 'No 13', 'raw_ha': '0.0080', 'action': 'Полный фантом',
             'accepted_after_ha': '0', 'comment': 'синтетик',
             'decided_at': '12.10.2031 16:12', 'decided_by': 'Администратор'},
            {'flight_id': '900000003', 'flight_date': '%s-23' % PERIOD,
             'drone': 'No 4', 'raw_ha': '0.3000',
             'action': 'Принят автоматический результат',
             'accepted_after_ha': '0.3000', 'comment': 'синтетик',
             'decided_at': '12.10.2031 16:16', 'decided_by': 'Администратор'},
            {'flight_id': '900000004', 'flight_date': '%s-27' % PERIOD,
             'drone': 'No 4', 'raw_ha': '0.5000',
             'action': 'Принят автоматический результат',
             'accepted_after_ha': '0.5000', 'comment': 'синтетик',
             'decided_at': '12.10.2031 16:14', 'decided_by': 'Администратор'},
        ],
        'semantics_note': 'синтетик: техник қабул қилинган майдон, '
                          'ҳисоб-китоб асоси эмас',
        'note': None}

    snapshot = {
        'schema': 'fin-recon-snapshot/1',
        'header': {
            'period': PERIOD, 'version': 1, 'supersedes': None,
            'revision_reason': None, 'status': 'RECONCILED_WITH_EXCEPTIONS',
            'owner_approved_at': None, 'financially_closed_at': None,
            'title': 'SYNTHETIC snapshot %s' % PERIOD, 'currency': 'UZS',
            'compiled_at': '2031-10-12', 'producer': 'tests/fin_recon_fixture',
            'normalization_version': 'synthetic-normalize/1',
            'money_rounding': '0.01 ROUND_HALF_UP',
            'evidence_boundaries': ['синтетик: касса тасдиқланмаган',
                                    'синтетик: иш ҳақи номаълум'],
            'status_note': 'синтетик: молиявий тасдиқланмаган'},
        'sources': sources, 'metrics': [], 'operators': operators,
        'payouts': payouts, 'works': works, 'anomalies': anomalies,
        'decisions': decisions, 'cross_period_links': links, 'dji': dji,
        'vs': None}
    vs = contract.vs_counts(snapshot)
    snapshot['vs'] = {
        'export_source': 'S14',
        'extract_log_sha256': hashlib.sha256(b'synthetic log').hexdigest(),
        'db_copy_sha256': hashlib.sha256(b'synthetic db').hexdigest(),
        'db_copy_label': 'синтетик нусха', 'compared_at': '2031-10-11T09:23:28',
        'register_rows': vs['register_rows'], 'db_rows': vs['db_rows'],
        'equal': vs['equal'], 'expected_only': vs['expected_only'],
        'unexpected': vs['unexpected'], 'missing_in_db': vs['missing_in_db'],
        'db_not_in_register': 0,
        'db_area_ha': format(vs['db_area_ha'], 'f')}
    snapshot['metrics'] = build_metrics(snapshot)
    return snapshot


CHECKED_METRICS = (
    # key, группа, подпись, смысл, единица, правило
    ('WORKS', 'WORKS', 'works', 'COUNT', 'count', 'WORKS_COUNT'),
    ('WORKS_PERIOD', 'WORKS', 'works of the month', 'COUNT', 'count',
     'WORKS_PERIOD_COUNT'),
    ('OPERATORS', 'WORKS', 'operators', 'COUNT', 'count', 'OPERATORS_COUNT'),
    ('ANOMALIES', 'WORKS', 'anomalies', 'COUNT', 'count', 'ANOMALIES_COUNT'),
    ('BOOK_AREA', 'WORKS', 'book area', 'BOOK_AREA', 'ha', 'BOOK_AREA_SUM'),
    ('BOOK_AREA_PERIOD', 'WORKS', 'book area of the month', 'BOOK_AREA', 'ha',
     'BOOK_AREA_PERIOD_SUM'),
    ('ACCRUED', 'MONEY', 'accrued', 'SALARY_ACCRUAL', 'UZS', 'ACCRUED_SUM'),
    ('PAYOUT_TEXT', 'MONEY', 'payout text', 'PAYOUT_TEXT', 'UZS',
     'PAYOUT_TEXT_SUM'),
    ('PAYOUT_OWNER', 'MONEY', 'payout owner', 'PAYOUT_OWNER', 'UZS',
     'PAYOUT_OWNER_SUM'),
    ('RECOGNIZED', 'MONEY', 'recognized', 'PAYOUT_RECOGNIZED', 'UZS',
     'PAYOUT_RECOGNIZED_SUM'),
    ('DIFFERENCE', 'MONEY', 'difference', 'DIFFERENCE', 'UZS',
     'DIFFERENCE_SUM'),
    ('UNKNOWN_SALARY_AREA', 'MONEY', 'area without salary', 'BOOK_AREA', 'ha',
     'UNKNOWN_SALARY_AREA_SUM'),
    ('VS_DB_ROWS', 'VS', 'db rows', 'COUNT', 'count', 'VS_DB_ROWS'),
    ('VS_EQUAL', 'VS', 'equal', 'COUNT', 'count', 'VS_EQUAL'),
    ('VS_EXPECTED', 'VS', 'expected', 'COUNT', 'count', 'VS_EXPECTED'),
    ('VS_UNEXPECTED', 'VS', 'unexpected', 'COUNT', 'count', 'VS_UNEXPECTED'),
    ('VS_MISSING', 'VS', 'missing', 'COUNT', 'count', 'VS_MISSING'),
    ('DJI_FLIGHTS', 'DJI', 'flights', 'COUNT', 'flights', 'DJI_FLIGHTS'),
    ('DJI_RAW', 'DJI', 'raw', 'DJI_RAW', 'ha', 'DJI_RAW'),
    ('DJI_ACCEPTED', 'DJI', 'accepted', 'DJI_ACCEPTED', 'ha', 'DJI_ACCEPTED'),
    ('DJI_EXCLUDED', 'DJI', 'excluded', 'DJI_EXCLUDED', 'ha', 'DJI_EXCLUDED'),
    ('DJI_UNATTRIBUTED', 'DJI', 'unattributed', 'DJI_UNATTRIBUTED', 'ha',
     'DJI_UNATTRIBUTED'),
)


def build_metrics(snapshot):
    computed = contract.derived_checks(snapshot)
    metrics = []
    for key, group, label, semantic, unit, rule in CHECKED_METRICS:
        value = computed[rule]
        metrics.append({'key': key, 'group': group, 'label': label,
                        'semantic': semantic, 'unit': unit,
                        'value': format(value, 'f'),
                        'evidence': 'REGISTER_CALC' if unit != 'flights'
                        and not key.startswith('DJI') else 'SYSTEM_EXPORT',
                        'check': rule, 'identity': None, 'note': None})
    # Объявленные числа без пересчёта и мост между ними.
    metrics += [
        {'key': 'SUMMARY_F27', 'group': 'BOOKS', 'label': 'secondary summary',
         'semantic': 'SECONDARY_SUMMARY', 'unit': 'UZS', 'value': '50000000',
         'evidence': 'BOOK_FORMULA', 'check': None, 'identity': None,
         'note': 'смешивает месяцы'},
        {'key': 'SUMMARY_OTHER_MONTHS', 'group': 'BOOKS',
         'label': 'rows of another month', 'semantic': 'SECONDARY_SUMMARY',
         'unit': 'UZS', 'value': '12000000', 'evidence': 'BOOK_NUMBER',
         'check': None, 'identity': None, 'note': None},
        {'key': 'SUMMARY_UNEXPLAINED', 'group': 'BOOKS',
         'label': 'unexplained difference', 'semantic': 'SECONDARY_SUMMARY',
         'unit': 'UZS', 'value': '3000000', 'evidence': 'REGISTER_CALC',
         'check': None, 'identity': None, 'note': None},
        {'key': 'SUMMARY_MISSING', 'group': 'BOOKS',
         'label': 'absent from the summary', 'semantic': 'SERVICE_AMOUNT',
         'unit': 'UZS', 'value': '500000', 'evidence': 'BOOK_NUMBER',
         'check': None, 'identity': None, 'note': None},
        {'key': 'CASH_BLOCKS', 'group': 'BOOKS',
         'label': 'cash blocks service total', 'semantic': 'SERVICE_AMOUNT',
         'unit': 'UZS', 'value': '35500000', 'evidence': 'REGISTER_CALC',
         'check': None,
         'identity': {'plus': ['SUMMARY_F27', 'SUMMARY_MISSING'],
                      'minus': ['SUMMARY_OTHER_MONTHS', 'SUMMARY_UNEXPLAINED']},
         'note': 'не деньги на руках'},
        {'key': 'UNKNOWN_SALARY_AMOUNT', 'group': 'MONEY',
         'label': 'salary for the spravka works', 'semantic': 'SALARY_ACCRUAL',
         'unit': 'UZS', 'value': None, 'evidence': 'UNKNOWN', 'check': None,
         'identity': None, 'note': 'неизвестно, не ноль'},
    ]
    return metrics


def expectations(snapshot=None):
    """Синтетические «ожидания месяца» -- как приватный файл настоящего."""
    snapshot = snapshot or build_snapshot()
    c = contract.derived_checks(snapshot)
    return {
        'schema': 'fin-recon-expectations/1', 'period': PERIOD, 'version': 1,
        'source': 'synthetic',
        'expect': {
            'check:WORKS_COUNT': 38, 'check:OPERATORS_COUNT': 7,
            'check:ANOMALIES_COUNT': 39,
            'check:BOOK_AREA_SUM': format(c['BOOK_AREA_SUM'], 'f'),
            'check:ACCRUED_SUM': '8170000.57',
            'check:PAYOUT_TEXT_SUM': '8049200.00',
            'check:PAYOUT_OWNER_SUM': '120000.00',
            'check:PAYOUT_RECOGNIZED_SUM': '8169200.00',
            'check:DIFFERENCE_SUM': '800.57',
            'check:UNKNOWN_SALARY_WORKS_COUNT': 3,
            'check:VS_DB_ROWS': 37, 'check:VS_EQUAL': 32,
            'check:VS_EXPECTED': 6, 'check:VS_UNEXPECTED': 0,
            'check:VS_MISSING': 1, 'check:DJI_FLIGHTS': 900,
            'check:DJI_ACCEPTED': '880.0034', 'check:DJI_ADMIN_DECISIONS': 4,
            'metric:CASH_BLOCKS': '35500000',
            'metric:UNKNOWN_SALARY_AMOUNT': None,
            'header:status': 'RECONCILED_WITH_EXCEPTIONS',
            'header:owner_approved_at': None,
            'header:financially_closed_at': None,
            'links': [{'work_month': PREV_MONTH, 'area_ha':
                       format(_area(27), 'f'), 'salary_amount': '150000.00'},
                      {'work_month': NEXT_MONTH, 'area_ha':
                       format(_area(10), 'f'), 'salary_amount': '96000.00'}],
        }}


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as fh:
        fh.write(data)


def write_package(root, snapshot=None, blobs=None, mutate_manifest=None,
                  with_expectations=True):
    """Записать полный пакет в root. Вернуть {'root', 'snapshot', ...}."""
    blobs = blobs or make_blobs()
    snapshot = copy.deepcopy(snapshot) if snapshot is not None \
        else build_snapshot(blobs)
    os.makedirs(root, exist_ok=True)
    entries = []
    for key in sorted(blobs):
        b = blobs[key]
        rel = 'blobs/%s%s' % (b['sha256'], b['ext'])
        if not any(e['path'] == rel for e in entries):
            _write(os.path.join(root, *rel.split('/')), b['bytes'])
            entries.append({'path': rel, 'sha256': b['sha256'],
                            'bytes': len(b['bytes']),
                            'original_name': b['name'],
                            'origin': 'synthetic', 'role': b['role']})
    snap_bytes = json.dumps(snapshot, ensure_ascii=False, indent=1,
                            sort_keys=True).encode('utf-8')
    _write(os.path.join(root, 'snapshot.json'), snap_bytes)
    outputs = []
    if with_expectations:
        exp_bytes = json.dumps(expectations(snapshot), ensure_ascii=False,
                               indent=1).encode('utf-8')
        _write(os.path.join(root, 'expectations', 'expect.json'), exp_bytes)
        outputs.append({'path': 'expectations/expect.json',
                        'sha256': hashlib.sha256(exp_bytes).hexdigest(),
                        'bytes': len(exp_bytes), 'role': 'EXPECTATIONS'})
    manifest = {
        'schema': 'fin-recon-package/1', 'package_id': 'synthetic-2031-03-v%d'
        % snapshot['header']['version'],
        'period': snapshot['header']['period'],
        'version': snapshot['header']['version'],
        'snapshot_schema_version': 'fin-recon-snapshot/1',
        'normalization_version': snapshot['header']['normalization_version'],
        'producer': 'tests/fin_recon_fixture', 'assembled_at': '2031-10-12',
        'register_compiled_on': '2031-10-12',
        'snapshot': {'path': 'snapshot.json',
                     'sha256': hashlib.sha256(snap_bytes).hexdigest(),
                     'content_sha256': contract.content_sha256(snapshot),
                     'bytes': len(snap_bytes)},
        'blobs': entries, 'outputs': outputs,
        'register': {'xlsx_sha256': blobs['S09']['sha256'],
                     'md_sha256': blobs['S10']['sha256']},
        'technical_export': {'vs_export_sha256': blobs['S14']['sha256']},
        'external_evidence': [{'name': 'synthetic_large_original.xlsx',
                               'status': 'NOT_PROVIDED'}],
        'integrity_note': 'SHA-256 is integrity of bits only.'}
    if mutate_manifest is not None:
        mutate_manifest(manifest)
    man_bytes = json.dumps(manifest, ensure_ascii=False, indent=1).encode(
        'utf-8')
    _write(os.path.join(root, 'manifest.json'), man_bytes)
    write_sums(root)
    return {'root': root, 'snapshot': snapshot, 'manifest': manifest,
            'expect': os.path.join(root, 'expectations', 'expect.json')}


def write_sums(root):
    lines = []
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, '/')
            if rel == 'SHA256SUMS.txt':
                continue
            with open(full, 'rb') as fh:
                lines.append('%s  %s' % (hashlib.sha256(fh.read()).hexdigest(),
                                         rel))
    with open(os.path.join(root, 'SHA256SUMS.txt'), 'w', encoding='ascii',
              newline='\n') as fh:
        fh.write('\n'.join(sorted(lines, key=lambda x: x[66:])) + '\n')


def version_two(snapshot_v1, change):
    """v2 того же месяца: ссылка на хеш v1, причина и одно изменение."""
    v2 = copy.deepcopy(snapshot_v1)
    v2['header']['version'] = 2
    v2['header']['supersedes'] = {'version': 1, 'content_sha256':
                                  contract.content_sha256(snapshot_v1)}
    v2['header']['revision_reason'] = 'синтетик: аномалия статуси ўзгарди'
    change(v2)
    v2['metrics'] = build_metrics(v2)
    return v2
