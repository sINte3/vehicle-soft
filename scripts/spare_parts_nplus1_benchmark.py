# -*- coding: utf-8 -*-
"""SPARE-PARTS-CYCLE-2-3 Part 9: N+1 benchmark on SELF-GENERATED data.

Seeds a DISPOSABLE SQLite database (in a temp directory) with a large
synthetic volume — thousands of equipment, tens of thousands of request
items — then measures query counts and wall time of the per-item reference
implementations vs the batched ones.

NEVER run this against a real database: it patches the app config to a
throwaway temp file before importing the app, and refuses to start if that
patch did not take effect.

Run from the project root:

  python scripts/spare_parts_nplus1_benchmark.py
"""
import os
import random
import sys
import tempfile
import time
from datetime import date, datetime, timedelta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.chdir(REPO)

TMP = tempfile.mkdtemp(prefix='sp_nplus1_bench_')
os.environ['FLASK_ENV'] = 'dev'
os.environ.setdefault('SECRET_KEY', 'bench-only-secret')

import config  # noqa: E402

BENCH_DB = os.path.join(TMP, 'bench.db')
config.DevelopmentConfig.SQLALCHEMY_DATABASE_URI = 'sqlite:///' + BENCH_DB
config.DevelopmentConfig.UPLOAD_FOLDER = os.path.join(TMP, 'uploads')

from app import app  # noqa: E402
from models import (db, User, Organization, Equipment, EquipmentModel,  # noqa: E402
                    EngineHoursRecord, SparePart, SparePartCategory,
                    SparePartMaintenanceNorm, SparePartRequest,
                    SparePartRequestItem, SparePartWriteOffAct,
                    SparePartWriteOffActItem, ROLE_ADMIN)
import spare_parts as sp  # noqa: E402

assert BENCH_DB in str(app.config['SQLALCHEMY_DATABASE_URI']), \
    'refusing to run: app is not pointed at the disposable benchmark DB'

# Synthetic volume (tunable).
N_EQUIPMENT = 2000
N_PARTS = 300
N_REQUESTS = 12000          # 1-3 items each => ~24k items
N_HOURS_DAYS = 60           # per-day engine-hour rows per machine
N_NORMS = 60

rng = random.Random(42)


