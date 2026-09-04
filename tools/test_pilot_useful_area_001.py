# -*- coding: utf-8 -*-
"""Проверка операторского комплекта DRONE-USEFUL-AREA-PILOT-001.

    python tools/test_pilot_useful_area_001.py

Комплект -- это готовые к вставке команды PowerShell и приборы, которые
решают, можно ли верить прогону. Вставлены команды будут буквально, а решению
поверят на слово. Поэтому комплект проверяется так же, как код.

Что здесь держится и почему именно это:

* **ревизий ДВЕ, и они разные.** `PRODUCT_SHA` -- проверенная ревизия
  продукта, `KIT_SHA` -- ревизия комплекта, которая ИЗМЕРЯЕТСЯ и не может
  быть константой: комплект живёт в коммите, который её создаёт. Скрипт,
  требующий `HEAD == PRODUCT_SHA` от репозитория, в котором лежит сам,
  требовал бы собственного отсутствия;
* **исполняемый файл привязан к ревизии, а не к копии.** Совпадение копии с
  файлом рабочего дерева доказывает аккуратность копирования; здесь
  сверяется git-blob -- и в истории, и на диске;
* **`C:\\transport-report-staging` НАЧИНАЕТСЯ с `C:\\transport-report`**, и
  гвардия через подстроку объявила бы площадку продакшеном;
* **отпечаток `drone_flights.area_ha` обязан РАЗЛИЧАТЬ два случая**: рядом с
  каждым утверждением стоит отрицательный контроль, не меняющий сумму;
* **«сухой прогон ничего не записал» доказывается ПОЛНЫМ отпечатком** всех
  строк и всех колонок. Число строк не меняется, когда строку переписали;
* **пересчёт не запускается по неполному захвату** -- ворота стоят до сухого
  прогона и тем более до `--apply`;
* **улики одного запуска.** Конверт сверяется по всем полям сразу, включая
  порядок времени: улики двух прогонов, сложенные вместе, дают отчёт, который
  выглядит безупречно;
* **`GO` требует решения владельца.** Порог, выбранный сессией, -- не решение
  владельца, и подставленный молча он им притворяется;
* **smoke-тест 404, 401 и 403 успехом не считает**, а redirect принимает
  только внутрь площадки;
* **Windows PowerShell 5.1 -- целевая платформа.** `utf8NoBOM` в ней нет, а
  `>` пишет UTF-16LE.

Только stdlib: набор гоняется в CI. Вывод -- ASCII.

ГЕОМЕТРИЯ ЗДЕСЬ СИНТЕТИЧЕСКАЯ. Поле -- квадрат 200 м вокруг круглой точки,
вылеты пронумерованы с 900001. Ни одной настоящей координаты.
"""

import contextlib
import copy
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest

from http.server import BaseHTTPRequestHandler, HTTPServer

