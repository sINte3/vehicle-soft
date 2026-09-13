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
    DEFAULT_LOGIN_WAIT_S,
    MAX_SESSION_BYTES,
    SessionMissing,
    authorized_url,
    canonical_hosts,
    clean_host,
    context_carries_session,
    context_page_urls,
    host_problem,
    inspect_session,
    landed_where_expected,
    login_url,
    login_wait_seconds,
    require_session,
    sanitize_url,
    save_state_atomically,
    session_exists,
    wait_for_records_page,
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
    """Схема, host И путь -- все три, и все до сохранения.

    [REASON]: проверялся только host и «путь не начинается с /login».
    У кабинета есть и другие страницы, и каждая ставит свои cookie: `/mission`,
    корень, что угодно. «Наполненный контекст на какой-то странице нужного
    хоста» подтверждением входа никогда не было. Схема не проверялась вовсе,
    и `http://` на верном хосте проходил.
    """

    RECORDS = 'https://www.example.invalid/records/list'

    def test_the_records_page_is_accepted(self):
        ok, why = landed_where_expected(self.RECORDS, self.RECORDS)
        self.assertTrue(ok, why)

    def test_a_trailing_slash_is_accepted(self):
        ok, why = landed_where_expected(self.RECORDS + '/', self.RECORDS)
        self.assertTrue(ok, why)

    def test_a_query_string_is_accepted(self):
        """Страница вылетов носит фильтры в query по построению."""
        ok, why = landed_where_expected(
            self.RECORDS + '?beginTime=1&endTime=2', self.RECORDS)
        self.assertTrue(ok, why)

    def test_the_login_page_is_refused(self):
        ok, why = landed_where_expected(
            'https://www.example.invalid/login', self.RECORDS)
        self.assertFalse(ok)
        self.assertIn('/login', why)

    def test_the_mission_page_is_refused(self):
        """Другая страница того же кабинета -- тоже не страница вылетов."""
        ok, why = landed_where_expected(
            'https://www.example.invalid/mission', self.RECORDS)
        self.assertFalse(ok)
        self.assertIn('/mission', why)

    def test_the_site_root_is_refused(self):
        ok, why = landed_where_expected(
            'https://www.example.invalid/', self.RECORDS)
        self.assertFalse(ok)
        self.assertIn('records page', why)

    def test_plain_http_on_the_right_host_is_refused(self):
        ok, why = landed_where_expected(
            'http://www.example.invalid/records/list', self.RECORDS)
        self.assertFalse(ok)
        self.assertIn('http', why)

    def test_another_host_is_refused(self):
        ok, why = landed_where_expected(
            'https://elsewhere.invalid/records/list', self.RECORDS)
        self.assertFalse(ok)
        self.assertIn('elsewhere.invalid', why)

    def test_no_url_is_refused(self):
        ok, why = landed_where_expected('', self.RECORDS)
        self.assertFalse(ok)
        self.assertIn('no URL', why)

    def test_no_reason_ever_quotes_a_value(self):
        for url in ('https://www.example.invalid/mission',
                    'http://www.example.invalid/records/list',
                    'https://elsewhere.invalid/records/list'):
            _ok, why = landed_where_expected(url, self.RECORDS)
            self.assertNotIn(COOKIE_VALUE, why)
            self.assertNotIn(STORAGE_VALUE, why)

    def test_the_login_url_is_on_the_records_host(self):
        self.assertEqual(login_url(self.RECORDS),
                         'https://www.example.invalid/login')


# ─── DJI-SESSION-HOTFIX-001 ─────────────────────────────────────────────────

RECORDS = 'https://www.djiag.com/records/list'
LOGIN = 'https://www.djiag.com/login'
APEX = 'https://djiag.com/records/list'


class _Driver(object):
    """Модель СИНХРОННОГО API Playwright, включая устаревание кэша.

    [REASON]: это не удобная абстракция, а воспроизведение той семантики, из
    которой вырос дефект. `page.url` в sync API -- чтение локального кэша, и
    кэш двигается ТОЛЬКО когда диспетчер получает управление, то есть внутри
    прокачивающего вызова. Поэтому здесь `tick` растёт исключительно в
    `pump`, а чтение `.url` не двигает ничего. Фейк, у которого адрес
    менялся от каждого ЧТЕНИЯ, проходил бы и на реализации с `time.sleep` --
    то есть на неисправленном коде.
    """

    def __init__(self, step=0.5):
        self.now = 0.0
        self.tick = 0
        self.pumps = 0
        self.step = step
        self.raise_on_pump = False

    def clock(self):
        return self.now

    def pump(self, seconds):
        if self.raise_on_pump:
            raise RuntimeError('browser stopped responding')
        self.pumps += 1
        self.tick += 1
        self.now += seconds or self.step


