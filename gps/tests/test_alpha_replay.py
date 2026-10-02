# -*- coding: utf-8 -*-
"""tools/gps_alpha_replay.py -- «было / стало» правила допуска A7.

Окружение расчёта (геометрия): прогон на настоящем движке. Здесь держится то,
что делает прогон доказательством, а не пересказом:
  * сутки ниже порога совпадают с действующим кодом бит в бит, сутки дорог
    срабатывают и теряют гектары, поле среди дорог остаётся полем;
  * контроль прогона ловит базу, которая не совпадает с пересчётом, и
    не судит строки старой версии метода;
  * условие 3 проваливается, пока не пересчитаны все шесть названных суток;
  * наборы с ручными замерами: поле -- PASS, сутки дорог -- FAIL;
  * KML несёт «было» и «стало» у каждых выбранных суток;
  * ни база, ни файлы точек, ни треки не меняются.
"""
import contextlib
import hashlib
import io
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
import unittest.mock
import xml.etree.ElementTree as ET
from datetime import datetime

import migrate_gps_daily_001 as migration
import tools.gps_alpha_replay as replay
from gps.daily import METHOD_VERSION, compute_day, write_day
from gps.tests.test_area import (TWO_ROADS, shuttle_track, slow_loop,
                                 xy_to_lonlat)
from gps_collector import config as collector_config
from gps_collector import storage
from tests.test_gps_units_inventory import DDL

ROADS, FIELD, MIXED, TAMPERED, NO_POINTS, OLD = 393, 387, 388, 389, 390, 391


def local_start(day):
    return int(datetime.strptime(day, '%Y-%m-%d')
               .replace(tzinfo=collector_config.TZ).timestamp())


def shifted(track, day, hour=8):
    start = local_start(day) + hour * 3600
    return [(start + int(t), lon, lat, speed, 10) for t, lon, lat, speed in track]


def field_and_roads():
    field = shuttle_track(100.0, 300.0, pass_spacing_m=6.0, point_step_m=100.0)
    roads = slow_loop([(e + 1000.0, n + 1000.0) for e, n in TWO_ROADS],
                      loops=6, start_time=field[-1][0] + 600)
    return field + roads


