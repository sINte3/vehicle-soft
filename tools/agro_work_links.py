# -*- coding: utf-8 -*-
"""agro-work B1 -- ручные связки машин agro-work с нашей техникой.

ЗАЧЕМ
Импорт связывает машину agro-work с нашей строкой техники сам, только когда
госномер после свёртки совпал ровно с одной нашей машиной. 21 машина реестра
agro-work записана нестандартным номером (B0, раздел 5.3), и по номеру их не
связать. Владелец 28.09 (вопрос 7): «свяжу вручную один раз; несопоставленное
-- в CSV, ручные связки переживают повторный импорт». Этот инструмент и есть
ручная связка.

КАК
Импорт пишет `agro_work_unmatched.csv`: машины без связи, число их заявок и
подсказки из нашего справочника. Владелец вписывает в колонку `equipment_id`
номер нашей машины -- id строки справочника «Техника», не Wialon и не IMEI --
и отдаёт файл сюда. Подсказка -- только подсказка: 29.09 у тракторов
Мирзачула `25290HA` она указала на МТЗ другого хозяйства с номером
`80 290 НА`, а настоящий трактор записан у нас как `25 HA 290`.

  & "C:\\Program Files\\Python314\\python.exe" tools\\agro_work_links.py --from-csv agro_work_unmatched.csv

  & "C:\\Program Files\\Python314\\python.exe" tools\\agro_work_links.py --from-csv agro_work_unmatched.csv --apply

Одна машина -- ключом `--set`: слева госномер agro-work как в CSV или id
машины agro-work, справа id нашей машины. `--unset` снимает связку -- это и
есть откат; снятая связка остаётся в таблице с отметкой `unlinked_at`.

  & "C:\\Program Files\\Python314\\python.exe" tools\\agro_work_links.py --set "ALFAKLAS12=512" --apply

СУХОЙ ПРОГОН ПО УМОЛЧАНИЮ. Без `--apply` ничего не пишется. С `--apply` --
одна транзакция: либо записаны все решения, либо ни одного. В каждой строке
плана рядом с номером нашей машины -- её название, госномер и хозяйство:
опечатку в номер другой существующей машины замки не ловят, её ловит глаз.

ЗАМКИ. Отказ, а не догадка, если: нашей машины с таким id нет; машины
agro-work с таким номером нет или их несколько; у машины уже есть связка
(сначала `--unset`); наша машина уже связана с ДРУГОЙ машиной agro-work --
две машины их реестра на одном нашем треке значат, что одна связь неверна, и
сверка отдала бы работу не той заявке.

Откуда брать id нашей машины, если подсказки нет: `--equipment-csv FILE`
выгружает наш справочник техники -- id, название, госномер, организация,
категория -- и больше ничего не делает.

  & "C:\\Program Files\\Python314\\python.exe" tools\\agro_work_links.py --equipment-csv agro_work_equipment.csv

Сеть не нужна: инструмент работает только с базой. Вывод в консоль -- ASCII.
"""

import argparse
import csv
import io
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agro_work import config, matching, records, store     # noqa: E402
from plate_norm import normalize_plate                     # noqa: E402


class Refused(ValueError):
    """Решение нельзя записать. Текст -- ASCII."""


def read_decisions_csv(path):
    """(agro transport id, equipment id) из CSV, где equipment_id заполнен.

    [REASON]: Excel в русской локали сохраняет CSV с `;` и в cp1251, в
    английской -- с `,` и в UTF-8. Файл вернётся от владельца в любом из
    этих видов, и ни один не должен молча прочитаться пустым.
    """
    with open(path, 'rb') as fh:
        raw = fh.read()
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = raw.decode('cp1251')
    first = text.splitlines()[0] if text else ''
    delimiter = ';' if first.count(';') >= first.count(',') else ','
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    columns = [c.strip() for c in (reader.fieldnames or [])]
    for needed in ('agro_transport_id', 'equipment_id'):
        if needed not in columns:
            raise Refused('%s: column %s is missing' % (os.path.basename(path),
                                                        needed))
    out = []
    for number, row in enumerate(reader, start=2):
        row = {(k or '').strip(): (v or '').strip() for k, v in row.items()}
        value = row.get('equipment_id', '')
        if not value:
            continue
        if not value.isdigit():
            raise Refused('line %d: equipment_id must be a number, got %r'
                          % (number, config.ascii_only(value)))
        transport_id = row.get('agro_transport_id', '')
        if not transport_id:
            raise Refused('line %d: agro_transport_id is empty' % number)
        out.append((transport_id, int(value)))
    return out


def parse_pairs(values):
    """'KEY=ID' -> (KEY, ID). Кривой формат -- отказ."""
    out = []
    for value in values:
        left, sep, right = value.rpartition('=')
        if not sep or not left.strip() or not right.strip().isdigit():
            raise Refused('--set expects PLATE_OR_ID=EQUIPMENT_ID, got %r'
                          % config.ascii_only(value))
        out.append((left.strip(), int(right.strip())))
    return out


