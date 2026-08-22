# -*- coding: utf-8 -*-
"""Migration DRONES_WORKS_OCT2025_REDATE_001 -- 118.40 га возвращаются в
октябрь, которому они и принадлежат.

ЧТО ПОЧИНЕНО. Наличные блоки ОКТЯБРЬСКОЙ книги Сервиса -- 9 строк Жумаева на
24.80 га и 6 строк Кудратова на 93.60 га -- числятся за СЕНТЯБРЁМ. Из-за этого
октябрь показывает Жумаеву 22.30 га вместо 47.10 и Кудратову 3.40 вместо
97.00, а подписанный октябрьский ОБЩИЙ СВОД говорит именно 47.10 и 97.00.

ПОЧЕМУ ТАК ВЫШЛО. Месяц строки -- COALESCE(месяц даты, period_month), а
period_month приходит из формы загрузки и относится ко всей партии. Книгу
октября загрузили, указав в форме сентябрь. Её ДАТИРОВАННЫЕ строки (20 строк
Имомова, 191.70 га, и три справки) ушли в октябрь по своим датам и лежат там
до сих пор; строки БЕЗ ДАТЫ -- эти пятнадцать -- остались в сентябре.

ЧЕМ ЭТО ДОКАЗАНО, а не предположено:

  * числа совпадают с книгой ПОСТРОЧНО: наличный блок «свод ичи (Фурқат)»
    октябрьского файла -- ровно 9 строк на 24.80 га, «свод ичи (Мухриддин)»
    -- ровно 6 на 93.60;
  * подписанный октябрьский свод даёт Жумаеву 22.3 справка + 24.8 нақд =
    47.10, Кудратову 3.4 + 93.6 = 97.00 -- обе суммы сходятся только С НИМИ;
  * после возврата книги октября сходятся со сводом ЦЕЛИКОМ: 1126.70 га по
    пяти подразделениям свода, оператор в оператор;
  * у всех пятнадцати строк work_date_from пуст, то есть в сентябре они стоят
    не по своей дате, а по period_month;
  * контрагенты этих строк («Нуряхши» 35.10, «Аслон Рахим Саман» 27.90,
    «Достонбек Дилбек» 14.00) искались во ВСЕХ 2 108 текстовых ячейках семи
    сентябрьских книг и не нашлись ни разу -- это записано в
    docs/DRONES_SEPT2025_RECONCILIATION.md ещё 2026-08-17, и вывод из этого
    факта тогда сделали противоположный.

ЧЕМ ЭТО БЫЛО НАЗВАНО РАНЬШЕ. «Книгой Сервиса, загруженной дважды»:
tools/drone_import_duplicates.py видел два файла с одинаковым именем листа в
одном месяце и предлагал DELETE. 2026-08-20 этот DELETE выполнили. Разбор --
docs/DRONES_OCT2025_RECONCILIATION.md; сам отчёт починен и такую группу теперь
называет «книгой другого месяца» и SQL по ней не печатает вовсе.

ДВА СОСТОЯНИЯ БАЗЫ, И МИГРАЦИЯ РАЗЛИЧАЕТ ИХ САМА:

  A. Пятнадцать строк НА МЕСТЕ, в сентябре. Тогда их надо перенести в
     октябрь -- это и делает миграция, правя ТОЛЬКО period_month.
  B. Пятнадцать строк УДАЛЕНЫ. Тогда переносить нечего, и миграция
     ОТКАЗЫВАЕТСЯ кодом 1, напечатав порядок возврата: книгу Сервиса надо
     загрузить заново через экран, указав период 2025-10. Предпросмотр
     обязан показать «15 новых, 11 уже есть».

[REASON]: в состоянии B миграция намеренно НЕ вставляет строки сама. Значения
всех двадцати полей строки -- цена, расходы, приход, вид прихода, заказчик,
подразделение -- считает импортёр из самой книги; переписать их сюда руками
значит завести второй источник истины и разойтись с ним при первой правке
книги. Загрузка через экран идёт тем же кодом, что и всегда, тройка
(файл, лист, строка) делает её идемпотентной, а обязательный предпросмотр
показывает ровно то, что будет вставлено.

Деньги не трогаются: у строки меняется только месяц. Сумма всех месяцев
вместе не двигается -- это проверяется постусловием.

Безопасность / идемпотентность:
  - отказ с кодом 2 при отсутствии instance/transport.db, файл НЕ создаётся;
  - ПРЕДУСЛОВИЕ: ровно 15 строк, ровно 9 / 24.80 и 6 / 93.60, у всех нет
    даты и period_month = '2025-09'. Одна цифра иначе -- отказ с кодом 1;
  - ПОСТУСЛОВИЕ: сентябрь минус 118.40, октябрь плюс 118.40, итог по всем
    месяцам и сумма денег не изменились, ни одна площадь не тронута --
    проверяется ДО записи в реестр;
  - одна транзакция; повторный запуск печатает «already applied»;
  - stdlib sqlite3 без Flask; вывод в консоль только ASCII.

Запуск (production, служба ОСТАНОВЛЕНА):
  cd C:\\transport-report
  .\\nssm.exe stop TransportReport
  copy instance\\transport.db instance\\transport.db.backup_20260822a
  & "C:\\Program Files\\Python314\\python.exe" migrate_drones_works_oct2025_redate_001.py
  & "C:\\Program Files\\Python314\\python.exe" migrate_drones_works_oct2025_redate_001.py --apply
  .\\nssm.exe start TransportReport

Откат КОДА: git revert коммита.
Откат ДАННЫХ: печатается готовым к вставке блоком вместе с
  DELETE FROM schema_migrations WHERE name = 'DRONES_WORKS_OCT2025_REDATE_001';
"""

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import migration_utils  # noqa: E402

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       'instance', 'transport.db')

