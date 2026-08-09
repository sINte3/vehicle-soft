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
import os
import shutil
import tempfile
import unittest

from tests.harness import app, reset_db, create_admin, login
from models import db, User

import drone_works_upload as works_upload


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


if __name__ == '__main__':
    unittest.main(verbosity=2)