def find_transport(con, key):
    """Машина agro-work по id или по госномеру. Ровно одна -- иначе отказ."""
    if records.UUID_RE.match(key) or con.execute(
            'SELECT 1 FROM agro_work_transports WHERE id = ?', (key,)).fetchone():
        row = con.execute('SELECT id, plate_number FROM agro_work_transports '
                          'WHERE id = ?', (key,)).fetchone()
        if row is None:
            raise Refused('no agro-work machine with id %s' % key)
        return row['id'], row['plate_number']
    norm = normalize_plate(key)
    rows = con.execute('SELECT id, plate_number FROM agro_work_transports '
                       'WHERE plate_norm = ?', (norm,)).fetchall()
    if not rows:
        raise Refused('no agro-work machine with plate %s'
                      % config.ascii_only(key))
    if len(rows) > 1:
        raise Refused('plate %s names %d agro-work machines - use the id from '
                      'the CSV' % (config.ascii_only(key), len(rows)))
    return rows[0]['id'], rows[0]['plate_number']


def plan(con, sets, unsets):
    """Проверить решения против базы. (to_link, to_unlink) или Refused."""
    links = store.active_links(con)
    equipment = {row['id'] for row in con.execute('SELECT id FROM equipment')}
    resolution = matching.resolve(store.transport_rows(con),
                                  store.equipment_rows(con), links)
    # Кто сейчас сидит на каждой нашей машине: связки и автоматика.
    holders = {}
    for transport_id, item in resolution.items():
        if item['equipment_id'] is not None:
            holders.setdefault(item['equipment_id'], set()).add(transport_id)

    to_link, to_unlink, seen = [], [], set()
    for key in unsets:
        transport_id, plate = find_transport(con, key)
        if transport_id in seen:
            raise Refused('%s is given twice' % config.ascii_only(plate))
        seen.add(transport_id)
        if transport_id not in links:
            raise Refused('%s has no owner link to remove'
                          % config.ascii_only(plate))
        to_unlink.append((transport_id, plate, links[transport_id]))
    for key, equipment_id in sets:
        transport_id, plate = find_transport(con, key)
        if transport_id in seen:
            raise Refused('%s is given twice' % config.ascii_only(plate))
        seen.add(transport_id)
        if equipment_id not in equipment:
            raise Refused('%s: our equipment %d does not exist'
                          % (config.ascii_only(plate), equipment_id))
        if transport_id in links:
            if links[transport_id] == equipment_id:
                continue
            raise Refused('%s is already linked to equipment %d - --unset it '
                          'first' % (config.ascii_only(plate),
                                     links[transport_id]))
        others = holders.get(equipment_id, set()) - {transport_id}
        others -= {t for t, _, _ in to_unlink}
        if others:
            # [REASON]: у agro-work бывают две карточки с одним госномером
            # (80765NBA, шаг 9, 29.09) -- по номеру отказ тогда называет одну
            # и ту же машину дважды. id карточки -- то, что есть в CSV.
            other_machines = ['%s [%s]' % (config.ascii_only(row['plate_number']),
                                           row['id']) for row in con.execute(
                'SELECT id, plate_number FROM agro_work_transports WHERE id IN '
                '(%s) ORDER BY id' % ','.join('?' * len(others)), sorted(others))]
            raise Refused('%s [%s]: equipment %d already belongs to agro-work '
                          'machine %s - two of their machines on one track of '
                          'ours means one link is wrong'
                          % (config.ascii_only(plate), transport_id, equipment_id,
                             ', '.join(other_machines)))
        holders.setdefault(equipment_id, set()).add(transport_id)
        to_link.append((transport_id, plate, equipment_id))
    return to_link, to_unlink


def describe_equipment(con, equipment_id):
    """«название / госномер / хозяйство» нашей машины для строки плана. ASCII.

    [REASON]: номер нашей машины владелец вписывает руками, а замки ловят
    только несуществующий или уже занятый номер. Опечатка в номер другой
    существующей машины прошла бы молча -- её видно только глазами, когда
    рядом с номером написано, что это за машина.
    """
    row = con.execute('SELECT e.name, e.plate, o.name FROM equipment e '
                      'LEFT JOIN organizations o ON o.id = e.organization_id '
                      'WHERE e.id = ?', (equipment_id,)).fetchone()
    if row is None:
        return '?'
    parts = [str(value).strip() for value in row
             if value is not None and str(value).strip()]
    return config.ascii_only(' / '.join(parts))


