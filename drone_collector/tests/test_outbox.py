# -*- coding: utf-8 -*-
"""Тесты очереди на диске (`drone_collector/outbox.py`).

Очередь -- единственное место этапа B, которое переживает процесс. Всё, что
она гарантирует, проверяется здесь, и почти каждая проверка снабжена
отрицательным контролем: контроль, дающий один и тот же результат при верном
и неверном коде, проверкой не является.
"""

import json
import os
import tempfile
import unittest

from pathlib import Path

from drone_collector.outbox import (
    CorruptEnvelope, ENVELOPE_VERSION, EnvelopeTooLarge, KIND_FIELD_GEOMETRY,
    KIND_ROUTE, MAX_ENVELOPE_BYTES, MAX_NAME_LENGTH, Outbox, OutboxError,
    SecretInEnvelope, TEMP_SUFFIX, dedupe_key_for, find_secret_markers,
    utc_now_iso)


class OutboxTestCase(unittest.TestCase):
    """Общая заготовка: очередь в собственном временном каталоге."""

    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.root = Path(self._directory.name) / 'outbox'
        self.outbox = Outbox(self.root).prepare()

    def enqueue(self, identity='622715275', body=None, sha='a' * 64,
                kind=KIND_ROUTE, **kwargs):
        return self.outbox.enqueue(kind, identity,
                                   body if body is not None else {'points': 3},
                                   sha, **kwargs)


# ─── 4. Атомарность записи ───────────────────────────────────────────────────

class TestAtomicWrite(OutboxTestCase):

    def test_a_written_envelope_is_complete_and_parses(self):
        path, duplicate = self.enqueue()
        self.assertFalse(duplicate)
        with open(path, encoding='utf-8') as handle:
            envelope = json.load(handle)
        self.assertEqual(envelope['envelope_version'], ENVELOPE_VERSION)
        self.assertEqual(envelope['kind'], KIND_ROUTE)
        self.assertEqual(envelope['identity'], '622715275')
        self.assertEqual(envelope['body'], {'points': 3})

    def test_no_temporary_file_survives_a_successful_write(self):
        self.enqueue()
        self.assertEqual(self.outbox.stale_temp_files(), [])

    def test_a_write_that_fails_leaves_neither_a_json_nor_a_tmp(self):
        """Обрыв на середине записи не должен оставлять ничего.

        Сбой вносится подменой `os.replace`: сериализация и запись прошли,
        а установка на место -- нет. Это в точности момент, в котором
        наблюдатель мог бы увидеть половину файла.
        """
        original = os.replace

        def explode(*_args, **_kwargs):
            raise OSError('disk went away')

        os.replace = explode
        try:
            with self.assertRaises(OSError):
                self.enqueue()
        finally:
            os.replace = original

        self.assertEqual(self.outbox.pending(), [])
        self.assertEqual(self.outbox.stale_temp_files(), [],
                         'оборванная запись оставила временный файл')

    def test_a_failure_while_writing_the_body_leaves_nothing_either(self):
        """Отказ до `os.replace` -- второй способ оборвать ту же запись."""
        import drone_collector.outbox as module
        original = module.json.dumps

        def explode(*_args, **_kwargs):
            raise ValueError('serialisation failed')

        module.json.dumps = explode
        try:
            with self.assertRaises(ValueError):
                self.enqueue()
        finally:
            module.json.dumps = original

        self.assertEqual(self.outbox.pending(), [])
        self.assertEqual(self.outbox.stale_temp_files(), [])

    def test_the_reader_never_sees_a_half_written_file(self):
        """Отрицательный контроль к атомарности.

        Пока запись идёт, в каталоге лежит `.tmp`. `pending()` обязан его не
        видеть -- иначе отправщик прочитал бы недописанный JSON.
        """
        half = self.outbox.pending_dir / ('route_x_deadbeef' + TEMP_SUFFIX)
        half.write_text('{"envelope_version": 1, "bo', encoding='utf-8')
        self.assertEqual(self.outbox.pending(), [])
        self.assertEqual(len(self.outbox.stale_temp_files()), 1)


