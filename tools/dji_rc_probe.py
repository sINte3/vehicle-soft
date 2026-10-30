# -*- coding: utf-8 -*-
"""tools/dji_rc_probe.py -- обследование файлов, снятых с пульта DJI. Только чтение.

VS-DJI-RC-COLLECTOR-001, этап 0А. Инструмент для этапа 0Б: владелец или
ответственный оператор копирует файлы с пульта (штатный экспорт, USB, папка,
выбранная на пульте) в отдельную папку на компьютере, а этот скрипт описывает,
ЧТО в этой папке лежит -- не выдумывая смысла того, чего не понимает.

    python tools/dji_rc_probe.py inventory C:\\VehicleSoft_RC_Survey\\RC-01\\raw --out C:\\VehicleSoft_RC_Survey\\RC-01\\probe --device-label RC-01
    python tools/dji_rc_probe.py probe C:\\VehicleSoft_RC_Survey\\RC-01\\raw\\some_file.bin
    python tools/dji_rc_probe.py compare-v4 C:\\...\\rc_file.bin C:\\...\\cloud_v4.bin
    python tools/dji_rc_probe.py snapshot C:\\VehicleSoft_RC_Survey\\RC-01\\raw C:\\VehicleSoft_RC_Survey\\RC-01\\snapshot --device-label RC-01

ЧТО ДЕЛАЕТ. Для каждого файла: размер, SHA-256, время изменения, класс по
содержимому (SQLite, ZIP, GZIP, JSON, текст, protobuf-подобный, картинка,
неизвестный двоичный), маркеры секретов, структурная перепись protobuf
(номера полей, wire types, счётчики -- без значений), проверка «похоже ли на
V4 облака» декодером `dji_area/v4.py` и «похоже ли на маршрут облака»
декодером `drone_collector/route_decode.py`. Для SQLite -- имена таблиц и
число строк, без содержимого. Для ZIP -- список записей без распаковки.

ЧЕГО НЕ ДЕЛАЕТ. Не пишет ни байта в исходную папку, не распаковывает архивы,
не открывает SQLite иначе как `mode=ro&immutable=1`, не ходит в сеть, не
открывает `instance/transport.db`, не зовёт Flask и не трогает очередь
сборщика. Координаты, значения неизвестных полей и содержимое строк в отчёт
не попадают -- только присутствие, счётчики и хеши. Файл с маркером секрета
(подписанная ссылка, cookie, токен) в отчёте назван по имени маркера, а его
содержимое не печатается и при `snapshot` по умолчанию не копируется.

ЧТО ЭТОТ ИНСТРУМЕНТ НЕ УТВЕРЖДАЕТ. «Похоже на V4» означает «байты разбираются
тем же декодером и несут те же номера полей» -- и ничего о происхождении,
подлинности, полноте и смысле. Искусственный файл, разобранный этим скриптом,
не является доказательством доступа к данным DJI (ТЗ §0.7). Происхождение
всего, что он описывает, -- `RC`, и это отдельный тип, не `card`, `route` и
не `v4` облака (ТЗ §0.6).

В консоль -- только ASCII (кодировка консоли Windows). Кириллица уходит
только в файлы отчёта (UTF-8).
"""

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import sqlite3
import sys
import time
import zipfile
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

PROBE_VERSION = 'dji-rc-probe-1'
# Явный тип происхождения всего, что описывает этот скрипт (ТЗ §0.6).
ORIGIN = 'RC'

EXIT_OK = 0
EXIT_NOTHING = 1
EXIT_NO_INPUT = 2
EXIT_SECRET_FOUND = 3
EXIT_PRECONDITION = 4
EXIT_VERIFY_FAILED = 5

# Маркеры секретов: объединение списка приёмника (`dji_area/evidence.py`,
# SECRET_MARKERS) и списка очереди сборщика (`drone_collector/outbox.py`).
# Сверяются по тексту файла в нижнем регистре. Тест
# `test_local_markers_cover_the_receiver_list` держит список не уже, чем у
# приёмника: файл, который приёмник отверг бы, обязан быть помечен здесь.
SECRET_MARKERS = (
    'ossaccesskeyid=', 'signature=', 'expires=', 'signedurl', 'set-cookie',
    'authorization:', 'bearer ', 'x-amz-signature', 'x-amz-credential',
    'authorization', 'storage_state', 'x-auth-token', 'aliyuncs.com',
    'x-oss-', 'password', 'passwd', 'access_token', 'refresh_token',
)
# Форма подписанной ссылки -- та же, что у очереди сборщика.
SIGNED_URL_QUERY = re.compile(
    r'[?&](?:x-amz-[a-z-]+|ossaccesskeyid|signature|sig|se|st|sp|sv|'
    r'expires|expiry|token|credential|key-pair-id|policy)=',
    re.IGNORECASE)

# Сигнатуры форматов. Порядок важен: ZIP раньше текста (KMZ -- это ZIP).
MAGIC = (
    (b'SQLite format 3\x00', 'SQLITE'),
    (b'PK\x03\x04', 'ZIP'),
    (b'PK\x05\x06', 'ZIP'),
    (b'\x1f\x8b', 'GZIP'),
    (b'\x89PNG', 'PNG'),
    (b'\xff\xd8\xff', 'JPEG'),
    (b'%PDF', 'PDF'),
)
KIND_EMPTY = 'EMPTY'
KIND_JSON = 'JSON'
KIND_TEXT = 'TEXT'
KIND_PROTOBUF = 'PROTOBUF_CANDIDATE'
KIND_UNKNOWN = 'UNKNOWN_BINARY'

