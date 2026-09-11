# -*- coding: utf-8 -*-
"""DRONE-AREA-CAPTURE-001: часть приёмочного набора БЕЗ Flask.

Набор макроэтапа один, но разнесён по двум файлам по СРЕДЕ ИСПОЛНЕНИЯ, а не
по смыслу.

[REASON]: `.github/workflows/checks.yml` намеренно ставит только jinja2 и
openpyxl и не ставит зависимости приложения -- решение общее для четырёх
треков, и этот инкремент его не меняет. Значит всё, что импортирует Flask,
в CI не выполняется вовсе. Здесь лежит то, что CI выполнить МОЖЕТ: пропуск
известной геометрии, клиент манифеста, выбор записи списка и очередь.
Приёмная половина -- `tests/test_dji_area_capture_001.py`, она гоняется
полным прогоном локально и на сервере.

Проверяется:
* известный `contentMd5` пропускается ДО скачивания (по тому, что ссылку
  никто не запросил), и бенчмарк брифа 6171 известных + 13 новых -> 13;
* «не знаю» (None) и «ничего нет» (пустое множество) -- разные ответы;
* запись списка выбирается по id, а не по позиции, в обеих формах тела;
* источник списка идёт через ОЧЕРЕДЬ: атомарно, с дедупом и гейтом секретов.
"""
import base64
import hashlib
import json
import unittest

from drone_collector import sources as src
from drone_collector import sender as snd
from drone_collector.geometry import node_content_md5s
from drone_collector.tests.test_sources import (V4_QUERY, STORAGE_HOST,
                                                land_node, polygon_bytes)

from dji_area import evidence as ev

TOKEN = 'SYNTHETIC-capture-token-NOT-REAL'
START = 1755000000


class _Log(object):
    """Тихий логгер, который ЗАПОМИНАЕТ строки: часть про секреты их читает."""

    def __init__(self):
        self.lines = []

    def _add(self, fmt, *a):
        try:
            self.lines.append(fmt % a if a else str(fmt))
        except Exception:                       # noqa: BLE001
            self.lines.append(str(fmt))

    info = warning = error = debug = _add

    @property
    def text(self):
        return '\n'.join(self.lines)


def md5_of(body):
    return hashlib.md5(body).hexdigest()


def list_page(flights, page=1, total_pages=1):
    """Ответ DJI /api/web/v1/flight_records как БАЙТЫ."""
    doc = {'code': 0, 'message': 'OK',
           'meta_data': {'current_page': page, 'total_pages': total_pages,
                         'total_count': len(flights)},
           'data': list(flights)}
    return json.dumps(doc, ensure_ascii=False).encode('utf-8')


def list_row(flight_id, area=12345.0, width=6.0, start=START, mode=4):
    return {'id': flight_id, 'new_work_area': area, 'spray_width': width,
            'start_timestamp': start, 'end_timestamp': start + 300,
            'mode_name': mode, 'manual_mode': False,
            'nickname': 'SYNTHETIC-NICK', 'serial_number': 'R-NOT-REAL'}


class _Page(object):
    __slots__ = ('url', 'body', 'raw')

    def __init__(self, raw, url=None):
        self.raw = raw
        self.url = url or ('https://example.invalid/api/web/v1/'
                           'flight_records?page=1&size=50')
        self.body = json.loads(raw.decode('utf-8')) if raw else {}


