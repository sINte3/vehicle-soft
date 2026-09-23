# -*- coding: utf-8 -*-
"""dji_area/decisions.py -- решение администратора поверх автоматического расчёта.

DRONE-AREA-CONTROL-V2-MEGA, блок B. Здесь НЕТ ввода-вывода, Flask и базы:
только словарь решений, правило «какое решение допустимо для какой записи» и
правило «что отчёт принимает, если решение есть». Писатель таблицы
`drone_area_decisions` -- `dji_area/control_store.py`, читатели -- отчёт и
тесты.

ТРИ СЛОЯ, КОТОРЫЕ НЕЛЬЗЯ СМЕШИВАТЬ

1. DJI RAW -- то, что записал DJI. Не меняется никогда.
2. Автоматический результат -- `dji_area_calculations` + `dji_area.accounting`.
   Решение администратора его НЕ переписывает: строка расчёта остаётся той
   же, её класс и принятая площадь -- тоже.
3. Эффективный результат отчёта -- автоматический, либо, если есть
   действующее решение, результат решения. Живёт только в отчёте.

[REASON]: решение -- отдельная append-only история, а не поле расчёта.
Пересчёт порождает новую строку расчёта на каждый новый вход; решение,
записанное в строку расчёта, пропадало бы с её вытеснением, а UPDATE прежнего
решения стирал бы, кто и почему решил иначе. Отмена и исправление -- новая
строка, которая замещает прежнюю (`supersedes_decision_id`), и порядковый
номер в цепочке вылета (`chain_seq`) держит историю линейной: две
одновременные правки одного вылета не могут обе стать «текущей».

ЭФФЕКТИВНАЯ ПЛОЩАДЬ РЕШЕНИЯ

    CONFIRM_FULL_PHANTOM  принято 0, исключено весь RAW;
    ACCEPT_AUTO_RESULT    принято то, что принял автомат (для корректировки --
                          проверенный прирост, для спорной -- RAW), запись
                          считается разрешённой;
    KEEP_DJI_RAW          принято RAW, исключено 0 -- фантом/коррекция
                          отклонены;
    NEEDS_MORE_EVIDENCE   принято RAW, исключено 0, спор ОСТАЁТСЯ ОТКРЫТЫМ.

Это не `billable_area_m2` и не коммерческий итог: техническая/операционная
площадь контроля, и слово «счёт» здесь не звучит.
"""

CONFIRM_FULL_PHANTOM = 'CONFIRM_FULL_PHANTOM'
ACCEPT_AUTO_RESULT = 'ACCEPT_AUTO_RESULT'
KEEP_DJI_RAW = 'KEEP_DJI_RAW'
NEEDS_MORE_EVIDENCE = 'NEEDS_MORE_EVIDENCE'
DECISION_TYPES = (CONFIRM_FULL_PHANTOM, ACCEPT_AUTO_RESULT, KEEP_DJI_RAW,
                  NEEDS_MORE_EVIDENCE)
# Отмена -- тоже строка истории: действующего решения после неё нет, и
# запись возвращается к автоматической семантике.
REVOKE = 'REVOKE'
ACTIONS = DECISION_TYPES + (REVOKE,)

DECISIONS_VERSION = 'dji-area-decisions-1'

# Учётные классы автомата (`dji_area.accounting`), к которым решение
# применимо. Обычные записи (NORMAL) -- тысячи вылетов без признака
# аномалии; объявлять фантомом вылет, о котором не сказал ни экран, ни
# счётчик, -- не разрешение спора, а новое правило, и в этом макроэтапе его
# нет.
AUTO_PROVEN = 'PHANTOM_PROVEN'
AUTO_STRUCTURAL = 'PHANTOM_STRUCTURAL'
AUTO_REVIEW = 'REVIEW'
AUTO_NORMAL = 'NORMAL'
DECIDABLE_CLASSES = (AUTO_PROVEN, AUTO_STRUCTURAL, AUTO_REVIEW)
# Доказанный автоматический результат. Решение по нему -- ПЕРЕОПРЕДЕЛЕНИЕ:
# отдельный путь, дополнительное подтверждение, обязательная причина.
OVERRIDE_CLASSES = (AUTO_PROVEN,)

