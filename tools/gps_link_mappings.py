# -*- coding: utf-8 -*-
"""GPS-10 -- связка строк сопоставления Wialon с объектами по id.

ЗАЧЕМ
`vialon_mappings.vialon_name` -- имя объекта Wialon, как оно пришло из
Excel-выгрузки; по нему ручной импорт моточасов находит нашу технику. Треку
GPS нужно не имя, а id объекта: точки лежат по `wialon_id`, и сверка нарядов
(`/gps/orders`) ищет объект техники через `VialonMapping.wialon_id`. Колонку
добавила миграция CORE_FOUNDATION_001, но заполнять её было нечем: ручной
импорт её не трогает, экрана для неё нет. Пока она пуста, каждый наряд на
`/gps/orders` уходит в «техника не сопоставлена с объектом Wialon», а
`/gps/fact` показывает числа вместо имён машин.

ЧТО ДЕЛАЕТ
Берёт у Wialon список объектов -- id, имя, время последнего сообщения; один
запрос на весь парк, сообщений не грузит -- и ставит `wialon_id` тем строкам
сопоставления, чьё имя совпадает с именем РОВНО ОДНОГО объекта. Это один и
тот же идентификатор в двух местах, а не угадывание.

ЧЕГО НЕ ДЕЛАЕТ
  Не угадывает. Имя на нескольких объектах, имя без объекта, два наших имени,
      различающихся только пробелами или регистром, -- всё это идёт в CSV
      владельцу, а не в базу.
  Не перезаписывает. Строка с уже стоящим `wialon_id` не трогается, даже если
      имя теперь указывает на другой объект: это только сообщается.
  Не удаляет и не гасит. Строки со `skip = 1` (не наша техника) не трогаются.
  Не ставит `equipment_id`: какой машине принадлежит имя, решает человек на
      экране сопоставления, как и прежде.

СОВПАДЕНИЕ ИМЁН. Пробелы по краям и повторные пробелы внутри не считаются --
так же нормализует имя сам импорт (`_normalize_wialon_name`); регистр букв не
считается. Латиница и кириллица -- разные буквы: «MT3» и «МТЗ» не совпадают,
и это намеренно. Подсказки по номеру в CSV -- только подсказки: в базу они
не попадают.

ТОЛЬКО STDLIB. Пишет через `sqlite3` напрямую, без Flask: `app = create_app()`
вызывает `db.create_all()` на импорте, и любой скрипт с `from app import app`
становится писателем схемы.

ТОЛЬКО ЧТЕНИЕ на стороне Wialon: `token/login`, `core/search_items`,
`core/logout`.

СУХОЙ ПРОГОН ПО УМОЛЧАНИЮ. Без `--apply` ничего не пишется: план в консоль и
в `gps_link_plan.csv`. С `--apply` -- одна транзакция, только строки со
статусом `svyazat`, и только если в момент записи `wialon_id` у них всё ещё
пуст; иначе откат целиком.

Запуск (PowerShell, по одной команде на строку):

  cd C:\\transport-report

  & "C:\\Program Files\\Python314\\python.exe" tools\\gps_link_mappings.py

  & "C:\\Program Files\\Python314\\python.exe" tools\\gps_link_mappings.py --apply

Решения человека по строкам из CSV -- тем же скриптом, с теми же замками:

  & "C:\\Program Files\\Python314\\python.exe" tools\\gps_link_mappings.py --set 77=12345 --apply

  & "C:\\Program Files\\Python314\\python.exe" tools\\gps_link_mappings.py --unset 77 --apply

`--set` ставит id объекта строке 77: объект обязан быть в парке Wialon, строка
-- нашей и без `wialon_id`; если её имя точно совпадает с другим объектом,
скрипт отказывает -- чинить надо имя строки, а не обходить его. `--unset`
снимает `wialon_id` -- это и есть откат. Ничего другого скрипт не меняет; поля
`wialon_id` на экране сопоставления нет, поэтому другого пути тоже нет.

Вывод в консоль -- ASCII.
"""

import argparse
import csv
import os
import re
import sqlite3
import sys

from collections import Counter, defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gps_collector import config                                   # noqa: E402
from gps_collector.wialon import Client                            # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, 'instance', 'transport.db')

