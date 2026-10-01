# -*- coding: utf-8 -*-
"""dji_area/field_view.py -- привязка вылета к полю DJI словами человека.

DRONE-FIELD-PASSPORT-001. Резолвер поля (`dji_area.field`) и его строки
`dji_field_attributions` здесь НЕ меняются: модуль только читает уже
записанный результат и переводит уровень (TIER1..TIER5) и метод резолвера в
короткое состояние, понятное владельцу, с причиной и подписями на двух
языках. Ни ввода-вывода, ни Flask, ни базы.

Состояния:

* ``EXACT``       -- TIER1_EXACT: запись DJI определена, байты исторической
                     границы сохранены и сверены по md5.
* ``IDENTIFIED``  -- TIER2_STRONG: запись DJI определена уверенно, но байтов
                     именно той версии границы нет.
* ``PROBABLE``    -- TIER3_SUPPORTED: опора на родство через ДРУГИЕ вылеты,
                     а не на прямое доказательство этого.
* ``CANDIDATE``   -- TIER4_GEOMETRIC: геометрический кандидат по СЕГОДНЯШНЕМУ
                     полигону. Только диагностика.
* ``AMBIGUOUS``   -- противоречие родства: несколько несовместимых записей.
* ``UNRESOLVED``  -- TIER5 с причиной: карточка не собрана, в карточке нет
                     ключа поля, ключ появился после расчёта привязки, ключа
                     нет в каталоге, ключ не разобран, прочее.
* ``NOT_RESOLVED``-- у вылета нет текущей строки привязки вовсе (резолвер для
                     него не запускался).

[REASON]: в подтверждённый итог поля входят ТОЛЬКО ``EXACT`` и
``IDENTIFIED``. Предположение, геометрический кандидат и противоречие
показываются отдельно и гектаров к полю не добавляют: иначе карточка поля
выдавала бы гипотезу за доказанную работу.

Отсутствие расчёта площади (Area Control) -- свойство площади, а не поля;
его даёт `dji_area.accepted` («не рассчитано»), здесь оно не выводится.
"""

import json

from dji_area import field as fld

STATE_EXACT = 'EXACT'
STATE_IDENTIFIED = 'IDENTIFIED'
STATE_PROBABLE = 'PROBABLE'
STATE_CANDIDATE = 'CANDIDATE'
STATE_AMBIGUOUS = 'AMBIGUOUS'
STATE_UNRESOLVED = 'UNRESOLVED'
STATE_NOT_RESOLVED = 'NOT_RESOLVED'
STATES = (STATE_EXACT, STATE_IDENTIFIED, STATE_PROBABLE, STATE_CANDIDATE,
          STATE_AMBIGUOUS, STATE_UNRESOLVED, STATE_NOT_RESOLVED)

# [REASON]: единственное место, где решено, что считается подтверждённой
# работой поля. Хранилище (`field_store`) и экраны берут его отсюда.
CONFIRMED_STATES = (STATE_EXACT, STATE_IDENTIFIED)
# Связаны с полем, но не подтверждены: показываются отдельной таблицей.
PROVISIONAL_STATES = (STATE_PROBABLE, STATE_CANDIDATE)

CONFIRMED_TIERS = (fld.TIER1_EXACT, fld.TIER2_STRONG)
PROVISIONAL_TIERS = (fld.TIER3_SUPPORTED, fld.TIER4_GEOMETRIC)

REASON_NO_CARD = 'NO_CARD'
REASON_NO_KEY = 'NO_KEY'
REASON_KEY_AFTER_RESOLUTION = 'KEY_AFTER_RESOLUTION'
REASON_NOT_IN_CATALOG = 'NOT_IN_CATALOG'
REASON_UNPARSED = 'UNPARSED'
REASON_LINEAGE_CONFLICT = 'LINEAGE_CONFLICT'
REASON_OTHER = 'OTHER'
REASONS = (REASON_NO_CARD, REASON_NO_KEY, REASON_KEY_AFTER_RESOLUTION,
           REASON_NOT_IN_CATALOG, REASON_UNPARSED, REASON_LINEAGE_CONFLICT,
           REASON_OTHER)

