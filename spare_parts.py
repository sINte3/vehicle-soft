import os
import re
import uuid
from difflib import SequenceMatcher

from flask import (Blueprint, render_template, request, redirect, url_for, flash,
                   abort, g, jsonify, current_app, send_from_directory)
from flask_login import login_required, current_user
from datetime import datetime, date, timedelta
from sqlalchemy import func
from sqlalchemy.orm import joinedload, selectinload

from models import (db, SparePart, SparePartCategory, SparePartPriceAudit,
                    SparePartAttachment, SparePartInventory,
                    SparePartInventoryMovement, SparePartRequest,
                    SparePartRequestItem, SparePartSku, SparePartStatusHistory,
                    SparePartWarehouse, SparePartWriteOffAct,
                    SparePartWriteOffActItem, Organization, Equipment,
                    module_required)
from sec003a_ext import log_audit, diff_dict

# [REASON]: BOT003 — Best-effort Telegram notifications. Import is guarded so that
# missing module does not crash spare parts. The actual enqueue functions catch all
# exceptions internally and never raise.
try:
    from bot003_notifications import (
        enqueue_spare_request_submitted_best_effort,
        enqueue_spare_request_status_best_effort,
    )
    _BOT003_AVAILABLE = True
except ImportError:
    _BOT003_AVAILABLE = False

spare_parts_bp = Blueprint('spare_parts', __name__, url_prefix='/spare-parts')

STATUS_LABELS = {
    'draft':     {'uz': 'Чорнов',       'ru': 'Черновик'},
    'submitted': {'uz': 'Юборилган',    'ru': 'Отправлено'},
    # [REASON]: SPARE-STAGE1 — application-level status for the price
    # reject/return workflow; no schema change (status column is free VARCHAR).
    # Flow: draft -> submitted -> (returned_for_revision -> submitted) -> approved | rejected
    'returned_for_revision': {'uz': 'Қайта ишлашга қайтарилган', 'ru': 'На доработке'},
    'approved':  {'uz': 'Тасдиқланган', 'ru': 'Утверждено'},
    'rejected':  {'uz': 'Рад этилган',  'ru': 'Отклонено'},
    # [REASON]: SPARE-STAGE2 — new terminal state, reachable ONLY from
    # 'approved' (issue_request enforces the transition): the approved goods
    # were physically handed over and a write-off act was generated.
    'issued':    {'uz': 'Берилган',     'ru': 'Выдано'},
}
STATUS_COLORS = {
    'draft':     'var(--text2)',
    'submitted': 'var(--info)',
    'returned_for_revision': 'var(--warn)',
    'approved':  'var(--accent)',
    'rejected':  'var(--danger)',
    'issued':    'var(--success)',
}

PRICE_STATUS_LABELS = {
    'pending':   {'uz': 'Нарх кутилмоқда',      'ru': 'Цена не подтверждена'},
    'confirmed': {'uz': 'Нарх тасдиқланган',    'ru': 'Цена подтверждена'},
    'rejected':  {'uz': 'Нарх рад этилган',     'ru': 'Цена отклонена'},
    'returned':  {'uz': 'Нарх қайтарилган',     'ru': 'Цена возвращена'},
}
PRICE_STATUS_COLORS = {
    'pending':   'var(--text2)',
    'confirmed': 'var(--accent)',
    'rejected':  'var(--danger)',
    'returned':  'var(--warn)',
}

# ─── SPARE-STAGE2: warning rule 3/4/6 thresholds ─────────────────────────────
# [REASON]: SPARE-STAGE2 — rules 3/4/6 are warn-only (never block), same
# principle as the SPARE-STAGE1 repeat engine (rules 1-2). The values below
# are tunable starting points agreed with the project owner, not validated
# business constants.

# Tunable starting value: rule 3 warns when the same equipment + same catalog
# part appears on MORE THAN this many approved requests in the trailing window.
RULE3_MAX_OCCURRENCES_90D = 3
# Tunable starting value: rule 3 trailing lookback window in days (mirrors the
# 90-day window already used by the rules 1-2 repeat engine).
RULE3_WINDOW_DAYS = 90
# Tunable starting value: rule 4 warns when current-month approved+confirmed
# spend exceeds the trailing-months average by more than this multiplier.
RULE4_COST_ANOMALY_MULTIPLIER = 2.0
# Tunable starting value: rule 4 baseline length in calendar months (the
# months immediately before the current one).
RULE4_BASELINE_MONTHS = 3
# Tunable starting value: rule 6 warns when the same equipment + part was on a
# request rejected within this many trailing days.
RULE6_REJECTED_LOOKBACK_DAYS = 30

# ─── SPARE-STAGE2: fuzzy catalog search ──────────────────────────────────────
# Tunable starting value: minimum difflib ratio for a fuzzy-only match
# (same default cutoff difflib.get_close_matches uses).
FUZZY_MATCH_MIN_RATIO = 0.6
# Tunable starting value: maximum results returned by api_catalog_search.
FUZZY_SEARCH_MAX_RESULTS = 10

# ─── SPARE-STAGE2: inventory movement types ──────────────────────────────────
INV_MOVEMENT_TYPES = ('receipt', 'issue', 'adjustment', 'write_off')
INV_MOVEMENT_LABELS = {
    'receipt':    {'uz': 'Кирим',         'ru': 'Приход'},
    'issue':      {'uz': 'Бериш',         'ru': 'Выдача'},
    'adjustment': {'uz': 'Тузатиш',       'ru': 'Корректировка'},
    'write_off':  {'uz': 'Ҳисобдан чиқариш', 'ru': 'Списание'},
}

# Photo/video upload rules (SPARE-STAGE1 + MOBILE-UPLOAD-001). Extension AND
# content signature are both checked -- Content-Type alone is never trusted.
# [REASON]: MOBILE-UPLOAD-001 — field mechanics use phone cameras; iPhones
# default to HEIC, and Android/action-cam footage is commonly MP4/WEBM/
# MKV/AVI, so JPG/PNG-only was too narrow for real field use.
ALLOWED_PHOTO_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.heic', '.heif'}
ALLOWED_VIDEO_EXTENSIONS = {'.mp4', '.mov', '.webm', '.avi', '.mkv'}
ALLOWED_ATTACHMENT_EXTENSIONS = ALLOWED_PHOTO_EXTENSIONS | ALLOWED_VIDEO_EXTENSIONS

# [REASON]: No Pillow/ffmpeg in requirements.txt (no new dependencies without
# approval), so file content is verified with minimal magic-byte checks per
# container format instead (see _validate_photo). ISO-BMFF (`ftyp` at offset
# 4) covers MP4/MOV/HEIC/HEIF -- deliberately NOT distinguishing exact brand
# codes between them (real complexity for negligible anti-spoofing benefit
# here); a valid `ftyp` box is sufficient proof the file is a real media
# container and not, e.g., a renamed .txt/.html (the actual failure mode
# this project hit once in QA, caught by the JPG/PNG check -- the new
# formats keep that same bar).

MAX_ATTACHMENT_SIZE_MB = 50
MAX_ATTACHMENT_SIZE = MAX_ATTACHMENT_SIZE_MB * 1024 * 1024
# [REASON]: MOBILE-UPLOAD-001 — photos and videos combined, per request line
# item (both the create flow and the detail-page add-more flow enforce this
# against the same running count).
MAX_ATTACHMENTS_PER_ITEM = 5

# [REASON]: MOBILE-UPLOAD-001 — attachment_file() no longer relies on
# mimetypes.guess_type() (unreliable for WEBP/HEIC across Windows Python
# installs); Content-Type is looked up explicitly per stored extension.
ATTACHMENT_CONTENT_TYPES = {
    '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
    '.png': 'image/png',
    '.webp': 'image/webp',
    '.heic': 'image/heic', '.heif': 'image/heif',
    '.mp4': 'video/mp4',
    '.mov': 'video/quicktime',
    '.webm': 'video/webm',
    '.avi': 'video/x-msvideo',
    '.mkv': 'video/x-matroska',
}


def _spare_lang():
    lang = getattr(g, 'lang', None)
    if not lang and getattr(current_user, 'is_authenticated', False):
        lang = getattr(current_user, 'language', None)
    return 'ru' if lang == 'ru' else 'uz'


def _spare_t(uz_text, ru_text):
    return ru_text if _spare_lang() == 'ru' else uz_text


def _spare_format_errors(errors, title_uz=None, title_ru=None):
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
    title = _spare_t(title_uz or 'Заявка сақланмади. Хатоларни тузатинг:',
                     title_ru or 'Заявка не сохранена. Исправьте ошибки:')
    return title + '\n' + '\n'.join('- ' + item for item in unique)


def _spare_flash_errors(errors, title_uz=None, title_ru=None):
    msg = _spare_format_errors(errors, title_uz, title_ru)
    if msg:
        flash(msg, 'warning')



def _add_status_history(request_id, old_status, new_status, comment="", changed_by=None):
    """Insert a SparePartStatusHistory row for a status transition.

    Called before db.session.commit() in status-changing routes.
    Does not flush or commit -- caller is responsible for the final commit.
    """
    history = SparePartStatusHistory(
        request_id=request_id,
        old_status=old_status,
        new_status=new_status,
        comment=comment or "",
        changed_by=changed_by,
    )
    db.session.add(history)


def _date_iso(value):
    if not value:
        return None
    try:
        return value.isoformat()
    except Exception:
        return str(value)


def _spare_user_org_ids():
    return current_user.get_org_ids() if not current_user.is_admin else None


def _spare_check_org_access(org_id):
    if not org_id or not current_user.can_access_org(org_id):
        abort(403)


def _spare_check_request_access(req):
    if not current_user.is_admin and not current_user.can_access_org(req.organization_id):
        abort(403)


