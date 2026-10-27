# -*- coding: utf-8 -*-
"""Migration DRONES_WORKS_JUN2026_MERGED_CUSTOMER_LINK_001 (и отдельная
DRONES_WORKS_JUN2026_ZAMINLARI_ANCHOR_137_001) -- заказчик из объединённой
ячейки, июнь 2026.

ЧТО ПОЧИНЕНО. В июне 2026 года 31 строка книг на 900.30 га (77 095 080.10 сум
по книге) лежит в drone_works с customer_raw = '' и drone_customer_id NULL, и
отчёты показывают их как «Заказчик не указан». В книге заказчик указан: он
стоит в ОБЪЕДИНЁННОЙ ячейке колонки «ФХ номи», которая накрывает и строку
выше, и все эти строки:

    книга / лист                                        ячейка   строк      га   плательщик (карточка)
    ИЮНЬ Агрокластер.xlsx / свод ичи (Сайфулло)         B12:B33     21  755.60   Бухоро Агрокластер Заминлари МЧЖ (137)
    Сервис Дрон маълумот Июнь.xlsx / свод ичи (Фуркат)  B33:B43     10  144.70   Бухоро Сервис Агрокластер (138)

ПОЧЕМУ ТАК ВЫШЛО. Импортёр читает книгу в режиме read_only и values_only. В
этом режиме openpyxl отдаёт значение объединённой ячейки только её первой
строке, остальные получают None. Первая строка каждого диапазона (id 997 и
1042) поэтому привязана к заказчику, а следующие -- нет. Это НЕ «на своей
земле название не пишут» (правило DRONE-UI-CUSTOMER-LABEL-001): название
написано, просто один раз на весь диапазон.

ЧЕМ ОПОЗНАН ЗАКАЗЧИК. Не похожестью букв, а самой книгой: диапазон
объединённой ячейки и есть запись диспетчера «эти строки -- этого хозяйства».
Диапазоны сняты с книг пакета FIN-RECON-002 (openpyxl, не read_only) и
совпали с производственной выгрузкой 22.10.2026 строка в строку.

КАКАЯ КАРТОЧКА -- РЕШЕНИЕ ВЛАДЕЛЬЦА 27.10.2026. «Заминлари» -- одна
организация при всех написаниях; каноническая карточка №137 «Бухоро
Агрокластер Заминлари МЧЖ», №136 и №209 -- её старые варианты. Поэтому 21
строку диапазона B12:B33 эта миграция ведёт на №137, а не на №136, к которой
импорт привязал первую строку. Остальные 10 -- на №138, как и прежде.
Карточки №136/№209 не удаляются и не сливаются, прошлые периоды не
переписываются.

ПЕРВАЯ СТРОКА ДИАПАЗОНА (id 997, 27.00 га, июнь) сохранена на №136. Её
перенос на №137 -- ОТДЕЛЬНАЯ миграция --scope june-anchor со своим
идентификатором и своей квитанцией: это смена уже сохранённой связи, а не
заполнение пустой, и владелец разрешил её только для июня. Майские работы
№136 (id 13 и 32) не трогает ни одна из двух; май закрыт 22.10.2026, и
майской части у этой миграции больше нет.

ЧТО НЕ ТРОГАЕТСЯ. Меняется ровно одно поле -- drone_customer_id, и только у
перечисленных id. customer_raw остаётся как есть ('' у 31 строки: ячейка в
книге действительно пуста; это сырьё, а не вывод), площадь, сумма, расходы,
приход, тип оплаты, даты, оператор, партия -- без изменений. Тип карточек
(internal/external) здесь не меняется.

[REASON]: ПОИМЁННО, по id, и каждая строка закреплена своими ключом, площадью
и суммой. Правка «все пустые строки листа» выглядит проще и неверна: в
сентябре 2025 правка по одному имени листа чуть не увела 154.70 га чужой
книги, а имя «свод ичи (Сайфулло)» есть и в майской, и в июньской книге
Агрокластера. Если книгу перезагрузили и id сместились -- отказ, а не догадка.

[REASON]: ID КАРТОЧКИ НЕ УГАДЫВАЕТСЯ. Номер 137 записан владельцем, и миграция
сверяет с ним имя и активность карточки в той базе, где запущена. Другое имя
или выключенная карточка -- отказ с кодом 1: значит, справочник не тот, что
видел владелец, а не повод выбрать похожую.

Безопасность / идемпотентность:
  - отказ с кодом 2 при отсутствии базы, файл НЕ создаётся;
  - ПРЕДУСЛОВИЕ: карточка существует, включена и названа так, как записал
    владелец; первая строка диапазона на своём месте, с написанием книги и на
    одной из допустимых карточек; каждая целевая строка -- ровно тот id,
    ключ, период, площадь и сумма, что сняты 22.10.2026, в исходном
    состоянии (у привязки: пустой customer_raw и NULL drone_customer_id; у
    переноса: прежняя карточка и написание книги); других пустых строк в этом
    листе нет; у переноса -- на прежней карточке нет других июньских работ.
    Одно отличие -- код 1, ничего не изменено;
  - ПОСТУСЛОВИЕ (до записи в реестр): все целевые строки за своей
    карточкой; все остальные поля этих строк и все прочие строки таблицы
    побайтно прежние; число строк и суммы площади, денег, расходов и прихода
    по таблице прежние; каждая затронутая карточка получила (или отдала)
    ровно своё число строк и гектаров;
  - ОДНА транзакция на всё (BEGIN IMMEDIATE): предусловия проверяются ещё раз
    уже под блокировкой записи, затем правка, постусловия и строка
    schema_migrations -- через то же соединение, одним COMMIT. Правка без
    строки реестра или строка реестра без правки невозможны;
  - СУХОЙ ПРОГОН (без --apply) ничего не пишет: база открывается только на
    чтение, нужные таблицы копируются в память, вся логика правки и
    постусловий выполняется над копией. schema_migrations не создаётся,
    файлов не появляется;
  - с --apply обязательна --receipt. Квитанция ДВУХФАЗНАЯ: до COMMIT пишется
    <receipt>.pending (fsync), после COMMIT она атомарно становится <receipt>.
    Обрыв между ними оставляет .pending, и следующий запуск с той же
    --receipt по состоянию базы либо завершает квитанцию (правка в базе
    есть), либо откладывает её как .aborted-<время> (правки нет);
  - итог печатается одной строкой RESULT: APPLIED / ALREADY APPLIED /
    NOT APPLIED / DRY RUN OK / NEEDS_RECOVERY. NEEDS_RECOVERY -- база в
    состоянии, которого эта миграция создать не могла (реестр без правки,
    правка без реестра, часть строк); ничего не меняется, нужен человек;
  - коды выхода: 0 -- APPLIED / ALREADY APPLIED / DRY RUN OK; 1 -- NOT APPLIED
    (предусловие, постусловие, сбой до COMMIT); 2 -- нет базы; 3 --
    NEEDS_RECOVERY;
  - stdlib sqlite3 без Flask; вывод в консоль только ASCII.

Запуск (production, служба ОСТАНОВЛЕНА, после свежего бэкапа; две миграции
независимы, порядок любой, у каждой своя квитанция):
  cd C:\\transport-report
  & "C:\\Program Files\\Python314\\python.exe" migrate_drones_works_merged_customer_link_001.py
  & "C:\\Program Files\\Python314\\python.exe" migrate_drones_works_merged_customer_link_001.py --apply --receipt C:\\VehicleSoft_Release\\FIN_RECON_002_MERGED_LINK_RECEIPT.json
  & "C:\\Program Files\\Python314\\python.exe" migrate_drones_works_merged_customer_link_001.py --scope june-anchor
  & "C:\\Program Files\\Python314\\python.exe" migrate_drones_works_merged_customer_link_001.py --scope june-anchor --apply --receipt C:\\VehicleSoft_Release\\FIN_RECON_002_ZAMINLARI_ANCHOR_RECEIPT.json

Откат КОДА: git revert коммита.
Откат ДАННЫХ: печатается готовым к вставке блоком и лежит в квитанции:
  UPDATE drone_works SET drone_customer_id = NULL WHERE id IN (...) AND drone_customer_id = <id>;   (привязка)
  UPDATE drone_works SET drone_customer_id = 136 WHERE id IN (997) AND drone_customer_id = 137;    (перенос)
  DELETE FROM schema_migrations WHERE name = '<MIGRATION_ID>';
"""

