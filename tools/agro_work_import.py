# -*- coding: utf-8 -*-
"""agro-work B1 -- импорт заявок из API agro-work.uz в transport.db.

ЗАЧЕМ
Сверка заявок agro-work с фактом GPS (трек agro-work, B3) нужна владельцу в
обе стороны: была ли работа по заявке и была ли заявка на работу. Для этого
заявки нужны у нас -- с датой создания, датой закрытия из истории статусов и
машиной, связанной с нашей техникой.

ЧТО ДЕЛАЕТ ОДИН ПРОГОН
  1. виды работ и реестр машин;
  2. полный обход списка заявок: новые добавляются, изменившиеся обновляются
     с журналом по полям (`agro_work_changes`), повторная загрузка ничего не
     дублирует -- ключ заявки -- её id в agro-work;
  3. история статусов -- только закрытым заявкам, которые новы или сдвинули
     `updated_at`, не больше `--max-history` запросов, новейшие первыми;
  4. связь машин с нашей техникой по госномеру; несопоставленное -- в CSV
     `agro_work_unmatched.csv` для ручной связки
     (`tools/agro_work_links.py`).

ЧЕГО НЕ ДЕЛАЕТ
  Ничего не пишет в agro-work: клиент умеет только GET по четырём адресам и
      вход с обновлением токена, прочее отказывается до сети
      (`agro_work/client.py`).
  Не хранит персональных полей: имён и телефонов фермеров, водителей и
      операторов, названия хозяйства (ответ на вопрос 8), комментариев.
  Не трогает связки владельца и метод сверки видов работ.
  Ничего не удаляет: пропавшая из полной выгрузки заявка получает отметку.

УЧЁТНЫЕ ДАННЫЕ -- файл вне репозитория, строки `login=` и `password=`
(необязательные `login_field=` и `auth_scheme=`). Путь -- `--credentials`,
иначе переменная AGRO_WORK_CREDENTIALS, иначе `agro_work_credentials.txt` в
рабочей папке или в корне репозитория. Ни содержимое файла, ни токены не
печатаются никогда.

ПРОВЕРКА ДО ИМПОРТА: `--check` входит и делает пять GET, печатает только
имена полей и счётчики, базу не открывает.

Запуск (PowerShell, по одной команде на строку; полный порядок --
docs/AGRO_WORK_B1_RUNBOOK.md):

  & "C:\\Program Files\\Python314\\python.exe" tools\\agro_work_import.py --credentials C:\\VehicleSoft_Secrets\\agro_work_credentials.txt --check

  & "C:\\Program Files\\Python314\\python.exe" tools\\agro_work_import.py --credentials C:\\VehicleSoft_Secrets\\agro_work_credentials.txt

КОДЫ ВОЗВРАТА. 0 -- прогон дошёл до конца; 1 -- сбой сервера или сети
посреди прогона (всё до сбоя записано, следующий прогон продолжит); 2 -- не
запускался: нет базы, нет таблиц, нет или испорчен файл учётных данных;
3 -- вход не удался; 4 -- другой прогон импорта ещё идёт.

Вывод в консоль -- ASCII.
"""

import argparse
import csv
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agro_work import config, importer, records, store     # noqa: E402
from agro_work.client import (ApiError, AuthFailed, Client,  # noqa: E402
                              RefusedRequest)

CSV_COLUMNS = ('status', 'agro_transport_id', 'plate_number', 'brand',
               'model', 'category', 'company', 'in_registry', 'applications',
               'last_application_day', 'candidates', 'equipment_id')


def _keys(value):
    if isinstance(value, dict):
        return ', '.join(config.ascii_only(k) for k in sorted(value))
    return '(%s)' % type(value).__name__


