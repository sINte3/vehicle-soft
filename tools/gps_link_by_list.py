# -*- coding: utf-8 -*-
"""GPS-17 -- связка объектов Wialon с машинами программы по утверждённому списку.

ЗАЧЕМ
14.10 владелец попросил инвентаризацию: «удалить не нужные, связать которые не
связаны». Инвентарь GPS-12 показал две группы машин холдинга, чей трек
программа видит без машины:
  объекты без строки сопоставления -- комбайны John Deere и CASE, МТЗ,
      погрузчик: машина в справочнике есть, госномер в имени объекта тот же,
      а строки нет. Гектары считаются, но без машины: в сверку заявок
      agro-work такой объект не попадает («У машины нет объекта Wialon»);
  объекты, помеченные «не наша» давно, у которых в справочнике программы есть
      машина с тем же госномером. Их трек не считается вовсе.
Экран сопоставления делает связь по одной строке: «+ Добавить», точное имя,
машина, затем связка GPS-10 ставит id. Этот инструмент делает то же списком.
Третья группа (14.10, ночь): у машины есть своя строка, но без id и под
именем, которого в Wialon нет (номер исправлен, кириллица вместо латиницы,
к имени объекта дописали пометку). GPS-10 `--set` ставит только id, а
переименовать строку экран не умеет: в форме «Изменить» поля имени нет.

ЧТО ДЕЛАЕТ
Читает список `--list` (столбцы `wialon_id`, `equipment_id`, `deystvie`,
необязательно `osnovanie`) и по каждой строке:
  deystvie = svyazat     -- объекту без строки создаёт строку, какую дал бы
      экран: `vialon_name` -- имя объекта, как его последним записал сборщик
      (`collector_units`), `wialon_id`, `equipment_id`, `skip = 0`;
  deystvie = snyat_pometku -- у объекта ровно одна строка, она помечена
      «не наша» и без машины: ставит ей машину и снимает пометку
      (`skip = 0`). Только по явному действию в списке: пометку когда-то
      поставил человек.
  deystvie = svyazat_stroku -- у машины ровно одна строка без пометки, без id
      (или уже с id этого объекта -- так бывает после GPS-10 `--set` -- и
      прежним именем), у объекта других строк нет: ставит строке id объекта
      и его имя, как его последним записал сборщик. Машина не меняется,
      пустой `skip` становится 0. Прежнее имя -- в отчёте (`bylo`), чем имена
      различаются -- в `raznitsa`.
Проверки экрана сопоставления (`wialon_import._validate_mapping_decision`)
повторены: машина должна быть в справочнике и активна, и у неё не должно
быть другой строки без пометки -- иначе строка остаётся человеку.

ЧТО ЭТО МЕНЯЕТ, КРОМЕ ПЛАН-ФАКТА
  Импорт моточасов (`wialon_import.apply_mappings`) находит машину по имени
      строки. После связки моточасы этих машин из выгрузки Wialon пишутся
      так же, как у всех связанных машин; снятая пометка перестаёт класть их
      в «пропущенные». Это ровно то, что сделал бы экран сопоставления.
      Поэтому имя строки обязано быть нынешним именем объекта и только его
      и совпадать с ним точно, как его сравнивает импорт: строка с другим
      или общим именем -- человеку (`imya_ne_sovpadaet`, `imya_obshchee`),
      без имени у сборщика -- тоже (`net_imeni`). Объект с двойным пробелом
      в имени -- тоже человеку (`imya_s_probelami`): экран такие пробелы
      сводит, а импорт нет, и строка с таким именем машину не нашла бы.
  Сверка заявок agro-work начинает находить работу этих машин.
  Категория машины решает, как её считать: полевая -- с гектарами,
      спецтехника -- след без гектаров, непольевая -- исключена.

ЧЕГО НЕ ДЕЛАЕТ
  Ничего не удаляет и не трогает чужие строки: объект со строкой (кроме
      снятия пометки по списку), имя, занятое строкой, машина, у которой уже
      есть строка (кроме её собственной строки без id по `svyazat_stroku`),
      -- в CSV человеку, колонка `primechanie` говорит, что делать.
  Не решает, чья машина. Список составляет человек; неизвестный объект или
      машина, повтор в списке -- отказ всего прогона.
  Не пересчитывает прошлые сутки. Ночной `--catch-up` смотрит окно
      последних 30 суток (`CATCH_UP_WINDOW_DAYS` в gps/daily.py): объекту, с
      которого снята пометка, он досчитает сутки окна, у которых есть точки и
      нет строки; спецтехнике в окне уберёт гектары. Сутки старше окна
      остаются как были -- кроме суток `sbor_nepolnyy`: их проход окна не
      знает и пересчитает в первую же ночь. Моточасы прошлых дат не
      дописываются: импорт находит машину только в выгрузках, загруженных
      после связки.

ОТКАТ
Строки, изменённые прогоном, -- строки CSV записи с `izmenena = da`; номер
строки -- в `mapping_id`, прежнее состояние -- в `bylo`; номера печатаются и
в консоль. Созданная строка: значок корзины у неё на экране сопоставления --
объект снова без строки, как до прогона. Строка со снятой пометкой: значок
«Нет в системе» у неё на экране сопоставления -- та же строка снова «не
наша» без машины, с тем же номером и `wialon_id`. Переименованная строка
(`pereimenuyu`): прежнее имя -- в `bylo` после `vialon_name=`; кнопки
переименования на экране нет, поэтому -- корзина у строки и «+ Добавить» с
прежним именем (скопировать из CSV, не набирать: кириллица и латиница
выглядят одинаково) и той же машиной -- строка снова под старым именем и без
id, но с новым номером (на номер строки ничего не ссылается). Если в `bylo`
стоит `wialon_id=N`, у строки до прогона был id -- после «+ Добавить» ещё
`gps_link_mappings.py --set НОВЫЙ_НОМЕР=N --apply`. Экран сводит пробелы в
имени: прежнее имя с лишними пробелами так не вернуть -- но по нему импорт
и до прогона машину не находил. Одно `gps_link_mappings.py --unset` -- не
откат: имя осталось бы именем объекта, и автоплан GPS-10 поставил бы id
снова. Отчёт записи -- единственное место, где прежнее имя лежит рядом с
номером строки (оно есть и в отчёте сухого прогона): повторный `--apply` с
тем же `--out` перепишет его строками `uzhe_svyazan`, поэтому отчёт первой
записи хранить. Код: схема и работа программы не меняются; откатывать
нечего.

СУХОЙ ПРОГОН ПО УМОЛЧАНИЮ. Без `--apply` база открывается `mode=ro`. Файл
отчёта проверяется на запись до базы; --out на месте базы или списка --
отказ. С `--apply` -- одна транзакция `BEGIN IMMEDIATE`, план пересобирается
внутри неё, постусловие проверяется до фиксации: у каждого объекта списка
все строки с его id ведут на машину списка без пометки, и правило
`gps.exclusion.excluded_units` не называет его «не наша».

Коды выхода -- как у GPS-16: 0 -- без остатка; 3 -- часть оставлена
человеку; 1 -- постусловие не сошлось, откат; 2 -- неверный ввод, ничего не
записано; 4 -- база занята дольше 30 с, ничего не записано; 5 -- строки
ЗАПИСАНЫ, отчёт записать не удалось.

ТОЛЬКО STDLIB, без Flask (`app = create_app()` пишет в базу на импорте). В
Wialon не ходит.

Запуск (PowerShell, по одной команде на строку):

  & "C:\\Program Files\\Python314\\python.exe" C:\\gps-tools\\tools\\gps_link_by_list.py --db C:\\transport-report\\instance\\transport.db --dir C:\\transport-report\\instance --list C:\\gps-tools\\docs\\gps_svyazki_2026-10-14.csv --out C:\\gps-tools\\check\\svyazki_plan.csv

  & "C:\\Program Files\\Python314\\python.exe" C:\\gps-tools\\tools\\gps_link_by_list.py --db C:\\transport-report\\instance\\transport.db --dir C:\\transport-report\\instance --list C:\\gps-tools\\docs\\gps_svyazki_2026-10-14.csv --out C:\\gps-tools\\check\\svyazki_apply.csv --apply

Вывод в консоль -- ASCII; имена -- только в CSV (UTF-8 с BOM).
"""

