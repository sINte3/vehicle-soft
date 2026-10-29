# tests/onec_export_harness.ps1 -- runs tools/onec_export.ps1 (Invoke-Main)
# against a stand-in 1C: a connection whose NewObject('Query') returns a
# query object that "executes" by picking one of two row sets from its text
# and filtering them by the Since/Until parameters, as the WHERE clause of
# the real query does. Nothing here can reach 1C.
#
# Called by tests/test_onec_export.py, one scenario per process; the exit
# code of the process is the export's.
#
# ASCII only (Windows PowerShell 5.1 and PowerShell 7 alike): the Cyrillic
# fixtures are \u escapes.
param(
  [Parameter(Mandatory = $true)][string]$Export,
  [Parameter(Mandatory = $true)][string]$Scenario,
  [Parameter(Mandatory = $true)][string]$Folder,
  [string]$WeekArg = '',
  [string]$TodayArg = ''
)
$ErrorActionPreference = 'Stop'

$C = @{}
$C['VYVOZ_DOC'] = [regex]::Unescape('\u0412\u044b\u0432\u043e\u0437\u0425\u043b\u043e\u043f\u043a\u0430\u0421\u044b\u0440\u0446\u0430\u0418\u0437\u041f\u0443\u043d\u043a\u0442\u043e\u0432\u041f\u0440\u0438\u0435\u043c\u0430\u041d\u0430\u0417\u0430\u0432\u043e\u0434_\u0428\u043b\u044e\u0437')
$C['REESTR_DOC'] = [regex]::Unescape('\u041f\u043e\u0441\u0442\u0443\u043f\u043b\u0435\u043d\u0438\u0435\u0425\u043b\u043e\u043f\u043a\u0430\u0421\u044b\u0440\u0446\u0430')
$C['ZAPROS'] = [regex]::Unescape('\u0417\u0430\u043f\u0440\u043e\u0441')
$C['P_VOBKENT'] = [regex]::Unescape('\u0412\u043e\u0431\u043a\u0435\u043d\u0442 \u041f\u0422\u041c')
$C['P_HARGUSH'] = [regex]::Unescape('\u0425\u0430\u0440\u0433\u0443\u0448 \u041f\u0422\u041c')
$C['P_KUMUSH'] = [regex]::Unescape('\u041a\u0443\u043c\u0443\u0448\u043a\u0435\u043d\u0442 \u041f\u0422\u041c')
$C['P_NARZI'] = [regex]::Unescape('\u0425.\u041d\u0430\u0440\u0437\u0438 \u041f\u0422\u041c')
$C['T_MTZ'] = [regex]::Unescape('\u041c\u0422\u0417-80-1 80 283 HA')
$C['T_KALHIDA'] = [regex]::Unescape('\u041c\u0422\u0417-80-1(\u043a\u0430\u043b\u0445\u0438\u0434\u0430) 80 240 HA')
$C['T_KAMAZ'] = [regex]::Unescape('\u041a\u0430\u043c\u0430\u0437')
$C['ORG_MIRZA'] = [regex]::Unescape('\u041c\u0438\u0440\u0437\u0430\u0447\u0443\u043b \u0410\u0433\u0440\u043e\u043a\u043b\u0430\u0441\u0442\u0435\u0440 \u0433\u0440\u043e\u0443\u043f \u041c\u0427\u0416')

function T([string]$text) { return [datetime]::ParseExact($text, 'yyyy-MM-dd HH:mm:ss', [System.Globalization.CultureInfo]::InvariantCulture) }

