# -*- coding: utf-8 -*-
r"""GPS: пересчитать прошедшие сутки действующим методом -- план и запись.

ЗАЧЕМ
07.10.2026 владелец принял правило A7 («допуск при переполнении», раздел 2.11
дорожной карты): «включаем и пересчитываем сентябрь». Ночной расчёт считает
только вчерашние сутки, поэтому после выпуска прошлые дни так и остались бы
посчитанными прежним методом (`adaptive-alpha-2026-08-12`) -- с ложными
гектарами между дорогами. Этот инструмент пересчитывает окно дней тем же
путём, что ночной расчёт (`gps.daily --date` для каждого дня), и печатает
«было -> стало».

ДВА РЕЖИМА
  без --apply  план, ничего не пишет (база открывается mode=ro): метод, окно,
      по месяцам -- машино-сутки со строкой, опубликованные у считаемых
      объектов и их гектары, исключённые объекты, версии метода в строках;
      ответы операторов «работа/проезд» в окне и разборы нарядов.
  --apply      те же проверки, затем каждые сутки окна по очереди; в конце --
      сводка «было -> стало» по месяцам и то, что осталось непересчитанным.

ЧТО ПЕРЕСЧИТЫВАЕТСЯ, А ЧТО НЕТ
  * Считаемые объекты -- все, у кого в этот день есть точки, ровно как в
    ночном расчёте. Объекты, исключённые владельцем («не наша», непольевая
    категория), ночной расчёт не трогает -- и здесь их строки остаются
    прежним методом: экран их скрывает, в план-факт они не входят.
  * Разборы нарядов (`gps_verdicts`) хранят числа снимком и не меняются:
    записанное человеком задним числом не переписывается.

[REASON]: отказ (код 3), если в окне есть ответы операторов «работа/проезд».
Пересчёт переносит ответ на новый участок по наложению, но ответ на участке,
которого больше нет, терялся бы -- а ответ это ручной труд и обучающий набор.
На 03.10 ответов за сентябрь не было ни одного; если они появились, решает
сессия вместе с владельцем, а не инструмент. Программа во время пересчёта
работает, поэтому ответы проверяются перед каждыми сутками и после них, а
сутки считаются с `gps.daily --keep-answers`: объект-сутки, чей ответ не нашёл
бы нового участка, не перезаписываются вовсе и остаются прежними вместе с
ответом.

КОДЫ ВЫХОДА: 0 -- все сутки окна посчитаны (или план без записи); 2 --
неверный ввод или в папке нет точек ни за один день окна, ничего не
записано; 3 -- отказ из-за ответов операторов: до запуска -- ничего не
записано; посреди окна -- сутки до названных пересчитаны, ответ, данный во
время пересчёта, остаётся в базе, сутки после названных не пересчитаны; 5 --
часть суток не посчиталась из-за сбоя: остальные посчитаны, сбойные
перечислены с причиной, их можно пересчитать повтором той же команды (журнал
лучше дописывать, а не перезаписывать: `*>>`).

Запуск -- из окружения расчёта, PowerShell, из C:\transport-report после
выпуска (пишет в рабочую базу только выпущенный код):

    & C:\gps_venv\Scripts\python.exe -u tools\gps_recompute_days.py --since 2026-09-01

    & C:\gps_venv\Scripts\python.exe -u tools\gps_recompute_days.py --since 2026-09-01 --apply

По умолчанию окно кончается вчерашними сутками по местному времени; база и
папка точек -- те же, что у ночного расчёта. Вывод -- только ASCII.
"""

import argparse
import contextlib
import io
import os
import sqlite3
import sys
import traceback
from collections import defaultdict
from datetime import datetime, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from gps import daily                                               # noqa: E402
from gps.area import METHOD_VERSION, PREVIOUS_METHOD_VERSION        # noqa: E402
from gps.exclusion import excluded_units                            # noqa: E402
from gps_collector import config as collector_config               # noqa: E402
from gps_collector import storage                                    # noqa: E402

# [REASON]: допуск на сложение гектаров с плавающей точкой: «стало больше»
# -- признак, который печатается поимённо, и шум округления его не должен
# поднимать.
EPSILON = 1e-6
LISTED = 20
# [REASON]: строка, которой `gps.daily.run_day` сообщает о потерянных ответах
# операторов. Сутки здесь считаются с --keep-answers, и ответ не теряется, а
# объект-сутки остаются прежними; строка -- последняя страховка, если это
# когда-нибудь сломается. Слова берутся из самого расчёта, а не копией.
DROPPED_MARK = daily.DROPPED_MARK


