# -*- coding: utf-8 -*-
"""Самотест tools/dji_area_block1b.py -- инструмент этапа 1B.

Инструмент вставляется в консоль сервера и читает БОЕВУЮ базу. Поэтому
проверяется не «отработал без исключения», а три свойства, каждое из которых
дёшево сломать и дорого заметить:

  1. он НЕ ПИШЕТ. Не «мы так задумали», а sha256 файла базы до и после, плюс
     DELETE, который SQLite обязан отклонить сам;
  2. на кейсе с намеренным стопроцентным перекрытием S = 2U, и кейс помечен
     различающим. Это единственная конфигурация, где сравнение S и U вообще
     способно отличить километражный интеграл от объединения; рядом стоит
     отрицательный контроль -- одиночный проход, где S = U и метка снята;
  3. коды возврата: нет базы -> 2 и файл НЕ создан; база без миграции -> 1.

Фикстуру строит сам, в отдельном временном каталоге: ни сети, ни сервера, ни
боевой базы. Stdlib плюс DDL, прочитанный из САМОЙ миграции.
"""

import hashlib
import math
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dji_area import store  # noqa: E402
from tests.test_dji_area_core import frame, v4_bytes  # noqa: E402

TOOL = os.path.join(ROOT, 'tools', 'dji_area_block1b.py')
MIGRATION = os.path.join(ROOT, 'migrate_dji_area_evidence_001.py')
HW = 'SYNTHETIC-HW-NOT-REAL'
FLIGHT = 900201
T0 = 1785526013
LAT0, LON0 = 39.9, 64.4
M_PER_DEG = 6371000.0 * math.pi / 180.0
PASS_M, PASS_FRAMES, WIDTH = 100.0, 21, 6.0


def _ddl():
    with open(MIGRATION, encoding='utf-8') as fh:
        text = fh.read()
    return re.findall(r'CREATE TABLE IF NOT EXISTS \w+ \(.*?\n    \)', text, re.S)


def _pass_frames(start_ms):
    out = []
    for i in range(PASS_FRAMES):
        out.append(frame(start_ms + i * 1000, area=10.0 + i * 0.05, width=WIDTH,
                         spray_flag=1, flow=100,
                         lat=LAT0 + (PASS_M / M_PER_DEG) * i / (PASS_FRAMES - 1),
                         lng=LON0))
    return out


def build(db_path, passes):
    """База с одним вылетом из `passes` одинаковых проходов по одной земле."""
    con = sqlite3.connect(db_path)
    for stmt in _ddl():
        con.execute(stmt)

    def cols(table):
        return {r[1]: (r[2], r[3], r[4], r[5])
                for r in con.execute('PRAGMA table_info(%s)' % table)}

    def ins(table, **vals):
        info, row = cols(table), dict(vals)
        for name, (typ, notnull, dflt, pk) in info.items():
            if pk or name in row:
                continue
            if notnull and dflt is None:
                t = (typ or '').upper()
                row[name] = 0 if any(k in t for k in ('INT', 'FLOAT', 'REAL',
                                                      'BOOL')) else 'x'
        keys = list(row)
        con.execute('INSERT INTO %s (%s) VALUES (%s)'
                    % (table, ','.join(keys), ','.join('?' * len(keys))),
                    [row[k] for k in keys])

    frames, t = [], T0 * 1000
    for _ in range(passes):
        frames.extend(_pass_frames(t))
        # Пауза между проходами: связующий отрезок обязан стать разрывом
        # записи, а не полосой длиной в сто метров.
        t += PASS_FRAMES * 1000 + 120000
    body = v4_bytes(frames)
    sha = hashlib.sha256(body).hexdigest()
    root = store.source_root(os.path.abspath(db_path))
    rel = store.body_relpath(sha, store.ENCODING_RAW)
    target = os.path.join(root, rel.replace('/', os.sep))
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, 'wb') as fh:
        fh.write(body)

    ins('dji_source_revisions', id=1, flight_id=FLIGHT, source_type='v4',
        sha256=sha, size_bytes=len(body), captured_at_utc='2026-09-09',
        storage_kind='file', body_path=rel, body_encoding=store.ENCODING_RAW,
        received_at='2026-09-09')
    ins('dji_area_calculations', flight_id=FLIGHT, hardware_id=HW,
        start_at_utc='2026-08-18T00:53:00', end_at_utc='2026-08-18T00:58:00',
        report_start_date='2026-08-18', raw_area_m2=PASS_M * WIDTH * passes,
        area_status='RAW_CORROBORATED_QUALIFIED', application_activity='PRESENT',
        application_channel_quality='INFORMATIVE', superseded_at=None)
    ins('dji_flight_evidence', flight_id=FLIGHT, hardware_id=HW,
        v4_revision_id=1, list_spray_width=WIDTH, list_start_ts=T0,
        list_end_ts=T0 + 300, updated_at='2026-09-09')
    ins('dji_v4_summaries', flight_id=FLIGHT, source_revision_id=1,
        frame_count=len(frames), span_s=300.0,
        moving_application_distance_m=PASS_M * passes,
        width_min=WIDTH, width_max=WIDTH, application_frames=len(frames))
    ins('dji_field_attributions', flight_id=FLIGHT,
        field_attribution_tier='TIER2_STRONG', field_land_uuid='uuid-1',
        field_name_at_snapshot='SYNTHETIC-FIELD', superseded_at=None)
    ins('dji_land_revisions', land_uuid='uuid-1', name='SYNTHETIC-FIELD',
        total_area_raw=30.0, work_area_raw=28.0, obstacle_area_raw=2.0,
        area_unit='mu', raw_json='{}', raw_sha256='s1',
        first_seen_snapshot_id=1, last_seen_snapshot_id=1)
    con.commit()
    con.close()


