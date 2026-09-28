# -*- coding: utf-8 -*-
"""DJI-AREA-RETAINED-FOOTPRINT-001: перенесённый скаляр с пренебрежимым следом.

Правило ставит доказанный ноль, только когда сходятся три независимых
свидетельства: счётчик V4 не вырос (полное проверенное окно, концы равны по
битам), RAW точно совпал с площадью базы цепочки (замороженный структурный
экран), а собственный след распыления -- путь на кадрах применения x
огибающая 12 м -- не больше 0,027 RAW. Всё остальное остаётся как было.

Проверки парные: рядом с «должно стать нулём» стоит случай, который при
неверной реализации стал бы нулём по ошибке. Сравнение сквозь конвейер идёт с
тем же конвейером при ВЫКЛЮЧЕННОМ правиле -- то есть с тем, что было до него.

Stdlib, без Flask и без сети; V4 -- синтетика матрицы приёмки.
"""

import hashlib
import math
import os
import sqlite3
import sys
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import dji_area  # noqa: E402
from dji_area import accounting as acc  # noqa: E402
from dji_area import control_report as cr  # noqa: E402
from dji_area import decisions as dec  # noqa: E402
from dji_area import evidence as ev  # noqa: E402
from dji_area import footprint as fpm  # noqa: E402
from dji_area import pipeline as pl  # noqa: E402
from dji_area import resolver as rs  # noqa: E402
from dji_area import store  # noqa: E402
from dji_area import v4  # noqa: E402
from dji_area.hashing import (calculation_input_hash, canonical_json,  # noqa: E402
                              resolver_config_snapshot)
from tests.test_dji_area_core import (MS, START, evidence, f_bytes, f_f32,  # noqa: E402
                                      f_varint, frame, v4_bytes)
from tests.test_dji_area_identity_001 import (BASE, BY_ID, DAY, LONE, MU,  # noqa: E402
                                              TARGET, Fixture, ts)

M_PER_DEG_LAT = math.pi * 6371000.0 / 180.0
FLAT_MU = 15.0
MATCH = {'applicable': True, 'candidate': True, 'reason': 'CANDIDATE',
         'base_flight_id': 1, 'bridge_flight_ids': [2],
         'boundary_gaps_s': [0.0, 0.0], 'scalar_source_check': True}
NO_CANDIDATE = dict(MATCH, candidate=False, reason='NO_CHAIN',
                    base_flight_id=None, bridge_flight_ids=[],
                    boundary_gaps_s=[], scalar_source_check=None)
SCALAR_MISMATCH = dict(MATCH, scalar_source_check=False)


# ─── Синтетика ───────────────────────────────────────────────────────────────

def burst(path_m, n=40, first=5, steps=28, area=FLAT_MU, width=6.0,
          spray_flag=1, flow=900, areas=None):
    """``n`` кадров по 0,1 с; применение в ``steps`` кадрах с ``first``, и
    за них борт проходит ровно ``path_m`` на север; остальное время стоит."""
    step = path_m / float(steps)
    frames, lat = [], 39.9
    for i in range(n):
        on = first <= i < first + steps
        if first < i <= first + steps:
            lat += step / M_PER_DEG_LAT
        frames.append(frame(MS + i * 100,
                            area=areas[i] if areas is not None else area,
                            lat=lat, width=width, vx=0.0,
                            vy=step / 0.1 if on else 0.0,
                            spray_flag=spray_flag if on else None,
                            flow=flow if on else None))
    return frames


def frame_without_position(t_ms, area=FLAT_MU, spray=True):
    body = f_varint(v4.F_TIME_MS, t_ms)
    body += f_bytes(v4.F_VELOCITY, f_f32(1, 0.0) + f_f32(2, 1.0))
    body += f_varint(v4.F_WORK_MODE, 4) + f_f32(v4.F_SPRAY_WIDTH, 6.0)
    body += f_bytes(v4.F_SPRAY, (f_varint(v4.S_SPRAY_FLAG, 1)
                                 + f_varint(v4.S_FLOW, 900)) if spray else b'')
    return body + f_f32(v4.F_AREA_STATE, area)


def decoded(frames):
    return v4.decode_v4(v4_bytes(frames)).frames


