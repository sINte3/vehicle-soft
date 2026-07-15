# -*- coding: utf-8 -*-
"""RE-SP-001: spare parts attachment reconciliation tooling.

Three strictly separated modes -- the default is ALWAYS read-only:

1. REPORT (default, always safe, any time incl. business hours):
     python spare_parts_attachment_reconcile.py ^
       --db instance\\transport.db ^
       --uploads instance\\uploads\\spare_parts ^
       --out C:\\reconcile\\attachment_report.json
   Opens the database with a SQLite mode=ro URI, lists every
   spare_part_attachments row (present / MISSING on disk, size, SHA-256,
   mtime, issued-request flag) and every orphan file (on disk, no DB row),
   writes a machine-readable JSON report to --out and prints a human
   summary. Writes NOTHING else -- not to the DB, not to the uploads dir.

2. MANIFEST DRY-RUN (read-only, default even when a manifest is supplied):
     ... --manifest C:\\reconcile\\manifest.json --out C:\\reconcile\\plan.json
   Validates every action in the owner-authored manifest and writes the
   would-be plan. No filesystem change of any kind.

3. MANIFEST APPLY (guarded, owner-approved, non-destructive):
     ... --manifest C:\\reconcile\\manifest.json --apply --out C:\\reconcile\\result.json
   Executes the manifest ONLY if every action validates (all-or-nothing).
   The only operations that exist:
     restore    -- copy a file from an explicitly supplied backup path into
                   the uploads dir, ONLY after its size matches the DB row's
                   recorded file_size (and its SHA-256, if the manifest
                   provides one). Never fabricates, never overwrites.
     quarantine -- MOVE (never delete) an orphan file into
                   <uploads>/_quarantine/. Reversible; the audit entry
                   records the exact rollback command.
     retain     -- record an orphan in the persistent known-orphans registry
                   so future reports stop flagging it, moving nothing.
   Every applied action appends an audit entry (what, when, hashes,
   manifest reference, rollback note) to <uploads>/_reconcile/apply_audit.jsonl.

[REASON]: non-negotiable data-safety rule of SPARE-PARTS-P0-RELEASE-BLOCKERS:
there is NO delete path anywhere in this tool, the DB connection is ALWAYS
read-only (mode=ro URI -- same pattern as the audit's own diagnostics), rows
tied to an ISSUED request are immutable evidence (only
restore-from-identical-backup is possible for them, which the size/hash
validation enforces structurally), and orphans are never linked to a request
beyond what the deterministic "<item_id>_<8 hex>.<ext>" filename scheme
itself encodes.

Manifest format (JSON, authored and approved by the owner):

  {
    "manifest_version": 1,
    "approved_by": "owner name",
    "actions": [
      {"action": "restore",    "attachment_id": 1,
       "backup_path": "D:\\\\backup\\\\4_8f818b0c.jpg",
       "sha256": "<optional expected hex digest>"},
      {"action": "quarantine", "file": "12_deadbeef.jpg"},
      {"action": "retain",     "file": "13_cafebabe.jpg",
       "note": "why this orphan is kept in place"}
    ]
  }

The --out path must lie OUTSIDE the repository working tree (generated
evidence is never committed to git); the tool refuses otherwise.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

QUARANTINE_DIRNAME = '_quarantine'
STATE_DIRNAME = '_reconcile'
REGISTRY_BASENAME = 'known_orphans.json'
AUDIT_BASENAME = 'apply_audit.jsonl'

# [REASON]: the deterministic on-disk naming scheme from
# spare_parts._store_item_photo: '<item_id>_<uuid4 hex[:8]>.<ext>'. Orphan
# context is inferred ONLY from this -- no guessing beyond the filename.
FILENAME_RE = re.compile(r'^(\d+)_[0-9a-f]{8}\.[A-Za-z0-9]+$')

ATTACHMENT_SQL = """
    SELECT a.id, a.item_id, a.file_path, a.original_filename, a.file_size,
           a.uploaded_at, i.request_id, r.status
    FROM spare_part_attachments a
    LEFT JOIN spare_part_request_items i ON i.id = a.item_id
    LEFT JOIN spare_part_requests r ON r.id = i.request_id
    ORDER BY a.id
"""

ITEM_CONTEXT_SQL = """
    SELECT i.request_id, r.status
    FROM spare_part_request_items i
    LEFT JOIN spare_part_requests r ON r.id = i.request_id
    WHERE i.id = ?
