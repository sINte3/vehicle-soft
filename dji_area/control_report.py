# -*- coding: utf-8 -*-
"""dji_area/control_report.py -- отчёт контроля площади DJI: то, что видит человек.

DJI-AREA-PRODUCTIONIZATION-001. Главная мысль отчёта умещается в строку:

    DJI показал  ->  программа доказанно исключила  ->  осталось.

Модуль НИЧЕГО не решает сам. Класс записи определяет `dji_area.accounting`
поверх решения резолвера; здесь это решение только переводится в величины
отчёта и в слова. Flask и базы тут нет -- страницу, книгу и тесты кормит один
и тот же код, поэтому числа на экране и в Excel расходиться не могут.

ВЕЛИЧИНЫ ОДНОЙ ЗАПИСИ

    NORMAL              принято = RAW
    PHANTOM_PROVEN      принято = проверенный прирост счётчика (0 или малый),
                        исключено = RAW - принято
    PHANTOM_STRUCTURAL  принято = RAW, ждёт V4 -- автоматически НЕ вычитается
    REVIEW              принято = RAW, требует проверки -- НЕ вычитается

[REASON]: RAW не переписывается никогда, а мостик B между базой и целью не
исключается: проверка показала, что на нём борт действительно льёт. В цепочке
A -> B -> C корректируется только C, и отчёт говорит это словами.

«Площадь после подтверждённых корректировок» -- НЕ «точная» и НЕ «финальная»
площадь, пока есть записи, ждущие доказательства или проверки: они сидят
внутри по своему RAW. Поэтому рядом с числом всегда стоит статус полноты.
"""

import json

from dji_area import accounting as acc
from dji_area import resolver as rs

REPORT_VERSION = 'area-control-report-1'
DJI_RECORD_URL = 'https://www.djiag.com/record/%d'
M2_PER_HA = 10000.0

VIEW_ALL = 'all'
VIEW_CONFIRMED = 'confirmed'
VIEW_PENDING = 'pending'
VIEW_REVIEW = 'review'
VIEWS = (VIEW_ALL, VIEW_CONFIRMED, VIEW_PENDING, VIEW_REVIEW)
_VIEW_CLASSES = {
    VIEW_CONFIRMED: (acc.PHANTOM_PROVEN,),
    VIEW_PENDING: (acc.PHANTOM_STRUCTURAL,),
    VIEW_REVIEW: (acc.REVIEW,),
}
# В реестр идут записи, по которым есть что сказать человеку. Обычные записи
# (их тысячи) остаются в итогах и в разрезе по дронам.
REGISTER_CLASSES = (acc.PHANTOM_PROVEN, acc.PHANTOM_STRUCTURAL, acc.REVIEW)

# Причины словами. Код причины -- ключ; (ru, uz). Узбекский -- кириллицей.
EXPLAIN_REPEAT = 'REPEAT_OF_PREVIOUS_AUTO_WORK'
EXPLAIN_PARTIAL = 'PARTIAL_NEW_GROWTH'
EXPLAIN_CONTROL = 'CONTROL_CONFIRMED_OVERSTATEMENT'
EXPLAIN_CONTROL_PARTIAL = 'CONTROL_CONFIRMED_PARTIAL'
EXPLAIN_PENDING = 'PENDING_V4'
EXPLAIN_PENDING_NO_V4 = 'PENDING_NO_V4_AT_SOURCE'

