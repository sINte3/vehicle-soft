# -*- coding: utf-8 -*-
"""Acceptance matrix of the DJI area evidence model, executable.

Source: VEHICLE_SOFT_DJI_AREA_ACCEPTANCE_TESTS.md (model
dji-area-evidence-2026-09-08-final-1). Every case below is a row of that
matrix or a cross-cutting rule from it. Numbers are m2 unless stated.

Pure modules only: no Flask, no database. V4 bodies are SYNTHETIC protobuf
built by the helpers at the top; the field numbers are the ones proved on
the August corpus (dji_area/v4.py). Nothing here is a real flight.
"""

import os
import struct
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from dji_area import aggregate as agg  # noqa: E402
from dji_area import field as fld  # noqa: E402
from dji_area import hashing  # noqa: E402
from dji_area import resolver as rs  # noqa: E402
from dji_area import structural as st  # noqa: E402
from dji_area import v4  # noqa: E402


# ─── Synthetic protobuf builders ─────────────────────────────────────────────

def _varint(value):
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def f_varint(number, value):
    return _varint((number << 3) | 0) + _varint(value)


def f_f32(number, value):
    return _varint((number << 3) | 5) + struct.pack('<f', value)


def f_f64(number, value):
    return _varint((number << 3) | 1) + struct.pack('<d', value)


def f_bytes(number, payload):
    return _varint((number << 3) | 2) + _varint(len(payload)) + payload


def frame(t_ms, area=None, mode=4, width=6.0, spray_flag=None, flow=None,
          quantity=None, lat=39.9, lng=64.4, vx=3.0, vy=0.0):
    body = f_varint(v4.F_TIME_MS, t_ms)
    body += f_bytes(v4.F_POSITION, f_f64(1, lng) + f_f64(2, lat))
    body += f_bytes(v4.F_VELOCITY, f_f32(1, vx) + f_f32(2, vy))
    if mode is not None:
        body += f_varint(v4.F_WORK_MODE, mode)
    if width is not None:
        body += f_f32(v4.F_SPRAY_WIDTH, width)
    spray = b''
    if spray_flag:
        spray += f_varint(v4.S_SPRAY_FLAG, spray_flag)
    if flow:
        spray += f_varint(v4.S_FLOW, flow)
    if quantity is not None:
        spray += f_varint(v4.S_QUANTITY, quantity)
    body += f_bytes(v4.F_SPRAY, spray)
    if area is not None:
        body += f_f32(v4.F_AREA_STATE, area)
    return body


def v4_bytes(frames, count=None, usage_type=None):
    out = b''
    if count is not None:
        out += f_varint(v4.TOP_COUNT, count)
    for fr in frames:
        out += f_bytes(v4.TOP_FRAME, fr)
    if usage_type is not None:
        out += f_varint(v4.TOP_USAGE_TYPE, usage_type)
    return out


START = 1785526013          # unix s, synthetic
MS = START * 1000


def counter_series(values, start_ms=MS, step_ms=100, **kw):
    """values: list of native counter values or None (omitted)."""
    return [frame(start_ms + i * step_ms, area=v, **kw)
            for i, v in enumerate(values)]


def summary_for(frames, start_ts=START, end_ts=None, count=None):
    if end_ts is None:
        end_ts = START + int(round((len(frames) - 1) * 0.1))
    decoded = v4.decode_v4(v4_bytes(frames, count=count))
    return v4.summarize_v4(decoded, start_ts, end_ts)


def evidence(raw, frames=None, channel=rs.CH_INFORMATIVE, structural=None,
             overlap=None, route=None, identity_ok=True, **summary_kw):
    ev = {'raw_area_m2': raw, 'raw_area_source': 'list',
          'channel_quality': channel, 'structural': structural,
          'overlap': overlap, 'route': route}
    if frames is not None:
        ev['v4'] = {'summary': summary_for(frames, **summary_kw),
                    'identity_ok': identity_ok}
    return ev


MU = v4.COUNTER_M2_PER_NATIVE


# ─── V4 decoder: presence semantics ──────────────────────────────────────────

class V4Presence(unittest.TestCase):

    def test_omitted_field_9_is_absent_not_zero(self):
        frames = counter_series([None, None, 0.5, 0.5])
        s = summary_for(frames)
        self.assertEqual(s['frame_count'], 4)
        self.assertEqual(s['counter_present_frames'], 2)
        self.assertEqual(s['counter_leading_omitted_frames'], 2)
        self.assertAlmostEqual(s['counter_observed_delta_native'], 0.0)
        # zero-default sees a jump of 0.5 mu that nobody measured
        self.assertAlmostEqual(s['counter_zero_default_delta_native'], 0.5)

    def test_channel_missing_when_no_frame_carries_field_9(self):
        frames = counter_series([None] * 5, spray_flag=1, flow=100)
        s = summary_for(frames)
        wq, baseline, reasons = v4.evaluate_window(s)
        self.assertEqual(wq, v4.WINDOW_CHANNEL_MISSING)
        self.assertEqual(baseline, v4.BASELINE_UNKNOWN)
        self.assertIn('AREA_CHANNEL_ABSENT', reasons)
        self.assertEqual(s['application_frames'], 5)

    def test_float32_bits_are_stored(self):
        s = summary_for(counter_series([14.43, 14.43]))
        self.assertEqual(s['counter_first_encoded_bits'],
                         s['counter_last_encoded_bits'])
        self.assertTrue(v4.is_exactly_flat(s))
        self.assertAlmostEqual(s['counter_first_encoded_native'],
                               struct.unpack('<f', struct.pack('<f', 14.43))[0])

    def test_scale_is_2000_over_3(self):
        s = summary_for(counter_series([0.0, 15.0]))
        self.assertAlmostEqual(s['counter_observed_delta_m2'], 10000.0, 3)
        self.assertAlmostEqual(v4.COUNTER_QUANTUM_M2, 6.6667, 3)

    def test_decoder_refuses_garbage(self):
        with self.assertRaises(v4.V4DecodeError):
            v4.decode_v4(b'\x07\x07\x07')
        with self.assertRaises(v4.V4DecodeError):
            v4.decode_v4(b'')

    def test_top_count_is_not_frame_count(self):
        s = summary_for(counter_series([0.0, 0.1]), count=4246)
        self.assertEqual(s['top_count'], 4246)
        self.assertIn('TOP_COUNT_NE_FRAMES', s['warnings'])


# ─── Window gates ────────────────────────────────────────────────────────────

