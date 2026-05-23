# PROMPT_PROTOCOL.md — How to Work Through Claude / Claude Code

## Purpose

This protocol prevents context loss and unsafe code changes when work is split between ChatGPT, Claude chat and Claude Code.

## Roles

- ChatGPT: project manager, architect, reviewer and prompt writer.
- User: operator who copies prompts/files and runs commands.
- Claude Code: implementation agent inside `C:\transport-report`.

## Standard Claude Code task prompt

Use this structure:

```text
Read CLAUDE.md and these files first:
- docs/PROJECT_BRIEF.md
- docs/AGENT_STATE.md
- docs/ARCHITECTURE.md
- docs/DECISIONS.md
- docs/TASKS.md
- docs/QA_CHECKLIST.md

Task ID: TASK-...
Goal:
...

Files likely affected:
...

Constraints:
- Preserve existing data.
- Do not change DB schema without migration.
- Add # [REASON]: comments for non-obvious business logic.
- Do not add dependencies without approval.
- Keep .bat and run_server.py ASCII-only.

Before editing:
1. Inspect the relevant files.
2. Summarize the current implementation.
3. List the exact changes you plan.

After editing:
1. List files changed.
2. List commands run.
3. Show syntax check results.
4. Explain manual tests.
5. Update docs/AGENT_STATE.md and docs/TASKS.md if the task changes project state.
```

## Required result format from Claude Code

```text
RESULT SUMMARY
- ...

FILES CHANGED
- ...

DATABASE CHANGES
- None / migration script name

COMMANDS RUN
- ...

TEST RESULTS
- ...

RISKS
- ...

ROLLBACK
- ...

NEXT STEP
- ...
```

## When to stop and ask

Claude Code must stop before:

- deleting records;
- dropping tables;
- changing report layout meaning;
- changing payment/fuel/accounting rules;
- adding dependencies;
- changing deployment architecture;
- rewriting large modules.

## Session handoff

At the end of a session, update:

- `docs/AGENT_STATE.md`
- `docs/TASKS.md`
- `docs/DECISIONS.md` if an architecture/business decision was made.

Then provide a one-message handoff:

```text
Current status:
Completed:
Changed files:
Commands/tests:
Open risks:
Next recommended task:
```
