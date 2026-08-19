# -*- coding: utf-8 -*-
"""GPS-4 -- зеркало справочника контуров Wialon в field_contours.

ЗАЧЕМ
Справочник полей не должен зависеть от чужого хостинга: в Wialon пишет и
модуль конкурента, доступ к серверу интегратора уже пропадал на пять дней, а
счёт заказчику ссылается на контур. Решение G8: контуры зеркалируются к нам,
`source='wialon'`, `external_id` -- id зоны.

ЧТО ЭТОТ СКРИПТ ДЕЛАЕТ И ЧЕГО НЕ ДЕЛАЕТ

  Делает: добавляет новые контуры, обновляет имя и геометрию изменившихся,
          считает дубли имён и пишет их списком.

  НЕ делает: ничего не удаляет и ничего не гасит. Удаления в Wialon к нам не
          каскадируют (G8). Контур, пропавший из Wialon, остаётся у нас как
          был -- на него могут ссылаться уже выставленные счета. Скрипт
          сообщает, сколько таких, и на этом останавливается: что означает
          исчезнувший контур -- вопрос владельца, а не скрипта.

  НЕ считает площадь. `area_ha` остаётся пустым. Площадь контура умеет считать
          метод (`gps/area.py`, проекция UTM 41N, проверено: 42,30 га против
          паспортных 42,16); вторая реализация в этом скрипте дала бы два
          числа для одной вещи, и рано или поздно они разошлись бы.

ТОЛЬКО STDLIB. Пишет через `sqlite3` напрямую, без Flask: `app = create_app()`
вызывает `db.create_all()` на импорте, и любой скрипт с `from app import app`
становится писателем схемы. Таблицу `field_contours` создаёт миграция
`migrate_core_foundation_001.py`; этот скрипт её только наполняет и отказывается
работать, если её нет.

ТОЛЬКО ЧТЕНИЕ на стороне Wialon: `token/login`, `core/search_items`,
`resource/get_zone_data`, `core/logout`.

Запуск (PowerShell, по одной команде на строку):

  cd C:\\transport-report

  & "C:\\Program Files\\Python314\\python.exe" tools\\gps_mirror_contours.py --dry-run

  & "C:\\Program Files\\Python314\\python.exe" tools\\gps_mirror_contours.py

Сначала `--dry-run`: он ничего не пишет и печатает, что изменилось бы.

Вывод в консоль -- ASCII.
"""

import argparse
import csv
import json
import os
import sqlite3
import sys

from collections import Counter, defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gps_collector import config                                   # noqa: E402
from gps_collector.wialon import Client                            # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, 'instance', 'transport.db')
SOURCE = 'wialon'


def ring_from_zone(zone):
    """Замкнутое кольцо [[lon, lat], ...] или None.

    [REASON]: геометрия берётся только у полигонов (тип 2). Окружность (тип 3)
    и линия (тип 1) -- это не полигон, и превратить их в него значит придумать
    геометрию, которой в Wialon нет: у окружности пришлось бы выбрать число
    сторон, у линии -- ширину коридора. Такие зоны зеркалируются как строки
    справочника, но без геометрии, и считаются отдельно.
    """
    if zone.get('t') != config.ZONE_TYPE_POLYGON:
        return None
    points = zone.get('p') or []
    if isinstance(points, dict):
        # [REASON]: сервер отдаёт точки то списком, то словарём с числовыми
        # ключами. Порядок точек -- это форма поля; сортировка по ключу как
        # по строке ставит "10" перед "2" и выворачивает полигон наизнанку.
        points = [points[key] for key in sorted(points, key=lambda k: int(k))]
    ring = [[float(p['x']), float(p['y'])] for p in points
            if isinstance(p, dict) and p.get('x') is not None
            and p.get('y') is not None]
    if len(ring) < 3:
        return None
    if ring[0] != ring[-1]:
        ring.append(list(ring[0]))
    return ring


def geometry_geojson(zone):
    ring = ring_from_zone(zone)
    if ring is None:
        return None
    return json.dumps({'type': 'Polygon', 'coordinates': [ring]},
                      ensure_ascii=False)


def existing_rows(con):
    """external_id -> (id, name, geometry_geojson) для зеркала Wialon."""
    rows = {}
    for row in con.execute(
            'SELECT id, external_id, name, geometry_geojson FROM field_contours '
            'WHERE source = ?', (SOURCE,)):
        rows[str(row[1])] = (row[0], row[2], row[3])
    return rows


def plan(zones, existing):
    """Что изменится. Возвращает (новые, изменённые, без_геометрии, пропавшие)."""
    fresh, changed, geomless = [], [], []
    seen = set()
    for zone in zones:
        zone_id = zone.get('id')
        if zone_id is None:
            continue
        key = str(zone_id)
        seen.add(key)
        name = (zone.get('n') or '').strip() or key
        geometry = geometry_geojson(zone)
        if geometry is None:
            geomless.append(key)
        current = existing.get(key)
        if current is None:
            fresh.append((key, name, geometry))
        elif current[1] != name or current[2] != geometry:
            changed.append((key, name, geometry))
    vanished = sorted(set(existing) - seen)
    return fresh, changed, geomless, vanished


