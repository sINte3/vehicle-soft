# -*- coding: utf-8 -*-
"""Tests for drone_collector/sources.py -- DJI-AREA-EVIDENCE-001.

No network, no browser, no database. Playwright is replaced by fakes in the
style of `test_route_ui_probe.py`; the protobuf route bodies are BUILT by the
helpers of `test_route_decode.py` rather than committed as a blob, so a test
states the shape it expects instead of trusting the decoder to agree with
itself.

Every host here ends in `.invalid` and every credential-shaped string carries
`NOT-REAL`: this file exercises the code that REFUSES signed links, so it has
to contain strings that look like them, and none of them may ever be mistaken
for something that once worked.

Almost every guard has a negative control next to it. A check that answers the
same way on correct and on broken code is not a check, and this module's whole
job is to keep bodies that must not be stored out of the queue.

Run from the repository root:

    python -m unittest discover -s drone_collector/tests -t .
"""

import base64
import hashlib
import json
import os
import sys
import tempfile
import unittest

from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from drone_collector.config import (  # noqa: E402
    DEFAULT_SOURCE_BATCH_SIZE, MAX_LAND_SNAPSHOT_BATCH_SIZE,
    MAX_SOURCE_BATCH_SIZE, CollectorConfig)
from drone_collector.outbox import (  # noqa: E402
    KIND_LAND_SNAPSHOT, KIND_SOURCE, Outbox, SecretInEnvelope,
    find_secret_markers)
from drone_collector.sender import (  # noqa: E402
    LandSnapshotSendResult, SourceSendResult)
from drone_collector.sources import (  # noqa: E402
    MAX_CONSECUTIVE_PAGE_ERRORS,
    ROUTE_IDENTITY_MISMATCH,
    ROUTE_IDENTITY_OK,
    ROUTE_IDENTITY_UNDECODABLE,
    SCHEMA_AIRLINES_PATHS_ONLY,
    SCHEMA_RAW_HTTP_BODY,
    SOURCE_AIRLINES,
    SOURCE_CARD,
    SOURCE_ROUTE,
    SOURCE_V4,
    SOURCES_MODE_VERSION,
    STATUS_NO_V4,
    STATUS_NO_V4_URL,
    STATUS_PAGE_ERROR,
    STATUS_V4,
    STATUS_V4_FAILED,
    FlightSources,
    SourceCapture,
    SourceRun,
    airlines_bytes,
    airlines_document,
    api_version_of,
    body_secret_markers,
    capture_run_id,
    classify_source_url,
    download_snapshot_geometries,
    drain_land_snapshot_outbox,
    drain_source_outbox,
    enqueue_snapshot_chunks,
    enqueue_sources,
    flight_already_captured,
    host_and_path,
    known_sources,
    route_identity,
    snapshot_chunk_bodies,
    snapshot_manifest_sha256,
    snapshot_run_id,
    source_item,
    source_items,
    source_refusal_reasons,
    snapshot_refusal_reasons,
    strip_signed_urls,
    url_path,
    v4_url_present,
    write_snapshot_dry_run,
    write_sources_dry_run,
)
from drone_collector.tests.test_route_decode import (  # noqa: E402
    f_bytes, f_varint, response, route_record)


# ─── Invented values. Every host is `.invalid`, every secret is NOT-REAL ─────

FLIGHT_ID = 673501214
OTHER_FLIGHT_ID = 673501299

API_HOST = 'https://kr-ag2-api.example.invalid'
STORAGE_HOST = 'https://storage.example.invalid'

CARD_URL = '%s/api/web/v1/flight_records/%d' % (API_HOST, FLIGHT_ID)
LIST_URL = '%s/api/web/v1/flight_records?page=1&page_size=30' % API_HOST
IDS_URL = '%s/api/web/v1/flight_records/only_all_ids' % API_HOST
ROUTE_URL = '%s/api/web/v2/flight_datas/flight_records' % API_HOST
AIRLINES_URL = '%s/api/web/v2/flight_datas/airlines/%d' % (API_HOST, FLIGHT_ID)

# The query string IS the credential; the collector must keep the path only.
V4_QUERY = ('Expires=1790000000&OSSAccessKeyId=NOTREALKEYID0000'
            '&Signature=NOT-REAL-SIGNATURE-V4')
V4_PATH_ONLY = '/objects/airline_v4/%d/airline_v4_NOT_REAL.pb' % FLIGHT_ID
V4_URL = '%s%s?%s' % (STORAGE_HOST, V4_PATH_ONLY, V4_QUERY)

DETAIL_URL = ('%s/objects/std_detail/%d/detail_NOT_REAL.pb?%s'
              % (STORAGE_HOST, FLIGHT_ID, V4_QUERY))
SUMMARY_URL = ('%s/objects/std_summary/%d/summary_NOT_REAL.pb?%s'
               % (STORAGE_HOST, FLIGHT_ID, V4_QUERY))

RECORD_URL_TEMPLATE = 'https://www.djiag.invalid/record/{id}'

CAPTURED_AT = '2026-09-08 04:12:00'
RUN_ID = 'sources:2026-08-01_2026-08-31:20260908T041200Z'


def card_body(flight_id=FLIGHT_ID, code=0):
    return json.dumps({
        'code': code, 'status': 200,
        'data': {'id': flight_id, 'work_area': 12.5, 'nickname': '9 Fixture'},
    }, ensure_ascii=False).encode('utf-8')


def airlines_raw(flight_id=FLIGHT_ID, v4_url=V4_URL, code=0):
    """The RAW airlines body -- three signed links and nothing else of use."""
    airline = {'id': flight_id,
               'std_detail_url': DETAIL_URL,
               'std_summary_url': SUMMARY_URL,
               'file_v4_url': v4_url}
    return json.dumps({'code': code, 'status': 200,
                       'data': {'airline': airline}},
                      ensure_ascii=False).encode('utf-8')


def route_body(flight_id=FLIGHT_ID):
    return response([route_record(flight_id=flight_id)])


def v4_body(size=512):
    """A protobuf-shaped blob. Not decoded by anything here; only stored."""
    return f_varint(1, 200) + f_bytes(2, b'V' * size)


def ids_post_data(flight_id=FLIGHT_ID):
    return json.dumps({'flight_record_ids': [flight_id],
                       'data_type': 'simplified'})


def source_config(outbox_dir=None, source_wait_ms=10000, source_pause_ms=0,
                  source_batch_size=DEFAULT_SOURCE_BATCH_SIZE,
                  record_url_template=RECORD_URL_TEMPLATE):
    return CollectorConfig(
        records_url='https://www.djiag.invalid/records/list',
        storage_state=Path('data/storage_state.json'),
        headless=True, window_days=30, tz_offset_hours=5,
        page_timeout_ms=45000, settle_ms=0, max_pages=500,
        base_url='http://vehicle-soft.invalid:5050',
        api_token='TOKEN-NOT-REAL', batch_size=500,
        outbox_dir=outbox_dir,
        record_url_template=record_url_template,
        source_wait_ms=source_wait_ms, source_pause_ms=source_pause_ms,
        source_batch_size=source_batch_size)


# ─── Playwright fakes ────────────────────────────────────────────────────────

class _QuietLog(object):
    def __init__(self):
        self.records = []

    def _note(self, level, message, *args):
        self.records.append((level, message % args if args else message))

    def info(self, message, *args):
        self._note('info', message, *args)

    def warning(self, message, *args):
        self._note('warning', message, *args)

    def error(self, message, *args):
        self._note('error', message, *args)

    def text(self):
        return '\n'.join(text for _level, text in self.records)


class _FakeResponse(object):
    def __init__(self, body=b'', status=200, body_error=None):
        self._body = body
        self.status = status
        self.body_error = body_error

    def body(self):
        if self.body_error is not None:
            raise self.body_error
        return self._body


class _FakeRequest(object):
    """A Playwright request: a URL, a method and -- once done -- its response."""

    def __init__(self, url, method='GET', post_data=None, body=b'',
                 status=200, response_error=None, body_error=None,
                 no_response=False):
        self.url = url
        self.method = method
        self.post_data = post_data
        self.response_error = response_error
        self.response_object = (None if no_response
                                else _FakeResponse(body, status,
                                                   body_error=body_error))

    def response(self):
        if self.response_error is not None:
            raise self.response_error
        return self.response_object


class _FakeRoute(object):
    """A Playwright route: the handler fetches, fulfils and keeps the bytes."""

    def __init__(self, url=V4_URL, body=None, status=200, fetch_error=None,
                 fulfill_error=None):
        self.request = _FakeRequest(url)
        self._response = _FakeResponse(v4_body() if body is None else body,
                                       status)
        self.fetch_error = fetch_error
        self.fulfill_error = fulfill_error
        self.fetched = 0
        self.fulfilled = []
        self.continued = 0

    def fetch(self):
        self.fetched += 1
        if self.fetch_error is not None:
            raise self.fetch_error
        return self._response

    def fulfill(self, response=None, body=None):
        if self.fulfill_error is not None:
            raise self.fulfill_error
        self.fulfilled.append((response, body))

    def continue_(self):
        self.continued += 1


class _FakePage(object):
    """The page the run drives. `script` delivers one event per pumped wait.

    [REASON]: the events are delivered from `wait_for_timeout`, not before the
    visit, because that is where they arrive in life -- the SPA fetches its
    own bodies WHILE the run waits. A fake that delivered everything up front
    would make `pump_until` untested and the settle logic unfalsifiable.
    """

    def __init__(self, script=None, goto_error=None,
                 landing_url='https://www.djiag.invalid/record/1'):
        self.handlers = {}
        self.routes = []
        self.script = list(script or [])
        self.goto_error = goto_error
        self.landing_url = landing_url
        self.url = landing_url
        self.goto_calls = []
        self.waits = []

    # -- the Playwright surface the capture uses ------------------------------

    def on(self, event, handler):
        self.handlers.setdefault(event, []).append(handler)

    def route(self, pattern, handler):
        self.routes.append((pattern, handler))

    def goto(self, url, **kwargs):
        self.goto_calls.append(url)
        if self.goto_error is not None:
            raise self.goto_error
        self.url = self.landing_url or url

    def wait_for_timeout(self, ms):
        self.waits.append(ms)
        if self.script:
            self.script.pop(0)(self)

    # -- delivery -------------------------------------------------------------

    def deliver_finished(self, request):
        for handler in self.handlers.get('requestfinished', []):
            handler(request)

    def deliver_failed(self, request):
        for handler in self.handlers.get('requestfailed', []):
            handler(request)

    def deliver_route(self, route):
        """Only the handlers whose pattern matches, as Playwright does it."""
        url = route.request.url
        for pattern, handler in self.routes:
            if pattern.search(url):
                handler(route)