def with_footprint(raw, frames, structural=MATCH, **kw):
    """Доказательство, как его собирает конвейер: сводка и след ОДНОГО файла."""
    evid = evidence(raw, frames, structural=structural, **kw)
    evid['v4']['retained_footprint'] = fpm.application_footprint(
        decoded(frames))
    return evid


def raw_frame(i, **kw):
    """Кадр-словарь, как его отдаёт декодер (для чистой функции)."""
    out = {'t': MS + i * 100, 'lat': 39.9 + kw.pop('north_m', 0.0)
           / M_PER_DEG_LAT, 'lng': 64.4, 'spray_flag': 1, 'flow': 900}
    out.update(kw)
    return out


# ─── Чистая функция следа ────────────────────────────────────────────────────

class FootprintReading(unittest.TestCase):

    def test_application_path_times_the_fixed_envelope(self):
        f = fpm.application_footprint(decoded(burst(2.8)))
        self.assertEqual(f['application_frames'], 28)
        self.assertEqual(f['steps_needed'], 28)
        self.assertEqual(f['steps_unobserved'], 0)
        self.assertAlmostEqual(f['application_path_m'], 2.8, 6)
        self.assertAlmostEqual(f['conservative_footprint_m2'], 2.8 * 12.0, 6)
        # Фактическая ширина -- только диагностика, в решение не входит.
        self.assertEqual(f['width_max_m'], 6.0)
        self.assertFalse(f['width_over_envelope'])

    def test_the_whole_route_is_not_the_footprint(self):
        # После применения борт летит ещё 100 м -- в след это не входит.
        frames = [raw_frame(i, north_m=0.1 * min(i, 5)) for i in range(6)]
        frames += [raw_frame(6 + i, north_m=0.5 + 10.0 * (i + 1),
                             spray_flag=None, flow=None) for i in range(10)]
        f = fpm.application_footprint(frames)
        self.assertAlmostEqual(f['application_path_m'], 0.5 + 10.0, 6)

    def test_flag_without_flow_and_flow_without_flag_are_one_predicate(self):
        # 13.
        only_flag = fpm.application_footprint(decoded(burst(2.0, flow=None)))
        only_flow = fpm.application_footprint(
            decoded(burst(2.0, spray_flag=None)))
        both = fpm.application_footprint(decoded(burst(2.0)))
        for f in (only_flag, only_flow):
            self.assertEqual(f['application_frames'], both['application_frames'])
            self.assertAlmostEqual(f['application_path_m'],
                                   both['application_path_m'], 9)

    def test_a_missing_coordinate_fails_closed(self):
        # 9.
        frames = [raw_frame(i) for i in range(6)]
        del frames[3]['lat']
        f = fpm.application_footprint(frames)
        self.assertEqual(f['steps_unobserved'], 2)   # в кадр 3 и из него
        self.assertIsNone(f['conservative_footprint_m2'])

    def test_a_missing_time_fails_closed(self):
        # 9.
        frames = [raw_frame(i) for i in range(6)]
        del frames[3]['t']
        f = fpm.application_footprint(frames)
        self.assertGreater(f['steps_unobserved'], 0)
        self.assertIsNone(f['conservative_footprint_m2'])

    def test_a_step_longer_than_the_window_limit_fails_closed(self):
        # 10.
        frames = [raw_frame(i) for i in range(6)]
        frames[4]['t'] = frames[3]['t'] + 1500
        frames[5]['t'] = frames[4]['t'] + 100
        f = fpm.application_footprint(frames)
        self.assertEqual(f['steps_unobserved'], 1)
        self.assertIsNone(f['conservative_footprint_m2'])

    def test_a_width_over_the_envelope_or_not_a_number_fails_closed(self):
        # 11.
        for width, over in ((12.0, False), (12.01, True), (float('nan'), True),
                            (0.0, False)):
            frames = [raw_frame(i, width=width) for i in range(4)]
            f = fpm.application_footprint(frames)
            self.assertIs(f['width_over_envelope'], over, width)

    def test_missing_width_does_not_change_the_decision_value(self):
        # 12.
        f = fpm.application_footprint(decoded(burst(2.8, width=None)))
        self.assertIsNone(f['width_max_m'])
        self.assertFalse(f['width_over_envelope'])
        self.assertAlmostEqual(f['conservative_footprint_m2'], 2.8 * 12.0, 6)

    def test_the_last_application_frame_needs_no_step(self):
        frames = [raw_frame(i) for i in range(5)]
        f = fpm.application_footprint(frames)
        self.assertEqual(f['application_frames'], 5)
        self.assertEqual(f['steps_needed'], 4)
        self.assertEqual(f['steps_unobserved'], 0)

    def test_the_boundary_is_inclusive_and_nothing_above_passes(self):
        # 17.
        self.assertTrue(fpm.negligible_footprint(27.0, 1000.0))
        self.assertTrue(fpm.negligible_footprint(270.0, 10000.0))
        self.assertFalse(fpm.negligible_footprint(27.000001, 1000.0))
        self.assertFalse(fpm.negligible_footprint(None, 1000.0))
        self.assertFalse(fpm.negligible_footprint(0.0, 0.0))

    def test_the_parameters_are_frozen(self):
        self.assertEqual(fpm.FOOTPRINT_TO_RAW_MAX, 0.027)
        self.assertEqual(fpm.FOOTPRINT_WIDTH_ENVELOPE_M, 12.0)