class WindowGates(unittest.TestCase):

    def test_good_window(self):
        s = summary_for(counter_series([0.0, 0.1, 0.2]))
        wq, baseline, reasons = v4.evaluate_window(s)
        self.assertEqual((wq, baseline, reasons),
                         (v4.WINDOW_GOOD, v4.BASELINE_ENCODED_AT_BOUNDARY, []))

    def test_single_frame_is_incomplete(self):
        s = summary_for(counter_series([0.0]), end_ts=START)
        wq, _b, reasons = v4.evaluate_window(s)
        self.assertEqual(wq, v4.WINDOW_INCOMPLETE)
        self.assertIn('FEWER_THAN_2_FRAMES', reasons)

    def test_offset_over_1_01s_is_incomplete(self):
        s = summary_for(counter_series([0.0, 0.1, 0.2]), start_ts=START - 2)
        wq, _b, reasons = v4.evaluate_window(s)
        self.assertEqual(wq, v4.WINDOW_INCOMPLETE)
        self.assertIn('START_OFFSET_OVER_1_01S', reasons)

    def test_offset_uncheckable_without_record_timestamps(self):
        decoded = v4.decode_v4(v4_bytes(counter_series([0.0, 0.1])))
        s = v4.summarize_v4(decoded, None, None)
        wq, _b, reasons = v4.evaluate_window(s)
        self.assertEqual(wq, v4.WINDOW_INCOMPLETE)
        self.assertIn('START_OFFSET_UNCHECKABLE', reasons)

    def test_dt_gap_over_1s_is_incomplete_not_padded(self):
        frames = [frame(MS, area=0.0), frame(MS + 100, area=0.1),
                  frame(MS + 2100, area=0.2)]
        s = summary_for(frames, end_ts=START + 2)
        wq, _b, reasons = v4.evaluate_window(s)
        self.assertEqual(wq, v4.WINDOW_INCOMPLETE)
        self.assertIn('DT_GAP_OVER_1S', reasons)
        self.assertEqual(s['frame_count'], 3)  # nothing padded

    def test_nonpositive_dt_is_incomplete(self):
        frames = [frame(MS, area=0.0), frame(MS, area=0.1),
                  frame(MS + 100, area=0.2)]
        s = summary_for(frames, end_ts=START)
        wq, _b, reasons = v4.evaluate_window(s)
        self.assertEqual(wq, v4.WINDOW_INCOMPLETE)
        self.assertIn('NONPOSITIVE_DT', reasons)

    def test_negative_step_is_nonmonotone(self):
        s = summary_for(counter_series([1.0, 1.5, 1.2, 1.6]))
        wq, _b, reasons = v4.evaluate_window(s)
        self.assertEqual(wq, v4.WINDOW_NONMONOTONE)
        self.assertEqual(s['counter_negative_steps'], 1)

    def test_identity_error_beats_everything(self):
        s = summary_for(counter_series([0.0, 0.1]))
        wq, baseline, _r = v4.evaluate_window(s, identity_ok=False)
        self.assertEqual(wq, v4.WINDOW_IDENTITY_ERROR)
        self.assertEqual(baseline, v4.BASELINE_UNKNOWN)

    def test_continuity_is_never_asserted(self):
        s = summary_for(counter_series([None, 5.0, 5.2]))
        _wq, baseline, _r = v4.evaluate_window(s)
        self.assertEqual(baseline, v4.BASELINE_UNVERIFIED_OMISSION)
        self.assertNotEqual(baseline, v4.BASELINE_VERIFIED_CONTINUITY)


# ─── Area resolver: the matrix ───────────────────────────────────────────────