# ─── 5. Идемпотентность ──────────────────────────────────────────────────────

class TestIdempotence(OutboxTestCase):

    def test_the_same_response_twice_creates_one_file(self):
        first, dup_first = self.enqueue()
        second, dup_second = self.enqueue()
        self.assertFalse(dup_first)
        self.assertTrue(dup_second)
        self.assertEqual(first, second)
        self.assertEqual(len(self.outbox.pending()), 1)

    def test_a_repeat_does_not_rewrite_the_existing_file(self):
        path, _ = self.enqueue(received_at='2026-01-01T00:00:00+00:00')
        self.enqueue(received_at='2026-12-31T23:59:59+00:00')
        envelope = self.outbox.read(path)
        self.assertEqual(envelope['received_at'], '2026-01-01T00:00:00+00:00',
                         'повторная постановка переписала запись')

    def test_a_different_content_hash_is_a_different_record(self):
        """Отрицательный контроль: дедупликация не склеивает разное."""
        self.enqueue(sha='a' * 64)
        _, duplicate = self.enqueue(sha='b' * 64)
        self.assertFalse(duplicate)
        self.assertEqual(len(self.outbox.pending()), 2)

    def test_an_already_sent_record_is_not_queued_again(self):
        path, _ = self.enqueue()
        moved = self.outbox.mark_sent(path)
        again, duplicate = self.enqueue()
        self.assertTrue(duplicate)
        self.assertEqual(again, moved,
                         'дубликат назван лежащим в pending, а он в sent')
        self.assertEqual(self.outbox.pending(), [])
        self.assertEqual(len(self.outbox.sent()), 1)


# ─── 6. Дедупликация ─────────────────────────────────────────────────────────

class TestDedupeKey(OutboxTestCase):

    def test_the_key_is_stable_across_calls(self):
        self.assertEqual(dedupe_key_for(KIND_ROUTE, '1', 'x' * 64),
                         dedupe_key_for(KIND_ROUTE, '1', 'x' * 64))

    def test_kind_participates_in_the_key(self):
        """Маршрут и геометрия с одним идентификатором -- разные записи."""
        self.assertNotEqual(dedupe_key_for(KIND_ROUTE, '1', 'x' * 64),
                            dedupe_key_for(KIND_FIELD_GEOMETRY, '1', 'x' * 64))

    def test_identities_that_the_filename_filter_collapses_stay_distinct(self):
        """Ключевой случай: фильтр имени схлопывает, хеш -- нет.

        `A/B` и `A:B` после вырезания небезопасных символов дают одну и ту же
        читаемую часть. Если бы имя строилось только из неё, две разные
        геометрии писались бы в один файл, и вторая молча пропадала.
        """
        left = dedupe_key_for(KIND_ROUTE, 'A/B', 'x' * 64)
        right = dedupe_key_for(KIND_ROUTE, 'A:B', 'x' * 64)
        self.assertNotEqual(left, right)

    def test_identities_differing_only_in_case_stay_distinct(self):
        """На Windows имена файлов регистронезависимы -- различие в хеш."""
        left = dedupe_key_for(KIND_ROUTE, 'p03335975', 'x' * 64)
        right = dedupe_key_for(KIND_ROUTE, 'P03335975', 'x' * 64)
        self.assertNotEqual(left.lower(), right.lower())

    def test_two_such_identities_really_produce_two_files(self):
        self.enqueue(identity='A/B')
        self.enqueue(identity='A:B')
        self.assertEqual(len(self.outbox.pending()), 2)


# ─── 7. Восстановление после прерванной записи ───────────────────────────────

