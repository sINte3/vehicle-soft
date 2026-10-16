# -*- coding: utf-8 -*-
"""fin_recon/view.py -- что показывает экран «Дроны -> Финансовая сверка».

Чистые функции без Flask и без базы: на вход -- снимок, собранный из строк
(`store.load_snapshot`), и его мета; на выход -- словари для шаблона и для
выгрузки. Числа здесь не пересчитываются и не округляются: экран
показывает строки снимка как есть, с их доказательным типом.

Правила показа, которые держит этот модуль:

* неизвестное (UNKNOWN, NULL) показывается словом «неизвестно», а не нулём и
  не пустой ячейкой, которую глаз прочтёт как ноль;
* у каждой суммы рядом её доказательный тип: «записано текстом», «расчёт»,
  «трактовка владельца», «неизвестно» и т.д.;
* статус -- «Сверен с исключениями» и рядом всегда «Финансово НЕ утверждён»;
  зелёного «касса сходится» нет ни в одной ветке;
* принятая площадь DJI подписана как техническая: не основание для счёта
  и не основание начисления;
* открытые хозяйственные вопросы выводятся из аномалий со статусом
  «ОТКРЫТО» -- технические «0 открытых случаев DJI» их не скрывают.
"""

from decimal import Decimal

import fin_recon as fr

EVIDENCE_LABELS = {
    fr.EVIDENCE_BOOK_TEXT: ('записано текстом', 'матн билан ёзилган'),
    fr.EVIDENCE_BOOK_NUMBER: ('число в книге', 'китобдаги сон'),
    fr.EVIDENCE_BOOK_FORMULA: ('расчёт (формула книги)',
                               'ҳисоб (китоб формуласи)'),
    fr.EVIDENCE_REGISTER_CALC: ('расчёт (реестр сверки)',
                                'ҳисоб (солиштирув реестри)'),
    fr.EVIDENCE_OWNER: ('трактовка владельца', 'эгасининг талқини'),
    fr.EVIDENCE_SYSTEM_EXPORT: ('выгрузка системы', 'тизим кўчирмаси'),
    fr.EVIDENCE_UNKNOWN: ('неизвестно', 'номаълум'),
}
# Бейдж доказательного типа: признание владельцем и неизвестное заметны.
EVIDENCE_BADGES = {
    fr.EVIDENCE_BOOK_TEXT: 'vs-badge-info',
    fr.EVIDENCE_BOOK_NUMBER: 'vs-badge-info',
    fr.EVIDENCE_BOOK_FORMULA: 'vs-badge-info',
    fr.EVIDENCE_REGISTER_CALC: 'vs-badge-info',
    fr.EVIDENCE_OWNER: 'vs-badge-purple',
    fr.EVIDENCE_SYSTEM_EXPORT: 'vs-badge-info',
    fr.EVIDENCE_UNKNOWN: 'vs-badge-warning',
}

STATUS_LABELS = {
    fr.STATUS_RECONCILED_WITH_EXCEPTIONS: ('Сверен с исключениями',
                                           'Истиснолар билан солиштирилган'),
}
NOT_APPROVED = ('Финансово НЕ утверждён', 'Молиявий жиҳатдан ТАСДИҚЛАНМАГАН')
NOT_CLOSED = ('НЕ закрыт', 'ЁПИЛМАГАН')

SALARY_LABELS = {
    fr.SALARY_INCLUDED: ('входит в итог начисления книги',
                         'китоб ҳисоби якунига киради'),
    fr.SALARY_UNKNOWN: ('зарплата неизвестна (не начислена в книге)',
                        'иш ҳақи номаълум (китобда ҳисобланмаган)'),
}
VS_LABELS = {
    fr.VS_MATCH: ('совпадает', 'мос келади', 'vs-badge-success'),
    fr.VS_EXPECTED: ('ожидаемо', 'кутилган', 'vs-badge-info'),
    fr.VS_UNEXPECTED: ('НЕОЖИДАННО', 'КУТИЛМАГАН', 'vs-badge-danger'),
}
BLOCK_LABELS = {fr.BLOCK_CASH: ('нақд (наличные)', 'нақд'),
                fr.BLOCK_SPRAVKA: ('справка (счёт)', 'справка (ҳисоб)')}
