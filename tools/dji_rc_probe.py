# -*- coding: utf-8 -*-
"""tools/dji_rc_probe.py -- обследование файлов, снятых с пульта DJI. Только чтение.

VS-DJI-RC-COLLECTOR-001, этап 0А (редакция 2 после ревизии от 30.10.2026).
Инструмент для этапа 0Б: владелец или ответственный оператор копирует файлы
с пульта (штатный экспорт, USB, папка, выбранная на пульте) в отдельную папку
на компьютере, а этот скрипт описывает, ЧТО в этой папке лежит, -- не
выдумывая смысла того, чего не понимает.

    python tools/dji_rc_probe.py inventory SRC --out DIR --device-label RC-01
    python tools/dji_rc_probe.py probe FILE [--json NEW_FILE]
    python tools/dji_rc_probe.py compare-v4 A B [--json NEW_FILE]
    python tools/dji_rc_probe.py snapshot SRC NEW_DIR --device-label RC-01
    python tools/dji_rc_probe.py extract-dump DUMP.json --out NEW_DIR [--flight-id N]
    python tools/dji_rc_probe.py match INVENTORY.json DUMP.json --out NEW_DIR

ЧТО ДЕЛАЕТ. Для каждого файла: размер, SHA-256, время изменения, класс по
содержимому (SQLite, ZIP, GZIP, JSON, текст, protobuf-подобный, картинка,
неизвестный двоичный), проверка на маркеры секретов, структурная перепись
protobuf (номера полей, wire types, счётчики -- без значений), проверка
«похоже ли на V4 облака» декодером `dji_area/v4.py` и «похоже ли на маршрут
облака» декодером `drone_collector/route_decode.py`. Для SQLite -- имена
таблиц и число строк, без содержимого. Для ZIP -- список записей без
распаковки на диск.

ЧЕГО НЕ ДЕЛАЕТ. Не пишет ни байта в исходную папку и в папки входных файлов,
не перезаписывает ни один существующий файл (все выходные файлы создаются
только как новые), не распаковывает архивы на диск, не открывает SQLite
иначе как `mode=ro&immutable=1` по экранированному URI с проверкой того,
какой файл реально открыт, не ходит в сеть, не открывает
`instance/transport.db`, не зовёт Flask и не трогает очередь сборщика.
Координаты, значения неизвестных полей и содержимое строк в отчёт не
попадают -- только присутствие, счётчики и хеши.

ПОЛИТИКА СЕКРЕТОВ (единая для `inventory` и `snapshot`). У каждого файла
есть статус проверки на маркеры: `MARKER_FOUND` -- известный маркер найден
в проверенных байтах (в самом файле, внутри GZIP, в записях ZIP, в имени
файла, ключе JSON, имени записи архива или таблицы);
`NO_KNOWN_MARKER_IN_SCANNED_BYTES` -- все байты файла и доступное содержимое
контейнеров проверены, известных маркеров нет; `PARTIAL` -- часть содержимого
проверить не удалось (повреждённый или слишком большой контейнер,
зашифрованная запись, вложенный архив, ошибка разбора); `NOT_SCANNED` --
содержимое не проверялось. Отсутствие известного маркера НЕ доказывает
отсутствие секретов. Найденный маркер называется по имени, его значение не
пишется никуда; имя файла, ключ или имя таблицы с маркером в отчёте
заменяется на `<redacted>`. В снимок по умолчанию копируются только файлы
со статусом `NO_KNOWN_MARKER_IN_SCANNED_BYTES`.

ДВА ОТЧЁТА ОПИСИ. `inventory.json` / `inventory.md` -- локальная опись с
именами файлов, ключей и таблиц (хранится с ограниченным доступом);
`inventory_transfer.json` / `inventory_transfer.md` -- отчёт для передачи:
без имён файлов, путей, ключей JSON, имён записей ZIP и таблиц SQLite, без
имён маркеров и без аппаратных идентификаторов -- только хеши, размеры,
классы, флаги, статусы и числа.

ЧТО ЭТОТ ИНСТРУМЕНТ НЕ УТВЕРЖДАЕТ. «Похоже на V4» означает «байты
разбираются тем же декодером и несут те же номера полей» -- и ничего о
происхождении, подлинности, полноте и смысле. Искусственный файл,
разобранный этим скриптом, не является доказательством доступа к данным DJI
(ТЗ §0.7). Происхождение `RC` в описи и снимке -- ЗАЯВЛЕНИЕ оператора о том,
откуда скопирована папка (`origin_basis = DECLARED_BY_OPERATOR`); инструмент
его не проверяет и проверить не может. Это отдельный тип, не `card`, `route`
и не `v4` облака (ТЗ §0.6): ни один файл отсюда не отправляется в
`POST /drones/api/source_sync` ни под каким типом. Проверка снимка
доказывает, что байты копии равны прочитанному потоку и что источник за
время наблюдения не менялся по размеру, времени и повторному хешу, -- но
не доказывает законченность файла, который ещё может дописываться.

В консоль -- только ASCII (кодировка консоли Windows). Кириллица уходит
только в файлы отчёта (UTF-8).
"""

import argparse
import base64
import gzip
import hashlib
import io
import json
import os
import pathlib
import re
import sqlite3
import sys
import time
import zipfile
import zlib
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

PROBE_VERSION = 'dji-rc-probe-2'
# Тип происхождения всего, что описывают `inventory`, `probe` и `snapshot`
# (ТЗ §0.6). Это заявление оператора о папке, не проверенный факт.
ORIGIN = 'RC'
ORIGIN_BASIS = 'DECLARED_BY_OPERATOR'
# Тела, извлечённые из дампа сухого прогона сборщика, -- облачные, не RC.
DUMP_ORIGIN = 'CLOUD_DRY_RUN_DUMP'

EXIT_OK = 0
EXIT_NOTHING = 1
EXIT_NO_INPUT = 2
EXIT_SECRET_FOUND = 3
EXIT_PRECONDITION = 4
EXIT_VERIFY_FAILED = 5
EXIT_PARTIAL = 6
EXIT_UNSTABLE = 7

# Статусы проверки на маркеры секретов -- единые для описи и снимка.
SCAN_MARKER_FOUND = 'MARKER_FOUND'
SCAN_CLEAN = 'NO_KNOWN_MARKER_IN_SCANNED_BYTES'
SCAN_PARTIAL = 'PARTIAL'
SCAN_NOT_SCANNED = 'NOT_SCANNED'

# Стабильность источника за время снимка.
STABLE = 'STABLE'
UNSTABLE = 'UNSTABLE'
INCONCLUSIVE = 'INCONCLUSIVE'

# Маркеры секретов: объединение списка приёмника (`dji_area/evidence.py`,
# SECRET_MARKERS) и списка очереди сборщика (`drone_collector/outbox.py`).
# Сверяются по тексту в нижнем регистре. Тест
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
REDACTED = '<redacted>'

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
CONTAINER_MAGIC = (b'PK\x03\x04', b'\x1f\x8b')
KIND_EMPTY = 'EMPTY'
KIND_JSON = 'JSON'
KIND_TEXT = 'TEXT'
KIND_PROTOBUF = 'PROTOBUF_CANDIDATE'
KIND_UNKNOWN = 'UNKNOWN_BINARY'

# Расширения, которые в архиве считаются исполняемыми (ТЗ §11.2: архивы не
# распаковывать, исполняемое не запускать). Только флаг, ничего не делается.
EXECUTABLE_SUFFIXES = ('.apk', '.dex', '.so', '.exe', '.dll', '.sh', '.bat',
                       '.cmd', '.ps1', '.jar', '.msi', '.com', '.scr')

# Предел глубокого разбора одного файла. Больше -- только хеш, класс по
# сигнатуре и ПОТОКОВАЯ проверка на маркеры; содержимое не читается в
# память целиком.
DEFAULT_DEEP_MAX_BYTES = 64 * 1024 * 1024
# Предел чтения содержимого контейнера (GZIP, записи ZIP) ради проверки на
# маркеры и классификации. Выше предела проверка объявляется частичной.
CONTAINER_SCAN_MAX_BYTES = 64 * 1024 * 1024
GZIP_INNER_MAX_BYTES = CONTAINER_SCAN_MAX_BYTES
# Потоковая проверка: размер блока и перекрытие между блоками (самый
# длинный маркер короче перекрытия, так что маркер на стыке не теряется).
STREAM_CHUNK = 1024 * 1024
SCAN_OVERLAP = 256
# Сколько имён записей ZIP попадает в локальный отчёт.
ZIP_ENTRIES_IN_REPORT = 200
# Сколько ключей верхнего уровня JSON попадает в локальный отчёт.
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

