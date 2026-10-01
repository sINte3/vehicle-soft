# -*- coding: utf-8 -*-
"""DRONE-FIELD-PASSPORT-001: поля DJI и паспорт работы -- ядро без Flask.

stdlib, идёт в CI. Схема -- DDL самих миграций (`DJI_AREA_EVIDENCE_001`,
`DRONE_AREA_CONTROL_V2_001`) плюс минимальные `drone_flights`/`drone_units`.
Здесь держатся инварианты, которые ломаются молча и стоят денег:

  1. Состояния привязки: EXACT / IDENTIFIED / PROBABLE / CANDIDATE /
     AMBIGUOUS / UNRESOLVED (с причиной) / NOT_RESOLVED.
  2. «Карточка не собрана» (NO_CARD) отличается от «ключа нет» (NO_KEY).
  3. ЧЛЕНСТВО: вылет входит в итог записи поля только по `field_land_uuid`
     текущей привязки и подтверждённому состоянию. Общая граница (md5) у двух
     записей НЕ удваивает гектары.
  4. Предположительные и кандидаты в гектары поля не входят.
  5. Итог поля -- `accepted.summarize` по тому же набору вылетов.
  6. Историчность: августовский вылет остаётся на границе V1, когда
     последняя ревизия записи -- V2.
  7. Запросов -- по кускам, не по вылетам (нет N+1).
  8. Только чтение; перепись и инструмент: числа, коды выхода, ASCII.

К каждому опасному инварианту приложен отрицательный контроль: заведомо
неверный вариант кода, на котором та же проверка ОБЯЗАНА упасть. Проверка,
дающая одинаковый ответ на верном и неверном коде, проверкой не является.

ВСЕ ДАННЫЕ СИНТЕТИЧЕСКИЕ: вылеты 950001.., записи полей с UUID вида
aaaaaaaa-0000-4000-8000-0000000000NN, md5 -- строки из одной цифры.
"""

import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from dji_area import accepted as acc  # noqa: E402
from dji_area import control_store  # noqa: E402
from dji_area import field as fld  # noqa: E402
from dji_area import field_store as fs  # noqa: E402
from dji_area import field_view as fv  # noqa: E402
from dji_area import resolver as rs  # noqa: E402
from dji_area import store as dji_store  # noqa: E402

import migrate_dji_area_evidence_001 as m_evidence  # noqa: E402
import migrate_drone_area_control_v2_001 as m_control  # noqa: E402

PROVIDER = 'SYNTHETIC-ACCOUNT-NOT-REAL'

LAND_A = 'aaaaaaaa-0000-4000-8000-000000000001'
LAND_B = 'aaaaaaaa-0000-4000-8000-000000000002'
LAND_C = 'aaaaaaaa-0000-4000-8000-000000000003'
LAND_D = 'aaaaaaaa-0000-4000-8000-000000000004'
MD5_SHARED = '1' * 32      # одна граница у записей A и B
MD5_NO_BYTES = '2' * 32    # граница A, байты которой не сохранены
MD5_V1 = '3' * 32          # C: граница августа
MD5_V2 = '4' * 32          # C: граница сентября (последняя ревизия)
MD5_D = '5' * 32

# Приманки безопасности: ни одна не должна дойти до HTML.
SECRET_TOKEN = 'SYNTHETIC-API-TOKEN-NOT-REAL'
SIGNED_URL = ('https://synthetic-bucket.example/land.json'
              '?X-Amz-Signature=SYNTHETIC-SIGNATURE-NOT-REAL')
STORAGE_PATH = 'C:\\SYNTHETIC\\dji_sources\\card-NOT-REAL.json'
COORDINATES = ('39.97667375006', '64.36347493190', '39.977634', '64.364628')

AUG = datetime(2026, 8, 10, 6, 0)    # UTC; местное 11:00
SEP = datetime(2026, 9, 12, 6, 0)

# Вылеты. (id, начало UTC, RAW га)
F_A_EXACT = 950001        # TIER1, запись A, общая граница
F_A_IDENT = 950002        # TIER2, запись A, байтов нет, доказанный фантом
F_A_REVIEW = 950003       # TIER1, запись A, расчёт REVIEW
F_A_NOCALC = 950004       # TIER1, запись A, расчёта площади нет
F_A_PROBABLE = 950005     # TIER3, запись A -- не в итоге
F_A_CANDIDATE = 950006    # TIER4, запись A -- не в итоге
F_A_SUPERSEDED = 950007   # была TIER1/A, текущая -- TIER5
F_A_REACTIVATED = 950008  # текущая TIER5 с МЕНЬШИМ id, чем замещённая TIER1/A
F_B_EXACT = 950011        # TIER1, запись B, та же граница MD5_SHARED
F_C_AUG = 950021          # TIER1, запись C, граница V1
F_C_SEP = 950022          # TIER1, запись C, граница V2
F_NO_CARD = 950031
F_NO_KEY = 950032
F_NO_KEY_MANUAL = 950033
F_KEY_LATE = 950034
F_NOT_IN_CATALOG = 950035
F_UNPARSED = 950036
F_AMBIGUOUS = 950037
F_NO_ATTR = 950038

