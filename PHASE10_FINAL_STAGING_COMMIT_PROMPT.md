# Task: Phase 10 final release commit (staging) — UI-NEXT complete

## Context
UI-NEXT (Phases 1-10) is fully complete and visually verified on staging
(RU+UZ) across all modules: shell, dashboard, login, daily_entry,
deficiencies, ref_*, admin_*, report, fuel/*, work_orders/*, spare_parts/*,
wialon/*. `NEXT_UI` flag retired, `base.html` deleted, `static/` already
tracked in a prior commit (`ee9234c`). This task creates ONE clean commit
covering the remaining Phase 1-10 changes and pushes both commits to GitHub.
Production is NOT touched in this task.

## Steps

1. `cd C:\transport-report-staging`

2. Confirm `PHASE10_RETIRE_NEXTUI_REMOVE_BASE_PROMPT.md` is not present in the
   working directory. If it still exists, delete it.

3. Run `git status` and print it. Expected: `app.py`, `docs/AGENT_STATE.md`,
   ~40 modified templates, `translations.py`, `work_orders.py` modified;
   `templates/base.html` deleted; `templates/base_next.html` untracked. No
   other untracked files should remain — if any unexpected file appears, STOP
   and report it instead of proceeding.

4. Update `docs/AGENT_STATE.md`: mark "UI-NEXT Phase 10" as COMPLETE (it
   currently says "NOT STARTED" / lists it under "Remaining phases"). Record:
   NEXT_UI flag retired, base.html removed, static/ assets tracked in git,
   all modules migrated and visually confirmed RU+UZ. Keep existing historical
   phase sections intact — only update the Phase 10 status and add a closing
   summary. This edit is itself part of the commit (docs should never lag
   what's actually true, per project convention).

5. Stage everything currently tracked-modified plus the new file and the
   deletion:
   ```powershell
   git add -A
   git status
   ```
   Confirm the staged list matches step 3's expectation exactly (plus the
   AGENT_STATE.md edit from step 4). Do not proceed if anything unexpected is
   staged.

6. Commit:
   ```powershell
   git commit -m "UI-NEXT Phases 1-10 complete: migrate all modules to base_next.html design system, retire NEXT_UI flag, remove legacy base.html"
   ```

7. Push both this commit and the earlier `ee9234c` (static/ tracking) commit
   to GitHub:
   ```powershell
   git push origin main
   ```

8. Print `git log -3 --oneline` and `git status` to confirm staging is clean
   and `origin/main` is up to date.

## Acceptance criteria
- `docs/AGENT_STATE.md` reflects Phase 10 as complete.
- Single new commit created covering all remaining Phase 1-10 changes.
- `git push` succeeds; `git status` afterward shows "up to date with
  origin/main", working tree clean.
- No production files touched, no production service restarted.

## Rollback
Nothing destructive happened to running services. If the commit content
turns out to be wrong after push: do not force-push; instead prepare a
follow-up corrective commit (`git revert` or a new fix commit) — this keeps
history honest, since production has not pulled this yet and can simply wait
for the corrected commit.

## Report back
- Confirmation the stray prompt file was absent/removed.
- Full `git status` output from step 3 (before staging).
- Full `git status` output from step 5 (after staging, before commit).
- The commit hash and message.
- `git log -3 --oneline` and final `git status` after push.
