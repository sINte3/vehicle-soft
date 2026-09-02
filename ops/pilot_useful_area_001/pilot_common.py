# -*- coding: utf-8 -*-
"""ops/pilot_useful_area_001/pilot_common.py -- общее для приборов пилота.

DRONE-USEFUL-AREA-PILOT-001. Здесь константы площадок, безопасное открытие
базы ТОЛЬКО НА ЧТЕНИЕ и запись машиночитаемых улик. Ни одного запроса к
Vehicle Soft, ни одного импорта приложения: `from app import app` вызывает
`create_app()`, а тот -- `db.create_all()`, и прибор диагностики превратился
бы в писателя (устав, раздел «Классы дефектов»).

Вывод в консоль -- только ASCII: он читается в PowerShell и в журнале NSSM,
где кодовая страница не наша гарантия. Русский текст живёт в докстрингах и в
комментариях, то есть в файле, а не в потоке вывода.

ЧТО ОТСЮДА НИКОГДА НЕ ВЫХОДИТ: координаты, точки маршрута, `points_json`,
`dji_flight_id`, uuid контуров, названия полей, токены, cookies, подписи и
`request_id`. Наружу идут счётчики, площади, статусы и криптографические
отпечатки -- то есть числа, по которым нельзя восстановить ни одну строку.
"""

import hashlib
import json
import os
import sqlite3
import struct
import sys

from datetime import datetime

# ─── Площадки. Один источник правды на весь комплект ────────────────────────
#
# [REASON]: ни один из шести скриптов не имеет права держать эти строки у
# себя. Комплект, в котором адрес площадки написан шесть раз, однажды
# разъедется в одном месте из шести -- и это будет то место, где стоит адрес
# production. Значения взяты из docs/ORG_WINDOWS_SERVER_STAGING_RUNBOOK.md и
# из задания макроэтапа; выдуманного среди них нет.
PRODUCTION_ROOT = r'C:\transport-report'
PRODUCTION_DB = r'C:\transport-report\instance\transport.db'
PRODUCTION_URL = 'http://10.103.25.14:5050'
PRODUCTION_SERVICE = 'TransportReport'

STAGING_ROOT = r'C:\transport-report-staging'
STAGING_DB = r'C:\transport-report-staging\instance\transport.db'
STAGING_URL = 'http://10.103.25.14:5051'

# Проверенный merge-коммит PR #113 и вошедший в него head функциональной ветки.
VERIFIED_MERGE_SHA = 'c3e6a12ab95117710eeea5e05133f5cd548b698e'
VERIFIED_FEATURE_HEAD_SHA = '82b3a2f4ffcb71158f8b0ddfd552ac51f89ca703'

MIGRATION_ID = 'DRONES_USEFUL_AREA_001'
TARGET_DAY = '2026-06-05'

KIT_ID = 'DRONE-USEFUL-AREA-PILOT-001'
KIT_VERSION = '1'

# Таблицы и индексы, которые обязана создать миграция. Список повторяет
# migrate_drones_useful_area_001.py намеренно: прибор проверки не имеет права
# спрашивать у проверяемого, что тот должен был сделать.
EXPECTED_TABLES = ('drone_flight_routes', 'drone_coverage_works')
EXPECTED_INDEXES = ('ix_drone_flight_routes_drone_flight_id',
                    'ix_drone_flight_routes_content_sha256',
                    'ix_drone_coverage_works_work_date',
                    'ix_drone_coverage_works_quality_status',
                    'ix_drone_coverage_works_date_status')

# Статусы качества. Дублировать список нельзя -- он живёт в drone_useful_area.
QUALITY_STATUSES = ('READY_ESTIMATE', 'PARTIAL_DATA', 'DATA_UNAVAILABLE',
                    'CONTOUR_AMBIGUOUS', 'CONTOUR_NOT_MATCHED',
                    'ROUTE_INVALID')
SUMMABLE_STATUSES = ('READY_ESTIMATE',)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NO_DATABASE = 2
EXIT_CHECK_FAILED = 3


class ProbeError(Exception):
    """Прибор не может ответить. Никогда не поднимается ради «странного» числа."""


# ─── Открытие базы ──────────────────────────────────────────────────────────

