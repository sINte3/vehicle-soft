# -*- coding: utf-8 -*-
"""B2 -- метод сверки вида работ. Размечает владелец, код не угадывает.

Четыре метода -- из плана трека (раздел 3, B2): гектары, время, рейсы, не
сверяется. Хранятся ASCII-слагами; миграция AGRO_WORK_001 держит их CHECK.

ЧЕГО ЗДЕСЬ НЕТ НАМЕРЕННО: никакого вывода метода из единицы API. «HECTARE ->
гектары» выглядит очевидным, но это решение владельца, а не кода (устав:
бизнес-правила не выдумываются), и у agro-work есть виды работ, где
единица и способ проверки расходятся -- почасовая работа трактора
закрывается «соатами», а сверяться может и по следу. Пока метод не
размечен, заявка этого вида получает «метод не размечен» и без вердикта.
"""

from . import store

METHOD_GA = 'ga'
METHOD_TIME = 'vremya'
METHOD_TRIPS = 'reysy'
METHOD_NONE = 'ne_sveryaetsya'
METHODS = (METHOD_GA, METHOD_TIME, METHOD_TRIPS, METHOD_NONE)

# (ru, uz). Русская подпись -- значение выпадающего списка в xlsx.
LABELS = {
    METHOD_GA: ('гектары', 'гектарлар'),
    METHOD_TIME: ('время', 'вақт'),
    METHOD_TRIPS: ('рейсы', 'рейслар'),
    METHOD_NONE: ('не сверяется', 'солиштирилмайди'),
}

# Что код делает с заявкой каждого метода. Это описание поведения сверки
# (agro_work/reconcile.py), а не правило: владелец выбирает, зная его.
MEANING = {
    METHOD_GA: ('Работа по GPS ищется участками работы трека (метод трека '
                'GPS, порог 0,3 га). Вердикт: была работа в окне заявки или нет.',
                'GPS бўйича иш трекдаги иш участкалари бўйича изланади (GPS '
                'трек усули, 0,3 га остонаси). Ҳукм: буюртма ойнасида иш '
                'бўлганми ёки йўқ.'),
    METHOD_TIME: ('Работа сверяется временем. Трек GPS время работы пока не '
                  'публикует: заявка остаётся без вердикта, пока он его не '
                  'даст.',
                  'Иш вақт бўйича солиштирилади. GPS трек иш вақтини ҳали '
                  'эълон қилмайди: у бермагунча буюртма ҳукмсиз қолади.'),
    METHOD_TRIPS: ('Работа сверяется рейсами (посещение геозон). Это этап 5 '
                   'трека GPS, пока не публикуется: заявка без вердикта.',
                   'Иш рейслар бўйича солиштирилади (геозоналарга кириш). Бу '
                   'GPS трекининг 5-босқичи, ҳали эълон қилинмайди: буюртма '
                   'ҳукмсиз.'),
    METHOD_NONE: ('Заявка этого вида в сверку не входит вовсе.',
                  'Бу турдаги буюртма солиштиришга умуман кирмайди.'),
}


class BadMethod(ValueError):
    """Значение ячейки не является методом."""


def parse_method(value):
    """Слаг метода, None для пустой ячейки, BadMethod для всего прочего.

    Принимаются слаг и обе подписи, без учёта регистра и краевых пробелов.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    folded = text.casefold()
    for slug, (ru, uz) in LABELS.items():
        if folded in (slug, ru.casefold(), uz.casefold()):
            return slug
    raise BadMethod(text)


def current_methods(con):
    return {row['id']: row['method'] for row in con.execute(
        'SELECT id, method FROM agro_work_work_types')}


def work_types_for_markup(con):
    """Виды работ с единицей и числом заявок -- для файла разметки."""
    return [dict(row) for row in con.execute(
        "SELECT w.id, w.name, w.unit, w.unit_display, w.method, "
        "COUNT(a.id) AS applications, MIN(a.created_day) AS first_day, "
        "MAX(a.created_day) AS last_day "
        "FROM agro_work_work_types w "
        "LEFT JOIN agro_work_applications a ON a.work_type_id = w.id "
        "AND a.gone_at IS NULL "
        "GROUP BY w.id ORDER BY applications DESC, w.name, w.id")]


SET, CHANGE, CLEAR, SAME = 'set', 'change', 'clear', 'same'


def plan(current, wanted):
    """Что изменит разметка. wanted -- {id: слаг или None}.

    Возвращает (изменения [(id, старый, новый, вид)], неизвестные id).
    Вид работы, которого нет в файле, не трогается: отсутствие строки --
    не решение «метод снят».
    """
    changes, unknown = [], []
    for work_type_id, method in wanted.items():
        if work_type_id not in current:
            unknown.append(work_type_id)
            continue
        old = current[work_type_id]
        if old == method:
            kind = SAME
        elif old is None:
            kind = SET
        elif method is None:
            kind = CLEAR
        else:
            kind = CHANGE
        changes.append((work_type_id, old, method, kind))
    return changes, unknown


def apply(con, changes, source):
    """Записать разметку одной транзакцией, с журналом. Число строк."""
    when = store.stamp(store.utc_now())
    written = 0
    con.execute('BEGIN')
    try:
        for work_type_id, old, new, kind in changes:
            if kind == SAME:
                continue
            cursor = con.execute(
                'UPDATE agro_work_work_types SET method = ?, method_set_at = ?, '
                'method_source = ? WHERE id = ? AND method IS ?',
                (new, when, source[:200], work_type_id, old))
            if cursor.rowcount != 1:
                raise RuntimeError('work type %s changed while planning'
                                   % work_type_id)
            store.journal(con, store.SOURCE_METHODS, store.ENTITY_WORK_TYPE,
                          work_type_id, 'method', old, new, when)
            written += 1
        con.commit()
    except Exception:
        con.rollback()
        raise
    return written
