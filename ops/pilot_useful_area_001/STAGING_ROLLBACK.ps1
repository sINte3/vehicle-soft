<#
    STAGING_ROLLBACK.ps1
    DRONE-USEFUL-AREA-PILOT-001, the way back.

    WHAT THIS DOES
      Reads the deploy evidence of ONE NAMED RUN, verifies the backup it names
      (sha256 and PRAGMA integrity_check), stops ONLY the staging service,
      restores the staging database, returns the staging checkout to the sha
      it had before the deploy, starts the staging service and smoke-tests it.

      NO FILE NAME IS TYPED BY HAND, and nothing is searched for: the run id
      names the directory, the directory names the evidence, the evidence
      names the backup.

    WHAT THIS NEVER DOES
      * touches C:\transport-report or the TransportReport service;
      * uses `git reset --hard` -- the project charter forbids it as a
        rollback. The checkout returns to the recorded sha with a DETACHED
        checkout, which restores exactly that tree while moving no branch ref
        and rewriting no history. Going forward again is `git checkout <branch>`
        and the evidence names the branch;
      * deletes a backup, or any production data.

    IT RUNS FROM THE KIT CHECKOUT, which is a different repository from the
    one being rolled back. That is the point: rolling staging back cannot take
    the rollback script away with it.

    RUN (on the server, from the KIT checkout):
      Set-Location C:\vehicle-soft-pilot-kit
      .\ops\pilot_useful_area_001\STAGING_ROLLBACK.ps1 -RunId ... -ApprovedKitSha ... -Force
      (step 1 prints the exact command as NEXT_COMMAND_ROLLBACK)

    Without -Force it asks for a typed confirmation: this overwrites the
    staging database.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$RunId,
    [Parameter(Mandatory)][string]$ApprovedKitSha,
    [string]$ExpectedHost = 'srv-yoqsh',
    [string]$RunsRoot = 'D:\transport-report-backups\pilot\DRONE-USEFUL-AREA-001\runs',
    [string]$Python = 'C:\Program Files\Python314\python.exe',
    [int]$ServiceTimeoutSeconds = 90,
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Import-Module (Join-Path $PSScriptRoot 'PilotKit.psm1') -Force
$K = Get-PilotConstants
$KitCheckout = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

Write-Output "=== DRONE-USEFUL-AREA-PILOT-001 / STAGING ROLLBACK ==="

Assert-PilotHost -Expected $ExpectedHost
Assert-PilotOutsideCheckouts -Path $RunsRoot -What 'runs root'

$run = Get-PilotRun -RunsRoot $RunsRoot -RunId $RunId -ApprovedKitSha $ApprovedKitSha
$KitSha = Assert-PilotApprovedKitSha -KitCheckout $KitCheckout -ApprovedKitSha $ApprovedKitSha
$deployPath = Get-PilotEvidencePath -RunsRoot $RunsRoot -RunId $RunId -Name 'deploy'
$deploy = Read-PilotJson -Path $deployPath
$payload = $deploy.payload

Write-Output "RUN_ID=$RunId"
Write-Output "KIT_SHA=$KitSha"
Write-Output "DEPLOY_EVIDENCE=$deployPath"
Write-Output "DEPLOY_PHASE=$($payload.phase)"

# --- 1. EVERYTHING is checked before the service is touched -----------------
#
# [REASON]: this script stops a service, moves a database aside and overwrites
# it. Every one of those is hard to undo, and the first edition reached them
# after checking two fields. What follows is the complete list, and each entry
# refuses BEFORE Stop-Service.
$refusals = @()

# 1a. The envelope, in full -- not just the run id.
foreach ($field in 'kit', 'kit_version', 'evidence_kind', 'run_id', 'kit_sha',
                   'product_sha', 'generated_utc', 'target_day', 'payload') {
    if (-not ($deploy.PSObject.Properties.Name -contains $field)) {
        $refusals += "DEPLOY_ENVELOPE_MISSING:$field"
    }
}
if ($deploy.kit -ne 'DRONE-USEFUL-AREA-PILOT-001') { $refusals += 'DEPLOY_ENVELOPE_WRONG_KIT' }
if ([string]$deploy.kit_version -ne '2') { $refusals += 'DEPLOY_ENVELOPE_WRONG_KIT_VERSION' }
if ($deploy.evidence_kind -ne 'deploy') { $refusals += 'DEPLOY_ENVELOPE_WRONG_EVIDENCE_KIND' }
if ($deploy.run_id -ne $RunId) { $refusals += 'DEPLOY_ENVELOPE_RUN_ID_MISMATCH' }
if ($deploy.product_sha -ne $K.ProductSha) { $refusals += 'DEPLOY_ENVELOPE_PRODUCT_SHA_MISMATCH' }
if ($deploy.target_day -ne $K.TargetDay) { $refusals += 'DEPLOY_ENVELOPE_TARGET_DAY_MISMATCH' }
if ([string]$deploy.generated_utc -notmatch '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$') {
    $refusals += 'DEPLOY_ENVELOPE_MALFORMED_TIMESTAMP'
}

