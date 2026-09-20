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
from datetime import datetime, timedelta

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
        # Каждый запуск инструмента -- новая отметка управляемых часов.
        if getattr(self, 'clock', None) is not None:
            self.clock['run'] += 1
        out = io.StringIO()
        with redirect_stdout(out):
            code = tool.main(['--db', self.db] + list(argv))
        return code, out.getvalue()

    def controlled_clock(self, step_minutes=17):
        """Часы, которые ГАРАНТИРОВАННО идут вперёд между прогонами.

        [REASON]: `store.utcnow` округляет до секунды, а фикстура крошечная --
        два `--apply` подряд укладывались в одну секунду, получали один и тот
        же `updated_at` и совпадали побайтово СЛУЧАЙНО. Проверка, которая
        одинаково проходит и на исправном, и на дефектном коде, проверкой не
        является: на площадке тот же код переписал 4623 строки. Часы делают
        расхождение неизбежным, без `sleep`.
        """
        state = {'run': 0, 'n': 0}

        def fake_utcnow():
            # Внутри одного прогона время стоит, между прогонами -- идёт.
            state['n'] += 1
            return datetime(2026, 9, 20, 0, 0, 0) + timedelta(
                minutes=step_minutes * state['run'])

        real = store.utcnow
        store.utcnow = fake_utcnow
        self.addCleanup(setattr, store, 'utcnow', real)
        self.clock = state
        return state

    def updated_at_values(self):
        con = sqlite3.connect(self.db)
        con.row_factory = sqlite3.Row
        rows = {int(r['flight_id']): r['updated_at'] for r in con.execute(
            'SELECT flight_id, updated_at FROM dji_flight_evidence')}
        con.close()
        return rows

    def evidence_dump(self):
        """Полное содержимое `dji_flight_evidence`, а не одна колонка."""
        con = sqlite3.connect(self.db)
        con.row_factory = sqlite3.Row
        rows = [tuple(r) for r in con.execute(
            'SELECT * FROM dji_flight_evidence ORDER BY flight_id')]
        con.close()
        return rows

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
        """Повтор не меняет базу ФИЗИЧЕСКИ, и это не зависит от секунд.

        [REASON]: прежняя редакция запускала два `--apply` подряд и сверяла
        SHA. На крошечной фикстуре оба попадали в одну секунду, получали один
        `updated_at` и совпадали СЛУЧАЙНО -- а на площадке тот же код
        отчитался `rows changed : 0` и переписал 4623 строки. Управляемые часы
        убирают совпадение: между прогонами гарантированно проходит время.
        """
        clock = self.controlled_clock()
        code, first = self.run_tool('--apply', '--quiet')
        self.assertEqual(code, tool.EXIT_OK)
        self.assertIn('list scalars recovered      : 4', first)
        self.assertIn('rows rewritten physically   : 4', first)
        stamps = self.updated_at_values()
        before_sha = self.db_sha()
        before_dump = self.evidence_dump()
        ticks = clock['n']
        self.assertGreater(ticks, 0, 'часы не использовались -- проверка пуста')

        code, text = self.run_tool('--apply', '--quiet')

        self.assertEqual(code, tool.EXIT_OK)
        self.assertIn('rows changed                : 0', text)
        self.assertIn('rows rewritten physically   : 0', text)
        self.assertIn('list scalars recovered      : 0', text)
        self.assertIn('list scalars lost           : 0', text)
        # Часы во втором прогоне ДЕЙСТВИТЕЛЬНО шли: расхождение было
        # доступно, и код обязан был его не записать.
        self.assertGreater(clock['n'], ticks)
        self.assertEqual(self.updated_at_values(), stamps)
        self.assertEqual(self.evidence_dump(), before_dump)
        self.assertEqual(self.db_sha(), before_sha)

    def test_the_controlled_clock_really_moves(self):
        """Отрицательный контроль к часам.

        [REASON]: если бы подмена `store.utcnow` не действовала, тест выше
        проходил бы ровно по той причине, из-за которой дефект и прожил --
        одинаковый `updated_at`. Здесь часы обязаны дать РАЗНЫЕ отметки на
        двух настоящих записях.
        """
        self.controlled_clock()
        self.run_tool('--apply', '--quiet')
        first = set(self.updated_at_values().values())
        self.assertEqual(len(first), 1, first)
        # Скаляры стёрты -- второй прогон обязан записать строку заново.
        con = sqlite3.connect(self.db)
        con.execute('UPDATE dji_flight_evidence SET %s'
                    % ', '.join('%s = NULL' % c for c in tool.SCALARS))
        con.commit()
        con.close()
        self.run_tool('--apply', '--quiet')
        second = set(self.updated_at_values().values())
        self.assertEqual(len(second), 1, second)
        self.assertNotEqual(first, second)

    def test_a_non_scalar_column_also_counts_as_a_real_change(self):
        """Сравнение идёт по ВСЕМ содержательным колонкам, не по семи скалярам.

        [REASON]: если сузить сравнение до `SCALARS`, строка, у которой
        разошлось любое другое поле, будет молча откачена -- инструмент
        перестанет применять настоящее исправление и отчитается нулём.
        `drone_flight_id` не входит в `SCALARS`, и его достаточно.
        """
        self.controlled_clock()
        self.run_tool('--apply', '--quiet')
        target = IN_PERIOD[0]
        self.assertNotIn('drone_flight_id', tool.SCALARS)

        con = sqlite3.connect(self.db)
        con.execute('UPDATE dji_flight_evidence SET drone_flight_id = NULL '
                    'WHERE flight_id = ?', (target,))
        con.commit()
        con.close()

        code, text = self.run_tool('--apply', '--quiet')
        self.assertEqual(code, tool.EXIT_OK)
        self.assertIn('rows rewritten physically   : 1', text)
        con = sqlite3.connect(self.db)
        restored = con.execute(
            'SELECT drone_flight_id FROM dji_flight_evidence '
            'WHERE flight_id = ?', (target,)).fetchone()[0]
        con.close()
        self.assertIsNotNone(restored)

    def test_a_row_whose_body_changed_is_still_rewritten(self):
        """Точка сохранения откатывает ТОЛЬКО совпавшие строки.

        [REASON]: откат «на всякий случай» сделал бы инструмент бесполезным.
        Здесь одна строка обязана быть переписана, а три -- нет.
        """
        self.controlled_clock()
        self.run_tool('--apply', '--quiet')
        stamps = self.updated_at_values()
        target = IN_PERIOD[0]

        con = sqlite3.connect(self.db)
        con.execute('UPDATE dji_flight_evidence SET list_raw_area_m2 = NULL '
                    'WHERE flight_id = ?', (target,))
        con.commit()
        con.close()

        code, text = self.run_tool('--apply', '--quiet')
        self.assertEqual(code, tool.EXIT_OK)
        self.assertIn('rows rewritten physically   : 1', text)
        now = self.updated_at_values()
        self.assertNotEqual(now[target], stamps[target])
        for fid, stamp in stamps.items():
            if fid != target:
                self.assertEqual(now[fid], stamp, fid)

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

    def test_a_failed_apply_keeps_every_scalar_it_already_had(self):
        """Тело пропало ПОСЛЕ восстановления -- и база остаётся прежней.

        [REASON]: кода возврата здесь МАЛО. Он защищает следующий шаг
        цепочки, но не эту базу. Пока `--apply` коммитил батчами, испорченные
        строки ложились в базу до того, как потеря вообще была замечена:
        прогон возвращал 3, пересчёт не стартовал, а улики уже были стёрты.
        Проверка требует, чтобы прежние значения скаляров УЦЕЛЕЛИ, а не стали
        `None`.
        """
        self.run_tool('--apply', '--quiet')
        good = dict(self.scalars())
        self.assertEqual(good[IN_PERIOD[0]], AREA[IN_PERIOD[0]])
        # Расхождение обязано быть настоящим: скаляры непусты у всех строк.
        self.assertNotIn(None, good.values())

        root = store.source_root(os.path.abspath(self.db))
        shutil.rmtree(root, ignore_errors=True)
        code, text = self.run_tool('--apply', '--quiet')

        self.assertEqual(code, tool.EXIT_LOST_SCALARS)
        self.assertIn('list scalars lost           : 4', text)
        self.assertIn('FAILED', text)
        self.assertIn('ROLLED BACK', text)
        # Главное: ни один уже известный скаляр не потерян в БАЗЕ.
        self.assertEqual(self.scalars(), good)
        self.assertNotIn(None, self.scalars().values())

    def test_a_failed_apply_leaves_the_database_byte_for_byte(self):
        """Отрицательный контроль по содержимому файла, а не по одной колонке.

        [REASON]: сравнение одной колонки прошло бы и тогда, когда прогон
        испортил соседние поля строки улик или другую таблицу. Здесь
        сверяется SHA-256 всего файла базы и полный дамп `dji_flight_evidence`.
        """
        self.run_tool('--apply', '--quiet')
        before_sha = self.db_sha()
        before_dump = self.evidence_dump()

        root = store.source_root(os.path.abspath(self.db))
        shutil.rmtree(root, ignore_errors=True)
        code, _text = self.run_tool('--apply', '--quiet')

        self.assertEqual(code, tool.EXIT_LOST_SCALARS)
        self.assertEqual(self.evidence_dump(), before_dump)
        self.assertEqual(self.db_sha(), before_sha)

    def test_the_byte_comparison_can_actually_see_a_change(self):
        """Без этого две проверки выше ничего не различают.

        [REASON]: если бы `db_sha` читал не тот файл или дамп всегда выходил
        пустым, неуспешный `--apply` «сохранял» бы базу тривиально. Здесь тот
        же контроль применяется к УСПЕШНОМУ прогону, который обязан базу
        изменить.
        """
        before_sha = self.db_sha()
        before_dump = self.evidence_dump()
        self.assertTrue(before_dump)
        code, _text = self.run_tool('--apply', '--quiet')
        self.assertEqual(code, tool.EXIT_OK)
        self.assertNotEqual(self.evidence_dump(), before_dump)
        self.assertNotEqual(self.db_sha(), before_sha)

    def test_a_dry_run_that_would_lose_scalars_exits_nonzero(self):
        """Ворота блока R: сухой прогон обязан НЕ пустить `--apply`.

        [REASON]: цепочка ранбука читает только `$LASTEXITCODE`. Пока потеря
        была предупреждением при коде 0, сухой прогон заканчивался успехом,
        за ним стартовал `--apply`, и тот же обвал повторялся уже с записью в
        базу. Проверка построена на КОДЕ, а не на тексте: текст мог бы
        печататься и при нуле -- и печатался.
        """
        self.run_tool('--apply', '--quiet')
        self.assertEqual(self.scalars()[IN_PERIOD[0]], AREA[IN_PERIOD[0]])
        root = store.source_root(os.path.abspath(self.db))
        shutil.rmtree(root, ignore_errors=True)

        code, text = self.run_tool('--dry-run', '--quiet')
        self.assertEqual(code, tool.EXIT_LOST_SCALARS)
        self.assertNotEqual(tool.EXIT_LOST_SCALARS, tool.EXIT_OK)
        self.assertIn('NOT a success', text)
        # Сухой прогон остаётся сухим: он ничего не стёр, и потому потерю
        # увидит и следующий за ним `--apply`.
        self.assertEqual(self.scalars()[IN_PERIOD[0]], AREA[IN_PERIOD[0]])

    def test_a_clean_run_still_exits_zero(self):
        # Отрицательный контроль к двум проверкам выше: если бы код 3
        # возвращался всегда, они прошли бы, ничего не различая.
        code, _text = self.run_tool('--dry-run', '--quiet')
        self.assertEqual(code, tool.EXIT_OK)
        code, _text = self.run_tool('--apply', '--quiet')
        self.assertEqual(code, tool.EXIT_OK)

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