class TestResume(OutboxTestCase):

    def test_pending_survives_a_new_outbox_object(self):
        self.enqueue(identity='1')
        self.enqueue(identity='2')
        reopened = Outbox(self.root)
        self.assertEqual(len(reopened.pending()), 2)

    def test_a_run_stopped_midway_resumes_with_exactly_what_is_left(self):
        first, _ = self.enqueue(identity='1')
        self.enqueue(identity='2')
        self.enqueue(identity='3')
        self.outbox.mark_sent(first)          # прогон оборвался здесь

        reopened = Outbox(self.root)
        left = [p.name for p in reopened.pending()]
        self.assertEqual(len(left), 2)
        self.assertNotIn(first.name, left)

    def test_a_stale_temp_file_is_swept_and_does_not_block_a_rewrite(self):
        stale = self.outbox.pending_dir / ('route_1_deadbeefdeadbeef' + TEMP_SUFFIX)
        stale.write_text('half', encoding='utf-8')
        self.assertEqual(self.outbox.sweep_stale_temp(), 1)
        self.assertFalse(stale.exists())
        _, duplicate = self.enqueue(identity='1')
        self.assertFalse(duplicate, 'остаток .tmp был принят за готовую запись')

    def test_sweeping_never_touches_real_records(self):
        """Отрицательный контроль: подметание не должно убирать очередь."""
        self.enqueue(identity='1')
        self.assertEqual(self.outbox.sweep_stale_temp(), 0)
        self.assertEqual(len(self.outbox.pending()), 1)


# ─── 8. Отказ при попытке сохранить секрет ───────────────────────────────────

class TestSecretsAreRefused(OutboxTestCase):

    SECRETS = (
        ('signedURL в теле',
         {'geometry': {'storage': {'signedURL': 'https://x.invalid/a'}}}),
        ('подписанная ссылка значением',
         {'url': 'https://oss.aliyuncs.com/a?OSSAccessKeyId=LTAI&Signature=abc'}),
        ('cookie',
         {'headers': {'Set-Cookie': 'session=abc; Path=/'}}),
        ('заголовок авторизации',
         {'headers': {'Authorization': 'Bearer eyJhbGciOi'}}),
        ('токен в поле',
         {'note': 'x-auth-token was rotated'}),
        ('состояние сессии',
         {'debug': {'storage_state': 'data/storage_state.json'}}),
        ('подпись AWS',
         {'link': 'https://s3.invalid/f?X-Amz-Signature=deadbeef'}),
        ('чужое облако той же формы',
         {'link': 'https://storage.example.test/f?sig=abc&se=2026-08-26'}),
    )

    def test_every_known_secret_shape_is_refused(self):
        for label, body in self.SECRETS:
            with self.subTest(label):
                with self.assertRaises(SecretInEnvelope):
                    self.enqueue(identity=label, body=body)

    def test_nothing_is_written_when_a_secret_is_refused(self):
        with self.assertRaises(SecretInEnvelope):
            self.enqueue(body={'signedURL': 'https://x.invalid/a'})
        self.assertEqual(self.outbox.pending(), [])
        self.assertEqual(self.outbox.stale_temp_files(), [])

    def test_the_exception_text_never_echoes_the_secret(self):
        secret = 'https://oss.aliyuncs.com/f?OSSAccessKeyId=LTAI5tSECRETVALUE'
        with self.assertRaises(SecretInEnvelope) as caught:
            self.enqueue(body={'link': secret})
        message = str(caught.exception)
        self.assertNotIn('LTAI5tSECRETVALUE', message)
        self.assertNotIn(secret, message)

    def test_a_secret_hidden_in_a_key_is_caught_too(self):
        with self.assertRaises(SecretInEnvelope):
            self.enqueue(body={'signedURL': 'nothing here'})

    def test_a_secret_in_diagnostics_is_caught_too(self):
        with self.assertRaises(SecretInEnvelope):
            self.enqueue(diagnostics={'source_url':
                                      'https://x.invalid/f?Signature=abc'})

    def test_a_clean_envelope_is_accepted(self):
        """Отрицательный контроль: проверка обязана пропускать нормальное.

        Без него «отказываться всегда» прошло бы все тесты выше.
        """
        path, _ = self.enqueue(body={
            'type': 'FeatureCollection',
            'features': [{'type': 'Feature',
                          'properties': {'funcType': 'PlantZone'},
                          'geometry': {'type': 'Polygon',
                                       'coordinates': [[[64.6, 40.1]]]}}]})
        self.assertTrue(path.exists())

    def test_ordinary_field_words_do_not_trip_the_detector(self):
        """Отрицательный контроль формы: `?expires=` ловится, слово -- нет."""
        self.assertEqual(find_secret_markers('the token expires tomorrow'), [])
        self.assertEqual(find_secret_markers('signature of the operator'), [])
        self.assertEqual(
            find_secret_markers('https://x.invalid/f?expires=1'),
            ['signed-url-query'])


