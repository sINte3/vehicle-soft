<#
    STAGING_RECALCULATE_AND_VERIFY.ps1
    DRONE-USEFUL-AREA-PILOT-001, step 4 of 6. STAGING ONLY.

    WHAT THIS DOES, AND IN WHICH ORDER
      1. ACCEPTS AND VERIFIES THE COLLECT EVIDENCE. Before the dry run and
         far before --apply. A recalculation over a capture that was not
         complete produces a number for an input nobody can vouch for, and
         that number is indistinguishable from a good one once it is in the
         table. Every condition below must hold or nothing runs at all:
         collector exit 0, collect_live_confirmed, the operator answered, the
         drain completed, zero observation/capture/decode errors, zero
         responses dropped by the size cap, zero failed and zero pending route
         requests, matching id sets, a fully accepted batch, zero ingest
         errors, zero unlinked routes, zero envelopes left pending -- and the
         same day, the same run id, the same KIT_SHA and the same PRODUCT_SHA
         as this run.
      2. checks that staging holds accepted routes for the target day and for
         no other day;
      3. --dry-run, then proves it wrote nothing by comparing a FULL
         fingerprint of every row and every column of drone_coverage_works
         before and after -- a row count would not notice a rewritten row;
      4. --apply, then --apply again, proving the repeat wrote nothing:
         inserted=0, updated=0, deleted=0, every row counted as unchanged.

    WHAT THIS NEVER DOES
      * touches C:\transport-report, its database or its service;
      * applies anything after a dry run that reported a problem;
      * recalculates a period other than the target day.

    RUN (on the server, from the KIT checkout):
      Set-Location C:\vehicle-soft-pilot-kit
      .\ops\pilot_useful_area_001\STAGING_RECALCULATE_AND_VERIFY.ps1 -RunId ... -ApprovedKitSha ...
      (step 1 prints the exact command as NEXT_COMMAND_STEP4)
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$RunId,
    [Parameter(Mandatory)][string]$ApprovedKitSha,
    [string]$ExpectedHost = 'srv-yoqsh',
    [string]$RunsRoot = 'D:\transport-report-backups\pilot\DRONE-USEFUL-AREA-001\runs',
    [string]$Python = 'C:\Program Files\Python314\python.exe',
    [string]$Day
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Import-Module (Join-Path $PSScriptRoot 'PilotKit.psm1') -Force
$K = Get-PilotConstants
$KitCheckout = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not $Day) { $Day = $K.TargetDay }

Write-Output "=== DRONE-USEFUL-AREA-PILOT-001 / STAGING RECALCULATE AND VERIFY ==="
Write-Output "RUN_ID=$RunId"
Write-Output "DAY=$Day"

# --- 1. Machine, run, staging only ------------------------------------------
Assert-PilotHost -Expected $ExpectedHost
Assert-PilotStagingPath -Path $K.StagingDb -What 'database'
Assert-PilotOutsideCheckouts -Path $RunsRoot -What 'runs root'
if (-not (Test-Path -LiteralPath $K.StagingDb)) {
    throw "REFUSED: the staging database $($K.StagingDb) was not found."
}

$run = Get-PilotRun -RunsRoot $RunsRoot -RunId $RunId -ApprovedKitSha $ApprovedKitSha
$KitSha = Assert-PilotApprovedKitSha -KitCheckout $KitCheckout -ApprovedKitSha $ApprovedKitSha
if ($KitSha -ne $run.approved_kit_sha) {
    throw "REFUSED: this kit checkout is at $KitSha, the run was opened with $($run.approved_kit_sha)."
}
Write-Output "KIT_SHA=$KitSha"
$runRoot = $run.run_root
$logDir = Join-Path $runRoot 'log'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

$probe = Join-Path $PSScriptRoot 'pilot_db_probe.py'
$parser = Join-Path $PSScriptRoot 'pilot_recalc_parse.py'
$gate = Join-Path $PSScriptRoot 'pilot_collect_gate.py'
$tool = Join-Path $K.StagingRoot 'tools\recalculate_drone_useful_area.py'
if (-not (Test-Path -LiteralPath $tool)) {
    throw "REFUSED: $tool not found. Deploy staging first (step 2)."
}

