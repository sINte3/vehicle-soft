"""
fuel_routes.py — АЗС модуль v2
Flask Blueprint: /fuel/* и /api/fuel_*

Логика:
  Склад (FuelWarehouse) = организация.
  У склада — несколько АЗС (FuelStation2), каждая с topaz_id.
  Агент Топаз присылает транзакции по topaz_id → списываем со склада.
  Баланс = НачОстаток + Приходы - Расходы (транзакции).
"""

from flask import (Blueprint, render_template, request, redirect,
                   url_for, flash, jsonify, abort, current_app, g)
from flask_login import login_required, current_user
from datetime import datetime, date, timedelta
from sqlalchemy import func
from types import SimpleNamespace

from models import (
    db, Organization,
    FuelWarehouse, FuelStation2, FuelInitialBalance,
    FuelReceipt2, FuelTransaction2, FuelSyncLog2,
    module_required,
)

from sec003a_ext import log_audit

fuel_bp = Blueprint('fuel', __name__, url_prefix='/fuel')


def fuel_t(uz, ru):
    # [REASON]: Local flash-message helper for route-level flash() messages.
    # Prefer the persisted user language because POST redirects can otherwise
    # be confusing during module-level UI tests. Fall back to g.lang.
    lang = getattr(g, 'lang', 'uz') or 'uz'
    try:
        if current_user.is_authenticated:
            lang = getattr(current_user, 'language', lang) or lang
    except Exception:
        pass
    return ru if lang == 'ru' else uz



TOPAZ_AUDIT_ACTOR = SimpleNamespace(
    id=None,
    username='system/topaz_agent',
    full_name='Topaz Fuel Agent',
    role='system',
)


def _iso_date(value):
    return value.isoformat() if value else None


def _fuel_warehouse_snapshot(warehouse):
    if not warehouse:
        return None
    return {
        'id': getattr(warehouse, 'id', None),
        'name': getattr(warehouse, 'name', '') or '',
        'organization_id': getattr(warehouse, 'organization_id', None),
        'notes': getattr(warehouse, 'notes', '') or '',
    }


def _fuel_station_snapshot(station):
    if not station:
        return None
    return {
        'id': getattr(station, 'id', None),
        'name': getattr(station, 'name', '') or '',
        'topaz_id': getattr(station, 'topaz_id', None),
        'warehouse_id': getattr(station, 'warehouse_id', None),
        'is_active': bool(getattr(station, 'is_active', False)),
    }


def _fuel_initial_balance_snapshot(balance):
    if not balance:
        return None
    return {
        'id': getattr(balance, 'id', None),
        'warehouse_id': getattr(balance, 'warehouse_id', None),
        'fuel_type': getattr(balance, 'fuel_type', '') or '',
        'quantity': getattr(balance, 'quantity', 0) or 0,
        'balance_date': _iso_date(getattr(balance, 'balance_date', None)),
        'note': getattr(balance, 'note', '') or '',
    }


def _fuel_receipt_snapshot(receipt):
    if not receipt:
        return None
    quantity = getattr(receipt, 'quantity', 0) or 0
    price = getattr(receipt, 'price_per_liter', 0) or 0
    return {
        'id': getattr(receipt, 'id', None),
        'warehouse_id': getattr(receipt, 'warehouse_id', None),
        'receipt_date': _iso_date(getattr(receipt, 'receipt_date', None)),
        'fuel_type': getattr(receipt, 'fuel_type', '') or '',
        'quantity': quantity,
        'price_per_liter': price,
        'amount': quantity * price,
        'supplier': getattr(receipt, 'supplier', '') or '',
        'doc_number': getattr(receipt, 'doc_number', '') or '',
        'note': getattr(receipt, 'note', '') or '',
    }


def _audit_fuel(action, entity_type='', entity_id=None, entity_label='', before=None,
                after=None, changes=None, status='ok', description='', actor_user=None):
    log_audit(
        db,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        entity_label=entity_label,
        module='fuel',
        before=before,
        after=after,
        changes=changes,
        status=status,
        description=description,
        actor_user=actor_user,
    )


