# -*- coding: utf-8 -*-
"""GPS: точки выбранных машино-суток и контуры -- для стенда сессии. Только чтение.

ЗАЧЕМ
Первое правило края поля отвергнуто прогоном 10-11.10 (дорожная карта, 2.12,
«Прогон на данных»). Владелец 11.10 выбрал второй круг: стенд дополняется
настоящими треками -- теми сутками, где первое правило ошиблось, и сутками с
ручными замерами (набор В-5). Точки лежат только на сервере, поэтому этот
инструмент выгружает их в файлы, которые владелец присылает в чат.

ЧТО ПИШЕТ
  --out-points  CSV вида выгрузок зондов Wialon: `unit_id;date;time;lat;lon;
                speed`, дата и время местные (часовой пояс сборщика). Файл
                читается тем же `tools/gps_alpha_replay.load_csv_days`, что и
                наборы 27.07 и 12.08. Все точки суток, не только рабочие:
                окно скорости стенд применяет сам, как движок.
  --out-zones   GeoJSON FeatureCollection выбранных контуров зеркала
                справочника (`field_contours`, source='wialon'); у каждого --
                номер зоны Wialon и имя.
  --out-labels  CSV участков с ответом оператора («работа» / «проезд»): сутки,
                объект, номер участка, гектары, ответ и многоугольник
                участка (GeoJSON). Единственная разметка людей по участкам --
                стенду она нужна как есть.
Машино-сутки задаются ключом --day ГГГГ-ММ-ДД:ID (повторяется), набором
приёмки --set CSV (столбцы day и wialon_id, как в
`docs/gps_v5_acceptance_set_2026-10-09.csv`) и ключом --labelled: все сутки,
где у участка есть ответ оператора (их точки -- в --out-points, участки -- в
--out-labels). Контуры -- ключом --zone НОМЕР (повторяется).

[REASON]: база и файлы точек открываются `mode=ro`; пишутся только два файла,
названные ключами, и только если их папка уже есть -- иначе отказ с кодом 2 до
любого чтения. Файлы содержат треки машин и имена контуров -- данные
холдинга; в репозиторий они не кладутся, только в чат сессии.

Код выхода: 0 -- записано; 3 -- записано, но у части суток точек на диске
нет (они названы); 2 -- неверный ввод, ничего не записано.

Запуск -- системным Python (геометрия не нужна), PowerShell, из C:\\gps-tools:

    & "C:\\Program Files\\Python314\\python.exe" tools\\gps_bench_export.py --db C:\\transport-report\\instance\\transport.db --dir C:\\transport-report\\instance --set docs\\gps_v5_acceptance_set_2026-10-09.csv --out-points C:\\gps-tools\\check\\bench_points.csv

    & "C:\\Program Files\\Python314\\python.exe" tools\\gps_bench_export.py --db C:\\transport-report\\instance\\transport.db --dir C:\\transport-report\\instance --labelled --out-labels C:\\gps-tools\\check\\bench_labels.csv --out-points C:\\gps-tools\\check\\bench_labelled_points.csv

Вывод в консоль -- ASCII.
"""

import argparse
import csv
import json
import os
import sqlite3
import sys
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from gps_collector import config as collector_config               # noqa: E402
from tools.gps_day_kml import parse_pair                           # noqa: E402
from tools.gps_implausible_sites import read_day_readonly          # noqa: E402
from tools.gps_track_only_days import console, open_readonly       # noqa: E402

POINT_COLUMNS = ('unit_id', 'date', 'time', 'lat', 'lon', 'speed')
LABEL_COLUMNS = ('day', 'wialon_id', 'site_number', 'area_ha', 'label',
                 'polygon_geojson')
ZONE_SOURCE = 'wialon'


class BadInput(Exception):
    pass


