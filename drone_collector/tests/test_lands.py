# -*- coding: utf-8 -*-
"""DRONE-LANDS-001: the Field Management directory walk.

The rejections matter more than the acceptances, and one of them is the whole
reason this file exists.

The page that lists the directory ALSO fires `graphql?name=landsCluster` --
the map's circle counts. Both responses come back HTTP 200 with the same
envelope (`code: 0`), so nothing about the transport distinguishes them; only
the operation name does, and only when it is compared exactly. A substring
test on 'name=lands' matches 'name=landsCluster' as well, and the walk would
then treat cluster payloads as pages of the directory: it would find no
`edges`, count them as rejections, and -- because the cluster fires on every
map interaction -- could exhaust the idle-scroll budget and end the walk early
with a partial snapshot that reports success. So the cluster URL has a
negative test of its own, and so does the `code: 200` trap that cost the
flight collector its first live run.

Every check below that asserts a behaviour also asserts the opposite case, so
that a check which cannot fail is not mistaken for a check that passed.
"""

import unittest

from drone_collector.lands import (
    BODY_CODE_OK,
    CapturedLands,
    DEFAULT_MAX_LAND_PAGES,
    LANDS_OPERATION,
    LandResult,
    body_code,
    classify_response,
    dedupe_lands,
    is_lands_url,
    lands_block,
)
from drone_collector.tests.support import load_fixture

GRAPHQL_BASE = 'https://kr-ag2-api.dji.com/ag-plot/api/graphql'


def lands_url(name=LANDS_OPERATION):
    return '%s?name=%s' % (GRAPHQL_BASE, name)


def envelope(edges=None, total=5489, has_next=True, cursor='20', code=0):
    """A directory response with the live envelope shape."""
    body = {'code': code, 'message': 'OK', 'request_id': 'x'}
    if edges is None:
        edges = []
    body['data'] = {'lands': {
        'totalCount': total,
        'pageInfo': {'hasNextPage': has_next, 'endCursor': cursor},
        'edges': edges,
    }}
    return body


def node_edge(uuid_text, name='Field', cursor='1'):
    return {'cursor': cursor, 'node': {'uuid': uuid_text, 'name': name}}


class UrlMatching(unittest.TestCase):
    """Check 1: the operation name is matched exactly, never as a substring."""

    def test_1_list_operation_is_accepted(self):
        self.assertTrue(is_lands_url(lands_url()))

    def test_1a_negative_cluster_operation_is_rejected(self):
        """THE trap. 'landsCluster' contains 'lands' and must NOT match."""
        self.assertFalse(is_lands_url(lands_url('landsCluster')))

    def test_1b_negative_other_operations_are_rejected(self):
        for other in ('userProfile', 'departmentTree', 'landsDetail', 'land'):
            self.assertFalse(is_lands_url(lands_url(other)),
                             'operation %r must not be captured' % other)

    def test_1c_negative_a_non_graphql_url_is_rejected(self):
        self.assertFalse(
            is_lands_url('https://www.djiag.com/api/web/v1/flight_records?page=1'))

    def test_1d_negative_empty_and_missing_urls_are_rejected(self):
        self.assertFalse(is_lands_url(''))
        self.assertFalse(is_lands_url(None))

    def test_1e_extra_query_parameters_do_not_break_the_match(self):
        self.assertTrue(is_lands_url(lands_url() + '&v=2'))
        self.assertTrue(is_lands_url('%s?v=2&name=%s' % (GRAPHQL_BASE,
                                                         LANDS_OPERATION)))