EXPLANATIONS = {
    EXPLAIN_REPEAT: (
        'Повтор площади предыдущей Auto-работы; V4 не подтвердил новый '
        'прирост.',
        'Олдинги Auto-иш майдонининг такрори; V4 янги ўсишни тасдиқламади.'),
    EXPLAIN_PARTIAL: (
        'DJI площадь завышена; V4 подтвердил только %s га нового прироста.',
        'DJI майдони ошириб кўрсатилган; V4 фақат %s га янги ўсишни '
        'тасдиқлади.'),
    EXPLAIN_CONTROL: (
        'Завышение подтверждено V4 контрольной проверкой; нового прироста '
        'нет.',
        'Ошириб кўрсатиш V4 назорат текшируви билан тасдиқланди; янги ўсиш '
        'йўқ.'),
    EXPLAIN_CONTROL_PARTIAL: (
        'Завышение подтверждено V4 контрольной проверкой; подтверждено '
        'только %s га нового прироста.',
        'Ошириб кўрсатиш V4 назорат текшируви билан тасдиқланди; фақат %s га '
        'янги ўсиш тасдиқланди.'),
    EXPLAIN_PENDING: (
        'Сильный кандидат на повтор площади; V4 ещё не получен. Пока учтён '
        'по DJI RAW.',
        'Майдон такрорига кучли номзод; V4 ҳали олинмаган. Ҳозирча DJI RAW '
        'бўйича ҳисобга олинган.'),
    EXPLAIN_PENDING_NO_V4: (
        'Сильный кандидат на повтор площади, но DJI не хранит V4 этой '
        'записи. Учтён по DJI RAW; нужна проверка человеком.',
        'Майдон такрорига кучли номзод, аммо DJI бу ёзувнинг V4 сини '
        'сақламайди. DJI RAW бўйича ҳисобга олинган; инсон текшируви керак.'),
    acc.R_APPLICATION_WITH_FLAT_COUNTER: (
        'Счётчик площади не вырос, но распыление наблюдалось; безопасного '
        'автоматического решения нет.',
        'Майдон ҳисоблагичи ўсмади, аммо пуркаш кузатилди; хавфсиз автоматик '
        'қарор йўқ.'),
    acc.R_OVERSTATEMENT_NOT_CERTIFIED: (
        'Признаки завышения есть, но окно V4 не позволяет подтвердить '
        'поправку.',
        'Ошириб кўрсатиш белгилари бор, аммо V4 ойнаси тузатишни тасдиқлашга '
        'имкон бермайди.'),
    acc.R_INTERVAL_OVERLAP: (
        'Интервал записи пересекается с другой записью того же борта.',
        'Ёзув оралиғи шу бортнинг бошқа ёзуви билан кесишади.'),
    acc.R_COUNTER_RELATIONSHIP: (
        'Счётчик V4 не совпал ни с нулём, ни с площадью DJI.',
        'V4 ҳисоблагичи на нолга, на DJI майдонига мос келмади.'),
    acc.R_COUNTER_NONMONOTONE: (
        'Счётчик V4 сбрасывался внутри записи.',
        'V4 ҳисоблагичи ёзув ичида қайта тикланган.'),
    acc.R_BASELINE_UNKNOWN: (
        'Начало счётчика V4 не измерено.',
        'V4 ҳисоблагичининг бошланиши ўлчанмаган.'),
    acc.R_UNKNOWN_SUSPECT: (
        'Данных для автоматического решения недостаточно.',
        'Автоматик қарор учун маълумот етарли эмас.'),
    acc.R_APPLICATION_WITHOUT_AREA: (
        'Распыление наблюдалось, а площадь не измерена.',
        'Пуркаш кузатилди, майдон эса ўлчанмаган.'),
    acc.R_UNMAPPED_STATUS: (
        'Неизвестный статус расчёта; нужна проверка.',
        'Номаълум ҳисоб ҳолати; текшириш керак.'),
}

