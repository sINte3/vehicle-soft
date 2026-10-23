# -*- coding: utf-8 -*-
"""Граница организаций на экранах GPS -- решение владельца 22.10.2026.

Владелец на вопрос сессии «должен ли не-админ на экранах GPS видеть только
машины своих организаций?» ответил: «да, только свои машины должен видеть».

Что проверяется и почему именно это:

1. **«Факт по технике»** показывает не-админу только машины его
   организаций: в списке, в подсказках «скрыто фильтрами», в выборе суток.
   Номер чужой машины в адресе её не открывает.
2. **Ответ «работа/проезд»** по участку чужой машины -- 403, ответ не
   записан: экран участок не показывает, но номер участка можно подставить
   в форму руками.
3. **«Машина за период»** -- та же граница.
4. **Объект без машины в справочнике** не-админ не видит: ни к одной
   организации он не относится. Человек без организаций не видит ни одной
   машины, а не все -- как в «Сверке» и сверке нарядов.
5. **Администратор** видит всё, как до решения.
6. **Объект со сменённым трекером** (две строки сопоставления) относится к
   той организации, под которой его называет экран (_machines): первая
   строка с машиной. Иначе список и граница разошлись бы.

Запуск:
  python -m unittest tests.test_gps_org_scope -v
"""
import json
import re
import unittest

from datetime import date, datetime
from html import unescape

from tests.harness import app, db, reset_db, create_admin, login, CSRF
from models import (Equipment, GpsDailyAggregate, GpsWorkPolygon, Organization,
                    User, UserModulePermission, VialonMapping, ROLE_OPERATOR)

import gps_routes

DAY = date(2026, 7, 27)
OTHER_DAY = date(2026, 7, 26)
SQUARE = json.dumps({"type": "Polygon", "coordinates": [[
    [64.550, 39.990], [64.560, 39.990], [64.560, 40.000],
    [64.550, 40.000], [64.550, 39.990]]]})


def aggregate(wialon_id, day=DAY):
    return GpsDailyAggregate(
        work_date=day, wialon_id=wialon_id, points_total=100, points_work=50,
        track_km=5.0, interval_median_s=30.0, sats_median=14.0, motion_gaps=0,
        lost_seconds=0.0, gps_jumps=0, reason=None,
        method_version='adaptive-alpha-2026-08-12',
        computed_at=datetime(2026, 7, 28, 3, 0, 0))


def site(wialon_id, day=DAY):
    return GpsWorkPolygon(
        work_date=day, wialon_id=wialon_id, site_number=1, area_ha=1.5,
        minutes=20.0, polygon_geojson=SQUARE, alpha_used_m=10.0,
        pass_spacing_m=5.0, suggested_label='работа')