# ─── 9. Ограничения размера ──────────────────────────────────────────────────

class TestSizeCap(OutboxTestCase):

    def test_an_oversized_envelope_is_refused(self):
        payload = {'pad': 'x' * (MAX_ENVELOPE_BYTES + 1024)}
        with self.assertRaises(EnvelopeTooLarge):
            self.enqueue(body=payload)

    def test_nothing_is_written_when_the_cap_is_exceeded(self):
        with self.assertRaises(EnvelopeTooLarge):
            self.enqueue(body={'pad': 'x' * (MAX_ENVELOPE_BYTES + 1024)})
        self.assertEqual(self.outbox.pending(), [])
        self.assertEqual(self.outbox.stale_temp_files(), [])

    def test_an_envelope_just_under_the_cap_is_accepted(self):
        """Отрицательный контроль: потолок не отвергает всё подряд."""
        payload = {'pad': 'x' * (MAX_ENVELOPE_BYTES // 2)}
        path, _ = self.enqueue(body=payload)
        self.assertTrue(path.exists())

    def test_the_cap_is_measured_in_bytes_not_characters(self):
        """Кириллица в UTF-8 занимает два байта на символ.

        Если бы предел считался по `len(text)`, запись из кириллицы прошла бы
        вдвое больше потолка и легла бы на диск.
        """
        payload = {'pad': 'я' * ((MAX_ENVELOPE_BYTES // 2) + 1024)}
        with self.assertRaises(EnvelopeTooLarge):
            self.enqueue(body=payload)


# ─── 10. Поведение при повреждённом элементе очереди ─────────────────────────

class TestCorruptElement(OutboxTestCase):

    def write_raw(self, name, text):
        path = self.outbox.pending_dir / name
        path.write_text(text, encoding='utf-8')
        return path

    def test_unparseable_json_raises_corrupt_envelope(self):
        path = self.write_raw('route_broken_0000000000000001.json', '{"a":')
        with self.assertRaises(CorruptEnvelope):
            self.outbox.read(path)

    def test_a_json_scalar_raises_corrupt_envelope(self):
        path = self.write_raw('route_scalar_0000000000000002.json', '42')
        with self.assertRaises(CorruptEnvelope):
            self.outbox.read(path)

    def test_a_future_version_is_refused_rather_than_guessed(self):
        path = self.write_raw(
            'route_future_0000000000000003.json',
            json.dumps({'envelope_version': ENVELOPE_VERSION + 1,
                        'kind': KIND_ROUTE, 'body': {}}))
        with self.assertRaises(CorruptEnvelope):
            self.outbox.read(path)

    def test_an_unknown_kind_is_refused(self):
        path = self.write_raw(
            'route_kind_0000000000000004.json',
            json.dumps({'envelope_version': ENVELOPE_VERSION,
                        'kind': 'whatever', 'body': {}}))
        with self.assertRaises(CorruptEnvelope):
            self.outbox.read(path)

    def test_invalid_utf8_raises_corrupt_envelope_not_unicode_error(self):
        path = self.outbox.pending_dir / 'route_bytes_0000000000000005.json'
        path.write_bytes(b'{"envelope_version": 1, "kind": "\xff\xfe"}')
        with self.assertRaises(CorruptEnvelope):
            self.outbox.read(path)

    def test_a_corrupt_record_can_be_quarantined_and_leaves_the_queue(self):
        path = self.write_raw('route_broken_0000000000000006.json', 'not json')
        moved = self.outbox.quarantine(path)
        self.assertTrue(moved.exists())
        self.assertFalse(path.exists())
        self.assertEqual(self.outbox.pending(), [])
        self.assertEqual(len(self.outbox.corrupt()), 1)

    def test_one_corrupt_record_does_not_hide_the_healthy_ones(self):
        """Главное свойство: битый элемент не валит весь прогон отправки."""
        good, _ = self.enqueue(identity='1')
        bad = self.write_raw('route_broken_0000000000000007.json', '{')
        readable, corrupt = [], []
        for path in self.outbox.pending():
            try:
                readable.append(self.outbox.read(path))
            except CorruptEnvelope:
                corrupt.append(path)
        self.assertEqual(len(readable), 1)
        self.assertEqual(corrupt, [bad])
        self.assertEqual(readable[0]['identity'], '1')
        self.assertTrue(good.exists())

    def test_a_healthy_record_reads_back(self):
        """Отрицательный контроль: read() не отвергает всё подряд."""
        path, _ = self.enqueue(identity='1')
        self.assertEqual(self.outbox.read(path)['identity'], '1')


# ─── 11. Совместимость с Windows ─────────────────────────────────────────────

class TestWindowsCompatibility(OutboxTestCase):

    FORBIDDEN = set('<>:"/\\|?*')
    DOS_DEVICES = {'CON', 'PRN', 'AUX', 'NUL'}
    DOS_DEVICES |= {'COM%d' % n for n in range(1, 10)}
    DOS_DEVICES |= {'LPT%d' % n for n in range(1, 10)}

    HOSTILE_IDENTITIES = (
        'ACCOUNT-flyer-04f66629-5fef-4c2a-856f-680baeb9f2ed',
        'C:\\Windows\\System32',
        'a/b/c',
        'кириллица в идентификаторе',
        'con',
        'NUL',
        'name with spaces',
        'trailing.dot.',
        'x' * 400,
        '?*<>|"',
    )

    def test_no_generated_name_contains_a_character_windows_forbids(self):
        for identity in self.HOSTILE_IDENTITIES:
            with self.subTest(identity[:40]):
                key = dedupe_key_for(KIND_ROUTE, identity, 'x' * 64)
                self.assertFalse(self.FORBIDDEN & set(key),
                                 'имя содержит запрещённый символ: %r' % key)

    def test_no_generated_name_is_a_dos_device_name(self):
        """`CON.json` на Windows создать нельзя вовсе.

        Имя всегда начинается с имени вида, поэтому совпасть не может -- но
        проверяется свойство, а не рассуждение о нём.
        """
        for identity in self.HOSTILE_IDENTITIES:
            with self.subTest(identity[:40]):
                key = dedupe_key_for(KIND_ROUTE, identity, 'x' * 64)
                self.assertNotIn(key.split('.')[0].upper(), self.DOS_DEVICES)

    def test_no_generated_name_exceeds_the_length_cap(self):
        for identity in self.HOSTILE_IDENTITIES:
            with self.subTest(identity[:40]):
                key = dedupe_key_for(KIND_FIELD_GEOMETRY, identity, 'x' * 64)
                self.assertLessEqual(len(key), MAX_NAME_LENGTH)

    def test_hostile_identities_really_round_trip_through_the_queue(self):
        for identity in self.HOSTILE_IDENTITIES:
            with self.subTest(identity[:40]):
                path, _ = self.enqueue(identity=identity, sha='c' * 64,
                                       body={'identity_echo': identity})
                self.assertEqual(self.outbox.read(path)['identity'], identity)

    def test_marking_sent_twice_over_an_existing_target_does_not_raise(self):
        """`os.rename` здесь падал бы: цель уже существует.

        Повторная отправка того же ключа -- обычное дело после возобновления,
        и на Windows наивное переименование сорвало бы прогон.
        """
        path, _ = self.enqueue(identity='1')
        self.outbox.mark_sent(path)
        again = self.outbox.pending_dir / path.name
        again.write_text(json.dumps({'envelope_version': ENVELOPE_VERSION,
                                     'kind': KIND_ROUTE, 'identity': '1',
                                     'body': {}}), encoding='utf-8')
        self.outbox.mark_sent(again)
        self.assertEqual(len(self.outbox.sent()), 1)
        self.assertEqual(self.outbox.pending(), [])

    def test_no_path_component_ends_with_a_space_or_a_dot(self):
        """Windows молча срезает хвостовые пробелы и точки в именах.

        Имя `x.` создаётся как `x`, и файл потом не находится по своему же
        имени. Хвост имени -- это шестнадцатеричный хеш, поэтому свойство
        держится; проверяется оно, а не намерение.
        """
        for identity in self.HOSTILE_IDENTITIES:
            with self.subTest(identity[:40]):
                key = dedupe_key_for(KIND_ROUTE, identity, 'x' * 64)
                self.assertFalse(key.endswith((' ', '.')))


# ─── Прочее: контракт постановки ─────────────────────────────────────────────

class TestEnqueueContract(OutboxTestCase):

    def test_an_unknown_kind_is_refused(self):
        with self.assertRaises(OutboxError):
            self.outbox.enqueue('whatever', '1', {}, 'x' * 64)

    def test_a_missing_content_hash_is_refused(self):
        with self.assertRaises(OutboxError):
            self.outbox.enqueue(KIND_ROUTE, '1', {}, '')

    def test_received_at_is_timezone_aware(self):
        path, _ = self.enqueue()
        stamp = self.outbox.read(path)['received_at']
        self.assertTrue(stamp.endswith('+00:00'),
                        'метка времени наивная: %r' % stamp)

    def test_utc_now_iso_carries_a_zone(self):
        self.assertTrue(utc_now_iso().endswith('+00:00'))

    def test_counts_reports_every_bucket(self):
        path, _ = self.enqueue(identity='1')
        self.enqueue(identity='2')
        self.outbox.mark_sent(path)
        (self.outbox.pending_dir / ('route_x_1' + TEMP_SUFFIX)).write_text(
            'half', encoding='utf-8')
        broken = self.outbox.pending_dir / 'route_b_0000000000000009.json'
        broken.write_text('{', encoding='utf-8')
        self.outbox.quarantine(broken)
        self.assertEqual(self.outbox.counts(),
                         {'pending': 1, 'sent': 1, 'corrupt': 1,
                          'stale_temp': 1})

    def test_records_returns_only_the_asked_for_kind(self):
        route_path, _ = self.enqueue(identity='1', kind=KIND_ROUTE)
        geometry_path, _ = self.outbox.enqueue(
            KIND_FIELD_GEOMETRY, 'u1', {}, 'b' * 64)
        self.assertEqual(self.outbox.records(KIND_ROUTE), [route_path])
        self.assertEqual(self.outbox.records(KIND_FIELD_GEOMETRY),
                         [geometry_path])

    def test_records_spans_pending_and_sent(self):
        first, _ = self.enqueue(identity='1')
        self.enqueue(identity='2')
        self.outbox.mark_sent(first)
        self.assertEqual(len(self.outbox.records(KIND_ROUTE)), 2)

    def test_records_refuses_an_unknown_kind(self):
        with self.assertRaises(OutboxError):
            self.outbox.records('whatever')

    def test_the_secret_check_runs_on_the_text_that_is_written(self):
        """Отрицательный контроль: проверяется ровно записываемый текст.

        Кириллица сериализуется с `ensure_ascii=False`, то есть в файл идут
        сами символы. Если бы проверка шла по другому представлению --
        например с `ensure_ascii=True`, где всё превращается в \\uXXXX, --
        подпись, набранная рядом с кириллицей, всё равно нашлась бы, а вот
        обратный случай молча разошёлся бы. Тест держит их одним текстом.
        """
        with self.assertRaises(SecretInEnvelope):
            self.enqueue(body={'примечание': 'ссылка https://x.invalid/f'
                                             '?Signature=abc'})
        path, _ = self.enqueue(body={'примечание': 'обычное поле'})
        self.assertIn('примечание', path.read_text(encoding='utf-8'))

    def test_the_queue_creates_its_own_directories(self):
        fresh = Outbox(self.root / 'nested' / 'deeper')
        path, _ = fresh.enqueue(KIND_ROUTE, '1', {}, 'x' * 64)
        self.assertTrue(path.exists())


if __name__ == '__main__':
    unittest.main()