def _parse_spare_date(value):
    try:
        if not value:
            raise ValueError()
        return datetime.strptime(value, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        raise ValueError('request_date')


def _parse_spare_positive_qty(value):
    try:
        result = float(str(value).replace(',', '.'))
    except (TypeError, ValueError):
        raise ValueError('quantity')
    if result <= 0:
        raise ValueError('quantity')
    return result


def _request_snapshot(req):
    if not req:
        return None
    return {
        'id': getattr(req, 'id', None),
        'request_date': _date_iso(getattr(req, 'request_date', None)),
        'organization_id': getattr(req, 'organization_id', None),
        'equipment_id': getattr(req, 'equipment_id', None),
        'status': getattr(req, 'status', ''),
        'note': getattr(req, 'note', ''),
        'created_by': getattr(req, 'created_by', None),
        'reviewed_by': getattr(req, 'reviewed_by', None),
        'reviewed_at': _date_iso(getattr(req, 'reviewed_at', None)),
        'review_comment': getattr(req, 'review_comment', ''),
        'items_count': len(getattr(req, 'items', []) or []),
    }


def _item_snapshot(item):
    if not item:
        return None
    return {
        'id': getattr(item, 'id', None),
        'request_id': getattr(item, 'request_id', None),
        'spare_part_id': getattr(item, 'spare_part_id', None),
        'sku_id': getattr(item, 'sku_id', None),
        'name': getattr(item, 'name', ''),
        'part_number': getattr(item, 'part_number', ''),
        'quantity': getattr(item, 'quantity', None),
        'unit': getattr(item, 'unit', ''),
        'note': getattr(item, 'note', ''),
        'price': getattr(item, 'price', None),
        'price_status': getattr(item, 'price_status', ''),
    }


def _catalog_snapshot(part):
    if not part:
        return None
    return {
        'id': getattr(part, 'id', None),
        'name': getattr(part, 'name', ''),
        'part_number': getattr(part, 'part_number', ''),
        'unit': getattr(part, 'unit', ''),
        'category': getattr(part, 'category', ''),
        'category_id': getattr(part, 'category_id', None),
        'status': getattr(part, 'status', ''),
        'merged_into_id': getattr(part, 'merged_into_id', None),
        'source_request_item_id': getattr(part, 'source_request_item_id', None),
        'created_at': _date_iso(getattr(part, 'created_at', None)),
    }


def _audit_spare(action, entity_type='', entity_id=None, entity_label='', before=None,
                 after=None, changes=None, description=''):
    log_audit(
        db,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        entity_label=entity_label,
        module='spare_parts',
        before=before,
        after=after,
        changes=changes,
        description=description,
    )


# ─── SPARE-STAGE1: price audit / repeat-order / photo-gate helpers ────────────

def write_price_audit(item_id, old_price, new_price, action, user_id):
    """Append a SparePartPriceAudit row for a price action.

    [REASON]: SPARE-STAGE1 — plain function with explicit arguments only (no
    current_user / request access) so the future Telegram bot process (BOT002B)
    can call it outside a Flask request context. Does not flush or commit --
    the caller is responsible for the final commit.
    """
    db.session.add(SparePartPriceAudit(
        item_id=item_id,
        old_price=old_price,
        new_price=new_price,
        action=action,
        changed_by=user_id,
    ))


def _update_sku_price_stats(sku_id):
    """Recompute a SKU's informational last_price / avg_price from its
    confirmed-price history.

    [REASON]: SPARE-STAGE2 business rule — one direction only: the price
    CONFIRM workflow writes SKU stats; SKU stats never set or bypass a
    request price (SparePartPriceAudit + price_status stay the single source
    of truth). avg_price is a simple running average over every 'confirm'
    audit row of items referencing this SKU. Does not commit — the caller's
    transaction covers it.
    """
    if not sku_id:
        return
    sku = SparePartSku.query.get(sku_id)
    if sku is None:
        return
    rows = (db.session.query(SparePartPriceAudit.new_price)
            .join(SparePartRequestItem,
                  SparePartPriceAudit.item_id == SparePartRequestItem.id)
            .filter(SparePartRequestItem.sku_id == sku_id,
                    SparePartPriceAudit.action == 'confirm',
                    SparePartPriceAudit.new_price.isnot(None))
            .order_by(SparePartPriceAudit.changed_at.asc(),
                      SparePartPriceAudit.id.asc())
            .all())
    prices = [float(r[0]) for r in rows]
    if not prices:
        return
    sku.last_price = prices[-1]
    sku.avg_price = round(sum(prices) / len(prices), 2)


def _check_repeat_orders(equipment_id, spare_part_id, exclude_request_id=None):
    """Repeat-order warning engine.

    Returns {'severity': 'red'|'yellow'|None, 'days_since': int|None, 'history': [...]}.

    [REASON]: SPARE-STAGE1 — same equipment + same catalog part requested again
    within 90 days is surfaced to the operator/approver. <=7 days is 'red',
    <=30 days 'yellow', <=90 days history only. Rejected requests are excluded.
    This engine WARNS only -- it never blocks saving or submitting.
    """
    empty = {'severity': None, 'days_since': None, 'history': []}
    if not equipment_id or not spare_part_id:
        return empty
    window_start = date.today() - timedelta(days=90)
    q = (db.session.query(SparePartRequestItem, SparePartRequest)
         .join(SparePartRequest,
               SparePartRequestItem.request_id == SparePartRequest.id)
         .filter(SparePartRequest.equipment_id == equipment_id,
                 SparePartRequestItem.spare_part_id == spare_part_id,
                 SparePartRequest.request_date >= window_start,
                 SparePartRequest.status != 'rejected'))
    if exclude_request_id:
        q = q.filter(SparePartRequest.id != exclude_request_id)
    rows = q.order_by(SparePartRequest.request_date.desc(),
                      SparePartRequest.id.desc()).all()
    if not rows:
        return empty
    history = [{
        'request_id': req.id,
        'request_date': _date_iso(req.request_date),
        'status': req.status,
        'quantity': item.quantity,
        'unit': item.unit or '',
    } for item, req in rows]
    days_since = (date.today() - rows[0][1].request_date).days
    if days_since <= 7:
        severity = 'red'
    elif days_since <= 30:
        severity = 'yellow'
    else:
        severity = None
    return {'severity': severity, 'days_since': days_since, 'history': history}


# ─── SPARE-STAGE2: warning rules 3, 4, 6 ─────────────────────────────────────
# Additive rule set alongside the SPARE-STAGE1 repeat engine above; the
# _check_repeat_orders function (rules 1-2) is deliberately not modified.

def _fmt_sum_text(value):
    """1234567.0 -> '1 234 567' (same style as the fmt_sum Jinja filter in app.py)."""
    return '{:,.0f}'.format(float(value or 0)).replace(',', ' ')


def _month_start_back(d, months_back):
    """First day of the month `months_back` calendar months before d's month."""
    total = d.year * 12 + (d.month - 1) - months_back
    return date(total // 12, total % 12 + 1, 1)


def _check_rule3_frequency(equipment_id, spare_part_id, exclude_request_id=None):
    """Rule 3 — frequency: same equipment + same canonical part on more than
    RULE3_MAX_OCCURRENCES_90D approved requests in the trailing window.

    [REASON]: SPARE-STAGE2 — approved requests only (unlike rules 1-2, which
    count everything non-rejected): the frequency signal is about parts the
    business actually paid for repeatedly, per the task spec. Yellow only.
    """
    window_start = date.today() - timedelta(days=RULE3_WINDOW_DAYS)
    q = (db.session.query(SparePartRequest.id, SparePartRequest.request_date)
         .join(SparePartRequestItem,
               SparePartRequestItem.request_id == SparePartRequest.id)
         .filter(SparePartRequest.equipment_id == equipment_id,
                 SparePartRequestItem.spare_part_id == spare_part_id,
                 SparePartRequest.request_date >= window_start,
                 SparePartRequest.status == 'approved'))
    if exclude_request_id:
        q = q.filter(SparePartRequest.id != exclude_request_id)
    # distinct: the same part listed twice on one request counts once.
    rows = q.distinct().order_by(SparePartRequest.request_date.desc()).all()
    if len(rows) <= RULE3_MAX_OCCURRENCES_90D:
        return None
    shown = [r.request_date.strftime('%d.%m.%Y') for r in rows[:8]]
    dates_text = ', '.join(shown) + ('…' if len(rows) > 8 else '')
    return {
        'rule': 3,
        'severity': 'yellow',
        'count': len(rows),
        'dates': [r.request_date.isoformat() for r in rows],
        'message': _spare_t(
            'Охирги {} кунда бу эҳтиёт қисм ушбу техника учун {} марта тасдиқланган: {}',
            'За последние {} дней эта деталь для этой техники уже утверждалась {} раз: {}'
        ).format(RULE3_WINDOW_DAYS, len(rows), dates_text),
    }


def _check_rule4_cost_anomaly(equipment_id, spare_part_id, exclude_request_id=None):
    """Rule 4 — category cost anomaly: current calendar month approved+confirmed
    spend for this equipment in this item's category vs the average of the
    RULE4_BASELINE_MONTHS months before the current one.

    [REASON]: SPARE-STAGE2 — spend counts only authorized money (approved
    request + confirmed price, same rule as the SPARE-REPORTS cost tables).
    No warning without at least one prior month of spend — a first-ever
    purchase in a category has nothing to compare against.
    """
    part = SparePart.query.get(spare_part_id)
    if part is None or part.category_id is None:
        return None
    today = date.today()
    month_start = today.replace(day=1)
    next_month_start = _month_start_back(today, -1)
    baseline_start = _month_start_back(today, RULE4_BASELINE_MONTHS)
    q = (db.session.query(SparePartRequestItem.price,
                          SparePartRequestItem.quantity,
                          SparePartRequest.request_date)
         .join(SparePartRequest,
               SparePartRequestItem.request_id == SparePartRequest.id)
         .join(SparePart, SparePartRequestItem.spare_part_id == SparePart.id)
         .filter(SparePartRequest.equipment_id == equipment_id,
                 SparePart.category_id == part.category_id,
                 SparePartRequest.status == 'approved',
                 SparePartRequestItem.price_status == 'confirmed',
                 SparePartRequest.request_date >= baseline_start,
                 SparePartRequest.request_date < next_month_start))
    if exclude_request_id:
        q = q.filter(SparePartRequest.id != exclude_request_id)
    current_total = 0.0
    prior_total = 0.0
    prior_months_with_data = set()
    for price, quantity, req_date in q.all():
        cost = float(price or 0) * float(quantity or 0)
        if req_date >= month_start:
            current_total += cost
        else:
            prior_total += cost
            prior_months_with_data.add((req_date.year, req_date.month))
    if not prior_months_with_data or prior_total <= 0:
        return None
    baseline_avg = prior_total / RULE4_BASELINE_MONTHS
    if current_total <= RULE4_COST_ANOMALY_MULTIPLIER * baseline_avg:
        return None
    category = part.category_ref
    cat_name = ((category.name_ru if _spare_lang() == 'ru' else category.name_uz)
                if category else '')
    return {
        'rule': 4,
        'severity': 'yellow',
        'current_month_total': round(current_total, 2),
        'baseline_avg': round(baseline_avg, 2),
        'message': _spare_t(
            '«{cat}» категорияси: шу ойдаги харажат {cur} сўм — олдинги {months} ой ўртачасидан ({avg} сўм) {mult} баробардан кўп',
            'Категория «{cat}»: расходы в этом месяце {cur} сум — более чем в {mult} раза выше среднего за прошлые {months} мес. ({avg} сум)'
        ).format(cat=cat_name,
                 cur=_fmt_sum_text(current_total),
                 avg=_fmt_sum_text(baseline_avg),
                 months=RULE4_BASELINE_MONTHS,
                 mult=RULE4_COST_ANOMALY_MULTIPLIER),
    }


def _check_rule6_recent_rejection(equipment_id, spare_part_id, exclude_request_id=None):
    """Rule 6 — recently rejected, resubmitted: same equipment + same part was
    on a request rejected within the trailing RULE6_REJECTED_LOOKBACK_DAYS.

    The warning carries the rejection date and the reviewer's comment so the
    approver sees WHY it was rejected last time.
    """
    cutoff_dt = datetime.utcnow() - timedelta(days=RULE6_REJECTED_LOOKBACK_DAYS)
    cutoff_date = date.today() - timedelta(days=RULE6_REJECTED_LOOKBACK_DAYS)
    q = (db.session.query(SparePartRequest)
         .join(SparePartRequestItem,
               SparePartRequestItem.request_id == SparePartRequest.id)
         .filter(SparePartRequest.equipment_id == equipment_id,
                 SparePartRequestItem.spare_part_id == spare_part_id,
                 SparePartRequest.status == 'rejected',
                 # [REASON]: reviewed_at is when the rejection actually happened;
                 # request_date is only the fallback for legacy rows without it.
                 db.or_(SparePartRequest.reviewed_at >= cutoff_dt,
                        db.and_(SparePartRequest.reviewed_at.is_(None),
                                SparePartRequest.request_date >= cutoff_date))))
    if exclude_request_id:
        q = q.filter(SparePartRequest.id != exclude_request_id)
    rejected = q.order_by(SparePartRequest.reviewed_at.desc(),
                          SparePartRequest.id.desc()).first()
    if rejected is None:
        return None
    rejected_on = (rejected.reviewed_at.date() if rejected.reviewed_at
                   else rejected.request_date)
    comment = (rejected.review_comment or '').strip()
    comment_text = comment if comment else _spare_t('изоҳсиз', 'без комментария')
    return {
        'rule': 6,
        'severity': 'yellow',
        'request_id': rejected.id,
        'rejected_date': rejected_on.isoformat(),
        'comment': comment,
        'message': _spare_t(
            'Бу эҳтиёт қисм бўйича #{} сўров {} куни рад этилган. Изоҳ: {}',
            'Похожая заявка #{} на эту деталь была отклонена {}. Комментарий: {}'
        ).format(rejected.id, rejected_on.strftime('%d.%m.%Y'), comment_text),
    }


def _check_extra_warnings(equipment_id, spare_part_id, exclude_request_id=None):
    """SPARE-STAGE2 aggregator for rules 3/4/6.

    Returns a list of warning dicts (possibly empty). Purely additive to the
    rules 1-2 engine: callers keep using _check_repeat_orders unchanged and
    attach this list alongside it.
    """
    if not equipment_id or not spare_part_id:
        return []
    warnings = []
    for check in (_check_rule3_frequency,
                  _check_rule4_cost_anomaly,
                  _check_rule6_recent_rejection):
        result = check(equipment_id, spare_part_id, exclude_request_id)
        if result:
            warnings.append(result)
    return warnings


def _photo_required(item):
    """True when the item needs at least one photo before submit.

    [REASON]: SPARE-STAGE1 — categories with kind='unit' require photo proof;
    an item whose catalog part has no category yet (including fresh
    pending_review candidates) is treated as requiring a photo (safe default).
    Only kind='consumable' makes photos optional.
    """
    part = item.spare_part
    if part is None or part.category_id is None:
        return True
    cat = part.category_ref
    return (cat.kind if cat else 'unit') != 'consumable'


def _items_missing_photos(items):
    """Return bilingual row labels for items that require a photo but have none."""
    missing = []
    for idx, item in enumerate(items, start=1):
        if not _photo_required(item):
            continue
        if not (item.attachments or []):
            missing.append('{}. {}'.format(idx, item.name))
    return missing


# ─── SPARE-STAGE1: photo upload infrastructure ────────────────────────────────

def _upload_dir():
    """Absolute path of the spare parts upload folder (created on demand).

    UPLOAD_FOLDER is a plain path relative to the app folder so the Telegram
    bot process can resolve the same location without Flask context.
    """
    folder = current_app.config.get('UPLOAD_FOLDER') or 'instance/uploads/spare_parts'
    if not os.path.isabs(folder):
        folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), folder)
    os.makedirs(folder, exist_ok=True)
    return folder


def _validate_photo(fs):
    """Validate an uploaded photo or video attachment.

    Returns (ext, None) on success, or (None, bilingual error) on failure.

    [REASON]: MOBILE-UPLOAD-001 — extends the SPARE-STAGE1 JPG/PNG-only
    check to the formats mechanics' phones actually produce (WEBP/HEIC
    photos, MP4/MOV/WEBM/AVI/MKV videos). Extension whitelist, size limit
    AND content magic-byte check are all required; the client-supplied
    Content-Type is never trusted.
    """
    filename = fs.filename or ''
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_ATTACHMENT_EXTENSIONS:
        return None, _spare_t(
            '«{}»: рухсат этилган форматлар — JPG, PNG, WEBP, HEIC/HEIF, MP4, MOV, WEBM, AVI, MKV',
            '«{}»: допустимые форматы — JPG, PNG, WEBP, HEIC/HEIF, MP4, MOV, WEBM, AVI, MKV').format(filename)

    fs.stream.seek(0, os.SEEK_END)
    size = fs.stream.tell()
    fs.stream.seek(0)
    if size > MAX_ATTACHMENT_SIZE:
        return None, _spare_t(
            '«{}»: файл ҳажми {} МБдан ошмаслиги керак',
            '«{}»: размер файла не должен превышать {} МБ').format(filename, MAX_ATTACHMENT_SIZE_MB)

    # [REASON]: WEBP/AVI (RIFF) signatures need bytes up to offset 12; read a
    # 16-byte header once and slice it for every check below.
    head = fs.stream.read(16)
    fs.stream.seek(0)

    if ext in ('.jpg', '.jpeg') and head.startswith(b'\xff\xd8\xff'):
        return ext, None
    if ext == '.png' and head.startswith(b'\x89PNG\r\n\x1a\n'):
        return ext, None
    if ext == '.webp' and head[0:4] == b'RIFF' and head[8:12] == b'WEBP':
        return ext, None
    if ext == '.avi' and head[0:4] == b'RIFF' and head[8:12] == b'AVI ':
        return ext, None
    if ext in ('.mp4', '.mov', '.heic', '.heif') and head[4:8] == b'ftyp':
        return ext, None
    if ext in ('.webm', '.mkv') and head[0:4] == b'\x1a\x45\xdf\xa3':
        return ext, None

    return None, _spare_t(
        '«{}»: файл мазмуни кўрсатилган кенгайтмага мос эмас',
        '«{}»: содержимое файла не соответствует указанному расширению').format(filename)