class BodyCode(unittest.TestCase):
    """Check 2: success is code 0 -- not HTTP status, and not 200."""

    def test_2_code_zero_is_the_success_value(self):
        self.assertEqual(BODY_CODE_OK, 0)
        self.assertEqual(body_code({'code': 0}), 0)

    def test_2a_negative_code_200_is_not_success(self):
        """200 is the envelope's `status`, never its `code`."""
        accepted, reason = classify_response(lands_url(),
                                             envelope(code=200))
        self.assertFalse(accepted)
        self.assertEqual(reason, 'code-200')

    def test_2b_negative_signature_and_timestamp_rejections(self):
        for code, reason in ((101, 'code-101'), (408, 'code-408')):
            accepted, got = classify_response(lands_url(), envelope(code=code))
            self.assertFalse(accepted)
            self.assertEqual(got, reason)

    def test_2c_negative_a_body_that_is_not_an_object(self):
        self.assertIsNone(body_code('nonsense'))
        self.assertIsNone(body_code(None))
        accepted, reason = classify_response(lands_url(), 'nonsense')
        self.assertFalse(accepted)
        self.assertEqual(reason, 'code-none')

    def test_2d_negative_boolean_code_is_not_an_integer_code(self):
        self.assertIsNone(body_code({'code': True}))


class Envelope(unittest.TestCase):
    """Check 3: code 0 alone is not enough -- the edges list must be there."""

    def test_3_a_page_with_edges_is_accepted(self):
        accepted, reason = classify_response(
            lands_url(), envelope(edges=[node_edge('a')]))
        self.assertTrue(accepted)
        self.assertEqual(reason, 'ok')

    def test_3a_an_empty_edges_list_is_still_a_page(self):
        """The last page of a filtered search legitimately has no rows."""
        accepted, _reason = classify_response(lands_url(), envelope(edges=[]))
        self.assertTrue(accepted)

    def test_3b_negative_code_zero_without_an_edges_list_is_rejected(self):
        body = {'code': 0, 'data': {'lands': {'totalCount': 5489}}}
        accepted, reason = classify_response(lands_url(), body)
        self.assertFalse(accepted)
        self.assertEqual(reason, 'no-edges')

    def test_3c_negative_code_zero_with_no_lands_block(self):
        body = {'code': 0, 'data': {'landsCluster': {'edges': []}}}
        self.assertIsNone(lands_block(body))
        accepted, reason = classify_response(lands_url(), body)
        self.assertFalse(accepted)
        self.assertEqual(reason, 'no-edges')

    def test_3d_negative_the_cluster_url_is_rejected_before_the_body(self):
        """Even a perfectly shaped body is refused on a cluster URL."""
        accepted, reason = classify_response(lands_url('landsCluster'),
                                             envelope(edges=[node_edge('a')]))
        self.assertFalse(accepted)
        self.assertEqual(reason, 'not-lands')


class Captured(unittest.TestCase):
    """Check 4: the page reads its own size, cursor and end-of-list flag."""

    def test_4_reads_the_live_fixture(self):
        page = CapturedLands(lands_url(), load_fixture('lands_page1.json'))
        self.assertEqual(len(page.nodes), 3)
        self.assertEqual(page.total_count, 5489)
        self.assertIs(page.has_next_page, True)
        self.assertEqual(page.end_cursor, '3')
        self.assertEqual(page.nodes[0]['name'], 'Azamat 3')

    def test_4a_last_page_reports_no_next(self):
        page = CapturedLands(lands_url(), envelope(edges=[], has_next=False))
        self.assertIs(page.has_next_page, False)

    def test_4b_negative_a_page_that_omits_the_flag_says_None_not_False(self):
        """[REASON]: the walk stops on `is False`. A missing flag must not be
        read as "no more pages" -- that would end the walk on the first page
        of a payload whose shape changed."""
        body = envelope(edges=[node_edge('a')])
        del body['data']['lands']['pageInfo']['hasNextPage']
        page = CapturedLands(lands_url(), body)
        self.assertIsNone(page.has_next_page)
        self.assertIsNot(page.has_next_page, False)

    def test_4c_edges_without_a_node_object_are_skipped(self):
        body = envelope(edges=[node_edge('a'), {'cursor': '2'},
                               {'cursor': '3', 'node': 'text'}])
        self.assertEqual(len(CapturedLands(lands_url(), body).nodes), 1)

    def test_4d_negative_total_count_that_is_not_a_number(self):
        page = CapturedLands(lands_url(), envelope(total='many'))
        self.assertIsNone(page.total_count)


