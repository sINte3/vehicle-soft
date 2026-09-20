# -*- coding: utf-8 -*-
"""tools/test_dji_area_reparse_evidence.py -- самотест пересборки улик.

Сценарий воспроизводит то, что случилось на площадке: приёмник прежней ревизии
сохранил ПРАВИЛЬНЫЕ байты страницы списка, но разобрал их как одну запись и
оставил `list_revision_id` при пустых скалярах. Инструмент обязан вернуть
скаляры из тех же байтов -- и ни разу не сходить в кабинет DJI.

Проверки парные: рядом с «восстановил» стоит «ничего не выдумал, когда тела
нет» и «в dry-run база не изменилась ни на байт».

Запуск:  python tools\\test_dji_area_reparse_evidence.py
"""

import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dji_area import store  # noqa: E402
from tools import dji_area_reparse_evidence as tool  # noqa: E402

TOOL = os.path.join(ROOT, 'tools', 'dji_area_reparse_evidence.py')
MIGRATION = os.path.join(ROOT, 'migrate_dji_area_evidence_001.py')

# День отчёта 2026-09-02 (UTC+5) и сосед, который в период не входит.
IN_PERIOD = (950001, 950002, 950003)
OUT_OF_PERIOD = 950009
ALL_IDS = IN_PERIOD + (OUT_OF_PERIOD,)
STARTS = {950001: '2026-09-01 20:00:00',   # 02.09 01:00 по UTC+5
          950002: '2026-09-02 05:00:00',
          950003: '2026-09-02 06:00:00',
          950009: '2026-09-05 05:00:00'}
AREA = {950001: 10000.0, 950002: 9000.0, 950003: 8000.0, 950009: 7000.0}


def ts(text):
    return int((datetime.strptime(text, '%Y-%m-%d %H:%M:%S')
                - datetime(1970, 1, 1)).total_seconds())


def _ddl():
    with open(MIGRATION, encoding='utf-8') as fh:
        text = fh.read()
    return re.findall(r'CREATE TABLE IF NOT EXISTS \w+ \(.*?\n    \)', text,
                      re.S)


def page_body(flight_ids):
    """Страница ответа DJI -- ровно та форма, на которой падал прежний разбор."""
    rows = []
    for fid in flight_ids:
        rows.append({'id': fid, 'new_work_area': AREA[fid], 'mode_name': 4,
                     'manual_mode': False, 'spray_width': 6.0,
                     'start_timestamp': ts(STARTS[fid]),
                     'end_timestamp': ts(STARTS[fid]) + 420,
                     'nickname': 'SYNTHETIC-NICK'})
    return json.dumps({'status': 200, 'code': 0, 'message': 'OK',
                       'meta_data': {'total_count': len(rows),
                                     'total_pages': 1, 'current_page': 1},
                       'data': rows}, ensure_ascii=False).encode('utf-8')


