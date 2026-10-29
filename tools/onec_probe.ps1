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
#   0  the file is written, every part read
#   4  the file is written, some parts failed: each is a line starting with
#      '!' that names the part and the 1C member it stopped on
#   2  bad input: no credentials file, a key missing or unknown, no folder
#      for -Out; nothing is written
#   3  1C refused the connection (wrong login or password, no right
#      "external connection", no licence): the masked 1C text is in -Out
#   1  the probe crashed outside its parts
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

$ProbeVersion = 'onec_probe v2'

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

# [REASON]: the second run on the server (29.10) stopped on
# DISP_E_UNKNOWNNAME without saying which name 1C did not know. Each 1C
# member is now asked for by its English name and then by its Russian one
# (1C knows both for platform objects, but which one a given object answers
# to through COM is exactly what we could not see), and a member neither
# name reaches is reported by both names.
$Alternates = @{}
$Alternates['Metadata'] = [regex]::Unescape('\u041c\u0435\u0442\u0430\u0434\u0430\u043d\u043d\u044b\u0435')
$Alternates['Name'] = [regex]::Unescape('\u0418\u043c\u044f')
$Alternates['Synonym'] = [regex]::Unescape('\u0421\u0438\u043d\u043e\u043d\u0438\u043c')
$Alternates['Version'] = [regex]::Unescape('\u0412\u0435\u0440\u0441\u0438\u044f')
$Alternates['Reports'] = [regex]::Unescape('\u041e\u0442\u0447\u0435\u0442\u044b')
$Alternates['Count'] = [regex]::Unescape('\u041a\u043e\u043b\u0438\u0447\u0435\u0441\u0442\u0432\u043e')
$Alternates['Get'] = [regex]::Unescape('\u041f\u043e\u043b\u0443\u0447\u0438\u0442\u044c')
$Alternates['MainDataCompositionSchema'] = [regex]::Unescape('\u041e\u0441\u043d\u043e\u0432\u043d\u0430\u044f\u0421\u0445\u0435\u043c\u0430\u041a\u043e\u043c\u043f\u043e\u043d\u043e\u0432\u043a\u0438\u0414\u0430\u043d\u043d\u044b\u0445')
$Alternates['Attributes'] = [regex]::Unescape('\u0420\u0435\u043a\u0432\u0438\u0437\u0438\u0442\u044b')
$Alternates['Templates'] = [regex]::Unescape('\u041c\u0430\u043a\u0435\u0442\u044b')
$Alternates['Type'] = [regex]::Unescape('\u0422\u0438\u043f')
$Alternates['Create'] = [regex]::Unescape('\u0421\u043e\u0437\u0434\u0430\u0442\u044c')
$Alternates['GetTemplate'] = [regex]::Unescape('\u041f\u043e\u043b\u0443\u0447\u0438\u0442\u044c\u041c\u0430\u043a\u0435\u0442')
$Alternates['Parameters'] = [regex]::Unescape('\u041f\u0430\u0440\u0430\u043c\u0435\u0442\u0440\u044b')
$Alternates['Title'] = [regex]::Unescape('\u0417\u0430\u0433\u043e\u043b\u043e\u0432\u043e\u043a')
$Alternates['ValueType'] = [regex]::Unescape('\u0422\u0438\u043f\u0417\u043d\u0430\u0447\u0435\u043d\u0438\u044f')
$Alternates['Value'] = [regex]::Unescape('\u0417\u043d\u0430\u0447\u0435\u043d\u0438\u0435')
$Alternates['DataSets'] = [regex]::Unescape('\u041d\u0430\u0431\u043e\u0440\u044b\u0414\u0430\u043d\u043d\u044b\u0445')
$Alternates['Query'] = [regex]::Unescape('\u0417\u0430\u043f\u0440\u043e\u0441')
$Alternates['Items'] = [regex]::Unescape('\u042d\u043b\u0435\u043c\u0435\u043d\u0442\u044b')
$Alternates['ObjectName'] = [regex]::Unescape('\u0418\u043c\u044f\u041e\u0431\u044a\u0435\u043a\u0442\u0430')
$Alternates['Fields'] = [regex]::Unescape('\u041f\u043e\u043b\u044f')
$Alternates['DataPath'] = [regex]::Unescape('\u041f\u0443\u0442\u044c\u041a\u0414\u0430\u043d\u043d\u044b\u043c')
$Alternates['Field'] = [regex]::Unescape('\u041f\u043e\u043b\u0435')
$Alternates['CalculatedFields'] = [regex]::Unescape('\u0412\u044b\u0447\u0438\u0441\u043b\u044f\u0435\u043c\u044b\u0435\u041f\u043e\u043b\u044f')
$Alternates['TotalFields'] = [regex]::Unescape('\u041f\u043e\u043b\u044f\u0418\u0442\u043e\u0433\u0430')
$Alternates['Expression'] = [regex]::Unescape('\u0412\u044b\u0440\u0430\u0436\u0435\u043d\u0438\u0435')
$Alternates['SettingsVariants'] = [regex]::Unescape('\u0412\u0430\u0440\u0438\u0430\u043d\u0442\u044b\u041d\u0430\u0441\u0442\u0440\u043e\u0435\u043a')
$Alternates['Presentation'] = [regex]::Unescape('\u041f\u0440\u0435\u0434\u0441\u0442\u0430\u0432\u043b\u0435\u043d\u0438\u0435')
$Alternates['Subsystems'] = [regex]::Unescape('\u041f\u043e\u0434\u0441\u0438\u0441\u0442\u0435\u043c\u044b')
$Alternates['Content'] = [regex]::Unescape('\u0421\u043e\u0441\u0442\u0430\u0432')
$Alternates['FullName'] = [regex]::Unescape('\u041f\u043e\u043b\u043d\u043e\u0435\u0418\u043c\u044f')
$Alternates['Documents'] = [regex]::Unescape('\u0414\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u044b')
$Alternates['AccumulationRegisters'] = [regex]::Unescape('\u0420\u0435\u0433\u0438\u0441\u0442\u0440\u044b\u041d\u0430\u043a\u043e\u043f\u043b\u0435\u043d\u0438\u044f')
$Alternates['InformationRegisters'] = [regex]::Unescape('\u0420\u0435\u0433\u0438\u0441\u0442\u0440\u044b\u0421\u0432\u0435\u0434\u0435\u043d\u0438\u0439')
$Alternates['String'] = [regex]::Unescape('\u0421\u0442\u0440\u043e\u043a\u0430')

