#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/check_migration_drift.py -- DEPLOY-DRIFT-001 (P1).

Compares the schema_migrations registry inside a SQLite database against
the migrate_*.py files present in the working tree. Production once ran
for a week missing three migrations and nothing reported it, because
db.create_all() silently creates missing tables empty and the app starts
cleanly while columns, indexes and seed rows are absent.

What it reports, as three lists:
  1. registered-but-no-file  -- names recorded in schema_migrations that no
     migrate_*.py in the tree claims via its MIGRATION_ID constant;
  2. file-but-not-registered -- migrate_*.py files whose MIGRATION_ID is
     not recorded in schema_migrations (the classic drift);
  3. unclassifiable          -- migrate_*.py files with no module-level
     MIGRATION_ID constant. Reported, never silently ignored: an earlier
     version of this check assumed a migrate_NNN_ prefix and missed six
     migrations; naming here is not uniform (migrate_fuel_counter_mismatch,
     migrate_spare_parts_stage1, ...), so every migrate_*.py counts.

plus one informational section:
  4. resolved-by-backfill    -- legacy pairs that predate the MIGRATION_ID
     convention (TOOL-DRIFT-LEGACY-001). Visible, but they do not fail the
     run.

TOOL-DRIFT-LEGACY-001
  On production this ended with DRIFT DETECTED on every single run while
  nothing was wrong. The real signal -- file-but-not-registered -- moved
  exactly as predicted across the v1.11 release, 0 -> 1 -> 0. The noise came
  from 15 registered-but-no-file and 21 unclassifiable entries: migrations
  that predate the MIGRATION_ID convention and were registered by
  migrate_001_backfill_historical_registry.py, which carries a ready
  name-to-file mapping table.

  An operator who sees DRIFT DETECTED on every run learns to ignore it, and
  the one time it means something it will be ignored too. So the fix is in
  this checker, not in 21 old migrations.

  A legacy pair is resolved only when ALL of these hold:
    - the backfill script maps the registered name to a file;
    - that file exists at --root;
    - the name really is in schema_migrations.
  A mapped file whose registry row is missing is NOT resolved -- it moves to
  file-but-not-registered, because a legacy migration present on disk but
  never applied is exactly the drift this tool exists to catch. Anything the
  map does not mention keeps its previous treatment and still fails the run:
  the goal is a quiet tool, not a permissive one.

Exit code 1 if any of the three drift lists is non-empty, 0 when registry
and tree agree, 2 on usage errors. Output is ASCII only.

Safety:
  - the database is opened read-only via a file: URI with mode=ro. NEVER
    immutable=1 for a live database -- immutable tells SQLite that no other
    process can modify the file, which is false while the service runs and
    corrupts reads; immutable=1 is correct only for a static backup copy;
  - files are parsed with ast; no migration module is imported or executed,
    and nothing imports app (create_app() calls db.create_all() -- that
    would turn a diagnostic into a writer).

Usage (run from anywhere; --root defaults to the repository root):
  python tools/check_migration_drift.py --db instance/transport.db
  python tools/check_migration_drift.py --db D:\\backups\\transport.db --root C:\\transport-report
