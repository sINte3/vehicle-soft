# -*- coding: utf-8 -*-
"""Work Orders module (WORK-ORDER-001) — Phase 1: list + create only.

Phase 1 intentionally implements only:
  GET  /work-orders                          — list with filters + counters
  GET  /work-orders/new                      — create form
  POST /work-orders/new                      — save new work order (always draft)
  GET  /api/work-orders/work-type-price      — JSON price/unit for a WorkType
  GET  /api/work-orders/equipment-by-org     — JSON equipment for an organization

Status transitions, detail/close pages, BOT003 notifications and dashboard KPIs
are deferred to later phases. Access patterns mirror spare_parts.py (org-scoped,
flash + redirect on validation error). Routes are guarded with @login_required;
role/ownership checks are inline per the TZ access matrix (section 3.2).
"""

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, abort, g, jsonify
)
from flask_login import login_required, current_user
from datetime import datetime, date
from sqlalchemy import func

from models import (
    db, WorkOrder, WorkOrderStatusHistory,
    Organization, Equipment, WorkType, Customer, User,
    ROLE_ADMIN, ROLE_OPERATOR, ROLE_MECHANIC,
    WO_STATUSES, WO_STATUS_DRAFT, WO_STATUS_ASSIGNED, WO_STATUS_IN_PROGRESS,
    WO_STATUS_DONE, WO_STATUS_CANCELLED,
    WO_STATUS_LABELS_RU, WO_STATUS_LABELS_UZ, WO_EVENT_STATUS_CHANGE,
)

work_orders_bp = Blueprint('work_orders', __name__)

# Statuses considered "open" / active for counters and the default list view.
WO_OPEN_STATUSES = (WO_STATUS_DRAFT, WO_STATUS_ASSIGNED, WO_STATUS_IN_PROGRESS)

# [REASON]: Colours reuse base.html CSS variables so badges match the rest of the
# UI (grey draft, blue assigned, amber in-progress, green done, red cancelled).
WO_STATUS_COLORS = {
    WO_STATUS_DRAFT:       'var(--text2)',
    WO_STATUS_ASSIGNED:    'var(--info)',
    WO_STATUS_IN_PROGRESS: 'var(--warn)',
    WO_STATUS_DONE:        'var(--accent)',
    WO_STATUS_CANCELLED:   'var(--danger)',
}

VALID_PAYMENT_TYPES = {'', 'naqd', 'bank', 'ichki', 'boshqa'}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _wo_lang():
    lang = getattr(g, 'lang', None)
    if not lang and getattr(current_user, 'is_authenticated', False):
        lang = getattr(current_user, 'language', None)
    return 'ru' if lang == 'ru' else 'uz'


def _wo_t(uz_text, ru_text):
    return ru_text if _wo_lang() == 'ru' else uz_text


def _wo_status_labels():
    return WO_STATUS_LABELS_RU if _wo_lang() == 'ru' else WO_STATUS_LABELS_UZ


def _wo_user_org_ids():
    """None for admin (sees all orgs), else the list of org ids the user manages."""
    return None if current_user.is_admin else current_user.get_org_ids()


def _wo_can_create():
    # [REASON]: TZ 3.2 — admin/operator/mechanic may create; viewer may only read.
    return current_user.role in (ROLE_ADMIN, ROLE_OPERATOR, ROLE_MECHANIC)


def _wo_user_orgs():
    org_ids = _wo_user_org_ids()
    # [REASON]: WORK-ORDER-001 — only surface organizations that own at least one
    # active piece of equipment; an org with no active equipment cannot host a work
    # order, so it should not appear in the org dropdowns. .any() emits an EXISTS.
    has_active_eq = Organization.equipment.any(Equipment.is_active == True)
    if org_ids is None:
        return (Organization.query
                .filter(has_active_eq)
                .order_by(Organization.sort_order).all())
    if not org_ids:
        return []
    return (Organization.query
            .filter(Organization.id.in_(org_ids))
            .filter(has_active_eq)
            .order_by(Organization.sort_order).all())


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), '%Y-%m-%d').date()
    except (TypeError, ValueError, AttributeError):
        return None


def _parse_float_or_none(value):
    s = (value or '').strip().replace(',', '.')
    if not s:
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _generate_wo_number(year: int) -> str:
    """Return the next WO-{year}-{00001} number.

    [REASON]: TZ 6.1 — sequential per year. `number` is UNIQUE so a rare race
    would raise IntegrityError on commit; acceptable for this low-concurrency app.
    """
    prefix = f"WO-{year}-"
    last = db.session.query(func.max(WorkOrder.number)).filter(
        WorkOrder.number.like(prefix + '%')
    ).scalar()
    seq = 1
    if last:
        try:
            seq = int(last.split('-')[-1]) + 1
        except (ValueError, IndexError):
            seq = 1
    return f"{prefix}{seq:05d}"


