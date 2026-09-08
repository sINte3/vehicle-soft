# -*- coding: utf-8 -*-
"""GPS-LABEL-014 -- ответы оператора с экрана «Факт по технике» для судьи.

ЗАЧЕМ
Экран `/gps/fact` (GPS-3) и есть форма разметки: под каждым участком две
кнопки, ответ ложится в `gps_work_polygons.operator_label` и переживает
пересчёт. Но судья предрегистрированного правила «работа / проезд» --
`tools/gps_label_evaluate.py` -- заморожен и читает два файла старой
HTML-страницы: `otvety.txt` (`site_id;метка`) и `gps_label.csv` (признаки).
Этот скрипт делает оба файла из базы, чтобы партию, размеченную на экране,
судил тот же судья, не тронутый ни строкой.

ЧТО ВЫГРУЖАЕТСЯ
Только участки с ответом «работа» или «проезд». Участок без ответа и участок,
с которого ответ сняли, в партию не входят: судье нужны метки, а не догадки.
Имя машины -- из сопоставления Wialon (GPS-10); без него -- голый id объекта,
чужое имя не подставляется.

Формат `site_id` тот же, что у страницы разметки: `машина|сутки|#номер|га` --
площадь входит в идентификатор нарочно, чтобы ответ нельзя было приклеить к
другому участку после пересчёта.

ТОЛЬКО ЧТЕНИЕ: база открывается в режиме `mode=ro`, и ни один UPDATE здесь
написать негде. Ничего не считается заново: площадь и минуты берутся такими,
какими их видел человек, отвечая.

Запуск (PowerShell, по одной команде на строку):

  cd C:\\transport-report

  & "C:\\Program Files\\Python314\\python.exe" tools\\gps_label_export.py --from 2026-08-20 --to 2026-09-06

  & C:\\gps_venv\\Scripts\\python.exe tools\\gps_label_evaluate.py --answers otvety.txt --features gps_label.csv

Критерий приёмки партии объявлен заранее (§2.8.1 дорожной карты): не меньше
40 участков, из них не меньше 15 проездов. Скрипт печатает, сколько уже есть.

Вывод в консоль -- ASCII: имена машин транслитерируются, в файлах они настоящие.
"""

import argparse
import csv
import os
import pathlib
import sqlite3
import sys

from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gps_collector.config import ascii_only                        # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, 'instance', 'transport.db')

WORK, PASSAGE = 'работа', 'проезд'

# Объявлено заранее в tools/gps_label_evaluate.py; здесь только повторено,
# чтобы счётчик показывал расстояние до цели. Судит судья, не этот скрипт.
ACCEPT_MIN_SITES = 40
ACCEPT_MIN_TRANSITS = 15

FEATURE_COLUMNS = ('site_id', 'machine', 'day', 'site', 'area_ha', 'minutes',
                   'hours_per_ha', 'pass_spacing_m', 'quality_flag',
                   'suggested_label', 'label', 'decided_at', 'wialon_id')


def site_id(machine, day, number, area_ha):
    # [REASON]: тот же формат, что у tools/gps_label_sites.py -- площадь в
    # идентификаторе, чтобы ответ, вернувшийся без своего csv, не лёг на
    # чужой участок после пересчёта.
    return '%s|%s|#%d|%.2f га' % (machine, day, number, area_ha)


def open_read_only(path):
    """Соединение, в котором писать нельзя по конструкции, а не по обещанию."""
    uri = pathlib.Path(path).resolve().as_uri() + '?mode=ro'
    return sqlite3.connect(uri, uri=True, timeout=30)


def _has_table(con, name):
    return con.execute("SELECT name FROM sqlite_master WHERE type='table' "
                       "AND name=?", (name,)).fetchone() is not None