class Base(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='reparse_')
        self.db = os.path.join(self.tmp, 'instance', 'test.db')
        os.makedirs(os.path.dirname(self.db))
        con = sqlite3.connect(self.db)
        con.execute('CREATE TABLE drone_units (id INTEGER PRIMARY KEY, '
                    'hardware_id TEXT)')
        con.execute('CREATE TABLE drone_flights (id INTEGER PRIMARY KEY, '
                    'dji_flight_id BIGINT UNIQUE, started_at TEXT, '
                    'finished_at TEXT, raw_json TEXT, drone_unit_id INTEGER, '
                    'nickname_raw TEXT)')
        for stmt in _ddl():
            con.execute(stmt)
        con.execute('INSERT INTO drone_units VALUES (1, ?)', ('BODY-CODE',))
        for fid in ALL_IDS:
            con.execute(
                'INSERT INTO drone_flights (dji_flight_id, started_at, '
                'finished_at, raw_json, drone_unit_id, nickname_raw) '
                'VALUES (?,?,?,?,1,?)',
                (fid, STARTS[fid], STARTS[fid], None, 'SYNTHETIC-NICK'))
        con.commit()
        con.close()
        self.stage()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def stage(self):
        """Состояние площадки: тела на месте, скаляры пусты."""
        body = page_body(ALL_IDS)
        con = store.connect(self.db)
        root = store.source_root(os.path.abspath(self.db))
        store.begin_immediate(con)
        for fid in ALL_IDS:
            store.upsert_source_revision(con, root, 'list', body,
                                         flight_id=fid, inline_max_bytes=0)
            store.refresh_flight_evidence(con, root, fid)
        con.execute('COMMIT')
        # Прежний приёмник разбирал страницу как одну запись и оставлял NULL.
        con.execute('UPDATE dji_flight_evidence SET %s'
                    % ', '.join('%s = NULL' % c for c in tool.SCALARS))
        con.commit()
        con.close()

    def run_tool(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = tool.main(['--db', self.db] + list(argv))
        return code, out.getvalue()

    def scalars(self):
        con = sqlite3.connect(self.db)
        con.row_factory = sqlite3.Row
        rows = {int(r['flight_id']): r['list_raw_area_m2'] for r in
                con.execute('SELECT flight_id, list_raw_area_m2 FROM '
                            'dji_flight_evidence')}
        con.close()
        return rows

    def db_sha(self):
        con = sqlite3.connect(self.db)
        con.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        con.close()
        with open(self.db, 'rb') as fh:
            return hashlib.sha256(fh.read()).hexdigest()


class ItRecoversWhatIsAlreadyStored(Base):

    def test_the_page_body_yields_the_scalars_again(self):
        self.assertEqual(set(self.scalars().values()), {None})
        code, text = self.run_tool('--apply', '--quiet')
        self.assertEqual(code, tool.EXIT_OK)
        self.assertIn('list scalars recovered      : 4', text)
        self.assertEqual(self.scalars(),
                         {fid: AREA[fid] for fid in ALL_IDS})

    def test_a_second_run_changes_nothing(self):
        self.run_tool('--apply', '--quiet')
        before = self.db_sha()
        code, text = self.run_tool('--apply', '--quiet')
        self.assertEqual(code, tool.EXIT_OK)
        self.assertIn('rows changed                : 0', text)
        self.assertEqual(self.db_sha(), before)

    def test_dry_run_writes_nothing_at_all(self):
        before = self.db_sha()
        code, text = self.run_tool('--dry-run', '--quiet')
        self.assertEqual(code, tool.EXIT_OK)
        self.assertIn('rows that would change      : 4', text)
        self.assertIn('Nothing was written', text)
        self.assertEqual(self.db_sha(), before)
        self.assertEqual(set(self.scalars().values()), {None})

    def test_the_period_filter_uses_the_report_day_in_utc_plus_5(self):
        # 950001 начат 01.09 20:00 UTC, то есть 02.09 01:00 по UTC+5.
        code, text = self.run_tool('--from', '2026-09-02', '--to',
                                   '2026-09-02', '--apply', '--quiet')
        self.assertEqual(code, tool.EXIT_OK)
        self.assertIn('flights with stored sources : 3', text)
        got = self.scalars()
        for fid in IN_PERIOD:
            self.assertEqual(got[fid], AREA[fid], fid)
        self.assertIsNone(got[OUT_OF_PERIOD])

    def test_only_unparsed_narrows_the_set(self):
        self.run_tool('--apply', '--quiet')
        code, text = self.run_tool('--only-unparsed', '--dry-run', '--quiet')
        self.assertEqual(code, tool.EXIT_OK)
        self.assertIn('flights with stored sources : 0', text)
        self.assertIn('Nothing to do.', text)

    def test_it_touches_neither_flights_nor_calculations(self):
        con = sqlite3.connect(self.db)
        con.execute(
            "INSERT INTO dji_area_calculations (flight_id, "
            "provider_account_id, area_algorithm_version, "
            "calculation_input_hash, calculated_at, start_at_utc, "
            "report_timezone, report_start_date, raw_area_m2, area_status, "
            "aggregation_eligibility) "
            "VALUES (950001, 'acct', 'v', 'h', '2026-09-20', '2026-09-02', "
            "'Asia/Tashkent', '2026-09-02', 1.0, 'RAW_UNVERIFIED', "
            "'PROVISIONAL')")
        con.commit()
        before = con.execute(
            'SELECT (SELECT COUNT(*) FROM drone_flights), '
            '(SELECT COUNT(*) FROM dji_area_calculations), '
            '(SELECT raw_area_m2 FROM dji_area_calculations)').fetchone()
        con.close()
        self.run_tool('--apply', '--quiet')
        con = sqlite3.connect(self.db)
        after = con.execute(
            'SELECT (SELECT COUNT(*) FROM drone_flights), '
            '(SELECT COUNT(*) FROM dji_area_calculations), '
            '(SELECT raw_area_m2 FROM dji_area_calculations)').fetchone()
        con.close()
        self.assertEqual(before, after)


class ItInventsNothing(Base):

    def test_a_missing_body_leaves_the_scalars_null(self):
        # Тело пропало из файлового хранилища -- восстанавливать нечего.
        root = store.source_root(os.path.abspath(self.db))
        shutil.rmtree(root, ignore_errors=True)
        code, text = self.run_tool('--apply', '--quiet')
        self.assertEqual(code, tool.EXIT_OK)
        self.assertIn('list scalars recovered      : 0', text)
        self.assertEqual(set(self.scalars().values()), {None})

    def test_losing_a_body_after_a_recovery_is_reported_not_silent(self):
        """Тело пропало ПОСЛЕ восстановления -- строка честно опустеет.

        [REASON]: строка улик -- производная от тел, и пересборка по
        пропавшему телу законно даёт пустые скаляры. Опасно здесь не это, а
        молчание: без отдельного счётчика прогон выглядел бы успешным ровно
        тогда, когда он стёр восстановленное. Поэтому потеря считается и
        печатается предупреждением.
        """
        self.run_tool('--apply', '--quiet')
        self.assertEqual(self.scalars()[IN_PERIOD[0]], AREA[IN_PERIOD[0]])
        root = store.source_root(os.path.abspath(self.db))
        shutil.rmtree(root, ignore_errors=True)
        code, text = self.run_tool('--apply', '--quiet')
        self.assertEqual(code, tool.EXIT_OK)
        self.assertIn('list scalars lost           : 4', text)
        self.assertIn('WARNING', text)
        self.assertEqual(set(self.scalars().values()), {None})

    def test_a_missing_database_is_code_2_and_no_file_appears(self):
        ghost = os.path.join(self.tmp, 'nowhere', 'absent.db')
        out = io.StringIO()
        with redirect_stdout(out):
            code = tool.main(['--db', ghost, '--apply'])
        self.assertEqual(code, tool.EXIT_NO_DATABASE)
        self.assertFalse(os.path.exists(ghost))

    def test_a_database_without_the_tables_is_refused_by_name(self):
        bare = os.path.join(self.tmp, 'bare.db')
        con = sqlite3.connect(bare)
        con.execute('CREATE TABLE drone_flights (id INTEGER PRIMARY KEY)')
        con.commit()
        con.close()
        out = io.StringIO()
        with redirect_stdout(out):
            code = tool.main(['--db', bare, '--apply'])
        self.assertEqual(code, tool.EXIT_USAGE)
        self.assertIn('migrate_dji_area_evidence_001.py', out.getvalue())

    def test_a_mode_must_be_chosen_explicitly(self):
        code, text = self.run_tool()
        self.assertEqual(code, tool.EXIT_USAGE)
        self.assertIn('choose a mode explicitly', text)
        code, text = self.run_tool('--apply', '--dry-run')
        self.assertEqual(code, tool.EXIT_USAGE)
        code, text = self.run_tool('--from', '2026-09-01', '--apply')
        self.assertEqual(code, tool.EXIT_USAGE)
        self.assertIn('given together', text)

    def test_console_output_is_ascii_only(self):
        result = subprocess.run(
            [sys.executable, TOOL, '--db', self.db, '--dry-run'],
            capture_output=True)
        self.assertEqual(result.returncode, tool.EXIT_OK, result.stderr)
        self.assertTrue(all(b < 128 for b in result.stdout))


if __name__ == '__main__':
    unittest.main()
