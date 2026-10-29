# tools/onec_export.ps1 -- GPS-37: the weekly 1C export for GPS-35, by itself.
#
# WHY
# The owner's decision of 27.10.2026 (GPS track, 0.7): the weekly cotton
# hauling list must come without a chat clicking in 1C windows. The probe of
# 29.10 (tools/onec_probe.ps1) read how the two reports the neighbour chat
# exported are built; this script asks 1C the same questions over the COM
# connection and writes the two files GPS-35 reads, for the last full week
# (Monday to Sunday) or the week named by -Week.
#
# WHAT IT ASKS (tools/onec_export/*.json, one query each)
#   vyvoz  -- removal PTM -> plant: document VyvozKhlopkaSyrtsaIzPunktovPriema
#             NaZavod_Shlyuz, not marked for deletion, second weighing in the
#             week (the period field of the report's schema);
#   reestr -- receiving on PTM (the PK-17 register): document Postuplenie
#             KhlopkaSyrtsa, posted, posting date in the week; every
#             organization at once (the report takes one at a time).
# The columns and their headers are the neighbour chat's files, so GPS-35
# reads either; "Gos nomer (iz nazvaniya)" is cut from the end of the
# transport name exactly as there ("MTZ-80-1 80 283 HA" -> "80 283 HA").
#
# WHAT IT DOES NOT DO
# Writes nothing to 1C: it runs two SELECT queries. Takes no farmer, no
# commodity expert, nothing but point, transport, time, mass, organization.
# A file is replaced only when both queries succeeded: the two files go to
# temporary names first and are moved over the old ones at the end, so a
# failed run leaves last week's files whole and says so by its exit code.
#
# RUN (Windows PowerShell 5.1 on the server)
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File C:\gps-tools\tools\onec_export.ps1 -Server srv-1c -Base uzpaxta -CredentialsFile C:\VehicleSoft_Secrets\onec_credentials.txt -OutDir C:\gps-tools\check
#
# -Week 2026-10-19 exports that week (a Monday) instead of the last one.
#
# EXIT CODES
#   0  both files written
#   2  bad input: no credentials file, a key missing or unknown, no -OutDir
#      folder, -Week not a Monday; nothing is written
#   3  1C refused the connection; nothing is written
#   1  a query failed; nothing is written, the old files stay
#
# ASCII only (Windows PowerShell 5.1 reads a file without a BOM as ANSI);
# the Cyrillic queries and headers live in the UTF-8 json files.
param(
  [string]$Server = '',
  [string]$Base = '',
  [string]$CredentialsFile = '',
  [string]$OutDir = '',
  [string]$Week = '',
  [string]$Today = '',
  [switch]$LoadOnly
)

$ExportVersion = 'onec_export v2'
$SpecDir = Join-Path $PSScriptRoot 'onec_export'
$SpecNames = @('vyvoz', 'reestr')
# [REASON]: the plate is the tail of the 1C transport name -- two digits of
# the region, three digits, two or three letters (all 54 tractors of the
# week 19-25.10 are written so). The neighbour chat's file cut it the same
# way; GPS-35 joins by it.
$PlatePattern = '(\d{2})\s*(\d{3})\s*([A-Za-z\u0400-\u04FF]{2,3})\s*$'
$Invariant = [System.Globalization.CultureInfo]::InvariantCulture

. (Join-Path $PSScriptRoot 'onec_com.ps1')

function Get-WeekStart([string]$week, [string]$today) {
  if ($week) {
    try {
      $start = [datetime]::ParseExact($week, 'yyyy-MM-dd', $Invariant)
    } catch {
      throw ('-Week is YYYY-MM-DD: ' + $week)
    }
    if ($start.DayOfWeek -ne [System.DayOfWeek]::Monday) { throw ('-Week is not a Monday: ' + $week) }
    return $start
  }
  $day = if ($today) { [datetime]::ParseExact($today, 'yyyy-MM-dd', $Invariant) } else { (Get-Date).Date }
  # Monday of this week, then one week back: the last full week.
  $monday = $day.AddDays(-((([int]$day.DayOfWeek) + 6) % 7))
  return $monday.AddDays(-7)
}

function Read-Spec([string]$name) {
  $path = Join-Path $SpecDir ($name + '.json')
  return ([System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8) | ConvertFrom-Json)
}

function ConvertTo-Cell($value) {
  if ($null -eq $value) { return '' }
  if ($value -is [datetime]) { return $value.ToString('yyyy-MM-dd HH:mm:ss', $Invariant) }
  if ($value -is [bool]) { return ([string]$value).ToLowerInvariant() }
  if ($value -is [ValueType]) { return ([System.IConvertible]$value).ToString($Invariant) }
  return [string]$value
}

function Get-Plate([string]$name) {
  $found = [regex]::Match($name, $PlatePattern)
  if (-not $found.Success) { return '' }
  return '{0} {1} {2}' -f $found.Groups[1].Value, $found.Groups[2].Value, $found.Groups[3].Value
}

function ConvertTo-CsvLine([string[]]$cells) {
  return ($cells | ForEach-Object { '"' + $_.Replace('"', '""') + '"' }) -join ';'
}

