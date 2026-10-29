# tests/fin_recon_002_release_harness.ps1 -- runs ONE block of
# docs/FIN_RECON_002_RELEASE_V132_RUNBOOK.md (step 2, step 3, the restore
# block or the rollback block) against stand-ins for git, the Windows
# services, the drives, python, the backup script and the web site.
#
# Called by tests/test_fin_recon_002_release_runbook.py (class
# ReleaseV132BlocksRunInPowerShell), which first replaces every path, service
# name and the site in the block with temporary ones. Nothing here can reach a
# real service, repository or database: the stand-ins are functions, and
# functions win over cmdlets and programs of the same name inside this script
# and the block it runs. The same layout as tests/agro_work_release_harness.ps1.
#
# What is under test is the block's own control flow -- which stop starts the
# services and which does not -- so the tools answer with the lines the block
# waits for (the suite checks those lines against the real tools separately).
# A tool that "writes" appends to the temporary database file and leaves a
# -wal file beside it, the way an interrupted SQLite writer would; the restore
# block has to move both aside.
#
# Scenario.FailAt names the one tool that fails: rehearsal, backup, merge,
# untracked, payer, link, decisions, import, registry, release_check (or
# nothing). Scenario.Main is the top of origin/main and Scenario.History the
# commits in the order they were made: the release is pinned, main may be
# ahead of it -- or, in a scenario, may not contain it at all.
#
# ASCII only, Windows PowerShell 5.1 and PowerShell 7 alike.
param(
  [Parameter(Mandatory = $true)][string]$BlockFile,
  [Parameter(Mandatory = $true)][string]$ScenarioFile,
  [Parameter(Mandatory = $true)][string]$CallsFile
)
$ErrorActionPreference = 'Continue'
$global:scenario = Get-Content -LiteralPath $ScenarioFile -Raw | ConvertFrom-Json
$global:calls = New-Object System.Collections.ArrayList
$global:state = @{ Head = [string]$global:scenario.Head; CloneHead = ''; Applied = 0; Merged = $false; Services = @{} }
foreach ($name in @($global:scenario.Services)) { $global:state.Services[[string]$name] = [string]$global:scenario.ServiceStatus }
$global:clock = [datetime]'2026-10-30T10:00:00'

function Add-Call([string]$text) { [void]$global:calls.Add($text) }

function Set-Answer([int]$code, [string[]]$lines) {
  $global:LASTEXITCODE = $code
  return $lines
}

function hostname { return [string]$global:scenario.Host }

function Get-Date {
  [CmdletBinding()]
  param([string]$Format)
  $global:clock = $global:clock.AddSeconds(5)
  if ($Format) { return $global:clock.ToString($Format) }
  return $global:clock
}

function Start-Sleep {
  [CmdletBinding()]
  param([int]$Seconds)
}

# Get-FileHash of Windows PowerShell 5.1 is itself a script function that calls
# Test-Path -PathType, so the stand-in passes every parameter through.
function Test-Path {
  [CmdletBinding()]
  param([string[]]$LiteralPath, [string[]]$Path, [string]$PathType)
  if (@($LiteralPath) -like 'Invoke-Fake*') { return $true }
  return (Microsoft.PowerShell.Management\Test-Path @PSBoundParameters)
}

function Copy-Item {
  [CmdletBinding()]
  param([string]$LiteralPath, [string]$Destination)
  Add-Call 'copy'
  if ($global:scenario.CopyCorrupts) {
    [System.IO.File]::WriteAllText($Destination, 'not the backup')
    return
  }
  Microsoft.PowerShell.Management\Copy-Item -LiteralPath $LiteralPath -Destination $Destination -ErrorAction Stop
}

function Get-Service {
  [CmdletBinding()]
  param([string[]]$Name)
  foreach ($one in $Name) {
    if ($global:state.Services.ContainsKey($one)) {
      [pscustomobject]@{ Name = $one; Status = $global:state.Services[$one] }
    }
  }
}

function Stop-Service {
  [CmdletBinding()]
  param([string]$Name)
  Add-Call "Stop-Service $Name"
  $global:state.Services[$Name] = 'Stopped'
}

