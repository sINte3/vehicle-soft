# -*- coding: utf-8 -*-
"""GPS: правило края поля, второй круг -- A7 и правило на одних точках. Только чтение.

ЗАЧЕМ
Владелец 03.10.2026: езда у края поля на рабочей скорости -- въезды, выезды,
проезды вдоль края, дороги к полю -- не работа, «не считать». Первое правило
(`gps/edge.py`, раздел 2.12 дорожной карты) отвергнуто прогоном 10-11.10: на
опрыскивании оно срезало саму работу. Решение владельца 11.10 -- второй круг:
новый стенд, новые кандидаты и судьи, новая предрегистрация (раздел 2.13) и
приёмка на новых ручных замерах -- наборе В-6. Выбранное правило --
`gps/edge_scale.py`, за переключателем `edge_rule=EDGE_SCALE_VERSION`. Этот
инструмент -- прогон, по которому его принимают или отвергают.

«A7» здесь -- действующий метод, `gps.daily.compute_day(points, contours)`;
«правило» -- `compute_day(points, contours, edge_rule=EDGE_SCALE_VERSION)`.
Оба -- на одних и тех же точках. Доказательств правила (шаг участка, что
убрано и почему, что оставлено как проходы) `compute_day` не отдаёт, поэтому
инструмент повторяет путь движка и вызывает `gps.edge_scale.trim` сам; каждые
сутки повтор сверяется с `compute_day` (`consistent`), и расхождение делает
прогон недействительным.

Условия раздела 2.13 -- по частям инструмента: условие 3 (набор В-6) --
часть 1, только числа правила для CSV до вскрытия замеров; условия 1
(production, инварианты) и 2 (KML, глаз владельца) -- часть 3. New Holland
01.10 (часть 2) и ответы операторов (часть 4) были данными выбора правила:
по ним -- отчёт и проверка воспроизведения, а не приёмка. В сводке в конце
вывода у каждого вердикта стоят оба номера.

ЧАСТИ -- каждая работает, когда даны её ключи; в одном запуске их может быть
несколько, у каждой свой заголовок.
  1. --v6 PATH (с --db --dir) -- набор приёмки В-6
     (docs/gps_v6_acceptance_set_2026-10-11.csv). Машина строки ищется так
     же, как в первом круге (`tools/gps_edge_replay.resolve_v5`): ключом
     поиска экрана среди объектов с опубликованными сутками на её дату;
     несколько объектов одного имени различает сумма участков в базе
     (gps_ha_a7 с точностью 0,0005 га). По найденной: сутки целиком --
     участки A7 и правила с доказательствами, контроль -- и строка для CSV:
       V6EDGE n=<n> unit=<id> day=<day> a7=<x.xxx> rule=<x.xxx> control=<ok|MISMATCH|FALLBACK|UNRESOLVED>
     Гектары здесь не судятся: их пишут в CSV до вскрытия замеров владельца.
     PASS -- все строки найдены, контроль сошёлся, правило отработало; RUN
     INVALID -- контроль или повтор не сошёлся (MISMATCH); NOT CHECKED --
     строка не найдена (не нашлась по имени -- сутки единственного объекта
     дня с гектарами строки считаются, но строка остаётся UNRESOLVED, пока
     сессия не подтвердит машину), точек нет, строку базы записала другая
     версия метода (UNRESOLVED) или правило упало и сутки остались формой A7
     (FALLBACK).
  2. --day ГГГГ-ММ-ДД:ID (повторяется; с --db --dir) -- сутки целиком:
     участки A7 и правила с доказательствами, итог, контроль. Для
     2026-10-01:1273 (New Holland 7060 -- 80 080 HA; владелец обвёл оба поля
     руками, 6,364 + 1,441 = 7,805 га, они внутри крупнейшего участка суток)
     -- отчёт: площадь правила внутри крупнейшего участка против ручных; и
     воспроизведение стенда: 8,2265 +- 0,001 га -- PASS, иначе OWNER CHECK
     (сервер считает не так, как стенд на тех же точках, -- разбирает
     сессия). Прочие сутки печатаются без вердикта.
  3. --since ДАТА [--until ДАТА] (с --db --dir; --kml PATH по желанию) --
     production: опубликованные машино-сутки периода с точками на диске,
     только считаемые объекты (исключённые -- одним числом). Контроль -- как
     у первого круга: строка версии METHOD_VERSION против пересчёта A7,
     строки иных версий -- «не проверено», числом по версиям. Инварианты
     (условие 1), любое нарушение -- FAIL (ошибка реализации):
       - гектары правила больше, чем у A7;
       - участок правила выходит за участки A7 больше чем на 1 м2;
       - форма правила -- не плоские многоугольники (движок считал бы
         вложенную коллекцию одним участком);
       - участок A7, на котором правило выключено (шаг задаёт альфу, работа
         широкими проходами, шаг не измерить), изменился больше чем на 1 м2.
     Правило упало хоть на одних сутках (исключение; сутки остались формой
     A7) -- NOT CHECKED: там правило не проверено, и это чинится до решения.
     Отчёт без вердикта: сколько суток изменилось; план-факт A7 -> правило;
     на скольких участках правило действует и по какой причине выключено;
     убрано -- пустыми промежутками, узкими полосами, остатком ниже 0,3 га;
     участки, удалённые целиком; узкие полосы, оставленные как проходы;
     распределение убранной доли по суткам; секунды на сутки. KML (условие
     2, глаз владельца, OWNER CHECK): 10 суток с наибольшей потерей и ещё
     10 изменившихся -- первые по sha1 «ГГГГ-ММ-ДД:id»; в каждых -- участки
     A7 и правила, убранные куски с причиной, полосы, оставленные как
     проходы, и трек.
  4. --labels (с --db --dir; с --since/--until -- за период, без них -- за
     все даты) -- участки с ответом оператора «работа» / «проезд»: сутки
     пересчитываются, и внутри сохранённого многоугольника участка меряется
     площадь A7 и правила. Сколько гектаров «проезда» правило убирает и
     сколько «работы» оставляет; каждая «работа», срезанная больше допуска
     В-2, -- OWNER CHECK с именем машины. FAIL здесь нет. Период общий:
     --labels вместе с --since запускает и часть 3.

ВЕРДИКТЫ И КОД ВЫХОДА -- как у первого круга
  PASS / FAIL -- условие проверено; NOT CHECKED -- проверять было нечего или
  нечем; RUN INVALID -- контроль прогона не сошёлся, чинить инструмент и
  повторять; OWNER CHECK -- нужен глаз владельца. Код выхода: 0 -- всё
  машинное PASS, 4 -- есть FAIL, 3 -- FAIL нет, но есть невыполненная
  проверка или нужен владелец, 2 -- неверный ввод (тогда ничего не
  считалось), 1 -- инструмент упал (так Python завершает любое необработанное
  исключение; о правиле это ничего не говорит).

[REASON]: база и файлы точек открываются `mode=ro`; пишется только файл,
указанный ключом --kml. Решения здесь нет: включение правила в ночной расчёт
-- отдельный шаг после решения владельца. Оговорка та же, что у первого
круга: SQLite, открывая базу в режиме WAL только на чтение, может создать
рядом пустые служебные файлы `-wal` и `-shm`; данные не меняются.

Запуск -- из окружения расчёта (нужна геометрия), PowerShell, из C:\\gps-tools:

    & C:\\gps_venv\\Scripts\\python.exe -u tools\\gps_edge_scale_replay.py --db C:\\transport-report\\instance\\transport.db --dir C:\\transport-report\\instance --v6 docs\\gps_v6_acceptance_set_2026-10-11.csv --day 2026-10-01:1273 *> C:\\gps-tools\\check\\edge_scale_v6.log

    & C:\\gps_venv\\Scripts\\python.exe -u tools\\gps_edge_scale_replay.py --db C:\\transport-report\\instance\\transport.db --dir C:\\transport-report\\instance --since 2026-09-01 --until 2026-10-10 --kml C:\\gps-tools\\check\\edge_scale.kml --labels *> C:\\gps-tools\\check\\edge_scale_period.log

Период production -- часы (строка хода прогона каждые 200 машино-суток),
поэтому вывод пишется в файл: консоль Windows в режиме выделения
останавливает процесс на первом же выводе. Вывод -- только ASCII, имена
машин транслитерируются; кириллица -- только в KML.
"""