# [REASON]: Token is no longer hardcoded; read per-request from app config which
# reads FUEL_API_TOKEN from the environment. If not configured, all sync requests
# are rejected (deny-all safe default). See docs/DEPLOYMENT_SECURITY.md.
FUEL_TYPES = ['ДТ', 'АИ-80', 'АИ-91', 'АИ-92', 'АИ-95', 'Бензин']


# ─── Helpers ─────────────────────────────────────────────────────────

def admin_required_fuel(f):
    from functools import wraps
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated


def get_warehouse_balance(warehouse_id, fuel_type=None):
    """
    Возвращает dict {fuel_type: current_liters}.
    current = initial_balance + receipts - transactions  (всё после initial.balance_date).
    """
    # Определяем виды топлива для данного склада
    fuel_types_q = (db.session.query(FuelInitialBalance.fuel_type)
                    .filter_by(warehouse_id=warehouse_id)
                    .distinct().all())
    fuel_types = [r[0] for r in fuel_types_q] or ['ДТ']

    if fuel_type:
        fuel_types = [fuel_type] if fuel_type in fuel_types else []

    result = {}
    for ft in fuel_types:
        # Начальный остаток
        ib = (FuelInitialBalance.query
              .filter_by(warehouse_id=warehouse_id, fuel_type=ft)
              .first())
        if not ib:
            result[ft] = None   # не задан начальный остаток
            continue

        since = ib.balance_date

        # Приходы после since
        receipts = (db.session.query(func.coalesce(func.sum(FuelReceipt2.quantity), 0))
                    .filter(FuelReceipt2.warehouse_id == warehouse_id,
                            FuelReceipt2.fuel_type == ft,
                            FuelReceipt2.receipt_date >= since)
                    .scalar())

        # Расходы (через АЗС) после since
        expenses = (db.session.query(func.coalesce(func.sum(FuelTransaction2.quantity), 0))
                    .join(FuelStation2)
                    .filter(FuelStation2.warehouse_id == warehouse_id,
                            FuelTransaction2.fuel_type == ft,
                            FuelTransaction2.txn_datetime >= datetime.combine(since, datetime.min.time()))
                    .scalar())

        result[ft] = round(ib.quantity + receipts - expenses, 2)

    return result


def get_all_balances():
    """Балансы всех складов для дашборда. Returns list of dicts."""
    warehouses = FuelWarehouse.query.order_by(FuelWarehouse.name).all()
    rows = []
    for wh in warehouses:
        balances = get_warehouse_balance(wh.id)
        last_txn = (FuelTransaction2.query
                    .join(FuelStation2)
                    .filter(FuelStation2.warehouse_id == wh.id)
                    .order_by(FuelTransaction2.txn_datetime.desc())
                    .first())
        today_expense = (db.session.query(func.coalesce(func.sum(FuelTransaction2.quantity), 0))
                         .join(FuelStation2)
                         .filter(FuelStation2.warehouse_id == wh.id,
                                 func.date(FuelTransaction2.txn_datetime) == date.today())
                         .scalar())
        rows.append({
            'warehouse': wh,
            'balances': balances,
            'last_txn': last_txn,
            'today_expense': today_expense,
            'stations': wh.stations.filter_by(is_active=True).count(),
        })
    return rows


# ─── Dashboard ────────────────────────────────────────────────────────