COMMENT_MIN_CHARS = 5
COMMENT_MAX_CHARS = 2000

# ─── Эффективное состояние записи ────────────────────────────────────────────

S_ACCEPTED_DJI = 'ACCEPTED_DJI'            # автомат: обычная запись
S_CORRECTED = 'CORRECTED'                  # автомат: доказанная корректировка
S_PENDING = 'PENDING_EVIDENCE'             # автомат: ждёт V4, по RAW
S_NEEDS_DECISION = 'NEEDS_DECISION'        # автомат: спорная, по RAW
S_ADMIN_PHANTOM = 'ADMIN_CONFIRMED_PHANTOM'
S_ADMIN_ACCEPTED_AUTO = 'ADMIN_ACCEPTED_AUTO'
S_ADMIN_KEPT_RAW = 'ADMIN_KEPT_RAW'
S_ADMIN_NEEDS_EVIDENCE = 'ADMIN_NEEDS_EVIDENCE'
STATES = (S_ACCEPTED_DJI, S_CORRECTED, S_PENDING, S_NEEDS_DECISION,
          S_ADMIN_PHANTOM, S_ADMIN_ACCEPTED_AUTO, S_ADMIN_KEPT_RAW,
          S_ADMIN_NEEDS_EVIDENCE)
# Подтверждённые корректировки: то, что вычтено из DJI.
CORRECTION_STATES = (S_CORRECTED, S_ADMIN_PHANTOM)
# Ждут доказательства -- автоматика ещё может решить сама.
PENDING_STATES = (S_PENDING,)
# Ждут человека. «Нужны доказательства» от администратора -- тоже открыто.
HUMAN_STATES = (S_NEEDS_DECISION, S_ADMIN_NEEDS_EVIDENCE)
OPEN_STATES = PENDING_STATES + HUMAN_STATES

# (ru, uz). Узбекский -- кириллицей; латиница только в DJI/RAW/V4.
DECISION_LABELS = {
    CONFIRM_FULL_PHANTOM: ('Подтвердить полный фантом (принять 0)',
                           'Тўлиқ фантомни тасдиқлаш (0 қабул қилиш)'),
    ACCEPT_AUTO_RESULT: ('Принять автоматический результат',
                         'Автоматик натижани қабул қилиш'),
    KEEP_DJI_RAW: ('Оставить площадь DJI RAW',
                   'DJI RAW майдонини қолдириш'),
    NEEDS_MORE_EVIDENCE: ('Нужны дополнительные доказательства',
                          'Қўшимча далиллар керак'),
    REVOKE: ('Отменить решение', 'Қарорни бекор қилиш'),
}
# Короткие подписи для таблиц и книги.
DECISION_SHORT = {
    CONFIRM_FULL_PHANTOM: ('Полный фантом', 'Тўлиқ фантом'),
    ACCEPT_AUTO_RESULT: ('Принят автоматический результат',
                         'Автоматик натижа қабул қилинган'),
    KEEP_DJI_RAW: ('DJI RAW оставлен', 'DJI RAW қолдирилган'),
    NEEDS_MORE_EVIDENCE: ('Нужны доказательства', 'Далиллар керак'),
    REVOKE: ('Решение отменено', 'Қарор бекор қилинган'),
}
DECISION_HELP = {
    CONFIRM_FULL_PHANTOM: (
        'Вылет — повтор площади. Принимается 0 га, весь DJI RAW этого '
        'вылета исключается. Промежуточный вылет B не затрагивается.',
        'Парвоз — майдон такрори. 0 га қабул қилинади, бу парвознинг бутун '
        'DJI RAW майдони чиқарилади. Оралиқ B парвозига тегилмайди.'),
    ACCEPT_AUTO_RESULT: (
        'Согласиться с тем, что посчитала программа. Запись считается '
        'разрешённой.',
        'Дастур ҳисоблаганига рози бўлиш. Ёзув ҳал қилинган ҳисобланади.'),
    KEEP_DJI_RAW: (
        'Фантома нет: принимается площадь DJI RAW, ничего не исключается.',
        'Фантом йўқ: DJI RAW майдони қабул қилинади, ҳеч нарса '
        'чиқарилмайди.'),
    NEEDS_MORE_EVIDENCE: (
        'Решить пока нельзя. Площадь остаётся по DJI RAW, запись остаётся '
        'в списке «требует решения».',
        'Ҳозирча ҳал қилиб бўлмайди. Майдон DJI RAW бўйича қолади, ёзув '
        '«қарор талаб қилинади» рўйхатида қолади.'),
    REVOKE: (
        'Действующее решение отменяется; запись возвращается к '
        'автоматическому результату. История сохраняется.',
        'Амалдаги қарор бекор қилинади; ёзув автоматик натижага қайтади. '
        'Тарих сақланади.'),
}

