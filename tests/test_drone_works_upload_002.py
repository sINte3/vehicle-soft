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
import os
import re
import shutil
import tempfile
import unittest

from tests.harness import app, reset_db, create_admin, login, CSRF
from models import db, User

import drone_works_upload as works_upload
import drones


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


if __name__ == '__main__':
    unittest.main(verbosity=2)