CLASS_LABELS = {
    acc.NORMAL: ('Принято по DJI', 'DJI бўйича қабул қилинган'),
    acc.PHANTOM_PROVEN: ('Подтверждённая корректировка',
                         'Тасдиқланган тузатиш'),
    acc.PHANTOM_STRUCTURAL: ('Ожидает V4', 'V4 кутилмоқда'),
    acc.REVIEW: ('Требует проверки', 'Текшириш талаб қилинади'),
}
# Цвет -- семантика дизайн-системы: подтверждённая корректировка нейтральна,
# ожидание -- предупреждение, проверка -- внимание. Красным всё подряд не
# красится: доказанная корректировка -- это порядок, а не тревога.
CLASS_BADGES = {
    acc.NORMAL: '',
    acc.PHANTOM_PROVEN: 'vs-badge-info',
    acc.PHANTOM_STRUCTURAL: 'vs-badge-warning',
    acc.REVIEW: 'vs-badge-danger',
}

EVIDENCE_V4_GOOD = 'V4_VALIDATED'
EVIDENCE_V4_LIMITED = 'V4_LIMITED'
EVIDENCE_V4_MISSING = 'V4_MISSING'
EVIDENCE_NO_V4_AT_SOURCE = 'NO_V4_AT_SOURCE'
EVIDENCE_LABELS = {
    EVIDENCE_V4_GOOD: ('V4 получен, счётчик проверен',
                       'V4 олинган, ҳисоблагич текширилган'),
    EVIDENCE_V4_LIMITED: ('V4 получен, окно неполное',
                          'V4 олинган, ойна тўлиқ эмас'),
    EVIDENCE_V4_MISSING: ('V4 не получен', 'V4 олинмаган'),
    EVIDENCE_NO_V4_AT_SOURCE: ('DJI не хранит V4', 'DJI V4 ни сақламайди'),
}

BRIDGE_NOTE = ('Промежуточный ручной участок; не исключается автоматически.',
               'Оралиқ қўлда бошқарилган қисм; автоматик чиқарилмайди.')

TOOLTIPS = {
    'raw': ('Исходная площадь, полученная от DJI. Не изменяется программой.',
            'DJI дан олинган дастлабки майдон. Дастур уни ўзгартирмайди.'),
    'excluded': ('Площадь, для которой дополнительное доказательство '
                 'подтвердило завышение.',
                 'Қўшимча далил ошириб кўрсатилганини тасдиқлаган майдон.'),
    'after': ('DJI RAW минус только доказанные корректировки. Нерешённые '
              'записи пока остаются внутри по RAW.',
              'DJI RAW дан фақат исботланган тузатишлар айирилган. Ҳал '
              'қилинмаган ёзувлар ҳозирча RAW бўйича ичида қолади.'),
    'pending': ('Сильные кандидаты, которые программа пока не корректирует.',
                'Дастур ҳозирча тузатмаётган кучли номзодлар.'),
    'review': ('Есть доказательство, не позволяющее безопасно принять '
               'автоматическое решение.',
               'Автоматик қарорни хавфсиз қабул қилишга имкон бермайдиган '
               'далил бор.'),
}

STATUS_COMPLETE = ('Все записи периода разрешены.',
                   'Даврнинг барча ёзувлари ҳал қилинган.')
STATUS_OPEN = ('Есть нерешённые записи; они пока учтены по DJI RAW.',
               'Ҳал қилинмаган ёзувлар бор; улар ҳозирча DJI RAW бўйича '
               'ҳисобга олинган.')


def pick(pair, lang):
    return pair[0] if lang == 'ru' else pair[1]


def ha(value_m2):
    return None if value_m2 is None else float(value_m2) / M2_PER_HA


def _flags(row):
    flags = row.get('anomaly_flags')
    if flags is None:
        try:
            flags = json.loads(row.get('anomaly_flags_json') or '[]')
        except ValueError:
            flags = []
    return [str(flag) for flag in flags if flag]


def _bridges(row):
    if row.get('structural_candidate') not in (True, 1):
        return []
    try:
        ids = json.loads(row.get('bridge_flight_ids_json') or '[]')
    except ValueError:
        return []
    return [int(i) for i in ids if isinstance(i, (int, float))
            or str(i).isdigit()]


