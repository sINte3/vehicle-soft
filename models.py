"""Database models — v4 with Wialon import support."""

from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

# ─── Roles ────────────────────────────────────────────────────────────
ROLE_ADMIN    = 'admin'
ROLE_OPERATOR = 'operator'
ROLE_VIEWER   = 'viewer'
# [REASON]: WORK-ORDER-001 — new role for shop mechanics who execute work orders.
ROLE_MECHANIC = 'mechanic'

ROLES = {
    ROLE_ADMIN:    'Администратор',
    ROLE_OPERATOR: 'Оператор',
    ROLE_MECHANIC: 'Механик',
    ROLE_VIEWER:   'Наблюдатель',
}

# ─── Equipment Categories (9 total matching Excel structure) ─────────
CAT_YUKORI        = 'yukori'        # 1. Юқори унумли техникалар
CAT_MTZ           = 'mtz'          # 2. Чопиқ тракторлар
CAT_QATNOV        = 'qatnov'       # 3. Қатнов тракторлар
CAT_MINI          = 'mini'         # 4. Мини тракторлар
CAT_COMBINE       = 'combine'      # 5. Комбайнлар
CAT_SPECIAL       = 'special'      # 6. Махсус техникалар
CAT_YUK_TRANSPORT = 'yuk_transport' # 7. Юк ташувчи техникалар
CAT_MOTORCYCLE    = 'motorcycle'   # 8. Мотоцикл
CAT_PASSENGER     = 'passenger'    # 9. Йўловчи ташиш техникаси

CATEGORIES = {
    CAT_YUKORI:        '1. Юқори унумли техникалар',
    CAT_MTZ:           '2. Чопиқ тракторлар',
    CAT_QATNOV:        '3. Қатнов тракторлар',
    CAT_MINI:          '4. Мини тракторлар',
    CAT_COMBINE:       '5. Комбайнлар',
    CAT_SPECIAL:       '6. Махсус техникалар',
    CAT_YUK_TRANSPORT: '7. Юк ташувчи техникалар',
    CAT_MOTORCYCLE:    '8. Мотоцикл',
    CAT_PASSENGER:     '9. Йўловчи ташиш техникаси',
}

# Groupings for Excel report (keeps 3-sheet structure)
REPORT_GROUPS = {
    'tractors': {
        'label': 'Тракторлар',
        'cats':  [CAT_MTZ, CAT_QATNOV, CAT_MINI],
    },
    'yukori': {
        'label': 'Юқори унумли ва махсус техникалар',
        'cats':  [CAT_YUKORI, CAT_COMBINE, CAT_SPECIAL],
    },
    'transport': {
        'label': 'Юк ташувчи ва бошқа техникалар',
        'cats':  [CAT_YUK_TRANSPORT, CAT_MOTORCYCLE, CAT_PASSENGER],
    },
}


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id             = db.Column(db.Integer, primary_key=True)
    username       = db.Column(db.String(80), unique=True, nullable=False)
    password_hash  = db.Column(db.String(256), nullable=False)
    full_name      = db.Column(db.String(200), default='')
    role           = db.Column(db.String(20), nullable=False, default=ROLE_OPERATOR)
    is_active_user = db.Column(db.Boolean, default=True)
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)
    last_login     = db.Column(db.DateTime, nullable=True)
    language       = db.Column(db.String(5), default='uz')

    # BOT001: Telegram integration fields
    # [REASON]: Added via migrate_bot001_telegram_foundation.py for existing DBs.
    # SQLAlchemy model kept in sync so db.create_all() works for fresh installs.
    telegram_id             = db.Column(db.Integer, unique=True, nullable=True)
    tg_notifications        = db.Column(db.Integer, nullable=False, default=1)
    tg_quiet_hours          = db.Column(db.String(20), nullable=True)
    tg_link_code_hash       = db.Column(db.String(128), nullable=True)   # SHA-256 of one-time code
    tg_link_code_expires_at = db.Column(db.DateTime, nullable=True)
    tg_link_code_created_at = db.Column(db.DateTime, nullable=True)

    organizations = db.relationship('Organization', secondary='user_organizations',
                                    backref=db.backref('users', lazy='dynamic'))

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_active(self):
        return self.is_active_user

    @property
    def is_admin(self):
        return self.role == ROLE_ADMIN

    @property
    def can_edit(self):
        return self.role in (ROLE_ADMIN, ROLE_OPERATOR)

    @property
    def role_display(self):
        return ROLES.get(self.role, self.role)

    def can_access_org(self, org_id):
        if self.is_admin:
            return True
        return any(o.id == org_id for o in self.organizations)

    def get_org_ids(self):
        if self.is_admin:
            return [o.id for o in Organization.query.all()]
        return [o.id for o in self.organizations]

    def has_module_access(self, module_code):
        # [REASON]: Admin bypasses module check entirely; non-admin requires an explicit
        # has_access=True record in user_module_permissions — deny-by-default policy.
        if self.is_admin:
            return True
        perm = UserModulePermission.query.filter_by(
            user_id=self.id, module_code=module_code
        ).first()
        return bool(perm and perm.has_access)


user_organizations = db.Table('user_organizations',
    db.Column('user_id',         db.Integer, db.ForeignKey('users.id'),         primary_key=True),
    db.Column('organization_id', db.Integer, db.ForeignKey('organizations.id'), primary_key=True),
)


class Organization(db.Model):
    __tablename__ = 'organizations'
    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(200), nullable=False)
    short_name = db.Column(db.String(100), default='')
    sort_order = db.Column(db.Integer, default=0)
    # [REASON]: AZS-ORG-REFACTOR unblock -- production carries duplicate
    # organisation rows (ids 20-24) that are linked to fuel warehouses and
    # therefore CANNOT be deleted; they must be hideable instead. Columns
    # only (added for existing DBs by migrate_core_foundation_001.py):
    # deciding WHICH rows to hide is the owner's data decision, made later,
    # and no row is modified by the migration. Nothing reads these yet.
    is_active   = db.Column(db.Boolean, default=True)
    archived_at = db.Column(db.DateTime, nullable=True)
    equipment  = db.relationship('Equipment', backref='organization',
                                 cascade='all, delete-orphan', lazy='dynamic')


class Equipment(db.Model):
    __tablename__ = 'equipment'
    id              = db.Column(db.Integer, primary_key=True)
    name            = db.Column(db.String(200), nullable=False)
    plate           = db.Column(db.String(50), default='')
    category        = db.Column(db.String(20), nullable=False)
    eq_type         = db.Column(db.String(100), default='')
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False)
    default_price   = db.Column(db.Float, default=0)
    default_unit    = db.Column(db.String(30), default='')
    is_active       = db.Column(db.Boolean, default=True)

    # [REASON]: SPARE-STAGE3 — canonical equipment-model reference, added
    # ALONGSIDE the legacy free-text `eq_type` (added via
    # migrate_spare_parts_stage3.py for existing DBs). `eq_type` is NOT removed
    # or renamed — it is still read directly by daily_entry.html and written by
    # the equipment form / init_data.py. Whenever model_id is set through the
    # new UI, eq_type is kept in sync (as the model's name text) so every
    # existing eq_type reader keeps seeing correct data.
    model_id        = db.Column(db.Integer, db.ForeignKey('equipment_models.id'), nullable=True)

    records = db.relationship('DailyRecord', backref='equipment',
                              cascade='all, delete-orphan', lazy='dynamic')
    model   = db.relationship('EquipmentModel', foreign_keys=[model_id],
                              backref=db.backref('equipment', lazy='dynamic'))

    @property
    def full_name(self):
        if self.plate:
            return f"{self.name}\n{self.plate}"
        return self.name

    @property
    def category_display(self):
        return CATEGORIES.get(self.category, self.category)


class EquipmentModel(db.Model):
    """SPARE-STAGE3: canonical equipment-model reference.

    [REASON]: SPARE-STAGE3 — the spare-parts compatibility matrix needs a stable
    identifier for "what model of machine is this part for". Historically the
    only signal was Equipment.eq_type free text (e.g. 'New Holland 7060' vs
    'NEW HOLLAND-7060' typed inconsistently). This table is seeded one-to-one
    from every distinct eq_type value by the Stage-3 migration (no near-match
    dedup — that is a manual human decision made later via the merge screen).
    `migrated_from_eq_type` keeps the raw source text for audit/traceability.
    """
    __tablename__ = 'equipment_models'
    id                    = db.Column(db.Integer, primary_key=True)
    name                  = db.Column(db.String(150), nullable=False, unique=True)
    name_uz               = db.Column(db.String(150), nullable=True)
    manufacturer          = db.Column(db.String(100), nullable=True)
    is_active             = db.Column(db.Boolean, default=True)
    # [REASON]: SPARE-STAGE3 — audit trail of the exact eq_type text this
    # canonical model was migrated from; NULL for models created by hand later.
    migrated_from_eq_type = db.Column(db.String(150), nullable=True)
    created_at            = db.Column(db.DateTime, default=datetime.utcnow)


class WorkType(db.Model):
    __tablename__ = 'work_types'
    id            = db.Column(db.Integer, primary_key=True)
    name          = db.Column(db.String(200), nullable=False)
    default_unit  = db.Column(db.String(30), default='га')
    default_price = db.Column(db.Float, default=0)


class Customer(db.Model):
    __tablename__ = 'customers'
    id            = db.Column(db.Integer, primary_key=True)
    name          = db.Column(db.String(300), nullable=False)
    customer_type = db.Column(db.String(20), default='external')

    # [REASON]: PHASE1 shared foundation -- customers is a SHARED table (read
    # by daily_records and work_orders; the drone and GPS tracks will attach
    # field contours to it). Additive nullable columns only, added for
    # existing DBs by migrate_core_foundation_001.py; every existing reader
    # keeps seeing exactly the rows it saw before.
    inn     = db.Column(db.String(20), nullable=True)
    phone   = db.Column(db.String(50), nullable=True)
    address = db.Column(db.String(300), nullable=True)
    notes   = db.Column(db.Text, nullable=True)
    # [REASON]: hide-not-delete -- a customer referenced by history cannot be
    # deleted; active=False will hide it from pickers. Wiring the flag into
    # the reference screens is a later task; nothing reads it yet.
    active  = db.Column(db.Boolean, default=True)


class DailyRecord(db.Model):
    __tablename__ = 'daily_records'
    id           = db.Column(db.Integer, primary_key=True)
    work_date    = db.Column(db.Date, nullable=False, index=True)
    equipment_id = db.Column(db.Integer, db.ForeignKey('equipment.id'), nullable=False)
    line_index   = db.Column(db.Integer, default=0)

    status    = db.Column(db.String(20), default='idle')
    work_type = db.Column(db.String(200), default='')
    customer  = db.Column(db.String(300), default='')
    unit      = db.Column(db.String(30), default='')
    quantity  = db.Column(db.Float, nullable=True)
    price     = db.Column(db.Float, nullable=True)

    amount_cash     = db.Column(db.Float, default=0)
    amount_transfer = db.Column(db.Float, default=0)
    amount_internal = db.Column(db.Float, default=0)
    amount_other    = db.Column(db.Float, default=0)
    payment_type    = db.Column(db.String(20), default='')

    idle_reason = db.Column(db.String(300), default='')
    note        = db.Column(db.Text, default='')

    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    # [REASON]: WORK-ORDER-001 — back-link to the work order that auto-created this
    # row on close (Phase 2). Nullable; existing rows and manual entries stay NULL.
    work_order_id = db.Column(db.Integer, db.ForeignKey('work_orders.id'), nullable=True)

    # [REASON]: DAILY_UNITS_001 — приведённое значение единицы измерения.
    # `unit` остаётся историческим снимком и не переписывается никогда; отчёты
    # группируют по `unit_code`, иначе «га» и «Га» расходятся по разным строкам.
    # NULL означает «не распознано» — это состояние, а не пробел заполнения.
    # Добавлено миграцией migrate_daily_units_001.py для существующих баз;
    # модель держится в синхроне, чтобы db.create_all() поднимал чистую базу.
    unit_code = db.Column(db.String(30), nullable=True)

    __table_args__ = (
        db.Index('ix_daily_date_eq', 'work_date', 'equipment_id', 'line_index'),
    )

    @property
    def total_amount(self):
        return ((self.amount_cash or 0) + (self.amount_transfer or 0) +
                (self.amount_internal or 0) + (self.amount_other or 0))


class DailyRecordUnit(db.Model):
    """Управляемый двуязычный справочник единиц дневного ввода.

    Решение владельца (2026-08-09, docs/ux/30-design-decisions.md, DD-004):
    единица перестаёт быть свободной строкой. `code` — короткий стабильный
    латинский ключ, он и пишется в `DailyRecord.unit_code`; `name_ru` и
    `name_uz` — изменяемые подписи, поэтому переименование никогда не ломает
    сохранённые данные.

    [REASON]: исторические значения `DailyRecord.unit` — снимки и НЕ
    переписываются. Справочник управляет только тем, что предлагается
    дальше. Значение, не попавшее в карту приведения, остаётся с
    `unit_code = NULL` и в справочник не добавляется: среди накопленного
    есть запись длиной 253 символа, и авторегистрация завела бы абзац текста
    в справочник единиц измерения.

    Форма повторяет SparePartUnit. Наполняется migrate_daily_units_001.py.
    """
    __tablename__ = 'daily_record_units'
    id         = db.Column(db.Integer, primary_key=True)
    code       = db.Column(db.String(30), nullable=False, unique=True)
    name_ru    = db.Column(db.String(100), nullable=False)
    name_uz    = db.Column(db.String(100), nullable=False)
    is_active  = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=100)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def label(self, lang):
        return self.name_uz if lang == 'uz' else self.name_ru


class Deficiency(db.Model):
    """Аникланган камчиликлар."""
    __tablename__ = 'deficiencies'
    id              = db.Column(db.Integer, primary_key=True)
    work_date       = db.Column(db.Date, nullable=False, index=True)
    sort_order      = db.Column(db.Integer, default=0)
    text            = db.Column(db.Text, nullable=False)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=True)
    created_by      = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)

    organization = db.relationship('Organization')


# ─── Work Orders (WORK-ORDER-001) ─────────────────────────────────────
WO_STATUS_DRAFT       = 'draft'
WO_STATUS_ASSIGNED    = 'assigned'
WO_STATUS_IN_PROGRESS = 'in_progress'
WO_STATUS_DONE        = 'done'
WO_STATUS_CANCELLED   = 'cancelled'

WO_STATUSES = [
    WO_STATUS_DRAFT,
    WO_STATUS_ASSIGNED,
    WO_STATUS_IN_PROGRESS,
    WO_STATUS_DONE,
    WO_STATUS_CANCELLED,
]

WO_STATUS_LABELS_RU = {
    WO_STATUS_DRAFT:       'Черновик',
    WO_STATUS_ASSIGNED:    'Назначен',
    WO_STATUS_IN_PROGRESS: 'В работе',
    WO_STATUS_DONE:        'Выполнен',
    WO_STATUS_CANCELLED:   'Отменён',
}

WO_STATUS_LABELS_UZ = {
    WO_STATUS_DRAFT:       'Qoralama',
    WO_STATUS_ASSIGNED:    'Tayinlangan',
    WO_STATUS_IN_PROGRESS: 'Bajarilmoqda',
    WO_STATUS_DONE:        'Bajarildi',
    WO_STATUS_CANCELLED:   'Bekor qilindi',
}