@fuel_bp.route('/')
@module_required('fuel')
def dashboard():
    balance_rows = get_all_balances()

    # Последняя синхронизация
    last_sync = (FuelSyncLog2.query
                 .filter_by(status='ok')
                 .order_by(FuelSyncLog2.synced_at.desc())
                 .first())

    # Последние 30 транзакций
    recent_txns = (FuelTransaction2.query
                   .join(FuelStation2)
                   .order_by(FuelTransaction2.txn_datetime.desc())
                   .limit(30).all())

    # Статистика за сегодня
    today_total = (db.session.query(func.coalesce(func.sum(FuelTransaction2.quantity), 0))
                   .filter(func.date(FuelTransaction2.txn_datetime) == date.today())
                   .scalar())

    return render_template('fuel/dashboard.html',
                           balance_rows=balance_rows,
                           last_sync=last_sync,
                           recent_txns=recent_txns,
                           today_total=today_total,
                           fuel_types=FUEL_TYPES)


# ─── Warehouses ───────────────────────────────────────────────────────

@fuel_bp.route('/warehouses')
@module_required('fuel')
@admin_required_fuel
def warehouses():
    whs = (FuelWarehouse.query
           .outerjoin(Organization)
           .order_by(FuelWarehouse.name).all())
    orgs = Organization.query.order_by(Organization.sort_order).all()
    edit_id = request.args.get('edit_id', type=int)
    edit_warehouse = None
    if edit_id:
        edit_warehouse = FuelWarehouse.query.get(edit_id)
    warehouse_delete_info = {}
    for wh in whs:
        linked = {
            'stations_count': FuelStation2.query.filter_by(warehouse_id=wh.id).count(),
            'receipts_count': FuelReceipt2.query.filter_by(warehouse_id=wh.id).count(),
            'initial_balances_count': FuelInitialBalance.query.filter_by(warehouse_id=wh.id).count(),
        }
        warehouse_delete_info[wh.id] = {
            'can_delete': not any(linked.values()),
            'linked_total': sum(linked.values()),
            'linked': linked,
        }
    return render_template('fuel/warehouses.html', warehouses=whs,
                           organizations=orgs, fuel_types=FUEL_TYPES,
                           edit_warehouse=edit_warehouse,
                           warehouse_delete_info=warehouse_delete_info)


@fuel_bp.route('/warehouses/save', methods=['POST'])
@module_required('fuel')
@admin_required_fuel
def save_warehouse():
    wid = request.form.get('id', type=int)
    name = request.form.get('name', '').strip()
    org_id = request.form.get('organization_id', type=int)
    notes = request.form.get('notes', '').strip()

    if not name:
        flash(fuel_t('Омбор номини киритинг', 'Введите название склада'), 'warning')
        return redirect(url_for('fuel.warehouses'))

    created = False
    before = None
    if wid:
        wh = FuelWarehouse.query.get_or_404(wid)
        before = _fuel_warehouse_snapshot(wh)
        wh.name = name
        wh.organization_id = org_id or None
        wh.notes = notes
    else:
        wh = FuelWarehouse(name=name, organization_id=org_id or None, notes=notes)
        db.session.add(wh)
        created = True

    db.session.flush()
    after = _fuel_warehouse_snapshot(wh)
    _audit_fuel(
        'fuel_warehouse_created' if created else 'fuel_warehouse_updated',
        entity_type='fuel_warehouse',
        entity_id=wh.id,
        entity_label=wh.name,
        before=before,
        after=after,
        description='Fuel warehouse saved',
    )
    db.session.commit()
    flash(fuel_t('Омбор сақланди', 'Склад сохранён'), 'success')
    return redirect(url_for('fuel.warehouses'))


