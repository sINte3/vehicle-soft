# -*- coding: utf-8 -*-
"""ops/pilot_useful_area_001/pilot_common.py -- общее для приборов пилота.

DRONE-USEFUL-AREA-PILOT-001. Здесь константы площадок, две РАЗНЫЕ ревизии,
безопасное открытие базы только на чтение, отпечатки, идентичность запуска и
проверка конвертов улик.

Вывод в консоль -- только ASCII: он читается в PowerShell и в журнале NSSM.
Русский текст живёт в докстрингах, то есть в файле, а не в потоке вывода.

ЧТО ОТСЮДА НИКОГДА НЕ ВЫХОДИТ: координаты, точки маршрута, `points_json`,
`dji_flight_id`, uuid контуров, названия полей, токены, cookies, подписи и
`request_id`. Наружу идут счётчики, площади, статусы и криптографические
отпечатки -- числа, по которым нельзя восстановить ни одну строку.

## Две ревизии, а не одна

Первая редакция комплекта знала один `verified_sha` и требовала его от всех
чекаутов. Это невозможно по построению: комплект живёт в коммите, которого на
`c3e6a12` ещё нет, поэтому скрипт, требующий `HEAD == c3e6a12` от репозитория,
в котором сам лежит, требовал собственного отсутствия.

Ревизий две, и у них разные роли:

* **PRODUCT_SHA** -- проверенная ревизия ПРОДУКТА (`c3e6a12`, merge PR #113).
  На ней стоит площадка, из неё материализуются миграция, `migration_utils`,
  инструмент бэкапа и инструмент пересчёта;
* **KIT_SHA** -- ревизия КОМПЛЕКТА. Она не зашита: её нельзя знать до коммита,
  который её создаёт. Она ИЗМЕРЯЕТСЯ у чекаута комплекта и попадает в улику
  измеренной.

Комплект живёт в ОТДЕЛЬНОМ чекауте (`C:\\vehicle-soft-pilot-kit`) и поэтому не
исчезает, когда целевой репозиторий переключают или откатывают.
"""

import binascii
import hashlib
import json
import os
import re
import sqlite3
import struct
import subprocess
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

# Чекаут комплекта -- отдельный и от площадки, и от продакшена.
KIT_ROOT = r'C:\vehicle-soft-pilot-kit'
KIT_SUBDIR = r'ops\pilot_useful_area_001'

# Каталоги запусков. Оба ВНЕ любого чекаута: артефакты пилота не имеют права
# делать рабочее дерево грязным.
SERVER_RUNS_ROOT = r'D:\transport-report-backups\pilot\DRONE-USEFUL-AREA-001\runs'
COLLECTOR_RUNS_ROOT = r'C:\vehicle-soft-pilot-runs'

COLLECTOR_REPO = r'C:\VehicleSoft_DJI_StageB_Pilot'

# Проверенная ревизия ПРОДУКТА: merge-коммит PR #113 и вошедший в него head.
PRODUCT_SHA = 'c3e6a12ab95117710eeea5e05133f5cd548b698e'
PRODUCT_FEATURE_HEAD_SHA = '82b3a2f4ffcb71158f8b0ddfd552ac51f89ca703'

MIGRATION_ID = 'DRONES_USEFUL_AREA_001'
TARGET_DAY = '2026-06-05'

KIT_ID = 'DRONE-USEFUL-AREA-PILOT-001'
KIT_VERSION = '2'

# Эндпоинт smoke-теста и ТОЧНЫЙ набор допустимых статусов.
#
# [REASON]: `GET /` под flask-login отвечает 302 на `/login`, а `GET /login`
# отдаёт саму страницу -- 200 и только 200. Первая редакция считала успехом
# «меньше 500», то есть 404 на несуществующем пути и 401 у неподнявшегося
# приложения объявлялись рабочей площадкой. Проверка, которую проходит
# сломанный сервис, проверкой не является.
SMOKE_PATH = '/login'
SMOKE_ALLOWED_STATUS = (200,)
SMOKE_REDIRECT_STATUS = (301, 302, 303, 307, 308)

