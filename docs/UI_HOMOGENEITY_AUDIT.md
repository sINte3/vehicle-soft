# UI Homogeneity Audit — TASK-UI-AUDIT-001

Date: 2026-07-06
Scope: all 42 user-facing templates under `templates/` (post UI-NEXT Phase 10).
Method: automated signal scan (`audit_templates.py`, read-only, not committed)
cross-checked with manual forensic read of every anomaly the scan flagged.
No template modified during this audit.

## Legend

- **A — Full visual redesign.** Page uses the `--vs-*` design system (via
  `vs-*` classes, or via a documented local component layer mapped to
  `--vs-*` tokens). Matches the Claude Design prototype look and feel.
- **B — Partial.** Inherits the new `base_next.html` chrome (sidebar/topbar),
  but the page's own content markup predates UI-NEXT: either untouched
  legacy structure, or a mechanical `form-control` pass with no card/badge/
  table redesign.
- **C — Untouched.** Zero design-system signal at all beyond inheriting the
  shell via the Phase 10 `extends` swap.

## A — Full visual redesign (4)

| File | Note |
|---|---|
| `index.html` | Phase 2. Full `vs-*` rewrite (289 hits). |
| `login.html` | Phase 3. Full `vs-*` rewrite (26 hits) + animation. |
| `deficiencies.html` | Phase 6. Pills, `vs-table`, page-header cleanup. |
| `daily_entry.html` | Phase 4/4b. **Heuristic blind spot:** only 3 raw `vs-*` hits, but this is a false negative — the page uses its own local `<style>` block with 27 legacy vars remapped to `--vs-*` tokens instead of `vs-*` class names. Confirmed fully redesigned per AGENT_STATE Phase 4 record, not a gap. |

`base_next.html` itself (78 `vs-*` hits) is the shell/infrastructure, not a content page — excluded from classification.

## B — Partial (33)

### Reference & admin
`admin_users.html`, `audit_logs.html`, `ref_customers.html`,
`ref_equipment.html`, `ref_organizations.html`, `ref_work_types.html`,
`report.html`

- `audit_logs.html` — known gap, Phase 7 explicitly deferred visual pass
  ("no verified design reference for this page yet").
- `ref_*.html` — Phase 6 gave `form-control` only, no card/table redesign.
- `report.html` — Phase 8 `form-control` only; page-header intentionally kept
  (carries live filter-summary content, do not delete in a future phase).
  Runs on the same `--ux-*` local variable set as spare_parts (see finding
  below) — 14 hits, own local design language, not `--vs-*`.
- Uses shared `.ms-wrap` legacy multiselect (2 hits) — functional
  post-Phase-9 fix in `base_next.html`, not a redesign blocker, just tracked
  tech debt (see AGENT_STATE Phase 9 notes).

### fuel/* module (12)
`balance_report.html`, `cards.html`, `dashboard.html`, `initial_balance.html`,
`receipts.html`, `report.html`, `reports.html`, `station_issues_report.html`,
`stations.html`, `transactions.html`, `warehouses.html`, `warnings.html`

- All extend correctly, all have `form-control`, most have full bilingual
  branching (Phase 9 added it where missing).
- Zero `vs-*` classes anywhere — content markup is legacy, using the
  `design-system.css` compat-layer variable names directly
  (`--text2`/`--surface`/etc.), not the `vs-*` component classes.
- **Confirmed leftover bug:** `cards.html` line 53 —
  `.cards-empty { ... color:var(--text-muted); }` — `--text-muted` is
  undefined in scope (should be `var(--text2)`, per the Phase 9 remap
  applied everywhere else in this file). Missed during the Phase 9 batch
  fix. Isolated one-line fix, safe, no visual regression risk.

### work_orders/* module (4)
`work_order_close.html`, `work_order_detail.html`, `work_order_form.html`,
`work_orders_list.html` — Phase 9, `form-control` + label fixes only, no
`vs-*` classes, no card/table redesign.

### spare_parts/* module (4)
`spare_part_detail.html`, `spare_part_form.html`, `spare_parts_catalog.html`,
`spare_parts_list.html`