# Что отчёт для передачи НЕ содержит -- перечислено в самом отчёте.
TRANSFER_OMITS = (
    'file names and paths', 'source directory', 'JSON key names',
    'ZIP entry names', 'SQLite table names', 'route hardware_ids',
    'secret marker names', 'analysis error messages',
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


def real_path(path):
    """Фактическое расположение: символические ссылки, junction и регистр
    (Windows) разрешены. Несуществующий хвост разрешается по существующей
    части пути."""
    return os.path.normcase(os.path.realpath(path))


def is_within(child, parent):
    """True, если `child` ФАКТИЧЕСКИ лежит внутри `parent` (или совпадает).

    [REASON]: сравниваются реальные пути. Папка вывода, заданная как
    символическая ссылка или junction на папку внутри источника, по строкам
    выглядит внешней, а пишет внутрь источника -- это воспроизведено ревизией
    на синтетике.
    """
    child = real_path(child)
    parent = real_path(parent)
    try:
        return os.path.commonpath([child, parent]) == parent
    except ValueError:
        return False


def same_file(a, b):
    """Один и тот же объект файловой системы (включая жёсткие ссылки)."""
    if real_path(a) == real_path(b):
        return True
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def guard_output_file(path, inputs=(), source_dirs=()):
    """Отказ, если выходной файл нельзя создать безопасно.

    Условия: файл не существует (инструмент ничего не перезаписывает --
    ни источник, ни прежний отчёт); реальное расположение не совпадает ни с
    одним входом и не лежит ни в папке входа, ни в папке-источнике.
    Проверяется ДО открытия файла; само открытие -- режимом `x`.
    """
    if os.path.lexists(path):
        raise ProbeError('output already exists and the probe never '
                         'overwrites anything: %s' % path)
    target = real_path(path)
    for item in inputs:
        if same_file(path, item) or target == real_path(item):
            raise ProbeError('output must not be an input: %s' % path)
        if is_within(path, os.path.dirname(os.path.abspath(item))):
            raise ProbeError('output must lie outside the directory of '
                             'every input (inputs are never written to): %s'
                             % path)
    for source in source_dirs:
        if is_within(path, source):
            raise ProbeError('output must lie outside the source directory '
                             '(the source is never written to): %s' % path)
    return target


def guard_output_dir(path, inputs=(), source_dirs=(), must_be_empty=True):
    """Отказ, если выходную папку нельзя использовать безопасно."""
    if os.path.lexists(path) and not os.path.isdir(path):
        raise ProbeError('output directory is a file: %s' % path)
    if must_be_empty and os.path.isdir(path) and os.listdir(path):
        raise ProbeError('target directory is not empty: %s' % path)
    for item in inputs:
        if is_within(path, os.path.dirname(os.path.abspath(item))):
            raise ProbeError('output directory must lie outside the '
                             'directory of every input: %s' % path)
    for source in source_dirs:
        if is_within(path, source) or is_within(source, path):
            raise ProbeError('output directory and the source directory must '
                             'not contain each other: %s' % path)
    return real_path(path)


def open_new(path):
    """Только создание нового файла: существующий никогда не перезаписывается."""
    return open(path, 'x', encoding='utf-8')


def sha256_file(path, chunk=STREAM_CHUNK):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


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


def stream_hash_and_scan(handle, limit=None, chunk=STREAM_CHUNK):
    """Потоковый SHA-256 и проверка на маркеры без чтения всего в память.

    Возвращает (sha256, markers, bytes_read, truncated). `limit` ограничивает
    число прочитанных байтов; `truncated` -- остались непрочитанные.
    """
    digest = hashlib.sha256()
    found = set()
    tail = b''
    total = 0
    truncated = False
    while True:
        want = chunk if limit is None else min(chunk, limit - total)
        if want <= 0:
            truncated = bool(handle.read(1))
            break
        block = handle.read(want)
        if not block:
            break
        total += len(block)
        digest.update(block)
        found.update(find_secret_markers(tail + block))
        tail = block[-SCAN_OVERLAP:] if SCAN_OVERLAP else b''
    return digest.hexdigest(), sorted(found), total, truncated


def name_markers(name):
    """Маркеры в имени (файла, ключа, записи, таблицы)."""
    return find_secret_markers(str(name).encode('utf-8', 'replace'))


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


def kind_by_magic(head):
    for magic, kind in MAGIC:
        if head.startswith(magic):
            return kind
    return None


def classify(blob):
    """Класс содержимого по байтам. Никогда не догадка: UNKNOWN_BINARY -- ответ."""
    blob = bytes(blob)
    if not blob:
        return KIND_EMPTY
    kind = kind_by_magic(blob)
    if kind:
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
    times = [f['t'] for f in frames if 't' in f]
    result['duplicate_t_frames'] = len(times) - len(set(times))
    result['t_monotone_nondecreasing'] = all(
        a <= b for a, b in zip(times, times[1:]))
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


def sqlite_uri(path):
    """URI только для чтения с экранированием спецсимволов пути.

    [REASON]: `#`, `%`, пробел и не-ASCII в имени файла ломают разбор URI:
    всё после `#` становится фрагментом, параметры `mode=ro&immutable=1`
    теряются, и SQLite открывает (и создаёт!) ДРУГОЙ файл в папке-источнике
    в режиме чтения-записи. Воспроизведено ревизией на `records#copy.db`.
    `Path.as_uri()` кодирует такие символы; после открытия
    `PRAGMA database_list` сверяется с ожидаемым файлом.
    """
    return (pathlib.Path(os.path.realpath(path)).as_uri()
            + '?mode=ro&immutable=1')


def sqlite_shape(path):
    """Имена таблиц и число строк. Открытие только `mode=ro&immutable=1`.

    Ошибка открытия или чтения называется ошибкой (`status = ERROR`), а не
    пустой базой. Открытый файл сверяется с запрошенным; при несовпадении
    соединение закрывается без единого запроса к данным.
    """
    out = {'status': 'ERROR', 'tables': [], 'error': None,
           'opened_path_verified': False}
    expected = real_path(path)
    try:
        con = sqlite3.connect(sqlite_uri(path), uri=True)
    except (sqlite3.Error, ValueError) as exc:
        out['error'] = type(exc).__name__
        return out
    try:
        con.execute('PRAGMA query_only = 1')
        opened = [row[2] for row in con.execute('PRAGMA database_list')
                  if row[1] == 'main']
        if len(opened) != 1 or real_path(opened[0]) != expected:
            out['error'] = 'OPENED_PATH_MISMATCH'
            return out
        out['opened_path_verified'] = True
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
        out['status'] = 'OK'
    except sqlite3.Error as exc:
        out['error'] = type(exc).__name__
    finally:
        con.close()
    return out


def zip_shape(path, scan_budget=CONTAINER_SCAN_MAX_BYTES):
    """Список записей ZIP и проверка их содержимого на маркеры.

    На диск ничего не распаковывается: каждая запись читается потоком в
    память блоками в пределах общего бюджета `scan_budget`. Что не удалось
    прочитать (шифрование, неподдерживаемое сжатие, повреждение, бюджет,
    вложенный контейнер), перечисляется в `scan_reasons` -- проверка тогда
    частичная, и это сказано прямо.
    """
    out = {'entries_total': 0, 'entries': [], 'flags': [],
           'compressed_total': 0, 'uncompressed_total': 0,
           'scan_markers': [], 'scan_reasons': [], 'scan_bytes': 0,
           'name_markers': []}
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError, ValueError, EOFError) as exc:
        out['error'] = type(exc).__name__
        out['scan_reasons'] = ['ZIP_ERROR:%s' % type(exc).__name__]
        return out
    flags = set()
    markers = set()
    reasons = set()
    remaining = scan_budget
    with archive:
        try:
            infos = archive.infolist()
        except (zipfile.BadZipFile, OSError, ValueError, EOFError) as exc:
            out['error'] = type(exc).__name__
            out['scan_reasons'] = ['ZIP_ERROR:%s' % type(exc).__name__]
            return out
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
            in_name = name_markers(name)
            shown = REDACTED if in_name else name
            if in_name:
                out['name_markers'].extend(in_name)
            if len(out['entries']) < ZIP_ENTRIES_IN_REPORT:
                out['entries'].append({'name': shown, 'size': info.file_size,
                                       'compressed': info.compress_size})
            if info.is_dir():
                continue
            if info.flag_bits & 0x1:
                reasons.add('ZIP_ENCRYPTED_ENTRY')
                continue
            if remaining <= 0:
                reasons.add('ZIP_OVER_SCAN_LIMIT')
                continue
            try:
                with archive.open(info) as handle:
                    head = handle.read(4)
                    if head.startswith(CONTAINER_MAGIC):
                        reasons.add('ZIP_NESTED_CONTAINER')
                    _sha, found, read, truncated = stream_hash_and_scan(
                        _Chain(head, handle), limit=remaining)
            except (zipfile.BadZipFile, RuntimeError, NotImplementedError,
                    zlib.error, OSError, EOFError, ValueError) as exc:
                reasons.add('ZIP_ENTRY_ERROR:%s' % type(exc).__name__)
                continue
            markers.update(found)
            out['scan_bytes'] += read
            remaining -= read
            if truncated:
                reasons.add('ZIP_OVER_SCAN_LIMIT')
    if out['compressed_total'] and \
            out['uncompressed_total'] > 100 * out['compressed_total']:
        flags.add('ZIP_HIGH_RATIO')
    out['flags'] = sorted(flags)
    out['scan_markers'] = sorted(markers)
    out['scan_reasons'] = sorted(reasons)
    out['name_markers'] = sorted(set(out['name_markers']))
    return out


class _Chain(object):
    """Читатель: сначала уже прочитанная голова, затем остаток потока."""

    def __init__(self, head, handle):
        self._head = head
        self._handle = handle

    def read(self, size=-1):
        if self._head:
            if size is None or size < 0 or size >= len(self._head):
                head, self._head = self._head, b''
                rest = self._handle.read(-1 if size is None or size < 0
                                         else size - len(head))
                return head + (rest or b'')
            head, self._head = self._head[:size], self._head[size:]
            return head
        return self._handle.read(size)


def json_shape(blob):
    """Тип верхнего уровня и имена ключей. Значения не выходят.

    Ключ с маркером секрета в отчёт не попадает -- заменяется `<redacted>`
    (ревизия: значение, спрятанное в имени ключа, уходило в опись).
    """
    try:
        document = json.loads(bytes(blob).decode('utf-8-sig'))
    except ValueError:
        return {'error': 'ValueError'}
    if isinstance(document, dict):
        keys = sorted(document.keys())
        markers = set()
        shown = []
        for key in keys[:JSON_KEYS_IN_REPORT]:
            found = name_markers(key)
            if found:
                markers.update(found)
                shown.append(REDACTED)
            else:
                shown.append(key)
        return {'top_type': 'object', 'key_count': len(keys), 'keys': shown,
                'key_markers': sorted(markers)}
    if isinstance(document, list):
        return {'top_type': 'array', 'length': len(document)}
    return {'top_type': type(document).__name__}