import argparse
import os
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
from shapely.ops import unary_union                                 # noqa: E402

import gps.edge_scale as edge_scale                                 # noqa: E402
from gps.area import (EDGE_SCALE_VERSION, METHOD_VERSION,            # noqa: E402
                      MIN_WORK_AREA_HA, _adaptive_alpha, _moving_mask,
                      alpha_shape, densify, method_version, to_utm)
from gps.daily import (PASSAGE, WORK, compute_day, load_contours,   # noqa: E402
                       polygon_geojson)
from gps.tolerance import TOLERANCE_GA_OK                           # noqa: E402
from tools.gps_alpha_replay import (FAIL, NEW_STYLE, NOT_CHECKED,   # noqa: E402
                                    OWNER_CHECK, PASS, RUN_INVALID, BadInput,
                                    _day, published_days, verdict_code)
from tools.gps_day_kml import (STYLES, _coordinates, number,        # noqa: E402
                               parse_pair, track_pieces)
from tools.gps_edge_replay import (EPSILON, MORE_HA_EPSILON,        # noqa: E402
                                   OUTSIDE_M2, OWNER_DAY, OWNER_HA,
                                   PROGRESS_EVERY, SHARE_BINS, SITE_CHANGED_HA,
                                   V5_TOTAL_TOLERANCE_HA, _control_word, _ha,
                                   _shapes, _share, beyond_b2, control_of,
                                   labelled_sites, load_v5, pick_for_kml,
                                   resolve_v5, share_bin, stored_day,
                                   utm_of_geojson)
from tools.gps_implausible_sites import kinds_of, read_day_readonly  # noqa: E402
from tools.gps_track_only_days import (console, open_readonly,      # noqa: E402
                                       unit_names)

RULE = EDGE_SCALE_VERSION

# --- New Holland 01.10 (данные выбора) --------------------------------------------
# [REASON]: сутки 2026-10-01:1273 -- часть стенда второго круга: их точки
# выгружены с сервера 10-11.10 (`tools/gps_bench_export.py`), и на них
# правило дало внутри крупнейшего участка A7 (8,3287 га) 8,2265 га (стенд:
# 8,226541; ручные владельца -- 7,805). Сервер на тех же точках тем же кодом
# обязан дать то же число: это проверка переноса, а не точности. Допуск --
# единица третьего знака: строка базы пишется с четырьмя, стенд считал без
# округления.
BENCH_NH_RULE_HA = 8.2265
BENCH_NH_TOLERANCE_HA = 0.001