class KnownGeometryIsSkippedBeforeTheDownload(unittest.TestCase):
    """Суть макроэтапа: 6171 полигон не качается заново каждый день."""

    def setUp(self):
        self.log = _Log()
        self.asked = []

    def downloader(self, table):
        def download(link):
            self.asked.append(link)
            answer = table.get(link)
            if isinstance(answer, Exception):
                raise answer
            return answer
        return download

    def node(self, uuid):
        body = polygon_bytes(uuid)
        link = ('%s/objects/lands/%s.geojson?%s'
                % (STORAGE_HOST, uuid, V4_QUERY))
        return land_node(uuid, md5_of(body), link=link), link, body

    def run_download(self, nodes, table, **kw):
        return src.download_snapshot_geometries(
            nodes, self.downloader(table), logger=self.log,
            sleep_fn=lambda _s: None, **kw)

    def test_a_known_md5_is_never_even_requested(self):
        node, link, body = self.node('u-known')
        md5 = md5_of(body)
        geometries, counters = self.run_download([node], {link: body},
                                                 known_md5={md5})
        # [REASON]: проверяется НЕ то, что тело не попало в результат, а то,
        # что ссылку никто не дёрнул. Иначе «инкрементальность» означала бы
        # те же семьдесят минут и выброшенные байты.
        self.assertEqual(self.asked, [])
        self.assertEqual(geometries, {})
        self.assertEqual(counters.known_skipped, 1)
        self.assertEqual(counters.downloaded, 0)

    def test_control_the_same_node_without_the_manifest_is_downloaded(self):
        # Без этого контроля тест выше прошёл бы и у кода, который просто
        # ничего не качает.
        node, link, body = self.node('u-known')
        geometries, counters = self.run_download([node], {link: body})
        self.assertEqual(self.asked, [link])
        self.assertEqual(counters.downloaded, 1)
        self.assertEqual(counters.known_skipped, 0)

    def test_the_benchmark_of_the_brief_holds(self):
        """6171 известных + 13 новых -> 13 загрузок, а не 6171."""
        nodes, table, known = [], {}, set()
        for i in range(6171):
            node, link, body = self.node('old-%d' % i)
            nodes.append(node)
            table[link] = body
            known.add(md5_of(body))
        for i in range(13):
            node, link, body = self.node('new-%d' % i)
            nodes.append(node)
            table[link] = body
        geometries, counters = self.run_download(nodes, table,
                                                 known_md5=known)
        self.assertEqual(len(self.asked), 13)
        self.assertEqual(counters.downloaded, 13)
        self.assertEqual(counters.known_skipped, 6171)
        self.assertEqual(len(geometries), 13)

    def test_an_unavailable_manifest_downloads_everything(self):
        # known_md5=None -- «выяснить не вышло». Медленно, но полно.
        nodes, table = [], {}
        for i in range(5):
            node, link, body = self.node('u-%d' % i)
            nodes.append(node)
            table[link] = body
        _g, counters = self.run_download(nodes, table, known_md5=None)
        self.assertEqual(len(self.asked), 5)
        self.assertEqual(counters.known_skipped, 0)

    def test_a_skipped_contour_still_gives_up_its_link_object(self):
        """Пропуск не оставляет живую ссылку в объекте контура.

        [REASON]: первая редакция этого теста проверяла УЗЕЛ и падала --
        `take_link()` очищает объект контура, а словарь узла чистит
        `strip_signed_urls` перед отправкой. Обе защиты нужны и обе
        проверяются: здесь -- первая, ниже -- вторая.
        """
        from drone_collector.geometry import contour_from_node
        node, _link, body = self.node('u-skip')
        source = contour_from_node(node)
        self.assertTrue(source.has_link)
        self.run_download([node], {}, known_md5={md5_of(body)})
        # Узел уходит в чанк только через strip_signed_urls -- вот она.
        stripped = src.strip_signed_urls(node)
        self.assertNotIn('signedURL', stripped['geometry']['storage'])
        self.assertNotIn('NOT-REAL-SIGNATURE',
                         json.dumps(stripped, ensure_ascii=False))

    def test_a_repeat_inside_one_run_is_counted_apart_from_a_known_one(self):
        # Два узла одной версии в одном обходе -- это не «уже в хранилище».
        node_a, link, body = self.node('u-same')
        node_b = land_node('u-other', md5_of(body), link=link)
        _g, counters = self.run_download([node_a, node_b], {link: body})
        self.assertEqual(counters.downloaded, 1)
        self.assertEqual(counters.duplicate_in_run, 1)
        self.assertEqual(counters.known_skipped, 0)

    def test_progress_is_rare_and_names_the_numbers(self):
        nodes, table, known = [], {}, set()
        for i in range(120):
            node, link, body = self.node('p-%d' % i)
            nodes.append(node)
            table[link] = body
            known.add(md5_of(body))
        self.run_download(nodes, table, known_md5=known, progress_every=50)
        progress = [l for l in self.log.lines if l.startswith('Geometry ')]
        # 120 контуров при шаге 50 -- две строки по ходу плюс итоговая.
        self.assertEqual(len(progress), 3, self.log.text)
        self.assertIn('known=', progress[-1])
        self.assertIn('new=', progress[-1])
        self.assertIn('failed=', progress[-1])

    def test_the_md5_comes_from_the_catalog_not_from_the_bytes(self):
        # Условие, без которого инкрементальность невозможна в принципе.
        node, _link, body = self.node('u-md5')
        self.assertEqual(node_content_md5s([node]), [md5_of(body)])