def run_check(client, log=print):
    """Вход и пять GET. Только имена полей и счётчики; значения -- никогда.

    Возвращает число найденных проблем.
    """
    problems = 0
    client.login()
    log('login OK: login field "%s", scheme "%s", refresh token: %s'
        % (config.ascii_only(client.login_field),
           config.ascii_only(client.auth_scheme),
           'yes' if client.has_refresh_token else 'NO'))

    answer = client.get('/applications/', {'page': 1, 'page_size': 2,
                                           'ordering': 'created_at'})
    rows = answer.get('results') if isinstance(answer, dict) else None
    if not isinstance(rows, list) or not rows:
        log('PROBLEM: /applications/ has no results list')
        return problems + 1
    log('applications: count=%s, keys of a row: %s'
        % (answer.get('count'), _keys(rows[0])))
    for nested in ('transport_info', 'work_type_info', 'farm_info'):
        log('  %s keys: %s' % (nested, _keys(rows[0].get(nested))))
    moments = [records.parse_moment(row.get('created_at')) for row in rows]
    if any(m is None for m in moments):
        log('PROBLEM: created_at is missing or has no time zone')
        problems += 1
    else:
        log('  created_at has a time zone: yes (offset %s)'
            % moments[0].strftime('%z'))
        if len(moments) == 2:
            ascending = moments[0] <= moments[1]
            log('  ordering=created_at is ascending: %s'
                % ('yes' if ascending else 'NO'))
            if not ascending:
                problems += 1
    try:
        records.application_record(rows[0])
        log('  first row is storable: yes')
    except records.Rejected as exc:
        log('PROBLEM: first row is not storable: %s' % exc)
        problems += 1

    first_id = rows[0].get('id')
    if isinstance(first_id, str) and records.UUID_RE.match(first_id):
        events = client.history(first_id)
        log('history of the oldest application: %d event(s)' % len(events))
        if events:
            log('  keys of an event: %s' % _keys(events[0]))
            log('  changes shape: %s'
                % type(events[0].get('changes')).__name__)
            try:
                records.history_event(events[0])
                log('  event is storable: yes')
            except records.Rejected as exc:
                log('PROBLEM: event is not storable: %s' % exc)
                problems += 1
    else:
        log('PROBLEM: application id is not a UUID - history path unknown')
        problems += 1

    for path in ('/transports/', '/work-types/'):
        answer = client.get(path, {'page': 1, 'page_size': 1})
        rows = answer.get('results') if isinstance(answer, dict) else None
        if not isinstance(rows, list) or not rows:
            log('PROBLEM: %s has no results list' % path)
            problems += 1
            continue
        log('%s count=%s, keys of a row: %s'
            % (path.strip('/'), answer.get('count'), _keys(rows[0])))
    log('requests: %d | logins: %d | refreshes: %d'
        % (client.requests, client.logins, client.refreshes))
    log('nothing was written anywhere')
    return problems


def _equipment_labels(con):
    labels = {}
    for row in con.execute('SELECT id, name, plate FROM equipment'):
        labels[row[0]] = ('%s %s %s' % (row[0], row[1] or '', row[2] or '')).strip()
    return labels


def write_unmatched(con, path):
    """CSV несопоставленных машин: владелец вписывает equipment_id."""
    from agro_work import matching
    resolution = matching.resolve(store.transport_rows(con),
                                  store.equipment_rows(con),
                                  store.active_links(con))
    labels = _equipment_labels(con)
    rows = store.unmatched_transports(con)
    with open(path, 'w', encoding='utf-8-sig', newline='') as fh:
        writer = csv.writer(fh, delimiter=';')
        writer.writerow(CSV_COLUMNS)
        for row in rows:
            candidates = resolution.get(row['id'], {}).get('candidates') or []
            writer.writerow([
                row['match_status'], row['id'], row['plate_number'],
                row['brand_name'] or '', row['model'] or '',
                row['category_name'] or '', row['company_name'] or '',
                row['in_registry'], row['applications'], row['last_day'] or '',
                ' | '.join(labels.get(c, str(c)) for c in candidates), ''])
    return len(rows)


def print_summary(run_id, status, counters, detail, log=print):
    log('')
    log('agro-work import run %d: %s' % (run_id, status))
    log('applications: seen %d | new %d | updated %d | unchanged %d | '
        'rejected %d | duplicate %d | deleted in agro-work %d'
        % (counters['rows_seen'], counters['rows_new'], counters['rows_updated'],
           counters['rows_unchanged'], counters['rows_rejected'],
           counters['rows_duplicate'], counters['rows_gone']))
    log('api count: %s | pages: %d' % (counters.get('api_count'),
                                        counters['pages']))
    log('by status: %s' % ' | '.join(
        '%s %d' % (k, v) for k, v in sorted(detail.get('by_status', {}).items())))
    if detail.get('inactive'):
        log('is_active = false: %d (meaning of the flag unknown)'
            % detail['inactive'])
    log('history: fetched %d | failed %d | still pending %s'
        % (counters['history_fetched'], counters['history_failed'],
           counters.get('history_pending')))
    extra = detail.get('counters', {})
    log('work types: new %d, updated %d, unchanged %d, rejected %d'
        % (extra.get('work_types_new', 0), extra.get('work_types_updated', 0),
           extra.get('work_types_unchanged', 0),
           extra.get('work_types_rejected', 0)))
    log('transports: new %d, updated %d, unchanged %d, rejected %d, '
        'known only from applications %d'
        % (extra.get('transports_new', 0), extra.get('transports_updated', 0),
           extra.get('transports_unchanged', 0),
           extra.get('transports_rejected', 0),
           extra.get('transports_from_applications_only', 0)))
    links = detail.get('links', {})
    if links:
        log('links: manual %d | auto %d | ambiguous %d | none %d '
            '(unmatched with applications: %d)'
            % (links.get('manual', 0), links.get('auto', 0),
               links.get('ambiguous', 0), links.get('none', 0),
               links.get('unmatched_with_applications', 0)))
    for key in ('application_rejections', 'history_rejections',
                'transport_rejections', 'work_type_rejections'):
        for reason in detail.get(key, []):
            log('  %s: %s' % (key, config.ascii_only(reason)))
    log('requests: %d | logins: %d | refreshes: %d | abandoned: %d'
        % (counters['requests'], counters['logins'], counters['refreshes'],
           detail.get('abandoned', 0)))