# [REASON]: части инструмента нумерованы по заданию, условия -- по
# предрегистрации (дорожная карта, 2.13): В-6 там условие 3, production -- 1
# и 2. В сводке стоят оба номера, чтобы вывод владельца читался прямо против
# текста условий.
LABEL_V6 = 'part 1, V-6 numbers (2.13 cond. 3)'
LABEL_NH = 'part 2, 1273 reproduction (2.13 report)'
LABEL_INVARIANTS = 'part 3, invariants (2.13 cond. 1)'
LABEL_KML = 'part 3, KML (2.13 cond. 2)'
LABEL_LABELS = 'part 4, labels (2.13 report)'

# [REASON]: причины, по которым правило оставляет участок как есть, --
# строки `gps.edge_scale.site_scales`; тест сверяет ключи с исходником, чтобы
# переименование там не оставило KML без перевода.
OFF_TEXT = {
    'step sets alpha': (
        'шаг проходов не меньше альфы — опрыскивание или другая работа '
        'широкими проходами',
        'ўтишлар оралиғи альфадан кам эмас — пуркаш ёки бошқа кенг ўтишли иш'),
    'wide-pass work': (
        'на участке есть работа широкими проходами: верхняя четверть шагов '
        'не меньше 10 м',
        'участкада кенг ўтишли иш бор: оралиқларнинг юқори чораги 10 метрдан '
        'кам эмас'),
    'no step': (
        'шаг проходов не измерить — слишком мало точек',
        'ўтишлар оралиғини ўлчаб бўлмайди — нуқталар жуда кам'),
}

KML_RULE = ('Правило края поля, второй круг / дала чети қоидаси, иккинчи '
            'босқич')
KML_B2 = 'больше допуска В-2 / В-2 йўл қўйилишидан ташқари'
KML_DELETED = 'удалён целиком / бутунлай олиб ташланди'
KML_GAP = ('Убрано: пустой промежуток у края / олиб ташланди: дала четидаги '
           'бўш оралиқ')
KML_STRIP = ('Убрано: узкая полоса, по которой ездили вдоль / олиб ташланди: '
             'бўйлаб юрилган тор йўлак')
KML_PASSES = ('Оставлено: проходы рядом друг с другом / қолдирилди: ёнма-ён '
              'ўтишлар')
# KML: цвет -- aabbggrr.
REMOVED_STYLES = (
    ('gap', '<LineStyle><color>ff0000ff</color><width>2</width></LineStyle>'
            '<PolyStyle><color>660000ff</color></PolyStyle>'),
    ('strip', '<LineStyle><color>ffff00ff</color><width>2</width></LineStyle>'
              '<PolyStyle><color>66ff00ff</color></PolyStyle>'),
    ('passes', '<LineStyle><color>ffffff00</color><width>2</width></LineStyle>'
               '<PolyStyle><color>44ffff00</color></PolyStyle>'),
)


def _engine_pieces(shape):
    """Участки так, как их режет `gps.area.work_sites`: части формы от 0,3 га."""
    if shape is None:
        return []
    return [piece for piece in getattr(shape, 'geoms', [shape])
            if piece.area / 10000.0 >= MIN_WORK_AREA_HA]


def _is_flat(shape):
    if shape is None:
        return True
    return (shape.geom_type in ('Polygon', 'MultiPolygon')
            and all(piece.geom_type == 'Polygon'
                    for piece in getattr(shape, 'geoms', [shape])))


# --- одни сутки: A7, правило, доказательства ------------------------------------