def _store_item_photo(fs, item_id, user_id):
    """Validate and persist one uploaded photo for an item.

    Returns a bilingual error string, or None on success. Adds the
    SparePartAttachment row without committing (caller commits).

    [REASON]: SPARE-STAGE1 — the on-disk name is built ONLY from item_id and a
    random suffix; original_filename is stored as metadata, never used for the
    path (path traversal risk), and the naming scheme is reproducible by the
    future bot process.
    """
    ext, err = _validate_photo(fs)
    if err:
        return err
    fname = '{}_{}{}'.format(item_id, uuid.uuid4().hex[:8], ext)
    fs.save(os.path.join(_upload_dir(), fname))
    db.session.add(SparePartAttachment(
        item_id=item_id,
        file_path=fname,
        original_filename=(fs.filename or '')[:300],
        file_size=os.path.getsize(os.path.join(_upload_dir(), fname)),
        uploaded_by=user_id,
    ))
    return None


def _store_attachments_for_item(files, item_id, user_id, existing_count):
    """Validate and store files for one item, up to MAX_ATTACHMENTS_PER_ITEM total.

    Returns (stored_count, errors). Files beyond the per-item cap (existing
    attachments + files already stored earlier in this same call) are
    rejected with a clear bilingual error instead of being silently dropped
    or truncated mid-file -- the caller's already-stored files are untouched.

    [REASON]: MOBILE-UPLOAD-001 — shared by the create-request flow (always
    existing_count=0) and the detail-page add-more flow (existing_count is
    the item's current SparePartAttachment count), so both enforce the same
    5-photos-and-videos-combined-per-item cap the same way.
    """
    errors = []
    stored = 0
    for fs in files:
        if existing_count + stored >= MAX_ATTACHMENTS_PER_ITEM:
            errors.append(_spare_t(
                '«{}»: позицияга энг кўпи билан {} та файл бириктириш мумкин',
                '«{}»: к позиции можно прикрепить не более {} файлов'
            ).format(fs.filename or '', MAX_ATTACHMENTS_PER_ITEM))
            continue
        err = _store_item_photo(fs, item_id, user_id)
        if err:
            errors.append(err)
        else:
            stored += 1
    return stored, errors


def _store_submitted_item_photos(created_items):
    """Intake per-row photo/video files posted with the request form.

    created_items is a list of (item, prepared) pairs; each row's files arrive
    as item_photo_{form_index}. Invalid files are skipped and reported — if
    that leaves a mandatory photo/video missing, the submit gate keeps the
    request as a draft with its own explicit message.
    """
    errors = []
    for item, prepared in created_items:
        files = [fs for fs in
                 request.files.getlist('item_photo_{}'.format(prepared['form_index']))
                 if fs and fs.filename]
        _, item_errors = _store_attachments_for_item(
            files, item.id, current_user.id, existing_count=0)
        errors.extend(item_errors)
    if errors:
        _spare_flash_errors(errors,
                            title_uz='Баъзи фотолар қабул қилинмади:',
                            title_ru='Некоторые фото не приняты:')


def _approval_blockers(spr):
    """Bilingual error list explaining why a submitted request cannot be approved.

    [REASON]: SPARE-STAGE1 approval gate — a request is approvable only when
    every item points to a reviewed catalog entry (no pending_review) and every
    item price is confirmed.
    """
    errors = []
    for idx, item in enumerate(spr.items, start=1):
        part = item.spare_part
        if part is not None and part.status == 'pending_review':
            errors.append(_spare_t(
                '{}-позиция «{}»: янги каталог ёзуви ҳали текширилмаган (каталог менежери тасдиқлаши керак)',
                '{}. позиция «{}»: новая запись каталога ещё не проверена (нужно решение менеджера каталога)'
            ).format(idx, item.name))
        if item.price_status != 'confirmed':
            errors.append(_spare_t(
                '{}-позиция «{}»: нарх тасдиқланмаган',
                '{}. позиция «{}»: цена не подтверждена'
            ).format(idx, item.name))
    return errors


@spare_parts_bp.route('/')
@module_required('spare_parts')
def index():
    lang = getattr(g, 'lang', 'uz')
    status_filter = request.args.get('status', '')
    org_id = request.args.get('org_id', type=int)
    date_from_s = request.args.get('date_from', '')
    date_to_s = request.args.get('date_to', '')

    # PERF-SPARE-001B: eager-load spare parts index relations.
    # [REASON]: Avoid N+1 SELECTs for req.items, req.equipment, req.organization and req.creator.
    q = (SparePartRequest.query
         .options(
             joinedload(SparePartRequest.organization),
             joinedload(SparePartRequest.equipment),
             joinedload(SparePartRequest.creator),
             selectinload(SparePartRequest.items),
         ))
    user_org_ids = _spare_user_org_ids()
    if user_org_ids is not None:
        q = q.filter(SparePartRequest.organization_id.in_(user_org_ids))
    if status_filter:
        q = q.filter(SparePartRequest.status == status_filter)
    if org_id:
        if not current_user.can_access_org(org_id):
            abort(403)
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

    counts = {s: 0 for s in STATUS_LABELS}
    count_base = SparePartRequest.query
    if user_org_ids:
        count_base = count_base.filter(SparePartRequest.organization_id.in_(user_org_ids))

    count_rows = (count_base
                  .with_entities(SparePartRequest.status,
                                 func.count(SparePartRequest.id))
                  .group_by(SparePartRequest.status)
                  .all())

    counts['all'] = 0
    for row_status, row_count in count_rows:
        row_count = int(row_count or 0)
        counts['all'] += row_count
        if row_status in counts:
            counts[row_status] = row_count

    if user_org_ids is None:
        organizations = Organization.query.order_by(Organization.sort_order).all()
    else:
        organizations = (Organization.query
                         .filter(Organization.id.in_(user_org_ids))
                         .order_by(Organization.sort_order).all())
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
        flash(_spare_t('Сизда ҳуқуқ йўқ', 'У вас нет прав'), 'warning')
        return redirect(url_for('spare_parts.index'))
    user_org_ids = _spare_user_org_ids()
    if user_org_ids is None:
        organizations = Organization.query.order_by(Organization.sort_order).all()
    else:
        organizations = (Organization.query
                         .filter(Organization.id.in_(user_org_ids))
                         .order_by(Organization.sort_order).all())
    # [REASON]: SPARE-STAGE1 — equipment is now loaded per organization via
    # AJAX (api_equipment_by_org), so the full equipment list is no longer
    # rendered into the form. Catalog parts feed the datalist part picker.
    catalog_parts = (SparePart.query
                     .options(joinedload(SparePart.category_ref))
                     .filter(SparePart.status == 'active',
                             db.or_(SparePart.is_active == True,  # noqa: E712
                                    SparePart.is_active.is_(None)))
                     .order_by(SparePart.name)
                     .all())
    return render_template('spare_part_form.html',
                           req=None,
                           today=date.today().isoformat(),
                           organizations=organizations,
                           catalog_parts=catalog_parts,
                           lang=_spare_lang())


@spare_parts_bp.route('/save', methods=['POST'])
@module_required('spare_parts')
def save_request():
    if not current_user.can_edit:
        abort(403)
    action = request.form.get('action', 'draft')
    if action not in {'draft', 'submit'}:
        abort(400)
    status = 'submitted' if action == 'submit' else 'draft'

    req_date_s = request.form.get('request_date', date.today().isoformat())
    try:
        req_date = _parse_spare_date(req_date_s)
    except ValueError:
        flash(_spare_t('Сана нотўғри', 'Некорректная дата'), 'warning')
        return redirect(url_for('spare_parts.new_request'))

    org_id = request.form.get('organization_id', type=int)
    eq_id = request.form.get('equipment_id', type=int) or None
    note = request.form.get('note', '').strip()

    if not org_id:
        flash(_spare_t('Ташкилотни танланг', 'Выберите организацию'), 'warning')
        return redirect(url_for('spare_parts.new_request'))
    _spare_check_org_access(org_id)
    if eq_id:
        eq = Equipment.query.get_or_404(eq_id)
        if eq.organization_id != org_id or not eq.is_active or not current_user.can_access_org(eq.organization_id):
            abort(403)

    names = request.form.getlist('item_name')
    parts = request.form.getlist('item_part_number')
    qtys = request.form.getlist('item_quantity')
    units = request.form.getlist('item_unit')
    notes = request.form.getlist('item_note')
    # [REASON]: SPARE-STAGE1 — hidden per-row field filled by the catalog part
    # picker; empty value means free-text entry ("not in catalog" flow).
    sp_ids = request.form.getlist('item_spare_part_id')
    # SPARE-STAGE2 — optional per-row SKU choice (select is always submitted,
    # empty value = no SKU, which keeps the exact Stage 1 item behaviour).
    sku_ids = request.form.getlist('item_sku_id')

    prepared_items = []
    validation_errors = []
    for i, raw_name in enumerate(names):
        row_no = i + 1
        name = raw_name.strip()
        qty_raw = qtys[i].strip() if i < len(qtys) else ''
        part_number = parts[i].strip() if i < len(parts) else ''
        unit = units[i].strip() if i < len(units) else 'dona'
        item_note = notes[i].strip() if i < len(notes) else ''
        sp_raw = sp_ids[i].strip() if i < len(sp_ids) else ''

        if not name and not qty_raw and not part_number and not item_note:
            continue
        if not name:
            validation_errors.append(_spare_t('{}. қатор: запчасть номини киритинг', '{} строка: введите название запчасти').format(row_no))
            continue
        try:
            qty = _parse_spare_positive_qty(qty_raw)
        except ValueError:
            validation_errors.append(_spare_t('{}. қатор: миқдор мусбат бўлиши керак', '{} строка: количество должно быть больше нуля').format(row_no))
            continue

        # Resolve the catalog link. A stale/merged/inactive id silently falls
        # back to the free-text flow instead of failing the whole request.
        spare_part = None
        if sp_raw:
            try:
                sp_id = int(sp_raw)
            except ValueError:
                sp_id = None
            if sp_id:
                cand = SparePart.query.get(sp_id)
                if cand and cand.status == 'merged' and cand.merged_into_id:
                    cand = SparePart.query.get(cand.merged_into_id)
                if cand and cand.status in ('active', 'pending_review') and cand.is_active != False:  # noqa: E712
                    spare_part = cand

        # [REASON]: SPARE-STAGE2 — resolve the optional SKU with the same
        # silent-fallback rule as the catalog link above: a stale, inactive or
        # wrong-part SKU id degrades to "no SKU" instead of failing the save.
        sku = None
        sku_raw = sku_ids[i].strip() if i < len(sku_ids) else ''
        if sku_raw and spare_part is not None:
            try:
                sku_id = int(sku_raw)
            except ValueError:
                sku_id = None
            if sku_id:
                sku_cand = SparePartSku.query.get(sku_id)
                if (sku_cand and sku_cand.spare_part_id == spare_part.id
                        and sku_cand.is_active != False):  # noqa: E712
                    sku = sku_cand

        prepared_items.append({
            'name': name,
            'part_number': part_number,
            'quantity': qty,
            'unit': unit or 'dona',
            'note': item_note,
            'spare_part': spare_part,
            'sku': sku,
            'form_index': i,
        })

    if validation_errors:
        _spare_flash_errors(validation_errors)
        return redirect(url_for('spare_parts.new_request'))

    if not prepared_items:
        _spare_flash_errors([_spare_t('Камида битта позиция киритинг', 'Добавьте хотя бы одну позицию')])
        return redirect(url_for('spare_parts.new_request'))

    spr = SparePartRequest(
        request_date=req_date,
        organization_id=org_id,
        equipment_id=eq_id,
        status='draft',
        note=note,
        created_by=current_user.id,
    )
    db.session.add(spr)
    db.session.flush()

    created_items = []
    for prepared in prepared_items:
        item = SparePartRequestItem(
            request_id=spr.id,
            spare_part_id=prepared['spare_part'].id if prepared['spare_part'] else None,
            sku_id=prepared['sku'].id if prepared['sku'] else None,
            name=prepared['name'],
            part_number=prepared['part_number'],
            quantity=prepared['quantity'],
            unit=prepared['unit'],
            note=prepared['note'],
        )
        db.session.add(item)
        created_items.append((item, prepared))

    db.session.flush()

    # [REASON]: SPARE-STAGE1 "not in catalog" flow — a free-text item creates a
    # pending_review catalog candidate linked back to this item. The request
    # can be saved/submitted with the pending link but cannot be approved until
    # a catalog manager approves or merges the candidate (_approval_blockers).
    for item, prepared in created_items:
        if prepared['spare_part'] is not None:
            continue
        candidate = SparePart(
            name=item.name,
            part_number=item.part_number,
            unit=item.unit,
            status='pending_review',
            created_by=current_user.id,
            source_request_item_id=item.id,
        )
        db.session.add(candidate)
        db.session.flush()
        item.spare_part_id = candidate.id
        _audit_spare(
            'spare_part_catalog_created',
            entity_type='spare_part_catalog',
            entity_id=candidate.id,
            entity_label=candidate.name,
            after=_catalog_snapshot(candidate),
            description='Pending-review catalog candidate auto-created from request item #{}'.format(item.id)
        )

    _store_submitted_item_photos(created_items)
    db.session.flush()

    # [REASON]: SPARE-STAGE1 mandatory-photo rule — items in 'unit' (or not yet
    # categorized) categories need at least one photo before submit. To avoid
    # losing the operator's typed rows, a failed submit is kept as a draft with
    # a clear bilingual error; photos can be added on the detail page.
    if status == 'submitted':
        missing = _items_missing_photos([item for item, _ in created_items])
        if missing:
            status = 'draft'
            _spare_flash_errors(
                missing,
                title_uz='Сўров юборилмади, чорнов сифатида сақланди. Қуйидаги позицияларга камида битта фото керак:',
                title_ru='Заявка не отправлена и сохранена как черновик. Этим позициям нужно хотя бы одно фото:',
            )

    if status == 'submitted':
        spr.status = 'submitted'

    _audit_spare(
        'spare_part_request_created',
        entity_type='spare_part_request',
        entity_id=spr.id,
        entity_label='Request #{}'.format(spr.id),
        after=_request_snapshot(spr),
        description='Spare part request created'
    )
    for item, _prepared in created_items:
        _audit_spare(
            'spare_part_item_created',
            entity_type='spare_part_request_item',
            entity_id=item.id,
            entity_label=item.name,
            after=_item_snapshot(item),
            description='Spare part request item created'
        )
    # [REASON]: FIX003A — Record status history before commit.
    if status == 'submitted':
        _add_status_history(spr.id, None, 'submitted', changed_by=current_user.id)
    db.session.commit()
    if status == 'submitted':
        flash(_spare_t('Сўров юборилди', 'Заявка отправлена'), 'success')
        # [REASON]: BOT003 — Post-commit best-effort notification, never raises.
        if _BOT003_AVAILABLE:
            enqueue_spare_request_submitted_best_effort(spr.id)
    elif status == 'draft':
        flash(_spare_t('Чорнов сақланди', 'Черновик сохранён'), 'info')
    return redirect(url_for('spare_parts.detail', rid=spr.id))