# Расширения, которые в архиве считаются исполняемыми (ТЗ §11.2: архивы не
# распаковывать, исполняемое не запускать). Только флаг, ничего не делается.
EXECUTABLE_SUFFIXES = ('.apk', '.dex', '.so', '.exe', '.dll', '.sh', '.bat',
                       '.cmd', '.ps1', '.jar', '.msi', '.com', '.scr')

# Предел глубокого разбора одного файла. Больше -- только хеш и класс по
# сигнатуре; содержимое не читается в память целиком.
DEFAULT_DEEP_MAX_BYTES = 64 * 1024 * 1024
# Предел распаковки GZIP для классификации внутреннего содержимого.
GZIP_INNER_MAX_BYTES = 64 * 1024 * 1024
# Сколько имён записей ZIP попадает в отчёт.
ZIP_ENTRIES_IN_REPORT = 200
# Сколько ключей верхнего уровня JSON попадает в отчёт.
JSON_KEYS_IN_REPORT = 100
# Файл, изменённый меньше этого числа секунд назад, может ещё дописываться
# (ТЗ §9.2: стартовая настройка, а не доказательство завершения).
RECENTLY_MODIFIED_S = 60

WIRE_NAMES = {0: 'varint', 1: 'fixed64', 2: 'bytes', 5: 'fixed32'}

# Ключи сводки V4, которые выходят в отчёт. Белый список, а не чёрный:
# новое поле сводки не выйдет наружу, пока его не назовут здесь.
V4_SUMMARY_KEYS = (
    'parser_version', 'size_bytes', 'frame_count', 'top_count', 'usage_type',
    't_first_ms', 't_last_ms', 'span_s', 'dt_min_s', 'dt_max_s',
    'dt_median_s', 'nonpositive_dt_count', 'dt_over_limit_count',
    'frames_with_position', 'frames_with_mode', 'frames_with_width',
    'frames_with_spray_block', 'frames_with_flow', 'frames_with_quantity',
    'counter_present_frames', 'counter_first_encoded_native',
    'counter_last_encoded_native', 'counter_leading_omitted_frames',
    'counter_trailing_omitted_frames', 'counter_interior_omitted_frames',
    'counter_negative_steps', 'counter_positive_steps',
    'counter_observed_delta_native', 'counter_observed_delta_m2',
    'counter_zero_default_delta_m2', 'application_flag_frames',
    'flow_positive_frames', 'application_frames', 'quantity_first',
    'quantity_last', 'quantity_delta', 'moving_application_frames',
    'moving_application_distance_m', 'gps_jump_over_100m', 'speed_max_mps',
    'width_positive_frames', 'width_min', 'width_max', 'mode_histogram',
    'warnings',
)
# Ключи кадра V4, присутствие которых переписывается. Координаты (`lat`,
# `lng`) считаются как ПРИСУТСТВИЕ, значения не выходят никогда.
V4_FRAME_KEYS = (
    't', 'lat', 'lng', 'vx', 'vy', 'vz', 'height', 'mode', 'width',
    'spray_block', 'spray_flag', 'flow', 'residual', 'spray_side', 'quantity',
    'sow', 'area_native', 'delivery', 'battery', 'rel_alt', 'rtk', 'pos_mode',
    'wind_dir', 'wind_speed', 'rtk_sat',
)


class ProbeError(Exception):
    """Отказ инструмента: предусловие не выполнено."""


# ─── Утилиты ─────────────────────────────────────────────────────────────────

def say(text):
    """В консоль -- только ASCII."""
    sys.stdout.write(str(text).encode('ascii', 'replace').decode('ascii')
                     + '\n')


def utc_iso(ts=None):
    dt = (datetime.fromtimestamp(ts, timezone.utc) if ts is not None
          else datetime.now(timezone.utc))
    return dt.strftime('%Y-%m-%dT%H:%M:%SZ')


def sha256_file(path, chunk=1024 * 1024):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def is_within(child, parent):
    """True, если `child` лежит внутри `parent` (или совпадает с ним)."""
    child = os.path.normcase(os.path.abspath(child))
    parent = os.path.normcase(os.path.abspath(parent))
    try:
        return os.path.commonpath([child, parent]) == parent
    except ValueError:
        return False


def find_secret_markers(blob):
    """Имена найденных маркеров секретов. Пустой список -- чисто.

    Проверяется текстовое чтение байтов (нечитаемое игнорируется) в нижнем
    регистре: подстрочные маркеры плюс форма подписанной ссылки. Значения
    не возвращаются -- только имена маркеров.
    """
    text = bytes(blob).decode('utf-8', errors='ignore').lower()
    found = sorted({marker.rstrip('=:').strip()
                    for marker in SECRET_MARKERS if marker in text})
    if SIGNED_URL_QUERY.search(text):
        found.append('signed-url-query')
    return found


# ─── Классификация ───────────────────────────────────────────────────────────

def _printable_ratio(text):
    if not text:
        return 0.0
    bad = sum(1 for ch in text if ord(ch) < 32 and ch not in '\t\r\n')
    return 1.0 - bad / float(len(text))


def _walk(blob):
    from drone_collector.route_decode import RouteDecodeError, walk
    return walk(blob), RouteDecodeError


def protobuf_walkable(blob):
    """True, если байты целиком разбираются как одно protobuf-сообщение."""
    if not blob:
        return False
    try:
        fields, _error = _walk(blob)
    except Exception:  # noqa: BLE001 -- любой отказ декодера: не protobuf
        return False
    if not fields:
        return False
    return all(0 < number < (1 << 29) for number, _wire, _value in fields)


