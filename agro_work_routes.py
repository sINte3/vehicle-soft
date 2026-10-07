# -*- coding: utf-8 -*-
"""agro_work_routes.py -- B3, экран сверки заявок agro-work с фактом GPS.

Карта маршрутов:
  GET /agro-work/                 свод: организация x категория, обе стороны
  GET /agro-work/applications     заявка -> работа: вердикт и причина
  GET /agro-work/work-days        работа -> заявка: машино-сутки и покрытие
  GET /agro-work/machine/<id>     сутки одной машины: GPS и заявки рядом
  GET /agro-work/import           прогоны импорта, связки машин, разметка

ТОЛЬКО ЧТЕНИЕ. Правила сверки -- в `agro_work/reconcile.py`, общем с отчётом
`tools/agro_work_reconcile.py`; экран их не повторяет, а показывает.

[REASON]: таблицы `agro_work_*` читаются отдельным соединением sqlite3 с
`mode=ro`, а не через ORM. У них один писатель -- stdlib-импорт; модели ORM
завели бы второго, и `db.create_all()` создавал бы их пустыми на базе, где
миграция AGRO_WORK_001 не применена, -- ровно тот дрейф, от которого устав
предостерегает. Так же читает экран манифеста дронов (drones.py).

[REASON]: маршруты закрыты @module_required('wialon'), а не новым кодом
модуля -- как экраны GPS: это факт GPS той же техники, право на него уже
роздано, а новый код потребовал бы миграции прав и решения владельца о том,
кому его выдавать. Организации -- тот же охват, что везде: не-администратор
видит только технику своих организаций, а заявки с машиной, не связанной с
нашей техникой, организации у нас не имеют и видны только администратору.
"""

import os
import sqlite3
from datetime import date, datetime, timedelta, timezone

from flask import Blueprint, abort, current_app, g, render_template, request
from flask_login import current_user, login_required

from agro_work import config as agro_config
from agro_work import labels, methods, reconcile as rc, store
from models import CATEGORIES, module_required

agro_work_bp = Blueprint('agro_work', __name__, url_prefix='/agro-work')

DEFAULT_PERIOD_DAYS = 14
MAX_PERIOD_DAYS = 92
LIST_LIMIT = 1000


def _aw_t(uz_text, ru_text):
    # Route-level bilingual helper -- the same pattern as _gps_t (gps_routes.py).
    return ru_text if getattr(g, 'lang', 'uz') == 'ru' else uz_text


def _is_ru():
    return getattr(g, 'lang', 'uz') == 'ru'