def gzip_inner(source, limit=CONTAINER_SCAN_MAX_BYTES):
    """Ограниченная потоковая распаковка GZIP: (inner_bytes | None, meta).

    `source` -- байты или открытый двоичный поток. Повреждённый, усечённый
    или слишком большой архив не роняет опись: ошибка объекта записывается
    по имени типа (без содержимого), и разбор объявляется частичным.
    """
    fileobj = io.BytesIO(bytes(source)) if isinstance(
        source, (bytes, bytearray)) else source
    meta = {'truncated': False, 'inner_size_bytes': 0, 'error': None}
    chunks = []
    total = 0
    try:
        with gzip.GzipFile(fileobj=fileobj) as handle:
            while total <= limit:
                block = handle.read(min(STREAM_CHUNK, limit + 1 - total))
                if not block:
                    break
                chunks.append(block)
                total += len(block)
    except (OSError, EOFError, zlib.error, ValueError) as exc:
        meta['error'] = type(exc).__name__
        if not chunks:
            return None, meta
    inner = b''.join(chunks)
    meta['truncated'] = len(inner) > limit
    meta['inner_size_bytes'] = min(len(inner), limit)
    return inner[:limit], meta


# ─── Один файл ───────────────────────────────────────────────────────────────

def new_scan():
    return {'status': SCAN_NOT_SCANNED, 'markers': [], 'where': [],
            'reasons': [], 'content_bytes_scanned': 0,
            'content_fully_scanned': False, 'container_bytes_scanned': 0}


def scan_add(scan, markers=(), where=None, reason=None):
    if markers:
        scan['markers'] = sorted(set(scan['markers']) | set(markers))
        if where:
            scan['where'] = sorted(set(scan['where']) | {where})
    if reason:
        scan['reasons'] = sorted(set(scan['reasons']) | {reason})


def scan_finalize(scan):
    if scan['markers']:
        scan['status'] = SCAN_MARKER_FOUND
    elif scan['reasons']:
        scan['status'] = SCAN_PARTIAL
    elif scan['content_fully_scanned']:
        scan['status'] = SCAN_CLEAN
    else:
        scan['status'] = SCAN_NOT_SCANNED
    return scan


def _analyse_bytes(blob, depth, scan):
    """Разбор уже прочитанных байтов: класс, формы, маркеры внутри GZIP."""
    kind = classify(blob)
    details = {}
    flags = []
    if kind == KIND_PROTOBUF:
        details['protobuf_census'] = protobuf_census(blob, depth)
        details['v4_shape'] = v4_shape(blob)
        details['route_shape'] = route_shape(blob)
    elif kind == KIND_JSON:
        shape = json_shape(blob)
        details['json_shape'] = shape
        if shape.get('key_markers'):
            flags.append('SECRET_MARKER_IN_NAME')
            scan_add(scan, shape['key_markers'], 'json_key')
    elif kind == 'GZIP':
        inner, meta = gzip_inner(blob)
        details['gzip'] = meta
        if meta.get('error'):
            scan_add(scan, reason='GZIP_ERROR:%s' % meta['error'])
            flags.append('CONTAINER_ERROR')
        if meta.get('truncated'):
            scan_add(scan, reason='GZIP_OVER_SCAN_LIMIT')
        if inner is not None:
            scan['container_bytes_scanned'] += len(inner)
            inner_kind = classify(inner)
            meta['inner_kind'] = inner_kind
            meta['inner_sha256'] = hashlib.sha256(inner).hexdigest()
            inner_markers = find_secret_markers(inner)
            if inner_markers:
                flags.append('SECRET_MARKER_INSIDE_GZIP')
                meta['secret_markers'] = inner_markers
                scan_add(scan, inner_markers, 'gzip_inner')
            if inner_kind == KIND_PROTOBUF:
                meta['protobuf_census'] = protobuf_census(inner, depth)
                meta['v4_shape'] = v4_shape(inner)
                meta['route_shape'] = route_shape(inner)
            elif inner_kind == KIND_JSON:
                meta['json_shape'] = json_shape(inner)
                if meta['json_shape'].get('key_markers'):
                    flags.append('SECRET_MARKER_IN_NAME')
                    scan_add(scan, meta['json_shape']['key_markers'],
                             'json_key')
            if inner.startswith(CONTAINER_MAGIC):
                scan_add(scan, reason='GZIP_NESTED_CONTAINER')
    return kind, flags, details


def _scan_large_gzip(path, scan):
    """Потоковая проверка содержимого большого GZIP без классификации."""
    try:
        with open(path, 'rb') as raw, gzip.GzipFile(fileobj=raw) as handle:
            _sha, markers, read, truncated = stream_hash_and_scan(
                handle, limit=CONTAINER_SCAN_MAX_BYTES)
    except (OSError, EOFError, zlib.error, ValueError) as exc:
        scan_add(scan, reason='GZIP_ERROR:%s' % type(exc).__name__)
        return
    scan['container_bytes_scanned'] += read
    scan_add(scan, markers, 'gzip_inner')
    if truncated:
        scan_add(scan, reason='GZIP_OVER_SCAN_LIMIT')


def probe_file(path, deep_max_bytes=DEFAULT_DEEP_MAX_BYTES, depth=2,
               now=None):
    """Описание одного файла. Файл открывается только на чтение.

    Содержимое ЛЮБОГО размера проверяется на маркеры целиком (потоком);
    глубокий разбор -- только до `deep_max_bytes`. Ошибка разбора одного
    объекта записывается по имени типа и не роняет опись.
    """
    if not os.path.isfile(path):
        raise ProbeError('not a file: %s' % path)
    stat = os.stat(path)
    now = time.time() if now is None else now
    age = max(0.0, now - stat.st_mtime)
    scan = new_scan()
    entry = {
        'size_bytes': stat.st_size,
        'mtime_utc': utc_iso(stat.st_mtime),
        'mtime_age_s': round(age, 1),
        'deep_analysis': stat.st_size <= deep_max_bytes,
        'flags': [],
        'details': {},
    }
    if age < RECENTLY_MODIFIED_S:
        entry['flags'].append('RECENTLY_MODIFIED')
    blob = None
    try:
        if entry['deep_analysis']:
            with open(path, 'rb') as handle:
                blob = handle.read()
            entry['sha256'] = hashlib.sha256(blob).hexdigest()
            markers = find_secret_markers(blob)
            scan['content_bytes_scanned'] = len(blob)
        else:
            with open(path, 'rb') as handle:
                entry['sha256'], markers, read, _t = stream_hash_and_scan(handle)
            scan['content_bytes_scanned'] = read
        scan['content_fully_scanned'] = True
    except OSError as exc:
        entry['sha256'] = None
        entry['flags'].append('READ_ERROR')
        entry['details']['read_error'] = type(exc).__name__
        scan_add(scan, reason='READ_ERROR:%s' % type(exc).__name__)
        entry['kind'] = KIND_UNKNOWN
        entry['secret_scan'] = scan_finalize(scan)
        entry['flags'] = sorted(set(entry['flags']))
        return entry
    if markers:
        entry['flags'].append('SECRET_MARKER')
        entry['details']['secret_markers'] = markers
        scan_add(scan, markers, 'content')
    kind = None
    if blob is not None:
        try:
            kind, flags, details = _analyse_bytes(blob, depth, scan)
            entry['flags'].extend(flags)
            entry['details'].update(details)
        except Exception as exc:  # noqa: BLE001 -- один объект, не вся опись
            kind = kind_by_magic(blob[:64]) or KIND_UNKNOWN
            entry['flags'].append('ANALYSIS_ERROR')
            entry['details']['analysis_error'] = type(exc).__name__
            scan_add(scan, reason='ANALYSIS_ERROR:%s' % type(exc).__name__)
    else:
        with open(path, 'rb') as handle:
            head = handle.read(64)
        kind = kind_by_magic(head) or KIND_UNKNOWN
        entry['flags'].append('TOO_LARGE_FOR_DEEP_ANALYSIS')
        if kind == 'GZIP':
            _scan_large_gzip(path, scan)
    entry['kind'] = kind
    if kind == 'SQLITE':
        shape = sqlite_shape(path)
        table_markers = set()
        for table in shape['tables']:
            found = name_markers(table['name'])
            if found:
                table_markers.update(found)
                table['name'] = REDACTED
        if table_markers:
            entry['flags'].append('SECRET_MARKER_IN_NAME')
            scan_add(scan, sorted(table_markers), 'sqlite_table_name')
        if shape['status'] != 'OK':
            entry['flags'].append('SQLITE_READ_ERROR')
        entry['details']['sqlite'] = shape
        siblings = [path + suffix for suffix in ('-wal', '-journal', '-shm')
                    if os.path.exists(path + suffix)]
        if siblings:
            entry['flags'].append('SQLITE_WAL_OR_JOURNAL_PRESENT')
            shape['siblings'] = [os.path.basename(s) for s in siblings]
            shape['wal_not_applied'] = True
    elif kind == 'ZIP':
        shape = zip_shape(path)
        entry['details']['zip'] = shape
        entry['flags'].extend(shape.get('flags', ()))
        if shape.get('scan_markers'):
            entry['flags'].append('SECRET_MARKER_INSIDE_ZIP')
            scan_add(scan, shape['scan_markers'], 'zip_entry')
        if shape.get('name_markers'):
            entry['flags'].append('SECRET_MARKER_IN_NAME')
            scan_add(scan, shape['name_markers'], 'zip_entry_name')
        for reason in shape.get('scan_reasons', ()):
            scan_add(scan, reason=reason)
        if shape.get('error'):
            entry['flags'].append('CONTAINER_ERROR')
        scan['container_bytes_scanned'] += shape.get('scan_bytes', 0)
    entry['secret_scan'] = scan_finalize(scan)
    if scan['status'] == SCAN_PARTIAL:
        entry['flags'].append('SECRET_SCAN_PARTIAL')
    elif scan['status'] == SCAN_NOT_SCANNED:
        entry['flags'].append('SECRET_SCAN_NOT_SCANNED')
    entry['flags'] = sorted(set(entry['flags']))
    return entry


