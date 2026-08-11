#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/drone_reattach_evidence.py -- DRONE-REATTACH-001: улики по вылетам,
у которых машина не определена. ЧИТАЕТ И НЕ ПИШЕТ НИЧЕГО.

Зачем. 5 977 вылетов (6 092.68 га по замеру трека) лежат с
drone_unit_id IS NULL под сорока старыми написаниями ника. В старой схеме ник
называл не машину: где имя оператора (`Q.Nurali`), где подразделение со своим
внутренним номером (`GardenN6`), и эти номера НЕ совпадают с парковыми. Какая
машина стоит за таким написанием -- знание владельца, а не вывод из данных, и
выдумывать его нельзя (устав, «не придумывать бизнес-правила»).

Поэтому скрипт не переназначает ничего. Он готовит материал для решения и
отделяет то, что ВЫВОДИТСЯ МЕХАНИЧЕСКИ, от того, что решает человек:

  ГОМОГЛИФ   написание совпадает с активным алиасом посимвольно, если свести
             кириллические двойники к латинским (О->O, С->C, Р->P и т.д.).
             Это тождество СТРОК, а не бизнес-правило: «8 Gаrden» с
             кириллической «а» и «8 Garden» -- одно и то же слово, набранное
             в двух раскладках. Названа машина алиаса.
  СЕМЬЯ      несколько НЕРАСПОЗНАННЫХ написаний сводятся друг к другу тем же
             тождеством: сорок написаний -- это меньше сорока настоящих имён.
  СПОРНО     ключ сводится к двум и более машинам. Скрипт останавливается и
             не гадает -- ровно как приём (_drone_resolve_unit).
  НЕИЗВЕСТНО совпадений нет. Печатаются улики: период, месяцы, гектары,
             области, состав алфавитов, цифры в нике -- то, из чего владелец
             узнаёт бригаду и машину.

Свод гомоглифов НАМЕРЕННО КОНСЕРВАТИВЕН: только пары, неразличимые на глаз в
обычном шрифте (А/A, В/B, Е/E, К/K, М/M, Н/H, О/O, Р/P, С/C, Т/T, У/Y, Х/X,
І/I, Ј/J, Ѕ/S и строчные а/a, е/e, о/o, р/p, с/c, у/y, х/x, і/i, ј/j, ѕ/s).
Строчные к/k, м/m, н/h, т/t, в/b в свод НЕ входят: в большинстве шрифтов они
различимы, а цена ложного сведения -- вылеты, приписанные чужой машине.
Буквы `ў`, `ғ`, `қ`, `ҳ` не имеют латинских двойников и не сводятся никогда,
поэтому «Мирзачўл» не может быть сведён к «Mirzachul» -- это разные строки, и
решение по ним остаётся за владельцем.

Ключ считается как fold(гомоглифы) -> normalized(lower + удаление ВСЕХ
пробелов). Второй шаг -- дословно правило приёма
(`_drone_normalize_nickname` в drones.py); свой нормализатор здесь не
заводится, иначе инструмент и приём разошлись бы в понимании того, что такое
одно и то же написание.

ЧТО ДЕЛАТЬ С ВЫВОДОМ. Скрипт ничего не меняет. Строка «ГОМОГЛИФ» -- готовый
кандидат для существующего пути с откатом:
  1. /drones/units -- добавить написание алиасом к названной машине;
  2. /drones/units/reattach -- предпросмотр (пишет ноль), сверить числа;
  3. «Применить» -- запись одной транзакцией с журналом прогона;
  4. «Откатить» -- возврат ровно тех строк, что были изменены.
Другого пути записи в треке нет и заводить его этот инкремент не стал:
у существующего есть журнал и откат, у нового не было бы ни того, ни другого.

Запуск (сервер, из каталога установки):
  & "C:\\Program Files\\Python314\\python.exe" tools\\drone_reattach_evidence.py --db instance\\transport.db
  & "C:\\Program Files\\Python314\\python.exe" tools\\drone_reattach_evidence.py --db instance\\transport.db --report C:\\qa\\reattach_evidence.txt

