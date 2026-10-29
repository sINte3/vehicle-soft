# tools/onec_com.ps1 -- what tools/onec_probe.ps1 and tools/onec_export.ps1
# share: the credentials file, the 1C connection string, masking the
# password, and reading 1C objects over late-bound COM (V83.COMConnector).
# Dot-sourced by both; defines functions and two tables, runs nothing.
#
# ASCII only (Windows PowerShell 5.1 reads a file without a BOM as ANSI):
# the Russian member names are \u escapes.

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
$Alternates['NewObject'] = [regex]::Unescape('\u041d\u043e\u0432\u044b\u0439\u041e\u0431\u044a\u0435\u043a\u0442')
$Alternates['Text'] = [regex]::Unescape('\u0422\u0435\u043a\u0441\u0442')
$Alternates['SetParameter'] = [regex]::Unescape('\u0423\u0441\u0442\u0430\u043d\u043e\u0432\u0438\u0442\u044c\u041f\u0430\u0440\u0430\u043c\u0435\u0442\u0440')
$Alternates['Execute'] = [regex]::Unescape('\u0412\u044b\u043f\u043e\u043b\u043d\u0438\u0442\u044c')
$Alternates['Select'] = [regex]::Unescape('\u0412\u044b\u0431\u0440\u0430\u0442\u044c')
$Alternates['Next'] = [regex]::Unescape('\u0421\u043b\u0435\u0434\u0443\u044e\u0449\u0438\u0439')

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
# $mode: 'get' a property, 'call' a method, 'set' a property to $arguments[0].
function Invoke-Raw($obj, [string]$name, [string]$mode, [object[]]$arguments) {
  if (Test-Com $obj) {
    # An argument that came out of a function is wrapped in a PSObject; 1C
    # must get its own object back (String() of a type description), not a
    # .NET wrapper, so each argument goes over as its base object.
    $plain = New-Object 'object[]' $arguments.Count
    for ($i = 0; $i -lt $arguments.Count; $i++) {
      if ($null -ne $arguments[$i]) { $plain[$i] = $arguments[$i].PSObject.BaseObject }
    }
    $flags = switch ($mode) {
      'call' { [System.Reflection.BindingFlags]::InvokeMethod }
      'set' { [System.Reflection.BindingFlags]::SetProperty }
      default { [System.Reflection.BindingFlags]::GetProperty }
    }
    # A property is read with no argument list at all -- the form that
    # worked on the server on 29.10.
    if ($mode -eq 'get') { $plain = $null }
    $value = $obj.GetType().InvokeMember($name, $flags, $null, $obj, $plain)
  } else {
    # A stand-in object answers like COM: a member it does not have is an
    # unknown name, not a silent $null.
    $member = $obj.PSObject.Members[$name]
    if ($null -eq $member) { throw (New-Object System.MissingMemberException($name)) }
    switch ($mode) {
      'call' { $value = $member.Invoke($arguments) }
      'set' { $member.Value = $arguments[0]; $value = $null }
      default { $value = $member.Value }
    }
  }
  return ,$value
}

function Invoke-Named($obj, [string]$name, [string]$mode, [object[]]$arguments) {
  $names = @($name)
  if ($Alternates.ContainsKey($name)) { $names += $Alternates[$name] }
  foreach ($candidate in $names) {
    try {
      return Invoke-Raw $obj $candidate $mode $arguments
    } catch {
      if (-not (Test-UnknownName $_)) { throw }
    }
  }
  throw (New-Object System.MissingMemberException(('1C does not know the member ' + ($names -join ' / '))))
}

function Get-Prop($obj, [string]$name) {
  if ($null -eq $obj) { return $null }
  return Invoke-Named $obj $name 'get' @()
}

function Set-Prop($obj, [string]$name, $value) {
  [void](Invoke-Named $obj $name 'set' @($value))
}

function Invoke-Method($obj, [string]$name, [object[]]$arguments = @()) {
  return Invoke-Named $obj $name 'call' $arguments
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

function Write-Utf8([string]$path, $lines) {
  [System.IO.File]::WriteAllLines($path, [string[]]$lines, (New-Object System.Text.UTF8Encoding($true)))
}

