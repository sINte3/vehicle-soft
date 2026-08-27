# -*- coding: utf-8 -*-
"""Тесты сохранённой сессии (`drone_collector/session.py`).

Модуль до этой правки не имел тестов вовсе, и это ровно то место, где живой
пилот этапа B потерял первый запуск: `--save-session` отчитался успехом и
записал тридцать байт

    {"cookies": [], "origins": []}

Проверка была «файл есть и он не пустой», а такой файл не пустой. Поэтому
здесь проверяется СОДЕРЖИМОЕ, и у каждой проверки есть отрицательный контроль:
годное состояние обязано проходить, иначе «строгость» просто гасит работу.

Ни один тест не кладёт в фикстуру настоящее значение cookie: значения
выдуманные, и часть тестов как раз доказывает, что значения никуда не
печатаются.
"""

import json
import os
import tempfile
import unittest

from pathlib import Path

from drone_collector.session import (
    MAX_SESSION_BYTES,
    SessionMissing,
    inspect_session,
    landed_where_expected,
    login_url,
    require_session,
    save_state_atomically,
    session_exists,
)

# Тридцать байт первого живого запуска, дословно.
EMPTY_STATE_TEXT = '{"cookies": [], "origins": []}'

COOKIE_VALUE = 'SYNTHETIC-COOKIE-VALUE-NOT-REAL'
STORAGE_VALUE = 'SYNTHETIC-LOCALSTORAGE-VALUE-NOT-REAL'


def cookie_state():
    return {'cookies': [{'name': 'sid', 'value': COOKIE_VALUE,
                         'domain': '.example.invalid', 'path': '/'}],
            'origins': []}


def local_storage_state():
    return {'cookies': [],
            'origins': [{'origin': 'https://www.example.invalid',
                         'localStorage': [{'name': 'token',
                                           'value': STORAGE_VALUE}]}]}


class SessionFileTestCase(unittest.TestCase):

    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.root = Path(self._directory.name)
        self.target = self.root / 'storage_state.json'

    def write(self, text, path=None):
        path = Path(path or self.target)
        path.write_text(text, encoding='utf-8')
        return path

    def write_json(self, document, path=None):
        return self.write(json.dumps(document), path=path)


# ─── Что считается сессией ───────────────────────────────────────────────────

class TestInspectSession(SessionFileTestCase):

    def test_the_thirty_byte_state_of_the_first_pilot_is_refused(self):
        """Тот самый файл, который прошёл прежнюю проверку.

        [REASON]: прежний критерий -- `st_size > 0`. Тридцать байт больше нуля,
        поэтому пустое состояние Playwright засчитывалось как рабочая сессия.
        """
        path = self.write(EMPTY_STATE_TEXT)
        self.assertEqual(len(EMPTY_STATE_TEXT.encode('utf-8')), 30)
        state = inspect_session(path)
        self.assertFalse(state.usable)
        self.assertIn('never signed in', state.reason)
        self.assertEqual((state.cookies, state.local_storage_items), (0, 0))

    def test_a_cookie_only_state_is_accepted(self):
        state = inspect_session(self.write_json(cookie_state()))
        self.assertTrue(state.usable, state.reason)
        self.assertEqual(state.cookies, 1)
        self.assertEqual(state.local_storage_items, 0)

    def test_a_local_storage_only_state_is_accepted(self):
        state = inspect_session(self.write_json(local_storage_state()))
        self.assertTrue(state.usable, state.reason)
        self.assertEqual(state.cookies, 0)
        self.assertEqual(state.local_storage_items, 1)
        self.assertEqual(state.origins, 1)

    def test_a_state_shaped_like_the_real_one_is_accepted(self):
        """Форма настоящей сессии кабинета: 14 cookie, 2 origin, 13 items."""
        document = {
            'cookies': [{'name': 'c%d' % i, 'value': COOKIE_VALUE}
                        for i in range(14)],
            'origins': [
                {'origin': 'https://www.example.invalid',
                 'localStorage': [{'name': 'k%d' % i, 'value': STORAGE_VALUE}
                                  for i in range(7)]},
                {'origin': 'https://api.example.invalid',
                 'localStorage': [{'name': 'k%d' % i, 'value': STORAGE_VALUE}
                                  for i in range(6)]},
            ],
        }
        state = inspect_session(self.write_json(document))
        self.assertTrue(state.usable)
        self.assertEqual((state.cookies, state.origins,
                          state.local_storage_items), (14, 2, 13))

    def test_a_missing_file_is_refused(self):
        state = inspect_session(self.root / 'nothing.json')
        self.assertFalse(state.usable)
        self.assertEqual(state.reason, 'no such file')

    def test_an_empty_file_is_refused(self):
        state = inspect_session(self.write(''))
        self.assertFalse(state.usable)
        self.assertIn('empty', state.reason)

    def test_broken_json_is_refused(self):
        state = inspect_session(self.write('{"cookies": [', ))
        self.assertFalse(state.usable)
        self.assertIn('not readable JSON', state.reason)

    def test_a_json_array_is_refused(self):
        state = inspect_session(self.write('[]'))
        self.assertFalse(state.usable)
        self.assertIn('not to an object', state.reason)

    def test_a_wrong_type_for_cookies_is_refused(self):
        state = inspect_session(self.write_json({'cookies': {'sid': 'x'}}))
        self.assertFalse(state.usable)
        self.assertIn('"cookies" is dict', state.reason)

    def test_a_wrong_type_for_origins_is_refused(self):
        state = inspect_session(self.write_json({'cookies': [],
                                                 'origins': 'nope'}))
        self.assertFalse(state.usable)
        self.assertIn('"origins" is str', state.reason)

    def test_an_oversized_file_is_refused_without_being_parsed(self):
        path = self.write('{"cookies": [' + 'x' * (MAX_SESSION_BYTES + 16))
        state = inspect_session(path)
        self.assertFalse(state.usable)
        self.assertIn('the cap is', state.reason)

    def test_an_origin_without_local_storage_counts_zero_items(self):
        document = {'cookies': [],
                    'origins': [{'origin': 'https://x.invalid'}]}
        state = inspect_session(self.write_json(document))
        self.assertFalse(state.usable)
        self.assertEqual(state.local_storage_items, 0)

    def test_session_exists_agrees_with_inspect(self):
        self.assertFalse(session_exists(self.write(EMPTY_STATE_TEXT)))
        self.assertTrue(session_exists(self.write_json(cookie_state())))


