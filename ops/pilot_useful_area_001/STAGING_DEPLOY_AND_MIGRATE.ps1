<#
    STAGING_DEPLOY_AND_MIGRATE.ps1
    DRONE-USEFUL-AREA-PILOT-001, step 2 of 6.

    WHAT THIS DOES
      Backs up the staging database through the SQLite online backup API and
      VERIFIES that backup, fast-forwards the staging checkout to PRODUCT_SHA
      and no other revision, runs the syntax and targeted checks, stops ONLY
      the staging service, applies DRONES_USEFUL_AREA_001 to the staging
      database only, proves drone_flights.area_ha did not move, starts the
      staging service again and smoke-tests it against a KNOWN endpoint with
      an exact allowed status.

      Everything lands in the run opened by step 1: same run id, same
      manifest, fixed evidence paths. Nothing here searches for the "newest"
      anything.

    WHAT THIS NEVER DOES
      * touches C:\transport-report in any way;
      * stops, starts or restarts TransportReport;
      * moves the checkout to whatever origin/main happens to be -- the merge
        is fast-forward to PRODUCT_SHA, so a commit merged after this kit was
        reviewed cannot arrive by accident;
      * rewrites history: the rollback is a detached checkout, never
        `git reset --hard`, which the project charter forbids as a rollback;
      * runs an executable it did not materialize from a git blob.

    RUN (on the server, from the KIT checkout):
      Set-Location C:\vehicle-soft-pilot-kit
      .\ops\pilot_useful_area_001\STAGING_DEPLOY_AND_MIGRATE.ps1 -RunId <the id step 1 printed>
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$RunId,
    [string]$ExpectedHost = 'srv-yoqsh',
    [string]$RunsRoot = 'D:\transport-report-backups\pilot\DRONE-USEFUL-AREA-001\runs',
    [string]$BackupDir = 'D:\transport-report-backups\staging\daily',
    [string]$Python = 'C:\Program Files\Python314\python.exe',
    [int]$ServiceTimeoutSeconds = 90
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Import-Module (Join-Path $PSScriptRoot 'PilotKit.psm1') -Force
$K = Get-PilotConstants
$KitCheckout = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

Write-Output "=== DRONE-USEFUL-AREA-PILOT-001 / STAGING DEPLOY AND MIGRATE ==="

# --- 1. Machine, run, service ----------------------------------------------
Assert-PilotHost -Expected $ExpectedHost
Assert-PilotOutsideCheckouts -Path $RunsRoot -What 'runs root'
Assert-PilotNotProduction -Path $BackupDir -What 'backup directory'

$run = Get-PilotRun -RunsRoot $RunsRoot -RunId $RunId
$KitSha = Get-PilotKitSha -KitCheckout $KitCheckout
if ($KitSha -ne $run.kit_sha) {
    throw "REFUSED: this kit checkout is at $KitSha, the run was opened with $($run.kit_sha). One run, one kit revision."
}
Write-Output "RUN_ID=$RunId"
Write-Output "KIT_SHA=$KitSha"
Write-Output "PRODUCT_SHA=$($K.ProductSha)"

if (-not (Test-Path -LiteralPath $K.StagingRoot)) {
    throw "REFUSED: the staging checkout $($K.StagingRoot) does not exist."
}
$runRoot = $run.run_root
$logDir = Join-Path $runRoot 'log'
$sandbox = Join-Path $runRoot 'sandbox'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

$runbook = Join-Path $KitCheckout 'docs\ORG_WINDOWS_SERVER_STAGING_RUNBOOK.md'
$stagingService = Resolve-PilotStagingService -RunbookPath $runbook
Assert-PilotServiceIsNotProduction -Name $stagingService
$serviceWhere = Get-PilotServiceImagePath -Name $stagingService
Assert-PilotStagingPath -Path $serviceWhere.AppDirectory -What 'staging service directory'
Write-Output "STAGING_SERVICE=$stagingService"

$serviceBefore = (Get-Service -Name $stagingService).Status.ToString()
Write-Output "STAGING_SERVICE_STATUS_BEFORE=$serviceBefore"

