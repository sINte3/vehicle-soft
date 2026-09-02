<#
    BAK_TEX11_DJI_COLLECT_TO_STAGING.ps1
    DRONE-USEFUL-AREA-PILOT-001, step 3 of 6. RUNS ON BAK-TEX11 ONLY.

    WHAT THIS DOES
      Drives the existing collector mode --route-ui-collect --send-routes on
      the pilot checkout, with every guard the pilot needs around it: the right
      machine, the right repository at the VERIFIED commit, a clean working
      tree, the collector venv, a structurally usable DJI session, probe
      timings checked BEFORE the browser opens, and an ingest target that is
      the STAGING url and can never be the production one.

      The operator drives the DJI cabinet by hand. The tool only listens: it
      never initiates the route POST and never reproduces the signature.

    THE DAY IS CHOSEN BY THE OPERATOR IN THE BROWSER.
      --route-ui-collect takes no --from/--to: the day is whichever day the
      operator opens in Task History. This script prints the target day in the
      prompt, and step 5 (STAGING_RECALCULATE_AND_VERIFY.ps1) REFUSES to
      recalculate if any accepted route belongs to another day. That check on
      staging, not a flag here, is what actually holds the day to 2026-06-05.

    WHAT THIS NEVER DOES
      * runs on any machine but BAK-TEX11;
      * works outside C:\VehicleSoft_DJI_StageB_Pilot;
      * sends to http://10.103.25.14:5050 -- the effective configured base url
        is checked and a production target is a refusal, not a warning;
      * prints a token, a cookie, a signature, a request_id, a flight id or a
        coordinate. The evidence carries the collector's own RUN SUMMARY
        counters, filtered through a whitelist of key names;
      * counts a partially accepted batch as success. Exit 17 leaves every
        envelope in the queue for a repeat, and this script reports that as a
        failure with the pending count.

    RUN (on BAK-TEX11):
      Set-Location C:\VehicleSoft_DJI_StageB_Pilot
      .\ops\pilot_useful_area_001\BAK_TEX11_DJI_COLLECT_TO_STAGING.ps1

    Secrets come from the environment or drone_collector\.env that is already
    configured on this machine. This script neither reads nor writes them.
#>

