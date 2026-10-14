# -*- coding: utf-8 -*-
"""GPS-18 -- группы геозон ресурса Wialon: где территории подразделений.

ЗАЧЕМ
Владелец 14.10: тракторы МТЗ ездят внутри «геозон подразделений организации,
заводы, пункты, автобазы, гаражи итд. Все они имеются в Виалоне». Участок на
такой территории -- не гектары поля, и правило об этом (§2.14, «Пересмотр
14.10», п. 6) пишется до включения. Для правила надо знать, КАКИЕ из 17 812
геозон ресурса 285 «Бухоро агрокластер» -- территории. Имя зоны этого не
говорит: рядом с гаражами в справочнике лежат поля фермеров, а угадывание по
слову в имени -- это бизнес-правило, которого никто не утверждал. Если в
Wialon зоны собраны в группы («Автобазы», «Гаражи»...), группа и есть ответ.
Инструмент показывает, какие группы есть и что в них.

ЧТО ДЕЛАЕТ
Один запрос `core/search_items` за ресурсами: у нужного -- список зон и группы
зон с id входящих в них зон. Затем `resource/get_zone_data` -- имя и тип
каждой зоны: тот же вызов и те же флаги, что у зеркала GPS-4
(`tools/gps_mirror_contours.py`), проверенные на всех 17 812 зонах. Затем
только чтение `transport.db`: есть ли зона в нашем зеркале `field_contours`
(`source='wialon'`, `external_id` -- id зоны).

Пишет два CSV (UTF-8 с BOM, разделитель `;` -- как у GPS-12):
  --groups-out  строка на группу: id, имя, описание, сколько в ней зон, сколько
                из них в зеркале, id зон группы, которых нет в списке зон
                ресурса, и id всех зон группы;
  --zones-out   строка на зону ресурса: id, имя, тип, сколько групп, имена и
                id групп, в которых она состоит, есть ли она в зеркале.
                Сначала зоны в группах, в конце -- зоны ни в одной группе.
В консоль -- сводка: сколько групп, сколько зон в каждой (по id группы),
сколько зон ни в одной группе и сколько -- в двух и больше.

ЧЕГО НЕ ДЕЛАЕТ
  НИЧЕГО НЕ ПИШЕТ В БАЗУ. `transport.db` открывается строкой
      `file:...?mode=ro`: запись отвергает SQLite, а не обещание скрипта.
      Отсутствующую базу не создаёт -- отказ с кодом 2.
  Ничего не меняет в Wialon. Сервисы только читающие: `token/login`,
      `core/search_items`, `resource/get_zone_data`, `core/logout`.
  Не решает, какая группа -- территория подразделения. Это решение владельца
      по CSV; правило «участок на территории -- не гектары» пишется отдельно.
  Не угадывает по имени. Зона без группы так и остаётся «ни в одной группе».
  Не печатает имён в консоль. Имена зон и групп -- часто имена людей
      (фермеров, ответственных); в консоль идут только числа и id, имена --
      только в CSV.

ФЛАГ ГРУПП ГЕОЗОН -- ПРОВЕРИТЬ ПЕРВЫМ ПРОГОНОМ
`0x00100000` (1048576, «geofences groups») -- строка таблицы флагов ресурса
Wialon Remote API, https://sdk.wialon.com/wiki/en/sidebar/remoteapi/apiref/format/resource
(та же строка в выпусках local 1604 / 1704 / 2104 / 2204). Сессия 14.10 саму
страницу открыть не смогла -- sdk.wialon.com закрыт политикой выхода её
контейнера; значение подтверждено выдачей поиска по этим страницам, не
чтением страницы. Имя свойства ответа `zg` и поле `zns` (id зон группы)
документацией НЕ подтверждены: `zns` -- имя параметра «массив id геозон» у
`resource/update_zones_group`, `zg` -- по формату SDK, как его помнит сессия.
Поэтому ответ без групп -- не «групп нет», а отказ с кодом 4 и списком
ключей, которыми ответил ресурс: по ним видно, пришла ли часть под другим
именем.

КОДЫ ВЫХОДА
  0  готово, оба CSV записаны;
  2  плохой ввод: нет базы или в ней нет `field_contours`, неверный
     --resource, нет папки для CSV, нет токена;
  3  Wialon: вход отклонён (печатается код ошибки), запрос не прошёл,
     ресурса нет в ответе, у ресурса нет зон, данные зон не пришли;
  4  Wialon ответил, но групп в ответе нет или ни в одной нет зон: флаг или
     имя свойства могут быть неверны. CSV НЕ пишутся -- пустой файл групп
     приняли бы за «групп в Wialon нет».

ТОЛЬКО STDLIB, системный Python: ни Flask (`app = create_app()` вызывает
`db.create_all()` на импорте), ни геостека.

Запуск (PowerShell, по одной команде на строку). Из C:\\transport-report --
там лежит wialon_token.txt, а `read_token` ищет его в текущем каталоге;
скрипт и CSV -- полными путями в клоне C:\\gps-tools:

  cd C:\\transport-report

  & "C:\\Program Files\\Python314\\python.exe" C:\\gps-tools\\tools\\gps_zone_groups.py --db C:\\transport-report\\instance\\transport.db --groups-out C:\\gps-tools\\check\\zone_groups.csv --zones-out C:\\gps-tools\\check\\zone_list.csv

  $LASTEXITCODE

Вывод в консоль -- ASCII.
"""