# Статусы строк плана. Слаги ASCII, как и прочие причины в треке.
LINK = 'svyazat'                  # имя совпало ровно с одним объектом
ALREADY = 'uzhe'                  # wialon_id стоит и имя всё ещё его
NOT_FOUND = 'ne_naideno'          # объекта с таким именем нет
AMBIGUOUS = 'neodnoznachno'       # имя стоит на нескольких объектах
COLLISION = 'stolknovenie_imen'   # две наши строки -- одно имя с точностью до пробелов и регистра
CONFLICT = 'rashozhdenie'         # wialon_id стоит, а имя указывает на другой объект
RENAMED = 'pereimenovan'          # wialon_id стоит, объект жив, но зовётся иначе
GONE = 'obekt_ischez'             # wialon_id стоит, объекта в Wialon больше нет
NO_ROW = 'bez_stroki'             # объект есть в Wialon, строки сопоставления нет

# Порядок в CSV: сначала то, что требует решения, потом план, потом справка.
CSV_ORDER = (CONFLICT, COLLISION, AMBIGUOUS, NOT_FOUND, RENAMED, GONE,
             LINK, ALREADY, NO_ROW)

CSV_COLUMNS = ('status', 'mapping_id', 'vialon_name', 'equipment', 'plate',
               'wialon_id_now', 'wialon_id_match', 'wialon_name_live',
               'last_message', 'equipment_objects', 'candidates')

MAX_CANDIDATES = 5


def normalize_name(text):
    """Ключ совпадения имён.

    [REASON]: то же правило, каким имя нормализует сам импорт
    (`_normalize_wialon_name` в wialon_import.py), плюс регистр. Регистр
    безопасен: два объекта, различающихся только им, дадут один ключ и уйдут в
    «неоднозначно», а не в случайный из двух. Гомоглифы латиницы и кириллицы
    НЕ сводятся: это уже не то же имя, а похожее, и решать похожесть -- не
    скрипту.
    """
    return re.sub(r'\s+', ' ', (text or '').strip()).casefold()


# [REASON]: подсказки по номеру повторяют _normalize/auto_match_vehicles из
# wialon_import.py -- те же гомоглифы, тот же отброс кода региона 80/25, тот же
# порог в четыре знака -- чтобы два инструмента не спорили о том, что такое
# «номер встречается в имени». Импортировать оттуда нельзя: модуль тянет Flask
# и модели, а этот скрипт обязан оставаться на stdlib.
LOOKALIKES = str.maketrans({'А': 'A', 'В': 'B', 'Е': 'E', 'О': 'O', 'Р': 'P',
                            'С': 'C', 'Т': 'T', 'Х': 'X', 'К': 'K', 'М': 'M',
                            'Н': 'H', 'У': 'Y'})


def plate_key(text):
    text = (text or '').upper().strip()
    text = re.sub(r'[\s\-\.\(\)]', '', text)
    return text.translate(LOOKALIKES)


def plate_suffix(plate):
    parts = (plate or '').strip().split()
    if parts and parts[0] in ('80', '25'):
        return plate_key(' '.join(parts[1:]))
    return plate_key(plate)


def candidates_by_plate(plate, units):
    """Объекты, в имени которых встречается номер машины. Только подсказка."""
    key = plate_suffix(plate)
    if len(key) < 4:
        return []
    return [u for u in units if key in plate_key(u['name'])][:MAX_CANDIDATES]


def local_stamp(epoch):
    if epoch is None:
        return ''
    return datetime.fromtimestamp(int(epoch), config.TZ).strftime('%Y-%m-%d %H:%M')


def mapping_rows(con):
    """Все строки сопоставления с именем и номером машины, если машина указана."""
    con.row_factory = sqlite3.Row
    rows = con.execute(
        'SELECT m.id, m.vialon_name, m.wialon_id, m.equipment_id, m.skip, '
        'e.name AS equipment_name, e.plate AS plate '
        'FROM vialon_mappings m LEFT JOIN equipment e ON e.id = m.equipment_id '
        'ORDER BY m.id').fetchall()
    con.row_factory = None
    out = []
    for row in rows:
        out.append({'id': row['id'], 'vialon_name': row['vialon_name'] or '',
                    'wialon_id': row['wialon_id'],
                    'equipment_id': row['equipment_id'],
                    'skip': bool(row['skip']),
                    'equipment_name': row['equipment_name'] or '',
                    'plate': row['plate'] or ''})
    return out


def _item(row, status, match=None, live=None, extra=None):
    item = {'status': status, 'mapping_id': row['id'],
            'vialon_name': row['vialon_name'],
            'equipment_id': row['equipment_id'],
            'equipment': row['equipment_name'], 'plate': row['plate'],
            'wialon_id_now': row['wialon_id'],
            'wialon_id_match': match['id'] if match else None,
            'wialon_name_live': (live or match or {}).get('name', ''),
            'last_message': local_stamp((live or match or {}).get('last_t')),
            'equipment_objects': '', 'candidates': ''}
    if extra:
        item.update(extra)
    return item


