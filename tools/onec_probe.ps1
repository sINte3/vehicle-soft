# tools/onec_probe.ps1 -- GPS-36: read-only look inside the 1C infobase.
#
# WHY
# The owner's decision of 27.10.2026 (GPS track, 0.10): the weekly 1C export
# for GPS-35 must run by itself on the server, with no chat and no clicking
# in 1C windows. There is no 1C programmer, so the export will be a script
# over the 1C COM connection (V83.COMConnector, registered on the server for
# platform 8.3.10.2299 x64; the infobase is Srvr="srv-1c";Ref="uzpaxta").
# Before that script can be written we must know how the two reports the
# neighbour chat exports are built: their internal names, the data
# composition schema (query text, parameters, fields) or, for an old-style
# report, its attributes and templates; plus the documents, registers and
# the subsystem "cotton procurement" they sit in. This probe writes exactly
# that to a UTF-8 text file. Nothing else.
#
# WHAT IT DOES NOT DO
# Writes nothing to 1C: it reads metadata and the report's own schema; it
# runs no query and builds no report. The login and password are read from
# a key=value file (login=..., password=..., the same format as the
# agro-work credentials) and are never printed or written: any 1C error text
# is written with the password masked.
#
# RUN (Windows PowerShell 5.1 on the server; the file lives in
# C:\VehicleSoft_Secrets like the agro-work one)
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File C:\gps-tools\tools\onec_probe.ps1 -Server srv-1c -Base uzpaxta -CredentialsFile C:\VehicleSoft_Secrets\onec_credentials.txt -Out C:\gps-tools\check\onec_probe.txt
#
# EXIT CODES
#   0  the file is written
#   2  bad input: no credentials file, a key missing or unknown, no folder
#      for -Out; nothing is written
#   3  1C refused the connection (wrong login or password, no right
#      "external connection", no licence): the masked 1C text is in -Out
#   1  the probe crashed
#
# ASCII only: Windows PowerShell 5.1 reads a file without a BOM as ANSI, so
# Cyrillic search words below are \u escapes inside .NET regular expressions.
param(
  [string]$Server = '',
  [string]$Base = '',
  [string]$CredentialsFile = '',
  [string]$Out = '',
  [string[]]$Report = @(),
  [switch]$LoadOnly
)

$ProbeVersion = 'onec_probe v1'

# [REASON]: the two reports are found by their synonyms -- "reestr" (the
# PK-17 register, "jadval 7"), "vyvoz" (removal by gross weighing date),
# "vzvesh" (weighing), "brutto", "jadval", "PK-17". A wider net than needed
# is cheap: every match is described, and the owner sends one text file.
$ReportPattern = '(?i)\u0440\u0435\u0435\u0441\u0442\u0440|\u0432\u044b\u0432\u043e\u0437|\u0432\u0437\u0432\u0435\u0448|\u0431\u0440\u0443\u0442\u0442\u043e|\u0436\u0430\u0434\u0432\u0430\u043b|\u043f\u043a-?17'
# Subsystems whose content is listed: "khlop" (cotton), "zagotov"
# (procurement), "pakhta" (cotton, Uzbek).
$SubsystemPattern = '(?i)\u0445\u043b\u043e\u043f|\u0437\u0430\u0433\u043e\u0442\u043e\u0432|\u043f\u0430\u0445\u0442\u0430'
$MaxDetailed = 30

