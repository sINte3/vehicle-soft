<#
    STAGING_DEPLOY_AND_MIGRATE.ps1
    DRONE-USEFUL-AREA-PILOT-001, step 2 of 6.

    WHAT THIS DOES
      Backs up the staging database through the SQLite online backup API,
      fast-forwards the staging checkout to the VERIFIED commit and no other,
      runs the syntax and targeted checks, stops ONLY the staging service,
      applies DRONES_USEFUL_AREA_001 to the staging database only, proves
      drone_flights.area_ha did not move, starts the staging service again and
      smoke-tests http://10.103.25.14:5051.

      It writes a MANIFEST. STAGING_ROLLBACK.ps1 reads that manifest and needs
      no file name typed by hand.

    WHAT THIS NEVER DOES
      * touches C:\transport-report in any way;
      * stops, starts or restarts TransportReport;
      * moves the checkout to whatever origin/main happens to be -- the merge
        is fast-forward to the VERIFIED sha, so a commit merged after this kit
        was written cannot arrive by accident;
      * rewrites history: the rollback is a detached checkout, never
        `git reset --hard`, which the project charter forbids as a rollback.

    RUN (on the server):
      Set-Location C:\transport-report-staging
      .\ops\pilot_useful_area_001\STAGING_DEPLOY_AND_MIGRATE.ps1

    On any failure the script stops where it is and prints the exact manual
    recovery point, including the backup path.
#>

