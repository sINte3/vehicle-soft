# -*- coding: utf-8 -*-
"""drone_collector/outbox.py -- файловая очередь на диске для этапа B.

Одна запись -- один JSON-файл. Простая форма выбрана намеренно: очередь
должна пережить остановку процесса, обрыв питания и повторный запуск, а
проверить такое свойство у файлов можно тестом, у чего-либо более хитрого --
уже нет.

ЧТО ГАРАНТИРУЕТСЯ

* **Атомарность.** Файл пишется во временный `.tmp` в том же каталоге и
  переименовывается на место. Переименование внутри одной файловой системы
  атомарно и на Windows, и на POSIX, поэтому наблюдатель видит либо старую
  запись, либо новую целиком, но никогда половину. Если запись сорвалась --
  временный файл удаляется, и `.json` не появляется вовсе.
* **Идемпотентность.** Имя записи -- это её `dedupe_key`, а не счётчик.
  Повторная постановка того же ответа НЕ создаёт вторую запись. Счётчик дал
  бы дубликат при каждом повторном прогоне.
* **Возобновление.** Отправленное переносится в `sent/`, а не удаляется, и
  прогон, прерванный на середине, при следующем запуске видит ровно то, что
  осталось в `pending/`. Оборванная запись оставляет `.tmp`, который очередь
  не считает элементом и умеет подмести.
* **Версия формата.** Каждая запись несёт `envelope_version`. Читатель, не
  знающий версии, обязан отказаться, а не разбирать «как получится».
* **Предел размера.** Запись больше потолка не пишется вовсе. Очередь --
  это буфер прогона, а не хранилище: один разросшийся элемент, записанный
  молча, забивает диск службы и обнаруживается только тогда, когда писать
  становится некуда.

ЧЕГО ЗДЕСЬ НЕТ И БЫТЬ НЕ ДОЛЖНО

Ни одного поля с подписанной ссылкой, cookie, токеном или заголовком
авторизации. Запись проходит проверку `assert_no_secrets` ПЕРЕД записью на
диск: утечка, попавшая в очередь, переживёт процесс и уедет дальше.

СОВМЕСТИМОСТЬ С WINDOWS

Служба работает на Windows, поэтому имя файла строится только из
`[A-Za-z0-9._-]`, всегда начинается с имени вида (`route_`,
`field_geometry_`) и ограничено по длине. Из этого следует и то, что имя
никогда не совпадёт с именем устройства DOS (`CON`, `PRN`, `AUX`, `NUL`,
`COM1`..`LPT9`), которое Windows не даёт создать как файл; двоеточия,
которых не терпит NTFS, вырезаются тем же фильтром. Перемещение сделано
через `os.replace`, а не `os.rename`: второй на Windows падает, когда цель
уже существует.
"""

import hashlib
import json
import os
import re
import tempfile

from datetime import datetime, timezone
from pathlib import Path

# Версия формата записи. Меняется, когда меняется СМЫСЛ полей, а не когда
# добавляется необязательное поле.
ENVELOPE_VERSION = 1

# Виды записей. Маршрут и геометрия поля идут разными записями: у них разные
# получатели на стороне Vehicle Soft и разная судьба при ошибке.
KIND_ROUTE = 'route'
KIND_FIELD_GEOMETRY = 'field_geometry'
KINDS = (KIND_ROUTE, KIND_FIELD_GEOMETRY)

# Потолок одной записи в байтах.
#
# [REASON]: маршрут на 107 точек в упакованном виде -- единицы килобайт,
# полигон поля из снимка -- 7 КБ. Восемь мегабайт покрывают самый крупный
# мыслимый контур с запасом в три порядка и при этом не дают одному
# ответу-переростку заполнить диск службы. Проверяется по СЕРИАЛИЗОВАННОЙ
# записи, а не по исходному объекту: на диск ложится именно она.
MAX_ENVELOPE_BYTES = 8 * 1024 * 1024

# Максимальная длина имени файла записи без расширения.
#
# [REASON]: Windows ограничивает путь целиком, а не имя, и каталог очереди
# может лежать глубоко. Короткое имя оставляет запас пути; уникальность
# держится на хеше, а не на длине читаемой части.
MAX_NAME_LENGTH = 96

