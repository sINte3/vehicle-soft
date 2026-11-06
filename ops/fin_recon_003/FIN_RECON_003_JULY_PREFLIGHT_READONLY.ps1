<#
FIN-RECON-003 July 2026 -- ONE read-only preflight before any selective import (v2).

Run on SRV-YOQSH in Windows PowerShell 5.1, AFTER the guarded release is on
the server, from any folder, with -ExpectedHead followed by the FULL
40-character SHA of the approved release commit exactly as the release
instruction gives it (the instruction holds the ready command; this header
deliberately does not).

It does NOT write the database, stop or start services, fetch/pull/checkout
git, import, migrate or create customers. It reads service state, reads git
state (--no-optional-locks), copies the live database through SQLite's
online backup API from a mode=ro connection into a new folder under %TEMP%,
runs every check on that copy, runs the server's own import code in memory
(python -I -B: no bytecode is written into the repository), deletes the
folder, and writes ONE ASCII text file to -OutputDir: counts, commit and
blob ids, SHA-256 digests -- no customer names, phones, notes, passwords or
tokens. It can be sent back as it is.

Verdict, three independent parts:
  DB_CODE_PREFLIGHT = OK | FAILED | INCOMPLETE  (database + code checks)
  CODE_RELEASE_PIN  = the SHA given | NOT_GIVEN  (HEAD must equal it)
  SERVICE_HEALTH    = VERIFIED | NOT_VERIFIED    (required services Running)
  RESULT=PREFLIGHT_PASSED only when the checks are OK, HEAD equals the given
  SHA and the required services are Running. Without -ExpectedHead the result
  is RESULT=CODE_RELEASE_NOT_PINNED; a stopped or missing required service
  gives RESULT=DB_CODE_OK_SERVICE_HEALTH_NOT_VERIFIED -- never PASSED. An
  abbreviated or malformed SHA stops before anything is read (exit 2).