function New-Query($conn) {
  # The type is asked for by its English name, then by its Russian one
  # ("Zapros"), as the members are (tools/onec_com.ps1).
  try {
    return Invoke-Method $conn 'NewObject' @('Query')
  } catch {
    $first = $_.Exception.Message
  }
  try {
    return Invoke-Method $conn 'NewObject' @([regex]::Unescape('\u0417\u0430\u043f\u0440\u043e\u0441'))
  } catch {
    throw ('1C made no query object: ' + $first + ' / ' + $_.Exception.Message)
  }
}

function Invoke-Export($conn, $spec, [datetime]$since, [datetime]$until) {
  $query = New-Query $conn
  Set-Prop $query 'Text' $spec.query
  [void](Invoke-Method $query 'SetParameter' @('Since', $since))
  [void](Invoke-Method $query 'SetParameter' @('Until', $until))
  $selection = Invoke-Method (Invoke-Method $query 'Execute') 'Select'
  $lines = New-Object System.Collections.ArrayList
  [void]$lines.Add((ConvertTo-CsvLine @($spec.columns | ForEach-Object { $_.header })))
  $count = 0
  $sum = [decimal]0
  while ([bool](Invoke-Method $selection 'Next')) {
    # [REASON]: a reference is turned into text by String() of 1C -- the
    # name forms and reports show ("MTZ-80-1 80 283 HA"); PRESENTATION() in
    # the query gives the model only, without the plate (export 29.10).
    $cells = foreach ($column in $spec.columns) {
      if ($column.plate_from) {
        Get-Plate (Get-Text $conn (Get-Prop $selection $column.plate_from))
      } elseif ($column.as_text) {
        Get-Text $conn (Get-Prop $selection $column.alias)
      } else {
        ConvertTo-Cell (Get-Prop $selection $column.alias)
      }
    }
    [void]$lines.Add((ConvertTo-CsvLine @($cells)))
    $mass = Get-Prop $selection $spec.sum
    if ($null -ne $mass) { $sum += [decimal]$mass }
    $count += 1
  }
  return @{ Lines = $lines; Count = $count; Sum = $sum }
}

# [REASON]: the console gets Write-Host, never Write-Output: output of a
# function is its return value, and a line written to the pipeline would
# arrive in `exit` together with the code.
function Invoke-Main([scriptblock]$Connect) {
  try {
    if (-not $Server -or -not $Base) { throw 'give -Server and -Base' }
    if (-not $OutDir -or -not (Test-Path -LiteralPath $OutDir -PathType Container)) {
      throw ('no folder -OutDir: ' + $OutDir)
    }
    $start = Get-WeekStart $Week $Today
    $credentials = Read-OneCCredentials $CredentialsFile
    $specs = @($SpecNames | ForEach-Object { Read-Spec $_ })
  } catch {
    Write-Host ('ERROR: ' + (ConvertTo-Ascii $_.Exception.Message))
    return 2
  }
  $since = $start
  $until = $start.AddDays(7).AddSeconds(-1)
  Write-Host ('{0} | week {1} .. {2}' -f $ExportVersion, $since.ToString('yyyy-MM-dd'), $until.ToString('yyyy-MM-dd'))
  $password = $credentials['password']
  $text = ConvertTo-ConnectionString $Server $Base $credentials['login'] $password
  try {
    $conn = & $Connect $text
  } catch {
    Write-Host ('ERROR: 1C refused the connection: ' + (ConvertTo-Ascii (Hide-Secret $_.Exception.Message $password)))
    return 3
  }
  $results = @{}
  try {
    for ($i = 0; $i -lt $SpecNames.Count; $i++) {
      $results[$SpecNames[$i]] = Invoke-Export $conn $specs[$i] $since $until
    }
  } catch {
    Write-Host ('ERROR: the {0} query failed, nothing was written: {1}' -f $SpecNames[$i],
      (ConvertTo-Ascii (Hide-Secret $_.Exception.Message $password)))
    return 1
  } finally {
    if (Test-Com $conn) {
      # The 1C session ends with the last reference: the licence is given
      # back at once.
      [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($conn)
    }
  }
  # Both answers are in hand: only now are the old files replaced. A file
  # held open (Excel) is found before any is moved -- otherwise one week's
  # removal could end up next to the previous week's register.
  for ($i = 0; $i -lt $SpecNames.Count; $i++) {
    $target = Join-Path $OutDir $specs[$i].file
    if (Test-Path -LiteralPath $target) {
      try {
        ([System.IO.File]::Open($target, 'Open', 'ReadWrite', 'None')).Close()
      } catch {
        Write-Host ('ERROR: {0} is open in another program, nothing was written' -f $target)
        return 1
      }
    }
  }
  for ($i = 0; $i -lt $SpecNames.Count; $i++) {
    $target = Join-Path $OutDir $specs[$i].file
    Write-Utf8 ($target + '.tmp') $results[$SpecNames[$i]].Lines
  }
  for ($i = 0; $i -lt $SpecNames.Count; $i++) {
    $target = Join-Path $OutDir $specs[$i].file
    Move-Item -LiteralPath ($target + '.tmp') -Destination $target -Force
    Write-Host ('{0}: {1} rows | sum {2} | {3}' -f $SpecNames[$i], $results[$SpecNames[$i]].Count,
      $results[$SpecNames[$i]].Sum.ToString($Invariant), $target)
  }
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