class Dedupe(unittest.TestCase):
    """Check 5: an offset-paginated list that shifts serves rows twice."""

    def test_5_repeated_uuid_is_dropped_once(self):
        nodes = [{'uuid': 'a'}, {'uuid': 'b'}, {'uuid': 'a'}]
        unique, duplicates = dedupe_lands(nodes)
        self.assertEqual([n['uuid'] for n in unique], ['a', 'b'])
        self.assertEqual(duplicates, 1)

    def test_5a_first_occurrence_wins(self):
        nodes = [{'uuid': 'a', 'name': 'first'}, {'uuid': 'a', 'name': 'later'}]
        unique, _ = dedupe_lands(nodes)
        self.assertEqual(unique[0]['name'], 'first')

    def test_5b_negative_distinct_uuids_are_never_merged(self):
        nodes = [{'uuid': 'a'}, {'uuid': 'b'}, {'uuid': 'c'}]
        unique, duplicates = dedupe_lands(nodes)
        self.assertEqual(len(unique), 3)
        self.assertEqual(duplicates, 0)

    def test_5c_a_row_without_a_uuid_is_kept_not_dropped(self):
        """It cannot be deduplicated; the ingest rejects it and COUNTS it."""
        nodes = [{'uuid': 'a'}, {'name': 'no id'}, {'name': 'no id either'}]
        unique, duplicates = dedupe_lands(nodes)
        self.assertEqual(len(unique), 3)
        self.assertEqual(duplicates, 0)

    def test_5d_non_objects_are_dropped(self):
        unique, _ = dedupe_lands([{'uuid': 'a'}, 'text', None])
        self.assertEqual(len(unique), 1)


class Completeness(unittest.TestCase):
    """Check 6: a short walk is a failure, and says so."""

    def make(self, collected, total):
        return LandResult(lands=[{'uuid': str(i)} for i in range(collected)],
                          pages_captured=1, total_count=total,
                          nodes_captured=collected, self_duplicates=0,
                          rejected={}, complete=(total is not None
                                                 and collected >= total))

    def test_6_a_full_walk_is_complete(self):
        self.assertTrue(self.make(5489, 5489).complete)

    def test_6a_negative_a_short_walk_is_not_complete(self):
        self.assertFalse(self.make(5488, 5489).complete)

    def test_6b_negative_an_unknown_total_is_not_complete(self):
        """[REASON]: without a stated total there is nothing to check against,
        and "we do not know" must not render as "we are done"."""
        self.assertFalse(self.make(100, None).complete)


class Guards(unittest.TestCase):
    """Check 7: the runaway guard is above the real directory, not below it."""

    def test_7_page_cap_clears_the_real_directory(self):
        # 5 489 contours at 20 per page is 275 pages.
        self.assertGreater(DEFAULT_MAX_LAND_PAGES, 275 * 2)


class Cli(unittest.TestCase):
    """Check 8: --lands takes no period, and the flag reaches the parser."""

    def test_8_flag_parses(self):
        from drone_collector.main import build_parser
        args = build_parser().parse_args(['--lands'])
        self.assertTrue(args.lands)
        self.assertIsNone(args.date_from)

    def test_8a_negative_default_is_a_flight_run(self):
        from drone_collector.main import build_parser
        self.assertFalse(build_parser().parse_args([]).lands)

    def test_8b_lands_with_a_period_is_refused(self):
        import logging

        from drone_collector.main import EXIT_CONFIG, _run

        messages = []

        class Sink(logging.Logger):
            pass

        logger = logging.getLogger('test-lands-usage')
        logger.handlers = []
        logger.error = lambda msg, *a: messages.append(msg % a if a else msg)
        logger.info = lambda *a, **k: None

        code = _run(['--lands', '--from', '2025-09-01', '--to', '2025-09-30'],
                    logger, {})
        self.assertEqual(code, EXIT_CONFIG)
        self.assertTrue(any('--lands takes no period' in m for m in messages),
                        messages)

    def test_8c_negative_a_period_without_lands_is_accepted(self):
        """Proof the check above rejects the combination, not the period."""
        from drone_collector.main import build_parser
        args = build_parser().parse_args(['--from', '2025-09-01',
                                          '--to', '2025-09-30'])
        self.assertEqual(args.date_from, '2025-09-01')
        self.assertFalse(args.lands)


