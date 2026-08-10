#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/ux/serve_ephemeral.py -- podnyat NASTOYASHCHEE prilozhenie na
odnorazovoy baze dlya vizualnogo audita.

Zachem
------
Vizualnyy audit trebuet rendera: kaskad iz 2249 strok inline-CSS, sloy
sovmestimosti i perepolnenie kolonok iz ishodnikov ne chitayutsya. No trogat
staging i production radi skrinshotov ne nuzhno i nebezopasno. Etot skript
daet tretiy put -- nastoyashchee prilozhenie na vybrasyvaemoy baze vo
vremennom kataloge.

Eto EDINSTVENNYY fayl treka UI, kotoromu razresheno importirovat app:
`app = create_app()` vyzyvaet db.create_all() na importe, poetomu vsyakiy
importer -- pisatel. Zdes eto bezopasno rovno potomu, chto config podmenen
DO importa i baza odnorazovaya.

Zapusk:
  python tools/ux/serve_ephemeral.py                 # podnyat na 5099
  python tools/ux/serve_ephemeral.py --port 5099
  python tools/ux/serve_ephemeral.py --routes-only   # tolko spisok marshrutov

Vyvod v konsol -- tolko ASCII.
"""

import argparse
import atexit
import json
import os
import shutil
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# [REASON]: config patchitsya DO pervogo `import app` -- app.py stroit svoy
# modulnyy singleton na importe, i pozdnyaya podmena ostavila by ego
# privyazannym k realnomu instance/. Tot zhe poryadok, chto v tests/harness.py.
_TMP = tempfile.mkdtemp(prefix='vehicle_soft_ux_')
atexit.register(shutil.rmtree, _TMP, True)

os.environ['FLASK_ENV'] = 'dev'
os.environ.setdefault('SECRET_KEY', 'ux-audit-local-only')

import config  # noqa: E402

DB_PATH = os.path.join(_TMP, 'ux.db')
UPLOAD_DIR = os.path.join(_TMP, 'uploads')
config.DevelopmentConfig.SQLALCHEMY_DATABASE_URI = 'sqlite:///' + DB_PATH
config.DevelopmentConfig.UPLOAD_FOLDER = UPLOAD_DIR

from app import app  # noqa: E402
from models import db  # noqa: E402

sys.path.insert(0, os.path.join(REPO_ROOT, 'tools', 'ux'))
import seed_ux_fixtures  # noqa: E402


def create_non_model_tables():
    """Sozdat tablicy, u kotoryh net modeli SQLAlchemy.

    [REASON]: audit_logs sozdaetsya migraciey migrate_sec003a_real.py syrym
    SQL i modeli ne imeet, poetomu db.create_all() ee ne sozdaet vovse -- i
    /admin/audit padaet s 500 "no such table". Eto ne defekt produkta, a
    probel odnorazovoy bazy, i bez etoy funkcii on popadal by v otchet obhoda
    kak oshibka interfeysa. Nabor kolonok povtoryaet migraciyu; pri ee
    izmenenii pravitsya i zdes.
    """
    from sqlalchemy import text
    db.session.execute(text("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            user_id INTEGER,
            username_snapshot TEXT,
            full_name_snapshot TEXT,
            role_snapshot TEXT,
            action TEXT NOT NULL,
            entity_type TEXT,
            entity_id INTEGER,
            entity_label TEXT,
            module TEXT,
            route TEXT,
            method TEXT,
            ip_address TEXT,
            user_agent TEXT,
            before_json TEXT,
            after_json TEXT,
            changes_json TEXT,
            status TEXT DEFAULT 'ok',
            description TEXT
        )
    """))
    db.session.commit()


