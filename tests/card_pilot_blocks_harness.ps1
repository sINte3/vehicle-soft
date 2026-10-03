# Stand-in server for the DRONE-CARD-COVERAGE-001 owner blocks B1 and R
# (docs/DRONE_CARD_COVERAGE_001.md, section 14), driven by
# tests/test_dji_card_coverage_blocks.py. Replaced: hostname, services,
# scheduled tasks, the registry, disks and the web. Real: git, python, the
# backup / lock / drift tools, the pilot tool and SQLite.
param([string]$BlockFile, [string]$ScenarioFile, [string]$CallsFile)
$ErrorActionPreference = 'Continue'
$global:sc = Get-Content -LiteralPath $ScenarioFile -Raw | ConvertFrom-Json
$global:calls = New-Object System.Collections.ArrayList
$global:svc = @{}
foreach ($p in $global:sc.Services.PSObject.Properties) { $global:svc[$p.Name] = @{ Status = [string]$p.Value.Status; StartType = [string]$p.Value.StartType } }
$global:reg = @{}
if ($global:sc.Registry) {
  foreach ($k in $global:sc.Registry.PSObject.Properties) {
    $h = [ordered]@{}
    foreach ($v in $k.Value.PSObject.Properties) { $h[$v.Name] = $v.Value }
    $global:reg[$k.Name] = $h
  }
}
function Add-Call([string]$t) { [void]$global:calls.Add($t) }
function hostname { return [string]$global:sc.Host }
function Start-Sleep { [CmdletBinding()] param([int]$Seconds) }
function New-SvcObject([string]$name) {
  $o = [pscustomobject]@{ Name = $name; Status = $global:svc[$name].Status; StartType = $global:svc[$name].StartType }
  $o | Add-Member -MemberType ScriptMethod -Name WaitForStatus -Value {
    param($want, $span)
    if ($global:svc[$this.Name].Status -ne [string]$want) { throw "Time out has expired and the operation has not been completed ($($this.Name) is $($global:svc[$this.Name].Status))" }
  }
  return $o
}
function Get-Service {
  [CmdletBinding()] param([string[]]$Name)
  $names = if ($Name) { $Name } else { @($global:svc.Keys | Sort-Object) }
  foreach ($n in $names) {
    if ($global:svc.ContainsKey($n)) { New-SvcObject $n }
    elseif ($ErrorActionPreference -ne 'SilentlyContinue' -and $PSBoundParameters['ErrorAction'] -ne 'SilentlyContinue') { throw "Cannot find any service with service name '$n'." }
  }
}
function Set-Service { [CmdletBinding()] param([string]$Name, [string]$StartupType) Add-Call "Set-Service $Name $StartupType"; $global:svc[$Name].StartType = $StartupType }
function Stop-Service {
  [CmdletBinding()] param([string]$Name, [switch]$Force)
  Add-Call "Stop-Service $Name"
  if (@($global:sc.StopFails) -notcontains $Name) { $global:svc[$Name].Status = 'Stopped' }
  if ($global:sc.WriteDb -and ($Name -eq 'TransportReportStaging') -and (-not $global:wrote)) {
    $global:wrote = $true
    Start-Process -FilePath ([string]$global:sc.Python) -ArgumentList @([string]$global:sc.WriterScript, [string]$global:sc.WriteDb) -Wait
  }
  if ($global:sc.HoldDb -and ($Name -eq 'TransportReportStaging')) {
    $flag = $global:sc.HoldDb + '.held'
    $global:holder = Start-Process -FilePath ([string]$global:sc.Python) -ArgumentList @([string]$global:sc.HolderScript, [string]$global:sc.HoldDb, $flag) -PassThru
    for ($i = 0; ($i -lt 100) -and (-not (Microsoft.PowerShell.Management\Test-Path -LiteralPath $flag)); $i++) { [System.Threading.Thread]::Sleep(100) }
  }
}
function Start-Service {
  [CmdletBinding()] param([string]$Name)
  Add-Call "Start-Service $Name"
  if ($global:svc[$Name].StartType -eq 'Disabled') { throw "Service '$Name' cannot be started because it is disabled." }
  $global:svc[$Name].Status = 'Running'
  if ($global:sc.EnableTaskOnSiteStart -and ($Name -eq 'TransportReportStaging')) {
    foreach ($t in $global:tasks) { if ($t.TaskName -eq [string]$global:sc.EnableTaskOnSiteStart) { $t.Enabled = $true; $t.State = 'Ready' } }
  }
  if ($global:sc.WriteOnStart -and ($Name -eq 'TransportReportStaging')) {
    Start-Process -FilePath ([string]$global:sc.Python) -ArgumentList @([string]$global:sc.WriterScript, [string]$global:sc.WriteOnStart) -Wait
  }
}
function Restart-Service { [CmdletBinding()] param([string]$Name) Add-Call "Restart-Service $Name"; $global:svc[$Name].Status = 'Running' }
$global:tasks = @()
foreach ($t in @($global:sc.Tasks)) {
  $global:tasks += [pscustomobject]@{ TaskName = [string]$t.TaskName; TaskPath = $(if ($t.TaskPath) { [string]$t.TaskPath } else { '\' }); State = [string]$t.State
    Enabled = $(if ($null -eq $t.Enabled) { [string]$t.State -ne 'Disabled' } else { [bool]$t.Enabled }); Execute = $t.Execute; Arguments = $t.Arguments
    WorkingDirectory = [string]$t.WorkingDirectory; Triggers = @($t.Triggers); Xml = [string]$t.Xml; Reads = 0; RunningFromRead = [int]$t.RunningFromRead }
}
function New-Cim([string]$class, $props) {
  $list = @()
  foreach ($p in $props.PSObject.Properties) {
    $v = $p.Value
    if ($v -is [System.Management.Automation.PSCustomObject]) { $v = New-Cim 'MSFT_TaskRepetitionPattern' $v }
    $list += [pscustomobject]@{ Name = $p.Name; Value = $v }
  }
  [pscustomobject]@{ CimClass = [pscustomobject]@{ CimClassName = $class }; CimInstanceProperties = $list }
}
function Get-ScheduledTask {
  [CmdletBinding()] param([string]$TaskName)
  foreach ($t in $global:tasks) {
    if ($TaskName -and ($t.TaskName -ne $TaskName)) { continue }
    if ($TaskName) { $t.Reads++; if (($t.RunningFromRead -gt 0) -and ($t.Reads -ge $t.RunningFromRead)) { $t.State = 'Running' } }
    $trs = @($t.Triggers | Where-Object { $_ } | ForEach-Object { New-Cim ([string]$_.Class) $_.Props })
    [pscustomobject]@{ TaskName = $t.TaskName; TaskPath = $t.TaskPath; State = $t.State
      Settings = [pscustomobject]@{ Enabled = $t.Enabled }
      Actions = @([pscustomobject]@{ Execute = $t.Execute; Arguments = $t.Arguments; WorkingDirectory = $t.WorkingDirectory })
      Triggers = $trs }
  }
}
function Find-Task([string]$TaskName, [string]$TaskPath) { @($global:tasks | Where-Object { ($_.TaskName -eq $TaskName) -and ($_.TaskPath -eq $TaskPath) }) }
function Disable-ScheduledTask {
  [CmdletBinding()] param([string]$TaskName, [string]$TaskPath)
  Add-Call ("Disable-ScheduledTask " + $TaskPath + $TaskName)
  $hit = Find-Task $TaskName $TaskPath
  if ($hit.Count -ne 1) { throw "The system cannot find the file specified: $TaskPath$TaskName" }
  $hit[0].Enabled = $false
  if ($hit[0].State -ne 'Running') { $hit[0].State = 'Disabled' }
  $hit[0]
}
function Enable-ScheduledTask {
  [CmdletBinding()] param([string]$TaskName, [string]$TaskPath)
  Add-Call ("Enable-ScheduledTask " + $TaskPath + $TaskName)
  $hit = Find-Task $TaskName $TaskPath
  if ($hit.Count -ne 1) { throw "The system cannot find the file specified: $TaskPath$TaskName" }
  $hit[0].Enabled = $true
  if ($hit[0].State -eq 'Disabled') { $hit[0].State = 'Ready' }
  $hit[0]
}
function Start-ScheduledTask { [CmdletBinding()] param([string]$TaskName, [string]$TaskPath) Add-Call ("Start-ScheduledTask " + $TaskPath + $TaskName) }
function Stop-ScheduledTask { [CmdletBinding()] param([string]$TaskName, [string]$TaskPath) Add-Call ("Stop-ScheduledTask " + $TaskPath + $TaskName) }
function Export-ScheduledTask {
  [CmdletBinding()] param([string]$TaskName, [string]$TaskPath)
  $hit = Find-Task $TaskName $TaskPath
  $hit[0].Xml + '<Enabled>' + ([string]$hit[0].Enabled).ToLower() + '</Enabled>'
}
function Get-CimInstance {
  [CmdletBinding()] param([string]$ClassName)
  foreach ($c in @($global:sc.Processes)) { if ($c) { [pscustomobject]@{ ProcessId = 4242; ParentProcessId = 1; Name = 'python.exe'; CommandLine = [string]$c } } }
}
function Get-ItemProperty {
  [CmdletBinding()] param([string]$LiteralPath)
  if ($global:reg.Contains($LiteralPath)) { return [pscustomobject]$global:reg[$LiteralPath] }
  if ($PSBoundParameters['ErrorAction'] -ne 'SilentlyContinue') { throw "Cannot find path '$LiteralPath' because it does not exist." }
}
function Set-ItemProperty {
  [CmdletBinding()] param([string]$LiteralPath, [string]$Name, $Value, [string]$Type)
  Add-Call "Set-ItemProperty $LiteralPath $Name $Type"
  if (-not $global:reg.Contains($LiteralPath)) { $global:reg[$LiteralPath] = [ordered]@{} }
  $global:reg[$LiteralPath][$Name] = $Value
}
function Get-PSDrive { [CmdletBinding()] param([string]$Name) [pscustomobject]@{ Name = $Name; Free = [long]50GB } }
function Invoke-WebRequest {
  [CmdletBinding()] param([string]$Uri, [switch]$UseBasicParsing, [int]$TimeoutSec)
  $path = $Uri -replace '^https?://[^/]+', ''
  Add-Call "GET $path"
  $a = $global:sc.Web.$path
  if ($null -eq $a) { throw "no answer for $path" }
  if ([int]$a.Status -ne 200) { throw "The remote server returned an error: ($($a.Status))." }
  return [pscustomobject]@{ StatusCode = [int]$a.Status; Content = [string]$a.Body }
}
$text = Get-Content -LiteralPath $BlockFile -Raw
try {
  . ([scriptblock]::Create($text))
} catch {
  Write-Output ("BLOCK THREW: " + $_.Exception.Message)
}
if ($global:holder) { try { Stop-Process -Id $global:holder.Id -Force } catch { } }
[System.IO.File]::WriteAllLines($CallsFile, [string[]]@($global:calls))
$final = @{}
foreach ($k in $global:svc.Keys) { $final[$k] = $global:svc[$k] }
[System.IO.File]::WriteAllText($CallsFile + '.services.json', ($final | ConvertTo-Json -Depth 4))
[System.IO.File]::WriteAllText($CallsFile + '.registry.json', ($global:reg | ConvertTo-Json -Depth 5))
$taskState = @($global:tasks | ForEach-Object { [ordered]@{ TaskName = $_.TaskName; TaskPath = $_.TaskPath; State = $_.State; Enabled = $_.Enabled } })
[System.IO.File]::WriteAllText($CallsFile + '.tasks.json', (ConvertTo-Json -InputObject $taskState -Depth 3))
