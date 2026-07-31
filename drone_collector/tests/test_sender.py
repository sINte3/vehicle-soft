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

from drone_collector.config import MAX_BATCH_SIZE, CollectorConfig
from drone_collector.sender import (
    IngestRejected,
    SendResult,
    TransportError,
    build_payload,
    chunk,
    dry_run_path,
    send,
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


if __name__ == '__main__':
    unittest.main()