def evidence(points):
    """Обе формы суток и доказательства правила -- путём `work_sites`.

    [REASON]: `gps.area.work_sites` вызывает `gps.edge_scale.trim` и оставляет
    от него только форму, а `compute_day` пишет многоугольники, округлённые до
    шести знаков: доказательств там нет, а округление сдвигает вершины на
    сантиметры -- на длинной границе это больше 1 м2 инварианта. Поэтому здесь
    повторён путь движка: те же точки окна рабочей скорости в том же порядке,
    та же альфа, та же альфа-форма и тот же `trim` (через атрибут модуля, так
    что подменённый в тесте `trim` видят оба пути). Что повтор дал ровно
    участки `compute_day`, проверяет `consistent_with_engine` на каждых
    сутках.
    """
    track = sorted(points)
    xs, ys = to_utm([row[1] for row in track], [row[2] for row in track])
    mask = _moving_mask([row[3] for row in track])
    xy = [(float(x), float(y))
          for x, y in zip(np.asarray(xs)[mask], np.asarray(ys)[mask])]
    found = {'work_points': len(xy), 'alpha': None, 'spacing': None,
             'a7': [], 'rule': [], 'trimmed': None, 'sites': [], 'removed': [],
             'passes': [], 'fallback': False, 'error': None,
             'evidence_ok': True, 'violations': []}
    if len(xy) < 4:
        return found
    spacing, alpha = _adaptive_alpha(xy, True)
    found.update(alpha=alpha, spacing=spacing)
    today = alpha_shape(densify(xy), alpha)
    if today is None:
        return found
    trimmed, info = edge_scale.trim(xy, today, alpha, spacing, MIN_WORK_AREA_HA)
    info = info if isinstance(info, dict) else {}
    pieces = _engine_pieces(today)
    kept = _engine_pieces(trimmed)
    marks = {mark.get('site'): mark for mark in info.get('sites') or []}
    removed = defaultdict(lambda: {'gap': 0.0, 'strip': 0.0})
    for piece in info.get('removed') or []:
        removed[piece['site']][piece['kind']] += piece['area_m2'] / 10000.0
    passes = defaultdict(list)
    for piece in info.get('passes') or []:
        passes[piece['site']].append(piece['area_m2'] / 10000.0)
    rule_union = unary_union(kept) if kept else None
    # [REASON]: куски формы правила меньше порога работы движок отбрасывает;
    # каждый кусок лежит внутри ровно одного участка A7, и его точка внутри
    # называет участок без пересечения многоугольников.
    small = ([g for g in getattr(trimmed, 'geoms', [trimmed])
              if g.area / 10000.0 < MIN_WORK_AREA_HA] if trimmed is not None else [])
    fallback = bool(info.get('fallback'))
    sites, evidence_ok = [], True
    # номера -- как у `compute_day` и у `trim`: от большего участка к меньшему
    for number_, piece in enumerate(sorted(pieces, key=lambda g: -g.area), 1):
        area = piece.area / 10000.0
        inside = (_ha(piece.intersection(rule_union))
                  if rule_union is not None else 0.0)
        below = sum(g.area for g in small
                    if piece.contains(g.representative_point())) / 10000.0
        site = {'number': number_, 'area': area, 'inside': inside,
                'removed': area - inside,
                'rule_pieces': sum(1 for g in kept
                                   if piece.intersection(g).area > OUTSIDE_M2),
                'gap': removed[number_]['gap'], 'strip': removed[number_]['strip'],
                'below_floor': below, 'passes': len(passes[number_]),
                'passes_ha': sum(passes[number_]), 'on': None, 'off': None,
                'g': None, 'median': None, 'p75': None, 'votes': None,
                'reach': None}
        mark = marks.get(number_)
        # [REASON]: доказательства участка -- по номеру `trim`; номер верен,
        # только если и площадь та же (равные площади могли бы поменять
        # участки местами). Иначе доказательства не этого участка, и сутки
        # -- расхождение повтора, а не нарушение правила.
        if mark is not None and abs(mark.get('area_m2', -1.0) - piece.area) <= 1e-6:
            site.update({key: mark.get(key) for key in
                         ('on', 'off', 'g', 'median', 'p75', 'votes', 'reach')})
        elif not fallback:
            evidence_ok = False
        sites.append(site)
    found.update(a7=pieces, rule=kept, trimmed=trimmed, sites=sites,
                 removed=list(info.get('removed') or []),
                 passes=list(info.get('passes') or []),
                 fallback=fallback, error=info.get('error'),
                 evidence_ok=evidence_ok,
                 violations=invariant_violations(pieces, kept, trimmed, sites))
    return found


def invariant_violations(a7_pieces, rule_pieces, trimmed, sites):
    """Четыре обещания правила на точной геометрии; пустой список -- все целы.

    Гектары не больше, чем у A7; ни один участок правила не выходит за участки
    A7 больше чем на OUTSIDE_M2; форма -- плоские многоугольники; участок, на
    котором правило выключено, не меняется больше чем на OUTSIDE_M2.
    """
    a7_union = unary_union(a7_pieces) if a7_pieces else None
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
    if not _is_flat(trimmed):
        violations.append('the rule shape is not flat polygons: %s%s' % (
            trimmed.geom_type, '' if not hasattr(trimmed, 'geoms') else ' of %s' % (
                ', '.join(sorted({g.geom_type for g in trimmed.geoms})))))
    for site in sites:
        changed_m2 = (site['area'] - site['inside']) * 10000.0
        if site['on'] is False and changed_m2 > OUTSIDE_M2:
            violations.append('A7 site %d is left alone (%s) yet changed by %.1f m2'
                              % (site['number'], site['off'], changed_m2))
    return violations


def replay(points, contours):
    """A7 и правило на одних и тех же точках -- так, как их считает ночной
    расчёт, -- и доказательства правила."""
    began = time.perf_counter()
    a7 = compute_day(points, contours=contours)
    middle = time.perf_counter()
    rule = compute_day(points, contours=contours, edge_rule=RULE)
    ended = time.perf_counter()
    return {'a7': a7, 'rule': rule,
            'found': evidence(points) if a7.reason is None else None,
            'seconds_a7': middle - began, 'seconds_rule': ended - middle}


def consistent_with_engine(found, a7, rule):
    """Дал ли повтор ровно участки `compute_day` -- A7 и правила.

    Площади до четырёх знаков, альфа и шаг -- как в строках; и доказательства
    `trim` относятся к тем же участкам (номер и площадь).
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
    return (found['evidence_ok']
            and all((row['alpha_used_m'], row['pass_spacing_m']) == expected
                    for row in a7.sites + rule.sites))


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
        'fallback': bool(found and found['fallback']),
        'error': found['error'] if found else None,
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


def site_deleted(site):
    return site_changed(site) and site['rule_pieces'] == 0


def site_line(site):
    """«site 1: 8.329 -> 8.227 ha, removed 0.102 ha (1.2%)» и отметки.

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
    if site_deleted(site):
        text += ' [deleted]'
    return text


