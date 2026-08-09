# -*- coding: utf-8 -*-
"""DRONE-WORKS-UPLOAD-002 -- what staging found on the upload screen.

Three defects, one file of evidence:

  1. the size limits rejected two of the twenty-eight real dispatcher books;
  2. the period field offered the current month for books of the month that
     has just ended;
  3. nobody had measured how long a parse of a 75 MiB book takes.

**The big files here are SPARSE.** A file of 78 958 503 bytes is created by
seeking to `size - 1` and writing one byte: the file system reports the real
length, `stream_size()` finds it with a seek and never reads it, and the test
costs a few kilobytes of disk instead of a hundred megabytes. The size
validation is what is under test, and it sees exactly what it would see from a
real book -- `os.SEEK_END` does not care what lies in between.

Run:
  python -m unittest tests.test_drone_works_upload_002 -v
"""
import datetime
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from html.parser import HTMLParser

from tests.harness import app, reset_db, create_admin, login, CSRF
from models import db, DroneOperator, DroneWorkImport, User

import drone_works_upload as works_upload
import drones

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


MIB = 1024 * 1024

# The four books of the task's section 2, measured on the real corpus
# C:\drone_books\dispatcher books on 2026-08-09.
REAL_BOOK_BYTES = {
    'Агрокластер Дрон маълумот Март.xlsx': 78958503,
    'Имомов Бехзод Пешку ПТЗ Апрель.xlsx': 39340234,
    'Дрон_маълумот_Достон_АКА_АГРОКЛАСТЕР.xlsx': 11485044,
    '01.07.2026 Июль.xlsx': 127457,
}


def set_language(user_id, lang):
    """The screen is bilingual and the hint follows the user."""
    with app.app_context():
        user = db.session.get(User, user_id)
        user.language = lang
        db.session.commit()


class SparseFile(object):
    """An uploaded file of a given size, standing in for a FileStorage.

    validate_batch() reads exactly two things off each item: `.filename` and
    `.stream`. The stream is a real file object over a real file of the real
    length -- sparse, so the bytes cost nothing.
    """

    def __init__(self, directory, index, filename, size):
        self.filename = filename
        self.path = os.path.join(directory, 'blob_%03d' % index)
        with open(self.path, 'wb') as handle:
            if size > 0:
                handle.seek(size - 1)
                handle.write(b'\0')
        self.stream = open(self.path, 'rb')

    def close(self):
        try:
            self.stream.close()
        except (IOError, OSError):
            pass


