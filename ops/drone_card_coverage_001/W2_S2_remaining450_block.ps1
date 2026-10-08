& {
  # DRONE-CARD-COVERAGE-001 -- W2+S2: the 450 flights of the frozen pilot that the canary
  # (W1, 08.10.2026) did not visit, then the staging recalculation of exactly those 450 and the
  # measurement of all 500. docs/DRONE_CARD_COVERAGE_001.md, section 14, "W2+S2".
  # Run on SRV-YOQSH:  powershell.exe -NoProfile -ExecutionPolicy Bypass -File <this file>
  # Production: v1.23 (3c5c8c5, docs/DEPLOYED.md, 08.10.2026, after W1 ran on 3434996); its DJI code
  # (drone_collector, dji_area, drones.py) is that of 8df5683. Any other HEAD stops this file before DJI.
  $ErrorActionPreference = 'Stop'
  $ProgressPreference = 'SilentlyContinue'
  $expectedHost = 'srv-yoqsh'
  $src          = 'C:\VehicleSoft_CardPilot\src'
  $cpy          = 'C:\transport-report\drone_collector\.venv\Scripts\python.exe'
  $python       = 'C:\Program Files\Python314\python.exe'
  $root         = 'C:\transport-report-staging'
  $db           = 'C:\transport-report-staging\instance\transport.db'
  $service      = 'TransportReportStaging'
  $bots         = @('TransportBotStaging', 'TransportBot003Staging')
  $site         = 'http://10.103.25.14:5051'
  $prodRoot     = 'C:\transport-report'
  $prodDb       = 'C:\transport-report\instance\transport.db'
  $prodExpected = '3c5c8c5688b6a1586ef868c0cb62d750e366f34b'
  $prodBase     = '8df568394a840054ef6f842c6a8b272ca4c31aa8'
  $prodDji      = @('drone_collector', 'dji_area', 'drones.py')
  $prodNames    = @('TransportBot', 'TransportBot003', 'TransportReport')
  $prodTasks    = @('DroneCollectorDaily', 'DroneAreaDaily', 'DjiAreaRefresh')
  $session      = 'C:\transport-report\drone_collector\data\storage_state.json'
  $lock         = 'C:\transport-report\drone_collector\data\collector.lock'
  $prodLog      = 'C:\transport-report\drone_collector\logs\collector.log'
  $pin          = '39eab503069b7bb01a8342542edcbebbfc2210c2'
  $runRoot      = 'D:\transport-report-backups\staging\card_pilot'
  $baseline     = 'D:\transport-report-backups\staging\card_pilot\baseline_20261003_072956'
  $snapshot     = 'D:\transport-report-backups\staging\card_pilot\baseline_20261003_072956\snapshot\transport_20261003_073000_card_pilot_baseline.db'
  $manifestSha  = '1782d19899ed1e06345e859542d4705d6ac750d6ac57078577f5c03ad6dc66f8'
  $pilotSha     = '456b6486f41c8cc66bb8e745196346ea0817e5f6c159c990d8070dcc08e21e8e'
  $canarySha    = '5913a88d1bfcecdfe0586fd0a81007ef7cc771d777a2754ebd8b5c1ec2e641da'
  $pilotCount   = 500
  $canaryCount  = 50
  $isoTask      = 'DjiAreaRefreshStaging'
  $work         = 'C:\VehicleSoft_CardPilot'
  $w1Run        = 'C:\VehicleSoft_CardPilot\w1\20261008_151912'
  $w1Log        = 'C:\VehicleSoft_CardPilot\card_pilot_w1_20261008_151912.log'
  $w1RunId      = 'sources:ids-file:20261008T101922Z'
  $w1New        = 200
  $w1Exact      = 14
  $w1Identified = 3
  $w1NoKey      = 6
  $w1NotInCatalog = 27
  $w1Confirmed  = $w1Exact + $w1Identified
  $w2Root       = 'C:\VehicleSoft_CardPilot\w2'
  $svcKey       = 'HKLM:\SYSTEM\CurrentControlSet\Services'
  $maxCollectMin = 100
  $minGapMin    = 130
  $maxNoCardRun = 5
  $maxCardRefused = 2
  $stamp        = Get-Date -Format 'yyyyMMdd_HHmmss'
  $planDir      = Join-Path $baseline 'plan'
  $siteParams   = $svcKey + '\' + $service + '\Parameters'
  $prodWant     = 'TransportBot=Running TransportBot003=Running TransportReport=Running'
  $collectorRx  = 'drone_collector|dji_area_daily|dji_area_recalc|dji_area_backfill'
  $stopMarkers  = @(@('SESSION', '(?i)no longer signed in|SessionExpired|session (is )?(missing|expired)|expired during the run'), @('HTTP_429', '(?i)\b429\b|too many requests|rate.?limit'), @('HTTP_403', '(?i)\bHTTP 403\b|\b403 Forbidden\b|forbidden'), @('CAPTCHA', '(?i)captcha|verify you are human|challenge'), @('BROWSER_DEAD', '(?i)browser is not usable'))
  $helperText = @'
import csv, hashlib, json, os, re, sqlite3, sys
# W2 read-only checks (DRONE-CARD-COVERAGE-001). Databases are opened mode=ro; prints KEY=VALUE.
#   snapshot DB                                     counters and the field catalog digest
#   newrows DB IDS SRC_AFTER V4_AFTER SINCE UNTIL RUNID  where rows written after SRC_AFTER / in SINCE..UNTIL belong
#   runrows DB IDS RUNID                            the revisions of one run and the cards of IDS
#   fpdiff FP_A FP_B ALLOWED KEEP                   what changed between two fingerprints
#   diag DB RESULTS_CSV                             why NO_KEY and NOT_IN_CATALOG: counts, flight ids, key NAMES only
KEYLIKE = re.compile(r'^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}__)?[0-9a-f]{32}$')
def con_ro(path):
    if not os.path.isfile(path):
        raise SystemExit('NOT FOUND: ' + path)
    uri = 'file:%s?mode=ro' % os.path.abspath(path).replace('\\', '/').replace('?', '%3f').replace('#', '%23')
    return sqlite3.connect(uri, uri=True, timeout=30)
def one(con, sql, args=()):
    return con.execute(sql, args).fetchone()[0]
def read_ids(path):
    with open(path, encoding='utf-8-sig') as fh:
        return [int(l.split('#')[0]) for l in fh if l.split('#')[0].strip()]
def snapshot(con):
    out = {'SOURCE_MAX_ID': one(con, 'SELECT COALESCE(MAX(id), 0) FROM dji_source_revisions'),
           'SOURCE_ROWS': one(con, 'SELECT COUNT(*) FROM dji_source_revisions'),
           'EVIDENCE_ROWS': one(con, 'SELECT COUNT(*) FROM dji_flight_evidence'),
           'V4SUM_ROWS': one(con, 'SELECT COUNT(*) FROM dji_v4_summaries'),
           'V4SUM_MAX_ID': one(con, 'SELECT COALESCE(MAX(id), 0) FROM dji_v4_summaries')}
    h = hashlib.sha256()
    for table, cols in (('dji_land_snapshots', 'id, captured_at_utc, received_count'),
                        ('dji_land_revisions', 'id, land_uuid, geometry_md5'),
                        ('dji_land_geometries', 'id, content_md5, sha256, md5_verified')):
        for row in con.execute('SELECT %s FROM %s ORDER BY id' % (cols, table)):
            h.update((table + '|' + '|'.join(repr(v) for v in row) + '\n').encode('utf-8'))
    out['CATALOG_SHA256'] = h.hexdigest()
    return out
def newrows(con, ids, after, v4_after, since, until, run_id):
    marks = ','.join('?' * len(ids))
    new = 'FROM dji_source_revisions WHERE id > ?'
    return {
        'V4SUM_NEW': one(con, 'SELECT COUNT(*) FROM dji_v4_summaries WHERE id > ?', (v4_after,)),
        'V4SUM_NEW_OUTSIDE_IDS': one(con, 'SELECT COUNT(*) FROM dji_v4_summaries WHERE id > ? AND flight_id NOT IN (%s)' % marks, [v4_after] + ids),
        'NEW_REVISIONS': one(con, 'SELECT COUNT(*) ' + new, (after,)),
        'NEW_FLIGHTS': one(con, 'SELECT COUNT(DISTINCT flight_id) ' + new, (after,)),
        'NEW_OUTSIDE_IDS': one(con, 'SELECT COUNT(*) %s AND (flight_id IS NULL OR flight_id NOT IN (%s))' % (new, marks), [after] + ids),
        'NEW_OTHER_RUN': one(con, 'SELECT COUNT(*) %s AND (capture_run_id IS NULL OR capture_run_id <> ?)' % new, (after, run_id)),
        'NEW_REPEATED_TYPE': one(con, 'SELECT COUNT(*) FROM (SELECT 1 %s GROUP BY flight_id, source_type HAVING COUNT(*) > 1)' % new, (after,)),
        'RUN_ID_ROWS': one(con, 'SELECT COUNT(*) FROM dji_source_revisions WHERE capture_run_id = ?', (run_id,)),
        'IDS_SOURCES_SINCE': one(con, 'SELECT COUNT(*) FROM dji_source_revisions WHERE flight_id IN (%s) AND ((received_at >= ? AND received_at <= ?) OR (last_seen_at >= ? AND last_seen_at <= ?))' % marks, ids + [since, until, since, until]),
        'IDS_EVIDENCE_SINCE': one(con, 'SELECT COUNT(*) FROM dji_flight_evidence WHERE flight_id IN (%s) AND updated_at >= ? AND updated_at <= ?' % marks, ids + [since, until]),
        'EVIDENCE_OUTSIDE_SINCE': one(con, 'SELECT COUNT(*) FROM dji_flight_evidence WHERE flight_id NOT IN (%s) AND updated_at >= ?' % marks, ids + [since]),
    }
def runrows(con, ids, run_id):
    marks = ','.join('?' * len(ids))
    return {
        'RUN_ROWS': one(con, 'SELECT COUNT(*) FROM dji_source_revisions WHERE capture_run_id = ?', (run_id,)),
        'RUN_FLIGHTS': one(con, 'SELECT COUNT(DISTINCT flight_id) FROM dji_source_revisions WHERE capture_run_id = ?', (run_id,)),
        'RUN_OUTSIDE_IDS': one(con, 'SELECT COUNT(*) FROM dji_source_revisions WHERE capture_run_id = ? AND (flight_id IS NULL OR flight_id NOT IN (%s))' % marks, [run_id] + ids),
        'IDS_WITH_CARD': one(con, 'SELECT COUNT(*) FROM dji_flight_evidence WHERE flight_id IN (%s) AND card_revision_id IS NOT NULL' % marks, ids),
        'IDS_CARD_FROM_RUN': one(con, 'SELECT COUNT(*) FROM dji_flight_evidence e JOIN dji_source_revisions r ON r.id = e.card_revision_id WHERE e.flight_id IN (%s) AND r.capture_run_id = ?' % marks, ids + [run_id]),
    }
def fpdiff(path_a, path_b, allowed, keep):
    with open(path_a, encoding='ascii') as fh:
        a = json.load(fh)
    with open(path_b, encoding='ascii') as fh:
        b = json.load(fh)
    pa, pb = a['period_current'], b['period_current']
    changed = sorted(k for k in set(pa) | set(pb) if pa.get(k) != pb.get(k))
    allowed = set(str(i) for i in allowed)
    keep = set(str(i) for i in keep)
    return {'SAME_RAW': int(a['raw_area_ha'] == b['raw_area_ha']),
            'SAME_DECISIONS': int(a['drone_area_decisions'] == b['drone_area_decisions']),
            'SAME_MIGRATIONS': int(a['schema_migrations'] == b['schema_migrations']),
            'SAME_SOURCES': int(a['source_revisions'] == b['source_revisions']),
            'CHANGED': len(changed),
            'CHANGED_OUTSIDE_ALLOWED': len([k for k in changed if k not in allowed]),
            'KEEP_CHANGED': len([k for k in changed if k in keep]),
            'ALLOWED_CHANGED': len([k for k in changed if k in allowed])}
def current_attr(con, fid):
    return con.execute('SELECT * FROM dji_field_attributions WHERE flight_id = ? AND superseded_at IS NULL ORDER BY id DESC LIMIT 1', (fid,)).fetchone()
def card_data(con, fid):
    ev = con.execute('SELECT card_revision_id FROM dji_flight_evidence WHERE flight_id = ?', (fid,)).fetchone()
    if not ev or not ev['card_revision_id']:
        return None, 'no_card'
    row = con.execute('SELECT storage_kind, body_text FROM dji_source_revisions WHERE id = ?', (ev['card_revision_id'],)).fetchone()
    if not row or row['storage_kind'] != 'inline' or row['body_text'] is None:
        return None, 'body_not_inline'
    try:
        doc = json.loads(row['body_text'])
    except ValueError:
        return None, 'body_not_json'
    data = doc.get('data') if isinstance(doc, dict) else None
    if not isinstance(data, dict):
        return None, 'body_without_data'
    return data, None
def counter(items):
    out = {}
    for x in items:
        out[str(x)] = out.get(str(x), 0) + 1
    return ' '.join('%s:%d' % kv for kv in sorted(out.items())) or '-'
def diag(con, results_path):
    con.row_factory = sqlite3.Row
    with open(results_path, encoding='utf-8') as fh:
        rows = list(csv.DictReader(fh))
    lines = []
    nic = [int(r['flight_id']) for r in rows if r['after'] == 'NOT_IN_CATALOG']
    nok = [int(r['flight_id']) for r in rows if r['after'] == 'NO_KEY']
    conf = [int(r['flight_id']) for r in rows if r['confirmed_after'] == 'True']
    snap = con.execute('SELECT MIN(captured_at_utc), MAX(captured_at_utc), COUNT(*) FROM dji_land_snapshots').fetchone()
    lands = one(con, 'SELECT COUNT(DISTINCT land_uuid) FROM dji_land_revisions')
    contours = one(con, 'SELECT COUNT(DISTINCT geometry_md5) FROM dji_land_revisions')
    lines.append('CATALOG snapshots=%s first_utc=%s last_utc=%s land_records=%s contours=%s' % (snap[2], snap[0], snap[1], lands, contours))
    fmt, md5s, linked_known = {}, {}, 0
    for fid in nic:
        a = current_attr(con, fid)
        f = (a['geometry_key_format'] if a else None) or 'NONE'
        fmt.setdefault(f, []).append(fid)
        if a and a['geometry_md5']:
            md5s.setdefault(a['geometry_md5'], []).append(fid)
        if a and a['linked_land_uuid'] and con.execute('SELECT 1 FROM dji_land_revisions WHERE land_uuid = ? LIMIT 1', (a['linked_land_uuid'],)).fetchone():
            linked_known += 1
    in_catalog = sum(1 for m in md5s if con.execute('SELECT 1 FROM dji_land_revisions WHERE geometry_md5 = ? LIMIT 1', (m,)).fetchone())
    affected = 0
    for m in md5s:
        affected += one(con, 'SELECT COUNT(DISTINCT flight_id) FROM dji_field_attributions WHERE geometry_md5 = ? AND superseded_at IS NULL', (m,))
    lines.append('NOT_IN_CATALOG flights=%d key_format=[%s] distinct_contours=%d contours_on_several_flights=%d contours_found_in_catalog_now=%d linked_land_known_now=%d all_period_flights_on_these_contours=%d'
                 % (len(nic), ' '.join('%s:%d' % (k, len(v)) for k, v in sorted(fmt.items())), len(md5s), sum(1 for v in md5s.values() if len(v) > 1), in_catalog, linked_known, affected))
    for f, ids in sorted(fmt.items()):
        what = {'COMPOSITE_UUID_MD5': 'the card names a DJI land record the catalog has never seen, and its contour md5 is unknown',
                'PLAIN_MD5': 'the card carries only a contour md5, and the catalog has no land record with it'}.get(f, 'other key format')
        lines.append('NOT_IN_CATALOG %s: %s -- e.g. %s' % (f, what, ', '.join(str(i) for i in sorted(ids)[:5])))
    absent, empty, present, keylike, manual, modes, skipped = [], [], [], [], [], [], {}
    for fid in nok:
        data, why = card_data(con, fid)
        if data is None:
            skipped[why] = skipped.get(why, 0) + 1
            continue
        raw = data.get('geometry_md5')
        if 'geometry_md5' not in data:
            absent.append(fid)
        elif raw in (None, '') or (isinstance(raw, str) and not raw.strip()):
            empty.append(fid)
        else:
            present.append(fid)
        keylike.extend(sorted(k for k, v in data.items() if k != 'geometry_md5' and isinstance(v, str) and KEYLIKE.match(v.strip().lower())))
        manual.append(data.get('manual_mode'))
        modes.append(data.get('mode_name'))
    conf_manual, conf_modes = [], []
    for fid in conf:
        data, _why = card_data(con, fid)
        if data is not None:
            conf_manual.append(data.get('manual_mode'))
            conf_modes.append(data.get('mode_name'))
    lines.append('NO_KEY flights=%d key_field_absent=%d key_field_empty=%d key_in_card_but_not_read=%d key_like_values_in_other_fields=[%s] not_inspected=[%s]'
                 % (len(nok), len(absent), len(empty), len(present), counter(keylike), ' '.join('%s:%d' % kv for kv in sorted(skipped.items())) or '-'))
    lines.append('NO_KEY manual_mode=[%s] mode_name=[%s] -- e.g. %s' % (counter(manual), counter(modes), ', '.join(str(i) for i in sorted(nok)[:5])))
    lines.append('CONFIRMED manual_mode=[%s] mode_name=[%s]' % (counter(conf_manual), counter(conf_modes)))
    if present:
        lines.append('NO_KEY key_in_card_but_not_read -- e.g. %s' % ', '.join(str(i) for i in sorted(present)[:5]))
    return lines
def main(argv):
    if argv[0] == 'fpdiff':
        res = fpdiff(argv[1], argv[2], read_ids(argv[3]), read_ids(argv[4]))
    else:
        con = con_ro(argv[1])
        try:
            if argv[0] == 'snapshot':
                res = snapshot(con)
            elif argv[0] == 'newrows':
                res = newrows(con, read_ids(argv[2]), int(argv[3]), int(argv[4]), argv[5], argv[6], argv[7])
            elif argv[0] == 'runrows':
                res = runrows(con, read_ids(argv[2]), argv[3])
            elif argv[0] == 'diag':
                for line in diag(con, argv[2]):
                    print('DIAG ' + line)
                return 0
            else:
                raise SystemExit('unknown mode')
        finally:
            con.close()
    for k in sorted(res):
        print('%s=%s' % (k, res[k]))
    return 0
if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
'@
  function Get-ProdServices { (@($prodNames | ForEach-Object { $s = Get-Service -Name $_ -ErrorAction SilentlyContinue; if ($s) { $_ + '=' + $s.Status } else { $_ + '=missing' } }) -join ' ') }
  function Get-Sha([string]$p) { (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLower() }
  function Test-SameFile([string]$a, [string]$b) { (Get-Sha $a) -eq (Get-Sha $b) }
  function Get-FileState([string]$p) { $i = Get-Item -LiteralPath $p -Force; (Get-Sha $p) + ' bytes=' + $i.Length + ' changed_utc=' + $i.LastWriteTimeUtc.ToString('yyyy-MM-dd HH:mm:ss') }
  function Read-Ids([string]$p) { @(Get-Content -LiteralPath $p | ForEach-Object { ($_ -split '#')[0].Trim() } | Where-Object { $_ } | ForEach-Object { [int64]$_ }) }
  function Pct($x) { if ($null -eq $x) { return '-' } ([double]$x * 100).ToString('0.0', [System.Globalization.CultureInfo]::InvariantCulture) + '%' }
  function Read-Pairs([string[]]$lines) { $h = @{}; foreach ($l in $lines) { if ($l -match '^([A-Z0-9_]+)=(.*)$') { $h[$Matches[1]] = $Matches[2] } }; $h }
  function Read-Json([string]$p) { Get-Content -LiteralPath $p -Raw | ConvertFrom-Json }
  function Read-Utf8([string]$p) {
    # [REASON]: another window may still be writing this log; FileShare.ReadWrite lets it be read.
    $fs = New-Object System.IO.FileStream($p, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
    try {
      $r = New-Object System.IO.StreamReader($fs, (New-Object System.Text.UTF8Encoding($false)))
      $out = New-Object System.Collections.Generic.List[string]
      while ($null -ne ($l = $r.ReadLine())) { $out.Add($l) }
      ,$out.ToArray()
    } finally { $fs.Dispose() }
  }
  function Invoke-Helper([string[]]$helperArgs) {
    $o = @(& $python -I (Join-Path $w2 'w2_check.py') @helperArgs)
    if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: the read-only check $($helperArgs[0]) exit $LASTEXITCODE -- $($o -join ' ')" }
    Read-Pairs $o
  }
  function Read-Summary([string[]]$lines) {
    $h = @{}
    $s = @($lines | Where-Object { $_ -match 'RUN SUMMARY ' } | Select-Object -Last 1)
    if ($s.Count -eq 1) { foreach ($m in [regex]::Matches(($s[0] -replace '^.*RUN SUMMARY ', ''), '(\w+)=("[^"]*"|\S+)')) { $h[$m.Groups[1].Value] = $m.Groups[2].Value.Trim('"') } }
    $h
  }
  function Write-Masked([string[]]$lines, [string]$to) {
    # [REASON]: "(429 bytes)" is not a DJI answer, but the HTTP_429 marker matches its byte count.
    [System.IO.File]::WriteAllLines($to, [string[]]@($lines | ForEach-Object { $_ -replace '\(\d+ bytes\)', '(N bytes)' }), (New-Object System.Text.UTF8Encoding($false)))
  }
  function Get-RunState([string]$dir) {
    if (Test-Path -LiteralPath (Join-Path $dir 's2_done.txt')) { return 'COMPLETE' }
    if (Test-Path -LiteralPath (Join-Path $dir 'collector_gate.json')) { return 'S2_PENDING' }
    $logFile = Join-Path $dir 'collector_stdout.log'
    if (-not (Test-Path -LiteralPath $logFile)) { return 'NOT_STARTED' }
    $doneFile = Join-Path $dir 'collector_done.json'
    if (Test-Path -LiteralPath $doneFile) {
      $d = Read-Json $doneFile
      # [REASON]: the collector ended by itself (0 or 18) and nothing stopped it: what the gate needs
      # is on disk, so a later paste checks it again without DJI.
      if ((-not [string]$d.stop_why) -and (@(0, 18) -contains [int]$d.collector_exit)) { return 'GATE_PENDING' }
      # [REASON]: exit 24 (the production collector held the lock), or a collector that ended
      # before its first flight with nothing queued, never visited DJI.
      $flights = @(Read-Utf8 $logFile | Where-Object { $_ -match ': Flight \d+: ' }).Count
      $queued = @(Get-ChildItem -LiteralPath (Join-Path $dir 'outbox') -File -Recurse -ErrorAction SilentlyContinue).Count
      if (([int]$d.collector_exit -eq 24) -or (($flights -eq 0) -and ($queued -eq 0))) { return 'NOT_STARTED' }
    }
    'COLLECTION_STOPPED'
  }
  function Write-PartialState([string]$dir) {
    $plines = Read-Utf8 (Join-Path $dir 'collector_stdout.log')
    $psum = Read-Summary $plines
    $pending = @(Get-ChildItem -LiteralPath ([System.IO.Path]::Combine($dir, 'outbox', 'pending')) -File -ErrorAction SilentlyContinue).Count
    $sent = @(Get-ChildItem -LiteralPath ([System.IO.Path]::Combine($dir, 'outbox', 'sent')) -File -ErrorAction SilentlyContinue).Count
    $e = Join-Path $dir 'collector_exit.txt'
    $pexit = if (Test-Path -LiteralPath $e) { ([string](Get-Content -LiteralPath $e -Raw)).Trim() } else { 'none (the window was closed or the block was stopped)' }
    $visited = Get-VisitedIds $plines
    # [REASON]: a continuation skips a flight only when its card, route and descriptor and its V4
    # (or a confirmed absence of V4) are queued (sources.flight_already_captured); the rest are
    # visited at DJI again, among them the flights that made this run stop.
    $complete = @($plines | ForEach-Object { if ($_ -match ': Flight (\d+): (V4|NO_V4_URL) \((.*)\)\s*$') { $it = @($Matches[3] -split ',\s*'); if (@(@('card', 'route', 'airlines') | Where-Object { $it -notcontains $_ }).Count -eq 0) { [int64]$Matches[1] } } } | Select-Object -Unique | Where-Object { -not $canarySet.Contains($_) })
    Write-Output ("PARTIAL_W2_STATE visited=" + $visited.Count + " of " + $remainingCount + " complete=" + $complete.Count + " visited_again_by_a_continuation=" + ($remainingCount - $complete.Count) + " collector_exit=" + $pexit + " run_id=" + $(if ($psum['snapshot_run_id']) { $psum['snapshot_run_id'] } else { 'none (no RUN SUMMARY)' }) + " outbox_pending=" + $pending + " outbox_sent=" + $sent)
    $live = @(Get-CimInstance -ClassName Win32_Process | Where-Object { [string]$_.CommandLine -like ('*' + (Join-Path $dir 'remaining_450_ids.txt') + '*') })
    foreach ($q in $live) { Write-Output ("COLLECTOR_STILL_RUNNING pid=" + $q.ProcessId + " -- the collector of this run is working without supervision; stop it now: taskkill /PID " + $q.ProcessId + " /T /F") }
    Write-Output 'PARTIAL_W2_NEXT=nothing is collected again by this block. A continuation is a separate step after the owner decides: it reuses this run folder and outbox, skips the complete flights, visits the others again and then sends what is pending; send this output.'
  }
  function Get-VisitedIds([string[]]$lines) { @($lines | ForEach-Object { if ($_ -match ': Flight (\d+): ((V4|NO_V4_URL|NO_V4|V4_FAILED) \(|the record page did not open)') { [int64]$Matches[1] } } | Select-Object -Unique) }
  function Start-Child([string]$exe, [string]$arguments, [string]$cwd) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $exe
    $psi.Arguments = $arguments
    $psi.WorkingDirectory = $cwd
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $psi.StandardErrorEncoding = New-Object System.Text.UTF8Encoding($false)
    # The child alone gets the pilot environment; this console and the machine keep theirs.
    foreach ($k in $childDrop) { if ($psi.EnvironmentVariables.ContainsKey($k)) { $psi.EnvironmentVariables.Remove($k) } }
    foreach ($k in $childEnv.Keys) { $psi.EnvironmentVariables[$k] = [string]$childEnv[$k] }
    [System.Diagnostics.Process]::Start($psi)
  }
  function Stop-Child($p) {
    if ($p.HasExited) { return }
    # Only this process and its children (browser, driver); no other process is touched.
    try { & taskkill.exe /PID $p.Id /T /F 2>&1 | Out-Null } catch { }
    if (-not $p.HasExited) { try { $p.Kill($true) } catch { try { $p.Kill() } catch { } } }
    [void]$p.WaitForExit(30000)
  }
  function Get-LockOwner {
    $h = $lock + '.owner'
    if (-not (Test-Path -LiteralPath $h)) { return 'none' }
    try { $o = Get-Content -LiteralPath $h -Raw | ConvertFrom-Json } catch { return 'unreadable' }
    if (Get-Process -Id ([int]$o.pid) -ErrorAction SilentlyContinue) { return ('held by running pid ' + $o.pid + ' purpose ' + $o.purpose) }
    'stale (pid ' + $o.pid + ' is not running; the lock itself is released by the OS)'
  }
  function Test-Production([string]$label) {
    $h = [string](git -C $prodRoot rev-parse HEAD)
    $s = Get-ProdServices
    Write-Output ("PROD_" + $label + " HEAD=" + $h + " " + $s)
    if (($h -ne $prodExpected) -or ($s -ne $prodWant)) { throw "STEP FAILED: production is not as expected ($label) -- send this output" }
    # [REASON]: the production collector shares the session and the lock with the pilot; its DJI
    # code must be the one the pilot was checked against (8df5683 = the pin for these paths).
    & git -C $prodRoot diff --quiet $prodBase HEAD -- @prodDji
    $committed = $LASTEXITCODE
    $edited = @(git -C $prodRoot --no-optional-locks status --porcelain --untracked-files=no -- @prodDji)
    if (($committed -ne 0) -or ($edited.Count -ne 0)) { throw "STEP FAILED: the production DJI code (drone_collector, dji_area, drones.py) differs from $prodBase (diff exit $committed, $($edited.Count) local change(s)) -- a new compatibility check is needed" }
    Write-Output ("PROD_DJI_CODE_" + $label + "=unchanged since " + $prodBase)
  }
  function Test-Collision([string]$label) {
    foreach ($n in $prodTasks) {
      $t = @(Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue)
      if ($t.Count -ne 1) { throw "STEP FAILED: $($t.Count) scheduled tasks named $n, expected one -- send this output" }
      $i = Get-ScheduledTaskInfo -TaskName $t[0].TaskName -TaskPath $t[0].TaskPath
      $next = $null
      if ($i.NextRunTime) { $next = [datetime]$i.NextRunTime; if ($next.Year -lt 2001) { $next = $null } }
      $gap = if ($next) { [math]::Floor(($next - (Get-Date)).TotalMinutes) } else { $null }
      Write-Output ("PROD_TASK_" + $label + " " + $n + " state=" + $t[0].State + " next=" + $(if ($next) { $next.ToString('yyyy-MM-dd HH:mm') + ' (in ' + $gap + ' min)' } else { 'none' }))
      if ([string]$t[0].State -eq 'Running') { throw "STEP FAILED: production task $n is running now -- run this block again after it finishes" }
      if (($null -ne $gap) -and ($gap -lt $minGapMin)) { throw "STEP FAILED: production task $n starts in $gap min; the collection needs a window of $minGapMin min -- run this block after that run" }
    }
    $procs = @(Get-CimInstance -ClassName Win32_Process | Where-Object { [string]$_.CommandLine -match $collectorRx })
    Write-Output ("COLLECTOR_PROCESSES_" + $label + "=" + $procs.Count)
    if ($procs.Count -gt 0) { throw "STEP FAILED: $($procs.Count) collector or cycle process(es) are running (pid $(($procs | ForEach-Object { $_.ProcessId }) -join ',')) -- run this block again after they finish" }
    $owner = Get-LockOwner
    Write-Output ("PROD_LOCK_OWNER_" + $label + "=" + $owner)
    if (($owner -ne 'none') -and ($owner -notlike 'stale*')) { throw "STEP FAILED: the production collector lock is $owner -- run this block again after it finishes" }
  }
  function Test-Staging([string]$label) {
    $h = [string](git -C $root rev-parse HEAD)
    $dirty = @(git -C $root --no-optional-locks status --porcelain --untracked-files=no)
    $svc = Get-Service -Name $service
    Write-Output ("STAGING_" + $label + " HEAD=" + $h + " tracked_changes=" + $dirty.Count + " " + $service + "=" + $svc.Status)
    if (($h -ne $pin) -or ($dirty.Count -ne 0)) { throw "STEP FAILED: staging is not on the clean pilot revision ($label) -- send this output" }
    if ([string]$svc.Status -ne 'Running') { throw "STEP FAILED: $service is $($svc.Status) ($label)" }
    foreach ($name in $bots) {
      $b = Get-Service -Name $name
      Write-Output ("STAGING_BOT_" + $label + " " + $name + " " + $b.Status + " " + $b.StartType)
      if (([string]$b.Status -ne 'Stopped') -or ([string]$b.StartType -ne 'Disabled')) { throw "STEP FAILED: $name is $($b.Status) $($b.StartType), B1 left it Stopped Disabled ($label)" }
    }
    $t = @(Get-ScheduledTask -TaskName $isoTask -ErrorAction SilentlyContinue)
    if (($t.Count -ne 1) -or ([string]$t[0].Settings.Enabled -ne 'False') -or ([string]$t[0].State -ne 'Disabled')) { throw "STEP FAILED: $isoTask is not Disabled as B1 left it ($label) -- send this output" }
    $extra = @((Get-ItemProperty -LiteralPath $siteParams).AppEnvironmentExtra)
    if (@($extra -match '^\s*DJI_REFRESH_LAUNCHER=').Count -ne 0) { throw "STEP FAILED: DJI_REFRESH_LAUNCHER is back in the staging site environment ($label)" }
    Write-Output ("STAGING_BARRIERS_" + $label + "=" + $isoTask + " Disabled, DJI_REFRESH_LAUNCHER absent")
    $login = Invoke-WebRequest -Uri ($site + '/login') -UseBasicParsing -TimeoutSec 30
    if (([int]$login.StatusCode -ne 200) -or ([string]$login.Content -notmatch 'vs-login-form')) { throw "STEP FAILED: the staging login page did not answer 200 with the form ($label)" }
    Write-Output ("STAGING_LOGIN_" + $label + "=200")
  }
  function Get-Registered([string]$out) {
    $ErrorActionPreference = 'Continue'
    & $python tools\check_migration_drift.py --db $db > $out 2>&1
    @(Select-String -LiteralPath $out -Pattern '^registered migrations: (\d+);' | ForEach-Object { $_.Matches[0].Groups[1].Value })
  }
  function Write-Fingerprint([string]$out) {
    & $python tools\dji_card_coverage_pilot.py fingerprint --db $db --out $out | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: fingerprint of the staging database (exit $LASTEXITCODE)" }
  }
  New-Item -ItemType Directory -Force -Path $work | Out-Null
  $log = Join-Path $work ('card_pilot_w2_' + $stamp + '.log')
  try { Start-Transcript -Path $log -Append | Out-Null } catch { Write-Output 'NOTE: the log file could not be started' }
  $failure = $null
  $collected = $false
  $stoppedSite = $false
  $measure = $null
  $statsAll = $null
  $gate = $null
  $w2 = $null
  $mode = 'fresh'
  $wall = $null
  try {
    Write-Output '== 1. Checks before DJI -- nothing is changed and nothing is sent'
    if ((hostname) -ne $expectedHost) { throw "STEP FAILED: host is $(hostname), expected $expectedHost" }
    $manifestFile = Join-Path $planDir 'pilot_manifest.csv'
    $pilotFile = Join-Path $planDir 'pilot_ids.txt'
    $canaryFile = Join-Path $planDir 'canary_ids.txt'
    $fpB0 = Join-Path $planDir 'fingerprint_before.json'
    foreach ($p in @($src, $cpy, $python, $db, $snapshot, $manifestFile, $pilotFile, $canaryFile, (Join-Path $planDir 'plan.json'), $fpB0, $session, $w1Log, (Join-Path $w1Run 'collector_stdout.log'), (Join-Path $w1Run 'collector_exit.txt'), (Join-Path $w1Run 'fingerprint_pre.json'), (Join-Path $w1Run 'fingerprint_post.json'), (Join-Path $w1Run 'measure\measure_canary.json'))) {
      if (-not (Test-Path -LiteralPath $p)) { throw "STEP FAILED: not found: $p" }
    }

    # The frozen sample: 500 = the 50 of W1 + the 450 of W2, nothing replaced, nothing added.
    $plan = Read-Json (Join-Path $planDir 'plan.json')
    foreach ($f in @(@($manifestFile, $manifestSha, [string]$plan.sample.manifest_sha256), @($pilotFile, $pilotSha, [string]$plan.sample.pilot_ids_sha256), @($canaryFile, $canarySha, [string]$plan.sample.canary_ids_sha256))) {
      $h = Get-Sha $f[0]
      if (($h -ne $f[1]) -or ($f[2] -ne $f[1])) { throw "STEP FAILED: $($f[0]) has sha256 $h, frozen $($f[1]) (plan.json $($f[2]))" }
    }
    $pilotIds = Read-Ids $pilotFile
    $canaryIds = Read-Ids $canaryFile
    $remainingCount = $pilotCount - $canaryCount
    if (([int]$plan.sample.size -ne $pilotCount) -or ([int]$plan.sample.canary -ne $canaryCount)) { throw "STEP FAILED: plan.json says $($plan.sample.size) / $($plan.sample.canary), expected $pilotCount / $canaryCount" }
    if (($pilotIds.Count -ne $pilotCount) -or (@($pilotIds | Select-Object -Unique).Count -ne $pilotCount)) { throw "STEP FAILED: pilot_ids.txt holds $($pilotIds.Count) ids, expected $pilotCount unique" }
    if (($canaryIds.Count -ne $canaryCount) -or (@($canaryIds | Select-Object -Unique).Count -ne $canaryCount)) { throw "STEP FAILED: canary_ids.txt holds $($canaryIds.Count) ids, expected $canaryCount unique" }
    $manifest = @(Import-Csv -LiteralPath $manifestFile)
    $manifestIds = @($manifest | ForEach-Object { [int64]$_.flight_id })
    $manifestCanary = @($manifest | Where-Object { $_.canary -eq '1' } | ForEach-Object { [int64]$_.flight_id })
    if ((($manifestIds -join ',') -ne ($pilotIds -join ',')) -or (($manifestCanary -join ',') -ne ($canaryIds -join ',')) -or (($pilotIds[0..($canaryCount - 1)] -join ',') -ne ($canaryIds -join ','))) { throw 'STEP FAILED: pilot_ids.txt, canary_ids.txt and pilot_manifest.csv do not agree (order, canary flags, canary = first of the manifest)' }
    $canarySet = New-Object 'System.Collections.Generic.HashSet[int64]'
    foreach ($i in $canaryIds) { [void]$canarySet.Add($i) }
    $pilotSet = New-Object 'System.Collections.Generic.HashSet[int64]'
    foreach ($i in $pilotIds) { [void]$pilotSet.Add($i) }
    if (@($canaryIds | Where-Object { -not $pilotSet.Contains($_) }).Count -ne 0) { throw 'STEP FAILED: a canary id is not in the frozen pilot' }
    $remaining = @($pilotIds | Where-Object { -not $canarySet.Contains($_) })
    $union = New-Object 'System.Collections.Generic.HashSet[int64]'
    foreach ($i in @($remaining + $canaryIds)) { [void]$union.Add($i) }
    if (($remaining.Count -ne $remainingCount) -or (@($remaining | Where-Object { $canarySet.Contains($_) }).Count -ne 0) -or (-not $union.SetEquals($pilotSet))) { throw "STEP FAILED: the frozen pilot minus the canary gives $($remaining.Count) ids, expected $remainingCount disjoint from the canary" }
    # [REASON]: the order of the frozen manifest is kept; the collector sorts by id anyway.
    $remainingText = "# DRONE-CARD-COVERAGE-001 -- W2: the frozen pilot minus the W1 canary ($remainingCount), manifest order`n# one DJI flight id per line; read by drone_collector --ids-file`n" + (($remaining | ForEach-Object { [string]$_ }) -join "`n") + "`n"
    $remainingSha = -join ([System.Security.Cryptography.SHA256]::Create().ComputeHash([System.Text.Encoding]::ASCII.GetBytes($remainingText)) | ForEach-Object { $_.ToString('x2') })
    Write-Output ("W1_ALREADY_DONE=" + $canaryIds.Count + " W2_REMAINING=" + $remaining.Count + " TOTAL_MANIFEST=" + $pilotIds.Count + " (W1 and W2 disjoint, together the frozen " + $pilotCount + ")")
    Write-Output ("PILOT_IDS_SHA256=" + $pilotSha + " CANARY_SHA256=" + $canarySha + " REMAINING_IDS_SHA256=" + $remainingSha)

    # [REASON]: the collector sends only after its whole walk and every run has its own queue, so a
    # stopped run leaves staging as it was; pasting again would visit DJI again. Only a run that
    # passed the collector gate may be continued here, and only by S2 (no DJI).
    $runs = @(Get-ChildItem -LiteralPath $w2Root -Directory -ErrorAction SilentlyContinue | Sort-Object Name | ForEach-Object { [pscustomobject]@{ Dir = $_.FullName; State = (Get-RunState $_.FullName) } })
    $done = @($runs | Where-Object { $_.State -eq 'COMPLETE' })
    $partial = @($runs | Where-Object { $_.State -eq 'COLLECTION_STOPPED' })
    $pendingRuns = @($runs | Where-Object { @('GATE_PENDING', 'S2_PENDING') -contains $_.State })
    if ($done.Count -gt 0) { $w2 = $done[-1].Dir; throw "STEP FAILED: W2+S2 was already completed in $w2 -- nothing is done again; send that run's output" }
    if ($partial.Count -gt 0) { $w2 = $partial[-1].Dir; throw "STEP FAILED: an earlier W2 run started the collector and its collection did not end by itself ($w2) -- no new collection without the owner's decision" }
    if ($pendingRuns.Count -gt 1) { throw "STEP FAILED: $($pendingRuns.Count) W2 runs collected without S2 -- send this output" }
    if ($pendingRuns.Count -eq 1) {
      $w2 = $pendingRuns[0].Dir
      if ($pendingRuns[0].State -eq 'S2_PENDING') {
        $mode = 'resume'
        Write-Output ("MODE=resume S2 of " + $w2 + " (its collection passed the gate; DJI is not visited again)")
      } else {
        $mode = 'gate'
        Write-Output ("MODE=gate of " + $w2 + " (its collector ended by itself; the gate is checked again from its files; DJI is not visited again)")
      }
    } else {
      $w2 = Join-Path $w2Root $stamp
      Write-Output ("MODE=fresh W2 run " + $w2)
    }
    $idsFile = Join-Path $w2 'remaining_450_ids.txt'
    $outbox = Join-Path $w2 'outbox'
    $collectLog = Join-Path $w2 'collector_stdout.log'

    $srcHead = [string](git -C $src rev-parse HEAD)
    $srcDirty = @(git -C $src --no-optional-locks status --porcelain --untracked-files=no)
    if (($srcHead -ne $pin) -or ($srcDirty.Count -ne 0)) { throw "STEP FAILED: the pilot checkout $src is at $srcHead with $($srcDirty.Count) tracked change(s); expected the clean pin $pin" }
    $pkg = [System.IO.Path]::Combine($src, 'drone_collector')
    $mainText = [System.IO.File]::ReadAllText([System.IO.Path]::Combine($pkg, 'main.py'))
    foreach ($w in @("'--sources'", "'--ids-file'", "'--send-sources'", 'DJI_COLLECTOR_LOCK_PATH', 'DJI_COLLECTOR_LOCK_WAIT_S')) { if (-not $mainText.Contains($w)) { throw "STEP FAILED: the pilot collector does not know $w" } }
    if (-not ([System.IO.File]::ReadAllText([System.IO.Path]::Combine($pkg, 'config.py'))).Contains('DRONE_OUTBOX_DIR')) { throw 'STEP FAILED: the pilot collector does not know DRONE_OUTBOX_DIR' }
    if (Test-Path -LiteralPath ([System.IO.Path]::Combine($pkg, '.env'))) { throw 'STEP FAILED: the pilot checkout has a drone_collector\.env; the child environment must be the only source of settings' }
    Write-Output ("COLLECTOR_CODE=" + $src + " HEAD=" + $srcHead + " clean")
    if ($site -match ':5050') { throw "STEP FAILED: the receiver $site is the production port 5050 -- refused" }
    if ($site -notmatch ':5051$') { throw "STEP FAILED: the receiver $site is not the staging port 5051" }

    if ($mode -eq 'fresh') {
      # [REASON]: staging accepts only its own token (W1, 08.10.2026: the machine token got 401).
      # It is read from the staging service environment, used once in the child, never shown.
      $tokenLines = @(@((Get-ItemProperty -LiteralPath $siteParams).AppEnvironmentExtra) | Where-Object { [string]$_ -match '^\s*DRONE_API_TOKEN=' })
      if ($tokenLines.Count -ne 1) { throw "STEP FAILED: the staging service environment holds $($tokenLines.Count) DRONE_API_TOKEN entries, expected exactly one" }
      $token = ([string]$tokenLines[0] -replace '^\s*DRONE_API_TOKEN=', '').Trim()
      $tokenLines = $null
      if (-not $token) { throw 'STEP FAILED: the DRONE_API_TOKEN of the staging service is empty' }
      $childEnv = [ordered]@{ VEHICLE_SOFT_BASE_URL = $site; DJI_STORAGE_STATE = $session; DJI_COLLECTOR_LOCK_PATH = $lock; DJI_COLLECTOR_LOCK_WAIT_S = '0'; DRONE_OUTBOX_DIR = $outbox; DJI_HEADLESS = 'true'; DRONE_API_TOKEN = $token; PYTHONIOENCODING = 'utf-8' }
      $childDrop = @('PYTHONPATH', 'PYTHONHOME', 'PYTHONSAFEPATH', 'PYTHONSTARTUP')
      $inherited = @(Get-ChildItem Env: | Where-Object { ($_.Name -match '^(DJI_|DRONE_|VEHICLE_SOFT_|PLAYWRIGHT_|HTTPS?_PROXY$|NO_PROXY$)') -and (-not $childEnv.Contains($_.Name)) } | ForEach-Object { $_.Name })
      Write-Output ("CHILD_ENV_SET=" + (@($childEnv.Keys) -join ',') + " (DRONE_API_TOKEN of the staging service, value not shown)")
      Write-Output ("CHILD_ENV_DROPPED=" + ($childDrop -join ',') + " CHILD_ENV_INHERITED=" + $(if ($inherited.Count) { $inherited -join ',' } else { 'none' }) + " (names only)")
      # [REASON]: python itself decides whether its import folders are the pilot folder
      # (samefile): 8.3 short names and letter case make a text comparison unreliable.
      $p = Start-Child $cpy ("-B -c `"import os, sys, importlib.util as u, drone_collector.config as c, drone_collector.main as m, drone_collector.sources as s; d = [str(c.PACKAGE_ROOT), os.path.dirname(os.path.abspath(m.__file__)), os.path.dirname(os.path.abspath(s.__file__))]; print(d[0]); print(os.path.abspath(m.__file__)); print(u.find_spec('playwright').origin); print(all(os.path.samefile(x, sys.argv[1]) for x in d))`" `"" + $pkg + "`"") $src
      $probeErr = $p.StandardError.ReadToEndAsync()
      $probe = @($p.StandardOutput.ReadToEnd() -split "`r?`n" | Where-Object { $_ })
      $p.WaitForExit()
      if (($p.ExitCode -ne 0) -or ($probe.Count -ne 4)) { throw "STEP FAILED: the collector python could not import the pilot collector and Playwright (exit $($p.ExitCode)) -- $($probeErr.Result)" }
      if ($probe[3] -ne 'True') { throw "STEP FAILED: the collector python imports drone_collector from $($probe[0]), not from $pkg" }
      Write-Output ("PYTHON=" + $cpy)
      Write-Output ("IMPORTS drone_collector=" + $probe[0] + " main=" + $probe[1] + " playwright=" + $probe[2])
      $sessionBefore = Get-FileState $session
      Write-Output ("SESSION=" + $session + " sha256=" + $sessionBefore + " (read only; --save-session is never passed)")
      Write-Output ("LOCK=" + $lock + " (shared with the production collector; wait 0 s)")
      Write-Output ("RECEIVER=" + $site + "/drones/api/source_sync")
      Write-Output ("PILOT_OUTBOX=" + $outbox)
      Write-Output ("PILOT_LOG=" + $collectLog)
    }

    Test-Production 'BEFORE'
    if ($mode -eq 'fresh') { Test-Collision 'BEFORE' }
    if (($mode -eq 'resume') -and ([string](Get-Service -Name $service).Status -eq 'Stopped') -and (Test-Path -LiteralPath (Join-Path $w2 's2_started.txt'))) {
      # [REASON]: S2 of this very run stopped the site and was cut off before starting it again;
      # it is started as S2 would have, because every check below needs it as B1 left it.
      Start-Service -Name $service
      (Get-Service -Name $service).WaitForStatus('Running', (New-TimeSpan -Seconds 90))
      Start-Sleep -Seconds 8
      Write-Output 'STAGING_SITE_STARTED_AGAIN=the interrupted S2 of this run had left it stopped'
    }
    Test-Staging 'BEFORE'
    $open = @(Get-ChildItem -LiteralPath $runRoot -Directory -Filter 'staging_*' | Where-Object { -not (Test-Path -LiteralPath (Join-Path $_.FullName 'returned.txt')) })
    if (($open.Count -ne 1) -or (-not (Test-Path -LiteralPath (Join-Path $open[0].FullName 'swapped.txt')))) { throw "STEP FAILED: expected exactly one open B1 run with swapped.txt under $runRoot, found $($open.Count) -- send this output" }
    Write-Output ("B1_RUN=" + $open[0].FullName + " (open; block R returns staging after the pilot)")
    if ($mode -eq 'fresh') {
      $body = @{ token = $token; content_md5 = @() } | ConvertTo-Json -Compress
      try { $pre = Invoke-WebRequest -Uri ($site + '/drones/api/land_geometry_manifest') -Method Post -Body $body -ContentType 'application/json' -UseBasicParsing -TimeoutSec 30 -MaximumRedirection 0 } catch { throw "STEP FAILED: staging refused the DRONE_API_TOKEN of its own service on a read-only call ($($_.Exception.Message)) -- nothing was collected" }
      if (([int]$pre.StatusCode -ne 200) -or ([string]$pre.Content -notmatch '"asked"\s*:\s*0')) { throw "STEP FAILED: staging answered the read-only token check with $($pre.StatusCode)" }
      Write-Output 'TOKEN_CHECK=staging accepts the DRONE_API_TOKEN of its own service (read-only land_geometry_manifest, nothing written)'
      New-Item -ItemType Directory -Force -Path $w2 | Out-Null
      if (Test-Path -LiteralPath $outbox) { throw "STEP FAILED: the pilot outbox $outbox already exists" }
      [System.IO.File]::WriteAllText($idsFile, $remainingText, [System.Text.Encoding]::ASCII)
    }
    Set-Content -LiteralPath (Join-Path $w2 'w2_check.py') -Value $helperText -Encoding ASCII
    if ((Get-Sha $idsFile) -ne $remainingSha) { throw "STEP FAILED: $idsFile is not the frozen pilot minus the canary" }
    $canaryCopy = Join-Path $w2 'canary_ids.txt'
    Copy-Item -LiteralPath $canaryFile -Destination $canaryCopy -Force
    $pilotCopy = Join-Path $w2 'pilot_ids.txt'
    Copy-Item -LiteralPath $pilotFile -Destination $pilotCopy -Force
    if (((Get-Sha $canaryCopy) -ne $canarySha) -or ((Get-Sha $pilotCopy) -ne $pilotSha)) { throw 'STEP FAILED: the copies of the frozen id files differ from the frozen files' }
    Set-Location -LiteralPath $root
    $reg = @(Get-Registered (Join-Path $w2 ('drift_before_' + $stamp + '.log')))
    if (($reg.Count -ne 1) -or ($reg[0] -ne '60')) { throw "STEP FAILED: the staging database reports $($reg -join ',') registered migrations, expected 60" }
    Write-Output 'REGISTERED=60'
    $stNow = Invoke-Helper @('snapshot', $db)
    $b0 = Invoke-Helper @('snapshot', $snapshot)
    if ($stNow['CATALOG_SHA256'] -ne $b0['CATALOG_SHA256']) { throw 'STEP FAILED: the staging field catalog differs from the B0 copy' }
    Write-Output ("STAGING_COUNTERS sources_max_id=" + $stNow['SOURCE_MAX_ID'] + " v4_summaries=" + $stNow['V4SUM_ROWS'] + " catalog=equals B0")

    if ($mode -eq 'fresh') {
      Write-Output '-- W1 evidence (the canary of 08.10.2026)'
      $w1Main = [System.IO.File]::ReadAllText($w1Log)
      if (($w1Main -notmatch '(?m)^STEP=PASS\s*$') -or ($w1Main -notmatch '(?m)^DECISION=GO_TO_500\s*$')) { throw "STEP FAILED: the W1 log $w1Log does not end W1 with STEP=PASS and DECISION=GO_TO_500" }
      $w1Lines = Read-Utf8 (Join-Path $w1Run 'collector_stdout.log')
      $w1Sum = Read-Summary $w1Lines
      $w1Exit = ([string](Get-Content -LiteralPath (Join-Path $w1Run 'collector_exit.txt') -Raw)).Trim()
      Write-Output ("W1_RUN_SUMMARY exit=" + $w1Exit + " run_id=" + $w1Sum['snapshot_run_id'] + " requested=" + $w1Sum['sources_requested'] + " visited=" + $w1Sum['sources_visited'] + " card=" + $w1Sum['sources_card'] + " new=" + $w1Sum['sources_new'] + " ingest_errors=" + $w1Sum['sources_ingest_errors'] + " rejected=" + $w1Sum['sources_rejected'])
      if (($w1Sum['snapshot_run_id'] -ne $w1RunId) -or ($w1Sum['sources_requested'] -ne [string]$canaryCount) -or ($w1Sum['sources_visited'] -ne [string]$canaryCount) -or ($w1Sum['sources_card'] -ne [string]$canaryCount) -or ($w1Sum['sources_new'] -ne [string]$w1New) -or ($w1Sum['sources_ingest_errors'] -ne '0') -or ($w1Sum['sources_batch_accepted'] -ne 'true') -or (@('0', '18') -notcontains $w1Exit)) { throw 'STEP FAILED: the W1 RUN SUMMARY is not the verified one (run id, 50/50 cards, new sources, no ingest error)' }
      if (-not (Test-SameFile (Join-Path $w1Run 'fingerprint_pre.json') $fpB0)) { throw 'STEP FAILED: W1 did not start from the B0 staging fingerprint' }
      if ((Get-Sha (Join-Path $w1Run 'canary_ids.txt')) -ne $canarySha) { throw 'STEP FAILED: the W1 run folder does not hold the frozen canary list' }
      $m1 = Read-Json (Join-Path $w1Run 'measure\measure_canary.json')
      $o1 = $m1.outcomes_among_fetched
      $w1Want = "fetched=$canaryCount confirmed=$w1Confirmed EXACT=$w1Exact IDENTIFIED=$w1Identified NO_KEY=$w1NoKey NOT_IN_CATALOG=$w1NotInCatalog"
      $w1Got = "fetched=$($m1.fetched) confirmed=$($m1.confirmed) EXACT=$([int]$o1.EXACT) IDENTIFIED=$([int]$o1.IDENTIFIED) NO_KEY=$([int]$o1.NO_KEY) NOT_IN_CATALOG=$([int]$o1.NOT_IN_CATALOG)"
      Write-Output ("W1_MEASURE " + $w1Got)
      if ($w1Got -ne $w1Want) { throw "STEP FAILED: the W1 measurement is not the verified one ($w1Want)" }
      $rr = Invoke-Helper @('runrows', $db, $canaryCopy, $w1RunId)
      Write-Output ("W1_ON_STAGING revisions=" + $rr['RUN_ROWS'] + " flights=" + $rr['RUN_FLIGHTS'] + " outside_canary=" + $rr['RUN_OUTSIDE_IDS'] + " canary_with_card=" + $rr['IDS_WITH_CARD'] + " canary_card_from_w1=" + $rr['IDS_CARD_FROM_RUN'])
      if (($rr['RUN_ROWS'] -ne [string]$w1New) -or ($rr['RUN_OUTSIDE_IDS'] -ne '0') -or ($rr['IDS_WITH_CARD'] -ne [string]$canaryCount) -or ($rr['IDS_CARD_FROM_RUN'] -ne [string]$canaryCount)) { throw 'STEP FAILED: staging does not hold the W1 evidence as verified (its sources, the 50 cards)' }
      $fpPre = Join-Path $w2 'fingerprint_pre.json'
      Write-Fingerprint $fpPre
      if (-not (Test-SameFile $fpPre (Join-Path $w1Run 'fingerprint_post.json'))) { throw 'STEP FAILED: staging changed after W1 (its fingerprint differs from W1 fingerprint_post.json) -- nothing is collected; send this output' }
      Write-Output 'FINGERPRINT_PRE=equals W1 fingerprint_post.json (nothing changed on staging since W1)'
      $recheck = @(& $python tools\dji_card_coverage_pilot.py measure --db $db --plan-dir $planDir --stage canary --before $fpB0 --out-dir (Join-Path $w2 'w1_recheck'))
      $recheckCode = $LASTEXITCODE
      $rcm = Read-Json ([System.IO.Path]::Combine($w2, 'w1_recheck', 'measure_canary.json'))
      $o2 = $rcm.outcomes_among_fetched
      $w1Now = "fetched=$($rcm.fetched) confirmed=$($rcm.confirmed) EXACT=$([int]$o2.EXACT) IDENTIFIED=$([int]$o2.IDENTIFIED) NO_KEY=$([int]$o2.NO_KEY) NOT_IN_CATALOG=$([int]$o2.NOT_IN_CATALOG)"
      Write-Output ("W1_RECHECK_NOW " + $w1Now + " gates_exit=" + $recheckCode)
      $recheck | Where-Object { $_ -match 'GATE ' } | ForEach-Object { Write-Output ('  ' + $_) }
      if (($recheckCode -ne 0) -or ($w1Now -ne $w1Want)) { throw 'STEP FAILED: the 50 W1 results or the B0 invariants (RAW, decisions, revisions, migrations, other flights) do not hold now' }
      $probeProd = Invoke-Helper @('newrows', $prodDb, $idsFile, '0', '0', (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ss'), '9999-12-31 23:59:59', ('w2-probe-' + $stamp))
      if (($probeProd['RUN_ID_ROWS'] -ne '0') -or ($probeProd['IDS_EVIDENCE_SINCE'] -ne '0')) { throw 'STEP FAILED: the read-only production check did not answer as expected' }
      Write-Output 'PROD_DB_READ=ok (mode=ro, counters only)'
      $sched = @(Get-ScheduledTask | Where-Object { ([string]$_.TaskPath -notlike '\Microsoft\*') -and ([string]$_.State -ne 'Disabled') -and ((@($_.Actions | ForEach-Object { [string]$_.Execute + ' ' + [string]$_.Arguments + ' ' + [string]$_.WorkingDirectory }) -join ' ') -match ('transport-report-staging|:5051|VehicleSoft_|' + $collectorRx)) -and ($prodTasks -notcontains $_.TaskName) -and ($_.TaskName -ne 'TransportDBBackupStaging') })
      if ($sched.Count -gt 0) { throw "STEP FAILED: enabled scheduled task(s) that may collect or write staging: $(($sched | ForEach-Object { $_.TaskName }) -join ', ')" }
    } elseif ($mode -eq 'resume') {
      $gate = Read-Json (Join-Path $w2 'collector_gate.json')
      if ([string]$gate.remaining_ids_sha256 -ne $remainingSha) { throw 'STEP FAILED: the W2 run that passed the collector gate was not the frozen pilot minus the canary' }
      $fpNow = Join-Path $w2 ('fingerprint_resume_' + $stamp + '.json')
      Write-Fingerprint $fpNow
      # [REASON]: the recalculation commits once, at its end, and repeats as "unchanged"; so S2 is
      # simply run again. Allowed since the gate: nothing, or the recalculation of the 450 alone.
      $dr = Invoke-Helper @('fpdiff', (Join-Path $w2 'fingerprint_after_collection.json'), $fpNow, $idsFile, $canaryCopy)
      Write-Output ("CHANGED_SINCE_GATE flights=" + $dr['CHANGED'] + " outside_w2=" + $dr['CHANGED_OUTSIDE_ALLOWED'] + " w1_canary=" + $dr['KEEP_CHANGED'] + " raw=" + $dr['SAME_RAW'] + " decisions=" + $dr['SAME_DECISIONS'] + " migrations=" + $dr['SAME_MIGRATIONS'] + " sources=" + $dr['SAME_SOURCES'] + " (1 = unchanged)")
      if (($dr['CHANGED_OUTSIDE_ALLOWED'] -ne '0') -or ($dr['KEEP_CHANGED'] -ne '0') -or ($dr['SAME_RAW'] -ne '1') -or ($dr['SAME_DECISIONS'] -ne '1') -or ($dr['SAME_MIGRATIONS'] -ne '1') -or ($dr['SAME_SOURCES'] -ne '1')) { throw 'STEP FAILED: staging changed after the W2 collection gate beyond the recalculation of the 450 -- S2 is not continued; send this output' }
      $wall = $gate.wall_seconds
    }

    if ($mode -eq 'fresh') {
      Write-Output '== 2. Live W2: the 450 flights W1 did not visit, staging receiver only, shared production lock'
      Test-Production 'LAUNCH'
      Test-Collision 'LAUNCH'
      $t0 = (Get-Date).ToUniversalTime().AddSeconds(-5).ToString('yyyy-MM-dd HH:mm:ss')
      Write-Output ("NOW=" + (Get-Date).ToString('yyyy-MM-dd HH:mm:ss') + " UTC=" + $t0 + " W1_ALREADY_DONE=" + $canaryIds.Count + " W2_REMAINING=" + $remaining.Count + " TOTAL_MANIFEST=" + $pilotIds.Count)
      $collected = $true
      $clock = [System.Diagnostics.Stopwatch]::StartNew()
      $proc = Start-Child $cpy ('-m drone_collector.main --sources --ids-file "' + $idsFile + '" --send-sources') $src
      $errTask = $proc.StandardError.ReadToEndAsync()
      Write-Output ("COLLECTOR_PID=" + $proc.Id + " -- keep this window open until STEP= is printed; if it was closed: taskkill /PID " + $proc.Id + " /T /F")
      $writer = New-Object System.IO.StreamWriter($collectLog, $false, (New-Object System.Text.UTF8Encoding($false)))
      $stopWhy = $null
      $configSeen = $false
      $noCard = 0
      $pageErr = 0
      $cardRefused = 0
      $ended = $false
      try {
        $next = $proc.StandardOutput.ReadLineAsync()
        while ($true) {
          # [REASON]: on every pass, not only when the collector is silent: a slow DJI that still
          # answers every few seconds must not run into the production window.
          if ($clock.Elapsed.TotalMinutes -gt $maxCollectMin) { $stopWhy = "the run passed the $maxCollectMin min limit"; break }
          if (-not $next.Wait(5000)) { continue }
          $line = $next.Result
          $next = $null
          if ($null -eq $line) { $ended = $true; break }
          $writer.WriteLine($line)
          $writer.Flush()
          if ($line -notmatch ': Flight \d+: captured ') { Write-Output ('  | ' + $line) }
          if ($line -match 'Configuration: (\{.*\})\s*$') {
            $configSeen = $true
            $cfg = $Matches[1] -replace '\\\\', '\'
            foreach ($want in @(("'source_sync_url': '" + $site + "/drones/api/source_sync'"), ("'outbox_dir': '" + $outbox + "'"), ("'storage_state': '" + $session + "'"), "'headless': True", "'api_token': 'set'")) {
              if (-not $cfg.Contains($want)) { $stopWhy = 'the collector configuration does not show ' + $want }
            }
          }
          if (($line -match ': Flight \d+: ') -and (-not $configSeen)) { $stopWhy = 'a flight was visited before the configuration line was seen' }
          # [REASON]: a byte count such as "(429 bytes)" is not a DJI answer; "HTTP 429" still is. Nor
          # is a RUN SUMMARY counter (sources_v4=429): collector-stats skips that line as well.
          $scan = $line -replace '\(\d+ bytes\)', '(N bytes)'
          if ($scan -notmatch 'RUN SUMMARY ') { foreach ($m in $stopMarkers) { if ($scan -match $m[1]) { $stopWhy = 'stop marker ' + $m[0] + ' in the collector log' } } }
          if ($line -match ': Flight (\d+): ') {
            if ($canarySet.Contains([int64]$Matches[1])) { $stopWhy = 'the collector visited a W1 canary flight' }
          }
          if ($line -match ': Flight \d+: (V4|NO_V4_URL|NO_V4|V4_FAILED) \((.*)\)\s*$') {
            $parts = @($Matches[2] -split ',\s*')
            $pageErr = 0
            if ($parts -contains 'card') { $noCard = 0 } else {
              $noCard++
              # [REASON]: the collector logs no line for a refused card request. A flight whose
              # route, descriptor or V4 came but whose card did not is the only sign of it.
              if (@($parts | Where-Object { @('route', 'airlines', 'v4') -contains $_ }).Count -gt 0) { $cardRefused++ }
            }
          } elseif ($line -match ': Flight \d+: the record page did not open') { $noCard++; $pageErr++ }
          if ($pageErr -ge 3) { $stopWhy = 'three record pages in a row did not open' }
          if ($cardRefused -ge $maxCardRefused) { $stopWhy = "$maxCardRefused flights came without a card while their other parts came (refused card requests)" }
          if ($noCard -ge $maxNoCardRun) { $stopWhy = "$maxNoCardRun flights in a row came without a card" }
          if ($stopWhy) { break }
          $next = $proc.StandardOutput.ReadLineAsync()
        }
      } finally {
        # [REASON]: a stop rule, Ctrl+C or an error here: the collector must not go on unwatched.
        if (-not $ended) { Stop-Child $proc }
        if (-not $proc.WaitForExit(120000)) { Stop-Child $proc; if (-not $stopWhy) { $stopWhy = 'the collector did not end after its output closed' } }
        if (-not $ended) {
          # What the collector printed before it was stopped belongs in the run log (visited flights).
          try {
            if ($null -eq $next) { $next = $proc.StandardOutput.ReadLineAsync() }
            while ($next.Wait(10000) -and ($null -ne $next.Result)) { $writer.WriteLine($next.Result); $next = $proc.StandardOutput.ReadLineAsync() }
          } catch { }
        }
        $writer.Close()
        $errText = if ($errTask.Wait(30000)) { $errTask.Result } else { 'stderr still open after 30 s: a process of the collector tree is alive' }
        Set-Content -LiteralPath (Join-Path $w2 'collector_stderr.log') -Value $errText -Encoding UTF8
      }
      $clock.Stop()
      $code = $proc.ExitCode
      Set-Content -LiteralPath (Join-Path $w2 'collector_exit.txt') -Value ([string]$code) -Encoding ASCII
      $wall = [math]::Round($clock.Elapsed.TotalSeconds)
      Write-Output ("COLLECTOR_EXIT=" + $code + " WALL_SECONDS=" + $wall + " CARD_REFUSED_FLIGHTS=" + $cardRefused + $(if ($stopWhy) { " STOPPED_BY_THIS_BLOCK=" + $stopWhy } else { '' }))
      $done = [ordered]@{ collector_exit = $code; stop_why = [string]$stopWhy; wall_seconds = $wall; card_refused = $cardRefused; t0_utc = $t0; t1_utc = (Get-Date).ToUniversalTime().AddSeconds(60).ToString('yyyy-MM-dd HH:mm:ss'); session_before = $sessionBefore; session_after = (Get-FileState $session); sources_max_id_before = [int]$stNow['SOURCE_MAX_ID']; v4sum_max_id_before = [int]$stNow['V4SUM_MAX_ID']; remaining_ids_sha256 = $remainingSha }
      # [REASON]: what the gate compares against goes to disk before the gate starts: a window closed
      # during the gate leaves a collection that a later paste checks again, without DJI.
      [System.IO.File]::WriteAllText((Join-Path $w2 'collector_done.json'), ($done | ConvertTo-Json), [System.Text.Encoding]::ASCII)
    }

    if (($mode -eq 'fresh') -or ($mode -eq 'gate')) {
      Write-Output '== 3. Collector gate'
      $done = Read-Json (Join-Path $w2 'collector_done.json')
      if ([string]$done.remaining_ids_sha256 -ne $remainingSha) { throw 'STEP FAILED: the collection of this run was not the frozen pilot minus the canary' }
      $code = [int]$done.collector_exit
      $stopWhy = [string]$done.stop_why
      $wall = $done.wall_seconds
      $t0 = [string]$done.t0_utc
      # [REASON]: production writes for these September flights only from the W2 launch to its end
      # count; a later gate check must not count what production legitimately stored since.
      $t1 = [string]$done.t1_utc
      $lines = Read-Utf8 $collectLog
      $sessionAfter = [string]$done.session_after
      Write-Output ("SESSION_AFTER=" + $(if ($sessionAfter -eq [string]$done.session_before) { 'unchanged' } else { 'CHANGED ' + $sessionAfter }))
      $ownerAfter = Get-LockOwner
      Write-Output ("PROD_LOCK_OWNER_AFTER=" + $ownerAfter)
      $left = @(Get-CimInstance -ClassName Win32_Process | Where-Object { [string]$_.CommandLine -like ('*' + $idsFile + '*') })
      Write-Output ("W2_PROCESSES_LEFT=" + $left.Count)
      $summary = Read-Summary @($lines)
      $runId = [string]$summary['snapshot_run_id']
      Write-Output ("RUN_SUMMARY run_id=" + $runId + " requested=" + $summary['sources_requested'] + " skipped_known=" + $summary['sources_skipped_known'] + " visited=" + $summary['sources_visited'] + " card=" + $summary['sources_card'] + " rejected=" + $summary['sources_rejected'] + " v4_failed=" + $summary['sources_v4_failed'] + " page_errors=" + $summary['sources_page_errors'] + " sent=" + $summary['sources_envelopes_sent'] + " accepted=" + $summary['sources_batch_accepted'] + " new=" + $summary['sources_new'] + " ingest_errors=" + $summary['sources_ingest_errors'])
      $inPilot = $false
      $inProd = $false
      if ($runId) {
        $ourLog = [System.IO.Path]::Combine($pkg, 'logs', 'collector.log')
        $inPilot = (Test-Path -LiteralPath $ourLog) -and [bool](Select-String -LiteralPath $ourLog -SimpleMatch -Pattern $runId -Quiet)
        $inProd = (Test-Path -LiteralPath $prodLog) -and [bool](Select-String -LiteralPath $prodLog -SimpleMatch -Pattern $runId -Quiet)
        Write-Output ("RUN_LOGGED_IN pilot_checkout=" + $inPilot + " production_checkout=" + $inProd)
      }
      $prodAfter = Invoke-Helper @('newrows', $prodDb, $idsFile, '0', '0', $t0, $t1, $(if ($runId) { $runId } else { 'w2-no-run-id' }))
      Write-Output ("PROD_DB_AFTER run_rows=" + $prodAfter['RUN_ID_ROWS'] + " w2_sources_since=" + $prodAfter['IDS_SOURCES_SINCE'] + " w2_evidence_since=" + $prodAfter['IDS_EVIDENCE_SINCE'] + " (UTC " + $t0 + " .. " + $t1 + ")")
      $revisited = @(Get-VisitedIds @($lines) | Where-Object { $canarySet.Contains($_) })
      Write-Output ("CANARY_REVISITED=" + $revisited.Count)
      if ($sessionAfter -ne [string]$done.session_before) { throw 'STEP FAILED: the production DJI session file changed during the run -- send this output' }
      if (($prodAfter['RUN_ID_ROWS'] -ne '0') -or ($prodAfter['IDS_SOURCES_SINCE'] -ne '0') -or ($prodAfter['IDS_EVIDENCE_SINCE'] -ne '0')) { throw 'STEP FAILED: the production database received W2 evidence -- send this output' }
      if ($stopWhy) { throw "STEP FAILED: W2 was stopped: $stopWhy -- nothing is recalculated and nothing is collected again; evidence kept in $w2" }
      if ($revisited.Count -ne 0) { throw "STEP FAILED: the collector visited $($revisited.Count) W1 canary flight(s) again -- nothing is recalculated; evidence kept in $w2" }
      if ($code -eq 24) { throw "STEP FAILED: the production collector took the shared lock first (exit 24); nothing was collected -- run this block again after it finishes" }
      if (@(0, 18) -notcontains $code) { throw "STEP FAILED: the collector ended with exit $code (2 session, 19 not accepted, 1 error) -- nothing is recalculated and nothing is collected again; evidence kept in $w2" }
      if (($ownerAfter -ne 'none') -and ($ownerAfter -notlike 'stale*')) { throw "STEP FAILED: the production lock is $ownerAfter after the run" }
      if ($left.Count -ne 0) { throw "STEP FAILED: $($left.Count) process(es) of this W2 run are still running" }
      if (-not $runId) { throw 'STEP FAILED: the collector printed no RUN SUMMARY with a run id' }
      if (-not $inPilot -or $inProd) { throw "STEP FAILED: the run was not logged by the pilot checkout only (pilot=$inPilot production=$inProd)" }
      if (($summary['sources_requested'] -ne [string]$remainingCount) -or ($summary['sources_visited'] -ne [string]$remainingCount) -or (($null -ne $summary['sources_skipped_known']) -and ($summary['sources_skipped_known'] -ne '0'))) { throw "STEP FAILED: the collector requested $($summary['sources_requested']), skipped $($summary['sources_skipped_known']) and visited $($summary['sources_visited']) of $remainingCount" }
      # [REASON]: card, airlines and route refusals (403, 429, an error code in the body) reach the
      # log only as this sum. A refused V4 download leaves its own V4_FAILED status and is measured.
      if ([int]$summary['sources_rejected'] -gt [int]$summary['sources_v4_failed']) { throw "STEP FAILED: DJI refused $([int]$summary['sources_rejected'] - [int]$summary['sources_v4_failed']) request(s) that were not V4 downloads (sources_rejected=$($summary['sources_rejected']), sources_v4_failed=$($summary['sources_v4_failed'])) -- nothing is recalculated; evidence kept in $w2" }
      if (($summary['sources_batch_accepted'] -ne 'true') -or ($summary['sources_ingest_errors'] -ne '0')) { throw "STEP FAILED: staging did not accept every source (accepted=$($summary['sources_batch_accepted']) errors=$($summary['sources_ingest_errors']))" }
      $statsLog = Join-Path $w2 'collector_for_stats.log'
      Write-Masked @($lines) $statsLog
      $statsJson = Join-Path $w2 'collector_stats_w2.json'
      $statsOut = @(& $python tools\dji_card_coverage_pilot.py collector-stats --log $statsLog --ids $idsFile --out $statsJson)
      $statsCode = $LASTEXITCODE
      $statsOut | ForEach-Object { Write-Output ('  ' + $_) }
      if ($statsCode -ne 0) { throw "STEP FAILED: collector-stats exit $statsCode (6 = DJI stop markers) -- nothing is recalculated; evidence kept in $w2" }
      $stats = Read-Json $statsJson
      if (([int]$stats.visited -ne $remainingCount) -or (@($stats.not_visited).Count -ne 0)) { throw "STEP FAILED: collector-stats saw $($stats.visited) of $remainingCount flights visited" }
      $stNew = Invoke-Helper @('newrows', $db, $idsFile, [string]$done.sources_max_id_before, [string]$done.v4sum_max_id_before, $t0, '9999-12-31 23:59:59', $runId)
      Write-Output ("STAGING_NEW_EVIDENCE revisions=" + $stNew['NEW_REVISIONS'] + " flights=" + $stNew['NEW_FLIGHTS'] + " outside_w2=" + $stNew['NEW_OUTSIDE_IDS'] + " other_run=" + $stNew['NEW_OTHER_RUN'] + " repeated_type=" + $stNew['NEW_REPEATED_TYPE'] + " evidence_outside_since=" + $stNew['EVIDENCE_OUTSIDE_SINCE'])
      if (($stNew['NEW_OUTSIDE_IDS'] -ne '0') -or ($stNew['NEW_OTHER_RUN'] -ne '0') -or ($stNew['NEW_REPEATED_TYPE'] -ne '0') -or ($stNew['EVIDENCE_OUTSIDE_SINCE'] -ne '0') -or ([int]$stNew['NEW_FLIGHTS'] -gt $remainingCount)) { throw 'STEP FAILED: staging holds new evidence that is not this run of the 450 W2 flights -- nothing is recalculated' }
      if ($stNew['NEW_REVISIONS'] -ne $summary['sources_new']) { throw "STEP FAILED: staging holds $($stNew['NEW_REVISIONS']) new revisions, the collector reported $($summary['sources_new'])" }
      Write-Fingerprint (Join-Path $w2 'fingerprint_after_collection.json')
      $gate = [ordered]@{ run_id = $runId; t0_utc = $t0; wall_seconds = $wall; sources_new = [int]$summary['sources_new']; visited = [int]$summary['sources_visited']; cards = [int]$summary['sources_card']; rejected = [int]$summary['sources_rejected']; v4_failed = [int]$summary['sources_v4_failed']; collector_exit = $code; remaining_ids_sha256 = $remainingSha; sources_max_id_before = [int]$done.sources_max_id_before }
      # [REASON]: written last: its presence means "collected and verified", and a later paste then
      # continues with S2 only, without visiting DJI again.
      [System.IO.File]::WriteAllText((Join-Path $w2 'collector_gate.json'), ($gate | ConvertTo-Json), [System.Text.Encoding]::ASCII)
      $gate = Read-Json (Join-Path $w2 'collector_gate.json')
      Write-Output 'COLLECTOR_GATE=PASS'
    }

    Write-Output '== 4. Staging S2: site stopped, recalc of exactly the 450 W2 flights, measure of all 500, census'
    $runId = [string]$gate.run_id
    [System.IO.File]::WriteAllText((Join-Path $w2 's2_started.txt'), ('S2 started ' + (Get-Date).ToString('yyyy-MM-dd HH:mm:ss') + "`n"), [System.Text.Encoding]::ASCII)
    Stop-Service -Name $service -Force
    $stoppedSite = $true
    (Get-Service -Name $service).WaitForStatus('Stopped', (New-TimeSpan -Seconds 90))
    & $python tools\check_db_lock.py --db $db | Out-Null
    if (@(0, 3) -notcontains $LASTEXITCODE) { throw "STEP FAILED: check_db_lock exit $LASTEXITCODE -- another process holds the staging database" }
    if ((Invoke-Helper @('snapshot', $db))['CATALOG_SHA256'] -ne $b0['CATALOG_SHA256']) { throw 'STEP FAILED: the staging field catalog changed during W2' }
    $fpRecalc = Join-Path $w2 'fingerprint_after_recalc.json'
    $v4Before = Invoke-Helper @('snapshot', $db)
    # [REASON]: --flight-id narrows the recalculation to exactly the 450; flights outside the
    # period would be dropped silently, so flights_in_period must be 450 in both runs.
    $flightArgs = @($remaining | ForEach-Object { '--flight-id'; [string]$_ })
    foreach ($mode2 in @('--dry-run', '--apply')) {
      $name = $mode2.TrimStart('-')
      $out = @(& $python tools\dji_area_recalc.py --db $db --from 2026-09-01 --to 2026-09-30 $mode2 --quiet --json (Join-Path $w2 ('recalc_' + $name + '.json')) @flightArgs)
      $rc = $LASTEXITCODE
      Set-Content -LiteralPath (Join-Path $w2 ('recalc_' + $name + '.txt')) -Value $out -Encoding UTF8
      if ($rc -ne 0) { throw "STEP FAILED: recalc $mode2 exit $rc -- $($out -join ' ')" }
      $r = Read-Json (Join-Path $w2 ('recalc_' + $name + '.json'))
      $calc = 0; foreach ($q in $r.calc_writes.PSObject.Properties) { $calc += [int]$q.Value }
      $field = 0; foreach ($q in $r.field_writes.PSObject.Properties) { $field += [int]$q.Value }
      Write-Output ("RECALC_" + $name.ToUpper() + " flights_in_period=" + $r.flights_in_period + " calc_writes=" + $calc + " (" + (@($r.calc_writes.PSObject.Properties | ForEach-Object { $_.Name + '=' + $_.Value }) -join ',') + ") field_writes=" + $field + " (" + (@($r.field_writes.PSObject.Properties | ForEach-Object { $_.Name + '=' + $_.Value }) -join ',') + ") tiers=" + (@($r.tier_counts.PSObject.Properties | ForEach-Object { $_.Name + '=' + $_.Value }) -join ','))
      if ([int]$r.flights_in_period -ne $remainingCount) { throw "STEP FAILED: recalc $mode2 took $($r.flights_in_period) flights, expected exactly the $remainingCount W2 flights" }
      if (($mode2 -eq '--apply') -and (($calc -ne $remainingCount) -or ($field -ne $remainingCount))) { throw "STEP FAILED: recalc accounted for $calc calculation and $field attribution rows, expected $remainingCount and $remainingCount" }
    }
    $v4After = Invoke-Helper @('newrows', $db, $idsFile, $v4Before['SOURCE_MAX_ID'], $v4Before['V4SUM_MAX_ID'], (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ss'), '9999-12-31 23:59:59', $runId)
    Write-Output ("V4_SUMMARY_CACHE new=" + $v4After['V4SUM_NEW'] + " outside_w2=" + $v4After['V4SUM_NEW_OUTSIDE_IDS'] + " (decoded-V4 cache recalc keeps for loaded neighbours; not evidence, not attribution)")
    if ($v4After['NEW_REVISIONS'] -ne '0') { throw 'STEP FAILED: source revisions changed during the recalc' }
    Write-Fingerprint $fpRecalc
    $d1 = Invoke-Helper @('fpdiff', (Join-Path $w2 'fingerprint_after_collection.json'), $fpRecalc, $idsFile, $canaryCopy)
    $d2 = Invoke-Helper @('fpdiff', (Join-Path $w2 'fingerprint_pre.json'), $fpRecalc, $idsFile, $canaryCopy)
    Write-Output ("CHANGED_BY_RECALC flights=" + $d1['CHANGED'] + " outside_w2=" + $d1['CHANGED_OUTSIDE_ALLOWED'] + " w1_canary=" + $d1['KEEP_CHANGED'] + " raw=" + $d1['SAME_RAW'] + " decisions=" + $d1['SAME_DECISIONS'] + " migrations=" + $d1['SAME_MIGRATIONS'] + " sources=" + $d1['SAME_SOURCES'] + " (1 = unchanged)")
    Write-Output ("CHANGED_SINCE_W1 flights=" + $d2['CHANGED'] + " outside_w2=" + $d2['CHANGED_OUTSIDE_ALLOWED'] + " w1_canary=" + $d2['KEEP_CHANGED'] + " raw=" + $d2['SAME_RAW'] + " decisions=" + $d2['SAME_DECISIONS'] + " migrations=" + $d2['SAME_MIGRATIONS'] + " (1 = unchanged)")
    if (($d1['CHANGED_OUTSIDE_ALLOWED'] -ne '0') -or ($d1['KEEP_CHANGED'] -ne '0') -or ($d1['SAME_RAW'] -ne '1') -or ($d1['SAME_DECISIONS'] -ne '1') -or ($d1['SAME_MIGRATIONS'] -ne '1') -or ($d1['SAME_SOURCES'] -ne '1')) { throw 'STEP FAILED: the recalculation changed something other than the 450 W2 flights' }
    if (($d2['CHANGED_OUTSIDE_ALLOWED'] -ne '0') -or ($d2['KEEP_CHANGED'] -ne '0') -or ($d2['SAME_RAW'] -ne '1') -or ($d2['SAME_DECISIONS'] -ne '1') -or ($d2['SAME_MIGRATIONS'] -ne '1')) { throw 'STEP FAILED: since W1 something other than the 450 W2 flights changed (the 50 W1 results must stay as they are)' }
    $w1ForStats = Join-Path $w2 'collector_for_stats_w1.log'
    Write-Masked (Read-Utf8 (Join-Path $w1Run 'collector_stdout.log')) $w1ForStats
    $statsAllJson = Join-Path $w2 'collector_stats_pilot.json'
    $sOut = @(& $python tools\dji_card_coverage_pilot.py collector-stats --log $w1ForStats --log (Join-Path $w2 'collector_for_stats.log') --ids $pilotCopy --out $statsAllJson)
    $sCode = $LASTEXITCODE
    $sOut | ForEach-Object { Write-Output ('  W1+W2 ' + $_) }
    if ($sCode -ne 0) { throw "STEP FAILED: collector-stats over the W1 and W2 logs exit $sCode" }
    $statsAll = Read-Json $statsAllJson
    if (([int]$statsAll.visited -ne $pilotCount) -or (@($statsAll.not_visited).Count -ne 0)) { throw "STEP FAILED: the W1 and W2 logs together visited $($statsAll.visited) of the $pilotCount frozen flights" }
    $mOut = @(& $python tools\dji_card_coverage_pilot.py measure --db $db --plan-dir $planDir --stage pilot --before $fpB0 --collector-stats $statsAllJson --out-dir (Join-Path $w2 'measure'))
    $mCode = $LASTEXITCODE
    $mOut | ForEach-Object { Write-Output ('  ' + $_) }
    if ($mCode -ne 0) { throw "STEP FAILED: measure exit $mCode (5 = an immutability gate against B0 failed) -- send this output" }
    $measure = Read-Json ([System.IO.Path]::Combine($w2, 'measure', 'measure_pilot.json'))
    if ([int]$measure.attempted -ne $pilotCount) { throw "STEP FAILED: measure counted $($measure.attempted) attempted flights, expected $pilotCount" }
    $m2Out = @(& $python tools\dji_card_coverage_pilot.py measure --db $db --plan-dir $planDir --stage pilot --before (Join-Path $w2 'fingerprint_pre.json') --out-dir (Join-Path $w2 'measure_since_w1'))
    $m2Code = $LASTEXITCODE
    $m2Out | Where-Object { $_ -match 'GATE ' } | ForEach-Object { Write-Output ('  SINCE_W1 ' + $_) }
    if ($m2Code -ne 0) { throw "STEP FAILED: measure against the state after W1 exit $m2Code (W1 sources, RAW, decisions, migrations or flights outside the 500 changed)" }
    $cOut = @(& $python tools\dji_field_census.py --db $db --from 2026-09-01 --to 2026-09-30 --json (Join-Path $w2 'census_after.json'))
    if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: census after W2 exit $LASTEXITCODE" }
    $seen = @{}
    foreach ($l in @($cOut | Where-Object { $_ -match 'flights total|CONFIRMED|EXACT|IDENTIFIED|NO_CARD|NO_KEY|NOT_IN_CATALOG|KEY_AFTER' })) { $x = $l.Trim(); if (-not $seen.ContainsKey($x)) { $seen[$x] = 1; Write-Output ('  CENSUS ' + $x) } }
    $diag = @(& $python -I (Join-Path $w2 'w2_check.py') diag $db ([System.IO.Path]::Combine($w2, 'measure', 'results_pilot.csv')))
    if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: the read-only diagnostics exit $LASTEXITCODE" }
    Set-Content -LiteralPath (Join-Path $w2 'diagnostics.txt') -Value $diag -Encoding ASCII
    $diag | ForEach-Object { Write-Output ('  ' + $_) }
    Start-Service -Name $service
    (Get-Service -Name $service).WaitForStatus('Running', (New-TimeSpan -Seconds 90))
    $stoppedSite = $false
    Start-Sleep -Seconds 8
    $reg = @(Get-Registered (Join-Path $w2 ('drift_after_' + $stamp + '.log')))
    if (($reg.Count -ne 1) -or ($reg[0] -ne '60')) { throw "STEP FAILED: after W2 the staging database reports $($reg -join ',') registered migrations" }
    Test-Staging 'AFTER'
    Test-Production 'AFTER'
    if ($mode -eq 'fresh') { if ((Get-FileState $session) -ne $sessionBefore) { throw 'STEP FAILED: the production DJI session file changed' } }
    [System.IO.File]::WriteAllText((Join-Path $w2 's2_done.txt'), ('S2 done ' + (Get-Date).ToString('yyyy-MM-dd HH:mm:ss') + "`n"), [System.Text.Encoding]::ASCII)
  } catch {
    $failure = $_.Exception.Message + ' [block line ' + $_.InvocationInfo.ScriptLineNumber + ']'
  } finally {
    # [REASON]: in finally, so that Ctrl+C during S2 does not leave the staging site stopped.
    if ($stoppedSite) {
      $stoppedSite = $false
      try { Start-Service -Name $service; (Get-Service -Name $service).WaitForStatus('Running', (New-TimeSpan -Seconds 90)); Write-Output ("STAGING_SITE_RESTARTED=" + (Get-Service -Name $service).Status) } catch { Write-Output ("STAGING_SITE_RESTART_FAILED=" + $_.Exception.Message) }
    }
  }

  try {
  Write-Output '== 5. Pilot result: all 500 frozen flights (W1 50 + W2 450)'
  if ($statsAll) {
    $median = if ($null -ne $statsAll.seconds_per_visit_median) { [math]::Round([double]$statsAll.seconds_per_visit_median, 1) } else { '-' }
    $mean = if ([int]$statsAll.visits_measured -gt 0) { [math]::Round([double]$statsAll.seconds_measured / [int]$statsAll.visits_measured, 1) } else { '-' }
    Write-Output ("COLLECTION visited=" + $statsAll.visited + " of " + $pilotCount + " cards=" + $statsAll.card_captured + " without_card=" + ($pilotCount - [int]$statsAll.card_captured) + " page_errors=" + $statsAll.page_errors + " W2_WALL_SECONDS=" + $wall + " MEAN_SECONDS_PER_FLIGHT=" + $mean + " MEDIAN_SECONDS_PER_FLIGHT=" + $median)
    Write-Output ("STATUSES=" + ($statsAll.statuses | ConvertTo-Json -Compress) + " STOP_MARKERS=" + ($statsAll.stop_markers | ConvertTo-Json -Compress))
    if ($gate) { Write-Output ("SOURCES_SAVED W1=" + $w1New + " W2=" + $gate.sources_new + " total=" + ($w1New + [int]$gate.sources_new) + " W2_REJECTED=" + $gate.rejected + " W2_V4_FAILED=" + $gate.v4_failed) }
  }
  if ((-not $failure) -and $measure) {
    $rows = @(Import-Csv -LiteralPath ([System.IO.Path]::Combine($w2, 'measure', 'results_pilot.csv')))
    $count = @{}
    foreach ($r in $rows) { $count[$r.after] = 1 + [int]$count[$r.after] }
    $exact = [int]$count['EXACT']
    $ident = [int]$count['IDENTIFIED']
    $other = @($rows | Where-Object { @('EXACT', 'IDENTIFIED', 'NO_KEY', 'NOT_IN_CATALOG', 'NO_CARD') -notcontains $_.after }).Count
    Write-Output ("OUTCOME EXACT=" + $exact + " IDENTIFIED=" + $ident + " CONFIRMED=" + ($exact + $ident) + " NO_KEY=" + [int]$count['NO_KEY'] + " NOT_IN_CATALOG=" + [int]$count['NOT_IN_CATALOG'] + " NO_CARD=" + [int]$count['NO_CARD'] + " OTHER_UNRESOLVED=" + $other + " NO_CALC=" + @($rows | Where-Object { $_.has_calc_after -ne 'True' }).Count)
    foreach ($g in @($rows | Group-Object after | Sort-Object Name)) { Write-Output ("CASES " + $g.Name + ": " + (@($g.Group | Select-Object -First 5 | ForEach-Object { $_.flight_id }) -join ', ')) }
    $w2Conf = @($rows | Where-Object { (-not $canarySet.Contains([int64]$_.flight_id)) -and ($_.confirmed_after -eq 'True') }).Count
    $w1Conf = @($rows | Where-Object { $canarySet.Contains([int64]$_.flight_id) -and ($_.confirmed_after -eq 'True') }).Count
    $cm = $measure.conversion_among_manifest
    $cf = $measure.conversion_among_fetched
    Write-Output ("CONFIRMED_RATE all_500=" + (Pct $cm.rate) + " wilson95=" + (Pct $cm.wilson95[0]) + ".." + (Pct $cm.wilson95[1]) + " among_fetched=" + (Pct $cf.rate) + " wilson95=" + (Pct $cf.wilson95[0]) + ".." + (Pct $cf.wilson95[1]) + " fetched=" + $measure.fetched)
    Write-Output ("COMPARE W1=" + $w1Conf + "/" + $canaryCount + " " + (Pct ($w1Conf / [double]$canaryCount)) + " W2=" + $w2Conf + "/" + $remainingCount + " " + (Pct ($w2Conf / [double]$remainingCount)) + " ALL=" + ($w1Conf + $w2Conf) + "/" + $pilotCount + " " + (Pct (($w1Conf + $w2Conf) / [double]$pilotCount)))
    $ps = $measure.post_stratified_among_fetched
    if ($ps) { Write-Output ("PROJECTION (not a fact; n=" + $pilotCount + ") post-stratified to the September NO_CARD cohort: " + (Pct $ps.estimate) + " (" + (Pct $ps.low) + ".." + (Pct $ps.high) + ")") }
    foreach ($k in @('at_manifest_rate', 'at_manifest_wilson_low', 'at_manifest_wilson_high')) { $v = $measure.projection_on_no_card_cohort.$k; if ($v) { Write-Output ("PROJECTION (not a fact) " + $k + ": +" + $v.additional_confirmed + " confirmed of " + $measure.projection_on_no_card_cohort.no_card_flights + " NO_CARD -> " + $v.coverage_pct + "% of September") } }
    foreach ($part in @('by_unit', 'by_week')) { foreach ($q in $measure.$part.PSObject.Properties) { Write-Output (($part.ToUpper()) + " " + $q.Name + " attempted=" + $q.Value.attempted + " fetched=" + $q.Value.fetched + " confirmed=" + $q.Value.confirmed + " no_key=" + $q.Value.no_key + " not_in_catalog=" + $q.Value.not_in_catalog) } }
    Write-Output 'PILOT_STATUS=COMPLETE (W2 and R are not started by this block; R is a separate step)'
  }
  if ($w2) {
    $state = Get-RunState $w2
    Write-Output ("RUN=" + $w2)
    if ($state -eq 'COLLECTION_STOPPED') { Write-PartialState $w2 }
    $stateText = @{ NOT_STARTED = 'W2 did not visit DJI; this block may be pasted again'; COLLECTION_STOPPED = 'DJI was visited and the collection did not end by itself; pasting this block again collects nothing'; GATE_PENDING = 'the collector ended by itself and its gate did not pass or was cut off; pasting this block again checks the gate again, without DJI'; S2_PENDING = 'the 450 are collected and verified; pasting this block again runs only S2, without DJI'; COMPLETE = 'W2+S2 done; R is a separate step' }
    Write-Output ("W2_STATE=" + $state + " -- " + $stateText[$state])
  }
  } catch {
    # [REASON]: the report only reads; if it fails, the owner still gets the STEP line.
    Write-Output ("REPORT_FAILED=" + $_.Exception.Message)
    if (-not $failure) { $failure = 'the report failed: ' + $_.Exception.Message }
  }
  Write-Output ("LOG FILE: " + $log)
  if ($failure) { Write-Output ("STEP=STOP - " + $failure) } else { Write-Output 'STEP=PASS' }
  try { Stop-Transcript | Out-Null } catch { }
}