def _step_text(site):
    def value(x):
        return '-' if x is None else '%.2f' % x
    return ('step g %s m (median %s, p75 %s, %s votes)'
            % (value(site['g']), value(site['median']), value(site['p75']),
               '-' if site['votes'] is None else site['votes']))


def evidence_text(site):
    if site['on'] is None:
        return 'evidence not available'
    if not site['on']:
        return 'left alone: %s; %s' % (site['off'], _step_text(site))
    parts = []
    if site_changed(site):
        parts.append('removed: gaps %.3f ha, strips %.3f ha, below the floor %.3f ha'
                     % (site['gap'], site['strip'], site['below_floor']))
    if site['passes']:
        parts.append('kept as passes: %d strip(s), %.3f ha'
                     % (site['passes'], site['passes_ha']))
    # [REASON]: правило может разрезать участок на части; сколько участков
    # правила лежит в участке A7 -- то, чего гектары одни не скажут.
    parts.append('in %d rule site(s)' % site['rule_pieces'])
    parts.append('%s, reach %s m' % (_step_text(site), '-' if site['reach'] is None
                                     else '%.2f' % site['reach']))
    return '; '.join(parts)


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
    if record['fallback']:
        out('%sFALLBACK: the rule failed (%s) and the day kept the A7 shape'
            % (indent, console(record['error'] or '')))
    for site in record['sites']:
        out('%s%s; %s' % (indent, site_line(site), evidence_text(site)))
    for violation in record['violations']:
        out('%sVIOLATION: %s' % (indent, violation))


# --- часть 1: набор В-6 ----------------------------------------------------------

def _replay_row(con, folder, contours, row, unit, out):
    """Сутки строки В-6 у объекта `unit`: запись прогона или None (точек нет)."""
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


def _v6_line(row, unit, record, word):
    return ('V6EDGE n=%d unit=%s day=%s a7=%s rule=%s control=%s'
            % (row['n'], '-' if unit is None else unit, row['day'],
               '-' if record is None else '%.3f' % record['a7_ha'],
               '-' if record is None else '%.3f' % record['rule_ha'], word))


def row_word(record):
    """Слово контроля строки В-6 по записи прогона."""
    if record is None:
        return 'UNRESOLVED'
    if record['control'] is False or not record['consistent']:
        return 'MISMATCH'
    if record['control'] is None:
        return 'UNRESOLVED'
    # [REASON]: правило упало -- сутки остались формой A7, и «гектары
    # правила» в строке были бы гектарами A7: записать их в CSV значит
    # сравнить A7 с самим собой. Строка не проверена, пока движок не починен.
    if record['fallback']:
        return 'FALLBACK'
    return 'ok'


def part_v6(con, folder, contours, rows, out):
    out('=== PART 1 -- V-6 acceptance set, the rule\'s numbers for the csv '
        '(roadmap 2.13, condition 3): %d row(s) ===' % len(rows))
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
                shown = by_hectares[:10]
            for unit in shown:
                out('    candidate unit %d stored %.4f ha  %s'
                    % (unit, totals[unit], console(names.get(unit) or '')))
            unit = record = None
            if len(by_hectares) == 1:
                # [REASON]: как в первом круге: имя машины в книге и в
                # справочнике пишут по-разному, а прогон на сервере -- день
                # владельца. Сутки единственного объекта с гектарами строки
                # считаются сразу; строка -- UNRESOLVED, пока сессия не
                # подтвердит машину по имени.
                unit = by_hectares[0]
                out('  matched by hectares only -- confirm the machine: unit %d %s'
                    % (unit, console(names.get(unit) or '')))
                record = _replay_row(con, folder, contours, row, unit, out)
            out(_v6_line(row, unit, record, 'UNRESOLVED'))
            words.append('UNRESOLVED')
            continue
        unit = chosen[0]
        out('  unit %d %s' % (unit, console(names.get(unit) or '')))
        record = _replay_row(con, folder, contours, row, unit, out)
        word = row_word(record)
        words.append(word)
        out(_v6_line(row, unit, record, word))
    if 'MISMATCH' in words:
        verdict, note = RUN_INVALID, ('the control failed on %d row(s): fix the '
                                      'replay and run it again'
                                      % words.count('MISMATCH'))
    elif any(word != 'ok' for word in words) or not words:
        verdict, note = NOT_CHECKED, (
            '%d of %d row(s) could not be checked: %s' % (
                sum(1 for word in words if word != 'ok'), len(words),
                ', '.join('%s %d' % (word, words.count(word))
                          for word in ('UNRESOLVED', 'FALLBACK')
                          if word in words) or 'no rows'))
    else:
        verdict, note = PASS, 'all %d row(s) resolved, control ok' % len(words)
    out('PART 1 (V-6 rows resolved and reproduced; hectares are not judged '
        'here): %s -- %s' % (verdict, note))
    return [(LABEL_V6, verdict)]


# --- часть 2: сутки по просьбе ----------------------------------------------------

