# FIN-RECON-003 · ремонт разбора · результаты проверок

Код: коммиты `00b0311` и `1079d41` ветки `claude/fin-recon-003-parser-repair`
от базы `b763cf6` (= `origin/main` на 03.11.2026). Импортёр: блоб `28dc0d3` →
`53f1c2b`. Все прогоны ниже сделаны на `1079d41`.
Все прогоны — Windows-станция владельца, Python 3.14, Git Bash,
`PYTHONUTF8=1`. `%WORK%` — короткая временная папка вне репозитория.

## 1. Тесты затронутых пакетов

```
python -X utf8 tools/test_import_drone_works.py                 Ran 97 tests   OK
FIN_RECON_003_JULY_BOOKS=%WORK%\july\books \
python -X utf8 tools/test_import_drone_works_counterless.py -v  Ran 38 tests   OK
python -X utf8 tools/test_import_drone_works_counterless.py     Ran 38 tests   OK (skipped=5)   # без книг
python -X utf8 tools/test_import_drone_works_fin_recon_h5.py    Ran 8 tests    OK
python -X utf8 tools/test_import_drone_works_fuel_expense.py    Ran 5 tests    OK
python -X utf8 tools/test_drone_alias_decisions.py              Ran 26 tests   OK
python -X utf8 tools/test_drone_import_alias_candidates.py      Ran 6 tests    OK
python -X utf8 -m unittest <18 модулей tests.*>                  Ran 516 tests  FAILED (errors=1, skipped=1)
```

18 модулей: `test_drone_payer_unidentified`, `test_drone_cash_default_001`,
`test_drone_close_subdiv_001`, `test_drone_works_upload_002`,
`test_drone_works_screens`, `test_fin_recon_002_release_runbook`,
`test_drone_import_duplicates_001` и все одиннадцать `test_drone_works_*`.

Единственная ошибка —
`test_drone_works_sept2025_operator_link_001.OperatorLinkTest.test_the_reassignment_needs_its_own_rows`:
`sqlite3.OperationalError: near "LIMIT"`. Она воспроизведена на нетронутой
базе `b763cf6` (`git archive` в отдельную папку, тот же модуль: `Ran 19,
FAILED (errors=1)`); тест не импортирует импортёр. Это один из девяти
известных отказов этой станции.

Ни один существующий тест не удалён и не ослаблен: дифф трогает только
`tools/import_drone_works.py` и добавляет новый файл тестов.

## 2. Гейты CI

```
python -m compileall -q .              exit 0
python tools/check_templates.py        exit 0   Summary: 119 file(s) checked, 0 blocking finding(s), 0 warning(s).
python tools/test_check_templates.py   exit 0   Ran 7 tests  OK
```

## 3. Что покрывают новые тесты (38)

| Требование TASK.md | Тест |
|---|---|
| пустой счётчик у валидной работы | `test_blank_counter_on_a_valid_job_is_accepted_and_flagged` |
| пустой счётчик у итога | `test_blank_counter_on_a_totals_row_is_not_a_job` (+ «ИТОГО:» в колонке площади, «Жами га:» = итог листа) |
| продолжение после итога | `test_rows_after_a_totals_row_are_read_in_the_next_section` |
| повторная шапка | `test_a_repeated_header_remaps_the_columns` |
| блок зарплаты | `test_a_wage_block_is_never_a_job_with_or_without_a_number`, `test_the_wage_tail_and_the_junk_under_the_totals_are_not_jobs` |
| несколько разделов оплаты | `test_several_payment_sections_keep_their_own_type` |
| формулы без кэша | `test_a_formula_without_cache` |
| дубли | `test_the_mirror_is_skipped_whichever_side_lacks_the_counter`, `test_repeated_counters_keep_both_physical_rows`, `test_the_counter_is_not_part_of_the_identity` |
| ошибка схемы | `test_the_dry_run_reads_and_says_no`, `test_apply_still_refuses_with_the_old_text_and_code` |
| без молчаливой потери | `test_a_note_in_the_area_column_is_listed_not_blocking`, `test_farm_and_area_only_is_unparsed`, `test_a_numbered_row_without_an_area_is_unparsed`, `test_a_detail_sheet_without_a_recognised_header`, `test_every_non_empty_row_lands_in_exactly_one_class`, `test_apply_refuses_and_writes_nothing`, `test_the_upload_path_refuses_through_the_same_function` |
| «Когон Фукаро» и реальное лицо | `KogonFukaroTests` (6): удержание, алиас, «Ахмад (фукаро)» и «Когон Фукаро (Халимов Ж)» — обычные новые карточки, решение владельца только смоделировано, история не тронута |
| пять реальных книг | `RealJulyBooksTests` (5): 328 / 5144.66 / 775357370.99, 236 / 2887.73, 92 / 2256.93, Усмон 43 / 466.12, 19 ключей поимённо, UNPARSED 0, 22 из 22 итогов, одна удержанная строка, месяц по датам |

Отрицательные контроли внутри файла: старое правило снова теряет строки
(`test_negative_control_the_old_rule_loses_the_rows_again`); пустой
`PAYER_DECISION_PENDING_KEYS` снова создаёт карточку
(`test_negative_control_without_the_hold_the_card_is_made`).

## 4. Мутационная проверка: тесты различают верный и неверный код

Копия импортёра коммита `1079d41` с одной поломкой, тот же файл тестов,
книги подключены. Каждый мутант убит:

| Мутант | Что сломано | Результат |
|---|---|---|
| M1 | `apply_rows` не проверяет блокеры | FAILED (failures=4) |
| M2 | путь «строка без №» выключен | FAILED (failures=15, errors=4) |
| M3 | пустой список удержания плательщика | FAILED (failures=4) |
| M4 | молчаливый `continue` вместо UNPARSED | FAILED (failures=7, errors=1) |
| M5 | нет защиты от строки итога | FAILED (failures=9) |
| M6 | число в колонке хозяйства считается именем | FAILED (failures=1) |
| M7 | правило «заметка в колонке площади» убрано | FAILED (failures=1) |

Полный список упавших тестов каждого мутанта — `mutation.txt`.

## 5. Сухие прогоны на пяти книгах (изолированные базы)

Книги — копии из пакета, SHA-256 сверены (`SHA256SUMS.txt`). Манифест:
пять строк `<файл> 2026-07`.

| Прогон | База | Строк | Га | Сумма книги | Нал. | Перечисл. | Итог |
|---|---|---|---|---|---|---|---|
| исправленный код | синтетическая, полная схема | 328 | 5144.66 | 775357370.99 | 236 / 2887.73 | 92 / 2256.93 | APPLY_PERMITTED: NO — PAYER_DECISION_REQUIRED 1 |
| код продакшна `28dc0d3` | та же | 309 | 4955.41 | 739054756.50 | 218 / 2712.01 | 91 / 2243.40 | — |
| серверный прогон 03.11 (из пакета) | снимок 30.10 | 309 | 4955.41 | 739054756.50 | 218 / 2712.01 | 91 / 2243.40 | — |
| исправленный код | копия реальной старой базы без `payer_unidentified` | 328 | 5144.66 | 775357370.99 | — | — | SCHEMA_WARNING, APPLY_PERMITTED: NO |
| `--apply` исправленным кодом | та же копия | — | — | — | — | — | exit 2, `ERROR: drone_works.payer_unidentified is missing. …` |

Локальный прогон кода продакшна совпал с серверным прогоном 03.11 во всех
числах консоли, кроме сопоставления со справочником (на синтетической базе
справочника нет). SHA-256 обеих баз до и после прогонов совпадают
(синтетическая — после сухого прогона; копия старой базы
`0949f068…7f66` — после сухого прогона и отказа `--apply`).

Мост строк исправленного прогона: кандидатов 328 → разобрано 328 (без «№»
19, 189.25 га) → дублей 0, карантина 0 → принято 328, мост сходится.
Классы: job 309, job_no_number 19, wage 0, rejected 0, unparsed 0,
totals 31, header 19, block_marker 32, empty_numbered 54, no_job_data 3,
above_header 21.

## 6. Независимая сверка по ключу

`reconcile_july_328.py` читает книги через `zipfile` + `xml.etree`, без
`openpyxl` и без кода репозитория, по своему правилу (в нём нет счётчика).
Вход: CSV импортёра, реестр 328 из пакета, список 19 из пакета, прежний
реестр от 30.10.

```
keys, union of all registries : 328
xml reader rows               : 328  5144.66 ha  book amount 775357370.99
  xml cash     : 236 rows 2887.73 ha
  xml transfer : 92 rows 2256.93 ha
importer accepted             : 328  5144.66 ha  amount 775357370.99
registry 328                  : 328  5144.66 ha  charge 775357370.99
named 19                      : 19  189.25 ha
previous registry             : 328  5144.66 ha
exceptions                    : 0
```

По каждому ключу сверены площадь, тип оплаты, дата, написание хозяйства и
сумма (книга против импортёра; реестр против импортёра; начисление реестра
против суммы книги — допуск 0.005). Расхождений 0.

Отрицательный контроль: тот же сверщик на CSV без 19 строк (ровно то, что
принимал код продакшна, 309 / 4955.41) — exit 1:
`MISSING_IN_IMPORTER 19`, `NAMED_19_NOT_ACCEPTED_WITHOUT_NUMBER 19`.

Первый прогон сверщика дал 2 расхождения написания («Гоу  бобо» с двойным
пробелом): сверщик схлопывал пробелы, импортёр хранит написание как есть.
Исправлен сверщик (только края), не импортёр.

Прогоны этого раздела повторены на `1079d41`: числа те же, CSV импортёра
побайтно совпал с прогоном на `00b0311`.

## 7. Другие месяцы: настоящие книги

Отдельно от июля — 35 настоящих книг октября 2025 и марта–июня 2026 со
станции владельца (запрещённая книга с паролями исключена и не
открывалась). Обе версии импортёра читали одни и те же файлы на
синтетической базе, только чтение. Скрипт сравнения — во временной папке,
полный вывод с SHA-256 книг — `OTHER_MONTHS_COMPARE.txt`.

```
old 28dc0d3 : rows 1110  ha 18569.75  amount 2961097481.71
new 1079d41 : rows 1111  ha 18576.75  amount 2962497481.71
rows only in OLD                  : 0
rows only in NEW                  : 1   (E15)
common rows with a changed field  : 0   (14 полей)
duplicates 8 / 8, quarantined 1 / 1, generic payer 33 / 33
new UNPARSED_CANDIDATE            : 0
new rejections not in old         : 4   (E17)
book-total checks                 : 103, MISMATCH 1 (E16)
bridge balanced                   : true
apply gate                        : []
```

Первый прогон на `00b0311` дал `UNPARSED_CANDIDATE` 1: строка 22 книги
Пешку «Кеча топширган | 36191000» под итогом — заметка в колонке площади.
Она заблокировала бы запись книги, которая сегодня импортируется. Правило
уточнено коммитом `1079d41`; тест
`test_a_note_in_the_area_column_is_listed_not_blocking`; мутант M7 падает
ровно на нём.