@spare_parts_bp.route('/<int:rid>')
@module_required('spare_parts')
def detail(rid):
    lang = getattr(g, 'lang', 'uz')
    spr = SparePartRequest.query.get_or_404(rid)
    _spare_check_request_access(spr)
    # SPARE-STAGE1 action visibility flags for the template.
    can_price = (current_user.has_module_access('spare_parts_price_confirm')
                 and spr.status == 'submitted')
    can_approve = (current_user.has_module_access('spare_parts_approve')
                   and spr.status == 'submitted')
    # [REASON]: SPARE-STAGE2 — issuing is a separate permission from approval
    # (warehouse handover vs purchase decision); only approved requests offer it.
    can_issue = (current_user.has_module_access('spare_parts_issue')
                 and spr.status == 'approved')
    issue_ctx = _issue_context(spr) if can_issue else None
    # Acts are visible on the request once issued (permanent, no un-issue).
    acts = (SparePartWriteOffAct.query.filter_by(request_id=spr.id)
            .order_by(SparePartWriteOffAct.id).all())
    can_edit_photos = (current_user.can_edit
                       and spr.created_by == current_user.id
                       and spr.status in ('draft', 'returned_for_revision'))
    # [REASON]: SPARE-STAGE2 — the warn-only rules must be visible to the
    # reviewer at approval time, not only to the operator while typing the
    # request. Computed per item while the request is under review; rules 1-2
    # come from the untouched SPARE-STAGE1 engine, 3/4/6 from the new checks.
    item_warnings = {}
    if spr.status == 'submitted' and (can_price or can_approve):
        for item in spr.items:
            if not spr.equipment_id or not item.spare_part_id:
                continue
            base = _check_repeat_orders(spr.equipment_id, item.spare_part_id,
                                        exclude_request_id=spr.id)
            extra = _check_extra_warnings(spr.equipment_id, item.spare_part_id,
                                          exclude_request_id=spr.id)
            if base['severity'] or extra:
                item_warnings[item.id] = {'base': base, 'extra': extra}
    return render_template('spare_part_detail.html',
                           req=spr,
                           status_labels=STATUS_LABELS,
                           status_colors=STATUS_COLORS,
                           price_status_labels=PRICE_STATUS_LABELS,
                           price_status_colors=PRICE_STATUS_COLORS,
                           can_price=can_price,
                           can_approve=can_approve,
                           can_edit_photos=can_edit_photos,
                           can_issue=can_issue,
                           issue_ctx=issue_ctx,
                           acts=acts,
                           item_warnings=item_warnings,
                           lang=lang)


@spare_parts_bp.route('/<int:rid>/submit', methods=['POST'])
@module_required('spare_parts')
def submit_request(rid):
    spr = SparePartRequest.query.get_or_404(rid)
    _spare_check_request_access(spr)
    # [REASON]: SPARE-STAGE1 — the price reject/return workflow sends a request
    # back as 'returned_for_revision'; the owner fixes it and resubmits from
    # the same button, so both statuses are submittable.
    if spr.status not in ('draft', 'returned_for_revision') or spr.created_by != current_user.id:
        abort(403)
    # [REASON]: SPARE-STAGE1 mandatory-photo rule at submit time.
    missing = _items_missing_photos(spr.items)
    if missing:
        _spare_flash_errors(
            missing,
            title_uz='Сўров юборилмади. Қуйидаги позицияларга камида битта фото керак:',
            title_ru='Заявка не отправлена. Этим позициям нужно хотя бы одно фото:',
        )
        return redirect(url_for('spare_parts.detail', rid=rid))
    before = _request_snapshot(spr)
    old_status = spr.status
    spr.status = 'submitted'
    after = _request_snapshot(spr)
    _audit_spare(
        'spare_part_request_status_changed',
        entity_type='spare_part_request',
        entity_id=spr.id,
        entity_label='Request #{}'.format(spr.id),
        before=before,
        after=after,
        changes={'status': {'before': old_status, 'after': spr.status}},
        description='Spare part request submitted'
    )
    # [REASON]: FIX003A — Record status history before commit.
    _add_status_history(spr.id, old_status, 'submitted', changed_by=current_user.id)
    db.session.commit()
    flash(_spare_t('Сўров юборилди', 'Заявка отправлена'), 'success')
    # [REASON]: BOT003 — Post-commit best-effort notification, never raises.
    if _BOT003_AVAILABLE:
        enqueue_spare_request_submitted_best_effort(spr.id)
    return redirect(url_for('spare_parts.detail', rid=rid))


@spare_parts_bp.route('/<int:rid>/approve', methods=['POST'])
@module_required('spare_parts')
def approve_request(rid):
    # [REASON]: SPARE-STAGE1 — final approval now requires the explicit
    # 'spare_parts_approve' permission; has_module_access() keeps the admin
    # bypass, so admins retain access and nobody else gains it automatically.
    if not current_user.has_module_access('spare_parts_approve'):
        abort(403)
    spr = SparePartRequest.query.get_or_404(rid)
    _spare_check_request_access(spr)
    if spr.status != 'submitted':
        abort(400)
    # [REASON]: SPARE-STAGE1 approval gate — pending_review catalog candidates
    # and unconfirmed prices block approval with an itemised bilingual error.
    blockers = _approval_blockers(spr)
    if blockers:
        _spare_flash_errors(
            blockers,
            title_uz='Сўровни тасдиқлаб бўлмайди:',
            title_ru='Заявку нельзя утвердить:',
        )
        return redirect(url_for('spare_parts.detail', rid=rid))
    before = _request_snapshot(spr)
    old_status = spr.status
    spr.status = 'approved'
    spr.reviewed_by = current_user.id
    spr.reviewed_at = datetime.utcnow()
    spr.review_comment = request.form.get('review_comment', '').strip()
    after = _request_snapshot(spr)
    _audit_spare(
        'spare_part_request_status_changed',
        entity_type='spare_part_request',
        entity_id=spr.id,
        entity_label='Request #{}'.format(spr.id),
        before=before,
        after=after,
        changes=diff_dict(before, after),
        description='Spare part request approved; status {} -> {}'.format(old_status, spr.status)
    )
    # [REASON]: FIX003A — Record status history before commit.
    _add_status_history(spr.id, old_status, 'approved', comment=spr.review_comment or '', changed_by=current_user.id)
    db.session.commit()
    flash(_spare_t('Сўров тасдиқланди', 'Заявка утверждена'), 'success')
    # [REASON]: BOT003 — Post-commit best-effort notification, never raises.
    if _BOT003_AVAILABLE:
        enqueue_spare_request_status_best_effort(spr.id, 'spare_request_approved')
    return redirect(url_for('spare_parts.detail', rid=rid))


@spare_parts_bp.route('/<int:rid>/reject', methods=['POST'])
@module_required('spare_parts')
def reject_request(rid):
    # [REASON]: SPARE-STAGE1 — same permission as approval (see approve_request).
    if not current_user.has_module_access('spare_parts_approve'):
        abort(403)
    spr = SparePartRequest.query.get_or_404(rid)
    _spare_check_request_access(spr)
    if spr.status != 'submitted':
        abort(400)
    before = _request_snapshot(spr)
    old_status = spr.status
    spr.status = 'rejected'
    spr.reviewed_by = current_user.id
    spr.reviewed_at = datetime.utcnow()
    spr.review_comment = request.form.get('review_comment', '').strip()
    after = _request_snapshot(spr)
    _audit_spare(
        'spare_part_request_status_changed',
        entity_type='spare_part_request',
        entity_id=spr.id,
        entity_label='Request #{}'.format(spr.id),
        before=before,
        after=after,
        changes=diff_dict(before, after),
        description='Spare part request rejected; status {} -> {}'.format(old_status, spr.status)
    )
    # [REASON]: FIX003A — Record status history before commit.
    _add_status_history(spr.id, old_status, 'rejected', comment=spr.review_comment or '', changed_by=current_user.id)
    db.session.commit()
    flash(_spare_t('Сўров рад этилди', 'Заявка отклонена'), 'warning')
    # [REASON]: BOT003 — Post-commit best-effort notification, never raises.
    if _BOT003_AVAILABLE:
        enqueue_spare_request_status_best_effort(spr.id, 'spare_request_rejected')
    return redirect(url_for('spare_parts.detail', rid=rid))


# ─── SPARE-STAGE1: price workflow ─────────────────────────────────────────────

def _price_action_target(rid, item_id):
    """Shared guard for price actions: access, permission, status, ownership."""
    spr = SparePartRequest.query.get_or_404(rid)
    _spare_check_request_access(spr)
    # [REASON]: SPARE-STAGE1 — price actions require the explicit
    # 'spare_parts_price_confirm' permission (admin passes automatically).
    if not current_user.has_module_access('spare_parts_price_confirm'):
        abort(403)
    # Prices are set/confirmed while the request is under review.
    if spr.status != 'submitted':
        abort(400)
    item = SparePartRequestItem.query.get_or_404(item_id)
    if item.request_id != spr.id:
        abort(404)
    return spr, item


@spare_parts_bp.route('/<int:rid>/items/<int:item_id>/price/set', methods=['POST'])
@module_required('spare_parts')
def item_price_set(rid, item_id):
    spr, item = _price_action_target(rid, item_id)
    raw = (request.form.get('price', '') or '').strip()
    try:
        price = float(raw.replace(',', '.').replace(' ', ''))
        if price < 0:
            raise ValueError()
    except (TypeError, ValueError):
        _spare_flash_errors([_spare_t('Нарх мусбат сон бўлиши керак',
                                      'Цена должна быть неотрицательным числом')],
                            title_uz='Нарх сақланмади:', title_ru='Цена не сохранена:')
        return redirect(url_for('spare_parts.detail', rid=rid))
    old_price = item.price
    item.price = price
    # Setting a new value restarts the confirm cycle.
    item.price_status = 'pending'
    item.price_set_by = current_user.id
    item.price_set_at = datetime.utcnow()
    write_price_audit(item.id, old_price, price, 'set', current_user.id)
    _audit_spare(
        'spare_part_item_price_set',
        entity_type='spare_part_request_item',
        entity_id=item.id,
        entity_label=item.name,
        changes={'price': {'before': old_price, 'after': price}},
        description='Item price set on request #{}'.format(spr.id)
    )
    db.session.commit()
    flash(_spare_t('Нарх сақланди', 'Цена сохранена'), 'success')
    return redirect(url_for('spare_parts.detail', rid=rid))


@spare_parts_bp.route('/<int:rid>/items/<int:item_id>/price/confirm', methods=['POST'])
@module_required('spare_parts')
def item_price_confirm(rid, item_id):
    spr, item = _price_action_target(rid, item_id)
    if item.price is None:
        _spare_flash_errors([_spare_t('Аввал нархни киритинг', 'Сначала укажите цену')],
                            title_uz='Нарх тасдиқланмади:', title_ru='Цена не подтверждена:')
        return redirect(url_for('spare_parts.detail', rid=rid))
    item.price_status = 'confirmed'
    write_price_audit(item.id, item.price, item.price, 'confirm', current_user.id)
    # [REASON]: SPARE-STAGE2 — confirm is the only event that feeds SKU price
    # stats (one-way write; see _update_sku_price_stats). Runs before commit so
    # the stats land in the same transaction as the confirmation itself.
    if item.sku_id:
        _update_sku_price_stats(item.sku_id)
    _audit_spare(
        'spare_part_item_price_confirmed',
        entity_type='spare_part_request_item',
        entity_id=item.id,
        entity_label=item.name,
        changes={'price_status': {'before': 'pending', 'after': 'confirmed'}},
        description='Item price confirmed on request #{}'.format(spr.id)
    )
    db.session.commit()
    flash(_spare_t('Нарх тасдиқланди', 'Цена подтверждена'), 'success')
    return redirect(url_for('spare_parts.detail', rid=rid))


@spare_parts_bp.route('/<int:rid>/items/<int:item_id>/price/reject', methods=['POST'])
@module_required('spare_parts')
def item_price_reject(rid, item_id):
    spr, item = _price_action_target(rid, item_id)
    mode = request.form.get('mode', 'reject')
    if mode not in ('reject', 'return'):
        abort(400)
    comment = (request.form.get('comment', '') or '').strip()
    old_price_status = item.price_status
    item.price_status = 'rejected' if mode == 'reject' else 'returned'
    write_price_audit(item.id, item.price, item.price, mode, current_user.id)
    # [REASON]: SPARE-STAGE1 — a price rejection/return sends the whole request
    # back to the operator; recorded in BOTH SparePartPriceAudit (above) and
    # SparePartStatusHistory (below), per the approved workflow.
    old_status = spr.status
    spr.status = 'returned_for_revision'
    _add_status_history(spr.id, old_status, 'returned_for_revision',
                        comment=comment or 'Item #{} price {}'.format(item.id, item.price_status),
                        changed_by=current_user.id)
    _audit_spare(
        'spare_part_request_status_changed',
        entity_type='spare_part_request',
        entity_id=spr.id,
        entity_label='Request #{}'.format(spr.id),
        changes={'status': {'before': old_status, 'after': spr.status},
                 'item_price_status': {'before': old_price_status, 'after': item.price_status}},
        description='Request returned for revision: item #{} price {}'.format(item.id, item.price_status)
    )
    db.session.commit()
    flash(_spare_t('Сўров қайта ишлашга қайтарилди', 'Заявка возвращена на доработку'), 'warning')
    return redirect(url_for('spare_parts.detail', rid=rid))


# ─── SPARE-STAGE1: photo routes ───────────────────────────────────────────────

