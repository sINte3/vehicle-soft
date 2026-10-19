# -*- coding: utf-8 -*-
"""GPS-23 -- датчики зажигания объектов Wialon: обзор перед методом «время». Только чтение.

ЗАЧЕМ
Метод «время» сверки заявок (решение владельца 09.10: моточас -- время с
включённым зажиганием по трекеру; порог «работа была» -- от 30 минут)
стоит на одном факте: у машины в Wialon есть датчик зажигания и трекер
присылает его параметр. Сборщик GPS зажигание не сохраняет, и прежде чем
его учить, надо знать, у каких машин датчик есть, как он назван и на
каком параметре сидит. Это первый шаг метода «время» (трек GPS, §0.6):
обзор, без единой записи.

ЧТО ДЕЛАЕТ
Один запрос `core/search_items` по всем объектам с флагами
  0x0001  базовые свойства (имя);
  0x0400  последнее сообщение и позиция (параметры последнего сообщения);
  0x1000  датчики объекта;
  0x2000  счётчики (пробег, моточасы и флаги их расчёта `cfl`).
По каждому объекту: все датчики (тип и параметр), датчики зажигания
(тип IGNITION_TYPE), откуда Wialon считает моточасы (биты `cfl`: 0x10 --
датчик зажигания, 0x20 -- абсолютный датчик моточасов, 0x40 --
относительный, 0x200 -- автоматически по новым сообщениям), значение
счётчика моточасов как прислал сервер, время последнего сообщения и есть
ли параметр датчика зажигания среди параметров последнего сообщения. Затем
только чтение `transport.db`: машина программы, её категория и исключён
ли объект из план-факта (`gps.exclusion`, то же правило, что у расчёта).

Пишет CSV (UTF-8 с BOM, разделитель `;`) -- строка на объект: COLUMNS.
В консоль -- только числа ASCII: сколько объектов, сколько с датчиком
зажигания (по категориям техники), какие типы датчиков встречаются,
откуда считаются моточасы, у скольких параметр зажигания есть в последнем
сообщении, у скольких последнее сообщение старше 7 суток.

ЧЕГО НЕ ДЕЛАЕТ
  НИЧЕГО НЕ ПИШЕТ В БАЗУ. `transport.db` открывается `mode=ro`; базу,
      которой нет, не создаёт -- отказ с кодом 2.
  Ничего не меняет в Wialon. Сервисы только читающие: `token/login`,
      `core/search_items`, `core/logout`.
  Не решает, считать ли машину «работавшей», и не выбирает порог: правило
      (от 30 минут) -- решение владельца 09.10, оно войдёт в сверку «время».
  Не печатает имён в консоль: имена объектов и датчиков -- только в CSV.

ЧТО НЕ ПОДТВЕРЖДЕНО ДОКУМЕНТАЦИЕЙ ИЗ КОНТЕЙНЕРА -- ПРОВЕРИТЬ ПЕРВЫМ ПРОГОНОМ
Флаги 0x1000 (датчики), 0x2000 (счётчики) и ключи `sens`, `cfl`, `cneh`,
`lmsg` -- по таблице формата объекта Wialon Remote API
(https://sdk.wialon.com/wiki/en/sidebar/remoteapi/apiref/format/unit) и
странице `unit/update_calc_flags` (биты `cfl`). Сессия 19.10 саму страницу
открыть не смогла -- sdk.wialon.com и help.wialon.com закрыты политикой
выхода контейнера; значения подтверждены выдачей поиска, не чтением
страницы. Строка типа датчика зажигания «engine operation» -- по памяти
сессии. Поэтому консоль печатает гистограмму ВСЕХ типов датчиков: если
зажигание названо иначе, это видно из прогона, и правится одна строка
IGNITION_TYPE. Единица счётчика моточасов `cneh` в документах разных
версий -- часы или секунды: значение пишется в CSV как прислано, без
пересчёта. Ответ, в котором ни у одного объекта нет ни датчиков, ни
счётчиков, -- не «датчиков нет», а отказ с кодом 4 и списком ключей
ответа: флаги или имена свойств неверны.

КОДЫ ВЫХОДА
  0  готово, CSV записан;
  2  плохой ввод: нет базы или в ней нет таблиц сопоставления, нет папки
     для CSV, --out на месте базы, нет токена;
  3  Wialon: вход отклонён (печатается код ошибки), запрос не прошёл,
     объектов в ответе нет;
  4  Wialon ответил, но ни у одного объекта нет ни `sens`, ни `cfl`: флаги
     или имена свойств могут быть неверны. CSV НЕ пишется.

ТОЛЬКО STDLIB, системный Python: ни Flask, ни геостека.

Запуск (PowerShell, по одной команде на строку). Из C:\\transport-report --
там лежит wialon_token.txt, а `read_token` ищет его в текущем каталоге;
скрипт и CSV -- полными путями в клоне C:\\gps-tools:

  cd C:\\transport-report

  & "C:\\Program Files\\Python314\\python.exe" C:\\gps-tools\\tools\\gps_ignition_sensors.py --db C:\\transport-report\\instance\\transport.db --out C:\\gps-tools\\check\\ignition_sensors.csv

  $LASTEXITCODE
"""