import argparse
import csv
import os
import sqlite3
import sys

from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gps_collector import config                                   # noqa: E402
from gps_collector.wialon import (Client, WialonError, err_code,   # noqa: E402
                                  err_reason, login_failure)
from tools.gps_mark_not_ours import out_clash                      # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, 'instance', 'transport.db')
SOURCE = 'wialon'

# [REASON]: флаг данных ресурса «geofences groups» = 0x00100000 (1048576),
# источник -- таблица флагов в
# https://sdk.wialon.com/wiki/en/sidebar/remoteapi/apiref/format/resource
# (и в выпусках local 1604/1704/2104/2204). Подтверждён выдачей поиска, страница
# из контейнера сессии не открылась (403 политики выхода). Неверный флаг не
# даёт ошибки -- сервер молча не присылает часть, и «групп нет» выглядело бы
# фактом. Поэтому пустой ответ -- код 4, а не пустой CSV.
ZONE_GROUPS_FLAG = 0x00100000

# [REASON]: имя свойства с группами и имя списка id зон в группе по
# документации НЕ подтверждены (см. докстринг): `zns` -- параметр
# `resource/update_zones_group`, `zg` -- формат SDK по памяти сессии. Вынесены
# сюда, чтобы правка после первого прогона была в одной строке.
ZONE_GROUPS_KEY = 'zg'
GROUP_MEMBERS_KEY = 'zns'

EXIT_OK = 0
EXIT_BAD_INPUT = 2
EXIT_WIALON = 3
EXIT_NO_GROUPS = 4

# Тип геозоны в Wialon -- те же коды, что в config.ZONE_TYPE_POLYGON.
ZONE_TYPES = {1: 'liniya', 2: 'poligon', 3: 'krug'}

GROUP_COLUMNS = ('gruppa_id', 'gruppa', 'opisanie', 'zon', 'zon_v_zerkale',
                 'net_v_spiske_zon', 'zona_ids')
ZONE_COLUMNS = ('zona_id', 'zona', 'tip', 'tip_kod', 'grupp', 'gruppy',
                'gruppa_ids', 'v_zerkale')


def as_int(value):
    """Целое из того, что прислал сервер, или None. bool -- не id."""
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def open_readonly(path):
    """Соединение, которым нельзя писать.

    [REASON]: `mode=ro` -- запрет на уровне SQLite, а не обещание скрипта:
    любая команда записи отвечает `attempt to write a readonly database`, а
    отсутствующий файл не создаётся. Тот же вид, что у GPS-12, проверенный на
    сервере владельца.
    """
    return sqlite3.connect('file:%s?mode=ro' % path, uri=True, timeout=30)


def mirror_ids(con):
    """id зон Wialon, которые уже лежат в зеркале. None -- таблицы нет."""
    present = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name='field_contours'").fetchone()
    if not present:
        return None
    # [REASON]: только source='wialon'. external_id контура из другого
    # источника может совпасть с id зоны цифрами, но это другая вещь.
    return {str(row[0]).strip() for row in con.execute(
        'SELECT external_id FROM field_contours '
        'WHERE source = ? AND external_id IS NOT NULL', (SOURCE,))}


def search_resource(client, resource_id):
    """Ресурс с его списком зон и группами зон. Один запрос. None -- нет его."""
    answer = client.call('core/search_items', {
        'spec': {'itemsType': 'avl_resource', 'propName': 'sys_name',
                 'propValueMask': '*', 'sortType': 'sys_name'},
        'force': 1, 'flags': config.ZONE_LIST_FLAGS | ZONE_GROUPS_FLAG,
        'from': 0, 'to': 0})
    code = err_code(answer)
    if code is not None:
        raise WialonError(code, err_reason(answer))
    items = answer.get('items') if isinstance(answer, dict) else None
    for item in items or []:
        if isinstance(item, dict) and as_int(item.get('id')) == resource_id:
            return item
    return None