class TheManifestClientSeparatesUnknownFromEmpty(unittest.TestCase):
    """«Не знаю» и «ничего нет» -- разные ответы (часть 3)."""

    class Cfg(object):
        api_token = TOKEN
        land_geometry_manifest_url = ('http://localhost/drones/api/'
                                      'land_geometry_manifest')

    def setUp(self):
        self.log = _Log()
        self.cfg = self.Cfg()

    def call(self, post_fn, md5s=('a' * 32,), **kw):
        return snd.known_geometry_md5(md5s, self.cfg, logger=self.log,
                                      post_fn=post_fn,
                                      sleep_fn=lambda _s: None, **kw)

    def test_a_good_answer_becomes_a_set(self):
        answer = self.call(lambda u, p, t: (200, {'known': ['A' * 32]}))
        self.assertEqual(answer, frozenset({'a' * 32}))

    def test_a_transport_failure_is_unknown_not_empty(self):
        def boom(_u, _p, _t):
            raise snd.TransportError('connection refused')
        self.assertIsNone(self.call(boom))
        self.assertIn('every polygon', self.log.text)

    def test_a_malformed_answer_is_unknown_not_empty(self):
        self.assertIsNone(self.call(lambda u, p, t: (200, {'oops': 1})))

    def test_an_empty_known_list_really_means_nothing_is_stored(self):
        answer = self.call(lambda u, p, t: (200, {'known': []}))
        self.assertEqual(answer, frozenset())
        self.assertIsNotNone(answer)

    def test_no_base_url_is_unknown(self):
        class NoUrl(object):
            api_token = TOKEN
            land_geometry_manifest_url = None
        self.assertIsNone(snd.known_geometry_md5(['a' * 32], NoUrl(),
                                                 logger=self.log))

    def test_the_ask_is_batched(self):
        seen = []

        def post(_u, payload, _t):
            seen.append(len(payload['content_md5']))
            return 200, {'known': []}
        self.call(post, md5s=['%032x' % i for i in range(250)],
                  batch_size=100)
        self.assertEqual(seen, [100, 100, 50])

    def test_the_token_is_in_the_body_and_the_md5_are_normalised(self):
        seen = {}

        def post(_u, payload, _t):
            seen.update(payload)
            return 200, {'known': []}
        self.call(post, md5s=['B' * 32, 'b' * 32])
        self.assertEqual(seen['token'], TOKEN)
        # Дубль после нормализации спрашивается один раз.
        self.assertEqual(seen['content_md5'], ['b' * 32])


class TheListRecordIsSelectedByIdNotByPosition(unittest.TestCase):

    def test_a_page_gives_each_flight_its_own_record(self):
        page = {'code': 0, 'data': [list_row(7, area=700.0),
                                    list_row(8, area=800.0)]}
        self.assertEqual(ev.select_list_record(page, 8)['raw_area_m2'], 800.0)
        self.assertEqual(ev.select_list_record(page, 7)['raw_area_m2'], 700.0)

    def test_a_bare_record_still_works_for_the_forensic_import(self):
        self.assertEqual(
            ev.select_list_record(list_row(7, area=700.0), 7)['raw_area_m2'],
            700.0)

    def test_a_missing_flight_is_an_error_not_a_silent_none(self):
        page = {'code': 0, 'data': [list_row(7)]}
        with self.assertRaises(ValueError):
            ev.select_list_record(page, 99)

    def test_a_record_of_another_flight_is_refused(self):
        with self.assertRaises(ValueError):
            ev.select_list_record(list_row(7), 8)

    def test_identical_duplicates_on_one_page_are_harmless(self):
        page = {'code': 0, 'data': [list_row(7, area=100.0),
                                    list_row(7, area=100.0)]}
        self.assertEqual(ev.select_list_record(page, 7)['raw_area_m2'], 100.0)

    def test_differing_duplicates_on_one_page_are_refused(self):
        """Два разных описания одного вылета -- неоднозначность, не данные.

        [REASON]: первая редакция молча брала первую запись, то есть
        выбирала площадь по порядку строк в чужом ответе. Правило проекта
        обратное: UNKNOWN лучше правдоподобного вывода. Отказ доводит
        строку до NULL и помечает ревизию неразобранной -- видно, что
        произошло.
        """
        page = {'code': 0, 'data': [list_row(7, area=100.0),
                                    list_row(7, area=999.0)]}
        with self.assertRaises(ValueError):
            ev.select_list_record(page, 7)


