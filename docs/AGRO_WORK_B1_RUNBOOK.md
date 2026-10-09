# agro-work: живые прогоны импорта и сверки на копии базы

Порядок для владельца. Трек `docs/tracks/agro-work.md`, инкременты B1–B3.
Облачная сессия до `api.agro-work.uz` не ходит и ходить не должна: вход под
учётной записью владельца, и живые прогоны делает владелец на сервере.

## Как это устроено и почему ничего боевого не меняется

- Код — **отдельный клон ветки** в `C:\VehicleSoft_AgroWork`. В
  `C:\transport-report` ничего не обновляется: `git pull` туда — только
  релизом (правило трека GPS от 28.09).
- База — **копия боевой** в `C:\VehicleSoft_AgroWork\instance\transport.db`.
  Миграция и импорт находят базу по месту своего файла, поэтому из этой
  папки они физически пишут только в копию. Боевая база читается один раз —
  при копировании, открытой только на чтение.
- Учётные данные agro-work — файл `C:\VehicleSoft_Secrets\agro_work_credentials.txt`
  вне любой рабочей копии git, доступ — администраторам, SYSTEM и вам.
  Содержимое не печатается ни одним инструментом.
- В agro-work не пишется ничего: клиент технически умеет только GET по
  четырём адресам плюс вход и обновление токена; всё прочее — отказ до
  отправки, и это проверяет тест (шаг 2).
- Бережно к их серверу: один запрос в 2 секунды; история статусов — только
  для новых и изменившихся закрытых заявок; первичная загрузка истории
  растянута на несколько прогонов (`--max-history`).

Все команды — в **PowerShell от имени администратора**, по одной строке,
как есть: плейсхолдеров в них нет. Длинный прогон пишет вывод в файл
(`*> файл.log`): консоль Windows в режиме выделения останавливает процесс на
первом выводе. Окно администратора открывается в `C:\Windows\system32`;
приглашение `PS C:\Users\...` значит окно без прав администратора — в нём
копию базы можно только читать. Каждый шаг, который запускает скрипт,
начинается с `cd C:\VehicleSoft_AgroWork`: скрипты ищутся от текущей папки.
Шаг можно начать и в новом окне.

---

## Шаг 0. Сервер видит api.agro-work.uz

```powershell
Test-NetConnection api.agro-work.uz -Port 443
```

**Ожидается:** `TcpTestSucceeded : True`.
**Прислать:** эту строку.

## Шаг 1. Отдельная копия кода

Адрес репозитория берётся из настроек рабочей копии прода — только чтение
настройки, в `C:\transport-report` ничего не меняется.

```powershell
$origin = git -C C:\transport-report config --get remote.origin.url
```

```powershell
git clone --branch claude/elegant-edison-zgmpbb $origin C:\VehicleSoft_AgroWork
```

```powershell
git -C C:\VehicleSoft_AgroWork --no-pager log --oneline -1
```

**Ожидается:** папка создана, последняя строка — последний коммит ветки.
**Прислать:** строку из `log`.

Если папка уже есть (повторный заход после исправлений) — вместо `clone`:

```powershell
git -C C:\VehicleSoft_AgroWork pull --ff-only
```

## Шаг 2. Самопроверка кода на сервере

Тесты поднимают свой сервер-подделку на 127.0.0.1 и временные базы; ни в
agro-work, ни в боевую базу они не ходят.

```powershell
cd C:\VehicleSoft_AgroWork
```

```powershell
& "C:\Program Files\Python314\python.exe" -m unittest tests.test_agro_work_client tests.test_agro_work_migration tests.test_agro_work_import tests.test_agro_work_links tests.test_agro_work_methods tests.test_agro_work_reconcile tests.test_agro_work_reconcile_tool tests.test_agro_work_copy_db
```

**Ожидается:** в конце `Ran 176 tests` и `OK`.
**Прислать:** две последние строки; если не `OK` — весь вывод. Дальше не
идти.

## Шаг 3. Файл учётных данных

Папка, доступная только администраторам, SYSTEM и вашей учётной записи
(группы указаны кодами SID — так команда одинакова на русской и английской
Windows):

```powershell
New-Item -ItemType Directory -Force -Path C:\VehicleSoft_Secrets | Out-Null
```

```powershell
icacls C:\VehicleSoft_Secrets /inheritance:r /grant:r "*S-1-5-32-544:(OI)(CI)F" "*S-1-5-18:(OI)(CI)F" "${env:USERDOMAIN}\${env:USERNAME}:(OI)(CI)F"
```