# Методы резолвера без ключа поля (`field.resolve_field`).
METHODS_NO_KEY = ('AUTO_NO_KEY', 'MANUAL_NO_KEY')
METHOD_MANUAL_NO_KEY = 'MANUAL_NO_KEY'
METHOD_UNPARSED = 'UNPARSED_KEY'

STATE_LABELS = {
    STATE_EXACT: ('Подтверждено, граница сохранена',
                  'Тасдиқланган, чегара сақланган'),
    STATE_IDENTIFIED: ('Подтверждено; историческая граница не сохранена',
                       'Тасдиқланган; тарихий чегара сақланмаган'),
    STATE_PROBABLE: ('Предположительно', 'Тахминий'),
    STATE_CANDIDATE: ('Геометрический кандидат', 'Геометрик номзод'),
    STATE_AMBIGUOUS: ('Неоднозначно', 'Ноаниқ'),
    STATE_UNRESOLVED: ('Поле не определено', 'Дала аниқланмаган'),
    STATE_NOT_RESOLVED: ('Привязка не рассчитана', 'Боғланиш ҳисобланмаган'),
}
STATE_BADGES = {
    STATE_EXACT: 'vs-badge-success',
    STATE_IDENTIFIED: 'vs-badge-info',
    STATE_PROBABLE: 'vs-badge-warning',
    STATE_CANDIDATE: 'vs-badge-warning',
    STATE_AMBIGUOUS: 'vs-badge-danger',
    STATE_UNRESOLVED: '',
    STATE_NOT_RESOLVED: '',
}
STATE_HELP = {
    STATE_EXACT: (
        'Карточка вылета DJI называет эту границу, и её байты сохранены и '
        'сверены по контрольной сумме. Это та граница, что была у пульта '
        'при взлёте.',
        'DJI парвоз карточкаси шу чегарани кўрсатади, унинг байтлари '
        'сақланган ва назорат йиғиндиси бўйича солиштирилган. Бу пульт '
        'учишда фойдаланган чегара.'),
    STATE_IDENTIFIED: (
        'Запись поля DJI определена уверенно, но байты той версии границы, '
        'по которой летали, не сохранены. Сегодняшняя граница за '
        'историческую не выдаётся.',
        'DJI дала ёзуви ишонч билан аниқланган, лекин училган чегара '
        'версиясининг байтлари сақланмаган. Бугунги чегара тарихий деб '
        'кўрсатилмайди.'),
    STATE_PROBABLE: (
        'Поддержано другими вылетами, а не прямым доказательством этого '
        'вылета. В подтверждённые гектары поля не входит.',
        'Бу парвознинг тўғридан-тўғри далили эмас, бошқа парвозлар орқали '
        'қўллаб-қувватланган. Даланинг тасдиқланган гектарларига кирмайди.'),
    STATE_CANDIDATE: (
        'Маршрут попал в сегодняшний полигон. Это диагностика, а не '
        'доказательство поля; в гектары поля не входит.',
        'Маршрут бугунги полигонга тушган. Бу дала далили эмас, '
        'диагностика; дала гектарларига кирмайди.'),
    STATE_AMBIGUOUS: (
        'Доказательства указывают на несколько несовместимых записей поля. '
        'Нужна проверка человеком.',
        'Далиллар бир нечта мос келмайдиган дала ёзувларини кўрсатади. '
        'Инсон текшируви керак.'),
    STATE_UNRESOLVED: (
        'Поле по доказательствам не определено.',
        'Дала далиллар бўйича аниқланмаган.'),
    STATE_NOT_RESOLVED: (
        'Для вылета нет текущей привязки к полю: резолвер его не '
        'рассчитывал (обычно — вылет вне периода расчётов Area Control).',
        'Парвоз учун далага жорий боғланиш йўқ: резолвер уни '
        'ҳисобламаган (одатда — Area Control ҳисоблари давридан ташқари).'),
}
REASON_LABELS = {
    REASON_NO_CARD: ('карточка вылета не собрана',
                     'парвоз карточкаси йиғилмаган'),
    REASON_NO_KEY: ('в карточке нет ключа поля',
                    'карточкада дала калити йўқ'),
    REASON_KEY_AFTER_RESOLUTION: (
        'ключ поля получен после расчёта привязки; нужен пересчёт',
        'дала калити боғланиш ҳисобидан кейин олинган; қайта ҳисоб керак'),
    REASON_NOT_IN_CATALOG: ('ключа нет в каталоге полей',
                            'калит далалар каталогида йўқ'),
    REASON_UNPARSED: ('ключ поля не разобран', 'дала калити ўқилмаган'),
    REASON_LINEAGE_CONFLICT: ('противоречие родства записей',
                              'ёзувлар қариндошлигида зиддият'),
    REASON_OTHER: ('прочая причина', 'бошқа сабаб'),
}
# Ручной режим без ключа -- уточнение причины NO_KEY.
MANUAL_NOTE = ('ручной режим', 'қўлда бошқариш')

