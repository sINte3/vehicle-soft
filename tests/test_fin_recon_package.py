# -*- coding: utf-8 -*-
"""FIN-RECON-STORAGE-001: проверка приватного пакета до импорта.

Синтетический пакет (tests/fin_recon_fixture.py) годен. Каждая порча --
один байт любого файла, лишний или недостающий файл, имя блоба не равно его
SHA-256, книга не из исходного архива, число JSON вместо строки, ключ
дважды -- даёт отказ с узнаваемой причиной, и ни одна порча не проходит.

ВСЕ ДАННЫЕ СИНТЕТИЧЕСКИЕ. Запуск:
  python -m unittest tests.test_fin_recon_package -v
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from fin_recon import package  # noqa: E402
from tests import fin_recon_fixture as fx  # noqa: E402


class PackageBase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='fin_recon_pkg_')
        self.root = os.path.join(self.tmp, 'pkg')
        self.paths = fx.write_package(self.root)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def refused(self, *fragments):
        with self.assertRaises(package.PackageError) as ctx:
            package.verify(self.root)
        text = '\n'.join(ctx.exception.problems)
        for fragment in fragments:
            self.assertIn(fragment, text)
        return text

    def flip(self, rel, resum=False):
        path = os.path.join(self.root, *rel.split('/'))
        with open(path, 'rb') as fh:
            data = bytearray(fh.read())
        data[len(data) // 2] ^= 0x01
        with open(path, 'wb') as fh:
            fh.write(bytes(data))
        if resum:
            fx.write_sums(self.root)

    def blob_of(self, role):
        return next(b['path'] for b in self.paths['manifest']['blobs']
                    if b['role'] == role)


class Valid(PackageBase):

    def test_synthetic_package_verifies(self):
        pkg = package.verify(self.root)
        self.assertEqual(pkg.period, '2031-03')
        self.assertEqual(pkg.version, 1)
        self.assertEqual(len(pkg.content_sha256), 64)

    def test_paths_are_ascii(self):
        for rel in package.verify(self.root).files:
            self.assertTrue(rel.isascii(), rel)


class OneByte(PackageBase):

    def test_snapshot_byte(self):
        self.flip('snapshot.json')
        self.refused('SHA-256 mismatch: snapshot.json')

    def test_manifest_byte(self):
        self.flip('manifest.json')
        self.refused('SHA-256 mismatch: manifest.json')

    def test_register_xlsx_byte(self):
        rel = self.blob_of('REGISTER_XLSX')
        self.flip(rel)
        self.refused('SHA-256 mismatch: %s' % rel, 'blob name is not its SHA-256')

    def test_register_md_byte_even_with_fresh_sums(self):
        """SHA256SUMS пересчитан -- имя блоба всё равно выдаёт подмену."""
        rel = self.blob_of('REGISTER_MD')
        self.flip(rel, resum=True)
        self.refused('blob name is not its SHA-256: %s' % rel)

    def test_expectations_byte(self):
        self.flip('expectations/expect.json')
        self.refused('SHA-256 mismatch: expectations/expect.json')

    def test_sums_line_changed(self):
        path = os.path.join(self.root, 'SHA256SUMS.txt')
        with open(path, encoding='ascii') as fh:
            text = fh.read()
        first = text.splitlines()[0]
        bad = ('0' if first[0] != '0' else '1') + first[1:]
        with open(path, 'w', encoding='ascii', newline='\n') as fh:
            fh.write(text.replace(first, bad, 1))
        self.refused('SHA-256 mismatch')


class Composition(PackageBase):

    def test_extra_file(self):
        with open(os.path.join(self.root, 'notes.txt'), 'w') as fh:
            fh.write('x')
        self.refused('file not in SHA256SUMS.txt: notes.txt')

    def test_missing_file(self):
        os.remove(os.path.join(self.root, 'expectations', 'expect.json'))
        self.refused('listed in SHA256SUMS.txt but missing')

    def test_missing_core_file(self):
        os.remove(os.path.join(self.root, 'snapshot.json'))
        self.refused('missing snapshot.json')

    def test_unlisted_blob_even_if_summed(self):
        blob = os.path.join(self.root, 'blobs', ('c' * 64) + '.bin')
        with open(blob, 'wb') as fh:
            fh.write(b'x')
        fx.write_sums(self.root)
        self.refused('blob name is not its SHA-256')

    def test_unsafe_path_in_sums(self):
        path = os.path.join(self.root, 'SHA256SUMS.txt')
        with open(path, 'a', encoding='ascii') as fh:
            fh.write('%s  ../outside.txt\n' % ('a' * 64))
        self.refused('unsafe path')

    def test_source_blob_absent(self):
        """Источник «в пакете», а блоба с его хешем нет -- отказ."""
        rel = self.blob_of('DJI_EXPORT_AFTER')
        os.remove(os.path.join(self.root, *rel.split('/')))

        def drop(manifest):
            manifest['blobs'] = [b for b in manifest['blobs']
                                 if b['path'] != rel]
        fx.write_package(self.root, snapshot=self.paths['snapshot'],
                         mutate_manifest=drop)
        os.remove(os.path.join(self.root, *rel.split('/')))
        fx.write_sums(self.root)
        self.refused('marked in_package but no blob has its SHA-256')

    def test_book_not_in_its_container(self):
        snap = self.paths['snapshot']
        book = next(s for s in snap['sources'] if s['role'] == 'BOOK_PRIMARY')
        foreign = b'SYNTHETIC FOREIGN BOOK\n'
        import hashlib
        book['sha256'] = hashlib.sha256(foreign).hexdigest()
        book['bytes'] = len(foreign)
        blobs = fx.make_blobs()
        key = book['key']
        blobs[key] = fx._blob(foreign, '.xlsx', 'BOOK_PRIMARY', book['name'],
                              'S01', book['member'])
        shutil.rmtree(self.root)
        fx.write_package(self.root, snapshot=snap, blobs=blobs)
        self.refused('is not a member of its container')


class Json(PackageBase):

    def rewrite_snapshot(self, text):
        with open(os.path.join(self.root, 'snapshot.json'), 'w',
                  encoding='utf-8') as fh:
            fh.write(text)

        def fix(manifest):
            pass
        # manifest хранит хеш файла снимка -- пересобрать его и суммы.
        import hashlib
        manifest_path = os.path.join(self.root, 'manifest.json')
        with open(manifest_path, encoding='utf-8') as fh:
            manifest = json.load(fh)
        data = text.encode('utf-8')
        manifest['snapshot']['sha256'] = hashlib.sha256(data).hexdigest()
        manifest['snapshot']['bytes'] = len(data)
        with open(manifest_path, 'w', encoding='utf-8') as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=1)
        fx.write_sums(self.root)

    def test_float_refused(self):
        with open(os.path.join(self.root, 'snapshot.json'),
                  encoding='utf-8') as fh:
            text = fh.read()
        self.rewrite_snapshot(text.replace('"raw_ha": "900.1234"',
                                           '"raw_ha": 900.1234', 1))
        self.refused('a JSON float 900.1234 is not allowed')

    def test_duplicate_key_refused(self):
        with open(os.path.join(self.root, 'snapshot.json'),
                  encoding='utf-8') as fh:
            text = fh.read()
        self.rewrite_snapshot(text.replace('"schema": "fin-recon-snapshot/1"',
                                           '"schema": "fin-recon-snapshot/1",'
                                           ' "schema": "x"', 1))
        self.refused('duplicate key')

    def test_contract_problem_surfaces_through_the_package(self):
        snap = self.paths['snapshot']
        snap['header']['owner_approved_at'] = '2031-10-13'
        shutil.rmtree(self.root)
        fx.write_package(self.root, snapshot=snap)
        self.refused('snapshot.json: header.owner_approved_at')

    def test_manifest_period_must_match(self):
        def change(manifest):
            manifest['period'] = '2031-04'
        shutil.rmtree(self.root)
        fx.write_package(self.root, mutate_manifest=change)
        self.refused('period/version')

    def test_manifest_content_hash_must_match(self):
        def change(manifest):
            manifest['snapshot']['content_sha256'] = 'a' * 64
        shutil.rmtree(self.root)
        fx.write_package(self.root, mutate_manifest=change)
        self.refused('content_sha256 does not match')


if __name__ == '__main__':
    unittest.main()
