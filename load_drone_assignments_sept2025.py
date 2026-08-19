#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""load_drone_assignments_sept2025.py -- DRONE-FLEET-ASSIGN-001, сентябрь 2025.

Ставит операторов на машины за сентябрь 2025 -- месяц, который до починки
привязки вылетов был почти целиком не привязан и потому в назначениях не
существовал вовсе: 245 машино-дней, 5 661 вылет, 5 572.18 га.

ОТКУДА ВЗЯТЫ ИМЕНА. Из трёх источников, и у каждой строки записано, из
какого именно:

  * TELEMETRY -- ник DJI сам называет оператора (Q.Nurali, PeshBekzod,
    PeshkShodi, №001 Sayfu, №3 Shaxzod) И/ИЛИ гектары книги сходятся с
    гектарами машины в пределах 11 %. Порог не выдуман: модель
    DroneOperatorAssignment документирует, что апрельская сверка после
    привязки с датами уложила всех двенадцать операторов в +-9 %.
  * OWNER -- назвал владелец 2026-08-14, телеметрия НЕ противоречит
    (машины не летают в один день с другой машиной того же человека), но и
    не подтверждает: книга либо неполна, либо расходится больше 11 %.
  * Машина 2 не получает никого: 5 вылетов, 1.76 га за месяц -- простой.

[REASON]: РАЗЛИЧИЕ ИСТОЧНИКОВ ХРАНИТСЯ, А НЕ ЗАБЫВАЕТСЯ. Владелец прямо
сказал, что документооборот в беспорядке и часть ответов даётся по памяти.
Записать догадку и проверенный факт одинаково -- значит через месяц не уметь
их различить и перепроверять всё заново. Отдельной колонки под источник в
схеме нет, менять схему ради этого рано, поэтому метка кладётся ПРЕФИКСОМ в
note: «[TELEMETRY] ...» / «[OWNER] ...». Префикс машинно отделяем, и когда
колонка появится, перенос будет механическим.

ГРАНИЦЫ ПЕРИОДА -- первый и последний день, когда машина РЕАЛЬНО летала в
сентябре, а не 01..30.09. Мы не знаем, когда человек принял машину; мы знаем,
когда она летала под его руками. Растягивать интервал на весь месяц -- значит
придумать дни, которых никто не подтверждал.

--dry-run -- РЕЖИМ ПО УМОЛЧАНИЮ: база открывается read-only через file:-URI,
не пишется ничего. --apply пишет ОДНОЙ транзакцией и откатывает всё при любой
ошибке. Повторный --apply обязан напечатать «0 rows to insert».

КОДЫ ВОЗВРАТА:
  0 -- загружено всё, что было к загрузке;
  1 -- предусловие не выполнено, не записано НИЧЕГО (нет машины в парке;
       имя, под которым в справочнике ДВОЕ);
  2 -- файла базы нет, база не создавалась;
  3 -- загружено, но не всё: чьё-то имя отсутствует в справочнике
       операторов. Отчёт называет кого, показывает похожие имена и печатает
       справочник целиком. Заведите человека и запустите ещё раз -- уже
       загруженные строки скрипт не тронет.

Запуск (сервер, из каталога установки, служба может работать -- read-only):
  & "C:\\Program Files\\Python314\\python.exe" load_drone_assignments_sept2025.py --db instance\\transport.db
Запись (служба ОСТАНОВЛЕНА):
  & "C:\\Program Files\\Python314\\python.exe" load_drone_assignments_sept2025.py --db instance\\transport.db --apply --create-missing-operators

ОТКАТ ДАННЫХ. Отчёт (UTF-8, --report, по умолчанию рядом с базой) печатает id
каждой вставленной строки. Вставка -- единственное изменение, поэтому откат
механический:
  DELETE FROM drone_operator_assignments WHERE id IN (<id из отчёта>);
Откат кода -- git revert коммита; схема не меняется.