RAW = {
    F_A_EXACT: 1.0, F_A_IDENT: 2.0, F_A_REVIEW: 0.5, F_A_NOCALC: 0.25,
    F_A_PROBABLE: 3.0, F_A_CANDIDATE: 4.0, F_A_SUPERSEDED: 6.0,
    F_A_REACTIVATED: 8.0,
    F_B_EXACT: 0.7, F_C_AUG: 1.1, F_C_SEP: 1.2,
    F_NO_CARD: 0.1, F_NO_KEY: 0.1, F_NO_KEY_MANUAL: 0.1, F_KEY_LATE: 0.1,
    F_NOT_IN_CATALOG: 0.1, F_UNPARSED: 0.1, F_AMBIGUOUS: 0.1, F_NO_ATTR: 0.1,
}
START = {fid: SEP for fid in RAW}
START[F_C_AUG] = AUG

# Расчёты площади: (статус, право, исправленная м2). Нет -- расчёта нет.
CALCS = {
    F_A_EXACT: (rs.RAW_CORROBORATED, rs.AGG_CERTIFIED, 10000.0),
    F_A_IDENT: (rs.COUNTER_FLAT_RAW_OVERSTATED, rs.AGG_CERTIFIED, 0.0),
    F_A_REVIEW: (rs.OVERLAP_REVIEW, rs.AGG_EXCLUDED_OVERLAP, None),
    F_A_PROBABLE: (rs.RAW_CORROBORATED, rs.AGG_CERTIFIED, 30000.0),
    F_A_CANDIDATE: (rs.RAW_CORROBORATED, rs.AGG_CERTIFIED, 40000.0),
    F_B_EXACT: (rs.RAW_CORROBORATED, rs.AGG_CERTIFIED, 7000.0),
    F_C_AUG: (rs.RAW_CORROBORATED, rs.AGG_CERTIFIED, 11000.0),
    F_C_SEP: (rs.RAW_CORROBORATED, rs.AGG_CERTIFIED, 12000.0),
}
# Ожидаемый итог записи A (подтверждённые: EXACT, IDENT, REVIEW, NOCALC).
A_CONFIRMED = {F_A_EXACT, F_A_IDENT, F_A_REVIEW, F_A_NOCALC}
A_RAW_M2 = 10000.0 + 20000.0 + 5000.0 + 2500.0
A_ACCEPTED_M2 = 10000.0 + 0.0 + 5000.0          # NOCALC не рассчитан
A_EXCLUDED_M2 = 0.0 + 20000.0 + 0.0


def make_schema(path):
    con = sqlite3.connect(path)
    con.execute('CREATE TABLE users (id INTEGER PRIMARY KEY)')
    con.execute('CREATE TABLE drone_units (id INTEGER PRIMARY KEY, '
                'number INTEGER NOT NULL, hardware_id VARCHAR(50))')
    con.execute('CREATE TABLE drone_flights (id INTEGER PRIMARY KEY, '
                'dji_flight_id BIGINT NOT NULL UNIQUE, drone_unit_id INTEGER, '
                'nickname_raw VARCHAR(100), started_at DATETIME NOT NULL, '
                'finished_at DATETIME, area_ha FLOAT NOT NULL DEFAULT 0, '
                'region VARCHAR(100), raw_json TEXT)')
    for module in (m_evidence, m_control):
        for _name, ddl in module.TABLES:
            con.execute(ddl)
        for _name, ddl in module.INDEXES:
            con.execute(ddl)
    for _name, ddl in m_control.TRIGGERS:
        con.execute(ddl)
    con.commit()
    return con