A PASSED result is a technical fact, never an approval to import.
#>
param(
  [string]$RepoDir = 'C:\transport-report',
  [string]$DbPath = 'C:\transport-report\instance\transport.db',
  [string]$Python = 'C:\Program Files\Python314\python.exe',
  [string]$OutputDir = [Environment]::GetFolderPath('Desktop'),
  # [REASON]: REQUIRED = the web application the import screens and the
  # upload path live in (docs/GPS_RELEASE_V134_RUNBOOK.md serves the site
  # from it). The two bots were Running at the v1.34 release too, but no
  # import path goes through them: INFO, reported, never part of the verdict.
  [string[]]$RequiredServices = @('TransportReport'),
  [string[]]$InfoServices = @('TransportBot', 'TransportBot003'),
  # [REASON]: architect's finding V2-G1. The approved blobs prove the import
  # path carries the guarded code, but release options A and B both carry
  # it: only the full SHA of the approved release commit proves that the
  # isolated option A -- and not main with its GPS/agro-work changes -- is
  # what runs. Without it the verdict is CODE_RELEASE_NOT_PINNED, never
  # PREFLIGHT_PASSED, so forgetting the argument cannot pass.
  [string]$ExpectedHead = ''
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2

$CoreSha256 = '30298d1962e9b346071ddcaae500c61e61a8f91dda6a7810da2389260227da7b'
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$lines = New-Object System.Collections.Generic.List[string]
function Add-Line([string]$Text) { $lines.Add($Text); Write-Host $Text }

function Invoke-Native([string]$Exe, [string[]]$Arguments) {
  # [REASON]: Windows PowerShell 5.1 turns a native program's stderr into a
  # terminating error under ErrorActionPreference=Stop.
  $old = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try {
    $out = & $Exe @Arguments 2>$null
    $code = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $old
  }
  return [pscustomobject]@{ Code = $code; Out = @($out | ForEach-Object { [string]$_ }) }
}

Add-Line "PREFLIGHT=FIN_RECON_003_JULY_READONLY_V2 stamp=$stamp host=$env:COMPUTERNAME"
foreach ($p in @($RepoDir, $DbPath, $Python, $OutputDir)) {
  if (-not (Test-Path -LiteralPath $p)) {
    Add-Line "RESULT=STOP missing path: $p"
    exit 2
  }
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  Add-Line 'RESULT=STOP git is not on PATH'
  exit 2
}
if ($ExpectedHead -and $ExpectedHead -cnotmatch '^[0-9a-f]{40}$') {
  Add-Line 'RESULT=STOP -ExpectedHead must be the full 40-character lowercase commit SHA'
  exit 2
}
$pinned = [bool]$ExpectedHead
Add-Line ('CODE_RELEASE_PIN=' + $(if ($pinned) { $ExpectedHead } else { 'NOT_GIVEN' }))

# --- services: state only, nothing is started or stopped --------------------
$serviceProblems = New-Object System.Collections.Generic.List[string]
foreach ($name in $RequiredServices) {
  $svc = Get-Service -Name $name -ErrorAction SilentlyContinue
  if (-not $svc) {
    Add-Line "SERVICE_REQUIRED $name=NOT_FOUND"
    $serviceProblems.Add("$name NOT_FOUND")
  } else {
    Add-Line "SERVICE_REQUIRED $name=$($svc.Status) start=$($svc.StartType)"
    if ([string]$svc.Status -ne 'Running') { $serviceProblems.Add("$name $($svc.Status)") }
  }
}
foreach ($name in $InfoServices) {
  $svc = Get-Service -Name $name -ErrorAction SilentlyContinue
  if ($svc) { Add-Line "SERVICE_INFO $name=$($svc.Status) start=$($svc.StartType)" }
  else { Add-Line "SERVICE_INFO $name=NOT_FOUND" }
}
if ($RequiredServices.Count -eq 0) { $serviceProblems.Add('no required service named') }

# --- database and code: online-backup copy, read-only checks, probes --------
$core = @'
# -*- coding: utf-8 -*-
"""FIN-RECON-003 July: READ-ONLY preflight core (stdlib only, ASCII source).

Run by FIN_RECON_003_JULY_PREFLIGHT_READONLY.ps1 (v2); can be run alone:
  python -I -B -X utf8 fr3_preflight_core.py --db DB --tmp EMPTY_DIR --repo REPO

The live database is opened ONLY as file:...?mode=ro and copied with the
SQLite online backup API into --tmp; every query runs on that copy. Nothing
is written to the database or to the repository. Output: ASCII KEY=VALUE and
CHECK lines -- counts and SHA-256 digests only, no names, no phones, no notes.
"""
import argparse
import hashlib
import os
import re
import sqlite3
import subprocess
import sys
import time
from urllib.parse import quote

# Expected after directory phase 2 of 05.11.2026, computed OFFLINE from the
# 04.11 read-only snapshot + phase-1 server report + phase-2 plan.
EXPECTED = {
    'customers': 1009,
    'aliases_all': 1052,
    'customers_904_1009': 106,
    'works_total': 1288,
    'july_works': 6,
    'migrations': 65,
}
EXPECTED_HASHES = {
    # SHA-256 over sorted canonical lines (see db_checks); no names leave
    # the server, only whether these digests match.
    'customers_le_903': 'e5dddcf4262e2ec5b926e506a739d23faf470d8b1760f2d724298bb8ac73cb9f',
    'customers_904_996': 'e2382a1d95e0fa5d2116a5f0c5b73302274b743bd34755b1e86db156b9034fdb',
    'customers_997_1009': '5a946e824b40ea3b6f67afd7b582034a865f0cc68b721826faee214eb4e56ec1',
    'customers_gt_1009': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
    'aliases_to_le_903': '311ba6a54783a3914ca4e21e81c518cb0c6157d44f90f0691f36bc6781cfa78a',
    'aliases_to_904_996': 'ddac6d41b8d45c6056d6c870b9dc637c5f12d6b72a296a2820c616bc185da9c4',
    'aliases_to_997_1009': '42139cea71db817b31b6105cdff2cfffc11a49ade485576d41e6a1dfdae0ad6d',
    'aliases_to_gt_1009': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
    'july_works': '9756bc8a256a0d9ad1b5c1a5e8dbc67b86382f3eecaceab1ad1f6a3fed169457',
}

FIVE_BOOKS = (
    '\u0414\u0440\u043e\u043d \u0492\u0438\u0436\u0434\u0443\u0432\u043e\u043d \u041f\u0422\u0417 \u0418\u044e\u043b\u044c.xlsx',
    '\u0418\u042e\u041b\u042c \u0410\u0433\u0440\u043e\u043a\u043b\u0430\u0441\u0442\u0435\u0440.xlsx',
    '\u041a\u043e\u0433\u043e\u043d \u041f\u0422\u0417 \u0418\u042e\u041b\u042c.xlsx',
    '\u0421\u0435\u0440\u0432\u0438\u0441 \u0414\u0440\u043e\u043d \u043c\u0430\u044a\u043b\u0443\u043c\u043e\u0442 \u0418\u044e\u043b\u044c.xlsx',
    '\u0428\u043e\u0444\u0438\u0440\u043a\u043e\u043d \u041f\u0422\u0417 \u0414\u0440\u043e\u043d \u043c\u0430\u044a\u043b\u0443\u043c\u043e\u0442 \u0418\u044e\u043b\u044c.xlsx',
)
KEYS_TO_PROBE = {
    'generic_fukaro': '\u0444\u0443\u043a\u0430\u0440\u043e',
    'generic_romitan_t_fukaro': '\u0440\u043e\u043c\u0438\u0442\u0430\u043d \u0442. \u0444\u0443\u043a\u0430\u0440\u043e',
    'r01_kogon_fukaro': '\u043a\u043e\u0433\u043e\u043d \u0444\u0443\u043a\u0430\u0440\u043e',
    'hold_gou_bobo': '\u0433\u043e\u0443 \u0431\u043e\u0431\u043e',
}
R02_KEY = ('\u0414\u0440\u043e\u043d \u0492\u0438\u0436\u0434\u0443\u0432\u043e\u043d \u041f\u0422\u0417 \u0418\u044e\u043b\u044c.xlsx'
           '!\u0441\u0432\u043e\u0434 \u0438\u0447\u0438 (\u0423\u0441\u043c\u043e\u043d)!11')
WORK_COLUMNS = (
    'period_month', 'work_date_from', 'work_date_to', 'date_raw',
    'drone_operator_id', 'operator_raw', 'drone_customer_id', 'customer_raw',
    'area_ha', 'price_per_ha', 'amount', 'other_costs', 'received_amount',
    'received_kind', 'payment_type', 'subdivision_name', 'source_file',
    'source_sheet', 'source_row', 'import_batch', 'note', 'created_at',
    'payer_unidentified')

_UZ = {ord(k): (v or None) for k, v in {
    '\u049a': '\u041a', '\u0492': '\u0413', '\u04b2': '\u0425',
    '\u040e': '\u0423', '\u04b6': '\u0416', '\u049c': '\u041a',
    '\u049b': '\u043a', '\u0493': '\u0433', '\u04b3': '\u0445',
    '\u045e': '\u0443', '\u04b7': '\u0436',
    '\u042a': '', '\u044a': '', '\u042c': '', '\u044c': '',
    '\u0401': '\u0415', '\u0451': '\u0435'}.items()}


def fold(value):
    """The importer's uz_fold: Uzbek letters folded, lower, spaces collapsed."""
    if value is None:
        return ''
    return re.sub(r'\s+', ' ', str(value).translate(_UZ).lower()).strip()


def num(v):
    return '' if v is None or v == '' else '%.2f' % float(v)


def intx(v):
    return '' if v is None or v == '' else str(int(float(v)))


def txt(v):
    return '' if v is None else str(v)


def digest(lines):
    h = hashlib.sha256()
    for line in sorted(lines):
        h.update(line.encode('utf-8'))
        h.update(b'\n')
    return h.hexdigest()


class Out(object):
    def __init__(self):
        self.failed = []
        self.hashes = {}

    def kv(self, key, value):
        print('%s=%s' % (key, str(value).encode('ascii', 'backslashreplace')
                         .decode('ascii')))

    def check(self, name, ok, expected, actual, info=False):
        state = 'INFO' if info else ('PASS' if ok else 'FAIL')
        if state == 'FAIL':
            self.failed.append(name)
        self.kv('CHECK %s' % name, '%s expected=%s actual=%s'
                % (state, expected, actual))


APPROVED_BLOBS = {
    'tools/import_drone_works.py': '73c169c0633a622774a8f77110fd7b15b6416975',
    'tools/drone_works_selective.py': '02a8a725890e761cbec132af86492d356b8b2afa',
    'tools/drone_works_fingerprint_export.py': 'c54946a62fec1140e514859c3530c5e0603ab97b',
    'drone_works_upload.py': 'f1c58d20ee4785855f4b5e859900fd4918ee42e7',
}
# Ancestry is reported, never required: release option A carries the PR
# branch tips 43ae59b/65252cc but not main's merge commits 45eb10c/bc0a7bc.
KNOWN_COMMITS = (
    ('pr260_branch_tip', '43ae59b251f2dc1ec3e882770aa10c92aa3a9343'),
    ('pr261_branch_tip', '65252cc2a9854a8cd428f975adb4077e1c4b1a49'),
    ('pr260_merge_on_main', '45eb10c5e883051fb7c8176d1f6aa4a81ef629d9'),
    ('pr261_merge_on_main', 'bc0a7bc5614f6bc4f64321bcfdbec265a9aa1e49'),
)


def git(repo, *args):
    """Read-only git; --no-optional-locks keeps even the index untouched."""
    p = subprocess.run(['git', '-C', repo, '--no-optional-locks'] + list(args),
                       capture_output=True)
    return p.returncode, p.stdout.decode('utf-8', 'replace').strip()


def code_checks(out, repo, expected_head):
    """The code is identified by content, not by searching it for words."""
    code, head = git(repo, 'rev-parse', 'HEAD')
    out.kv('SERVER_HEAD', head if code == 0 else 'UNKNOWN')
    out.kv('SERVER_TAGS_AT_HEAD', git(repo, 'tag', '--points-at', 'HEAD')[1]
           .replace('\n', ',') or '-')
    if expected_head:
        out.check('code_head_is_pinned_release', head == expected_head,
                  expected_head[:12], head[:12])
    else:
        out.kv('CODE_HEAD_PIN', 'none given (-ExpectedHead); blobs decide')
    code, status = git(repo, 'status', '--porcelain', '--untracked-files=no')
    dirty = [l for l in status.splitlines() if l.strip()] if code == 0 else ['?']
    out.check('code_tracked_files_clean', not dirty, 0, len(dirty))
    for path, want in sorted(APPROVED_BLOBS.items()):
        label = os.path.basename(path).replace('.py', '')
        code, at_head = git(repo, 'rev-parse', 'HEAD:' + path)
        at_head = at_head if code == 0 else 'MISSING'
        code, in_tree = git(repo, 'hash-object', '--', path)
        in_tree = in_tree if code == 0 else 'MISSING'
        out.check('code_blob_head_' + label, at_head == want, want[:12], at_head[:12])
        out.check('code_blob_worktree_' + label, in_tree == at_head,
                  'head', 'head' if in_tree == at_head else in_tree[:12])
    for label, commit in KNOWN_COMMITS:
        code, _ = git(repo, 'merge-base', '--is-ancestor', commit, 'HEAD')
        out.kv('INFO_ANCESTOR_' + label.upper(),
               {0: 'YES', 1: 'NO'}.get(code, 'UNKNOWN_COMMIT'))
    deployed = os.path.join(repo, 'docs', 'DEPLOYED.md')
    if os.path.isfile(deployed):
        for line in open(deployed, encoding='utf-8'):
            if line.startswith('| production |'):
                out.kv('DEPLOYED_MD_PRODUCTION_ROW', line.strip())
                break


# The probe runs the SERVER'S modules in a separate `python -I -B` process
# (no bytecode is written into the repository) against an in-memory database
# that has the schema of the copy and no rows. It writes nothing to disk.
PROBE = r"""
import io, os, re, sqlite3, subprocess, sys, tempfile
repo, copy = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.path.join(repo, 'tools'))
sys.path.insert(0, repo)
R02 = '\u0414\u0440\u043e\u043d \u0492\u0438\u0436\u0434\u0443\u0432\u043e\u043d \u041f\u0422\u0417 \u0418\u044e\u043b\u044c.xlsx!\u0441\u0432\u043e\u0434 \u0438\u0447\u0438 (\u0423\u0441\u043c\u043e\u043d)!11'


TOKENS = re.compile(r'[A-Za-z_][A-Za-z0-9_]*=-?[0-9]+|[A-Z][A-Z0-9_]{2,}|[A-Z][A-Za-z]*(?:Error|Exception)')


def say(name, ok, detail):
    # [REASON]: exception text from the server code carries source keys, and
    # July sheet names are people's names. The output file is sent back as
    # it is, so only error codes, exception types and counters leave here.
    detail = ' '.join(TOKENS.findall(str(detail)))[:160]
    print('PROBE %s=%s %s' % (name, 'PASS' if ok else 'FAIL',
                               detail.encode('ascii', 'backslashreplace').decode('ascii')))


def schema_db():
    from urllib.parse import quote
    src = sqlite3.connect('file:%s?mode=ro' % quote(copy.replace(chr(92), '/')), uri=True)
    ddl = [r[0] for r in src.execute(
        "SELECT sql FROM sqlite_master WHERE type IN ('table', 'index') "
        "AND sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY type DESC")]
    src.close()
    mem = sqlite3.connect(':memory:')
    for statement in ddl:
        mem.execute(statement)
    mem.commit()
    return mem


def july_row(customer):
    import datetime
    day = datetime.date(2026, 7, 15)
    return {'source_file': 'probe.xlsx', 'source_sheet': 'probe', 'source_row': 3,
            'period_month': '2026-07', 'work_date_from': day, 'work_date_to': day,
            'date_raw': '2026-07-15', 'date_kind': 'date', 'operator_raw': 'probe',
            'customer_raw': customer, 'subdivision_name': 'probe', 'area_ha': 1.0,
            'price_per_ha': 1.0, 'amount': 1.0, 'other_costs': 0.0,
            'received_amount': 0.0, 'received_kind': 'received',
            'payment_type': 'cash', 'note': '', 'drone_operator_id': 1,
            'drone_customer_id': None, 'payer_unidentified': False,
            'customer_is_new': False}


try:
    import import_drone_works as imp
except Exception as exc:
    say('importer_imports', False, '%s: %s' % (type(exc).__name__, exc))
    sys.exit(0)
say('importer_imports', True, '')

# 1. The shared write gate: a July work without an approved list, from a
#    renamed book, must be refused BEFORE anything is written.
try:
    mem = schema_db()
    result = imp.RunResult()
    result.rows = [july_row('')]
    try:
        imp.apply_rows(mem, result, 'fr3_probe')
        refused, why = False, 'apply_rows returned'
    except Exception as exc:
        refused, why = True, '%s: %s' % (type(exc).__name__, exc)
    written = mem.execute('SELECT COUNT(*) FROM drone_works').fetchone()[0]
    mem.close()
    say('legacy_and_web_july_write_refused',
        refused and written == 0 and 'JULY_2026_WORK_REQUIRES_ALLOWLIST' in why,
        'written=%d %s' % (written, why))
except Exception as exc:
    say('legacy_and_web_july_write_refused', False, 'probe error %s: %s' % (type(exc).__name__, exc))

# 2. R02 is refused whatever its row hash.
try:
    import drone_works_selective as sel
    try:
        sel.validate_row_hashes([], {R02: '0' * 64})
        ok, why = False, 'accepted'
    except Exception as exc:
        ok, why = 'EXCLUDED_SOURCE_KEY' in str(exc), str(exc)
    say('r02_excluded_in_every_list', ok, why)
except Exception as exc:
    say('r02_excluded_in_every_list', False, 'probe error %s: %s' % (type(exc).__name__, exc))


# 3. R01: the exact spelling is an unidentified citizen; the folded variant
#    is still held. Resolved against an empty directory, in memory.
class NoDirectory(object):
    aliases = {}
    existing_provenance = set()

    def resolve_operator(self, raw, subdivision):
        return 1, 'probe'


for name, spelling, want in (
        ('r01_exact_spelling_unidentified', '\u041a\u043e\u0433\u043e\u043d \u0424\u0443\u043a\u0430\u0440\u043e', (None, True, False)),
        ('r01_folded_variant_still_held', '\u041a\u043e\u0433\u043e\u043d \u0424\u0443\u049b\u0430\u0440\u043e', (None, False, True))):
    try:
        result = imp.RunResult()
        result.rows = [july_row(spelling)]
        imp.resolve(result, NoDirectory())
        row = result.rows[0]
        got = (row.get('drone_customer_id'), bool(row.get('payer_unidentified')),
               bool(row.get('payer_decision_pending')))
        say(name, got == want, 'has_customer=%d unidentified=%d pending=%d'
            % (got[0] is not None, got[1], got[2]))
    except Exception as exc:
        say(name, False, 'probe error %s: %s' % (type(exc).__name__, exc))

# 4. Both CLIs start under `python -I` and refuse a missing folder cleanly.
for name, script, args in (
        ('guarded_cli_runs_isolated', 'drone_works_selective.py',
         ['--manifest', 'm', '--db', 'd', '--only-keys', 'k', '--only-keys-sha256', '0' * 64]),
        ('fingerprint_export_runs_isolated', 'drone_works_fingerprint_export.py',
         ['--manifest', 'm', '--db', 'd', '--keys', 'k', '--out', 'o.csv'])):
    path = os.path.join(repo, 'tools', script)
    if not os.path.isfile(path):
        say(name, False, 'missing tools/' + script)
        continue
    with tempfile.TemporaryDirectory() as cwd:
        p = subprocess.run([sys.executable, '-I', '-B', '-X', 'utf8', path,
                            '--dir', 'no_such_dir'] + args, capture_output=True, cwd=cwd)
    text = (p.stdout + p.stderr).decode('utf-8', 'replace')
    say(name, p.returncode == 2 and 'Traceback' not in text,
        'exit=%d %s' % (p.returncode, text.strip().splitlines()[-1] if text.strip() else ''))
"""


CODE_TOKENS = re.compile(r'[A-Za-z_][A-Za-z0-9_]*=-?[0-9]+|[A-Z][A-Z0-9_]{2,}|[A-Z][A-Za-z]*(?:Error|Exception)')


def codes_only(text):
    # Second fence, in this process: a probe of a foreign code version may
    # print anything; only tokens of the code alphabet reach the file.
    return ' '.join(CODE_TOKENS.findall(text))[:160]


def behaviour_probes(out, repo, copy, tmp):
    path = os.path.join(tmp, 'fr3_probe.py')
    with open(path, 'w', encoding='utf-8') as stream:
        stream.write(PROBE)
    p = subprocess.run([sys.executable, '-I', '-B', '-X', 'utf8', path, repo, copy],
                       capture_output=True, cwd=tmp, timeout=600)
    seen = 0
    for line in p.stdout.decode('ascii', 'replace').splitlines():
        if line.startswith('PROBE '):
            name, rest = line[6:].split('=', 1)
            state, _, detail = rest.partition(' ')
            out.check('behaviour_' + name, state == 'PASS', 'PASS', state)
            out.kv('DETAIL behaviour_' + name, codes_only(detail) or '-')
            seen += 1
    if p.returncode != 0 or seen < 7:
        tail = p.stderr.decode('utf-8', 'replace').strip().splitlines()[-1:] or ['-']
        out.check('behaviour_probe_completed', False, 7, '%d (exit %d %s)'
                  % (seen, p.returncode, codes_only(tail[0]) or '-'))


def copy_live(out, db, tmp):
    if not os.path.isfile(db):
        out.kv('DB_FOUND', 'NO')
        return None
    st = os.stat(db)
    out.kv('DB_FOUND', 'YES')
    out.kv('DB_BYTES_BEFORE', st.st_size)
    out.kv('DB_MTIME_BEFORE', time.strftime('%Y-%m-%d %H:%M:%S',
                                            time.localtime(st.st_mtime)))
    out.kv('DB_WAL_PRESENT', 'YES' if os.path.exists(db + '-wal') else 'NO')
    uri = 'file:%s?mode=ro' % quote(os.path.abspath(db).replace('\\', '/'))
    copy_path = os.path.join(tmp, 'preflight_copy.sqlite')
    src = sqlite3.connect(uri, uri=True, timeout=30)
    try:
        dst = sqlite3.connect(copy_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    out.kv('DB_OPENED_MODE', 'ro (online backup into a temporary copy)')
    return copy_path


def db_checks(out, path):
    con = sqlite3.connect('file:%s?mode=ro' % quote(path.replace('\\', '/')),
                          uri=True)
    try:
        q = con.execute('PRAGMA quick_check').fetchall()
        out.check('copy_quick_check', q == [('ok',)], 'ok',
                  q[0][0] if len(q) == 1 else '%d lines' % len(q))
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        need = ('drone_works', 'drone_customers', 'drone_customer_aliases',
                'drone_operators', 'schema_migrations')
        missing = [t for t in need if t not in tables]
        out.check('tables_present', not missing, 'all', missing or 'all')
        if missing:
            return
        cols = {r[1] for r in con.execute('PRAGMA table_info(drone_works)')}
        lack = [c for c in WORK_COLUMNS if c not in cols]
        out.check('drone_works_columns_for_apply', not lack, 'all',
                  lack or 'all')

        def unique_on(table, columns):
            for idx in con.execute('PRAGMA index_list(%s)' % table):
                if idx[2]:
                    got = [r[2] for r in con.execute(
                        'PRAGMA index_info("%s")' % idx[1])]
                    if got == list(columns):
                        return True
            return False
        out.check('unique_source_provenance', unique_on(
            'drone_works', ('source_file', 'source_sheet', 'source_row')),
            True, unique_on('drone_works', ('source_file', 'source_sheet',
                                            'source_row')))
        out.check('unique_alias_key', unique_on(
            'drone_customer_aliases', ('normalized_key',)), True,
            unique_on('drone_customer_aliases', ('normalized_key',)))
        mig = con.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0]
        out.check('migrations_registered', mig == EXPECTED['migrations'],
                  EXPECTED['migrations'], mig)

        n = lambda sql, *a: con.execute(sql, a).fetchone()[0]
        customers = n('SELECT COUNT(*) FROM drone_customers')
        aliases = n('SELECT COUNT(*) FROM drone_customer_aliases')
        active = n('SELECT COUNT(*) FROM drone_customer_aliases '
                   'WHERE is_active IS NOT 0')
        band = n('SELECT COUNT(*) FROM drone_customers '
                 'WHERE id BETWEEN 904 AND 1009')
        out.check('customers', customers == EXPECTED['customers'],
                  EXPECTED['customers'], customers)
        out.check('aliases_all', aliases == EXPECTED['aliases_all'],
                  EXPECTED['aliases_all'], aliases)
        out.kv('ALIASES_ACTIVE', active)
        out.check('customers_904_1009', band == EXPECTED['customers_904_1009'],
                  EXPECTED['customers_904_1009'], band)
        out.kv('CUSTOMER_MAX_ID', n('SELECT MAX(id) FROM drone_customers'))

        ranges = (('le_903', 0, 903), ('904_996', 904, 996),
                  ('997_1009', 997, 1009), ('gt_1009', 1010, 10 ** 9))
        for label, lo, hi in ranges:
            lines = ['\t'.join((intx(r[0]), txt(r[1]), txt(r[2]), intx(r[3])))
                     for r in con.execute(
                         'SELECT id, name, customer_type, is_active FROM '
                         'drone_customers WHERE id BETWEEN ? AND ?', (lo, hi))]
            h = digest(lines)
            key = 'customers_' + label
            out.hashes[key] = h
            want = EXPECTED_HASHES.get(key)
            out.check('hash_' + key, want == h, (want or '?')[:16], h[:16],
                      info=want is None)
            lines = ['\t'.join((txt(r[0]), intx(r[1]), intx(r[2])))
                     for r in con.execute(
                         'SELECT normalized_key, drone_customer_id, is_active '
                         'FROM drone_customer_aliases WHERE drone_customer_id '
                         'BETWEEN ? AND ?', (lo, hi))]
            h = digest(lines)
            key = 'aliases_to_' + label
            out.hashes[key] = h
            want = EXPECTED_HASHES.get(key)
            out.check('hash_' + key, want == h, (want or '?')[:16], h[:16],
                      info=want is None)

        alias_rows = list(con.execute(
            'SELECT normalized_key, drone_customer_id, is_active '
            'FROM drone_customer_aliases'))
        card_rows = list(con.execute('SELECT id, name FROM drone_customers'))
        for label, key in KEYS_TO_PROBE.items():
            f = fold(key)
            a = [r for r in alias_rows if fold(r[0]) == f]
            c = [r for r in card_rows if fold(r[1]) == f]
            must_be_absent = label.startswith(('r01_', 'hold_'))
            out.check('folded_key_%s_aliases' % label,
                      (not a) if must_be_absent else True,
                      0 if must_be_absent else 'info', len(a),
                      info=not must_be_absent)
            out.check('folded_key_%s_cards' % label,
                      (not c) if must_be_absent else True,
                      0 if must_be_absent else 'info', len(c),
                      info=not must_be_absent)
        for cid in (136, 138):
            t = con.execute('SELECT customer_type FROM drone_customers '
                            'WHERE id = ?', (cid,)).fetchone()
            out.check('customer_%d_type' % cid, t is not None and t[0] == 'external',
                      'external', t[0] if t else 'MISSING')

        works = n('SELECT COUNT(*) FROM drone_works')
        out.check('works_total', works == EXPECTED['works_total'],
                  EXPECTED['works_total'], works)
        july = list(con.execute(
            "SELECT id, period_month, work_date_from, work_date_to, "
            "drone_operator_id, drone_customer_id, area_ha, amount, "
            "payment_type, source_file, source_sheet, source_row, import_batch "
            "FROM drone_works WHERE period_month = '2026-07' "
            "OR substr(work_date_from, 1, 7) = '2026-07' "
            "OR substr(work_date_to, 1, 7) = '2026-07'"))
        out.check('july_works', len(july) == EXPECTED['july_works'],
                  EXPECTED['july_works'], len(july))
        lines = ['\t'.join((intx(r[0]), txt(r[1]), txt(r[2])[:10],
                            txt(r[3])[:10], intx(r[4]), intx(r[5]), num(r[6]),
                            num(r[7]), txt(r[8]), txt(r[9]), txt(r[10]),
                            intx(r[11]), txt(r[12]))) for r in july]
        h = digest(lines)
        out.hashes['july_works'] = h
        want = EXPECTED_HASHES.get('july_works')
        out.check('hash_july_works_unchanged_since_0411', want == h,
                  (want or '?')[:16], h[:16], info=want is None)
        out.kv('JULY_WORK_IDS', ','.join(sorted((intx(r[0]) for r in july),
                                               key=int)) or '-')
        exact = n('SELECT COUNT(*) FROM drone_works WHERE source_file IN '
                  '(?,?,?,?,?)', *FIVE_BOOKS)
        out.check('works_from_five_books_exact', exact == 0, 0, exact)
        five = {fold(b) for b in FIVE_BOOKS}
        folded = sum(c for f, c in con.execute(
            'SELECT source_file, COUNT(*) FROM drone_works '
            'WHERE source_file IS NOT NULL GROUP BY source_file')
            if fold(f) in five)
        out.check('works_from_five_books_folded_name', folded == 0, 0, folded)
        r02 = R02_KEY.split('!')
        hit = n('SELECT COUNT(*) FROM drone_works WHERE source_file = ? AND '
                'source_sheet = ? AND source_row = ?', r02[0], r02[1],
                int(r02[2]))
        out.check('r02_key_not_in_db', hit == 0, 0, hit)
        if 'drone_work_imports' in tables:
            for status, count in con.execute(
                    "SELECT status, COUNT(*) FROM drone_work_imports "
                    "WHERE period_month = '2026-07' GROUP BY status"):
                out.kv('WEB_UPLOADS_2026_07_%s' % txt(status).upper(), count)
            pending = n("SELECT COUNT(*) FROM drone_work_imports WHERE "
                        "period_month = '2026-07' AND status = 'preview'")
            out.check('web_upload_july_preview_pending', pending == 0, 0,
                      pending)
        else:
            out.kv('WEB_UPLOAD_JOURNAL', 'absent')
    finally:
        con.close()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', required=True)
    ap.add_argument('--tmp', required=True)
    ap.add_argument('--repo', required=True)
    ap.add_argument('--expected-head', default='')
    ap.add_argument('--print-hashes', action='store_true',
                    help='developer aid: print the digests for EXPECTED_HASHES')
    a = ap.parse_args(argv)
    out = Out()
    if os.listdir(a.tmp):
        out.kv('RESULT', 'REFUSED_TMP_NOT_EMPTY')
        return 2
    code_checks(out, a.repo, a.expected_head.strip().lower())
    try:
        copy = copy_live(out, a.db, a.tmp)
        if copy is None:
            out.kv('RESULT', 'REFUSED_DB_NOT_FOUND')
            return 2
        db_checks(out, copy)
        behaviour_probes(out, a.repo, copy, a.tmp)
    except sqlite3.Error as exc:
        out.kv('DB_ERROR', '%s: %s' % (type(exc).__name__, exc))
        out.kv('RESULT', 'REFUSED_DB_ERROR')
        return 2
    finally:
        for name in os.listdir(a.tmp):
            try:
                os.remove(os.path.join(a.tmp, name))
            except OSError:
                pass
    if a.print_hashes:
        for key in sorted(out.hashes):
            out.kv('FULL_HASH ' + key, out.hashes[key])
    out.kv('CHECKS_FAILED', len(out.failed))
    out.kv('CHECKS_FAILED_NAMES', ','.join(out.failed) or '-')
    return 0


if __name__ == '__main__':
    sys.exit(main())
'@
$core = ($core -replace "`r`n", "`n") + "`n"
$tmp = Join-Path $env:TEMP ('fr3_preflight_' + $stamp)
$pyCode = -1
try {
  New-Item -ItemType Directory -Path $tmp | Out-Null
  $work = Join-Path $tmp 'copy'
  New-Item -ItemType Directory -Path $work | Out-Null
  $corePath = Join-Path $tmp 'fr3_preflight_core.py'
  [IO.File]::WriteAllText($corePath, $core, (New-Object System.Text.UTF8Encoding($false)))
  $sha = (Get-FileHash -Algorithm SHA256 -LiteralPath $corePath).Hash.ToLower()
  if ($sha -ne $CoreSha256) {
    Add-Line "RESULT=STOP embedded check code changed (sha256 $sha)"
    exit 2
  }
  Add-Line "CORE_SHA256=$sha"
  $pyArgs = @('-I', '-B', '-X', 'utf8', $corePath, '--db', $DbPath, '--tmp', $work, '--repo', $RepoDir)
  # [REASON]: Windows PowerShell 5.1 silently drops an empty-string argument
  # to a native program; `--expected-head ''` arrived as a bare flag and the
  # core stopped with a usage error. The flag is passed only when it is set.
  if ($ExpectedHead) { $pyArgs += @('--expected-head', $ExpectedHead) }
  $py = Invoke-Native $Python $pyArgs
  $pyCode = $py.Code
  foreach ($l in $py.Out) { Add-Line $l }
} finally {
  if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue }
}
Add-Line ("TEMP_COPY_REMOVED=" + $(if (Test-Path -LiteralPath $tmp) { 'NO' } else { 'YES' }))