class AreaMatrix(unittest.TestCase):

    def test_normal_full_window(self):
        # A=10000, encoded 0 -> 15 mu, complete identified window
        d = rs.resolve_area(evidence(10000.0, counter_series([0.0, 5.0, 15.0])))
        self.assertEqual(d.area_status, rs.RAW_CORROBORATED)
        self.assertEqual(d.corrected_recorded_area_m2, 10000.0)
        self.assertAlmostEqual(d.controller_delta_area_m2, 10000.0, 3)
        self.assertEqual(d.area_method, rs.M_RAW_WITH_VALIDATED_COUNTER)
        self.assertEqual(d.area_confidence, rs.C_HIGH)
        self.assertEqual(d.aggregation_eligibility, rs.AGG_CERTIFIED)

    def test_small_normal_673501214_leading_omission(self):
        # A=373; first present 0.01 -> 0.56 : observed 366.667, default 373.333
        values = [None] * 4 + [0.01, 0.3, 0.56]
        d = rs.resolve_area(evidence(373.0, counter_series(values)))
        self.assertEqual(d.area_status, rs.RAW_CORROBORATED_QUALIFIED)
        self.assertEqual(d.corrected_recorded_area_m2, 373.0)
        self.assertIsNone(d.controller_delta_area_m2)
        self.assertAlmostEqual(d.counter_observed_delta_m2, 366.667, 2)
        self.assertAlmostEqual(d.counter_zero_default_delta_m2, 373.333, 2)
        self.assertEqual(d.counter_baseline_status,
                         v4.BASELINE_UNVERIFIED_OMISSION)
        self.assertEqual(d.aggregation_eligibility, rs.AGG_PROVISIONAL)

    def test_large_normal_677850572_flagless_aircraft(self):
        values = [None] * 3 + [0.02, 30.0, 51.17]
        ev = evidence(34106.0, counter_series(values), channel=rs.CH_UNRELIABLE)
        d = rs.resolve_area(ev)
        self.assertEqual(d.area_status, rs.RAW_CORROBORATED_QUALIFIED)
        self.assertEqual(d.application_activity, rs.ACT_UNKNOWN)
        self.assertNotEqual(d.application_activity, rs.ACT_NOT_OBSERVED)
        self.assertEqual(d.application_evidence_kind, rs.EK_UNRELIABLE)
        self.assertIsNone(d.application_without_area)
        self.assertNotEqual(d.corrected_recorded_area_m2, 0.0)

    def test_manual_675819746_null_width_full_window(self):
        values = [5.42, 7.0, 8.85]
        d = rs.resolve_area(evidence(2286.0, counter_series(
            values, mode=1, width=None, spray_flag=1, flow=500)))
        self.assertEqual(d.area_status, rs.RAW_CORROBORATED)
        self.assertAlmostEqual(d.controller_delta_area_m2, 2286.667, 2)
        self.assertEqual(d.corrected_recorded_area_m2, 2286.0)

    def test_null_width_real_work_675819715(self):
        values = [None] * 2 + [0.01, 4.0, 8.83]
        d = rs.resolve_area(evidence(5886.0, counter_series(
            values, width=None, spray_flag=1, flow=800)))
        self.assertEqual(d.area_status, rs.RAW_CORROBORATED_QUALIFIED)
        self.assertEqual(d.application_activity, rs.ACT_PRESENT)
        self.assertIsNone(d.structural_candidate)  # no screen input given

    def test_stale_flat_688669423(self):
        structural = {'applicable': True, 'candidate': True,
                      'base_flight_id': 688669418,
                      'bridge_flight_ids': [688669421],
                      'boundary_gaps_s': [0.0, 0.0],
                      'scalar_source_check': True}
        d = rs.resolve_area(evidence(8980.0, counter_series([14.43] * 6),
                                     channel=rs.CH_UNRELIABLE,
                                     structural=structural))
        self.assertEqual(d.area_status, rs.COUNTER_FLAT_RAW_OVERSTATED)
        self.assertEqual(d.controller_delta_area_m2, 0.0)
        self.assertEqual(d.corrected_recorded_area_m2, 0.0)
        self.assertEqual(d.raw_area_m2, 8980.0)
        self.assertIn('RETAINED_SCALAR_SOURCE_MATCH', d.anomaly_flags)
        self.assertEqual(d.application_activity, rs.ACT_UNKNOWN)

    def test_stale_tiny_positive_684409277(self):
        d = rs.resolve_area(evidence(12413.0, counter_series([19.39, 19.39, 19.45])))
        self.assertEqual(d.area_status, rs.PARTIAL_RECORDED_OVERSTATEMENT)
        self.assertAlmostEqual(d.corrected_recorded_area_m2, 40.0, 0)
        self.assertGreater(d.corrected_recorded_area_m2, 0.0)
        self.assertEqual(d.raw_area_m2, 12413.0)

    def test_one_quantum_positive_674061957(self):
        d = rs.resolve_area(evidence(3580.0, counter_series([13.76, 13.76, 13.77])))
        self.assertEqual(d.area_status, rs.PARTIAL_RECORDED_OVERSTATEMENT)
        self.assertAlmostEqual(d.corrected_recorded_area_m2, 6.667, 1)
        self.assertNotEqual(d.corrected_recorded_area_m2, 0.0)

    def test_width_present_hidden_stale_687623234(self):
        d = rs.resolve_area(evidence(18533.0, counter_series([27.8] * 5, width=7.4)))
        self.assertEqual(d.area_status, rs.COUNTER_FLAT_RAW_OVERSTATED)
        self.assertEqual(d.controller_delta_area_m2, 0.0)
        # structural screen would not flag it (width present) -- and must not
        self.assertIsNone(d.structural_candidate)

    def test_raw_zero_application_690480852(self):
        frames = counter_series([None] * 6, mode=0, spray_flag=1, flow=900,
                                quantity=10)
        d = rs.resolve_area(evidence(0.0, frames))
        self.assertEqual(d.area_status, rs.APPLICATION_WITHOUT_MEASURED_AREA)
        self.assertIsNone(d.corrected_recorded_area_m2)
        self.assertEqual(d.raw_area_m2, 0.0)
        self.assertIs(d.application_without_area, True)
        self.assertEqual(d.counter_window_quality, v4.WINDOW_CHANNEL_MISSING)

    def test_raw_zero_no_activity_field_9_absent(self):
        frames = counter_series([None] * 6, mode=0)
        d = rs.resolve_area(evidence(0.0, frames))
        self.assertEqual(d.area_status, rs.CHANNEL_MISSING)
        self.assertIsNone(d.corrected_recorded_area_m2)
        self.assertEqual(d.application_activity, rs.ACT_NOT_OBSERVED)
        d2 = rs.resolve_area(evidence(0.0, frames, channel=rs.CH_UNKNOWN))
        self.assertEqual(d2.application_activity, rs.ACT_UNKNOWN)

    def test_raw_zero_validated_flat_counter(self):
        d = rs.resolve_area(evidence(0.0, counter_series([3.0] * 5)))
        self.assertEqual(d.area_status, rs.COUNTER_ZERO)
        self.assertEqual(d.controller_delta_area_m2, 0.0)
        self.assertEqual(d.corrected_recorded_area_m2, 0.0)
        # measured technical zero is a different thing from NULL above
        self.assertIsNotNone(d.corrected_recorded_area_m2)

    def test_v4_absent_ordinary(self):
        d = rs.resolve_area(evidence(4693.0, None))
        self.assertEqual(d.area_status, rs.RAW_UNVERIFIED)
        self.assertEqual(d.corrected_recorded_area_m2, 4693.0)
        self.assertIsNone(d.controller_delta_area_m2)
        self.assertIn('V4_MISSING', d.anomaly_flags)
        self.assertEqual(d.aggregation_eligibility, rs.AGG_PROVISIONAL)

    def test_v4_absent_suspicious_694956303(self):
        structural = {'applicable': True, 'candidate': True,
                      'base_flight_id': 695027340, 'bridge_flight_ids': [1],
                      'boundary_gaps_s': [0.0, 1.0], 'scalar_source_check': True}
        d = rs.resolve_area(evidence(4693.0, None, structural=structural))
        self.assertEqual(d.area_status, rs.UNKNOWN_SUSPECT)
        self.assertIsNone(d.corrected_recorded_area_m2)
        self.assertEqual(d.raw_area_m2, 4693.0)
        self.assertEqual(d.aggregation_eligibility, rs.AGG_UNRESOLVED)

    def test_structural_candidate_without_scalar_match_is_not_suspect(self):
        structural = {'applicable': True, 'candidate': True,
                      'base_flight_id': 1, 'bridge_flight_ids': [2],
                      'boundary_gaps_s': [0.0, 0.0], 'scalar_source_check': False}
        d = rs.resolve_area(evidence(4693.0, None, structural=structural))
        self.assertEqual(d.area_status, rs.RAW_UNVERIFIED)

    def test_v4_incomplete_falls_back_never_flat(self):
        frames = [frame(MS, area=5.0), frame(MS + 100, area=5.0),
                  frame(MS + 3000, area=5.0)]
        d = rs.resolve_area(evidence(9000.0, frames, end_ts=START + 3))
        self.assertEqual(d.area_status, rs.RAW_UNVERIFIED)
        self.assertIsNone(d.controller_delta_area_m2)
        self.assertIn('V4_WINDOW_INCOMPLETE', d.anomaly_flags)
        self.assertNotEqual(d.corrected_recorded_area_m2, 0.0)

    def test_application_channel_absent_on_usable_counter(self):
        frames = counter_series([0.0, 7.5, 15.0])  # no spray fields at all
        d = rs.resolve_area(evidence(10000.0, frames, channel=rs.CH_UNKNOWN))
        self.assertEqual(d.area_status, rs.RAW_CORROBORATED)
        self.assertEqual(d.application_activity, rs.ACT_UNKNOWN)
        self.assertIn('APPLICATION_CHANNEL_UNKNOWN', d.anomaly_flags)

    def test_gijduvon_flagless_counter_validates(self):
        self.assertEqual(rs.channel_quality_for('1581F5742255T0C1L061', True),
                         rs.CH_UNRELIABLE)
        frames = counter_series([0.0, 10.0, 15.0])
        d = rs.resolve_area(evidence(10000.0, frames, channel=rs.CH_UNRELIABLE))
        self.assertEqual(d.area_status, rs.RAW_CORROBORATED)
        self.assertEqual(d.application_activity, rs.ACT_UNKNOWN)
        self.assertEqual(d.application_evidence_kind, rs.EK_UNRELIABLE)

    def test_repeated_equal_scalars_both_normal(self):
        a = rs.resolve_area(evidence(8980.0, counter_series([0.0, 13.47])))
        b = rs.resolve_area(evidence(8980.0, counter_series([13.47, 26.94])))
        self.assertEqual(a.area_status, rs.RAW_CORROBORATED)
        self.assertEqual(b.area_status, rs.RAW_CORROBORATED)

    def test_overlap_review_excludes_from_certified(self):
        overlap = {'group_id': 'overlap:1', 'conflict': True}
        d = rs.resolve_area(evidence(10000.0, counter_series([0.0, 15.0]),
                                     overlap=overlap))
        self.assertEqual(d.area_status, rs.OVERLAP_REVIEW)
        self.assertEqual(d.evidence_status, rs.RAW_CORROBORATED)
        self.assertIsNone(d.corrected_recorded_area_m2)
        self.assertEqual(d.raw_area_m2, 10000.0)
        self.assertAlmostEqual(d.controller_delta_area_m2, 10000.0, 3)
        self.assertEqual(d.aggregation_eligibility, rs.AGG_EXCLUDED_OVERLAP)

    def test_route_identity_error_does_not_touch_area(self):
        route = {'identity_status': 'ROUTE_IDENTITY_ERROR'}
        d = rs.resolve_area(evidence(10000.0, counter_series([0.0, 15.0]),
                                     route=route))
        self.assertEqual(d.area_status, rs.RAW_CORROBORATED)
        self.assertIn('ROUTE_IDENTITY_ERROR', d.anomaly_flags)

    def test_v4_identity_conflict(self):
        d = rs.resolve_area(evidence(10000.0, counter_series([0.0, 15.0]),
                                     identity_ok=False))
        self.assertEqual(d.area_status, rs.UNKNOWN_SUSPECT)
        self.assertIsNone(d.controller_delta_area_m2)
        self.assertEqual(d.counter_window_quality, v4.WINDOW_IDENTITY_ERROR)

    def test_counter_reset_review_no_positive_jump_sum(self):
        d = rs.resolve_area(evidence(10000.0, counter_series([10.0, 20.0, 5.0, 15.0])))
        self.assertEqual(d.area_status, rs.COUNTER_NONMONOTONE_REVIEW)
        self.assertIsNone(d.controller_delta_area_m2)
        self.assertIsNone(d.corrected_recorded_area_m2)

    def test_baseline_unknown_685264927(self):
        values = [None] * 10 + [13.61] * 8
        d = rs.resolve_area(evidence(9066.0, counter_series(values)))
        self.assertEqual(d.area_status, rs.BASELINE_UNKNOWN)
        self.assertIsNone(d.controller_delta_area_m2)
        self.assertIsNone(d.corrected_recorded_area_m2)
        self.assertAlmostEqual(d.counter_observed_delta_m2, 0.0)
        self.assertAlmostEqual(d.counter_zero_default_delta_m2, 9073.333, 2)
        self.assertEqual(d.counter_window_quality, v4.WINDOW_BASELINE_UNKNOWN)
        self.assertIn('ZERO_DEFAULT_DELTA_IS_DIAGNOSTIC_ONLY', d.anomaly_flags)
        self.assertEqual(d.aggregation_eligibility, rs.AGG_UNRESOLVED)

    def test_residual_outlier_692752823(self):
        # observed subwindow 4.2 mu = 2800; default 2813; RAW 2626
        values = [None] * 2 + [0.02, 2.0, 4.22]
        d = rs.resolve_area(evidence(2626.0, counter_series(values)))
        self.assertEqual(d.area_status, rs.COUNTER_RELATIONSHIP_OUTLIER)
        self.assertIsNone(d.corrected_recorded_area_m2)
        self.assertAlmostEqual(d.counter_observed_delta_m2, 2800.0, 0)
        self.assertAlmostEqual(d.counter_zero_default_delta_m2, 2813.333, 0)

    def test_full_window_magnitude_outlier(self):
        d = rs.resolve_area(evidence(2626.0, counter_series([0.0, 4.22])))
        self.assertEqual(d.area_status, rs.COUNTER_RELATIONSHIP_OUTLIER)
        self.assertAlmostEqual(d.controller_delta_area_m2, 2813.333, 0)
        self.assertIsNone(d.corrected_recorded_area_m2)

    def test_quantity_only_is_not_spray_proof(self):
        frames = counter_series([None] * 4, mode=0, quantity=100)
        frames[0] = frame(MS, mode=0, quantity=0)
        d = rs.resolve_area(evidence(0.0, frames))
        self.assertEqual(d.application_activity, rs.ACT_UNKNOWN)
        self.assertEqual(d.application_evidence_kind, rs.EK_QUANTITY_ONLY)
        self.assertIn('QUANTITY_INCREASE_WITHOUT_AREA', d.anomaly_flags)
        self.assertNotEqual(d.area_status, rs.APPLICATION_WITHOUT_MEASURED_AREA)
        self.assertIsNone(d.application_without_area)

    def test_no_auto_zero_hard_requirement(self):
        """A duplicate candidate without validated interval never becomes 0."""
        structural = {'applicable': True, 'candidate': True,
                      'base_flight_id': 1, 'bridge_flight_ids': [2],
                      'boundary_gaps_s': [0.0, 0.0], 'scalar_source_check': True}
        for frames in (None,
                       counter_series([None] * 3 + [5.0, 5.0]),
                       [frame(MS, area=5.0), frame(MS + 5000, area=5.0)]):
            d = rs.resolve_area(evidence(8980.0, frames, structural=structural,
                                         end_ts=START + 5))
            self.assertNotEqual(d.corrected_recorded_area_m2, 0.0, d.area_status)
            self.assertNotEqual(d.controller_delta_area_m2, 0.0, d.area_status)

    def test_raw_missing_never_becomes_zero(self):
        d = rs.resolve_area(evidence(None, counter_series([0.0, 15.0])))
        self.assertIsNone(d.raw_area_m2)
        self.assertIn('RAW_MISSING', d.anomaly_flags)
        self.assertNotEqual(d.corrected_recorded_area_m2, 0.0)

    def test_decision_is_deterministic(self):
        ev = evidence(373.0, counter_series([None] * 4 + [0.01, 0.3, 0.56]))
        a = rs.resolve_area(ev).as_dict()
        b = rs.resolve_area(ev).as_dict()
        self.assertEqual(a, b)