# [REASON]: WORK-ORDER-001 — payment_type is stored as a short code ('naqd' etc.)
# for DB stability; UI always shows the localised label from these dicts.
WO_PAYMENT_TYPES_RU = {
    'naqd':   'Наличные',
    'bank':   'Банк',
    'ichki':  'Внутренний',
    'boshqa': 'Прочее',
}

WO_PAYMENT_TYPES_UZ = {
    'naqd':   'Накд',
    'bank':   'Банк',
    'ichki':  'Ички',
    'boshqa': 'Бошқа',
}

WO_EVENT_STATUS_CHANGE     = 'status_change'
WO_EVENT_PRICE_OVERRIDE    = 'price_override'
WO_EVENT_ASSIGNMENT_CHANGE = 'assignment_change'


class WorkOrder(db.Model):
    __tablename__ = 'work_orders'
    id              = db.Column(db.Integer, primary_key=True)
    number          = db.Column(db.String(20), unique=True, nullable=False)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False)
    equipment_id    = db.Column(db.Integer, db.ForeignKey('equipment.id'), nullable=False)
    work_type_id    = db.Column(db.Integer, db.ForeignKey('work_types.id'), nullable=True)
    work_type_text  = db.Column(db.String(200), nullable=False, default='')
    customer_id     = db.Column(db.Integer, db.ForeignKey('customers.id'), nullable=True)
    customer_text   = db.Column(db.String(300), nullable=False, default='')
    assigned_to     = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_by      = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    status          = db.Column(db.String(20), nullable=False, default=WO_STATUS_DRAFT)
    planned_date    = db.Column(db.Date, nullable=False)
    actual_date     = db.Column(db.Date, nullable=True)
    unit            = db.Column(db.String(30), nullable=False, default='ga')
    planned_quantity = db.Column(db.Float, nullable=True)
    actual_quantity  = db.Column(db.Float, nullable=True)
    default_price   = db.Column(db.Float, nullable=False, default=0.0)
    price           = db.Column(db.Float, nullable=False, default=0.0)
    payment_type    = db.Column(db.String(20), nullable=False, default='')
    note            = db.Column(db.Text, nullable=False, default='')
    daily_record_id = db.Column(db.Integer, db.ForeignKey('daily_records.id'), nullable=True)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at      = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    closed_at       = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.Index('ix_wo_org_status_date', 'organization_id', 'status', 'planned_date'),
        db.Index('ix_wo_equipment_date',  'equipment_id', 'planned_date'),
        db.Index('ix_wo_assigned',        'assigned_to', 'status'),
    )

    organization = db.relationship('Organization', foreign_keys=[organization_id], lazy='joined')
    equipment    = db.relationship('Equipment',    foreign_keys=[equipment_id],    lazy='joined')
    work_type    = db.relationship('WorkType',     foreign_keys=[work_type_id])
    customer     = db.relationship('Customer',     foreign_keys=[customer_id])
    creator      = db.relationship('User',         foreign_keys=[created_by])
    assignee     = db.relationship('User',         foreign_keys=[assigned_to])
    history      = db.relationship('WorkOrderStatusHistory',
                                   backref='work_order',
                                   cascade='all, delete-orphan',
                                   order_by='WorkOrderStatusHistory.changed_at',
                                   lazy='dynamic')

    def can_edit(self, user):
        """True if user may edit this work order.

        [REASON]: WORK-ORDER-001 ownership rule — admin edits anything; others may
        edit only their own draft (matrix in the TZ, section 3.2).
        """
        if user is None:
            return False
        if user.role == ROLE_ADMIN:
            return True
        if self.status != WO_STATUS_DRAFT:
            return False
        return self.created_by == user.id

    @property
    def is_active(self):
        return self.status not in (WO_STATUS_DONE, WO_STATUS_CANCELLED)

    def status_label(self, lang='ru'):
        labels = WO_STATUS_LABELS_UZ if lang == 'uz' else WO_STATUS_LABELS_RU
        return labels.get(self.status, self.status)


class WorkOrderStatusHistory(db.Model):
    __tablename__ = 'work_order_status_history'
    id            = db.Column(db.Integer, primary_key=True)
    work_order_id = db.Column(db.Integer,
                              db.ForeignKey('work_orders.id', ondelete='CASCADE'),
                              nullable=False)
    event_type    = db.Column(db.String(30), nullable=False, default=WO_EVENT_STATUS_CHANGE)
    old_value     = db.Column(db.String(200), nullable=True)
    new_value     = db.Column(db.String(200), nullable=False)
    changed_by    = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    changed_at    = db.Column(db.DateTime, default=datetime.utcnow)
    comment       = db.Column(db.Text, nullable=False, default='')

    user = db.relationship('User', foreign_keys=[changed_by])


# ─── NEW: Wialon integration tables ──────────────────────────────────

class VialonMapping(db.Model):
    """
    Mapping between Wialon vehicle names and Equipment records.
    Set once, applied automatically on every subsequent import.
    """
    __tablename__ = 'vialon_mappings'
    id           = db.Column(db.Integer, primary_key=True)
    vialon_name  = db.Column(db.String(300), unique=True, nullable=False)
    # [REASON]: PHASE1 -- vialon_name is the historical unique key, but names
    # in Wialon change, six plates point at more than one object, and one
    # machine exists as three objects because trackers were replaced.
    # wialon_id is the durable key for the GPS track. Nullable + indexed
    # (added for existing DBs by migrate_core_foundation_001.py); the
    # existing manual import never sets it and keeps working exactly as
    # before.
    wialon_id    = db.Column(db.Integer, nullable=True, index=True)
    equipment_id = db.Column(db.Integer, db.ForeignKey('equipment.id'), nullable=True)
    skip         = db.Column(db.Boolean, default=False)   # True = not our vehicle, ignore
    created_by   = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at   = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    equipment = db.relationship('Equipment', backref='vialon_mappings')


class VialonImport(db.Model):
    """Log of Wialon file imports."""
    __tablename__ = 'vialon_imports'
    id               = db.Column(db.Integer, primary_key=True)
    import_date      = db.Column(db.Date, nullable=False)   # date of the report data
    filename         = db.Column(db.String(300), default='')
    vehicles_in_file = db.Column(db.Integer, default=0)
    vehicles_matched = db.Column(db.Integer, default=0)
    vehicles_saved   = db.Column(db.Integer, default=0)
    vehicles_skipped = db.Column(db.Integer, default=0)
    vehicles_unknown = db.Column(db.Integer, default=0)
    # JSON list of unknown vehicle names for admin review: '["Name1","Name2"]'
    unknown_vehicles_json = db.Column(db.Text, default='[]')
    created_by       = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at       = db.Column(db.DateTime, default=datetime.utcnow)

    records = db.relationship('EngineHoursRecord', backref='import_log',
                              cascade='all, delete-orphan', lazy='dynamic')


class EngineHoursRecord(db.Model):
    """
    Engine hours per equipment per day, imported from Wialon.
    Separate from DailyRecord — doesn't interfere with work records.
    """
    __tablename__ = 'engine_hours_records'
    id           = db.Column(db.Integer, primary_key=True)
    work_date    = db.Column(db.Date, nullable=False, index=True)
    equipment_id = db.Column(db.Integer, db.ForeignKey('equipment.id'), nullable=False)
    import_id    = db.Column(db.Integer, db.ForeignKey('vialon_imports.id'), nullable=True)

    # Hours as decimal floats (e.g. 5:55:41 -> 5.928)
    engine_hours  = db.Column(db.Float, default=0)   # total engine-on time
    hours_moving  = db.Column(db.Float, default=0)   # in motion
    hours_idle    = db.Column(db.Float, default=0)   # engine on but not moving

    vialon_name   = db.Column(db.String(300), default='')  # original name for audit

    __table_args__ = (
        db.UniqueConstraint('work_date', 'equipment_id',
                            name='uq_engine_hours_date_eq'),
    )

    equipment = db.relationship('Equipment',
                                backref=db.backref('engine_hours_records', lazy='dynamic'))


# ─── NEW: Topaz Fuel Integration ──────────────────────────────────────────────

class FuelStation(db.Model):
    """АЗС точки из Топаз (dcPointsOfSales)."""
    __tablename__ = 'fuel_stations'
    id         = db.Column(db.Integer, primary_key=True)
    pos_id     = db.Column(db.Integer, unique=True, nullable=False)  # Topaz PointOfSalesID
    pos_name   = db.Column(db.String(200), default='')
    pos_code   = db.Column(db.String(50),  default='')
    is_active  = db.Column(db.Boolean, default=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

    tanks      = db.relationship('FuelTank', backref='station',
                                  cascade='all, delete-orphan', lazy='dynamic')
    snapshots  = db.relationship('FuelSnapshot', backref='station',
                                  cascade='all, delete-orphan', lazy='dynamic')


class FuelTank(db.Model):
    """Резервуары АЗС."""
    __tablename__ = 'fuel_tanks'
    id          = db.Column(db.Integer, primary_key=True)
    station_id  = db.Column(db.Integer, db.ForeignKey('fuel_stations.id'), nullable=False)
    tank_name   = db.Column(db.String(200), default='')
    fuel_name   = db.Column(db.String(100), default='')
    max_volume  = db.Column(db.Float, default=0)
    updated_at  = db.Column(db.DateTime, default=datetime.utcnow)


class FuelSnapshot(db.Model):
    """
    Снимок уровня топлива в резервуаре (из dcSnapshotsTanks).
    Агент отправляет последние 500 записей при каждом запуске.
    Хранится только последний снимок на резервуар (upsert по station+tank_name).
    """
    __tablename__ = 'fuel_snapshots'
    id            = db.Column(db.Integer, primary_key=True)
    station_id    = db.Column(db.Integer, db.ForeignKey('fuel_stations.id'), nullable=False)
    snapshot_date = db.Column(db.DateTime, nullable=False, index=True)
    tank_name     = db.Column(db.String(200), default='')
    fuel_name     = db.Column(db.String(100), default='')
    volume        = db.Column(db.Float, default=0)      # текущий объём, литры
    max_volume    = db.Column(db.Float, default=0)      # ёмкость резервуара
    temperature   = db.Column(db.Float, nullable=True)
    density       = db.Column(db.Float, nullable=True)
    height        = db.Column(db.Float, nullable=True)

    __table_args__ = (
        db.UniqueConstraint('station_id', 'tank_name',
                            name='uq_snapshot_station_tank'),
    )

    @property
    def fill_pct(self):
        if self.max_volume and self.max_volume > 0:
            return min(self.volume / self.max_volume, 1.0)
        return 0.0


class FuelTransaction(db.Model):
    """
    Транзакции отпуска топлива по картам Mifare (из rgChargeCardRests).
    """
    __tablename__ = 'fuel_transactions'
    id          = db.Column(db.Integer, primary_key=True)
    station_id  = db.Column(db.Integer, db.ForeignKey('fuel_stations.id'), nullable=True)
    tx_date     = db.Column(db.DateTime, nullable=False, index=True)
    card_id     = db.Column(db.String(100), default='')
    fuel_name   = db.Column(db.String(100), default='')
    volume      = db.Column(db.Float, default=0)
    amount      = db.Column(db.Float, default=0)
    price       = db.Column(db.Float, default=0)
    azs_code    = db.Column(db.String(50),  default='')
    session_num = db.Column(db.Integer, nullable=True)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.Index('ix_fuel_tx_date_azs', 'tx_date', 'azs_code'),
    )


class FuelSyncLog(db.Model):
    """История синхронизаций с агентом Топаз."""
    __tablename__ = 'fuel_sync_logs'
    id              = db.Column(db.Integer, primary_key=True)
    synced_at       = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    stations_count  = db.Column(db.Integer, default=0)
    snapshots_count = db.Column(db.Integer, default=0)
    tx_count        = db.Column(db.Integer, default=0)
    status          = db.Column(db.String(20), default='ok')  # ok / error
    error_msg       = db.Column(db.Text, default='')


# ═══════════════════════════════════════════════════════════════════════
# АЗС модуль v2 — склады, АЗС, топаз транзакции, приходы
# ═══════════════════════════════════════════════════════════════════════


class FuelWarehouse(db.Model):
    """Склад топлива (= организация). Одна организация — один склад."""
    __tablename__ = 'fuel_warehouses'
    id              = db.Column(db.Integer, primary_key=True)
    name            = db.Column(db.String(200), nullable=False)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=True)
    notes           = db.Column(db.Text, default='')
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)
    # [REASON]: FUEL-RESERVE -- "show this warehouse in the fuel module even
    # though it owns no target station". The reserve («Резерв») has no physical
    # station and never will, so fuel_target_warehouse_ids() unions it in by
    # this flag. It HIDES nothing: warehouses visible through their stations
    # stay visible regardless of the flag. See migrate_fuel_reserve_warehouse.py.
    show_in_ui      = db.Column(db.Integer, nullable=False, default=0)

    organization     = db.relationship('Organization')
    stations         = db.relationship('FuelStation2', backref='warehouse',
                                       cascade='all, delete-orphan', lazy='dynamic')
    receipts         = db.relationship('FuelReceipt2', backref='warehouse',
                                       cascade='all, delete-orphan', lazy='dynamic')
    initial_balances = db.relationship('FuelInitialBalance', backref='warehouse',
                                       cascade='all, delete-orphan', lazy='dynamic')


class FuelStation2(db.Model):
    """Конкретная АЗС с topaz_id (ID колонки в базе Топаз)."""
    __tablename__ = 'fuel_stations2'
    id           = db.Column(db.Integer, primary_key=True)
    name         = db.Column(db.String(200), nullable=False)
    topaz_id     = db.Column(db.Integer, unique=True, nullable=False)
    warehouse_id = db.Column(db.Integer, db.ForeignKey('fuel_warehouses.id'), nullable=False)
    is_active         = db.Column(db.Boolean, default=True)
    valid_from        = db.Column(db.Date, nullable=True)
    valid_to          = db.Column(db.Date, nullable=True)
    replacement_of_id = db.Column(db.Integer, nullable=True)
    notes             = db.Column(db.Text, default='')
    created_at        = db.Column(db.DateTime, default=datetime.utcnow)

    transactions = db.relationship('FuelTransaction2', backref='station',
                                   cascade='all, delete-orphan', lazy='dynamic')

    @property
    def warehouse_name(self):
        return self.warehouse.name if self.warehouse else ''


class FuelInitialBalance(db.Model):
    """Начальный остаток топлива по складу (устанавливается вручную)."""
    __tablename__ = 'fuel_initial_balances'
    id           = db.Column(db.Integer, primary_key=True)
    warehouse_id = db.Column(db.Integer, db.ForeignKey('fuel_warehouses.id'), nullable=False)
    fuel_type    = db.Column(db.String(50), default='ДТ')
    quantity     = db.Column(db.Float, default=0)
    balance_date = db.Column(db.Date, nullable=False)
    note         = db.Column(db.Text, default='')
    created_by   = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('warehouse_id', 'fuel_type', name='uq_whse_fuel_type'),
    )


