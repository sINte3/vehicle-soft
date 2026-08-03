#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for tools/check_migration_drift.py -- TOOL-DRIFT-LEGACY-001.

Two groups:

  DriftSyntheticTests    -- a temporary root and a temporary database, fully
                            hermetic. Asserts both directions: a healthy tree
                            exits 0, real drift still exits 1, a legacy file
                            in the backfill map resolves, and a legacy file
                            absent from the map still fails. The last pair is
                            what proves the change narrows the noise rather
                            than widening the blind spot.

  DriftProductionFixtureTests -- the schema_migrations state production
                            printed on 2026-08-02, replayed against the real
                            repository root. Only the registry is synthetic;
                            the migrate_*.py files are the ones in the clone.

No real database is touched and nothing is written outside a temp dir.

Run:
  python tools/test_check_migration_drift.py
"""

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, 'check_migration_drift.py')
# [REASON]: the override exists so this suite can be pointed at a copy of the
# PRE-change checker while still scanning the real migrate_*.py files. A test
# that has never been seen to fail against the broken version is not evidence
# that it tests the fix, and for this tool the broken version passes the happy
# path. Unset in normal use, where it resolves to the repository root.
REPO_ROOT = os.environ.get('DRIFT_TEST_REPO_ROOT') or os.path.dirname(HERE)

sys.path.insert(0, HERE)

import check_migration_drift as drift  # noqa: E402  (path set up just above)

# The 15 registered-but-no-file names production reported on 2026-08-02.
PRODUCTION_LEGACY_REGISTERED = [
    'FUEL_012H_CARDS_DIRECTORY',
    'FUEL_IDX_001_FUEL_TRANSACTIONS2_INDEXES',
    'REPORT001E_FUEL_WARNING_REVIEWS',
    'migrate_000_migration_registry',
    'migrate_001_backfill_historical_registry',
    'migrate_add_wialon',
    'migrate_bot001_telegram_foundation',
    'migrate_bot003_outbox_v1',
    'migrate_equipment_excel',
    'migrate_fuel_v2',
    'migrate_module_permissions',
    'migrate_tasks_abc3',
    'migrate_to_v3',
    'migrate_to_v45',
    'migrate_v46',
]

# Of those, the ones migrate_001_backfill_historical_registry.py maps to a
# file that exists. Resolving these is the whole point of the change.
PRODUCTION_RESOLVED_BY_BACKFILL = [
    'migrate_001_backfill_historical_registry',
    'migrate_add_wialon',
    'migrate_equipment_excel',
    'migrate_fuel_v2',
    'migrate_module_permissions',
    'migrate_tasks_abc3',
    'migrate_to_v3',
    'migrate_to_v45',
    'migrate_v46',
]

# [REASON]: these six are NOT in the backfill map and must keep failing the
# run. Five of them do declare their registry name in their own source, but
# under MIGRATION_NAME / THIS_MIGRATION rather than MIGRATION_ID, and
# REPORT001E_FUEL_WARNING_REVIEWS has no file anywhere in the tree. Teaching
# the checker those aliases is a separate decision; matching them on filename
# stems is exactly the shortcut this test exists to prevent.
PRODUCTION_STILL_UNRESOLVED = [
    'FUEL_012H_CARDS_DIRECTORY',
    'FUEL_IDX_001_FUEL_TRANSACTIONS2_INDEXES',
    'REPORT001E_FUEL_WARNING_REVIEWS',
    'migrate_000_migration_registry',
    'migrate_bot001_telegram_foundation',
    'migrate_bot003_outbox_v1',
]

MODERN_TEMPLATE = textwrap.dedent('''
    """A migration written after the MIGRATION_ID convention."""
    MIGRATION_ID = "{migration_id}"
''')

LEGACY_TEMPLATE = textwrap.dedent('''
    """A migration written before the MIGRATION_ID convention.

    It carries no MIGRATION_ID constant, which is exactly why the backfill
    map is the only thing that can identify it.
    """
    THIS_MIGRATION = "{name}"
''')

BACKFILL_TEMPLATE = textwrap.dedent('''
    """Stand-in for migrate_001_backfill_historical_registry.py."""
    THIS_MIGRATION = 'migrate_001_backfill_historical_registry'

    CONFIRMED_MIGRATIONS = [
    {rows}
    ]
''')


def make_registry(db_path, names):
    con = sqlite3.connect(db_path)
    con.execute('CREATE TABLE schema_migrations ('
                'id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, '
                'applied_at TEXT, description TEXT, checksum TEXT)')
    con.executemany('INSERT INTO schema_migrations (name) VALUES (?)',
                    [(n,) for n in names])
    con.commit()
    con.close()


def run_tool(db_path, root, extra_env=None, extra_args=()):
    env = dict(os.environ)
    env.pop('PYTHONWARNINGS', None)
    if extra_env:
        env.update(extra_env)
    argv = [sys.executable]
    argv.extend(extra_args)
    argv.extend([TOOL, '--db', db_path, '--root', root])
    proc = subprocess.run(argv, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, env=env)
    return proc.returncode, proc.stdout


def section(output, title):
    """Return the bullet lines of one named section of the report."""
    lines = output.splitlines()
    items = []
    inside = False
    for line in lines:
        if line.startswith(title + ':'):
            inside = True
            continue
        if inside:
            if line.startswith('  - '):
                items.append(line[4:].strip())
            else:
                break
    return items


class DriftSyntheticTests(unittest.TestCase):
    """Hermetic: temp root, temp database, no dependency on the clone."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='check_drift_test_')
        self.root = os.path.join(self.tmp, 'repo')
        os.makedirs(self.root)
        self.db = os.path.join(self.tmp, 'transport.db')
        self.mapped = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- fixture helpers ---------------------------------------------------

    def add_modern(self, migration_id, filename):
        with open(os.path.join(self.root, filename), 'w',
                  encoding='ascii') as fh:
            fh.write(MODERN_TEMPLATE.format(migration_id=migration_id))

    def add_legacy(self, name, filename, in_map=True):
        with open(os.path.join(self.root, filename), 'w',
                  encoding='ascii') as fh:
            fh.write(LEGACY_TEMPLATE.format(name=name))
        if in_map:
            self.mapped.append((name, filename))

    def map_only(self, name, filename):
        """Add a map entry whose file is deliberately NOT created."""
        self.mapped.append((name, filename))

    def write_backfill(self):
        rows = '\n'.join(
            "        ('%s', '%s', 'desc')," % (n, f) for n, f in self.mapped)
        with open(os.path.join(
                self.root, 'migrate_001_backfill_historical_registry.py'),
                'w', encoding='ascii') as fh:
            fh.write(BACKFILL_TEMPLATE.format(rows=rows))

    def build(self, registered):
        self.write_backfill()
        make_registry(self.db, registered)
        return run_tool(self.db, self.root)

    # -- both directions ---------------------------------------------------

    def test_healthy_state_exits_zero(self):
        self.add_modern('SPARE_PARTS_STAGE9', 'migrate_spare_parts_stage9.py')
        self.add_legacy('migrate_to_v3', 'migrate_to_v3.py')
        code, out = self.build([
            'SPARE_PARTS_STAGE9',
            'migrate_to_v3',
            'migrate_001_backfill_historical_registry',
        ])
        self.assertEqual(code, 0, out)
        self.assertIn('OK: registry and working tree are consistent', out)

    def test_unregistered_migration_id_fails_and_is_named(self):
        """The classic drift must still be caught."""
        self.add_modern('SPARE_PARTS_STAGE9', 'migrate_spare_parts_stage9.py')
        code, out = self.build(['migrate_001_backfill_historical_registry'])
        self.assertEqual(code, 1, out)
        self.assertIn('DRIFT DETECTED', out)
        named = ' '.join(section(out, 'file-but-not-registered'))
        self.assertIn('SPARE_PARTS_STAGE9', named)
        self.assertIn('migrate_spare_parts_stage9.py', named)

    # -- the pair that proves noise narrowed, blind spot did not widen -----

    def test_legacy_file_in_the_map_is_resolved(self):
        self.add_legacy('migrate_fuel_v2', 'migrate_fuel_v2.py', in_map=True)
        code, out = self.build([
            'migrate_fuel_v2',
            'migrate_001_backfill_historical_registry',
        ])
        self.assertEqual(code, 0, out)
        resolved = section(out, 'resolved-by-backfill (legacy, pre-MIGRATION_ID)')
        self.assertIn('migrate_fuel_v2 -> migrate_fuel_v2.py', resolved)
        self.assertEqual(section(out, 'registered-but-no-file'), [])
        self.assertEqual(
            section(out, 'unclassifiable (no MIGRATION_ID constant)'), [])

    def test_legacy_file_absent_from_the_map_still_fails(self):
        self.add_legacy('migrate_mystery', 'migrate_mystery.py', in_map=False)
        code, out = self.build([
            'migrate_mystery',
            'migrate_001_backfill_historical_registry',
        ])
        self.assertEqual(code, 1, out)
        self.assertIn('migrate_mystery',
                      ' '.join(section(out, 'registered-but-no-file')))
        self.assertIn('migrate_mystery.py',
                      section(out, 'unclassifiable (no MIGRATION_ID constant)'))

    def test_mapped_file_whose_registry_row_is_missing_still_fails(self):
        """A legacy migration present on disk but never applied is drift."""
        self.add_legacy('migrate_fuel_v2', 'migrate_fuel_v2.py', in_map=True)
        code, out = self.build(['migrate_001_backfill_historical_registry'])
        self.assertEqual(code, 1, out)
        named = ' '.join(section(out, 'file-but-not-registered'))
        self.assertIn('migrate_fuel_v2', named)
        self.assertIn('legacy', named)
        resolved = ' '.join(
            section(out, 'resolved-by-backfill (legacy, pre-MIGRATION_ID)'))
        self.assertNotIn('migrate_fuel_v2', resolved)

    def test_map_pointing_at_a_missing_file_does_not_resolve(self):
        """The map is not taken on trust: the file has to be there."""
        self.map_only('migrate_deleted', 'migrate_deleted.py')
        code, out = self.build([
            'migrate_deleted',
            'migrate_001_backfill_historical_registry',
        ])
        self.assertEqual(code, 1, out)
        self.assertIn('migrate_deleted',
                      section(out, 'registered-but-no-file'))

    def test_absent_backfill_script_resolves_nothing(self):
        """Safe direction: without the map every legacy entry still fails."""
        self.add_legacy('migrate_fuel_v2', 'migrate_fuel_v2.py', in_map=True)
        make_registry(self.db, ['migrate_fuel_v2'])
        code, out = run_tool(self.db, self.root)   # backfill never written
        self.assertEqual(code, 1, out)
        self.assertIn('migrate_fuel_v2',
                      section(out, 'registered-but-no-file'))

    # -- the SyntaxWarning that polluted every run -------------------------

    def test_bad_escape_sequence_raises_no_syntax_warning(self):
        """migrate_add_wialon.py carries a '\\P' escape.

        Run under -W error::SyntaxWarning: without the catch_warnings() in
        parse_module the warning is promoted to an exception and the tool
        dies, so this fails loudly against the pre-change version.
        """
        path = os.path.join(self.root, 'migrate_badescape.py')
        with open(path, 'w', encoding='ascii') as fh:
            fh.write('MIGRATION_ID = "BAD_ESCAPE"\n')
            fh.write('PATTERN = "C:\\Program Files\\Python314"\n')
        make_registry(self.db, ['BAD_ESCAPE',
                                'migrate_001_backfill_historical_registry'])
        self.write_backfill()
        code, out = run_tool(self.db, self.root,
                             extra_args=['-W', 'error::SyntaxWarning'])
        self.assertNotIn('SyntaxWarning', out)
        self.assertNotIn('Traceback', out)
        self.assertEqual(code, 0, out)