class World(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.db = os.path.join(self.folder, 'transport.db')
        con = sqlite3.connect(self.db)
        try:
            for statement in DDL[:2]:   # vialon_mappings, equipment
                con.execute(statement)
            con.execute(migration.CREATE_DAILY_AGGREGATES)
            con.execute(migration.CREATE_WORK_POLYGONS)
            con.execute("INSERT INTO equipment (name, plate, category, "
                        "organization_id, is_active) VALUES "
                        "('МТЗ-80.1', '80 239 NA', 'mtz', 1, 1)")
            con.execute("INSERT INTO vialon_mappings (vialon_name, wialon_id, "
                        "equipment_id, skip) VALUES ('МТЗ 239', ?, 1, 0)",
                        (ROADS,))
            con.commit()
            self.day(con, '2026-09-26', ROADS, slow_loop(TWO_ROADS))
            self.day(con, '2026-09-27', FIELD,
                     shuttle_track(300.0, 300.0, pass_spacing_m=14.0))
            self.day(con, '2026-09-28', MIXED, field_and_roads())
            self.day(con, '2026-09-29', TAMPERED,
                     shuttle_track(300.0, 300.0, pass_spacing_m=6.0))
            con.execute("UPDATE gps_work_polygons SET area_ha = area_ha + 1 "
                        "WHERE wialon_id = ?", (TAMPERED,))
            con.commit()
            self.day(con, '2026-09-25', OLD,
                     shuttle_track(300.0, 300.0, pass_spacing_m=6.0))
            con.execute("UPDATE gps_daily_aggregates SET method_version = "
                        "'fixed-alpha-2026-07-29' WHERE wialon_id = ?", (OLD,))
            # опубликованная строка без точек на диске
            con.execute("INSERT INTO gps_daily_aggregates (work_date, wialon_id, "
                        "method_version, computed_at) VALUES "
                        "('2026-09-30', ?, ?, '2026-10-01 01:00:00')",
                        (NO_POINTS, METHOD_VERSION))
            con.commit()
        finally:
            con.close()

    def day(self, con, day, unit, track):
        points = shifted(track, day)
        storage.write_points(self.folder, [(unit, t, lon, lat, speed, None, sats)
                                           for t, lon, lat, speed, sats in points])
        write_day(con, day, unit, compute_day(points), '2026-10-01 01:00:00')

    def run_production(self, named=None, kml=None):
        out = []
        patch = (unittest.mock.patch.object(replay, 'NAMED_DAYS', named)
                 if named is not None else contextlib.nullcontext())
        with patch:
            ok = replay.replay_production(self.db, self.folder, '2026-09-01',
                                          '2026-09-30', kml, out=out.append)
        return ok, '\n'.join(out)

    def digest(self, path):
        with open(path, 'rb') as handle:
            return hashlib.sha256(handle.read()).hexdigest()


class Production(World):

    def test_roads_fall_fields_stay_and_the_mixed_day_keeps_its_field(self):
        con = sqlite3.connect(self.db)
        try:
            rows = {}
            for day, unit, version in replay.published_days(
                    con, '2026-09-01', '2026-09-30'):
                points = replay.read_day_readonly(self.folder, unit, day)
                if not points:
                    continue
                today, capped = replay.replay_day(points, None)
                rows[unit] = replay.judge_day(
                    day, unit, version, replay.stored_sites(con, day, unit),
                    today, capped, METHOD_VERSION)
        finally:
            con.close()
        roads, field, mixed = rows[ROADS], rows[FIELD], rows[MIXED]
        self.assertTrue(roads['triggered'])
        self.assertAlmostEqual(roads['ha_today'], 60.0, delta=0.5)
        self.assertEqual(roads['ha_cap'], 0.0)
        self.assertFalse(field['triggered'])
        self.assertEqual(replay.row_key(field['today']),
                         replay.row_key(field['capped']))
        self.assertTrue(mixed['triggered'])
        self.assertLess(mixed['ha_cap'], mixed['ha_today'] - 1.0)
        self.assertAlmostEqual(mixed['ha_cap'], 2.8, delta=0.3)
        for unit in (ROADS, FIELD, MIXED):
            self.assertEqual(rows[unit]['violations'], [], unit)
            self.assertTrue(rows[unit]['control'], unit)
        self.assertFalse(rows[TAMPERED]['control'])
        self.assertIsNone(rows[OLD]['control'])

    def test_the_control_fails_the_run_when_the_database_disagrees(self):
        ok, out = self.run_production(named=(('2026-09-26', ROADS),
                                             ('2026-09-28', MIXED)))
        self.assertFalse(ok)
        self.assertIn('control mismatch 2026-09-29 %d' % TAMPERED, out)
        self.assertIn('CONDITION 2 (invariants on every machine-day): FAIL', out)
        self.assertIn('fix the replay first', out)
        self.assertIn('not checked (older method version)', out)
        self.assertIn('points gone from disk: 1', out)

    def test_the_named_days_pass_only_when_all_are_replayed_and_fall(self):
        con = sqlite3.connect(self.db)
        try:
            con.execute("DELETE FROM gps_daily_aggregates WHERE wialon_id = ?",
                        (TAMPERED,))
            con.commit()
        finally:
            con.close()
        ok, out = self.run_production(named=(('2026-09-26', ROADS),
                                             ('2026-09-28', MIXED)))
        self.assertTrue(ok, out)
        self.assertIn('CONDITION 2 (invariants on every machine-day): PASS', out)
        self.assertIn('all six triggered, hectares strictly fall): PASS', out)
        ok, out = self.run_production()     # the real six: none of them here
        self.assertFalse(ok)
        self.assertIn('not replayed', out)
        self.assertIn('hectares strictly fall): FAIL', out)
        # поле не срабатывает -- значит, и «упасть» не может
        ok, out = self.run_production(named=(('2026-09-27', FIELD),))
        self.assertFalse(ok)
        self.assertIn('triggered NO', out)

    def test_the_kml_carries_before_and_after_for_every_picked_day(self):
        kml = os.path.join(self.folder, 'a7.kml')
        self.run_production(named=(('2026-09-26', ROADS),), kml=kml)
        root = ET.parse(kml).getroot()
        ns = {'k': 'http://www.opengis.net/kml/2.2'}
        folders = root.findall('.//k:Folder', ns)
        names = [f.find('k:name', ns).text for f in folders]
        # шесть закреплены, одни есть; плюс сработавшее «смешанное» из потерь
        self.assertEqual(len(folders), 2)
        self.assertIn('было / эди 60.00 га → стало / бўлди 0.00 га', names[0])
        marks = [p.find('k:name', ns).text
                 for p in folders[1].findall('k:Placemark', ns)]
        self.assertTrue(any(m.startswith('Было / эди: участок') for m in marks))
        self.assertTrue(any(m.startswith('Стало / бўлди: участок') for m in marks))
        self.assertTrue(any(m.startswith('Трек в работе') for m in marks))

    def test_nothing_is_written(self):
        points = storage.points_path(self.folder, '202609')
        before = (self.digest(self.db), self.digest(points))
        self.run_production()
        self.assertEqual((self.digest(self.db), self.digest(points)), before)


class Sets(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, True)
        corners = [(-20, -20), (320, -20), (320, 320), (-20, 320)]
        zone = {'type': 2, 'name': 'Поле 1',
                'points': [{'x': lon, 'y': lat}
                           for lon, lat in (xy_to_lonlat(e, n) for e, n in corners)]}
        self.zones = os.path.join(self.folder, 'wialon_zones.json')
        with open(self.zones, 'w', encoding='utf-8') as handle:
            json.dump({'101': zone}, handle, ensure_ascii=False)

    def write_tracks(self, name, days):
        path = os.path.join(self.folder, name)
        with open(path, 'w', encoding='utf-8-sig', newline='') as handle:
            handle.write('unit_id;date;time;lat;lon;speed;course;sats\n')
            for unit, day, track in days:
                for t, lon, lat, speed in track:
                    t = int(t) + 8 * 3600
                    handle.write('%d;%s;%02d:%02d:%02d;%.7f;%.7f;%.1f;0;10\n'
                                 % (unit, day, t // 3600, t % 3600 // 60, t % 60,
                                    lat, lon, speed))
        return path

    def digest(self, path):
        with open(path, 'rb') as handle:
            return hashlib.sha256(handle.read()).hexdigest()

    def run_main(self, *argv):
        with contextlib.redirect_stdout(io.StringIO()) as out, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            code = replay.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_a_set_of_field_work_is_bit_identical(self):
        tracks = self.write_tracks('fields.csv', [
            (1, '2026-08-01', shuttle_track(300.0, 300.0, pass_spacing_m=6.0)),
            (2, '2026-08-02', shuttle_track(300.0, 300.0, pass_spacing_m=37.2))])
        code, out, _err = self.run_main('--tracks', tracks, '--zones', self.zones)
        self.assertEqual(code, 0)
        self.assertIn('fields.csv: machine-days 2, zone works 2, triggered 0, '
                      'different 0', out)
        self.assertIn('CONDITION 1 (hand-measured sets bit-identical): PASS', out)

    def test_a_road_day_in_a_set_is_named_and_fails_the_condition(self):
        tracks = self.write_tracks('mixed.csv', [
            (1, '2026-08-01', shuttle_track(300.0, 300.0, pass_spacing_m=6.0)),
            (5, '2026-08-01', slow_loop(TWO_ROADS))])
        code, out, _err = self.run_main('--tracks', tracks, '--zones', self.zones)
        self.assertEqual(code, 0)
        self.assertIn('triggered 1, different 1', out)
        self.assertRegex(out, r'day\s+unit 5\s+day 2026-08-01 zone -\s+'
                              r'spacing 299\.\d\d\s+ha 60\.\d+ -> 0\.0000\s+DIFFERENT')
        self.assertIn('CONDITION 1 (hand-measured sets bit-identical): FAIL', out)

    def test_bad_input_is_refused_and_nothing_is_written(self):
        tracks = self.write_tracks('fields.csv', [
            (1, '2026-08-01', shuttle_track(300.0, 300.0, pass_spacing_m=6.0))])
        before = self.digest(tracks)
        self.assertEqual(self.run_main('--tracks', tracks)[0], 2)          # no zones
        self.assertEqual(self.run_main('--tracks', tracks, '--zones',
                                       self.zones, '--db', 'x.db')[0], 2)  # both
        self.assertEqual(self.run_main('--db', os.path.join(self.folder, 'no.db'),
                                       '--dir', self.folder, '--since',
                                       '2026-09-01')[0], 2)
        self.assertFalse(os.path.exists(os.path.join(self.folder, 'no.db')))
        self.run_main('--tracks', tracks, '--zones', self.zones)
        self.assertEqual(self.digest(tracks), before)


if __name__ == '__main__':
    unittest.main()
