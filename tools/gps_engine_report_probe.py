# -*- coding: utf-8 -*-
"""GPS-27 -- зонд отчёта «Моточасы» Wialon: эталон часов за прошлые месяцы. Только чтение.

ЗАЧЕМ
Правило часов метода «время» (правило R, `gps/ignition.py`) проверяется
против моточасов, которые Wialon считает сам по тому же датчику зажигания.
Снимки счётчика сборщик снимает только с GPS-25, а Wialon хранит сообщения
три месяца и умеет посчитать моточасы за любой прошедший день -- это его
отчёт «Моточасы». Если отчёт выполняется через API, правило можно проверить
на сентябре и октябре, не дожидаясь недели ночей.

Формат отчёта в API из контейнера сессии не прочитан (sdk.wialon.com и
help.wialon.com закрыты политикой выхода). Этот зонд узнаёт его у самого
сервера и больше ничего не делает:
  1. `report/get_report_tables` -- какие типы таблиц отчётов знает сервер и
     какие у таблицы моточасов колонки;
  2. `core/search_items` по ресурсам с флагом шаблонов отчётов (0x2000) --
     какие шаблоны уже есть;
  3. `report/get_report_data` -- определения шаблонов; ищутся таблицы
     моточасов;
  4. если такой шаблон есть -- `report/exec_report` на одном объекте за одни
     сутки, `report/get_result_rows`, `report/cleanup_result`; если нет --
     то же с шаблоном, переданным в самом запросе (`reportTemplateId` = 0):
     такой шаблон в Wialon не сохраняется.
Всё, что сервер ответил, -- в JSON (--out). В консоль -- только числа и
имена типов таблиц транслитом.

ЧЕГО НЕ ДЕЛАЕТ
  НИЧЕГО НЕ ПИШЕТ В WIALON: шаблоны не создаются и не меняются; результат
      отчёта живёт в сессии и освобождается `report/cleanup_result`.
  Не сравнивает отчёт с нашим счётом: сравнение -- отдельный прогон после
      записанной заранее предрегистрации.
  В базу не ходит.

КОДЫ ВЫХОДА
  0  JSON записан (что бы сервер ни ответил -- ответ и есть результат);
  2  плохой ввод: нет папки для JSON, день не YYYY-MM-DD, нет токена;
  3  Wialon: вход отклонён или запрос не прошёл.

ТОЛЬКО STDLIB, системный Python.

Запуск (PowerShell, по одной команде на строку). Из C:\\transport-report --
там лежит wialon_token.txt:
  cd C:\\transport-report
  & "C:\\Program Files\\Python314\\python.exe" C:\\gps-tools\\tools\\gps_engine_report_probe.py --out C:\\gps-tools\\check\\engine_report_probe.json
  $LASTEXITCODE
"""

import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gps_collector import config                                   # noqa: E402
from gps_collector.wialon import (Client, err_code, err_reason,   # noqa: E402
                                  login_failure)

EXIT_OK = 0
EXIT_BAD_INPUT = 2
EXIT_WIALON = 3

# [REASON]: флаг шаблонов отчётов у ресурса -- по памяти сессии, документация
# из контейнера закрыта. Если он неверен, у ресурсов не будет ключа `rep`, и
# зонд скажет это прямо, с ключами ответа, а не «шаблонов нет».
FLAG_RESOURCE_BASE = 0x0001
FLAG_RESOURCE_REPORTS = 0x2000
ENGINE_MARK = 'engine_hours'

# Объект и сутки по умолчанию: трактор МТЗ из считаемых план-фактом, датчик
# зажигания io_239, моточасы Wialon по датчику и автоматически (GPS-23).
DEFAULT_UNIT = 923
DEFAULT_DAY = '2026-10-15'

READ_ONLY_SERVICES = ('token/login', 'report/get_report_tables',
                      'core/search_items', 'report/get_report_data',
                      'report/exec_report', 'report/get_result_rows',
                      'report/cleanup_result', 'core/logout')


def is_engine(name):
    return ENGINE_MARK in str(name or '')


def ask(client, svc, params, found):
    """One call; the raw answer is kept in `found` under the service name."""
    answer = client.call(svc, params)
    found.setdefault('answers', []).append({'svc': svc, 'params': params,
                                            'answer': answer})
    return answer