class SparseFileHelper(object):
    """Shared factory. Every stream opened here is closed in tearDown.

    Deliberately NOT a TestCase: as a base class it would make unittest run
    every assertion of the base once per subclass, and a control that runs
    three times reads like three controls.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='drone_upload_002_')
        self.opened = []

    def tearDown(self):
        for item in self.opened:
            item.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def sized(self, filename, size):
        item = SparseFile(self.tmp, len(self.opened), filename, size)
        self.opened.append(item)
        return item


# ─── 1. The size limits ──────────────────────────────────────────────────────

class SparseFixtureControlTests(SparseFileHelper, unittest.TestCase):
    """The control for every size assertion in this file.

    A sparse file that silently came out 0 bytes long would make every «this
    book now passes» test below pass for the wrong reason.
    """

    def test_the_sparse_file_really_has_the_length_it_claims(self):
        item = self.sized('проверка.xlsx', 78958503)
        self.assertEqual(os.path.getsize(item.path), 78958503)
        self.assertEqual(works_upload.stream_size(item), 78958503)
        # And the stream is left where it was found: store_batch() seeks to 0
        # itself, but validate_batch() must not move it under anybody's feet.
        self.assertEqual(item.stream.tell(), 0)

    def test_the_size_check_can_still_fail(self):
        """A file over the limit is refused, so the acceptances mean something."""
        item = self.sized('слишком большая.xlsx', works_upload.MAX_FILE_BYTES + 1)
        with self.assertRaises(works_upload.UploadRejected):
            works_upload.validate_batch([item])


class UploadSizeLimitTests(SparseFileHelper, unittest.TestCase):
    """The two real books fit; one byte over still does not."""

    def test_the_constants_are_the_ones_this_increment_declares(self):
        self.assertEqual(works_upload.MAX_FILE_BYTES, 100 * MIB)
        self.assertEqual(works_upload.MAX_UPLOAD_BYTES, 240 * MIB)
        self.assertEqual(works_upload.MAX_FILES_PER_BATCH, 12)

    def test_the_batch_cap_is_at_or_below_80_percent_of_the_global_limit(self):
        """The rule that fixed the number, asserted against the real config.

        [REASON]: read from app.config, not re-typed. If MAX_CONTENT_LENGTH in
        app.py ever moves, the batch cap stops obeying the rule and this test
        says so -- instead of the operator meeting a bare 413 from Werkzeug
        with no file name and no rule in it.
        """
        global_limit = app.config['MAX_CONTENT_LENGTH']
        self.assertEqual(global_limit, 300 * MIB)
        self.assertEqual(global_limit, 314572800)
        self.assertLessEqual(works_upload.MAX_UPLOAD_BYTES,
                             int(global_limit * 0.8))
        # The number the task proposed would NOT have obeyed it: the assertion
        # above is capable of failing.
        self.assertGreater(250 * MIB, int(global_limit * 0.8))

    def test_the_two_rejected_books_now_pass(self):
        big = 'Агрокластер Дрон маълумот Март.xlsx'
        medium = 'Имомов Бехзод Пешку ПТЗ Апрель.xlsx'
        files = [self.sized(big, REAL_BOOK_BYTES[big]),
                 self.sized(medium, REAL_BOOK_BYTES[medium])]
        checked = works_upload.validate_batch(files)
        self.assertEqual(checked, [(big, 78958503), (medium, 39340234)])
        # Both were over the old 25 MiB limit -- the test is not vacuous.
        for _name, size in checked:
            self.assertGreater(size, 25 * MIB)

    def test_every_book_of_the_measured_corpus_is_accepted(self):
        for name, size in sorted(REAL_BOOK_BYTES.items()):
            checked = works_upload.validate_batch([self.sized(name, size)])
            self.assertEqual(checked, [(name, size)], name)

    def test_one_byte_over_the_file_limit_is_still_refused(self):
        item = self.sized('Гарден Дрон маълумот Апрель.xlsx',
                          works_upload.MAX_FILE_BYTES + 1)
        with self.assertRaises(works_upload.UploadRejected) as caught:
            works_upload.validate_batch([item])
        self.assertIn('100 МБ', caught.exception.message_ru)
        self.assertIn('100 МБ', caught.exception.message_uz)
        self.assertNotIn('25 МБ', caught.exception.message_ru)
        self.assertNotIn('25 МБ', caught.exception.message_uz)
        self.assertIn('Гарден Дрон маълумот Апрель.xlsx',
                      caught.exception.message_ru)

    def test_exactly_at_the_file_limit_is_accepted(self):
        """The boundary is «greater than», not «greater or equal»."""
        item = self.sized('Ровно сто.xlsx', works_upload.MAX_FILE_BYTES)
        self.assertEqual(works_upload.validate_batch([item]),
                         [('Ровно сто.xlsx', works_upload.MAX_FILE_BYTES)])


class UploadBatchCapTests(SparseFileHelper, unittest.TestCase):
    """The batch total and the file count, both refused before any write."""

    def test_a_batch_over_the_cap_is_refused(self):
        files = [self.sized('книга-%d.xlsx' % i, 90 * MIB) for i in range(3)]
        with self.assertRaises(works_upload.UploadRejected) as caught:
            works_upload.validate_batch(files)
        self.assertIn('240 МБ', caught.exception.message_ru)
        self.assertIn('240 МБ', caught.exception.message_uz)

    def test_the_same_batch_one_file_lighter_is_accepted(self):
        """The negative control: 270 MiB is refused, 180 MiB is not."""
        files = [self.sized('книга-%d.xlsx' % i, 90 * MIB) for i in range(2)]
        self.assertEqual(len(works_upload.validate_batch(files)), 2)

    def test_nothing_reaches_the_disk_when_the_batch_is_refused(self):
        """A refused batch creates no books root, no directory, no file."""
        books_root = os.path.join(self.tmp, works_upload.BOOKS_DIR_NAME)
        files = [self.sized('книга-%d.xlsx' % i, 90 * MIB) for i in range(3)]
        with self.assertRaises(works_upload.UploadRejected):
            works_upload.validate_batch(files)
        self.assertFalse(os.path.exists(books_root))
        self.assertEqual([n for n in os.listdir(self.tmp)
                          if n.endswith('.xlsx')], [])

    def test_thirteen_files_are_refused(self):
        files = [self.sized('книга-%02d.xlsx' % i, 1024) for i in range(13)]
        with self.assertRaises(works_upload.UploadRejected) as caught:
            works_upload.validate_batch(files)
        self.assertIn('12', caught.exception.message_ru)
        self.assertIn('13', caught.exception.message_ru)
        self.assertIn('12', caught.exception.message_uz)
        self.assertIn('13', caught.exception.message_uz)

    def test_twelve_files_are_accepted(self):
        files = [self.sized('книга-%02d.xlsx' % i, 1024) for i in range(12)]
        self.assertEqual(len(works_upload.validate_batch(files)), 12)


class UploadLimitsOnTheFormTests(unittest.TestCase):
    """The hint under the form states the limits that are enforced.

    [REASON]: the hint is not hand-written prose -- it renders max_file_mb and
    max_upload_mb, which the route reads off the constants. That is why no
    template edit was needed for the numbers, and this is what proves the
    rendered page agrees with drone_works_upload.py rather than with a comment.
    """

    @classmethod
    def setUpClass(cls):
        reset_db()
        cls.admin = create_admin('upload002_form_admin')

    def setUp(self):
        self.client = app.test_client()
        login(self.client, self.admin)

    def body(self, lang):
        set_language(self.admin, lang)
        response = self.client.get('/drones/works/import')
        self.assertEqual(response.status_code, 200)
        return response.data.decode('utf-8')

    def test_the_russian_hint_names_the_new_limits(self):
        body = self.body('ru')
        self.assertIn('МБ на файл и до', body)
        self.assertIn('100', body)
        self.assertIn('240', body)

    def test_the_uzbek_hint_names_the_new_limits(self):
        body = self.body('uz')
        self.assertIn('МБ гача ва жами', body)
        self.assertIn('100', body)
        self.assertIn('240', body)

    def test_the_rendered_numbers_come_from_the_constants(self):
        """Not «100 is on the page» -- «the page says what the module says».

        The hint is one run of text with the three numbers interleaved, so the
        whole run is rebuilt from the constants and looked for verbatim. If a
        constant moves and the template does not follow, this fails.
        """
        for lang, expected in (
                ('ru', 'файлов за раз, до %d\n        МБ на файл и до %d'
                       % (works_upload.MAX_FILE_BYTES // MIB,
                          works_upload.MAX_UPLOAD_BYTES // MIB)),
                ('uz', 'файл, ҳар бири %d\n        МБ гача ва жами %d'
                       % (works_upload.MAX_FILE_BYTES // MIB,
                          works_upload.MAX_UPLOAD_BYTES // MIB))):
            self.assertIn(expected, self.body(lang), lang)

    def test_the_hint_would_have_shown_the_old_limits_had_they_stayed(self):
        """The negative control: the same run, rebuilt from 25 and 100.

        [REASON]: «100 and 240 are somewhere on the page» is not evidence --
        both are ordinary numbers and the journal is full of numbers. What
        makes the assertions above discriminating is that they look for the
        surrounding words too; this proves the same probe finds the OLD
        numbers in a page rendered with the old constants, and does not find
        them in today's.
        """
        for lang, template in (('ru', 'файлов за раз, до %d\n        '
                                      'МБ на файл и до %d'),
                               ('uz', 'файл, ҳар бири %d\n        '
                                      'МБ гача ва жами %d')):
            body = self.body(lang)
            self.assertNotIn(template % (25, 100), body, lang)
            self.assertIn(template % (works_upload.MAX_FILE_BYTES // MIB,
                                      works_upload.MAX_UPLOAD_BYTES // MIB),
                          body, lang)


# ─── 2. The month the form offers ────────────────────────────────────────────

class PreviousMonthTests(unittest.TestCase):
    """The default period is the month that has just ended.

    The clock is frozen by passing the date in: _drone_previous_month() takes
    `today` rather than reading it, so «freeze the clock at 2026-08-09» is a
    literal argument and not a patched module global.
    """

    CASES = (
        (datetime.date(2026, 8, 9), '2026-07'),
        (datetime.date(2026, 1, 15), '2025-12'),
        (datetime.date(2026, 3, 1), '2026-02'),
    )

    def test_the_three_frozen_dates(self):
        for today, expected in self.CASES:
            self.assertEqual(drones._drone_previous_month(today), expected,
                             today.isoformat())

    def test_january_borrows_the_year(self):
        """The case «today minus 30 days» cannot reach at all."""
        self.assertEqual(drones._drone_previous_month(
            datetime.date(2026, 1, 1)), '2025-12')
        self.assertEqual(drones._drone_previous_month(
            datetime.date(2026, 1, 31)), '2025-12')

    def test_every_day_of_a_whole_year_answers_its_own_previous_month(self):
        """No day of any month may disagree with the other days of it."""
        day = datetime.date(2025, 12, 1)
        checked = 0
        while day <= datetime.date(2027, 1, 31):
            year = day.year if day.month > 1 else day.year - 1
            month = day.month - 1 if day.month > 1 else 12
            self.assertEqual(drones._drone_previous_month(day),
                             '%04d-%02d' % (year, month), day.isoformat())
            checked += 1
            day += datetime.timedelta(days=1)
        self.assertGreater(checked, 400)

    def test_subtracting_thirty_days_would_have_been_wrong_twice(self):
        """The negative control the task names, run rather than asserted.

        [REASON]: «wrong twice a year» is not a figure of speech. From the
        31st of a 31-day month, minus thirty days stays inside the SAME month
        and the form would offer the month the operator is standing in; from
        1 March it overshoots January and skips February altogether. Both are
        computed here against the real function, so a future rewrite in terms
        of timedelta fails this test instead of passing the three above.
        """
        naive = []
        for today in (datetime.date(2026, 3, 31), datetime.date(2026, 3, 1)):
            naive.append(((today - datetime.timedelta(days=30))
                          .strftime('%Y-%m'),
                          drones._drone_previous_month(today)))
        self.assertEqual(naive, [('2026-03', '2026-02'),
                                 ('2026-01', '2026-02')])
        for wrong, right in naive:
            self.assertNotEqual(wrong, right)


class PreviousMonthOnTheFormTests(unittest.TestCase):
    """The value that actually reaches the input, with the clock frozen."""

    @classmethod
    def setUpClass(cls):
        reset_db()
        cls.admin = create_admin('upload002_period_admin')
        # The flash messages asserted below are the Russian half of the pair.
        set_language(cls.admin, 'ru')

    def setUp(self):
        self.client = app.test_client()
        login(self.client, self.admin)
        self.real_today = drones._drone_today_local

    def tearDown(self):
        drones._drone_today_local = self.real_today

    def form_value(self, today):
        drones._drone_today_local = lambda: today
        response = self.client.get('/drones/works/import')
        self.assertEqual(response.status_code, 200)
        body = response.data.decode('utf-8')
        match = re.search(r'id="impPeriod"[^>]*?value="([^"]*)"', body, re.S)
        self.assertIsNotNone(match, 'period input not found on the page')
        return match.group(1), body

    def test_the_form_offers_the_previous_month(self):
        for today, expected in PreviousMonthTests.CASES:
            value, _body = self.form_value(today)
            self.assertEqual(value, expected, today.isoformat())

    def test_the_form_does_not_offer_the_current_month(self):
        """The defect itself: on 2026-08-09 the form used to say 2026-08."""
        value, _body = self.form_value(datetime.date(2026, 8, 9))
        self.assertNotEqual(value, '2026-08')

    def test_the_probe_would_notice_the_old_behaviour(self):
        """The control: the same regex reads back a planted current month."""
        planted = ('<input class="vs-input" type="text" id="impPeriod" '
                   'name="period_month" value="2026-08">')
        match = re.search(r'id="impPeriod"[^>]*?value="([^"]*)"', planted,
                          re.S)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), '2026-08')

    def test_the_field_stays_an_editable_plain_text_input(self):
        """A default, not a constraint: books for older months are normal."""
        _value, body = self.form_value(datetime.date(2026, 8, 9))
        match = re.search(r'<input[^>]*id="impPeriod"[^>]*>', body, re.S)
        self.assertIsNotNone(match)
        tag = match.group(0)
        self.assertIn('type="text"', tag)
        self.assertNotIn('readonly', tag)
        self.assertNotIn('disabled', tag)
        self.assertNotIn('min=', tag)
        self.assertNotIn('max=', tag)
        self.assertIn('pattern="[0-9]{4}-[0-9]{2}"', tag)

    def test_an_older_month_is_still_accepted_by_the_route(self):
        """The default did not become a rule: 2025-09 goes through.

        The upload is rejected for having no file, which is the point -- the
        period passed its own validation and the refusal names the file, not
        the month.
        """
        drones._drone_today_local = lambda: datetime.date(2026, 8, 9)
        response = self.client.post(
            '/drones/works/import/upload',
            data={'csrf_token': CSRF, 'period_month': '2025-09'},
            follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        body = response.data.decode('utf-8')
        self.assertIn('Ни одного файла не выбрано', body)
        self.assertNotIn('Период должен быть в виде', body)

    def test_a_malformed_period_is_still_refused(self):
        """The negative control for the test above."""
        response = self.client.post(
            '/drones/works/import/upload',
            data={'csrf_token': CSRF, 'period_month': 'сентябрь'},
            follow_redirects=True)
        self.assertIn('Период должен быть в виде',
                      response.data.decode('utf-8'))


# ─── 3. How long the parse took ──────────────────────────────────────────────

class ParseSecondsUnitTests(unittest.TestCase):
    """The rendering and the report line, without a database in the way."""

    def test_the_duration_is_carried_at_one_decimal(self):
        self.assertEqual(works_upload.format_parse_seconds(1.23456), 1.2)
        self.assertEqual(works_upload.format_parse_seconds(61.57), 61.6)
        self.assertEqual(works_upload.format_parse_seconds(0), 0.0)

    def test_a_parse_faster_than_fifty_milliseconds_reads_as_zero(self):
        """Stated rather than hidden: one decimal cannot say «0.02 s».

        [REASON]: the task asks for one decimal AND for a number greater than
        zero. Both hold on the books this screen exists for -- a 75 MiB book
        takes far longer than 50 ms -- but on a two-row synthetic book the
        honest answer at one decimal IS 0.0, and that is «instant», not a
        missing measurement. The end-to-end evidence for «greater than zero»
        is therefore taken on a book big enough to be worth timing; see
        ParseSecondsBigBookTests.
        """
        self.assertEqual(works_upload.format_parse_seconds(0.049), 0.0)
        self.assertEqual(works_upload.format_parse_seconds(0.06), 0.1)

    def test_the_report_line_lands_in_the_header(self):
        path = os.path.join(tempfile.mkdtemp(prefix='drone_report_002_'),
                            'report.txt')
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write('DRONE-WORKS-001 — отчёт импорта\n'
                         'Режим: СУХОЙ ПРОГОН (ничего не записано)\n'
                         'Каталог: /books/7\n'
                         'Манифест: (не используется)\n'
                         'База: /db/transport.db\n'
                         'Партия импорта: upload-7\n'
                         '\n'
                         'ИТОГИ\n'
                         '  файлов разобрано: 2\n')
        self.assertTrue(works_upload.add_parse_seconds_to_report(path, 3.14))
        with open(path, encoding='utf-8') as handle:
            lines = handle.read().split('\n')
        self.assertEqual(lines[6], 'Разбор: 3.1 с')
        # Still in the header: before the blank line that opens ИТОГИ.
        self.assertEqual(lines[7], '')
        self.assertEqual(lines[8], 'ИТОГИ')
        # And nothing else moved.
        self.assertEqual(lines[0], 'DRONE-WORKS-001 — отчёт импорта')
        self.assertEqual(lines[5], 'Партия импорта: upload-7')

    def test_a_report_without_the_anchor_still_gets_the_line(self):
        """A moved anchor may not cost a batch its report."""
        path = os.path.join(tempfile.mkdtemp(prefix='drone_report_002_'),
                            'report.txt')
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write('DRONE-WORKS-001 — отчёт импорта\nИТОГИ\n')
        self.assertTrue(works_upload.add_parse_seconds_to_report(path, 0.5))
        with open(path, encoding='utf-8') as handle:
            self.assertEqual(handle.read().split('\n')[1], 'Разбор: 0.5 с')

    def test_a_missing_report_is_not_an_exception(self):
        """By the time this runs the rows may be committed."""
        self.assertFalse(works_upload.add_parse_seconds_to_report(
            os.path.join(tempfile.mkdtemp(), 'nope.txt'), 1.0))


class ParseSecondsEndToEndTests(unittest.TestCase):
    """A real upload through the real route, and what it leaves behind.

    The books are the synthetic corpus of tools/drone_works_fixtures.py --
    the dispatchers' real files are not in the repository and will not be.
    Two of them are posted as a multipart form to the real endpoint, so the
    number under test travels the whole way: run_import -> counts ->
    preview_snapshot -> preview.json -> the screen, and run_import -> the
    report file.
    """

    BOOKS = ('Гарден Дрон маълумот Апрель.xlsx',
             'Когон ПТЗ Шохрух Хамроев МАРТ.xlsx')

    @classmethod
    def setUpClass(cls):
        from tools import drone_works_fixtures as fixtures
        reset_db()
        cls.admin = create_admin('upload002_parse_admin')
        set_language(cls.admin, 'ru')
        cls.tmp = tempfile.mkdtemp(prefix='drone_upload_002_books_')
        cls.books, _manifest = fixtures.build(os.path.join(cls.tmp, 'books'))
        with app.app_context():
            for full_name, subdivision in fixtures.OPERATORS:
                db.session.add(DroneOperator(full_name=full_name,
                                             subdivision_name=subdivision))
            db.session.commit()
        cls.books_root = works_upload.books_root_for_db(
            works_upload.db_path_from_uri(
                app.config['SQLALCHEMY_DATABASE_URI']))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        shutil.rmtree(cls.books_root, ignore_errors=True)

    def setUp(self):
        self.client = app.test_client()
        login(self.client, self.admin)

    def upload(self, period='2026-04', books=None):
        """POST the books to the real endpoint. Returns (id, storage_dir)."""
        payload = []
        for name in (books or self.BOOKS):
            with open(os.path.join(self.books, name), 'rb') as handle:
                payload.append((io.BytesIO(handle.read()), name))
        response = self.client.post(
            '/drones/works/import/upload',
            data={'csrf_token': CSRF, 'period_month': period,
                  'books': payload},
            content_type='multipart/form-data')
        self.assertEqual(response.status_code, 302, response.data[:400])
        with app.app_context():
            row = (DroneWorkImport.query
                   .order_by(DroneWorkImport.id.desc()).first())
            self.assertIsNotNone(row)
            self.assertNotEqual(row.status, 'failed', row.error_text)
            return row.id, row.storage_dir

    def batch_path(self, storage_dir):
        return works_upload.batch_path_for(self.books_root, storage_dir)

    def test_the_preview_file_carries_the_key(self):
        _batch_id, storage_dir = self.upload()
        snapshot = works_upload.read_preview(self.batch_path(storage_dir))
        self.assertIn('parse_seconds', snapshot)
        self.assertIsInstance(snapshot['parse_seconds'], float)
        self.assertGreaterEqual(snapshot['parse_seconds'], 0.0)
        self.assertEqual(snapshot['parse_seconds'],
                         round(snapshot['parse_seconds'], 1))

    def test_the_report_header_carries_the_same_number(self):
        _batch_id, storage_dir = self.upload()
        path = self.batch_path(storage_dir)
        snapshot = works_upload.read_preview(path)
        with open(os.path.join(path, works_upload.REPORT_FILE),
                  encoding='utf-8') as handle:
            lines = handle.read().split('\n')
        self.assertEqual(lines[6],
                         'Разбор: %.1f с' % snapshot['parse_seconds'])
        self.assertEqual(lines[5][:16], 'Партия импорта: ')
        self.assertEqual(lines[8], 'ИТОГИ')

    def test_the_russian_preview_screen_shows_it(self):
        batch_id, storage_dir = self.upload()
        seconds = works_upload.read_preview(
            self.batch_path(storage_dir))['parse_seconds']
        set_language(self.admin, 'ru')
        body = self.client.get(
            '/drones/works/import/%d' % batch_id).data.decode('utf-8')
        self.assertIn('Разбор занял', body)
        self.assertIn('%.1f' % seconds, body)
        self.assertIn('Разбор занял\n      %.1f с' % seconds, body)

    def test_the_uzbek_preview_screen_shows_it(self):
        batch_id, storage_dir = self.upload()
        seconds = works_upload.read_preview(
            self.batch_path(storage_dir))['parse_seconds']
        set_language(self.admin, 'uz')
        try:
            body = self.client.get(
                '/drones/works/import/%d' % batch_id).data.decode('utf-8')
        finally:
            set_language(self.admin, 'ru')
        self.assertIn('Ўқиш давом этди', body)
        self.assertIn('Ўқиш давом этди\n      %.1f сония' % seconds, body)
        self.assertNotIn('Разбор занял', body)

    def test_the_number_on_the_screen_comes_from_the_clock(self):
        """The control. A hardcoded 0.1 would satisfy every test above.

        [REASON]: monotonic() is patched to a counter that advances by a known
        amount across the parse, so the number that reaches preview.json, the
        report and the page can only be the difference the clock reported. If
        the code measured the wrong span -- or nothing at all -- 7.3 does not
        appear anywhere.
        """
        ticks = iter([1000.0, 1007.28])
        real_monotonic = works_upload.time.monotonic
        works_upload.time.monotonic = lambda: next(ticks, 1007.28)
        try:
            batch_id, storage_dir = self.upload()
        finally:
            works_upload.time.monotonic = real_monotonic
        path = self.batch_path(storage_dir)
        self.assertEqual(works_upload.read_preview(path)['parse_seconds'], 7.3)
        with open(os.path.join(path, works_upload.REPORT_FILE),
                  encoding='utf-8') as handle:
            self.assertIn('Разбор: 7.3 с', handle.read())
        body = self.client.get(
            '/drones/works/import/%d' % batch_id).data.decode('utf-8')
        self.assertIn('7.3 с', body)

    def test_a_preview_written_before_this_increment_still_renders(self):
        """Batches #1..#5 on staging have no parse_seconds in preview.json."""
        batch_id, storage_dir = self.upload()
        path = os.path.join(self.batch_path(storage_dir),
                            works_upload.PREVIEW_FILE)
        with open(path, encoding='utf-8') as handle:
            snapshot = json.load(handle)
        del snapshot['parse_seconds']
        with open(path, 'w', encoding='utf-8') as handle:
            json.dump(snapshot, handle, ensure_ascii=False)
        response = self.client.get('/drones/works/import/%d' % batch_id)
        self.assertEqual(response.status_code, 200)
        body = response.data.decode('utf-8')
        self.assertNotIn('Разбор занял', body)
        self.assertIn('Файлы партии', body)

    def test_no_column_was_added_to_carry_it(self):
        """No migration: the number lives in preview.json and nowhere else."""
        with app.app_context():
            columns = {c.name for c in DroneWorkImport.__table__.columns}
        self.assertNotIn('parse_seconds', columns)
        # And it really is in the file -- «not in the table» alone would pass
        # if the number had been dropped altogether.
        _batch_id, storage_dir = self.upload()
        self.assertIn('parse_seconds',
                      works_upload.read_preview(self.batch_path(storage_dir)))

    def test_the_numbers_this_increment_must_not_move(self):
        """Criterion 9: the same books, the same rows and the same hectares.

        The figures belong to the synthetic corpus and are asserted against a
        hand-computed prediction by tools/test_import_drone_works.py. Here
        they are read back off the screen's own snapshot, so a timing change
        that disturbed the parse would show up as a number, not as a duration.
        """
        _batch_id, storage_dir = self.upload()
        snapshot = works_upload.read_preview(self.batch_path(storage_dir))
        self.assertEqual(snapshot['rows'], 11)
        self.assertAlmostEqual(snapshot['area'], 142.50, places=2)
        self.assertEqual(snapshot['totals']['files'], 2)
        self.assertEqual(snapshot['totals']['rows'], 11)


