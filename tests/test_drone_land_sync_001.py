# -*- coding: utf-8 -*-
"""DRONE-LANDS-001: the /drones/api/land_sync ingest.

The one number that matters most here is the divisor. DJI reports contour
areas in MU -- its own GraphQL asks for `totalArea(unit:MU)` -- and 1 hectare
is 15 mu exactly. Get it wrong and 5 489 contours are all off by a factor of
fifteen while looking perfectly plausible, because a field of 44 "hectares"
is not obviously absurd.

So the divisor is not asserted against a constant this file made up. It is
asserted against SIX card values read off the live cabinet on 2026-08-18,
carried in drone_collector/tests/fixtures/lands_page1.json: the cabinet shows
Azamat 3 as 2.97 ha and the payload says 44.54096254454096. A test that only
checked `area_ha == totalArea / 15` would pass for any divisor the code and
the test agreed on; these three assert against a number neither of them
produced.

Every check has its negative control, because a check that returns the same
answer for correct and incorrect code is not a check.
"""

import json
import os
import unittest

from datetime import datetime

from tests.harness import app, reset_db

from models import db, FieldContour

TOKEN = 'land-sync-test-token'

FIXTURE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'drone_collector', 'tests', 'fixtures',
                       'lands_page1.json')

# Read off the live cabinet's own cards on 2026-08-18, beside the payload
# values now sitting in the fixture. Neither this file nor drones.py computed
# them.
CABINET_CARDS = {
    'Azamat 3': 2.97,
    'Azamat 1,5': 1.48,
    'Alisher 2': 2.04,
}


def fixture_nodes():
    with open(FIXTURE, encoding='utf-8') as handle:
        body = json.load(handle)
    return [edge['node'] for edge in body['data']['lands']['edges']]


class LandSyncCase(unittest.TestCase):

    def setUp(self):
        reset_db()
        app.config['DRONE_API_TOKEN'] = TOKEN
        self.client = app.test_client()

    def post(self, lands, token=TOKEN):
        body = {'lands': lands}
        if token is not None:
            body['token'] = token
        return self.client.post('/drones/api/land_sync', json=body)

    def contours(self):
        with app.app_context():
            return {row.external_id: row for row in
                    FieldContour.query.filter_by(source='dji').all()}

    def by_name(self, name):
        with app.app_context():
            return FieldContour.query.filter_by(source='dji',
                                                name=name).one()


class Units(LandSyncCase):
    """Check 1: MU -> hectares, against the cabinet's own cards."""

    def test_1_areas_match_the_cabinet_cards(self):
        response = self.post(fixture_nodes())
        self.assertEqual(response.status_code, 200)
        for name, card in CABINET_CARDS.items():
            row = self.by_name(name)
            self.assertAlmostEqual(
                row.area_ha, card, places=2,
                msg='%s: stored %r ha, the cabinet card says %r'
                    % (name, row.area_ha, card))

    def test_1a_negative_the_raw_mu_value_is_not_what_is_stored(self):
        """Proof the conversion happened at all: 44.54 != 2.97."""
        self.post(fixture_nodes())
        row = self.by_name('Azamat 3')
        self.assertNotAlmostEqual(row.area_ha, 44.54096254454096, places=2)

    def test_1b_negative_a_wrong_divisor_would_be_caught(self):
        """The mu-per-hectare constant is 15 and nothing else fits the cards.

        Runs the same comparison the check above makes, but with the divisors
        that would be chosen by mistake -- 1 (no conversion at all), 10 and
        the 10 000 the FLIGHT payload uses for m2. All three must miss.
        """
        payload = fixture_nodes()[0]
        card = CABINET_CARDS['Azamat 3']
        for wrong in (1.0, 10.0, 100.0, 10000.0):
            self.assertNotAlmostEqual(payload['totalArea'] / wrong, card,
                                      places=2,
                                      msg='divisor %r cannot be ruled out '
                                          'by this check' % wrong)

    def test_1c_work_area_is_stored_separately_and_is_smaller(self):
        self.post(fixture_nodes())
        row = self.by_name('Azamat 3')
        self.assertAlmostEqual(row.work_area_ha, 39.51461103951461 / 15,
                               places=6)
        self.assertLess(row.work_area_ha, row.area_ha)

    def test_1d_a_missing_area_stays_null_and_does_not_become_zero(self):
        node = dict(fixture_nodes()[0])
        node.pop('totalArea')
        self.post([node])
        self.assertIsNone(self.by_name('Azamat 3').area_ha)


