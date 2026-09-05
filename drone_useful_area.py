# -*- coding: utf-8 -*-
"""drone_useful_area.py -- DRONE-USEFUL-AREA-001: расчёт полезной площади.

Здесь живёт ОДИН production-расчёт показателя
`Расчётная полезная площадь, га` (`estimated_useful_area_ha`). Действующая
версия правил -- `useful-area-v2`; формула у обеих версий одна:

    estimated_useful_area_ha =
        area(union(buffer(work_segments, spray_width / 2))
             intersect field_polygon) / 10000

ДВЕ ВЕРСИИ, И ОБЕ СЧИТАЮТСЯ

`useful-area-v1` считала точки маршрута выборкой телеметрии и отбрасывала
отрезок длиннее `gap_m = 60 м` как разрыв записи. Живой пилот 2026-09-04
показал, что DJI отдаёт `data_type=simplified` -- вершины упрощённой ломаной,
где прямой проход записан двумя точками, -- и правило разрыва вырезало 69.8 %
длины маршрута. `useful-area-v2` разрыва по расстоянию не имеет: точки --
вершины ломаной, а «не работа» решают контур (проверенный изнутри отрезка) и
курс. Всё остальное -- ширина, объединение, обрезка, растровая погрешность,
статусы -- общее.

Версия не переключатель, а СВОЙСТВО ПАРАМЕТРОВ: `version_of_params()` выводит
её из политики точек маршрута, и число под версией v1 считается только по
правилам v1. Старая строка под старой версией воспроизводится байт в байт --
это держат регрессионные тесты и сухой прогон `--algorithm-version
useful-area-v1` инструмента пересчёта, который ничего не пишет.

ЧЕГО ЗДЕСЬ НЕТ

Flask, SQLAlchemy, `models`, `app`. Модуль импортируется и веб-приложением, и
инструментом пересчёта, и тестами -- и ни один из трёх не обязан ради него
поднимать приложение. `from app import app` тут был бы прямым нарушением:
`create_app()` зовёт `db.create_all()` на импорте и превращает читателя в
писателя.

ПОЧЕМУ ФОРМУЛА НЕ ПЕРЕПИСАНА ЗАНОВО

Геометрию считают уже проверенные чистые функции
`drone_collector/area_study.py`: `plane_for`, `classify_segments`,
`coverage_with_uncertainty`, `choose_contour`, `group_flights`. Второй
экземпляр формулы -- это два числа под одним именем, которые разойдутся на
первом же исправлении. Здесь только решение о СТАТУСЕ: какое число можно
назвать готовым, а какое нельзя назвать вовсе.

[REASON]: `drone_collector.area_study` тянет за собой только stdlib (это
держит тест `test_useful_area_imports_without_flask`). Playwright в цепочке
импорта нет: он подгружается лениво внутри функций `main.py`/`browser.py`.
Правило устава «в `drone_collector/` не тащить зависимости приложения и
наоборот» здесь не нарушено -- зависимость односторонняя и состоит из чистых
функций без ввода-вывода.

ЧТО ЭТОТ ПОКАЗАТЕЛЬ НЕ ЗНАЧИТ

Признак включённого насоса в источнике DJI не доказан (`docs/
DJI_AREA_48H_DECISION.md`). «Рабочий проход» здесь -- вывод геометрии, а не
наблюдение за опрыскиванием. Число называется расчётным всюду, где
показывается.
"""

import hashlib
import json

from drone_collector.area_study import (
    DEFAULT_PARAMS,
    ROUTE_POLICY_POLYLINE,
    ROUTE_POLICY_TELEMETRY,
    SEG_WORK,
    SIMPLIFIED_ROUTE_PARAMS,
    StudyParams,
    AreaStudyError,
    CONTOUR_AMBIGUOUS as _AREA_CONTOUR_AMBIGUOUS,
    CONTOUR_MATCHED as _AREA_CONTOUR_MATCHED,
    CONTOUR_NOT_MATCHED as _AREA_CONTOUR_NOT_MATCHED,
    CONTOUR_NOT_OFFERED as _AREA_CONTOUR_NOT_OFFERED,
    classify_segments,
    coverage_once,
    coverage_with_uncertainty,
    choose_contour,
    group_flights,
    plane_for,
)

