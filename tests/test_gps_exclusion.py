# -*- coding: utf-8 -*-
"""GPS-12: какие объекты план-факт не считает, и одинаково ли это в двух местах.

Что проверяется и почему именно это:

1. **Исключает только явное.** `skip = 1` со стоящим `wialon_id` либо машина в
   непольевой категории. `skip NULL`, объект без строки сопоставления, строка
   без `wialon_id` — не исключают ничего. Две ошибки не равны: посчитанный
   лишний объект виден на экране, исключённый по ошибке теряет работу машины
   молча.

2. **Противоречие не решается за человека.** Если у одного id одна строка
   говорит «не наша», а другая — нет; если одна ведёт на легковую, а другая на
   трактор, — объект остаётся в расчёте, и инвентарь называет это
   `protivorechie`.

3. **Легковая исключается категорией, а НЕ `skip`.** Это главный контроль
   этого файла: `skip` выключает машину ещё и из импорта моточасов
   (`wialon_import.apply_mappings` кладёт помеченные строки в `skipped`), то
   есть из учёта соседнего трека. Тест держит границу: после исключения
   легковой её строка сопоставления обязана остаться непомеченной.

4. **Слаг категории совпадает с `models.CATEGORIES`.** Значение продублировано
   в `gps/exclusion.py`, потому что `models.py` тянет Flask, а `gps/` его не
   импортирует. Дубль без этого теста разъехался бы при переименовании молча.

5. **Две реализации отвечают одно и то же.** Расчёт считает правило через
   stdlib `sqlite3` (`gps.exclusion`), экран — через SQLAlchemy
   (`gps_routes._excluded_units`), и иначе нельзя: служба Flask не тянет стек
   numpy/shapely, а `gps/` не тянет `models`. Здесь обе гоняются по одной и
   той же базе и сверяются между собой — расхождение означало бы машину,
   исчезнувшую из расчёта, но оставшуюся на экране.

Запуск:
  python -m unittest tests.test_gps_exclusion -v
"""
import os
import sqlite3
import sys
import tempfile
import unittest

from datetime import date, datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tests.harness import app, db, reset_db, create_admin, create_org, login
from models import (CAT_MOTORCYCLE, CAT_MTZ, CAT_PASSENGER, CAT_SPECIAL,
                    CAT_YUK_TRANSPORT, CATEGORIES, Equipment,
                    GpsDailyAggregate, User, VialonMapping)

import gps_routes                                                   # noqa: E402
from gps.exclusion import (EXCLUDED_NON_FIELD, EXCLUDED_NOT_OURS,   # noqa: E402
                           NON_FIELD_CATEGORIES, REASON_TRACK_ONLY,
                           TRACK_ONLY_CATEGORIES, excluded_units,
                           track_only_units)

DDL = (
    'CREATE TABLE vialon_mappings (id INTEGER PRIMARY KEY AUTOINCREMENT, '
    'vialon_name VARCHAR(300) NOT NULL UNIQUE, wialon_id INTEGER, '
    'equipment_id INTEGER, skip BOOLEAN, created_by INTEGER, '
    'created_at DATETIME, updated_at DATETIME)',
    'CREATE TABLE equipment (id INTEGER PRIMARY KEY AUTOINCREMENT, '
    'name VARCHAR(200) NOT NULL, plate VARCHAR(50), '
    'category VARCHAR(20) NOT NULL, eq_type VARCHAR(100), '
    'organization_id INTEGER NOT NULL, default_price FLOAT, '
    'default_unit VARCHAR(30), is_active BOOLEAN, model_id INTEGER)',
)


