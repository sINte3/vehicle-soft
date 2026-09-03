<#
    STAGING_PILOT_REPORT.ps1
    DRONE-USEFUL-AREA-PILOT-001, step 5 of 6.

    WHAT THIS DOES
      Takes the evidence of ONE NAMED RUN from its fixed paths, hands it to
      pilot_report.py, and produces one JSON and one Markdown report with a
      machine verdict and stable reason codes.

      NOTHING IS SEARCHED FOR. Every file is at a fixed name inside the run
      directory, and every envelope is checked to belong to this run, this
      kit revision, this product revision and this day. Evidence from two
      runs cannot be mixed, because mixing it is what this refuses.

    WHAT THIS DOES NOT DO
      It does not make the business decision, and it will not pretend to.
      The share of works allowed to go without a number and the deviation from
      the DJI area that counts as acceptable are the owner's rules. Until BOTH
      are given on the command line, the best verdict available is
      TECHNICAL_GO: the pipeline works, the decision has not been made, and
      production is NOT authorised by this report.

    RUN (on the server, from the KIT checkout):
      Set-Location C:\vehicle-soft-pilot-kit
      .\ops\pilot_useful_area_001\STAGING_PILOT_REPORT.ps1 -RunId <the id step 1 printed>

    With the owner's rules, once he has named them:
      .\ops\pilot_useful_area_001\STAGING_PILOT_REPORT.ps1 -RunId <id> -OwnerShareThreshold <number> -OwnerDjiDeltaPercent <number>
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$RunId,
    [string]$ExpectedHost = 'srv-yoqsh',
    [string]$RunsRoot = 'D:\transport-report-backups\pilot\DRONE-USEFUL-AREA-001\runs',
    [string]$Python = 'C:\Program Files\Python314\python.exe',
    [double]$OwnerShareThreshold = -1,
    [double]$OwnerDjiDeltaPercent = -1
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Import-Module (Join-Path $PSScriptRoot 'PilotKit.psm1') -Force
$K = Get-PilotConstants
$KitCheckout = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

Write-Output "=== DRONE-USEFUL-AREA-PILOT-001 / PILOT REPORT ==="
Write-Output "RUN_ID=$RunId"

Assert-PilotHost -Expected $ExpectedHost
Assert-PilotOutsideCheckouts -Path $RunsRoot -What 'runs root'

$run = Get-PilotRun -RunsRoot $RunsRoot -RunId $RunId
$KitSha = Get-PilotKitSha -KitCheckout $KitCheckout
if ($KitSha -ne $run.kit_sha) {
    throw "REFUSED: this kit checkout is at $KitSha, the run was opened with $($run.kit_sha)."
}

# Fixed paths. No Get-ChildItem, no "newest", nothing to pick wrongly.
$paths = [ordered]@{
    preflight        = Get-PilotEvidencePath -RunsRoot $RunsRoot -RunId $RunId -Name 'preflight'
    deploy           = Get-PilotEvidencePath -RunsRoot $RunsRoot -RunId $RunId -Name 'deploy'
    collect          = Get-PilotEvidencePath -RunsRoot $RunsRoot -RunId $RunId -Name 'collect'
    recalc_dry       = Get-PilotEvidencePath -RunsRoot $RunsRoot -RunId $RunId -Name 'recalc_dry'
    recalc_apply_1   = Get-PilotEvidencePath -RunsRoot $RunsRoot -RunId $RunId -Name 'recalc_apply_1'
    recalc_apply_2   = Get-PilotEvidencePath -RunsRoot $RunsRoot -RunId $RunId -Name 'recalc_apply_2'
    staging_snapshot = Get-PilotEvidencePath -RunsRoot $RunsRoot -RunId $RunId -Name 'staging_snapshot'
}
foreach ($entry in $paths.GetEnumerator()) {
    if (-not (Test-Path -LiteralPath $entry.Value)) {
        throw "REFUSED: the $($entry.Key) evidence of run $RunId is missing:`n  $($entry.Value)`nRun the earlier steps of THIS run first."
    }
    Write-Output "EVIDENCE_$($entry.Key.ToUpperInvariant())=$($entry.Value)"
}

$outJson = Get-PilotEvidencePath -RunsRoot $RunsRoot -RunId $RunId -Name 'report_json'
$outMd   = Get-PilotEvidencePath -RunsRoot $RunsRoot -RunId $RunId -Name 'report_md'

$arguments = @(
    (Join-Path $PSScriptRoot 'pilot_report.py'),
    '--preflight', $paths.preflight,
    '--deploy', $paths.deploy,
    '--collect', $paths.collect,
    '--staging-snapshot', $paths.staging_snapshot,
    '--recalc', $paths.recalc_dry,
    '--recalc', $paths.recalc_apply_1,
    '--recalc', $paths.recalc_apply_2,
    '--run-id', $RunId,
    '--kit-sha', $KitSha,
    '--out-json', $outJson,
    '--out-md', $outMd
)

# [REASON]: a NEGATIVE value means "the owner has not named this rule". There
# is no default, and there must not be one: a threshold chosen by the kit and
# passed silently becomes a decision the owner never made.
if ($OwnerShareThreshold -ge 0) {
    $arguments += @('--owner-share-threshold', ([string]$OwnerShareThreshold))
}
if ($OwnerDjiDeltaPercent -ge 0) {
    $arguments += @('--owner-dji-delta-percent', ([string]$OwnerDjiDeltaPercent))
}

$reportCode = Invoke-PilotPython -Python $Python -Arguments $arguments -PassThruExitCode
$report = Read-PilotJson -Path $outJson

Write-Output ""
Write-Output "VERDICT=$($report.verdict)"
foreach ($reason in $report.verdict_reasons) { Write-Output "REASON=$reason" }
Write-Output "PRODUCTION_ROLLOUT_AUTHORISED=$($report.production_rollout_authorised)"
Write-Output "PRIVACY_SCAN=$(if ($report.privacy_scan_passed) { 'PASS' } else { 'FAIL' })"
Write-Output "REPORT_JSON=$outJson"
Write-Output "REPORT_MD=$outMd"

Set-PilotRunStep -RunsRoot $RunsRoot -RunId $RunId -Step 'report' -Value ([ordered]@{
    completed_utc = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    verdict       = $report.verdict
    json          = $outJson
    markdown      = $outMd
}) | Out-Null

if ($reportCode -ne 0) {
    throw "STAGING_PILOT_REPORT=FAIL: the report did not pass its own privacy scan (exit $reportCode). Do NOT send the report anywhere until it is clean."
}

Write-Output "STAGING_PILOT_REPORT=PASS"