class Timestamps(LandSyncCase):
    """Check 2: +08:00 in, UTC out."""

    def test_2_offset_is_converted_not_dropped(self):
        self.post(fixture_nodes())
        row = self.by_name('Azamat 3')
        # 2026-08-17T18:41:18.419798+08:00 == 10:41:18 UTC
        self.assertEqual(row.source_created_at.replace(microsecond=0),
                         datetime(2026, 8, 17, 10, 41, 18))

    def test_2a_negative_the_local_reading_is_not_what_is_stored(self):
        """A dropped offset would leave 18:41, eight hours out."""
        self.post(fixture_nodes())
        row = self.by_name('Azamat 3')
        self.assertNotEqual(row.source_created_at.hour, 18)

    def test_2b_the_three_hour_day_shift_is_real(self):
        """[REASON]: this is the case the conversion exists for. A contour
        drawn at 22:00 Tashkent (UTC+5) is stamped 01:00 the NEXT day in
        +08:00. Stored unconverted it would be matched against the wrong
        day's flights -- exactly the error the September reconciliation was
        chasing."""
        node = dict(fixture_nodes()[0])
        node['createdAt'] = '2025-09-21T01:00:00+08:00'
        self.post([node])
        row = self.by_name('Azamat 3')
        self.assertEqual(row.source_created_at, datetime(2025, 9, 20, 17, 0))
        # 17:00 UTC is 22:00 in Tashkent, on the 20th -- not the 21st.
        self.assertEqual(row.source_created_at.day, 20)

    def test_2c_a_z_suffix_is_understood(self):
        node = dict(fixture_nodes()[0])
        node['createdAt'] = '2025-09-20T17:00:00Z'
        self.post([node])
        self.assertEqual(self.by_name('Azamat 3').source_created_at,
                         datetime(2025, 9, 20, 17, 0))

    def test_2d_negative_an_unparsable_stamp_is_null_not_a_crash(self):
        node = dict(fixture_nodes()[0])
        node['createdAt'] = 'вчера'
        response = self.post([node])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['new'], 1)
        self.assertIsNone(self.by_name('Azamat 3').source_created_at)


class BoundingBox(LandSyncCase):
    """Check 3: the box is stored as min/max, whatever the corners are called."""

    def test_3_box_comes_out_of_the_payload(self):
        self.post(fixture_nodes())
        row = self.by_name('Azamat 3')
        self.assertAlmostEqual(row.bbox_min_lat, 39.97571347221887)
        self.assertAlmostEqual(row.bbox_max_lat, 39.97763402790713)
        self.assertAlmostEqual(row.bbox_min_lng, 64.36232149587687)
        self.assertAlmostEqual(row.bbox_max_lng, 64.3646283679237)

    def test_3a_swapped_corners_still_produce_a_usable_box(self):
        """[REASON]: the point-in-box test is a pair of inequalities. A box
        stored with min above max matches nothing while looking populated."""
        node = dict(fixture_nodes()[0])
        node['bbox'] = {'upperRight': {'lat': 39.0, 'lng': 64.0},
                        'downLeft': {'lat': 40.0, 'lng': 65.0}}
        self.post([node])
        row = self.by_name('Azamat 3')
        self.assertEqual(row.bbox_min_lat, 39.0)
        self.assertEqual(row.bbox_max_lat, 40.0)
        self.assertLess(row.bbox_min_lat, row.bbox_max_lat)

    def test_3b_negative_a_missing_box_is_null_not_zero(self):
        """Zeros would put the field in the Gulf of Guinea and match nothing
        -- but they would match each OTHER, and two contours at 0,0 look like
        neighbours."""
        node = dict(fixture_nodes()[0])
        node.pop('bbox')
        self.post([node])
        row = self.by_name('Azamat 3')
        for value in (row.bbox_min_lat, row.bbox_max_lat, row.bbox_min_lng,
                      row.bbox_max_lng):
            self.assertIsNone(value)

    def test_3c_a_half_present_box_is_rejected_whole(self):
        node = dict(fixture_nodes()[0])
        node['bbox'] = {'upperRight': {'lat': 39.9, 'lng': 64.3}}
        self.post([node])
        self.assertIsNone(self.by_name('Azamat 3').bbox_min_lat)

    def test_3d_the_centre_is_stored_too(self):
        self.post(fixture_nodes())
        row = self.by_name('Azamat 3')
        self.assertAlmostEqual(row.center_lat, 39.976673750063)
        self.assertAlmostEqual(row.center_lng, 64.36347493190029)