def table_types(client, found):
    answer = ask(client, 'report/get_report_tables', {}, found)
    if err_code(answer) is not None or not isinstance(answer, list):
        return []
    return answer


def report_resources(client, found):
    answer = ask(client, 'core/search_items', {
        'spec': {'itemsType': 'avl_resource', 'propName': 'sys_name',
                 'propValueMask': '*', 'sortType': 'sys_name'},
        'force': 1, 'flags': FLAG_RESOURCE_BASE | FLAG_RESOURCE_REPORTS,
        'from': 0, 'to': 0}, found)
    if err_code(answer) is not None:
        return None
    return [item for item in (answer.get('items') or []) if item.get('id')]


def template_ids(item):
    reports = item.get('rep') or {}
    if isinstance(reports, list):
        return sorted(int(r['id']) for r in reports if r.get('id') is not None)
    return sorted(int(key) for key in reports)


def engine_templates(client, resources, found):
    """[(resource_id, template)] whose tables include engine hours."""
    out = []
    for item in resources:
        ids = template_ids(item)
        if not ids:
            continue
        answer = ask(client, 'report/get_report_data',
                     {'itemId': int(item['id']), 'col': ids, 'flags': 0}, found)
        if err_code(answer) is not None:
            continue
        for template in (answer if isinstance(answer, list) else []):
            tables = template.get('tbl') or []
            if template.get('ct') == 'avl_unit' and any(
                    is_engine(t.get('n')) for t in tables):
                out.append((int(item['id']), template))
    return out


def inline_template(types):
    """A template passed in the request itself: one engine-hours table, all
    of its columns as the server lists them."""
    kind = next((t for t in types if is_engine(t.get('n'))), None)
    name = kind.get('n') if kind else 'unit_engine_hours'
    columns = [c for c in (kind.get('c') or []) if isinstance(c, dict)] if kind else []
    return {'id': 0, 'n': 'gps-27 probe', 'ct': 'avl_unit', 'p': '',
            'tbl': [{'n': name, 'l': 'Engine hours',
                     'c': ','.join(str(c.get('n', '')) for c in columns),
                     'cl': ','.join(str(c.get('l', '')) for c in columns),
                     's': '', 'sl': '', 'p': '',
                     'sch': {'f1': 0, 'f2': 0, 't1': 0, 't2': 0, 'm': 0,
                             'y': 0, 'w': 0, 'fl': 0},
                     'f': 0}]}


def run_report(client, resource_id, template_id, unit_id, t_from, t_to, found,
               template=None):
    params = {'reportResourceId': resource_id, 'reportTemplateId': template_id,
              'reportObjectId': unit_id, 'reportObjectSecId': 0,
              'interval': {'from': t_from, 'to': t_to, 'flags': 0}}
    if template is not None:
        params['reportTemplate'] = template
    result = {'template_id': template_id, 'inline': template is not None}
    try:
        answer = ask(client, 'report/exec_report', params, found)
        code = err_code(answer)
        if code is not None:
            result['error'] = code
            result['reason'] = err_reason(answer)
            return result
        tables = ((answer.get('reportResult') or {}).get('tables') or [])
        result['tables'] = []
        for index, table in enumerate(tables):
            rows = ask(client, 'report/get_result_rows',
                       {'tableIndex': index, 'indexFrom': 0,
                        'indexTo': int(table.get('rows') or 0)}, found)
            result['tables'].append({'name': table.get('name'),
                                     'header': table.get('header'),
                                     'total': table.get('total'),
                                     'rows': rows})
        return result
    finally:
        # [REASON]: результат отчёта держит память сервера до конца сессии;
        # освобождаем его сразу, как `messages/unload` после загрузки.
        try:
            client.call('report/cleanup_result', {})
        except Exception:                                          # noqa: BLE001
            pass


