# -*- coding: utf-8 -*-
"""DRONE-COVERAGE-001, этап B: диагностика собранных маршрутов.

    python tools/drone_route_semantics_probe.py --outbox drone_collector/data/outbox
    python tools/drone_route_semantics_probe.py --outbox ... --report out.txt

ЧТО ЭТО ОТВЕЧАЕТ И ЧЕГО НЕ ОТВЕЧАЕТ

Три открытых вопроса этапа B, по каждому -- НАБЛЮДЕНИЯ, а не выводы:

  1. `mission_uuid` -- это идентификатор задания? Считается, сколько маршрутов
     его несут и по скольку маршрутов приходится на одно значение. Группа из
     нескольких вылетов СОГЛАСУЕТСЯ с трактовкой «задание», но её не
     доказывает: точно так же выглядел бы любой признак, общий у вылетов
     одного дня одной машины.
  2. Есть ли ещё идентификаторы задания? Перечисляются неопознанные поля
     protobuf: номер, тип, как часто встречаются, сколько различных значений.
     Поле, у которого различных значений столько же, сколько маршрутов, --
     кандидат в идентификатор записи; поле, у которого их меньше, -- кандидат
     в идентификатор группы. Кандидат, а не ответ.
  3. Что DJI кладёт в `new_work_area`? Считается ОДНОСТОРОННЯЯ проверка
     согласованности: длина пути, которой требует площадь DJI при её
     собственной ширине (`area / width`), против длины фактического маршрута.
     Отношение больше единицы означает, что площадь нельзя свести с этим
     маршрутом при рабочей гипотезе; отношение меньше единицы НЕ подтверждает
     гипотезу -- ей удовлетворяет любая площадь, не превышающая длину на
     ширину.

ЧЕГО ЗДЕСЬ НЕТ

Ни оплачиваемых гектаров, ни уникального покрытия, ни слова «обработано».
Маршрут -- это геометрический путь. Состояния распыления в источнике
`data_type=simplified` нет, поэтому назвать маршрут работой нечем.

БЕЗ СЕТИ И БЕЗ БАЗЫ. Читается только каталог очереди, который собрал
`python -m drone_collector.main --routes`. Значения идентификаторов
(`mission_uuid`, содержимое неопознанных полей) в вывод НЕ печатаются --
печатаются их количества и распределения.

Вывод в консоль -- только ASCII; развёрнутый отчёт уходит в --report.
"""

import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

EXIT_OK = 0
EXIT_NOTHING = 1
EXIT_NO_DIRECTORY = 2

EARTH_RADIUS_M = 6378137.0

# Границы гистограммы отношения «нужная длина / фактическая длина».
RATIO_BUCKETS = (0.25, 0.5, 0.75, 0.9, 1.0, 1.1, 1.5, 2.0)


def path_length_m(points):
    """Длина ломаной в метрах. Равнопромежуточная проекция о центр трека.

    [REASON]: формула повторяет `drone_collector/route_decode.path_length_m`
    намеренно. Инструмент живёт в Python приложения, декодер -- в venv
    collector; тащить один в другой запрещено уставом. Совпадение чисел
    проверяется тестом на одних и тех же точках.
    """
    if len(points) < 2:
        return 0.0
    lat0 = sum(p[0] for p in points) / len(points)
    lng0 = sum(p[1] for p in points) / len(points)
    scale = math.cos(math.radians(lat0))
    flat = [(math.radians(p[1] - lng0) * EARTH_RADIUS_M * scale,
             math.radians(p[0] - lat0) * EARTH_RADIUS_M) for p in points]
    return sum(math.dist(flat[i], flat[i + 1]) for i in range(len(flat) - 1))


def load_routes(outbox_dir):
    """Тела маршрутов из очереди -- и из `pending/`, и из `sent/`."""
    routes = []
    unreadable = 0
    for bucket in ('pending', 'sent'):
        directory = os.path.join(outbox_dir, bucket)
        if not os.path.isdir(directory):
            continue
        for name in sorted(os.listdir(directory)):
            if not name.endswith('.json'):
                continue
            try:
                with open(os.path.join(directory, name), encoding='utf-8') as h:
                    envelope = json.load(h)
            except (ValueError, OSError, UnicodeDecodeError):
                unreadable += 1
                continue
            if not isinstance(envelope, dict) or envelope.get('kind') != 'route':
                continue
            body = envelope.get('body')
            if isinstance(body, dict):
                routes.append(body)
    return routes, unreadable


