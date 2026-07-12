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

    __table_args__ = (
        db.Index('ix_daily_date_eq', 'work_date', 'equipment_id', 'line_index'),
    )

    @property
    def total_amount(self):
        return ((self.amount_cash or 0) + (self.amount_transfer or 0) +
                (self.amount_internal or 0) + (self.amount_other or 0))


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


class SparePart(db.Model):
    __tablename__ = 'spare_parts'
    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(300), nullable=False)
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
        db.Index('idx_spare_part_write_off_acts_request_id', 'request_id'),
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
