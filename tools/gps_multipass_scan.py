# -*- coding: utf-8 -*-
"""GPS: кратность прохода -- признаки двойного прохода по суткам периода. Только чтение.

ЗАЧЕМ
Владелец 13.10.2026 (дорожная карта, §2.14): двойной проход в одни сутки --
гектары обработки вдвое больше площади; «надо научиться определять двойные
проходы». Разведка на стенде нашла признак -- «повторный ход»
(`gps/multipass.py`), но подтверждённый двойной проход в одни сутки на стенде
один: борона вдоль и поперёк 09.10. Для правила нужны примеры, размеченные
владельцем. Инструмент проходит по всем опубликованным суткам периода,
меряет признаки и отбирает сутки для глаза владельца -- KML и шаблон
ответов, как в В-6. Владелец 13.10 (вечер): «стоит смотреть весь месяц», --
поэтому в CSV у каждых суток есть и участки A7: по ним сессия смотрит,
проходит ли техника те же места в другие сутки.

ЧТО СЧИТАЕТ
Опубликованные машино-сутки периода (`gps_daily_aggregates`, причина пуста)
с точками на диске. Не берутся объекты, исключённые владельцем («не наша»),
и спецтехника: их гектары в план-факт не идут. По каждым суткам: участки A7
-- `gps.area.work_sites` по точкам, как в ночном расчёте, -- и признаки
`gps.multipass.measure_day`. Контроль: площади участков против строк базы,
если строку записала та же версия метода; иначе -- «другая версия».

ЧТО ПИШЕТ -- только файлы, названные ключами, и только если их папка есть
  --csv PATH      все сутки: признаки, контроль, как сутки отобраны, номер
                  папки KML и участки A7 (GeoJSON, градусы) -- UTF-8;
  --kml PATH      отобранные сутки: участки A7 и трек. На каждом из
                  SITE_COLOURED крупнейших участков рабочий трек -- четырьмя
                  цветами по времени на этом участке, от тёмного к светлому:
                  второй проход по тому же полю виден другим цветом, даже
                  если всё поле заняло час. Рабочий трек вне них -- зелёным,
                  трек вне работы -- серым. Двойной щелчок по папке приводит
                  к участкам (<LookAt>);
  --answers PATH  шаблон ответов владельца -- строка на папку KML, столбец
                  ответа пуст. UTF-8 с BOM: Excel открывает его кириллицей.

ОТБОР В KML -- не правило, а выборка для глаза владельца. Годятся сутки с
участками не меньше PICK_MIN_HA и ходом не меньше PICK_MIN_KM.
  strongest -- --top суток с наибольшим из двух признаков (повторный ход,
               два направления) не ниже PICK_LEVEL;
  above     -- --spread суток выше уровня из остальных, первые по sha1
               «ГГГГ-ММ-ДД:id»: случайная, а не лучшая часть кандидатов;
  below     -- --sample суток ниже уровня, первые по sha1. Эти сутки
               признак не выбирает, поэтому только по ним видно, сколько
               двойных проходов он пропускает.
  Не больше PER_UNIT_MAX суток одного объекта в отборе выше уровня: иначе
  одна машина, работающая каждый день одинаково, заняла бы весь KML.
  «Возврат по тем же рядам» в отбор не входит: у вспашки соседний проход
  ложится ближе 1,5 м, и признак был бы занят пахотой (§2.14).

РАЗМЕТКА ВСЛЕПУЮ
[REASON]: ответы владельца станут эталоном правила, а эталон не должен
знать, что о сутках думает признак. Поэтому в KML и в шаблоне ответов нет
ни чисел признаков, ни того, как сутки отобраны, а папки идут в порядке
sha1, где отобранные признаком и контрольные перемешаны. Всё это есть в CSV
и в столбцах `picked_as`, `kml_folder`, -- для сессии. Консоль печатает
только числа по группам, без списка папок.

Код выхода: 0 -- записано; 3 -- записано, но на части суток расчёт упал
(они названы в выводе и в CSV не попали); 2 -- неверный ввод, ничего не
считалось; 1 -- инструмент упал (необработанное исключение).

[REASON]: база и файлы точек открываются `mode=ro`, пишутся только
названные файлы. Решения здесь нет: кратность в расчёт не входит, гектары
не меняются. Оговорка та же, что у прогонов края: SQLite, открывая базу в
режиме WAL только на чтение, может создать рядом пустые служебные файлы
`-wal` и `-shm`; данные не меняются.

Запуск -- окружением расчёта (нужна геометрия), PowerShell, из C:\\gps-tools:

    & C:\\gps_venv\\Scripts\\python.exe -u tools\\gps_multipass_scan.py --db C:\\transport-report\\instance\\transport.db --dir C:\\transport-report\\instance --since 2026-09-01 --until 2026-10-13 --csv C:\\gps-tools\\check\\multipass_scan.csv --kml C:\\gps-tools\\check\\multipass_scan.kml --answers C:\\gps-tools\\check\\multipass_answers.csv *> C:\\gps-tools\\check\\multipass_scan.log

Период в шесть недель -- часы (строка хода каждые 200 машино-суток),
поэтому вывод пишется в файл. Вывод -- ASCII; кириллица -- только в CSV и
KML.
"""