# --- 2. THE COLLECT GATE. Nothing runs until this passes --------------------
$collectPath = Get-PilotEvidencePath -RunsRoot $RunsRoot -RunId $RunId -Name 'collect'
if (-not (Test-Path -LiteralPath $collectPath)) {
    throw @"
REFUSED: the collect evidence of this run is not here:
  $collectPath
Step 3 runs on BAK-TEX11 and prints the file to copy. Copy it to exactly that
path. Nothing was recalculated and nothing was written.
"@
}
$deployPath = Get-PilotEvidencePath -RunsRoot $RunsRoot -RunId $RunId -Name 'deploy'
$gateOut = Join-Path $runRoot 'evidence\collect_gate.json'
$gateCode = Invoke-PilotPython -Python $Python -PassThruExitCode -Arguments @(
    $gate, '--collect', $collectPath, '--deploy', $deployPath,
    '--run-id', $RunId, '--kit-sha', $KitSha, '--day', $Day, '--out', $gateOut)
$gateEvidence = Read-PilotJson -Path $gateOut
Write-Output "COLLECT_GATE_PASSED=$($gateEvidence.payload.passed)"
foreach ($reason in $gateEvidence.payload.reasons) { Write-Output "  - $reason" }
if ($gateCode -ne 0) {
    throw @"
REFUSED before the dry run: the live collection of this run is not a complete,
confirmed, fully accepted capture ($($gateEvidence.payload.reasons -join ', ')).

NOTHING was recalculated and NOTHING was written to the database. Repeat step 3
on BAK-TEX11; the route ingest is idempotent, so re-sending the whole batch is
safe.
"@
}
Write-Output "COLLECT_GATE=PASS"

# --- 3. What is actually in staging for that day -----------------------------
$inputPath = Get-PilotEvidencePath -RunsRoot $RunsRoot -RunId $RunId -Name 'staging_input'
$inputRun = Invoke-PilotProbe -Python $Python -Script $probe -RunId $RunId -KitSha $KitSha `
    -OutFile $inputPath -AllowedExitCodes @(0, 3) `
    -Arguments @('snapshot', '--db', $K.StagingDb, '--day', $Day,
                 '--require', 'integrity', '--require', 'schema',
                 '--require', 'no-off-day-routes')
$inputState = $inputRun.Evidence

Write-Output "FLIGHTS_OF_DAY=$($inputState.payload.routes.flights_of_target_day)"
Write-Output "ROUTES_OF_DAY=$($inputState.payload.routes.routes_of_target_day)"
Write-Output "ROUTES_OUTSIDE_DAY=$($inputState.payload.routes.routes_outside_target_day)"
Write-Output "AREA_SHA256_BEFORE=$($inputState.payload.area_ha.sha256)"
Write-Output "COVERAGE_SHA256_BEFORE=$($inputState.payload.coverage_fingerprint.sha256)"

if ($inputRun.ExitCode -ne 0) {
    throw "REFUSED: the staging database did not satisfy the input checks (integrity, schema, no off-day routes). See $inputPath. Nothing was recalculated."
}
if ($inputState.payload.routes.routes_of_target_day -le 0) {
    throw "REFUSED: staging holds no accepted route for $Day. Run step 3 on BAK-TEX11 first. Nothing was recalculated."
}
if ($inputState.payload.routes.routes_outside_target_day -ne 0) {
    throw "REFUSED: $($inputState.payload.routes.routes_outside_target_day) accepted route(s) belong to a day other than $Day. The collection captured the wrong day; recalculating it would produce a number for a period nobody asked about."
}
$areaBefore = $inputState.payload.area_ha.sha256
$coverageBefore = $inputState.payload.coverage_fingerprint.sha256

# --- 4. Dry run --------------------------------------------------------------
Write-Output "--- dry run (writes nothing) ---"
$dryLog = Join-Path $logDir 'recalc_dry.txt'
Push-Location $K.StagingRoot
try {
    # [REASON]: Invoke-PilotNative, never a bare `2>&1` -- see the module. A
    # warning on stderr must not end the step before its exit code is read.
    $dryRun = Invoke-PilotNative -FilePath $Python -Arguments @(
        $tool, '--from', $Day, '--to', $Day, '--dry-run', '--db', $K.StagingDb)
    $dryCode = $dryRun.ExitCode
} finally {
    Pop-Location
}
$dryText = $dryRun.Text
[System.IO.File]::WriteAllText($dryLog, $dryText, (New-Object System.Text.UTF8Encoding($false)))
Write-Output $dryText.TrimEnd()
if ($dryCode -ne 0) {
    throw "REFUSED: the dry run exited $dryCode. Nothing was applied."
}

