# -*- coding: utf-8 -*-
"""DJI-AREA-SIMPLIFY-001: устойчивая идентичность машины против идентичности
из доказательства (impl-4).

Сентябрьский holdout поймал дефект, который синтетика прежде не ловила: на
площадке 643 вылета имели карточку и получили `1581F...` (серийный номер
полётного контроллера), а 3980 -- `64TBL...` (код корпуса из паспорта машины)
либо ничего. Один борт распался на две хронологические группы, цепочки
`база -> мостик -> цель` порвались на границе доказательства, и структурный
экран не нашёл ни одного кандидата из 233 запланированных.

Каждая проверка здесь построена как РАСХОЖДЕНИЕ: у записей одной машины
`hardware_id` НАМЕРЕННО разный, и тест требует, чтобы `chronology_key`
остался одним. Проверка, в которой идентичности совпадают, прошла бы и на
старом коде и ничего бы не значила.

Замороженное правило `structural.py` здесь не участвует ни одной строкой --
оно получает уже сгруппированные записи. Меняется только то, чем определяется
«один и тот же борт».

Stdlib sqlite3, без Flask и без приложения.
"""

import json
import os
import re
import sqlite3
import sys
import tempfile
import shutil
import unittest
from datetime import date, datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from dji_area import pipeline as pl  # noqa: E402
from dji_area import resolver as rs  # noqa: E402
from dji_area import store  # noqa: E402
from tests.test_dji_area_core import frame, v4_bytes  # noqa: E402

MIGRATION = os.path.join(REPO_ROOT, 'migrate_dji_area_evidence_001.py')

BODY_CODE = '64TBL-BODY-CODE-NOT-REAL'        # паспорт машины
FC_SN = '1581F-FLIGHT-CONTROLLER-NOT-REAL'    # карточка вылета
UNRELIABLE_FC = '1581F5742255T0C1L061'        # из KNOWN_UNRELIABLE (3 Gijduvon)
NICK = 'SYNTHETIC-3'
MU = 2000.0 / 3.0

BASE, BRIDGE, TARGET = 940001, 940002, 940003
LONE = 940010
DAY = date(2026, 9, 2)

# (id, начало, конец, режим, ширина, RAW м²) -- цепочка с нулевыми зазорами.
CHAIN = (
    (BASE, '2026-09-02 10:00:00', '2026-09-02 10:07:00', 4, 6.0, 10000.0),
    (BRIDGE, '2026-09-02 10:07:00', '2026-09-02 10:07:20', 1, None, 500.0),
    (TARGET, '2026-09-02 10:07:20', '2026-09-02 10:08:30', 4, None, 10000.0),
    (LONE, '2026-09-02 11:00:00', '2026-09-02 11:08:00', 4, 6.0, 9000.0),
)
BY_ID = {row[0]: row for row in CHAIN}


def ts(text):
    return int((datetime.strptime(text, '%Y-%m-%d %H:%M:%S')
                - datetime(1970, 1, 1)).total_seconds())


def _ddl():
    with open(MIGRATION, encoding='utf-8') as fh:
        text = fh.read()
    return re.findall(r'CREATE TABLE IF NOT EXISTS \w+ \(.*?\n    \)', text,
                      re.S)


def card_body(flight_id, hardware_id):
    row = BY_ID[flight_id]
    return json.dumps({'code': 0, 'data': {
        'id': flight_id, 'hardware_id': hardware_id,
        'new_work_area': row[5], 'mode_name': row[3],
        'manual_mode': row[3] != 4, 'spray_width': row[4],
        'start_timestamp': ts(row[1]), 'end_timestamp': ts(row[2]),
    }}, ensure_ascii=False).encode('utf-8')


