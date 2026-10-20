# Stand-in workstation for the DRONE-CARD-COVERAGE-001 owner block W0
# (docs/DRONE_CARD_COVERAGE_001.md, section 14), driven by
# tests/test_dji_card_coverage_blocks.py. Replaced: hostname, the drive list,
# Task Scheduler, CIM (OS, processes, services), the registry and the web.
# Real: the file system of the scenario, git, the console environment and the
# background job W0 starts for SRV-YOQSH (which finds no such host here).
param([string]$BlockFile, [string]$ScenarioFile, [string]$CallsFile)
$ErrorActionPreference = 'Continue'
$global:sc = Get-Content -LiteralPath $ScenarioFile -Raw | ConvertFrom-Json
$global:web = New-Object System.Collections.ArrayList
function hostname { return [string]$global:sc.Host }
function Get-PSDrive {
  [CmdletBinding()] param([string]$PSProvider, [string]$Name)
  foreach ($d in @($global:sc.Drives)) {
    $free = if ($null -ne $d.Free) { [double]$d.Free } else { $null }
    [pscustomobject]@{ Name = [string]$d.Name; Root = [string]$d.Root; Free = $free; Used = [double]$d.Used; DisplayRoot = [string]$d.DisplayRoot }
  }
}
function New-CimLike([string]$cls, $props) {
  $list = @()
  if ($props) { foreach ($p in $props.PSObject.Properties) { $list += [pscustomobject]@{ Name = $p.Name; Value = $p.Value } } }
  [pscustomobject]@{ CimClass = [pscustomobject]@{ CimClassName = $cls }; CimInstanceProperties = $list }
}
function Get-ScheduledTask {
  [CmdletBinding()] param([string]$TaskName, [string]$TaskPath)
  if ($global:sc.TasksFail) { throw [string]$global:sc.TasksFail }
  foreach ($t in @($global:sc.Tasks)) {
    [pscustomobject]@{
      TaskName = [string]$t.TaskName; TaskPath = [string]$t.TaskPath; State = [string]$t.State
      Settings = [pscustomobject]@{ Enabled = [bool]$t.Enabled }
      Principal = [pscustomobject]@{ UserId = [string]$t.UserId; LogonType = 'Interactive'; RunLevel = 'Limited' }
      Actions = @(@($t.Actions) | ForEach-Object { [pscustomobject]@{ Execute = [string]$_.Execute; Arguments = [string]$_.Arguments; WorkingDirectory = [string]$_.WorkingDirectory } })
      Triggers = @(@($t.Triggers) | Where-Object { $_ } | ForEach-Object { New-CimLike ([string]$_.Class) $_.Props })
    }
  }
}
function Get-ScheduledTaskInfo {
  [CmdletBinding()] param([string]$TaskName, [string]$TaskPath)
  $t = @(@($global:sc.Tasks) | Where-Object { $_.TaskName -eq $TaskName })[0]
  [pscustomobject]@{ LastRunTime = [string]$t.LastRunTime; LastTaskResult = [string]$t.LastTaskResult; NextRunTime = [string]$t.NextRunTime }
}
function Get-CimInstance {
  [CmdletBinding()] param([string]$ClassName)
  switch ($ClassName) {
    'Win32_OperatingSystem' { [pscustomobject]@{ Caption = 'Microsoft Windows 10 Pro'; Version = '10.0.19045'; BuildNumber = '19045'; OSArchitecture = '64-bit' } }
    'Win32_Process' { foreach ($p in @($global:sc.Processes)) { if ($p) { [pscustomobject]@{ ProcessId = [int]$p.ProcessId; Name = [string]$p.Name; CommandLine = [string]$p.CommandLine; ExecutablePath = [string]$p.ExecutablePath; CreationDate = [string]$p.CreationDate } } } }
    'Win32_Service' { foreach ($s in @($global:sc.Services)) { if ($s) { [pscustomobject]@{ Name = [string]$s.Name; State = [string]$s.State; StartMode = [string]$s.StartMode; StartName = [string]$s.StartName; PathName = [string]$s.PathName } } } }
    default { throw "no stand-in for $ClassName" }
  }
}
function Get-ItemProperty {
  [CmdletBinding()] param([string]$LiteralPath)
  if ($LiteralPath -like 'HK*') {
    $k = $null
    if ($global:sc.Registry) { $k = $global:sc.Registry.PSObject.Properties[$LiteralPath] }
    if ($null -eq $k) { throw "Cannot find path '$LiteralPath' because it does not exist." }
    $o = New-Object PSObject
    foreach ($v in $k.Value.PSObject.Properties) { $o | Add-Member -NotePropertyName $v.Name -NotePropertyValue $v.Value }
    return $o
  }
  Microsoft.PowerShell.Management\Get-ItemProperty -LiteralPath $LiteralPath
}
function Invoke-WebRequest {
  [CmdletBinding()] param([string]$Uri, [string]$Method, [switch]$UseBasicParsing, [int]$TimeoutSec, [int]$MaximumRedirection)
  [void]$global:web.Add(([string]$Method) + ' ' + $Uri)
  $a = $null
  if ($global:sc.Web) { $a = $global:sc.Web.PSObject.Properties[$Uri] }
  if ($null -eq $a) { throw 'Unable to connect to the remote server' }
  [pscustomobject]@{ StatusCode = [int]$a.Value.Status; Content = [string]$a.Value.Body }
}
$text = Get-Content -LiteralPath $BlockFile -Raw
try {
  . ([scriptblock]::Create($text))
} catch {
  Write-Output ("BLOCK THREW: " + $_.Exception.Message)
}
[System.IO.File]::WriteAllLines($CallsFile, [string[]]@($global:web))