class _Lines(io.TextIOBase):
    """Вывод `gps.daily` -- дальше построчно, по мере печати.

    [REASON]: вывод суток копился целиком и печатался после них. Если процесс
    умирал посреди суток (окно закрыли, Ctrl+C, перезагрузка), строки суток --
    и строка о потерянном ответе, и причина сбоя -- не доходили до журнала.
    """

    def __init__(self, out, real):
        super().__init__()
        self.out, self.real, self.rest, self.dropped = out, real, '', 0

    def writable(self):
        return True

    def write(self, text):
        self.rest += text
        while '\n' in self.rest:
            line, self.rest = self.rest.split('\n', 1)
            self._pass(line)
        return len(text)

    def finish(self):
        if self.rest:
            line, self.rest = self.rest, ''
            self._pass(line)

    def _pass(self, line):
        if DROPPED_MARK in line:
            self.dropped += 1
        # `out` по умолчанию -- print, а sys.stdout сейчас -- этот объект.
        with contextlib.redirect_stdout(self.real):
            self.out(line)


def window(since, until):
    first = datetime.strptime(since, '%Y-%m-%d')
    last = datetime.strptime(until, '%Y-%m-%d')
    return [(first + timedelta(days=n)).strftime('%Y-%m-%d')
            for n in range((last - first).days + 1)]


def open_readonly(path):
    return sqlite3.connect('file:%s?mode=ro' % path, uri=True, timeout=30)


def snapshot(db, since, until):
    """(день, объект) -> (причина, версия метода, гектары участков)."""
    con = open_readonly(db)
    try:
        hectares = defaultdict(float)
        for day, unit, area in con.execute(
                'SELECT work_date, wialon_id, area_ha FROM gps_work_polygons '
                'WHERE work_date >= ? AND work_date <= ?', (since, until)):
            hectares[(str(day), int(unit))] += float(area)
        rows = {}
        for day, unit, reason, version in con.execute(
                'SELECT work_date, wialon_id, reason, method_version '
                'FROM gps_daily_aggregates WHERE work_date >= ? AND work_date <= ?',
                (since, until)):
            key = (str(day), int(unit))
            rows[key] = (reason, version, round(hectares.get(key, 0.0), 4))
        excluded = set(excluded_units(con))
    finally:
        con.close()
    return rows, excluded


def answers_in_window(db, since, until):
    """(ответы операторов, разборы нарядов) в окне -- только чтение."""
    con = open_readonly(db)
    try:
        answers = [(str(day), int(unit), site, label) for day, unit, site, label
                   in con.execute(
                       "SELECT work_date, wialon_id, site_number, operator_label "
                       "FROM gps_work_polygons WHERE work_date >= ? "
                       "AND work_date <= ? AND operator_label IS NOT NULL "
                       "AND operator_label != '' ORDER BY work_date, wialon_id",
                       (since, until))]
        try:
            verdicts = con.execute(
                'SELECT COUNT(*) FROM gps_verdicts WHERE work_date >= ? '
                'AND work_date <= ?', (since, until)).fetchone()[0]
        except sqlite3.OperationalError:
            verdicts = 0
    finally:
        con.close()
    return answers, verdicts


def month_lines(rows, excluded):
    """По месяцам: (строк, опубликовано у считаемых, их га, исключённых,
    версии метода)."""
    months = defaultdict(lambda: {'rows': 0, 'published': 0, 'ha': 0.0,
                                  'excluded': 0, 'versions': defaultdict(int)})
    for (day, unit), (reason, version, ha) in rows.items():
        month = months[day[:7]]
        month['rows'] += 1
        month['versions'][version] += 1
        if unit in excluded:
            month['excluded'] += 1
        elif reason is None:
            month['published'] += 1
            month['ha'] += ha
    return months


def print_plan(days, rows, excluded, answers, verdicts, out):
    out('method of this code: %s (previous: %s)'
        % (METHOD_VERSION, PREVIOUS_METHOD_VERSION))
    out('window: %s .. %s, %d day(s)' % (days[0], days[-1], len(days)))
    for month, info in sorted(month_lines(rows, excluded).items()):
        out('  %s: rows %d; published at counted objects %d, %.2f ha; rows of '
            'excluded objects %d (left as they are); method versions: %s'
            % (month, info['rows'], info['published'], info['ha'],
               info['excluded'],
               ', '.join('%s %d' % pair
                         for pair in sorted(info['versions'].items()))))
    out('operator answers (work/passage) in the window: %d' % len(answers))
    for day, unit, site, label in answers[:LISTED]:
        out('  answer %s unit %d site %d: %s'
            % (day, unit, site, collector_config.ascii_only(label)))
    out('work-order reviews in the window: %d (they keep their numbers, a '
        'recompute does not touch them)' % verdicts)