import argparse
import csv
import os
import re
import sqlite3
import sys
import time

from collections import Counter
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gps import exclusion                                          # noqa: E402
from gps_collector import config                                   # noqa: E402
from gps_collector.wialon import (INPUT_PARAM, Client,            # noqa: E402
                                  WialonError, err_code, err_reason,
                                  ignition_value, login_failure)
from tools.gps_mark_not_ours import out_clash                      # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, 'instance', 'transport.db')

# [REASON]: флаги данных объекта -- см. докстринг «Что не подтверждено».
# Неверный флаг ошибки не даёт: сервер молча не присылает часть, поэтому
# ответ без датчиков и счётчиков у всех объектов -- код 4, а не CSV «датчиков
# нет».
FLAG_BASE = 0x0001
FLAG_LAST_MESSAGE = 0x0400
FLAG_SENSORS = 0x1000
FLAG_COUNTERS = 0x2000
FLAGS = FLAG_BASE | FLAG_LAST_MESSAGE | FLAG_SENSORS | FLAG_COUNTERS

# [REASON]: тип датчика зажигания Wialon -- по памяти сессии (докстринг).
# Гистограмма типов в консоли покажет настоящую строку первым же прогоном.
IGNITION_TYPE = 'engine operation'

# Биты `cfl`, источник моточасов (`unit/update_calc_flags`).
EH_SOURCES = ((0x10, 'datchik_zazhiganiya'), (0x20, 'absolyutnyy_datchik'),
              (0x40, 'otnositelnyy_datchik'))
EH_AUTO = 0x200

STALE_DAYS = 7

EXIT_OK = 0
EXIT_BAD_INPUT = 2
EXIT_WIALON = 3
EXIT_NO_SENSOR_DATA = 4

COLUMNS = ('wialon_id', 'obekt', 'mashina', 'gosnomer', 'kategoriya',
           'isklyuchen', 'datchik_zazhiganiya', 'datchiki_zazhiganiya',
           'parametry_zazhiganiya', 'vse_datchiki', 'motochasy_istochnik',
           'motochasy_avto', 'cfl', 'cneh_kak_prislano',
           'poslednee_soobshchenie', 'param_v_poslednem', 'znachenie_v_poslednem')

# Имя параметра в выражении датчика: «in1», «io_239», «can_eng*0.1» -> can_eng.
PARAM_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')


def open_readonly(path):
    """Соединение, которым нельзя писать: запрет SQLite, а не обещание."""
    return sqlite3.connect('file:%s?mode=ro' % path, uri=True, timeout=30)