@spare_parts_bp.route('/<int:rid>/items/<int:item_id>/photo', methods=['POST'])
@module_required('spare_parts')
def item_photo_upload(rid, item_id):
    spr = SparePartRequest.query.get_or_404(rid)
    _spare_check_request_access(spr)
    item = SparePartRequestItem.query.get_or_404(item_id)
    if item.request_id != spr.id:
        abort(404)
    is_owner = current_user.can_edit and spr.created_by == current_user.id
    if not (is_owner or current_user.is_admin):
        abort(403)
    # Photos are attached while the operator can still change the request.
    if spr.status not in ('draft', 'returned_for_revision'):
        abort(400)
    # [REASON]: MOBILE-UPLOAD-001 — freshly counted (not item.attachments, which
    # may be a stale relationship cache) so the 5-per-item cap is enforced
    # against what is actually stored right now.
    existing_count = SparePartAttachment.query.filter_by(item_id=item.id).count()
    files = [fs for fs in request.files.getlist('photo') if fs and fs.filename]
    stored, errors = _store_attachments_for_item(
        files, item.id, current_user.id, existing_count)
    if errors:
        _spare_flash_errors(errors,
                            title_uz='Баъзи фотолар қабул қилинмади:',
                            title_ru='Некоторые фото не приняты:')
    if stored:
        _audit_spare(
            'spare_part_item_photo_uploaded',
            entity_type='spare_part_request_item',
            entity_id=item.id,
            entity_label=item.name,
            description='{} photo(s) uploaded for item on request #{}'.format(stored, spr.id)
        )
        db.session.commit()
        flash(_spare_t('Фото юкланди', 'Фото загружено'), 'success')
    elif not errors:
        flash(_spare_t('Файл танланмаган', 'Файл не выбран'), 'warning')
    return redirect(url_for('spare_parts.detail', rid=rid))


@spare_parts_bp.route('/attachments/<int:att_id>')
@module_required('spare_parts')
def attachment_file(att_id):
    att = SparePartAttachment.query.get_or_404(att_id)
    item = att.item
    spr = item.request if item else None
    if spr is None:
        abort(404)
    # [REASON]: SPARE-STAGE1 — uploads are NOT served as raw static files; the
    # same org-scoped access check as detail(rid) applies to every download.
    _spare_check_request_access(spr)
    fname = os.path.basename(att.file_path or '')
    if not fname:
        abort(404)
    # [REASON]: MOBILE-UPLOAD-001 — explicit Content-Type per stored
    # extension so videos render/scrub correctly in <video> and images
    # don't fall back to a generic download prompt; conditional=True (Flask
    # default) gives Range-request support for free, which video seeking
    # needs.
    ext = os.path.splitext(fname)[1].lower()
    mimetype = ATTACHMENT_CONTENT_TYPES.get(ext, 'application/octet-stream')
    return send_from_directory(_upload_dir(), fname, mimetype=mimetype)


# ─── SPARE-STAGE1: JSON endpoints ─────────────────────────────────────────────

@spare_parts_bp.route('/api/equipment-by-org')
@module_required('spare_parts')
def api_equipment_by_org():
    # [REASON]: SPARE-STAGE1 — mirrors work_orders.api_equipment_by_org (that
    # file is outside this task's scope fence and returns no eq_type, which the
    # spare parts form must show as read-only reference text). Same org-access
    # rule and active-equipment filter.
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
            'eq_type': e.eq_type or '',
            'category': e.category_display,
        }
        for e in eqs
    ])


@spare_parts_bp.route('/api/repeat-check')
@module_required('spare_parts')
def api_repeat_check():
    equipment_id = request.args.get('equipment_id', type=int)
    spare_part_id = request.args.get('spare_part_id', type=int)
    exclude_request_id = request.args.get('exclude_request_id', type=int)
    empty = {'severity': None, 'days_since': None, 'history': [], 'extra_warnings': []}
    if not equipment_id or not spare_part_id:
        return jsonify(empty)
    eq = Equipment.query.get(equipment_id)
    if not eq or not current_user.can_access_org(eq.organization_id):
        return jsonify(empty)
    result = _check_repeat_orders(equipment_id, spare_part_id, exclude_request_id)
    # [REASON]: SPARE-STAGE2 — rules 3/4/6 ride along under an additive key;
    # the original rules 1-2 payload stays byte-identical for existing callers.
    result['extra_warnings'] = _check_extra_warnings(
        equipment_id, spare_part_id, exclude_request_id)
    return jsonify(result)


@spare_parts_bp.route('/api/price-suggestions')
@module_required('spare_parts')
def api_price_suggestions():
    # [REASON]: SPARE-STAGE1 — last 3 confirmed prices for a catalog part,
    # shown as a hint when setting a price. Exact part match only, no fuzzy.
    spare_part_id = request.args.get('spare_part_id', type=int)
    if not spare_part_id:
        return jsonify([])
    rows = (SparePartPriceAudit.query
            .join(SparePartRequestItem,
                  SparePartPriceAudit.item_id == SparePartRequestItem.id)
            .filter(SparePartRequestItem.spare_part_id == spare_part_id,
                    SparePartPriceAudit.action == 'confirm')
            .order_by(SparePartPriceAudit.changed_at.desc())
            .limit(3)
            .all())
    return jsonify([
        {'price': a.new_price,
         'date': a.changed_at.date().isoformat() if a.changed_at else None}
        for a in rows
    ])


# ─── SPARE-STAGE2: fuzzy catalog search ───────────────────────────────────────

def _norm_search_text(value):
    """Normalize text for catalog search comparison.

    [REASON]: SPARE-STAGE2 — same normalization approach as app.py's
    _task_ref_001d_norm (casefold + collapse whitespace), plus punctuation
    folded to spaces, so «Фильтр, масляный.» equals «фильтр масляный» before
    any fuzzy scoring; difflib then only has to absorb genuine typos.
    """
    value = (value or '').casefold()
    value = re.sub(r'[^\w\s]+', ' ', value)
    return re.sub(r'\s+', ' ', value).strip()


def _fuzzy_score(query_norm, name_norm):
    """Best difflib ratio between direct and token-sorted comparison.

    [REASON]: the token-sorted pass makes word-order swaps («масляный фильтр»
    vs «фильтр масляный») score near 1.0 instead of ~0.5, without any
    third-party fuzzy library.
    """
    direct = SequenceMatcher(None, query_norm, name_norm).ratio()
    query_sorted = ' '.join(sorted(query_norm.split()))
    name_sorted = ' '.join(sorted(name_norm.split()))
    if query_sorted == query_norm and name_sorted == name_norm:
        return direct
    return max(direct, SequenceMatcher(None, query_sorted, name_sorted).ratio())


def _search_catalog_parts(query, limit=FUZZY_SEARCH_MAX_RESULTS):
    """Ranked catalog search over selectable parts: exact > substring > fuzzy.

    Matches against the part name only (single `name` column in Stage 1/2).
    Returns a list of (part, match_kind, score) tuples. Stdlib difflib only —
    at this catalog's scale (a few hundred rows) scoring the full candidate
    list per request is fast enough, no search dependency needed.

    [REASON]: exact and substring tiers always rank above fuzzy-only hits, so
    a correctly typed name can never be outranked by a fuzzy competitor.
    """
    query_norm = _norm_search_text(query)
    if not query_norm:
        return []
    parts = (SparePart.query
             .options(joinedload(SparePart.category_ref))
             .filter(SparePart.status == 'active',
                     db.or_(SparePart.is_active == True,  # noqa: E712
                            SparePart.is_active.is_(None)))
             .order_by(SparePart.name)
             .all())
    exact, substr, fuzzy = [], [], []
    for part in parts:
        name_norm = _norm_search_text(part.name)
        if not name_norm:
            continue
        if name_norm == query_norm:
            exact.append((part, 'exact', 1.0))
            continue
        score = _fuzzy_score(query_norm, name_norm)
        if query_norm in name_norm:
            substr.append((part, 'substring', score))
        elif score >= FUZZY_MATCH_MIN_RATIO:
            fuzzy.append((part, 'fuzzy', score))
    # Python sort is stable: ties inside a tier keep alphabetical order.
    substr.sort(key=lambda t: -t[2])
    fuzzy.sort(key=lambda t: -t[2])
    return (exact + substr + fuzzy)[:limit]


@spare_parts_bp.route('/api/catalog-search')
@module_required('spare_parts')
def api_catalog_search():
    """Typo-tolerant catalog picker search (SPARE-STAGE2, Task 2).

    Used by the request form's part picker and the catalog screen's search
    box. Same candidate set as the form's datalist: active, non-merged parts.
    """
    q = (request.args.get('q', '') or '').strip()
    if len(q) < 2:
        return jsonify([])
    return jsonify([
        {
            'id': part.id,
            'name': part.name,
            'part_number': part.part_number or '',
            'unit': part.unit or 'dona',
            'kind': (part.category_ref.kind if part.category_ref else ''),
            'match': match_kind,
            'score': round(score, 3),
        }
        for part, match_kind, score in _search_catalog_parts(q)
    ])


# ─── SPARE-STAGE2: SKU catalog ────────────────────────────────────────────────

def _sku_snapshot(sku):
    if not sku:
        return None
    return {
        'id': getattr(sku, 'id', None),
        'spare_part_id': getattr(sku, 'spare_part_id', None),
        'brand': getattr(sku, 'brand', ''),
        'article_number': getattr(sku, 'article_number', ''),
        'supplier': getattr(sku, 'supplier', ''),
        'last_price': getattr(sku, 'last_price', None),
        'avg_price': getattr(sku, 'avg_price', None),
        'is_active': getattr(sku, 'is_active', None),
    }


@spare_parts_bp.route('/api/skus-by-part')
@module_required('spare_parts')
def api_skus_by_part():
    """Active SKUs of one canonical part, for the request form's SKU picker.

    last_price is included as a SUGGESTION only — the price confirm workflow
    still applies to whatever ends up on the request item.
    """
    spare_part_id = request.args.get('spare_part_id', type=int)
    if not spare_part_id:
        return jsonify([])
    skus = (SparePartSku.query
            .filter(SparePartSku.spare_part_id == spare_part_id,
                    db.or_(SparePartSku.is_active == True,  # noqa: E712
                           SparePartSku.is_active.is_(None)))
            .order_by(SparePartSku.brand, SparePartSku.article_number,
                      SparePartSku.id)
            .all())
    return jsonify([
        {
            'id': s.id,
            'brand': s.brand or '',
            'article_number': s.article_number or '',
            'supplier': s.supplier or '',
            'label': s.label,
            'last_price': s.last_price,
            'avg_price': s.avg_price,
        }
        for s in skus
    ])


@spare_parts_bp.route('/skus')
@module_required('spare_parts')
def skus():
    # [REASON]: SPARE-STAGE2 — SKU management is part of catalog stewardship,
    # so it sits behind the existing spare_parts_catalog_manage permission
    # (no new permission for this screen, per the task decision).
    if not current_user.has_module_access('spare_parts_catalog_manage'):
        abort(403)
    sku_rows = (SparePartSku.query
                .options(joinedload(SparePartSku.spare_part))
                .order_by(SparePartSku.spare_part_id, SparePartSku.brand,
                          SparePartSku.id)
                .all())
    catalog_parts = (SparePart.query
                     .filter(SparePart.status == 'active',
                             db.or_(SparePart.is_active == True,  # noqa: E712
                                    SparePart.is_active.is_(None)))
                     .order_by(SparePart.name)
                     .all())
    return render_template('spare_parts_skus.html',
                           sku_rows=sku_rows,
                           catalog_parts=catalog_parts,
                           lang=_spare_lang())


@spare_parts_bp.route('/skus/save', methods=['POST'])
@module_required('spare_parts')
def sku_save():
    if not current_user.has_module_access('spare_parts_catalog_manage'):
        abort(403)
    sku_id = request.form.get('id', type=int)
    spare_part_id = request.form.get('spare_part_id', type=int)
    brand = (request.form.get('brand', '') or '').strip()
    article_number = (request.form.get('article_number', '') or '').strip()
    supplier = (request.form.get('supplier', '') or '').strip()

    part = SparePart.query.get(spare_part_id) if spare_part_id else None
    errors = []
    if part is None or part.status != 'active' or part.is_active == False:  # noqa: E712
        errors.append(_spare_t('Каталогдан фаол эҳтиёт қисмни танланг',
                               'Выберите активную запчасть из каталога'))
    # A SKU with no brand, no article and no supplier identifies nothing.
    if not (brand or article_number or supplier):
        errors.append(_spare_t(
            'Камида битта майдонни тўлдиринг: бренд, артикул ёки таъминотчи',
            'Заполните хотя бы одно поле: бренд, артикул или поставщик'))
    if errors:
        _spare_flash_errors(errors, title_uz='SKU сақланмади:',
                            title_ru='SKU не сохранён:')
        return redirect(url_for('spare_parts.skus'))

    created = False
    before = None
    if sku_id:
        sku = SparePartSku.query.get_or_404(sku_id)
        before = _sku_snapshot(sku)
        sku.spare_part_id = part.id
        sku.brand = brand
        sku.article_number = article_number
        sku.supplier = supplier
        sku.is_active = request.form.get('is_active') is not None
    else:
        sku = SparePartSku(spare_part_id=part.id, brand=brand,
                           article_number=article_number, supplier=supplier,
                           created_by=current_user.id)
        db.session.add(sku)
        created = True
    db.session.flush()
    after = _sku_snapshot(sku)
    _audit_spare(
        'spare_part_sku_created' if created else 'spare_part_sku_updated',
        entity_type='spare_part_sku',
        entity_id=sku.id,
        entity_label='{} — {}'.format(part.name, sku.label),
        before=before,
        after=after,
        changes=diff_dict(before, after),
        description='Spare part SKU saved'
    )
    db.session.commit()
    flash(_spare_t('SKU сақланди', 'SKU сохранён'), 'success')
    return redirect(url_for('spare_parts.skus'))


# ─── SPARE-STAGE2: warehouses + inventory + movements ─────────────────────────