def main(argv=None, client_factory=Client):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--db', default=config.DB_PATH)
    parser.add_argument('--credentials', default=None,
                        help='path to the credentials file (never printed)')
    parser.add_argument('--pause', type=float, default=config.PAUSE_S,
                        help='seconds between two requests (default 2)')
    parser.add_argument('--max-history', type=int,
                        default=config.MAX_HISTORY_PER_RUN,
                        help='status histories to fetch in this run '
                             '(default %d; 0 = none)'
                             % config.MAX_HISTORY_PER_RUN)
    parser.add_argument('--csv-out', default='agro_work_unmatched.csv')
    parser.add_argument('--check', action='store_true',
                        help='log in and read five pages; write nothing')
    args = parser.parse_args(argv)
    if args.max_history < 0:
        sys.stderr.write('ERROR: --max-history must not be negative\n')
        return 2
    if args.pause < 1.0:
        # [REASON]: пауза -- обещание владельцу и их серверу, а не ручка
        # скорости: чаще раза в секунду этот инструмент к ним не ходит.
        sys.stderr.write('ERROR: --pause below 1 second is refused\n')
        return 2

    try:
        credentials, used = config.read_credentials(args.credentials)
    except config.CredentialsError as exc:
        sys.stderr.write('ERROR: %s\n' % exc)
        return 2
    except OSError as exc:
        sys.stderr.write('ERROR: credentials file unreadable (%s)\n'
                         % type(exc).__name__)
        return 2
    print('credentials: %s' % config.ascii_only(used))
    client = client_factory(credentials, pause=args.pause)

    if args.check:
        try:
            problems = run_check(client)
        except AuthFailed as exc:
            sys.stderr.write('ERROR: login failed: %s\n' % exc)
            return 3
        except (ApiError, RefusedRequest) as exc:
            sys.stderr.write('ERROR: %s\n' % exc)
            return 1
        if problems:
            print('CHECK FOUND %d PROBLEM(S) - send this text back' % problems)
            return 1
        print('check OK')
        return 0

    if not os.path.exists(args.db):
        # [REASON]: sqlite3.connect создал бы пустую базу, и импорт молча
        # наполнил бы её вместо настоящей.
        sys.stderr.write('ERROR: database not found at %s - refusing to run\n'
                         % args.db)
        return 2
    con = store.connect(args.db)
    try:
        missing = store.missing_tables(con)
        if missing:
            sys.stderr.write('ERROR: tables missing: %s - run '
                             'migrate_agro_work_001.py first\n'
                             % ', '.join(missing))
            return 2
        try:
            run_id, status, counters, detail = importer.run_import(
                client, con, args.max_history)
        except store.AnotherRunActive as exc:
            sys.stderr.write('ERROR: import run %s is still running - wait '
                             'for it or for %d hours\n'
                             % (exc.args[0], store.STALE_RUNNING_HOURS))
            return 4
        except AuthFailed as exc:
            sys.stderr.write('ERROR: login failed: %s\n' % exc)
            return 3
        except sqlite3.OperationalError as exc:
            # Первая запись прогона -- его строка в журнале, до первого
            # запроса к agro-work: база только на чтение ломается на ней.
            if not store.is_readonly(exc):
                raise
            sys.stderr.write('ERROR: %s; nothing was requested from '
                             'agro-work\n' % store.READONLY_HINT)
            return 2
        except (ApiError, RefusedRequest) as exc:
            sys.stderr.write('ERROR: the run stopped: %s\n' % exc)
            sys.stderr.write('everything read before the stop is saved; the '
                             'next run continues\n')
            return 1
        print_summary(run_id, status, counters, detail)
        path = os.path.join(os.getcwd(), args.csv_out)
        count = write_unmatched(con, path)
        print('unmatched machines: %d, written to %s'
              % (count, config.ascii_only(args.csv_out)))
        return 0
    finally:
        con.close()


if __name__ == '__main__':
    sys.exit(main())
