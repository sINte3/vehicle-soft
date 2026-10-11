# -*- coding: utf-8 -*-
"""GPS: правило края поля -- A7 и правило на одних и тех же точках. Только чтение.

ЗАЧЕМ
Владелец 03.10.2026: езда на рабочей скорости у края поля -- въезды, выезды,
проезды вдоль линии разворотов -- не работа. Кандидат-правило (`gps/edge.py`,
за переключателем `edge_rule`) обрезает такую езду с участков действующего
метода; его условия приёмки записываются до прогона (дорожная карта, раздел
2.12). Этот инструмент и есть прогон. «A7» здесь -- действующий метод,
`gps.daily.compute_day(points, contours)` по умолчанию; «правило» --
`compute_day(points, contours, edge_rule=True)`. Оба -- на одних и тех же
точках. Доказательства правила (сердцевина, g, d, T, S, участки, возвращённые
охраной) `compute_day` не отдаёт, поэтому инструмент повторяет путь движка и
вызывает `gps.edge.trim` сам; каждые сутки повтор сверяется с `compute_day`
(`consistent`), и расхождение делает прогон недействительным.

Условия приёмки раздела 2.12 -- по частям инструмента: условия 1
(production) и 2 (KML) -- часть 4; условие 3 (New Holland 01.10) -- часть 2;
условие 4 (В-5) -- часть 1, только числа правила для CSV до вскрытия
замеров; условие 5 (набор 27.07) -- часть 3. Набор 12.08 и ответы
операторов -- отчёт без вердикта правилу (части 3 и 5). В сводке в конце
вывода у каждого вердикта стоят оба номера.

ЧАСТИ -- каждая работает, когда даны её ключи; в одном запуске их может быть
несколько, у каждой свой заголовок.
  1. --v5 PATH (с --db --dir) -- набор приёмки В-5
     (docs/gps_v5_acceptance_set_2026-10-09.csv). Машина строки ищется среди
     объектов с опубликованными сутками на её дату ключом поиска экрана
     (`gps_routes.search_key`); совпало несколько -- остаются те, у кого сумма
     участков в базе равна gps_ha_a7 с точностью 0,0005 га; ровно один --
     найден, иначе UNRESOLVED со списком кандидатов. По найденной: база, A7,
     правило, участки с доказательствами, контроль и строка для записи в CSV:
       V5EDGE n=<n> unit=<id> day=<day> a7=<x.xxx> edge=<x.xxx> control=<ok|MISMATCH|UNRESOLVED>
     Гектары здесь не судятся: их пишут в CSV до вскрытия замеров владельца.
     Не найдена по имени, но сумма участков равна gps_ha_a7 ровно у одного
     объекта дня -- его сутки всё равно считаются и печатаются под строкой
     «matched by hectares only -- confirm the machine», а в V5EDGE -- числа
     и control=UNRESOLVED: машину по имени подтверждает сессия.
     PASS -- все строки найдены и контроль сошёлся; RUN INVALID -- контроль
     не сошёлся (MISMATCH); NOT CHECKED -- строка не найдена (unit=-, или
     unit=<id> с числами при совпадении по гектарам), точек нет или строку
     базы записала другая версия метода (unit=<id>, control=UNRESOLVED,
     причина -- строкой выше).
  2. --day ГГГГ-ММ-ДД:ID (повторяется; с --db --dir) -- сутки целиком: участки
     A7 и правила с доказательствами, итог, контроль. Для 2026-10-01:1273
     (New Holland 7060 -- 80 080 HA; владелец обвёл оба поля руками, 6,364 +
     1,441 = 7,805 га, это крупнейший участок суток) -- условие: PASS, если
     площадь правила внутри крупнейшего участка A7 ближе к 7,805, чем у A7, и
     не меньше 7,805 x 0,9, иначе FAIL; и сверка с предсказанием 7,90 +- 0,05
     по KML владельца -- вне него OWNER CHECK, не FAIL (в KML координаты с
     шестью знаками). Прочие сутки печатаются без вердикта.
  3. --tracks PATH (повторяется) --zones PATH (без базы) -- наборы с ручными
     замерами 27.07 и 12.08 (`C:\\diag\\wialon`; набор 27.07 узнаётся по дате
     2026-07-27, 12.08 -- по своим 15 машино-суткам). По каждым машино-суткам
     -- `work_sites` A7 и правила; у 27.07 ещё по каждому контуру, куда
     машина заехала хотя бы 10 точками в движении, -- площадь участков A7 и
     правила внутри контура и ручной замер раздела 2.2 (правило
     сопоставления -- ниже). Условие 27.07: FAIL, если работа в зелёном
     допуске владельца под A7 (В-2: |A7 - ручной| не больше 10% или 0,3 га)
     уходит под правилом в недобор за допуск, или если медиана |расхождения|
     (в % ручного) у правила больше, чем у A7; иначе PASS; сопоставлено меньше
     10 строк из 17 -- NOT CHECKED. 12.08 -- только по суткам, OWNER CHECK
     (сверяет книга владельца). Набор не прочитан -- NOT CHECKED.
  4. --since ДАТА [--until ДАТА] (с --db --dir; --kml PATH по желанию) --
     production: опубликованные машино-сутки периода с точками на диске,
     только считаемые объекты (исключённые -- одним числом). Контроль -- как
     у прогона A7: строка версии METHOD_VERSION против пересчёта A7, строки
     иных версий -- «не проверено», числом по версиям. Инварианты, любое
     нарушение -- FAIL (ошибка реализации): гектары правила больше, чем у A7;
     участок правила выходит за участки A7 больше чем на 1 м2; участок A7 от
     0,3 га, внутри которого у правила осталось меньше 0,3 га (правило обещает
     участков не удалять). Отчёт без вердикта: сколько суток изменилось,
     план-факт A7 -> правило, обрезанные участки, участки без сердцевины,
     возвращённые охраной, распределение убранной доли по суткам, секунды на
     сутки. KML (глаз владельца, OWNER CHECK): 10 суток с наибольшей потерей
     и ещё 10 изменившихся -- первые по sha1 «ГГГГ-ММ-ДД:id»; у каждого
     изменённого участка A7 в имени -- было, стало, убрано и отметка «больше
     допуска В-2».
  5. --labels (с --db --dir; с --since/--until -- за период, без них -- за
     все даты) -- участки с ответом оператора «работа» / «проезд»: сутки
     пересчитываются, и внутри сохранённого многоугольника участка меряется
     площадь A7 и правила. Числа и гектары по ответам; каждая «работа», у
     которой правило срезало больше допуска В-2, -- OWNER CHECK с именем
     машины; доля гектаров «проезда», которую правило убирает. FAIL здесь нет.
     Период общий: --labels вместе с --since запускает и часть 4.

СОПОСТАВЛЕНИЕ РУЧНЫХ ЗАМЕРОВ 27.07 С КОНТУРАМИ
Имена таблицы 2.2 -- короткие формы имён контуров («1508 Нурхон» -- зона
«1508 Нурхон Бобохон», которая в справочнике дважды, 3207 и 3208). Оба имени
режутся по пробелам, каждое слово -- ключом поиска экрана («8696-2» ->
«86962»). Строка совпадает с контуром, куда машина заехала, если первое
слово контура РАВНО первому слову строки, а каждое следующее слово строки --
НАЧАЛО слова контура на том же месте («8696-2» не совпадает с «8696-21»).
Строка «3304+3303» -- сумма по одной машине контуров с первым словом «3304»
и «3303»; заехать машина должна в оба. Строка сопоставлена, если ей отвечает
ровно одна пара «машина, контур» (у строки с «+» -- ровно одна машина);
несколько или ни одной -- AMBIGUOUS / UNMATCHED с кандидатами, и в условие
строка не входит.

ВЕРДИКТЫ И КОД ВЫХОДА
  PASS / FAIL -- условие проверено; NOT CHECKED -- проверять было нечего или
  нечем; RUN INVALID -- контроль прогона не сошёлся, чинить инструмент и
  повторять; OWNER CHECK -- нужен глаз владельца. Код выхода: 0 -- всё
  машинное PASS, 4 -- есть FAIL, 3 -- FAIL нет, но есть невыполненная
  проверка или нужен владелец, 2 -- неверный ввод (тогда ничего не
  считалось), 1 -- инструмент упал (так Python завершает любое необработанное
  исключение; о правиле это ничего не говорит).

[REASON]: база и файлы точек открываются `mode=ro`; пишется только файл,
указанный ключом --kml. Решения здесь нет: включение правила в ночной расчёт
-- отдельный шаг после решения владельца. Одна оговорка про «ничего не
пишет»: SQLite, открывая базу в режиме WAL только на чтение, может создать
рядом пустые служебные файлы `-wal` и `-shm`; данные не меняются.

Запуск -- из окружения расчёта (нужна геометрия), PowerShell, из C:\\gps-tools:

    & C:\\gps_venv\\Scripts\\python.exe -u tools\\gps_edge_replay.py --db C:\\transport-report\\instance\\transport.db --dir C:\\transport-report\\instance --v5 docs\\gps_v5_acceptance_set_2026-10-09.csv --day 2026-10-01:1273

    & C:\\gps_venv\\Scripts\\python.exe -u tools\\gps_edge_replay.py --tracks C:\\diag\\wialon\\verify_tracks.csv --tracks C:\\diag\\wialon\\verify2_tracks.csv --zones C:\\diag\\wialon\\wialon_zones.json

    & C:\\gps_venv\\Scripts\\python.exe -u tools\\gps_edge_replay.py --db C:\\transport-report\\instance\\transport.db --dir C:\\transport-report\\instance --since 2026-09-01 --until 2026-09-30 --kml C:\\gps-tools\\check\\edge_replay.kml *> C:\\gps-tools\\check\\edge_replay_09.log

    & C:\\gps_venv\\Scripts\\python.exe -u tools\\gps_edge_replay.py --db C:\\transport-report\\instance\\transport.db --dir C:\\transport-report\\instance --labels

Месяц production -- часы (строка хода прогона каждые 200 машино-суток),
поэтому третья команда пишет вывод в файл: консоль Windows в режиме
выделения останавливает процесс на первом же выводе. Вывод -- только ASCII,
имена машин и контуров транслитерируются; кириллица -- только в KML.
"""