# --- 2. State before anything changes --------------------------------------
Assert-PilotWorktreeClean -Repo $K.StagingRoot
$shaBefore = Get-PilotHeadSha -Repo $K.StagingRoot
$branchBefore = (Invoke-PilotGit -Repo $K.StagingRoot -Arguments @('rev-parse', '--abbrev-ref', 'HEAD')).Output
Write-Output "SHA_BEFORE=$shaBefore"
Write-Output "BRANCH_BEFORE=$branchBefore"

if (-not (Test-Path -LiteralPath $K.StagingDb)) {
    throw "REFUSED: the staging database $($K.StagingDb) was not found."
}

$probe = Join-Path $PSScriptRoot 'pilot_db_probe.py'
$repoTool = Join-Path $PSScriptRoot 'pilot_repo_check.py'

# --- 3. Backup BEFORE any change, and VERIFY it ----------------------------
# The backup tool itself is materialized from PRODUCT_SHA, like everything
# else this kit executes.
New-Item -ItemType Directory -Path $sandbox -Force | Out-Null
Invoke-PilotProbe -Python $Python -Script $repoTool -RunId $RunId -KitSha $KitSha `
    -OutFile (Join-Path $runRoot 'evidence\materialize_deploy.json') `
    -Arguments @('materialize', '--repo', $K.StagingRoot, '--rev', $K.ProductSha,
                 '--into', $sandbox,
                 '--file', 'backup_transport_db.py',
                 '--file', 'migrate_drones_useful_area_001.py',
                 '--file', 'migration_utils.py') | Out-Null
$backupTool = Join-Path $sandbox 'backup_transport_db.py'
$migration = Join-Path $sandbox 'migrate_drones_useful_area_001.py'

Write-Output "--- backing up the staging database (SQLite online backup API) ---"
$before = @(Get-ChildItem -LiteralPath $BackupDir -Filter '*.db' -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty FullName)
Invoke-PilotPython -Python $Python -Arguments @(
    $backupTool, '--source', $K.StagingDb, '--dest-dir', $BackupDir,
    '--suffix', 'pilot_before_useful_area') | Out-Null
$fresh = @(Get-ChildItem -LiteralPath $BackupDir -Filter '*.db' |
           Where-Object { $before -notcontains $_.FullName } |
           Sort-Object LastWriteTime -Descending)
if ($fresh.Count -lt 1) {
    throw "REFUSED: the backup reported success but no new file appeared in $BackupDir."
}
$backupPath = $fresh[0].FullName
$backupSha  = Get-PilotFileSha256 -Path $backupPath
Write-Output "BACKUP=$backupPath"
Write-Output "BACKUP_SHA256=$backupSha"

# [REASON]: the backup is checked HERE, not at rollback time. A backup whose
# integrity nobody looked at is a rollback plan nobody has tested, and the
# moment it is needed is the worst moment to discover that.
$backupState = (Invoke-PilotProbe -Python $Python -Script $probe -RunId $RunId -KitSha $KitSha `
    -OutFile (Join-Path $runRoot 'evidence\backup_integrity.json') -AllowedExitCodes @(0, 3) `
    -Arguments @('integrity', '--db', $backupPath, '--require', 'integrity')).Evidence
$backupVerified = [bool]$backupState.payload.integrity.integrity_ok
Write-Output "BACKUP_INTEGRITY=$backupVerified"
if (-not $backupVerified) {
    throw "REFUSED: PRAGMA integrity_check on the fresh backup did not return ok. NOTHING was changed."
}

# --- 4. Fingerprints and the manifest, BEFORE the migration -----------------
$snapBefore = (Invoke-PilotProbe -Python $Python -Script $probe -RunId $RunId -KitSha $KitSha `
    -OutFile (Get-PilotEvidencePath -RunsRoot $RunsRoot -RunId $RunId -Name 'staging_before') `
    -Arguments @('snapshot', '--db', $K.StagingDb, '--day', $K.TargetDay,
                 '--require', 'integrity')).Evidence
Write-Output "STAGING_AREA_SHA256_BEFORE=$($snapBefore.payload.area_ha.sha256)"