ANOMALY_BADGES = {'OPEN': 'vs-badge-danger', 'MONITORED': 'vs-badge-warning',
                  'ACCOUNTED': 'vs-badge-info',
                  'OWNER_DECISION': 'vs-badge-purple'}
PAYOUT_LABELS = {
    fr.PAYOUT_BOOK_TEXT: ('запись о выдаче текстом книги',
                          'китобдаги матнли бериш ёзуви'),
    fr.PAYOUT_OWNER_RECOGNIZED: ('признано выплаченным по трактовке владельца',
                                 'эгасининг талқини бўйича тўланган деб тан '
                                 'олинган'),
}
DECISION_KIND_LABELS = {
    fr.DECISION_OWNER: ('Решение владельца', 'Эгасининг қарори'),
    fr.DECISION_CONTROL_NOTE: ('Контрольная заметка реестра',
                               'Реестрнинг назорат изоҳи'),
}
GROUP_TITLES = (
    ('WORKS', ('Работы, операторы, аномалии', 'Ишлар, операторлар, аномалиялар')),
    ('MONEY', ('Начисления и записи о выдаче', 'Ҳисоблаш ва бериш ёзувлари')),
    ('VS', ('Сверка с копией Vehicle Soft', 'Vehicle Soft нусхаси билан '
                                            'солиштирув')),
    ('BOOKS', ('Книги и общий свод', 'Китоблар ва умумий свод')),
    ('DJI', ('DJI: технический снимок', 'DJI: техник сурат')),
)
UNIT_LABELS = {'ha': ('га', 'га'), 'UZS': ('сум', 'сўм'),
               'count': ('', ''), 'flights': ('вылетов', 'парвоз')}
ROLE_LABELS = {
    'BOOK_PRIMARY': ('первичная книга', 'бирламчи китоб'),
    'BOOK_SECONDARY': ('вторичный общий свод', 'иккиламчи умумий свод'),
    'BOOK_LOCK_FILE': ('файл блокировки Excel (не книга)',
                       'Excel блокировка файли (китоб эмас)'),
    'BOOKS_ARCHIVE': ('архив книг владельца', 'эгасининг китоблар архиви'),
    'REGISTER_XLSX': ('итоговый реестр (XLSX)', 'якуний реестр (XLSX)'),
    'REGISTER_MD': ('итоговый реестр (записка)', 'якуний реестр (изоҳ)'),
    'OWNER_CLARIFICATIONS': ('решения и пояснения владельца',
                             'эгасининг қарорлари ва изоҳлари'),
    'DJI_EXPORT_BEFORE': ('экспорт DJI до решений', 'қарорлардан олдинги '
                                                   'DJI экспорти'),
    'DJI_EXPORT_AFTER': ('экспорт DJI после решений', 'қарорлардан кейинги '
                                                     'DJI экспорти'),
    'DJI_REPORT': ('проверка решений DJI', 'DJI қарорлари текшируви'),
    'VS_EXPORT': ('техническая выгрузка Vehicle Soft', 'Vehicle Soft техник '
                                                      'кўчирмаси'),
    'VS_CONSOLE': ('вывод консоли выгрузки', 'кўчирма консоли чиқиши'),
    'AUDIT_MODEL': ('модель аудита', 'аудит модели'),
    'HANDOFF': ('передача контекста', 'контекстни узатиш'),
    'EVIDENCE_NOTE': ('доказательная записка', 'далил изоҳи'),
    'TASK': ('задание', 'топшириқ'),
    'BUNDLE': ('комплект', 'тўплам'),
    'TOOL': ('инструмент', 'восита'),
    'EXTERNAL': ('вне пакета (у владельца)', 'пакетдан ташқари (эгасида)'),
}


