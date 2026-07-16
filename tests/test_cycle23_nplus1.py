# -*- coding: utf-8 -*-
"""SPARE-PARTS-CYCLE-2-3 Part 9: batched warning/report/maintenance queries
must produce IDENTICAL output to the per-item reference implementations.

The reference paths are the untouched per-pair functions still in the
module (_check_repeat_orders, _check_extra_warnings,
_last_replacement_date, _hours_since_replacement); the maintenance
reference below is a verbatim copy of the pre-change _maintenance_due_rows
loop built on those helpers. Everything runs on the disposable harness DB.
"""
import random
import unittest
from datetime import date, datetime, timedelta

from flask import g

from sqlalchemy import event

from tests.harness import app, db, reset_db, create_admin, create_org, login
from models import (Equipment, EquipmentModel, EngineHoursRecord, SparePart,
                    SparePartCategory, SparePartCompatibility,
                    SparePartMaintenanceNorm, SparePartRequest,
                    SparePartRequestItem, SparePartWriteOffAct,
                    SparePartWriteOffActItem)

import spare_parts as sp


def _seed(admin_id, org_ids, rng):
    """Synthetic volume with the edge cases the engines care about:
    rejected/returned requests, multi-item requests, same part repeated,
    acts, engine hours, norms with and without a model filter."""
    today = date.today()
    models = []
    for i in range(4):
        m = EquipmentModel(name='Model {}'.format(i), is_active=True)
        db.session.add(m)
        models.append(m)
    db.session.flush()
    cats = []
    for i in range(3):
        c = SparePartCategory(name_ru='Кат {}'.format(i),
                              name_uz='Кат {}'.format(i), kind='unit')
        db.session.add(c)
        cats.append(c)
    db.session.flush()
    parts = []
    for i in range(12):
        p = SparePart(name='Деталь {}'.format(i), status='active',
                      category_id=(cats[i % 3].id if i % 4 else None))
        db.session.add(p)
        parts.append(p)
    db.session.flush()
    eqs = []
    for i in range(10):
        e = Equipment(name='Техника {}'.format(i), category='mtz',
                      organization_id=org_ids[i % len(org_ids)],
                      model_id=(models[i % 4].id if i % 5 else None),
                      is_active=(i != 7))
        db.session.add(e)
        eqs.append(e)
    db.session.flush()
    # compatibility: part0 compatible with model0 only; part1 with all models;
    # part2 has NO rows (silence rule).
    db.session.add(SparePartCompatibility(spare_part_id=parts[0].id,
                                          equipment_model_id=models[0].id))
    for m in models:
        db.session.add(SparePartCompatibility(spare_part_id=parts[1].id,
                                              equipment_model_id=m.id))
    # requests across statuses/dates
    statuses = ['approved', 'issued', 'rejected', 'submitted', 'draft',
                'returned_for_revision']
    requests = []
    for i in range(220):
        eq = rng.choice(eqs)
        req = SparePartRequest(
            request_date=today - timedelta(days=rng.randint(0, 200)),
            organization_id=eq.organization_id,
            equipment_id=(eq.id if rng.random() > 0.1 else None),
            status=rng.choice(statuses),
            created_by=admin_id)
        if req.status == 'rejected':
            if rng.random() > 0.3:
                req.reviewed_at = datetime.combine(
                    req.request_date + timedelta(days=rng.randint(0, 5)),
                    datetime.min.time())
            req.review_comment = rng.choice(['дорого', ''])
        db.session.add(req)
        db.session.flush()
        for _ in range(rng.randint(1, 3)):
            part = rng.choice(parts)
            db.session.add(SparePartRequestItem(
                request_id=req.id, spare_part_id=part.id, name=part.name,
                quantity=rng.randint(1, 5), unit='dona',
                price=(rng.randint(10, 900) * 1000 if rng.random() > 0.2 else None),
                price_status=rng.choice(['confirmed', 'pending'])))
        requests.append(req)
    db.session.flush()
    # acts for a subset of issued requests
    seq = 1
    for req in requests:
        if req.status != 'issued' or rng.random() > 0.7:
            continue
        act = SparePartWriteOffAct(
            act_number='SPW-2026-{:05d}'.format(seq), request_id=req.id,
            organization_id=req.organization_id,
            issued_date=req.request_date + timedelta(days=1),
            issued_by=admin_id)
        seq += 1
        db.session.add(act)
        db.session.flush()
        for item in req.items:
            db.session.add(SparePartWriteOffActItem(
                act_id=act.id, request_item_id=item.id, name=item.name,
                quantity=item.quantity, unit=item.unit, price=item.price,
                total=(item.price or 0) * item.quantity))
    # engine hours (per-day records)
    for eq in eqs:
        for d in range(0, 200, rng.randint(1, 4)):
            db.session.add(EngineHoursRecord(
                equipment_id=eq.id,
                work_date=today - timedelta(days=d),
                engine_hours=rng.random() * 12))
    # norms: with and without model filter, one inactive, one zero-interval
    db.session.add(SparePartMaintenanceNorm(
        spare_part_id=parts[0].id, interval_hours=50, is_active=True))
    db.session.add(SparePartMaintenanceNorm(
        spare_part_id=parts[1].id, equipment_model_id=models[0].id,
        interval_hours=30, is_active=True))
    db.session.add(SparePartMaintenanceNorm(
        spare_part_id=parts[2].id, interval_hours=10, is_active=True))
    db.session.add(SparePartMaintenanceNorm(
        spare_part_id=parts[3].id, interval_hours=100, is_active=False))
    db.session.add(SparePartMaintenanceNorm(
        spare_part_id=parts[4].id, interval_hours=0, is_active=True))
    db.session.commit()
    return eqs, parts, requests