# ─── Решение резолвера ───────────────────────────────────────────────────────

class RetainedRuleInTheResolver(unittest.TestCase):

    def decide(self, raw, frames, **kw):
        return rs.resolve_area(with_footprint(raw, frames, **kw))

    def assert_review(self, d):
        self.assertEqual(d.area_status, rs.COUNTER_FLAT_RAW_OVERSTATED)
        self.assertIn(rs.F_APPLICATION_WITH_FLAT_COUNTER, d.anomaly_flags)
        self.assertNotIn(rs.F_RETAINED_NEGLIGIBLE_FOOTPRINT, d.anomaly_flags)
        self.assertEqual(d.aggregation_eligibility, rs.AGG_UNRESOLVED)

    def assert_proven(self, d, raw):
        self.assertEqual(d.area_status, rs.COUNTER_FLAT_RAW_OVERSTATED)
        self.assertIn(rs.F_RETAINED_NEGLIGIBLE_FOOTPRINT, d.anomaly_flags)
        self.assertNotIn(rs.F_APPLICATION_WITH_FLAT_COUNTER, d.anomaly_flags)
        self.assertEqual(d.aggregation_eligibility, rs.AGG_CERTIFIED)
        self.assertEqual(d.corrected_recorded_area_m2, 0.0)
        self.assertEqual(d.controller_delta_area_m2, 0.0)
        self.assertEqual(d.raw_area_m2, raw)                      # 18
        # Распыление было -- активность не прячется под NOT_OBSERVED.
        self.assertEqual(d.application_activity, rs.ACT_PRESENT)

    def test_the_three_manual_phantoms_are_proven_zero(self):
        # 1. RAW и путь при применении трёх записей, признанных фантомами.
        for raw, path in ((15906.0, 26.8), (14106.0, 28.0), (10200.0, 22.8)):
            self.assert_proven(self.decide(raw, burst(path)), raw)

    def test_a_687610350_like_record_stays_in_review(self):
        # 2. След 142 м x 12 м -- 0,40 RAW: RAW им не объяснён, но и не ноль.
        self.assert_review(self.decide(4286.0, burst(142.4)))

    def test_without_a_structural_candidate_it_stays_in_review(self):
        # 3, 5.
        for structural in (NO_CANDIDATE, None, {}):
            self.assert_review(self.decide(15906.0, burst(26.8),
                                           structural=structural))

    def test_a_scalar_mismatch_stays_in_review(self):
        # 4.
        self.assert_review(self.decide(15906.0, burst(26.8),
                                       structural=SCALAR_MISMATCH))

    def test_a_real_treatment_is_normal_never_phantom(self):
        # 6. Счётчик вырос на весь RAW -- даже у кандидата экрана.
        frames = burst(2.0, areas=[i * 15.0 / 39 for i in range(40)])
        d = self.decide(10000.0, frames)
        self.assertEqual(d.area_status, rs.RAW_CORROBORATED)
        self.assertNotIn(rs.F_RETAINED_NEGLIGIBLE_FOOTPRINT, d.anomaly_flags)
        self.assertEqual(acc.classify(d.as_dict())['accounting_class'],
                         acc.NORMAL)

    def test_a_qualified_window_is_never_this_rule(self):
        # 7. A2: начало счётчика не закодировано, подокно выросло на RAW, след
        # крошечный -- как у четырёх отрицательных контролей production.
        areas = [None] * 3 + [0.01 + i * 14.99 / 36 for i in range(37)]
        d = self.decide(10000.0, burst(0.5, areas=areas))
        self.assertEqual(d.area_status, rs.RAW_CORROBORATED_QUALIFIED)
        self.assertNotIn(rs.F_RETAINED_NEGLIGIBLE_FOOTPRINT, d.anomaly_flags)
        self.assertEqual(acc.classify(d.as_dict())['accounting_class'],
                         acc.NORMAL)

    def test_an_unobservable_application_step_stays_in_review(self):
        # 9. После кадра применения координат нет.
        frames = burst(26.8)
        frames[20] = frame_without_position(MS + 2000)
        self.assert_review(self.decide(15906.0, frames))

    def test_a_long_gap_never_becomes_a_zero(self):
        # 10. Разрыв 1,5 с: окно неполное -- запасной RAW, не ноль.
        frames = burst(26.8, n=12, first=2, steps=8)
        frames = frames[:6] + [frame(MS + 2000 + i * 100, area=FLAT_MU)
                               for i in range(6)]
        d = self.decide(15906.0, frames, end_ts=START + 3)
        self.assertNotIn(rs.F_RETAINED_NEGLIGIBLE_FOOTPRINT, d.anomaly_flags)
        self.assertNotEqual(d.corrected_recorded_area_m2, 0.0)

    def test_a_width_over_the_envelope_stays_in_review(self):
        # 11.
        self.assert_review(self.decide(15906.0, burst(26.8, width=12.5)))

    def test_a_missing_width_still_decides_on_the_envelope(self):
        # 12.
        self.assert_proven(self.decide(15906.0, burst(26.8, width=None)),
                           15906.0)

    def test_raw_zero_is_not_this_rule(self):
        # 14.
        d = self.decide(0.0, burst(26.8))
        self.assertEqual(d.area_status, rs.COUNTER_ZERO)
        self.assertNotIn(rs.F_RETAINED_NEGLIGIBLE_FOOTPRINT, d.anomaly_flags)

    def test_an_overlap_conflict_stays_in_review(self):
        # 15.
        d = self.decide(15906.0, burst(26.8),
                        overlap={'group_id': 'overlap:1', 'conflict': True})
        self.assertEqual(d.area_status, rs.OVERLAP_REVIEW)
        self.assertNotIn(rs.F_RETAINED_NEGLIGIBLE_FOOTPRINT, d.anomaly_flags)
        self.assertEqual(d.aggregation_eligibility, rs.AGG_EXCLUDED_OVERLAP)

    def test_an_identity_conflict_uses_no_footprint(self):
        # 16.
        d = self.decide(15906.0, burst(26.8), identity_ok=False)
        self.assertEqual(d.area_status, rs.UNKNOWN_SUSPECT)
        self.assertNotIn(rs.F_RETAINED_NEGLIGIBLE_FOOTPRINT, d.anomaly_flags)

    def test_an_unreliable_or_unknown_channel_stays_in_review(self):
        # 6 из условий правила: канал применения обязан быть информативным.
        for channel in (rs.CH_UNRELIABLE, rs.CH_UNKNOWN):
            self.assert_review(self.decide(15906.0, burst(26.8),
                                           channel=channel))

    def test_the_boundary_through_the_resolver(self):
        # 17. Ровно 0,027 RAW -- срабатывает; чуть больше -- нет.
        for value, proven in ((270.0, True), (270.0001, False)):
            evid = with_footprint(10000.0, burst(1.0))
            evid['v4']['retained_footprint']['conservative_footprint_m2'] = value
            d = rs.resolve_area(evid)
            if proven:
                self.assert_proven(d, 10000.0)
            else:
                self.assert_review(d)

    def test_a_reading_of_another_file_or_version_is_ignored(self):
        # Резолвер не верит следу на слово: каждое поле, которое чтение
        # обязано было проверить, проверяется ещё раз.
        for key, value in (('application_frames', 27),
                           ('rule_version', 'retained-scalar-negligible-'
                                            'footprint-0'),
                           ('width_over_envelope', None),
                           ('steps_unobserved', 1),
                           ('steps_unobserved', None),
                           ('conservative_footprint_m2', None)):
            evid = with_footprint(15906.0, burst(26.8))
            evid['v4']['retained_footprint'][key] = value
            self.assert_review(rs.resolve_area(evid))

    def test_control_without_a_footprint_reading_it_stays_in_review(self):
        # Отрицательный контроль: те же кадры, следа нет (тело не читалось).
        evid = evidence(15906.0, burst(26.8), structural=MATCH)
        self.assert_review(rs.resolve_area(evid))