def apply_name_policy(entry, relpath):
    """Имя файла с маркером секрета в отчёт не попадает."""
    found = name_markers(relpath)
    entry['relpath'] = REDACTED if found else relpath
    if found:
        scan_add(entry['secret_scan'], found, 'name')
        scan_finalize(entry['secret_scan'])
        entry['flags'] = sorted(set(entry['flags']) | {'SECRET_MARKER_IN_NAME'})
    return entry


# ─── Инвентаризация папки ────────────────────────────────────────────────────

def _is_link(path):
    if os.path.islink(path):
        return True
    isjunction = getattr(os.path, 'isjunction', None)
    return bool(isjunction and isjunction(path))


def iter_files(root):
    """Регулярные файлы под `root`; символические ссылки и junction не
    следуются (ни папки, ни файлы), порядок -- по именам."""
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames
                             if not _is_link(os.path.join(dirpath, d)))
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if _is_link(full) or not os.path.isfile(full):
                continue
            yield full


def inventory(source_dir, device_label, deep_max_bytes=DEFAULT_DEEP_MAX_BYTES,
              depth=2, now=None):
    """Опись папки. Словарь, пригодный для JSON; в папку ничего не пишется."""
    if not os.path.isdir(source_dir):
        raise ProbeError('source directory not found: %s' % source_dir)
    files = []
    by_kind = {}
    by_scan = {}
    total_bytes = 0
    flagged_secret = 0
    v4_like = 0
    route_like = 0
    partial = 0
    errors = 0
    for index, full in enumerate(iter_files(source_dir), start=1):
        entry = probe_file(full, deep_max_bytes, depth, now)
        apply_name_policy(entry, os.path.relpath(full, source_dir).replace('\\', '/'))
        entry['index'] = index
        files.append(entry)
        by_kind[entry['kind']] = by_kind.get(entry['kind'], 0) + 1
        status = entry['secret_scan']['status']
        by_scan[status] = by_scan.get(status, 0) + 1
        total_bytes += entry['size_bytes']
        if status == SCAN_MARKER_FOUND:
            flagged_secret += 1
        elif status in (SCAN_PARTIAL, SCAN_NOT_SCANNED):
            partial += 1
        if 'ANALYSIS_ERROR' in entry['flags'] or 'READ_ERROR' in entry['flags']:
            errors += 1
        shapes = [entry['details'].get('v4_shape'),
                  (entry['details'].get('gzip') or {}).get('v4_shape')]
        if any(s and s.get('status') == 'V4_LIKE' for s in shapes):
            v4_like += 1
        shapes = [entry['details'].get('route_shape'),
                  (entry['details'].get('gzip') or {}).get('route_shape')]
        if any(s and s.get('status') == 'ROUTE_LIKE' for s in shapes):
            route_like += 1
    status = 'COMPLETE' if not (partial or errors) else 'PARTIAL'
    return {
        'probe_version': PROBE_VERSION,
        'origin': ORIGIN,
        'origin_basis': ORIGIN_BASIS,
        'generated_at_utc': utc_iso(now),
        'device_label': device_label,
        'source_dir': os.path.abspath(source_dir),
        'status': status,
        'totals': {
            'files': len(files),
            'bytes': total_bytes,
            'by_kind': dict(sorted(by_kind.items())),
            'by_secret_scan': dict(sorted(by_scan.items())),
            'flagged_secret': flagged_secret,
            'partially_scanned': partial,
            'analysis_errors': errors,
            'v4_like': v4_like,
            'route_like': route_like,
        },
        'files': files,
    }


def _transfer_details(details):
    """Детали без имён и строк данных: только числа, статусы, хеши."""
    out = {}
    if 'protobuf_census' in details:
        out['protobuf_census'] = details['protobuf_census']
    if 'v4_shape' in details:
        out['v4_shape'] = details['v4_shape']
    if 'route_shape' in details:
        shape = dict(details['route_shape'])
        shape['hardware_id_count'] = len(shape.pop('hardware_ids', []) or [])
        out['route_shape'] = shape
    if 'json_shape' in details:
        shape = details['json_shape']
        out['json_shape'] = {k: shape[k] for k in ('top_type', 'key_count',
                                                   'length', 'error')
                             if k in shape}
    if 'zip' in details:
        shape = details['zip']
        out['zip'] = {k: shape[k] for k in (
            'entries_total', 'compressed_total', 'uncompressed_total',
            'flags', 'scan_reasons', 'scan_bytes', 'error') if k in shape}
    if 'sqlite' in details:
        shape = details['sqlite']
        out['sqlite'] = {
            'status': shape['status'], 'error': shape['error'],
            'opened_path_verified': shape.get('opened_path_verified'),
            'table_count': len(shape['tables']),
            'row_counts': sorted((t['rows'] for t in shape['tables']
                                  if t.get('rows') is not None), reverse=True),
            'wal_not_applied': shape.get('wal_not_applied', False),
        }
    if 'gzip' in details:
        meta = dict(details['gzip'])
        meta.pop('secret_markers', None)
        for key in ('protobuf_census', 'v4_shape', 'route_shape', 'json_shape'):
            if key in meta:
                meta[key] = _transfer_details({key: meta[key]})[key]
        out['gzip'] = meta
    for key in ('analysis_error', 'read_error'):
        if key in details:
            out[key] = details[key]
    return out


def transfer_view(report):
    """Отчёт для передачи: без имён, путей, ключей и маркеров."""
    files = []
    for entry in report['files']:
        scan = entry['secret_scan']
        files.append({
            'index': entry['index'],
            'size_bytes': entry['size_bytes'],
            'mtime_utc': entry['mtime_utc'],
            'sha256': entry['sha256'],
            'kind': entry['kind'],
            'flags': entry['flags'],
            'deep_analysis': entry['deep_analysis'],
            'secret_scan': {'status': scan['status'],
                            'reasons': scan['reasons'],
                            'content_fully_scanned': scan['content_fully_scanned']},
            'details': _transfer_details(entry['details']),
        })
    return {
        'probe_version': report['probe_version'],
        'origin': report['origin'],
        'origin_basis': report['origin_basis'],
        'generated_at_utc': report['generated_at_utc'],
        'device_label': report['device_label'],
        'status': report['status'],
        'report_kind': 'TRANSFER',
        'omitted_for_transfer': list(TRANSFER_OMITS),
        'totals': report['totals'],
        'files': files,
    }


