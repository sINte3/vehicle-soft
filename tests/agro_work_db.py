# -*- coding: utf-8 -*-
"""Временная база для тестов трека agro-work -- без Flask.

Таблицы agro-work создаются DDL самой миграции AGRO_WORK_001, таблицы GPS --
DDL миграции GPS_DAILY_001: тест проверяет ровно ту схему, которая встанет
на сервер. Справочники приложения (организации, техника, сопоставление
Wialon) -- минимальной DDL с теми колонками, которые читает трек; имена
колонок сверены с models.py.
"""

import os
import sqlite3
import tempfile

import migrate_agro_work_001 as agro_mig
import migrate_gps_daily_001 as gps_mig

APP_DDL = (
    'CREATE TABLE organizations (id INTEGER PRIMARY KEY, name TEXT NOT NULL, '
    "short_name TEXT DEFAULT '', sort_order INTEGER DEFAULT 0)",
    'CREATE TABLE equipment (id INTEGER PRIMARY KEY, name TEXT NOT NULL, '
    "plate TEXT DEFAULT '', category TEXT NOT NULL, eq_type TEXT DEFAULT '', "
    'organization_id INTEGER NOT NULL, is_active BOOLEAN DEFAULT 1)',
    'CREATE TABLE vialon_mappings (id INTEGER PRIMARY KEY, vialon_name TEXT '
    'UNIQUE NOT NULL, wialon_id INTEGER, equipment_id INTEGER, '
    'skip BOOLEAN DEFAULT 0)',
    'CREATE TABLE field_contours (id INTEGER PRIMARY KEY, name TEXT)',
)


def make_db(folder=None, gps=True):
    """Путь к новой временной базе со всей схемой, которую читает трек."""
    folder = folder or tempfile.mkdtemp(prefix='agro_work_test_')
    path = os.path.join(folder, 'transport.db')
    con = sqlite3.connect(path)
    try:
        for ddl in APP_DDL:
            con.execute(ddl)
        if gps:
            con.execute(gps_mig.CREATE_DAILY_AGGREGATES)
            con.execute(gps_mig.CREATE_WORK_POLYGONS)
        for _, ddl in agro_mig.TABLES:
            con.execute(ddl)
        for _, ddl, _ in agro_mig.INDEXES:
            con.execute(ddl)
        for _, ddl in agro_mig.TRIGGERS:
            con.execute(ddl)
        con.commit()
    finally:
        con.close()
    return path


def add_org(con, org_id, name):
    con.execute('INSERT INTO organizations (id, name) VALUES (?, ?)',
                (org_id, name))


def add_equipment(con, eq_id, plate, category='mtz', org_id=1,
                  name='МТЗ-80.1', is_active=1):
    con.execute('INSERT INTO equipment (id, name, plate, category, '
                'organization_id, is_active) VALUES (?, ?, ?, ?, ?, ?)',
                (eq_id, name, plate, category, org_id, is_active))


def add_mapping(con, mapping_id, wialon_id, equipment_id, skip=0, name=None):
    con.execute('INSERT INTO vialon_mappings (id, vialon_name, wialon_id, '
                'equipment_id, skip) VALUES (?, ?, ?, ?, ?)',
                (mapping_id, name or 'unit %d' % mapping_id, wialon_id,
                 equipment_id, skip))


def add_day(con, wialon_id, day, reason=None, sites=()):
    """Сутки GPS: агрегат и участки. sites -- [(area_ha, operator_label)]."""
    con.execute("INSERT INTO gps_daily_aggregates (work_date, wialon_id, "
                "points_total, points_work, reason, method_version, "
                "computed_at) VALUES (?, ?, 1000, 400, ?, 'test', 'now')",
                (day, wialon_id, reason))
    for number, (area, label) in enumerate(sites, start=1):
        con.execute("INSERT INTO gps_work_polygons (work_date, wialon_id, "
                    "site_number, area_ha, minutes, polygon_geojson, "
                    "operator_label) VALUES (?, ?, ?, ?, 60, '{}', ?)",
                    (day, wialon_id, number, area, label))


def all_text(path):
    """Каждое текстовое значение каждой таблицы agro_work -- одной строкой."""
    con = sqlite3.connect(path)
    try:
        chunks = []
        for table in agro_mig.EXPECTED_COLUMNS:
            for row in con.execute('SELECT * FROM %s' % table):
                chunks.extend(str(v) for v in row if v is not None)
        return '\n'.join(chunks)
    finally:
        con.close()