import argparse
import csv
import hashlib
import json
import math
import os
import sys
import time
from datetime import datetime
from xml.sax.saxutils import escape

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from shapely.geometry import Point, shape                           # noqa: E402
from shapely.prepared import prep                                   # noqa: E402

from gps.area import METHOD_VERSION, work_sites                     # noqa: E402
from gps.daily import polygon_geojson                               # noqa: E402
from gps.multipass import measure_day                               # noqa: E402
from gps_collector import config as collector_config                # noqa: E402
from tools.gps_alpha_replay import published_days                   # noqa: E402
from tools.gps_day_kml import (STYLES, _coordinates, _polygon_kml,  # noqa: E402
                               number, polygons_of)
from tools.gps_implausible_sites import (SITE_GAP_CAP_S,           # noqa: E402
                                         SPEED_MAX_KMH, SPEED_MIN_KMH,
                                         kinds_of, read_day_readonly)
from tools.gps_track_only_days import (console, open_readonly,     # noqa: E402
                                       unit_names)

PICK_LEVEL = 0.15
PICK_MIN_HA = 1.0
PICK_MIN_KM = 2.0
PER_UNIT_MAX = 2
DEFAULT_TOP = 5
DEFAULT_SPREAD = 10
DEFAULT_SAMPLE = 10
PROGRESS_EVERY = 200
FAILED_SHOWN = 10
# [REASON]: объекты, чьи гектары в план-факт не идут: «не наша» -- решение
# владельца, спецтехника -- след без гектаров. Их сутки владельцу в разметку
# не нужны, а признаки по ним только шумели бы в отборе.
SKIPPED_KINDS = ('excluded', 'special')
GROUPS = ('strongest', 'above', 'below')
SITE_COLOURED = 6
LOOK_MIN_M = 300.0
LOOK_FACTOR = 2.5

# Рабочий трек на участке -- четыре куска по времени, от тёмного к
# светлому (KML: aabbggrr), как на картинке разведки 13.10.
TIME_STYLES = (('t1', 'ff752448'), ('t2', 'ff8e782a'), ('t3', 'ff71bf43'),
               ('t4', 'ff18e4e2'))

CSV_COLUMNS = ('day', 'wialon_id', 'machine', 'kind', 'a7_ha', 'stored_ha',
               'stored_version', 'control', 'sites', 'track_km', 'km_per_ha',
               'repeat', 'returns', 'same_rows', 'two_dirs', 'two_dirs_angle',
               'picked_as', 'kml_folder', 'sites_geojson')
ANSWER_COLUMNS = ('folder', 'day', 'wialon_id', 'machine', 'owner_answer',
                  'owner_note')


class BadInput(Exception):
    """Ключи нельзя выполнить -- код выхода 2, ничего не считалось."""


def stored_areas(con, day, unit):
    """(площади участков строки базы по убыванию, версия метода строки)."""
    areas = [round(float(area), 4) for (area,) in con.execute(
        'SELECT area_ha FROM gps_work_polygons WHERE work_date = ? '
        'AND wialon_id = ? ORDER BY area_ha DESC', (day, unit))]
    row = con.execute('SELECT method_version FROM gps_daily_aggregates '
                      'WHERE work_date = ? AND wialon_id = ?', (day, unit)).fetchone()
    return areas, (row[0] if row else None)


