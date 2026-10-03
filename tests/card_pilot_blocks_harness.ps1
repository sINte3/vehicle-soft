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
  if ($global:sc.WriteOnStart -and ($Name -eq 'TransportReportStaging')) {
    Start-Process -FilePath ([string]$global:sc.Python) -ArgumentList @([string]$global:sc.WriterScript, [string]$global:sc.WriteOnStart) -Wait
  }
}
function Restart-Service { [CmdletBinding()] param([string]$Name) Add-Call "Restart-Service $Name"; $global:svc[$Name].Status = 'Running' }
function Get-ScheduledTask {
  foreach ($t in @($global:sc.Tasks)) {
    $path = if ($t.TaskPath) { [string]$t.TaskPath } else { '\' }
    [pscustomobject]@{ TaskName = $t.TaskName; TaskPath = $path; State = $t.State; Actions = @([pscustomobject]@{ Execute = $t.Execute; Arguments = $t.Arguments; WorkingDirectory = [string]$t.WorkingDirectory }) }
  }
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