import argparse
import csv
import hashlib
import json
import os
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from xml.sax.saxutils import escape

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import numpy as np                                                  # noqa: E402
import shapely                                                      # noqa: E402
from shapely.errors import GEOSException                            # noqa: E402
from shapely.geometry import shape as shapely_shape                 # noqa: E402
from shapely.ops import unary_union                                 # noqa: E402

import gps.edge as edge                                             # noqa: E402
from gps.area import (METHOD_VERSION, MIN_WORK_AREA_HA,              # noqa: E402
                      _adaptive_alpha, _moving_mask, alpha_shape,
                      candidate_contours, densify, method_version,
                      repair_polygon, to_utm, work_sites)
from gps.daily import PASSAGE, WORK, compute_day, load_contours     # noqa: E402
from gps.tolerance import (TOLERANCE_GA_OK, VERDICT_OK,             # noqa: E402
                           verdict_for_ga)
from tools.gps_alpha_replay import (DAY_0727, FAIL,                 # noqa: E402
                                    MIN_MOVING_POINTS_IN_ZONE, NEW_STYLE,
                                    NOT_CHECKED, OWNER_CHECK, PASS,
                                    POINTS_0727, POINTS_1208, RUN_INVALID,
                                    TRACTORS_0727, WORK_DAYS_1208, BadInput,
                                    _day, load_csv_days, published_days,
                                    row_key, stored_sites, verdict_code)
from tools.gps_area_method_repro import load_zones                  # noqa: E402
from tools.gps_day_kml import (STYLES, _coordinates, _polygon_kml,  # noqa: E402
                               number, parse_pair, polygons_of,
                               track_pieces)
from tools.gps_implausible_sites import kinds_of, read_day_readonly  # noqa: E402
from tools.gps_track_only_days import (console, open_readonly,      # noqa: E402
                                       unit_names)

# --- 2026-10-01:1273 -------------------------------------------------------------
# [REASON]: New Holland 7060 -- 80 080 HA (объект 1273) за 01.10.2026 --
# единственные сутки, где владелец обвёл руками оба поля: 6,364 + 1,441 =
# 7,805 га (его KML, трек 02.10), а метод дал крупнейший участок 8,33 га --
# лишние 0,52 га и есть езда у края, ради которой правило заведено. Поля --
# это крупнейший участок A7 суток. 7,90 +- 0,05 -- предсказание правила по
# точкам того же KML (7,904 га, `gps/tests/test_edge.py`, замер 09.10 до
# прогона на данных владельца). В KML координаты с шестью знаками и нет
# времени, а здесь -- точки трекера, поэтому расхождение с предсказанием --
# вопрос к глазу владельца (OWNER CHECK), а не отказ правилу.
OWNER_DAY = ('2026-10-01', 1273)
OWNER_FIELDS_HA = (6.364, 1.441)
OWNER_HA = round(sum(OWNER_FIELDS_HA), 3)
OWNER_FLOOR_SHARE = 0.9
PREDICTED_HA = 7.90
PREDICTED_TOLERANCE_HA = 0.05

# --- набор В-5 ----------------------------------------------------------------
V5_COLUMNS = ('n', 'group', 'day', 'machine', 'org', 'application',
              'work_type', 'gps_ha_a7')
# [REASON]: gps_ha_a7 набора записан с тремя знаками по книге отчёта, а строка
# базы -- сумма участков с четырьмя: половина единицы третьего знака -- это
# округление, а не другая машина.
V5_TOTAL_TOLERANCE_HA = 0.0005

# [REASON]: ключ поиска -- копия `gps_routes.search_key`: машину набора В-5 и
# имя контура надо найти тем же ключом, что и экран «Факт по технике», а
# `gps_routes` -- модуль Flask, импортировать его нельзя. Тест сверяет три
# постоянные с исходником `gps_routes.py`, прочитанным как текст.
SEARCH_LOOKALIKE_FROM = 'авеёкмнорстух'
SEARCH_LOOKALIKE_TO = 'abeekmhopctyx'
SEARCH_KEEP = '0-9a-z\u0400-\u04ff'
_SEARCH_LOOKALIKE = str.maketrans(SEARCH_LOOKALIKE_FROM, SEARCH_LOOKALIKE_TO)
_SEARCH_DROP = re.compile('[^%s]' % SEARCH_KEEP)

# --- набор 27.07 ----------------------------------------------------------------
# [REASON]: семнадцать ручных замеров набора 27.07 -- таблица раздела 2.2
# дорожной карты (docs/GPS_PLAN_FAKT_VISION_ROADMAP.md), имена и гектары как
# в ней; тест сверяет постоянную с текстом документа. Имена -- короткие формы
# имён контуров, поэтому сопоставление -- по словам (см. `manual_matches`).
MANUAL_0727 = (
    ('1580 Маликова', 5.711), ('8696-2', 2.249), ('8693', 3.637),
    ('8833-3', 4.314), ('5634 Гарден', 2.085), ('5643 Гарден', 1.607),
    ('5650 Гарден', 0.602), ('5598 Гарден', 1.132), ('5637 Гарден', 5.400),
    ('4382 Бозоров', 2.036), ('3269 Маруф', 3.271), ('3309 Маруф', 2.645),
    ('3305 Маруф', 3.582), ('3304+3303', 2.936), ('3307 Маруф', 0.874),
    ('1357 Вержилиё', 2.418), ('1508 Нурхон', 8.587))
MIN_MATCHED_0727 = 10
MATCHED, AMBIGUOUS, UNMATCHED = 'matched', 'AMBIGUOUS', 'UNMATCHED'

# --- production -------------------------------------------------------------------
# [REASON]: допуск на сложение чисел с плавающей точкой у инварианта «не
# больше гектаров» (0,01 м2), и 1 м2 у «не выходит за участки A7» и у «участок
# изменён»: пересечение многоугольников из тысяч треугольников оставляет
# площадь порядка 1e-9 м2, а метр -- четвёртый знак гектара в строке базы.
MORE_HA_EPSILON = 1e-6
OUTSIDE_M2 = 1.0
SITE_CHANGED_HA = 1e-4
EPSILON = 1e-9
TOP_REMOVED = 10
SHA1_PICKS = 10
PROGRESS_EVERY = 200
SHARE_BINS = ('0', '<1%', '1-5%', '5-10%', '10-25%', '>25%')

# [REASON]: части инструмента нумерованы по заданию, условия -- по
# предрегистрации (дорожная карта, 2.12): В-5 там условие 4, production --
# 1 и 2, New Holland -- 3, набор 27.07 -- 5. В сводке стоят оба номера, чтобы
# вывод владельца читался прямо против текста условий.
LABEL_V5 = 'part 1, V-5 numbers (2.12 cond. 4)'
LABEL_OWNER = 'part 2, 1273 (2.12 cond. 3)'
LABEL_FORECAST = 'part 2, 1273 forecast (2.12 cond. 3)'
LABEL_0727 = 'part 3, 27.07 (2.12 cond. 5)'
LABEL_1208 = 'part 3, 12.08 (2.12 report)'
LABEL_INVARIANTS = 'part 4, invariants (2.12 cond. 1)'
LABEL_KML = 'part 4, KML (2.12 cond. 2)'
LABEL_LABELS = 'part 5, labels (2.12 report)'

KML_RULE = 'Правило края поля / дала чети қоидаси'
KML_B2 = 'больше допуска В-2 / В-2 йўл қўйилишидан ташқари'


def search_key(text):
    """Ключ поиска экрана: «80 156 СА» и «80156ca» дают одно и то же."""
    return _SEARCH_DROP.sub('', (text or '').lower().translate(_SEARCH_LOOKALIKE))


def beyond_b2(base_ha, fact_ha):
    """Расхождение больше зелёного допуска владельца (В-2, `gps.tolerance`)."""
    return verdict_for_ga(base_ha, fact_ha)[0] != VERDICT_OK


def _ha(geometry):
    return 0.0 if geometry is None or geometry.is_empty else geometry.area / 10000.0


def _share(part, whole):
    return 100.0 * part / whole if whole > EPSILON else 0.0


# --- одни сутки: A7, правило, доказательства ------------------------------------

def _put_back(xy, keep, today, alpha, pieces):
    """Какие участки A7 охрана `trim` вернула целиком -- шагами самого `trim`.

    [REASON]: `trim` сообщает только ЧИСЛО возвращённых участков, а владельцу
    нужен участок. Здесь повторены последние шаги `trim` его же функциями
    (`shape_of_kept`, `_polygonal`, `_pieces`): участок возвращён, если в
    обрезанной форме от него не осталось куска рабочего размера. Считается
    только на сутках, где `trim` что-то вернул; число сверяется с его числом.
    """
    rebuilt = edge.shape_of_kept(xy, keep, alpha)
    rest_of_day = (None if rebuilt is None
                   else edge._polygonal(rebuilt.intersection(today)))
    flags = []
    for piece in pieces:
        rest = (None if rest_of_day is None
                else edge._polygonal(rest_of_day.intersection(piece)))
        flags.append(not edge._pieces(rest, MIN_WORK_AREA_HA))
    return flags


