# tests/agro_work_release_harness.ps1 -- runs ONE block of
# docs/AGRO_WORK_RELEASE_RUNBOOK.md against stand-ins for git, the Windows
# services, python, the backup script and the web site.
#
# Called by tests/test_agro_work_runbook.py (class ReleaseBlocksInPowerShell),
# which first replaces every path, service name and the site in the block with
# temporary ones. Nothing here can reach a real service, repository or
# database: the stand-ins are functions, and functions win over cmdlets and
# programs of the same name inside this script and the block it runs.
#
# The outputs the stand-ins replay were produced by the REAL tools
# (check_migration_drift.py, check_db_lock.py, backup_transport_db.py,
# migrate_agro_work_001.py) on temporary databases; the test passes them in
# the scenario file.
#
# ASCII only, Windows PowerShell 5.1 and PowerShell 7 alike: the server runs
# 5.1, CI runs the same scenarios on both.
param(
  [Parameter(Mandatory = $true)][string]$BlockFile,
  [Parameter(Mandatory = $true)][string]$ScenarioFile,
  [Parameter(Mandatory = $true)][string]$CallsFile
)
$ErrorActionPreference = 'Continue'
$global:scenario = Get-Content -LiteralPath $ScenarioFile -Raw | ConvertFrom-Json
$global:calls = New-Object System.Collections.ArrayList
$global:state = @{ Head = [string]$global:scenario.Head; Migrated = $false; MigrateRuns = 0; Services = @{} }
foreach ($name in @($global:scenario.Services)) { $global:state.Services[[string]$name] = 'Running' }
$global:clock = [datetime]'2026-10-01T10:00:00'

function Add-Call([string]$text) { [void]$global:calls.Add($text) }

function Send-Output($entry) {
  $global:LASTEXITCODE = [int]$entry.code
  return @($entry.lines | ForEach-Object { [string]$_ })
}

function hostname { return [string]$global:scenario.Host }

function Get-Date {
  $global:clock = $global:clock.AddSeconds(5)
  return $global:clock
}

function Start-Sleep {
  [CmdletBinding()]
  param([int]$Seconds)
}

function Test-Path {
  [CmdletBinding()]
  param([string]$LiteralPath)
  if ($LiteralPath -like 'Invoke-Fake*') { return $true }
  return (Microsoft.PowerShell.Management\Test-Path -LiteralPath $LiteralPath)
}

function Get-PSDrive {
  [CmdletBinding()]
  param([string]$Name)
  if ($null -eq $global:scenario.FreeBytes) { return $null }
  return [pscustomobject]@{ Name = $Name; Free = [long]$global:scenario.FreeBytes }
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
  if (@($global:scenario.StopFails) -notcontains $Name) { $global:state.Services[$Name] = 'Stopped' }
}

function Restart-Service {
  [CmdletBinding()]
  param([string]$Name)
  Add-Call "Restart-Service $Name"
  $global:state.Services[$Name] = 'Running'
  if ($global:scenario.ErrorLogAfterStart -and ($Name -eq [string]@($global:scenario.Services)[0])) {
    Add-Content -LiteralPath ([string]$global:scenario.ErrorLog) -Value ([string]$global:scenario.ErrorLogAfterStart)
  }
}

function Start-Service {
  [CmdletBinding()]
  param([string]$Name)
  Add-Call "Start-Service $Name"
}

function Invoke-WebRequest {
  [CmdletBinding()]
  param([string]$Uri, [switch]$UseBasicParsing, [int]$TimeoutSec)
  $path = $Uri -replace '^https?://[^/]+', ''
  Add-Call "GET $path"
  $answer = $global:scenario.Web.$path
  if ($null -eq $answer) { throw "no answer for $path" }
  if ([int]$answer.Status -ne 200) { throw "HTTP $($answer.Status)" }
  return [pscustomobject]@{ StatusCode = [int]$answer.Status; Content = [string]$answer.Body }
}

function git {
  $a = @($args | ForEach-Object { [string]$_ })
  if ($a[0] -eq '-C') { $a = @($a | Select-Object -Skip 2) }
  if ($a[0] -eq '--no-pager') { $a = @($a | Select-Object -Skip 1) }
  Add-Call ('git ' + ($a -join ' '))
  $s = $global:scenario
  $global:LASTEXITCODE = 0
  switch ($a[0]) {
    'fetch' { $global:LASTEXITCODE = [int]$s.FetchCode; return }
    'rev-parse' {
      if ($a -contains 'origin/main') { return [string]$s.Release }
      if ($a -contains '--short') { return $global:state.Head.Substring(0, 7) }
      return $global:state.Head
    }
    'log' {
      if ($a -contains '-1') {
        if ($a -contains 'HEAD') { return [string]$s.HeadSubject }
        return [string]$s.Top
      }
      return @('Merge pull request #151 from sINte3/claude/elegant-edison-zgmpbb', 'Merge origin/main into the branch', 'Merge pull request #152 from sINte3/claude/elegant-edison-zgmpbb')
    }
    'status' {
      if ($a -contains '--untracked-files=no') { return @($s.Modified | ForEach-Object { [string]$_ }) }
      return @('?? notes.txt', '?? screenshots/one.png')
    }
    'merge-base' { $global:LASTEXITCODE = [int]$s.AncestorCode; return }
    'diff' { return @($s.DiffNames | ForEach-Object { [string]$_ }) }
    'merge' {
      $global:LASTEXITCODE = [int]$s.MergeCode
      if ([int]$s.MergeCode -eq 0) {
        $global:state.Head = [string]$s.Release
        return 'Fast-forward'
      }
      return 'error: The following untracked working tree files would be overwritten by merge'
    }
    'reset' {
      $global:LASTEXITCODE = [int]$s.ResetCode
      if ([int]$s.ResetCode -eq 0) { $global:state.Head = [string]$a[-1] }
      return
    }
    default {
      $global:LASTEXITCODE = 99
      return "unexpected git call: $($a -join ' ')"
    }
  }
}

function Invoke-FakePython {
  $a = @($args | ForEach-Object { [string]$_ })
  Add-Call ('python ' + ($a -join ' '))
  $o = $global:scenario.Outputs
  if ($a[0] -like '*check_db_lock*') { return (Send-Output $o.('lock_' + [string]$global:scenario.LockKind)) }
  if ($a[0] -like '*check_migration_drift*') {
    if ($global:state.Head -ne [string]$global:scenario.Release) { return (Send-Output $o.drift_before) }
    if ($global:state.Migrated) { return (Send-Output $o.drift_migrated) }
    return (Send-Output $o.drift_merged)
  }
  if ($a[0] -eq 'migrate_agro_work_001.py') {
    $global:state.MigrateRuns = $global:state.MigrateRuns + 1
    if ($global:state.MigrateRuns -eq 1) { $kind = [string]$global:scenario.MigrateFirst } else { $kind = [string]$global:scenario.MigrateSecond }
    if ($kind -eq 'done') { $global:state.Migrated = $true }
    return (Send-Output $o.('migrate_' + $kind))
  }
  $global:LASTEXITCODE = 99
  return "unexpected python call: $($a -join ' ')"
}

function Invoke-FakeBackup {
  Add-Call 'backup'
  return (Send-Output $global:scenario.Outputs.('backup_' + [string]$global:scenario.BackupKind))
}

$text = Get-Content -LiteralPath $BlockFile -Raw
$block = [scriptblock]::Create($text)
. $block
# WriteAllLines, not Set-Content: a block that stops at its first check makes
# no call at all, and an empty list must still leave an (empty) file.
[System.IO.File]::WriteAllLines($CallsFile, [string[]]@($global:calls))