$DispUnknownName = -2147352570   # 0x80020006, DISP_E_UNKNOWNNAME

function Test-UnknownName($err) {
  $e = $err.Exception
  while ($null -ne $e) {
    if ($e.HResult -eq $DispUnknownName -or $e -is [System.MissingMemberException]) { return $true }
    $e = $e.InnerException
  }
  return $false
}

# [REASON]: a 1C collection supports For Each over COM, and PowerShell
# unrolls every enumerable value a function returns into its elements: the
# caller would get an array instead of the collection (the first run on the
# server failed so on 29.10). The value leaves through `return ,$value`,
# which hands the object over whole; the callers pass command output on
# with a plain `return`, which does not unroll.
function Invoke-Raw($obj, [string]$name, [bool]$method, [object[]]$arguments) {
  if (Test-Com $obj) {
    if ($method) {
      # An argument that came out of a function is wrapped in a PSObject;
      # 1C must get its own object back (String() of a type description),
      # not a .NET wrapper, so each argument goes over as its base object.
      $plain = New-Object 'object[]' $arguments.Count
      for ($i = 0; $i -lt $arguments.Count; $i++) {
        if ($null -ne $arguments[$i]) { $plain[$i] = $arguments[$i].PSObject.BaseObject }
      }
      $value = $obj.GetType().InvokeMember($name, [System.Reflection.BindingFlags]::InvokeMethod, $null, $obj, $plain)
    } else {
      $value = $obj.GetType().InvokeMember($name, [System.Reflection.BindingFlags]::GetProperty, $null, $obj, $null)
    }
  } else {
    # A stand-in object answers like COM: a member it does not have is an
    # unknown name, not a silent $null.
    $member = $obj.PSObject.Members[$name]
    if ($null -eq $member) { throw (New-Object System.MissingMemberException($name)) }
    if ($method) { $value = $member.Invoke($arguments) } else { $value = $member.Value }
  }
  return ,$value
}

function Invoke-Named($obj, [string]$name, [bool]$method, [object[]]$arguments) {
  $names = @($name)
  if ($Alternates.ContainsKey($name)) { $names += $Alternates[$name] }
  foreach ($candidate in $names) {
    try {
      return Invoke-Raw $obj $candidate $method $arguments
    } catch {
      if (-not (Test-UnknownName $_)) { throw }
    }
  }
  throw (New-Object System.MissingMemberException(('1C does not know the member ' + ($names -join ' / '))))
}

function Get-Prop($obj, [string]$name) {
  if ($null -eq $obj) { return $null }
  return Invoke-Named $obj $name $false @()
}

function Invoke-Method($obj, [string]$name, [object[]]$arguments = @()) {
  return Invoke-Named $obj $name $true $arguments
}