def evidence(points):
    """Обе формы суток и доказательства правила -- путём `work_sites`.

    [REASON]: `gps.area.work_sites` вызывает `gps.edge.trim` и оставляет от
    него только форму (`shape, _ = trim(...)`), а `compute_day` пишет
    многоугольники, округлённые до шести знаков: доказательств правила там нет,
    а округление сдвигает вершины пересечения на сантиметры -- на длинной
    границе это больше 1 м2 инварианта. Поэтому здесь повторён путь движка:
    те же точки окна рабочей скорости в том же порядке, та же альфа, та же
    альфа-форма и тот же `trim` (через атрибут модуля, так что подменённый в
    тесте `trim` видят оба пути). Что повтор дал ровно участки `compute_day`,
    проверяет `consistent_with_engine` на каждых сутках.
    """
    track = sorted(points)
    xs, ys = to_utm([row[1] for row in track], [row[2] for row in track])
    mask = _moving_mask([row[3] for row in track])
    xy = [(float(x), float(y))
          for x, y in zip(np.asarray(xs)[mask], np.asarray(ys)[mask])]
    found = {'work_points': len(xy), 'alpha': None, 'spacing': None,
             'a7': [], 'rule': [], 'a7_union': None, 'rule_union': None,
             'sites': [], 'restored': 0, 'guard': True, 'violations': []}
    if len(xy) < 4:
        return found
    spacing, alpha = _adaptive_alpha(xy, True)
    found.update(alpha=alpha, spacing=spacing)
    today = alpha_shape(densify(xy), alpha)
    if today is None:
        return found
    trimmed, info = edge.trim(xy, today, alpha, MIN_WORK_AREA_HA)
    info = info if isinstance(info, dict) else {}
    pieces = edge._pieces(today, MIN_WORK_AREA_HA)
    kept = sorted(edge._pieces(trimmed, MIN_WORK_AREA_HA), key=lambda g: -g.area)
    # [REASON]: `analyse` перебирает `_pieces(today)` в том же порядке, что и
    # здесь, поэтому i-й участок A7 и i-я запись доказательств -- одно и то же.
    marks = info.get('sites') or []
    if len(marks) != len(pieces):
        marks = [None] * len(pieces)
    keep = info.get('keep')
    restored = int(info.get('restored') or 0)
    back = (_put_back(xy, keep, today, alpha, pieces)
            if restored and keep is not None else [False] * len(pieces))
    found['guard'] = sum(back) == restored
    a7_union = unary_union(pieces) if pieces else None
    rule_union = unary_union(kept) if kept else None
    # номера -- как у `compute_day`: от большего участка к меньшему
    order = sorted(range(len(pieces)), key=lambda i: -pieces[i].area)
    sites = []
    for number_, i in enumerate(order, 1):
        piece, mark = pieces[i], marks[i]
        area = piece.area / 10000.0
        inside = (_ha(piece.intersection(rule_union))
                  if rule_union is not None else 0.0)
        site = {'number': number_, 'area': area, 'inside': inside,
                'removed': area - inside, 'restored': back[i],
                'rule_pieces': sum(1 for g in kept
                                   if piece.intersection(g).area > OUTSIDE_M2),
                'core_points': None, 'points': None, 'kept': None,
                'g': None, 'd': None, 'T': None, 'S': None, 'turns': None}
        if mark is not None:
            site['core_points'] = int(mark.get('core_points', 0))
            site['points'] = len(mark['points'])
            if keep is not None:
                site['kept'] = int(np.asarray(keep)[mark['points']].sum())
            for key in ('g', 'd', 'T', 'S', 'turns'):
                site[key] = mark.get(key)
        sites.append(site)
    found.update(a7=pieces, rule=kept, a7_union=a7_union, rule_union=rule_union,
                 sites=sites, restored=restored,
                 violations=invariant_violations(pieces, kept))
    return found


def invariant_violations(a7_pieces, rule_pieces):
    """Три обещания правила на точной геометрии; пустой список -- все целы.

    Гектары не больше, чем у A7; ни один участок правила не выходит за участки
    A7 больше чем на OUTSIDE_M2; ни один участок A7 не теряет рабочего размера
    внутри участков правила (охрана `trim` обещает участков не удалять).
    """
    a7_union = unary_union(a7_pieces) if a7_pieces else None
    rule_union = unary_union(rule_pieces) if rule_pieces else None
    violations = []
    a7_ha = sum(g.area for g in a7_pieces) / 10000.0
    rule_ha = sum(g.area for g in rule_pieces) / 10000.0
    if rule_ha > a7_ha + MORE_HA_EPSILON:
        violations.append('more hectares than A7: %.4f > %.4f' % (rule_ha, a7_ha))
    for number_, piece in enumerate(sorted(rule_pieces, key=lambda g: -g.area), 1):
        outside = (piece.area if a7_union is None
                   else piece.difference(a7_union).area)
        if outside > OUTSIDE_M2:
            violations.append('rule site %d lies %.1f m2 outside the A7 sites'
                              % (number_, outside))
    for number_, piece in enumerate(sorted(a7_pieces, key=lambda g: -g.area), 1):
        area = piece.area / 10000.0
        inside = (_ha(piece.intersection(rule_union))
                  if rule_union is not None else 0.0)
        if area >= MIN_WORK_AREA_HA and inside < MIN_WORK_AREA_HA:
            violations.append('A7 site %d (%.4f ha) deleted: %.4f ha of it left in '
                              'the rule sites' % (number_, area, inside))
    return violations


def replay(points, contours):
    """A7 и правило на одних и тех же точках -- так, как их считает ночной
    расчёт, -- и доказательства правила."""
    began = time.perf_counter()
    a7 = compute_day(points, contours=contours)
    middle = time.perf_counter()
    rule = compute_day(points, contours=contours, edge_rule=True)
    ended = time.perf_counter()
    return {'a7': a7, 'rule': rule,
            'found': evidence(points) if a7.reason is None else None,
            'seconds_a7': middle - began, 'seconds_rule': ended - middle}


def consistent_with_engine(found, a7, rule):
    """Дал ли повтор ровно участки `compute_day` -- A7 и правила.

    Площади до четырёх знаков, альфа и шаг -- как в строках; и число
    участков, возвращённых охраной, -- как у самого `trim`.
    """
    if found is None:
        return rule.reason == a7.reason and not a7.sites and not rule.sites
    if a7.reason is not None or rule.reason is not None:
        return False

    def areas(pieces):
        return sorted(round(g.area / 10000.0, 4) for g in pieces)
    if areas(found['a7']) != sorted(row['area_ha'] for row in a7.sites):
        return False
    if areas(found['rule']) != sorted(row['area_ha'] for row in rule.sites):
        return False
    expected = (None if found['alpha'] is None else round(found['alpha'], 3),
                None if found['spacing'] is None else round(found['spacing'], 3))
    return (found['guard']
            and all((row['alpha_used_m'], row['pass_spacing_m']) == expected
                    for row in a7.sites + rule.sites))


def stored_day(con, day, unit):
    """Строка суток в базе: есть ли, причина, версия метода и участки."""
    row = con.execute('SELECT reason, method_version FROM gps_daily_aggregates '
                      'WHERE work_date = ? AND wialon_id = ?',
                      (day, unit)).fetchone()
    return {'has_row': row is not None,
            'reason': row[0] if row else None,
            'version': row[1] if row else None,
            'rows': stored_sites(con, day, unit)}


def control_of(stored, a7):
    """A7, пересчитанный по точкам, == строки базы (площадь, альфа, шаг).

    None -- не судится: строку записала другая версия метода (или строки
    нет). [REASON]: как у прогона A7 -- строку сверяют с пересчётом тем
    методом, который её записал; здесь пересчитан только действующий.
    """
    if stored['version'] != METHOD_VERSION:
        return None
    if a7.reason != stored['reason']:
        return False
    return row_key(a7.sites) == sorted(stored['rows'], key=lambda item: -item[0])


def judge(day, unit, played, stored=None):
    """Одни сутки числами: без геометрии, чтобы месяц уместился в памяти."""
    a7, rule, found = played['a7'], played['rule'], played['found']
    record = {
        'day': day, 'unit': unit, 'a7_reason': a7.reason,
        'a7_ha': sum(row['area_ha'] for row in a7.sites),
        'rule_ha': sum(row['area_ha'] for row in rule.sites),
        'a7_sites': len(a7.sites), 'rule_sites': len(rule.sites),
        'changed': (sorted(row['area_ha'] for row in a7.sites)
                    != sorted(row['area_ha'] for row in rule.sites)),
        'points': a7.aggregate['points_total'],
        'work_points': found['work_points'] if found else 0,
        'alpha': found['alpha'] if found else None,
        'spacing': found['spacing'] if found else None,
        'sites': found['sites'] if found else [],
        'restored': found['restored'] if found else 0,
        'violations': found['violations'] if found else [],
        'consistent': consistent_with_engine(found, a7, rule),
        'seconds_a7': played['seconds_a7'], 'seconds_rule': played['seconds_rule'],
        'has_row': False, 'version': None, 'stored_reason': None,
        'stored_ha': None, 'stored_sites': None, 'control': None}
    if stored is not None:
        record.update(has_row=stored['has_row'], version=stored['version'],
                      stored_reason=stored['reason'],
                      stored_ha=sum(item[0] for item in stored['rows']),
                      stored_sites=len(stored['rows']),
                      control=control_of(stored, a7))
    return record


# --- печать суток ----------------------------------------------------------------

def site_changed(site):
    return site['removed'] > SITE_CHANGED_HA


def site_line(site):
    """«site 1: 8.329 -> 7.904 ha, removed 0.425 ha (5.1%) [beyond B-2]».

    Та же строка, что в имени участка в KML: владелец сверяет одно с другим.
    """
    text = 'site %d: %.3f -> %.3f ha' % (site['number'], site['area'],
                                         site['inside'])
    if not site_changed(site):
        return text + ', unchanged'
    text += ', removed %.3f ha (%.1f%%)' % (site['removed'],
                                            _share(site['removed'], site['area']))
    if beyond_b2(site['area'], site['inside']):
        text += ' [beyond B-2]'
    return text