def classify(blob):
    """Класс содержимого по байтам. Никогда не догадка: UNKNOWN_BINARY -- ответ."""
    blob = bytes(blob)
    if not blob:
        return KIND_EMPTY
    for magic, kind in MAGIC:
        if blob.startswith(magic):
            return kind
    text = None
    try:
        text = blob.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = None
    if text is not None and _printable_ratio(text) >= 0.99:
        stripped = text.strip()
        if stripped[:1] in ('{', '['):
            try:
                json.loads(stripped)
                return KIND_JSON
            except ValueError:
                pass
        return KIND_TEXT
    if protobuf_walkable(blob):
        return KIND_PROTOBUF
    return KIND_UNKNOWN


# ─── Разборы по классам ──────────────────────────────────────────────────────

def protobuf_census(blob, depth=2):
    """Структурная перепись protobuf: номера полей, wire types, счётчики.

    Значения не читаются: у varint не записывается даже длина (длина кодирует
    величину). Вложенные bytes-поля разбираются до глубины `depth`; то, что
    не разобралось, считается непрозрачным (`opaque`), а не «строкой».
    """
    top = {}
    nested = {}
    opaque = 0

    def note(table, key, wire, length):
        slot = table.setdefault(key, {'count': 0, 'bytes_total': 0})
        slot['count'] += 1
        if wire != 0:
            slot['bytes_total'] += length

    def visit(buf, path, level):
        nonlocal opaque
        try:
            fields, _error = _walk(buf)
        except Exception:  # noqa: BLE001
            opaque += 1
            return
        for number, wire, value in fields:
            key = '%s%d:%s' % (path, number, WIRE_NAMES.get(wire, str(wire)))
            length = len(value) if wire != 0 else 0
            note(top if level == 0 else nested, key, wire, length)
            if wire == 2 and level + 1 < depth and value:
                visit(value, '%s%d/' % (path, number), level + 1)

    visit(bytes(blob), '', 0)
    return {
        'top': [dict(key=k, **v) for k, v in sorted(
            top.items(), key=lambda kv: (int(kv[0].split(':')[0]), kv[0]))],
        'nested': [dict(key=k, **v) for k, v in sorted(nested.items())],
        'opaque_bytes_fields': opaque,
        'depth': depth,
    }


def v4_shape(blob):
    """Похожи ли байты на V4 облака по декодеру `dji_area/v4.py`.

    Статусы: `V4_LIKE` -- разобрано, кадры есть, верхние поля только
    известные, хотя бы один кадр несёт время; `NOT_V4_LIKE` -- разобрано,
    но форма другая; `UNDECODABLE` -- декодер отказал. Ничего из этого не
    говорит о происхождении и подлинности файла.
    """
    from dji_area import v4
    try:
        decoded = v4.decode_v4(bytes(blob))
    except v4.V4DecodeError as exc:
        return {'status': 'UNDECODABLE', 'error': type(exc).__name__}
    frames = decoded.frames
    presence = {key: sum(1 for f in frames if key in f) for key in V4_FRAME_KEYS}
    other_frame_fields = sorted({n for f in frames for n in f.get('other', ())})
    result = {
        'frame_count': len(frames),
        'top_other_fields': sorted(decoded.top_other_fields),
        'frame_other_fields': other_frame_fields,
        'frame_key_presence': presence,
    }
    if not frames or decoded.top_other_fields or presence['t'] == 0:
        result['status'] = 'NOT_V4_LIKE'
        return result
    summary = v4.summarize_v4(decoded)
    result['status'] = 'V4_LIKE'
    result['summary'] = {key: summary.get(key) for key in V4_SUMMARY_KEYS}
    flags = {}
    for f in frames:
        if 'spray_flag' in f:
            flags[str(f['spray_flag'])] = flags.get(str(f['spray_flag']), 0) + 1
    result['spray_flag_histogram'] = flags
    # Окно без привязки к записи: смещения концов проверить нечем.
    quality, baseline, reasons = v4.evaluate_window(summary, identity_ok=True)
    result['window_quality_without_record'] = quality
    result['baseline_status'] = baseline
    result['window_reasons'] = reasons
    return result


def route_shape(blob):
    """Похожи ли байты на ответ маршрутов облака (`route_decode.py`)."""
    from drone_collector.route_decode import (RouteDecodeError,
                                              decode_route_response,
                                              point_shape_census)
    try:
        response = decode_route_response(bytes(blob))
    except (RouteDecodeError, ValueError, TypeError) as exc:
        return {'status': 'UNDECODABLE', 'error': type(exc).__name__}
    records = list(response.routes)
    return {
        'status': 'ROUTE_LIKE' if records else 'ROUTE_EMPTY',
        'api_status': response.status,
        'records': len(records),
        'embedded_flight_ids': [r.flight_id for r in records],
        'hardware_ids': [r.hardware_id for r in records],
        'point_counts': [len(r.points or ()) for r in records],
        'spray_width_known': [bool(r.spray_width_known) for r in records],
        'start_ms': [r.start_ms for r in records],
        'end_ms': [r.end_ms for r in records],
        'point_shape_census': point_shape_census(records),
    }


