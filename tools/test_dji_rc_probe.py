# -*- coding: utf-8 -*-
"""Тесты tools/dji_rc_probe.py. Без сети, без браузера, без базы, без пульта.

    python tools/test_dji_rc_probe.py
    python -m unittest tools.test_dji_rc_probe

Все материалы здесь СИНТЕТИЧЕСКИЕ: тела V4 и маршрутов собраны теми же
protobuf-помощниками, что в `tests/test_dji_area_core.py`, по номерам полей,
доказанным на августовском корпусе. Ни один из них не является файлом
пульта, и прохождение этих тестов не доказывает доступ к данным DJI
(ТЗ §0.7). Они доказывают другое: что инструмент различает присутствие и
ноль, не пишет в источник и не перезаписывает ничего, не печатает координат
и значений, и говорит «не знаю», когда не знает.

Классы с префиксом `Review` закрывают замечания ревизии этапа 0А от
30.10.2026 (пункты 1–4, 6, 8, 9) -- каждый сценарий ревизии здесь обязан
давать правильное поведение, а не прежний дефект. У каждого контроля есть
отрицательный случай: проверка, которая зелена и при верном, и при неверном
поведении, проверкой не является.
"""

import base64
import contextlib
import gzip
import hashlib
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
from unittest.mock import patch

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
SECRET = 'SYNTHETIC_SECRET_VALUE_7f3a'


def frame(t_ms, area=None, mode=4, width=6.0, spray_flag=None, flow=None,
          quantity=None, lat=LAT, lng=LNG, vx=3.0, vy=0.0, with_spray=True):
    body = b''
    if t_ms is not None:
        body += f_varint(v4.F_TIME_MS, t_ms)
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


def age(path, seconds=3600):
    old = time.time() - seconds
    os.utime(path, (old, old))
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


def make_sqlite(path, secret_value='the-row-value-must-not-leak',
                tables=('flight_log', 'settings')):
    con = sqlite3.connect(path)
    con.execute('CREATE TABLE "%s" (id INTEGER PRIMARY KEY, note TEXT)'
                % tables[0])
    con.execute('INSERT INTO "%s" (note) VALUES (?)' % tables[0],
                (secret_value,))
    for name in tables[1:]:
        con.execute('CREATE TABLE "%s" (k TEXT, v TEXT)' % name)
    con.commit()
    con.close()


def make_zip(path, entries, compression=zipfile.ZIP_DEFLATED):
    with zipfile.ZipFile(path, 'w', compression=compression) as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return path


def mark_zip_entry_encrypted(path, name):
    """Ставит бит шифрования (бит 0 флагов) записи `name` в локальном
    заголовке и в центральном каталоге. `zipfile.writestr` этот бит
    сбрасывает, поэтому синтетическая «зашифрованная» запись делается
    правкой байтов; содержимое при этом не шифруется -- важен только флаг."""
    with open(path, 'rb') as handle:
        blob = bytearray(handle.read())
    encoded = name.encode('utf-8')
    patched = 0
    for signature, flag_at, len_at, name_at in (
            (b'PK\x03\x04', 6, 26, 30), (b'PK\x01\x02', 8, 28, 46)):
        start = 0
        while True:
            pos = blob.find(signature, start)
            if pos < 0:
                break
            length = struct.unpack_from('<H', blob, pos + len_at)[0]
            if blob[pos + name_at:pos + name_at + length] == encoded:
                flags = struct.unpack_from('<H', blob, pos + flag_at)[0]
                struct.pack_into('<H', blob, pos + flag_at, flags | 0x1)
                patched += 1
            start = pos + 4
    assert patched == 2, patched
    with open(path, 'wb') as handle:
        handle.write(bytes(blob))
    return path


def dump_document(items):
    """Синтетический дамп `--sources --dry-run` (`write_sources_dry_run`)."""
    sources = []
    for item in items:
        flight_id, source_type, body = item[:3]
        declared_sha = item[3] if len(item) > 3 else None
        declared_size = item[4] if len(item) > 4 else None
        sources.append({
            'flight_id': flight_id,
            'source_type': source_type,
            'captured_at_utc': '2026-10-30T00:00:00Z',
            'body_b64': base64.b64encode(body).decode('ascii'),
            'sha256': declared_sha or hashlib.sha256(body).hexdigest(),
            'size_bytes': len(body) if declared_size is None else declared_size,
            'request_context': {'path': '/synthetic', 'association': 'url_path'},
            'capture_run_id': 'synthetic-run',
            'parser_version': None,
            'schema_version': None,
            'api_status': 0,
        })
    return {'dry_run': True, 'nothing_was_queued': True,
            'capture_run_id': 'synthetic-run', 'count': len(sources),
            'flights': [], 'sources': sources}