def evidence_text(site):
    if site['core_points'] is None:
        return 'evidence not available'
    parts = []
    if site['core_points'] == 0:
        parts.append('no core: left as it is')
    else:
        parts.append('core %d dense pts, g %.2f m, d %.2f m, T %.2f m, S %.2f m, '
                     'turns %d' % (site['core_points'], site['g'], site['d'],
                                   site['T'], site['S'], site['turns'] or 0))
    # [REASON]: правило может разрезать участок на части; сколько участков
    # правила лежит в участке A7 -- то, чего гектары одни не скажут.
    parts.append('in %d rule site(s)' % site['rule_pieces'])
    if site['kept'] is not None:
        parts.append('points kept %d of %d' % (site['kept'], site['points']))
    parts.append('put back by the guard: %s' % ('YES' if site['restored'] else 'no'))
    return '; '.join(parts)


def _control_word(record):
    if record['control'] is None:
        return ('not checked: %s' % ('no row in the database'
                                     if not record['has_row'] else
                                     'the row was written by %s, not %s'
                                     % (record['version'], METHOD_VERSION)))
    return 'ok' if record['control'] else 'MISMATCH'


def print_record(record, out, indent='  '):
    if record['has_row']:
        out('%sstored: %s, %d site(s), %.4f ha, version %s'
            % (indent, 'published' if record['stored_reason'] is None
               else 'reason %s' % record['stored_reason'],
               record['stored_sites'], record['stored_ha'], record['version']))
    else:
        out('%sstored: no row for this day' % indent)
    out('%spoints %d (work window %d), alpha %s m, spacing %s m'
        % (indent, record['points'], record['work_points'],
           '-' if record['alpha'] is None else '%.2f' % record['alpha'],
           '-' if record['spacing'] is None else '%.2f' % record['spacing']))
    if record['a7_reason']:
        out('%sA7 recomputed: reason %s, no sites' % (indent, record['a7_reason']))
    removed = record['a7_ha'] - record['rule_ha']
    out('%sA7 recomputed %.4f ha in %d site(s) -> rule %.4f ha in %d site(s): '
        'removed %.4f ha (%.2f%%)'
        % (indent, record['a7_ha'], record['a7_sites'], record['rule_ha'],
           record['rule_sites'], removed, _share(removed, record['a7_ha'])))
    out('%scontrol (A7 recomputed == stored rows: area, alpha, spacing): %s'
        % (indent, _control_word(record)))
    if not record['consistent']:
        out('%sREPLAY MISMATCH: the repetition of the engine path gave other sites '
            'than compute_day -- the evidence below is not the engine\'s' % indent)
    for site in record['sites']:
        out('%s%s; %s' % (indent, site_line(site), evidence_text(site)))
    out('%sput back by the guard: %d site(s)' % (indent, record['restored']))
    for violation in record['violations']:
        out('%sVIOLATION: %s' % (indent, violation))


# --- часть 1: набор В-5 ----------------------------------------------------------

