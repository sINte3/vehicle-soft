<#
    BAK_TEX11_DJI_COLLECT_TO_STAGING.ps1
    DRONE-USEFUL-AREA-PILOT-001, step 3 of 6. RUNS ON BAK-TEX11 ONLY.

    WHAT THIS DOES
      Drives the existing collector mode --route-ui-collect --send-routes on
      the pilot checkout, with every guard the pilot needs around it: the right
      machine, the right repository at the KIT revision, a clean working tree
      CHECKED BEFORE ANY FILE IS CREATED, the collector venv, a structurally
      usable DJI session, probe timings checked BEFORE the browser opens, and
      an ingest target that is the STAGING url and can never be the production
      one.

    WHICH REVISION, AND WHY IT IS THE KIT'S
      The collector runs at KIT_SHA, not at PRODUCT_SHA. The kit revision adds
      two numbers to the collector's run summary -- probe_request_failures and
      probe_pending_requests -- and without them the completeness of the live
      capture had to be inferred from observations == confirmed. That
      inference is wrong: a request that dies BEFORE its body never becomes an
      observation, so the equality holds while a route is lost. Staging stays
      on PRODUCT_SHA; only this machine, which deploys nothing, runs the kit
      revision. PRODUCT_BLOBS.json records exactly which file differs.

    THE WORK DIRECTORY IS OUTSIDE THE CHECKOUT
      C:\vehicle-soft-pilot-runs, never inside C:\VehicleSoft_DJI_StageB_Pilot.
      Evidence written inside the checkout would appear in `git status` and
      make the clean-tree check fail on the run's own artefacts.

    WHAT THIS NEVER DOES
      * runs on any machine but BAK-TEX11;
      * sends to http://10.103.25.14:5050 -- the effective configured base url
        is checked and a production target is a refusal, not a warning;
      * prints a token, a cookie, a signature, a request_id, a flight id or a
        coordinate;
      * counts a partially accepted batch as success.

    RUN (on BAK-TEX11, from the collector checkout):
      Set-Location C:\VehicleSoft_DJI_StageB_Pilot
      .\ops\pilot_useful_area_001\BAK_TEX11_DJI_COLLECT_TO_STAGING.ps1 -RunId <the id step 1 printed> -KitSha <the kit sha step 1 printed>

    Secrets come from the environment or drone_collector\.env that is already
    configured on this machine. This script neither reads nor writes them.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$RunId,
    [Parameter(Mandatory)][string]$KitSha,
    [string]$ExpectedHost = 'BAK-TEX11',
    [string]$RunsRoot = 'C:\vehicle-soft-pilot-runs',
    [switch]$SkipCodeUpdate
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Import-Module (Join-Path $PSScriptRoot 'PilotKit.psm1') -Force
$K = Get-PilotConstants

Write-Output "=== DRONE-USEFUL-AREA-PILOT-001 / DJI COLLECT TO STAGING ==="
Write-Output "RUN_ID=$RunId"
Write-Output "TARGET_DAY=$($K.TargetDay)"

if ($RunId -notmatch '^\d{8}T\d{6}Z-[0-9a-f]{8}$') {
    throw "REFUSED: '$RunId' is not a run identifier of this kit. Take it from the line RUN_ID= that step 1 printed."
}
if ($KitSha -notmatch '^[0-9a-f]{40}$') {
    throw "REFUSED: '$KitSha' is not a revision. Take it from the line KIT_SHA= that step 1 printed."
}

# --- 1. The right machine and the right repository --------------------------
Assert-PilotHost -Expected $ExpectedHost

if (-not (Test-Path -LiteralPath $K.CollectorRepo)) {
    throw "REFUSED: the pilot checkout $($K.CollectorRepo) does not exist on this machine."
}
$here = (Get-Location).Path
if (-not (Test-PilotPathWithin -Path $here -Root $K.CollectorRepo)) {
    throw "REFUSED: run this from $($K.CollectorRepo). The current directory is '$here'."
}
Assert-PilotNotProduction -Path $K.CollectorRepo -What 'collector repository'

# --- 2. Clean tree FIRST, before a single file of ours exists ---------------
# [REASON]: the order matters and it used to be wrong. The first edition
# created its work directory inside the checkout and only then asked whether
# the tree was clean -- so the run's own evidence was what made it dirty.
# Nothing of ours is written until this passes, and the work root is outside
# the checkout anyway.
Assert-PilotWorktreeClean -Repo $K.CollectorRepo
Assert-PilotOutsideCheckouts -Path $RunsRoot -What 'runs root'