function Read-OneCCredentials([string]$path) {
  if (-not $path -or -not (Test-Path -LiteralPath $path -PathType Leaf)) {
    throw ('credentials file not found: ' + $path)
  }
  $bytes = [System.IO.File]::ReadAllBytes($path)
  # [REASON]: Notepad on the server writes either UTF-8 (with a BOM) or
  # ANSI cp1251, depending on the Windows version -- as for the agro-work
  # credentials (agro_work/config.py). A Cyrillic password must not arrive
  # garbled, so strict UTF-8 first and cp1251 only if that fails.
  try {
    $text = (New-Object System.Text.UTF8Encoding($false, $true)).GetString($bytes)
  } catch {
    $text = [System.Text.Encoding]::GetEncoding(1251).GetString($bytes)
  }
  $text = $text.TrimStart([char]0xFEFF)
  $values = @{}
  $number = 0
  foreach ($line in ($text -split "`r?`n")) {
    $number += 1
    $stripped = $line.Trim()
    if (-not $stripped -or $stripped.StartsWith('#')) { continue }
    $at = $stripped.IndexOf('=')
    if ($at -lt 0) {
      throw "line $number has no '=': expected key=value"
    }
    $key = $stripped.Substring(0, $at).Trim().ToLowerInvariant()
    # [REASON]: an unknown key is refused, not skipped: a typo such as
    # "pasword=" would otherwise become "no password" at the 1C door. The
    # message names the line, never the value.
    if ($key -ne 'login' -and $key -ne 'password') {
      throw "line ${number}: unknown key (expected login or password)"
    }
    $values[$key] = $stripped.Substring($at + 1).Trim()
  }
  foreach ($key in @('login', 'password')) {
    if (-not $values.ContainsKey($key) -or -not $values[$key]) {
      throw "credentials file has no $key="
    }
  }
  return $values
}

function ConvertTo-ConnectionString([string]$server, [string]$base, [string]$login, [string]$password) {
  # A double quote inside a 1C connection string value is written twice.
  $parts = @(('Srvr', $server), ('Ref', $base), ('Usr', $login), ('Pwd', $password))
  return (($parts | ForEach-Object { '{0}="{1}"' -f $_[0], ($_[1] -replace '"', '""') }) -join ';') + ';'
}

function ConvertTo-Ascii([string]$text) {
  # The console of the server gets ASCII only (charter); a localized .NET
  # message keeps its shape with '?' in place of each non-ASCII letter.
  return ($text -replace '[^\x20-\x7E]', '?')
}

function Hide-Secret([string]$text, [string]$secret) {
  if ($null -eq $text) { return '' }
  if (-not $secret) { return $text }
  # [REASON]: a 1C error may echo the connection string, where a quote of
  # the password is doubled (ConvertTo-ConnectionString): the escaped form
  # is masked first, or `Pwd="ab""c"` would survive a search for `ab"c`.
  return $text.Replace(($secret -replace '"', '""'), '***').Replace($secret, '***')
}

# 1C objects reach PowerShell as late-bound COM: every member is resolved
# through IDispatch by name, which also works for names only known at run
# time (a report manager). The tests pass plain objects with the same
# member names, so both paths are kept. `IsCOMObject`, not the type
# literal [System.__ComObject]: the literal does not exist outside Windows,
# where the tests also run.
function Test-Com($obj) {
  return ($null -ne $obj -and $obj.GetType().IsCOMObject)
}

# [REASON]: a 1C collection supports For Each over COM, and PowerShell
# unrolls every enumerable a function returns into its elements: the caller
# would get an array of report descriptions instead of the collection, and
# Count() of 1C would turn into the array's length. That is how the probe
# failed on the server on 29.10. Every value leaves these helpers through
# one `return ,$value`, which hands the object over whole.
function Get-Prop($obj, [string]$name) {
  if ($null -eq $obj) { return $null }
  if (Test-Com $obj) {
    $value = $obj.GetType().InvokeMember($name, [System.Reflection.BindingFlags]::GetProperty, $null, $obj, $null)
  } else {
    $value = $obj.$name
  }
  return ,$value
}