# Версии правил расчёта. Версия меняется вместе со СМЫСЛОМ числа, а не при
# правке опечатки. Хранится рядом с каждым результатом: число без объяснимой
# версии правил хранить нельзя.
ALGORITHM_VERSION_V1 = 'useful-area-v1'
ALGORITHM_VERSION_V2 = 'useful-area-v2'
ALGORITHM_VERSIONS = (ALGORITHM_VERSION_V1, ALGORITHM_VERSION_V2)

# Действующая версия. Всё, что считается без явно названной версии, считается
# по ней; смена этой константы меняет отпечаток входа каждой строки и потому
# ЗАСТАВЛЯЕТ пересчёт переписать все строки -- старое число под новой версией
# остаться не может.
ALGORITHM_VERSION = ALGORITHM_VERSION_V2

# Параметры каждой версии берутся из `area_study`, а не переписываются
# числами: расхождение между «параметрами исследования» и «параметрами
# production» было бы невидимым. v1 -- проверенные в DJI-AREA-48H
# (политика телеметрии, `gap_m = 60`); v2 -- политика ломаной.
PARAMS_BY_VERSION = {
    ALGORITHM_VERSION_V1: DEFAULT_PARAMS,
    ALGORITHM_VERSION_V2: SIMPLIFIED_ROUTE_PARAMS,
}
_VERSION_BY_POLICY = {
    ROUTE_POLICY_TELEMETRY: ALGORITHM_VERSION_V1,
    ROUTE_POLICY_POLYLINE: ALGORITHM_VERSION_V2,
}
PARAMS = PARAMS_BY_VERSION[ALGORITHM_VERSION]


def params_for_version(version):
    """Параметры названной версии. Незнакомая версия -- отказ, не догадка."""
    try:
        return PARAMS_BY_VERSION[version]
    except KeyError:
        raise AreaStudyError('unknown algorithm version: %r; known: %s'
                             % (version, ', '.join(ALGORITHM_VERSIONS)))


def version_of_params(params=None):
    """Версия правил, которой соответствуют параметры.

    [REASON]: версия выводится из политики точек маршрута, а не передаётся
    рядом с параметрами отдельным аргументом. Два аргумента можно передать
    несогласованно -- «useful-area-v1» при правилах v2, -- и тогда под старой
    версией лежало бы новое число. Один источник истины такой пары не
    допускает.
    """
    return _VERSION_BY_POLICY[(params or PARAMS).route_policy]

# ─── Статусы качества ────────────────────────────────────────────────────────
#
# Ровно один статус на работу. Число, пригодное для итоговой сводки,
# появляется ТОЛЬКО при `READY_ESTIMATE`.

READY_ESTIMATE = 'READY_ESTIMATE'
PARTIAL_DATA = 'PARTIAL_DATA'
DATA_UNAVAILABLE = 'DATA_UNAVAILABLE'
CONTOUR_AMBIGUOUS = 'CONTOUR_AMBIGUOUS'
CONTOUR_NOT_MATCHED = 'CONTOUR_NOT_MATCHED'
ROUTE_INVALID = 'ROUTE_INVALID'

QUALITY_STATUSES = (READY_ESTIMATE, PARTIAL_DATA, DATA_UNAVAILABLE,
                    CONTOUR_AMBIGUOUS, CONTOUR_NOT_MATCHED, ROUTE_INVALID)

# Единственный статус, чьё число входит в итоговые суммы.
SUMMABLE_STATUSES = (READY_ESTIMATE,)

# ─── Машинно-читаемые причины ────────────────────────────────────────────────
#
# [REASON]: причина -- константа, а не собранная строка. Строку нельзя
# отфильтровать в запросе и нельзя перевести, не сломав при первой же правке
# формулировки.