class Seed(object):
    """Синтетическая база одного сценария; запись -- прямой SQL."""

    def __init__(self, con):
        self.con = con
        self.n = 0

    def _hash(self):
        self.n += 1
        return 'SYNTHETIC-HASH-%05d' % self.n

    def unit(self, number=6):
        cur = self.con.execute('INSERT INTO drone_units (number) VALUES (?)',
                               (number,))
        return cur.lastrowid

    def flight(self, fid, started, area_ha, unit_id=None):
        # 26 символов, как пишет SQLAlchemy: граница периода обязана держать
        # и этот формат.
        self.con.execute(
            'INSERT INTO drone_flights (dji_flight_id, drone_unit_id, '
            'nickname_raw, started_at, area_ha, raw_json) '
            'VALUES (?,?,?,?,?,?)',
            (fid, unit_id, 'SYNTHETIC-%d' % fid,
             started.strftime('%Y-%m-%d %H:%M:%S.%f'), area_ha, '{}'))

    def snapshot(self, captured, complete=1, imported=0):
        cur = self.con.execute(
            'INSERT INTO dji_land_snapshots (captured_at_utc, received_count, '
            'complete, is_evidence_import, created_at) VALUES (?,?,?,?,?)',
            (captured.strftime('%Y-%m-%d %H:%M:%S'), 1, complete, imported,
             captured.strftime('%Y-%m-%d %H:%M:%S')))
        return cur.lastrowid

    def revision(self, land, md5, name, first_snap, last_snap, serial=None,
                 external=None, address=None, work_mu=None):
        raw = {'uuid': land, 'name': name, 'address': address,
               # Координаты узла DJI: на страницы не выходят никогда.
               'position': {'lat': 39.97667375006, 'lng': 64.36347493190},
               'bbox': {'upperRight': {'lat': 39.977634, 'lng': 64.364628}},
               'geometry': {'storage': {'contentMd5': md5}}}
        text = json.dumps(raw, sort_keys=True)
        cur = self.con.execute(
            'INSERT INTO dji_land_revisions (land_uuid, external_id, '
            'serial_number, name, work_area_raw, total_area_raw, area_unit, '
            'geometry_md5, raw_json, raw_sha256, first_seen_snapshot_id, '
            'last_seen_snapshot_id, seen_count) '
            "VALUES (?,?,?,?,?,?,'mu',?,?,?,?,?,1)",
            (land, external, serial, name, work_mu, work_mu, md5, text,
             self._hash(), first_snap, last_snap))
        return cur.lastrowid

    def geometry(self, md5, verified=1):
        self.con.execute(
            'INSERT INTO dji_land_geometries (content_md5, sha256, size_bytes, '
            'md5_verified, body_blob, parse_status, first_seen_at) '
            "VALUES (?,?,?,?,?, 'OK', '2026-08-01 00:00:00')",
            (md5, self._hash(), 10, verified,
             sqlite3.Binary(b'SYNTHETIC-POLYGON-BODY-NOT-REAL')))

    def attribution(self, fid, tier, method, land=None, md5=None, hist=0,
                    warnings=None, superseded=False, linked=None,
                    version=None, name=None):
        self.con.execute(
            'INSERT INTO dji_field_attributions (flight_id, '
            'field_resolver_version, field_input_hash, calculated_at, '
            'superseded_at, geometry_md5, linked_land_uuid, '
            'historical_geometry_available, field_attribution_tier, '
            'field_attribution_method, field_confidence, field_land_uuid, '
            'field_name_at_snapshot, warnings_json) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (fid, version or dji_store.FIELD_RESOLVER_VERSION, self._hash(),
             '2026-09-20 00:00:00',
             '2026-09-20 01:00:00' if superseded else None, md5, linked,
             hist, tier, method, 'HIGH', land, name,
             json.dumps(warnings or [])))

    def evidence(self, fid, card_rev=None, card_key=None):
        self.con.execute(
            'INSERT INTO dji_flight_evidence (flight_id, provider_account_id, '
            'card_revision_id, card_geometry_md5, updated_at) '
            "VALUES (?,?,?,?, '2026-09-20 00:00:00')",
            (fid, PROVIDER, card_rev, card_key))

    def source(self, fid, source_type='CARD'):
        # Тело, путь и контекст запроса -- приманки: страницам их выводить
        # нельзя, тест безопасности ищет их в HTML.
        cur = self.con.execute(
            'INSERT INTO dji_source_revisions (provider_account_id, flight_id, '
            'scope_key, source_type, sha256, size_bytes, captured_at_utc, '
            'parser_version, request_context_json, is_evidence_import, '
            'storage_kind, body_text, body_path, received_at, ingest_count) '
            "VALUES (?,?,?,?,?,?, '2026-09-12 07:00:00', 'SYNTHETIC-PARSER-1',"
            " ?, 0, 'INLINE', ?, ?, '2026-09-12 07:00:00', 1)",
            (PROVIDER, fid, str(fid), source_type, 'f' * 64, 100,
             json.dumps({'authorization': SECRET_TOKEN}),
             json.dumps({'signedUrl': SIGNED_URL,
                         'points_json': [[64.36347493190, 39.97667375006]]}),
             STORAGE_PATH))
        return cur.lastrowid

    def calc(self, fid, status, eligibility, corrected, raw_m2, started):
        cols = list(dji_store.CALC_COLUMNS)
        row = dict.fromkeys(cols)
        row.update(
            flight_id=fid, provider_account_id=PROVIDER,
            area_algorithm_version=dji_store.AREA_ALGORITHM_VERSION,
            calculation_input_hash=self._hash(),
            calculated_at='2026-09-20 00:00:00',
            start_at_utc=started.strftime('%Y-%m-%d %H:%M:%S'),
            report_timezone='Asia/Tashkent',
            report_start_date=started.strftime('%Y-%m-%d'),
            raw_area_m2=raw_m2, corrected_recorded_area_m2=corrected,
            area_status=status, aggregation_eligibility=eligibility,
            anomaly_flags_json='[]')
        self.con.execute(
            'INSERT INTO dji_area_calculations (%s) VALUES (%s)'
            % (', '.join(cols), ', '.join('?' * len(cols))),
            [row[c] for c in cols])