function Invoke-Method($obj, [string]$name, [object[]]$arguments = @()) {
  if (Test-Com $obj) {
    # An argument that came out of a function is wrapped in a PSObject; 1C
    # must get its own object back (String() of a type description), not a
    # .NET wrapper, so each argument goes over as its base object.
    $plain = New-Object 'object[]' $arguments.Count
    for ($i = 0; $i -lt $arguments.Count; $i++) {
      if ($null -ne $arguments[$i]) { $plain[$i] = $arguments[$i].PSObject.BaseObject }
    }
    $value = $obj.GetType().InvokeMember($name, [System.Reflection.BindingFlags]::InvokeMethod, $null, $obj, $plain)
  } else {
    $value = $obj.$name.Invoke($arguments)
  }
  return ,$value
}

function Get-Items($collection) {
  $items = New-Object System.Collections.ArrayList
  if ($null -eq $collection) { return ,$items }
  $count = [int](Invoke-Method $collection 'Count')
  for ($i = 0; $i -lt $count; $i++) {
    [void]$items.Add((Invoke-Method $collection 'Get' @($i)))
  }
  return ,$items
}

function Get-Text($conn, $value) {
  if ($null -eq $value) { return '' }
  if ($value -is [string]) { return $value }
  if ($value -is [ValueType]) { return [string]$value }
  return [string](Invoke-Method $conn 'String' @($value))
}

function Get-Field($obj, [string]$name) {
  # A member the object does not have (Query on a union data set) is not an
  # error of the probe: it is the answer "this data set is of another kind".
  try { return Get-Prop $obj $name } catch { return $null }
}

function Add-Line($lines, [string]$text) {
  [void]$lines.Add(($text -replace "`r?`n", ' '))
}

function Add-Block($lines, [string]$text) {
  foreach ($line in ($text -split "`r?`n")) { [void]$lines.Add('    | ' + $line) }
}

function Add-MetaList($conn, $lines, [string]$title, $collection) {
  $items = Get-Items $collection
  Add-Line $lines ('=== {0}: {1}' -f $title, $items.Count)
  foreach ($item in $items) {
    Add-Line $lines ('  {0} | {1}' -f (Get-Prop $item 'Name'), (Get-Text $conn (Get-Prop $item 'Synonym')))
  }
}

function Add-Subsystems($conn, $lines, $collection, [string]$indent) {
  foreach ($sub in (Get-Items $collection)) {
    $name = [string](Get-Prop $sub 'Name')
    $synonym = Get-Text $conn (Get-Prop $sub 'Synonym')
    $content = Get-Items (Get-Prop $sub 'Content')
    Add-Line $lines ('{0}S {1} | {2} | objects {3}' -f $indent, $name, $synonym, $content.Count)
    if (($name + ' ' + $synonym) -match $SubsystemPattern) {
      foreach ($item in $content) {
        Add-Line $lines ('{0}    - {1}' -f $indent, (Invoke-Method $item 'FullName'))
      }
    }
    Add-Subsystems $conn $lines (Get-Prop $sub 'Subsystems') ($indent + '  ')
  }
}

function Add-Schema($conn, $lines, $schema) {
  $parameters = Get-Items (Get-Prop $schema 'Parameters')
  Add-Line $lines ('  parameters: {0}' -f $parameters.Count)
  foreach ($p in $parameters) {
    Add-Line $lines ('    P {0} | {1} | type {2} | value {3}' -f (Get-Prop $p 'Name'),
      (Get-Text $conn (Get-Prop $p 'Title')), (Get-Text $conn (Get-Prop $p 'ValueType')),
      (Get-Text $conn (Get-Prop $p 'Value')))
  }
  $sets = Get-Items (Get-Prop $schema 'DataSets')
  Add-Line $lines ('  data sets: {0}' -f $sets.Count)
  foreach ($set in $sets) { Add-DataSet $conn $lines $set '    ' }
  foreach ($kind in @('CalculatedFields', 'TotalFields')) {
    $fields = Get-Items (Get-Prop $schema $kind)
    Add-Line $lines ('  {0}: {1}' -f $kind, $fields.Count)
    foreach ($f in $fields) {
      Add-Line $lines ('    C {0} | {1} | {2}' -f (Get-Prop $f 'DataPath'),
        (Get-Text $conn (Get-Field $f 'Title')), (Get-Text $conn (Get-Prop $f 'Expression')))
    }
  }
  $variants = Get-Items (Get-Prop $schema 'SettingsVariants')
  Add-Line $lines ('  settings variants: {0}' -f $variants.Count)
  foreach ($v in $variants) {
    Add-Line $lines ('    V {0} | {1}' -f (Get-Prop $v 'Name'), (Get-Text $conn (Get-Prop $v 'Presentation')))
  }
}

