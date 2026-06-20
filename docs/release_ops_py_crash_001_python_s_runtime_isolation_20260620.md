# OPS-PY-CRASH-001 - Python runtime isolation with python -s

Date: 2026-06-20
Status: CLOSED
Scope: operations / Windows NSSM service configuration
Code changes: no
Database changes: no
Token rotation: no
Service restarts: yes, controlled restart during NSSM AppParameters update only

## Summary

Vehicle Soft had repeated Windows APPCRASH events involving python.exe, MSVCP140.dll and exception 0xc0000005.
The issue was handled as an operational Python runtime isolation problem, not as an application-code defect.

## Root finding

Read-only diagnostics showed that Vehicle Soft services could see the user-site package directory:

C:\Users\umid\AppData\Roaming\Python\Python314\site-packages

WER reports also showed crash contexts involving user-site packages and other Python project native modules, including numpy, SQLAlchemy C extensions, greenlet, shapely, GEOS, psycopg2 and libpq.

## Decision

Telegram bot tokens were not rotated by user decision.
The selected fix was to start Vehicle Soft services with Python -s.
Python -s disables user-site packages for the process and prevents accidental loading of packages from AppData Roaming.

## Compatibility validation

Both production and staging were tested with python -s before changing NSSM service parameters.

Validation passed for:

- py_compile
- Flask app import
- bot.py import
- bot003_outbox_worker.py import
- Flask route smoke checks
- canonical fuel sync route
- removed legacy fuel sync route

## Staging configuration applied

- transportreportstaging: -s run_server.py
- transportbotstaging: -s bot.py
- transportbot003staging: -s bot003_outbox_worker.py --interval 30 --batch-size 20

Staging config backup:

D:\transport-report-backups\staging\service_config\ops_py_crash_009_nssm_before_20260620_100240.txt

Staging validation result:

- OPS-PY-CRASH-011 PASS
- all staging services RUNNING
- all staging Python children running with -s
- no staging crash or restart evidence after 2026-06-20 10:04

## Production configuration applied

- transportreport: -s run_server.py
- transportbot: -s bot.py
- transportbot003: -s bot003_outbox_worker.py --interval 30 --batch-size 20

Production config backup:

D:\transport-report-backups\production\service_config\ops_py_crash_012_nssm_before_20260620_100709.txt

Production validation result:

- OPS-PY-CRASH-013 PASS
- all production services RUNNING
- all staging services RUNNING
- all six Vehicle Soft Python children running with -s
- production and staging HTTP checks passed
- no python/MSVCP140.dll Application Error events after 2026-06-20 10:09
- no NSSM exit or restart events after 2026-06-20 10:09
- no Vehicle Soft WER crash reports after 2026-06-20 10:09

## Final state

Git baseline at closure:

b86c9ab document legacy fuel sync alias removal completion

Service runtime state:

- transportreport: RUNNING with python -s
- transportreportstaging: RUNNING with python -s
- transportbot: RUNNING with python -s
- transportbotstaging: RUNNING with python -s
- transportbot003: RUNNING with python -s
- transportbot003staging: RUNNING with python -s

## Rollback path

Rollback is not required.

If rollback is ever needed:

- restore NSSM AppParameters from the service config backup files
- restart only the affected service
- no DB restore is required because this change did not touch the database

## Future recommendation

A dedicated virtual environment for Vehicle Soft remains recommended for future hardening. For the current Windows service deployment, python -s is a low-risk isolation fix.