$deployEvidence = Get-PilotEvidencePath -RunsRoot $RunsRoot -RunId $RunId -Name 'deploy'

# [REASON]: the deploy evidence is written NOW, before the first change, and
# rewritten at each phase. A run that dies between the backup and the end must
# still leave a rollback point behind -- evidence written only on success is
# evidence that never exists when it is needed.
$payload = [ordered]@{
    machine                 = $env:COMPUTERNAME
    phase                   = 'backed-up'
    staging_root            = $K.StagingRoot
    staging_db              = $K.StagingDb
    staging_service         = $stagingService
    service_status_before   = $serviceBefore
    branch_before           = $branchBefore
    sha_before              = $shaBefore
    sha_after               = $null
    backup_path             = $backupPath
    backup_sha256           = $backupSha
    backup_bytes            = $fresh[0].Length
    backup_verified         = $backupVerified
    area_ha_before          = $snapBefore.payload.area_ha
    area_ha_after           = $null
    area_ha_unchanged       = $false
    migration_on_staging_ok = $false
    service_started         = $false
    smoke_test_ok           = $false
    smoke_test_status       = $null
    smoke_test_path         = $K.SmokePath
    failures                = @()
}

function Save-Deploy([string]$phase) {
    $payload.phase = $phase
    $envelope = [ordered]@{
        kit           = 'DRONE-USEFUL-AREA-PILOT-001'
        kit_version   = '2'
        evidence_kind = 'deploy'
        run_id        = $RunId
        kit_sha       = $KitSha
        product_sha   = $K.ProductSha
        generated_utc = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        target_day    = $K.TargetDay
        payload       = $payload
    }
    Write-PilotJson -Path $deployEvidence -Value $envelope | Out-Null
    Set-PilotRunStep -RunsRoot $RunsRoot -RunId $RunId -Step 'deploy' -Value ([ordered]@{
        completed_utc = $envelope.generated_utc
        phase         = $phase
        evidence      = $deployEvidence
    }) | Out-Null
}
Save-Deploy 'backed-up'
Write-Output "DEPLOY_EVIDENCE=$deployEvidence"

