# tools/onec_weekly_task.ps1 -- GPS-38: puts tools/onec_weekly.ps1 into the
# Windows Task Scheduler (or takes it out with -Remove).
#
# THE TASK
#   every Monday at 06:40 (-Day, -At), as the account that runs this script,
#   whether or not it is logged on, without storing its Windows password
#   (logon type S4U), with its highest rights; a run missed while the server
#   was off starts when it is back; one run at a time; at most two hours.
# 1C is entered with the login and password from the credentials file, not
# with Windows credentials, so S4U is enough: the task touches no Windows
# network share.
#
# RUN (an administrator's Windows PowerShell 5.1 on the server)
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File C:\gps-weekly\tools\onec_weekly_task.ps1
#
# -Remove takes the task out. Registering again replaces the task.
#
# EXIT CODES: 0 done; 1 the Task Scheduler refused (the message says why).
#
# ASCII only (Windows PowerShell 5.1 reads a file without a BOM as ANSI).
param(
  [string]$TaskName = 'VehicleSoft perevozki 1C',
  [string]$Day = 'Monday',
  [string]$At = '06:40',
  [string]$Script = '',
  [switch]$Remove
)

. (Join-Path $PSScriptRoot 'onec_com.ps1')
if (-not $Script) { $Script = Join-Path $PSScriptRoot 'onec_weekly.ps1' }

try {
  if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
    Write-Host (ConvertTo-Ascii ('removed: ' + $TaskName))
    exit 0
  }
  $action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument ('-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $Script)
  $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Day -At $At
  $user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
  $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Highest
  $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2)
  [void](Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force -ErrorAction Stop)
  $info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction Stop
  Write-Host (ConvertTo-Ascii ('registered: {0} | {1} {2} | runs as {3} | next run {4}' -f $TaskName, $Day,
    $At, $user, $info.NextRunTime.ToString('yyyy-MM-dd HH:mm')))
  Write-Host (ConvertTo-Ascii ('script: ' + $Script))
  exit 0
} catch {
  Write-Host ('ERROR: the Task Scheduler refused: ' + (ConvertTo-Ascii $_.Exception.Message))
  exit 1
}