def load_v5(path):
    """Строки набора В-5. Неверный файл -- BadInput (код 2), ничего не считалось."""
    with open(path, encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        absent = [name for name in V5_COLUMNS
                  if name not in (reader.fieldnames or [])]
        if absent:
            raise BadInput('%s has no column(s) %s'
                           % (console(os.path.basename(path)), ', '.join(absent)))
        rows = []
        for line, row in enumerate(reader, 2):
            try:
                item = {'n': int(row['n']), 'group': (row['group'] or '').strip(),
                        'day': datetime.strptime(row['day'].strip(), '%Y-%m-%d')
                        .strftime('%Y-%m-%d'),
                        'machine': (row['machine'] or '').strip(),
                        'gps_ha_a7': float(row['gps_ha_a7'])}
            except (AttributeError, KeyError, TypeError, ValueError):
                raise BadInput('%s line %d is not a V-5 row'
                               % (console(os.path.basename(path)), line))
            if not search_key(item['machine']):
                raise BadInput('%s line %d has no machine'
                               % (console(os.path.basename(path)), line))
            rows.append(item)
    numbers = Counter(item['n'] for item in rows)
    twice = sorted(n for n, count in numbers.items() if count > 1)
    if not rows or twice:
        raise BadInput('%s: %s' % (console(os.path.basename(path)),
                                   'no rows' if not rows else
                                   'n repeats: %s' % twice))
    return rows


def stored_total(con, day, unit):
    value = con.execute('SELECT COALESCE(SUM(area_ha), 0) FROM gps_work_polygons '
                        'WHERE work_date = ? AND wialon_id = ?',
                        (day, unit)).fetchone()[0]
    return float(value or 0.0)


def resolve_v5(con, row):
    """(выбранные, совпавшие по имени, суммы в базе, имена) для строки В-5."""
    units = [int(unit) for (unit,) in con.execute(
        'SELECT wialon_id FROM gps_daily_aggregates WHERE work_date = ? '
        'AND reason IS NULL ORDER BY wialon_id', (row['day'],))]
    names = unit_names(con, units)
    key = search_key(row['machine'])
    named = [unit for unit in units if search_key(names.get(unit)) == key]
    totals = {unit: stored_total(con, row['day'], unit) for unit in units}
    chosen = named
    if len(named) > 1:
        # [REASON]: одна машина -- несколько объектов Wialon (смена трекера:
        # «New Holland 80 605 EA» -- три объекта), и все носят одно имя.
        # Сутки набора выбраны по книге отчёта, где у каждой строки свои
        # гектары, -- по ним и различаются.
        chosen = [unit for unit in named
                  if abs(totals[unit] - row['gps_ha_a7']) <= V5_TOTAL_TOLERANCE_HA]
    return chosen, named, totals, names


def _replay_v5(con, folder, contours, row, unit, out):
    """Сутки строки В-5 у объекта `unit`: запись прогона или None (точек нет)."""
    points = read_day_readonly(folder, unit, row['day'])
    if not points:
        out('  points of this day are gone from disk: nothing to replay')
        return None
    record = judge(row['day'], unit, replay(points, contours),
                   stored_day(con, row['day'], unit))
    print_record(record, out, '  ')
    if abs(record['a7_ha'] - row['gps_ha_a7']) > V5_TOTAL_TOLERANCE_HA:
        out('  NOTE: A7 recomputed %.4f ha differs from the csv gps_ha_a7 %.3f'
            % (record['a7_ha'], row['gps_ha_a7']))
    return record


def _v5_line(row, unit, record, word):
    return ('V5EDGE n=%d unit=%s day=%s a7=%s edge=%s control=%s'
            % (row['n'], '-' if unit is None else unit, row['day'],
               '-' if record is None else '%.3f' % record['a7_ha'],
               '-' if record is None else '%.3f' % record['rule_ha'], word))


def part_v5(con, folder, contours, rows, out):
    out('=== PART 1 -- V-5 acceptance set, the rule\'s numbers for the csv '
        '(roadmap 2.12, condition 4): %d row(s) ===' % len(rows))
    words = []
    for row in rows:
        chosen, named, totals, names = resolve_v5(con, row)
        out('row %d (%s, %s, %s), csv gps_ha_a7 %.3f'
            % (row['n'], console(row['group']), row['day'], console(row['machine']),
               row['gps_ha_a7']))
        if len(chosen) != 1:
            by_hectares = [unit for unit in sorted(totals)
                           if abs(totals[unit] - row['gps_ha_a7'])
                           <= V5_TOTAL_TOLERANCE_HA]
            if named:
                out('  UNRESOLVED: %d unit(s) carry this name, %d of them with the '
                    'csv hectares' % (len(named), len(chosen)))
                shown = named
            else:
                out('  UNRESOLVED: no unit published on that day carries this name')
                # [REASON]: подсказка, а не выбор: объекты того же дня с теми же
                # гектарами или с похожим именем -- чтобы причину нашли за
                # один прогон, а не за переписку.
                key = search_key(row['machine'])
                shown = [unit for unit in sorted(totals)
                         if unit in by_hectares
                         or (search_key(names.get(unit))
                             and (key in search_key(names.get(unit))
                                  or search_key(names.get(unit)) in key))][:10]
            for unit in shown:
                out('    candidate unit %d stored %.4f ha  %s'
                    % (unit, totals[unit], console(names.get(unit) or '')))
            unit = record = None
            if len(by_hectares) == 1:
                # [REASON]: имя машины в книге отчёта и в справочнике пишут
                # по-разному, а второй прогон на сервере -- это день владельца.
                # Если гектары дня указывают ровно на один объект, его сутки
                # считаются сразу; строка остаётся UNRESOLVED (часть -- NOT
                # CHECKED), пока сессия не подтвердит машину по имени.
                unit = by_hectares[0]
                out('  matched by hectares only -- confirm the machine: unit %d %s'
                    % (unit, console(names.get(unit) or '')))
                record = _replay_v5(con, folder, contours, row, unit, out)
            out(_v5_line(row, unit, record, 'UNRESOLVED'))
            words.append('UNRESOLVED')
            continue
        unit = chosen[0]
        out('  unit %d %s' % (unit, console(names.get(unit) or '')))
        record = _replay_v5(con, folder, contours, row, unit, out)
        if record is None:
            word = 'UNRESOLVED'
        elif record['control'] is False or not record['consistent']:
            word = 'MISMATCH'
        elif record['control'] is None:
            word = 'UNRESOLVED'
        else:
            word = 'ok'
        words.append(word)
        out(_v5_line(row, unit, record, word))
    resolved = sum(1 for word in words if word != 'UNRESOLVED')
    if 'MISMATCH' in words:
        verdict, note = RUN_INVALID, ('the control failed on %d row(s): fix the '
                                      'replay and run it again'
                                      % words.count('MISMATCH'))
    elif 'UNRESOLVED' in words or not words:
        verdict, note = NOT_CHECKED, ('%d of %d row(s) could not be checked: '
                                      'see the UNRESOLVED lines'
                                      % (words.count('UNRESOLVED'), len(words)))
    else:
        verdict, note = PASS, 'all %d row(s) resolved, control ok' % resolved
    out('PART 1 (V-5 rows resolved and reproduced; hectares are not judged '
        'here): %s -- %s' % (verdict, note))
    return [(LABEL_V5, verdict)]


# --- часть 2: сутки по просьбе ----------------------------------------------------

def judge_owner(a7_site_ha, rule_inside_ha):
    """Условие 1273: (вердикт, пояснение)."""
    closer = abs(rule_inside_ha - OWNER_HA) < abs(a7_site_ha - OWNER_HA)
    floor = OWNER_HA * OWNER_FLOOR_SHARE
    verdict = PASS if closer and rule_inside_ha >= floor else FAIL
    note = ('rule %.4f ha inside the largest A7 site (A7 %.4f ha), owner %.3f ha: '
            '|rule - owner| %.4f %s |A7 - owner| %.4f; floor %.4f (%.0f%% of the '
            'owner) %s' % (rule_inside_ha, a7_site_ha, OWNER_HA,
                           abs(rule_inside_ha - OWNER_HA),
                           '<' if closer else 'NOT <', abs(a7_site_ha - OWNER_HA),
                           floor, 100 * OWNER_FLOOR_SHARE,
                           'held' if rule_inside_ha >= floor else 'BROKEN'))
    return verdict, note


def owner_condition(record, stored, out):
    title = 'CONDITION %s:%d (owner\'s hand measurement %.3f ha)' % (
        OWNER_DAY[0], OWNER_DAY[1], OWNER_HA)
    if record is None or not stored['has_row'] or stored['reason'] is not None \
            or not record['sites']:
        out('%s: %s -- the day is not published, has no A7 site or its points are '
            'gone' % (title, NOT_CHECKED))
        return [(LABEL_OWNER, NOT_CHECKED)]
    if record['control'] is False or not record['consistent']:
        # [REASON]: условие выбрано по суткам, которые владелец мерил; если
        # повтор не воспроизвёл базу или движок, пересчитаны другие сутки, и
        # судить по ним нельзя -- ни в плюс, ни в минус.
        out('%s: %s -- the replay does not reproduce this day' % (title, RUN_INVALID))
        return [(LABEL_OWNER, RUN_INVALID)]
    largest = record['sites'][0]
    verdict, note = judge_owner(largest['area'], largest['inside'])
    out('%s: %s -- %s' % (title, verdict, note))
    gap = round(abs(largest['inside'] - PREDICTED_HA), 6)
    repro = PASS if gap <= PREDICTED_TOLERANCE_HA else OWNER_CHECK
    out('REPRODUCTION %s:%d: %s -- rule %.4f ha inside the largest A7 site against '
        '%.2f +- %.2f ha predicted from the owner\'s KML%s'
        % (OWNER_DAY[0], OWNER_DAY[1], repro, largest['inside'], PREDICTED_HA,
           PREDICTED_TOLERANCE_HA,
           '' if repro == PASS else ' (outside: the KML has 6-decimal coordinates '
           'and no time -- the owner looks, this is not a FAIL)'))
    return [(LABEL_OWNER, verdict), (LABEL_FORECAST, repro)]


def part_days(con, folder, contours, pairs, out):
    out('=== PART 2 -- machine-days asked for (2026-10-01:1273 -- roadmap 2.12, '
        'condition 3): %d ===' % len(pairs))
    names = unit_names(con, sorted({unit for _day, unit in pairs}))
    verdicts = []
    for day, unit in pairs:
        out('--- %s unit %d %s' % (day, unit, console(names.get(unit) or '')))
        stored = stored_day(con, day, unit)
        points = read_day_readonly(folder, unit, day)
        record = None
        if not points:
            out('  no points on disk for this day')
        else:
            record = judge(day, unit, replay(points, contours), stored)
            print_record(record, out, '  ')
        if (day, unit) == OWNER_DAY:
            verdicts += owner_condition(record, stored, out)
    if OWNER_DAY not in pairs:
        out('(no verdict: the days above are printed for the eye; only %s:%d has '
            'a condition)' % OWNER_DAY)
    return verdicts


# --- часть 3: наборы с ручными замерами ---------------------------------------------

def tokens(name):
    """Слова имени ключом поиска; слово, от которого ключ ничего не оставил,
    выпадает (тире между словами)."""
    return [key for key in (search_key(word) for word in (name or '').split()) if key]


def name_fits(row_words, zone_words):
    """Первое слово равно, каждое следующее слово строки -- начало слова
    контура на том же месте."""
    if not row_words or len(zone_words) < len(row_words):
        return False
    return (zone_words[0] == row_words[0]
            and all(zone.startswith(row)
                    for row, zone in zip(row_words[1:], zone_words[1:])))


def manual_matches(manual, entered, zone_names):
    """Каждой ручной строке 27.07 -- её пары «машина, контур».

    `entered` -- пары наборa 27.07: dict unit, day, zone, points, a7, rule.
    Возвращает [dict name, manual, status, pairs, a7, rule].
    """
    words = {zone: tokens(zone_names.get(zone, '')) for zone in
             {pair['zone'] for pair in entered}}
    out = []
    for name, manual_ha in manual:
        parts = [tokens(part) for part in name.split('+')]
        if len(parts) > 1:
            by_unit = defaultdict(list)
            for pair in entered:
                if any(name_fits(part, words[pair['zone']]) for part in parts):
                    by_unit[(pair['unit'], pair['day'])].append(pair)
            # [REASON]: «3304+3303» -- одна работа на двух контурах; её
            # гектары -- сумма по одной машине, и заехать она должна в оба.
            full = {key: pairs for key, pairs in by_unit.items()
                    if all(any(name_fits(part, words[p['zone']]) for p in pairs)
                           for part in parts)}
            candidates = [p for pairs in by_unit.values() for p in pairs]
            matched = list(full.values())[0] if len(full) == 1 else None
            status = (MATCHED if matched else
                      AMBIGUOUS if len(full) > 1 else UNMATCHED)
        else:
            candidates = [pair for pair in entered
                          if name_fits(parts[0], words[pair['zone']])]
            matched = candidates if len(candidates) == 1 else None
            status = (MATCHED if matched else
                      AMBIGUOUS if len(candidates) > 1 else UNMATCHED)
        chosen = matched or []
        out.append({'name': name, 'manual': manual_ha, 'status': status,
                    'pairs': chosen if matched else candidates,
                    'a7': sum(p['a7'] for p in chosen),
                    'rule': sum(p['rule'] for p in chosen)})
    return out


def directory_fits(name, zone_words):
    """Контуры справочника, чьё имя подходит к строке, -- заезжала машина или нет."""
    parts = [tokens(part) for part in name.split('+')]
    return sorted(zone for zone, words in zone_words.items()
                  if any(name_fits(part, words) for part in parts))


def condition_0727(rows, out):
    """Условие 27.07 по сопоставленным строкам. Возвращает вердикт."""
    matched = [row for row in rows if row['status'] == MATCHED]
    fell = []
    for row in matched:
        # [REASON]: зелёный допуск -- тот же, что у светофора сверок
        # (`gps.tolerance`, ответ владельца на В-2), с тем же округлением на
        # границе.
        inside_a7 = not beyond_b2(row['manual'], row['a7'])
        under_rule = (row['rule'] < row['manual']
                      and beyond_b2(row['manual'], row['rule']))
        if inside_a7 and under_rule:
            fell.append(row)
    dev_a7 = [abs(row['a7'] - row['manual']) / row['manual'] * 100 for row in matched]
    dev_rule = [abs(row['rule'] - row['manual']) / row['manual'] * 100
                for row in matched]
    median_a7 = statistics.median(dev_a7) if dev_a7 else None
    median_rule = statistics.median(dev_rule) if dev_rule else None
    if matched:
        out('matched %d of %d rows; median |deviation| from the hand measurement: '
            'A7 %.2f%%, rule %.2f%%' % (len(matched), len(rows), median_a7,
                                         median_rule))
    for row in fell:
        out('  out of the green band under the rule: %s manual %.3f, A7 %.3f, rule '
            '%.3f' % (console(row['name']), row['manual'], row['a7'], row['rule']))
    if not matched or len(matched) < MIN_MATCHED_0727:
        # [REASON]: предрегистрация судит набор, а не отдельные работы: меньше
        # 10 сопоставленных строк из 17 -- условие не проверено, и найденный
        # выше выход из допуска печатается, но вердикта не меняет.
        out('CONDITION 27.07: %s -- %d of %d rows matched, at least %d are needed'
            % (NOT_CHECKED, len(matched), len(rows), MIN_MATCHED_0727))
        return NOT_CHECKED
    if fell:
        out('CONDITION 27.07: %s -- %d work(s) leave the green band under the rule'
            % (FAIL, len(fell)))
        return FAIL
    if median_rule > median_a7 + EPSILON:
        out('CONDITION 27.07: %s -- the median |deviation| grows: A7 %.2f%% -> rule '
            '%.2f%%' % (FAIL, median_a7, median_rule))
        return FAIL
    out('CONDITION 27.07: %s -- no work leaves the green band, median |deviation| '
        'A7 %.2f%% -> rule %.2f%%' % (PASS, median_a7, median_rule))
    return PASS


def compare_track(track, zones):
    """Машино-сутки набора: итоги A7 и правила и -- при `zones` -- контуры."""
    a7, _ = work_sites(track)
    rule, _ = work_sites(track, edge_rule=True)
    result = {'points': len(track), 'a7': sum(s.area_ha for s in a7),
              'rule': sum(s.area_ha for s in rule), 'contours': []}
    if zones:
        a7_union = unary_union([s.polygon for s in a7]) if a7 else None
        rule_union = unary_union([s.polygon for s in rule]) if rule else None
        for zone, inside in candidate_contours(
                track, zones, min_moving_points=MIN_MOVING_POINTS_IN_ZONE):
            result['contours'].append({
                'zone': zone, 'points': inside,
                'a7': _ha(a7_union.intersection(zones[zone])) if a7_union else 0.0,
                'rule': (_ha(rule_union.intersection(zones[zone]))
                         if rule_union else 0.0)})
    return result


def _set_dates(days):
    days = sorted(set(days))
    if len(days) <= 3:
        return ', '.join(days) or 'none'
    return '%d dates %s..%s' % (len(days), days[0], days[-1])


def part_sets(sets, zones, zone_names, out):
    """`sets` -- [(имя файла, {(unit, day): трек}, пропущено строк)]."""
    out('=== PART 3 -- hand-measured sets 27.07 (roadmap 2.12, condition 5) and '
        '12.08 (report) ===')
    work_days = {(unit, day): name for unit, name, day in WORK_DAYS_1208}
    early, late = [], {}
    for name, days, skipped in sets:
        out('%s: unreadable rows skipped %d, machine-days %d, points %d, dates %s'
            % (console(name), skipped, len(days),
               sum(len(track) for track in days.values()),
               _set_dates(day or '-' for _unit, day in days)))
        for (unit, day), track in sorted(days.items()):
            label = day or '-'
            if (unit, day) in work_days:
                result = compare_track(track, None)
                late[(unit, day)] = result
                continue
            is_early = label in (DAY_0727, '-')
            result = compare_track(track, zones if is_early else None)
            removed = result['a7'] - result['rule']
            out('  %s unit %-6d day %-10s points %5d: A7 %.3f -> rule %.3f ha '
                '(removed %.3f ha, %.1f%%)'
                % ('27.07' if is_early else 'other', unit, label, result['points'],
                   result['a7'], result['rule'], removed,
                   _share(removed, result['a7'])))
            if not is_early:
                continue
            for contour in result['contours']:
                pair = dict(contour, unit=unit, day=label)
                early.append(pair)
    verdicts = []
    out('')
    if not early:
        out('the 27.07 set (%d tractors, %d points, tracks of %s) was not read'
            % (TRACTORS_0727, POINTS_0727, DAY_0727))
        out('CONDITION 27.07: %s -- nothing to compare' % NOT_CHECKED)
        verdicts.append((LABEL_0727, NOT_CHECKED))
    else:
        rows = manual_matches(MANUAL_0727, early, zone_names)
        chosen = {(p['unit'], p['zone']): row['name'] for row in rows
                  if row['status'] == MATCHED for p in row['pairs']}
        out('27.07, contours entered (>= %d moving points), area inside the '
            'contour:' % MIN_MOVING_POINTS_IN_ZONE)
        for pair in early:
            row_name = chosen.get((pair['unit'], pair['zone']))
            manual = dict(MANUAL_0727).get(row_name) if row_name else None
            out('  unit %-6d zone %-6s pts %4d  A7 %.3f -> rule %.3f ha  manual %s  %s'
                % (pair['unit'], pair['zone'], pair['points'], pair['a7'],
                   pair['rule'], '-' if manual is None else '%.3f (%s)'
                   % (manual, console(row_name)),
                   console(zone_names.get(pair['zone'], ''))))
        out('27.07, the %d hand measurements of roadmap 2.2:' % len(MANUAL_0727))
        zone_words = {zone: tokens(name) for zone, name in zone_names.items()}
        for row in rows:
            head = '  %-16s %6.3f  %s' % (console(row['name']), row['manual'],
                                          row['status'])
            if row['status'] == MATCHED:
                out('%s: unit %d zone(s) %s -- A7 %.3f (%+.1f%%), rule %.3f (%+.1f%%)'
                    % (head, row['pairs'][0]['unit'],
                       ', '.join(str(p['zone']) for p in row['pairs']), row['a7'],
                       _share(row['a7'] - row['manual'], row['manual']), row['rule'],
                       _share(row['rule'] - row['manual'], row['manual'])))
                continue
            if row['pairs']:
                out('%s: %s' % (head, '; '.join(
                    'unit %d zone %s (%s) pts %d' % (
                        p['unit'], p['zone'], console(zone_names.get(p['zone'], '')),
                        p['points']) for p in row['pairs'])))
                continue
            # [REASON]: «не сопоставлена» бывает по двум разным причинам: машина
            # не заехала в контур этого имени (10 точек в движении) или такого
            # имени в справочнике нет вовсе -- и чинятся они по-разному.
            known = directory_fits(row['name'], zone_words)
            out('%s: no entered contour fits the name; %s' % (head, (
                'in the directory, not entered: %s' % ', '.join(
                    '%s (%s)' % (zone, console(zone_names.get(zone, '')))
                    for zone in known[:5]) if known
                else 'no zone of the directory fits it either')))
        verdicts.append((LABEL_0727, condition_0727(rows, out)))
    out('')
    absent = [(unit, name, day) for unit, name, day in WORK_DAYS_1208
              if (unit, day) not in late]
    for unit, name, day in WORK_DAYS_1208:
        result = late.get((unit, day))
        if result is None:
            continue
        removed = result['a7'] - result['rule']
        out('  12.08 unit %-6d day %s %-24s points %5d: A7 %.3f -> rule %.3f ha '
            '(removed %.3f ha, %.1f%%)'
            % (unit, day, console(name), result['points'], result['a7'],
               result['rule'], removed, _share(removed, result['a7'])))
    if absent:
        out('12.08 SET: %s -- %d of the %d works were not read: %s'
            % (NOT_CHECKED, len(absent), len(WORK_DAYS_1208),
               ', '.join('%d %s' % (unit, day) for unit, _name, day in absent)))
        verdicts.append((LABEL_1208, NOT_CHECKED))
    else:
        out('12.08 SET: %s -- all %d works read, %d points (recorded: %d); the '
            'owner compares the machine-days with his book'
            % (OWNER_CHECK, len(WORK_DAYS_1208),
               sum(result['points'] for result in late.values()), POINTS_1208))
        verdicts.append((LABEL_1208, OWNER_CHECK))
    return verdicts


# --- часть 4: production -----------------------------------------------------------

def share_bin(share):
    """Убранная доля суток (0..1) -> корзина отчёта."""
    if share <= 0:
        return '0'
    if share < 0.01:
        return '<1%'
    if share < 0.05:
        return '1-5%'
    if share < 0.10:
        return '5-10%'
    if share <= 0.25:
        return '10-25%'
    return '>25%'


def sha1_key(day, unit):
    return hashlib.sha1(('%s:%d' % (day, unit)).encode('utf-8')).hexdigest()


def pick_for_kml(records):
    """(10 суток с наибольшей потерей, ещё 10 изменившихся по sha1).

    [REASON]: случайная, но воспроизводимая выборка: sha1 строки
    «ГГГГ-ММ-ДД:id» не зависит ни от порядка обхода, ни от запуска, и любой
    может пересчитать её по списку суток.
    """
    changed = [row for row in records if row['changed']]
    largest = sorted(changed, key=lambda row: (-(row['a7_ha'] - row['rule_ha']),
                                               row['day'], row['unit']))[:TOP_REMOVED]
    taken = {(row['day'], row['unit']) for row in largest}
    rest = sorted((row for row in changed if (row['day'], row['unit']) not in taken),
                  key=lambda row: sha1_key(row['day'], row['unit']))[:SHA1_PICKS]
    return largest, rest


def report_period(records, total, excluded, gone, out):
    """Контроль, инварианты, отчёт. Возвращает вердикт инвариантов."""
    out('published machine-days of the period: %d; excluded objects: %d (counted '
        'as a number, not replayed); points gone from disk: %d; replayed: %d'
        % (total, excluded, gone, len(records)))
    checked = [row for row in records if row['control'] is not None]
    mismatched = [row for row in checked if not row['control']]
    out('control, A7 recomputed == stored rows (rows of %s only): %d of %d same, '
        '%d different, %d not checked (another method version)'
        % (METHOD_VERSION, len(checked) - len(mismatched), len(checked),
           len(mismatched), len(records) - len(checked)))
    versions = Counter(str(row['version']) for row in records)
    out('rows by method version, counted objects: %s'
        % (', '.join('%s %d' % pair for pair in sorted(versions.items())) or 'none'))
    for row in mismatched[:20]:
        out('  control mismatch %s %d' % (row['day'], row['unit']))
    inconsistent = [row for row in records if not row['consistent']]
    for row in inconsistent[:20]:
        out('  replay mismatch %s %d: the repetition of the engine path gave other '
            'sites than compute_day' % (row['day'], row['unit']))
    broken = [row for row in records if row['violations']]
    for row in broken[:20]:
        out('  VIOLATION %s %d: %s' % (row['day'], row['unit'],
                                       '; '.join(row['violations'])))
    # [REASON]: как у прогона A7: нарушение считается по одному повтору точек,
    # база в нём не участвует, поэтому несошедшийся контроль на других сутках
    # его не отменяет. Не доверяются только сутки, где повтор разошёлся с
    # движком.
    standing = [row for row in broken if row['consistent']]
    if not records:
        verdict, note = NOT_CHECKED, 'no machine-day was replayed'
    elif standing:
        verdict, note = FAIL, ('%d of %d machine-days break an invariant'
                               % (len(standing), len(records)))
    elif mismatched or inconsistent:
        verdict, note = RUN_INVALID, ('the control failed on %d machine-day(s), the '
                                      'replay on %d: fix the replay and run it '
                                      'again' % (len(mismatched), len(inconsistent)))
    else:
        verdict, note = PASS, 'no invariant broken on %d machine-days' % len(records)
    out('INVARIANTS (the rule never adds hectares, never leaves the A7 sites by more '
        'than %.0f m2, never deletes an A7 site): %s -- %s' % (OUTSIDE_M2, verdict,
                                                               note))
    out('report, no verdict:')
    changed = [row for row in records if row['changed']]
    out('  machine-days changed: %d of %d' % (len(changed), len(records)))
    a7_ha = sum(row['a7_ha'] for row in records)
    rule_ha = sum(row['rule_ha'] for row in records)
    out('  plan-fact of the period, counted objects: A7 %.2f ha -> rule %.2f ha '
        '(%+.2f ha, %+.2f%%)' % (a7_ha, rule_ha, rule_ha - a7_ha,
                                 -_share(a7_ha - rule_ha, a7_ha)))
    sites = [site for row in records for site in row['sites']]
    trimmed = [site for site in sites if site_changed(site)]
    coreless = [site for site in sites if site['core_points'] == 0]
    back = [site for site in sites if site['restored']]
    out('  A7 sites: %d; trimmed: %d (removed %.2f ha); without a core, left as '
        'they are: %d (%.2f ha); put back whole by the guard: %d (%.2f ha)'
        % (len(sites), len(trimmed), sum(site['removed'] for site in trimmed),
           len(coreless), sum(site['area'] for site in coreless), len(back),
           sum(site['area'] for site in back)))
    bins = Counter(share_bin((row['a7_ha'] - row['rule_ha']) / row['a7_ha'])
                   for row in records if row['a7_ha'] > EPSILON)
    out('  removed share per machine-day: %s; without an A7 site: %d'
        % (', '.join('%s %d' % (name, bins.get(name, 0)) for name in SHARE_BINS),
           sum(1 for row in records if row['a7_ha'] <= EPSILON)))
    if records:
        out('  seconds per machine-day: A7 median %.2f max %.2f; rule median %.2f '
            'max %.2f' % (statistics.median(row['seconds_a7'] for row in records),
                          max(row['seconds_a7'] for row in records),
                          statistics.median(row['seconds_rule'] for row in records),
                          max(row['seconds_rule'] for row in records)))
    return verdict


def site_label(site):
    """(имя, описание) участка A7 в KML; у изменённого -- было, стало, убрано."""
    area, inside = number(site['area'], 3), number(site['inside'], 3)
    if not site_changed(site):
        return ('Участок / участка %d: %s га, без изменений / ўзгаришсиз'
                % (site['number'], area),
                'A7 (действующий метод): %s га; правило края поля его не '
                'изменило. / A7 (амалдаги усул): %s га; дала чети қоидаси уни '
                'ўзгартирмади.' % (area, area))
    removed = number(site['removed'], 3)
    share = number(_share(site['removed'], site['area']), 1)
    beyond = beyond_b2(site['area'], site['inside'])
    band = '%d %% или %s га' % (round(100 * TOLERANCE_GA_OK[0]),
                                number(TOLERANCE_GA_OK[1], 1))
    band_uz = '%d %% ёки %s га' % (round(100 * TOLERANCE_GA_OK[0]),
                                   number(TOLERANCE_GA_OK[1], 1))
    name = ('Участок / участка %d: %s -> %s га, убрано / олиб ташланди %s га (%s %%)'
            % (site['number'], area, inside, removed, share))
    if beyond:
        name += ', ' + KML_B2
    description = (
        'A7 (действующий метод): %s га. Правило края поля внутри участка: %s га. '
        'Убрано %s га (%s %%)%s. / A7 (амалдаги усул): %s га. Участка ичида дала '
        'чети қоидаси: %s га. Олиб ташланди %s га (%s %%)%s.'
        % (area, inside, removed, share,
           '; больше допуска В-2 (%s)' % band if beyond else '',
           area, inside, removed, share,
           '; В-2 йўл қўйилишидан ташқари (%s)' % band_uz if beyond else ''))
    return name, description


def _shapes(text):
    shapes = [_polygon_kml(rings) for rings in polygons_of(text)]
    shapes = [shape for shape in shapes if shape]
    if not shapes:
        return ''
    return shapes[0] if len(shapes) == 1 else ('<MultiGeometry>%s</MultiGeometry>'
                                               % ''.join(shapes))


def kml_folder(record, a7_rows, rule_rows, points, name):
    day = datetime.strptime(record['day'], '%Y-%m-%d').strftime('%d.%m.%Y')
    removed = record['a7_ha'] - record['rule_ha']
    title = ('%s · %s · A7 %s га → правило / қоида %s га, убрано / олиб ташланди '
             '%s га (%s %%)' % (name or record['unit'], day,
                                number(record['a7_ha'], 3),
                                number(record['rule_ha'], 3), number(removed, 3),
                                number(_share(removed, record['a7_ha']), 1)))
    parts = ['<Folder><name>%s</name>' % escape(title)]
    for site, row in zip(record['sites'],
                         sorted(a7_rows, key=lambda r: r['site_number'])):
        geometry = _shapes(row['polygon_geojson'])
        if not geometry:
            continue
        label, description = site_label(site)
        parts.append('<Placemark><name>%s</name><description>%s</description>'
                     '<styleUrl>#site</styleUrl>%s</Placemark>'
                     % (escape(label), escape(description), geometry))
    for row in sorted(rule_rows, key=lambda r: r['site_number']):
        geometry = _shapes(row['polygon_geojson'])
        if geometry:
            parts.append('<Placemark><name>%s</name><styleUrl>#new</styleUrl>%s'
                         '</Placemark>'
                         % (escape('%s: участок / участка %d — %s га'
                                   % (KML_RULE, row['site_number'],
                                      number(row['area_ha'], 3))), geometry))
    pieces = track_pieces(points or [])
    for work, style, label in (
            (True, 'work', 'Трек в работе, 1–15 км/ч / иш тезлигидаги трек'),
            (False, 'move', 'Трек вне работы / ишдан ташқари трек')):
        lines = ''.join('<LineString><tessellate>1</tessellate><coordinates>%s'
                        '</coordinates></LineString>' % _coordinates(line)
                        for kind, line in pieces if kind is work)
        if lines:
            parts.append('<Placemark><name>%s</name><styleUrl>#%s</styleUrl>'
                         '<MultiGeometry>%s</MultiGeometry></Placemark>'
                         % (escape(label), style, lines))
    parts.append('</Folder>')
    return ''.join(parts)


def kml_document(folders):
    styles = ''.join('<Style id="%s">%s</Style>' % (key, body)
                     for key, body in tuple(STYLES) + (NEW_STYLE,))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
            '<name>%s</name>%s%s</Document></kml>\n'
            % (escape('GPS: правило края поля -- A7 и правило / дала чети '
                      'қоидаси -- A7 ва қоида'), styles, ''.join(folders)))