class TheListSourceGoesThroughTheQueue(unittest.TestCase):
    """Как все остальные неизменяемые тела -- атомарно и с досылкой.

    [REASON]: первая редакция звала `send_sources` напрямую. Оппонент
    заметил, что это теряет ключ дедупликации, атомарную запись,
    коллекторный гейт секретов и саму возможность дослать после обрыва --
    всё, ради чего очередь и существует.
    """

    def setUp(self):
        import tempfile
        from drone_collector.outbox import Outbox
        self.dir = tempfile.mkdtemp(prefix='capture-outbox-')
        self.outbox = Outbox(self.dir).prepare()
        self.log = _Log()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def items(self, rows):
        items, _stats = src.list_source_items(
            [_Page(list_page(rows))], 'run', '2026-09-11 04:00:00')
        return items

    def test_the_pages_land_in_pending_and_survive_a_crash(self):
        result = src.enqueue_sources(self.outbox, self.items([list_row(1),
                                                              list_row(2)]),
                                     logger=self.log)
        self.assertEqual(result.queued, 2)
        # Файлы на диске -- прогон можно убить и продолжить следующим.
        self.assertEqual(len(self.outbox.pending()), 2)

    def test_the_same_page_queued_twice_is_deduplicated(self):
        items = self.items([list_row(1)])
        src.enqueue_sources(self.outbox, items, logger=self.log)
        again = src.enqueue_sources(self.outbox, items, logger=self.log)
        self.assertEqual(again.queued, 0)
        self.assertEqual(again.duplicates, 1)
        self.assertEqual(len(self.outbox.pending()), 1)

    def test_a_page_with_a_secret_is_refused_before_the_queue(self):
        poisoned = list_page([dict(list_row(1),
                                   note='https://x/?OSSAccessKeyId=LEAK')])
        items, _stats = src.list_source_items(
            [_Page(poisoned)], 'run', '2026-09-11 04:00:00')
        result = src.enqueue_sources(self.outbox, items, logger=self.log)
        self.assertEqual(result.queued, 0)
        self.assertEqual(result.secret_refused, 1)
        self.assertEqual(self.outbox.pending(), [])
        # И сам маркер в лог не печатается целиком.
        self.assertNotIn('LEAK', self.log.text)


