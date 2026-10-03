# -*- coding: utf-8 -*-
"""Экран сопоставления Wialon: «Добавить» сохраняет новую строку.

[REASON]: с 04.06.2026 (коммит 4d76db1) проверка дубля в `wialon_mapping_save`
находила саму сохраняемую строку: новая строка уже стояла в сессии, запрос с
autoflush сначала записывал её, а потом видел её же. «Добавить» всегда
отвечал «Такой Wialon объект уже существует» и ничего не сохранял. 01.10.2026
владелец дважды размечал легковые «Нет в системе», и ни одна строка не легла
-- на экране это выглядело так, будто строки уже есть.

Здесь держится: новая строка сохраняется -- и «Нет в системе», и с машиной;
имя, у которого строка уже есть, обновляет её, а не заводит вторую; правка
своей строки сохраняется; переименование в чужое имя по-прежнему отказывается
-- проверка дубля работает, но не против самой себя. И имя ложится так, как
его нормализует импорт: `tools/gps_link_mappings.py` сверяет с Wialon именно
его.
"""
import unittest

from tests.harness import app, db, reset_db, create_admin, create_org, login, CSRF
from models import Equipment, VialonMapping

SAVED = ('Маппинг сохранён', 'Маппинг сақланди')
DUPLICATE = ('Такой Wialon объект уже существует',
             'Бундай Wialon номи аллақачон мавжуд')


class MappingSave(unittest.TestCase):

    def setUp(self):
        reset_db()
        self.admin = create_admin()
        self.client = app.test_client()
        login(self.client, self.admin)

    def save(self, **fields):
        data = {'csrf_token': CSRF}
        data.update(fields)
        response = self.client.post('/wialon/mapping/save', data=data,
                                    follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def rows(self):
        with app.app_context():
            return [(m.vialon_name, m.equipment_id, bool(m.skip))
                    for m in VialonMapping.query.order_by(VialonMapping.id)]

    def existing(self, name, equipment_id=None, skip=False):
        with app.app_context():
            mapping = VialonMapping(vialon_name=name, equipment_id=equipment_id,
                                    skip=skip, created_by=self.admin)
            db.session.add(mapping)
            db.session.commit()
            return mapping.id

    def machine(self):
        organization = create_org()
        with app.app_context():
            equipment = Equipment(name='МТЗ-80.1', plate='80 239 NA',
                                  category='mtz', organization_id=organization,
                                  is_active=True)
            db.session.add(equipment)
            db.session.commit()
            return equipment.id

    def assertSaved(self, html):
        self.assertTrue(any(text in html for text in SAVED), html[-2000:])
        self.assertFalse(any(text in html for text in DUPLICATE))

    def assertRefusedAsDuplicate(self, html):
        self.assertTrue(any(text in html for text in DUPLICATE))
        self.assertFalse(any(text in html for text in SAVED))

    def test_a_new_row_marked_not_ours_is_saved(self):
        html = self.save(vialon_name='Cobalt 80 915 MBA (Халимов Жобир)',
                         equipment_id='', skip='1')
        self.assertSaved(html)
        self.assertEqual(self.rows(),
                         [('Cobalt 80 915 MBA (Халимов Жобир)', None, True)])

    def test_a_new_row_with_a_machine_is_saved(self):
        tractor = self.machine()
        html = self.save(vialon_name='МТЗ 239 NA', equipment_id=str(tractor))
        self.assertSaved(html)
        self.assertEqual(self.rows(), [('МТЗ 239 NA', tractor, False)])

    def test_the_name_is_saved_as_the_import_normalises_it(self):
        # имя из Wialon с двойным пробелом -- как у Labo 80 482 CAA
        html = self.save(vialon_name='  Labo 80  482 CAA (Латипов Мухаммад) ',
                         skip='1')
        self.assertSaved(html)
        self.assertEqual(self.rows(),
                         [('Labo 80 482 CAA (Латипов Мухаммад)', None, True)])

    def test_a_name_that_has_a_row_updates_it_instead_of_adding_one(self):
        tractor = self.machine()
        self.existing('DAMAS 80 744 RBA', equipment_id=tractor)
        html = self.save(vialon_name='DAMAS 80 744 RBA', skip='1')
        self.assertSaved(html)
        self.assertEqual(self.rows(), [('DAMAS 80 744 RBA', None, True)])

    def test_editing_a_row_still_saves(self):
        row = self.existing('Nexia 80 050 KBA (Ойбек Кенжаев)')
        html = self.save(id=str(row), skip='1')
        self.assertSaved(html)
        self.assertEqual(self.rows(),
                         [('Nexia 80 050 KBA (Ойбек Кенжаев)', None, True)])

    def test_renaming_onto_another_rows_name_is_still_refused(self):
        first = self.existing('LABO 80 273 XBA (Ибрагимов Улугбек)', skip=True)
        self.existing('LABO 80 273 XBA (Салимов Шариф)', skip=True)
        html = self.save(id=str(first),
                         vialon_name='LABO 80 273 XBA (Салимов Шариф)', skip='1')
        self.assertRefusedAsDuplicate(html)
        self.assertEqual(self.rows(),
                         [('LABO 80 273 XBA (Ибрагимов Улугбек)', None, True),
                          ('LABO 80 273 XBA (Салимов Шариф)', None, True)])


if __name__ == '__main__':
    unittest.main()
