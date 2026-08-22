# -*- coding: utf-8 -*-
"""DRONES_WORKS_OCT2025_OPERATOR_LINK_001: привязка книг октября к операторам.

Миграция привязывает 6 строк на 335.50 га книги Ғиждувона к их операторам по
листу-источнику: «свод ичи (Шахзод)» -> Холмуродов Шахзод (315.00),
«свод ичи (Усмон) » -> Анваров Усмон (20.50).

Главный риск -- ЛИСТ-ТЁЗКА: «свод ичи (Шахзод)» есть и в книге Ғиждувона, и в
книге Сервиса (апрель и май 2026). Поэтому условие правки включает ИМЯ ФАЙЛА,
и это проверено отрицательным контролем: сервисные строки того же листа лежат
в базе рядом и обязаны остаться нетронутыми -- у них тоже нет оператора, так
что условие «оператора нет» само по себе их бы НЕ спасло. В сентябре спасал
именно оператор; здесь его недостаточно, и в этом всё отличие.

Четыре пути устава и отрицательные контроли на каждую сеть. Проверка, дающая
одинаковый результат на верной и на испорченной базе, проверкой не является.

Run:
  python -m unittest tests.test_drone_works_oct2025_operator_link_001 -v
"""
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT,
                      'migrate_drones_works_oct2025_operator_link_001.py')
sys.path.insert(0, REPO_ROOT)

import migrate_drones_works_oct2025_operator_link_001 as migration  # noqa

SCHEMA = """
CREATE TABLE drone_operators (id INTEGER PRIMARY KEY, full_name TEXT);
CREATE TABLE drone_works (id INTEGER PRIMARY KEY AUTOINCREMENT,
  period_month TEXT NOT NULL, work_date_from TEXT, work_date_to TEXT,
  drone_operator_id INTEGER, customer_raw TEXT NOT NULL, area_ha NUMERIC,
  amount NUMERIC, source_file TEXT, source_sheet TEXT, source_row INTEGER);
"""

OPERATORS = ('Холмуродов Шахзод', 'Анваров Усмон', 'Имомов Беҳзод',
             'Жураев Туйғун', 'Хамроев Шохрух', 'Қобилов Фаррух')

GIJDUVON = migration.SOURCE_FILE
SERVIS_APRIL = 'Сервис Дрон маълумот АПРЕЛЬ.xlsx'


def build_db(path, extra_rows=(), skip=()):
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    for idx, name in enumerate(OPERATORS, 1):
        conn.execute('INSERT INTO drone_operators (id, full_name) '
                     'VALUES (?, ?)', (idx, name))
    ids = {name: idx for idx, name in enumerate(OPERATORS, 1)}
    row_id = 0

    def add(source_file, sheet, operator, count, hectares, month='2025-10',
            dated=True):
        nonlocal row_id
        per = hectares / count
        for number in range(count):
            row_id += 1
            date = (month + '-10') if dated else None
            conn.execute(
                'INSERT INTO drone_works (id, period_month, work_date_from, '
                'work_date_to, drone_operator_id, customer_raw, area_ha, '
                'amount, source_file, source_sheet, source_row) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (row_id, month, date, date, ids.get(operator),
                 'ФХ %d' % row_id, per, per * 200000, source_file, sheet,
                 number + 4))

    # Две группы без оператора -- ровно то, что снято с production.
    for source_file, sheet, _name, count, hectares in migration.LINKS:
        if sheet in skip:
            continue
        add(source_file, sheet, None, count, hectares)
    # [REASON]: В ТОМ ЖЕ ЛИСТЕ «свод ичи (Усмон) » у Анварова УЖЕ стоит
    # справочная строка на 7.00 га. Без неё откат «верни всех Анварова в этом
    # листе в NULL» выглядит правильным, а на живой базе обнулил бы и её.
    add(GIJDUVON, 'свод ичи (Усмон) ', 'Анваров Усмон', 1, 7.00)
    # [REASON]: ЛИСТ-ТЁЗКА, и это главный отрицательный контроль файла. Тот же
    # «свод ичи (Шахзод)» приходит из книги Сервиса за апрель 2026, и у ЭТИХ
    # строк оператора тоже нет. Условие «оператора нет» их не отсекает --
    # отсекает только имя файла (и месяц).
    add(SERVIS_APRIL, 'свод ичи (Шахзод)', None, 28, 492.15, month='2026-04')
    # [REASON]: ТОТ ЖЕ ФАЙЛ, ТОТ ЖЕ ЛИСТ, оператора нет -- но ДРУГОЙ МЕСЯЦ, и
    # строки БЕЗ ДАТЫ, то есть месяц им дал period_month формы загрузки. Ровно
    # так книга октября уже разъехалась по двум месяцам однажды (DRONE-IMPORT-
    # DUP-001, 118.40 га). Имя файла тут не отсекает НИЧЕГО -- отсекает только
    # месяц, и без этой группы проверка на месяц была бы одинаково зелёной с
    # ним и без него.
    add(GIJDUVON, 'свод ичи (Шахзод)', None, 3, 99.00, month='2025-11',
        dated=False)
    # Соседи октября, которых трогать нельзя вовсе.
    add('Сервис Дрон Маълумот.xlsx', 'свод ичи (Беҳзод)', 'Имомов Беҳзод',
        20, 191.70)
    add('Шофиркон ПТЗ Дрон Октябрь.xlsx', 'свод ичи (Туйғун)',
        'Жураев Туйғун', 8, 254.90)
    add('Когон ПТЗ Дрон маълумот.xlsx', 'свод ичи (Шохрух)',
        'Хамроев Шохрух', 9, 172.60)
    for source_file, sheet, operator, count, hectares in extra_rows:
        add(source_file, sheet, operator, count, hectares)
    conn.commit()
    conn.close()