class Fixture(object):
    """База с одной машиной; карточку получает только часть вылетов."""

    def __init__(self, unit_hardware=BODY_CODE, with_unit=True, nickname=NICK):
        self.tmp = tempfile.mkdtemp(prefix='identity_')
        self.db = os.path.join(self.tmp, 'instance', 'test.db')
        os.makedirs(os.path.dirname(self.db))
        con = sqlite3.connect(self.db)
        con.execute('CREATE TABLE drone_units (id INTEGER PRIMARY KEY, '
                    'hardware_id TEXT)')
        con.execute('CREATE TABLE drone_flights (id INTEGER PRIMARY KEY, '
                    'dji_flight_id BIGINT UNIQUE, started_at TEXT, '
                    'finished_at TEXT, raw_json TEXT, drone_unit_id INTEGER, '
                    'nickname_raw TEXT)')
        for stmt in _ddl():
            con.execute(stmt)
        if with_unit:
            con.execute('INSERT INTO drone_units VALUES (1, ?)',
                        (unit_hardware,))
        for fid, start, end, mode, width, raw in CHAIN:
            record = {'id': fid, 'new_work_area': raw, 'mode_name': mode,
                      'manual_mode': mode != 4, 'spray_width': width,
                      'start_timestamp': ts(start), 'end_timestamp': ts(end),
                      'nickname': nickname}
            con.execute(
                'INSERT INTO drone_flights (dji_flight_id, started_at, '
                'finished_at, raw_json, drone_unit_id, nickname_raw) '
                'VALUES (?,?,?,?,?,?)',
                (fid, start, end, json.dumps(record),
                 1 if with_unit else None, nickname))
        con.commit()
        con.close()

    def add_card(self, flight_id, hardware_id=FC_SN):
        con = store.connect(self.db)
        root = store.source_root(os.path.abspath(self.db))
        store.begin_immediate(con)
        store.upsert_source_revision(con, root, 'card',
                                     card_body(flight_id, hardware_id),
                                     flight_id=flight_id)
        store.refresh_flight_evidence(con, root, flight_id)
        con.execute('COMMIT')
        con.close()

    def add_v4(self, flight_id, first_mu, last_mu, spray=False):
        row = BY_ID[flight_id]
        t0, t1 = ts(row[1]), ts(row[2])
        n = t1 - t0
        frames = [frame((t0 + i) * 1000,
                        area=first_mu + (last_mu - first_mu) * i / float(n),
                        spray_flag=1 if spray else None,
                        flow=100 if spray else None)
                  for i in range(n + 1)]
        con = store.connect(self.db)
        root = store.source_root(os.path.abspath(self.db))
        store.begin_immediate(con)
        store.upsert_source_revision(con, root, 'v4', v4_bytes(frames),
                                     flight_id=flight_id)
        store.refresh_flight_evidence(con, root, flight_id)
        con.execute('COMMIT')
        con.close()

    def items(self):
        con = store.connect(self.db)
        try:
            return {i['flight_id']: i
                    for i in pl.load_flights(con, DAY, DAY)}
        finally:
            con.close()

    def rows(self):
        con = store.connect(self.db)
        root = store.source_root(os.path.abspath(self.db))
        try:
            summary = pl._recalculate(con, root, DAY, DAY, False, None, False,
                                      500, None, True, None)
        finally:
            con.close()
        return {r['flight_id']: r for r in summary['flights']}, summary

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class ChronologyIdentityIsStable(unittest.TestCase):

    def setUp(self):
        self.fx = Fixture()
        # Карточку получает ТОЛЬКО база: ровно та смесь, что была на площадке.
        self.fx.add_card(BASE)
        self.addCleanup(self.fx.close)

    def test_the_evidence_identity_really_does_differ(self):
        # Без этого расхождения все проверки ниже прошли бы и на старом коде.
        items = self.fx.items()
        self.assertEqual(items[BASE]['hardware_id'], FC_SN)
        self.assertEqual(items[BASE]['hardware_id_source'], 'card')
        self.assertEqual(items[TARGET]['hardware_id'], BODY_CODE)
        self.assertEqual(items[TARGET]['hardware_id_source'],
                         pl.HW_SOURCE_UNIT_NICKNAME)
        self.assertNotEqual(items[BASE]['hardware_id'],
                            items[TARGET]['hardware_id'])

    def test_one_aircraft_is_one_chronology_group(self):
        items = self.fx.items()
        keys = {i['chronology_key'] for i in items.values()}
        self.assertEqual(len(keys), 1, keys)
        self.assertEqual(keys.pop(), 'unit:1')
        for item in items.values():
            self.assertEqual(item['chronology_key_source'],
                             pl.CHRONOLOGY_FROM_UNIT)

    def test_the_structural_candidate_survives_mixed_evidence(self):
        rows, summary = self.fx.rows()
        self.assertEqual(summary['structural_candidates'], 1)
        self.assertTrue(rows[TARGET]['structural_candidate'])
        self.assertEqual(rows[TARGET]['candidate_base_flight_id'], BASE)
        self.assertTrue(rows[TARGET]['scalar_source_check'])
        # База и мостик кандидатами не являются: экран не расширен.
        self.assertFalse(rows[BASE]['structural_candidate'])
        self.assertFalse(rows[BRIDGE]['structural_candidate'])
        self.assertFalse(rows[LONE]['structural_candidate'])

    def test_the_candidate_is_found_whichever_flight_carries_the_card(self):
        # Доказательство может прийти к любому звену -- результат тот же.
        for carrier in (BASE, BRIDGE, TARGET, None):
            fx = Fixture()
            if carrier is not None:
                fx.add_card(carrier)
            try:
                rows, summary = fx.rows()
                self.assertEqual(summary['structural_candidates'], 1,
                                 'card on %s' % carrier)
                self.assertEqual(rows[TARGET]['candidate_base_flight_id'],
                                 BASE, 'card on %s' % carrier)
            finally:
                fx.close()

    def test_card_hardware_provenance_stays_on_the_record(self):
        rows, _summary = self.fx.rows()
        self.assertEqual(rows[BASE]['hardware_id'], FC_SN)
        self.assertEqual(rows[TARGET]['hardware_id'], BODY_CODE)
        self.assertIn('HARDWARE_FROM_NICKNAME', rows[TARGET]['anomaly_flags'])
        self.assertNotIn('HARDWARE_FROM_NICKNAME', rows[BASE]['anomaly_flags'])

    def test_without_a_unit_the_nickname_holds_the_chronology(self):
        fx = Fixture(with_unit=False)
        fx.add_card(BASE)
        try:
            items = fx.items()
            keys = {i['chronology_key'] for i in items.values()}
            self.assertEqual(keys, {'nickname:synthetic-3'})
            self.assertEqual(items[BASE]['chronology_key_source'],
                             pl.CHRONOLOGY_FROM_NICKNAME)
            rows, summary = fx.rows()
            self.assertEqual(summary['structural_candidates'], 1)
            self.assertEqual(rows[TARGET]['candidate_base_flight_id'], BASE)
        finally:
            fx.close()

    def test_evidence_hardware_is_the_last_resort_not_the_first(self):
        fx = Fixture(with_unit=False, nickname='')
        fx.add_card(BASE)
        try:
            items = fx.items()
            self.assertEqual(items[BASE]['chronology_key'], 'hardware:%s' % FC_SN)
            self.assertEqual(items[BASE]['chronology_key_source'],
                             pl.CHRONOLOGY_FROM_HARDWARE)
            # Без машины, ника и карточки записи в хронологию не попадают.
            self.assertIsNone(items[TARGET]['chronology_key'])
            _rows, summary = fx.rows()
            self.assertEqual(summary['structural_candidates'], 0)
        finally:
            fx.close()

    def test_identity_is_a_pure_function_of_its_three_inputs(self):
        self.assertEqual(pl.chronology_identity(7, 'X', 'HW'),
                         ('unit:7', pl.CHRONOLOGY_FROM_UNIT))
        self.assertEqual(pl.chronology_identity(None, ' 3 Gijduvon ', 'HW'),
                         ('nickname:3gijduvon', pl.CHRONOLOGY_FROM_NICKNAME))
        self.assertEqual(pl.chronology_identity(None, '  ', 'HW'),
                         ('hardware:HW', pl.CHRONOLOGY_FROM_HARDWARE))
        self.assertEqual(pl.chronology_identity(None, None, None),
                         (None, None))
        # Ноль -- законный идентификатор машины, а не «нет машины».
        self.assertEqual(pl.chronology_identity(0, None, None)[0], 'unit:0')