function Restart-Service {
  [CmdletBinding()]
  param([string]$Name)
  Add-Call "Restart-Service $Name"
  $global:state.Services[$Name] = 'Running'
}

function Start-Service {
  [CmdletBinding()]
  param([string]$Name)
  Add-Call "Start-Service $Name"
  $global:state.Services[$Name] = 'Running'
}

function Get-PSDrive {
  [CmdletBinding()]
  param([string]$Name)
  return [pscustomobject]@{ Name = $Name; Free = [long]1000000000000 }
}

function Invoke-WebRequest {
  [CmdletBinding()]
  param([string]$Uri, [switch]$UseBasicParsing, [int]$TimeoutSec)
  $path = $Uri -replace '^https?://[^/]+', ''
  Add-Call "GET $path"
  return [pscustomobject]@{ StatusCode = 200; Content = '<form method="post" class="vs-login-form">' }
}

function git {
  $a = @($args | ForEach-Object { [string]$_ })
  $dir = ''
  if ($a[0] -eq '-C') { $dir = $a[1]; $a = @($a | Select-Object -Skip 2) }
  if ($a[0] -eq '--no-pager') { $a = @($a | Select-Object -Skip 1) }
  Add-Call ('git ' + ($a -join ' '))
  $s = $global:scenario
  $global:LASTEXITCODE = 0
  switch ($a[0]) {
    'fetch' { return }
    'rev-parse' {
      if ($a -contains 'origin/main') { return [string]$s.Main }
      if ($dir -like '*fr002_src_*') { return $global:state.CloneHead }
      if ($a -contains '--short') { return $global:state.Head.Substring(0, 7) }
      return $global:state.Head
    }
    'clone' { return }
    'checkout' {
      $global:state.CloneHead = [string]$a[-1]
      return
    }
    'log' { return @($s.Later | ForEach-Object { [string]$_ }) }
    'status' {
      if ($a -contains '--untracked-files=no') { return @() }
      if ($global:state.Merged -and ($s.FailAt -eq 'untracked')) { return @('?? notes.txt', '?? tools/onec_export.ps1') }
      return @('?? notes.txt')
    }
    'merge-base' {
      # --is-ancestor A B: A was made before B in Scenario.History.
      $order = @($s.History | ForEach-Object { [string]$_ })
      $from = [array]::IndexOf($order, $a[2])
      $to = [array]::IndexOf($order, $a[3])
      if (($from -ge 0) -and ($to -ge 0) -and ($from -le $to)) { $global:LASTEXITCODE = 0 } else { $global:LASTEXITCODE = 1 }
      return
    }
    'diff' {
      if ($a -contains [string]$s.Reviewed) { return @('docs/FIN_RECON_002_RELEASE_V132_RUNBOOK.md') }
      return @('drones.py', 'migrate_drones_works_merged_customer_link_001.py', 'migrate_drones_works_payer_unidentified_001.py')
    }
    'show' { return @($s.GateLines | ForEach-Object { [string]$_ }) }
    'merge' {
      if ($s.FailAt -eq 'merge') {
        $global:LASTEXITCODE = 1
        return 'error: The following untracked working tree files would be overwritten by merge'
      }
      # git merge --ff-only X moves the code to X, whatever X is.
      $global:state.Head = [string]$a[-1]
      $global:state.Merged = $true
      return 'Fast-forward'
    }
    'reset' {
      $global:state.Head = [string]$a[-1]
      return
    }
    default {
      $global:LASTEXITCODE = 99
      return "unexpected git call: $($a -join ' ')"
    }
  }
}

function Write-Database([string[]]$a) {
  # A tool that writes: the database file grows and a -wal file is left.
  $at = [array]::IndexOf($a, '--db')
  $path = $a[$at + 1]
  Add-Content -LiteralPath $path -Value 'written by the window'
  Add-Content -LiteralPath ($path + '-wal') -Value 'wal of the window'
}

function Get-DriftLines {
  $s = $global:scenario
  $registered = 61
  $noFile = @()
  if ($global:state.Merged) {
    $registered = 61 + $global:state.Applied
    if ($s.FailAt -eq 'registry') { $registered = $registered - 1 }
    $noFile = @($s.NoFile | Select-Object -First ([math]::Max(0, $global:state.Applied - 1)))
  }
  $lines = @("registered migrations: $registered; migrate_*.py files: 70 (+0 known, 0 under the scope)", '', 'file-but-not-registered: 0', "registered-but-no-file: $($noFile.Count)")
  foreach ($name in $noFile) { $lines += "  - $name" }
  return $lines
}

