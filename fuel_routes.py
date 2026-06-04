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
                   url_for, flash, jsonify, abort, current_app, g, send_file)
from flask_login import login_required, current_user
from datetime import datetime, date, timedelta
from io import BytesIO
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


def fuel_format_errors(errors, title_uz=None, title_ru=None):
    unique = []
    seen = set()
    for err in errors or []:
        text = str(err).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        unique.append(text)
    if not unique:
        return ''
    title = fuel_t(title_uz or 'Маълумот сақланмади. Хатоларни тузатинг:',
                   title_ru or 'Данные не сохранены. Исправьте ошибки:')
    return title + '\n' + '\n'.join('- ' + item for item in unique)


def fuel_flash_errors(errors, title_uz=None, title_ru=None):
    msg = fuel_format_errors(errors, title_uz, title_ru)
    if msg:
        flash(msg, 'warning')


def _parse_fuel_date(value, field_label):
    try:
        if not value:
            raise ValueError()
        return datetime.strptime(value, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        raise ValueError(field_label)


def _parse_non_negative(value, field_label, allow_empty=True):
    if value is None or str(value).strip() == '':
        if allow_empty:
            return None
        raise ValueError(field_label)
    try:
        result = float(str(value).replace(',', '.'))
    except (TypeError, ValueError):
        raise ValueError(field_label)
    if result < 0:
        raise ValueError(field_label)
    return result


def _parse_float_required(value, field_label):
    if value is None or str(value).strip() == '':
        raise ValueError(field_label)
    try:
        return float(str(value).replace(',', '.'))
    except (TypeError, ValueError):
        raise ValueError(field_label)


def _parse_positive(value, field_label):
    result = _parse_non_negative(value, field_label, allow_empty=False)
    if result <= 0:
        raise ValueError(field_label)
    return result



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
FUEL_TYPES = ['ДТ']


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




# ─── Fuel management report ─────────────────────────────────────────

def _fuel_report_lang():
    lang = getattr(g, 'lang', 'uz') or 'uz'
    try:
        if current_user.is_authenticated:
            lang = getattr(current_user, 'language', lang) or lang
    except Exception:
        pass
    return 'ru' if lang == 'ru' else 'uz'


def _fuel_report_label(ru, uz):
    return ru if _fuel_report_lang() == 'ru' else uz


def _parse_report_date(value, default):
    try:
        return datetime.strptime(value or '', '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return default


def _sum_receipts_for_period(warehouse_id, start_date, end_date):
    return float(db.session.query(func.coalesce(func.sum(FuelReceipt2.quantity), 0))
                 .filter(FuelReceipt2.warehouse_id == warehouse_id,
                         FuelReceipt2.receipt_date >= start_date,
                         FuelReceipt2.receipt_date <= end_date)
                 .scalar() or 0)


def _sum_issues_for_period(warehouse_id, start_dt, end_dt, station_id=None):
    q = (db.session.query(func.coalesce(func.sum(FuelTransaction2.quantity), 0))
         .join(FuelStation2)
         .filter(FuelStation2.warehouse_id == warehouse_id,
                 FuelTransaction2.txn_datetime >= start_dt,
                 FuelTransaction2.txn_datetime <= end_dt))
    if station_id:
        q = q.filter(FuelTransaction2.station_id == station_id)
    return float(q.scalar() or 0)


def _fuel_opening_balance(warehouse_id, d_from):
    ib = (FuelInitialBalance.query
          .filter_by(warehouse_id=warehouse_id, fuel_type='ДТ')
          .first())
    if not ib:
        return None, None
    start_date = ib.balance_date
    if d_from <= start_date:
        return float(ib.quantity or 0), ib
    before_date = d_from - timedelta(days=1)
    receipts_before = _sum_receipts_for_period(warehouse_id, start_date, before_date)
    issues_before = _sum_issues_for_period(
        warehouse_id,
        datetime.combine(start_date, datetime.min.time()),
        datetime.combine(before_date, datetime.max.time()),
    )
    return round(float(ib.quantity or 0) + receipts_before - issues_before, 2), ib


def _collect_fuel_report_data(d_from, d_to, warehouse_id=None, station_id=None):
    d_from_dt = datetime.combine(d_from, datetime.min.time())
    d_to_dt = datetime.combine(d_to, datetime.max.time())

    selected_station = None
    if station_id:
        selected_station = FuelStation2.query.get(station_id)
        if selected_station:
            warehouse_id = selected_station.warehouse_id
        else:
            station_id = None

    wh_query = FuelWarehouse.query.order_by(FuelWarehouse.name)
    if warehouse_id:
        wh_query = wh_query.filter(FuelWarehouse.id == warehouse_id)
    warehouses = wh_query.all()

    warehouse_rows = []
    totals = {
        'opening': 0.0,
        'receipts': 0.0,
        'issued': 0.0,
        'ending': 0.0,
        'warehouses': len(warehouses),
        'stations': 0,
        'transactions': 0,
        'negative_balances': 0,
        'missing_initial': 0,
    }

    for wh in warehouses:
        opening, initial_balance = _fuel_opening_balance(wh.id, d_from)
        receipts = _sum_receipts_for_period(wh.id, d_from, d_to)
        issued = _sum_issues_for_period(wh.id, d_from_dt, d_to_dt, station_id=station_id)
        tx_count_q = (db.session.query(func.count(FuelTransaction2.id))
                      .join(FuelStation2)
                      .filter(FuelStation2.warehouse_id == wh.id,
                              FuelTransaction2.txn_datetime >= d_from_dt,
                              FuelTransaction2.txn_datetime <= d_to_dt))
        if station_id:
            tx_count_q = tx_count_q.filter(FuelTransaction2.station_id == station_id)
        tx_count = int(tx_count_q.scalar() or 0)
        stations_count = wh.stations.count()
        last_txn_q = (FuelTransaction2.query
                      .join(FuelStation2)
                      .filter(FuelStation2.warehouse_id == wh.id,
                              FuelTransaction2.txn_datetime >= d_from_dt,
                              FuelTransaction2.txn_datetime <= d_to_dt))
        if station_id:
            last_txn_q = last_txn_q.filter(FuelTransaction2.station_id == station_id)
        last_txn = last_txn_q.order_by(FuelTransaction2.txn_datetime.desc()).first()
        ending = None if opening is None else round(opening + receipts - issued, 2)

        if opening is None:
            totals['missing_initial'] += 1
        else:
            totals['opening'] += opening
            totals['ending'] += ending or 0
            if ending is not None and ending < 0:
                totals['negative_balances'] += 1
        totals['receipts'] += receipts
        totals['issued'] += issued
        totals['transactions'] += tx_count
        totals['stations'] += stations_count

        warehouse_rows.append({
            'warehouse': wh,
            'opening': opening,
            'receipts': receipts,
            'issued': issued,
            'ending': ending,
            'initial_balance': initial_balance,
            'stations_count': stations_count,
            'tx_count': tx_count,
            'last_txn': last_txn,
        })

    station_query = (db.session.query(
            FuelStation2.id,
            FuelStation2.name,
            FuelStation2.topaz_id,
            FuelStation2.is_active,
            FuelWarehouse.name.label('warehouse_name'),
            func.coalesce(func.sum(FuelTransaction2.quantity), 0).label('issued'),
            func.count(FuelTransaction2.id).label('tx_count'),
            func.max(FuelTransaction2.txn_datetime).label('last_txn'),
        )
        .join(FuelWarehouse, FuelWarehouse.id == FuelStation2.warehouse_id)
        .outerjoin(FuelTransaction2,
                   (FuelTransaction2.station_id == FuelStation2.id) &
                   (FuelTransaction2.txn_datetime >= d_from_dt) &
                   (FuelTransaction2.txn_datetime <= d_to_dt)))
    if warehouse_id:
        station_query = station_query.filter(FuelStation2.warehouse_id == warehouse_id)
    if station_id:
        station_query = station_query.filter(FuelStation2.id == station_id)
    station_rows = station_query.group_by(
        FuelStation2.id, FuelStation2.name, FuelStation2.topaz_id,
        FuelStation2.is_active, FuelWarehouse.name,
    ).order_by(func.coalesce(func.sum(FuelTransaction2.quantity), 0).desc(), FuelStation2.name).all()

    recent_txns_q = (FuelTransaction2.query
                     .join(FuelStation2)
                     .filter(FuelTransaction2.txn_datetime >= d_from_dt,
                             FuelTransaction2.txn_datetime <= d_to_dt))
    if warehouse_id:
        recent_txns_q = recent_txns_q.filter(FuelStation2.warehouse_id == warehouse_id)
    if station_id:
        recent_txns_q = recent_txns_q.filter(FuelTransaction2.station_id == station_id)
    recent_txns = recent_txns_q.order_by(FuelTransaction2.txn_datetime.desc()).limit(200).all()

    sync_logs = (FuelSyncLog2.query
                 .filter(FuelSyncLog2.synced_at >= d_from_dt,
                         FuelSyncLog2.synced_at <= d_to_dt)
                 .order_by(FuelSyncLog2.synced_at.desc())
                 .limit(100).all())
    sync_summary = {
        'logs': len(sync_logs),
        'received': sum((l.transactions_received or 0) for l in sync_logs),
        'new': sum((l.transactions_new or 0) for l in sync_logs),
        'dup': sum((l.transactions_dup or 0) for l in sync_logs),
        'unknown': sum((l.unknown_stations or 0) for l in sync_logs),
        'last': sync_logs[0] if sync_logs else None,
    }

    totals = {k: round(v, 2) if isinstance(v, float) else v for k, v in totals.items()}
    return {
        'd_from': d_from,
        'd_to': d_to,
        'd_from_dt': d_from_dt,
        'd_to_dt': d_to_dt,
        'warehouse_rows': warehouse_rows,
        'station_rows': station_rows,
        'recent_txns': recent_txns,
        'sync_logs': sync_logs,
        'sync_summary': sync_summary,
        'totals': totals,
        'selected_warehouse_id': warehouse_id,
        'selected_station_id': station_id,
        'selected_station': selected_station,
    }


def _safe_ws_title(title, used):
    bad = '[]:*?/\\'
    cleaned = ''.join('_' if c in bad else c for c in str(title or 'Sheet')).strip()[:31]
    cleaned = cleaned or 'Sheet'
    base = cleaned
    idx = 2
    while cleaned in used:
        suffix = f' {idx}'
        cleaned = (base[:31-len(suffix)] + suffix).strip()
        idx += 1
    used.add(cleaned)
    return cleaned


def _fuel_report_workbook(data, lang='uz'):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    def L(ru, uz):
        return ru if lang == 'ru' else uz

    wb = Workbook()
    wb.remove(wb.active)
    used = set()
    header_fill = PatternFill('solid', fgColor='D9EAD3')
    title_fill = PatternFill('solid', fgColor='1A6B3C')
    title_font = Font(bold=True, color='FFFFFF', size=12)
    header_font = Font(bold=True)
    thin = Side(style='thin', color='D9D9D9')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    def style_table(ws, header_row=1):
        ws.freeze_panes = f'A{header_row + 1}'
        ws.sheet_view.showGridLines = False
        max_col = ws.max_column
        max_row = ws.max_row
        if max_row >= header_row and max_col:
            ws.auto_filter.ref = f'A{header_row}:{get_column_letter(max_col)}{max_row}'
        for cell in ws[header_row]:
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        for row in ws.iter_rows(min_row=1, max_row=max_row, max_col=max_col):
            for cell in row:
                cell.border = border
                cell.alignment = Alignment(vertical='center', wrap_text=True)
        for col in range(1, max_col + 1):
            letter = get_column_letter(col)
            width = 10
            for cell in ws[letter]:
                val = '' if cell.value is None else str(cell.value)
                width = max(width, min(len(val) + 2, 38))
            ws.column_dimensions[letter].width = width
        ws.page_setup.orientation = 'landscape'
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_margins.left = 0.3
        ws.page_margins.right = 0.3
        ws.page_margins.top = 0.5
        ws.page_margins.bottom = 0.5

    ws = wb.create_sheet(_safe_ws_title(L('Сводка', 'Сводка'), used))
    ws.append([L('Отчёт по топливу', 'Ёқилғи ҳисоботи')])
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=4)
    ws['A1'].font = title_font
    ws['A1'].fill = title_fill
    ws.append([L('Период', 'Давр'), data['d_from'].strftime('%d.%m.%Y'), data['d_to'].strftime('%d.%m.%Y'), 'ДТ'])
    ws.append([])
    ws.append([L('Показатель', 'Кўрсаткич'), L('Значение', 'Қиймат')])
    summary_rows = [
        (L('Начальный остаток, л', 'Бошланғич қолдиқ, л'), data['totals']['opening']),
        (L('Приход, л', 'Кирим, л'), data['totals']['receipts']),
        (L('Выдача Topaz, л', 'Topaz бериш, л'), data['totals']['issued']),
        (L('Расчётный остаток, л', 'Ҳисобий қолдиқ, л'), data['totals']['ending']),
        (L('Склады', 'Омборлар'), data['totals']['warehouses']),
        (L('АЗС', 'АЗС'), data['totals']['stations']),
        (L('Транзакции', 'Транзакциялар'), data['totals']['transactions']),
        (L('Складов без начального остатка', 'Бошланғич қолдиқсиз омборлар'), data['totals']['missing_initial']),
        (L('Отрицательных остатков', 'Манфий қолдиқлар'), data['totals']['negative_balances']),
        (L('Неизвестных АЗС в sync logs', 'Sync logларда номаълум АЗС'), data['sync_summary']['unknown']),
    ]
    for label, value in summary_rows:
        ws.append([label, value])
    style_table(ws, header_row=4)

    ws = wb.create_sheet(_safe_ws_title(L('Склады', 'Омборлар'), used))
    ws.append([L('Склад', 'Омбор'), L('АЗС', 'АЗС'), L('Начальный остаток', 'Бошланғич қолдиқ'), L('Приход', 'Кирим'), L('Выдача', 'Бериш'), L('Расчётный остаток', 'Ҳисобий қолдиқ'), L('Транзакций', 'Транзакциялар'), L('Последняя выдача', 'Охирги бериш')])
    for r in data['warehouse_rows']:
        last = r['last_txn'].txn_datetime.strftime('%d.%m.%Y %H:%M') if r['last_txn'] else ''
        ws.append([r['warehouse'].name, r['stations_count'], r['opening'], r['receipts'], r['issued'], r['ending'], r['tx_count'], last])
    style_table(ws)

    ws = wb.create_sheet(_safe_ws_title(L('АЗС', 'АЗС'), used))
    ws.append([L('АЗС', 'АЗС'), 'Topaz ID', L('Склад', 'Омбор'), L('Активна', 'Фаол'), L('Выдано, л', 'Берилди, л'), L('Транзакций', 'Транзакциялар'), L('Последняя выдача', 'Охирги бериш')])
    for r in data['station_rows']:
        last = r.last_txn.strftime('%d.%m.%Y %H:%M') if r.last_txn else ''
        ws.append([r.name, r.topaz_id, r.warehouse_name, L('Да', 'Ҳа') if r.is_active else L('Нет', 'Йўқ'), float(r.issued or 0), int(r.tx_count or 0), last])
    style_table(ws)

    ws = wb.create_sheet(_safe_ws_title(L('Транзакции', 'Транзакциялар'), used))
    ws.append([L('Дата/время', 'Сана/вақт'), L('АЗС', 'АЗС'), L('Склад', 'Омбор'), L('Карта', 'Карта'), L('Топливо', 'Ёқилғи'), L('Литры', 'Литр'), 'Topaz ID'])
    for t in data['recent_txns']:
        ws.append([t.txn_datetime.strftime('%d.%m.%Y %H:%M'), t.station.name, t.station.warehouse_name, t.card_number or '', t.fuel_type or 'ДТ', t.quantity or 0, t.topaz_txn_id or ''])
    style_table(ws)

    ws = wb.create_sheet(_safe_ws_title(L('Синхронизация', 'Синхронизация'), used))
    ws.append([L('Время', 'Вақт'), L('Агент IP', 'Агент IP'), L('Получено', 'Қабул қилинди'), L('Новых', 'Янги'), L('Дублей', 'Такрор'), L('Неизвестных АЗС', 'Номаълум АЗС'), L('Статус', 'Ҳолат'), L('Ошибка', 'Хато')])
    for l in data['sync_logs']:
        ws.append([l.synced_at.strftime('%d.%m.%Y %H:%M:%S'), l.agent_ip or '', l.transactions_received or 0, l.transactions_new or 0, l.transactions_dup or 0, l.unknown_stations or 0, l.status or '', l.error_msg or ''])
    style_table(ws)

    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, (int, float)):
                    cell.number_format = '#,##0.00'
    return wb