# ─── Structural screen ───────────────────────────────────────────────────────

def rec(fid, start, end, mode=4, width=6.0, raw=1000.0):
    return {'flight_id': fid, 'start_ts': start, 'end_ts': end,
            'mode_name': mode, 'spray_width': width, 'raw_area_m2': raw}


class StructuralScreen(unittest.TestCase):

    def test_frozen_chain_selects_base_before_scalar(self):
        chain = [rec(688669418, 0, 300, width=6.66, raw=8980.0),
                 rec(688669421, 300, 320, mode=1, width=None, raw=0.0),
                 rec(688669423, 320, 380, width=None, raw=8980.0)]
        r = st.screen(chain, 2)
        self.assertTrue(r['candidate'])
        self.assertEqual(r['base_flight_id'], 688669418)
        self.assertEqual(r['bridge_flight_ids'], [688669421])
        self.assertEqual(r['boundary_gaps_s'], [0.0, 0.0])
        self.assertIs(r['scalar_source_check'], True)

    def test_scalar_mismatch_keeps_candidate_but_fails_check(self):
        chain = [rec(1, 0, 300, width=6.66, raw=8980.0),
                 rec(2, 300, 320, mode=1, width=None, raw=0.0),
                 rec(3, 320, 380, width=None, raw=4000.0)]
        r = st.screen(chain, 2)
        self.assertTrue(r['candidate'])
        self.assertIs(r['scalar_source_check'], False)

    def test_gap_over_one_second_breaks_chain(self):
        chain = [rec(1, 0, 300, width=6.66, raw=8980.0),
                 rec(2, 302, 320, mode=1, width=None, raw=0.0),
                 rec(3, 320, 380, width=None, raw=8980.0)]
        r = st.screen(chain, 2)
        self.assertFalse(r['candidate'])
        self.assertEqual(r['reason'], 'NO_WIDTH_PRESENT_MODE4_BASE_IN_CHAIN')

    def test_no_bridge_is_not_a_candidate(self):
        chain = [rec(1, 0, 300, width=6.66, raw=8980.0),
                 rec(3, 300, 380, width=None, raw=8980.0)]
        r = st.screen(chain, 1)
        self.assertFalse(r['candidate'])
        self.assertEqual(r['reason'], 'NO_BRIDGE_RECORD')

    def test_width_present_target_is_not_applicable(self):
        chain = [rec(1, 0, 300, width=6.66), rec(2, 300, 320, mode=1, width=None),
                 rec(3, 320, 380, width=7.4)]
        r = st.screen(chain, 2)
        self.assertFalse(r['applicable'])

    def test_equality_alone_is_never_a_candidate(self):
        chain = [rec(1, 0, 300, width=6.66, raw=8980.0),
                 rec(3, 400, 480, width=None, raw=8980.0)]
        r = st.screen(chain, 1)
        self.assertFalse(r['candidate'])

    def test_midnight_does_not_break_chain(self):
        midnight = 1785531600  # any epoch; chain uses timestamps only
        chain = [rec(1, midnight - 100, midnight - 1, width=6.0, raw=500.0),
                 rec(2, midnight - 1, midnight + 5, mode=1, width=None, raw=0.0),
                 rec(3, midnight + 5, midnight + 60, width=None, raw=500.0)]
        self.assertTrue(st.screen(chain, 2)['candidate'])

    def test_overlap_groups(self):
        rows = [rec(1, 0, 100), rec(2, 50, 120), rec(3, 120, 200), rec(4, 300, 400)]
        groups = st.overlap_groups(rows)
        self.assertEqual(set(groups), {1, 2})
        self.assertEqual(groups[1], groups[2])