[CmdletBinding()]
param(
    [string]$ExpectedHost = 'srv-yoqsh',
    [string]$WorkRoot = 'D:\transport-report-backups\pilot\DRONE-USEFUL-AREA-001',
    [string]$BackupDir = 'D:\transport-report-backups\staging\daily',
    [string]$Python = 'C:\Program Files\Python314\python.exe',
    [int]$ServiceTimeoutSeconds = 90
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Import-Module (Join-Path $PSScriptRoot 'PilotKit.psm1') -Force
$K = Get-PilotConstants

$stamp      = (Get-Date).ToString('yyyyMMdd_HHmmss')
$runRoot    = Join-Path $WorkRoot ("deploy_{0}" -f $stamp)
$evidence   = Join-Path $runRoot 'evidence'
$logDir     = Join-Path $runRoot 'log'
$manifestPath = Join-Path $WorkRoot 'manifests\latest.json'
$stampedManifest = Join-Path $WorkRoot ("manifests\deploy_{0}.json" -f $stamp)

Write-Output "=== DRONE-USEFUL-AREA-PILOT-001 / STAGING DEPLOY AND MIGRATE ==="
Write-Output "STAMP=$stamp"

# --- 1. Machine, directories, service --------------------------------------
Assert-PilotHost -Expected $ExpectedHost
Assert-PilotNotProduction -Path $WorkRoot -What 'work root'
Assert-PilotNotProduction -Path $BackupDir -What 'backup directory'
if (-not (Test-Path -LiteralPath $K.StagingRoot)) {
    throw "REFUSED: the staging checkout $($K.StagingRoot) does not exist."
}
foreach ($directory in @($runRoot, $evidence, $logDir,
                         (Join-Path $WorkRoot 'manifests'))) {
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
}

$runbook = Join-Path $K.StagingRoot 'docs\ORG_WINDOWS_SERVER_STAGING_RUNBOOK.md'
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

# --- 3. Backup BEFORE any change -------------------------------------------
Write-Output "--- backing up the staging database (SQLite online backup API) ---"
$backupTool = Join-Path $K.StagingRoot 'backup_transport_db.py'
$before = @(Get-ChildItem -LiteralPath $BackupDir -Filter '*.db' -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty FullName)
& $Python $backupTool --source $K.StagingDb --dest-dir $BackupDir --suffix pilot_before_useful_area
if ($LASTEXITCODE -ne 0) {
    throw "REFUSED: the staging backup failed with exit $LASTEXITCODE. NOTHING was changed."
}
$fresh = @(Get-ChildItem -LiteralPath $BackupDir -Filter '*.db' |
           Where-Object { $before -notcontains $_.FullName } |
           Sort-Object LastWriteTime -Descending)
if ($fresh.Count -lt 1) {
    throw "REFUSED: the backup reported success but no new file appeared in $BackupDir."
}
$backupPath = $fresh[0].FullName
$backupSha  = Get-PilotFileSha256 -Path $backupPath
Write-Output "BACKUP=$backupPath"
Write-Output "BACKUP_BYTES=$($fresh[0].Length)"
Write-Output "BACKUP_SHA256=$backupSha"

# --- 4. Fingerprint of area_ha, and the manifest, BEFORE the migration ------
$probe = Join-Path $PSScriptRoot 'pilot_db_probe.py'
$beforePath = Join-Path $evidence 'staging_before.json'
Invoke-PilotPython -Python $Python -StdoutPath $beforePath -Arguments @(
    $probe, 'snapshot', '--db', $K.StagingDb, '--day', $K.TargetDay,
    '--require', 'integrity') | Out-Null
$snapBefore = Read-PilotJson -Path $beforePath
Write-Output "STAGING_AREA_SHA256_BEFORE=$($snapBefore.payload.area_ha.sha256)"

# [REASON]: the manifest is written NOW, before the first change, and rewritten
# at the end. A run that dies between the backup and the end must still leave a
# rollback point behind -- a manifest written only on success is a manifest
# that never exists when it is needed.
function Save-Manifest([string]$phase, $extra) {
    $manifest = [ordered]@{
        kit             = 'DRONE-USEFUL-AREA-PILOT-001'
        evidence_kind   = 'deploy'
        phase           = $phase
        generated_utc   = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
        target_day      = $K.TargetDay
        verified_sha    = $K.VerifiedSha
        payload         = $extra
    }
    Write-PilotJson -Path $stampedManifest -Value $manifest | Out-Null
    return (Write-PilotJson -Path $manifestPath -Value $manifest)
}

$payload = [ordered]@{
    machine                 = $env:COMPUTERNAME
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
    area_ha_before          = $snapBefore.payload.area_ha
    area_ha_after           = $null
    area_ha_unchanged       = $false
    migration_on_staging_ok = $false
    smoke_test_status       = $null
    failures                = @()
}
Save-Manifest -phase 'backed-up' -extra $payload | Out-Null
Write-Output "MANIFEST=$manifestPath"

# --- 5. Fast-forward to the VERIFIED commit, and to nothing else -----------
Write-Output "--- fetching and fast-forwarding to $($K.VerifiedSha) ---"
Invoke-PilotGit -Repo $K.StagingRoot -Arguments @('fetch', 'origin', 'main') | Out-Null
$hasCommit = Invoke-PilotGit -Repo $K.StagingRoot -AllowFailure `
    -Arguments @('cat-file', '-e', ($K.VerifiedSha + '^{commit}'))
if ($hasCommit.ExitCode -ne 0) {
    throw "REFUSED: the verified commit $($K.VerifiedSha) is not in the staging checkout after fetching origin/main."
}
# [REASON]: `merge --ff-only <verified sha>`, NOT `pull origin main`. Fetching
# main and merging its tip would move staging to whatever was merged after
# this kit was reviewed. Fast-forwarding to the named commit refuses anything
# that is not a fast-forward and lands on exactly that tree.
Invoke-PilotGit -Repo $K.StagingRoot -Arguments @('merge', '--ff-only', $K.VerifiedSha) | Out-Null
Assert-PilotHeadIsVerified -Repo $K.StagingRoot
Assert-PilotWorktreeClean -Repo $K.StagingRoot
$shaAfter = Get-PilotHeadSha -Repo $K.StagingRoot
$payload.sha_after = $shaAfter
Save-Manifest -phase 'code-updated' -extra $payload | Out-Null
Write-Output "SHA_AFTER=$shaAfter"

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
$migrationLog = Join-Path $logDir 'migration.txt'
Write-Output "--- applying $($K.MigrationId) to staging ---"
Push-Location $K.StagingRoot
try {
    & $Python 'migrate_drones_useful_area_001.py' *> $migrationLog
    $migrationCode = $LASTEXITCODE
} finally {
    Pop-Location
}
Get-Content -LiteralPath $migrationLog | ForEach-Object { Write-Output "  $_" }

if ($migrationCode -ne 0) {
    $payload.failures = @('MIGRATION_FAILED')
    Save-Manifest -phase 'migration-failed' -extra $payload | Out-Null
    throw @"
REFUSED: the migration exited $migrationCode. The staging service is STILL STOPPED on purpose.

Manual recovery point:
  1. read $migrationLog
  2. roll staging back with the manifest:
     .\ops\pilot_useful_area_001\STAGING_ROLLBACK.ps1
  Backup: $backupPath
"@
}

# --- 9. Prove the migration and the untouched area_ha -----------------------
$afterPath = Join-Path $evidence 'staging_after.json'
& $Python $probe 'snapshot' '--db' $K.StagingDb '--day' $K.TargetDay `
    '--require' 'integrity' '--require' 'schema' `
    '--require' ("area-sha256=" + $snapBefore.payload.area_ha.sha256) > $afterPath
$probeCode = $LASTEXITCODE
$snapAfter = Read-PilotJson -Path $afterPath

$payload.area_ha_after = $snapAfter.payload.area_ha
$payload.area_ha_unchanged = ($snapAfter.payload.area_ha.sha256 -eq $snapBefore.payload.area_ha.sha256)

$failures = @()
if ($probeCode -ne 0) { $failures += 'PROBE_REQUIREMENTS_FAILED' }
if (-not $snapAfter.payload.integrity.integrity_ok) { $failures += 'INTEGRITY_CHECK_FAILED' }
if (-not $snapAfter.payload.schema.tables_all_present) { $failures += 'TABLES_MISSING' }
if (-not $snapAfter.payload.schema.indexes_all_present) { $failures += 'INDEXES_MISSING' }
if (-not $snapAfter.payload.schema.migration_registered) { $failures += 'REGISTRY_ROW_MISSING' }
if (-not $payload.area_ha_unchanged) { $failures += 'AREA_HA_CHANGED' }

Write-Output "STAGING_INTEGRITY=$($snapAfter.payload.integrity.integrity_ok)"
Write-Output "STAGING_AREA_SHA256_AFTER=$($snapAfter.payload.area_ha.sha256)"
Write-Output "STAGING_REGISTRY_ROWS=$($snapAfter.payload.schema.schema_migrations_rows)"

if ($failures.Count -gt 0) {
    $payload.failures = $failures
    Save-Manifest -phase 'verification-failed' -extra $payload | Out-Null
    throw @"
REFUSED after the migration: $($failures -join ', ').
The staging service is STILL STOPPED on purpose.

Manual recovery point:
  .\ops\pilot_useful_area_001\STAGING_ROLLBACK.ps1
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
    Save-Manifest -phase 'service-did-not-start' -extra $payload | Out-Null
    throw "REFUSED: $stagingService did not start within $ServiceTimeoutSeconds s. Backup: $backupPath"
}

Assert-PilotStagingUrl -Url $K.StagingUrl
$smokeStatus = $null
for ($attempt = 1; $attempt -le 10; $attempt++) {
    try {
        $response = Invoke-WebRequest -Uri $K.StagingUrl -UseBasicParsing -TimeoutSec 15
        $smokeStatus = [int]$response.StatusCode
        break
    } catch {
        if ($_.Exception.PSObject.Properties.Name -contains 'Response' -and $_.Exception.Response) {
            $smokeStatus = [int]$_.Exception.Response.StatusCode
            break
        }
        Start-Sleep -Seconds 3
    }
}
$payload.smoke_test_status = $smokeStatus
Write-Output "SMOKE_TEST_STATUS=$smokeStatus"
if ($null -eq $smokeStatus -or $smokeStatus -ge 500) {
    $payload.failures = @('SMOKE_TEST_FAILED')
    Save-Manifest -phase 'smoke-test-failed' -extra $payload | Out-Null
    throw "REFUSED: $($K.StagingUrl) did not answer (status '$smokeStatus'). Backup: $backupPath"
}

$manifestWritten = Save-Manifest -phase 'done' -extra $payload
Write-Output ""
Write-Output "MANIFEST=$manifestWritten"
Write-Output "BACKUP=$backupPath"
Write-Output "STAGING_DEPLOY_AND_MIGRATE=PASS"
Write-Output "Return to the owner: this console output, $manifestWritten and the exact BACKUP path above."
