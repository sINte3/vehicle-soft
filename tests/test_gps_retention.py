# -*- coding: utf-8 -*-
"""GPS-8: ретенция точек. Проверяется то, что необратимо.

Удаление файла отменить нельзя, поэтому проверок больше, чем кода:

1. **Месяц удаляется, только когда истёк ВЕСЬ.** Файл за июль сносится не
   раньше, чем срок истечёт для 31 июля, а не для 1-го. Счёт «первое число
   плюс 30 дней» промахивается на феврале и на 31-дневных месяцах, и промах
   здесь стоит месяца точек.

2. **Текущий месяц не удаляется никогда,** какой бы срок ни назвали: в него
   прямо сейчас пишет коллектор.

3. **Сухой прогон не удаляет ничего.** Устав запрещает удалять продовые
   данные автоматически; `--apply` человек пишет руками.

4. **Ничего постороннего.** Ни `transport.db`, ни файл watermark, ни файл с
   похожим именем, заведённый кем-то другим.

5. **Спутники WAL уходят вместе с файлом.** Оставленный `-wal` — это не мусор,
   а кусок базы, который SQLite потом попытается применить.

Запуск:
  python -m unittest tests.test_gps_retention -v
"""
import os
import sys
import tempfile
import unittest

from datetime import date

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import tools.gps_retention as retention  # noqa: E402
from gps_collector import storage  # noqa: E402

TODAY = date(2026, 8, 19)


class MonthEnd(unittest.TestCase):
    def test_the_end_of_a_month_is_the_first_of_the_next(self):
        self.assertEqual(retention.month_end(2026, 7), date(2026, 8, 1))
        self.assertEqual(retention.month_end(2026, 2), date(2026, 3, 1))
        self.assertEqual(retention.month_end(2026, 12), date(2027, 1, 1))


