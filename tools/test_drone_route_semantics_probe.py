# -*- coding: utf-8 -*-
"""Тесты диагностики собранных маршрутов.

Проверяется не только арифметика, но и словарь: инструмент не должен
называть маршрут работой или обработкой, а односторонняя проверка не должна
превращаться в вердикт.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.drone_route_semantics_probe import (  # noqa: E402
    EXIT_NO_DIRECTORY, EXIT_NOTHING, EXIT_OK, area_consistency,
    coverage_census, format_report, load_routes, main, mission_grouping,
    path_length_m, unknown_field_census)


def route(flight_id=900000001, points=None, area=None, width=None,
          mission=None, hardware='FIXTURE0000000000000', unknown=None,
          data_type='simplified'):
    return {
        'dji_flight_id': flight_id,
        'data_type': data_type,
        'points': points if points is not None else [[40.08, 64.63],
                                                     [40.08, 64.64]],
        'point_count': len(points) if points is not None else 2,
        'dji_area_m2': area,
        'spray_width_m': width,
        'spray_width_recorded': width is not None,
        'hardware_id': hardware,
        'mission_uuid': mission,
        'unknown_fields': unknown or [],
    }


class ProbeTestCase(unittest.TestCase):

    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.outbox = Path(self._directory.name) / 'outbox'
        (self.outbox / 'pending').mkdir(parents=True)
        (self.outbox / 'sent').mkdir(parents=True)

    def put(self, body, bucket='pending', name=None, kind='route'):
        name = name or ('route_%s_%016x.json'
                        % (body.get('dji_flight_id'),
                           abs(hash(json.dumps(body, sort_keys=True)))))
        envelope = {'envelope_version': 1, 'kind': kind,
                    'identity': str(body.get('dji_flight_id')),
                    'body': body}
        (self.outbox / bucket / name).write_text(
            json.dumps(envelope, ensure_ascii=False), encoding='utf-8')

    def report_text(self):
        routes, unreadable = load_routes(self.outbox)
        return '\n'.join(format_report(routes, unreadable))


class TestLoading(ProbeTestCase):

    def test_both_buckets_are_read(self):
        self.put(route(1), bucket='pending')
        self.put(route(2), bucket='sent')
        routes, unreadable = load_routes(self.outbox)
        self.assertEqual(len(routes), 2)
        self.assertEqual(unreadable, 0)

    def test_a_geometry_envelope_is_not_a_route(self):
        """Отрицательный контроль: в очереди лежат записи двух видов."""
        self.put(route(1))
        self.put({'external_id': 'u1'}, kind='field_geometry',
                 name='field_geometry_u1_0000000000000001.json')
        routes, _ = load_routes(self.outbox)
        self.assertEqual(len(routes), 1)

    def test_a_broken_file_is_counted_not_fatal(self):
        self.put(route(1))
        (self.outbox / 'pending' / 'route_broken_1.json').write_text(
            '{', encoding='utf-8')
        routes, unreadable = load_routes(self.outbox)
        self.assertEqual(len(routes), 1)
        self.assertEqual(unreadable, 1)

    def test_an_empty_outbox_reads_as_empty(self):
        routes, unreadable = load_routes(self.outbox)
        self.assertEqual((routes, unreadable), ([], 0))


class TestMissionGrouping(ProbeTestCase):

    def test_routes_sharing_a_mission_form_one_group(self):
        summary = mission_grouping([route(1, mission='m1'),
                                    route(2, mission='m1'),
                                    route(3, mission='m2')])
        self.assertEqual(summary['distinct_values'], 2)
        self.assertEqual(summary['group_sizes'], {1: 1, 2: 1})
        self.assertEqual(summary['largest_group'], 2)

    def test_routes_without_a_mission_are_counted_separately(self):
        summary = mission_grouping([route(1), route(2, mission='m1')])
        self.assertEqual(summary['without_mission_uuid'], 1)
        self.assertEqual(summary['with_mission_uuid'], 1)

    def test_the_mission_value_itself_is_never_printed(self):
        self.put(route(1, mission='MISSION-SECRET-VALUE-0001'))
        self.assertNotIn('MISSION-SECRET-VALUE-0001', self.report_text())

    def test_the_report_refuses_to_call_a_group_a_proven_task(self):
        self.put(route(1, mission='m1'))
        text = self.report_text()
        self.assertIn('does not prove it', text)


class TestUnknownFieldCensus(unittest.TestCase):

    def test_a_field_with_one_value_per_route_is_an_identifier_candidate(self):
        census = unknown_field_census([
            route(1, unknown=[{'field': 77, 'wire': 2, 'sha256': 'a'}]),
            route(2, unknown=[{'field': 77, 'wire': 2, 'sha256': 'b'}])])
        self.assertEqual(census[0]['candidate'], 'per-route-identifier-like')
        self.assertEqual(census[0]['distinct_values'], 2)

    def test_a_field_shared_by_several_routes_is_a_group_candidate(self):
        census = unknown_field_census([
            route(1, unknown=[{'field': 77, 'wire': 2, 'sha256': 'a'}]),
            route(2, unknown=[{'field': 77, 'wire': 2, 'sha256': 'a'}]),
            route(3, unknown=[{'field': 77, 'wire': 2, 'sha256': 'b'}])])
        self.assertEqual(census[0]['candidate'], 'group-identifier-like')

    def test_a_field_with_one_value_everywhere_is_constant_like(self):
        census = unknown_field_census([
            route(1, unknown=[{'field': 77, 'wire': 0, 'varint': 4}]),
            route(2, unknown=[{'field': 77, 'wire': 0, 'varint': 4}])])
        self.assertEqual(census[0]['candidate'], 'constant-like')

    def test_no_routes_means_no_census(self):
        self.assertEqual(unknown_field_census([]), [])

    def test_the_wording_calls_it_a_candidate_not_an_answer(self):
        text = '\n'.join(format_report(
            [route(1, unknown=[{'field': 77, 'wire': 2, 'sha256': 'a'}])], 0))
        self.assertIn('nothing more', text)
        self.assertIn('never by the shape alone', text)


class TestAreaConsistency(unittest.TestCase):

    STRAIGHT = [[40.08, 64.63], [40.08, 64.6417766]]   # около 1000 м

    def test_the_length_of_a_known_span(self):
        """Ожидание считается здесь же из первых начал, а не берётся круглым.

        Круглое «примерно 1000 м» с широким допуском прошло бы и при
        перепутанных широте и долготе -- проверкой это бы не было.
        """
        import math
        delta_lon = 64.6417766 - 64.63
        expected = (math.radians(delta_lon) * 6378137.0
                    * math.cos(math.radians(40.08)))
        measured = path_length_m([(40.08, 64.63), (40.08, 64.6417766)])
        self.assertAlmostEqual(measured, expected, delta=0.5)
        self.assertGreater(measured, 990.0)
        self.assertLess(measured, 1015.0)

    def test_a_route_whose_area_needs_more_path_than_flown_is_flagged(self):
        """Ширина 6 м, площадь 12 000 м2 -- нужно 2000 м, пролетели 1000."""
        summary = area_consistency([route(1, points=self.STRAIGHT,
                                          area=12000.0, width=6.0)])
        self.assertEqual(summary['checked'], 1)
        self.assertEqual(summary['exceeding_the_route'], 1)
        self.assertAlmostEqual(summary['median'], 2.0, delta=0.01)

    def test_a_consistent_route_is_not_flagged(self):
        """Отрицательный контроль: проверка обязана уметь не срабатывать."""
        summary = area_consistency([route(1, points=self.STRAIGHT,
                                          area=3000.0, width=6.0)])
        self.assertEqual(summary['exceeding_the_route'], 0)
        self.assertAlmostEqual(summary['median'], 0.5, delta=0.01)

    def test_a_route_without_a_width_is_skipped_never_substituted(self):
        summary = area_consistency([route(1, points=self.STRAIGHT,
                                          area=3000.0, width=None)])
        self.assertEqual(summary['checked'], 0)
        self.assertEqual(summary['skipped_no_width'], 1)

    def test_a_route_without_an_area_is_skipped(self):
        summary = area_consistency([route(1, points=self.STRAIGHT, width=6.0)])
        self.assertEqual(summary['skipped_no_area'], 1)

    def test_a_single_point_route_is_skipped(self):
        summary = area_consistency([route(1, points=[[40.08, 64.63]],
                                          area=3000.0, width=6.0)])
        self.assertEqual(summary['skipped_too_short'], 1)

    def test_the_report_states_that_the_check_is_one_sided(self):
        text = '\n'.join(format_report(
            [route(1, points=self.STRAIGHT, area=3000.0, width=6.0)], 0))
        self.assertIn('ONE-SIDED', text)
        self.assertIn('confirms nothing', text)

    def test_the_probe_matches_the_collector_decoder_on_the_same_points(self):
        """Формула дублирует декодер намеренно -- числа обязаны совпадать."""
        from drone_collector.route_decode import path_length_m as decoder_length
        points = [(40.0800, 64.6300), (40.0810, 64.6350), (40.0790, 64.6400)]
        self.assertAlmostEqual(path_length_m(points), decoder_length(points),
                               places=6)


class TestCoverageCensus(unittest.TestCase):

    def test_widths_and_hardware_are_counted(self):
        census = coverage_census([route(1, width=6.0),
                                  route(2, width=None, hardware=None)])
        self.assertEqual(census['with_recorded_width'], 1)
        self.assertEqual(census['without_recorded_width'], 1)
        self.assertEqual(census['with_hardware_id'], 1)

    def test_the_observed_data_type_is_reported(self):
        census = coverage_census([route(1), route(2)])
        self.assertEqual(census['data_types'], {'simplified': 2})


class TestVocabulary(ProbeTestCase):
    """Слова, которых в выводе быть не должно."""

    FORBIDDEN = ('sprayed', 'spraying', 'treated area', 'work area covered',
                 'billable', 'overpayment', 'double billing')

    MARKER = 'NOT ESTABLISHED BY ANY NUMBER ABOVE'

    def test_the_findings_never_call_a_route_work(self):
        """Проверяются НАБЛЮДЕНИЯ, а не раздел отрицаний.

        Раздел «что не установлено» обязан произносить эти самые слова -- он
        для того и написан. Запрет относится к тексту ВЫШЕ него: именно там
        число, названное обработкой, стало бы фактом.
        """
        self.put(route(1, area=3000.0, width=6.0, mission='m1'))
        findings = self.report_text().split(self.MARKER)[0].lower()
        for word in self.FORBIDDEN:
            self.assertNotIn(word, findings, 'в наблюдениях найдено %r' % word)

    def test_the_denials_really_are_present_below(self):
        """Отрицательный контроль: тест выше не должен проходить впустую."""
        self.put(route(1, area=3000.0, width=6.0))
        tail = self.report_text().split(self.MARKER)[1].lower()
        self.assertIn('sprayed', tail)
        self.assertIn('billable hectares', tail)

    def test_the_report_names_what_is_not_established(self):
        self.put(route(1, area=3000.0, width=6.0))
        text = self.report_text()
        self.assertIn(self.MARKER, text)
        self.assertIn('billable hectares', text)
        self.assertIn('how DJI computes the swath width', text)
        self.assertIn('derives the SWATH WIDTH from the area and the route',
                      text)

    def test_the_simplified_limitation_is_stated_as_scoped(self):
        """Ограничение доказано для `simplified`, а не для всех источников."""
        self.put(route(1))
        self.assertIn('not for every DJI source', self.report_text())


class TestCommandLine(ProbeTestCase):

    def run_main(self, argv):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(argv)
        return code, buffer.getvalue()

    def test_a_missing_directory_exits_two_and_says_how_to_collect(self):
        code, text = self.run_main(['--outbox', str(self.outbox / 'nope')])
        self.assertEqual(code, EXIT_NO_DIRECTORY)
        self.assertIn('--routes', text)

    def test_an_empty_outbox_exits_one_and_says_so(self):
        code, text = self.run_main(['--outbox', str(self.outbox)])
        self.assertEqual(code, EXIT_NOTHING)
        self.assertIn('NOTHING TO ANALYSE', text)

    def test_a_populated_outbox_exits_zero(self):
        self.put(route(1, area=3000.0, width=6.0, mission='m1'))
        code, text = self.run_main(['--outbox', str(self.outbox)])
        self.assertEqual(code, EXIT_OK)
        self.assertIn('WHAT WAS COLLECTED', text)

    def test_the_console_output_is_pure_ascii(self):
        """Правило устава: кириллица уходит в файл, консоль получает ASCII."""
        self.put(route(1, area=3000.0, width=6.0))
        _code, text = self.run_main(['--outbox', str(self.outbox)])
        text.encode('ascii')

    def test_the_report_file_is_written_in_utf8(self):
        self.put(route(1, area=3000.0, width=6.0))
        target = self.outbox.parent / 'report.txt'
        code, _text = self.run_main(['--outbox', str(self.outbox),
                                     '--report', str(target)])
        self.assertEqual(code, EXIT_OK)
        self.assertIn('WHAT WAS COLLECTED',
                      target.read_text(encoding='utf-8'))

    def test_help_does_not_crash(self):
        """Тот же класс дефекта, что чинился в PR №106: литерал % в help."""
        import subprocess
        here = os.path.dirname(os.path.abspath(__file__))
        result = subprocess.run(
            [sys.executable,
             os.path.join(here, 'drone_route_semantics_probe.py'), '--help'],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