REASON_OK = 'ALL_INPUTS_PRESENT'
REASON_NO_ROUTE = 'NO_ROUTE_STORED_FOR_ANY_FLIGHT'
REASON_SOME_FLIGHTS_WITHOUT_ROUTE = 'SOME_FLIGHTS_HAVE_NO_STORED_ROUTE'
REASON_WIDTH_MISSING_ON_WORK_PASS = 'SPRAY_WIDTH_ABSENT_ON_A_WORK_PASS'
REASON_CONTOUR_AMBIGUOUS = 'TWO_CONTOURS_FIT_EQUALLY_WELL'
REASON_CONTOUR_NOT_MATCHED = 'NO_CONTOUR_MATCHED_THE_ROUTE'
REASON_CONTOUR_NOT_OFFERED = 'NO_CONTOUR_CANDIDATE_WAS_OFFERED'
REASON_ROUTE_UNUSABLE = 'STORED_ROUTE_CARRIES_NO_USABLE_GEOMETRY'
# [REASON]: у машины за день несколько пространственно РАЗНЫХ работ, и есть
# вылет, маршрута которого нет. К какой из работ он относится, неизвестно, и
# узнать это неоткуда: рамки у него нет. Приписать его первой работе значило
# бы оставить остальные в READY_ESTIMATE и посчитать их полными -- при том,
# что любая из них могла недосчитаться этого вылета. Поэтому полной не
# считается ни одна.
REASON_UNROUTED_FLIGHT_NOT_ASSIGNABLE = (
    'UNROUTED_FLIGHT_CANNOT_BE_ASSIGNED_TO_ONE_WORK_OF_THE_DAY')

# Причины, по которым площадь остаётся `None`. Ноль вместо `None` читался бы
# как измеренный ноль -- то есть как утверждение «дрон ничего не обработал».
NULL_AREA_REASONS = (REASON_NO_ROUTE, REASON_WIDTH_MISSING_ON_WORK_PASS,
                     REASON_CONTOUR_AMBIGUOUS, REASON_CONTOUR_NOT_MATCHED,
                     REASON_CONTOUR_NOT_OFFERED, REASON_ROUTE_UNUSABLE,
                     REASON_SOME_FLIGHTS_WITHOUT_ROUTE,
                     REASON_UNROUTED_FLIGHT_NOT_ASSIGNABLE)


def algorithm_params(params=None):
    """Снимок параметров, который ложится рядом с числом."""
    return (params or PARAMS).as_dict()


def params_from_snapshot(snapshot):
    """Параметры из сохранённого снимка. Незнакомый ключ -- отказ.

    [REASON]: молча проигнорированный ключ означал бы, что старый результат
    пересчитан по НОВЫМ правилам под старой версией. Отказ заставляет поднять
    версию алгоритма, а не притвориться, что ничего не изменилось.
    """
    # [REASON]: «знакомые» ключи -- объединение по ВСЕМ версиям, а не ключи
    # действующей: снимок строки v1 несёт `gap_m`, которого у v2 нет, и
    # сверка с ключами v2 отвергла бы каждую строку v1 как незнакомую.
    known = StudyParams.KNOWN_KEYS
    unknown = sorted(set(snapshot or {}) - known)
    if unknown:
        raise AreaStudyError('unknown algorithm parameter(s): %s'
                             % ', '.join(unknown))
    return StudyParams(**{key: snapshot[key] for key in snapshot})


# ─── Отпечаток входа ─────────────────────────────────────────────────────────

def route_fingerprint(routes):
    """Устойчивый отпечаток МНОЖЕСТВА входных маршрутов работы.

    Складывается из пар «идентификатор вылета -> хеш содержимого маршрута»,
    отсортированных по идентификатору. Отсюда три свойства, на которых стоит
    идемпотентность пересчёта:

    * порядок маршрутов на вход не влияет;
    * изменившееся содержимое ОДНОГО маршрута меняет отпечаток работы целиком;
    * добавленный или убранный маршрут меняет отпечаток.

    [REASON]: отпечаток берётся по хешу содержимого, а не по времени приёма.
    Повторный приём того же тела не двигает время, но если бы двигал --
    пересчёт считал бы вход изменившимся и переписывал бы результат каждым
    прогоном сборщика.
    """
    parts = sorted('%s:%s' % (flight_id, sha)
                   for flight_id, sha in routes)
    digest = hashlib.sha256('|'.join(parts).encode('utf-8'))
    return digest.hexdigest()