class _FakePage(object):
    """Страница, чей адрес обновляется только при прокачке диспетчера."""

    def __init__(self, urls, driver, closed=False, raises=False):
        self._urls = list(urls)
        self._driver = driver
        self.closed = closed
        self.raises = raises

    def is_closed(self):
        return self.closed

    @property
    def url(self):
        if self.raises:
            raise RuntimeError('page is gone')
        if not self._urls:
            return ''
        return self._urls[min(self._driver.tick, len(self._urls) - 1)]


class _PagesContext(object):
    def __init__(self, pages, cookies=None, cookies_raise=False):
        self.pages = list(pages)
        self._cookies = list(cookies or [])
        self._cookies_raise = cookies_raise

    def cookies(self):
        if self._cookies_raise:
            raise RuntimeError('context is gone')
        return list(self._cookies)


class TestCanonicalHosts(unittest.TestCase):
    """Ровно два хоста, и это МНОЖЕСТВО, а не суффикс."""

    def test_www_config_accepts_the_apex_too(self):
        self.assertEqual(canonical_hosts(RECORDS),
                         frozenset(['www.djiag.com', 'djiag.com']))

    def test_apex_config_accepts_www_too(self):
        self.assertEqual(canonical_hosts(APEX),
                         frozenset(['www.djiag.com', 'djiag.com']))

    def test_a_staging_host_is_not_widened_to_anything_unrelated(self):
        hosts = canonical_hosts('https://staging.djiag.com/records/list')
        self.assertEqual(hosts, frozenset(['staging.djiag.com',
                                           'www.staging.djiag.com']))
        self.assertNotIn('djiag.com', hosts)

    def test_the_set_is_exactly_two_hosts(self):
        """Контроль на разрастание: правило не должно стать подстановочным."""
        self.assertEqual(len(canonical_hosts(RECORDS)), 2)

    def test_a_url_without_a_host_yields_nothing(self):
        self.assertEqual(canonical_hosts('not a url'), frozenset())

    def test_clean_host_strips_one_trailing_dot_only(self):
        self.assertEqual(clean_host('DJIAG.COM.'), 'djiag.com')
        # [REASON]: не rstrip('.') -- иначе 'djiag.com...' стало бы равно хосту.
        self.assertEqual(clean_host('djiag.com..'), 'djiag.com.')


class TestHostProblem(unittest.TestCase):
    """Форма хоста проверяется до сравнения, и на СЫРОМ netloc."""

    def test_a_unicode_host_is_refused_before_it_folds_to_ascii(self):
        """U+212A становится ASCII 'k' внутри .hostname -- измерено.

        [REASON]: поэтому ascii-проверка идёт по netloc. Если спросить
        hostname, она вернёт True для юникодного хоста и защита будет
        пустой -- ровно та ошибка порядка, которую легко не заметить.
        """
        from urllib.parse import urlsplit
        parts = urlsplit('https://www.dji\u212Ag.com/records/list')
        self.assertFalse(parts.netloc.isascii())
        self.assertTrue(parts.hostname.isascii(),
                        'если это False, складывания больше нет -- '
                        'перечитать обоснование проверки')
        self.assertIn('ASCII', host_problem(parts.netloc, parts.hostname))

    def test_user_info_is_refused_even_on_the_right_host(self):
        problem = host_problem('user:pass@www.djiag.com', 'www.djiag.com')
        self.assertIn('user-info', problem)

    def test_an_empty_label_is_refused(self):
        self.assertIn('well-formed', host_problem('www..djiag.com',
                                                  'www..djiag.com'))

    def test_percent_encoding_is_refused(self):
        self.assertIn('well-formed', host_problem('djiag%2ecom',
                                                  'djiag%2ecom'))

    def test_a_plain_host_has_no_problem(self):
        """Отрицательный контроль: строгость не отвергает нормальный хост."""
        self.assertEqual(host_problem('www.djiag.com', 'www.djiag.com'), '')
        self.assertEqual(host_problem('djiag.com', 'djiag.com'), '')