def evidence_state(row):
    if 'NO_V4_AT_SOURCE' in _flags(row):
        return EVIDENCE_NO_V4_AT_SOURCE
    if not row.get('v4_revision_id') and not row.get('v4_summary_id'):
        return EVIDENCE_V4_MISSING
    if row.get('aggregation_eligibility') == rs.AGG_CERTIFIED:
        return EVIDENCE_V4_GOOD
    return EVIDENCE_V4_LIMITED


def explanation(decision, row, lang):
    """(код причины, текст) -- человеку, а не enum резолвера."""
    cls = decision['accounting_class']
    if cls == acc.PHANTOM_PROVEN:
        accepted = decision['accounted_area_m2'] or 0.0
        structural = decision['reason'] == acc.R_RETAINED_VALIDATED
        if accepted > 0:
            code = EXPLAIN_PARTIAL if structural else EXPLAIN_CONTROL_PARTIAL
            return code, pick(EXPLANATIONS[code], lang) % (
                '%.4f' % ha(accepted))
        code = EXPLAIN_REPEAT if structural else EXPLAIN_CONTROL
        return code, pick(EXPLANATIONS[code], lang)
    if cls == acc.PHANTOM_STRUCTURAL:
        code = (EXPLAIN_PENDING_NO_V4
                if evidence_state(row) == EVIDENCE_NO_V4_AT_SOURCE
                else EXPLAIN_PENDING)
        return code, pick(EXPLANATIONS[code], lang)
    code = decision['reason']
    pair = EXPLANATIONS.get(code) or EXPLANATIONS[acc.R_UNMAPPED_STATUS]
    return code, pick(pair, lang)


def record_view(row, lang='ru'):
    """Одна строка реестра. RAW не меняется; исключается только доказанное."""
    decision = acc.classify(row)
    cls = decision['accounting_class']
    raw = decision['raw_area_m2']
    if cls == acc.PHANTOM_PROVEN:
        accepted = decision['accounted_area_m2']
        excluded = decision['confirmed_overstatement_m2']
    else:
        accepted = raw
        excluded = 0.0 if raw is not None else None
    code, text = explanation(decision, row, lang)
    state = evidence_state(row)
    flight_id = int(row['flight_id'])
    chain = bool(row.get('structural_candidate') in (True, 1)
                 and row.get('candidate_base_flight_id'))
    return {
        'flight_id': flight_id,
        'report_start_date': row.get('report_start_date'),
        'machine_key': row.get('machine_key'),
        'machine_label': row.get('machine_label'),
        'accounting_class': cls,
        'class_label': pick(CLASS_LABELS[cls], lang),
        'class_badge': CLASS_BADGES[cls],
        'raw_m2': raw,
        'accepted_m2': accepted,
        'excluded_m2': excluded,
        'controller_delta_m2': row.get('controller_delta_area_m2'),
        'reason_code': code,
        'reason_text': text,
        'evidence_state': state,
        'evidence_label': pick(EVIDENCE_LABELS[state], lang),
        'base_flight_id': row.get('candidate_base_flight_id') if chain
        else None,
        'bridge_flight_ids': _bridges(row) if chain else [],
        'dji_url': DJI_RECORD_URL % flight_id,
        'structural_rule_version': row.get('structural_rule_version'),
        'area_algorithm_version': row.get('area_algorithm_version'),
    }


def _totals_view(totals):
    pending = totals['class_records'][acc.PHANTOM_STRUCTURAL]
    review = totals['class_records'][acc.REVIEW]
    return {
        'records': totals['records'],
        'raw_m2': totals['raw_sum_m2'],
        'raw_missing_records': totals['raw_missing_records'],
        'excluded_m2': totals['confirmed_overstatement_m2'],
        'excluded_records': totals['class_records'][acc.PHANTOM_PROVEN],
        'after_m2': totals['raw_minus_confirmed_overstatement_m2'],
        'pending_m2': totals['class_raw_m2'][acc.PHANTOM_STRUCTURAL],
        'pending_records': pending,
        'review_m2': totals['class_raw_m2'][acc.REVIEW],
        'review_records': review,
        'complete': pending == 0 and review == 0,
        'partition_holds': totals['partition_holds'],
    }