MIGRATION_ID = 'DRONES_WORKS_OCT2025_REDATE_001'
DESCRIPTION = ('October 2025: the 15 undated cash rows of the October Servis '
               'book (118.40 ha) move from September to October, where the '
               'signed general summary puts them.')

FROM_MONTH = '2025-09'
TO_MONTH = '2025-10'

SOURCE_FILE = 'Сервис Дрон Маълумот.xlsx'
# (лист-источник, чья книга, строк, гектаров). Числа сняты с самой книги
# октября 2026-08-22 и совпадают с подписанным ОБЩИМ СВОДОМ.
GROUPS = (
    ('свод ичи (Фурқат)', 'Жумаев Фуркат', 9, 24.80),
    ('свод ичи (Мухриддин)', 'Қудратов Мухриддин', 6, 93.60),
)

EXPECTED_ROWS = 15
EXPECTED_HA = 118.40
TOLERANCE_HA = 0.005
TOLERANCE_SUM = 1.0

MONTH_WHERE = ("COALESCE(strftime('%Y-%m', w.work_date_from), "
               "w.period_month) = ?")


def _ascii(text):
    return str(text).encode('ascii', 'replace').decode('ascii')


def group_stats(conn, sheet):
    """Строк и гектаров группы: тот файл, тот лист, БЕЗ ДАТЫ, в сентябре.

    [REASON]: «без даты» -- часть ключа, а не украшение. Датированные строки
    того же файла и того же листа (две справки Фурқата и одна Кудратова) уже
    лежат в октябре по своим датам, и трогать их нельзя: у них period_month
    тоже '2025-09', и без условия на дату они попали бы под ту же правку.
    Месяц бы у них не изменился -- COALESCE берёт дату, -- но строка была бы
    переписана зря, а откат стал бы шире, чем правка.
    """
    row = conn.execute(
        'SELECT COUNT(*), COALESCE(SUM(w.area_ha), 0) FROM drone_works w '
        'WHERE w.source_file = ? AND w.source_sheet = ? '
        'AND w.work_date_from IS NULL AND w.period_month = ?',
        (SOURCE_FILE, sheet, FROM_MONTH)).fetchone()
    return int(row[0]), float(row[1] or 0)


def _ids(conn, sheet):
    return [row[0] for row in conn.execute(
        'SELECT w.id FROM drone_works w WHERE w.source_file = ? '
        'AND w.source_sheet = ? AND w.work_date_from IS NULL '
        'AND w.period_month = ? ORDER BY w.id',
        (SOURCE_FILE, sheet, FROM_MONTH))]