def seed_scenario(con, with_flights=True):
    """Весь сценарий; ``with_flights=False`` -- журнал вылетов пишет ORM."""
    s = Seed(con)
    if with_flights:
        unit = s.unit(6)
        for fid, area in RAW.items():
            s.flight(fid, START[fid], area, unit)
    s1 = s.snapshot(datetime(2026, 8, 1, 3, 0))
    s2 = s.snapshot(datetime(2026, 9, 5, 3, 0))
    s3 = s.snapshot(datetime(2026, 9, 25, 3, 0))
    s.revision(LAND_A, MD5_SHARED, 'SYNTHETIC field A', s1, s3,
               serial='SER-A', external='SYNTHETIC-EXT-A',
               address='SYNTHETIC village A', work_mu=150.0)
    s.revision(LAND_A, MD5_NO_BYTES, 'SYNTHETIC field A renamed', s3, s3,
               serial='SER-A', external='SYNTHETIC-EXT-A',
               address='SYNTHETIC village A', work_mu=150.0)
    s.revision(LAND_B, MD5_SHARED, 'SYNTHETIC field B', s2, s3,
               serial='SER-B', address='SYNTHETIC village B')
    s.revision(LAND_C, MD5_V1, 'SYNTHETIC field C', s1, s1, serial='SER-C')
    s.revision(LAND_C, MD5_V2, 'SYNTHETIC field C', s2, s3, serial='SER-C')
    s.revision(LAND_D, MD5_D, 'SYNTHETIC field D without flights', s1, s3)
    for md5 in (MD5_SHARED, MD5_V1, MD5_V2, MD5_D):
        s.geometry(md5)
    t1, t2, t3, t4, t5 = (fld.TIER1_EXACT, fld.TIER2_STRONG,
                          fld.TIER3_SUPPORTED, fld.TIER4_GEOMETRIC,
                          fld.TIER5_UNKNOWN)
    s.attribution(F_A_EXACT, t1, 'PLAIN_MD5_GEOMETRY_OBJECT', LAND_A,
                  MD5_SHARED, 1, ['MULTIPLE_GEOMETRY_HOLDERS'])
    s.attribution(F_A_IDENT, t2, 'COMPOSITE_UUID_CATALOG_MD5_BYTES_UNAVAILABLE',
                  LAND_A, MD5_NO_BYTES, 0, ['HISTORICAL_GEOMETRY_UNAVAILABLE'],
                  linked=LAND_A)
    s.attribution(F_A_REVIEW, t1, 'COMPOSITE_UUID_CURRENT_GEOMETRY', LAND_A,
                  MD5_SHARED, 1, linked=LAND_A)
    s.attribution(F_A_NOCALC, t1, 'COMPOSITE_UUID_CURRENT_GEOMETRY', LAND_A,
                  MD5_SHARED, 1, linked=LAND_A)
    s.attribution(F_A_PROBABLE, t3,
                  'COMPOSITE_UUID_DELETED_LINEAGE_VIA_OTHER_FLIGHTS', LAND_A,
                  MD5_SHARED, 0)
    s.attribution(F_A_CANDIDATE, t4, 'ROUTE_INSIDE_CURRENT_POLYGON_CANDIDATE',
                  LAND_A, None, 0, ['TIER4_CANDIDATE_ONLY_91_PCT'])
    s.attribution(F_A_SUPERSEDED, t1, 'PLAIN_MD5_GEOMETRY_OBJECT', LAND_A,
                  MD5_SHARED, 1, superseded=True)
    s.attribution(F_A_SUPERSEDED, t5, 'AUTO_NO_KEY')
    # Пересчёт с прежним входом реактивирует старую строку с её id: текущая
    # строка может оказаться СТАРШЕ замещённой. Текущесть -- `superseded_at`,
    # а не порядок id.
    s.attribution(F_A_REACTIVATED, t5, 'AUTO_NO_KEY')
    s.attribution(F_A_REACTIVATED, t1, 'PLAIN_MD5_GEOMETRY_OBJECT', LAND_A,
                  MD5_SHARED, 1, superseded=True)
    s.attribution(F_B_EXACT, t1, 'COMPOSITE_UUID_CURRENT_GEOMETRY', LAND_B,
                  MD5_SHARED, 1, linked=LAND_B)
    s.attribution(F_C_AUG, t1, 'COMPOSITE_UUID_CURRENT_GEOMETRY', LAND_C,
                  MD5_V1, 1, linked=LAND_C)
    s.attribution(F_C_SEP, t1, 'COMPOSITE_UUID_CURRENT_GEOMETRY', LAND_C,
                  MD5_V2, 1, linked=LAND_C)
    s.evidence(F_A_EXACT, card_rev=s.source(F_A_EXACT), card_key=MD5_SHARED)
    s.attribution(F_NO_CARD, t5, 'AUTO_NO_KEY')
    s.attribution(F_NO_KEY, t5, 'AUTO_NO_KEY')
    s.evidence(F_NO_KEY, card_rev=101)
    s.attribution(F_NO_KEY_MANUAL, t5, 'MANUAL_NO_KEY')
    s.evidence(F_NO_KEY_MANUAL, card_rev=102)
    s.attribution(F_KEY_LATE, t5, 'AUTO_NO_KEY')
    s.evidence(F_KEY_LATE, card_rev=103, card_key=MD5_D)
    s.attribution(F_NOT_IN_CATALOG, t5, 'PLAIN_MD5_NOT_IN_CATALOG',
                  md5='9' * 32)
    s.evidence(F_NOT_IN_CATALOG, card_rev=104, card_key='9' * 32)
    s.attribution(F_UNPARSED, t5, 'UNPARSED_KEY',
                  warnings=['GEOMETRY_KEY_UNRECOGNIZED'])
    s.evidence(F_UNPARSED, card_rev=105, card_key='garbage')
    s.attribution(F_AMBIGUOUS, t5, 'COMPOSITE_UUID_LINEAGE_CONFLICT',
                  md5=MD5_SHARED, linked=LAND_A, warnings=['LINEAGE_CONFLICT'])
    s.evidence(F_AMBIGUOUS, card_rev=106, card_key=LAND_A + '__' + MD5_SHARED)
    for fid, (status, elig, corrected) in CALCS.items():
        s.calc(fid, status, elig, corrected, RAW[fid] * 10000.0, START[fid])
    con.commit()
    return s


class Scenario(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='field_passport_core_')
        self.path = os.path.join(self.tmp, 'core.db')
        con = make_schema(self.path)
        seed_scenario(con)
        con.close()
        self.con = dji_store.connect(self.path, read_only=True)

    def tearDown(self):
        self.con.close()
        shutil.rmtree(self.tmp, True)

    def members(self, land, fn=None):
        rows = fs.field_flights(self.con, land)['rows']
        return {int(r['flight_id']) for r in (fn or fs.confirmed_members)(
            rows, land)}


# ─── 1-2. Состояния привязки, NO_CARD против NO_KEY ──────────────────────────

