# -*- coding: utf-8 -*-
"""GPS: одно сравнение двух методов площади с ручными замерами владельца.

ЗАЧЕМ
Предрегистрация правила края поля (дорожная карта, 2.12, условие 4) судит
кандидата по набору приёмки с ручными замерами: гектары кандидата пишутся в
CSV и коммитятся ДО вскрытия замеров, потом -- одно сравнение. Этот
инструмент и есть то сравнение, записанное в репозиторий, чтобы его можно было
повторить по файлу, а не по рабочему каталогу сессии.

ВХОД -- CSV набора (`docs/gps_v5_acceptance_set_2026-10-09.csv`): столбцы n,
group, day, machine, manual_ha, manual_note и два столбца гектаров GPS --
действующий метод (--base, по умолчанию gps_ha_a7) и кандидат (--candidate,
по умолчанию gps_ha_edge). Гектары суток -- целиком, как в столбцах: тремя
знаками.

ЧТО СЧИТАЕТСЯ
  Расхождение = (GPS - ручной) / ручной. Строка без числа в manual_ha, но с
  записью в manual_note («Полевые работы не проводились», «замер неполный»)
  в счёт не идёт и печатается отдельно: так решено в предрегистрации --
  по тексту отчёта владельца, а не по числам.
  Допуск -- зелёный В-2 из `gps.tolerance.verdict_for_ga` (тот же, что у
  экранов сверки): |GPS - ручной| <= max(10 %; 0,3 га), граница включительно.

ВЕРДИКТ (условие 4 раздела 2.12; условие 3 раздела 2.13 -- то же)
  FAIL -- медиана |расхождения| у кандидата больше, чем у действующего
  метода, ИЛИ хоть одни сутки, бывшие у действующего метода в зелёном
  допуске, у кандидата ушли в недосчёт за ним. Иначе PASS.
  --min-rows N: в счёт вошло меньше N суток -- NOT CHECKED (код 3), каким бы
  ни вышло сравнение: так раздел 2.13 судит набор В-6 (не меньше 10 из 14).
  --condition -- имя условия в строке вердикта (по умолчанию -- 2.12).
  Отчёт без вердикта: группы (S -- опрыскивание, P -- вспашка, O -- прочие
  почвенные работы) и критерий этапа 2 дорожной карты для обоих методов
  (медиана |расхождения| не больше 5 %, опрыскивание по сумме не хуже -5 %).

КОД ВЫХОДА: 0 -- PASS, 4 -- FAIL, 3 -- NOT CHECKED (мало строк в счёте),
2 -- неверный ввод (ничего не считалось).

Только stdlib и `gps.tolerance` (тоже stdlib): базу и точки не открывает,
ничего не пишет. Вывод -- ASCII.

    python tools/gps_acceptance_compare.py --csv docs/gps_v5_acceptance_set_2026-10-09.csv

    python tools/gps_acceptance_compare.py --csv docs/gps_v6_acceptance_set_2026-10-11.csv --candidate gps_ha_edge_scale --min-rows 10 --condition "roadmap 2.13, condition 3"
"""

import argparse
import csv
import os
import statistics
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from gps.tolerance import VERDICT_OK, verdict_for_ga                 # noqa: E402

REQUIRED = ('n', 'group', 'day', 'machine', 'manual_ha', 'manual_note')
GROUPS = (('S', 'spraying'), ('P', 'ploughing'), ('O', 'other soil work'))
# [REASON]: критерий этапа 2 дорожной карты (раздел 6) -- записан до
# набора В-5 и здесь только печатается рядом с вердиктом, не судит кандидата.
STAGE2_MEDIAN_PCT = 5.0
STAGE2_SPRAYING_PCT = -5.0
PASS, FAIL, NOT_CHECKED = 'PASS', 'FAIL', 'NOT CHECKED'
CONDITION = 'roadmap 2.12, condition 4'


class BadInput(Exception):
    pass


