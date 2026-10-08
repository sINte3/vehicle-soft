# Stand-in SRV-YOQSH for the DRONE-CARD-COVERAGE-001 owner block W1+S1
# (docs/DRONE_CARD_COVERAGE_001.md, section 14), driven by
# tests/test_dji_card_coverage_blocks.py. Replaced: hostname, services,
# Task Scheduler, the registry, CIM processes and the web. Real: git, python,
# SQLite, the pilot tool, check_migration_drift, check_db_lock,
# dji_area_recalc, dji_field_census and the collector process itself, which
# the test checkout replaces with tests/card_pilot_fake_collector.py.
param([string]$BlockFile, [string]$ScenarioFile, [string]$CallsFile)
$ErrorActionPreference = 'Continue'
$global:sc = Get-Content -LiteralPath $ScenarioFile -Raw | ConvertFrom-Json
$global:calls = New-Object System.Collections.ArrayList
$global:svc = @{}
foreach ($p in $global:sc.Services.PSObject.Properties) { $global:svc[$p.Name] = @{ Status = [string]$p.Value.Status; StartType = [string]$p.Value.StartType } }
function Add-Call([string]$t) { [void]$global:calls.Add($t) }
function hostname { return [string]$global:sc.Host }
function Start-Sleep { [CmdletBinding()] param([int]$Seconds) }
function New-SvcObject([string]$name) {
  $o = [pscustomobject]@{ Name = $name; Status = $global:svc[$name].Status; StartType = $global:svc[$name].StartType }
  $o | Add-Member -MemberType ScriptMethod -Name WaitForStatus -Value {
    param($want, $span)
    if ($global:svc[$this.Name].Status -ne [string]$want) { throw "Time out has expired ($($this.Name) is $($global:svc[$this.Name].Status))" }
  }
  return $o
}
function Get-Service {
  [CmdletBinding()] param([string[]]$Name)
  foreach ($n in $Name) {
    if ($global:svc.ContainsKey($n)) { New-SvcObject $n }
    elseif ($PSBoundParameters['ErrorAction'] -ne 'SilentlyContinue') { throw "Cannot find any service with service name '$n'." }
  }
}
function Stop-Service { [CmdletBinding()] param([string]$Name, [switch]$Force) Add-Call "Stop-Service $Name"; $global:svc[$Name].Status = 'Stopped' }
function Start-Service {
  [CmdletBinding()] param([string]$Name)
  Add-Call "Start-Service $Name"
  if (@($global:sc.StartFails) -contains $Name) { throw "Service '$Name' cannot be started." }
  $global:svc[$Name].Status = 'Running'
}
function Set-Service { [CmdletBinding()] param([string]$Name, [string]$StartupType) Add-Call "Set-Service $Name $StartupType" }
function New-Task($t) {
  [pscustomobject]@{
    TaskName = [string]$t.TaskName; TaskPath = $(if ($t.TaskPath) { [string]$t.TaskPath } else { '\' }); State = [string]$t.State
    Settings = [pscustomobject]@{ Enabled = ([string]$t.State -ne 'Disabled') }
    Actions = @([pscustomobject]@{ Execute = [string]$t.Execute; Arguments = [string]$t.Arguments; WorkingDirectory = [string]$t.WorkingDirectory })
  }
}
function Get-ScheduledTask {
  [CmdletBinding()] param([string]$TaskName, [string]$TaskPath)
  foreach ($t in @($global:sc.Tasks)) { if ((-not $TaskName) -or ($t.TaskName -eq $TaskName)) { New-Task $t } }
}
function Get-ScheduledTaskInfo {
  [CmdletBinding()] param([string]$TaskName, [string]$TaskPath)
  $t = @(@($global:sc.Tasks) | Where-Object { $_.TaskName -eq $TaskName })[0]
  [pscustomobject]@{ NextRunTime = $(if ($t.NextRunTime) { [datetime]::ParseExact([string]$t.NextRunTime, 'yyyy-MM-dd HH:mm:ss', [System.Globalization.CultureInfo]::InvariantCulture) } else { $null }); LastRunTime = $null; LastTaskResult = 0 }
}
foreach ($verb in @('Disable-ScheduledTask', 'Enable-ScheduledTask', 'Start-ScheduledTask', 'Stop-ScheduledTask', 'Set-ItemProperty', 'New-ItemProperty', 'Remove-ItemProperty')) {
  Set-Item -Path ("function:global:" + $verb) -Value ([scriptblock]::Create("Add-Call '$verb'; throw '$verb must not be called by W1'"))
}
function Get-CimInstance {
  [CmdletBinding()] param([string]$ClassName)
  foreach ($p in @($global:sc.Processes)) { if ($p) { [pscustomobject]@{ ProcessId = [int]$p.ProcessId; Name = [string]$p.Name; CommandLine = [string]$p.CommandLine } } }
}
function Get-ItemProperty {
  [CmdletBinding()] param([string]$LiteralPath)
  $k = $null
  if ($global:sc.Registry) { $k = $global:sc.Registry.PSObject.Properties[$LiteralPath] }
  if ($null -eq $k) { if ($PSBoundParameters['ErrorAction'] -eq 'SilentlyContinue') { return $null }; throw "Cannot find path '$LiteralPath' because it does not exist." }
  $o = New-Object PSObject
  foreach ($v in $k.Value.PSObject.Properties) { $o | Add-Member -NotePropertyName $v.Name -NotePropertyValue $v.Value }
  return $o
}
function Invoke-WebRequest {
  [CmdletBinding()] param([string]$Uri, [string]$Method = 'Get', $Body, [string]$ContentType, [switch]$UseBasicParsing, [int]$TimeoutSec, [int]$MaximumRedirection)
  Add-Call ("WEB " + $Method + " " + $Uri)
  if ($Uri -like '*/drones/api/land_geometry_manifest') {
    $b = [string]$Body | ConvertFrom-Json
    if (($b.token -ne [string]$global:sc.Token) -or (@($b.content_md5).Count -ne 0)) { throw 'The remote server returned an error: (401) Unauthorized.' }
    return [pscustomobject]@{ StatusCode = 200; Content = '{"asked":0,"considered":0,"known":[],"known_count":0}' }
  }
  $a = $null
  if ($global:sc.Web) { $a = $global:sc.Web.PSObject.Properties[$Uri] }
  if ($null -eq $a) { throw 'Unable to connect to the remote server' }
  [pscustomobject]@{ StatusCode = [int]$a.Value.Status; Content = [string]$a.Value.Body }
}
# The console environment as the owner's PowerShell has it; the block must
# leave it as it was (the pilot settings go to the collector process only).
$consoleBefore = @{}
foreach ($e in @(Get-ChildItem Env:)) { $consoleBefore[$e.Name] = [string]$e.Value }
$text = Get-Content -LiteralPath $BlockFile -Raw
try {
  . ([scriptblock]::Create($text))
} catch {
  Write-Output ("BLOCK THREW: " + $_.Exception.Message)
}
$consoleAfter = @{}
foreach ($e in @(Get-ChildItem Env:)) { $consoleAfter[$e.Name] = [string]$e.Value }
foreach ($n in @(@($consoleBefore.Keys) + @($consoleAfter.Keys) | Sort-Object -Unique)) {
  if ($consoleBefore[$n] -cne $consoleAfter[$n]) { Add-Call ("CONSOLE_ENV_CHANGED " + $n) }
}
[System.IO.File]::WriteAllLines($CallsFile, [string[]]@($global:calls))
$final = @{}
foreach ($k in $global:svc.Keys) { $final[$k] = $global:svc[$k] }
[System.IO.File]::WriteAllText($CallsFile + '.services.json', ($final | ConvertTo-Json -Depth 4))
