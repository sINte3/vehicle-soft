# tests/onec_probe_harness.ps1 -- runs tools/onec_probe.ps1 (Invoke-Main)
# against a stand-in 1C infobase: plain PowerShell objects with the member
# names of the 1C COM objects (Metadata, Reports, Count(), Get(i), ...).
# Nothing here can reach 1C: the connector is a script block.
#
# Called by tests/test_onec_probe.py, one scenario per process; the exit code
# of the process is the probe's.
#
# ASCII only (Windows PowerShell 5.1 and PowerShell 7 alike): the Cyrillic
# fixtures are \u escapes.
param(
  [Parameter(Mandatory = $true)][string]$Probe,
  [Parameter(Mandatory = $true)][string]$Scenario,
  [Parameter(Mandatory = $true)][string]$Folder
)
$ErrorActionPreference = 'Stop'

$C = @{}
$C['R1'] = [regex]::Unescape('\u0420\u0435\u0435\u0441\u0442\u0440\u041f\u041a17')
$C['R1S'] = [regex]::Unescape('\u0420\u0435\u0435\u0441\u0442\u0440 \u041f\u041a-17 (\u0436\u0430\u0434\u0432\u0430\u043b 7)')
$C['R2'] = [regex]::Unescape('\u0412\u044b\u0432\u043e\u0437\u041f\u043e\u0414\u0430\u0442\u0435\u0411\u0440\u0443\u0442\u0442\u043e')
$C['R2S'] = [regex]::Unescape('\u0412\u044b\u0432\u043e\u0437 \u043f\u043e \u0434\u0430\u0442\u0435 \u0432\u0437\u0432\u0435\u0448\u0438\u0432\u0430\u043d\u0438\u044f \u0431\u0440\u0443\u0442\u0442\u043e')
$C['R3'] = [regex]::Unescape('\u041e\u0441\u0442\u0430\u0442\u043a\u0438')
$C['R3S'] = [regex]::Unescape('\u041e\u0441\u0442\u0430\u0442\u043a\u0438 \u043d\u0430 \u0441\u043a\u043b\u0430\u0434\u0430\u0445')
$C['R4'] = [regex]::Unescape('\u0416\u0443\u0440\u043d\u0430\u043b\u0412\u0437\u0432\u0435\u0448\u0438\u0432\u0430\u043d\u0438\u0439')
$C['R4S'] = [regex]::Unescape('\u0416\u0443\u0440\u043d\u0430\u043b \u0432\u0437\u0432\u0435\u0448\u0438\u0432\u0430\u043d\u0438\u0439')
$C['TPL'] = [regex]::Unescape('\u041e\u0441\u043d\u043e\u0432\u043d\u0430\u044f\u0421\u0445\u0435\u043c\u0430\u041a\u043e\u043c\u043f\u043e\u043d\u043e\u0432\u043a\u0438\u0414\u0430\u043d\u043d\u044b\u0445')
$C['P1'] = [regex]::Unescape('\u041d\u0430\u0447\u0430\u043b\u043e\u041f\u0435\u0440\u0438\u043e\u0434\u0430')
$C['P1T'] = [regex]::Unescape('\u041d\u0430\u0447\u0430\u043b\u043e \u043f\u0435\u0440\u0438\u043e\u0434\u0430')
$C['DS1'] = [regex]::Unescape('\u041d\u0430\u0431\u043e\u0440\u0414\u0430\u043d\u043d\u044b\u04451')
$C['Q1'] = [regex]::Unescape('\u0412\u042b\u0411\u0420\u0410\u0422\u042c\n    \u0412\u0437\u0432\u0435\u0448\u0438\u0432\u0430\u043d\u0438\u0435.\u041c\u0430\u0441\u0441\u0430\u0424\u0438\u0437\u0438\u0447\u0435\u0441\u043a\u0430\u044f \u041a\u0410\u041a \u041c\u0430\u0441\u0441\u0430\n\u0418\u0417\n    \u0414\u043e\u043a\u0443\u043c\u0435\u043d\u0442.\u0412\u0437\u0432\u0435\u0448\u0438\u0432\u0430\u043d\u0438\u0435 \u041a\u0410\u041a \u0412\u0437\u0432\u0435\u0448\u0438\u0432\u0430\u043d\u0438\u0435')
$C['F1'] = [regex]::Unescape('\u041c\u0430\u0441\u0441\u0430')
$C['F1T'] = [regex]::Unescape('\u041c\u0430\u0441\u0441\u0430 \u0444\u0438\u0437\u0438\u0447\u0435\u0441\u043a\u0430\u044f,\u043a\u0433')
$C['UN'] = [regex]::Unescape('\u041e\u0431\u044a\u0435\u0434\u0438\u043d\u0435\u043d\u0438\u0435')
$C['IN'] = [regex]::Unescape('\u0412\u043b\u043e\u0436\u0435\u043d\u043d\u044b\u0439')
$C['Q2'] = [regex]::Unescape('\u0412\u042b\u0411\u0420\u0410\u0422\u042c 1')
$C['CF'] = [regex]::Unescape('\u0422\u043e\u043d\u043d\u044b')
$C['CFE'] = [regex]::Unescape('\u041c\u0430\u0441\u0441\u0430 / 1000')
$C['TFE'] = [regex]::Unescape('\u0421\u0443\u043c\u043c\u0430(\u041c\u0430\u0441\u0441\u0430)')
$C['VAR'] = [regex]::Unescape('\u041e\u0441\u043d\u043e\u0432\u043d\u043e\u0439')
$C['AT'] = [regex]::Unescape('\u041f\u0435\u0440\u0438\u043e\u0434')
$C['TM'] = [regex]::Unescape('\u041c\u0430\u043a\u0435\u0442')
$C['SS1'] = [regex]::Unescape('\u0417\u0430\u0433\u043e\u0442\u043e\u0432\u043a\u0430\u0425\u043b\u043e\u043f\u043a\u0430\u0421\u044b\u0440\u0446\u0430')
$C['SS1S'] = [regex]::Unescape('\u0417\u0430\u0433\u043e\u0442\u043e\u0432\u043a\u0430 \u0445\u043b\u043e\u043f\u043a\u0430 \u0441\u044b\u0440\u0446\u0430')
$C['C1'] = [regex]::Unescape('\u041e\u0442\u0447\u0435\u0442.\u0420\u0435\u0435\u0441\u0442\u0440\u041f\u041a17')
$C['C2'] = [regex]::Unescape('\u0414\u043e\u043a\u0443\u043c\u0435\u043d\u0442.\u0412\u0437\u0432\u0435\u0448\u0438\u0432\u0430\u043d\u0438\u0435')
$C['SS2'] = [regex]::Unescape('\u041d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438')
$C['C3'] = [regex]::Unescape('\u0421\u043f\u0440\u0430\u0432\u043e\u0447\u043d\u0438\u043a.\u041f\u0443\u043d\u043a\u0442\u044b')
$C['SS3'] = [regex]::Unescape('\u0411\u0443\u0445\u0433\u0430\u043b\u0442\u0435\u0440\u0438\u044f')
$C['C4'] = [regex]::Unescape('\u0414\u043e\u043a\u0443\u043c\u0435\u043d\u0442.\u041f\u043b\u0430\u0442\u0435\u0436')
$C['DOC'] = [regex]::Unescape('\u0412\u0437\u0432\u0435\u0448\u0438\u0432\u0430\u043d\u0438\u0435')
$C['DOCS'] = [regex]::Unescape('\u0412\u0437\u0432\u0435\u0448\u0438\u0432\u0430\u043d\u0438\u0435 \u0445\u043b\u043e\u043f\u043a\u0430')
$C['AR'] = [regex]::Unescape('\u041e\u0441\u0442\u0430\u0442\u043a\u0438\u0425\u043b\u043e\u043f\u043a\u0430')
$C['ARS'] = [regex]::Unescape('\u041e\u0441\u0442\u0430\u0442\u043a\u0438 \u0445\u043b\u043e\u043f\u043a\u0430')
$C['CONF'] = [regex]::Unescape('\u0423\u0437\u043f\u0430\u0445\u0442\u0430')
$C['REFUSED'] = [regex]::Unescape('\u0418\u0434\u0435\u043d\u0442\u0438\u0444\u0438\u043a\u0430\u0446\u0438\u044f \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044f \u043d\u0435 \u0432\u044b\u043f\u043e\u043b\u043d\u0435\u043d\u0430')