# [REASON]: 200 сам по себе не значит «приложение поднялось». Страница
# обслуживания, заглушка обратного прокси и чужой сервер на том же порту
# отвечают двумястами так же охотно. Признак -- класс формы входа из
# templates/login.html: он есть на странице приложения и его нет ни у одной
# generic-страницы. Это разметка нашего же шаблона, не секрет и не данные.
SMOKE_PAGE_MARKER = 'vs-login-form'
SMOKE_PAGE_MARKER_ALT = 'vsLoginField'

# Коды возврата сборщика отчёта. Вердикт -- ЧАСТЬ КОНТРАКТА, а не текст в
# файле: скрипт, печатающий PASS при REJECT, врал ровно тем, кто читает
# только последнюю строку.
EXIT_VERDICT_GO = 0
EXIT_VERDICT_TECHNICAL_GO = 10
EXIT_VERDICT_ADJUST = 11
EXIT_VERDICT_REJECT = 12
VERDICT_EXIT_CODES = {
    'GO': EXIT_VERDICT_GO,
    'TECHNICAL_GO': EXIT_VERDICT_TECHNICAL_GO,
    'ADJUST': EXIT_VERDICT_ADJUST,
    'REJECT': EXIT_VERDICT_REJECT,
}

# Таблицы и индексы, которые обязана создать миграция.
EXPECTED_TABLES = ('drone_flight_routes', 'drone_coverage_works')
EXPECTED_INDEXES = ('ix_drone_flight_routes_drone_flight_id',
                    'ix_drone_flight_routes_content_sha256',
                    'ix_drone_coverage_works_work_date',
                    'ix_drone_coverage_works_quality_status',
                    'ix_drone_coverage_works_date_status')

QUALITY_STATUSES = ('READY_ESTIMATE', 'PARTIAL_DATA', 'DATA_UNAVAILABLE',
                    'CONTOUR_AMBIGUOUS', 'CONTOUR_NOT_MATCHED',
                    'ROUTE_INVALID')
SUMMABLE_STATUSES = ('READY_ESTIMATE',)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NO_DATABASE = 2
EXIT_CHECK_FAILED = 3

RUN_ID_RE = re.compile(r'^\d{8}T\d{6}Z-[0-9a-f]{8}$')
SHA_RE = re.compile(r'^[0-9a-f]{40}$')


class ProbeError(Exception):
    """Прибор не может ответить. Никогда не поднимается ради «странного» числа."""


# ─── Идентичность запуска ────────────────────────────────────────────────────

def new_run_id(now=None, entropy=None):
    """`20260902T113000Z-a1b2c3d4`: сортируется и не повторяется.

    [REASON]: без общего идентификатора запуска каждый шаг искал «самый свежий»
    файл сам. Два прогона одного дня -- и отчёт спокойно смешивал предполёт
    одного с пересчётом другого, а выглядел бы он при этом безупречно.
    """
    stamp = (now or datetime.utcnow()).strftime('%Y%m%dT%H%M%SZ')
    tail = entropy if entropy is not None else binascii.hexlify(
        os.urandom(4)).decode('ascii')
    return '%s-%s' % (stamp, tail)


def is_run_id(value):
    return bool(value) and bool(RUN_ID_RE.match(str(value)))


def run_directory(runs_root, run_id):
    if not is_run_id(run_id):
        raise ProbeError('not a run id: %r' % (run_id,))
    return os.path.join(str(runs_root), str(run_id))


# ─── Числа, которым можно верить ─────────────────────────────────────────────

def finite_number(value):
    """Конечное число или None.

    [REASON]: `float('nan')` и `float('inf')` проходят разбор командной строки
    молча, а дальше ведут себя как угодно: `nan > threshold` ложно ВСЕГДА,
    поэтому порог с NaN пропускает любую долю, а `inf` секунд выглядит как
    измеренное время. Оба обязаны быть отвергнуты там, где число входит в
    решение.
    """
    if isinstance(value, bool) or value is None:
        return None
    if not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number:            # NaN
        return None
    if number in (float('inf'), float('-inf')):
        return None
    return number