class Rule(unittest.TestCase):
    """Пункты 1 и 2, на голой базе без Flask."""

    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.db = os.path.join(self.folder, 'transport.db')
        self.con = sqlite3.connect(self.db)
        for statement in DDL:
            self.con.execute(statement)
        self.con.commit()

    def tearDown(self):
        self.con.close()

    def equipment(self, name, category):
        cursor = self.con.execute(
            'INSERT INTO equipment (name, category, organization_id, is_active) '
            'VALUES (?, ?, 1, 1)', (name, category))
        self.con.commit()
        return cursor.lastrowid

    def mapping(self, name, wialon_id=None, equipment_id=None, skip=0):
        cursor = self.con.execute(
            'INSERT INTO vialon_mappings (vialon_name, wialon_id, equipment_id, '
            'skip) VALUES (?, ?, ?, ?)', (name, wialon_id, equipment_id, skip))
        self.con.commit()
        return cursor.lastrowid

    def test_an_empty_database_excludes_nothing(self):
        self.assertEqual(excluded_units(self.con), {})

    def test_no_mapping_table_at_all_excludes_nothing(self):
        bare = sqlite3.connect(':memory:')
        try:
            self.assertEqual(excluded_units(bare), {})
        finally:
            bare.close()

    def test_a_marked_row_with_an_id_is_excluded(self):
        self.mapping('Chuzhoy kran', wialon_id=5001, skip=1)
        self.assertEqual(excluded_units(self.con),
                         {5001: EXCLUDED_NOT_OURS})

    def test_a_marked_row_without_an_id_excludes_nothing(self):
        # [REASON]: экран сопоставления `wialon_id` не заполняет вовсе, поэтому
        # помеченная руками строка почти всегда без id. Исключать по имени
        # нельзя: расчёт работает по id, и «по имени похоже» -- это угадывание.
        self.mapping('Chuzhoy kran', wialon_id=None, skip=1)
        self.assertEqual(excluded_units(self.con), {})

    def test_skip_null_excludes_nothing(self):
        # Колонку добавляли миграцией к уже существующим строкам: NULL там
        # значит «никто не решал», а не «не наша».
        self.con.execute('INSERT INTO vialon_mappings (vialon_name, wialon_id, '
                         'skip) VALUES (?, ?, NULL)', ('MTZ 292 HA', 387))
        self.con.commit()
        self.assertEqual(excluded_units(self.con), {})

    def test_a_passenger_car_is_excluded_by_its_category(self):
        car = self.equipment('Nexia', CAT_PASSENGER)
        self.mapping('Nexia 80 123 ABA', wialon_id=6001, equipment_id=car)
        self.assertEqual(excluded_units(self.con),
                         {6001: EXCLUDED_NON_FIELD})

    def test_excluding_a_passenger_car_does_not_mark_it_as_not_ours(self):
        """Пункт 3: главный контроль границы с импортом моточасов."""
        car = self.equipment('Nexia', CAT_PASSENGER)
        row = self.mapping('Nexia 80 123 ABA', wialon_id=6001, equipment_id=car)
        self.assertEqual(excluded_units(self.con)[6001], EXCLUDED_NON_FIELD)
        skip, equipment_id = self.con.execute(
            'SELECT skip, equipment_id FROM vialon_mappings WHERE id = ?',
            (row,)).fetchone()
        # Не помечена и не отвязана: моточасы этой машины по-прежнему
        # импортируются, потому что машина наша -- она просто не пашет.
        self.assertEqual(skip, 0)
        self.assertEqual(equipment_id, car)

    def test_a_tractor_is_not_excluded(self):
        tractor = self.equipment('MTZ 892', CAT_MTZ)
        self.mapping('MTZ 292 HA', wialon_id=387, equipment_id=tractor)
        self.assertEqual(excluded_units(self.con), {})

    def test_a_row_without_equipment_is_not_excluded(self):
        self.mapping('Neizvestnyy obekt', wialon_id=7001)
        self.assertEqual(excluded_units(self.con), {})

    def test_two_rows_disagreeing_about_skip_keep_the_object(self):
        """Пункт 2: противоречие не решается за человека."""
        self.mapping('Staryy treker', wialon_id=8001, skip=1)
        self.mapping('Novyy treker', wialon_id=8001, skip=0)
        self.assertEqual(excluded_units(self.con), {})

    def test_two_rows_disagreeing_about_the_category_keep_the_object(self):
        car = self.equipment('Nexia', CAT_PASSENGER)
        tractor = self.equipment('MTZ 892', CAT_MTZ)
        self.mapping('Odno imya', wialon_id=9001, equipment_id=car)
        self.mapping('Drugoe imya', wialon_id=9001, equipment_id=tractor)
        self.assertEqual(excluded_units(self.con), {})

    def test_a_marked_row_wins_over_the_category_in_the_reason(self):
        # Объект исключён и так и так; причина называется человеческая.
        car = self.equipment('Nexia', CAT_PASSENGER)
        self.mapping('Nexia 80 123 ABA', wialon_id=6001, equipment_id=car,
                     skip=1)
        self.assertEqual(excluded_units(self.con),
                         {6001: EXCLUDED_NOT_OURS})