class AccountingAndReport(unittest.TestCase):

    def decision_row(self):
        return rs.resolve_area(with_footprint(15906.0, burst(26.8))).as_dict()

    def test_it_is_phantom_proven_with_its_own_reason(self):
        out = acc.classify(self.decision_row())
        self.assertEqual(out['accounting_class'], acc.PHANTOM_PROVEN)
        self.assertEqual(out['reason'], acc.R_RETAINED_NEGLIGIBLE_FOOTPRINT)
        self.assertEqual(out['accounted_area_m2'], 0.0)
        self.assertEqual(out['confirmed_overstatement_m2'], 15906.0)
        self.assertEqual(out['exposure_m2'], 15906.0)

    def test_the_words_say_retained_not_that_nothing_was_sprayed(self):
        row = dict(self.decision_row(), flight_id=1, report_start_date=DAY,
                   machine_key='D', machine_label='D')
        for lang, word in (('ru', 'перенесена'), ('uz', 'кўчирилган')):
            item = cr.build([row], lang)['register'][0]
            self.assertEqual(item['reason_code'],
                             acc.R_RETAINED_NEGLIGIBLE_FOOTPRINT)
            self.assertIn(word, item['reason_text'])
            self.assertEqual(item['accepted_m2'], 0.0)
            self.assertEqual(item['excluded_m2'], 15906.0)
        text = cr.pick(cr.EXPLANATIONS[acc.R_RETAINED_NEGLIGIBLE_FOOTPRINT],
                       'ru').lower()
        self.assertNotIn('не было', text)