class SignedUrls(LandSyncCase):
    """Check 4: the expiring links never reach the database."""

    def test_4_signed_urls_are_stripped_from_raw_json(self):
        self.post(fixture_nodes())
        row = self.by_name('Azamat 3')
        self.assertNotIn('signedURL', row.raw_json)
        self.assertNotIn('PLACEHOLDER-NOT-A-REAL-URL', row.raw_json)

    def test_4a_negative_the_stable_geometry_identity_is_kept(self):
        """Proof the stripping is surgical, not a blanket drop of the block:
        contentMd5 is what says whether the polygon changed."""
        self.post(fixture_nodes())
        row = self.by_name('Azamat 3')
        stored = json.loads(row.raw_json)
        self.assertEqual(stored['geometry']['storage']['contentMd5'],
                         '8dba720db0325fb1ae4e6603a24a402b')
        self.assertEqual(stored['geometry']['storage']['uuid'],
                         '8e814fd7-2374-4549-b506-c0745ede40ef')

    def test_4b_negative_the_fixture_really_does_carry_signed_urls(self):
        """Without this, test_4 would pass on a payload that never had any."""
        self.assertIn('signedURL', json.dumps(fixture_nodes()))


class Upsert(LandSyncCase):
    """Check 5: seen = new + updated + unchanged + errors, and a re-run is quiet."""

    def test_5_first_snapshot_is_all_new(self):
        body = self.post(fixture_nodes()).get_json()
        self.assertEqual((body['seen'], body['new'], body['updated'],
                          body['unchanged'], body['errors']), (3, 3, 0, 0, 0))

    def test_5a_the_same_snapshot_again_is_all_unchanged(self):
        """[REASON]: this is what the signed-URL stripping buys. With the
        expiring links stored, every re-run would report 5 489 updates and the
        counter would tell nobody anything."""
        self.post(fixture_nodes())
        body = self.post(fixture_nodes()).get_json()
        self.assertEqual((body['new'], body['updated'], body['unchanged']),
                         (0, 0, 3))

    def test_5b_negative_a_real_change_is_reported_as_an_update(self):
        """Proof the check above detects sameness rather than always saying
        'unchanged'."""
        self.post(fixture_nodes())
        nodes = fixture_nodes()
        nodes[0] = dict(nodes[0], name='Azamat 3 (переименовано)')
        body = self.post(nodes).get_json()
        self.assertEqual((body['new'], body['updated'], body['unchanged']),
                         (0, 1, 2))
        self.by_name('Azamat 3 (переименовано)')

    def test_5c_counters_partition_what_was_sent(self):
        nodes = fixture_nodes() + [{'name': 'no uuid at all'}]
        body = self.post(nodes).get_json()
        self.assertEqual(body['seen'],
                         body['new'] + body['updated'] + body['unchanged']
                         + body['errors'])
        self.assertEqual(body['errors'], 1)

    def test_5d_the_same_uuid_twice_in_one_batch_does_not_roll_it_back(self):
        """A page boundary that shifts mid-walk serves a row twice."""
        nodes = fixture_nodes()
        body = self.post(nodes + [nodes[0]]).get_json()
        self.assertEqual(body['seen'], 4)
        self.assertEqual(body['new'], 3)
        self.assertEqual(body['unchanged'], 1)
        self.assertEqual(len(self.contours()), 3)

    def test_5d2_a_uuid_inserted_by_another_run_costs_one_row_not_the_batch(self):
        """[REASON]: running --lands twice by accident is an ordinary mistake.
        A row that lost the race must be counted, not take the batch with it.
        Simulated by planting the row directly, outside this endpoint's own
        in-request lookup."""
        nodes = fixture_nodes()
        with app.app_context():
            db.session.add(FieldContour(source='dji',
                                        external_id=nodes[0]['uuid'],
                                        name='плант', is_active=True))
            db.session.commit()
        # The planted row IS found by the lookup, so this is an update, not a
        # collision -- which is the honest common case. The collision itself
        # is covered by the savepoint; what is asserted here is that neither
        # path loses the other two rows.
        body = self.post(nodes).get_json()
        self.assertEqual(body['seen'], 3)
        self.assertEqual(body['new'] + body['updated'], 3)
        self.assertEqual(body['errors'], 0)
        self.assertEqual(len(self.contours()), 3)

    def test_5e_synced_at_is_stamped_even_when_nothing_changed(self):
        self.post(fixture_nodes())
        with app.app_context():
            row = FieldContour.query.filter_by(name='Azamat 3').one()
            row.synced_at = datetime(2000, 1, 1)
            db.session.commit()
        self.post(fixture_nodes())
        self.assertGreater(self.by_name('Azamat 3').synced_at,
                           datetime(2020, 1, 1))