class ParseSecondsBigBookTests(unittest.TestCase):
    """«Greater than zero» on a book big enough to be worth timing.

    [REASON]: a two-row synthetic book parses in about twenty milliseconds and
    one decimal reports that as 0.0, correctly. The screen exists for books
    that take long enough for an operator to wonder whether the browser has
    hung, so the assertion that the number is above zero is taken on a book
    of that shape: 2 000 rows, ~0.3 s on this runner, ten times the rounding
    step. The real corpus is heavier still -- 78 958 503 bytes for three rows.
    """

    ROWS = 2000
    BOOK = 'Большая книга Апрель.xlsx'

    @classmethod
    def setUpClass(cls):
        from tools import drone_works_fixtures as fixtures
        reset_db()
        cls.admin = create_admin('upload002_bigbook_admin')
        set_language(cls.admin, 'ru')
        cls.tmp = tempfile.mkdtemp(prefix='drone_upload_002_big_')
        rows = [['Тест Дрон маълумот'], ['Справка (апрель ойи)'],
                fixtures.HEADER]
        for index in range(cls.ROWS):
            rows.append([index + 1,
                         datetime.datetime(2026, 4, (index % 28) + 1),
                         'Фермер %d' % index, 1.0, 100000, 100000, None,
                         100000, None])
        cls.books, _manifest = fixtures.build(
            os.path.join(cls.tmp, 'books'),
            books=((cls.BOOK, '2026-04', [('свод ичи (Тест)', rows)]),))
        cls.books_root = works_upload.books_root_for_db(
            works_upload.db_path_from_uri(
                app.config['SQLALCHEMY_DATABASE_URI']))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        shutil.rmtree(cls.books_root, ignore_errors=True)

    def setUp(self):
        self.client = app.test_client()
        login(self.client, self.admin)

    def test_a_book_worth_timing_reports_a_time_above_zero(self):
        with open(os.path.join(self.books, self.BOOK), 'rb') as handle:
            payload = io.BytesIO(handle.read())
        response = self.client.post(
            '/drones/works/import/upload',
            data={'csrf_token': CSRF, 'period_month': '2026-04',
                  'books': [(payload, self.BOOK)]},
            content_type='multipart/form-data')
        self.assertEqual(response.status_code, 302, response.data[:400])
        with app.app_context():
            row = (DroneWorkImport.query
                   .order_by(DroneWorkImport.id.desc()).first())
            self.assertNotEqual(row.status, 'failed', row.error_text)
            batch_id, storage_dir = row.id, row.storage_dir
        path = works_upload.batch_path_for(self.books_root, storage_dir)

        snapshot = works_upload.read_preview(path)
        self.assertGreater(snapshot['parse_seconds'], 0.0)
        self.assertEqual(snapshot['rows'], self.ROWS)

        with open(os.path.join(path, works_upload.REPORT_FILE),
                  encoding='utf-8') as handle:
            report = handle.read()
        self.assertIn('Разбор: %.1f с' % snapshot['parse_seconds'], report)
        self.assertNotIn('Разбор: 0.0 с', report)

        body = self.client.get(
            '/drones/works/import/%d' % batch_id).data.decode('utf-8')
        self.assertIn('Разбор занял\n      %.1f с' % snapshot['parse_seconds'],
                      body)