class FuelReceipt2(db.Model):
    """Приход топлива на склад (вводится вручную)."""
    __tablename__ = 'fuel_receipts2'
    id              = db.Column(db.Integer, primary_key=True)
    warehouse_id    = db.Column(db.Integer, db.ForeignKey('fuel_warehouses.id'), nullable=False)
    receipt_date    = db.Column(db.Date, nullable=False)
    fuel_type       = db.Column(db.String(50), default='ДТ')
    quantity        = db.Column(db.Float, nullable=False)
    price_per_liter = db.Column(db.Float, default=0)
    supplier        = db.Column(db.String(200), default='')
    doc_number      = db.Column(db.String(100), default='')
    note            = db.Column(db.Text, default='')
    created_by      = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)


class FuelManualExpense(db.Model):
    """Ручной расход топлива со склада мимо ТРК/Топаз (например, налив в автоцистерну)."""
    # [REASON]: FUEL-MANUAL-EXP -- this model is fitted to a PRE-EXISTING
    # production table (created outside the migration system, already holding
    # live rows the balance report counts). Column names, types and
    # nullability must match PRAGMA table_info(fuel_manual_expenses) exactly;
    # a mismatch will not raise, it will silently produce wrong SQL.
    __tablename__ = 'fuel_manual_expenses'
    id           = db.Column(db.Integer, primary_key=True)
    warehouse_id = db.Column(db.Integer, db.ForeignKey('fuel_warehouses.id'), nullable=False)
    expense_date = db.Column(db.Date, nullable=False)
    fuel_type    = db.Column(db.String(20), nullable=False, default='ДТ')
    quantity     = db.Column(db.Float, nullable=False)
    note         = db.Column(db.Text)
    source       = db.Column(db.String(50), nullable=False, default='manual')
    is_deleted   = db.Column(db.Integer, nullable=False, default=0)
    created_at   = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at   = db.Column(db.DateTime, nullable=True)
    created_by   = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    deleted_by   = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    deleted_at   = db.Column(db.DateTime, nullable=True)

    # [REASON]: no UniqueConstraint on (warehouse_id, expense_date) -- unlike
    # FuelInitialBalance, the same warehouse can legitimately have several
    # manual expenses on one date (e.g. two tanker loads plus a top-up).
    # Plain relationship, no cascade: warehouse deletion must never
    # hard-delete manual expense rows.
    warehouse = db.relationship('FuelWarehouse')


class FuelTransaction2(db.Model):
    """Транзакция из Топаз (выдача топлива). Списывается со склада АЗС."""
    __tablename__ = 'fuel_transactions2'
    id              = db.Column(db.Integer, primary_key=True)
    station_id      = db.Column(db.Integer, db.ForeignKey('fuel_stations2.id'), nullable=False)
    topaz_txn_id    = db.Column(db.String(100), default='')
    topaz_col_id    = db.Column(db.Integer, nullable=True)
    txn_datetime    = db.Column(db.DateTime, nullable=False)
    card_number     = db.Column(db.String(50), default='')
    fuel_type       = db.Column(db.String(50), default='ДТ')
    quantity        = db.Column(db.Float, nullable=False)
    price_per_liter = db.Column(db.Float, default=0)
    amount          = db.Column(db.Float, default=0)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('station_id', 'topaz_txn_id', name='uq_station_txn_id'),
        db.Index('ix_fuel_transactions2_txn_datetime', 'txn_datetime'),
        db.Index('ix_fuel_transactions2_station_datetime', 'station_id', 'txn_datetime'),
    )


class FuelSyncLog2(db.Model):
    """Лог синхронизации с агентом Топаз v2."""
    __tablename__ = 'fuel_sync_logs2'
    id                    = db.Column(db.Integer, primary_key=True)
    synced_at             = db.Column(db.DateTime, default=datetime.utcnow)
    agent_ip              = db.Column(db.String(50), default='')
    transactions_received = db.Column(db.Integer, default=0)
    transactions_new      = db.Column(db.Integer, default=0)
    transactions_dup      = db.Column(db.Integer, default=0)
    unknown_stations      = db.Column(db.Integer, default=0)
    status                = db.Column(db.String(20), default='ok')
    error_msg             = db.Column(db.Text, default='')


class FuelWarningReview(db.Model):
    """Persistent review state for calculated Fuel report warnings."""
    __tablename__ = 'fuel_warning_reviews'
    id              = db.Column(db.Integer, primary_key=True)
    warning_key     = db.Column(db.String(80), unique=True, nullable=False, index=True)
    warning_code    = db.Column(db.String(80), nullable=False, index=True)
    severity        = db.Column(db.String(20), default='warning', index=True)
    entity_type     = db.Column(db.String(80), default='', index=True)
    entity_id       = db.Column(db.Integer, nullable=True, index=True)
    title_snapshot  = db.Column(db.String(500), default='')
    details_snapshot = db.Column(db.Text, default='')
    value_snapshot  = db.Column(db.String(200), default='')
    status          = db.Column(db.String(20), default='new', index=True)
    comment         = db.Column(db.Text, default='')
    first_seen_at   = db.Column(db.DateTime, default=datetime.utcnow)
    last_seen_at    = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_by      = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    updated_at      = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    resolved_at     = db.Column(db.DateTime, nullable=True)

    reviewer        = db.relationship('User', foreign_keys=[updated_by])

    __table_args__ = (
        db.Index('ix_fuel_warning_reviews_status_code', 'status', 'warning_code'),
    )


class FuelTransactionReattribution(db.Model):
    """FUEL-RESERVE: a mark that moves a Topaz transaction's litres from its
    station's warehouse to the reserve («Резерв»), at the REPORT layer only.

    [REASON]: The reserve's diesel sits in Pakhtasanoattrans's common tanks and
    leaves through the same dispenser, so Topaz records it as ordinary
    Pakhtasanoattrans expense. There is no data signal distinguishing a reserve
    transfer (the card-198 hypothesis was refuted against production), so a human
    marks the transactions. Marking must never edit fuel_transactions2: the row
    stays attached to its station, stays visible in /fuel/transactions and keeps
    its original values -- reattribution is applied when expense is computed,
    exactly as FUEL-CARD-CLASS nets out external fuel. Unmarking is a SOFT delete
    (is_deleted=1); a row is never hard-deleted, so the mark/unmark history
    survives. The partial unique index makes a second ACTIVE mark on one
    transaction impossible at the database level while that history remains.
    created_by/deleted_by naming follows FuelInitialBalance and FuelManualExpense.
    """
    __tablename__ = 'fuel_transaction_reattributions'
    id                  = db.Column(db.Integer, primary_key=True)
    transaction_id      = db.Column(db.Integer, db.ForeignKey('fuel_transactions2.id'), nullable=False)
    target_warehouse_id = db.Column(db.Integer, db.ForeignKey('fuel_warehouses.id'), nullable=False)
    # [REASON]: mandatory in the application layer (the mark must say where the
    # fuel went), nullable in the schema so the unmark reason can be appended
    # without a NOT NULL fight and to match migrate_fuel_reserve_warehouse.py.
    note                = db.Column(db.Text, nullable=True)
    created_by          = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at          = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    is_deleted          = db.Column(db.Integer, nullable=False, default=0)
    deleted_by          = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    deleted_at          = db.Column(db.DateTime, nullable=True)

    transaction      = db.relationship('FuelTransaction2')
    target_warehouse = db.relationship('FuelWarehouse')
    creator          = db.relationship('User', foreign_keys=[created_by])
    deleter          = db.relationship('User', foreign_keys=[deleted_by])

    __table_args__ = (
        db.Index('idx_fuel_reattribution_txn', 'transaction_id'),
        db.Index('idx_fuel_reattribution_target_wh', 'target_warehouse_id'),
        # [REASON]: partial UNIQUE -- at most one ACTIVE mark per transaction,
        # enforced by the database itself; soft-deleted history rows for the same
        # transaction remain possible. Mirrors uq_spare_part_reservations_active_item.
        db.Index('uq_fuel_reattribution_active_txn', 'transaction_id',
                 unique=True, sqlite_where=db.text("coalesce(is_deleted, 0) = 0")),
    )



class FuelCounterMismatch(db.Model):
    """FUEL-SHIFT-001: log of card-30 («ПЕРЕЛИВ») rows excluded at ingest.

    [REASON]: card 30 is Topaz's own record of a mismatch between a hose's
    hardware counter and the software total. Excluding it from consumption is
    correct and unchanged (EXCLUDED_CARD_NUMBERS in _perform_fuel_sync); this
    table keeps the SIGNAL that used to vanish, one row per Topaz record, so
    a per-dispenser picture can accumulate. The table is created on existing
    databases by migrate_fuel_counter_mismatch.py; this model matches that
    DDL exactly (no foreign keys there, none here — an unresolvable column id
    must still be recorded with NULL station/warehouse, never dropped).
    Records accumulate from deployment; earlier history was never stored.
    """
    __tablename__ = 'fuel_counter_mismatches'
    id           = db.Column(db.Integer, primary_key=True)
    topaz_txn_id = db.Column(db.Text, nullable=True)
    topaz_col_id = db.Column(db.Integer, nullable=True)
    station_id   = db.Column(db.Integer, nullable=True)
    warehouse_id = db.Column(db.Integer, nullable=True)
    txn_datetime = db.Column(db.DateTime, nullable=True)
    card_number  = db.Column(db.Text, nullable=True)
    quantity     = db.Column(db.Float, nullable=True)
    amount       = db.Column(db.Float, nullable=True)
    created_at   = db.Column(db.DateTime, nullable=True, default=datetime.utcnow)

    __table_args__ = (
        # [REASON]: the agent re-reads a three-day window on every sync; the
        # unique pair makes a repeated sync insert nothing twice.
        db.Index('uq_fuel_counter_mismatch_txn', 'topaz_col_id', 'topaz_txn_id',
                 unique=True),
        db.Index('ix_fuel_counter_mismatches_txn_datetime', 'txn_datetime'),
    )


# ─── Task 3: Module Permissions ───────────────────────────────────────────────

class AppModule(db.Model):
    __tablename__ = 'app_modules'
    id       = db.Column(db.Integer, primary_key=True)
    code     = db.Column(db.String(50), unique=True, nullable=False)
    name_uz  = db.Column(db.String(200), nullable=False)
    name_ru  = db.Column(db.String(200), nullable=False)
    is_active = db.Column(db.Boolean, default=True)


class UserModulePermission(db.Model):
    __tablename__ = 'user_module_permissions'
    id          = db.Column(db.Integer, primary_key=True)
    user_id     = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    module_code = db.Column(db.String(50), nullable=False)
    has_access  = db.Column(db.Boolean, default=True)
    __table_args__ = (db.UniqueConstraint('user_id', 'module_code'),)


# ─── Task P3: Spare Parts ─────────────────────────────────────────────────────

class SparePartCategory(db.Model):
    """SPARE-STAGE1: managed category tree for the spare parts catalog.

    kind='unit' items require photo proof on request submit;
    kind='consumable' items do not. Created by migrate_spare_parts_stage1.py.
    """
    __tablename__ = 'spare_part_categories'
    id          = db.Column(db.Integer, primary_key=True)
    name_ru     = db.Column(db.String(200), nullable=False)
    name_uz     = db.Column(db.String(200), nullable=False)
    parent_id   = db.Column(db.Integer, db.ForeignKey('spare_part_categories.id'), nullable=True)
    kind        = db.Column(db.String(20), nullable=False, default='unit')  # 'unit' | 'consumable'
    is_active   = db.Column(db.Boolean, default=True)
    sort_order  = db.Column(db.Integer, default=0)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    created_by  = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    parent  = db.relationship('SparePartCategory', remote_side=[id], backref='children')
    creator = db.relationship('User', foreign_keys=[created_by])

    __table_args__ = (
        db.Index('idx_spare_part_categories_parent_id', 'parent_id'),
    )


class SparePartUnit(db.Model):
    """SP-F-024: managed bilingual directory of units of measure.

    Owner decision (2026-07-14): units are a STRICT managed directory, not
    free text. `code` is a short stable Latin key stored on request items and
    catalog parts going forward ('dona' matches the value historical rows
    already carry as the default); name_ru/name_uz are editable display
    labels, so relabeling never breaks stored data. Historical unit values
    are snapshots and are NEVER rewritten (see SparePartRequestItem) — the
    directory only governs what the pickers offer going forward. Seeded by
    migrate_spare_parts_units.py (which also auto-registers any extra legacy
    values found in existing data so nothing historical becomes unpickable).
    Follows the SparePartCategory reference-table shape.
    """
    __tablename__ = 'spare_part_units'
    id         = db.Column(db.Integer, primary_key=True)
    code       = db.Column(db.String(30), nullable=False)
    name_ru    = db.Column(db.String(100), nullable=False)
    name_uz    = db.Column(db.String(100), nullable=False)
    is_active  = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.Index('uq_spare_part_units_code', 'code', unique=True),
    )


class SparePart(db.Model):
    __tablename__ = 'spare_parts'
    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(300), nullable=False)
    # [REASON]: CYCLE-2-3 Part 7 — optional Uzbek display alias for the
    # canonical (Russian) name. Nullable by design: translations are filled
    # in over time by the owner through the catalog UI; every display site
    # falls back to `name` when name_uz is empty, so a missing translation
    # can never blank a part name. Added by migrate_spare_parts_name_uz.py.
    name_uz     = db.Column(db.String(300), nullable=True)
    part_number = db.Column(db.String(100), default='')
    unit        = db.Column(db.String(30), default='dona')
    # [REASON]: SPARE-STAGE1 — free-text `category` is deprecated in favour of
    # category_id but kept untouched this stage so existing data/UI stay valid.
    category    = db.Column(db.String(100), default='')
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    # SPARE-STAGE1 additive columns (migrate_spare_parts_stage1.py):
    category_id            = db.Column(db.Integer, db.ForeignKey('spare_part_categories.id'), nullable=True)
    # [REASON]: SPARE-STAGE1 — 'pending_review' marks operator-created candidates
    # a catalog manager must approve or merge before the request can be approved.
    status                 = db.Column(db.String(20), nullable=False, default='active')  # active/pending_review/merged
    merged_into_id         = db.Column(db.Integer, db.ForeignKey('spare_parts.id'), nullable=True)
    created_by             = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    source_request_item_id = db.Column(db.Integer, db.ForeignKey('spare_part_request_items.id'), nullable=True)
    is_active              = db.Column(db.Boolean, default=True)

    category_ref = db.relationship('SparePartCategory', foreign_keys=[category_id])
    merged_into  = db.relationship('SparePart', remote_side=[id], foreign_keys=[merged_into_id])
    creator      = db.relationship('User', foreign_keys=[created_by])
    source_item  = db.relationship('SparePartRequestItem', foreign_keys=[source_request_item_id])


# [REASON]: SPARE-STAGE2 — the fuzzy catalog search fetches the full active
# candidate list on every keystroke-triggered request; this index keeps that
# fetch cheap as the catalog grows. Existing DBs get it via
# migrate_spare_parts_stage2.py.
db.Index('idx_spare_parts_status_active', SparePart.status, SparePart.is_active)


