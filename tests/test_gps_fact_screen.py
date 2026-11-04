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
import os
import re
import unittest

from datetime import date, datetime

from tests.harness import app, db, reset_db, create_admin, create_org, login, CSRF
from models import (Equipment, GpsDailyAggregate, GpsWorkPolygon, Organization, User,
                    UserModulePermission, VialonMapping, ROLE_OPERATOR)

import migrate_gps_daily_001 as gps_mig
import gps_routes
from tests.test_agro_work_runbook import released

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Production до третьего шага «для человека» (v1.34, 04.11.2026): правила
# экрана там -- в карточке над фильтрами.
BEFORE_DAY_FOR_PEOPLE = 'a73d756dcb67c06388e58bc7c4a0a1ac64e59e34'
RULE_START = 'Сколько машина обработала за сутки — посчитано по её собственному треку'

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
        # 04.11.2026: гектары -- до сотых, как итог суток и подпись на карте;
        # минуты -- «5 ч 17 мин», как время в движении (8.772 га, 317.4 мин).
        self.assertIn('<div><b>8.77</b> га · 5 ч 17 мин</div>', html)
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
        self.assertIn('точка раз в 301 с', html)
        self.assertNotIn('<polygon', html)

    def test_an_unfinished_collection_shows_the_reason_and_the_hint(self):
        # GPS-11: коллектор не дошёл до конца суток -- причина словами,
        # обещание, что расчёт повторится сам, и никаких гектаров и полигонов.
        # Команды запуска на экране нет (04.11.2026): она нужна
        # администратору, и она в ранбуке, а не в сообщении людям.
        with app.app_context():
            db.session.add(_aggregate(reason='sbor_nepolnyy'))
            db.session.commit()
        html = self._get()
        self.assertIn('Сбор точек за эти сутки не завершён', html)
        self.assertIn('и расчёт повторится сам. ', html)
        self.assertNotIn('--catch-up', html)
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
        self.assertIn('<b>8.77</b> га', html)
        # Выбор машины -- поле с подсказками (09.10.2026): открытая машина --
        # в скрытом поле `unit` и отмечена в списке подсказок.
        selected = re.search(r'<input type="hidden" name="unit" value="(\d+)"', html)
        self.assertIsNotNone(selected)
        self.assertEqual(selected.group(1), '9002')
        self.assertEqual(re.findall(r'data-value="(\d+)"\s+aria-selected="true"', html),
                         ['9002'])

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

    def test_the_track_length_is_shown(self):
        # A1: «след» -- это точки, километры, запись и качество. Километры
        # лежали в агрегате с GPS-2, а на экран не выводились.
        with app.app_context():
            db.session.add(_aggregate(track_km=32.14))
            db.session.commit()
        html = self._get()
        self.assertIn('Пробег по треку, км', html)
        self.assertIn('32.1', html)

    def test_a_special_machine_shows_its_track_and_no_hectares(self):
        """A1: след без гектаров -- причина словами, участков нет.

        [REASON]: полигон кладётся в базу нарочно. Расчёт не удаляет полигоны,
        оставшиеся от счёта до решения 28.09 (на них могут быть ответы
        оператора), и экран обязан не показать их сам -- иначе гектары, которые
        владелец велел не считать, вернулись бы на экран через старую строку.
        """
        with app.app_context():
            db.session.add(_aggregate(reason='spetstekhnika', track_km=12.34))
            db.session.add(_site(area=0.77))
            db.session.commit()
        html = self._get()
        self.assertIn('Спецтехника — гектары не считаются', html)
        self.assertIn('поменяйте ей категорию', html)
        self.assertIn('12.3', html)                # след на месте
        self.assertIn('точек 1598, из них в работе 990', html)
        self.assertNotIn('0.77', html)             # гектаров нет
        self.assertNotIn('<polygon', html)
        self.assertNotIn('Участки за сутки', html)
        self.assertNotIn('Работы за эти сутки не найдено', html)

    def test_the_same_leftover_site_is_shown_on_a_field_machine(self):
        """Отрицательный контроль к тесту выше: без причины участок виден."""
        with app.app_context():
            db.session.add(_aggregate())
            db.session.add(_site(area=0.77))
            db.session.commit()
        html = self._get()
        self.assertIn('0.77', html)
        self.assertIn('Участки за сутки', html)

    def test_the_special_reason_speaks_uzbek_in_the_uzbek_interface(self):
        with app.app_context():
            User.query.get(self.admin_id).language = 'uz'
            db.session.add(_aggregate(reason='spetstekhnika'))
            db.session.commit()
        html = self._get()
        self.assertIn('Махсус техника — гектар ҳисобланмайди', html)
        self.assertIn('категориясини ўзгартиринг', html)
        self.assertNotIn('Спецтехника', html)
        self.assertNotIn('поменяйте', html)

    def _machine(self, wialon_id, name, plate, vialon_name=None):
        with app.app_context():
            equipment = Equipment(name=name, plate=plate, category='mtz',
                                  organization_id=self.org_id)
            db.session.add(equipment)
            db.session.flush()
            db.session.add(VialonMapping(
                vialon_name=vialon_name or 'obj %d' % wialon_id,
                wialon_id=wialon_id, equipment_id=equipment.id, skip=False))
            db.session.add(_aggregate(wialon_id=wialon_id))
            db.session.commit()

    def test_two_machines_of_one_model_are_told_apart_by_the_plate(self):
        """Отзыв владельца 28.09: дюжина одинаковых «МТЗ-80.1» в списке."""
        self._machine(5001, 'МТЗ-80.1', '80 248 HA')
        self._machine(5002, 'МТЗ-80.1', '80 249 HA')
        html = self._get('/gps/fact?date=2026-07-27')
        self.assertIn('МТЗ-80.1 — 80 248 HA', html)
        self.assertIn('МТЗ-80.1 — 80 249 HA', html)
        # и имя объекта Wialon не подменяет машину, когда машина известна
        self.assertNotIn('obj 5001', html)

    def test_a_machine_without_a_plate_is_shown_by_its_name_alone(self):
        self._machine(5001, 'Doosan 225', '')
        html = self._get('/gps/fact?date=2026-07-27')
        self.assertIn('>Doosan 225<', html)
        self.assertNotIn('Doosan 225 —', html)

    def test_the_list_of_machines_is_in_name_order(self):
        # Номера объектов нарочно в обратном порядке имён: сортировка по id
        # поставила бы «John Deere» раньше «Case» и голый номер между ними.
        self._machine(5003, 'Case 3230', '80 101 HA')
        self._machine(5002, 'John Deere 8R', '80 202 HA')
        with app.app_context():
            db.session.add(_aggregate(wialon_id=5000))    # без строки
            db.session.commit()
        html = self._get('/gps/fact?date=2026-07-27')
        listbox = html.split('role="listbox"')[1].split('</ul>')[0]
        options = re.findall(r'data-value="(\d+)"', listbox)
        self.assertEqual(options, ['5003', '5002', '5000'])

    def test_several_rows_of_one_object_give_one_stable_name(self):
        # Сменённые трекеры: две строки на один id. Имя берётся из строки с
        # машиной, как бы база ни вернула строки.
        with app.app_context():
            db.session.add(VialonMapping(vialon_name='NH 80 605 EA (Bitrek)',
                                         wialon_id=2982, skip=False))
            db.session.commit()
        self._machine(2982, 'New Holland 7060', '80 605 EA',
                      vialon_name='NH 80 605 EA (FMB 140)')
        with app.app_context():
            names = gps_routes._machine_names({2982})
        self.assertEqual(names, {2982: 'New Holland 7060 — 80 605 EA'})

    def test_a_named_machine_can_still_be_asked_for(self):
        with app.app_context():
            db.session.add(_aggregate(wialon_id=1001, reason='net_dvizheniya'))
            db.session.add(_aggregate(wialon_id=9002))
            db.session.commit()
        html = self._get('/gps/fact?date=2026-07-27&unit=1001')
        self.assertIn('Движения нет', html)