[CmdletBinding()]
param(
    [string]$ExpectedHost = 'BAK-TEX11',
    [string]$WorkRoot = 'C:\VehicleSoft_DJI_StageB_Pilot\pilot_evidence',
    [switch]$SkipCodeUpdate
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Import-Module (Join-Path $PSScriptRoot 'PilotKit.psm1') -Force
$K = Get-PilotConstants

$stamp    = (Get-Date).ToString('yyyyMMdd_HHmmss')
$runRoot  = Join-Path $WorkRoot ("collect_{0}" -f $stamp)
$transcript = Join-Path $runRoot 'collector_output.txt'
$evidencePath = Join-Path $runRoot 'collect.json'
$preflightPath = Join-Path $runRoot 'collect_preflight.json'

Write-Output "=== DRONE-USEFUL-AREA-PILOT-001 / DJI COLLECT TO STAGING ==="
Write-Output "STAMP=$stamp"
Write-Output "TARGET_DAY=$($K.TargetDay)"

# --- 1. The right machine and the right repository --------------------------
Assert-PilotHost -Expected $ExpectedHost

if (-not (Test-Path -LiteralPath $K.CollectorRepo)) {
    throw "REFUSED: the pilot checkout $($K.CollectorRepo) does not exist on this machine."
}
$here = (Get-Location).Path
if (-not (Test-PilotPathWithin -Path $here -Root $K.CollectorRepo)) {
    throw "REFUSED: run this from $($K.CollectorRepo). The current directory is '$here'."
}
# [REASON]: this machine is not the server, but the guard costs nothing and
# the day someone copies this kit onto the server it is the only thing
# standing between a pilot and the production checkout.
Assert-PilotNotProduction -Path $K.CollectorRepo -What 'collector repository'
Assert-PilotNotProduction -Path $WorkRoot -What 'work root'
New-Item -ItemType Directory -Path $runRoot -Force | Out-Null

# --- 2. Clean tree, verified commit -----------------------------------------
Assert-PilotWorktreeClean -Repo $K.CollectorRepo
if (-not $SkipCodeUpdate) {
    Invoke-PilotGit -Repo $K.CollectorRepo -Arguments @('fetch', 'origin', 'main') | Out-Null
    $hasCommit = Invoke-PilotGit -Repo $K.CollectorRepo -AllowFailure `
        -Arguments @('cat-file', '-e', ($K.VerifiedSha + '^{commit}'))
    if ($hasCommit.ExitCode -ne 0) {
        throw "REFUSED: the verified commit $($K.VerifiedSha) is not in this checkout after fetching origin/main."
    }
    $head = Get-PilotHeadSha -Repo $K.CollectorRepo
    if ($head -ne $K.VerifiedSha) {
        # ff-only to the NAMED commit, never to the current tip of main.
        Invoke-PilotGit -Repo $K.CollectorRepo -Arguments @('merge', '--ff-only', $K.VerifiedSha) | Out-Null
    }
}
Assert-PilotHeadIsVerified -Repo $K.CollectorRepo
Assert-PilotWorktreeClean -Repo $K.CollectorRepo

# --- 3. Interpreter: the collector venv, never the system python ------------
if (-not (Test-Path -LiteralPath $K.CollectorPython)) {
    throw "REFUSED: the collector venv python $($K.CollectorPython) was not found. Playwright and Chromium live there; the system python does not see them."
}
& $K.CollectorPython --version
if ($LASTEXITCODE -ne 0) { throw "REFUSED: the venv python did not run." }

& $K.CollectorPython -c 'import playwright; print("PLAYWRIGHT_IMPORT=PASS")'
if ($LASTEXITCODE -ne 0) {
    throw "REFUSED: playwright is not importable in the collector venv. Fix the environment separately -- this run installs nothing."
}

& $K.CollectorPython -m drone_collector.main --help | Out-Null
if ($LASTEXITCODE -ne 0) { throw "REFUSED: the collector CLI did not start." }
Write-Output "PREFLIGHT_INTERPRETER=PASS"

# --- 4. Session, timings and ingest target, BEFORE the browser --------------
if (-not (Test-Path -LiteralPath $K.CollectorSession)) {
    throw "REFUSED: no saved DJI session at $($K.CollectorSession). Run --save-session and sign in once by hand."
}
$checker = Join-Path $PSScriptRoot 'pilot_collect_check.py'
& $K.CollectorPython $checker 'preflight' '--expect-url' $K.StagingUrl '--out' $preflightPath > (Join-Path $runRoot 'preflight_stdout.json')
$preflightCode = $LASTEXITCODE
$preflight = Read-PilotJson -Path $preflightPath

Write-Output "INGEST_BASE_URL=$($preflight.payload.base_url)"
Write-Output "INGEST_TARGET_IS_STAGING=$($preflight.payload.target_is_staging)"
Write-Output "INGEST_TARGET_IS_PRODUCTION=$($preflight.payload.target_is_production)"
Write-Output "DRONE_API_TOKEN=$($preflight.payload.api_token)"
Write-Output "SESSION=$($preflight.payload.session.usable) bytes=$($preflight.payload.session.bytes) cookies=$($preflight.payload.session.cookies) origins=$($preflight.payload.session.origins)"
Write-Output "PROBE_TIMINGS_VALID=$($preflight.payload.probe_timings_valid)"

if ($preflight.payload.target_is_production) {
    throw "REFUSED: the collector is configured to send to PRODUCTION. This pilot sends to staging only. Nothing was collected."
}
Assert-PilotStagingUrl -Url $preflight.payload.base_url
if ($preflightCode -ne 0) {
    throw "REFUSED: the collector preflight failed: $($preflight.payload.reasons -join ', '). The browser was never opened."
}
Write-Output "PREFLIGHT_COLLECTOR=PASS"

# --- 5. The live run --------------------------------------------------------
Write-Output ""
Write-Output "=============================================================="
Write-Output " A browser will open. In it:"
Write-Output "   1. open Task History (flight records);"
Write-Output "   2. choose the day $($K.TargetDay) -- THIS DAY AND NO OTHER;"
Write-Output "   3. switch to the MAP view;"
Write-Output "   4. wait until the routes are drawn;"
Write-Output "   5. come back to this console and press Enter."
Write-Output " The cabinet signs its own request. This tool only listens."
Write-Output "=============================================================="
Write-Output ""

# [REASON]: the collector's output is NOT piped through Tee-Object. This run
# asks the operator a question and waits for Enter; a pipeline buffers, and a
# prompt the operator never sees is a run that fails on a timeout with the
# person sitting right there. The console stays direct, and the RUN SUMMARY is
# read afterwards from the collector's own rotating log -- which is where it
# durably lives anyway (drone_collector/logging_setup.py).
$collectorLog = Join-Path $K.CollectorRepo 'drone_collector\logs\collector.log'
$logBytesBefore = 0
if (Test-Path -LiteralPath $collectorLog) {
    $logBytesBefore = (Get-Item -LiteralPath $collectorLog).Length
}

Push-Location $K.CollectorRepo
try {
    & $K.CollectorPython -m drone_collector.main --route-ui-collect --send-routes
    $collectCode = $LASTEXITCODE
} finally {
    Pop-Location
}
Write-Output "COLLECTOR_EXIT=$collectCode"

if (-not (Test-Path -LiteralPath $collectorLog)) {
    throw "REFUSED: the collector log $collectorLog was not written, so this run cannot be summarised."
}
$logBytesAfter = (Get-Item -LiteralPath $collectorLog).Length
# The log rotates at 5 MB; a shorter file means it rotated during the run, and
# the offset from before it no longer means anything.
$offset = if ($logBytesAfter -lt $logBytesBefore) { 0 } else { $logBytesBefore }
$stream = [System.IO.File]::Open($collectorLog, 'Open', 'Read', 'ReadWrite')
try {
    $null = $stream.Seek($offset, 'Begin')
    $reader = New-Object System.IO.StreamReader($stream, [System.Text.Encoding]::UTF8)
    Set-Content -LiteralPath $transcript -Value $reader.ReadToEnd() -Encoding utf8NoBOM
} finally {
    $stream.Dispose()
}
Write-Output "RUN_LOG_SLICE=$transcript"

# --- 6. Safe evidence from the collector's own RUN SUMMARY -------------------
& $K.CollectorPython $checker 'summary' '--input' $transcript '--out' $evidencePath > (Join-Path $runRoot 'summary_stdout.json')
$summaryCode = $LASTEXITCODE
$summary = Read-PilotJson -Path $evidencePath
$counters = $summary.payload.counters

Write-Output ""
Write-Output "--- collector counters (no identifier, no coordinate) ---"
Write-Output "observed/confirmed : $($counters.probe_observations) / $($counters.probe_confirmed)"
Write-Output "over the size cap  : $($counters.probe_skipped_over_cap)"
Write-Output "operator answered  : $($counters.probe_operator_answered)"
Write-Output "drained            : $($counters.probe_drained)"
Write-Output "bodies captured    : $($counters.collect_bodies_captured)"
Write-Output "decode failures    : $($counters.collect_decode_failures)"
Write-Output "routes captured    : $($counters.collect_routes_captured)"
Write-Output "queued / duplicate : $($counters.collect_routes_queued) / $($counters.collect_routes_duplicate)"
Write-Output "envelopes sent     : $($counters.collect_envelopes_sent)"
Write-Output "batch accepted     : $($counters.collect_batch_accepted)"
Write-Output "left pending       : $($counters.collect_left_pending)"
Write-Output "seen/new/upd/unch  : $($counters.collect_seen) / $($counters.collect_new) / $($counters.collect_updated) / $($counters.collect_unchanged)"
Write-Output "errors / unlinked  : $($counters.collect_errors) / $($counters.collect_unlinked)"

# --- 7. Working tree after the run ------------------------------------------
Assert-PilotWorktreeClean -Repo $K.CollectorRepo
Assert-PilotHeadIsVerified -Repo $K.CollectorRepo

# --- 8. Verdict --------------------------------------------------------------
$failures = @()
if ($collectCode -ne 0) { $failures += "COLLECTOR_EXIT_$collectCode" }
if ($summaryCode -ne 0) { $failures += 'SUMMARY_CHECKS_FAILED' }
foreach ($reason in $summary.payload.reasons) { $failures += $reason }

if ($failures.Count -gt 0) {
    Write-Output ""
    Write-Output "COLLECTION NOT ACCEPTED AS COMPLETE:"
    foreach ($reason in $failures) { Write-Output "  - $reason" }
    if ($counters.collect_left_pending -and $counters.collect_left_pending -gt 0) {
        Write-Output ""
        Write-Output "$($counters.collect_left_pending) envelope(s) stayed in the queue on purpose."
        Write-Output "The route ingest is idempotent: re-sending the whole batch later is safe."
    }
    Write-Output ""
    Write-Output "EVIDENCE=$evidencePath"
    throw "BAK_TEX11_DJI_COLLECT_TO_STAGING=FAIL. Do NOT recalculate on staging: the input is not a complete confirmed capture."
}

Write-Output ""
Write-Output "EVIDENCE=$evidencePath"
Write-Output "BAK_TEX11_DJI_COLLECT_TO_STAGING=PASS"
Write-Output "Return to the owner: this console output and the file $evidencePath."
Write-Output "Copy that ONE json file to the server for the final report. The"
Write-Output "transcript $transcript stays on this machine: it is the run log,"
Write-Output "not evidence, and it is not part of the safe report."
