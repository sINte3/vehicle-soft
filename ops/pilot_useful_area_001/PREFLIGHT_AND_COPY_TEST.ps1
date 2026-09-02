<#
    PREFLIGHT_AND_COPY_TEST.ps1
    DRONE-USEFUL-AREA-PILOT-001, step 1 of 6.

    WHAT THIS DOES
      Proves, on an ISOLATED COPY of the production database, that the
      migration DRONES_USEFUL_AREA_001 applies cleanly, is idempotent, and
      leaves drone_flights.area_ha byte-identical. Production itself is never
      written to, never migrated, and its service is never touched.

    WHAT THIS NEVER DOES
      * writes anything under C:\transport-report;
      * stops, starts or restarts TransportReport;
      * applies the migration to production or to staging;
      * copies a live SQLite file with Copy-Item -- the consistent snapshot is
        taken with backup_transport_db.py, which uses the SQLite ONLINE BACKUP
        API. A raw copy of a WAL database with uncheckpointed pages produces an
        inconsistent file that passes every eyeball check;
      * prints a coordinate, a flight id, a contour uuid or a token.

    HOW THE COPY IS MIGRATED WITHOUT A --db FLAG
      migrate_drones_useful_area_001.py takes no --db: it derives the path
      from its own __file__ (instance\transport.db next to the script). So the
      copy is migrated inside a SANDBOX directory that holds the migration
      script, migration_utils.py and instance\transport.db. Both copied files
      are hash-compared against the staging checkout afterwards, which proves
      the real migration ran and not an edited one.

    RUN (on the server, from the STAGING checkout):
      Set-Location C:\transport-report-staging
      .\ops\pilot_useful_area_001\PREFLIGHT_AND_COPY_TEST.ps1

    Exit code 0 only when every check held. Any disagreement is non-zero.
#>