class ChannelCapabilityFollowsTheAircraft(unittest.TestCase):
    """Способность канала -- свойство БОРТА, а не наличия карточки."""

    def test_evidence_from_a_card_flight_covers_a_non_card_flight(self):
        fx = Fixture()
        fx.add_card(BASE)
        # У базы V4 с кадрами применения -- канал машины доказан информативным.
        fx.add_v4(BASE, 0.0, 10000.0 / MU, spray=True)
        # У одиночного вылета V4 есть, применения в нём нет.
        fx.add_v4(LONE, 0.0, 9000.0 / MU, spray=False)
        try:
            rows, _summary = fx.rows()
            self.assertEqual(rows[BASE]['application_activity'], rs.ACT_PRESENT)
            # Вылет без карточки лежит в той же группе и потому знает, что
            # канал борта работает: «не наблюдалось» -- это НЕ «неизвестно».
            self.assertEqual(rows[LONE]['application_channel_quality'],
                             rs.CH_INFORMATIVE)
            self.assertEqual(rows[LONE]['application_activity'],
                             rs.ACT_NOT_OBSERVED)
        finally:
            fx.close()

    def test_the_unreliable_list_is_matched_through_the_group_identity(self):
        # Конфигурация названа серийными номерами. Запись без карточки несёт
        # код корпуса, и без групповой идентичности борт 3 Gijduvon перестал бы
        # считаться ненадёжным ровно там, где карточки не собрали.
        fx = Fixture()
        fx.add_card(BASE, hardware_id=UNRELIABLE_FC)
        fx.add_v4(LONE, 0.0, 9000.0 / MU, spray=False)
        try:
            rows, _summary = fx.rows()
            self.assertEqual(rows[BASE]['application_channel_quality'],
                             rs.CH_UNRELIABLE)
            self.assertEqual(rows[LONE]['application_channel_quality'],
                             rs.CH_UNRELIABLE)
            self.assertEqual(rows[LONE]['application_activity'], rs.ACT_UNKNOWN)
            self.assertIn('CHANNEL_IDENTITY_FROM_GROUP',
                          rows[LONE]['anomaly_flags'])
            self.assertNotIn('CHANNEL_IDENTITY_FROM_GROUP',
                             rows[BASE]['anomaly_flags'])
        finally:
            fx.close()

    def test_a_group_with_two_read_identities_is_named_not_guessed(self):
        fx = Fixture()
        fx.add_card(BASE, hardware_id=FC_SN)
        fx.add_card(LONE, hardware_id=UNRELIABLE_FC)
        try:
            rows, _summary = fx.rows()
            for fid in (BASE, TARGET, LONE):
                self.assertIn('CHRONOLOGY_GROUP_HARDWARE_CONFLICT',
                              rows[fid]['anomaly_flags'], fid)
            # При конфликте групповая идентичность НЕ подставляется.
            self.assertNotIn('CHANNEL_IDENTITY_FROM_GROUP',
                             rows[TARGET]['anomaly_flags'])
        finally:
            fx.close()


