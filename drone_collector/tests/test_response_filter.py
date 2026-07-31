# -*- coding: utf-8 -*-
"""Response filtering (section 7.3 of the DRONE-003 task).

A list URL is accepted, a detail URL is rejected, a body with code 101 is
rejected, a body with code 200 and a data list is accepted.

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

    def test_code_200_with_a_data_list_is_accepted(self):
        body = load_fixture('flight_list_page1.json')
        self.assertEqual(body_code(body), BODY_CODE_OK)
        self.assertTrue(is_flight_list_body(body))

    def test_code_101_is_rejected(self):
        body = load_fixture('flight_list_rejected_101.json')
        self.assertEqual(body_code(body), 101)
        self.assertFalse(is_flight_list_body(body))

    def test_code_408_is_rejected(self):
        self.assertFalse(is_flight_list_body(
            {'status': 200, 'code': 408, 'message': 'timeout', 'data': []}))

    def test_http_200_does_not_make_a_body_acceptable(self):
        # status 200 with code 101 -- the exact shape that fools a filter
        # written against response.status.
        body = load_fixture('flight_list_rejected_101.json')
        self.assertEqual(body.get('status'), 200)
        self.assertFalse(is_flight_list_body(body))

    def test_code_200_without_a_data_list_is_rejected(self):
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
            'https://www.djiag.com/api/web/v1/devices?page=1', {'code': 200})
        self.assertFalse(accepted)
        self.assertEqual(reason, 'not-flight-list')

    def test_code_200_without_data_list(self):
        accepted, reason = classify_response(list_url(), {'code': 200})
        self.assertFalse(accepted)
        self.assertEqual(reason, 'no-data-list')

    def test_missing_code(self):
        accepted, reason = classify_response(list_url(), {'data': []})
        self.assertFalse(accepted)
        self.assertEqual(reason, 'code-none')


class CapturedPageTests(unittest.TestCase):

    def test_reads_meta_data_and_flights(self):
        page = CapturedPage(list_url(), load_fixture('flight_list_page1.json'))
        self.assertEqual(page.current_page, 1)
        self.assertEqual(page.total_pages, 37)
        self.assertEqual(len(page.flights), 2)
        self.assertEqual(page.flights[0]['id'], 900000001)

    def test_missing_meta_data_does_not_raise(self):
        page = CapturedPage(list_url(), {'code': 200, 'data': []})
        self.assertIsNone(page.current_page)
        self.assertIsNone(page.total_pages)
        self.assertEqual(page.flights, [])

    def test_non_object_rows_are_dropped(self):
        page = CapturedPage(list_url(),
                            {'code': 200, 'data': [{'id': 1}, None, 'x']})
        self.assertEqual(page.flights, [{'id': 1}])


if __name__ == '__main__':
    unittest.main()