WARNING_LABELS = {
    'MULTIPLE_GEOMETRY_HOLDERS': (
        'та же граница у нескольких записей DJI; вылет учтён в одной из них',
        'шу чегара бир нечта DJI ёзувида; парвоз фақат биттасида '
        'ҳисобланган'),
    'HOLDER_UUID_NE_LINKED_UUID': (
        'байты границы хранит другая запись DJI',
        'чегара байтларини бошқа DJI ёзуви сақлайди'),
    'LINKED_LAND_UUID_NOT_IN_CATALOG': (
        'запись DJI из ключа удалена из каталога',
        'калитдаги DJI ёзуви каталогдан ўчирилган'),
    'HISTORICAL_GEOMETRY_UNAVAILABLE': (
        'байты исторической границы не сохранены',
        'тарихий чегара байтлари сақланмаган'),
    'LINEAGE_CONFLICT': ('противоречие родства', 'қариндошлик зиддияти'),
    'GEOMETRY_KEY_UNRECOGNIZED': ('ключ поля не распознан',
                                  'дала калити танилмаган'),
    'TIER4_AMBIGUOUS_CANDIDATES': (
        'несколько геометрических кандидатов',
        'бир нечта геометрик номзод'),
}

CONFIDENCE_LABELS = {
    fld.CONF_HIGH: ('высокая', 'юқори'),
    fld.CONF_MEDIUM: ('средняя', 'ўртача'),
    fld.CONF_LOW: ('низкая', 'паст'),
    fld.CONF_UNKNOWN: ('не определена', 'аниқланмаган'),
}

BOUNDARY_SAVED = 'SAVED_VERIFIED'
BOUNDARY_SAVED_UNVERIFIED = 'SAVED_UNVERIFIED'
BOUNDARY_MISSING = 'MISSING'
BOUNDARY_LABELS = {
    BOUNDARY_SAVED: ('сохранена и сверена', 'сақланган ва солиштирилган'),
    BOUNDARY_SAVED_UNVERIFIED: ('сохранена, не сверена',
                                'сақланган, солиштирилмаган'),
    BOUNDARY_MISSING: ('историческая граница не сохранена',
                       'тарихий чегара сақланмаган'),
}

M2_PER_HA = 10000.0
# Площадь каталога DJI -- в mu (у кабинета), 15 mu = 1 га.
MU_PER_HA = 15.0


def pick(pair, lang):
    return pair[0] if lang == 'ru' else pair[1]


def _get(row, key):
    if row is None:
        return None
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def warnings_of(attr):
    """Коды предупреждений строки привязки списком; мусор -- пустой список."""
    raw = _get(attr, 'warnings_json')
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(value, list):
        return []
    return [str(code) for code in value if code]