# Маркеры, наличие которых в записи означает утечку.
#
# [REASON]: проверяется СЕРИАЛИЗОВАННАЯ запись, а не набор известных ключей.
# Список ключей пришлось бы поддерживать вручную, и новое поле от DJI
# проскочило бы мимо него молча. Подстрока по готовому JSON ловит и то, чего
# мы не предвидели.
SECRET_MARKERS = ('signedurl', 'ossaccesskeyid', 'signature=',
                  'x-amz-signature', 'x-amz-credential', 'set-cookie',
                  'authorization', 'bearer ', 'storage_state', 'x-auth-token',
                  'aliyuncs.com', 'x-oss-')

# Форма подписанной ссылки: параметр запроса, которым подписывают доступ.
#
# [REASON]: подстрочные маркеры выше ловят известные имена. Этот шаблон ловит
# ФОРМУ -- `?...expires=...`, `&signature=...` -- и потому срабатывает на
# подписи облака, имени которого мы ещё не видели. Оба контроля нужны: ни
# один не покрывает другой.
SIGNED_URL_QUERY = re.compile(
    r'[?&](?:x-amz-[a-z-]+|ossaccesskeyid|signature|sig|se|st|sp|sv|'
    r'expires|expiry|token|credential|key-pair-id|policy)=',
    re.IGNORECASE)

# Имя файла делается из dedupe_key: только эти символы, остальное вырезается.
_SAFE_NAME = re.compile(r'[^A-Za-z0-9._-]+')

# Суффикс недописанного файла. Не `.json`, поэтому `pending()` его не видит.
TEMP_SUFFIX = '.tmp'


class OutboxError(Exception):
    """Запись нельзя поставить в очередь или нельзя прочитать."""


class SecretInEnvelope(OutboxError):
    """В записи нашлось то, чего в ней быть не может."""


class EnvelopeTooLarge(OutboxError):
    """Запись больше потолка. На диск не попала."""


class CorruptEnvelope(OutboxError):
    """Файл очереди не читается или несёт чужую версию формата."""


def utc_now_iso():
    """Текущее время UTC в ISO-8601.

    [REASON]: `datetime.utcnow()` объявлен устаревшим и снимается в будущих
    версиях Python. Время записи -- timezone-aware, и суффикс зоны в строке
    сохраняется: наивная метка «12:00» из журнала прогона на Windows и та же
    метка из базы UTC отличаются на пять часов, и это уже стоило треку
    разбирательства.
    """
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def find_secret_markers(text):
    """Отсортированный список найденных маркеров. Пустой -- чисто."""
    lowered = text.lower()
    found = {marker for marker in SECRET_MARKERS if marker in lowered}
    if SIGNED_URL_QUERY.search(text):
        found.add('signed-url-query')
    return sorted(found)


def assert_no_secrets(payload):
    """Отказ, если в сериализованной записи есть маркер секрета."""
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    found = find_secret_markers(text)
    if found:
        # [REASON]: само значение НЕ попадает в текст исключения. Сообщение об
        # ошибке уходит в лог, и утечка через лог ничем не лучше утечки через
        # файл очереди.
        raise SecretInEnvelope(
            'the envelope carries %s; nothing was written' % ', '.join(found))


def dedupe_key_for(kind, identity, content_sha256):
    """Устойчивый ключ записи.

    Складывается из вида, естественного идентификатора и хеша содержимого.
    Тот же ответ, поставленный второй раз, даёт тот же ключ и ту же запись.

    [REASON]: хеш считается по ПОЛНОМУ идентификатору, а читаемая часть имени
    -- по обрезанному и отфильтрованному. Поэтому два идентификатора, которые
    после фильтра совпали (или различались только регистром -- на Windows это
    одно имя), всё равно дают разные файлы: различие уходит в хеш.
    """
    raw = '%s|%s|%s' % (kind, identity, content_sha256)
    digest = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]
    safe = _SAFE_NAME.sub('_', str(identity))
    room = MAX_NAME_LENGTH - len(kind) - len(digest) - 2
    return '%s_%s_%s' % (kind, safe[:max(room, 0)], digest)