def plan(rows, units):
    """Что изменится и что требует решения. Ничего не пишет.

    Возвращает (строки плана, сводка). Строки со `skip` в план не входят:
    владелец уже сказал, что это не наша техника.
    """
    by_key = defaultdict(list)
    by_id = {}
    for unit in units:
        by_key[normalize_name(unit['name'])].append(unit)
        by_id[unit['id']] = unit

    active = [row for row in rows if not row['skip']]
    key_count = Counter(normalize_name(row['vialon_name']) for row in active)

    items = []
    for row in active:
        key = normalize_name(row['vialon_name'])
        matches = by_key.get(key, [])
        current = row['wialon_id']
        if current is not None:
            live = by_id.get(current)
            if any(unit['id'] == current for unit in matches):
                items.append(_item(row, ALREADY, live=live))
            elif matches:
                # Имя теперь указывает на другой объект. Не перезаписываем:
                # стоящий id мог быть поставлен человеком нарочно.
                items.append(_item(row, CONFLICT, live=live, extra={
                    'wialon_id_match': ' '.join(str(u['id']) for u in matches),
                    'wialon_name_live': live['name'] if live else '',
                }))
            elif live is not None:
                items.append(_item(row, RENAMED, live=live))
            else:
                items.append(_item(row, GONE))
            continue

        if key_count[key] > 1:
            # [REASON]: две наши строки с одним именем с точностью до пробелов
            # и регистра. Привязать обе к одному объекту значит отдать один
            # трек двум машинам -- и посчитать одну работу дважды в сверке.
            items.append(_item(row, COLLISION, extra={
                'wialon_id_match': ' '.join(str(u['id']) for u in matches),
                'wialon_name_live': matches[0]['name'] if matches else '',
            }))
        elif len(matches) == 1:
            items.append(_item(row, LINK, match=matches[0]))
        elif matches:
            # [REASON]: шесть номеров указывают на несколько объектов, у одной
            # машины их три -- история замены трекеров. Взять первый значит
            # угадать; какой трекер живой, видно по времени последнего
            # сообщения, и решает это человек.
            items.append(_item(row, AMBIGUOUS, extra={
                'wialon_id_match': ' '.join(str(u['id']) for u in matches),
                'wialon_name_live': matches[0]['name'],
                'last_message': ' / '.join(local_stamp(u['last_t']) or '-'
                                           for u in matches),
            }))
        else:
            found = candidates_by_plate(row['plate'], units)
            items.append(_item(row, NOT_FOUND, extra={
                'candidates': ' | '.join('%d %s' % (u['id'], u['name'])
                                         for u in found),
            }))

    # Объекты Wialon, о которых сопоставление не знает вовсе: ни строки с
    # таким именем (включая skip -- «не наша» тоже знание), ни строки с таким id.
    known_keys = {normalize_name(row['vialon_name']) for row in rows}
    known_ids = {row['wialon_id'] for row in rows if row['wialon_id'] is not None}
    for unit in units:
        if normalize_name(unit['name']) in known_keys or unit['id'] in known_ids:
            continue
        items.append({'status': NO_ROW, 'mapping_id': '', 'vialon_name': '',
                      'equipment_id': None, 'equipment': '', 'plate': '',
                      'wialon_id_now': '', 'wialon_id_match': unit['id'],
                      'wialon_name_live': unit['name'],
                      'last_message': local_stamp(unit['last_t']),
                      'equipment_objects': '', 'candidates': ''})

    # Сколько объектов будет у машины после записи плана. Больше одного -- не
    # ошибка скрипта, а факт, который сверка нарядов покажет как
    # «несколько объектов»; владельцу стоит погасить мёртвый трекер через skip.
    objects = defaultdict(set)
    for item in items:
        if not item['equipment_id']:
            continue
        if item['status'] in (ALREADY, CONFLICT, RENAMED, GONE):
            objects[item['equipment_id']].add(item['wialon_id_now'])
        elif item['status'] == LINK:
            objects[item['equipment_id']].add(item['wialon_id_match'])
    for item in items:
        if item['equipment_id'] in objects:
            item['equipment_objects'] = len(objects[item['equipment_id']])

    summary = Counter(item['status'] for item in items)
    summary['skipped'] = len(rows) - len(active)
    summary['rows'] = len(rows)
    summary['multi_object_equipment'] = sum(
        1 for ids in objects.values() if len(ids) > 1)
    return items, summary