# --- 5. Fast-forward to PRODUCT_SHA, and to nothing else -------------------
Write-Output "--- fetching and fast-forwarding to $($K.ProductSha) ---"
Invoke-PilotGit -Repo $K.StagingRoot -Arguments @('fetch', 'origin', 'main') | Out-Null
$hasCommit = Invoke-PilotGit -Repo $K.StagingRoot -AllowFailure `
    -Arguments @('cat-file', '-e', ($K.ProductSha + '^{commit}'))
if ($hasCommit.ExitCode -ne 0) {
    throw "REFUSED: the verified product commit $($K.ProductSha) is not in the staging checkout after fetching origin/main."
}
# [REASON]: `merge --ff-only <product sha>`, NOT `pull origin main`. Fetching
# main and merging its tip would move staging to whatever was merged after
# this kit was reviewed. Fast-forwarding to the named commit refuses anything
# that is not a fast-forward and lands on exactly that tree.
Invoke-PilotGit -Repo $K.StagingRoot -Arguments @('merge', '--ff-only', $K.ProductSha) | Out-Null
Assert-PilotProductSha -Repo $K.StagingRoot
Assert-PilotWorktreeClean -Repo $K.StagingRoot
$payload.sha_after = Get-PilotHeadSha -Repo $K.StagingRoot
Save-Deploy 'code-updated'
Write-Output "SHA_AFTER=$($payload.sha_after)"

# --- 6. Syntax and targeted checks, service still up ------------------------
# [REASON]: the test harness points the application at a THROWAWAY database in
# a temp directory (tests/harness.py), so these run safely while staging is up.
# The staging fingerprint is taken again after them, which is what proves it.
Write-Output "--- syntax and targeted checks ---"
Push-Location $K.StagingRoot
try {
    & $Python -m compileall -q .
    if ($LASTEXITCODE -ne 0) { throw "REFUSED: compileall failed with exit $LASTEXITCODE." }

    & $Python 'tools\check_templates.py'
    if ($LASTEXITCODE -ne 0) { throw "REFUSED: tools\check_templates.py failed with exit $LASTEXITCODE." }

    & $Python 'tools\test_drone_coverage_recalc.py'
    if ($LASTEXITCODE -ne 0) { throw "REFUSED: tools\test_drone_coverage_recalc.py failed with exit $LASTEXITCODE." }

    & $Python -m unittest tests.test_drone_useful_area_migration_001 tests.test_drone_useful_area_001 tests.test_drone_useful_area_002
    if ($LASTEXITCODE -ne 0) { throw "REFUSED: the useful-area test suites failed with exit $LASTEXITCODE." }
} finally {
    Pop-Location
}
Write-Output "CHECKS=PASS"

# --- 7. Stop ONLY the staging service ---------------------------------------
Write-Output "--- stopping $stagingService ---"
Assert-PilotServiceIsNotProduction -Name $stagingService
Stop-Service -Name $stagingService -Force
$deadline = (Get-Date).AddSeconds($ServiceTimeoutSeconds)
while ((Get-Service -Name $stagingService).Status -ne 'Stopped' -and (Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
}
if ((Get-Service -Name $stagingService).Status -ne 'Stopped') {
    throw "REFUSED: $stagingService did not stop within $ServiceTimeoutSeconds s. The migration was NOT applied. Restore point: $backupPath"
}
Write-Output "STAGING_SERVICE_STATUS=Stopped"

$productionStatus = (Get-Service -Name $K.ProductionService -ErrorAction SilentlyContinue)
if ($productionStatus) {
    Write-Output "PRODUCTION_SERVICE_STATUS=$($productionStatus.Status) (untouched)"
}

# --- 8. Apply the migration to STAGING only ---------------------------------
# [REASON]: the migration is run IN PLACE, from the staging checkout, and that
# is not a shortcut. It derives its database path from its own __file__, so it
# has to sit beside instance\transport.db; and the file it runs is already
# proven to be the PRODUCT_SHA blob -- checked twice, in the history and on
# disk, by the verify step below, on a checkout whose working tree is clean.
# A junction or a copy-out-copy-back would add a way to lose the database
# without adding a single fact about which bytes ran.
$migrationVerify = (Invoke-PilotProbe -Python $Python -Script $repoTool -RunId $RunId -KitSha $KitSha `
    -OutFile (Join-Path $runRoot 'evidence\repo_staging_before_migration.json') `
    -AllowedExitCodes @(0, 3) `
    -Arguments @('verify', '--repo', $K.StagingRoot, '--expect-sha', $K.ProductSha,
                 '--role', 'product')).Evidence
if (-not $migrationVerify.payload.passed) {
    $payload.failures = @('STAGING_BLOBS_NOT_VERIFIED')
    Save-Deploy 'verification-failed'
    throw "REFUSED: the staging checkout does not carry the verified bytes ($($migrationVerify.payload.problems -join ', ')). The service is stopped; restore point: $backupPath"
}
$migrationInPlace = Join-Path $K.StagingRoot 'migrate_drones_useful_area_001.py'
Write-Output "MIGRATION_BLOB=$($migrationVerify.payload.blobs_checked.'migrate_drones_useful_area_001.py'.on_disk)"

$migrationLog = Join-Path $logDir 'migration.txt'
Write-Output "--- applying $($K.MigrationId) to staging ---"
Push-Location $K.StagingRoot
try {
    $migrationOutput = & $Python $migrationInPlace 2>&1
} finally {
    Pop-Location
}
$migrationCode = $LASTEXITCODE
$migrationText = ($migrationOutput | Out-String)
[System.IO.File]::WriteAllText($migrationLog, $migrationText, (New-Object System.Text.UTF8Encoding($false)))
Write-Output ("  " + $migrationText.Trim())

if ($migrationCode -ne 0) {
    $payload.failures = @('MIGRATION_FAILED')
    Save-Deploy 'migration-failed'
    throw @"
REFUSED: the migration exited $migrationCode. The staging service is STILL STOPPED on purpose.

Manual recovery point:
  1. read $migrationLog
  2. roll staging back with the run manifest:
     .\ops\pilot_useful_area_001\STAGING_ROLLBACK.ps1 -RunId $RunId
  Backup: $backupPath
"@
}