def sqlite_shape(path):
    """Имена таблиц и число строк. Открытие только `mode=ro&immutable=1`."""
    out = {'tables': [], 'error': None}
    uri = 'file:%s?mode=ro&immutable=1' % path.replace('\\', '/')
    try:
        con = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        out['error'] = type(exc).__name__
        return out
    try:
        rows = con.execute(
            "SELECT name, type FROM sqlite_master WHERE type IN "
            "('table', 'view') ORDER BY name").fetchall()
        for name, kind in rows:
            entry = {'name': name, 'type': kind, 'rows': None}
            if kind == 'table':
                try:
                    entry['rows'] = con.execute(
                        'SELECT COUNT(*) FROM "%s"'
                        % name.replace('"', '""')).fetchone()[0]
                except sqlite3.Error as exc:
                    entry['error'] = type(exc).__name__
            out['tables'].append(entry)
    except sqlite3.Error as exc:
        out['error'] = type(exc).__name__
    finally:
        con.close()
    return out


def zip_shape(path):
    """Список записей ZIP без распаковки; флаги опасных имён и bomb-ratio."""
    out = {'entries_total': 0, 'entries': [], 'flags': [],
           'compressed_total': 0, 'uncompressed_total': 0}
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
    except (zipfile.BadZipFile, OSError) as exc:
        out['error'] = type(exc).__name__
        return out
    flags = set()
    for info in infos:
        name = info.filename
        out['entries_total'] += 1
        out['compressed_total'] += info.compress_size
        out['uncompressed_total'] += info.file_size
        normalized = name.replace('\\', '/')
        if (normalized.startswith('/') or re.match(r'^[A-Za-z]:', normalized)
                or '..' in normalized.split('/')):
            flags.add('ZIP_PATH_TRAVERSAL')
        if normalized.lower().endswith(EXECUTABLE_SUFFIXES):
            flags.add('ZIP_EXECUTABLE_ENTRY')
        if len(out['entries']) < ZIP_ENTRIES_IN_REPORT:
            out['entries'].append({'name': name, 'size': info.file_size,
                                   'compressed': info.compress_size})
    if out['compressed_total'] and \
            out['uncompressed_total'] > 100 * out['compressed_total']:
        flags.add('ZIP_HIGH_RATIO')
    out['flags'] = sorted(flags)
    return out


def json_shape(blob):
    """Тип верхнего уровня и имена ключей. Значения не выходят."""
    try:
        document = json.loads(bytes(blob).decode('utf-8-sig'))
    except ValueError:
        return {'error': 'ValueError'}
    if isinstance(document, dict):
        keys = sorted(document.keys())
        return {'top_type': 'object', 'key_count': len(keys),
                'keys': keys[:JSON_KEYS_IN_REPORT]}
    if isinstance(document, list):
        return {'top_type': 'array', 'length': len(document)}
    return {'top_type': type(document).__name__}


def gzip_inner(blob):
    """Ограниченная распаковка GZIP ради классификации содержимого."""
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(bytes(blob))) as handle:
            inner = handle.read(GZIP_INNER_MAX_BYTES + 1)
    except (OSError, EOFError, zipfile.BadZipFile) as exc:
        return None, {'error': type(exc).__name__}
    truncated = len(inner) > GZIP_INNER_MAX_BYTES
    return inner[:GZIP_INNER_MAX_BYTES], {'truncated': truncated,
                                          'inner_size_bytes': len(inner)}


# ─── Один файл ───────────────────────────────────────────────────────────────

def _analyse_bytes(blob, depth):
    """Разбор уже прочитанных байтов: класс, секреты, формы."""
    kind = classify(blob)
    details = {}
    flags = []
    markers = find_secret_markers(blob)
    if markers:
        flags.append('SECRET_MARKER')
        details['secret_markers'] = markers
    if kind == KIND_PROTOBUF:
        details['protobuf_census'] = protobuf_census(blob, depth)
        details['v4_shape'] = v4_shape(blob)
        details['route_shape'] = route_shape(blob)
    elif kind == KIND_JSON:
        details['json_shape'] = json_shape(blob)
    elif kind == 'GZIP':
        inner, meta = gzip_inner(blob)
        details['gzip'] = meta
        if inner is not None:
            inner_kind = classify(inner)
            details['gzip']['inner_kind'] = inner_kind
            details['gzip']['inner_sha256'] = hashlib.sha256(inner).hexdigest()
            inner_markers = find_secret_markers(inner)
            if inner_markers:
                flags.append('SECRET_MARKER_INSIDE_GZIP')
                details['gzip']['secret_markers'] = inner_markers
            if inner_kind == KIND_PROTOBUF:
                details['gzip']['protobuf_census'] = protobuf_census(inner, depth)
                details['gzip']['v4_shape'] = v4_shape(inner)
                details['gzip']['route_shape'] = route_shape(inner)
            elif inner_kind == KIND_JSON:
                details['gzip']['json_shape'] = json_shape(inner)
    return kind, flags, details