def _apply_inventory_movement(warehouse_id, sku_id, movement_type, quantity,
                              reference_type='manual', reference_id=None,
                              note='', user_id=None):
    """Apply one signed stock change to a (warehouse, SKU) pair WITH its audit row.

    [REASON]: SPARE-STAGE2 core invariant — spare_part_inventory.quantity is
    never written anywhere except through this function, which records the
    matching movement row (with a balance_after snapshot) in the same
    transaction. A reviewer can sum a pair's movement quantities and always
    get the pair's current quantity. Does not flush/commit beyond the flush
    needed for a lazily created inventory row — the caller owns the
    transaction, so a multi-item action (Task 5 issue) stays all-or-nothing.

    Sign rules per type: receipt > 0; issue/write_off < 0; adjustment any
    non-zero. The resulting balance MAY go negative: warehouses deliberately
    start at zero (opening balances are a future task), so blocking negative
    stock would block the whole issue workflow until real counts arrive.

    Raises ValueError with a short reason code on invalid input.
    """
    if movement_type not in INV_MOVEMENT_TYPES:
        raise ValueError('movement_type')
    try:
        quantity = float(quantity)
    except (TypeError, ValueError):
        raise ValueError('quantity')
    if quantity == 0:
        raise ValueError('zero_quantity')
    if movement_type == 'receipt' and quantity < 0:
        raise ValueError('receipt_negative')
    if movement_type in ('issue', 'write_off') and quantity > 0:
        raise ValueError('issue_positive')

    inv = SparePartInventory.query.filter_by(
        warehouse_id=warehouse_id, sku_id=sku_id).first()
    if inv is None:
        # [REASON]: inventory rows are created lazily on first movement — no
        # zero-quantity seeding of every SKU × warehouse combination.
        sku = SparePartSku.query.get(sku_id)
        unit = (sku.spare_part.unit if sku and sku.spare_part else '') or 'dona'
        inv = SparePartInventory(warehouse_id=warehouse_id, sku_id=sku_id,
                                 quantity=0, unit=unit)
        db.session.add(inv)
        db.session.flush()

    new_qty = round(float(inv.quantity or 0) + quantity, 3)
    inv.quantity = new_qty
    inv.updated_at = datetime.utcnow()
    movement = SparePartInventoryMovement(
        warehouse_id=warehouse_id,
        sku_id=sku_id,
        movement_type=movement_type,
        quantity=quantity,
        balance_after=new_qty,
        reference_type=reference_type,
        reference_id=reference_id,
        note=note or '',
        created_by=user_id,
    )
    db.session.add(movement)
    return movement


def _spare_orgs_for_user():
    """Organizations the current user may see, ordered like the rest of the module."""
    user_org_ids = _spare_user_org_ids()
    q = Organization.query.order_by(Organization.sort_order)
    if user_org_ids is not None:
        q = q.filter(Organization.id.in_(user_org_ids))
    return q.all()


@spare_parts_bp.route('/inventory')
@module_required('spare_parts')
def inventory():
    # [REASON]: SPARE-STAGE2 — new permission primitive for warehouse work,
    # same has_module_access()+admin-bypass pattern as the other five
    # spare-parts permissions. Seeded by migrate_spare_parts_stage2.py.
    if not current_user.has_module_access('spare_parts_inventory_manage'):
        abort(403)
    organizations = _spare_orgs_for_user()
    org_id = request.args.get('org_id', type=int)
    if org_id and not current_user.can_access_org(org_id):
        abort(403)
    if not org_id and organizations:
        org_id = organizations[0].id

    warehouse = (SparePartWarehouse.query.filter_by(organization_id=org_id).first()
                 if org_id else None)
    inventory_rows = []
    movements = []
    if warehouse:
        inventory_rows = (SparePartInventory.query
                          .options(joinedload(SparePartInventory.sku)
                                   .joinedload(SparePartSku.spare_part))
                          .filter_by(warehouse_id=warehouse.id)
                          .all())
        inventory_rows.sort(key=lambda r: ((r.sku.spare_part.name if r.sku and r.sku.spare_part else ''),
                                           (r.sku.label if r.sku else '')))
        movements = (SparePartInventoryMovement.query
                     .options(joinedload(SparePartInventoryMovement.sku)
                              .joinedload(SparePartSku.spare_part),
                              joinedload(SparePartInventoryMovement.creator))
                     .filter_by(warehouse_id=warehouse.id)
                     .order_by(SparePartInventoryMovement.id.desc())
                     .limit(30)
                     .all())
    # SKU picker for the manual movement form: all active SKUs, labelled with
    # their canonical part name.
    sku_options = (SparePartSku.query
                   .options(joinedload(SparePartSku.spare_part))
                   .filter(db.or_(SparePartSku.is_active == True,  # noqa: E712
                                  SparePartSku.is_active.is_(None)))
                   .all())
    sku_options.sort(key=lambda s: ((s.spare_part.name if s.spare_part else ''), s.label))
    return render_template('spare_parts_inventory.html',
                           organizations=organizations,
                           org_id=org_id,
                           warehouse=warehouse,
                           inventory_rows=inventory_rows,
                           movements=movements,
                           sku_options=sku_options,
                           movement_labels=INV_MOVEMENT_LABELS,
                           lang=_spare_lang())


@spare_parts_bp.route('/inventory/warehouse', methods=['POST'])
@module_required('spare_parts')
def inventory_warehouse_create():
    if not current_user.has_module_access('spare_parts_inventory_manage'):
        abort(403)
    org_id = request.form.get('organization_id', type=int)
    name = (request.form.get('name', '') or '').strip()
    if not org_id or not current_user.can_access_org(org_id):
        abort(403)
    org = Organization.query.get_or_404(org_id)
    if not name:
        name = org.name
    # [REASON]: the UNIQUE(organization_id) constraint is the real guard; this
    # pre-check only exists to show a friendly message instead of a 500.
    if SparePartWarehouse.query.filter_by(organization_id=org_id).first():
        _spare_flash_errors(
            [_spare_t('Бу ташкилот учун омбор аллақачон мавжуд',
                      'Склад для этой организации уже существует')],
            title_uz='Омбор яратилмади:', title_ru='Склад не создан:')
        return redirect(url_for('spare_parts.inventory', org_id=org_id))
    warehouse = SparePartWarehouse(organization_id=org_id, name=name[:200])
    db.session.add(warehouse)
    db.session.flush()
    _audit_spare(
        'spare_part_warehouse_created',
        entity_type='spare_part_warehouse',
        entity_id=warehouse.id,
        entity_label=warehouse.name,
        after={'id': warehouse.id, 'organization_id': org_id, 'name': warehouse.name},
        description='Spare part warehouse created'
    )
    db.session.commit()
    flash(_spare_t('Омбор яратилди', 'Склад создан'), 'success')
    return redirect(url_for('spare_parts.inventory', org_id=org_id))


@spare_parts_bp.route('/inventory/movement', methods=['POST'])
@module_required('spare_parts')
def inventory_movement_create():
    """Manual receipt / adjustment entry."""
    if not current_user.has_module_access('spare_parts_inventory_manage'):
        abort(403)
    warehouse_id = request.form.get('warehouse_id', type=int)
    warehouse = SparePartWarehouse.query.get_or_404(warehouse_id)
    if not current_user.can_access_org(warehouse.organization_id):
        abort(403)
    movement_type = (request.form.get('movement_type', '') or '').strip()
    # Manual entry allows receipts and adjustments only; issues and write-offs
    # come from the request issue workflow (Task 5), never typed by hand.
    if movement_type not in ('receipt', 'adjustment'):
        abort(400)
    sku_id = request.form.get('sku_id', type=int)
    sku = SparePartSku.query.get(sku_id) if sku_id else None
    note = (request.form.get('note', '') or '').strip()
    raw_qty = (request.form.get('quantity', '') or '').strip().replace(',', '.')

    errors = []
    if sku is None or sku.is_active == False:  # noqa: E712
        errors.append(_spare_t('SKU танланг', 'Выберите SKU'))
    quantity = None
    try:
        quantity = float(raw_qty)
    except (TypeError, ValueError):
        errors.append(_spare_t('Миқдор сон бўлиши керак', 'Количество должно быть числом'))
    if quantity is not None:
        if quantity == 0:
            errors.append(_spare_t('Миқдор нолдан фарқли бўлиши керак',
                                   'Количество не может быть нулём'))
        elif movement_type == 'receipt' and quantity < 0:
            errors.append(_spare_t('Кирим миқдори мусбат бўлиши керак',
                                   'Количество прихода должно быть положительным'))
    if errors:
        _spare_flash_errors(errors, title_uz='Ҳаракат сақланмади:',
                            title_ru='Движение не сохранено:')
        return redirect(url_for('spare_parts.inventory',
                                org_id=warehouse.organization_id))

    movement = _apply_inventory_movement(
        warehouse.id, sku.id, movement_type, quantity,
        reference_type='manual', note=note, user_id=current_user.id)
    _audit_spare(
        'spare_part_inventory_movement_created',
        entity_type='spare_part_inventory_movement',
        entity_id=None,
        entity_label='{} {} {}'.format(movement_type, quantity, sku.label),
        after={'warehouse_id': warehouse.id, 'sku_id': sku.id,
               'movement_type': movement_type, 'quantity': quantity,
               'balance_after': movement.balance_after},
        description='Manual inventory movement'
    )
    db.session.commit()
    flash(_spare_t('Ҳаракат сақланди. Янги қолдиқ: {}',
                   'Движение сохранено. Новый остаток: {}').format(movement.balance_after),
          'success')
    return redirect(url_for('spare_parts.inventory',
                            org_id=warehouse.organization_id))


# ─── SPARE-STAGE2: issue + write-off acts ─────────────────────────────────────

def _acts_dir():
    """Absolute path of the write-off act PDF folder (created on demand).

    Sibling of the photo UPLOAD_FOLDER, same resolution rule: a plain path
    relative to the app folder so non-Flask processes can find the files.
    """
    folder = os.path.join('instance', 'uploads', 'spare_parts_acts')
    root = os.path.dirname(os.path.abspath(__file__))
    folder = os.path.join(root, folder)
    os.makedirs(folder, exist_ok=True)
    return folder


def _generate_act_number(year):
    """Next SPW-{year}-{00001} act number.

    [REASON]: SPARE-STAGE2 — exact reuse of the MAX+1-inside-a-transaction
    technique proven by work_orders._generate_wo_number: act_number is UNIQUE,
    so a rare concurrent race raises IntegrityError on commit instead of
    silently duplicating a number; acceptable for this low-concurrency app.
    """
    prefix = 'SPW-{}-'.format(year)
    last = db.session.query(func.max(SparePartWriteOffAct.act_number)).filter(
        SparePartWriteOffAct.act_number.like(prefix + '%')
    ).scalar()
    seq = 1
    if last:
        try:
            seq = int(last.split('-')[-1]) + 1
        except (ValueError, IndexError):
            seq = 1
    return '{}{:05d}'.format(prefix, seq)


def _issue_context(spr):
    """Data the detail page needs to offer the issue action.

    Returns dict with: sku_items / no_sku_items, the organization's warehouse
    (or None), and current stock per SKU item so the issuer sees what will be
    deducted (and what would go negative) BEFORE confirming.
    """
    sku_items = [i for i in spr.items if i.sku_id]
    no_sku_items = [i for i in spr.items if not i.sku_id]
    warehouse = SparePartWarehouse.query.filter_by(
        organization_id=spr.organization_id).first()
    stock = {}
    if warehouse and sku_items:
        rows = (SparePartInventory.query
                .filter(SparePartInventory.warehouse_id == warehouse.id,
                        SparePartInventory.sku_id.in_([i.sku_id for i in sku_items]))
                .all())
        stock = {r.sku_id: r.quantity for r in rows}
    return {
        'sku_items': sku_items,
        'no_sku_items': no_sku_items,
        'warehouse': warehouse,
        'stock': stock,
    }


@spare_parts_bp.route('/<int:rid>/issue', methods=['POST'])
@module_required('spare_parts')
def issue_request(rid):
    """approved -> issued: deduct SKU stock, create the write-off act + PDF.

    All-or-nothing: every deduction, the act row, its item snapshots and the
    PDF render happen in ONE transaction — any failure rolls the whole issue
    back and the request stays 'approved'.
    """
    # [REASON]: SPARE-STAGE2 — separate permission from spare_parts_approve,
    # matching the real-world split between "approve the purchase" and
    # "physically hand it over from the warehouse".
    if not current_user.has_module_access('spare_parts_issue'):
        abort(403)
    spr = SparePartRequest.query.get_or_404(rid)
    _spare_check_request_access(spr)
    # [REASON]: 'issued' is reachable ONLY from 'approved' — never from any
    # other status, and never twice (no un-issue; acts are permanent).
    if spr.status != 'approved':
        abort(400)

    ctx = _issue_context(spr)
    errors = []
    if ctx['sku_items'] and ctx['warehouse'] is None:
        errors.append(_spare_t(
            'Ташкилотда эҳтиёт қисмлар омбори йўқ. Аввал «Омбор» экранида омбор яратинг.',
            'У организации нет склада запчастей. Сначала создайте склад на экране «Склад».'))
    # [REASON]: SPARE-STAGE2 — untracked (no-SKU) items must be issued
    # CONSCIOUSLY: the form shows which items skip stock deduction and the
    # server independently requires the explicit confirmation flag, so the
    # rule holds even if the client-side UI is bypassed.
    if ctx['no_sku_items'] and request.form.get('confirm_no_sku') != '1':
        errors.append(_spare_t(
            'SKUсиз позициялар омбордан ҳисобдан чиқарилмайди — беришдан олдин буни тасдиқлаш шарт',
            'Позиции без SKU не будут списаны со склада — перед выдачей это нужно явно подтвердить'))
    if errors:
        _spare_flash_errors(errors, title_uz='Бериб бўлмади:',
                            title_ru='Выдача не выполнена:')
        return redirect(url_for('spare_parts.detail', rid=rid))

    warehouse = ctx['warehouse']
    act_pdf_full_path = None
    try:
        # 1. Inventory movements for every SKU item (negative issue quantities).
        for item in ctx['sku_items']:
            _apply_inventory_movement(
                warehouse.id, item.sku_id, 'issue', -float(item.quantity or 0),
                reference_type='request_item', reference_id=item.id,
                note='Request #{}'.format(spr.id), user_id=current_user.id)

        # 2. The act with MAX+1 numbering and snapshotted items.
        issued_date = date.today()
        act = SparePartWriteOffAct(
            act_number=_generate_act_number(issued_date.year),
            request_id=spr.id,
            organization_id=spr.organization_id,
            warehouse_id=warehouse.id if warehouse else None,
            issued_date=issued_date,
            issued_by=current_user.id,
        )
        db.session.add(act)
        db.session.flush()
        for item in spr.items:
            total = None
            if item.price is not None:
                total = round(float(item.price) * float(item.quantity or 0), 2)
            db.session.add(SparePartWriteOffActItem(
                act_id=act.id,
                request_item_id=item.id,
                name=item.name,
                sku_label=(item.sku.label if item.sku else ''),
                quantity=item.quantity,
                unit=item.unit or 'dona',
                price=item.price,
                total=total,
            ))
        db.session.flush()

        # 3. Status transition + history + audit.
        old_status = spr.status
        spr.status = 'issued'
        _add_status_history(spr.id, old_status, 'issued',
                            comment='Act {}'.format(act.act_number),
                            changed_by=current_user.id)
        _audit_spare(
            'spare_part_request_status_changed',
            entity_type='spare_part_request',
            entity_id=spr.id,
            entity_label='Request #{}'.format(spr.id),
            changes={'status': {'before': old_status, 'after': 'issued'},
                     'act_number': {'before': None, 'after': act.act_number}},
            description='Request issued; write-off act {} created'.format(act.act_number)
        )

        # 4. Render the PDF inside the same transaction: a request must never
        # end up 'issued' without its printable act.
        from spare_parts_pdf import generate_write_off_act_pdf
        fname = '{}_{}.pdf'.format(act.id, act.act_number)
        act_pdf_full_path = os.path.join(_acts_dir(), fname)
        generate_write_off_act_pdf(act, act_pdf_full_path, lang=_spare_lang())
        act.pdf_path = fname

        db.session.commit()
    except Exception:
        db.session.rollback()
        # Remove the orphan file if the PDF was written but the commit failed.
        if act_pdf_full_path and os.path.exists(act_pdf_full_path):
            try:
                os.remove(act_pdf_full_path)
            except OSError:
                pass
        current_app.logger.exception('Issue action failed for request %s', rid)
        _spare_flash_errors(
            [_spare_t('Ички хатолик — ҳеч нарса ўзгартирилмади',
                      'Внутренняя ошибка — ничего не изменено')],
            title_uz='Бериб бўлмади:', title_ru='Выдача не выполнена:')
        return redirect(url_for('spare_parts.detail', rid=rid))

    flash(_spare_t('Сўров берилди. Далолатнома: {}',
                   'Заявка выдана. Акт: {}').format(act.act_number), 'success')
    # [REASON]: deliberately NO bot003 notification here — the bot's event
    # allowlist (out of this task's scope fence) has no issued event type, so
    # a call would only produce "Unknown event_type" log noise on every issue.
    return redirect(url_for('spare_parts.act_detail', act_id=act.id))


