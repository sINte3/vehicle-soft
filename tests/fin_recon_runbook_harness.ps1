# tests/fin_recon_runbook_harness.ps1 -- runs ONE block of
# docs/FIN_RECON_STORAGE_001_RUNBOOK.md with stand-ins for what only the
# Windows server has: the host name, the services, the TCP listeners and the
# addresses they listen on, the clone process, the folder rights (icacls and
# Get-Acl), sc.exe, the disks and the web site.
#
# Called by tests/test_fin_recon_runbook.py, which first points every
# constant of the block (folders, python, repository, commit, hashes, period)
# at temporary ones. git and python stay REAL: the block clones a synthetic
# repository, runs the real migration, import, verification, backup and
# restore test on a synthetic package, and reads synthetic production and
# staging checkouts. git calls are recorded on their way through, so the test
# can prove which subcommands touched which folder.
#
# The stand-ins keep the "server" (live processes, listeners, the folders
# whose rights icacls restricted) in a small text file, so blocks run one
# after another in separate processes see each other's effects, as the
# owner's console would. Functions win over cmdlets and programs of the same
# name inside this script and the block it runs, so nothing here can reach a
# real service, port, firewall or folder right.
#
# Folder rights, as Get-Acl shows them by SID on the server: a folder that
# icacls restricted is protected and grants Administrators (S-1-5-32-544) and
# SYSTEM (S-1-5-18) full control of everything inside; everything below it
# inherits exactly that; any other folder or file also grants Users
# (S-1-5-32-545) and Authenticated Users (S-1-5-11), as the root of C: does.
# A scenario can make icacls fail on a folder (IcaclsFailPath), succeed there
# without effect (IcaclsIgnoredPath -- a share that ignores it), make the
# rights of a path unreadable (GetAclFails), set exactly what a path shows
# (AclOverride), or add an explicit grant for one more account on a path
# (AclForeign -- what Explorer's "Continue" does).
#
# The firewall cmdlets and netsh are tripwires: the clone listens on
# 127.0.0.1 only and no block may open a port, so a call is recorded as
# "FIREWALL ..." and the test requires that there is none.
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
$global:realGit = (Get-Command git -CommandType Application | Select-Object -First 1).Source
$global:serviceCalls = 0
$global:partitionCalls = 0

function Add-Call([string]$text) { [void]$global:calls.Add($text) }

