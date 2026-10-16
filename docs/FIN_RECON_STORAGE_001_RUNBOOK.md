# FIN-RECON-STORAGE-001 — ранбук владельца: архив марта, восстановление, клон staging

Для владельца. Всё выполняется на сервере **SRV-YOQSH** в Windows PowerShell
от имени администратора. Каждый блок — «скопировать целиком, вставить,
нажать Enter, посмотреть последнюю строку». Прислать надо **весь вывод**
каждого блока.

Технический документ задачи — `docs/FIN_RECON_STORAGE_001.md`. Числа марта
в этом файле не пишутся (репозиторий публичный): контрольные ожидания лежат
в приватном пакете, блоки сверяют с ними сами и печатают результат.

## Что произойдёт — коротко

1. **Production (`C:\transport-report`, порт 5050) не меняется ни одним
   блоком.** Блоки только читают о нём: HEAD, службы, размер файла базы.
   Живая база production не открывается вовсе.
2. **Базовый staging (`C:\transport-report-staging`, порт 5051) не
   меняется ни одним блоком.** Ни код, ни база, ни службы. Его база только
   копируется штатным инструментом онлайн-копии `backup_transport_db.py`.
3. **Приватные данные лежат только в двух папках, закрытых правами «только
   администраторы и SYSTEM»:** `C:\VehicleSoft_FinRecon` (inbox, копия
   базы staging, клон, журналы, базы проверки восстановления) и
   `D:\fin-recon-evidence` (архив). Каждый блок, который что-то пишет,
   **сначала** ставит и проверяет эти права и останавливается до первой
   записи, если поставить или проверить их не удалось.
4. Мартовский приватный пакет кладётся в `D:\fin-recon-evidence\2026-03\v1`
   и проверяется по SHA-256 каждого файла.
5. Восстановление из пакета проверяется в **новой пустой** базе — без
   staging и production.
6. Экран «Дроны → Финансовая сверка» показывается на **изолированном клоне
   staging**: отдельная папка `C:\VehicleSoft_FinRecon\app` с кодом ветки и
   копией базы staging, отдельный процесс на `127.0.0.1:5052`. **Клон
   доступен только с самого сервера**: порт в сеть не открывается, правило
   брандмауэра не создаётся. Экран смотрят в браузере на сервере, в окне
   InPrivate. Клон останавливается и удаляется отдельными блоками; базовый
   staging при этом не затрагивается.

Почему клон, а не сам staging. По `docs/STAGING.md` площадка 5051 с
02.10.2026 занята инкрементом DRONE-CARD-COVERAGE-001 (исторический пилот:
своя ревизия кода, база — онлайн-копия production). Выложить эту ветку на
сам staging значило бы сменить код пилота посреди его проверки и заодно
поднять staging до кода `main` (выпуски v1.21–v1.27 и миграция
`AGRO_WORK_001`). Задание это запрещает: «без самовольного обновления
staging до production», а при разрушительном обновлении — «изолированный
staging clone и точный план». Клон даёт ту же проверку экрана и не трогает
ни пилот, ни его базу.

**Утвердить или закрыть март этими блоками нельзя** — такой функции нет.
Снимок загружается со статусом «Сверен с исключениями», «Финансово НЕ
утверждён».

---

## Как выполнять — прочитайте один раз

**Открыть PowerShell от имени администратора:** «Пуск» → набрать
`PowerShell` → правая кнопка на «Windows PowerShell» → «Запуск от имени
администратора» → «Да».

**Вставить блок:** выделить блок целиком (от `& {` до последней `}`),
скопировать, щёлкнуть правой кнопкой в окне PowerShell, нажать Enter.

**Как понять результат:** последняя строка `STEP=PASS` — блок прошёл.
`STEP FAILED: ...` — блок остановился сам на первой проблеме и сказал
почему; дальше этой проверки он ничего не делал. Пришлите весь вывод.

**Порядок:** 1 → 2 → 3 → 4 → (проверка экрана на сервере) → 5. Блоки 6, 7,
8 — когда понадобятся (7 — пока папка клона не удалена блоком 6: он берёт
из неё инструмент проверки; после 6 сначала снова блок 2). Повторный запуск
любого блока безопасен: блок либо видит, что всё уже сделано, либо
останавливается.

**Приватный ZIP.** Сохраните `FIN_RECON_EVIDENCE_2026-03_v1.zip` из
приватного комплекта в папку **«Загрузки» (Downloads) или на «Рабочий
стол»** своей учётной записи на сервере. Папки `C:\VehicleSoft_FinRecon`
вручную не создавайте: блок 2 сам создаст её закрытой, скопирует ZIP туда
(копия получает закрытые права) и напомнит удалить исходную копию. Файл
приватный — только по закрытому каналу, не в почту и не в GitHub.

**Не открывайте закрытые папки в Проводнике.** Проводник работает без прав
администратора и на закрытой папке предлагает «Продолжить»; эта кнопка
добавляет вашу учётную запись в права папки — следующий блок увидит лишнее
право и остановится (так и задумано). Всё нужное блоки печатают сами.

---

## Блок 1. Проверка перед началом — ничего не меняет

Печатает, что стоит на production и staging, какие службы запущены, где
лежат базы, сколько свободно на дисках, есть ли уже приватные папки и
закрыты ли они правами, свободен ли порт 5052. Ничего не создаёт и не
меняет. Базу production не открывает; базу staging читает только её
собственным инструментом `tools\state_snapshot.py` в режиме только чтения.