class TestNoValueEverLeaks(SessionFileTestCase):
    """Ни одно значение не попадает ни в причину, ни в исключение."""

    def test_the_reason_never_quotes_a_cookie(self):
        document = cookie_state()
        document['cookies'] = {'sid': COOKIE_VALUE}   # неверный тип
        state = inspect_session(self.write_json(document))
        self.assertFalse(state.usable)
        self.assertNotIn(COOKIE_VALUE, state.reason)
        self.assertNotIn(COOKIE_VALUE, state.describe())
        self.assertNotIn(COOKIE_VALUE, repr(state))

    def test_the_reason_never_quotes_local_storage(self):
        document = local_storage_state()
        document['origins'] = STORAGE_VALUE            # неверный тип
        state = inspect_session(self.write_json(document))
        self.assertFalse(state.usable)
        self.assertNotIn(STORAGE_VALUE, state.reason)

    def test_require_session_names_the_defect_without_the_content(self):
        path = self.write_json({'cookies': [{'name': 'sid',
                                             'value': COOKIE_VALUE}],
                                'origins': 'nope'})
        with self.assertRaises(SessionMissing) as caught:
            require_session(path)
        message = str(caught.exception)
        self.assertNotIn(COOKIE_VALUE, message)
        self.assertIn('"origins" is str', message)

    def test_a_usable_session_passes_require_session(self):
        """Отрицательный контроль: проверка не отвергает рабочую сессию."""
        path = self.write_json(cookie_state())
        self.assertEqual(Path(require_session(path)), Path(path))


# ─── Атомарное сохранение ────────────────────────────────────────────────────

class _FakeContext(object):
    """Подставной контекст Playwright: пишет заданный текст, куда скажут."""

    def __init__(self, text):
        self.text = text
        self.written = []

    def storage_state(self, path):
        self.written.append(path)
        Path(path).write_text(self.text, encoding='utf-8')


