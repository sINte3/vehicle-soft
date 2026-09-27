# -*- coding: utf-8 -*-
"""DJI-AREA-APPLICATION-MOTION-001: правило движения при применении -- сквозь
конвейер и инструмент сухой оценки.

Чистые модули проверены в `test_dji_area_core` (чтение движения, решение
резолвера) и `test_dji_area_accounting` (учётная причина). Здесь то, что
видно только на базе:

* правило доходит до базы РОВНО там, где сработало: запись с движением
  остаётся той же строкой с тем же отпечатком, запись без движения получает
  новую строку, прежняя закрывается (append-only);
* повторный пересчёт -- `unchanged`; откат кода возвращает прежнюю строку
  (`reactivated`), а не пишет третью;
* нечитаемое тело V4 -- не доказательство неподвижности и не повод
  переписывать строку;
* V4 с чужой идентичностью оценки движения не получает;
* сухая оценка ничего не пишет, называет, что изменится, и готовит команды
  адресного пересчёта без заполнителей.

Каждая проверка различает верный и неверный код: сравнение идёт с тем же
конвейером при ВЫКЛЮЧЕННОМ правиле (`application_without_moving_work` ->
False), то есть с тем, что было до него.

Stdlib sqlite3, без Flask и без приложения.
"""

import hashlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from dji_area import accounting as acc  # noqa: E402
from dji_area import evidence as ev  # noqa: E402
from dji_area import pipeline as pl  # noqa: E402
from dji_area import resolver as rs  # noqa: E402
from dji_area import store  # noqa: E402
from tests.test_dji_area_core import M_PER_DEG_LAT, frame, v4_bytes  # noqa: E402
from tests.test_dji_area_identity_001 import (  # noqa: E402
    BASE, BY_ID, DAY, LONE, MU, TARGET, Fixture, ts)
from tools import dji_area_application_motion_eval as tool  # noqa: E402

TARGET_MU = 10000.0 / MU     # плоский счётчик = RAW цели
LONE_MU = 9000.0 / MU


def rule_off():
    """Конвейер без правила -- ровно тот, что был до него."""
    return mock.patch.object(rs, 'application_without_moving_work',
                             return_value=False)


def add_flat_v4(fx, flight_id, native, moving):
    """V4 записи: счётчик плоский, применение в середине записи; ``moving``
    -- борт летит 5 м/с (и по полю 3, и по координатам), иначе стоит, а
    скорость опущена, как её опускает protobuf при нуле."""
    row = BY_ID[flight_id]
    t0, t1 = ts(row[1]), ts(row[2])
    frames = []
    for i in range(t1 - t0 + 1):
        spraying = 5 <= i < 15
        speed = 5.0 if moving and spraying else 0.0
        frames.append(frame(
            (t0 + i) * 1000, area=native,
            lat=39.9 + (5.0 * max(0, i - 5) if moving else 0.0) / M_PER_DEG_LAT,
            vx=None, vy=speed or None,
            spray_flag=1 if spraying else None,
            flow=900 if spraying else None))
    con = store.connect(fx.db)
    root = store.source_root(os.path.abspath(fx.db))
    store.begin_immediate(con)
    store.upsert_source_revision(con, root, 'v4', v4_bytes(frames),
                                 flight_id=flight_id)
    store.refresh_flight_evidence(con, root, flight_id)
    con.execute('COMMIT')
    con.close()


def current_rows(db):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        return {r['flight_id']: dict(r) for r in con.execute(
            'SELECT * FROM dji_area_calculations WHERE superseded_at IS NULL')}
    finally:
        con.close()


def sha256(path):
    with open(path, 'rb') as fh:
        return hashlib.sha256(fh.read()).hexdigest()