class IncompleteMessage(unittest.TestCase):
    """Check 10: a short walk must not claim data was sent on a dry run.

    Found on the first real production run 2026-08-18: the dry run stalled at
    3 040 of 5 489 and printed "What was collected has been sent" -- while
    --dry-run sends nothing at all. The message decided what the operator
    would do next, and it was false.
    """

    def test_10_a_real_run_says_what_was_collected_was_sent(self):
        from drone_collector.main import incomplete_walk_message
        text = incomplete_walk_message(3040, 5489, dry_run=False)
        self.assertIn('3040 contour(s) collected of 5489', text)
        self.assertIn('has been sent', text)
        self.assertIn('re-run --lands to finish it', text)

    def test_10a_a_dry_run_says_nothing_was_sent(self):
        from drone_collector.main import incomplete_walk_message
        text = incomplete_walk_message(3040, 5489, dry_run=True)
        self.assertIn('Nothing was sent', text)
        self.assertNotIn('has been sent', text)

    def test_10b_negative_the_two_messages_differ(self):
        """Without this the pair above would pass on one shared sentence."""
        from drone_collector.main import incomplete_walk_message
        self.assertNotEqual(incomplete_walk_message(3040, 5489, True),
                            incomplete_walk_message(3040, 5489, False))

    def test_10c_an_unknown_total_reads_as_unknown_not_as_None(self):
        from drone_collector.main import incomplete_walk_message
        text = incomplete_walk_message(3040, None, dry_run=False)
        self.assertIn('an unknown number', text)
        self.assertNotIn('None', text)


class Sender(unittest.TestCase):
    """Check 9: the snapshot body, its batching and its counters."""

    def make_cfg(self, batch_size=1000):
        class Cfg(object):
            pass
        cfg = Cfg()
        cfg.batch_size = batch_size
        cfg.api_token = 'secret-token'
        cfg.land_sync_url = 'http://localhost:5050/drones/api/land_sync'
        return cfg

    def test_9_payload_carries_the_token_and_the_lands(self):
        from drone_collector.sender import build_land_payload
        body = build_land_payload('tok', [{'uuid': 'a'}])
        self.assertEqual(body, {'token': 'tok', 'lands': [{'uuid': 'a'}]})

    def test_9a_no_kind_and_no_period_in_the_body(self):
        """A directory snapshot has neither; sending them would be a lie."""
        from drone_collector.sender import build_land_payload
        body = build_land_payload('tok', [])
        self.assertNotIn('kind', body)
        self.assertNotIn('period_from', body)

    def test_9b_batches_are_capped(self):
        from drone_collector.sender import send_lands
        posted = []

        def fake_post(url, payload, timeout):
            posted.append(len(payload['lands']))
            return 200, {'seen': len(payload['lands']), 'new': 0, 'updated': 0,
                         'unchanged': len(payload['lands']), 'errors': 0}

        lands = [{'uuid': str(i)} for i in range(2500)]
        result = send_lands(lands, self.make_cfg(1000), post_fn=fake_post,
                            sleep_fn=lambda _s: None)
        self.assertEqual(posted, [1000, 1000, 500])
        self.assertEqual(result.seen, 2500)
        self.assertEqual(result.unchanged, 2500)
        self.assertEqual(result.batches, 3)

    def test_9c_negative_nothing_is_posted_for_an_empty_snapshot(self):
        from drone_collector.sender import send_lands
        posted = []

        def fake_post(url, payload, timeout):  # pragma: no cover -- must not run
            posted.append(payload)
            return 200, {}

        result = send_lands([], self.make_cfg(), post_fn=fake_post,
                            sleep_fn=lambda _s: None)
        self.assertEqual(posted, [])
        self.assertEqual(result.batches, 0)

    def test_9d_a_refusal_is_raised_not_swallowed(self):
        from drone_collector.sender import IngestRejected, send_lands

        def fake_post(url, payload, timeout):
            return 401, {'error': 'unauthorized'}

        with self.assertRaises(IngestRejected):
            send_lands([{'uuid': 'a'}], self.make_cfg(), post_fn=fake_post,
                       sleep_fn=lambda _s: None)


if __name__ == '__main__':
    unittest.main()