def part_period(con, folder, contours, since, until, kml_path, out, progress=None):
    out('=== PART 4 -- production %s .. %s (roadmap 2.12, conditions 1 and 2) ==='
        % (since, until or '(no end)'))
    days = published_days(con, since, until)
    kinds = kinds_of(con, sorted({unit for _day, unit, _version in days}))
    counted = [(day, unit) for day, unit, _version in days
               if kinds.get(unit) != 'excluded']
    records, gone = [], 0
    for index, (day, unit) in enumerate(counted, 1):
        points = read_day_readonly(folder, unit, day)
        if not points:
            gone += 1
        else:
            records.append(judge(day, unit, replay(points, contours),
                                 stored_day(con, day, unit)))
        if progress and index % PROGRESS_EVERY == 0:
            progress('  %d of %d machine-days' % (index, len(counted)))
    verdict = report_period(records, len(days), len(days) - len(counted), gone, out)
    largest, rest = pick_for_kml(records)
    names = unit_names(con, sorted({row['unit'] for row in largest + rest}))
    out('KML for the owner\'s eye -- %d machine-day(s) with the largest removal and '
        '%d more changed ones by sha1 of "day:unit":' % (len(largest), len(rest)))
    for title, picked in (('largest removal', largest), ('by sha1', rest)):
        for row in picked:
            removed = row['a7_ha'] - row['rule_ha']
            out('  [%s] %s unit %d: A7 %.3f -> rule %.3f ha, removed %.3f ha (%.1f%%)'
                '  %s' % (title, row['day'], row['unit'], row['a7_ha'],
                          row['rule_ha'], removed, _share(removed, row['a7_ha']),
                          console(names.get(row['unit']) or '')))
            for site in row['sites']:
                if site_changed(site):
                    out('    %s' % site_line(site))
    verdicts = [(LABEL_INVARIANTS, verdict)]
    if largest or rest:
        verdicts.append((LABEL_KML, OWNER_CHECK))
    if kml_path:
        folders = []
        for row in largest + rest:
            points = read_day_readonly(folder, row['unit'], row['day'])
            played = replay(points, contours)
            folders.append(kml_folder(row, played['a7'].sites, played['rule'].sites,
                                      points, names.get(row['unit'])))
        with open(kml_path, 'w', encoding='utf-8') as handle:
            handle.write(kml_document(folders))
        out('KML written (%d machine-days): %s' % (len(folders), console(kml_path)))
    return verdicts