function New-Coll([object[]]$items) {
  $o = [pscustomobject]@{ Items = @($items) }
  $o | Add-Member -MemberType ScriptMethod -Name Count -Value { return @($this.Items).Count }
  $o | Add-Member -MemberType ScriptMethod -Name Get -Value { param($i) return @($this.Items)[$i] }
  return $o
}

function New-Named([string]$name, [string]$synonym) {
  return [pscustomobject]@{ Name = $name; Synonym = $synonym }
}

function New-Full([string]$full) {
  $o = [pscustomobject]@{ Full = $full }
  $o | Add-Member -MemberType ScriptMethod -Name FullName -Value { return $this.Full }
  return $o
}

function New-Subsystem([string]$name, [string]$synonym, [object[]]$content, [object[]]$children) {
  return [pscustomobject]@{ Name = $name; Synonym = $synonym
    Content = (New-Coll $content); Subsystems = (New-Coll $children) }
}

# A value 1C turns into text with String(): the stand-in conn.String() gives
# 'S<kind>' so the test sees that the probe asked 1C, not .NET, for the text.
$typeDate = [pscustomobject]@{ Kind = 'date-type' }
$period = [pscustomobject]@{ Kind = 'standard-period' }

$querySet = [pscustomobject]@{ Name = $C['DS1']; Query = $C['Q1']
  Fields = (New-Coll @([pscustomobject]@{ DataPath = $C['F1']; Field = $C['F1']; Title = $C['F1T'] })) }