@fuel_bp.route('/warehouses/delete/<int:wid>', methods=['POST'])
@module_required('fuel')
@admin_required_fuel
def delete_warehouse(wid):
    wh = FuelWarehouse.query.get_or_404(wid)
    before = _fuel_warehouse_snapshot(wh)
    label = wh.name
    linked = {
        'stations_count': FuelStation2.query.filter_by(warehouse_id=wid).count(),
        'receipts_count': FuelReceipt2.query.filter_by(warehouse_id=wid).count(),
        'initial_balances_count': FuelInitialBalance.query.filter_by(warehouse_id=wid).count(),
    }
    if any(linked.values()):
        _audit_fuel(
            'fuel_warehouse_delete_blocked',
            entity_type='fuel_warehouse',
            entity_id=wh.id,
            entity_label=label,
            before=before,
            after=linked,
            description='Fuel warehouse delete blocked because linked records exist',
        )
        db.session.commit()
        flash(fuel_t('Омбор ўчирилмади: боғланган маълумотлар мавжуд',
                     'Склад не удалён: есть связанные данные'), 'warning')
        return redirect(url_for('fuel.warehouses'))
    _audit_fuel(
        'fuel_warehouse_deleted',
        entity_type='fuel_warehouse',
        entity_id=wh.id,
        entity_label=label,
        before=before,
        description='Fuel warehouse deleted',
    )
    db.session.delete(wh)
    db.session.commit()
    flash(fuel_t('Омбор ўчирилди', 'Склад удалён'), 'warning')
    return redirect(url_for('fuel.warehouses'))


# ─── Initial Balance ──────────────────────────────────────────────────

@fuel_bp.route('/initial-balance', methods=['GET', 'POST'])
@module_required('fuel')
@admin_required_fuel
def initial_balance():
    warehouses = FuelWarehouse.query.order_by(FuelWarehouse.name).all()
    existing = {}
    for wh in warehouses:
        existing[wh.id] = {ib.fuel_type: ib
                           for ib in wh.initial_balances.all()}
    return render_template('fuel/initial_balance.html',
                           warehouses=warehouses, existing=existing,
                           fuel_types=FUEL_TYPES, today=date.today())


@fuel_bp.route('/initial-balance/save', methods=['POST'])
@module_required('fuel')
@admin_required_fuel
def save_initial_balance():
    warehouse_id = request.form.get('warehouse_id', type=int)
    fuel_type = request.form.get('fuel_type', 'ДТ').strip()
    quantity = request.form.get('quantity', type=float)
    balance_date_s = request.form.get('balance_date', '')
    note = request.form.get('note', '').strip()

    if not warehouse_id or quantity is None:
        flash(fuel_t('Барча майдонларни тўлдиринг', 'Заполните все поля'), 'warning')
        return redirect(url_for('fuel.initial_balance'))

    try:
        balance_date = datetime.strptime(balance_date_s, '%Y-%m-%d').date()
    except ValueError:
        balance_date = date.today()

    existing = (FuelInitialBalance.query
                .filter_by(warehouse_id=warehouse_id, fuel_type=fuel_type).first())
    created = False
    before = _fuel_initial_balance_snapshot(existing)
    if existing:
        ib = existing
        ib.quantity = quantity
        ib.balance_date = balance_date
        ib.note = note
    else:
        ib = FuelInitialBalance(warehouse_id=warehouse_id, fuel_type=fuel_type,
                                quantity=quantity, balance_date=balance_date,
                                note=note, created_by=current_user.id)
        db.session.add(ib)
        created = True

    db.session.flush()
    after = _fuel_initial_balance_snapshot(ib)
    _audit_fuel(
        'fuel_initial_balance_saved',
        entity_type='fuel_initial_balance',
        entity_id=ib.id,
        entity_label=f'{fuel_type} / warehouse {warehouse_id}',
        before=before,
        after=after,
        changes={'created': created},
        description='Fuel initial balance saved',
    )
    db.session.commit()
    flash(fuel_t('Бошланғич қолдиқ сақланди', 'Начальный остаток сохранён'), 'success')
    return redirect(url_for('fuel.initial_balance'))


# ─── Receipts (Приходы) ───────────────────────────────────────────────