class TrackOnly(Rule):
    """A1: спецтехника -- след без гектаров. То же правило явного решения.

    Наследует базу Rule; тесты Rule при этом прогоняются второй раз -- так же,
    как в gps/tests/test_daily.py, и это дешевле второй копии фикстуры.
    """

    def test_a_special_machine_is_track_only_and_not_excluded(self):
        loader = self.equipment('Pogruzchik', CAT_SPECIAL)
        row = self.mapping('Pogruzchik 373 HA', wialon_id=4001,
                           equipment_id=loader)
        self.assertEqual(track_only_units(self.con), {4001})
        # «без гектаров» -- не «исключён»: объект считается, моточасы целы
        self.assertEqual(excluded_units(self.con), {})
        skip, equipment_id = self.con.execute(
            'SELECT skip, equipment_id FROM vialon_mappings WHERE id = ?',
            (row,)).fetchone()
        self.assertEqual((skip, equipment_id), (0, loader))

    def test_an_empty_database_and_a_bare_one_give_nothing(self):
        self.assertEqual(track_only_units(self.con), set())
        bare = sqlite3.connect(':memory:')
        try:
            self.assertEqual(track_only_units(bare), set())
        finally:
            bare.close()

    def test_a_tractor_and_a_row_without_a_machine_are_not_track_only(self):
        tractor = self.equipment('MTZ 892', CAT_MTZ)
        self.mapping('MTZ 292 HA', wialon_id=387, equipment_id=tractor)
        self.mapping('Neizvestnyy obekt', wialon_id=7001)
        self.assertEqual(track_only_units(self.con), set())

    def test_a_special_row_next_to_a_field_row_keeps_the_hectares(self):
        """Противоречие не решается за человека: гектары остаются."""
        loader = self.equipment('Pogruzchik', CAT_SPECIAL)
        tractor = self.equipment('MTZ 892', CAT_MTZ)
        self.mapping('Staryy treker', wialon_id=4001, equipment_id=loader)
        self.mapping('Novyy treker', wialon_id=4001, equipment_id=tractor)
        self.assertEqual(track_only_units(self.con), set())

    def test_a_row_without_a_machine_does_not_undo_the_category(self):
        # Строка без машины о категории не говорит ничего: второй трекер той же
        # машины без привязки не должен возвращать погрузчику гектары.
        loader = self.equipment('Pogruzchik', CAT_SPECIAL)
        self.mapping('Pogruzchik 373 HA', wialon_id=4001, equipment_id=loader)
        self.mapping('Pogruzchik bez privyazki', wialon_id=4001)
        self.assertEqual(track_only_units(self.con), {4001})


class TrackOnlySlug(unittest.TestCase):
    """Пункт 4 для спецтехники: дубль слага закреплён против models.py.

    [REASON]: отдельный класс, а не продолжение CategorySlug ниже: второе
    определение класса с тем же именем молча заслонило бы первое, и его тесты
    перестали бы запускаться без единой ошибки.
    """

    def test_the_track_only_slug_is_the_one_models_uses(self):
        self.assertEqual(TRACK_ONLY_CATEGORIES, frozenset({CAT_SPECIAL}))
        self.assertTrue(TRACK_ONLY_CATEGORIES.issubset(set(CATEGORIES)))

    def test_track_only_and_excluded_never_share_a_category(self):
        # Категория в обоих множествах дала бы объект, который одновременно
        # исключён и считается со следом, -- и какой ответ победит, решал бы
        # порядок проверок, а не владелец.
        self.assertEqual(TRACK_ONLY_CATEGORIES & NON_FIELD_CATEGORIES,
                         frozenset())

    def test_the_screen_knows_the_reason_word(self):
        # Слово пишет расчёт, подпись к нему -- экран. Без подписи экран
        # показал бы человеку «spetstekhnika».
        self.assertEqual(REASON_TRACK_ONLY, 'spetstekhnika')
        self.assertIn(REASON_TRACK_ONLY, gps_routes.REASON_LABELS)
        ru, uz = gps_routes.REASON_LABELS[REASON_TRACK_ONLY]
        self.assertIn('Спецтехника', ru)
        self.assertIn('Махсус техника', uz)


