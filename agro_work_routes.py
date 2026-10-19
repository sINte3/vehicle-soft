# -*- coding: utf-8 -*-
"""agro_work_routes.py -- B3, экран сверки заявок agro-work с фактом GPS.

Карта маршрутов:
  GET /agro-work/                 свод: организация x категория, обе стороны
  GET /agro-work/applications     заявка -> работа: вердикт и причина
  GET /agro-work/work-days        работа -> заявка: машино-сутки и покрытие
  GET /agro-work/export           книга сверки xlsx с фильтрами экрана
  GET /agro-work/machine/<id>     сутки одной машины: GPS и заявки рядом
  GET /agro-work/import           прогоны импорта, связки машин, разметка

Свод и оба списка -- один пункт меню «Сверка» с вкладками и общей строкой
фильтров (просьба владельца 19.10): период, организация, вид техники,
статус заявки, тип оплаты. Те же фильтры уходят в книгу xlsx.

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

import io
import os
import sqlite3
from datetime import date, datetime, timedelta, timezone

from flask import (Blueprint, abort, current_app, g, render_template, request,
                   send_file, url_for)
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
        'volume_label': lambda key: labels.pick(labels.VOLUME, key, is_ru),
        'volume_reason_label': lambda key: labels.pick(labels.VOLUME_REASONS,
                                                       key, is_ru),
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


def _known(name, table):
    """Значение параметра, если оно из таблицы подписей; иначе None."""
    value = (request.args.get(name) or '').strip()
    return value if value in table else None


class Filters:
    """Общая строка фильтров сверки: организация, вид техники, статус, оплата.

    [REASON]: одно определение для свода, обоих списков и книги xlsx -- иначе
    число на своде и строки списка, к которому оно ведёт, разошлись бы.
    Статус и тип оплаты -- свойства заявки: у суток работы по GPS фильтр
    смотрит на заявки этих суток (`row['apps']`), и сутки без заявки при
    таком фильтре не показываются -- у них нет ни статуса, ни оплаты.
    """

    def __init__(self):
        self.org, self.category, self.unmatched = _group_filter()
        self.status = _known('status', labels.STATUSES)
        self.payment = _known('pay', labels.PAYMENT_TYPES)

    @property
    def by_application(self):
        return bool(self.status or self.payment)

    def params(self):
        """Параметры адреса для ссылок и формы: только заданные."""
        values = {'org': 'none' if self.unmatched else self.org,
                  'cat': self.category, 'status': self.status,
                  'pay': self.payment}
        return {key: value for key, value in values.items() if value}

    def app_ok(self, app):
        if self.status and app.status != self.status:
            return False
        if self.payment and (app.row.get('payment_type') or '') != self.payment:
            return False
        return True

    def group_ok(self, ctx, equipment_id):
        return _in_group(ctx, equipment_id, self.org, self.category,
                         self.unmatched)

    def forward(self, ctx, rows):
        return [row for row in rows
                if self.group_ok(ctx, row['equipment_id'])
                and self.app_ok(row['app'])]

    def reverse(self, ctx, rows):
        return [row for row in rows
                if self.group_ok(ctx, row['equipment_id'])
                and (not self.by_application
                     or any(self.app_ok(app) for app in row['apps']))]

    def opened(self, ctx):
        return [row for row in ctx.open_applications()
                if self.group_ok(ctx, row['equipment_id'])
                and self.app_ok(row['app'])]

    def choices(self, ctx):
        """Выпадающие списки общей строки, в порядке на экране."""
        is_ru = _is_ru()
        orgs = sorted({ctx.group_of(eq)[0] for eq in ctx.equipment
                       if ctx.equipment_in_scope(eq)} - {None},
                      key=lambda org_id: ctx.orgs.get(org_id, ''))
        org_keys = [str(org_id) for org_id in orgs]
        names = {str(org_id): ctx.orgs.get(org_id, '') for org_id in orgs}
        if ctx.org_ids is None:
            # Машины agro-work без нашей техники видит только тот, кому видны
            # все организации (как в списках, `_scope`).
            org_keys.append('none')
            names['none'] = _aw_t('Бизнинг техника билан боғланмаган',
                                  'Не связаны с нашей техникой')
        present = {ctx.group_of(eq)[1] for eq in ctx.equipment
                   if ctx.equipment_in_scope(eq)}
        categories = [key for key in CATEGORIES if key in present]
        current_org = 'none' if self.unmatched else (
            str(self.org) if self.org else None)
        return [
            _choice('org', _aw_t('Ташкилот', 'Организация'), org_keys,
                    lambda key: names.get(key, key), current_org),
            _choice('cat', _aw_t('Техника тури', 'Вид техники'), categories,
                    lambda key: CATEGORIES.get(key, key), self.category),
            _choice('status', _aw_t('Буюртма ҳолати', 'Статус заявки'),
                    list(labels.STATUSES),
                    lambda key: labels.pick(labels.STATUSES, key, is_ru),
                    self.status),
            _choice('pay', _aw_t('Тўлов тури', 'Тип оплаты'),
                    list(labels.PAYMENT_TYPES),
                    lambda key: labels.pick(labels.PAYMENT_TYPES, key, is_ru),
                    self.payment)]

    def describe(self, ctx):
        """[(ru, uz)] заданных фильтров -- строка над сводом книги xlsx."""
        out = []
        if self.unmatched:
            out.append(('организация: машины, не связанные с нашей техникой',
                        'ташкилот: бизнинг техника билан боғланмаган машиналар'))
        elif self.org:
            name = ctx.orgs.get(self.org, str(self.org))
            out.append(('организация: ' + name, 'ташкилот: ' + name))
        if self.category:
            label = CATEGORIES.get(self.category, self.category)
            out.append(('вид техники: ' + label, 'техника тури: ' + label))
        if self.status:
            pair = labels.STATUSES[self.status]
            out.append(('статус заявки: ' + pair[0],
                        'буюртма ҳолати: ' + pair[1]))
        if self.payment:
            pair = labels.PAYMENT_TYPES[self.payment]
            out.append(('тип оплаты: ' + pair[0], 'тўлов тури: ' + pair[1]))
        return out


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


def _screen(date_from, date_to, flt, view, ctx=None, **extra):
    """Контекст страниц сверки: вкладки, ссылки с фильтрами, кнопка xlsx."""
    base = {'from': date_from.isoformat(), 'to': date_to.isoformat()}
    base.update(flt.params())

    def link(endpoint, **overrides):
        # [REASON]: ссылка со свода или вкладки несёт заданные фильтры:
        # число «12» на своде с фильтром «Наличные» обязано открыть те же
        # 12 строк, а не все.
        values = dict(base)
        values.update(overrides)
        return url_for(endpoint, **{k: v for k, v in values.items()
                                    if v not in (None, '')})

    values = _common(date_from, date_to, view=view, link=link,
                     filters=flt, export_url=link('agro_work.export'))
    values['common_choices'] = flt.choices(ctx) if ctx is not None else []
    values.update(extra)
    return values


# --- свод --------------------------------------------------------------------

@agro_work_bp.route('/')
@module_required('wialon')
@login_required
def dashboard():
    date_from, date_to = _period()
    flt = Filters()
    ctx, empty = _context(date_from, date_to)
    if ctx is None:
        return render_template('agro_work/dashboard.html',
                               **_screen(date_from, date_to, flt, 'summary',
                                         empty=empty))
    forward = flt.forward(ctx, ctx.forward_rows())
    reverse = flt.reverse(ctx, ctx.reverse_rows())
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
    for item in flt.opened(ctx):
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
        **_screen(date_from, date_to, flt, 'summary', ctx, empty=None,
                  rows=rows, total=total,
                  orphan_days=ctx.orphan_work_days(),
                  open_groups=open_groups,
                  after_keys=[(kind, rc.DAY_AFTER + kind)
                              for kind in rc.AFTER_KINDS],
                  volume_keys=[(key, rc.VOL + key)
                               for key in rc.VOLUME_VERDICTS]))


# --- заявка -> работа ----------------------------------------------------------

@agro_work_bp.route('/applications')
@module_required('wialon')
@login_required
def applications():
    date_from, date_to = _period()
    flt = Filters()
    ctx, empty = _context(date_from, date_to)
    if ctx is None:
        return render_template('agro_work/applications.html',
                               **_screen(date_from, date_to, flt,
                                         'applications', empty=empty))
    verdict = (request.args.get('verdict') or '').strip() or None
    reason = (request.args.get('reason') or '').strip() or None
    volume = (request.args.get('volume') or '').strip() or None
    rows = []
    for row in flt.forward(ctx, ctx.forward_rows()):
        if verdict and row['verdict'] != verdict:
            continue
        if reason and row['reason'] != reason:
            continue
        if volume and rc.volume_key(row) != volume:
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
    # U2: внутри «работа была» -- вне допуска и на грани первыми.
    volume_rank = {key: index for index, key in enumerate(rc.VOLUME_VERDICTS)}
    rows.sort(key=lambda r: (rank.get(r['verdict'], 3),
                             volume_rank.get(rc.volume_key(r), 0),
                             r['app'].created_day, r['app'].number))
    shown = rows[:LIST_LIMIT]
    is_ru = _is_ru()
    choices = [
        _choice('verdict', _aw_t('Ҳукм', 'Вердикт'),
                (rc.V_NO_WORK, rc.V_WORK, rc.V_NONE),
                lambda key: labels.pick(labels.VERDICTS, key, is_ru), verdict),
        _choice('reason', _aw_t('Сабаб', 'Причина'), rc.FORWARD_REASONS,
                lambda key: labels.pick(labels.REASONS, key, is_ru), reason),
        # U2: объём заявки против гектаров GPS -- только «работа была».
        _choice('volume', _aw_t('Ҳажм GPS га қарши', 'Объём против GPS'),
                rc.VOLUME_VERDICTS,
                lambda key: labels.pick(labels.VOLUME, key, is_ru), volume)]
    return render_template(
        'agro_work/applications.html',
        **_screen(date_from, date_to, flt, 'applications', ctx, empty=None,
                  rows=shown, total=len(rows), choices=choices))


# --- работа -> заявка ------------------------------------------------------------

@agro_work_bp.route('/work-days')
@module_required('wialon')
@login_required
def work_days():
    date_from, date_to = _period()
    flt = Filters()
    ctx, empty = _context(date_from, date_to)
    if ctx is None:
        return render_template('agro_work/work_days.html',
                               **_screen(date_from, date_to, flt, 'work_days',
                                         empty=empty))
    coverage = (request.args.get('coverage') or '').strip() or None
    reason = (request.args.get('reason') or '').strip() or None
    after = (request.args.get('after') or '').strip() or None
    rows = []
    for row in flt.reverse(ctx, ctx.reverse_rows()):
        if coverage and row['coverage'] != coverage:
            continue
        if reason and row['reason'] != reason:
            continue
        if after and row['after'] != after:
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
                lambda key: labels.pick(labels.REASONS, key, is_ru), reason),
        # B5: сутки «без заявки» по тому, что было потом.
        _choice('after', _aw_t('Буюртмасиз: кейин нима бўлган',
                               'Без заявки: что было потом'),
                rc.AFTER_KINDS,
                lambda key: labels.pick(labels.AFTER, key, is_ru), after)]
    return render_template(
        'agro_work/work_days.html',
        **_screen(date_from, date_to, flt, 'work_days', ctx, empty=None,
                  rows=rows[:LIST_LIMIT], total=len(rows), choices=choices))


# --- книга xlsx ------------------------------------------------------------------

XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


@agro_work_bp.route('/export')
@module_required('wialon')
@login_required
def export():
    """Книга сверки за период с общими фильтрами экрана.

    [REASON]: та же книга, что строит `tools/agro_work_reconcile.py` (листы
    и столбцы одни), а не новая: выгрузки уходят людям, и две книги сверки
    с разным содержанием разошлись бы. Фильтры вкладок (вердикт, покрытие)
    в книгу не идут: в ней обе стороны сверки, и фильтр одной стороны
    сделал бы свод несогласованным; общие фильтры названы строкой над
    сводом.
    """
    from tools.agro_work_reconcile import build_book
    date_from, date_to = _period()
    flt = Filters()
    con = _open_ro()
    if con is None or store.missing_tables(con):
        if con is not None:
            con.close()
        abort(404)
    try:
        ctx = rc.Reconciliation(con, date_from, date_to, org_ids=_scope())
        forward = flt.forward(ctx, ctx.forward_rows())
        reverse = flt.reverse(ctx, ctx.reverse_rows())
        lags, excluded = rc.measure_lags(con, today=ctx.today)
    finally:
        con.close()
    book = build_book(ctx, forward, reverse, lags, excluded, None,
                      open_rows=flt.opened(ctx), filters=flt.describe(ctx))
    out = io.BytesIO()
    book.save(out)
    out.seek(0)
    return send_file(out, mimetype=XLSX, as_attachment=True,
                     download_name='sverka_%s_%s.xlsx'
                     % (date_from.isoformat(), date_to.isoformat()))


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
        # Решение владельца 09.10: открытая заявка -- нарушение, с возрастом
        # рядом, как в списке заявок.
        row['days_open'] = ((ctx.today - row['app'].created_day).days
                            if row['reason'] == rc.R_OPEN else None)
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
