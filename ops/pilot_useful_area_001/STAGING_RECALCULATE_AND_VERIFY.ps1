<#
    STAGING_RECALCULATE_AND_VERIFY.ps1
    DRONE-USEFUL-AREA-PILOT-001, step 4 of 6. STAGING ONLY.

    WHAT THIS DOES
      Checks that staging actually holds accepted routes for the target day
      and for no other day, runs tools/recalculate_drone_useful_area.py first
      as --dry-run, refuses to apply if anything is wrong with the input, then
      applies, then applies AGAIN and proves the repeat wrote nothing:
      inserted=0, updated=0, deleted=0 and every row counted as unchanged.

      It measures how long the applying run takes, proves
      drone_flights.area_ha did not move across all three runs, and proves
      that only READY_ESTIMATE works carry a number.

    WHAT THIS NEVER DOES
      * touches C:\transport-report, its database or its service;
      * applies anything after a dry run that reported a problem;
      * recalculates a period other than the target day.

    RUN (on the server):
      Set-Location C:\transport-report-staging
      .\ops\pilot_useful_area_001\STAGING_RECALCULATE_AND_VERIFY.ps1
#>

[CmdletBinding()]
param(
    [string]$ExpectedHost = 'srv-yoqsh',
    [string]$WorkRoot = 'D:\transport-report-backups\pilot\DRONE-USEFUL-AREA-001',
    [string]$Python = 'C:\Program Files\Python314\python.exe',
    [string]$Day
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Import-Module (Join-Path $PSScriptRoot 'PilotKit.psm1') -Force
$K = Get-PilotConstants
if (-not $Day) { $Day = $K.TargetDay }

$stamp    = (Get-Date).ToString('yyyyMMdd_HHmmss')
$runRoot  = Join-Path $WorkRoot ("recalc_{0}" -f $stamp)
$evidence = Join-Path $runRoot 'evidence'
$logDir   = Join-Path $runRoot 'log'

Write-Output "=== DRONE-USEFUL-AREA-PILOT-001 / STAGING RECALCULATE AND VERIFY ==="
Write-Output "STAMP=$stamp"
Write-Output "DAY=$Day"

# --- 1. Machine and staging only --------------------------------------------
Assert-PilotHost -Expected $ExpectedHost
Assert-PilotStagingPath -Path $K.StagingDb -What 'database'
Assert-PilotNotProduction -Path $WorkRoot -What 'work root'
if (-not (Test-Path -LiteralPath $K.StagingDb)) {
    throw "REFUSED: the staging database $($K.StagingDb) was not found."
}
foreach ($directory in @($runRoot, $evidence, $logDir)) {
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
}

$probe = Join-Path $PSScriptRoot 'pilot_db_probe.py'
$parser = Join-Path $PSScriptRoot 'pilot_recalc_parse.py'
$tool = Join-Path $K.StagingRoot 'tools\recalculate_drone_useful_area.py'
if (-not (Test-Path -LiteralPath $tool)) {
    throw "REFUSED: $tool not found. Deploy staging first (step 2)."
}

# --- 2. What is actually in staging for that day -----------------------------
$inputPath = Join-Path $evidence 'staging_input.json'
& $Python $probe 'snapshot' '--db' $K.StagingDb '--day' $Day `
    '--require' 'integrity' '--require' 'schema' '--require' 'no-off-day-routes' > $inputPath
$inputCode = $LASTEXITCODE
$inputState = Read-PilotJson -Path $inputPath

Write-Output "FLIGHTS_OF_DAY=$($inputState.payload.routes.flights_of_target_day)"
Write-Output "ROUTES_OF_DAY=$($inputState.payload.routes.routes_of_target_day)"
Write-Output "ROUTES_OUTSIDE_DAY=$($inputState.payload.routes.routes_outside_target_day)"
Write-Output "AREA_SHA256_BEFORE=$($inputState.payload.area_ha.sha256)"

if ($inputCode -ne 0) {
    throw "REFUSED: the staging database did not satisfy the input checks (integrity, schema, no off-day routes). See $inputPath. Nothing was recalculated."
}
if ($inputState.payload.routes.routes_of_target_day -le 0) {
    throw "REFUSED: staging holds no accepted route for $Day. Run step 3 on BAK-TEX11 first. Nothing was recalculated."
}
if ($inputState.payload.routes.routes_outside_target_day -ne 0) {
    throw "REFUSED: $($inputState.payload.routes.routes_outside_target_day) accepted route(s) belong to a day other than $Day. The collection captured the wrong day; recalculating it would produce a number for a period nobody asked about."
}
$areaBefore = $inputState.payload.area_ha.sha256

# --- 3. Dry run --------------------------------------------------------------
Write-Output "--- dry run (writes nothing) ---"
$dryLog = Join-Path $logDir 'recalc_dry.txt'
Push-Location $K.StagingRoot
try {
    & $Python $tool '--from' $Day '--to' $Day '--dry-run' '--db' $K.StagingDb *> $dryLog
    $dryCode = $LASTEXITCODE
} finally {
    Pop-Location
}
Get-Content -LiteralPath $dryLog | ForEach-Object { Write-Output "  $_" }
if ($dryCode -ne 0) {
    throw "REFUSED: the dry run exited $dryCode. Nothing was applied."
}

$dryPath = Join-Path $evidence 'recalc_dry.json'
Invoke-PilotPython -Python $Python -StdoutPath (Join-Path $evidence 'recalc_dry_stdout.json') -Arguments @(
    $parser, '--input', $dryLog, '--label', 'dry-run', '--expect-day', $Day,
    '--out', $dryPath) | Out-Null
$dry = Read-PilotJson -Path $dryPath

# The dry run must have WRITTEN NOTHING. Proven by re-reading the database,
# not by trusting the flag: a tool that says "dry run" and writes anyway is
# exactly the failure this check exists for.
$afterDryPath = Join-Path $evidence 'staging_after_dry.json'
Invoke-PilotPython -Python $Python -StdoutPath $afterDryPath -Arguments @(
    $probe, 'snapshot', '--db', $K.StagingDb, '--day', $Day) | Out-Null
$afterDry = Read-PilotJson -Path $afterDryPath
if ($afterDry.payload.coverage.works -ne $inputState.payload.coverage.works) {
    throw "REFUSED: the dry run changed the number of stored works from $($inputState.payload.coverage.works) to $($afterDry.payload.coverage.works). It must write nothing."
}
if ($afterDry.payload.area_ha.sha256 -ne $areaBefore) {
    throw "REFUSED: drone_flights.area_ha changed during the DRY RUN."
}
Write-Output "DRY_RUN_WROTE_NOTHING=True"

$blockers = @()
if (-not $dry.payload.period_is_the_target_day) { $blockers += 'PERIOD_IS_NOT_THE_TARGET_DAY' }
if (-not $dry.payload.summary.status_total_matches_works) { $blockers += 'STATUS_COUNTS_DO_NOT_MATCH_WORKS' }
if ($dry.payload.summary.ROUTE_INVALID -ne 0) { $blockers += 'ROUTE_INVALID_PRESENT' }
if ($dry.payload.summary.works -le 0) { $blockers += 'NO_WORK_WAS_PRODUCED' }
if ($dry.payload.summary.algorithm_version -ne 'useful-area-v1') { $blockers += 'UNEXPECTED_ALGORITHM_VERSION' }

if ($blockers.Count -gt 0) {
    throw "REFUSED before --apply: $($blockers -join ', '). The dry run is the gate, and it did not open. Nothing was written."
}
Write-Output "DRY_RUN=PASS"

# --- 4. Apply -----------------------------------------------------------------
Write-Output "--- apply (one transaction) ---"
$applyLog = Join-Path $logDir 'recalc_apply_1.txt'
$watch = [System.Diagnostics.Stopwatch]::StartNew()
Push-Location $K.StagingRoot
try {
    & $Python $tool '--from' $Day '--to' $Day '--apply' '--db' $K.StagingDb *> $applyLog
    $applyCode = $LASTEXITCODE
} finally {
    Pop-Location
}
$watch.Stop()
$applySeconds = [math]::Round($watch.Elapsed.TotalSeconds, 3)
Get-Content -LiteralPath $applyLog | ForEach-Object { Write-Output "  $_" }
Write-Output "APPLY_SECONDS=$applySeconds"
if ($applyCode -ne 0) {
    throw "REFUSED: --apply exited $applyCode."
}

$applyPath = Join-Path $evidence 'recalc_apply_1.json'
Invoke-PilotPython -Python $Python -StdoutPath (Join-Path $evidence 'recalc_apply_1_stdout.json') -Arguments @(
    $parser, '--input', $applyLog, '--label', 'apply-1', '--expect-day', $Day,
    '--compare-with', $dryPath, '--out', $applyPath) | Out-Null
$apply1 = Read-PilotJson -Path $applyPath
Write-Output "DRY_RUN_AND_APPLY_AGREE=$($apply1.payload.outputs_agree)"
if (-not $apply1.payload.outputs_agree) {
    throw "REFUSED: the dry run and the apply disagree: $($apply1.payload.differences | ConvertTo-Json -Depth 5)"
}

# --- 5. Apply again: idempotence ------------------------------------------------
Write-Output "--- apply again (idempotence) ---"
$apply2Log = Join-Path $logDir 'recalc_apply_2.txt'
Push-Location $K.StagingRoot
try {
    & $Python $tool '--from' $Day '--to' $Day '--apply' '--db' $K.StagingDb *> $apply2Log
    $apply2Code = $LASTEXITCODE
} finally {
    Pop-Location
}
Get-Content -LiteralPath $apply2Log | ForEach-Object { Write-Output "  $_" }
if ($apply2Code -ne 0) {
    throw "REFUSED: the repeated --apply exited $apply2Code."
}

$apply2Path = Join-Path $evidence 'recalc_apply_2.json'
Invoke-PilotPython -Python $Python -StdoutPath (Join-Path $evidence 'recalc_apply_2_stdout.json') -Arguments @(
    $parser, '--input', $apply2Log, '--label', 'apply-2', '--expect-day', $Day,
    '--compare-with', $applyPath, '--out', $apply2Path) | Out-Null
$apply2 = Read-PilotJson -Path $apply2Path

$second = $apply2.payload.summary
Write-Output "SECOND_APPLY inserted=$($second.inserted) updated=$($second.updated) deleted=$($second.deleted) unchanged=$($second.unchanged) works=$($second.works)"

$idempotenceFailures = @()
if ($second.inserted -ne 0) { $idempotenceFailures += 'SECOND_APPLY_INSERTED_ROWS' }
if ($second.updated -ne 0) { $idempotenceFailures += 'SECOND_APPLY_UPDATED_ROWS' }
if ($second.deleted -ne 0) { $idempotenceFailures += 'SECOND_APPLY_DELETED_ROWS' }
if ($second.unchanged -ne $second.works) { $idempotenceFailures += 'SECOND_APPLY_DID_NOT_COUNT_EVERY_ROW_UNCHANGED' }
if (-not $apply2.payload.outputs_agree) { $idempotenceFailures += 'SECOND_APPLY_OUTPUTS_DIFFER' }

# --- 6. Final state ---------------------------------------------------------------
$finalPath = Join-Path $evidence 'staging_snapshot.json'
& $Python $probe 'snapshot' '--db' $K.StagingDb '--day' $Day `
    '--require' 'integrity' '--require' 'schema' '--require' 'only-ready-summed' `
    '--require' ("area-sha256=" + $areaBefore) > $finalPath
$finalCode = $LASTEXITCODE
$final = Read-PilotJson -Path $finalPath

Write-Output ""
Write-Output "--- staging after the recalculation ---"
Write-Output "works              : $($final.payload.coverage.works)"
Write-Output "READY_ESTIMATE     : $($final.payload.coverage.by_status.READY_ESTIMATE)"
Write-Output "PARTIAL_DATA       : $($final.payload.coverage.by_status.PARTIAL_DATA)"
Write-Output "DATA_UNAVAILABLE   : $($final.payload.coverage.by_status.DATA_UNAVAILABLE)"
Write-Output "CONTOUR_AMBIGUOUS  : $($final.payload.coverage.by_status.CONTOUR_AMBIGUOUS)"
Write-Output "CONTOUR_NOT_MATCHED: $($final.payload.coverage.by_status.CONTOUR_NOT_MATCHED)"
Write-Output "ROUTE_INVALID      : $($final.payload.coverage.by_status.ROUTE_INVALID)"
Write-Output "useful area (READY): $($final.payload.coverage.ready_useful_area_ha) ha"
Write-Output "DJI area           : $($final.payload.coverage.dji_area_ha) ha"
Write-Output "only READY numbered: $($final.payload.coverage.only_ready_carries_a_number)"
Write-Output "AREA_SHA256_AFTER  : $($final.payload.area_ha.sha256)"
Write-Output "rows outside day   : $($final.payload.coverage_rows_outside_target_day)"

$failures = @() + $idempotenceFailures
if ($finalCode -ne 0) { $failures += 'FINAL_PROBE_REQUIREMENTS_FAILED' }
if ($final.payload.area_ha.sha256 -ne $areaBefore) { $failures += 'AREA_HA_CHANGED' }
if (-not $final.payload.coverage.only_ready_carries_a_number) { $failures += 'A_NON_READY_WORK_CARRIES_A_NUMBER' }
if ($final.payload.coverage_rows_outside_target_day -ne 0) { $failures += 'COVERAGE_ROWS_OUTSIDE_THE_TARGET_DAY' }

$timing = [ordered]@{
    kit           = 'DRONE-USEFUL-AREA-PILOT-001'
    evidence_kind = 'recalc:timing'
    generated_utc = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    target_day    = $Day
    verified_sha  = $K.VerifiedSha
    payload       = [ordered]@{
        apply_seconds        = $applySeconds
        dry_run_wrote_nothing = $true
        failures             = $failures
    }
}
$timingPath = Write-PilotJson -Path (Join-Path $evidence 'recalc_timing.json') -Value $timing

Write-Output ""
Write-Output "EVIDENCE_DIR=$evidence"
Write-Output "RECALC_DRY=$dryPath"
Write-Output "RECALC_APPLY_1=$applyPath"
Write-Output "RECALC_APPLY_2=$apply2Path"
Write-Output "STAGING_SNAPSHOT=$finalPath"
Write-Output "TIMING=$timingPath"
Write-Output "APPLY_SECONDS=$applySeconds"

if ($failures.Count -gt 0) {
    throw "STAGING_RECALCULATE_AND_VERIFY=FAIL: $($failures -join ', ')"
}

Write-Output ""
Write-Output "STAGING_RECALCULATE_AND_VERIFY=PASS"
Write-Output "Now open $($K.StagingUrl)/drones/coverage?date_from=$Day&date_to=$Day and look at it."
Write-Output "Return to the owner: this console output and the five files above."
