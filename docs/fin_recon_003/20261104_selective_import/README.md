# FIN-RECON-003 — guarded selective July import (review-only)

**Status: NO IMPORT GO. NO MERGE. NO DEPLOY. NO MIGRATIONS. NO BUSINESS DATA WRITES.**

This increment adds a separate guarded CLI, tools/drone_works_selective.py,
and refuses legacy/web writes of July 2026 works through shared apply_rows,
including renamed and repacked source workbooks. It does not decide whether
work occurred, cash was received, or any customer is the right one.

## Why this is needed

The corrected parser (PR #260, merge 45eb10c) reads all 328 jobs,
5,144.66 ha, 775,357,370.99 UZS. Four source-only blockers remain,
and the STRICT cohort of 167 jobs / 2,217.34 ha is only a PROPOSAL,
not an approved import. Importing all five books now would write
unapproved jobs and create unapproved customers.

The 2026-11-04 read-only production snapshot established 65 registered
migrations, payer_unidentified present, 6 existing July jobs from a
different book, 0 keys from the five source books, 903 customers and
922 active aliases. A new snapshot is required before any future apply.

## Technical contract

A PRIVATE, approved CSV must contain exactly:

    source_key,row_sha256

A source key is the exact source_file!source_sheet!source_row string.
row_sha256 is the SHA-256 of canonical JSON from the parsed AND
CUSTOMER/OPERATOR-RESOLVED row, computed by drone_works_selective.row_fingerprint.
The fingerprint pins both customer and operator IDs, and the unidentified
payer flag: a later alias reassignment invalidates the signed row. The owner separately approves
the exact CSV bytes and the overall SHA-256.

IMPORTANT: a hash is only an integrity pin; it is NOT an electronic
signature or evidence of authorization.

The tool requires --only-keys and --only-keys-sha256 together. Default
is read-only. --apply is an explicit write switch. No approved list is
created or supplied by this PR.

Before any write it verifies:

1. Exactly the five pinned original XLSX files and their SHA-256 checksums,
   and five manifest entries for 2026-07.
2. Exact byte hash of the private CSV, the 2-column CSV shape, valid unique
   source keys and fingerprints.
3. Every approved physical source key occurs exactly once among accepted
   parsed records (missing/duplicate/changed references are refused).
4. Every selected row matches its complete financial fingerprint, so
   changing area, amount, customer, date, payment type, received value,
   resolved customer/operator ID or payer classification invalidates approval.
5. No unparsed source jobs, duplicate provenance, or alias conflicts.
6. No automatic creation of customers/aliases, unresolved operators,
   previously imported selected rows, blank customers or undated jobs.

The legacy whole-book CLI and web-upload route call the same
import_drone_works.apply_rows guard. Both refuse ANY parsed July-2026 work
without an approved list, identified by work date or resulting period_month
regardless of filename or packaging. The original five filenames and original
byte SHA-256 are also pinned (defense-in-depth), even if someone falsely
changes the manifest month. The approved subset mode still requires the exact
original five filenames and manifest period 2026-07.

This intentionally changes FUTURE legacy uploads of *other* July-2026 books:
they will be held for separate review; previously imported Hasanboy works
in the DB are never altered or re-imported. Earlier periods remain unaffected
for dated works. Work rows with no date and a deliberately false historical
manifest period cannot be identified by their date alone: the original
five pinned names and original byte hashes remain a secondary control.
A future import for genuinely new July source books requires a separately
approved policy, not an automatic override.

If a selected row appears between preflight and apply, the whole approved
batch fails instead of silently reporting partial success.

## Operating restrictions

A successful dry-run is a TECHNICAL result, never financial approval.
The signed approval and current customer-directory review are separate
requirements. The CLI intentionally does not auto-generate an approved
list or permit new customer cards. No production data, approved list,
password, personal phone or database extract is committed to this PR.

Unit tests use synthetic XLSX books, temporary SQLite databases and in-memory
SQLite. No production data is used. CI must pass on Linux and Windows.
Independent Codex review found P1/P2/P3 on 2026-11-04; this follow-up is
the narrowly scoped repair and regression suite. No code merge is authorized
by a green CI. Nothing here authorizes deploy or import.
