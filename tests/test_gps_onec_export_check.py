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
    строки -- в --csv; консоль -- ASCII; неверный ввод -- код 2;
  * дополнение 29.10 (после второго прогона): лишняя строка скрипта с «Датой
    документа» позже времени файла чата (по Ташкенту) -- введена после
    выгрузки чата, считается отдельно; из строк с одним ключом лишние --
    самые поздние; без колонки и для недостающих -- не действует.

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
from datetime import datetime

from gps_collector import config as collector_config

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


# файлы чата записаны 29.10 в 13:20:00 по Ташкенту
CHAT_WRITTEN = datetime(2026, 10, 29, 13, 20, tzinfo=collector_config.TZ).timestamp()
REESTR_DATED = REESTR_XLSX + ('Дата документа',)
EXPORT_REESTR_DATED = [REESTR_DATED] + [row + ('2026-10-26 10:00:00',) for row in EXPORT_REESTR[1:]]


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
        for name in ('exp_v.xlsx', 'exp_r.xlsx'):
            os.utime(self.paths[name], (CHAT_WRITTEN, CHAT_WRITTEN))

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

    @staticmethod
    def line(lines, prefix):
        found = [text for text in lines if text.startswith(prefix)]
        assert len(found) == 1, (prefix, lines)
        return found[0]

    def differences(self):
        with open(self.paths['diff.csv'], encoding='utf-8-sig', newline='') as handle:
            return list(csv.reader(handle, delimiter=';'))

    def test_the_same_rows_match(self):
        # серийная дата и текст времени, двойной пробел в пункте, серия
        # реестра впереди -- всё это одни и те же строки; третья организация
        # скрипта -- не расхождение
        code, lines, text = self.run_tool(EXPORT_VYVOZ, EXPORT_REESTR)
        self.assertEqual(code, tool.EXIT_OK, text)
        # время файла чата -- по Ташкенту, как дата документа 1С
        self.assertEqual(lines[:2], ["vyvoz: the chat's file was written 2026-10-29 13:20:00",
                                     "reestr: the chat's file was written 2026-10-29 13:20:00"])
        self.assertEqual(lines[2], "vyvoz: expected 2 rows / 11940 kg | export 2 rows / 11940 kg "
                                   "| missing 0 | extra 0 | entered after the chat's file 0 "
                                   "| other organizations in the export: 0 (0 rows / 0 kg)")
        self.assertEqual(lines[3], "reestr: expected 3 rows / 9640 kg | export 3 rows / 9640 kg "
                                   "| missing 0 | extra 0 | entered after the chat's file 0 "
                                   "| other organizations in the export: 1 (1 rows / 1500 kg)")
        self.assertEqual(lines[-1], 'VERDICT EXPORT: MATCH')
        self.assertEqual(self.differences(), [list(tool.CSV_COLUMNS)])

    def test_a_missing_duplicate_and_a_changed_mass_differ(self):
        reestr = [EXPORT_REESTR[0], EXPORT_REESTR[1][:4] + ('4841',), EXPORT_REESTR[2],
                  EXPORT_REESTR[4]]
        code, lines, text = self.run_tool(EXPORT_VYVOZ, reestr)
        self.assertEqual(code, tool.EXIT_OK, text)
        self.assertIn('reestr: expected 3 rows / 9640 kg | export 2 rows / 7241 kg '
                      '| missing 2 | extra 1 |', self.line(lines, 'reestr: expected'))
        self.assertEqual(lines[-1], 'VERDICT EXPORT: DIFFERS')
        self.assertEqual(sorted(self.differences()[1:]), sorted([
            ['reestr', 'lishnyaya_v_skripte', 'кумушкент птм', '80368KA', '2026-10-20 19:16:47',
             '4841', BUXORO, ''],
            ['reestr', 'net_v_skripte', 'кумушкент птм', '80368KA', '2026-10-20 19:16:47',
             '4840', BUXORO, ''],
            ['reestr', 'net_v_skripte', 'х.нарзи птм', '80254HA', '2026-10-25 20:26:10',
             '2400', MIRZA, '']]))

    def test_extra_rows_alone_differ_and_are_listed_each(self):
        # недостающих нет, лишняя строка -- дважды: расхождение, и в --csv
        # она дважды
        # без колонки «Дата документа» ни одна строка не считается введённой
        # после выгрузки чата
        extra = (BUXORO, 'Кумушкент ПТМ', '80 KA 368', '2026-10-22 09:00:00', '3000')
        code, lines, text = self.run_tool(EXPORT_VYVOZ, EXPORT_REESTR + [extra, extra])
        self.assertIn("| missing 0 | extra 2 | entered after the chat's file 0 |",
                      self.line(lines, 'reestr: expected'))
        self.assertEqual(lines[-1], 'VERDICT EXPORT: DIFFERS')
        self.assertEqual(self.differences()[1:], [
            ['reestr', 'lishnyaya_v_skripte', 'кумушкент птм', '80368KA', '2026-10-22 09:00:00',
             '3000', BUXORO, '']] * 2)

    def test_rows_entered_after_the_chats_file_are_counted_apart(self):
        # вторая строка с ключом, который у чата один раз, и новая строка --
        # обе датированы позже файла чата: отдельно, расхождения нет. Из двух
        # строк с одним ключом лишняя -- более поздняя
        twin = EXPORT_REESTR[1] + ('2026-10-29 14:07:44',)
        dated = list(EXPORT_REESTR_DATED)
        dated[1] = EXPORT_REESTR[1] + ('2026-10-20 19:30:00',)
        new = (BUXORO, 'Кумушкент ПТМ', '80 KA 368', '2026-10-25 17:39:35', '218',
               '2026-10-29 14:11:44')
        # двойник -- ниже своей пары: лишней берётся самая поздняя по дате
        # документа, а не первая в файле
        code, lines, text = self.run_tool(EXPORT_VYVOZ, dated[:2] + [twin] + dated[2:] + [new])
        self.assertEqual(code, tool.EXIT_OK, text)
        self.assertEqual(self.line(lines, 'reestr: expected'),
                         "reestr: expected 3 rows / 9640 kg | export 5 rows / 14698 kg "
                         "| missing 0 | extra 0 | entered after the chat's file 2 "
                         "| other organizations in the export: 1 (1 rows / 1500 kg)")
        self.assertEqual(lines[-1], 'VERDICT EXPORT: MATCH')
        self.assertEqual(sorted(self.differences()[1:]), sorted([
            ['reestr', 'vvedena_posle_fayla_chata', 'кумушкент птм', '80368KA',
             '2026-10-20 19:16:47', '4840', BUXORO, '2026-10-29 14:07:44'],
            ['reestr', 'vvedena_posle_fayla_chata', 'кумушкент птм', '80368KA',
             '2026-10-25 17:39:35', '218', BUXORO, '2026-10-29 14:11:44']]))

    def test_an_extra_row_dated_before_the_chats_file_still_differs(self):
        # дата документа раньше файла чата, ровно его время или пустая --
        # обычная лишняя строка
        for stamp in ('2026-10-29 13:19:59', '2026-10-29 13:20:00', ''):
            with self.subTest(stamp=stamp):
                extra = (BUXORO, 'Кумушкент ПТМ', '80 KA 368', '2026-10-22 09:00:00', '3000',
                         stamp)
                code, lines, text = self.run_tool(EXPORT_VYVOZ, EXPORT_REESTR_DATED + [extra])
                self.assertIn("| missing 0 | extra 1 | entered after the chat's file 0 |",
                              self.line(lines, 'reestr: expected'))
                self.assertEqual(lines[-1], 'VERDICT EXPORT: DIFFERS')
                self.assertEqual(self.differences()[1][1], 'lishnyaya_v_skripte')

    def test_one_key_can_hold_a_late_and_a_real_extra_row(self):
        # у чата строка один раз, у скрипта трижды: одна -- пара, одна
        # датирована позже файла чата, третья -- раньше: она остаётся лишней
        early = EXPORT_REESTR[1] + ('2026-10-21 08:00:00',)
        late = EXPORT_REESTR[1] + ('2026-10-29 14:07:44',)
        code, lines, text = self.run_tool(EXPORT_VYVOZ, EXPORT_REESTR_DATED + [early, late])
        self.assertIn("| missing 0 | extra 1 | entered after the chat's file 1 |",
                      self.line(lines, 'reestr: expected'))
        self.assertEqual(lines[-1], 'VERDICT EXPORT: DIFFERS')
        self.assertEqual(sorted(row[1] + ' ' + row[7] for row in self.differences()[1:]), [
            'lishnyaya_v_skripte 2026-10-26 10:00:00',
            'vvedena_posle_fayla_chata 2026-10-29 14:07:44'])

    def test_a_row_without_a_date_is_the_chats_pair_of_a_late_one(self):
        # строка, датированная позже файла чата, у чата быть не могла: его
        # парой считается строка без даты
        undated = EXPORT_REESTR[1] + ('',)
        late = EXPORT_REESTR[1] + ('2026-10-29 14:07:44',)
        rows = [REESTR_DATED, undated, late] + EXPORT_REESTR_DATED[2:]
        code, lines, text = self.run_tool(EXPORT_VYVOZ, rows)
        self.assertIn("| missing 0 | extra 0 | entered after the chat's file 1 |",
                      self.line(lines, 'reestr: expected'))
        self.assertEqual(lines[-1], 'VERDICT EXPORT: MATCH')

    def test_a_missing_row_differs_even_beside_late_ones(self):
        # недостающая строка не прощается ничем
        late = (BUXORO, 'Кумушкент ПТМ', '80 KA 368', '2026-10-25 17:39:35', '218',
                '2026-10-29 14:11:44')
        code, lines, text = self.run_tool(EXPORT_VYVOZ, EXPORT_REESTR_DATED[:3] + [late])
        self.assertIn("| missing 1 | extra 0 | entered after the chat's file 1 |",
                      self.line(lines, 'reestr: expected'))
        self.assertEqual(lines[-1], 'VERDICT EXPORT: DIFFERS')

    def test_a_shifted_time_or_another_plate_differs(self):
        for column, value in ((4, '2026-10-19 07:46:40'), (3, '80 284 HA')):
            with self.subTest(column=column):
                row = list(EXPORT_VYVOZ[1])
                row[column] = value
                code, lines, text = self.run_tool([EXPORT_VYVOZ[0], tuple(row), EXPORT_VYVOZ[2]],
                                                  EXPORT_REESTR, with_csv=False)
                self.assertIn('| missing 1 | extra 1 |', self.line(lines, 'vyvoz: expected'))
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
