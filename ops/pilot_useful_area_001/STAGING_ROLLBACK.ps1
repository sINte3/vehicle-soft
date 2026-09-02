<#
    STAGING_ROLLBACK.ps1
    DRONE-USEFUL-AREA-PILOT-001, the way back.

    WHAT THIS DOES
      Reads the manifest STAGING_DEPLOY_AND_MIGRATE.ps1 wrote, verifies the
      backup it names (sha256 and PRAGMA integrity_check), stops ONLY the
      staging service, restores the staging database, returns the staging
      checkout to the sha it had before the deploy, starts the staging service
      and smoke-tests it.

      NO FILE NAME IS TYPED BY HAND. The backup comes from the manifest.

    WHAT THIS NEVER DOES
      * touches C:\transport-report or the TransportReport service;
      * uses `git reset --hard` -- the project charter forbids it as a
        rollback. The checkout returns to the recorded sha with a DETACHED
        checkout, which restores exactly that tree while moving no branch ref
        and rewriting no history. Going forward again is `git checkout <branch>`
        and the manifest names the branch;
      * deletes a backup, or any production data.

    RUN (on the server):
      Set-Location C:\transport-report-staging
      .\ops\pilot_useful_area_001\STAGING_ROLLBACK.ps1 -Force

    Without -Force it asks for a typed confirmation: this overwrites the
    staging database.
#>

[CmdletBinding()]
param(
    [string]$ExpectedHost = 'srv-yoqsh',
    [string]$ManifestPath = 'D:\transport-report-backups\pilot\DRONE-USEFUL-AREA-001\manifests\latest.json',
    [string]$Python = 'C:\Program Files\Python314\python.exe',
    [int]$ServiceTimeoutSeconds = 90,
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Import-Module (Join-Path $PSScriptRoot 'PilotKit.psm1') -Force
$K = Get-PilotConstants

Write-Output "=== DRONE-USEFUL-AREA-PILOT-001 / STAGING ROLLBACK ==="

Assert-PilotHost -Expected $ExpectedHost
Assert-PilotNotProduction -Path $ManifestPath -What 'manifest path'

$manifest = Read-PilotJson -Path $ManifestPath
$payload = $manifest.payload
Write-Output "MANIFEST=$ManifestPath"
Write-Output "MANIFEST_PHASE=$($manifest.phase)"

# --- 1. The manifest must describe STAGING and nothing else -----------------
Assert-PilotStagingPath -Path $payload.staging_root -What 'manifest staging root'
Assert-PilotStagingPath -Path $payload.staging_db -What 'manifest staging database'
Assert-PilotServiceIsNotProduction -Name $payload.staging_service
Assert-PilotNotProduction -Path $payload.backup_path -What 'manifest backup path'

$stagingService = [string]$payload.staging_service
$runbook = Join-Path $K.StagingRoot 'docs\ORG_WINDOWS_SERVER_STAGING_RUNBOOK.md'
$resolved = Resolve-PilotStagingService -RunbookPath $runbook
if ($resolved -ne $stagingService) {
    throw "REFUSED: the manifest names service '$stagingService' but this machine resolves staging to '$resolved'. Nothing was stopped."
}
Write-Output "STAGING_SERVICE=$stagingService"
Write-Output "SHA_TO_RESTORE=$($payload.sha_before)"
Write-Output "BACKUP_TO_RESTORE=$($payload.backup_path)"

# --- 2. Verify the backup BEFORE stopping anything --------------------------
if (-not (Test-Path -LiteralPath $payload.backup_path)) {
    throw "REFUSED: the backup named by the manifest is gone: $($payload.backup_path)"
}
$sha = Get-PilotFileSha256 -Path $payload.backup_path
if ($sha -ne $payload.backup_sha256) {
    throw "REFUSED: the backup file changed since it was taken. Manifest sha256 $($payload.backup_sha256), file now $sha. Nothing was restored."
}
Write-Output "BACKUP_SHA256_MATCHES=True"

$probe = Join-Path $PSScriptRoot 'pilot_db_probe.py'
$check = Join-Path ([System.IO.Path]::GetTempPath()) ("pilot_rollback_backup_{0}.json" -f (Get-Date).ToString('yyyyMMddHHmmss'))
Invoke-PilotPython -Python $Python -StdoutPath $check -Arguments @(
    $probe, 'integrity', '--db', $payload.backup_path, '--require', 'integrity') | Out-Null
$backupState = Read-PilotJson -Path $check
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

Assert-PilotStagingUrl -Url $K.StagingUrl
$smokeStatus = $null
for ($attempt = 1; $attempt -le 10; $attempt++) {
    try {
        $response = Invoke-WebRequest -Uri $K.StagingUrl -UseBasicParsing -TimeoutSec 15
        $smokeStatus = [int]$response.StatusCode
        break
    } catch {
        if ($_.Exception.PSObject.Properties.Name -contains 'Response' -and $_.Exception.Response) {
            $smokeStatus = [int]$_.Exception.Response.StatusCode
            break
        }
        Start-Sleep -Seconds 3
    }
}
Write-Output "SMOKE_TEST_STATUS=$smokeStatus"
if ($null -eq $smokeStatus -or $smokeStatus -ge 500) {
    throw "REFUSED: $($K.StagingUrl) did not answer after the rollback (status '$smokeStatus')."
}

$productionStatus = (Get-Service -Name $K.ProductionService -ErrorAction SilentlyContinue)
if ($productionStatus) {
    Write-Output "PRODUCTION_SERVICE_STATUS=$($productionStatus.Status) (untouched)"
}

Write-Output ""
Write-Output "STAGING_ROLLBACK=PASS"
Write-Output "Return to the owner: this console output."