def _has_card(evidence):
    return bool(_get(evidence, 'card_revision_id'))


def _card_key(evidence):
    value = _get(evidence, 'card_geometry_md5')
    return (value or '').strip() if isinstance(value, str) else ''


def classify(attr, evidence=None):
    """(состояние, причина) по строке привязки и указателям доказательств.

    ``attr`` -- текущая строка `dji_field_attributions` (словарь или
    sqlite3.Row) либо None; ``evidence`` -- строка `dji_flight_evidence`
    либо None. Причина есть только у ``UNRESOLVED`` и ``AMBIGUOUS``.
    """
    if attr is None:
        return STATE_NOT_RESOLVED, None
    tier = _get(attr, 'field_attribution_tier')
    method = _get(attr, 'field_attribution_method') or ''
    if tier == fld.TIER1_EXACT:
        return STATE_EXACT, None
    if tier == fld.TIER2_STRONG:
        return STATE_IDENTIFIED, None
    if tier == fld.TIER3_SUPPORTED:
        return STATE_PROBABLE, None
    if tier == fld.TIER4_GEOMETRIC:
        return STATE_CANDIDATE, None
    # TIER5 и всё незнакомое: незнакомый уровень не повышается до
    # подтверждения, а становится неопределённым с причиной «прочее».
    if method.endswith('LINEAGE_CONFLICT') \
            or 'LINEAGE_CONFLICT' in warnings_of(attr):
        return STATE_AMBIGUOUS, REASON_LINEAGE_CONFLICT
    if method in METHODS_NO_KEY:
        # [REASON]: резолвер отвечает «ключа нет» одинаково, собрана
        # карточка или нет: в обоих случаях `card_geometry_md5` пуст. Разница
        # видна только по указателю на ревизию карточки -- без новой колонки.
        # «Карточка не собрана» лечится сбором, «ключа нет» -- нет; смешать
        # их значит спрятать, сколько вылетов вообще можно привязать.
        if not _has_card(evidence):
            return STATE_UNRESOLVED, REASON_NO_CARD
        if _card_key(evidence):
            return STATE_UNRESOLVED, REASON_KEY_AFTER_RESOLUTION
        return STATE_UNRESOLVED, REASON_NO_KEY
    if method == METHOD_UNPARSED:
        return STATE_UNRESOLVED, REASON_UNPARSED
    if method.endswith('NOT_IN_CATALOG'):
        return STATE_UNRESOLVED, REASON_NOT_IN_CATALOG
    return STATE_UNRESOLVED, REASON_OTHER


def is_confirmed(attr, evidence=None):
    return classify(attr, evidence)[0] in CONFIRMED_STATES


def boundary_state(geometry_row):
    """Состояние байтов границы по строке `dji_land_geometries` (без тела)."""
    if geometry_row is None:
        return BOUNDARY_MISSING
    if _get(geometry_row, 'md5_verified'):
        return BOUNDARY_SAVED
    return BOUNDARY_SAVED_UNVERIFIED


def short_hash(value, length=12):
    if not value:
        return ''
    text = str(value)
    return text if len(text) <= length else text[:length] + '…'


def mu_to_ha(value):
    if value is None:
        return None
    try:
        return float(value) / MU_PER_HA
    except (TypeError, ValueError):
        return None