def run(db, *extra):
    return subprocess.run([sys.executable, SCRIPT, '--db', db] + list(extra),
                          capture_output=True, text=True)


def by_operator(db, month='2025-10'):
    conn = sqlite3.connect(db)
    try:
        return dict(conn.execute(
            "SELECT COALESCE(o.full_name, ''), ROUND(SUM(w.area_ha), 2) "
            'FROM drone_works w '
            'LEFT JOIN drone_operators o ON o.id = w.drone_operator_id '
            "WHERE COALESCE(strftime('%Y-%m', w.work_date_from), "
            '      w.period_month) = ? GROUP BY 1', (month,)).fetchall())
    finally:
        conn.close()


def snapshot(db):
    conn = sqlite3.connect(db)
    try:
        return conn.execute('SELECT id, drone_operator_id, area_ha, amount, '
                            'source_file, source_sheet FROM drone_works '
                            'ORDER BY id').fetchall()
    finally:
        conn.close()


def registry(db):
    conn = sqlite3.connect(db)
    try:
        if not conn.execute("SELECT name FROM sqlite_master WHERE type='table'"
                            " AND name='schema_migrations'").fetchone():
            return []
        return [r[0] for r in conn.execute('SELECT name FROM '
                                           'schema_migrations')]
    finally:
        conn.close()


def before_map(db):
    conn = sqlite3.connect(db)
    try:
        return ({migration.normalize(n): migration.operator_hectares(conn, n)
                 for _f, _s, n, _r, _h in migration.LINKS},
                migration.month_totals(conn))
    finally:
        conn.close()