class TestLandedHostAndPort(unittest.TestCase):
    """Сценарии задания по хосту и порту."""

    def test_www_to_apex_is_accepted(self):
        ok, why = landed_where_expected(APEX, RECORDS)
        self.assertTrue(ok, why)

    def test_apex_to_www_is_accepted(self):
        ok, why = landed_where_expected(RECORDS, APEX)
        self.assertTrue(ok, why)

    def test_uppercase_and_a_trailing_dot_are_accepted(self):
        ok, why = landed_where_expected(
            'https://DJIAG.COM./records/list', RECORDS)
        self.assertTrue(ok, why)

    def test_a_wrong_host_is_refused(self):
        for host in ('evil-djiag.com', 'login.djiag.com',
                     'evil.www.djiag.com', 'djiag.com.evil.example'):
            ok, why = landed_where_expected(
                'https://%s/records/list' % host, RECORDS)
            self.assertFalse(ok, host)
            self.assertIn(host, why)

    def test_the_right_host_on_the_wrong_path_is_refused(self):
        for path in ('/login', '/mission', '/', '/records'):
            ok, why = landed_where_expected(
                'https://djiag.com%s' % path, RECORDS)
            self.assertFalse(ok, path)
            self.assertIn('records page', why)

    def test_port_zero_does_not_pass_as_the_https_default(self):
        """`port or 443` подменял ноль на 443 -- измерено на urlsplit."""
        ok, why = landed_where_expected('https://djiag.com:0/records/list',
                                        RECORDS)
        self.assertFalse(ok)
        self.assertIn('port 0', why)

    def test_another_port_is_refused(self):
        ok, why = landed_where_expected('https://djiag.com:8443/records/list',
                                        RECORDS)
        self.assertFalse(ok)
        self.assertIn('8443', why)

    def test_an_explicit_https_port_in_the_config_still_matches(self):
        """Отрицательный контроль: конфиг с портом не должен ломаться."""
        ok, why = landed_where_expected('https://djiag.com:443/records/list',
                                        RECORDS)
        self.assertTrue(ok, why)


class TestSanitizeUrl(unittest.TestCase):
    """В диагностику уходит scheme://host/path и ничего больше."""

    def test_the_query_is_dropped_because_it_carries_the_sso_code(self):
        line = sanitize_url('https://djiag.com/records/list'
                            '?code=SYNTHETIC-SSO-CODE&state=x')
        self.assertEqual(line, 'https://djiag.com/records/list')
        self.assertNotIn('SYNTHETIC-SSO-CODE', line)

    def test_the_fragment_is_dropped_too(self):
        self.assertEqual(sanitize_url('https://djiag.com/records/list#t=SECRET'),
                         'https://djiag.com/records/list')

    def test_user_info_never_reaches_the_line(self):
        line = sanitize_url('https://user:SYNTHETIC-PASS@djiag.com/records/list')
        self.assertNotIn('SYNTHETIC-PASS', line)
        self.assertNotIn('user', line)

    def test_a_nonstandard_port_is_shown_because_it_is_the_diagnosis(self):
        self.assertEqual(sanitize_url('https://djiag.com:8443/records/list'),
                         'https://djiag.com:8443/records/list')

    def test_nothing_in_becomes_a_readable_marker(self):
        self.assertEqual(sanitize_url(''), '(no URL)')


class TestAuthorizedUrl(unittest.TestCase):
    """Перебираются ВСЕ страницы, а не первая."""

    def test_the_records_page_is_found_behind_the_login_page(self):
        self.assertEqual(authorized_url([LOGIN, APEX], RECORDS), APEX)

    def test_nothing_matches_when_every_page_is_wrong(self):
        self.assertIsNone(authorized_url([LOGIN, 'https://evil.example/'],
                                         RECORDS))

    def test_an_empty_list_matches_nothing(self):
        self.assertIsNone(authorized_url([], RECORDS))