def compare(before, after, excluded, out):
    """Сводка «было -> стало» по месяцам; возвращает число непересчитанных."""
    months = defaultdict(lambda: [0.0, 0.0, 0, 0])
    increased, appeared, left_old = [], [], []
    # [REASON]: «было» читается из базы в начале КАЖДОГО запуска. Если это
    # повтор после прерванного или сбойного прогона -- или ночной расчёт уже
    # посчитал новым кодом сутки после выпуска, -- часть окна уже на новом
    # методе, и сводка покажет только остаток. Это надо сказать прямо, не
    # угадывая, какая из двух причин.
    earlier = sum(1 for (day, unit), row in before.items()
                  if unit not in excluded and row[1] == METHOD_VERSION)
    for key in sorted(set(before) | set(after)):
        day, unit = key
        if unit in excluded:
            continue
        old, new = before.get(key), after.get(key)
        month = months[day[:7]]
        if old is not None and old[0] is None:
            month[0] += old[2]
            month[2] += 1
        if new is not None and new[0] is None:
            month[1] += new[2]
            month[3] += 1
        if old is None and new is not None:
            appeared.append(key)
        if (old is not None and new is not None and old[0] is None
                and new[0] is None and new[2] > old[2] + EPSILON):
            increased.append((key, old[2], new[2]))
        if new is not None and new[1] != METHOD_VERSION:
            left_old.append((key, new[1]))
    out('')
    if earlier:
        out('rows of counted objects already on %s before this run: %d (an '
            'earlier run of this tool, or the nightly computation since the '
            'release) -- "before" below is what was left' % (METHOD_VERSION,
                                                            earlier))
    out('counted objects, published machine-days and their hectares, before '
        '-> after:')
    for month, (ha_before, ha_after, days_before, days_after) in sorted(
            months.items()):
        out('  %s: %.2f ha -> %.2f ha (%+.2f); published machine-days %d -> %d'
            % (month, ha_before, ha_after, ha_after - ha_before, days_before,
               days_after))
    out('machine-days with more hectares than before: %d (the rule never adds '
        'any; more can come only from points that arrived after the day was '
        'first computed)' % len(increased))
    for (day, unit), old, new in increased[:LISTED]:
        out('  %s unit %d: %.4f -> %.4f ha' % (day, unit, old, new))
    out('rows that appeared (points but no row before): %d' % len(appeared))
    out('rows of counted objects still not on %s: %d'
        % (METHOD_VERSION, len(left_old)))
    for (day, unit), version in left_old[:LISTED]:
        out('  %s unit %d: %s' % (day, unit, version))
    return len(left_old)


def _day(option, value):
    try:
        return datetime.strptime(value, '%Y-%m-%d').strftime('%Y-%m-%d')
    except (TypeError, ValueError):
        sys.stderr.write('ERROR: %s must look like YYYY-MM-DD\n' % option)
        return None


def recompute_day(day, db, folder, out):
    """Одни сутки путём ночного расчёта: (код, потеряно ответов)."""
    lines = _Lines(out, sys.stdout)
    try:
        with contextlib.redirect_stdout(lines):
            code = daily.main(['--date', day, '--db', db, '--dir', folder,
                               '--keep-answers'])
    except Exception as error:                          # noqa: BLE001
        # [REASON]: падение одних суток не должно оборвать окно без сводки:
        # сутки записываются в сбойные, остальные считаются дальше. Причина
        # печатается целиком: по одному имени исключения не отличить
        # занятую базу, которую лечит повтор, от ошибки, которую он не лечит.
        lines.finish()
        for line in traceback.format_exc().splitlines():
            out(collector_config.ascii_only(line))
        code = 'crash: %s: %s' % (type(error).__name__,
                                  collector_config.ascii_only(str(error))[:160])
    finally:
        lines.finish()
    return code, lines.dropped