def sites_geojson(sites):
    """Участки A7 суток -- JSON-список геометрий GeoJSON в градусах."""
    return '[%s]' % ','.join(polygon_geojson(site.polygon) for site in sites)


def scan_day(con, folder, day, unit):
    """Строка CSV одних суток; None -- точек на диске нет."""
    points = read_day_readonly(folder, unit, day)
    if not points:
        return None
    points = sorted(points)
    sites, _ = work_sites(points)
    measured = measure_day(points, sites)
    areas, version = stored_areas(con, day, unit)
    if version != METHOD_VERSION:
        control = 'other version'
    elif sorted((round(site.area_ha, 4) for site in sites), reverse=True) == areas:
        control = 'ok'
    else:
        control = 'MISMATCH'
    row = {'day': day, 'wialon_id': unit, 'stored_ha': round(sum(areas), 4),
           'stored_version': version or '', 'control': control,
           'sites_geojson': sites_geojson(sites)}
    row.update(measured)
    return row


def score(row):
    """Больший из двух признаков отбора."""
    return max(row['repeat'], row['two_dirs'])


def eligible(row):
    return (row['kind'] not in SKIPPED_KINDS and row['repeat'] is not None
            and row['a7_ha'] >= PICK_MIN_HA and row['track_km'] >= PICK_MIN_KM)


def sha_key(row):
    return hashlib.sha1(('%s:%d' % (row['day'], row['wialon_id']))
                        .encode('ascii')).hexdigest()


def pick(rows, top, spread, sample):
    """{(день, объект): группа} -- сутки для KML, в порядке групп отбора."""
    pool = [row for row in rows if eligible(row)]
    above = [row for row in pool if score(row) >= PICK_LEVEL]
    below = [row for row in pool if score(row) < PICK_LEVEL]
    picked, per_unit = {}, {}

    def take(candidates, count, group, capped):
        for row in candidates:
            if count <= 0:
                return
            key = (row['day'], row['wialon_id'])
            if key in picked:
                continue
            if capped and per_unit.get(row['wialon_id'], 0) >= PER_UNIT_MAX:
                continue
            picked[key] = group
            if capped:
                per_unit[row['wialon_id']] = per_unit.get(row['wialon_id'], 0) + 1
            count -= 1

    take(sorted(above, key=lambda row: (-score(row), row['day'], row['wialon_id'])),
         top, 'strongest', True)
    take(sorted(above, key=sha_key), spread, 'above', True)
    take(sorted(below, key=sha_key), sample, 'below', False)
    return picked


def folder_order(rows, picked):
    """Строки отобранных суток в порядке папок KML: по sha1, группы вперемешку."""
    return sorted((row for row in rows if (row['day'], row['wialon_id']) in picked),
                  key=sha_key)


# --- KML ---------------------------------------------------------------------

def local_clock(t):
    return datetime.fromtimestamp(t, collector_config.TZ).strftime('%H:%M')


def site_finder(row):
    """(lon, lat) -> номер участка суток (0 -- крупнейший) или None.

    Свой цвет времени получают только SITE_COLOURED крупнейших участков: у
    суток опрыскивания участков бывает больше десятка, и на каждом по четыре
    метки засыпали бы список Google Earth.
    """
    sites = [prep(shape(geometry)) for geometry
             in json.loads(row['sites_geojson'])[:SITE_COLOURED]]

    def which(lon, lat):
        point = Point(lon, lat)
        for index, site in enumerate(sites):
            if site.contains(point):
                return index
        return None
    return which