"""

import argparse
import ast
import glob
import os
import sqlite3
import sys
import warnings

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# [REASON]: the one script that knows which pre-MIGRATION_ID registry names
# belong to which file. Its mapping is read, never guessed from filename
# stems -- several files (migrate_categories_v9, migrate_v42, migrate_v47,
# migrate_worktypes, ...) look like they should pair with a registered name
# and deliberately do not, and REPORT001E_FUEL_WARNING_REVIEWS has no file at
# all. Only the map settles it.
BACKFILL_FILE = 'migrate_001_backfill_historical_registry.py'


def _ascii(text):
    return str(text).encode('ascii', 'backslashreplace').decode('ascii')


def read_registered_names(db_path):
    """Return the set of names in schema_migrations, opening the db read-only.

    Raises RuntimeError with an explanation when the file or the table is
    missing -- both mean the comparison cannot be made.
    """
    if not os.path.exists(db_path):
        raise RuntimeError('database not found at %s' % db_path)
    # [REASON]: mode=ro refuses to create a missing file and refuses writes;
    # sqlite3.connect(path) without the URI would CREATE an empty database.
    uri = 'file:%s?mode=ro' % db_path.replace('\\', '/').replace('?', '%3f')
    con = sqlite3.connect(uri, uri=True)
    try:
        cur = con.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name='schema_migrations'")
        if cur.fetchone() is None:
            raise RuntimeError(
                'schema_migrations table does not exist in %s -- run '
                'migrate_000_migration_registry.py first' % db_path)
        cur.execute('SELECT name FROM schema_migrations')
        return {row[0] for row in cur.fetchall()}
    finally:
        con.close()


def parse_module(path):
    """Return the ast for path, or None when it does not parse.

    Parses with ast only -- the module is never imported, so a migration
    script cannot run as a side effect of this check.
    """
    with open(path, 'r', encoding='utf-8', errors='replace') as fh:
        source = fh.read()
    # [REASON]: migrate_add_wialon.py contains a '\P' escape and raises
    # SyntaxWarning on every parse, which polluted every run of this tool.
    # Suppressed here rather than by editing the migration: migrate_*.py
    # files are applied history and are not touched to quieten a diagnostic.
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', SyntaxWarning)
        try:
            return ast.parse(source, filename=path)
        except SyntaxError:
            return None


def _string_constant(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def read_backfill_map(root):
    """Return {registry_name: filename} as declared by the backfill script.

    Empty when the script is absent -- in that case nothing is resolved and
    every legacy entry keeps failing the run, which is the safe direction.
    """
    path = os.path.join(root, BACKFILL_FILE)
    if not os.path.exists(path):
        return {}
    tree = parse_module(path)
    if tree is None:
        return {}
    mapping = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            if target.id == 'CONFIRMED_MIGRATIONS':
                if not isinstance(node.value, (ast.List, ast.Tuple)):
                    continue
                for element in node.value.elts:
                    if not isinstance(element, (ast.Tuple, ast.List)):
                        continue
                    if len(element.elts) < 2:
                        continue
                    name = _string_constant(element.elts[0])
                    filename = _string_constant(element.elts[1])
                    if name and filename:
                        mapping[name] = filename
            elif target.id == 'THIS_MIGRATION':
                # [REASON]: the backfill records itself too, with
                # migration_checksum(os.path.abspath(__file__)) -- so the
                # pair (THIS_MIGRATION -> its own filename) is declared by
                # the script just as explicitly as the table entries, and
                # leaving it out would strand one real legacy row.
                name = _string_constant(node.value)
                if name:
                    mapping[name] = BACKFILL_FILE
    return mapping


def extract_migration_id(path):
    """Return the module-level MIGRATION_ID string constant, or None."""
    tree = parse_module(path)
    if tree is None:
        return None
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Name) and target.id == 'MIGRATION_ID':
                value = node.value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    return value.value
    return None


def scan_migration_files(root):
    """Return (id_to_file, unclassifiable) for migrate_*.py at root.

    The glob is deliberately broad: naming is not uniform in this project
    (no migrate_NNN_ prefix convention), so every migrate_*.py at the
    repository root is considered.
    """
    id_to_file = {}
    unclassifiable = []
    for path in sorted(glob.glob(os.path.join(root, 'migrate_*.py'))):
        name = os.path.basename(path)
        migration_id = extract_migration_id(path)
        if migration_id is None:
            unclassifiable.append(name)
        else:
            id_to_file.setdefault(migration_id, []).append(name)
    return id_to_file, unclassifiable


def _print_list(title, items):
    print('%s: %d' % (title, len(items)))
    for item in items:
        print('  - %s' % _ascii(item))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Compare schema_migrations against migrate_*.py files.')
    parser.add_argument('--db', required=True,
                        help='path to the SQLite database file')
    parser.add_argument('--root', default=REPO_ROOT,
                        help='directory holding migrate_*.py '
                             '(default: repository root)')
    args = parser.parse_args(argv)

    if not os.path.isdir(args.root):
        print('ERROR: root directory not found: %s' % _ascii(args.root))
        return 2

    try:
        registered = read_registered_names(args.db)
    except (RuntimeError, sqlite3.Error) as exc:
        print('ERROR: %s' % _ascii(exc))
        return 2

    id_to_file, unclassifiable = scan_migration_files(args.root)
    file_ids = set(id_to_file)

    registered_but_no_file = set(registered - file_ids)
    file_but_not_registered = [
        '%s (%s)' % (mid, ', '.join(id_to_file[mid]))
        for mid in file_ids - registered]
    unclassifiable = list(unclassifiable)

    # [REASON]: legacy pairs are resolved only when the map names the file,
    # the file is really there AND the row is really registered. Dropping any
    # one of those conditions would let a legacy migration that is present but
    # never applied pass silently -- which widens the blind spot instead of
    # narrowing the noise.
    backfill_map = read_backfill_map(args.root)
    resolved_legacy = []
    for name in sorted(backfill_map):
        filename = backfill_map[name]
        if not os.path.exists(os.path.join(args.root, filename)):
            # The map points at a file that is gone: keep the registered name
            # in registered-but-no-file, where it is already counted.
            continue
        if name in registered:
            resolved_legacy.append((name, filename))
            registered_but_no_file.discard(name)
        else:
            file_but_not_registered.append(
                '%s (%s) [legacy, via backfill map]' % (name, filename))
        if filename in unclassifiable:
            unclassifiable.remove(filename)

    registered_but_no_file = sorted(registered_but_no_file)
    file_but_not_registered = sorted(file_but_not_registered)

    print('database : %s' % _ascii(args.db))
    print('root     : %s' % _ascii(args.root))
    print('registered migrations: %d; migrate_*.py files: %d '
          '(%d with MIGRATION_ID)'
          % (len(registered),
             len(unclassifiable) + len(resolved_legacy)
             + sum(len(v) for v in id_to_file.values()),
             sum(len(v) for v in id_to_file.values())))
    print('backfill map: %d name-to-file pairs from %s'
          % (len(backfill_map), BACKFILL_FILE))
    print('')
    _print_list('resolved-by-backfill (legacy, pre-MIGRATION_ID)',
                ['%s -> %s' % pair for pair in resolved_legacy])
    _print_list('registered-but-no-file', registered_but_no_file)
    _print_list('file-but-not-registered', file_but_not_registered)
    _print_list('unclassifiable (no MIGRATION_ID constant)', unclassifiable)
    print('')

    if registered_but_no_file or file_but_not_registered or unclassifiable:
        print('DRIFT DETECTED: at least one list above is non-empty.')
        print('(resolved-by-backfill is informational and never fails a run.)')
        return 1
    print('OK: registry and working tree are consistent.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