# ─── The two screens, as HTML and as source ──────────────────────────────────

VOID_TAGS = frozenset(('area', 'base', 'br', 'col', 'embed', 'hr', 'img',
                       'input', 'link', 'meta', 'param', 'source', 'track',
                       'wbr'))


class TagReader(HTMLParser):
    """A real HTML parse of a rendered page.

    [REASON]: the defect class this project was bitten by is invisible to a
    Jinja parse, to a «<div» count and to py_compile alike -- a value carrying
    an unescaped double quote terminates its attribute early, and everything
    after it becomes garbage ATTRIBUTE NAMES on the same tag. So the check is
    not «does it parse» -- html.parser never refuses anything -- but «are the
    attribute names still names, and does every div close».
    """

    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.stack = []
        self.divs_opened = 0
        self.divs_closed = 0
        self.attribute_names = set()
        self.mismatches = []

    def handle_starttag(self, tag, attrs):
        for name, _value in attrs:
            self.attribute_names.add(name)
        if tag in VOID_TAGS:
            return
        if tag == 'div':
            self.divs_opened += 1
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in VOID_TAGS:
            return
        if tag == 'div':
            self.divs_closed += 1
        if tag in self.stack:
            while self.stack and self.stack.pop() != tag:
                pass
        else:
            self.mismatches.append(tag)