def probe_file(path, deep_max_bytes=DEFAULT_DEEP_MAX_BYTES, depth=2,
               now=None):
    """Описание одного файла. Файл открывается только на чтение."""
    if not os.path.isfile(path):
        raise ProbeError('not a file: %s' % path)
    stat = os.stat(path)
    now = time.time() if now is None else now
    age = max(0.0, now - stat.st_mtime)
    entry = {
        'size_bytes': stat.st_size,
        'mtime_utc': utc_iso(stat.st_mtime),
        'mtime_age_s': round(age, 1),
        'sha256': sha256_file(path),
        'deep_analysis': stat.st_size <= deep_max_bytes,
        'flags': [],
        'details': {},
    }
    if age < RECENTLY_MODIFIED_S:
        entry['flags'].append('RECENTLY_MODIFIED')
    if entry['deep_analysis']:
        with open(path, 'rb') as handle:
            blob = handle.read()
        kind, flags, details = _analyse_bytes(blob, depth)
        entry['flags'].extend(flags)
        entry['details'] = details
    else:
        with open(path, 'rb') as handle:
            head = handle.read(64)
        kind = KIND_UNKNOWN
        for magic, magic_kind in MAGIC:
            if head.startswith(magic):
                kind = magic_kind
                break
        entry['flags'].append('TOO_LARGE_FOR_DEEP_ANALYSIS')
    entry['kind'] = kind
    if kind == 'SQLITE':
        entry['details']['sqlite'] = sqlite_shape(path)
        siblings = [path + suffix for suffix in ('-wal', '-journal', '-shm')
                    if os.path.exists(path + suffix)]
        if siblings:
            entry['flags'].append('SQLITE_WAL_OR_JOURNAL_PRESENT')
            entry['details']['sqlite']['siblings'] = [
                os.path.basename(s) for s in siblings]
    elif kind == 'ZIP':
        shape = zip_shape(path)
        entry['details']['zip'] = shape
        entry['flags'].extend(shape.get('flags', ()))
    entry['flags'] = sorted(set(entry['flags']))
    return entry


# ─── Инвентаризация папки ────────────────────────────────────────────────────

def iter_files(root):
    """Регулярные файлы под `root`, без символических ссылок, в порядке имён."""
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames
                             if not os.path.islink(os.path.join(dirpath, d)))
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if os.path.islink(full) or not os.path.isfile(full):
                continue
            yield full


def inventory(source_dir, device_label, deep_max_bytes=DEFAULT_DEEP_MAX_BYTES,
              depth=2, now=None):
    """Опись папки. Словарь, пригодный для JSON; в папку ничего не пишется."""
    if not os.path.isdir(source_dir):
        raise ProbeError('source directory not found: %s' % source_dir)
    files = []
    by_kind = {}
    total_bytes = 0
    flagged_secret = 0
    v4_like = 0
    route_like = 0
    for full in iter_files(source_dir):
        entry = probe_file(full, deep_max_bytes, depth, now)
        entry['relpath'] = os.path.relpath(full, source_dir).replace('\\', '/')
        files.append(entry)
        by_kind[entry['kind']] = by_kind.get(entry['kind'], 0) + 1
        total_bytes += entry['size_bytes']
        if any(flag.startswith('SECRET_MARKER') for flag in entry['flags']):
            flagged_secret += 1
        shapes = [entry['details'].get('v4_shape'),
                  (entry['details'].get('gzip') or {}).get('v4_shape')]
        if any(s and s.get('status') == 'V4_LIKE' for s in shapes):
            v4_like += 1
        shapes = [entry['details'].get('route_shape'),
                  (entry['details'].get('gzip') or {}).get('route_shape')]
        if any(s and s.get('status') == 'ROUTE_LIKE' for s in shapes):
            route_like += 1
    return {
        'probe_version': PROBE_VERSION,
        'origin': ORIGIN,
        'generated_at_utc': utc_iso(now),
        'device_label': device_label,
        'source_dir': os.path.abspath(source_dir),
        'totals': {
            'files': len(files),
            'bytes': total_bytes,
            'by_kind': dict(sorted(by_kind.items())),
            'flagged_secret': flagged_secret,
            'v4_like': v4_like,
            'route_like': route_like,
        },
        'files': files,
    }


def render_markdown(report):
    """Отчёт для человека (UTF-8, в файл). Значений данных здесь нет."""
    totals = report['totals']
    lines = [
        '# Обследование файлов с пульта DJI -- %s' % report['device_label'],
        '',
        'Инструмент `%s`, происхождение `%s`, сформировано %s (UTC).'
        % (report['probe_version'], report['origin'],
           report['generated_at_utc']),
        '',
        'Папка-источник: `%s`. Файлов: %d, байт: %d. Классы: %s.'
        % (report['source_dir'], totals['files'], totals['bytes'],
           ', '.join('%s %d' % kv for kv in totals['by_kind'].items())
           or 'нет'),
        '',
        'С маркером секрета: %d (эти файлы не отправлять и не публиковать). '
        'Похожи на V4 облака: %d. Похожи на маршрут облака: %d.'
        % (totals['flagged_secret'], totals['v4_like'], totals['route_like']),
        '',
        '**Что это доказывает.** Только то, что перечисленные байты лежат в '
        'папке и разбираются (или не разбираются) декодерами проекта. '
        'Происхождение, подлинность DJI, полноту и смысл полей этот отчёт '
        'не подтверждает.',
        '',
        '| Файл | Байт | Класс | Флаги | SHA-256 (первые 16) | Изменён (UTC) |',
        '|---|---|---|---|---|---|',
    ]
    for entry in report['files']:
        lines.append('| `%s` | %d | %s | %s | `%s` | %s |' % (
            entry['relpath'], entry['size_bytes'], entry['kind'],
            ', '.join(entry['flags']) or '—', entry['sha256'][:16],
            entry['mtime_utc']))
    for entry in report['files']:
        shapes = []
        if entry['details'].get('v4_shape'):
            shapes.append(('', entry['details']['v4_shape']))
        if (entry['details'].get('gzip') or {}).get('v4_shape'):
            shapes.append(('внутри GZIP ', entry['details']['gzip']['v4_shape']))
        for prefix, shape in shapes:
            if shape.get('status') != 'V4_LIKE':
                continue
            summary = shape['summary']
            lines.extend([
                '',
                '## `%s` -- %sпохож на V4 облака' % (entry['relpath'], prefix),
                '',
                'Кадров: %d; окно по кадрам: %s .. %s (unix ms), span %s с; '
                'dt медиана %s с; кадров с позицией %d, с шириной %d, '
                'с блоком распыления %d, с расходом %d; счётчик площади '
                'закодирован в %d кадрах (ведущих пропусков %s).'
                % (summary['frame_count'], summary['t_first_ms'],
                   summary['t_last_ms'], summary['span_s'],
                   summary['dt_median_s'], summary['frames_with_position'],
                   summary['frames_with_width'],
                   summary['frames_with_spray_block'],
                   summary['frames_with_flow'],
                   summary['counter_present_frames'],
                   summary['counter_leading_omitted_frames']),
                '',
                'Присутствие ключей кадра (число кадров): %s.'
                % ', '.join('%s %d' % kv for kv in
                            shape['frame_key_presence'].items() if kv[1]),
                '',
                'Окно без привязки к записи: %s (%s). Предупреждения '
                'декодера: %s.'
                % (shape['window_quality_without_record'],
                   ', '.join(shape['window_reasons']) or 'нет',
                   ', '.join(summary['warnings']) or 'нет'),
            ])
    lines.append('')
    return '\n'.join(lines)


