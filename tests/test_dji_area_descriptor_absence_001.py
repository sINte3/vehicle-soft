# -*- coding: utf-8 -*-
"""DRONE-AREA-CONTROL-V2-MEGA, live case 715984635: the receiver's side of
the direct descriptor request.

The collector records "DJI holds no descriptor for this flight" only after a
404 to the flight's own descriptor path, confirmed by a control request for
a flight whose descriptor exists. What the receiver does with that record:

* the collector's own items (card, route, absence), built by the collector's
  code and posted as the collector posts them, are kept;
  `v4_absent_reason` becomes NO_V4_URL_AT_SOURCE;
* the kept revision IS the evidence: its bytes carry the 404 answer's
  sha256, size and text; `request_context` the path, the status and the
  control; a repeat is a duplicate, not a second revision;
* an incomplete or contradictory record is refused (errors), and the flight
  stays NOT_CAPTURED;
* drone_flights.area_ha and the calculated RAW/effective area do not move;
  only the flag NO_V4_AT_SOURCE appears.

Flask test client over the synthetic test database (tests.harness).
"""
import base64
import hashlib
import json
import logging
import os
import sqlite3
import tempfile
import unittest

from tests.harness import app, TEST_DB_PATH
from tests.test_dji_area_ingest_001 import (Base, START, airlines_derived,
                                            card_json, source)
from tests.test_dji_area_core import counter_series, v4_bytes

from dji_area import store
from drone_collector.sources import (DESCRIPTOR_ABSENT,
                                     SCHEMA_AIRLINES_DESCRIPTOR_ABSENT,
                                     source_items)
from drone_collector.tests.test_sources import (API_HOST, NOT_FOUND_BODY,
                                                RUN_ID, _DescriptorStand,
                                                _FakeRequest,
                                                descriptor_ok,
                                                descriptor_path, not_found,
                                                route_request, step_finished)

FLIGHT = 900031
REFERENCE = 900099
OTHER = 900032


def collector_items(flight_id, card=None, control=None):
    """What the collector queues for the live case -- card, route and the
    absence record -- built by the collector's own code, not by hand."""
    stand = _DescriptorStand(
        {descriptor_path(flight_id): not_found(),
         descriptor_path(REFERENCE): control or descriptor_ok(REFERENCE)},
        reference_ids=[REFERENCE])
    card_request = _FakeRequest(
        '%s/api/web/v1/flight_records/%d' % (API_HOST, flight_id),
        body=card if card is not None else card_json(flight_id))
    flight = stand.visit(flight_id, script=[
        step_finished(card_request), step_finished(route_request(flight_id))])
    stand.run.confirm_absences(final=True)
    assert flight.descriptor == DESCRIPTOR_ABSENT, flight.describe()
    return {item['source_type']: item
            for item in source_items(flight, RUN_ID)}