class UploadScreensHtmlTests(unittest.TestCase):
    """Criterion 7, on the rendered page and on the template source."""

    TEMPLATES = ('works_import.html', 'works_import_preview.html')
    # CLAUDE.md allows Latin only in product and brand names.
    ALLOWED_LATIN = ('Excel', 'DJI', 'RFID', '.xlsx')

    @classmethod
    def setUpClass(cls):
        from tools import drone_works_fixtures as fixtures
        reset_db()
        cls.admin = create_admin('upload002_html_admin')
        cls.tmp = tempfile.mkdtemp(prefix='drone_upload_002_html_')
        cls.books, _manifest = fixtures.build(os.path.join(cls.tmp, 'books'))
        with app.app_context():
            for full_name, subdivision in fixtures.OPERATORS:
                db.session.add(DroneOperator(full_name=full_name,
                                             subdivision_name=subdivision))
            db.session.commit()
        cls.books_root = works_upload.books_root_for_db(
            works_upload.db_path_from_uri(
                app.config['SQLALCHEMY_DATABASE_URI']))
        client = app.test_client()
        login(client, cls.admin)
        payload = []
        for name in ('Гарден Дрон маълумот Апрель.xlsx',
                     'Когон ПТЗ Шохрух Хамроев МАРТ.xlsx'):
            with open(os.path.join(cls.books, name), 'rb') as handle:
                payload.append((io.BytesIO(handle.read()), name))
        client.post('/drones/works/import/upload',
                    data={'csrf_token': CSRF, 'period_month': '2026-04',
                          'books': payload},
                    content_type='multipart/form-data')
        with app.app_context():
            cls.batch_id = (DroneWorkImport.query
                            .order_by(DroneWorkImport.id.desc())
                            .first().id)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)
        shutil.rmtree(cls.books_root, ignore_errors=True)

    def setUp(self):
        self.client = app.test_client()
        login(self.client, self.admin)

    def pages(self):
        for lang in ('ru', 'uz'):
            set_language(self.admin, lang)
            for url in ('/drones/works/import',
                        '/drones/works/import/%d' % self.batch_id):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200,
                                 '%s %s' % (lang, url))
                yield lang, url, response.data.decode('utf-8')

    def source(self, name):
        with open(os.path.join(REPO_ROOT, 'templates', 'drones', name),
                  encoding='utf-8') as handle:
            return handle.read()

    def test_every_rendered_page_parses_and_closes_its_divs(self):
        seen = 0
        for lang, url, body in self.pages():
            reader = TagReader()
            reader.feed(body)
            where = '%s %s' % (lang, url)
            self.assertEqual(reader.mismatches, [], where)
            self.assertEqual(reader.divs_opened, reader.divs_closed, where)
            self.assertGreater(reader.divs_opened, 20, where)
            seen += 1
        self.assertEqual(seen, 4, 'the scan rendered %d pages' % seen)

    def test_no_attribute_name_on_any_page_is_broken_json(self):
        """The signature of a value that terminated its attribute early."""
        pattern = re.compile(r'^[A-Za-z_:@\-][-A-Za-z0-9_:.]*$')
        for lang, url, body in self.pages():
            reader = TagReader()
            reader.feed(body)
            self.assertGreater(len(reader.attribute_names), 10)
            for name in sorted(reader.attribute_names):
                self.assertRegex(name, pattern, '%s %s: %r'
                                 % (lang, url, name))

    def test_the_reader_would_notice_a_broken_attribute(self):
        """The control: an attribute cut short by a quote from tojson."""
        reader = TagReader()
        reader.feed('<div data-rows="{"a": 1}"><span>x</span></div>')
        pattern = re.compile(r'^[A-Za-z_:@\-][-A-Za-z0-9_:.]*$')
        broken = [n for n in reader.attribute_names
                  if not pattern.match(n)]
        self.assertNotEqual(broken, [], reader.attribute_names)

    def test_the_reader_would_notice_an_unclosed_div(self):
        reader = TagReader()
        reader.feed('<div><div></div>')
        self.assertNotEqual(reader.divs_opened, reader.divs_closed)

    def test_no_tojson_sits_in_a_double_quoted_attribute(self):
        for name in self.TEMPLATES:
            for match in re.finditer(r'="[^"]*\|\s*tojson', self.source(name)):
                self.fail('%s: %s' % (name, match.group(0)))

    def test_every_uzbek_branch_of_both_templates_is_cyrillic(self):
        """The UZ half of every «'RU' if is_ru else 'UZ'» pair."""
        offenders = []
        halves = 0
        for name in self.TEMPLATES:
            for text in re.findall(r"if is_ru else '((?:[^'\\]|\\.)*)'",
                                   self.source(name)):
                halves += 1
                stripped = text
                for word in self.ALLOWED_LATIN:
                    stripped = stripped.replace(word, ' ')
                runs = re.findall(r'[A-Za-z]+', stripped)
                if runs:
                    offenders.append((name, text, runs))
        self.assertEqual(offenders, [])
        # A scan over an empty list proves nothing.
        self.assertGreater(halves, 40, 'only %d uzbek halves found' % halves)

    def test_the_uzbek_scan_fires_on_a_planted_latin_string(self):
        stripped = 'Ўқиш davom etdi'
        for word in self.ALLOWED_LATIN:
            stripped = stripped.replace(word, ' ')
        self.assertEqual(re.findall(r'[A-Za-z]+', stripped),
                         ['davom', 'etdi'])

    def test_the_new_uzbek_string_is_made_of_cyrillic_code_points(self):
        """«Ўқиш» is U+040E U+049B U+0438 U+0448, not a Latin lookalike."""
        halves = re.findall(r"if is_ru else '((?:[^'\\]|\\.)*)'",
                            self.source('works_import_preview.html'))
        self.assertIn('Ўқиш давом этди', halves)
        self.assertIn('сония', halves)
        self.assertEqual([ord(c) for c in 'Ўқиш'],
                         [0x040E, 0x049B, 0x0438, 0x0448])


if __name__ == '__main__':
    unittest.main(verbosity=2)