$innerSet = [pscustomobject]@{ Name = $C['IN']; Query = $C['Q2']; Fields = (New-Coll @()) }
$unionSet = [pscustomobject]@{ Name = $C['UN']; Items = (New-Coll @($innerSet)); Fields = (New-Coll @()) }
$schema = [pscustomobject]@{
  Parameters = (New-Coll @([pscustomobject]@{ Name = $C['P1']; Title = $C['P1T']; ValueType = $typeDate; Value = $period }))
  DataSets = (New-Coll @($querySet, $unionSet))
  CalculatedFields = (New-Coll @([pscustomobject]@{ DataPath = $C['CF']; Title = $C['CF']; Expression = $C['CFE'] }))
  TotalFields = (New-Coll @([pscustomobject]@{ DataPath = $C['F1']; Expression = $C['TFE'] }))
  SettingsVariants = (New-Coll @([pscustomobject]@{ Name = $C['VAR']; Presentation = $C['VAR'] }))
}

function New-Manager($schemaValue, [string]$template, [bool]$broken) {
  $report = [pscustomobject]@{ Schema = $schemaValue; Template = $template; Broken = $broken }
  $report | Add-Member -MemberType ScriptMethod -Name GetTemplate -Value {
    param($name)
    if ($this.Broken) { throw 'template is not available in an external connection' }
    if ($name -ne $this.Template) { throw ('asked for a wrong template: ' + $name) }
    return $this.Schema
  }
  $manager = [pscustomobject]@{ Object = $report }
  $manager | Add-Member -MemberType ScriptMethod -Name Create -Value { return $this.Object }
  return $manager
}

function New-ReportMeta([string]$name, [string]$synonym, $main, [object[]]$attributes, [object[]]$templates) {
  return [pscustomobject]@{ Name = $name; Synonym = $synonym; MainDataCompositionSchema = $main
    Attributes = (New-Coll $attributes); Templates = (New-Coll $templates) }
}

$main = [pscustomobject]@{ Name = $C['TPL'] }
$reports = @(
  (New-ReportMeta $C['R1'] $C['R1S'] $main @() @()),
  (New-ReportMeta $C['R2'] $C['R2S'] $null @([pscustomobject]@{ Name = $C['AT']; Synonym = $C['AT']; Type = $typeDate }) @((New-Named $C['TM'] ''))),
  (New-ReportMeta $C['R3'] $C['R3S'] $main @() @()),
  (New-ReportMeta $C['R4'] $C['R4S'] $main @() @())
)
$managers = @{}
$managers[$C['R1']] = New-Manager $schema $C['TPL'] $false
$managers[$C['R3']] = New-Manager $schema $C['TPL'] $false
$managers[$C['R4']] = New-Manager $schema $C['TPL'] $true

$metadata = [pscustomobject]@{
  Name = 'UzPaxta'; Synonym = $C['CONF']; Version = '1.0.5'
  Reports = (New-Coll $reports)
  Subsystems = (New-Coll @(
    (New-Subsystem $C['SS1'] $C['SS1S'] @((New-Full $C['C1']), (New-Full $C['C2'])) @(
      (New-Subsystem $C['SS2'] $C['SS2'] @((New-Full $C['C3'])) @()))),
    (New-Subsystem $C['SS3'] $C['SS3'] @((New-Full $C['C4'])) @())))
  Documents = (New-Coll @((New-Named $C['DOC'] $C['DOCS'])))
  AccumulationRegisters = (New-Coll @((New-Named $C['AR'] $C['ARS'])))
  InformationRegisters = (New-Coll @())
}

$conn = [pscustomobject]@{ Metadata = $metadata; Reports = [pscustomobject]$managers }
$conn | Add-Member -MemberType ScriptMethod -Name String -Value { param($value) return 'S<' + $value.Kind + '>' }

. $Probe -LoadOnly

$Server = 'srv-1c'
$Base = 'uzpaxta'
$CredentialsFile = Join-Path $Folder 'credentials.txt'
$Out = Join-Path $Folder 'probe.txt'
$Report = @()
$script:expected = Get-Content -LiteralPath (Join-Path $Folder 'expected.txt') -Encoding UTF8 -ErrorAction SilentlyContinue
$script:conn = $conn

$connect = {
  param($connectionString)
  if ($connectionString -ne $script:expected) { throw 'the probe built an unexpected connection string' }
  return $script:conn
}

switch ($Scenario) {
  'ok' { }
  'named' { $Report = @(($C['R3'] + ',' + $C['R2'])) }
  'refused' {
    $connect = { param($connectionString) throw ($C['REFUSED'] + ': ' + $connectionString) }
  }
  'broken' {
    # A method, not a property getter: PowerShell swallows an exception of
    # a script property getter, while a COM call (InvokeMember) raises it.
    $failing = [pscustomobject]@{}
    $failing | Add-Member -MemberType ScriptMethod -Name Count -Value { throw ('metadata failed for ' + $script:expected) }
    $script:conn = [pscustomobject]@{ Reports = $null
      Metadata = [pscustomobject]@{ Name = 'UzPaxta'; Synonym = ''; Version = '1'; Reports = $failing } }
  }
  'no-folder' { $Out = Join-Path (Join-Path $Folder 'missing') 'probe.txt' }
  default { throw ('unknown scenario ' + $Scenario) }
}

$code = Invoke-Main $connect
exit $code