# ─── Field resolver ──────────────────────────────────────────────────────────

MD5_A = 'a' * 32
MD5_B = 'b' * 32
UUID_X = '11111111-2222-3333-4444-555555555555'
UUID_Y = '66666666-7777-8888-9999-aaaaaaaaaaaa'


def revision(uuid, name, md5, snapshot=1, rid=1):
    return {'land_uuid': uuid, 'name': name, 'serial_number': 'P1',
            'geometry_md5': md5, 'snapshot_id': snapshot,
            'land_revision_id': rid}


class FieldTiers(unittest.TestCase):

    def test_parse_formats(self):
        self.assertEqual(fld.parse_geometry_key(MD5_A),
                         (fld.KEY_PLAIN_MD5, None, MD5_A))
        self.assertEqual(fld.parse_geometry_key(UUID_X + '__' + MD5_A),
                         (fld.KEY_COMPOSITE, UUID_X, MD5_A))
        self.assertEqual(fld.parse_geometry_key(''), (fld.KEY_NONE, None, None))
        self.assertEqual(fld.parse_geometry_key(None), (fld.KEY_NONE, None, None))
        self.assertEqual(fld.parse_geometry_key('zzz__' + MD5_A)[0],
                         fld.KEY_UNRECOGNIZED)
        self.assertEqual(fld.parse_geometry_key(MD5_A[:31])[0],
                         fld.KEY_UNRECOGNIZED)

    def test_tier1_plain_md5_verified_bytes(self):
        cat = fld.DictCatalog(
            geometries={MD5_A: {'geometry_object_id': 7, 'sha256': 's',
                                'holder_land_uuids': [UUID_Y], 'verified': True}},
            lands={UUID_Y: [revision(UUID_Y, 'Karvon', MD5_A)]})
        r = fld.resolve_field(MD5_A, cat, flight_id=1)
        self.assertEqual(r['field_attribution_tier'], fld.TIER1_EXACT)
        self.assertEqual(r['geometry_holder_land_uuid'], UUID_Y)
        self.assertIsNone(r['linked_land_uuid'])
        self.assertTrue(r['historical_geometry_available'])
        self.assertEqual(r['field_name_at_snapshot'], 'Karvon')

    def test_tier1_composite_holder_differs_from_linked(self):
        cat = fld.DictCatalog(
            geometries={MD5_A: {'geometry_object_id': 7, 'sha256': 's',
                                'holder_land_uuids': [UUID_Y], 'verified': True}},
            lands={UUID_X: [revision(UUID_X, 'Zaminlari 1', MD5_B)],
                   UUID_Y: [revision(UUID_Y, 'Zaminlari 1', MD5_A, rid=2)]})
        r = fld.resolve_field(UUID_X + '__' + MD5_A, cat, flight_id=1)
        self.assertEqual(r['field_attribution_tier'], fld.TIER1_EXACT)
        self.assertEqual(r['linked_land_uuid'], UUID_X)
        self.assertEqual(r['geometry_holder_land_uuid'], UUID_Y)
        self.assertEqual(r['field_land_uuid'], UUID_X)
        self.assertIn('HOLDER_UUID_NE_LINKED_UUID', r['warnings'])
        self.assertEqual(r['field_attribution_method'],
                         'COMPOSITE_UUID_GEOMETRY_VIA_TWIN')

    def test_unverified_bytes_do_not_give_tier1(self):
        cat = fld.DictCatalog(
            geometries={MD5_A: {'geometry_object_id': 7, 'sha256': 's',
                                'holder_land_uuids': [UUID_Y], 'verified': False}},
            lands={UUID_X: [revision(UUID_X, 'F', MD5_B)]})
        r = fld.resolve_field(UUID_X + '__' + MD5_A, cat, flight_id=1)
        self.assertEqual(r['field_attribution_tier'], fld.TIER2_STRONG)

    def test_tier2_composite_uuid_only(self):
        cat = fld.DictCatalog(lands={UUID_X: [revision(UUID_X, 'F', MD5_B)]})
        r = fld.resolve_field(UUID_X + '__' + MD5_A, cat, flight_id=1)
        self.assertEqual(r['field_attribution_tier'], fld.TIER2_STRONG)
        self.assertFalse(r['historical_geometry_available'])
        self.assertIn('HISTORICAL_GEOMETRY_UNAVAILABLE', r['warnings'])
        self.assertEqual(r['field_land_uuid'], UUID_X)

    def test_tier2_plain_md5_known_to_catalog_without_bytes(self):
        # The catalog names a land whose contentMd5 equals the key, but the
        # bytes were never retained: identity strong, history unavailable.
        cat = fld.DictCatalog(lands={UUID_Y: [revision(UUID_Y, 'garden', MD5_A)]})
        r = fld.resolve_field(MD5_A, cat, flight_id=1)
        self.assertEqual(r['field_attribution_tier'], fld.TIER2_STRONG)
        self.assertEqual(r['field_attribution_method'],
                         'PLAIN_MD5_CATALOG_MATCH_BYTES_UNAVAILABLE')
        self.assertFalse(r['historical_geometry_available'])
        self.assertEqual(r['geometry_holder_land_uuid'], UUID_Y)
        self.assertEqual(r['field_name_at_snapshot'], 'garden')
        # negative control: md5 unknown to the catalog stays TIER5
        r2 = fld.resolve_field(MD5_B, cat, flight_id=1)
        self.assertEqual(r2['field_attribution_tier'], fld.TIER5_UNKNOWN)

    def test_tier3_lineage_via_other_flights(self):
        cat = fld.DictCatalog(
            geometries={MD5_B: {'geometry_object_id': 9, 'sha256': 't',
                                'holder_land_uuids': [UUID_Y], 'verified': True}},
            lands={UUID_Y: [revision(UUID_Y, 'Zaminlari', MD5_B)]},
            flight_keys={2: UUID_X + '__' + MD5_B, 3: UUID_X + '__' + MD5_B})
        r = fld.resolve_field(UUID_X + '__' + MD5_A, cat, flight_id=1)
        self.assertEqual(r['field_attribution_tier'], fld.TIER3_SUPPORTED)
        self.assertEqual(r['field_lineage_evidence_ids'], [2, 3])
        self.assertEqual(r['field_land_uuid'], UUID_Y)

    def test_tier3_conflict_becomes_unknown(self):
        cat = fld.DictCatalog(
            geometries={MD5_B: {'geometry_object_id': 9, 'sha256': 't',
                                'holder_land_uuids': [UUID_Y], 'verified': True},
                        'c' * 32: {'geometry_object_id': 10, 'sha256': 'u',
                                   'holder_land_uuids': ['9' * 8 + '-1111-2222-3333-444444444444'],
                                   'verified': True}},
            flight_keys={2: UUID_X + '__' + MD5_B, 3: UUID_X + '__' + 'c' * 32})
        r = fld.resolve_field(UUID_X + '__' + MD5_A, cat, flight_id=1)
        self.assertEqual(r['field_attribution_tier'], fld.TIER5_UNKNOWN)
        self.assertIn('LINEAGE_CONFLICT', r['warnings'])

    def test_tier4_single_candidate_only(self):
        square = [(39.0, 64.0), (39.0, 64.01), (39.01, 64.01), (39.01, 64.0)]
        polys = [{'land_uuid': UUID_Y, 'name': 'F', 'rings': [square],
                  'center': (39.005, 64.005), 'snapshot_id': 1}]
        pts = [(39.005 + i * 0.0001, 64.005) for i in range(10)]
        r = fld.resolve_field('', fld.DictCatalog(), flight_id=1,
                              route_points=pts, route_identity_ok=True,
                              current_polygons=polys)
        self.assertEqual(r['field_attribution_tier'], fld.TIER4_GEOMETRIC)
        self.assertEqual(r['field_confidence'], fld.CONF_LOW)
        self.assertFalse(r['historical_geometry_available'])
        # wrong-route identity forbids the geometric candidate
        r2 = fld.resolve_field('', fld.DictCatalog(), flight_id=1,
                               route_points=pts, route_identity_ok=False,
                               current_polygons=polys)
        self.assertEqual(r2['field_attribution_tier'], fld.TIER5_UNKNOWN)
        # two candidates -> ambiguous
        polys2 = polys + [dict(polys[0], land_uuid=UUID_X)]
        r3 = fld.resolve_field('', fld.DictCatalog(), flight_id=1,
                               route_points=pts, route_identity_ok=True,
                               current_polygons=polys2)
        self.assertEqual(r3['field_attribution_tier'], fld.TIER5_UNKNOWN)
        self.assertEqual(r3['candidate_count'], 2)

    def test_tier5_manual_keyless(self):
        r = fld.resolve_field('', fld.DictCatalog(), flight_id=1, manual_mode=True)
        self.assertEqual(r['field_attribution_tier'], fld.TIER5_UNKNOWN)
        self.assertEqual(r['field_attribution_method'], 'MANUAL_NO_KEY')

    def test_partial_snapshot_absence_is_unknown_not_deleted(self):
        cat = fld.DictCatalog()
        r = fld.resolve_field(UUID_X + '__' + MD5_A, cat, flight_id=1)
        self.assertEqual(r['field_attribution_tier'], fld.TIER5_UNKNOWN)
        self.assertEqual(r['field_attribution_method'],
                         'COMPOSITE_UUID_AND_MD5_NOT_IN_CATALOG')

    def test_same_bytes_under_two_uuids_keeps_both(self):
        cat = fld.DictCatalog(
            geometries={MD5_A: {'geometry_object_id': 7, 'sha256': 's',
                                'holder_land_uuids': [UUID_X, UUID_Y],
                                'verified': True}},
            lands={UUID_X: [revision(UUID_X, 'Usmon', MD5_A)],
                   UUID_Y: [revision(UUID_Y, 'Usmon', MD5_A, snapshot=2, rid=2)]})
        r = fld.resolve_field(MD5_A, cat, flight_id=1)
        self.assertEqual(r['field_attribution_tier'], fld.TIER1_EXACT)
        self.assertEqual(r['holder_count'], 2)
        self.assertIn('MULTIPLE_GEOMETRY_HOLDERS', r['warnings'])

    def test_name_with_fake_hectares_is_text(self):
        cat = fld.DictCatalog(lands={UUID_X: [revision(UUID_X, 'Zaminlari 39.7 ga', MD5_B)]})
        r = fld.resolve_field(UUID_X + '__' + MD5_A, cat, flight_id=1)
        self.assertEqual(r['field_name_at_snapshot'], 'Zaminlari 39.7 ga')
        self.assertNotIn('field_area', r)

    def test_rings_from_geojson_lng_lat_order(self):
        doc = {'type': 'FeatureCollection', 'features': [
            {'type': 'Feature', 'properties': {'funcType': 'PlantZone'},
             'geometry': {'type': 'Polygon',
                          'coordinates': [[[64.0, 39.0, 0], [64.01, 39.0, 0],
                                           [64.01, 39.01, 0], [64.0, 39.0, 0]]]}},
            {'type': 'Feature', 'properties': {'funcType': 'ObstacleZone'},
             'geometry': {'type': 'Polygon', 'coordinates': [[[1, 1], [2, 2], [3, 1]]]}}]}
        rings = fld.rings_from_geojson(doc)
        self.assertEqual(len(rings), 1)
        self.assertEqual(rings[0][0], (39.0, 64.0))