def owner_day_report(record, stored, out):
    title = 'NEW HOLLAND %s:%d (owner\'s hand measurement %.3f ha; a bench day)' % (
        OWNER_DAY[0], OWNER_DAY[1], OWNER_HA)
    if record is None or not stored['has_row'] or stored['reason'] is not None \
            or not record['sites']:
        out('%s: %s -- the day is not published, has no A7 site or its points are '
            'gone' % (title, NOT_CHECKED))
        return [(LABEL_NH, NOT_CHECKED)]
    if record['control'] is False or not record['consistent']:
        out('%s: %s -- the replay does not reproduce this day' % (title, RUN_INVALID))
        return [(LABEL_NH, RUN_INVALID)]
    largest = record['sites'][0]
    out('%s: rule %.4f ha inside the largest A7 site (A7 %.4f ha); against the '
        'owner: A7 %+.1f%%, rule %+.1f%% (report, no verdict)'
        % (title, largest['inside'], largest['area'],
           _share(largest['area'] - OWNER_HA, OWNER_HA),
           _share(largest['inside'] - OWNER_HA, OWNER_HA)))
    gap = round(abs(largest['inside'] - BENCH_NH_RULE_HA), 6)
    verdict = PASS if gap <= BENCH_NH_TOLERANCE_HA else OWNER_CHECK
    out('REPRODUCTION %s:%d: %s -- rule %.4f ha inside the largest A7 site against '
        '%.4f +- %.3f ha on the bench (the same points, exported 10-11.10)%s'
        % (OWNER_DAY[0], OWNER_DAY[1], verdict, largest['inside'],
           BENCH_NH_RULE_HA, BENCH_NH_TOLERANCE_HA,
           '' if verdict == PASS else ' -- the server does not compute what the '
           'bench did: the session looks before any number is used'))
    return [(LABEL_NH, verdict)]


def part_days(con, folder, contours, pairs, out):
    out('=== PART 2 -- machine-days asked for (2026-10-01:1273 -- roadmap 2.13, '
        'report): %d ===' % len(pairs))
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
            verdicts += owner_day_report(record, stored, out)
    if OWNER_DAY not in pairs:
        out('(no verdict: the days above are printed for the eye; only %s:%d has '
            'a reproduction check)' % OWNER_DAY)
    return verdicts


# --- часть 3: production -----------------------------------------------------------

def report_period(records, total, excluded, gone, out):
    """Контроль, инварианты, отчёт. Возвращает вердикт условия 1."""
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
    fallen = [row for row in records if row['fallback']]
    for row in fallen[:20]:
        out('  FALLBACK %s %d: %s' % (row['day'], row['unit'],
                                      console(row['error'] or '')))
    # [REASON]: как у первого круга: нарушение считается по одному повтору
    # точек, база в нём не участвует, поэтому несошедшийся контроль на других
    # сутках его не отменяет. Не доверяются только сутки, где повтор
    # разошёлся с движком.
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
    elif fallen:
        # [REASON]: правило, упавшее на сутках, их не изменило -- инварианты
        # там выполнены пусто. Это не довод за правило: там оно не
        # проверено, и его исключение чинится до решения владельца.
        verdict, note = NOT_CHECKED, ('the rule failed on %d machine-day(s) and '
                                      'left them as A7: not checked there'
                                      % len(fallen))
    else:
        verdict, note = PASS, ('no invariant broken on %d machine-days, the rule '
                               'ran on all of them' % len(records))
    out('INVARIANTS (the rule never adds hectares, never leaves the A7 sites by more '
        'than %.0f m2, returns flat polygons, never changes a site it leaves alone; '
        'and it ran on every day): %s -- %s' % (OUTSIDE_M2, verdict, note))
    out('report, no verdict:')
    changed = [row for row in records if row['changed']]
    out('  machine-days changed: %d of %d' % (len(changed), len(records)))
    a7_ha = sum(row['a7_ha'] for row in records)
    rule_ha = sum(row['rule_ha'] for row in records)
    out('  plan-fact of the period, counted objects: A7 %.2f ha -> rule %.2f ha '
        '(%+.2f ha, %+.2f%%)' % (a7_ha, rule_ha, rule_ha - a7_ha,
                                 -_share(a7_ha - rule_ha, a7_ha)))
    sites = [site for row in records for site in row['sites']]
    acting = [site for site in sites if site['on']]
    out('  A7 sites: %d; the rule acts on %d (%.2f ha); left alone: %s'
        % (len(sites), len(acting), sum(site['area'] for site in acting),
           ', '.join('%s %d (%.2f ha)' % (
               reason, sum(1 for site in sites if site['off'] == reason),
               sum(site['area'] for site in sites if site['off'] == reason))
               for reason in OFF_TEXT)))
    unknown = [site for site in sites if site['on'] is None]
    if unknown:
        out('  A7 sites without evidence (fallback days): %d (%.2f ha)'
            % (len(unknown), sum(site['area'] for site in unknown)))
    out('  removed: empty gaps at the edge %.2f ha, narrow strips %.2f ha, left '
        'under the work floor %.2f ha'
        % (sum(site['gap'] for site in sites), sum(site['strip'] for site in sites),
           sum(site['below_floor'] for site in sites)))
    trimmed = [site for site in sites if site_changed(site)]
    deleted = [site for site in sites if site_deleted(site)]
    out('  A7 sites changed: %d (removed %.2f ha); of them deleted whole: %d '
        '(%.2f ha)' % (len(trimmed), sum(site['removed'] for site in trimmed),
                       len(deleted), sum(site['area'] for site in deleted)))
    out('  narrow strips kept as passes: %d (%.2f ha)'
        % (sum(site['passes'] for site in sites),
           sum(site['passes_ha'] for site in sites)))
    bins = Counter(share_bin((row['a7_ha'] - row['rule_ha']) / row['a7_ha'])
                   for row in records if row['a7_ha'] > EPSILON)
    out('  removed share per machine-day: %s; without an A7 site: %d'
        % (', '.join('%s %d' % (name, bins.get(name, 0)) for name in SHARE_BINS),
           sum(1 for row in records if row['a7_ha'] <= EPSILON)))
    out('  the rule fell back to A7: %d machine-day(s)' % len(fallen))
    if records:
        out('  seconds per machine-day: A7 median %.2f max %.2f; rule median %.2f '
            'max %.2f' % (statistics.median(row['seconds_a7'] for row in records),
                          max(row['seconds_a7'] for row in records),
                          statistics.median(row['seconds_rule'] for row in records),
                          max(row['seconds_rule'] for row in records)))
    return verdict


