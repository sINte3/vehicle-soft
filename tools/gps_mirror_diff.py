# -*- coding: utf-8 -*-
"""GPS-21 -- что изменила запись зеркала контуров: копия базы до неё против базы после. Только чтение.

ЗАЧЕМ
15.10 зеркало контуров (`tools/gps_mirror_contours.py`, GPS-4) на сервере
владельца переписало 16 097 строк `field_contours` (`source='wialon'`) из
17 817 и добавило 13. Шаги сессии ждали меньше 500 обновлений. Зеркало
сравнивает строки целиком: имя зоны и текст геометрии (GeoJSON). Поэтому
число не говорит, что поменялось: имена, контуры или только запись тех же
точек. Копия базы, снятая перед записью
(`backup_transport_db.py --suffix kontury`), хранит строки «до». Этот
инструмент сравнивает её с базой «после» и раскладывает изменения по видам.

ЧТО ДЕЛАЕТ
Обе базы открываются `mode=ro`. Читаются строки `field_contours` с
`source='wialon'`, ключ -- `external_id` (id зоны Wialon). Строки других
источников (контуры DJI и прочие) не сравниваются: зеркало их не трогает.
Они только пересчитываются по источникам, до и после.

По каждой зоне:
  - есть только «после» -- `novaya`, только «до» -- `propala` (зеркало не
    удаляет строки, поэтому `propala` должна быть 0);
  - имя: изменилось ли; если да, осталось ли прежнее имя внутри нового
    (дописали район или номер) и не отличаются ли они только пробелами;
  - геометрия: тот же текст; те же координаты другой записью; то же кольцо
    с другого начала или в другую сторону; те же точки в другом порядке;
    другие точки; контур появился или пропал;
  - для другого контура -- площадь до и после и сдвиг центра в метрах.
    Площадь приближённая (местная равнопромежуточная проекция по средней
    широте кольца) и годится только для сравнения «до» с «после». Площадь
    расчёта -- в `gps/area.py` (UTM 41N);
  - `is_active`: менялся ли (зеркало его не меняет).

Пишет CSV по каждой изменившейся, новой или пропавшей зоне (`;`, UTF-8 с BOM
-- для Excel). В CSV -- имена «до» и «после»: имена зон часто содержат имена
людей. Поэтому CSV не кладётся в репозиторий, а в консоль имена не выводятся.
В консоли -- только числа (ASCII).

ЧЕГО НЕ ДЕЛАЕТ
Ничего не пишет ни в одну базу и ничего не исправляет. Оговорка та же, что у
GPS-19: SQLite, открывая базу в режиме WAL на чтение, может создать рядом
пустые служебные `-wal` и `-shm`; данные не меняются. Обращений к Wialon
нет. Что делать с найденным, решает владелец.

Коды выхода:
  0 -- сравнение сделано, CSV записан;
  2 -- неверный вход, ничего не записано: базы нет (файл не создаётся),
       --before и --after -- один файл, в пути к базе есть `#`, `?` или `%`
       (SQLite прочёл бы его как адрес), в базе нет `field_contours`,
       --out указывает на базу, её -wal/-shm или любой файл SQLite, папки
       для --out нет;
  1 -- инструмент упал (необработанное исключение) -- CSV не записан.

Запуск -- обычный Python сервера, геостек не нужен (PowerShell, по одной
команде на строку):

  & "C:\\Program Files\\Python314\\python.exe" C:\\gps-tools\\tools\\gps_mirror_diff.py --before C:\\gps-tools\\check\\transport_20261015_145308_kontury.db --after C:\\transport-report\\instance\\transport.db --out C:\\gps-tools\\check\\mirror_diff.csv *> C:\\gps-tools\\check\\mirror_diff.log

  $LASTEXITCODE
"""

import argparse
import csv
import json
import math
import os
import sqlite3
import sys

from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gps_collector import config                                   # noqa: E402
from tools.gps_mark_not_ours import out_clash                      # noqa: E402

SOURCE = 'wialon'
EXIT_OK, EXIT_CRASH, EXIT_BAD_INPUT = 0, 1, 2
EARTH_RADIUS_M = 6371008.8
# [REASON]: координаты Wialon приходят с шестью-семью знаками после запятой
# (сантиметры). Точки сравниваются округлёнными до 1e-7 градуса (~1 см), чтобы
# одна и та же точка, записанная двумя путями float -> текст -> float, не
# сошла за другую.
DIGITS = 7

CSV_COLUMNS = ['external_id', 'izmenenie', 'imya', 'imya_vnutri_novogo',
               'imya_tolko_probely', 'geometriya', 'ploshchad_do_ga',
               'ploshchad_posle_ga', 'raznitsa_ga', 'sdvig_tsentra_m',
               'tochek_do', 'tochek_posle', 'is_active_do', 'is_active_posle',
               'imya_do', 'imya_posle']