def summary_lines(found):
    lines = []
    types = found.get('table_types') or []
    engine = [t for t in types if is_engine(t.get('n'))]
    lines.append('table types known to the server: %d, engine hours: %s'
                 % (len(types), ', '.join(config.ascii_only(t.get('n'))
                                          for t in engine) or 'none'))
    for kind in engine:
        lines.append('  columns of %s: %d' % (config.ascii_only(kind.get('n')),
                                              len(kind.get('c') or [])))
    resources = found.get('resources')
    if resources is None:
        lines.append('resources: the search failed')
    else:
        with_rep = [r for r in resources if 'rep' in r]
        lines.append('resources: %d, with report templates (key rep): %d, '
                     'templates: %d'
                     % (len(resources), len(with_rep),
                        sum(len(template_ids(r)) for r in resources)))
        if resources and not with_rep:
            lines.append('  NO key rep in any resource - flag 0x2000 may be '
                         'wrong; keys seen: %s'
                         % ', '.join(sorted({k for r in resources for k in r})))
    lines.append('templates with an engine-hours table: %d'
                 % len(found.get('engine_templates') or []))
    for run in found.get('runs') or []:
        head = 'run template %s (%s)' % (run['template_id'],
                                          'inline' if run['inline'] else 'saved')
        if 'error' in run:
            lines.append('%s: error %s' % (head, run['error']))
            continue
        for table in run.get('tables') or []:
            rows = table.get('rows')
            lines.append('%s: table %s, rows %d, total %s'
                         % (head, config.ascii_only(table.get('name')),
                            len(rows) if isinstance(rows, list) else -1,
                            config.ascii_only(json.dumps(table.get('total'),
                                                         ensure_ascii=False))))
    return lines


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=config.ascii_only(__doc__.splitlines()[0]))
    parser.add_argument('--out', required=True, help='JSON to write')
    parser.add_argument('--unit', type=int, default=DEFAULT_UNIT,
                        help='wialon id of the object to run the report on')
    parser.add_argument('--day', default=DEFAULT_DAY,
                        help='local day to run the report for, YYYY-MM-DD')
    parser.add_argument('--pause', type=float, default=config.PAUSE_S)
    args = parser.parse_args(argv)

    if not os.path.isdir(os.path.dirname(os.path.abspath(args.out))) \
            or os.path.isdir(args.out):
        sys.stderr.write('ERROR: cannot write %s\n' % config.ascii_only(args.out))
        return EXIT_BAD_INPUT
    try:
        t_from = int(datetime.strptime(args.day, '%Y-%m-%d')
                     .replace(tzinfo=config.TZ).timestamp())
    except ValueError:
        sys.stderr.write('ERROR: --day is YYYY-MM-DD\n')
        return EXIT_BAD_INPUT
    t_to = t_from + 86399

    token = config.read_token()
    client = Client(pause=args.pause)
    try:
        client.login(token)
    except Exception as problem:                                   # noqa: BLE001
        sys.stderr.write('\nERROR: ne udalos voyti na %s (%s)\n'
                         % (config.BASE_URL, login_failure(problem)))
        return EXIT_WIALON
    print('login OK | object %d | day %s' % (args.unit, args.day))
    found = {'unit': args.unit, 'day': args.day, 'from': t_from, 'to': t_to}
    try:
        found['table_types'] = table_types(client, found)
        resources = report_resources(client, found)
        found['resources'] = None if resources is None else [
            {k: v for k, v in r.items() if k in ('id', 'nm', 'rep')}
            for r in resources]
        found['engine_templates'] = [] if resources is None else [
            {'resource': res, 'template': tpl}
            for res, tpl in engine_templates(client, resources, found)]
        found['runs'] = []
        if found['engine_templates']:
            first = found['engine_templates'][0]
            found['runs'].append(run_report(
                client, first['resource'], int(first['template']['id']),
                args.unit, t_from, t_to, found))
        resource = (found['engine_templates'][0]['resource']
                    if found['engine_templates'] else config.ZONE_RESOURCE_ID)
        found['runs'].append(run_report(
            client, resource, 0, args.unit, t_from, t_to, found,
            template=inline_template(found['table_types'])))
    except Exception as problem:                                   # noqa: BLE001
        found['failure'] = '%s: %s' % (type(problem).__name__, problem)
        print('FAILED: %s' % config.ascii_only(found['failure']))
        with open(args.out, 'w', encoding='utf-8') as handle:
            json.dump(found, handle, ensure_ascii=False, indent=1)
        return EXIT_WIALON
    finally:
        client.logout()
    with open(args.out, 'w', encoding='utf-8') as handle:
        json.dump(found, handle, ensure_ascii=False, indent=1)
    for line in summary_lines(found):
        print(line)
    print('')
    print('READ ONLY: no template was created or changed in Wialon')
    print('json: %s' % config.ascii_only(args.out))
    return EXIT_OK


if __name__ == '__main__':
    sys.exit(main())