STATE_LABELS = {
    S_ACCEPTED_DJI: ('Принято по DJI', 'DJI бўйича қабул қилинган'),
    S_CORRECTED: ('Подтверждённая корректировка', 'Тасдиқланган тузатиш'),
    S_PENDING: ('Ожидает доказательства', 'Далил кутилмоқда'),
    S_NEEDS_DECISION: ('Требует решения', 'Қарор талаб қилинади'),
    S_ADMIN_PHANTOM: ('Подтверждённая корректировка (решение администратора)',
                      'Тасдиқланган тузатиш (администратор қарори)'),
    S_ADMIN_ACCEPTED_AUTO: ('Принято (решение администратора)',
                            'Қабул қилинган (администратор қарори)'),
    S_ADMIN_KEPT_RAW: ('DJI RAW оставлен (решение администратора)',
                       'DJI RAW қолдирилган (администратор қарори)'),
    S_ADMIN_NEEDS_EVIDENCE: ('Нужны доказательства (решение администратора)',
                             'Далиллар керак (администратор қарори)'),
}
# Семантика дизайн-системы; цвет -- подсказка, смысл несёт слово рядом.
STATE_BADGES = {
    S_ACCEPTED_DJI: '',
    S_CORRECTED: 'vs-badge-info',
    S_PENDING: 'vs-badge-warning',
    S_NEEDS_DECISION: 'vs-badge-danger',
    S_ADMIN_PHANTOM: 'vs-badge-info',
    S_ADMIN_ACCEPTED_AUTO: 'vs-badge-success',
    S_ADMIN_KEPT_RAW: 'vs-badge-success',
    S_ADMIN_NEEDS_EVIDENCE: 'vs-badge-warning',
}

STALE_NOTE = ('Автоматический расчёт изменился после решения — проверьте.',
              'Қарордан кейин автоматик ҳисоб ўзгарди — текширинг.')
LAPSED_NOTE = ('Решение «принять автоматический результат» относилось к '
               'прежнему расчёту и больше не действует.',
               '«Автоматик натижани қабул қилиш» қарори олдинги ҳисобга '
               'тегишли эди ва энди амал қилмайди.')