def run(db_path, out_dir):
    return subprocess.run(
        [sys.executable, TOOL, '--db', db_path, '--date', '2026-08-18',
         '--hardware', HW, '--out', out_dir],
        capture_output=True, text=True)


def sha_of(path):
    with open(path, 'rb') as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def coverage_row(out_dir):
    import json
    with open(os.path.join(out_dir, 'coverage.json'), encoding='utf-8') as fh:
        rows = json.load(fh)
    assert len(rows) == 1, 'expected one flight, got %d' % len(rows)
    return rows[0]


class Base(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='block1b-')
        self.db = os.path.join(self.dir, 'transport.db')
        self.out = os.path.join(self.dir, 'out')

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class ItNeverWrites(Base):

    def test_database_bytes_are_identical_before_and_after(self):
        build(self.db, passes=2)
        before = sha_of(self.db)
        res = run(self.db, self.out)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(sha_of(self.db), before)

    def test_control_sqlite_itself_refuses_a_write_on_this_handle(self):
        # Доказывает, что режим ro настоящий, а не «мы просто не пишем».
        build(self.db, passes=1)
        uri = 'file:%s?mode=ro' % self.db
        con = sqlite3.connect(uri, uri=True)
        with self.assertRaises(sqlite3.OperationalError):
            con.execute('DELETE FROM dji_area_calculations')
        con.close()


class OverlapIsTheDiscriminatingCase(Base):

    def test_two_identical_passes_give_S_twice_U_and_are_flagged(self):
        build(self.db, passes=2)
        self.assertEqual(run(self.db, self.out).returncode, 0)
        row = coverage_row(self.out)
        self.assertEqual(row['coverage_status'], 'ESTIMATE')
        self.assertAlmostEqual(row['unique_application_ha'], 0.06, delta=0.006)
        self.assertAlmostEqual(row['swath_integral_ha'], 0.12, delta=0.006)
        self.assertGreater(row['s_over_u'], 1.7)
        self.assertTrue(row['discriminating'])

    def test_control_a_single_pass_is_not_discriminating(self):
        # Без этого контроля проверка выше прошла бы и у кода, который метит
        # различающим КАЖДЫЙ вылет.
        build(self.db, passes=1)
        self.assertEqual(run(self.db, self.out).returncode, 0)
        row = coverage_row(self.out)
        self.assertAlmostEqual(row['unique_application_ha'], 0.06, delta=0.006)
        self.assertLess(abs(row['s_over_u'] - 1.0), 0.25)
        self.assertFalse(row['discriminating'])


class ExitCodes(Base):

    def test_missing_database_is_code_2_and_creates_no_file(self):
        missing = os.path.join(self.dir, 'nope', 'transport.db')
        res = run(missing, self.out)
        self.assertEqual(res.returncode, 2)
        self.assertFalse(os.path.exists(missing))
        self.assertFalse(os.path.isdir(os.path.dirname(missing)))

    def test_database_without_the_migration_is_code_1(self):
        sqlite3.connect(self.db).execute('CREATE TABLE x (a INT)')
        res = run(self.db, self.out)
        self.assertEqual(res.returncode, 1)
        self.assertIn('migration is not applied', res.stdout)


if __name__ == '__main__':
    unittest.main()