@spare_parts_bp.route('/acts/<int:act_id>')
@module_required('spare_parts')
def act_detail(act_id):
    act = SparePartWriteOffAct.query.get_or_404(act_id)
    # Same org-scoped access rule as the request it belongs to.
    spr = act.request
    if spr is None:
        abort(404)
    _spare_check_request_access(spr)
    return render_template('spare_part_act.html',
                           act=act,
                           req=spr,
                           lang=_spare_lang())


@spare_parts_bp.route('/acts/<int:act_id>/pdf')
@module_required('spare_parts')
def act_pdf(act_id):
    act = SparePartWriteOffAct.query.get_or_404(act_id)
    spr = act.request
    if spr is None:
        abort(404)
    # [REASON]: act PDFs are NOT served as raw static files; the same
    # org-scoped access check as the act/request detail applies to downloads.
    _spare_check_request_access(spr)
    fname = os.path.basename(act.pdf_path or '')
    if not fname:
        abort(404)
    return send_from_directory(_acts_dir(), fname, mimetype='application/pdf',
                               download_name='{}.pdf'.format(act.act_number))


# ─── Catalog ──────────────────────────────────────────────────────────────────

@spare_parts_bp.route('/catalog')
@module_required('spare_parts')
def catalog():
    # [REASON]: SPARE-STAGE1 — catalog management now sits behind the explicit
    # 'spare_parts_catalog_manage' permission (admin passes automatically, so
    # existing admin access is preserved; previously this was is_admin-only).
    if not current_user.has_module_access('spare_parts_catalog_manage'):
        abort(403)
    parts = (SparePart.query
             .options(joinedload(SparePart.category_ref))
             .order_by(SparePart.name).all())
    pending_parts = (SparePart.query
                     .options(joinedload(SparePart.creator),
                              joinedload(SparePart.source_item))
                     .filter(SparePart.status == 'pending_review')
                     .order_by(SparePart.created_at)
                     .all())
    merge_targets = [p for p in parts
                     if p.status == 'active' and p.is_active != False]  # noqa: E712
    categories = (SparePartCategory.query
                  .order_by(SparePartCategory.sort_order, SparePartCategory.id)
                  .all())
    return render_template('spare_parts_catalog.html',
                           parts=parts,
                           pending_parts=pending_parts,
                           merge_targets=merge_targets,
                           categories=categories,
                           lang=_spare_lang())


@spare_parts_bp.route('/catalog/save', methods=['POST'])
@module_required('spare_parts')
def catalog_save():
    if not current_user.has_module_access('spare_parts_catalog_manage'):
        abort(403)
    pid = request.form.get('id', type=int)
    name = request.form.get('name', '').strip()
    part_number = request.form.get('part_number', '').strip()
    unit = request.form.get('unit', 'dona').strip()
    # [REASON]: SPARE-STAGE1 — the form now sends category_id (managed
    # categories); the deprecated free-text `category` column is left untouched.
    category_id = request.form.get('category_id', type=int) or None
    if category_id and not SparePartCategory.query.get(category_id):
        category_id = None

    if not name:
        flash(_spare_t('Номини киритинг', 'Введите название'), 'warning')
        return redirect(url_for('spare_parts.catalog'))

    created = False
    before = None
    if pid:
        part = SparePart.query.get_or_404(pid)
        before = _catalog_snapshot(part)
        part.name = name
        part.part_number = part_number
        part.unit = unit
        part.category_id = category_id
    else:
        part = SparePart(name=name, part_number=part_number, unit=unit,
                         category_id=category_id, created_by=current_user.id)
        db.session.add(part)
        created = True
    db.session.flush()
    after = _catalog_snapshot(part)
    _audit_spare(
        'spare_part_catalog_created' if created else 'spare_part_catalog_updated',
        entity_type='spare_part_catalog',
        entity_id=part.id,
        entity_label=part.name,
        before=before,
        after=after,
        changes=diff_dict(before, after),
        description='Spare part catalog saved'
    )
    db.session.commit()
    flash(_spare_t('Сақланди', 'Сохранено'), 'success')
    return redirect(url_for('spare_parts.catalog'))


@spare_parts_bp.route('/catalog/categories/save', methods=['POST'])
@module_required('spare_parts')
def catalog_category_save():
    if not current_user.has_module_access('spare_parts_catalog_manage'):
        abort(403)
    cid = request.form.get('id', type=int)
    name_ru = (request.form.get('name_ru', '') or '').strip()
    name_uz = (request.form.get('name_uz', '') or '').strip()
    kind = (request.form.get('kind', 'unit') or 'unit').strip()
    sort_order = request.form.get('sort_order', type=int) or 0
    if kind not in ('unit', 'consumable'):
        abort(400)
    if not name_ru or not name_uz:
        _spare_flash_errors(
            [_spare_t('Категория номини иккала тилда киритинг',
                      'Введите название категории на обоих языках')],
            title_uz='Категория сақланмади:', title_ru='Категория не сохранена:')
        return redirect(url_for('spare_parts.catalog'))

    created = False
    if cid:
        cat = SparePartCategory.query.get_or_404(cid)
        cat.name_ru = name_ru
        cat.name_uz = name_uz
        cat.kind = kind
        cat.sort_order = sort_order
        cat.is_active = request.form.get('is_active') is not None
    else:
        cat = SparePartCategory(name_ru=name_ru, name_uz=name_uz, kind=kind,
                                sort_order=sort_order, created_by=current_user.id)
        db.session.add(cat)
        created = True
    db.session.flush()
    _audit_spare(
        'spare_part_category_created' if created else 'spare_part_category_updated',
        entity_type='spare_part_category',
        entity_id=cat.id,
        entity_label=cat.name_ru,
        description='Spare part category saved (kind={})'.format(cat.kind)
    )
    db.session.commit()
    flash(_spare_t('Категория сақланди', 'Категория сохранена'), 'success')
    return redirect(url_for('spare_parts.catalog'))


@spare_parts_bp.route('/catalog/review/<int:pid>/approve', methods=['POST'])
@module_required('spare_parts')
def catalog_review_approve(pid):
    """Approve a pending_review candidate as a new canonical catalog entry."""
    if not current_user.has_module_access('spare_parts_catalog_manage'):
        abort(403)
    part = SparePart.query.get_or_404(pid)
    if part.status != 'pending_review':
        abort(400)
    category_id = request.form.get('category_id', type=int)
    category = SparePartCategory.query.get(category_id) if category_id else None
    # [REASON]: SPARE-STAGE1 — approving as new requires a category so the
    # photo rule (unit/consumable) and analytics have a defined bucket.
    if not category:
        _spare_flash_errors(
            [_spare_t('Категорияни танланг', 'Выберите категорию')],
            title_uz='Ёзув тасдиқланмади:', title_ru='Запись не подтверждена:')
        return redirect(url_for('spare_parts.catalog'))
    before = _catalog_snapshot(part)
    part.category_id = category.id
    part.status = 'active'
    after = _catalog_snapshot(part)
    _audit_spare(
        'spare_part_catalog_review_approved',
        entity_type='spare_part_catalog',
        entity_id=part.id,
        entity_label=part.name,
        before=before,
        after=after,
        changes=diff_dict(before, after),
        description='Pending-review candidate approved as new catalog entry'
    )
    db.session.commit()
    flash(_spare_t('Каталог ёзуви тасдиқланди', 'Запись каталога подтверждена'), 'success')
    return redirect(url_for('spare_parts.catalog'))


@spare_parts_bp.route('/catalog/review/<int:pid>/merge', methods=['POST'])
@module_required('spare_parts')
def catalog_review_merge(pid):
    """Merge a pending_review candidate into an existing canonical entry."""
    if not current_user.has_module_access('spare_parts_catalog_manage'):
        abort(403)
    part = SparePart.query.get_or_404(pid)
    if part.status != 'pending_review':
        abort(400)
    target_id = request.form.get('target_id', type=int)
    target = SparePart.query.get(target_id) if target_id else None
    if not target or target.id == part.id or target.status != 'active':
        _spare_flash_errors(
            [_spare_t('Бирлаштириш учун фаол каталог ёзувини танланг',
                      'Выберите активную запись каталога для объединения')],
            title_uz='Бирлаштирилмади:', title_ru='Объединение не выполнено:')
        return redirect(url_for('spare_parts.catalog'))
    before = _catalog_snapshot(part)
    # [REASON]: SPARE-STAGE1 — repoint every request item that referenced the
    # duplicate candidate to the canonical entry so history and the repeat
    # engine count them against one part. Bulk update runs before the part is
    # modified so no in-session state needs re-synchronisation.
    repointed = (SparePartRequestItem.query
                 .filter(SparePartRequestItem.spare_part_id == part.id)
                 .update({'spare_part_id': target.id}, synchronize_session=False))
    part.status = 'merged'
    part.merged_into_id = target.id
    after = _catalog_snapshot(part)
    _audit_spare(
        'spare_part_catalog_review_merged',
        entity_type='spare_part_catalog',
        entity_id=part.id,
        entity_label=part.name,
        before=before,
        after=after,
        changes=diff_dict(before, after),
        description='Candidate merged into catalog entry #{} ({} items repointed)'.format(
            target.id, repointed)
    )
    db.session.commit()
    flash(_spare_t('Ёзув бирлаштирилди', 'Записи объединены'), 'success')
    return redirect(url_for('spare_parts.catalog'))


# ─── SPARE-REPORTS: costs & repeat-warning analytics ──────────────────────────