@fuel_bp.route('/receipts')
@module_required('fuel')
def receipts():
    d_from_s = request.args.get('date_from', '')
    d_to_s   = request.args.get('date_to', '')
    wh_id    = request.args.get('warehouse_id', type=int)

    today = date.today()
    try:
        d_from = datetime.strptime(d_from_s, '%Y-%m-%d').date()
    except ValueError:
        d_from = today.replace(day=1)
    try:
        d_to = datetime.strptime(d_to_s, '%Y-%m-%d').date()
    except ValueError:
        d_to = today

    q = FuelReceipt2.query.filter(
        FuelReceipt2.receipt_date >= d_from,
        FuelReceipt2.receipt_date <= d_to,
    )
    if wh_id:
        q = q.filter(FuelReceipt2.warehouse_id == wh_id)
    items = q.order_by(FuelReceipt2.receipt_date.desc(), FuelReceipt2.id.desc()).all()

    warehouses = FuelWarehouse.query.order_by(FuelWarehouse.name).all()
    total_qty = sum(r.quantity for r in items)
    return render_template('fuel/receipts.html',
                           items=items, warehouses=warehouses,
                           d_from=d_from, d_to=d_to,
                           selected_wh_id=wh_id,
                           fuel_types=FUEL_TYPES, total_qty=total_qty,
                           today=today)


@fuel_bp.route('/receipts/save', methods=['POST'])
@module_required('fuel')
def save_receipt():
    if not current_user.can_edit:
        abort(403)
    rid = request.form.get('id', type=int)
    warehouse_id = request.form.get('warehouse_id', type=int)
    fuel_type = request.form.get('fuel_type', 'ДТ').strip()
    quantity = request.form.get('quantity', type=float)
    price = request.form.get('price_per_liter', type=float) or 0
    supplier = request.form.get('supplier', '').strip()
    doc_number = request.form.get('doc_number', '').strip()
    note = request.form.get('note', '').strip()
    date_s = request.form.get('receipt_date', '')
    try:
        receipt_date = datetime.strptime(date_s, '%Y-%m-%d').date()
    except ValueError:
        receipt_date = date.today()

    if not warehouse_id or not quantity:
        flash(fuel_t('Мажбурий майдонларни тўлдиринг', 'Заполните обязательные поля'), 'warning')
        return redirect(url_for('fuel.receipts'))

    created = False
    before = None
    if rid:
        r = FuelReceipt2.query.get_or_404(rid)
        before = _fuel_receipt_snapshot(r)
        r.warehouse_id = warehouse_id
        r.fuel_type = fuel_type
        r.quantity = quantity
        r.price_per_liter = price
        r.supplier = supplier
        r.doc_number = doc_number
        r.note = note
        r.receipt_date = receipt_date
    else:
        r = FuelReceipt2(
            warehouse_id=warehouse_id, fuel_type=fuel_type, quantity=quantity,
            price_per_liter=price, supplier=supplier, doc_number=doc_number,
            note=note, receipt_date=receipt_date, created_by=current_user.id,
        )
        db.session.add(r)
        created = True

    db.session.flush()
    after = _fuel_receipt_snapshot(r)
    _audit_fuel(
        'fuel_receipt_created' if created else 'fuel_receipt_updated',
        entity_type='fuel_receipt',
        entity_id=r.id,
        entity_label=r.doc_number or f'{r.fuel_type} {r.quantity}',
        before=before,
        after=after,
        description='Fuel receipt saved',
    )
    db.session.commit()
    flash(fuel_t('Кирим сақланди', 'Приход сохранён'), 'success')
    return redirect(url_for('fuel.receipts'))


@fuel_bp.route('/receipts/delete/<int:rid>', methods=['POST'])
@module_required('fuel')
def delete_receipt(rid):
    if not current_user.can_edit:
        abort(403)
    r = FuelReceipt2.query.get_or_404(rid)
    before = _fuel_receipt_snapshot(r)
    label = r.doc_number or f'{r.fuel_type} {r.quantity}'
    _audit_fuel(
        'fuel_receipt_deleted',
        entity_type='fuel_receipt',
        entity_id=r.id,
        entity_label=label,
        before=before,
        description='Fuel receipt deleted',
    )
    db.session.delete(r)
    db.session.commit()
    flash(fuel_t('Кирим ўчирилди', 'Приход удалён'), 'warning')
    return redirect(url_for('fuel.receipts'))