```powershell
notepad C:\VehicleSoft_Secrets\agro_work_credentials.txt
```

Блокнот спросит, создать ли файл, — «Да». Впишите **две строки**: в первой
`login=` и сразу за знаком равенства логин, которым вы входите на
agro-work.uz; во второй `password=` и пароль. Без пробелов вокруг `=` и без
кавычек. Сохраните (Ctrl+S) и закройте.

Проверка — содержимое не показывается:

```powershell
icacls C:\VehicleSoft_Secrets\agro_work_credentials.txt
```

```powershell
(Get-Content C:\VehicleSoft_Secrets\agro_work_credentials.txt | Measure-Object -Line).Lines
```

**Ожидается:** в `icacls` три строки доступа — администраторы, `NT
AUTHORITY\SYSTEM` и ваша учётная запись; число строк файла — `2`.
**Прислать:** вывод `icacls` и число. Сам файл не присылать никогда.

## Шаг 4. Вход и форма ответа — ничего не пишется

Один вход и пять GET. Печатаются только имена полей и счётчики.

```powershell
cd C:\VehicleSoft_AgroWork
```

```powershell
& "C:\Program Files\Python314\python.exe" tools\agro_work_import.py --credentials C:\VehicleSoft_Secrets\agro_work_credentials.txt --check
```

**Ожидается:** `login OK`, строка `applications: count=...` (около 4 800),
`ordering=created_at is ascending: yes`, `first row is storable: yes`,
`event is storable: yes`, в конце `check OK`.
**Прислать:** весь вывод — в нём нет значений, только имена полей и числа.

Если вход не прошёл — см. «Что делать, если» внизу.

## Шаг 5. Копия боевой базы

Онлайн-копия: служба продолжает работать, боевая база открывается только на
чтение. Инструмент откажется писать в любую папку `transport-report` и не
перезапишет уже сделанную копию.

```powershell
cd C:\VehicleSoft_AgroWork
```

```powershell
& "C:\Program Files\Python314\python.exe" tools\agro_work_copy_db.py --from C:\transport-report\instance\transport.db --to C:\VehicleSoft_AgroWork\instance\transport.db
```

**Ожидается:** `copied ... bytes`, `integrity of the copy: ok`.
**Прислать:** обе строки.

## Шаг 6. Миграция на копии

Дважды: второй прогон обязан ничего не сделать.

```powershell
cd C:\VehicleSoft_AgroWork
```

```powershell
& "C:\Program Files\Python314\python.exe" migrate_agro_work_001.py
```

```powershell
& "C:\Program Files\Python314\python.exe" migrate_agro_work_001.py
```

**Ожидается:** первый прогон —

```
Done. 7 agro_work tables (100 columns), 7 indexes and 4 triggers are in place.
```

второй — `Already applied. Nothing to do.`
**Прислать:** обе строки.

## Шаг 7. Первый импорт

Около 2 минут на обход 48 страниц заявок и около 10 минут на 300 историй
статусов (новейшие заявки первыми).

```powershell
cd C:\VehicleSoft_AgroWork
```

```powershell
& "C:\Program Files\Python314\python.exe" tools\agro_work_import.py --credentials C:\VehicleSoft_Secrets\agro_work_credentials.txt *> agro_import_1.log
```

```powershell
Get-Content agro_import_1.log -Tail 25
```

**Ожидается:** `agro-work import run 1: ok`; `applications: seen ... | new
...` — около 4 800 новых; `rejected 0`; `history: fetched 300 | failed 0 |
still pending ...`; строка `links:`; `unmatched machines: ..., written to
agro_work_unmatched.csv`.
**Прислать:** `C:\VehicleSoft_AgroWork\agro_import_1.log` и
`C:\VehicleSoft_AgroWork\agro_work_unmatched.csv` (госномера, марки,
предприятия — персональных полей в нём нет).

## Шаг 8. Второй импорт: повтор ничего не дублирует, история догружается

Тот же обход; истории — ещё до 1 000 штук (около 35 минут). Этого хватает,
чтобы история была у всех заявок с 20.08 — с тех пор есть факт GPS.
Остальное догрузится после релиза уже на боевой базе.

```powershell
cd C:\VehicleSoft_AgroWork
```

```powershell
& "C:\Program Files\Python314\python.exe" tools\agro_work_import.py --credentials C:\VehicleSoft_Secrets\agro_work_credentials.txt --max-history 1000 *> agro_import_2.log
```

