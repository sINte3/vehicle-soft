# -*- coding: utf-8 -*-
"""fin_recon/package.py -- проверка приватного пакета снимка до импорта.

Пакет -- каталог:

    manifest.json        что в пакете, откуда и зачем (fin-recon-package/1)
    SHA256SUMS.txt       хеш каждого файла пакета, кроме самого себя
    snapshot.json        снимок (fin-recon-snapshot/1)
    blobs/<sha256>.<ext> неизменённые оригиналы, имя файла -- его SHA-256
    expectations/*.json  необязательно: приватные контрольные ожидания месяца
    tools/*              необязательно: инструмент, собравший снимок

[REASON]: пути внутри пакета -- только ASCII. Оригинальные имена книг
(кириллица, имена людей) живут в manifest.json, а не в файловой системе:
Expand-Archive и консоль Windows PowerShell 5.1 с кодовой страницей OEM
калечат не-ASCII имена, и проверка хешей по искалеченному имени упала бы
не там, где лежит проблема. Имя блоба равно его SHA-256 -- подмена файла
видна по имени, а не только по SHA256SUMS.

Проверяется ВСЁ и до транзакции: лишний файл, недостающий файл, один байт
в любом файле, ссылка снимка на источник, которого нет в пакете, книга,
которой нет в исходном архиве, -- отказ. SHA-256 подтверждает целостность
битов относительно манифеста, но не подлинность хозяйственной операции и
не подпись владельца.
"""

import hashlib
import json
import os
import re
import zipfile

import fin_recon as fr
from fin_recon import contract

SUMS_NAME = 'SHA256SUMS.txt'
MANIFEST_NAME = 'manifest.json'
SNAPSHOT_NAME = 'snapshot.json'

_SUM_LINE = re.compile(r'^([0-9a-f]{64}) [ *](\S.*)$')
_SAFE_PATH = re.compile(r'^[A-Za-z0-9_][A-Za-z0-9_.\-]*'
                        r'(/[A-Za-z0-9_][A-Za-z0-9_.\-]*)*$')
_BLOB_PATH = re.compile(r'^blobs/([0-9a-f]{64})(\.[a-z0-9]{1,8})?$')
_SHA = re.compile(fr.SHA256_RE)

MANIFEST_KEYS = ('schema', 'package_id', 'period', 'version',
                 'snapshot_schema_version', 'normalization_version',
                 'producer', 'assembled_at', 'register_compiled_on',
                 'snapshot', 'blobs', 'outputs', 'register',
                 'technical_export', 'external_evidence', 'integrity_note')
BLOB_KEYS = ('path', 'sha256', 'bytes', 'original_name', 'origin', 'role')
OUTPUT_KEYS = ('path', 'sha256', 'bytes', 'role')
SNAPSHOT_ENTRY_KEYS = ('path', 'sha256', 'content_sha256', 'bytes')


class PackageError(Exception):
    """Пакет негоден. problems -- полный список причин."""

    def __init__(self, problems):
        self.problems = list(problems)
        super(PackageError, self).__init__(
            '%d package problem(s)' % len(self.problems))


class Package(object):
    """Проверенный пакет: манифест, снимок, хеши."""

    def __init__(self, root, manifest, snapshot, files):
        self.root = root
        self.manifest = manifest
        self.snapshot = snapshot
        self.files = files                       # relpath -> sha256
        self.manifest_sha256 = files[MANIFEST_NAME]
        self.snapshot_file_sha256 = files[SNAPSHOT_NAME]
        self.content_sha256 = contract.content_sha256(snapshot)
        self.period = snapshot['header']['period']
        self.version = snapshot['header']['version']
        self.package_id = manifest['package_id']

    def path(self, relpath):
        return os.path.join(self.root, *relpath.split('/'))


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def read_sums(path):
    """{relpath: sha256} из SHA256SUMS.txt (формат sha256sum)."""
    problems = []
    out = {}
    with open(path, 'rb') as fh:
        raw = fh.read()
    try:
        text = raw.decode('ascii')
    except UnicodeDecodeError:
        return {}, ['%s: not ASCII' % SUMS_NAME]
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        match = _SUM_LINE.match(line.rstrip('\r'))
        if not match:
            problems.append('%s line %d: not "<sha256>  <path>"'
                            % (SUMS_NAME, number))
            continue
        digest, rel = match.group(1), match.group(2)
        if not _SAFE_PATH.match(rel) or '..' in rel.split('/'):
            problems.append('%s line %d: unsafe path %r'
                            % (SUMS_NAME, number, rel))
            continue
        if rel in out:
            problems.append('%s line %d: %s listed twice'
                            % (SUMS_NAME, number, rel))
        out[rel] = digest
    return out, problems