def write_plan(path, items):
    order = {status: index for index, status in enumerate(CSV_ORDER)}
    ordered = sorted(items, key=lambda i: (order.get(i['status'], 99),
                                           i['vialon_name'] or i['wialon_name_live']))
    with open(path, 'w', encoding='utf-8-sig', newline='') as fh:
        writer = csv.writer(fh, delimiter=';')
        writer.writerow(CSV_COLUMNS)
        for item in ordered:
            writer.writerow(['' if item[column] is None else item[column]
                             for column in CSV_COLUMNS])


def apply_plan(con, links, unsets=()):
    """Записать связки одной транзакцией. Возвращает число строк или None.

    [REASON]: `AND wialon_id IS NULL` в самом UPDATE, а не только в плане:
    между планом и записью строку мог тронуть человек на экране. Если хоть одна
    строка не записалась, откатывается всё -- частично записанный план
    неотличим от полностью записанного, пока не откроешь CSV.
    """
    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    con.execute('BEGIN')
    try:
        written = 0
        for mapping_id, unit_id in links:
            cursor = con.execute(
                'UPDATE vialon_mappings SET wialon_id = ?, updated_at = ? '
                'WHERE id = ? AND wialon_id IS NULL',
                (int(unit_id), stamp, int(mapping_id)))
            written += cursor.rowcount
        for mapping_id in unsets:
            cursor = con.execute(
                'UPDATE vialon_mappings SET wialon_id = NULL, updated_at = ? '
                'WHERE id = ? AND wialon_id IS NOT NULL',
                (stamp, int(mapping_id)))
            written += cursor.rowcount
        if written != len(links) + len(unsets):
            con.rollback()
            return None
        con.commit()
        return written
    except Exception:
        con.rollback()
        raise


def _schema_ok(con):
    """(True, '') или (False, что не так). Ничего не создаёт."""
    for table in ('vialon_mappings', 'equipment'):
        present = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,)).fetchone()
        if not present:
            return False, 'table %s is missing' % table
    columns = {row[1] for row in con.execute('PRAGMA table_info(vialon_mappings)')}
    if 'wialon_id' not in columns:
        return False, ('column vialon_mappings.wialon_id is missing - run '
                       'migrate_core_foundation_001.py first')
    return True, ''


def parse_manual(values):
    """'77=12345' -> (77, 12345). Кривой формат -- отказ, а не догадка."""
    out = []
    for value in values:
        left, sep, right = value.partition('=')
        if not sep or not left.strip().isdigit() or not right.strip().isdigit():
            raise ValueError(value)
        out.append((int(left), int(right)))
    return out