def time_pieces(points, which):
    """([(ключ, [(lon, lat), ...])], {ключ: подпись}) трека одних суток.

    Ключ рабочей точки внутри цветного участка -- (участок, четверть): у
    каждого участка свои четверти времени, от его первой рабочей точки до
    последней. Так на поле, пройденном дважды, второй проход другого цвета,
    даже если всё поле заняло час. Рабочие точки вне цветных участков --
    'work', остальное -- 'move'. Смена ключа начинает новый кусок с последней
    точки прежнего, чтобы линия не рвалась; промежуток длиннее
    `SITE_GAP_CAP_S` рвёт её, как у `gps_day_kml.track_pieces`.
    """
    quarters = len(TIME_STYLES)
    located = []
    spans = {}
    for row in points:
        t, lon, lat, speed = row[0], row[1], row[2], row[3]
        site = (which(lon, lat) if SPEED_MIN_KMH <= speed <= SPEED_MAX_KMH
                else 'move')
        if site is None:
            site = 'work'
        if isinstance(site, int):
            first, last = spans.get(site, (t, t))
            spans[site] = (min(first, t), max(last, t))
        located.append((t, lon, lat, site))
    pieces, current, previous_t = [], None, None
    for t, lon, lat, site in located:
        if isinstance(site, int):
            first, last = spans[site]
            span = max(last - first, 1.0)
            key = (site, min(int((t - first) / span * quarters), quarters - 1))
        else:
            key = site
        gap = previous_t is not None and t - previous_t > SITE_GAP_CAP_S
        if current is None or gap or current[0] != key:
            begin = [] if (current is None or gap) else [current[1][-1]]
            current = (key, begin + [(lon, lat)])
            pieces.append(current)
        else:
            current[1].append((lon, lat))
        previous_t = t
    names = {'work': 'Трек в работе вне участков / участкалардан ташқари иш '
                     'тезлигидаги трек',
             'move': 'Трек вне работы / ишдан ташқари трек'}
    for site, (first, last) in spans.items():
        span = max(last - first, 1.0)
        for quarter in range(quarters):
            clock = '%s–%s' % (local_clock(first + span * quarter / quarters),
                               local_clock(first + span * (quarter + 1) / quarters))
            names[(site, quarter)] = ('Участок %d, трек %s / участка %d, трек %s'
                                      % (site + 1, clock, site + 1, clock))
    return [piece for piece in pieces if len(piece[1]) >= 2], names


def look_at(row):
    """<LookAt> на участки суток: двойной щелчок по папке -- сразу поле."""
    xs, ys = [], []
    for geometry in json.loads(row['sites_geojson']):
        for rings in polygons_of(json.dumps(geometry)):
            for lon, lat in rings[0] if rings else []:
                xs.append(float(lon))
                ys.append(float(lat))
    if not xs:
        return ''
    lon, lat = 0.5 * (min(xs) + max(xs)), 0.5 * (min(ys) + max(ys))
    # метры на градус у широты участка; размах -- больший из двух
    extent = max((max(xs) - min(xs)) * 111320.0 * math.cos(math.radians(lat)),
                 (max(ys) - min(ys)) * 111132.0)
    return ('<LookAt><longitude>%.6f</longitude><latitude>%.6f</latitude>'
            '<altitude>0</altitude><heading>0</heading><tilt>0</tilt>'
            '<range>%.0f</range></LookAt>' % (lon, lat, max(LOOK_MIN_M,
                                                            LOOK_FACTOR * extent)))


def folder_kml(number_, row, points):
    day = datetime.strptime(row['day'], '%Y-%m-%d').strftime('%d.%m.%Y')
    title = '%d. %s · %s' % (number_, row['machine'], day)
    description = (
        'Участки A7 / A7 участкалари: %s га. Вопрос: прошла ли машина те же '
        'места дважды за эти сутки? / Савол: машина бир кунда ўша жойлардан '
        'икки марта ўтдими? Ответ — в шаблон, строка %d / жавоб — шаблонга, '
        '%d-қатор. Цвет трека — время на участке: от тёмного (раньше) к '
        'светлому (позже) / трек ранги — участкадаги вақт: тўқдан (эртароқ) '
        'очга (кечроқ).' % (number(row['a7_ha'], 2), number_, number_))
    parts = ['<Folder><name>%s</name><description>%s</description>%s'
             % (escape(title), escape(description), look_at(row))]
    for index, geometry in enumerate(json.loads(row['sites_geojson']), 1):
        shapes = [_polygon_kml(rings)
                  for rings in polygons_of(json.dumps(geometry))]
        shapes = [shape_ for shape_ in shapes if shape_]
        if not shapes:
            continue
        body = (shapes[0] if len(shapes) == 1 else
                '<MultiGeometry>%s</MultiGeometry>' % ''.join(shapes))
        parts.append('<Placemark><name>%s</name><styleUrl>#site</styleUrl>%s'
                     '</Placemark>' % (escape('Участок / участка %d' % index), body))
    pieces, names = time_pieces(points or [], site_finder(row))
    keys = sorted({key for key, _line in pieces if isinstance(key, tuple)})
    for key in keys + ['work', 'move']:
        lines = ''.join('<LineString><tessellate>1</tessellate><coordinates>%s'
                        '</coordinates></LineString>' % _coordinates(line)
                        for piece_key, line in pieces if piece_key == key)
        if not lines:
            continue
        style = TIME_STYLES[key[1]][0] if isinstance(key, tuple) else key
        parts.append('<Placemark><name>%s</name><styleUrl>#%s</styleUrl>'
                     '<MultiGeometry>%s</MultiGeometry></Placemark>'
                     % (escape(names[key]), style, lines))
    parts.append('</Folder>')
    return ''.join(parts)