function Invoke-FakePython {
  $a = @($args | ForEach-Object { [string]$_ })
  Add-Call ('python ' + ($a -join ' '))
  $s = $global:scenario
  $tool = $a[0]
  if ($tool -like '*check_db_lock*') { return (Set-Answer 0 @('CLEAN: no -wal/-shm, nobody holds the database')) }
  if ($tool -like '*check_migration_drift*') { return (Set-Answer 0 (Get-DriftLines)) }
  if ($tool -like 'migrate_drones_works_payer_unidentified_001.py') {
    if ($s.FailAt -eq 'payer') { Write-Database $a; return (Set-Answer 1 @('Traceback (most recent call last):', 'sqlite3.OperationalError: disk I/O error')) }
    Write-Database $a
    $global:state.Applied = $global:state.Applied + 1
    return (Set-Answer 0 @('Done. 1 column added to drone_works, no row backfilled.'))
  }
  if ($tool -like 'migrate_drones_works_merged_customer_link_001.py') {
    if (($s.FailAt -eq 'link') -and ($a -contains 'june-anchor')) { return (Set-Answer 1 @('RESULT: NOT APPLIED')) }
    Write-Database $a
    $global:state.Applied = $global:state.Applied + 1
    return (Set-Answer 0 @('RESULT: APPLIED'))
  }
  if ($tool -like '*drone_alias_decisions*') {
    if ($s.FailAt -eq 'decisions') { return (Set-Answer 1 @('RESULT: NOT APPLIED')) }
    Write-Database $a
    $global:state.Applied = $global:state.Applied + 1
    return (Set-Answer 0 @('RESULT: APPLIED'))
  }
  if ($tool -like '*import_drone_works*') {
    if ($s.FailAt -eq 'import') { return (Set-Answer 1 @('Traceback (most recent call last):', 'sqlite3.OperationalError: database disk image is malformed')) }
    Write-Database $a
    return (Set-Answer 0 @('WRITTEN: 0 customers created, 132 works inserted, 0 already present and skipped.'))
  }
  if ($tool -like '*fin_recon_002_staging_check*') {
    if ($s.FailAt -eq 'rehearsal') { return (Set-Answer 1 @('FAIL  May byte-identical', 'RESULT: FAIL (1 failed). Report: STAGING_CHECK_REPORT.txt')) }
    return (Set-Answer 0 @('RESULT: PASS (0 failed). Report: STAGING_CHECK_REPORT.txt'))
  }
  if ($tool -like '*fin_recon_002_release_check*') {
    if ($s.FailAt -eq 'release_check') { return (Set-Answer 1 @('FAIL  May byte-identical', 'RESULT: RELEASE CHECK FAILED (1 failed)')) }
    return (Set-Answer 0 @('RESULT: RELEASE CHECK PASSED (0 failed)'))
  }
  $global:LASTEXITCODE = 99
  return "unexpected python call: $($a -join ' ')"
}

function Invoke-FakeBackup {
  Add-Call 'backup'
  $s = $global:scenario
  if ($s.FailAt -eq 'backup') { return (Set-Answer 1 @('ERROR: Source database not found.', 'Backup FAILED.')) }
  Microsoft.PowerShell.Management\Copy-Item -LiteralPath ([string]$s.Db) -Destination ([string]$s.BackupFile)
  $size = '{0:N0}' -f (Get-Item -LiteralPath ([string]$s.BackupFile)).Length
  return (Set-Answer 0 @(" Source size : $size bytes", " Dest size   : $size bytes", ' Integrity check : ok', 'SUCCESS: Backup written to:', "  $([string]$s.BackupFile)", 'Backup completed successfully.'))
}

$text = Get-Content -LiteralPath $BlockFile -Raw
$block = [scriptblock]::Create($text)
. $block
[System.IO.File]::WriteAllLines($CallsFile, [string[]]@($global:calls))
