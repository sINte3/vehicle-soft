# -*- coding: utf-8 -*-
"""GPS-37 -- приёмка недельной выгрузки 1С: скрипт против соседнего чата. Только чтение.

ЗАЧЕМ
Недельную выгрузку для GPS-35 будет делать по расписанию скрипт
`tools/onec_export.ps1` -- запросами через COM, повторяющими схемы двух
отчётов (разведка 29.10). Прежде чем он заменит соседний чат, его выгрузка
за неделю, которую чат уже выгрузил (19--25.10), должна совпасть с выгрузкой
чата строка в строку. Условие записано в треке до прогона (§0.6,
«Приёмка недельной выгрузки 1С»).

ЧТО СРАВНИВАЕТ
Строка рейса -- (пункт, госномер, время до секунды, масса): вывоз -- «Вес
нетто, кг», реестр -- «Масса физическая,кг». Пункт и госномер -- как их
читают GPS-34/35 (`read_trips`). Строки выгрузки скрипта по организациям,
которых нет у чата, -- отдельным числом, в расхождения не идут: чат выгружал
реестр двух организаций, скрипт берёт все.

ВЕРДИКТ последней строкой
  VERDICT EXPORT: MATCH   -- у обоих видов нет ни недостающих, ни лишних строк;
  VERDICT EXPORT: DIFFERS -- иначе; с --csv расходящиеся строки пишутся в файл.

КОДЫ ВЫХОДА: 0 -- вердикт напечатан (любой); 2 -- неверный ввод; 1 -- упал.

Запуск -- системный Python, из C:\\gps-tools, по одной команде на строку:

  & "C:\\Program Files\\Python314\\python.exe" C:\\gps-tools\\tools\\gps_onec_export_check.py --expected-vyvoz C:\\gps-tools\\check\\onec_vyvoz_po_brutto_nedelya.xlsx --expected-reestr C:\\gps-tools\\check\\onec_pk17_reestr_nedelya.xlsx --vyvoz C:\\gps-tools\\check\\export_test\\onec_vyvoz_po_brutto_nedelya.csv --reestr C:\\gps-tools\\check\\export_test\\onec_pk17_reestr_nedelya.csv --csv C:\\gps-tools\\check\\export_check.csv

В консоль -- только числа, ASCII.
"""

import argparse
import csv
import os
import sys
from collections import Counter

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from gps_collector import config as collector_config                # noqa: E402
from tools.gps_onec_hauling_report import MASS, parse_mass          # noqa: E402
from tools.gps_onec_trips_check import (REESTR, VYVOZ, BadInput,    # noqa: E402
                                        read_trips)

EXIT_OK = 0
EXIT_CRASHED = 1
EXIT_BAD_INPUT = 2

ORG = {VYVOZ: 'Завод', REESTR: 'Организация (завод)'}
MATCH = 'MATCH'
DIFFERS = 'DIFFERS'
CSV_COLUMNS = ('vid', 'storona', 'punkt', 'gosnomer', 'vremya', 'massa', 'organizaciya')


def rows_of(path, kind):
    """[(ключ строки, организация, масса)] выгрузки."""
    out = []
    for trip in read_trips(path, kind, extra=(MASS[kind], ORG[kind])):
        mass = parse_mass(trip['extra'][MASS[kind]])
        org = ' '.join((trip['extra'][ORG[kind]] or '').split())
        when = trip['time'].strftime('%Y-%m-%d %H:%M:%S') if trip['time'] else ''
        out.append(((trip['point_key'], trip['plate'], when, round(mass, 3)), org, mass))
    return out


def compare(expected, exported):
    """Сравнение одного вида: числа и расходящиеся ключи."""
    orgs = {org for _key, org, _mass in expected}
    inside = [row for row in exported if row[1] in orgs]
    other = [row for row in exported if row[1] not in orgs]
    want = Counter((key, org) for key, org, _mass in expected)
    have = Counter((key, org) for key, org, _mass in inside)
    return {
        'expected': len(expected), 'expected_kg': sum(r[2] for r in expected),
        'exported': len(inside), 'exported_kg': sum(r[2] for r in inside),
        'missing': want - have, 'extra': have - want,
        'other': len(other), 'other_kg': sum(r[2] for r in other),
        'other_orgs': len({row[1] for row in other}),
    }


def kg(value):
    return ('%.3f' % value).rstrip('0').rstrip('.')


def write_differences(path, results):
    with open(path, 'w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.writer(handle, delimiter=';')
        writer.writerow(CSV_COLUMNS)
        for kind, result in results:
            for side, keys in (('net_v_skripte', result['missing']),
                               ('lishnyaya_v_skripte', result['extra'])):
                for (key, org), count in sorted(keys.items()):
                    point, plate, when, mass = key
                    for _ in range(count):
                        writer.writerow((kind, side, point, plate, when, kg(mass), org))


def check_inputs(args):
    files = (args.expected_vyvoz, args.expected_reestr, args.vyvoz, args.reestr)
    for path in files:
        if not os.path.isfile(path):
            raise BadInput('file not found: %s' % path)
    if args.csv:
        out = os.path.abspath(args.csv)
        if (not os.path.isdir(os.path.dirname(out)) or os.path.isdir(out)
                or out in {os.path.abspath(p) for p in files}):
            raise BadInput('cannot write %s' % args.csv)


def run(args, out=print):
    check_inputs(args)
    results = []
    for kind, expected, exported in ((VYVOZ, args.expected_vyvoz, args.vyvoz),
                                     (REESTR, args.expected_reestr, args.reestr)):
        results.append((kind, compare(rows_of(expected, kind), rows_of(exported, kind))))
    differs = False
    for kind, r in results:
        missing, extra = sum(r['missing'].values()), sum(r['extra'].values())
        differs = differs or bool(missing or extra)
        out('%s: expected %d rows / %s kg | export %d rows / %s kg | missing %d | extra %d '
            '| other organizations in the export: %d (%d rows / %s kg)'
            % (kind, r['expected'], kg(r['expected_kg']), r['exported'], kg(r['exported_kg']),
               missing, extra, r['other_orgs'], r['other'], kg(r['other_kg'])))
    if args.csv:
        write_differences(args.csv, results)
        out('csv: %s' % collector_config.ascii_only(args.csv))
    out('READ ONLY: nothing was written but the csv')
    out('VERDICT EXPORT: %s' % (DIFFERS if differs else MATCH))
    return EXIT_OK


def main(argv=None, out=print):
    parser = argparse.ArgumentParser(
        description=collector_config.ascii_only(__doc__.splitlines()[0]))
    parser.add_argument('--expected-vyvoz', required=True,
                        help='the removal export the neighbour chat made (xlsx)')
    parser.add_argument('--expected-reestr', required=True,
                        help='the PK-17 register export the neighbour chat made (xlsx)')
    parser.add_argument('--vyvoz', required=True, help='the removal file of onec_export.ps1')
    parser.add_argument('--reestr', required=True, help='the register file of onec_export.ps1')
    parser.add_argument('--csv', help='write the differing rows here')
    args = parser.parse_args(argv)
    try:
        return run(args, out)
    except BadInput as problem:
        sys.stderr.write('ERROR: %s\n' % collector_config.ascii_only(str(problem)))
        return EXIT_BAD_INPUT
    except Exception as problem:                                  # noqa: BLE001
        sys.stderr.write('ERROR: %s: %s\n' % (
            type(problem).__name__, collector_config.ascii_only(str(problem))))
        return EXIT_CRASHED


if __name__ == '__main__':
    sys.exit(main())
