# tests/fin_recon_runbook_harness.ps1 -- runs ONE block of
# docs/FIN_RECON_STORAGE_001_RUNBOOK.md with stand-ins for what only the
# Windows server has: the host name, the services, the TCP listeners, the
# firewall, the clone process, icacls, sc.exe, the disks and the web site.
#
# Called by tests/test_fin_recon_runbook.py, which first points every
# constant of the block (folders, python, repository, commit, hashes, period)
# at temporary ones. git and python stay REAL: the block clones a synthetic
# repository, runs the real migration, import, verification, backup and
# restore test on a synthetic package, and reads synthetic production and
# staging checkouts. git calls are recorded on their way through, so the test
# can prove which subcommands touched which folder.
#
# The stand-ins keep the "server" (live processes, listeners, firewall rules)
# in a small text file, so blocks run one after another in separate processes
# see each other's effects, as the owner's console would. Functions win over
# cmdlets and programs of the same name inside this script and the block it
# runs, so nothing here can reach a real service, port or firewall.
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

function Read-ServerState {
  $server = @{ Processes = @(); Listeners = @(); Rules = @() }
  $path = [string]$global:scenario.StateFile
  if (Test-Path -LiteralPath $path) {
    foreach ($line in [System.IO.File]::ReadAllLines($path)) {
      $f = $line.Split(' ')
      if ($f[0] -eq 'process') {
        $server.Processes += , @{ Id = [int]$f[1]; Ticks = [long]$f[2] }
      } elseif ($f[0] -eq 'listen') {
        $server.Listeners += , @{ Port = [int]$f[1]; Owner = [int]$f[2] }
      } elseif ($f[0] -eq 'rule') {
        $server.Rules += $line.Substring(5)
      }
    }
  }
  return $server
}

function Write-ServerState($server) {
  $out = New-Object System.Collections.ArrayList
  foreach ($p in $server.Processes) { [void]$out.Add('process ' + $p.Id + ' ' + $p.Ticks) }
  foreach ($l in $server.Listeners) { [void]$out.Add('listen ' + $l.Port + ' ' + $l.Owner) }
  foreach ($r in $server.Rules) { [void]$out.Add('rule ' + $r) }
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
    if ($l.Port -eq $LocalPort) { [pscustomobject]@{ LocalPort = $l.Port; OwningProcess = $l.Owner } }
  }
}

function Get-NetFirewallRule {
  [CmdletBinding()]
  param([string]$DisplayName, [string]$Direction, [string]$Enabled)
  foreach ($r in (Read-ServerState).Rules) {
    if ((-not $DisplayName) -or ($r -eq $DisplayName)) { [pscustomobject]@{ DisplayName = $r } }
  }
}

function New-NetFirewallRule {
  [CmdletBinding()]
  param([string]$DisplayName, [string]$Direction, [string]$Protocol, [int]$LocalPort, [string]$RemoteAddress, [string]$Action)
  Add-Call ('New-NetFirewallRule ' + $DisplayName + ' ' + $Direction + ' ' + $Protocol + ' ' + $LocalPort + ' ' + $RemoteAddress + ' ' + $Action)
  $server = Read-ServerState
  $server.Rules += $DisplayName
  Write-ServerState $server
  return [pscustomobject]@{ DisplayName = $DisplayName }
}

function Remove-NetFirewallRule {
  [CmdletBinding()]
  param([string]$DisplayName)
  Add-Call ('Remove-NetFirewallRule ' + $DisplayName)
  $server = Read-ServerState
  $server.Rules = @($server.Rules | Where-Object { $_ -ne $DisplayName })
  Write-ServerState $server
}

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
  if (-not $exited) {
    $server = Read-ServerState
    $server.Processes += , @{ Id = $newId; Ticks = $ticks }
    if (-not $global:scenario.StartNoListen) {
      $server.Listeners += , @{ Port = [int][Environment]::GetEnvironmentVariable('PORT', 'Process'); Owner = $newId }
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
  Add-Call ('icacls ' + (@($args | ForEach-Object { [string]$_ }) -join ' '))
  $global:LASTEXITCODE = [int]$global:scenario.IcaclsCode
  'processed file: ' + [string]$args[0]
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