def connect_readonly(db_path):
    """Открыть СУЩЕСТВУЮЩУЮ базу так, чтобы запись была невозможна.

    Возвращает (соединение, режим): 'uri-ro' или 'query_only'.

    [REASON]: `sqlite3.connect(path)` создаёт пустой файл, когда его нет.
    Прибор, молча заведший новую базу вместо отказа, отчитается о нуле строк
    и будет выглядеть успешным -- ровно тот дефект, из-за которого миграции
    проекта отказываются с кодом 2.

    [REASON]: `file:...?mode=ro` -- первый выбор, но на базе в режиме WAL он
    отказывает, когда рядом нет `-shm`: соединение только на чтение не может
    его создать. Это уже записано в `tools/check_db_lock.py`. Поэтому есть
    отступление: обычное соединение с немедленным `PRAGMA query_only=1`.
    Запрет действует на уровне SQLite -- любая попытка записи возвращает
    SQLITE_READONLY, -- а не на уровне нашей аккуратности. Использованный
    режим попадает в улику: проверка, про которую неизвестно, чем она была
    открыта, доказательством не является.
    """
    if not os.path.exists(db_path):
        raise ProbeError('database not found at %s - refusing to run' % db_path)

    uri = 'file:%s?mode=ro' % _sqlite_uri_path(db_path)
    con = None
    try:
        con = sqlite3.connect(uri, uri=True, timeout=30)
        con.execute('SELECT count(*) FROM sqlite_master').fetchone()
        con.row_factory = sqlite3.Row
        return con, 'uri-ro'
    except sqlite3.Error:
        if con is not None:
            try:
                con.close()
            except sqlite3.Error:
                pass

    con = sqlite3.connect(db_path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA query_only=1')
    return con, 'query_only'


def _sqlite_uri_path(db_path):
    """Путь для URI SQLite. Windows-путь с диском и пробелами -- обычный случай.

    [REASON]: `os.path.abspath` на Linux приклеивает текущий каталог к
    `C:\\transport-report\\...`, потому что диска он не знает. Прибор гоняется в
    тестах на Linux и работает на Windows, поэтому абсолютность определяется
    по виду пути, а не по платформе интерпретатора.
    """
    raw = str(db_path)
    looks_absolute = (len(raw) > 2 and raw[1] == ':' and raw[2] in '\\/'
                      or raw.startswith(('\\\\', '/')))
    path = (raw if looks_absolute else os.path.abspath(raw)).replace('\\', '/')
    if len(path) > 1 and path[1] == ':':
        path = '/' + path
    out = []
    for char in path:
        if char.isalnum() or char in '/_-.~:':
            out.append(char)
        else:
            out.append('%%%02X' % ord(char))
    return ''.join(out)


# ─── Отпечаток drone_flights.area_ha ────────────────────────────────────────

def _value_bytes(value):
    """Точное представление хранимого значения SQLite в байтах.

    [REASON]: сравнение по `str(value)` или по `round()` не различает два
    числа, расходящиеся в последнем бите мантиссы, -- а именно такое
    расхождение и появляется, когда число проходит через пересчёт вместо
    того, чтобы остаться нетронутым. REAL кодируется всеми 64 битами
    IEEE-754, а не десятичной записью.
    """
    if value is None:
        return b'N'
    if isinstance(value, float):
        return b'R' + struct.pack('<d', value)
    if isinstance(value, int):
        return b'I' + repr(value).encode('ascii')
    if isinstance(value, bytes):
        return b'B' + value
    return b'T' + str(value).encode('utf-8')


def area_ha_fingerprint(con):
    """Безопасное доказательство неизменности drone_flights.area_ha.

    Наружу выходят: hex-дайджест, число строк, перепись типов SQLite и сумма
    площадей. Ни одной строки, ни одного `dji_flight_id`, ни одного ника.
    Восстановить по дайджесту исходные значения нельзя -- в этом и смысл.

    В хеш входит `typeof()`, а не только значение: INTEGER 2 и REAL 2.0
    читаются Python-ом по-разному, но перепутать их местами -- это ИЗМЕНЕНИЕ
    хранимого, и проверка обязана его увидеть.
    """
    digest = hashlib.sha256()
    census = {}
    rows = 0
    for row in con.execute('SELECT id, typeof(area_ha) AS kind, area_ha '
                           '  FROM drone_flights ORDER BY id'):
        rows += 1
        kind = row['kind']
        census[kind] = census.get(kind, 0) + 1
        digest.update(repr(row['id']).encode('ascii'))
        digest.update(b'|')
        digest.update(kind.encode('ascii'))
        digest.update(b'|')
        digest.update(_value_bytes(row['area_ha']))
        digest.update(b'\n')

    total = con.execute('SELECT sum(area_ha) FROM drone_flights').fetchone()[0]
    return {
        'rows': rows,
        'sha256': digest.hexdigest(),
        'typeof_census': census,
        'sum_area_ha': round(total, 4) if total is not None else None,
    }


# ─── Улики ──────────────────────────────────────────────────────────────────

def evidence_envelope(kind, payload):
    """Конверт улики. Одинаковый у всех приборов комплекта."""
    return {
        'kit': KIT_ID,
        'kit_version': KIT_VERSION,
        'evidence_kind': kind,
        'generated_utc': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'target_day': TARGET_DAY,
        'verified_sha': VERIFIED_MERGE_SHA,
        'payload': payload,
    }


def write_evidence(path, document):
    """Записать улику в UTF-8 JSON с LF. Каталог создаётся, если его нет."""
    directory = os.path.dirname(os.path.abspath(path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    text = json.dumps(document, ensure_ascii=True, indent=2, sort_keys=True)
    with open(path, 'w', encoding='ascii', newline='\n') as handle:
        handle.write(text)
        handle.write('\n')
    return os.path.abspath(path)


def emit(document, out_path=None):
    """Напечатать улику (ASCII JSON) и, если попросили, положить её в файл."""
    text = json.dumps(document, ensure_ascii=True, indent=2, sort_keys=True)
    sys.stdout.write(text + '\n')
    if out_path:
        write_evidence(out_path, document)
    return document


# ─── Пути. Самая важная гвардия комплекта ───────────────────────────────────

def normalize_path(path):
    """Windows-путь в сравнимый вид: нижний регистр, '/' -> '\\', без хвоста.

    [REASON]: сравнение путей подстрокой -- тот же класс дефекта, что и
    проверка использования переменной подстрокой (`L_initial` совпадает с
    `L_initial_missing`, устав). Здесь он стоит дороже:
    `C:\\transport-report-staging` НАЧИНАЕТСЯ с `C:\\transport-report`, и
    гвардия «не трогать production», написанная через startswith, пропустила
    бы ровно наоборот -- объявила бы площадку продакшеном и, что хуже, при
    обратном сравнении объявила бы продакшен площадкой.
    """
    text = str(path).strip().strip('"').replace('/', '\\')
    while len(text) > 3 and text.endswith('\\'):
        text = text[:-1]
    return text.lower()


def path_segments(path):
    """Путь в виде списка сегментов. Пустые сегменты отбрасываются."""
    return [part for part in normalize_path(path).split('\\') if part]


def path_equals(left, right):
    """Один и тот же путь? Сравнение посегментное, не подстрокой."""
    return path_segments(left) == path_segments(right)


def path_is_within(child, parent):
    """`child` лежит внутри `parent` (или равен ему)? Посегментно.

    `C:\\transport-report-staging` внутри `C:\\transport-report` НЕ лежит.
    """
    child_parts = path_segments(child)
    parent_parts = path_segments(parent)
    if len(child_parts) < len(parent_parts):
        return False
    return child_parts[:len(parent_parts)] == parent_parts


def touches_production(path):
    """Путь ведёт в production Vehicle Soft? Единственное место этого вопроса."""
    return path_is_within(path, PRODUCTION_ROOT)


def is_staging_path(path):
    """Путь ведёт в площадку?"""
    return path_is_within(path, STAGING_ROOT)


def url_is_production(url):
    """Адрес -- боевой? Сравнение по хосту и порту, а не по вхождению строки."""
    return _url_authority(url) == _url_authority(PRODUCTION_URL)


def url_is_staging(url):
    return _url_authority(url) == _url_authority(STAGING_URL)


def _url_authority(url):
    text = str(url or '').strip().rstrip('/').lower()
    if '://' in text:
        text = text.split('://', 1)[1]
    return text.split('/', 1)[0]