def seed():
    t0 = time.time()
    db.create_all()
    admin = User(username='admin', role=ROLE_ADMIN)
    admin.set_password('x')
    db.session.add(admin)
    orgs = [Organization(name='Org {}'.format(i)) for i in range(8)]
    db.session.add_all(orgs)
    db.session.flush()
    models = [EquipmentModel(name='Model {}'.format(i), is_active=True)
              for i in range(40)]
    db.session.add_all(models)
    cats = [SparePartCategory(name_ru='Кат {}'.format(i),
                              name_uz='Кат {}'.format(i), kind='unit')
            for i in range(10)]
    db.session.add_all(cats)
    db.session.flush()

    db.session.bulk_insert_mappings(SparePart, [{
        'name': 'Деталь {}'.format(i), 'status': 'active',
        'category_id': cats[i % 10].id if i % 5 else None,
    } for i in range(N_PARTS)])
    db.session.bulk_insert_mappings(Equipment, [{
        'name': 'Техника {}'.format(i), 'category': 'mtz',
        'organization_id': orgs[i % 8].id,
        'model_id': models[i % 40].id if i % 7 else None,
        'is_active': True,
    } for i in range(N_EQUIPMENT)])
    db.session.commit()
    part_ids = [r[0] for r in db.session.query(SparePart.id).all()]
    eq_rows = db.session.query(Equipment.id, Equipment.organization_id).all()
    today = date.today()

    statuses = ['approved', 'issued', 'rejected', 'submitted', 'draft']
    req_maps, next_req_id = [], 1
    for _ in range(N_REQUESTS):
        eq_id, org_id = eq_rows[rng.randrange(len(eq_rows))]
        status = rng.choice(statuses)
        rd = today - timedelta(days=rng.randint(0, 365))
        req_maps.append({
            'id': next_req_id, 'request_date': rd, 'organization_id': org_id,
            'equipment_id': eq_id if rng.random() > 0.05 else None,
            'status': status, 'created_by': 1,
            'reviewed_at': (datetime.combine(rd, datetime.min.time())
                            if status == 'rejected' and rng.random() > 0.3 else None),
            'review_comment': 'дорого' if status == 'rejected' else '',
        })
        next_req_id += 1
    db.session.bulk_insert_mappings(SparePartRequest, req_maps)

    item_maps, next_item_id = [], 1
    for req in req_maps:
        # Skew part choice so repeats actually happen.
        for _ in range(rng.randint(1, 3)):
            pid = part_ids[int(rng.betavariate(1.2, 5) * (len(part_ids) - 1))]
            item_maps.append({
                'id': next_item_id, 'request_id': req['id'],
                'spare_part_id': pid, 'name': 'Деталь', 'quantity': rng.randint(1, 5),
                'unit': 'dona',
                'price': rng.randint(10, 900) * 1000 if rng.random() > 0.2 else None,
                'price_status': 'confirmed' if rng.random() > 0.3 else 'pending',
            })
            next_item_id += 1
    db.session.bulk_insert_mappings(SparePartRequestItem, item_maps)
    db.session.commit()

    items_by_req = {}
    for im in item_maps:
        items_by_req.setdefault(im['request_id'], []).append(im)
    act_maps, act_item_maps, seq = [], [], 1
    for req in req_maps:
        if req['status'] != 'issued' or rng.random() > 0.6:
            continue
        act_maps.append({
            'id': seq, 'act_number': 'SPW-2026-{:05d}'.format(seq),
            'request_id': req['id'], 'organization_id': req['organization_id'],
            'issued_date': req['request_date'] + timedelta(days=1),
            'issued_by': 1,
        })
        for im in items_by_req[req['id']]:
            act_item_maps.append({
                'act_id': seq, 'request_item_id': im['id'], 'name': im['name'],
                'quantity': im['quantity'], 'unit': im['unit'],
                'price': im['price'],
                'total': (im['price'] or 0) * im['quantity'],
            })
        seq += 1
    db.session.bulk_insert_mappings(SparePartWriteOffAct, act_maps)
    db.session.bulk_insert_mappings(SparePartWriteOffActItem, act_item_maps)

    hour_maps = []
    for eq_id, _org in eq_rows:
        for d in range(0, N_HOURS_DAYS * 3, 3):
            hour_maps.append({'equipment_id': eq_id,
                              'work_date': today - timedelta(days=d),
                              'engine_hours': rng.random() * 12})
    db.session.bulk_insert_mappings(EngineHoursRecord, hour_maps)

    db.session.bulk_insert_mappings(SparePartMaintenanceNorm, [{
        'spare_part_id': part_ids[i], 'interval_hours': rng.choice([50, 100, 250]),
        'equipment_model_id': models[i % 40].id if i % 3 == 0 else None,
        'is_active': True,
    } for i in range(N_NORMS)])
    db.session.commit()
    print('seeded: {} equipment, {} parts, {} requests, {} items, {} acts, '
          '{} engine-hour rows in {:.1f}s'.format(
              N_EQUIPMENT, N_PARTS, len(req_maps), len(item_maps),
              len(act_maps), len(hour_maps), time.time() - t0))


# ─── query counting ───────────────────────────────────────────────────────────
_COUNTER = {'n': 0}


def _install_counter():
    from sqlalchemy import event

    @event.listens_for(db.engine, 'before_cursor_execute')
    def _count(conn, cursor, statement, params, context, executemany):
        _COUNTER['n'] += 1


def measure(label, fn):
    _COUNTER['n'] = 0
    t0 = time.time()
    result = fn()
    dt = (time.time() - t0) * 1000
    print('  {:<44} {:>7} queries  {:>9.1f} ms'.format(label, _COUNTER['n'], dt))
    return result


# ─── reference (pre-Part-9) implementations ──────────────────────────────────

def report_lines(d_from, d_to):
    q = (db.session.query(SparePartRequestItem, SparePartRequest, SparePart)
         .join(SparePartRequest,
               SparePartRequestItem.request_id == SparePartRequest.id)
         .outerjoin(SparePart,
                    SparePartRequestItem.spare_part_id == SparePart.id)
         .filter(SparePartRequest.request_date >= d_from,
                 SparePartRequest.request_date <= d_to))
    return q.order_by(SparePartRequest.request_date, SparePartRequest.id,
                      SparePartRequestItem.id).all()


def report_repeat_reference(lines):
    rows = []
    for item, req, _part in lines:
        if req.status not in ('approved', 'issued'):
            continue
        if not req.equipment_id or not item.spare_part_id:
            continue
        res = sp._check_repeat_orders(req.equipment_id, item.spare_part_id,
                                      exclude_request_id=req.id,
                                      as_of_date=req.request_date,
                                      eligible_statuses=('approved', 'issued'))
        if res['severity'] in ('red', 'yellow'):
            rows.append((req.id, item.id, res['severity'], res['days_since']))
    return rows