"""


def _now_iso():
    return datetime.utcnow().isoformat(timespec='seconds') + 'Z'


def _sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _connect_ro(db_path):
    """Read-only SQLite connection -- the tool NEVER writes to the database."""
    if not os.path.exists(db_path):
        sys.exit('ERROR: database not found: {}'.format(db_path))
    uri = 'file:{}?mode=ro'.format(os.path.abspath(db_path).replace('\\', '/'))
    return sqlite3.connect(uri, uri=True)


def _ensure_outside_repo(out_path):
    # [REASON]: generated evidence must never land inside the working tree
    # where a later `git add .` could commit it.
    resolved = os.path.abspath(out_path)
    if resolved == REPO_ROOT or resolved.startswith(REPO_ROOT + os.sep):
        sys.exit('ERROR: --out must point OUTSIDE the repository working '
                 'tree ({}), got: {}'.format(REPO_ROOT, resolved))


def _state_dir(uploads_dir):
    return os.path.join(uploads_dir, STATE_DIRNAME)


def _registry_path(uploads_dir):
    return os.path.join(_state_dir(uploads_dir), REGISTRY_BASENAME)


def _load_registry(uploads_dir):
    path = _registry_path(uploads_dir)
    if not os.path.exists(path):
        return {}
    with open(path, 'r', encoding='utf-8') as fh:
        return json.load(fh)


def _save_registry(uploads_dir, registry):
    os.makedirs(_state_dir(uploads_dir), exist_ok=True)
    path = _registry_path(uploads_dir)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(registry, fh, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, path)


def _append_audit(uploads_dir, entry):
    os.makedirs(_state_dir(uploads_dir), exist_ok=True)
    with open(os.path.join(_state_dir(uploads_dir), AUDIT_BASENAME),
              'a', encoding='utf-8') as fh:
        fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + '\n')


def _file_facts(path):
    st = os.stat(path)
    return {
        'size': st.st_size,
        'sha256': _sha256(path),
        'mtime': datetime.utcfromtimestamp(st.st_mtime)
                 .isoformat(timespec='seconds') + 'Z',
    }


def build_report(db_path, uploads_dir):
    """Read-only reconciliation snapshot: rows vs disk, plus orphans."""
    con = _connect_ro(db_path)
    try:
        cur = con.cursor()
        rows = cur.execute(ATTACHMENT_SQL).fetchall()

        attachments = []
        referenced = set()
        for (att_id, item_id, file_path, original_filename, db_size,
             uploaded_at, request_id, status) in rows:
            fname = os.path.basename(file_path or '')
            referenced.add(fname)
            full = os.path.join(uploads_dir, fname)
            entry = {
                'attachment_id': att_id,
                'item_id': item_id,
                'request_id': request_id,
                'request_status': status,
                # [REASON]: issued requests are immutable evidence -- the
                # apply mode can only restore-from-identical-backup or leave
                # the visible 'unavailable' status for these rows.
                'issued_request': status == 'issued',
                'file_name': fname,
                'original_filename': original_filename,
                'db_file_size': db_size,
                'uploaded_at': uploaded_at,
            }
            if fname and os.path.isfile(full):
                entry['status'] = 'present'
                entry['disk'] = _file_facts(full)
            else:
                entry['status'] = 'missing'
            attachments.append(entry)

        registry = _load_registry(uploads_dir)
        orphans = []
        if os.path.isdir(uploads_dir):
            for name in sorted(os.listdir(uploads_dir)):
                full = os.path.join(uploads_dir, name)
                if not os.path.isfile(full):
                    continue  # _quarantine/, _reconcile/ and other dirs
                if name in referenced:
                    continue
                entry = {'file_name': name}
                entry.update(_file_facts(full))
                m = FILENAME_RE.match(name)
                if m:
                    item_id = int(m.group(1))
                    entry['inferred_item_id'] = item_id
                    ctx = cur.execute(ITEM_CONTEXT_SQL, (item_id,)).fetchone()
                    if ctx:
                        entry['inferred_request_id'] = ctx[0]
                        entry['inferred_request_status'] = ctx[1]
                entry['known'] = name in registry
                if name in registry:
                    entry['known_since'] = registry[name].get('recorded_at')
                orphans.append(entry)
    finally:
        con.close()

    missing = [a for a in attachments if a['status'] == 'missing']
    unexpected = [o for o in orphans if not o['known']]
    return {
        'generated_at': _now_iso(),
        'mode': 'report',
        'db_path': os.path.abspath(db_path),
        'uploads_dir': os.path.abspath(uploads_dir),
        'attachments': attachments,
        'orphans': orphans,
        'summary': {
            'attachment_rows': len(attachments),
            'present': len(attachments) - len(missing),
            'missing': len(missing),
            'missing_on_issued_requests':
                sum(1 for a in missing if a['issued_request']),
            'orphan_files': len(orphans),
            'orphan_bytes': sum(o['size'] for o in orphans),
            'known_orphans': sum(1 for o in orphans if o['known']),
            'unexpected_orphans': len(unexpected),
        },
    }


def _print_report_summary(report):
    s = report['summary']
    print('Attachment reconciliation report ({})'.format(report['generated_at']))
    print('  DB:      {}'.format(report['db_path']))
    print('  uploads: {}'.format(report['uploads_dir']))
    print('  rows: {}  present: {}  MISSING: {} (of which on issued '
          'requests: {})'.format(s['attachment_rows'], s['present'],
                                 s['missing'],
                                 s['missing_on_issued_requests']))
    print('  orphans: {} ({} bytes) -- known/retained: {}, unexpected: {}'
          .format(s['orphan_files'], s['orphan_bytes'], s['known_orphans'],
                  s['unexpected_orphans']))
    for a in report['attachments']:
        if a['status'] == 'missing':
            print('  MISSING row #{}: {} (item {}, request {}, status {}{})'
                  .format(a['attachment_id'], a['file_name'], a['item_id'],
                          a['request_id'], a['request_status'],
                          ', ISSUED EVIDENCE' if a['issued_request'] else ''))


# ─── manifest apply ──────────────────────────────────────────────────────────

def _load_manifest(path):
    with open(path, 'r', encoding='utf-8') as fh:
        manifest = json.load(fh)
    if not isinstance(manifest.get('actions'), list) or not manifest['actions']:
        sys.exit('ERROR: manifest has no actions list')
    return manifest


def _validate_actions(manifest, db_path, uploads_dir):
    """Validate every manifest action. Returns (plan, problems)."""
    con = _connect_ro(db_path)
    try:
        cur = con.cursor()
        rows = {r[0]: r for r in cur.execute(ATTACHMENT_SQL).fetchall()}
    finally:
        con.close()
    referenced = {os.path.basename(r[2] or ''): r for r in rows.values()}
    registry = _load_registry(uploads_dir)

    plan = []
    problems = []
    seen_targets = set()
    for i, action in enumerate(manifest['actions']):
        kind = action.get('action')
        step = {'index': i, 'action': kind}
        plan.append(step)

        def refuse(reason):
            step['status'] = 'refused'
            step['reason'] = reason
            problems.append('action #{} ({}): {}'.format(i, kind, reason))

        if kind == 'restore':
            att_id = action.get('attachment_id')
            row = rows.get(att_id)
            if row is None:
                refuse('attachment row {} does not exist'.format(att_id))
                continue
            (_, item_id, file_path, _orig, db_size, _up, request_id,
             status) = row
            fname = os.path.basename(file_path or '')
            target = os.path.join(uploads_dir, fname)
            backup = action.get('backup_path') or ''
            step.update({'attachment_id': att_id, 'file_name': fname,
                         'request_id': request_id, 'request_status': status,
                         'backup_path': backup, 'target': target})
            if not fname:
                refuse('row has an empty file_path')
            elif os.path.exists(target):
                refuse('target file already exists -- nothing to restore, '
                       'never overwriting')
            elif not backup or not os.path.isfile(backup):
                refuse('backup file not found: {}'.format(backup))
            else:
                facts = _file_facts(backup)
                expected_sha = (action.get('sha256') or '').lower()
                # [REASON]: never restore a non-matching file -- and never
                # fabricate one. Size must equal the row's recorded
                # file_size; when the row has no usable size the manifest
                # MUST pin the content by hash.
                if db_size and facts['size'] != db_size:
                    refuse('backup size {} does not match DB row file_size '
                           '{}'.format(facts['size'], db_size))
                elif not db_size and not expected_sha:
                    refuse('DB row has no recorded file_size; the manifest '
                           'must provide a sha256 to pin the backup content')
                elif expected_sha and facts['sha256'] != expected_sha:
                    refuse('backup sha256 {} does not match manifest sha256 '
                           '{}'.format(facts['sha256'], expected_sha))
                else:
                    step['status'] = 'valid'
                    step['backup_facts'] = facts

        elif kind in ('quarantine', 'retain'):
            name = os.path.basename(action.get('file') or '')
            full = os.path.join(uploads_dir, name)
            step['file_name'] = name
            if not name or name != (action.get('file') or ''):
                refuse('file must be a bare filename inside the uploads '
                       'directory')
            elif name in referenced:
                row = referenced[name]
                # [REASON]: a file a DB row points to is NOT an orphan --
                # quarantining/retaining it would hide referenced (possibly
                # issued-request) evidence. Refuse, always.
                refuse('file is referenced by attachment row #{} (request '
                       '{}, status {}) -- referenced evidence may only be '
                       'restored or surfaced as unavailable, never moved'
                       .format(row[0], row[6], row[7]))
            elif not os.path.isfile(full):
                refuse('file not found in uploads directory')
            elif kind == 'quarantine' and name in seen_targets:
                refuse('duplicate quarantine action for the same file')
            else:
                if kind == 'retain' and name in registry:
                    step['status'] = 'valid'
                    step['note'] = 'already in known-orphans registry'
                else:
                    step['status'] = 'valid'
                if kind == 'quarantine':
                    seen_targets.add(name)
                    step['quarantine_to'] = os.path.join(
                        uploads_dir, QUARANTINE_DIRNAME, name)
                step['facts'] = _file_facts(full)
        else:
            refuse('unknown action type (allowed: restore, quarantine, '
                   'retain -- there is NO delete)')
    return plan, problems


def _execute_actions(plan, manifest_path, uploads_dir):
    """Execute a fully validated plan. Only copy/move/registry -- no delete."""
    registry = _load_registry(uploads_dir)
    manifest_ref = os.path.abspath(manifest_path)
    for step in plan:
        stamp = _now_iso()
        if step['action'] == 'restore':
            shutil.copy2(step['backup_path'], step['target'])
            step['applied'] = True
            step['rollback'] = ('restored file may simply be removed again '
                                'by the owner if needed: {}'
                                .format(step['target']))
        elif step['action'] == 'quarantine':
            qdir = os.path.join(uploads_dir, QUARANTINE_DIRNAME)
            os.makedirs(qdir, exist_ok=True)
            dest = step['quarantine_to']
            if os.path.exists(dest):
                # validated set can't contain duplicates; a leftover from an
                # earlier run must not be silently overwritten
                raise RuntimeError('quarantine destination already exists: '
                                   '{}'.format(dest))
            shutil.move(os.path.join(uploads_dir, step['file_name']), dest)
            step['applied'] = True
            step['rollback'] = ('move "{}" back to "{}"'.format(
                dest, os.path.join(uploads_dir, step['file_name'])))
        elif step['action'] == 'retain':
            registry[step['file_name']] = {
                'recorded_at': stamp,
                'sha256': step['facts']['sha256'],
                'size': step['facts']['size'],
                'note': step.get('note', ''),
                'manifest': manifest_ref,
            }
            step['applied'] = True
            step['rollback'] = ('remove the "{}" entry from {}'.format(
                step['file_name'], _registry_path(uploads_dir)))
        _append_audit(uploads_dir, {
            'at': stamp,
            'manifest': manifest_ref,
            'step': {k: v for k, v in step.items()},
        })
    _save_registry(uploads_dir, registry)


def run_manifest(db_path, uploads_dir, manifest_path, apply_mode):
    manifest = _load_manifest(manifest_path)
    plan, problems = _validate_actions(manifest, db_path, uploads_dir)
    result = {
        'generated_at': _now_iso(),
        'mode': 'apply' if apply_mode else 'dry-run',
        'manifest': os.path.abspath(manifest_path),
        'db_path': os.path.abspath(db_path),
        'uploads_dir': os.path.abspath(uploads_dir),
        'plan': plan,
        'problems': problems,
    }
    if problems:
        # [REASON]: all-or-nothing -- a partially valid manifest is applied
        # in NO part, so the owner reviews and fixes it as one unit.
        result['applied'] = False
        print('Manifest REFUSED ({} problem(s)):'.format(len(problems)))
        for p in problems:
            print('  - ' + p)
        return result, 1
    if not apply_mode:
        result['applied'] = False
        print('DRY-RUN ONLY (pass --apply to execute). {} action(s) valid.'
              .format(len(plan)))
        return result, 0
    _execute_actions(plan, manifest_path, uploads_dir)
    result['applied'] = True
    print('Applied {} action(s). Audit log: {}'.format(
        len(plan), os.path.join(_state_dir(uploads_dir), AUDIT_BASENAME)))
    return result, 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Spare parts attachment reconciliation (RE-SP-001). '
                    'Read-only report by default; manifest apply is '
                    'non-destructive (restore/quarantine/retain only, '
                    'never delete).')
    parser.add_argument('--db', required=True,
                        help='path to transport.db (opened READ-ONLY)')
    parser.add_argument('--uploads', required=True,
                        help='path to instance/uploads/spare_parts')
    parser.add_argument('--out', required=True,
                        help='JSON output path, OUTSIDE the repository')
    parser.add_argument('--manifest',
                        help='owner-approved manifest JSON (enables apply '
                             'planning; still dry-run without --apply)')
    parser.add_argument('--apply', action='store_true',
                        help='actually execute a valid manifest (default is '
                             'dry-run even with --manifest)')
    args = parser.parse_args(argv)

    _ensure_outside_repo(args.out)
    if args.apply and not args.manifest:
        sys.exit('ERROR: --apply requires --manifest')

    if args.manifest:
        payload, code = run_manifest(args.db, args.uploads, args.manifest,
                                     args.apply)
    else:
        payload = build_report(args.db, args.uploads)
        _print_report_summary(payload)
        code = 0

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    print('JSON written: {}'.format(os.path.abspath(args.out)))
    return code


if __name__ == '__main__':
    sys.exit(main())