# ─── List ───────────────────────────────────────────────────────────────────

@work_orders_bp.route('/work-orders')
@login_required
def index():
    lang = _wo_lang()
    status_filter = request.args.get('status', '')
    org_id = request.args.get('org_id', type=int)
    date_from_s = request.args.get('date_from', '')
    date_to_s = request.args.get('date_to', '')

    user_org_ids = _wo_user_org_ids()

    q = WorkOrder.query
    if user_org_ids is not None:
        q = q.filter(WorkOrder.organization_id.in_(user_org_ids or [-1]))

    # status: '' = open/active (default), 'all' = every status, else exact match.
    if status_filter == '':
        q = q.filter(WorkOrder.status.in_(WO_OPEN_STATUSES))
    elif status_filter != 'all' and status_filter in WO_STATUSES:
        q = q.filter(WorkOrder.status == status_filter)

    if org_id:
        if not current_user.can_access_org(org_id):
            abort(403)
        q = q.filter(WorkOrder.organization_id == org_id)

    date_from = _parse_date(date_from_s)
    if date_from:
        q = q.filter(WorkOrder.planned_date >= date_from)
    date_to = _parse_date(date_to_s)
    if date_to:
        q = q.filter(WorkOrder.planned_date <= date_to)

    work_orders = q.order_by(WorkOrder.planned_date.desc(), WorkOrder.id.desc()).all()

    status_counts = _compute_status_counts(user_org_ids)

    return render_template(
        'work_orders_list.html',
        work_orders=work_orders,
        organizations=_wo_user_orgs(),
        filters={
            'status': status_filter,
            'org_id': org_id,
            'date_from': date_from_s,
            'date_to': date_to_s,
        },
        status_counts=status_counts,
        statuses=WO_STATUSES,
        status_labels=_wo_status_labels(),
        status_colors=WO_STATUS_COLORS,
        can_create=_wo_can_create(),
        lang=lang,
    )


def _compute_status_counts(user_org_ids):
    """Counts over the user's visible orders (independent of the page filters)."""
    today_iso = date.today().isoformat()

    def base():
        b = WorkOrder.query
        if user_org_ids is not None:
            b = b.filter(WorkOrder.organization_id.in_(user_org_ids or [-1]))
        return b

    total_open = base().filter(WorkOrder.status.in_(WO_OPEN_STATUSES)).count()
    in_progress = base().filter(WorkOrder.status == WO_STATUS_IN_PROGRESS).count()
    done_today = (base()
                  .filter(WorkOrder.status == WO_STATUS_DONE)
                  .filter(func.date(WorkOrder.closed_at) == today_iso)
                  .count())
    overdue = (base()
               .filter(WorkOrder.status.in_(WO_OPEN_STATUSES))
               .filter(WorkOrder.planned_date < date.today())
               .count())
    return {
        'total_open': total_open,
        'in_progress': in_progress,
        'done_today': done_today,
        'overdue': overdue,
    }


# ─── Create form ──────────────────────────────────────────────────────────────

@work_orders_bp.route('/work-orders/new', methods=['GET'])
@login_required
def new():
    if not _wo_can_create():
        abort(403)
    orgs = _wo_user_orgs()

    # [REASON]: TZ 8.2 — when the user manages exactly one org, preselect it and
    # render its equipment server-side; otherwise equipment loads via AJAX.
    initial_equipment = []
    if len(orgs) == 1:
        initial_equipment = (Equipment.query
                             .filter_by(organization_id=orgs[0].id, is_active=True)
                             .order_by(Equipment.name, Equipment.plate).all())

    mechanics = []
    if current_user.is_admin:
        mechanics = (User.query
                     .filter_by(role=ROLE_MECHANIC, is_active_user=True)
                     .order_by(User.full_name, User.username).all())

    return render_template(
        'work_order_form.html',
        organizations=orgs,
        initial_equipment=initial_equipment,
        work_types=WorkType.query.order_by(WorkType.name).all(),
        customers=Customer.query.order_by(Customer.name).all(),
        mechanics=mechanics,
        today=date.today().isoformat(),
        is_admin=current_user.is_admin,
        lang=_wo_lang(),
    )