def render_markdown(report, transfer=False):
    """Отчёт для человека (UTF-8, в файл). Значений данных здесь нет."""
    totals = report['totals']

    def label(entry):
        if transfer:
            return '#%d %s' % (entry['index'], (entry['sha256'] or '')[:16])
        return entry.get('relpath', '#%d' % entry['index'])

    lines = [
        '# Обследование файлов с пульта DJI -- %s%s' % (
            report['device_label'], ' (отчёт для передачи)' if transfer else ''),
        '',
        'Инструмент `%s`, происхождение `%s` (заявлено оператором, не '
        'проверено), сформировано %s (UTC). Статус описи: `%s`.'
        % (report['probe_version'], report['origin'],
           report['generated_at_utc'], report['status']),
        '',
    ]
    if transfer:
        lines += ['В этом отчёте нет: %s.' % ', '.join(TRANSFER_OMITS), '']
    else:
        lines += ['Папка-источник: `%s`. Этот файл -- локальная опись с '
                  'именами; для передачи предназначен `inventory_transfer.*`.'
                  % report['source_dir'], '']
    lines += [
        'Файлов: %d, байт: %d. Классы: %s.'
        % (totals['files'], totals['bytes'],
           ', '.join('%s %d' % kv for kv in totals['by_kind'].items())
           or 'нет'),
        '',
        'Проверка на маркеры секретов: %s. С маркером: %d (не отправлять и не '
        'публиковать); проверено частично или не проверено: %d; ошибок '
        'разбора: %d. Похожи на V4 облака: %d. Похожи на маршрут облака: %d.'
        % (', '.join('%s %d' % kv for kv in totals['by_secret_scan'].items())
           or 'нет файлов', totals['flagged_secret'],
           totals['partially_scanned'], totals['analysis_errors'],
           totals['v4_like'], totals['route_like']),
        '',
        '**Что это доказывает.** Только то, что перечисленные байты лежат в '
        'папке и разбираются (или не разбираются) декодерами проекта. '
        'Отсутствие известного маркера не доказывает отсутствие секретов. '
        'Происхождение, подлинность DJI, полноту и смысл полей этот отчёт '
        'не подтверждает.',
        '',
        '| Файл | Байт | Класс | Проверка секретов | Флаги | SHA-256 (первые 16) | Изменён (UTC) |',
        '|---|---|---|---|---|---|---|',
    ]
    for entry in report['files']:
        lines.append('| `%s` | %d | %s | %s | %s | `%s` | %s |' % (
            label(entry), entry['size_bytes'], entry['kind'],
            entry['secret_scan']['status'],
            ', '.join(entry['flags']) or '—', (entry['sha256'] or '')[:16],
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
                '## `%s` -- %sпохож на V4 облака' % (label(entry), prefix),
                '',
                'Кадров: %d (повторов времени %d); окно по кадрам: %s .. %s '
                '(unix ms), span %s с; dt медиана %s с; кадров с позицией %d, '
                'с шириной %d, с блоком распыления %d, с расходом %d; счётчик '
                'площади закодирован в %d кадрах (ведущих пропусков %s).'
                % (summary['frame_count'], shape.get('duplicate_t_frames', 0),
                   summary['t_first_ms'], summary['t_last_ms'],
                   summary['span_s'], summary['dt_median_s'],
                   summary['frames_with_position'],
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

def _v4_side(blob):
    from dji_area import v4
    side = {'size_bytes': len(blob), 'sha256': hashlib.sha256(blob).hexdigest()}
    try:
        decoded = v4.decode_v4(blob)
    except v4.V4DecodeError as exc:
        side['decoded'] = False
        side['error'] = type(exc).__name__
        return side, None
    side['decoded'] = True
    summary = v4.summarize_v4(decoded)
    side['summary'] = {key: summary.get(key) for key in V4_SUMMARY_KEYS}
    side['frame_key_presence'] = {
        key: sum(1 for f in decoded.frames if key in f) for key in V4_FRAME_KEYS}
    return side, decoded.frames


def _group_by_t(frames):
    groups = {}
    without_t = 0
    for frame in frames:
        if 't' not in frame:
            without_t += 1
            continue
        groups.setdefault(frame['t'], []).append(frame)
    return groups, without_t


def compare_v4_blobs(blob_a, blob_b):
    """Сравнение двух тел, разобранных как V4: байты, окна, присутствие.

    Кадры сопоставляются по времени; при ПОВТОРЯЮЩЕМСЯ времени кадры с
    одной меткой сопоставляются попарно в порядке файла, лишние остаются
    несопоставленными, и сравнение объявляется неоднозначным
    (`pairing = AMBIGUOUS_DUPLICATE_T`). Ни один кадр не перезаписывает
    другой (ревизия: словарь «время → кадр» терял кадры с равным временем).
    Личность вылета тела не несут (V4 без flight id); совпадение окон -- не
    доказательство того же вылета. Координаты не выводятся.
    """
    out = {'probe_version': PROBE_VERSION,
           'identity_proof': 'NONE -- V4 carries no flight id; equal windows '
                             'are not proof of the same flight'}
    side_a, frames_a = _v4_side(bytes(blob_a))
    side_b, frames_b = _v4_side(bytes(blob_b))
    out['identical_bytes'] = side_a['sha256'] == side_b['sha256']
    out['a'] = side_a
    out['b'] = side_b
    if frames_a is None or frames_b is None:
        return out
    groups_a, without_a = _group_by_t(frames_a)
    groups_b, without_b = _group_by_t(frames_b)
    common = sorted(set(groups_a) & set(groups_b))
    pairs = []
    unpaired = 0
    ambiguous = 0
    for t in common:
        la, lb = groups_a[t], groups_b[t]
        n = min(len(la), len(lb))
        pairs.extend(zip(la[:n], lb[:n]))
        unpaired += abs(len(la) - len(lb))
        if len(la) > 1 or len(lb) > 1:
            ambiguous += 1
    dup_a = len(frames_a) - without_a - len(groups_a)
    dup_b = len(frames_b) - without_b - len(groups_b)
    times_a = [f['t'] for f in frames_a if 't' in f]
    times_b = [f['t'] for f in frames_b if 't' in f]
    # Окно -- по наименьшему и наибольшему времени, не по первому и
    # последнему кадру: у неупорядоченного файла они не совпадают.
    overlap = None
    if times_a and times_b:
        overlap = max(0, min(max(times_a), max(times_b))
                      - max(min(times_a), min(times_b)))

    def equal(key, require_present=True):
        count = 0
        for fa, fb in pairs:
            if key in fa and (fa.get(key) is not None or not require_present) \
                    and fa.get(key) == fb.get(key) and key in fb:
                count += 1
        return count

    out['comparison'] = {
        'frames_a': len(frames_a),
        'frames_b': len(frames_b),
        'frames_without_t_a': without_a,
        'frames_without_t_b': without_b,
        'distinct_t_a': len(groups_a),
        'distinct_t_b': len(groups_b),
        'duplicate_t_frames_a': dup_a,
        'duplicate_t_frames_b': dup_b,
        't_monotone_a': all(x <= y for x, y in zip(times_a, times_a[1:])),
        't_monotone_b': all(x <= y for x, y in zip(times_b, times_b[1:])),
        'common_t_count': len(common),
        'ambiguous_common_t_count': ambiguous,
        'unpaired_frames_at_common_t': unpaired,
        'pairing': ('AMBIGUOUS_DUPLICATE_T' if (dup_a or dup_b)
                    else 'UNAMBIGUOUS'),
        'overlap_ms': overlap,
        'window_a_ms': [min(times_a), max(times_a)] if times_a else None,
        'window_b_ms': [min(times_b), max(times_b)] if times_b else None,
        'frames_with_equal_t': len(pairs),
        'equal_t_frames_with_equal_area_bits': equal('area_bits'),
        'equal_t_frames_with_equal_width': equal('width'),
        'equal_t_frames_with_equal_spray_flag': equal('spray_flag', False),
        'equal_t_frames_with_equal_position': sum(
            1 for fa, fb in pairs
            if 'lat' in fa and 'lng' in fa and 'lat' in fb and 'lng' in fb
            and fa['lat'] == fb['lat'] and fa['lng'] == fb['lng']),
        'keys_present_only_in_a': sorted(
            k for k in V4_FRAME_KEYS
            if side_a['frame_key_presence'][k]
            and not side_b['frame_key_presence'][k]),
        'keys_present_only_in_b': sorted(
            k for k in V4_FRAME_KEYS
            if side_b['frame_key_presence'][k]
            and not side_a['frame_key_presence'][k]),
    }
    return out


def compare_v4(path_a, path_b):
    """Сравнение двух файлов, разобранных как V4 (см. `compare_v4_blobs`)."""
    blobs = []
    for path in (path_a, path_b):
        if not os.path.isfile(path):
            raise ProbeError('not a file: %s' % path)
        with open(path, 'rb') as handle:
            blobs.append(handle.read())
    out = compare_v4_blobs(blobs[0], blobs[1])
    out['origin_a'] = ORIGIN
    out['origin_basis_a'] = ORIGIN_BASIS
    out['a']['path'] = os.path.abspath(path_a)
    out['b']['path'] = os.path.abspath(path_b)
    return out


# ─── Дамп сухого прогона сборщика ────────────────────────────────────────────

def read_dump(path):
    """Дамп `--sources --dry-run` сборщика (`write_sources_dry_run`).

    Принимается словарь со списком `sources`, каждый элемент которого несёт
    `flight_id`, `source_type`, `body_b64`, `sha256`, `size_bytes`. Поля
    `dry_run` и `nothing_was_queued` записываются как наблюдение.
    """
    if not os.path.isfile(path):
        raise ProbeError('dump not found: %s' % path)
    with open(path, 'rb') as handle:
        blob = handle.read()
    try:
        document = json.loads(blob.decode('utf-8-sig'))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ProbeError('dump is not JSON: %s' % type(exc).__name__)
    if not isinstance(document, dict) or not isinstance(
            document.get('sources'), list):
        raise ProbeError('dump has no `sources` list')
    return document


def dump_items(document, source_type=None, flight_id=None):
    """Элементы дампа с перепроверенным хешем тела. Тело не печатается."""
    rows = []
    for position, item in enumerate(document['sources']):
        if not isinstance(item, dict):
            continue
        row = {'position': position,
               'flight_id': item.get('flight_id'),
               'source_type': item.get('source_type'),
               'declared_sha256': item.get('sha256'),
               'declared_size_bytes': item.get('size_bytes')}
        if source_type is not None and row['source_type'] != source_type:
            continue
        if flight_id is not None:
            try:
                if int(row['flight_id']) != int(flight_id):
                    continue
            except (TypeError, ValueError):
                continue
        try:
            body = base64.b64decode(item.get('body_b64') or '', validate=True)
        except (ValueError, TypeError) as exc:
            row['status'] = 'BODY_UNDECODABLE:%s' % type(exc).__name__
            rows.append(row)
            continue
        row['body'] = body
        row['sha256'] = hashlib.sha256(body).hexdigest()
        row['size_bytes'] = len(body)
        row['markers'] = find_secret_markers(body)
        if row['markers']:
            row['status'] = 'MARKER_FOUND'
        elif row['declared_sha256'] and row['declared_sha256'] != row['sha256']:
            row['status'] = 'HASH_MISMATCH'
        elif (row['declared_size_bytes'] is not None
              and row['declared_size_bytes'] != row['size_bytes']):
            row['status'] = 'SIZE_MISMATCH'
        else:
            row['status'] = 'OK'
        rows.append(row)
    return rows


def extract_from_dump(dump_path, out_dir, source_type='v4', flight_id=None):
    """Тела из дампа в новую пустую папку с проверкой хеша каждой записи.

    Записывается только тело со статусом `OK` (хеш и размер сошлись с
    заявленными, маркеров нет), и только как НОВЫЙ файл
    `<source_type>_<flight_id>.bin`; каждый записанный файл перечитывается
    и сверяется. Эти тела -- облачные (`origin = CLOUD_DRY_RUN_DUMP`).
    """
    document = read_dump(dump_path)
    guard_output_dir(out_dir, inputs=[dump_path])
    rows = dump_items(document, source_type, flight_id)
    os.makedirs(out_dir, exist_ok=True)
    manifest = {
        'probe_version': PROBE_VERSION,
        'origin': DUMP_ORIGIN,
        'dump_path': os.path.abspath(dump_path),
        'dump_dry_run': document.get('dry_run'),
        'dump_nothing_was_queued': document.get('nothing_was_queued'),
        'dump_capture_run_id': document.get('capture_run_id'),
        'requested_source_type': source_type,
        'requested_flight_id': flight_id,
        'items': [],
        'totals': {'matched': len(rows), 'written': 0, 'verified': 0,
                   'refused': 0},
    }
    for row in rows:
        record = {k: row[k] for k in (
            'position', 'flight_id', 'source_type', 'declared_sha256',
            'declared_size_bytes', 'status') if k in row}
        record['sha256'] = row.get('sha256')
        record['size_bytes'] = row.get('size_bytes')
        if row['status'] != 'OK':
            record['written'] = None
            manifest['totals']['refused'] += 1
            manifest['items'].append(record)
            continue
        name = '%s_%s.bin' % (re.sub(r'[^A-Za-z0-9_-]+', '_', str(source_type)),
                              re.sub(r'[^0-9A-Za-z_-]+', '_', str(row['flight_id'])))
        target = os.path.join(out_dir, name)
        if os.path.lexists(target):
            record['status'] = 'TARGET_EXISTS'
            record['written'] = None
            manifest['totals']['refused'] += 1
            manifest['items'].append(record)
            continue
        with open(target, 'xb') as handle:
            handle.write(row['body'])
        record['written'] = name
        record['verified'] = sha256_file(target) == row['sha256']
        manifest['totals']['written'] += 1
        if record['verified']:
            manifest['totals']['verified'] += 1
        manifest['items'].append(record)
    manifest['status'] = ('OK' if manifest['totals']['refused'] == 0
                          and manifest['totals']['verified']
                          == manifest['totals']['written']
                          else 'PARTIAL')
    with open_new(os.path.join(out_dir, 'extract_manifest.json')) as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=1,
                  sort_keys=True)
    return manifest


# ─── Сопоставление описи с дампом ────────────────────────────────────────────

def match_inventory_with_dump(inventory_path, dump_path):
    """Каждый файл описи, похожий на V4, против каждого V4 дампа.

    Файл читается заново по `source_dir` + `relpath` локальной описи, и его
    текущий хеш обязан совпасть с хешем описи -- иначе он не сравнивается
    (`FILE_CHANGED_SINCE_INVENTORY`). Лучший кандидат выбирается по
    перекрытию окон и называется кандидатом, не связью.
    """
    with open(inventory_path, 'rb') as handle:
        try:
            report = json.loads(handle.read().decode('utf-8-sig'))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ProbeError('inventory is not JSON: %s' % type(exc).__name__)
    if report.get('report_kind') == 'TRANSFER' or 'source_dir' not in report:
        raise ProbeError('match needs the LOCAL inventory.json (with relpath '
                         'and source_dir), not the transfer report')
    document = read_dump(dump_path)
    cloud = [row for row in dump_items(document, 'v4')]
    out = {
        'probe_version': PROBE_VERSION,
        'inventory_path': os.path.abspath(inventory_path),
        'dump_path': os.path.abspath(dump_path),
        'device_label': report.get('device_label'),
        'identity_proof': 'NONE -- a window overlap names a CANDIDATE, '
                          'not a link; V4 carries no flight id',
        'cloud_bodies': [{k: row.get(k) for k in (
            'flight_id', 'status', 'sha256', 'size_bytes')} for row in cloud],
        'rc_files': [],
        'pairs': [],
    }
    usable_cloud = [row for row in cloud if row['status'] == 'OK']
    for entry in report.get('files', ()):
        shape = (entry.get('details') or {}).get('v4_shape') or {}
        gz_shape = ((entry.get('details') or {}).get('gzip') or {}).get(
            'v4_shape') or {}
        if shape.get('status') != 'V4_LIKE':
            if gz_shape.get('status') == 'V4_LIKE':
                out['rc_files'].append({
                    'index': entry.get('index'), 'relpath': entry.get('relpath'),
                    'sha256': entry.get('sha256'),
                    'status': 'NOT_COMPARED_INSIDE_GZIP'})
            continue
        row = {'index': entry.get('index'), 'relpath': entry.get('relpath'),
               'sha256': entry.get('sha256')}
        if entry.get('relpath') in (None, REDACTED):
            row['status'] = 'NOT_COMPARED_NAME_REDACTED'
            out['rc_files'].append(row)
            continue
        path = os.path.join(report['source_dir'], *entry['relpath'].split('/'))
        if not os.path.isfile(path):
            row['status'] = 'FILE_MISSING'
            out['rc_files'].append(row)
            continue
        with open(path, 'rb') as handle:
            blob = handle.read()
        if hashlib.sha256(blob).hexdigest() != entry.get('sha256'):
            row['status'] = 'FILE_CHANGED_SINCE_INVENTORY'
            out['rc_files'].append(row)
            continue
        best = None
        for cloud_row in usable_cloud:
            result = compare_v4_blobs(blob, cloud_row['body'])
            comparison = result.get('comparison') or {}
            pair = {
                'rc_index': entry.get('index'),
                'rc_sha256': entry.get('sha256'),
                'cloud_flight_id': cloud_row['flight_id'],
                'cloud_sha256': cloud_row['sha256'],
                'identical_bytes': result['identical_bytes'],
                'cloud_decoded': result['b'].get('decoded'),
                'overlap_ms': comparison.get('overlap_ms'),
                'frames_with_equal_t': comparison.get('frames_with_equal_t', 0),
                'equal_t_frames_with_equal_area_bits': comparison.get(
                    'equal_t_frames_with_equal_area_bits', 0),
                'equal_t_frames_with_equal_position': comparison.get(
                    'equal_t_frames_with_equal_position', 0),
                'pairing': comparison.get('pairing'),
            }
            out['pairs'].append(pair)
            key = (pair['identical_bytes'], pair['overlap_ms'] or 0,
                   pair['frames_with_equal_t'])
            if best is None or key > best[0]:
                best = (key, pair)
        row['status'] = 'COMPARED' if usable_cloud else 'NO_USABLE_CLOUD_BODY'
        row['candidate_cloud_flight_id'] = best[1]['cloud_flight_id'] if best else None
        row['candidate_overlap_ms'] = best[1]['overlap_ms'] if best else None
        row['candidate_identical_bytes'] = best[1]['identical_bytes'] if best else None
        out['rc_files'].append(row)
    out['totals'] = {
        'cloud_bodies': len(cloud),
        'cloud_bodies_usable': len(usable_cloud),
        'rc_v4_like': len(out['rc_files']),
        'rc_compared': sum(1 for r in out['rc_files'] if r['status'] == 'COMPARED'),
        'pairs': len(out['pairs']),
        'identical_pairs': sum(1 for p in out['pairs'] if p['identical_bytes']),
    }
    return out


def render_match_markdown(result):
    totals = result['totals']
    lines = [
        '# Сопоставление файлов пульта с облачными V4 -- %s' % result['device_label'],
        '',
        'Облачных тел V4 в дампе: %d (пригодных %d); файлов пульта, похожих '
        'на V4: %d, сравнено %d; пар: %d, из них с одинаковыми байтами: %d.'
        % (totals['cloud_bodies'], totals['cloud_bodies_usable'],
           totals['rc_v4_like'], totals['rc_compared'], totals['pairs'],
           totals['identical_pairs']),
        '',
        '**Перекрытие окон называет кандидата, не связь.** V4 не несёт flight '
        'id; одинаковые байты доказывают одинаковую копию, не подлинность DJI '
        'и не факт распыления.',
        '',
        '| Файл пульта (#, SHA-256) | Статус | Кандидат (dji_flight_id) | Перекрытие, мс | Байты совпали |',
        '|---|---|---|---|---|',
    ]
    for row in result['rc_files']:
        lines.append('| #%s `%s` | %s | %s | %s | %s |' % (
            row.get('index'), (row.get('sha256') or '')[:16], row['status'],
            row.get('candidate_cloud_flight_id', '—'),
            row.get('candidate_overlap_ms', '—'),
            row.get('candidate_identical_bytes', '—')))
    lines += ['', '| # | flight_id | Перекрытие, мс | Кадров с равным t | '
              'Равные биты счётчика | Равные позиции | Сопоставление | Байты |',
              '|---|---|---|---|---|---|---|---|']
    for pair in result['pairs']:
        lines.append('| #%s | %s | %s | %d | %d | %d | %s | %s |' % (
            pair['rc_index'], pair['cloud_flight_id'], pair['overlap_ms'],
            pair['frames_with_equal_t'],
            pair['equal_t_frames_with_equal_area_bits'],
            pair['equal_t_frames_with_equal_position'], pair['pairing'],
            pair['identical_bytes']))
    lines.append('')
    return '\n'.join(lines)


# ─── Снимок в изолированную папку ────────────────────────────────────────────

def verify_copy(source_path, target_path):
    """(sha источника, sha копии, совпали ли). Оба файла только читаются."""
    source_sha = sha256_file(source_path)
    target_sha = sha256_file(target_path)
    return source_sha, target_sha, source_sha == target_sha


def copy_with_hash(source_path, target_path, chunk=STREAM_CHUNK):
    """Копия НОВЫМ файлом с хешем прочитанного потока."""
    digest = hashlib.sha256()
    with open(source_path, 'rb') as src, open(target_path, 'xb') as dst:
        while True:
            block = src.read(chunk)
            if not block:
                break
            digest.update(block)
            dst.write(block)
    return digest.hexdigest()


def _stat_signature(stat):
    return {'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns,
            'ctime_ns': stat.st_ctime_ns, 'ino': stat.st_ino,
            'dev': stat.st_dev}


SNAPSHOT_STATUS_ORDER = ('VERIFY_FAILED', UNSTABLE, INCONCLUSIVE, 'PARTIAL',
                         'OK')


def snapshot(source_dir, target_dir, device_label, include_flagged=False,
             include_unscanned=False, deep_max_bytes=DEFAULT_DEEP_MAX_BYTES,
             now=None):
    """Копия папки в пустую целевую папку с проверкой каждой копии.

    Источник только читается. Для каждого файла -- та же проверка на
    маркеры, что в описи (`probe_file`). По умолчанию копируются только файлы
    со статусом `NO_KNOWN_MARKER_IN_SCANNED_BYTES`; `MARKER_FOUND` --
    только с `include_flagged`, `PARTIAL`/`NOT_SCANNED` -- только с
    `include_unscanned`; пропущенные перечисляются в `skipped` с причиной,
    и снимок тогда `PARTIAL`.

    Две разные проверки, записанные раздельно:
    - `verified` -- байты копии равны прочитанному потоку (копия
      перечитана и её хеш совпал);
    - `stability` -- источник за время наблюдения не менялся: размер, время
      изменения, идентификаторы и ПОВТОРНЫЙ хеш источника после копирования
      равны тому, что было прочитано (`STABLE`); иначе `UNSTABLE`. Файл,
      изменённый меньше `RECENTLY_MODIFIED_S` назад, и SQLite с WAL/журналом
      рядом -- `INCONCLUSIVE`: завершённость не доказана.
    Ни одна из них не доказывает, что файл закончен и больше не будет
    дописан. Статус снимка -- худший из статусов файлов.
    """
    if not os.path.isdir(source_dir):
        raise ProbeError('source directory not found: %s' % source_dir)
    guard_output_dir(target_dir, source_dirs=[source_dir])
    os.makedirs(target_dir, exist_ok=True)
    now = time.time() if now is None else now
    manifest = {
        'probe_version': PROBE_VERSION,
        'origin': ORIGIN,
        'origin_basis': ORIGIN_BASIS,
        'device_label': device_label,
        'captured_at_utc': utc_iso(now),
        'source_dir': os.path.abspath(source_dir),
        'target_dir': os.path.abspath(target_dir),
        'files': [],
        'skipped': [],
        'totals': {'copied': 0, 'verified': 0, 'verify_failed': 0,
                   'stable': 0, 'unstable': 0, 'inconclusive': 0,
                   'skipped': 0, 'bytes': 0},
    }
    conditions = set()
    for full in iter_files(source_dir):
        rel = os.path.relpath(full, source_dir).replace('\\', '/')
        probed = probe_file(full, deep_max_bytes, depth=1, now=now)
        apply_name_policy(probed, rel)
        scan = probed['secret_scan']
        flags = set(probed['flags'])
        if scan['status'] == SCAN_MARKER_FOUND and not include_flagged \
                or scan['status'] in (SCAN_PARTIAL, SCAN_NOT_SCANNED) \
                and not include_unscanned:
            manifest['skipped'].append({
                'relpath': probed['relpath'], 'sha256': probed['sha256'],
                'reason': scan['status'],
                'scan_reasons': scan['reasons']})
            manifest['totals']['skipped'] += 1
            conditions.add('PARTIAL')
            continue
        before = _stat_signature(os.stat(full))
        target = os.path.join(target_dir, *rel.split('/'))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        stream_sha = copy_with_hash(full, target)
        os.utime(target, (before['mtime_ns'] / 1e9, before['mtime_ns'] / 1e9))
        copied_sha = sha256_file(target)
        verified = copied_sha == stream_sha
        after_stat = os.stat(full)
        after = _stat_signature(after_stat)
        source_sha_after = sha256_file(full)
        rehash_matches = source_sha_after == stream_sha
        changed = before != after or not rehash_matches
        if changed:
            stability = UNSTABLE
            flags.add('SOURCE_CHANGED_DURING_SNAPSHOT')
        elif ('RECENTLY_MODIFIED' in flags
              or max(0.0, now - after_stat.st_mtime) < RECENTLY_MODIFIED_S
              or 'SQLITE_WAL_OR_JOURNAL_PRESENT' in flags):
            stability = INCONCLUSIVE
        else:
            stability = STABLE
        manifest['files'].append({
            'relpath': probed['relpath'],
            'size_bytes': before['size'],
            'mtime_utc': utc_iso(before['mtime_ns'] / 1e9),
            'sha256': stream_sha,
            'copy_sha256': copied_sha,
            'verified': verified,
            'source_sha256_after_copy': source_sha_after,
            'source_rehash_matches': rehash_matches,
            'source_stat_before': before,
            'source_stat_after': after,
            'stability': stability,
            'secret_scan_status': scan['status'],
            'kind': probed['kind'],
            'flags': sorted(flags),
        })
        manifest['totals']['copied'] += 1
        manifest['totals']['bytes'] += before['size']
        manifest['totals']['verified' if verified else 'verify_failed'] += 1
        manifest['totals'][stability.lower()] += 1
        if not verified:
            conditions.add('VERIFY_FAILED')
        if stability != STABLE:
            conditions.add(stability)
    manifest['conditions'] = sorted(conditions)
    manifest['status'] = next((s for s in SNAPSHOT_STATUS_ORDER
                               if s in conditions), 'OK')
    with open_new(os.path.join(target_dir, 'manifest.json')) as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=1,
                  sort_keys=True)
    return manifest


# ─── CLI ─────────────────────────────────────────────────────────────────────

def _write_json_new(path, document):
    with open_new(path) as handle:
        json.dump(document, handle, ensure_ascii=False, indent=1,
                  sort_keys=True, default=str)


INVENTORY_FILES = ('inventory.json', 'inventory.md',
                   'inventory_transfer.json', 'inventory_transfer.md')


def cmd_inventory(args):
    if not os.path.isdir(args.source):
        say('ERROR: source directory not found')
        return EXIT_NO_INPUT
    guard_output_dir(args.out, source_dirs=[args.source], must_be_empty=False)
    for name in INVENTORY_FILES:
        guard_output_file(os.path.join(args.out, name),
                          source_dirs=[args.source])
    report = inventory(args.source, args.device_label,
                       deep_max_bytes=args.deep_max_bytes, depth=args.depth)
    os.makedirs(args.out, exist_ok=True)
    transfer = transfer_view(report)
    _write_json_new(os.path.join(args.out, 'inventory.json'), report)
    with open_new(os.path.join(args.out, 'inventory.md')) as handle:
        handle.write(render_markdown(report))
    _write_json_new(os.path.join(args.out, 'inventory_transfer.json'), transfer)
    with open_new(os.path.join(args.out, 'inventory_transfer.md')) as handle:
        handle.write(render_markdown(transfer, transfer=True))
    totals = report['totals']
    say('%s origin=%s(%s) device=%s status=%s' % (
        PROBE_VERSION, ORIGIN, ORIGIN_BASIS, args.device_label,
        report['status']))
    say('files=%d bytes=%d kinds=%s' % (
        totals['files'], totals['bytes'],
        ','.join('%s:%d' % kv for kv in totals['by_kind'].items()) or '-'))
    say('v4_like=%d route_like=%d secret_flagged=%d partially_scanned=%d '
        'analysis_errors=%d' % (
            totals['v4_like'], totals['route_like'], totals['flagged_secret'],
            totals['partially_scanned'], totals['analysis_errors']))
    say('local inventory (names, restricted): %s'
        % os.path.join(args.out, 'inventory.json'))
    say('transfer report (no names): %s'
        % os.path.join(args.out, 'inventory_transfer.json'))
    if totals['flagged_secret']:
        say('WARNING: %d file(s) carry a secret marker -- do not send them, '
            'do not attach them to a report' % totals['flagged_secret'])
        return EXIT_SECRET_FOUND
    if report['status'] == 'PARTIAL':
        say('NOTE: %d file(s) scanned partially or not at all, %d analysis '
            'error(s) -- the inventory is PARTIAL' % (
                totals['partially_scanned'], totals['analysis_errors']))
        return EXIT_PARTIAL
    if not totals['files']:
        return EXIT_NOTHING
    return EXIT_OK


def cmd_probe(args):
    if not os.path.isfile(args.file):
        say('ERROR: file not found')
        return EXIT_NO_INPUT
    if args.json:
        guard_output_file(args.json, inputs=[args.file])
    entry = probe_file(args.file, deep_max_bytes=args.deep_max_bytes,
                       depth=args.depth)
    apply_name_policy(entry, os.path.basename(args.file))
    entry['index'] = 1
    entry['probe_version'] = PROBE_VERSION
    entry['origin'] = ORIGIN
    entry['origin_basis'] = ORIGIN_BASIS
    if args.json:
        _write_json_new(args.json, entry)
        say('written: %s' % args.json)
    say('%s kind=%s size=%d sha256=%s scan=%s flags=%s' % (
        entry['relpath'], entry['kind'], entry['size_bytes'],
        entry['sha256'], entry['secret_scan']['status'],
        ','.join(entry['flags']) or '-'))
    shape = entry['details'].get('v4_shape')
    if shape:
        say('v4_shape=%s frames=%s' % (shape['status'],
                                        shape.get('frame_count')))
    shape = entry['details'].get('route_shape')
    if shape:
        say('route_shape=%s records=%s' % (shape['status'],
                                            shape.get('records')))
    if entry['secret_scan']['status'] == SCAN_MARKER_FOUND:
        say('WARNING: secret marker present -- do not send this file')
        return EXIT_SECRET_FOUND
    if entry['secret_scan']['status'] in (SCAN_PARTIAL, SCAN_NOT_SCANNED):
        say('NOTE: secret scan is %s (%s)' % (
            entry['secret_scan']['status'],
            ','.join(entry['secret_scan']['reasons']) or '-'))
        return EXIT_PARTIAL
    return EXIT_OK


def cmd_compare_v4(args):
    for path in (args.a, args.b):
        if not os.path.isfile(path):
            say('ERROR: file not found')
            return EXIT_NO_INPUT
    if args.json:
        guard_output_file(args.json, inputs=[args.a, args.b])
    result = compare_v4(args.a, args.b)
    if args.json:
        _write_json_new(args.json, result)
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
        say('pairing=%s duplicate_t a=%d b=%d unpaired_at_common_t=%d' % (
            comparison['pairing'], comparison['duplicate_t_frames_a'],
            comparison['duplicate_t_frames_b'],
            comparison['unpaired_frames_at_common_t']))
        say('only_in_a=%s only_in_b=%s' % (
            ','.join(comparison['keys_present_only_in_a']) or '-',
            ','.join(comparison['keys_present_only_in_b']) or '-'))
    say('identity: %s' % result['identity_proof'])
    return EXIT_OK


def _snapshot_exit(status):
    return {'VERIFY_FAILED': EXIT_VERIFY_FAILED, UNSTABLE: EXIT_UNSTABLE,
            INCONCLUSIVE: EXIT_UNSTABLE, 'PARTIAL': EXIT_PARTIAL}.get(
                status, EXIT_OK)


def cmd_snapshot(args):
    if not os.path.isdir(args.source):
        say('ERROR: source directory not found')
        return EXIT_NO_INPUT
    manifest = snapshot(args.source, args.target, args.device_label,
                        include_flagged=args.include_flagged,
                        include_unscanned=args.include_unscanned,
                        deep_max_bytes=args.deep_max_bytes)
    totals = manifest['totals']
    say('%s origin=%s(%s) device=%s status=%s conditions=%s' % (
        PROBE_VERSION, ORIGIN, ORIGIN_BASIS, args.device_label,
        manifest['status'], ','.join(manifest['conditions']) or '-'))
    say('copied=%d verified=%d verify_failed=%d stable=%d unstable=%d '
        'inconclusive=%d skipped=%d bytes=%d' % (
            totals['copied'], totals['verified'], totals['verify_failed'],
            totals['stable'], totals['unstable'], totals['inconclusive'],
            totals['skipped'], totals['bytes']))
    say('manifest: %s' % os.path.join(args.target, 'manifest.json'))
    if totals['skipped']:
        reasons = {}
        for item in manifest['skipped']:
            reasons[item['reason']] = reasons.get(item['reason'], 0) + 1
        say('NOTE: %d file(s) NOT copied: %s (--include-flagged copies '
            'MARKER_FOUND, --include-unscanned copies PARTIAL/NOT_SCANNED, '
            'into this isolated folder only)' % (
                totals['skipped'],
                ','.join('%s:%d' % kv for kv in sorted(reasons.items()))))
    if totals['unstable']:
        say('WARNING: %d source file(s) changed during the snapshot -- the '
            'copies are not a finished object; wait and repeat into a NEW '
            'folder' % totals['unstable'])
    if totals['inconclusive']:
        say('NOTE: %d file(s) modified less than %ds ago or with a WAL/journal '
            'beside them -- completeness not proven' % (
                totals['inconclusive'], RECENTLY_MODIFIED_S))
    return _snapshot_exit(manifest['status'])


def cmd_extract_dump(args):
    if not os.path.isfile(args.dump):
        say('ERROR: dump not found')
        return EXIT_NO_INPUT
    if args.list:
        rows = dump_items(read_dump(args.dump))
        say('position flight_id source_type size_bytes sha256 status')
        for row in rows:
            say('%d %s %s %s %s %s' % (
                row['position'], row['flight_id'], row['source_type'],
                row.get('size_bytes'), row.get('sha256'), row['status']))
        say('items=%d' % len(rows))
        return EXIT_OK if rows else EXIT_NOTHING
    manifest = extract_from_dump(args.dump, args.out, args.source_type,
                                 args.flight_id)
    totals = manifest['totals']
    say('%s origin=%s status=%s dry_run=%s nothing_was_queued=%s' % (
        PROBE_VERSION, DUMP_ORIGIN, manifest['status'],
        manifest['dump_dry_run'], manifest['dump_nothing_was_queued']))
    say('matched=%d written=%d verified=%d refused=%d' % (
        totals['matched'], totals['written'], totals['verified'],
        totals['refused']))
    for item in manifest['items']:
        say('flight_id=%s type=%s status=%s sha256=%s written=%s' % (
            item.get('flight_id'), item.get('source_type'), item['status'],
            item.get('sha256'), item.get('written')))
    say('manifest: %s' % os.path.join(args.out, 'extract_manifest.json'))
    if any(i['status'] == 'MARKER_FOUND' for i in manifest['items']):
        say('WARNING: a body carries a secret marker and was NOT written')
        return EXIT_SECRET_FOUND
    if any(i['status'] in ('HASH_MISMATCH', 'SIZE_MISMATCH')
           or i.get('verified') is False for i in manifest['items']):
        return EXIT_VERIFY_FAILED
    if not totals['matched']:
        return EXIT_NOTHING
    if manifest['status'] != 'OK':
        return EXIT_PARTIAL
    return EXIT_OK


def cmd_match(args):
    for path in (args.inventory, args.dump):
        if not os.path.isfile(path):
            say('ERROR: file not found')
            return EXIT_NO_INPUT
    guard_output_dir(args.out, inputs=[args.inventory, args.dump])
    result = match_inventory_with_dump(args.inventory, args.dump)
    os.makedirs(args.out, exist_ok=True)
    _write_json_new(os.path.join(args.out, 'match.json'), result)
    with open_new(os.path.join(args.out, 'match.md')) as handle:
        handle.write(render_match_markdown(result))
    totals = result['totals']
    say('cloud_bodies=%d usable=%d rc_v4_like=%d compared=%d pairs=%d '
        'identical_pairs=%d' % (
            totals['cloud_bodies'], totals['cloud_bodies_usable'],
            totals['rc_v4_like'], totals['rc_compared'], totals['pairs'],
            totals['identical_pairs']))
    for row in result['rc_files']:
        say('rc #%s sha256=%s status=%s candidate=%s overlap_ms=%s '
            'identical=%s' % (
                row.get('index'), (row.get('sha256') or '')[:16],
                row['status'], row.get('candidate_cloud_flight_id'),
                row.get('candidate_overlap_ms'),
                row.get('candidate_identical_bytes')))
    say('written: %s' % os.path.join(args.out, 'match.json'))
    say('identity: %s' % result['identity_proof'])
    if not totals['rc_v4_like'] or not totals['cloud_bodies_usable']:
        return EXIT_NOTHING
    if totals['rc_compared'] != totals['rc_v4_like']:
        return EXIT_PARTIAL
    return EXIT_OK


def build_parser():
    parser = argparse.ArgumentParser(
        prog='dji_rc_probe',
        description='Read-only inspection of files copied from a DJI remote '
                    'controller (origin RC, declared by the operator). Writes '
                    'nothing into the source and never overwrites a file.')
    sub = parser.add_subparsers(dest='command')
    sub.required = True

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--deep-max-bytes', type=int,
                        default=DEFAULT_DEEP_MAX_BYTES,
                        help='files above this size get hash, magic and a '
                             'streaming secret scan only')
    common.add_argument('--depth', type=int, default=2,
                        help='protobuf census nesting depth (default 2)')

    p_inv = sub.add_parser('inventory', parents=[common],
                           help='describe every file under a directory')
    p_inv.add_argument('source', help='directory with files copied from the RC')
    p_inv.add_argument('--out', required=True,
                       help='directory for inventory*.json/.md (outside the '
                            'source; the four files must not exist yet)')
    p_inv.add_argument('--device-label', required=True,
                       help='inventory label of the remote controller')
    p_inv.set_defaults(func=cmd_inventory)

    p_probe = sub.add_parser('probe', parents=[common],
                             help='describe one file')
    p_probe.add_argument('file')
    p_probe.add_argument('--json', help='write the full description to this '
                                        'NEW file (outside the input folder)')
    p_probe.set_defaults(func=cmd_probe)

    p_cmp = sub.add_parser('compare-v4',
                           help='compare two bodies decoded as V4')
    p_cmp.add_argument('a', help='file from the RC')
    p_cmp.add_argument('b', help='V4 body of the cloud (or another RC file)')
    p_cmp.add_argument('--json', help='write the comparison to this NEW file')
    p_cmp.set_defaults(func=cmd_compare_v4)

    p_snap = sub.add_parser('snapshot', parents=[common],
                            help='copy a directory into an empty isolated '
                                 'directory with SHA-256 and stability checks')
    p_snap.add_argument('source')
    p_snap.add_argument('target', help='empty or missing directory')
    p_snap.add_argument('--device-label', required=True)
    p_snap.add_argument('--include-flagged', action='store_true',
                        help='copy MARKER_FOUND files too')
    p_snap.add_argument('--include-unscanned', action='store_true',
                        help='copy PARTIAL / NOT_SCANNED files too')
    p_snap.set_defaults(func=cmd_snapshot)

    p_dump = sub.add_parser('extract-dump',
                            help='list or extract bodies from a collector '
                                 '--sources --dry-run dump with hash checks')
    p_dump.add_argument('dump', help='sources_dry_run_*.json of the collector')
    p_dump.add_argument('--out', help='NEW directory for the extracted bodies')
    p_dump.add_argument('--source-type', default='v4')
    p_dump.add_argument('--flight-id', type=int, default=None)
    p_dump.add_argument('--list', action='store_true',
                        help='only list the items with recomputed hashes')
    p_dump.set_defaults(func=cmd_extract_dump)

    p_match = sub.add_parser('match',
                             help='compare every V4-like file of a LOCAL '
                                  'inventory with every V4 body of a dump')
    p_match.add_argument('inventory', help='inventory.json (local, with names)')
    p_match.add_argument('dump', help='sources_dry_run_*.json of the collector')
    p_match.add_argument('--out', required=True,
                         help='NEW directory for match.json / match.md')
    p_match.set_defaults(func=cmd_match)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == 'extract-dump' and not args.list and not args.out:
        say('ERROR: --out is required unless --list is given')
        return EXIT_PRECONDITION
    try:
        return args.func(args)
    except ProbeError as exc:
        say('ERROR: %s' % exc)
        return EXIT_PRECONDITION


if __name__ == '__main__':
    sys.exit(main())