# --- verdict ------------------------------------------------------------------
$fails = @($lines | Where-Object { $_ -match '^CHECK .*=FAIL ' })
$dbCode = if ($pyCode -ne 0) { 'INCOMPLETE' } elseif ($fails.Count -gt 0) { 'FAILED' } else { 'OK' }
Add-Line ("DB_CODE_PREFLIGHT=$dbCode checks_failed=" + $fails.Count)
if ($serviceProblems.Count -eq 0) {
  Add-Line 'SERVICE_HEALTH=VERIFIED'
} else {
  Add-Line ('SERVICE_HEALTH=NOT_VERIFIED (' + ($serviceProblems -join '; ') + ')')
}
if ($dbCode -eq 'INCOMPLETE') {
  Add-Line "RESULT=PREFLIGHT_INCOMPLETE python exit $pyCode"
} elseif ($dbCode -eq 'FAILED') {
  Add-Line 'RESULT=PREFLIGHT_FAILED'
} elseif (-not $pinned) {
  Add-Line 'RESULT=CODE_RELEASE_NOT_PINNED (rerun with -ExpectedHead set to the full SHA of the approved release commit)'
} elseif ($serviceProblems.Count -gt 0) {
  Add-Line 'RESULT=DB_CODE_OK_SERVICE_HEALTH_NOT_VERIFIED'
} else {
  Add-Line 'RESULT=PREFLIGHT_PASSED'
}
Add-Line 'IMPORT_AUTHORIZED=NO (a technical preflight is not an approval; the signed acceptance act is separate)'
$outFile = Join-Path $OutputDir ('FIN_RECON_003_JULY_PREFLIGHT_' + $stamp + '.txt')
[IO.File]::WriteAllLines($outFile, $lines.ToArray(), [Text.Encoding]::ASCII)
Write-Host "WRITTEN=$outFile"