class AnInterruptedSnapshotSendIsResumable(unittest.TestCase):
    """§6.7: оборванная отправка не теряет уже полученное.

    Восстановление -- НА УРОВНЕ КУСКОВ, и в отчёте это надо называть именно
    так. Куски, которые приёмник принял, лежат в `sent/` и второй раз не
    уходят; непринятые остаются в `pending/` и уходят следующим прогоном.
    Полигоны, скачанные внутри ОДНОГО оборванного цикла загрузки, в очередь
    попасть не успевают и будут скачаны заново -- этого уровня
    восстановления нет и он не заявляется.
    """

    def setUp(self):
        import tempfile
        from drone_collector.outbox import Outbox
        self.dir = tempfile.mkdtemp(prefix='capture-snap-')
        self.outbox = Outbox(self.dir).prepare()
        self.log = _Log()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    @staticmethod
    def accepted(chunk):
        """Ответ приёмника, который дренаж признаёт полным принятием.

        [REASON]: первая редакция возвращала просто `{'status': 'ok'}`, и
        дренаж справедливо счёл кусок НЕпринятым -- счётчики не сходились.
        Фикстура обязана повторять контракт приёмника, иначе она проверяет
        не тот путь.
        """
        lands = len(chunk.get('lands') or [])
        geoms = len(chunk.get('geometries') or [])
        return snd.LandSnapshotSendResult().add(
            {'status': 'ok', 'lands_seen': lands, 'lands_new': lands,
             'lands_seen_before': 0, 'errors': 0,
             'geometries_seen': geoms, 'geometries_new': geoms,
             'geometries_unchanged': 0, 'geometries_errors': 0})

    def bodies(self, run_id, count=3):
        nodes = []
        for i in range(count):
            body = polygon_bytes('%s-%d' % (run_id, i))
            nodes.append(land_node('%s-%d' % (run_id, i), md5_of(body),
                                   link=''))
        # [REASON]: ровно как в `_run_land_snapshot`. Без этого очередь
        # отвергает конверт -- её последний рубеж запрещает сам КЛЮЧ
        # `signedURL`, даже пустой. Первая редакция фикстуры об этом
        # забыла и получила ноль поставленных кусков.
        nodes = [src.strip_signed_urls(node) for node in nodes]
        return src.snapshot_chunk_bodies(run_id, '2026-09-11 04:00:00', nodes,
                                         expected_count=count, complete=True,
                                         max_nodes=1)

    def test_the_unsent_chunks_stay_and_the_sent_ones_do_not_return(self):
        bodies = self.bodies('run-a')
        src.enqueue_snapshot_chunks(self.outbox, bodies, logger=self.log)
        self.assertEqual(len(self.outbox.pending()), 3)

        # Приёмник принял два куска, потом связь оборвалась.
        calls = []

        class Cfg(object):
            api_token = TOKEN
            land_snapshot_sync_url = 'http://localhost/x'

        def flaky(chunk, cfg, logger=None, index=None, total=None, **kw):
            calls.append(index)
            if index == 3:
                raise snd.TransportError('connection reset')
            return self.accepted(chunk)

        with self.assertRaises(snd.TransportError):
            src.drain_land_snapshot_outbox(self.outbox, Cfg(), self.log,
                                           send_fn=flaky)
        self.assertEqual(len(self.outbox.pending()), 1)

        # Следующий прогон досылает ровно недостающий кусок.
        calls.clear()

        def ok(chunk, cfg, logger=None, index=None, total=None, **kw):
            calls.append(chunk['snapshot']['capture_run_id'])
            return self.accepted(chunk)

        src.drain_land_snapshot_outbox(self.outbox, Cfg(), self.log,
                                       send_fn=ok)
        self.assertEqual(calls, ['run-a'])
        self.assertEqual(self.outbox.pending(), [])

    def test_an_older_unsent_snapshot_goes_before_a_newer_one(self):
        src.enqueue_snapshot_chunks(self.outbox, self.bodies('run-a', 1),
                                    logger=self.log)
        src.enqueue_snapshot_chunks(self.outbox, self.bodies('run-b', 1),
                                    logger=self.log)
        seen = []

        class Cfg(object):
            api_token = TOKEN
            land_snapshot_sync_url = 'http://localhost/x'

        def ok(chunk, cfg, logger=None, index=None, total=None, **kw):
            seen.append(chunk['snapshot']['capture_run_id'])
            return self.accepted(chunk)

        src.drain_land_snapshot_outbox(self.outbox, Cfg(), self.log,
                                       send_fn=ok)
        self.assertEqual(seen, ['run-a', 'run-b'])