class States(Scenario):

    def states(self):
        ids = list(RAW)
        attrs = fs.current_attributions(self.con, ids)
        ev = fs.evidence_for(self.con, ids)
        return {fid: fv.classify(attrs.get(fid), ev.get(fid)) for fid in ids}

    def test_every_tier_maps_to_one_state(self):
        st = self.states()
        self.assertEqual(st[F_A_EXACT], (fv.STATE_EXACT, None))
        self.assertEqual(st[F_A_IDENT], (fv.STATE_IDENTIFIED, None))
        self.assertEqual(st[F_A_PROBABLE], (fv.STATE_PROBABLE, None))
        self.assertEqual(st[F_A_CANDIDATE], (fv.STATE_CANDIDATE, None))
        self.assertEqual(st[F_AMBIGUOUS], (fv.STATE_AMBIGUOUS,
                                           fv.REASON_LINEAGE_CONFLICT))
        self.assertEqual(st[F_NOT_IN_CATALOG], (fv.STATE_UNRESOLVED,
                                                fv.REASON_NOT_IN_CATALOG))
        self.assertEqual(st[F_UNPARSED], (fv.STATE_UNRESOLVED,
                                          fv.REASON_UNPARSED))
        self.assertEqual(st[F_NO_ATTR], (fv.STATE_NOT_RESOLVED, None))
        # Текущая строка, а не замещённая: TIER1 в истории не считается.
        self.assertEqual(st[F_A_SUPERSEDED], (fv.STATE_UNRESOLVED,
                                              fv.REASON_NO_CARD))
        self.assertEqual(st[F_A_REACTIVATED], (fv.STATE_UNRESOLVED,
                                               fv.REASON_NO_CARD))

    def test_no_card_is_not_no_key(self):
        st = self.states()
        self.assertEqual(st[F_NO_CARD], (fv.STATE_UNRESOLVED,
                                         fv.REASON_NO_CARD))
        self.assertEqual(st[F_NO_KEY], (fv.STATE_UNRESOLVED, fv.REASON_NO_KEY))
        self.assertEqual(st[F_NO_KEY_MANUAL], (fv.STATE_UNRESOLVED,
                                               fv.REASON_NO_KEY))
        # Ключ пришёл после расчёта привязки -- не «ключа нет».
        self.assertEqual(st[F_KEY_LATE], (fv.STATE_UNRESOLVED,
                                          fv.REASON_KEY_AFTER_RESOLUTION))

    def test_negative_control_card_blind_classifier_merges_the_two(self):
        """Классификатор, не глядящий на карточку, слил бы NO_CARD с NO_KEY.

        Проверка выше ОБЯЗАНА это ловить: здесь тот же вызов на испорченной
        функции даёт другой ответ.
        """
        original = fv._has_card
        try:
            fv._has_card = lambda evidence: True
            st = self.states()
        finally:
            fv._has_card = original
        self.assertNotEqual(st[F_NO_CARD][1], fv.REASON_NO_CARD)

    def test_unknown_tier_never_becomes_confirmed(self):
        self.assertEqual(fv.classify({'field_attribution_tier': 'TIER0_NEW',
                                      'field_attribution_method': 'X'}),
                         (fv.STATE_UNRESOLVED, fv.REASON_OTHER))

    def test_views_carry_labels_in_both_languages(self):
        attr = fs.current_attributions(self.con, [F_NO_KEY_MANUAL])[
            F_NO_KEY_MANUAL]
        ev = fs.evidence_for(self.con, [F_NO_KEY_MANUAL])[F_NO_KEY_MANUAL]
        ru = fv.attribution_view(attr, ev, 'ru')
        uz = fv.attribution_view(attr, ev, 'uz')
        self.assertEqual(ru['state_label'], 'Поле не определено')
        self.assertIn('ручной режим', ru['reason_text'])
        self.assertEqual(uz['state_label'], 'Дала аниқланмаган')
        self.assertIn('қўлда', uz['reason_text'])


# ─── 3-5. Членство и итог поля ───────────────────────────────────────────────

def assert_no_double_count(case, membership_of):
    """Ни один вылет не входит в подтверждённый итог двух записей сразу."""
    seen = {}
    for land in (LAND_A, LAND_B, LAND_C, LAND_D):
        for fid in membership_of(land):
            case.assertNotIn(fid, seen, 'flight %d counted in %s and %s'
                             % (fid, seen.get(fid), land))
            seen[fid] = land
    return seen


