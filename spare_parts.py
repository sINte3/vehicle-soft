from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, g
from flask_login import login_required, current_user
from datetime import datetime, date

from models import db, SparePart, SparePartRequest, SparePartRequestItem, Organization, Equipment, module_required

spare_parts_bp = Blueprint('spare_parts', __name__, url_prefix='/spare-parts')

STATUS_LABELS = {
    'draft':     {'uz': 'Чорнов',       'ru': 'Черновик'},
    'submitted': {'uz': 'Юборилган',    'ru': 'Отправлено'},
    'approved':  {'uz': 'Тасдиқланган', 'ru': 'Утверждено'},
    'rejected':  {'uz': 'Рад этилган',  'ru': 'Отклонено'},
}
STATUS_COLORS = {
    'draft':     'var(--text2)',
    'submitted': 'var(--info)',
    'approved':  'var(--accent)',
    'rejected':  'var(--danger)',
}


@spare_parts_bp.route('/')
@module_required('spare_parts')
def index():
    lang = getattr(g, 'lang', 'uz')
    status_filter = request.args.get('status', '')
    org_id = request.args.get('org_id', type=int)
    date_from_s = request.args.get('date_from', '')
    date_to_s = request.args.get('date_to', '')

    q = SparePartRequest.query
    if status_filter:
        q = q.filter(SparePartRequest.status == status_filter)
    if org_id:
        q = q.filter(SparePartRequest.organization_id == org_id)
    if date_from_s:
        try:
            q = q.filter(SparePartRequest.request_date >= datetime.strptime(date_from_s, '%Y-%m-%d').date())
        except ValueError:
            pass
    if date_to_s:
        try:
            q = q.filter(SparePartRequest.request_date <= datetime.strptime(date_to_s, '%Y-%m-%d').date())
        except ValueError:
            pass

    requests_list = q.order_by(SparePartRequest.created_at.desc()).all()

    counts = {}
    for s in STATUS_LABELS:
        counts[s] = SparePartRequest.query.filter_by(status=s).count()
    counts['all'] = SparePartRequest.query.count()

    organizations = Organization.query.order_by(Organization.sort_order).all()
    return render_template('spare_parts_list.html',
                           requests_list=requests_list,
                           status_filter=status_filter,
                           org_id=org_id,
                           date_from_s=date_from_s,
                           date_to_s=date_to_s,
                           counts=counts,
                           organizations=organizations,
                           status_labels=STATUS_LABELS,
                           status_colors=STATUS_COLORS,
                           lang=lang)


@spare_parts_bp.route('/new')
@module_required('spare_parts')
def new_request():
    if not current_user.can_edit:
        flash('Сизда ҳуқуқ йўқ', 'warning')
        return redirect(url_for('spare_parts.index'))
    organizations = Organization.query.order_by(Organization.sort_order).all()
    all_equipment = Equipment.query.filter_by(is_active=True).order_by(Equipment.name).all()
    return render_template('spare_part_form.html',
                           req=None,
                           today=date.today().isoformat(),
                           organizations=organizations,
                           all_equipment=all_equipment)


@spare_parts_bp.route('/save', methods=['POST'])
@module_required('spare_parts')
def save_request():
    if not current_user.can_edit:
        abort(403)
    action = request.form.get('action', 'draft')
    status = 'submitted' if action == 'submit' else 'draft'

    req_date_s = request.form.get('request_date', date.today().isoformat())
    try:
        req_date = datetime.strptime(req_date_s, '%Y-%m-%d').date()
    except ValueError:
        req_date = date.today()

    org_id = request.form.get('organization_id', type=int)
    eq_id = request.form.get('equipment_id', type=int) or None
    note = request.form.get('note', '').strip()

    spr = SparePartRequest(
        request_date=req_date,
        organization_id=org_id,
        equipment_id=eq_id,
        status=status,
        note=note,
        created_by=current_user.id,
    )
    db.session.add(spr)
    db.session.flush()

    names = request.form.getlist('item_name')
    parts = request.form.getlist('item_part_number')
    qtys = request.form.getlist('item_quantity')
    units = request.form.getlist('item_unit')
    notes = request.form.getlist('item_note')

    for i, name in enumerate(names):
        name = name.strip()
        if not name:
            continue
        try:
            qty = float(qtys[i]) if i < len(qtys) and qtys[i] else 1
        except ValueError:
            qty = 1
        item = SparePartRequestItem(
            request_id=spr.id,
            name=name,
            part_number=parts[i].strip() if i < len(parts) else '',
            quantity=qty,
            unit=units[i].strip() if i < len(units) else 'dona',
            note=notes[i].strip() if i < len(notes) else '',
        )
        db.session.add(item)

    db.session.commit()
    if status == 'submitted':
        flash('Сўров юборилди', 'success')
    else:
        flash('Чорнов сақланди', 'info')
    return redirect(url_for('spare_parts.detail', rid=spr.id))