$buxoro = 'OOO "BUXORO AGROKLASTER"'
# Rows of the removal document; SecondWeighing is also the period field.
$vyvozRows = @(
  [ordered]@{ Org = $buxoro; Point = $C['P_VOBKENT']; Transport = $C['T_MTZ']; FirstWeighing = (T '2026-10-19 07:28:06'); SecondWeighing = (T '2026-10-19 08:46:56'); Net = [decimal]6620 },
  [ordered]@{ Org = $buxoro; Point = $C['P_HARGUSH']; Transport = $C['T_KALHIDA']; FirstWeighing = (T '2026-10-25 22:10:00'); SecondWeighing = (T '2026-10-25 23:59:59'); Net = [decimal]5320.5 },
  [ordered]@{ Org = $buxoro; Point = $C['P_VOBKENT']; Transport = $C['T_KAMAZ']; FirstWeighing = $null; SecondWeighing = (T '2026-10-19 00:00:00'); Net = [decimal]1000 },
  [ordered]@{ Org = $buxoro; Point = $C['P_VOBKENT']; Transport = $C['T_MTZ']; FirstWeighing = (T '2026-10-18 22:00:00'); SecondWeighing = (T '2026-10-18 23:59:59'); Net = [decimal]7000 },
  [ordered]@{ Org = $buxoro; Point = $C['P_VOBKENT']; Transport = $C['T_MTZ']; FirstWeighing = (T '2026-10-25 23:30:00'); SecondWeighing = (T '2026-10-26 00:00:00'); Net = [decimal]7100 }
)
# Rows of the receiving document; PostedAt is the period field and is not
# selected (the real query filters by the posting date, selects the gross
# weighing time).
$reestrRows = @(
  [ordered]@{ Org = $buxoro; Point = $C['P_KUMUSH']; Transport = '80 KA 368'; GrossWeighing = (T '2026-10-20 19:16:47'); Mass = [decimal]4840; PostedAt = (T '2026-10-20 19:16:47') },
  [ordered]@{ Org = $C['ORG_MIRZA']; Point = $C['P_NARZI']; Transport = '80 HA 254'; GrossWeighing = (T '2026-10-25 20:26:10'); Mass = [decimal]2400; PostedAt = (T '2026-10-25 20:26:10') },
  [ordered]@{ Org = $buxoro; Point = $C['P_KUMUSH']; Transport = '80 HA 101'; GrossWeighing = (T '2026-10-25 23:50:00'); Mass = [decimal]3000; PostedAt = (T '2026-10-26 00:00:01') }
)

function New-Selection($rows) {
  $sel = [pscustomobject]@{ Rows = @($rows); Index = -1 }
  $sel | Add-Member -MemberType ScriptMethod -Name Next -Value {
    $this.Index += 1
    if ($this.Index -ge @($this.Rows).Count) { return $false }
    $row = @($this.Rows)[$this.Index]
    foreach ($key in @($row.Keys)) {
      $this | Add-Member -NotePropertyName $key -NotePropertyValue $row[$key] -Force
    }
    return $true
  }
  return $sel
}

function New-FakeQuery {
  $q = [pscustomobject]@{ Text = ''; Params = @{}; FailOn = $script:failOn }
  $q | Add-Member -MemberType ScriptMethod -Name SetParameter -Value { param($name, $value) $this.Params[$name] = $value }
  $q | Add-Member -MemberType ScriptMethod -Name Execute -Value {
    if (-not $this.Text) { throw 'the query has no text' }
    if ($this.Text.Contains($C['VYVOZ_DOC'])) { $rows = $vyvozRows; $field = 'SecondWeighing'; $kind = 'vyvoz' }
    elseif ($this.Text.Contains($C['REESTR_DOC'])) { $rows = $reestrRows; $field = 'PostedAt'; $kind = 'reestr' }
    else { throw 'unknown query' }
    if ($this.FailOn -eq $kind) { throw ('query failed for ' + $script:expected) }
    $since = $this.Params['Since']; $until = $this.Params['Until']
    $picked = @($rows | Where-Object { $_[$field] -ge $since -and $_[$field] -le $until })
    $result = [pscustomobject]@{ Picked = $picked }
    $result | Add-Member -MemberType ScriptMethod -Name Select -Value { return New-Selection $this.Picked }
    return $result
  }
  return $q
}

$script:failOn = ''
$script:queryType = 'Query'
$conn = [pscustomobject]@{}
$conn | Add-Member -MemberType ScriptMethod -Name NewObject -Value {
  param($type)
  if ($type -ne $script:queryType) { throw ('Type is not defined (' + $type + ')') }
  return New-FakeQuery
}

. $Export -LoadOnly

$Server = 'srv-1c'
$Base = 'uzpaxta'
$CredentialsFile = Join-Path $Folder 'credentials.txt'
$OutDir = Join-Path $Folder 'out'
$Week = $WeekArg
$Today = $TodayArg
$script:expected = Get-Content -LiteralPath (Join-Path $Folder 'expected.txt') -Encoding UTF8 -ErrorAction SilentlyContinue
$script:conn = $conn

$connect = {
  param($connectionString)
  if ($connectionString -ne $script:expected) { throw 'the export built an unexpected connection string' }
  return $script:conn
}

$lock = $null
switch ($Scenario) {
  'ok' { }
  'russian-type' { $script:queryType = $C['ZAPROS'] }
  'refused' { $connect = { param($connectionString) throw ('refused: ' + $connectionString) } }
  'reestr-fails' { $script:failOn = 'reestr' }
  'locked' {
    $lock = [System.IO.File]::Open((Join-Path $OutDir 'onec_pk17_reestr_nedelya.csv'), 'Open', 'ReadWrite', 'None')
  }
  'no-outdir' { $OutDir = Join-Path $Folder 'missing' }
  default { throw ('unknown scenario ' + $Scenario) }
}

try {
  $code = Invoke-Main $connect
} finally {
  if ($null -ne $lock) { $lock.Close() }
}
exit $code