class CategorySlug(unittest.TestCase):
    """Пункт 4: дубль слага закреплён против models.py."""

    def test_a_truck_is_excluded_and_keeps_its_machine_and_mothours(self):
        """Главный контроль решения 28.09 про грузовые.

        [REASON]: молоковоз HYUNDAI 80 555 UBA дал 354,4 га за 13 суток при
        4287 км пробега -- крупнейший ложный гектар парка. Убирать его надо
        КАТЕГОРИЕЙ, а не галочкой «нет в системе»: владельцу нужна связь
        объекта с карточкой машины под будущий отчёт по пробегу с геозонами, а
        галочка эту связь обнуляет. Тест держит обе стороны сразу.
        """
        rule = Rule("test_an_empty_database_excludes_nothing")
        rule.setUp()
        try:
            truck = rule.equipment("Hyundai", CAT_YUK_TRANSPORT)
            row = rule.mapping("HYUNDAI 80 555 UBA", wialon_id=8780,
                               equipment_id=truck)
            self.assertEqual(excluded_units(rule.con),
                             {8780: EXCLUDED_NON_FIELD})
            skip, equipment_id = rule.con.execute(
                "SELECT skip, equipment_id FROM vialon_mappings WHERE id = ?",
                (row,)).fetchone()
            self.assertEqual(skip, 0)          # моточасы пишутся как прежде
            self.assertEqual(equipment_id, truck)   # связь с машиной цела
        finally:
            rule.tearDown()

    def test_the_non_field_slugs_are_the_ones_models_uses(self):
        self.assertEqual(NON_FIELD_CATEGORIES,
                         frozenset({CAT_PASSENGER, CAT_YUK_TRANSPORT}))

    def test_every_non_field_slug_is_a_real_category(self):
        self.assertTrue(NON_FIELD_CATEGORIES.issubset(set(CATEGORIES)))

    def test_the_screen_and_the_engine_name_the_same_categories(self):
        self.assertEqual(gps_routes.NON_FIELD_CATEGORIES, NON_FIELD_CATEGORIES)

    def test_only_the_two_categories_the_owner_named_are_excluded(self):
        """Заменяет test_motorcycles_and_trucks_are_deliberately_not_excluded.

        [REASON]: растяжка сработала ровно как задумано. 28.09 владелец решил
        внести грузовые -- прежний тест упал и потребовал внести их СОЗНАТЕЛЬНО,
        а не заметить через месяц по пропавшим гектарам. Растяжка остаётся на
        месте, только с новым числом: мотоциклы (8) и спецтехника (6) не
        внесены, и следующее их внесение снова придётся сделать руками.
        """
        self.assertEqual(NON_FIELD_CATEGORIES,
                         frozenset({CAT_PASSENGER, CAT_YUK_TRANSPORT}))
        self.assertNotIn(CAT_SPECIAL, NON_FIELD_CATEGORIES)
        self.assertNotIn(CAT_MOTORCYCLE, NON_FIELD_CATEGORIES)