def apply(con, to_link, to_unlink, note):
    """Одна транзакция: связки, отметки снятия, журнал, пересчёт связи."""
    when = store.stamp(store.utc_now())
    con.execute('BEGIN')
    try:
        for transport_id, plate, equipment_id in to_unlink:
            cursor = con.execute(
                'UPDATE agro_work_transport_links SET unlinked_at = ? '
                'WHERE agro_transport_id = ? AND unlinked_at IS NULL',
                (when, transport_id))
            if cursor.rowcount != 1:
                raise Refused('%s changed while planning - nothing written'
                              % config.ascii_only(plate))
            store.journal(con, store.SOURCE_LINKS, store.ENTITY_LINK,
                          transport_id, 'equipment_id', equipment_id, None, when)
        for transport_id, plate, equipment_id in to_link:
            con.execute('INSERT INTO agro_work_transport_links '
                        '(agro_transport_id, equipment_id, plate_at_link, '
                        'linked_at, note) VALUES (?, ?, ?, ?, ?)',
                        (transport_id, equipment_id, plate, when, note))
            store.journal(con, store.SOURCE_LINKS, store.ENTITY_LINK,
                          transport_id, 'equipment_id', None, equipment_id, when)
        resolution = matching.resolve(store.transport_rows(con),
                                      store.equipment_rows(con),
                                      store.active_links(con))
        store.apply_resolution(con, resolution, when, None,
                               source=store.SOURCE_LINKS)
        con.commit()
    except Exception:
        con.rollback()
        raise


def write_equipment_csv(con, path):
    """Наш справочник техники -- чтобы было откуда взять equipment_id."""
    rows = con.execute(
        'SELECT e.id, e.name, e.plate, o.name, e.category, e.is_active '
        'FROM equipment e LEFT JOIN organizations o ON o.id = e.organization_id '
        'ORDER BY o.name, e.name, e.id').fetchall()
    with open(path, 'w', encoding='utf-8-sig', newline='') as fh:
        writer = csv.writer(fh, delimiter=';')
        writer.writerow(('equipment_id', 'name', 'plate', 'organization',
                         'category', 'is_active'))
        for row in rows:
            writer.writerow(['' if value is None else value for value in row])
    return len(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--db', default=config.DB_PATH)
    parser.add_argument('--equipment-csv', default=None, metavar='FILE',
                        help='write our equipment list (id, name, plate) and '
                             'stop')
    parser.add_argument('--from-csv', default=None, metavar='FILE',
                        help='the unmatched CSV with equipment_id filled in')
    parser.add_argument('--set', action='append', default=[],
                        metavar='PLATE_OR_ID=EQUIPMENT_ID')
    parser.add_argument('--unset', action='append', default=[],
                        metavar='PLATE_OR_ID')
    parser.add_argument('--note', default='', help='kept with the link')
    parser.add_argument('--apply', action='store_true',
                        help='write; without it nothing is written')
    args = parser.parse_args(argv)
    if not os.path.exists(args.db):
        sys.stderr.write('ERROR: database not found at %s - refusing to run\n'
                         % args.db)
        return 2
    con = store.connect(args.db)
    con.isolation_level = None
    try:
        missing = store.missing_tables(con)
        if missing:
            sys.stderr.write('ERROR: tables missing: %s - run '
                             'migrate_agro_work_001.py first\n'
                             % ', '.join(missing))
            return 2
        if args.equipment_csv:
            count = write_equipment_csv(con, args.equipment_csv)
            print('our equipment: %d row(s) written to %s'
                  % (count, config.ascii_only(args.equipment_csv)))
            return 0
        try:
            sets = parse_pairs(args.set)
            if args.from_csv:
                sets.extend(read_decisions_csv(args.from_csv))
            to_link, to_unlink = plan(con, sets, args.unset)
        except (Refused, OSError) as exc:
            sys.stderr.write('ERROR: %s\n' % exc)
            print('nothing written')
            return 2
        for transport_id, plate, equipment_id in to_unlink:
            print('unlink %s (was equipment %d: %s)'
                  % (config.ascii_only(plate), equipment_id,
                     describe_equipment(con, equipment_id)))
        for transport_id, plate, equipment_id in to_link:
            print('link   %s -> equipment %d (%s)'
                  % (config.ascii_only(plate), equipment_id,
                     describe_equipment(con, equipment_id)))
        if not to_link and not to_unlink:
            print('nothing to write')
            return 0
        if not args.apply:
            print('\ndry run: nothing was written. Re-run with --apply to '
                  'write %d link(s) and %d unlink(s).'
                  % (len(to_link), len(to_unlink)))
            return 0
        try:
            apply(con, to_link, to_unlink, args.note[:200] or None)
        except Refused as exc:
            sys.stderr.write('ERROR: %s\n' % exc)
            return 1
        print('\nwritten: %d link(s), %d unlink(s)' % (len(to_link),
                                                       len(to_unlink)))
        summary = store.match_summary(con)
        print('links now: manual %d | auto %d | ambiguous %d | none %d'
              % (summary['manual'], summary['auto'], summary['ambiguous'],
                 summary['none']))
        return 0
    finally:
        con.close()


if __name__ == '__main__':
    sys.exit(main())
