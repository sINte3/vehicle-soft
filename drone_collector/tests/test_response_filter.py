# -*- coding: utf-8 -*-
"""Response filtering (section 7.3 of the DRONE-003 task).

A list URL is accepted, a detail URL is rejected, a body with code 101 is
rejected, a body with code 0 and a data list is accepted.

Note the success code: **0**, not 200. The envelope is
{"status": 200, "code": 0, "message": "OK"} on success and status == code on
failure (101/101, 408/408). The first version of this module took the success
code to be 200, and the live run rejected every good page with "Rejected
flight-list response (code-0)". So 200 has a negative test of its own here --
it is a plausible-looking wrong answer, and a constant that merely looks right
is exactly what cost the first live run.

The rejection cases matter more than the acceptance ones: the server answers
HTTP 200 for every one of them, so a filter that trusts the HTTP status
accepts `code 101` -- an empty run that looks like a successful one.
"""

import unittest

from drone_collector.browser import (
    BODY_CODE_OK,
    CapturedPage,
    body_code,
    classify_response,
    is_expected_api_version,
    is_flight_list_body,
    is_flight_list_url,
    parse_page_size_from_url,
    parse_page_size_option,
)

from drone_collector.tests.support import (
    detail_url,
    list_url,
    load_fixture,
)


class UrlFilterTests(unittest.TestCase):

    def test_list_url_is_accepted(self):
        self.assertTrue(is_flight_list_url(list_url()))

    def test_list_url_with_readable_brackets_is_accepted(self):
        self.assertTrue(is_flight_list_url(list_url(encoded=False)))

    def test_detail_url_is_rejected(self):
        self.assertFalse(is_flight_list_url(detail_url(123)))

    def test_detail_url_with_a_query_string_is_still_rejected(self):
        self.assertFalse(is_flight_list_url(detail_url(123) + '?lang=en'))

    def test_overview_url_is_rejected(self):
        # Map mode fetches this one; it carries no flight list.
        self.assertFalse(is_flight_list_url(
            'https://www.djiag.com/api/web/v1/flight_records/overview?a=1'))

    def test_list_endpoint_without_a_query_string_is_rejected(self):
        # The marker includes the '?': no query string, no list request.
        self.assertFalse(is_flight_list_url(
            'https://www.djiag.com/api/web/v1/flight_records'))

    def test_another_endpoint_is_rejected(self):
        self.assertFalse(is_flight_list_url(
            'https://www.djiag.com/api/web/v1/devices?page=1'))

    def test_the_aggregate_endpoints_are_rejected(self):
        # The same page fetches these alongside the list, with the very same
        # filter parameters (observed on the live cabinet, 2026-07-31). They
        # carry totals, not flights, and must not be captured.
        for path in ('aggr', 'aggr_by_day'):
            url = ('https://www.djiag.com/api/web/v1/%s?'
                   'filters%%5Btimestamp_gteq%%5D=1767207600000' % path)
            self.assertFalse(is_flight_list_url(url), url)

    def test_a_different_api_version_is_still_captured_but_flagged(self):
        # Matching on the endpoint name rather than the API version: a version
        # bump must not turn every run into a silent "0 flights collected".
        url = list_url().replace('/api/web/v1/', '/api/web/v2/')
        self.assertTrue(is_flight_list_url(url))
        self.assertFalse(is_expected_api_version(url))

    def test_the_expected_version_is_recognised(self):
        self.assertTrue(is_expected_api_version(list_url()))
        self.assertFalse(is_expected_api_version(''))

    def test_empty_and_none_are_rejected(self):
        self.assertFalse(is_flight_list_url(''))
        self.assertFalse(is_flight_list_url(None))


class BodyFilterTests(unittest.TestCase):

    def test_code_0_with_a_data_list_is_accepted(self):
        body = load_fixture('flight_list_page1.json')
        self.assertEqual(body_code(body), 0)
        self.assertEqual(BODY_CODE_OK, 0)
        self.assertEqual(body.get('status'), 200)
        self.assertEqual(body.get('message'), 'OK')
        self.assertTrue(is_flight_list_body(body))

    def test_code_200_is_REJECTED(self):
        # The regression that cost the first live run. 200 is the envelope's
        # `status`, never its `code`; a body carrying code 200 is not a
        # success and must not be captured.
        body = {'status': 200, 'code': 200, 'message': 'OK',
                'data': [{'id': 1}], 'meta_data': {'current_page': 1,
                                                   'total_pages': 1}}
        self.assertFalse(is_flight_list_body(body))
        accepted, reason = classify_response(list_url(), body)
        self.assertFalse(accepted)
        self.assertEqual(reason, 'code-200')

    def test_code_101_is_rejected(self):
        body = load_fixture('flight_list_rejected_101.json')
        self.assertEqual(body_code(body), 101)
        self.assertFalse(is_flight_list_body(body))

    def test_code_408_is_rejected(self):
        self.assertFalse(is_flight_list_body(
            {'status': 200, 'code': 408, 'message': 'timeout', 'data': []}))

    def test_a_failure_carries_status_equal_to_code(self):
        # Observed shape of a rejection: status and code are the same value.
        # The HTTP status is 200 all the same, which is what fools a filter
        # written against response.status.
        body = load_fixture('flight_list_rejected_101.json')
        self.assertEqual(body.get('status'), body.get('code'))
        self.assertFalse(is_flight_list_body(body))

    def test_a_successful_detail_body_is_rejected_for_having_no_data_list(self):
        # A real detail response: code 0, but `data` is an object.
        body = load_fixture('flight_detail.json')
        self.assertEqual(body_code(body), BODY_CODE_OK)
        self.assertFalse(is_flight_list_body(body))

    def test_missing_and_malformed_bodies_are_rejected(self):
        for body in (None, [], 'text', {}, {'code': 'abc'}, {'code': True}):
            self.assertIsNone(body_code(body), body)
            self.assertFalse(is_flight_list_body(body), body)