def _pick(pair, is_ru):
    return pair[0] if is_ru else pair[1]


def cell(value, evidence, is_ru, unit='UZS'):
    """Значение с доказательным типом. Неизвестное -- словом, не нулём."""
    unknown = evidence == fr.EVIDENCE_UNKNOWN or value is None
    return {
        'value': None if unknown else value,
        'unknown': unknown,
        'unknown_text': _pick(EVIDENCE_LABELS[fr.EVIDENCE_UNKNOWN], is_ru),
        'evidence': evidence,
        'evidence_label': _pick(EVIDENCE_LABELS.get(
            evidence, (evidence, evidence)), is_ru),
        'badge': EVIDENCE_BADGES.get(evidence, 'vs-badge-info'),
        'unit': _pick(UNIT_LABELS.get(unit, ('', '')), is_ru),
    }


def _dec(text):
    return Decimal(text) if text is not None else None


def status_view(meta, is_ru):
    return {
        'status_label': _pick(STATUS_LABELS.get(
            meta['status'], (meta['status'], meta['status'])), is_ru),
        'not_approved_label': _pick(NOT_APPROVED, is_ru),
        'not_closed_label': _pick(NOT_CLOSED, is_ru),
        'owner_approved_at': meta['owner_approved_at'],
        'financially_closed_at': meta['financially_closed_at'],
    }


def metrics_view(snapshot, is_ru):
    groups = []
    by_group = {}
    for m in snapshot['metrics']:
        by_group.setdefault(m['group'], []).append(m)
    for key, title in GROUP_TITLES:
        rows = []
        for m in by_group.pop(key, []):
            rows.append({'key': m['key'], 'label': m['label'],
                         'cell': cell(m['value'], m['evidence'], is_ru,
                                      m['unit']),
                         'semantic': m['semantic'], 'checked': m['check']
                         is not None, 'identity': m['identity'],
                         'note': m['note']})
        if rows:
            groups.append({'key': key, 'title': _pick(title, is_ru),
                           'rows': rows})
    for key, ms in sorted(by_group.items()):
        groups.append({'key': key, 'title': key, 'rows': [
            {'key': m['key'], 'label': m['label'],
             'cell': cell(m['value'], m['evidence'], is_ru, m['unit']),
             'semantic': m['semantic'], 'checked': m['check'] is not None,
             'identity': m['identity'], 'note': m['note']} for m in ms]})
    return groups


def operators_view(snapshot, is_ru):
    period = snapshot['header']['period']
    payouts = {}
    for p in snapshot['payouts']:
        payouts.setdefault(p['operator'], []).append(p)
    unknown_area = {}
    for w in snapshot['works']:
        if w['salary_status'] == fr.SALARY_UNKNOWN:
            unknown_area[w['operator']] = unknown_area.get(
                w['operator'], Decimal(0)) + Decimal(w['area_ha'])
    out = []
    for op in snapshot['operators']:
        slots = {}
        other = []
        for part in op['area']['breakdown']:
            if part['work_month'] == period:
                slots[part['block']] = part['area_ha']
            else:
                other.append(part)
        rows = []
        for p in payouts.get(op['key'], []):
            rows.append({
                'key': p['key'], 'kind_label': _pick(PAYOUT_LABELS[p['kind']],
                                                     is_ru),
                'cell': cell(p['amount'], p['evidence'], is_ru),
                'record_date': p['record_date'], 'text': p['text'],
                'ref': p['ref'], 'decision': p['decision'], 'note': p['note'],
                'is_owner': p['kind'] == fr.PAYOUT_OWNER_RECOGNIZED})
        acc = op['accrued']
        gap = unknown_area.get(op['key'])
        out.append({
            'key': op['key'], 'name': op['name'],
            'organization': op['organization'],
            'area_total': op['area']['total_ha'],
            'area_spravka': slots.get(fr.BLOCK_SPRAVKA),
            'area_cash': slots.get(fr.BLOCK_CASH),
            'area_other': other,
            'accrued': cell(acc['amount'], acc['evidence'], is_ru),
            'accrued_source_value': acc['source_value'],
            'accrued_ref': acc['ref'],
            'payouts': rows,
            'recognized_total': op['recognized_total'],
            'difference': cell(op['difference'], fr.EVIDENCE_REGISTER_CALC
                               if op['difference'] is not None
                               else fr.EVIDENCE_UNKNOWN, is_ru),
            'unknown_salary_area': None if gap is None else format(gap, 'f'),
            'unknown_salary_cell': cell(None, fr.EVIDENCE_UNKNOWN, is_ru)
            if gap is not None else None,
            'basis': op['basis'], 'limitations': op['limitations']})
    return out