import argparse
import datetime
import hashlib
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import migration_utils  # noqa: E402

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       'instance', 'transport.db')

TOLERANCE_HA = 0.005
TOLERANCE_SUM = 0.01

ZAMINLARI_CARD = 137
ZAMINLARI_CARD_NAME = 'Бухоро Агрокластер Заминлари МЧЖ'
# The spelling of the book, and the old card the import put the first row on.
ZAMINLARI_BOOK_NAME = 'Бухоро Агрокластер Заминлари'
ZAMINLARI_OLD_CARD = 136

# Значения сняты с производственной выгрузки 22.10.2026 (бэкап
# transport_20261022_150645.db, MANIFEST.json b1d9d11d...) и сверены с книгами.
# Группа:
#   file, sheet, cell   -- источник
#   card, card_name     -- куда ведёт (id и имя записаны владельцем)
#   from_card           -- None: привязка пустой связи; id: перенос с этой карточки
#   raw                 -- написание книги у целевых строк ('' у привязки)
#   anchor, anchor_raw, anchor_cards -- первая строка диапазона (id, строка),
#                          её написание и карточки, на которых она может стоять
#   period_guard        -- у переноса: на from_card в этом периоде только эти id
#   targets             -- (id, строка, период, га, сумма)
SCOPES = {
    'june': {
        'migration_id': 'DRONES_WORKS_JUN2026_MERGED_CUSTOMER_LINK_001',
        'description': ('June 2026: 31 book rows (900.30 ha) whose farm sits in a '
                        'merged Excel cell linked to the payer of that range: 21 '
                        'to canonical card 137 (owner decision 27.10.2026), 10 to '
                        '138. Only drone_customer_id changes.'),
        'groups': (
            {'file': 'ИЮНЬ Агрокластер.xlsx', 'sheet': 'свод ичи (Сайфулло)', 'cell': 'B12:B33',
             'card': ZAMINLARI_CARD, 'card_name': ZAMINLARI_CARD_NAME, 'from_card': None, 'raw': '',
             'anchor': (997, 12), 'anchor_raw': ZAMINLARI_BOOK_NAME,
             'anchor_cards': (ZAMINLARI_OLD_CARD, ZAMINLARI_CARD), 'period_guard': None,
             'targets': (
                 (998, 13, '2026-06', 38.80, 3322544.49),
                 (999, 14, '2026-06', 27.40, 2346332.97),
                 (1000, 15, '2026-06', 19.00, 1627019.21),
                 (1001, 16, '2026-06', 19.00, 1627019.21),
                 (1002, 17, '2026-06', 27.00, 2312079.93),
                 (1003, 18, '2026-06', 27.60, 2363459.48),
                 (1004, 19, '2026-06', 30.70, 2628920.51),
                 (1005, 20, '2026-06', 15.00, 1284488.85),
                 (1006, 21, '2026-06', 25.20, 2157941.27),
                 (1007, 22, '2026-06', 22.00, 1883916.98),
                 (1008, 23, '2026-06', 20.10, 1721215.06),
                 (1009, 24, '2026-06', 39.70, 3399613.82),
                 (1010, 25, '2026-06', 28.30, 2423402.30),
                 (1011, 26, '2026-06', 32.80, 2808748.95),
                 (1012, 27, '2026-06', 44.10, 3776397.22),
                 (1013, 28, '2026-06', 22.20, 1901043.50),
                 (1014, 29, '2026-06', 30.00, 2568977.70),
                 (1015, 30, '2026-06', 36.80, 3151279.31),
                 (1016, 31, '2026-06', 15.20, 1301615.37),
                 (1017, 32, '2026-06', 50.10, 4290192.76),
                 (1018, 33, '2026-06', 184.60, 15807776.11))},
            {'file': 'Сервис Дрон маълумот Июнь.xlsx', 'sheet': 'свод ичи (Фуркат)', 'cell': 'B33:B43',
             'card': 138, 'card_name': 'Бухоро Сервис Агрокластер', 'from_card': None, 'raw': '',
             'anchor': (1042, 33), 'anchor_raw': 'Бухоро Сервис Агрокластер',
             'anchor_cards': (138,), 'period_guard': None,
             'targets': (
                 (1043, 34, '2026-06', 20.65, 1768321.45),
                 (1044, 35, '2026-06', 13.30, 1138918.90),
                 (1045, 36, '2026-06', 17.50, 1498577.50),
                 (1046, 37, '2026-06', 27.70, 2372034.10),
                 (1047, 38, '2026-06', 5.10, 436728.30),
                 (1048, 39, '2026-06', 11.00, 941963.00),
                 (1049, 40, '2026-06', 19.40, 1661280.20),
                 (1050, 41, '2026-06', 11.50, 984779.50),
                 (1051, 42, '2026-06', 7.70, 659374.10),
                 (1052, 43, '2026-06', 10.85, 929118.05))},
        ),
        'expect': (31, 900.30, 77095080.10),
    },
    # [REASON]: owner's decision of 27.10.2026 -- the one June work already
    # STORED on the old card 136 may move to 137, June only, with its own
    # proof and receipt. A separate migration id, so it is applied, recorded
    # and rolled back on its own; the 31 empty links above do not depend on it.
    'june-anchor': {
        'migration_id': 'DRONES_WORKS_JUN2026_ZAMINLARI_ANCHOR_137_001',
        'description': ('June 2026: the one stored June work of «Бухоро Агрокластер '
                        'Заминлари» (id 997, 27.00 ha) moves from old card 136 to '
                        'canonical card 137 (owner decision 27.10.2026). May on 136 '
                        'is not touched. Only drone_customer_id changes.'),
        'groups': (
            {'file': 'ИЮНЬ Агрокластер.xlsx', 'sheet': 'свод ичи (Сайфулло)', 'cell': 'B12',
             'card': ZAMINLARI_CARD, 'card_name': ZAMINLARI_CARD_NAME,
             'from_card': ZAMINLARI_OLD_CARD, 'from_card_name': ZAMINLARI_BOOK_NAME,
             'raw': ZAMINLARI_BOOK_NAME, 'anchor': None, 'anchor_raw': None, 'anchor_cards': (),
             'period_guard': '2026-06',
             'targets': ((997, 12, '2026-06', 27.00, 2312079.93),)},
        ),
        'expect': (1, 27.00, 2312079.93),
    },
}