function Get-NormalPath([string]$path) {
  return [System.IO.Path]::GetFullPath($path).TrimEnd([char[]]@('\', '/'))
}

function Test-SamePath([string]$path, $other) {
  if (-not $other) { return $false }
  return [string]::Equals($path, (Get-NormalPath ([string]$other)), [System.StringComparison]::OrdinalIgnoreCase)
}

function Test-Under([string]$path, [string]$root) {
  if ([string]::Equals($path, $root, [System.StringComparison]::OrdinalIgnoreCase)) { return $true }
  return $path.StartsWith($root + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)
}

function Read-ServerState {
  $server = @{ Processes = @(); Listeners = @(); Locked = @() }
  $path = [string]$global:scenario.StateFile
  if (Test-Path -LiteralPath $path) {
    foreach ($line in [System.IO.File]::ReadAllLines($path)) {
      $f = $line.Split(' ')
      if ($f[0] -eq 'process') {
        $server.Processes += , @{ Id = [int]$f[1]; Ticks = [long]$f[2] }
      } elseif ($f[0] -eq 'listen') {
        $server.Listeners += , @{ Port = [int]$f[1]; Owner = [int]$f[2]; Address = [string]$f[3] }
      } elseif ($f[0] -eq 'acl') {
        $server.Locked += $line.Substring(4)
      }
    }
  }
  return $server
}

function Write-ServerState($server) {
  $out = New-Object System.Collections.ArrayList
  foreach ($p in $server.Processes) { [void]$out.Add('process ' + $p.Id + ' ' + $p.Ticks) }
  foreach ($l in $server.Listeners) { [void]$out.Add('listen ' + $l.Port + ' ' + $l.Owner + ' ' + $l.Address) }
  foreach ($r in $server.Locked) { [void]$out.Add('acl ' + $r) }
  [System.IO.File]::WriteAllLines([string]$global:scenario.StateFile, [string[]]@($out))
}

function hostname { return [string]$global:scenario.Host }

function Start-Sleep {
  [CmdletBinding()]
  param([int]$Seconds)
}

function Get-Service {
  [CmdletBinding()]
  param([string[]]$Name)
  $global:serviceCalls = $global:serviceCalls + 1
  foreach ($s in @($global:scenario.Services)) {
    $status = [string]$s.Status
    if ($global:scenario.FlipFromCall -and ($global:serviceCalls -ge [int]$global:scenario.FlipFromCall) -and ([string]$s.Name -eq [string]$global:scenario.FlipService)) { $status = 'Stopped' }
    [pscustomobject]@{ Name = [string]$s.Name; Status = $status; StartType = [string]$s.StartType }
  }
}

function Get-NetTCPConnection {
  [CmdletBinding()]
  param([int]$LocalPort, [string]$State)
  foreach ($l in (Read-ServerState).Listeners) {
    if ($l.Port -eq $LocalPort) { [pscustomobject]@{ LocalAddress = $l.Address; LocalPort = $l.Port; OwningProcess = $l.Owner } }
  }
}

function New-NetFirewallRule { Add-Call ('FIREWALL New-NetFirewallRule ' + ($args -join ' ')) }
function Set-NetFirewallRule { Add-Call ('FIREWALL Set-NetFirewallRule ' + ($args -join ' ')) }
function Enable-NetFirewallRule { Add-Call ('FIREWALL Enable-NetFirewallRule ' + ($args -join ' ')) }
function Remove-NetFirewallRule { Add-Call ('FIREWALL Remove-NetFirewallRule ' + ($args -join ' ')) }
function Get-NetFirewallRule { Add-Call ('FIREWALL Get-NetFirewallRule ' + ($args -join ' ')) }
function netsh { Add-Call ('FIREWALL netsh ' + ($args -join ' ')) }
function netsh.exe { Add-Call ('FIREWALL netsh.exe ' + ($args -join ' ')) }

function Start-Process {
  [CmdletBinding()]
  param([string]$FilePath, [string[]]$ArgumentList, [string]$WorkingDirectory, [string]$WindowStyle, [switch]$PassThru)
  $seen = @()
  if ([Environment]::GetEnvironmentVariable('SECRET_KEY', 'Process') -cmatch '^[0-9A-F]{64}$') { $seen += 'SECRET_KEY=hex64' } else { $seen += 'SECRET_KEY=bad' }
  foreach ($name in @('PORT', 'HOST', 'FLASK_ENV')) { $seen += ($name + '=' + [Environment]::GetEnvironmentVariable($name, 'Process')) }
  foreach ($name in @('FUEL_API_TOKEN', 'DRONE_API_TOKEN', 'TG_BOT_TOKEN')) {
    if ([Environment]::GetEnvironmentVariable($name, 'Process')) { $seen += ($name + '=set') } else { $seen += ($name + '=unset') }
  }
  Add-Call ('Start-Process ' + $FilePath + ' ' + ($ArgumentList -join ' ') + ' cwd=' + $WorkingDirectory + ' window=' + $WindowStyle + ' passthru=' + [bool]$PassThru + ' ' + ($seen -join ' '))
  $newId = [int]$global:scenario.ClonePid
  $ticks = [long]$global:scenario.CloneTicks
  $exited = [bool]$global:scenario.StartExits
  # run_server.py listens on the address it is given in HOST; a scenario can
  # make the clone listen elsewhere, as an edited run_server.py would.
  $address = [string][Environment]::GetEnvironmentVariable('HOST', 'Process')
  if ($global:scenario.ListenAddress) { $address = [string]$global:scenario.ListenAddress }
  if (-not $exited) {
    $server = Read-ServerState
    $server.Processes += , @{ Id = $newId; Ticks = $ticks }
    if (-not $global:scenario.StartNoListen) {
      $server.Listeners += , @{ Port = [int][Environment]::GetEnvironmentVariable('PORT', 'Process'); Owner = $newId; Address = $address }
    }
    Write-ServerState $server
  }
  return [pscustomobject]@{ Id = $newId; HasExited = $exited; StartTime = (New-Object DateTime ($ticks, [DateTimeKind]::Utc)) }
}

function Get-Process {
  [CmdletBinding()]
  param([int]$Id)
  foreach ($p in (Read-ServerState).Processes) {
    if ($p.Id -eq $Id) { return [pscustomobject]@{ Id = $p.Id; HasExited = $false; StartTime = (New-Object DateTime ([long]$p.Ticks, [DateTimeKind]::Utc)) } }
  }
}

function Stop-Process {
  [CmdletBinding()]
  param([int]$Id, [switch]$Force)
  Add-Call ('Stop-Process ' + $Id)
  $server = Read-ServerState
  $server.Processes = @($server.Processes | Where-Object { $_.Id -ne $Id })
  $server.Listeners = @($server.Listeners | Where-Object { $_.Owner -ne $Id })
  Write-ServerState $server
}

function Invoke-WebRequest {
  [CmdletBinding()]
  param([string]$Uri, [switch]$UseBasicParsing, [int]$TimeoutSec)
  Add-Call ('GET ' + $Uri)
  $port = ([uri]$Uri).Port
  if (@((Read-ServerState).Listeners | Where-Object { $_.Port -eq $port }).Count -eq 0) { throw ('Unable to connect to the remote server: ' + $Uri) }
  $path = $Uri -replace '^https?://[^/]+', ''
  $answer = $global:scenario.Web.$path
  if ($null -eq $answer) { throw ('no answer for ' + $path) }
  if ([int]$answer.Status -ne 200) { throw ('The remote server returned an error: (' + $answer.Status + ')') }
  return [pscustomobject]@{ StatusCode = [int]$answer.Status; Content = [string]$answer.Body }
}

function icacls {
  $a = @($args | ForEach-Object { [string]$_ })
  Add-Call ('icacls ' + ($a -join ' '))
  $target = Get-NormalPath $a[0]
  if (Test-SamePath $target $global:scenario.IcaclsFailPath) {
    $global:LASTEXITCODE = 5
    $a[0] + ': Access is denied.'
    'Successfully processed 0 files; Failed processing 1 files'
    return
  }
  if (($a -contains '/inheritance:r') -and (-not (Test-SamePath $target $global:scenario.IcaclsIgnoredPath))) {
    $server = Read-ServerState
    if (@($server.Locked | Where-Object { [string]::Equals($_, $target, [System.StringComparison]::OrdinalIgnoreCase) }).Count -eq 0) {
      $server.Locked += $target
      Write-ServerState $server
    }
  }
  $global:LASTEXITCODE = 0
  'processed file: ' + $a[0]
  'Successfully processed 1 files; Failed processing 0 files'
}

function New-AclRule([string]$sid, [bool]$inherited) {
  return [pscustomobject]@{
    IdentityReference = [pscustomobject]@{ Value = $sid }
    FileSystemRights = 'FullControl'
    AccessControlType = 'Allow'
    InheritanceFlags = 'ContainerInherit, ObjectInherit'
    IsInherited = $inherited
  }
}

function Get-Acl {
  [CmdletBinding()]
  param([string]$LiteralPath)
  $path = Get-NormalPath $LiteralPath
  foreach ($failing in @($global:scenario.GetAclFails)) {
    if (Test-SamePath $path $failing) { throw 'Attempted to perform an unauthorized operation.' }
  }
  if (-not (Test-Path -LiteralPath $LiteralPath)) { throw ('Cannot find path ' + $LiteralPath + ' because it does not exist.') }
  $locked = @((Read-ServerState).Locked | Where-Object { Test-Under $path $_ })
  $protected = @($locked | Where-Object { [string]::Equals($_, $path, [System.StringComparison]::OrdinalIgnoreCase) }).Count -gt 0
  $rules = New-Object System.Collections.ArrayList
  if ($locked.Count -gt 0) {
    foreach ($sid in @('S-1-5-32-544', 'S-1-5-18')) { [void]$rules.Add((New-AclRule $sid (-not $protected))) }
  } else {
    foreach ($sid in @('S-1-5-32-544', 'S-1-5-18', 'S-1-5-32-545', 'S-1-5-11')) { [void]$rules.Add((New-AclRule $sid $true)) }
  }
  # AclOverride: the exact rights a path shows, whatever icacls did -- a root
  # that still inherits, or one where an account lacks full control.
  foreach ($override in @($global:scenario.AclOverride)) {
    if ($override -and (Test-SamePath $path $override.Path)) {
      $protected = [bool]$override.Protected
      $rules.Clear()
      foreach ($r in @($override.Rules)) {
        [void]$rules.Add([pscustomobject]@{ IdentityReference = [pscustomobject]@{ Value = [string]$r.Sid }; FileSystemRights = [string]$r.Rights; AccessControlType = 'Allow'; InheritanceFlags = [string]$r.Inheritance; IsInherited = [bool]$r.Inherited })
      }
    }
  }
  foreach ($extra in @($global:scenario.AclForeign)) {
    if ($extra -and (Test-SamePath $path $extra.Path)) { [void]$rules.Add((New-AclRule ([string]$extra.Sid) $false)) }
  }
  $acl = [pscustomobject]@{ Path = $path; AreAccessRulesProtected = $protected; Rules = $rules.ToArray() }
  $acl | Add-Member -MemberType ScriptMethod -Name GetAccessRules -Value { param($includeExplicit, $includeInherited, $targetType) return $this.Rules }
  return $acl
}

function sc.exe {
  Add-Call ('sc.exe ' + (@($args | ForEach-Object { [string]$_ }) -join ' '))
  $global:LASTEXITCODE = 0
  '[SC] QueryServiceConfig SUCCESS'
}

function Get-Partition {
  [CmdletBinding()]
  param([string]$DriveLetter)
  Add-Call ('Get-Partition ' + $DriveLetter)
  $disks = @($global:scenario.PartitionDisks)
  $index = [math]::Min($global:partitionCalls, $disks.Count - 1)
  $global:partitionCalls = $global:partitionCalls + 1
  return [pscustomobject]@{ DriveLetter = $DriveLetter; DiskNumber = [int]$disks[$index] }
}

function Get-Volume {
  [CmdletBinding()]
  param()
  foreach ($v in @($global:scenario.Volumes)) {
    [pscustomobject]@{ DriveLetter = [string]$v.DriveLetter; FileSystem = 'NTFS'; DriveType = [string]$v.DriveType; SizeRemaining = [long]$v.SizeRemaining }
  }
}

function git {
  $ErrorActionPreference = 'Continue'
  $a = @($args | ForEach-Object { [string]$_ })
  Add-Call ('git ' + ($a -join ' '))
  & $global:realGit @a 2>&1 | ForEach-Object { "$_" }
}

try {
  $block = [scriptblock]::Create((Get-Content -LiteralPath $BlockFile -Raw))
  . $block
} catch {
  Write-Output ('THROWN=' + $_.Exception.Message)
} finally {
  # WriteAllLines, not Set-Content: a block that stops at its first check
  # makes no call at all, and an empty list must still leave an (empty) file.
  [System.IO.File]::WriteAllLines($CallsFile, [string[]]@($global:calls))
}
# The block must hand the clone its own key and ports and give the console
# its own values back. Values of the two keys are never printed, only whether
# they are set.
foreach ($name in @('PORT', 'HOST', 'FLASK_ENV')) {
  Write-Output ('ENV_AFTER ' + $name + '=' + [Environment]::GetEnvironmentVariable($name, 'Process'))
}
foreach ($name in @('SECRET_KEY', 'FUEL_API_TOKEN')) {
  if ([Environment]::GetEnvironmentVariable($name, 'Process')) { Write-Output ('ENV_AFTER ' + $name + '=set') } else { Write-Output ('ENV_AFTER ' + $name + '=unset') }
}