GEOMETRY_KINDS = [
    ('tot_zhe_tekst', 'same text'),
    ('te_zhe_koordinaty', 'same coordinates, other text'),
    ('to_zhe_koltso', 'same ring, other start or direction'),
    ('te_zhe_tochki', 'same points, other order'),
    ('drugie_tochki', 'other points'),
    ('poyavilsya', 'outline appeared'),
    ('propal', 'outline disappeared'),
    ('ne_chitaetsya', 'outline unreadable before or after'),
]
AREA_BUCKETS = [(0.001, 'under 0.1%'), (0.01, '0.1% - 1%'),
                (0.1, '1% - 10%'), (None, '10% and more')]
SHIFT_BUCKETS = [(1.0, 'under 1 m'), (10.0, '1 - 10 m'),
                 (100.0, '10 - 100 m'), (None, '100 m and more')]


class BadInput(Exception):
    """Неверный вход: отказ с кодом 2 до того, как что-то записано."""


def open_readonly(path):
    """Соединение, которым нельзя писать.

    [REASON]: `mode=ro` -- запрет на уровне SQLite, а не обещание скрипта:
    любая запись отвечает `attempt to write a readonly database`, а
    отсутствующий файл не создаётся. Тот же вид, что у GPS-12 и GPS-18.
    """
    return sqlite3.connect('file:%s?mode=ro' % path, uri=True, timeout=30)


def read_rows(path):
    """({external_id: (name, geometry, is_active)} зеркала, Counter источников).

    BadInput -- если таблицы `field_contours` нет: пустое сравнение вместо
    отказа сказало бы «ничего не изменилось».
    """
    con = open_readonly(path)
    try:
        present = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='field_contours'").fetchone()
        if not present:
            raise BadInput('table field_contours is missing in %s'
                           % config.ascii_only(path))
        sources = Counter()
        rows = {}
        for source, external_id, name, geometry, active in con.execute(
                'SELECT source, external_id, name, geometry_geojson, '
                'is_active FROM field_contours'):
            sources[source] += 1
            if source == SOURCE:
                rows[str(external_id)] = (name or '', geometry, active)
        return rows, sources
    finally:
        con.close()


def ring_of(text):
    """Внешнее кольцо [(lon, lat), ...] без замыкающей точки, или None.

    None -- геометрии нет; ValueError -- она не читается как многоугольник.
    """
    if text is None:
        return None
    shape = json.loads(text)
    if not isinstance(shape, dict) or shape.get('type') != 'Polygon':
        raise ValueError('not a Polygon')
    coordinates = shape.get('coordinates') or []
    if not coordinates:
        raise ValueError('no ring')
    ring = [(float(point[0]), float(point[1])) for point in coordinates[0]]
    if len(ring) > 1 and ring[0] == ring[-1]:
        ring = ring[:-1]
    if len(ring) < 3:
        raise ValueError('ring of %d point(s)' % len(ring))
    return ring


def rounded(ring):
    return [(round(lon, DIGITS), round(lat, DIGITS)) for lon, lat in ring]


def same_cycle(first, second):
    """Одно ли кольцо: тот же круг точек с другого начала или в другую сторону.

    [REASON]: сдвиг пробуется только с тех мест, где стоит первая точка, -- у
    контура в тысячу точек перебор всех начал на 16 тысячах зон занял бы часы.
    """
    if len(first) != len(second) or not first:
        return False
    for candidate in (second, second[::-1]):
        for start, point in enumerate(candidate):
            if point == first[0] and candidate[start:] + candidate[:start] == first:
                return True
    return False


def projected(ring, lat0):
    """Кольцо в метрах: местная равнопромежуточная проекция у широты lat0."""
    scale = math.cos(math.radians(lat0))
    return [(math.radians(lon) * EARTH_RADIUS_M * scale,
             math.radians(lat) * EARTH_RADIUS_M) for lon, lat in ring]


def area_ha(ring, lat0):
    """Площадь кольца по формуле шнурка в проекции у lat0, га."""
    points = projected(ring, lat0)
    total = 0.0
    for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0 / 10000.0


def centre(ring, lat0):
    points = projected(ring, lat0)
    return (sum(x for x, _ in points) / len(points),
            sum(y for _, y in points) / len(points))