# ─── Сравнение двух V4 ───────────────────────────────────────────────────────

def compare_v4(path_a, path_b):
    """Сравнение двух файлов, разобранных как V4: байты, окна, присутствие.

    Личность вылета файлы не несут (V4 без flight id -- `dji_area/evidence.py`);
    совпадение окон -- не доказательство того же вылета, и отчёт так и
    говорит. Координаты не сравниваются и не выводятся.
    """
    from dji_area import v4
    out = {'probe_version': PROBE_VERSION, 'origin_a': ORIGIN,
           'identity_proof': 'NONE -- V4 carries no flight id; equal windows '
                             'are not proof of the same flight'}
    sides = {}
    blobs = {}
    for label, path in (('a', path_a), ('b', path_b)):
        if not os.path.isfile(path):
            raise ProbeError('not a file: %s' % path)
        with open(path, 'rb') as handle:
            blob = handle.read()
        blobs[label] = blob
        side = {'path': os.path.abspath(path), 'size_bytes': len(blob),
                'sha256': hashlib.sha256(blob).hexdigest()}
        try:
            decoded = v4.decode_v4(blob)
            side['decoded'] = True
            side['frames'] = decoded.frames
            summary = v4.summarize_v4(decoded)
            side['summary'] = {key: summary.get(key) for key in V4_SUMMARY_KEYS}
            side['frame_key_presence'] = {
                key: sum(1 for f in decoded.frames if key in f)
                for key in V4_FRAME_KEYS}
        except v4.V4DecodeError as exc:
            side['decoded'] = False
            side['error'] = type(exc).__name__
        sides[label] = side
    out['identical_bytes'] = sides['a']['sha256'] == sides['b']['sha256']
    if sides['a'].get('decoded') and sides['b'].get('decoded'):
        by_t_a = {f['t']: f for f in sides['a']['frames'] if 't' in f}
        by_t_b = {f['t']: f for f in sides['b']['frames'] if 't' in f}
        common = sorted(set(by_t_a) & set(by_t_b))
        ta = sides['a']['summary']
        tb = sides['b']['summary']
        overlap = None
        if None not in (ta['t_first_ms'], ta['t_last_ms'],
                        tb['t_first_ms'], tb['t_last_ms']):
            overlap = max(0, min(ta['t_last_ms'], tb['t_last_ms'])
                          - max(ta['t_first_ms'], tb['t_first_ms']))
        out['comparison'] = {
            'frames_a': len(sides['a']['frames']),
            'frames_b': len(sides['b']['frames']),
            'overlap_ms': overlap,
            'frames_with_equal_t': len(common),
            'equal_t_frames_with_equal_area_bits': sum(
                1 for t in common
                if by_t_a[t].get('area_bits') is not None
                and by_t_a[t].get('area_bits') == by_t_b[t].get('area_bits')),
            'equal_t_frames_with_equal_width': sum(
                1 for t in common
                if by_t_a[t].get('width') is not None
                and by_t_a[t].get('width') == by_t_b[t].get('width')),
            'equal_t_frames_with_equal_spray_flag': sum(
                1 for t in common
                if 'spray_flag' in by_t_a[t]
                and by_t_a[t].get('spray_flag') == by_t_b[t].get('spray_flag')),
            'equal_t_frames_with_equal_position': sum(
                1 for t in common
                if 'lat' in by_t_a[t] and 'lng' in by_t_a[t]
                and by_t_a[t].get('lat') == by_t_b[t].get('lat')
                and by_t_a[t].get('lng') == by_t_b[t].get('lng')),
            'keys_present_only_in_a': sorted(
                k for k in V4_FRAME_KEYS
                if sides['a']['frame_key_presence'][k]
                and not sides['b']['frame_key_presence'][k]),
            'keys_present_only_in_b': sorted(
                k for k in V4_FRAME_KEYS
                if sides['b']['frame_key_presence'][k]
                and not sides['a']['frame_key_presence'][k]),
        }
    for side in sides.values():
        side.pop('frames', None)
    out['a'] = sides['a']
    out['b'] = sides['b']
    return out


# ─── Снимок в изолированную папку ────────────────────────────────────────────

def verify_copy(source_path, target_path):
    """(sha источника, sha копии, совпали ли). Оба файла только читаются."""
    source_sha = sha256_file(source_path)
    target_sha = sha256_file(target_path)
    return source_sha, target_sha, source_sha == target_sha