def zone_ids_of(item):
    """id зон ресурса.

    [REASON]: id -- ключи `zl`, ровно как в `Client.zone_ids`: этим списком
    зеркало GPS-4 скачало все 17 812 зон. Другой способ читать тот же список
    дал бы два ответа на один вопрос.
    """
    listed = item.get('zl') or {}
    if isinstance(listed, dict):
        raw = list(listed)
    elif isinstance(listed, list):
        raw = [entry.get('id') for entry in listed if isinstance(entry, dict)]
    else:
        raw = []
    return sorted({value for value in (as_int(key) for key in raw)
                   if value is not None})


def groups_of(item):
    """Группы зон ресурса: [{'id', 'name', 'description', 'members'}].

    None -- в ответе нет свойства групп вовсе (это не то же, что пустое).
    Сервер отдаёт такие части словарём по id; список тоже принимается.
    """
    raw = item.get(ZONE_GROUPS_KEY)
    if raw is None:
        return None
    if isinstance(raw, dict):
        pairs = list(raw.items())
    elif isinstance(raw, list):
        pairs = [(None, value) for value in raw]
    else:
        pairs = []
    groups = []
    for key, value in pairs:
        if not isinstance(value, dict):
            continue
        group_id = as_int(value.get('id'))
        if group_id is None:
            group_id = as_int(key)
        if group_id is None:
            continue
        members = value.get(GROUP_MEMBERS_KEY) or []
        if isinstance(members, dict):
            members = list(members)
        if not isinstance(members, list):
            members = []
        groups.append({
            'id': group_id,
            'name': str(value.get('n') or ''),
            'description': str(value.get('d') or ''),
            'members': sorted({m for m in (as_int(x) for x in members)
                               if m is not None})})
    return sorted(groups, key=lambda group: group['id'])


def answer_keys(mapping):
    """Ключи ответа для консоли: имена свойств API, не значения."""
    keys = sorted(config.ascii_only(key)[:24] for key in mapping)
    return ' '.join(keys[:30]) or '(none)'


def groups_refusal(item, groups):
    """Строки отказа, если группы в ответе подозрительны; None -- всё в порядке.

    [REASON]: неверный флаг или неверное имя свойства не дают ошибки -- сервер
    молча не присылает часть. «Групп нет» в этом случае выглядело бы фактом о
    Wialon, и по нему решили бы, что территории искать по группам нечего.
    Ответ без групп поэтому -- отказ, как пустой парк у GPS-12.
    """
    tail = 'NOT treating this as a resource without groups; nothing written'
    if groups is None:
        return ['FAILED: no groups returned - flag may be wrong: the resource '
                'answered without "%s" (flags asked 0x%X)'
                % (ZONE_GROUPS_KEY, config.ZONE_LIST_FLAGS | ZONE_GROUPS_FLAG),
                '  keys in the answer: %s' % answer_keys(item), tail]
    if not groups:
        return ['FAILED: no groups returned - flag may be wrong ("%s" came '
                'empty), or the resource really has no groups'
                % ZONE_GROUPS_KEY, tail]
    if not any(group['members'] for group in groups):
        raw = item.get(ZONE_GROUPS_KEY)
        first = (next(iter(raw.values())) if isinstance(raw, dict) and raw
                 else raw[0] if isinstance(raw, list) and raw else {})
        return ['FAILED: %d group(s) returned but none lists a member zone - '
                'the member key "%s" may be wrong'
                % (len(groups), GROUP_MEMBERS_KEY),
                '  keys of a group: %s' % (answer_keys(first)
                                           if isinstance(first, dict)
                                           else '(not an object)'), tail]
    return None