def compare_geometry(before, after):
    """(вид, площадь до, площадь после, сдвиг центра м) для двух текстов."""
    if before == after:
        return 'tot_zhe_tekst', None, None, None
    try:
        old, new = ring_of(before), ring_of(after)
    except (ValueError, TypeError, IndexError, KeyError):
        return 'ne_chitaetsya', None, None, None
    if old is None:
        return 'poyavilsya', None, None, None
    if new is None:
        return 'propal', None, None, None
    old_r, new_r = rounded(old), rounded(new)
    if old_r == new_r:
        return 'te_zhe_koordinaty', None, None, None
    if sorted(old_r) == sorted(new_r):
        if same_cycle(old_r, new_r):
            return 'to_zhe_koltso', None, None, None
        return 'te_zhe_tochki', None, None, None
    # [REASON]: одна широта проекции на обе стороны -- иначе разница площадей
    # и сдвиг вобрали бы разницу проекций, а не контуров.
    lat0 = sum(lat for _, lat in old) / len(old)
    (x1, y1), (x2, y2) = centre(old, lat0), centre(new, lat0)
    return ('drugie_tochki', area_ha(old, lat0), area_ha(new, lat0),
            math.hypot(x2 - x1, y2 - y1))


def compare_names(before, after):
    """(изменилось, прежнее внутри нового, только пробелы)."""
    if before == after:
        return False, False, False
    inside = bool(before.strip()) and before.strip() in after
    spaces = ' '.join(before.split()) == ' '.join(after.split())
    return True, inside, spaces


def compare(before_rows, after_rows):
    """Строки CSV по изменившимся, новым и пропавшим зонам и сводка."""
    summary = Counter()
    geometry = Counter()
    area_change = Counter()
    shift = Counter()
    out = []

    def number(value, digits):
        return '' if value is None else round(value, digits)

    for key in sorted(set(before_rows) | set(after_rows),
                      key=lambda k: (len(k), k)):
        old = before_rows.get(key)
        new = after_rows.get(key)
        if old is None:
            summary['novaya'] += 1
            out.append({'external_id': key, 'izmenenie': 'novaya',
                        'tochek_posle': _points(new[1]),
                        'is_active_posle': new[2], 'imya_posle': new[0]})
            continue
        if new is None:
            summary['propala'] += 1
            out.append({'external_id': key, 'izmenenie': 'propala',
                        'tochek_do': _points(old[1]),
                        'is_active_do': old[2], 'imya_do': old[0]})
            continue
        renamed, inside, spaces = compare_names(old[0], new[0])
        kind, old_area, new_area, moved = compare_geometry(old[1], new[1])
        reshaped = kind != 'tot_zhe_tekst'
        toggled = old[2] != new[2]
        if not (renamed or reshaped or toggled):
            summary['bez_izmeneniy'] += 1
            continue
        what = '+'.join(label for label, flag in (
            ('imya', renamed), ('geometriya', reshaped),
            ('is_active', toggled)) if flag)
        summary[what] += 1
        summary['izmenena'] += 1
        if renamed:
            summary['imya_vsego'] += 1
            summary['imya_vnutri_novogo'] += inside
            summary['imya_tolko_probely'] += spaces
        if reshaped:
            geometry[kind] += 1
        if kind == 'drugie_tochki':
            area_change[_bucket(abs(new_area - old_area) / old_area
                                if old_area > 0 else None, AREA_BUCKETS)] += 1
            shift[_bucket(moved, SHIFT_BUCKETS)] += 1
        out.append({
            'external_id': key, 'izmenenie': what,
            'imya': 'da' if renamed else 'net',
            'imya_vnutri_novogo': 'da' if inside else ('net' if renamed else ''),
            'imya_tolko_probely': 'da' if spaces else ('net' if renamed else ''),
            'geometriya': kind,
            'ploshchad_do_ga': number(old_area, 4),
            'ploshchad_posle_ga': number(new_area, 4),
            'raznitsa_ga': number(None if old_area is None
                                  else new_area - old_area, 4),
            'sdvig_tsentra_m': number(moved, 1),
            'tochek_do': _points(old[1]), 'tochek_posle': _points(new[1]),
            'is_active_do': old[2], 'is_active_posle': new[2],
            'imya_do': old[0], 'imya_posle': new[0]})
    return out, summary, geometry, area_change, shift


def _points(text):
    try:
        ring = ring_of(text)
    except (ValueError, TypeError, IndexError, KeyError):
        return ''
    return '' if ring is None else len(ring)


def _bucket(value, buckets):
    if value is None:
        return buckets[-1][1]
    for limit, label in buckets:
        if limit is None or value < limit:
            return label
    return buckets[-1][1]