def snapshot(source_dir, target_dir, device_label, include_flagged=False,
             deep_max_bytes=DEFAULT_DEEP_MAX_BYTES, now=None):
    """Копия папки в пустую целевую папку с проверкой SHA-256 каждой копии.

    Источник не изменяется: файлы открываются на чтение, время изменения
    копии приводится к времени источника. Файл с маркером секрета по
    умолчанию не копируется и перечисляется в `skipped`. Манифест несёт
    происхождение `RC`, хеши и время захвата; успех -- когда каждая копия
    перечитана и её хеш совпал с хешем источника.
    """
    if not os.path.isdir(source_dir):
        raise ProbeError('source directory not found: %s' % source_dir)
    if is_within(target_dir, source_dir) or is_within(source_dir, target_dir):
        raise ProbeError('target must lie outside the source directory')
    if os.path.exists(target_dir) and os.listdir(target_dir):
        raise ProbeError('target directory is not empty: %s' % target_dir)
    os.makedirs(target_dir, exist_ok=True)
    now = time.time() if now is None else now
    manifest = {
        'probe_version': PROBE_VERSION,
        'origin': ORIGIN,
        'device_label': device_label,
        'captured_at_utc': utc_iso(now),
        'source_dir': os.path.abspath(source_dir),
        'target_dir': os.path.abspath(target_dir),
        'files': [],
        'skipped': [],
        'totals': {'copied': 0, 'verified': 0, 'verify_failed': 0,
                   'skipped': 0, 'bytes': 0},
    }
    for full in iter_files(source_dir):
        rel = os.path.relpath(full, source_dir).replace('\\', '/')
        stat = os.stat(full)
        flags = []
        if stat.st_size <= deep_max_bytes:
            with open(full, 'rb') as handle:
                blob = handle.read()
            markers = find_secret_markers(blob)
            if markers:
                flags.append('SECRET_MARKER')
        if max(0.0, now - stat.st_mtime) < RECENTLY_MODIFIED_S:
            flags.append('RECENTLY_MODIFIED')
        if 'SECRET_MARKER' in flags and not include_flagged:
            manifest['skipped'].append({'relpath': rel,
                                        'reason': 'SECRET_MARKER'})
            manifest['totals']['skipped'] += 1
            continue
        target = os.path.join(target_dir, *rel.split('/'))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        digest = hashlib.sha256()
        with open(full, 'rb') as src, open(target, 'wb') as dst:
            while True:
                block = src.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
                dst.write(block)
        os.utime(target, (stat.st_atime, stat.st_mtime))
        copied_sha = sha256_file(target)
        ok = copied_sha == digest.hexdigest()
        manifest['files'].append({
            'relpath': rel,
            'size_bytes': stat.st_size,
            'mtime_utc': utc_iso(stat.st_mtime),
            'sha256': digest.hexdigest(),
            'copy_sha256': copied_sha,
            'verified': ok,
            'flags': flags,
        })
        manifest['totals']['copied'] += 1
        manifest['totals']['bytes'] += stat.st_size
        manifest['totals']['verified' if ok else 'verify_failed'] += 1
    manifest['status'] = ('OK' if manifest['totals']['verify_failed'] == 0
                          else 'VERIFY_FAILED')
    with open(os.path.join(target_dir, 'manifest.json'), 'w',
              encoding='utf-8') as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=1,
                  sort_keys=True)
    return manifest


# ─── CLI ─────────────────────────────────────────────────────────────────────

def _write_json(path, document):
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(document, handle, ensure_ascii=False, indent=1,
                  sort_keys=True, default=str)


def cmd_inventory(args):
    if not os.path.isdir(args.source):
        say('ERROR: source directory not found')
        return EXIT_NO_INPUT
    if is_within(args.out, args.source):
        say('ERROR: --out must lie outside the source directory '
            '(the source is never written to)')
        return EXIT_PRECONDITION
    report = inventory(args.source, args.device_label,
                       deep_max_bytes=args.deep_max_bytes, depth=args.depth)
    os.makedirs(args.out, exist_ok=True)
    _write_json(os.path.join(args.out, 'inventory.json'), report)
    with open(os.path.join(args.out, 'inventory.md'), 'w',
              encoding='utf-8') as handle:
        handle.write(render_markdown(report))
    totals = report['totals']
    say('%s origin=%s device=%s' % (PROBE_VERSION, ORIGIN, args.device_label))
    say('files=%d bytes=%d kinds=%s' % (
        totals['files'], totals['bytes'],
        ','.join('%s:%d' % kv for kv in totals['by_kind'].items()) or '-'))
    say('v4_like=%d route_like=%d secret_flagged=%d' % (
        totals['v4_like'], totals['route_like'], totals['flagged_secret']))
    say('written: %s' % os.path.join(args.out, 'inventory.json'))
    say('written: %s' % os.path.join(args.out, 'inventory.md'))
    if totals['flagged_secret']:
        say('WARNING: %d file(s) carry a secret marker -- do not send them, '
            'do not attach them to a report' % totals['flagged_secret'])
        return EXIT_SECRET_FOUND
    if not totals['files']:
        return EXIT_NOTHING
    return EXIT_OK