# Поля строки, которые правка обязана оставить как были (всё, кроме
# drone_customer_id). Читаются из самой таблицы: колонка, добавленная позже,
# тоже окажется под защитой.
LINK_COLUMN = 'drone_customer_id'


def normalize(name):
    """Название карточки и написание в книге могут разниться пробелами."""
    return ' '.join(str(name or '').split()).casefold()


def _ascii(text):
    return str(text).encode('ascii', 'replace').decode('ascii')


def _columns(conn):
    return [row[1] for row in conn.execute('PRAGMA table_info(drone_works)')]


def _fingerprint(conn, where, params=(), skip=()):
    """sha256 строк drone_works по id, без колонок из skip."""
    cols = [c for c in _columns(conn) if c not in skip]
    digest = hashlib.sha256()
    for row in conn.execute('SELECT %s FROM drone_works %s ORDER BY id'
                            % (', '.join(cols), where), params):
        digest.update(repr(tuple(row)).encode('utf-8'))
    return digest.hexdigest()


def _table_totals(conn):
    row = conn.execute(
        'SELECT COUNT(*), ROUND(COALESCE(SUM(area_ha), 0), 4), '
        'ROUND(COALESCE(SUM(amount), 0), 2), '
        'ROUND(COALESCE(SUM(other_costs), 0), 2), '
        'ROUND(COALESCE(SUM(received_amount), 0), 2) FROM drone_works').fetchone()
    return tuple(row)