# ─── Transactions (Расходы из Топаз) ──────────────────────────────────

@fuel_bp.route('/transactions')
@module_required('fuel')
def transactions():
    d_from_s = request.args.get('date_from', '')
    d_to_s   = request.args.get('date_to', '')
    wh_id    = request.args.get('warehouse_id', type=int)

    today = date.today()
    try:
        d_from = datetime.strptime(d_from_s, '%Y-%m-%d').date()
    except ValueError:
        d_from = today

    try:
        d_to = datetime.strptime(d_to_s, '%Y-%m-%d').date()
    except ValueError:
        d_to = today

    d_from_dt = datetime.combine(d_from, datetime.min.time())
    d_to_dt   = datetime.combine(d_to, datetime.max.time())

    q = (FuelTransaction2.query
         .join(FuelStation2)
         .filter(FuelTransaction2.txn_datetime >= d_from_dt,
                 FuelTransaction2.txn_datetime <= d_to_dt))
    if wh_id:
        q = q.filter(FuelStation2.warehouse_id == wh_id)

    items = q.order_by(FuelTransaction2.txn_datetime.desc()).limit(500).all()

    warehouses = FuelWarehouse.query.order_by(FuelWarehouse.name).all()
    total_qty = sum(t.quantity for t in items)
    total_amount = sum(t.amount for t in items)

    sync_logs = (FuelSyncLog2.query
                 .order_by(FuelSyncLog2.synced_at.desc()).limit(10).all())

    return render_template('fuel/transactions.html',
                           items=items, warehouses=warehouses,
                           d_from=d_from, d_to=d_to,
                           selected_wh_id=wh_id,
                           total_qty=total_qty, total_amount=total_amount,
                           sync_logs=sync_logs, today=today)


# ─── Stations (АЗС — справочник) ─────────────────────────────────────

@fuel_bp.route('/stations')
@module_required('fuel')
@admin_required_fuel
def stations():
    all_stations = (FuelStation2.query
                    .join(FuelWarehouse)
                    .order_by(FuelWarehouse.name, FuelStation2.name).all())
    warehouses = FuelWarehouse.query.order_by(FuelWarehouse.name).all()
    station_delete_info = {}
    for st in all_stations:
        tx_count = FuelTransaction2.query.filter_by(station_id=st.id).count()
        station_delete_info[st.id] = {
            'can_delete': tx_count == 0,
            'can_deactivate': tx_count > 0 and bool(st.is_active),
            'is_disabled': tx_count > 0 and not bool(st.is_active),
            'transactions_count': tx_count,
        }
    return render_template('fuel/stations.html',
                           stations=all_stations, warehouses=warehouses,
                           station_delete_info=station_delete_info)


@fuel_bp.route('/stations/save', methods=['POST'])
@module_required('fuel')
@admin_required_fuel
def save_station():
    sid = request.form.get('id', type=int)
    name = request.form.get('name', '').strip()
    topaz_id = request.form.get('topaz_id', type=int)
    warehouse_id = request.form.get('warehouse_id', type=int)
    is_active = request.form.get('is_active') == 'on'

    if not name or not topaz_id or not warehouse_id:
        flash(fuel_t('Барча майдонларни тўлдиринг', 'Заполните все поля'), 'warning')
        return redirect(url_for('fuel.stations'))

    created = False
    before = None
    if sid:
        st = FuelStation2.query.get_or_404(sid)
        before = _fuel_station_snapshot(st)
        st.name = name
        st.topaz_id = topaz_id
        st.warehouse_id = warehouse_id
        st.is_active = is_active
    else:
        existing = FuelStation2.query.filter_by(topaz_id=topaz_id).first()
        if existing:
            flash(fuel_t(f'Topaz ID {topaz_id} бўлган АЗС аллақачон мавжуд', f'АЗС с Topaz ID {topaz_id} уже существует'), 'warning')
            return redirect(url_for('fuel.stations'))
        st = FuelStation2(name=name, topaz_id=topaz_id,
                          warehouse_id=warehouse_id, is_active=is_active)
        db.session.add(st)
        created = True

    db.session.flush()
    after = _fuel_station_snapshot(st)
    _audit_fuel(
        'fuel_station_created' if created else 'fuel_station_updated',
        entity_type='fuel_station',
        entity_id=st.id,
        entity_label=st.name,
        before=before,
        after=after,
        description='Fuel station saved',
    )
    db.session.commit()
    flash(fuel_t('АЗС сақланди', 'АЗС сохранена'), 'success')
    return redirect(url_for('fuel.stations'))