class _Clock(object):
    """A monotonic clock in milliseconds that never waits."""

    def __init__(self, step=250):
        self.step = step
        self.value = 0

    def __call__(self):
        current = self.value
        self.value += self.step
        return current


def step_finished(request):
    return lambda page: page.deliver_finished(request)


def step_route(route):
    return lambda page: page.deliver_route(route)


def card_request(flight_id=FLIGHT_ID, code=0, **kwargs):
    return _FakeRequest('%s/api/web/v1/flight_records/%d' % (API_HOST,
                                                             flight_id),
                        body=card_body(flight_id, code=code), **kwargs)


def airlines_request(flight_id=FLIGHT_ID, v4_url=V4_URL, **kwargs):
    return _FakeRequest('%s/api/web/v2/flight_datas/airlines/%d'
                        % (API_HOST, flight_id),
                        body=airlines_raw(flight_id, v4_url=v4_url), **kwargs)


def route_request(flight_id=FLIGHT_ID, embedded_id=None, **kwargs):
    return _FakeRequest(ROUTE_URL, method='POST',
                        post_data=ids_post_data(flight_id),
                        body=route_body(flight_id if embedded_id is None
                                        else embedded_id), **kwargs)


def full_visit_script(flight_id=FLIGHT_ID, v4_url=V4_URL, v4=None):
    route = v4 if v4 is not None else _FakeRoute(
        url='%s/objects/airline_v4/%d/airline_v4_NOT_REAL.pb?%s'
            % (STORAGE_HOST, flight_id, V4_QUERY))
    return [step_finished(card_request(flight_id)),
            step_finished(route_request(flight_id)),
            step_finished(airlines_request(flight_id, v4_url=v4_url)),
            step_route(route)]


# ─── 1. What each URL is ─────────────────────────────────────────────────────

class ClassifyTests(unittest.TestCase):
    """The four sources of one visit, told apart by path and method alone."""

    def test_the_card_url_is_a_card_and_names_the_flight(self):
        self.assertEqual(classify_source_url(CARD_URL), (SOURCE_CARD,
                                                         FLIGHT_ID))

    def test_the_route_post_is_a_route_and_names_nobody(self):
        self.assertEqual(classify_source_url(ROUTE_URL, 'POST'),
                         (SOURCE_ROUTE, None))

    def test_the_airlines_url_is_airlines_and_names_the_flight(self):
        self.assertEqual(classify_source_url(AIRLINES_URL),
                         (SOURCE_AIRLINES, FLIGHT_ID))

    def test_the_signed_v4_url_is_a_v4_and_names_the_flight(self):
        self.assertEqual(classify_source_url(V4_URL), (SOURCE_V4, FLIGHT_ID))

    def test_a_get_on_the_route_path_is_not_a_route_post(self):
        """The route is a POST. A GET on the same path is something else."""
        self.assertEqual(classify_source_url(ROUTE_URL, 'GET'), (None, None))

    def test_the_flight_list_is_not_a_card(self):
        """[REASON]: the list has been mistaken for a card once already."""
        self.assertEqual(classify_source_url(LIST_URL), (None, None))
        self.assertEqual(classify_source_url(IDS_URL), (None, None))
        self.assertEqual(
            classify_source_url('%s/api/web/v1/flight_records/overview'
                                % API_HOST), (None, None))

    def test_an_unrelated_url_classifies_to_nothing(self):
        for url in ('https://www.djiag.invalid/record/673501214',
                    'https://fonts.example.invalid/x.woff2',
                    '%s/api/web/v1/users/me' % API_HOST,
                    ''):
            with self.subTest(url):
                self.assertEqual(classify_source_url(url), (None, None))

    def test_a_trailing_slash_does_not_hide_a_card(self):
        self.assertEqual(classify_source_url(CARD_URL + '/'),
                         (SOURCE_CARD, FLIGHT_ID))

    def test_the_api_version_travels_with_the_path(self):
        self.assertEqual(api_version_of(url_path(CARD_URL)), 'v1')
        self.assertEqual(api_version_of(url_path(ROUTE_URL)), 'v2')
        self.assertEqual(api_version_of(V4_PATH_ONLY), 'storage-object')
        self.assertIsNone(api_version_of('/record/1'))

    def test_the_path_never_carries_the_query(self):
        self.assertEqual(url_path(V4_URL), V4_PATH_ONLY)
        self.assertNotIn('?', url_path(V4_URL))
        self.assertNotIn('Signature', url_path(V4_URL))


# ─── 2. The airlines descriptor: paths only, never the links ─────────────────

class AirlinesDocumentTests(unittest.TestCase):

    def setUp(self):
        self.raw = airlines_raw()
        self.document = airlines_document(self.raw)

    def test_the_raw_body_really_carries_the_signature(self):
        """NEGATIVE CONTROL for everything below.

        If the fixture did not carry a credential, 'the stored document has
        none' would be true of any code at all.
        """
        self.assertIn(b'Signature', self.raw)
        self.assertIn(b'OSSAccessKeyId', self.raw)
        self.assertIn(b'Expires', self.raw)

    def test_only_the_code_status_and_three_paths_survive(self):
        self.assertEqual(sorted(self.document),
                         ['code', 'file_v4_url_path', 'status',
                          'std_detail_url_path', 'std_summary_url_path'])
        self.assertEqual(self.document['code'], 0)
        self.assertEqual(self.document['status'], 200)

    def test_a_path_is_host_and_path_without_the_query(self):
        self.assertEqual(self.document['file_v4_url_path'],
                         'storage.example.invalid' + V4_PATH_ONLY)

    def test_the_stored_bytes_carry_no_signature_at_all(self):
        stored = airlines_bytes(self.document).decode('utf-8')
        for marker in ('Signature', 'OSSAccessKeyId', 'Expires', '?', '&'):
            self.assertNotIn(marker, stored,
                             'the airlines record kept %r' % marker)

    def test_the_stored_bytes_pass_the_queue_detector(self):
        self.assertEqual(body_secret_markers(airlines_bytes(self.document)),
                         [])

    def test_the_raw_body_would_not_pass_the_detector(self):
        """NEGATIVE CONTROL: the detector is able to tell the two apart."""
        self.assertNotEqual(body_secret_markers(self.raw), [])

    def test_the_bytes_are_canonical_and_stable(self):
        first = airlines_bytes(airlines_document(airlines_raw()))
        second = airlines_bytes(airlines_document(airlines_raw()))
        self.assertEqual(first, second)

    def test_no_v4_url_is_recorded_as_a_finding(self):
        for missing in (None, ''):
            with self.subTest(repr(missing)):
                document = airlines_document(airlines_raw(v4_url=missing))
                self.assertIsNone(document['file_v4_url_path'])
                self.assertFalse(v4_url_present(document))

    def test_a_v4_url_is_present_when_dji_sent_one(self):
        """NEGATIVE CONTROL: the verdict is not always 'no V4'."""
        self.assertTrue(v4_url_present(self.document))

    def test_an_airline_without_the_key_at_all_is_no_v4(self):
        raw = json.dumps({'code': 0, 'status': 200,
                          'data': {'airline': {'id': FLIGHT_ID}}}).encode()
        self.assertFalse(v4_url_present(airlines_document(raw)))

    def test_a_body_that_is_not_the_airlines_json_is_no_document(self):
        for raw in (b'', b'not json', b'[1, 2, 3]', b'\xff\xfe\x00'):
            with self.subTest(repr(raw)):
                self.assertIsNone(airlines_document(raw))

    def test_a_missing_data_object_still_yields_a_document(self):
        raw = json.dumps({'code': 101, 'status': 200}).encode()
        document = airlines_document(raw)
        self.assertEqual(document['code'], 101)
        self.assertIsNone(document['file_v4_url_path'])

    def test_host_and_path_drops_the_query_and_the_fragment(self):
        self.assertEqual(host_and_path(V4_URL),
                         'storage.example.invalid' + V4_PATH_ONLY)
        self.assertEqual(host_and_path(V4_URL + '#frag'),
                         'storage.example.invalid' + V4_PATH_ONLY)
        self.assertIsNone(host_and_path(''))


# ─── 3. Whose route is this ──────────────────────────────────────────────────

class RouteIdentityTests(unittest.TestCase):

    def test_the_same_flight_id_is_ok(self):
        status, ids = route_identity(route_body(FLIGHT_ID), FLIGHT_ID)
        self.assertEqual(status, ROUTE_IDENTITY_OK)
        self.assertEqual(ids, [FLIGHT_ID])

    def test_another_flight_id_is_a_mismatch(self):
        status, ids = route_identity(route_body(OTHER_FLIGHT_ID), FLIGHT_ID)
        self.assertEqual(status, ROUTE_IDENTITY_MISMATCH)
        self.assertEqual(ids, [OTHER_FLIGHT_ID])

    def test_two_different_ids_in_one_body_are_a_mismatch(self):
        body = response([route_record(flight_id=FLIGHT_ID),
                         route_record(flight_id=OTHER_FLIGHT_ID)])
        status, ids = route_identity(body, FLIGHT_ID)
        self.assertEqual(status, ROUTE_IDENTITY_MISMATCH)
        self.assertEqual(sorted(ids), sorted([FLIGHT_ID, OTHER_FLIGHT_ID]))

    def test_an_undecodable_body_has_its_own_verdict(self):
        for raw in (b'', b'\xff\xff\xff\xff', b'not protobuf at all'):
            with self.subTest(repr(raw)):
                status, ids = route_identity(raw, FLIGHT_ID)
                self.assertEqual(status, ROUTE_IDENTITY_UNDECODABLE)
                self.assertEqual(ids, [])

    def test_a_body_naming_nobody_disagrees_with_nobody(self):
        status, ids = route_identity(response([]), FLIGHT_ID)
        self.assertEqual(status, ROUTE_IDENTITY_OK)
        self.assertEqual(ids, [])

    def test_a_mismatched_route_is_still_captured_counted_and_queued(self):
        """The receiver quarantines it -- but it must ARRIVE to be quarantined.

        [REASON]: a route filed under the wrong flight is exactly the quiet
        wrong number this track exists to prevent. Dropping it here would
        destroy the only evidence that DJI answered with another flight.
        """
        capture = SourceCapture(logger=_QuietLog())
        page = _FakePage()
        capture.attach(page)
        capture.begin_flight(FLIGHT_ID)
        page.deliver_finished(route_request(FLIGHT_ID,
                                            embedded_id=OTHER_FLIGHT_ID))

        flight = capture.flight(FLIGHT_ID)
        self.assertTrue(flight.has(SOURCE_ROUTE))
        self.assertEqual(flight.route_identity, ROUTE_IDENTITY_MISMATCH)
        self.assertEqual(flight.route_embedded_ids, [OTHER_FLIGHT_ID])

        items = source_items(flight, RUN_ID)
        self.assertEqual([item['source_type'] for item in items],
                         [SOURCE_ROUTE])
        self.assertEqual(items[0]['request_context']['route_identity'],
                         ROUTE_IDENTITY_MISMATCH)
        # The route is filed under the flight the page ASKED for -- the id
        # in the POST body -- and the disagreeing id is what the verdict
        # names. Filing it under the id DJI answered with would hide the
        # mismatch by making the two agree.
        self.assertEqual(items[0]['request_context']['visited_flight_id'],
                         FLIGHT_ID)
        self.assertEqual(items[0]['flight_id'], FLIGHT_ID)

    def test_a_matching_route_is_not_flagged(self):
        """NEGATIVE CONTROL: the flag is not set on every route."""
        capture = SourceCapture(logger=_QuietLog())
        page = _FakePage()
        capture.attach(page)
        capture.begin_flight(FLIGHT_ID)
        page.deliver_finished(route_request(FLIGHT_ID))
        self.assertEqual(capture.flight(FLIGHT_ID).route_identity,
                         ROUTE_IDENTITY_OK)