class TestAtomicSave(SessionFileTestCase):

    def temps(self):
        return sorted(p.name for p in self.root.glob('*.partial'))

    def test_a_usable_state_replaces_the_target(self):
        context = _FakeContext(json.dumps(cookie_state()))
        state = save_state_atomically(context, self.target)
        self.assertTrue(state.usable)
        self.assertTrue(self.target.is_file())
        self.assertEqual(inspect_session(self.target).cookies, 1)

    def test_playwright_never_writes_to_the_target_itself(self):
        """Замена делается нами, а не чужой библиотекой.

        [REASON]: пока Playwright писал прямо в `storage_state.json`, КАЖДОЕ
        сохранение было разрушительным -- пустой контекст затирал рабочую
        сессию, и узнавали об этом на следующем прогоне.
        """
        context = _FakeContext(json.dumps(cookie_state()))
        save_state_atomically(context, self.target)
        self.assertEqual(len(context.written), 1)
        self.assertNotEqual(Path(context.written[0]), self.target)
        self.assertTrue(context.written[0].endswith('.partial'))

    def test_an_empty_state_does_not_destroy_a_working_session(self):
        self.write_json(cookie_state())
        before = self.target.read_text(encoding='utf-8')
        context = _FakeContext(EMPTY_STATE_TEXT)
        with self.assertRaises(SessionMissing) as caught:
            save_state_atomically(context, self.target)
        self.assertEqual(self.target.read_text(encoding='utf-8'), before)
        self.assertIn('left untouched', str(caught.exception))

    def test_broken_output_does_not_destroy_a_working_session(self):
        self.write_json(local_storage_state())
        before = self.target.read_text(encoding='utf-8')
        context = _FakeContext('{"cookies": [')
        with self.assertRaises(SessionMissing):
            save_state_atomically(context, self.target)
        self.assertEqual(self.target.read_text(encoding='utf-8'), before)

    def test_no_partial_file_is_left_behind_on_refusal(self):
        context = _FakeContext(EMPTY_STATE_TEXT)
        with self.assertRaises(SessionMissing):
            save_state_atomically(context, self.target)
        self.assertEqual(self.temps(), [])

    def test_no_partial_file_is_left_behind_on_success(self):
        context = _FakeContext(json.dumps(cookie_state()))
        save_state_atomically(context, self.target)
        self.assertEqual(self.temps(), [])

    def test_a_raising_writer_leaves_nothing_behind(self):
        class Boom(object):
            def storage_state(self, path):
                raise OSError('disk went away')

        self.write_json(cookie_state())
        before = self.target.read_text(encoding='utf-8')
        with self.assertRaises(OSError):
            save_state_atomically(Boom(), self.target)
        self.assertEqual(self.temps(), [])
        self.assertEqual(self.target.read_text(encoding='utf-8'), before)

    def test_the_refusal_names_no_value(self):
        self.write_json(cookie_state())
        context = _FakeContext(EMPTY_STATE_TEXT)
        with self.assertRaises(SessionMissing) as caught:
            save_state_atomically(context, self.target)
        self.assertNotIn(COOKIE_VALUE, str(caught.exception))

    def test_a_failed_replace_leaves_no_partial_and_keeps_the_target(self):
        """Замена может не пройти -- временный файл всё равно не остаётся.

        [REASON]: на Windows `os.replace` отклоняет и антивирус, и файл,
        открытый чужим процессом. Без уборки в обработчике каталог сессии
        зарастал `.partial`-ами после каждого такого отказа, при том что
        прежняя сессия цела и работает.
        """
        self.write_json(cookie_state())
        before = self.target.read_text(encoding='utf-8')
        context = _FakeContext(json.dumps(local_storage_state()))
        real_replace = os.replace

        def refuse(src, dst):
            if str(dst) == str(self.target):
                raise OSError('the file is in use by another process')
            return real_replace(src, dst)

        os.replace = refuse
        try:
            with self.assertRaises(OSError):
                save_state_atomically(context, self.target)
        finally:
            os.replace = real_replace
        self.assertEqual(self.temps(), [],
                         'отказ замены оставил .partial в каталоге сессии')
        self.assertEqual(self.target.read_text(encoding='utf-8'), before)

    def test_a_successful_replace_still_installs_the_state(self):
        """Отрицательный контроль: уборка не мешает нормальной замене."""
        context = _FakeContext(json.dumps(local_storage_state()))
        state = save_state_atomically(context, self.target)
        self.assertTrue(state.usable)
        self.assertEqual(inspect_session(self.target).local_storage_items, 1)
        self.assertEqual(self.temps(), [])

    def test_saving_without_a_previous_session_says_so(self):
        context = _FakeContext(EMPTY_STATE_TEXT)
        with self.assertRaises(SessionMissing) as caught:
            save_state_atomically(context, self.target)
        self.assertIn('no previous usable session', str(caught.exception))
        self.assertFalse(self.target.exists())


# ─── Где оказался браузер ────────────────────────────────────────────────────

class TestLandedWhereExpected(unittest.TestCase):

    RECORDS = 'https://www.example.invalid/records/list'

    def test_the_records_page_is_accepted(self):
        ok, why = landed_where_expected(self.RECORDS, self.RECORDS)
        self.assertTrue(ok, why)

    def test_the_login_page_is_refused(self):
        ok, why = landed_where_expected(
            'https://www.example.invalid/login', self.RECORDS)
        self.assertFalse(ok)
        self.assertIn('sign-in page', why)

    def test_another_host_is_refused(self):
        ok, why = landed_where_expected(
            'https://elsewhere.invalid/records/list', self.RECORDS)
        self.assertFalse(ok)
        self.assertIn('elsewhere.invalid', why)

    def test_no_url_is_refused(self):
        ok, why = landed_where_expected('', self.RECORDS)
        self.assertFalse(ok)
        self.assertIn('no URL', why)

    def test_the_login_url_is_on_the_records_host(self):
        self.assertEqual(login_url(self.RECORDS),
                         'https://www.example.invalid/login')


if __name__ == '__main__':
    unittest.main(verbosity=2)