@fuel_bp.route('/stations/enable/<int:sid>', methods=['POST'])
@module_required('fuel')
@admin_required_fuel
def enable_station(sid):
    st = FuelStation2.query.get_or_404(sid)
    before = _fuel_station_snapshot(st)
    if st.is_active:
        flash(fuel_t('АЗС аллақачон фаол', 'АЗС уже активна'), 'info')
        return redirect(url_for('fuel.stations'))
    st.is_active = True
    db.session.flush()
    after = _fuel_station_snapshot(st)
    _audit_fuel(
        'fuel_station_reactivated',
        entity_type='fuel_station',
        entity_id=st.id,
        entity_label=st.name,
        before=before,
        after=after,
        description='Fuel station reactivated',
    )
    db.session.commit()
    flash(fuel_t('АЗС қайта фаоллаштирилди', 'АЗС включена'), 'success')
    return redirect(url_for('fuel.stations'))


@fuel_bp.route('/stations/delete/<int:sid>', methods=['POST'])
@module_required('fuel')
@admin_required_fuel
def delete_station(sid):
    st = FuelStation2.query.get_or_404(sid)
    before = _fuel_station_snapshot(st)
    label = st.name
    transactions_count = FuelTransaction2.query.filter_by(station_id=sid).count()
    if transactions_count:
        if st.is_active:
            st.is_active = False
            db.session.flush()
            after = _fuel_station_snapshot(st)
            after['transactions_count'] = transactions_count
            action = 'fuel_station_delete_blocked_deactivated'
            description = 'Fuel station delete blocked; station was deactivated because transactions exist'
        else:
            after = {'transactions_count': transactions_count}
            action = 'fuel_station_delete_blocked'
            description = 'Fuel station delete blocked because transactions exist'
        _audit_fuel(
            action,
            entity_type='fuel_station',
            entity_id=st.id,
            entity_label=label,
            before=before,
            after=after,
            description=description,
        )
        db.session.commit()
        flash(fuel_t('АЗС ўчирилмади: транзакциялар мавжуд. АЗС ўчириб қўйилди.',
                     'АЗС не удалена: есть транзакции. АЗС отключена.'), 'warning')
        return redirect(url_for('fuel.stations'))
    _audit_fuel(
        'fuel_station_deleted',
        entity_type='fuel_station',
        entity_id=st.id,
        entity_label=label,
        before=before,
        description='Fuel station deleted',
    )
    db.session.delete(st)
    db.session.commit()
    flash(fuel_t('АЗС ўчирилди', 'АЗС удалена'), 'warning')
    return redirect(url_for('fuel.stations'))


# ─── API: Ping ────────────────────────────────────────────────────────

@fuel_bp.route('/api/fuel_ping')
def api_fuel_ping():
    return jsonify(status='ok', server_time=datetime.utcnow().isoformat())


# ─── API: Fuel Sync (принимаем данные от агента Топаз) ────────────────