# Отказы проверки решения: код -> (ru, uz).
E_UNKNOWN_ACTION = 'UNKNOWN_ACTION'
E_NOT_DECIDABLE = 'NOT_DECIDABLE'
E_COMMENT_REQUIRED = 'COMMENT_REQUIRED'
E_COMMENT_TOO_LONG = 'COMMENT_TOO_LONG'
E_OVERRIDE_NOT_CONFIRMED = 'OVERRIDE_NOT_CONFIRMED'
E_NOT_CONFIRMED = 'NOT_CONFIRMED'
E_NOTHING_TO_REVOKE = 'NOTHING_TO_REVOKE'
E_RAW_MISSING = 'RAW_MISSING'
E_STALE_FORM = 'STALE_FORM'
E_STALE_CALC = 'STALE_CALC'
E_SAME_AS_ACTIVE = 'SAME_AS_ACTIVE'
ERRORS = {
    E_UNKNOWN_ACTION: ('Неизвестное решение.', 'Номаълум қарор.'),
    E_NOT_DECIDABLE: (
        'Для этой записи решение не предусмотрено: она принята по DJI без '
        'признаков завышения.',
        'Бу ёзув учун қарор кўзда тутилмаган: у ошириб кўрсатиш белгиларисиз '
        'DJI бўйича қабул қилинган.'),
    E_COMMENT_REQUIRED: ('Укажите причину решения (не короче %d символов).'
                         % COMMENT_MIN_CHARS,
                         'Қарор сабабини кўрсатинг (камида %d белги).'
                         % COMMENT_MIN_CHARS),
    E_COMMENT_TOO_LONG: ('Комментарий слишком длинный.',
                         'Изоҳ жуда узун.'),
    E_OVERRIDE_NOT_CONFIRMED: (
        'Это переопределение доказанного автоматического результата. '
        'Отметьте дополнительное подтверждение.',
        'Бу исботланган автоматик натижани қайта белгилаш. Қўшимча '
        'тасдиқни белгиланг.'),
    E_NOT_CONFIRMED: ('Подтвердите решение отметкой.',
                      'Қарорни белги билан тасдиқланг.'),
    E_NOTHING_TO_REVOKE: ('Действующего решения нет — отменять нечего.',
                          'Амалдаги қарор йўқ — бекор қиладиган нарса йўқ.'),
    E_RAW_MISSING: ('У записи нет площади DJI — это решение к ней '
                    'неприменимо.',
                    'Ёзувда DJI майдони йўқ — бу қарор унга татбиқ '
                    'этилмайди.'),
    E_STALE_FORM: ('Пока форма была открыта, решение по этой записи '
                   'изменилось. Обновите страницу и проверьте историю.',
                   'Шакл очиқ турганда бу ёзув бўйича қарор ўзгарди. '
                   'Саҳифани янгиланг ва тарихни текширинг.'),
    E_STALE_CALC: ('Пока форма была открыта, автоматический расчёт этой '
                   'записи изменился. Обновите страницу и проверьте новый '
                   'результат.',
                   'Шакл очиқ турганда бу ёзувнинг автоматик ҳисоби '
                   'ўзгарди. Саҳифани янгиланг ва янги натижани текширинг.'),
    E_SAME_AS_ACTIVE: ('Такое решение уже действует.',
                       'Бундай қарор аллақачон амал қилмоқда.'),
}


def pick(pair, lang):
    return pair[0] if lang == 'ru' else pair[1]


def is_override(auto_class):
    return auto_class in OVERRIDE_CLASSES


def allowed_actions(auto_class, raw_m2, active):
    """Какие действия администратор может выбрать для записи.

    ``active`` -- действующее решение (словарь) либо None. Отмена доступна
    только при действующем решении.

    [REASON]: отмена доступна ВСЕГДА, когда решение действует, -- даже если
    пересчёт перевёл запись в обычные (например, V4 пришёл и опроверг
    кандидата). Иначе решение, принятое по ожидающей записи, оставалось бы в
    силе навсегда без способа его снять.
    """
    if auto_class not in DECIDABLE_CLASSES:
        return (REVOKE,) if active is not None else ()
    out = []
    for action in DECISION_TYPES:
        if raw_m2 is None and action in (CONFIRM_FULL_PHANTOM, KEEP_DJI_RAW):
            continue
        out.append(action)
    if active is not None:
        out.append(REVOKE)
    return tuple(out)