class DriftProductionFixtureTests(unittest.TestCase):
    """The 2026-08-02 production registry, replayed against the real clone.

    Only schema_migrations is synthetic. --root is the repository root, so
    the migrate_*.py files are the real ones -- copying 38 files around to
    say the same thing would only add a way for the fixture to drift.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='check_drift_prod_')
        self.db = os.path.join(self.tmp, 'transport.db')
        # [REASON]: the MIGRATION_ID half of the registry is derived from the
        # tree rather than frozen at the 17 rows of 2026-08-02. Freezing it
        # would turn this fixture into a trap for the next track that adds a
        # migration -- its file would show up as file-but-not-registered and
        # fail CI for a change that is perfectly correct. The legacy half is
        # what this fixture is actually about, and that half is verbatim.
        id_to_file, self.raw_unclassifiable = drift.scan_migration_files(
            REPO_ROOT)
        registered = sorted(set(PRODUCTION_LEGACY_REGISTERED) | set(id_to_file))
        make_registry(self.db, registered)
        self.registry_size = len(registered)
        self.code, self.out = run_tool(self.db, REPO_ROOT)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fixture_carries_the_fifteen_recorded_legacy_names(self):
        """On 2026-08-02 the registry held 32 rows: these 15 plus 17 files.

        The 15 are asserted to be genuinely legacy -- none of them is a
        MIGRATION_ID claimed by a file, which is what made them show up as
        registered-but-no-file in the first place.
        """
        self.assertEqual(len(PRODUCTION_LEGACY_REGISTERED), 15)
        id_to_file, _ = drift.scan_migration_files(REPO_ROOT)
        overlap = set(PRODUCTION_LEGACY_REGISTERED) & set(id_to_file)
        self.assertEqual(overlap, set(),
                         'these names must not be claimed by any MIGRATION_ID')
        self.assertEqual(self.registry_size, 15 + len(id_to_file))

    def test_the_real_signal_is_clean(self):
        """file-but-not-registered was 0 after the release. It still is."""
        self.assertEqual(section(self.out, 'file-but-not-registered'), [])

    def test_backfill_map_resolves_the_nine_legacy_pairs(self):
        resolved = section(
            self.out, 'resolved-by-backfill (legacy, pre-MIGRATION_ID)')
        self.assertEqual(len(resolved), len(PRODUCTION_RESOLVED_BY_BACKFILL))
        names = [item.split(' -> ')[0] for item in resolved]
        self.assertEqual(sorted(names), sorted(PRODUCTION_RESOLVED_BY_BACKFILL))

    def test_noise_drops_from_fifteen_to_six(self):
        remaining = section(self.out, 'registered-but-no-file')
        self.assertEqual(sorted(remaining), sorted(PRODUCTION_STILL_UNRESOLVED))
        self.assertEqual(len(PRODUCTION_LEGACY_REGISTERED), 15)
        self.assertEqual(len(remaining), 6)

    def test_unclassifiable_shrinks_by_exactly_the_resolved_pairs(self):
        """21 -> 12 on 2026-08-02: nine files identified by the map.

        Expressed as a difference rather than the literal 12 so that adding a
        migration later cannot make this fail for the wrong reason.
        """
        remaining = section(
            self.out, 'unclassifiable (no MIGRATION_ID constant)')
        self.assertEqual(
            len(remaining),
            len(self.raw_unclassifiable) - len(PRODUCTION_RESOLVED_BY_BACKFILL))
        for resolved_file in ('migrate_to_v3.py', 'migrate_fuel_v2.py',
                              'migrate_add_wialon.py'):
            self.assertNotIn(resolved_file, remaining)

    def test_unmapped_files_are_never_resolved_by_filename_stem(self):
        """The entries the task warns against engineering to zero."""
        remaining = section(
            self.out, 'unclassifiable (no MIGRATION_ID constant)')
        for never in ('migrate_categories_v9.py', 'migrate_equipment.py',
                      'migrate_sec003a_real.py', 'migrate_v42.py',
                      'migrate_v47.py', 'migrate_worktypes.py'):
            self.assertIn(never, remaining)

    def test_still_exits_non_zero_because_six_entries_remain(self):
        self.assertEqual(self.code, 1, self.out)
        self.assertIn('DRIFT DETECTED', self.out)

    def test_output_is_ascii_only(self):
        self.out.encode('ascii')


if __name__ == '__main__':
    unittest.main(verbosity=2)