# The dry run must have WRITTEN NOTHING. Proven by re-reading the database and
# comparing a FULL fingerprint -- every row, every column -- not by trusting
# the flag and not by counting rows.
$afterDryPath = Get-PilotEvidencePath -RunsRoot $RunsRoot -RunId $RunId -Name 'staging_after_dry'
$afterDryRun = Invoke-PilotProbe -Python $Python -Script $probe -RunId $RunId -KitSha $KitSha `
    -OutFile $afterDryPath -AllowedExitCodes @(0, 3) `
    -Arguments @('snapshot', '--db', $K.StagingDb, '--day', $Day,
                 '--require', ("coverage-sha256=" + $coverageBefore),
                 '--require', ("area-sha256=" + $areaBefore))
$afterDry = $afterDryRun.Evidence
$dryWroteNothing = ($afterDryRun.ExitCode -eq 0) -and
                   ($afterDry.payload.coverage_fingerprint.sha256 -eq $coverageBefore) -and
                   ($afterDry.payload.area_ha.sha256 -eq $areaBefore)
Write-Output "COVERAGE_SHA256_AFTER_DRY=$($afterDry.payload.coverage_fingerprint.sha256)"
Write-Output "DRY_RUN_WROTE_NOTHING=$dryWroteNothing"
if (-not $dryWroteNothing) {
    throw "REFUSED: the coverage table is not byte-identical after the DRY RUN. It must write nothing."
}

$dryPath = Get-PilotEvidencePath -RunsRoot $RunsRoot -RunId $RunId -Name 'recalc_dry'
Invoke-PilotPython -Python $Python -Arguments @(
    $parser, '--input', $dryLog, '--label', 'dry-run', '--expect-day', $Day,
    '--wrote-nothing', '--run-id', $RunId, '--kit-sha', $KitSha,
    '--out', $dryPath) | Out-Null
$dry = Read-PilotJson -Path $dryPath

$blockers = @()
if (-not $dry.payload.period_is_the_target_day) { $blockers += 'PERIOD_IS_NOT_THE_TARGET_DAY' }
if (-not $dry.payload.summary.status_total_matches_works) { $blockers += 'STATUS_COUNTS_DO_NOT_MATCH_WORKS' }
if ($dry.payload.summary.ROUTE_INVALID -ne 0) { $blockers += 'ROUTE_INVALID_PRESENT' }
if ($dry.payload.summary.works -le 0) { $blockers += 'NO_WORK_WAS_PRODUCED' }
# The rule the pilot validates. useful-area-v1 treated the simplified DJI polyline as
# telemetry sampling and dropped 69.8 % of the route as recording gaps (live pilot
# 2026-09-04); a dry run still reporting it means the staging checkout is not on the
# v2 product revision.
if ($dry.payload.summary.algorithm_version -ne 'useful-area-v2') { $blockers += 'UNEXPECTED_ALGORITHM_VERSION' }

if ($blockers.Count -gt 0) {
    throw "REFUSED before --apply: $($blockers -join ', '). The dry run is the gate, and it did not open. Nothing was written."
}
Write-Output "DRY_RUN=PASS"

# --- 5. Apply -----------------------------------------------------------------
Write-Output "--- apply (one transaction) ---"
$applyLog = Join-Path $logDir 'recalc_apply_1.txt'
$watch = [System.Diagnostics.Stopwatch]::StartNew()
Push-Location $K.StagingRoot
try {
    $applyRun = Invoke-PilotNative -FilePath $Python -Arguments @(
        $tool, '--from', $Day, '--to', $Day, '--apply', '--db', $K.StagingDb)
    $applyCode = $applyRun.ExitCode
} finally {
    Pop-Location
}
$watch.Stop()
$applySeconds = [math]::Round($watch.Elapsed.TotalSeconds, 3)
$applyText = $applyRun.Text
[System.IO.File]::WriteAllText($applyLog, $applyText, (New-Object System.Text.UTF8Encoding($false)))
Write-Output $applyText.TrimEnd()
Write-Output "APPLY_SECONDS=$applySeconds"
if ($applyCode -ne 0) {
    throw "REFUSED: --apply exited $applyCode."
}

$applyPath = Get-PilotEvidencePath -RunsRoot $RunsRoot -RunId $RunId -Name 'recalc_apply_1'
Invoke-PilotPython -Python $Python -Arguments @(
    $parser, '--input', $applyLog, '--label', 'apply-1', '--expect-day', $Day,
    '--compare-with', $dryPath, '--seconds', ([string]$applySeconds),
    '--run-id', $RunId, '--kit-sha', $KitSha, '--out', $applyPath) | Out-Null
$apply1 = Read-PilotJson -Path $applyPath
Write-Output "DRY_RUN_AND_APPLY_AGREE=$($apply1.payload.outputs_agree)"
if (-not $apply1.payload.outputs_agree) {
    throw "REFUSED: the dry run and the apply disagree: $($apply1.payload.differences | ConvertTo-Json -Depth 5)"
}

