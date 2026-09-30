# -*- coding: utf-8 -*-
"""Поддельный сервер API agro-work для тестов трека agro-work.

Настоящий HTTP на 127.0.0.1, а не подмена функций: проверяется то, что
действительно уходит по сети -- метод, адрес, заголовки, тело. Сервер
записывает каждый полученный запрос, и тест может доказать, что запроса НЕ
было.

Данные -- синтетические. Персональные поля (имена, телефоны) заполнены
маркерами PII-..., чтобы тест мог искать их в базе поиском подстроки.
"""

import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PREFIX = '/api/v1'

LOGIN = 'owner-login'
PASSWORD = 'PII-secret-password-7731'
ACCESS = 'access-token-AAAA1111'
ACCESS_2 = 'access-token-BBBB2222'
REFRESH = 'refresh-token-RRRR3333'

PII_MARKERS = ('PII-farmer-Nurxon', 'PII-+998901234567', 'PII-driver-Salimov',
               'PII-operator-Karimova', 'PII-editor-Tosheva',
               'PII-driver-phone-+998907654321', 'PII-farm-name-Bobokhon',
               'PII-comment-call-Anvar')


def uuid_for(prefix, number):
    return '%08x-0000-4000-8000-%012x' % (prefix, number)


def application(number, status='COMPLETED', created='2026-09-10T08:15:00+05:00',
                updated='2026-09-11T17:40:00+05:00', transport=1, work_type=1,
                volume='5.00', **extra):
    row = {
        'id': uuid_for(0xA, number),
        'application_number': 'APP-BKHRA-2026-%07d' % number,
        'company': uuid_for(0xC, 1), 'company_key': 'BKHRA',
        'company_name': 'Buxoro agroklaster',
        'transport': uuid_for(0xB, transport),
        'transport_info': {'plate_number': '80 %03d EA' % transport,
                           'brand_name': 'MTZ', 'model': '80.1',
                           'category_name': 'Чопиқ тракторлар',
                           'driver_name': 'PII-driver-Salimov'},
        'farm': uuid_for(0xF, 1),
        'farm_info': {'name': 'PII-farm-name-Bobokhon',
                      'owner_name': 'PII-farmer-Nurxon',
                      'owner_phone': 'PII-+998901234567'},
        'work_type': uuid_for(0xD, work_type),
        'work_type_info': {'name': 'Култивация', 'unit': 'HECTARE',
                           'unit_display': 'Гектар', 'price': '150000.00'},
        'volume': volume, 'unit_price_snapshot': '150000.00',
        'total_amount': '750000.00', 'payment_type': 'TRANSFER',
        'with_fuel': True, 'status': status,
        'status_display': 'Бажарилди', 'start_time': None, 'end_time': None,
        'created_at': created, 'updated_at': updated,
        'created_by': uuid_for(0xE, 1), 'created_by_name': 'PII-operator-Karimova',
        'updated_by': uuid_for(0xE, 2), 'updated_by_name': 'PII-editor-Tosheva',
        'comment': 'PII-comment-call-Anvar', 'is_active': True,
        'start_reminder_sent': False, 'end_reminder_sent': False,
    }
    row.update(extra)
    return row


def transport(number, plate=None, **extra):
    row = {'id': uuid_for(0xB, number),
           'plate_number': plate or '80 %03d EA' % number,
           'brand': 1, 'brand_name': 'MTZ', 'model': '80.1', 'category': 2,
           'category_name': 'Чопиқ тракторлар', 'company': uuid_for(0xC, 1),
           'company_name': 'Buxoro agroklaster',
           'driver_name': 'PII-driver-Salimov',
           'driver_phone': 'PII-driver-phone-+998907654321',
           'year': 2019, 'vin_code': None, 'status': 'AVAILABLE',
           'comment': None, 'created_at': '2026-06-01T09:00:00+05:00',
           'updated_at': '2026-06-01T09:00:00+05:00'}
    row.update(extra)
    return row


def work_type(number, name='Култивация', unit='HECTARE', **extra):
    row = {'id': uuid_for(0xD, number), 'name': name, 'unit': unit,
           'unit_display': 'Гектар', 'price': '150000.00'}
    row.update(extra)
    return row


def history(created_status='IN_PROGRESS', created='2026-09-10T08:15:00+05:00',
            completed='2026-09-11T17:40:00+05:00', cancelled=None):
    events = [{'id': 1, 'action': 'created', 'old_status': None,
               'new_status': created_status, 'changed_at': created,
               'changed_by': 'x', 'changed_by_name': 'PII-operator-Karimova',
               'changes': {}, 'note': 'PII-comment-call-Anvar'}]
    if completed and created_status != 'COMPLETED':
        events.append({'id': 2, 'action': 'status_changed',
                       'old_status': created_status, 'new_status': 'COMPLETED',
                       'changed_at': completed,
                       'changed_by_name': 'PII-editor-Tosheva',
                       'changes': {'status': {'old': created_status,
                                              'new': 'COMPLETED',
                                              'label': 'Ҳолат'},
                                   'farm': {'old': 'PII-farmer-Nurxon',
                                            'new': 'PII-farm-name-Bobokhon'}},
                       'note': None})
    if cancelled:
        events.append({'id': 3, 'action': 'status_changed',
                       'old_status': created_status, 'new_status': 'CANCELLED',
                       'changed_at': cancelled, 'changes': [],
                       'note': None})
    return events