def set_days(path):
    """Машино-сутки набора приёмки: [(день, объект)] из столбцов day и wialon_id."""
    with open(path, encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        absent = [name for name in ('day', 'wialon_id')
                  if name not in (reader.fieldnames or [])]
        if absent:
            raise BadInput('%s has no column(s) %s'
                           % (console(os.path.basename(path)), ', '.join(absent)))
        pairs = []
        for line, row in enumerate(reader, 2):
            pair = parse_pair('%s:%s' % ((row['day'] or '').strip(),
                                         (row['wialon_id'] or '').strip()))
            if pair is None:
                raise BadInput('%s line %d: no day or wialon_id'
                               % (console(os.path.basename(path)), line))
            pairs.append(pair)
    return pairs


def labelled_days(con):
    """[(день, объект)] суток, где у участка есть ответ оператора."""
    return [(str(day), int(unit)) for day, unit in con.execute(
        'SELECT DISTINCT work_date, wialon_id FROM gps_work_polygons '
        'WHERE operator_label IS NOT NULL ORDER BY work_date, wialon_id')]


def write_labels(con, path, out):
    """Участки с ответом оператора в CSV. Возвращает число строк."""
    rows = con.execute(
        'SELECT work_date, wialon_id, site_number, area_ha, operator_label, '
        'polygon_geojson FROM gps_work_polygons WHERE operator_label IS NOT NULL '
        'ORDER BY work_date, wialon_id, site_number').fetchall()
    with open(path, 'w', encoding='utf-8', newline='') as handle:
        writer = csv.writer(handle, lineterminator='\n')
        writer.writerow(LABEL_COLUMNS)
        writer.writerows(rows)
    out('  labelled sites written %d' % len(rows))
    return len(rows)


def local_clock(t):
    """(дата, время) местные -- как у суток расчёта и у выгрузок зондов."""
    moment = datetime.fromtimestamp(int(t), collector_config.TZ)
    return moment.strftime('%Y-%m-%d'), moment.strftime('%H:%M:%S')


def write_points(folder, pairs, path, out):
    """Точки суток в CSV. Возвращает список суток без точек на диске."""
    gone = []
    with open(path, 'w', encoding='utf-8', newline='') as handle:
        writer = csv.writer(handle, delimiter=';', lineterminator='\n')
        writer.writerow(POINT_COLUMNS)
        for day, unit in pairs:
            points = read_day_readonly(folder, unit, day)
            if not points:
                gone.append((day, unit))
                out('  %s unit %d: no points on disk' % (day, unit))
                continue
            for t, lon, lat, speed, _sats in points:
                date, clock = local_clock(t)
                writer.writerow((unit, date, clock, '%.7f' % lat, '%.7f' % lon,
                                 '%g' % speed))
            out('  %s unit %d: %d points' % (day, unit, len(points)))
    return gone


def write_zones(con, zones, path, out):
    """Контуры зеркала в GeoJSON. Возвращает номера зон, которых нет."""
    found, features = set(), []
    marks = ','.join('?' * len(zones))
    for external_id, name, geometry in con.execute(
            'SELECT external_id, name, geometry_geojson FROM field_contours '
            'WHERE source = ? AND external_id IN (%s) ORDER BY external_id'
            % marks, [ZONE_SOURCE] + [str(zone) for zone in zones]):
        try:
            shape = json.loads(geometry or '')
        except ValueError:
            shape = None
        if not isinstance(shape, dict):
            out('  zone %s: geometry not readable, skipped' % external_id)
            continue
        found.add(str(external_id))
        features.append({'type': 'Feature', 'geometry': shape,
                         'properties': {'zone_id': str(external_id),
                                        'name': name}})
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump({'type': 'FeatureCollection', 'features': features}, handle,
                  ensure_ascii=False)
    missing = [str(zone) for zone in zones if str(zone) not in found]
    out('  zones written %d, missing %d%s'
        % (len(features), len(missing),
           (': ' + ', '.join(missing)) if missing else ''))
    return missing


def _refuse(message):
    sys.stderr.write('ERROR: %s\n' % message)
    return 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--db', required=True, help='path to transport.db')
    parser.add_argument('--dir', help='folder with gps_points_YYYYMM.db files')
    parser.add_argument('--day', action='append', default=[],
                        help='YYYY-MM-DD:WIALON_ID, may repeat')
    parser.add_argument('--set', help='acceptance set csv (day, wialon_id)')
    parser.add_argument('--zone', action='append', default=[],
                        help='Wialon zone number of the contour, may repeat')
    parser.add_argument('--labelled', action='store_true',
                        help='every machine-day with an operator answer')
    parser.add_argument('--out-points', help='CSV file for the points')
    parser.add_argument('--out-zones', help='GeoJSON file for the contours')
    parser.add_argument('--out-labels', help='CSV file for the labelled sites')
    args = parser.parse_args(argv)

    if not os.path.isfile(args.db):
        return _refuse('database not found: %s' % console(args.db))
    pairs = []
    for text in args.day:
        pair = parse_pair(text)
        if pair is None:
            return _refuse('--day must look like YYYY-MM-DD:WIALON_ID')
        pairs.append(pair)
    if args.set:
        if not os.path.isfile(args.set):
            return _refuse('not found: %s' % console(args.set))
        try:
            pairs += set_days(args.set)
        except BadInput as error:
            return _refuse(error)
    zones = list(dict.fromkeys(z.strip() for z in args.zone if z.strip()))
    if not pairs and not zones and not args.labelled:
        return _refuse('nothing to export: give --day, --set, --labelled or --zone')
    if args.labelled and not (args.out_labels and args.out_points):
        return _refuse('--labelled needs --out-labels and --out-points')
    if pairs and not args.out_points:
        return _refuse('machine-days need --out-points')
    if zones and not args.out_zones:
        return _refuse('--zone needs --out-zones')
    if (pairs or args.labelled) and (not args.dir or not os.path.isdir(args.dir)):
        return _refuse('machine-days need --dir (an existing points folder)')
    for path in (args.out_points if pairs or args.labelled else None,
                 args.out_zones if zones else None,
                 args.out_labels if args.labelled else None):
        if path and not os.path.isdir(os.path.dirname(os.path.abspath(path))):
            return _refuse('the folder for %s does not exist' % console(path))

    if args.labelled:
        con = open_readonly(args.db)
        try:
            print('labelled sites -> %s' % console(args.out_labels))
            write_labels(con, args.out_labels, print)
            pairs += labelled_days(con)
        finally:
            con.close()
    pairs = list(dict.fromkeys(pairs))
    gone = []
    if pairs:
        print('points of %d machine-day(s) -> %s' % (len(pairs),
                                                   console(args.out_points)))
        gone = write_points(args.dir, pairs, args.out_points, print)
    if zones:
        print('%d contour(s) -> %s' % (len(zones), console(args.out_zones)))
        con = open_readonly(args.db)
        try:
            write_zones(con, zones, args.out_zones, print)
        finally:
            con.close()
    print('read-only: the database and the point files were opened mode=ro; '
          'written only the files named above')
    return 3 if gone else 0


if __name__ == '__main__':
    sys.exit(main())
