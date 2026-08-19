# -*- coding: utf-8 -*-
"""GPS-3: экран «Факт по технике» и ответ оператора.

Что здесь проверяется и почему именно это:

1. **Схема модели совпадает с миграцией.** `db.create_all()` создаёт
   недостающую таблицу, но НИКОГДА не добавляет колонку. Разойдись модель с
   `migrate_gps_daily_001.py` — на свежей установке таблица встанет без
   колонки, а на боевой базе экран прочитает не то, что записал расчёт. Ни
   один другой контроль этого не увидит.

2. **Отказ считать виден человеку.** Сутки с редкой записью обязаны показать
   причину и не показать площадь. Пустая цифра без объяснения — худший из
   возможных выводов: её примут за ноль работы.

3. **«Расчёта нет» и «работы нет» — разные экраны.** Строка агрегата без
   участков означает «мы смотрели, работы нет»; отсутствие строки означает
   «мы не смотрели». Если экран покажет одно и то же, различие, ради которого
   в GPS-2 всегда пишется строка, пропадает на последнем шаге.

4. **Ответ оператора пишется, заменяется и снимается**, и никуда, кроме двух
   колонок, экран не пишет.

Запуск:
  python -m unittest tests.test_gps_fact_screen -v
"""
import json
import re
import unittest

from datetime import date, datetime

from tests.harness import app, db, reset_db, create_admin, create_org, login, CSRF
from models import (GpsDailyAggregate, GpsWorkPolygon, Organization, User,
                    UserModulePermission, VialonMapping, ROLE_OPERATOR)

import migrate_gps_daily_001 as gps_mig
import gps_routes

DAY = date(2026, 7, 27)

# Прямоугольник рядом с Бухарой, замкнутый: 64.55..64.56 x 39.99..40.00
SQUARE = json.dumps({"type": "Polygon", "coordinates": [[
    [64.550, 39.990], [64.560, 39.990], [64.560, 40.000],
    [64.550, 40.000], [64.550, 39.990]]]})


def _aggregate(wialon_id=3464, reason=None, **kwargs):
    values = dict(work_date=DAY, wialon_id=wialon_id, points_total=1598,
                  points_work=990, track_km=32.1, interval_median_s=30.0,
                  sats_median=14.0, motion_gaps=0, lost_seconds=0.0,
                  gps_jumps=0, reason=reason,
                  method_version='adaptive-alpha-2026-08-12',
                  computed_at=datetime(2026, 7, 28, 3, 0, 0))
    values.update(kwargs)
    return GpsDailyAggregate(**values)


def _site(wialon_id=3464, number=1, area=8.772, minutes=317.4,
          suggested='работа', **kwargs):
    values = dict(work_date=DAY, wialon_id=wialon_id, site_number=number,
                  area_ha=area, minutes=minutes, polygon_geojson=SQUARE,
                  alpha_used_m=10.0, pass_spacing_m=5.45,
                  suggested_label=suggested)
    values.update(kwargs)
    return GpsWorkPolygon(**values)


class SchemaAgreement(unittest.TestCase):
    """Пункт 1: модель и миграция описывают одну и ту же таблицу."""

    def test_aggregate_columns_match_the_migration(self):
        model = {c.name for c in GpsDailyAggregate.__table__.columns}
        self.assertEqual(model, set(gps_mig.EXPECTED_AGGREGATE_COLUMNS))

    def test_polygon_columns_match_the_migration(self):
        model = {c.name for c in GpsWorkPolygon.__table__.columns}
        self.assertEqual(model, set(gps_mig.EXPECTED_POLYGON_COLUMNS))

    def test_table_names_match_the_migration(self):
        self.assertEqual(GpsDailyAggregate.__tablename__, 'gps_daily_aggregates')
        self.assertEqual(GpsWorkPolygon.__tablename__, 'gps_work_polygons')