class TwoImplementationsAgree(unittest.TestCase):
    """Пункт 5: stdlib-правило и запрос экрана дают одно множество.

    [REASON]: расхождение этих двух ответов -- самый неприятный из возможных
    исходов инкремента: машина исчезает из расчёта, но остаётся на экране
    пустой строкой, либо наоборот -- считается, но человеку не показана. Ни
    один другой контроль этого не увидит: у каждой реализации свои тесты, и
    оба зелёные.
    """

    DAY = date(2026, 9, 20)

    def setUp(self):
        reset_db()
        self.admin_id = create_admin()
        self.org_id = create_org()
        with app.app_context():
            User.query.get(self.admin_id).language = 'ru'
            db.session.commit()

    def _build(self):
        """Один и тот же набор: легковая, трактор, помеченный, спорный, голый."""
        with app.app_context():
            car = Equipment(name='Nexia', plate='80 123 ABA',
                            category=CAT_PASSENGER, organization_id=self.org_id)
            tractor = Equipment(name='MTZ 892', plate='80 292 HA',
                                category=CAT_MTZ, organization_id=self.org_id)
            db.session.add_all([car, tractor])
            db.session.commit()
            db.session.add_all([
                VialonMapping(vialon_name='Nexia 80 123 ABA', wialon_id=6001,
                              equipment_id=car.id, skip=False),
                VialonMapping(vialon_name='MTZ 292 HA', wialon_id=387,
                              equipment_id=tractor.id, skip=False),
                VialonMapping(vialon_name='Chuzhoy kran', wialon_id=5001,
                              skip=True),
                VialonMapping(vialon_name='Staryy treker', wialon_id=8001,
                              skip=True),
                VialonMapping(vialon_name='Novyy treker', wialon_id=8001,
                              skip=False),
                VialonMapping(vialon_name='Bez mashiny', wialon_id=7001,
                              skip=False),
                VialonMapping(vialon_name='Pomechen bez id', skip=True),
            ])
            for unit_id in (6001, 387, 5001, 8001, 7001):
                db.session.add(GpsDailyAggregate(
                    work_date=self.DAY, wialon_id=unit_id, points_total=100,
                    points_work=90, track_km=1.0, interval_median_s=30.0,
                    sats_median=12.0, motion_gaps=0, lost_seconds=0.0,
                    gps_jumps=0, reason=None,
                    method_version='adaptive-alpha-2026-08-12',
                    computed_at=datetime(2026, 9, 21, 3, 0, 0)))
            db.session.commit()

    def _engine_answer(self):
        """Тот же ответ, но stdlib-правилом, по файлу боевой схемы."""
        with app.app_context():
            path = db.engine.url.database
        con = sqlite3.connect(path, timeout=30)
        try:
            return excluded_units(con)
        finally:
            con.close()

    def test_both_implementations_return_the_same_set(self):
        self._build()
        with app.app_context():
            screen = gps_routes._excluded_units()
        engine = self._engine_answer()
        self.assertEqual(screen, set(engine))
        # И это множество -- ровно легковая и помеченный со стоящим id.
        self.assertEqual(screen, {6001, 5001})
        self.assertEqual(engine, {6001: EXCLUDED_NON_FIELD,
                                  5001: EXCLUDED_NOT_OURS})

    def test_the_screen_hides_the_excluded_and_keeps_the_rest(self):
        self._build()
        client = app.test_client()
        login(client, self.admin_id)
        page = client.get('/gps/fact?date=2026-09-20').data.decode('utf-8')
        self.assertIn('MTZ 892', page)          # трактор на месте
        self.assertNotIn('Nexia', page)         # легковая ушла
        self.assertNotIn('Chuzhoy kran', page)  # «не наша» ушла
        self.assertIn('8001', page)             # спорный остался
        self.assertIn('7001', page)             # без машины остался

    def test_the_rows_of_the_excluded_stay_in_the_database(self):
        """Ничего не удаляется: экран только перестаёт показывать."""
        self._build()
        client = app.test_client()
        login(client, self.admin_id)
        client.get('/gps/fact?date=2026-09-20')
        with app.app_context():
            self.assertEqual(GpsDailyAggregate.query.count(), 5)
            self.assertIsNotNone(GpsDailyAggregate.query.filter_by(
                wialon_id=6001).first())

    def test_a_day_left_with_only_excluded_objects_is_not_offered(self):
        """Пункт: сутки фильтруются тоже, иначе день открывается пустым."""
        with app.app_context():
            car = Equipment(name='Nexia', category=CAT_PASSENGER,
                            organization_id=self.org_id)
            db.session.add(car)
            db.session.commit()
            db.session.add(VialonMapping(vialon_name='Nexia 80 123 ABA',
                                         wialon_id=6001, equipment_id=car.id,
                                         skip=False))
            db.session.add(GpsDailyAggregate(
                work_date=date(2026, 9, 19), wialon_id=6001, points_total=100,
                points_work=90, track_km=1.0, interval_median_s=30.0,
                sats_median=12.0, motion_gaps=0, lost_seconds=0.0, gps_jumps=0,
                reason=None, method_version='adaptive-alpha-2026-08-12',
                computed_at=datetime(2026, 9, 20, 3, 0, 0)))
            db.session.commit()
        client = app.test_client()
        login(client, self.admin_id)
        page = client.get('/gps/fact').data.decode('utf-8')
        self.assertNotIn('2026-09-19', page)
        self.assertNotIn('19.09.2026', page)


if __name__ == '__main__':
    unittest.main()