```powershell
Get-Content agro_import_2.log -Tail 25
```

**Ожидается:** `new` — только заявки, созданные между прогонами; `updated`
— единицы; `rejected 0`; `history: fetched` до 1000 и `still pending`
меньше, чем в шаге 7.
**Прислать:** `agro_import_2.log`.

## Шаг 9. Ручные связки машин (вопрос 7)

`equipment_id` — номер машины в нашей программе, id строки справочника
«Техника». Не Wialon и не IMEI: связь нашей машины с трекером в программе
уже есть, и сверка находит трек через неё. На экране этот номер не виден:
колонка «№» в «Справочник → Техника» — порядковый номер строки, он меняется
от фильтра. Поэтому справочник сначала выгружается в файл, где id — первая
колонка (база не меняется):

```powershell
cd C:\VehicleSoft_AgroWork
```

```powershell
& "C:\Program Files\Python314\python.exe" tools\agro_work_links.py --equipment-csv agro_work_equipment.csv
```

Откройте в Excel `C:\VehicleSoft_AgroWork\agro_work_unmatched.csv` и
`C:\VehicleSoft_AgroWork\agro_work_equipment.csv`. Заполнять нужно строки,
у которых в колонке `applications` не ноль: файл отсортирован по ней. В
колонку `equipment_id` впишите id нашей машины — найдите её в
`agro_work_equipment.csv` через Ctrl+F по цифрам номера и сверьте название
и хозяйство. Колонка `candidates` — только подсказка: первое число в ней —
id, но машина может оказаться другой. 29.09 у тракторов Мирзачула `25 2xx HA`
подсказка указала на МТЗ другого хозяйства с номерами `80 2xx НА`, а
настоящие тракторы записаны у нас как `25 HA 290`. Не нашли или сомневаетесь
— оставьте пустым. Сохраните как CSV под тем же именем. Можно в несколько
заходов: уже записанные строки при повторе пропускаются.

План — ничего не пишет:

```powershell
& "C:\Program Files\Python314\python.exe" tools\agro_work_links.py --from-csv agro_work_unmatched.csv
```

**Ожидается:** строки `link   ... -> equipment ... (название / госномер /
хозяйство)` и `dry run: nothing was written`. Сверьте каждую строку глазами:
замки ловят несуществующий и уже занятый номер, но не опечатку в номер другой
существующей машины. Если `ERROR` — ничего не записано; прислать строку.

Запись:

```powershell
& "C:\Program Files\Python314\python.exe" tools\agro_work_links.py --from-csv agro_work_unmatched.csv --apply
```

**Ожидается:** `written: N link(s)` и строка `links now: ...`.
**Прислать:** вывод обеих команд и сохранённый `agro_work_unmatched.csv`.

## Шаг 10. Метод сверки видов работ (B2)

```powershell
cd C:\VehicleSoft_AgroWork
```

```powershell
& "C:\Program Files\Python314\python.exe" tools\agro_work_methods.py --export agro_work_methods.xlsx
```

Откройте `C:\VehicleSoft_AgroWork\agro_work_methods.xlsx`. В колонке «Метод
сверки» у каждого вида работ выберите из списка: гектары, время, рейсы или
не сверяется. Что сверка делает с каждым методом — на втором листе. Чего не
знаете — оставьте пустым. Сохраните.

План — ничего не пишет:

```powershell
& "C:\Program Files\Python314\python.exe" tools\agro_work_methods.py --import agro_work_methods.xlsx
```

Запись:

```powershell
& "C:\Program Files\Python314\python.exe" tools\agro_work_methods.py --import agro_work_methods.xlsx --apply
```

**Ожидается:** план `set ... | change 0 | clear 0 | same ...`, затем
`written: ...`.
**Прислать:** вывод обеих команд и сам `agro_work_methods.xlsx`.

## Шаг 11. Отчёт сверки и замер N (вопрос 4)

Сверка сентября в обе стороны и замер: сколько суток проходит от последнего
дня работы по GPS до отметки «Выполнено» на обычных заявках. По нему
предлагается N для заявок, заведённых задним числом.

```powershell
cd C:\VehicleSoft_AgroWork
```

```powershell
& "C:\Program Files\Python314\python.exe" tools\agro_work_reconcile.py --from 2026-09-01 --to 2026-09-27 *> agro_reconcile.log
```

```powershell
Get-Content agro_reconcile.log
```