def cmd_probe(args):
    if not os.path.isfile(args.file):
        say('ERROR: file not found')
        return EXIT_NO_INPUT
    entry = probe_file(args.file, deep_max_bytes=args.deep_max_bytes,
                       depth=args.depth)
    entry['relpath'] = os.path.basename(args.file)
    entry['probe_version'] = PROBE_VERSION
    entry['origin'] = ORIGIN
    if args.json:
        _write_json(args.json, entry)
        say('written: %s' % args.json)
    say('%s kind=%s size=%d sha256=%s flags=%s' % (
        entry['relpath'], entry['kind'], entry['size_bytes'],
        entry['sha256'], ','.join(entry['flags']) or '-'))
    shape = entry['details'].get('v4_shape')
    if shape:
        say('v4_shape=%s frames=%s' % (shape['status'],
                                        shape.get('frame_count')))
    shape = entry['details'].get('route_shape')
    if shape:
        say('route_shape=%s records=%s' % (shape['status'],
                                            shape.get('records')))
    if any(flag.startswith('SECRET_MARKER') for flag in entry['flags']):
        say('WARNING: secret marker present -- do not send this file')
        return EXIT_SECRET_FOUND
    return EXIT_OK


def cmd_compare_v4(args):
    for path in (args.a, args.b):
        if not os.path.isfile(path):
            say('ERROR: file not found')
            return EXIT_NO_INPUT
    result = compare_v4(args.a, args.b)
    if args.json:
        _write_json(args.json, result)
        say('written: %s' % args.json)
    say('identical_bytes=%s decoded_a=%s decoded_b=%s' % (
        result['identical_bytes'], result['a'].get('decoded'),
        result['b'].get('decoded')))
    comparison = result.get('comparison')
    if comparison:
        say('frames a=%d b=%d equal_t=%d overlap_ms=%s equal_area_bits=%d '
            'equal_width=%d equal_spray_flag=%d equal_position=%d' % (
                comparison['frames_a'], comparison['frames_b'],
                comparison['frames_with_equal_t'], comparison['overlap_ms'],
                comparison['equal_t_frames_with_equal_area_bits'],
                comparison['equal_t_frames_with_equal_width'],
                comparison['equal_t_frames_with_equal_spray_flag'],
                comparison['equal_t_frames_with_equal_position']))
        say('only_in_a=%s only_in_b=%s' % (
            ','.join(comparison['keys_present_only_in_a']) or '-',
            ','.join(comparison['keys_present_only_in_b']) or '-'))
    say('identity: %s' % result['identity_proof'])
    return EXIT_OK


def cmd_snapshot(args):
    if not os.path.isdir(args.source):
        say('ERROR: source directory not found')
        return EXIT_NO_INPUT
    try:
        manifest = snapshot(args.source, args.target, args.device_label,
                            include_flagged=args.include_flagged,
                            deep_max_bytes=args.deep_max_bytes)
    except ProbeError as exc:
        say('ERROR: %s' % exc)
        return EXIT_PRECONDITION
    totals = manifest['totals']
    say('%s origin=%s device=%s status=%s' % (
        PROBE_VERSION, ORIGIN, args.device_label, manifest['status']))
    say('copied=%d verified=%d verify_failed=%d skipped=%d bytes=%d' % (
        totals['copied'], totals['verified'], totals['verify_failed'],
        totals['skipped'], totals['bytes']))
    say('manifest: %s' % os.path.join(args.target, 'manifest.json'))
    if totals['skipped']:
        say('NOTE: %d file(s) with a secret marker were NOT copied '
            '(--include-flagged copies them into this isolated folder only)'
            % totals['skipped'])
    if manifest['status'] != 'OK':
        return EXIT_VERIFY_FAILED
    return EXIT_OK


def build_parser():
    parser = argparse.ArgumentParser(
        prog='dji_rc_probe',
        description='Read-only inspection of files copied from a DJI remote '
                    'controller (origin RC). Writes nothing into the source.')
    sub = parser.add_subparsers(dest='command')
    sub.required = True

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--deep-max-bytes', type=int,
                        default=DEFAULT_DEEP_MAX_BYTES,
                        help='files above this size get hash and magic only')
    common.add_argument('--depth', type=int, default=2,
                        help='protobuf census nesting depth (default 2)')

    p_inv = sub.add_parser('inventory', parents=[common],
                           help='describe every file under a directory')
    p_inv.add_argument('source', help='directory with files copied from the RC')
    p_inv.add_argument('--out', required=True,
                       help='directory for inventory.json / inventory.md '
                            '(must lie outside the source)')
    p_inv.add_argument('--device-label', required=True,
                       help='inventory label of the remote controller')
    p_inv.set_defaults(func=cmd_inventory)

    p_probe = sub.add_parser('probe', parents=[common],
                             help='describe one file')
    p_probe.add_argument('file')
    p_probe.add_argument('--json', help='write the full description here')
    p_probe.set_defaults(func=cmd_probe)

    p_cmp = sub.add_parser('compare-v4',
                           help='compare two bodies decoded as V4')
    p_cmp.add_argument('a', help='file from the RC')
    p_cmp.add_argument('b', help='V4 body of the cloud (or another RC file)')
    p_cmp.add_argument('--json', help='write the comparison here')
    p_cmp.set_defaults(func=cmd_compare_v4)

    p_snap = sub.add_parser('snapshot', parents=[common],
                            help='copy a directory into an empty isolated '
                                 'directory with SHA-256 verification')
    p_snap.add_argument('source')
    p_snap.add_argument('target', help='empty or missing directory')
    p_snap.add_argument('--device-label', required=True)
    p_snap.add_argument('--include-flagged', action='store_true',
                        help='copy files with a secret marker too')
    p_snap.set_defaults(func=cmd_snapshot)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ProbeError as exc:
        say('ERROR: %s' % exc)
        return EXIT_PRECONDITION


if __name__ == '__main__':
    sys.exit(main())