class OctoberOperatorLinkTest(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='octlink_')
        self.db = os.path.join(self.dir, 'transport.db')
        build_db(self.db)

    # 1. Сухой прогон ничего не пишет.
    def test_dry_run_writes_nothing(self):
        before = snapshot(self.db)
        result = run(self.db)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn('DRY RUN', result.stdout)
        self.assertEqual(before, snapshot(self.db))
        self.assertEqual([], registry(self.db))

    # 1. Боевой прогон даёт ровно обещанные числа.
    def test_apply_gives_the_promised_totals(self):
        result = run(self.db, '--apply')
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn('APPLIED', result.stdout)
        totals = by_operator(self.db)
        self.assertAlmostEqual(315.00, totals['Холмуродов Шахзод'], 2)
        self.assertAlmostEqual(27.50, totals['Анваров Усмон'], 2)
        self.assertNotIn('', totals)          # ни одной строки без оператора
        self.assertIn(migration.MIGRATION_ID, registry(self.db))

    # 1. ГЛАВНОЕ: лист-тёзка из книги Сервиса не тронут.
    def test_the_namesake_sheet_of_another_book_is_not_touched(self):
        """[REASON]: «свод ичи (Шахзод)» есть и у Ғиждувона, и у Сервиса.

        У сервисных строк оператора тоже нет, поэтому условие «оператора
        нет», спасавшее сентябрь, здесь не спасает НИЧЕГО: 492.15 га апреля
        уехали бы Холмуродову, и в диффе это выглядело бы совершенно
        нормально. Апрельскую тёзку отсекают ДВА условия сразу -- чужой файл
        и чужой месяц, -- поэтому эта проверка сама по себе не говорит,
        которое из них работает. Каждое проверено отдельно: имя файла --
        на тёзке ТОГО ЖЕ месяца, месяц -- на ТОМ ЖЕ файле в ноябре.
        """
        before = {row[0]: row[1] for row in snapshot(self.db)}
        run(self.db, '--apply')
        after = {row[0]: row[1] for row in snapshot(self.db)}
        conn = sqlite3.connect(self.db)
        try:
            servis = conn.execute('SELECT id FROM drone_works '
                                  'WHERE source_file = ?',
                                  (SERVIS_APRIL,)).fetchall()
        finally:
            conn.close()
        self.assertEqual(28, len(servis))
        for (work_id,) in servis:
            self.assertIsNone(after[work_id])
            self.assertEqual(before[work_id], after[work_id])
        april = by_operator(self.db, '2026-04')
        self.assertAlmostEqual(492.15, april[''], 2)

    # 1. Тёзка ТОГО ЖЕ МЕСЯЦА: отсекается именем файла, поимённо.
    def test_a_same_month_namesake_is_excluded_by_the_file_name(self):
        """Изолированный контроль на имя файла, без предусловия.

        [REASON]: апрельскую тёзку отсекает месяц, и на ней имя файла ничего
        не доказывает -- проверка была бы одинаково зелёной с ним и без него.
        Здесь тёзка лежит в ТОМ ЖЕ октябре и тоже без оператора: месяц её не
        отсекает, оператор её не отсекает, отсекает только файл. Проверяется
        прямо на _ids(), потому что через main() такую базу первым отвергнет
        предусловие по числу сирот -- и настоящая работа имени файла осталась
        бы непроверенной.
        """
        conn = sqlite3.connect(self.db)
        try:
            conn.execute(
                "INSERT INTO drone_works (period_month, work_date_from, "
                'work_date_to, drone_operator_id, customer_raw, area_ha, '
                'amount, source_file, source_sheet, source_row) '
                "VALUES ('2025-10', '2025-10-11', '2025-10-11', NULL, "
                "'Чужая фх', 77.0, 0, 'Сервис Дрон Маълумот.xlsx', "
                "'свод ичи (Шахзод)', 5)")
            conn.commit()
            ours = migration._ids(conn, GIJDUVON, 'свод ичи (Шахзод)')
            theirs = migration._ids(conn, 'Сервис Дрон Маълумот.xlsx',
                                    'свод ичи (Шахзод)')
        finally:
            conn.close()
        self.assertEqual(2, len(ours))
        self.assertEqual(1, len(theirs))
        self.assertFalse(set(ours) & set(theirs))

    # 1. ТОТ ЖЕ файл и лист в ДРУГОМ месяце: отсекается месяцем.
    def test_the_same_book_in_another_month_is_not_touched(self):
        """Изолированный контроль на месяц, без помощи имени файла.

        [REASON]: у этих трёх строк тот же файл, тот же лист и тот же
        отсутствующий оператор -- отличается только месяц, и он приходит из
        period_month, потому что даты у строк нет. Такое состояние базы не
        выдумано: книга октября уже разъезжалась по двум месяцам ровно этим
        способом. Убери условие месяца -- и 99.00 га ноября молча уедут в
        октябрь к Холмуродову.
        """
        before = {row[0]: row[1] for row in snapshot(self.db)}
        run(self.db, '--apply')
        after = {row[0]: row[1] for row in snapshot(self.db)}
        conn = sqlite3.connect(self.db)
        try:
            november = conn.execute(
                'SELECT id FROM drone_works WHERE source_file = ? '
                "AND period_month = '2025-11'", (GIJDUVON,)).fetchall()
        finally:
            conn.close()
        self.assertEqual(3, len(november))
        for (work_id,) in november:
            self.assertIsNone(after[work_id])
            self.assertEqual(before[work_id], after[work_id])
        self.assertAlmostEqual(99.00, by_operator(self.db, '2025-11')[''], 2)

    # 1. И такую базу main() всё равно отвергает -- по числу сирот.
    def test_a_same_month_namesake_makes_the_precondition_refuse(self):
        os.remove(self.db)
        build_db(self.db, extra_rows=(('Сервис Дрон Маълумот.xlsx',
                                       'свод ичи (Шахзод)', None, 1, 77.0),))
        before = snapshot(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn('PRECONDITION FAILED', result.stdout)
        self.assertIn('rows with no operator: expected 6', result.stdout)
        self.assertEqual(before, snapshot(self.db))

    # 1. Справочная строка Анварова, уже привязанная, не задета.
    def test_the_already_linked_transfer_row_keeps_its_owner(self):
        before = {row[0]: row[1] for row in snapshot(self.db)}
        run(self.db, '--apply')
        after = {row[0]: row[1] for row in snapshot(self.db)}
        anvarov = [work_id for work_id, op_id in before.items()
                   if op_id == 2]
        self.assertEqual(1, len(anvarov))
        self.assertEqual(2, after[anvarov[0]])

    # 1. Деньги остаются на своих строках.
    def test_money_does_not_move(self):
        before = {row[0]: (row[2], row[3]) for row in snapshot(self.db)}
        run(self.db, '--apply')
        after = {row[0]: (row[2], row[3]) for row in snapshot(self.db)}
        self.assertEqual(before, after)

    # 1. Соседи октября не тронуты.
    def test_the_other_october_books_are_left_alone(self):
        before = snapshot(self.db)
        run(self.db, '--apply')
        after = {row[0]: row[1] for row in snapshot(self.db)}
        untouched = [row for row in before
                     if row[4] != GIJDUVON]
        self.assertTrue(untouched)
        for row in untouched:
            self.assertEqual(row[1], after[row[0]])

    # 2. Повтор ничего не делает.
    def test_second_apply_is_a_no_op(self):
        run(self.db, '--apply')
        after_first = snapshot(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(0, result.returncode, result.stdout)
        self.assertIn('already applied', result.stdout)
        self.assertEqual(after_first, snapshot(self.db))

    # 3. Базы нет: код 2, файл НЕ создан.
    def test_missing_database_gives_code_two_and_creates_nothing(self):
        absent = os.path.join(self.dir, 'nope.db')
        result = run(absent)
        self.assertEqual(2, result.returncode, result.stdout)
        self.assertFalse(os.path.exists(absent))

    # 4. Предусловие: лишняя строка без оператора -- отказ.
    def test_one_extra_orphan_row_is_refused(self):
        os.remove(self.db)
        build_db(self.db, extra_rows=((GIJDUVON, 'свод ичи (Шахзод)', None,
                                       1, 5.0),))
        before = snapshot(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn('PRECONDITION FAILED', result.stdout)
        self.assertEqual(before, snapshot(self.db))
        self.assertEqual([], registry(self.db))

    # 4. Предусловие: пропавшая группа -- отказ, и он называет обе цифры.
    def test_a_missing_group_is_refused(self):
        os.remove(self.db)
        build_db(self.db, skip=('свод ичи (Усмон) ',))
        before = snapshot(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn('PRECONDITION FAILED', result.stdout)
        self.assertIn('rows with no operator: expected 6', result.stdout)
        self.assertIn('expected 4 / 20.50 ha, found 0 / 0.00', result.stdout)
        self.assertEqual(before, snapshot(self.db))

    # 4. Применённое состояние второй раз предусловие не проходит.
    def test_applied_state_no_longer_matches_the_precondition(self):
        run(self.db, '--apply')
        conn = sqlite3.connect(self.db)
        conn.execute('DELETE FROM schema_migrations')
        conn.commit()
        conn.close()
        before = snapshot(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn('PRECONDITION FAILED', result.stdout)
        self.assertEqual(before, snapshot(self.db))

    # 5. Предусловие ловит площадь ГРУППЫ, а не только итог по месяцу.
    def test_hectares_moved_between_the_two_groups_are_refused(self):
        """[REASON]: гектар переложен из одной группы в другую.

        Строк по-прежнему 6, сирот по месяцу по-прежнему 335.50 -- итог
        сходится, и месячная сеть молчит. Отличить такую базу может ТОЛЬКО
        сверка площади внутри каждой группы. Прежняя редакция этой проверки
        просто добавляла гектар, и тогда её ловил месячный итог: она была
        одинаково зелёной и с групповой сверкой площади, и без неё.
        """
        conn = sqlite3.connect(self.db)
        for sheet, delta in (('свод ичи (Шахзод)', 1.0),
                             ('свод ичи (Усмон) ', -1.0)):
            conn.execute('UPDATE drone_works SET area_ha = area_ha + ? '
                         'WHERE id = (SELECT MIN(id) FROM drone_works '
                         'WHERE source_file = ? AND source_sheet = ? '
                         'AND drone_operator_id IS NULL '
                         "AND period_month = '2025-10')",
                         (delta, GIJDUVON, sheet))
        conn.commit()
        try:
            rows, hectares = migration.orphan_stats(conn)
        finally:
            conn.close()
        # Сначала показать, что месячная сеть тут молчит.
        self.assertEqual(migration.EXPECTED_ORPHAN_ROWS, rows)
        self.assertAlmostEqual(migration.EXPECTED_ORPHAN_HA, hectares, 2)

        before = snapshot(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(1, result.returncode, result.stdout)
        self.assertIn('PRECONDITION FAILED', result.stdout)
        self.assertIn('expected 2 / 315.00 ha, found 2 / 316.00',
                      result.stdout)
        self.assertNotIn('rows with no operator: expected', result.stdout)
        self.assertEqual(before, snapshot(self.db))

    # 5. Постусловие -- вторая сеть. Проверяется ПРЯМО, на испорченной базе.
    def test_the_postcondition_sees_a_row_left_without_an_operator(self):
        """[REASON]: сеть должна РАБОТАТЬ, а не просто быть написанной."""
        before_ops, before_month = before_map(self.db)
        run(self.db, '--apply')
        conn = sqlite3.connect(self.db)
        try:
            conn.execute('UPDATE drone_works SET drone_operator_id = NULL '
                         'WHERE source_file = ? AND source_sheet = ? '
                         'AND id = (SELECT MIN(id) FROM drone_works '
                         'WHERE source_file = ? AND source_sheet = ?)',
                         (GIJDUVON, 'свод ичи (Шахзод)', GIJDUVON,
                          'свод ичи (Шахзод)'))
            conn.commit()
            problems = migration.check_postcondition(conn, before_ops,
                                                     before_month)
        finally:
            conn.close()
        self.assertTrue(any('without an operator' in line
                            for line in problems), problems)

    def test_the_postcondition_sees_money_moving(self):
        run(self.db, '--apply')
        before_ops, before_month = before_map(self.db)
        conn = sqlite3.connect(self.db)
        try:
            before_ops = {migration.normalize(n): 0.0
                          for _f, _s, n, _r, _h in migration.LINKS}
            conn.execute('UPDATE drone_works SET amount = amount + 1000 '
                         'WHERE id = (SELECT MIN(id) FROM drone_works)')
            conn.commit()
            problems = migration.check_postcondition(conn, before_ops,
                                                     before_month)
        finally:
            conn.close()
        self.assertTrue(any('month amount moved' in line
                            for line in problems), problems)

    def test_the_postcondition_sees_hectares_moving(self):
        before_ops, before_month = before_map(self.db)
        run(self.db, '--apply')
        conn = sqlite3.connect(self.db)
        try:
            conn.execute("INSERT INTO drone_works (period_month, "
                         'work_date_from, work_date_to, drone_operator_id, '
                         'customer_raw, area_ha, amount, source_file, '
                         'source_sheet, source_row) '
                         "VALUES ('2025-10', '2025-10-10', '2025-10-10', 1, "
                         "'Лишняя фх', 9.0, 0, 'x.xlsx', 'x', 1)")
            conn.commit()
            problems = migration.check_postcondition(conn, before_ops,
                                                     before_month)
        finally:
            conn.close()
        self.assertTrue(any('month hectares moved' in line
                            for line in problems), problems)

    def test_the_postcondition_sees_a_wrong_operator_delta(self):
        """Дельта каждого оператора сверяется отдельно, а не только итог."""
        before_ops, before_month = before_map(self.db)
        run(self.db, '--apply')
        conn = sqlite3.connect(self.db)
        try:
            # Строка Холмуродова уходит Анварову: итог месяца тот же,
            # дельты -- нет.
            conn.execute('UPDATE drone_works SET drone_operator_id = 2 '
                         'WHERE source_file = ? AND source_sheet = ? '
                         'AND id = (SELECT MIN(id) FROM drone_works '
                         'WHERE source_file = ? AND source_sheet = ?)',
                         (GIJDUVON, 'свод ичи (Шахзод)', GIJDUVON,
                          'свод ичи (Шахзод)'))
            conn.commit()
            problems = migration.check_postcondition(conn, before_ops,
                                                     before_month)
        finally:
            conn.close()
        self.assertEqual(2, len(problems), problems)

    def test_the_postcondition_is_silent_on_a_correct_apply(self):
        """Отрицательный контроль: сеть, срабатывающая всегда, бесполезна."""
        before_ops, before_month = before_map(self.db)
        run(self.db, '--apply')
        conn = sqlite3.connect(self.db)
        try:
            problems = migration.check_postcondition(conn, before_ops,
                                                     before_month)
        finally:
            conn.close()
        self.assertEqual([], problems)

    # 6. Напечатанный откат возвращает базу.
    def test_printed_rollback_restores_the_books(self):
        before = snapshot(self.db)
        result = run(self.db, '--apply')
        self.assertEqual(0, result.returncode, result.stdout)
        self.assertNotEqual(before, snapshot(self.db))

        statements, collecting = [], False
        for line in result.stdout.splitlines():
            if line.startswith('ROLLBACK OF DATA'):
                collecting = True
                continue
            if collecting and line.strip():
                statements.append(line.strip())
        self.assertTrue(statements)
        conn = sqlite3.connect(self.db)
        try:
            for statement in statements:
                conn.execute(statement)
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(before, snapshot(self.db))
        self.assertEqual([], registry(self.db))

    # 6. Отчёт называет неоператорские остатки, но не валит прогон.
    def test_a_leftover_row_of_another_book_is_reported_not_refused(self):
        """[REASON]: строка октября без оператора из ДРУГОЙ книги -- повод

        посмотреть, а не повод откатить верную привязку. Предусловие такую
        базу отвергнет по числу сирот; здесь проверяется, что когда сирота
        появляется ПОСЛЕ правки (переимпорт соседней книги между прогонами),
        отчёт её называет, а привязка остаётся.
        """
        conn = sqlite3.connect(self.db)
        try:
            problems = migration.check_postcondition(
                conn, {migration.normalize(n): 0.0
                       for _f, _s, n, _r, _h in migration.LINKS},
                migration.month_totals(conn))
        finally:
            conn.close()
        # Пока привязки нет, обе группы полны сирот -- сеть это видит.
        self.assertTrue(any('without an operator' in line
                            for line in problems), problems)

    # 7. Консоль -- только ASCII.
    def test_the_console_never_leaks_cyrillic(self):
        result = run(self.db, '--apply')
        result.stdout.encode('ascii')
        self.assertIn('Kholmurodov', result.stdout)
        self.assertIn('Anvarov', result.stdout)


if __name__ == '__main__':
    unittest.main()
