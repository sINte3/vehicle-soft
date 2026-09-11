# -*- coding: utf-8 -*-
"""tools/dji_area_idempotence_gate.py -- ворота идемпотентности пересчёта.

Второй `--apply` того же входа обязан не написать НИ ОДНОЙ новой строки. До
сих пор это проверял человек, читая консоль; надпись «MUST REPORT unchanged»
проверкой не является -- её можно не заметить, а вывод пролистать.

Скрипт читает сводку второго прогона (`--json` от `dji_area_recalc.py`) и
ОСТАНАВЛИВАЕТ работу кодом 1, если:

  * в `calc_writes` есть ненулевое состояние, отличное от `unchanged`
    (`new`, `reactivated` -- значит вход изменился между прогонами);
  * то же в `field_writes`;
  * записи в периоде есть, а `unchanged` отсутствует или равен нулю
    (значит не проверено ничего);
  * поле `flights_in_period` отсутствует или не является целым
    неотрицательным числом (значит это не сводка пересчёта).

[REASON]: число обработанных записей берётся из `flights_in_period`, а НЕ из
списка `flights`. Списка в настоящем файле нет: `dji_area_recalc.py:169`
делает `summary.pop('flights', [])` ДО `json.dump(summary, ...)`, а
`pipeline.py:537` наполняет его только при `collect_rows`, то есть лишь
когда передан `--rows`. Пока ворота считали по `flights`, `n_flights` всегда
выходил 0, и контроль «непустой период без `unchanged`» был мёртвым: сводка
с пустыми `calc_writes` на 226 вылетах получала `IDEMPOTENCE CONFIRMED`.
`flights_in_period` = `len(targets)` (`pipeline.py:345`), а цикл записи идёт
ровно по `targets` без единого `continue`, поэтому это число целей записи.
`flights_loaded` включает краевые сутки и для этой цели велико.

[REASON]: гейт живёт отдельным файлом, а не строкой PowerShell в ранбуке, по
двум причинам. Разбор JSON в PowerShell 5.1 идёт через
`PSObject.Properties`, и трек уже ловил на этом реальный баг с
`OrderedDictionary` (PR #115). И код внутри markdown не покрывается тестами
вовсе, а этот файл покрыт положительным и отрицательными контролями.

ЗАПУСК

  & "C:\\Program Files\\Python314\\python.exe" tools\\dji_area_idempotence_gate.py --summary "C:\\path\\apply2.json"

КОДЫ ВОЗВРАТА
  0  идемпотентность подтверждена
  1  НЕ подтверждена, причина названа
  2  файл сводки не найден или не разбирается

ОТКАТ
  Кода: удалить файл, его никто не импортирует.
  Данных: не производит записей вовсе.
"""

import argparse
import json
import os
import sys

GATE_ID = 'DJI_AREA_IDEMPOTENCE_GATE_001'
ALLOWED_STATE = 'unchanged'
WRITE_KEYS = ('calc_writes', 'field_writes')
COUNT_KEY = 'flights_in_period'


def log(msg):
    """Консоль -- только ASCII (правило проекта)."""
    sys.stdout.write(msg + '\n')
    sys.stdout.flush()


def load(path):
    if not os.path.isfile(path):
        log('ERROR: summary not found: %s' % path)
        sys.exit(2)
    try:
        with open(path, encoding='utf-8') as fh:
            return json.load(fh)
    except (ValueError, UnicodeDecodeError) as exc:
        log('ERROR: summary is not readable JSON: %s' % type(exc).__name__)
        sys.exit(2)


def flights_in_period(summary):
    """-> (число целей, причина отказа). Ровно одно из двух непусто."""
    if COUNT_KEY not in summary:
        return None, ('%s is missing from the summary '
                      '(not a recalc summary)' % COUNT_KEY)
    value = summary[COUNT_KEY]
    # [REASON]: `bool` -- подкласс `int`, и `True` прошёл бы как единица.
    # Источник значения -- `len(targets)`, то есть всегда целое; всё
    # остальное означает чужой или испорченный файл, а не пустой период.
    if isinstance(value, bool) or not isinstance(value, int):
        return None, ('%s is %r, expected a whole number'
                      % (COUNT_KEY, value))
    if value < 0:
        return None, '%s is %d, expected a non-negative number' % (COUNT_KEY,
                                                                   value)
    return value, None


def check(summary):
    """-> список причин отказа. Пустой список означает идемпотентность."""
    reasons = []
    n_flights, count_reason = flights_in_period(summary)
    if count_reason:
        # [REASON]: без достоверного числа целей контроль «непустой период
        # без unchanged» не выполним. Молча считать период пустым значило бы
        # вернуть ровно тот мёртвый контроль, ради которого ворота и писались.
        return [count_reason]

    for key in WRITE_KEYS:
        writes = summary.get(key)
        if not isinstance(writes, dict):
            reasons.append('%s is missing from the summary' % key)
            continue
        for state, count in sorted(writes.items()):
            if not isinstance(count, (int, float)) or isinstance(count, bool):
                reasons.append('%s.%s is not a number' % (key, state))
                continue
            if state != ALLOWED_STATE and count:
                reasons.append('%s.%s = %s (a second apply must write nothing)'
                               % (key, state, count))
        # [REASON]: пустой `calc_writes` при наличии записей означает, что
        # прогон не дошёл до сравнения. Это НЕ идемпотентность, это отсутствие
        # проверки, и по коду возврата 0 их не различить.
        if n_flights and not writes.get(ALLOWED_STATE):
            reasons.append('%s has no %s while %d flight(s) were processed'
                           % (key, ALLOWED_STATE, n_flights))
    return reasons


def main():
    ap = argparse.ArgumentParser(description=GATE_ID)
    ap.add_argument('--summary', required=True,
                    help='JSON summary of the SECOND --apply run')
    args = ap.parse_args()

    log(GATE_ID)
    log('summary: %s' % args.summary)
    summary = load(args.summary)
    log('%s: %r' % (COUNT_KEY, summary.get(COUNT_KEY)))
    for key in WRITE_KEYS:
        log('  %s: %s' % (key, json.dumps(summary.get(key), sort_keys=True)))

    reasons = check(summary)
    if reasons:
        log('IDEMPOTENCE NOT CONFIRMED:')
        for reason in reasons:
            log('  - %s' % reason)
        return 1
    log('IDEMPOTENCE CONFIRMED: the second apply wrote nothing new')
    return 0


if __name__ == '__main__':
    sys.exit(main())