def _maintenance_due_rows_reference(org_ids=None):
    """Verbatim copy of the pre-Part-9 per-pair implementation."""
    due = []
    norms = (SparePartMaintenanceNorm.query
             .filter_by(is_active=True).all())
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
            due.append({
                'equipment': eq,
                'part': norm.spare_part,
                'norm': norm,
                'model': eq.model,
                'last_replaced': anchor,
                'hours_since': round(hours_since, 1),
                'interval_hours': norm.interval_hours,
                'overdue_by': round(hours_since - norm.interval_hours, 1),
            })
    due.sort(key=lambda d: d['overdue_by'], reverse=True)
    return due


class NPlusOneEquivalenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reset_db()
        rng = random.Random(20260715)
        with app.app_context():
            cls.admin_id = create_admin()
        cls.org1 = create_org('Org A')
        cls.org2 = create_org('Org B')
        with app.app_context():
            cls.eq_ids = None
            eqs, parts, requests = _seed(cls.admin_id, [cls.org1, cls.org2], rng)
            cls.eq_ids = [e.id for e in eqs]
            cls.part_ids = [p.id for p in parts]
            cls.request_ids = [r.id for r in requests]

    def _ctx(self):
        ctx = app.test_request_context()
        ctx.push()
        g.lang = 'ru'
        # _reports_data reads current_user for org scoping — run as admin.
        from flask_login import login_user
        from models import User
        login_user(User.query.get(self.admin_id))
        return ctx

    def test_detail_warning_batches_equal_per_item_engines(self):
        ctx = self._ctx()
        try:
            checked_pairs = 0
            for eq_id in self.eq_ids:
                exclude = self.request_ids[0]
                base_batch = sp._check_repeat_orders_batch(
                    eq_id, self.part_ids, exclude_request_id=exclude)
                extra_batch = sp._check_extra_warnings_batch(
                    eq_id, self.part_ids, exclude_request_id=exclude)
                for pid in self.part_ids:
                    ref_base = sp._check_repeat_orders(
                        eq_id, pid, exclude_request_id=exclude)
                    ref_extra = sp._check_extra_warnings(
                        eq_id, pid, exclude_request_id=exclude)
                    self.assertEqual(ref_base, base_batch[pid],
                                     'rules 1-2 diverged eq={} part={}'.format(eq_id, pid))
                    self.assertEqual(ref_extra, extra_batch[pid],
                                     'rules 3-6 diverged eq={} part={}'.format(eq_id, pid))
                    checked_pairs += 1
            self.assertEqual(checked_pairs, len(self.eq_ids) * len(self.part_ids))
        finally:
            ctx.pop()

    def test_reports_repeat_rows_equal_per_line_reference(self):
        ctx = self._ctx()
        try:
            d_from = date.today() - timedelta(days=200)
            d_to = date.today()
            data = sp._reports_data(d_from, d_to)

            # Reference: the pre-Part-9 per-line loop over the same lines.
            q = (db.session.query(SparePartRequestItem, SparePartRequest, SparePart)
                 .join(SparePartRequest,
                       SparePartRequestItem.request_id == SparePartRequest.id)
                 .outerjoin(SparePart,
                            SparePartRequestItem.spare_part_id == SparePart.id)
                 .filter(SparePartRequest.request_date >= d_from,
                         SparePartRequest.request_date <= d_to))
            lines = q.order_by(SparePartRequest.request_date,
                               SparePartRequest.id,
                               SparePartRequestItem.id).all()
            ref_rows = []
            for item, req, part in lines:
                if req.status not in ('approved', 'issued'):
                    continue
                if not req.equipment_id or not item.spare_part_id:
                    continue
                res = sp._check_repeat_orders(
                    req.equipment_id, item.spare_part_id,
                    exclude_request_id=req.id, as_of_date=req.request_date,
                    eligible_statuses=('approved', 'issued'))
                if res['severity'] in ('red', 'yellow'):
                    ref_rows.append({
                        'request_id': req.id,
                        'request_date': req.request_date,
                        'part_name': item.name,
                        'severity': res['severity'],
                        'days_since': res['days_since'],
                    })
            ref_rows.sort(key=lambda r: (0 if r['severity'] == 'red' else 1,
                                         r['days_since'] if r['days_since'] is not None else 999))

            got = [{k: r[k] for k in ('request_id', 'request_date', 'part_name',
                                      'severity', 'days_since')}
                   for r in data['repeat_rows']]
            self.assertTrue(ref_rows, 'synthetic data produced no repeat rows '
                                      '— the equivalence check would be vacuous')
            self.assertEqual(ref_rows, got)
        finally:
            ctx.pop()

    def test_maintenance_due_equal_per_pair_reference(self):
        ctx = self._ctx()
        try:
            for org_ids in (None, [self.org1]):
                ref = _maintenance_due_rows_reference(org_ids=org_ids)
                got = sp._maintenance_due_rows(org_ids=org_ids)

                def strip(rows):
                    return [{
                        'eq': r['equipment'].id,
                        'part': r['part'].id if r['part'] else None,
                        'norm': r['norm'].id,
                        'model': r['model'].id if r['model'] else None,
                        'last_replaced': r['last_replaced'],
                        'hours_since': r['hours_since'],
                        'interval_hours': r['interval_hours'],
                        'overdue_by': r['overdue_by'],
                    } for r in rows]
                if org_ids is None:
                    self.assertTrue(ref, 'synthetic data produced no due rows '
                                         '— the equivalence check would be vacuous')
                self.assertEqual(strip(ref), strip(got))
        finally:
            ctx.pop()