class TestWaitForRecordsPage(unittest.TestCase):
    """Ожидание само доводит до подтверждённого состояния.

    Во всех тестах адрес страницы двигается ТОЛЬКО при прокачке (см.
    `_Driver`). Значит реализация, которая спит без прокачки диспетчера,
    здесь не пройдёт -- а именно такой была первая редакция этой правки.
    """

    def wait(self, page_lists, timeout_s=5.0, driver=None):
        driver = driver or _Driver()
        pages = [_FakePage(u, driver) for u in page_lists]
        outcome = wait_for_records_page(
            lambda: [p.url for p in pages if not p.is_closed()],
            RECORDS, driver.pump, timeout_s=timeout_s, poll_s=0.5,
            clock=driver.clock)
        return outcome, driver

    def test_the_same_page_navigating_from_login_to_records_is_caught(self):
        """Сценарий 1 задания: /login -> /records/list в той же page."""
        outcome, driver = self.wait([[LOGIN, LOGIN, RECORDS]])
        self.assertTrue(outcome.ok, outcome.reason)
        self.assertEqual(outcome.url, RECORDS)
        self.assertGreater(outcome.polls, 1, 'ожидание не дождалось, а угадало')
        self.assertGreater(driver.pumps, 1)

    def test_the_cache_is_pumped_before_the_very_first_read(self):
        """Кэш устарел ещё до входа в петлю -- первое чтение обязано быть после прокачки.

        [REASON]: оператор входит в браузер МИНУТАМИ. К моменту, когда поток
        начинает ждать, снимок адреса относится к `page.goto(/login)`. Если
        прокачать только между опросами, первый опрос всё равно прочтёт
        `/login`; здесь страница уже на второй позиции списка и находится
        ровно потому, что прокачка идёт первой.
        """
        outcome, driver = self.wait([[LOGIN, RECORDS]])
        self.assertTrue(outcome.ok, outcome.reason)
        self.assertEqual(outcome.polls, 1, 'нашлось не с первого опроса')
        self.assertEqual(driver.pumps, 1)

    def test_a_new_page_with_the_records_page_is_caught(self):
        """Сценарий 2: вход открыл НОВУЮ вкладку, исходная осталась на /login.

        Это и есть живой дефект 13.09.2026: прежняя проверка читала только
        исходную page и сообщала про /login.
        """
        driver = _Driver()
        first = _FakePage([LOGIN], driver)
        second = _FakePage([RECORDS], driver)
        pages = [first]

        def list_urls():
            # Вторая вкладка появляется после первой прокачки.
            if driver.pumps >= 2 and second not in pages:
                pages.append(second)
            return [p.url for p in pages if not p.is_closed()]

        outcome = wait_for_records_page(list_urls, RECORDS, driver.pump,
                                        timeout_s=5.0, poll_s=0.5,
                                        clock=driver.clock)
        self.assertTrue(outcome.ok, outcome.reason)
        self.assertEqual(outcome.url, RECORDS)
        self.assertEqual(outcome.pages_seen, 2)

    def test_the_apex_redirect_is_caught(self):
        """Сценарий 3: www -> apex."""
        outcome, _driver = self.wait([[LOGIN, APEX]])
        self.assertTrue(outcome.ok, outcome.reason)
        self.assertEqual(outcome.url, APEX)

    def test_a_timeout_without_a_login_is_refused(self):
        """Сценарий 7: оператор так и не вошёл."""
        outcome, _driver = self.wait([[LOGIN]], timeout_s=2.0)
        self.assertFalse(outcome.ok)
        self.assertIn('within', outcome.reason)
        self.assertGreater(outcome.polls, 1)

    def test_a_closed_window_gives_up_early_instead_of_waiting_out(self):
        driver = _Driver()
        outcome = wait_for_records_page(lambda: [], RECORDS, driver.pump,
                                        timeout_s=600.0, poll_s=0.5,
                                        clock=driver.clock)
        self.assertFalse(outcome.ok)
        self.assertIn('window was closed', outcome.reason)
        self.assertEqual(outcome.polls, 1, 'ждал закрытое окно')

    def test_a_browser_that_cannot_be_asked_is_reported_not_hung(self):
        def boom():
            raise RuntimeError('browser is gone')
        driver = _Driver()
        outcome = wait_for_records_page(boom, RECORDS, driver.pump,
                                        timeout_s=600.0, poll_s=0.5,
                                        clock=driver.clock)
        self.assertFalse(outcome.ok)
        self.assertIn('could not be asked', outcome.reason)
        self.assertEqual(outcome.polls, 1)

    def test_a_pump_that_raises_ends_the_wait_instead_of_hanging(self):
        driver = _Driver()
        driver.raise_on_pump = True
        outcome, _driver = self.wait([[LOGIN]], timeout_s=600.0,
                                     driver=driver)
        self.assertFalse(outcome.ok)
        self.assertIn('stopped responding', outcome.reason)

    def test_the_diagnostic_carries_counts_and_sanitized_addresses_only(self):
        outcome, _driver = self.wait(
            [['https://www.djiag.com/login?code=SYNTHETIC-SSO-CODE']],
            timeout_s=1.0)
        line = outcome.describe()
        self.assertIn('pages=1', line)
        self.assertIn('polls=', line)
        self.assertIn('https://www.djiag.com/login', line)
        self.assertNotIn('SYNTHETIC-SSO-CODE', line)

    def test_a_wrong_host_never_satisfies_the_wait(self):
        """Сценарий 5 внутри ожидания: чужой хост не завершает ожидание."""
        outcome, _driver = self.wait([['https://evil-djiag.com/records/list']],
                                     timeout_s=2.0)
        self.assertFalse(outcome.ok)

    def test_the_right_host_on_the_wrong_path_never_satisfies_the_wait(self):
        """Сценарий 6 внутри ожидания."""
        outcome, _driver = self.wait([['https://djiag.com/mission']],
                                     timeout_s=2.0)
        self.assertFalse(outcome.ok)

    def test_the_pump_is_not_optional(self):
        """Забыть прокачку нельзя: у параметра нет значения по умолчанию.

        [REASON]: значение по умолчанию было бы ловушкой. `time.sleep` в
        роли паузы выглядит правильно и не работает, а тест с подставной
        паузой прошёл бы и на такой реализации.
        """
        with self.assertRaises(TypeError):
            wait_for_records_page(lambda: [RECORDS], RECORDS)


