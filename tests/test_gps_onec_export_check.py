# -*- coding: utf-8 -*-
"""tools/gps_onec_export_check.py -- приёмка недельной выгрузки 1С (GPS-37).

Держится то, что записано в треке до прогона (§0.6, «Приёмка недельной
выгрузки 1С»):
  * строка -- (пункт, госномер, время до секунды, масса); xlsx соседнего
    чата (время -- серийная дата Excel, госномер реестра серией вперёд)
    и CSV скрипта сравниваются так, как их читают GPS-34/35;
  * строки скрипта по организациям, которых нет у чата, -- отдельным
    числом, не расхождение;
  * MATCH -- ни недостающих, ни лишних строк; DIFFERS -- иначе, расходящиеся
    строки -- в --csv; консоль -- ASCII; неверный ввод -- код 2.

Запуск из корня репозитория:
  python -m unittest tests.test_gps_onec_export_check
"""
import contextlib
import csv
import io
import os
import shutil
import tempfile
import unittest

import tools.gps_onec_export_check as tool
from tests.onec_xlsx import write_xlsx

BUXORO = 'OOO "BUXORO AGROKLASTER"'
MIRZA = 'Мирзачул Агрокластер гроуп МЧЖ'
OTHER = 'Другой завод'
VYVOZ_XLSX = ('Завод', 'Пункт приема', 'Гос номер (из названия)', 'Время первого взвешивания',
              'Вес нетто, кг')
REESTR_XLSX = ('Организация (завод)', 'Пункт приема', 'Транспорт', 'Дата брутто',
               'Масса физическая,кг')
VYVOZ_CSV = ('Завод', 'Пункт приема', 'Транспорт (как в 1С)', 'Гос номер (из названия)',
             'Время первого взвешивания', 'Время второго взвешивания', 'Вес нетто, кг')

# 46314.3240625 -- 19.10.2026 07:46:39 серийной датой Excel
EXPECTED_VYVOZ = [VYVOZ_XLSX,
                  (BUXORO, 'Вобкент ПТМ', '80 283 HA', 46314.3240625, '6620'),
                  (BUXORO, 'Харгуш  ПТМ', '80 240 HA', '25.10.2026 22:10:00', '5320')]
EXPECTED_REESTR = [REESTR_XLSX,
                   (BUXORO, 'Кумушкент ПТМ', '80 KA 368', '20.10.2026 19:16:47', '4840'),
                   (MIRZA, 'Х.Нарзи ПТМ', '80 HA 254', '25.10.2026 20:26:10', '2400'),
                   (MIRZA, 'Х.Нарзи ПТМ', '80 HA 254', '25.10.2026 20:26:10', '2400')]
EXPORT_VYVOZ = [VYVOZ_CSV,
                (BUXORO, 'Вобкент ПТМ', 'МТЗ-80-1 80 283 HA', '80 283 HA', '2026-10-19 07:46:39',
                 '2026-10-19 08:46:56', '6620'),
                (BUXORO, 'Харгуш ПТМ', 'МТЗ-80-1(калхида) 80 240 HA', '80 240 HA',
                 '2026-10-25 22:10:00', '2026-10-25 23:59:59', '5320')]
EXPORT_REESTR = [REESTR_XLSX,
                 (BUXORO, 'Кумушкент ПТМ', '80 KA 368', '2026-10-20 19:16:47', '4840'),
                 (MIRZA, 'Х.Нарзи ПТМ', '80 HA 254', '2026-10-25 20:26:10', '2400'),
                 (MIRZA, 'Х.Нарзи ПТМ', '80 HA 254', '2026-10-25 20:26:10', '2400'),
                 (OTHER, 'Завод олди ПТМ', '80 HA 999', '2026-10-21 10:00:00', '1500')]


def write_csv(path, rows):
    with open(path, 'w', encoding='utf-8-sig', newline='') as handle:
        csv.writer(handle, delimiter=';', quoting=csv.QUOTE_ALL).writerows(rows)