def mission_grouping(routes):
    """Как маршруты раскладываются по `mission_uuid`. Значения не печатаются."""
    groups = {}
    without = 0
    for route in routes:
        value = route.get('mission_uuid')
        if not value:
            without += 1
            continue
        groups.setdefault(value, []).append(route)
    sizes = {}
    for members in groups.values():
        sizes[len(members)] = sizes.get(len(members), 0) + 1
    return {
        'routes': len(routes),
        'with_mission_uuid': len(routes) - without,
        'without_mission_uuid': without,
        'distinct_values': len(groups),
        'group_sizes': dict(sorted(sizes.items())),
        'largest_group': max(sizes) if sizes else 0,
    }


def unknown_field_census(routes):
    """Неопознанные поля protobuf: номер, частота, число различных значений."""
    census = {}
    for route in routes:
        for entry in route.get('unknown_fields') or ():
            if not isinstance(entry, dict):
                continue
            number = entry.get('field')
            record = census.setdefault(number, {'field': number,
                                                'wire_types': set(),
                                                'seen': 0,
                                                'digests': set()})
            record['seen'] += 1
            record['wire_types'].add(entry.get('wire'))
            digest = entry.get('sha256') or entry.get('varint')
            if digest is not None:
                record['digests'].add(digest)
    out = []
    for number in sorted(census, key=lambda value: (value is None, value)):
        record = census[number]
        distinct = len(record['digests'])
        out.append({
            'field': number,
            'wire_types': sorted(w for w in record['wire_types']
                                 if w is not None),
            'seen': record['seen'],
            'distinct_values': distinct,
            # Классификация КАНДИДАТА, не вывод. Названа так, чтобы её нельзя
            # было пересказать как установленный факт.
            'candidate': _candidate_kind(record['seen'], distinct),
        })
    return out


def _candidate_kind(seen, distinct):
    if distinct <= 1:
        return 'constant-like'
    if distinct == seen:
        return 'per-route-identifier-like'
    return 'group-identifier-like'


def area_consistency(routes):
    """Односторонняя проверка согласованности площади DJI с маршрутом.

    Возвращает сводку по маршрутам, у которых ЕСТЬ и площадь, и записанная
    ширина. Остальные считаются непроверяемыми и называются так прямо.
    """
    ratios = []
    skipped_no_width = 0
    skipped_no_area = 0
    skipped_short = 0
    for route in routes:
        area = route.get('dji_area_m2')
        width = route.get('spray_width_m')
        points = route.get('points') or []
        if area is None:
            skipped_no_area += 1
            continue
        if width is None or width <= 0:
            # Подстановки здесь нет и быть не может: решение владельца
            # 2026-08-25. Такой вылет просто непроверяем.
            skipped_no_width += 1
            continue
        if len(points) < 2:
            skipped_short += 1
            continue
        actual = path_length_m([(p[0], p[1]) for p in points])
        if actual <= 0:
            skipped_short += 1
            continue
        ratios.append((area / width) / actual)

    histogram = {}
    for ratio in ratios:
        histogram[_bucket(ratio)] = histogram.get(_bucket(ratio), 0) + 1
    ordered = sorted(ratios)
    return {
        'checked': len(ratios),
        'skipped_no_area': skipped_no_area,
        'skipped_no_width': skipped_no_width,
        'skipped_too_short': skipped_short,
        'exceeding_the_route': sum(1 for ratio in ratios if ratio > 1.0),
        'median': _median(ordered),
        'min': ordered[0] if ordered else None,
        'max': ordered[-1] if ordered else None,
        'histogram': {key: histogram[key] for key in sorted(histogram)},
    }


def _bucket(ratio):
    for edge in RATIO_BUCKETS:
        if ratio <= edge:
            return '<=%.2f' % edge
    return '>%.2f' % RATIO_BUCKETS[-1]


def _median(ordered):
    if not ordered:
        return None
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def coverage_census(routes):
    """Что вообще есть в собранном: ширина, борт, тип ответа, точки."""
    with_width = sum(1 for r in routes if r.get('spray_width_m') is not None)
    with_hardware = sum(1 for r in routes if r.get('hardware_id'))
    points = sum(int(r.get('point_count') or 0) for r in routes)
    data_types = {}
    for route in routes:
        value = route.get('data_type')
        data_types[value] = data_types.get(value, 0) + 1
    return {
        'routes': len(routes),
        'with_recorded_width': with_width,
        'without_recorded_width': len(routes) - with_width,
        'with_hardware_id': with_hardware,
        'total_points': points,
        'points_per_route': (points / len(routes)) if routes else None,
        'data_types': data_types,
    }


# ─── Вывод ───────────────────────────────────────────────────────────────────