class SparePartSku(db.Model):
    """SPARE-STAGE2: purchasable variant (brand/article/supplier) of a canonical part.

    A SKU always belongs to exactly one canonical spare_parts row. last_price /
    avg_price are informational only and updated ONE WAY by the price-confirm
    workflow (see spare_parts._update_sku_price_stats): SparePartPriceAudit +
    price_status remain the single source of truth for what a request pays.
    Created by migrate_spare_parts_stage2.py.
    """
    __tablename__ = 'spare_part_skus'
    id             = db.Column(db.Integer, primary_key=True)
    spare_part_id  = db.Column(db.Integer, db.ForeignKey('spare_parts.id'), nullable=False)
    brand          = db.Column(db.String(200), default='')
    article_number = db.Column(db.String(100), default='')
    supplier       = db.Column(db.String(200), default='')
    last_price     = db.Column(db.Float, nullable=True)
    avg_price      = db.Column(db.Float, nullable=True)
    is_active      = db.Column(db.Boolean, default=True)
    created_by     = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)

    spare_part = db.relationship('SparePart', foreign_keys=[spare_part_id],
                                 backref=db.backref('skus', lazy='dynamic'))
    creator    = db.relationship('User', foreign_keys=[created_by])

    __table_args__ = (
        db.Index('idx_spare_part_skus_spare_part_id', 'spare_part_id'),
        # [REASON]: SP-F-008 — existing DBs additionally carry the partial
        # expression unique index uq_spare_part_skus_normalized ON
        # (spare_part_id, lower(trim(brand)), lower(trim(article_number)),
        # lower(trim(supplier))) WHERE is_active = 1, created by
        # migrate_spare_parts_sku_uniqueness.py (the source of truth for its
        # definition). SQLAlchemy cannot portably express the lower(trim())
        # + partial-WHERE combination in __table_args__, so it is deliberately
        # NOT mirrored here; sku_save() pre-checks with the same normalization
        # and handles the index's IntegrityError for the race case.
    )

    @property
    def label(self):
        """Human-readable 'brand / article / supplier' line for pickers and acts."""
        fields = [f for f in (self.brand, self.article_number, self.supplier) if f]
        return ' / '.join(fields) if fields else '#{}'.format(self.id)


class SparePartWarehouse(db.Model):
    """SPARE-STAGE2: spare parts warehouse (= organization).

    [REASON]: Confirmed business rule — exactly ONE warehouse per organization
    (not multiple locations, not a shared central warehouse). Mirrors
    FuelWarehouse's organization-scoping pattern, but here organization_id is
    NOT NULL + UNIQUE so the one-per-organization rule is enforced by the
    database, not just application code. Created by migrate_spare_parts_stage2.py.
    """
    __tablename__ = 'spare_part_warehouses'
    id              = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'),
                                nullable=False, unique=True)
    name            = db.Column(db.String(200), nullable=False)
    is_active       = db.Column(db.Boolean, default=True)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)

    organization = db.relationship('Organization')


class SparePartInventory(db.Model):
    """SPARE-STAGE2: current on-hand quantity per (warehouse, SKU).

    [REASON]: Inventory is tracked at the SKU level ONLY (confirmed business
    rule) — a canonical part with no SKU has no trackable stock. Rows are
    created lazily on first movement; quantity must never be written except
    through spare_parts._apply_inventory_movement, which records the matching
    movement row in the same transaction.
    """
    __tablename__ = 'spare_part_inventory'
    id           = db.Column(db.Integer, primary_key=True)
    warehouse_id = db.Column(db.Integer, db.ForeignKey('spare_part_warehouses.id'),
                             nullable=False)
    sku_id       = db.Column(db.Integer, db.ForeignKey('spare_part_skus.id'),
                             nullable=False)
    quantity     = db.Column(db.Float, nullable=False, default=0)
    unit         = db.Column(db.String(30), default='dona')
    updated_at   = db.Column(db.DateTime, default=datetime.utcnow,
                             onupdate=datetime.utcnow)

    warehouse = db.relationship('SparePartWarehouse',
                                backref=db.backref('inventory', lazy='dynamic'))
    sku       = db.relationship('SparePartSku')

    __table_args__ = (
        db.UniqueConstraint('warehouse_id', 'sku_id',
                            name='uq_spare_part_inventory_wh_sku'),
    )


class SparePartInventoryMovement(db.Model):
    """SPARE-STAGE2: append-only audit trail of every stock change.

    quantity is SIGNED (positive receipts, negative issues/write-offs);
    balance_after snapshots the resulting on-hand quantity at movement time so
    the trail stays historically correct even if later movements are inserted
    out of chronological order. Summing a (warehouse, sku) pair's movement
    quantities must always equal its spare_part_inventory.quantity.
    """
    __tablename__ = 'spare_part_inventory_movements'
    id             = db.Column(db.Integer, primary_key=True)
    warehouse_id   = db.Column(db.Integer, db.ForeignKey('spare_part_warehouses.id'),
                               nullable=False)
    sku_id         = db.Column(db.Integer, db.ForeignKey('spare_part_skus.id'),
                               nullable=False)
    movement_type  = db.Column(db.String(20), nullable=False)  # receipt/issue/adjustment/write_off
    quantity       = db.Column(db.Float, nullable=False)       # signed
    balance_after  = db.Column(db.Float, nullable=False)
    reference_type = db.Column(db.String(30), nullable=False, default='manual')  # request_item/manual/import
    reference_id   = db.Column(db.Integer, nullable=True)
    note           = db.Column(db.Text, nullable=False, default='')
    created_by     = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)

    warehouse = db.relationship('SparePartWarehouse')
    sku       = db.relationship('SparePartSku')
    creator   = db.relationship('User', foreign_keys=[created_by])

    __table_args__ = (
        db.Index('idx_spare_part_inv_movements_wh_sku', 'warehouse_id', 'sku_id'),
        db.Index('idx_spare_part_inv_movements_created_at', 'created_at'),
        db.Index('idx_spare_part_inv_movements_reference',
                 'reference_type', 'reference_id'),
    )


class SparePartReservation(db.Model):
    """SP-RESERVE-003: a claim on warehouse stock held by an approved request item.

    [REASON]: A reservation is NOT a stock movement — approving a request does
    not change spare_part_inventory.quantity, only the claim on it. That is why
    these rows live in their own table and are never written through
    spare_parts._apply_inventory_movement: the module's core invariant (a
    pair's movements always sum to its on-hand quantity) stays intact and
    unambiguous, and "available" is always derived as on-hand minus the sum of
    active reservations.

    [REASON]: One row per request ITEM with a non-null sku_id, ALWAYS created
    at approval time — even when nothing could be reserved (quantity = 0). The
    row snapshots both requested_quantity and the actually granted quantity, so
    a shortage is recorded explicitly at the moment of the approval decision
    (feeding the future purchase-queue increment) and the desk counters can be
    a single SQL EXISTS over these rows instead of a Python loop.

    [REASON]: Reservations are advisory, not enforcement. Two near-simultaneous
    approvals could in theory over-reserve a SKU; the atomic conditional UPDATE
    inside _apply_inventory_movement remains the real guard against negative
    stock at issue time. This app runs a single Waitress instance with a
    handful of users and already accepts the same class of race for act
    numbering, so no locking/retry machinery is added here.

    Lifecycle: active -> consumed (request issued) | released (manual release
    from the warehouse screen — 'approved' has no other exit for an abandoned
    request). The partial unique index makes a second ACTIVE row for the same
    item impossible at the database level.
    """
    __tablename__ = 'spare_part_reservations'
    id                 = db.Column(db.Integer, primary_key=True)
    request_id         = db.Column(db.Integer, db.ForeignKey('spare_part_requests.id'), nullable=False)
    request_item_id    = db.Column(db.Integer, db.ForeignKey('spare_part_request_items.id'), nullable=False)
    warehouse_id       = db.Column(db.Integer, db.ForeignKey('spare_part_warehouses.id'), nullable=False)
    sku_id             = db.Column(db.Integer, db.ForeignKey('spare_part_skus.id'), nullable=False)
    quantity           = db.Column(db.Float, nullable=False, default=0)   # actually reserved, >= 0
    requested_quantity = db.Column(db.Float, nullable=False, default=0)   # snapshot of item.quantity
    status             = db.Column(db.String(20), nullable=False, default='active')  # active/consumed/released
    created_at         = db.Column(db.DateTime, default=datetime.utcnow)
    created_by         = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    closed_at          = db.Column(db.DateTime, nullable=True)
    closed_by          = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    close_note         = db.Column(db.String(300), nullable=False, default='')

    request   = db.relationship('SparePartRequest',
                                backref=db.backref('reservations', lazy='select'))
    item      = db.relationship('SparePartRequestItem', foreign_keys=[request_item_id])
    warehouse = db.relationship('SparePartWarehouse')
    sku       = db.relationship('SparePartSku')
    creator   = db.relationship('User', foreign_keys=[created_by])
    closer    = db.relationship('User', foreign_keys=[closed_by])

    __table_args__ = (
        db.Index('idx_spare_part_reservations_wh_sku_status',
                 'warehouse_id', 'sku_id', 'status'),
        db.Index('idx_spare_part_reservations_request_id', 'request_id'),
        # [REASON]: partial UNIQUE — at most one ACTIVE reservation per request
        # item, enforced by the database itself; consumed/released history rows
        # for the same item remain possible.
        db.Index('uq_spare_part_reservations_active_item', 'request_item_id',
                 unique=True, sqlite_where=db.text("status = 'active'")),
    )


class SparePartMinLevel(db.Model):
    """SP-MINSTOCK-004: minimum (safety) stock level per (warehouse, SKU).

    [REASON]: Minimums live in their OWN table, not as a column on
    spare_part_inventory: an inventory row is created lazily on the first
    movement (see SparePartInventory), so a minimum for a SKU that has never
    been received at the warehouse would have nowhere to live. min_quantity=0
    is never stored — clearing a minimum DELETES the row
    (spare_parts.inventory_min_level_save), so "no row" and "no minimum" are
    the same thing and the purchase queue never scans zero-level noise.
    """
    __tablename__ = 'spare_part_min_levels'
    id           = db.Column(db.Integer, primary_key=True)
    warehouse_id = db.Column(db.Integer, db.ForeignKey('spare_part_warehouses.id'), nullable=False)
    sku_id       = db.Column(db.Integer, db.ForeignKey('spare_part_skus.id'), nullable=False)
    min_quantity = db.Column(db.Float, nullable=False, default=0)
    note         = db.Column(db.String(300), nullable=False, default='')
    updated_at   = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by   = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    warehouse = db.relationship('SparePartWarehouse')
    sku       = db.relationship('SparePartSku')
    editor    = db.relationship('User', foreign_keys=[updated_by])

    __table_args__ = (
        db.UniqueConstraint('warehouse_id', 'sku_id', name='uq_spare_part_min_levels_wh_sku'),
    )


class SparePartWriteOffAct(db.Model):
    """SPARE-STAGE2: write-off act generated when an approved request is issued.

    act_number format SPW-{YEAR}-{5-digit-seq}: MAX+1 inside the issue
    transaction, the exact technique proven by work_orders._generate_wo_number
    (act_number is UNIQUE, so a rare race raises IntegrityError on commit).
    Once created, acts are permanent — there is deliberately no un-issue.
    """
    __tablename__ = 'spare_part_write_off_acts'
    id              = db.Column(db.Integer, primary_key=True)
    act_number      = db.Column(db.String(20), unique=True, nullable=False)
    request_id      = db.Column(db.Integer, db.ForeignKey('spare_part_requests.id'),
                                nullable=False)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'),
                                nullable=False)
    # [REASON]: nullable — a request whose items all lack a SKU can be issued
    # even when its organization has no warehouse (nothing trackable to deduct).
    warehouse_id    = db.Column(db.Integer, db.ForeignKey('spare_part_warehouses.id'),
                                nullable=True)
    issued_date     = db.Column(db.Date, nullable=False)
    issued_by       = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    pdf_path        = db.Column(db.String(500), default='')
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)

    request      = db.relationship('SparePartRequest',
                                   backref=db.backref('write_off_acts', lazy='select'))
    organization = db.relationship('Organization')
    warehouse    = db.relationship('SparePartWarehouse')
    issuer       = db.relationship('User', foreign_keys=[issued_by])
    items        = db.relationship('SparePartWriteOffActItem', backref='act',
                                   cascade='all, delete-orphan')

    __table_args__ = (
        # [REASON]: SP-F-019 — UNIQUE: 'issued' is terminal and unrepeatable,
        # so a request can never legitimately have two acts; a concurrent
        # double-issue now fails with IntegrityError on commit (handled by
        # issue_request's existing rollback path) instead of silently creating
        # a second act. Existing DBs get the unique index via
        # migrate_spare_parts_acts_permission.py (duplicate-free confirmed,
        # DQ-022: 0 on staging and production).
        db.Index('idx_spare_part_write_off_acts_request_id', 'request_id',
                 unique=True),
    )


class SparePartWriteOffActItem(db.Model):
    """SPARE-STAGE2: one act line per request item, SNAPSHOTTED at issue time.

    [REASON]: name/sku_label/quantity/unit/price/total are copied, not joined
    back to the live request/catalog later, so the act stays historically
    accurate even if catalog or SKU data changes afterwards.
    """
    __tablename__ = 'spare_part_write_off_act_items'
    id              = db.Column(db.Integer, primary_key=True)
    act_id          = db.Column(db.Integer,
                                db.ForeignKey('spare_part_write_off_acts.id'),
                                nullable=False)
    request_item_id = db.Column(db.Integer,
                                db.ForeignKey('spare_part_request_items.id'),
                                nullable=True)
    name            = db.Column(db.String(300), nullable=False)
    sku_label       = db.Column(db.String(500), default='')
    quantity        = db.Column(db.Float, nullable=False)
    unit            = db.Column(db.String(30), default='dona')
    price           = db.Column(db.Float, nullable=True)
    total           = db.Column(db.Float, nullable=True)

    # [REASON]: CYCLE-2-3 Part 7 — read-only link back to the live request
    # item so display-time rendering (act PDF) can resolve the catalog
    # part's Uzbek alias. The snapshotted columns above remain the stored
    # record; this relationship is never used to rewrite them.
    request_item = db.relationship('SparePartRequestItem',
                                   foreign_keys=[request_item_id])

    __table_args__ = (
        db.Index('idx_spare_part_write_off_act_items_act_id', 'act_id'),
    )


class SparePartCompatibility(db.Model):
    """SPARE-STAGE3: which equipment models a catalog part is compatible with.

    [REASON]: SPARE-STAGE3 business rule — ABSENCE of any row for a given
    spare_part_id means "compatibility not yet defined", NOT "incompatible with
    everything". Warning rule 5 must stay completely silent for parts that have
    zero compatibility rows (the matrix starts empty on deploy day; treating
    empty as incompatible would red-flag nearly every request and be useless
    noise). See spare_parts._check_rule5_incompatibility.
    """
    __tablename__ = 'spare_part_compatibility'
    id                 = db.Column(db.Integer, primary_key=True)
    spare_part_id      = db.Column(db.Integer, db.ForeignKey('spare_parts.id'), nullable=False)
    equipment_model_id = db.Column(db.Integer, db.ForeignKey('equipment_models.id'), nullable=False)
    created_by         = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at         = db.Column(db.DateTime, default=datetime.utcnow)

    spare_part      = db.relationship('SparePart', foreign_keys=[spare_part_id],
                                      backref=db.backref('compatibilities', lazy='dynamic'))
    equipment_model = db.relationship('EquipmentModel', foreign_keys=[equipment_model_id])
    creator         = db.relationship('User', foreign_keys=[created_by])

    __table_args__ = (
        db.UniqueConstraint('spare_part_id', 'equipment_model_id',
                            name='uq_spare_part_compatibility_part_model'),
        db.Index('idx_spare_part_compatibility_part', 'spare_part_id'),
        db.Index('idx_spare_part_compatibility_model', 'equipment_model_id'),
    )