def _parse_date(value):
    try:
        return datetime.strptime((value or '').strip(), '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None


def _period():
    """(с, по): по умолчанию две недели по вчерашний день включительно.

    [REASON]: вчера, а не сегодня: факт GPS за сутки считается ночью после
    них, и сегодняшний день на экране был бы сплошным «нет данных».
    """
    date_to = _parse_date(request.args.get('to')) or (date.today()
                                                      - timedelta(days=1))
    date_from = _parse_date(request.args.get('from')) or (
        date_to - timedelta(days=DEFAULT_PERIOD_DAYS - 1))
    if date_from > date_to:
        date_from, date_to = date_to, date_from
    if (date_to - date_from).days >= MAX_PERIOD_DAYS:
        date_from = date_to - timedelta(days=MAX_PERIOD_DAYS - 1)
    return date_from, date_to


def _db_path():
    uri = current_app.config.get('SQLALCHEMY_DATABASE_URI', '') or ''
    prefix = 'sqlite:///'
    if not uri.startswith(prefix):
        return None
    return uri[len(prefix):]


def _open_ro():
    """Соединение только на чтение или None, если база не SQLite."""
    path = _db_path()
    if not path:
        return None
    return sqlite3.connect('file:%s?mode=ro' % os.path.abspath(path)
                           .replace('\\', '/'), uri=True, timeout=30)


def _scope():
    """None -- все организации (и несопоставленные машины); иначе список."""
    if current_user.is_admin:
        return None
    return current_user.get_org_ids() or [0]


def _context(date_from, date_to):
    """(Reconciliation или None, причина пустоты)."""
    con = _open_ro()
    if con is None:
        return None, 'not_sqlite'
    try:
        if store.missing_tables(con):
            return None, 'not_installed'
        return rc.Reconciliation(con, date_from, date_to,
                                 org_ids=_scope()), None
    finally:
        con.close()


def _last_run():
    con = _open_ro()
    if con is None:
        return None
    try:
        if store.missing_tables(con):
            return None
        con.row_factory = sqlite3.Row
        row = con.execute('SELECT * FROM agro_work_import_runs ORDER BY id '
                          'DESC LIMIT 1').fetchone()
        return dict(row) if row else None
    finally:
        con.close()


def _common(date_from, date_to, **extra):
    is_ru = _is_ru()
    values = {
        'date_from': date_from, 'date_to': date_to,
        'verdict_label': lambda key: labels.pick(labels.VERDICTS, key, is_ru),
        'coverage_label': lambda key: labels.pick(labels.COVERAGE, key, is_ru),
        'reason_label': lambda key: labels.pick(labels.REASONS, key, is_ru),
        'status_label': lambda key: labels.pick(labels.STATUSES, key, is_ru),
        'method_label': lambda key: labels.pick(methods.LABELS, key, is_ru)
        if key else '',
        'category_label': lambda key: CATEGORIES.get(key, key or ''),
        'lookback': rc.BACKDATED_LOOKBACK_DAYS,
        'last_run': _last_run(),
        'local_time': _local_time,
    }
    values.update(extra)
    return values


def _local_time(value):
    """UTC-метка журнала импорта -> местное время (UTC+5) для экрана."""
    try:
        moment = datetime.strptime(value, store.STAMP).replace(
            tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return value or ''
    return moment.astimezone(agro_config.TZ).strftime('%d.%m.%Y %H:%M')


def _machine_label(ctx, equipment_id):
    item = ctx.equipment.get(equipment_id)
    if item is None:
        return ''
    return ('%s %s' % (item['name'], item['plate'])).strip()


def _group_filter():
    org = request.args.get('org', type=int)
    category = (request.args.get('cat') or '').strip() or None
    unmatched = request.args.get('org') == 'none'
    return org, category, unmatched


def _keep(org, category, unmatched):
    """Параметры группы, которые форма периода должна сохранить."""
    return {'org': 'none' if unmatched else org, 'cat': category}


def _choice(name, title, keys, label, current):
    return {'name': name, 'title': title, 'current': current,
            'items': [(key, label(key)) for key in keys]}


def _in_group(ctx, equipment_id, org, category, unmatched):
    org_id, cat = ctx.group_of(equipment_id)
    if unmatched:
        return org_id is None
    if org is not None and org_id != org:
        return False
    if category is not None and cat != category:
        return False
    return True


# --- свод --------------------------------------------------------------------

@agro_work_bp.route('/')
@module_required('wialon')
@login_required
def dashboard():
    date_from, date_to = _period()
    ctx, empty = _context(date_from, date_to)
    if ctx is None:
        return render_template('agro_work/dashboard.html',
                               **_common(date_from, date_to, empty=empty))
    forward = ctx.forward_rows()
    reverse = ctx.reverse_rows()
    groups, total = ctx.summary(forward, reverse)
    rows = []
    for (org_id, category), counter in groups.items():
        rows.append({'org_id': org_id, 'category': category,
                     'org_name': ctx.orgs.get(org_id, '') if org_id else None,
                     'c': counter})
    order = list(CATEGORIES)
    rows.sort(key=lambda r: (r['org_id'] is None, r['org_name'] or '',
                             order.index(r['category'])
                             if r['category'] in order else len(order)))
    # B4, пункт 5: открытые заявки по организациям, самая старая -- первой.
    opened = {}
    for item in ctx.open_applications():
        org_id = ctx.group_of(item['equipment_id'])[0]
        group = opened.setdefault(org_id, {
            'org_id': org_id, 'count': 0, 'oldest': 0,
            'org_name': ctx.orgs.get(org_id, '') if org_id else None})
        group['count'] += 1
        group['oldest'] = max(group['oldest'], item['days_open'])
    open_groups = sorted(opened.values(),
                         key=lambda g: (-g['oldest'], g['org_name'] or ''))
    return render_template(
        'agro_work/dashboard.html',
        **_common(date_from, date_to, empty=None, rows=rows, total=total,
                  orphan_days=ctx.orphan_work_days(),
                  open_groups=open_groups, late_key=rc.DAY_LATE))


# --- заявка -> работа ----------------------------------------------------------

@agro_work_bp.route('/applications')
@module_required('wialon')
@login_required
def applications():
    date_from, date_to = _period()
    ctx, empty = _context(date_from, date_to)
    if ctx is None:
        return render_template('agro_work/applications.html',
                               **_common(date_from, date_to, empty=empty))
    org, category, unmatched = _group_filter()
    verdict = (request.args.get('verdict') or '').strip() or None
    reason = (request.args.get('reason') or '').strip() or None
    rows = []
    for row in ctx.forward_rows():
        if not _in_group(ctx, row['equipment_id'], org, category, unmatched):
            continue
        if verdict and row['verdict'] != verdict:
            continue
        if reason and row['reason'] != reason:
            continue
        row['machine'] = _machine_label(ctx, row['equipment_id'])
        row['work_type'] = ctx.work_type_names.get(row['app'].work_type_id) \
            or row['app'].row.get('work_type_name') or ''
        # B4, пункт 5: у открытой заявки -- сколько суток она открыта.
        row['days_open'] = ((ctx.today - row['app'].created_day).days
                            if row['reason'] == rc.R_OPEN else None)
        rows.append(row)
    # [REASON]: нарушения -- первыми: ради них экран и открывают, а в
    # хронологическом порядке они тонут среди подтверждённых.
    rank = {rc.V_NO_WORK: 0, rc.V_NONE: 1, rc.V_WORK: 2}
    rows.sort(key=lambda r: (rank.get(r['verdict'], 3),
                             r['app'].created_day, r['app'].number))
    shown = rows[:LIST_LIMIT]
    is_ru = _is_ru()
    choices = [
        _choice('verdict', _aw_t('Ҳукм', 'Вердикт'),
                (rc.V_NO_WORK, rc.V_WORK, rc.V_NONE),
                lambda key: labels.pick(labels.VERDICTS, key, is_ru), verdict),
        _choice('reason', _aw_t('Сабаб', 'Причина'), rc.FORWARD_REASONS,
                lambda key: labels.pick(labels.REASONS, key, is_ru), reason)]
    return render_template(
        'agro_work/applications.html',
        **_common(date_from, date_to, empty=None, rows=shown,
                  total=len(rows), org=org, category=category,
                  unmatched=unmatched, org_name=ctx.orgs.get(org) if org else None,
                  keep=_keep(org, category, unmatched), choices=choices))


# --- работа -> заявка ------------------------------------------------------------

@agro_work_bp.route('/work-days')
@module_required('wialon')
@login_required
def work_days():
    date_from, date_to = _period()
    ctx, empty = _context(date_from, date_to)
    if ctx is None:
        return render_template('agro_work/work_days.html',
                               **_common(date_from, date_to, empty=empty))
    org, category, unmatched = _group_filter()
    coverage = (request.args.get('coverage') or '').strip() or None
    reason = (request.args.get('reason') or '').strip() or None
    rows = []
    for row in ctx.reverse_rows():
        if not _in_group(ctx, row['equipment_id'], org, category, unmatched):
            continue
        if coverage and row['coverage'] != coverage:
            continue
        if reason and row['reason'] != reason:
            continue
        row['machine'] = _machine_label(ctx, row['equipment_id'])
        row['org_name'] = ctx.orgs.get(ctx.group_of(row['equipment_id'])[0], '')
        rows.append(row)
    rank = {rc.C_UNCOVERED: 0, rc.C_NONE: 1, rc.C_COVERED: 2}
    rows.sort(key=lambda r: (rank.get(r['coverage'], 3), r['day'],
                             r['machine']))
    is_ru = _is_ru()
    choices = [
        _choice('coverage', _aw_t('Қопланиш', 'Покрытие'),
                (rc.C_UNCOVERED, rc.C_COVERED, rc.C_NONE),
                lambda key: labels.pick(labels.COVERAGE, key, is_ru), coverage),
        _choice('reason', _aw_t('Сабаб', 'Причина'), rc.REVERSE_REASONS,
                lambda key: labels.pick(labels.REASONS, key, is_ru), reason)]
    return render_template(
        'agro_work/work_days.html',
        **_common(date_from, date_to, empty=None, rows=rows[:LIST_LIMIT],
                  total=len(rows), org=org, category=category,
                  unmatched=unmatched, org_name=ctx.orgs.get(org) if org else None,
                  keep=_keep(org, category, unmatched), choices=choices))


# --- одна машина -------------------------------------------------------------------

@agro_work_bp.route('/machine/<int:equipment_id>')
@module_required('wialon')
@login_required
def machine(equipment_id):
    date_from, date_to = _period()
    ctx, empty = _context(date_from, date_to)
    if ctx is None:
        return render_template('agro_work/machine.html',
                               **_common(date_from, date_to, empty=empty))
    if equipment_id not in ctx.equipment:
        abort(404)
    if not ctx.equipment_in_scope(equipment_id):
        # Та же граница, что у списков: чужую организацию не показывать.
        abort(403)
    days = ctx.machine_days(equipment_id)
    transports = [ctx.transports[t] for t in
                  ctx.transports_by_equipment.get(equipment_id, [])]
    forward = [ctx.forward_row(app) for t in transports
               for app in ctx.apps_by_transport.get(t['id'], [])
               if ctx.in_period(app)]
    forward.sort(key=lambda r: (r['app'].created_day, r['app'].number))
    for row in forward:
        row['work_type'] = ctx.work_type_names.get(row['app'].work_type_id) \
            or row['app'].row.get('work_type_name') or ''
    units = ctx.wialon_by_equipment.get(equipment_id, [])
    return render_template(
        'agro_work/machine.html',
        **_common(date_from, date_to, empty=None, days=days,
                  equipment=ctx.equipment[equipment_id],
                  org_name=ctx.orgs.get(ctx.equipment[equipment_id]
                                        ['organization_id'], ''),
                  transports=transports, forward=forward, units=units,
                  excluded=[u for u in units if u in ctx.excluded],
                  WORK=rc.WORK, IDLE=rc.IDLE))


# --- импорт ------------------------------------------------------------------------

@agro_work_bp.route('/import')
@module_required('wialon')
@login_required
def import_status():
    today = date.today()
    con = _open_ro()
    installed = con is not None and not store.missing_tables(con)
    runs, links, unmatched, work_types = [], {}, [], []
    try:
        if installed:
            con.row_factory = sqlite3.Row
            runs = [dict(r) for r in con.execute(
                'SELECT * FROM agro_work_import_runs ORDER BY id DESC LIMIT 20')]
            links = store.match_summary(con)
            if current_user.is_admin:
                unmatched = [r for r in store.unmatched_transports(con)
                             if r['applications']][:100]
            work_types = methods.work_types_for_markup(con)
    finally:
        if con is not None:
            con.close()
    marked = sum(1 for w in work_types if w['method'])
    waiting = sum(w['applications'] for w in work_types if not w['method'])
    return render_template(
        'agro_work/import.html',
        **_common(today, today, empty=None if installed else 'not_installed',
                  runs=runs, links=links, unmatched=unmatched,
                  work_types=work_types, marked=marked, waiting=waiting))
