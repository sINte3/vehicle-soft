# -*- coding: utf-8 -*-
"""GPS-1 -- the Wialon point collector.

STANDARD LIBRARY ONLY. Nothing here imports numpy, shapely, scipy or pyproj,
and nothing here imports `app`, `models` or `gps.area`. The collector runs as
a Windows service in its own venv; growing a geo stack because of a module it
never calls is exactly what the charter forbids (the same rule keeps
drone_collector clean).

Points go into monthly SQLite files of their own -- see
gps_collector/storage.py and README.md -- never into instance/transport.db.

The ONE thing it writes to transport.db is a single row of the ingestion
journal after a run has finished (gps_collector/sync_log.py, table
gps_sync_log), through stdlib sqlite3 and never through models. Any failure of
that write is printed and swallowed: the points matter more than the record of
the points.
"""