class TestContextPlumbing(unittest.TestCase):
    """Чтение страниц и cookie у контекста -- защищённое."""

    def test_closed_pages_are_skipped_not_fatal(self):
        driver = _Driver()
        context = _PagesContext([_FakePage([LOGIN], driver, closed=True),
                                _FakePage([RECORDS], driver)])
        self.assertEqual(context_page_urls(context), [RECORDS])

    def test_a_page_that_raises_on_url_is_skipped(self):
        driver = _Driver()
        context = _PagesContext([_FakePage([], driver, raises=True),
                                _FakePage([RECORDS], driver)])
        self.assertEqual(context_page_urls(context), [RECORDS])

    def test_a_context_without_pages_yields_nothing(self):
        self.assertEqual(context_page_urls(_PagesContext([])), [])

    def test_cookies_are_counted_never_returned(self):
        usable, note = context_carries_session(
            _PagesContext([], cookies=[{'name': 'sid',
                                       'value': COOKIE_VALUE}]))
        self.assertTrue(usable)
        self.assertIn('cookies=1', note)
        self.assertNotIn(COOKIE_VALUE, note)

    def test_a_context_without_a_cookie_is_refused(self):
        usable, note = context_carries_session(_PagesContext([], cookies=[]))
        self.assertFalse(usable)
        self.assertIn('no cookie', note)

    def test_a_failing_cookie_query_defers_instead_of_burning_the_login(self):
        """Разовый сбой опроса не должен стоить оператору ручного входа.

        [REASON]: проверка совещательная, итоговый гейт -- файловый и
        безопасный по построению. Первая редакция отказывала здесь, то есть
        сжигала только что сделанный вход ради проверки, которая ничего не
        гарантирует сверх файловой.
        """
        usable, note = context_carries_session(
            _PagesContext([], cookies_raise=True))
        self.assertTrue(usable)
        self.assertIn('deferring to the file gate', note)


class TestLoginWaitSeconds(unittest.TestCase):
    """Таймаут настраивается, мусор не роняет команду."""

    def setUp(self):
        self._saved = os.environ.get('DJI_LOGIN_WAIT_S')

    def tearDown(self):
        if self._saved is None:
            os.environ.pop('DJI_LOGIN_WAIT_S', None)
        else:
            os.environ['DJI_LOGIN_WAIT_S'] = self._saved

    def test_the_default_is_minutes_not_seconds(self):
        os.environ.pop('DJI_LOGIN_WAIT_S', None)
        self.assertEqual(login_wait_seconds(), DEFAULT_LOGIN_WAIT_S)
        self.assertGreaterEqual(DEFAULT_LOGIN_WAIT_S, 300)

    def test_the_environment_overrides_it(self):
        os.environ['DJI_LOGIN_WAIT_S'] = '12.5'
        self.assertEqual(login_wait_seconds(), 12.5)

    def test_junk_falls_back_instead_of_timing_out_at_once(self):
        for junk in ('abc', '-5', '0', ''):
            os.environ['DJI_LOGIN_WAIT_S'] = junk
            self.assertEqual(login_wait_seconds(), DEFAULT_LOGIN_WAIT_S, junk)


if __name__ == '__main__':
    unittest.main(verbosity=2)