def _customer_totals(conn, customer_id):
    row = conn.execute(
        'SELECT COUNT(*), COALESCE(SUM(area_ha), 0) FROM drone_works '
        'WHERE drone_customer_id = ?', (customer_id,)).fetchone()
    return int(row[0]), float(row[1] or 0)


def target_ids(scope):
    return [t[0] for g in SCOPES[scope]['groups'] for t in g['targets']]


def touched_cards(scope):
    """{card id: (rows, ha)} the scope adds to (+) or takes from (-) each card."""
    delta = {}
    for g in SCOPES[scope]['groups']:
        n, ha = len(g['targets']), sum(t[3] for t in g['targets'])
        rows, hectares = delta.get(g['card'], (0, 0.0))
        delta[g['card']] = (rows + n, hectares + ha)
        if g['from_card'] is not None:
            rows, hectares = delta.get(g['from_card'], (0, 0.0))
            delta[g['from_card']] = (rows - n, hectares - ha)
    return delta


def _check_card(conn, card_id, card_name, role):
    card = conn.execute('SELECT name, is_active FROM drone_customers WHERE id = ?',
                        (card_id,)).fetchone()
    if card is None:
        return ['  %s card %d: does not exist' % (role, card_id)]
    problems = []
    if normalize(card[0]) != normalize(card_name):
        problems.append('  %s card %d: is not named as the owner wrote it' % (role, card_id))
    # [REASON]: is_active IS 0, never «not = 1»: the column is BOOLEAN DEFAULT
    # 1 and a hand-inserted card can hold NULL, which the directory treats as
    # active (see import_drone_works.Directory).
    if card[1] is not None and int(card[1]) == 0:
        problems.append('  %s card %d: is switched off' % (role, card_id))
    return problems