def machines(con):
    """{wialon_id: (машина, госномер, категория)} по непомеченным строкам.

    None -- нет таблиц сопоставления (не та база). Объект с несколькими
    строками берёт первую непомеченную -- как отчёты GPS-19 и GPS-12.
    """
    present = {row[0] for row in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if not {'vialon_mappings', 'equipment'} <= present:
        return None
    out = {}
    for wialon_id, name, plate, category in con.execute(
            'SELECT m.wialon_id, e.name, e.plate, e.category '
            'FROM vialon_mappings m LEFT JOIN equipment e '
            'ON e.id = m.equipment_id '
            'WHERE m.wialon_id IS NOT NULL AND (m.skip IS NULL OR m.skip = 0) '
            'ORDER BY m.id'):
        out.setdefault(int(wialon_id), (name or '', plate or '', category or ''))
    return out


def search_units(client):
    """Объекты со всеми запрошенными частями. Один запрос."""
    answer = client.call('core/search_items', {
        'spec': {'itemsType': 'avl_unit', 'propName': 'sys_name',
                 'propValueMask': '*', 'sortType': 'sys_name'},
        'force': 1, 'flags': FLAGS, 'from': 0, 'to': 0})
    code = err_code(answer)
    if code is not None:
        raise WialonError(code, err_reason(answer))
    return [item for item in (answer.get('items') or []) if item.get('id')]


def sensors_of(item):
    """[(имя, тип, параметр)] датчиков объекта, по id датчика."""
    found = item.get('sens') or {}
    if isinstance(found, list):
        found = {str(i): value for i, value in enumerate(found)}
    out = []
    for key in sorted(found, key=lambda k: (len(str(k)), str(k))):
        sensor = found[key] or {}
        out.append((str(sensor.get('n') or ''), str(sensor.get('t') or ''),
                    str(sensor.get('p') or '')))
    return out


def param_names(expression):
    return [name for name in PARAM_NAME.findall(expression or '')
            if not name.isdigit()]


def last_value(last, name):
    """Значение параметра в последнем сообщении, или None.

    [REASON]: «in» и «inN» -- не ключи `p`, а цифровые входы Wialon в поле
    сообщения `i` (gps_collector.wialon.ignition_value). Прогон 19.10.2026
    искал их в `p` и назвал все 76 объектов с зажиганием на «in» объектами
    без параметра в последнем сообщении.
    """
    if INPUT_PARAM.match(name):
        value = ignition_value(last, name)
        return None if value is None else '%g' % value
    return (last.get('p') or {}).get(name)


def eh_source(cfl):
    """Источник моточасов по битам `cfl`; '' -- бит не стоит; None -- нет cfl."""
    if cfl is None:
        return None
    try:
        value = int(cfl)
    except (TypeError, ValueError):
        return None
    names = [name for bit, name in EH_SOURCES if value & bit]
    return '+'.join(names) if names else 'net'


def local_time(stamp):
    if not stamp:
        return ''
    try:
        return datetime.fromtimestamp(int(stamp), config.TZ).strftime(
            '%Y-%m-%d %H:%M')
    except (TypeError, ValueError, OSError):
        return ''


def build(items, known, excluded, now):
    """Строки CSV и сводка."""
    rows = []
    summary = {'units': len(items), 'with_ignition': 0, 'types': Counter(),
               'eh_sources': Counter(), 'eh_auto': 0, 'param_in_last': 0,
               'param_not_in_last': 0, 'stale': 0, 'never': 0,
               'by_category': Counter(), 'by_category_ignition': Counter(),
               'counted': 0, 'counted_ignition': 0}
    for item in items:
        unit_id = int(item['id'])
        sensors = sensors_of(item)
        ignition = [(n, p) for n, t, p in sensors if t == IGNITION_TYPE]
        for _n, kind, _p in sensors:
            summary['types'][kind or '(pusto)'] += 1
        cfl = item.get('cfl')
        source = eh_source(cfl)
        summary['eh_sources'][source if source is not None else '(net cfl)'] += 1
        auto = ''
        if source is not None:
            auto = 'da' if int(cfl) & EH_AUTO else 'net'
            summary['eh_auto'] += auto == 'da'
        last = item.get('lmsg') or {}
        last_t = last.get('t') or (item.get('pos') or {}).get('t')
        if not last_t:
            summary['never'] += 1
        elif now - int(last_t) > STALE_DAYS * 86400:
            summary['stale'] += 1
        names = []
        for _n, expression in ignition:
            names.extend(param_names(expression))
        present = [(name, value) for name in names
                   for value in (last_value(last, name),) if value is not None]
        if ignition:
            summary['with_ignition'] += 1
            if present:
                summary['param_in_last'] += 1
            else:
                summary['param_not_in_last'] += 1
        machine = known.get(unit_id)
        category = machine[2] if machine else ''
        why = excluded.get(unit_id, '')
        summary['by_category'][category or '(net mashiny)'] += 1
        if ignition:
            summary['by_category_ignition'][category or '(net mashiny)'] += 1
        if not why:
            summary['counted'] += 1
            summary['counted_ignition'] += bool(ignition)
        rows.append({
            'wialon_id': unit_id, 'obekt': item.get('nm') or '',
            'mashina': machine[0] if machine else '',
            'gosnomer': machine[1] if machine else '',
            'kategoriya': category, 'isklyuchen': why,
            'datchik_zazhiganiya': 'da' if ignition else 'net',
            'datchiki_zazhiganiya': ' | '.join(n for n, _p in ignition),
            'parametry_zazhiganiya': ' | '.join(p for _n, p in ignition),
            'vse_datchiki': ' | '.join('%s: %s' % (t, p) for _n, t, p in sensors),
            'motochasy_istochnik': source if source is not None else '',
            'motochasy_avto': auto,
            'cfl': '' if cfl is None else cfl,
            'cneh_kak_prislano': '' if item.get('cneh') is None else item.get('cneh'),
            'poslednee_soobshchenie': local_time(last_t),
            'param_v_poslednem': ('da' if present else 'net') if ignition else '',
            'znachenie_v_poslednem': ' | '.join(
                '%s=%s' % (name, value) for name, value in present)})
    rows.sort(key=lambda r: (r['datchik_zazhiganiya'] != 'net',
                             r['kategoriya'], r['wialon_id']))
    return rows, summary


def no_sensor_data(items):
    """Ни у одного объекта нет ни `sens`, ни `cfl` -- флаги или ключи неверны."""
    return not any(('sens' in item) or ('cfl' in item) for item in items)


def answer_keys(items):
    keys = Counter()
    for item in items:
        keys.update(item.keys())
    return ', '.join('%s:%d' % pair for pair in sorted(keys.items()))


def write_csv(path, rows):
    with open(path, 'w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, delimiter=';')
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def report(summary, log=print):
    log('objects in Wialon            : %d' % summary['units'])
    log('  with an ignition sensor    : %d (type %r)'
        % (summary['with_ignition'], IGNITION_TYPE))
    log('  counted by plan-fact       : %d, of them with ignition: %d'
        % (summary['counted'], summary['counted_ignition']))
    log('  ignition parameter in the last message: %d, not in it: %d'
        % (summary['param_in_last'], summary['param_not_in_last']))
    log('  last message older than %d days: %d, never reported: %d'
        % (STALE_DAYS, summary['stale'], summary['never']))
    log('by category (objects / with ignition):')
    for category in sorted(summary['by_category']):
        log('  %-16s : %5d / %5d' % (config.ascii_only(category),
                                     summary['by_category'][category],
                                     summary['by_category_ignition'][category]))
    log('sensor types (sensors):')
    for kind, count in summary['types'].most_common():
        log('  %-30s : %d' % (config.ascii_only(kind)[:30], count))
    log('engine hours source (objects, cfl bits):')
    for source, count in summary['eh_sources'].most_common():
        log('  %-30s : %d' % (source, count))
    log('  automatic engine hours from new messages: %d' % summary['eh_auto'])


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=config.ascii_only(__doc__.splitlines()[0]))
    parser.add_argument('--db', default=DB_PATH)
    parser.add_argument('--pause', type=float, default=config.PAUSE_S)
    parser.add_argument('--out', default='gps_ignition_sensors.csv')
    args = parser.parse_args(argv)

    if not os.path.isdir(os.path.dirname(os.path.abspath(args.out))):
        sys.stderr.write('ERROR: folder for %s does not exist\n'
                         % config.ascii_only(args.out))
        return EXIT_BAD_INPUT
    # [REASON]: CSV пишется режимом 'w'; путь базы на месте CSV заменил бы
    # боевую базу таблицей -- правило то же, что у GPS-16 и GPS-18.
    clash = out_clash(args.out, (args.db, args.db + '-wal', args.db + '-shm'))
    if clash:
        sys.stderr.write('ERROR: --out points at %s - refusing to overwrite '
                         'it\n' % config.ascii_only(clash))
        return EXIT_BAD_INPUT
    if not os.path.isfile(args.db):
        sys.stderr.write('ERROR: database not found at %s - refusing to run\n'
                         % config.ascii_only(args.db))
        return EXIT_BAD_INPUT
    try:
        con = open_readonly(args.db)
        try:
            known = machines(con)
            excluded = exclusion.excluded_units(con) if known is not None else {}
        finally:
            con.close()
    except sqlite3.DatabaseError as problem:
        sys.stderr.write('ERROR: cannot read %s (%s)\n'
                         % (config.ascii_only(args.db), type(problem).__name__))
        return EXIT_BAD_INPUT
    if known is None:
        sys.stderr.write('ERROR: tables vialon_mappings / equipment are '
                         'missing - wrong --db?\n')
        return EXIT_BAD_INPUT

    token = config.read_token()
    client = Client(pause=args.pause)
    try:
        client.login(token)
    except Exception as problem:                                   # noqa: BLE001
        sys.stderr.write('\nERROR: ne udalos voyti na %s (%s)\n'
                         % (config.BASE_URL, login_failure(problem)))
        return EXIT_WIALON
    print('login OK')
    try:
        try:
            items = search_units(client)
        except Exception as problem:                               # noqa: BLE001
            # [REASON]: Client.call поднимает и ошибку Wialon, и сетевой сбой
            # после повторов; оба -- «запрос не прошёл», код 3, CSV нет.
            print('FAILED: core/search_items (%s: %s)'
                  % (type(problem).__name__, config.ascii_only(problem)))
            return EXIT_WIALON
        if not items:
            print('FAILED: no objects in the answer - NOT treating this as a '
                  'fleet without objects')
            return EXIT_WIALON
        if no_sensor_data(items):
            print('REFUSED: %d object(s) came back, none with sensors (sens) '
                  'or counters (cfl) - the flags or key names may be wrong. '
                  'Keys in the answer: %s' % (len(items), answer_keys(items)))
            print('nothing was written')
            return EXIT_NO_SENSOR_DATA
    finally:
        client.logout()
    rows, summary = build(items, known, excluded, int(time.time()))
    write_csv(args.out, rows)
    report(summary)
    print('')
    print('READ ONLY: nothing was written to the database or to Wialon')
    print('csv: %s' % config.ascii_only(args.out))
    return EXIT_OK


if __name__ == '__main__':
    sys.exit(main())