from datetime import datetime, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KIT_DIR = os.path.join(REPO_ROOT, 'ops', 'pilot_useful_area_001')
for path in (REPO_ROOT, KIT_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

import pilot_blob_manifest as blobs  # noqa: E402
import pilot_collect_check as collect_check  # noqa: E402
import pilot_collect_gate as gate  # noqa: E402
import pilot_common as common  # noqa: E402
import pilot_db_probe as probe  # noqa: E402
import pilot_recalc_parse as recalc_parse  # noqa: E402
import pilot_report as report_mod  # noqa: E402
import pilot_repo_check as repo_check  # noqa: E402

import drone_coverage_recalc as recalc  # noqa: E402

PS_SCRIPTS = ('PREFLIGHT_AND_COPY_TEST.ps1', 'STAGING_DEPLOY_AND_MIGRATE.ps1',
              'STAGING_ROLLBACK.ps1', 'BAK_TEX11_DJI_COLLECT_TO_STAGING.ps1',
              'STAGING_RECALCULATE_AND_VERIFY.ps1', 'STAGING_PILOT_REPORT.ps1')
PS_FILES = PS_SCRIPTS + ('PilotKit.psm1',)

STAGING_SCRIPTS = ('PREFLIGHT_AND_COPY_TEST.ps1',
                   'STAGING_DEPLOY_AND_MIGRATE.ps1', 'STAGING_ROLLBACK.ps1',
                   'STAGING_RECALCULATE_AND_VERIFY.ps1',
                   'STAGING_PILOT_REPORT.ps1')

RUN_STEP_SCRIPTS = ('STAGING_DEPLOY_AND_MIGRATE.ps1', 'STAGING_ROLLBACK.ps1',
                    'BAK_TEX11_DJI_COLLECT_TO_STAGING.ps1',
                    'STAGING_RECALCULATE_AND_VERIFY.ps1',
                    'STAGING_PILOT_REPORT.ps1')

# Every script takes the reviewer-approved kit revision, step 1 included.
APPROVED_SHA_SCRIPTS = PS_SCRIPTS

# SYNTHETIC / NOT-REAL.
LAT0 = 39.70
LON0 = 64.40
M_PER_DEG_LAT = 111320.0
M_PER_DEG_LON = 111320.0 * 0.7679
DAY = '2026-06-05'


def script_text(name):
    with open(os.path.join(KIT_DIR, name), encoding='utf-8') as handle:
        return handle.read()


def code_lines(name):
    """Строки скрипта БЕЗ комментариев и без блока документации.

    [REASON]: проверки «команды X здесь нет» обязаны смотреть на команды.
    Первая редакция читала весь файл и нашла `git reset --hard` в абзаце,
    который объясняет, почему этой команды в комплекте нет.
    """
    lines = []
    inside_block = False
    for line in script_text(name).splitlines():
        stripped = line.strip()
        if stripped.startswith('<#'):
            inside_block = True
        if inside_block:
            if '#>' in stripped:
                inside_block = False
            continue
        if stripped.startswith('#'):
            continue
        lines.append(line)
    return lines


def code_text(name):
    return '\n'.join(code_lines(name))


def at(east_m, north_m):
    return [LAT0 + north_m / M_PER_DEG_LAT, LON0 + east_m / M_PER_DEG_LON]


def square(half_m):
    corners = [at(-half_m, -half_m), at(half_m, -half_m), at(half_m, half_m),
               at(-half_m, half_m), at(-half_m, -half_m)]
    return {'type': 'Polygon',
            'coordinates': [[[point[1], point[0]] for point in corners]]}


def pass_line(east_m, north_from, north_to, step_m=5.0):
    points = []
    north = north_from
    while north <= north_to + 1e-9:
        points.append(at(east_m, north))
        north += step_m
    return points


def run_python(*arguments, **kwargs):
    return subprocess.run([sys.executable] + list(arguments),
                          capture_output=True, text=True, **kwargs)


KIT_SHA_FIXTURE = 'b' * 40


def stamp(offset_seconds=0):
    return (datetime.utcnow()
            + timedelta(seconds=offset_seconds)).strftime('%Y-%m-%dT%H:%M:%SZ')


def envelope(kind, payload, run_id, kit_sha=KIT_SHA_FIXTURE, when=None,
             product_sha=None, target_day=None):
    return {'kit': common.KIT_ID, 'kit_version': common.KIT_VERSION,
            'evidence_kind': kind, 'run_id': run_id, 'kit_sha': kit_sha,
            'product_sha': product_sha or common.PRODUCT_SHA,
            'generated_utc': when or stamp(),
            'target_day': target_day or DAY, 'payload': payload}


HEALTHY_COUNTERS = {
    'mode': 'route-collect', 'dry_run': False, 'region': 'CN',
    'probe_route_responses': 2, 'probe_observations': 2, 'probe_confirmed': 2,
    'probe_errors': 0, 'probe_skipped_over_cap': 0,
    'probe_request_failures': 0, 'probe_pending_requests': 0,
    'probe_operator_answered': True, 'probe_drained': True,
    'collect_live_confirmed': True, 'collect_bodies_captured': 2,
    'collect_decode_failures': 0, 'collect_capture_errors': 0,
    'collect_routes_captured': 2, 'collect_routes_queued': 2,
    'collect_routes_duplicate': 0, 'collect_send_enabled': True,
    'collect_envelopes_sent': 2, 'collect_batch_accepted': True,
    'collect_left_pending': 0, 'collect_seen': 2, 'collect_new': 2,
    'collect_updated': 0, 'collect_unchanged': 0, 'collect_errors': 0,
    'collect_unlinked': 0, 'exit': 0,
}


def healthy_collect(run_id, when=None, **overrides):
    counters = dict(HEALTHY_COUNTERS)
    counters.update(overrides)
    return envelope('collect:summary', {
        'counters': counters, 'passed': True, 'reasons': [],
        'ingest_counters_balance': True,
        'no_unfinished_route_requests': True}, run_id, when=when)


_SHARED = {}


def shared_evidence():
    """Один набор здоровых улик на весь модуль, собранный по требованию.

    [REASON]: три класса брали улики из `Verdict.setUpClass`. Это работало
    только потому, что unittest идёт по алфавиту и Verdict оказывался раньше;
    запуск одного класса отдельно падал на AttributeError. Тест, зависящий от
    порядка запуска, проверяет порядок запуска.
    """
    if _SHARED:
        return _SHARED
    site = SyntheticSite()
    paths = site.recalc_evidence(run_id=site.run_id)
    runs = [common.read_evidence(paths[label])
            for label in ('dry-run', 'apply-1', 'apply-2')]
    _result, snapshot = site.snapshot(run_id=site.run_id)
    area = snapshot['payload']['area_ha']
    _SHARED.update({
        'site': site,
        'run_id': site.run_id,
        'runs': runs,
        'snapshot': snapshot,
        'preflight': envelope('preflight', {
            'migration_on_copy_ok': True,
            'area_ha_before': area, 'area_ha_after': area},
            site.run_id, when=stamp(-300)),
        'deploy': envelope('deploy', {
            'phase': 'done',
            'migration_on_staging_ok': True,
            'area_ha_before': area, 'area_ha_after': area,
            'backup_path': 'D:\\runs\\x\\backup\\transport_x.db',
            'backup_sha256': 'c' * 64,
            'backup_verified': True,
            'service_started': True,
            'smoke_test_ok': True,
            'smoke_test_status': 200,
            'smoke_test_path': '/login',
            'smoke_page_marker_seen': True,
            'sha_before': 'aa11b9f000000000000000000000000000000abc',
            'sha_after': common.PRODUCT_SHA}, site.run_id, when=stamp(-240)),
        'collect': healthy_collect(site.run_id, when=stamp(-180)),
    })
    return _SHARED


class SyntheticSite(object):
    """Временная база, ПРОШЕДШАЯ НАСТОЯЩУЮ миграцию, и улики по ней.

    Миграция запускается так же, как её запускает операторский скрипт: файл
    МАТЕРИАЛИЗУЕТСЯ из git-blob-а проверенной ревизии в песочницу рядом с
    `instance/transport.db`. Если этот способ перестанет работать, он
    перестанет работать и здесь -- а не только на сервере.
    """

    def __init__(self, materialize=True):
        self.root = tempfile.mkdtemp(prefix='pilot_kit_')
        self.sandbox = os.path.join(self.root, 'sandbox')
        os.makedirs(os.path.join(self.sandbox, 'instance'))
        self.db = os.path.join(self.sandbox, 'instance', 'transport.db')
        self.evidence = os.path.join(self.root, 'evidence')
        os.makedirs(self.evidence)
        self.run_id = common.new_run_id()
        self._build_base()
        self.materialized = self._migrate(materialize)
        self._populate()
        self.recalc_output = {}
        for label, mode in (('dry-run', '--dry-run'), ('apply-1', '--apply'),
                            ('apply-2', '--apply')):
            self.recalc_output[label] = self._recalculate(mode, label)

    def _build_base(self):
        con = sqlite3.connect(self.db)
        try:
            con.executescript(
                'CREATE TABLE drone_units ('
                ' id INTEGER PRIMARY KEY, number INTEGER);'
                'CREATE TABLE drone_flights ('
                ' id INTEGER PRIMARY KEY AUTOINCREMENT, dji_flight_id BIGINT,'
                ' drone_unit_id INTEGER, nickname_raw TEXT,'
                ' started_at DATETIME, area_ha FLOAT);'
                'CREATE TABLE field_contours ('
                ' id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT,'
                ' external_id TEXT, name TEXT, geometry_geojson TEXT,'
                ' is_active BOOLEAN, bbox_min_lat FLOAT, bbox_max_lat FLOAT,'
                ' bbox_min_lng FLOAT, bbox_max_lng FLOAT);')
            con.commit()
        finally:
            con.close()

    def _migrate(self, materialize):
        report = None
        if materialize and _product_sha_is_reachable():
            report = repo_check.materialize(
                REPO_ROOT, common.PRODUCT_SHA, self.sandbox,
                ['migrate_drones_useful_area_001.py', 'migration_utils.py'])
        else:
            for name in ('migrate_drones_useful_area_001.py',
                         'migration_utils.py'):
                shutil.copy2(os.path.join(REPO_ROOT, name),
                             os.path.join(self.sandbox, name))
        result = run_python(
            os.path.join(self.sandbox, 'migrate_drones_useful_area_001.py'),
            cwd=self.sandbox)
        if result.returncode != 0:
            raise AssertionError('the migration failed in the sandbox: %s%s'
                                 % (result.stdout, result.stderr))
        return report

    def _populate(self):
        geojson = square(100.0)
        ring = geojson['coordinates'][0]
        lats = [point[1] for point in ring]
        lngs = [point[0] for point in ring]
        con = sqlite3.connect(self.db)
        try:
            con.execute('INSERT INTO drone_units (id, number) VALUES (6, 6)')
            con.execute(
                'INSERT INTO field_contours (source, external_id, name,'
                ' geometry_geojson, is_active, bbox_min_lat, bbox_max_lat,'
                ' bbox_min_lng, bbox_max_lng) VALUES (?,?,?,?,1,?,?,?,?)',
                ('dji', 'SYNTHETIC-1', 'SYNTHETIC field',
                 json.dumps(geojson), min(lats), max(lats), min(lngs),
                 max(lngs)))
            for index, east in enumerate((-40.0, 0.0), start=1):
                flight_id = 900000 + index
                con.execute(
                    'INSERT INTO drone_flights (dji_flight_id, drone_unit_id,'
                    ' nickname_raw, started_at, area_ha) VALUES (?,6,?,?,?)',
                    (flight_id, 'SYNTHETIC-NICK',
                     '2026-06-05 03:0%d:00' % index, 2.0))
                row_id = con.execute(
                    'SELECT id FROM drone_flights WHERE dji_flight_id = ?',
                    (flight_id,)).fetchone()[0]
                points = pass_line(east, -90.0, 90.0)
                con.execute(
                    'INSERT INTO drone_flight_routes (dji_flight_id,'
                    ' drone_flight_id, point_count, points_json,'
                    ' spray_width_m, spray_width_recorded, dji_area_m2,'
                    ' content_sha256, source, received_at, updated_at,'
                    ' ingest_count) VALUES (?,?,?,?,8.0,1,?,?,?,?,?,1)',
                    (flight_id, row_id, len(points),
                     json.dumps(points, separators=(',', ':')), 20000.0,
                     'SYNTHETIC-CONTENT-%d' % flight_id, 'dji-ui-capture',
                     '2026-06-05 03:00:00', '2026-06-05 03:00:00'))
            con.commit()
        finally:
            con.close()

    def _recalculate(self, mode, label):
        result = run_python(
            os.path.join(REPO_ROOT, 'tools',
                         'recalculate_drone_useful_area.py'),
            '--from', DAY, '--to', DAY, mode, '--db', self.db)
        if result.returncode != 0:
            raise AssertionError('the recalculation failed (%s): %s%s'
                                 % (label, result.stdout, result.stderr))
        path = os.path.join(self.evidence, 'recalc_%s.txt' % label)
        with open(path, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(result.stdout)
        return path

    def snapshot(self, *requirements, **kwargs):
        arguments = [os.path.join(KIT_DIR, 'pilot_db_probe.py'), 'snapshot',
                     '--db', self.db, '--day', DAY,
                     '--run-id', kwargs.get('run_id', self.run_id),
                     '--kit-sha', kwargs.get('kit_sha', KIT_SHA_FIXTURE)]
        for requirement in requirements:
            arguments += ['--require', requirement]
        if kwargs.get('out'):
            arguments += ['--out', kwargs['out']]
        result = run_python(*arguments)
        return result, (json.loads(result.stdout) if result.stdout.strip()
                        else None)

    def recalc_evidence(self, run_id=None, kit_sha=KIT_SHA_FIXTURE):
        run_id = run_id or self.run_id
        paths = {}
        previous = None
        for label in ('dry-run', 'apply-1', 'apply-2'):
            out = os.path.join(self.evidence, 'recalc_%s.json' % label)
            arguments = [os.path.join(KIT_DIR, 'pilot_recalc_parse.py'),
                         '--input', self.recalc_output[label],
                         '--label', label, '--expect-day', DAY,
                         '--run-id', run_id, '--kit-sha', kit_sha,
                         '--out', out]
            if label == 'dry-run':
                arguments.append('--wrote-nothing')
            if label == 'apply-1':
                arguments += ['--seconds', '1.234']
            if previous:
                arguments += ['--compare-with', previous]
            result = run_python(*arguments)
            if result.returncode != 0:
                raise AssertionError('the recalculation summary of %s did not '
                                     'parse: %s' % (label, result.stderr))
            paths[label] = out
            previous = out
        return paths

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)


def _product_sha_is_reachable():
    try:
        common.blob_sha_at(REPO_ROOT, common.PRODUCT_SHA, 'migration_utils.py')
        return True
    except common.ProbeError:
        return False


PRODUCT_REACHABLE = _product_sha_is_reachable()


# ═══ 1. Две ревизии, а не одна ═══════════════════════════════════════════════

class TwoRevisions(unittest.TestCase):
    """Комплект не может требовать от себя ревизии, на которой его нет."""

    def test_the_kit_declares_a_product_sha_and_never_a_single_verified_sha(self):
        self.assertEqual(common.PRODUCT_SHA,
                         'c3e6a12ab95117710eeea5e05133f5cd548b698e')
        self.assertFalse(hasattr(common, 'VERIFIED_MERGE_SHA'),
                         'a single verified sha is exactly the circular '
                         'dependency this split removes')

    def test_the_kit_sha_is_never_a_constant(self):
        """Отрицательный контроль: константы KIT_SHA в комплекте быть не должно."""
        for name in PS_FILES:
            text = code_text(name)
            self.assertNotRegex(
                text, r'KitSha\s*=\s*[\'"][0-9a-f]{40}[\'"]',
                '%s hardcodes a kit revision; it must be measured' % name)
        for name in ('pilot_common.py', 'pilot_repo_check.py'):
            with open(os.path.join(KIT_DIR, name), encoding='utf-8') as handle:
                body = handle.read()
            self.assertNotRegex(body, r"KIT_SHA\s*=\s*'[0-9a-f]{40}'", name)

    def test_the_module_measures_the_kit_revision_at_the_kit_checkout(self):
        text = code_text('PilotKit.psm1')
        self.assertIn('function Get-PilotKitSha', text)
        self.assertIn('Assert-PilotWorktreeClean -Repo $KitCheckout', text)
        self.assertIn('Get-PilotHeadSha -Repo $KitCheckout', text)

    def test_the_kit_lives_in_its_own_checkout(self):
        self.assertEqual(common.KIT_ROOT, r'C:\vehicle-soft-pilot-kit')
        self.assertFalse(common.path_is_within(common.KIT_ROOT,
                                               common.STAGING_ROOT))
        self.assertFalse(common.touches_production(common.KIT_ROOT))
        # The server-side scripts locate the kit checkout from their own
        # location. The BAK-TEX11 script does not need to: it RUNS from the
        # collector checkout, which is itself at the kit revision, and it is
        # handed that revision as -KitSha.
        for name in STAGING_SCRIPTS:
            self.assertIn('$KitCheckout = Split-Path -Parent (Split-Path '
                          '-Parent $PSScriptRoot)', code_text(name), name)
        collector = code_text('BAK_TEX11_DJI_COLLECT_TO_STAGING.ps1')
        self.assertIn('[Parameter(Mandatory)][string]$ApprovedKitSha',
                      collector)
        self.assertIn("$ApprovedKitSha -notmatch '^[0-9a-f]{40}$'", collector)

    def test_no_script_demands_the_product_sha_of_the_kit_checkout(self):
        for name in PS_SCRIPTS:
            for line in code_lines(name):
                if 'Assert-PilotProductSha' in line:
                    self.assertIn('StagingRoot', line,
                                  '%s demands the product revision of '
                                  'something other than staging: %s'
                                  % (name, line.strip()))

    def test_the_collector_runs_at_the_kit_revision(self):
        """Сборщик работает НА ревизии комплекта -- и не переводит себя на неё.

        [REASON]: раньше здесь требовался `merge --ff-only`. Он и был дефектом:
        обновить код, который уже исполняется, нельзя, а проверка HEAD после
        merge ничего не доказывала о защитах, которые уже отработали.
        """
        text = code_text('BAK_TEX11_DJI_COLLECT_TO_STAGING.ps1')
        self.assertIn('$head -ne $ApprovedKitSha', text)
        self.assertNotIn("'--ff-only'", text)
        self.assertNotIn("'merge'", text)

    def test_the_readme_starts_from_a_reproducible_state(self):
        with open(os.path.join(KIT_DIR, 'README.md'), encoding='utf-8') as handle:
            text = handle.read()
        self.assertIn(common.KIT_ROOT, text)
        self.assertIn('git status --porcelain', text)
        # The bootstrap block itself is published by the reviewer WITH the
        # approved revision already substituted: a command carrying a
        # placeholder is pasted into a console placeholder and all.
        self.assertIn('ApprovedKitSha', text)
        self.assertIn('NEXT_COMMAND_STEP2', text)
        self.assertIsNone(re.search(r'<[^>\n]{1,40}>', text),
                          'a placeholder in an owner instruction is pasted '
                          'literally')


# ═══ 2. Исполняемые байты привязаны к ревизии ════════════════════════════════

class ExecutablesComeFromARevision(unittest.TestCase):

    def test_the_local_blob_hash_agrees_with_git(self):
        digest = common.blob_sha_of_bytes(b'')
        self.assertEqual(digest,
                         'e69de29bb2d1d6434b8b29ae775ad8c2e48c5391')
        payload = b'hello\n'
        self.assertEqual(
            common.blob_sha_of_bytes(payload),
            subprocess.run(['git', 'hash-object', '--stdin'], input=payload,
                           stdout=subprocess.PIPE).stdout.decode().strip())

    def test_the_manifest_matches_the_worktree(self):
        manifest = common.load_product_blobs()
        self.assertEqual(blobs.check_against_worktree(manifest, REPO_ROOT), [])

    def test_the_manifest_names_every_executable_the_kit_runs(self):
        manifest = common.load_product_blobs()
        for path in ('migrate_drones_useful_area_001.py', 'migration_utils.py',
                     'backup_transport_db.py',
                     'tools/recalculate_drone_useful_area.py'):
            self.assertIn(path, manifest['identical_on_both_revisions'], path)

    def test_the_collector_difference_is_declared_not_hidden(self):
        manifest = common.load_product_blobs()
        entry = manifest['kit_differs_on_purpose']['drone_collector/main.py']
        self.assertNotEqual(entry['product_blob'], entry['kit_blob'],
                            'the kit claims to change the collector; if the '
                            'blobs are equal the claim is stale')

    def test_a_file_outside_the_manifest_is_never_materialized(self):
        directory = tempfile.mkdtemp(prefix='pilot_mat_')
        try:
            with self.assertRaises(common.ProbeError):
                repo_check.materialize(REPO_ROOT, common.PRODUCT_SHA,
                                       directory, ['app.py'])
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    @unittest.skipUnless(PRODUCT_REACHABLE,
                         'the product revision is not in this clone')
    def test_materialized_bytes_are_the_blob_bytes(self):
        directory = tempfile.mkdtemp(prefix='pilot_mat_')
        try:
            report = repo_check.materialize(
                REPO_ROOT, common.PRODUCT_SHA, directory,
                ['migration_utils.py'])
            written = report['materialized']['migration_utils.py']
            with open(written['destination'], 'rb') as handle:
                payload = handle.read()
            self.assertEqual(common.blob_sha_of_bytes(payload),
                             written['blob'])
            self.assertEqual(
                payload,
                common.read_blob(REPO_ROOT, common.PRODUCT_SHA,
                                 'migration_utils.py'))
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    @unittest.skipUnless(PRODUCT_REACHABLE,
                         'the product revision is not in this clone')
    def test_a_wrong_expected_blob_is_refused(self):
        directory = tempfile.mkdtemp(prefix='pilot_mat_')
        try:
            with self.assertRaises(common.ProbeError):
                common.materialize_blob(
                    REPO_ROOT, common.PRODUCT_SHA, 'migration_utils.py',
                    os.path.join(directory, 'x.py'), expected_blob='f' * 40)
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def test_a_crlf_checkout_still_matches_its_blob(self):
        """Windows разворачивает переводы строк -- блоб от этого не меняется.

        [REASON]: это поймала Windows-задача CI, а не рассуждение. На
        `windows-latest` рабочая копия приезжает с CRLF (`core.autocrlf`), а
        блобы в git лежат с LF. Сырой хеш файла на диске не совпал с блобом ни
        разу -- восемь файлов из восьми, -- то есть проверка «исполняемое
        взято из ревизии» падала на той самой платформе, ради которой она
        написана. Отрицательный контроль ниже показывает разницу: сырой хеш
        РАСХОДИТСЯ, хеш глазами git -- совпадает.
        """
        directory = tempfile.mkdtemp(prefix='pilot_crlf_')
        try:
            def run(*arguments):
                subprocess.run(('git',) + arguments, cwd=directory,
                               check=True, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
            run('init', '-q')
            run('config', 'user.email', 'test@example.invalid')
            run('config', 'user.name', 'test')
            target = os.path.join(directory, 'sample.py')
            with open(target, 'wb') as handle:
                handle.write(b'one\ntwo\nthree\n')
            run('add', 'sample.py')
            run('commit', '-qm', 'lf')
            recorded = common.blob_sha_at(directory, 'HEAD', 'sample.py')

            # Как выглядит эта же рабочая копия на Windows.
            run('config', 'core.autocrlf', 'true')
            with open(target, 'wb') as handle:
                handle.write(b'one\r\ntwo\r\nthree\r\n')

            raw = common.file_blob_sha(target)
            through_git = common.worktree_blob_sha(directory, 'sample.py')
            self.assertNotEqual(raw, recorded,
                                'the CRLF trap must exist, else this test '
                                'proves nothing')
            self.assertEqual(through_git, recorded)
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def test_the_worktree_hash_is_taken_through_git_everywhere(self):
        for name in ('pilot_repo_check.py', 'pilot_blob_manifest.py'):
            with open(os.path.join(KIT_DIR, name), encoding='utf-8') as handle:
                text = handle.read()
            self.assertIn('worktree_blob_sha', text, name)
            self.assertNotIn('common.file_blob_sha(full)', text, name)

    def test_verify_checks_both_the_history_and_the_disk(self):
        """Отрицательный контроль: подменённый на диске файл обязан всплыть."""
        manifest = copy.deepcopy(common.load_product_blobs())
        problems = []
        entry = repo_check._compare_blob(REPO_ROOT, 'HEAD',
                                         'migration_utils.py', 'f' * 40,
                                         problems)
        self.assertFalse(entry['matches'])
        self.assertIsNotNone(entry['on_disk'])
        self.assertIsNotNone(entry['in_history'])
        self.assertTrue(any(item.startswith('BLOB_MISMATCH')
                            for item in problems))

    def test_the_scripts_materialize_rather_than_copy_executables(self):
        text = code_text('PREFLIGHT_AND_COPY_TEST.ps1')
        self.assertIn("'materialize', '--repo'", text)
        for line in code_lines('PREFLIGHT_AND_COPY_TEST.ps1'):
            if 'Copy-Item' in line:
                self.assertIn('$copyPath', line,
                              'the only Copy-Item left is the consistent '
                              'database snapshot: %s' % line.strip())


# ═══ 3. Windows PowerShell 5.1 ═══════════════════════════════════════════════

class WindowsPowerShell51(unittest.TestCase):

    def test_no_script_uses_utf8nobom(self):
        """`utf8NoBOM` не существует в 5.1 -- это ошибка привязки параметра."""
        for name in PS_FILES:
            self.assertNotIn('utf8NoBOM', code_text(name), name)

    def test_the_ban_on_utf8nobom_is_explained_where_it_will_be_read(self):
        """Запрет объявлен там, где следующий читатель его увидит."""
        self.assertIn('utf8NoBOM', script_text('PilotKit.psm1'))
        self.assertIn('5.1', script_text('PilotKit.psm1'))

    def test_no_script_redirects_a_json_document_with_an_arrow(self):
        """`>` в 5.1 пишет UTF-16LE; JSON, снятый так, не читается никем."""
        pattern = re.compile(r'>\s*\$\w*(json|Path|Out|out|evidence)\w*',
                             re.IGNORECASE)
        for name in PS_FILES:
            for line in code_lines(name):
                if '2>&1' in line or '*>' in line:
                    self.assertNotIn('.json', line, '%s: %s' % (name, line))
                self.assertIsNone(pattern.search(line),
                                  '%s redirects into a json path: %s'
                                  % (name, line.strip()))

    def test_json_is_written_through_dotnet_without_a_bom(self):
        text = code_text('PilotKit.psm1')
        self.assertIn('New-Object System.Text.UTF8Encoding($false)', text)
        self.assertIn('[System.IO.File]::WriteAllText', text)
        self.assertIn('[System.IO.File]::ReadAllText', text)

    def test_the_probes_are_given_their_own_out_flag(self):
        text = code_text('PilotKit.psm1')
        self.assertIn("'--out', $OutFile", text)

    def test_python_writes_evidence_as_ascii_without_a_bom(self):
        directory = tempfile.mkdtemp(prefix='pilot_json_')
        try:
            path = os.path.join(directory, 'e.json')
            common.write_evidence(path, {'a': 1, 'b': 'x'})
            with open(path, 'rb') as handle:
                payload = handle.read()
            self.assertFalse(payload.startswith(b'\xef\xbb\xbf'))
            self.assertFalse(payload.startswith(b'\xff\xfe'))
            self.assertEqual(payload.decode('ascii'),
                             payload.decode('utf-8'))
            self.assertEqual(common.read_evidence(path)['a'], 1)
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def test_a_bom_on_input_is_tolerated_on_read(self):
        directory = tempfile.mkdtemp(prefix='pilot_json_')
        try:
            path = os.path.join(directory, 'bom.json')
            with open(path, 'wb') as handle:
                handle.write(b'\xef\xbb\xbf{"a": 1}')
            self.assertEqual(common.read_evidence(path)['a'], 1)
        finally:
            shutil.rmtree(directory, ignore_errors=True)


# ═══ 4. Каталог запуска BAK-TEX11 вне чекаута ════════════════════════════════

class CollectorWorkRoot(unittest.TestCase):

    def test_the_default_runs_root_is_outside_the_collector_checkout(self):
        self.assertEqual(common.COLLECTOR_RUNS_ROOT,
                         r'C:\vehicle-soft-pilot-runs')
        self.assertFalse(common.path_is_within(common.COLLECTOR_RUNS_ROOT,
                                               common.COLLECTOR_REPO))
        text = code_text('BAK_TEX11_DJI_COLLECT_TO_STAGING.ps1')
        self.assertIn("[string]$RunsRoot = 'C:\\vehicle-soft-pilot-runs'", text)
        self.assertNotIn('VehicleSoft_DJI_StageB_Pilot\\pilot_evidence', text)

    def test_the_clean_check_happens_before_any_directory_is_created(self):
        """Порядок: сначала чистота, потом наши файлы. Не наоборот.

        [REASON]: первая редакция создавала каталог улик внутри чекаута и
        только потом спрашивала, чисто ли дерево, -- то есть проверка падала
        бы из-за артефактов самого прогона.
        """
        text = code_text('BAK_TEX11_DJI_COLLECT_TO_STAGING.ps1')
        clean = text.index('Assert-PilotWorktreeClean -Repo $K.CollectorRepo')
        first_new_item = text.index('New-Item -ItemType Directory')
        self.assertLess(clean, first_new_item,
                        'the run creates a directory before it checks that '
                        'the working tree is clean')
        self.assertLess(text.index('Assert-PilotOutsideCheckouts'),
                        first_new_item)

    def test_the_guard_refuses_every_checkout(self):
        for root in (common.COLLECTOR_REPO, common.STAGING_ROOT,
                     common.KIT_ROOT, common.PRODUCTION_ROOT):
            inside = os.path.join(root, 'runs')
            self.assertTrue(
                common.path_is_within(inside, root),
                'the fixture must actually be inside %s' % root)

    def test_the_server_runs_root_is_outside_every_checkout(self):
        for root in (common.STAGING_ROOT, common.KIT_ROOT,
                     common.PRODUCTION_ROOT, common.COLLECTOR_REPO):
            self.assertFalse(
                common.path_is_within(common.SERVER_RUNS_ROOT, root),
                'the server runs root must not live inside %s' % root)


# ═══ 5. Ворота по улике сбора ════════════════════════════════════════════════

class CollectGate(unittest.TestCase):
    """Пересчёт не запускается по неполному захвату."""

    def setUp(self):
        self.run_id = common.new_run_id()
        self.deploy = envelope('deploy', {'phase': 'done'}, self.run_id,
                               when=stamp(-60))
        self.collect = healthy_collect(self.run_id, when=stamp())

    def test_a_healthy_capture_opens_the_gate(self):
        self.assertEqual(gate.evaluate(self.collect, self.deploy, self.run_id,
                                       KIT_SHA_FIXTURE, DAY), [])

    def refuses(self, code, **overrides):
        collect = healthy_collect(self.run_id, when=stamp(), **overrides)
        reasons = gate.evaluate(collect, self.deploy, self.run_id,
                                KIT_SHA_FIXTURE, DAY)
        self.assertIn(code, reasons)

    def test_a_non_zero_collector_exit_closes_the_gate(self):
        self.refuses('COLLECTOR_EXIT_NOT_ZERO', exit=17)

    def test_an_unconfirmed_collection_closes_the_gate(self):
        self.refuses('COLLECTION_NOT_CONFIRMED', collect_live_confirmed=False)

    def test_an_unanswered_operator_closes_the_gate(self):
        self.refuses('OPERATOR_DID_NOT_ANSWER', probe_operator_answered=False)

    def test_an_incomplete_drain_closes_the_gate(self):
        self.refuses('DRAIN_DID_NOT_COMPLETE', probe_drained=False)

    def test_observation_capture_and_decode_errors_close_the_gate(self):
        self.refuses('OBSERVATION_ERRORS', probe_errors=1)
        self.refuses('CAPTURE_ERRORS', collect_capture_errors=1)
        self.refuses('DECODE_FAILURES', collect_decode_failures=1)

    def test_a_response_over_the_cap_closes_the_gate(self):
        self.refuses('RESPONSE_DROPPED_BY_THE_SIZE_CAP',
                     probe_skipped_over_cap=1)

    def test_a_failed_route_request_closes_the_gate(self):
        """Своё число, а не вывод из равенства observations и confirmed."""
        collect = healthy_collect(self.run_id, when=stamp(),
                                  probe_request_failures=1)
        counters = collect['payload']['counters']
        self.assertEqual(counters['probe_observations'],
                         counters['probe_confirmed'],
                         'the equality must still hold, else this proves '
                         'nothing about the new counter')
        self.assertIn('ROUTE_REQUESTS_FAILED',
                      gate.evaluate(collect, self.deploy, self.run_id,
                                    KIT_SHA_FIXTURE, DAY))

    def test_a_pending_route_request_closes_the_gate(self):
        collect = healthy_collect(self.run_id, when=stamp(),
                                  probe_pending_requests=2)
        counters = collect['payload']['counters']
        self.assertEqual(counters['probe_observations'],
                         counters['probe_confirmed'])
        self.assertIn('ROUTE_REQUESTS_STILL_PENDING',
                      gate.evaluate(collect, self.deploy, self.run_id,
                                    KIT_SHA_FIXTURE, DAY))

    def test_an_unconfirmed_observation_closes_the_gate(self):
        self.refuses('NOT_EVERY_OBSERVATION_CONFIRMED', probe_confirmed=1)

    def test_a_partially_accepted_batch_closes_the_gate(self):
        self.refuses('BATCH_NOT_FULLY_ACCEPTED', collect_batch_accepted=False)
        self.refuses('ENVELOPES_LEFT_PENDING', collect_left_pending=2)
        self.refuses('INGEST_REPORTED_ERRORS', collect_errors=1)
        self.refuses('INGEST_REPORTED_UNLINKED_ROUTES', collect_unlinked=1)

    def test_counters_that_do_not_balance_close_the_gate(self):
        self.refuses('INGEST_COUNTERS_DO_NOT_BALANCE', collect_seen=9)

    def test_a_wrong_day_closes_the_gate(self):
        collect = healthy_collect(self.run_id, when=stamp())
        collect['target_day'] = '2026-06-06'
        self.assertIn('COLLECT_ENVELOPE_TARGET_DAY_MISMATCH',
                      gate.evaluate(collect, self.deploy, self.run_id,
                                    KIT_SHA_FIXTURE, DAY))

    def test_another_run_closes_the_gate(self):
        collect = healthy_collect(common.new_run_id(), when=stamp())
        self.assertIn('COLLECT_ENVELOPE_RUN_ID_MISMATCH',
                      gate.evaluate(collect, self.deploy, self.run_id,
                                    KIT_SHA_FIXTURE, DAY))

    def test_another_kit_revision_closes_the_gate(self):
        collect = healthy_collect(self.run_id, when=stamp())
        collect['kit_sha'] = 'c' * 40
        self.assertIn('COLLECT_ENVELOPE_KIT_SHA_MISMATCH',
                      gate.evaluate(collect, self.deploy, self.run_id,
                                    KIT_SHA_FIXTURE, DAY))

    def test_another_product_revision_closes_the_gate(self):
        collect = healthy_collect(self.run_id, when=stamp())
        collect['product_sha'] = 'd' * 40
        self.assertIn('COLLECT_ENVELOPE_PRODUCT_SHA_MISMATCH',
                      gate.evaluate(collect, self.deploy, self.run_id,
                                    KIT_SHA_FIXTURE, DAY))

    def test_a_collection_older_than_the_deploy_closes_the_gate(self):
        collect = healthy_collect(self.run_id, when=stamp(-600))
        reasons = gate.evaluate(collect, self.deploy, self.run_id,
                                KIT_SHA_FIXTURE, DAY)
        self.assertTrue(any(reason.startswith('ORDER_OUT_OF_ORDER')
                            for reason in reasons), reasons)

    def test_the_gate_runs_before_the_dry_run_and_before_apply(self):
        text = code_text('STAGING_RECALCULATE_AND_VERIFY.ps1')
        gate_index = text.index('COLLECT_GATE=PASS')
        self.assertLess(gate_index, text.index("'--dry-run'"))
        self.assertLess(gate_index, text.index("'--apply'"))
        self.assertIn('if ($gateCode -ne 0) {', text)
        self.assertIn('NOTHING was recalculated and NOTHING was written',
                      text)

    def test_a_missing_collect_evidence_stops_the_recalculation(self):
        text = code_text('STAGING_RECALCULATE_AND_VERIFY.ps1')
        self.assertIn('if (-not (Test-Path -LiteralPath $collectPath)) {',
                      text)
        self.assertLess(text.index('$collectPath'), text.index("'--dry-run'"))

    def test_the_gate_tool_exits_non_zero_when_it_refuses(self):
        directory = tempfile.mkdtemp(prefix='pilot_gate_')
        try:
            collect_path = os.path.join(directory, 'collect.json')
            deploy_path = os.path.join(directory, 'deploy.json')
            common.write_evidence(collect_path, healthy_collect(
                self.run_id, when=stamp(), collect_batch_accepted=False))
            common.write_evidence(deploy_path, self.deploy)
            result = run_python(
                os.path.join(KIT_DIR, 'pilot_collect_gate.py'),
                '--collect', collect_path, '--deploy', deploy_path,
                '--run-id', self.run_id, '--kit-sha', KIT_SHA_FIXTURE,
                '--day', DAY)
            self.assertEqual(result.returncode, common.EXIT_CHECK_FAILED)
            self.assertIn('BATCH_NOT_FULLY_ACCEPTED', result.stderr)
        finally:
            shutil.rmtree(directory, ignore_errors=True)


# ═══ 6. Два новых числа сборщика ═════════════════════════════════════════════

class CollectorReportsFailuresAndPending(unittest.TestCase):

    def test_the_collector_summary_carries_both_numbers(self):
        from drone_collector.main import COLLECT_SUMMARY_KEYS
        self.assertIn('probe_request_failures', COLLECT_SUMMARY_KEYS)
        self.assertIn('probe_pending_requests', COLLECT_SUMMARY_KEYS)

    def test_the_kit_allowlist_matches_the_collector(self):
        from drone_collector.main import COLLECT_SUMMARY_KEYS
        self.assertEqual(tuple(collect_check.COLLECT_KEYS),
                         tuple(COLLECT_SUMMARY_KEYS))

    def test_the_collect_run_records_them_from_the_capture(self):
        import inspect
        from drone_collector import main as collector_main
        source = inspect.getsource(collector_main._run_route_ui_collect)
        self.assertIn(
            "state['probe_request_failures'] = capture.route_requests_failed",
            source)
        self.assertIn(
            "state['probe_pending_requests'] = capture.pending_route_requests",
            source)

    def test_the_verdict_reads_the_numbers_rather_than_inferring_them(self):
        counters = dict(HEALTHY_COUNTERS)
        counters['probe_request_failures'] = 3
        verdict = collect_check.collect_verdict(counters)
        self.assertFalse(verdict['no_unfinished_route_requests'])
        self.assertIn('ROUTE_REQUESTS_FAILED', verdict['reasons'])
        self.assertEqual(verdict['probe_request_failures'], 3)

    def test_no_identifier_travels_with_the_numbers(self):
        counters = dict(HEALTHY_COUNTERS)
        text = json.dumps(collect_check.collect_verdict(counters))
        self.assertNotIn('900001', text)
        self.assertIsNone(re.search(r'\b\d{9,}\b', text))


# ═══ 7. Полный отпечаток таблицы расчёта ═════════════════════════════════════

class CoverageFingerprint(unittest.TestCase):
    """Число строк не меняется, когда строку переписали."""

    @classmethod
    def setUpClass(cls):
        cls.site = SyntheticSite()

    @classmethod
    def tearDownClass(cls):
        cls.site.close()

    def fingerprint(self):
        con, _mode = common.connect_readonly(self.site.db)
        try:
            return common.coverage_fingerprint(con)
        finally:
            con.close()

    def rows(self):
        con = sqlite3.connect(self.site.db)
        try:
            return con.execute(
                'SELECT count(*) FROM drone_coverage_works').fetchone()[0]
        finally:
            con.close()

    def write(self, sql, args=()):
        con = sqlite3.connect(self.site.db)
        con.execute(sql, args)
        con.commit()
        con.close()

    def test_an_untouched_table_keeps_its_digest(self):
        self.assertEqual(self.fingerprint()['sha256'],
                         self.fingerprint()['sha256'])

    def test_a_rewritten_row_is_caught_at_an_identical_row_count(self):
        base = self.fingerprint()
        rows_before = self.rows()
        self.write('UPDATE drone_coverage_works '
                   '   SET estimated_useful_area_ha = '
                   '       estimated_useful_area_ha + 0.0001')
        try:
            after = self.fingerprint()
            self.assertEqual(self.rows(), rows_before,
                             'the row count must NOT change, else this test '
                             'proves nothing about the fingerprint')
            self.assertNotEqual(after['sha256'], base['sha256'])
        finally:
            self.write('UPDATE drone_coverage_works '
                       '   SET estimated_useful_area_ha = '
                       '       estimated_useful_area_ha - 0.0001')
        self.assertEqual(self.fingerprint()['sha256'], base['sha256'])

    def test_a_changed_status_is_caught_at_an_identical_row_count(self):
        base = self.fingerprint()
        self.write("UPDATE drone_coverage_works "
                   "   SET quality_status = 'PARTIAL_DATA'")
        try:
            self.assertNotEqual(self.fingerprint()['sha256'], base['sha256'])
        finally:
            self.write("UPDATE drone_coverage_works "
                       "   SET quality_status = 'READY_ESTIMATE'")

    def test_every_column_is_in_the_digest(self):
        con, _mode = common.connect_readonly(self.site.db)
        try:
            columns = common.table_columns(con, 'drone_coverage_works')
        finally:
            con.close()
        self.assertEqual(self.fingerprint()['columns'], len(columns))
        self.assertEqual(self.fingerprint()['excluded_columns'], [])

    def test_the_probe_requirement_compares_the_fingerprint(self):
        _result, document = self.site.snapshot()
        digest = document['payload']['coverage_fingerprint']['sha256']
        result, _document = self.site.snapshot('coverage-sha256=%s' % digest)
        self.assertEqual(result.returncode, common.EXIT_OK)
        result, _document = self.site.snapshot('coverage-sha256=%s' % ('f' * 64))
        self.assertEqual(result.returncode, common.EXIT_CHECK_FAILED)

    def test_the_script_proves_the_dry_run_with_the_fingerprint(self):
        text = code_text('STAGING_RECALCULATE_AND_VERIFY.ps1')
        self.assertIn('"coverage-sha256=" + $coverageBefore', text)
        self.assertIn('$afterDry.payload.coverage_fingerprint.sha256 -eq '
                      '$coverageBefore', text)
        self.assertIn('DRY_RUN_WROTE_NOTHING=$dryWroteNothing', text)
        self.assertNotIn('$afterDry.payload.coverage.works -ne', text)


class DryRunWritesNothing(unittest.TestCase):

    def test_a_dry_run_leaves_the_table_byte_identical(self):
        site = SyntheticSite()
        try:
            # [REASON]: the fixture already applied three times, so a fourth
            # --apply would be a no-op and the negative control below would
            # pass for the wrong reason. The table is emptied first, which
            # makes the apply actually write.
            con = sqlite3.connect(site.db)
            con.execute('DELETE FROM drone_coverage_works')
            con.commit()
            con.close()
            con, _mode = common.connect_readonly(site.db)
            try:
                before = common.coverage_fingerprint(con)
            finally:
                con.close()
            result = run_python(
                os.path.join(REPO_ROOT, 'tools',
                             'recalculate_drone_useful_area.py'),
                '--from', DAY, '--to', DAY, '--dry-run', '--db', site.db)
            self.assertEqual(result.returncode, 0, result.stderr)
            con, _mode = common.connect_readonly(site.db)
            try:
                after = common.coverage_fingerprint(con)
            finally:
                con.close()
            self.assertEqual(after['sha256'], before['sha256'])

            # Отрицательный контроль: --apply на тех же данных МЕНЯЕТ отпечаток.
            self.assertEqual(run_python(
                os.path.join(REPO_ROOT, 'tools',
                             'recalculate_drone_useful_area.py'),
                '--from', DAY, '--to', DAY, '--apply', '--db', site.db,
            ).returncode, 0)
            con, _mode = common.connect_readonly(site.db)
            try:
                applied = common.coverage_fingerprint(con)
            finally:
                con.close()
            self.assertNotEqual(applied['sha256'], before['sha256'])
        finally:
            site.close()


# ═══ 8. Один запуск, один манифест ═══════════════════════════════════════════

class OneRunOneManifest(unittest.TestCase):

    def test_a_run_id_has_a_shape_and_is_checked(self):
        run_id = common.new_run_id()
        self.assertTrue(common.is_run_id(run_id))
        for bad in ('', 'yesterday', '20260902-abc', None):
            self.assertFalse(common.is_run_id(bad), repr(bad))

    def test_two_run_ids_differ(self):
        self.assertNotEqual(common.new_run_id(), common.new_run_id())

    def test_every_step_after_the_first_demands_a_run_id(self):
        for name in RUN_STEP_SCRIPTS:
            self.assertIn('[Parameter(Mandatory)][string]$RunId',
                          code_text(name), name)

    def test_the_first_step_creates_the_run_and_prints_it(self):
        text = code_text('PREFLIGHT_AND_COPY_TEST.ps1')
        self.assertIn('New-PilotRun', text)
        self.assertIn('Write-Output "RUN_ID=$RunId"', text)

    def test_no_script_searches_for_the_newest_evidence(self):
        """Поиск «самого свежего» -- это способ смешать два прогона."""
        for name in PS_SCRIPTS:
            text = code_text(name)
            self.assertNotIn('Sort-Object Name -Descending', text, name)
            self.assertNotIn('Find-Newest', text, name)
            lines = code_lines(name)
            for index, line in enumerate(lines):
                if 'Sort-Object LastWriteTime -Descending' not in line:
                    continue
                # The statement, not the line: the pipeline that picks the
                # freshly written backup spans several lines.
                window = '\n'.join(lines[max(0, index - 3):index + 1])
                self.assertTrue(
                    "Filter '*.db'" in window,
                    '%s sorts something other than the database backups by '
                    'time: %s' % (name, window.strip()))

    def test_evidence_paths_are_fixed_names_in_the_run_directory(self):
        text = code_text('PilotKit.psm1')
        self.assertIn('function Get-PilotEvidencePath', text)
        self.assertIn('ValidateSet', text)
        for name in ('STAGING_PILOT_REPORT.ps1',
                     'STAGING_RECALCULATE_AND_VERIFY.ps1'):
            self.assertIn('Get-PilotEvidencePath', code_text(name), name)

    def test_the_report_takes_no_hand_typed_path(self):
        text = code_text('STAGING_PILOT_REPORT.ps1')
        self.assertNotIn('CollectEvidence', text)
        self.assertIn('Get-PilotEvidencePath -RunsRoot $RunsRoot -RunId '
                      '$RunId -Name \'collect\'', text)

    def test_the_owner_instructions_contain_no_path_placeholder(self):
        with open(os.path.join(KIT_DIR, 'README.md'), encoding='utf-8') as handle:
            text = handle.read()
        self.assertNotIn('<path>', text)
        self.assertNotIn('<путь>', text)
        self.assertIsNone(re.search(r'<[^>\n]{1,40}>', text),
                          'a placeholder in an owner instruction is pasted '
                          'literally')

    def test_the_collector_names_the_exact_destination_path(self):
        text = code_text('BAK_TEX11_DJI_COLLECT_TO_STAGING.ps1')
        self.assertIn('runs\\$RunId\\evidence\\collect.json', text)


# ═══ 9. Конверты улик ════════════════════════════════════════════════════════

class EvidenceEnvelopes(unittest.TestCase):

    def setUp(self):
        self.run_id = common.new_run_id()

    def test_a_correct_envelope_validates(self):
        document = envelope('deploy', {}, self.run_id)
        self.assertEqual(
            common.validate_envelope(document, 'deploy', self.run_id,
                                     KIT_SHA_FIXTURE), [])

    def test_each_field_is_checked(self):
        cases = {
            'kit': ('WRONG_KIT', 'SOMETHING-ELSE'),
            'kit_version': ('WRONG_KIT_VERSION', '99'),
            'evidence_kind': ('WRONG_EVIDENCE_KIND', 'other'),
            'run_id': ('MALFORMED_RUN_ID', 'nonsense'),
            'kit_sha': ('KIT_SHA_MISMATCH', 'a' * 40),
            'product_sha': ('PRODUCT_SHA_MISMATCH', 'a' * 40),
            'target_day': ('TARGET_DAY_MISMATCH', '2026-06-06'),
            'generated_utc': ('MALFORMED_TIMESTAMP', 'yesterday'),
        }
        for field, (code, value) in cases.items():
            document = envelope('deploy', {}, self.run_id)
            document[field] = value
            problems = common.validate_envelope(document, 'deploy',
                                                self.run_id, KIT_SHA_FIXTURE)
            self.assertIn(code, problems, field)

    def test_a_missing_field_is_named(self):
        document = envelope('deploy', {}, self.run_id)
        del document['product_sha']
        problems = common.validate_envelope(document, 'deploy', self.run_id,
                                            KIT_SHA_FIXTURE)
        self.assertIn('MISSING_FIELD:product_sha', problems)

    def test_another_runs_envelope_is_caught(self):
        document = envelope('deploy', {}, common.new_run_id())
        self.assertIn('RUN_ID_MISMATCH',
                      common.validate_envelope(document, 'deploy',
                                               self.run_id, KIT_SHA_FIXTURE))

    def test_time_order_is_checked(self):
        first = envelope('deploy', {}, self.run_id, when=stamp())
        second = envelope('collect:summary', {}, self.run_id, when=stamp(-60))
        self.assertEqual(common.check_time_order([('deploy', first),
                                                  ('collect', second)]),
                         ['OUT_OF_ORDER:collect_BEFORE_deploy'])
        self.assertEqual(common.check_time_order([('deploy', second),
                                                  ('collect', first)]), [])

    def test_an_envelope_cannot_be_built_without_a_run_and_a_measured_sha(self):
        with self.assertRaises(common.ProbeError):
            common.evidence_envelope('x', {}, 'not-a-run', KIT_SHA_FIXTURE)
        with self.assertRaises(common.ProbeError):
            common.evidence_envelope('x', {}, common.new_run_id(), 'short')

    def test_every_probe_demands_the_run_identity(self):
        for name in ('pilot_db_probe.py', 'pilot_recalc_parse.py',
                     'pilot_collect_check.py', 'pilot_collect_gate.py',
                     'pilot_repo_check.py', 'pilot_report.py'):
            with open(os.path.join(KIT_DIR, name), encoding='utf-8') as handle:
                text = handle.read()
            self.assertIn("'--run-id', required=True", text, name)
            self.assertIn("'--kit-sha', required=True", text, name)


# ═══ 10. Вердикт: обязательные условия и решение владельца ═══════════════════

class Verdict(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        shared = shared_evidence()
        cls.site = shared['site']
        cls.run_id = shared['run_id']
        cls.runs = shared['runs']
        cls.snapshot = shared['snapshot']
        cls.preflight = shared['preflight']
        cls.deploy = shared['deploy']
        cls.collect = shared['collect']

    def build(self, preflight=None, deploy=None, collect=None, runs=None,
              snapshot=None, owner_threshold=None, dji_limit=None,
              run_id=None):
        return report_mod.build_report(
            preflight or copy.deepcopy(self.preflight),
            deploy or copy.deepcopy(self.deploy),
            collect or copy.deepcopy(self.collect),
            runs if runs is not None else copy.deepcopy(self.runs),
            snapshot or copy.deepcopy(self.snapshot),
            owner_threshold, dji_limit, [],
            run_id=run_id or self.run_id, kit_sha=KIT_SHA_FIXTURE)

    def test_healthy_evidence_without_owner_rules_is_technical_go(self):
        report, _markdown = self.build()
        failed = [item['code'] for item in report['conditions']
                  if not item['passed']]
        self.assertEqual(failed, [])
        self.assertEqual(report['envelope_problems'], [])
        self.assertEqual(report['verdict'], 'TECHNICAL_GO')
        self.assertIn('BUSINESS_DECISION_REQUIRED', report['verdict_reasons'])
        self.assertIn('PRODUCTION_ROLLOUT_NOT_AUTHORISED',
                      report['verdict_reasons'])
        self.assertFalse(report['production_rollout_authorised'])
        self.assertTrue(report['business_decision_required'])

    def test_go_requires_both_owner_rules(self):
        report, _markdown = self.build(owner_threshold=0.9)
        self.assertEqual(report['verdict'], 'TECHNICAL_GO')
        self.assertIn('OWNER_DJI_DELTA_RULE_NOT_SET',
                      report['verdict_reasons'])
        report, _markdown = self.build(owner_threshold=0.9, dji_limit=999.0)
        self.assertEqual(report['verdict'], 'GO')
        # Even GO does not authorise production: this kit runs a staging
        # pilot, and lifting the release gate is the owner's separate act.
        self.assertFalse(report['production_rollout_authorised'])
        self.assertTrue(report['release_gate_action_required'])

    def test_the_kit_carries_no_default_threshold(self):
        with open(os.path.join(KIT_DIR, 'pilot_report.py'),
                  encoding='utf-8') as handle:
            text = handle.read()
        self.assertNotIn('default=0.20', text)
        self.assertNotIn('adjust_share_threshold', text)
        for flag in ('--owner-share-threshold', '--owner-dji-delta-percent'):
            found = re.search(
                re.escape("'%s'" % flag) + r"[^)]*?default=None", text,
                re.DOTALL)
            self.assertIsNotNone(found, '%s must have no default' % flag)
        script = code_text('STAGING_PILOT_REPORT.ps1')
        # Никакого значения-признака: решает ПРИСУТСТВИЕ параметра. Иначе
        # явно переданный -0.01 молча превращался в «правило не задано».
        self.assertIn('[double]$OwnerShareThreshold,', script)
        self.assertNotIn('$OwnerShareThreshold = -1', script)
        self.assertIn("$PSBoundParameters.ContainsKey('OwnerShareThreshold')",
                      script)

    def assert_rejected(self, code, **kwargs):
        report, _markdown = self.build(**kwargs)
        self.assertEqual(report['verdict'], 'REJECT',
                         'a broken %s still produced %s'
                         % (code, report['verdict']))
        self.assertIn(code, report['verdict_reasons'])
        self.assertFalse(report['production_rollout_authorised'])

    # -- the deploy gate of item 10 --------------------------------------
    def test_a_manifest_that_is_not_done_blocks_go(self):
        broken = copy.deepcopy(self.deploy)
        broken['payload']['phase'] = 'code-updated'
        self.assert_rejected('DEPLOY_MANIFEST_IS_DONE', deploy=broken)

    def test_a_smoke_test_failed_manifest_blocks_go(self):
        broken = copy.deepcopy(self.deploy)
        broken['payload']['phase'] = 'smoke-test-failed'
        broken['payload']['smoke_test_ok'] = False
        report, _markdown = self.build(deploy=broken)
        self.assertEqual(report['verdict'], 'REJECT')
        self.assertIn('DEPLOY_MANIFEST_IS_DONE', report['verdict_reasons'])
        self.assertIn('SMOKE_TEST_OK', report['verdict_reasons'])

    def test_a_smoke_test_that_did_not_pass_blocks_go_even_when_done(self):
        broken = copy.deepcopy(self.deploy)
        broken['payload']['smoke_test_ok'] = False
        self.assert_rejected('SMOKE_TEST_OK', deploy=broken)

    def test_an_unverified_backup_blocks_go(self):
        broken = copy.deepcopy(self.deploy)
        broken['payload']['backup_verified'] = False
        self.assert_rejected('BACKUP_CREATED_AND_VERIFIED', deploy=broken)

    def test_a_missing_backup_blocks_go(self):
        broken = copy.deepcopy(self.deploy)
        broken['payload']['backup_path'] = ''
        self.assert_rejected('BACKUP_CREATED_AND_VERIFIED', deploy=broken)

    def test_a_service_that_did_not_start_blocks_go(self):
        broken = copy.deepcopy(self.deploy)
        broken['payload']['service_started'] = False
        self.assert_rejected('STAGING_SERVICE_STARTED', deploy=broken)

    def test_a_dry_run_that_was_not_proven_blocks_go(self):
        broken = copy.deepcopy(self.runs)
        broken[0]['payload']['wrote_nothing'] = False
        self.assert_rejected('DRY_RUN_WROTE_NOTHING', runs=broken)

    # -- evidence identity of item 9 --------------------------------------
    def test_evidence_from_two_runs_is_rejected(self):
        other = healthy_collect(common.new_run_id(), when=stamp(-180))
        report, _markdown = self.build(collect=other)
        self.assertEqual(report['verdict'], 'REJECT')
        self.assertIn('EVIDENCE_DOES_NOT_BELONG_TO_ONE_RUN',
                      report['verdict_reasons'])
        self.assertTrue(any('RUN_ID_MISMATCH' in problem
                            for problem in report['envelope_problems']))

    def test_evidence_from_another_kit_revision_is_rejected(self):
        other = copy.deepcopy(self.collect)
        other['kit_sha'] = 'e' * 40
        report, _markdown = self.build(collect=other)
        self.assertEqual(report['verdict'], 'REJECT')
        self.assertTrue(any('KIT_SHA_MISMATCH' in problem
                            for problem in report['envelope_problems']))

    def test_evidence_out_of_order_is_rejected(self):
        late = copy.deepcopy(self.preflight)
        late['generated_utc'] = stamp(600)
        report, _markdown = self.build(preflight=late)
        self.assertEqual(report['verdict'], 'REJECT')
        self.assertTrue(report['evidence_order_problems'])

    def test_a_wrong_evidence_kind_is_rejected(self):
        wrong = copy.deepcopy(self.collect)
        wrong['evidence_kind'] = 'db-probe:snapshot'
        report, _markdown = self.build(collect=wrong)
        self.assertEqual(report['verdict'], 'REJECT')
        self.assertTrue(any('WRONG_EVIDENCE_KIND' in problem
                            for problem in report['envelope_problems']))

    def test_the_report_records_measured_revisions_not_constants(self):
        report, _markdown = self.build()
        self.assertEqual(report['kit_sha'], KIT_SHA_FIXTURE)
        self.assertEqual(report['run_id'], self.run_id)
        self.assertEqual(report['product_sha'], common.PRODUCT_SHA)
        self.assertNotIn('verified_sha', report)

    # -- the conditions carried over from the first edition ---------------
    def test_a_failed_migration_on_the_copy_blocks_go(self):
        broken = copy.deepcopy(self.preflight)
        broken['payload']['migration_on_copy_ok'] = False
        self.assert_rejected('MIGRATION_ON_COPY_OK', preflight=broken)

    def test_a_failed_migration_on_staging_blocks_go(self):
        broken = copy.deepcopy(self.deploy)
        broken['payload']['migration_on_staging_ok'] = False
        self.assert_rejected('MIGRATION_ON_STAGING_OK', deploy=broken)

    def test_a_failed_integrity_check_blocks_go(self):
        broken = copy.deepcopy(self.snapshot)
        broken['payload']['integrity']['integrity_ok'] = False
        self.assert_rejected('INTEGRITY_OK', snapshot=broken)

    def test_a_changed_area_ha_blocks_go(self):
        broken = copy.deepcopy(self.deploy)
        broken['payload']['area_ha_after'] = dict(
            broken['payload']['area_ha_after'])
        broken['payload']['area_ha_after']['sha256'] = 'f' * 64
        self.assert_rejected('AREA_HA_UNCHANGED', deploy=broken)

    def test_a_decode_failure_blocks_go(self):
        broken = healthy_collect(self.run_id, when=stamp(-180),
                                 collect_decode_failures=1)
        self.assert_rejected('LIVE_ROUTE_DECODE_OK', collect=broken)

    def test_a_failed_route_request_blocks_go(self):
        broken = copy.deepcopy(self.collect)
        broken['payload']['no_unfinished_route_requests'] = False
        self.assert_rejected('NO_UNFINISHED_ROUTE_REQUESTS', collect=broken)

    def test_a_partially_accepted_batch_blocks_go(self):
        broken = healthy_collect(self.run_id, when=stamp(-180),
                                 collect_batch_accepted=False)
        self.assert_rejected('BATCH_FULLY_ACCEPTED', collect=broken)

    def test_a_second_apply_that_wrote_blocks_go(self):
        broken = copy.deepcopy(self.runs)
        broken[2]['payload']['summary']['updated'] = 1
        self.assert_rejected('SECOND_APPLY_IDEMPOTENT', runs=broken)

    def test_a_corrupted_route_blocks_go(self):
        broken = copy.deepcopy(self.snapshot)
        broken['payload']['coverage']['by_status']['ROUTE_INVALID'] = 1
        self.assert_rejected('NO_ROUTE_INVALID', snapshot=broken)

    def test_a_non_ready_work_carrying_a_number_blocks_go(self):
        broken = copy.deepcopy(self.snapshot)
        broken['payload']['coverage']['only_ready_carries_a_number'] = False
        self.assert_rejected('ONLY_READY_IN_TOTAL', snapshot=broken)

    def test_a_day_that_produced_no_work_blocks_go(self):
        broken = copy.deepcopy(self.snapshot)
        broken['payload']['coverage']['works'] = 0
        self.assert_rejected('WORKS_WERE_PRODUCED', snapshot=broken)

    def test_the_three_unrecorded_fields_are_null_and_named(self):
        report, markdown = self.build()
        for field in ('fully_idle_flights_excluded', 'mixed_flights',
                      'idle_segments'):
            self.assertIsNone(report[field], field)
        self.assertIn('NOT_RECORDED_BY_SCHEMA', markdown)

    def test_the_markdown_says_production_is_not_authorised(self):
        _report, markdown = self.build()
        self.assertIn('TECHNICAL_GO', markdown)
        self.assertIn('production', markdown.lower())


# ═══ 11. Smoke-тест ══════════════════════════════════════════════════════════

class SmokeTestSemantics(unittest.TestCase):
    """404, 401 и 403 успехом не считаются; redirect -- только внутрь площадки."""

    def test_the_endpoint_is_a_named_one_with_an_exact_status(self):
        self.assertEqual(common.SMOKE_PATH, '/login')
        self.assertEqual(common.SMOKE_ALLOWED_STATUS, (200,))

    def test_the_statuses_a_broken_service_returns_are_not_success(self):
        for status in (401, 403, 404, 500, 502, 503):
            self.assertNotIn(status, common.SMOKE_ALLOWED_STATUS, status)

    def test_a_relative_redirect_stays_inside(self):
        self.assertTrue(common.redirect_stays_in_staging('/login'))
        self.assertTrue(common.redirect_stays_in_staging('login'))

    def test_a_redirect_to_production_is_refused(self):
        self.assertFalse(common.redirect_stays_in_staging(
            common.PRODUCTION_URL + '/login'))

    def test_a_redirect_to_a_foreign_host_is_refused(self):
        for location in ('http://evil.example/login',
                         'https://10.103.25.99:5051/login',
                         'http://10.103.25.14:5052/login'):
            self.assertFalse(common.redirect_stays_in_staging(location),
                             location)

    def test_a_userinfo_trick_does_not_pass_as_staging(self):
        """`http://<staging>@evil.example/` -- чужой хост, а не площадка."""
        self.assertFalse(common.redirect_stays_in_staging(
            'http://10.103.25.14:5051@evil.example/x'))

    def test_an_empty_location_is_refused(self):
        self.assertFalse(common.redirect_stays_in_staging(''))
        self.assertFalse(common.redirect_stays_in_staging(None))

    def test_the_module_carries_the_same_rule(self):
        text = code_text('PilotKit.psm1')
        self.assertIn('function Test-PilotRedirectStaysInStaging', text)
        self.assertIn('function Test-PilotSmokeStatus', text)
        self.assertIn('$script:SmokeAllowedStatus = @(200)', text)
        # Redirects are NOT followed automatically. Not via
        # `-MaximumRedirection 0`, which on Windows PowerShell 5.1 throws an
        # exception carrying no response and loses the status entirely -- the
        # Windows CI job caught exactly that.
        self.assertIn('$request.AllowAutoRedirect = $false', text)
        self.assertNotIn('-MaximumRedirection', text)

    def test_the_deploy_records_the_smoke_result_in_its_evidence(self):
        text = code_text('STAGING_DEPLOY_AND_MIGRATE.ps1')
        self.assertIn('$payload.smoke_test_ok = [bool]$smoke.ok', text)
        self.assertIn("Save-Deploy 'smoke-test-failed'", text)
        self.assertLess(text.index("Save-Deploy 'smoke-test-failed'"),
                        text.index("Save-Deploy 'done'"))

    def test_the_old_less_than_500_rule_is_gone(self):
        for name in PS_SCRIPTS:
            self.assertNotIn('-ge 500', code_text(name), name)
            self.assertNotIn('$smokeStatus -ge 500', code_text(name), name)


# ═══ 12. Операторские скрипты: статические свойства ══════════════════════════

class ScriptsRefuseTheWrongMachine(unittest.TestCase):

    def test_every_script_asserts_the_host(self):
        for name in PS_SCRIPTS:
            self.assertIn('Assert-PilotHost', code_text(name), name)

    def test_the_collector_script_names_bak_tex11(self):
        text = code_text('BAK_TEX11_DJI_COLLECT_TO_STAGING.ps1')
        self.assertIn("ExpectedHost = 'BAK-TEX11'", text)

    def test_the_staging_scripts_name_the_staging_host(self):
        for name in STAGING_SCRIPTS:
            self.assertIn("ExpectedHost = 'srv-yoqsh'", code_text(name), name)

    def test_every_script_runs_under_strict_mode_and_stops_on_error(self):
        for name in PS_SCRIPTS:
            text = code_text(name)
            self.assertIn('Set-StrictMode -Version Latest', text, name)
            self.assertIn("$ErrorActionPreference = 'Stop'", text, name)


class ProductionIsUnreachable(unittest.TestCase):

    def test_no_script_writes_to_the_production_checkout(self):
        writers = ('Set-Content', 'Add-Content', 'Out-File', 'New-Item',
                   'Remove-Item', 'Move-Item')
        for name in PS_FILES:
            for line in code_lines(name):
                if 'C:\\transport-report\\' not in line:
                    continue
                for writer in writers:
                    self.assertNotIn(writer, line,
                                     '%s: a writing cmdlet on a production '
                                     'path: %s' % (name, line.strip()))

    def test_the_production_database_is_only_ever_a_backup_source(self):
        for name in PS_SCRIPTS:
            for line in code_lines(name):
                if 'ProductionDb' not in line:
                    continue
                allowed = ("'--source'" in line or 'Test-Path' in line
                           or 'Get-Item' in line or 'throw' in line)
                self.assertTrue(allowed,
                                '%s: production database used outside a '
                                'read-only context: %s' % (name, line.strip()))

    def test_no_script_passes_the_production_database_as_db(self):
        for name in PS_FILES:
            text = code_text(name)
            self.assertNotIn("'--db', $K.ProductionDb", text, name)
            self.assertNotIn("'--db' $K.ProductionDb", text, name)

    def test_no_script_touches_the_production_service(self):
        for name in PS_SCRIPTS:
            for line in code_lines(name):
                if ('Stop-Service' in line or 'Start-Service' in line
                        or 'Restart-Service' in line):
                    self.assertNotIn('ProductionService', line,
                                     '%s: %s' % (name, line.strip()))
                    self.assertIn('$', line,
                                  '%s: a service is named literally: %s'
                                  % (name, line.strip()))

    def test_the_production_url_is_never_requested(self):
        for name in PS_FILES:
            for line in code_lines(name):
                if 'Invoke-WebRequest' in line:
                    self.assertIn('$Url', line,
                                  '%s: a request to a literal address: %s'
                                  % (name, line.strip()))
        self.assertIn('Assert-PilotStagingUrl -Url $BaseUrl',
                      code_text('PilotKit.psm1'))

    def test_every_service_change_is_guarded_first(self):
        for name in PS_SCRIPTS:
            lines = code_lines(name)
            for index, line in enumerate(lines):
                if 'Stop-Service' not in line and 'Start-Service' not in line:
                    continue
                window = '\n'.join(lines[max(0, index - 6):index])
                self.assertIn('Assert-PilotServiceIsNotProduction', window,
                              '%s line %d changes a service without the guard '
                              'immediately above it' % (name, index + 1))


class ForbiddenCommands(unittest.TestCase):

    def test_no_script_uses_reset_hard(self):
        for name in PS_FILES:
            self.assertNotIn('reset --hard', code_text(name), name)
            self.assertNotIn("'reset'", code_text(name), name)

    def test_the_rollback_explains_why_reset_hard_is_absent(self):
        self.assertIn('reset --hard', script_text('STAGING_ROLLBACK.ps1'))

    def test_no_script_uses_git_add_everything(self):
        for name in PS_FILES:
            text = code_text(name)
            self.assertNotIn('git add -A', text, name)
            self.assertNotIn('git add .', text, name)

    def test_the_live_run_installs_nothing(self):
        text = code_text('BAK_TEX11_DJI_COLLECT_TO_STAGING.ps1')
        self.assertNotIn('pip install', text)
        self.assertNotIn('playwright install', text)

    def test_the_collector_script_uses_the_venv_python_only(self):
        text = code_text('BAK_TEX11_DJI_COLLECT_TO_STAGING.ps1')
        self.assertNotIn('Program Files\\Python314', text)
        self.assertIn('CollectorPython', text)

    def test_no_script_leaves_a_placeholder_to_fill_in(self):
        placeholder = re.compile(r'<[A-Z_]{3,}>|TODO|FIXME')
        for name in PS_FILES:
            for line in code_lines(name):
                self.assertIsNone(placeholder.search(line),
                                  '%s: a placeholder would be pasted '
                                  'literally: %s' % (name, line.strip()))


class OnlyRealCommandLineFlags(unittest.TestCase):

    def test_every_collector_flag_exists(self):
        from drone_collector.main import build_parser
        known = set()
        for action in build_parser()._actions:
            known.update(action.option_strings)
        text = code_text('BAK_TEX11_DJI_COLLECT_TO_STAGING.ps1')
        for chunk in re.findall(r'drone_collector\.main ([^\n|>]*)', text):
            for flag in re.findall(r'--[a-z0-9-]+', chunk):
                self.assertIn(flag, known,
                              'the collector has no flag %s' % flag)

    def test_every_recalculation_flag_exists(self):
        sys.path.insert(0, os.path.join(REPO_ROOT, 'tools'))
        import recalculate_drone_useful_area as tool
        known = set()
        for action in tool.build_parser()._actions:
            known.update(action.option_strings)
        text = code_text('STAGING_RECALCULATE_AND_VERIFY.ps1')
        for chunk in re.findall(r'\$tool ([^\n]*)', text):
            for flag in re.findall(r"'(--[a-z0-9-]+)'", chunk):
                self.assertIn(flag, known,
                              'the recalculation tool has no flag %s' % flag)

    def test_the_backup_tool_flags_exist(self):
        for name in ('PREFLIGHT_AND_COPY_TEST.ps1',
                     'STAGING_DEPLOY_AND_MIGRATE.ps1'):
            for chunk in re.findall(r'\$backupTool,([^\n]*)', code_text(name)):
                for flag in re.findall(r"'(--[a-z-]+)'", chunk):
                    self.assertIn(flag, ('--source', '--dest-dir', '--suffix'),
                                  '%s: backup_transport_db.py has no %s'
                                  % (name, flag))

    def test_the_migration_is_never_given_a_flag_it_does_not_have(self):
        for name in ('PREFLIGHT_AND_COPY_TEST.ps1',
                     'STAGING_DEPLOY_AND_MIGRATE.ps1'):
            for line in code_lines(name):
                if 'migrate_drones_useful_area_001.py' in line:
                    self.assertNotIn('--db', line, name)


class PassIsNeverPrintedAfterAFailure(unittest.TestCase):

    def test_every_pass_line_comes_after_the_last_throw(self):
        for name in PS_SCRIPTS:
            text = code_text(name)
            passes = [index for index, line in enumerate(text.splitlines())
                      if re.search(r'Write-Output "[A-Z_0-9]+=PASS"', line)]
            if not passes:
                continue
            throws = [index for index, line in enumerate(text.splitlines())
                      if line.strip().startswith('throw')]
            self.assertTrue(throws, '%s prints PASS but can never fail' % name)
            self.assertGreater(max(passes), max(throws),
                               '%s prints PASS before its last refusal' % name)

    def test_every_script_can_actually_refuse(self):
        for name in PS_SCRIPTS:
            self.assertIn('throw', code_text(name), name)


class RollbackUsesTheRunEvidence(unittest.TestCase):

    def test_the_rollback_reads_the_run_and_needs_no_typed_file_name(self):
        text = code_text('STAGING_ROLLBACK.ps1')
        self.assertIn('Get-PilotRun -RunsRoot $RunsRoot -RunId $RunId', text)
        self.assertIn('$payload.backup_path', text)
        self.assertNotIn('transport_20', text)

    def test_the_rollback_refuses_evidence_of_another_run(self):
        text = code_text('STAGING_ROLLBACK.ps1')
        self.assertIn("if ($deploy.run_id -ne $RunId) { $refusals += "
                      "'DEPLOY_ENVELOPE_RUN_ID_MISMATCH' }", text)
        self.assertIn("if ($deploy.kit_sha -ne $ApprovedKitSha) { $refusals "
                      "+= 'DEPLOY_KIT_SHA_IS_NOT_APPROVED' }", text)

    def test_the_rollback_verifies_the_backup_before_stopping_anything(self):
        text = code_text('STAGING_ROLLBACK.ps1')
        self.assertIn('if ($sha -ne $payload.backup_sha256) {', text)
        self.assertIn('if (-not $backupState.payload.integrity.integrity_ok) {',
                      text)
        self.assertLess(text.index('if ($sha -ne $payload.backup_sha256) {'),
                        text.index('Stop-Service'))

    def test_the_restored_file_is_compared_to_the_backup(self):
        self.assertIn('if ($restoredSha -ne $payload.backup_sha256) {',
                      code_text('STAGING_ROLLBACK.ps1'))

    def test_the_rollback_restores_the_recorded_sha(self):
        text = code_text('STAGING_ROLLBACK.ps1')
        self.assertIn("'checkout', '--detach', $payload.sha_before", text)
        self.assertIn('TO_GO_FORWARD_AGAIN', text)

    def test_the_rollback_moves_the_wal_sidecars_aside(self):
        self.assertIn("'-wal', '-shm'", code_text('STAGING_ROLLBACK.ps1'))


class DeployRefusesAnUnverifiedCommit(unittest.TestCase):

    def test_the_deploy_fast_forwards_to_the_product_sha_not_to_main(self):
        text = code_text('STAGING_DEPLOY_AND_MIGRATE.ps1')
        self.assertIn("'merge', '--ff-only', $K.ProductSha", text)
        self.assertNotIn("'pull'", text)
        self.assertIn('Assert-PilotProductSha', text)

    def test_the_backup_is_verified_before_the_code_moves(self):
        text = code_text('STAGING_DEPLOY_AND_MIGRATE.ps1')
        self.assertLess(text.index('BACKUP_INTEGRITY=$backupVerified'),
                        text.index("'merge', '--ff-only'"))
        self.assertIn('if (-not $backupVerified) {', text)

    def test_the_migration_never_runs_before_the_service_is_stopped(self):
        text = code_text('STAGING_DEPLOY_AND_MIGRATE.ps1')
        self.assertLess(text.index('Stop-Service'),
                        text.index('$migrationInPlace'))

    def test_the_migration_runs_only_from_verified_bytes(self):
        # [REASON]: якорь -- запуск миграции, а не способ его написать. Раньше
        # это была строка `& $Python $migrationInPlace`; теперь запуск идёт
        # через Invoke-PilotNative, потому что прямой захват через 2>&1 убивал
        # шаг на предупреждении в stderr. Проверяемое свойство прежнее: байты
        # миграции сверены ДО того, как она запущена.
        text = code_text('STAGING_DEPLOY_AND_MIGRATE.ps1')
        self.assertIn('if (-not $migrationVerify.payload.passed) {', text)
        self.assertIn('Invoke-PilotNative -FilePath $Python '
                      '-Arguments @($migrationInPlace)', text)
        self.assertLess(text.index('if (-not $migrationVerify.payload.passed) {'),
                        text.index('Invoke-PilotNative -FilePath $Python '
                                   '-Arguments @($migrationInPlace)'))

    def test_a_failed_migration_stops_before_the_service_is_started(self):
        text = code_text('STAGING_DEPLOY_AND_MIGRATE.ps1')
        self.assertIn('if ($migrationCode -ne 0) {', text)
        self.assertLess(text.index('if ($migrationCode -ne 0) {'),
                        text.index('Start-Service'))


class RecalculationOrder(unittest.TestCase):

    def test_the_dry_run_comes_before_the_apply(self):
        text = code_text('STAGING_RECALCULATE_AND_VERIFY.ps1')
        self.assertLess(text.index("'--dry-run'"), text.index("'--apply'"))

    def test_the_apply_is_gated_on_the_dry_run(self):
        text = code_text('STAGING_RECALCULATE_AND_VERIFY.ps1')
        self.assertIn('if ($blockers.Count -gt 0) {', text)
        self.assertLess(text.index('REFUSED before --apply'),
                        text.index("'--apply'"))

    def test_the_apply_runs_twice(self):
        text = code_text('STAGING_RECALCULATE_AND_VERIFY.ps1')
        self.assertEqual(text.count("'--apply'"), 2)
        self.assertIn('SECOND_APPLY_INSERTED_ROWS', text)

    def test_the_recalculation_refuses_a_day_it_did_not_collect(self):
        text = code_text('STAGING_RECALCULATE_AND_VERIFY.ps1')
        self.assertIn('if ($inputState.payload.routes.routes_outside_target_day '
                      '-ne 0) {', text)
        self.assertIn('if ($inputState.payload.routes.routes_of_target_day '
                      '-le 0) {', text)

    def test_the_duration_is_measured(self):
        text = code_text('STAGING_RECALCULATE_AND_VERIFY.ps1')
        self.assertIn('Stopwatch', text)
        self.assertIn('APPLY_SECONDS', text)


class CollectorScriptSendsToStagingOnly(unittest.TestCase):

    def test_the_effective_configuration_is_checked_not_the_variable(self):
        text = code_text('BAK_TEX11_DJI_COLLECT_TO_STAGING.ps1')
        self.assertIn('pilot_collect_check.py', text)
        self.assertIn('target_is_production', text)
        self.assertIn('Assert-PilotStagingUrl', text)

    def test_a_production_target_is_a_refusal(self):
        self.assertIn('the collector is configured to send to PRODUCTION',
                      code_text('BAK_TEX11_DJI_COLLECT_TO_STAGING.ps1'))

    def test_no_secret_value_is_ever_printed(self):
        for name in PS_FILES:
            text = code_text(name)
            self.assertNotIn('$env:DRONE_API_TOKEN', text, name)
            for line in code_lines(name):
                if 'Write-Output' not in line and 'Write-Host' not in line:
                    continue
                if 'token' not in line.lower():
                    continue
                self.assertIn('api_token', line, name)
                self.assertNotIn('$env:', line, name)

    def test_the_preflight_happens_before_the_browser_opens(self):
        text = code_text('BAK_TEX11_DJI_COLLECT_TO_STAGING.ps1')
        self.assertLess(
            text.index('PREFLIGHT_COLLECTOR=PASS'),
            text.index('drone_collector.main --route-ui-collect'))

    def test_a_partially_accepted_batch_is_a_failure_of_the_script_too(self):
        text = code_text('BAK_TEX11_DJI_COLLECT_TO_STAGING.ps1')
        self.assertIn('COLLECTION NOT ACCEPTED AS COMPLETE', text)
        self.assertIn('BAK_TEX11_DJI_COLLECT_TO_STAGING=FAIL', text)


# ═══ 13. Пути, отпечаток area_ha, приборы, приватность ══════════════════════

class PathGuards(unittest.TestCase):

    def test_staging_root_is_not_inside_production(self):
        self.assertFalse(common.path_is_within(common.STAGING_ROOT,
                                               common.PRODUCTION_ROOT))

    def test_a_substring_guard_would_have_been_wrong(self):
        naive = common.STAGING_ROOT.lower().startswith(
            common.PRODUCTION_ROOT.lower())
        self.assertTrue(naive, 'the substring trap must still exist, else '
                               'this test proves nothing')
        self.assertNotEqual(naive,
                            common.path_is_within(common.STAGING_ROOT,
                                                  common.PRODUCTION_ROOT))

    def test_production_database_is_recognised(self):
        self.assertTrue(common.touches_production(common.PRODUCTION_DB))
        self.assertFalse(common.touches_production(common.STAGING_DB))

    def test_slashes_and_case_do_not_change_the_answer(self):
        self.assertTrue(common.path_equals('C:/transport-report/instance',
                                           'C:\\TRANSPORT-REPORT\\instance\\'))

    def test_production_and_staging_urls_never_collide(self):
        self.assertTrue(common.url_is_production(common.PRODUCTION_URL))
        self.assertFalse(common.url_is_production(common.STAGING_URL))
        self.assertTrue(common.url_is_staging(common.STAGING_URL + '/drones/'))


class AreaFingerprint(unittest.TestCase):
    """Проверка обязана РАЗЛИЧАТЬ изменённую и неизменённую площадь."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='pilot_area_')
        self.db = os.path.join(self.tmp, 'throwaway.db')
        con = sqlite3.connect(self.db)
        con.execute('CREATE TABLE drone_flights (id INTEGER PRIMARY KEY, '
                    'area_ha FLOAT)')
        con.executemany('INSERT INTO drone_flights VALUES (?, ?)',
                        [(1, 1.2345), (2, 2.0), (3, None)])
        con.commit()
        con.close()
        self.base = self.fingerprint()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fingerprint(self):
        con, _mode = common.connect_readonly(self.db)
        try:
            return common.area_ha_fingerprint(con)
        finally:
            con.close()

    def write(self, sql, args=()):
        con = sqlite3.connect(self.db)
        con.execute(sql, args)
        con.commit()
        con.close()

    def test_the_smallest_possible_change_is_seen(self):
        self.write('UPDATE drone_flights SET area_ha = ? WHERE id = 1',
                   (1.2345 + 1e-12,))
        self.assertNotEqual(self.fingerprint()['sha256'], self.base['sha256'])

    def test_null_becoming_zero_is_seen_and_the_sum_would_not_see_it(self):
        self.write('UPDATE drone_flights SET area_ha = 0.0 WHERE id = 3')
        after = self.fingerprint()
        self.assertNotEqual(after['sha256'], self.base['sha256'])
        self.assertEqual(after['sum_area_ha'], self.base['sum_area_ha'])

    def test_swapping_two_rows_is_seen_and_the_sum_would_not_see_it(self):
        self.write('UPDATE drone_flights SET area_ha = 2.0 WHERE id = 1')
        self.write('UPDATE drone_flights SET area_ha = 1.2345 WHERE id = 2')
        after = self.fingerprint()
        self.assertNotEqual(after['sha256'], self.base['sha256'])
        self.assertEqual(after['sum_area_ha'], self.base['sum_area_ha'])

    def test_an_added_null_row_is_seen_and_the_sum_would_not_see_it(self):
        self.write('INSERT INTO drone_flights VALUES (4, NULL)')
        after = self.fingerprint()
        self.assertNotEqual(after['sha256'], self.base['sha256'])
        self.assertEqual(after['sum_area_ha'], self.base['sum_area_ha'])

    def test_restoring_the_value_restores_the_digest(self):
        self.write('UPDATE drone_flights SET area_ha = 9.9 WHERE id = 1')
        self.assertNotEqual(self.fingerprint()['sha256'], self.base['sha256'])
        self.write('UPDATE drone_flights SET area_ha = 1.2345 WHERE id = 1')
        self.assertEqual(self.fingerprint()['sha256'], self.base['sha256'])


class ProbeIsReadOnly(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='pilot_probe_')
        self.db = os.path.join(self.tmp, 'throwaway.db')
        con = sqlite3.connect(self.db)
        con.execute('CREATE TABLE drone_flights (id INTEGER PRIMARY KEY, '
                    'area_ha FLOAT)')
        con.execute('INSERT INTO drone_flights VALUES (1, 1.0)')
        con.commit()
        con.close()
        self.run_id = common.new_run_id()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_connection_refuses_a_write(self):
        con, mode = common.connect_readonly(self.db)
        try:
            self.assertIn(mode, ('uri-ro', 'query_only'))
            with self.assertRaises(sqlite3.OperationalError):
                con.execute('UPDATE drone_flights SET area_ha = 2.0')
                con.commit()
        finally:
            con.close()

    def test_a_missing_database_is_refused_and_no_file_is_created(self):
        missing = os.path.join(self.tmp, 'not-there.db')
        result = run_python(os.path.join(KIT_DIR, 'pilot_db_probe.py'),
                            'integrity', '--db', missing,
                            '--run-id', self.run_id,
                            '--kit-sha', KIT_SHA_FIXTURE)
        self.assertEqual(result.returncode, common.EXIT_NO_DATABASE)
        self.assertFalse(os.path.exists(missing))

    def test_an_unknown_requirement_fails_rather_than_passing_silently(self):
        payload = {'integrity': {'integrity_ok': True}}
        self.assertEqual(
            probe.evaluate_requirements(payload, ['integrity', 'integritty']),
            [('integrity', True), ('integritty', False)])

    def test_stdout_is_one_json_document_even_when_a_check_fails(self):
        result = run_python(os.path.join(KIT_DIR, 'pilot_db_probe.py'),
                            'schema', '--db', self.db, '--require', 'schema',
                            '--run-id', self.run_id,
                            '--kit-sha', KIT_SHA_FIXTURE)
        self.assertEqual(result.returncode, common.EXIT_CHECK_FAILED)
        json.loads(result.stdout)
        self.assertIn('CHECK FAILED', result.stderr)


class RecalcSummaryParsing(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.site = SyntheticSite()
        cls.evidence = cls.site.recalc_evidence()

    @classmethod
    def tearDownClass(cls):
        cls.site.close()

    def load(self, label):
        return common.read_evidence(self.evidence[label])['payload']

    def test_the_parser_reads_the_real_format_summary(self):
        parsed = recalc_parse.parse_summary(recalc.format_summary({
            'applied': True, 'algorithm_version': 'useful-area-v1',
            'days': 1, 'works': 3, 'flights': 7, 'routes': 6,
            'inserted': 3, 'updated': 0, 'unchanged': 0, 'deleted': 0,
            'READY_ESTIMATE': 2, 'PARTIAL_DATA': 1, 'DATA_UNAVAILABLE': 0,
            'CONTOUR_AMBIGUOUS': 0, 'CONTOUR_NOT_MATCHED': 0,
            'ROUTE_INVALID': 0, 'ready_area_ha': 12.5,
        }))
        self.assertTrue(parsed['applied'])
        self.assertEqual(parsed['works'], 3)
        self.assertEqual(parsed['ready_area_ha'], 12.5)

    def test_a_missing_field_is_refused_rather_than_defaulted_to_zero(self):
        text = recalc.format_summary({
            'applied': False, 'algorithm_version': 'useful-area-v1',
            'days': 1, 'works': 1, 'flights': 1, 'routes': 1, 'inserted': 1,
            'updated': 0, 'unchanged': 0, 'deleted': 0, 'READY_ESTIMATE': 1,
            'PARTIAL_DATA': 0, 'DATA_UNAVAILABLE': 0, 'CONTOUR_AMBIGUOUS': 0,
            'CONTOUR_NOT_MATCHED': 0, 'ROUTE_INVALID': 0,
            'ready_area_ha': 1.0})
        broken = '\n'.join(line for line in text.splitlines()
                           if not line.startswith('rows updated'))
        with self.assertRaises(recalc_parse.ParseError):
            recalc_parse.parse_summary(broken)

    def test_the_dry_run_and_the_first_apply_agree(self):
        self.assertTrue(self.load('apply-1')['outputs_agree'])

    def test_the_second_apply_wrote_nothing_and_the_first_did(self):
        second = self.load('apply-2')['summary']
        self.assertEqual(second['inserted'], 0)
        self.assertEqual(second['updated'], 0)
        self.assertEqual(second['deleted'], 0)
        self.assertEqual(second['unchanged'], second['works'])
        self.assertGreater(self.load('apply-1')['summary']['inserted'], 0)

    def test_the_dry_run_carries_the_wrote_nothing_flag(self):
        self.assertTrue(self.load('dry-run')['wrote_nothing'])
        self.assertFalse(self.load('apply-1')['wrote_nothing'])

    def test_comparing_with_another_runs_evidence_is_refused(self):
        other_run = common.new_run_id()
        other = os.path.join(self.site.evidence, 'foreign.json')
        common.write_evidence(other, envelope('recalc:dry-run', {
            'label': 'dry-run', 'summary': {}}, other_run))
        result = run_python(
            os.path.join(KIT_DIR, 'pilot_recalc_parse.py'),
            '--input', self.site.recalc_output['apply-1'],
            '--label', 'apply-1', '--expect-day', DAY,
            '--compare-with', other, '--run-id', self.site.run_id,
            '--kit-sha', KIT_SHA_FIXTURE)
        self.assertEqual(result.returncode, common.EXIT_ERROR)
        self.assertIn('belongs to another run', result.stderr)


class PrivacyScan(unittest.TestCase):

    def clean(self):
        return {'kit': common.KIT_ID, 'target_day': DAY,
                'kit_sha': 'a' * 40, 'product_sha': common.PRODUCT_SHA,
                'run_id': common.new_run_id(),
                'works_formed': 16, 'ready_useful_area_ha': 12.3456,
                'verdict': 'TECHNICAL_GO'}

    def test_a_clean_report_passes(self):
        self.assertEqual(
            report_mod.scan_for_private_values(self.clean(), 'ok'), [])

    def test_a_uuid_is_caught(self):
        report = self.clean()
        report['verdict_reasons'] = ['00000000-1111-2222-3333-444444444444']
        codes = [item['code']
                 for item in report_mod.scan_for_private_values(report, '')]
        self.assertIn('UUID_IN_REPORT', codes)

    def test_a_coordinate_is_caught_in_a_string_and_as_a_number(self):
        report = self.clean()
        report['verdict_reasons'] = ['39.7001234, 64.4005678']
        codes = [item['code']
                 for item in report_mod.scan_for_private_values(report, '')]
        self.assertIn('COORDINATE_LIKE_VALUE', codes)
        report = self.clean()
        report['ready_useful_area_ha'] = 39.7001234
        codes = [item['code']
                 for item in report_mod.scan_for_private_values(report, '')]
        self.assertIn('COORDINATE_LIKE_NUMBER', codes)

    def test_an_area_rounded_to_four_places_is_not_a_coordinate(self):
        report = self.clean()
        report['ready_useful_area_ha'] = 1234.5678
        self.assertEqual(report_mod.scan_for_private_values(report, ''), [])

    def test_a_field_outside_the_allowlist_is_caught(self):
        report = self.clean()
        report['dji_flight_ids'] = [1, 2, 3]
        codes = [item['code']
                 for item in report_mod.scan_for_private_values(report, '')]
        self.assertIn('UNDECLARED_FIELD', codes)

    def test_a_secret_word_is_caught_in_the_markdown(self):
        codes = [item['code'] for item in report_mod.scan_for_private_values(
            self.clean(), 'Authorization: Bearer x')]
        self.assertIn('SECRET_WORD_IN_MARKDOWN', codes)

    def test_the_measured_revisions_are_allowed_to_be_hex(self):
        self.assertEqual(report_mod.scan_for_private_values(self.clean(), ''),
                         [])

    def test_a_dirty_report_is_forced_to_reject(self):
        verdict, reasons = report_mod.decide(
            [{'code': 'X', 'passed': True, 'means': ''}],
            {'works_without_number_share': 0.0}, 0.5, None, 10.0, False, [])
        self.assertEqual(verdict, 'REJECT')
        self.assertIn('REPORT_CONTAINS_PRIVATE_VALUES', reasons)


# ═══ 14. PowerShell: настоящий парсер и выполнение чистых функций ════════════

def find_pwsh():
    """Какой PowerShell гонять.

    [REASON]: `PILOT_POWERSHELL` существует ради Windows-раннера. На нём стоят
    оба: `pwsh` (7) и `powershell.exe` (5.1), и `shutil.which` находит
    седьмой. А целевая консоль на сервере -- пятая, и именно в ней нет
    `utf8NoBOM` и именно она пишет UTF-16LE через `>`. Проверка, выбравшая
    седьмой, проверила бы не ту платформу.
    """
    override = os.environ.get('PILOT_POWERSHELL')
    if override:
        return shutil.which(override) or override
    for name in ('pwsh', 'powershell'):
        found = shutil.which(name)
        if found:
            return found
    return None


PWSH = find_pwsh()
PWSH_IS_PINNED = bool(os.environ.get('PILOT_POWERSHELL'))

PARSE_SCRIPT = r'''
param([string[]]$Paths)
$bad = 0
foreach ($p in $Paths) {
  $tokens = $null; $errors = $null
  $null = [System.Management.Automation.Language.Parser]::ParseFile(
      (Resolve-Path -LiteralPath $p).Path, [ref]$tokens, [ref]$errors)
  if ($errors -and $errors.Count -gt 0) {
    $bad = 1
    foreach ($e in $errors) {
      Write-Host ("PARSE ERROR {0}:{1} {2}" -f $p, $e.Extent.StartLineNumber, $e.Message)
    }
  }
}
exit $bad
'''

GUARD_SCRIPT = r'''
param([string]$Module, [string]$Python, [string]$Probe, [string]$Db)
Import-Module $Module -Force
$results = [ordered]@{}
$results['ps_major'] = $PSVersionTable.PSVersion.Major
$results['ps_edition'] = if ($PSVersionTable.PSEdition) { [string]$PSVersionTable.PSEdition } else { 'Desktop' }
function Throws([scriptblock]$b) { try { & $b | Out-Null; return $false } catch { return $true } }

$results['staging_not_within_production'] = (Test-PilotPathWithin -Path 'C:\transport-report-staging' -Root 'C:\transport-report')
$results['production_db_within_production'] = (Test-PilotPathWithin -Path 'C:\transport-report\instance\transport.db' -Root 'C:\transport-report')
$results['slashes_and_case'] = (Test-PilotPathEquals 'C:/transport-report/instance' 'C:\TRANSPORT-REPORT\instance\')
$results['production_url'] = (Test-PilotUrlIsProduction 'http://10.103.25.14:5050/')
$results['staging_url_is_production'] = (Test-PilotUrlIsProduction 'http://10.103.25.14:5051')
$results['refuses_production_path'] = (Throws { Assert-PilotNotProduction -Path 'C:\transport-report\instance\transport.db' })
$results['allows_staging_path'] = (Throws { Assert-PilotNotProduction -Path 'C:\transport-report-staging\instance' })
$results['refuses_production_url'] = (Throws { Assert-PilotStagingUrl -Url 'http://10.103.25.14:5050' })
$results['refuses_production_service'] = (Throws { Assert-PilotServiceIsNotProduction -Name 'TransportReport' })

$results['smoke_200'] = (Test-PilotSmokeStatus -Status 200)
$results['smoke_404'] = (Test-PilotSmokeStatus -Status 404)
$results['smoke_401'] = (Test-PilotSmokeStatus -Status 401)
$results['smoke_403'] = (Test-PilotSmokeStatus -Status 403)
$results['redirect_relative'] = (Test-PilotRedirectStaysInStaging -Location '/login')
$results['redirect_production'] = (Test-PilotRedirectStaysInStaging -Location 'http://10.103.25.14:5050/login')
$results['redirect_foreign'] = (Test-PilotRedirectStaysInStaging -Location 'http://evil.example/login')
$results['redirect_userinfo'] = (Test-PilotRedirectStaysInStaging -Location 'http://10.103.25.14:5051@evil.example/x')

$results['work_root_in_collector_refused'] = (Throws { Assert-PilotOutsideCheckouts -Path 'C:\VehicleSoft_DJI_StageB_Pilot\pilot_evidence' })
$results['work_root_in_staging_refused'] = (Throws { Assert-PilotOutsideCheckouts -Path 'C:\transport-report-staging\runs' })
$results['work_root_in_kit_refused'] = (Throws { Assert-PilotOutsideCheckouts -Path 'C:\vehicle-soft-pilot-kit\runs' })
$results['work_root_outside_allowed'] = (Throws { Assert-PilotOutsideCheckouts -Path 'C:\vehicle-soft-pilot-runs' })

$prod    = [pscustomobject]@{ Name='TransportReport';        AppDirectory='C:\transport-report';         Application='py.exe'; ImagePath='x' }
$staging = [pscustomobject]@{ Name='TransportReportStaging'; AppDirectory='C:\transport-report-staging'; Application='py.exe'; ImagePath='y' }
$other   = [pscustomobject]@{ Name='TransportReportOther';   AppDirectory='C:\transport-report-staging'; Application='';       ImagePath='' }
$results['picks_staging'] = ((Select-PilotStagingService -Candidates @($prod,$staging)).Name)
$results['refuses_when_none'] = (Throws { Select-PilotStagingService -Candidates @($prod) })
$results['refuses_when_two'] = (Throws { Select-PilotStagingService -Candidates @($staging,$other) })

# --- JSON round trip: no BOM, not UTF-16, readable back ---------------------
$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("pilotkit_{0}.json" -f (Get-Random))
Write-PilotJson -Path $tmp -Value ([ordered]@{ run_id = '20260902T101500Z-deadbeef'; n = 42 }) | Out-Null
$bytes = [System.IO.File]::ReadAllBytes($tmp)
$results['json_has_no_bom'] = -not ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
$results['json_is_not_utf16'] = -not ($bytes.Length -ge 2 -and $bytes[0] -eq 0xFF -and $bytes[1] -eq 0xFE)
$back = Read-PilotJson -Path $tmp
$results['json_round_trip'] = ($back.run_id -eq '20260902T101500Z-deadbeef' -and [int]$back.n -eq 42)
Remove-Item -LiteralPath $tmp -Force

# --- Invoke-PilotPython and Invoke-PilotProbe against a real probe ----------
if ($Python -and $Probe -and $Db) {
    $out = Join-Path ([System.IO.Path]::GetTempPath()) ("pilotprobe_{0}.json" -f (Get-Random))
    $probeRun = Invoke-PilotProbe -Python $Python -Script $Probe `
        -Arguments @('integrity', '--db', $Db) `
        -RunId '20260902T101500Z-deadbeef' -KitSha ('a' * 40) -OutFile $out
    $results['probe_exit'] = $probeRun.ExitCode
    $results['probe_evidence_run_id'] = $probeRun.Evidence.run_id
    $results['probe_integrity_ok'] = $probeRun.Evidence.payload.integrity.integrity_ok
    $evidenceBytes = [System.IO.File]::ReadAllBytes($out)
    $results['probe_json_has_no_bom'] = -not ($evidenceBytes.Length -ge 3 -and $evidenceBytes[0] -eq 0xEF)
    Remove-Item -LiteralPath $out -Force
    $results['python_exit_is_checked'] = (Throws { Invoke-PilotPython -Python $Python -Arguments @('-c', 'import sys; sys.exit(7)') })
}

$results | ConvertTo-Json -Compress
'''


@unittest.skipIf(PWSH is None, 'no PowerShell in this environment')
class PowerShellIsValid(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix='pilot_ps_db_')
        cls.db = os.path.join(cls.tmp, 'throwaway.db')
        con = sqlite3.connect(cls.db)
        con.execute('CREATE TABLE drone_flights (id INTEGER PRIMARY KEY, '
                    'area_ha FLOAT)')
        con.execute('INSERT INTO drone_flights VALUES (1, 1.0)')
        con.commit()
        con.close()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_pwsh(self, script, *arguments):
        directory = tempfile.mkdtemp(prefix='pilot_ps_')
        try:
            path = os.path.join(directory, 'check.ps1')
            with open(path, 'w', encoding='utf-8', newline='\n') as handle:
                handle.write(script)
            return subprocess.run([PWSH, '-NoProfile', '-File', path]
                                  + list(arguments),
                                  capture_output=True, text=True,
                                  cwd=REPO_ROOT)
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def state(self):
        result = self.run_pwsh(
            GUARD_SCRIPT,
            '-Module', os.path.join(KIT_DIR, 'PilotKit.psm1'),
            '-Python', sys.executable,
            '-Probe', os.path.join(KIT_DIR, 'pilot_db_probe.py'),
            '-Db', self.db)
        self.assertEqual(result.returncode, 0,
                         '%s\n%s' % (result.stdout, result.stderr))
        line = [row for row in result.stdout.splitlines()
                if row.strip().startswith('{')][-1]
        return json.loads(line)

    def test_every_script_parses(self):
        paths = [os.path.join(KIT_DIR, name) for name in PS_FILES]
        result = self.run_pwsh(PARSE_SCRIPT, '-Paths', *paths)
        self.assertEqual(result.returncode, 0,
                         'PowerShell refused to parse a script:\n%s'
                         % result.stdout)

    def test_the_path_and_url_guards_behave_as_claimed(self):
        state = self.state()
        self.assertFalse(state['staging_not_within_production'])
        self.assertTrue(state['production_db_within_production'])
        self.assertTrue(state['slashes_and_case'])
        self.assertTrue(state['production_url'])
        self.assertFalse(state['staging_url_is_production'])
        self.assertTrue(state['refuses_production_path'])
        self.assertFalse(state['allows_staging_path'])
        self.assertTrue(state['refuses_production_url'])
        self.assertTrue(state['refuses_production_service'])
        self.assertEqual(state['picks_staging'], 'TransportReportStaging')
        self.assertTrue(state['refuses_when_none'])
        self.assertTrue(state['refuses_when_two'])

    def test_the_smoke_rules_behave_as_claimed(self):
        state = self.state()
        self.assertTrue(state['smoke_200'])
        self.assertFalse(state['smoke_404'])
        self.assertFalse(state['smoke_401'])
        self.assertFalse(state['smoke_403'])
        self.assertTrue(state['redirect_relative'])
        self.assertFalse(state['redirect_production'])
        self.assertFalse(state['redirect_foreign'])
        self.assertFalse(state['redirect_userinfo'])

    def test_the_work_root_guard_behaves_as_claimed(self):
        state = self.state()
        self.assertTrue(state['work_root_in_collector_refused'])
        self.assertTrue(state['work_root_in_staging_refused'])
        self.assertTrue(state['work_root_in_kit_refused'])
        self.assertFalse(state['work_root_outside_allowed'])

    def test_json_is_written_and_read_back_without_a_bom(self):
        state = self.state()
        self.assertTrue(state['json_has_no_bom'])
        self.assertTrue(state['json_is_not_utf16'])
        self.assertTrue(state['json_round_trip'])

    def test_invoke_pilot_probe_runs_a_real_probe_and_reads_its_evidence(self):
        state = self.state()
        self.assertEqual(state['probe_exit'], 0)
        self.assertEqual(state['probe_evidence_run_id'],
                         '20260902T101500Z-deadbeef')
        self.assertTrue(state['probe_integrity_ok'])
        self.assertTrue(state['probe_json_has_no_bom'])
        self.assertTrue(state['python_exit_is_checked'])

    def test_the_reported_powershell_is_the_one_that_was_asked_for(self):
        """Windows-задача CI обязана доказать, что гоняла ИМЕННО 5.1."""
        state = self.state()
        self.assertGreaterEqual(int(state['ps_major']), 5)
        expected = os.environ.get('PILOT_POWERSHELL_MAJOR')
        if expected:
            self.assertEqual(int(state['ps_major']), int(expected),
                             'the job asked for PowerShell %s and got %s'
                             % (expected, state['ps_major']))

    def test_the_python_and_powershell_guards_agree(self):
        state = self.state()
        self.assertEqual(
            state['staging_not_within_production'],
            common.path_is_within(common.STAGING_ROOT, common.PRODUCTION_ROOT))
        self.assertEqual(state['production_url'],
                         common.url_is_production(common.PRODUCTION_URL))
        self.assertEqual(state['redirect_production'],
                         common.redirect_stays_in_staging(
                             common.PRODUCTION_URL + '/login'))
        self.assertEqual(state['smoke_404'],
                         404 in common.SMOKE_ALLOWED_STATUS)


class ConstantsAgreeAcrossLanguages(unittest.TestCase):

    def test_the_module_and_the_python_carry_the_same_sites(self):
        text = script_text('PilotKit.psm1')
        for value in (common.PRODUCTION_ROOT, common.PRODUCTION_DB,
                      common.PRODUCTION_URL, common.PRODUCTION_SERVICE,
                      common.STAGING_ROOT, common.STAGING_DB,
                      common.STAGING_URL, common.PRODUCT_SHA,
                      common.TARGET_DAY, common.MIGRATION_ID,
                      common.KIT_ROOT, common.SERVER_RUNS_ROOT,
                      common.COLLECTOR_RUNS_ROOT, common.SMOKE_PATH):
            self.assertIn(value, text,
                          'PilotKit.psm1 does not carry %r' % value)

    def test_the_kit_version_agrees(self):
        self.assertRegex(script_text('PilotKit.psm1'),
                         r"kit_version\s+= '%s'" % common.KIT_VERSION)

    def test_the_staging_service_name_comes_from_the_repository(self):
        runbook = os.path.join(REPO_ROOT, 'docs',
                               'ORG_WINDOWS_SERVER_STAGING_RUNBOOK.md')
        with open(runbook, encoding='utf-8') as handle:
            text = handle.read()
        found = re.search(r'Service name.*`([A-Za-z0-9_]+)`', text)
        self.assertIsNotNone(found, 'the runbook no longer names the service')
        self.assertEqual(found.group(1), 'TransportReportStaging')

    def test_the_kit_never_hardcodes_the_service_without_resolving_it(self):
        for name in STAGING_SCRIPTS:
            text = code_text(name)
            if 'Stop-Service' in text or 'Start-Service' in text:
                self.assertIn('Resolve-PilotStagingService', text, name)



# ═══ Р1. Предполёт и деплой больше не противоречат друг другу ════════════════

class PreflightDoesNotDemandTheEndState(unittest.TestCase):
    """Первый шаг обязан ПУСКАТЬ ко второму, а не требовать его результата.

    [REASON]: предполёт требовал от площадки уже стоять на PRODUCT_SHA, а
    перевести её туда -- работа второго шага. Первый делал второй
    недостижимым, и заметить это можно было только на сервере.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='pilot_ff_')
        self.repo = os.path.join(self.tmp, 'repo')
        os.makedirs(self.repo)
        self.git('init', '-q')
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('config', 'user.name', 'test')
        self.write('a.txt', 'one\n')
        self.git('add', 'a.txt')
        self.git('commit', '-qm', 'first')
        self.base = common.head_sha(self.repo)
        self.write('a.txt', 'two\n')
        self.git('add', 'a.txt')
        self.git('commit', '-qm', 'second')
        self.target = common.head_sha(self.repo)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *arguments):
        subprocess.run(('git',) + arguments, cwd=self.repo, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def write(self, name, text):
        with open(os.path.join(self.repo, name), 'w', newline='\n') as handle:
            handle.write(text)

    def state(self, head):
        return common.fast_forward_state(self.repo, head, self.target)

    def test_an_ancestor_can_still_fast_forward(self):
        self.assertEqual(self.state(self.base), 'behind')

    def test_the_target_itself_is_accepted(self):
        self.assertEqual(self.state(self.target), 'at-target')

    def test_a_checkout_that_is_ahead_is_refused(self):
        self.git('checkout', '-q', self.target)
        self.write('a.txt', 'three\n')
        self.git('add', 'a.txt')
        self.git('commit', '-qm', 'third')
        self.assertEqual(self.state(common.head_sha(self.repo)), 'ahead')

    def test_a_diverged_checkout_is_refused(self):
        self.git('checkout', '-q', '-b', 'side', self.base)
        self.write('b.txt', 'other\n')
        self.git('add', 'b.txt')
        self.git('commit', '-qm', 'side')
        self.assertEqual(self.state(common.head_sha(self.repo)), 'diverged')

    def test_verify_accepts_a_behind_checkout_and_names_the_state(self):
        self.git('checkout', '-q', self.base)
        payload = repo_check.verify(self.repo, None, 'product',
                                    ancestor_of=self.target,
                                    blob_rev=self.target,
                                    manifest=self._empty_manifest())
        self.assertTrue(payload['passed'], payload['problems'])
        self.assertEqual(payload['fast_forward_state'], 'behind')
        self.assertTrue(payload['update_is_fast_forward'])

    def test_verify_refuses_a_diverged_checkout(self):
        self.git('checkout', '-q', '-b', 'side', self.base)
        self.write('b.txt', 'other\n')
        self.git('add', 'b.txt')
        self.git('commit', '-qm', 'side')
        payload = repo_check.verify(self.repo, None, 'product',
                                    ancestor_of=self.target,
                                    manifest=self._empty_manifest())
        self.assertFalse(payload['passed'])
        self.assertIn('CHECKOUT_HAS_DIVERGED_FROM_THE_TARGET_REVISION',
                      payload['problems'])

    def test_verify_refuses_a_dirty_worktree(self):
        self.write('a.txt', 'dirty\n')
        payload = repo_check.verify(self.repo, None, 'product',
                                    ancestor_of=self.target,
                                    manifest=self._empty_manifest())
        self.assertFalse(payload['passed'])
        self.assertIn('WORKTREE_IS_DIRTY', payload['problems'])

    def test_none_of_these_checks_change_the_checkout(self):
        """Отрицательный контроль: предполёт ничего не двигает."""
        self.git('checkout', '-q', self.base)
        before = common.head_sha(self.repo)
        listing = sorted(os.listdir(self.repo))
        for _attempt in range(3):
            repo_check.verify(self.repo, None, 'product',
                              ancestor_of=self.target, blob_rev=self.target,
                              manifest=self._empty_manifest())
        self.assertEqual(common.head_sha(self.repo), before)
        self.assertEqual(sorted(os.listdir(self.repo)), listing)
        self.assertTrue(common.worktree_is_clean(self.repo))

    def _empty_manifest(self):
        return {'product_sha': common.PRODUCT_SHA,
                'identical_on_both_revisions': {},
                'kit_differs_on_purpose': {}, 'kit_own_files': {}}

    def test_the_preflight_asks_for_an_ancestor_not_for_the_end_state(self):
        text = code_text('PREFLIGHT_AND_COPY_TEST.ps1')
        self.assertIn("'--ancestor-of', $K.ProductSha", text)
        self.assertIn("'--blob-rev', $K.ProductSha", text)
        self.assertNotIn("'verify', '--repo', $K.StagingRoot, '--expect-sha'",
                         text)
        self.assertIn('STAGING_CANNOT_FAST_FORWARD_TO_PRODUCT_SHA', text)

    def test_only_the_deploy_demands_the_end_state(self):
        deploy = code_text('STAGING_DEPLOY_AND_MIGRATE.ps1')
        self.assertIn('Assert-PilotProductSha -Repo $K.StagingRoot', deploy)
        self.assertLess(deploy.index("'merge', '--ff-only', $K.ProductSha"),
                        deploy.index('Assert-PilotProductSha'))


# ═══ Р2. Измеренная ревизия -- не одобренная ═════════════════════════════════

class ApprovedKitRevision(unittest.TestCase):

    def test_every_script_takes_the_approved_revision(self):
        for name in APPROVED_SHA_SCRIPTS:
            self.assertIn('[Parameter(Mandatory)][string]$ApprovedKitSha',
                          code_text(name), name)

    def test_the_module_separates_measuring_from_approving(self):
        text = code_text('PilotKit.psm1')
        self.assertIn('function Assert-PilotApprovedKitSha', text)
        self.assertIn('$measured -ne $ApprovedKitSha', text)

    def test_the_run_is_opened_only_after_the_revision_is_approved(self):
        text = code_text('PREFLIGHT_AND_COPY_TEST.ps1')
        self.assertLess(text.index('Assert-PilotApprovedKitSha'),
                        text.index('New-PilotRun'))

    def test_the_run_records_both_revisions_and_they_must_agree(self):
        text = code_text('PilotKit.psm1')
        self.assertIn('approved_kit_sha = $ApprovedKitSha', text)
        self.assertIn('measured_kit_sha = $kitSha', text)
        self.assertIn("$problems += 'APPROVED_AND_MEASURED_KIT_SHA_DIFFER'",
                      text)

    def test_every_later_step_rechecks_against_the_approved_revision(self):
        for name in RUN_STEP_SCRIPTS:
            text = code_text(name)
            if name == 'BAK_TEX11_DJI_COLLECT_TO_STAGING.ps1':
                self.assertIn('$KitSha = $ApprovedKitSha', text, name)
                continue
            self.assertIn('Assert-PilotApprovedKitSha -KitCheckout '
                          '$KitCheckout -ApprovedKitSha $ApprovedKitSha',
                          text, name)
            self.assertIn('$run.approved_kit_sha', text, name)

    def test_the_manifest_covers_the_kits_own_files(self):
        manifest = common.load_product_blobs()
        own = manifest.get('kit_own_files', {})
        for name in PS_FILES:
            self.assertIn('ops/pilot_useful_area_001/%s' % name, own, name)
        for name in ('pilot_common.py', 'pilot_report.py',
                     'pilot_repo_check.py', 'pilot_collect_gate.py'):
            self.assertIn('ops/pilot_useful_area_001/%s' % name, own, name)

    def test_regenerating_the_manifest_still_covers_the_kit(self):
        """Отрицательный контроль СБОРКИ манифеста, а не хранимого файла.

        [REASON]: `check_against_worktree` читает записанный JSON, поэтому
        поломка в `build()` им не ловится вовсе -- ловится лишь тем, что файл
        на диске изменился. Здесь манифест собирается заново.
        """
        # Собирается заново ТОЛЬКО часть про комплект: истории для неё не
        # нужно, поэтому проверка работает и в мелком клоне CI.
        rebuilt = blobs.build_kit_own_files(REPO_ROOT)
        self.assertTrue(rebuilt,
                        'a rebuilt manifest with no kit files would let the '
                        'kit execute its own code unpinned')
        for name in PS_FILES:
            self.assertIn('ops/pilot_useful_area_001/%s' % name, rebuilt, name)
        self.assertEqual(rebuilt,
                         common.load_product_blobs()['kit_own_files'],
                         'the stored manifest is stale')

    def test_a_new_kit_file_outside_the_manifest_is_caught(self):
        """Отрицательный контроль: файл комплекта без записи в манифесте."""
        manifest = copy.deepcopy(common.load_product_blobs())
        removed = manifest['kit_own_files'].pop(
            'ops/pilot_useful_area_001/pilot_common.py')
        self.assertTrue(removed)
        problems = blobs.check_against_worktree(manifest, REPO_ROOT)
        self.assertIn('NOT_IN_MANIFEST:ops/pilot_useful_area_001/'
                      'pilot_common.py', problems)


# ═══ Р3. run.json проверяется и не может перенаправить запись ════════════════

class RunManifestIsValidated(unittest.TestCase):

    def test_the_module_validates_every_field(self):
        text = code_text('PilotKit.psm1')
        self.assertIn('function Test-PilotRunManifest', text)
        for code in ('WRONG_KIT', 'WRONG_KIT_VERSION', 'RUN_ID_MISMATCH',
                     'PRODUCT_SHA_MISMATCH', 'TARGET_DAY_MISMATCH',
                     'MALFORMED_APPROVED_KIT_SHA', 'MACHINE_ABSENT',
                     'KIT_CHECKOUT_IS_NOT_THE_KIT_ROOT',
                     'RUN_ROOT_IS_NOT_THE_RUN_DIRECTORY',
                     'RUN_ROOT_IS_INSIDE_A_CHECKOUT', 'STEPS_ABSENT'):
            self.assertIn("'%s'" % code, text, code)

    def test_the_run_directory_is_created_atomically(self):
        text = code_text('PilotKit.psm1')
        self.assertIn('[System.IO.Directory]::CreateDirectory($directory)',
                      text)
        self.assertIn('already exists', text)
        self.assertNotIn('New-Item -ItemType Directory -Path $target -Force',
                         text)

    def test_recording_a_step_validates_first(self):
        text = code_text('PilotKit.psm1')
        step = text.index('function Set-PilotRunStep')
        body = text[step:step + 2000]
        self.assertIn('Assert-PilotRunManifest', body)
        self.assertLess(body.index('Assert-PilotRunManifest'),
                        body.index('Write-PilotJson'))

    def test_reading_a_run_validates_it(self):
        text = code_text('PilotKit.psm1')
        get = text.index('function Get-PilotRun {')
        body = text[get:get + 1500]
        self.assertIn('Assert-PilotRunManifest', body)


# ═══ Р4. Резервная копия -- каталог этого запуска ════════════════════════════

class BackupBelongsToTheRun(unittest.TestCase):

    def test_the_deploy_backs_up_into_the_run_directory(self):
        text = code_text('STAGING_DEPLOY_AND_MIGRATE.ps1')
        self.assertIn("$BackupDir = Join-Path $runRoot 'backup'", text)
        self.assertIn('New-PilotRunBackup', text)

    def test_the_shared_daily_directory_is_no_longer_used(self):
        """Отрицательный контроль: общий каталог -- источник чужого файла."""
        text = code_text('STAGING_DEPLOY_AND_MIGRATE.ps1')
        self.assertNotIn('transport-report-backups\\staging\\daily', text)
        self.assertNotIn('$before -notcontains', text)
        self.assertNotIn('Sort-Object LastWriteTime -Descending', text)

    def test_the_helper_refuses_a_directory_that_is_not_empty(self):
        text = code_text('PilotKit.psm1')
        self.assertIn('if ($existing.Count -ne 0) {', text)
        self.assertIn('It belongs to this run alone', text)

    def test_the_helper_accepts_exactly_one_produced_file(self):
        text = code_text('PilotKit.psm1')
        self.assertIn('if ($produced.Count -ne 1) {', text)

    def test_the_rollback_accepts_only_this_runs_backup(self):
        text = code_text('STAGING_ROLLBACK.ps1')
        self.assertIn("$runBackupDir = Join-Path $run.run_root 'backup'", text)
        self.assertIn('BACKUP_IS_NOT_IN_THIS_RUNS_BACKUP_DIRECTORY', text)


# ═══ Р5. Откат проверяет весь конверт и точные цели ══════════════════════════

class RollbackChecksEverythingFirst(unittest.TestCase):

    def setUp(self):
        self.text = code_text('STAGING_ROLLBACK.ps1')

    def test_every_named_refusal_exists(self):
        for code in ('DEPLOY_ENVELOPE_WRONG_KIT',
                     'DEPLOY_ENVELOPE_WRONG_KIT_VERSION',
                     'DEPLOY_ENVELOPE_WRONG_EVIDENCE_KIND',
                     'DEPLOY_ENVELOPE_RUN_ID_MISMATCH',
                     'DEPLOY_ENVELOPE_PRODUCT_SHA_MISMATCH',
                     'DEPLOY_ENVELOPE_TARGET_DAY_MISMATCH',
                     'DEPLOY_ENVELOPE_MALFORMED_TIMESTAMP',
                     'KIT_CHECKOUT_IS_NOT_APPROVED',
                     'RUN_KIT_SHA_IS_NOT_APPROVED',
                     'DEPLOY_KIT_SHA_IS_NOT_APPROVED',
                     'RECORDED_STAGING_ROOT_IS_NOT_THE_STAGING_ROOT',
                     'RECORDED_STAGING_DB_IS_NOT_THE_STAGING_DATABASE',
                     'BACKUP_IS_NOT_IN_THIS_RUNS_BACKUP_DIRECTORY',
                     'RECORDED_SHA_BEFORE_IS_NOT_A_REVISION',
                     'RECORDED_SHA_BEFORE_IS_NOT_IN_THE_CHECKOUT',
                     'SHA_BEFORE_DISAGREES_WITH_THE_PREFLIGHT_RECORD',
                     'STAGING_WORKTREE_IS_DIRTY'):
            self.assertIn(code, self.text, code)

    def test_the_targets_are_compared_by_equality_not_containment(self):
        """Отрицательный контроль: «внутри staging» -- это не «и есть staging»."""
        self.assertIn('Test-PilotPathEquals -Left ([string]$payload.staging_root)',
                      self.text)
        self.assertIn('Test-PilotPathEquals -Left ([string]$payload.staging_db)',
                      self.text)
        self.assertNotIn('Assert-PilotStagingPath -Path $payload.staging_db',
                         self.text)

    def test_the_allowed_deploy_phases_are_named(self):
        self.assertIn('$rollbackablePhases = @(', self.text)
        self.assertIn('DEPLOY_PHASE_IS_NOT_ROLLBACKABLE', self.text)

    def test_everything_is_refused_before_the_service_is_touched(self):
        self.assertLess(self.text.index('ROLLBACK_PRECONDITIONS=PASS'),
                        self.text.index('Stop-Service'))
        self.assertLess(self.text.index('$refusals.Count -gt 0'),
                        self.text.index('Stop-Service'))
        self.assertLess(self.text.index('$sha -ne $payload.backup_sha256'),
                        self.text.index('Stop-Service'))
        self.assertLess(self.text.index('Move-Item'),
                        self.text.index('Copy-Item'))


# ═══ Р6. Время пересчёта не теряется ════════════════════════════════════════

class RecalculationIsTimed(unittest.TestCase):

    def runs(self, seconds):
        payload = {'label': 'apply-1', 'summary': {}, 'seconds': seconds}
        return [envelope('recalc:apply-1', payload, common.new_run_id())]

    def test_the_duration_comes_from_the_evidence(self):
        self.assertEqual(report_mod.recalculation_seconds(self.runs(1.25)),
                         1.25)

    def test_a_missing_duration_is_not_a_duration(self):
        self.assertIsNone(report_mod.recalculation_seconds(self.runs(None)))
        self.assertIsNone(report_mod.recalculation_seconds([]))

    def test_nan_and_infinity_are_not_durations(self):
        for bad in (float('nan'), float('inf'), float('-inf'), -1.0):
            self.assertIsNone(report_mod.recalculation_seconds(self.runs(bad)),
                              repr(bad))

    def test_the_report_carries_the_measured_duration(self):
        """Не «поле существует», а «в нём измеренное число этого запуска»."""
        report, markdown = VerdictContract._build(None, None)
        self.assertEqual(report['recalculation_seconds'], 1.234)
        self.assertIn('1.234', markdown)
        for item in report['conditions']:
            if item['code'] == 'RECALCULATION_WAS_TIMED':
                self.assertTrue(item['passed'])
                break
        else:
            raise AssertionError('RECALCULATION_WAS_TIMED is gone')

    def test_a_report_without_a_duration_fails_the_condition(self):
        shared = shared_evidence()
        runs = copy.deepcopy(shared['runs'])
        for run in runs:
            if run['payload'].get('label') == 'apply-1':
                run['payload']['seconds'] = None
        report, _markdown = report_mod.build_report(
            copy.deepcopy(shared['preflight']), copy.deepcopy(shared['deploy']),
            copy.deepcopy(shared['collect']), runs,
            copy.deepcopy(shared['snapshot']), None, None, [],
            run_id=shared['run_id'], kit_sha=KIT_SHA_FIXTURE)
        self.assertEqual(report['verdict'], 'REJECT')
        self.assertIn('RECALCULATION_WAS_TIMED', report['verdict_reasons'])
        self.assertIsNone(report['recalculation_seconds'])

    def test_the_report_no_longer_takes_a_hand_typed_duration(self):
        with open(os.path.join(KIT_DIR, 'pilot_report.py'),
                  encoding='utf-8') as handle:
            text = handle.read()
        self.assertNotIn('--recalc-seconds', text)
        self.assertNotIn('--recalc-seconds',
                         code_text('STAGING_PILOT_REPORT.ps1'))

    def test_the_recalculation_records_the_seconds_it_measured(self):
        text = code_text('STAGING_RECALCULATE_AND_VERIFY.ps1')
        self.assertIn("'--seconds', ([string]$applySeconds)", text)


# ═══ Р7. Вердикт -- часть контракта ═════════════════════════════════════════

class VerdictContract(unittest.TestCase):

    def test_the_exit_codes_are_distinct_and_named(self):
        codes = common.VERDICT_EXIT_CODES
        self.assertEqual(sorted(codes.values()), [0, 10, 11, 12])
        self.assertEqual(len(set(codes.values())), 4)
        self.assertNotIn(common.EXIT_ERROR, codes.values())

    def test_the_tool_exits_with_the_verdict_it_reports(self):
        """Настоящий вызов инструмента, а не чтение его исходника."""
        directory = tempfile.mkdtemp(prefix='pilot_exit_')
        try:
            shared = shared_evidence()
            run_id = shared['run_id']
            paths = {}
            for name, document in (
                    ('preflight', shared['preflight']),
                    ('deploy', shared['deploy']),
                    ('collect', shared['collect']),
                    ('snapshot', shared['snapshot'])):
                paths[name] = os.path.join(directory, '%s.json' % name)
                common.write_evidence(paths[name], document)
            recalc_paths = []
            for index, run in enumerate(shared['runs']):
                path = os.path.join(directory, 'recalc_%d.json' % index)
                common.write_evidence(path, run)
                recalc_paths.append(path)

            def call(*extra):
                arguments = [os.path.join(KIT_DIR, 'pilot_report.py'),
                             '--preflight', paths['preflight'],
                             '--deploy', paths['deploy'],
                             '--collect', paths['collect'],
                             '--staging-snapshot', paths['snapshot'],
                             '--run-id', run_id, '--kit-sha', KIT_SHA_FIXTURE,
                             '--out-json', os.path.join(directory, 'r.json'),
                             '--out-md', os.path.join(directory, 'r.md')]
                for path in recalc_paths:
                    arguments += ['--recalc', path]
                result = run_python(*(arguments + list(extra)))
                verdict = ''
                for line in result.stdout.splitlines():
                    if line.startswith('PILOT_VERDICT='):
                        verdict = line.split('=', 1)[1]
                return result.returncode, verdict

            code, verdict = call()
            self.assertEqual(verdict, 'TECHNICAL_GO')
            self.assertEqual(code, common.EXIT_VERDICT_TECHNICAL_GO)

            code, verdict = call('--owner-share-threshold', '0.9',
                                 '--owner-dji-delta-percent', '999')
            self.assertEqual(verdict, 'GO')
            self.assertEqual(code, common.EXIT_VERDICT_GO)

            code, verdict = call('--owner-share-threshold', '0.0',
                                 '--owner-dji-delta-percent', '0')
            self.assertEqual(verdict, 'ADJUST')
            self.assertEqual(code, common.EXIT_VERDICT_ADJUST)

            broken = copy.deepcopy(shared['deploy'])
            broken['payload']['phase'] = 'smoke-test-failed'
            common.write_evidence(paths['deploy'], broken)
            code, verdict = call()
            self.assertEqual(verdict, 'REJECT')
            self.assertEqual(code, common.EXIT_VERDICT_REJECT,
                             'a REJECT that exits 0 is what let the caller '
                             'print PASS after it')
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def test_the_script_never_prints_pass_for_a_rejected_pilot(self):
        text = code_text('STAGING_PILOT_REPORT.ps1')
        self.assertNotIn('STAGING_PILOT_REPORT=PASS', text)
        self.assertIn('REPORT_GENERATED=yes', text)
        self.assertIn('PILOT_VERDICT=', text)
        self.assertIn("if ([string]$report.verdict -eq 'REJECT') {", text)

    def test_the_script_checks_the_exit_code_against_the_verdict(self):
        text = code_text('STAGING_PILOT_REPORT.ps1')
        self.assertIn("@{ 'GO' = 0; 'TECHNICAL_GO' = 10; 'ADJUST' = 11; "
                      "'REJECT' = 12 }", text)
        self.assertIn('if ($reportCode -ne $expectedCode) {', text)

    def test_production_is_never_authorised_by_this_kit(self):
        for threshold, limit in ((None, None), (0.9, 999.0), (0.0, 0.0)):
            report, _markdown = VerdictContract._build(threshold, limit)
            self.assertFalse(report['production_rollout_authorised'],
                             '%s/%s' % (threshold, limit))
            self.assertTrue(report['release_gate_action_required'])

    def test_the_owner_thresholds_reject_nan_and_out_of_range(self):
        for bad in (float('nan'), float('inf'), -0.01, 1.01):
            with self.assertRaises(common.ProbeError):
                common.owner_share_threshold(bad)
        for bad in (float('nan'), float('inf'), -1.0):
            with self.assertRaises(common.ProbeError):
                common.owner_delta_percent(bad)
        self.assertEqual(common.owner_share_threshold(0.0), 0.0)
        self.assertEqual(common.owner_share_threshold(1.0), 1.0)
        self.assertEqual(common.owner_delta_percent(0.0), 0.0)

    def test_a_nan_threshold_would_have_passed_every_share(self):
        """Отрицательный контроль: почему NaN опасен, а не просто странен."""
        self.assertFalse(0.99 > float('nan'),
                         'a NaN threshold lets every share through, which is '
                         'why it must be refused at the boundary')

    def test_the_readme_prints_no_example_threshold(self):
        with open(os.path.join(KIT_DIR, 'README.md'), encoding='utf-8') as handle:
            text = handle.read()
        self.assertNotIn('-OwnerShareThreshold 0', text)
        self.assertNotIn('-OwnerDjiDeltaPercent 9', text)

    @staticmethod
    def _build(threshold, limit):
        shared = shared_evidence()
        return report_mod.build_report(
            copy.deepcopy(shared['preflight']), copy.deepcopy(shared['deploy']),
            copy.deepcopy(shared['collect']), copy.deepcopy(shared['runs']),
            copy.deepcopy(shared['snapshot']), threshold, limit, [],
            run_id=shared['run_id'], kit_sha=KIT_SHA_FIXTURE)


# ═══ Р8. Доказательства в отчёте fail-closed ════════════════════════════════

class EvidenceIsFailClosed(unittest.TestCase):

    def full(self):
        return {name: 'a' * 64 for name in report_mod.REQUIRED_FINGERPRINTS}

    def test_all_five_fingerprints_are_required(self):
        self.assertEqual(len(report_mod.REQUIRED_FINGERPRINTS), 5)
        self.assertTrue(report_mod.area_ha_unchanged(self.full()))

    def test_a_single_missing_fingerprint_breaks_the_claim(self):
        """Отрицательный контроль: меньше улик -- не сильнее доказательство."""
        for name in report_mod.REQUIRED_FINGERPRINTS:
            broken = self.full()
            broken[name] = None
            self.assertFalse(report_mod.area_ha_unchanged(broken), name)

    def test_one_surviving_fingerprint_is_not_a_proof(self):
        lonely = {name: None for name in report_mod.REQUIRED_FINGERPRINTS}
        lonely['staging_after_recalculation'] = 'a' * 64
        self.assertFalse(report_mod.area_ha_unchanged(lonely))
        # The rule the first edition used would have said "unchanged" here.
        present = [value for value in lonely.values() if value]
        self.assertTrue(len(set(present)) <= 1 and bool(present),
                        'the old rule must still say yes, else this test '
                        'proves nothing about the change')

    def test_one_differing_fingerprint_breaks_the_claim(self):
        broken = self.full()
        broken['staging_after_migration'] = 'b' * 64
        self.assertFalse(report_mod.area_ha_unchanged(broken))

    def test_the_smoke_condition_needs_all_four_facts(self):
        base = {'smoke_test_ok': True, 'smoke_test_status': 200,
                'smoke_test_path': '/login', 'smoke_page_marker_seen': True}
        for field, wrong in (('smoke_test_ok', False),
                             ('smoke_test_status', 404),
                             ('smoke_test_path', '/'),
                             ('smoke_page_marker_seen', False)):
            broken = dict(base)
            broken[field] = wrong
            self.assertFalse(self._smoke(broken), field)
        self.assertTrue(self._smoke(base))

    def _smoke(self, deploy_payload):
        deploy = envelope('deploy', deploy_payload, common.new_run_id())
        conditions = report_mod.build_conditions(
            envelope('preflight', {}, common.new_run_id()), deploy,
            envelope('collect:summary', {}, common.new_run_id()), [],
            envelope('db-probe:snapshot', {}, common.new_run_id()))
        for item in conditions:
            if item['code'] == 'SMOKE_TEST_OK':
                return item['passed']
        raise AssertionError('SMOKE_TEST_OK is gone from the conditions')

    def test_a_lone_200_is_not_the_application(self):
        self.assertEqual(common.SMOKE_PAGE_MARKER, 'vs-login-form')
        with open(os.path.join(REPO_ROOT, 'templates', 'login.html'),
                  encoding='utf-8') as handle:
            template = handle.read()
        self.assertIn(common.SMOKE_PAGE_MARKER, template,
                      'the marker must exist in the real login template, '
                      'else the smoke test asserts a string nobody serves')

    def test_the_module_reads_the_body_and_looks_for_the_marker(self):
        text = code_text('PilotKit.psm1')
        self.assertIn('$script:SmokePageMarker', text)
        self.assertIn('PAGE_IS_NOT_THE_APPLICATION_LOGIN_FORM', text)
        self.assertIn('$result.Body', text)

    def test_the_deploy_records_the_marker(self):
        text = code_text('STAGING_DEPLOY_AND_MIGRATE.ps1')
        self.assertIn('$payload.smoke_page_marker_seen = [bool]$smoke.marker_seen',
                      text)

# ═══ Р8b. Живой smoke-тест против настоящего HTTP-сервера ═══════════════════

LOGIN_BODY = ('<html><body><form class="vs-login-form" method="post">'
              '<input id="vsLoginField" name="username"></form></body></html>')
GENERIC_BODY = ('<html><body><h1>Service temporarily unavailable</h1>'
                '<p>Maintenance in progress.</p></body></html>')


class _SmokeFixtureHandler(BaseHTTPRequestHandler):
    """Пять ответов, которые smoke-тест обязан различать."""

    def do_GET(self):                                    # noqa: N802
        if self.path == '/login':
            self._send(200, LOGIN_BODY)
        elif self.path == '/generic':
            # 200 без признака приложения: страница обслуживания.
            self._send(200, GENERIC_BODY)
        elif self.path == '/redirect-internal':
            self._redirect('/login')
        elif self.path == '/redirect-external':
            self._redirect(common.PRODUCTION_URL + '/login')
        else:
            self._send(404, '<html><body>not found</body></html>')

    def _send(self, status, body):
        payload = body.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _redirect(self, location):
        self.send_response(302)
        self.send_header('Location', location)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def log_message(self, *_args):
        return


@contextlib.contextmanager
def smoke_fixture_server():
    server = HTTPServer(('127.0.0.1', 0), _SmokeFixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield 'http://127.0.0.1:%d' % server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


LIVE_SMOKE_SCRIPT = r'''
param([string]$Module, [string]$BaseUrl)
Import-Module $Module -Force
$marker = 'vs-login-form'
$results = [ordered]@{}
function Probe([string]$path) {
    $r = Invoke-PilotSmokeEndpoint -BaseUrl $BaseUrl -Path $path -Authority $BaseUrl `
                                   -Marker $marker -TimeoutSec 10 -Attempts 2 -DelaySeconds 1
    return [ordered]@{ ok = [bool]$r.ok; status = $r.status; reason = [string]$r.reason
                       marker = [bool]$r.marker_seen; followed = [bool]$r.followed_redirect }
}
$results['correct_200']      = Probe '/login'
$results['wrong_200']        = Probe '/generic'
$results['not_found']        = Probe '/missing'
$results['redirect_internal']= Probe '/redirect-internal'
$results['redirect_external']= Probe '/redirect-external'
$results | ConvertTo-Json -Depth 5 -Compress
'''


@unittest.skipIf(PWSH is None, 'no PowerShell in this environment')
class LiveSmokeTestAgainstARealServer(unittest.TestCase):
    """Не «код выглядит правильно», а настоящий запрос и настоящий ответ."""

    def run_live(self):
        directory = tempfile.mkdtemp(prefix='pilot_live_')
        try:
            path = os.path.join(directory, 'live.ps1')
            with open(path, 'w', encoding='utf-8', newline='\n') as handle:
                handle.write(LIVE_SMOKE_SCRIPT)
            with smoke_fixture_server() as base:
                result = subprocess.run(
                    [PWSH, '-NoProfile', '-File', path,
                     '-Module', os.path.join(KIT_DIR, 'PilotKit.psm1'),
                     '-BaseUrl', base],
                    capture_output=True, text=True, cwd=REPO_ROOT)
            self.assertEqual(result.returncode, 0,
                             '%s\n%s' % (result.stdout, result.stderr))
            line = [row for row in result.stdout.splitlines()
                    if row.strip().startswith('{')][-1]
            return json.loads(line)
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def test_the_five_cases_are_told_apart(self):
        state = self.run_live()

        # The application's login page: 200 AND the marker.
        self.assertTrue(state['correct_200']['ok'], state['correct_200'])
        self.assertEqual(state['correct_200']['status'], 200)
        self.assertTrue(state['correct_200']['marker'])

        # A maintenance page also answers 200 -- and must not pass.
        self.assertFalse(state['wrong_200']['ok'], state['wrong_200'])
        self.assertEqual(state['wrong_200']['status'], 200)
        self.assertFalse(state['wrong_200']['marker'])
        self.assertEqual(state['wrong_200']['reason'],
                         'PAGE_IS_NOT_THE_APPLICATION_LOGIN_FORM')

        # 404 is a failure, not "the server answered".
        self.assertFalse(state['not_found']['ok'])
        self.assertEqual(state['not_found']['status'], 404)

        # A redirect inside the site is followed, and the landing page is
        # then held to the same two conditions.
        self.assertTrue(state['redirect_internal']['ok'],
                        state['redirect_internal'])
        self.assertTrue(state['redirect_internal']['followed'])
        self.assertEqual(state['redirect_internal']['status'], 200)

        # A redirect off the site is refused before it is followed.
        self.assertFalse(state['redirect_external']['ok'])
        self.assertEqual(state['redirect_external']['reason'],
                         'REDIRECT_LEAVES_STAGING')
        self.assertFalse(state['redirect_external']['followed'])

    def test_the_staging_guard_still_wraps_the_endpoint(self):
        """Разделение не ослабило гвардию: адрес по-прежнему обязан быть площадкой."""
        text = code_text('PilotKit.psm1')
        body = text[text.index('function Invoke-PilotSmokeTest'):]
        body = body[:body.index('function Invoke-PilotSmokeEndpoint')]
        self.assertIn('Assert-PilotStagingUrl -Url $BaseUrl', body)
        self.assertIn('-Authority $script:StagingUrl', body)
        self.assertIn('-Marker $script:SmokePageMarker', body)


# ═══ Д1. Загруженный модуль остаётся старым ═════════════════════════════════

MODULE_STALENESS_SCRIPT = r'''
param([string]$ModulePath)
# Загружаем модуль, меняем ЕГО ФАЙЛ на диске и зовём функцию снова.
Import-Module $ModulePath -Force
$before = Get-PilotStalenessProbe
$text = [System.IO.File]::ReadAllText($ModulePath)
$text = $text.Replace("'ORIGINAL'", "'REPLACED'")
[System.IO.File]::WriteAllText($ModulePath, $text)
$after = Get-PilotStalenessProbe
[ordered]@{ before = $before; after = $after
            on_disk = ([System.IO.File]::ReadAllText($ModulePath).Contains("'REPLACED'")) } |
    ConvertTo-Json -Compress
'''


@unittest.skipIf(PWSH is None, 'no PowerShell in this environment')
class ARunningScriptCannotUpdateItself(unittest.TestCase):
    """Обновить с диска код, который уже исполняется, нельзя.

    [REASON]: скрипт BAK-TEX11 импортировал PilotKit, затем делал fetch/merge и
    после этого проверял HEAD. Проверка проходила, но выполнялся при этом СТАРЫЙ
    модуль и старый текст скрипта: PowerShell читает файл один раз, при импорте.
    То есть «HEAD равен одобренной ревизии» не доказывало, что защитные проверки
    исполнялись из одобренной ревизии. Проверка порядка строк это не поймала бы:
    порядок как раз был «сначала обновить, потом проверить».
    """

    def test_a_loaded_module_keeps_its_old_definition(self):
        directory = tempfile.mkdtemp(prefix='pilot_stale_')
        try:
            module = os.path.join(directory, 'Staleness.psm1')
            with open(module, 'w', encoding='utf-8', newline='\n') as handle:
                handle.write("function Get-PilotStalenessProbe { return "
                             "'ORIGINAL' }\n"
                             "Export-ModuleMember -Function "
                             "Get-PilotStalenessProbe\n")
            script = os.path.join(directory, 'probe.ps1')
            with open(script, 'w', encoding='utf-8', newline='\n') as handle:
                handle.write(MODULE_STALENESS_SCRIPT)
            result = subprocess.run(
                [PWSH, '-NoProfile', '-File', script, '-ModulePath', module],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0,
                             '%s\n%s' % (result.stdout, result.stderr))
            line = [row for row in result.stdout.splitlines()
                    if row.strip().startswith('{')][-1]
            state = json.loads(line)

            self.assertTrue(state['on_disk'],
                            'the file on disk must really have changed, else '
                            'this test proves nothing')
            self.assertEqual(state['before'], 'ORIGINAL')
            self.assertEqual(
                state['after'], 'ORIGINAL',
                'the RUNNING session kept the old definition even though the '
                'file changed -- which is exactly why a script must not '
                'update the checkout it is executing from')
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def test_the_collector_script_never_updates_its_own_checkout(self):
        text = code_text('BAK_TEX11_DJI_COLLECT_TO_STAGING.ps1')
        for forbidden in ("'fetch'", "'merge'", "'--ff-only'",
                          'SkipCodeUpdate'):
            self.assertNotIn(forbidden, text,
                             'the script must not update the checkout it '
                             'runs from: %s' % forbidden)

    def test_the_collector_script_only_verifies(self):
        text = code_text('BAK_TEX11_DJI_COLLECT_TO_STAGING.ps1')
        self.assertIn('Assert-PilotWorktreeClean -Repo $K.CollectorRepo', text)
        self.assertIn('$head -ne $ApprovedKitSha', text)
        self.assertIn("'--role', 'collector'", text)
        # Everything above happens before the first file of ours exists.
        self.assertLess(text.index('$head -ne $ApprovedKitSha'),
                        text.index('New-Item -ItemType Directory'))

    def test_the_readme_no_longer_claims_the_step_updates_itself(self):
        with open(os.path.join(KIT_DIR, 'README.md'), encoding='utf-8') as handle:
            text = handle.read()
        self.assertNotIn('в нужную ревизию его переведёт сам скрипт', text)


# ═══ Д2. Поле area_ha_unchanged считается той же функцией ═══════════════════

class AreaFieldMatchesTheCondition(unittest.TestCase):
    """Отчёт не может одновременно говорить «не менялась» и REJECT."""

    def build(self, mutate=None):
        shared = shared_evidence()
        preflight = copy.deepcopy(shared['preflight'])
        deploy = copy.deepcopy(shared['deploy'])
        snapshot = copy.deepcopy(shared['snapshot'])
        if mutate:
            mutate(preflight, deploy, snapshot)
        return report_mod.build_report(
            preflight, deploy, copy.deepcopy(shared['collect']),
            copy.deepcopy(shared['runs']), snapshot, None, None, [],
            run_id=shared['run_id'], kit_sha=KIT_SHA_FIXTURE)

    def condition(self, report):
        for item in report['conditions']:
            if item['code'] == 'AREA_HA_UNCHANGED':
                return item['passed']
        raise AssertionError('AREA_HA_UNCHANGED is gone from the conditions')

    def test_all_five_present_and_equal_gives_true(self):
        report, markdown = self.build()
        self.assertTrue(report['area_ha_unchanged'])
        self.assertTrue(self.condition(report))
        self.assertIn('drone_flights.area_ha не менялась | да', markdown)

    def test_any_one_missing_gives_false_and_reject(self):
        cases = {
            'production_copy_before_migration':
                lambda p, d, s: p['payload'].pop('area_ha_before'),
            'production_copy_after_migration':
                lambda p, d, s: p['payload'].pop('area_ha_after'),
            'staging_before_migration':
                lambda p, d, s: d['payload'].pop('area_ha_before'),
            'staging_after_migration':
                lambda p, d, s: d['payload'].pop('area_ha_after'),
            'staging_after_recalculation':
                lambda p, d, s: s['payload'].pop('area_ha'),
        }
        for name, mutate in cases.items():
            report, markdown = self.build(mutate)
            self.assertFalse(report['area_ha_unchanged'], name)
            self.assertFalse(self.condition(report), name)
            self.assertEqual(report['verdict'], 'REJECT', name)
            self.assertIn('AREA_HA_UNCHANGED', report['verdict_reasons'], name)
            self.assertIn('drone_flights.area_ha не менялась | НЕТ', markdown,
                          name)

    def test_all_five_missing_gives_false_and_reject(self):
        def drop_everything(preflight, deploy, snapshot):
            preflight['payload'].pop('area_ha_before')
            preflight['payload'].pop('area_ha_after')
            deploy['payload'].pop('area_ha_before')
            deploy['payload'].pop('area_ha_after')
            snapshot['payload'].pop('area_ha')

        report, markdown = self.build(drop_everything)
        # Пять None давали множество из одного элемента, и старое выражение
        # объявляло площадь неизменной именно при ПОЛНОМ отсутствии улик.
        self.assertFalse(report['area_ha_unchanged'])
        self.assertFalse(self.condition(report))
        self.assertEqual(report['verdict'], 'REJECT')
        self.assertIn('drone_flights.area_ha не менялась | НЕТ', markdown)

    def test_one_differing_gives_false_and_reject(self):
        def change_one(preflight, deploy, snapshot):
            deploy['payload']['area_ha_after'] = dict(
                deploy['payload']['area_ha_after'])
            deploy['payload']['area_ha_after']['sha256'] = 'f' * 64

        report, markdown = self.build(change_one)
        self.assertFalse(report['area_ha_unchanged'])
        self.assertFalse(self.condition(report))
        self.assertEqual(report['verdict'], 'REJECT')
        self.assertIn('drone_flights.area_ha не менялась | НЕТ', markdown)

    def test_the_field_and_the_condition_never_disagree(self):
        for mutate in (None,
                       lambda p, d, s: d['payload'].pop('area_ha_after'),
                       lambda p, d, s: s['payload'].pop('area_ha')):
            report, _markdown = self.build(mutate)
            self.assertEqual(report['area_ha_unchanged'],
                             self.condition(report))



# ═══ Д3. Переданный неверный порог не исчезает ══════════════════════════════

THRESHOLD_SCRIPT = r'''
param([string]$Module, [double]$Share, [double]$Delta, [string]$Which)
Import-Module $Module -Force
# Повторяет ровно тот способ, которым скрипт отчёта решает, передавать ли
# параметр дальше.
function Build-Arguments {
    [CmdletBinding()]
    param([double]$OwnerShareThreshold, [double]$OwnerDjiDeltaPercent)
    $arguments = @()
    if ($PSBoundParameters.ContainsKey('OwnerShareThreshold')) {
        $arguments += @('--owner-share-threshold',
                        $OwnerShareThreshold.ToString([System.Globalization.CultureInfo]::InvariantCulture))
    }
    if ($PSBoundParameters.ContainsKey('OwnerDjiDeltaPercent')) {
        $arguments += @('--owner-dji-delta-percent',
                        $OwnerDjiDeltaPercent.ToString([System.Globalization.CultureInfo]::InvariantCulture))
    }
    return ,$arguments
}
switch ($Which) {
    'none'  { $result = Build-Arguments }
    'share' { $result = Build-Arguments -OwnerShareThreshold $Share }
    'delta' { $result = Build-Arguments -OwnerDjiDeltaPercent $Delta }
    'both'  { $result = Build-Arguments -OwnerShareThreshold $Share -OwnerDjiDeltaPercent $Delta }
}
[ordered]@{ args = @($result) } | ConvertTo-Json -Compress
'''


@unittest.skipIf(PWSH is None, 'no PowerShell in this environment')
class ExplicitThresholdsReachPython(unittest.TestCase):
    """Переданное значение обязано дойти до единственной проверки, а не пропасть.

    [REASON]: отрицательное число служило признаком «параметр не задан», и
    условие `-ge 0` решало, передавать ли его дальше. Поэтому явные -0.01, -1
    и NaN не отвергались как неверные -- они молча исчезали и превращались в
    «правило владельца не названо», то есть в TECHNICAL_GO вместо ошибки ввода.
    """

    def bound(self, which, share=0.0, delta=0.0):
        directory = tempfile.mkdtemp(prefix='pilot_thr_')
        try:
            path = os.path.join(directory, 'thr.ps1')
            with open(path, 'w', encoding='utf-8', newline='\n') as handle:
                handle.write(THRESHOLD_SCRIPT)
            result = subprocess.run(
                [PWSH, '-NoProfile', '-File', path,
                 '-Module', os.path.join(KIT_DIR, 'PilotKit.psm1'),
                 '-Share', str(share), '-Delta', str(delta), '-Which', which],
                capture_output=True, text=True, cwd=REPO_ROOT)
            self.assertEqual(result.returncode, 0,
                             '%s\n%s' % (result.stdout, result.stderr))
            line = [row for row in result.stdout.splitlines()
                    if row.strip().startswith('{')][-1]
            return json.loads(line)['args']
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def test_an_unbound_parameter_is_not_passed(self):
        self.assertEqual(self.bound('none'), [])

    def test_a_negative_share_is_passed_on_rather_than_dropped(self):
        arguments = self.bound('share', share=-0.01)
        self.assertIn('--owner-share-threshold', arguments)
        self.assertIn('-0.01', arguments)

    def test_a_negative_delta_is_passed_on_rather_than_dropped(self):
        arguments = self.bound('delta', delta=-1)
        self.assertIn('--owner-dji-delta-percent', arguments)
        self.assertIn('-1', arguments)

    def test_zero_is_passed_on(self):
        """Ноль -- законное значение обоих правил, а не «не задано»."""
        self.assertIn('--owner-share-threshold', self.bound('share', share=0))
        self.assertIn('--owner-dji-delta-percent', self.bound('delta', delta=0))

    def test_the_number_is_formatted_invariantly(self):
        """Русская локаль сервера иначе прислала бы python «0,5»."""
        arguments = self.bound('share', share=0.5)
        self.assertIn('0.5', arguments)
        self.assertNotIn('0,5', arguments)

    def test_the_script_binds_by_presence_not_by_sign(self):
        text = code_text('STAGING_PILOT_REPORT.ps1')
        self.assertIn("$PSBoundParameters.ContainsKey('OwnerShareThreshold')",
                      text)
        self.assertIn("$PSBoundParameters.ContainsKey('OwnerDjiDeltaPercent')",
                      text)
        self.assertNotIn('$OwnerShareThreshold -ge 0', text)
        self.assertNotIn('$OwnerDjiDeltaPercent -ge 0', text)
        self.assertNotIn('$OwnerShareThreshold = -1', text)
        # [REASON]: обе величины, а не «где-то в файле встречается слово
        # InvariantCulture». Подмена одной из них на `[string]$x` вернула бы
        # культурное форматирование: на ru-RU сервере python получил бы «0,5».
        self.assertIn('$OwnerShareThreshold.ToString($invariant)', text)
        self.assertIn('$OwnerDjiDeltaPercent.ToString($invariant)', text)
        self.assertNotIn('([string]$OwnerShareThreshold)', text)
        self.assertNotIn('([string]$OwnerDjiDeltaPercent)', text)

    def test_python_refuses_every_bad_value_that_now_reaches_it(self):
        """Единственная проверка диапазона -- в python, и она видит их все."""
        for bad in ('-0.01', '1.01', 'NaN', 'Infinity', '-Infinity'):
            with self.assertRaises(common.ProbeError, msg=bad):
                common.owner_share_threshold(float(bad))
        for bad in ('-1', 'NaN', 'Infinity', '-Infinity'):
            with self.assertRaises(common.ProbeError, msg=bad):
                common.owner_delta_percent(float(bad))
        self.assertEqual(common.owner_share_threshold(0.0), 0.0)
        self.assertEqual(common.owner_share_threshold(1.0), 1.0)
        self.assertEqual(common.owner_delta_percent(0.0), 0.0)



# ═══ Д4. run.json проверяется целиком, а не почти ═══════════════════════════

MANIFEST_SCRIPT = r'''
param([string]$Module, [string]$RunsRoot, [string]$RunId, [string]$Machine)
Import-Module $Module -Force
$kit = ('a' * 40)
function New-Case([hashtable]$Override) {
    $m = [ordered]@{
        kit              = 'DRONE-USEFUL-AREA-PILOT-001'
        kit_version      = '2'
        run_id           = $RunId
        approved_kit_sha = $kit
        measured_kit_sha = $kit
        product_sha      = 'c3e6a12ab95117710eeea5e05133f5cd548b698e'
        target_day       = '2026-06-05'
        created_utc      = '2026-09-03T10:00:00Z'
        machine          = $Machine
        kit_checkout     = 'C:\vehicle-soft-pilot-kit'
        run_root         = (Get-PilotRunDirectory -RunsRoot $RunsRoot -RunId $RunId)
        steps            = [ordered]@{}
    }
    foreach ($key in $Override.Keys) {
        if ($null -eq $Override[$key] -and $Override[$key] -isnot [string]) {
            $m.Remove($key)
        } else {
            $m[$key] = $Override[$key]
        }
    }
    return ([pscustomobject]$m)
}
function Check([hashtable]$Override) {
    $problems = @(Test-PilotRunManifest -Manifest (New-Case $Override) `
                                        -RunsRoot $RunsRoot -RunId $RunId `
                                        -ApprovedKitSha $kit `
                                        -ExpectedMachine $Machine)
    return ,$problems
}
$results = [ordered]@{}
$results['healthy']              = Check @{}
$results['run_root_value']       = (Get-PilotRunDirectory -RunsRoot $RunsRoot -RunId $RunId)
$results['no_created_utc']       = Check @{ created_utc = $null }
$results['bad_created_utc']      = Check @{ created_utc = 'yesterday' }
$results['created_utc_no_zulu']  = Check @{ created_utc = '2026-09-03 10:00:00' }
$results['other_machine']        = Check @{ machine = 'SOMEONE-ELSE' }
$results['machine_other_case']   = Check @{ machine = $Machine.ToLower() }
$results['steps_is_a_string']    = Check @{ steps = 'not-an-object' }
$results['steps_is_a_number']    = Check @{ steps = 7 }
$results['no_steps']             = Check @{ steps = $null }
$results['run_root_elsewhere']   = Check @{ run_root = 'C:\transport-report\runs\x' }
$results['run_root_in_staging']  = Check @{ run_root = 'C:\transport-report-staging\x' }
$results['run_root_other_run']   = Check @{ run_root = (Get-PilotRunDirectory -RunsRoot $RunsRoot -RunId '20260101T000000Z-deadbeef') }
$results['wrong_product']        = Check @{ product_sha = ('f' * 40) }
$results['wrong_day']            = Check @{ target_day = '2026-06-06' }
$results['sha_disagree']         = Check @{ measured_kit_sha = ('b' * 40) }
$results | ConvertTo-Json -Depth 5 -Compress
'''


@unittest.skipIf(PWSH is None, 'no PowerShell in this environment')
class RunManifestIsFullyValidated(unittest.TestCase):
    """Поведенческие проверки, а не поиск строк в исходнике.

    [REASON]: `New-PilotRun` пишет `created_utc`, а проверка его не требовала
    и формата не смотрела; `machine` проверялась лишь на непустоту, то есть
    манифест другой машины принимался; `steps` могло быть строкой. Всё это --
    поля, из которых берутся пути и решения, и «почти проверено» здесь значит
    «не проверено».
    """

    @classmethod
    def setUpClass(cls):
        cls.runs_root = 'D:\\pilot-runs'
        cls.run_id = '20260903T100000Z-abcdef01'
        cls.machine = 'SRV-YOQSH'
        cls.state = cls._run()

    @classmethod
    def _run(cls):
        directory = tempfile.mkdtemp(prefix='pilot_man_')
        try:
            path = os.path.join(directory, 'manifest.ps1')
            with open(path, 'w', encoding='utf-8', newline='\n') as handle:
                handle.write(MANIFEST_SCRIPT)
            result = subprocess.run(
                [PWSH, '-NoProfile', '-File', path,
                 '-Module', os.path.join(KIT_DIR, 'PilotKit.psm1'),
                 '-RunsRoot', cls.runs_root, '-RunId', cls.run_id,
                 '-Machine', cls.machine],
                capture_output=True, text=True, cwd=REPO_ROOT)
            if result.returncode != 0:
                raise AssertionError('%s\n%s' % (result.stdout, result.stderr))
            line = [row for row in result.stdout.splitlines()
                    if row.strip().startswith('{')][-1]
            return json.loads(line)
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def problems(self, name):
        value = self.state[name]
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return list(value)

    def test_a_healthy_manifest_has_no_problems(self):
        self.assertEqual(self.problems('healthy'), [])
        # [REASON]: и он не пустой по недоразумению. Join-Path на Linux не
        # умеет `D:\...` и возвращал $null -- два null сравнивались равными,
        # и «здоровый» манифест проходил, не имея run_root вовсе.
        self.assertTrue(self.state['run_root_value'].endswith(self.run_id))
        self.assertIn(self.runs_root.replace('\\\\', '\\'),
                      self.state['run_root_value'])

    def test_created_utc_is_required(self):
        self.assertIn('MISSING_FIELD:created_utc',
                      self.problems('no_created_utc'))

    def test_created_utc_must_be_a_utc_timestamp(self):
        self.assertIn('MALFORMED_CREATED_UTC',
                      self.problems('bad_created_utc'))
        self.assertIn('MALFORMED_CREATED_UTC',
                      self.problems('created_utc_no_zulu'))

    def test_the_machine_must_be_this_machine(self):
        self.assertIn('MACHINE_MISMATCH', self.problems('other_machine'))

    def test_the_machine_comparison_ignores_case(self):
        self.assertEqual(self.problems('machine_other_case'), [])

    def test_steps_must_be_an_object(self):
        self.assertIn('STEPS_IS_NOT_AN_OBJECT',
                      self.problems('steps_is_a_string'))
        self.assertIn('STEPS_IS_NOT_AN_OBJECT',
                      self.problems('steps_is_a_number'))
        self.assertTrue(self.problems('no_steps'))

    def test_the_run_root_must_be_this_runs_directory(self):
        for name in ('run_root_elsewhere', 'run_root_in_staging',
                     'run_root_other_run'):
            self.assertIn('RUN_ROOT_IS_NOT_THE_RUN_DIRECTORY',
                          self.problems(name), name)
        self.assertIn('RUN_ROOT_IS_INSIDE_A_CHECKOUT',
                      self.problems('run_root_elsewhere'))
        self.assertIn('RUN_ROOT_IS_INSIDE_A_CHECKOUT',
                      self.problems('run_root_in_staging'))

    def test_the_revisions_and_the_day_are_checked(self):
        self.assertIn('PRODUCT_SHA_MISMATCH', self.problems('wrong_product'))
        self.assertIn('TARGET_DAY_MISMATCH', self.problems('wrong_day'))
        self.assertIn('APPROVED_AND_MEASURED_KIT_SHA_DIFFER',
                      self.problems('sha_disagree'))

    def test_the_required_field_list_is_declared_and_complete(self):
        text = code_text('PilotKit.psm1')
        self.assertIn('$script:RunManifestRequiredFields', text)
        for field in ('kit', 'kit_version', 'run_id', 'approved_kit_sha',
                      'measured_kit_sha', 'product_sha', 'target_day',
                      'created_utc', 'machine', 'kit_checkout', 'run_root',
                      'steps'):
            self.assertIn("'%s'" % field, text, field)

    def test_every_caller_passes_the_expected_machine(self):
        text = code_text('PilotKit.psm1')
        # Имена якорятся точно: `function Get-PilotRun` -- префикс
        # `function Get-PilotRunDirectory`, и без скобки поиск попадал бы в
        # соседнюю функцию.
        for caller in ('function New-PilotRun {', 'function Get-PilotRun {',
                       'function Set-PilotRunStep {'):
            body = text[text.index(caller):][:2600]
            self.assertIn('Assert-PilotRunManifest', body, caller)
            self.assertIn('-ExpectedMachine $env:COMPUTERNAME', body, caller)



# ═══ Д5. Белый список действует и на вложенные поля ═════════════════════════

PRIVATE_MARKER = 'SYNTHETIC-PRIVATE-NAME'
PRIVATE_KEY = 'customer_field_name'


class NestedFieldsObeyTheAllowlist(unittest.TestCase):
    """Необъявленное вложенное поле не уезжает в отчёт.

    [REASON]: `scan_for_private_values` сверяла с белым списком только КЛЮЧИ
    ВЕРХНЕГО УРОВНЯ, а `build_report` целиком копировала `collect_counters` и
    `summary` каждого пересчёта. Обычная строка, не похожая ни на uuid, ни на
    координату, ни на слово-секрет, проходила насквозь -- то есть заявление
    «отчёт строится по белому списку» было неверным ровно там, где данные
    приезжают снаружи.
    """

    def build(self, mutate=None):
        shared = shared_evidence()
        collect = copy.deepcopy(shared['collect'])
        runs = copy.deepcopy(shared['runs'])
        if mutate:
            mutate(collect, runs)
        return report_mod.build_report(
            copy.deepcopy(shared['preflight']), copy.deepcopy(shared['deploy']),
            collect, runs, copy.deepcopy(shared['snapshot']), None, None, [],
            run_id=shared['run_id'], kit_sha=KIT_SHA_FIXTURE)

    def test_a_clean_report_is_not_rejected(self):
        report, _markdown = self.build()
        self.assertNotIn('REPORT_CONTAINS_UNDECLARED_NESTED_FIELDS',
                         report['verdict_reasons'])
        self.assertEqual(report['undeclared_nested_fields'], 0)

    def test_an_unknown_counter_is_refused_and_never_printed(self):
        def plant(collect, runs):
            collect['payload']['counters'][PRIVATE_KEY] = PRIVATE_MARKER

        report, markdown = self.build(plant)
        document = json.dumps(report, ensure_ascii=True)

        self.assertNotIn(PRIVATE_MARKER, document)
        self.assertNotIn(PRIVATE_KEY, document)
        self.assertNotIn(PRIVATE_MARKER, markdown)
        self.assertNotIn(PRIVATE_KEY, markdown)
        self.assertEqual(report['verdict'], 'REJECT')
        self.assertIn('REPORT_CONTAINS_UNDECLARED_NESTED_FIELDS',
                      report['verdict_reasons'])
        self.assertGreaterEqual(report['undeclared_nested_fields'], 1)

    def test_an_unknown_recalc_summary_field_is_refused_and_never_printed(self):
        def plant(collect, runs):
            for run in runs:
                if run['payload'].get('label') == 'apply-1':
                    run['payload']['summary'][PRIVATE_KEY] = PRIVATE_MARKER

        report, markdown = self.build(plant)
        document = json.dumps(report, ensure_ascii=True)

        self.assertNotIn(PRIVATE_MARKER, document)
        self.assertNotIn(PRIVATE_KEY, document)
        self.assertNotIn(PRIVATE_MARKER, markdown)
        self.assertNotIn(PRIVATE_KEY, markdown)
        self.assertEqual(report['verdict'], 'REJECT')
        self.assertIn('REPORT_CONTAINS_UNDECLARED_NESTED_FIELDS',
                      report['verdict_reasons'])

    def test_both_at_once_are_counted(self):
        def plant(collect, runs):
            collect['payload']['counters'][PRIVATE_KEY] = PRIVATE_MARKER
            for run in runs:
                run['payload']['summary'][PRIVATE_KEY] = PRIVATE_MARKER

        report, _markdown = self.build(plant)
        self.assertEqual(report['verdict'], 'REJECT')
        self.assertGreaterEqual(report['undeclared_nested_fields'], 2)

    def test_the_declared_counters_still_arrive(self):
        report, _markdown = self.build()
        counters = report['collect_counters']
        for name in ('probe_observations', 'probe_confirmed',
                     'probe_request_failures', 'probe_pending_requests',
                     'collect_batch_accepted', 'exit'):
            self.assertIn(name, counters, name)

    def test_the_declared_summary_fields_still_arrive(self):
        report, _markdown = self.build()
        summaries = [run['summary'] for run in report['recalc_runs']]
        self.assertTrue(summaries)
        for summary in summaries:
            for name in ('works', 'inserted', 'updated', 'unchanged',
                         'deleted', 'READY_ESTIMATE', 'ready_area_ha'):
                self.assertIn(name, summary, name)

    def test_decide_rejects_an_undeclared_nested_field_on_its_own(self):
        """Само правило, а не только сборка отчёта поверх него."""
        healthy = [{'code': 'X', 'passed': True, 'means': ''}]
        coverage = {'works_without_number_share': 0.0}
        verdict, reasons = report_mod.decide(
            healthy, coverage, 0.9, -1.0, 999.0, True, (),
            undeclared_nested=1)
        self.assertEqual(verdict, 'REJECT')
        self.assertIn('REPORT_CONTAINS_UNDECLARED_NESTED_FIELDS', reasons)

        # Положительный контроль: без чужого поля тот же вход даёт GO.
        verdict, _reasons = report_mod.decide(
            healthy, coverage, 0.9, -1.0, 999.0, True, (),
            undeclared_nested=0)
        self.assertEqual(verdict, 'GO')

    def test_the_allowlists_are_declared_not_inferred(self):
        self.assertTrue(report_mod.ALLOWED_COUNTER_FIELDS)
        self.assertTrue(report_mod.ALLOWED_SUMMARY_FIELDS)
        self.assertNotIn(PRIVATE_KEY, report_mod.ALLOWED_COUNTER_FIELDS)
        self.assertNotIn(PRIVATE_KEY, report_mod.ALLOWED_SUMMARY_FIELDS)

    def test_the_counter_allowlist_matches_the_collector(self):
        from drone_collector.main import COLLECT_SUMMARY_KEYS
        self.assertEqual(tuple(report_mod.ALLOWED_COUNTER_FIELDS),
                         tuple(COLLECT_SUMMARY_KEYS))

    def test_the_docstring_describes_the_real_exit_codes(self):
        with open(os.path.join(KIT_DIR, 'pilot_report.py'),
                  encoding='utf-8') as handle:
            head = handle.read().split('"""')[1]
        self.assertIn('0', head)
        self.assertIn('10', head)
        self.assertIn('11', head)
        self.assertIn('12', head)
        self.assertNotIn('3 -- отчёт не прошёл', head)


# ═══ С1. Настоящая форма кандидата на службу ════════════════════════════════

SERVICE_SHAPE_SCRIPT = r"""
param([string]$Module)
Import-Module $Module -Force
$results = [ordered]@{}
function Throws([scriptblock]$b) { try { & $b | Out-Null; return $false } catch { return $true } }

# Ровно то, что Get-PilotServiceImagePath вернула на SRV-YOQSH: значения из
# реестра верные, тип -- OrderedDictionary.
$orderedStaging = [ordered]@{
    Name = 'TransportReportStaging'
    AppDirectory = 'C:\transport-report-staging'
    Application = 'C:\Program Files\Python314\python.exe'
    ImagePath = 'C:\transport-report-staging\nssm.exe'
}
$orderedProduction = [ordered]@{
    Name = 'TransportReport'
    AppDirectory = 'C:\transport-report'
    Application = 'C:\Program Files\Python314\python.exe'
    ImagePath = 'C:\transport-report\nssm.exe'
}
# Имя staging-овое, но привязка к каталогу ничем не доказана.
$orderedNamedOnly = [ordered]@{
    Name = 'TransportReportStaging'
    AppDirectory = ''
    Application = ''
    ImagePath = ''
}
# Указывает на оба контура сразу.
$orderedBothRoots = [ordered]@{
    Name = 'TransportReportAmbiguous'
    AppDirectory = 'C:\transport-report-staging'
    Application = 'C:\transport-report\python.exe'
    ImagePath = ''
}
$orderedSecondStaging = [ordered]@{
    Name = 'TransportReportOther'
    AppDirectory = 'C:\transport-report-staging'
    Application = ''
    ImagePath = ''
}
# Имя production, но ВСЕ пути указывают на staging. Единственный случай, в
# котором правило «TransportReport не выбирается никогда» работает само, а не
# заодно с проверкой каталогов.
$orderedProductionNameAtStagingPath = [ordered]@{
    Name = 'TransportReport'
    AppDirectory = 'C:\transport-report-staging'
    Application = 'C:\transport-report-staging\python.exe'
    ImagePath = 'C:\transport-report-staging\nssm.exe'
}
$objectStaging = [pscustomobject]@{
    Name = 'TransportReportStaging'
    AppDirectory = 'C:\transport-report-staging'
    Application = 'C:\Program Files\Python314\python.exe'
    ImagePath = 'C:\transport-report-staging\nssm.exe'
}
$objectProduction = [pscustomobject]@{
    Name = 'TransportReport'
    AppDirectory = 'C:\transport-report'
    Application = ''
    ImagePath = 'C:\transport-report\nssm.exe'
}
$hashStaging = @{
    Name = 'TransportReportStaging'
    AppDirectory = 'C:\transport-report-staging'
    Application = ''
    ImagePath = ''
}

$results['ps_major'] = $PSVersionTable.PSVersion.Major
$results['ps_edition'] = if ($PSVersionTable.PSEdition) { [string]$PSVersionTable.PSEdition } else { 'Desktop' }

# Ловушка, ради которой тест вообще существует: ключи словаря НЕ появляются
# среди свойств объекта, и старая проверка наличия поля отвечала на другой
# вопрос.
$results['candidate_type'] = $orderedStaging.GetType().FullName
$results['keys_are_not_properties'] = ($orderedStaging.PSObject.Properties.Name -contains 'AppDirectory')
$results['dot_access_does_work'] = [string]$orderedStaging.AppDirectory

# Живой отказ SRV-YOQSH.
$results['ordered_picks_staging'] = ((Select-PilotStagingService -Candidates @($orderedStaging)).Name)
$results['ordered_picks_staging_beside_production'] = ((Select-PilotStagingService -Candidates @($orderedProduction, $orderedStaging)).Name)

# Fail-closed правила на том же типе.
$results['ordered_refuses_production_only'] = (Throws { Select-PilotStagingService -Candidates @($orderedProduction) })
$results['ordered_refuses_name_only'] = (Throws { Select-PilotStagingService -Candidates @($orderedNamedOnly) })
$results['ordered_refuses_both_roots'] = (Throws { Select-PilotStagingService -Candidates @($orderedBothRoots) })
$results['ordered_refuses_two_staging'] = (Throws { Select-PilotStagingService -Candidates @($orderedStaging, $orderedSecondStaging) })
$results['ordered_refuses_empty'] = (Throws { Select-PilotStagingService -Candidates @() })
$results['ordered_refuses_production_name_at_staging_path'] = (Throws { Select-PilotStagingService -Candidates @($orderedProductionNameAtStagingPath) })
$results['ordered_refuses_production_name_beside_staging'] = ((Select-PilotStagingService -Candidates @($orderedProductionNameAtStagingPath, $orderedStaging)).Name)

# Прежняя форма не сломана.
$results['object_picks_staging'] = ((Select-PilotStagingService -Candidates @($objectProduction, $objectStaging)).Name)
$results['object_refuses_production_only'] = (Throws { Select-PilotStagingService -Candidates @($objectProduction) })
$results['hashtable_picks_staging'] = ((Select-PilotStagingService -Candidates @($hashStaging)).Name)

# Смешанный список: словарь и объект в одном вызове.
$results['mixed_refuses_two_staging'] = (Throws { Select-PilotStagingService -Candidates @($orderedStaging, $objectStaging) })

# Функция возвращает ИМЕННО переданный объект, а не его копию.
$chosen = Select-PilotStagingService -Candidates @($orderedProduction, $orderedStaging)
$results['returns_the_input_object'] = [object]::ReferenceEquals($chosen, $orderedStaging)

# Что производит сама Get-PilotServiceImagePath. Реестра на этой машине может
# не быть -- значения будут пустыми, но ТИП от машины не зависит.
$produced = Get-PilotServiceImagePath -Name 'TransportReportStaging' 2>$null
$results['produced_type'] = $produced.GetType().FullName
$results['produced_is_dictionary'] = ($produced -is [System.Collections.IDictionary])
$results['produced_fields'] = (($produced.PSObject.Properties.Name) -join ',')

# Помощник читает обе формы и различает отсутствующее поле.
$results['field_from_ordered'] = (Get-PilotCandidateField -Candidate $orderedStaging -Field 'AppDirectory')
$results['field_from_object'] = (Get-PilotCandidateField -Candidate $objectStaging -Field 'AppDirectory')
$results['field_case_insensitive'] = (Get-PilotCandidateField -Candidate $orderedStaging -Field 'appdirectory')
$results['field_missing_is_null'] = ($null -eq (Get-PilotCandidateField -Candidate $orderedStaging -Field 'Nonexistent'))
$results['field_of_null_is_null'] = ($null -eq (Get-PilotCandidateField -Candidate $null -Field 'Name'))

# Отрицательный контроль: точечное обращение к ОТСУТСТВУЮЩЕМУ полю словаря
# подставляет член самого словаря, а помощник -- нет.
$short = [ordered]@{ Name = 'TransportReportStaging'; AppDirectory = 'C:\transport-report-staging' }
$results['dot_access_invents_count'] = [string]$short.Count
$results['helper_reports_count_absent'] = ($null -eq (Get-PilotCandidateField -Candidate $short -Field 'Count'))

$results | ConvertTo-Json -Compress
"""


@unittest.skipIf(PWSH is None, 'no PowerShell in this environment')
class TheRealServiceShapeIsSelected(unittest.TestCase):
    """Кандидат приходит тем типом, каким его делает Get-PilotServiceImagePath.

    [REASON]: комплект отказал на SRV-YOQSH сообщением «no service could be
    resolved to the staging checkout C:\\transport-report-staging», хотя служба
    TransportReportStaging была запущена и её AppDirectory в реестре указывал
    ровно на этот каталог. Причина -- не правило выбора, а чтение полей:
    Get-PilotServiceImagePath возвращала `[ordered]@{...}`, а
    Select-PilotStagingService спрашивала о наличии поля через
    `PSObject.Properties.Name -contains`. У словаря там лежат его собственные
    члены (Count, Keys, Values, IsReadOnly, IsFixedSize, SyncRoot,
    IsSynchronized), а не ключи, поэтому все три поля читались как
    отсутствующие и настоящая staging-служба отбрасывалась.

    [REASON]: старые проверки этого не ловили, потому что строили кандидатов
    как `[pscustomobject]@{...}` -- тип, которого настоящая функция никогда не
    возвращала. Проверка шла по форме, которой на сервере не бывает, и давала
    одинаковый ответ при верном и неверном коде. Поэтому здесь кандидаты
    создаются ИМЕННО как `[ordered]@{...}`.
    """

    def state(self):
        directory = tempfile.mkdtemp(prefix='pilot_shape_')
        try:
            path = os.path.join(directory, 'shape.ps1')
            with open(path, 'w', encoding='utf-8', newline='\n') as handle:
                handle.write(SERVICE_SHAPE_SCRIPT)
            result = subprocess.run(
                [PWSH, '-NoProfile', '-File', path,
                 '-Module', os.path.join(KIT_DIR, 'PilotKit.psm1')],
                capture_output=True, text=True, cwd=REPO_ROOT)
        finally:
            shutil.rmtree(directory, ignore_errors=True)
        self.assertEqual(result.returncode, 0,
                         '%s\n%s' % (result.stdout, result.stderr))
        line = [row for row in result.stdout.splitlines()
                if row.strip().startswith('{')][-1]
        return json.loads(line)

    def test_the_trap_is_really_there(self):
        """Ловушка проверяется первой, иначе тест ниже доказывал бы не то."""
        state = self.state()
        # [REASON]: сбой пришёл с Windows PowerShell 5.1, и регрессия обязана
        # быть доказана на той же консоли. Класс сам называет версию, на
        # которой отработал, чтобы это было видно из него, а не только из
        # соседнего теста.
        expected = os.environ.get('PILOT_POWERSHELL_MAJOR')
        if expected:
            self.assertEqual(int(state['ps_major']), int(expected),
                             'задача просила PowerShell %s, а регрессия '
                             'отработала на %s'
                             % (expected, state['ps_major']))
        self.assertEqual(state['candidate_type'],
                         'System.Collections.Specialized.OrderedDictionary')
        self.assertFalse(
            state['keys_are_not_properties'],
            'ключ словаря не должен значиться среди свойств объекта -- если он '
            'там есть, эта версия PowerShell не воспроизводит дефект и '
            'регрессия ниже ничего не доказывает')
        self.assertEqual(state['dot_access_does_work'],
                         'C:\\transport-report-staging',
                         'значения в кандидате есть; отброшен он был не из-за '
                         'них')

    def test_the_real_staging_service_is_selected(self):
        """Тот самый случай, на котором комплект встал на сервере."""
        state = self.state()
        self.assertEqual(state['ordered_picks_staging'],
                         'TransportReportStaging')
        self.assertEqual(state['ordered_picks_staging_beside_production'],
                         'TransportReportStaging')

    def test_the_fail_closed_rules_survive_the_new_shape(self):
        state = self.state()
        self.assertTrue(state['ordered_refuses_production_only'],
                        'production никогда не может быть выбрана')
        self.assertTrue(state['ordered_refuses_name_only'],
                        'выбор только по имени запрещён')
        self.assertTrue(state['ordered_refuses_both_roots'],
                        'кандидат на оба контура сразу отклоняется')
        self.assertTrue(state['ordered_refuses_two_staging'],
                        'два кандидата -- отказ, а не догадка')
        self.assertTrue(state['ordered_refuses_empty'],
                        'ноль кандидатов -- отказ')
        self.assertTrue(state['mixed_refuses_two_staging'],
                        'смешанный список не обходит правило двух кандидатов')
        # [REASON]: остальные случаи отклоняют production по КАТАЛОГАМ, и
        # проверка имени в них не нужна -- убери её, и они всё равно пройдут.
        # Здесь пути указывают на staging, поэтому отказать может только
        # правило имени. Без этого случая самое опасное правило комплекта
        # ничем не доказано.
        self.assertTrue(state['ordered_refuses_production_name_at_staging_path'],
                        'служба с именем TransportReport не может быть выбрана '
                        'ни при каких путях')
        self.assertEqual(state['ordered_refuses_production_name_beside_staging'],
                         'TransportReportStaging',
                         'рядом с настоящей staging-службой кандидат с именем '
                         'production обязан быть отброшен, а не создать спор '
                         'двух кандидатов')

    def test_the_previous_shape_still_works(self):
        state = self.state()
        self.assertEqual(state['object_picks_staging'],
                         'TransportReportStaging')
        self.assertTrue(state['object_refuses_production_only'])
        self.assertEqual(state['hashtable_picks_staging'],
                         'TransportReportStaging')

    def test_the_selector_returns_the_object_it_was_given(self):
        """[REASON]: нормализация не должна подменять выбранный объект копией.

        Вызывающий код читает у результата не только Name, и получить обратно
        не тот объект, что передал, -- отдельный способ ошибиться.
        """
        state = self.state()
        self.assertTrue(state['returns_the_input_object'])

    def test_the_producer_returns_a_real_object(self):
        state = self.state()
        self.assertEqual(state['produced_type'],
                         'System.Management.Automation.PSCustomObject')
        self.assertFalse(state['produced_is_dictionary'])
        for field in ('Name', 'ImagePath', 'AppDirectory', 'Application'):
            self.assertIn(field, state['produced_fields'].split(','),
                          'поле %s обязано быть видно как свойство' % field)

    def test_the_field_reader_is_total_over_both_shapes(self):
        state = self.state()
        self.assertEqual(state['field_from_ordered'],
                         'C:\\transport-report-staging')
        self.assertEqual(state['field_from_object'],
                         'C:\\transport-report-staging')
        self.assertEqual(state['field_case_insensitive'],
                         'C:\\transport-report-staging')
        self.assertTrue(state['field_missing_is_null'])
        self.assertTrue(state['field_of_null_is_null'])

    def test_the_reader_does_not_fall_back_to_dictionary_members(self):
        """Отрицательный контроль: почему не годится точечное обращение.

        [REASON]: у словаря БЕЗ ключа 'Count' выражение `$candidate.Count`
        возвращает размер словаря, а не $null. Читатель, упростивший помощника
        до точечного обращения, начнёт принимать отсутствующее поле за
        заполненное. Проверка сначала доказывает, что подмена действительно
        происходит, и только потом -- что помощник ей не поддался.
        """
        state = self.state()
        self.assertEqual(state['dot_access_invents_count'], '2',
                         'без этой подмены контроль ниже не различает два '
                         'случая')
        self.assertTrue(state['helper_reports_count_absent'])

    def test_the_candidates_in_this_file_use_the_real_type(self):
        """[REASON]: тест обязан остаться отличимым от прежнего.

        Если кандидаты снова станут `[pscustomobject]@{...}`, проверка вернётся
        к форме, которой на сервере не бывает, и снова начнёт давать
        одинаковый ответ при верном и неверном коде.
        """
        self.assertIn("$orderedStaging = [ordered]@{", SERVICE_SHAPE_SCRIPT)
        self.assertIn("Name = 'TransportReportStaging'", SERVICE_SHAPE_SCRIPT)
        self.assertIn("AppDirectory = 'C:\\transport-report-staging'",
                      SERVICE_SHAPE_SCRIPT)
        self.assertIn("ImagePath = 'C:\\transport-report-staging\\nssm.exe'",
                      SERVICE_SHAPE_SCRIPT)


class TheSelectorReadsFieldsShapeBlind(unittest.TestCase):
    """Проверки текста модуля -- работают и там, где PowerShell не установлен."""

    def setUp(self):
        with open(os.path.join(KIT_DIR, 'PilotKit.psm1'),
                  encoding='utf-8') as handle:
            self.text = handle.read()

    def test_the_producer_no_longer_returns_a_bare_dictionary(self):
        self.assertIn('return [pscustomobject][ordered]@{', self.text)
        self.assertNotIn('    return [ordered]@{\n        Name         = $Name',
                         self.text)

    def test_the_selector_no_longer_probes_psobject_properties(self):
        """[REASON]: это и есть строка, отбросившая настоящую службу."""
        self.assertNotIn(
            "if ($candidate.PSObject.Properties.Name -contains $field) {",
            self.text)
        self.assertIn(
            "$value = Get-PilotCandidateField -Candidate $candidate "
            "-Field $field", self.text)

    def test_the_reader_handles_dictionaries(self):
        self.assertIn('if ($Candidate -is [System.Collections.IDictionary]) {',
                      self.text)
        self.assertIn('if ($Candidate.Contains($Field)) {', self.text)

    def test_the_reader_is_exported(self):
        export = self.text.split('Export-ModuleMember -Function')[1]
        self.assertIn('Get-PilotCandidateField', export)

# ═══ С2. stderr успешного процесса не убивает шаг ═══════════════════════════

# Дочерний процесс, повторяющий живой сбой: строка в stdout, DeprecationWarning
# в stderr, выход 0. Ровно так вела себя миграция на SRV-YOQSH.
WARNING_CHILD = (
    "import sys\n"
    "sys.stdout.write('MIGRATION_ID=DRONES_USEFUL_AREA_001\\n')\n"
    "sys.stdout.write('Already applied. Nothing to do.\\n')\n"
    "sys.stderr.write('DeprecationWarning: datetime.datetime.utcnow() is "
    "deprecated\\n')\n"
    "sys.exit(0)\n")

# Тот же процесс, но с настоящим отказом: stderr И ненулевой код.
FAILING_CHILD = (
    "import sys\n"
    "sys.stdout.write('partial output\\n')\n"
    "sys.stderr.write('Traceback (most recent call last): boom\\n')\n"
    "sys.exit(2)\n")

# Заглушка инструмента пересчёта: принимает настоящие флаги, пишет сводку в
# stdout и предупреждение в stderr, выходит 0.
RECALC_CHILD = (
    "import sys\n"
    "sys.stdout.write('mode : DRY RUN\\n')\n"
    "sys.stdout.write('algorithm : useful-area-v1\\n')\n"
    "sys.stderr.write('DeprecationWarning: utcnow() is deprecated\\n')\n"
    "sys.exit(0)\n")


NATIVE_HELPER_SCRIPT = r"""
param([string]$Module, [string]$Py, [string]$Warn, [string]$Fail)
Import-Module $Module -Force
$ErrorActionPreference = 'Stop'
$results = [ordered]@{}
$results['ps_major'] = $PSVersionTable.PSVersion.Major
$results['ps_edition'] = if ($PSVersionTable.PSEdition) { [string]$PSVersionTable.PSEdition } else { 'Desktop' }
$results['eap_before'] = [string]$ErrorActionPreference

# --- ЛОВУШКА. Сначала доказать, что она на этой консоли есть. ---------------
# Прямой захват через 2>&1 отдаёт stderr ОБЪЕКТОМ ErrorRecord. Именно его 5.1
# под 'Stop' превращает в терминирующий NativeCommandError. Если здесь окажется
# обычная строка, эта версия PowerShell дефект не воспроизводит и всё, что
# ниже, ничего не доказывает.
$rawThrew = $false
$rawHasErrorRecord = $false
$rawId = ''
try {
    $raw = & $Py $Warn 2>&1
    foreach ($item in @($raw)) {
        if ($item -is [System.Management.Automation.ErrorRecord]) { $rawHasErrorRecord = $true }
    }
} catch {
    $rawThrew = $true
    $rawId = [string]$_.FullyQualifiedErrorId
}
$results['raw_capture_threw'] = $rawThrew
$results['raw_capture_error_id'] = $rawId
$results['raw_capture_yields_error_record'] = $rawHasErrorRecord


# --- 1. Успешный процесс, написавший предупреждение в stderr ----------------
$okThrew = $false
try {
    $ok = Invoke-PilotNative -FilePath $Py -Arguments @($Warn)
} catch {
    $okThrew = $true
    $results['ok_error_id'] = [string]$_.FullyQualifiedErrorId
}
$results['ok_threw'] = $okThrew
if (-not $okThrew) {
    $results['ok_exit'] = $ok.ExitCode
    $results['ok_stdout_has_line'] = ($ok.Stdout -match 'MIGRATION_ID=DRONES_USEFUL_AREA_001')
    $results['ok_stdout_has_idempotence_line'] = ($ok.Stdout -match 'Already applied')
    $results['ok_stderr_kept'] = ($ok.Stderr -match 'DeprecationWarning')
    $results['ok_stderr_not_in_stdout'] = -not ($ok.Stdout -match 'DeprecationWarning')
    $results['ok_text_has_both'] = (($ok.Text -match 'MIGRATION_ID') -and ($ok.Text -match 'DeprecationWarning'))
    # [REASON]: проверяется порядок ВНУТРИ потока, а не между потоками.
    # Межпотоковый порядок недетерминирован: python при перенаправлении
    # буферизует stdout блоками, а stderr не буферизует, и какая строка придёт
    # раньше -- зависит от момента сброса буфера. Прежняя редакция этой
    # проверки сравнивала межпотоковый порядок двух РАЗНЫХ запусков и падала на
    # CI через раз. Порядок внутри потока воспроизводим, и именно он делает
    # журнал читаемым.
    $results['ok_stdout_keeps_order'] = ($ok.Stdout.IndexOf('MIGRATION_ID') -ge 0) -and
        ($ok.Stdout.IndexOf('MIGRATION_ID') -lt $ok.Stdout.IndexOf('Already applied'))
    $results['ok_text_keeps_order'] = ($ok.Text.IndexOf('MIGRATION_ID') -ge 0) -and
        ($ok.Text.IndexOf('MIGRATION_ID') -lt $ok.Text.IndexOf('Already applied'))
    $results['ok_no_error_record'] = -not (($ok.Stdout -is [System.Management.Automation.ErrorRecord]) -or
                                           ($ok.Text -is [System.Management.Automation.ErrorRecord]))
}
$results['eap_after_ok'] = [string]$ErrorActionPreference

# --- 2. Настоящий отказ: stderr И ненулевой код ----------------------------
$badThrew = $false
try {
    $bad = Invoke-PilotNative -FilePath $Py -Arguments @($Fail)
} catch {
    $badThrew = $true
}
$results['bad_threw'] = $badThrew
if (-not $badThrew) {
    $results['bad_exit'] = $bad.ExitCode
    $results['bad_stderr_kept'] = ($bad.Stderr -match 'Traceback')
    $results['bad_stdout_kept'] = ($bad.Stdout -match 'partial output')
}
$results['eap_after_bad'] = [string]$ErrorActionPreference

# --- 3. Запуск, который вообще не состоялся, обязан отказать громко --------
$missingThrew = $false
try {
    Invoke-PilotNative -FilePath 'pilot-no-such-binary-xyz' -Arguments @() | Out-Null
} catch {
    $missingThrew = $true
}
$results['missing_binary_threw'] = $missingThrew
$results['eap_after_missing'] = [string]$ErrorActionPreference

# --- 4. Invoke-PilotGit: диагностика в stderr не должна пачкать данные -----
$repo = Join-Path ([System.IO.Path]::GetTempPath()) ("pilotgit_{0}" -f (Get-Random))
New-Item -ItemType Directory -Path $repo | Out-Null
Invoke-PilotNative -FilePath 'git' -Arguments @('-C', $repo, 'init', '--quiet') | Out-Null
Invoke-PilotNative -FilePath 'git' -Arguments @('-C', $repo, 'config', 'user.email', 'pilot@example.invalid') | Out-Null
Invoke-PilotNative -FilePath 'git' -Arguments @('-C', $repo, 'config', 'user.name', 'Pilot') | Out-Null
Set-Content -LiteralPath (Join-Path $repo 'a.txt') -Value 'x'
Invoke-PilotNative -FilePath 'git' -Arguments @('-C', $repo, 'add', 'a.txt') | Out-Null
# `git commit` пишет подсказки в stderr; ровно та диагностика, что склеивалась
# с данными при 2>&1.
Invoke-PilotNative -FilePath 'git' -Arguments @('-C', $repo, 'commit', '-m', 'x', '--quiet') | Out-Null

$head = Get-PilotHeadSha -Repo $repo
$results['git_head_is_a_bare_sha'] = ($head -match '^[0-9a-f]{40}$')
$results['git_head_value'] = $head

# Отрицательный контроль: git с диагностикой в stderr и нулевым кодом.
$noisy = Invoke-PilotGit -Repo $repo -Arguments @('checkout', '--detach', 'HEAD')
$results['git_noisy_exit'] = $noisy.ExitCode
$results['git_noisy_stderr_present'] = ($noisy.Stderr.Trim().Length -gt 0)
$results['git_noisy_stdout_clean'] = -not ($noisy.Output -match 'HEAD is now at')
$results['git_noisy_stderr_kept'] = ($noisy.Text.Trim().Length -gt 0)

# Ненулевой код обязан дойти до вызывающего, а не потеряться.
$failGit = Invoke-PilotGit -Repo $repo -Arguments @('rev-parse', 'refs/heads/definitely-absent') -AllowFailure
$results['git_failure_exit_is_nonzero'] = ($failGit.ExitCode -ne 0)
$results['git_failure_stderr_kept'] = ($failGit.Stderr.Trim().Length -gt 0)
$gitRefused = $false
try {
    Invoke-PilotGit -Repo $repo -Arguments @('rev-parse', 'refs/heads/definitely-absent') | Out-Null
} catch {
    $gitRefused = $true
}
$results['git_refuses_without_allowfailure'] = $gitRefused

# `status --porcelain` в чистом дереве обязан быть ПУСТЫМ, а не нести stderr.
$results['git_clean_status_is_empty'] = ((Invoke-PilotGit -Repo $repo -Arguments @('status', '--porcelain')).Output -eq '')

Remove-Item -LiteralPath $repo -Recurse -Force -ErrorAction SilentlyContinue

$results['eap_at_end'] = [string]$ErrorActionPreference
$results | ConvertTo-Json -Compress
"""


# Один и тот же шаблон запуска, вынутый ИЗ НАСТОЯЩЕГО ФАЙЛА и исполненный.
SCRIPT_STATEMENT_SCRIPT = r"""
param([string]$Module, [string]$Statement, [string]$VariableName,
      [string]$Py, [string]$Tool, [string]$Db)
Import-Module $Module -Force
$ErrorActionPreference = 'Stop'
$results = [ordered]@{}
$results['ps_major'] = $PSVersionTable.PSVersion.Major

# Имена, которыми пользуются настоящие строки комплекта.
$Python = $Py
$migrationInPlace = $Tool
$migration = $Tool
$tool = $Tool
$Day = '2026-06-05'
$K = [pscustomobject]@{ StagingDb = $Db }

$threw = $false
$errorId = ''
try {
    Invoke-Expression $Statement
} catch {
    $threw = $true
    $errorId = [string]$_.FullyQualifiedErrorId
}
$results['threw'] = $threw
$results['error_id'] = $errorId

$hasErrorRecord = $false
if (-not $threw) {
    $value = (Get-Variable -Name $VariableName -ValueOnly -ErrorAction SilentlyContinue)
    foreach ($item in @($value)) {
        if ($item -is [System.Management.Automation.ErrorRecord]) { $hasErrorRecord = $true }
    }
}
$results['reaches_caller_as_error_record'] = $hasErrorRecord
$results['eap_after'] = [string]$ErrorActionPreference
$results | ConvertTo-Json -Compress
"""


ASSIGNMENT = re.compile(r"^\s*\$(\w+)\s*=")


def powershell_code_lines(text):
    """Строки PowerShell без комментариев -- строчных и блочных.

    [REASON]: `<# ... #>` не начинается с решётки, и сканер, который её не
    знает, считает объяснение дефекта самим дефектом. Первая версия этой
    проверки так и сделала.
    """
    result = []
    in_block = False
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if in_block:
            if '#>' in stripped:
                in_block = False
            continue
        if stripped.startswith('<#'):
            if '#>' not in stripped:
                in_block = True
            continue
        if stripped.startswith('#'):
            continue
        result.append((number, stripped))
    return result


def find_native_capture_statements(text):
    """Все операторы файла, которые запускают native-процесс с захватом.

    Возвращает список (номер строки, текст оператора, имя переменной).

    [REASON]: НЕ поиск по якорю. Якорь пришлось бы писать под одну из двух
    форм -- прямой захват `& $Python ... 2>&1` или вызов помощника, -- и на
    другой форме тест молчал бы «оператор не найден» вместо того, чтобы
    исполнить его и упасть по настоящей причине. Первая версия этой проверки
    именно так и промолчала на базовом коммите.

    [REASON]: оператор бывает многострочным (аргументы помощника перенесены),
    поэтому границей служит баланс скобок, а не перевод строки.
    """
    lines = text.splitlines()
    found = []
    index = 0
    while index < len(lines):
        match = ASSIGNMENT.match(lines[index])
        if not match:
            index += 1
            continue
        collected = []
        depth = 0
        last = index
        for offset in range(index, len(lines)):
            line = lines[offset]
            collected.append(line.strip())
            depth += line.count('(') - line.count(')')
            last = offset
            if depth <= 0:
                break
        statement = '\n'.join(collected)
        if '2>&1' in statement or 'Invoke-PilotNative' in statement:
            found.append((index + 1, statement, match.group(1)))
        index = last + 1
    return found


# Скрипты, в которых комплект запускает native-процессы с захватом вывода.
# На базовом коммите их пять: миграция staging, повторная миграция копии и три
# запуска пересчёта.
NATIVE_CAPTURE_FILES = ('STAGING_DEPLOY_AND_MIGRATE.ps1',
                        'PREFLIGHT_AND_COPY_TEST.ps1',
                        'STAGING_RECALCULATE_AND_VERIFY.ps1')
NATIVE_CAPTURE_EXPECTED = 5


@unittest.skipIf(PWSH is None, 'no PowerShell in this environment')
class NativeStderrDoesNotKillTheStep(unittest.TestCase):
    """Предупреждение в stderr успешного процесса не должно останавливать шаг.

    [REASON]: на SRV-YOQSH шаг 2 дошёл до backup, обновления кода, тестов,
    остановки staging-службы и проверки блоба миграции, а затем умер на строке
    `$migrationOutput = & $Python $migrationInPlace 2>&1`, потому что миграция
    написала в stderr обычный DeprecationWarning. Windows PowerShell 5.1 под
    $ErrorActionPreference = 'Stop' превращает КАЖДУЮ строку stderr,
    объединённую через 2>&1, в терминирующий NativeCommandError. Настоящий exit
    code при этом не читается вовсе, и транзакция миграции не фиксируется.

    [REASON]: терминирующей ошибку делает ПРЕДПОЧТЕНИЕ, а не перенаправление.
    Поэтому дефект воспроизводится и на pwsh 7 в наблюдаемой части: stderr там
    тоже приходит объектом ErrorRecord, просто под 'Stop' семёрка его не делает
    фатальным. Проверка ловушки ниже сначала доказывает, что ErrorRecord на
    этой консоли действительно появляется, и только потом что-то утверждает.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix='pilot_native_')
        cls.warn = os.path.join(cls.tmp, 'warn_child.py')
        cls.fail = os.path.join(cls.tmp, 'fail_child.py')
        cls.recalc = os.path.join(cls.tmp, 'recalc_child.py')
        for path, content in ((cls.warn, WARNING_CHILD),
                              (cls.fail, FAILING_CHILD),
                              (cls.recalc, RECALC_CHILD)):
            with open(path, 'w', encoding='utf-8', newline='\n') as handle:
                handle.write(content)
        cls._state = None

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def state(self):
        if type(self)._state is not None:
            return type(self)._state
        directory = tempfile.mkdtemp(prefix='pilot_native_ps_')
        try:
            path = os.path.join(directory, 'native.ps1')
            with open(path, 'w', encoding='utf-8', newline='\n') as handle:
                handle.write(NATIVE_HELPER_SCRIPT)
            result = subprocess.run(
                [PWSH, '-NoProfile', '-File', path,
                 '-Module', os.path.join(KIT_DIR, 'PilotKit.psm1'),
                 '-Py', sys.executable,
                 '-Warn', self.warn,
                 '-Fail', self.fail],
                capture_output=True, text=True, cwd=REPO_ROOT)
        finally:
            shutil.rmtree(directory, ignore_errors=True)
        self.assertEqual(result.returncode, 0,
                         '%s\n%s' % (result.stdout, result.stderr))
        line = [row for row in result.stdout.splitlines()
                if row.strip().startswith('{')][-1]
        type(self)._state = json.loads(line)
        return type(self)._state

    def test_the_trap_is_really_there(self):
        """Ловушка проверяется первой, иначе всё ниже доказывало бы не то."""
        state = self.state()
        expected = os.environ.get('PILOT_POWERSHELL_MAJOR')
        if expected:
            self.assertEqual(int(state['ps_major']), int(expected),
                             'задача просила PowerShell %s, а регрессия '
                             'отработала на %s'
                             % (expected, state['ps_major']))
        self.assertEqual(state['eap_before'], 'Stop',
                         'вся проверка идёт под тем же предпочтением, что и '
                         'настоящие скрипты')
        self.assertTrue(
            state['raw_capture_yields_error_record'] or state['raw_capture_threw'],
            'прямой захват через 2>&1 обязан отдать stderr объектом '
            'ErrorRecord (7) или сразу бросить NativeCommandError (5.1). Если '
            'здесь пришла обычная строка, эта версия PowerShell дефект не '
            'воспроизводит и регрессия ниже ничего не доказывает')
        if int(state['ps_major']) == 5:
            # [REASON]: платформа живого сбоя. На ней прямой захват обязан
            # именно БРОСИТЬ, иначе воспроизведение неполно.
            self.assertTrue(state['raw_capture_threw'],
                            'на Windows PowerShell 5.1 прямой захват обязан '
                            'бросать; он бросил на сервере')
            self.assertIn('NativeCommandError', state['raw_capture_error_id'])

    def test_a_warning_on_stderr_does_not_end_a_successful_run(self):
        """Случай 1: stdout, DeprecationWarning в stderr, выход 0."""
        state = self.state()
        self.assertFalse(state['ok_threw'],
                         'успешный процесс, написавший предупреждение, не '
                         'должен останавливать шаг: %s'
                         % state.get('ok_error_id'))
        self.assertEqual(state['ok_exit'], 0)
        self.assertTrue(state['ok_stdout_has_line'])
        self.assertTrue(state['ok_stderr_kept'],
                        'строка stderr обязана сохраниться, а не быть '
                        'выброшенной')
        self.assertTrue(state['ok_text_has_both'])
        # [REASON]: порядок строк ВНУТРИ потока -- то, что делает журнал
        # читаемым, и он воспроизводим. Межпотоковый порядок здесь НЕ
        # утверждается: он зависит от момента сброса буфера python и на разных
        # платформах разный. Прежняя редакция утверждала именно его и падала
        # на CI через раз.
        self.assertTrue(state['ok_stdout_keeps_order'],
                        'строки stdout обязаны сохранить свой порядок')
        self.assertTrue(state['ok_text_keeps_order'],
                        'объединённый текст обязан сохранить порядок строк '
                        'одного потока')
        self.assertTrue(state['ok_stderr_not_in_stdout'],
                        'stdout остаётся данными: предупреждение в него не '
                        'подмешивается')

    def test_a_real_failure_still_returns_its_exit_code(self):
        """Случай 2: stderr и ненулевой код. Код обязан дойти целым."""
        state = self.state()
        self.assertFalse(state['bad_threw'])
        self.assertEqual(state['bad_exit'], 2,
                         'настоящий код процесса, а не потерянный в '
                         'NativeCommandError')
        self.assertTrue(state['bad_stderr_kept'])
        self.assertTrue(state['bad_stdout_kept'])

    def test_the_preference_is_restored_after_success_and_after_failure(self):
        """Случай 3: предпочтение вызывающего не остаётся ослабленным."""
        state = self.state()
        for key in ('eap_after_ok', 'eap_after_bad', 'eap_after_missing',
                    'eap_at_end'):
            self.assertEqual(state[key], 'Stop',
                             '%s: помощник обязан вернуть предпочтение '
                             'вызывающего' % key)

    def test_a_process_that_cannot_start_still_refuses(self):
        """[REASON]: 'Continue' на время вызова не должен глушить настоящее.

        Отсутствующий бинарник -- не диагностическая строка, а несостоявшийся
        запуск. Помощник обязан отказать, а не вернуть чужой $LASTEXITCODE.
        """
        state = self.state()
        self.assertTrue(state['missing_binary_threw'])

    def test_git_data_is_not_polluted_by_git_diagnostics(self):
        """Случай 6: Invoke-PilotGit при диагностике в stderr."""
        state = self.state()
        self.assertTrue(
            state['git_head_is_a_bare_sha'],
            'HEAD обязан вернуться голым SHA. Получено: %r. При склейке через '
            '2>&1 к нему прилипала бы любая строка, которую git пишет в stderr, '
            'и каждая сверка ревизий в комплекте ломалась бы'
            % state['git_head_value'])
        self.assertEqual(state['git_noisy_exit'], 0)
        self.assertTrue(state['git_noisy_stderr_present'],
                        'отрицательный контроль: git действительно написал в '
                        'stderr, иначе проверка ниже не различает два случая')
        self.assertTrue(state['git_noisy_stdout_clean'],
                        'диагностика git не попадает в поле данных')
        self.assertTrue(state['git_noisy_stderr_kept'],
                        'и при этом не выброшена')
        self.assertTrue(state['git_clean_status_is_empty'],
                        'чистое дерево обязано читаться чистым')

    def test_a_failing_git_still_reports_its_exit_code(self):
        state = self.state()
        self.assertTrue(state['git_failure_exit_is_nonzero'])
        self.assertTrue(state['git_failure_stderr_kept'])
        self.assertTrue(state['git_refuses_without_allowfailure'],
                        'ненулевой код не скрывается')


@unittest.skipIf(PWSH is None, 'no PowerShell in this environment')
class TheRealScriptStatementsAreSafe(unittest.TestCase):
    """Настоящие строки запуска из настоящих файлов, исполненные как есть.

    [REASON]: проверять помощник в отрыве от скриптов мало. Живой сбой пришёл
    не из модуля, а из строки в STAGING_DEPLOY_AND_MIGRATE.ps1, и следующая
    такая строка остановила бы шаг 4. Поэтому оператор вынимается ИЗ ФАЙЛА и
    исполняется: если он снова станет прямым захватом через 2>&1, stderr
    придёт вызывающему объектом ErrorRecord, и проверка это увидит на любой
    версии PowerShell -- а на 5.1 ещё и бросит.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix='pilot_stmt_')
        cls.child = os.path.join(cls.tmp, 'child.py')
        with open(cls.child, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(RECALC_CHILD)
        cls.db = os.path.join(cls.tmp, 'throwaway.db')

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_statement(self, statement, name):
        directory = tempfile.mkdtemp(prefix='pilot_stmt_ps_')
        try:
            path = os.path.join(directory, 'stmt.ps1')
            with open(path, 'w', encoding='utf-8', newline='\n') as handle:
                handle.write(SCRIPT_STATEMENT_SCRIPT)
            result = subprocess.run(
                [PWSH, '-NoProfile', '-File', path,
                 '-Module', os.path.join(KIT_DIR, 'PilotKit.psm1'),
                 '-Statement', statement,
                 '-VariableName', name,
                 '-Py', sys.executable,
                 '-Tool', self.child,
                 '-Db', self.db],
                capture_output=True, text=True, cwd=REPO_ROOT)
        finally:
            shutil.rmtree(directory, ignore_errors=True)
        self.assertEqual(result.returncode, 0,
                         '%s\n%s' % (result.stdout, result.stderr))
        line = [row for row in result.stdout.splitlines()
                if row.strip().startswith('{')][-1]
        return json.loads(line)

    def test_every_native_capture_in_the_scripts_is_safe(self):
        """Случаи 4 и 5: миграция staging, повторная миграция, три пересчёта."""
        checked = []
        for name in NATIVE_CAPTURE_FILES:
            with open(os.path.join(KIT_DIR, name), encoding='utf-8') as handle:
                text = handle.read()
            for number, statement, variable in find_native_capture_statements(
                    text):
                state = self.run_statement(statement, variable)
                self.assertFalse(
                    state['threw'],
                    '%s:%d -- строка комплекта остановила шаг на предупреждении '
                    'в stderr (%s):\n%s'
                    % (name, number, state['error_id'], statement))
                self.assertFalse(
                    state['reaches_caller_as_error_record'],
                    '%s:%d -- stderr дошёл до вызывающего объектом ErrorRecord. '
                    "На Windows PowerShell 5.1 под $ErrorActionPreference = "
                    "'Stop' это терминирующий NativeCommandError: процесс "
                    'обрывается, и его настоящий exit code не читается вовсе. '
                    'Ровно так шаг 2 умер на SRV-YOQSH.\n%s'
                    % (name, number, statement))
                self.assertEqual(
                    state['eap_after'], 'Stop',
                    '%s:%d -- предпочтение вызывающего не восстановлено'
                    % (name, number))
                checked.append('%s:%d:%s' % (name, number, variable))

        # [REASON]: без этой проверки пустой список операторов прошёл бы как
        # успех, и переименование любой из пяти строк тихо сняло бы её с
        # контроля.
        self.assertEqual(
            len(checked), NATIVE_CAPTURE_EXPECTED,
            'комплект обязан запускать native-процессы ровно в пяти местах, а '
            'исполнено %d: %s' % (len(checked), checked))
        self.assertEqual(len(set(checked)), len(checked),
                         'каждое место -- отдельный оператор: %s' % checked)


class NoUnsafeNativeCaptureRemains(unittest.TestCase):
    """Случай 7: прямых захватов через 2>&1 вне помощника не осталось.

    [REASON]: эта проверка работает и там, где PowerShell не установлен, и
    падает на базовом коммите по настоящей причине -- в нём шесть таких мест.
    Она же ловит возврат дефекта в любой НОВОЙ строке комплекта, которую
    исполняемые проверки выше ещё не знают.
    """

    def setUp(self):
        self.files = {}
        for name in PS_FILES:
            with open(os.path.join(KIT_DIR, name), encoding='utf-8') as handle:
                self.files[name] = handle.read()

    def unsafe_lines(self):
        found = []
        for name, text in self.files.items():
            for number, stripped in powershell_code_lines(text):
                if '2>&1' not in stripped:
                    continue
                # Единственное законное место -- строка захвата внутри
                # Invoke-PilotNative.
                if (name == 'PilotKit.psm1'
                        and stripped == '$captured = & $FilePath @Arguments 2>&1'):
                    continue
                found.append('%s:%d %s' % (name, number, stripped))
        return found

    def test_no_script_captures_a_native_process_with_a_bare_redirect(self):
        remaining = self.unsafe_lines()
        self.assertEqual(
            remaining, [],
            'прямой захват native-процесса через 2>&1 под '
            "$ErrorActionPreference = 'Stop' -- это тот самый дефект, который "
            'остановил шаг 2 на SRV-YOQSH. Оставшиеся места:\n%s'
            % '\n'.join(remaining))

    def test_the_one_legal_capture_lives_in_the_helper(self):
        """Отрицательный контроль: исключение выше не пустое и не всеядное."""
        module = self.files['PilotKit.psm1']
        self.assertIn('$captured = & $FilePath @Arguments 2>&1', module,
                      'единственный законный захват должен существовать, '
                      'иначе проверка выше проходит вакуумно')
        helper = module.split('function Invoke-PilotNative {')[1]
        helper = helper.split('\nfunction ')[0]
        self.assertIn('$captured = & $FilePath @Arguments 2>&1', helper,
                      'и он должен лежать ВНУТРИ помощника')

    def test_the_helper_lowers_the_preference_only_around_the_call(self):
        module = self.files['PilotKit.psm1']
        helper = module.split('function Invoke-PilotNative {')[1]
        helper = helper.split('\nfunction ')[0]
        self.assertIn("$previous = $ErrorActionPreference", helper)
        self.assertIn("$ErrorActionPreference = 'Continue'", helper)
        self.assertIn('$ErrorActionPreference = $previous', helper)
        self.assertIn('} finally {', helper,
                      'восстановление обязано быть в finally, иначе исключение '
                      'оставит предпочтение ослабленным')

    def test_no_script_silences_python_warnings_instead_of_fixing_this(self):
        """[REASON]: глушение предупреждения лечит симптом и прячет причину.

        Следующий DeprecationWarning придёт из другого места и снова остановит
        шаг. Кроме того, PYTHONWARNINGS изменил бы поведение самого продукта на
        площадке, а комплект обязан наблюдать, а не менять.
        """
        for name, text in self.files.items():
            self.assertNotIn('PYTHONWARNINGS', text, name)
            self.assertNotIn('-W ignore', text, name)

    def test_no_script_lowers_the_preference_for_its_whole_body(self):
        for name in PS_SCRIPTS:
            text = self.files[name]
            self.assertIn("$ErrorActionPreference = 'Stop'", text, name)
            self.assertNotIn("$ErrorActionPreference = 'Continue'", text,
                             '%s: предпочтение ослабляется только внутри '
                             'помощника, не в теле скрипта' % name)

    def test_the_helper_is_exported(self):
        export = self.files['PilotKit.psm1'].split(
            'Export-ModuleMember -Function')[1]
        self.assertIn('Invoke-PilotNative', export)

# ═══ С3. Кавычки в аргументе native-процесса на PowerShell 5.1 ══════════════

NATIVE_QUOTING_SCRIPT = r"""
param([string]$Module, [string]$Py, [string]$Block, [string]$FakeRoot, [string]$Mode)
Import-Module $Module -Force
$ErrorActionPreference = 'Stop'
$results = [ordered]@{}
$results['ps_major'] = $PSVersionTable.PSVersion.Major
$results['ps_edition'] = if ($PSVersionTable.PSEdition) { [string]$PSVersionTable.PSEdition } else { 'Desktop' }

# 5.1 не знает этой переменной, и её семантика передачи аргументов И ЕСТЬ
# Legacy. На 7 режим выставляется явно, чтобы дефект воспроизводился там же.
$hasSwitch = [bool](Get-Variable PSNativeCommandArgumentPassing -ErrorAction SilentlyContinue)
if ($hasSwitch) { $PSNativeCommandArgumentPassing = $Mode }
$results['has_argument_passing_switch'] = $hasSwitch
$results['mode'] = if ($hasSwitch) { [string]$PSNativeCommandArgumentPassing } else { 'legacy-by-version' }

# Подставной пакет playwright: настоящий python, настоящий успешный импорт.
$env:PYTHONPATH = $FakeRoot

# --- ЛОВУШКА. Историческая строка комплекта, слово в слово. -----------------
$old = & $Py -c 'import playwright; print("PLAYWRIGHT_IMPORT=PASS")' 2>&1
$results['old_form_exit'] = $LASTEXITCODE
$results['old_form_text'] = (($old | ForEach-Object { $_.ToString() }) -join ' | ')

# --- Настоящий блок ИЗ ФАЙЛА, при импортируемом playwright ------------------
$K = [pscustomobject]@{ CollectorPython = $Py }
$blockThrew = $false
$printed = @()
try {
    $printed = @(Invoke-Expression $Block)
} catch {
    $blockThrew = $true
    $results['block_error'] = [string]$_.Exception.Message
}
$results['block_threw_with_playwright'] = $blockThrew
$results['block_printed'] = ($printed -join ' | ')

# --- Тот же блок, когда playwright ДЕЙСТВИТЕЛЬНО не импортируется -----------
$env:PYTHONPATH = ''
$refusedThrew = $false
$refusedPrinted = @()
try {
    $refusedPrinted = @(Invoke-Expression $Block)
} catch {
    $refusedThrew = $true
    $results['refusal_message'] = [string]$_.Exception.Message
}
$results['block_threw_without_playwright'] = $refusedThrew
$results['block_printed_without_playwright'] = ($refusedPrinted -join ' | ')

$results | ConvertTo-Json -Compress
"""


def extract_playwright_block(text):
    """Вынуть из файла блок проверки playwright целиком.

    [REASON]: тест обязан исполнять КОД ИЗ ФАЙЛА. Извлечение построено так,
    чтобы находить обе формы -- и прежнюю (python сам печатал ответ), и
    нынешнюю (печатает PowerShell). Иначе на прежней форме тест промолчал бы
    «блок не найден» вместо того, чтобы упасть по настоящей причине.
    """
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('& $K.CollectorPython -c ') and not stripped.startswith('#'):
            start = index
            break
    if start is None:
        return None
    # [REASON]: подтверждение могло уехать и ВВЕРХ, до проверки кода возврата.
    # Окно, начинающееся строго с вызова, такую перестановку не увидело бы --
    # мутация «напечатать PASS раньше проверки» прошла мимо первой редакции
    # этого извлечения.
    while (start > 0
           and lines[start - 1].strip().startswith('Write-Output "PLAYWRIGHT_IMPORT=')):
        start -= 1
    collected = []
    depth = 0
    end = start
    for offset in range(start, len(lines)):
        line = lines[offset]
        collected.append(line)
        depth += line.count('{') - line.count('}')
        end = offset
        if offset > start and depth <= 0:
            break
    # Печать ответа могла переехать из python в PowerShell -- забрать и её.
    for offset in range(end + 1, min(end + 4, len(lines))):
        candidate = lines[offset].strip()
        if candidate == '':
            continue
        if candidate.startswith('Write-Output "PLAYWRIGHT_IMPORT='):
            collected.append(lines[offset])
        break
    return '\n'.join(collected)


@unittest.skipIf(PWSH is None, 'no PowerShell in this environment')
class NativeArgumentQuotingSurvivesPowerShell51(unittest.TestCase):
    """Аргумент native-процесса не должен нести в себе кавычек.

    [REASON]: на BAK-TEX11 шаг 3 дошёл до проверки Playwright и упал с
    `NameError: name 'PASS' is not defined`. Windows PowerShell 5.1 не
    экранирует двойные кавычки, найденные ВНУТРИ аргумента, когда собирает
    командную строку для native-процесса, поэтому
    `-c 'import playwright; print("PLAYWRIGHT_IMPORT=PASS")'` доехал до python
    как `import playwright; print(PLAYWRIGHT_IMPORT=PASS)`. Это ключевое слово
    функции, а `PASS` -- имя, которого нет. То есть `import playwright` УЖЕ
    отработал успешно, и отказ был ложным.

    [REASON]: почему это не поймала ни одна проверка. PowerShell 7.3 исправил
    передачу аргументов через $PSNativeCommandArgumentPassing, и в режиме
    'Standard' та же строка работает. Windows-задача CI гоняет НАБОР ТЕСТОВ на
    5.1, а не этот скрипт, а на семёрке дефекта попросту нет. Поэтому проверка
    ниже выставляет режим 'Legacy' там, где переменная есть, и полагается на
    саму версию там, где её нет.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix='pilot_quoting_')
        # Подставной пакет: настоящий python, настоящий успешный импорт.
        package = os.path.join(cls.tmp, 'playwright')
        os.makedirs(package)
        with open(os.path.join(package, '__init__.py'), 'w',
                  encoding='utf-8', newline='\n') as handle:
            handle.write('# stand-in for the real package; import must succeed\n')
        cls._state = {}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def state(self, mode='Legacy'):
        if mode in type(self)._state:
            return type(self)._state[mode]
        with open(os.path.join(KIT_DIR, 'BAK_TEX11_DJI_COLLECT_TO_STAGING.ps1'),
                  encoding='utf-8') as handle:
            block = extract_playwright_block(handle.read())
        self.assertIsNotNone(block, 'блок проверки playwright не найден в файле')
        directory = tempfile.mkdtemp(prefix='pilot_quoting_ps_')
        try:
            path = os.path.join(directory, 'quoting.ps1')
            with open(path, 'w', encoding='utf-8', newline='\n') as handle:
                handle.write(NATIVE_QUOTING_SCRIPT)
            result = subprocess.run(
                [PWSH, '-NoProfile', '-File', path,
                 '-Module', os.path.join(KIT_DIR, 'PilotKit.psm1'),
                 '-Py', sys.executable,
                 '-Block', block,
                 '-FakeRoot', self.tmp,
                 '-Mode', mode],
                capture_output=True, text=True, cwd=REPO_ROOT)
        finally:
            shutil.rmtree(directory, ignore_errors=True)
        self.assertEqual(result.returncode, 0,
                         '%s\n%s' % (result.stdout, result.stderr))
        line = [row for row in result.stdout.splitlines()
                if row.strip().startswith('{')][-1]
        type(self)._state[mode] = json.loads(line)
        return type(self)._state[mode]

    def test_the_trap_is_really_there(self):
        """Ловушка проверяется первой, иначе всё ниже доказывало бы не то."""
        state = self.state('Legacy')
        expected = os.environ.get('PILOT_POWERSHELL_MAJOR')
        if expected:
            self.assertEqual(int(state['ps_major']), int(expected),
                             'задача просила PowerShell %s, а регрессия '
                             'отработала на %s'
                             % (expected, state['ps_major']))
        if int(state['ps_major']) == 5:
            self.assertFalse(state['has_argument_passing_switch'],
                             '5.1 не знает $PSNativeCommandArgumentPassing; '
                             'если знает -- это не 5.1')
        self.assertNotEqual(
            state['old_form_exit'], 0,
            'историческая строка обязана падать под семантикой 5.1. Она '
            'вернула %s и вывела %r -- значит эта консоль дефект не '
            'воспроизводит, и проверка ниже ничего не доказывает'
            % (state['old_form_exit'], state['old_form_text']))
        self.assertIn(
            "name 'PASS' is not defined", state['old_form_text'],
            'ошибка обязана быть ТОЙ ЖЕ, что пришла с BAK-TEX11, а не любой '
            'другой. Получено: %r' % state['old_form_text'])

    def test_the_block_from_the_file_passes_when_playwright_imports(self):
        """Тот самый случай, на котором комплект встал на BAK-TEX11.

        playwright импортируется по-настоящему: рядом лежит подставной пакет,
        и python его действительно находит. Значит шаг обязан сказать PASS, а
        не отказать.
        """
        state = self.state('Legacy')
        self.assertFalse(
            state['block_threw_with_playwright'],
            'playwright импортируется, а шаг отказал: %s'
            % state.get('block_error'))
        self.assertIn(
            'PLAYWRIGHT_IMPORT=PASS', state['block_printed'],
            'шаг обязан подтвердить импорт. Выведено: %r'
            % state['block_printed'])

    def test_the_block_still_refuses_when_playwright_is_missing(self):
        """Fail-closed не ослаблен: без playwright шаг по-прежнему отказывает.

        [REASON]: без этой проверки предыдущую можно было бы «починить»,
        убрав проверку кода возврата вовсе.
        """
        state = self.state('Legacy')
        self.assertTrue(state['block_threw_without_playwright'],
                        'отсутствие playwright обязано остановить шаг')
        self.assertIn('REFUSED', state.get('refusal_message', ''))
        self.assertIn('installs nothing', state.get('refusal_message', ''))
        self.assertNotIn(
            'PLAYWRIGHT_IMPORT=PASS',
            state['block_printed_without_playwright'],
            'PASS не должен печататься раньше проверки кода возврата')

    def test_powershell_7_does_not_reproduce_it(self):
        """Отрицательный контроль: объяснение слепого пятна CI.

        [REASON]: на 7 в режиме 'Standard' историческая строка РАБОТАЕТ. Это и
        есть причина, по которой дефект дожил до живого запуска: ни одна
        проверка на семёрке его увидеть не могла. Проверка идёт только там,
        где режим вообще существует.
        """
        legacy = self.state('Legacy')
        if not legacy['has_argument_passing_switch']:
            self.skipTest('на 5.1 режима передачи аргументов нет')
        standard = self.state('Standard')
        self.assertEqual(standard['old_form_exit'], 0,
                         'в режиме Standard историческая строка обязана '
                         'работать, иначе объяснение слепого пятна неверно')
        self.assertIn('PLAYWRIGHT_IMPORT=PASS', standard['old_form_text'])


class NoNativeArgumentCarriesQuotes(unittest.TestCase):
    """Статическое правило, выведенное из измерения выше.

    [REASON]: работает и там, где PowerShell не установлен, и ловит возврат
    дефекта в ЛЮБОЙ новой строке комплекта, которой исполняемые проверки ещё
    не знают.
    """

    def setUp(self):
        self.files = {}
        for name in PS_FILES:
            with open(os.path.join(KIT_DIR, name), encoding='utf-8') as handle:
                self.files[name] = handle.read()

    def native_lines(self):
        """Строки, запускающие native-процесс, без комментариев."""
        for name, text in self.files.items():
            for number, stripped in powershell_code_lines(text):
                if re.search(r'&\s+\$[A-Za-z_]', stripped):
                    yield name, number, stripped

    def test_no_native_argument_literal_contains_a_double_quote(self):
        """[REASON]: именно эти кавычки 5.1 съедает, собирая командную строку."""
        offenders = []
        for name, number, line in self.native_lines():
            for literal in re.findall(r"'([^']*)'", line):
                if '"' in literal:
                    offenders.append('%s:%d %s' % (name, number, line))
        self.assertEqual(
            offenders, [],
            'аргумент native-процесса несёт в себе двойные кавычки. Windows '
            'PowerShell 5.1 их не экранирует, и python получит не то, что '
            'написано. Ровно так шаг 3 упал на BAK-TEX11:\n%s'
            % '\n'.join(offenders))

    def test_no_python_c_argument_carries_any_quote(self):
        """`python -c` -- самый опасный случай: аргумент и есть программа."""
        offenders = []
        for name, number, line in self.native_lines():
            match = re.search(r"-c\s+(['\"])(.*?)\1", line)
            if not match:
                continue
            code = match.group(2)
            if '"' in code or "'" in code:
                offenders.append('%s:%d %s' % (name, number, line))
        self.assertEqual(
            offenders, [],
            'аргумент `python -c` не должен содержать кавычек вовсе: то, что '
            'внутри, -- программа, и её нельзя доверять пересборке командной '
            'строки на 5.1.\n%s' % '\n'.join(offenders))

    def test_the_step_prints_the_answer_from_powershell(self):
        """[REASON]: печатать ответ должен PowerShell, а не python.

        Пока подтверждение печатает сам python, оно снова окажется внутри
        аргумента -- в кавычках, которые 5.1 съест.
        """
        text = self.files['BAK_TEX11_DJI_COLLECT_TO_STAGING.ps1']
        self.assertIn('Write-Output "PLAYWRIGHT_IMPORT=PASS"', text)
        for _, _, line in self.native_lines():
            self.assertNotIn('PLAYWRIGHT_IMPORT=PASS', line,
                             'подтверждение снова уехало внутрь аргумента '
                             'native-процесса: %s' % line)

    def test_the_answer_is_printed_only_after_the_check(self):
        """Порядок: запуск -> проверка кода возврата -> подтверждение.

        [REASON]: подтверждение, напечатанное раньше проверки, врёт ровно в том
        случае, ради которого проверка существует. Мутация с перестановкой
        прошла мимо исполняемого теста, потому что уехала за границу
        извлекаемого блока; здесь она видна прямо.
        """
        text = self.files['BAK_TEX11_DJI_COLLECT_TO_STAGING.ps1']
        self.assertEqual(
            text.count('Write-Output "PLAYWRIGHT_IMPORT=PASS"'), 1,
            'подтверждение должно печататься ровно один раз')
        run = text.index('& $K.CollectorPython -c "import playwright"')
        guard = text.index('REFUSED: playwright is not importable')
        answer = text.index('Write-Output "PLAYWRIGHT_IMPORT=PASS"')
        self.assertLess(run, guard, 'проверка кода возврата идёт после запуска')
        self.assertLess(guard, answer,
                        'подтверждение печатается ПОСЛЕ проверки кода '
                        'возврата, а не до неё')

    def test_the_run_still_installs_nothing(self):
        """Правка не должна была превратиться в установку окружения."""
        text = self.files['BAK_TEX11_DJI_COLLECT_TO_STAGING.ps1']
        self.assertNotIn('pip install', text)
        self.assertNotIn('playwright install', text)
        self.assertNotIn('ensurepip', text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