# --- 9. Prove the migration and the untouched area_ha -----------------------
$afterRun = Invoke-PilotProbe -Python $Python -Script $probe -RunId $RunId -KitSha $KitSha `
    -OutFile (Get-PilotEvidencePath -RunsRoot $RunsRoot -RunId $RunId -Name 'staging_after') `
    -AllowedExitCodes @(0, 3) `
    -Arguments @('snapshot', '--db', $K.StagingDb, '--day', $K.TargetDay,
                 '--require', 'integrity', '--require', 'schema',
                 '--require', ("area-sha256=" + $snapBefore.payload.area_ha.sha256))
$snapAfter = $afterRun.Evidence

$payload.area_ha_after = $snapAfter.payload.area_ha
$payload.area_ha_unchanged = ($snapAfter.payload.area_ha.sha256 -eq $snapBefore.payload.area_ha.sha256)

$failures = @()
if ($afterRun.ExitCode -ne 0) { $failures += 'PROBE_REQUIREMENTS_FAILED' }
if (-not $snapAfter.payload.integrity.integrity_ok) { $failures += 'INTEGRITY_CHECK_FAILED' }
if (-not $snapAfter.payload.schema.tables_all_present) { $failures += 'TABLES_MISSING' }
if (-not $snapAfter.payload.schema.indexes_all_present) { $failures += 'INDEXES_MISSING' }
if (-not $snapAfter.payload.schema.migration_registered) { $failures += 'REGISTRY_ROW_MISSING' }
if (-not $payload.area_ha_unchanged) { $failures += 'AREA_HA_CHANGED' }

Write-Output "STAGING_INTEGRITY=$($snapAfter.payload.integrity.integrity_ok)"
Write-Output "STAGING_AREA_SHA256_AFTER=$($snapAfter.payload.area_ha.sha256)"

if ($failures.Count -gt 0) {
    $payload.failures = $failures
    Save-Deploy 'verification-failed'
    throw @"
REFUSED after the migration: $($failures -join ', ').
The staging service is STILL STOPPED on purpose.

Manual recovery point:
  .\ops\pilot_useful_area_001\STAGING_ROLLBACK.ps1 -RunId $RunId
  Backup: $backupPath
"@
}
$payload.migration_on_staging_ok = $true

# --- 10. Start the staging service and smoke-test it ------------------------
Write-Output "--- starting $stagingService ---"
Assert-PilotServiceIsNotProduction -Name $stagingService
Start-Service -Name $stagingService
$deadline = (Get-Date).AddSeconds($ServiceTimeoutSeconds)
while ((Get-Service -Name $stagingService).Status -ne 'Running' -and (Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
}
if ((Get-Service -Name $stagingService).Status -ne 'Running') {
    $payload.failures = @('SERVICE_DID_NOT_START')
    Save-Deploy 'service-did-not-start'
    throw "REFUSED: $stagingService did not start within $ServiceTimeoutSeconds s. Backup: $backupPath"
}
$payload.service_started = $true

$smoke = Invoke-PilotSmokeTest -BaseUrl $K.StagingUrl
$payload.smoke_test_ok = [bool]$smoke.ok
$payload.smoke_test_status = $smoke.status
Write-Output "SMOKE_TEST $($smoke.path) status=$($smoke.status) ok=$($smoke.ok) reason=$($smoke.reason)"
if (-not $smoke.ok) {
    $payload.failures = @("SMOKE_TEST_FAILED_$($smoke.reason)")
    Save-Deploy 'smoke-test-failed'
    throw "REFUSED: the staging smoke test did not pass ($($smoke.reason)). Backup: $backupPath"
}

Save-Deploy 'done'
Write-Output ""
Write-Output "DEPLOY_EVIDENCE=$deployEvidence"
Write-Output "BACKUP=$backupPath"
Write-Output "STAGING_DEPLOY_AND_MIGRATE=PASS"
Write-Output "RUN_ID=$RunId"