class Plan(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()

    def _file(self, name, size=1024, sidecars=False):
        path = os.path.join(self.folder, name)
        with open(path, 'wb') as fh:
            fh.write(b'x' * size)
        if sidecars:
            for suffix in retention.SIDECARS:
                with open(path + suffix, 'wb') as fh:
                    fh.write(b'y')
        return path

    def _months(self, entries):
        return [month for month, _, _ in entries]

    def test_a_month_survives_until_its_last_day_is_old_enough(self):
        # 19.08.2026 минус 90 дней = 21.05.2026. Апрель кончился 01.05 --
        # ему 110 дней, он уходит. Май кончился 01.06 -- ему 79, он остаётся.
        for name in ('gps_points_202604.db', 'gps_points_202605.db',
                     'gps_points_202607.db'):
            self._file(name)
        doomed, kept = retention.plan(self.folder, days=90, today=TODAY)
        self.assertEqual(self._months(doomed), ['202604'])
        self.assertEqual(self._months(kept), ['202605', '202607'])

    def test_the_boundary_is_the_end_of_the_month_not_its_start(self):
        # Май: начало 01.05 старше 90 дней (110), конец 01.06 -- нет (79).
        # Счёт по началу месяца удалил бы его на 31 день раньше срока.
        self._file('gps_points_202605.db')
        doomed, _ = retention.plan(self.folder, days=90, today=TODAY)
        self.assertEqual(doomed, [])
        doomed, _ = retention.plan(self.folder, days=79, today=TODAY)
        self.assertEqual(self._months(doomed), ['202605'])

    def test_the_current_month_is_never_touched(self):
        self._file('gps_points_202608.db')
        doomed, kept = retention.plan(self.folder, days=1, today=TODAY)
        self.assertEqual(doomed, [])
        self.assertEqual(self._months(kept), ['202608'])

    def test_the_current_month_survives_even_an_absurd_retention(self):
        # [REASON]: этот тест написан ПОСЛЕ того, как мутация «убрать проверку
        # текущего месяца» прошла незамеченной. Проверка возраста и так держит
        # текущий месяц при любом разумном сроке -- его возраст отрицательный,
        # месяц ещё не кончился. Отдельная проверка -- второй замок, и он
        # срабатывает только на бессмысленном сроке, который CLI отвергает, но
        # вызывающий код мог бы посчитать сам. Раз замок есть, он обязан быть
        # проверен на том единственном входе, где он работает.
        self._file('gps_points_202608.db')
        doomed, kept = retention.plan(self.folder, days=-100, today=TODAY)
        self.assertEqual(doomed, [])
        self.assertEqual(self._months(kept), ['202608'])

    def test_a_future_month_is_never_touched_either(self):
        # Часы на сервере сбились -- файл будущего месяца не повод его снести.
        self._file('gps_points_202612.db')
        doomed, _ = retention.plan(self.folder, days=1, today=TODAY)
        self.assertEqual(doomed, [])

    def test_nothing_else_in_the_folder_is_a_candidate(self):
        for name in ('transport.db', 'gps_collector_state.db',
                     'gps_points_backup.db', 'gps_points_2026.db',
                     'gps_points_202604.db.bak', 'notes.txt'):
            self._file(name)
        doomed, kept = retention.plan(self.folder, days=1, today=TODAY)
        self.assertEqual(doomed, [])
        self.assertEqual(kept, [])


class Apply(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.old = os.path.join(self.folder, 'gps_points_202601.db')
        for path in (self.old,):
            with open(path, 'wb') as fh:
                fh.write(b'x' * 2048)
            for suffix in retention.SIDECARS:
                with open(path + suffix, 'wb') as fh:
                    fh.write(b'y')
        self.fresh = os.path.join(self.folder, 'gps_points_202608.db')
        with open(self.fresh, 'wb') as fh:
            fh.write(b'z')
        storage.set_watermark(self.folder, 101, 1785148225)

    def _run(self, *argv):
        import io
        out, saved = io.StringIO(), sys.stdout
        sys.stdout = out
        try:
            code = retention.main(['--dir', self.folder] + list(argv))
        finally:
            sys.stdout = saved
        return code, out.getvalue()

    def test_a_dry_run_deletes_nothing(self):
        code, log = self._run()
        self.assertEqual(code, 0)
        self.assertIn('dry run', log)
        self.assertTrue(os.path.exists(self.old))
        for suffix in retention.SIDECARS:
            self.assertTrue(os.path.exists(self.old + suffix))

    def test_apply_removes_the_file_and_its_sidecars(self):
        code, log = self._run('--apply')
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(self.old))
        for suffix in retention.SIDECARS:
            self.assertFalse(os.path.exists(self.old + suffix),
                             'оставленный %s SQLite потом попытается '
                             'применить' % suffix)
        self.assertIn('deleted 3 file(s)', log)

    def test_apply_keeps_everything_it_should(self):
        self._run('--apply')
        self.assertTrue(os.path.exists(self.fresh))
        # watermark обязан пережить удаление месяца -- иначе следующий прогон
        # пойдёт за всей историей по каждому объекту
        self.assertTrue(os.path.exists(storage.state_path(self.folder)))
        self.assertEqual(storage.read_watermark(self.folder, 101), 1785148225)

    def test_zero_days_is_refused_rather_than_obeyed(self):
        code, _ = self._run('--days', '0', '--apply')
        self.assertEqual(code, 2)
        self.assertTrue(os.path.exists(self.old))

    def test_a_missing_folder_is_refused(self):
        code = retention.main(['--dir', os.path.join(self.folder, 'nope')])
        self.assertEqual(code, 2)

    def test_nothing_to_delete_says_so_and_changes_nothing(self):
        code, log = self._run('--days', '3650', '--apply')
        self.assertEqual(code, 0)
        self.assertIn('nothing to delete', log)
        self.assertTrue(os.path.exists(self.old))


if __name__ == '__main__':
    unittest.main()