class SparePartMaintenanceNorm(db.Model):
    """SPARE-STAGE3: engine-hours replacement interval for a part.

    [REASON]: SPARE-STAGE3 — passive maintenance notification only (the owner
    deliberately deferred any auto-created draft request to a later stage).
    equipment_model_id NULL means the norm applies to every model. The
    notification fires when accumulated engine-hours since the last recorded
    replacement of this part on a given machine reach interval_hours; see
    spare_parts._maintenance_due_rows for how "hours since last replacement" is
    derived from the per-day Wialon EngineHoursRecord data.
    """
    __tablename__ = 'spare_part_maintenance_norms'
    id                 = db.Column(db.Integer, primary_key=True)
    spare_part_id      = db.Column(db.Integer, db.ForeignKey('spare_parts.id'), nullable=False)
    equipment_model_id = db.Column(db.Integer, db.ForeignKey('equipment_models.id'), nullable=True)
    interval_hours     = db.Column(db.Float, nullable=False)
    is_active          = db.Column(db.Boolean, default=True)
    created_by         = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at         = db.Column(db.DateTime, default=datetime.utcnow)

    spare_part      = db.relationship('SparePart', foreign_keys=[spare_part_id],
                                      backref=db.backref('maintenance_norms', lazy='dynamic'))
    equipment_model = db.relationship('EquipmentModel', foreign_keys=[equipment_model_id])
    creator         = db.relationship('User', foreign_keys=[created_by])

    __table_args__ = (
        db.Index('idx_spare_part_maintenance_norms_part', 'spare_part_id'),
    )


class SparePartRequest(db.Model):
    __tablename__ = 'spare_part_requests'
    id              = db.Column(db.Integer, primary_key=True)
    request_date    = db.Column(db.Date, nullable=False)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False)
    equipment_id    = db.Column(db.Integer, db.ForeignKey('equipment.id'), nullable=True)
    status          = db.Column(db.String(20), default='draft')
    note            = db.Column(db.Text, default='')
    created_by      = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)
    reviewed_by     = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    reviewed_at     = db.Column(db.DateTime, nullable=True)
    review_comment  = db.Column(db.Text, default='')
    organization    = db.relationship('Organization')
    equipment       = db.relationship('Equipment', foreign_keys=[equipment_id])
    creator         = db.relationship('User', foreign_keys=[created_by])
    reviewer        = db.relationship('User', foreign_keys=[reviewed_by])
    items           = db.relationship('SparePartRequestItem', backref='request',
                                      cascade='all, delete-orphan')


class SparePartRequestItem(db.Model):
    __tablename__ = 'spare_part_request_items'
    id            = db.Column(db.Integer, primary_key=True)
    request_id    = db.Column(db.Integer, db.ForeignKey('spare_part_requests.id'), nullable=False)
    spare_part_id = db.Column(db.Integer, db.ForeignKey('spare_parts.id'), nullable=True)
    name          = db.Column(db.String(300), nullable=False)
    part_number   = db.Column(db.String(100), default='')
    quantity      = db.Column(db.Float, nullable=False, default=1)
    unit          = db.Column(db.String(30), default='dona')
    note          = db.Column(db.String(300), default='')

    # SPARE-STAGE1 additive columns (migrate_spare_parts_stage1.py):
    # [REASON]: SPARE-STAGE1 — manual price entry with mandatory audit trail;
    # a request cannot be approved until every item's price_status == 'confirmed'.
    price         = db.Column(db.Float, nullable=True)
    price_status  = db.Column(db.String(20), nullable=False, default='pending')  # pending/confirmed/rejected/returned
    price_set_by  = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    price_set_at  = db.Column(db.DateTime, nullable=True)

    # SPARE-STAGE2 additive column (migrate_spare_parts_stage2.py):
    # [REASON]: SPARE-STAGE2 — optional concrete SKU choice; NULL keeps the
    # exact Stage 1 behaviour (free canonical part, no inventory tracking).
    sku_id        = db.Column(db.Integer, db.ForeignKey('spare_part_skus.id'), nullable=True)

    spare_part    = db.relationship('SparePart', foreign_keys=[spare_part_id])
    price_setter  = db.relationship('User', foreign_keys=[price_set_by])
    sku           = db.relationship('SparePartSku', foreign_keys=[sku_id])


# [REASON]: SPARE-STAGE1 — performance indexes for the repeat-order warning
# engine, which scans prior items by spare_part_id and equipment_id + date.
db.Index('idx_spare_part_request_items_spare_part_id', SparePartRequestItem.spare_part_id)
db.Index('idx_spare_part_requests_equipment_id_date', SparePartRequest.equipment_id, SparePartRequest.request_date)
# [REASON]: SPARE-STAGE2 — the SKU price-stats recompute scans confirm audit
# rows of all items referencing one SKU.
db.Index('idx_spare_part_request_items_sku_id', SparePartRequestItem.sku_id)
# [REASON]: SP-RESERVE-003 — the desk's coverage EXISTS correlates items by
# request_id; without this index SQLite full-scans the items table once per
# approved request (measured ~50 ms vs ~1.4 ms per counter at staging scale).
db.Index('idx_spare_part_request_items_request_id', SparePartRequestItem.request_id)


class SparePartPriceAudit(db.Model):
    """SPARE-STAGE1: audit trail for every price action on a request item.

    Written through a plain helper taking explicit arguments (see
    spare_parts.write_price_audit) so a non-Flask process (Telegram bot)
    can reuse it without a request context.
    """
    __tablename__ = 'spare_part_price_audit'
    id         = db.Column(db.Integer, primary_key=True)
    item_id    = db.Column(db.Integer, db.ForeignKey('spare_part_request_items.id'), nullable=False)
    old_price  = db.Column(db.Float, nullable=True)
    new_price  = db.Column(db.Float, nullable=True)
    action     = db.Column(db.String(20), nullable=False, default='set')  # set/confirm/reject/return
    changed_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    changed_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    item = db.relationship('SparePartRequestItem', foreign_keys=[item_id])
    user = db.relationship('User', foreign_keys=[changed_by])

    __table_args__ = (
        db.Index('idx_spare_part_price_audit_item_id', 'item_id'),
    )


class SparePartAttachment(db.Model):
    """SPARE-STAGE1: photo attachments for request items.

    file_path stores only the on-disk filename built from item_id + a random
    suffix (never original_filename), so any process that knows UPLOAD_FOLDER
    can resolve it without Flask context.
    """
    __tablename__ = 'spare_part_attachments'
    id                = db.Column(db.Integer, primary_key=True)
    item_id           = db.Column(db.Integer, db.ForeignKey('spare_part_request_items.id'), nullable=False)
    file_path         = db.Column(db.String(500), nullable=False)
    original_filename = db.Column(db.String(300), default='')
    file_size         = db.Column(db.Integer, default=0)
    uploaded_by       = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    uploaded_at       = db.Column(db.DateTime, default=datetime.utcnow)

    item     = db.relationship('SparePartRequestItem', foreign_keys=[item_id],
                               backref='attachments')
    uploader = db.relationship('User', foreign_keys=[uploaded_by])

    __table_args__ = (
        db.Index('idx_spare_part_attachments_item_id', 'item_id'),
    )


# ─── BOT001: Spare Part Status History ───────────────────────────────────────

class SparePartStatusHistory(db.Model):
    """Audit trail of spare part request status changes.

    Created by migrate_bot001_telegram_foundation.py (BOT001).
    Populated in BOT002/BOT003 when status transitions are implemented.
    """
    __tablename__ = 'spare_part_status_history'
    id         = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer, db.ForeignKey('spare_part_requests.id'), nullable=False)
    old_status = db.Column(db.String(30), nullable=True)
    new_status = db.Column(db.String(30), nullable=False)
    comment    = db.Column(db.Text, nullable=False, default='')
    changed_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    changed_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        db.Index('idx_spare_part_status_history_request_id', 'request_id'),
    )


# ─── BOT001: Bot API Sessions ─────────────────────────────────────────────────

class BotApiSession(db.Model):
    """Long-lived API session tokens for the Telegram bot.

    The raw token is never stored -- only its SHA-256 hash.
    Created by migrate_bot001_telegram_foundation.py (BOT001).
    """
    __tablename__ = 'bot_api_sessions'
    id           = db.Column(db.Integer, primary_key=True)
    user_id      = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    telegram_id  = db.Column(db.Integer, nullable=False)
    token_hash   = db.Column(db.String(128), nullable=False, unique=True)
    expires_at   = db.Column(db.DateTime, nullable=False)
    created_at   = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    last_used_at = db.Column(db.DateTime, nullable=True)
    revoked_at   = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.Index('idx_bot_api_sessions_token_hash', 'token_hash'),
        db.Index('idx_bot_api_sessions_user_id', 'user_id'),
    )

# ─── FUEL-REPORT-012H-C: Card Directory ───────────────────────────────────────

class FuelCard(db.Model):
    """Card directory entry from Topaz dcCards.

    Stores card display names and metadata so fuel reports can show
    human-readable card/vehicle names instead of raw card numbers or RFID values.
    """
    __tablename__ = 'fuel_cards'
    id                  = db.Column(db.Integer, primary_key=True)
    topaz_card_id       = db.Column(db.String(100), unique=True, nullable=True)
    display_name        = db.Column(db.String(300), nullable=False)
    rfid_code           = db.Column(db.String(150), nullable=True)
    partner_id          = db.Column(db.String(100), nullable=True)
    enabled             = db.Column(db.Boolean, default=True)
    car_number          = db.Column(db.String(100), nullable=True)
    car_model           = db.Column(db.String(200), nullable=True)
    topaz_transaction_id = db.Column(db.String(100), nullable=True)
    source              = db.Column(db.String(100), default='topaz_dcCards')
    first_seen          = db.Column(db.DateTime, nullable=True)
    last_seen           = db.Column(db.DateTime, nullable=True)
    created_at          = db.Column(db.DateTime, nullable=True)
    updated_at          = db.Column(db.DateTime, nullable=True)
    notes               = db.Column(db.Text, default='')

    aliases = db.relationship('FuelCardAlias', backref='card',
                               cascade='all, delete-orphan', lazy='dynamic')

    __table_args__ = (
        db.Index('ix_fuel_cards_topaz_card_id', 'topaz_card_id'),
        db.Index('ix_fuel_cards_display_name', 'display_name'),
        db.Index('ix_fuel_cards_rfid_code', 'rfid_code'),
        db.Index('ix_fuel_cards_enabled', 'enabled'),
    )


class FuelCardAlias(db.Model):
    """Alias mapping from a card_number or RFID value to a FuelCard."""
    __tablename__ = 'fuel_card_aliases'
    id          = db.Column(db.Integer, primary_key=True)
    card_id     = db.Column(db.Integer, db.ForeignKey('fuel_cards.id'), nullable=False)
    alias_type  = db.Column(db.String(30), nullable=False)
    alias_value = db.Column(db.String(150), nullable=False)
    source      = db.Column(db.String(100), default='topaz_dcCards')
    first_seen  = db.Column(db.DateTime, nullable=True)
    last_seen   = db.Column(db.DateTime, nullable=True)
    created_at  = db.Column(db.DateTime, nullable=True)
    updated_at  = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.UniqueConstraint('alias_type', 'alias_value', name='uq_card_alias_type_value'),
        db.Index('ix_fuel_card_aliases_card_id', 'card_id'),
        db.Index('ix_fuel_card_aliases_alias_value', 'alias_value'),
    )


class FuelCardSyncLog(db.Model):
    """Log of card directory sync attempts from Topaz agent."""
    __tablename__ = 'fuel_card_sync_logs'
    id                = db.Column(db.Integer, primary_key=True)
    synced_at         = db.Column(db.DateTime, nullable=True)
    source            = db.Column(db.String(100), nullable=True)
    rows_received     = db.Column(db.Integer, default=0)
    cards_created     = db.Column(db.Integer, default=0)
    cards_updated     = db.Column(db.Integer, default=0)
    aliases_created   = db.Column(db.Integer, default=0)
    aliases_updated   = db.Column(db.Integer, default=0)
    aliases_conflicted = db.Column(db.Integer, default=0)
    rows_skipped      = db.Column(db.Integer, default=0)
    status            = db.Column(db.String(30), nullable=True)
    message           = db.Column(db.Text, default='')


# ─── BOT001: Bot Notification Queue ───────────────────────────────────────────

class BotNotificationQueue(db.Model):
    """Outgoing Telegram notification queue.

    Populated in future BOT002/BOT003 patches.
    Created by migrate_bot001_telegram_foundation.py (BOT001).
    """
    __tablename__ = 'bot_notification_queue'
    id           = db.Column(db.Integer, primary_key=True)
    telegram_id  = db.Column(db.Integer, nullable=False)
    user_id      = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    request_id   = db.Column(db.Integer, db.ForeignKey('spare_part_requests.id'), nullable=True)
    event_type   = db.Column(db.String(80), nullable=False)
    payload_json = db.Column(db.Text, nullable=False, default='{}')
    status       = db.Column(db.String(30), nullable=False, default='pending')
    attempts     = db.Column(db.Integer, nullable=False, default=0)
    last_error   = db.Column(db.Text, nullable=False, default='')
    created_at   = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    sent_at      = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.Index('idx_bot_notification_queue_status', 'status'),
        db.Index('idx_bot_notification_queue_telegram_id', 'telegram_id'),
    )


# ─── PHASE1 shared foundation: field contours ────────────────────────────────

class FieldContour(db.Model):
    """Shared directory of field contours for the GPS and drone tracks.

    [REASON]: PHASE1 -- two tracks were about to build the same directory:
    the GPS track mirrors 17 812 Wialon geozones, the drone track collects
    4 776 DJI Field Management records. One table with a source
    discriminator prevents two versions of the same data. Created EMPTY by
    migrate_core_foundation_001.py (db.create_all() covers fresh installs);
    nothing populates or reads it yet -- the collectors ship in later tasks.
    """
    __tablename__ = 'field_contours'
    id          = db.Column(db.Integer, primary_key=True)
    # [REASON]: source is one of 'wialon' / 'dji' / 'manual'; together with
    # external_id it forms the natural key. external_id is nullable because
    # a manually created contour has no external system id (SQLite treats
    # NULLs as distinct inside a unique constraint, so many manual rows are
    # allowed by design).
    source      = db.Column(db.String(20), nullable=False)
    external_id = db.Column(db.String(100), nullable=True)
    name        = db.Column(db.String(300), nullable=False)
    name_uz     = db.Column(db.String(300), nullable=True)
    # [REASON]: geometry as GeoJSON text -- SQLite has no native geometry
    # type and no consumer needs spatial queries yet; text keeps the value
    # portable between Wialon polygons and DJI field boundaries.
    geometry_geojson = db.Column(db.Text, nullable=True)
    area_ha     = db.Column(db.Float, nullable=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id'),
                            nullable=True, index=True)
    season_year = db.Column(db.Integer, nullable=True)
    is_active   = db.Column(db.Boolean, default=True)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at  = db.Column(db.DateTime, default=datetime.utcnow,
                            onupdate=datetime.utcnow)

    customer = db.relationship(
        'Customer', backref=db.backref('field_contours', lazy='dynamic'))

    __table_args__ = (
        db.UniqueConstraint('source', 'external_id',
                            name='uq_field_contours_source_external'),
        db.Index('ix_field_contours_source_active', 'source', 'is_active'),
    )


