# -*- coding: utf-8 -*-
"""Тесты tools/dji_rc_probe.py. Без сети, без браузера, без базы, без пульта.

    python tools/test_dji_rc_probe.py
    python -m unittest tools.test_dji_rc_probe

Все материалы здесь СИНТЕТИЧЕСКИЕ: тела V4 и маршрутов собраны теми же
protobuf-помощниками, что в `tests/test_dji_area_core.py`, по номерам полей,
доказанным на августовском корпусе. Ни один из них не является файлом
пульта, и прохождение этих тестов не доказывает доступ к данным DJI
(ТЗ §0.7). Они доказывают другое: что инструмент различает присутствие и
ноль, не пишет в источник, не печатает координат и значений, и говорит
«не знаю», когда не знает.

У каждого контроля есть отрицательный случай: проверка, которая зелена и
при верном, и при неверном поведении, проверкой не является.
"""

import contextlib
import gzip
import io
import json
import os
import sqlite3
import struct
import sys
import tempfile
import time
import unittest
import zipfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from dji_area import evidence as dji_evidence  # noqa: E402
from dji_area import v4  # noqa: E402
from drone_collector import route_decode as rd  # noqa: E402
from tools import dji_rc_probe as probe  # noqa: E402


# ─── Синтетический protobuf (как в tests/test_dji_area_core.py) ──────────────

def _varint(value):
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def f_varint(number, value):
    return _varint((number << 3) | 0) + _varint(value)


def f_f32(number, value):
    return _varint((number << 3) | 5) + struct.pack('<f', value)


def f_f64(number, value):
    return _varint((number << 3) | 1) + struct.pack('<d', value)


def f_bytes(number, payload):
    if isinstance(payload, str):
        payload = payload.encode('utf-8')
    return _varint((number << 3) | 2) + _varint(len(payload)) + payload


LAT = 39.123456
LNG = 64.654321
START_MS = 1785526013000


def frame(t_ms, area=None, mode=4, width=6.0, spray_flag=None, flow=None,
          quantity=None, lat=LAT, lng=LNG, vx=3.0, vy=0.0, with_spray=True):
    body = f_varint(v4.F_TIME_MS, t_ms)
    body += f_bytes(v4.F_POSITION, f_f64(1, lng) + f_f64(2, lat))
    body += f_bytes(v4.F_VELOCITY, f_f32(1, vx) + f_f32(2, vy))
    if mode is not None:
        body += f_varint(v4.F_WORK_MODE, mode)
    if width is not None:
        body += f_f32(v4.F_SPRAY_WIDTH, width)
    if with_spray:
        spray = b''
        if spray_flag:
            spray += f_varint(v4.S_SPRAY_FLAG, spray_flag)
        if flow:
            spray += f_varint(v4.S_FLOW, flow)
        if quantity is not None:
            spray += f_varint(v4.S_QUANTITY, quantity)
        body += f_bytes(v4.F_SPRAY, spray)
    if area is not None:
        body += f_f32(v4.F_AREA_STATE, area)
    return body


def v4_bytes(frames, count=None, usage_type=None):
    out = b''
    if count is not None:
        out += f_varint(v4.TOP_COUNT, count)
    for fr in frames:
        out += f_bytes(v4.TOP_FRAME, fr)
    if usage_type is not None:
        out += f_varint(v4.TOP_USAGE_TYPE, usage_type)
    return out


def v4_series(n=5, area_values=None, step_ms=1000, start_ms=START_MS, **kw):
    frames = []
    for i in range(n):
        area = area_values[i] if area_values is not None else None
        frames.append(frame(start_ms + i * step_ms, area=area, **kw))
    return v4_bytes(frames, count=n, usage_type=0)