class Scope(unittest.TestCase):
    """27.07: 101 -- «Сад» (80 613 EA), 102 -- «Когон» (80 156 СА), 103 --
    объект без машины в справочнике. 26.07 работала только машина 102.
    104 -- трекер сменили: первая строка сопоставления -- машина «Когона»,
    вторая -- машина «Сада»; экран называет её машиной «Когона».
    """

    def setUp(self):
        reset_db()
        self.admin_id = create_admin()
        with app.app_context():
            User.query.get(self.admin_id).language = 'ru'
            garden = Organization(name='Сад', short_name='', sort_order=1)
            kogon = Organization(name='Когон', short_name='', sort_order=2)
            db.session.add_all([garden, kogon])
            db.session.flush()
            self.garden_id, self.kogon_id = garden.id, kogon.id
            equipment = {}
            for key, name, plate, org in (
                    ('a', 'МТЗ-80Х', '80 613 EA', garden),
                    ('b', 'New Holland 7060', '80 156 СА', kogon),
                    ('c', 'МТЗ-82', '80 777 KA', kogon),
                    ('d', 'МТЗ-82', '80 778 KA', garden)):
                item = Equipment(name=name, plate=plate, category='mtz',
                                 organization_id=org.id)
                db.session.add(item)
                db.session.flush()
                equipment[key] = item.id
            for wialon_id, key in ((101, 'a'), (102, 'b'), (104, 'c'),
                                   (104, 'd')):
                db.session.add(VialonMapping(vialon_name='obj %d%s' % (wialon_id, key),
                                             wialon_id=wialon_id,
                                             equipment_id=equipment[key],
                                             skip=False))
                db.session.flush()
            db.session.add(VialonMapping(vialon_name='Объект 103',
                                         wialon_id=103, skip=False))
            for wialon_id in (101, 102, 103, 104):
                db.session.add(aggregate(wialon_id))
            db.session.add(aggregate(102, OTHER_DAY))
            db.session.add_all([site(101), site(102), site(103), site(104)])
            db.session.commit()
            self.sites = {s.wialon_id: s.id for s in GpsWorkPolygon.query}
        self.garden_user = self.operator('garden', [self.garden_id])
        self.kogon_user = self.operator('kogon', [self.kogon_id])
        self.nobody = self.operator('nobody', [])

    def operator(self, username, org_ids):
        with app.app_context():
            user = User(username=username, role=ROLE_OPERATOR,
                        full_name=username, language='ru')
            user.set_password('x')
            for org_id in org_ids:
                user.organizations.append(Organization.query.get(org_id))
            db.session.add(user)
            db.session.flush()
            db.session.add(UserModulePermission(
                user_id=user.id, module_code='wialon', has_access=True))
            db.session.commit()
            return user.id

    def get(self, user_id, url):
        client = app.test_client()
        login(client, user_id)
        resp = client.get(url)
        self.assertEqual(resp.status_code, 200, url)
        return resp.get_data(as_text=True)

    def answer(self, user_id, wialon_id, label='работа'):
        client = app.test_client()
        login(client, user_id)
        return client.post('/gps/fact/answer', data={
            'csrf_token': CSRF, 'site_id': self.sites[wialon_id],
            'label': label})

    def listed(self, html):
        listbox = html.split('role="listbox"')[1].split('</ul>')[0]
        return sorted(int(v) for v in re.findall(r'data-value="(\d+)"', listbox))

    def opened(self, html):
        found = re.search(r'<input type="hidden" name="unit" value="(\d*)"', html)
        return int(found.group(1)) if found and found.group(1) else None

    def hidden_keys(self, html):
        found = re.search(r"data-vs-combobox-hidden='([^']*)'", html)
        return json.loads(unescape(found.group(1)))

    def label(self, wialon_id):
        with app.app_context():
            return GpsWorkPolygon.query.get(self.sites[wialon_id]).operator_label

    # --- «Факт по технике» -------------------------------------------------------

    def test_fact_lists_only_the_machines_of_ones_organizations(self):
        self.assertEqual(self.listed(self.get(self.garden_user,
                                              '/gps/fact?date=2026-07-27')), [101])
        self.assertEqual(self.listed(self.get(self.kogon_user,
                                              '/gps/fact?date=2026-07-27')), [102, 104])

    def test_the_admin_still_sees_every_machine(self):
        self.assertEqual(self.listed(self.get(self.admin_id,
                                              '/gps/fact?date=2026-07-27')),
                         [101, 102, 103, 104])

    def test_a_foreign_machine_in_the_address_is_not_opened(self):
        html = self.get(self.garden_user, '/gps/fact?date=2026-07-27&unit=102')
        self.assertEqual(self.opened(html), 101)
        self.assertNotIn('80 156 СА', html)

    def test_a_foreign_machine_is_not_counted_as_hidden_by_filters(self):
        # «Скрыто фильтрами» -- про свои машины. Ключ чужой машины в странице
        # выдал бы её номер подсказкам в браузере.
        html = self.get(self.garden_user,
                        '/gps/fact?date=2026-07-27&cat=yukori&q=156')
        self.assertEqual(self.hidden_keys(html),
                         [gps_routes.search_key('МТЗ-80Х — 80 613 EA')])
        self.assertNotIn(gps_routes.search_key('80 156 СА'), html)

    def test_days_with_only_foreign_machines_are_not_offered(self):
        html = self.get(self.garden_user, '/gps/fact')
        self.assertIn('value="2026-07-27"', html)
        self.assertNotIn('value="2026-07-26"', html)
        self.assertIn('value="2026-07-26"', self.get(self.kogon_user, '/gps/fact'))

    def test_an_object_without_a_machine_is_seen_only_by_the_admin(self):
        for user_id in (self.garden_user, self.kogon_user):
            html = self.get(user_id, '/gps/fact?date=2026-07-27&unit=103')
            self.assertNotEqual(self.opened(html), 103)
            self.assertNotIn('Нет в справочнике техники', html)
        html = self.get(self.admin_id, '/gps/fact?date=2026-07-27&unit=103')
        self.assertEqual(self.opened(html), 103)

    def test_a_user_without_organizations_sees_no_machine(self):
        html = self.get(self.nobody, '/gps/fact?date=2026-07-27')
        self.assertIn('За эти сутки расчёта нет', html)
        self.assertNotIn('role="listbox"', html.split('За эти сутки расчёта нет')[1])
        self.assertEqual(self.listed(html), [])

    def test_a_changed_tracker_belongs_where_the_screen_names_it(self):
        # Первая строка сопоставления 104 -- машина «Когона»: так её называет
        # экран, так её и пропускает граница.
        kogon = self.get(self.kogon_user, '/gps/fact?date=2026-07-27&unit=104')
        self.assertEqual(self.opened(kogon), 104)
        self.assertIn('МТЗ-82 — 80 777 KA', kogon)
        garden = self.get(self.garden_user, '/gps/fact?date=2026-07-27&unit=104')
        self.assertNotEqual(self.opened(garden), 104)

    # --- ответ «работа/проезд» ------------------------------------------------------

    def test_an_answer_on_a_foreign_site_is_refused_and_not_written(self):
        self.assertEqual(self.answer(self.garden_user, 102).status_code, 403)
        self.assertIsNone(self.label(102))
        self.assertEqual(self.answer(self.nobody, 101).status_code, 403)
        self.assertIsNone(self.label(101))
        self.assertEqual(self.answer(self.garden_user, 103).status_code, 403)
        self.assertIsNone(self.label(103))

    def test_an_answer_on_ones_own_site_is_written(self):
        self.assertEqual(self.answer(self.garden_user, 101).status_code, 302)
        self.assertEqual(self.label(101), 'работа')
        self.assertEqual(self.answer(self.admin_id, 103, 'проезд').status_code, 302)
        self.assertEqual(self.label(103), 'проезд')

    # --- «Машина за период» -------------------------------------------------------------

    def test_the_period_card_lists_only_ones_own_machines(self):
        url = '/gps/machine?from=2026-07-26&to=2026-07-27'
        self.assertEqual(self.listed(self.get(self.garden_user, url)), [101])
        self.assertEqual(self.listed(self.get(self.kogon_user, url)), [102, 104])
        self.assertEqual(self.listed(self.get(self.admin_id, url)),
                         [101, 102, 103, 104])

    def test_the_period_card_does_not_open_a_foreign_machine(self):
        html = self.get(self.garden_user,
                        '/gps/machine?from=2026-07-26&to=2026-07-27&unit=102')
        self.assertEqual(self.opened(html), 101)
        self.assertNotIn('80 156 СА', html)
        html = self.get(self.nobody, '/gps/machine?from=2026-07-26&to=2026-07-27')
        self.assertIn('За этот период расчёта нет ни по одной машине', html)


if __name__ == '__main__':
    unittest.main()