def works_view(snapshot, is_ru):
    names = {o['key']: o['name'] for o in snapshot['operators']}
    sources = {s['key']: s for s in snapshot['sources']}
    period = snapshot['header']['period']
    out = []
    for w in snapshot['works']:
        vs = VS_LABELS.get(w['vs_verdict'])
        out.append({
            'key': w['key'], 'operator': w['operator'],
            'operator_name': names.get(w['operator']),
            'farm': w['farm'], 'block_label': w['block_label'],
            'area_ha': w['area_ha'], 'area_evidence_label': _pick(
                EVIDENCE_LABELS[w['area_evidence']], is_ru),
            'area_source_value': w['area_source_value'],
            'date_text': w['date_text'], 'work_dates': w['work_dates'],
            'work_month': w['work_month'], 'ledger_month': w['ledger_month'],
            'other_month': w['work_month'] != period,
            'salary_label': _pick(SALARY_LABELS[w['salary_status']], is_ru),
            'salary_unknown': w['salary_status'] == fr.SALARY_UNKNOWN,
            'vs_label': None if vs is None else (vs[0] if is_ru else vs[1]),
            'vs_badge': None if vs is None else vs[2],
            'vs_book_label': w['vs_verdict_label'],
            'vs_db_rows': w['vs_db_rows'], 'vs_note': w['vs_note'],
            'source_name': (sources.get(w['source']) or {}).get('name'),
            'sheet': w['sheet'], 'row': w['row'], 'refs': w['refs'],
            'anomalies': w['anomalies']})
    return out


def anomalies_view(snapshot, is_ru):
    names = {o['key']: o['name'] for o in snapshot['operators']}
    out = []
    for a in snapshot['anomalies']:
        code = fr.ANOMALY_STATUSES.get(a['status'])
        out.append({'id': a['id'], 'priority': a['priority'],
                    'status': a['status'], 'status_code': code,
                    'badge': ANOMALY_BADGES.get(code, 'vs-badge-info'),
                    'group': a['group'], 'finding': a['finding'],
                    'rule': a['rule'], 'operators': a['operators'],
                    'operator_names': [names.get(k, k) for k in a['operators']],
                    'source_address': a['source_address']})
    return out


def decisions_view(snapshot, is_ru):
    out = []
    for d in snapshot['decisions']:
        out.append({
            'key': d['key'], 'kind': d['kind'],
            'kind_label': _pick(DECISION_KIND_LABELS[d['kind']], is_ru),
            'topic': d['topic'], 'rule': d['rule'],
            'confirmation': d['confirmation'], 'decided_on': d['decided_on'],
            'detail': d['detail'],
            # [REASON]: текст решения многострочный; строки отдаются списком,
            # а не через |safe с <br>: текст пришёл извне и экранируется.
            'detail_lines': [line for line in (d['detail'] or '').splitlines()
                             if line.strip()],
            'anomalies': d['anomalies'],
            # [REASON]: ключ не `values`: в Jinja `d.values` -- метод
            # словаря, а не элемент, и шаблон получил бы функцию.
            'typed_values': [{'label': v['label'],
                        'cell': cell(v['value'], v['evidence'], is_ru,
                                     v['unit']),
                        'semantic': v['semantic'], 'note': v['note']}
                       for v in d['values']]})
    return out