def geometry_fingerprint(document):
    """Устойчивый SHA-256 НОРМАЛИЗОВАННОЙ геометрии контура.

    Нормализация -- сортировка ключей и фиксированные разделители. Отсюда два
    свойства, ради которых отпечаток и нужен:

    * переформатирование одного и того же полигона (порядок ключей, отступы,
      пробелы) отпечаток НЕ меняет -- иначе каждый повторный снимок
      справочника объявлял бы все работы изменившимися;
    * исправление координат или колец меняет его обязательно.

    [REASON]: значения координат отсюда никуда не выходят -- ни в лог, ни в
    отчёт, ни в сообщение об ошибке. Наружу идёт только хеш, по которому
    полигон не восстановить.
    """
    if document is None:
        return None
    text = json.dumps(document, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'))
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _stable_number(value):
    """Число в устойчивой текстовой форме. `None` -> 'NULL'.

    [REASON]: `repr(float)` в Python 3 обратим и детерминирован, но 2 и 2.0
    дают разный текст при равном значении. Приведение к float делает форму
    одной, иначе отпечаток менялся бы от того, как СУБД вернула значение --
    целым или дробным, -- а не от того, что оно изменилось.
    """
    if value is None or isinstance(value, bool):
        return 'NULL'
    if isinstance(value, (int, float)):
        return repr(float(value))
    return 'NULL'


def flight_input(flight_id, route_state, content_sha256=None, area_ha=None,
                 mission_uuid=None):
    """Нормализованное описание ОДНОГО вылета для отпечатка.

    Ровно те поля, любое из которых способно изменить сохранённую строку:

    * идентичность вылета -- появление или исчезновение вылета меняет
      `flights_total` и состав работы;
    * состояние маршрута (PRESENT / ABSENT / INVALID) -- от него зависит и
      статус качества, и то, в какую строку вылет попадёт;
    * хеш содержимого маршрута -- геометрия;
    * `area_ha` -- из неё складывается сохраняемая `dji_area_ha`;
    * `mission_uuid` -- из него выводится сохраняемый `mission_state`.

    [REASON]: значения наружу не выходят -- ни `mission_uuid`, ни
    идентификатор. Это слагаемое отпечатка, и дальше от него остаётся только
    SHA-256.
    """
    return '%s|%s|%s|%s|%s' % (flight_id, route_state or 'ABSENT',
                               content_sha256 or 'NO-CONTENT',
                               _stable_number(area_ha),
                               mission_uuid or 'NO-MISSION')


def flights_fingerprint(entries):
    """Устойчивый отпечаток МНОЖЕСТВА описаний вылетов.

    Порядок на вход не влияет: строки сортируются. Добавленный, убранный или
    изменившийся вылет отпечаток меняет.
    """
    digest = hashlib.sha256('|'.join(sorted(entries or ())).encode('utf-8'))
    return digest.hexdigest()


def contour_inputs_fingerprint(candidates):
    """Отпечаток ВСЕГО короткого списка контуров, а не только выбранного.

    `candidates` -- [(uuid, geojson)] в любом порядке.

    [REASON]: `contour_status` и `quality_reason` определяются набором
    кандидатов, а не победителем. Когда победителя нет, `uuid` равен `None` и
    в обоих состояниях -- CONTOUR_NOT_OFFERED («кандидатов не предложено») и
    CONTOUR_NOT_MATCHED («ни один не подошёл») -- он одинаков. Переход между
    ними, как и переход в CONTOUR_AMBIGUOUS, виден ТОЛЬКО по входу: сколько
    контуров попало в короткий список и какая у них геометрия. Отпечаток по
    одному победителю такие переходы пропускал, и в базе оставался прежний
    статус с прежней причиной.
    """
    parts = sorted('%s|%s' % (uuid or 'NO-UUID', geometry_fingerprint(geojson)
                              or 'NO-GEOMETRY')
                   for uuid, geojson in (candidates or ()))
    digest = hashlib.sha256('|'.join(parts).encode('utf-8'))
    return digest.hexdigest()


def inputs_fingerprint(flight_entries, params=None, algorithm_version=None,
                       contour_key=None, contour_geometry=None,
                       contour_candidates=None):
    """Отпечаток ВСЕГО, от чего зависит СОХРАНЁННАЯ СТРОКА.

    Не «от чего зависит площадь», а от чего зависит любая сохраняемая колонка
    `drone_coverage_works`. Это разные множества, и разница уже стоила
    неверного числа.

    Слагаемые:

    * описания всех вылетов, влияющих на эту строку (`flight_input`) -- в том
      числе тех, у кого маршрута нет или он негоден;
    * параметры алгоритма и его версия;
    * выбранный контур и его геометрия;
    * ВЕСЬ короткий список контуров-кандидатов с их геометриями.

    [REASON]: прежняя редакция брала только маршруты выбранной группы,
    параметры, версию и победивший контур. Из-за этого целый класс изменений
    не менял отпечаток вовсе, и `recalculate()` объявлял строку `unchanged`,
    оставляя в базе прежние `quality_status`, `flights_total` и
    `dji_area_ha`. Самый дорогой случай: у машины появлялся второй вылет БЕЗ
    маршрута, `compute_work()` честно отдавал PARTIAL_DATA -- а в базе
    оставался READY_ESTIMATE, и неполная работа продолжала попадать в
    итоговую полезную площадь. Маршрут первого вылета при этом не менялся, и
    отпечаток по маршрутам оставался прежним.
    """
    # [REASON]: версия выводится из параметров, и явно названная обязана с
    # ними сходиться. Отпечаток «v1» над правилами v2 сделал бы строку v1
    # неотличимой от пересчитанной по-новому.
    derived = version_of_params(params)
    if algorithm_version is not None and algorithm_version != derived:
        raise AreaStudyError(
            'algorithm version %r does not match the parameters, which '
            'belong to %r' % (algorithm_version, derived))
    payload = {
        'flights': flights_fingerprint(flight_entries),
        'params': algorithm_params(params),
        'algorithm_version': derived,
        'contour': contour_key,
        'contour_geometry': geometry_fingerprint(contour_geometry),
        'contour_candidates': contour_inputs_fingerprint(contour_candidates),
    }
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


# ─── Один расчёт ─────────────────────────────────────────────────────────────

class WorkCoverage(object):
    """Результат расчёта ОДНОЙ работы. Ровно то, что ложится в строку.

    `estimated_useful_area_ha` равно `None` всюду, где число назвать нельзя.
    Ноль здесь означает измеренный ноль и ничего больше.
    """

    __slots__ = ('quality_status', 'quality_reason',
                 'estimated_useful_area_ha', 'partial_estimate_ha',
                 'sum_independent_swaths_ha', 'swath_union_ha',
                 'clipped_all_ha', 'contour_status', 'contour_uuid',
                 'contour_area_ha', 'uncertainty_percent',
                 'algorithm_version', 'params', 'flights_total',
                 'routes_total', 'flights_without_route',
                 'flights_without_width', 'flights_without_width_on_work',
                 'work_segments', 'route_points', 'mission_state',
                 'diagnostics')

    # Поля, которые в строку НЕ ложатся и в отпечаток НЕ входят.
    #
    # [REASON]: `diagnostics` -- наблюдаемые величины сухого прогона: длина
    # маршрута по причинам отрезков и подразумеваемая скорость. Они объясняют
    # число, но не являются его частью: правила по ним ничего не решают, и
    # хранить их рядом с числом значило бы выдать наблюдение за критерий.
    NOT_STORED = ('diagnostics',)

    def __init__(self, **kwargs):
        for name in self.__slots__:
            setattr(self, name, kwargs.get(name))
        if self.diagnostics is None:
            self.diagnostics = {}

    @property
    def is_summable(self):
        """True только тогда, когда число можно класть в итоговую сумму."""
        return (self.quality_status in SUMMABLE_STATUSES
                and self.estimated_useful_area_ha is not None)

    def as_dict(self):
        return {name: getattr(self, name) for name in self.__slots__}


def implied_speed_mps(path_length_m, work_seconds):
    """Средняя скорость по длине ломаной и длительности вылета, м/с.

    НАБЛЮДАЕМАЯ ВЕЛИЧИНА, НЕ ПРАВИЛО. Ни один статус от неё не зависит.

    [REASON]: в evidence проекта нет доказанной физической верхней границы
    скорости DJI T40 (ни в документах, ни в тестах, ни в захваченных
    записях -- только имя поля `work_speed`). Порог, взятый из общих
    соображений, стал бы бизнес-правилом, придуманным сессией: устав это
    запрещает. Поэтому скорость печатается в сухом прогоне рядом с числом и
    остаётся зафиксированным остаточным риском: маршрут, невозможный
    физически, v2 сам по себе не отвергнет. Границу, если она появится,
    вводить через `ROUTE_INVALID` с новой машинно-читаемой причиной.
    Длительность -- `drone_flights.work_seconds` (DJI, у вылета целиком);
    ноль, отсутствие или не-число дают `None`, а не бесконечность.
    """
    if isinstance(work_seconds, bool):
        return None
    if not isinstance(work_seconds, (int, float)) or work_seconds <= 0:
        return None
    if not isinstance(path_length_m, (int, float)) or path_length_m < 0:
        return None
    return path_length_m / float(work_seconds)


def _usable_points(points):
    """Точки маршрута, годные для проекции. Мусор отбрасывается молча НЕ БУДЕТ.

    Возвращает список пар; вызывающий сам решает, что делать с коротким
    маршрутом.
    """
    usable = []
    for point in points or ():
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            return None
        lat, lng = point[0], point[1]
        if isinstance(lat, bool) or isinstance(lng, bool):
            return None
        if not isinstance(lat, (int, float)) or not isinstance(lng, (int, float)):
            return None
        if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lng <= 180.0):
            return None
        usable.append((float(lat), float(lng)))
    return usable