def kml_document(folders):
    styles = [(key, body) for key, body in STYLES
              if key in ('site', 'work', 'move')]
    styles += [(key, '<LineStyle><color>%s</color><width>2</width></LineStyle>'
                % color) for key, color in TIME_STYLES]
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>%s</name>'
            '%s%s</Document></kml>\n'
            % (escape('Двойной проход: сутки для разметки / икки марта ўтиш: '
                      'белгилаш учун кунлар'),
               ''.join('<Style id="%s">%s</Style>' % item for item in styles),
               ''.join(folders)))


# --- запуск --------------------------------------------------------------------

def _date(option, value):
    try:
        return datetime.strptime(value, '%Y-%m-%d').strftime('%Y-%m-%d')
    except (TypeError, ValueError):
        raise BadInput('%s must look like YYYY-MM-DD' % option)


def check_inputs(args):
    since = _date('--since', args.since)
    until = _date('--until', args.until) if args.until else None
    if until is not None and until < since:
        raise BadInput('--until is before --since')
    if not os.path.isfile(args.db):
        raise BadInput('database not found: %s' % args.db)
    if not os.path.isdir(args.dir):
        raise BadInput('points folder not found: %s' % args.dir)
    outputs = [path for path in (args.csv, args.kml, args.answers) if path]
    if not outputs:
        raise BadInput('nothing to write: give --csv, --kml or --answers')
    for path in outputs:
        parent = os.path.dirname(os.path.abspath(path))
        if not os.path.isdir(parent):
            raise BadInput('folder of %s does not exist' % path)
    if min(args.top, args.spread, args.sample) < 0:
        raise BadInput('--top, --spread and --sample cannot be negative')
    return since, until


def scan(db, folder, since, until, out=print):
    """Все сутки периода: (строки, сутки без точек, пропущено по виду
    объекта, [(день, объект, ошибка)] -- сутки, на которых расчёт упал)."""
    con = open_readonly(db)
    try:
        days = published_days(con, since, until)
        kinds = kinds_of(con, sorted({unit for _day, unit, _version in days}))
        rows, missing, skipped, failed = [], 0, 0, []
        started = time.time()
        for index, (day, unit, _version) in enumerate(days, 1):
            if kinds.get(unit) in SKIPPED_KINDS:
                skipped += 1
                continue
            # [REASON]: прогон идёт часами на боевых данных; исключение
            # геометрии на одних сутках не должно стоить владельцу всего
            # прогона. Такие сутки названы, а код выхода -- 3: записано не всё.
            try:
                row = scan_day(con, folder, day, unit)
            except Exception as error:                      # noqa: BLE001
                failed.append((day, unit, '%s: %s' % (type(error).__name__, error)))
                continue
            if row is None:
                missing += 1
            else:
                row['kind'] = kinds.get(unit)
                rows.append(row)
            if index % PROGRESS_EVERY == 0:
                out('  %d of %d machine-days, %.0f s' % (index, len(days),
                                                         time.time() - started))
        names = unit_names(con, sorted({row['wialon_id'] for row in rows}), folder)
    finally:
        con.close()
    for row in rows:
        row['machine'] = names.get(row['wialon_id']) or str(row['wialon_id'])
    return rows, missing, skipped, failed