def load(path, base, candidate):
    """Строки набора: dict n, group, day, machine, manual (или None), note,
    base, candidate. Неверный файл -- BadInput."""
    with open(path, encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        absent = [name for name in REQUIRED + (base, candidate)
                  if name not in (reader.fieldnames or [])]
        if absent:
            raise BadInput('no column(s): %s' % ', '.join(absent))
        rows = []
        for line, row in enumerate(reader, 2):
            note = (row['manual_note'] or '').strip()
            text = (row['manual_ha'] or '').strip()
            try:
                item = {'n': int(row['n']), 'group': row['group'].strip(),
                        'day': row['day'].strip(), 'machine': row['machine'].strip(),
                        'manual': float(text) if text else None, 'note': note,
                        'base': float(row[base]), 'candidate': float(row[candidate])}
            except (AttributeError, TypeError, ValueError):
                raise BadInput('line %d is not a row of the set' % line)
            # [REASON]: строка выходит из счёта только по записи владельца;
            # пустое число без записи -- ошибка файла, а не «работы не было».
            if item['manual'] is None and not note:
                raise BadInput('line %d has neither a hand measurement nor a note'
                               % line)
            if item['manual'] is not None and item['manual'] <= 0:
                raise BadInput('line %d: the hand measurement must be positive' % line)
            rows.append(item)
    if not rows:
        raise BadInput('no rows')
    return rows


def compare(rows):
    """Счёт сравнения: (вердикт, подробности)."""
    counted = [row for row in rows if row['manual'] is not None]
    out = []
    for row in counted:
        green_base = verdict_for_ga(row['manual'], row['base'])[0] == VERDICT_OK
        green_cand = verdict_for_ga(row['manual'], row['candidate'])[0] == VERDICT_OK
        out.append(dict(row,
                        dev_base=100.0 * (row['base'] - row['manual']) / row['manual'],
                        dev_cand=100.0 * (row['candidate'] - row['manual'])
                        / row['manual'],
                        green_base=green_base, green_cand=green_cand))
    median_base = statistics.median(abs(r['dev_base']) for r in out) if out else None
    median_cand = statistics.median(abs(r['dev_cand']) for r in out) if out else None
    fell = [r['n'] for r in out if r['green_base'] and not r['green_cand']
            and r['candidate'] < r['manual']]
    worse = out and median_cand > median_base
    verdict = FAIL if (worse or fell) else PASS
    return verdict, {'rows': out, 'out_of_count': [r for r in rows
                                                   if r['manual'] is None],
                     'median_base': median_base, 'median_cand': median_cand,
                     'fell': fell, 'worse': bool(worse)}


def group_line(found, code, title):
    rows = [r for r in found['rows'] if r['group'] == code]
    if not rows:
        return None
    manual = sum(r['manual'] for r in rows)
    return ('  %s %-15s rows %d: median |dev| base %.1f%% -> candidate %.1f%%; '
            'sum base %+.1f%% -> candidate %+.1f%%'
            % (code, title, len(rows),
               statistics.median(abs(r['dev_base']) for r in rows),
               statistics.median(abs(r['dev_cand']) for r in rows),
               100.0 * (sum(r['base'] for r in rows) / manual - 1),
               100.0 * (sum(r['candidate'] for r in rows) / manual - 1)))


def stage2(found, key, dev):
    rows = found['rows']
    median = statistics.median(abs(r[dev]) for r in rows)
    spray = [r for r in rows if r['group'] == 'S']
    spray_pct = (100.0 * (sum(r[key] for r in spray)
                          / sum(r['manual'] for r in spray) - 1) if spray else None)
    held = (median <= STAGE2_MEDIAN_PCT
            and (spray_pct is None or spray_pct >= STAGE2_SPRAYING_PCT))
    return median, spray_pct, held


def report(verdict, found, base, candidate, out, condition=CONDITION,
           min_rows=None):
    out('base %s, candidate %s; tolerance: gps.tolerance green band (B-2)'
        % (base, candidate))
    out('%3s %-2s %-10s %8s %8s %9s %8s %8s  %-5s %-5s'
        % ('n', 'g', 'day', 'manual', 'base', 'candidate', 'dev b', 'dev c',
           'green', 'green'))
    for r in found['rows']:
        out('%3d %-2s %-10s %8.3f %8.3f %9.3f %+7.1f%% %+7.1f%%  %-5s %-5s'
            % (r['n'], r['group'], r['day'], r['manual'], r['base'],
               r['candidate'], r['dev_base'], r['dev_cand'],
               'yes' if r['green_base'] else 'no',
               'yes' if r['green_cand'] else 'no'))
    for r in found['out_of_count']:
        out('%3d %-2s %-10s out of the count by the owner\'s note; base %.3f, '
            'candidate %.3f' % (r['n'], r['group'], r['day'], r['base'],
                                r['candidate']))
    out('counted rows: %d; median |deviation|: base %.2f%% -> candidate %.2f%%'
        % (len(found['rows']), found['median_base'], found['median_cand']))
    out('green under base -> undercount beyond green under the candidate: %s'
        % (', '.join('n=%d' % n for n in found['fell']) or 'none'))
    out('groups, no verdict:')
    for code, title in GROUPS:
        line = group_line(found, code, title)
        if line:
            out(line)
    for key, dev, name in (('base', 'dev_base', 'base'),
                           ('candidate', 'dev_cand', 'candidate')):
        median, spray, held = stage2(found, key, dev)
        out('stage 2 criterion (median <= %.0f%%, spraying sum >= %.0f%%), %s: '
            'median %.1f%%, spraying %s -- %s'
            % (STAGE2_MEDIAN_PCT, STAGE2_SPRAYING_PCT, name, median,
               '-' if spray is None else '%+.1f%%' % spray,
               'met' if held else 'not met'))
    reasons = []
    if min_rows is not None and len(found['rows']) < min_rows:
        # [REASON]: предрегистрация 2.13 судит набор, а не горсть суток:
        # меньше min_rows строк в счёте -- условие не проверено, и найденное
        # выше печатается, но вердикта не даёт ни в одну сторону.
        out('VERDICT (%s): %s -- %d counted row(s), at least %d are needed; the '
            'comparison above is printed, not judged'
            % (condition, NOT_CHECKED, len(found['rows']), min_rows))
        return NOT_CHECKED
    if found['worse']:
        reasons.append('the median |deviation| grows')
    if found['fell']:
        reasons.append('%d machine-day(s) leave the green band into undercount'
                       % len(found['fell']))
    out('VERDICT (%s): %s%s'
        % (condition, verdict, ' -- ' + '; '.join(reasons) if reasons else ''))
    return verdict


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--csv', required=True, help='the acceptance set csv')
    parser.add_argument('--base', default='gps_ha_a7',
                        help='column of the method in force')
    parser.add_argument('--candidate', default='gps_ha_edge',
                        help='column of the candidate')
    parser.add_argument('--min-rows', type=int, default=None,
                        help='fewer counted rows than this: NOT CHECKED')
    parser.add_argument('--condition', default=CONDITION,
                        help='the name of the condition in the verdict line')
    args = parser.parse_args(argv)
    if args.min_rows is not None and args.min_rows < 1:
        sys.stderr.write('ERROR: --min-rows must be at least 1\n')
        return 2
    if not os.path.isfile(args.csv):
        sys.stderr.write('ERROR: not found: %s\n' % args.csv)
        return 2
    try:
        rows = load(args.csv, args.base, args.candidate)
    except BadInput as error:
        sys.stderr.write('ERROR: %s\n' % error)
        return 2
    verdict, found = compare(rows)
    if not found['rows']:
        sys.stderr.write('ERROR: no row with a hand measurement\n')
        return 2
    verdict = report(verdict, found, args.base, args.candidate, print,
                     args.condition, args.min_rows)
    return {PASS: 0, NOT_CHECKED: 3}.get(verdict, 4)


if __name__ == '__main__':
    sys.exit(main())