def check_precondition(conn, scope):
    problems = []
    for g in SCOPES[scope]['groups']:
        problems += _check_card(conn, g['card'], g['card_name'], 'target')
        if g['from_card'] is not None:
            problems += _check_card(conn, g['from_card'], g['from_card_name'], 'source')
        if g['anchor'] is not None:
            anchor_row = conn.execute(
                'SELECT source_file, source_sheet, source_row, drone_customer_id, '
                'customer_raw FROM drone_works WHERE id = ?', (g['anchor'][0],)).fetchone()
            if (anchor_row is None
                    or anchor_row[:3] != (g['file'], g['sheet'], g['anchor'][1])
                    or anchor_row[3] not in g['anchor_cards']
                    or normalize(anchor_row[4]) != normalize(g['anchor_raw'])):
                problems.append('  %s: first row of the range (id %d, row %d) is not the '
                                'book name on card %s'
                                % (g['cell'], g['anchor'][0], g['anchor'][1],
                                   ' or '.join(str(c) for c in g['anchor_cards'])))
        for work_id, row_no, period, area, amount in g['targets']:
            got = conn.execute(
                'SELECT source_file, source_sheet, source_row, period_month, '
                'area_ha, amount, drone_customer_id, customer_raw '
                'FROM drone_works WHERE id = ?', (work_id,)).fetchone()
            if got is None:
                problems.append('  id %d: missing' % work_id)
                continue
            if got[:4] != (g['file'], g['sheet'], row_no, period):
                problems.append('  id %d: key or period differs from row %d of %s'
                                % (work_id, row_no, g['cell']))
            if (got[4] is None or abs(float(got[4]) - area) > TOLERANCE_HA
                    or got[5] is None or abs(float(got[5]) - amount) > TOLERANCE_SUM):
                problems.append('  id %d: area or amount differs (expected %.2f ha / %.2f)'
                                % (work_id, area, amount))
            if got[6] != g['from_card']:
                problems.append('  id %d: on card %s, expected %s'
                                % (work_id, got[6], g['from_card'] if g['from_card'] is not None
                                   else 'none'))
            if normalize(got[7]) != normalize(g['raw']):
                problems.append('  id %d: customer_raw is not %s'
                                % (work_id, 'the book name' if g['raw'] else 'empty'))
        ids = {t[0] for t in g['targets']}
        if g['from_card'] is None:
            # [REASON]: a NULL row of this sheet that is NOT in the list means the
            # book was re-imported or the range is wider than measured -- linking
            # the listed rows only would leave a silent remainder.
            others = [r[0] for r in conn.execute(
                "SELECT id FROM drone_works WHERE source_file = ? AND source_sheet = ? "
                "AND drone_customer_id IS NULL AND TRIM(COALESCE(customer_raw, '')) = ''",
                (g['file'], g['sheet'])) if r[0] not in ids]
            if others:
                problems.append('  %s: %d other unlinked row(s) with an empty customer in '
                                'this sheet, ids %s' % (g['cell'], len(others), others[:10]))
        if g['period_guard']:
            # [REASON]: the owner allowed moving THE stored June work of the old
            # card. Another June work on it means the database is not the one he
            # looked at; moving "the listed one" would split June silently.
            on_card = sorted(r[0] for r in conn.execute(
                'SELECT id FROM drone_works WHERE drone_customer_id = ? AND period_month = ?',
                (g['from_card'], g['period_guard'])))
            if on_card != sorted(ids):
                problems.append('  card %d in %s holds ids %s, expected exactly %s'
                                % (g['from_card'], g['period_guard'], on_card[:10], sorted(ids)))
    if problems:
        problems.append('  The figures were measured on production 2026-10-22 and the '
                        'cards were named by the owner 2026-10-27. A difference means '
                        'the books or the directory changed since; refusing to guess.')
    return problems


def apply_changes(conn, scope):
    rows = []
    for g in SCOPES[scope]['groups']:
        for work_id, row_no, _period, area, amount in g['targets']:
            if g['from_card'] is None:
                cur = conn.execute(
                    'UPDATE drone_works SET drone_customer_id = ? WHERE id = ? '
                    "AND drone_customer_id IS NULL AND TRIM(COALESCE(customer_raw, '')) = ''",
                    (g['card'], work_id))
            else:
                cur = conn.execute(
                    'UPDATE drone_works SET drone_customer_id = ? WHERE id = ? '
                    'AND drone_customer_id = ?', (g['card'], work_id, g['from_card']))
            if cur.rowcount != 1:
                raise LookupError('id %d: expected to update 1 row, updated %d'
                                  % (work_id, cur.rowcount))
            rows.append({'id': work_id, 'source_file': g['file'], 'source_sheet': g['sheet'],
                         'source_row': row_no, 'merged_cell': g['cell'], 'area_ha': area,
                         'amount': amount, 'drone_customer_id_before': g['from_card'],
                         'drone_customer_id_after': g['card']})
    return rows


def rollback_sql(scope):
    lines = []
    for g in SCOPES[scope]['groups']:
        lines.append('UPDATE drone_works SET drone_customer_id = %s WHERE id IN (%s) '
                     'AND drone_customer_id = %d;'
                     % ('NULL' if g['from_card'] is None else g['from_card'],
                        ', '.join(str(t[0]) for t in g['targets']), g['card']))
    lines.append("DELETE FROM schema_migrations WHERE name = '%s';"
                 % SCOPES[scope]['migration_id'])
    return lines


def check_postcondition(conn, scope, before):
    problems = []
    ids = target_ids(scope)
    marks = ', '.join('?' * len(ids))
    for g in SCOPES[scope]['groups']:
        for work_id, *_rest in g['targets']:
            got = conn.execute('SELECT drone_customer_id FROM drone_works WHERE id = ?',
                               (work_id,)).fetchone()
            if got is None or got[0] != g['card']:
                problems.append('  id %d: not linked to card %d' % (work_id, g['card']))
    # Per card, not per group: two groups may share a card, and a move both
    # adds to one card and takes from another.
    for cust_id, (want_rows, want_ha) in touched_cards(scope).items():
        b_rows, b_ha = before['cards'][cust_id]
        rows, hectares = _customer_totals(conn, cust_id)
        if rows - b_rows != want_rows or abs((hectares - b_ha) - want_ha) > TOLERANCE_HA:
            problems.append('  card %d: expected %+d rows / %+.2f ha, got %+d / %+.2f'
                            % (cust_id, want_rows, want_ha, rows - b_rows, hectares - b_ha))
    if _fingerprint(conn, 'WHERE id IN (%s)' % marks, ids, skip=(LINK_COLUMN,)) != before['targets']:
        problems.append('  a field other than drone_customer_id moved on a target row')
    if _fingerprint(conn, 'WHERE id NOT IN (%s)' % marks, ids) != before['others']:
        problems.append('  a row outside the target list changed')
    if _table_totals(conn) != before['totals']:
        problems.append('  table totals moved: %s -> %s' % (before['totals'], _table_totals(conn)))
    return problems