class Base(unittest.TestCase):

    def setUp(self):
        self.fx = Fixture()
        self.addCleanup(self.fx.close)
        add_flat_v4(self.fx, TARGET, TARGET_MU, moving=False)
        add_flat_v4(self.fx, LONE, LONE_MU, moving=True)
        # Исходное состояние -- строки, записанные кодом ДО правила.
        with rule_off():
            first = pl.recalculate(self.fx.db, DAY, DAY, apply=True)
        self.assertEqual(set(first['calc_writes']), {'new'})
        self.before = current_rows(self.fx.db)

    def recalc(self, apply=False):
        return pl.recalculate(self.fx.db, DAY, DAY, apply=apply,
                              collect_rows=True)


class TheRuleReachesTheDatabaseOnlyWhereItFired(Base):

    def test_before_the_rule_both_flat_records_wait_for_a_human(self):
        for fid in (TARGET, LONE):
            self.assertEqual(acc.classify(self.before[fid])['reason'],
                             acc.R_APPLICATION_WITH_FLAT_COUNTER, fid)

    def test_only_the_standing_record_is_rewritten(self):
        dry = self.recalc()
        self.assertEqual(dry['calc_writes'],
                         {'unchanged': 3, 'would_write': 1})
        lines = {r['flight_id']: r for r in dry['flights']}
        self.assertIn(rs.F_APPLICATION_WITHOUT_MOVING_WORK,
                      lines[TARGET]['anomaly_flags'])
        self.assertIn(rs.F_APPLICATION_WITH_FLAT_COUNTER,
                      lines[LONE]['anomaly_flags'])

        applied = self.recalc(apply=True)
        self.assertEqual(applied['calc_writes'], {'new': 1, 'unchanged': 3})
        after = current_rows(self.fx.db)
        target = acc.classify(after[TARGET])
        self.assertEqual(target['accounting_class'], acc.PHANTOM_PROVEN)
        self.assertEqual(target['reason'], acc.R_APPLICATION_WITHOUT_MOVING_WORK)
        self.assertNotEqual(after[TARGET]['id'], self.before[TARGET]['id'])
        # Запись с движением (LONE) и записи без V4 -- те же строки с теми же
        # отпечатками.
        self.assertIn(LONE, self.before)
        for fid in self.before:
            if fid == TARGET:
                continue
            self.assertEqual(after[fid]['id'], self.before[fid]['id'], fid)
            self.assertEqual(after[fid]['calculation_input_hash'],
                             self.before[fid]['calculation_input_hash'], fid)

    def test_append_only_the_previous_row_is_closed_not_rewritten(self):
        self.recalc(apply=True)
        con = sqlite3.connect(self.fx.db)
        con.row_factory = sqlite3.Row
        rows = con.execute('SELECT * FROM dji_area_calculations WHERE '
                           'flight_id=? ORDER BY id', (TARGET,)).fetchall()
        con.close()
        self.assertEqual(len(rows), 2)
        old, new = dict(rows[0]), dict(rows[1])
        self.assertEqual(old['supersede_reason'], 'INPUT_CHANGED')
        self.assertIsNotNone(old['superseded_at'])
        self.assertIsNone(new['superseded_at'])
        # Прежняя строка не переписана ни в одном поле решения.
        for key in ('area_status', 'aggregation_eligibility',
                    'anomaly_flags_json', 'calculation_input_hash'):
            self.assertEqual(old[key], self.before[TARGET][key], key)

    def test_raw_is_kept_and_billable_stays_empty(self):
        self.recalc(apply=True)
        after = current_rows(self.fx.db)
        self.assertEqual(after[TARGET]['raw_area_m2'], 10000.0)
        self.assertEqual(after[TARGET]['raw_area_m2'],
                         self.before[TARGET]['raw_area_m2'])
        self.assertEqual(after[TARGET]['corrected_recorded_area_m2'], 0.0)
        self.assertEqual(after[TARGET]['application_activity'], rs.ACT_PRESENT)
        con = sqlite3.connect(self.fx.db)
        try:
            self.assertEqual(con.execute(
                'SELECT COUNT(*) FROM dji_area_calculations WHERE '
                'billable_area_m2 IS NOT NULL').fetchone()[0], 0)
        finally:
            con.close()

    def test_a_second_apply_writes_nothing(self):
        self.recalc(apply=True)
        again = self.recalc(apply=True)
        self.assertEqual(again['calc_writes'], {'unchanged': 4})

    def test_rolling_the_code_back_reactivates_the_previous_row(self):
        self.recalc(apply=True)
        with rule_off():
            back = self.recalc(apply=True)
        self.assertEqual(back['calc_writes'],
                         {'reactivated': 1, 'unchanged': 3})
        after = current_rows(self.fx.db)
        self.assertEqual(after[TARGET]['id'], self.before[TARGET]['id'])