# ─── DRONE-001: drones module data foundation ────────────────────────────────

class DroneUnit(db.Model):
    """One physical DJI Agras machine, identified by its number (1..15).

    [REASON]: DRONE-001 / decision R3 of the drones track -- drones are NOT
    rows of `equipment`: they have no plate and no equipment category, fly up
    to 476 times a day against the one-record-per-day model of daily_records,
    and work_orders.equipment_id is NOT NULL. Identity is the machine NUMBER;
    the DJI nickname is a mutable label resolved via DroneNickname.
    """
    __tablename__ = 'drone_units'
    id              = db.Column(db.Integer, primary_key=True)
    number          = db.Column(db.Integer, nullable=False, unique=True)
    # [REASON]: decision R4 -- all 15 machines belong to organization 12
    # (Agrocluster); revenue attribution below organization level does not
    # exist until DRONE-005 orders.
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'),
                                nullable=False)
    model           = db.Column(db.String(50), nullable=True)
    # [REASON]: exclusion from reporting is DATED, not a constant flag:
    # flights with started_at >= excluded_from are omitted from reports when
    # is_excluded is true; earlier flights still count. A machine that was a
    # test unit and later went to work ("9 Garden") must not retroactively
    # lose its real hectares.
    is_excluded     = db.Column(db.Boolean, default=False)
    excluded_from   = db.Column(db.Date, nullable=True)
    excluded_reason = db.Column(db.String(300), nullable=True)
    notes           = db.Column(db.Text, nullable=True)
    is_active       = db.Column(db.Boolean, default=True)
    # ─── DRONE-FLEET-001 additive columns ────────────────────────────────
    # [REASON]: the airframe identifier is hardware_id and NEVER
    # serial_number. In this module serial_number already means the
    # per-flight record id of the DJI payload -- 22 855 distinct values on
    # 22 855 flights (see DroneFlight below, and the trap is recorded in
    # CLAUDE.md). Reusing that name for the airframe would make every query
    # in the module ambiguous to the next reader.
    hardware_id      = db.Column(db.String(50), nullable=True)
    generator_id     = db.Column(db.String(50), nullable=True)
    # [REASON]: DESCRIPTIVE ONLY -- never a key of revenue or attribution.
    # Invariant R4 of the track stands: all fifteen machines belong to
    # organization_id = 12, reconfirmed by the owner on 2026-08-03 after he
    # was shown that the dispatchers split them across seven subdivisions in
    # their own sheets. The column exists so a screen can say where a machine
    # is stationed; summing anything by it would resurrect the fictional
    # sub-organisations the track already rejected.
    subdivision_name = db.Column(db.String(120), nullable=True)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at      = db.Column(db.DateTime, default=datetime.utcnow,
                                onupdate=datetime.utcnow)

    organization = db.relationship(
        'Organization', backref=db.backref('drone_units', lazy='dynamic'))


class DroneNickname(db.Model):
    """Alias map: one DJI nickname spelling -> one machine.

    [REASON]: DRONE-001 -- a hardcoded nickname dictionary in the old system
    knew 16 of 20 raw spellings and silently dropped 2 036 flights (2 082 ha)
    into an unattributed bucket. The alias map therefore lives in the
    database and is extensible at runtime; an unknown nickname must never
    reject a flight (it is stored unresolved instead, see DroneFlight).

    Resolution order (used by the ingest): exact match on `nickname`; on a
    miss, match on `normalized`; if the normalized match returns rows
    pointing at more than one drone_unit_id, treat the nickname as
    UNRESOLVED rather than guessing.
    """
    __tablename__ = 'drone_nicknames'
    id            = db.Column(db.Integer, primary_key=True)
    drone_unit_id = db.Column(db.Integer, db.ForeignKey('drone_units.id'),
                              nullable=False, index=True)
    # Exactly as DJI sends it, including script and spacing.
    nickname      = db.Column(db.String(100), nullable=False, unique=True)
    # [REASON]: normalized (lowercase, all whitespace removed) is deliberately
    # NOT unique: '8 Garden U' and '8 GardenU' both normalise to '8gardenu'.
    normalized    = db.Column(db.String(100), nullable=False, index=True)
    first_seen_at = db.Column(db.DateTime, nullable=True)
    last_seen_at  = db.Column(db.DateTime, nullable=True)
    is_active     = db.Column(db.Boolean, default=True)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)

    drone_unit = db.relationship(
        'DroneUnit', backref=db.backref('nicknames', lazy='dynamic'))


class DroneSyncLog(db.Model):
    """One ingestion run of the drones endpoint (backfill/incremental/replay).

    [REASON]: DRONE-001 -- the old collector always reported
    records_written == records_seen (verified: all 520 rows of its sync_runs),
    so the log could not answer whether new data had arrived. These five
    counters must never be collapsed into two.

    Counter relations (DRONE-002 QA): records_seen = records_new +
    records_duplicate + records_error, and records_unresolved is a SUBSET
    of records_new (unresolved <= new) -- an unattributed flight is still
    a stored flight, and it becomes attributed later without the log
    changing. A rolled-back batch records new = duplicate = unresolved = 0
    and errors = seen: the log must never claim rows that are not in the
    table.
    """
    __tablename__ = 'drone_sync_logs'
    id                 = db.Column(db.Integer, primary_key=True)
    kind               = db.Column(db.String(20), nullable=False)  # backfill/incremental/replay
    period_from        = db.Column(db.Date, nullable=True)
    period_to          = db.Column(db.Date, nullable=True)
    started_at         = db.Column(db.DateTime, nullable=False,
                                   default=datetime.utcnow)
    finished_at        = db.Column(db.DateTime, nullable=True)
    status             = db.Column(db.String(20), nullable=False,
                                   default='running')  # running/ok/error
    records_seen       = db.Column(db.Integer, default=0)
    records_new        = db.Column(db.Integer, default=0)
    records_duplicate  = db.Column(db.Integer, default=0)
    records_unresolved = db.Column(db.Integer, default=0)
    records_error      = db.Column(db.Integer, default=0)
    source_ip          = db.Column(db.String(45), nullable=True)
    error_text         = db.Column(db.Text, nullable=True)


class DroneFlight(db.Model):
    """One DJI flight (= one tank load, not a shift; ~0.98 ha on average).

    [REASON]: DRONE-001 -- dji_flight_id (payload `id`) is the dedup key;
    serial_number is a PER-FLIGHT identifier in the DJI payload, not a
    machine identifier, and is kept only for traceability. drone_unit_id is
    nullable ON PURPOSE: an unknown nickname must never reject a flight --
    the row is stored with drone_unit_id NULL and nickname_raw filled, and
    can be re-attributed once the alias map learns the spelling. raw_json
    always stores the complete payload, so every derived column (including
    `region`) can be recomputed if a rule changes.
    """
    __tablename__ = 'drone_flights'
    id            = db.Column(db.Integer, primary_key=True)
    dji_flight_id = db.Column(db.BigInteger, nullable=False, unique=True)
    drone_unit_id = db.Column(db.Integer, db.ForeignKey('drone_units.id'),
                              nullable=True)
    nickname_raw  = db.Column(db.String(100), nullable=True)
    serial_number = db.Column(db.String(50), nullable=True)
    flyer_name    = db.Column(db.String(100), nullable=True)
    team_name     = db.Column(db.String(100), nullable=True)
    # UTC, from payload start_timestamp / end_timestamp (unix seconds).
    # Display is UTC+5 -- conversion happens at render time, never here.
    started_at    = db.Column(db.DateTime, nullable=False, index=True)
    finished_at   = db.Column(db.DateTime, nullable=True)
    work_seconds  = db.Column(db.Integer, nullable=True)
    # Normalised units: new_work_area / 10000 (m2 -> ha),
    # spray_usage / 1000 (ml -> l), sow_usage / 1000 (GRAMS -> kg).
    area_ha       = db.Column(db.Float, nullable=False, default=0)
    spray_liters  = db.Column(db.Float, nullable=True)
    sow_kg        = db.Column(db.Float, nullable=True)
    usage_type    = db.Column(db.Integer, nullable=True)  # 0 spray, 1 sow
    # [REASON]: mode_name semantics are unknown (values 0/1/4 observed) --
    # stored, never interpreted.
    mode_name     = db.Column(db.Integer, nullable=True)
    manual_mode   = db.Column(db.Boolean, nullable=True)
    work_speed    = db.Column(db.Float, nullable=True)
    spray_width   = db.Column(db.Float, nullable=True)
    radar_height  = db.Column(db.Float, nullable=True)
    lat           = db.Column(db.Float, nullable=True)
    lng           = db.Column(db.Float, nullable=True)
    location_text = db.Column(db.String(500), nullable=True)
    # [REASON]: region is computed AT INGEST and stored, so reports never
    # re-parse tens of thousands of address strings; location_text is kept
    # verbatim so the whole column can be recomputed if the rule changes.
    region        = db.Column(db.String(100), nullable=True, index=True)
    raw_json      = db.Column(db.Text, nullable=False)
    sync_log_id   = db.Column(db.Integer, db.ForeignKey('drone_sync_logs.id'),
                              nullable=True)
    ingested_at   = db.Column(db.DateTime, nullable=False,
                              default=datetime.utcnow)

    drone_unit = db.relationship(
        'DroneUnit', backref=db.backref('flights', lazy='dynamic'))
    sync_log   = db.relationship(
        'DroneSyncLog', backref=db.backref('flights', lazy='dynamic'))

    # [REASON]: the composite index also serves unit-only lookups via its
    # leading column, so no separate index on drone_unit_id is created.
    __table_args__ = (
        db.Index('ix_drone_flights_unit_started', 'drone_unit_id',
                 'started_at'),
    )


class DroneReattachRun(db.Model):
    """One re-attribution pass over flights whose drone_unit_id is NULL.

    [REASON]: DRONE-007 -- deliberately NOT a DroneSyncLog row. That model
    documents, and the project's verification scripts rely on, the invariant
    records_seen = records_new + records_duplicate + records_error, with
    records_unresolved a subset of records_new. A re-attribution pass stores
    no flight at all: it only fills blanks on rows that are already in the
    table, so it belongs in none of those buckets and recording it there
    would break the invariant and every check written against it.

    [REASON]: detail_json carries the COMPLETE list of affected flight ids,
    not merely the counts. That list is what makes undo exact rather than
    approximate -- it reverts precisely the rows this pass changed and can
    tell a row that has since moved from one it still owns. Shape:

        {"by_nickname": {"<raw spelling>": {"drone_unit_id": 6,
                                            "unit_number": 6,
                                            "count": 724}},
         "flight_ids": [12, 13, 14]}

    At the current worst case (5 977 unattributed flights) that is roughly
    60 KB -- cheap for a ledger written once per pass and read by one screen.

    Created by migrate_drones_reattach_001.py. A row with drone_unit_id
    already set is never modified by the routes that write here: this ledger
    only ever records blanks being filled.
    """
    __tablename__ = 'drone_reattach_runs'
    id           = db.Column(db.Integer, primary_key=True)
    performed_by = db.Column(db.Integer, db.ForeignKey('users.id'),
                             nullable=True)
    performed_at = db.Column(db.DateTime, nullable=False,
                             default=datetime.utcnow)
    rows_matched = db.Column(db.Integer, nullable=False, default=0)
    rows_updated = db.Column(db.Integer, nullable=False, default=0)
    detail_json  = db.Column(db.Text, nullable=False)
    undone_at    = db.Column(db.DateTime, nullable=True)
    undone_by    = db.Column(db.Integer, db.ForeignKey('users.id'),
                             nullable=True)

    # Two foreign keys onto the same table, so the join condition of each
    # relationship has to be spelled out.
    performer = db.relationship('User', foreign_keys=[performed_by])
    undoer    = db.relationship('User', foreign_keys=[undone_by])


# ─── DRONE-FLEET-001: operators, assignments, status ledger, batteries ───────

# The two statuses a machine can be in, and nothing else. The source sheet
# uses exactly `Соз` (serviceable) and `Носоз` (unserviceable); everything
# else it records -- "in Tashkent", "hit a 380 kV line", "5 propellers to
# replace" -- is free text and lives in DroneUnitStatusLog.comment.
#
# [REASON]: no `in_repair`, no `written_off`. A third value looks harmless
# and is not: the moment one exists, half the rows drift into it, the two
# real states stop being countable, and the dashboard tile ("9 serviceable,
# 6 not") can no longer be produced from the data.
DRONE_STATUS_SERVICEABLE = 'serviceable'
DRONE_STATUS_UNSERVICEABLE = 'unserviceable'
DRONE_STATUSES = (DRONE_STATUS_SERVICEABLE, DRONE_STATUS_UNSERVICEABLE)

# Battery condition values, from the source sheet: соз / носоз /
# янги очилмаган (new and unopened). The set is closed for the same reason.
DRONE_BATTERY_WORKING = 'working'
DRONE_BATTERY_FAULTY = 'faulty'
DRONE_BATTERY_NEW_SEALED = 'new_sealed'
DRONE_BATTERY_CONDITIONS = (DRONE_BATTERY_WORKING, DRONE_BATTERY_FAULTY,
                            DRONE_BATTERY_NEW_SEALED)


class DroneOperator(db.Model):
    """One pilot, as the dispatchers write his name.

    [REASON]: DRONE-FLEET-001 -- an operator is NOT a `users` row. He has no
    login, several have no phone at all, and the directory has to hold people
    who left: an operator who is gone still owns the hectares he flew. Hence
    is_active and NO delete route anywhere in the module.

    Two people in the source share one phone number and the two source sheets
    disagree about which of them owns it; the seed records that as a resolved
    conflict in `note` rather than as certainty.
    """
    __tablename__ = 'drone_operators'
    id               = db.Column(db.Integer, primary_key=True)
    full_name        = db.Column(db.String(200), nullable=False)
    phone_primary    = db.Column(db.String(50), nullable=True)
    # Several operators genuinely carry two numbers in the source sheet.
    phone_secondary  = db.Column(db.String(50), nullable=True)
    # [REASON]: descriptive, exactly like DroneUnit.subdivision_name -- it
    # says where the man works, and it is never summed, shared or attributed
    # by. All fifteen machines belong to organization 12 (invariant R4).
    subdivision_name = db.Column(db.String(120), nullable=True)
    is_active        = db.Column(db.Boolean, default=True)
    note             = db.Column(db.Text, nullable=True)
    created_at       = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at       = db.Column(db.DateTime, default=datetime.utcnow,
                                 onupdate=datetime.utcnow)