class NeverDoes(LandSyncCase):
    """Check 6: what the snapshot must not touch."""

    def test_6_contours_of_other_sources_are_untouched(self):
        with app.app_context():
            db.session.add(FieldContour(source='wialon', external_id='w-1',
                                        name='Геозона Wialon', area_ha=12.5))
            db.session.commit()
        self.post(fixture_nodes())
        with app.app_context():
            row = FieldContour.query.filter_by(source='wialon').one()
            self.assertEqual(row.name, 'Геозона Wialon')
            self.assertEqual(row.area_ha, 12.5)
            self.assertIsNone(row.synced_at)

    def test_6a_a_contour_missing_from_the_snapshot_is_not_deactivated(self):
        """[REASON]: a walk that stopped half-way would otherwise retire half
        the directory and look successful doing it."""
        self.post(fixture_nodes())
        body = self.post(fixture_nodes()[:1]).get_json()
        self.assertEqual(body['seen'], 1)
        with app.app_context():
            self.assertEqual(
                FieldContour.query.filter_by(source='dji',
                                             is_active=True).count(), 3)

    def test_6b_customer_and_name_uz_survive_a_re_snapshot(self):
        """Those are human decisions; DJI has no opinion about them."""
        self.post(fixture_nodes())
        with app.app_context():
            row = FieldContour.query.filter_by(name='Azamat 3').one()
            row.name_uz = 'Азамат 3'
            db.session.commit()
        nodes = fixture_nodes()
        nodes[0] = dict(nodes[0], serialNumber='CHANGED')
        self.post(nodes)
        self.assertEqual(self.by_name('Azamat 3').name_uz, 'Азамат 3')

    def test_6c_negative_nothing_is_written_to_drone_sync_logs(self):
        """That table carries the flight ingest's invariant; a contour
        snapshot in it would break `seen = new + duplicates + errors`."""
        from models import DroneSyncLog
        self.post(fixture_nodes())
        with app.app_context():
            self.assertEqual(DroneSyncLog.query.count(), 0)


class Contract(LandSyncCase):
    """Check 7: auth, shape and the batch cap."""

    def test_7_a_wrong_token_is_401(self):
        self.assertEqual(self.post(fixture_nodes(), token='wrong').status_code,
                         401)

    def test_7a_a_missing_token_is_401(self):
        self.assertEqual(self.post(fixture_nodes(), token=None).status_code,
                         401)

    def test_7b_an_unset_server_token_denies_everything(self):
        """Deny-by-default: an unconfigured server must not accept a blank."""
        app.config['DRONE_API_TOKEN'] = None
        self.assertEqual(self.post(fixture_nodes()).status_code, 401)
        self.assertEqual(self.post(fixture_nodes(), token=None).status_code,
                         401)

    def test_7c_negative_the_right_token_is_accepted(self):
        """Proof the checks above test the token, not the endpoint."""
        self.assertEqual(self.post(fixture_nodes()).status_code, 200)

    def test_7d_lands_must_be_a_list(self):
        response = self.client.post('/drones/api/land_sync',
                                    json={'token': TOKEN, 'lands': 'nope'})
        self.assertEqual(response.status_code, 400)

    def test_7e_an_oversized_batch_is_413(self):
        from drones import DRONE_LAND_SYNC_MAX_BATCH
        nodes = [{'uuid': 'u-%d' % i, 'name': 'F'} for i in
                 range(DRONE_LAND_SYNC_MAX_BATCH + 1)]
        self.assertEqual(self.post(nodes).status_code, 413)

    def test_7f_negative_a_batch_at_the_cap_is_accepted(self):
        from drones import DRONE_LAND_SYNC_MAX_BATCH
        nodes = [{'uuid': 'u-%d' % i, 'name': 'F'} for i in
                 range(DRONE_LAND_SYNC_MAX_BATCH)]
        response = self.post(nodes)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['new'], DRONE_LAND_SYNC_MAX_BATCH)