class MissingEvidenceIsNotProof(Base):

    def test_an_unreadable_v4_body_leaves_the_record_where_it_was(self):
        # Сводка V4 уже в кэше базы, а тело для оценки движения пропало.
        con = store.connect(self.fx.db)
        try:
            rev_id = con.execute(
                'SELECT v4_revision_id FROM dji_flight_evidence WHERE '
                'flight_id=?', (TARGET,)).fetchone()[0]
            rev = store.revision_by_id(con, rev_id)
        finally:
            con.close()
        self.assertEqual(rev['storage_kind'], store.STORAGE_FILE)
        body = os.path.join(store.source_root(os.path.abspath(self.fx.db)),
                            rev['body_path'].replace('/', os.sep))
        os.rename(body, body + '.away')
        try:
            dry = self.recalc()
            self.assertEqual(dry['calc_writes'], {'unchanged': 4})
            line = {r['flight_id']: r for r in dry['flights']}[TARGET]
            self.assertIn(rs.F_APPLICATION_WITH_FLAT_COUNTER,
                          line['anomaly_flags'])
        finally:
            os.rename(body + '.away', body)
        # Тело вернулось -- правило срабатывает, и пересчёт это запишет.
        self.assertEqual(self.recalc()['calc_writes'],
                         {'unchanged': 3, 'would_write': 1})

    def test_a_mismatched_v4_identity_gets_no_motion_reading(self):
        con = sqlite3.connect(self.fx.db)
        con.execute('UPDATE dji_flight_evidence SET v4_identity_status=? '
                    'WHERE flight_id=?', (ev.V4_MISMATCH, TARGET))
        con.commit()
        con.close()
        with mock.patch.object(pl, 'ensure_application_motion',
                               wraps=pl.ensure_application_motion) as spy:
            dry = self.recalc()
        asked = [c.args[2]['flight_id'] for c in spy.call_args_list]
        self.assertNotIn(TARGET, asked)
        self.assertIn(LONE, asked)
        line = {r['flight_id']: r for r in dry['flights']}[TARGET]
        self.assertNotIn(rs.F_APPLICATION_WITHOUT_MOVING_WORK,
                         line['anomaly_flags'])
        self.assertEqual(line['area_status'], rs.UNKNOWN_SUSPECT)


class ReadOnlyRecalculation(Base):

    def test_read_only_refuses_to_apply(self):
        with self.assertRaises(pl.PipelineError):
            pl.recalculate(self.fx.db, DAY, DAY, apply=True, read_only=True)

    def test_a_read_only_dry_run_writes_nothing(self):
        before = sha256(self.fx.db)
        out = pl.recalculate(self.fx.db, DAY, DAY, read_only=True,
                             collect_rows=True)
        self.assertEqual(out['calc_writes'],
                         {'unchanged': 3, 'would_write': 1})
        self.assertEqual(sha256(self.fx.db), before)
        for line in out['flights']:
            self.assertIsNone(line['billable_area_m2'])
            self.assertEqual(len(line['calculation_input_hash']), 64)


