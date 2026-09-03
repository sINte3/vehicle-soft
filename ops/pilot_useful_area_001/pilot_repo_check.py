# -*- coding: utf-8 -*-
"""ops/pilot_useful_area_001/pilot_repo_check.py -- ревизии и исполняемые байты.

DRONE-USEFUL-AREA-PILOT-001. Две подкоманды:

  verify      -- какая ревизия стоит в чекауте, чисто ли рабочее дерево и
                 совпадают ли blob-хеши исполняемых файлов с манифестом;
  materialize -- достать файлы ИЗ ИСТОРИИ (`git cat-file blob`) на диск,
                 проверив хеш каждого.

Почему materialize, а не копирование. Копия, совпавшая с файлом рабочего
дерева, доказывает аккуратность копирования и ничего больше: дерево могло
уехать, файл могли поправить руками, чекаут мог стоять не на той ревизии.
`git cat-file blob <sha>:<path>` выдаёт байты, записанные в коммите, а их
локально посчитанный хеш привязывает исполняемое к ревизии.

Байты пишутся ДВОИЧНО. Ни одной перекодировки по дороге: PowerShell 5.1
перенаправлением `>` пишет UTF-16LE, и файл, «скопированный» так, отличался
бы от коммита каждым байтом, оставаясь при этом читаемым глазом.

Запуск:

  python pilot_repo_check.py verify --repo C:\\transport-report-staging --expect-sha <product sha> --role product --run-id <id> --kit-sha <sha> --out <file>
  python pilot_repo_check.py materialize --repo C:\\transport-report-staging --rev <sha> --into <dir> --file migrate_drones_useful_area_001.py --file migration_utils.py

Коды возврата: 0 -- сходится; 1 -- ошибка; 3 -- проверка провалена.
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import pilot_common as common  # noqa: E402

ROLES = ('product', 'kit', 'collector')


def verify(repo, expect_sha, role, require_clean=True, manifest=None):
    """Ревизия, чистота и blob-хеши исполняемых файлов этого чекаута."""
    manifest = manifest or common.load_product_blobs()
    head = common.head_sha(repo)
    clean = common.worktree_is_clean(repo)

    problems = []
    if expect_sha and head != expect_sha:
        problems.append('HEAD_IS_NOT_THE_EXPECTED_REVISION')
    if require_clean and not clean:
        problems.append('WORKTREE_IS_DIRTY')

    # Какие файлы обязаны совпасть, зависит от роли чекаута.
    #
    # [REASON]: сборщик на BAK-TEX11 работает на ревизии КОМПЛЕКТА, а не
    # продукта: ревизия комплекта добавила в сводку сбора два числа, без
    # которых полноту захвата приходилось выводить из равенства счётчиков.
    # Площадка при этом остаётся на проверенной ревизии продукта. Разница
    # объявлена в манифесте и проверяется, а не подразумевается.
    checked = {}
    identical = manifest['identical_on_both_revisions']
    differs = manifest['kit_differs_on_purpose']

    if role in ('product', 'kit'):
        for path, entry in sorted(identical.items()):
            expected = entry['product_blob']
            checked[path] = _compare_blob(repo, head, path, expected, problems)
    if role in ('kit', 'collector'):
        for path, entry in sorted(differs.items()):
            checked[path] = _compare_blob(repo, head, path, entry['kit_blob'],
                                          problems)
    if role == 'collector':
        for path, entry in sorted(identical.items()):
            checked[path] = _compare_blob(repo, head, path,
                                          entry['product_blob'], problems)

    return {
        'repo_role': role,
        'head_sha': head,
        'expected_sha': expect_sha,
        'head_is_expected': bool(expect_sha) and head == expect_sha,
        'worktree_clean': clean,
        'blobs_checked': checked,
        'blobs_all_match': all(item['matches'] for item in checked.values()),
        'problems': problems,
        'passed': not problems,
    }


def _compare_blob(repo, rev, path, expected, problems):
    """Сверить blob и в ИСТОРИИ, и НА ДИСКЕ.

    [REASON]: два разных вопроса. `git rev-parse <rev>:<path>` отвечает, что
    записано в коммите; хеш файла на диске отвечает, что будет исполнено.
    Совпадение обоих с манифестом -- это и есть «исполняемый файл доказуемо
    получен из проверенной ревизии». Чистое рабочее дерево делает их равными
    по построению, но проверка не имеет права опираться на «по построению»:
    она для того и существует, чтобы поймать случай, когда это неверно.
    """
    entry = {'expected': expected, 'in_history': None, 'on_disk': None,
             'matches': False}
    try:
        entry['in_history'] = common.blob_sha_at(repo, rev, path)
    except common.ProbeError as exc:
        problems.append('BLOB_UNREADABLE:%s' % path)
        entry['error'] = str(exc)[:120]
        return entry

    full = os.path.join(str(repo), path.replace('/', os.sep))
    if os.path.exists(full):
        entry['on_disk'] = common.file_blob_sha(full)
    else:
        problems.append('FILE_ABSENT:%s' % path)

    entry['matches'] = (entry['in_history'] == expected
                        and entry['on_disk'] == expected)
    if entry['in_history'] != expected:
        problems.append('BLOB_MISMATCH_IN_HISTORY:%s' % path)
    elif entry['on_disk'] != expected:
        problems.append('BLOB_MISMATCH_ON_DISK:%s' % path)
    return entry


def materialize(repo, rev, into, paths, manifest=None):
    """Достать файлы из истории на диск, сверив каждый с манифестом."""
    manifest = manifest or common.load_product_blobs()
    expectations = {}
    for path, entry in manifest['identical_on_both_revisions'].items():
        expectations[path] = entry['product_blob']
    for path, entry in manifest['kit_differs_on_purpose'].items():
        expectations.setdefault(path, entry['product_blob'])

    written = {}
    for path in paths:
        expected = expectations.get(path)
        if expected is None:
            raise common.ProbeError('%s is not in the blob manifest; the kit '
                                    'never materializes a file it cannot '
                                    'pin to a revision' % path)
        destination = os.path.join(into, os.path.basename(path))
        actual = common.materialize_blob(repo, rev, path, destination,
                                         expected_blob=expected)
        on_disk = common.file_blob_sha(destination)
        if on_disk != expected:
            raise common.ProbeError('%s landed on disk as blob %s, expected %s'
                                    % (destination, on_disk, expected))
        written[path] = {'destination': os.path.abspath(destination),
                         'blob': actual, 'bytes': os.path.getsize(destination)}
    return {'revision': rev, 'materialized': written,
            'files': len(written), 'passed': True}


def build_parser():
    parser = argparse.ArgumentParser(
        prog='pilot_repo_check.py',
        description='DRONE-USEFUL-AREA-PILOT-001: prove which revision a '
                    'checkout is at and materialize executables from it.')
    parser.add_argument('command', choices=('verify', 'materialize'))
    parser.add_argument('--repo', required=True, metavar='PATH')
    parser.add_argument('--expect-sha', metavar='SHA')
    parser.add_argument('--role', choices=ROLES, default='product')
    parser.add_argument('--allow-dirty', action='store_true',
                        help='verify: do not fail on a dirty working tree')
    parser.add_argument('--rev', metavar='SHA',
                        help='materialize: the revision to read blobs from')
    parser.add_argument('--into', metavar='DIR',
                        help='materialize: where the files land')
    parser.add_argument('--file', action='append', default=[],
                        metavar='PATH', dest='files',
                        help='materialize: repository-relative path')
    parser.add_argument('--run-id', required=True, metavar='ID')
    parser.add_argument('--kit-sha', required=True, metavar='SHA')
    parser.add_argument('--out', metavar='PATH')
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == 'verify':
            payload = verify(args.repo, args.expect_sha, args.role,
                             require_clean=not args.allow_dirty)
            kind = 'repo:verify:%s' % args.role
        else:
            if not (args.rev and args.into and args.files):
                sys.stderr.write('ERROR: materialize needs --rev, --into and '
                                 'at least one --file\n')
                return common.EXIT_ERROR
            payload = materialize(args.repo, args.rev, args.into, args.files)
            kind = 'repo:materialize'
    except common.ProbeError as exc:
        sys.stderr.write('ERROR: %s\n' % exc)
        return common.EXIT_ERROR
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write('ERROR: %s: %s\n' % (type(exc).__name__, exc))
        return common.EXIT_ERROR

    document = common.evidence_envelope(kind, payload, args.run_id,
                                        args.kit_sha)
    common.emit(document, args.out)
    if not payload.get('passed'):
        for problem in payload.get('problems', ()):
            sys.stderr.write('CHECK FAILED: %s\n' % problem)
        return common.EXIT_CHECK_FAILED
    return common.EXIT_OK


if __name__ == '__main__':
    sys.exit(main())