def format_report(routes, unreadable):
    lines = []
    add = lines.append

    census = coverage_census(routes)
    add('WHAT WAS COLLECTED')
    add('  routes in the outbox        : %d' % census['routes'])
    add('  unreadable queue files      : %d' % unreadable)
    add('  with a recorded width       : %d' % census['with_recorded_width'])
    add('  without a recorded width    : %d  (never substituted)'
        % census['without_recorded_width'])
    add('  carrying a hardware id      : %d' % census['with_hardware_id'])
    add('  points in total             : %d' % census['total_points'])
    if census['points_per_route'] is not None:
        add('  points per route            : %.1f' % census['points_per_route'])
    for value in sorted(census['data_types'], key=lambda v: str(v)):
        add('  data_type %-18s: %d' % (str(value), census['data_types'][value]))
    add('')

    grouping = mission_grouping(routes)
    add('QUESTION 1 -- mission_uuid')
    add('  routes carrying it          : %d' % grouping['with_mission_uuid'])
    add('  routes without it           : %d' % grouping['without_mission_uuid'])
    add('  distinct values             : %d' % grouping['distinct_values'])
    add('  largest group of routes     : %d' % grouping['largest_group'])
    for size in sorted(grouping['group_sizes']):
        add('    groups of %-3d routes      : %d'
            % (size, grouping['group_sizes'][size]))
    add('  A group of several routes is CONSISTENT with mission_uuid being a')
    add('  task identifier. It does not prove it: any attribute shared by the')
    add('  flights of one day of one machine would look exactly the same.')
    add('')

    add('QUESTION 2 -- other identifiers among the unrecognised fields')
    fields = unknown_field_census(routes)
    if not fields:
        add('  none: every field of every route was recognised.')
    for entry in fields:
        add('  field %-4s wire %-8s seen %-6d distinct %-6d %s'
            % (entry['field'],
               ','.join(str(w) for w in entry['wire_types']) or '-',
               entry['seen'], entry['distinct_values'], entry['candidate']))
    add('  "candidate" classifies the SHAPE of the values, nothing more.')
    add('  Confirm a meaning by changing it in the cabinet and watching the')
    add('  response change -- never by the shape alone.')
    add('')

    add('QUESTION 3 -- what DJI puts in new_work_area')
    consistency = area_consistency(routes)
    add('  routes the check can run on : %d' % consistency['checked'])
    add('  skipped, no area            : %d' % consistency['skipped_no_area'])
    add('  skipped, no recorded width  : %d' % consistency['skipped_no_width'])
    add('  skipped, fewer than 2 points: %d' % consistency['skipped_too_short'])
    if consistency['checked']:
        add('  ratio needed-length / flown-length:')
        add('    min    : %.4f' % consistency['min'])
        add('    median : %.4f' % consistency['median'])
        add('    max    : %.4f' % consistency['max'])
        for bucket in consistency['histogram']:
            add('    %-8s : %d' % (bucket, consistency['histogram'][bucket]))
        add('  above 1.0 (area cannot be reconciled with the route): %d'
            % consistency['exceeding_the_route'])
    add('  The check is ONE-SIDED. A ratio above 1.0 falsifies reading the')
    add('  record as an independent area of that route under the working')
    add('  hypothesis. A ratio below 1.0 confirms nothing: any area no larger')
    add('  than length x width satisfies it just as well.')
    add('')
    add('NOT ESTABLISHED BY ANY NUMBER ABOVE')
    add('  * how DJI computes the swath width;')
    add('  * that DJI derives the SWATH WIDTH from the area and the route;')
    add('  * that a route segment was sprayed -- the payload carries no pump')
    add('    and no spray state at all, and that is proved only for')
    add('    data_type=simplified, not for every DJI source;')
    add('  * any figure of overpayment, double billing or billable hectares.')
    return lines


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='DRONE-COVERAGE-001 stage B: what the collected routes '
                    'say about mission_uuid, task identifiers and '
                    'new_work_area. Observations only, no verdicts.')
    parser.add_argument('--outbox', required=True,
                        help='the outbox directory written by '
                             '`--routes` (the one holding pending/ and sent/)')
    parser.add_argument('--report', default=None,
                        help='write the full report to this UTF-8 file as '
                             'well as to the console')
    args = parser.parse_args(argv)

    if not os.path.isdir(args.outbox):
        print('NO SUCH DIRECTORY: %s' % args.outbox)
        print('Collect the routes first:')
        print('  python -m drone_collector.main --routes --ids-file ids.txt')
        return EXIT_NO_DIRECTORY

    routes, unreadable = load_routes(args.outbox)
    lines = format_report(routes, unreadable)
    for line in lines:
        print(_ascii(line))

    if args.report:
        with open(args.report, 'w', encoding='utf-8') as handle:
            handle.write('\n'.join(lines) + '\n')
        print('')
        print('Report written: %s' % args.report)

    if not routes:
        print('')
        print('NOTHING TO ANALYSE: the outbox holds no route envelope.')
        return EXIT_NOTHING
    return EXIT_OK


def _ascii(text):
    """Консоль получает только ASCII -- правило устава для Windows-хоста."""
    return text.encode('ascii', 'replace').decode('ascii')


if __name__ == '__main__':
    sys.exit(main())