def main(argv=None, out=print, today=None):
    parser = argparse.ArgumentParser(
        description='GPS: recompute past days by the current method - plan '
                    'without --apply, write with it.')
    parser.add_argument('--since', required=True, help='first day, YYYY-MM-DD')
    parser.add_argument('--until', help='last day, YYYY-MM-DD (default: '
                                        'yesterday, local)')
    parser.add_argument('--db', default=daily.DB_PATH, help='transport.db')
    parser.add_argument('--dir', default=None,
                        help='folder with gps_points_YYYYMM.db files')
    parser.add_argument('--apply', action='store_true',
                        help='recompute; without it nothing is written')
    args = parser.parse_args(argv)
    today = today or datetime.now(collector_config.TZ).strftime('%Y-%m-%d')
    yesterday = (datetime.strptime(today, '%Y-%m-%d')
                 - timedelta(days=1)).strftime('%Y-%m-%d')
    since = _day('--since', args.since)
    until = yesterday if args.until is None else _day('--until', args.until)
    if since is None or until is None:
        return 2
    if since > until:
        sys.stderr.write('ERROR: --since %s is after --until %s\n' % (since, until))
        return 2
    # [REASON]: сегодняшние сутки ещё идут, а вчерашние ночной расчёт уже
    # посчитал действующим кодом. Окно дальше вчера -- это сутки, которые
    # ночной расчёт посчитает сам, и посчитанные наполовину.
    if until > yesterday:
        sys.stderr.write('ERROR: --until %s is not in the past: the last day '
                         'that can be recomputed is %s\n' % (until, yesterday))
        return 2
    folder = args.dir or daily.points_dir()
    if not os.path.isfile(args.db):
        sys.stderr.write('ERROR: database not found: %s\n' % args.db)
        return 2
    if not os.path.isdir(folder):
        sys.stderr.write('ERROR: points folder not found: %s\n' % folder)
        return 2

    days = window(since, until)
    before, excluded = snapshot(args.db, since, until)
    answers, verdicts = answers_in_window(args.db, since, until)
    print_plan(days, before, excluded, answers, verdicts, out)
    with_points = [day for day in days if storage.units_with_points(folder, day)]
    out('days with points in %s: %d of %d'
        % (collector_config.ascii_only(folder), len(with_points), len(days)))
    # [REASON]: папка без файлов точек -- не «пересчитано, ничего не
    # изменилось»: ночной расчёт на каждом дне ответит «нет точек» и вернёт 0.
    # Такой запуск -- неверный ввод, а не успех.
    if not with_points:
        out('ERROR: no point file holds any day of the window: is --dir the '
            'folder with gps_points_YYYYMM.db?')
        return 2
    if not args.apply:
        out('PLAN ONLY: nothing was written. Add --apply to recompute.')
        return 0
    if answers:
        out('REFUSED: %d operator answer(s) in the window would be carried or '
            'lost by a recompute. Nothing was written; ask the session.'
            % len(answers))
        return 3

    failed, refused = [], None
    for index, day in enumerate(days, 1):
        out('')
        out('== %s (%d of %d)' % (day, index, len(days)))
        appeared, _ = answers_in_window(args.db, day, day)
        if appeared:
            refused = ('%d operator answer(s) appeared on %s while the window '
                       'was being recomputed; %s and the days after it were NOT '
                       'recomputed' % (len(appeared), day, day))
            break
        code, dropped = recompute_day(day, args.db, folder, out)
        if code != 0:
            failed.append((day, code))
        if dropped:
            # [REASON]: с --keep-answers этого быть не должно. Если всё же
            # случилось, ответ дан во время пересчёта: в копии 4.2 его нет,
            # вернуть его может только оператор.
            refused = ('the computation reports %d operator answer(s) lost on '
                       '%s: they were given during the recompute, so the backup '
                       'taken before it does not hold them either - the '
                       'operator has to answer again; %s was recomputed, the '
                       'days after it were NOT' % (dropped, day, day))
            break
        # [REASON]: проверка перед сутками не видит ответа, данного, пока эти
        # сутки считались. Такой ответ не теряется (объект-сутки с ним либо
        # перенесли его на новый участок, либо остались прежними), но окно
        # останавливается так же, как при ответе перед сутками.
        given, _ = answers_in_window(args.db, day, day)
        if given:
            refused = ('%d operator answer(s) were given on %s while it was '
                       'being recomputed; they are in the database - on the new '
                       'site or, where it is gone, with the old rows of that '
                       'object; the days after %s were NOT recomputed'
                       % (len(given), day, day))
            break
    after, excluded_after = snapshot(args.db, since, until)
    left_old = compare(before, after, excluded | excluded_after, out)
    out('days without points (nothing to recompute there): %d'
        % (len(days) - len(with_points)))
    out('')
    if failed:
        # [REASON]: при отказе повтор упрётся в те же ответы, поэтому совет
        # «повторить» печатается только без отказа.
        out('days that did not compute completely: %s%s'
            % (', '.join('%s (exit %s)' % pair for pair in failed),
               '' if refused else ' -- run the same command again, it '
               'recomputes them'))
    if refused:
        out('REFUSED: %s. Ask the session.' % refused)
        return 3
    if failed:
        out('RESULT: RECOMPUTED WITH FAILURES')
        return 5
    out('RESULT: RECOMPUTED %d day(s) by %s; rows of counted objects left on '
        'another method: %d' % (len(days), METHOD_VERSION, left_old))
    return 0


if __name__ == '__main__':
    sys.exit(main())
