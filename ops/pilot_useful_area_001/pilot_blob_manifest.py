# -*- coding: utf-8 -*-
"""ops/pilot_useful_area_001/pilot_blob_manifest.py -- какие байты исполняются.

DRONE-USEFUL-AREA-PILOT-001. Список файлов, которые комплект ЗАПУСКАЕТ, с
их git-blob-хешами на проверенной ревизии продукта. Скрипты материализуют эти
файлы из истории (`git cat-file blob`) и сверяют хеш с этим списком.

Почему не сравнение копии с оригиналом. Копия, совпавшая с файлом рабочего
дерева, доказывает аккуратность копирования и ничего больше: рабочее дерево
могло уехать, файл могли поправить руками, чекаут мог стоять не на той
ревизии. Хеш blob-а привязывает исполняемые байты к КОММИТУ.

Две ревизии, и разница между ними названа явно:

* восемь файлов побайтово ОДИНАКОВЫ на `PRODUCT_SHA` и на ревизии комплекта --
  значит приборы, импортирующие расчёт из чекаута комплекта, и сборщик на
  BAK-TEX11, работающий на ревизии комплекта, исполняют ровно тот код,
  который стоит на площадке;
* список намеренных различий (`kit_differs_on_purpose`) с 2026-09-05 ПУСТ.
  Пока площадка стояла на `c3e6a12`, `drone_collector/main.py` различался
  намеренно: ревизия комплекта добавила в сводку сбора два числа
  (`probe_request_failures`, `probe_pending_requests`), без которых полноту
  живого захвата приходилось выводить из равенства счётчиков, а вывод был
  неверен. Ревизия продукта `useful-area-v2` это изменение уже содержит.
  Механизм оставлен: следующее намеренное различие записывается сюда, а не
  подразумевается.

Запуск:

  python ops/pilot_useful_area_001/pilot_blob_manifest.py --check
  python ops/pilot_useful_area_001/pilot_blob_manifest.py --write

Коды возврата: 0 -- сходится; 1 -- расхождение или ошибка.
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import pilot_common as common  # noqa: E402

# Файлы, которые комплект исполняет или импортирует. Каждый обязан быть
# доказуемо взят из ревизии, а не из рабочего дерева.
PRODUCT_FILES = (
    'migrate_drones_useful_area_001.py',
    'migration_utils.py',
    'backup_transport_db.py',
    'tools/recalculate_drone_useful_area.py',
    'drone_coverage_recalc.py',
    'drone_useful_area.py',
    'drone_collector/area_study.py',
    'drone_collector/main.py',
)

# Файлы, которые у комплекта СВОИ и отличаются от продукта осознанно. Пусто с
# тех пор, как ревизия продукта содержит сводку сбора комплекта (см. выше).
KIT_ONLY_FILES = ()

# Сам комплект. Его скрипты и приборы -- тоже исполняемое, и происхождение у
# них должно быть доказано ровно так же.
#
# [REASON]: первая редакция манифеста покрывала чужой код и молчала о своём.
# Комплект доказывал, что миграция взята из проверенной ревизии, и при этом
# ничего не говорил о том, из какой ревизии взят он сам.
KIT_OWN_DIR = 'ops/pilot_useful_area_001'
KIT_OWN_SUFFIXES = ('.ps1', '.psm1', '.py')
KIT_OWN_EXCLUDED = ('PRODUCT_BLOBS.json',)


def build_kit_own_files(repo=REPO_ROOT):
    """Файлы самого комплекта и их хеши глазами git.

    [REASON]: отдельной функцией, потому что истории здесь не нужно вовсе --
    хешируется рабочее дерево. `build()` без истории не работает (в мелком
    клоне CI `PRODUCT_SHA` недостижим), и тест сборки манифеста падал именно
    на этом, хотя проверял он совсем другое.
    """
    own = {}
    for name in sorted(os.listdir(os.path.join(repo, KIT_OWN_DIR))):
        if name in KIT_OWN_EXCLUDED:
            continue
        if not name.endswith(KIT_OWN_SUFFIXES):
            continue
        path = '%s/%s' % (KIT_OWN_DIR, name)
        own[path] = common.worktree_blob_sha(repo, path)
    return own


def build(repo=REPO_ROOT, product_sha=None):
    """Собрать манифест: blob продукта из ИСТОРИИ, blob комплекта -- с ДИСКА.

    [REASON]: `kit_blob` не берётся из ревизии. Ревизии комплекта в момент
    сборки манифеста ещё не существует -- манифест уезжает в тот же коммит,
    который её создаёт. Хеш файла рабочего дерева и есть то, что будет
    закоммичено, поэтому берётся он.
    """
    product_sha = product_sha or common.PRODUCT_SHA

    def kit_blob(path):
        # Глазами git: на Windows рабочая копия лежит с CRLF.
        return common.worktree_blob_sha(repo, path)

    files = {}
    for path in PRODUCT_FILES:
        files[path] = {
            'product_blob': common.blob_sha_at(repo, product_sha, path),
            'kit_blob': kit_blob(path),
        }
    differs = {}
    for path in KIT_ONLY_FILES:
        differs[path] = {
            'product_blob': common.blob_sha_at(repo, product_sha, path),
            'kit_blob': kit_blob(path),
        }

    own = build_kit_own_files(repo)
    return {
        'kit': common.KIT_ID,
        'kit_version': common.KIT_VERSION,
        'product_sha': product_sha,
        'identical_on_both_revisions': files,
        'kit_differs_on_purpose': differs,
        'kit_own_files': own,
    }


def check_against_worktree(manifest, repo=REPO_ROOT):
    """Сверить манифест с файлами рабочего дерева.

    Работает и в мелком клоне, где `PRODUCT_SHA` недостижим: хешируется то,
    что лежит на диске. Для семи одинаковых файлов это ТА ЖЕ проверка --
    их blob на обеих ревизиях один.
    """
    problems = []
    for path, entry in sorted(manifest['identical_on_both_revisions'].items()):
        if entry['product_blob'] != entry['kit_blob']:
            problems.append('NOT_IDENTICAL:%s' % path)
        full = os.path.join(repo, path.replace('/', os.sep))
        if not os.path.exists(full):
            problems.append('MISSING:%s' % path)
            continue
        if common.worktree_blob_sha(repo, path) != entry['kit_blob']:
            problems.append('WORKTREE_DIFFERS:%s' % path)
    for path, blob in sorted(manifest.get('kit_own_files', {}).items()):
        full = os.path.join(repo, path.replace('/', os.sep))
        if not os.path.exists(full):
            problems.append('MISSING:%s' % path)
            continue
        if common.worktree_blob_sha(repo, path) != blob:
            problems.append('WORKTREE_DIFFERS:%s' % path)
    expected_own = set()
    for name in sorted(os.listdir(os.path.join(repo, KIT_OWN_DIR))):
        if name in KIT_OWN_EXCLUDED or not name.endswith(KIT_OWN_SUFFIXES):
            continue
        expected_own.add('%s/%s' % (KIT_OWN_DIR, name))
    # [REASON]: новый файл комплекта, не попавший в манифест, исполнялся бы
    # без единого доказательства происхождения. Манифест обязан покрывать
    # ВЕСЬ комплект, а не тот его состав, который был на момент записи.
    for path in sorted(expected_own - set(manifest.get('kit_own_files', {}))):
        problems.append('NOT_IN_MANIFEST:%s' % path)

    for path, entry in sorted(manifest['kit_differs_on_purpose'].items()):
        if entry['product_blob'] == entry['kit_blob']:
            problems.append('DECLARED_DIFFERENT_BUT_IDENTICAL:%s' % path)
        full = os.path.join(repo, path.replace('/', os.sep))
        if os.path.exists(full):
            if common.worktree_blob_sha(repo, path) != entry['kit_blob']:
                problems.append('WORKTREE_DIFFERS:%s' % path)
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(prog='pilot_blob_manifest.py')
    parser.add_argument('--write', action='store_true',
                        help='regenerate PRODUCT_BLOBS.json from git')
    parser.add_argument('--check', action='store_true',
                        help='verify the stored manifest against the worktree')
    parser.add_argument('--repo', default=REPO_ROOT)
    args = parser.parse_args(argv)

    if args.write:
        manifest = build(args.repo)
        path = common.write_evidence(common.PRODUCT_BLOBS_FILE, manifest)
        print('WROTE %s' % path)
        return common.EXIT_OK

    manifest = common.load_product_blobs()
    problems = check_against_worktree(manifest, args.repo)
    for problem in problems:
        sys.stderr.write('BLOB MANIFEST: %s\n' % problem)
    if problems:
        return common.EXIT_ERROR
    print('BLOB_MANIFEST=OK (%d identical, %d deliberately different, '
          '%d kit files)'
          % (len(manifest['identical_on_both_revisions']),
             len(manifest['kit_differs_on_purpose']),
             len(manifest.get('kit_own_files', {}))))
    return common.EXIT_OK


if __name__ == '__main__':
    sys.exit(main())