def sources_view(snapshot, is_ru):
    out = []
    for s in snapshot['sources']:
        out.append({'key': s['key'], 'role': s['role'],
                    'role_label': _pick(ROLE_LABELS.get(
                        s['role'], (s['role'], s['role'])), is_ru),
                    'name': s['name'], 'sha256': s['sha256'],
                    'bytes': s['bytes'], 'container': s['container'],
                    'member': s['member'], 'in_package': s['in_package'],
                    'note': s['note'], 'external': s['role'] == 'EXTERNAL'})
    return out


def links_view(snapshot, candidates, is_ru):
    works = {w['key']: w for w in snapshot['works']}
    names = {o['key']: o['name'] for o in snapshot['operators']}
    out = []
    for x in snapshot['cross_period_links']:
        w = works.get(x['work'], {})
        out.append({
            'key': x['key'], 'work': x['work'],
            'operator_name': names.get(w.get('operator')),
            'farm': w.get('farm'), 'work_dates': w.get('work_dates') or [],
            'work_month': x['work_month'], 'ledger_month': x['ledger_month'],
            'accrual_month': x['accrual_month'], 'area_ha': x['area_ha'],
            'salary': cell(x['salary_amount'], x['salary_evidence'], is_ru),
            'payout_record_date': x['payout_record_date'],
            'anomalies': x['anomalies'], 'policy': x['policy'],
            'note': x['note']})
    return {'links': out, 'candidates': candidates or []}


def dji_view(snapshot, is_ru):
    d = snapshot['dji']
    return dict(d, decisions=list(d['decisions']),
                accepted_caveat=(
                    'Принятая площадь DJI -- ТЕХНИЧЕСКАЯ: не основание для '
                    'счёта заказчику, не площадь к оплате и не основание '
                    'начисления зарплаты.'
                    if is_ru else
                    'DJI қабул қилинган майдони -- ТЕХНИК: буюртмачига '
                    'ҳисоб-фактура асоси эмас, тўлов майдони эмас ва иш ҳақи '
                    'ҳисоблаш асоси эмас.'))


def open_questions(anomalies):
    return [a for a in anomalies if a['status_code'] == 'OPEN']


def period_view(snapshot, meta, seal, versions, candidates, is_ru):
    anomalies = anomalies_view(snapshot, is_ru)
    return {
        'meta': meta, 'seal': seal, 'versions': versions,
        'header': snapshot['header'], 'status': status_view(meta, is_ru),
        'metric_groups': metrics_view(snapshot, is_ru),
        'operators': operators_view(snapshot, is_ru),
        'works': works_view(snapshot, is_ru),
        'anomalies': anomalies, 'open_questions': open_questions(anomalies),
        'decisions': decisions_view(snapshot, is_ru),
        'sources': sources_view(snapshot, is_ru),
        'cross': links_view(snapshot, candidates, is_ru),
        'dji': dji_view(snapshot, is_ru), 'vs': snapshot['vs'],
        'counts': {'works': len(snapshot['works']),
                   'operators': len(snapshot['operators']),
                   'anomalies': len(snapshot['anomalies']),
                   'links': len(snapshot['cross_period_links'])},
    }


# ── выгрузка XLSX (не источник истины) ────────────────────────────────────

FORMULA_PREFIXES = ('=', '+', '-', '@', '\t', '\r')