@spare_parts_bp.route('/<int:rid>')
@module_required('spare_parts')
def detail(rid):
    lang = getattr(g, 'lang', 'uz')
    spr = SparePartRequest.query.get_or_404(rid)
    return render_template('spare_part_detail.html',
                           req=spr,
                           status_labels=STATUS_LABELS,
                           status_colors=STATUS_COLORS,
                           lang=lang)


@spare_parts_bp.route('/<int:rid>/submit', methods=['POST'])
@module_required('spare_parts')
def submit_request(rid):
    spr = SparePartRequest.query.get_or_404(rid)
    if spr.status != 'draft' or spr.created_by != current_user.id:
        abort(403)
    spr.status = 'submitted'
    db.session.commit()
    flash('Сўров юборилди', 'success')
    return redirect(url_for('spare_parts.detail', rid=rid))


@spare_parts_bp.route('/<int:rid>/approve', methods=['POST'])
@module_required('spare_parts')
def approve_request(rid):
    if not current_user.can_edit:
        abort(403)
    spr = SparePartRequest.query.get_or_404(rid)
    if spr.status != 'submitted':
        abort(400)
    spr.status = 'approved'
    spr.reviewed_by = current_user.id
    spr.reviewed_at = datetime.utcnow()
    spr.review_comment = request.form.get('review_comment', '').strip()
    db.session.commit()
    flash('Сўров тасдиқланди', 'success')
    return redirect(url_for('spare_parts.detail', rid=rid))


@spare_parts_bp.route('/<int:rid>/reject', methods=['POST'])
@module_required('spare_parts')
def reject_request(rid):
    if not current_user.can_edit:
        abort(403)
    spr = SparePartRequest.query.get_or_404(rid)
    if spr.status != 'submitted':
        abort(400)
    spr.status = 'rejected'
    spr.reviewed_by = current_user.id
    spr.reviewed_at = datetime.utcnow()
    spr.review_comment = request.form.get('review_comment', '').strip()
    db.session.commit()
    flash('Сўров рад этилди', 'warning')
    return redirect(url_for('spare_parts.detail', rid=rid))


@spare_parts_bp.route('/catalog')
@module_required('spare_parts')
def catalog():
    if not current_user.is_admin:
        abort(403)
    parts = SparePart.query.order_by(SparePart.name).all()
    return render_template('spare_parts_catalog.html', parts=parts)


@spare_parts_bp.route('/catalog/save', methods=['POST'])
@module_required('spare_parts')
def catalog_save():
    if not current_user.is_admin:
        abort(403)
    pid = request.form.get('id', type=int)
    name = request.form.get('name', '').strip()
    part_number = request.form.get('part_number', '').strip()
    unit = request.form.get('unit', 'dona').strip()
    category = request.form.get('category', '').strip()

    if not name:
        flash('Номини киритинг', 'warning')
        return redirect(url_for('spare_parts.catalog'))

    if pid:
        part = SparePart.query.get_or_404(pid)
        part.name = name
        part.part_number = part_number
        part.unit = unit
        part.category = category
    else:
        part = SparePart(name=name, part_number=part_number, unit=unit, category=category)
        db.session.add(part)
    db.session.commit()
    flash('Сақланди', 'success')
    return redirect(url_for('spare_parts.catalog'))