**Ожидается:** строки `applications: ...`, `machine-days with GPS work:
...`, блок `completion lag` с медианой и долями для N = 0..7, в конце
`written agro_work_reconcile_2026-09-27.xlsx`.
**Прислать:** `agro_reconcile.log` и
`C:\VehicleSoft_AgroWork\agro_work_reconcile_2026-09-27.xlsx`.

## Шаг 12 (по желанию). Посмотреть экран сверки на копии

Второй экземпляр приложения из папки проверки, на копии базы, только для
этого компьютера (127.0.0.1, порт 5098). Боевая служба не затрагивается.

```powershell
cd C:\VehicleSoft_AgroWork
```

```powershell
$env:HOST = '127.0.0.1'
```

```powershell
$env:PORT = '5098'
```

```powershell
& "C:\Program Files\Python314\python.exe" run_server.py
```

Откройте в браузере на сервере `http://127.0.0.1:5098/agro-work/`, войдите
своей учётной записью. Остановить — Ctrl+C в окне PowerShell.

---

## Что сохранить до релиза

Копия базы — проверочная: после релиза миграция и импорт пойдут на боевой
базе, а ваши решения туда надо перенести. Храните:

- `C:\VehicleSoft_AgroWork\agro_work_unmatched.csv` с вписанными
  `equipment_id` — ручные связки (шаг 9);
- `C:\VehicleSoft_AgroWork\agro_work_methods.xlsx` — разметка методов
  (шаг 10).

После релиза они применяются той же парой команд `--from-csv ... --apply` и
`--import ... --apply`: ключи в них — идентификаторы agro-work, и на боевой
базе они те же.

## Что делать, если

| Сообщение | Что это | Что делать |
|---|---|---|
| `the database is read-only for this window` (прежде — трассировка `attempt to write a readonly database`) | окно PowerShell открыто не от имени администратора, приглашение `PS C:\Users\...` | открыть PowerShell от имени администратора, `cd C:\VehicleSoft_AgroWork`, повторить команду; ничего не записано |
| `can't open file 'C:\\Windows\\system32\\tools\\...'` | окно PowerShell открыто не в папке проверки | выполнить `cd C:\VehicleSoft_AgroWork` и повторить команду; ничего не записано |
| `credentials file not found` | инструмент не нашёл файл | проверить путь в шаге 3; прислать строку |
| `login failed: POST /auth/token/ -> 400 (keys: ...)` | сервер ждёт логин под другим именем поля — оно названо после `keys:` | в файл учётных данных третьей строкой `login_field=` и имя из сообщения, например `login_field=phone`; повторить шаг 4 |
| `login failed: ... -> 401 (detail: No active account ...)` | неверный логин или пароль | исправить в Блокноте, повторить шаг 4 |
| `GET /applications/ -> 401` после `login OK` | сервер ждёт другой тип заголовка | третьей строкой `auth_scheme=JWT`; повторить шаг 4; прислать вывод |
| `CHECK FOUND N PROBLEM(S)` | ответ agro-work не той формы, что изучена в B0 | прислать весь вывод; дальше не идти |
| `the run stopped: ... -> 503` или `-> 429` | их сервер занят | подождать час и повторить тот же шаг: всё прочитанное сохранено, прогон продолжит |
| `import run N is still running` | идёт другой прогон, или окно закрыли меньше 6 часов назад | дождаться; если окно закрыто давно — прислать строку |
| `tables missing ... run migrate_agro_work_001.py first` | пропущен шаг 6 | выполнить шаг 6 |
| `database not found` | нет копии базы | выполнить шаг 5 |
| `target already exists` в шаге 5 | копия уже есть, в ней может быть импорт | ничего не делать; копия нужна одна |
| `line N: equipment_id must be a number` в шаге 9 | в колонку `equipment_id` строки N попал текст | вписать число или очистить ячейку; повторить план |
| `our equipment N does not exist` в шаге 9 | такого id в нашем справочнике нет — опечатка | исправить по `agro_work_equipment.csv`; повторить план |
| `equipment N already belongs to agro-work machine ...` в шаге 9 | наша машина уже досталась другой машине agro-work (в скобках — id её карточки) | одно из двух решений неверно: оставить одно, у второй строки очистить `equipment_id` |

## Откат

На боевом сервере меняются только новые папки `C:\VehicleSoft_AgroWork` и
`C:\VehicleSoft_Secrets`. Боевая база, служба и `C:\transport-report` не
меняются ни одним шагом. Убрать всё — удалить обе папки вручную, когда
проверка закончена и решения из «Что сохранить до релиза» перенесены.
