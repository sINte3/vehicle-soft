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

# Credentials, the connection string, masking and the COM helpers are
# shared with tools/onec_export.ps1.
. (Join-Path $PSScriptRoot 'onec_com.ps1')

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
