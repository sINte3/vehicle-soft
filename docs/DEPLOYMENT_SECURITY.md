# DEPLOYMENT_SECURITY.md — Secrets and Environment Variables

## Overview

Two sensitive values must be set as Windows environment variables before starting
the TransportReport service:

| Variable | Required | Purpose |
|---|---|---|
| `SECRET_KEY` | **Yes** — service refuses to start without it | Flask session signing key |
| `FUEL_API_TOKEN` | Yes for Topaz fuel sync | Token checked on every `/fuel/api/fuel_sync` POST |

**Do not put real secret values in source code, templates, or log files.**

---

## Step 1 — Generate a strong SECRET_KEY

Open PowerShell or CMD and run one of these to generate a random key:

```cmd
python -c "import secrets; print(secrets.token_hex(32))"
```

Copy the printed value. It will look like:
`a3f8c2d1e0b4...` (64 hex characters)

---

## Step 2 — Set environment variables (system-wide, persistent)

Run CMD **as Administrator**:

```cmd
setx SECRET_KEY "paste-your-generated-key-here" /M
setx FUEL_API_TOKEN "paste-your-topaz-agent-token-here" /M
```

The `/M` flag writes to the system-wide environment (HKLM), which NSSM reads
when starting the service. Without `/M` the variable is user-only and the service
may not see it.

---

## Step 3 — Verify the variables are set

Still in the same CMD (as Administrator):

```cmd
reg query "HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v SECRET_KEY
reg query "HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v FUEL_API_TOKEN
```

Both commands should print the variable name and value.

---

## Step 4 — Restart the TransportReport service

Environment changes only take effect for new processes. Restart the service:

```cmd
cd C:\transport-report
.\nssm.exe stop TransportReport
.\nssm.exe start TransportReport
```

If nssm.exe is not in the project directory:

```cmd
net stop TransportReport
net start TransportReport
```

---

## Step 5 — Confirm startup

Check the NSSM log or run directly to confirm the service starts:

```cmd
cd C:\transport-report
"C:\Program Files\Python314\python.exe" run_server.py
```

If `SECRET_KEY` is missing, the output will be:

```
ERROR: SECRET_KEY environment variable is not set.
```

If `FUEL_API_TOKEN` is missing, the service starts normally but every Topaz
agent sync request to `/fuel/api/fuel_sync` will receive HTTP 401 Unauthorized.

---

## Behavior summary

| Condition | Result |
|---|---|
| `SECRET_KEY` set | Service starts normally |
| `SECRET_KEY` missing | `run_server.py` exits immediately with a clear error; NSSM marks service as failed |
| `FUEL_API_TOKEN` set | Topaz agent sync works if agent sends the matching token |
| `FUEL_API_TOKEN` missing | Service starts; all `/fuel/api/fuel_sync` calls return 401 |
| `FUEL_API_TOKEN` wrong value | Same as missing — 401 |

---

## Development mode

When running locally with `FLASK_ENV=dev`, `SECRET_KEY` is not required.
A clearly-named dev-only fallback is used automatically:

```
dev-only-insecure-key-do-not-use-in-production
```

This fallback is **never** active when `FLASK_ENV` is `sqlite_prod` or `prod`.

---

## Rollback instructions

If the service fails to start after deploying these changes:

1. Confirm `SECRET_KEY` is set:
   ```cmd
   reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v SECRET_KEY
   ```
2. If missing, set it with `setx` (see Step 2) and restart.
3. To revert to the previous code behaviour (insecure hardcoded fallback):
   - Restore the previous `config.py`, `fuel_routes.py`, and `run_server.py` from backup.
   - Restart the service.
   - Do not leave the insecure fallback in place longer than necessary.

---

## Security notes

- Rotate `SECRET_KEY` if it is ever exposed (e.g., committed to a repository).
  Rotating invalidates all active user sessions (users will be logged out).
- `FUEL_API_TOKEN` must match the token configured in the Topaz agent.
  Changing it requires updating both the server environment variable and the
  Topaz agent configuration.
- Never print, log, or display these values in templates or API responses.