def month_totals(conn, month):
    row = conn.execute(
        'SELECT COUNT(*), COALESCE(SUM(w.area_ha), 0), '
        'COALESCE(SUM(w.amount), 0) FROM drone_works w WHERE ' + MONTH_WHERE,
        (month,)).fetchone()
    return int(row[0]), float(row[1] or 0), float(row[2] or 0)


def grand_totals(conn):
    row = conn.execute(
        'SELECT COUNT(*), COALESCE(SUM(area_ha), 0), '
        'COALESCE(SUM(amount), 0) FROM drone_works').fetchone()
    return int(row[0]), float(row[1] or 0), float(row[2] or 0)


def state_b_message():
    """Что печатать, когда переносить нечего: строк в базе нет вовсе."""
    return [
        '  The 15 rows are NOT in the database at all -- they were deleted '
        'on 2026-08-20',
        '  as a "double upload". They are October work; see '
        'docs/DRONES_OCT2025_RECONCILIATION.md.',
        '  NOTHING TO RE-DATE. Bring them back by re-importing the book '
        'instead:',
        '    /drones/works/upload  ->  period 2025-10  ->  upload EXACTLY '
        'one file,',
        '    named "Servis Dron Ma\'lumot.xlsx" (Cyrillic, the same name the '
        'database already has).',
        '    The preview MUST say 15 new rows and 11 already present. If it '
        'says 26 new,',
        '    the file name differs -- stop, the batch would double the book.',
    ]


def check_precondition(conn):
    problems = []
    total_rows = 0
    total_ha = 0.0
    for sheet, whose, want_rows, want_ha in GROUPS:
        got_rows, got_ha = group_stats(conn, sheet)
        total_rows += got_rows
        total_ha += got_ha
        if got_rows != want_rows or abs(got_ha - want_ha) > TOLERANCE_HA:
            problems.append('  %-22s (%-20s) expected %d / %.2f ha, found '
                            '%d / %.2f' % (_ascii(sheet), _ascii(whose),
                                           want_rows, want_ha, got_rows,
                                           got_ha))
    if not problems and (total_rows != EXPECTED_ROWS
                         or abs(total_ha - EXPECTED_HA) > TOLERANCE_HA):
        problems.append('  together: expected %d rows / %.2f ha, found '
                        '%d / %.2f' % (EXPECTED_ROWS, EXPECTED_HA, total_rows,
                                       total_ha))
    if problems and total_rows == 0:
        # [REASON]: состояние B -- не «данные испорчены», а «уже удалили».
        # Общий совет «книги переимпортировали, отказываюсь гадать» тут
        # бесполезен: делать надо совершенно другое, и это надо сказать.
        return state_b_message()
    if problems:
        problems.append('  These figures come from the October Servis book '
                        'itself and from the signed general summary. A '
                        'difference means the book was edited or re-imported '
                        'since; refusing to guess.')
    return problems


def apply_changes(conn):
    report = []
    rollback = []
    for sheet, whose, want_rows, want_ha in GROUPS:
        ids = _ids(conn, sheet)
        if len(ids) != want_rows:
            raise LookupError('%s: expected %d rows to move, found %d'
                              % (_ascii(sheet), want_rows, len(ids)))
        placeholders = ', '.join('?' * len(ids))
        conn.execute('UPDATE drone_works SET period_month = ? '
                     'WHERE id IN (%s)' % placeholders, [TO_MONTH] + ids)
        report.append('  %-22s -> %s  %2d rows, %8.2f ha  (%s)'
                      % (_ascii(sheet), TO_MONTH, want_rows, want_ha,
                         _ascii(whose)))
        # [REASON]: откат по СПИСКУ id, а не по «верни всё этого файла в
        # сентябрь». Тот же файл держит в базе двадцать датированных строк
        # Имомова, и широкий откат утащил бы в сентябрь и их.
        rollback.append("UPDATE drone_works SET period_month = '%s' "
                        'WHERE id IN (%s);'
                        % (FROM_MONTH, ', '.join(str(i) for i in ids)))
    return report, rollback


