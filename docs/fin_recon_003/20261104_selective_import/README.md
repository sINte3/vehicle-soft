# FIN-RECON-003 — guarded selective July import (Draft PR)

**Status: NO IMPORT GO. NO MERGE. NO DEPLOY. NO MIGRATIONS. NO BUSINESS DATA WRITES.**

This increment adds a separate guarded CLI, tools/drone_works_selective.py,
and refuses legacy full-book writes through shared apply_rows when the
five original July 2026 books are involved. It does not decide whether
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
row_sha256 is the SHA-256 of canonical JSON from the parsed row, computed
by drone_works_selective.row_fingerprint. The owner separately approves
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
   changing area, amount, customer, date, payment type or received value
   invalidates the approval.
5. No unparsed source jobs, duplicate provenance, or alias conflicts.
6. No automatic creation of customers/aliases, unresolved operators,
   previously imported selected rows, blank customers or undated jobs.

The legacy whole-book CLI and the web-upload route call the same
import_drone_works.apply_rows guard. Both refuse original July 2026 jobs
without an approved exact list. The guard is limited to the five named
original books and the manifest month 2026-07, leaving other periods
and the separate existing Hasanboy book unchanged.

## Operating restrictions

A successful dry-run is a TECHNICAL result, never financial approval.
The signed approval and current customer-directory review are separate
requirements. The CLI intentionally does not auto-generate an approved
list or permit new customer cards. No production data, approved list,
password, personal phone or database extract is committed to this PR.

All unit tests operate on synthetic rows and in-memory SQLite. CI must
pass on Linux and Windows before considering Ready for review.
The PR must remain Draft until independent code review. Nothing here
authorizes deploy or import.