# --- 6. Apply again: idempotence ------------------------------------------------
Write-Output "--- apply again (idempotence) ---"
$apply2Log = Join-Path $logDir 'recalc_apply_2.txt'
Push-Location $K.StagingRoot
try {
    $apply2Run = Invoke-PilotNative -FilePath $Python -Arguments @(
        $tool, '--from', $Day, '--to', $Day, '--apply', '--db', $K.StagingDb)
    $apply2Code = $apply2Run.ExitCode
} finally {
    Pop-Location
}
$apply2Text = $apply2Run.Text
[System.IO.File]::WriteAllText($apply2Log, $apply2Text, (New-Object System.Text.UTF8Encoding($false)))
Write-Output $apply2Text.TrimEnd()
if ($apply2Code -ne 0) {
    throw "REFUSED: the repeated --apply exited $apply2Code."
}

$apply2Path = Get-PilotEvidencePath -RunsRoot $RunsRoot -RunId $RunId -Name 'recalc_apply_2'
Invoke-PilotPython -Python $Python -Arguments @(
    $parser, '--input', $apply2Log, '--label', 'apply-2', '--expect-day', $Day,
    '--compare-with', $applyPath, '--run-id', $RunId, '--kit-sha', $KitSha,
    '--out', $apply2Path) | Out-Null
$apply2 = Read-PilotJson -Path $apply2Path

$second = $apply2.payload.summary
Write-Output "SECOND_APPLY inserted=$($second.inserted) updated=$($second.updated) deleted=$($second.deleted) unchanged=$($second.unchanged) works=$($second.works)"

$idempotenceFailures = @()
if ($second.inserted -ne 0) { $idempotenceFailures += 'SECOND_APPLY_INSERTED_ROWS' }
if ($second.updated -ne 0) { $idempotenceFailures += 'SECOND_APPLY_UPDATED_ROWS' }
if ($second.deleted -ne 0) { $idempotenceFailures += 'SECOND_APPLY_DELETED_ROWS' }
if ($second.unchanged -ne $second.works) { $idempotenceFailures += 'SECOND_APPLY_DID_NOT_COUNT_EVERY_ROW_UNCHANGED' }
if (-not $apply2.payload.outputs_agree) { $idempotenceFailures += 'SECOND_APPLY_OUTPUTS_DIFFER' }

# --- 7. Final state ---------------------------------------------------------------
$finalPath = Get-PilotEvidencePath -RunsRoot $RunsRoot -RunId $RunId -Name 'staging_snapshot'
$finalRun = Invoke-PilotProbe -Python $Python -Script $probe -RunId $RunId -KitSha $KitSha `
    -OutFile $finalPath -AllowedExitCodes @(0, 3) `
    -Arguments @('snapshot', '--db', $K.StagingDb, '--day', $Day,
                 '--require', 'integrity', '--require', 'schema',
                 '--require', 'only-ready-summed',
                 '--require', ("area-sha256=" + $areaBefore))
$final = $finalRun.Evidence

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
Write-Output "AREA_SHA256_AFTER  : $($final.payload.area_ha.sha256)"
Write-Output "rows outside day   : $($final.payload.coverage_rows_outside_target_day)"

$failures = @() + $idempotenceFailures
if ($finalRun.ExitCode -ne 0) { $failures += 'FINAL_PROBE_REQUIREMENTS_FAILED' }
if ($final.payload.area_ha.sha256 -ne $areaBefore) { $failures += 'AREA_HA_CHANGED' }
if (-not $final.payload.coverage.only_ready_carries_a_number) { $failures += 'A_NON_READY_WORK_CARRIES_A_NUMBER' }
if ($final.payload.coverage_rows_outside_target_day -ne 0) { $failures += 'COVERAGE_ROWS_OUTSIDE_THE_TARGET_DAY' }

Set-PilotRunStep -RunsRoot $RunsRoot -RunId $RunId -Step 'recalculate' -Value ([ordered]@{
    completed_utc = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    apply_seconds = $applySeconds
    passed        = ($failures.Count -eq 0)
    failures      = $failures
}) | Out-Null

Write-Output ""
Write-Output "APPLY_SECONDS=$applySeconds"
Write-Output "RUN_ID=$RunId"

if ($failures.Count -gt 0) {
    throw "STAGING_RECALCULATE_AND_VERIFY=FAIL: $($failures -join ', ')"
}

Write-Output ""
Write-Output "STAGING_RECALCULATE_AND_VERIFY=PASS"
Write-Output "Now open $($K.StagingUrl)/drones/coverage?date_from=$Day&date_to=$Day and look at it."
