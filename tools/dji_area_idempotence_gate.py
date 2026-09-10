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
    (значит не проверено ничего).

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


def check(summary):
    """-> список причин отказа. Пустой список означает идемпотентность."""
    reasons = []
    flights = summary.get('flights')
    n_flights = len(flights) if isinstance(flights, list) else 0

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
    flights = summary.get('flights')
    log('flights in summary: %d'
        % (len(flights) if isinstance(flights, list) else 0))
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