def _area_text(value):
    return number(value, 3)


def site_label(site):
    """(имя, описание) участка A7 в KML; у изменённого -- было, стало, убрано
    и из чего убрано; у нетронутого -- почему."""
    area, inside = _area_text(site['area']), _area_text(site['inside'])
    if not site_changed(site):
        if site['on'] is False:
            ru, uz = OFF_TEXT.get(site['off'], (site['off'], site['off']))
            why = ('Правило на этом участке не действует: %s. / Қоида бу участкада '
                   'ишламайди: %s.' % (ru, uz))
        elif site['on']:
            why = ('Правило проверило участок и ничего не убрало. / Қоида '
                   'участкани текширди ва ҳеч нарса олиб ташламади.')
        else:
            why = ('Доказательств правила нет. / Қоида далиллари йўқ.')
        return ('Участок / участка %d: %s га, без изменений / ўзгаришсиз'
                % (site['number'], area),
                'A7 (действующий метод): %s га. %s / A7 (амалдаги усул): %s га.'
                % (area, why, area))
    removed = _area_text(site['removed'])
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
    if site_deleted(site):
        name += ', ' + KML_DELETED
    parts = (_area_text(site['gap']), _area_text(site['strip']),
             _area_text(site['below_floor']))
    description = (
        'A7 (действующий метод): %s га. Правило внутри участка: %s га. Убрано %s га '
        '(%s %%): пустые промежутки у края %s га, узкие полосы, по которым ездили '
        'вдоль, %s га, остаток меньше 0,3 га %s га%s. / A7 (амалдаги усул): %s га. '
        'Участка ичида қоида: %s га. Олиб ташланди %s га (%s %%): дала четидаги '
        'бўш оралиқлар %s га, бўйлаб юрилган тор йўлаклар %s га, 0,3 гектардан '
        'кичик қолдиқ %s га%s.'
        % ((area, inside, removed, share) + parts
           + ('; больше допуска В-2 (%s)' % band if beyond else '',)
           + (area, inside, removed, share) + parts
           + ('; В-2 йўл қўйилишидан ташқари (%s)' % band_uz if beyond else '',)))
    return name, description


def _geometry_kml(polygon):
    return _shapes(polygon_geojson(polygon)) if polygon is not None else ''


def kml_folder(record, played, points, name):
    day = datetime.strptime(record['day'], '%Y-%m-%d').strftime('%d.%m.%Y')
    removed = record['a7_ha'] - record['rule_ha']
    title = ('%s · %s · A7 %s га → правило / қоида %s га, убрано / олиб ташланди '
             '%s га (%s %%)' % (name or record['unit'], day,
                                number(record['a7_ha'], 3),
                                number(record['rule_ha'], 3), number(removed, 3),
                                number(_share(removed, record['a7_ha']), 1)))
    parts = ['<Folder><name>%s</name>' % escape(title)]
    found = played['found'] or {}
    for site, row in zip(record['sites'],
                         sorted(played['a7'].sites, key=lambda r: r['site_number'])):
        geometry = _shapes(row['polygon_geojson'])
        if not geometry:
            continue
        label, description = site_label(site)
        parts.append('<Placemark><name>%s</name><description>%s</description>'
                     '<styleUrl>#site</styleUrl>%s</Placemark>'
                     % (escape(label), escape(description), geometry))
    for row in sorted(played['rule'].sites, key=lambda r: r['site_number']):
        geometry = _shapes(row['polygon_geojson'])
        if geometry:
            parts.append('<Placemark><name>%s</name><styleUrl>#new</styleUrl>%s'
                         '</Placemark>'
                         % (escape('%s: участок / участка %d — %s га'
                                   % (KML_RULE, row['site_number'],
                                      number(row['area_ha'], 3))), geometry))
    for piece in list(found.get('removed') or []) + list(found.get('passes') or []):
        geometry = _geometry_kml(piece.get('polygon'))
        if not geometry:
            continue
        label = {'gap': KML_GAP, 'strip': KML_STRIP, 'passes': KML_PASSES}.get(
            piece['kind'], piece['kind'])
        parts.append('<Placemark><name>%s</name><styleUrl>#%s</styleUrl>%s'
                     '</Placemark>'
                     % (escape('%s, %s га (участок / участка %d)'
                               % (label, number(piece['area_m2'] / 10000.0, 3),
                                  piece['site'])), piece['kind'], geometry))
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
                     for key, body in tuple(STYLES) + (NEW_STYLE,) + REMOVED_STYLES)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
            '<name>%s</name>%s%s</Document></kml>\n'
            % (escape('GPS: %s — A7 и правило / A7 ва қоида' % KML_RULE), styles,
               ''.join(folders)))