@fuel_bp.route('/report')
@module_required('fuel')
def fuel_report():
    today = date.today()
    default_from = today.replace(day=1)
    d_from = _parse_report_date(request.args.get('date_from'), default_from)
    d_to = _parse_report_date(request.args.get('date_to'), today)
    if d_from > d_to:
        d_from, d_to = d_to, d_from

    warehouse_id = request.args.get('warehouse_id', type=int)
    station_id = request.args.get('station_id', type=int)
    data = _collect_fuel_report_data(d_from, d_to, warehouse_id=warehouse_id, station_id=station_id)

    if request.args.get('export') == '1':
        wb = _fuel_report_workbook(data, lang=_fuel_report_lang())
        buffer = BytesIO()
        wb.save(buffer)
        buffer.seek(0)
        prefix = 'Fuel_report' if _fuel_report_lang() == 'ru' else 'Yoqilgi_hisoboti'
        fname = f"{prefix}_{d_from.strftime('%d_%m_%Y')}_{d_to.strftime('%d_%m_%Y')}.xlsx"
        return send_file(
            buffer,
            as_attachment=True,
            download_name=fname,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )

    warehouses = FuelWarehouse.query.order_by(FuelWarehouse.name).all()
    stations = (FuelStation2.query
                .join(FuelWarehouse)
                .order_by(FuelWarehouse.name, FuelStation2.name).all())
    return render_template('fuel/report.html',
                           warehouses=warehouses,
                           stations=stations,
                           fuel_types=FUEL_TYPES,
                           **data)


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
    fuel_type = 'ДТ'
    balance_date_s = request.form.get('balance_date', '')
    note = request.form.get('note', '').strip()

    errors = []
    if not warehouse_id or not FuelWarehouse.query.get(warehouse_id):
        errors.append(fuel_t('Омборни танланг', 'Выберите склад'))
    if fuel_type not in FUEL_TYPES:
        errors.append(fuel_t('Ёқилғи тури нотўғри', 'Некорректный тип топлива'))
    try:
        quantity = _parse_float_required(request.form.get('quantity'), 'quantity')
    except ValueError:
        quantity = None
        errors.append(fuel_t('Миқдор сон бўлиши керак', 'Количество должно быть числом'))
    try:
        balance_date = _parse_fuel_date(balance_date_s, 'balance_date')
    except ValueError:
        balance_date = None
        errors.append(fuel_t('Сана тўғри бўлиши керак', 'Дата должна быть корректной'))
    if errors:
        fuel_flash_errors(errors)
        return redirect(url_for('fuel.initial_balance'))

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
    fuel_type = 'ДТ'
    supplier = request.form.get('supplier', '').strip()
    doc_number = request.form.get('doc_number', '').strip()
    note = request.form.get('note', '').strip()
    date_s = request.form.get('receipt_date', '')

    errors = []
    if not warehouse_id or not FuelWarehouse.query.get(warehouse_id):
        errors.append(fuel_t('Омборни танланг', 'Выберите склад'))
    if fuel_type not in FUEL_TYPES:
        errors.append(fuel_t('Ёқилғи тури нотўғри', 'Некорректный тип топлива'))
    try:
        quantity = _parse_positive(request.form.get('quantity'), 'quantity')
    except ValueError:
        quantity = None
        errors.append(fuel_t('Миқдор мусбат бўлиши керак', 'Количество должно быть больше нуля'))
    price = 0
    try:
        receipt_date = _parse_fuel_date(date_s, 'receipt_date')
    except ValueError:
        receipt_date = None
        errors.append(fuel_t('Сана тўғри бўлиши керак', 'Дата должна быть корректной'))
    if errors:
        fuel_flash_errors(errors)
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

    if not name or not topaz_id or topaz_id <= 0 or not warehouse_id:
        flash(fuel_t('Барча майдонларни тўлдиринг', 'Заполните все поля'), 'warning')
        return redirect(url_for('fuel.stations'))
    if not FuelWarehouse.query.get(warehouse_id):
        flash(fuel_t('Омборни танланг', 'Выберите склад'), 'warning')
        return redirect(url_for('fuel.stations'))
    existing_topaz = FuelStation2.query.filter_by(topaz_id=topaz_id).first()
    if existing_topaz and (not sid or existing_topaz.id != sid):
        flash(fuel_t(f'Topaz ID {topaz_id} бўлган АЗС аллақачон мавжуд',
                     f'АЗС с Topaz ID {topaz_id} уже существует'), 'warning')
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
                fuel_type='ДТ',
                quantity=float(txn.get('quantity', 0) or 0),
                price_per_liter=0,
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
