# -*- coding: utf-8 -*-
"""Chunking, the retry policy and the dry-run dump (section 7.4 of the task).

0, 1, 999, 1000, 1001 and 2500 flights produce the expected number of batches
and no batch exceeds 1000. The retry cases are extra: they cost nothing here
and the alternative is discovering the policy against the live endpoint.
"""

import json
import shutil
import tempfile
import unittest

from datetime import date
from pathlib import Path

from drone_collector.config import (MAX_BATCH_SIZE,
                                    MAX_LAND_SNAPSHOT_BATCH_SIZE,
                                    MAX_SOURCE_BATCH_SIZE, CollectorConfig)
from drone_collector.sender import (
    IngestRejected,
    LandSnapshotSendResult,
    SendResult,
    SourceSendResult,
    TransportError,
    build_land_snapshot_payload,
    build_payload,
    build_source_payload,
    chunk,
    chunk_sources,
    dry_run_path,
    send,
    send_land_snapshot_chunk,
    send_sources,
    write_dry_run,
)

from drone_collector.tests.support import make_flight

PERIOD_FROM = date(2026, 7, 1)
PERIOD_TO = date(2026, 7, 31)


def flights(count):
    return [make_flight(900000000 + i) for i in range(count)]


def config(batch_size=500, token='test-token'):
    return CollectorConfig(
        records_url='https://www.djiag.com/records/list',
        storage_state=Path('data/storage_state.json'),
        headless=True,
        window_days=30,
        tz_offset_hours=5,
        page_timeout_ms=45000,
        settle_ms=0,
        max_pages=500,
        base_url='http://10.103.25.14:5050',
        api_token=token,
        batch_size=batch_size,
    )


def ok_body(seen, log_id=1, new=None, duplicates=0, unresolved=0, errors=0):
    return {'status': 'ok', 'log_id': log_id, 'seen': seen,
            'new': seen if new is None else new, 'duplicates': duplicates,
            'unresolved': unresolved, 'errors': errors}