def compute_work(routes, candidates, params=None, flights_total=None,
                 mission_state=None, unassigned_flights=0):
    """Полезная площадь ОДНОЙ работы.

    `routes` -- маршруты работы: словари с `points` ([[lat, lng], ...]),
    `spray_width_m` (или `None`) и, необязательно, `flight_id`.
    `candidates` -- контуры-кандидаты: [{'uuid', 'geojson'}].
    `flights_total` -- сколько вылетов НАСЧИТАЛА работа всего, включая те, у
    которых маршрута нет. Меньше числа маршрутов быть не может.
    `unassigned_flights` -- сколько вылетов дня этой машины остались без
    маршрута ПРИ НЕСКОЛЬКИХ работах, то есть могли принадлежать этой работе, а
    могли и соседней. Такая работа полной не считается никогда, но и вылеты
    эти в её `flights_total` не входят: их считает своя строка, иначе один
    вылет попал бы в счётчики двух работ сразу.

    Порядок решений намеренно такой:

    1. годность маршрутов -- без геометрии считать нечего;
    2. контур -- неоднозначный или ненайденный контур останавливает расчёт
       ПЛОЩАДИ, но не расчёт полос: они объясняют число и без обрезки;
    3. ширина -- и только та, что нужна: у полностью холостого маршрута её
       отсутствие ничего не меняет, потому что рабочей полосы там нет вовсе.
    """
    params = params or PARAMS
    routes = list(routes or ())
    flights_total = (len(routes) if flights_total is None
                     else max(int(flights_total), len(routes)))
    flights_without_route = flights_total - len(routes)

    unassigned_flights = int(unassigned_flights or 0)
    base = {
        # Версия -- свойство параметров: под v1 считается только по правилам v1.
        'algorithm_version': version_of_params(params),
        'params': algorithm_params(params),
        'flights_total': flights_total,
        'routes_total': len(routes),
        'flights_without_route': flights_without_route,
        'flights_without_width': 0,
        'flights_without_width_on_work': 0,
        'work_segments': 0,
        'route_points': 0,
        'mission_state': mission_state,
        'contour_status': _AREA_CONTOUR_NOT_OFFERED,
        'estimated_useful_area_ha': None,
        'partial_estimate_ha': None,
    }

    # 1. Ни одного маршрута -- нечего считать, и это НЕ ноль гектаров.
    if not routes:
        return WorkCoverage(quality_status=DATA_UNAVAILABLE,
                            quality_reason=REASON_NO_ROUTE, **base)

    prepared = []
    for route in routes:
        points = _usable_points(route.get('points'))
        # Маршрут короче двух точек не даёт ни одного отрезка: полосы нет, и
        # «0.00 га» здесь было бы утверждением, которого мы не измеряли.
        if points is None or len(points) < 2:
            return WorkCoverage(quality_status=ROUTE_INVALID,
                                quality_reason=REASON_ROUTE_UNUSABLE, **base)
        prepared.append((route, points))

    all_points = [point for _route, points in prepared for point in points]
    base['route_points'] = len(all_points)
    try:
        plane = plane_for(all_points)
    except AreaStudyError:
        return WorkCoverage(quality_status=ROUTE_INVALID,
                            quality_reason=REASON_ROUTE_UNUSABLE, **base)

    # 2. Контур решается по НАСТОЯЩЕМУ полигону -- `choose_contour`, не рамка.
    choice = choose_contour(plane, all_points, candidates, params)
    rings = choice['rings']
    base['contour_status'] = choice['status']
    base['contour_uuid'] = choice['uuid']
    base['contour_area_ha'] = choice['area_ha']

    # 3. Отрезки и ширины. Классификация НЕ зависит от ширины: холостой ход
    #    остаётся холостым ходом и у вылета без записанной ширины.
    tracks = []
    without_width = 0
    without_width_on_work = 0
    work_segments = 0
    length_by_reason = {}
    speeds = []
    for route, points in prepared:
        segments = classify_segments(plane.project(points), params, rings)
        for segment in segments:
            length_by_reason[segment.reason] = (
                length_by_reason.get(segment.reason, 0.0) + segment.length)
        speed = implied_speed_mps(sum(s.length for s in segments),
                                  route.get('work_seconds'))
        if speed is not None:
            speeds.append(speed)
        width = route.get('spray_width_m')
        usable_width = (not isinstance(width, bool)
                        and isinstance(width, (int, float))
                        and width > 0)
        half = (float(width) / 2.0) if usable_width else None
        own_work = sum(1 for segment in segments if segment.reason == SEG_WORK)
        work_segments += own_work
        if not usable_width:
            without_width += 1
            if own_work:
                # [REASON]: ширина «нужна» ровно там, где есть что закрашивать.
                # Вылет без единого рабочего прохода не даёт рабочей полосы ни
                # при какой ширине, поэтому её отсутствие не делает число
                # неполным -- и, что важнее, не превращает нулевой вклад в
                # положительный.
                without_width_on_work += 1
        tracks.append((segments, half))

    base['flights_without_width'] = without_width
    base['flights_without_width_on_work'] = without_width_on_work
    base['work_segments'] = work_segments
    base['diagnostics'] = {
        'length_by_reason_m': {reason: round(length, 1) for reason, length
                               in sorted(length_by_reason.items())},
        'route_length_m': round(sum(length_by_reason.values()), 1),
        'flights_with_duration': len(speeds),
        'implied_speed_min_mps': (round(min(speeds), 2) if speeds else None),
        'implied_speed_max_mps': (round(max(speeds), 2) if speeds else None),
    }

    fine, _coarse, uncertainty = coverage_with_uncertainty(tracks, rings,
                                                           params)
    base['swath_union_ha'] = fine.swath_all_ha
    base['clipped_all_ha'] = fine.clipped_all_ha
    base['uncertainty_percent'] = uncertainty.get('clipped_work_ha')

    # Сумма НЕЗАВИСИМЫХ полос: каждый вылет измерен в одиночку и сложен.
    # Ровно то число, которое перекрытие завышает, -- оно объясняет разницу.
    independent = 0.0
    for segments, half in tracks:
        alone = coverage_once([(segments, half)], rings, params, params.cell_m)
        independent += alone.swath_all_ha or 0.0
    base['sum_independent_swaths_ha'] = round(independent, 4)

    # ── Статус ───────────────────────────────────────────────────────────────

    if choice['status'] == _AREA_CONTOUR_AMBIGUOUS:
        # Площадь `None`, не ноль: два одинаково подходящих поля -- это
        # незнание, а не пустая работа.
        return WorkCoverage(quality_status=CONTOUR_AMBIGUOUS,
                            quality_reason=REASON_CONTOUR_AMBIGUOUS, **base)
    if choice['status'] in (_AREA_CONTOUR_NOT_MATCHED,
                            _AREA_CONTOUR_NOT_OFFERED):
        reason = (REASON_CONTOUR_NOT_OFFERED
                  if choice['status'] == _AREA_CONTOUR_NOT_OFFERED
                  else REASON_CONTOUR_NOT_MATCHED)
        return WorkCoverage(quality_status=CONTOUR_NOT_MATCHED,
                            quality_reason=reason, **base)

    # Контур назначен. Дальше решает полнота входа.
    # Контур назначен (`_AREA_CONTOUR_MATCHED`) -- три ветки выше исчерпали
    # остальные значения.

    # `clipped_work_ha` равно `None`, когда НИ У ОДНОГО вылета нет ширины.
    # Это ещё не «нет числа»: если рабочих проходов нет вовсе, полезная
    # площадь честно равна нулю и от ширины не зависит.
    measured = fine.clipped_work_ha
    if measured is None:
        measured = 0.0 if work_segments == 0 else None

    if unassigned_flights:
        # Площадь посчитана и хранится как частичная оценка: геометрия
        # известных маршрутов верна. Итоговым числом она не становится --
        # работе мог принадлежать ещё один вылет, и какой именно, неизвестно.
        base['partial_estimate_ha'] = measured
        return WorkCoverage(
            quality_status=PARTIAL_DATA,
            quality_reason=REASON_UNROUTED_FLIGHT_NOT_ASSIGNABLE, **base)

    if without_width_on_work or flights_without_route:
        # Частичная диагностическая оценка хранится ОТДЕЛЬНО и в итоговые
        # суммы не входит: она посчитана по неполному входу.
        base['partial_estimate_ha'] = measured
        reason = (REASON_WIDTH_MISSING_ON_WORK_PASS if without_width_on_work
                  else REASON_SOME_FLIGHTS_WITHOUT_ROUTE)
        return WorkCoverage(quality_status=PARTIAL_DATA,
                            quality_reason=reason, **base)

    if measured is None:
        return WorkCoverage(quality_status=PARTIAL_DATA,
                            quality_reason=REASON_WIDTH_MISSING_ON_WORK_PASS,
                            **base)

    base['estimated_useful_area_ha'] = measured
    return WorkCoverage(quality_status=READY_ESTIMATE,
                        quality_reason=REASON_OK, **base)