def xlsx_safe(value):
    """Текст из книг не должен стать формулой в чужом Excel.

    [REASON]: тот же приём, что `_drone_xlsx_safe` (drones.py, SP-F-004):
    ячейка, начинающаяся с = + - @ (и табуляции/перевода строки), Excel
    выполняет как формулу. В снимке такие строки есть по природе -- адреса
    формул книг вида «=+K9*0.15» и хозяйства, набранные руками.
    """
    if isinstance(value, str):
        stripped = value.lstrip(' ')
        if stripped and stripped[0] in FORMULA_PREFIXES:
            return "'" + value
    return value


def _x(value):
    """Ячейка выгрузки: Decimal -- числом без потерь, текст -- безопасно."""
    if value is None:
        return None
    if isinstance(value, (int, Decimal)) and not isinstance(value, bool):
        return value
    return xlsx_safe(str(value))


def _num_or_unknown(c):
    if c['unknown']:
        return c['unknown_text']
    return Decimal(c['value'])


def workbook(v, is_ru):
    """XLSX-копия экрана. Первая строка каждого листа: НЕ источник истины."""
    from openpyxl import Workbook

    t = (lambda ru, uz: ru if is_ru else uz)
    meta = v['meta']
    banner = t('НЕ ИСТОЧНИК ИСТИНЫ: выгрузка снимка %s v%d (SHA-256 '
               'содержимого %s). Статус: %s; %s; %s.',
               'ҲАҚИҚАТ МАНБАИ ЭМАС: %s v%d суратининг кўчирмаси (мазмун '
               'SHA-256 %s). Ҳолат: %s; %s; %s.') % (
        meta['period'], meta['version'], meta['content_sha256'],
        v['status']['status_label'], v['status']['not_approved_label'],
        v['status']['not_closed_label'])
    wb = Workbook()

    def sheet(title, header, rows, first=False):
        ws = wb.active if first else wb.create_sheet()
        ws.title = title
        ws.append([_x(banner)])
        ws.append([_x(h) for h in header])
        for row in rows:
            ws.append([_x(c) for c in row])
        return ws

    sheet(t('Итоги', 'Якунлар'),
          [t('Группа', 'Гуруҳ'), t('Показатель', 'Кўрсаткич'),
           t('Значение', 'Қиймат'), t('Единица', 'Бирлик'),
           t('Доказательный тип', 'Далил тури'),
           t('Примечание', 'Изоҳ')],
          [(g['title'], r['label'], _num_or_unknown(r['cell']),
            r['cell']['unit'], r['cell']['evidence_label'], r['note'])
           for g in v['metric_groups'] for r in g['rows']], first=True)
    op_rows = []
    for o in v['operators']:
        text = [p for p in o['payouts'] if not p['is_owner']]
        owner = [p for p in o['payouts'] if p['is_owner']]
        op_rows.append((
            o['key'], o['name'], o['organization'], Decimal(o['area_total']),
            _num_or_unknown(o['accrued']), o['accrued']['evidence_label'],
            sum((Decimal(p['cell']['value']) for p in text), Decimal(0))
            if text else None,
            ', '.join(p['record_date'] or '-' for p in text) or None,
            sum((Decimal(p['cell']['value']) for p in owner), Decimal(0))
            if owner else None,
            Decimal(o['recognized_total']), _num_or_unknown(o['difference']),
            o['unknown_salary_area'] and Decimal(o['unknown_salary_area']),
            o['basis'], o['limitations']))
    sheet(t('Операторы', 'Операторлар'),
          [t('Ключ', 'Калит'), t('Оператор', 'Оператор'),
           t('Организация', 'Ташкилот'), t('Га в книге', 'Китобда га'),
           t('Начислено, сум', 'Ҳисобланган, сўм'),
           t('Тип начисления', 'Ҳисоблаш тури'),
           t('Записано как выдано (текст), сум',
             'Берилган деб ёзилган (матн), сўм'),
           t('Дата записи о выдаче', 'Бериш ёзуви санаси'),
           t('Признано владельцем, сум', 'Эгаси тан олган, сўм'),
           t('Всего признано, сум', 'Жами тан олинган, сўм'),
           t('Разница, сум (не долг)', 'Фарқ, сўм (қарз эмас)'),
           t('Га без начисления зарплаты (неизвестно)',
             'Иш ҳақи ҳисобланмаган га (номаълум)'),
           t('Основание', 'Асос'), t('Ограничения', 'Чекловлар')], op_rows)
    sheet(t('Работы', 'Ишлар'),
          [t('Работа', 'Иш'), t('Оператор', 'Оператор'),
           t('Хозяйство', 'Хўжалик'), t('Блок', 'Блок'), t('Га', 'Га'),
           t('Дата в книге', 'Китобдаги сана'),
           t('Даты работы', 'Иш саналари'), t('Месяц работы', 'Иш ойи'),
           t('Месяц книги', 'Китоб ойи'), t('Зарплата', 'Иш ҳақи'),
           'Vehicle Soft', t('Пометка', 'Белги'),
           t('Источник', 'Манба'), t('Лист', 'Варақ'), t('Строка', 'Сатр')],
          [(w['key'], w['operator_name'], w['farm'], w['block_label'],
            Decimal(w['area_ha']), w['date_text'], ', '.join(w['work_dates']),
            w['work_month'], w['ledger_month'], w['salary_label'],
            w['vs_label'], w['vs_note'], w['source_name'], w['sheet'],
            w['row']) for w in v['works']])
    sheet(t('Аномалии', 'Аномалиялар'),
          ['ID', 'P', t('Статус', 'Ҳолат'), t('Группа', 'Гуруҳ'),
           t('Находка', 'Топилма'), t('Как учитываем', 'Қандай ҳисобга '
                                                       'оламиз'),
           t('Операторы', 'Операторлар'), t('Адрес-основание', 'Асос манзили')],
          [(a['id'], a['priority'], a['status'], a['group'], a['finding'],
            a['rule'], ', '.join(a['operators']), a['source_address'])
           for a in v['anomalies']])
    rows = [(d['key'], d['kind_label'], d['topic'], d['rule'],
             d['confirmation'], d['decided_on'], d['detail'])
            for d in v['decisions']]
    rows.append(())
    rows += [(s['key'], s['role_label'], s['name'], s['sha256'] or
              t('хеш не вычислен', 'хеш ҳисобланмаган'), s['bytes'],
              s['member'], s['note']) for s in v['sources']]
    sheet(t('Источники_и_решения', 'Манбалар_ва_қарорлар'),
          [t('Ключ', 'Калит'), t('Вид', 'Тури'), t('Тема / имя', 'Мавзу / ном'),
           t('Правило / SHA-256', 'Қоида / SHA-256'),
           t('Подтверждение / байт', 'Тасдиқ / байт'),
           t('Дата / путь в архиве', 'Сана / архивдаги йўл'),
           t('Подробности / примечание', 'Тафсилот / изоҳ')], rows)
    sheet(t('Переходящие', 'Ойлараро'),
          [t('Связь', 'Боғланиш'), t('Работа', 'Иш'), t('Оператор', 'Оператор'),
           t('Месяц работы', 'Иш ойи'), t('Месяц книги', 'Китоб ойи'),
           t('Месяц начисления', 'Ҳисоблаш ойи'), t('Га', 'Га'),
           t('Зарплата, сум', 'Иш ҳақи, сўм'), t('Тип', 'Тури'),
           t('Дата записи о выдаче', 'Бериш ёзуви санаси'),
           t('Правило', 'Қоида'), t('Примечание', 'Изоҳ')],
          [(x['key'], x['work'], x['operator_name'], x['work_month'],
            x['ledger_month'], x['accrual_month'], Decimal(x['area_ha']),
            _num_or_unknown(x['salary']), x['salary']['evidence_label'],
            x['payout_record_date'], x['policy'], x['note'])
           for x in v['cross']['links']])
    return wb