class FactScreen(unittest.TestCase):
    def setUp(self):
        reset_db()
        self.admin_id = create_admin()
        self.org_id = create_org()
        # [REASON]: язык по умолчанию у пользователя -- узбекский, и экран
        # честно рисуется по-узбекски. Проверки ниже написаны на русских
        # подписях, поэтому язык задаётся явно; отдельный тест ниже смотрит
        # ровно на узбекский интерфейс.
        with app.app_context():
            User.query.get(self.admin_id).language = 'ru'
            db.session.commit()

    def _client(self):
        client = app.test_client()
        login(client, self.admin_id)
        return client

    def _get(self, url='/gps/fact'):
        resp = self._client().get(url)
        self.assertEqual(resp.status_code, 200, url)
        return resp.get_data(as_text=True)

    def test_a_published_day_shows_its_hectares_and_the_site(self):
        with app.app_context():
            db.session.add(_aggregate())
            db.session.add(_site())
            db.session.commit()
        html = self._get()
        self.assertIn('8.772', html)
        self.assertIn('317.4', html)
        self.assertIn('<polygon', html)
        self.assertIn('без геозоны', html)

    def test_a_machine_name_is_shown_when_the_mapping_knows_it(self):
        with app.app_context():
            db.session.add(_aggregate())
            db.session.add(VialonMapping(vialon_name='МТЗ 261 EA',
                                         wialon_id=3464))
            db.session.commit()
        self.assertIn('МТЗ 261 EA', self._get())

    def test_an_unmapped_machine_is_shown_by_its_wialon_id(self):
        # [REASON]: подставить имя по похожести номера нельзя -- шесть номеров
        # указывают на несколько объектов, у одной машины их три. Пусть лучше
        # стоит голый id, чем чужое имя.
        with app.app_context():
            db.session.add(_aggregate())
            db.session.commit()
        self.assertIn('3464', self._get())

    def test_a_refused_day_shows_the_reason_and_no_hectares(self):
        with app.app_context():
            db.session.add(_aggregate(reason='redkaya_zapis',
                                      interval_median_s=301.0))
            db.session.commit()
        html = self._get()
        self.assertIn('Редкая запись', html)
        self.assertIn('301.0', html)
        self.assertNotIn('<polygon', html)

    def test_looked_and_found_nothing_differs_from_did_not_look(self):
        # строка есть, участков нет
        with app.app_context():
            db.session.add(_aggregate(points_work=12))
            db.session.commit()
        looked = self._get()
        self.assertIn('Работы за эти сутки не найдено', looked)
        self.assertNotIn('За эти сутки расчёта нет', looked)

        # строки нет вовсе
        with app.app_context():
            GpsDailyAggregate.query.delete()
            db.session.commit()
        not_looked = self._get()
        self.assertIn('За эти сутки расчёта нет', not_looked)
        self.assertNotIn('Работы за эти сутки не найдено', not_looked)

    def test_the_default_machine_is_one_that_has_something_to_show(self):
        with app.app_context():
            db.session.add(_aggregate(wialon_id=1001, reason='net_dvizheniya'))
            db.session.add(_aggregate(wialon_id=9002))
            db.session.add(_site(wialon_id=9002))
            db.session.commit()
        html = self._get()
        self.assertIn('8.772', html)
        selected = re.search(r'<option value="(\d+)" selected>', html)
        self.assertIsNotNone(selected)
        self.assertEqual(selected.group(1), '9002')

    def test_the_uzbek_interface_shows_uzbek_and_not_russian(self):
        # [REASON]: подпись, забытая на языке исходника, видна только тому, кто
        # смотрит вторым языком -- а смотрят обычно первым. Так у этого проекта
        # уже уезжали целые экраны (проверка t-key-missing родилась из этого).
        with app.app_context():
            User.query.get(self.admin_id).language = 'uz'
            db.session.add(_aggregate(reason='redkaya_zapis'))
            db.session.commit()
        html = self._get()
        self.assertIn('Трек сийрак ёзилган', html)
        self.assertNotIn('Редкая запись', html)
        self.assertNotIn('Техника бўйича факт — GPS'.replace('бўйича', 'по'), html)

    def test_the_screen_is_reachable_from_the_sidebar(self):
        # [REASON]: экран без входа не сделан наполовину -- он не сделан.
        # Ссылка добавлена в base_next.html (общий файл, объявлено в PR).
        self.assertIn('/gps/fact', self._get('/'))

    def test_a_named_machine_can_still_be_asked_for(self):
        with app.app_context():
            db.session.add(_aggregate(wialon_id=1001, reason='net_dvizheniya'))
            db.session.add(_aggregate(wialon_id=9002))
            db.session.commit()
        html = self._get('/gps/fact?date=2026-07-27&unit=1001')
        self.assertIn('Движения нет', html)