def duplicate_names(zones):
    """Имена, встречающиеся больше одного раза -> [id зон].

    [REASON]: справочник Wialon содержит дубли по конструкции. «1508 Нурхон
    Бобохон» существует двумя зонами -- 4,32 и 8,97 га, -- и поиск по точному
    имени однажды выбрал ту, где трактора не было ни одной точкой. Дубли не
    ошибка импорта, их надо ВИДЕТЬ: контур определяется по треку, а имя только
    называет.
    """
    by_name = defaultdict(list)
    for zone in zones:
        if zone.get('id') is None:
            continue
        by_name[(zone.get('n') or '').strip()].append(int(zone['id']))
    return {name: sorted(ids) for name, ids in by_name.items() if len(ids) > 1}


def apply_plan(con, fresh, changed):
    stamp = datetime.now(config.TZ).strftime('%Y-%m-%d %H:%M:%S')
    con.execute('BEGIN')
    try:
        con.executemany(
            'INSERT INTO field_contours (source, external_id, name, '
            'geometry_geojson, is_active, created_at, updated_at) '
            'VALUES (?, ?, ?, ?, 1, ?, ?)',
            [(SOURCE, key, name, geometry, stamp, stamp)
             for key, name, geometry in fresh])
        con.executemany(
            'UPDATE field_contours SET name = ?, geometry_geojson = ?, '
            'updated_at = ? WHERE source = ? AND external_id = ?',
            [(name, geometry, stamp, SOURCE, key)
             for key, name, geometry in changed])
        con.commit()
    except Exception:
        con.rollback()
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--db', default=DB_PATH)
    parser.add_argument('--resource', type=int, default=config.ZONE_RESOURCE_ID)
    parser.add_argument('--pause', type=float, default=config.PAUSE_S)
    parser.add_argument('--chunk', type=int, default=0,
                        help='ids per request; 0 = all at once, the way the '
                             'probe did it and the only way proven to work')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--duplicates-out', default='gps_contour_duplicates.csv')
    args = parser.parse_args(argv)

    if not os.path.exists(args.db):
        # [REASON]: sqlite3.connect создал бы пустую базу. Пустая база вместо
        # боевой -- это молчаливая потеря всего справочника.
        sys.stderr.write('ERROR: database not found at %s - refusing to run\n'
                         % args.db)
        return 2
    con = sqlite3.connect(args.db, timeout=30)
    try:
        present = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='field_contours'").fetchone()
        if not present:
            sys.stderr.write('ERROR: table field_contours is missing - run '
                             'migrate_core_foundation_001.py first\n')
            return 2

        token = config.read_token()
        client = Client(pause=args.pause)
        try:
            client.login(token)
        except Exception as problem:                               # noqa: BLE001
            sys.stderr.write('\nERROR: ne udalos voyti na %s (%s)\n'
                             % (config.BASE_URL, type(problem).__name__))
            return 3
        print('login OK')
        try:
            ids = client.zone_ids(args.resource)
            print('zones listed in resource %d: %d' % (args.resource, len(ids)))
            if not ids:
                print('FAILED: resource returned no zone list, nothing to do')
                return 1
            zones = []
            step = args.chunk if args.chunk > 0 else len(ids)
            for start in range(0, len(ids), step):
                batch = client.zone_data(args.resource, ids[start:start + step])
                zones.extend(batch)
                print('  downloaded %d of %d' % (len(zones), len(ids)))
        finally:
            client.logout()

        if not zones:
            # [REASON]: список зон непустой, а данных нет -- это и есть тот
            # отказ, который однажды выглядел успехом: файл на два байта.
            print('FAILED: %d zones listed but none downloaded - NOT treating '
                  'this as an empty directory' % len(ids))
            return 1

        existing = existing_rows(con)
        fresh, changed, geomless, vanished = plan(zones, existing)
        duplicates = duplicate_names(zones)

        print('\ndownloaded: %d | with geometry: %d | without: %d'
              % (len(zones), len(zones) - len(geomless), len(geomless)))
        print('in our directory already: %d' % len(existing))
        print('to add: %d | to update: %d' % (len(fresh), len(changed)))
        print('names repeated in Wialon: %d (%d zones)'
              % (len(duplicates), sum(len(v) for v in duplicates.values())))
        if vanished:
            # Ничего не делаем -- см. докстринг. Только называем число.
            print('in our directory but no longer in Wialon: %d - LEFT AS IS, '
                  'nothing deleted or deactivated' % len(vanished))

        if duplicates:
            path = os.path.join(os.getcwd(), args.duplicates_out)
            with open(path, 'w', encoding='utf-8-sig', newline='') as fh:
                writer = csv.writer(fh, delimiter=';')
                writer.writerow(['imya', 'zon', 'wialon_id'])
                for name, zone_ids in sorted(duplicates.items(),
                                             key=lambda kv: -len(kv[1])):
                    writer.writerow([name, len(zone_ids),
                                     ' '.join(str(i) for i in zone_ids)])
            print('duplicate names written to %s' % args.duplicates_out)

        if args.dry_run:
            print('\ndry run: nothing was written')
            return 0
        apply_plan(con, fresh, changed)
        print('\nwritten: %d added, %d updated' % (len(fresh), len(changed)))
        return 0
    finally:
        con.close()


if __name__ == '__main__':
    sys.exit(main())