class GroupIdentityDoesNotDependOnTheWindow(unittest.TestCase):
    """Результат зависит от данных, а не от дат в командной строке.

    [REASON]: найдено сквозным прогоном ежедневного цикла. Идентичность
    группы собиралась по ЗАГРУЖЕННОМУ окну; в трёхдневном окне у борта не
    оказывалось ни одного вылета с карточкой, флаг
    `CHANNEL_IDENTITY_FROM_GROUP` пропадал, отпечаток входа менялся, и
    ежедневный пересчёт переписывал строки, верно посчитанные месячным. Две
    строки из 452 на настоящих сентябрьских данных -- и так каждый день.
    """

    LATE = 940030
    LATE_DAY = date(2026, 9, 10)

    def setUp(self):
        self.fx = Fixture()
        self.addCleanup(self.fx.close)
        # Карточка есть только у вылета 2 сентября...
        self.fx.add_card(BASE)
        # ...а этот вылет того же борта -- 10 сентября и без карточки.
        record = {'id': self.LATE, 'new_work_area': 7000.0, 'mode_name': 4,
                  'manual_mode': False, 'spray_width': 6.0,
                  'start_timestamp': ts('2026-09-10 09:00:00'),
                  'end_timestamp': ts('2026-09-10 09:06:00'),
                  'nickname': NICK}
        con = sqlite3.connect(self.fx.db)
        con.execute(
            'INSERT INTO drone_flights (dji_flight_id, started_at, '
            'finished_at, raw_json, drone_unit_id, nickname_raw) '
            'VALUES (?,?,?,?,1,?)',
            (self.LATE, '2026-09-10 09:00:00', '2026-09-10 09:06:00',
             json.dumps(record), NICK))
        con.commit()
        con.close()

    def flags(self, flight_id):
        con = sqlite3.connect(self.fx.db)
        row = con.execute(
            'SELECT anomaly_flags_json FROM dji_area_calculations WHERE '
            'flight_id=? AND superseded_at IS NULL', (flight_id,)).fetchone()
        con.close()
        return json.loads(row[0])

    def test_a_narrow_window_agrees_with_the_whole_month(self):
        month = pl.recalculate(self.fx.db, date(2026, 9, 1),
                               date(2026, 9, 30), apply=True)
        self.assertEqual(set(month['calc_writes']), {'new'})
        self.assertIn('CHANNEL_IDENTITY_FROM_GROUP', self.flags(self.LATE))

        narrow = pl.recalculate(self.fx.db, self.LATE_DAY, self.LATE_DAY,
                                apply=True)
        # Тот же вход -- та же строка: окно в один день ничего не переписывает.
        self.assertEqual(narrow['calc_writes'], {'unchanged': 1},
                         narrow['calc_writes'])
        self.assertIn('CHANNEL_IDENTITY_FROM_GROUP', self.flags(self.LATE))

    def test_another_month_does_not_lend_its_identity(self):
        """Ключ -- (борт, МЕСЯЦ): сентябрьская карточка октябрю не свидетель.

        Отрицательный контроль к проверке выше: независимость от окна
        достигнута областью «свой полный месяц», а не тем, что идентичность
        берётся откуда попало.
        """
        con = sqlite3.connect(self.fx.db)
        con.execute("UPDATE drone_flights SET started_at = "
                    "'2026-10-10 09:00:00', finished_at = "
                    "'2026-10-10 09:06:00' WHERE dji_flight_id = ?",
                    (self.LATE,))
        con.commit()
        con.close()
        pl.recalculate(self.fx.db, date(2026, 9, 1), date(2026, 10, 31),
                       apply=True)
        self.assertNotIn('CHANNEL_IDENTITY_FROM_GROUP', self.flags(self.LATE))