class Membership(Scenario):

    def test_membership_is_by_field_land_uuid_and_confirmed_state(self):
        self.assertEqual(self.members(LAND_A), A_CONFIRMED)
        self.assertEqual(self.members(LAND_B), {F_B_EXACT})
        self.assertEqual(self.members(LAND_C), {F_C_AUG, F_C_SEP})
        self.assertEqual(self.members(LAND_D), set())

    def test_shared_geometry_does_not_double_count(self):
        """§4.3: A и B держат одну границу. Вылет A не входит в итог B."""
        holders = fs.other_holders(self.con, [MD5_SHARED], LAND_A)
        self.assertEqual(holders[MD5_SHARED], [LAND_B])
        self.assertNotIn(F_A_EXACT, self.members(LAND_B))
        seen = assert_no_double_count(self, self.members)
        self.assertEqual(seen[F_A_EXACT], LAND_A)
        # B видит вылет A диагностикой «та же граница», без гектаров.
        shared = fs.shared_geometry_flights(self.con, LAND_B, [MD5_SHARED])
        ids = {int(r['flight_id']) for r in shared['rows']}
        self.assertIn(F_A_EXACT, ids)
        self.assertIn(F_AMBIGUOUS, ids)
        self.assertNotIn(F_B_EXACT, ids)

    def test_negative_control_md5_membership_is_caught(self):
        """Членство «uuid ИЛИ md5 его границ» удвоило бы вылет A в B."""
        def by_md5(land):
            rows = fs.field_flights(self.con, land)['rows']
            md5s = [r['geometry_md5'] for r in fs.land_revisions(
                self.con, land) if r['geometry_md5']]
            rows += fs.shared_geometry_flights(self.con, land, md5s)['rows']
            return {int(r['flight_id']) for r in rows
                    if fv.classify(r)[0] in fv.CONFIRMED_STATES}
        self.assertIn(F_A_EXACT, by_md5(LAND_B))
        with self.assertRaises(AssertionError):
            assert_no_double_count(self, by_md5)

    def test_provisional_states_stay_out_of_the_total(self):
        rows = fs.field_flights(self.con, LAND_A)['rows']
        provisional = {int(r['flight_id'])
                       for r in fs.provisional_members(rows, LAND_A)}
        self.assertEqual(provisional, {F_A_PROBABLE, F_A_CANDIDATE})
        self.assertFalse(provisional & self.members(LAND_A))

    def test_negative_control_probable_in_hectares_is_caught(self):
        def leaky(rows, land):
            return [r for r in rows if r.get('field_land_uuid') == land]
        self.assertNotEqual(self.members(LAND_A, leaky), A_CONFIRMED)

    def totals(self, land, members_fn=None):
        rows = fs.field_flights(self.con, land)['rows']
        chosen = (members_fn or fs.confirmed_members)(rows, land)
        raw = {int(r['flight_id']): float(r['area_ha']) * 10000.0
               for r in chosen}
        by = control_store.accepted_for(self.con, raw)
        return acc.summarize(by[fid] for fid in raw)

    def test_field_total_equals_the_provider_on_the_same_set(self):
        t = self.totals(LAND_A)
        self.assertEqual(t['records'], 4)
        self.assertAlmostEqual(t['raw_m2'], A_RAW_M2, places=6)
        self.assertAlmostEqual(t['accepted_m2'], A_ACCEPTED_M2, places=6)
        self.assertAlmostEqual(t['excluded_m2'], A_EXCLUDED_M2, places=6)
        self.assertEqual(t['not_calculated_records'], 1)
        self.assertAlmostEqual(t['not_calculated_raw_m2'], 2500.0, places=6)
        self.assertEqual(t['open_records'], 1)          # REVIEW
        self.assertEqual(t['corrected_records'], 1)     # фантом
        self.assertFalse(t['control_ready'])            # не всё рассчитано
        self.assertIsNone(t['accepted_full_m2'])
        # Тот же набор, отданный провайдеру отдельно, -- то же число.
        direct = control_store.accepted_for(
            self.con, {fid: RAW[fid] * 10000.0 for fid in A_CONFIRMED})
        self.assertEqual(acc.summarize(direct.values()), t)

    def test_negative_control_one_swapped_flight_breaks_the_total(self):
        def swapped(rows, land):
            chosen = fs.confirmed_members(rows, land)
            out = [r for r in chosen if int(r['flight_id']) != F_A_EXACT]
            out += [r for r in rows if int(r['flight_id']) == F_A_PROBABLE]
            return out
        self.assertNotAlmostEqual(self.totals(LAND_A, swapped)['raw_m2'],
                                  A_RAW_M2, places=3)

    def test_no_calculation_is_not_accepted_as_raw(self):
        item = control_store.accepted_for(
            self.con, {F_A_NOCALC: 2500.0})[F_A_NOCALC]
        self.assertFalse(item['calculated'])
        self.assertIsNone(item['accepted_m2'])
        self.assertEqual(item['status'], acc.ST_NOT_CALCULATED)

    def test_review_stays_open_with_accepted_equal_raw(self):
        item = control_store.accepted_for(
            self.con, {F_A_REVIEW: 5000.0})[F_A_REVIEW]
        self.assertTrue(item['is_open'])
        self.assertEqual(item['status'], acc.ST_NEEDS_DECISION)
        self.assertAlmostEqual(item['accepted_m2'], 5000.0)


# ─── 6. Историчность ─────────────────────────────────────────────────────────

class History(Scenario):

    def test_august_flight_keeps_its_august_boundary(self):
        header = fs.land_header(self.con, LAND_C)
        self.assertEqual(header['geometry_md5'], MD5_V2)   # сегодня -- V2
        versions = [r['geometry_md5'] for r in fs.land_revisions(self.con,
                                                                 LAND_C)]
        self.assertEqual(versions, [MD5_V1, MD5_V2])
        attrs = fs.current_attributions(self.con, [F_C_AUG, F_C_SEP])
        self.assertEqual(attrs[F_C_AUG]['geometry_md5'], MD5_V1)
        self.assertEqual(attrs[F_C_SEP]['geometry_md5'], MD5_V2)
        view = fv.attribution_view(attrs[F_C_AUG])
        self.assertEqual(view['geometry_md5'], MD5_V1)
        self.assertTrue(view['historical_geometry_available'])

    def test_negative_control_latest_boundary_would_rewrite_history(self):
        def latest_boundary(fid):
            attr = fs.current_attributions(self.con, [fid])[fid]
            return fs.land_header(self.con, attr['field_land_uuid'])[
                'geometry_md5']
        self.assertNotEqual(latest_boundary(F_C_AUG), MD5_V1)

    def test_boundary_bytes_state(self):
        rows = fs.geometry_rows(self.con, [MD5_V1, MD5_NO_BYTES])
        self.assertEqual(fv.boundary_state(rows.get(MD5_V1)),
                         fv.BOUNDARY_SAVED)
        self.assertEqual(fv.boundary_state(rows.get(MD5_NO_BYTES)),
                         fv.BOUNDARY_MISSING)
        self.assertNotIn('body_blob', rows[MD5_V1])