- **New finding, not previously documented in AGENT_STATE:** `spare_part_form.html`
  and `spare_parts_list.html` run on a separate, self-contained local design
  language (`--ux-*` CSS custom properties with hardcoded fallback values,
  e.g. `var(--ux-muted, #667085)`), apparently from undocumented prior passes
  referred to internally as SPARE002A. Not broken (fallbacks render
  correctly) but structurally and visually independent of both the old
  Bootstrap-era markup and the current `--vs-*` system. `spare_parts_catalog.html`
  and `spare_part_detail.html` don't use this pattern.
- No Bootstrap leftovers (`panel panel`/`btn-default`: 0 everywhere).
- Div-count "imbalance" flagged by the automated scan on 3 files is a false
  positive — legitimate `<div class="spare001a-scope">` wrapper closed at
  template end. Confirmed via manual read, no embedded/corrupted `</div>`
  found in any of the 3 files.

### wialon/* module (5 migrated + 1 missed)
`wialon.html`, `wialon_auto_match.html`, `wialon_mapping.html`,
`wialon_mapping_list.html`, `wialon_report.html` — Phase 9, `form-control`
pass only, no `vs-*` classes.

- **New finding:** `workload.html` (route `/wialon/workload`, live, under
  `module_required('wialon')`, has its own `/export` route) was **not**
  part of the 5-file Phase 9 wialon batch and received zero treatment —
  zero `form-control`, zero bilingual branching signal detected. This is a
  real gap in the wialon module, not an intentional exclusion.

## C — Untouched (4, plus 1 out-of-scope)

| File | Note |
|---|---|
| `admin_permissions.html` | Zero signal on every metric. Extends-swap only. |
| `change_temporary_password.html` | Known gap, 2-line diff, never in any phase's scope. |
| `profile.html` | Zero signal on every metric. Newly discovered — not previously tracked. |
| `error.html` | Zero signal, but this had its own separate pre-UI-NEXT redesign (`UI003A_ERROR_TEMPLATE` marker, 2026-06 timeframe). Minimal chrome by design (error pages). Lower priority — not part of the `--vs-*` system, but not obviously broken either. Recommend explicit decision on whether it needs `vs-*` treatment or can stay as-is. |

`workload.html` is technically zero-signal too but is grouped under the
wialon/* section above since it belongs to that module's incomplete Phase 9
batch, not a standalone gap.

## Summary counts

- Full redesign (A): 4
- Partial (B): 33 (12 fuel + 4 work_orders + 4 spare_parts + 6 wialon incl. workload + 7 ref/admin/report)
- Untouched (C): 4
- Infrastructure/out-of-scope: `base_next.html`, `error.html` (flagged for a decision, not counted as a phase target by default)

## Recommended next-phase plan (proposal — needs sign-off before starting)

Ordered by a mix of risk and value, same phased-per-module discipline as
UI-NEXT:

1. **Phase 11 — trivial fixes, no visual work.** `fuel/cards.html` line 53
   var fix; add `workload.html` to a completed wialon batch (form-control +
   bilingual pass, matching what the other 5 wialon files got in Phase 9).
   Very low risk, closes two concrete confirmed bugs/gaps.
2. **Phase 12 — spare_parts/* + report.html full visual redesign.** Highest
   value: retires the undocumented `--ux-*` local design language entirely,
   replaces with `--vs-*`/`vs-*` components. Also gives `report.html` its
   first real visual pass (page-header content preserved, per prior note).
3. **Phase 13 — fuel/* full visual redesign (12 files).** Largest single
   module; do after Phase 12 proves the pattern on a smaller scope.
4. **Phase 14 — work_orders/* + wialon/* full visual redesign (4 + 6 files).**
5. **Phase 15 — ref_*/admin_users.html/audit_logs.html full visual redesign.**
6. **Phase 16 — Category C pages.** `admin_permissions.html`,
   `change_temporary_password.html`, `profile.html` — blank slate, lowest
   risk, could arguably move earlier as quick wins if preferred.
7. **Decision needed, not a phase:** `error.html` — confirm whether it
   should be brought onto `--vs-*` or left as its own minimal design.

This ordering is a proposal, not a decision — phase order should be
confirmed before Phase 11 starts.