# --- часть 5: ответы операторов ------------------------------------------------------

def utm_of_geojson(text):
    """Сохранённый многоугольник участка (GeoJSON, WGS84) в UTM 41N.

    [REASON]: `gps.daily._utm_polygon_from_geojson` берёт только внешнее кольцо
    одного Polygon: для переноса ответа по перекрытию этого хватает, а здесь
    участок МЕРЯЕТСЯ -- участок с дырой вышел бы больше, а из двух частей не
    читался бы вовсе. Поэтому кольца и части сохраняются все.
    """
    try:
        geometry = shapely_shape(json.loads(text or ''))
    except (ValueError, TypeError, AttributeError, KeyError, IndexError,
            GEOSException):
        return None
    if geometry.is_empty or geometry.geom_type not in ('Polygon', 'MultiPolygon'):
        return None
    projected = shapely.transform(
        geometry, lambda xy: np.column_stack(to_utm(xy[:, 0], xy[:, 1])))
    return repair_polygon(projected)


def labelled_sites(con, since=None, until=None):
    query = ('SELECT p.work_date, p.wialon_id, p.site_number, p.polygon_geojson, '
             'p.operator_label, a.wialon_id IS NOT NULL, a.reason '
             'FROM gps_work_polygons p LEFT JOIN gps_daily_aggregates a '
             'ON a.work_date = p.work_date AND a.wialon_id = p.wialon_id '
             'WHERE p.operator_label IS NOT NULL')
    args = []
    if since is not None:
        query += ' AND p.work_date >= ?'
        args.append(since)
    if until is not None:
        query += ' AND p.work_date <= ?'
        args.append(until)
    return [{'day': str(day), 'unit': int(unit), 'site': site, 'geojson': text,
             'label': label, 'has_row': bool(has_row), 'reason': reason}
            for day, unit, site, text, label, has_row, reason in con.execute(
                query + ' ORDER BY p.work_date, p.wialon_id, p.site_number', args)]