def route_body(flight_id=673501214, hardware_id='1ZNBJ7C00A0001',
               points=((LAT, LNG), (LAT + 0.0001, LNG + 0.0001))):
    record = b''
    for lat, lng in points:
        record += f_bytes(rd.FIELD_POINT, f_f64(1, lat) + f_f64(2, lng))
    record += f_varint(rd.FIELD_FLIGHT_ID, flight_id)
    record += f_f32(rd.FIELD_WORK_AREA, 9946.0)
    record += f_bytes(rd.FIELD_DEVICE, f_varint(1, 7)
                      + f_bytes(2, '8 GardenU') + f_bytes(3, hardware_id))
    record += f_varint(rd.FIELD_START_MS, START_MS)
    record += f_varint(rd.FIELD_END_MS, START_MS + 4000)
    record += f_f32(rd.FIELD_SPRAY_WIDTH, 6.0)
    payload = f_bytes(rd.FIELD_ROUTE, record)
    return (f_varint(rd.FIELD_STATUS, rd.STATUS_OK)
            + f_bytes(rd.FIELD_MESSAGE, 'Success.')
            + f_bytes(rd.FIELD_PAYLOAD, payload))


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mode = 'wb' if isinstance(data, (bytes, bytearray)) else 'w'
    kwargs = {} if mode == 'wb' else {'encoding': 'utf-8'}
    with open(path, mode, **kwargs) as handle:
        handle.write(data)
    return path