class TheInputHashFollowsTheIdentity(unittest.TestCase):

    def test_a_different_chronology_key_gives_a_different_hash(self):
        # [REASON]: иначе пересчёт после исправления ответил бы `unchanged` и
        # навсегда оставил строки, посчитанные по разорванной цепочке.
        from dji_area.hashing import calculation_input_hash
        sources = {'list': 'a', 'card': None, 'route': None, 'v4': None}
        base = calculation_input_hash(
            sources, [], False, extra={'chronology_key': 'unit:1'})
        other = calculation_input_hash(
            sources, [], False, extra={'chronology_key': 'unit:2'})
        self.assertNotEqual(base, other)

    def test_the_pipeline_really_puts_the_key_into_the_hash(self):
        """Сквозная проверка: пересчёт обязан ЗАМЕТИТЬ смену группировки.

        [REASON]: проверки чистой функции `calculation_input_hash` мало --
        она проходит и тогда, когда конвейер ключ в отпечаток не кладёт. Тогда
        пересчёт после исправления идентичности ответил бы `unchanged` и
        навсегда оставил строки, посчитанные по разорванной цепочке.

        Две машины с ОДНИМ кодом корпуса -- искусственно, и в этом весь смысл:
        при переносе вылета `hardware_id` не меняется, меняется только ключ
        хронологии. Если он не в отпечатке, различить нечем.
        """
        fx = Fixture()
        self.addCleanup(fx.close)
        con = sqlite3.connect(fx.db)
        con.execute('INSERT INTO drone_units VALUES (2, ?)', (BODY_CODE,))
        con.commit()
        con.close()
        first = pl.recalculate(fx.db, DAY, DAY, apply=True)
        self.assertEqual(set(first['calc_writes']), {'new'})
        again = pl.recalculate(fx.db, DAY, DAY, apply=True)
        self.assertEqual(set(again['calc_writes']), {'unchanged'})

        con = sqlite3.connect(fx.db)
        con.execute('UPDATE drone_flights SET drone_unit_id = 2 '
                    'WHERE dji_flight_id = ?', (LONE,))
        con.commit()
        con.close()
        items = fx.items()
        self.assertEqual(items[LONE]['hardware_id'], BODY_CODE)
        self.assertEqual(items[BASE]['hardware_id'], BODY_CODE)
        self.assertNotEqual(items[LONE]['chronology_key'],
                            items[BASE]['chronology_key'])

        moved = pl.recalculate(fx.db, DAY, DAY, apply=True)
        self.assertEqual(moved['calc_writes'].get('new'), 1, moved['calc_writes'])

    def test_evidence_arriving_at_a_SIBLING_flight_changes_the_hash(self):
        """Свидетельство канала -- вход расчёта, и оно приходит от СОСЕДА.

        [REASON]: с impl-4 словарь свидетельства ключуется устойчивой
        идентичностью. Если отпечаток входа по-прежнему спрашивает словарь
        идентичностью ИЗ ДОКАЗАТЕЛЬСТВА, он получает `False` всегда, и
        пересчёт отвечает `unchanged` на запись, качество канала которой уже
        изменилось. Строка навсегда остаётся с прежним `UNKNOWN`.

        Проверка построена так, чтобы различать верный и неверный код: у
        одиночного вылета собственные источники и соседи НЕ меняются между
        двумя прогонами (`_neighbour_revision` берёт card/list, но не v4),
        меняется ровно одно -- V4 с применением у ДРУГОГО вылета борта.
        """
        fx = Fixture()
        self.addCleanup(fx.close)
        fx.add_card(BASE)
        # У одиночного вылета есть свой V4, применения в нём нет.
        fx.add_v4(LONE, 0.0, 9000.0 / MU, spray=False)

        first = pl.recalculate(fx.db, DAY, DAY, apply=True,
                               collect_rows=True)
        self.assertEqual(set(first['calc_writes']), {'new'})
        before = {r['flight_id']: r for r in first['flights']}
        # Канал борта пока ничем не доказан.
        self.assertEqual(before[LONE]['application_channel_quality'],
                         rs.CH_UNKNOWN)
        self.assertEqual(before[LONE]['application_activity'], rs.ACT_UNKNOWN)
        self.assertEqual(set(pl.recalculate(fx.db, DAY, DAY,
                                            apply=True)['calc_writes']),
                         {'unchanged'})

        # Приходит V4 СОСЕДА -- с применением. Источники LONE не тронуты.
        fx.add_v4(BASE, 0.0, 10000.0 / MU, spray=True)
        after_run = pl.recalculate(fx.db, DAY, DAY, apply=True,
                                   collect_rows=True)
        after = {r['flight_id']: r for r in after_run['flights']}

        # Расхождение реально: решение по LONE изменилось.
        self.assertEqual(after[LONE]['application_channel_quality'],
                         rs.CH_INFORMATIVE)
        self.assertEqual(after[LONE]['application_activity'],
                         rs.ACT_NOT_OBSERVED)
        # И пересчёт обязан это ЗАПИСАТЬ, а не ответить `unchanged`.
        self.assertEqual(after_run['calc_writes'].get('new'), len(CHAIN),
                         after_run['calc_writes'])

    def test_the_recorded_versions_say_the_model_moved(self):
        import dji_area
        self.assertTrue(dji_area.AREA_ALGORITHM_VERSION.endswith('-impl-4'))
        self.assertEqual(dji_area.CHANNEL_CAPABILITY_REVISION,
                         'app-channel-unit-month-2')
        # Правило не менялось -- его версия обязана остаться прежней.
        self.assertEqual(dji_area.STRUCTURAL_RULE_VERSION,
                         'structural-retained-screen-frozen-1')


if __name__ == '__main__':
    unittest.main()
