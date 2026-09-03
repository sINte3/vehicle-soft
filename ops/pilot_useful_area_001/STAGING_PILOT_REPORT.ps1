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
      .\ops\pilot_useful_area_001\STAGING_PILOT_REPORT.ps1 -RunId ... -ApprovedKitSha ...
      (step 1 prints the exact command as NEXT_COMMAND_STEP5)

    Once the owner has named his two rules, they are added as
    -OwnerShareThreshold and -OwnerDjiDeltaPercent. This kit prints no example
    values for them: a number printed here reads as a recommendation, and the
    decision has not been made.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$RunId,
    [Parameter(Mandatory)][string]$ApprovedKitSha,
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

$run = Get-PilotRun -RunsRoot $RunsRoot -RunId $RunId -ApprovedKitSha $ApprovedKitSha
$KitSha = Assert-PilotApprovedKitSha -KitCheckout $KitCheckout -ApprovedKitSha $ApprovedKitSha
if ($KitSha -ne $run.approved_kit_sha) {
    throw "REFUSED: this kit checkout is at $KitSha, the run was opened with $($run.approved_kit_sha)."
}
Write-Output "KIT_SHA=$KitSha"

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

# [REASON]: the exit code of the report tool CARRIES THE VERDICT. 1 means the
# report could not be built at all; 0/10/11/12 mean it was built and this is
# what it says. The previous edition returned 0 on REJECT and this script then
# printed PASS after it -- the one line an operator actually reads.
$reportCode = Invoke-PilotPython -Python $Python -Arguments $arguments -PassThruExitCode
if ($reportCode -eq 1) {
    throw "REFUSED: the report could not be built (exit 1). See the ERROR line above. No verdict was produced."
}
$report = Read-PilotJson -Path $outJson

$expectedCode = @{ 'GO' = 0; 'TECHNICAL_GO' = 10; 'ADJUST' = 11; 'REJECT' = 12 }[[string]$report.verdict]
if ($reportCode -ne $expectedCode) {
    throw "REFUSED: the report says '$($report.verdict)' but exited $reportCode; the contract says $expectedCode. The two must agree or neither can be trusted."
}

Write-Output ""
Write-Output "REPORT_GENERATED=yes"
Write-Output "REPORT_JSON=$outJson"
Write-Output "REPORT_MD=$outMd"
Write-Output "PILOT_VERDICT=$($report.verdict)"
foreach ($reason in $report.verdict_reasons) { Write-Output "REASON=$reason" }
Write-Output "PRIVACY_SCAN=$(if ($report.privacy_scan_passed) { 'PASS' } else { 'FAIL' })"
# This staging kit NEVER authorises production. Lifting the release-gate row
# and deciding on a production rollout is a separate act by the owner, after
# this report.
Write-Output "PRODUCTION_ROLLOUT_AUTHORISED=no"
Write-Output "RELEASE_GATE_ACTION_REQUIRED=yes"

Set-PilotRunStep -RunsRoot $RunsRoot -RunId $RunId -Step 'report' -Value ([ordered]@{
    completed_utc = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    verdict       = [string]$report.verdict
    exit_code     = $reportCode
    json          = $outJson
    markdown      = $outMd
}) | Out-Null

if ([string]$report.verdict -eq 'REJECT') {
    throw "PILOT_VERDICT=REJECT. The report was written and is the record of why. Nothing about this run may be presented as a successful pilot."
}
Write-Output "STAGING_PILOT_REPORT_COMPLETED=yes"
