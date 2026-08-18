# -*- coding: utf-8 -*-
"""GPS-1 -- the Wialon point collector.

STANDARD LIBRARY ONLY. Nothing here imports numpy, shapely, scipy or pyproj,
and nothing here imports `app`, `models` or `gps.area`. The collector runs as
a Windows service in its own venv; growing a geo stack because of a module it
never calls is exactly what the charter forbids (the same rule keeps
drone_collector clean).

It also never opens instance/transport.db. Points go into monthly SQLite
files of their own -- see gps_collector/storage.py and README.md.
"""