```powershell
& {
  $ErrorActionPreference = 'Stop'
  $expectedHost = 'srv-yoqsh'
  $prodRoot = 'C:\transport-report'
  $stagingRoot = 'C:\transport-report-staging'
  $finRoot = 'C:\VehicleSoft_FinRecon'
  $evidenceRoot = 'D:\fin-recon-evidence'
  $python = 'C:\Program Files\Python314\python.exe'
  $stagingUrl = 'http://10.103.25.14:5051/login'
  $port = 5052
  function Invoke-Native([string]$exe, [string[]]$arguments) {
    $ErrorActionPreference = 'Continue'
    $global:LASTEXITCODE = -1
    $lines = @(& $exe @arguments 2>&1 | ForEach-Object { "$_" })
    [pscustomobject]@{ Code = $LASTEXITCODE; Lines = $lines; Text = ($lines -join "`n").Trim() }
  }
  function Test-PrivateAcl([string]$path, [bool]$isRoot) {
    try {
      $acl = Get-Acl -LiteralPath $path
      $rules = @($acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))
    } catch {
      return ('the rights of ' + $path + ' cannot be read: ' + $_.Exception.Message)
    }
    $foreign = @($rules | Where-Object { ([string]$_.AccessControlType -eq 'Allow') -and (@('S-1-5-32-544', 'S-1-5-18') -notcontains $_.IdentityReference.Value) })
    if ($foreign.Count -gt 0) {
      $problem = $path + ' also grants access to ' + (@($foreign | ForEach-Object { $_.IdentityReference.Value } | Select-Object -Unique) -join ', ')
      $explicit = @($foreign | Where-Object { -not $_.IsInherited } | ForEach-Object { '*' + $_.IdentityReference.Value } | Select-Object -Unique)
      if ($explicit.Count -gt 0) { $problem = $problem + ' -- to take that access away run: icacls "' + $path + '" /remove:g ' + ($explicit -join ' ') + ' /T /C' }
      return $problem
    }
    if ($isRoot) {
      if (-not $acl.AreAccessRulesProtected) { return ($path + ' still inherits rights from its parent folder') }
      foreach ($sid in @('S-1-5-32-544', 'S-1-5-18')) {
        $full = @($rules | Where-Object { ($_.IdentityReference.Value -eq $sid) -and ([string]$_.AccessControlType -eq 'Allow') -and ([string]$_.FileSystemRights -eq 'FullControl') -and ([string]$_.InheritanceFlags -eq 'ContainerInherit, ObjectInherit') })
        if ($full.Count -eq 0) { return ($path + ' does not give ' + $sid + ' full control of everything inside') }
      }
    }
    return ''
  }

  if ((hostname) -ne $expectedHost) { throw "STEP FAILED: this computer is $(hostname), not $expectedHost" }
  Write-Output ('DATE=' + (Get-Date -Format 's'))
  Write-Output ('POWERSHELL=' + $PSVersionTable.PSVersion)
  if (-not (Test-Path -LiteralPath $python)) { throw "STEP FAILED: python not found at $python" }
  Write-Output ('PYTHON=' + (Invoke-Native $python @('--version')).Text)
  $gitVersion = Invoke-Native 'git' @('--version')
  if ($gitVersion.Code -ne 0) { throw 'STEP FAILED: git does not run in this console' }
  Write-Output ('GIT=' + $gitVersion.Text)

  foreach ($site in @(@{ Label = 'PROD'; Root = $prodRoot }, @{ Label = 'STAGING'; Root = $stagingRoot })) {
    $label = $site.Label
    $root = $site.Root
    if (-not (Test-Path -LiteralPath (Join-Path $root '.git'))) { throw "STEP FAILED: $label checkout not found at $root" }
    $head = Invoke-Native 'git' @('-C', $root, 'rev-parse', 'HEAD')
    $branchName = Invoke-Native 'git' @('-C', $root, 'symbolic-ref', '-q', '--short', 'HEAD')
    $changed = Invoke-Native 'git' @('--no-optional-locks', '-C', $root, 'status', '--porcelain', '--untracked-files=no')
    Write-Output ($label + '_ROOT=' + $root)
    Write-Output ($label + '_HEAD=' + $head.Text)
    if ($branchName.Code -eq 0) { Write-Output ($label + '_BRANCH=' + $branchName.Text) } else { Write-Output ($label + '_BRANCH=(detached)') }
    Write-Output ($label + '_TRACKED_CHANGES=' + @($changed.Lines | Where-Object { $_ }).Count)
    $db = Join-Path (Join-Path $root 'instance') 'transport.db'
    if (Test-Path -LiteralPath $db) {
      $item = Get-Item -LiteralPath $db
      Write-Output ($label + '_DB=' + $db + ' BYTES=' + $item.Length + ' MODIFIED=' + $item.LastWriteTime.ToString('s'))
    } else {
      Write-Output ($label + '_DB=MISSING ' + $db)
    }
  }

  foreach ($service in @(Get-Service -ErrorAction SilentlyContinue | Where-Object { ($_.Name -like 'Transport*') -or ($_.Name -like 'Dji*') -or ($_.Name -like 'Gps*') } | Sort-Object Name)) {
    Write-Output ('SERVICE ' + $service.Name + ' ' + $service.Status + ' ' + $service.StartType)
  }

  Write-Output '--- staging state: tools\state_snapshot.py of the staging checkout, database opened read-only ---'
  $snapshotTool = Join-Path (Join-Path $stagingRoot 'tools') 'state_snapshot.py'
  if (Test-Path -LiteralPath $snapshotTool) {
    Write-Output (Invoke-Native $python @($snapshotTool, '--worktree', $stagingRoot, '--label', 'staging')).Text
  } else {
    Write-Output 'STAGING_STATE=tools\state_snapshot.py is not in the staging checkout'
  }

  try {
    $login = Invoke-WebRequest -Uri $stagingUrl -UseBasicParsing -TimeoutSec 30
    Write-Output ('STAGING_LOGIN=' + $login.StatusCode)
  } catch {
    Write-Output ('STAGING_LOGIN=ERROR ' + $_.Exception.Message)
  }

  foreach ($root in @($finRoot, $evidenceRoot)) {
    if (Test-Path -LiteralPath $root) {
      $problem = Test-PrivateAcl $root $true
      if ($problem) { Write-Output ('PRIVATE_ACL ' + $root + ' : NOT restricted -- ' + $problem) } else { Write-Output ('PRIVATE_ACL ' + $root + ' : Administrators and SYSTEM only') }
      $names = @(Get-ChildItem -LiteralPath $root -Force | ForEach-Object { $_.Name })
      Write-Output ('PRIVATE_DIR ' + $root + ' : ' + ($names -join ', '))
    } else {
      Write-Output ('PRIVATE_ACL ' + $root + ' : absent -- block 2 creates it restricted')
    }
  }

  $drives = @(@($finRoot, $evidenceRoot) | ForEach-Object { [System.IO.Path]::GetPathRoot($_) } | Select-Object -Unique)
  foreach ($drive in $drives) {
    if (Test-Path -LiteralPath $drive) {
      $info = New-Object System.IO.DriveInfo $drive
      Write-Output ('FREE ' + $drive + ' ' + [math]::Round($info.AvailableFreeSpace / 1GB, 1) + ' GB')
    } else {
      Write-Output ('FREE ' + $drive + ' absent')
    }
  }

  $listening = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
  if ($listening.Count -gt 0) { Write-Output ('PORT_' + $port + '=BUSY process ' + $listening[0].OwningProcess) } else { Write-Output ('PORT_' + $port + '=free') }
  Write-Output 'PLAN=isolated staging clone on 127.0.0.1:5052 only; production and the base staging are not changed'
  Write-Output 'STEP=PASS'
}
```

Ключевые строки ответа: `PROD_HEAD`, `STAGING_HEAD`, `STAGING_BRANCH`,
`STAGING_TRACKED_CHANGES`, строки `SERVICE`, блок состояния staging (число
миграций и последние пять), `PRIVATE_ACL`, `FREE`, `PORT_5052`, `STEP=PASS`.

---

## Блок 2. Закрытые папки, код ветки, архив и восстановление в пустую базу

Что делает блок, по порядку:

* **сначала** создаёт `C:\VehicleSoft_FinRecon` и `D:\fin-recon-evidence`
  и ставит им права «только администраторы и SYSTEM» (`icacls`), затем
  проверяет права по SID — без наследования от диска, без чужих записей;
  проверяет и всё, что в этих папках уже лежит. Не удалось поставить или
  проверить — остановка, ничего приватного не записано;
* ищет приватный ZIP: в `C:\VehicleSoft_FinRecon\inbox`, иначе в
  «Загрузках» и на «Рабочем столе» вашей учётной записи — и копирует его в
  закрытую inbox. Нет нигде — остановка с просьбой сохранить его туда;
* сразу сверяет SHA-256 ZIP с ожидаемым. Не совпало — откладывает файл в
  сторону (`...zip.rejected_<время>` в той же закрытой inbox) и
  останавливается: сохраните правильный файл и запустите блок снова;
* кладёт код ветки в **отдельную** папку `C:\VehicleSoft_FinRecon\app` ровно
  на проверенный коммит (из GitHub; staging и production не трогает) и
  прогоняет тесты задачи этим питоном на этом сервере;
* распаковывает ZIP во временную папку
  внутри закрытого `D:\fin-recon-evidence`, проверяет пакет целиком
  (каждый файл по SHA-256, манифест, контракт снимка, контрольные ожидания
  марта) и только потом переименовывает папку в
  `D:\fin-recon-evidence\2026-03\v1`; ставит файлам пакета «только чтение»;
* восстанавливает снимок в **новую пустую** базу
  (`C:\VehicleSoft_FinRecon\restore\<время запуска>`): миграция, загрузка,
  сверка с пакетом и с контрольными числами, повторная загрузка — ни одной
  записи, файл базы не меняется ни на байт;
* в конце ещё раз проверяет права на всём, что записал.

Если пакет уже лежит на месте, блок проверяет его заново и повторяет
восстановление в новую пустую базу.

```powershell
& {
  $ErrorActionPreference = 'Stop'
  $expectedHost = 'srv-yoqsh'
  $finRoot = 'C:\VehicleSoft_FinRecon'
  $evidenceRoot = 'D:\fin-recon-evidence'
  $python = 'C:\Program Files\Python314\python.exe'
  $repoUrl = 'https://github.com/sINte3/vehicle-soft.git'
  $branch = 'claude/fin-recon-prompt-ry4xsx'
  $sha = 'a267782679f83deb68d17a8871d66c6ea91707a4'
  $packageZip = 'FIN_RECON_EVIDENCE_2026-03_v1.zip'
  $packageZipSha = '8d793e777b08cf4493f958ceed6ccd585752a1c6aa49c767e8024c93d66a9100'
  $packageId = 'fin-recon-2026-03-v1'
  $contentSha = '42d09d775958e110de396a5cb3b3737292aaf5a647806b5b91574276a4973b6e'
  $period = '2026-03'
  $version = 1
  $expectName = 'task_contract_2026-03_v1.json'
  function Invoke-Native([string]$exe, [string[]]$arguments) {
    $ErrorActionPreference = 'Continue'
    $global:LASTEXITCODE = -1
    $lines = @(& $exe @arguments 2>&1 | ForEach-Object { "$_" })
    [pscustomobject]@{ Code = $LASTEXITCODE; Lines = $lines; Text = ($lines -join "`n").Trim() }
  }
  function Test-PrivateAcl([string]$path, [bool]$isRoot) {
    try {
      $acl = Get-Acl -LiteralPath $path
      $rules = @($acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))
    } catch {
      return ('the rights of ' + $path + ' cannot be read: ' + $_.Exception.Message)
    }
    $foreign = @($rules | Where-Object { ([string]$_.AccessControlType -eq 'Allow') -and (@('S-1-5-32-544', 'S-1-5-18') -notcontains $_.IdentityReference.Value) })
    if ($foreign.Count -gt 0) {
      $problem = $path + ' also grants access to ' + (@($foreign | ForEach-Object { $_.IdentityReference.Value } | Select-Object -Unique) -join ', ')
      $explicit = @($foreign | Where-Object { -not $_.IsInherited } | ForEach-Object { '*' + $_.IdentityReference.Value } | Select-Object -Unique)
      if ($explicit.Count -gt 0) { $problem = $problem + ' -- to take that access away run: icacls "' + $path + '" /remove:g ' + ($explicit -join ' ') + ' /T /C' }
      return $problem
    }
    if ($isRoot) {
      if (-not $acl.AreAccessRulesProtected) { return ($path + ' still inherits rights from its parent folder') }
      foreach ($sid in @('S-1-5-32-544', 'S-1-5-18')) {
        $full = @($rules | Where-Object { ($_.IdentityReference.Value -eq $sid) -and ([string]$_.AccessControlType -eq 'Allow') -and ([string]$_.FileSystemRights -eq 'FullControl') -and ([string]$_.InheritanceFlags -eq 'ContainerInherit, ObjectInherit') })
        if ($full.Count -eq 0) { return ($path + ' does not give ' + $sid + ' full control of everything inside') }
      }
    }
    return ''
  }
  function Lock-PrivateRoot([string]$root) {
    New-Item -ItemType Directory -Force -Path $root | Out-Null
    $set = Invoke-Native 'icacls' @($root, '/inheritance:r', '/grant:r', '*S-1-5-32-544:(OI)(CI)F', '*S-1-5-18:(OI)(CI)F')
    if ($set.Code -ne 0) { throw ('STEP FAILED: the rights of ' + $root + ' could not be restricted -- ' + $set.Text + ' -- this block wrote no private data') }
    $problem = Test-PrivateAcl $root $true
    if ($problem) { throw ('STEP FAILED: ' + $problem + ' -- this block wrote no private data') }
    Write-Output ('PRIVATE_ACL ' + $root + ' = Administrators and SYSTEM only, verified')
  }
  function Assert-PrivateTree([string[]]$folders) {
    $count = 0
    foreach ($folder in $folders) {
      if (-not (Test-Path -LiteralPath $folder)) { continue }
      foreach ($item in @(@(Get-Item -LiteralPath $folder -Force) + @(Get-ChildItem -LiteralPath $folder -Recurse -Force))) {
        $problem = Test-PrivateAcl $item.FullName $false
        if ($problem) { throw ('STEP FAILED: ' + $problem) }
        $count = $count + 1
      }
    }
    Write-Output ('PRIVATE_ACL_ITEMS_VERIFIED=' + $count)
  }

  if ((hostname) -ne $expectedHost) { throw "STEP FAILED: this computer is $(hostname), not $expectedHost" }
  $appDir = Join-Path $finRoot 'app'
  $inbox = Join-Path $finRoot 'inbox'
  Lock-PrivateRoot $finRoot
  Lock-PrivateRoot $evidenceRoot
  New-Item -ItemType Directory -Force -Path $inbox | Out-Null
  Assert-PrivateTree @($finRoot, $evidenceRoot)

  $cloneDb = Join-Path (Join-Path $appDir 'instance') 'transport.db'
  $tool = Join-Path (Join-Path $appDir 'tools') 'fin_recon_storage.py'
  $periodDir = Join-Path $evidenceRoot $period
  $target = Join-Path $periodDir ('v' + $version)
  $zipPath = Join-Path $inbox $packageZip
  $runName = Get-Date -Format 'yyyyMMdd_HHmmss_fff'
  if (-not (Test-Path -LiteralPath $target)) {
    if (-not (Test-Path -LiteralPath $zipPath)) {
      foreach ($place in @('Downloads', 'Desktop')) {
        $candidate = Join-Path (Join-Path $env:USERPROFILE $place) $packageZip
        if ((-not (Test-Path -LiteralPath $zipPath)) -and (Test-Path -LiteralPath $candidate)) {
          Copy-Item -LiteralPath $candidate -Destination $zipPath
          Write-Output ('PACKAGE_ZIP_COPIED_TO_INBOX from ' + $candidate + ' -- delete that copy once this block has passed')
        }
      }
      if (-not (Test-Path -LiteralPath $zipPath)) { throw ('STEP FAILED: ' + $packageZip + ' not found -- save it into Downloads or Desktop of this Windows account, then run block 2 again') }
    }
    $zipHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $zipPath).Hash.ToLower()
    Write-Output ('PACKAGE_ZIP_SHA256=' + $zipHash)
    if ($zipHash -ne $packageZipSha) {
      $aside = $zipPath + '.rejected_' + $runName
      Move-Item -LiteralPath $zipPath -Destination $aside
      throw ('STEP FAILED: ' + $packageZip + ' has SHA-256 ' + $zipHash + ', expected ' + $packageZipSha + ' -- it was set aside as ' + $aside + '; save the right file into Downloads or Desktop, then run block 2 again')
    }
  }

  $runDir = Join-Path (Join-Path $finRoot 'runs') $runName
  New-Item -ItemType Directory -Force -Path $runDir | Out-Null
  Write-Output ('RUN=' + $runDir)

  if (-not (Test-Path -LiteralPath (Join-Path $appDir '.git'))) {
    if (Test-Path -LiteralPath $appDir) { throw "STEP FAILED: $appDir exists but is not a git checkout -- run block 6 to delete it" }
    $r = Invoke-Native 'git' @('clone', '--quiet', '--no-checkout', '--single-branch', '--branch', $branch, $repoUrl, $appDir)
    if ($r.Code -ne 0) { throw "STEP FAILED: git clone -- $($r.Text)" }
  }
  $r = Invoke-Native 'git' @('-C', $appDir, 'fetch', '--quiet', 'origin', $branch)
  if ($r.Code -ne 0) { throw "STEP FAILED: git fetch origin $branch -- $($r.Text)" }
  $r = Invoke-Native 'git' @('-C', $appDir, 'cat-file', '-e', ($sha + '^{commit}'))
  if ($r.Code -ne 0) { throw "STEP FAILED: commit $sha is not in the fetched history" }
  $r = Invoke-Native 'git' @('-C', $appDir, 'merge-base', '--is-ancestor', $sha, 'FETCH_HEAD')
  if ($r.Code -ne 0) { throw "STEP FAILED: commit $sha is not on $branch" }
  $later = Invoke-Native 'git' @('-C', $appDir, 'diff', '--name-only', $sha, 'FETCH_HEAD')
  if ($later.Code -ne 0) { throw "STEP FAILED: git diff -- $($later.Text)" }
  $appFiles = @($later.Lines | Where-Object { $_ -and ($_ -notmatch '^(docs|tests|\.github)/') })
  if ($appFiles.Count -gt 0) { throw "STEP FAILED: after $sha the branch changes application files: $($appFiles -join ', ')" }
  $head = Invoke-Native 'git' @('-C', $appDir, 'rev-parse', 'HEAD')
  $changed = Invoke-Native 'git' @('--no-optional-locks', '-C', $appDir, 'status', '--porcelain', '--untracked-files=no')
  if (($head.Text -ne $sha) -or (@($changed.Lines | Where-Object { $_ }).Count -gt 0)) {
    if (Test-Path -LiteralPath $cloneDb) { throw 'STEP FAILED: the clone exists on another commit -- run blocks 5 and 6 first' }
    $r = Invoke-Native 'git' @('-C', $appDir, 'checkout', '--quiet', '--force', '--detach', $sha)
    if ($r.Code -ne 0) { throw "STEP FAILED: git checkout $sha -- $($r.Text)" }
  }
  if ((Invoke-Native 'git' @('-C', $appDir, 'rev-parse', 'HEAD')).Text -ne $sha) { throw 'STEP FAILED: the app folder is not on the reviewed commit' }
  Write-Output ('APP_HEAD=' + $sha)

  Push-Location $appDir
  try {
    $tests = Invoke-Native $python @('-m', 'unittest', 'tests.test_fin_recon_contract', 'tests.test_fin_recon_package', 'tests.test_fin_recon_import', 'tests.test_fin_recon_cli', 'tests.test_fin_recon_migration')
  } finally {
    Pop-Location
  }
  Set-Content -LiteralPath (Join-Path $runDir 'tests.txt') -Value $tests.Text -Encoding UTF8
  $ran = [regex]::Match($tests.Text, 'Ran \d+ tests')
  Write-Output ('TESTS=' + $ran.Value + ' exit=' + $tests.Code)
  if (($tests.Code -ne 0) -or (-not $ran.Success)) { throw ('STEP FAILED: the tests did not pass -- see ' + (Join-Path $runDir 'tests.txt')) }

  if (-not (Test-Path -LiteralPath $target)) {
    $free = (New-Object System.IO.DriveInfo ([System.IO.Path]::GetPathRoot($evidenceRoot))).AvailableFreeSpace
    if ($free -lt (20 * (Get-Item -LiteralPath $zipPath).Length)) { throw "STEP FAILED: not enough free space for $evidenceRoot" }
    New-Item -ItemType Directory -Force -Path $periodDir | Out-Null
    $incoming = Join-Path $periodDir ('incoming_' + $runName)
    Expand-Archive -LiteralPath $zipPath -DestinationPath $incoming
    $check = Invoke-Native $python @('-I', $tool, 'check-package', '--package', $incoming, '--expect', (Join-Path (Join-Path $incoming 'expectations') $expectName))
    Set-Content -LiteralPath (Join-Path $runDir 'check_incoming.txt') -Value $check.Text -Encoding UTF8
    if (($check.Code -ne 0) -or (-not $check.Text.Contains('RESULT=PASS package=' + $packageId + ' content_sha256=' + $contentSha))) {
      Write-Output $check.Text
      throw "STEP FAILED: the unpacked package did not verify -- it stays in $incoming for inspection"
    }
    Move-Item -LiteralPath $incoming -Destination $target
    Write-Output ('PACKAGE_PLACED=' + $target)
  } else {
    Write-Output ('PACKAGE_ALREADY_PLACED=' + $target)
  }

  $expect = Join-Path (Join-Path $target 'expectations') $expectName
  $check = Invoke-Native $python @('-I', $tool, 'check-package', '--package', $target, '--expect', $expect)
  Set-Content -LiteralPath (Join-Path $runDir 'check_package.txt') -Value $check.Text -Encoding UTF8
  Write-Output $check.Text
  if (($check.Code -ne 0) -or (-not $check.Text.Contains('RESULT=PASS package=' + $packageId + ' content_sha256=' + $contentSha))) { throw "STEP FAILED: the package in $target does not verify or is not the reviewed one" }
  Get-ChildItem -LiteralPath $target -Recurse -File | ForEach-Object { $_.IsReadOnly = $true }
  Write-Output 'ARCHIVE_FILES=read-only'

  $restoreDir = Join-Path (Join-Path $finRoot 'restore') $runName
  $restore = Invoke-Native $python @('-I', $tool, 'restore-test', '--package', $target, '--workdir', $restoreDir, '--expect', $expect)
  Set-Content -LiteralPath (Join-Path $runDir 'restore_test.txt') -Value $restore.Text -Encoding UTF8
  Write-Output $restore.Text
  if (($restore.Code -ne 0) -or (-not $restore.Text.Contains('RESULT=PASS restore-test period=' + $period + ' version=' + $version + ' content_sha256=' + $contentSha))) { throw 'STEP FAILED: restore into a new empty database' }
  Assert-PrivateTree @($finRoot, $evidenceRoot)
  Write-Output 'STEP=PASS'
}
```

Первый запуск без ZIP в «Загрузках» или на «Рабочем столе» закончится
`STEP FAILED: FIN_RECON_EVIDENCE_2026-03_v1.zip not found` — это ожидаемо:
закрытые папки уже созданы, сохраните ZIP и запустите блок 2 снова.

Ключевые строки: две `PRIVATE_ACL ... verified`, `PRIVATE_ACL_ITEMS_VERIFIED`
(в начале и в конце), `PACKAGE_ZIP_COPIED_TO_INBOX` (если ZIP взят из
«Загрузок» или с «Рабочего стола» — после `STEP=PASS` **удалите** оттуда
исходную копию: Shift+Delete, мимо корзины), `PACKAGE_ZIP_SHA256`,
`APP_HEAD`, `TESTS=Ran N tests exit=0`, `PACKAGE_PLACED` (или
`PACKAGE_ALREADY_PLACED`), строки `CONTROL` и `EXPECT OK` (контрольные
числа марта), `EXPECTATIONS N checked, 0 failed`, `RESULT=PASS package=`,
`import #1=IMPORTED`, `import #2=ALREADY_IMPORTED rows_written=0`,
`unchanged=True`, `RESULT=PASS restore-test`, `STEP=PASS`.