def rebuilt(item, change_document=None, change_context=None):
    """The same item with its document or context changed -- and its sha256
    and size recomputed, so a refusal is about the record, not the digest."""
    body = base64.b64decode(item['body_b64'])
    document = json.loads(body.decode('utf-8'))
    if change_document:
        change_document(document)
    body = json.dumps(document, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')
    out = dict(item)
    out['body_b64'] = base64.b64encode(body).decode('ascii')
    out['sha256'] = hashlib.sha256(body).hexdigest()
    out['size_bytes'] = len(body)
    context = dict(item['request_context'])
    if change_context:
        change_context(context)
    out['request_context'] = context
    return out


class DescriptorBase(Base):

    def setUp(self):
        Base.setUp(self)
        self.add_flight(FLIGHT)

    def reason(self, flight_id=FLIGHT):
        rows = self.raw('SELECT v4_absent_reason, airlines_revision_id FROM '
                        'dji_flight_evidence WHERE flight_id=?', (flight_id,))
        return rows[0] if rows else (None, None)

    def airlines_revisions(self, flight_id=FLIGHT):
        con = sqlite3.connect(TEST_DB_PATH)
        con.row_factory = sqlite3.Row
        try:
            return con.execute(
                "SELECT * FROM dji_source_revisions WHERE flight_id=? AND "
                "source_type='airlines' ORDER BY id", (flight_id,)).fetchall()
        finally:
            con.close()

    def post_and_log(self, items):
        """(answer, receiver's log text): a refusal must be for its reason."""
        with self.assertLogs(app.logger, level=logging.WARNING) as caught:
            app.logger.warning('probe')
            answer = self.post_sources(items).get_json()
        return answer, '\n'.join(caught.output)


class TheReceiverKeepsTheAbsence(DescriptorBase):

    def test_the_collectors_items_make_the_flight_no_v4_at_source(self):
        items = collector_items(FLIGHT)
        answer = self.post_sources([items['card'], items['route'],
                                    items['airlines']]).get_json()
        self.assertEqual((answer['errors'], answer['new']), (0, 3), answer)
        reason, airlines_id = self.reason()
        self.assertIsNotNone(airlines_id)
        self.assertEqual(reason, 'NO_V4_URL_AT_SOURCE')

    def test_the_kept_revision_is_the_evidence_of_the_404(self):
        items = collector_items(FLIGHT)
        self.post_sources([items['card'], items['route'], items['airlines']])
        rows = self.airlines_revisions()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['schema_version'],
                         SCHEMA_AIRLINES_DESCRIPTOR_ABSENT)
        self.assertEqual(row['sha256'], items['airlines']['sha256'])
        body = store.read_body(store.source_root(TEST_DB_PATH), row)
        self.assertEqual(hashlib.sha256(body).hexdigest(), row['sha256'])
        document = json.loads(body.decode('utf-8'))
        self.assertIsNone(document['file_v4_url_path'])
        self.assertEqual(document['descriptor_http_status'], 404)
        self.assertEqual(document['descriptor_path'], descriptor_path(FLIGHT))
        self.assertEqual(document['descriptor_body_sha256'],
                         hashlib.sha256(NOT_FOUND_BODY).hexdigest())
        self.assertEqual(document['descriptor_body_size'],
                         len(NOT_FOUND_BODY))
        self.assertEqual(document['descriptor_body_text'].encode('utf-8'),
                         NOT_FOUND_BODY)
        context = json.loads(row['request_context_json'])
        self.assertEqual(context['association'], 'direct_descriptor_get')
        self.assertEqual(context['http_status'], 404)
        self.assertEqual(context['path'], descriptor_path(FLIGHT))
        self.assertEqual(context['control_flight_id'], REFERENCE)
        self.assertEqual(context['control_http_status'], 200)

    def test_a_repeat_is_a_duplicate_not_a_second_revision(self):
        items = collector_items(FLIGHT)
        batch = [items['card'], items['route'], items['airlines']]
        self.post_sources(batch)
        answer = self.post_sources(batch).get_json()
        self.assertEqual((answer['errors'], answer['new'],
                          answer['duplicates']), (0, 0, 3), answer)
        self.assertEqual(len(self.airlines_revisions()), 1)

    def test_the_absence_may_come_before_its_card_in_the_batch(self):
        items = collector_items(FLIGHT)
        answer = self.post_sources([items['airlines'], items['card'],
                                    items['route']]).get_json()
        self.assertEqual(answer['errors'], 0, answer)
        self.assertEqual(self.reason()[0], 'NO_V4_URL_AT_SOURCE')

    def test_a_card_kept_earlier_confirms_a_later_absence(self):
        items = collector_items(FLIGHT)
        self.post_sources([items['card'], items['route']])
        self.assertEqual(self.reason()[0], 'NOT_CAPTURED')
        answer = self.post_sources([items['airlines']]).get_json()
        self.assertEqual(answer['errors'], 0, answer)
        self.assertEqual(self.reason()[0], 'NO_V4_URL_AT_SOURCE')

    def test_a_v4_that_arrives_later_still_wins(self):
        """The absence is not a lock: when DJI later serves the flight's V4,
        it is kept and the flight has its V4 again."""
        items = collector_items(FLIGHT)
        self.post_sources([items['card'], items['route'], items['airlines']])
        answer = self.post_sources([source(
            FLIGHT, 'v4', v4_bytes(counter_series(
                [0.0, 0.5, 1.0], start_ms=START * 1000, step_ms=1000)),
            request_context={'path': '/airline_v4/%d/x' % FLIGHT})]).get_json()
        self.assertEqual(answer['errors'], 0, answer)
        rows = self.raw('SELECT v4_revision_id, v4_absent_reason FROM '
                        'dji_flight_evidence WHERE flight_id=?', (FLIGHT,))
        self.assertIsNotNone(rows[0][0])
        self.assertIsNone(rows[0][1])