class TheAlarmReachesTheOperator(unittest.TestCase):
    """Находки состязательного ревью: тревога и счётчики.

    [REASON]: приёмник считал `geometries_referenced_but_absent` и отдавал
    его в JSON, а сборщик молча выбрасывал -- ключа не было ни в
    `COUNTER_KEYS`, ни в строке лога, ни в сводке прогона. Тревога, которую
    никто не читает, тревогой не является.
    """

    def test_the_counter_survives_the_trip_from_the_receiver(self):
        result = snd.LandSnapshotSendResult().add({
            'status': 'ok', 'lands_seen': 1, 'lands_new': 1,
            'lands_seen_before': 0, 'errors': 0, 'geometries_seen': 0,
            'geometries_new': 0, 'geometries_unchanged': 0,
            'geometries_errors': 0, 'geometries_referenced_but_absent': 3})
        self.assertEqual(result.geometries_referenced_but_absent, 3)

    def test_it_sums_over_chunks_like_every_other_counter(self):
        result = snd.LandSnapshotSendResult()
        for value in (1, 0, 2):
            result.add({'status': 'ok', 'lands_seen': 0, 'lands_new': 0,
                        'lands_seen_before': 0, 'errors': 0,
                        'geometries_seen': 0, 'geometries_new': 0,
                        'geometries_unchanged': 0, 'geometries_errors': 0,
                        'geometries_referenced_but_absent': value})
        self.assertEqual(result.geometries_referenced_but_absent, 3)

    def test_an_answer_without_the_key_is_zero_not_a_crash(self):
        # Приёмник прежней версии просто не пришлёт это поле.
        result = snd.LandSnapshotSendResult().add({'status': 'ok'})
        self.assertEqual(result.geometries_referenced_but_absent, 0)


class TheListApiStatusComesFromTheBytes(unittest.TestCase):
    """`body_code` ждёт БАЙТЫ; разобранный dict давал молчаливый NULL.

    [REASON]: `bytes(dict)` бросает TypeError, который `body_code` гасит и
    возвращает None. В результате `api_status` у КАЖДОЙ живой ревизии
    списка оставался NULL, а те же байты из форензик-импорта получали 0 --
    одно и то же тело описывалось по-разному в зависимости от того, кто
    записался первым.
    """

    def test_a_live_page_records_the_code_dji_answered(self):
        items, _stats = src.list_source_items(
            [_Page(list_page([list_row(1)]))], 'run', '2026-09-11 04:00:00')
        self.assertEqual(items[0]['api_status'], 0)

    def test_control_a_rejection_code_is_recorded_too(self):
        raw = json.dumps({'code': 101, 'message': 'nope',
                          'data': [list_row(1)]}).encode('utf-8')
        items, _stats = src.list_source_items(
            [_Page(raw)], 'run', '2026-09-11 04:00:00')
        self.assertEqual(items[0]['api_status'], 101)

    def test_the_parsed_dict_would_have_given_nothing(self):
        # Прямое доказательство исходного дефекта.
        raw = list_page([list_row(1)])
        self.assertIsNone(src.body_code(json.loads(raw.decode('utf-8'))))
        self.assertEqual(src.body_code(raw), 0)


class WindowCountersAccumulate(unittest.TestCase):
    """Период, пересекающий границу года, идёт двумя окнами.

    [REASON]: `_account_for` вызывается по окну, и все остальные счётчики
    прогона накапливаются. Первая редакция счётчиков списка присваивала --
    сводка показывала только последнее окно, и страницы БЕЗ доказательства
    из первого окна исчезали из отчёта совсем.
    """

    def test_bump_adds_instead_of_replacing(self):
        from drone_collector.main import _bump
        state = {}
        _bump(state, 'list_sources_built', 700)
        _bump(state, 'list_sources_built', 300)
        self.assertEqual(state['list_sources_built'], 1000)

    def test_bump_starts_from_nothing_and_tolerates_none(self):
        from drone_collector.main import _bump
        state = {}
        _bump(state, 'list_pages_without_raw', None)
        self.assertEqual(state['list_pages_without_raw'], 0)
        _bump(state, 'list_pages_without_raw', 3)
        self.assertEqual(state['list_pages_without_raw'], 3)

    def test_every_accumulating_list_counter_is_in_the_summary(self):
        """Счётчик, которого нет в шаблоне сводки, не печатается вовсе."""
        from drone_collector.main import FLIGHT_SUMMARY_KEYS
        for key in ('list_pages_captured', 'list_pages_without_raw',
                    'list_sources_built', 'list_sources_queued',
                    'list_sources_duplicates', 'list_sources_refused',
                    'list_sources_sent', 'list_sources_left_pending'):
            self.assertIn(key, FLIGHT_SUMMARY_KEYS, key)

    def test_the_absent_body_alarm_is_in_the_snapshot_summary(self):
        from drone_collector.main import SNAPSHOT_SUMMARY_KEYS
        self.assertIn('snapshot_geometries_referenced_but_absent',
                      SNAPSHOT_SUMMARY_KEYS)
