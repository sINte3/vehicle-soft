# tools/onec_weekly.ps1 -- GPS-38: the weekly 1C export and the GPS-35 book,
# run by the Windows Task Scheduler on the server.
#
# WHY
# The owner's decision of 27.10.2026 (GPS track, 0.7): the weekly cotton
# hauling list comes by schedule on the server, into a folder there, 1C under
# his login -- not by asking a chat. The export script passed its acceptance
# on 29.10 (track 0.6, third run: MATCH -- the week 19-25.10 equal to the
# neighbour chat's files row for row), which was the condition for this.
#
# WHAT ONE RUN DOES
#   1. The week: the last full week Monday..Sunday before today, or -Week.
#   2. tools/onec_export.ps1 for that week into <Out>\vygruzka\<Monday>\, in
#      a PowerShell process of its own (the 1C session ends with it). Every
#      week keeps its own two files.
#   3. tools/gps_onec_hauling_report.py (GPS-35) on those two files and the
#      database (opened read only): the book
#      <Out>\perevozki_1c_<first day>_<last day>.xlsx.
#   4. Every line both print goes, with the time, to <Out>\perevozki_1c.log.
# An export that failed leaves no book: GPS-35 never runs on another week's
# files. Nothing is written to 1C or to the database.
#
# RUN BY HAND (the Task Scheduler runs the same line without -Week)
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File C:\gps-weekly\tools\onec_weekly.ps1
#
# -Week 2026-10-19 makes that week's book again (a Monday).
#
# EXIT CODES (the Task Scheduler shows them as "Last Run Result")
#   0     the book is written
#   2     bad input of this script: -Week, no Python, no database; nothing run
#   1N    the export failed with its own code N: 12 bad input, 13 1C refused
#         the connection, 11 a query failed; 19 it could not be started
#   2N    GPS-35 failed with its own code N: 22 bad input, 21 crashed;
#         29 it could not be started
#
# ASCII only (Windows PowerShell 5.1 reads a file without a BOM as ANSI).
param(
  [string]$Out = 'C:\perevozki_1c',
  [string]$Server = 'srv-1c',
  [string]$Base = 'uzpaxta',
  [string]$CredentialsFile = 'C:\VehicleSoft_Secrets\onec_credentials.txt',
  [string]$Db = 'C:\transport-report\instance\transport.db',
  [string]$Python = 'C:\Program Files\Python314\python.exe',
  [string]$PowerShell = 'powershell.exe',
  [string]$ExportScript = '',
  [string]$ReportScript = '',
  [string]$Week = '',
  [string]$Today = ''
)

$WeeklyVersion = 'onec_weekly v1'
. (Join-Path $PSScriptRoot 'onec_com.ps1')
if (-not $ExportScript) { $ExportScript = Join-Path $PSScriptRoot 'onec_export.ps1' }
if (-not $ReportScript) { $ReportScript = Join-Path $PSScriptRoot 'gps_onec_hauling_report.py' }
$LogName = 'perevozki_1c.log'
$Files = @('onec_vyvoz_po_brutto_nedelya.csv', 'onec_pk17_reestr_nedelya.csv')

function Write-Log([string]$text) {
  $line = '{0} {1}' -f (Get-Date).ToString('yyyy-MM-dd HH:mm:ss'), (ConvertTo-Ascii $text)
  Write-Host $line
  if ($script:LogPath) {
    [System.IO.File]::AppendAllText($script:LogPath, $line + "`r`n", (New-Object System.Text.UTF8Encoding($false)))
  }
}

# [REASON]: each step is a process of its own and its code is the answer:
# the export ends its 1C session by exiting, and a step that could not be
# started at all (no interpreter) must not look like a step that ran. A
# native command's error lines come back as error records; they are logged
# as text and never stop this script (no 'Stop' preference here).
function Invoke-Step([string]$name, [string]$exe, [string[]]$arguments) {
  Write-Log ($name + ': start')
  try {
    $output = & $exe @arguments 2>&1
    $code = $LASTEXITCODE
  } catch {
    Write-Log ('{0}: could not be started: {1}' -f $name, $_.Exception.Message)
    return 9
  }
  foreach ($line in @($output)) {
    if ($null -ne $line) { Write-Log ('{0} | {1}' -f $name, [string]$line) }
  }
  if ($null -eq $code) { $code = 9 }
  Write-Log ('{0}: exit code {1}' -f $name, $code)
  return [int]$code
}

function Invoke-Weekly {
  try {
    # The folder and its log come first: a scheduled run that stops on bad
    # input still says why in the log.
    [void](New-Item -ItemType Directory -Force -Path $Out)
    $script:LogPath = Join-Path $Out $LogName
    $start = Get-WeekStart $Week $Today
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf) -and -not (Get-Command $Python -ErrorAction SilentlyContinue)) {
      throw ('no Python: ' + $Python)
    }
    if (-not (Test-Path -LiteralPath $Db -PathType Leaf)) { throw ('no database: ' + $Db) }
    $monday = $start.ToString('yyyy-MM-dd')
    $folder = Join-Path (Join-Path $Out 'vygruzka') $monday
    [void](New-Item -ItemType Directory -Force -Path $folder)
  } catch {
    Write-Log ('ERROR: ' + $_.Exception.Message)
    return 2
  }
  Write-Log ('{0} | week {1} .. {2} | {3}' -f $WeeklyVersion, $monday, $start.AddDays(6).ToString('yyyy-MM-dd'), $folder)
  $code = Invoke-Step 'export' $PowerShell @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $ExportScript,
    '-Server', $Server, '-Base', $Base, '-CredentialsFile', $CredentialsFile, '-OutDir', $folder, '-Week', $monday)
  if ($code -ne 0) {
    Write-Log ('FAILED: the export, no book this time (exit {0})' -f (10 + $code))
    return 10 + $code
  }
  $code = Invoke-Step 'book' $Python @($ReportScript, '--db', $Db,
    '--vyvoz', (Join-Path $folder $Files[0]), '--reestr', (Join-Path $folder $Files[1]), '--out-dir', $Out)
  if ($code -ne 0) {
    Write-Log ('FAILED: the book (exit {0})' -f (20 + $code))
    return 20 + $code
  }
  Write-Log ('DONE: the book of the week {0} is in {1}' -f $monday, $Out)
  return 0
}

$script:LogPath = ''
exit (Invoke-Weekly)