def part_labels(con, folder, contours, since, until, out, progress=None):
    out('=== PART 5 -- operator answers %s and %s, %s (roadmap 2.12, report) ===' % (
        console(WORK), console(PASSAGE),
        'all dates' if since is None and until is None
        else '%s .. %s' % (since or '(no start)', until or '(no end)')))
    sites = labelled_sites(con, since, until)
    groups = defaultdict(list)
    for site in sites:
        groups[(site['day'], site['unit'])].append(site)
    names = unit_names(con, sorted({unit for _day, unit in groups}))
    measured, untrusted = [], []
    skipped = Counter()
    for index, ((day, unit), mine) in enumerate(sorted(groups.items()), 1):
        if progress and index % PROGRESS_EVERY == 0:
            progress('  %d of %d labelled machine-days' % (index, len(groups)))
        if not mine[0]['has_row'] or mine[0]['reason'] is not None:
            skipped['the day is not published'] += len(mine)
            continue
        points = read_day_readonly(folder, unit, day)
        if not points:
            skipped['points gone from disk'] += len(mine)
            continue
        played = replay(points, contours)
        record = judge(day, unit, played, stored_day(con, day, unit))
        if record['control'] is False or not record['consistent']:
            untrusted.append((day, unit))
        found = played['found'] or {}
        a7_union, rule_union = found.get('a7_union'), found.get('rule_union')
        for site in mine:
            if site['label'] not in (WORK, PASSAGE):
                skipped['another answer'] += 1
                continue
            polygon = utm_of_geojson(site['geojson'])
            if polygon is None:
                skipped['polygon not readable'] += 1
                continue
            a7_in = _ha(polygon.intersection(a7_union)) if a7_union else 0.0
            rule_in = _ha(polygon.intersection(rule_union)) if rule_union else 0.0
            measured.append(dict(site, a7=a7_in, rule=rule_in,
                                 control=_control_word(record)))
    out('labelled sites: %d in %d machine-day(s); compared %d; not compared: %s'
        % (len(sites), len(groups), len(measured),
           ', '.join('%s %d' % pair for pair in sorted(skipped.items())) or 'none'))
    for day, unit in untrusted:
        out('  replay does not reproduce %s %d (control or engine mismatch)'
            % (day, unit))
    for label in (WORK, PASSAGE):
        mine = [site for site in measured if site['label'] == label]
        a7_ha = sum(site['a7'] for site in mine)
        rule_ha = sum(site['rule'] for site in mine)
        out('  %-8s sites %d: A7 %.3f ha -> rule %.3f ha inside the labelled '
            'polygons, removed %.3f ha (%.1f%%)'
            % (console(label), len(mine), a7_ha, rule_ha, a7_ha - rule_ha,
               _share(a7_ha - rule_ha, a7_ha)))
    cut = [site for site in measured if site['label'] == WORK
           and site['rule'] < site['a7'] and beyond_b2(site['a7'], site['rule'])]
    for site in cut:
        out('  %s cut beyond B-2: %s unit %d site %s: A7 %.3f -> rule %.3f ha, '
            'removed %.3f ha (%.1f%%)  %s'
            % (console(WORK), site['day'], site['unit'], site['site'], site['a7'],
               site['rule'], site['a7'] - site['rule'],
               _share(site['a7'] - site['rule'], site['a7']),
               console(names.get(site['unit']) or '')))
    passage = [site for site in measured if site['label'] == PASSAGE]
    passage_a7 = sum(site['a7'] for site in passage)
    out('  share of %s hectares the rule removes: %.1f%%'
        % (console(PASSAGE),
           _share(passage_a7 - sum(site['rule'] for site in passage), passage_a7)))
    if not measured:
        verdict, note = NOT_CHECKED, 'no labelled site could be compared'
    elif untrusted:
        verdict, note = RUN_INVALID, ('the replay does not reproduce %d labelled '
                                      'machine-day(s)' % len(untrusted))
    elif cut:
        verdict, note = OWNER_CHECK, ('%d %s site(s) cut beyond B-2: the owner looks'
                                      % (len(cut), console(WORK)))
    else:
        verdict, note = PASS, 'no %s site is cut beyond B-2' % console(WORK)
    out('PART 5 (labels, no FAIL here): %s -- %s' % (verdict, note))
    return [(LABEL_LABELS, verdict)]


# --- запуск ------------------------------------------------------------------------

def _refuse(message):
    sys.stderr.write('ERROR: %s\n' % message)
    return 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=console(__doc__.splitlines()[0]))
    parser.add_argument('--v5', help='part 1: the V-5 acceptance set csv')
    parser.add_argument('--day', action='append', default=[],
                        help='part 2: YYYY-MM-DD:WIALON_ID, may repeat')
    parser.add_argument('--tracks', action='append', default=[],
                        help='part 3: hand-measured set tracks csv, may repeat')
    parser.add_argument('--zones', help='part 3: wialon_zones.json')
    parser.add_argument('--since', help='part 4: first date, YYYY-MM-DD')
    parser.add_argument('--until', help='part 4 (and 5): last date, YYYY-MM-DD')
    parser.add_argument('--kml', help='part 4: KML file for the owner')
    parser.add_argument('--labels', action='store_true',
                        help='part 5: operator answers; the period options '
                             'restrict it')
    parser.add_argument('--db', help='path to transport.db (parts 1, 2, 4, 5)')
    parser.add_argument('--dir', help='folder with gps_points_YYYYMM.db files')
    args = parser.parse_args(argv)

    wants_db = bool(args.v5 or args.day or args.since or args.labels)
    if not (wants_db or args.tracks or args.zones):
        return _refuse('nothing to do: give --v5, --day, --tracks/--zones, '
                       '--since or --labels')
    if bool(args.tracks) != bool(args.zones):
        return _refuse('--tracks and --zones go together')
    missing = [path for path in args.tracks + ([args.zones] if args.zones else [])
               + ([args.v5] if args.v5 else []) if not os.path.isfile(path)]
    if missing:
        return _refuse('not found: %s' % ', '.join(console(m) for m in missing))
    since = until = None
    if args.since is not None:
        since = _day('--since', args.since)
        if since is None:
            return 2
    if args.until is not None:
        until = _day('--until', args.until)
        if until is None:
            return 2
        if since is None and not args.labels:
            return _refuse('--until needs --since (or --labels)')
    if since and until and until < since:
        return _refuse('--until is before --since')
    if args.kml and not since:
        return _refuse('--kml goes with --since (part 4)')
    if args.kml and not os.path.isdir(os.path.dirname(os.path.abspath(args.kml))):
        return _refuse('the folder for --kml does not exist')
    pairs = []
    for text in args.day:
        pair = parse_pair(text)
        if pair is None:
            return _refuse('--day must look like YYYY-MM-DD:WIALON_ID')
        if pair not in pairs:
            pairs.append(pair)
    if wants_db and (not args.db or not os.path.isfile(args.db)
                     or not args.dir or not os.path.isdir(args.dir)):
        return _refuse('parts 1, 2, 4 and 5 need --db (an existing database) and '
                       '--dir (an existing points folder)')
    # [REASON]: всё, что читается из файлов, читается ДО первого расчёта:
    # неверный ввод -- код 2 и ничего не посчитано, а не обрыв через час.
    try:
        v5_rows = load_v5(args.v5) if args.v5 else None
        sets = []
        for path in args.tracks:
            days, skipped = load_csv_days(path)
            sets.append((os.path.basename(path), days, skipped))
    except BadInput as error:
        return _refuse(error)
    zones = zone_names = None
    if args.zones:
        try:
            loaded = load_zones(args.zones)
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            return _refuse('%s is not a zones file: %s'
                           % (console(os.path.basename(args.zones)),
                              type(error).__name__))
        zones = {zone: polygon for zone, (_name, polygon) in loaded.items()}
        zone_names = {zone: name for zone, (name, _polygon) in loaded.items()}

    def progress(line):
        print(line, flush=True)

    print('A7: compute_day, method %s; rule: compute_day(edge_rule=True), method %s'
          % (method_version(True), method_version(True, edge_rule=True)))
    verdicts = []
    con = open_readonly(args.db) if wants_db else None
    try:
        contours = (load_contours(con) or None) if con is not None else None
        if v5_rows is not None:
            verdicts += part_v5(con, args.dir, contours, v5_rows, print)
        if pairs:
            verdicts += part_days(con, args.dir, contours, pairs, print)
        if sets:
            verdicts += part_sets(sets, zones, zone_names, print)
        if since:
            verdicts += part_period(con, args.dir, contours, since, until, args.kml,
                                    print, progress)
        if args.labels:
            verdicts += part_labels(con, args.dir, contours, since, until, print,
                                    progress)
    finally:
        if con is not None:
            con.close()
    code = verdict_code([verdict for _name, verdict in verdicts])
    print('=== SUMMARY ===')
    for name, verdict in verdicts:
        print('%-38s %s' % (name, verdict))
    print('exit code %d (0 -- all machine PASS, 4 -- a FAIL, 3 -- no FAIL but '
          'something not checked or for the owner)' % code)
    print('nothing was written to the database or the point files: they were '
          'opened mode=ro%s' % ('; the only file written is the KML: %s'
                                % console(args.kml) if args.kml else ''))
    return code


if __name__ == '__main__':
    sys.exit(main())
