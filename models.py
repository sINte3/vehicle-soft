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

ROLES = {
    ROLE_ADMIN:    'Администратор',
    ROLE_OPERATOR: 'Оператор',
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

    records = db.relationship('DailyRecord', backref='equipment',
                              cascade='all, delete-orphan', lazy='dynamic')

    @property
    def full_name(self):
        if self.plate:
            return f"{self.name}\n{self.plate}"
        return self.name

    @property
    def category_display(self):
        return CATEGORIES.get(self.category, self.category)


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
    is_active    = db.Column(db.Boolean, default=True)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

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

class SparePart(db.Model):
    __tablename__ = 'spare_parts'
    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(300), nullable=False)
    part_number = db.Column(db.String(100), default='')
    unit        = db.Column(db.String(30), default='dona')
    category    = db.Column(db.String(100), default='')
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)


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
    spare_part    = db.relationship('SparePart')


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