def write_csv(path, rows, picked, folders):
    with open(path, 'w', encoding='utf-8', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_COLUMNS)
        for row in rows:
            key = (row['day'], row['wialon_id'])
            row = dict(row, picked_as=picked.get(key, ''),
                       kml_folder=folders.get(key, ''))
            writer.writerow(['' if row.get(column) is None else row[column]
                             for column in CSV_COLUMNS])


def write_answers(path, ordered):
    with open(path, 'w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(ANSWER_COLUMNS)
        for number_, row in enumerate(ordered, 1):
            writer.writerow([number_, row['day'], row['wialon_id'], row['machine'],
                             '', ''])


def report(rows, missing, skipped, failed, picked, out=print):
    out('machine-days measured      : %d' % len(rows))
    out('published, no points       : %d' % missing)
    out('skipped (excluded/special) : %d' % skipped)
    out('failed (not measured)      : %d' % len(failed))
    for day, unit, error in failed[:FAILED_SHOWN]:
        out('  %s %d %s' % (day, unit, console(error)[:160]))
    if len(failed) > FAILED_SHOWN:
        out('  ... and %d more' % (len(failed) - FAILED_SHOWN))
    controls = {}
    for row in rows:
        controls[row['control']] = controls.get(row['control'], 0) + 1
    out('control against the db     : %s' % ', '.join(
        '%s %d' % item for item in sorted(controls.items())))
    pool = [row for row in rows if eligible(row)]
    out('eligible for review (A7 >= %.1f ha, track >= %.1f km): %d'
        % (PICK_MIN_HA, PICK_MIN_KM, len(pool)))
    for level in (PICK_LEVEL, 0.30, 0.50):
        out('  repeat or two directions >= %.2f : %d'
            % (level, sum(1 for row in pool if score(row) >= level)))
    # [REASON]: без списка папок -- разметка вслепую (см. докстринг).
    out('KML folders: %d (%s)' % (len(picked), ', '.join(
        '%s %d' % (group, sum(1 for value in picked.values() if value == group))
        for group in GROUPS)))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--db', required=True, help='path to transport.db')
    parser.add_argument('--dir', required=True, help='folder with gps_points_YYYYMM.db')
    parser.add_argument('--since', required=True, help='first day, YYYY-MM-DD')
    parser.add_argument('--until', help='last day, YYYY-MM-DD')
    parser.add_argument('--csv', help='CSV file for every machine-day')
    parser.add_argument('--kml', help='KML file for the picked machine-days')
    parser.add_argument('--answers', help="CSV template for the owner's answers")
    parser.add_argument('--top', type=int, default=DEFAULT_TOP,
                        help='strongest days above the level (default %d)' % DEFAULT_TOP)
    parser.add_argument('--spread', type=int, default=DEFAULT_SPREAD,
                        help='random days above the level (default %d)' % DEFAULT_SPREAD)
    parser.add_argument('--sample', type=int, default=DEFAULT_SAMPLE,
                        help='random days below the level (default %d)' % DEFAULT_SAMPLE)
    args = parser.parse_args(argv)
    try:
        since, until = check_inputs(args)
    except BadInput as error:
        sys.stderr.write('ERROR: %s\n' % error)
        return 2
    print('multipass scan %s .. %s, method %s' % (since, until or 'today', METHOD_VERSION))
    rows, missing, skipped, failed = scan(args.db, args.dir, since, until)
    picked = pick(rows, args.top, args.spread, args.sample)
    ordered = folder_order(rows, picked)
    folders = {(row['day'], row['wialon_id']): number_
               for number_, row in enumerate(ordered, 1)}
    report(rows, missing, skipped, failed, picked)
    if args.csv:
        write_csv(args.csv, rows, picked, folders)
        print('CSV (%d machine-days): %s' % (len(rows), console(args.csv)))
    if args.kml:
        parts = []
        for number_, row in enumerate(ordered, 1):
            points = read_day_readonly(args.dir, row['wialon_id'], row['day'])
            parts.append(folder_kml(number_, row, sorted(points or [])))
        with open(args.kml, 'w', encoding='utf-8') as handle:
            handle.write(kml_document(parts))
        print('KML (%d folders): %s' % (len(ordered), console(args.kml)))
    if args.answers:
        write_answers(args.answers, ordered)
        print('answers template (%d rows): %s' % (len(ordered), console(args.answers)))
    print('read-only: the database and the point files were opened mode=ro')
    return 3 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