def validate(action, auto_class, raw_m2, active, comment, confirmed,
             override_confirmed, expected_active_id, active_stale=False):
    """Код отказа либо None. Чистая функция: её держат тесты.

    ``expected_active_id`` -- id действующего решения, каким его видела
    форма (None, если решения не было). Расхождение -- правка поверх чужой.
    ``active_stale`` -- действующее решение принято против прежнего расчёта:
    тогда то же решение можно подтвердить заново, против нынешнего.
    """
    if action not in ACTIONS:
        return E_UNKNOWN_ACTION
    if auto_class not in DECIDABLE_CLASSES and not (
            action == REVOKE and active is not None):
        return E_NOT_DECIDABLE
    current_id = active['id'] if active is not None else None
    if (expected_active_id or None) != (current_id or None):
        return E_STALE_FORM
    if action == REVOKE and active is None:
        return E_NOTHING_TO_REVOKE
    if raw_m2 is None and action in (CONFIRM_FULL_PHANTOM, KEEP_DJI_RAW):
        return E_RAW_MISSING
    if active is not None and action == active.get('decision_type') \
            and not active_stale:
        return E_SAME_AS_ACTIVE
    text = (comment or '').strip()
    if len(text) < COMMENT_MIN_CHARS:
        return E_COMMENT_REQUIRED
    if len(text) > COMMENT_MAX_CHARS:
        return E_COMMENT_TOO_LONG
    if not confirmed:
        return E_NOT_CONFIRMED
    if is_override(auto_class) and action != REVOKE and not override_confirmed:
        return E_OVERRIDE_NOT_CONFIRMED
    return None


def auto_state(auto_class):
    return {AUTO_NORMAL: S_ACCEPTED_DJI, AUTO_PROVEN: S_CORRECTED,
            AUTO_STRUCTURAL: S_PENDING}.get(auto_class, S_NEEDS_DECISION)


def calc_identity(row):
    """Устойчивая идентичность автоматического расчёта: версия + отпечаток."""
    return (row.get('area_algorithm_version'),
            row.get('calculation_input_hash'))


def decision_is_stale(decision, row):
    if decision is None:
        return False
    return ((decision.get('area_algorithm_version'),
             decision.get('calculation_input_hash')) != calc_identity(row))


def effective(auto_class, raw_m2, auto_accepted_m2, auto_excluded_m2,
              decision, stale=False):
    """(состояние, принято м², исключено м², применено ли решение).

    [REASON]: `ACCEPT_AUTO_RESULT` привязан к ТОМУ расчёту, который
    администратор видел. Если пересчёт дал другой результат, согласие с
    прежним не переносится на новый молча: решение перестаёт действовать, и
    запись возвращается к автоматической семантике (с пометкой). Три других
    решения от автомата не зависят -- 0, RAW, RAW -- и продолжают действовать,
    но тоже с пометкой «расчёт изменился».
    """
    base = auto_state(auto_class)
    if decision is None or decision.get('decision_type') not in DECISION_TYPES:
        return base, auto_accepted_m2, auto_excluded_m2, False
    kind = decision['decision_type']
    if kind == ACCEPT_AUTO_RESULT:
        if stale:
            return base, auto_accepted_m2, auto_excluded_m2, False
        state = S_CORRECTED if auto_class == AUTO_PROVEN \
            else S_ADMIN_ACCEPTED_AUTO
        return state, auto_accepted_m2, auto_excluded_m2, True
    if raw_m2 is None:
        # Нечего ни исключать, ни принимать: решение фиксируется, числа пусты.
        state = (S_ADMIN_NEEDS_EVIDENCE if kind == NEEDS_MORE_EVIDENCE
                 else S_ADMIN_KEPT_RAW)
        return state, None, None, True
    if kind == CONFIRM_FULL_PHANTOM:
        return S_ADMIN_PHANTOM, 0.0, float(raw_m2), True
    if kind == KEEP_DJI_RAW:
        return S_ADMIN_KEPT_RAW, float(raw_m2), 0.0, True
    return S_ADMIN_NEEDS_EVIDENCE, float(raw_m2), 0.0, True