class Outbox(object):
    """Очередь на диске: `pending/`, `sent/` и `corrupt/` в одном каталоге."""

    def __init__(self, root):
        self.root = Path(root)
        self.pending_dir = self.root / 'pending'
        self.sent_dir = self.root / 'sent'
        self.corrupt_dir = self.root / 'corrupt'

    def prepare(self):
        for directory in (self.pending_dir, self.sent_dir, self.corrupt_dir):
            directory.mkdir(parents=True, exist_ok=True)
        return self

    # -- постановка -----------------------------------------------------------

    def build_envelope(self, kind, identity, body, content_sha256, source=None,
                       received_at=None, diagnostics=None):
        """Готовая запись без записи на диск. Отдельно -- чтобы её можно было
        проверить на секреты и на размер, ничего не создав."""
        if kind not in KINDS:
            raise OutboxError('unknown envelope kind %r' % (kind,))
        if not isinstance(content_sha256, str) or not content_sha256:
            raise OutboxError('content_sha256 is required and must be a string')
        return {
            'envelope_version': ENVELOPE_VERSION,
            'kind': kind,
            'identity': identity,
            'content_sha256': content_sha256,
            'source': source or 'dji-smartfarm',
            'received_at': received_at or utc_now_iso(),
            'diagnostics': diagnostics or {},
            'body': body,
        }

    def enqueue(self, kind, identity, body, content_sha256, source=None,
                received_at=None, diagnostics=None):
        """Поставить запись. Возвращает (путь, был_ли_дубликат).

        При дубликате возвращается путь СУЩЕСТВУЮЩЕГО файла -- он может уже
        лежать в `sent/`, и назвать его лежащим в `pending/` значило бы
        соврать вызывающему о состоянии очереди.
        """
        envelope = self.build_envelope(kind, identity, body, content_sha256,
                                       source=source, received_at=received_at,
                                       diagnostics=diagnostics)
        text = self._serialize(envelope)

        key = dedupe_key_for(kind, identity, content_sha256)
        name = '%s.json' % key
        for directory in (self.pending_dir, self.sent_dir):
            existing = directory / name
            if existing.exists():
                # [REASON]: повторная постановка не переписывает уже
                # отправленное и не создаёт второй файл. Тот же ответ -- та же
                # запись, и это ровно то, что делает повторный прогон
                # безопасным.
                return existing, True

        self.prepare()
        target = self.pending_dir / name
        self._atomic_write(target, text)
        return target, False

    def _serialize(self, envelope):
        """Текст записи, уже проверенный на секреты и на размер.

        [REASON]: маркеры ищутся в ТОМ САМОМ тексте, который ляжет на диск, а
        не в отдельно построенном представлении той же записи. Две
        сериализации -- это два разных текста, и рано или поздно проверка
        начнёт проверять не то, что пишется.
        """
        text = json.dumps(envelope, ensure_ascii=False, indent=1)
        found = find_secret_markers(text)
        if found:
            raise SecretInEnvelope(
                'the envelope carries %s; nothing was written'
                % ', '.join(found))
        size = len(text.encode('utf-8'))
        if size > MAX_ENVELOPE_BYTES:
            raise EnvelopeTooLarge(
                'the envelope is %d bytes, the cap is %d; nothing was written'
                % (size, MAX_ENVELOPE_BYTES))
        return text

    def _atomic_write(self, target, text):
        """Запись через временный файл и `os.replace`.

        [REASON]: временный файл удаляется в `except`, а не только в `finally`
        при успехе. Прогон, упавший на середине сериализации, иначе оставлял
        бы `.tmp` за каждым сбоем, и каталог очереди медленно зарастал бы
        мусором, который никто не читает и никто не убирает.
        """
        handle = tempfile.NamedTemporaryFile(
            mode='w', encoding='utf-8', dir=str(target.parent),
            prefix=target.stem + '.', suffix=TEMP_SUFFIX, delete=False)
        temp_name = handle.name
        try:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            handle.close()
            _remove_quietly(temp_name)
            raise
        handle.close()
        try:
            os.replace(temp_name, str(target))
        except BaseException:
            _remove_quietly(temp_name)
            raise
        _fsync_directory(target.parent)
        return target

    # -- чтение ---------------------------------------------------------------

    def pending(self):
        """Записи, ожидающие отправки, в устойчивом порядке.

        Недописанные `.tmp` сюда не попадают по построению: маска `*.json`.
        """
        return _sorted_json(self.pending_dir)

    def sent(self):
        return _sorted_json(self.sent_dir)

    def corrupt(self):
        return _sorted_json(self.corrupt_dir)

    def records(self, kind):
        """Пути записей ОДНОГО вида, из `pending/` и `sent/`.

        [REASON]: опирается на то, что `dedupe_key_for` ставит имя вида в
        начало имени файла, и это не украшение: возобновление сбора маршрутов
        иначе разбирает JSON каждого контура, а обход справочника -- JSON
        каждого маршрута со всеми его точками. На тридцати тысячах маршрутов
        это разница между «мгновенно» и «полминуты на пустом месте».
        """
        if kind not in KINDS:
            raise OutboxError('unknown envelope kind %r' % (kind,))
        prefix = '%s_' % kind
        return [path for path in (self.pending() + self.sent())
                if path.name.startswith(prefix)]

    def stale_temp_files(self):
        """Оставшиеся от оборванной записи временные файлы."""
        if not self.pending_dir.exists():
            return []
        return sorted(self.pending_dir.glob('*%s' % TEMP_SUFFIX))

    def sweep_stale_temp(self):
        """Удалить остатки оборванных записей. Возвращает их число."""
        removed = 0
        for path in self.stale_temp_files():
            if _remove_quietly(path):
                removed += 1
        return removed

    def read(self, path):
        """Прочитать запись. Всё нечитаемое -- `CorruptEnvelope`.

        [REASON]: единый класс ошибки. Без него вызывающий ловил бы
        `JSONDecodeError` из одного места, `UnicodeDecodeError` из другого и
        `OSError` из третьего -- и почти наверняка не поймал бы все три, а
        один битый файл в очереди валил бы весь прогон отправки.
        """
        path = Path(path)
        try:
            with open(path, encoding='utf-8') as handle:
                envelope = json.load(handle)
        except (ValueError, UnicodeDecodeError) as exc:
            raise CorruptEnvelope('%s is not readable JSON (%s)'
                                  % (path.name, type(exc).__name__))
        except OSError as exc:
            raise CorruptEnvelope('%s could not be read (%s)'
                                  % (path.name, type(exc).__name__))
        if not isinstance(envelope, dict):
            raise CorruptEnvelope('%s decodes to %s, not to an object'
                                  % (path.name, type(envelope).__name__))
        version = envelope.get('envelope_version')
        if version != ENVELOPE_VERSION:
            raise CorruptEnvelope(
                'envelope %s has version %r, this reader knows %d'
                % (path.name, version, ENVELOPE_VERSION))
        if envelope.get('kind') not in KINDS:
            raise CorruptEnvelope('envelope %s has kind %r'
                                  % (path.name, envelope.get('kind')))
        return envelope

    # -- перемещение ----------------------------------------------------------

    def mark_sent(self, path):
        """Перенести запись в `sent/`. Возврата к `pending/` не бывает."""
        return self._move(path, self.sent_dir)

    def quarantine(self, path):
        """Убрать нечитаемую запись из очереди, но НЕ удалить.

        [REASON]: удаление битого элемента уничтожает единственное
        свидетельство того, что запись вообще была. Он уезжает в `corrupt/`,
        перестаёт мешать прогону и остаётся доступен для разбора.
        """
        return self._move(path, self.corrupt_dir)

    def _move(self, path, directory):
        self.prepare()
        target = directory / Path(path).name
        # os.replace, а не os.rename: второй на Windows падает, когда цель
        # уже существует -- а она существует при повторной отправке.
        os.replace(str(path), str(target))
        _fsync_directory(directory)
        return target

    def counts(self):
        return {
            'pending': len(self.pending()),
            'sent': len(self.sent()),
            'corrupt': len(self.corrupt()),
            'stale_temp': len(self.stale_temp_files()),
        }


def _sorted_json(directory):
    if not directory.exists():
        return []
    return sorted(directory.glob('*.json'))


def _remove_quietly(path):
    try:
        os.remove(str(path))
        return True
    except OSError:
        return False


def _fsync_directory(directory):
    """Сбросить запись каталога на диск. На Windows молча пропускается.

    [REASON]: без этого переименование может остаться в кеше каталога, и
    после внезапной перезагрузки файл окажется потерянным при том, что его
    содержимое уже на диске. На Windows `os.open` каталога не работает вовсе,
    и там гарантию даёт сама файловая система -- поэтому не ошибка, а пропуск.
    """
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return False
    try:
        os.fsync(fd)
        return True
    except OSError:  # pragma: no cover -- зависит от файловой системы
        return False
    finally:
        os.close(fd)