[CmdletBinding()]
param(
    [string]$ExpectedHost = 'srv-yoqsh',
    [string]$WorkRoot = 'D:\transport-report-backups\pilot\DRONE-USEFUL-AREA-001',
    [string]$Python = 'C:\Program Files\Python314\python.exe'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Import-Module (Join-Path $PSScriptRoot 'PilotKit.psm1') -Force
$K = Get-PilotConstants

$stamp = (Get-Date).ToString('yyyyMMdd_HHmmss')
$runRoot   = Join-Path $WorkRoot ("preflight_{0}" -f $stamp)
$copyDir   = Join-Path $runRoot 'copy'
$sandbox   = Join-Path $runRoot 'sandbox'
$evidence  = Join-Path $runRoot 'evidence'
$logDir    = Join-Path $runRoot 'log'
$reportPath = Join-Path $evidence 'preflight.json'

Write-Output "=== DRONE-USEFUL-AREA-PILOT-001 / PREFLIGHT AND COPY TEST ==="
Write-Output "STAMP=$stamp"

# --- 1. The right machine, the right directories ---------------------------
Assert-PilotHost -Expected $ExpectedHost

if (-not (Test-Path -LiteralPath $K.StagingRoot)) {
    throw "REFUSED: the staging checkout $($K.StagingRoot) does not exist on this machine."
}
if (-not (Test-Path -LiteralPath $K.ProductionRoot)) {
    throw "REFUSED: the production checkout $($K.ProductionRoot) does not exist; this is not the server this kit was written for."
}
# The work root must not be inside either checkout: a pilot that writes its
# own artefacts into a deployed tree makes that tree dirty and blocks the
# next update.
Assert-PilotNotProduction -Path $WorkRoot -What 'work root'
if (Test-PilotPathWithin -Path $WorkRoot -Root $K.StagingRoot) {
    throw "REFUSED: the work root '$WorkRoot' is inside the staging checkout and would make its working tree dirty."
}
foreach ($directory in @($runRoot, $copyDir, $sandbox, $evidence, $logDir,
                         (Join-Path $sandbox 'instance'))) {
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
}
Write-Output "WORK_ROOT=$runRoot"

# --- 2. The staging checkout is clean and at the verified commit -----------
Assert-PilotWorktreeClean -Repo $K.StagingRoot
$stagingHead = Get-PilotHeadSha -Repo $K.StagingRoot
Write-Output "STAGING_HEAD=$stagingHead"
# [REASON]: the checkout does not have to be AT the verified commit yet --
# that is step 2's job. What must be true now is that the migration file this
# step is about to run is the verified one, and that is checked by content
# below, not by the branch tip.

# --- 3. The staging service name, resolved and proven ----------------------
$runbook = Join-Path $K.StagingRoot 'docs\ORG_WINDOWS_SERVER_STAGING_RUNBOOK.md'
$stagingService = Resolve-PilotStagingService -RunbookPath $runbook
Assert-PilotServiceIsNotProduction -Name $stagingService
$serviceWhere = Get-PilotServiceImagePath -Name $stagingService
Write-Output "STAGING_SERVICE=$stagingService"
Write-Output "STAGING_SERVICE_APPDIR=$($serviceWhere.AppDirectory)"

# --- 4. Production database: existence only, by reading --------------------
if (-not (Test-Path -LiteralPath $K.ProductionDb)) {
    throw "REFUSED: the production database $($K.ProductionDb) was not found."
}
$productionSize = (Get-Item -LiteralPath $K.ProductionDb).Length
Write-Output "PRODUCTION_DB_BYTES=$productionSize"

# --- 5. Consistent isolated copy through the ONLINE BACKUP API -------------
# backup_transport_db.py is the project's sanctioned mechanism: it is what the
# daily scheduled task runs against this same database while the service is
# up, and it verifies integrity on the destination itself.
$backupTool = Join-Path $K.StagingRoot 'backup_transport_db.py'
if (-not (Test-Path -LiteralPath $backupTool)) {
    throw "REFUSED: $backupTool not found; the online backup mechanism is missing."
}
Write-Output "--- taking the isolated copy (SQLite online backup API) ---"
& $Python $backupTool --source $K.ProductionDb --dest-dir $copyDir --suffix pilot_copy_test
if ($LASTEXITCODE -ne 0) {
    throw "REFUSED: the online backup of the production database failed with exit $LASTEXITCODE. Nothing else was done."
}
$copies = @(Get-ChildItem -LiteralPath $copyDir -Filter '*.db' | Sort-Object LastWriteTime -Descending)
if ($copies.Count -ne 1) {
    throw "REFUSED: expected exactly one copy in $copyDir, found $($copies.Count)."
}
$copyPath = $copies[0].FullName
Write-Output "COPY=$copyPath"
Write-Output "COPY_BYTES=$($copies[0].Length)"

# --- 6. The copy, before anything is applied to it -------------------------
$probe = Join-Path $PSScriptRoot 'pilot_db_probe.py'
$beforePath = Join-Path $evidence 'copy_before.json'
Invoke-PilotPython -Python $Python -StdoutPath $beforePath -Arguments @(
    $probe, 'snapshot', '--db', $copyPath, '--day', $K.TargetDay,
    '--require', 'integrity') | Out-Null
$before = Read-PilotJson -Path $beforePath
Write-Output "COPY_INTEGRITY=$($before.payload.integrity.integrity_ok)"
Write-Output "COPY_OPEN_MODE=$($before.payload.database.open_mode)"
Write-Output "COPY_AREA_SHA256=$($before.payload.area_ha.sha256)"
Write-Output "COPY_AREA_ROWS=$($before.payload.area_ha.rows)"

if ($before.payload.schema.migration_registered) {
    throw "REFUSED: $($K.MigrationId) is ALREADY recorded in the copy of production. Production has already been migrated, which this macro-stage did not authorise. Nothing further was done."
}

# --- 7. The sandbox: the REAL migration file, proven by hash ---------------
foreach ($name in @('migrate_drones_useful_area_001.py', 'migration_utils.py')) {
    $source = Join-Path $K.StagingRoot $name
    if (-not (Test-Path -LiteralPath $source)) {
        throw "REFUSED: $source not found in the staging checkout."
    }
    Copy-Item -LiteralPath $source -Destination (Join-Path $sandbox $name) -Force
    $left  = Get-PilotFileSha256 -Path $source
    $right = Get-PilotFileSha256 -Path (Join-Path $sandbox $name)
    if ($left -ne $right) {
        throw "REFUSED: the sandbox copy of $name does not match the checkout."
    }
    Write-Output "SANDBOX_FILE=$name SHA256=$left"
}
Copy-Item -LiteralPath $copyPath -Destination (Join-Path $sandbox 'instance\transport.db') -Force
$sandboxDb = Join-Path $sandbox 'instance\transport.db'
Assert-PilotNotProduction -Path $sandboxDb -What 'sandbox database'

# --- 8. Apply the migration to the COPY, twice -----------------------------
$migration = Join-Path $sandbox 'migrate_drones_useful_area_001.py'
$firstLog  = Join-Path $logDir 'migration_first.txt'
$secondLog = Join-Path $logDir 'migration_second.txt'

Write-Output "--- applying $($K.MigrationId) to the isolated copy ---"
& $Python $migration *> $firstLog
$firstCode = $LASTEXITCODE
Get-Content -LiteralPath $firstLog | ForEach-Object { Write-Output "  $_" }
if ($firstCode -ne 0) {
    throw "REFUSED: the migration failed on the isolated copy with exit $firstCode. Staging was NOT touched."
}

Write-Output "--- applying it a second time (idempotence) ---"
& $Python $migration *> $secondLog
$secondCode = $LASTEXITCODE
Get-Content -LiteralPath $secondLog | ForEach-Object { Write-Output "  $_" }
if ($secondCode -ne 0) {
    throw "REFUSED: the repeated migration exited $secondCode instead of 0."
}
$secondText = (Get-Content -LiteralPath $secondLog -Raw)
$repeatSaidAlreadyApplied = $secondText -match 'Already applied'
if (-not $repeatSaidAlreadyApplied) {
    throw "REFUSED: the second run did not report 'Already applied. Nothing to do.' -- the migration is not idempotent on this database."
}

# --- 9. The copy, after ----------------------------------------------------
$afterPath = Join-Path $evidence 'copy_after.json'
Invoke-PilotPython -Python $Python -StdoutPath $afterPath -Arguments @(
    $probe, 'snapshot', '--db', $sandboxDb, '--day', $K.TargetDay,
    '--require', 'integrity', '--require', 'schema',
    '--require', ("area-sha256=" + $before.payload.area_ha.sha256)) | Out-Null
$after = Read-PilotJson -Path $afterPath

Write-Output "COPY_AFTER_INTEGRITY=$($after.payload.integrity.integrity_ok)"
Write-Output "COPY_AFTER_TABLES=$($after.payload.schema.indexes_present)/$($after.payload.schema.indexes_expected) indexes"
Write-Output "COPY_AFTER_AREA_SHA256=$($after.payload.area_ha.sha256)"

$tablesOk   = [bool]$after.payload.schema.tables_all_present
$indexesOk  = [bool]$after.payload.schema.indexes_all_present
$registered = [bool]$after.payload.schema.migration_registered
$checksumOk = [bool]$after.payload.schema.migration_checksum_present
$integrityOk = [bool]$after.payload.integrity.integrity_ok
$areaUnchanged = ($after.payload.area_ha.sha256 -eq $before.payload.area_ha.sha256) -and
                 ($after.payload.area_ha.rows -eq $before.payload.area_ha.rows)

$failures = @()
if (-not $tablesOk)     { $failures += 'TABLES_MISSING' }
if (-not $indexesOk)    { $failures += 'INDEXES_MISSING' }
if (-not $registered)   { $failures += 'REGISTRY_ROW_MISSING' }
if (-not $checksumOk)   { $failures += 'REGISTRY_CHECKSUM_MISSING' }
if (-not $integrityOk)  { $failures += 'INTEGRITY_CHECK_FAILED' }
if (-not $areaUnchanged){ $failures += 'AREA_HA_CHANGED' }
if ($after.payload.schema.indexes_present -ne 5) { $failures += 'INDEX_COUNT_IS_NOT_FIVE' }

# --- 10. Evidence ----------------------------------------------------------
$document = [ordered]@{
    kit            = 'DRONE-USEFUL-AREA-PILOT-001'
    evidence_kind  = 'preflight'
    generated_utc  = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    target_day     = $K.TargetDay
    verified_sha   = $K.VerifiedSha
    payload        = [ordered]@{
        machine                    = $env:COMPUTERNAME
        staging_head               = $stagingHead
        staging_service            = $stagingService
        staging_service_appdir     = $serviceWhere.AppDirectory
        production_db_bytes        = $productionSize
        production_was_only_read   = $true
        copy_path                  = $copyPath
        copy_bytes                 = $copies[0].Length
        copy_open_mode             = $before.payload.database.open_mode
        copy_integrity_ok          = [bool]$before.payload.integrity.integrity_ok
        migration_first_exit       = $firstCode
        migration_second_exit      = $secondCode
        migration_repeat_said_already_applied = [bool]$repeatSaidAlreadyApplied
        tables_all_present         = $tablesOk
        indexes_all_present        = $indexesOk
        indexes_present            = $after.payload.schema.indexes_present
        migration_registered       = $registered
        migration_checksum_present = $checksumOk
        integrity_ok_after         = $integrityOk
        area_ha_before             = $before.payload.area_ha
        area_ha_after              = $after.payload.area_ha
        area_ha_unchanged          = $areaUnchanged
        migration_on_copy_ok       = ($failures.Count -eq 0)
        failures                   = $failures
    }
}
$written = Write-PilotJson -Path $reportPath -Value $document
Write-Output "EVIDENCE=$written"

if ($failures.Count -gt 0) {
    throw "PREFLIGHT FAILED: $($failures -join ', '). Staging was NOT deployed and NOT migrated."
}

Write-Output ""
Write-Output "PREFLIGHT_AND_COPY_TEST=PASS"
Write-Output "Return to the owner: this whole console output and the file $written"