import argparse
import csv
import os
import sqlite3
import sys

from collections import Counter
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from gps_collector import config                                   # noqa: E402
from gps.exclusion import (EXCLUDED_NOT_OURS, NON_FIELD_CATEGORIES,  # noqa: E402
                           TRACK_ONLY_CATEGORIES, excluded_units)
from tools.gps_link_mappings import LOOKALIKES                     # noqa: E402
from tools.gps_mark_not_ours import (BadInput, collector_units,     # noqa: E402
                                     computed_history, name_key,
                                     normalize_name, out_clash,
                                     out_writable, schema_problem)

DB_PATH = os.path.join(REPO_ROOT, 'instance', 'transport.db')

LINK = 'svyazat'
UNMARK = 'snyat_pometku'
ATTACH = 'svyazat_stroku'
ACTIONS = (LINK, UNMARK, ATTACH)

CREATE = 'sozdam'               # строки нет -- будет создана со связью
RELEASE = 'snimu_pometku'       # строка «не наша» без машины -- машина, skip=0
RENAME = 'pereimenuyu'          # строка машины без id -- id и имя объекта
DONE = 'uzhe_svyazan'           # объект уже ведёт на эту машину без пометки
HAS_ROW = 'est_stroka'          # строка с этим id есть, действие ей не подходит
NAME_TAKEN = 'imya_zanyato'     # имя занято строкой сопоставления
NAME_SHARED = 'imya_obshchee'   # имя носит ещё один объект Wialon
NAME_STALE = 'imya_ne_sovpadaet'  # имя строки «не наша» -- не имя объекта
NO_NAME = 'net_imeni'           # сборщик имени не знает
MACHINE_HAS_ROW = 'u_mashiny_est_stroka'  # у машины другая строка без пометки
MACHINE_OFF = 'mashina_otklyuchena'       # машина неактивна -- экран не даст
NO_ROW = 'u_mashiny_net_stroki'           # svyazat_stroku: строки у машины нет
MANY_ROWS = 'u_mashiny_neskolko_strok'    # svyazat_stroku: строк несколько
OLD_NAME_HELD = 'staroe_imya_u_obekta'    # имя строки носит другой объект
NAME_SPACES = 'imya_s_probelami'          # в имени объекта лишние пробелы