# ─── Aggregation ─────────────────────────────────────────────────────────────

class Aggregation(unittest.TestCase):

    def rows(self):
        return [
            {'day': 'd1', 'raw_area_m2': 10000.0, 'area_status': rs.RAW_CORROBORATED,
             'corrected_recorded_area_m2': 10000.0, 'controller_delta_area_m2': 10000.0,
             'aggregation_eligibility': rs.AGG_CERTIFIED,
             'field_attribution_tier': fld.TIER1_EXACT},
            {'day': 'd1', 'raw_area_m2': 373.0, 'area_status': rs.RAW_CORROBORATED_QUALIFIED,
             'corrected_recorded_area_m2': 373.0, 'controller_delta_area_m2': None,
             'aggregation_eligibility': rs.AGG_PROVISIONAL,
             'field_attribution_tier': fld.TIER5_UNKNOWN},
            {'day': 'd1', 'raw_area_m2': 9066.0, 'area_status': rs.BASELINE_UNKNOWN,
             'corrected_recorded_area_m2': None, 'controller_delta_area_m2': None,
             'aggregation_eligibility': rs.AGG_UNRESOLVED,
             'field_attribution_tier': fld.TIER2_STRONG},
            {'day': 'd1', 'raw_area_m2': 0.0, 'area_status': rs.APPLICATION_WITHOUT_MEASURED_AREA,
             'corrected_recorded_area_m2': None, 'application_without_area': True,
             'aggregation_eligibility': rs.AGG_UNRESOLVED,
             'application_channel_quality': rs.CH_INFORMATIVE},
            {'day': 'd1', 'raw_area_m2': 500.0, 'area_status': rs.OVERLAP_REVIEW,
             'corrected_recorded_area_m2': None, 'controller_delta_area_m2': 500.0,
             'aggregation_eligibility': rs.AGG_EXCLUDED_OVERLAP,
             'application_channel_quality': rs.CH_UNRELIABLE},
        ]

    def test_buckets_partition_and_exposure(self):
        out = agg.aggregate(self.rows(), lambda r: r['day'])
        b = out['d1']
        self.assertEqual(b['records'], 5)
        self.assertAlmostEqual(b['raw_sum_m2'], 19939.0)
        self.assertAlmostEqual(b['certified_sum_m2'], 10000.0)
        self.assertAlmostEqual(b['provisional_sum_m2'], 373.0)
        self.assertEqual(b['unresolved_records'], 2)
        self.assertAlmostEqual(b['unresolved_raw_exposure_m2'], 9066.0)
        self.assertEqual(b['overlap_records'], 1)
        self.assertAlmostEqual(b['overlap_raw_exposure_m2'], 500.0)
        self.assertEqual(b['application_without_area_records'], 1)
        self.assertEqual(b['unreliable_channel_records'], 1)
        self.assertTrue(b['eligibility_partition_holds'])
        self.assertTrue(b['tier_partition_holds'])
        self.assertEqual(b['tier_counts'][fld.TIER5_UNKNOWN], 3)
        self.assertEqual(b['unassigned_records'], 3)
        self.assertFalse(b['is_complete'])
        self.assertNotIn('total_m2', b)  # no pretended complete total
        self.assertEqual(out['__total__']['records'], 5)

    def test_null_stays_null_in_sums(self):
        rows = [{'raw_area_m2': None, 'area_status': rs.UNKNOWN_SUSPECT,
                 'corrected_recorded_area_m2': None,
                 'aggregation_eligibility': rs.AGG_UNRESOLVED}]
        b = agg.aggregate(rows, lambda r: 'k')['k']
        self.assertEqual(b['raw_missing_records'], 1)
        self.assertEqual(b['raw_sum_m2'], 0.0)
        self.assertEqual(b['raw_known_records'], 0)
        self.assertFalse(b['is_complete'])