---

## Блок 3. Клон staging: создать (база staging не меняется)

Что делает блок:

* сначала ставит и проверяет права обеих закрытых папок и всего, что в них
  лежит; не удалось — остановка до первой записи;
* записывает состояние production и базового staging (HEAD и службы) —
  в конце блока сверяет, что оно не изменилось; сохраняет отчёт
  `state_snapshot.py` staging и конфигурацию его службы (`sc.exe qc`) в
  журнал прогона;
* делает **онлайн-копию базы staging** штатным `backup_transport_db.py` с
  `integrity_check` в закрытую
  `C:\VehicleSoft_FinRecon\staging_backup\<время>`; копирует её в
  `C:\VehicleSoft_FinRecon\app\instance\transport.db` и сверяет SHA-256
  копии;
* на **копии**: печатает, каких миграций кода ветки в ней нет (только
  отчёт), применяет `FIN_RECON_STORAGE_001`, загружает мартовский снимок,
  сверяет копию с пакетом и с контрольными числами;
* в конце ещё раз проверяет права на всём, что записал.

Если клон уже создан, блок останавливается и отсылает к блоку 4
(запустить) или к блокам 5 и 6 (удалить и создать заново).

```powershell
& {
  $ErrorActionPreference = 'Stop'
  $expectedHost = 'srv-yoqsh'
  $prodRoot = 'C:\transport-report'
  $stagingRoot = 'C:\transport-report-staging'
  $finRoot = 'C:\VehicleSoft_FinRecon'
  $evidenceRoot = 'D:\fin-recon-evidence'
  $python = 'C:\Program Files\Python314\python.exe'
  $sha = 'a267782679f83deb68d17a8871d66c6ea91707a4'
  $contentSha = '42d09d775958e110de396a5cb3b3737292aaf5a647806b5b91574276a4973b6e'
  $period = '2026-03'
  $version = 1
  $expectName = 'task_contract_2026-03_v1.json'
  $stagingService = 'TransportReportStaging'
  function Invoke-Native([string]$exe, [string[]]$arguments) {
    $ErrorActionPreference = 'Continue'
    $global:LASTEXITCODE = -1
    $lines = @(& $exe @arguments 2>&1 | ForEach-Object { "$_" })
    [pscustomobject]@{ Code = $LASTEXITCODE; Lines = $lines; Text = ($lines -join "`n").Trim() }
  }
  function Test-PrivateAcl([string]$path, [bool]$isRoot) {
    try {
      $acl = Get-Acl -LiteralPath $path
      $rules = @($acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))
    } catch {
      return ('the rights of ' + $path + ' cannot be read: ' + $_.Exception.Message)
    }
    $foreign = @($rules | Where-Object { ([string]$_.AccessControlType -eq 'Allow') -and (@('S-1-5-32-544', 'S-1-5-18') -notcontains $_.IdentityReference.Value) })
    if ($foreign.Count -gt 0) {
      $problem = $path + ' also grants access to ' + (@($foreign | ForEach-Object { $_.IdentityReference.Value } | Select-Object -Unique) -join ', ')
      $explicit = @($foreign | Where-Object { -not $_.IsInherited } | ForEach-Object { '*' + $_.IdentityReference.Value } | Select-Object -Unique)
      if ($explicit.Count -gt 0) { $problem = $problem + ' -- to take that access away run: icacls "' + $path + '" /remove:g ' + ($explicit -join ' ') + ' /T /C' }
      return $problem
    }
    if ($isRoot) {
      if (-not $acl.AreAccessRulesProtected) { return ($path + ' still inherits rights from its parent folder') }
      foreach ($sid in @('S-1-5-32-544', 'S-1-5-18')) {
        $full = @($rules | Where-Object { ($_.IdentityReference.Value -eq $sid) -and ([string]$_.AccessControlType -eq 'Allow') -and ([string]$_.FileSystemRights -eq 'FullControl') -and ([string]$_.InheritanceFlags -eq 'ContainerInherit, ObjectInherit') })
        if ($full.Count -eq 0) { return ($path + ' does not give ' + $sid + ' full control of everything inside') }
      }
    }
    return ''
  }
  function Lock-PrivateRoot([string]$root) {
    New-Item -ItemType Directory -Force -Path $root | Out-Null
    $set = Invoke-Native 'icacls' @($root, '/inheritance:r', '/grant:r', '*S-1-5-32-544:(OI)(CI)F', '*S-1-5-18:(OI)(CI)F')
    if ($set.Code -ne 0) { throw ('STEP FAILED: the rights of ' + $root + ' could not be restricted -- ' + $set.Text + ' -- this block wrote no private data') }
    $problem = Test-PrivateAcl $root $true
    if ($problem) { throw ('STEP FAILED: ' + $problem + ' -- this block wrote no private data') }
    Write-Output ('PRIVATE_ACL ' + $root + ' = Administrators and SYSTEM only, verified')
  }
  function Assert-PrivateTree([string[]]$folders) {
    $count = 0
    foreach ($folder in $folders) {
      if (-not (Test-Path -LiteralPath $folder)) { continue }
      foreach ($item in @(@(Get-Item -LiteralPath $folder -Force) + @(Get-ChildItem -LiteralPath $folder -Recurse -Force))) {
        $problem = Test-PrivateAcl $item.FullName $false
        if ($problem) { throw ('STEP FAILED: ' + $problem) }
        $count = $count + 1
      }
    }
    Write-Output ('PRIVATE_ACL_ITEMS_VERIFIED=' + $count)
  }
  function Get-SiteState {
    $prodHead = (Invoke-Native 'git' @('-C', $prodRoot, 'rev-parse', 'HEAD')).Text
    $stagingHead = (Invoke-Native 'git' @('-C', $stagingRoot, 'rev-parse', 'HEAD')).Text
    $services = @(Get-Service -ErrorAction SilentlyContinue | Where-Object { $_.Name -like 'Transport*' } | Sort-Object Name | ForEach-Object { $_.Name + '=' + $_.Status }) -join ' '
    'prod=' + $prodHead + ' staging=' + $stagingHead + ' services=' + $services
  }

  if ((hostname) -ne $expectedHost) { throw "STEP FAILED: this computer is $(hostname), not $expectedHost" }
  $appDir = Join-Path $finRoot 'app'
  Lock-PrivateRoot $finRoot
  Lock-PrivateRoot $evidenceRoot
  Assert-PrivateTree @($finRoot, $evidenceRoot)

  $stateDir = Join-Path $finRoot 'state'
  $backupRoot = Join-Path $finRoot 'staging_backup'
  $cloneDb = Join-Path (Join-Path $appDir 'instance') 'transport.db'
  $stagingDb = Join-Path (Join-Path $stagingRoot 'instance') 'transport.db'
  $target = Join-Path (Join-Path $evidenceRoot $period) ('v' + $version)
  $expect = Join-Path (Join-Path $target 'expectations') $expectName
  $tool = Join-Path (Join-Path $appDir 'tools') 'fin_recon_storage.py'
  $runName = Get-Date -Format 'yyyyMMdd_HHmmss_fff'
  $runDir = Join-Path (Join-Path $finRoot 'runs') $runName

  if (Test-Path -LiteralPath $cloneDb) { throw 'STEP FAILED: the clone database already exists -- block 4 starts it; to create it anew run blocks 5 and 6 first' }
  if (-not (Test-Path -LiteralPath (Join-Path $appDir '.git'))) { throw 'STEP FAILED: the app folder is missing -- run block 2 first' }
  if ((Invoke-Native 'git' @('-C', $appDir, 'rev-parse', 'HEAD')).Text -ne $sha) { throw 'STEP FAILED: the app folder is not on the reviewed commit -- run block 2 first' }
  if (-not (Test-Path -LiteralPath $expect)) { throw 'STEP FAILED: the archived package is missing -- run block 2 first' }
  if (-not (Test-Path -LiteralPath $stagingDb)) { throw "STEP FAILED: staging database not found at $stagingDb" }
  New-Item -ItemType Directory -Force -Path $runDir | Out-Null
  New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
  Write-Output ('RUN=' + $runDir)

  $before = Get-SiteState
  $stagingBytes = (Get-Item -LiteralPath $stagingDb).Length
  [ordered]@{ state = $before; staging_db_bytes = $stagingBytes; recorded = (Get-Date -Format 's') } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $stateDir 'baseline.json') -Encoding UTF8
  Write-Output ('BASELINE ' + $before)
  Write-Output ('STAGING_DB_BYTES=' + $stagingBytes)
  $snapshotTool = Join-Path (Join-Path $stagingRoot 'tools') 'state_snapshot.py'
  if (Test-Path -LiteralPath $snapshotTool) {
    $stagingState = Invoke-Native $python @($snapshotTool, '--worktree', $stagingRoot, '--label', 'staging')
    Set-Content -LiteralPath (Join-Path $runDir 'staging_state_before.txt') -Value $stagingState.Text -Encoding UTF8
  }
  $serviceConfig = Invoke-Native 'sc.exe' @('qc', $stagingService)
  Set-Content -LiteralPath (Join-Path $runDir 'staging_service_qc.txt') -Value $serviceConfig.Text -Encoding UTF8

  $free = (New-Object System.IO.DriveInfo ([System.IO.Path]::GetPathRoot($finRoot))).AvailableFreeSpace
  if ($free -lt [long](2.6 * $stagingBytes)) { throw "STEP FAILED: not enough free space under $finRoot for the backup and the clone of the staging database" }

  $backupDir = Join-Path $backupRoot $runName
  $backup = Invoke-Native $python @('-I', (Join-Path $appDir 'backup_transport_db.py'), '--source', $stagingDb, '--dest-dir', $backupDir, '--suffix', 'staging_before_finrecon')
  Set-Content -LiteralPath (Join-Path $runDir 'backup.txt') -Value $backup.Text -Encoding UTF8
  if (($backup.Code -ne 0) -or ($backup.Text -notmatch 'Integrity check : ok')) { Write-Output $backup.Text; throw 'STEP FAILED: online backup of the staging database' }
  $copies = @(Get-ChildItem -LiteralPath $backupDir -Filter '*.db')
  if ($copies.Count -ne 1) { throw "STEP FAILED: expected one backup file in $backupDir, found $($copies.Count)" }
  $copySha = (Get-FileHash -Algorithm SHA256 -LiteralPath $copies[0].FullName).Hash.ToLower()
  Write-Output ('STAGING_BACKUP=' + $copies[0].FullName + ' BYTES=' + $copies[0].Length + ' SHA256=' + $copySha + ' integrity=ok')

  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $cloneDb) | Out-Null
  Copy-Item -LiteralPath $copies[0].FullName -Destination $cloneDb
  if ((Get-FileHash -Algorithm SHA256 -LiteralPath $cloneDb).Hash.ToLower() -ne $copySha) { throw 'STEP FAILED: the clone database is not an exact copy of the backup' }
  Write-Output ('CLONE_DB=' + $cloneDb)

  $drift = Invoke-Native $python @('-I', (Join-Path (Join-Path $appDir 'tools') 'check_migration_drift.py'), '--db', $cloneDb, '--root', $appDir)
  Set-Content -LiteralPath (Join-Path $runDir 'drift_before.txt') -Value $drift.Text -Encoding UTF8
  $pending = @()
  $inSection = $false
  foreach ($line in $drift.Lines) {
    if ($line -like 'file-but-not-registered:*') { $inSection = $true; continue }
    if ($inSection -and ($line -like '  - *')) { $pending += $line.Substring(4).Split(' ')[0]; continue }
    $inSection = $false
  }
  Write-Output ('CLONE_MIGRATIONS_NOT_IN_STAGING_COPY=' + ($pending -join ' '))

  $migration = Invoke-Native $python @('-I', (Join-Path $appDir 'migrate_fin_recon_storage_001.py'), '--db', $cloneDb)
  Write-Output ('MIGRATION=' + $migration.Text)
  if ($migration.Code -ne 0) { throw 'STEP FAILED: migration FIN_RECON_STORAGE_001 on the clone database' }

  $import = Invoke-Native $python @('-I', $tool, 'import', '--db', $cloneDb, '--package', $target, '--imported-by', ('runbook block 3, ' + $env:USERNAME))
  Set-Content -LiteralPath (Join-Path $runDir 'import.txt') -Value $import.Text -Encoding UTF8
  if (($import.Code -ne 0) -or (-not $import.Text.Contains('RESULT=PASS import=IMPORTED period=' + $period + ' version=' + $version + ' content_sha256=' + $contentSha))) { Write-Output $import.Text; throw 'STEP FAILED: import into the clone database' }
  Write-Output 'IMPORT=IMPORTED'

  $verify = Invoke-Native $python @('-I', $tool, 'verify', '--db', $cloneDb, '--package', $target, '--expect', $expect)
  Set-Content -LiteralPath (Join-Path $runDir 'verify.txt') -Value $verify.Text -Encoding UTF8
  Write-Output $verify.Text
  if (($verify.Code -ne 0) -or (-not $verify.Text.Contains('RESULT=PASS verify period=' + $period + ' version=' + $version + ' content_sha256=' + $contentSha))) { throw 'STEP FAILED: the clone database does not match the package' }

  Assert-PrivateTree @($finRoot, $evidenceRoot)
  $after = Get-SiteState
  if ($after -ne $before) { throw ('STEP FAILED: production or staging changed while this block ran (the block does not touch them) -- before: ' + $before + ' after: ' + $after) }
  Write-Output 'PROD_AND_STAGING_UNCHANGED=yes'
  Write-Output 'STEP=PASS'
}
```

Ключевые строки: две `PRIVATE_ACL ... verified`, `PRIVATE_ACL_ITEMS_VERIFIED`,
`BASELINE ...`, `STAGING_BACKUP=... integrity=ok`, `CLONE_DB`,
`CLONE_MIGRATIONS_NOT_IN_STAGING_COPY` (только отчёт: каких миграций кода
ветки в копии нет; среди них `FIN_RECON_STORAGE_001`, которую блок
применяет следом; остальные на экран финансовой сверки не влияют),
`MIGRATION=Done...`, `IMPORT=IMPORTED`, все `CHECK OK`, `EXPECTATIONS N
checked, 0 failed`, `RESULT=PASS verify`, `PROD_AND_STAGING_UNCHANGED=yes`,
`STEP=PASS`.

---

## Блок 4. Клон staging: запустить и проверить (только 127.0.0.1)

Что делает блок:

* сначала ставит и проверяет права обеих закрытых папок; не удалось —
  клон не запускается;
* ещё раз сверяет базу клона с пакетом (только чтение);
* запускает клон — `run_server.py` из папки клона, адрес
  **`127.0.0.1:5052`**, отдельное свёрнутое окно `python`. Порт в сеть не
  открывается, правила брандмауэра нет. У клона свой случайный ключ
  сессий; ключи интеграций (Topaz, сборщик дронов, бот) клону не
  передаются. Ботов, сборщиков и служб клон не запускает;
* проверяет, что клон слушает **только** `127.0.0.1` — иначе останавливает
  его и сообщает;
* проверяет: `/login` отвечает формой входа, `/drones/fin-recon` без входа
  ведёт на форму входа. Если проверка не прошла, только что запущенный
  клон блок сам останавливает;
* печатает, какой браузер для просмотра есть на сервере;
* сверяет, что production и базовый staging за время блока не изменились.

Если клон уже запущен, блок только проверяет.

```powershell
& {
  $ErrorActionPreference = 'Stop'
  $expectedHost = 'srv-yoqsh'
  $prodRoot = 'C:\transport-report'
  $stagingRoot = 'C:\transport-report-staging'
  $finRoot = 'C:\VehicleSoft_FinRecon'
  $evidenceRoot = 'D:\fin-recon-evidence'
  $python = 'C:\Program Files\Python314\python.exe'
  $sha = 'a267782679f83deb68d17a8871d66c6ea91707a4'
  $contentSha = '42d09d775958e110de396a5cb3b3737292aaf5a647806b5b91574276a4973b6e'
  $period = '2026-03'
  $version = 1
  $expectName = 'task_contract_2026-03_v1.json'
  $port = 5052
  function Invoke-Native([string]$exe, [string[]]$arguments) {
    $ErrorActionPreference = 'Continue'
    $global:LASTEXITCODE = -1
    $lines = @(& $exe @arguments 2>&1 | ForEach-Object { "$_" })
    [pscustomobject]@{ Code = $LASTEXITCODE; Lines = $lines; Text = ($lines -join "`n").Trim() }
  }
  function Test-PrivateAcl([string]$path, [bool]$isRoot) {
    try {
      $acl = Get-Acl -LiteralPath $path
      $rules = @($acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))
    } catch {
      return ('the rights of ' + $path + ' cannot be read: ' + $_.Exception.Message)
    }
    $foreign = @($rules | Where-Object { ([string]$_.AccessControlType -eq 'Allow') -and (@('S-1-5-32-544', 'S-1-5-18') -notcontains $_.IdentityReference.Value) })
    if ($foreign.Count -gt 0) {
      $problem = $path + ' also grants access to ' + (@($foreign | ForEach-Object { $_.IdentityReference.Value } | Select-Object -Unique) -join ', ')
      $explicit = @($foreign | Where-Object { -not $_.IsInherited } | ForEach-Object { '*' + $_.IdentityReference.Value } | Select-Object -Unique)
      if ($explicit.Count -gt 0) { $problem = $problem + ' -- to take that access away run: icacls "' + $path + '" /remove:g ' + ($explicit -join ' ') + ' /T /C' }
      return $problem
    }
    if ($isRoot) {
      if (-not $acl.AreAccessRulesProtected) { return ($path + ' still inherits rights from its parent folder') }
      foreach ($sid in @('S-1-5-32-544', 'S-1-5-18')) {
        $full = @($rules | Where-Object { ($_.IdentityReference.Value -eq $sid) -and ([string]$_.AccessControlType -eq 'Allow') -and ([string]$_.FileSystemRights -eq 'FullControl') -and ([string]$_.InheritanceFlags -eq 'ContainerInherit, ObjectInherit') })
        if ($full.Count -eq 0) { return ($path + ' does not give ' + $sid + ' full control of everything inside') }
      }
    }
    return ''
  }
  function Lock-PrivateRoot([string]$root) {
    New-Item -ItemType Directory -Force -Path $root | Out-Null
    $set = Invoke-Native 'icacls' @($root, '/inheritance:r', '/grant:r', '*S-1-5-32-544:(OI)(CI)F', '*S-1-5-18:(OI)(CI)F')
    if ($set.Code -ne 0) { throw ('STEP FAILED: the rights of ' + $root + ' could not be restricted -- ' + $set.Text + ' -- this block wrote no private data') }
    $problem = Test-PrivateAcl $root $true
    if ($problem) { throw ('STEP FAILED: ' + $problem + ' -- this block wrote no private data') }
    Write-Output ('PRIVATE_ACL ' + $root + ' = Administrators and SYSTEM only, verified')
  }
  function Assert-PrivateTree([string[]]$folders) {
    $count = 0
    foreach ($folder in $folders) {
      if (-not (Test-Path -LiteralPath $folder)) { continue }
      foreach ($item in @(@(Get-Item -LiteralPath $folder -Force) + @(Get-ChildItem -LiteralPath $folder -Recurse -Force))) {
        $problem = Test-PrivateAcl $item.FullName $false
        if ($problem) { throw ('STEP FAILED: ' + $problem) }
        $count = $count + 1
      }
    }
    Write-Output ('PRIVATE_ACL_ITEMS_VERIFIED=' + $count)
  }
  function Get-SiteState {
    $prodHead = (Invoke-Native 'git' @('-C', $prodRoot, 'rev-parse', 'HEAD')).Text
    $stagingHead = (Invoke-Native 'git' @('-C', $stagingRoot, 'rev-parse', 'HEAD')).Text
    $services = @(Get-Service -ErrorAction SilentlyContinue | Where-Object { $_.Name -like 'Transport*' } | Sort-Object Name | ForEach-Object { $_.Name + '=' + $_.Status }) -join ' '
    'prod=' + $prodHead + ' staging=' + $stagingHead + ' services=' + $services
  }
  function Get-Listeners {
    @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | ForEach-Object { [int]$_.OwningProcess })
  }
  function Get-ListenAddresses([int]$owner) {
    @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Where-Object { [int]$_.OwningProcess -eq $owner } | ForEach-Object { [string]$_.LocalAddress } | Select-Object -Unique)
  }

  if ((hostname) -ne $expectedHost) { throw "STEP FAILED: this computer is $(hostname), not $expectedHost" }
  $appDir = Join-Path $finRoot 'app'
  Lock-PrivateRoot $finRoot
  Lock-PrivateRoot $evidenceRoot
  Assert-PrivateTree @($finRoot, $evidenceRoot)

  $stateDir = Join-Path $finRoot 'state'
  $cloneDb = Join-Path (Join-Path $appDir 'instance') 'transport.db'
  $target = Join-Path (Join-Path $evidenceRoot $period) ('v' + $version)
  $expect = Join-Path (Join-Path $target 'expectations') $expectName
  $tool = Join-Path (Join-Path $appDir 'tools') 'fin_recon_storage.py'
  $pidFile = Join-Path $stateDir 'clone.json'
  if (-not (Test-Path -LiteralPath $cloneDb)) { throw 'STEP FAILED: the clone is not created -- run block 3 first' }
  if ((Invoke-Native 'git' @('-C', $appDir, 'rev-parse', 'HEAD')).Text -ne $sha) { throw 'STEP FAILED: the app folder is not on the reviewed commit' }

  $before = Get-SiteState
  $baselineFile = Join-Path $stateDir 'baseline.json'
  if (Test-Path -LiteralPath $baselineFile) {
    $baseline = Get-Content -LiteralPath $baselineFile -Raw | ConvertFrom-Json
    if ($baseline.state -eq $before) { Write-Output 'SINCE_BLOCK3=same' } else { Write-Output ('SINCE_BLOCK3=DIFFERENT block3: ' + $baseline.state + ' now: ' + $before) }
  }

  $verify = Invoke-Native $python @('-I', $tool, 'verify', '--db', $cloneDb, '--package', $target, '--expect', $expect)
  if (($verify.Code -ne 0) -or (-not $verify.Text.Contains('RESULT=PASS verify period=' + $period + ' version=' + $version + ' content_sha256=' + $contentSha))) { Write-Output $verify.Text; throw 'STEP FAILED: the clone database does not match the package' }
  Write-Output 'CLONE_VERIFY=PASS'

  $running = $null
  if (Test-Path -LiteralPath $pidFile) {
    $info = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
    $candidate = Get-Process -Id ([int]$info.Id) -ErrorAction SilentlyContinue
    if ($candidate -and ($candidate.StartTime.ToUniversalTime().Ticks -eq [long]$info.Started) -and (@(Get-Listeners) -contains [int]$info.Id)) {
      $running = $candidate
    } else {
      Remove-Item -LiteralPath $pidFile -Force
      Write-Output 'CLONE_STALE_RECORD_REMOVED'
    }
  }
  $started = $false
  if ($running) {
    Write-Output ('CLONE_ALREADY_RUNNING pid=' + $running.Id)
  } else {
    $busy = @(Get-Listeners)
    if ($busy.Count -gt 0) { throw "STEP FAILED: port $port is used by process $($busy[0]), not by the clone -- nothing was started" }
    $names = @('SECRET_KEY', 'PORT', 'HOST', 'FLASK_ENV', 'FUEL_API_TOKEN', 'DRONE_API_TOKEN', 'TG_BOT_TOKEN')
    $saved = @{}
    foreach ($name in $names) { $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    try {
      [Environment]::SetEnvironmentVariable('SECRET_KEY', ([BitConverter]::ToString($bytes) -replace '-', ''), 'Process')
      [Environment]::SetEnvironmentVariable('PORT', [string]$port, 'Process')
      [Environment]::SetEnvironmentVariable('HOST', '127.0.0.1', 'Process')
      [Environment]::SetEnvironmentVariable('FLASK_ENV', 'sqlite_prod', 'Process')
      foreach ($name in @('FUEL_API_TOKEN', 'DRONE_API_TOKEN', 'TG_BOT_TOKEN')) { [Environment]::SetEnvironmentVariable($name, $null, 'Process') }
      $running = Start-Process -FilePath $python -ArgumentList 'run_server.py' -WorkingDirectory $appDir -WindowStyle Minimized -PassThru
    } finally {
      foreach ($name in $names) { [Environment]::SetEnvironmentVariable($name, $saved[$name], 'Process') }
    }
    $started = $true
    [ordered]@{ Id = $running.Id; Started = $running.StartTime.ToUniversalTime().Ticks } | ConvertTo-Json | Set-Content -LiteralPath $pidFile -Encoding ASCII
    Write-Output ('CLONE_STARTED pid=' + $running.Id)
    $up = $false
    foreach ($i in 1..60) {
      if (@(Get-Listeners) -contains $running.Id) { $up = $true; break }
      if ($running.HasExited) { break }
      Start-Sleep -Seconds 1
    }
    if (-not $up) {
      if (-not $running.HasExited) { Stop-Process -Id $running.Id -Force }
      Remove-Item -LiteralPath $pidFile -Force
      throw "STEP FAILED: the clone did not start listening on port $port -- it was stopped; run block 4 again and watch its window"
    }
  }

  $addresses = @(Get-ListenAddresses $running.Id)
  $exposed = @($addresses | Where-Object { @('127.0.0.1', '::1') -notcontains $_ })
  if (($addresses.Count -eq 0) -or ($exposed.Count -gt 0)) {
    Stop-Process -Id $running.Id -Force
    Remove-Item -LiteralPath $pidFile -Force
    throw ('STEP FAILED: the clone listened on ' + ($addresses -join ', ') + ' -- it must listen on 127.0.0.1 only; it was stopped')
  }
  Write-Output ('CLONE_LISTENS=' + ($addresses -join ', ') + ' port ' + $port + ' only')

  try {
    $login = Invoke-WebRequest -Uri ('http://127.0.0.1:' + $port + '/login') -UseBasicParsing -TimeoutSec 30
    if (($login.StatusCode -ne 200) -or ($login.Content -notmatch 'vs-login-form')) { throw '/login did not render the login form' }
    $page = Invoke-WebRequest -Uri ('http://127.0.0.1:' + $port + '/drones/fin-recon') -UseBasicParsing -TimeoutSec 30
    if ($page.Content -notmatch 'vs-login-form') { throw '/drones/fin-recon without a session did not lead to the login form' }
  } catch {
    if ($started) {
      Stop-Process -Id $running.Id -Force
      Remove-Item -LiteralPath $pidFile -Force
      Write-Output ('CLONE_STOPPED_AFTER_FAILED_CHECK pid=' + $running.Id)
    }
    throw ('STEP FAILED: smoke check of the clone -- ' + $_.Exception.Message)
  }
  Write-Output 'CLONE_LOGIN=200'
  Write-Output 'CLONE_FIN_RECON_ANONYMOUS=login form (sign-in required)'

  $after = Get-SiteState
  if ($after -ne $before) { throw ('STEP FAILED: production or staging changed while this block ran (the block does not touch them) -- before: ' + $before + ' after: ' + $after) }
  Write-Output 'PROD_AND_STAGING_UNCHANGED=yes'
  $url = 'http://127.0.0.1:' + $port + '/drones/fin-recon'
  Write-Output ('CLONE_URL=' + $url)
  $edge = @(@('C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe', 'C:\Program Files\Microsoft\Edge\Application\msedge.exe') | Where-Object { Test-Path -LiteralPath $_ })
  $chrome = @(@('C:\Program Files\Google\Chrome\Application\chrome.exe', 'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe') | Where-Object { Test-Path -LiteralPath $_ })
  if ($edge.Count -gt 0) {
    Write-Output 'VIEW=on this server open the Run box (right-click Start, Run), paste the next line, press Enter -- Edge opens an InPrivate window'
    Write-Output ('"' + $edge[0] + '" --inprivate ' + $url)
  } elseif ($chrome.Count -gt 0) {
    Write-Output 'VIEW=on this server open the Run box (right-click Start, Run), paste the next line, press Enter -- Chrome opens an incognito window'
    Write-Output ('"' + $chrome[0] + '" --incognito ' + $url)
  } else {
    Write-Output ('VIEW=neither Edge nor Chrome is installed on this server; Internet Explorer shows the page without styles -- in it press Ctrl+Shift+P (InPrivate) and open ' + $url)
  }
  Write-Output 'STEP=PASS'
}
```

Ключевые строки: две `PRIVATE_ACL ... verified`, `CLONE_VERIFY=PASS`,
`CLONE_STARTED` (или `CLONE_ALREADY_RUNNING`), `CLONE_LISTENS=127.0.0.1 port
5052 only`, `CLONE_LOGIN=200`, `CLONE_FIN_RECON_ANONYMOUS`,
`PROD_AND_STAGING_UNCHANGED=yes`, `CLONE_URL`, `VIEW=` и строка под ней,
`STEP=PASS`.

### Проверка экрана — на самом сервере

1. Клон доступен **только с этого сервера**: адрес из строки `CLONE_URL` —
   `http://127.0.0.1:5052/drones/fin-recon`. С других компьютеров он не
   открывается — так и задумано.