class ActPdfQueryCountTests(unittest.TestCase):
    """CYCLE-2-3-HOTFIX F-002: Uzbek act PDF rendering must not scale its
    query count with the number of act lines (the name_uz resolution walks
    item.request_item.spare_part per line — that chain must be eager-loaded).
    """

    def setUp(self):
        reset_db()
        self.admin_id = create_admin()
        self.org_id = create_org()

    def _make_act(self, n_items, seq):
        with app.app_context():
            req = SparePartRequest(request_date=date(2026, 7, 1),
                                   organization_id=self.org_id,
                                   status='issued', created_by=self.admin_id)
            db.session.add(req)
            db.session.flush()
            act = SparePartWriteOffAct(
                act_number='SPW-2026-{:05d}'.format(seq),
                request_id=req.id, organization_id=self.org_id,
                issued_date=date(2026, 7, 2), issued_by=self.admin_id)
            db.session.add(act)
            db.session.flush()
            for i in range(n_items):
                part = SparePart(name='Деталь {} {}'.format(seq, i),
                                 name_uz='Эҳтиёт қисм {} {}'.format(seq, i),
                                 status='active')
                db.session.add(part)
                db.session.flush()
                item = SparePartRequestItem(
                    request_id=req.id, spare_part_id=part.id,
                    name=part.name, quantity=2, unit='dona',
                    price=15000, price_status='confirmed')
                db.session.add(item)
                db.session.flush()
                db.session.add(SparePartWriteOffActItem(
                    act_id=act.id, request_item_id=item.id, name=item.name,
                    quantity=item.quantity, unit=item.unit,
                    price=item.price, total=item.price * item.quantity))
            db.session.commit()
            return act.id

    def _pdf_query_count(self, act_id):
        queries = []

        def count(conn, cursor, statement, parameters, context, executemany):
            queries.append(statement)

        with app.app_context():
            engine = db.engine
        client = app.test_client()
        login(client, self.admin_id)
        event.listen(engine, 'before_cursor_execute', count)
        try:
            resp = client.get('/spare-parts/acts/{}/pdf?lang=uz'.format(act_id))
        finally:
            event.remove(engine, 'before_cursor_execute', count)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.mimetype, 'application/pdf')
        return len(queries)

    def test_uzbek_act_pdf_query_count_is_bounded_and_non_scaling(self):
        act_1 = self._make_act(1, seq=1)
        act_10 = self._make_act(10, seq=2)
        q1 = self._pdf_query_count(act_1)
        q10 = self._pdf_query_count(act_10)
        # Same fixed number of queries whether the act has 1 or 10 lines
        # (a per-line lazy load would add ~2 queries per extra line), and
        # comfortably under a small absolute ceiling.
        self.assertEqual(q1, q10,
                         'PDF query count scales with item count: '
                         '{} vs {}'.format(q1, q10))
        self.assertLessEqual(q10, 10, 'PDF query count too high: {}'.format(q10))


if __name__ == '__main__':
    unittest.main()