def build(rows, lang='ru', view=VIEW_ALL):
    """Итог периода, разрез по дронам и реестр -- из одной выборки строк."""
    if view not in VIEWS:
        view = VIEW_ALL
    summary = acc.summarize(rows, key_fn=lambda r: r.get('machine_key'))
    labels = {}
    for row in rows:
        labels.setdefault(row.get('machine_key'), row.get('machine_label'))
    drones = []
    for key, totals in summary['by_key'].items():
        item = _totals_view(totals)
        item['machine_key'] = key
        item['machine_label'] = labels.get(key)
        drones.append(item)
    # Полезный порядок: где больше всего доказанно исключено -- сверху.
    drones.sort(key=lambda d: (-d['excluded_m2'], str(d['machine_label'])))

    wanted = _VIEW_CLASSES.get(view, REGISTER_CLASSES)
    register = []
    for row in rows:
        item = record_view(row, lang)
        if item['accounting_class'] in wanted:
            register.append(item)
    register.sort(key=lambda r: (str(r['report_start_date'] or ''),
                                 r['flight_id']), reverse=True)

    total = _totals_view(summary['total'])
    total['status_text'] = pick(STATUS_COMPLETE if total['complete']
                                else STATUS_OPEN, lang)
    return {'report_version': REPORT_VERSION, 'view': view, 'total': total,
            'drones': drones, 'register': register}


# ─── Книга «Отчёт контроля площади DJI» ──────────────────────────────────────

SHEET_SUMMARY = ('Сводка', 'Жамланма')
SHEET_DRONES = ('По_дронам', 'Дронлар_бўйича')
SHEET_CORRECTIONS = ('Корректировки', 'Тузатишлар')
SHEET_REVIEW = ('Требует_проверки', 'Текшириш_керак')