class DayForPeople(unittest.TestCase):
    """Решение владельца 04.11.2026, третий шаг: «Работа за день» для человека.

    Вместо карточки с абзацем правил -- одна строка; итог суток -- пять чисел
    над картой, запись трека -- одной строкой словами; на виду нет ни номера
    объекта Wialon, ни кода «razryvy=3», ни путей к файлам ключей; правила --
    дословно, один раз, свёрнуто внизу. Расчёт и маршрут не менялись: те же
    числа, на новом месте.
    """

    setUp = FactScreen.setUp
    _client = FactScreen._client
    _get = FactScreen._get

    def _day(self, *sites, **aggregate):
        with app.app_context():
            db.session.add(_aggregate(**aggregate))
            db.session.add_all(sites)
            db.session.commit()
        return self._get()

    @staticmethod
    def _rules(html):
        start = html.index('<details class="gps-fact-rules">\n  <summary>Как считается')
        return html[start:html.index('</details>', start)]

    def test_no_wall_of_text_above_the_numbers(self):
        html = self._day(_site())
        self.assertNotIn('vs-hero', html)
        lead = html.index('<p class="gps-fact-lead">')
        self.assertLess(lead, html.index('data-gps-fact-filters'))
        self.assertIn('Отметьте каждый участок: работа это или проезд.', html)
        # Ни абзаца правил, ни строки метода выше итога суток и карты.
        above = html[:html.index('class="gps-fact-day"')]
        self.assertNotIn(RULE_START, above)
        self.assertNotIn('Метод: ', above)

    def test_the_rules_are_kept_word_for_word_once_at_the_bottom(self):
        html = self._day(_site())
        rules = self._rules(html)
        self.assertEqual(html.count(RULE_START), 1)
        self.assertIn(RULE_START, rules)
        self.assertEqual(html.count('Версия метода: '), 1)
        self.assertIn('Версия метода: adaptive-alpha-2026-08-12.', rules)
        # «Как считается» -- после карты и участков, не между ними.
        self.assertGreater(html.index(rules), html.index('</section>'))

    def test_the_rules_are_the_production_text(self):
        # Дословно -- значит, выражение шаблона то же, что было в карточке на
        # production, по обоим языкам, и строка метода та же.
        before = released(self, BEFORE_DAY_FOR_PEOPLE, 'templates', 'gps', 'fact.html')
        with open(os.path.join(REPO_ROOT, 'templates', 'gps', 'fact.html'),
                  encoding='utf-8') as fh:
            after = fh.read()
        rule = re.search(r'<div class="vs-hero-sub">(\{\{ .*? \}\})</div>', before).group(1)
        method = re.search(r'<div class="vs-help vs-mb">\n  (\{\{ \'Метод: .*?\.)\n</div>',
                           before, re.S).group(1)
        rules = after[after.index('<details class="gps-fact-rules">\n  <summary>{{ \'Как считается\''):]
        self.assertIn('<p>%s</p>' % rule, rules)
        self.assertIn('<p>%s</p>' % method, rules)

    def test_the_day_is_summed_up_above_the_map(self):
        html = self._day(_site(), track_km=32.14)
        day = html[html.index('class="gps-fact-day"'):html.index('class="gps-fact-split')]
        labels = re.findall(r'<span class="gps-fact-fig-label">([^<]+)</span>', day)
        self.assertEqual(labels, ['Обработано, га', 'Участков', 'В движении',
                                  'Первое и последнее движение',
                                  'Пробег по треку, км'])
        values = re.findall(r'<span class="gps-fact-fig-value">([^<]+)</span>', day)
        self.assertEqual(values[0], '8.77')
        self.assertEqual(values[1], '1')
        self.assertEqual(values[4], '32.1')
        self.assertIn('Запись трека:', day)
        self.assertIn('точек 1598, из них в работе 990', day)
        self.assertNotIn('vs-stat', html)          # одиннадцати плиток нет

    def test_a_reason_follows_the_summary_it_refers_to(self):
        # «След трека выше измерен и верен»: показатели теперь и правда выше.
        html = self._day(reason='spetstekhnika')
        self.assertLess(html.index('class="gps-fact-day"'),
                        html.index('Спецтехника — гектары не считаются'))
        self.assertIn('<span class="gps-fact-fig-value">—</span>', html)

    def test_a_named_machine_shows_no_wialon_object_number(self):
        with app.app_context():
            db.session.add(VialonMapping(vialon_name='МТЗ 261 EA', wialon_id=3464))
            db.session.commit()
        html = self._day(_site())
        head = html[html.index('class="gps-fact-head"'):html.index('class="gps-fact-day"')]
        self.assertIn('МТЗ 261 EA', head)
        self.assertNotIn('Wialon', head)
        self.assertNotIn('3464', head.split('href=')[0])

    def test_the_track_quality_is_words_not_a_code(self):
        html = self._day(_site(quality_flag='razryvy=3;pryzhki=2'),
                         motion_gaps=3, gps_jumps=2)
        self.assertNotIn('razryvy', html)
        self.assertNotIn('pryzhki', html)
        self.assertIn('молчаний на ходу <b class="is-marked">3</b>', html)
        self.assertIn('прыжков координат <b class="is-marked">2</b>', html)

    def test_a_clean_track_is_not_marked(self):
        """Отрицательный контроль к тесту выше: без разрывов цвета нет."""
        html = self._day(_site())
        self.assertIn('молчаний на ходу <b>0</b>', html)
        self.assertIn('прыжков координат <b>0</b>', html)
        self.assertNotIn('is-marked', html)
        self.assertNotIn('чисто', html)

    def test_the_summary_counts_the_sites_without_an_answer(self):
        html = self._day(_site(), _site(number=2, operator_label='работа'))
        self.assertIn('<span class="gps-fact-fig-note is-open">без ответа: 1</span>', html)
        with app.app_context():
            for row in GpsWorkPolygon.query:
                row.operator_label = 'проезд'
            db.session.commit()
        html = self._get()
        self.assertIn('<span class="gps-fact-fig-note">все отвечены</span>', html)
        self.assertNotIn('gps-fact-fig-note is-open', html)

    def test_work_time_is_hours_and_minutes(self):
        html = self._day(_site(minutes=317.4), _site(number=2, minutes=44.6, area=0.5),
                         _site(number=3, minutes=60.0, area=1.0))
        self.assertIn('<div><b>8.77</b> га · 5 ч 17 мин</div>', html)
        self.assertIn('<div><b>0.50</b> га · 45 мин</div>', html)
        self.assertIn('<div><b>1.00</b> га · 1 ч 00 мин</div>', html)

    def test_one_time_is_written_one_way(self):
        self.assertEqual(gps_routes.duration(317.4), '5 ч 17 мин')
        self.assertEqual(gps_routes.duration(44.6), '45 мин')
        self.assertEqual(gps_routes.duration(60.0), '1 ч 00 мин')
        self.assertEqual(gps_routes.duration(None), '0 мин')
        self.assertEqual(gps_routes.duration(317.4, is_ru=False), '5 соат 17 дақ')
        # Подпись участка на карте -- то же время, что в таблице участков.
        html = self._day(_site())
        data = json.loads(re.search(r'id="gps-fact-map">(.*?)</script>', html, re.S).group(1))
        titles = [layer['title'] for layer in data['layers'] if layer['kind'] == 'area']
        self.assertEqual(titles, ['№1 · 8.77 га · 5 ч 17 мин'])
        self.assertIn('<div><b>8.77</b> га · 5 ч 17 мин</div>', html)

    def test_the_suggestion_speaks_the_language_of_the_screen(self):
        html = self._day(_site(suggested='проезд'))
        self.assertIn('программа предлагает: проезд', html)
        with app.app_context():
            User.query.get(self.admin_id).language = 'uz'
            db.session.commit()
        html = self._get()
        self.assertIn('дастур таклифи: ўтиш', html)
        self.assertNotIn('проезд</span>', html)

    def test_answer_buttons_look_like_buttons(self):
        # Прежде неотвеченные кнопки были «призрачными» -- читались как текст.
        html = self._day(_site(), _site(number=2, operator_label='работа'))
        classes = re.findall(r'value="(работа|проезд)"\s+class="([^"]+)"', html)
        self.assertEqual(classes, [
            ('работа', 'vs-btn vs-btn-sm'), ('проезд', 'vs-btn vs-btn-sm'),
            ('работа', 'vs-btn vs-btn-sm vs-btn-success'),
            ('проезд', 'vs-btn vs-btn-sm')])

    def test_the_satellite_how_to_is_folded_for_the_admin(self):
        html = self._day(_site())
        self.assertIn('<div class="vs-soft vs-mt-sm">Спутниковая подложка не '
                      'подключена — показана карта OpenStreetMap.</div>', html)
        start = html.index('<summary>Подключение спутниковых снимков</summary>')
        how_to = html[start:html.index('</details>', start)]
        self.assertIn('esri_api_key.txt', how_to)
        self.assertEqual(html.count('esri_api_key.txt'), how_to.count('esri_api_key.txt'))

    def test_an_employee_sees_the_plain_line_without_the_how_to(self):
        """Отрицательный контроль: пути к файлам -- не для сотрудника."""
        with app.app_context():
            org = Organization.query.get(self.org_id)
            machine = Equipment(name='МТЗ-80.1', plate='80 261 EA',
                                category='mtz', organization_id=org.id)
            db.session.add(machine)
            db.session.flush()
            db.session.add(VialonMapping(vialon_name='obj', wialon_id=3464,
                                         equipment_id=machine.id, skip=False))
            user = User(username='gps_emp', role=ROLE_OPERATOR, full_name='gps_emp',
                        language='ru')
            user.set_password('x')
            user.organizations.append(org)
            db.session.add(user)
            db.session.flush()
            db.session.add(UserModulePermission(user_id=user.id, module_code='wialon',
                                                has_access=True))
            db.session.add(_aggregate())
            db.session.add(_site())
            db.session.commit()
            user_id = user.id
        client = app.test_client()
        login(client, user_id)
        html = client.get('/gps/fact').get_data(as_text=True)
        self.assertIn('МТЗ-80.1', html)
        self.assertIn('Спутниковая подложка не подключена', html)
        self.assertNotIn('Подключение спутниковых снимков', html)
        self.assertNotIn('esri_api_key.txt', html)

    def test_the_stylesheet_uses_only_tokens(self):
        with open(os.path.join(REPO_ROOT, 'static', 'css', 'gps-fact.css'),
                  encoding='utf-8') as fh:
            css = fh.read()
        with open(os.path.join(REPO_ROOT, 'static', 'css', 'design-system.css'),
                  encoding='utf-8') as fh:
            system = fh.read()
        self.assertNotRegex(css, r'#[0-9a-fA-F]{3,8}\b')
        self.assertNotRegex(css, r'\brgba?\(')
        for token in set(re.findall(r'var\((--[a-z0-9-]+)\)', css)):
            with self.subTest(token=token):
                self.assertRegex(system, r'(?<![\w-])' + re.escape(token) + r'\s*:')


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