# ─── 7-8. Пакетность, только чтение, список ──────────────────────────────────

class Batching(unittest.TestCase):
    """Число SELECT на карточку поля не растёт с числом её вылетов."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='field_passport_batch_')
        self.path = os.path.join(self.tmp, 'batch.db')
        con = make_schema(self.path)
        s = Seed(con)
        snap = s.snapshot(datetime(2026, 9, 1))
        s.revision(LAND_A, MD5_SHARED, 'SYNTHETIC A', snap, snap)
        s.revision(LAND_B, MD5_SHARED, 'SYNTHETIC B', snap, snap)
        for i in range(900):
            fid = 960000 + i
            land = LAND_A if i < 10 else LAND_B
            s.flight(fid, SEP, 1.0)
            s.attribution(fid, fld.TIER1_EXACT, 'PLAIN_MD5_GEOMETRY_OBJECT',
                          land, MD5_SHARED, 1)
            s.calc(fid, rs.RAW_CORROBORATED, rs.AGG_CERTIFIED, 10000.0,
                   10000.0, SEP)
        con.commit()
        con.close()

    def tearDown(self):
        shutil.rmtree(self.tmp, True)

    def card_statements(self, land):
        con = dji_store.connect(self.path, read_only=True)
        seen = []
        con.set_trace_callback(seen.append)
        try:
            rows = fs.field_flights(con, land)['rows']
            chosen = fs.confirmed_members(rows, land)
            raw = {int(r['flight_id']): 10000.0 for r in chosen}
            control_store.accepted_for(con, raw)
            fs.current_attributions(con, list(raw))
        finally:
            con.close()
        return len(chosen), len([s for s in seen
                                 if s.lstrip().upper().startswith('SELECT')])

    def test_statements_depend_on_chunks_not_on_flights(self):
        few, few_sql = self.card_statements(LAND_A)
        many, many_sql = self.card_statements(LAND_B)
        self.assertEqual((few, many), (10, 890))
        # 890 -- три куска по 400, 10 -- один: разница только в кусках.
        self.assertLessEqual(many_sql - few_sql, 2 * 3)
        self.assertLess(many_sql, 20)

    def test_negative_control_per_flight_reads_are_visible(self):
        con = dji_store.connect(self.path, read_only=True)
        seen = []
        con.set_trace_callback(seen.append)
        try:
            for fid in range(960010, 960110):
                fs.current_attributions(con, [fid])
        finally:
            con.close()
        self.assertGreaterEqual(len(seen), 100)


class ReadOnlyAndList(Scenario):

    def test_the_connection_refuses_writes(self):
        with self.assertRaises(sqlite3.OperationalError):
            self.con.execute("DELETE FROM dji_field_attributions")

    def test_list_search_and_counts(self):
        everything = fs.land_list(self.con)
        self.assertEqual(everything['total'], 4)
        by_uuid = {r['land_uuid']: r for r in everything['rows']}
        self.assertEqual(by_uuid[LAND_A]['confirmed'], 4)
        self.assertEqual(by_uuid[LAND_A]['provisional'], 2)
        self.assertEqual(by_uuid[LAND_A]['boundaries'], 2)
        self.assertEqual(by_uuid[LAND_A]['name'], 'SYNTHETIC field A renamed')
        self.assertEqual(by_uuid[LAND_B]['confirmed'], 1)
        self.assertEqual(by_uuid[LAND_D]['confirmed'], 0)
        # Список не несёт ни площади вылетов, ни координат.
        for key in ('raw_m2', 'accepted_m2', 'raw_json', 'center_lat'):
            self.assertNotIn(key, everything['rows'][0])
        for term, expected in (('renamed', {LAND_A}), ('SER-B', {LAND_B}),
                               (LAND_C[-6:], {LAND_C}),
                               ('village B', {LAND_B}),
                               ('SYNTHETIC-EXT-A', {LAND_A}),
                               ('no such field', set())):
            got = {r['land_uuid'] for r in fs.land_list(self.con, term)['rows']}
            self.assertEqual(got, expected, term)
        # LIKE-символы ищутся буквально.
        self.assertEqual(fs.land_list(self.con, '%')['total'], 0)
        only = fs.land_list(self.con, only_with_flights=True)
        self.assertNotIn(LAND_D, {r['land_uuid'] for r in only['rows']})

    def test_list_period_and_pagination(self):
        aug = fs.land_list(self.con, utc_start=datetime(2026, 7, 31, 19),
                           utc_end_excl=datetime(2026, 8, 31, 19),
                           only_with_flights=True)
        self.assertEqual({r['land_uuid'] for r in aug['rows']}, {LAND_C})
        self.assertEqual(aug['rows'][0]['confirmed'], 1)
        paged = fs.land_list(self.con, page=2, page_size=3)
        self.assertEqual((paged['page'], paged['pages'], len(paged['rows'])),
                         (2, 2, 1))
        clamped = fs.land_list(self.con, page=99, page_size=3)
        self.assertEqual(clamped['page'], 2)


# ─── Перепись и инструмент ───────────────────────────────────────────────────

class Census(Scenario):

    def test_counts_for_september(self):
        total, months = fs.flight_census(self.con, datetime(2026, 8, 31, 19),
                                         datetime(2026, 9, 30, 19))
        self.assertEqual(total['flights'], len(RAW) - 1)        # без C_AUG
        st, rsn = total['states'], total['reasons']
        self.assertEqual(st[fv.STATE_EXACT], 5)    # A_EXACT REVIEW NOCALC B C_SEP
        self.assertEqual(st[fv.STATE_IDENTIFIED], 1)
        self.assertEqual(st[fv.STATE_PROBABLE], 1)
        self.assertEqual(st[fv.STATE_CANDIDATE], 1)
        self.assertEqual(st[fv.STATE_AMBIGUOUS], 1)
        self.assertEqual(st[fv.STATE_NOT_RESOLVED], 1)
        # NO_CARD + SUPERSEDED + REACTIVATED
        self.assertEqual(rsn[fv.REASON_NO_CARD], 3)
        self.assertEqual(rsn[fv.REASON_NO_KEY], 2)
        self.assertEqual(rsn[fv.REASON_KEY_AFTER_RESOLUTION], 1)
        self.assertEqual(rsn[fv.REASON_NOT_IN_CATALOG], 1)
        self.assertEqual(rsn[fv.REASON_UNPARSED], 1)
        self.assertEqual(total['with_calculation'], 7)
        self.assertEqual(total['with_attribution'], len(RAW) - 2)
        self.assertEqual(total['with_historical_bytes'], 5)
        # IDENT (md5 без байтов), PROBABLE, AMBIGUOUS, NOT_IN_CATALOG.
        self.assertEqual(total['md5_without_bytes'], 4)
        self.assertEqual(sorted(months), ['2026-09'])
        self.assertEqual(sum(st.values()), total['flights'])

    def test_catalog_and_snapshots(self):
        result = fs.census(self.con, None, None,
                           now_utc=datetime(2026, 9, 30, 0, 0))
        cat = result['catalog']
        self.assertEqual(cat['land_records'], 4)
        self.assertEqual(cat['land_revisions'], 6)
        self.assertEqual(cat['shared_md5'], 1)
        self.assertEqual(cat['shared_md5_records'], 2)
        self.assertEqual(cat['md5_without_body'], 1)
        snap = result['snapshots']
        self.assertEqual(snap['count'], 2)            # 05.09 и 25.09
        self.assertEqual(snap['days_with_snapshot'], 2)
        self.assertEqual(snap['last_utc'], '2026-09-25 03:00:00')
        self.assertEqual(sorted(result['months']), ['2026-08', '2026-09'])


class CensusTool(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='field_census_tool_')
        self.path = os.path.join(self.tmp, 'tool.db')
        con = make_schema(self.path)
        seed_scenario(con)
        con.close()

    def tearDown(self):
        shutil.rmtree(self.tmp, True)

    def run_tool(self, args):
        sys.path.insert(0, os.path.join(REPO_ROOT, 'tools'))
        try:
            import dji_field_census as tool
        finally:
            sys.path.pop(0)
        out = io.StringIO()
        code = tool.main(args, out=out)
        return code, out.getvalue()

    def test_a_period_census_is_ascii_and_complete(self):
        json_path = os.path.join(self.tmp, 'census.json')
        code, text = self.run_tool(['--db', self.path, '--from', '2026-09-01',
                                    '--to', '2026-09-30', '--json', json_path,
                                    '--now', '2026-09-30T00:00'])
        self.assertEqual(code, 0, text)
        text.encode('ascii')
        for marker in ('NO_CARD', 'NO_KEY', 'EXACT', 'IDENTIFIED',
                       'md5 shared by >1 record', 'land snapshots'):
            self.assertIn(marker, text)
        with open(json_path, encoding='utf-8') as handle:
            data = json.load(handle)
        self.assertEqual(data['result']['total']['flights'], len(RAW) - 1)

    def test_a_missing_database_is_code_2_and_nothing_is_created(self):
        missing = os.path.join(self.tmp, 'absent.db')
        code, text = self.run_tool(['--db', missing, '--from', '2026-09-01',
                                    '--to', '2026-09-30'])
        self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(missing))

    def test_missing_tables_is_code_3(self):
        empty = os.path.join(self.tmp, 'empty.db')
        sqlite3.connect(empty).close()
        code, text = self.run_tool(['--db', empty, '--from', '2026-09-01',
                                    '--to', '2026-09-30'])
        self.assertEqual(code, 3)
        self.assertIn('missing tables', text)

    def test_bad_arguments_are_code_1(self):
        for args in (['--db', self.path, '--from', '2026-13-01', '--to',
                      '2026-09-30'],
                     ['--db', self.path, '--from', '2026-09-30', '--to',
                      '2026-09-01'],
                     ['--db', self.path]):
            code, _text = self.run_tool(args)
            self.assertEqual(code, 1, args)

    def test_the_tool_does_not_write(self):
        before = os.path.getmtime(self.path)
        size = os.path.getsize(self.path)
        code, _ = self.run_tool(['--db', self.path, '--from', '2026-08-01',
                                 '--to', '2026-09-30'])
        self.assertEqual(code, 0)
        self.assertEqual((os.path.getmtime(self.path),
                          os.path.getsize(self.path)), (before, size))


if __name__ == '__main__':
    unittest.main()