# ─── Hashing ─────────────────────────────────────────────────────────────────

class Hashing(unittest.TestCase):

    def test_input_hash_is_deterministic_and_sensitive(self):
        a = hashing.calculation_input_hash({'list': 'x', 'v4': 'y'}, [(1, 'a')], True)
        b = hashing.calculation_input_hash({'v4': 'y', 'list': 'x'}, [(1, 'a')], True)
        c = hashing.calculation_input_hash({'list': 'x', 'v4': 'z'}, [(1, 'a')], True)
        d = hashing.calculation_input_hash({'list': 'x', 'v4': 'y'}, [(1, 'a')], False)
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertNotEqual(a, d)
        self.assertEqual(len(a), 64)

    def test_config_snapshot_names_thresholds(self):
        snap = hashing.resolver_config_snapshot()
        self.assertEqual(snap['agreement_abs_m2'], 20.0)
        self.assertEqual(snap['window_max_offset_s'], 1.01)
        self.assertIn('1581F5742255T0C1L061', snap['known_unreliable_hardware'])


# ─── Разбор состязательного ревью (DJI-AREA-EVIDENCE-001) ───────────────────

class AdjudicatedFindings(unittest.TestCase):
    """Подтверждённые находки состязательного ревью и их отрицательные
    контроли. Каждый тест обязан РАЗЛИЧАТЬ исправленный и прежний код."""

    def test_mismatched_v4_cannot_assert_application_at_raw_zero(self):
        # Файл снят по чужому пути: его флаги распыления -- о другом вылете.
        frames = counter_series([None] * 6, mode=0, spray_flag=1, flow=900)
        d = rs.resolve_area(evidence(0.0, frames, identity_ok=False))
        self.assertEqual(d.area_status, rs.ZERO_RECORDED_UNVERIFIED)
        self.assertEqual(d.application_activity, rs.ACT_UNKNOWN)
        self.assertIsNone(d.application_without_area)
        self.assertIn('V4_IDENTITY_APPLICATION_EVIDENCE_EXCLUDED',
                      d.anomaly_flags)

    def test_control_the_same_v4_with_its_own_identity_does_assert_it(self):
        frames = counter_series([None] * 6, mode=0, spray_flag=1, flow=900)
        d = rs.resolve_area(evidence(0.0, frames, identity_ok=True))
        self.assertEqual(d.area_status, rs.APPLICATION_WITHOUT_MEASURED_AREA)
        self.assertEqual(d.application_activity, rs.ACT_PRESENT)
        self.assertTrue(d.application_without_area)

    def test_mismatched_v4_keeps_its_diagnostic_deltas_but_not_the_delta(self):
        # Спецификация запрещает interval delta, а не наблюдение: связанная
        # ревизия остаётся видимой, полная разность -- NULL.
        d = rs.resolve_area(evidence(10000.0, counter_series([0.0, 15.0]),
                                     identity_ok=False))
        self.assertEqual(d.area_status, rs.UNKNOWN_SUSPECT)
        self.assertIsNone(d.controller_delta_area_m2)
        self.assertIsNotNone(d.counter_observed_delta_m2)
        self.assertEqual(d.counter_window_quality, v4.WINDOW_IDENTITY_ERROR)

    def test_incomplete_v4_is_low_confidence(self):
        frames = [frame(MS, area=5.0), frame(MS + 100, area=5.0),
                  frame(MS + 3000, area=5.0)]
        d = rs.resolve_area(evidence(9000.0, frames, end_ts=START + 3))
        self.assertEqual(d.area_status, rs.RAW_UNVERIFIED)
        self.assertEqual(d.area_confidence, rs.C_LOW)

    def test_control_v4_simply_absent_stays_medium_confidence(self):
        d = rs.resolve_area(evidence(9000.0, None))
        self.assertEqual(d.area_status, rs.RAW_UNVERIFIED)
        self.assertEqual(d.area_confidence, rs.C_MEDIUM)

    def test_channel_missing_with_positive_raw_is_low_confidence(self):
        frames = counter_series([None] * 6, mode=0)
        d = rs.resolve_area(evidence(9000.0, frames))
        self.assertEqual(d.area_status, rs.RAW_UNVERIFIED)
        self.assertEqual(d.area_confidence, rs.C_LOW)
        self.assertIn('AREA_CHANNEL_ABSENT', d.anomaly_flags)

    def test_malformed_geojson_is_skipped_not_raised(self):
        bad = [
            {'features': [{'properties': {'funcType': 'PlantZone'},
                           'geometry': {'type': 'MultiPolygon',
                                        'coordinates': [{'x': 1}]}}]},
            {'features': [{'properties': {'funcType': 'PlantZone'},
                           'geometry': {'type': 'Polygon',
                                        'coordinates': [[[1, 2], [3, 4],
                                                         [5, None]]]}}]},
            {'features': [{'properties': {}, 'geometry': 'x'}]},
            {'features': [{'properties': 'x', 'geometry': {'type': 'Polygon',
                                                           'coordinates': []}}]},
        ]
        for doc in bad:
            self.assertEqual(fld.rings_from_geojson(doc), [],
                             'raised or produced rings for %r' % (doc,))

    def test_control_a_well_formed_polygon_still_parses(self):
        doc = {'features': [{'properties': {'funcType': 'PlantZone'},
                             'geometry': {'type': 'Polygon', 'coordinates': [
                                 [[64.0, 39.0], [64.1, 39.0], [64.1, 39.1],
                                  [64.0, 39.0]]]}}]}
        rings = fld.rings_from_geojson(doc)
        self.assertEqual(len(rings), 1)
        self.assertEqual(rings[0][0], (39.0, 64.0))


if __name__ == '__main__':
    unittest.main()