def tree_fingerprint(root):
    """(relpath, size, mtime_ns, sha256) каждого файла -- для «источник не тронут»."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            stat = os.stat(full)
            out.append((os.path.relpath(full, root), stat.st_size,
                        stat.st_mtime_ns, probe.sha256_file(full)))
    return out


def make_sqlite(path, secret_value='the-row-value-must-not-leak'):
    con = sqlite3.connect(path)
    con.execute('CREATE TABLE flight_log (id INTEGER PRIMARY KEY, note TEXT)')
    con.execute('INSERT INTO flight_log (note) VALUES (?)', (secret_value,))
    con.execute('CREATE TABLE settings (k TEXT, v TEXT)')
    con.commit()
    con.close()


def run_main(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = probe.main(argv)
    return code, out.getvalue()


class ProbeCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.src = os.path.join(self.root, 'raw')
        self.out = os.path.join(self.root, 'probe')
        os.makedirs(self.src)

    def tearDown(self):
        self.tmp.cleanup()

    def path(self, *parts):
        return os.path.join(self.src, *parts)


# ─── Классификация ───────────────────────────────────────────────────────────

class Classification(ProbeCase):

    def test_every_kind_is_told_apart(self):
        make_sqlite(self.path('a.db'))
        with open(self.path('a.db'), 'rb') as handle:
            sqlite_blob = handle.read()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as archive:
            archive.writestr('inner.txt', 'hello')
        cases = {
            'SQLITE': sqlite_blob,
            'ZIP': buf.getvalue(),
            'GZIP': gzip.compress(b'anything'),
            'PNG': b'\x89PNG\r\n\x1a\n' + b'\x00' * 8,
            'JPEG': b'\xff\xd8\xff\xe0' + b'\x00' * 8,
            'PDF': b'%PDF-1.4\n',
            'JSON': b'{"a": 1, "b": [1, 2]}',
            'TEXT': 'plain text, no structure\n'.encode('utf-8'),
            'PROTOBUF_CANDIDATE': v4_series(3),
            'UNKNOWN_BINARY': b'\xff\xfe\xfd\xfc\xfb\xfa' * 5,
            'EMPTY': b'',
        }
        for expected, blob in cases.items():
            self.assertEqual(probe.classify(blob), expected, expected)

    def test_a_truncated_v4_body_is_not_called_v4(self):
        whole = v4_series(4)
        cut = whole[:len(whole) - 7]
        kind = probe.classify(cut)
        self.assertNotEqual(kind, 'PROTOBUF_CANDIDATE')
        shape = probe.v4_shape(cut)
        self.assertEqual(shape['status'], 'UNDECODABLE')

    def test_text_is_not_mistaken_for_protobuf(self):
        # Буквы ASCII разбираются как номера полей; текст всё равно TEXT.
        self.assertEqual(probe.classify(b'abcdefgh' * 4), 'TEXT')


# ─── V4: присутствие против нуля ─────────────────────────────────────────────

class V4Shape(ProbeCase):

    def test_omitted_counter_is_absent_not_zero(self):
        omitted = probe.v4_shape(v4_series(5, area_values=[None] * 5))
        zero = probe.v4_shape(v4_series(5, area_values=[0.0] * 5))
        self.assertEqual(omitted['status'], 'V4_LIKE')
        self.assertEqual(zero['status'], 'V4_LIKE')
        self.assertEqual(omitted['frame_key_presence']['area_native'], 0)
        self.assertEqual(zero['frame_key_presence']['area_native'], 5)
        self.assertEqual(omitted['summary']['counter_present_frames'], 0)
        self.assertIsNone(omitted['summary']['counter_first_encoded_native'])
        self.assertEqual(zero['summary']['counter_present_frames'], 5)
        self.assertEqual(zero['summary']['counter_first_encoded_native'], 0.0)
        self.assertEqual(omitted['window_quality_without_record'],
                         v4.WINDOW_CHANNEL_MISSING)

    def test_spray_block_presence_is_separate_from_flag_value(self):
        body = v4_series(4, spray_flag=None, with_spray=True)
        shape = probe.v4_shape(body)
        self.assertEqual(shape['frame_key_presence']['spray_block'], 4)
        self.assertEqual(shape['frame_key_presence']['spray_flag'], 0)
        self.assertEqual(shape['summary']['application_frames'], 0)
        body = v4_series(4, spray_flag=1, flow=120)
        shape = probe.v4_shape(body)
        self.assertEqual(shape['frame_key_presence']['spray_flag'], 4)
        self.assertEqual(shape['summary']['application_frames'], 4)
        self.assertEqual(shape['spray_flag_histogram'], {'1': 4})

    def test_no_coordinate_value_reaches_the_report(self):
        shape = probe.v4_shape(v4_series(3))
        text = json.dumps(shape, default=str)
        self.assertNotIn('39.123456', text)
        self.assertNotIn('64.654321', text)
        self.assertNotIn('39.12', text)
        # Отрицательный контроль: присутствие позиции при этом видно.
        self.assertEqual(shape['frame_key_presence']['lat'], 3)
        self.assertEqual(shape['summary']['frames_with_position'], 3)

    def test_a_foreign_protobuf_is_not_v4_like(self):
        foreign = f_bytes(7, 'x' * 10) + f_varint(9, 3)
        shape = probe.v4_shape(foreign)
        self.assertEqual(shape['status'], 'NOT_V4_LIKE')
        self.assertEqual(shape['top_other_fields'], [7, 9])

    def test_unknown_frame_field_is_reported_as_a_number_only(self):
        extra = frame(START_MS) + f_bytes(31, 'secret-ish payload')
        body = v4_bytes([extra, frame(START_MS + 1000)], count=2)
        shape = probe.v4_shape(body)
        self.assertEqual(shape['status'], 'V4_LIKE')
        self.assertEqual(shape['frame_other_fields'], [31])
        self.assertIn('UNKNOWN_FRAME_FIELD_31', shape['summary']['warnings'])
        self.assertNotIn('secret-ish', json.dumps(shape, default=str))


# ─── Маршрут ─────────────────────────────────────────────────────────────────

class RouteShape(ProbeCase):

    def test_a_route_body_is_recognised_without_printing_points(self):
        shape = probe.route_shape(route_body(flight_id=673501214))
        self.assertEqual(shape['status'], 'ROUTE_LIKE')
        self.assertEqual(shape['records'], 1)
        self.assertEqual(shape['embedded_flight_ids'], [673501214])
        self.assertEqual(shape['hardware_ids'], ['1ZNBJ7C00A0001'])
        self.assertEqual(shape['point_counts'], [2])
        text = json.dumps(shape, default=str)
        self.assertNotIn('39.123456', text)
        self.assertNotIn('64.654321', text)

    def test_v4_bytes_are_not_a_route_and_vice_versa(self):
        self.assertEqual(probe.route_shape(v4_series(3))['status'],
                         'UNDECODABLE')
        self.assertNotEqual(probe.v4_shape(route_body())['status'], 'V4_LIKE')


# ─── Перепись protobuf ───────────────────────────────────────────────────────

class Census(ProbeCase):

    def test_census_carries_numbers_and_counts_but_no_values(self):
        body = v4_series(3)
        census = probe.protobuf_census(body, depth=2)
        top = {row['key']: row for row in census['top']}
        self.assertEqual(top['2:bytes']['count'], 3)
        self.assertEqual(top['1:varint']['count'], 1)
        self.assertEqual(top['1:varint']['bytes_total'], 0)
        nested = {row['key']: row for row in census['nested']}
        self.assertEqual(nested['2/1:varint']['count'], 3)
        self.assertEqual(nested['2/2:bytes']['count'], 3)
        text = json.dumps(census)
        self.assertNotIn('39.12', text)
        self.assertNotIn(str(START_MS), text)

    def test_opaque_bytes_are_counted_not_guessed(self):
        body = f_bytes(1, b'\xff\xff\xff\xff\xff')
        census = probe.protobuf_census(body, depth=2)
        self.assertEqual(census['opaque_bytes_fields'], 1)
        self.assertEqual(census['nested'], [])


# ─── Секреты ─────────────────────────────────────────────────────────────────

class Secrets(ProbeCase):

    def test_local_markers_cover_the_receiver_list(self):
        local = {m.lower() for m in probe.SECRET_MARKERS}
        for marker in dji_evidence.SECRET_MARKERS:
            self.assertIn(marker.lower(), local, marker)

    def test_a_marker_is_named_and_its_value_is_not(self):
        blob = b'{"url": "https://x.aliyuncs.com/a?OSSAccessKeyId=LTAIabc&Signature=zzz"}'
        found = probe.find_secret_markers(blob)
        self.assertIn('ossaccesskeyid', found)
        self.assertIn('signed-url-query', found)
        self.assertNotIn('LTAIabc', ' '.join(found))

    def test_a_clean_body_reports_nothing(self):
        self.assertEqual(probe.find_secret_markers(v4_series(3)), [])
        self.assertEqual(probe.find_secret_markers(b'{"id": 673501214}'), [])

    def test_inventory_flags_the_file_and_exits_3(self):
        write(self.path('clean.json'), '{"id": 1}')
        write(self.path('leak.json'),
              '{"link": "https://h/o?Expires=1&OSSAccessKeyId=K&Signature=S"}')
        code, text = run_main(['inventory', self.src, '--out', self.out,
                               '--device-label', 'RC-TEST'])
        self.assertEqual(code, probe.EXIT_SECRET_FOUND)
        self.assertIn('secret_flagged=1', text)
        with open(os.path.join(self.out, 'inventory.json'),
                  encoding='utf-8') as handle:
            report = json.load(handle)
        by_name = {f['relpath']: f for f in report['files']}
        self.assertIn('SECRET_MARKER', by_name['leak.json']['flags'])
        self.assertNotIn('SECRET_MARKER', by_name['clean.json']['flags'])
        # Значение подписи в отчёт не попало.
        with open(os.path.join(self.out, 'inventory.json'),
                  encoding='utf-8') as handle:
            self.assertNotIn('Signature=S', handle.read())


# ─── Инвентаризация: источник не тронут, ничего не распаковано ───────────────

class Inventory(ProbeCase):

    def _fill(self):
        write(self.path('flight', 'v4_like.bin'),
              v4_series(6, area_values=[1.0, 1.0, 1.01, 1.01, 1.02, 1.02]))
        write(self.path('flight', 'route_like.bin'), route_body())
        write(self.path('v4.gz'), gzip.compress(v4_series(2)))
        write(self.path('meta.json'), '{"device": "x", "firmware": "y"}')
        make_sqlite(self.path('records.db'))
        write(self.path('records.db-wal'), b'\x37\x7f\x06\x82' + b'\x00' * 28)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as archive:
            archive.writestr('logs/a.txt', 'a' * 1000)
            archive.writestr('../evil.txt', 'b')
            archive.writestr('tool.apk', 'c')
        write(self.path('export.zip'), buf.getvalue())
        write(self.path('noise.bin'), b'\xff\xfe\xfd' * 100)

    def test_source_tree_is_byte_for_byte_untouched_and_nothing_is_extracted(self):
        self._fill()
        before = tree_fingerprint(self.src)
        code, _text = run_main(['inventory', self.src, '--out', self.out,
                                '--device-label', 'RC-TEST'])
        self.assertEqual(code, probe.EXIT_OK)
        self.assertEqual(tree_fingerprint(self.src), before)
        self.assertFalse(os.path.exists(self.path('logs')))
        self.assertFalse(os.path.exists(os.path.join(self.out, 'logs')))
        self.assertEqual(sorted(os.listdir(self.out)),
                         ['inventory.json', 'inventory.md'])

    def test_every_file_is_described_by_its_content(self):
        self._fill()
        report = probe.inventory(self.src, 'RC-TEST')
        by_name = {f['relpath']: f for f in report['files']}
        self.assertEqual(by_name['flight/v4_like.bin']['kind'],
                         'PROTOBUF_CANDIDATE')
        self.assertEqual(
            by_name['flight/v4_like.bin']['details']['v4_shape']['status'],
            'V4_LIKE')
        self.assertEqual(
            by_name['flight/route_like.bin']['details']['route_shape']['status'],
            'ROUTE_LIKE')
        gz = by_name['v4.gz']['details']['gzip']
        self.assertEqual(gz['inner_kind'], 'PROTOBUF_CANDIDATE')
        self.assertEqual(gz['v4_shape']['status'], 'V4_LIKE')
        self.assertEqual(by_name['meta.json']['details']['json_shape']['keys'],
                         ['device', 'firmware'])
        self.assertNotIn('"x"', json.dumps(by_name['meta.json']))
        db = by_name['records.db']
        self.assertEqual(db['kind'], 'SQLITE')
        self.assertIn('SQLITE_WAL_OR_JOURNAL_PRESENT', db['flags'])
        tables = {t['name']: t for t in db['details']['sqlite']['tables']}
        self.assertEqual(tables['flight_log']['rows'], 1)
        self.assertNotIn('the-row-value-must-not-leak', json.dumps(db))
        zip_entry = by_name['export.zip']
        self.assertIn('ZIP_PATH_TRAVERSAL', zip_entry['flags'])
        self.assertIn('ZIP_EXECUTABLE_ENTRY', zip_entry['flags'])
        self.assertEqual(zip_entry['details']['zip']['entries_total'], 3)
        self.assertEqual(by_name['noise.bin']['kind'], 'UNKNOWN_BINARY')
        self.assertEqual(report['totals']['v4_like'], 2)
        self.assertEqual(report['totals']['route_like'], 1)
        self.assertEqual(report['origin'], 'RC')
        self.assertEqual(report['probe_version'], probe.PROBE_VERSION)

    def test_sqlite_without_wal_is_not_flagged(self):
        make_sqlite(self.path('clean.db'))
        report = probe.inventory(self.src, 'RC-TEST')
        self.assertNotIn('SQLITE_WAL_OR_JOURNAL_PRESENT',
                         report['files'][0]['flags'])

    def test_out_inside_source_is_refused(self):
        write(self.path('a.txt'), 'x')
        code, text = run_main(['inventory', self.src, '--out',
                               self.path('probe'), '--device-label', 'RC'])
        self.assertEqual(code, probe.EXIT_PRECONDITION)
        self.assertFalse(os.path.exists(self.path('probe')))
        self.assertIn('ERROR', text)

    def test_missing_source_exits_2(self):
        code, _ = run_main(['inventory', self.path('nope'), '--out', self.out,
                            '--device-label', 'RC'])
        self.assertEqual(code, probe.EXIT_NO_INPUT)

    def test_large_file_gets_hash_and_magic_only(self):
        body = v4_series(50)
        write(self.path('big.bin'), body)
        report = probe.inventory(self.src, 'RC', deep_max_bytes=len(body) - 1)
        entry = report['files'][0]
        self.assertFalse(entry['deep_analysis'])
        self.assertIn('TOO_LARGE_FOR_DEEP_ANALYSIS', entry['flags'])
        self.assertEqual(entry['sha256'],
                         __import__('hashlib').sha256(body).hexdigest())
        self.assertNotIn('v4_shape', entry['details'])
        # Отрицательный контроль: тот же файл в пределе разбирается.
        report = probe.inventory(self.src, 'RC', deep_max_bytes=len(body))
        self.assertTrue(report['files'][0]['deep_analysis'])

    def test_recently_modified_file_is_flagged_and_old_one_is_not(self):
        write(self.path('fresh.bin'), v4_series(2))
        write(self.path('old.bin'), v4_series(2))
        old = time.time() - 3600
        os.utime(self.path('old.bin'), (old, old))
        report = probe.inventory(self.src, 'RC')
        by_name = {f['relpath']: f for f in report['files']}
        self.assertIn('RECENTLY_MODIFIED', by_name['fresh.bin']['flags'])
        self.assertNotIn('RECENTLY_MODIFIED', by_name['old.bin']['flags'])

    def test_markdown_report_has_no_coordinates_and_names_v4_files(self):
        self._fill()
        report = probe.inventory(self.src, 'RC-TEST')
        text = probe.render_markdown(report)
        self.assertIn('flight/v4_like.bin', text)
        self.assertIn('похож на V4', text)
        self.assertNotIn('39.123456', text)
        self.assertNotIn('64.654321', text)

    def test_console_output_is_ascii_only(self):
        self._fill()
        _code, text = run_main(['inventory', self.src, '--out', self.out,
                                '--device-label', 'RC-пульт'])
        self.assertTrue(all(ord(ch) < 128 for ch in text), text)


# ─── Сравнение двух V4 ───────────────────────────────────────────────────────

class CompareV4(ProbeCase):

    def test_identical_bodies(self):
        body = v4_series(5, area_values=[1.0, 1.0, 1.01, 1.01, 1.02])
        a = write(self.path('a.bin'), body)
        b = write(self.path('b.bin'), body)
        result = probe.compare_v4(a, b)
        self.assertTrue(result['identical_bytes'])
        cmp = result['comparison']
        self.assertEqual(cmp['frames_with_equal_t'], 5)
        self.assertEqual(cmp['equal_t_frames_with_equal_area_bits'], 5)
        self.assertEqual(cmp['equal_t_frames_with_equal_position'], 5)
        self.assertEqual(cmp['overlap_ms'], 4000)
        self.assertNotIn('39.123456', json.dumps(result, default=str))
        self.assertIn('NONE', result['identity_proof'])

    def test_shifted_windows_do_not_overlap(self):
        a = write(self.path('a.bin'), v4_series(5))
        b = write(self.path('b.bin'),
                  v4_series(5, start_ms=START_MS + 60 * 60 * 1000))
        cmp = probe.compare_v4(a, b)['comparison']
        self.assertFalse(probe.compare_v4(a, b)['identical_bytes'])
        self.assertEqual(cmp['frames_with_equal_t'], 0)
        self.assertEqual(cmp['overlap_ms'], 0)

    def test_same_times_but_counter_omitted_on_one_side(self):
        a = write(self.path('a.bin'), v4_series(5, area_values=[2.0] * 5))
        b = write(self.path('b.bin'), v4_series(5, area_values=[None] * 5))
        cmp = probe.compare_v4(a, b)['comparison']
        self.assertEqual(cmp['frames_with_equal_t'], 5)
        self.assertEqual(cmp['equal_t_frames_with_equal_area_bits'], 0)
        self.assertEqual(cmp['keys_present_only_in_a'], ['area_native'])
        self.assertEqual(cmp['keys_present_only_in_b'], [])

    def test_undecodable_side_is_reported_not_guessed(self):
        a = write(self.path('a.bin'), v4_series(3))
        b = write(self.path('b.bin'), b'not protobuf at all \xff\xfe')
        result = probe.compare_v4(a, b)
        self.assertTrue(result['a']['decoded'])
        self.assertFalse(result['b']['decoded'])
        self.assertNotIn('comparison', result)


# ─── Снимок ──────────────────────────────────────────────────────────────────

class Snapshot(ProbeCase):

    def test_copies_verifies_and_leaves_the_source_alone(self):
        write(self.path('flight', 'v4.bin'), v4_series(4))
        write(self.path('meta.json'), '{"k": 1}')
        old = time.time() - 7200
        os.utime(self.path('meta.json'), (old, old))
        before = tree_fingerprint(self.src)
        target = os.path.join(self.root, 'snap')
        code, text = run_main(['snapshot', self.src, target,
                               '--device-label', 'RC-TEST'])
        self.assertEqual(code, probe.EXIT_OK, text)
        self.assertEqual(tree_fingerprint(self.src), before)
        with open(os.path.join(target, 'manifest.json'),
                  encoding='utf-8') as handle:
            manifest = json.load(handle)
        self.assertEqual(manifest['status'], 'OK')
        self.assertEqual(manifest['origin'], 'RC')
        self.assertEqual(manifest['totals']['copied'], 2)
        self.assertEqual(manifest['totals']['verified'], 2)
        by_name = {f['relpath']: f for f in manifest['files']}
        self.assertTrue(by_name['flight/v4.bin']['verified'])
        self.assertEqual(by_name['flight/v4.bin']['sha256'],
                         probe.sha256_file(os.path.join(target, 'flight',
                                                        'v4.bin')))
        self.assertNotIn('RECENTLY_MODIFIED', by_name['meta.json']['flags'])
        self.assertIn('RECENTLY_MODIFIED', by_name['flight/v4.bin']['flags'])
        # Время изменения копии равно времени источника.
        self.assertEqual(int(os.stat(os.path.join(target, 'meta.json')).st_mtime),
                         int(old))

    def test_flagged_file_is_skipped_unless_asked(self):
        write(self.path('leak.txt'), 'token: bearer abc')
        write(self.path('ok.txt'), 'fine')
        target = os.path.join(self.root, 'snap')
        manifest = probe.snapshot(self.src, target, 'RC')
        self.assertEqual([s['relpath'] for s in manifest['skipped']],
                         ['leak.txt'])
        self.assertFalse(os.path.exists(os.path.join(target, 'leak.txt')))
        self.assertTrue(os.path.exists(os.path.join(target, 'ok.txt')))
        target2 = os.path.join(self.root, 'snap2')
        manifest = probe.snapshot(self.src, target2, 'RC', include_flagged=True)
        self.assertEqual(manifest['skipped'], [])
        self.assertTrue(os.path.exists(os.path.join(target2, 'leak.txt')))
        by_name = {f['relpath']: f for f in manifest['files']}
        self.assertIn('SECRET_MARKER', by_name['leak.txt']['flags'])

    def test_non_empty_target_and_nested_target_are_refused(self):
        write(self.path('a.txt'), 'a')
        busy = os.path.join(self.root, 'busy')
        write(os.path.join(busy, 'x'), 'x')
        code, text = run_main(['snapshot', self.src, busy,
                               '--device-label', 'RC'])
        self.assertEqual(code, probe.EXIT_PRECONDITION)
        self.assertIn('not empty', text)
        code, text = run_main(['snapshot', self.src, self.path('inner'),
                               '--device-label', 'RC'])
        self.assertEqual(code, probe.EXIT_PRECONDITION)
        self.assertFalse(os.path.exists(self.path('inner')))

    def test_verify_copy_catches_a_tampered_copy(self):
        src = write(self.path('a.bin'), v4_series(3))
        good = write(os.path.join(self.root, 'good.bin'), v4_series(3))
        bad = write(os.path.join(self.root, 'bad.bin'), v4_series(3) + b'\x00')
        self.assertTrue(probe.verify_copy(src, good)[2])
        self.assertFalse(probe.verify_copy(src, bad)[2])


# ─── CLI ─────────────────────────────────────────────────────────────────────

class Cli(ProbeCase):

    def test_help_of_every_subcommand_exits_zero(self):
        for argv in (['--help'], ['inventory', '--help'], ['probe', '--help'],
                     ['compare-v4', '--help'], ['snapshot', '--help']):
            with self.assertRaises(SystemExit) as ctx:
                with contextlib.redirect_stdout(io.StringIO()):
                    probe.main(argv)
            self.assertEqual(ctx.exception.code, 0, argv)

    def test_probe_single_file_writes_json_and_prints_ascii(self):
        path = write(self.path('v4.bin'), v4_series(3))
        out = os.path.join(self.root, 'one.json')
        code, text = run_main(['probe', path, '--json', out])
        self.assertEqual(code, probe.EXIT_OK)
        self.assertIn('v4_shape=V4_LIKE frames=3', text)
        self.assertTrue(all(ord(ch) < 128 for ch in text))
        with open(out, encoding='utf-8') as handle:
            document = json.load(handle)
        self.assertEqual(document['origin'], 'RC')
        self.assertNotIn('39.123456', json.dumps(document))

    def test_probe_missing_file_exits_2(self):
        code, _ = run_main(['probe', self.path('nope.bin')])
        self.assertEqual(code, probe.EXIT_NO_INPUT)

    def test_compare_cli(self):
        a = write(self.path('a.bin'), v4_series(3))
        b = write(self.path('b.bin'), v4_series(3))
        out = os.path.join(self.root, 'cmp.json')
        code, text = run_main(['compare-v4', a, b, '--json', out])
        self.assertEqual(code, probe.EXIT_OK)
        self.assertIn('identical_bytes=True', text)
        self.assertIn('equal_t=3', text)
        self.assertTrue(os.path.exists(out))


if __name__ == '__main__':
    unittest.main()