def build(zone_ids, zone_data, groups, mirror):
    """Строки обоих CSV и сводка. Ничего не пишет."""
    zones = {zone_id: {'id': zone_id, 'name': '', 'type': None,
                       'downloaded': False, 'groups': []}
             for zone_id in zone_ids}
    for raw in zone_data:
        if not isinstance(raw, dict):
            continue
        zone = zones.get(as_int(raw.get('id')))
        if zone is None:
            continue
        zone['name'] = str(raw.get('n') or '')
        zone['type'] = as_int(raw.get('t'))
        zone['downloaded'] = True

    group_rows = []
    per_group = []
    for group in groups:
        missing = [m for m in group['members'] if m not in zones]
        for member in group['members']:
            if member in zones:
                zones[member]['groups'].append(group)
        group_rows.append({
            'gruppa_id': group['id'],
            'gruppa': group['name'],
            'opisanie': group['description'],
            'zon': len(group['members']),
            'zon_v_zerkale': sum(1 for m in group['members']
                                 if str(m) in mirror),
            'net_v_spiske_zon': ' '.join(str(m) for m in missing),
            'zona_ids': ' '.join(str(m) for m in group['members'])})
        per_group.append((group['id'], len(group['members']), len(missing)))

    zone_rows = []
    for zone in zones.values():
        kind = zone['type']
        zone_rows.append({
            'zona_id': zone['id'],
            'zona': zone['name'],
            'tip': ('' if kind is None
                    else ZONE_TYPES.get(kind, 'kod %d' % kind)),
            'tip_kod': '' if kind is None else kind,
            'grupp': len(zone['groups']),
            'gruppy': ' | '.join(group['name'] for group in zone['groups']),
            'gruppa_ids': ' '.join(str(group['id'])
                                   for group in zone['groups']),
            'v_zerkale': 'da' if str(zone['id']) in mirror else 'net'})
    # [REASON]: сначала зоны в группах -- ради них инструмент и запускается;
    # зоны ни в одной группе (поля фермеров в основном) -- в конце, чтобы
    # 17 тысяч полей не закрыли собой несколько территорий.
    zone_rows.sort(key=lambda row: (
        0 if row['grupp'] else 1,
        int(row['gruppa_ids'].split()[0]) if row['grupp'] else 0,
        row['zona'].casefold(), row['zona_id']))

    listed = {str(zone_id) for zone_id in zones}
    summary = {
        'zones': len(zones),
        'downloaded': sum(1 for zone in zones.values() if zone['downloaded']),
        'groups': len(groups),
        'per_group': per_group,
        'no_group': sum(1 for zone in zones.values() if not zone['groups']),
        'several': sum(1 for zone in zones.values()
                       if len(zone['groups']) >= 2),
        'not_listed': sum(missing for _, _, missing in per_group),
        'in_mirror': sum(1 for zone_id in listed if zone_id in mirror),
        'mirror_only': len(set(mirror) - listed),
        'types': Counter(row['tip'] or '(bez dannyh)' for row in zone_rows)}
    return group_rows, zone_rows, summary


def write_csv(path, columns, rows):
    with open(path, 'w', encoding='utf-8-sig', newline='') as fh:
        writer = csv.writer(fh, delimiter=';')
        writer.writerow(columns)
        for row in rows:
            writer.writerow(['' if row.get(column) is None else row[column]
                             for column in columns])


def report(resource_id, summary, log=print):
    """Сводка в консоль. Только числа и id -- имён здесь нет и быть не должно."""
    log('')
    log('resource                    : %d' % resource_id)
    log('zones listed                : %d' % summary['zones'])
    log('zones with name and type    : %d' % summary['downloaded'])
    if summary['downloaded'] < summary['zones']:
        log('zones listed but without data: %d'
            % (summary['zones'] - summary['downloaded']))
    for kind, count in sorted(summary['types'].items(),
                              key=lambda pair: (-pair[1], pair[0])):
        log('  type %-12s : %d' % (kind, count))
    log('groups                      : %d' % summary['groups'])
    for group_id, members, missing in summary['per_group']:
        line = '  group %d: %d zone(s)' % (group_id, members)
        if missing:
            line += ', %d not in the zone list' % missing
        log(line)
    log('zones in no group           : %d' % summary['no_group'])
    log('zones in two or more groups : %d' % summary['several'])
    if summary['not_listed']:
        log('group members not in the zone list: %d' % summary['not_listed'])
    log('zones in our mirror         : %d of %d (field_contours, source=%s)'
        % (summary['in_mirror'], summary['zones'], SOURCE))
    log('mirror rows not in the list : %d' % summary['mirror_only'])