def _now_utc():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat()


def _ro_connect(db):
    """Read-only connection: the dry run and every state check use only this."""
    path = os.path.abspath(db).replace('\\', '/').replace('?', '%3f').replace('#', '%23')
    return sqlite3.connect('file:%s?mode=ro' % path, uri=True)


def _registry_has(conn, migration_id):
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' "
                    "AND name = 'schema_migrations'").fetchone() is None:
        return False
    return conn.execute('SELECT 1 FROM schema_migrations WHERE name = ?',
                        (migration_id,)).fetchone() is not None


def database_state(conn, scope):
    """APPLIED / NOT_APPLIED / NEEDS_RECOVERY, decided by the database alone.

    [REASON]: after an interrupted run the receipt, the exception and the
    console can all disagree with each other; the committed database cannot.
    APPLIED means the registry row AND every target link are there;
    NOT_APPLIED means neither is. Anything in between is a state this
    migration cannot produce -- its change and its registry row share one
    COMMIT -- so it is handed to a human instead of being "repaired".
    """
    registered = _registry_has(conn, SCOPES[scope]['migration_id'])
    linked = 0
    for g in SCOPES[scope]['groups']:
        for target in g['targets']:
            row = conn.execute('SELECT drone_customer_id FROM drone_works WHERE id = ?',
                               (target[0],)).fetchone()
            if row is not None and row[0] == g['card']:
                linked += 1
    if registered and linked == len(target_ids(scope)):
        return 'APPLIED'
    if not registered and linked == 0:
        return 'NOT_APPLIED'
    return 'NEEDS_RECOVERY'


def snapshot_in_memory(db):
    """Private in-memory copy of the tables the migration reads and writes.

    [REASON]: the dry run must not write anything -- not even the
    schema_migrations table that migration_utils would create, and not a
    rolled-back transaction under a write lock on production. The file is
    opened read-only, drone_customers / drone_works / schema_migrations are
    copied with their own CREATE statements, and the whole change with its
    postconditions runs on the copy.
    """
    src = _ro_connect(db)
    mem = sqlite3.connect(':memory:')
    mem.isolation_level = None
    try:
        for table in ('drone_customers', 'drone_works', 'schema_migrations'):
            row = src.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
                              (table,)).fetchone()
            if row is None:
                continue
            mem.execute(row[0])
            cols = ['"%s"' % c[1] for c in src.execute('PRAGMA table_info("%s")' % table)]
            mem.executemany('INSERT INTO "%s" (%s) VALUES (%s)'
                            % (table, ', '.join(cols), ', '.join('?' * len(cols))),
                            src.execute('SELECT %s FROM "%s"' % (', '.join(cols), table)))
    finally:
        src.close()
    return mem


def _before(conn, scope):
    ids = target_ids(scope)
    marks = ', '.join('?' * len(ids))
    return {
        'targets': _fingerprint(conn, 'WHERE id IN (%s)' % marks, ids, skip=(LINK_COLUMN,)),
        'others': _fingerprint(conn, 'WHERE id NOT IN (%s)' % marks, ids),
        'totals': _table_totals(conn),
        'cards': {cid: _customer_totals(conn, cid) for cid in touched_cards(scope)},
    }


def _change_and_verify(conn, scope):
    """Inside an open transaction: change, postconditions. Returns (rows, before, problems)."""
    before = _before(conn, scope)
    rows = apply_changes(conn, scope)
    problems = check_postcondition(conn, scope, before)
    got = (len(rows), round(sum(r['area_ha'] for r in rows), 2),
           round(sum(r['amount'] for r in rows), 2))
    if not problems and got != SCOPES[scope]['expect']:
        problems = ['  %s rows/ha/amount, expected %s' % (got, SCOPES[scope]['expect'])]
    return rows, before, problems


def _record_in_transaction(conn, migration_id, description, checksum):
    """Строка реестра -- НА ТОЙ ЖЕ транзакции, что и правка.

    [REASON]: migration_utils.record_migration() открывает СВОЁ соединение;
    вызванная после COMMIT, она делала реестр второй транзакцией, и сбой
    между ними оставлял 31 привязку без строки реестра (воспроизведено при
    проверке 23.10.2026). Тот же урок -- migrate_drones_useful_area_001.py.
    Отдельной функцией -- чтобы тест мог заставить её отказать.
    """
    conn.execute('INSERT INTO schema_migrations (name, applied_at, checksum, description) '
                 'VALUES (?, ?, ?, ?)', (migration_id, _now_utc(), checksum, description))


def _write_pending(receipt_path, receipt):
    """Phase one: the receipt exists, durably, BEFORE the COMMIT."""
    with open(receipt_path + '.pending', 'x', encoding='utf-8') as fh:
        json.dump(receipt, fh, ensure_ascii=False, indent=1)
        fh.flush()
        os.fsync(fh.fileno())