def _perform_fuel_sync():
    # [REASON]: Shared sync logic called by both the canonical /fuel/api/fuel_sync
    # and the legacy /api/fuel_sync compatibility alias. Keeping logic in one place
    # prevents drift between the two paths.
    try:
        payload = request.get_json(force=True)
    except Exception:
        return jsonify(error='invalid JSON'), 400

    api_token = current_app.config.get('FUEL_API_TOKEN')
    if not api_token or not payload or payload.get('token') != api_token:
        return jsonify(error='unauthorized'), 401

    transactions = payload.get('transactions', [])
    agent_ip = request.remote_addr

    new_count = 0
    dup_count = 0
    unknown_count = 0
    errors = []

    # Предзагружаем маппинг topaz_id → station
    station_map = {s.topaz_id: s for s in FuelStation2.query.filter_by(is_active=True).all()}

    for txn in transactions:
        try:
            col_id = int(txn.get('topaz_col_id') or 0)
            station = station_map.get(col_id)

            if not station:
                unknown_count += 1
                continue

            topaz_txn_id = str(txn.get('topaz_txn_id', ''))
            txn_dt_s = txn.get('txn_datetime', '')
            try:
                txn_dt = datetime.fromisoformat(txn_dt_s)
            except Exception:
                txn_dt = datetime.utcnow()

            # Дедупликация
            if topaz_txn_id:
                existing = (FuelTransaction2.query
                            .filter_by(station_id=station.id,
                                       topaz_txn_id=topaz_txn_id)
                            .first())
                if existing:
                    dup_count += 1
                    continue

            t = FuelTransaction2(
                station_id=station.id,
                topaz_txn_id=topaz_txn_id,
                topaz_col_id=col_id,
                txn_datetime=txn_dt,
                card_number=str(txn.get('card_number', '') or ''),
                fuel_type=str(txn.get('fuel_type', 'ДТ') or 'ДТ'),
                quantity=float(txn.get('quantity', 0) or 0),
                price_per_liter=float(txn.get('price_per_liter', 0) or 0),
                amount=float(txn.get('amount', 0) or 0),
            )
            db.session.add(t)
            new_count += 1

        except Exception as e:
            errors.append(str(e))

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        summary = {
            'agent_ip': agent_ip,
            'transactions_received': len(transactions),
            'transactions_new': new_count,
            'transactions_dup': dup_count,
            'unknown_stations': unknown_count,
            'errors_count': len(errors),
            'db_error': str(e),
        }
        try:
            _audit_fuel(
                'fuel_topaz_sync_failed',
                entity_type='fuel_sync',
                entity_label='topaz_agent',
                after=summary,
                status='error',
                description='Topaz fuel sync failed during transaction commit',
                actor_user=TOPAZ_AUDIT_ACTOR,
            )
            db.session.commit()
        except Exception:
            db.session.rollback()
        return jsonify(error=str(e)), 500

    # Лог
    log = FuelSyncLog2(
        agent_ip=agent_ip,
        transactions_received=len(transactions),
        transactions_new=new_count,
        transactions_dup=dup_count,
        unknown_stations=unknown_count,
        status='ok' if not errors else 'partial',
        error_msg='; '.join(errors[:5]),
    )
    db.session.add(log)
    db.session.flush()
    summary = {
        'sync_log_id': log.id,
        'agent_ip': agent_ip,
        'transactions_received': len(transactions),
        'transactions_new': new_count,
        'transactions_dup': dup_count,
        'unknown_stations': unknown_count,
        'errors_count': len(errors),
        'status': log.status,
    }
    _audit_fuel(
        'fuel_topaz_sync_completed',
        entity_type='fuel_sync',
        entity_id=log.id,
        entity_label='topaz_agent',
        after=summary,
        status='ok' if not errors else 'partial',
        description='Topaz fuel sync completed',
        actor_user=TOPAZ_AUDIT_ACTOR,
    )
    db.session.commit()

    return jsonify(
        status='ok',
        received=len(transactions),
        new=new_count,
        duplicates=dup_count,
        unknown=unknown_count,
    )


@fuel_bp.route('/api/fuel_sync', methods=['POST'])
def api_fuel_sync():
    return _perform_fuel_sync()