2. В том же сеансе удалённого рабочего стола откройте окно «Выполнить»
   (правой кнопкой по «Пуску» → «Выполнить», или Win+R), вставьте строку,
   которую блок 4 напечатал **под** строкой `VIEW=`, и нажмите Enter:
   откроется Edge в отдельном окне **InPrivate** (или Chrome в окне
   инкогнито) сразу на нужном адресе. Окно «Выполнить» запускает браузер
   от вашей учётной записи, а не от администратора, — так и нужно; из окна
   PowerShell браузер не запускайте. Если блок написал, что ни Edge, ни
   Chrome на сервере нет, — Internet Explorer: `Ctrl+Shift+P` (InPrivate)
   и тот же адрес; страница в нём без оформления.
3. Войдите учётной записью администратора **staging** — у клона копия базы
   staging.
4. Пункт «Финансовая сверка» в полосе раздела «Дроны» виден только
   администратору. Проверьте экран по приватному списку
   `OWNER_UAT_CHECKLIST.md` (5–8 пунктов с ожидаемыми цифрами марта).
5. Проверьте отказ: войдите на клон пользователем без роли администратора
   (если такой есть в копии staging) — пункта нет, прямой адрес
   `/drones/fin-recon` отвечает «доступ запрещён».
6. Выгрузка XLSX сохраняется в «Загрузки» даже из окна InPrivate. Если
   открывали её — после проверки удалите файл оттуда (Shift+Delete). Закройте
   окно InPrivate — сессия клона в браузере не сохранится. Затем блок 5.