class DroneOperatorAssignment(db.Model):
    """Operator on a machine BETWEEN TWO DATES. date_to NULL = still current.

    [REASON]: DRONE-FLEET-001, and the single most consequential rule in this
    model file. The 2026-08-03 reconciliation against the dispatchers' April
    ledgers matched the month total to 0.15 % (6 379.24 ha ours against
    6 388.83 ha theirs) while the per-subdivision breakdown carried two large
    errors, +460 ha and -544 ha, that happened to cancel. They were not
    measurement errors: one operator flew machine No 4 until 13 April and
    machine No 8 from 14 April, and the dispatcher kept booking his work to
    his own subdivision. Our data confirms the boundary independently --
    No 4 has 198 flights, none after 13 April; No 8 jumps from 107 to 541
    flights on that date. Once the operator was bound to the machine WITH
    DATES, all twelve operators fell within +-9 %.

    An assignment without a validity period is worthless: it cannot reproduce
    April and it will be wrong again the next time an operator moves. So
    date_from is NOT NULL, and closing an assignment means SETTING date_to --
    never deleting the row, which would erase the history that explains an
    old report.

    [REASON]: OVERLAPS ARE PERMITTED AND MUST NOT BE BLOCKED -- no unique
    constraint over (drone_unit_id, period) exists or may be added. Two
    operators genuinely appear on one machine in the source, and
    substitutions are written as "Anvarov Usmon (Qodirov Nurali)". The screen
    saves an overlapping row and shows a warning naming the other assignment;
    the report gives a flight covered by more than one assignment its own
    visible row. A flight is NEVER silently assigned to one of several
    candidates.

    ATTRIBUTION IS DERIVED, NOT OBSERVED. DJI records flyer_name = DzzDron
    and team_name = 'Бригада 1' on all 28 832 flights -- the whole fleet flies
    under one account, so the operator is never in the payload. Every screen
    and every export carrying an operator cut states this, because a wrong
    assignment silently moves hectares onto the wrong person.
    """
    __tablename__ = 'drone_operator_assignments'
    id            = db.Column(db.Integer, primary_key=True)
    operator_id   = db.Column(db.Integer, db.ForeignKey('drone_operators.id'),
                              nullable=False)
    drone_unit_id = db.Column(db.Integer, db.ForeignKey('drone_units.id'),
                              nullable=False)
    date_from     = db.Column(db.Date, nullable=False)
    # NULL means "still current" -- an open interval, not a missing value.
    date_to       = db.Column(db.Date, nullable=True)
    # Substitution, vacancy, "sheet says both names on one line", ...
    note          = db.Column(db.Text, nullable=True)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    created_by    = db.Column(db.Integer, db.ForeignKey('users.id'),
                              nullable=True)

    operator   = db.relationship(
        'DroneOperator', backref=db.backref('assignments', lazy='dynamic'))
    drone_unit = db.relationship(
        'DroneUnit', backref=db.backref('operator_assignments',
                                        lazy='dynamic'))
    creator    = db.relationship('User', foreign_keys=[created_by])

    __table_args__ = (
        db.Index('ix_drone_oper_assign_unit_from', 'drone_unit_id',
                 'date_from'),
        db.Index('ix_drone_oper_assign_operator_from', 'operator_id',
                 'date_from'),
    )


class DroneUnitStatusLog(db.Model):
    """Append-only ledger of a machine's serviceability.

    Current status of a machine = the row with the greatest changed_on, ties
    broken by the greatest id. Nothing here is ever UPDATEd in place: the
    reason a machine was down in May has to stay readable in August.

    [REASON]: changed_on is a DATE ENTERED BY THE USER and may be in the
    past -- a machine is reported broken after the fact, and stamping now()
    would put the whole fleet's history on the day someone first opened the
    screen. Only FUTURE dates are refused.

    [REASON]: `comment` is free text and carries everything that is not one
    of the two statuses -- where the machine physically is, what it hit, how
    many propellers it needs. Promoting any of that to a status value would
    make the two real states uncountable.
    """
    __tablename__ = 'drone_unit_status_log'
    id            = db.Column(db.Integer, primary_key=True)
    drone_unit_id = db.Column(db.Integer, db.ForeignKey('drone_units.id'),
                              nullable=False)
    # One of DRONE_STATUSES. Enforced in the route, not by a CHECK: a CHECK
    # constraint on SQLite cannot be altered later without rebuilding the
    # table, and the closed set is a business rule the routes state loudly.
    status        = db.Column(db.String(20), nullable=False)
    comment       = db.Column(db.Text, nullable=True)
    changed_on    = db.Column(db.Date, nullable=False)
    changed_by    = db.Column(db.Integer, db.ForeignKey('users.id'),
                              nullable=True)
    # When the ROW was written, as opposed to when the status changed.
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)

    drone_unit = db.relationship(
        'DroneUnit', backref=db.backref('status_log', lazy='dynamic'))
    author     = db.relationship('User', foreign_keys=[changed_by])

    __table_args__ = (
        db.Index('ix_drone_unit_status_log_unit_changed', 'drone_unit_id',
                 'changed_on'),
    )


class DroneBattery(db.Model):
    """One battery of the fleet, in the dispatchers' own numbering (1..46).

    [REASON]: serial_number CARRIES NO UNIQUE CONSTRAINT, and none may be
    added. The source contains a genuine duplicate -- the batteries labelled
    No 8 and No 10 both bear 65VPN19DA50MSU. That is either a transcription
    error or two identical labels, and neither this module nor the person
    running the seed can tell which. A unique index would make the seed fail
    and would push somebody to "fix" a value he cannot verify against the
    physical battery. The screen surfaces duplicates as a warning instead,
    which is the honest outcome: the register shows what the sheet says and
    names the contradiction.

    label_number is the dispatchers' numbering, not an identifier of ours: it
    is what they will say on the phone, so it has to be visible.
    """
    __tablename__ = 'drone_batteries'
    id            = db.Column(db.Integer, primary_key=True)
    serial_number = db.Column(db.String(50), nullable=False)
    label_number  = db.Column(db.Integer, nullable=True)
    # NULL = not attached to any machine (spare, in repair, unknown).
    drone_unit_id = db.Column(db.Integer, db.ForeignKey('drone_units.id'),
                              nullable=True)
    # One of DRONE_BATTERY_CONDITIONS.
    condition     = db.Column(db.String(20), nullable=True)
    note          = db.Column(db.Text, nullable=True)
    is_active     = db.Column(db.Boolean, default=True)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at    = db.Column(db.DateTime, default=datetime.utcnow,
                              onupdate=datetime.utcnow)

    drone_unit = db.relationship(
        'DroneUnit', backref=db.backref('batteries', lazy='dynamic'))

    # [REASON]: both indexes are named EXPLICITLY rather than declared with
    # index=True, because the auto-generated names (ix_drone_batteries_serial_
    # number, ix_drone_batteries_drone_unit_id) would differ from the names
    # migrate_drones_fleet_001.py creates on production. tools/
    # check_migration_drift.py compares a fresh db.create_all() database with
    # the migrated one; two names for one index is exactly the silent drift
    # that check exists to catch. Neither index is UNIQUE -- see the class
    # docstring on 65VPN19DA50MSU.
    __table_args__ = (
        db.Index('ix_drone_batteries_serial', 'serial_number'),
        db.Index('ix_drone_batteries_unit', 'drone_unit_id'),
    )


# ─── DRONE-WORKS-001: the commercial ledger of the drone operation ───────────

# The payment types the dispatchers actually use, and no more.
#   cash     -- Нақд, money handed over
#   transfer -- Справка: the operator writes a certificate, the client signs
#               and stamps it, accounting issues an invoice
#   internal -- Тизим корхонаси, the holding's own land
#   unknown  -- the sheet does not say
# [REASON]: the certificate's document states (written / signed / invoiced /
# paid) were deliberately left OUT of scope by the owner on 2026-08-03.
# Modelling them here would create four half-filled columns nobody maintains
# and a report that looks authoritative while resting on guesses.
#
# [REASON]: `unknown` was added on 2026-08-04 by the owner's decision, after
# the first dry run over the real books rejected 360 rows with «no payment
# block above this row». The September and October detail sheets carry no
# «Справка (…ойи)» / «Нақд (…ойи)» markers at all -- header, then data. The
# payment split lives only in the «ОБШИЙ СВОД» summary sheets, which are not
# imported. Roughly two fifths of the corpus therefore has no payment type in
# the source, and refusing those rows threw away their hectares, customers and
# dates along with the one field that was missing. `unknown` keeps the row and
# names the gap; the owner sets the type later on the works screen.
DRONE_WORK_PAYMENT_CASH = 'cash'
DRONE_WORK_PAYMENT_TRANSFER = 'transfer'
DRONE_WORK_PAYMENT_INTERNAL = 'internal'
DRONE_WORK_PAYMENT_UNKNOWN = 'unknown'
DRONE_WORK_PAYMENT_TYPES = (DRONE_WORK_PAYMENT_CASH,
                            DRONE_WORK_PAYMENT_TRANSFER,
                            DRONE_WORK_PAYMENT_INTERNAL,
                            DRONE_WORK_PAYMENT_UNKNOWN)

# Which header the figure in received_amount came from. See DroneWork.
DRONE_RECEIVED_KIND_RECEIVED = 'received'
DRONE_RECEIVED_KIND_OPERATOR_DUE = 'operator_due'
DRONE_RECEIVED_KINDS = (DRONE_RECEIVED_KIND_RECEIVED,
                        DRONE_RECEIVED_KIND_OPERATOR_DUE)

DRONE_CUSTOMER_EXTERNAL = 'external'
DRONE_CUSTOMER_INTERNAL = 'internal'
DRONE_CUSTOMER_TYPES = (DRONE_CUSTOMER_EXTERNAL, DRONE_CUSTOMER_INTERNAL)

# Suggested prices for the MANUAL entry form only -- never for the import.
# [REASON]: 200 000 so'm/ha dominates the dispatchers' books (639 rows of
# ~708), but 150 000, 250 000, 300 000, 350 000, 400 000, 500 000, 600 000,
# 800 000 and 2 000 000 all occur, and the internal tariff is quoted as
# 85 633 on one sheet and 76 458 on another. So these are SUGGESTIONS shown
# in a form somebody looks at, not defaults the import may substitute: a
# price the import invented is indistinguishable from a price the farm
# agreed. tools/import_drone_works.py stores NULL when the cell is empty.
DRONE_WORK_PRICE_SUGGESTIONS = {
    DRONE_WORK_PAYMENT_CASH: 200000,
    DRONE_WORK_PAYMENT_TRANSFER: 200000,
    DRONE_WORK_PAYMENT_INTERNAL: 85633,
}


class DroneCustomer(db.Model):
    """A farm or organisation the drone service was sold to.

    [REASON]: DRONE-WORKS-001 -- a directory SEPARATE from `customers`, by the
    owner's explicit decision of 2026-08-03. The dispatchers' books carry ~526
    distinct spellings of farms nobody in `customers` has ever heard of, most
    without an INN («Фукаро» -- a private citizen -- will never have one). A
    foreign key into `customers` would presume the outcome of a merge that has
    not been decided; merging the two directories by INN is a separate task.
    Nothing here joins to `customers`, and no row is migrated between them.
    """
    __tablename__ = 'drone_customers'
    id            = db.Column(db.Integer, primary_key=True)
    # Canonical spelling, as it should appear in reports. The verbatim
    # spelling of any single job stays on DroneWork.customer_raw.
    name          = db.Column(db.String(300), nullable=False)
    inn           = db.Column(db.String(20), nullable=True)
    # One of DRONE_CUSTOMER_TYPES. Enforced in the route, not by a CHECK:
    # a CHECK on SQLite cannot be altered later without rebuilding the table.
    customer_type = db.Column(db.String(20), nullable=True,
                              default=DRONE_CUSTOMER_EXTERNAL)
    is_active     = db.Column(db.Boolean, default=True)
    note          = db.Column(db.Text, nullable=True)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at    = db.Column(db.DateTime, default=datetime.utcnow,
                              onupdate=datetime.utcnow)

    __table_args__ = (
        db.Index('ix_drone_customers_name', 'name'),
    )


class DroneCustomerAlias(db.Model):
    """One spelling of a farm name -> one customer. Exactly DroneNickname.

    [REASON]: DRONE-WORKS-001 -- the same shape and the same reason as the DJI
    nickname map. The dispatchers type the farm by hand every time; one farm
    arrives as three spellings across two months. A hardcoded dictionary in
    the old drone system knew 16 of 20 nicknames and silently dropped 2 036
    flights, so the map lives in the database and is extensible at runtime.

    An unknown spelling NEVER rejects a work row: the row is stored with
    drone_customer_id = NULL and counts as unresolved, the same rule flights
    obey.
    """
    __tablename__ = 'drone_customer_aliases'
    id                = db.Column(db.Integer, primary_key=True)
    # Verbatim, as the operator wrote it.
    raw_name          = db.Column(db.String(300), nullable=False)
    # Case-folded and whitespace-collapsed. UNIQUE: one spelling can point at
    # exactly one customer, otherwise resolution would have to guess.
    normalized_key    = db.Column(db.String(300), nullable=False, unique=True)
    drone_customer_id = db.Column(db.Integer,
                                  db.ForeignKey('drone_customers.id'),
                                  nullable=False)
    is_active         = db.Column(db.Boolean, default=True)
    created_at        = db.Column(db.DateTime, default=datetime.utcnow)

    drone_customer = db.relationship(
        'DroneCustomer', backref=db.backref('aliases', lazy='dynamic'))

    __table_args__ = (
        db.Index('ix_drone_customer_aliases_customer', 'drone_customer_id'),
    )