# ─── Сквозь конвейер ─────────────────────────────────────────────────────────

def rule_off():
    """Конвейер без правила -- ровно тот, что был до него."""
    return mock.patch.object(rs, 'retained_negligible_footprint',
                             return_value=False)


def add_v4(fx, flight_id, first_mu, last_mu, spray_seconds, speed):
    """V4 записи с шагом 1 с: применение с 5-й секунды на ``speed`` м/с."""
    row = BY_ID[flight_id]
    t0, t1 = ts(row[1]), ts(row[2])
    n = t1 - t0
    frames, lat = [], 39.9
    for i in range(n + 1):
        on = 5 <= i < 5 + spray_seconds
        if 5 < i <= 5 + spray_seconds:
            lat += speed / M_PER_DEG_LAT
        frames.append(frame(
            (t0 + i) * 1000, area=first_mu + (last_mu - first_mu) * i / float(n),
            lat=lat, width=6.0, vx=0.0, vy=speed if on else 0.0,
            spray_flag=1 if on else None, flow=900 if on else None))
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


class EndToEnd(unittest.TestCase):
    """База: A (настоящая работа, счётчик вырос), C -- перенесённый скаляр с
    плоским счётчиком и 2 м распыления (RAW = RAW базы цепочки), LONE --
    плоский счётчик и 300 м распыления без цепочки."""

    def setUp(self):
        self.fx = Fixture()
        self.addCleanup(self.fx.close)
        add_v4(self.fx, BASE, 0.0, 10000.0 / MU, spray_seconds=400, speed=5.0)
        add_v4(self.fx, TARGET, FLAT_MU, FLAT_MU, spray_seconds=2, speed=1.0)
        add_v4(self.fx, LONE, 13.5, 13.5, spray_seconds=60, speed=5.0)
        with rule_off():
            first = pl.recalculate(self.fx.db, DAY, DAY, apply=True)
        self.assertEqual(set(first['calc_writes']), {'new'})
        self.before = current_rows(self.fx.db)

    def recalc(self, apply=False):
        return pl.recalculate(self.fx.db, DAY, DAY, apply=apply,
                              collect_rows=True)

    def test_before_the_rule_the_retained_record_waits_for_a_human(self):
        self.assertEqual(acc.classify(self.before[TARGET])['reason'],
                         acc.R_APPLICATION_WITH_FLAT_COUNTER)
        self.assertEqual(self.before[TARGET]['scalar_source_check'], 1)
        self.assertEqual(acc.classify(self.before[BASE])['accounting_class'],
                         acc.NORMAL)

    def test_only_the_retained_record_is_rewritten(self):
        # 21.
        dry = self.recalc()
        self.assertEqual(dry['calc_writes'],
                         {'unchanged': 3, 'would_write': 1})
        applied = self.recalc(apply=True)
        self.assertEqual(applied['calc_writes'], {'new': 1, 'unchanged': 3})
        after = current_rows(self.fx.db)
        target = acc.classify(after[TARGET])
        self.assertEqual(target['accounting_class'], acc.PHANTOM_PROVEN)
        self.assertEqual(target['reason'], acc.R_RETAINED_NEGLIGIBLE_FOOTPRINT)
        for fid in self.before:
            if fid == TARGET:
                continue
            self.assertEqual(after[fid]['id'], self.before[fid]['id'], fid)
            self.assertEqual(after[fid]['calculation_input_hash'],
                             self.before[fid]['calculation_input_hash'], fid)
        # Плоский счётчик без цепочки остаётся человеку.
        self.assertEqual(acc.classify(after[LONE])['reason'],
                         acc.R_APPLICATION_WITH_FLAT_COUNTER)

    def test_raw_is_kept_and_billable_stays_empty(self):
        # 18, 19.
        self.recalc(apply=True)
        after = current_rows(self.fx.db)
        for fid, row in after.items():
            self.assertEqual(row['raw_area_m2'], self.before[fid]['raw_area_m2'])
        self.assertEqual(after[TARGET]['corrected_recorded_area_m2'], 0.0)
        con = sqlite3.connect(self.fx.db)
        try:
            self.assertEqual(con.execute(
                'SELECT COUNT(*) FROM dji_area_calculations WHERE '
                'billable_area_m2 IS NOT NULL').fetchone()[0], 0)
        finally:
            con.close()

    def test_a_second_apply_writes_nothing(self):
        # 20.
        self.recalc(apply=True)
        self.assertEqual(self.recalc(apply=True)['calc_writes'],
                         {'unchanged': 4})

    def test_rolling_the_code_back_reactivates_the_previous_row(self):
        self.recalc(apply=True)
        with rule_off():
            back = self.recalc(apply=True)
        self.assertEqual(back['calc_writes'],
                         {'reactivated': 1, 'unchanged': 3})
        self.assertEqual(current_rows(self.fx.db)[TARGET]['id'],
                         self.before[TARGET]['id'])

    def test_decisions_on_unaffected_rows_do_not_go_stale(self):
        # 22. Решение запоминает (версия, отпечаток) строки, против которой
        # принято; «устарело» -- значит, строка сменилась.
        decisions = {fid: {'decision_type': dec.KEEP_DJI_RAW,
                           'area_algorithm_version':
                               row['area_algorithm_version'],
                           'calculation_input_hash':
                               row['calculation_input_hash']}
                     for fid, row in self.before.items()}
        self.recalc(apply=True)
        after = current_rows(self.fx.db)
        for fid, row in after.items():
            self.assertIs(dec.decision_is_stale(decisions[fid], row),
                          fid == TARGET, fid)

    def test_an_unreadable_v4_body_is_not_proof(self):
        con = store.connect(self.fx.db)
        try:
            rev_id = con.execute(
                'SELECT v4_revision_id FROM dji_flight_evidence WHERE '
                'flight_id=?', (TARGET,)).fetchone()[0]
            rev = store.revision_by_id(con, rev_id)
        finally:
            con.close()
        body = os.path.join(store.source_root(os.path.abspath(self.fx.db)),
                            rev['body_path'].replace('/', os.sep))
        os.rename(body, body + '.away')
        try:
            self.assertEqual(self.recalc()['calc_writes'], {'unchanged': 4})
        finally:
            os.rename(body + '.away', body)
        self.assertEqual(self.recalc()['calc_writes'],
                         {'unchanged': 3, 'would_write': 1})

    def test_the_body_is_read_again_only_for_the_retained_candidate(self):
        # LONE тоже плоский и с распылением, но без цепочки: его тело V4 не
        # перечитывается -- правило к нему неприменимо при любом следе.
        with mock.patch.object(pl, 'ensure_retained_footprint',
                               wraps=pl.ensure_retained_footprint) as spy:
            self.recalc()
        self.assertEqual([c.args[2]['flight_id'] for c in spy.call_args_list],
                         [TARGET])

    def test_a_mismatched_identity_gets_no_footprint(self):
        # 16.
        con = sqlite3.connect(self.fx.db)
        con.execute('UPDATE dji_flight_evidence SET v4_identity_status=? '
                    'WHERE flight_id=?', (ev.V4_MISMATCH, TARGET))
        con.commit()
        con.close()
        with mock.patch.object(pl, 'ensure_retained_footprint',
                               wraps=pl.ensure_retained_footprint) as spy:
            dry = self.recalc()
        self.assertNotIn(TARGET, [c.args[2]['flight_id']
                                  for c in spy.call_args_list])
        line = {r['flight_id']: r for r in dry['flights']}[TARGET]
        self.assertNotIn(rs.F_RETAINED_NEGLIGIBLE_FOOTPRINT,
                         line['anomaly_flags'])

    def test_a_read_only_dry_run_writes_nothing_and_refuses_to_apply(self):
        before = sha256(self.fx.db)
        out = pl.recalculate(self.fx.db, DAY, DAY, read_only=True,
                             collect_rows=True)
        self.assertEqual(out['calc_writes'],
                         {'unchanged': 3, 'would_write': 1})
        self.assertEqual(sha256(self.fx.db), before)
        for line in out['flights']:
            self.assertIsNone(line['billable_area_m2'])
        with self.assertRaises(pl.PipelineError):
            pl.recalculate(self.fx.db, DAY, DAY, apply=True, read_only=True)