def labelled_sites(con, day_from=None, day_to=None):
    """Участки с ответом человека, с именем машины, где связка есть."""
    names_known = _has_table(con, 'vialon_mappings') and _has_table(con, 'equipment')
    name_sql = ("(SELECT e.name FROM vialon_mappings m JOIN equipment e "
                "ON e.id = m.equipment_id WHERE m.wialon_id = p.wialon_id "
                "AND (m.skip IS NULL OR m.skip = 0) ORDER BY m.id LIMIT 1)"
                if names_known else 'NULL')
    where = ["p.operator_label IN (?, ?)"]
    params = [WORK, PASSAGE]
    if day_from:
        where.append('p.work_date >= ?')
        params.append(day_from)
    if day_to:
        where.append('p.work_date <= ?')
        params.append(day_to)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        'SELECT p.work_date, p.wialon_id, p.site_number, p.area_ha, p.minutes, '
        'p.pass_spacing_m, p.quality_flag, p.suggested_label, p.operator_label, '
        'p.decided_at, %s AS equipment_name FROM gps_work_polygons p '
        'WHERE %s ORDER BY p.work_date, p.wialon_id, p.site_number'
        % (name_sql, ' AND '.join(where)), params).fetchall()
    out = []
    for row in rows:
        machine = row['equipment_name'] or str(row['wialon_id'])
        area = float(row['area_ha'])
        minutes = float(row['minutes'])
        out.append({
            'site_id': site_id(machine, row['work_date'], row['site_number'], area),
            'machine': machine, 'day': row['work_date'],
            'site': row['site_number'],
            'area_ha': round(area, 3), 'minutes': round(minutes, 1),
            'hours_per_ha': (round((minutes / 60.0) / area, 3) if area > 0 else ''),
            'pass_spacing_m': ('' if row['pass_spacing_m'] is None
                               else round(float(row['pass_spacing_m']), 2)),
            'quality_flag': row['quality_flag'] or '',
            'suggested_label': row['suggested_label'] or '',
            'label': row['operator_label'], 'decided_at': row['decided_at'] or '',
            'wialon_id': row['wialon_id']})
    return out


def write_files(rows, answers_path, features_path):
    with open(answers_path, 'w', encoding='utf-8-sig', newline='') as fh:
        for row in rows:
            fh.write('%s;%s\n' % (row['site_id'], row['label']))
    with open(features_path, 'w', encoding='utf-8-sig', newline='') as fh:
        writer = csv.DictWriter(fh, delimiter=';', fieldnames=list(FEATURE_COLUMNS))
        writer.writeheader()
        writer.writerows(rows)


def report(rows, log=print):
    labels = Counter(row['label'] for row in rows)
    works, passages = labels.get(WORK, 0), labels.get(PASSAGE, 0)
    log('labelled sites: %d | work: %d | passage: %d'
        % (len(rows), works, passages))
    if not rows:
        log('nothing labelled yet: answer on /gps/fact first')
        return
    days = len({(row['machine'], row['day']) for row in rows})
    log('machine-days: %d' % days)
    per_machine = Counter(row['machine'] for row in rows)
    for machine, count in per_machine.most_common():
        log('  %-30s %d' % (ascii_only(machine)[:30], count))
    log('batch size: %d of %d %s' % (len(rows), ACCEPT_MIN_SITES,
                                     'OK' if len(rows) >= ACCEPT_MIN_SITES else 'short'))
    log('transits:   %d of %d %s' % (passages, ACCEPT_MIN_TRANSITS,
                                     'OK' if passages >= ACCEPT_MIN_TRANSITS else 'short'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--db', default=DB_PATH)
    parser.add_argument('--from', dest='day_from', default=None,
                        help='first day, YYYY-MM-DD (inclusive)')
    parser.add_argument('--to', dest='day_to', default=None,
                        help='last day, YYYY-MM-DD (inclusive)')
    parser.add_argument('--answers-out', default='otvety.txt')
    parser.add_argument('--features-out', default='gps_label.csv')
    args = parser.parse_args(argv)

    if not os.path.exists(args.db):
        sys.stderr.write('ERROR: database not found at %s\n' % args.db)
        return 2
    con = open_read_only(args.db)
    try:
        if not _has_table(con, 'gps_work_polygons'):
            sys.stderr.write('ERROR: table gps_work_polygons is missing - run '
                             'migrate_gps_daily_001.py first\n')
            return 2
        rows = labelled_sites(con, args.day_from, args.day_to)
    finally:
        con.close()

    report(rows)
    answers_path = os.path.join(os.getcwd(), args.answers_out)
    features_path = os.path.join(os.getcwd(), args.features_out)
    write_files(rows, answers_path, features_path)
    print('written: %s, %s' % (args.answers_out, args.features_out))
    print('judge: gps_label_evaluate.py --answers %s --features %s'
          % (args.answers_out, args.features_out))
    return 0


if __name__ == '__main__':
    sys.exit(main())