def _walk(root):
    """[relpath] всех файлов каталога; символьные ссылки -- проблема."""
    files, problems = [], []
    for dirpath, dirnames, filenames in os.walk(root):
        for name in list(dirnames):
            if os.path.islink(os.path.join(dirpath, name)):
                problems.append('symbolic link %s' % name)
                dirnames.remove(name)
        for name in filenames:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, '/')
            if os.path.islink(full):
                problems.append('symbolic link %s' % rel)
                continue
            files.append(rel)
    return sorted(files), problems


def _load_json(path, label, problems):
    try:
        with open(path, 'rb') as fh:
            raw = fh.read()
        return json.loads(raw.decode('utf-8'),
                          parse_float=_reject_float,
                          object_pairs_hook=_no_duplicate_keys)
    except (UnicodeDecodeError, ValueError) as exc:
        problems.append('%s: not valid JSON (%s)' % (label, exc))
        return None


def _reject_float(text):
    # [REASON]: число с точкой в JSON -- float, а деньги и гектары контракта
    # только строки. Отказ здесь ловит его раньше, чем Python округлит.
    raise ValueError('a JSON float %s is not allowed: decimals are strings'
                     % text)


def _no_duplicate_keys(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError('duplicate key %r' % key)
        out[key] = value
    return out


def _zip_member_hashes(path):
    hashes = set()
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            hashes.add(hashlib.sha256(zf.read(info)).hexdigest())
    return hashes


def verify(root):
    """Проверить пакет целиком; вернуть Package или бросить PackageError."""
    problems = []
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        raise PackageError(['package directory not found: %s' % root])
    for name in (SUMS_NAME, MANIFEST_NAME, SNAPSHOT_NAME):
        if not os.path.isfile(os.path.join(root, name)):
            problems.append('missing %s' % name)
    if problems:
        raise PackageError(problems)

    listed, sum_problems = read_sums(os.path.join(root, SUMS_NAME))
    problems.extend(sum_problems)
    present, walk_problems = _walk(root)
    problems.extend(walk_problems)
    present = [rel for rel in present if rel != SUMS_NAME]
    for rel in present:
        if rel not in listed:
            problems.append('file not in %s: %s' % (SUMS_NAME, rel))
        if not _SAFE_PATH.match(rel):
            problems.append('unsafe file name in the package: %r' % rel)
    for rel in sorted(listed):
        if rel not in present:
            problems.append('listed in %s but missing: %s' % (SUMS_NAME, rel))
    files = {}
    for rel in present:
        actual = sha256_file(os.path.join(root, *rel.split('/')))
        files[rel] = actual
        if rel in listed and listed[rel] != actual:
            problems.append('SHA-256 mismatch: %s' % rel)
        blob = _BLOB_PATH.match(rel)
        if rel.startswith('blobs/') and (not blob or blob.group(1) != actual):
            problems.append('blob name is not its SHA-256: %s' % rel)
    if problems:
        raise PackageError(problems)

    manifest = _load_json(os.path.join(root, MANIFEST_NAME), MANIFEST_NAME,
                          problems)
    snapshot = _load_json(os.path.join(root, SNAPSHOT_NAME), SNAPSHOT_NAME,
                          problems)
    if manifest is None or snapshot is None:
        raise PackageError(problems)

    problems.extend('snapshot.json: ' + p for p in contract.validate(snapshot))
    problems.extend(_check_manifest(manifest, snapshot, files, root))
    if problems:
        raise PackageError(problems)
    return Package(root, manifest, snapshot, files)


def _check_manifest(manifest, snapshot, files, root):
    problems = []

    def bad(message):
        problems.append('manifest.json: ' + message)

    if not isinstance(manifest, dict):
        return ['manifest.json: must be an object']
    extra = sorted(set(manifest) - set(MANIFEST_KEYS))
    missing = [k for k in MANIFEST_KEYS if k not in manifest]
    if extra:
        bad('unexpected key(s) %s' % ', '.join(extra))
    if missing:
        bad('missing key(s) %s' % ', '.join(missing))
        return problems
    if manifest['schema'] != fr.PACKAGE_SCHEMA:
        bad('schema must be %r' % fr.PACKAGE_SCHEMA)
    if manifest['snapshot_schema_version'] != fr.SNAPSHOT_SCHEMA:
        bad('snapshot_schema_version must be %r' % fr.SNAPSHOT_SCHEMA)
    header = snapshot.get('header') or {}
    if manifest['period'] != header.get('period') or \
            manifest['version'] != header.get('version'):
        bad('period/version %r/%r differ from the snapshot %r/%r'
            % (manifest['period'], manifest['version'], header.get('period'),
               header.get('version')))
    if manifest['normalization_version'] != header.get('normalization_version'):
        bad('normalization_version differs from the snapshot')
    if not isinstance(manifest['package_id'], str) or \
            not re.match(r'^[a-z0-9][a-z0-9.\-]{2,79}$', manifest['package_id']):
        bad('package_id must be lowercase ASCII, 3..80 characters')
    entry = manifest['snapshot']
    if not isinstance(entry, dict) or sorted(entry) != sorted(
            SNAPSHOT_ENTRY_KEYS):
        bad('snapshot entry must have keys %s' % ', '.join(SNAPSHOT_ENTRY_KEYS))
    else:
        if entry['path'] != SNAPSHOT_NAME:
            bad('snapshot.path must be %s' % SNAPSHOT_NAME)
        if entry['sha256'] != files.get(SNAPSHOT_NAME):
            bad('snapshot.sha256 does not match snapshot.json')
        if entry['content_sha256'] != contract.content_sha256(snapshot):
            bad('snapshot.content_sha256 does not match the canonical content')
        if entry['bytes'] != os.path.getsize(os.path.join(root, SNAPSHOT_NAME)):
            bad('snapshot.bytes does not match snapshot.json')

    blobs = manifest['blobs'] if isinstance(manifest['blobs'], list) else []
    if not isinstance(manifest['blobs'], list):
        bad('blobs must be a list')
    by_sha = {}
    for i, blob in enumerate(blobs):
        where = 'blobs[%d]' % i
        if not isinstance(blob, dict) or sorted(blob) != sorted(BLOB_KEYS):
            bad('%s must have keys %s' % (where, ', '.join(BLOB_KEYS)))
            continue
        path = blob['path']
        match = _BLOB_PATH.match(path) if isinstance(path, str) else None
        if not match or match.group(1) != blob['sha256']:
            bad('%s.path must be blobs/<sha256>.<ext>' % where)
            continue
        if files.get(path) != blob['sha256']:
            bad('%s: %s is not in the package with that SHA-256'
                % (where, path))
        elif blob['bytes'] != os.path.getsize(os.path.join(root, *path.split('/'))):
            bad('%s.bytes does not match the file' % where)
        if blob['role'] not in fr.SOURCE_ROLES:
            bad('%s.role %r is unknown' % (where, blob['role']))
        if path in by_sha.values():
            bad('%s: %s listed twice' % (where, path))
        by_sha[blob['sha256']] = path
    for rel in files:
        if rel.startswith('blobs/') and rel not in by_sha.values():
            bad('%s is in the package but not in blobs' % rel)

    outputs = manifest['outputs'] if isinstance(manifest['outputs'], list) else []
    for i, out in enumerate(outputs):
        where = 'outputs[%d]' % i
        if not isinstance(out, dict) or sorted(out) != sorted(OUTPUT_KEYS):
            bad('%s must have keys %s' % (where, ', '.join(OUTPUT_KEYS)))
            continue
        if files.get(out['path']) != out['sha256']:
            bad('%s: %s does not match the package' % (where, out['path']))

    # Источники снимка: каждый, кто «в пакете», лежит в blobs с тем же хешем;
    # извлечённый из архива -- действительно есть в этом архиве.
    sources = {s.get('key'): s for s in snapshot.get('sources') or []
               if isinstance(s, dict)}
    member_cache = {}
    for key, src in sorted(sources.items(), key=lambda kv: str(kv[0])):
        if src.get('in_package'):
            if src.get('sha256') not in by_sha:
                bad('source %s (%s) is marked in_package but no blob has its '
                    'SHA-256' % (key, src.get('role')))
        container = sources.get(src.get('container'))
        if container is not None:
            blob_path = by_sha.get(container.get('sha256'))
            if blob_path is None:
                bad('source %s: its container %s is not in the package'
                    % (key, src.get('container')))
                continue
            if blob_path not in member_cache:
                try:
                    member_cache[blob_path] = _zip_member_hashes(
                        os.path.join(root, *blob_path.split('/')))
                except (zipfile.BadZipFile, OSError) as exc:
                    bad('container %s is not a readable ZIP (%s)'
                        % (blob_path, exc))
                    member_cache[blob_path] = set()
            if src.get('sha256') not in member_cache[blob_path]:
                bad('source %s is not a member of its container %s'
                    % (key, src.get('container')))

    register = manifest['register']
    if not isinstance(register, dict) or \
            any(not isinstance(register.get(k), str) or
                not _SHA.match(register.get(k))
                for k in ('xlsx_sha256', 'md_sha256')):
        bad('register must name xlsx_sha256 and md_sha256')
    else:
        for k in ('xlsx_sha256', 'md_sha256'):
            if register[k] not in by_sha:
                bad('register.%s is not a blob of the package' % k)
    tech = manifest['technical_export']
    if not isinstance(tech, dict) or not isinstance(tech.get('vs_export_sha256'),
                                                    str):
        bad('technical_export must name vs_export_sha256')
    elif tech['vs_export_sha256'] not in by_sha:
        bad('technical_export.vs_export_sha256 is not a blob of the package')
    if not isinstance(manifest['external_evidence'], list):
        bad('external_evidence must be a list')
    if not isinstance(manifest['integrity_note'], str) or \
            not manifest['integrity_note'].strip():
        bad('integrity_note must be a non-empty string')
    return problems


def load_expectations(path):
    """Приватные ожидания месяца (fin-recon-expectations/1)."""
    problems = []
    data = _load_json(path, os.path.basename(path), problems)
    if data is None:
        raise PackageError(problems)
    if not isinstance(data, dict) or data.get('schema') != fr.EXPECTATIONS_SCHEMA:
        raise PackageError(['%s: schema must be %r'
                            % (os.path.basename(path), fr.EXPECTATIONS_SCHEMA)])
    for key in ('period', 'version', 'expect', 'source'):
        if key not in data:
            problems.append('%s: missing key %s'
                            % (os.path.basename(path), key))
    if not isinstance(data.get('expect'), dict) or not data.get('expect'):
        problems.append('%s: expect must be a non-empty object'
                        % os.path.basename(path))
    if problems:
        raise PackageError(problems)
    return data


__all__ = ['Package', 'PackageError', 'read_sums', 'sha256_file', 'verify',
           'load_expectations', 'MANIFEST_NAME', 'SNAPSHOT_NAME', 'SUMS_NAME']