class SelectiveVersionContract(unittest.TestCase):
    """Контракт версий `dji_area/__init__.py`, способ 2."""

    def test_every_selective_rule_is_declared_once_with_a_version(self):
        flags = [flag for flag, _key, _snap in rs.SELECTIVE_RULES]
        keys = [key for _flag, key, _snap in rs.SELECTIVE_RULES]
        self.assertEqual(len(flags), len(set(flags)))
        self.assertEqual(len(keys), len(set(keys)))
        self.assertIn(rs.F_RETAINED_NEGLIGIBLE_FOOTPRINT, flags)
        for _flag, _key, snapshot in rs.SELECTIVE_RULES:
            self.assertTrue(snapshot()['rule_version'])

    def test_a_row_without_a_selective_flag_gets_no_mark(self):
        for flags in ([], [rs.F_APPLICATION_WITH_FLAT_COUNTER, 'V4_MISSING']):
            self.assertEqual(pl.selective_rule_marks(flags), {})

    def test_the_mark_carries_the_frozen_parameters(self):
        marks = pl.selective_rule_marks([rs.F_RETAINED_NEGLIGIBLE_FOOTPRINT])
        snap = marks['retained_footprint_rule']
        self.assertEqual(snap['rule_version'],
                         dji_area.RETAINED_FOOTPRINT_RULE_VERSION)
        self.assertEqual(snap['footprint_to_raw_max'], 0.027)
        self.assertEqual(snap['width_envelope_m'], 12.0)

    def test_the_mark_changes_the_fingerprint_and_its_absence_does_not(self):
        sources = {'list': 'a', 'card': None, 'route': None, 'v4': 'b'}
        base = calculation_input_hash(sources, [], True, extra={'k': 1})
        marked = dict({'k': 1}, **pl.selective_rule_marks(
            [rs.F_RETAINED_NEGLIGIBLE_FOOTPRINT]))
        self.assertNotEqual(
            calculation_input_hash(sources, [], True, extra=marked), base)
        unmarked = dict({'k': 1}, **pl.selective_rule_marks([]))
        self.assertEqual(
            calculation_input_hash(sources, [], True, extra=unmarked), base)

    def test_the_rule_version_stays_out_of_the_shared_configuration(self):
        shared = canonical_json(resolver_config_snapshot())
        self.assertNotIn(dji_area.RETAINED_FOOTPRINT_RULE_VERSION, shared)

    def test_the_algorithm_version_does_not_move(self):
        self.assertTrue(dji_area.AREA_ALGORITHM_VERSION.endswith('-impl-4'))
        self.assertEqual(dji_area.V4_PARSER_VERSION, 'v4-parse-1')


if __name__ == '__main__':
    unittest.main()
