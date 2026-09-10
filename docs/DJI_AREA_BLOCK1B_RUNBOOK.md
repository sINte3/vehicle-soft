# DRONE-AREA этап 1B — один готовый блок для владельца

Собирает evidence bundle по дронам №5 и №6 за 18.08.2026 на площадке.

**Ничего не меняет.** Базу открывает только на чтение, служба не трогается,
рабочая копия `C:\transport-report-staging` не изменяется: код берётся в
отдельный временный клон. Production не участвует вовсе.

Блок сам печатает sha256 файла базы **до и после** прогона — это и есть
доказательство того, что чтение было чтением. Если хеши разошлись, работу
надо остановить и сказать об этом.

## Что нужно прислать обратно

Каталог `C:\VehicleSoft_Block1B\out3` целиком (несколько МБ: JSON, CSV,
GeoJSON) и **полный текст вывода консоли**, включая обе строки sha256.

## Блок

По одной команде на строку. В PowerShell нет `&&` — вставлять целиком, ничего
не редактируя.

```powershell
$ErrorActionPreference = 'Continue'
$staging = 'C:\transport-report-staging'
$db      = 'C:\transport-report-staging\instance\transport.db'
$work    = 'C:\VehicleSoft_Block1B'
$out     = 'C:\VehicleSoft_Block1B\out3'
$py      = 'C:\Program Files\Python314\python.exe'
$branch  = 'claude/dji-agras-area-review-7sw9c1'
if (-not (Test-Path -LiteralPath $db)) { throw "STEP FAILED: database not found: $db" }
if (-not (Test-Path -LiteralPath $py)) { throw "STEP FAILED: python not found: $py" }
if ($staging -notlike '*transport-report-staging*') { throw "STEP FAILED: refusing a root that is not the staging checkout" }
$before = (Get-FileHash -LiteralPath $db -Algorithm SHA256).Hash
Write-Host "DB SHA256 BEFORE: $before"
$origin = (& git -C $staging config --get remote.origin.url)
if (-not $origin) { throw "STEP FAILED: cannot read origin url from $staging" }
if (Test-Path -LiteralPath $work) { Remove-Item -LiteralPath $work -Recurse -Force }
& git clone --branch $branch --depth 1 --quiet $origin $work
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: git clone exit $LASTEXITCODE" }
& git -C $work --no-pager log --oneline -1
Set-Location $work
& $py tools\test_dji_area_block1b.py
if ($LASTEXITCODE -ne 0) { throw "STEP FAILED: tool self-test exit $LASTEXITCODE" }
& $py tools\dji_area_block1b.py --db $db --date 2026-08-18 --hardware 1581F574B2387001009R --hardware 1581F574B235W00100Q5 --out $out
$rc = $LASTEXITCODE
Write-Host "BUNDLE EXIT CODE: $rc"
$after = (Get-FileHash -LiteralPath $db -Algorithm SHA256).Hash
Write-Host "DB SHA256 AFTER : $after"
if ($before -ne $after) { throw "STOP: the database changed during a read-only run" }
Write-Host "READ-ONLY CONFIRMED: database bytes identical"
Get-ChildItem -LiteralPath $out | Select-Object Name, Length | Format-Table -AutoSize
Compress-Archive -Path "$out\*" -DestinationPath 'C:\VehicleSoft_Block1B\block1b_out3.zip' -Force
Write-Host 'SEND BACK: C:\VehicleSoft_Block1B\block1b_out3.zip and the console text above'
```

## Что делать, если блок остановился

- `database not found` — база площадки лежит не по этому пути; прислать
  фактический путь, блок будет выдан заново.
- `git clone exit ...` — сервер не смог получить ветку. Прислать текст ошибки.
- `tool self-test exit ...` — окружение не проходит собственный самотест
  инструмента; прогон боевой базы **не начинался**. Прислать вывод.
- `exit 1` у bundle — на этой базе не применена миграция
  `DJI_AREA_EVIDENCE_001`.
- `exit 2` у bundle — база не найдена, файл не создавался.
- `STOP: the database changed` — остановиться и сообщить. Такого быть не
  должно: инструмент открывает базу через `mode=ro`.

## Если строк за 18.08 нет

Инструмент читает расчёты ТОЛЬКО текущей версии алгоритма. Если прогон на
площадке был на прежней версии, он напечатает `NOTE: no rows for this
algorithm version` и выйдет кодом 0 с пустой выгрузкой. Это честный отказ, а
не ошибка. Тогда сообщите — блок будет выдан либо с
`--algorithm-version <прежняя>`, либо после отдельного решения о пересчёте
(пересчёт — это ЗАПИСЬ в базу, и он требует вашего согласия отдельно).

## Что дальше

Bundle сам по себе числом площади к счёту **не является**. Он содержит
`V4_APPLICATION_COVERAGE_ESTIMATE` — оценку уникальной поверхности под
подтверждённым применением, с отдельно показанными повторным покрытием,
выносом за контур, холостым пролётом и неизвестным. Соответствие записанной
ширины реальной полосе осаждения проверяется только полевой калибровкой
(этап 2, методика NY/T 3213—2023).

Bundle содержит: уникальное покрытие, его долю внутри исторического контура
и вынос за него, повторное покрытие, холостой пролёт, разложение длин по
причинам, S против U, и неизменяемые тела V4/маршрутов/геометрии с проверкой
SHA/MD5. Разбиение «внутри/снаружи» появляется ТОЛЬКО там, где историческая
геометрия контура доказана; иначе выводится причина, а не число.

После получения каталога работа продолжается в той же задаче: разбор кластеров
по `land_uuid`, forensic плоских записей и сравнение S против U на
различающей подвыборке.