def part_period(con, folder, contours, since, until, kml_path, out, progress=None):
    out('=== PART 3 -- production %s .. %s (roadmap 2.13, conditions 1 and 2) ==='
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
                    out('    %s; %s' % (site_line(site), evidence_text(site)))
    verdicts = [(LABEL_INVARIANTS, verdict)]
    if largest or rest:
        verdicts.append((LABEL_KML, OWNER_CHECK))
    if kml_path:
        folders = []
        for row in largest + rest:
            points = read_day_readonly(folder, row['unit'], row['day'])
            folders.append(kml_folder(row, replay(points, contours), points,
                                      names.get(row['unit'])))
        with open(kml_path, 'w', encoding='utf-8') as handle:
            handle.write(kml_document(folders))
        out('KML written (%d machine-days): %s' % (len(folders), console(kml_path)))
    return verdicts


# --- часть 4: ответы операторов ------------------------------------------------------

def part_labels(con, folder, contours, since, until, out, progress=None):
    out('=== PART 4 -- operator answers %s and %s, %s (roadmap 2.13, report) ===' % (
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
        a7_union = unary_union(found['a7']) if found.get('a7') else None
        rule_union = unary_union(found['rule']) if found.get('rule') else None
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
            measured.append(dict(site, a7=a7_in, rule=rule_in))
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
            'polygons, removed %.3f ha (%.1f%%), kept %.1f%%'
            % (console(label), len(mine), a7_ha, rule_ha, a7_ha - rule_ha,
               _share(a7_ha - rule_ha, a7_ha), _share(rule_ha, a7_ha)))
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
    out('PART 4 (labels, no FAIL here): %s -- %s' % (verdict, note))
    return [(LABEL_LABELS, verdict)]


# --- запуск ------------------------------------------------------------------------

def _refuse(message):
    sys.stderr.write('ERROR: %s\n' % message)
    return 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=console(__doc__.splitlines()[0]))
    parser.add_argument('--v6', help='part 1: the V-6 acceptance set csv')
    parser.add_argument('--day', action='append', default=[],
                        help='part 2: YYYY-MM-DD:WIALON_ID, may repeat')
    parser.add_argument('--since', help='part 3: first date, YYYY-MM-DD')
    parser.add_argument('--until', help='part 3 (and 4): last date, YYYY-MM-DD')
    parser.add_argument('--kml', help='part 3: KML file for the owner')
    parser.add_argument('--labels', action='store_true',
                        help='part 4: operator answers; the period options '
                             'restrict it')
    parser.add_argument('--db', help='path to transport.db')
    parser.add_argument('--dir', help='folder with gps_points_YYYYMM.db files')
    args = parser.parse_args(argv)

    if not (args.v6 or args.day or args.since or args.labels):
        return _refuse('nothing to do: give --v6, --day, --since or --labels')
    if args.v6 and not os.path.isfile(args.v6):
        return _refuse('not found: %s' % console(args.v6))
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
        return _refuse('--kml goes with --since (part 3)')
    if args.kml and not os.path.isdir(os.path.dirname(os.path.abspath(args.kml))):
        return _refuse('the folder for --kml does not exist')
    pairs = []
    for text in args.day:
        pair = parse_pair(text)
        if pair is None:
            return _refuse('--day must look like YYYY-MM-DD:WIALON_ID')
        if pair not in pairs:
            pairs.append(pair)
    if (not args.db or not os.path.isfile(args.db)
            or not args.dir or not os.path.isdir(args.dir)):
        return _refuse('every part needs --db (an existing database) and --dir '
                       '(an existing points folder)')
    # [REASON]: всё, что читается из файлов, читается ДО первого расчёта:
    # неверный ввод -- код 2 и ничего не посчитано, а не обрыв через час.
    try:
        v6_rows = load_v5(args.v6, 'V-6') if args.v6 else None
    except BadInput as error:
        return _refuse(error)

    def progress(line):
        print(line, flush=True)

    print('A7: compute_day, method %s; rule: compute_day(edge_rule=%r), method %s'
          % (method_version(True), RULE, method_version(True, edge_rule=RULE)))
    verdicts = []
    con = open_readonly(args.db)
    try:
        contours = load_contours(con) or None
        if v6_rows is not None:
            verdicts += part_v6(con, args.dir, contours, v6_rows, print)
        if pairs:
            verdicts += part_days(con, args.dir, contours, pairs, print)
        if since:
            verdicts += part_period(con, args.dir, contours, since, until, args.kml,
                                    print, progress)
        if args.labels:
            verdicts += part_labels(con, args.dir, contours, since, until, print,
                                    progress)
    finally:
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
