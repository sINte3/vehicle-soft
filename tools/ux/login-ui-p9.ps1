# Выпустить сессию для агента проверки, не передавая ему пароль.
#
# [REASON]: UI-P9. Пароль вводит владелец, скрыто; в командную строку и в
# историю PowerShell он не попадает, агенту достаётся только файл сессии.
#
# Положить рядом с login_staging.mjs в C:\qa и запустить:
#   cd C:\qa
#   .\login-ui-p9.ps1
#
# Вывод только ASCII.

$ErrorActionPreference = 'Stop'

$base = 'http://10.103.25.14:5051'
$user = 'admin'
$out  = 'C:\qa\auth-ui-p9.json'

Write-Host "Staging : $base"
Write-Host "User    : $user"
Write-Host "Output  : $out"
Write-Host ""

$secure = Read-Host -AsSecureString "Password for $user (input is hidden)"
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $env:VS_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}

try {
    node C:\qa\login_staging.mjs --base $base --user $user --out $out
    $code = $LASTEXITCODE
}
finally {
    Remove-Item Env:\VS_PASSWORD -ErrorAction SilentlyContinue
}

if ($code -ne 0) {
    Write-Host ""
    Write-Host "FAILED (exit $code). Session file NOT created."
    exit $code
}