Консоль -- только ASCII; читаемый вывод с кириллицей уходит в отчёт.
"""

import argparse
import datetime
import os
import sqlite3
import sys

TELEMETRY = 'TELEMETRY'
OWNER = 'OWNER'

# (машина, оператор, дата с, дата по, источник, чем обосновано)
ASSIGNMENTS = (
    (1, 'Рухиллоев Сайфулло', '2025-09-02', '2025-09-30', TELEMETRY,
     'ник "№001 Sayfu"; книга Достон АКА 1008.10 га против 1009.73 га '
     'машины, расхождение 0.2 %'),
    (3, 'Холмуродов Шахзод', '2025-09-04', '2025-09-30', TELEMETRY,
     'ник "№3 Shaxzod"; книга Гиждувон 879.50 га (292.00 + 587.50 строк, '
     'ошибочно помеченных октябрём) против 795.06 га машины, 10.6 %'),
    (4, 'Анваров Усмон', '2025-09-17', '2025-09-29', OWNER,
     'ник "N2Gijduvon" называет подразделение, не человека; книга 167.00 га '
     'против 217.50 га машины, 23 % -- за пределами порога'),
    (5, 'Кобилов Фаррух', '2025-09-05', '2025-09-29', TELEMETRY,
     'книга Шофиркон 285.90 га против 311.27 га машины, 8.2 %'),
    (6, 'Жураев Туйгун', '2025-09-17', '2025-09-28', OWNER,
     'назвал владелец; книга даёт ему 67.00 га против 130.49 га машины. '
     'Ближе подходил Кодиров Нурали (132.70), но он в те же дни летал в '
     'Гардене на 884.50 га = машина 8, физически невозможно'),
    (7, 'Файзуллаев Шохрух', '2025-09-17', '2025-09-30', TELEMETRY,
     'ник "VobkenShox" = Вобкент Шохрух; книга Гарден 130.60 га против '
     '143.97 га машины, 9.3 %'),
    (8, 'Кодиров Нурали', '2025-09-04', '2025-09-30', TELEMETRY,
     'ник "Q.Nurali"; книга Гарден 884.50 га против 890.22 га машины, 0.6 %'),
    (9, 'Хамроев Шохрух', '2025-09-05', '2025-09-27', OWNER,
     'назвал владелец; 3 дня, 10.66 га, в книгах отдельно не выделяются. '
     'Телеметрия не противоречит: машины 9 и 10 (тот же человек) ни разу '
     'не летали в один день'),
    (10, 'Хамроев Шохрух', '2025-09-07', '2025-09-22', OWNER,
     'назвал владелец; книга Когон 412.60 га против 299.44 га машины, 38 %. '
     'В пользу ответа -- окно: Шохрух писал 16 дней, машина летала ровно 16'),
    (11, 'Ибодуллаев Хасанбой', '2025-09-05', '2025-09-30', OWNER,
     'назвал владелец; книга Когон 427.20 га против 534.55 га машины, 20 %. '
     'Суточная выработка совпадает: 21.4 против 21.4 га в день'),
    (12, 'Кудратов Мухриддин', '2025-09-06', '2025-09-30', TELEMETRY,
     'книга Сервис 298.60 га против 333.69 га машины, 10.5 %'),
    (13, 'Имомов Бехзод', '2025-09-17', '2025-09-30', TELEMETRY,
     'ник "PeshBekzod"; книга Сервис 406.63 га против 273.20 га машины -- '
     'ник называет человека прямо, расхождение гектаров остаётся вопросом'),
    (14, 'Файзуллаев Шоди', '2025-09-17', '2025-10-31', TELEMETRY,
     'ник "PeshkShodi"; книга Сервис 174.50 га против 168.80 га машины, 3.4 %. '
     'Дата ухода 31.10.2025 -- со слов владельца (2026-08-14, исправление '
     'его же опечатки "31.10.2026"); машина после 30.09 не летала, поэтому '
     'на гектары растянутый хвост не влияет'),
    (15, 'Жумаев Фуркат', '2025-09-06', '2025-09-30', TELEMETRY,
     'книга Сервис 423.70 га против 451.85 га машины, 6.2 %'),
)

# Машина 2 сознательно отсутствует: 5 вылетов, 1.76 га -- простой.
IDLE_UNITS = (2,)

# Люди, которых можно ЗАВЕСТИ в справочнике, если их там нет: имя -> телефон.
# Только те, чьи данные пришли документом, а не устно.
#
# [REASON]: заводится ТОЛЬКО по флагу --create-missing-operators и ТОЛЬКО
# когда в справочнике нет никого с тем же личным именем. Живой прогон
# 2026-08-14 искал «Файзиев Шоди», а по карточке парка человек называется
# Файзуллаев Шоди -- и в справочнике уже есть Файзуллаев ШОХРУХ, машина 7.
# Однофамильцы. Заводить вслепую нельзя ни в ту сторону (двойник Шоди с
# другим написанием фамилии -- Файзуллоев, как пишут книги), ни в другую
# (перепутать Шоди с Шохрухом). Отсюда правило: фамилия совпадать МОЖЕТ,
# личное имя -- не должно.
OPERATOR_DETAILS = {
    # Карточка парка: Дрон 14 Id 64TBL91002004L, оператор Файзуллаев Шоди,
    # телефон 91-923-37-67 (документ владельца, 2026-08-14).
    'Файзуллаев Шоди': '91-923-37-67',
}

# Серийники планеров из той же карточки парка -- ТОЛЬКО ДЛЯ СВЕРКИ.
#
# [REASON]: скрипт их не пишет и не чинит, а только сообщает о расхождении.
# Карточки парка уже один раз врали: серийники машин 2 и 9 стояли наоборот и
# были исправлены отдельной миграцией. Молча подменить hardware_id значит
# повторить ту же ошибку с другой стороны -- привязка по борту опирается
# именно на него.
PARK_CARD_HARDWARE = {
    14: '64TBL91002004L',
}

MONTH_FROM = '2025-09-01'
MONTH_TO = '2025-09-30'


def die(message, code):
    print('ERROR: %s' % message)
    sys.exit(code)


def connect(db_path, writable):
    if not os.path.exists(db_path):
        # [REASON]: sqlite3.connect СОЗДАЁТ пустую базу, если файла нет.
        # Молча созданная пустая база на проде хуже отказа.
        die('database not found at %s - refusing to run.' % db_path, 2)
    if writable:
        return sqlite3.connect(db_path)
    uri = 'file:%s?mode=ro' % db_path.replace('?', '%3f').replace('#', '%23')
    return sqlite3.connect(uri, uri=True)


def normalize(name):
    """Сравнение имён без учёта регистра, кириллических гомоглифов и пробелов.

    [REASON]: в справочнике операторов и в этом файле одна и та же фамилия
    может быть набрана с узбекскими буквами (Қ, Ғ, Ў, Ҳ) или с русскими
    (К, Г, У, Х). Точное сравнение строк развалилось бы на первом же имени.
    """
    table = {ord('қ'): 'к', ord('ғ'): 'г', ord('ў'): 'у', ord('ҳ'): 'х',
             ord('ҷ'): 'ж', ord('ъ'): '', ord('ь'): '', ord('ё'): 'е'}
    return ' '.join(name.lower().translate(table).split())


def resolve_operators(conn):
    rows = conn.execute('SELECT id, full_name FROM drone_operators').fetchall()
    by_norm = {}
    for oid, full_name in rows:
        by_norm.setdefault(normalize(full_name or ''), []).append(
            (oid, full_name))
    return by_norm


def resolve_units(conn):
    rows = conn.execute('SELECT id, number FROM drone_units').fetchall()
    return {number: uid for uid, number in rows}


def creation_conflicts(by_norm, wanted):
    """Кто в справочнике мешает завести `wanted` как нового человека.

    Личное имя в этих карточках стоит вторым словом («Фамилия Имя»), и
    именно оно различает людей: Файзуллаев Шоди и Файзуллаев Шохрух -- двое
    разных, а Файзуллаев Шоди и Файзуллоев Шоди -- один и тот же под двумя
    написаниями фамилии. Поэтому конфликтом считается совпадение ЛИЧНОГО
    ИМЕНИ в любой позиции, а не совпадение фамилии.

    Имя не из двух слов -- случай неразобранный, и тогда конфликтом считается
    любое пересечение: лучше остановиться, чем завести двойника.
    """
    parts = normalize(wanted).split()
    if len(parts) == 2:
        keys = {parts[1]}
    else:
        keys = set(parts)
    out = []
    for norm, rows in by_norm.items():
        if keys & set(norm.split()):
            out.extend(name for _oid, name in rows)
    return sorted(set(out))


def suggest(by_norm, wanted):
    """Кого из справочника стоит показать рядом с ненайденным именем.

    [REASON]: «оператора нет» -- бесполезный ответ, когда в справочнике он
    записан иначе. Показываем всех, у кого совпало хотя бы одно слово, и
    решение принимает человек, а не подстрочное сравнение.
    """
    parts = set(normalize(wanted).split())
    out = []
    for norm, rows in by_norm.items():
        if parts & set(norm.split()):
            out.extend(name for _oid, name in rows)
    return sorted(set(out))


def hardware_mismatches(conn):
    """Расхождения между карточкой парка и колонкой hardware_id. Не чинит."""
    out = []
    for number, card in sorted(PARK_CARD_HARDWARE.items()):
        row = conn.execute(
            'SELECT hardware_id FROM drone_units WHERE number = ?',
            (number,)).fetchone()
        stored = row[0] if row else None
        if (stored or '').strip() != card:
            out.append((number, stored, card))
    return out


def existing_overlaps(conn, unit_id):
    """Назначения этой машины, пересекающие сентябрь 2025."""
    return conn.execute(
        'SELECT id, operator_id, date_from, date_to '
        'FROM drone_operator_assignments '
        'WHERE drone_unit_id = ? AND date_from <= ? '
        '  AND (date_to IS NULL OR date_to >= ?)',
        (unit_id, MONTH_TO, MONTH_FROM)).fetchall()


def plan(conn):
    """Что вставить, что пропустить и почему. Ничего не пишет.

    [REASON]: НЕНАЙДЕННЫЙ ОПЕРАТОР -- НЕ ПОВОД ОТКАЗАТЬ ВСЕМ ОСТАЛЬНЫМ.
    Первый живой прогон 2026-08-14 упёрся в одно имя, которого нет в
    справочнике, и не записал ни одной из тринадцати верных строк. Каждое
    назначение -- самостоятельный факт: соседнее ни подтверждает его, ни
    отменяет. Поэтому такая строка откладывается ИМЕНОВАННО, остальные
    грузятся, а код возврата 3 говорит, что загружено не всё. Отказом (код 1)
    остаётся только то, что ставит под сомнение саму базу: отсутствующая
    машина или имя, под которым в справочнике ДВОЕ.
    """
    by_norm = resolve_operators(conn)
    units = resolve_units(conn)
    to_insert, skipped, unresolved, problems = [], [], [], []

    for number, operator, date_from, date_to, source, why in ASSIGNMENTS:
        if number not in units:
            problems.append('unit %d is absent from drone_units' % number)
            continue
        found = by_norm.get(normalize(operator), [])
        if len(found) > 1:
            problems.append(
                'operator %r resolves to %d rows in drone_operators - '
                'ambiguous, refusing' % (operator, len(found)))
            continue
        if not found:
            unresolved.append((number, operator, suggest(by_norm, operator)))
            continue
        operator_id, stored_name = found[0]
        overlaps = existing_overlaps(conn, units[number])
        if overlaps:
            skipped.append((number, operator, overlaps))
            continue
        to_insert.append((units[number], operator_id, number, stored_name,
                          date_from, date_to, source, why))
    return to_insert, skipped, unresolved, problems


def write_report(path, to_insert, skipped, unresolved, problems, inserted_ids,
                 directory=None, created_ids=(), blocked=(), mismatches=()):
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write('DRONE-FLEET-ASSIGN-001, сентябрь 2025\n')
        fh.write('=' * 72 + '\n\n')
        if mismatches:
            fh.write('СЕРИЙНИК ПЛАНЕРА: карточка парка и база расходятся '
                     '(НИЧЕГО НЕ ИЗМЕНЕНО):\n')
            for number, stored, card in mismatches:
                fh.write('  машина %-3d в базе: %s\n' % (number, stored))
                fh.write('             карточка: %s\n' % card)
            fh.write('  Привязка вылетов к бортам опирается на hardware_id, '
                     'поэтому чинить его можно только миграцией и только '
                     'убедившись, чья запись верна. Серийники машин 2 и 9 '
                     'уже один раз стояли наоборот.\n\n')
        if created_ids:
            fh.write('ЗАВЕДЕНО В СПРАВОЧНИКЕ ОПЕРАТОРОВ:\n')
            for name, oid in created_ids:
                fh.write('  id=%d  %s  тел. %s\n'
                         % (oid, name, OPERATOR_DETAILS.get(name, '')))
            fh.write('  Откат: DELETE FROM drone_operators WHERE id IN (%s);\n'
                     '  (сначала снять назначения этого человека -- см. ниже)\n\n'
                     % ', '.join(str(oid) for _n, oid in created_ids))
        if blocked:
            fh.write('ЗАВЕСТИ НЕЛЬЗЯ -- в справочнике уже есть человек с тем '
                     'же личным именем:\n')
            for name, conflicts in blocked:
                fh.write('  «%s» -- в справочнике: %s\n'
                         % (name, '; '.join(conflicts)))
            fh.write('  Это защита от двойника: фамилия у однофамильцев '
                     'совпадать может, личное имя -- нет. Решает человек.\n\n')
        if problems:
            fh.write('ПРОБЛЕМЫ (ничего не записано):\n')
            for p in problems:
                fh.write('  - %s\n' % p)
            fh.write('\n')
        if unresolved:
            fh.write('ОПЕРАТОР НЕ НАЙДЕН В СПРАВОЧНИКЕ -- строка отложена, '
                     'остальные загружены:\n')
            for number, operator, hints in unresolved:
                fh.write('  машина %-3d ищем «%s»\n' % (number, operator))
                if hints:
                    fh.write('      похожие в справочнике: %s\n'
                             % '; '.join(hints))
                else:
                    fh.write('      похожих в справочнике нет вовсе\n')
            fh.write('  Что делать: завести человека в справочнике операторов '
                     'под тем именем, под которым он там нужен, и запустить '
                     'скрипт ещё раз -- уже загруженные строки он не тронет.\n')
            if directory:
                fh.write('\n  СПРАВОЧНИК ОПЕРАТОРОВ ЦЕЛИКОМ (%d):\n'
                         % len(directory))
                for name in directory:
                    fh.write('      %s\n' % name)
            fh.write('\n')
        fh.write('К ВСТАВКЕ: %d\n' % len(to_insert))
        for i, item in enumerate(to_insert):
            _uid, _oid, number, stored, date_from, date_to, source, why = item
            new_id = inserted_ids[i] if i < len(inserted_ids) else None
            fh.write('  машина %-3d %-24s %s..%s  [%s]%s\n'
                     % (number, stored, date_from, date_to, source,
                        '  id=%s' % new_id if new_id else ''))
            fh.write('      %s\n' % why)
        fh.write('\nПРОПУЩЕНО (уже есть пересекающееся назначение): %d\n'
                 % len(skipped))
        for number, operator, overlaps in skipped:
            fh.write('  машина %-3d %s -- в базе уже: %s\n'
                     % (number, operator,
                        '; '.join('id=%d %s..%s' % (o[0], o[2], o[3] or '')
                                  for o in overlaps)))
        fh.write('\nМашины без оператора по решению: %s (простой)\n'
                 % ', '.join(str(u) for u in IDLE_UNITS))
        if inserted_ids:
            fh.write('\nОТКАТ:\n  DELETE FROM drone_operator_assignments '
                     'WHERE id IN (%s);\n'
                     % ', '.join(str(i) for i in inserted_ids))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', default=os.path.join('instance',
                                                     'transport.db'))
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--create-missing-operators', action='store_true',
                        help='завести в справочнике тех, чьи данные пришли '
                             'карточкой парка, если однофамильца с тем же '
                             'личным именем там нет')
    parser.add_argument('--report', default=None)
    args = parser.parse_args()

    conn = connect(args.db, writable=args.apply)
    try:
        to_insert, skipped, unresolved, problems = plan(conn)
        if problems:
            for p in problems:
                print('  problem: %s' % p)
            die('preconditions failed - nothing was written.', 1)

        print('Rows to insert: %d' % len(to_insert))
        print('Skipped (assignment already covers September): %d' % len(skipped))
        for number, _operator, _overlaps in skipped:
            print('  unit %d already has an overlapping assignment' % number)
        if unresolved:
            print('Operators absent from drone_operators: %d '
                  '(these rows are POSTPONED, the rest still load)'
                  % len(unresolved))
            for number, _operator, hints in unresolved:
                print('  unit %d: not found; %d similar name(s) in the '
                      'directory - see the report' % (number, len(hints)))

        # Кого из ненайденных МОЖНО завести, а кого нельзя и почему.
        by_norm = resolve_operators(conn)
        creatable, blocked = [], []
        for number, operator, _hints in unresolved:
            if operator not in OPERATOR_DETAILS:
                continue
            conflicts = creation_conflicts(by_norm, operator)
            if conflicts:
                blocked.append((operator, conflicts))
            else:
                creatable.append(operator)
        for operator, conflicts in blocked:
            print('  cannot create %r: directory already has someone with the '
                  'same given name (%d) - see the report'
                  % (operator, len(conflicts)))
        if creatable and not args.create_missing_operators:
            print('Can create %d operator(s) from the park card - '
                  'pass --create-missing-operators' % len(creatable))

        # Сверка серийников -- только сообщение, ничего не меняется.
        try:
            mismatches = hardware_mismatches(conn)
        except sqlite3.OperationalError:
            mismatches = []          # колонки нет -- нечего и сверять
        for number, stored, card in mismatches:
            print('  NOTE unit %d: hardware_id in DB %r, park card says %r - '
                  'NOT changed, look at it yourself'
                  % (number, stored, card))

        inserted_ids = []
        created_ids = []
        if args.apply and (to_insert or (creatable
                                         and args.create_missing_operators)):
            now = datetime.datetime.now(
                    datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
            try:
                cur = conn.cursor()
                if args.create_missing_operators:
                    for operator in creatable:
                        cur.execute(
                            'INSERT INTO drone_operators '
                            '(full_name, phone_primary, is_active, note, '
                            ' created_at, updated_at) VALUES (?, ?, 1, ?, ?, ?)',
                            (operator, OPERATOR_DETAILS[operator],
                             '[OWNER] заведён из карточки парка 2026-08-14',
                             now, now))
                        created_ids.append((operator, cur.lastrowid))
                    if created_ids:
                        # Пере-планируем: заведённые люди теперь резолвятся.
                        to_insert, skipped, unresolved, problems = plan(conn)
                        if problems:
                            raise RuntimeError('; '.join(problems))
                for (unit_id, operator_id, _n, _s, date_from, date_to,
                     source, why) in to_insert:
                    cur.execute(
                        'INSERT INTO drone_operator_assignments '
                        '(operator_id, drone_unit_id, date_from, date_to, '
                        ' note, created_at) VALUES (?, ?, ?, ?, ?, ?)',
                        (operator_id, unit_id, date_from, date_to,
                         '[%s] %s' % (source, why), now))
                    inserted_ids.append(cur.lastrowid)
                # Постусловие проверяется ДО коммита.
                got = conn.execute(
                    'SELECT COUNT(*) FROM drone_operator_assignments '
                    'WHERE date_from <= ? AND (date_to IS NULL OR date_to >= ?)',
                    (MONTH_TO, MONTH_FROM)).fetchone()[0]
                if got < len(to_insert):
                    raise RuntimeError(
                        'post-check failed: %d rows cover September, '
                        'expected at least %d' % (got, len(to_insert)))
                conn.commit()
            except Exception as exc:
                conn.rollback()
                die('insert failed and was rolled back: %s' % exc, 1)

        directory = None
        if unresolved:
            directory = sorted(
                name for (name,) in conn.execute(
                    'SELECT full_name FROM drone_operators'))
        report = args.report or os.path.join(
            os.path.dirname(os.path.abspath(args.db)) or '.',
            'drone_assignments_sept2025_report.txt')
        write_report(report, to_insert, skipped, unresolved, problems,
                     inserted_ids, directory, created_ids, blocked,
                     mismatches)
        print('Report written to %s' % report)
        if created_ids:
            print('Operators created: %d' % len(created_ids))
        if not args.apply:
            print('DRY RUN - nothing was written. Re-run with --apply.')
        else:
            print('Done. %d assignment(s) inserted.' % len(inserted_ids))
    finally:
        conn.close()

    # [REASON]: код 3 -- «загружено, но не всё». Ноль сказал бы, что сентябрь
    # закрыт, а он закрыт не полностью, и это должно быть видно без чтения
    # отчёта.
    if unresolved:
        sys.exit(3)


if __name__ == '__main__':
    main()