def _commit(conn):
    conn.execute('COMMIT')


def _finalize_receipt(receipt_path, receipt):
    """Phase two: the final receipt appears atomically, then .pending goes."""
    tmp = receipt_path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(receipt, fh, ensure_ascii=False, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, receipt_path)
    if os.path.exists(receipt_path + '.pending'):
        os.remove(receipt_path + '.pending')


def _archive_pending(receipt_path):
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    target = '%s.aborted-%s' % (receipt_path, stamp)
    os.replace(receipt_path + '.pending', target)
    return target


def _state_of(db, scope):
    conn = _ro_connect(db)
    try:
        return database_state(conn, scope)
    finally:
        conn.close()


def _result(status, code, lines=()):
    for line in lines:
        print(_ascii(line))
    print('RESULT: %s' % status)
    return code


def _report_changes(got, scope):
    print('Changes: %d rows / %.2f ha / %.2f linked; only drone_customer_id moved.' % got)
    for g in SCOPES[scope]['groups']:
        print('  %s: %d row(s) / %.2f ha  card %s -> %d'
              % (g['cell'], len(g['targets']), sum(t[3] for t in g['targets']),
                 'none' if g['from_card'] is None else g['from_card'], g['card']))
    print('Postconditions: every target row is on the card the owner named for its '
          'payer; all other fields of these rows, all other rows and the table totals '
          'are byte-identical.')
    print('')
    print('ROLLBACK OF DATA (run in this order):')
    for line in rollback_sql(scope):
        print('  ' + line)


def _needs_recovery(migration_id, why):
    return _result('NEEDS_RECOVERY', 3, [
        '%s: %s' % (migration_id, why),
        'Nothing was changed. Compare the receipt files next to --receipt with the '
        'database and decide by hand; do not re-run --apply until then.'])


def dry_run(args):
    scope = SCOPES[args.scope]
    migration_id = scope['migration_id']
    state = _state_of(args.db, args.scope)
    if state == 'APPLIED':
        return _result('ALREADY APPLIED', 0, ['%s: already applied, nothing to do.' % migration_id])
    if state == 'NEEDS_RECOVERY':
        return _needs_recovery(migration_id, 'the registry and the links disagree.')
    mem = snapshot_in_memory(args.db)
    try:
        problems = check_precondition(mem, args.scope)
        if problems:
            return _result('NOT APPLIED', 1, ['PRECONDITION FAILED -- nothing changed.'] + problems)
        mem.execute('BEGIN')
        rows, _before_state, problems = _change_and_verify(mem, args.scope)
        mem.execute('ROLLBACK')
    finally:
        mem.close()
    if problems:
        return _result('NOT APPLIED', 1, ['POSTCONDITION FAILED -- nothing changed.'] + problems)
    print('%s: DRY RUN on an in-memory copy, nothing written.' % migration_id)
    _report_changes((len(rows), round(sum(r['area_ha'] for r in rows), 2),
                     round(sum(r['amount'] for r in rows), 2)), args.scope)
    return _result('DRY RUN OK', 0)