def out_folder_missing(path):
    folder = os.path.dirname(os.path.abspath(path))
    return not os.path.isdir(folder)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=config.ascii_only(__doc__.splitlines()[0]))
    parser.add_argument('--db', default=DB_PATH)
    parser.add_argument('--resource', type=int, default=config.ZONE_RESOURCE_ID)
    parser.add_argument('--pause', type=float, default=config.PAUSE_S)
    parser.add_argument('--groups-out', default='gps_zone_groups.csv')
    parser.add_argument('--zones-out', default='gps_zone_list.csv')
    args = parser.parse_args(argv)

    if args.resource <= 0:
        sys.stderr.write('ERROR: --resource must be a positive id\n')
        return EXIT_BAD_INPUT
    # [REASON]: сравнение строк путей не видит жёсткой ссылки и, на Windows,
    # другого регистра: CSV зон молча записался бы поверх CSV групп.
    if out_clash(args.zones_out, (args.groups_out,)) == args.groups_out:
        sys.stderr.write('ERROR: --groups-out and --zones-out are the same '
                         'file\n')
        return EXIT_BAD_INPUT
    for path in (args.groups_out, args.zones_out):
        if out_folder_missing(path):
            sys.stderr.write('ERROR: folder for %s does not exist\n'
                             % config.ascii_only(path))
            return EXIT_BAD_INPUT
        # [REASON]: CSV пишется режимом 'w'. Опечатка -- путь базы на месте
        # CSV -- заменила бы боевую базу таблицей; правило то же, что у
        # GPS-16 и GPS-17.
        clash = out_clash(path, (args.db, args.db + '-wal', args.db + '-shm'))
        if clash:
            sys.stderr.write('ERROR: %s points at %s - refusing to overwrite '
                             'it\n' % (config.ascii_only(path),
                                        config.ascii_only(clash)))
            return EXIT_BAD_INPUT
    if not os.path.isfile(args.db):
        # [REASON]: проверка до любого соединения. sqlite3.connect без mode=ro
        # создал бы пустую базу, а пустая база вместо боевой -- это зеркало
        # «без единой зоны», то есть неверный ответ, а не отказ.
        sys.stderr.write('ERROR: database not found at %s - refusing to run\n'
                         % config.ascii_only(args.db))
        return EXIT_BAD_INPUT
    try:
        con = open_readonly(args.db)
        try:
            mirror = mirror_ids(con)
        finally:
            con.close()
    except sqlite3.DatabaseError as problem:
        sys.stderr.write('ERROR: cannot read %s (%s)\n'
                         % (config.ascii_only(args.db),
                            type(problem).__name__))
        return EXIT_BAD_INPUT
    if mirror is None:
        sys.stderr.write('ERROR: table field_contours is missing - wrong '
                         '--db?\n')
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
            item = search_resource(client, args.resource)
            if item is None:
                print('FAILED: resource %d is not in the answer - wrong '
                      '--resource, or no access to it' % args.resource)
                return EXIT_WIALON
            ids = zone_ids_of(item)
            print('zones listed in resource %d: %d' % (args.resource, len(ids)))
            if not ids:
                print('FAILED: resource returned no zone list - NOT treating '
                      'this as a resource without zones')
                return EXIT_WIALON
            groups = groups_of(item)
            refusal = groups_refusal(item, groups)
            if refusal:
                for line in refusal:
                    print(line)
                return EXIT_NO_GROUPS
            print('groups returned: %d' % len(groups))
            # [REASON]: все id одним запросом -- так зеркало GPS-4 скачивает
            # этот же ресурс, и это единственный проверенный на нём способ.
            zone_data = client.zone_data(args.resource, ids)
        except Exception as problem:                               # noqa: BLE001
            # login_failure печатает код ошибки Wialon и только имя класса
            # чужого исключения: токен едет в теле запроса (wialon._fetch).
            sys.stderr.write('\nERROR: zapros k Wialon ne udalsya (%s)\n'
                             % login_failure(problem))
            return EXIT_WIALON
    finally:
        client.logout()

    if not zone_data:
        # [REASON]: список зон непустой, а данных нет -- тот самый отказ, что
        # однажды стал файлом на два байта и сошёл за успешную выгрузку.
        print('FAILED: %d zones listed but none downloaded - NOT treating '
              'this as zones without names' % len(ids))
        return EXIT_WIALON

    group_rows, zone_rows, summary = build(ids, zone_data, groups, mirror)
    write_csv(args.groups_out, GROUP_COLUMNS, group_rows)
    write_csv(args.zones_out, ZONE_COLUMNS, zone_rows)
    report(args.resource, summary)
    print('')
    print('written          : %s' % config.ascii_only(args.groups_out))
    print('written          : %s' % config.ascii_only(args.zones_out))
    print('')
    print('nothing was written to the database: it was opened mode=ro')
    return EXIT_OK


if __name__ == '__main__':
    sys.exit(main())