if (-not $SkipCodeUpdate) {
    Invoke-PilotGit -Repo $K.CollectorRepo -Arguments @('fetch', 'origin') | Out-Null
    $hasCommit = Invoke-PilotGit -Repo $K.CollectorRepo -AllowFailure `
        -Arguments @('cat-file', '-e', ($KitSha + '^{commit}'))
    if ($hasCommit.ExitCode -ne 0) {
        throw "REFUSED: the kit revision $KitSha is not in this checkout after fetching origin."
    }
    $head = Get-PilotHeadSha -Repo $K.CollectorRepo
    if ($head -ne $KitSha) {
        # ff-only to the NAMED commit, never to the current tip of a branch.
        Invoke-PilotGit -Repo $K.CollectorRepo -Arguments @('merge', '--ff-only', $KitSha) | Out-Null
    }
}
$head = Get-PilotHeadSha -Repo $K.CollectorRepo
if ($head -ne $KitSha) {
    throw "REFUSED: $($K.CollectorRepo) is at $head, not at the kit revision $KitSha."
}
Assert-PilotWorktreeClean -Repo $K.CollectorRepo
Write-Output "COLLECTOR_HEAD=$head"

# --- 3. Now, and only now, the run directory (outside the checkout) ---------
$runRoot = Join-Path $RunsRoot $RunId
New-Item -ItemType Directory -Path $runRoot -Force | Out-Null
$transcript = Join-Path $runRoot 'collector_run_log.txt'
$evidencePath = Join-Path $runRoot 'collect.json'
$preflightPath = Join-Path $runRoot 'collect_preflight.json'
Write-Output "RUN_ROOT=$runRoot"

# --- 4. The executables this machine runs are the kit revision's ------------
$repoTool = Join-Path $PSScriptRoot 'pilot_repo_check.py'
$checker = Join-Path $PSScriptRoot 'pilot_collect_check.py'

if (-not (Test-Path -LiteralPath $K.CollectorPython)) {
    throw "REFUSED: the collector venv python $($K.CollectorPython) was not found. Playwright and Chromium live there; the system python does not see them."
}
Invoke-PilotProbe -Python $K.CollectorPython -Script $repoTool -RunId $RunId -KitSha $KitSha `
    -OutFile (Join-Path $runRoot 'repo_collector.json') `
    -Arguments @('verify', '--repo', $K.CollectorRepo, '--expect-sha', $KitSha,
                 '--role', 'collector') | Out-Null
Write-Output "COLLECTOR_BLOBS=VERIFIED"

& $K.CollectorPython --version
if ($LASTEXITCODE -ne 0) { throw "REFUSED: the venv python did not run." }

& $K.CollectorPython -c 'import playwright; print("PLAYWRIGHT_IMPORT=PASS")'
if ($LASTEXITCODE -ne 0) {
    throw "REFUSED: playwright is not importable in the collector venv. Fix the environment separately -- this run installs nothing."
}

& $K.CollectorPython -m drone_collector.main --help | Out-Null
if ($LASTEXITCODE -ne 0) { throw "REFUSED: the collector CLI did not start." }
Write-Output "PREFLIGHT_INTERPRETER=PASS"

# --- 5. Session, timings and ingest target, BEFORE the browser --------------
if (-not (Test-Path -LiteralPath $K.CollectorSession)) {
    throw "REFUSED: no saved DJI session at $($K.CollectorSession). Run --save-session and sign in once by hand."
}
$preflightRun = Invoke-PilotProbe -Python $K.CollectorPython -Script $checker `
    -RunId $RunId -KitSha $KitSha -OutFile $preflightPath -AllowedExitCodes @(0, 1, 3) `
    -Arguments @('preflight', '--expect-url', $K.StagingUrl)
$preflight = $preflightRun.Evidence

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
if ($preflightRun.ExitCode -ne 0) {
    throw "REFUSED: the collector preflight failed: $($preflight.payload.reasons -join ', '). The browser was never opened."
}
Write-Output "PREFLIGHT_COLLECTOR=PASS"

# --- 6. The live run --------------------------------------------------------
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
# read afterwards from the collector's own rotating log.
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
    $slice = $reader.ReadToEnd()
} finally {
    $stream.Dispose()
}
[System.IO.File]::WriteAllText($transcript, $slice, (New-Object System.Text.UTF8Encoding($false)))
Write-Output "RUN_LOG_SLICE=$transcript"

# --- 7. Safe evidence from the collector's own RUN SUMMARY -------------------
$summaryRun = Invoke-PilotProbe -Python $K.CollectorPython -Script $checker `
    -RunId $RunId -KitSha $KitSha -OutFile $evidencePath -AllowedExitCodes @(0, 1, 3) `
    -Arguments @('summary', '--input', $transcript)
$summary = $summaryRun.Evidence
$counters = $summary.payload.counters

Write-Output ""
Write-Output "--- collector counters (no identifier, no coordinate) ---"
Write-Output "observed/confirmed : $($counters.probe_observations) / $($counters.probe_confirmed)"
Write-Output "over the size cap  : $($counters.probe_skipped_over_cap)"
Write-Output "requests failed    : $($counters.probe_request_failures)"
Write-Output "requests pending   : $($counters.probe_pending_requests)"
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

# --- 8. Working tree after the run ------------------------------------------
Assert-PilotWorktreeClean -Repo $K.CollectorRepo
$headAfter = Get-PilotHeadSha -Repo $K.CollectorRepo
if ($headAfter -ne $KitSha) {
    throw "REFUSED: the checkout moved during the run: $headAfter."
}

# --- 9. Verdict --------------------------------------------------------------
$failures = @()
if ($collectCode -ne 0) { $failures += "COLLECTOR_EXIT_$collectCode" }
if ($summaryRun.ExitCode -ne 0) { $failures += 'SUMMARY_CHECKS_FAILED' }
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
Write-Output "RUN_ID=$RunId"
Write-Output ""
Write-Output "Copy that ONE file to the server, to exactly this path:"
Write-Output "  D:\transport-report-backups\pilot\DRONE-USEFUL-AREA-001\runs\$RunId\evidence\collect.json"
Write-Output "The run log $transcript stays on this machine: it is the run log,"
Write-Output "not evidence, and it is not part of the safe report."