class DryEvaluationTool(Base):

    def run_tool(self, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = tool.main(list(args))
        return code, buf.getvalue()

    def setUp(self):
        super().setUp()
        self.out = tempfile.mkdtemp(prefix='motion_eval_')
        self.addCleanup(shutil.rmtree, self.out, True)

    def test_it_names_what_would_change_and_writes_nothing(self):
        before = sha256(self.fx.db)
        code, text = self.run_tool('--db', self.fx.db, '--out', self.out)
        self.assertEqual(code, tool.EXIT_OK, text)
        self.assertEqual(sha256(self.fx.db), before)
        self.assertEqual(current_rows(self.fx.db), self.before)
        with open(os.path.join(self.out, 'motion_eval.json'),
                  encoding='utf-8') as fh:
            report = json.load(fh)
        totals = report['totals']
        self.assertEqual(totals['flat_app_before'], 2)
        self.assertEqual(totals['fired'], 1)
        self.assertEqual(totals['review_after'], 1)
        self.assertEqual(totals['would_write'], 1)
        self.assertEqual(totals['would_write_without_rule'], 0)
        self.assertEqual(report['problems'], [])
        by_id = {t['flight_id']: t for t in report['targets']}
        self.assertTrue(by_id[TARGET]['rule_fired'])
        self.assertEqual(by_id[TARGET]['class_after'], acc.PHANTOM_PROVEN)
        self.assertEqual(by_id[TARGET]['displaced_frames'], 0)
        self.assertEqual(by_id[TARGET]['unobserved_frames'], 0)
        self.assertEqual(by_id[TARGET]['application_path_m'], 0.0)
        self.assertFalse(by_id[LONE]['rule_fired'])
        self.assertFalse(by_id[LONE]['would_write'])
        self.assertGreater(by_id[LONE]['displaced_frames'], 0)
        self.assertGreater(by_id[LONE]['application_path_m'], 0)
        self.assertTrue(text.isascii())

    def test_the_prepared_commands_are_ready_to_paste(self):
        self.run_tool('--db', self.fx.db, '--out', self.out)
        for name, mode in (('recalc_dry_run.txt', '--dry-run'),
                           ('recalc_apply.txt', '--apply')):
            with open(os.path.join(self.out, name), encoding='utf-8') as fh:
                command = fh.read()
            self.assertIn(mode, command)
            for fid in (TARGET, LONE):
                self.assertIn('--flight-id %d' % fid, command)
            self.assertIn('--from %s --to %s' % (DAY, DAY), command)
            for placeholder in ('<', '>', 'PATH', 'YYYY'):
                self.assertNotIn(placeholder, command)

    def test_the_prepared_dry_run_really_runs(self):
        self.run_tool('--db', self.fx.db, '--out', self.out)
        with open(os.path.join(self.out, 'recalc_dry_run.txt'),
                  encoding='utf-8') as fh:
            command = fh.read()
        ids = [int(chunk.split()[0])
               for chunk in command.split('--flight-id ')[1:]]
        self.assertEqual(sorted(ids), sorted([TARGET, LONE]))
        out = pl.recalculate(self.fx.db, DAY, DAY, flight_ids=ids)
        self.assertEqual(out['calc_writes'],
                         {'unchanged': 1, 'would_write': 1})

    def test_a_named_flight_is_evaluated_whatever_its_class(self):
        # База цепочки -- обычная запись; названная, она оценивается тоже.
        code, _text = self.run_tool('--db', self.fx.db, '--out', self.out,
                                    '--flight-id', str(BASE))
        self.assertEqual(code, tool.EXIT_OK)
        with open(os.path.join(self.out, 'motion_eval.json'),
                  encoding='utf-8') as fh:
            report = json.load(fh)
        by_id = {t['flight_id']: t for t in report['targets']}
        self.assertEqual(by_id[BASE]['class_before'], acc.NORMAL)
        self.assertEqual(by_id[BASE]['class_after'], acc.NORMAL)
        self.assertFalse(by_id[BASE]['would_write'])

    def test_a_filled_billable_breaks_the_invariant(self):
        # Отрицательный контроль: инвариант обязан уметь падать.
        con = sqlite3.connect(self.fx.db)
        con.execute('UPDATE dji_area_calculations SET billable_area_m2=1.0 '
                    'WHERE flight_id=?', (LONE,))
        con.commit()
        con.close()
        code, text = self.run_tool('--db', self.fx.db)
        self.assertEqual(code, tool.EXIT_INVARIANT, text)
        self.assertIn('billable_area_m2', text)

    def test_a_missing_database_is_refused_and_not_created(self):
        missing = os.path.join(self.out, 'nope', 'transport.db')
        code, _text = self.run_tool('--db', missing)
        self.assertEqual(code, tool.EXIT_NO_DATABASE)
        self.assertFalse(os.path.exists(missing))


class SelectiveVersionContract(unittest.TestCase):
    """Контракт версий `dji_area/__init__.py`, способ 2: версия выборочного
    правила входит в отпечаток ТОЛЬКО строки с его флагом и никогда -- в общую
    конфигурацию, которая есть у каждой строки. Сквозная половина контракта --
    `TheRuleReachesTheDatabaseOnlyWhereItFired` выше."""

    def test_every_selective_rule_is_declared_once_with_a_version(self):
        flags = [flag for flag, _key, _snap in rs.SELECTIVE_RULES]
        keys = [key for _flag, key, _snap in rs.SELECTIVE_RULES]
        self.assertEqual(len(flags), len(set(flags)))
        self.assertEqual(len(keys), len(set(keys)))
        self.assertIn(rs.F_APPLICATION_WITHOUT_MOVING_WORK, flags)
        for _flag, _key, snapshot in rs.SELECTIVE_RULES:
            self.assertTrue(snapshot()['rule_version'])

    def test_a_row_without_a_selective_flag_gets_no_mark(self):
        for flags in ([], [rs.F_APPLICATION_WITH_FLAT_COUNTER, 'V4_MISSING']):
            self.assertEqual(pl.selective_rule_marks(flags), {})

    def test_a_row_with_the_flag_gets_exactly_its_own_mark(self):
        marks = pl.selective_rule_marks(
            ['V4_MISSING', rs.F_APPLICATION_WITHOUT_MOVING_WORK])
        self.assertEqual(marks, {'application_motion_rule':
                                 rs.application_motion_rule_snapshot()})

    def test_the_mark_changes_the_fingerprint_and_its_absence_does_not(self):
        from dji_area.hashing import calculation_input_hash
        sources = {'list': 'a', 'card': None, 'route': None, 'v4': 'b'}
        base = calculation_input_hash(sources, [], True, extra={'k': 1})
        marked = dict({'k': 1}, **pl.selective_rule_marks(
            [rs.F_APPLICATION_WITHOUT_MOVING_WORK]))
        self.assertNotEqual(
            calculation_input_hash(sources, [], True, extra=marked), base)
        unmarked = dict({'k': 1}, **pl.selective_rule_marks([]))
        self.assertEqual(
            calculation_input_hash(sources, [], True, extra=unmarked), base)

    def test_selective_versions_stay_out_of_the_shared_configuration(self):
        from dji_area.hashing import canonical_json, resolver_config_snapshot
        shared = canonical_json(resolver_config_snapshot())
        for _flag, _key, snapshot in rs.SELECTIVE_RULES:
            self.assertNotIn(snapshot()['rule_version'], shared)

    def test_a_selective_rule_does_not_move_the_algorithm_version(self):
        import dji_area
        self.assertTrue(dji_area.AREA_ALGORITHM_VERSION.endswith('-impl-4'))
        self.assertEqual(dji_area.V4_PARSER_VERSION, 'v4-parse-1')


if __name__ == '__main__':
    unittest.main()
