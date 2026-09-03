<#
    PREFLIGHT_AND_COPY_TEST.ps1
    DRONE-USEFUL-AREA-PILOT-001, step 1 of 6. CREATES THE RUN.

    WHAT THIS DOES
      Opens the run -- one identifier, one directory, one manifest -- and then
      proves, on an ISOLATED COPY of the production database, that the
      migration DRONES_USEFUL_AREA_001 applies cleanly, is idempotent, and
      leaves drone_flights.area_ha byte-identical.

    WHERE THIS RUNS FROM
      The KIT checkout (C:\vehicle-soft-pilot-kit), which is a separate clone
      and is never the repository being deployed or rolled back. Two revisions
      matter and they are not the same:

        * PRODUCT_SHA -- the verified product revision the staging checkout
          must be at, and the revision the migration is materialized FROM;
        * KIT_SHA -- the kit's own revision, MEASURED here at the kit
          checkout and written into every piece of evidence. It cannot be a
          constant: the kit lives in the commit that creates it.

    WHAT THIS NEVER DOES
      * writes anything under C:\transport-report;
      * stops, starts or restarts TransportReport;
      * applies the migration to production or to staging;
      * copies a live SQLite file with Copy-Item -- the consistent snapshot is
        taken with backup_transport_db.py, which uses the SQLite ONLINE BACKUP
        API. A raw copy of a WAL database with uncheckpointed pages produces an
        inconsistent file that passes every eyeball check;
      * copies an executable from the working tree. Every file it runs is
        MATERIALIZED from a git blob of PRODUCT_SHA and its hash checked
        against PRODUCT_BLOBS.json. A copy that matches the working tree
        proves the copying was careful and nothing else.

    RUN (on the server, from the KIT checkout):
      Set-Location C:\vehicle-soft-pilot-kit
      .\ops\pilot_useful_area_001\PREFLIGHT_AND_COPY_TEST.ps1

    It prints RUN_ID=... Every later step takes that identifier.
#>