def build_workbook(report, lang='ru', period=('', ''), versions=None):
    """openpyxl.Workbook из результата `build(..., view=VIEW_ALL)`.

    [REASON]: NULL -- ПУСТАЯ ячейка, никогда не 0: книга -- та копия, которую
    суммируют, и ноль в колонке читался бы как измеренный ноль.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font

    def tr(ru, uz):
        return ru if lang == 'ru' else uz

    versions = versions or {}
    total = report['total']
    link_font = Font(color='0563C1', underline='single')
    bold = Font(bold=True)

    def header(sheet, titles):
        sheet.append(titles)
        for cell in sheet[1]:
            cell.font = bold
        sheet.freeze_panes = 'A2'

    book = Workbook()
    sheet = book.active
    sheet.title = pick(SHEET_SUMMARY, lang)
    header(sheet, [tr('Показатель', 'Кўрсаткич'), tr('Значение', 'Қиймат'),
                   tr('Записей', 'Ёзувлар'), tr('Пояснение', 'Изоҳ')])
    sheet.append([tr('Период: с', 'Давр: бошланиши'), period[0] or None])
    sheet.append([tr('Период: по', 'Давр: тугаши'), period[1] or None])
    sheet.append([tr('DJI RAW, га', 'DJI RAW, га'), ha(total['raw_m2']),
                  total['records'], pick(TOOLTIPS['raw'], lang)])
    sheet.append([tr('Подтверждённо исключено, га',
                     'Тасдиқланган ҳолда чиқарилган, га'),
                  ha(total['excluded_m2']), total['excluded_records'],
                  pick(TOOLTIPS['excluded'], lang)])
    sheet.append([tr('Площадь после подтверждённых корректировок, га',
                     'Тасдиқланган тузатишлардан кейинги майдон, га'),
                  ha(total['after_m2']), None, pick(TOOLTIPS['after'], lang)])
    sheet.append([tr('Ожидает доказательства / V4, га',
                     'Далил / V4 кутилмоқда, га'),
                  ha(total['pending_m2']), total['pending_records'],
                  pick(TOOLTIPS['pending'], lang)])
    sheet.append([tr('Требует проверки, га', 'Текшириш талаб қилинади, га'),
                  ha(total['review_m2']), total['review_records'],
                  pick(TOOLTIPS['review'], lang)])
    sheet.append([tr('Записей без RAW', 'RAW йўқ ёзувлар'),
                  total['raw_missing_records']])
    sheet.append([tr('Статус периода', 'Давр ҳолати'), total['status_text']])
    for title, key in ((tr('Алгоритм площади', 'Майдон алгоритми'),
                        'area_algorithm'),
                       (tr('Структурное правило', 'Структура қоидаси'),
                        'structural_rule'),
                       (tr('Учётные классы', 'Ҳисоб синфлари'),
                        'accounting_classes'),
                       (tr('Версия отчёта', 'Ҳисобот версияси'), 'report')):
        sheet.append([title, versions.get(key)])

    sheet = book.create_sheet(pick(SHEET_DRONES, lang))
    header(sheet, [tr('Дрон', 'Дрон'), tr('Вылетов', 'Парвозлар'),
                   tr('DJI RAW, га', 'DJI RAW, га'),
                   tr('Подтверждённо исключено, га',
                      'Тасдиқланган ҳолда чиқарилган, га'),
                   tr('После подтверждённых корректировок, га',
                      'Тасдиқланган тузатишлардан кейин, га'),
                   tr('Ожидает V4, га', 'V4 кутилмоқда, га'),
                   tr('Требует проверки, га', 'Текшириш керак, га')])
    for drone in report['drones']:
        sheet.append([drone['machine_label'], drone['records'],
                      ha(drone['raw_m2']), ha(drone['excluded_m2']),
                      ha(drone['after_m2']), ha(drone['pending_m2']),
                      ha(drone['review_m2'])])

    def register_sheet(title, rows):
        sheet = book.create_sheet(title)
        header(sheet, [
            tr('Дата', 'Сана'), tr('Дрон', 'Дрон'), 'C Flight ID',
            'A Flight ID', tr('Bridge IDs (не корректируются)',
                              'Bridge IDs (тузатилмайди)'),
            tr('DJI RAW, га', 'DJI RAW, га'),
            tr('Принято, га', 'Қабул қилинган, га'),
            tr('Исключено, га', 'Чиқарилган, га'), tr('Класс', 'Синф'),
            tr('Причина', 'Сабаб'), tr('Доказательство', 'Далил'),
            tr('Прирост счётчика V4, м²', 'V4 ҳисоблагич ўсиши, м²'),
            tr('Ссылка DJI', 'DJI ҳаволаси')])
        for item in rows:
            day = item['report_start_date']
            sheet.append([
                day.isoformat() if hasattr(day, 'isoformat') else day,
                item['machine_label'], item['flight_id'],
                item['base_flight_id'],
                '; '.join(str(b) for b in item['bridge_flight_ids']) or None,
                ha(item['raw_m2']), ha(item['accepted_m2']),
                ha(item['excluded_m2']), item['class_label'],
                item['reason_text'], item['evidence_label'],
                item['controller_delta_m2'], item['dji_url']])
            cell = sheet.cell(row=sheet.max_row, column=13)
            cell.hyperlink = item['dji_url']
            cell.font = link_font

    register_sheet(pick(SHEET_CORRECTIONS, lang),
                   [r for r in report['register']
                    if r['accounting_class'] == acc.PHANTOM_PROVEN])
    register_sheet(pick(SHEET_REVIEW, lang),
                   [r for r in report['register']
                    if r['accounting_class'] in (acc.PHANTOM_STRUCTURAL,
                                                 acc.REVIEW)])
    return book