class ClassifyResponseTests(unittest.TestCase):

    def test_accepted_page(self):
        accepted, reason = classify_response(
            list_url(), load_fixture('flight_list_page1.json'))
        self.assertTrue(accepted)
        self.assertEqual(reason, 'ok')

    def test_detail_url_has_its_own_reason(self):
        accepted, reason = classify_response(
            detail_url(), load_fixture('flight_detail.json'))
        self.assertFalse(accepted)
        self.assertEqual(reason, 'detail-url')

    def test_rejection_code_is_reported_verbatim(self):
        accepted, reason = classify_response(
            list_url(), load_fixture('flight_list_rejected_101.json'))
        self.assertFalse(accepted)
        self.assertEqual(reason, 'code-101')

    def test_unrelated_endpoint(self):
        accepted, reason = classify_response(
            'https://www.djiag.com/api/web/v1/devices?page=1', {'code': 0})
        self.assertFalse(accepted)
        self.assertEqual(reason, 'not-flight-list')

    def test_code_0_without_data_list(self):
        accepted, reason = classify_response(list_url(), {'code': 0})
        self.assertFalse(accepted)
        self.assertEqual(reason, 'no-data-list')

    def test_missing_code(self):
        accepted, reason = classify_response(list_url(), {'data': []})
        self.assertFalse(accepted)
        self.assertEqual(reason, 'code-none')


class PageSizeTests(unittest.TestCase):
    """page_size is a per-session setting with its own control, not a constant.

    What the signature test proved is narrower: an ALTERED URL is rejected.
    The value itself changes when the site's own control is used, and it has
    been seen at 30 in one session and 50 in another.
    """

    def test_option_labels(self):
        self.assertEqual(parse_page_size_option('30 / page'), 30)
        self.assertEqual(parse_page_size_option('50 / page'), 50)
        self.assertEqual(parse_page_size_option('100/page'), 100)

    def test_localised_option_labels(self):
        # The first run of digits is taken rather than the wording matched,
        # so a Russian or Chinese UI still parses.
        self.assertEqual(parse_page_size_option('50 / страница'), 50)
        self.assertEqual(parse_page_size_option('100 条/页'), 100)

    def test_unreadable_labels(self):
        for text in ('', None, 'per page', 'all'):
            self.assertIsNone(parse_page_size_option(text))

    def test_read_back_from_a_request_url(self):
        self.assertEqual(parse_page_size_from_url(list_url(page_size=30)), 30)
        self.assertEqual(parse_page_size_from_url(list_url(page_size=100)), 100)

    def test_read_back_when_absent(self):
        self.assertIsNone(parse_page_size_from_url(
            'https://www.djiag.com/api/web/v1/flight_records?page=1'))
        self.assertIsNone(parse_page_size_from_url(''))


class CapturedPageTests(unittest.TestCase):

    def test_reads_meta_data_and_flights(self):
        page = CapturedPage(list_url(), load_fixture('flight_list_page1.json'))
        self.assertEqual(page.current_page, 1)
        self.assertEqual(page.total_pages, 37)
        self.assertEqual(len(page.flights), 2)
        self.assertEqual(page.flights[0]['id'], 900000001)

    def test_missing_meta_data_does_not_raise(self):
        page = CapturedPage(list_url(), {'code': 0, 'data': []})
        self.assertIsNone(page.current_page)
        self.assertIsNone(page.total_pages)
        self.assertEqual(page.flights, [])

    def test_non_object_rows_are_dropped(self):
        page = CapturedPage(list_url(),
                            {'code': 0, 'data': [{'id': 1}, None, 'x']})
        self.assertEqual(page.flights, [{'id': 1}])


if __name__ == '__main__':
    unittest.main()