# ─── 4. One item of the request ──────────────────────────────────────────────

class SourceItemTests(unittest.TestCase):

    def build(self, body=b'{"code":0}', path='/api/web/v1/flight_records/1'):
        return source_item(FLIGHT_ID, SOURCE_CARD, body, CAPTURED_AT, path,
                           'url_path', 0, RUN_ID,
                           schema_version=SCHEMA_RAW_HTTP_BODY)

    def test_the_body_round_trips_through_base64(self):
        body = card_body()
        item = self.build(body=body)
        self.assertEqual(base64.b64decode(item['body_b64']), body)

    def test_the_hash_and_the_size_describe_the_raw_bytes(self):
        body = v4_body(4096)
        item = self.build(body=body)
        raw = base64.b64decode(item['body_b64'])
        self.assertEqual(item['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(item['size_bytes'], len(raw))
        self.assertEqual(item['size_bytes'], len(body))

    def test_the_hash_is_not_taken_over_the_base64_text(self):
        """NEGATIVE CONTROL: the receiver re-hashes the DECODED bytes.

        Hashing the base64 would agree with itself forever and be refused by
        the endpoint on the first live batch.
        """
        item = self.build(body=v4_body(64))
        base64_digest = hashlib.sha256(
            item['body_b64'].encode('ascii')).hexdigest()
        self.assertNotEqual(item['sha256'], base64_digest)

    def test_the_request_context_path_carries_no_query(self):
        item = source_item(FLIGHT_ID, SOURCE_V4, v4_body(), CAPTURED_AT,
                           url_path(V4_URL), 'url_path', None, RUN_ID)
        path = item['request_context']['path']
        self.assertEqual(path, V4_PATH_ONLY)
        self.assertNotIn('?', path)
        self.assertNotIn('Signature', path)

    def test_the_contract_fields_are_all_present(self):
        self.assertEqual(sorted(self.build()),
                         ['api_status', 'body_b64', 'capture_run_id',
                          'captured_at_utc', 'flight_id', 'parser_version',
                          'request_context', 'schema_version', 'sha256',
                          'size_bytes', 'source_type'])

    def test_the_mode_version_travels_in_the_context(self):
        self.assertEqual(self.build()['request_context']['mode_version'],
                         SOURCES_MODE_VERSION)

    def test_items_of_a_visit_carry_the_right_schema_and_parser_versions(self):
        capture = SourceCapture(logger=_QuietLog())
        page = _FakePage(script=full_visit_script())
        capture.attach(page)
        run = SourceRun(page, capture, source_config(), logger=_QuietLog(),
                        clock=_Clock())
        flight = run.capture_flight(FLIGHT_ID)

        by_type = {item['source_type']: item
                   for item in source_items(flight, RUN_ID)}
        self.assertEqual(by_type[SOURCE_CARD]['schema_version'],
                         SCHEMA_RAW_HTTP_BODY)
        self.assertEqual(by_type[SOURCE_AIRLINES]['schema_version'],
                         SCHEMA_AIRLINES_PATHS_ONLY)
        self.assertIsNotNone(by_type[SOURCE_ROUTE]['parser_version'])
        self.assertIsNotNone(by_type[SOURCE_V4]['parser_version'])
        for item in by_type.values():
            self.assertNotIn('?', item['request_context']['path'])

    def test_the_capture_run_id_carries_no_secret_and_names_the_period(self):
        run_id = capture_run_id('2026-08-01', '2026-08-31')
        self.assertTrue(run_id.startswith('sources:2026-08-01_2026-08-31:'))
        self.assertEqual(find_secret_markers(run_id), [])
        self.assertTrue(capture_run_id().startswith('sources:ids-file:'))


# ─── 5. The visit, end to end, with fakes ────────────────────────────────────

class CaptureLifecycleTests(unittest.TestCase):

    def run_visit(self, script=None, cfg=None, goto_error=None,
                  landing_url=None, capture=None, page=None):
        self.log = _QuietLog()
        capture = capture or SourceCapture(logger=self.log)
        page = page or _FakePage(script=script, goto_error=goto_error,
                                 landing_url=landing_url
                                 or 'https://www.djiag.invalid/record/1')
        capture.attach(page)
        run = SourceRun(page, capture, cfg or source_config(),
                        logger=self.log, clock=_Clock())
        self.capture = capture
        self.page = page
        self.run = run
        return run.capture_flight(FLIGHT_ID)

    def test_a_full_visit_captures_all_four_and_is_complete(self):
        flight = self.run_visit(script=full_visit_script())
        self.assertEqual(sorted(flight.items),
                         [SOURCE_AIRLINES, SOURCE_CARD, SOURCE_ROUTE,
                          SOURCE_V4])
        self.assertTrue(flight.complete)
        self.assertTrue(flight.settled)
        self.assertEqual(flight.status, STATUS_V4)
        self.assertTrue(flight.v4_url_present)

    def test_the_record_page_of_the_flight_is_the_one_opened(self):
        self.run_visit(script=full_visit_script())
        self.assertEqual(self.page.goto_calls,
                         ['https://www.djiag.invalid/record/%d' % FLIGHT_ID])

    def test_the_v4_body_is_handed_back_to_the_page_unchanged(self):
        """The page must receive exactly what the storage sent."""
        route = _FakeRoute(url='%s%s?%s' % (STORAGE_HOST, V4_PATH_ONLY,
                                            V4_QUERY),
                           body=v4_body(256))
        self.run_visit(script=full_visit_script(v4=route))
        self.assertEqual(route.fetched, 1)
        self.assertEqual(len(route.fulfilled), 1)
        self.assertEqual(route.fulfilled[0][1], route._response.body())
        self.assertEqual(route.continued, 0)

    def test_no_v4_at_dji_is_a_complete_capture_and_settles_the_wait(self):
        script = [step_finished(card_request()),
                  step_finished(route_request()),
                  step_finished(airlines_request(v4_url=None))]
        flight = self.run_visit(script=script)
        self.assertEqual(flight.status, STATUS_NO_V4_URL)
        self.assertFalse(flight.v4_url_present)
        self.assertTrue(flight.settled)
        self.assertTrue(flight.complete,
                        'NO_V4_URL must count as a complete capture')

    def test_a_wait_that_never_settles_ends_in_no_v4_and_is_incomplete(self):
        script = [step_finished(card_request()),
                  step_finished(route_request()),
                  step_finished(airlines_request())]
        flight = self.run_visit(script=script,
                                cfg=source_config(source_wait_ms=2000))
        self.assertEqual(flight.status, STATUS_NO_V4)
        self.assertFalse(flight.settled)
        self.assertFalse(flight.complete)
        self.assertIn('without a V4 body', self.log.text())

    def test_the_wait_really_ends_rather_than_hanging(self):
        """NEGATIVE CONTROL to the case above: the deadline is honoured."""
        flight = self.run_visit(cfg=source_config(source_wait_ms=1000))
        self.assertEqual(flight.status, STATUS_NO_V4)
        self.assertEqual(flight.items, {})
        self.assertLessEqual(len(self.page.waits), 8)

    def test_a_page_that_does_not_open_is_a_page_error(self):
        flight = self.run_visit(goto_error=RuntimeError('TimeoutError'))
        self.assertEqual(flight.status, STATUS_PAGE_ERROR)
        self.assertEqual(flight.error_type, 'RuntimeError')
        self.assertFalse(flight.complete)
        self.assertEqual(self.page.waits, [],
                         'a page that did not open must not be waited on')

    def test_three_page_errors_in_a_row_declare_the_browser_dead(self):
        self.run_visit(goto_error=RuntimeError('TimeoutError'))
        for _ in range(MAX_CONSECUTIVE_PAGE_ERRORS - 2):
            self.run.capture_flight(FLIGHT_ID + 1)
            self.assertFalse(self.run.browser_looks_dead)
        self.run.capture_flight(FLIGHT_ID + 2)
        self.assertTrue(self.run.browser_looks_dead)

    def test_two_page_errors_do_not_declare_the_browser_dead(self):
        """NEGATIVE CONTROL: the threshold is a threshold, not a trigger."""
        self.run_visit(goto_error=RuntimeError('TimeoutError'))
        self.run.capture_flight(FLIGHT_ID + 1)
        self.assertEqual(MAX_CONSECUTIVE_PAGE_ERRORS, 3)
        self.assertFalse(self.run.browser_looks_dead)

    def test_one_page_that_opens_resets_the_run_of_errors(self):
        self.run_visit(goto_error=RuntimeError('TimeoutError'))
        self.run.capture_flight(FLIGHT_ID + 1)
        self.page.goto_error = None
        self.run.capture_flight(FLIGHT_ID + 2)
        self.assertFalse(self.run.browser_looks_dead)
        self.run.capture_flight(FLIGHT_ID + 3)
        self.assertFalse(self.run.browser_looks_dead)

    def test_landing_on_the_login_page_raises_session_expired(self):
        from drone_collector.browser import SessionExpired
        with self.assertRaises(SessionExpired):
            self.run_visit(landing_url='https://www.djiag.invalid/login')

    def test_a_failed_v4_fetch_is_counted_and_the_request_continues(self):
        route = _FakeRoute(url='%s%s' % (STORAGE_HOST, V4_PATH_ONLY),
                           fetch_error=RuntimeError('TargetClosedError'))
        script = [step_finished(card_request()),
                  step_finished(route_request()),
                  step_finished(airlines_request()),
                  step_route(route)]
        flight = self.run_visit(script=script)
        self.assertEqual(flight.status, STATUS_V4_FAILED)
        self.assertTrue(flight.v4_failed)
        self.assertEqual(route.continued, 1)
        self.assertEqual(self.capture.counts()['v4_handler_errors'], 1)
        self.assertFalse(flight.complete)

    def test_a_v4_answered_with_403_is_not_stored(self):
        route = _FakeRoute(url='%s%s' % (STORAGE_HOST, V4_PATH_ONLY),
                           status=403, body=b'AccessDenied')
        flight = self.run_visit(script=full_visit_script(v4=route))
        self.assertFalse(flight.has(SOURCE_V4))
        self.assertEqual(flight.status, STATUS_V4_FAILED)
        self.assertEqual(self.capture.counts()['rejected'].get('v4:http-403'),
                         1)

    def test_an_oversized_body_is_counted_and_not_stored(self):
        capture = SourceCapture(logger=_QuietLog(), max_body_bytes=64)
        flight = self.run_visit(script=[step_finished(card_request())],
                                capture=capture,
                                cfg=source_config(source_wait_ms=1000))
        self.assertFalse(flight.has(SOURCE_CARD))
        self.assertEqual(capture.counts()['oversized'], 1)
        self.assertEqual(capture.counts()['rejected'].get('card:oversized'), 1)

    def test_a_body_under_the_cap_is_stored(self):
        """NEGATIVE CONTROL: the cap does not reject everything."""
        capture = SourceCapture(logger=_QuietLog(), max_body_bytes=64 * 1024)
        flight = self.run_visit(script=[step_finished(card_request())],
                                capture=capture,
                                cfg=source_config(source_wait_ms=1000))
        self.assertTrue(flight.has(SOURCE_CARD))
        self.assertEqual(capture.counts()['oversized'], 0)

    def test_an_oversized_v4_is_counted_and_decides_the_v4_question(self):
        capture = SourceCapture(logger=_QuietLog(), max_body_bytes=64)
        route = _FakeRoute(url='%s%s' % (STORAGE_HOST, V4_PATH_ONLY),
                           body=v4_body(4096))
        self.run_visit(script=[step_route(route)], capture=capture,
                       cfg=source_config(source_wait_ms=1000))
        self.assertEqual(capture.counts()['oversized'], 1)
        self.assertTrue(capture.flight(FLIGHT_ID).v4_failed)

    def test_a_listener_that_raises_never_reaches_playwright(self):
        """[REASON]: an exception inside a handler aborts the navigation."""
        capture = SourceCapture(logger=_QuietLog())
        page = _FakePage()
        capture.attach(page)
        capture.begin_flight(FLIGHT_ID)
        broken = card_request(response_error=RuntimeError('boom'))
        try:
            page.deliver_finished(broken)
        except Exception as exc:                       # pragma: no cover
            self.fail('the listener let %r escape' % (exc,))
        self.assertEqual(capture.counts()['listener_errors'], 1)
        self.assertFalse(capture.flight(FLIGHT_ID).has(SOURCE_CARD))

    def test_a_body_call_that_raises_is_swallowed_too(self):
        capture = SourceCapture(logger=_QuietLog())
        page = _FakePage()
        capture.attach(page)
        capture.begin_flight(FLIGHT_ID)
        page.deliver_finished(card_request(body_error=RuntimeError('evicted')))
        self.assertEqual(capture.counts()['listener_errors'], 1)

    def test_a_healthy_listener_counts_no_error(self):
        """NEGATIVE CONTROL: the counter is not incremented unconditionally."""
        capture = SourceCapture(logger=_QuietLog())
        page = _FakePage()
        capture.attach(page)
        capture.begin_flight(FLIGHT_ID)
        page.deliver_finished(card_request())
        self.assertEqual(capture.counts()['listener_errors'], 0)

    def test_a_request_that_never_answered_is_counted_not_stored(self):
        capture = SourceCapture(logger=_QuietLog())
        page = _FakePage()
        capture.attach(page)
        capture.begin_flight(FLIGHT_ID)
        page.deliver_finished(card_request(no_response=True))
        self.assertEqual(capture.counts()['rejected'].get('card:no-response'),
                         1)

    def test_a_failed_request_of_a_known_kind_is_counted(self):
        capture = SourceCapture(logger=_QuietLog())
        page = _FakePage()
        capture.attach(page)
        page.deliver_failed(card_request())
        self.assertEqual(capture.counts()['requests_failed'], 1)

    def test_a_failed_request_of_an_unknown_kind_is_not_counted(self):
        """NEGATIVE CONTROL: the counter means what it says."""
        capture = SourceCapture(logger=_QuietLog())
        page = _FakePage()
        capture.attach(page)
        page.deliver_failed(_FakeRequest('https://fonts.invalid/x.woff2'))
        self.assertEqual(capture.counts()['requests_failed'], 0)

    def test_a_card_with_a_rejection_code_is_not_the_flights_card(self):
        """[REASON]: the API answers HTTP 200 for its rejections as well."""
        capture = SourceCapture(logger=_QuietLog())
        page = _FakePage()
        capture.attach(page)
        capture.begin_flight(FLIGHT_ID)
        page.deliver_finished(card_request(code=101))
        self.assertFalse(capture.flight(FLIGHT_ID).has(SOURCE_CARD))
        self.assertEqual(capture.counts()['rejected'].get('card:code-101'), 1)

    def test_a_card_with_code_zero_is_stored(self):
        """NEGATIVE CONTROL to the rejection above."""
        capture = SourceCapture(logger=_QuietLog())
        page = _FakePage()
        capture.attach(page)
        capture.begin_flight(FLIGHT_ID)
        page.deliver_finished(card_request(code=0))
        self.assertTrue(capture.flight(FLIGHT_ID).has(SOURCE_CARD))

    def test_the_same_source_fetched_twice_keeps_the_first_answer(self):
        capture = SourceCapture(logger=_QuietLog())
        page = _FakePage()
        capture.attach(page)
        capture.begin_flight(FLIGHT_ID)
        page.deliver_finished(card_request())
        first = capture.flight(FLIGHT_ID).items[SOURCE_CARD].body
        page.deliver_finished(card_request(code=0))
        self.assertIs(capture.flight(FLIGHT_ID).items[SOURCE_CARD].body, first)

    def test_release_drops_the_bodies_but_keeps_the_verdicts(self):
        flight = self.run_visit(script=full_visit_script())
        flight.release()
        self.assertEqual(flight.items[SOURCE_V4].body, b'')
        self.assertEqual(flight.status, STATUS_V4)
        self.assertTrue(flight.complete)

    def test_the_pause_between_flights_is_the_configured_one(self):
        self.run_visit(script=full_visit_script(),
                       cfg=source_config(source_pause_ms=1500))
        before = len(self.page.waits)
        self.run.pause()
        self.assertEqual(self.page.waits[before:], [1500])

    def test_a_zero_pause_makes_no_wait_at_all(self):
        """NEGATIVE CONTROL: the pause is not unconditional."""
        self.run_visit(script=full_visit_script(),
                       cfg=source_config(source_pause_ms=0))
        before = len(self.page.waits)
        self.run.pause()
        self.assertEqual(self.page.waits[before:], [])

    def test_describe_names_the_outcome_without_a_body(self):
        flight = self.run_visit(script=full_visit_script())
        described = json.dumps(flight.describe(), ensure_ascii=False)
        self.assertIn(STATUS_V4, described)
        self.assertEqual(find_secret_markers(described), [])


# ─── 6. Resumability ─────────────────────────────────────────────────────────

class OutboxTestCase(unittest.TestCase):
    """A queue in its own temporary directory."""

    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.root = Path(self._directory.name) / 'outbox'
        self.outbox = Outbox(self.root).prepare()
        self.log = _QuietLog()

    def visited(self, flight_id=FLIGHT_ID, v4=True, v4_url=V4_URL,
                embedded_id=None):
        """A FlightSources as one visit would leave it, without a browser."""
        capture = SourceCapture(logger=_QuietLog())
        page = _FakePage()
        capture.attach(page)
        capture.begin_flight(flight_id)
        page.deliver_finished(card_request(flight_id))
        page.deliver_finished(route_request(flight_id,
                                            embedded_id=embedded_id))
        page.deliver_finished(airlines_request(flight_id, v4_url=v4_url))
        if v4:
            page.deliver_route(_FakeRoute(
                url='%s/objects/airline_v4/%d/airline_v4_NOT_REAL.pb?%s'
                    % (STORAGE_HOST, flight_id, V4_QUERY)))
        flight = capture.flight(flight_id)
        flight.status = flight.outcome()
        return flight

    def queue(self, flight, exclude=None):
        items = source_items(flight, RUN_ID, exclude=exclude)
        return enqueue_sources(self.outbox, items, flight=flight,
                               logger=self.log), items


class ResumeTests(OutboxTestCase):

    def test_known_sources_reads_the_names_and_finds_every_type(self):
        self.queue(self.visited())
        known = known_sources(self.outbox)
        self.assertEqual(sorted(known[FLIGHT_ID]),
                         [SOURCE_AIRLINES, SOURCE_CARD, SOURCE_ROUTE,
                          SOURCE_V4])

    def test_an_empty_queue_knows_nothing(self):
        self.assertEqual(known_sources(self.outbox), {})

    def test_a_flight_with_everything_is_not_visited_again(self):
        self.queue(self.visited())
        known = known_sources(self.outbox)
        self.assertTrue(flight_already_captured(FLIGHT_ID, known,
                                                self.outbox))

    def test_a_flight_missing_only_the_v4_is_visited_again(self):
        """NEGATIVE CONTROL: the skip is not 'three of four is enough'."""
        self.queue(self.visited(v4=False))
        known = known_sources(self.outbox)
        self.assertEqual(sorted(known[FLIGHT_ID]),
                         [SOURCE_AIRLINES, SOURCE_CARD, SOURCE_ROUTE])
        self.assertFalse(flight_already_captured(FLIGHT_ID, known,
                                                 self.outbox))

    def test_a_flight_dji_holds_no_v4_for_is_not_visited_again(self):
        self.queue(self.visited(v4=False, v4_url=None))
        known = known_sources(self.outbox)
        self.assertNotIn(SOURCE_V4, known[FLIGHT_ID])
        self.assertTrue(flight_already_captured(FLIGHT_ID, known,
                                                self.outbox),
                        'NO_V4_URL must count as a complete capture')

    def test_a_flight_with_only_a_card_is_visited_again(self):
        flight = self.visited()
        self.queue(flight, exclude={SOURCE_ROUTE, SOURCE_AIRLINES, SOURCE_V4})
        known = known_sources(self.outbox)
        self.assertFalse(flight_already_captured(FLIGHT_ID, known,
                                                 self.outbox))

    def test_a_flight_nobody_queued_is_visited(self):
        self.queue(self.visited())
        known = known_sources(self.outbox)
        self.assertFalse(flight_already_captured(OTHER_FLIGHT_ID, known,
                                                 self.outbox))

    def test_what_was_already_sent_still_counts_as_known(self):
        """[REASON]: `sent/` is not 'gone'; re-asking DJI for it is waste."""
        self.queue(self.visited())
        for path in self.outbox.pending():
            self.outbox.mark_sent(path)
        self.assertEqual(self.outbox.pending(), [])
        known = known_sources(self.outbox)
        self.assertTrue(flight_already_captured(FLIGHT_ID, known,
                                                self.outbox))

    def test_a_foreign_kind_in_the_queue_is_not_read_as_a_source(self):
        from drone_collector.outbox import KIND_ROUTE
        self.outbox.enqueue(KIND_ROUTE, '%d' % FLIGHT_ID, {'points': 3},
                            'b' * 64)
        self.assertEqual(known_sources(self.outbox), {})

    def test_source_items_omits_what_is_already_queued(self):
        flight = self.visited()
        items = source_items(flight, RUN_ID,
                             exclude={SOURCE_CARD, SOURCE_AIRLINES})
        self.assertEqual(sorted(item['source_type'] for item in items),
                         [SOURCE_ROUTE, SOURCE_V4])

    def test_source_items_without_an_exclusion_returns_everything(self):
        """NEGATIVE CONTROL to the exclusion above."""
        self.assertEqual(len(source_items(self.visited(), RUN_ID)), 4)

    def test_the_items_come_out_in_a_stable_order(self):
        first = [item['source_type']
                 for item in source_items(self.visited(), RUN_ID)]
        second = [item['source_type']
                  for item in source_items(self.visited(), RUN_ID)]
        self.assertEqual(first, second)


# ─── 7. What the queue accepts and what it refuses ───────────────────────────

class EnqueueSourcesTests(OutboxTestCase):

    def test_a_clean_visit_queues_four_envelopes(self):
        result, items = self.queue(self.visited())
        self.assertEqual(result.queued, 4)
        self.assertEqual(result.duplicates, 0)
        self.assertEqual(result.secret_refused, 0)
        self.assertEqual(result.too_large, 0)
        self.assertEqual(len(self.outbox.pending()), 4)
        self.assertEqual(len(items), 4)

    def test_the_same_visit_queued_twice_adds_nothing(self):
        """The dedupe key of (flight, source_type, bytes) is stable."""
        self.queue(self.visited())
        result, _items = self.queue(self.visited())
        self.assertEqual(result.queued, 0)
        self.assertEqual(result.duplicates, 4)
        self.assertEqual(len(self.outbox.pending()), 4)

    def test_a_different_body_of_the_same_type_is_a_new_revision(self):
        """NEGATIVE CONTROL: dedupe is by BYTES, not by (flight, type)."""
        self.queue(self.visited())
        other = self.visited()
        other.items[SOURCE_CARD].body = card_body() + b' '
        result, _items = self.queue(other)
        self.assertEqual(result.queued, 1)
        self.assertEqual(len(self.outbox.pending()), 5)

    def test_the_airlines_verdict_is_written_into_the_diagnostics(self):
        self.queue(self.visited(v4=False, v4_url=None))
        airlines = [path for path in self.outbox.pending()
                    if SOURCE_AIRLINES in path.name][0]
        envelope = self.outbox.read(airlines)
        self.assertIs(envelope['diagnostics']['v4_url_present'], False)

    def test_a_body_carrying_a_signed_link_is_refused_and_nothing_is_written(self):
        flight = self.visited()
        flight.items[SOURCE_CARD].body = json.dumps(
            {'code': 0, 'leak': V4_URL}).encode('utf-8')
        result, _items = self.queue(flight)
        self.assertEqual(result.secret_refused, 1)
        self.assertEqual(result.queued, 3)
        self.assertEqual(
            [path for path in self.outbox.pending()
             if '_%s_' % SOURCE_CARD in path.name], [])
        self.assertIn('NOT queued', self.log.text())

    def test_the_refusal_message_never_echoes_the_secret(self):
        flight = self.visited()
        flight.items[SOURCE_CARD].body = json.dumps(
            {'code': 0, 'leak': V4_URL}).encode('utf-8')
        self.queue(flight)
        self.assertNotIn('NOT-REAL-SIGNATURE-V4', self.log.text())

    def test_a_clean_body_is_not_refused(self):
        """NEGATIVE CONTROL: the detector does not refuse everything."""
        result, _items = self.queue(self.visited())
        self.assertEqual(result.secret_refused, 0)

    def test_an_oversized_envelope_is_counted_and_not_written(self):
        from drone_collector.outbox import MAX_ENVELOPE_BYTES
        flight = self.visited()
        flight.items[SOURCE_V4].body = b'A' * (MAX_ENVELOPE_BYTES + 1024)
        result, _items = self.queue(flight)
        self.assertEqual(result.too_large, 1)
        self.assertEqual(result.queued, 3)
        self.assertEqual(self.outbox.stale_temp_files(), [])
        self.assertEqual(
            [path for path in self.outbox.pending()
             if '_%s_' % SOURCE_V4 in path.name], [])

    def test_the_enqueued_set_records_what_actually_went_in(self):
        flight = self.visited()
        self.queue(flight)
        self.assertEqual(sorted(flight.enqueued),
                         [SOURCE_AIRLINES, SOURCE_CARD, SOURCE_ROUTE,
                          SOURCE_V4])


class SourceDryRunTests(OutboxTestCase):

    def test_a_dry_run_writes_the_items_and_queues_nothing(self):
        flight = self.visited()
        items = source_items(flight, RUN_ID)
        target = write_sources_dry_run(items, [flight], self.root, RUN_ID)
        document = json.loads(target.read_text(encoding='utf-8'))
        self.assertTrue(document['dry_run'])
        self.assertTrue(document['nothing_was_queued'])
        self.assertEqual(document['count'], 4)
        self.assertEqual(self.outbox.pending(), [])

    def test_a_dry_run_carrying_a_secret_in_a_body_writes_nothing_at_all(self):
        """The base64 of a body must not hide a signed link from the check.

        [REASON]: this was a live defect. `find_secret_markers` reads the
        report as TEXT, and a signed link inside `body_b64` is invisible to
        it, so the dry run wrote to disk exactly the body `enqueue_sources`
        refuses to queue -- and a dry run is what an operator reaches for
        when something already looks wrong.
        """
        flight = self.visited()
        flight.items[SOURCE_CARD].body = json.dumps(
            {'code': 0, 'leak': V4_URL}).encode('utf-8')
        items = source_items(flight, RUN_ID)
        self.assertEqual(
            find_secret_markers(json.dumps(items, ensure_ascii=False)), [],
            'the fixture no longer hides the link in base64; the test would '
            'pass on the broken code too')
        with self.assertRaises(SecretInEnvelope):
            write_sources_dry_run(items, [flight], self.root / 'dry', RUN_ID)
        self.assertFalse((self.root / 'dry').exists())

    def test_a_dry_run_carrying_a_secret_in_plain_text_writes_nothing(self):
        flight = self.visited()
        flight.items[SOURCE_CARD].path = '/api/x?Signature=NOT-REAL-SIGNATURE'
        items = source_items(flight, RUN_ID)
        with self.assertRaises(SecretInEnvelope):
            write_sources_dry_run(items, [flight], self.root / 'dry2', RUN_ID)
        self.assertFalse((self.root / 'dry2').exists())

    def test_a_clean_dry_run_is_written(self):
        """NEGATIVE CONTROL: the guard does not refuse every report."""
        flight = self.visited()
        target = write_sources_dry_run(source_items(flight, RUN_ID), [flight],
                                       self.root / 'dry3', RUN_ID)
        self.assertTrue(target.exists())

    def test_the_dry_run_refusal_never_echoes_the_secret(self):
        flight = self.visited()
        flight.items[SOURCE_CARD].body = json.dumps(
            {'code': 0, 'leak': V4_URL}).encode('utf-8')
        with self.assertRaises(SecretInEnvelope) as caught:
            write_sources_dry_run(source_items(flight, RUN_ID), [flight],
                                  self.root / 'dry4', RUN_ID)
        self.assertNotIn('NOT-REAL-SIGNATURE-V4', str(caught.exception))


# ─── 8. Draining the queue ───────────────────────────────────────────────────

def source_counters(seen, new=None, duplicates=0, errors=0, refreshed=0,
                    status='ok'):
    return SourceSendResult().add({
        'status': status, 'seen': seen, 'new': seen if new is None else new,
        'duplicates': duplicates, 'errors': errors, 'refreshed': refreshed})


class SourceRefusalReasonTests(unittest.TestCase):
    """Full acceptance is four conditions at once; none is implied by 200."""

    def test_a_clean_answer_has_no_reasons(self):
        self.assertEqual(source_refusal_reasons(source_counters(4), 4), [])

    def test_no_counters_at_all_is_a_refusal(self):
        self.assertEqual(len(source_refusal_reasons(None, 4)), 1)

    def test_a_status_that_is_not_ok_is_a_refusal(self):
        reasons = source_refusal_reasons(source_counters(4, status='error'), 4)
        self.assertTrue(any('status' in reason for reason in reasons))

    def test_a_rejected_source_is_a_refusal(self):
        reasons = source_refusal_reasons(
            source_counters(4, new=3, errors=1), 4)
        self.assertTrue(any('rejected' in reason for reason in reasons))

    def test_a_seen_below_the_number_sent_is_a_refusal(self):
        reasons = source_refusal_reasons(source_counters(3), 4)
        self.assertTrue(any('saw 3' in reason for reason in reasons))

    def test_counters_that_do_not_add_up_are_a_refusal(self):
        reasons = source_refusal_reasons(source_counters(4, new=1), 4)
        self.assertTrue(any('add up' in reason for reason in reasons))


class DrainSourceOutboxTests(OutboxTestCase):

    def setUp(self):
        super(DrainSourceOutboxTests, self).setUp()
        self.queue(self.visited())
        self.cfg = source_config(outbox_dir=self.root)
        self.sent_bodies = []

    def accepting(self):
        def send(bodies, cfg, logger=None):
            self.sent_bodies.append(list(bodies))
            return source_counters(len(bodies))
        return send

    def refusing(self, errors=1):
        def send(bodies, cfg, logger=None):
            self.sent_bodies.append(list(bodies))
            return source_counters(len(bodies), new=len(bodies) - errors,
                                   errors=errors)
        return send

    def test_a_fully_accepted_batch_moves_every_envelope_to_sent(self):
        result = drain_source_outbox(self.outbox, self.cfg, self.log,
                                     send_fn=self.accepting())
        self.assertTrue(result.accepted)
        self.assertEqual(result.envelopes, 4)
        self.assertEqual(result.sent, 4)
        self.assertEqual(result.left_pending, 0)
        self.assertEqual(self.outbox.pending(), [])
        self.assertEqual(len(self.outbox.sent()), 4)

    def test_the_items_reach_the_sender_verbatim(self):
        drain_source_outbox(self.outbox, self.cfg, self.log,
                            send_fn=self.accepting())
        sent = {item['source_type']: item for item in self.sent_bodies[0]}
        self.assertEqual(sorted(sent),
                         [SOURCE_AIRLINES, SOURCE_CARD, SOURCE_ROUTE,
                          SOURCE_V4])
        for item in sent.values():
            raw = base64.b64decode(item['body_b64'])
            self.assertEqual(item['sha256'], hashlib.sha256(raw).hexdigest())
            self.assertEqual(item['size_bytes'], len(raw))

    def test_a_partly_accepted_batch_leaves_everything_pending(self):
        """[REASON]: the receiver answers with counters, not with a list --
        which item landed cannot be known, so the whole chunk stays."""
        result = drain_source_outbox(self.outbox, self.cfg, self.log,
                                     send_fn=self.refusing())
        self.assertFalse(result.accepted)
        self.assertEqual(result.sent, 0)
        self.assertEqual(len(self.outbox.pending()), 4)
        self.assertEqual(self.outbox.sent(), [])
        self.assertTrue(result.refusal_reasons)
        self.assertIn('NOT fully accepted', self.log.text())

    def test_a_refused_chunk_stops_the_run_and_leaves_the_rest(self):
        self.queue(self.visited(OTHER_FLIGHT_ID))
        self.assertEqual(len(self.outbox.pending()), 8)
        cfg = source_config(outbox_dir=self.root, source_batch_size=4)
        answers = [source_counters(4), source_counters(4, new=3, errors=1)]

        def send(bodies, _cfg, logger=None):
            return answers.pop(0)

        result = drain_source_outbox(self.outbox, cfg, self.log, send_fn=send)
        self.assertFalse(result.accepted)
        self.assertEqual(result.sent, 4)
        self.assertEqual(result.batches, 2)
        self.assertEqual(len(self.outbox.pending()), 4)
        self.assertEqual(len(self.outbox.sent()), 4)

    def test_an_empty_queue_sends_nothing(self):
        for path in self.outbox.pending():
            self.outbox.mark_sent(path)
        calls = []
        result = drain_source_outbox(
            self.outbox, self.cfg, self.log,
            send_fn=lambda *a, **k: calls.append(a))
        self.assertEqual(calls, [])
        self.assertEqual(result.envelopes, 0)
        self.assertTrue(result.accepted)

    def test_a_corrupt_envelope_is_quarantined_and_the_rest_still_goes(self):
        victim = self.outbox.pending()[0]
        victim.write_text('{not json', encoding='utf-8')
        result = drain_source_outbox(self.outbox, self.cfg, self.log,
                                     send_fn=self.accepting())
        self.assertEqual(result.corrupt, 1)
        self.assertEqual(result.sent, 3)
        self.assertEqual(len(self.outbox.corrupt()), 1)

    def test_the_batch_size_is_clamped_to_the_endpoint_cap(self):
        cfg = source_config(outbox_dir=self.root, source_batch_size=5000)
        sizes = []

        def send(bodies, _cfg, logger=None):
            sizes.append(len(bodies))
            return source_counters(len(bodies))

        drain_source_outbox(self.outbox, cfg, self.log, send_fn=send)
        self.assertTrue(all(size <= MAX_SOURCE_BATCH_SIZE for size in sizes))


# ─── 9. The catalog snapshot ─────────────────────────────────────────────────

def land_node(uuid, md5, link=None, name='Поле'):
    return {
        'uuid': uuid,
        'name': name,
        'serialNumber': 'P0000000',
        'totalArea': 150.0,
        'geometry': {
            'type': 'polygon',
            'storage': {
                'contentMd5': md5,
                'signedURL': (link if link is not None else
                              '%s/objects/lands/%s.geojson?%s'
                              % (STORAGE_HOST, uuid, V4_QUERY)),
            },
        },
    }


def polygon_bytes(uuid):
    return json.dumps({'type': 'Polygon',
                       'id': uuid,
                       'coordinates': [[[64.63, 40.08], [64.64, 40.08],
                                        [64.64, 40.09], [64.63, 40.08]]]},
                      ensure_ascii=False).encode('utf-8')


class StripSignedUrlsTests(unittest.TestCase):

    def test_the_key_is_removed_at_any_depth(self):
        node = {'a': {'b': [{'c': {'signedURL': 'https://x.invalid/?Signature=NOT-REAL'}}]}}
        stripped = strip_signed_urls(node)
        self.assertEqual(stripped, {'a': {'b': [{'c': {}}]}})

    def test_the_input_is_not_mutated(self):
        node = land_node('u1', 'a' * 32)
        strip_signed_urls(node)
        self.assertIn('signedURL', node['geometry']['storage'])

    def test_the_identity_of_the_contour_survives(self):
        stripped = strip_signed_urls(land_node('u1', 'a' * 32))
        storage = stripped['geometry']['storage']
        self.assertEqual(stripped['uuid'], 'u1')
        self.assertEqual(storage['contentMd5'], 'a' * 32)
        self.assertNotIn('signedURL', storage)

    def test_a_renamed_key_of_the_same_name_is_removed_too(self):
        for key in ('signedurl', 'SignedUrl', 'SIGNEDURL'):
            with self.subTest(key):
                stripped = strip_signed_urls({key: 'x', 'uuid': 'u1'})
                self.assertEqual(stripped, {'uuid': 'u1'})

    def test_a_key_that_merely_starts_with_the_name_is_kept(self):
        """NEGATIVE CONTROL: the match is the whole key, not a prefix."""
        stripped = strip_signed_urls({'signedURLPath': 'objects/x',
                                      'uuid': 'u1'})
        self.assertEqual(sorted(stripped), ['signedURLPath', 'uuid'])

    def test_a_stripped_catalog_passes_the_queue_detector(self):
        nodes = [strip_signed_urls(land_node('u%d' % i, '%032x' % i))
                 for i in range(3)]
        self.assertEqual(
            find_secret_markers(json.dumps(nodes, ensure_ascii=False)), [])

    def test_the_unstripped_catalog_would_not(self):
        """NEGATIVE CONTROL: the detector can tell the two apart."""
        nodes = [land_node('u%d' % i, '%032x' % i) for i in range(3)]
        self.assertNotEqual(
            find_secret_markers(json.dumps(nodes, ensure_ascii=False)), [])


class SnapshotManifestTests(unittest.TestCase):

    def nodes(self, count=5):
        return [strip_signed_urls(land_node('u%d' % i, '%032x' % i))
                for i in range(count)]

    def test_the_manifest_is_stable_under_reordering(self):
        nodes = self.nodes()
        self.assertEqual(snapshot_manifest_sha256(nodes),
                         snapshot_manifest_sha256(list(reversed(nodes))))

    def test_the_manifest_changes_when_a_contour_changes(self):
        """NEGATIVE CONTROL: a hash equal for everything proves nothing."""
        nodes = self.nodes()
        other = self.nodes()
        other[2]['geometry']['storage']['contentMd5'] = 'f' * 32
        self.assertNotEqual(snapshot_manifest_sha256(nodes),
                            snapshot_manifest_sha256(other))

    def test_the_manifest_changes_when_a_contour_is_missing(self):
        nodes = self.nodes()
        self.assertNotEqual(snapshot_manifest_sha256(nodes),
                            snapshot_manifest_sha256(nodes[:-1]))

    def test_a_node_without_geometry_still_counts(self):
        nodes = self.nodes(2) + [{'uuid': 'u9'}]
        self.assertNotEqual(snapshot_manifest_sha256(nodes),
                            snapshot_manifest_sha256(nodes[:2]))


class SnapshotChunkTests(unittest.TestCase):

    def nodes(self, count=5):
        return [strip_signed_urls(land_node('u%d' % i, '%032x' % i))
                for i in range(count)]

    def bodies(self, nodes=None, geometries=None, max_nodes=2, max_bytes=None,
               complete=True):
        nodes = self.nodes() if nodes is None else nodes
        return snapshot_chunk_bodies(
            'lands:20260908T041200Z', CAPTURED_AT, nodes,
            geometries=geometries, expected_count=len(nodes),
            scope={'walk': 'lands'}, complete=complete, max_nodes=max_nodes,
            max_bytes=max_bytes if max_bytes is not None else 6 * 1024 * 1024)

    def test_the_nodes_are_split_by_count(self):
        bodies = self.bodies(max_nodes=2)
        self.assertEqual(len(bodies), 3)
        self.assertEqual([len(body['lands']) for body in bodies], [2, 2, 1])

    def test_no_node_is_lost_or_duplicated(self):
        bodies = self.bodies(max_nodes=2)
        uuids = [node['uuid'] for body in bodies for node in body['lands']]
        self.assertEqual(uuids, ['u0', 'u1', 'u2', 'u3', 'u4'])

    def test_the_nodes_are_split_by_the_byte_budget_too(self):
        nodes = self.nodes(6)
        one = len(json.dumps(nodes[0], ensure_ascii=False,
                             separators=(',', ':')).encode('utf-8'))
        bodies = self.bodies(nodes=nodes, max_nodes=1000,
                             max_bytes=one * 2 + 1)
        self.assertEqual(len(bodies), 3)
        self.assertEqual([len(body['lands']) for body in bodies], [2, 2, 2])

    def test_only_the_last_chunk_is_final(self):
        bodies = self.bodies(max_nodes=2)
        self.assertEqual([body['snapshot']['final'] for body in bodies],
                         [False, False, True])

    def test_complete_and_the_manifest_ride_only_on_the_last_chunk(self):
        bodies = self.bodies(max_nodes=2)
        for body in bodies[:-1]:
            self.assertIsNone(body['snapshot']['complete'])
            self.assertIsNone(body['snapshot']['manifest_sha256'])
        last = bodies[-1]['snapshot']
        self.assertIs(last['complete'], True)
        self.assertEqual(last['manifest_sha256'],
                         snapshot_manifest_sha256(self.nodes()))

    def test_an_incomplete_walk_says_so_on_the_last_chunk(self):
        """NEGATIVE CONTROL: `complete` is not hard-wired to True."""
        self.assertIs(self.bodies(complete=False)[-1]['snapshot']['complete'],
                      False)

    def test_the_chunk_numbering_is_one_based_and_names_the_total(self):
        bodies = self.bodies(max_nodes=2)
        self.assertEqual([body['snapshot']['chunk'] for body in bodies],
                         [1, 2, 3])
        self.assertEqual({body['snapshot']['chunks'] for body in bodies}, {3})

    def test_an_empty_catalog_still_produces_one_final_chunk(self):
        bodies = self.bodies(nodes=[])
        self.assertEqual(len(bodies), 1)
        self.assertTrue(bodies[0]['snapshot']['final'])
        self.assertEqual(bodies[0]['lands'], [])

    def test_the_node_cap_never_exceeds_the_endpoint_cap(self):
        bodies = self.bodies(nodes=self.nodes(3), max_nodes=100000)
        self.assertEqual(len(bodies), 1)
        for body in bodies:
            self.assertLessEqual(len(body['lands']),
                                 MAX_LAND_SNAPSHOT_BATCH_SIZE)

    def test_every_geometry_travels_in_exactly_one_chunk(self):
        nodes = self.nodes(4)
        geometries = {node['geometry']['storage']['contentMd5']:
                      base64.b64encode(polygon_bytes(node['uuid'])
                                       ).decode('ascii')
                      for node in nodes}
        bodies = self.bodies(nodes=nodes, geometries=geometries, max_nodes=2)
        placed = [geometry['content_md5'] for body in bodies
                  for geometry in body['geometries']]
        self.assertEqual(sorted(placed), sorted(geometries))
        self.assertEqual(len(placed), len(set(placed)))

    def test_two_nodes_sharing_a_polygon_carry_it_once(self):
        shared = 'c' * 32
        nodes = [strip_signed_urls(land_node('u1', shared)),
                 strip_signed_urls(land_node('u2', shared))]
        geometries = {shared: base64.b64encode(polygon_bytes('u1')
                                               ).decode('ascii')}
        bodies = self.bodies(nodes=nodes, geometries=geometries, max_nodes=1)
        placed = [geometry['content_md5'] for body in bodies
                  for geometry in body['geometries']]
        self.assertEqual(placed, [shared])

    def test_a_geometry_nobody_names_is_not_shipped(self):
        bodies = self.bodies(geometries={'d' * 32: 'AAAA'}, max_nodes=5)
        self.assertEqual([geometry for body in bodies
                          for geometry in body['geometries']], [])

    def test_the_snapshot_carries_the_run_id_and_the_scope(self):
        body = self.bodies(max_nodes=100)[0]
        self.assertEqual(body['snapshot']['capture_run_id'],
                         'lands:20260908T041200Z')
        self.assertEqual(body['snapshot']['captured_at_utc'], CAPTURED_AT)
        self.assertEqual(body['snapshot']['expected_count'], 5)
        self.assertEqual(body['snapshot']['scope'], {'walk': 'lands'})

    def test_the_run_id_carries_no_secret(self):
        self.assertTrue(snapshot_run_id().startswith('lands:'))
        self.assertEqual(find_secret_markers(snapshot_run_id()), [])


class SnapshotQueueTests(OutboxTestCase):

    def bodies(self, count=3):
        nodes = [strip_signed_urls(land_node('u%d' % i, '%032x' % i))
                 for i in range(count)]
        return snapshot_chunk_bodies('lands:20260908T041200Z', CAPTURED_AT,
                                     nodes, expected_count=count,
                                     complete=True, max_nodes=1)

    def test_the_chunks_round_trip_through_the_queue(self):
        result = enqueue_snapshot_chunks(self.outbox, self.bodies(),
                                         logger=self.log)
        self.assertEqual(result.queued, 3)
        self.assertEqual(len(self.outbox.records(KIND_LAND_SNAPSHOT)), 3)
        envelope = self.outbox.read(self.outbox.pending()[0])
        self.assertEqual(envelope['kind'], KIND_LAND_SNAPSHOT)
        self.assertIn('snapshot', envelope['body'])

    def test_the_same_snapshot_queued_twice_adds_nothing(self):
        bodies = self.bodies()
        enqueue_snapshot_chunks(self.outbox, bodies, logger=self.log)
        again = enqueue_snapshot_chunks(self.outbox, bodies, logger=self.log)
        self.assertEqual(again.queued, 0)
        self.assertEqual(again.duplicates, 3)

    def test_a_chunk_carrying_a_signed_link_is_refused(self):
        bodies = self.bodies(count=1)
        bodies[0]['lands'] = [land_node('u1', 'a' * 32)]
        result = enqueue_snapshot_chunks(self.outbox, bodies, logger=self.log)
        self.assertEqual(result.secret_refused, 1)
        self.assertEqual(result.queued, 0)
        self.assertEqual(self.outbox.pending(), [])

    def test_a_dry_run_of_the_snapshot_writes_and_queues_nothing(self):
        target = write_snapshot_dry_run(self.bodies(), self.root,
                                        'lands:20260908T041200Z')
        document = json.loads(target.read_text(encoding='utf-8'))
        self.assertTrue(document['nothing_was_queued'])
        self.assertEqual(len(document['chunks']), 3)
        self.assertEqual(self.outbox.pending(), [])

    def test_a_dry_run_carrying_a_secret_writes_nothing(self):
        bodies = self.bodies(count=1)
        bodies[0]['lands'] = [land_node('u1', 'a' * 32)]
        with self.assertRaises(SecretInEnvelope):
            write_snapshot_dry_run(bodies, self.root / 'dry', 'lands:x')
        self.assertFalse((self.root / 'dry').exists())


def snapshot_counters(lands_seen, lands_new=None, geometries_seen=0,
                      geometries_new=0, errors=0, geometries_errors=0,
                      status='ok'):
    return LandSnapshotSendResult().add({
        'status': status, 'snapshot_id': 7,
        'lands_seen': lands_seen,
        'lands_new': lands_seen if lands_new is None else lands_new,
        'lands_seen_before': 0, 'errors': errors,
        'geometries_seen': geometries_seen, 'geometries_new': geometries_new,
        'geometries_unchanged': geometries_seen - geometries_new
        - geometries_errors,
        'geometries_errors': geometries_errors})


class SnapshotRefusalReasonTests(unittest.TestCase):

    def test_a_clean_answer_has_no_reasons(self):
        self.assertEqual(snapshot_refusal_reasons(snapshot_counters(3), 3, 0),
                         [])

    def test_no_counters_at_all_is_a_refusal(self):
        self.assertEqual(len(snapshot_refusal_reasons(None, 3, 0)), 1)

    def test_a_rejected_land_is_a_refusal(self):
        reasons = snapshot_refusal_reasons(
            snapshot_counters(3, lands_new=2, errors=1), 3, 0)
        self.assertTrue(any('land(s) were rejected' in r for r in reasons))

    def test_a_rejected_geometry_is_a_refusal(self):
        reasons = snapshot_refusal_reasons(
            snapshot_counters(3, geometries_seen=2, geometries_new=1,
                              geometries_errors=1), 3, 2)
        self.assertTrue(any('geometry(ies) were rejected' in r
                            for r in reasons))

    def test_a_lands_seen_below_the_number_sent_is_a_refusal(self):
        reasons = snapshot_refusal_reasons(snapshot_counters(2), 3, 0)
        self.assertTrue(any('saw 2 land' in r for r in reasons))

    def test_a_geometry_the_endpoint_did_not_see_is_a_refusal(self):
        reasons = snapshot_refusal_reasons(snapshot_counters(3), 3, 1)
        self.assertTrue(any('geometry(ies) of the 1' in r for r in reasons))


class DrainSnapshotOutboxTests(OutboxTestCase):

    def setUp(self):
        super(DrainSnapshotOutboxTests, self).setUp()
        nodes = [strip_signed_urls(land_node('u%d' % i, '%032x' % i))
                 for i in range(5)]
        self.bodies = snapshot_chunk_bodies(
            'lands:20260908T041200Z', CAPTURED_AT, nodes, expected_count=5,
            complete=True, max_nodes=1)
        enqueue_snapshot_chunks(self.outbox, self.bodies, logger=self.log)
        self.cfg = source_config(outbox_dir=self.root)
        self.posted = []

    def accepting(self):
        def send(body, cfg, logger=None, index=1, total=1):
            self.posted.append(body)
            return snapshot_counters(len(body.get('lands') or []),
                                     geometries_seen=len(
                                         body.get('geometries') or []),
                                     geometries_new=len(
                                         body.get('geometries') or []))
        return send

    def test_the_chunks_are_posted_in_order_and_only_the_last_is_final(self):
        result = drain_land_snapshot_outbox(self.outbox, self.cfg, self.log,
                                            send_fn=self.accepting())
        self.assertTrue(result.accepted)
        self.assertEqual(result.sent, 5)
        self.assertEqual([body['snapshot']['chunk'] for body in self.posted],
                         [1, 2, 3, 4, 5])
        self.assertEqual([body['snapshot']['final'] for body in self.posted],
                         [False, False, False, False, True])
        self.assertEqual(self.outbox.pending(), [])

    def test_a_refused_chunk_stops_the_run_and_leaves_the_rest_pending(self):
        answers = [snapshot_counters(1), snapshot_counters(1),
                   snapshot_counters(1, lands_new=0, errors=1)]

        def send(body, cfg, logger=None, index=1, total=1):
            self.posted.append(body)
            return answers.pop(0)

        result = drain_land_snapshot_outbox(self.outbox, self.cfg, self.log,
                                            send_fn=send)
        self.assertFalse(result.accepted)
        self.assertEqual(result.sent, 2)
        self.assertEqual(len(self.posted), 3)
        self.assertEqual(len(self.outbox.pending()), 3)
        self.assertTrue(result.refusal_reasons)

    def test_an_older_snapshot_goes_before_a_newer_one(self):
        nodes = [strip_signed_urls(land_node('v0', 'f' * 32))]
        enqueue_snapshot_chunks(self.outbox, snapshot_chunk_bodies(
            'lands:20260909T041200Z', CAPTURED_AT, nodes, expected_count=1,
            complete=True, max_nodes=1), logger=self.log)
        drain_land_snapshot_outbox(self.outbox, self.cfg, self.log,
                                   send_fn=self.accepting())
        run_ids = [body['snapshot']['capture_run_id'] for body in self.posted]
        self.assertEqual(run_ids, ['lands:20260908T041200Z'] * 5
                         + ['lands:20260909T041200Z'])

    def test_an_empty_queue_posts_nothing(self):
        for path in self.outbox.pending():
            self.outbox.mark_sent(path)
        result = drain_land_snapshot_outbox(self.outbox, self.cfg, self.log,
                                            send_fn=self.accepting())
        self.assertEqual(self.posted, [])
        self.assertEqual(result.envelopes, 0)


# ─── 10. Downloading the polygons ────────────────────────────────────────────

class DownloadSnapshotGeometriesTests(unittest.TestCase):

    def setUp(self):
        self.log = _QuietLog()
        self.calls = []

    def downloader(self, table):
        def download(link):
            self.calls.append(link)
            answer = table.get(link)
            if isinstance(answer, Exception):
                raise answer
            return answer
        return download

    def node_for(self, uuid, body, link=None):
        link = link or '%s/objects/lands/%s.geojson?%s' % (STORAGE_HOST, uuid,
                                                           V4_QUERY)
        md5 = hashlib.md5(body).hexdigest()
        return land_node(uuid, md5, link=link), link, md5

    def run_download(self, nodes, table, **kwargs):
        return download_snapshot_geometries(
            nodes, self.downloader(table), logger=self.log,
            sleep_fn=lambda _s: None, **kwargs)

    def test_two_successful_downloads_are_both_kept(self):
        """REGRESSION GUARD.

        [REASON]: a stray `del` of a name used on the next turn of the loop
        made the SECOND successful download raise. One-download tests all
        passed. Two is the smallest number that can tell the two cases apart.
        """
        first, link1, md5_1 = self.node_for('u1', polygon_bytes('u1'))
        second, link2, md5_2 = self.node_for('u2', polygon_bytes('u2'))
        geometries, counters = self.run_download(
            [first, second], {link1: polygon_bytes('u1'),
                              link2: polygon_bytes('u2')})
        self.assertEqual(counters.downloaded, 2)
        self.assertEqual(sorted(geometries), sorted([md5_1, md5_2]))
        self.assertEqual(base64.b64decode(geometries[md5_1]),
                         polygon_bytes('u1'))
        self.assertEqual(counters.failed, 0)

    def test_three_successful_downloads_are_all_kept(self):
        nodes, table, md5s = [], {}, []
        for index in range(3):
            uuid = 'u%d' % index
            body = polygon_bytes(uuid)
            node, link, md5 = self.node_for(uuid, body)
            nodes.append(node)
            table[link] = body
            md5s.append(md5)
        geometries, counters = self.run_download(nodes, table)
        self.assertEqual(counters.downloaded, 3)
        self.assertEqual(sorted(geometries), sorted(md5s))

    def test_the_bytes_are_kept_verbatim(self):
        body = polygon_bytes('u1')
        node, link, md5 = self.node_for('u1', body)
        geometries, _counters = self.run_download([node], {link: body})
        self.assertEqual(base64.b64decode(geometries[md5]), body)

    def test_a_body_whose_md5_differs_is_not_kept(self):
        node = land_node('u1', 'a' * 32,
                         link='%s/objects/lands/u1.geojson?%s' % (STORAGE_HOST,
                                                                  V4_QUERY))
        link = node['geometry']['storage']['signedURL']
        geometries, counters = self.run_download([node],
                                                 {link: polygon_bytes('u1')})
        self.assertEqual(geometries, {})
        self.assertEqual(counters.md5_mismatch, 1)
        self.assertEqual(counters.downloaded, 0)

    def test_a_matching_md5_is_kept(self):
        """NEGATIVE CONTROL: the md5 guard does not reject everything."""
        node, link, md5 = self.node_for('u1', polygon_bytes('u1'))
        geometries, counters = self.run_download([node],
                                                 {link: polygon_bytes('u1')})
        self.assertEqual(list(geometries), [md5])
        self.assertEqual(counters.md5_mismatch, 0)

    def test_a_payload_carrying_a_secret_marker_is_not_kept(self):
        body = json.dumps({'type': 'Polygon',
                           'leak': V4_URL}).encode('utf-8')
        node = land_node('u1', hashlib.md5(body).hexdigest(),
                         link='%s/objects/lands/u1.geojson' % STORAGE_HOST)
        link = node['geometry']['storage']['signedURL']
        geometries, counters = self.run_download([node], {link: body})
        self.assertEqual(geometries, {})
        self.assertEqual(counters.secret_in_payload, 1)

    def test_the_signed_link_never_appears_in_the_result(self):
        node, link, _md5 = self.node_for('u1', polygon_bytes('u1'))
        geometries, _counters = self.run_download([node],
                                                  {link: polygon_bytes('u1')})
        rendered = json.dumps(geometries)
        self.assertNotIn('NOT-REAL-SIGNATURE-V4', rendered)
        self.assertEqual(find_secret_markers(rendered), [])

    def test_the_link_is_asked_for_exactly_once(self):
        node, link, _md5 = self.node_for('u1', polygon_bytes('u1'))
        self.run_download([node], {link: polygon_bytes('u1')})
        self.assertEqual(self.calls, [link])

    def test_two_nodes_sharing_one_md5_download_once(self):
        body = polygon_bytes('shared')
        md5 = hashlib.md5(body).hexdigest()
        first = land_node('u1', md5,
                          link='%s/objects/lands/u1.geojson' % STORAGE_HOST)
        second = land_node('u2', md5,
                           link='%s/objects/lands/u2.geojson' % STORAGE_HOST)
        table = {first['geometry']['storage']['signedURL']: body,
                 second['geometry']['storage']['signedURL']: body}
        geometries, counters = self.run_download([first, second], table)
        self.assertEqual(list(geometries), [md5])
        self.assertEqual(counters.downloaded, 1)
        self.assertEqual(len(self.calls), 1)

    def test_a_node_without_a_link_or_an_md5_is_skipped(self):
        node = land_node('u1', None, link=None)
        node['geometry']['storage']['signedURL'] = None
        geometries, counters = self.run_download([node], {})
        self.assertEqual(geometries, {})
        self.assertEqual(counters.no_geometry, 1)
        self.assertEqual(self.calls, [])

    def test_a_download_that_raises_is_counted_and_does_not_stop_the_rest(self):
        first, link1, _md5_1 = self.node_for('u1', polygon_bytes('u1'))
        second, link2, md5_2 = self.node_for('u2', polygon_bytes('u2'))
        geometries, counters = self.run_download(
            [first, second], {link1: RuntimeError('connection reset'),
                              link2: polygon_bytes('u2')})
        self.assertEqual(counters.failed, 1)
        self.assertEqual(counters.downloaded, 1)
        self.assertEqual(list(geometries), [md5_2])

    def test_an_empty_body_is_a_failure_not_a_polygon(self):
        node, link, _md5 = self.node_for('u1', polygon_bytes('u1'))
        geometries, counters = self.run_download([node], {link: b''})
        self.assertEqual(geometries, {})
        self.assertEqual(counters.failed, 1)

    def test_a_body_over_the_cap_is_not_kept(self):
        body = polygon_bytes('u1')
        node, link, _md5 = self.node_for('u1', body)
        geometries, counters = self.run_download([node], {link: body},
                                                 max_bytes=8)
        self.assertEqual(geometries, {})
        self.assertEqual(counters.too_large, 1)

    def test_the_selected_count_reports_every_contour_of_the_catalog(self):
        first, link1, _m1 = self.node_for('u1', polygon_bytes('u1'))
        second, link2, _m2 = self.node_for('u2', polygon_bytes('u2'))
        _geometries, counters = self.run_download(
            [first, second, {'no': 'uuid'}],
            {link1: polygon_bytes('u1'), link2: polygon_bytes('u2')})
        self.assertEqual(counters.selected, 2)
        self.assertEqual(counters.bytes,
                         len(polygon_bytes('u1')) + len(polygon_bytes('u2')))

    def test_the_pause_happens_between_downloads_and_not_after_the_last(self):
        waits = []
        first, link1, _m1 = self.node_for('u1', polygon_bytes('u1'))
        second, link2, _m2 = self.node_for('u2', polygon_bytes('u2'))
        download_snapshot_geometries(
            [first, second], self.downloader({link1: polygon_bytes('u1'),
                                              link2: polygon_bytes('u2')}),
            logger=self.log, sleep_fn=waits.append, pause_s=0.35)
        self.assertEqual(waits, [0.35])


# ─── 13. What the run summary is able to show ────────────────────────────────

class FlightSourcesStateTests(unittest.TestCase):
    """The three flags the exit code of `--sources` is decided by."""

    def test_a_flight_with_everything_is_complete(self):
        flight = FlightSources(FLIGHT_ID)
        for source_type in (SOURCE_CARD, SOURCE_ROUTE, SOURCE_AIRLINES,
                            SOURCE_V4):
            flight.items[source_type] = object()
        flight.v4_url_present = True
        self.assertTrue(flight.complete)
        self.assertEqual(flight.outcome(), STATUS_V4)

    def test_a_flight_dji_holds_no_v4_for_is_complete_too(self):
        flight = FlightSources(FLIGHT_ID)
        for source_type in (SOURCE_CARD, SOURCE_ROUTE, SOURCE_AIRLINES):
            flight.items[source_type] = object()
        flight.v4_url_present = False
        self.assertTrue(flight.complete)
        self.assertEqual(flight.outcome(), STATUS_NO_V4_URL)

    def test_a_timeout_is_not_complete(self):
        """NEGATIVE CONTROL: `complete` is not true of every visit."""
        flight = FlightSources(FLIGHT_ID)
        for source_type in (SOURCE_CARD, SOURCE_ROUTE, SOURCE_AIRLINES):
            flight.items[source_type] = object()
        flight.v4_url_present = True
        self.assertFalse(flight.complete)
        self.assertEqual(flight.outcome(), STATUS_NO_V4)

    def test_a_page_error_outranks_everything_else(self):
        flight = FlightSources(FLIGHT_ID)
        flight.status = STATUS_PAGE_ERROR
        self.assertEqual(flight.outcome(), STATUS_PAGE_ERROR)
        self.assertFalse(flight.complete)

    def test_a_failed_v4_settles_the_wait_but_is_not_complete(self):
        flight = FlightSources(FLIGHT_ID)
        for source_type in (SOURCE_CARD, SOURCE_ROUTE, SOURCE_AIRLINES):
            flight.items[source_type] = object()
        flight.v4_url_present = True
        flight.v4_failed = True
        self.assertTrue(flight.settled)
        self.assertFalse(flight.complete)
        self.assertEqual(flight.outcome(), STATUS_V4_FAILED)


if __name__ == '__main__':
    unittest.main()