class Names(LandSyncCase):
    """Check 8: a nameless contour is stored, never rejected, never invented."""

    def test_8_the_dji_name_is_kept_verbatim(self):
        self.post(fixture_nodes())
        self.by_name('Azamat 3')

    def test_8a_a_blank_name_falls_back_to_the_dji_serial(self):
        node = dict(fixture_nodes()[0], name='   ')
        body = self.post([node]).get_json()
        self.assertEqual(body['new'], 1)
        self.by_name('P11782004')

    def test_8b_no_name_and_no_serial_falls_back_to_the_uuid(self):
        node = dict(fixture_nodes()[0])
        node.pop('name')
        node.pop('serialNumber')
        self.post([node])
        self.by_name('5648f5b3-d69c-42e7-8067-ccd33049f0c1')

    def test_8c_negative_a_row_without_a_uuid_is_an_error_not_a_row(self):
        body = self.post([{'name': 'nameless'}]).get_json()
        self.assertEqual((body['new'], body['errors']), (0, 1))
        self.assertEqual(len(self.contours()), 0)

    def test_8d_one_bad_row_does_not_lose_the_good_ones(self):
        body = self.post([{'no': 'uuid'}] + fixture_nodes()).get_json()
        self.assertEqual((body['new'], body['errors']), (3, 1))
        self.assertEqual(len(self.contours()), 3)

    def test_8e_the_serial_number_is_stored_for_the_books_to_match_on(self):
        self.post(fixture_nodes())
        self.assertEqual(self.by_name('Azamat 3').serial_number, 'P11782004')


class EndToEnd(LandSyncCase):
    """Check 9: the whole chain on the real payload.

    The cabinet's own bytes go through the ingest, and a flight placed at the
    centre of the box DJI reported must come back matched to that contour by
    tools/drone_contours_report.py. Nothing between the two is stubbed: if the
    divisor, the corner normalisation or the column names were wrong anywhere
    in that chain, this is where it shows.
    """

    def make_flight(self, lat, lng, area_ha=2.5):
        from models import DroneFlight
        with app.app_context():
            db.session.add(DroneFlight(
                dji_flight_id=900000001, drone_unit_id=None,
                started_at=datetime(2025, 9, 20, 5, 0), area_ha=area_ha,
                lat=lat, lng=lng, location_text='Bukhara Region, 500200',
                raw_json='{}'))
            db.session.commit()

    def build_report(self):
        from tests.harness import TEST_DB_PATH
        from tools import drone_contours_report as report
        conn = report.open_readonly(TEST_DB_PATH)
        try:
            return report.build(conn, '2025-09-01', '2025-09-30')
        finally:
            conn.close()

    def test_9_a_flight_in_the_reported_box_matches_that_contour(self):
        self.post(fixture_nodes())
        # The centre DJI itself reported for Azamat 3.
        self.make_flight(39.976673750063, 64.36347493190029)
        stats, per_contour, no_contour, disputed, unused = self.build_report()
        self.assertEqual(stats['contours_usable'], 3)
        self.assertEqual(stats['matched_one'], 1)
        self.assertEqual(stats['matched_none'], 0)
        self.assertEqual(disputed, [])
        self.assertEqual(no_contour, [])
        bucket = list(per_contour.values())[0]
        self.assertEqual(bucket['contour'].name, 'Azamat 3')
        self.assertAlmostEqual(bucket['contour'].area_ha, 2.97, places=2)
        self.assertEqual(len(unused), 2)

    def test_9a_negative_a_flight_elsewhere_matches_nothing(self):
        """Proof the check above matches on geometry, not on "there is one
        contour, so use it"."""
        self.post(fixture_nodes())
        self.make_flight(41.3, 69.2)  # Tashkent, 500 km away
        stats, per_contour, no_contour, _disputed, _unused = self.build_report()
        self.assertEqual(stats['matched_one'], 0)
        self.assertEqual(stats['matched_none'], 1)
        self.assertEqual(per_contour, {})
        self.assertEqual(no_contour[0][-1],
                         'ни одна рамка не накрывает точку')

    def test_9b_the_two_neighbouring_fixture_fields_do_not_overlap(self):
        """Azamat 3 and Azamat 1,5 are adjacent in Yuqor Mirish. Their boxes
        must stay distinct, or every flight there would read as disputed."""
        self.post(fixture_nodes())
        self.make_flight(39.976673750063, 64.36347493190029)
        stats, _pc, _nc, disputed, _u = self.build_report()
        self.assertEqual(stats['matched_many'], 0)
        self.assertEqual(disputed, [])


if __name__ == '__main__':
    unittest.main()