def check_manual(sets, unsets, rows, units, auto_links):
    """Решения человека против базы и парка. Список проблем; пустой -- можно.

    [REASON]: те же замки, что у автоплана. Ошибка в id, набранном руками, --
    самый вероятный путь привязать трек чужой машины, и без проверки по парку
    она проходит молча.
    """
    by_row = {row['id']: row for row in rows}
    unit_ids = {unit['id'] for unit in units}
    auto = dict(auto_links)
    problems = []
    seen = set()
    for mapping_id, unit_id in sets:
        if mapping_id in seen:
            problems.append('--set %d: given twice' % mapping_id)
            continue
        seen.add(mapping_id)
        row = by_row.get(mapping_id)
        if row is None:
            problems.append('--set %d: no such mapping row' % mapping_id)
        elif row['skip']:
            problems.append('--set %d: row is marked "not ours" (skip)' % mapping_id)
        elif row['wialon_id'] is not None:
            problems.append('--set %d: wialon_id is already %d - never '
                            'overwritten, --unset it first'
                            % (mapping_id, row['wialon_id']))
        elif unit_id not in unit_ids:
            problems.append('--set %d: object %d is not in Wialon'
                            % (mapping_id, unit_id))
        elif mapping_id in auto and auto[mapping_id] != unit_id:
            problems.append('--set %d: the name matches object %d exactly and '
                            '%d contradicts it - fix the row name instead'
                            % (mapping_id, auto[mapping_id], unit_id))
    for mapping_id in unsets:
        if mapping_id in seen:
            problems.append('--unset %d: also given to --set' % mapping_id)
            continue
        seen.add(mapping_id)
        row = by_row.get(mapping_id)
        if row is None:
            problems.append('--unset %d: no such mapping row' % mapping_id)
        elif row['wialon_id'] is None:
            problems.append('--unset %d: wialon_id is already empty' % mapping_id)
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--db', default=DB_PATH)
    parser.add_argument('--pause', type=float, default=config.PAUSE_S)
    parser.add_argument('--apply', action='store_true',
                        help='write the links; without it nothing is written')
    parser.add_argument('--plan-out', default='gps_link_plan.csv')
    parser.add_argument('--set', action='append', default=[],
                        metavar='MAPPING_ID=WIALON_ID',
                        help='link one row by hand (a decision from the CSV); '
                             'written only with --apply')
    parser.add_argument('--unset', action='append', default=[], type=int,
                        metavar='MAPPING_ID',
                        help='clear wialon_id of one row (rollback); written '
                             'only with --apply')
    args = parser.parse_args(argv)
    try:
        manual = parse_manual(args.set)
    except ValueError as bad:
        sys.stderr.write('ERROR: --set expects MAPPING_ID=WIALON_ID, got %r\n'
                         % (bad.args[0],))
        return 2

    if not os.path.exists(args.db):
        # [REASON]: sqlite3.connect создал бы пустую базу. Пустая база вместо
        # боевой -- это молчаливая потеря всего справочника.
        sys.stderr.write('ERROR: database not found at %s - refusing to run\n'
                         % args.db)
        return 2
    con = sqlite3.connect(args.db, timeout=30)
    try:
        ok, problem = _schema_ok(con)
        if not ok:
            sys.stderr.write('ERROR: %s\n' % problem)
            return 2

        token = config.read_token()
        client = Client(pause=args.pause)
        try:
            client.login(token)
        except Exception as problem:                               # noqa: BLE001
            sys.stderr.write('\nERROR: ne udalos voyti na %s (%s)\n'
                             % (config.BASE_URL, type(problem).__name__))
            return 3
        print('login OK')
        try:
            units = client.list_units()
        finally:
            client.logout()

        if not units:
            # [REASON]: пустой список объектов -- это отказ, а не парк без
            # техники: 481 объект не исчезает за ночь. Принять его за «нечего
            # связывать» значит молча оставить сверку пустой ещё на сутки.
            print('FAILED: Wialon returned no objects - NOT treating this as '
                  'nothing to link')
            return 1
        reported = sum(1 for u in units if u['last_t'] is not None)
        print('objects in Wialon: %d (ever reported: %d)' % (len(units), reported))

        rows = mapping_rows(con)
        items, summary = plan(rows, units)

        print('mapping rows: %d | skipped (not ours): %d | already linked: %d'
              % (summary['rows'], summary['skipped'], summary[ALREADY]))
        print('to link: %d' % summary[LINK])
        print('name not found in Wialon: %d' % summary[NOT_FOUND])
        print('name on several objects: %d' % summary[AMBIGUOUS])
        print('name collision inside mappings: %d' % summary[COLLISION])
        print('linked to another object than the name says: %d'
              % summary[CONFLICT])
        print('renamed in Wialon: %d | gone from Wialon: %d'
              % (summary[RENAMED], summary[GONE]))
        print('equipment with several objects after linking: %d'
              % summary['multi_object_equipment'])
        print('objects in Wialon without a mapping row: %d' % summary[NO_ROW])

        path = os.path.join(os.getcwd(), args.plan_out)
        write_plan(path, items)
        print('plan written to %s' % args.plan_out)

        links = [(item['mapping_id'], item['wialon_id_match'])
                 for item in items if item['status'] == LINK]
        problems = check_manual(manual, args.unset, rows, units, links)
        if problems:
            for problem in problems:
                sys.stderr.write('ERROR: %s\n' % problem)
            print('\nFAILED: %d problem(s) with --set/--unset - nothing written'
                  % len(problems))
            return 2
        automatic = dict(links)
        by_hand = [(mapping_id, unit_id) for mapping_id, unit_id in manual
                   if automatic.get(mapping_id) != unit_id]
        unsets = list(args.unset)
        if manual or unsets:
            print('by hand: link %d, unlink %d' % (len(by_hand), len(unsets)))
        links = links + by_hand

        if not args.apply:
            print('\ndry run: nothing was written. Re-run with --apply to '
                  'write %d links and %d unlinks.' % (len(links), len(unsets)))
            return 0
        if not links and not unsets:
            print('\nnothing to write')
            return 0
        written = apply_plan(con, links, unsets)
        if written is None:
            print('\nFAILED: some rows changed between plan and write - '
                  'nothing written, rolled back')
            return 1
        print('\nwritten: %d links, %d unlinks' % (len(links), len(unsets)))
        return 0
    finally:
        con.close()


if __name__ == '__main__':
    sys.exit(main())