def maintenance_reference(org_ids=None):
    due = []
    norms = SparePartMaintenanceNorm.query.filter_by(is_active=True).all()
    for norm in norms:
        if not norm.interval_hours or norm.interval_hours <= 0:
            continue
        anchor_eq_q = (db.session.query(SparePartRequest.equipment_id)
                       .join(SparePartWriteOffAct,
                             SparePartWriteOffAct.request_id == SparePartRequest.id)
                       .join(SparePartWriteOffActItem,
                             SparePartWriteOffActItem.act_id == SparePartWriteOffAct.id)
                       .join(SparePartRequestItem,
                             SparePartWriteOffActItem.request_item_id == SparePartRequestItem.id)
                       .filter(SparePartRequestItem.spare_part_id == norm.spare_part_id,
                               SparePartRequest.equipment_id.isnot(None))
                       .distinct())
        eq_ids = [r[0] for r in anchor_eq_q.all()]
        if not eq_ids:
            continue
        eq_q = Equipment.query.filter(Equipment.id.in_(eq_ids),
                                      Equipment.is_active.is_(True))
        if norm.equipment_model_id:
            eq_q = eq_q.filter(Equipment.model_id == norm.equipment_model_id)
        if org_ids is not None:
            eq_q = eq_q.filter(Equipment.organization_id.in_(org_ids))
        for eq in eq_q.all():
            anchor = sp._last_replacement_date(eq.id, norm.spare_part_id)
            if anchor is None:
                continue
            hours_since = sp._hours_since_replacement(eq.id, anchor)
            if hours_since < norm.interval_hours:
                continue
            due.append((eq.id, norm.id, anchor, round(hours_since, 1),
                        round(hours_since - norm.interval_hours, 1)))
    due.sort(key=lambda d: d[4], reverse=True)   # overdue_by, like the app
    return due


def detail_reference(eq_id, part_ids, exclude):
    out = {}
    for pid in part_ids:
        out[pid] = (sp._check_repeat_orders(eq_id, pid, exclude_request_id=exclude),
                    sp._check_extra_warnings(eq_id, pid, exclude_request_id=exclude))
    return out


def main():
    with app.app_context():
        seed()
        _install_counter()
        with app.test_request_context():
            from flask import g
            from flask_login import login_user
            g.lang = 'ru'
            login_user(User.query.get(1))

            today = date.today()
            d_from, d_to = today - timedelta(days=90), today

            print('\nREPORT repeat-rows pass (period {}..{}):'.format(d_from, d_to))
            lines = report_lines(d_from, d_to)
            examined = sum(1 for i, r, _p in lines
                           if r.status in ('approved', 'issued')
                           and r.equipment_id and i.spare_part_id)
            print('  examined lines: {}'.format(examined))
            ref = measure('before: one query per examined line',
                          lambda: report_repeat_reference(lines))
            new = measure('after:  full _reports_data (all 5 tables)',
                          lambda: sp._reports_data(d_from, d_to))
            ref_set = {(r[0], r[2], r[3]) for r in ref}
            new_set = {(r['request_id'], r['severity'], r['days_since'])
                       for r in new['repeat_rows']}
            print('  repeat rows: before={} after={} identical={}'.format(
                len(ref), len(new['repeat_rows']), ref_set == new_set))

            print('\nMAINTENANCE due list:')
            ref = measure('before: per-norm + per-machine queries',
                          maintenance_reference)
            new = measure('after:  batched _maintenance_due_rows',
                          sp._maintenance_due_rows)
            ref_cmp = [(r[0], r[1], r[2], r[3]) for r in ref]
            ref = ref_cmp
            new_cmp = [(r['equipment'].id, r['norm'].id, r['last_replaced'],
                        r['hours_since']) for r in new]
            print('  due rows: before={} after={} identical={}'.format(
                len(ref), len(new), ref_cmp == new_cmp))

            print('\nDETAIL warnings for one busy submitted request '
                  '(simulated 15-item request):')
            eq_id = db.session.query(Equipment.id).first()[0]
            part_ids = [r[0] for r in
                        db.session.query(SparePart.id).limit(15).all()]
            ref = measure('before: ~5 queries per item',
                          lambda: detail_reference(eq_id, part_ids, 1))
            new_base = {}
            new_extra = {}

            def _batch():
                new_base.update(sp._check_repeat_orders_batch(
                    eq_id, part_ids, exclude_request_id=1))
                new_extra.update(sp._check_extra_warnings_batch(
                    eq_id, part_ids, exclude_request_id=1))
            measure('after:  fixed-count batched engines', _batch)
            same = all(ref[pid] == (new_base[pid], new_extra[pid])
                       for pid in part_ids)
            print('  identical={}'.format(same))

    print('\nDisposable DB was {}'.format(BENCH_DB))


if __name__ == '__main__':
    main()
