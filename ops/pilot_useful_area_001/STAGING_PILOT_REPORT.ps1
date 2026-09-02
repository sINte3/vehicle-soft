<#
    STAGING_PILOT_REPORT.ps1
    DRONE-USEFUL-AREA-PILOT-001, step 5 of 6.

    WHAT THIS DOES
      Collects the evidence the four earlier steps left behind, hands it to
      pilot_report.py, and produces ONE json and ONE markdown report with a
      machine verdict GO / ADJUST / REJECT and stable reason codes.

      The evidence files are found by convention under the work root: the
      newest preflight, the deploy manifest, the newest recalculation set.
      Only the collector evidence has to be named, because it is carried over
      from BAK-TEX11 by hand.

    WHAT THIS DOES NOT DO
      It does not decide the business question. The threshold that turns GO
      into ADJUST is a REPORTING parameter chosen by this kit, not a rule of
      the holding, and the report says so on its face. Both the raw share and
      the threshold are printed so the verdict can be recomputed by eye.

    RUN (on the server):
      Set-Location C:\transport-report-staging
      .\ops\pilot_useful_area_001\STAGING_PILOT_REPORT.ps1 -CollectEvidence D:\pilot-in\collect.json
#>

[CmdletBinding()]
param(
    [string]$ExpectedHost = 'srv-yoqsh',
    [string]$WorkRoot = 'D:\transport-report-backups\pilot\DRONE-USEFUL-AREA-001',
    [Parameter(Mandatory)][string]$CollectEvidence,
    [string]$Python = 'C:\Program Files\Python314\python.exe',
    [double]$AdjustShareThreshold = 0.20,
    [double]$DjiDeltaAdjustPercent = -1
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Import-Module (Join-Path $PSScriptRoot 'PilotKit.psm1') -Force
$K = Get-PilotConstants

Write-Output "=== DRONE-USEFUL-AREA-PILOT-001 / PILOT REPORT ==="

Assert-PilotHost -Expected $ExpectedHost
Assert-PilotNotProduction -Path $WorkRoot -What 'work root'

function Find-Newest([string]$pattern, [string]$leaf) {
    $candidates = @(Get-ChildItem -LiteralPath $WorkRoot -Directory -ErrorAction SilentlyContinue |
                    Where-Object { $_.Name -like $pattern } |
                    Sort-Object Name -Descending)
    foreach ($candidate in $candidates) {
        $path = Join-Path $candidate.FullName $leaf
        if (Test-Path -LiteralPath $path) { return $path }
    }
    return $null
}

$preflight = Find-Newest 'preflight_*' 'evidence\preflight.json'
$manifest  = Join-Path $WorkRoot 'manifests\latest.json'
$dry       = Find-Newest 'recalc_*' 'evidence\recalc_dry.json'
$apply1    = Find-Newest 'recalc_*' 'evidence\recalc_apply_1.json'
$apply2    = Find-Newest 'recalc_*' 'evidence\recalc_apply_2.json'
$snapshot  = Find-Newest 'recalc_*' 'evidence\staging_snapshot.json'
$timing    = Find-Newest 'recalc_*' 'evidence\recalc_timing.json'

foreach ($pair in @(@('preflight', $preflight), @('deploy manifest', $manifest),
                    @('dry run', $dry), @('apply 1', $apply1),
                    @('apply 2', $apply2), @('staging snapshot', $snapshot))) {
    if (-not $pair[1] -or -not (Test-Path -LiteralPath $pair[1])) {
        throw "REFUSED: the $($pair[0]) evidence was not found under $WorkRoot. Run the earlier steps first."
    }
    Write-Output "EVIDENCE_$($pair[0].ToUpperInvariant().Replace(' ','_'))=$($pair[1])"
}
if (-not (Test-Path -LiteralPath $CollectEvidence)) {
    throw "REFUSED: the collector evidence $CollectEvidence was not found. Copy it over from BAK-TEX11."
}
Write-Output "EVIDENCE_COLLECT=$CollectEvidence"

$applySeconds = $null
if ($timing -and (Test-Path -LiteralPath $timing)) {
    $applySeconds = (Read-PilotJson -Path $timing).payload.apply_seconds
}

$stamp   = (Get-Date).ToString('yyyyMMdd_HHmmss')
$outDir  = Join-Path $WorkRoot ("report_{0}" -f $stamp)
New-Item -ItemType Directory -Path $outDir -Force | Out-Null
$outJson = Join-Path $outDir 'PILOT_REPORT.json'
$outMd   = Join-Path $outDir 'PILOT_REPORT.md'

$arguments = @(
    (Join-Path $PSScriptRoot 'pilot_report.py'),
    '--preflight', $preflight,
    '--deploy', $manifest,
    '--collect', $CollectEvidence,
    '--staging-snapshot', $snapshot,
    '--recalc', $dry, '--recalc', $apply1, '--recalc', $apply2,
    '--adjust-share-threshold', ([string]$AdjustShareThreshold),
    '--out-json', $outJson,
    '--out-md', $outMd
)
if ($null -ne $applySeconds) {
    $arguments += @('--recalc-seconds', ([string]$applySeconds))
}
# [REASON]: a NEGATIVE default means "the owner has not named a rule". No
# deviation between the useful area and the DJI figure is proven to be
# systematic, and inventing a percentage here would be inventing a business
# rule -- which the charter forbids outright.
if ($DjiDeltaAdjustPercent -ge 0) {
    $arguments += @('--dji-delta-adjust-percent', ([string]$DjiDeltaAdjustPercent))
}

& $Python @arguments
$reportCode = $LASTEXITCODE

Write-Output ""
Write-Output "REPORT_JSON=$outJson"
Write-Output "REPORT_MD=$outMd"

if ($reportCode -ne 0) {
    throw "STAGING_PILOT_REPORT=FAIL: the report did not pass its own privacy scan (exit $reportCode). Read the PRIVACY VIOLATION lines above. Do NOT send the report anywhere until it is clean."
}

Write-Output "STAGING_PILOT_REPORT=PASS"
Write-Output "Return to the owner: BOTH files above. They are the safe report."