class TheReceiverRefusesAnIncompleteRecord(DescriptorBase):
    """Every refusal leaves the flight NOT_CAPTURED and stores nothing."""

    def assert_refused(self, items, why):
        answer, log_text = self.post_and_log(items)
        self.assertGreaterEqual(answer['errors'], 1, answer)
        self.assertIn(why, log_text)
        self.assertEqual(self.airlines_revisions(), [])
        self.assertNotEqual(self.reason()[0], 'NO_V4_URL_AT_SOURCE')

    def test_the_record_as_the_collector_built_it_is_accepted(self):
        """NEGATIVE CONTROL for everything below."""
        items = collector_items(FLIGHT)
        answer = self.post_sources([items['card'], items['route'],
                                    items['airlines']]).get_json()
        self.assertEqual(answer['errors'], 0, answer)

    def test_a_record_without_a_working_control_is_refused(self):
        items = collector_items(FLIGHT)
        cases = {
            'the control answered 404': lambda c: c.update(
                control_http_status=404),
            'no control flight': lambda c: c.pop('control_flight_id'),
            'the control is the flight itself': lambda c: c.update(
                control_flight_id=FLIGHT),
        }
        for label, change in cases.items():
            with self.subTest(label):
                self.assert_refused(
                    [items['card'], rebuilt(items['airlines'],
                                            change_context=change)],
                    'has no working control request')

    def test_a_record_that_is_not_a_404_of_its_own_descriptor_is_refused(
            self):
        items = collector_items(FLIGHT)
        cases = {
            'the page captured it': lambda c: c.update(
                association='url_path'),
            'the answer was 403': lambda c: c.update(http_status=403),
            'another flight path': lambda c: c.update(
                path=descriptor_path(OTHER)),
        }
        for label, change in cases.items():
            with self.subTest(label):
                self.assert_refused(
                    [items['card'], rebuilt(items['airlines'],
                                            change_context=change)],
                    'is not a 404 of its own descriptor')

    def test_a_record_whose_bytes_do_not_match_is_refused(self):
        items = collector_items(FLIGHT)
        cases = {
            'other bytes': lambda d: d.update(
                descriptor_body_text='404 something else\n'),
            'another size': lambda d: d.update(descriptor_body_size=20),
        }
        for label, change in cases.items():
            with self.subTest(label):
                self.assert_refused(
                    [items['card'], rebuilt(items['airlines'],
                                            change_document=change)],
                    'the 404 bytes do not match their sha256')

    def test_a_record_that_contradicts_itself_is_refused(self):
        items = collector_items(FLIGHT)
        cases = {
            'a V4 link inside': lambda d: d.update(
                file_v4_url_path='host.invalid/objects/airline_v4/1/x'),
            'another status inside': lambda d: d.update(
                descriptor_http_status=403),
            'a code inside': lambda d: d.update(code=0),
        }
        for label, change in cases.items():
            with self.subTest(label):
                self.assert_refused(
                    [items['card'], rebuilt(items['airlines'],
                                            change_document=change)],
                    'contradicts itself')
        with self.subTest('an extra field'):
            self.assert_refused(
                [items['card'], rebuilt(
                    items['airlines'],
                    change_document=lambda d: d.update(data={}))],
                'has unexpected fields')

    def test_a_record_without_the_card_of_its_flight_is_refused(self):
        items = collector_items(FLIGHT)
        with self.subTest('no card anywhere'):
            self.assert_refused([items['airlines']],
                                'no card of that flight confirms')
        with self.subTest('a card naming another flight'):
            self.assert_refused(
                [source(FLIGHT, 'card', card_json(OTHER)), items['airlines']],
                'no card of that flight confirms')

    def test_a_record_for_a_flight_with_a_v4_is_refused(self):
        items = collector_items(FLIGHT)
        self.post_sources([items['card'], source(
            FLIGHT, 'v4', v4_bytes(counter_series(
                [0.0, 0.5, 1.0], start_ms=START * 1000, step_ms=1000)),
            request_context={'path': '/airline_v4/%d/x' % FLIGHT})])
        answer, log_text = self.post_and_log([items['airlines']])
        self.assertEqual(answer['errors'], 1, answer)
        self.assertIn('its V4 is already kept', log_text)
        self.assertEqual(self.airlines_revisions(), [])

    def test_a_record_after_dji_named_a_v4_is_refused(self):
        items = collector_items(FLIGHT)
        self.post_sources([items['card'], source(
            FLIGHT, 'airlines', airlines_derived(),
            schema_version='airlines-paths-only')])
        answer, log_text = self.post_and_log([items['airlines']])
        self.assertEqual(answer['errors'], 1, answer)
        self.assertIn('DJI named a V4 for it earlier', log_text)
        self.assertEqual(len(self.airlines_revisions()), 1)
        self.assertEqual(self.reason()[0], 'NOT_CAPTURED')