@work_orders_bp.route('/work-orders/new', methods=['POST'])
@login_required
def create():
    if not _wo_can_create():
        abort(403)

    errors = []

    planned_date = _parse_date(request.form.get('planned_date', ''))
    if not planned_date:
        errors.append(_wo_t('Rejalashtirilgan sanani kiriting',
                            'Укажите плановую дату'))

    org_id = request.form.get('organization_id', type=int)
    if not org_id:
        errors.append(_wo_t('Tashkilotni tanlang', 'Выберите организацию'))
    elif not current_user.can_access_org(org_id):
        abort(403)

    eq_id = request.form.get('equipment_id', type=int)
    equipment = None
    if not eq_id:
        errors.append(_wo_t('Texnikani tanlang', 'Выберите технику'))
    else:
        equipment = Equipment.query.get(eq_id)
        if (not equipment or not equipment.is_active
                or (org_id and equipment.organization_id != org_id)):
            errors.append(_wo_t('Texnika tanlangan tashkilotga tegishli emas',
                                'Техника не относится к выбранной организации'))
            equipment = None

    work_type_id = request.form.get('work_type_id', type=int)
    work_type_text = request.form.get('work_type_text', '').strip()
    work_type = WorkType.query.get(work_type_id) if work_type_id else None
    if work_type_id and not work_type:
        # Stale/invalid id — treat as free text.
        work_type_id = None
    if not work_type and not work_type_text:
        errors.append(_wo_t('Ish turini tanlang yoki kiriting',
                            'Выберите или введите тип работы'))

    if errors:
        for msg in errors:
            flash(msg, 'warning')
        return redirect(url_for('work_orders.new'))

    # --- Resolve work type / pricing ---
    submitted_price = _parse_float_or_none(request.form.get('price', ''))
    submitted_unit = request.form.get('unit', '').strip()
    if work_type:
        # [REASON]: default_price is the catalog snapshot (for Phase 2 price_override
        # detection); price keeps any manual edit from the form, unit falls back to
        # the catalog default. work_type_text prefers the submitted label, else the
        # catalog name.
        default_price = work_type.default_price or 0.0
        price = submitted_price if submitted_price is not None else default_price
        unit = submitted_unit or work_type.default_unit or 'ga'
        work_type_text = work_type_text or work_type.name
    else:
        default_price = 0.0
        price = submitted_price if submitted_price is not None else 0.0
        unit = submitted_unit or 'ga'

    # --- Resolve customer ---
    customer_id = request.form.get('customer_id', type=int)
    customer_text = request.form.get('customer_text', '').strip()
    customer = Customer.query.get(customer_id) if customer_id else None
    if customer_id and not customer:
        customer_id = None
    if customer:
        customer_text = customer_text or customer.name

    payment_type = request.form.get('payment_type', '').strip()
    if payment_type not in VALID_PAYMENT_TYPES:
        payment_type = ''

    note = request.form.get('note', '').strip()
    planned_quantity = _parse_float_or_none(request.form.get('planned_quantity', ''))

    # --- assigned_to (admin only) ---
    assigned_to = None
    if current_user.is_admin:
        cand = request.form.get('assigned_to', type=int)
        if cand:
            mech = User.query.get(cand)
            if mech and mech.role == ROLE_MECHANIC:
                assigned_to = mech.id

    wo = WorkOrder(
        number=_generate_wo_number(planned_date.year),
        organization_id=org_id,
        equipment_id=eq_id,
        work_type_id=work_type.id if work_type else None,
        work_type_text=work_type_text,
        customer_id=customer.id if customer else None,
        customer_text=customer_text,
        assigned_to=assigned_to,
        created_by=current_user.id,
        status=WO_STATUS_DRAFT,
        planned_date=planned_date,
        unit=unit,
        planned_quantity=planned_quantity,
        default_price=default_price,
        price=price,
        payment_type=payment_type,
        note=note,
    )
    db.session.add(wo)
    db.session.flush()  # assign wo.id for the history row

    db.session.add(WorkOrderStatusHistory(
        work_order_id=wo.id,
        event_type=WO_EVENT_STATUS_CHANGE,
        old_value=None,
        new_value=WO_STATUS_DRAFT,
        changed_by=current_user.id,
        comment='Created',
    ))
    db.session.commit()

    flash(_wo_t('Buyurtma yaratildi', 'Наряд создан') + f' ({wo.number})', 'success')
    return redirect(url_for('work_orders.index'))


# ─── JSON APIs ────────────────────────────────────────────────────────────────

@work_orders_bp.route('/api/work-orders/work-type-price')
@login_required
def api_work_type_price():
    wid = request.args.get('work_type_id', type=int)
    wt = WorkType.query.get(wid) if wid else None
    if not wt:
        return jsonify({'price': 0, 'unit': ''})
    return jsonify({'price': wt.default_price or 0, 'unit': wt.default_unit or ''})


@work_orders_bp.route('/api/work-orders/equipment-by-org')
@login_required
def api_equipment_by_org():
    org_id = request.args.get('org_id', type=int)
    if not org_id or not current_user.can_access_org(org_id):
        return jsonify([])
    eqs = (Equipment.query
           .filter_by(organization_id=org_id, is_active=True)
           .order_by(Equipment.name, Equipment.plate).all())
    return jsonify([
        {
            'id': e.id,
            'name': e.name,
            'plate': e.plate or '',
            'category': e.category_display,
        }
        for e in eqs
    ])
