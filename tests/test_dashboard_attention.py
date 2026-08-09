# -*- coding: utf-8 -*-
"""ADAPT-2: дашборд поделён по решению — «требует внимания» перед остальным.

Проверяется ровно то, что решает референс: блок показывает ТОЛЬКО непустые
пункты, каждый ведёт туда, где это чинят, а на чистых данных говорит словами,
что смотреть нечего.

[REASON]: пустое состояние этого блока — не редкость, а нормальный рабочий
день, и без теста оно не проверяется ничем: на фикстурах визуального аудита
предупреждения есть всегда, поэтому кадр «всё в норме» не снимается никогда.

Работает на одноразовой SQLite через общий harness.
"""
import re
import unittest
from datetime import date, datetime, timedelta

from tests.harness import app, db, reset_db, create_admin, create_org, login
from models import DailyRecord, Equipment, FuelSyncLog2, User


ATTENTION_BLOCK = re.compile(
    r'vs-kpi-strip is-attention.*?vs-kpi-strip-body(.*?)</div>\s*</div>', re.S)


class DashboardAttentionTests(unittest.TestCase):
    def setUp(self):
        reset_db()
        self.admin_id = create_admin()
        self.org_id = create_org()
        self.day = date.today() - timedelta(days=1)
        with app.app_context():
            # Язык берётся из учётной записи, а не из адреса; по умолчанию у
            # нового пользователя узбекский, и проверка русских подписей без
            # этой строки сравнивала бы не то.
            User.query.get(self.admin_id).language = 'ru'
            # [REASON]: на пустой базе топливо ЗАКОННО поднимает предупреждение
            # no_sync степени danger — агент Topaz действительно ни разу не
            # синхронизировался. Это не помеха тесту, а верное поведение,
            # поэтому «чистый день» строится с журналом синхронизации, а не
            # затиранием проверки.
            db.session.add(FuelSyncLog2(synced_at=datetime.utcnow(), status='ok',
                                        agent_ip='127.0.0.1'))
            db.session.commit()
        self.client = app.test_client()
        login(self.client, self.admin_id)

    def _block(self):
        resp = self.client.get('/?date=%s' % self.day.isoformat())
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        found = ATTENTION_BLOCK.search(html)
        self.assertIsNotNone(found, 'блок «требует внимания» не отрисован')
        return found.group(1)

    def _add_equipment(self):
        with app.app_context():
            eq = Equipment(name='МТЗ-82.1', plate='01A123AA', category='mtz',
                           organization_id=self.org_id, is_active=True)
            db.session.add(eq)
            db.session.commit()
            return eq.id

    def test_clean_day_says_so_in_words(self):
        block = self._block()
        self.assertIn('Всё в норме', block)
        # Ни одной ссылки: чинить нечего, значит и вести некуда.
        self.assertNotIn('<a class="vs-kpi-strip-item"', block)

    def test_idle_rows_appear_as_a_link(self):
        eq_id = self._add_equipment()
        with app.app_context():
            db.session.add(DailyRecord(work_date=self.day, equipment_id=eq_id,
                                       status='idle', idle_reason='Ремонт',
                                       line_index=0))
            db.session.commit()
        block = self._block()
        self.assertIn('Простой / без работ', block)
        self.assertIn('<a class="vs-kpi-strip-item"', block)
        self.assertNotIn('Всё в норме', block)

    def test_zero_values_never_take_a_slot(self):
        # Рабочая строка — не повод для внимания; блок обязан остаться пустым.
        eq_id = self._add_equipment()
        with app.app_context():
            db.session.add(DailyRecord(work_date=self.day, equipment_id=eq_id,
                                       status='working', work_type='Шудгорлаш',
                                       quantity=5, price=1000, line_index=0))
            db.session.commit()
        block = self._block()
        self.assertIn('Всё в норме', block)
        for absent in ('Простой', 'просрочен', 'Критические'):
            self.assertNotIn(absent, block)

    def test_the_strip_replaced_the_nested_card_grid(self):
        # ADOPT-1: семь карточек с вложенной сеткой 2x2 давали 28 чисел,
        # каждое в своей рамке. Возврат к ним — регрессия, а не вкусовщина.
        html = self.client.get('/').get_data(as_text=True)
        self.assertNotIn('vs-kpi-grid-2', html)
        self.assertIn('vs-kpi-strip-body', html)


if __name__ == '__main__':
    unittest.main()