def check_postcondition(conn, before_september, before_october, before_grand):
    problems = []
    rows, hectares, amount = month_totals(conn, FROM_MONTH)
    if abs((before_september[1] - hectares) - EXPECTED_HA) > TOLERANCE_HA:
        problems.append('  September hectares: %.2f -> %.2f, expected -%.2f'
                        % (before_september[1], hectares, EXPECTED_HA))
    if rows != before_september[0] - EXPECTED_ROWS:
        problems.append('  September rows: %d -> %d, expected -%d'
                        % (before_september[0], rows, EXPECTED_ROWS))
    rows, hectares, amount = month_totals(conn, TO_MONTH)
    if abs((hectares - before_october[1]) - EXPECTED_HA) > TOLERANCE_HA:
        problems.append('  October hectares: %.2f -> %.2f, expected +%.2f'
                        % (before_october[1], hectares, EXPECTED_HA))
    if rows != before_october[0] + EXPECTED_ROWS:
        problems.append('  October rows: %d -> %d, expected +%d'
                        % (before_october[0], rows, EXPECTED_ROWS))
    # [REASON]: перенос НЕ создаёт и НЕ уничтожает работу. Если итог по всем
    # месяцам сдвинулся хоть на строку, это уже не перенос.
    grand = grand_totals(conn)
    if grand[0] != before_grand[0]:
        problems.append('  total rows changed: %d -> %d'
                        % (before_grand[0], grand[0]))
    if abs(grand[1] - before_grand[1]) > TOLERANCE_HA:
        problems.append('  total hectares changed: %.2f -> %.2f'
                        % (before_grand[1], grand[1]))
    if abs(grand[2] - before_grand[2]) > TOLERANCE_SUM:
        problems.append('  total amount changed: %.2f -> %.2f'
                        % (before_grand[2], grand[2]))
    return problems


def main():
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument('--db', default=DB_PATH,
                        help='override only for testing on a synthetic copy')
    parser.add_argument('--apply', action='store_true',
                        help='write the changes; without it only a dry run')
    args = parser.parse_args()

    if not os.path.isfile(args.db):
        print('ERROR: database not found: %s' % _ascii(args.db))
        print('Nothing was created. Run this from the install directory.')
        return 2

    # [REASON]: migration_utils открывает СВОЁ соединение по своей константе.
    migration_utils.DB_PATH = os.path.abspath(args.db)

    conn = sqlite3.connect(args.db)
    try:
        conn.execute('PRAGMA foreign_keys = ON')
        migration_utils.ensure_schema_migrations_table()
        if migration_utils.is_migration_applied(MIGRATION_ID):
            print('%s: already applied, nothing to do.' % MIGRATION_ID)
            return 0

        problems = check_precondition(conn)
        if problems:
            print('PRECONDITION FAILED -- nothing changed.')
            for line in problems:
                print(line)
            return 1

        before_september = month_totals(conn, FROM_MONTH)
        before_october = month_totals(conn, TO_MONTH)
        before_grand = grand_totals(conn)

        conn.execute('BEGIN')
        try:
            report, rollback = apply_changes(conn)
            problems = check_postcondition(conn, before_september,
                                           before_october, before_grand)
            if problems:
                conn.rollback()
                print('POSTCONDITION FAILED -- rolled back, nothing changed.')
                for line in problems:
                    print(line)
                return 1
            if not args.apply:
                conn.rollback()
                print('%s: DRY RUN, nothing written.' % MIGRATION_ID)
            else:
                conn.commit()
                migration_utils.record_migration(
                    MIGRATION_ID, description=DESCRIPTION,
                    checksum=migration_utils.migration_checksum(
                        os.path.abspath(__file__)))
                print('%s: APPLIED.' % MIGRATION_ID)
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()

    print('Changes:')
    for line in report:
        print(line)
    print('Postconditions: September -%d rows / -%.2f ha, October +%d / '
          '+%.2f; rows, hectares and money over ALL months unchanged.'
          % (EXPECTED_ROWS, EXPECTED_HA, EXPECTED_ROWS, EXPECTED_HA))
    print('')
    print('After this the October books read against the signed summary:')
    print('  Zhumaev Furqat        47.10 ha  (22.3 transfer + 24.8 cash)')
    print('  Qudratov Muhriddin    97.00 ha  ( 3.4 transfer + 93.6 cash)')
    print('  October book total  1373.60 ha')
    print('')
    print('ROLLBACK OF DATA (run in this order):')
    for line in rollback:
        print('  ' + line)
    print("  DELETE FROM schema_migrations WHERE name = '%s';" % MIGRATION_ID)
    return 0


if __name__ == '__main__':
    sys.exit(main())