function Get-Items($collection) {
  $items = New-Object System.Collections.ArrayList
  if ($null -eq $collection) { return ,$items }
  # [REASON]: PowerShell walks a 1C collection by itself -- it did so on the
  # server on 29.10, unasked. That is the first way; Count()/Get() of 1C
  # stay for a collection PowerShell does not see as one.
  $enumerator = [System.Management.Automation.LanguagePrimitives]::GetEnumerator($collection)
  if ($null -ne $enumerator) {
    while ($enumerator.MoveNext()) { [void]$items.Add($enumerator.Current) }
    return ,$items
  }
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

# [REASON]: one failing part must not cost the owner the rest of the file:
# every run on the server is a round trip. A part that fails is written as
# a line starting with '!' and counted; the probe goes on to the next part.
function Add-Failure($lines, [string]$part, $err) {
  $script:Failed += 1
  Add-Line $lines ('  ! {0} failed: {1}' -f $part, (Hide-Secret $err.Exception.Message $script:Secret))
}

function Add-MetaList($conn, $lines, [string]$title, $metadata, [string]$member) {
  try {
    $items = Get-Items (Get-Prop $metadata $member)
  } catch {
    Add-Line $lines ('=== {0}' -f $title)
    Add-Failure $lines $title $_
    return
  }
  Add-Line $lines ('=== {0}: {1}' -f $title, $items.Count)
  foreach ($item in $items) {
    try {
      Add-Line $lines ('  {0} | {1}' -f (Get-Prop $item 'Name'), (Get-Text $conn (Get-Prop $item 'Synonym')))
    } catch {
      Add-Failure $lines ($title + ' item') $_
    }
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

function Add-Report($conn, $lines, $meta, [string]$name, [string]$synonym) {
  Add-Line $lines ('=== report {0} | {1}' -f $name, $synonym)
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
    Add-Line $lines ('  schema not read: ' + (Hide-Secret $_.Exception.Message $script:Secret))
  }
}

function Get-MemberNames($obj) {
  # What PowerShell itself sees on the object: on a 1C object this shows in
  # which language its members answer, if any name above was unknown.
  try {
    $names = @(Get-Member -InputObject $obj -ErrorAction Stop | ForEach-Object { $_.Name } | Sort-Object -Unique)
  } catch {
    return 'Get-Member failed: ' + $_.Exception.Message
  }
  if ($names.Count -eq 0) { return '(none)' }
  return (@($names | Select-Object -First 80)) -join ', '
}

function Invoke-Probe($conn, [string[]]$wanted) {
  $lines = New-Object System.Collections.ArrayList
  $script:Failed = 0
  Add-Line $lines $ProbeVersion
  $metadata = $null
  $reports = New-Object System.Collections.ArrayList
  $chosen = New-Object System.Collections.ArrayList
  try {
    $metadata = Get-Prop $conn 'Metadata'
    Add-Line $lines ('configuration: {0} | {1} | version {2}' -f (Get-Prop $metadata 'Name'),
      (Get-Text $conn (Get-Prop $metadata 'Synonym')), (Get-Prop $metadata 'Version'))
  } catch {
    Add-Failure $lines 'configuration' $_
  }
  try {
    $reports = Get-Items (Get-Prop $metadata 'Reports')
    Add-Line $lines ('=== reports: {0}' -f $reports.Count)
  } catch {
    Add-Line $lines '=== reports'
    Add-Failure $lines 'reports' $_
  }
  $number = 0
  foreach ($meta in $reports) {
    $number += 1
    try {
      $name = [string](Get-Prop $meta 'Name')
      $synonym = Get-Text $conn (Get-Prop $meta 'Synonym')
      $schema = if ($null -eq (Get-Prop $meta 'MainDataCompositionSchema')) { 'no' } else { 'yes' }
      $pick = if ($wanted.Count -gt 0) { $wanted -contains $name } else { ($name + ' ' + $synonym) -match $ReportPattern }
      $mark = if ($pick) { '*' } else { ' ' }
      Add-Line $lines ('{0} {1} | {2} | schema {3}' -f $mark, $name, $synonym, $schema)
      if ($pick -and $chosen.Count -lt $MaxDetailed) { [void]$chosen.Add(@($meta, $name, $synonym)) }
    } catch {
      Add-Failure $lines ('report #' + $number) $_
    }
  }
  foreach ($entry in $chosen) {
    try {
      Add-Report $conn $lines $entry[0] $entry[1] $entry[2]
    } catch {
      Add-Failure $lines ('report ' + $entry[1]) $_
    }
  }
  Add-Line $lines '=== subsystems'
  try {
    Add-Subsystems $conn $lines (Get-Prop $metadata 'Subsystems') '  '
  } catch {
    Add-Failure $lines 'subsystems' $_
  }
  Add-MetaList $conn $lines 'documents' $metadata 'Documents'
  Add-MetaList $conn $lines 'accumulation registers' $metadata 'AccumulationRegisters'
  Add-MetaList $conn $lines 'information registers' $metadata 'InformationRegisters'
  Add-Line $lines '=== members seen by PowerShell'
  Add-Line $lines ('  connection: ' + (Get-MemberNames $conn))
  if ($null -ne $metadata) { Add-Line $lines ('  metadata: ' + (Get-MemberNames $metadata)) }
  return @{ Lines = $lines; Reports = $reports.Count; Detailed = $chosen.Count; Failed = $script:Failed }
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
  # [REASON]: only error texts are masked -- they are where a 1C message
  # could echo the connection string. Masking the whole file would cut a
  # short password out of query texts and names.
  $script:Secret = $password
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
  Write-Host ('connected; reports {0}; described {1}; failed parts {2}' -f $result.Reports, $result.Detailed, $result.Failed)
  Write-Host ('written: ' + $Out)
  Write-Host 'READ ONLY: nothing was written to 1C'
  if ($result.Failed -gt 0) { return 4 }
  return 0
}

if ($LoadOnly) { return }

$code = Invoke-Main {
  param($connectionString)
  $connector = New-Object -ComObject 'V83.COMConnector'
  return ,($connector.Connect($connectionString))
}
exit $code