def apply(args):
    scope = SCOPES[args.scope]
    migration_id = scope['migration_id']
    if not args.receipt:
        return _result('NOT APPLIED', 1, ['ERROR: --apply needs --receipt <path>.'])
    receipt_path = os.path.abspath(args.receipt)
    state = _state_of(args.db, args.scope)

    # 1. An earlier run of this receipt that did not finish.
    if os.path.exists(receipt_path + '.pending'):
        if state == 'APPLIED':
            try:
                with open(receipt_path + '.pending', encoding='utf-8') as fh:
                    receipt = json.load(fh)
            except ValueError:
                return _needs_recovery(migration_id, 'committed, but the pending receipt is unreadable.')
            receipt.update(status='APPLIED', recovered_at_utc=_now_utc())
            _finalize_receipt(receipt_path, receipt)
            return _result('APPLIED', 0, ['%s: the previous run committed; its receipt was '
                                          'completed now.' % migration_id])
        if state == 'NOT_APPLIED':
            kept = _archive_pending(receipt_path)
            print('NOTE: the previous run stopped before COMMIT and wrote nothing; its '
                  'pending receipt is kept as %s.' % _ascii(os.path.basename(kept)))
        else:
            return _needs_recovery(migration_id, 'a pending receipt exists and the registry '
                                                 'and the links disagree.')
    # 2. A finished receipt already at this path.
    if os.path.exists(receipt_path):
        try:
            with open(receipt_path, encoding='utf-8') as fh:
                theirs = json.load(fh).get('migration_id')
        except (ValueError, AttributeError):
            theirs = None
        if theirs != migration_id:
            return _result('NOT APPLIED', 1, ['ERROR: --receipt points at an existing file that '
                                              'is not a receipt of %s.' % migration_id])
        if state == 'APPLIED':
            return _result('ALREADY APPLIED', 0, ['%s: already applied, nothing to do.' % migration_id])
        return _needs_recovery(migration_id, 'a receipt says APPLIED but the database does '
                                             'not hold the change (restored backup?).')
    if state == 'APPLIED':
        return _result('ALREADY APPLIED', 0, ['%s: already applied, nothing to do.' % migration_id])
    if state == 'NEEDS_RECOVERY':
        return _needs_recovery(migration_id, 'the registry and the links disagree.')

    # 3. The change itself: one connection, one transaction, one COMMIT.
    migration_utils.DB_PATH = os.path.abspath(args.db)
    migration_utils.ensure_schema_migrations_table()
    conn = sqlite3.connect(args.db)
    conn.isolation_level = None
    pending_written = False
    try:
        try:
            conn.execute('BEGIN IMMEDIATE')
            # [REASON]: re-checked UNDER the write lock: whatever was checked
            # before BEGIN could have changed in between.
            if _registry_has(conn, migration_id):
                conn.execute('ROLLBACK')
                return _result('ALREADY APPLIED', 0, ['%s: already applied, nothing to do.'
                                                      % migration_id])
            problems = check_precondition(conn, args.scope)
            if problems:
                conn.execute('ROLLBACK')
                return _result('NOT APPLIED', 1, ['PRECONDITION FAILED -- nothing changed.'] + problems)
            rows, before, problems = _change_and_verify(conn, args.scope)
            if problems:
                conn.execute('ROLLBACK')
                return _result('NOT APPLIED', 1, ['POSTCONDITION FAILED -- rolled back, nothing '
                                                  'changed.'] + problems)
            checksum = migration_utils.migration_checksum(os.path.abspath(__file__))
            _record_in_transaction(conn, migration_id, scope['description'], checksum)
            receipt = {
                'migration_id': migration_id,
                'status': 'PENDING',
                'script_sha256': checksum,
                'db_basename': os.path.basename(args.db),
                'started_at_utc': _now_utc(),
                'rows': rows,
                'totals_unchanged': list(before['totals']),
                'fingerprint_other_rows': before['others'],
                'fingerprint_target_rows_without_link': before['targets'],
                'rollback_sql': rollback_sql(args.scope),
            }
            _write_pending(receipt_path, receipt)
            pending_written = True
        except Exception as exc:
            if conn.in_transaction:
                conn.execute('ROLLBACK')
            if pending_written:
                os.remove(receipt_path + '.pending')
            return _result('NOT APPLIED', 1, ['ERROR before COMMIT, rolled back, nothing '
                                              'changed: %s: %s' % (type(exc).__name__, exc)])
        try:
            _commit(conn)
        except Exception as exc:
            # [REASON]: an error raised by COMMIT does not say whether the
            # commit happened. The database does.
            if conn.in_transaction:
                conn.execute('ROLLBACK')
            print(_ascii('WARNING: COMMIT raised %s: %s' % (type(exc).__name__, exc)))
            state = _state_of(args.db, args.scope)
            if state == 'APPLIED':
                receipt.update(status='APPLIED', committed_at_utc=_now_utc())
                _finalize_receipt(receipt_path, receipt)
                return _result('APPLIED', 0, ['%s: the database holds the change despite the '
                                              'error; receipt completed.' % migration_id])
            if state == 'NOT_APPLIED':
                _archive_pending(receipt_path)
                return _result('NOT APPLIED', 1, ['%s: COMMIT did not happen; nothing changed.'
                                                  % migration_id])
            return _needs_recovery(migration_id, 'COMMIT failed and the registry and the links '
                                                 'disagree.')
    finally:
        conn.close()

    receipt.update(status='APPLIED', committed_at_utc=_now_utc())
    _finalize_receipt(receipt_path, receipt)
    print('%s: APPLIED.' % migration_id)
    _report_changes((len(rows), round(sum(r['area_ha'] for r in rows), 2),
                     round(sum(r['amount'] for r in rows), 2)), args.scope)
    return _result('APPLIED', 0)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Link merged-cell customers (FIN-RECON-002).')
    parser.add_argument('--db', default=DB_PATH,
                        help='override only for testing on a copy')
    parser.add_argument('--scope', choices=sorted(SCOPES), default='june',
                        help="'june' (default): the 31 empty links; 'june-anchor': the one "
                             "stored June work 997 from card 136 to 137, its own receipt")
    parser.add_argument('--apply', action='store_true',
                        help='write the changes; without it a dry run on an in-memory copy')
    parser.add_argument('--receipt', default=None,
                        help='UTF-8 JSON receipt path; required with --apply. Re-run with the '
                             'same path to recover an interrupted run.')
    args = parser.parse_args(argv)
    if not os.path.isfile(args.db):
        print('ERROR: database not found: %s' % _ascii(args.db))
        print('Nothing was created. Run this from the install directory.')
        return 2
    return apply(args) if args.apply else dry_run(args)


if __name__ == '__main__':
    sys.exit(main())