class Recorder(object):
    """A fake transport: replays scripted answers and records what it was sent."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, url, payload, timeout_s):
        self.calls.append({'url': url, 'payload': payload,
                           'timeout': timeout_s})
        answer = self.answers.pop(0) if self.answers else ok_body(
            len(payload['flights']))
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, tuple):
            return answer
        return 200, answer


class ChunkTests(unittest.TestCase):

    def test_batch_counts_at_the_cap(self):
        cases = {0: 0, 1: 1, 999: 1, 1000: 1, 1001: 2, 2500: 3}
        for count, expected in sorted(cases.items()):
            batches = chunk(flights(count), MAX_BATCH_SIZE)
            self.assertEqual(len(batches), expected,
                             '%d flights -> %d batches' % (count,
                                                           len(batches)))
            self.assertEqual(sum(len(b) for b in batches), count)
            for batch in batches:
                self.assertLessEqual(len(batch), MAX_BATCH_SIZE)

    def test_batch_counts_at_the_default_size(self):
        cases = {0: 0, 1: 1, 999: 2, 1000: 2, 1001: 3, 2500: 5}
        for count, expected in sorted(cases.items()):
            batches = chunk(flights(count), 500)
            self.assertEqual(len(batches), expected)
            self.assertEqual(sum(len(b) for b in batches), count)

    def test_an_oversized_batch_size_is_clamped(self):
        batches = chunk(flights(2500), 5000)
        self.assertEqual(len(batches), 3)
        for batch in batches:
            self.assertLessEqual(len(batch), MAX_BATCH_SIZE)

    def test_a_zero_batch_size_does_not_loop_forever(self):
        self.assertEqual(len(chunk(flights(3), 0)), 3)

    def test_nothing_is_lost_or_reordered(self):
        source = flights(1001)
        rebuilt = [f for batch in chunk(source, 500) for f in batch]
        self.assertEqual([f['id'] for f in rebuilt], [f['id'] for f in source])


class PayloadTests(unittest.TestCase):

    def test_shape_matches_the_ingest_contract(self):
        payload = build_payload('tok', 'incremental', PERIOD_FROM, PERIOD_TO,
                                flights(2))
        self.assertEqual(sorted(payload.keys()),
                         ['flights', 'kind', 'period_from', 'period_to',
                          'token'])
        self.assertEqual(payload['period_from'], '2026-07-01')
        self.assertEqual(payload['period_to'], '2026-07-31')
        self.assertEqual(payload['token'], 'tok')

    def test_flights_go_through_verbatim(self):
        source = make_flight(900000001)
        payload = build_payload('tok', 'backfill', PERIOD_FROM, PERIOD_TO,
                                [source])
        sent = payload['flights'][0]
        # Units are converted by the ingest endpoint, never here.
        self.assertEqual(sent['new_work_area'], source['new_work_area'])
        self.assertEqual(sent['spray_usage'], source['spray_usage'])
        self.assertEqual(sent['sow_usage'], source['sow_usage'])
        self.assertEqual(sent, source)

    def test_unknown_kind_is_refused(self):
        for bad in ('daily', '', None, 'INCREMENTAL'):
            with self.assertRaises(ValueError):
                build_payload('tok', bad, PERIOD_FROM, PERIOD_TO, [])


class SendTests(unittest.TestCase):

    def test_no_flights_makes_no_request(self):
        post = Recorder([])
        result = send([], 'incremental', PERIOD_FROM, PERIOD_TO, config(),
                      post_fn=post, sleep_fn=lambda _s: None)
        self.assertEqual(post.calls, [])
        self.assertEqual(result.batches, 0)

    def test_counters_are_summed_over_batches(self):
        post = Recorder([ok_body(500, log_id=11, new=500, unresolved=3),
                         ok_body(500, log_id=12, new=200, duplicates=299,
                                 errors=1)])
        result = send(flights(1000), 'backfill', PERIOD_FROM, PERIOD_TO,
                      config(batch_size=500), post_fn=post,
                      sleep_fn=lambda _s: None)
        self.assertEqual(result.batches, 2)
        self.assertEqual(result.seen, 1000)
        self.assertEqual(result.new, 700)
        self.assertEqual(result.duplicates, 299)
        self.assertEqual(result.unresolved, 3)
        self.assertEqual(result.errors, 1)
        self.assertEqual(result.log_ids, [11, 12])

    def test_the_token_is_sent_in_the_body(self):
        post = Recorder([])
        send(flights(1), 'incremental', PERIOD_FROM, PERIOD_TO,
             config(token='secret'), post_fn=post, sleep_fn=lambda _s: None)
        self.assertEqual(post.calls[0]['payload']['token'], 'secret')
        self.assertTrue(post.calls[0]['url'].endswith(
            '/drones/api/flight_sync'))

    def test_connection_errors_are_retried_then_succeed(self):
        waits = []
        post = Recorder([TransportError('connection refused'),
                         TransportError('connection refused'),
                         ok_body(1)])
        result = send(flights(1), 'incremental', PERIOD_FROM, PERIOD_TO,
                      config(), post_fn=post, sleep_fn=waits.append)
        self.assertEqual(len(post.calls), 3)
        self.assertEqual(waits, [2, 4])
        self.assertEqual(result.seen, 1)

    def test_five_hundreds_are_retried_then_give_up(self):
        post = Recorder([(500, {'error': 'boom'})] * 3)
        with self.assertRaises(IngestRejected) as caught:
            send(flights(1), 'incremental', PERIOD_FROM, PERIOD_TO, config(),
                 post_fn=post, sleep_fn=lambda _s: None)
        self.assertEqual(len(post.calls), 3)
        self.assertIn('after 3 attempts', str(caught.exception))

    def test_401_is_not_retried(self):
        post = Recorder([(401, {'error': 'unauthorized'})])
        with self.assertRaises(IngestRejected) as caught:
            send(flights(1), 'incremental', PERIOD_FROM, PERIOD_TO, config(),
                 post_fn=post, sleep_fn=lambda _s: None)
        self.assertEqual(len(post.calls), 1)
        self.assertIn('401', str(caught.exception))

    def test_413_is_not_retried(self):
        post = Recorder([(413, {'error': 'batch too large'})])
        with self.assertRaises(IngestRejected):
            send(flights(1), 'incremental', PERIOD_FROM, PERIOD_TO, config(),
                 post_fn=post, sleep_fn=lambda _s: None)
        self.assertEqual(len(post.calls), 1)

    def test_400_is_not_retried(self):
        post = Recorder([(400, {'error': 'flights must be a list'})])
        with self.assertRaises(IngestRejected):
            send(flights(1), 'incremental', PERIOD_FROM, PERIOD_TO, config(),
                 post_fn=post, sleep_fn=lambda _s: None)
        self.assertEqual(len(post.calls), 1)

    def test_a_failing_batch_stops_the_run(self):
        # The first batch lands, the second is refused: the exception must
        # surface rather than be swallowed into a partial success.
        post = Recorder([ok_body(500), (401, {'error': 'unauthorized'})])
        with self.assertRaises(IngestRejected):
            send(flights(1000), 'backfill', PERIOD_FROM, PERIOD_TO,
                 config(batch_size=500), post_fn=post,
                 sleep_fn=lambda _s: None)
        self.assertEqual(len(post.calls), 2)


class SendResultTests(unittest.TestCase):

    def test_missing_counters_do_not_raise(self):
        result = SendResult().add({'status': 'ok'})
        self.assertEqual(result.as_dict(),
                         {'batches': 1, 'seen': 0, 'new': 0, 'duplicates': 0,
                          'unresolved': 0, 'errors': 0})


class DryRunTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='drone_collector_test_'))

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def test_file_name_carries_the_period(self):
        self.assertEqual(dry_run_path(self.tmp, PERIOD_FROM, PERIOD_TO).name,
                         'flights_2026-07-01_2026-07-31.json')

    def test_written_document_holds_the_flights_and_no_token(self):
        target = write_dry_run(flights(3), 'backfill', PERIOD_FROM, PERIOD_TO,
                               self.tmp)
        with open(str(target), encoding='utf-8') as handle:
            document = json.load(handle)
        self.assertEqual(document['count'], 3)
        self.assertEqual(len(document['flights']), 3)
        self.assertEqual(document['kind'], 'backfill')
        self.assertEqual(document['period_from'], '2026-07-01')
        self.assertNotIn('token', document)
        self.assertNotIn('test-token', target.read_text(encoding='utf-8'))

    def test_directory_is_created(self):
        target = write_dry_run([], 'incremental', PERIOD_FROM, PERIOD_TO,
                               self.tmp / 'out')
        self.assertTrue(target.exists())


# ─── DJI-AREA-EVIDENCE-001: the two new senders ─────────────────────────────


def source_items(count, flight_id=673501214):
    """Source items of the /drones/api/source_sync contract, without a body."""
    return [{'flight_id': flight_id + i, 'source_type': 'card',
             'captured_at_utc': '2026-09-08 04:12:00',
             'body_b64': 'eyJjb2RlIjogMH0=', 'sha256': '%064d' % i,
             'size_bytes': 11,
             'request_context': {'path': '/api/web/v1/flight_records/%d'
                                         % (flight_id + i),
                                 'association': 'url_path'},
             'capture_run_id': 'sources:ids-file:20260908T041200Z',
             'parser_version': None, 'schema_version': 'raw-http-body',
             'api_status': 0}
            for i in range(count)]


def source_ok(seen, new=None, duplicates=0, errors=0, refreshed=0):
    return {'status': 'ok', 'seen': seen,
            'new': seen if new is None else new, 'duplicates': duplicates,
            'errors': errors, 'refreshed': refreshed}


def snapshot_ok(lands_seen, geometries_seen=0):
    return {'status': 'ok', 'snapshot_id': 7, 'lands_seen': lands_seen,
            'lands_new': lands_seen, 'lands_seen_before': 0, 'errors': 0,
            'geometries_seen': geometries_seen,
            'geometries_new': geometries_seen, 'geometries_unchanged': 0,
            'geometries_errors': 0}


class SourceRecorder(object):
    """A fake transport for the source endpoint."""

    def __init__(self, answers=None):
        self.answers = list(answers or [])
        self.calls = []

    def __call__(self, url, payload, timeout_s):
        self.calls.append({'url': url, 'payload': payload})
        if self.answers:
            answer = self.answers.pop(0)
        else:
            answer = source_ok(len(payload.get('sources')
                                   if payload.get('sources') is not None
                                   else payload.get('lands') or []))
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, tuple):
            return answer
        return 200, answer


class ChunkSourcesTests(unittest.TestCase):

    def test_fifty_one_items_become_two_requests_capped_at_fifty(self):
        batches = chunk_sources(source_items(51))
        self.assertEqual([len(batch) for batch in batches], [50, 1])
        for batch in batches:
            self.assertLessEqual(len(batch), MAX_SOURCE_BATCH_SIZE)

    def test_the_counts_at_and_around_the_cap(self):
        cases = {0: 0, 1: 1, 49: 1, 50: 1, 51: 2, 100: 2, 101: 3}
        for count, expected in sorted(cases.items()):
            with self.subTest(count):
                batches = chunk_sources(source_items(count))
                self.assertEqual(len(batches), expected)
                self.assertEqual(sum(len(b) for b in batches), count)

    def test_a_larger_batch_size_is_clamped_to_the_endpoint_cap(self):
        """[REASON]: drones.py answers 413 above 50 -- not a suggestion."""
        batches = chunk_sources(source_items(120), 5000)
        self.assertEqual(len(batches), 3)
        for batch in batches:
            self.assertLessEqual(len(batch), MAX_SOURCE_BATCH_SIZE)

    def test_a_smaller_batch_size_is_honoured(self):
        """NEGATIVE CONTROL: the clamp does not overwrite every setting."""
        self.assertEqual([len(b) for b in chunk_sources(source_items(5), 2)],
                         [2, 2, 1])

    def test_an_unset_batch_size_falls_back_to_the_endpoint_cap(self):
        for unset in (None, 0):
            with self.subTest(repr(unset)):
                self.assertEqual(
                    [len(b) for b in chunk_sources(source_items(51), unset)],
                    [50, 1])

    def test_a_negative_batch_size_does_not_loop_forever(self):
        self.assertEqual(len(chunk_sources(source_items(3), -5)), 3)

    def test_nothing_is_lost_or_reordered(self):
        items = source_items(101)
        rebuilt = [item for batch in chunk_sources(items) for item in batch]
        self.assertEqual([i['flight_id'] for i in rebuilt],
                         [i['flight_id'] for i in items])


class SourcePayloadTests(unittest.TestCase):

    def test_the_payload_is_the_token_and_the_sources(self):
        payload = build_source_payload('tok', source_items(2))
        self.assertEqual(sorted(payload), ['sources', 'token'])
        self.assertEqual(payload['token'], 'tok')
        self.assertEqual(len(payload['sources']), 2)

    def test_the_items_go_through_verbatim(self):
        """The endpoint re-hashes the body: nothing here may touch it."""
        items = source_items(1)
        payload = build_source_payload('tok', items)
        self.assertEqual(payload['sources'][0], items[0])


class SendSourcesTests(unittest.TestCase):

    def test_nothing_to_send_makes_no_request(self):
        post = SourceRecorder()
        result = send_sources([], config(), post_fn=post,
                              sleep_fn=lambda _s: None)
        self.assertEqual(post.calls, [])
        self.assertEqual(result.batches, 0)

    def test_the_request_goes_to_the_source_endpoint_with_the_token(self):
        post = SourceRecorder()
        send_sources(source_items(1), config(token='secret'), post_fn=post,
                     sleep_fn=lambda _s: None)
        self.assertTrue(post.calls[0]['url'].endswith(
            '/drones/api/source_sync'))
        self.assertEqual(post.calls[0]['payload']['token'], 'secret')

    def test_fifty_one_items_really_travel_in_two_requests(self):
        post = SourceRecorder([source_ok(50), source_ok(1)])
        result = send_sources(source_items(51), config(), post_fn=post,
                              sleep_fn=lambda _s: None)
        self.assertEqual(len(post.calls), 2)
        self.assertEqual([len(call['payload']['sources'])
                          for call in post.calls], [50, 1])
        self.assertEqual(result.batches, 2)
        self.assertEqual(result.seen, 51)

    def test_the_counters_are_summed_over_batches(self):
        post = SourceRecorder([source_ok(50, new=48, duplicates=2),
                               source_ok(1, new=0, errors=1, refreshed=3)])
        result = send_sources(source_items(51), config(), post_fn=post,
                              sleep_fn=lambda _s: None)
        self.assertEqual((result.seen, result.new, result.duplicates,
                          result.errors, result.refreshed),
                         (51, 48, 2, 1, 3))

    def test_counters_that_add_up_are_reported_as_agreeing(self):
        post = SourceRecorder([source_ok(4, new=3, duplicates=1)])
        self.assertTrue(send_sources(source_items(4), config(), post_fn=post,
                                     sleep_fn=lambda _s: None).counters_agree)

    def test_counters_that_do_not_add_up_are_caught_on_our_side(self):
        """NEGATIVE CONTROL: the invariant is checked, not assumed."""
        post = SourceRecorder([source_ok(4, new=1)])
        result = send_sources(source_items(4), config(), post_fn=post,
                              sleep_fn=lambda _s: None)
        self.assertFalse(result.counters_agree)

    def test_the_first_non_ok_status_wins(self):
        cfg = config()
        cfg.source_batch_size = 1
        post = SourceRecorder([source_ok(1), dict(source_ok(1),
                                                  status='error')])
        result = send_sources(source_items(2), cfg, post_fn=post,
                              sleep_fn=lambda _s: None)
        self.assertEqual(len(post.calls), 2)
        self.assertEqual(result.status, 'error')

    def test_an_all_ok_run_keeps_the_ok_status(self):
        """NEGATIVE CONTROL: the status is not pessimistic by default."""
        cfg = config()
        cfg.source_batch_size = 1
        post = SourceRecorder([source_ok(1), source_ok(1)])
        result = send_sources(source_items(2), cfg, post_fn=post,
                              sleep_fn=lambda _s: None)
        self.assertEqual(result.status, 'ok')

    def test_the_source_batch_size_of_the_config_is_the_one_used(self):
        cfg = config()
        cfg.source_batch_size = 10
        post = SourceRecorder()
        send_sources(source_items(25), cfg, post_fn=post,
                     sleep_fn=lambda _s: None)
        self.assertEqual([len(call['payload']['sources'])
                          for call in post.calls], [10, 10, 5])

    def test_413_is_not_retried(self):
        post = SourceRecorder([(413, {'error': 'batch too large'})])
        with self.assertRaises(IngestRejected):
            send_sources(source_items(1), config(), post_fn=post,
                         sleep_fn=lambda _s: None)
        self.assertEqual(len(post.calls), 1)

    def test_missing_counters_do_not_raise(self):
        self.assertEqual(SourceSendResult().add({'status': 'ok'}).as_dict(),
                         {'batches': 1, 'seen': 0, 'new': 0, 'duplicates': 0,
                          'errors': 0, 'refreshed': 0, 'status': 'ok'})


class SendLandSnapshotTests(unittest.TestCase):

    def chunk_body(self, lands=2, geometries=0, index=1, total=1,
                   final=True):
        return {
            'snapshot': {'capture_run_id': 'lands:20260908T041200Z',
                         'captured_at_utc': '2026-09-08 04:12:00',
                         'expected_count': lands, 'scope': {'walk': 'lands'},
                         'chunk': index, 'chunks': total, 'final': final,
                         'complete': True if final else None,
                         'manifest_sha256': 'a' * 64 if final else None},
            'lands': [{'uuid': 'u%d' % i} for i in range(lands)],
            'geometries': [{'content_md5': '%032d' % i, 'body_b64': 'AAAA'}
                           for i in range(geometries)],
        }

    def test_the_request_goes_to_the_snapshot_endpoint_with_the_token(self):
        post = SourceRecorder([snapshot_ok(2)])
        send_land_snapshot_chunk(self.chunk_body(), config(token='secret'),
                                 post_fn=post, sleep_fn=lambda _s: None)
        self.assertTrue(post.calls[0]['url'].endswith(
            '/drones/api/land_snapshot_sync'))
        self.assertEqual(post.calls[0]['payload']['token'], 'secret')

    def test_the_payload_is_the_snapshot_the_lands_and_the_geometries(self):
        post = SourceRecorder([snapshot_ok(2, geometries_seen=1)])
        send_land_snapshot_chunk(self.chunk_body(geometries=1), config(),
                                 post_fn=post, sleep_fn=lambda _s: None)
        payload = post.calls[0]['payload']
        self.assertEqual(sorted(payload),
                         ['geometries', 'lands', 'snapshot', 'token'])
        self.assertEqual(len(payload['lands']), 2)
        self.assertEqual(len(payload['geometries']), 1)

    def test_the_chunks_are_posted_in_order_and_only_the_last_is_final(self):
        """[REASON]: the receiver reads `final` PER REQUEST, so the order and
        the boundaries the collector chose must reach it exactly."""
        post = SourceRecorder([snapshot_ok(2), snapshot_ok(2),
                               snapshot_ok(1)])
        bodies = [self.chunk_body(lands=2, index=1, total=3, final=False),
                  self.chunk_body(lands=2, index=2, total=3, final=False),
                  self.chunk_body(lands=1, index=3, total=3, final=True)]
        for index, body in enumerate(bodies, start=1):
            send_land_snapshot_chunk(body, config(), post_fn=post,
                                     sleep_fn=lambda _s: None, index=index,
                                     total=3)
        sent = [call['payload']['snapshot'] for call in post.calls]
        self.assertEqual([s['chunk'] for s in sent], [1, 2, 3])
        self.assertEqual([s['final'] for s in sent], [False, False, True])
        self.assertEqual([s['manifest_sha256'] for s in sent],
                         [None, None, 'a' * 64])

    def test_a_chunk_over_the_cap_is_not_sent_at_all(self):
        post = SourceRecorder()
        with self.assertRaises(IngestRejected):
            send_land_snapshot_chunk(
                self.chunk_body(lands=MAX_LAND_SNAPSHOT_BATCH_SIZE + 1),
                config(), post_fn=post, sleep_fn=lambda _s: None)
        self.assertEqual(post.calls, [],
                         'a chunk the endpoint would 413 was still posted')

    def test_a_chunk_at_the_cap_is_sent(self):
        """NEGATIVE CONTROL: the cap is a cap, not an off-by-one refusal."""
        post = SourceRecorder([snapshot_ok(MAX_LAND_SNAPSHOT_BATCH_SIZE)])
        send_land_snapshot_chunk(
            self.chunk_body(lands=MAX_LAND_SNAPSHOT_BATCH_SIZE), config(),
            post_fn=post, sleep_fn=lambda _s: None)
        self.assertEqual(len(post.calls), 1)

    def test_the_counters_come_back_named(self):
        post = SourceRecorder([snapshot_ok(2, geometries_seen=1)])
        result = send_land_snapshot_chunk(self.chunk_body(geometries=1),
                                          config(), post_fn=post,
                                          sleep_fn=lambda _s: None)
        self.assertEqual(result.lands_seen, 2)
        self.assertEqual(result.geometries_seen, 1)
        self.assertEqual(result.snapshot_id, 7)
        self.assertTrue(result.counters_agree)

    def test_counters_that_do_not_add_up_are_caught(self):
        """NEGATIVE CONTROL to the agreement above."""
        result = LandSnapshotSendResult().add(
            dict(snapshot_ok(4), lands_new=1))
        self.assertFalse(result.counters_agree)

    def test_missing_counters_do_not_raise(self):
        result = LandSnapshotSendResult().add({'status': 'ok'})
        self.assertEqual(result.lands_seen, 0)
        self.assertEqual(result.batches, 1)

    def test_the_payload_builder_names_the_four_fields(self):
        payload = build_land_snapshot_payload('tok', {'chunk': 1},
                                              [{'uuid': 'u1'}], [])
        self.assertEqual(sorted(payload),
                         ['geometries', 'lands', 'snapshot', 'token'])


if __name__ == '__main__':
    unittest.main()