def attribution_view(attr, evidence=None, lang='ru'):
    """Привязка одного вылета для шаблона: состояние, причина, подписи.

    Ничего не вычисляется заново: каждое поле -- из записанной строки
    резолвера. ``geometry_md5`` -- версия границы, названная карточкой
    вылета; ею, а не сегодняшней ревизией, определяется историческая
    граница вылета.
    """
    state, reason = classify(attr, evidence)
    method = _get(attr, 'field_attribution_method')
    reason_text = pick(REASON_LABELS[reason], lang) if reason else ''
    if reason == REASON_NO_KEY and method == METHOD_MANUAL_NO_KEY:
        reason_text += ' (%s)' % pick(MANUAL_NOTE, lang)
    warnings = warnings_of(attr)
    warning_texts = []
    for code in warnings:
        if code.startswith('TIER4_CANDIDATE_ONLY_'):
            continue
        pair = WARNING_LABELS.get(code)
        warning_texts.append(pick(pair, lang) if pair else code)
    confidence = _get(attr, 'field_confidence')
    return {
        'state': state,
        'reason': reason,
        'state_label': pick(STATE_LABELS[state], lang),
        'state_badge': STATE_BADGES[state],
        'state_help': pick(STATE_HELP[state], lang),
        'reason_text': reason_text,
        'confirmed': state in CONFIRMED_STATES,
        'provisional': state in PROVISIONAL_STATES,
        'tier': _get(attr, 'field_attribution_tier'),
        'method': method,
        'confidence': confidence,
        'confidence_label': (pick(CONFIDENCE_LABELS[confidence], lang)
                             if confidence in CONFIDENCE_LABELS else ''),
        'field_land_uuid': _get(attr, 'field_land_uuid'),
        'field_name': _get(attr, 'field_name_at_snapshot'),
        'field_serial': _get(attr, 'field_serial_number'),
        'linked_land_uuid': _get(attr, 'linked_land_uuid'),
        'geometry_holder_land_uuid': _get(attr, 'geometry_holder_land_uuid'),
        'geometry_md5': _get(attr, 'geometry_md5'),
        'geometry_key_format': _get(attr, 'geometry_key_format'),
        'historical_geometry_available': bool(
            _get(attr, 'historical_geometry_available')),
        'historical_geometry_sha256': _get(attr,
                                           'historical_geometry_sha256'),
        'holder_count': _get(attr, 'holder_count'),
        'warnings': warnings,
        'warning_texts': warning_texts,
        'resolver_version': _get(attr, 'field_resolver_version'),
        'field_input_hash': _get(attr, 'field_input_hash'),
        'calculated_at': _get(attr, 'calculated_at'),
        'land_snapshot_id': _get(attr, 'land_snapshot_id'),
        'land_revision_id': _get(attr, 'land_revision_id'),
        'tier4_inside_share': _get(attr, 'tier4_inside_share'),
    }


def empty_census():
    """Счётчики переписи привязок одного периода (или месяца)."""
    out = {
        'flights': 0,
        'with_calculation': 0,
        'with_attribution': 0,
        'with_historical_bytes': 0,
        'md5_without_bytes': 0,
        'states': dict.fromkeys(STATES, 0),
        'reasons': dict.fromkeys(REASONS, 0),
    }
    return out


def census_add(bucket, attr, evidence, has_calc, count=1):
    """Добавить ``count`` вылетов одного вида в корзину переписи.

    Вид -- то, что классификация читает: строка привязки (или её значимые
    поля) и указатели карточки. Так перепись и экраны делят одну функцию.
    """
    state, reason = classify(attr, evidence)
    bucket['flights'] += count
    if has_calc:
        bucket['with_calculation'] += count
    if attr is not None:
        bucket['with_attribution'] += count
        if _get(attr, 'historical_geometry_available'):
            bucket['with_historical_bytes'] += count
        elif _get(attr, 'geometry_md5'):
            bucket['md5_without_bytes'] += count
    bucket['states'][state] += count
    if reason:
        bucket['reasons'][reason] += count
    return bucket


def census_shares(bucket):
    """Доли подтверждённых и неопределённых, %; None на пустой корзине."""
    total = bucket['flights']
    if not total:
        return {'confirmed_pct': None, 'unresolved_pct': None,
                'no_card_pct': None}
    confirmed = sum(bucket['states'][s] for s in CONFIRMED_STATES)
    return {
        'confirmed_pct': 100.0 * confirmed / total,
        'unresolved_pct': 100.0 * bucket['states'][STATE_UNRESOLVED] / total,
        'no_card_pct': 100.0 * bucket['reasons'][REASON_NO_CARD] / total,
    }
