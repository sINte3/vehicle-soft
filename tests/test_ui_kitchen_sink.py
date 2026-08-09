# -*- coding: utf-8 -*-
"""UI-P4: служебная страница /__ui/kitchen-sink доступна только в dev.

[REASON]: маршрут показывает все компоненты дизайн-системы и на production не
нужен никому. Проверка держит именно это: страница открывается при
UI_KITCHEN_SINK=True и отдаёт 404 при False — а не «мы её просто не
показываем в меню».
"""
import unittest

from tests.harness import app, reset_db, create_admin, login


class KitchenSinkVisibilityTests(unittest.TestCase):
    def setUp(self):
        reset_db()
        self.admin_id = create_admin()
        self.client = app.test_client()
        login(self.client, self.admin_id)
        self._saved = app.config.get('UI_KITCHEN_SINK')

    def tearDown(self):
        app.config['UI_KITCHEN_SINK'] = self._saved

    def test_open_when_the_flag_is_on(self):
        app.config['UI_KITCHEN_SINK'] = True
        resp = self.client.get('/__ui/kitchen-sink')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        # Страница обязана показывать все три режима таблицы: ради них DD-028
        # и существует, и увидеть их рядом можно только здесь.
        for mode in ('is-static', 'is-stream'):
            self.assertIn(mode, html)
        # И не иметь собственного CSS: компонент, который нельзя показать без
        # локального правила, в системе ещё не собран.
        self.assertNotIn('<style>', html.split('</head>', 1)[-1])

    def test_404_when_the_flag_is_off(self):
        app.config['UI_KITCHEN_SINK'] = False
        self.assertEqual(self.client.get('/__ui/kitchen-sink').status_code, 404)

    def test_production_configs_keep_it_off(self):
        import config
        self.assertFalse(config.ProductionConfig.UI_KITCHEN_SINK)
        self.assertFalse(config.SqliteProductionConfig.UI_KITCHEN_SINK)
        self.assertTrue(config.DevelopmentConfig.UI_KITCHEN_SINK)


if __name__ == '__main__':
    unittest.main()