class TheAreaDoesNotMove(Base):
    """9: RAW and effective area of a candidate stay as they were."""

    CANDIDATE = 900014

    def seed_chain(self):
        """base (4, width) -> bridge (1) -> candidate (4, no width), no V4."""
        s0 = START
        self.add_flight(900012, area_m2=8980.0, start=s0 + 400, end=s0 + 700,
                        width=6.66)
        self.add_flight(900013, area_m2=0.0, start=s0 + 700, end=s0 + 720,
                        mode=1, width=None)
        self.add_flight(900014, area_m2=8980.0, start=s0 + 720, end=s0 + 780,
                        width=None)
        cards = [
            source(900012, 'card', card_json(900012, area=8980.0,
                                             start=s0 + 400, end=s0 + 700,
                                             width=6.66)),
            source(900014, 'card', card_json(900014, area=8980.0,
                                             start=s0 + 720, end=s0 + 780,
                                             width=None)),
        ]
        answer = self.post_sources(cards).get_json()
        self.assertEqual(answer['errors'], 0, answer)

    def absence_of_candidate(self):
        items = collector_items(self.CANDIDATE, card=card_json(
            self.CANDIDATE, area=8980.0, start=START + 720, end=START + 780,
            width=None))
        answer = self.post_sources([items['airlines']]).get_json()
        self.assertEqual(answer['errors'], 0, answer)
        rows = self.raw('SELECT v4_absent_reason FROM dji_flight_evidence '
                        'WHERE flight_id=?', (self.CANDIDATE,))
        self.assertEqual(rows[0][0], 'NO_V4_URL_AT_SOURCE')

    def computed(self):
        """The calculation as a dry run sees it now: {flight_id: row}."""
        handle, path = tempfile.mkstemp(suffix='.json')
        os.close(handle)
        self.addCleanup(os.remove, path)
        out = self.recalc('--dry-run', '--rows', path)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        with open(path, encoding='utf-8') as fh:
            return {int(row['flight_id']): row for row in json.load(fh)}

    AREA_KEYS = ('area_status', 'evidence_status', 'area_method',
                 'area_confidence', 'raw_area_m2',
                 'corrected_recorded_area_m2', 'controller_delta_area_m2',
                 'aggregation_eligibility', 'structural_candidate')

    def test_the_absence_changes_the_flags_and_nothing_else(self):
        self.seed_chain()
        before = self.computed()[self.CANDIDATE]
        self.assertIs(before['structural_candidate'], True)
        self.absence_of_candidate()
        after = self.computed()[self.CANDIDATE]
        for key in self.AREA_KEYS:
            self.assertEqual(after[key], before[key], key)
        # RAW is kept, nothing is corrected and nothing is zeroed.
        self.assertEqual(after['raw_area_m2'], 8980.0)
        self.assertIsNone(after['corrected_recorded_area_m2'])
        self.assertNotIn('NO_V4_AT_SOURCE', before['anomaly_flags'])
        self.assertIn('NO_V4_AT_SOURCE', after['anomaly_flags'])
        self.assertEqual(set(after['anomaly_flags'])
                         - set(before['anomaly_flags']), {'NO_V4_AT_SOURCE'})
        self.assertEqual(self.raw('SELECT area_ha FROM drone_flights WHERE '
                                  'dji_flight_id=?', (self.CANDIDATE,))[0][0],
                         0.898)

    def test_a_written_calculation_is_not_rewritten_by_the_absence(self):
        self.seed_chain()
        first = self.recalc('--apply')
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.absence_of_candidate()
        second = self.recalc('--apply')
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        rows = self.raw('SELECT area_status, raw_area_m2, '
                        'corrected_recorded_area_m2, billable_area_m2, '
                        'superseded_at IS NULL FROM dji_area_calculations '
                        'WHERE flight_id=? ORDER BY id', (self.CANDIDATE,))
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0][1], 8980.0)
        self.assertIsNone(rows[0][3])
        self.assertEqual(self.raw('SELECT area_ha FROM drone_flights WHERE '
                                  'dji_flight_id=?', (self.CANDIDATE,))[0][0],
                         0.898)


if __name__ == '__main__':
    unittest.main()