function Add-DataSet($conn, $lines, $set, [string]$indent) {
  $query = Get-Field $set 'Query'
  $items = Get-Field $set 'Items'
  $object = Get-Field $set 'ObjectName'
  $kind = if ($null -ne $query) { 'query' } elseif ($null -ne $items) { 'union' } elseif ($null -ne $object) { 'object' } else { 'unknown' }
  Add-Line $lines ('{0}D {1} | {2}' -f $indent, (Get-Prop $set 'Name'), $kind)
  $fields = Get-Items (Get-Field $set 'Fields')
  foreach ($f in $fields) {
    Add-Line $lines ('{0}  F {1} | {2} | {3}' -f $indent, (Get-Prop $f 'DataPath'),
      (Get-Field $f 'Field'), (Get-Text $conn (Get-Field $f 'Title')))
  }
  if ($null -ne $query) {
    Add-Line $lines ($indent + '  query:')
    Add-Block $lines ([string]$query)
  }
  if ($null -ne $items) {
    foreach ($inner in (Get-Items $items)) { Add-DataSet $conn $lines $inner ($indent + '  ') }
  }
}

function Add-Report($conn, $lines, $meta) {
  $name = [string](Get-Prop $meta 'Name')
  Add-Line $lines ('=== report {0} | {1}' -f $name, (Get-Text $conn (Get-Prop $meta 'Synonym')))
  foreach ($a in (Get-Items (Get-Prop $meta 'Attributes'))) {
    Add-Line $lines ('  A {0} | {1} | {2}' -f (Get-Prop $a 'Name'),
      (Get-Text $conn (Get-Prop $a 'Synonym')), (Get-Text $conn (Get-Prop $a 'Type')))
  }
  foreach ($t in (Get-Items (Get-Prop $meta 'Templates'))) {
    Add-Line $lines ('  T {0}' -f (Get-Prop $t 'Name'))
  }
  $main = Get-Prop $meta 'MainDataCompositionSchema'
  if ($null -eq $main) {
    Add-Line $lines '  main schema: none (an old-style report: its logic is in the module)'
    return
  }
  $template = [string](Get-Prop $main 'Name')
  Add-Line $lines ('  main schema: ' + $template)
  try {
    $manager = Get-Prop (Get-Prop $conn 'Reports') $name
    $schema = Invoke-Method (Invoke-Method $manager 'Create') 'GetTemplate' @($template)
    Add-Schema $conn $lines $schema
  } catch {
    Add-Line $lines ('  schema not read: ' + $_.Exception.Message)
  }
}

