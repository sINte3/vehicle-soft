# DRONE-USEFUL-AREA-PILOT-001 — операторский комплект

Контролируемый пилот расчётной полезной площади на **площадке**. Один день —
**2026-06-05**.

## Две ревизии, и они разные

| | Что это | Где стоит |
|---|---|---|
| **PRODUCT_SHA** | `c3e6a12ab95117710eeea5e05133f5cd548b698e` — проверенная ревизия продукта (merge PR #113, второй родитель `82b3a2f`) | `C:\transport-report-staging` |
| **KIT_SHA** | ревизия комплекта. **Не константа**: комплект живёт в коммите, который её создаёт. Измеряется у чекаута комплекта и печатается шагом 1 | `C:\vehicle-soft-pilot-kit` и `C:\VehicleSoft_DJI_StageB_Pilot` |

Комплект живёт в **отдельном чекауте** и поэтому не исчезает, когда целевой
репозиторий переключают или откатывают.

**Сборщик на BAK-TEX11 работает на ревизии КОМПЛЕКТА, а не продукта.** Ревизия
комплекта добавила в сводку сбора два числа — `probe_request_failures` и
`probe_pending_requests`, — без которых полноту живого захвата приходилось
выводить из равенства `observations == confirmed`. Вывод неверен: запрос,
умерший до тела, в `observations` не попадает вовсе, равенство держится, а
маршрут потерян. Площадка при этом остаётся на `PRODUCT_SHA`.
`PRODUCT_BLOBS.json` записывает, какие семь файлов побайтово одинаковы на
обеих ревизиях и какой один отличается намеренно.

**Production не участвует.** Ни один скрипт не пишет в `C:\transport-report`,
не останавливает `TransportReport`, не применяет миграцию к продовой базе и не
обращается к `http://10.103.25.14:5050`. Продовую базу трогает один шаг —
предполётный, — и только как **источник онлайн-бэкапа**.

---

## Шаг 0 — развернуть комплект (один раз)

Комплект — отдельный клон. Выполняется на сервере; `KIT_SHA` берётся из
описания PR, где он назван как head ветки пилота.

```
git clone https://github.com/sINte3/vehicle-soft.git C:\vehicle-soft-pilot-kit
Set-Location C:\vehicle-soft-pilot-kit
git fetch origin
git checkout --detach KIT_SHA_FROM_THE_PR
git status --porcelain
```

Последняя команда обязана напечатать **пусто**: комплект измеряет свою
ревизию, а грязное дерево означает, что исполняются не те байты, которые
записаны в коммите.

То же самое на BAK-TEX11, но там чекаут уже есть:

```
Set-Location C:\VehicleSoft_DJI_StageB_Pilot
git status --porcelain
```

Тоже пусто. Ревизию туда переведёт сам скрипт шага 3.

---

## Что в комплекте

| Файл | Где выполняется | Что делает |
|---|---|---|
| `PREFLIGHT_AND_COPY_TEST.ps1` | сервер `srv-yoqsh` | **открывает запуск**, изолированная копия продовой базы, миграция на копии дважды, неизменность `area_ha` |
| `STAGING_DEPLOY_AND_MIGRATE.ps1` | сервер | бэкап площадки **с проверкой**, ff до `PRODUCT_SHA`, проверки, миграция, запуск службы, smoke-тест |
| `STAGING_ROLLBACK.ps1` | сервер | откат площадки по улике запуска |
| `BAK_TEX11_DJI_COLLECT_TO_STAGING.ps1` | **BAK-TEX11** | живой сбор маршрутов и отправка только на площадку |
| `STAGING_RECALCULATE_AND_VERIFY.ps1` | сервер | **ворота по улике сбора**, сухой прогон, `--apply`, повторный `--apply` |
| `STAGING_PILOT_REPORT.ps1` | сервер | один JSON и один Markdown с вердиктом |
| `PilotKit.psm1` | — | все гвардии комплекта; совместим с Windows PowerShell 5.1 |
| `PRODUCT_BLOBS.json` | — | какие байты комплект имеет право исполнять |
| `pilot_*.py` | — | приборы: осмотр базы, ревизии и blob-и, ворота сбора, разбор сводок, отчёт |

---

## Порядок для владельца

Каждый блок выполняется **целиком**. Шаг 1 печатает `RUN_ID=` и `KIT_SHA=`;
дальше они подставляются в каждую команду вместо `RUN_ID_FROM_STEP_1` и
`KIT_SHA_FROM_STEP_1`. **Все пути в командах ниже — окончательные**, подставлять
в них ничего не нужно.

### Блок 1 — предполётная проверка, миграция на копии, открытие запуска

```
Set-Location C:\vehicle-soft-pilot-kit
.\ops\pilot_useful_area_001\PREFLIGHT_AND_COPY_TEST.ps1
```

**Вернуть:** весь вывод. Ключевые строки — `RUN_ID=`, `KIT_SHA=`,
`STAGING_SERVICE=`, `COPY_AREA_SHA256=`, `COPY_AFTER_AREA_SHA256=`,
`PREFLIGHT_AND_COPY_TEST=PASS`.

### Блок 2 — обновление и миграция площадки

```
Set-Location C:\vehicle-soft-pilot-kit
.\ops\pilot_useful_area_001\STAGING_DEPLOY_AND_MIGRATE.ps1 -RunId RUN_ID_FROM_STEP_1
```

**Вернуть:** весь вывод; строки `BACKUP=`, `BACKUP_INTEGRITY=`, `SHA_AFTER=`,
`SMOKE_TEST`, `STAGING_DEPLOY_AND_MIGRATE=PASS`.

Если блок отказал:

```
Set-Location C:\vehicle-soft-pilot-kit
.\ops\pilot_useful_area_001\STAGING_ROLLBACK.ps1 -RunId RUN_ID_FROM_STEP_1
```

### Блок 3 — живой сбор DJI (на BAK-TEX11)

```
Set-Location C:\VehicleSoft_DJI_StageB_Pilot
.\ops\pilot_useful_area_001\BAK_TEX11_DJI_COLLECT_TO_STAGING.ps1 -RunId RUN_ID_FROM_STEP_1 -KitSha KIT_SHA_FROM_STEP_1
```

Откроется браузер: Task History → **день 2026-06-05** → вид «Карта» →
дождаться прорисовки → вернуться в консоль и нажать Enter.

Скрипт напечатает точный путь, куда положить один файл `collect.json` на
сервере. Путь фиксированный и выглядит так:

```
D:\transport-report-backups\pilot\DRONE-USEFUL-AREA-001\runs\RUN_ID_FROM_STEP_1\evidence\collect.json
```

**Вернуть:** весь вывод и этот файл. Журнал прогона остаётся на BAK-TEX11 и в
отчёт не входит.

### Блок 4 — пересчёт на площадке

```
Set-Location C:\vehicle-soft-pilot-kit
.\ops\pilot_useful_area_001\STAGING_RECALCULATE_AND_VERIFY.ps1 -RunId RUN_ID_FROM_STEP_1
```

Первое, что делает блок, — **ворота по улике сбора**. Пока захват не полон,
не подтверждён и не принят площадкой целиком, пересчёт не запускается вовсе и
в базу не пишется ничего.

**Вернуть:** весь вывод, включая `COLLECT_GATE=PASS`, `DRY_RUN_WROTE_NOTHING=`,
`APPLY_SECONDS=`, `SECOND_APPLY ...`, `STAGING_RECALCULATE_AND_VERIFY=PASS`.

Затем открыть глазами:

```
http://10.103.25.14:5051/drones/coverage?date_from=2026-06-05&date_to=2026-06-05
```

### Блок 5 — итоговый безопасный отчёт

```
Set-Location C:\vehicle-soft-pilot-kit
.\ops\pilot_useful_area_001\STAGING_PILOT_REPORT.ps1 -RunId RUN_ID_FROM_STEP_1
```

**Вернуть:** строки `VERDICT=`, `REASON=`, `PRODUCTION_ROLLOUT_AUTHORISED=`,
`PRIVACY_SCAN=` и **оба файла** отчёта.

---

## Вердикт: чего комплект не решает

| Вердикт | Что означает |
|---|---|
| `REJECT` | нарушено обязательное условие, улики не про один запуск, или отчёт не прошёл проверку на приватные значения |
| `ADJUST` | тракт исправен, но правило владельца нарушено |
| `TECHNICAL_GO` | тракт исправен, **решение владельца не принято**, production этим отчётом НЕ разрешён |
| `GO` | тракт исправен И оба правила владельца названы и соблюдены |

**Порог доли работ без числа и допустимое отклонение от площади DJI — это
бизнес-правила.** Устав запрещает их выдумывать, поэтому значения по умолчанию
у них НЕТ. Пока владелец их не назвал, верхний возможный вердикт —
`TECHNICAL_GO`. Когда назовёт:

```
Set-Location C:\vehicle-soft-pilot-kit
.\ops\pilot_useful_area_001\STAGING_PILOT_REPORT.ps1 -RunId RUN_ID_FROM_STEP_1 -OwnerShareThreshold 0.5 -OwnerDjiDeltaPercent 90
```

(числа в примере — **пример формата**, не рекомендация.)

## Три показателя, которых схема не хранит

`DRONES_USEFUL_AREA_001` держит `work_segments` на работу, но не число отрезков
холостого перелёта, не признак «вылет целиком холостой» и не `mission_state`
на вылет. В отчёте они `null` с кодом `NOT_RECORDED_BY_SCHEMA`, а рядом стоят
честные величины уровня работы: `works_with_zero_work_segments`,
`works_with_mixed_mission_state`, `work_segments`. Посчитать их можно только
новой колонкой или вторым разбором геометрии — это новая функция продукта,
запрещённая этим макроэтапом.

## День

У режима `--route-ui-collect` нет `--from/--to`: день — тот, который оператор
открыл в кабинете. Держит день не флаг, а проверка на площадке: блок 4
отказывается считать, если хоть один принятый маршрут принадлежит другим
локальным суткам.