def collect_routes():
    """Spisok GET-marshrutov dlya obhoda.

    [REASON]: obhod idet po YAVNOMU spisku, a ne perekhodom po ssylkam. Nabor
    ssylok zavisit ot prav i dannyh, mezhdu progonami ne vosproizvoditsya i ne
    pokazyvaet, chto propushcheno. Krome togo, v istorii proekta tri
    ispravlennyh defekta klassa "GET s pobochnym effektom", poetomu perekhod po
    ssylkam ne yavlyaetsya bezopasnym po umolchaniyu dazhe zdes.
    """
    import sqlalchemy as sa

    ids = {}
    with app.app_context():
        for table in db.metadata.sorted_tables:
            pks = list(table.primary_key.columns)
            if not pks:
                continue
            try:
                row = db.session.execute(sa.select(pks[0]).limit(1)).first()
            except Exception:
                continue
            if row:
                ids[table.name] = row[0]

    # Imena parametrov marshruta -> tablica, otkuda brat sushchestvuyushchiy id.
    hint = {
        'unit_id': 'drone_units', 'op_id': 'drone_operators',
        'work_id': 'drone_works', 'customer_id': 'drone_customers',
        'nick_id': 'drone_nicknames', 'run_id': 'drone_reattach_runs',
        'alias_id': 'drone_customer_aliases', 'batch_id': 'drone_work_imports',
        'wo_id': 'work_orders', 'user_id': 'users', 'part_id': 'spare_parts',
        'sku_id': 'spare_part_skus', 'item_id': 'spare_part_request_items',
        'req_id': 'spare_part_requests', 'act_id': 'spare_part_write_off_acts',
        'eq_id': 'equipment', 'equipment_id': 'equipment',
        'station_id': 'fuel_stations2', 'warehouse_id': 'fuel_warehouses',
        'receipt_id': 'fuel_receipts2', 'card_id': 'fuel_cards',
        'mapping_id': 'vialon_mappings', 'import_id': 'vialon_imports',
        'id': 'equipment',
    }

    routes, skipped = [], []
    for rule in app.url_map.iter_rules():
        if rule.endpoint == 'static' or 'GET' not in (rule.methods or set()):
            continue
        path = str(rule)
        if rule.arguments:
            values = {}
            for arg in rule.arguments:
                table = hint.get(arg)
                if table and ids.get(table) is not None:
                    values[arg] = ids[table]
                else:
                    values[arg] = None
            if any(v is None for v in values.values()):
                skipped.append({'rule': path, 'endpoint': rule.endpoint,
                                'reason': 'no id available for %s' % sorted(
                                    a for a in rule.arguments
                                    if values.get(a) is None)})
                continue
            for arg, val in values.items():
                path = path.replace('<int:%s>' % arg, str(val))
                path = path.replace('<%s>' % arg, str(val))
        if '<' in path:
            skipped.append({'rule': str(rule), 'endpoint': rule.endpoint,
                            'reason': 'unresolved converter'})
            continue
        routes.append({'url': path, 'endpoint': rule.endpoint,
                       'slug': path.strip('/').replace('/', '-') or 'root'})

    routes.sort(key=lambda r: r['url'])
    skipped.sort(key=lambda r: r['rule'])
    return routes, skipped


def main():
    parser = argparse.ArgumentParser(description='Ephemeral Vehicle Soft for UX audit.')
    parser.add_argument('--port', type=int, default=5099)
    parser.add_argument('--rows', type=int, default=14, help='rows per table')
    parser.add_argument('--routes-only', action='store_true')
    parser.add_argument('--routes-out', default='docs/ux/11-baseline/routes.json')
    args = parser.parse_args()

    with app.app_context():
        db.create_all()
        create_non_model_tables()
    seed_ux_fixtures.seed(app, db, rows_per_table=args.rows)

    routes, skipped = collect_routes()
    out = os.path.join(REPO_ROOT, args.routes_out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'w', encoding='utf-8') as handle:
        json.dump({'routes': routes, 'skipped': skipped},
                  handle, ensure_ascii=False, indent=1, sort_keys=True)
        handle.write('\n')

    print('database : %s' % DB_PATH)
    print('routes   : %d crawlable, %d skipped -> %s' % (
        len(routes), len(skipped), args.routes_out))
    print('users    : ux_admin / ux_operator / ux_viewer / ux_mechanic, password ux-audit-local')

    if args.routes_only:
        return 0

    print('serving  : http://127.0.0.1:%d  (Ctrl-C to stop)' % args.port)
    sys.stdout.flush()
    app.run(host='127.0.0.1', port=args.port, debug=False, use_reloader=False)
    return 0


if __name__ == '__main__':
    sys.exit(main())