# ─── Группировка ─────────────────────────────────────────────────────────────

def group_routes(rows, margin_deg=0.0015):
    """[(ключ_машины, локальный день, индекс, [строки])] -- работы.

    Правило пространственное и то же, что в исследовании: одна машина, один
    локальный день, пересекающиеся рамки маршрутов. `mission_uuid` в
    группировке НЕ участвует ни одним битом -- его семантика не доказана.

    `rows` -- словари с `unit_key`, `day`, `points`. Ключ машины идёт в
    `group_flights` под именем `nickname`: та функция группирует по паре
    (nickname, day) и кластеризует по рамкам, а как называется первый элемент
    пары, ей всё равно. Второй реализации кластеризации в проекте не заводится.
    """
    prepared = []
    for row in rows:
        item = dict(row)
        item['nickname'] = row.get('unit_key')
        prepared.append(item)

    groups = group_flights(prepared, margin_deg=margin_deg)

    # Индекс работы внутри (машина, день) -- устойчивый номер, по которому
    # результат находится при повторном пересчёте.
    counters = {}
    numbered = []
    for _basis, members in groups:
        head = members[0]
        key = (head.get('unit_key'), head.get('day'))
        index = counters.get(key, 0)
        counters[key] = index + 1
        numbered.append((head.get('unit_key'), head.get('day'), index,
                         members))
    return numbered
