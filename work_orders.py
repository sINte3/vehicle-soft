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

import json

from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, abort, g, jsonify
)
from flask_login import login_required, current_user
from datetime import datetime, date
from sqlalchemy import func, text, or_

from models import (
    db, WorkOrder, WorkOrderStatusHistory,
    Organization, Equipment, WorkType, Customer, User,
    DailyRecord,
    ROLE_ADMIN, ROLE_OPERATOR, ROLE_MECHANIC,
    WO_STATUSES, WO_STATUS_DRAFT, WO_STATUS_ASSIGNED, WO_STATUS_IN_PROGRESS,
    WO_STATUS_DONE, WO_STATUS_CANCELLED,
    WO_STATUS_LABELS_RU, WO_STATUS_LABELS_UZ,
    WO_PAYMENT_TYPES_RU, WO_PAYMENT_TYPES_UZ,
    WO_EVENT_STATUS_CHANGE,
    WO_EVENT_PRICE_OVERRIDE,
    WO_EVENT_ASSIGNMENT_CHANGE,
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


def _wo_payment_labels():
    return WO_PAYMENT_TYPES_RU if _wo_lang() == 'ru' else WO_PAYMENT_TYPES_UZ


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


# ─── Phase 2 access-check helpers ───────────────────────────────────────────────

def _wo_can_start(wo):
    if wo.status not in (WO_STATUS_DRAFT, WO_STATUS_ASSIGNED):
        return False
    return (current_user.is_admin or
            wo.created_by == current_user.id or
            wo.assigned_to == current_user.id)


def _wo_can_close(wo):
    if wo.status != WO_STATUS_IN_PROGRESS:
        return False
    return (current_user.is_admin or
            wo.created_by == current_user.id or
            wo.assigned_to == current_user.id)


def _wo_can_cancel(wo):
    if wo.status in (WO_STATUS_DONE, WO_STATUS_CANCELLED):
        return False
    return (current_user.is_admin or
            (wo.status == WO_STATUS_DRAFT and wo.created_by == current_user.id))


def _wo_can_assign(wo):
    return (current_user.is_admin and
            wo.status not in (WO_STATUS_DONE, WO_STATUS_CANCELLED))


def _wo_outbox_insert(wo, event_type, target_user_id, target_telegram_id, payload_dict):
    """Insert one notification row into bot003_notification_outbox.

    [REASON]: bot003_notification_outbox.request_id is NOT NULL (designed for
    spare parts). We pass wo.id as request_id — safe because the bot reads
    context from payload_json, not by JOIN on request_id.
    available_at / created_at / updated_at are TEXT columns; use ISO strings.
    INSERT OR IGNORE prevents duplicate notifications on double-submit.
    """
    now_iso = datetime.utcnow().isoformat(timespec='seconds')
    dedupe = f"wo_{wo.id}_{event_type}_{target_user_id or 0}"
    db.session.execute(text(
        "INSERT OR IGNORE INTO bot003_notification_outbox "
        "(event_type, request_id, target_user_id, target_telegram_id, "
        " payload_json, dedupe_key, status, attempts, max_attempts, "
        " available_at, created_at, updated_at) "
        "VALUES (:et, :rid, :tuid, :ttid, :pj, :dk, "
        "        'pending', 0, 5, :now, :now, :now)"
    ), {
        'et':   event_type,
        'rid':  wo.id,
        'tuid': target_user_id,
        'ttid': str(target_telegram_id) if target_telegram_id else None,
        'pj':   json.dumps(payload_dict, ensure_ascii=False),
        'dk':   dedupe,
        'now':  now_iso,
    })


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

    # [REASON]: WORK-ORDER-001 TZ 3.4 — a mechanic only sees orders they created
    # or are assigned to (within their orgs), not every order in those orgs.
    if current_user.role == ROLE_MECHANIC:
        q = q.filter(or_(WorkOrder.assigned_to == current_user.id,
                         WorkOrder.created_by == current_user.id))

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
        # [REASON]: WORK-ORDER-001 TZ 3.4 — keep the header counters consistent
        # with the mechanic-scoped list (own/assigned orders only).
        if current_user.role == ROLE_MECHANIC:
            b = b.filter(or_(WorkOrder.assigned_to == current_user.id,
                             WorkOrder.created_by == current_user.id))
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


# ─── Phase 2: detail + status transitions ──────────────────────────────────────

@work_orders_bp.route('/work-orders/<int:wo_id>')
@login_required
def detail(wo_id):
    wo = WorkOrder.query.get_or_404(wo_id)
    if not current_user.can_access_org(wo.organization_id):
        abort(403)
    # [REASON]: WORK-ORDER-001 TZ 3.5 — a mechanic may open only their own or
    # assigned orders, even inside an organization they belong to.
    if current_user.role == ROLE_MECHANIC and not (
            wo.assigned_to == current_user.id or wo.created_by == current_user.id):
        abort(403)
    lang = _wo_lang()
    history = wo.history.order_by(WorkOrderStatusHistory.changed_at).all()
    mechanics = []
    if current_user.is_admin:
        mechanics = (User.query
                     .filter_by(role=ROLE_MECHANIC, is_active_user=True)
                     .order_by(User.full_name, User.username).all())
    return render_template(
        'work_order_detail.html',
        wo=wo,
        history=history,
        mechanics=mechanics,
        can_start=_wo_can_start(wo),
        can_close=_wo_can_close(wo),
        can_cancel=_wo_can_cancel(wo),
        can_assign=_wo_can_assign(wo),
        status_labels=_wo_status_labels(),
        status_colors=WO_STATUS_COLORS,
        payment_labels=_wo_payment_labels(),
        lang=lang,
    )


@work_orders_bp.route('/work-orders/<int:wo_id>/start', methods=['POST'])
@login_required
def start(wo_id):
    wo = WorkOrder.query.get_or_404(wo_id)
    if not current_user.can_access_org(wo.organization_id):
        abort(403)
    if not _wo_can_start(wo):
        flash(_wo_t('Bu amalni bajarish mumkin emas', 'Действие недоступно'), 'warning')
        return redirect(url_for('work_orders.detail', wo_id=wo_id))
    old_status = wo.status
    wo.status = WO_STATUS_IN_PROGRESS
    db.session.add(WorkOrderStatusHistory(
        work_order_id=wo.id,
        event_type=WO_EVENT_STATUS_CHANGE,
        old_value=old_status,
        new_value=WO_STATUS_IN_PROGRESS,
        changed_by=current_user.id,
        comment='Started',
    ))
    db.session.commit()
    flash(_wo_t('Buyurtma boshlandi', 'Наряд взят в работу'), 'success')
    return redirect(url_for('work_orders.detail', wo_id=wo_id))


@work_orders_bp.route('/work-orders/<int:wo_id>/close', methods=['GET', 'POST'])
@login_required
def close(wo_id):
    wo = WorkOrder.query.get_or_404(wo_id)
    if not current_user.can_access_org(wo.organization_id):
        abort(403)
    if not _wo_can_close(wo):
        flash(_wo_t('Bu amalni bajarish mumkin emas', 'Действие недоступно'), 'warning')
        return redirect(url_for('work_orders.detail', wo_id=wo_id))

    if request.method == 'GET':
        existing_count = DailyRecord.query.filter_by(
            equipment_id=wo.equipment_id,
            work_date=wo.planned_date,
        ).count()
        return render_template(
            'work_order_close.html',
            wo=wo,
            existing_records_count=existing_count,
            lang=_wo_lang(),
        )

    # --- POST: validate and commit ---
    actual_date = _parse_date(request.form.get('actual_date', '')) or wo.planned_date
    actual_quantity = _parse_float_or_none(request.form.get('actual_quantity', ''))
    if actual_quantity is None:
        flash(_wo_t('Haqiqiy hajmni kiriting', 'Укажите фактический объём'), 'warning')
        return redirect(url_for('work_orders.close', wo_id=wo_id))

    payment_type = request.form.get('payment_type', wo.payment_type or '').strip()
    if payment_type not in VALID_PAYMENT_TYPES:
        payment_type = ''
    close_note = request.form.get('note', '').strip()

    # Warn (non-blocking) if duplicate DailyRecord exists for actual_date
    existing_count = DailyRecord.query.filter_by(
        equipment_id=wo.equipment_id,
        work_date=actual_date,
    ).count()
    if existing_count > 0:
        flash(
            _wo_t(
                f'Diqqat: bu texnika uchun {existing_count} ta yozuv mavjud.',
                f'Внимание: для этой техники уже есть {existing_count} записей за эту дату.',
            ),
            'warning',
        )

    # Compute amounts
    total = (actual_quantity or 0.0) * (wo.price or 0.0)
    amount_cash     = total if payment_type == 'naqd'  else 0.0
    amount_transfer = total if payment_type == 'bank'  else 0.0
    amount_internal = total if payment_type == 'ichki' else 0.0
    amount_other    = total if payment_type not in ('naqd', 'bank', 'ichki') else 0.0

    # Next line_index for (equipment_id, actual_date)
    max_idx = db.session.query(func.max(DailyRecord.line_index)).filter(
        DailyRecord.equipment_id == wo.equipment_id,
        DailyRecord.work_date == actual_date,
    ).scalar()
    next_idx = (max_idx + 1) if max_idx is not None else 0

    # Create DailyRecord
    dr = DailyRecord(
        work_date=actual_date,
        equipment_id=wo.equipment_id,
        line_index=next_idx,
        status='working',
        work_type=wo.work_type_text,
        customer=wo.customer_text,
        unit=wo.unit,
        quantity=actual_quantity,
        price=wo.price,
        payment_type=payment_type,
        amount_cash=amount_cash,
        amount_transfer=amount_transfer,
        amount_internal=amount_internal,
        amount_other=amount_other,
        note=f'Buyurtma {wo.number}',
        created_by=current_user.id,
        work_order_id=wo.id,
    )
    db.session.add(dr)
    db.session.flush()  # get dr.id before setting back-reference

    # Update work order
    old_status = wo.status
    wo.actual_date = actual_date
    wo.actual_quantity = actual_quantity
    wo.payment_type = payment_type
    wo.status = WO_STATUS_DONE
    wo.daily_record_id = dr.id
    wo.closed_at = datetime.utcnow()
    if close_note:
        wo.note = (wo.note + '\n' + close_note).strip() if wo.note else close_note

    # History
    db.session.add(WorkOrderStatusHistory(
        work_order_id=wo.id,
        event_type=WO_EVENT_STATUS_CHANGE,
        old_value=old_status,
        new_value=WO_STATUS_DONE,
        changed_by=current_user.id,
        comment=f'Closed. qty={actual_quantity} {wo.unit} date={actual_date}',
    ))

    # [REASON]: WORK-ORDER-001 TZ 5 — record when the closing price differs from
    # the catalog default so manual price overrides are auditable in the timeline.
    if abs((wo.price or 0.0) - (wo.default_price or 0.0)) > 0.01:
        db.session.add(WorkOrderStatusHistory(
            work_order_id=wo.id,
            event_type=WO_EVENT_PRICE_OVERRIDE,
            old_value=str(wo.default_price),
            new_value=str(wo.price),
            changed_by=current_user.id,
            comment=f'price_override: {wo.default_price} -> {wo.price}',
        ))

    # BOT003: notify admins and creator
    payload = {
        'wo_number': wo.number,
        'work_type': wo.work_type_text,
        'equipment': wo.equipment.name,
        'actual_qty': actual_quantity,
        'unit': wo.unit,
        'actual_date': str(actual_date),
        'closed_by': current_user.username,
    }
    for admin_user in User.query.filter_by(role=ROLE_ADMIN, is_active_user=True).all():
        if admin_user.telegram_id:
            _wo_outbox_insert(wo, 'wo_closed', admin_user.id, admin_user.telegram_id, payload)
    creator = User.query.get(wo.created_by)
    if creator and not creator.is_admin and creator.telegram_id:
        _wo_outbox_insert(wo, 'wo_closed', creator.id, creator.telegram_id, payload)

    db.session.commit()
    flash(
        _wo_t(
            f'Buyurtma {wo.number} yopildi.',
            f'Наряд {wo.number} закрыт. Запись в отчёте создана.',
        ),
        'success',
    )
    return redirect(url_for('work_orders.detail', wo_id=wo_id))


@work_orders_bp.route('/work-orders/<int:wo_id>/cancel', methods=['POST'])
@login_required
def cancel(wo_id):
    wo = WorkOrder.query.get_or_404(wo_id)
    if not current_user.can_access_org(wo.organization_id):
        abort(403)
    if not _wo_can_cancel(wo):
        flash(_wo_t('Bu amalni bajarish mumkin emas', 'Действие недоступно'), 'warning')
        return redirect(url_for('work_orders.detail', wo_id=wo_id))

    old_status = wo.status
    wo.status = WO_STATUS_CANCELLED
    db.session.add(WorkOrderStatusHistory(
        work_order_id=wo.id,
        event_type=WO_EVENT_STATUS_CHANGE,
        old_value=old_status,
        new_value=WO_STATUS_CANCELLED,
        changed_by=current_user.id,
        comment='Cancelled',
    ))

    # BOT003: notify creator
    creator = User.query.get(wo.created_by)
    if creator and creator.telegram_id:
        _wo_outbox_insert(wo, 'wo_cancelled', creator.id, creator.telegram_id, {
            'wo_number': wo.number,
            'equipment': wo.equipment.name,
            'cancelled_by': current_user.username,
        })

    db.session.commit()
    flash(_wo_t(f'Buyurtma {wo.number} bekor qilindi', f'Наряд {wo.number} отменён'), 'warning')
    return redirect(url_for('work_orders.index'))


@work_orders_bp.route('/work-orders/<int:wo_id>/assign', methods=['POST'])
@login_required
def assign(wo_id):
    if not current_user.is_admin:
        abort(403)
    wo = WorkOrder.query.get_or_404(wo_id)
    if wo.status in (WO_STATUS_DONE, WO_STATUS_CANCELLED):
        flash(_wo_t('Bu buyurtmani tayinlab bolmaydi', 'Наряд закрыт, назначение невозможно'), 'warning')
        return redirect(url_for('work_orders.detail', wo_id=wo_id))

    mechanic_id = request.form.get('assigned_to', type=int)
    if not mechanic_id:
        flash(_wo_t('Mexanikni tanlang', 'Выберите механика'), 'warning')
        return redirect(url_for('work_orders.detail', wo_id=wo_id))
    mechanic = User.query.get(mechanic_id)
    if not mechanic or mechanic.role != ROLE_MECHANIC:
        flash(_wo_t('Mexanik topilmadi', 'Механик не найден'), 'warning')
        return redirect(url_for('work_orders.detail', wo_id=wo_id))

    old_assigned = wo.assigned_to
    wo.assigned_to = mechanic.id
    if wo.status == WO_STATUS_DRAFT:
        wo.status = WO_STATUS_ASSIGNED
        db.session.add(WorkOrderStatusHistory(
            work_order_id=wo.id,
            event_type=WO_EVENT_STATUS_CHANGE,
            old_value=WO_STATUS_DRAFT,
            new_value=WO_STATUS_ASSIGNED,
            changed_by=current_user.id,
            comment=f'Assigned to {mechanic.username}',
        ))
    db.session.add(WorkOrderStatusHistory(
        work_order_id=wo.id,
        event_type=WO_EVENT_ASSIGNMENT_CHANGE,
        old_value=str(old_assigned) if old_assigned else None,
        new_value=str(mechanic.id),
        changed_by=current_user.id,
        comment=f'Mechanic: {mechanic.full_name or mechanic.username}',
    ))
    db.session.commit()
    flash(
        _wo_t(
            f'{mechanic.full_name or mechanic.username} tayinlandi',
            f'Механик {mechanic.full_name or mechanic.username} назначен',
        ),
        'success',
    )
    return redirect(url_for('work_orders.detail', wo_id=wo_id))
