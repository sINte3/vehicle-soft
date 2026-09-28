# -*- coding: utf-8 -*-
"""agro-work B1: клиент API умеет только читать, и это проверено по сети.

Владелец 28.09: импорт идёт под его админской учётной записью, поэтому
клиент технически умеет только GET плюс вход и обновление токена, а любой
другой запрос -- отказ. Здесь это доказывается на настоящем HTTP-сервере:
после попытки записи в журнале сервера нет НИ ОДНОГО запроса.

Плюс то, что стоит дёшево сломать и дорого заметить: токен не уходит по
редиректу и по ссылке `next`, секреты не попадают ни в журнал, ни в текст
исключения, темп держится, 401 обновляет токен ровно один раз.

Запуск: python -m unittest tests.test_agro_work_client -v
"""

import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from agro_work import config                                    # noqa: E402
from agro_work.client import (ApiError, AuthFailed, Client,     # noqa: E402
                              RefusedRequest, allowed, build_opener)
from tests import agro_work_fake as fake_api                    # noqa: E402

APP_ID = fake_api.uuid_for(0xA, 1)


class FakeClock:
    """Время, которое идёт только когда клиент спит."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def make_client(server, pause=0.0, log=None, clock=None, **credentials):
    creds = {'login': fake_api.LOGIN, 'password': fake_api.PASSWORD}
    creds.update(credentials)
    kwargs = {}
    if clock is not None:
        kwargs.update(sleep=clock.sleep, clock=clock.clock)
    return Client(creds, base_url=server.base_url, pause=pause,
                  log=log or (lambda text: None),
                  opener=build_opener(proxies={}), **kwargs)


class AllowedSurface(unittest.TestCase):
    """Таблица истинности: что клиент вообще согласен отправить."""

    def test_reads_that_the_importer_needs_are_allowed(self):
        for path in ('/applications/', '/applications/%s/history/' % APP_ID,
                     '/transports/', '/work-types/'):
            self.assertTrue(allowed('GET', path), path)
        for path in ('/auth/token/', '/auth/token/refresh/'):
            self.assertTrue(allowed('POST', path), path)

    def test_everything_else_is_refused(self):
        refused = [
            ('POST', '/applications/'),
            ('POST', '/applications/%s/change-status/' % APP_ID),
            ('PUT', '/applications/%s/' % APP_ID),
            ('PATCH', '/applications/%s/' % APP_ID),
            ('DELETE', '/applications/%s/' % APP_ID),
            ('PUT', '/applications/'), ('PATCH', '/transports/'),
            ('DELETE', '/work-types/'),
            ('POST', '/auth/logout/'), ('POST', '/auth/change-password/'),
            ('POST', '/users/%s/activate/' % APP_ID),
            ('GET', '/users/%s/reset-password/' % APP_ID),
            ('GET', '/applications/export/excel/'),
            ('GET', '/applications/export/pdf/'),
            ('GET', '/applications/%s/' % APP_ID),
            ('GET', '/users/'), ('GET', '/farms/'),
            ('GET', '/dashboard/overview/'),
            ('GET', '/auth/token/'),
            ('GET', '/applications/../users/'),
            ('GET', '/applications/not-a-uuid/history/'),
            ('HEAD', '/applications/'), ('OPTIONS', '/applications/'),
        ]
        for method, path in refused:
            self.assertFalse(allowed(method, path), '%s %s' % (method, path))


class RefusalNeverReachesTheNetwork(unittest.TestCase):
    def setUp(self):
        self.server = fake_api.FakeAgroWork()

    def tearDown(self):
        self.server.close()

    def test_a_write_is_refused_before_a_socket_is_opened(self):
        client = make_client(self.server)
        attempts = [
            ('POST', '/applications/%s/change-status/' % APP_ID,
             {'status': 'COMPLETED'}),
            ('PUT', '/applications/%s/' % APP_ID, {'volume': '1'}),
            ('PATCH', '/applications/%s/' % APP_ID, {'volume': '1'}),
            ('DELETE', '/applications/%s/' % APP_ID, None),
            ('POST', '/auth/logout/', {}),
            ('POST', '/applications/', {'transport': 'x'}),
            ('GET', '/users/%s/reset-password/' % APP_ID, None),
            ('GET', '/applications/export/excel/', None),
        ]
        for method, path, body in attempts:
            with self.assertRaises(RefusedRequest, msg='%s %s' % (method, path)):
                client.send(method, path, body=body)
        # Отрицательный контроль: сервер жив и отвечает на разрешённое.
        self.assertEqual(self.server.requests, [])
        client.get('/work-types/')
        self.assertEqual([(r['method'], r['path']) for r in self.server.requests],
                         [('POST', '/api/v1/auth/token/'),
                          ('GET', '/api/v1/work-types/')])

    def test_no_public_method_of_the_client_can_write(self):
        # Всё, что клиент делает сам, -- GET и две точки входа.
        self.server.applications = [fake_api.application(1)]
        self.server.histories[APP_ID] = fake_api.history()
        self.server.transports = [fake_api.transport(1)]
        self.server.work_types = [fake_api.work_type(1)]
        client = make_client(self.server)
        client.login()
        list(client.pages('/applications/', {'ordering': 'created_at'}))
        client.history(APP_ID)
        list(client.pages('/transports/'))
        list(client.pages('/work-types/'))
        client.refresh_access()
        methods = {(r['method'], r['path']) for r in self.server.requests}
        writes = {(m, p) for m, p in methods if m != 'GET'}
        self.assertEqual(writes, {('POST', '/api/v1/auth/token/'),
                                  ('POST', '/api/v1/auth/token/refresh/')})


class AuthFlow(unittest.TestCase):
    def setUp(self):
        self.server = fake_api.FakeAgroWork()
        self.server.work_types = [fake_api.work_type(1)]

    def tearDown(self):
        self.server.close()

    def test_login_is_json_and_reads_carry_the_bearer_token(self):
        import json
        client = make_client(self.server)
        client.get('/work-types/')
        login, read = self.server.requests
        self.assertEqual(login['headers'].get('Content-Type'), 'application/json')
        self.assertEqual(json.loads(login['body']),
                         {'username': fake_api.LOGIN,
                          'password': fake_api.PASSWORD})
        self.assertNotIn('Authorization', login['headers'])
        self.assertEqual(read['headers']['Authorization'],
                         'Bearer ' + fake_api.ACCESS)
        self.assertEqual(client.logins, 1)

    def test_login_field_is_configurable_and_a_wrong_one_names_the_right_one(self):
        self.server.login_field = 'phone'
        client = make_client(self.server)
        with self.assertRaises(AuthFailed) as caught:
            client.login()
        self.assertEqual(caught.exception.status, 400)
        self.assertIn('phone', str(caught.exception))
        client = make_client(self.server, login_field='phone')
        client.get('/work-types/')
        self.assertEqual(client.logins, 1)

    def test_expired_access_is_refreshed_once_and_the_read_repeated(self):
        client = make_client(self.server)
        client.get('/work-types/')
        self.server.expire_after = 1
        client.get('/work-types/')           # 1-й после входа -- ещё годен
        answer = client.get('/work-types/')  # 401 -> refresh -> повтор
        self.assertEqual(answer['count'], 1)
        self.assertEqual(client.refreshes, 1)
        self.assertEqual(client.logins, 1)
        last = self.server.requests[-1]
        self.assertEqual(last['headers']['Authorization'],
                         'Bearer ' + fake_api.ACCESS_2)

    def test_without_a_refresh_token_a_401_logs_in_again(self):
        self.server.issue_refresh = False
        client = make_client(self.server)
        client.get('/work-types/')
        self.server.expire_after = 1
        answer = client.get('/work-types/')   # 401 -> обновить нечем -> вход
        self.assertEqual(answer['count'], 1)
        self.assertEqual(client.logins, 2)
        self.assertEqual(client.refreshes, 0)
        self.assertNotIn('/api/v1/auth/token/refresh/', self.server.paths())

    def test_wrong_password_fails_without_repeating_the_password(self):
        lines = []
        client = make_client(self.server, log=lines.append,
                             password='PII-wrong-password-99')
        with self.assertRaises(AuthFailed) as caught:
            client.get('/work-types/')
        text = str(caught.exception) + ' '.join(lines) + repr(client)
        self.assertEqual(caught.exception.status, 401)
        self.assertIn('No active account', text)
        for secret in ('PII-wrong-password-99', fake_api.PASSWORD,
                       fake_api.ACCESS, fake_api.REFRESH, fake_api.LOGIN):
            self.assertNotIn(secret, text)


class TransportSafety(unittest.TestCase):
    def setUp(self):
        self.server = fake_api.FakeAgroWork()
        self.server.applications = [fake_api.application(n, created=(
            '2026-09-%02dT08:00:00+05:00' % (1 + n % 28))) for n in range(1, 251)]
        self.server.transports = [fake_api.transport(1)]

    def tearDown(self):
        self.server.close()

    def test_a_redirect_is_refused_and_the_token_goes_nowhere(self):
        self.server.redirect_paths = {'/transports/'}
        client = make_client(self.server)
        with self.assertRaises(ApiError) as caught:
            client.get('/transports/')
        self.assertEqual(caught.exception.status, 302)
        self.assertEqual(self.server.paths(), ['/api/v1/auth/token/',
                                               '/api/v1/transports/'])

    def test_pages_are_built_here_and_the_next_link_is_never_opened(self):
        # Сервер кладёт в `next` чужой хост; если бы клиент по нему пошёл,
        # второй страницы здесь бы не было.
        client = make_client(self.server)
        pages = list(client.pages('/applications/', {'ordering': 'created_at'}))
        self.assertEqual([page for page, _ in pages], [1, 2, 3])
        self.assertEqual(sum(len(answer['results']) for _, answer in pages), 250)
        queries = [r['query'] for r in self.server.requests if r['method'] == 'GET']
        self.assertEqual([q['page'] for q in queries], ['1', '2', '3'])
        self.assertTrue(all(q['page_size'] == '100' for q in queries))
        self.assertTrue(all(q['ordering'] == 'created_at' for q in queries))

    def test_the_pause_holds_between_every_two_requests(self):
        clock = FakeClock()
        client = make_client(self.server, pause=2.0, clock=clock)
        list(client.pages('/applications/'))
        client.get('/transports/')
        # вход + 3 страницы + реестр = 5 запросов, 4 паузы по 2 с.
        self.assertEqual(client.requests, 5)
        self.assertEqual(clock.sleeps, [2.0, 2.0, 2.0, 2.0])

    def test_busy_server_is_retried_and_a_refusal_is_not(self):
        clock = FakeClock()
        client = make_client(self.server, clock=clock)
        client.login()
        self.server.fail_next = [503, 429]
        answer = client.get('/transports/')
        self.assertEqual(answer['count'], 1)
        self.assertEqual(client.requests, 4)       # вход + 503 + 429 + ответ
        self.assertIn(float(config.RETRY_PAUSE_S[0]), clock.sleeps)
        self.assertIn(0.0, clock.sleeps)            # Retry-After: 0 уважен
        self.server.fail_next = [403]
        with self.assertRaises(ApiError) as caught:
            client.get('/transports/')
        self.assertEqual(caught.exception.status, 403)
        self.assertEqual(client.requests, 5)       # 403 не повторялся

    def test_three_failures_stop_the_request(self):
        client = make_client(self.server, clock=FakeClock())
        client.login()
        self.server.fail_next = [502, 502, 502]
        with self.assertRaises(ApiError) as caught:
            client.get('/transports/')
        self.assertEqual(caught.exception.status, 502)
        self.assertEqual(client.requests, 1 + config.RETRIES)


class Credentials(unittest.TestCase):
    def test_both_keys_are_read_and_defaults_filled(self):
        values = config.parse_credentials(
            '# agro-work\n\nlogin = owner\npassword=p=ss word\n')
        self.assertEqual(values['login'], 'owner')
        self.assertEqual(values['password'], 'p=ss word')
        self.assertEqual(values['login_field'], 'username')
        self.assertEqual(values['auth_scheme'], 'Bearer')

    def test_a_typo_is_refused_by_key_name_never_by_value(self):
        with self.assertRaises(config.CredentialsError) as caught:
            config.parse_credentials('login=owner\npasword=Secret-777\n')
        self.assertIn('pasword', str(caught.exception))
        self.assertNotIn('Secret-777', str(caught.exception))

    def test_missing_password_is_refused(self):
        with self.assertRaises(config.CredentialsError):
            config.parse_credentials('login=owner\npassword=\n')

    def test_notepad_encodings_are_both_read(self):
        folder = tempfile.mkdtemp()
        for name, data in (('bom.txt', '﻿login=owner\npassword=Пароль1\n'
                            .encode('utf-8')),
                           ('ansi.txt', 'login=owner\npassword=Пароль1\n'
                            .encode('cp1251'))):
            path = os.path.join(folder, name)
            with open(path, 'wb') as fh:
                fh.write(data)
            values, used = config.read_credentials(path)
            self.assertEqual(values['password'], 'Пароль1', name)
            self.assertEqual(used, path)

    def test_a_missing_file_names_where_it_looked(self):
        missing = os.path.join(tempfile.mkdtemp(), 'nope.txt')
        with self.assertRaises(config.CredentialsError) as caught:
            config.read_credentials(missing)
        self.assertIn('nope.txt', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