def make_dir_link(target, link):
    """Символическая ссылка; на Windows без привилегии -- junction."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return 'symlink'
    except (OSError, NotImplementedError):
        pass
    if os.name == 'nt':
        try:
            import _winapi
            _winapi.CreateJunction(target, link)
            return 'junction'
        except (OSError, AttributeError, ImportError):
            pass
    return None


def run_main(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = probe.main(argv)
    return code, out.getvalue()


def read_json(path):
    with open(path, encoding='utf-8') as handle:
        return json.load(handle)


def read_text(path):
    with open(path, encoding='utf-8') as handle:
        return handle.read()


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

    def test_duplicate_times_are_counted_in_the_shape(self):
        dup = v4_bytes([frame(START_MS), frame(START_MS),
                        frame(START_MS + 1000)], count=3)
        shape = probe.v4_shape(dup)
        self.assertEqual(shape['duplicate_t_frames'], 1)
        self.assertTrue(shape['t_monotone_nondecreasing'])
        unordered = v4_bytes([frame(START_MS + 1000), frame(START_MS)], count=2)
        self.assertFalse(probe.v4_shape(unordered)['t_monotone_nondecreasing'])
        self.assertEqual(probe.v4_shape(v4_series(3))['duplicate_t_frames'], 0)


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

    def test_streaming_scan_finds_a_marker_on_a_chunk_boundary(self):
        chunk = 4096
        body = b'A' * (chunk - 4) + b'password=' + SECRET.encode() + b'B' * 100
        sha, markers, read, truncated = probe.stream_hash_and_scan(
            io.BytesIO(body), chunk=chunk)
        self.assertEqual(markers, ['password'])
        self.assertEqual(read, len(body))
        self.assertFalse(truncated)
        self.assertEqual(sha, hashlib.sha256(body).hexdigest())
        # Отрицательный контроль: без перекрытия маркер на стыке теряется.
        with patch.object(probe, 'SCAN_OVERLAP', 0):
            _s, markers, _r, _t = probe.stream_hash_and_scan(
                io.BytesIO(body), chunk=chunk)
        self.assertEqual(markers, [])
        # Предел: прочитано ровно limit байт и сказано, что остались ещё.
        _s, _m, read, truncated = probe.stream_hash_and_scan(
            io.BytesIO(body), limit=10, chunk=chunk)
        self.assertEqual(read, 10)
        self.assertTrue(truncated)


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
                         ['inventory.json', 'inventory.md',
                          'inventory_transfer.json', 'inventory_transfer.md'])

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
        self.assertEqual(report['origin_basis'], 'DECLARED_BY_OPERATOR')
        self.assertEqual(report['probe_version'], probe.PROBE_VERSION)
        self.assertEqual(report['status'], 'COMPLETE')
        for entry in report['files']:
            self.assertEqual(entry['secret_scan']['status'], probe.SCAN_CLEAN,
                             entry['relpath'])

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
        # Содержимое всё равно проверено на маркеры целиком.
        self.assertEqual(entry['secret_scan']['status'], probe.SCAN_CLEAN)
        self.assertTrue(entry['secret_scan']['content_fully_scanned'])
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

    def test_links_inside_the_source_are_not_followed(self):
        foreign_dir = os.path.join(self.root, 'foreign')
        write(os.path.join(foreign_dir, 'outside.txt'), 'not part of the RC')
        write(self.path('inside.txt'), 'inside')
        kind = make_dir_link(foreign_dir, self.path('link_to_foreign'))
        if kind is None:
            self.skipTest('neither symlink nor junction available here')
        report = probe.inventory(self.src, 'RC')
        self.assertEqual([f['relpath'] for f in report['files']],
                         ['inside.txt'])
        # Отрицательный контроль: без ссылки-папки тот же файл виден.
        write(self.path('plain', 'outside.txt'), 'not part of the RC')
        report = probe.inventory(self.src, 'RC')
        self.assertIn('plain/outside.txt', [f['relpath'] for f in report['files']])


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
        self.assertEqual(cmp['pairing'], 'UNAMBIGUOUS')
        self.assertNotIn('39.123456', json.dumps(result, default=str))
        self.assertIn('NONE', result['identity_proof'])
        self.assertEqual(result['origin_basis_a'], 'DECLARED_BY_OPERATOR')

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
        # Свежий файл (< 60 с) -- завершённость не доказана: INCONCLUSIVE,
        # код 7. Прежняя редакция называла такой снимок OK (ревизия, п. 4).
        self.assertEqual(code, probe.EXIT_UNSTABLE, text)
        self.assertEqual(tree_fingerprint(self.src), before)
        with open(os.path.join(target, 'manifest.json'),
                  encoding='utf-8') as handle:
            manifest = json.load(handle)
        self.assertEqual(manifest['status'], 'INCONCLUSIVE')
        self.assertEqual(manifest['conditions'], ['INCONCLUSIVE'])
        self.assertEqual(manifest['origin'], 'RC')
        self.assertEqual(manifest['origin_basis'], 'DECLARED_BY_OPERATOR')
        self.assertEqual(manifest['totals']['copied'], 2)
        self.assertEqual(manifest['totals']['verified'], 2)
        self.assertEqual(manifest['totals']['stable'], 1)
        self.assertEqual(manifest['totals']['inconclusive'], 1)
        by_name = {f['relpath']: f for f in manifest['files']}
        self.assertTrue(by_name['flight/v4.bin']['verified'])
        self.assertEqual(by_name['flight/v4.bin']['sha256'],
                         probe.sha256_file(os.path.join(target, 'flight',
                                                        'v4.bin')))
        self.assertNotIn('RECENTLY_MODIFIED', by_name['meta.json']['flags'])
        self.assertIn('RECENTLY_MODIFIED', by_name['flight/v4.bin']['flags'])
        self.assertEqual(by_name['meta.json']['stability'], 'STABLE')
        self.assertEqual(by_name['flight/v4.bin']['stability'], 'INCONCLUSIVE')
        # Время изменения копии равно времени источника.
        self.assertEqual(int(os.stat(os.path.join(target, 'meta.json')).st_mtime),
                         int(old))
        # Положительный контроль: те же файлы, состарившиеся, -- OK и код 0.
        os.utime(self.path('flight', 'v4.bin'), (old, old))
        target2 = os.path.join(self.root, 'snap2')
        code, text = run_main(['snapshot', self.src, target2,
                               '--device-label', 'RC-TEST'])
        self.assertEqual(code, probe.EXIT_OK, text)
        manifest = read_json(os.path.join(target2, 'manifest.json'))
        self.assertEqual(manifest['status'], 'OK')
        self.assertEqual(manifest['conditions'], [])
        self.assertEqual(manifest['totals']['stable'], 2)

    def test_flagged_file_is_skipped_unless_asked(self):
        write(self.path('leak.txt'), 'token: bearer abc')
        write(self.path('ok.txt'), 'fine')
        target = os.path.join(self.root, 'snap')
        manifest = probe.snapshot(self.src, target, 'RC')
        self.assertEqual([s['relpath'] for s in manifest['skipped']],
                         ['leak.txt'])
        self.assertEqual(manifest['skipped'][0]['reason'], 'MARKER_FOUND')
        self.assertFalse(os.path.exists(os.path.join(target, 'leak.txt')))
        self.assertTrue(os.path.exists(os.path.join(target, 'ok.txt')))
        self.assertIn('PARTIAL', manifest['conditions'])
        target2 = os.path.join(self.root, 'snap2')
        manifest = probe.snapshot(self.src, target2, 'RC', include_flagged=True)
        self.assertEqual(manifest['skipped'], [])
        self.assertTrue(os.path.exists(os.path.join(target2, 'leak.txt')))
        by_name = {f['relpath']: f for f in manifest['files']}
        self.assertIn('SECRET_MARKER', by_name['leak.txt']['flags'])
        self.assertEqual(by_name['leak.txt']['secret_scan_status'],
                         'MARKER_FOUND')

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
                     ['compare-v4', '--help'], ['snapshot', '--help'],
                     ['extract-dump', '--help'], ['match', '--help']):
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
        self.assertEqual(document['origin_basis'], 'DECLARED_BY_OPERATOR')
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
        self.assertIn('pairing=UNAMBIGUOUS', text)
        self.assertTrue(os.path.exists(out))


# ─── Ревизия, п. 1: SQLite и особые символы в путях ──────────────────────────

class ReviewSqlitePaths(ProbeCase):

    SPECIAL_NAMES = ('records#copy.db', '100%sure.db', 'with space.db',
                     'пульт-01.db', 'a+b.db', 'a&b=c.db', 'tilde~1.db')

    def _assert_shape_ok(self, path):
        shape = probe.sqlite_shape(path)
        self.assertEqual(shape['status'], 'OK', (path, shape))
        self.assertIsNone(shape['error'])
        self.assertTrue(shape['opened_path_verified'])
        self.assertEqual([t['name'] for t in shape['tables']],
                         ['flight_log', 'settings'])
        self.assertEqual(shape['tables'][0]['rows'], 1)

    def test_special_characters_in_the_name_open_the_right_file_and_create_nothing(self):
        for name in self.SPECIAL_NAMES:
            make_sqlite(self.path(name))
        before = tree_fingerprint(self.src)
        listing = sorted(os.listdir(self.src))
        for name in self.SPECIAL_NAMES:
            self._assert_shape_ok(self.path(name))
        self.assertEqual(sorted(os.listdir(self.src)), listing)
        self.assertEqual(tree_fingerprint(self.src), before)
        # То же через опись: ни одного нового файла, у каждой базы -- таблицы.
        code, _text = run_main(['inventory', self.src, '--out', self.out,
                                '--device-label', 'RC'])
        self.assertEqual(code, probe.EXIT_OK)
        self.assertEqual(sorted(os.listdir(self.src)), listing)
        report = read_json(os.path.join(self.out, 'inventory.json'))
        for entry in report['files']:
            self.assertEqual(entry['kind'], 'SQLITE', entry['relpath'])
            self.assertEqual(entry['details']['sqlite']['status'], 'OK')
            self.assertEqual(len(entry['details']['sqlite']['tables']), 2)

    def test_special_characters_in_the_source_and_output_directories(self):
        src = os.path.join(self.root, 'raw #1 100% пульт')
        out = os.path.join(self.root, 'probe #1 100% пульт')
        os.makedirs(src)
        make_sqlite(os.path.join(src, 'records#copy.db'))
        write(os.path.join(src, 'v4 #1.bin'), v4_series(3))
        before = tree_fingerprint(src)
        code, text = run_main(['inventory', src, '--out', out,
                               '--device-label', 'RC'])
        self.assertEqual(code, probe.EXIT_OK, text)
        self.assertEqual(tree_fingerprint(src), before)
        report = read_json(os.path.join(out, 'inventory.json'))
        by_name = {f['relpath']: f for f in report['files']}
        self.assertEqual(by_name['records#copy.db']['details']['sqlite']['status'],
                         'OK')
        self.assertEqual(by_name['v4 #1.bin']['details']['v4_shape']['status'],
                         'V4_LIKE')
        target = os.path.join(self.root, 'snap #1 100% пульт')
        manifest = probe.snapshot(src, target, 'RC')
        self.assertEqual(manifest['totals']['verified'], 2)
        self.assertEqual(tree_fingerprint(src), before)

    def test_the_review_scenario_hash_in_filename(self):
        db = self.path('records#copy.db')
        con = sqlite3.connect(db)
        con.execute('CREATE TABLE synthetic (id INTEGER)')
        con.commit()
        con.close()
        before = set(os.listdir(self.src))
        code, _text = run_main(['inventory', self.src, '--out', self.out,
                                '--device-label', 'SYNTHETIC'])
        report = read_json(os.path.join(self.out, 'inventory.json'))
        result = report['files'][0]['details']['sqlite']
        self.assertEqual(set(os.listdir(self.src)), before)
        self.assertEqual(len(result['tables']), 1)
        self.assertIsNone(result['error'])
        self.assertEqual(code, probe.EXIT_OK)

    def test_missing_file_is_an_error_not_an_empty_database(self):
        missing = self.path('nope#1.db')
        shape = probe.sqlite_shape(missing)
        self.assertEqual(shape['status'], 'ERROR')
        self.assertEqual(shape['error'], 'OperationalError')
        self.assertEqual(shape['tables'], [])
        self.assertFalse(shape['opened_path_verified'])
        self.assertEqual(os.listdir(self.src), [])

    def test_a_fake_sqlite_header_is_an_error_not_an_empty_database(self):
        write(self.path('fake#1.db'), b'SQLite format 3\x00' + b'\x00' * 200)
        report = probe.inventory(self.src, 'RC')
        entry = report['files'][0]
        self.assertEqual(entry['kind'], 'SQLITE')
        self.assertEqual(entry['details']['sqlite']['status'], 'ERROR')
        self.assertIsNotNone(entry['details']['sqlite']['error'])
        self.assertIn('SQLITE_READ_ERROR', entry['flags'])
        self.assertEqual(os.listdir(self.src), ['fake#1.db'])

    def test_opened_path_is_verified_against_the_request(self):
        make_sqlite(self.path('a.db'))
        make_sqlite(self.path('b.db'), tables=('other',))
        # Отрицательный контроль: URI на другой файл -- отказ до чтения таблиц.
        uri_of_b = probe.sqlite_uri(self.path('b.db'))
        with patch.object(probe, 'sqlite_uri', lambda path: uri_of_b):
            shape = probe.sqlite_shape(self.path('a.db'))
        self.assertEqual(shape['status'], 'ERROR')
        self.assertEqual(shape['error'], 'OPENED_PATH_MISMATCH')
        self.assertEqual(shape['tables'], [])

    def test_unquoted_uri_would_open_another_file(self):
        """Отрицательный контроль дефекта ревизии: без экранирования `#`
        SQLite открывает другой путь. Проверяется самим SQLite, чтобы тест
        отличал исправленный код от прежнего."""
        make_sqlite(self.path('records#copy.db'))
        naive = 'file:%s?mode=ro&immutable=1' % self.path(
            'records#copy.db').replace('\\', '/')
        self.assertNotEqual(naive, probe.sqlite_uri(self.path('records#copy.db')))
        self.assertIn('%23', probe.sqlite_uri(self.path('records#copy.db')))
        self.assertTrue(probe.sqlite_uri(self.path('records#copy.db')).endswith(
            '?mode=ro&immutable=1'))


# ─── Ревизия, п. 2: пути вывода не могут изменить источник ───────────────────

class ReviewOutputGuards(ProbeCase):

    def test_probe_refuses_to_write_over_its_input(self):
        initial = b'synthetic source, do not change'
        a = write(self.path('a.txt'), initial)
        code, text = run_main(['probe', a, '--json', a])
        self.assertEqual(code, probe.EXIT_PRECONDITION)
        self.assertIn('ERROR', text)
        with open(a, 'rb') as handle:
            self.assertEqual(handle.read(), initial)

    def test_compare_refuses_to_write_over_either_input(self):
        initial = v4_series(3)
        a = write(self.path('a.bin'), initial)
        b = write(self.path('b.bin'), initial)
        for target in (a, b, os.path.join(self.src, '..', 'raw', 'a.bin')):
            code, _text = run_main(['compare-v4', a, b, '--json', target])
            self.assertEqual(code, probe.EXIT_PRECONDITION, target)
        with open(a, 'rb') as handle:
            self.assertEqual(handle.read(), initial)
        with open(b, 'rb') as handle:
            self.assertEqual(handle.read(), initial)

    def test_output_inside_the_input_folder_is_refused_even_as_a_new_file(self):
        a = write(self.path('a.bin'), v4_series(3))
        before = tree_fingerprint(self.src)
        code, _text = run_main(['probe', a, '--json', self.path('new.json')])
        self.assertEqual(code, probe.EXIT_PRECONDITION)
        self.assertEqual(tree_fingerprint(self.src), before)
        # Положительный контроль: вне папки входа запись разрешена.
        out = os.path.join(self.root, 'new.json')
        code, _text = run_main(['probe', a, '--json', out])
        self.assertEqual(code, probe.EXIT_OK)
        self.assertTrue(os.path.exists(out))

    def test_existing_output_is_never_overwritten(self):
        a = write(self.path('a.bin'), v4_series(3))
        earlier = write(os.path.join(self.root, 'earlier.json'),
                        '{"earlier": "report"}')
        code, _text = run_main(['probe', a, '--json', earlier])
        self.assertEqual(code, probe.EXIT_PRECONDITION)
        self.assertEqual(read_text(earlier), '{"earlier": "report"}')
        # Опись: прежний отчёт в той же папке -- отказ ещё до разбора.
        write(os.path.join(self.out, 'inventory.json'), '{"earlier": 1}')
        code, text = run_main(['inventory', self.src, '--out', self.out,
                               '--device-label', 'RC'])
        self.assertEqual(code, probe.EXIT_PRECONDITION)
        self.assertIn('already exists', text)
        self.assertEqual(read_text(os.path.join(self.out, 'inventory.json')),
                         '{"earlier": 1}')
        self.assertEqual(os.listdir(self.out), ['inventory.json'])

    def test_hard_link_of_an_input_is_refused(self):
        initial = v4_series(3)
        a = write(self.path('a.bin'), initial)
        link = os.path.join(self.root, 'alias.bin')
        try:
            os.link(a, link)
        except OSError:
            self.skipTest('hard links unavailable here')
        self.assertTrue(probe.same_file(a, link))
        code, _text = run_main(['probe', a, '--json', link])
        self.assertEqual(code, probe.EXIT_PRECONDITION)
        with open(a, 'rb') as handle:
            self.assertEqual(handle.read(), initial)

    def test_output_directory_as_a_link_into_the_source_is_refused(self):
        inside = self.path('inside')
        os.makedirs(inside)
        write(self.path('a.txt'), 'synthetic input')
        link = os.path.join(self.root, 'link')
        kind = make_dir_link(inside, link)
        if kind is None:
            self.skipTest('neither symlink nor junction available here')
        before = tree_fingerprint(self.src)
        code, text = run_main(['inventory', self.src, '--out', link,
                               '--device-label', 'SYNTHETIC'])
        self.assertEqual(code, probe.EXIT_PRECONDITION, (kind, text))
        self.assertFalse(os.path.exists(os.path.join(inside, 'inventory.json')))
        self.assertEqual(tree_fingerprint(self.src), before)
        code, text = run_main(['snapshot', self.src, link,
                               '--device-label', 'SYNTHETIC'])
        self.assertEqual(code, probe.EXIT_PRECONDITION, (kind, text))
        self.assertEqual(os.listdir(inside), [])
        # Ссылка, чей хвост ещё не создан: родитель -- ссылка внутрь источника.
        code, text = run_main(['inventory', self.src, '--out',
                               os.path.join(link, 'deeper'),
                               '--device-label', 'SYNTHETIC'])
        self.assertEqual(code, probe.EXIT_PRECONDITION, (kind, text))
        self.assertEqual(os.listdir(inside), [])
        # Положительный контроль: ссылка на папку ВНЕ источника допустима.
        outside = os.path.join(self.root, 'outside')
        os.makedirs(outside)
        link2 = os.path.join(self.root, 'link2')
        self.assertIsNotNone(make_dir_link(outside, link2))
        code, text = run_main(['inventory', self.src, '--out', link2,
                               '--device-label', 'SYNTHETIC'])
        self.assertEqual(code, probe.EXIT_OK, text)
        self.assertTrue(os.path.exists(os.path.join(outside, 'inventory.json')))

    def test_is_within_uses_real_paths(self):
        inside = self.path('inside')
        os.makedirs(inside)
        link = os.path.join(self.root, 'link')
        if make_dir_link(inside, link) is None:
            self.skipTest('neither symlink nor junction available here')
        self.assertTrue(probe.is_within(link, self.src))
        self.assertTrue(probe.is_within(os.path.join(link, 'x', 'y'), self.src))
        self.assertFalse(probe.is_within(self.root, self.src))
        # Отрицательный контроль: строковое сравнение этого не видит.
        self.assertFalse(os.path.normcase(os.path.abspath(link)).startswith(
            os.path.normcase(os.path.abspath(self.src)) + os.sep))

    def test_every_command_leaves_every_input_tree_untouched(self):
        write(self.path('a.bin'), v4_series(3, area_values=[1.0, 1.01, 1.02]))
        write(self.path('b.bin'), v4_series(3, start_ms=START_MS + 500))
        make_sqlite(self.path('records.db'))
        age(self.path('a.bin'))
        age(self.path('b.bin'))
        age(self.path('records.db'))
        dump = write(os.path.join(self.root, 'dump', 'sources_dry_run_x.json'),
                     json.dumps(dump_document([(673501214, 'v4', v4_series(3))])))
        before_src = tree_fingerprint(self.src)
        before_dump = tree_fingerprint(os.path.dirname(dump))
        steps = [
            ['inventory', self.src, '--out', self.out, '--device-label', 'RC'],
            ['probe', self.path('a.bin'), '--json',
             os.path.join(self.root, 'a.json')],
            ['compare-v4', self.path('a.bin'), self.path('b.bin'), '--json',
             os.path.join(self.root, 'cmp.json')],
            ['snapshot', self.src, os.path.join(self.root, 'snap'),
             '--device-label', 'RC'],
            ['extract-dump', dump, '--out', os.path.join(self.root, 'cloud')],
            ['match', os.path.join(self.out, 'inventory.json'), dump, '--out',
             os.path.join(self.root, 'match')],
        ]
        for argv in steps:
            code, text = run_main(argv)
            self.assertIn(code, (probe.EXIT_OK, probe.EXIT_NOTHING),
                          (argv[0], text))
            self.assertEqual(tree_fingerprint(self.src), before_src, argv[0])
            self.assertEqual(tree_fingerprint(os.path.dirname(dump)),
                             before_dump, argv[0])


# ─── Ревизия, п. 3: единая политика секретов ─────────────────────────────────

class ReviewSecretPolicy(ProbeCase):

    def _snapshot(self, **kw):
        # Источник состарен: здесь проверяется политика секретов, а не
        # стабильность (та -- в ReviewSourceStability).
        for dirpath, _dirs, names in os.walk(self.src):
            for name in names:
                age(os.path.join(dirpath, name))
        target = os.path.join(self.root, 'snap_%d' % len(os.listdir(self.root)))
        return probe.snapshot(self.src, target, 'SYNTHETIC', **kw), target

    def test_marker_inside_gzip_is_found_by_both_inventory_and_snapshot(self):
        write(self.path('credentials.gz'),
              gzip.compress(('password=' + SECRET).encode()))
        write(self.path('clean.gz'), gzip.compress(b'nothing here'))
        inv = probe.inventory(self.src, 'SYNTHETIC')
        by_name = {f['relpath']: f for f in inv['files']}
        self.assertEqual(inv['totals']['flagged_secret'], 1)
        self.assertEqual(by_name['credentials.gz']['secret_scan']['status'],
                         'MARKER_FOUND')
        self.assertIn('gzip_inner', by_name['credentials.gz']['secret_scan']['where'])
        self.assertEqual(by_name['clean.gz']['secret_scan']['status'],
                         probe.SCAN_CLEAN)
        manifest, target = self._snapshot()
        self.assertEqual([s['relpath'] for s in manifest['skipped']],
                         ['credentials.gz'])
        self.assertEqual(manifest['skipped'][0]['reason'], 'MARKER_FOUND')
        self.assertFalse(os.path.exists(os.path.join(target, 'credentials.gz')))
        self.assertTrue(os.path.exists(os.path.join(target, 'clean.gz')))
        self.assertEqual(manifest['status'], 'PARTIAL')
        self.assertNotIn(SECRET, json.dumps(manifest))
        self.assertNotIn(SECRET, json.dumps(inv))

    def test_marker_inside_a_zip_entry_is_found_without_extracting(self):
        make_zip(self.path('credentials.zip'),
                 [('inner.txt', ('password=' + SECRET).encode())])
        make_zip(self.path('clean.zip'), [('inner.txt', b'no marker inside')])
        before = tree_fingerprint(self.src)
        inv = probe.inventory(self.src, 'SYNTHETIC')
        self.assertEqual(tree_fingerprint(self.src), before)
        self.assertFalse(os.path.exists(self.path('inner.txt')))
        by_name = {f['relpath']: f for f in inv['files']}
        self.assertEqual(inv['totals']['flagged_secret'], 1)
        self.assertEqual(by_name['credentials.zip']['secret_scan']['status'],
                         'MARKER_FOUND')
        self.assertIn('zip_entry', by_name['credentials.zip']['secret_scan']['where'])
        self.assertIn('SECRET_MARKER_INSIDE_ZIP', by_name['credentials.zip']['flags'])
        self.assertEqual(by_name['clean.zip']['secret_scan']['status'],
                         probe.SCAN_CLEAN)
        manifest, target = self._snapshot()
        self.assertEqual([s['relpath'] for s in manifest['skipped']],
                         ['credentials.zip'])
        self.assertTrue(os.path.exists(os.path.join(target, 'clean.zip')))
        self.assertNotIn(SECRET, json.dumps(inv))

    def test_file_above_the_deep_limit_is_still_scanned_in_full(self):
        limit = 4096
        with open(self.path('large.bin'), 'wb') as handle:
            handle.seek(limit)
            handle.write(('password=' + SECRET).encode())
        write(self.path('large_clean.bin'), b'\x00' * (limit + 50))
        inv = probe.inventory(self.src, 'SYNTHETIC', deep_max_bytes=limit)
        by_name = {f['relpath']: f for f in inv['files']}
        self.assertIn('TOO_LARGE_FOR_DEEP_ANALYSIS', by_name['large.bin']['flags'])
        self.assertEqual(by_name['large.bin']['secret_scan']['status'],
                         'MARKER_FOUND')
        self.assertTrue(by_name['large.bin']['secret_scan']['content_fully_scanned'])
        self.assertEqual(by_name['large_clean.bin']['secret_scan']['status'],
                         probe.SCAN_CLEAN)
        self.assertEqual(inv['totals']['flagged_secret'], 1)
        manifest, target = self._snapshot(deep_max_bytes=limit)
        self.assertEqual([s['relpath'] for s in manifest['skipped']],
                         ['large.bin'])
        self.assertFalse(os.path.exists(os.path.join(target, 'large.bin')))
        copied = {f['relpath']: f for f in manifest['files']}
        self.assertEqual(copied['large_clean.bin']['secret_scan_status'],
                         probe.SCAN_CLEAN)

    def test_large_gzip_is_streamed_and_scanned(self):
        inner = bytes(range(256)) * 50 + ('password=' + SECRET).encode()
        write(self.path('large.gz'), gzip.compress(inner))
        inv = probe.inventory(self.src, 'SYNTHETIC', deep_max_bytes=100)
        entry = inv['files'][0]
        self.assertIn('TOO_LARGE_FOR_DEEP_ANALYSIS', entry['flags'])
        self.assertEqual(entry['secret_scan']['status'], 'MARKER_FOUND')
        self.assertIn('gzip_inner', entry['secret_scan']['where'])
        self.assertGreater(entry['secret_scan']['container_bytes_scanned'], 12800)

    def test_encrypted_zip_entry_makes_the_scan_partial_and_is_not_copied_by_default(self):
        path = make_zip(self.path('locked.zip'),
                        [('plain.txt', b'visible'),
                         ('locked.txt', b'cannot be read without the key')])
        mark_zip_entry_encrypted(path, 'locked.txt')
        with zipfile.ZipFile(path) as archive:
            self.assertTrue(archive.getinfo('locked.txt').flag_bits & 0x1)
            self.assertFalse(archive.getinfo('plain.txt').flag_bits & 0x1)
        inv = probe.inventory(self.src, 'SYNTHETIC')
        entry = inv['files'][0]
        self.assertEqual(entry['secret_scan']['status'], 'PARTIAL')
        self.assertIn('ZIP_ENCRYPTED_ENTRY', entry['secret_scan']['reasons'])
        self.assertIn('SECRET_SCAN_PARTIAL', entry['flags'])
        self.assertEqual(inv['status'], 'PARTIAL')
        manifest, target = self._snapshot()
        self.assertEqual(manifest['skipped'][0]['reason'], 'PARTIAL')
        self.assertFalse(os.path.exists(os.path.join(target, 'locked.zip')))
        manifest, target = self._snapshot(include_unscanned=True)
        self.assertEqual(manifest['skipped'], [])
        self.assertTrue(os.path.exists(os.path.join(target, 'locked.zip')))
        self.assertEqual(manifest['files'][0]['secret_scan_status'], 'PARTIAL')
        code, text = run_main(['inventory', self.src, '--out', self.out,
                               '--device-label', 'SYNTHETIC'])
        self.assertEqual(code, probe.EXIT_PARTIAL)
        self.assertIn('PARTIAL', text)

    def test_zip_scan_budget_is_honoured_and_declared(self):
        make_zip(self.path('big.zip'), [('a.bin', b'\x01' * 5000),
                                        ('b.bin', b'\x02' * 5000)])
        shape = probe.zip_shape(self.path('big.zip'), scan_budget=6000)
        self.assertEqual(shape['scan_bytes'], 6000)
        self.assertIn('ZIP_OVER_SCAN_LIMIT', shape['scan_reasons'])
        shape = probe.zip_shape(self.path('big.zip'), scan_budget=20000)
        self.assertEqual(shape['scan_bytes'], 10000)
        self.assertEqual(shape['scan_reasons'], [])

    def test_nested_container_inside_zip_is_declared_partial(self):
        inner_zip = io.BytesIO()
        with zipfile.ZipFile(inner_zip, 'w') as archive:
            archive.writestr('deep.txt', 'password=' + SECRET)
        make_zip(self.path('outer.zip'), [('inner.zip', inner_zip.getvalue())],
                 compression=zipfile.ZIP_STORED)
        inv = probe.inventory(self.src, 'SYNTHETIC')
        entry = inv['files'][0]
        self.assertIn('ZIP_NESTED_CONTAINER', entry['secret_scan']['reasons'])
        self.assertIn(entry['secret_scan']['status'], ('PARTIAL', 'MARKER_FOUND'))
        self.assertNotEqual(entry['secret_scan']['status'], probe.SCAN_CLEAN)

    def test_secret_in_a_json_key_is_redacted_everywhere(self):
        write(self.path('metadata.json'),
              json.dumps({'?token=' + SECRET: 'x', 'device': 'y'}))
        code, text = run_main(['inventory', self.src, '--out', self.out,
                               '--device-label', 'SYNTHETIC'])
        self.assertEqual(code, probe.EXIT_SECRET_FOUND)
        for name in probe.INVENTORY_FILES:
            self.assertNotIn(SECRET, read_text(os.path.join(self.out, name)),
                             name)
        self.assertNotIn(SECRET, text)
        report = read_json(os.path.join(self.out, 'inventory.json'))
        entry = report['files'][0]
        self.assertEqual(entry['secret_scan']['status'], 'MARKER_FOUND')
        self.assertIn('json_key', entry['secret_scan']['where'])
        self.assertEqual(entry['details']['json_shape']['keys'],
                         ['<redacted>', 'device'])
        self.assertIn('SECRET_MARKER_IN_NAME', entry['flags'])

    def test_secret_in_file_zip_entry_and_table_names_is_redacted(self):
        write(self.path('password=' + SECRET + '.txt'), 'plain')
        make_zip(self.path('export.zip'),
                 [('access_token=' + SECRET + '.txt', b'plain')])
        make_sqlite(self.path('records.db'), tables=('flight_log',
                                                     'refresh_token_' + SECRET))
        code, text = run_main(['inventory', self.src, '--out', self.out,
                               '--device-label', 'SYNTHETIC'])
        self.assertEqual(code, probe.EXIT_SECRET_FOUND)
        for name in probe.INVENTORY_FILES:
            self.assertNotIn(SECRET, read_text(os.path.join(self.out, name)),
                             name)
        self.assertNotIn(SECRET, text)
        report = read_json(os.path.join(self.out, 'inventory.json'))
        by_sha = {f['sha256']: f for f in report['files']}
        named = [f for f in report['files'] if f['relpath'] == '<redacted>']
        self.assertEqual(len(named), 1)
        self.assertIn('name', named[0]['secret_scan']['where'])
        zip_entry = by_sha[probe.sha256_file(self.path('export.zip'))]
        self.assertEqual(zip_entry['details']['zip']['entries'][0]['name'],
                         '<redacted>')
        self.assertIn('zip_entry_name', zip_entry['secret_scan']['where'])
        db = by_sha[probe.sha256_file(self.path('records.db'))]
        self.assertEqual([t['name'] for t in db['details']['sqlite']['tables']],
                         ['flight_log', '<redacted>'])
        self.assertIn('sqlite_table_name', db['secret_scan']['where'])
        self.assertEqual(report['totals']['flagged_secret'], 3)
        # Снимок: имя с маркером не копируется и в манифесте скрыто.
        manifest, target = self._snapshot()
        self.assertNotIn(SECRET, json.dumps(manifest))
        self.assertEqual(len(manifest['skipped']), 3)
        self.assertEqual(os.listdir(target), ['manifest.json'])

    def test_transfer_report_carries_no_names_keys_or_identifiers(self):
        write(self.path('flight', 'secretish_name.bin'), v4_series(3))
        write(self.path('flight', 'route.bin'), route_body())
        write(self.path('meta.json'), '{"device_nickname": "x", "owner": "y"}')
        make_sqlite(self.path('records.db'), tables=('flight_log', 'accounts'))
        make_zip(self.path('export.zip'), [('logs/private_name.txt', b'data')])
        write(self.path('leak.txt'), 'password=' + SECRET)
        code, _text = run_main(['inventory', self.src, '--out', self.out,
                                '--device-label', 'RC-01'])
        self.assertEqual(code, probe.EXIT_SECRET_FOUND)
        local = read_text(os.path.join(self.out, 'inventory.json'))
        transfer = read_text(os.path.join(self.out, 'inventory_transfer.json'))
        transfer_md = read_text(os.path.join(self.out, 'inventory_transfer.md'))
        for needle in ('secretish_name', 'route.bin', 'meta.json', 'records.db',
                       'device_nickname', 'owner', 'accounts', 'flight_log',
                       'private_name', '1ZNBJ7C00A0001', 'leak.txt', 'password',
                       self.src.replace('\\', '\\\\'), SECRET):
            self.assertNotIn(needle, transfer, needle)
            self.assertNotIn(needle, transfer_md, needle)
        # Положительный контроль: локальная опись имена несёт (кроме секрета).
        for needle in ('secretish_name', 'device_nickname', 'accounts',
                       'private_name', '1ZNBJ7C00A0001'):
            self.assertIn(needle, local, needle)
        self.assertNotIn(SECRET, local)
        document = read_json(os.path.join(self.out, 'inventory_transfer.json'))
        self.assertEqual(document['report_kind'], 'TRANSFER')
        self.assertEqual(document['totals']['files'], 6)
        for entry in document['files']:
            self.assertNotIn('relpath', entry)
            self.assertNotIn('markers', entry['secret_scan'])
            self.assertIn('sha256', entry)
            self.assertIn(entry['secret_scan']['status'],
                          (probe.SCAN_CLEAN, 'MARKER_FOUND', 'PARTIAL',
                           'NOT_SCANNED'))
        by_kind = {e['kind']: e for e in document['files']}
        self.assertEqual(by_kind['SQLITE']['details']['sqlite']['table_count'], 2)
        self.assertEqual(by_kind['JSON']['details']['json_shape'],
                         {'top_type': 'object', 'key_count': 2})
        self.assertEqual(by_kind['ZIP']['details']['zip']['entries_total'], 1)
        v4_entries = [e for e in document['files']
                      if (e['details'].get('v4_shape') or {}).get('status')
                      == 'V4_LIKE']
        self.assertEqual(len(v4_entries), 1)
        route_entries = [e for e in document['files']
                         if 'route_shape' in e['details']
                         and e['details']['route_shape']['status'] == 'ROUTE_LIKE']
        self.assertEqual(route_entries[0]['details']['route_shape']['hardware_id_count'], 1)
        self.assertNotIn('hardware_ids', route_entries[0]['details']['route_shape'])


# ─── Ревизия, п. 4: стабильность источника ───────────────────────────────────

class ReviewSourceStability(ProbeCase):

    def _stable_source(self):
        path = write(self.path('growing.bin'), b'BEFORE_SYNTHETIC')
        age(path)
        return path

    def test_the_review_scenario_change_after_copy_before_target_hash(self):
        source = self._stable_source()
        target_dir = os.path.join(self.root, 'snapshot')
        original = probe.sha256_file
        changed = [False]

        def modify_source_before_target_hash(path, *args, **kwargs):
            if (str(path).endswith('growing.bin')
                    and str(path).startswith(target_dir)):
                write(source, b'AFTER_SYNTHETIC')
                changed[0] = True
            return original(path, *args, **kwargs)

        with patch.object(probe, 'sha256_file',
                          side_effect=modify_source_before_target_hash):
            manifest = probe.snapshot(self.src, target_dir, 'SYNTHETIC')
        self.assertTrue(changed[0])
        entry = manifest['files'][0]
        current = hashlib.sha256(b'AFTER_SYNTHETIC').hexdigest()
        self.assertTrue(entry['verified'])
        self.assertEqual(entry['stability'], 'UNSTABLE')
        self.assertFalse(entry['source_rehash_matches'])
        self.assertEqual(entry['source_sha256_after_copy'], current)
        self.assertNotEqual(entry['sha256'], current)
        self.assertIn('SOURCE_CHANGED_DURING_SNAPSHOT', entry['flags'])
        self.assertNotEqual(entry['source_stat_before'], entry['source_stat_after'])
        self.assertEqual(manifest['status'], 'UNSTABLE')
        self.assertEqual(manifest['totals']['unstable'], 1)

    def test_change_during_the_copy_is_detected_by_the_rehash(self):
        source = self._stable_source()
        original = probe.copy_with_hash

        def copy_then_modify(src, dst, chunk=probe.STREAM_CHUNK):
            digest = original(src, dst, chunk)
            with open(src, 'r+b') as handle:
                handle.seek(0)
                handle.write(b'AFTERX')
            return digest

        with patch.object(probe, 'copy_with_hash', side_effect=copy_then_modify):
            manifest = probe.snapshot(self.src, os.path.join(self.root, 'snap'),
                                      'SYNTHETIC')
        entry = manifest['files'][0]
        self.assertEqual(entry['stability'], 'UNSTABLE')
        self.assertFalse(entry['source_rehash_matches'])
        self.assertEqual(manifest['status'], 'UNSTABLE')
        self.assertEqual(entry['size_bytes'], len(b'BEFORE_SYNTHETIC'))
        # Копия при этом целостна относительно прочитанного потока.
        self.assertTrue(entry['verified'])

    def test_same_bytes_rewritten_change_the_stat_and_are_reported(self):
        source = self._stable_source()
        original = probe.copy_with_hash

        def copy_then_touch(src, dst, chunk=probe.STREAM_CHUNK):
            digest = original(src, dst, chunk)
            with open(src, 'r+b') as handle:
                handle.write(b'BEFORE_SYNTHETIC')
            return digest

        with patch.object(probe, 'copy_with_hash', side_effect=copy_then_touch):
            manifest = probe.snapshot(self.src, os.path.join(self.root, 'snap'),
                                      'SYNTHETIC')
        entry = manifest['files'][0]
        self.assertTrue(entry['source_rehash_matches'])
        self.assertEqual(entry['stability'], 'UNSTABLE')
        self.assertIn('SOURCE_CHANGED_DURING_SNAPSHOT', entry['flags'])

    def test_same_length_rewrite_with_restored_mtime_is_caught_by_the_rehash(self):
        """На Windows размер, mtime и ctime (создание) здесь не меняются --
        подмену байтов видит только повторный хеш источника."""
        source = self._stable_source()
        stat = os.stat(source)
        original = probe.copy_with_hash

        def swap_bytes_and_restore_time(src, dst, chunk=probe.STREAM_CHUNK):
            digest = original(src, dst, chunk)
            with open(src, 'r+b') as handle:
                handle.write(b'CHANGED-SYNTHETI')  # та же длина, что BEFORE_SYNTHETIC
            os.utime(src, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            return digest

        with patch.object(probe, 'copy_with_hash',
                          side_effect=swap_bytes_and_restore_time):
            manifest = probe.snapshot(self.src, os.path.join(self.root, 'snap'),
                                      'SYNTHETIC')
        entry = manifest['files'][0]
        self.assertEqual(entry['source_stat_before']['size'],
                         entry['source_stat_after']['size'])
        self.assertEqual(entry['source_stat_before']['mtime_ns'],
                         entry['source_stat_after']['mtime_ns'])
        self.assertFalse(entry['source_rehash_matches'])
        self.assertEqual(entry['stability'], 'UNSTABLE')
        self.assertEqual(manifest['status'], 'UNSTABLE')

    def test_recent_modification_and_wal_are_inconclusive_not_ok(self):
        write(self.path('fresh.bin'), b'just written')
        make_sqlite(self.path('records.db'))
        write(self.path('records.db-wal'), b'\x37\x7f\x06\x82' + b'\x00' * 28)
        age(self.path('records.db'))
        age(self.path('records.db-wal'))
        target = os.path.join(self.root, 'snap')
        code, text = run_main(['snapshot', self.src, target,
                               '--device-label', 'SYNTHETIC'])
        self.assertEqual(code, probe.EXIT_UNSTABLE, text)
        manifest = read_json(os.path.join(target, 'manifest.json'))
        by_name = {f['relpath']: f for f in manifest['files']}
        self.assertEqual(by_name['fresh.bin']['stability'], 'INCONCLUSIVE')
        self.assertEqual(by_name['records.db']['stability'], 'INCONCLUSIVE')
        self.assertEqual(by_name['records.db-wal']['stability'], 'STABLE')
        self.assertEqual(manifest['status'], 'INCONCLUSIVE')
        self.assertIn('completeness not proven', text)

    def test_stable_aged_source_is_ok_with_exit_zero(self):
        self._stable_source()
        age(write(self.path('v4.bin'), v4_series(3)))
        target = os.path.join(self.root, 'snap')
        code, text = run_main(['snapshot', self.src, target,
                               '--device-label', 'SYNTHETIC'])
        self.assertEqual(code, probe.EXIT_OK, text)
        manifest = read_json(os.path.join(target, 'manifest.json'))
        self.assertEqual(manifest['status'], 'OK')
        for entry in manifest['files']:
            self.assertEqual(entry['stability'], 'STABLE')
            self.assertTrue(entry['source_rehash_matches'])
            self.assertEqual(entry['source_stat_before'], entry['source_stat_after'])
        # Отрицательный контроль: без повторного хеша подмена байтов после
        # копирования с восстановлением stat осталась бы невидимой.
        self.assertIn('source_sha256_after_copy', manifest['files'][0])

    def test_verify_failed_outranks_everything(self):
        self._stable_source()
        original = probe.sha256_file

        def corrupt_target_hash(path, *args, **kwargs):
            if str(path).startswith(os.path.join(self.root, 'snap')):
                return '0' * 64
            return original(path, *args, **kwargs)

        with patch.object(probe, 'sha256_file', side_effect=corrupt_target_hash):
            manifest = probe.snapshot(self.src, os.path.join(self.root, 'snap'),
                                      'SYNTHETIC')
        self.assertEqual(manifest['status'], 'VERIFY_FAILED')
        self.assertFalse(manifest['files'][0]['verified'])
        self.assertEqual(probe._snapshot_exit(manifest['status']),
                         probe.EXIT_VERIFY_FAILED)


# ─── Ревизия, п. 6: кадры с одинаковой меткой времени ────────────────────────

class ReviewDuplicateTimes(ProbeCase):

    def test_the_review_scenario_two_frames_one_timestamp(self):
        a = write(self.path('a.bin'), v4_bytes(
            [frame(START_MS, area=1), frame(START_MS, area=2)], count=2))
        b = write(self.path('b.bin'), v4_bytes(
            [frame(START_MS, area=3), frame(START_MS, area=2)], count=2))
        cmp = probe.compare_v4(a, b)['comparison']
        self.assertEqual(cmp['frames_a'], 2)
        self.assertEqual(cmp['frames_b'], 2)
        self.assertEqual(cmp['frames_with_equal_t'], 2)
        self.assertEqual(cmp['equal_t_frames_with_equal_area_bits'], 1)
        self.assertEqual(cmp['duplicate_t_frames_a'], 1)
        self.assertEqual(cmp['duplicate_t_frames_b'], 1)
        self.assertEqual(cmp['ambiguous_common_t_count'], 1)
        self.assertEqual(cmp['pairing'], 'AMBIGUOUS_DUPLICATE_T')
        self.assertEqual(cmp['unpaired_frames_at_common_t'], 0)
        # Отрицательный контроль: без повторов -- однозначно.
        c = write(self.path('c.bin'), v4_series(2, area_values=[1, 2]))
        self.assertEqual(probe.compare_v4(c, c)['comparison']['pairing'],
                         'UNAMBIGUOUS')

    def test_unequal_duplicate_counts_leave_unpaired_frames(self):
        a = write(self.path('a.bin'), v4_bytes(
            [frame(START_MS, area=1), frame(START_MS, area=1),
             frame(START_MS, area=1)], count=3))
        b = write(self.path('b.bin'), v4_bytes([frame(START_MS, area=1)], count=1))
        cmp = probe.compare_v4(a, b)['comparison']
        self.assertEqual(cmp['frames_with_equal_t'], 1)
        self.assertEqual(cmp['unpaired_frames_at_common_t'], 2)
        self.assertEqual(cmp['equal_t_frames_with_equal_area_bits'], 1)
        self.assertEqual(cmp['pairing'], 'AMBIGUOUS_DUPLICATE_T')

    def test_frames_without_time_are_counted_not_paired(self):
        a = write(self.path('a.bin'), v4_bytes(
            [frame(START_MS), frame(None)], count=2))
        b = write(self.path('b.bin'), v4_bytes([frame(START_MS)], count=1))
        cmp = probe.compare_v4(a, b)['comparison']
        self.assertEqual(cmp['frames_without_t_a'], 1)
        self.assertEqual(cmp['frames_without_t_b'], 0)
        self.assertEqual(cmp['frames_with_equal_t'], 1)
        self.assertEqual(cmp['pairing'], 'UNAMBIGUOUS')

    def test_unordered_and_partially_overlapping_windows(self):
        a = write(self.path('a.bin'), v4_bytes(
            [frame(START_MS + 2000, area=3), frame(START_MS, area=1),
             frame(START_MS + 1000, area=2)], count=3))
        b = write(self.path('b.bin'), v4_series(
            3, area_values=[2, 3, 4], start_ms=START_MS + 1000))
        cmp = probe.compare_v4(a, b)['comparison']
        self.assertFalse(cmp['t_monotone_a'])
        self.assertTrue(cmp['t_monotone_b'])
        self.assertEqual(cmp['overlap_ms'], 1000)
        self.assertEqual(cmp['frames_with_equal_t'], 2)
        self.assertEqual(cmp['equal_t_frames_with_equal_area_bits'], 2)
        self.assertEqual(cmp['pairing'], 'UNAMBIGUOUS')


# ─── Ревизия, п. 8: извлечение из дампа и сопоставление без плейсхолдеров ────

class ReviewDumpExtraction(ProbeCase):

    def _dump(self, items):
        return write(os.path.join(self.root, 'collector', 'sources_dry_run_run.json'),
                     json.dumps(dump_document(items)))

    def test_list_recomputes_hashes_and_prints_no_body(self):
        body = v4_series(3)
        dump = self._dump([(673501214, 'v4', body), (673501214, 'card', b'{}'),
                           (673501215, 'v4', v4_series(2), '0' * 64)])
        code, text = run_main(['extract-dump', dump, '--list'])
        self.assertEqual(code, probe.EXIT_OK)
        self.assertIn('673501214 v4 %d %s OK' % (
            len(body), hashlib.sha256(body).hexdigest()), text)
        self.assertIn('673501215 v4', text)
        self.assertIn('HASH_MISMATCH', text)
        self.assertNotIn(base64.b64encode(body).decode('ascii'), text)
        self.assertTrue(all(ord(ch) < 128 for ch in text))

    def test_extract_writes_only_verified_bodies_as_new_files(self):
        body = v4_series(3)
        dump = self._dump([(673501214, 'v4', body), (673501214, 'card', b'{}'),
                           (673501215, 'v4', v4_series(2))])
        out = os.path.join(self.root, 'cloud_v4')
        code, text = run_main(['extract-dump', dump, '--out', out,
                               '--flight-id', '673501214'])
        self.assertEqual(code, probe.EXIT_OK, text)
        self.assertEqual(sorted(os.listdir(out)),
                         ['extract_manifest.json', 'v4_673501214.bin'])
        with open(os.path.join(out, 'v4_673501214.bin'), 'rb') as handle:
            self.assertEqual(handle.read(), body)
        manifest = read_json(os.path.join(out, 'extract_manifest.json'))
        self.assertEqual(manifest['status'], 'OK')
        self.assertEqual(manifest['origin'], 'CLOUD_DRY_RUN_DUMP')
        self.assertTrue(manifest['dump_nothing_was_queued'])
        self.assertEqual(manifest['items'][0]['status'], 'OK')
        self.assertTrue(manifest['items'][0]['verified'])
        self.assertEqual(manifest['totals'], {'matched': 1, 'written': 1,
                                              'verified': 1, 'refused': 0})
        # Без --flight-id -- все V4; папка должна быть новой.
        code, _text = run_main(['extract-dump', dump, '--out', out])
        self.assertEqual(code, probe.EXIT_PRECONDITION)
        out2 = os.path.join(self.root, 'cloud_all')
        code, _text = run_main(['extract-dump', dump, '--out', out2])
        self.assertEqual(code, probe.EXIT_OK)
        self.assertEqual(sorted(os.listdir(out2)),
                         ['extract_manifest.json', 'v4_673501214.bin',
                          'v4_673501215.bin'])

    def test_hash_mismatch_and_marker_refuse_the_body(self):
        dump = self._dump([(1, 'v4', v4_series(2), '0' * 64),
                           (2, 'v4', v4_series(2), None, 999)])
        out = os.path.join(self.root, 'cloud')
        code, text = run_main(['extract-dump', dump, '--out', out])
        self.assertEqual(code, probe.EXIT_VERIFY_FAILED, text)
        self.assertEqual(os.listdir(out), ['extract_manifest.json'])
        manifest = read_json(os.path.join(out, 'extract_manifest.json'))
        self.assertEqual([i['status'] for i in manifest['items']],
                         ['HASH_MISMATCH', 'SIZE_MISMATCH'])
        leaky = self._dump([(3, 'v4', ('password=' + SECRET).encode())])
        out2 = os.path.join(self.root, 'cloud2')
        code, text = run_main(['extract-dump', leaky, '--out', out2])
        self.assertEqual(code, probe.EXIT_SECRET_FOUND, text)
        self.assertEqual(os.listdir(out2), ['extract_manifest.json'])
        self.assertNotIn(SECRET, read_text(os.path.join(out2,
                                                        'extract_manifest.json')))
        self.assertNotIn(SECRET, text)

    def test_nothing_matched_and_bad_dump(self):
        dump = self._dump([(1, 'card', b'{}')])
        code, _text = run_main(['extract-dump', dump, '--out',
                                os.path.join(self.root, 'cloud')])
        self.assertEqual(code, probe.EXIT_NOTHING)
        bad = write(os.path.join(self.root, 'bad.json'), '{"no": "sources"}')
        code, text = run_main(['extract-dump', bad, '--out',
                               os.path.join(self.root, 'cloud2')])
        self.assertEqual(code, probe.EXIT_PRECONDITION)
        self.assertIn('sources', text)
        code, _text = run_main(['extract-dump', dump])
        self.assertEqual(code, probe.EXIT_PRECONDITION)
        code, _text = run_main(['extract-dump', dump, '--out',
                                os.path.join(os.path.dirname(dump), 'x')])
        self.assertEqual(code, probe.EXIT_PRECONDITION)

    def test_match_compares_every_v4_like_file_with_every_cloud_body(self):
        same = v4_series(4, area_values=[1.0, 1.01, 1.02, 1.03])
        age(write(self.path('rc_same.bin'), same))
        age(write(self.path('rc_other.bin'),
                  v4_series(4, start_ms=START_MS + 3600 * 1000)))
        age(write(self.path('not_v4.txt'), 'nothing'))
        dump = self._dump([(673501214, 'v4', same),
                           (673501215, 'v4', v4_series(4, start_ms=START_MS + 2000)),
                           (673501214, 'card', b'{}')])
        code, _text = run_main(['inventory', self.src, '--out', self.out,
                                '--device-label', 'RC-01'])
        self.assertEqual(code, probe.EXIT_OK)
        out = os.path.join(self.root, 'match')
        code, text = run_main(['match', os.path.join(self.out, 'inventory.json'),
                               dump, '--out', out])
        self.assertEqual(code, probe.EXIT_OK, text)
        result = read_json(os.path.join(out, 'match.json'))
        self.assertEqual(result['totals'], {
            'cloud_bodies': 2, 'cloud_bodies_usable': 2, 'rc_v4_like': 2,
            'rc_compared': 2, 'pairs': 4, 'identical_pairs': 1})
        by_name = {r['relpath']: r for r in result['rc_files']}
        self.assertEqual(by_name['rc_same.bin']['candidate_cloud_flight_id'],
                         673501214)
        self.assertTrue(by_name['rc_same.bin']['candidate_identical_bytes'])
        self.assertEqual(by_name['rc_other.bin']['candidate_overlap_ms'], 0)
        self.assertFalse(by_name['rc_other.bin']['candidate_identical_bytes'])
        self.assertIn('NONE', result['identity_proof'])
        md = read_text(os.path.join(out, 'match.md'))
        self.assertIn('кандидата, не связь', md)
        self.assertNotIn('39.123456', md + json.dumps(result))
        self.assertTrue(all(ord(ch) < 128 for ch in text))
        # Файл, изменённый после описи, не сравнивается.
        write(self.path('rc_same.bin'), same + b'\x00')
        out2 = os.path.join(self.root, 'match2')
        code, _text = run_main(['match', os.path.join(self.out, 'inventory.json'),
                                dump, '--out', out2])
        self.assertEqual(code, probe.EXIT_PARTIAL)
        result = read_json(os.path.join(out2, 'match.json'))
        by_name = {r['relpath']: r for r in result['rc_files']}
        self.assertEqual(by_name['rc_same.bin']['status'],
                         'FILE_CHANGED_SINCE_INVENTORY')
        # Отчёт для передачи для сопоставления не годится -- сказано прямо.
        code, text = run_main(['match',
                               os.path.join(self.out, 'inventory_transfer.json'),
                               dump, '--out', os.path.join(self.root, 'match3')])
        self.assertEqual(code, probe.EXIT_PRECONDITION)
        self.assertIn('LOCAL', text)


# ─── Ревизия, п. 9: повреждённый GZIP не роняет опись ────────────────────────

class ReviewCorruptContainers(ProbeCase):

    def test_corrupt_truncated_and_oversized_gzip_are_described_not_fatal(self):
        write(self.path('a_corrupt.gz'),
              b'\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03\xff\xff\xff\xff\xff')
        whole = gzip.compress(b'y' * 5000)
        write(self.path('b_truncated.gz'), whole[:len(whole) // 2])
        write(self.path('c_good.gz'), gzip.compress(v4_series(2)))
        write(self.path('d_after.bin'), v4_series(2))
        report = probe.inventory(self.src, 'SYNTHETIC')
        by_name = {f['relpath']: f for f in report['files']}
        self.assertEqual(len(report['files']), 4)
        corrupt = by_name['a_corrupt.gz']
        self.assertIn('CONTAINER_ERROR', corrupt['flags'])
        self.assertEqual(corrupt['secret_scan']['status'], 'PARTIAL')
        self.assertTrue(any(r.startswith('GZIP_ERROR:')
                            for r in corrupt['secret_scan']['reasons']))
        self.assertIsNotNone(corrupt['details']['gzip']['error'])
        truncated = by_name['b_truncated.gz']
        self.assertEqual(truncated['secret_scan']['status'], 'PARTIAL')
        self.assertTrue(any(r.startswith('GZIP_ERROR:')
                            for r in truncated['secret_scan']['reasons']))
        good = by_name['c_good.gz']
        self.assertEqual(good['secret_scan']['status'], probe.SCAN_CLEAN)
        self.assertEqual(good['details']['gzip']['v4_shape']['status'], 'V4_LIKE')
        self.assertEqual(by_name['d_after.bin']['details']['v4_shape']['status'],
                         'V4_LIKE')
        self.assertEqual(report['status'], 'PARTIAL')
        self.assertEqual(report['totals']['partially_scanned'], 2)
        code, text = run_main(['inventory', self.src, '--out', self.out,
                               '--device-label', 'SYNTHETIC'])
        self.assertEqual(code, probe.EXIT_PARTIAL, text)
        self.assertNotIn('Traceback', text)

    def test_oversized_gzip_is_declared_partial(self):
        inner, meta = probe.gzip_inner(gzip.compress(b'z' * 1000), limit=100)
        self.assertEqual(len(inner), 100)
        self.assertTrue(meta['truncated'])
        inner, meta = probe.gzip_inner(gzip.compress(b'z' * 50), limit=100)
        self.assertEqual(len(inner), 50)
        self.assertFalse(meta['truncated'])
        self.assertIsNone(meta['error'])
        write(self.path('big.gz'), gzip.compress(b'z' * 1000))
        with patch.object(probe, 'gzip_inner',
                          lambda blob, limit=100: _ORIGINAL_GZIP_INNER(blob, 100)):
            report = probe.inventory(self.src, 'SYNTHETIC')
        entry = report['files'][0]
        self.assertIn('GZIP_OVER_SCAN_LIMIT', entry['secret_scan']['reasons'])
        self.assertEqual(entry['secret_scan']['status'], 'PARTIAL')

    def test_a_decoder_crash_on_one_object_does_not_stop_the_inventory(self):
        write(self.path('a.bin'), v4_series(2))
        write(self.path('b.bin'), v4_series(2))

        def explode(blob):
            raise RuntimeError('synthetic decoder failure: ' + SECRET)

        with patch.object(probe, 'v4_shape', side_effect=explode):
            report = probe.inventory(self.src, 'SYNTHETIC')
        self.assertEqual(len(report['files']), 2)
        for entry in report['files']:
            self.assertIn('ANALYSIS_ERROR', entry['flags'])
            self.assertEqual(entry['details']['analysis_error'], 'RuntimeError')
            self.assertEqual(entry['kind'], 'UNKNOWN_BINARY')
        self.assertEqual(report['status'], 'PARTIAL')
        self.assertEqual(report['totals']['analysis_errors'], 2)
        self.assertNotIn(SECRET, json.dumps(report))


_ORIGINAL_GZIP_INNER = probe.gzip_inner


if __name__ == '__main__':
    unittest.main()