Клон работает, пока открыто его окно `python` и вы не вышли из сеанса
Windows.

---

## Блок 5. Клон: остановить

Останавливает процесс клона — только его. Процесс опознаётся по номеру,
**времени старта** и тому, что именно он слушает порт 5052; номер процесса
после перезагрузки может достаться другой программе (в том числе службе
staging или production — у них тот же `python.exe`), и такой процесс блок
не тронет. Сверяет, что production и базовый staging не изменились. Папку
клона и базу клона не удаляет — клон можно запустить снова блоком 4.

```powershell
& {
  $ErrorActionPreference = 'Stop'
  $expectedHost = 'srv-yoqsh'
  $prodRoot = 'C:\transport-report'
  $stagingRoot = 'C:\transport-report-staging'
  $finRoot = 'C:\VehicleSoft_FinRecon'
  $port = 5052
  function Invoke-Native([string]$exe, [string[]]$arguments) {
    $ErrorActionPreference = 'Continue'
    $global:LASTEXITCODE = -1
    $lines = @(& $exe @arguments 2>&1 | ForEach-Object { "$_" })
    [pscustomobject]@{ Code = $LASTEXITCODE; Lines = $lines; Text = ($lines -join "`n").Trim() }
  }
  function Get-SiteState {
    $prodHead = (Invoke-Native 'git' @('-C', $prodRoot, 'rev-parse', 'HEAD')).Text
    $stagingHead = (Invoke-Native 'git' @('-C', $stagingRoot, 'rev-parse', 'HEAD')).Text
    $services = @(Get-Service -ErrorAction SilentlyContinue | Where-Object { $_.Name -like 'Transport*' } | Sort-Object Name | ForEach-Object { $_.Name + '=' + $_.Status }) -join ' '
    'prod=' + $prodHead + ' staging=' + $stagingHead + ' services=' + $services
  }
  function Get-Listeners {
    @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | ForEach-Object { [int]$_.OwningProcess })
  }

  if ((hostname) -ne $expectedHost) { throw "STEP FAILED: this computer is $(hostname), not $expectedHost" }
  $pidFile = Join-Path (Join-Path $finRoot 'state') 'clone.json'
  $before = Get-SiteState
  if (Test-Path -LiteralPath $pidFile) {
    $info = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
    $clonePid = [int]$info.Id
    $process = Get-Process -Id $clonePid -ErrorAction SilentlyContinue
    if ($process) {
      $sameStart = ($process.StartTime.ToUniversalTime().Ticks -eq [long]$info.Started)
      if ((-not $sameStart) -or (@(Get-Listeners) -notcontains $clonePid)) { throw "STEP FAILED: process $clonePid is not the clone (its start time or port differ) -- nothing was stopped" }
      Stop-Process -Id $clonePid -Force
      Write-Output ('CLONE_STOPPED pid=' + $clonePid)
    } else {
      Write-Output ('CLONE_NOT_RUNNING pid=' + $clonePid)
    }
    Remove-Item -LiteralPath $pidFile -Force
  } else {
    Write-Output 'CLONE_NOT_RUNNING (no record of a started clone)'
  }
  foreach ($i in 1..30) {
    if (@(Get-Listeners).Count -eq 0) { break }
    Start-Sleep -Seconds 1
  }
  $busy = @(Get-Listeners)
  if ($busy.Count -gt 0) { throw "STEP FAILED: port $port is still used by process $($busy[0])" }
  Write-Output ('PORT_' + $port + '=free')
  $after = Get-SiteState
  if ($after -ne $before) { throw ('STEP FAILED: production or staging changed while this block ran (the block does not touch them) -- before: ' + $before + ' after: ' + $after) }
  Write-Output 'PROD_AND_STAGING_UNCHANGED=yes'
  Write-Output 'STEP=PASS'
}
```

---

## Блок 6. Клон: удалить (полный откат клона)

Удаляет папку `C:\VehicleSoft_FinRecon\app` — код ветки и базу клона.
Запускать после блока 5. **Не удаляет**: приватный архив на `D:`,
онлайн-копию базы staging (`C:\VehicleSoft_FinRecon\staging_backup`), базы
проверки восстановления, журналы прогонов — они остаются в закрытых
папках. Базовый staging и production не менялись — откатывать на них
нечего.

```powershell
& {
  $ErrorActionPreference = 'Stop'
  $expectedHost = 'srv-yoqsh'
  $finRoot = 'C:\VehicleSoft_FinRecon'
  $port = 5052

  if ((hostname) -ne $expectedHost) { throw "STEP FAILED: this computer is $(hostname), not $expectedHost" }
  $appDir = Join-Path $finRoot 'app'
  $pidFile = Join-Path (Join-Path $finRoot 'state') 'clone.json'
  if (Test-Path -LiteralPath $pidFile) { throw 'STEP FAILED: the clone may still run -- run block 5 first' }
  if (@(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue).Count -gt 0) { throw "STEP FAILED: port $port is in use -- run block 5 first" }
  if (Test-Path -LiteralPath $appDir) {
    Get-ChildItem -LiteralPath $appDir -Recurse -Force -File | ForEach-Object { $_.IsReadOnly = $false }
    Remove-Item -LiteralPath $appDir -Recurse -Force
    if (Test-Path -LiteralPath $appDir) { throw "STEP FAILED: $appDir is still there" }
    Write-Output ('CLONE_DELETED=' + $appDir)
  } else {
    Write-Output ('CLONE_ABSENT=' + $appDir)
  }
  Write-Output 'KEPT=archive, staging backup copy, restore test databases, run logs -- all in the restricted folders'
  Write-Output 'STEP=PASS'
}
```

---

## Блок 7. Второй независимый экземпляр архива

Копия пакета на **другой физический диск** (второй диск сервера или
съёмный диск **NTFS**) или в сетевую папку резервного хранилища. Соседняя
папка на том же диске резервом не считается — блок такой путь не примет.

Копия — те же приватные данные, поэтому перед копированием блок ставит и
проверяет на `выбранный корень\fin-recon-evidence` те же права «только
администраторы и SYSTEM». Не удалось (носитель FAT/exFAT без прав NTFS;
сетевая папка, где ваша учётная запись не администратор) — остановка, ничего
не скопировано.

Блок запускается дважды:

1. как есть (`$secondRoot = ''`) — печатает подходящие диски и
   останавливается, ничего не копируя;
2. впишите в первую строку блока выбранный корень — например букву
   съёмного диска `E:\` или сетевую папку `\\сервер\папка` — и запустите
   снова: блок закроет правами
   `выбранный корень\fin-recon-evidence`, скопирует туда пакет, проверит
   каждый файл копии по SHA-256 и допишет, где лежит копия, в
   `D:\fin-recon-evidence\2026-03\COPIES.txt`.

```powershell
& {
  $secondRoot = ''
  $ErrorActionPreference = 'Stop'
  $expectedHost = 'srv-yoqsh'
  $finRoot = 'C:\VehicleSoft_FinRecon'
  $evidenceRoot = 'D:\fin-recon-evidence'
  $python = 'C:\Program Files\Python314\python.exe'
  $contentSha = '42d09d775958e110de396a5cb3b3737292aaf5a647806b5b91574276a4973b6e'
  $period = '2026-03'
  $version = 1
  function Invoke-Native([string]$exe, [string[]]$arguments) {
    $ErrorActionPreference = 'Continue'
    $global:LASTEXITCODE = -1
    $lines = @(& $exe @arguments 2>&1 | ForEach-Object { "$_" })
    [pscustomobject]@{ Code = $LASTEXITCODE; Lines = $lines; Text = ($lines -join "`n").Trim() }
  }
  function Test-PrivateAcl([string]$path, [bool]$isRoot) {
    try {
      $acl = Get-Acl -LiteralPath $path
      $rules = @($acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))
    } catch {
      return ('the rights of ' + $path + ' cannot be read: ' + $_.Exception.Message)
    }
    $foreign = @($rules | Where-Object { ([string]$_.AccessControlType -eq 'Allow') -and (@('S-1-5-32-544', 'S-1-5-18') -notcontains $_.IdentityReference.Value) })
    if ($foreign.Count -gt 0) {
      $problem = $path + ' also grants access to ' + (@($foreign | ForEach-Object { $_.IdentityReference.Value } | Select-Object -Unique) -join ', ')
      $explicit = @($foreign | Where-Object { -not $_.IsInherited } | ForEach-Object { '*' + $_.IdentityReference.Value } | Select-Object -Unique)
      if ($explicit.Count -gt 0) { $problem = $problem + ' -- to take that access away run: icacls "' + $path + '" /remove:g ' + ($explicit -join ' ') + ' /T /C' }
      return $problem
    }
    if ($isRoot) {
      if (-not $acl.AreAccessRulesProtected) { return ($path + ' still inherits rights from its parent folder') }
      foreach ($sid in @('S-1-5-32-544', 'S-1-5-18')) {
        $full = @($rules | Where-Object { ($_.IdentityReference.Value -eq $sid) -and ([string]$_.AccessControlType -eq 'Allow') -and ([string]$_.FileSystemRights -eq 'FullControl') -and ([string]$_.InheritanceFlags -eq 'ContainerInherit, ObjectInherit') })
        if ($full.Count -eq 0) { return ($path + ' does not give ' + $sid + ' full control of everything inside') }
      }
    }
    return ''
  }
  function Lock-PrivateRoot([string]$root) {
    New-Item -ItemType Directory -Force -Path $root | Out-Null
    $set = Invoke-Native 'icacls' @($root, '/inheritance:r', '/grant:r', '*S-1-5-32-544:(OI)(CI)F', '*S-1-5-18:(OI)(CI)F')
    if ($set.Code -ne 0) { throw ('STEP FAILED: the rights of ' + $root + ' could not be restricted -- ' + $set.Text + ' -- this block wrote no private data') }
    $problem = Test-PrivateAcl $root $true
    if ($problem) { throw ('STEP FAILED: ' + $problem + ' -- this block wrote no private data') }
    Write-Output ('PRIVATE_ACL ' + $root + ' = Administrators and SYSTEM only, verified')
  }
  function Assert-PrivateTree([string[]]$folders) {
    $count = 0
    foreach ($folder in $folders) {
      if (-not (Test-Path -LiteralPath $folder)) { continue }
      foreach ($item in @(@(Get-Item -LiteralPath $folder -Force) + @(Get-ChildItem -LiteralPath $folder -Recurse -Force))) {
        $problem = Test-PrivateAcl $item.FullName $false
        if ($problem) { throw ('STEP FAILED: ' + $problem) }
        $count = $count + 1
      }
    }
    Write-Output ('PRIVATE_ACL_ITEMS_VERIFIED=' + $count)
  }

  if ((hostname) -ne $expectedHost) { throw "STEP FAILED: this computer is $(hostname), not $expectedHost" }
  Lock-PrivateRoot $evidenceRoot
  Assert-PrivateTree @($evidenceRoot)
  $periodDir = Join-Path $evidenceRoot $period
  $source = Join-Path $periodDir ('v' + $version)
  $tool = Join-Path (Join-Path (Join-Path $finRoot 'app') 'tools') 'fin_recon_storage.py'
  if (-not (Test-Path -LiteralPath $source)) { throw 'STEP FAILED: the archive is missing -- run block 2 first' }
  if (-not (Test-Path -LiteralPath $tool)) { throw 'STEP FAILED: the app folder is missing -- run block 2 first' }
  $sourceDisk = (Get-Partition -DriveLetter ([System.IO.Path]::GetPathRoot($source)).Substring(0, 1)).DiskNumber
  Write-Output ('PRIMARY=' + $source + ' DISK=' + $sourceDisk)
  if (-not $secondRoot) {
    foreach ($volume in @(Get-Volume | Where-Object { $_.DriveLetter -and $_.FileSystem })) {
      $disk = (Get-Partition -DriveLetter $volume.DriveLetter).DiskNumber
      if ($disk -ne $sourceDisk) {
        Write-Output ('CANDIDATE ' + $volume.DriveLetter + ':\ DISK=' + $disk + ' TYPE=' + $volume.DriveType + ' FS=' + $volume.FileSystem + ' FREE_GB=' + [math]::Round($volume.SizeRemaining / 1GB, 1))
      }
    }
    throw 'STEP FAILED: write a drive of ANOTHER disk (NTFS) or a network share into $secondRoot on the first line of this block, then run it again'
  }
  if ($secondRoot -like '\\*') {
    $kind = 'network share'
  } else {
    $secondDisk = (Get-Partition -DriveLetter ([System.IO.Path]::GetPathRoot($secondRoot)).Substring(0, 1)).DiskNumber
    if ($secondDisk -eq $sourceDisk) { throw "STEP FAILED: $secondRoot is on the same physical disk $sourceDisk as the archive -- that is not a second copy" }
    $kind = 'disk ' + $secondDisk
  }
  if (-not (Test-Path -LiteralPath $secondRoot)) { throw "STEP FAILED: $secondRoot is not reachable" }
  $copyRoot = Join-Path $secondRoot 'fin-recon-evidence'
  Lock-PrivateRoot $copyRoot
  $copy = Join-Path (Join-Path $copyRoot $period) ('v' + $version)
  if (Test-Path -LiteralPath $copy) {
    Write-Output ('SECOND_COPY_EXISTS=' + $copy)
  } else {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $copy) | Out-Null
    Copy-Item -LiteralPath $source -Destination $copy -Recurse
    Write-Output ('SECOND_COPY_WRITTEN=' + $copy + ' (' + $kind + ')')
  }
  Assert-PrivateTree @($copyRoot)
  $check = Invoke-Native $python @('-I', $tool, 'check-package', '--package', $copy)
  Write-Output $check.Text
  if (($check.Code -ne 0) -or (-not $check.Text.Contains('content_sha256=' + $contentSha))) { throw 'STEP FAILED: the second copy did not verify' }
  Add-Content -LiteralPath (Join-Path $periodDir 'COPIES.txt') -Value ((Get-Date -Format 's') + ' second copy ' + $copy + ' (' + $kind + ') verified content_sha256=' + $contentSha) -Encoding UTF8
  Write-Output 'SECOND_COPY=PASS'
  Write-Output 'STEP=PASS'
}
```

---

## Блок 8. Большой оригинал (полный архив марта) — записать хеш

`FIN_RECON_MARCH_FULL_EVIDENCE_20261012.zip` — в нём большой оригинал книги
Агрокластера с фотографиями (78 958 503 байт). Исполнителю он не
передавался, его хеша в пакете нет. Сохраните архив в «Загрузки» или на
«Рабочий стол» своей учётной записи на сервере и запустите блок: он
сначала проверит права закрытых папок, скопирует архив в закрытую
`C:\VehicleSoft_FinRecon\inbox` (и напомнит удалить исходную копию),
проверит, что внутри есть ровно один файл в 78 958 503 байт (не тот файл
блок отложит в сторону в той же inbox и остановится), скопирует
архив в `D:\fin-recon-evidence\2026-03\v1-full-evidence` и запишет SHA-256
и размер архива и самого большого оригинала в
`FULL_EVIDENCE_SHA256SUMS.txt` рядом. Пакет `v1` при этом не меняется.

```powershell
& {
  $ErrorActionPreference = 'Stop'
  $expectedHost = 'srv-yoqsh'
  $finRoot = 'C:\VehicleSoft_FinRecon'
  $evidenceRoot = 'D:\fin-recon-evidence'
  $period = '2026-03'
  $fullZip = 'FIN_RECON_MARCH_FULL_EVIDENCE_20261012.zip'
  $largeOriginalBytes = 78958503
  function Invoke-Native([string]$exe, [string[]]$arguments) {
    $ErrorActionPreference = 'Continue'
    $global:LASTEXITCODE = -1
    $lines = @(& $exe @arguments 2>&1 | ForEach-Object { "$_" })
    [pscustomobject]@{ Code = $LASTEXITCODE; Lines = $lines; Text = ($lines -join "`n").Trim() }
  }
  function Test-PrivateAcl([string]$path, [bool]$isRoot) {
    try {
      $acl = Get-Acl -LiteralPath $path
      $rules = @($acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))
    } catch {
      return ('the rights of ' + $path + ' cannot be read: ' + $_.Exception.Message)
    }
    $foreign = @($rules | Where-Object { ([string]$_.AccessControlType -eq 'Allow') -and (@('S-1-5-32-544', 'S-1-5-18') -notcontains $_.IdentityReference.Value) })
    if ($foreign.Count -gt 0) {
      $problem = $path + ' also grants access to ' + (@($foreign | ForEach-Object { $_.IdentityReference.Value } | Select-Object -Unique) -join ', ')
      $explicit = @($foreign | Where-Object { -not $_.IsInherited } | ForEach-Object { '*' + $_.IdentityReference.Value } | Select-Object -Unique)
      if ($explicit.Count -gt 0) { $problem = $problem + ' -- to take that access away run: icacls "' + $path + '" /remove:g ' + ($explicit -join ' ') + ' /T /C' }
      return $problem
    }
    if ($isRoot) {
      if (-not $acl.AreAccessRulesProtected) { return ($path + ' still inherits rights from its parent folder') }
      foreach ($sid in @('S-1-5-32-544', 'S-1-5-18')) {
        $full = @($rules | Where-Object { ($_.IdentityReference.Value -eq $sid) -and ([string]$_.AccessControlType -eq 'Allow') -and ([string]$_.FileSystemRights -eq 'FullControl') -and ([string]$_.InheritanceFlags -eq 'ContainerInherit, ObjectInherit') })
        if ($full.Count -eq 0) { return ($path + ' does not give ' + $sid + ' full control of everything inside') }
      }
    }
    return ''
  }
  function Lock-PrivateRoot([string]$root) {
    New-Item -ItemType Directory -Force -Path $root | Out-Null
    $set = Invoke-Native 'icacls' @($root, '/inheritance:r', '/grant:r', '*S-1-5-32-544:(OI)(CI)F', '*S-1-5-18:(OI)(CI)F')
    if ($set.Code -ne 0) { throw ('STEP FAILED: the rights of ' + $root + ' could not be restricted -- ' + $set.Text + ' -- this block wrote no private data') }
    $problem = Test-PrivateAcl $root $true
    if ($problem) { throw ('STEP FAILED: ' + $problem + ' -- this block wrote no private data') }
    Write-Output ('PRIVATE_ACL ' + $root + ' = Administrators and SYSTEM only, verified')
  }
  function Assert-PrivateTree([string[]]$folders) {
    $count = 0
    foreach ($folder in $folders) {
      if (-not (Test-Path -LiteralPath $folder)) { continue }
      foreach ($item in @(@(Get-Item -LiteralPath $folder -Force) + @(Get-ChildItem -LiteralPath $folder -Recurse -Force))) {
        $problem = Test-PrivateAcl $item.FullName $false
        if ($problem) { throw ('STEP FAILED: ' + $problem) }
        $count = $count + 1
      }
    }
    Write-Output ('PRIVATE_ACL_ITEMS_VERIFIED=' + $count)
  }

  if ((hostname) -ne $expectedHost) { throw "STEP FAILED: this computer is $(hostname), not $expectedHost" }
  $inbox = Join-Path $finRoot 'inbox'
  Lock-PrivateRoot $finRoot
  Lock-PrivateRoot $evidenceRoot
  New-Item -ItemType Directory -Force -Path $inbox | Out-Null
  Assert-PrivateTree @($finRoot, $evidenceRoot)

  $source = Join-Path $inbox $fullZip
  $dir = Join-Path (Join-Path $evidenceRoot $period) 'v1-full-evidence'
  $dest = Join-Path $dir $fullZip
  $sums = Join-Path $dir 'FULL_EVIDENCE_SHA256SUMS.txt'
  if (Test-Path -LiteralPath $dest) {
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $dest).Hash.ToLower()
    $recorded = ((Get-Content -LiteralPath $sums)[0] -split ' ')[0]
    if ($hash -ne $recorded) { throw "STEP FAILED: $dest does not match its recorded SHA-256" }
    Write-Output ('FULL_EVIDENCE_ALREADY=' + $dest + ' SHA256=' + $hash)
    Write-Output 'STEP=PASS'
    return
  }
  if (-not (Test-Path -LiteralPath $source)) {
    foreach ($place in @('Downloads', 'Desktop')) {
      $candidate = Join-Path (Join-Path $env:USERPROFILE $place) $fullZip
      if ((-not (Test-Path -LiteralPath $source)) -and (Test-Path -LiteralPath $candidate)) {
        Copy-Item -LiteralPath $candidate -Destination $source
        Write-Output ('FULL_EVIDENCE_COPIED_TO_INBOX from ' + $candidate + ' -- delete that copy once this block has passed')
      }
    }
    if (-not (Test-Path -LiteralPath $source)) { throw ('STEP FAILED: ' + $fullZip + ' not found -- save it into Downloads or Desktop of this Windows account, then run block 8 again') }
  }
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  $large = @()
  $zip = $null
  try {
    $zip = [System.IO.Compression.ZipFile]::OpenRead($source)
    $large = @($zip.Entries | Where-Object { $_.Length -eq $largeOriginalBytes })
    if ($large.Count -eq 1) {
      $stream = $large[0].Open()
      try {
        $memberHash = ([BitConverter]::ToString([System.Security.Cryptography.SHA256]::Create().ComputeHash($stream)) -replace '-', '').ToLower()
      } finally {
        $stream.Dispose()
      }
      $memberName = $large[0].FullName
    }
  } catch {
    $large = @()
  } finally {
    if ($zip) { $zip.Dispose() }
  }
  if ($large.Count -ne 1) {
    $aside = $source + '.rejected_' + (Get-Date -Format 'yyyyMMdd_HHmmss_fff')
    Move-Item -LiteralPath $source -Destination $aside
    throw ('STEP FAILED: ' + $fullZip + ' is not a ZIP holding exactly one file of ' + $largeOriginalBytes + ' bytes -- it was set aside as ' + $aside + '; save the right file into Downloads or Desktop, then run block 8 again')
  }
  New-Item -ItemType Directory -Force -Path $dir | Out-Null
  Copy-Item -LiteralPath $source -Destination $dest
  $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $dest).Hash.ToLower()
  $size = (Get-Item -LiteralPath $dest).Length
  $lines = @(
    ($hash + '  ' + $fullZip + '  bytes=' + $size),
    ($memberHash + '  member: ' + $memberName + '  bytes=' + $largeOriginalBytes),
    ('recorded=' + (Get-Date -Format 's') + ' by=' + $env:USERNAME)
  )
  Set-Content -LiteralPath $sums -Value $lines -Encoding UTF8
  Get-ChildItem -LiteralPath $dir -File | ForEach-Object { $_.IsReadOnly = $true }
  Assert-PrivateTree @($finRoot, $evidenceRoot)
  Write-Output ('FULL_EVIDENCE=' + $dest + ' BYTES=' + $size + ' SHA256=' + $hash)
  Write-Output ('LARGE_ORIGINAL_SHA256=' + $memberHash + ' BYTES=' + $largeOriginalBytes)
  Write-Output 'STEP=PASS'
}
```

Пришлите строки `FULL_EVIDENCE=...` и `LARGE_ORIGINAL_SHA256=...` — они
войдут в приватный манифест следующей редакцией отчёта. Сам файл приватный.

---

## Если что-то пошло не так

* Блок остановился на `STEP FAILED` — пришлите весь вывод. Всё, что блок
  успел сделать до этой строки, перечислено в его выводе выше неё.
* Остановка на правах (`could not be restricted`, `cannot be read`, `also
  grants access to`, `still inherits`) — блок остановился до записи
  приватного. Чаще всего это след Проводника: кнопка «Продолжить» на
  закрытой папке добавила вашу учётную запись в её права. Тогда в той же
  строке после `to take that access away run:` блок печатает готовую
  команду `icacls`, которая убирает именно эту запись. Выполните её в том
  же окне PowerShell и запустите блок снова. Других прав вручную не
  меняйте; если команды в строке нет — пришлите вывод.
* Блок отложил ZIP (`set aside as ...rejected_...`) — в «Загрузках» или на
  «Рабочем столе» лежит не тот файл. Сохраните туда правильный и запустите
  блок снова; отложенная копия остаётся в закрытой inbox.
* Экран клона открывается только в браузере **на самом сервере** по
  `http://127.0.0.1:5052/drones/fin-recon`. С другого компьютера он не
  открывается — так и задумано.
* Окно клона (`python`) можно закрыть — клон остановится; потом выполните
  блок 5: он уберёт запись о запуске.
* Ни один блок не меняет production и базовый staging. Если блок пишет, что
  они изменились, — изменил кто-то другой: остановитесь и пришлите вывод.

## Откат

* **Клон** — блоки 5 и 6. Больше на сервере откатывать нечего: базовый
  staging и production этой задачей не менялись, база staging только
  копировалась, порт и брандмауэр не трогались. Онлайн-копия базы staging,
  снятая блоком 3, — доказательство состояния на момент копирования,
  восстанавливать из неё ничего не нужно.
* **Архив** на `D:\fin-recon-evidence` откатывать не нужно — это хранилище
  доказательств. Удалять его нельзя без отдельного решения владельца.
* **Код в `main`** (если PR когда-нибудь вольют) — `git revert` коммитов
  задачи в обратном порядке; таблицы `fin_recon_*` после отката кода
  удаляются вручную по списку в `migrate_fin_recon_storage_001.py` и только
  после отката кода. Ни одна строка других таблиц не меняется.