def _reports_parse_date(value, default):
    """Lenient date parser for report query-string filters.

    [REASON]: SPARE-REPORTS — mirrors fuel_routes._parse_report_date: a bad
    query-string value falls back to the default period instead of failing
    the whole page (unlike _parse_spare_date, which raises for form input).
    """
    try:
        return datetime.strptime(value or '', '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return default


def _reports_filters():
    """Read and validate the filter set shared by reports and reports_export."""
    today = date.today()
    default_start = today.replace(day=1)
    d_from = _reports_parse_date(request.args.get('date_from'), default_start)
    d_to = _reports_parse_date(request.args.get('date_to'), today)
    if d_from > d_to:
        d_from, d_to = d_to, d_from

    org_id = request.args.get('org_id', type=int) or None
    if org_id and not current_user.can_access_org(org_id):
        abort(403)

    equipment_id = request.args.get('equipment_id', type=int) or None
    if equipment_id:
        eq = Equipment.query.get(equipment_id)
        if eq is None:
            equipment_id = None
        elif not current_user.can_access_org(eq.organization_id):
            abort(403)
        elif org_id and eq.organization_id != org_id:
            # Stale equipment kept from a previously selected organization —
            # the organization filter wins, the equipment filter is dropped.
            equipment_id = None

    category_id = request.args.get('category_id', type=int) or None
    if category_id and not SparePartCategory.query.get(category_id):
        category_id = None

    return d_from, d_to, org_id, equipment_id, category_id


def _reports_data(d_from, d_to, org_id=None, equipment_id=None, category_id=None):
    """Collect the five spare parts report tables for one filter set.

    [REASON]: SPARE-REPORTS business rule — cost tables count only money
    actually authorized: SparePartRequest.status == 'approved' AND
    SparePartRequestItem.price_status == 'confirmed'. Draft/pending amounts
    never enter cost totals. Line cost = price * quantity.
    """
    user_org_ids = _spare_user_org_ids()

    q = (db.session.query(SparePartRequestItem, SparePartRequest, SparePart)
         .join(SparePartRequest,
               SparePartRequestItem.request_id == SparePartRequest.id)
         .outerjoin(SparePart,
                    SparePartRequestItem.spare_part_id == SparePart.id)
         .filter(SparePartRequest.request_date >= d_from,
                 SparePartRequest.request_date <= d_to))
    if user_org_ids is not None:
        q = q.filter(SparePartRequest.organization_id.in_(user_org_ids))
    if org_id:
        q = q.filter(SparePartRequest.organization_id == org_id)
    if equipment_id:
        q = q.filter(SparePartRequest.equipment_id == equipment_id)
    if category_id:
        # NULL-category items (free-text/no catalog link) drop out here by
        # SQL NULL semantics, which is correct for an explicit category filter.
        q = q.filter(SparePart.category_id == category_id)
    lines = q.order_by(SparePartRequest.request_date,
                       SparePartRequest.id,
                       SparePartRequestItem.id).all()

    org_map = {o.id: o for o in Organization.query.all()}
    cat_map = {c.id: c for c in SparePartCategory.query.all()}
    eq_ids = {req.equipment_id for _item, req, _part in lines if req.equipment_id}
    eq_map = ({e.id: e for e in Equipment.query.filter(Equipment.id.in_(eq_ids)).all()}
              if eq_ids else {})

    def _cost(item):
        return float(item.price or 0) * float(item.quantity or 0)

    cost_lines = [(item, req, part) for item, req, part in lines
                  if req.status == 'approved' and item.price_status == 'confirmed']

    # Table 1: costs by equipment. Grouped by (organization_id, equipment_id)
    # so the NULL-equipment bucket stays split per organization and every row
    # can show its organization; real equipment belongs to exactly one
    # organization, so this equals grouping by equipment_id for it.
    eq_totals = {}
    for item, req, _part in cost_lines:
        key = (req.organization_id, req.equipment_id)
        row = eq_totals.setdefault(key, {
            'organization': org_map.get(req.organization_id),
            'equipment': eq_map.get(req.equipment_id),
            'total': 0.0, 'lines': 0,
        })
        row['total'] += _cost(item)
        row['lines'] += 1
    by_equipment = sorted(eq_totals.values(),
                          key=lambda r: r['total'], reverse=True)

    # Table 2: costs by organization.
    org_totals = {}
    for item, req, _part in cost_lines:
        row = org_totals.setdefault(req.organization_id, {
            'organization': org_map.get(req.organization_id),
            'total': 0.0, 'lines': 0,
        })
        row['total'] += _cost(item)
        row['lines'] += 1
    by_organization = sorted(org_totals.values(),
                             key=lambda r: r['total'], reverse=True)

    # Table 3: costs by catalog category. Items without a category (no catalog
    # link, or a part not yet categorized) get their own None bucket — they are
    # shown as «без категории» / «категориясиз», never silently dropped.
    cat_totals = {}
    for item, _req, part in cost_lines:
        cat_id = part.category_id if part is not None else None
        row = cat_totals.setdefault(cat_id, {
            'category': cat_map.get(cat_id),
            'total': 0.0, 'lines': 0,
        })
        row['total'] += _cost(item)
        row['lines'] += 1
    by_category = sorted(cat_totals.values(),
                         key=lambda r: r['total'], reverse=True)

    grand_total = sum(_cost(item) for item, _req, _part in cost_lines)

    # Table 4: repeat-order warnings triggered in the period.
    # [REASON]: SPARE-REPORTS — verbatim reuse of the SPARE-STAGE1 engine
    # (_check_repeat_orders), not a reimplementation: severity/days_since are
    # the engine's live view (anchored to today, 90-day window, red ≤7 /
    # yellow ≤30, rejected prior requests excluded), i.e. exactly what the
    # request detail page shows for the same item today.
    repeat_rows = []
    for item, req, _part in lines:
        if not req.equipment_id or not item.spare_part_id:
            continue
        res = _check_repeat_orders(req.equipment_id, item.spare_part_id,
                                   exclude_request_id=req.id)
        if res['severity'] in ('red', 'yellow'):
            repeat_rows.append({
                'request_id': req.id,
                'request_date': req.request_date,
                'equipment': eq_map.get(req.equipment_id),
                'organization': org_map.get(req.organization_id),
                'part_name': item.name,
                'severity': res['severity'],
                'days_since': res['days_since'],
            })
    repeat_rows.sort(key=lambda r: (0 if r['severity'] == 'red' else 1,
                                    r['days_since'] if r['days_since'] is not None else 999))

    # Table 5: top-20 most expensive confirmed approved line items.
    top_lines = sorted(cost_lines, key=lambda t: _cost(t[0]), reverse=True)[:20]
    top_items = [{
        'request_id': req.id,
        'request_date': req.request_date,
        'organization': org_map.get(req.organization_id),
        'equipment': eq_map.get(req.equipment_id),
        'name': item.name,
        'quantity': item.quantity,
        'unit': item.unit or '',
        'price': item.price,
        'total': _cost(item),
    } for item, req, _part in top_lines]

    return {
        'd_from': d_from,
        'd_to': d_to,
        'by_equipment': by_equipment,
        'by_organization': by_organization,
        'by_category': by_category,
        'grand_total': grand_total,
        'cost_lines_count': len(cost_lines),
        'repeat_rows': repeat_rows,
        'top_items': top_items,
    }


def _spare_reports_workbook(data, lang='uz'):
    """Build the 5-sheet Excel workbook for the spare parts reports.

    Follows the styling conventions of fuel_routes._fuel_report_workbook
    (bold filled header row, frozen header, borders, auto column widths).
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    def L(ru, uz):
        return ru if lang == 'ru' else uz

    # [REASON]: SPARE-REPORTS — costs are сум amounts, so the money format is
    # excel_export.py's NUM_FMT ('#,##0', no decimals — same style as fmt_sum
    # in templates), not the fuel report's '#,##0.00' liters format.
    money_fmt = '#,##0'

    wb = Workbook()
    wb.remove(wb.active)
    header_fill = PatternFill('solid', fgColor='D9EAD3')
    header_font = Font(bold=True)
    total_font = Font(bold=True)
    thin = Side(style='thin', color='D9D9D9')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    def style_table(ws, money_cols=()):
        ws.freeze_panes = 'A2'
        ws.sheet_view.showGridLines = False
        max_col = ws.max_column
        max_row = ws.max_row
        for cell in ws[1]:
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal='center', vertical='center',
                                       wrap_text=True)
        for row in ws.iter_rows(min_row=1, max_row=max_row, max_col=max_col):
            for cell in row:
                cell.border = border
                if cell.row > 1 and cell.column in money_cols \
                        and isinstance(cell.value, (int, float)):
                    cell.number_format = money_fmt
        for col in range(1, max_col + 1):
            letter = get_column_letter(col)
            width = 10
            for cell in ws[letter]:
                val = '' if cell.value is None else str(cell.value)
                width = max(width, min(len(val) + 2, 38))
            ws.column_dimensions[letter].width = width

    def eq_name(eq):
        return eq.name if eq else L('— без техники —', '— техникасиз —')

    def eq_plate(eq):
        return (eq.plate or '') if eq else ''

    def org_name(org):
        return (org.short_name or org.name) if org else '—'

    severity_labels = {
        'red': L('Красный (≤7 дней)', 'Қизил (≤7 кун)'),
        'yellow': L('Жёлтый (≤30 дней)', 'Сариқ (≤30 кун)'),
    }

    # Sheet 1: costs by equipment.
    ws = wb.create_sheet(L('Затраты по технике', 'Техника бўйича харажатлар'))
    ws.append([L('Техника', 'Техника'), L('Гос. номер', 'Давлат рақами'),
               L('Организация', 'Ташкилот'), L('Позиций', 'Позициялар'),
               L('Сумма, сум', 'Сумма, сўм')])
    for r in data['by_equipment']:
        ws.append([eq_name(r['equipment']), eq_plate(r['equipment']),
                   org_name(r['organization']), r['lines'], r['total']])
    ws.append([L('ИТОГО', 'ЖАМИ'), '', '', data['cost_lines_count'],
               data['grand_total']])
    for cell in ws[ws.max_row]:
        cell.font = total_font
    style_table(ws, money_cols=(5,))

    # Sheet 2: costs by organization.
    ws = wb.create_sheet(L('Затраты по организациям', 'Ташкилотлар бўйича харажатлар'))
    ws.append([L('Организация', 'Ташкилот'), L('Позиций', 'Позициялар'),
               L('Сумма, сум', 'Сумма, сўм')])
    for r in data['by_organization']:
        ws.append([org_name(r['organization']), r['lines'], r['total']])
    ws.append([L('ИТОГО', 'ЖАМИ'), data['cost_lines_count'], data['grand_total']])
    for cell in ws[ws.max_row]:
        cell.font = total_font
    style_table(ws, money_cols=(3,))

    # Sheet 3: costs by catalog category.
    ws = wb.create_sheet(L('Затраты по категориям', 'Категориялар бўйича харажатлар'))
    ws.append([L('Категория', 'Категория'), L('Позиций', 'Позициялар'),
               L('Сумма, сум', 'Сумма, сўм')])
    for r in data['by_category']:
        cat = r['category']
        label = ((cat.name_ru if lang == 'ru' else cat.name_uz) if cat
                 else L('— без категории —', '— категориясиз —'))
        ws.append([label, r['lines'], r['total']])
    ws.append([L('ИТОГО', 'ЖАМИ'), data['cost_lines_count'], data['grand_total']])
    for cell in ws[ws.max_row]:
        cell.font = total_font
    style_table(ws, money_cols=(3,))

    # Sheet 4: repeat-order warnings.
    # [REASON]: MOBILE-UPLOAD-001/i18n — Excel worksheet names are capped at
    # 31 characters and cannot contain : \ / ? * [ ]. The on-screen card
    # title for this table also carries a "(red <=7d, yellow <=30d)"
    # parenthetical, which alone pushes the RU/UZ pair to 49/47 characters.
    # That detail is already shown per-row via the Уровень/Даража column
    # (severity_labels below), so the sheet name reuses only the base
    # RU/UZ title without the parenthetical -- still the exact label pair
    # used on screen, just without the redundant, over-length suffix.
    ws = wb.create_sheet(L('Повторные заказы', 'Такрорий сўровлар'))
    ws.append([L('Заявка', 'Сўров'), L('Дата', 'Сана'),
               L('Организация', 'Ташкилот'), L('Техника', 'Техника'),
               L('Запчасть', 'Эҳтиёт қисм'), L('Уровень', 'Даража'),
               L('Дней с прошлого заказа', 'Олдинги сўровдан кунлар')])
    for r in data['repeat_rows']:
        ws.append(['#{}'.format(r['request_id']),
                   r['request_date'].strftime('%d.%m.%Y'),
                   org_name(r['organization']), eq_name(r['equipment']),
                   r['part_name'],
                   severity_labels.get(r['severity'], r['severity']),
                   r['days_since']])
    style_table(ws)

    # Sheet 5: top-20 most expensive line items.
    ws = wb.create_sheet(L('Топ-20 самых дорогих позиций', 'Топ-20 энг қиммат позициялар'))
    ws.append([L('Заявка', 'Сўров'), L('Дата', 'Сана'),
               L('Организация', 'Ташкилот'), L('Техника', 'Техника'),
               L('Запчасть', 'Эҳтиёт қисм'), L('Кол-во', 'Миқдор'),
               L('Ед. изм.', 'Ўлчов бирлиги'), L('Цена, сум', 'Нарх, сўм'),
               L('Сумма, сум', 'Сумма, сўм')])
    for r in data['top_items']:
        ws.append(['#{}'.format(r['request_id']),
                   r['request_date'].strftime('%d.%m.%Y'),
                   org_name(r['organization']), eq_name(r['equipment']),
                   r['name'], r['quantity'], r['unit'], r['price'], r['total']])
    style_table(ws, money_cols=(8, 9))

    return wb


@spare_parts_bp.route('/reports')
@module_required('spare_parts')
def reports():
    # [REASON]: SPARE-REPORTS — the whole screen sits behind the explicit
    # spare_parts_reports permission, checked the same way as the three
    # SPARE-STAGE1 permissions (has_module_access keeps the admin bypass).
    if not current_user.has_module_access('spare_parts_reports'):
        abort(403)
    d_from, d_to, org_id, equipment_id, category_id = _reports_filters()
    data = _reports_data(d_from, d_to, org_id=org_id,
                         equipment_id=equipment_id, category_id=category_id)

    user_org_ids = _spare_user_org_ids()
    if user_org_ids is None:
        organizations = Organization.query.order_by(Organization.sort_order).all()
    else:
        organizations = (Organization.query
                         .filter(Organization.id.in_(user_org_ids))
                         .order_by(Organization.sort_order).all())
    categories = (SparePartCategory.query
                  .order_by(SparePartCategory.sort_order, SparePartCategory.id)
                  .all())
    # Initial options for the equipment filter; refreshed on organization
    # change via the same AJAX endpoint the request form uses
    # (api_equipment_by_org — active equipment of the selected organization).
    if org_id:
        equipment_options = (Equipment.query
                             .filter_by(organization_id=org_id, is_active=True)
                             .order_by(Equipment.name, Equipment.plate).all())
    else:
        equipment_options = []
    return render_template('spare_parts_reports.html',
                           data=data,
                           date_from_s=d_from.isoformat(),
                           date_to_s=d_to.isoformat(),
                           org_id=org_id,
                           equipment_id=equipment_id,
                           category_id=category_id,
                           organizations=organizations,
                           categories=categories,
                           equipment_options=equipment_options,
                           lang=_spare_lang())


@spare_parts_bp.route('/reports/export')
@module_required('spare_parts')
def reports_export():
    # Same gate as the reports screen (this is the same data, as a file).
    if not current_user.has_module_access('spare_parts_reports'):
        abort(403)
    d_from, d_to, org_id, equipment_id, category_id = _reports_filters()
    data = _reports_data(d_from, d_to, org_id=org_id,
                         equipment_id=equipment_id, category_id=category_id)
    wb = _spare_reports_workbook(data, lang=_spare_lang())
    # Local imports mirror fuel_routes.balance_report_export — keeps this
    # change additive (module-level flask import line untouched).
    from io import BytesIO
    from flask import send_file
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    prefix = ('Spare_parts_report' if _spare_lang() == 'ru'
              else 'Ehtiyot_qismlar_hisoboti')
    fname = '{}_{}_{}.xlsx'.format(prefix, d_from.strftime('%d_%m_%Y'),
                                   d_to.strftime('%d_%m_%Y'))
    return send_file(
        buffer,
        as_attachment=True,
        download_name=fname,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