class ExportCheck(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.paths = {name: os.path.join(self.root, name) for name in
                      ('exp_v.xlsx', 'exp_r.xlsx', 'v.csv', 'r.csv', 'diff.csv')}
        write_xlsx(self.paths['exp_v.xlsx'], EXPECTED_VYVOZ)
        write_xlsx(self.paths['exp_r.xlsx'], EXPECTED_REESTR)

    def run_tool(self, vyvoz, reestr, with_csv=True):
        write_csv(self.paths['v.csv'], vyvoz)
        write_csv(self.paths['r.csv'], reestr)
        argv = ['--expected-vyvoz', self.paths['exp_v.xlsx'],
                '--expected-reestr', self.paths['exp_r.xlsx'],
                '--vyvoz', self.paths['v.csv'], '--reestr', self.paths['r.csv']]
        if with_csv:
            argv += ['--csv', self.paths['diff.csv']]
        lines = []
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = tool.main(argv, out=lines.append)
        text = '\n'.join(lines) + err.getvalue()
        self.assertTrue(text.isascii(), text)
        return code, lines, text

    def differences(self):
        with open(self.paths['diff.csv'], encoding='utf-8-sig', newline='') as handle:
            return list(csv.reader(handle, delimiter=';'))

    def test_the_same_rows_match(self):
        # серийная дата и текст времени, двойной пробел в пункте, серия
        # реестра впереди -- всё это одни и те же строки; третья организация
        # скрипта -- не расхождение
        code, lines, text = self.run_tool(EXPORT_VYVOZ, EXPORT_REESTR)
        self.assertEqual(code, tool.EXIT_OK, text)
        self.assertEqual(lines[0], 'vyvoz: expected 2 rows / 11940 kg | export 2 rows / 11940 kg '
                                   '| missing 0 | extra 0 | other organizations in the export: '
                                   '0 (0 rows / 0 kg)')
        self.assertEqual(lines[1], 'reestr: expected 3 rows / 9640 kg | export 3 rows / 9640 kg '
                                   '| missing 0 | extra 0 | other organizations in the export: '
                                   '1 (1 rows / 1500 kg)')
        self.assertEqual(lines[-1], 'VERDICT EXPORT: MATCH')
        self.assertEqual(self.differences(), [list(tool.CSV_COLUMNS)])

    def test_a_missing_duplicate_and_a_changed_mass_differ(self):
        reestr = [EXPORT_REESTR[0], EXPORT_REESTR[1][:4] + ('4841',), EXPORT_REESTR[2],
                  EXPORT_REESTR[4]]
        code, lines, text = self.run_tool(EXPORT_VYVOZ, reestr)
        self.assertEqual(code, tool.EXIT_OK, text)
        self.assertIn('reestr: expected 3 rows / 9640 kg | export 2 rows / 7241 kg '
                      '| missing 2 | extra 1 |', lines[1])
        self.assertEqual(lines[-1], 'VERDICT EXPORT: DIFFERS')
        self.assertEqual(sorted(self.differences()[1:]), sorted([
            ['reestr', 'lishnyaya_v_skripte', 'кумушкент птм', '80368KA', '2026-10-20 19:16:47',
             '4841', BUXORO],
            ['reestr', 'net_v_skripte', 'кумушкент птм', '80368KA', '2026-10-20 19:16:47',
             '4840', BUXORO],
            ['reestr', 'net_v_skripte', 'х.нарзи птм', '80254HA', '2026-10-25 20:26:10',
             '2400', MIRZA]]))

    def test_extra_rows_alone_differ_and_are_listed_each(self):
        # недостающих нет, лишняя строка -- дважды: расхождение, и в --csv
        # она дважды
        extra = (BUXORO, 'Кумушкент ПТМ', '80 KA 368', '2026-10-22 09:00:00', '3000')
        code, lines, text = self.run_tool(EXPORT_VYVOZ, EXPORT_REESTR + [extra, extra])
        self.assertIn('| missing 0 | extra 2 |', lines[1])
        self.assertEqual(lines[-1], 'VERDICT EXPORT: DIFFERS')
        self.assertEqual(self.differences()[1:], [
            ['reestr', 'lishnyaya_v_skripte', 'кумушкент птм', '80368KA', '2026-10-22 09:00:00',
             '3000', BUXORO]] * 2)

    def test_a_shifted_time_or_another_plate_differs(self):
        for column, value in ((4, '2026-10-19 07:46:40'), (3, '80 284 HA')):
            with self.subTest(column=column):
                row = list(EXPORT_VYVOZ[1])
                row[column] = value
                code, lines, text = self.run_tool([EXPORT_VYVOZ[0], tuple(row), EXPORT_VYVOZ[2]],
                                                  EXPORT_REESTR, with_csv=False)
                self.assertIn('| missing 1 | extra 1 |', lines[0])
                self.assertEqual(lines[-1], 'VERDICT EXPORT: DIFFERS')

    def test_bad_input_is_code_two(self):
        write_csv(self.paths['v.csv'], EXPORT_VYVOZ)
        write_csv(self.paths['r.csv'], EXPORT_REESTR)
        for changes in ({'--vyvoz': os.path.join(self.root, 'nope.csv')},
                        {'--csv': self.paths['v.csv']},
                        {'--csv': os.path.join(self.root, 'nodir', 'd.csv')}):
            with self.subTest(changes=changes):
                args = {'--expected-vyvoz': self.paths['exp_v.xlsx'],
                        '--expected-reestr': self.paths['exp_r.xlsx'],
                        '--vyvoz': self.paths['v.csv'], '--reestr': self.paths['r.csv'],
                        '--csv': self.paths['diff.csv']}
                args.update(changes)
                argv = [item for pair in args.items() for item in pair]
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    code = tool.main(argv, out=lambda line: None)
                self.assertEqual(code, tool.EXIT_BAD_INPUT, err.getvalue())


if __name__ == '__main__':
    unittest.main()