[CmdletBinding()]
param(
    [string]$ExpectedHost = 'srv-yoqsh',
    [string]$RunsRoot = 'D:\transport-report-backups\pilot\DRONE-USEFUL-AREA-001\runs',
    [string]$Python = 'C:\Program Files\Python314\python.exe'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Import-Module (Join-Path $PSScriptRoot 'PilotKit.psm1') -Force
$K = Get-PilotConstants
$KitCheckout = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

Write-Output "=== DRONE-USEFUL-AREA-PILOT-001 / PREFLIGHT AND COPY TEST ==="

# --- 1. The right machine, the right checkouts ------------------------------
Assert-PilotHost -Expected $ExpectedHost

if (-not (Test-Path -LiteralPath $K.StagingRoot)) {
    throw "REFUSED: the staging checkout $($K.StagingRoot) does not exist on this machine."
}
if (-not (Test-Path -LiteralPath $K.ProductionRoot)) {
    throw "REFUSED: the production checkout $($K.ProductionRoot) does not exist; this is not the server this kit was written for."
}
Assert-PilotOutsideCheckouts -Path $RunsRoot -What 'runs root'

# --- 2. Open the run --------------------------------------------------------
$run = New-PilotRun -RunsRoot $RunsRoot -Python $Python `
                    -KitScriptRoot $PSScriptRoot -KitCheckout $KitCheckout
$RunId = $run.run_id
$KitSha = $run.kit_sha
Write-Output "RUN_ID=$RunId"
Write-Output "KIT_SHA=$KitSha   (measured at $KitCheckout)"
Write-Output "PRODUCT_SHA=$($K.ProductSha)"
Write-Output "RUN_ROOT=$($run.run_root)"

$runRoot   = $run.run_root
$copyDir   = Join-Path $runRoot 'copy'
$sandbox   = Join-Path $runRoot 'sandbox'
$logDir    = Join-Path $runRoot 'log'
New-Item -ItemType Directory -Path (Join-Path $sandbox 'instance') -Force | Out-Null

$probe   = Join-Path $PSScriptRoot 'pilot_db_probe.py'
$repoTool = Join-Path $PSScriptRoot 'pilot_repo_check.py'

# --- 3. The staging checkout is clean and at the PRODUCT revision -----------
$stagingCheck = Invoke-PilotProbe -Python $Python -Script $repoTool -RunId $RunId -KitSha $KitSha `
    -OutFile (Get-PilotEvidencePath -RunsRoot $RunsRoot -RunId $RunId -Name 'repo_staging') `
    -Arguments @('verify', '--repo', $K.StagingRoot, '--expect-sha', $K.ProductSha, '--role', 'product')
Write-Output "STAGING_HEAD=$($stagingCheck.Evidence.payload.head_sha)"
Write-Output "STAGING_BLOBS_MATCH=$($stagingCheck.Evidence.payload.blobs_all_match)"

$kitCheck = Invoke-PilotProbe -Python $Python -Script $repoTool -RunId $RunId -KitSha $KitSha `
    -OutFile (Get-PilotEvidencePath -RunsRoot $RunsRoot -RunId $RunId -Name 'repo_kit') `
    -Arguments @('verify', '--repo', $KitCheckout, '--expect-sha', $KitSha, '--role', 'kit')
Write-Output "KIT_BLOBS_MATCH=$($kitCheck.Evidence.payload.blobs_all_match)"

# --- 4. The staging service name, resolved and proven ----------------------
$runbook = Join-Path $KitCheckout 'docs\ORG_WINDOWS_SERVER_STAGING_RUNBOOK.md'
$stagingService = Resolve-PilotStagingService -RunbookPath $runbook
Assert-PilotServiceIsNotProduction -Name $stagingService
$serviceWhere = Get-PilotServiceImagePath -Name $stagingService
Write-Output "STAGING_SERVICE=$stagingService"
Write-Output "STAGING_SERVICE_APPDIR=$($serviceWhere.AppDirectory)"

# --- 5. Production database: existence only, by reading --------------------
if (-not (Test-Path -LiteralPath $K.ProductionDb)) {
    throw "REFUSED: the production database $($K.ProductionDb) was not found."
}
$productionSize = (Get-Item -LiteralPath $K.ProductionDb).Length
Write-Output "PRODUCTION_DB_BYTES=$productionSize"

# --- 6. Materialize the executables from PRODUCT_SHA ------------------------
$materialized = Invoke-PilotProbe -Python $Python -Script $repoTool -RunId $RunId -KitSha $KitSha `
    -OutFile (Get-PilotEvidencePath -RunsRoot $RunsRoot -RunId $RunId -Name 'materialize') `
    -Arguments @('materialize', '--repo', $K.StagingRoot, '--rev', $K.ProductSha,
                 '--into', $sandbox,
                 '--file', 'migrate_drones_useful_area_001.py',
                 '--file', 'migration_utils.py',
                 '--file', 'backup_transport_db.py')
foreach ($property in $materialized.Evidence.payload.materialized.PSObject.Properties) {
    Write-Output "MATERIALIZED $($property.Name) blob=$($property.Value.blob)"
}
$backupTool = Join-Path $sandbox 'backup_transport_db.py'
$migration  = Join-Path $sandbox 'migrate_drones_useful_area_001.py'

# --- 7. Consistent isolated copy through the ONLINE BACKUP API -------------
Write-Output "--- taking the isolated copy (SQLite online backup API) ---"
Invoke-PilotPython -Python $Python -Arguments @(
    $backupTool, '--source', $K.ProductionDb, '--dest-dir', $copyDir,
    '--suffix', 'pilot_copy_test') | Out-Null
$copies = @(Get-ChildItem -LiteralPath $copyDir -Filter '*.db' | Sort-Object LastWriteTime -Descending)
if ($copies.Count -ne 1) {
    throw "REFUSED: expected exactly one copy in $copyDir, found $($copies.Count)."
}
$copyPath = $copies[0].FullName
Write-Output "COPY=$copyPath"
Write-Output "COPY_BYTES=$($copies[0].Length)"

# --- 8. The copy, before anything is applied to it -------------------------
$beforePath = Join-Path $runRoot 'evidence\copy_before.json'
$before = (Invoke-PilotProbe -Python $Python -Script $probe -RunId $RunId -KitSha $KitSha `
    -OutFile $beforePath `
    -Arguments @('snapshot', '--db', $copyPath, '--day', $K.TargetDay,
                 '--require', 'integrity')).Evidence
Write-Output "COPY_INTEGRITY=$($before.payload.integrity.integrity_ok)"
Write-Output "COPY_OPEN_MODE=$($before.payload.database.open_mode)"
Write-Output "COPY_AREA_SHA256=$($before.payload.area_ha.sha256)"

if ($before.payload.schema.migration_registered) {
    throw "REFUSED: $($K.MigrationId) is ALREADY recorded in the copy of production. Production has already been migrated, which this macro-stage did not authorise. Nothing further was done."
}

# --- 9. Apply the migration to the COPY, twice -----------------------------
Copy-Item -LiteralPath $copyPath -Destination (Join-Path $sandbox 'instance\transport.db') -Force
$sandboxDb = Join-Path $sandbox 'instance\transport.db'
Assert-PilotNotProduction -Path $sandboxDb -What 'sandbox database'

$firstLog  = Join-Path $logDir 'migration_first.txt'
$secondLog = Join-Path $logDir 'migration_second.txt'

Write-Output "--- applying $($K.MigrationId) to the isolated copy ---"
$firstCode = Invoke-PilotPython -Python $Python -Arguments @($migration) -PassThruExitCode
Write-Output "MIGRATION_FIRST_EXIT=$firstCode"
if ($firstCode -ne 0) {
    throw "REFUSED: the migration failed on the isolated copy with exit $firstCode. Staging was NOT touched."
}

Write-Output "--- applying it a second time (idempotence) ---"
$secondOutput = & $Python $migration 2>&1
$secondCode = $LASTEXITCODE
$secondText = ($secondOutput | Out-String)
[System.IO.File]::WriteAllText($secondLog, $secondText, (New-Object System.Text.UTF8Encoding($false)))
Write-Output ("  " + $secondText.Trim())
if ($secondCode -ne 0) {
    throw "REFUSED: the repeated migration exited $secondCode instead of 0."
}
$repeatSaidAlreadyApplied = $secondText -match 'Already applied'
if (-not $repeatSaidAlreadyApplied) {
    throw "REFUSED: the second run did not report 'Already applied. Nothing to do.' -- the migration is not idempotent on this database."
}

# --- 10. The copy, after ---------------------------------------------------
$afterPath = Join-Path $runRoot 'evidence\copy_after.json'
$afterRun = Invoke-PilotProbe -Python $Python -Script $probe -RunId $RunId -KitSha $KitSha `
    -OutFile $afterPath -AllowedExitCodes @(0, 3) `
    -Arguments @('snapshot', '--db', $sandboxDb, '--day', $K.TargetDay,
                 '--require', 'integrity', '--require', 'schema',
                 '--require', ("area-sha256=" + $before.payload.area_ha.sha256))
$after = $afterRun.Evidence

Write-Output "COPY_AFTER_INTEGRITY=$($after.payload.integrity.integrity_ok)"
Write-Output "COPY_AFTER_INDEXES=$($after.payload.schema.indexes_present)/$($after.payload.schema.indexes_expected)"
Write-Output "COPY_AFTER_AREA_SHA256=$($after.payload.area_ha.sha256)"

$tablesOk    = [bool]$after.payload.schema.tables_all_present
$indexesOk   = [bool]$after.payload.schema.indexes_all_present
$registered  = [bool]$after.payload.schema.migration_registered
$checksumOk  = [bool]$after.payload.schema.migration_checksum_present
$integrityOk = [bool]$after.payload.integrity.integrity_ok
$areaUnchanged = ($after.payload.area_ha.sha256 -eq $before.payload.area_ha.sha256) -and
                 ($after.payload.area_ha.rows -eq $before.payload.area_ha.rows)

$failures = @()
if ($afterRun.ExitCode -ne 0) { $failures += 'PROBE_REQUIREMENTS_FAILED' }
if (-not $tablesOk)     { $failures += 'TABLES_MISSING' }
if (-not $indexesOk)    { $failures += 'INDEXES_MISSING' }
if (-not $registered)   { $failures += 'REGISTRY_ROW_MISSING' }
if (-not $checksumOk)   { $failures += 'REGISTRY_CHECKSUM_MISSING' }
if (-not $integrityOk)  { $failures += 'INTEGRITY_CHECK_FAILED' }
if (-not $areaUnchanged){ $failures += 'AREA_HA_CHANGED' }
if ($after.payload.schema.indexes_present -ne 5) { $failures += 'INDEX_COUNT_IS_NOT_FIVE' }
if (-not $stagingCheck.Evidence.payload.passed) { $failures += 'STAGING_CHECKOUT_NOT_VERIFIED' }
if (-not $kitCheck.Evidence.payload.passed) { $failures += 'KIT_CHECKOUT_NOT_VERIFIED' }

# --- 11. Evidence and the run manifest -------------------------------------
$payload = [ordered]@{
    machine                    = $env:COMPUTERNAME
    kit_checkout               = $KitCheckout
    staging_head               = $stagingCheck.Evidence.payload.head_sha
    staging_blobs_all_match    = [bool]$stagingCheck.Evidence.payload.blobs_all_match
    kit_blobs_all_match        = [bool]$kitCheck.Evidence.payload.blobs_all_match
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
$envelope = [ordered]@{
    kit           = 'DRONE-USEFUL-AREA-PILOT-001'
    kit_version   = '2'
    evidence_kind = 'preflight'
    run_id        = $RunId
    kit_sha       = $KitSha
    product_sha   = $K.ProductSha
    generated_utc = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    target_day    = $K.TargetDay
    payload       = $payload
}
$evidencePath = Get-PilotEvidencePath -RunsRoot $RunsRoot -RunId $RunId -Name 'preflight'
Write-PilotJson -Path $evidencePath -Value $envelope | Out-Null
Set-PilotRunStep -RunsRoot $RunsRoot -RunId $RunId -Step 'preflight' -Value ([ordered]@{
    completed_utc = $envelope.generated_utc
    passed        = ($failures.Count -eq 0)
    evidence      = $evidencePath
}) | Out-Null
Write-Output "EVIDENCE=$evidencePath"

if ($failures.Count -gt 0) {
    throw "PREFLIGHT FAILED: $($failures -join ', '). Staging was NOT deployed and NOT migrated."
}

Write-Output ""
Write-Output "PREFLIGHT_AND_COPY_TEST=PASS"
Write-Output "RUN_ID=$RunId"
Write-Output "Every later step of this pilot takes -RunId $RunId"