def write_csv(path, rows):
    with open(path, 'w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.writer(handle, delimiter=';')
        writer.writerow(CSV_COLUMNS)
        for row in rows:
            writer.writerow([row.get(column, '') for column in CSV_COLUMNS])


def report(before_rows, after_rows, sources, summary, geometry, area_change,
           shift, log=print):
    log('wialon rows before: %d | after: %d' % (len(before_rows), len(after_rows)))
    log('  only after (added)       : %d' % summary['novaya'])
    log('  only before (gone)       : %d' % summary['propala'])
    log('  unchanged                : %d' % summary['bez_izmeneniy'])
    log('  changed                  : %d' % summary['izmenena'])
    for what in ('imya', 'geometriya', 'imya+geometriya', 'is_active',
                 'imya+is_active', 'geometriya+is_active',
                 'imya+geometriya+is_active'):
        if summary[what]:
            log('    %-24s: %d' % (what.replace('imya', 'name')
                                   .replace('geometriya', 'geometry'),
                                   summary[what]))
    log('name changed               : %d' % summary['imya_vsego'])
    log('  old name inside the new  : %d' % summary['imya_vnutri_novogo'])
    log('  whitespace only          : %d' % summary['imya_tolko_probely'])
    log('geometry changed           : %d' % sum(geometry.values()))
    for kind, text in GEOMETRY_KINDS[1:]:
        if geometry[kind]:
            log('  %-37s: %d' % (text, geometry[kind]))
    if geometry['drugie_tochki']:
        log('  other points, area change :')
        for _, label in AREA_BUCKETS:
            log('    %-14s: %d' % (label, area_change[label]))
        log('  other points, centre shift:')
        for _, label in SHIFT_BUCKETS:
            log('    %-14s: %d' % (label, shift[label]))
    log('rows by source (before -> after, only wialon compared):')
    for source in sorted(set(sources[0]) | set(sources[1]), key=str):
        log('  %-10s: %d -> %d' % (config.ascii_only(source),
                                   sources[0][source], sources[1][source]))


def check_paths(args):
    """BadInput до любого соединения и до записи."""
    for path in (args.before, args.after):
        # [REASON]: база открывается адресом `file:...?mode=ro`; `#`, `?` и `%`
        # SQLite читает как часть адреса и открыл бы другой файл.
        if any(mark in path for mark in '#?%'):
            raise BadInput('the database path %s contains #, ? or %% - move '
                           'or rename the file' % config.ascii_only(path))
        # [REASON]: проверка до соединения: отсутствующая база не создаётся, а
        # пустая база «до» дала бы «все строки новые» вместо отказа.
        if not os.path.isfile(path):
            raise BadInput('database not found at %s - refusing to run'
                           % config.ascii_only(path))
    if os.path.samefile(args.before, args.after):
        raise BadInput('--before and --after are the same file - nothing to '
                       'compare')
    folder = os.path.dirname(os.path.abspath(args.out))
    if not os.path.isdir(folder):
        raise BadInput('folder for %s does not exist'
                       % config.ascii_only(args.out))
    protected = []
    for path in (args.before, args.after):
        protected += [path, path + '-wal', path + '-shm']
    # [REASON]: CSV пишется режимом 'w'; опечатка -- путь базы на месте --out
    # -- заменила бы базу таблицей. Любой файл SQLite тоже защищён.
    clash = out_clash(args.out, protected)
    if clash:
        raise BadInput('--out points at %s - refusing to overwrite it'
                       % config.ascii_only(clash))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=config.ascii_only(__doc__.splitlines()[0]))
    parser.add_argument('--before', required=True,
                        help='copy of transport.db taken before the mirror wrote')
    parser.add_argument('--after', required=True,
                        help='transport.db after the mirror wrote')
    parser.add_argument('--out', default='gps_mirror_diff.csv')
    args = parser.parse_args(argv)
    try:
        check_paths(args)
        before_rows, before_sources = read_rows(args.before)
        after_rows, after_sources = read_rows(args.after)
    except BadInput as problem:
        sys.stderr.write('ERROR: %s\n' % problem)
        return EXIT_BAD_INPUT
    except sqlite3.DatabaseError as problem:
        sys.stderr.write('ERROR: cannot read a database (%s)\n'
                         % type(problem).__name__)
        return EXIT_BAD_INPUT
    rows, summary, geometry, area_change, shift = compare(before_rows,
                                                          after_rows)
    print('before: %s' % config.ascii_only(args.before))
    print('after : %s' % config.ascii_only(args.after))
    report(before_rows, after_rows, (before_sources, after_sources), summary,
           geometry, area_change, shift)
    write_csv(args.out, rows)
    print('')
    print('written: %s (%d row(s))' % (config.ascii_only(args.out), len(rows)))
    print('nothing was written to either database: both were opened mode=ro')
    return EXIT_OK


if __name__ == '__main__':
    sys.exit(main())