class FakeAgroWork:
    """Поднимает сервер, держит данные, пишет журнал запросов."""

    def __init__(self):
        self.applications = []
        self.transports = []
        self.work_types = []
        self.histories = {}
        self.requests = []
        self.access_valid = {ACCESS}
        self.expire_after = None      # число успешных GET, после которого 401
        self.fail_next = []           # коды ответа для ближайших запросов
        self.redirect_paths = set()
        self.login_field = 'username'
        self.page_size_cap = 100
        self.issue_refresh = True
        self._gets = 0
        self._lock = threading.Lock()
        handler = self._handler()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={'poll_interval': 0.05},
                                       daemon=True)
        self.thread.start()

    @property
    def base_url(self):
        return 'http://127.0.0.1:%d%s' % (self.server.server_address[1], PREFIX)

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def paths(self, method=None):
        return [r['path'] for r in self.requests
                if method is None or r['method'] == method]

    def _handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _record(self):
                length = int(self.headers.get('Content-Length') or 0)
                body = self.rfile.read(length) if length else b''
                parsed = urllib.parse.urlparse(self.path)
                entry = {'method': self.command, 'path': parsed.path,
                         'query': dict(urllib.parse.parse_qsl(parsed.query)),
                         'headers': dict(self.headers.items()), 'body': body}
                with fake._lock:
                    fake.requests.append(entry)
                return entry

            def _send(self, status, payload=None, headers=None):
                data = json.dumps(payload, ensure_ascii=False).encode('utf-8') \
                    if payload is not None else b''
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(data)

            def _any(self):
                entry = self._record()
                if fake.fail_next:
                    status = fake.fail_next.pop(0)
                    extra = {'Retry-After': '0'} if status == 429 else {}
                    return self._send(status, {'detail': 'busy'}, extra)
                path = entry['path']
                if not path.startswith(PREFIX):
                    return self._send(404, {'detail': 'Not found.'})
                path = path[len(PREFIX):]
                if path in fake.redirect_paths:
                    return self._send(302, None,
                                      {'Location': 'http://evil.example/steal'})
                if self.command == 'POST' and path == '/auth/token/':
                    body = json.loads(entry['body'] or b'{}')
                    if fake.login_field not in body:
                        return self._send(400, {fake.login_field:
                                                ['This field is required.']})
                    if body.get(fake.login_field) != LOGIN \
                            or body.get('password') != PASSWORD:
                        return self._send(401, {'detail': 'No active account '
                                                'found with the given '
                                                'credentials'})
                    # Новый вход -- новый действующий токен доступа.
                    fake.access_valid.add(ACCESS)
                    answer = {'access': ACCESS}
                    if fake.issue_refresh:
                        answer['refresh'] = REFRESH
                    return self._send(200, answer)
                if self.command == 'POST' and path == '/auth/token/refresh/':
                    body = json.loads(entry['body'] or b'{}')
                    if body.get('refresh') != REFRESH:
                        return self._send(401, {'detail': 'Token is invalid'})
                    fake.access_valid.add(ACCESS_2)
                    return self._send(200, {'access': ACCESS_2})
                if self.command != 'GET':
                    return self._send(405, {'detail': 'Method not allowed'})
                auth = entry['headers'].get('Authorization', '')
                token = auth.split(' ', 1)[1] if ' ' in auth else ''
                if not auth.startswith('Bearer ') or token not in fake.access_valid:
                    return self._send(401, {'detail': 'Given token not valid'})
                if fake.expire_after is not None and fake._gets >= fake.expire_after:
                    fake.expire_after = None
                    fake.access_valid.discard(token)
                    return self._send(401, {'detail': 'Token expired'})
                fake._gets += 1
                return self._get(path, entry['query'])

            def _get(self, path, query):
                if path == '/applications/':
                    rows = list(fake.applications)
                    if query.get('ordering') == 'created_at':
                        rows.sort(key=lambda r: r['created_at'])
                    else:
                        rows.sort(key=lambda r: r['created_at'], reverse=True)
                    return self._page(rows, query)
                if path == '/transports/':
                    return self._page(fake.transports, query)
                if path == '/work-types/':
                    return self._page(fake.work_types, query)
                if path.startswith('/applications/') and path.endswith('/history/'):
                    app_id = path.split('/')[2]
                    if app_id not in fake.histories:
                        return self._send(404, {'detail': 'Not found.'})
                    return self._send(200, fake.histories[app_id])
                return self._send(404, {'detail': 'Not found.'})

            def _page(self, rows, query):
                size = min(int(query.get('page_size') or 20), fake.page_size_cap)
                page = int(query.get('page') or 1)
                start = (page - 1) * size
                chunk = rows[start:start + size]
                total_pages = max(1, -(-len(rows) // size))
                nxt = None
                if page < total_pages:
                    nxt = 'http://evil.example/api/v1/?page=%d' % (page + 1)
                return self._send(200, {'count': len(rows),
                                        'current_page': page,
                                        'total_pages': total_pages,
                                        'next': nxt, 'previous': None,
                                        'results': chunk})

            do_GET = _any
            do_POST = _any
            do_PUT = _any
            do_PATCH = _any
            do_DELETE = _any

        return Handler