WRITES = (CREATE, RELEASE, RENAME)
LEFT_TO_PERSON = (HAS_ROW, NAME_TAKEN, NAME_SHARED, NAME_STALE, NO_NAME,
                  MACHINE_HAS_ROW, MACHINE_OFF, NO_ROW, MANY_ROWS,
                  OLD_NAME_HELD, NAME_SPACES)
CSV_ORDER = LEFT_TO_PERSON + (CREATE, RELEASE, RENAME, DONE)
CSV_COLUMNS = ('status', 'wialon_id', 'equipment_id', 'deystvie', 'osnovanie',
               'wialon_name', 'machine', 'plate', 'category', 'v_plan_fakte',
               'days_computed', 'ha_total', 'last_day', 'izmenena',
               'mapping_id', 'mapping_name', 'raznitsa', 'bylo', 'primechanie')

# [REASON]: экран не сохраняет имя короче двух знаков (`wialon_import`,
# сохранение строки); строку, какой экран не создал бы, инструмент не пишет.
MIN_NAME_LENGTH = 2

EXIT_BUSY = 4
EXIT_REPORT_FAILED = 5
BUSY_TIMEOUT_S = 30

NEXT_STEP = {
    HAS_ROW: ('строка с этим id уже есть и не подходит действию списка '
              '(для «не наша» -- deystvie snyat_pometku): пришлите CSV сессии'),
    NAME_TAKEN: ('имя занято строкой сопоставления (номер -- в mapping_id): '
                 'пришлите CSV сессии'),
    NAME_SHARED: ('это имя носит ещё один объект Wialon: по имени импорт '
                  'моточасов их не различит -- пришлите CSV сессии'),
    NO_NAME: 'сборщик не знает имени объекта: пришлите CSV сессии',
    NAME_STALE: ('имя строки (mapping_name) -- не нынешнее имя объекта в '
                 'Wialon: по нему импорт моточасов машину не найдёт; '
                 'пришлите CSV сессии'),
    MACHINE_HAS_ROW: ('у машины уже есть строка без пометки (номер -- в '
                      'mapping_id); если у этой строки нет id -- deystvie '
                      'svyazat_stroku: строка получит id и имя объекта (одно '
                      'gps_link_mappings.py --set оставит прежнее имя, и '
                      'импорт моточасов машину не найдёт)'),
    MACHINE_OFF: ('машина в справочнике отключена: экран сопоставления такую '
                  'не связывает -- включить её в карточке или убрать из списка'),
    NO_ROW: 'у машины нет строки без пометки: для неё -- deystvie svyazat',
    MANY_ROWS: ('у машины несколько строк без пометки (номера -- в '
                'mapping_id): какую оставить, решает человек -- пришлите CSV '
                'сессии'),
    OLD_NAME_HELD: ('нынешнее имя строки (mapping_name) носит другой объект '
                    'Wialon: импорт моточасов, возможно, уже находит по нему '
                    'машину -- пришлите CSV сессии'),
    NAME_SPACES: ('в имени объекта в Wialon двойные пробелы (или табуляция): '
                  'экран и инструмент их сводят, а импорт моточасов сравнивает '
                  'имя как есть -- строка по такому имени машину не найдёт; '
                  'поправить имя объекта в Wialon или пришлите CSV сессии'),
}

