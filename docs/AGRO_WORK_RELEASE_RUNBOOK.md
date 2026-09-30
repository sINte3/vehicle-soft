# agro-work: вывод на production

Трек `docs/tracks/agro-work.md`. Код B1–B3 и N = 2 смержены (PR #149,
#151); проверка на копии боевой базы пройдена 29–30.09
(`docs/AGRO_WORK_B1_RUNBOOK.md`, шаги 0–11).

## Как это устроено

- **Код на сервер привозит релиз**, не этот ранбук: общий порядок
  `docs/RELEASE_AND_BACKUP_PROCEDURE.md`, шаги 1–10 — остановка трёх служб,
  резервная копия, `git merge --ff-only`, миграции, подъём служб, дымовая
  проверка. Здесь ни одной команды git, которая меняет
  `C:\transport-report`, нет.
- **Миграцию `AGRO_WORK_001` запускает релиз** — на шаге 7, при
  остановленных службах. Что о ней знать ведущему релиза, — первый раздел.
- **Остальное делает владелец после релиза**, при работающих службах:
  первый импорт на боевой базе, перенос своих решений с копии (связки шага 9
  и методы шага 10) и ночное расписание (вопрос 10, решено 29.09).
- В agro-work по-прежнему ничего не пишется: клиент умеет только читать.
  Учётные данные — тот же файл `C:\VehicleSoft_Secrets\agro_work_credentials.txt`,
  содержимое не печатается ни одним инструментом.

Все команды — в **PowerShell от имени администратора** (окно открывается в
`C:\Windows\system32`), по одной строке, как есть: плейсхолдеров нет. Каждый
шаг, который запускает скрипт или пишет файл, начинается с
`cd C:\transport-report`.

---

## Что знать ведущему релиза

- **Дельта** `eb7d003..main` на 30.09: код приложения меняет только
  agro-work (PR #149, #151: `agro_work/`, `agro_work_routes.py`,
  `templates/agro_work/`, и объявленные в PR общие файлы `app.py`,
  `templates/base_next.html`, `templates/_module_nav.html`). PR Дронов
  #146–#148 и #150 — документы и инструменты.
- **Миграций в дельте одна** — `migrate_agro_work_001.py`, семь новых
  таблиц, ни одна существующая не меняется. Ожидаемый дрейф шага 6:
  `file-but-not-registered` **0 → 1 → 0**.
- **Шаг 7**, первый прогон печатает

  ```
  Done. 7 agro_work tables (100 columns), 7 indexes and 4 triggers are in place.
  ```

  второй — `Already applied. Nothing to do.`
- **Шаг 9**: в боковом меню «Сверка agro-work» (право `wialon`). До шага 2
  ниже страница говорит «Импорт ещё не запускался.» — так и должно быть. Если
  она говорит «Импорт agro-work на этом сервере ещё не установлен», миграция
  не применена.

---

## Шаг 0 (до релиза, в любое время). Файлы на месте

Всё только читается.

```powershell
Test-Path C:\VehicleSoft_Secrets\agro_work_credentials.txt
```

```powershell
Test-Path C:\VehicleSoft_AgroWork\agro_work_unmatched.csv
```

```powershell
Test-Path C:\VehicleSoft_AgroWork\agro_work_methods.xlsx
```

**Ожидается:** три раза `True`. Это файл учётных данных, ваши ручные связки
(шаг 9 проверки) и ваша разметка методов (шаг 10).
**Прислать:** три строки. Если где-то `False` — дальше не идти.

## Шаг 1. Код релиза на месте

```powershell
cd C:\transport-report
```

```powershell
git --no-pager log --oneline -1
```

```powershell
git merge-base --is-ancestor 012390c HEAD
```

```powershell
$LASTEXITCODE
```

**Ожидается:** строка коммита; затем `0` — релиз привёз мерж PR #151
(`012390c`) или более новый код. `1` — agro-work в релиз не попал, дальше
не идти.
**Прислать:** строку коммита и число.

## Шаг 2. Первый импорт на боевой базе

Около 40 минут: обход заявок и 1 000 историй статусов, один запрос в 2 с.
Службы работают — импорт пишет только в таблицы agro-work короткими
транзакциями.

```powershell
cd C:\transport-report
```

```powershell
& "C:\Program Files\Python314\python.exe" tools\agro_work_import.py --credentials C:\VehicleSoft_Secrets\agro_work_credentials.txt --max-history 1000 *> agro_import_prod_1.log
```

```powershell
Get-Content agro_import_prod_1.log -Tail 25
```

**Ожидается:** `agro-work import run 1: ok`; в строке `applications:` почти
все заявки новые (около 5 000), `rejected 0`; `history: fetched 1000 |
failed 0 | still pending ...`; строка `links:` — по номеру около 286; в конце
`unmatched machines: ...`. Файл `agro_work_unmatched.csv` в
`C:\transport-report` — новый, пустой по `equipment_id`: ваши решения берутся
из копии в шаге 3.
**Прислать:** `C:\transport-report\agro_import_prod_1.log`.

## Шаг 3. Ручные связки — из вашего файла шага 9

План — ничего не пишет:

```powershell
cd C:\transport-report
```

```powershell
& "C:\Program Files\Python314\python.exe" tools\agro_work_links.py --from-csv C:\VehicleSoft_AgroWork\agro_work_unmatched.csv
```

**Ожидается:** 46 строк `link   ... -> equipment ... (название / госномер /
хозяйство)` и `dry run: nothing was written`. Пробегите глазами названия —
это те же 46 машин, что 29.09.

Запись:

```powershell
& "C:\Program Files\Python314\python.exe" tools\agro_work_links.py --from-csv C:\VehicleSoft_AgroWork\agro_work_unmatched.csv --apply
```

**Ожидается:** `written: 46 link(s), 0 unlink(s)` и строка `links now:
manual 46 | ...`.
**Прислать:** вывод обеих команд.

## Шаг 4. Методы сверки — из вашей книги шага 10

План — ничего не пишет:

```powershell
cd C:\transport-report
```

```powershell
& "C:\Program Files\Python314\python.exe" tools\agro_work_methods.py --import C:\VehicleSoft_AgroWork\agro_work_methods.xlsx
```

**Ожидается:** строки `set ...` и итог `set 81 | change 0 | clear 0 | same
20` (если видов работ по-прежнему 101), затем `dry run: nothing was written`.

Запись:

```powershell
& "C:\Program Files\Python314\python.exe" tools\agro_work_methods.py --import C:\VehicleSoft_AgroWork\agro_work_methods.xlsx --apply
```

**Ожидается:** тот же план и `written: 81`.
**Прислать:** вывод обеих команд.

## Шаг 5. Ночной импорт (вопрос 10)

Командный файл рядом с проектом, как у задач GPS:

```powershell
cd C:\transport-report
```

```powershell
Set-Content -Path C:\transport-report\agro_work_import.bat -Encoding Ascii -Value '@echo off', 'cd /d C:\transport-report', '"C:\Program Files\Python314\python.exe" tools\agro_work_import.py --credentials C:\VehicleSoft_Secrets\agro_work_credentials.txt --max-history 1000 >> agro_work_import.log 2>&1'
```

Задача — в 04:00, после ночных задач сервера: сбор GPS 01:00, резервная
копия 02:00, суточный расчёт GPS 02:30. Прогон с 1 000 историй занимает около
40 минут.

```powershell
schtasks /create /tn "AgroWorkImport" /tr "C:\transport-report\agro_work_import.bat" /sc daily /st 04:00 /ru SYSTEM /f
```

```powershell
schtasks /query /tn "AgroWorkImport" /fo LIST
```

**Ожидается:** сообщение об успешном создании задачи; в `/query` — время
следующего запуска в 04:00. Задача идёт от SYSTEM: файл учётных данных ей
доступен (права шага 3 проверки).
**Прислать:** вывод `/query`.

## Шаг 6. Сверка на боевой базе

База только читается; книга `agro_work_reconcile_2026-09-27.xlsx` появится
в `C:\transport-report`.

```powershell
cd C:\transport-report
```

```powershell
& "C:\Program Files\Python314\python.exe" tools\agro_work_reconcile.py --from 2026-09-01 --to 2026-09-27 *> agro_reconcile_prod.log
```

```powershell
Get-Content agro_reconcile_prod.log
```

**Ожидается:** те же строки, что в шаге 11 на копии, и числа того же
порядка, что в предпросмотре N = 2 от 30.09: заявка → работа 99 / 1 / около
1 500 без вердикта, работа → заявка без заявки около 190. Расхождение —
история статусов: на боевой базе пока 1 000 историй против 1 300 на копии.
Экран «Сверка agro-work» показывает то же самое.
**Прислать:** `agro_reconcile_prod.log`.

## Шаг 7. Когда история догрузится — умолчание 300

Каждое утро первые дни:

```powershell
cd C:\transport-report
```

```powershell
Select-String -Path C:\transport-report\agro_work_import.log -Pattern "still pending" | Select-Object -Last 1
```

Когда в строке `still pending 0` (около трёх ночей), ночной прогон переводится
на умолчание — 300 историй за раз, только новые и изменившиеся заявки:

```powershell
Set-Content -Path C:\transport-report\agro_work_import.bat -Encoding Ascii -Value '@echo off', 'cd /d C:\transport-report', '"C:\Program Files\Python314\python.exe" tools\agro_work_import.py --credentials C:\VehicleSoft_Secrets\agro_work_credentials.txt >> agro_work_import.log 2>&1'
```

Задачу пересоздавать не нужно: она запускает тот же файл.
**Прислать:** строку `still pending 0`.

## Шаг 8 (по желанию). Папка проверки

После шагов 3 и 4 ваши решения живут в боевой базе, с журналом. Папка
`C:\VehicleSoft_AgroWork` с копией базы больше не нужна; `agro_work_unmatched.csv`
и `agro_work_methods.xlsx` из неё можно сохранить себе как копию. Удаляется
вручную, когда сочтёте нужным. `C:\VehicleSoft_Secrets` не удалять — его
читает ночная задача.

---

## Что делать, если

| Сообщение | Что это | Что делать |
|---|---|---|
| `tables missing: ... run migrate_agro_work_001.py first` в шаге 2 | релиз не применил миграцию | ничего не записано и в agro-work не спрошено; миграция — шаг 7 релиза при остановленных службах |
| `the database is read-only for this window` | окно PowerShell открыто без прав администратора | открыть PowerShell от имени администратора, `cd C:\transport-report`, повторить |
| `import run N is still running` | идёт другой прогон, например ночной | дождаться; если окно закрыто давно — прислать строку |
| `the run stopped: ... -> 503` или `-> 429` | их сервер занят | всё прочитанное сохранено; повторить шаг через час |
| `equipment N already belongs to agro-work machine ...` в шаге 3 | номер нашей машины занят другой машиной agro-work | прислать строку; ничего не записано |
| в `/query` нет задачи или `Last Result` не 0 | задача не создана или упала | прислать вывод `schtasks /query /tn "AgroWorkImport" /fo LIST /v` и хвост `agro_work_import.log` |

## Откат

- **Ночной импорт:** `schtasks /delete /tn "AgroWorkImport" /f`, файл
  `agro_work_import.bat` удалить вручную.
- **Код** — откатом релиза, `docs/RELEASE_AND_BACKUP_PROCEDURE.md`, раздел
  Rollback.
- **Данные** — только после отката кода и только вручную, по докстрингу
  `migrate_agro_work_001.py`: сначала выгрузить ваши связки и методы, потом
  удалить семь таблиц. Автоматически ничего не удаляется.