ОТКАТ НЕ ТРЕБУЕТСЯ: база открывается через file:-URI в режиме `mode=ro`,
транзакций не открывается, ни одного INSERT/UPDATE/DELETE в файле нет.
Откат кода -- git revert коммита.

Консоль -- только ASCII: кириллица уходит escape-последовательностями
(службы на сервере пишут журнал в cp1251). Полный читаемый вывод -- в отчёте
UTF-8, он же остаётся приложением к решению владельца.
"""

import argparse
import collections
import datetime
import os
import sqlite3
import sys

# Сведение кириллических двойников к латинским. Ключ -- кириллица, значение --
# латиница. Прописные и строчные заданы раздельно: строчные к/м/н/т/в в своде
# отсутствуют намеренно (см. модульный докстринг).
HOMOGLYPHS = {
    'А': 'A', 'В': 'B', 'Е': 'E', 'К': 'K', 'М': 'M', 'Н': 'H', 'О': 'O',
    'Р': 'P', 'С': 'C', 'Т': 'T', 'У': 'Y', 'Х': 'X', 'І': 'I', 'Ј': 'J',
    'Ѕ': 'S',
    'а': 'a', 'е': 'e', 'о': 'o', 'р': 'p', 'с': 'c', 'у': 'y', 'х': 'x',
    'і': 'i', 'ј': 'j', 'ѕ': 's',
}

# Смещение показа времени: вылеты хранятся в UTC, холдинг живёт в UTC+5.
# Та же константа, что в drones.py; здесь она нужна только для дат в отчёте.
DISPLAY_TZ_HOURS = 5


def fold_homoglyphs(text):
    """Кириллические двойники -> латиница. Прочие символы не трогаются."""
    if not text:
        return ''
    return ''.join(HOMOGLYPHS.get(ch, ch) for ch in text)


def normalize_nickname(nickname):
    """Дословно `_drone_normalize_nickname` из drones.py.

    [REASON]: правило приёма («lower + удалить ВСЕ пробелы») повторяется
    здесь, а не изобретается заново: инструмент, понимающий одинаковость
    написаний иначе, чем приём, показывал бы улики про другую реальность.
    Копия, а не импорт, потому что `from drones import ...` тянет за собой
    приложение, а `app = create_app()` вызывает db.create_all() на импорте --
    диагностика обязана быть читателем.
    """
    if not nickname:
        return ''
    return ''.join(nickname.lower().split())


def evidence_key(nickname):
    """Ключ сравнения: сначала гомоглифы, потом правило приёма.

    Порядок важен: normalize() приводит к нижнему регистру, и прописная «К»
    стала бы строчной «к», которой в консервативном своде нет. Свести надо
    ДО понижения регистра.
    """
    return normalize_nickname(fold_homoglyphs(nickname))


def alphabet_profile(text):
    """Из каких алфавитов набрано написание -- улика о раскладке."""
    cyr = sum(1 for ch in text if '\u0400' <= ch <= '\u04ff')
    lat = sum(1 for ch in text if ('a' <= ch <= 'z') or ('A' <= ch <= 'Z'))
    dig = sum(1 for ch in text if ch.isdigit())
    parts = []
    if cyr:
        parts.append('cyr=%d' % cyr)
    if lat:
        parts.append('lat=%d' % lat)
    if dig:
        parts.append('dig=%d' % dig)
    if cyr and lat:
        parts.append('MIXED')
    return ' '.join(parts) or 'empty'


def open_readonly(db_path):
    """Только чтение, через file:-URI. Отсутствие файла -- код 2."""
    if not os.path.exists(db_path):
        sys.stderr.write('ERROR: database not found: %s\n' % db_path)
        sys.exit(2)
    uri = 'file:%s?mode=ro' % db_path.replace('?', '%3f').replace('#', '%23')
    return sqlite3.connect(uri, uri=True)


def load_aliases(conn):
    """Активные алиасы: ключ улик -> множество номеров машин.

    Фильтр `is_active IS NOT 0` -- тот же, что у приёма
    (`DroneNickname.is_active.isnot(False)`): NULL в старой строке считается
    активным. Иначе инструмент назвал бы выводимым то, что приём не разрешит.
    """
    rows = conn.execute(
        'SELECT n.nickname, n.drone_unit_id, u.number '
        'FROM drone_nicknames n '
        'JOIN drone_units u ON u.id = n.drone_unit_id '
        'WHERE n.is_active IS NOT 0').fetchall()
    by_key = collections.defaultdict(set)
    spellings = collections.defaultdict(list)
    for nickname, unit_id, number in rows:
        key = evidence_key(nickname)
        by_key[key].add((unit_id, number))
        spellings[key].append(nickname)
    return by_key, spellings


def load_unattributed(conn):
    """Вылеты без машины, сгруппированные по сырому написанию."""
    rows = conn.execute(
        'SELECT nickname_raw, COUNT(*), COALESCE(SUM(area_ha), 0.0), '
        '       MIN(started_at), MAX(started_at) '
        'FROM drone_flights WHERE drone_unit_id IS NULL '
        'GROUP BY nickname_raw').fetchall()
    groups = []
    for nickname, flights, area, first_ts, last_ts in rows:
        groups.append({
            'nickname': nickname or '',
            'flights': flights,
            'area': float(area or 0.0),
            'first': first_ts,
            'last': last_ts,
        })
    groups.sort(key=lambda g: (-g['flights'], g['nickname']))
    return groups


def load_months_and_regions(conn, nickname):
    """Месяцы и области одного написания -- улики «когда и где летали»."""
    if nickname:
        where = 'nickname_raw = ?'
        params = (nickname,)
    else:
        where = 'nickname_raw IS NULL OR nickname_raw = ?'
        params = ('',)
    months = conn.execute(
        "SELECT strftime('%Y-%m', started_at, '+" + str(DISPLAY_TZ_HOURS) +
        " hours') AS m, COUNT(*) "
        'FROM drone_flights WHERE drone_unit_id IS NULL AND (' + where + ') '
        'GROUP BY m ORDER BY m', params).fetchall()
    regions = conn.execute(
        "SELECT COALESCE(region, 'n/a'), COUNT(*) "
        'FROM drone_flights WHERE drone_unit_id IS NULL AND (' + where + ') '
        'GROUP BY 1 ORDER BY 2 DESC LIMIT 4', params).fetchall()
    return months, regions


def classify(groups, alias_keys):
    """Разложить написания по четырём классам. Ничего не решает за человека."""
    unresolved_keys = collections.defaultdict(list)
    for group in groups:
        unresolved_keys[evidence_key(group['nickname'])].append(
            group['nickname'])

    for group in groups:
        key = evidence_key(group['nickname'])
        units = alias_keys.get(key, set())
        family = [n for n in unresolved_keys.get(key, [])
                  if n != group['nickname']]
        group['key'] = key
        group['family'] = family
        if len(units) == 1:
            unit_id, number = next(iter(units))
            group['verdict'] = 'HOMOGLYPH'
            group['unit_id'] = unit_id
            group['unit_number'] = number
        elif len(units) > 1:
            group['verdict'] = 'AMBIGUOUS'
            group['unit_id'] = None
            group['unit_number'] = None
            group['candidates'] = sorted(n for _i, n in units)
        else:
            group['verdict'] = 'UNKNOWN'
            group['unit_id'] = None
            group['unit_number'] = None
    return groups


def fmt_ts(value):
    """UTC из базы -> строка даты в UTC+5. Пустое значение -- прочерк."""
    if not value:
        return '-'
    text = str(value)
    for pattern in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S',
                    '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            moment = datetime.datetime.strptime(text, pattern)
        except ValueError:
            continue
        moment += datetime.timedelta(hours=DISPLAY_TZ_HOURS)
        return moment.strftime('%Y-%m-%d')
    return text[:10]


def render(groups, alias_spellings, conn):
    """Отчёт одной строкой на написание плюс улики по нераспознанным."""
    lines = []
    add = lines.append
    total_flights = sum(g['flights'] for g in groups)
    total_area = sum(g['area'] for g in groups)
    by_verdict = collections.Counter(g['verdict'] for g in groups)

    add('DRONE-REATTACH-001 -- evidence for flights with no machine')
    add('=' * 72)
    add('spellings: %d   flights: %d   hectares: %.2f'
        % (len(groups), total_flights, total_area))
    add('verdicts: homoglyph=%d ambiguous=%d unknown=%d'
        % (by_verdict.get('HOMOGLYPH', 0), by_verdict.get('AMBIGUOUS', 0),
           by_verdict.get('UNKNOWN', 0)))
    add('')

    for verdict, title in (
            ('HOMOGLYPH', 'DERIVABLE -- same string, other keyboard layout'),
            ('AMBIGUOUS', 'AMBIGUOUS -- key points at more than one machine'),
            ('UNKNOWN', 'UNKNOWN -- owner decides, evidence below')):
        chunk = [g for g in groups if g['verdict'] == verdict]
        if not chunk:
            continue
        add(title)
        add('-' * 72)
        for group in chunk:
            head = ('  %-28s flights=%-6d ha=%-10.2f %s..%s'
                    % (group['nickname'][:28], group['flights'],
                       group['area'], fmt_ts(group['first']),
                       fmt_ts(group['last'])))
            add(head)
            if verdict == 'HOMOGLYPH':
                add('      -> machine #%s, alias %s'
                    % (group['unit_number'],
                       ' / '.join(alias_spellings.get(group['key'], []))))
            if verdict == 'AMBIGUOUS':
                add('      -> machines %s -- NOT decided here'
                    % ', '.join('#%s' % n for n in group['candidates']))
            if group['family']:
                add('      family (same key): %s' % ' | '.join(group['family']))
            if verdict == 'UNKNOWN':
                months, regions = load_months_and_regions(conn,
                                                          group['nickname'])
                add('      alphabet: %s' % alphabet_profile(group['nickname']))
                add('      months: %s'
                    % ', '.join('%s=%d' % (m, c) for m, c in months))
                add('      regions: %s'
                    % ', '.join('%s=%d' % (r, c) for r, c in regions))
        add('')

    add('NOTHING WAS WRITTEN. Next step for a DERIVABLE row: add the spelling')
    add('as an alias on /drones/units, preview on /drones/units/reattach,')
    add('apply, and use the run journal to undo if the numbers disagree.')
    return lines


def main():
    parser = argparse.ArgumentParser(
        description='Evidence for flights whose machine is undetermined. '
                    'Read-only: writes nothing to the database.')
    parser.add_argument('--db', required=True, help='path to transport.db')
    parser.add_argument('--report', help='UTF-8 report file (default: next '
                                         'to the database)')
    args = parser.parse_args()

    conn = open_readonly(args.db)
    try:
        alias_keys, alias_spellings = load_aliases(conn)
        groups = load_unattributed(conn)
        if not groups:
            print('No flights with drone_unit_id IS NULL. Nothing to report.')
            return 0
        classify(groups, alias_keys)
        lines = render(groups, alias_spellings, conn)
    finally:
        conn.close()

    report_path = args.report or os.path.join(
        os.path.dirname(os.path.abspath(args.db)), 'reattach_evidence.txt')
    with open(report_path, 'w', encoding='utf-8') as handle:
        handle.write('\n'.join(lines) + '\n')

    # Консоль -- ASCII: кириллические написания уходят escape-ами.
    for line in lines:
        print(line.encode('ascii', 'backslashreplace').decode('ascii'))
    print('report written: %s' % report_path)
    return 0


if __name__ == '__main__':
    sys.exit(main())