class DroneWork(db.Model):
    """One job from the dispatchers' books: a field sprayed and its price.

    [REASON]: DRONE-WORKS-001, and the single most consequential rule of this
    table -- IT STORES THE OPERATOR, NEVER THE MACHINE. The dispatchers' sheets
    do not name the machine; a row is "Anvarov Usmon sprayed 12.5 ha for
    Mirobod AMT on 11 April at 200 000 so'm". The machine is DERIVED from
    operator + date through drone_operator_assignments, exactly as the flight
    reports derive their operator cut. That inversion is what lets the import
    run before a single assignment exists: every imported row acquires its
    machine retroactively as the owner enters assignments, with no re-import.
    A drone_unit_id column here would invert the dependency and force a
    reload later. Do not add one.

    [REASON]: A ROW IS A JOB, NOT A DAY. Roughly one row in ten carries a date
    written as free text spanning several days -- '23-24.03.2026',
    '13,16,17.05.2026', '08-09.092025'. One field sprayed over three days is
    billed as one line. Hence work_date_from / work_date_to, both nullable,
    and date_raw keeping the original cell verbatim. Measured across all
    files: 86.0 % of rows carry a real date, 9.2 % a text span, 4.8 % nothing.

    [REASON]: period_month is NOT NULL while the dates are nullable. 34 rows
    of ~708 carry no date at all; without a month they would fall out of every
    monthly report exactly the way the old system lost its 2 036 flights. The
    month is always knowable from the source file, so the import takes it from
    the manifest and never from the file-name pattern -- the names are
    inconsistent and two carry no month at all.
    """
    __tablename__ = 'drone_works'
    id                = db.Column(db.Integer, primary_key=True)
    # 'YYYY-MM'. Always known, even when the row has no date at all.
    period_month      = db.Column(db.String(7), nullable=False)
    work_date_from    = db.Column(db.Date, nullable=True)
    # Equals work_date_from for a single-day job.
    work_date_to      = db.Column(db.Date, nullable=True)
    # The original cell, verbatim, whenever it was text. Kept even when the
    # dates parsed, so a parsing rule can be revisited without the sources.
    date_raw          = db.Column(db.String(100), nullable=True)

    drone_operator_id = db.Column(db.Integer,
                                  db.ForeignKey('drone_operators.id'),
                                  nullable=True)
    # The dispatchers' spelling, from the sheet title. Never rewritten.
    operator_raw      = db.Column(db.String(200), nullable=True)

    drone_customer_id = db.Column(db.Integer,
                                  db.ForeignKey('drone_customers.id'),
                                  nullable=True)
    # Verbatim, NEVER rewritten -- it is the evidence behind the resolution.
    customer_raw      = db.Column(db.String(300), nullable=False)

    # [REASON]: NUMERIC with asdecimal=False. The DDL type matches what
    # migrate_drones_works_001.py writes on production, so a fresh
    # db.create_all() database and a migrated one carry the same column type;
    # asdecimal=False keeps the Python value a float, because SQLite has no
    # native decimal and SQLAlchemy would otherwise round-trip through one.
    area_ha           = db.Column(db.Numeric(asdecimal=False), nullable=False)
    # As written in the row. NULL when the cell was empty -- never computed
    # from amount / area, see DRONE_WORK_PRICE_SUGGESTIONS.
    price_per_ha      = db.Column(db.Numeric(asdecimal=False), nullable=True)
    amount            = db.Column(db.Numeric(asdecimal=False), nullable=True)
    # «Бошка харажатлар», «Транспорт харажати», «ЁММ харажати» -- other costs
    # charged on the same line. Six of the twenty-four real layouts carry TWO
    # expense columns at once, and this is their SUM; the import reports per
    # sheet which headers it added together.
    other_costs       = db.Column(db.Numeric(asdecimal=False), nullable=True)
    # «Кирим қилинган» -- how much of the amount came back.
    received_amount   = db.Column(db.Numeric(asdecimal=False), nullable=True)
    # [REASON]: DRONES_WORKS_002. «Кирим қилинган» is money already handed in;
    # «Оператор топшириши керак» is what the operator STILL OWES. In the real
    # sheets the two occupy the same position and both equal
    # amount - other_costs, so they are trivial to conflate -- and conflating
    # them would report an unpaid job as collected and understate the debt
    # report by the whole column. One nullable value of DRONE_RECEIVED_KINDS
    # records which header the figure came from. NULL on rows imported before
    # this migration and on rows typed by hand.
    received_kind     = db.Column(db.String(20), nullable=True)

    # One of DRONE_WORK_PAYMENT_TYPES.
    payment_type      = db.Column(db.String(20), nullable=False)
    # [REASON]: DESCRIPTIVE ONLY, never an attribution key -- the same rule
    # DroneUnit.subdivision_name and DroneOperator.subdivision_name already
    # obey. It says which book the row came out of; summing revenue by it
    # would resurrect the fictional sub-organisations the track rejected.
    subdivision_name  = db.Column(db.String(120), nullable=True)

    # Provenance. NULL on a row typed into the manual form.
    source_file       = db.Column(db.String(300), nullable=True)
    source_sheet      = db.Column(db.String(200), nullable=True)
    source_row        = db.Column(db.Integer, nullable=True)
    import_batch      = db.Column(db.String(100), nullable=True)

    note              = db.Column(db.Text, nullable=True)
    created_at        = db.Column(db.DateTime, default=datetime.utcnow)
    created_by        = db.Column(db.Integer, db.ForeignKey('users.id'),
                                  nullable=True)

    drone_operator = db.relationship(
        'DroneOperator', backref=db.backref('works', lazy='dynamic'))
    drone_customer = db.relationship(
        'DroneCustomer', backref=db.backref('works', lazy='dynamic'))
    creator        = db.relationship('User', foreign_keys=[created_by])

    __table_args__ = (
        # [REASON]: THE reason re-running the import is a no-op instead of a
        # doubling. SQLite treats NULLs inside a unique index as distinct, so
        # manually entered rows -- which carry no provenance at all -- are
        # unaffected and any number of them may exist.
        db.UniqueConstraint('source_file', 'source_sheet', 'source_row',
                            name='uq_drone_works_source'),
        db.Index('ix_drone_works_period', 'period_month'),
        db.Index('ix_drone_works_customer', 'drone_customer_id'),
        db.Index('ix_drone_works_operator', 'drone_operator_id'),
        db.Index('ix_drone_works_date_from', 'work_date_from'),
    )


# ─── DRONE-WORKS-UPLOAD-001: the journal of dispatcher-book uploads ──────────

# One upload batch goes through exactly these states and no others.
#   preview -- the files are stored and parsed, nothing is written to the
#              ledger yet. This is the ONLY state an apply accepts.
#   applied -- apply_rows() ran and committed; the counters below are what it
#              actually wrote, not what the preview predicted.
#   failed  -- parsing raised. The files and the directory are KEPT: they are
#              the evidence, and a book that broke the parser is the one book
#              worth still having.
DRONE_WORK_IMPORT_PREVIEW = 'preview'
DRONE_WORK_IMPORT_APPLIED = 'applied'
DRONE_WORK_IMPORT_FAILED = 'failed'
DRONE_WORK_IMPORT_STATUSES = (DRONE_WORK_IMPORT_PREVIEW,
                              DRONE_WORK_IMPORT_APPLIED,
                              DRONE_WORK_IMPORT_FAILED)


class DroneWorkImport(db.Model):
    """One upload of dispatchers' books through the screen. A JOURNAL row.

    [REASON]: DRONE-WORKS-UPLOAD-001. Nothing here participates in any report
    and no report may ever join to it: the works themselves are the ledger,
    this is the record of how a batch of them got in. Deleting a row loses
    provenance and loses no work.

    [REASON]: ONE ROW PER BATCH, not per file. The dispatchers send a month's
    books together, they are parsed together -- the duplicate detection in
    tools/import_drone_works.py is deliberately run-wide, because one book
    carries two sheets with identical content and two others overlap -- and
    they are written in one transaction. A per-file row would have to describe
    a per-file outcome that does not exist.

    [REASON]: the numbers are stored TWICE on purpose. preview_* is what the
    dry run predicted; works_inserted / works_skipped_existing /
    customers_created are what the apply wrote. They differ legitimately --
    re-uploading a book already in the ledger predicts N rows and inserts
    zero, because apply_rows() skips any row whose (source_file, source_sheet,
    source_row) is already present. Keeping only one of the two would make
    that ordinary outcome unreadable afterwards.

    The writing itself does NOT go through this model. It goes through
    tools/import_drone_works.py:apply_rows() on a plain sqlite3 connection --
    the same function the CLI calls. An ORM re-implementation of that INSERT
    would be a second writer that can drift from the first.
    """
    __tablename__ = 'drone_work_imports'
    id                     = db.Column(db.Integer, primary_key=True)
    # ISO text, not DateTime: written by the route through the ORM but read
    # back by nothing else, and the sqlite3 side of this increment writes
    # plain ISO strings everywhere else too.
    created_at             = db.Column(db.String(40), nullable=False)
    created_by             = db.Column(db.Integer, db.ForeignKey('users.id'),
                                       nullable=True)
    # 'YYYY-MM'. Chosen on the form and applied to every file in the upload.
    # [REASON]: it governs ONLY rows with no parseable date; a dated row is
    # bucketed by its own date, and the preview marks every month that falls
    # outside this one so a book filed under the wrong month is visible.
    period_month           = db.Column(db.String(7), nullable=False)
    # One of DRONE_WORK_IMPORT_STATUSES. Enforced in the route, not by a
    # CHECK: a CHECK on SQLite cannot be altered later without rebuilding.
    status                 = db.Column(db.String(20), nullable=False)
    # The value written into DroneWork.import_batch, 'upload-<id>'. Set on
    # apply. It is derived from this row's id so the works of one batch can be
    # deleted again by anybody who wants them gone.
    import_batch           = db.Column(db.String(100), nullable=True)
    # RELATIVE to the books root, which is derived at runtime from the SQLite
    # file's own directory. Never absolute: production and staging must not be
    # able to point at each other's files after a database copy.
    storage_dir            = db.Column(db.String(200), nullable=False)
    # [{"name": …, "sha256": …, "size": …}] -- re-hashed before every apply.
    files_json             = db.Column(db.Text, nullable=False)
    note                   = db.Column(db.Text, nullable=True)

    preview_rows           = db.Column(db.Integer, nullable=True)
    preview_area_ha        = db.Column(db.Numeric(asdecimal=False),
                                       nullable=True)
    preview_amount         = db.Column(db.Numeric(asdecimal=False),
                                       nullable=True)

    applied_at             = db.Column(db.String(40), nullable=True)
    applied_by             = db.Column(db.Integer, db.ForeignKey('users.id'),
                                       nullable=True)
    works_inserted         = db.Column(db.Integer, nullable=True)
    works_skipped_existing = db.Column(db.Integer, nullable=True)
    customers_created      = db.Column(db.Integer, nullable=True)
    rows_rejected          = db.Column(db.Integer, nullable=True)
    rows_duplicate         = db.Column(db.Integer, nullable=True)
    # File name inside the batch directory, never a path.
    report_file            = db.Column(db.String(200), nullable=True)
    error_text             = db.Column(db.Text, nullable=True)

    creator  = db.relationship('User', foreign_keys=[created_by])
    applier  = db.relationship('User', foreign_keys=[applied_by])

    __table_args__ = (
        db.Index('ix_drone_work_imports_created', 'created_at'),
    )


# ─── GPS-2/GPS-3: результат суточного расчёта по треку ───────────────────────

GPS_LABEL_WORK = 'работа'
GPS_LABEL_PASSAGE = 'проезд'
GPS_OPERATOR_LABELS = (GPS_LABEL_WORK, GPS_LABEL_PASSAGE)


class GpsDailyAggregate(db.Model):
    """Одна строка на объект-сутки. Пишет gps/daily.py, приложение только читает.

    [REASON]: строка есть ВСЕГДА, в том числе когда работы нет. «Мы посмотрели,
    и работы нет» и «мы не смотрели» — разные факты, и отсутствующая строка их
    не различает. Причина отказа лежит в `reason`; NULL означает, что площадь
    посчитана и опубликована.

    [REASON]: таблицу создаёт миграция GPS_DAILY_001, а не эта модель.
    Определение здесь нужно экрану GPS-3 и db.create_all() на свежей установке;
    столбцы обязаны совпадать с миграцией — расхождение db.create_all() не
    поймает, оно молча создаст таблицу без недостающей колонки.

    Расчёт идёт вне приложения: gps/daily.py импортирует геостек и пишет через
    stdlib sqlite3, потому что `app = create_app()` вызывает db.create_all() на
    импорте и превратил бы расчётный скрипт в писателя схемы.
    """
    __tablename__ = 'gps_daily_aggregates'
    id                = db.Column(db.Integer, primary_key=True)
    work_date         = db.Column(db.Date, nullable=False)
    wialon_id         = db.Column(db.Integer, nullable=False)
    points_total      = db.Column(db.Integer, nullable=False, default=0)
    points_work       = db.Column(db.Integer, nullable=False, default=0)
    track_km          = db.Column(db.Float, nullable=False, default=0)
    interval_median_s = db.Column(db.Float, nullable=True)
    sats_median       = db.Column(db.Float, nullable=True)
    motion_gaps       = db.Column(db.Integer, nullable=False, default=0)
    lost_seconds      = db.Column(db.Float, nullable=False, default=0)
    gps_jumps         = db.Column(db.Integer, nullable=False, default=0)
    reason            = db.Column(db.String(40), nullable=True)
    method_version    = db.Column(db.String(60), nullable=False)
    computed_at       = db.Column(db.DateTime, nullable=False)

    __table_args__ = (
        db.UniqueConstraint('work_date', 'wialon_id',
                            name='uq_gps_daily_aggregates_day_unit'),
        db.Index('ix_gps_daily_aggregates_unit_day', 'wialon_id', 'work_date'),
    )

    @property
    def published(self):
        """Площадь посчитана и опубликована — то есть отказа не было."""
        return self.reason is None


class GpsWorkPolygon(db.Model):
    """Один найденный участок работы за сутки.

    Приложение читает всё, кроме двух колонок: `operator_label` и `decided_at`
    заполняет экран GPS-3 ответом человека. Всё остальное — результат расчёта,
    и переписывать его из приложения нельзя: пересчёт всё равно заменит.

    [REASON]: ответ оператора лежит здесь, а не в отдельной таблице. Это
    обучающий набор для правила «работа/проезд» — единственного, что пока не
    даётся ни одному измеренному признаку, — а ответ, оторванный от геометрии,
    о которой он дан, не стоит ничего. Пересчёт суток переносит ответ на новый
    участок по площади пересечения (gps/daily.py), а не по номеру.
    """
    __tablename__ = 'gps_work_polygons'
    id              = db.Column(db.Integer, primary_key=True)
    work_date       = db.Column(db.Date, nullable=False)
    wialon_id       = db.Column(db.Integer, nullable=False)
    site_number     = db.Column(db.Integer, nullable=False)
    area_ha         = db.Column(db.Float, nullable=False)
    minutes         = db.Column(db.Float, nullable=False)
    polygon_geojson = db.Column(db.Text, nullable=False)
    # [REASON]: NULLABLE, и это норма, а не дефект. Операторы геозоны обычно
    # не заводят; требование контура теряло 31,77 га из 159 на наборе по
    # опрыскиванию и давало два ровных нуля при машине, простоявшей в поле
    # весь день.
    contour_id      = db.Column(db.Integer, db.ForeignKey('field_contours.id'),
                                nullable=True)
    alpha_used_m    = db.Column(db.Float, nullable=True)
    pass_spacing_m  = db.Column(db.Float, nullable=True)
    quality_flag    = db.Column(db.String(120), nullable=True)
    suggested_label = db.Column(db.String(20), nullable=True)
    operator_label  = db.Column(db.String(20), nullable=True)
    decided_at      = db.Column(db.DateTime, nullable=True)

    contour = db.relationship('FieldContour')

    __table_args__ = (
        db.UniqueConstraint('work_date', 'wialon_id', 'site_number',
                            name='uq_gps_work_polygons_day_unit_site'),
        db.Index('ix_gps_work_polygons_unit_day', 'wialon_id', 'work_date'),
        db.Index('ix_gps_work_polygons_contour', 'contour_id'),
    )


# ─── Migration Registry ───────────────────────────────────────────────────────

class SchemaMigration(db.Model):
    """Registry of applied database migrations.

    Created by migrate_000_migration_registry.py (TASK-OPS-001).
    For existing production databases the table is created by running that
    script once; for fresh installs db.create_all() will create it automatically.
    """
    __tablename__ = 'schema_migrations'
    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(300), unique=True, nullable=False)
    applied_at  = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    checksum    = db.Column(db.String(64), nullable=True)
    description = db.Column(db.Text, nullable=True)


# ─── Module permission decorator ─────────────────────────────────────────────

def module_required(module_code):
    """Decorator factory: returns 403 if the logged-in user lacks access to module_code.

    Usage:
        @app.route('/wialon')
        @module_required('wialon')
        @editor_required
        def wialon_index(): ...
    """
    def decorator(f):
        from functools import wraps
        from flask import abort
        from flask_login import login_required, current_user
        @wraps(f)
        @login_required
        def decorated(*args, **kwargs):
            # [REASON]: Central route guard that enforces user_module_permissions.
            # Without this check, direct URL access would bypass the admin UI permission toggles.
            if not current_user.has_module_access(module_code):
                abort(403)
            return f(*args, **kwargs)
        return decorated
    return decorator