# [REASON]: у svyazat_stroku «у машины есть строка» значит другое: строка
# машины уже связана с ДРУГИМ объектом, и совет «svyazat_stroku» здесь --
# тот же путь по кругу.
ATTACH_MACHINE_HAS_ROW = ('строка машины уже связана с другим объектом '
                          '(его id -- в bylo): какой трекер её, решает '
                          'человек -- пришлите CSV сессии')


def difference(old, new):
    """Чем имя строки отличается от имени объекта -- словом, для CSV.

    [REASON]: в Excel кириллица «ЕА» и латиница «EA» неразличимы; без этой
    колонки человек, сверяющий план, не увидит, что строку вообще меняют.
    """
    if old == new:
        return 'net'
    if normalize_name(old) == normalize_name(new):
        return 'probely'
    if normalize_name(old).casefold() == normalize_name(new).casefold():
        return 'registr'
    if (normalize_name(old).upper().translate(LOOKALIKES)
            == normalize_name(new).upper().translate(LOOKALIKES)):
        return 'kirillitsa_latinitsa'
    return 'imya'


def read_list(path):
    """[(wialon_id, equipment_id, действие, основание)]. Кривое -- отказ."""
    if not os.path.isfile(path):
        raise BadInput('list file not found: %s' % path)
    name = os.path.basename(path)
    with open(path, encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        missing = [column for column in ('wialon_id', 'equipment_id', 'deystvie')
                   if column not in (reader.fieldnames or [])]
        if missing:
            raise BadInput('%s has no column(s) %s' % (name, ', '.join(missing)))
        out, units, machines = [], set(), set()
        for line, row in enumerate(reader, 2):
            values = []
            for column in ('wialon_id', 'equipment_id'):
                value = (row.get(column) or '').strip()
                if not value.isdigit() or int(value) <= 0:
                    raise BadInput('%s line %d: %s is not a positive number'
                                   % (name, line, column))
                values.append(int(value))
            unit_id, equipment_id = values
            action = (row.get('deystvie') or '').strip()
            if action not in ACTIONS:
                raise BadInput('%s line %d: deystvie must be one of %s'
                               % (name, line, ', '.join(ACTIONS)))
            # [REASON]: повтор объекта или машины -- ошибка списка, а не
            # намерение: два объекта на одну машину бывают (три трекера
            # New Holland 80 605 EA), но такую связь решает человек на
            # экране, а не список.
            if unit_id in units:
                raise BadInput('%s line %d: wialon_id %d is listed twice'
                               % (name, line, unit_id))
            if equipment_id in machines:
                raise BadInput('%s line %d: equipment_id %d is listed twice'
                               % (name, line, equipment_id))
            units.add(unit_id)
            machines.add(equipment_id)
            out.append((unit_id, equipment_id, action,
                        (row.get('osnovanie') or '').strip()))
    if not out:
        raise BadInput('%s lists no objects' % name)
    return out


def mapping_rows(con):
    return [{'id': row[0], 'vialon_name': row[1] or '', 'wialon_id': row[2],
             'skip': bool(row[3]), 'equipment_id': row[4]}
            for row in con.execute(
                'SELECT id, vialon_name, wialon_id, skip, equipment_id '
                'FROM vialon_mappings ORDER BY id')]


def machines_of(con, ids):
    """equipment_id -> карточка. Таблицы нет -- отказ: связывать не с чем."""
    if not ids:
        return {}
    marks = ','.join('?' * len(ids))
    try:
        rows = con.execute(
            'SELECT id, name, plate, category, is_active FROM equipment '
            'WHERE id IN (%s)' % marks, list(ids)).fetchall()
    except sqlite3.OperationalError:
        raise BadInput('table equipment is missing')
    # [REASON]: «активна» -- как у экрана: `_validate_mapping_decision`
    # отказывает машине с пустым is_active (`not getattr(eq, 'is_active',
    # True)`), а список машин экрана берёт только `is_active == True`.
    return {row[0]: {'name': row[1] or '', 'plate': row[2] or '',
                     'category': row[3] or '', 'active': bool(row[4])}
            for row in rows}


def counted_as(category):
    """Как план-факт будет считать объект с машиной этой категории."""
    if category in NON_FIELD_CATEGORIES:
        return 'net (ne_polevaya)'
    if category in TRACK_ONLY_CATEGORIES:
        return 'sled_bez_ga'
    return 'da'


def attach_status(unit_id, equipment_id, name, spaced, own, by_machine, by_key,
                  holders):
    """svyazat_stroku: (статус, строки для отчёта, номер строки для записи).

    Строка, которую меняют, -- единственная строка машины без пометки. У неё
    нет id, или id уже этого объекта, а имя прежнее (так бывает после GPS-10
    `--set`). Всё остальное -- человеку.
    """
    rows = by_machine.get(equipment_id, [])
    if own:
        if not (len(own) == 1 and own[0] in rows):
            return HAS_ROW, own, None
        if name and not spaced and own[0]['vialon_name'] == name:
            return DONE, own, None
    if not rows:
        return NO_ROW, [], None
    if len(rows) > 1:
        # [REASON]: какая из двух строк машины настоящая, по списку не
        # решить: вторая строка -- это уже «машина с двумя трекерами».
        return MANY_ROWS, rows, None
    row = rows[0]
    if row['wialon_id'] is not None and int(row['wialon_id']) != unit_id:
        return MACHINE_HAS_ROW, [row], None
    if len(name) < MIN_NAME_LENGTH:
        return NO_NAME, [row], None
    if spaced:
        return NAME_SPACES, [row], None
    if holders.get(name_key(name), set()) - {unit_id}:
        return NAME_SHARED, [row], None
    takers = [other for other in by_key.get(name_key(name), [])
              if other['id'] != row['id']]
    if takers:
        return NAME_TAKEN, takers, None
    # [REASON]: если прежнее имя строки -- имя другого объекта, импорт уже
    # сейчас отдаёт машине моточасы того объекта, и, может быть, это и есть
    # её настоящий трекер. Переименование молча оборвало бы эту связь.
    if holders.get(name_key(row['vialon_name']), set()) - {unit_id}:
        return OLD_NAME_HELD, [row], None
    return RENAME, [row], row['id']


def plan(con, wanted, names):
    """Строки плана по каждой строке списка. Ничего не пишет."""
    rows = mapping_rows(con)
    by_id, by_key, by_machine = {}, {}, {}
    for row in rows:
        if row['wialon_id'] is not None:
            by_id.setdefault(int(row['wialon_id']), []).append(row)
        by_key.setdefault(name_key(row['vialon_name']), []).append(row)
        if row['equipment_id'] is not None and not row['skip']:
            by_machine.setdefault(int(row['equipment_id']), []).append(row)
    machines = machines_of(con, [equipment for _, equipment, _, _ in wanted])
    history = computed_history(con, [unit for unit, _, _, _ in wanted])
    holders = {}
    for unit, (name, _) in names.items():
        holders.setdefault(name_key(name), set()).add(unit)

    unknown = [unit for unit, _, _, _ in wanted
               if unit not in names and unit not in history
               and unit not in by_id]
    if unknown:
        raise BadInput('object(s) unknown to the collector, the computation '
                       'and the mapping - a typo in the list? %s'
                       % ', '.join(str(unit) for unit in unknown))
    absent = [equipment for _, equipment, _, _ in wanted
              if equipment not in machines]
    if absent:
        raise BadInput('machine(s) not in the equipment table - a typo in the '
                       'list? %s' % ', '.join(str(e) for e in absent))

    items = []
    for unit_id, equipment_id, action, basis in wanted:
        raw = (names.get(unit_id, ('', ''))[0] or '').strip()
        name = normalize_name(raw)
        # [REASON]: импорт моточасов ищет строку по имени из выгрузки Wialon,
        # снимая только края (`wialon_import.parse_moto_csv`), а экран и этот
        # инструмент сводят пробелы внутри. Для имени объекта с двойным
        # пробелом записанная строка совпала бы с экраном, но не с импортом.
        spaced = raw != name
        machine = machines[equipment_id]
        own = by_id.get(unit_id, [])
        others, row_id, old = [], None, None
        machine_rows = [row for row in by_machine.get(equipment_id, [])
                        if row['wialon_id'] != unit_id]
        if action == ATTACH:
            if not machine['active']:
                status = MACHINE_OFF
            else:
                status, others, row_id = attach_status(
                    unit_id, equipment_id, name, spaced, own, by_machine,
                    by_key, holders)
            if status == RENAME:
                old = others[0]['vialon_name']
                by_key.setdefault(name_key(name), []).append(
                    {'id': row_id, 'vialon_name': name, 'skip': False,
                     'equipment_id': equipment_id})
        elif own and all(row['equipment_id'] == equipment_id and not row['skip']
                         for row in own):
            status, others = DONE, own
        elif not machine['active']:
            status = MACHINE_OFF
        elif machine_rows:
            # [REASON]: то же правило, что у экрана: у машины уже есть
            # строка без пометки. Вторая связь -- это уже «машина с двумя
            # трекерами», и её решает человек.
            status, others = MACHINE_HAS_ROW, machine_rows
        elif action == UNMARK:
            if (len(own) == 1 and own[0]['skip']
                    and own[0]['equipment_id'] is None):
                kept = own[0]['vialon_name']
                others = own
                # [REASON]: строка остаётся со своим именем, а по имени её
                # находит импорт моточасов. Имя, не совпадающее с нынешним
                # именем объекта (прежний водитель, номер GPS-16), моточасов
                # не принесёт, а экран не даст добавить строку с настоящим
                # именем для той же машины. Имя, которое носит ещё один
                # объект, отдало бы машине моточасы чужого трекера. Всё это
                # -- человеку, как и у связки. Сравнение -- точное, правилом
                # импорта (`apply_mappings`): «мтз» вместо «МТЗ» или лишний
                # пробел в имени строки моточасов не найдут.
                if not name:
                    status = NO_NAME
                elif spaced:
                    status = NAME_SPACES
                elif kept != name:
                    status = NAME_STALE
                elif holders.get(name_key(kept), set()) - {unit_id}:
                    status = NAME_SHARED
                else:
                    status = RELEASE
                    row_id = own[0]['id']
            else:
                status, others = HAS_ROW, own
        elif own:
            status, others = HAS_ROW, own
        elif len(name) < MIN_NAME_LENGTH:
            status = NO_NAME
        elif spaced:
            status = NAME_SPACES
        elif holders.get(name_key(name), set()) - {unit_id}:
            # [REASON]: связь живёт в имени: по нему импорт моточасов найдёт
            # машину. Имя, общее с другим объектом, отдало бы моточасы
            # чужого трекера этой машине.
            status = NAME_SHARED
        elif by_key.get(name_key(name)):
            status, others = NAME_TAKEN, by_key[name_key(name)]
        else:
            status = CREATE
            by_key[name_key(name)] = [{'id': '', 'vialon_name': name,
                                       'skip': False, 'equipment_id': equipment_id}]
        done = history.get(unit_id, {})
        note = NEXT_STEP.get(status, '')
        if action == ATTACH and status == MACHINE_HAS_ROW:
            note = ATTACH_MACHINE_HAS_ROW
        if status == OLD_NAME_HELD:
            note += ' (объект: %s)' % ' '.join(str(other) for other in sorted(
                holders.get(name_key(others[0]['vialon_name']), set())
                - {unit_id}))
        bylo = ' | '.join('skip=%d equipment_id=%s'
                          % (int(row['skip']), row['equipment_id'] or '')
                          for row in others)
        if status == RENAME or (action == ATTACH
                                and status == MACHINE_HAS_ROW):
            # [REASON]: имя -- последним и как есть, без нормализации: всё
            # после `vialon_name=` -- точное прежнее имя для отката.
            bylo = 'skip=0 equipment_id=%s wialon_id=%s vialon_name=%s' % (
                equipment_id, others[0]['wialon_id'] or '',
                others[0]['vialon_name'])
        items.append({
            'status': status, 'wialon_id': unit_id,
            'equipment_id': equipment_id, 'deystvie': action, 'osnovanie': basis,
            'wialon_name': name, 'machine': machine['name'],
            'plate': machine['plate'], 'category': machine['category'],
            'v_plan_fakte': counted_as(machine['category']),
            'days_computed': done.get('days', 0),
            'ha_total': round(done.get('ha', 0.0), 2),
            'last_day': done.get('last', ''), 'izmenena': '',
            'mapping_id': ' '.join(str(row['id']) for row in others),
            'mapping_name': ' | '.join(row['vialon_name'] for row in others),
            'raznitsa': difference(old, name) if status == RENAME else '',
            'bylo': bylo, 'primechanie': note,
            '_row_id': row_id, '_old_name': old})
    return items


def write_plan(path, items):
    order = {status: index for index, status in enumerate(CSV_ORDER)}
    ordered = sorted(items, key=lambda item: (order.get(item['status'], 99),
                                              -item['ha_total'],
                                              item['wialon_id']))
    with open(path, 'w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.writer(handle, delimiter=';')
        writer.writerow(CSV_COLUMNS)
        for item in ordered:
            writer.writerow([item[column] for column in CSV_COLUMNS])


def write_links(con, items):
    """Записи плана внутри открытой транзакции. [(item, номер строки)].

    [REASON]: UPDATE снятия пометки и переименования повторяет условие плана
    в самом WHERE (`skip = 1 AND equipment_id IS NULL`; прежнее имя, машина,
    пустой или этот же id): если строку тронули между планом и записью, она
    не изменится, и постусловие откатит всё.
    """
    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    changed = []
    for item in items:
        if item['status'] == CREATE:
            cursor = con.execute(
                'INSERT INTO vialon_mappings (vialon_name, wialon_id, '
                'equipment_id, skip, created_by, created_at, updated_at) '
                'VALUES (?, ?, ?, 0, NULL, ?, ?)',
                (item['wialon_name'], item['wialon_id'], item['equipment_id'],
                 stamp, stamp))
            changed.append((item, cursor.lastrowid))
        elif item['status'] == RELEASE:
            cursor = con.execute(
                'UPDATE vialon_mappings SET equipment_id = ?, skip = 0, '
                'updated_at = ? WHERE id = ? AND wialon_id = ? AND skip = 1 '
                'AND equipment_id IS NULL',
                (item['equipment_id'], stamp, item['_row_id'],
                 item['wialon_id']))
            if cursor.rowcount == 1:
                changed.append((item, item['_row_id']))
        elif item['status'] == RENAME:
            cursor = con.execute(
                'UPDATE vialon_mappings SET vialon_name = ?, wialon_id = ?, '
                'skip = 0, updated_at = ? WHERE id = ? '
                'AND (wialon_id IS NULL OR wialon_id = ?) '
                'AND equipment_id = ? AND COALESCE(skip, 0) = 0 '
                'AND vialon_name = ?',
                (item['wialon_name'], item['wialon_id'], stamp,
                 item['_row_id'], item['wialon_id'], item['equipment_id'],
                 item['_old_name']))
            if cursor.rowcount == 1:
                changed.append((item, item['_row_id']))
    return changed


def postcondition(con, items, changed):
    """'' или что не сошлось. Проверяет то, ради чего писали."""
    expected = [item for item in items if item['status'] in WRITES]
    if len(changed) != len(expected):
        return 'written %d of %d planned change(s)' % (len(changed), len(expected))
    excluded = excluded_units(con)
    wrong, keys = [], None
    for item in expected:
        rows = con.execute('SELECT equipment_id, skip FROM vialon_mappings '
                           'WHERE wialon_id = ?', (item['wialon_id'],)).fetchall()
        if (not rows or any(row[0] != item['equipment_id'] or row[1]
                            for row in rows)
                or excluded.get(item['wialon_id']) == EXCLUDED_NOT_OURS):
            wrong.append(item['wialon_id'])
            continue
        if item['status'] != RENAME:
            continue
        # [REASON]: переименовывают ради импорта моточасов, поэтому и
        # проверка -- его правилом: точное имя находит ровно эту строку с
        # этой машиной, и другой строки с тем же именем без учёта регистра
        # нет (SQLite `lower()` кириллицу не сворачивает -- считается здесь).
        # `skip` строго 0, а не пустой: экран заказов (/gps/orders) ищет
        # объект машины по `skip IS 0`, и пустой skip связь там не покажет.
        found = con.execute('SELECT id, equipment_id, skip '
                            'FROM vialon_mappings WHERE vialon_name = ?',
                            (item['wialon_name'],)).fetchall()
        if keys is None:
            keys = Counter(name_key(row['vialon_name'])
                           for row in mapping_rows(con))
        if (found != [(item['_row_id'], item['equipment_id'], 0)]
                or keys[name_key(item['wialon_name'])] != 1):
            wrong.append(item['wialon_id'])
    if wrong:
        return ('not linked as planned after the write: %s'
                % ', '.join(str(unit) for unit in wrong))
    return ''


def record_changed(changed):
    """Номера строк -- в отчёт. Только после COMMIT (см. GPS-16)."""
    for item, row_id in changed:
        item['mapping_id'] = str(row_id)
        item['mapping_name'] = (item['wialon_name']
                                if item['status'] in (CREATE, RENAME)
                                else item['mapping_name'])
        if item['status'] == CREATE:
            item['bylo'] = 'strok ne bylo'
        item['izmenena'] = 'da'


def report(items, out=print):
    counts = Counter(item['status'] for item in items)
    out('objects in the list : %d' % len(items))
    for status in CSV_ORDER:
        if counts.get(status):
            ids = sorted(item['wialon_id'] for item in items
                         if item['status'] == status)
            out('  %-20s %3d : %s' % (status, counts[status],
                                      ' '.join(str(unit) for unit in ids)))
    writes = [item for item in items if item['status'] in WRITES]
    by_kind = Counter(item['v_plan_fakte'] for item in writes)
    out('after the write, counted by plan-fact: %s'
        % (', '.join('%s %d' % pair for pair in sorted(by_kind.items()))
           or 'nothing changes'))
    return counts


def _refuse(message):
    sys.stderr.write('ERROR: %s\n' % message)
    return 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--db', default=DB_PATH)
    parser.add_argument('--dir', default=None,
                        help='folder with the collector files (default: instance/)')
    parser.add_argument('--list', required=True,
                        help='CSV: wialon_id, equipment_id, deystvie[, osnovanie]')
    parser.add_argument('--out', default='gps_svyazki_plan.csv')
    parser.add_argument('--apply', action='store_true',
                        help='write the links; without it nothing is written')
    args = parser.parse_args(argv)

    if not os.path.isfile(args.db):
        return _refuse('database not found at %s - refusing to run' % args.db)
    out_dir = os.path.dirname(os.path.abspath(args.out))
    if not os.path.isdir(out_dir):
        return _refuse('folder for --out does not exist: %s' % out_dir)
    clash = out_clash(args.out, (args.db, args.db + '-wal', args.db + '-shm',
                                 args.list))
    if clash:
        return _refuse('--out points at %s - refusing to overwrite it' % clash)
    why = out_writable(args.out)
    if why:
        return _refuse('cannot write --out %s (%s) - nothing was written'
                       % (args.out, why))
    try:
        wanted = read_list(args.list)
    except BadInput as problem:
        return _refuse(str(problem))
    names = collector_units(args.dir or config.points_dir())
    print('collector names read : %d' % len(names))

    if not args.apply:
        con = sqlite3.connect('file:%s?mode=ro' % args.db, uri=True, timeout=30)
        try:
            problem = schema_problem(con)
            if problem:
                return _refuse(problem)
            items = plan(con, wanted, names)
        except BadInput as problem:
            return _refuse(str(problem))
        finally:
            con.close()
        write_plan(args.out, items)
        counts = report(items)
        print('')
        print('DRY RUN - nothing was written to the database (opened mode=ro)')
        print('changes that --apply would make: %d'
              % sum(counts.get(status, 0) for status in WRITES))
        print('plan: %s' % args.out)
        return 3 if any(counts.get(status) for status in LEFT_TO_PERSON) else 0
    return _apply(args, wanted, names)


def _apply(args, wanted, names):
    con = sqlite3.connect(args.db, timeout=BUSY_TIMEOUT_S, isolation_level=None)
    try:
        problem = schema_problem(con)
        if problem:
            return _refuse(problem)
        try:
            con.execute('BEGIN IMMEDIATE')
        except sqlite3.OperationalError as problem:
            print('FAILED: database is busy (%s) - nothing was written, '
                  'run the same command again later' % problem)
            return EXIT_BUSY
        try:
            items = plan(con, wanted, names)
            changed = write_links(con, items)
            failed = postcondition(con, items, changed)
            if failed:
                con.execute('ROLLBACK')
                for item in items:
                    if item['status'] in WRITES:
                        item['primechanie'] = 'откат -- ничего не записано'
                try:
                    write_plan(args.out, items)
                except OSError:
                    pass
                report(items)
                print('')
                print('FAILED: %s' % failed)
                print('rolled back - nothing was written')
                return 1
            con.execute('COMMIT')
        except BadInput as problem:
            con.execute('ROLLBACK')
            return _refuse(str(problem))
        except Exception:
            con.execute('ROLLBACK')
            raise
    finally:
        con.close()
    record_changed(changed)
    counts = report(items)
    print('')
    print('written: %d change(s), one transaction' % len(changed))
    if changed:
        print('changed mapping rows: %s'
              % ' '.join(str(row_id) for _, row_id in changed))
    try:
        write_plan(args.out, items)
    except OSError as problem:
        print('report CSV could not be written (%s) - the rows ARE written'
              % problem.__class__.__name__)
        return EXIT_REPORT_FAILED
    print('report : %s' % args.out)
    return 3 if any(counts.get(status) for status in LEFT_TO_PERSON) else 0


if __name__ == '__main__':
    sys.exit(main())