# 1b. One approved kit revision, agreed by the checkout, the run and the evidence.
if ($KitSha -ne $ApprovedKitSha) { $refusals += 'KIT_CHECKOUT_IS_NOT_APPROVED' }
if ($run.approved_kit_sha -ne $ApprovedKitSha) { $refusals += 'RUN_KIT_SHA_IS_NOT_APPROVED' }
if ($deploy.kit_sha -ne $ApprovedKitSha) { $refusals += 'DEPLOY_KIT_SHA_IS_NOT_APPROVED' }

# 1c. The recorded targets must EQUAL the constants, not merely sit inside them.
if (-not (Test-PilotPathEquals -Left ([string]$payload.staging_root) -Right $K.StagingRoot)) {
    $refusals += 'RECORDED_STAGING_ROOT_IS_NOT_THE_STAGING_ROOT'
}
if (-not (Test-PilotPathEquals -Left ([string]$payload.staging_db) -Right $K.StagingDb)) {
    $refusals += 'RECORDED_STAGING_DB_IS_NOT_THE_STAGING_DATABASE'
}
Assert-PilotServiceIsNotProduction -Name $payload.staging_service

# 1d. The backup must live in THIS run's backup directory.
$runBackupDir = Join-Path $run.run_root 'backup'
if (-not (Test-PilotPathWithin -Path ([string]$payload.backup_path) -Root $runBackupDir)) {
    $refusals += 'BACKUP_IS_NOT_IN_THIS_RUNS_BACKUP_DIRECTORY'
}
Assert-PilotNotProduction -Path $payload.backup_path -What 'recorded backup path'

# 1e. The phase must be one this kit knows how to roll back from.
$rollbackablePhases = @('backed-up', 'code-updated', 'migration-failed',
                        'verification-failed', 'service-did-not-start',
                        'smoke-test-failed', 'done')
if ($rollbackablePhases -notcontains [string]$payload.phase) {
    $refusals += "DEPLOY_PHASE_IS_NOT_ROLLBACKABLE:$($payload.phase)"
}

# 1f. The revision to restore must be a revision, and the recorded one.
if ([string]$payload.sha_before -notmatch '^[0-9a-f]{40}$') {
    $refusals += 'RECORDED_SHA_BEFORE_IS_NOT_A_REVISION'
} else {
    $exists = Invoke-PilotGit -Repo $K.StagingRoot -AllowFailure `
        -Arguments @('cat-file', '-e', ([string]$payload.sha_before + '^{commit}'))
    if ($exists.ExitCode -ne 0) { $refusals += 'RECORDED_SHA_BEFORE_IS_NOT_IN_THE_CHECKOUT' }
}
$preflightPath = Get-PilotEvidencePath -RunsRoot $RunsRoot -RunId $RunId -Name 'preflight'
if (Test-Path -LiteralPath $preflightPath) {
    $preflight = Read-PilotJson -Path $preflightPath
    if ($preflight.payload.staging_sha_before -and
        $preflight.payload.staging_sha_before -ne $payload.sha_before) {
        $refusals += 'SHA_BEFORE_DISAGREES_WITH_THE_PREFLIGHT_RECORD'
    }
}

# 1g. The staging working tree must be clean: a rollback over local edits
#     would silently discard them.
if (-not ((Invoke-PilotGit -Repo $K.StagingRoot -Arguments @('status', '--porcelain')).Output -eq '')) {
    $refusals += 'STAGING_WORKTREE_IS_DIRTY'
}

if ($refusals.Count -gt 0) {
    foreach ($reason in $refusals) { Write-Output "  - $reason" }
    throw "REFUSED before touching the service: $($refusals -join ', '). Nothing was stopped, moved or overwritten."
}
Write-Output "ROLLBACK_PRECONDITIONS=PASS"

$stagingService = [string]$payload.staging_service
$runbook = Join-Path $KitCheckout 'docs\ORG_WINDOWS_SERVER_STAGING_RUNBOOK.md'
$resolved = Resolve-PilotStagingService -RunbookPath $runbook
if ($resolved -ne $stagingService) {
    throw "REFUSED: the evidence names service '$stagingService' but this machine resolves staging to '$resolved'. Nothing was stopped."
}
Write-Output "STAGING_SERVICE=$stagingService"
Write-Output "SHA_TO_RESTORE=$($payload.sha_before)"
Write-Output "BACKUP_TO_RESTORE=$($payload.backup_path)"

# --- 2. Verify the backup BEFORE stopping anything --------------------------
if (-not (Test-Path -LiteralPath $payload.backup_path)) {
    throw "REFUSED: the backup named by the evidence is gone: $($payload.backup_path)"
}
$sha = Get-PilotFileSha256 -Path $payload.backup_path
if ($sha -ne $payload.backup_sha256) {
    throw "REFUSED: the backup file changed since it was taken. Evidence sha256 $($payload.backup_sha256), file now $sha. Nothing was restored."
}
Write-Output "BACKUP_SHA256_MATCHES=True"

$probe = Join-Path $PSScriptRoot 'pilot_db_probe.py'
$backupState = (Invoke-PilotProbe -Python $Python -Script $probe -RunId $RunId -KitSha $KitSha `
    -OutFile (Join-Path $run.run_root 'evidence\rollback_backup_integrity.json') `
    -AllowedExitCodes @(0, 3) `
    -Arguments @('integrity', '--db', $payload.backup_path,
                 '--require', 'integrity')).Evidence