class OperatorAnswer(unittest.TestCase):
    def setUp(self):
        reset_db()
        self.admin_id = create_admin()
        with app.app_context():
            db.session.add(_aggregate())
            db.session.add(_site())
            db.session.commit()
            self.site_id = GpsWorkPolygon.query.one().id

    def _post(self, label, site_id=None, user_id=None):
        client = app.test_client()
        login(client, user_id or self.admin_id)
        return client.post('/gps/fact/answer', data={
            'csrf_token': CSRF, 'site_id': site_id or self.site_id,
            'label': label})

    def _site_row(self):
        with app.app_context():
            return GpsWorkPolygon.query.get(self.site_id)

    def test_an_answer_is_stored_with_its_time(self):
        self.assertEqual(self._post('проезд').status_code, 302)
        row = self._site_row()
        self.assertEqual(row.operator_label, 'проезд')
        self.assertIsNotNone(row.decided_at)

    def test_a_second_answer_replaces_the_first(self):
        self._post('проезд')
        self._post('работа')
        self.assertEqual(self._site_row().operator_label, 'работа')

    def test_an_answer_can_be_taken_back(self):
        self._post('работа')
        self._post('')
        row = self._site_row()
        self.assertIsNone(row.operator_label)
        self.assertIsNone(row.decided_at)

    def test_the_machines_suggestion_is_not_touched_by_the_answer(self):
        # [REASON]: подсказка -- это то, что предсказало предрегистрированное
        # правило, а ответ -- то, что сказал человек. Слейся они, набор
        # перестал бы быть независимым от правила, которое на нём проверяют.
        self._post('проезд')
        self.assertEqual(self._site_row().suggested_label, 'работа')

    def test_a_label_the_screen_never_offers_is_refused(self):
        self.assertEqual(self._post('не знаю').status_code, 400)
        self.assertIsNone(self._site_row().operator_label)

    def test_an_unknown_site_is_a_404(self):
        self.assertEqual(self._post('работа', site_id=999999).status_code, 404)

    def test_a_user_without_the_wialon_module_gets_403(self):
        with app.app_context():
            operator = User(username='gps_op', role=ROLE_OPERATOR, language='ru')
            operator.set_password('x')
            db.session.add(operator)
            db.session.flush()
            db.session.add(UserModulePermission(
                user_id=operator.id, module_code='fuel', has_access=True))
            db.session.commit()
            operator_id = operator.id
        client = app.test_client()
        login(client, operator_id)
        self.assertEqual(client.get('/gps/fact').status_code, 403)
        self.assertEqual(self._post('работа', user_id=operator_id).status_code, 403)
        # [REASON]: и ссылки он тоже не видит. Пункт, ведущий в 403, -- это
        # ровно то, против чего написано правило показа в _module_nav.html:
        # пункт, который не открыть, не показывается вовсе.
        self.assertNotIn('/gps/fact', client.get('/fuel/').get_data(as_text=True))


class Drawing(unittest.TestCase):
    """Полигоны в картинке: без картинки экран не заменит ручной обвод."""

    def test_a_square_field_comes_out_square_on_screen(self):
        # [REASON]: градус долготы на широте Бухары примерно в 0,77 раза
        # короче градуса широты. 0,01 градуса по долготе -- это около 855 м,
        # 0,01 по широте -- 1110 м. Без поправки квадрат на экране стал бы
        # квадратом на местности вытянутым, и человек не узнал бы своё поле.
        site = _site()
        shapes, width, height = gps_routes._svg_shapes([site])
        self.assertEqual(len(shapes), 1)
        self.assertAlmostEqual(width / height, 0.77, delta=0.01)
        self.assertEqual(len(shapes[0]['points'].split()), 5)

    def test_broken_geometry_does_not_take_the_screen_down(self):
        self.assertEqual(gps_routes._svg_shapes(
            [_site(polygon_geojson='not json at all')]), ([], 0, 0))
        self.assertEqual(gps_routes._svg_shapes(
            [_site(polygon_geojson='{"type": "Point", "coordinates": [1, 2]}')]),
            ([], 0, 0))

    def test_several_sites_share_one_frame(self):
        far = json.dumps({"type": "Polygon", "coordinates": [[
            [64.570, 39.990], [64.580, 39.990], [64.580, 40.000],
            [64.570, 40.000], [64.570, 39.990]]]})
        shapes, width, height = gps_routes._svg_shapes(
            [_site(), _site(number=2, polygon_geojson=far)])
        self.assertEqual(len(shapes), 2)
        # два квадрата рядом: кадр вдвое с лишним шире одного
        self.assertGreater(width / height, 1.5)


if __name__ == '__main__':
    unittest.main()