function Invoke-Probe($conn, [string[]]$wanted) {
  $lines = New-Object System.Collections.ArrayList
  Add-Line $lines $ProbeVersion
  $metadata = Get-Prop $conn 'Metadata'
  Add-Line $lines ('configuration: {0} | {1} | version {2}' -f (Get-Prop $metadata 'Name'),
    (Get-Text $conn (Get-Prop $metadata 'Synonym')), (Get-Prop $metadata 'Version'))
  $reports = Get-Items (Get-Prop $metadata 'Reports')
  $chosen = New-Object System.Collections.ArrayList
  Add-Line $lines ('=== reports: {0}' -f $reports.Count)
  foreach ($meta in $reports) {
    $name = [string](Get-Prop $meta 'Name')
    $synonym = Get-Text $conn (Get-Prop $meta 'Synonym')
    $schema = if ($null -eq (Get-Prop $meta 'MainDataCompositionSchema')) { 'no' } else { 'yes' }
    $pick = if ($wanted.Count -gt 0) { $wanted -contains $name } else { ($name + ' ' + $synonym) -match $ReportPattern }
    $mark = if ($pick) { '*' } else { ' ' }
    Add-Line $lines ('{0} {1} | {2} | schema {3}' -f $mark, $name, $synonym, $schema)
    if ($pick -and $chosen.Count -lt $MaxDetailed) { [void]$chosen.Add($meta) }
  }
  foreach ($meta in $chosen) { Add-Report $conn $lines $meta }
  Add-Line $lines '=== subsystems'
  Add-Subsystems $conn $lines (Get-Prop $metadata 'Subsystems') '  '
  Add-MetaList $conn $lines 'documents' (Get-Prop $metadata 'Documents')
  Add-MetaList $conn $lines 'accumulation registers' (Get-Prop $metadata 'AccumulationRegisters')
  Add-MetaList $conn $lines 'information registers' (Get-Prop $metadata 'InformationRegisters')
  return @{ Lines = $lines; Reports = $reports.Count; Detailed = $chosen.Count }
}

function Write-Utf8([string]$path, $lines) {
  [System.IO.File]::WriteAllLines($path, [string[]]$lines, (New-Object System.Text.UTF8Encoding($true)))
}

# [REASON]: the console gets Write-Host, never Write-Output: output of a
# function is its return value, and a line written to the pipeline would
# arrive in `exit` together with the code.
function Invoke-Main([scriptblock]$Connect) {
  # Bad input: everything that can fail before 1C is touched. Our own
  # messages are ASCII; a .NET one (access denied on a localized Windows)
  # goes through ConvertTo-Ascii.
  try {
    if (-not $Server -or -not $Base) { throw 'give -Server and -Base' }
    if (-not $Out) { throw 'give -Out' }
    $folder = Split-Path -Parent ([System.IO.Path]::GetFullPath($Out))
    if (-not (Test-Path -LiteralPath $folder -PathType Container)) {
      throw ('no folder for -Out: ' + $folder)
    }
    $credentials = Read-OneCCredentials $CredentialsFile
  } catch {
    Write-Host ('ERROR: ' + (ConvertTo-Ascii $_.Exception.Message))
    return 2
  }
  # `-File script.ps1 -Report A,B` hands the script one string "A,B".
  $wanted = @($Report | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
  $password = $credentials['password']
  $text = ConvertTo-ConnectionString $Server $Base $credentials['login'] $password
  try {
    $conn = & $Connect $text
  } catch {
    $message = Hide-Secret $_.Exception.Message $password
    Write-Utf8 $Out @($ProbeVersion, 'connection refused by 1C:', $message)
    Write-Host ('ERROR: 1C refused the connection; the 1C message is in ' + $Out)
    return 3
  }
  try {
    $result = Invoke-Probe $conn $wanted
    Write-Utf8 $Out $result.Lines
  } catch {
    $message = Hide-Secret $_.Exception.Message $password
    Write-Utf8 $Out @($ProbeVersion, 'probe failed:', $message)
    Write-Host ('ERROR: the probe failed; the message is in ' + $Out)
    return 1
  } finally {
    if (Test-Com $conn) {
      # The 1C session ends with the last reference, not at process exit
      # only: the licence is given back at once.
      [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($conn)
    }
  }
  Write-Host ('connected; reports {0}; described {1}' -f $result.Reports, $result.Detailed)
  Write-Host ('written: ' + $Out)
  Write-Host 'READ ONLY: nothing was written to 1C'
  return 0
}

if ($LoadOnly) { return }

$code = Invoke-Main {
  param($connectionString)
  $connector = New-Object -ComObject 'V83.COMConnector'
  return ,($connector.Connect($connectionString))
}
exit $code