if (-not $backupState.payload.integrity.integrity_ok) {
    throw "REFUSED: PRAGMA integrity_check on the backup did not return ok. Nothing was restored."
}
Write-Output "BACKUP_INTEGRITY=ok"

# --- 3. Confirmation --------------------------------------------------------
if (-not $Force) {
    Write-Output ""
    Write-Output "This OVERWRITES $($payload.staging_db) with the backup above"
    Write-Output "and returns $($payload.staging_root) to $($payload.sha_before)."
    $answer = Read-Host "Type ROLLBACK to continue"
    if ($answer -ne 'ROLLBACK') {
        Write-Output "Nothing was done."
        exit 1
    }
}

# --- 4. Stop ONLY the staging service ---------------------------------------
Assert-PilotServiceIsNotProduction -Name $stagingService
Stop-Service -Name $stagingService -Force
$deadline = (Get-Date).AddSeconds($ServiceTimeoutSeconds)
while ((Get-Service -Name $stagingService).Status -ne 'Stopped' -and (Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
}
if ((Get-Service -Name $stagingService).Status -ne 'Stopped') {
    throw "REFUSED: $stagingService did not stop. NOTHING was restored."
}
Write-Output "STAGING_SERVICE_STATUS=Stopped"

# --- 5. Restore the database -------------------------------------------------
# [REASON]: the sidecar -wal and -shm files are moved aside, not left in place.
# A restored .db next to the WAL of a NEWER database is not the database that
# was backed up: SQLite would replay that WAL over it on the next open.
$aside = "{0}.superseded_{1}" -f $payload.staging_db, (Get-Date).ToString('yyyyMMdd_HHmmss')
Move-Item -LiteralPath $payload.staging_db -Destination $aside -Force
Write-Output "SUPERSEDED_DB=$aside"
foreach ($suffix in @('-wal', '-shm')) {
    $sidecar = "$($payload.staging_db)$suffix"
    if (Test-Path -LiteralPath $sidecar) {
        Move-Item -LiteralPath $sidecar -Destination "$aside$suffix" -Force
        Write-Output "SUPERSEDED_SIDECAR=$aside$suffix"
    }
}
Copy-Item -LiteralPath $payload.backup_path -Destination $payload.staging_db -Force
$restoredSha = Get-PilotFileSha256 -Path $payload.staging_db
if ($restoredSha -ne $payload.backup_sha256) {
    throw "REFUSED: the restored database does not match the backup (sha256 $restoredSha). The previous file is kept at $aside."
}
Write-Output "RESTORED_SHA256_MATCHES=True"

# --- 6. Return the checkout to the recorded sha ------------------------------
$currentSha = Get-PilotHeadSha -Repo $payload.staging_root
if ($currentSha -ne $payload.sha_before) {
    Invoke-PilotGit -Repo $payload.staging_root -Arguments @('checkout', '--detach', $payload.sha_before) | Out-Null
}
$nowSha = Get-PilotHeadSha -Repo $payload.staging_root
if ($nowSha -ne $payload.sha_before) {
    throw "REFUSED: the checkout is at $nowSha, not at the recorded $($payload.sha_before)."
}
Write-Output "SHA_NOW=$nowSha"
Write-Output "TO_GO_FORWARD_AGAIN=git -C $($payload.staging_root) checkout $($payload.branch_before)"

# --- 7. Start and smoke-test --------------------------------------------------
Assert-PilotServiceIsNotProduction -Name $stagingService
Start-Service -Name $stagingService
$deadline = (Get-Date).AddSeconds($ServiceTimeoutSeconds)
while ((Get-Service -Name $stagingService).Status -ne 'Running' -and (Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
}
if ((Get-Service -Name $stagingService).Status -ne 'Running') {
    throw "REFUSED: $stagingService did not start after the rollback."
}

$smoke = Invoke-PilotSmokeTest -BaseUrl $K.StagingUrl
Write-Output "SMOKE_TEST $($smoke.path) status=$($smoke.status) ok=$($smoke.ok) reason=$($smoke.reason)"
if (-not $smoke.ok) {
    throw "REFUSED: the staging smoke test did not pass after the rollback ($($smoke.reason))."
}

$productionStatus = (Get-Service -Name $K.ProductionService -ErrorAction SilentlyContinue)
if ($productionStatus) {
    Write-Output "PRODUCTION_SERVICE_STATUS=$($productionStatus.Status) (untouched)"
}

Set-PilotRunStep -RunsRoot $RunsRoot -RunId $RunId -Step 'rollback' -Value ([ordered]@{
    completed_utc = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    restored_from = $payload.backup_path
    sha_now       = $nowSha
}) | Out-Null

Write-Output ""
Write-Output "STAGING_ROLLBACK=PASS"