def owner_share_threshold(value):
    """Порог доли работ без числа: конечное число в [0, 1] или отказ."""
    number = finite_number(value)
    if number is None:
        raise ProbeError('the owner share threshold must be a finite number, '
                         'got %r' % (value,))
    if not (0.0 <= number <= 1.0):
        raise ProbeError('the owner share threshold is a SHARE and must lie '
                         'in [0, 1], got %r' % (value,))
    return number


def owner_delta_percent(value):
    """Допустимое отклонение от площади DJI: конечное число >= 0 или отказ."""
    number = finite_number(value)
    if number is None:
        raise ProbeError('the owner DJI delta must be a finite number, '
                         'got %r' % (value,))
    if number < 0.0:
        raise ProbeError('the owner DJI delta is a magnitude and cannot be '
                         'negative, got %r' % (value,))
    return number


# ─── Git: ревизии и blob-и ───────────────────────────────────────────────────

def git(repo, *arguments):
    """git в указанном репозитории. Ненулевой код -- отказ, а не пустая строка."""
    result = subprocess.run(('git', '-C', str(repo)) + tuple(arguments),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        raise ProbeError('git %s failed in %s: %s'
                         % (' '.join(arguments), repo,
                            result.stderr.decode('utf-8', 'replace').strip()))
    return result.stdout


def git_text(repo, *arguments):
    return git(repo, *arguments).decode('utf-8', 'replace').strip()


def head_sha(repo):
    return git_text(repo, 'rev-parse', 'HEAD')


def worktree_is_clean(repo):
    return git_text(repo, 'status', '--porcelain') == ''


def commit_exists(repo, rev):
    try:
        git(repo, 'cat-file', '-e', '%s^{commit}' % rev)
        return True
    except ProbeError:
        return False


def is_ancestor(repo, candidate, descendant):
    """`candidate` -- предок `descendant` (или равен ему)?"""
    result = subprocess.run(
        ('git', '-C', str(repo), 'merge-base', '--is-ancestor',
         str(candidate), str(descendant)),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise ProbeError('git merge-base --is-ancestor failed in %s: %s'
                     % (repo, result.stderr.decode('utf-8', 'replace').strip()))


def fast_forward_state(repo, head, target):
    """Можно ли перевести `head` в `target` одним fast-forward.

    Возвращает одно из: 'at-target', 'behind', 'ahead', 'diverged'.

    [REASON]: предполётная проверка НЕ ИМЕЕТ ПРАВА требовать, чтобы площадка
    уже стояла на проверенной ревизии: перевести её туда -- работа второго
    шага, и требование первого делало второй недостижимым. Правильный вопрос
    первого шага другой: возможно ли это обновление вообще и будет ли оно
    fast-forward. «Опережает» и «разошлась» -- отказ до любых изменений: в
    первом случае на площадке есть неизвестные коммиты, во втором она уже не
    на этой линии истории.
    """
    if head == target:
        return 'at-target'
    if is_ancestor(repo, head, target):
        return 'behind'
    if is_ancestor(repo, target, head):
        return 'ahead'
    return 'diverged'


def blob_sha_of_bytes(payload):
    """Git-хеш содержимого: sha1 от 'blob <len>\\0' и байтов.

    Считается ЛОКАЛЬНО. Это и есть разница между «файл совпал со своей копией»
    и «файл совпал с тем, что записано в истории»: копия доказывает только
    аккуратность копирования.
    """
    digest = hashlib.sha1()
    digest.update(b'blob %d\x00' % len(payload))
    digest.update(payload)
    return digest.hexdigest()


def blob_sha_at(repo, rev, path):
    """Хеш blob-а по ревизии и пути, как его знает сам git."""
    return git_text(repo, 'rev-parse', '%s:%s' % (rev, path))


def read_blob(repo, rev, path):
    """Содержимое blob-а БАЙТАМИ. Ни одна перекодировка по дороге не случается."""
    return git(repo, 'cat-file', 'blob', '%s:%s' % (rev, path))


def materialize_blob(repo, rev, path, destination, expected_blob=None):
    """Достать файл ИЗ ИСТОРИИ и положить на диск, сверив хеш.

    Возвращает фактический blob-хеш. Расхождение с `expected_blob` -- отказ:
    исполняемый файл, про который нельзя сказать, из какой он ревизии, не
    исполняется вовсе.
    """
    payload = read_blob(repo, rev, path)
    actual = blob_sha_of_bytes(payload)
    if expected_blob and actual != expected_blob:
        raise ProbeError('%s at %s has blob %s, the manifest says %s'
                         % (path, rev, actual, expected_blob))
    declared = blob_sha_at(repo, rev, path)
    if declared != actual:
        raise ProbeError('%s at %s: git names blob %s, the bytes hash to %s'
                         % (path, rev, declared, actual))
    directory = os.path.dirname(os.path.abspath(destination))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with open(destination, 'wb') as handle:
        handle.write(payload)
    return actual


def file_blob_sha(path):
    """Хеш СЫРЫХ байтов файла. Для того, что мы записали сами.

    Годится ровно для материализованных файлов: их пишет
    `materialize_blob` побайтово, без единого фильтра, поэтому сырые байты и
    есть байты коммита.
    """
    with open(path, 'rb') as handle:
        return blob_sha_of_bytes(handle.read())


def worktree_blob_sha(repo, path):
    """Хеш файла рабочего дерева ГЛАЗАМИ GIT.

    [REASON]: на Windows рабочая копия лежит с CRLF -- `core.autocrlf`
    разворачивает переводы строк при checkout, а блобы в git хранятся с LF
    (устав, раздел «Переводы строк и BOM»). Сырой хеш такого файла НИКОГДА не
    совпадёт с блобом, и проверка «исполняемое взято из ревизии» падала бы на
    каждом файле на той самой платформе, ради которой она написана. Это и
    поймала Windows-задача CI: восемь файлов из восьми.

    `git hash-object` применяет те же фильтры, что применил бы коммит, и
    отвечает на нужный вопрос: соответствует ли файл на диске блобу ревизии,
    как это понимает сам git.
    """
    return git_text(repo, 'hash-object', '--', path)


PRODUCT_BLOBS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  'PRODUCT_BLOBS.json')


def load_product_blobs(path=None):
    with open(path or PRODUCT_BLOBS_FILE, encoding='ascii') as handle:
        document = json.load(handle)
    if document.get('product_sha') != PRODUCT_SHA:
        raise ProbeError('the blob manifest names product %r, the kit expects %r'
                         % (document.get('product_sha'), PRODUCT_SHA))
    return document


# ─── Открытие базы ──────────────────────────────────────────────────────────

def connect_readonly(db_path):
    """Открыть СУЩЕСТВУЮЩУЮ базу так, чтобы запись была невозможна.

    Возвращает (соединение, режим): 'uri-ro' или 'query_only'.

    [REASON]: `sqlite3.connect(path)` создаёт пустой файл, когда его нет.
    Прибор, молча заведший новую базу вместо отказа, отчитается о нуле строк
    и будет выглядеть успешным.

    [REASON]: `file:...?mode=ro` -- первый выбор, но на базе в режиме WAL он
    отказывает, когда рядом нет `-shm`. Это уже записано в
    `tools/check_db_lock.py`. Поэтому есть отступление: обычное соединение с
    немедленным `PRAGMA query_only=1`. Запрет действует на уровне SQLite --
    любая попытка записи возвращает SQLITE_READONLY.
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
    out = []
    for char in path:
        if char.isalnum() or char in '/_-.~:':
            out.append(char)
        else:
            out.append('%%%02X' % ord(char))
    return ''.join(out)


# ─── Отпечатки ──────────────────────────────────────────────────────────────

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


def table_columns(con, table):
    return [row[1] for row in con.execute('PRAGMA table_info(%s)' % table)]


def coverage_fingerprint(con, exclude=()):
    """Отпечаток ВСЕХ строк и ВСЕХ колонок `drone_coverage_works`.

    [REASON]: «сухой прогон ничего не записал» доказывалось числом строк.
    Число строк не меняется, когда строку ПЕРЕПИСАЛИ: площадь, статус, версия
    алгоритма и отпечаток входа могли смениться целиком, а счётчик остался
    прежним -- и проверка сказала бы «не писал». Здесь в хеш идёт каждая
    колонка каждой строки, в порядке идентичности работы, со значениями,
    закодированными побитово.

    `exclude` -- имена колонок, которые исключаются ЯВНО и называются в улике.
    Для сухого прогона исключать нечего: он не пишет вовсе, поэтому меняться
    не должно НИЧЕГО, включая `computed_at`.
    """
    if not _table_exists(con, 'drone_coverage_works'):
        return {'table_present': False}

    columns = [name for name in table_columns(con, 'drone_coverage_works')
               if name not in tuple(exclude)]
    digest = hashlib.sha256()
    rows = 0
    quoted = ', '.join(columns)
    for row in con.execute(
            'SELECT %s FROM drone_coverage_works '
            ' ORDER BY unit_key, work_date, group_index' % quoted):
        rows += 1
        for name in columns:
            digest.update(name.encode('ascii'))
            digest.update(b'=')
            digest.update(_value_bytes(row[name]))
            digest.update(b'\x1f')
        digest.update(b'\n')
    return {
        'table_present': True,
        'rows': rows,
        'columns': len(columns),
        'excluded_columns': sorted(exclude),
        'sha256': digest.hexdigest(),
    }


def _table_exists(con, name):
    row = con.execute("SELECT name FROM sqlite_master "
                      " WHERE type='table' AND name=?", (name,)).fetchone()
    return row is not None


# ─── Улики ──────────────────────────────────────────────────────────────────

def evidence_envelope(kind, payload, run_id, kit_sha, product_sha=None):
    """Конверт улики. Одинаковый у всех приборов комплекта.

    `kit_sha` -- ИЗМЕРЕННЫЙ, а не зашитый. Улика, печатающая константу,
    доказывает лишь то, что константа записана в файле.
    """
    if not is_run_id(run_id):
        raise ProbeError('an evidence envelope needs a run id, got %r'
                         % (run_id,))
    if not (kit_sha and SHA_RE.match(str(kit_sha))):
        raise ProbeError('an evidence envelope needs a measured kit sha, '
                         'got %r' % (kit_sha,))
    return {
        'kit': KIT_ID,
        'kit_version': KIT_VERSION,
        'evidence_kind': kind,
        'run_id': run_id,
        'kit_sha': kit_sha,
        'product_sha': product_sha or PRODUCT_SHA,
        'generated_utc': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'target_day': TARGET_DAY,
        'payload': payload,
    }


ENVELOPE_FIELDS = ('kit', 'kit_version', 'evidence_kind', 'run_id', 'kit_sha',
                   'product_sha', 'generated_utc', 'target_day', 'payload')


def validate_envelope(document, expected_kind, run_id=None, kit_sha=None,
                      product_sha=None, target_day=None):
    """Список несоответствий конверта. Пустой список -- конверт свой.

    [REASON]: улики приезжают с трёх машин и пишутся четырьмя скриптами.
    Конверт, который никто не проверяет, однажды приедет от другого запуска,
    другого дня или другой ревизии -- и отчёт сложит их вместе, ни на что не
    пожаловавшись. Проверяется КАЖДОЕ поле, потому что расхождение любого из
    них означает, что улики не про один и тот же прогон.
    """
    problems = []
    if not isinstance(document, dict):
        return ['EVIDENCE_IS_NOT_AN_OBJECT']

    for field in ENVELOPE_FIELDS:
        if field not in document:
            problems.append('MISSING_FIELD:%s' % field)

    if document.get('kit') != KIT_ID:
        problems.append('WRONG_KIT')
    if str(document.get('kit_version')) != KIT_VERSION:
        problems.append('WRONG_KIT_VERSION')
    if expected_kind is not None and document.get('evidence_kind') != expected_kind:
        problems.append('WRONG_EVIDENCE_KIND')
    if not is_run_id(document.get('run_id')):
        problems.append('MALFORMED_RUN_ID')
    elif run_id is not None and document.get('run_id') != run_id:
        problems.append('RUN_ID_MISMATCH')
    if not (document.get('kit_sha')
            and SHA_RE.match(str(document.get('kit_sha')))):
        problems.append('MALFORMED_KIT_SHA')
    elif kit_sha is not None and document.get('kit_sha') != kit_sha:
        problems.append('KIT_SHA_MISMATCH')
    if document.get('product_sha') != (product_sha or PRODUCT_SHA):
        problems.append('PRODUCT_SHA_MISMATCH')
    if document.get('target_day') != (target_day or TARGET_DAY):
        problems.append('TARGET_DAY_MISMATCH')
    if parse_stamp(document.get('generated_utc')) is None:
        problems.append('MALFORMED_TIMESTAMP')
    return problems


def parse_stamp(value):
    try:
        return datetime.strptime(str(value), '%Y-%m-%dT%H:%M:%SZ')
    except (TypeError, ValueError):
        return None


def check_time_order(pairs):
    """[(имя, конверт)] в ожидаемом порядке -> список нарушений порядка.

    Улики одного запуска не могут быть выписаны в обратном порядке: пересчёт
    не бывает раньше миграции. Равные метки допустимы -- шаги укладываются в
    одну секунду.
    """
    problems = []
    previous_name = None
    previous_stamp = None
    for name, document in pairs:
        stamp = parse_stamp((document or {}).get('generated_utc'))
        if stamp is None:
            problems.append('NO_TIMESTAMP:%s' % name)
            continue
        if previous_stamp is not None and stamp < previous_stamp:
            problems.append('OUT_OF_ORDER:%s_BEFORE_%s'
                            % (name, previous_name))
        previous_name = name
        previous_stamp = stamp
    return problems


def write_text_utf8(path, text):
    """UTF-8 БЕЗ BOM и с LF. Одно место записи на весь комплект.

    [REASON]: PowerShell 5.1 не знает `utf8NoBOM`, а его `Set-Content
    -Encoding UTF8` ставит BOM. Файл с BOM ломает `json.load` в приборах и
    `Get-Content -Raw | ConvertFrom-Json` в самом PowerShell. Поэтому все
    улики пишет Python, а PowerShell их только читает.
    """
    directory = os.path.dirname(os.path.abspath(path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with open(path, 'w', encoding='utf-8', newline='\n') as handle:
        handle.write(text)
    return os.path.abspath(path)


def write_evidence(path, document):
    """Записать улику ASCII-JSON без BOM."""
    return write_text_utf8(path, json.dumps(document, ensure_ascii=True,
                                            indent=2, sort_keys=True) + '\n')


def read_evidence(path):
    """Прочитать улику. BOM допускается на входе и не допускается на выходе."""
    if not os.path.exists(path):
        raise ProbeError('evidence file not found: %s' % path)
    with open(path, encoding='utf-8-sig') as handle:
        try:
            return json.load(handle)
        except ValueError as exc:
            raise ProbeError('%s is not readable JSON: %s' % (path, exc))


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
    бы ровно наоборот.
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
    if not parent_parts or len(child_parts) < len(parent_parts):
        return False
    return child_parts[:len(parent_parts)] == parent_parts


def touches_production(path):
    """Путь ведёт в production Vehicle Soft? Единственное место этого вопроса."""
    return path_is_within(path, PRODUCTION_ROOT)


def is_staging_path(path):
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
    if '@' in text:
        text = text.split('@', 1)[1]
    return text.split('/', 1)[0]


def redirect_stays_in_staging(location, base=None):
    """Куда бы ни вёл redirect, он обязан остаться внутри площадки.

    Относительный `Location` остаётся внутри по построению; абсолютный
    сверяется по authority. Redirect на боевой адрес или на чужой хост --
    это не «страница переехала», это не та площадка.
    """
    text = str(location or '').strip()
    if not text:
        return False
    if text.startswith('/'):
        return True
    if '://' not in text:
        return True
    return _url_authority(text) == _url_authority(base or STAGING_URL)
