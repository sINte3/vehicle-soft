# -*- coding: utf-8 -*-
"""B3 -- сверка заявок agro-work с фактом GPS в обе стороны. Только stdlib.

Читает, ничего не пишет: заявки и связки -- из таблиц импорта, факт работы
-- из таблиц трека GPS (`gps_daily_aggregates`, `gps_work_polygons`), как их
посчитал `gps/daily.py`. Факт здесь не пересчитывается (граница трека:
«расчёт факта работы -- в треке GPS, здесь только читается»).

ОКНО ЗАЯВКИ -- ответ владельца на вопрос 4, 28.09:
  * обычная заявка: с дня создания по день перехода в «Выполнено» из истории
    статусов, включительно;
  * заявка, заведённая сразу «Выполнено»: день ввода и N дней до него. N = 2
    утверждён владельцем 29.09 по замеру `measure_lags`; при N = None (не
    утверждён) такая заявка -- отдельная строка «заведена задним числом», не
    нарушение;
  * отменённые работу не покрывают; «в ожидании» и «в процессе» -- без
    вердикта до закрытия.

ЗАЯВКА, УДАЛЁННАЯ В AGRO-WORK (пропала из полной выгрузки, `gone_at`), --
ответ владельца на вопрос 11, 28.09: работу не покрывает, как отменённая, и
остаётся в сверке с пометкой «удалена в agro-work».

СУТКИ МАШИНЫ ПО GPS -- три состояния, и третье не сводится ко второму:
  * работа -- площадь опубликована и есть участок, который человек не назвал
    проездом (порог участка 0,3 га -- решение владельца, трек GPS);
  * работы не было -- площадь опубликована, участков нет (или все названы
    проездом), либо «движения нет»: трекер жив и машина стояла;
  * неизвестно -- суток не считали, точек нет, запись редкая, сбор не
    завершён, спецтехника (трек GPS с 30.09, PR #153: по ней публикуется
    только след, без гектаров, причина `spetstekhnika`). «Точек нет» -- это
    молчащий трекер, а не стоящая машина; у спецтехники гектаров нет по
    правилу, поэтому ни «работы», ни «работы не было» по ним не бывает.

ВЕРДИКТ ЗАЯВКИ (в сторону «заявка -> работа»): работа была, если хотя бы в
одних сутках окна она есть; работы не было, только если ВСЕ сутки окна
известны и работы нет ни в одних. Иначе -- без вердикта с названной
причиной. Сверяются только заявки метода «гектары»: для времени и рейсов
трек GPS факта пока не публикует, и выдумывать его здесь нельзя.

ПОКРЫТИЕ СУТОК (в сторону «работа -> заявка»): сутки с работой машины по GPS
покрыты, если в их окно попадает закрытая обычная заявка этой машины. Иначе
-- без вердикта, если сутки может покрыть открытая заявка, заявка с
неизвестным окном или заведённая задним числом; нарушение «работа без
заявки» -- только когда не может никакая.

ПОЗДНЯЯ ЗАЯВКА (B4, пункт 4, решение 30.09). У суток «работа без заявки»
называется ближайшая заявка той же машины, заведённая задним числом ПОЗЖЕ
этих суток, и через сколько суток после них её ввели. Вердикт не меняется:
ввод задним числом позже N суток -- нарушение (владелец, 30.09). Порога
«насколько поздно» нет: его пришлось бы выдумать, а число суток читающему
видно само -- 3 похоже на поздний ввод той же работы, 29 -- на другую.

ЧТО БЫЛО ПОТОМ (B5, решение владельца 07.10: «нужно отделять»). Сутки
«работа без заявки» делятся на четыре части, в сумме -- все такие сутки:
  * позже заведена заявка задним числом -- это поздняя заявка B4;
  * позже заведена обычная заявка -- открытая или закрытая, но не сразу
    «выполненной»; называется ближайшая. Её окно начинается с дня создания
    (правило 4), поэтому сутки до него она не покрывает и вердикт остаётся
    «без заявки»: B5 только называет факт;
  * позже заведена заявка, порядок которой неизвестен -- нет истории
    статусов или статус незнакомый: задним ли числом, сказать нельзя;
  * заявка не заведена совсем: после этих суток у машины новых заявок нет
    (отменённые и удалённые в agro-work не в счёт -- они работу не
    покрывают, ответы 4 и 11).
Порога нет, как и в B4: число суток читающему видно.

ОТКРЫТЫЕ ЗАЯВКИ (B4, пункт 5). Список открытых заявок периода от самой
старой, с числом суток с ввода -- единственной даты, которая есть у каждой
заявки. Сколько суток заявке позволено быть открытой, решает владелец;
пока он не назвал это число, список ничего не подсвечивает.

ОБЪЁМ ПРОТИВ GPS (U2; решения сессии по поручению владельца 08.10 --
раздел 9.2 трека agro-work, не его правила). Светофор допусков «Сверки
нарядов» (`gps/tolerance.py`) ставится заявке только тогда, когда гектары
GPS можно отнести к ней без угадывания:
  * только «работа была», единица «гектар», объём больше нуля;
  * все сутки окна известны -- иначе гектары неполные;
  * ни одни сутки окна не могла покрыть другая живая заявка той же машины
    (тот же критерий, что у покрытия суток). Иначе -- «сверить нельзя» с
    причиной и номерами; если вся цепочка пересекающихся окон сравнима,
    показаны её общие числа -- без цвета: цвет цепочки лёг бы и на заявку,
    которая сама в порядке.
Вердикты, покрытие, B4 и B5 объём не меняет.

МАШИНА. Заявка -> машина agro-work -> наша техника (связь импорта или
владельца) -> объект Wialon (`vialon_mappings`, без `skip`). Неоднозначное
не угадывается: несколько объектов у машины -- причина, как в сверке
нарядов трека GPS; объект, который план-факт не считает (`gps.exclusion`),
-- причина.
"""

import math
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

from gps import tolerance
from gps.exclusion import excluded_units

from . import methods, records

# [REASON]: N из ответа владельца на вопрос 4 -- сколько суток ДО ввода
# заявки, заведённой сразу «Выполнено», искать её работу. Число не
# выдумано: `measure_lags` 29.09 на копии боевой базы -- у 366 обычных
# закрытых заявок «Выполнено» отмечено не позже двух суток после последней
# работы по GPS у 97 %, у метода «гектары» (88 заявок) -- у всех. Владелец
# утвердил N = 2 в тот же день. 30.09 он же решил, что ввод заявки задним
# числом через 3-6 суток после работы -- НАРУШЕНИЕ: при N = 2 такие сутки
# в сверке «работа -> заявка» -- «работа без заявки», и так и задумано
# (в сентябре около 150 машино-суток тракторов Пахтасаноаттранса; N = 6
# спрятало бы их). Менять -- только по новому замеру и решению владельца.
# None значит «не утверждено»: такие заявки идут отдельной строкой без
# вердикта.
BACKDATED_LOOKBACK_DAYS = 2

# Знак «взять BACKDATED_LOOKBACK_DAYS в момент вызова»: None в аргументе --
# уже значение («не утверждено»), поэтому умолчанию нужен свой знак.
_DEFAULT = object()

# Состояния суток по GPS.
WORK, IDLE, UNKNOWN = 'work', 'idle', 'unknown'

GPS_REASON_NO_MOTION = 'net_dvizheniya'
PASSAGE_LABEL = 'проезд'

# Вердикты заявки.
V_WORK = 'rabota_est'
V_NO_WORK = 'rabota_net'
V_NONE = 'bez_verdikta'

# Покрытие суток с работой.
C_COVERED = 'pokryta'
C_UNCOVERED = 'bez_zayavki'
C_NONE = 'bez_verdikta'

# Счётчик свода: из суток «без заявки» -- те, у которых есть поздняя заявка
# задним числом (B4, пункт 4).
DAY_LATE = 'day_bez_zayavki_pozdnyaya'

# Что было потом у суток «без заявки» (B5). Слаги ASCII, подписи -- в
# agro_work/labels.py. Счётчик свода -- DAY_AFTER + слаг.
A_BACKDATED = 'zadnim_chislom_pozzhe'
A_ORDINARY = 'obychnaya_pozzhe'
A_UNCLEAR = 'pozzhe_poryadok_neizvesten'
A_NOT_ENTERED = 'ne_zavedena'
AFTER_KINDS = (A_BACKDATED, A_ORDINARY, A_UNCLEAR, A_NOT_ENTERED)
DAY_AFTER = 'day_bez_zayavki_potom_'

# Объём заявки против гектаров GPS (U2). Светофор -- общий со «Сверкой
# нарядов»; четвёртое значение -- «сверить нельзя», причина -- ниже.
VOL_OK = tolerance.VERDICT_OK
VOL_WARN = tolerance.VERDICT_WARN
VOL_FAIL = tolerance.VERDICT_FAIL
VOL_NONE = 'sverit_nelzya'
VOLUME_VERDICTS = (VOL_FAIL, VOL_WARN, VOL_NONE, VOL_OK)
# Счётчик свода: VOL + значение выше; причины -- VOL_REASON + причина.
VOL = 'vol_'
VOL_REASON = 'vol_reason_'

# Почему объём сверить нельзя -- в порядке проверки: первая сработавшая и
# названа. Пересечение: открытая заявка -- худшее (её надо закрыть), затем
# неизвестное окно, затем пересечение закрытых окон.
VR_NOT_HECTARE = 'obem_ne_v_gektarakh'
VR_NO_VOLUME = 'obem_ne_ukazan'
VR_DAYS_UNKNOWN = 'obem_ne_vse_sutki'
VR_OPEN = 'obem_otkrytaya_zayavka'
VR_WINDOW_UNKNOWN = 'obem_okno_neizvestno'
VR_OVERLAP = 'obem_okna_peresekayutsya'
VOLUME_REASONS = (VR_NOT_HECTARE, VR_NO_VOLUME, VR_DAYS_UNKNOWN, VR_OPEN,
                  VR_WINDOW_UNKNOWN, VR_OVERLAP)

# Причины «без вердикта». Слаги ASCII, подписи -- на экране и в отчёте.
R_OPEN = 'otkryta'
R_CANCELLED = 'otmenena'
R_GONE = 'udalena_v_agro_work'
R_BACKDATED = 'zadnim_chislom'
R_NO_HISTORY = 'istoriya_ne_zagruzhena'
R_NO_CLOSE_DATE = 'net_daty_zakrytiya'
R_BAD_WINDOW = 'nekorrektnoe_okno'
R_UNKNOWN_STATUS = 'neizvestnyy_status'
R_METHOD_UNMARKED = 'metod_ne_razmechen'
R_METHOD_NONE = 'ne_sveryaetsya'
R_METHOD_TIME = 'metod_vremya_ne_publikuetsya'
R_METHOD_TRIPS = 'metod_reysy_ne_publikuetsya'
R_NOT_MATCHED = 'mashina_ne_sopostavlena'
R_NO_WIALON = 'net_svyazi_s_wialon'
R_MANY_OBJECTS = 'neskolko_obektov'
R_EXCLUDED = 'obekt_isklyuchen'
R_NO_GPS = 'net_dannyh_gps'
R_OPEN_COVERS = 'otkrytaya_zayavka'
R_MAYBE_BACKDATED = 'vozmozhno_zadnim_chislom'
R_WINDOW_UNKNOWN = 'okno_zayavki_neizvestno'
R_NOT_IN_AGRO = 'net_v_agro_work'
R_MANY_AGRO = 'neodnoznachnaya_svyaz'

FORWARD_REASONS = (R_OPEN, R_CANCELLED, R_GONE, R_BACKDATED, R_NO_HISTORY,
                   R_NO_CLOSE_DATE, R_BAD_WINDOW, R_UNKNOWN_STATUS,
                   R_METHOD_UNMARKED, R_METHOD_NONE, R_METHOD_TIME,
                   R_METHOD_TRIPS, R_NOT_MATCHED, R_NO_WIALON, R_MANY_OBJECTS,
                   R_EXCLUDED, R_NO_GPS)
REVERSE_REASONS = (R_OPEN_COVERS, R_WINDOW_UNKNOWN, R_MAYBE_BACKDATED,
                   R_NOT_IN_AGRO, R_MANY_AGRO, R_MANY_OBJECTS)

METHOD_REASON = {None: R_METHOD_UNMARKED, methods.METHOD_NONE: R_METHOD_NONE,
                 methods.METHOD_TIME: R_METHOD_TIME,
                 methods.METHOD_TRIPS: R_METHOD_TRIPS}

UNIT_HECTARE = 'HECTARE'

# Окно неизвестно, но заявка могла покрыть сутки (`could_cover`).
UNKNOWN_WINDOW_REASONS = (R_NO_HISTORY, R_NO_CLOSE_DATE, R_BAD_WINDOW,
                          R_UNKNOWN_STATUS)


def parse_volume(value):
    """Объём заявки числом или None: пусто, не число, не больше нуля.

    API отдаёт десятичные строкой («12.50»), импорт хранит её как пришла.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).strip().replace(',', '.'))
    except ValueError:
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def parse_day(value):
    if isinstance(value, date):
        return value
    if not value:
        return None
    return datetime.strptime(str(value)[:10], '%Y-%m-%d').date()


def stamp_day(value):
    """Местные сутки нашей UTC-метки 'YYYY-MM-DD HH:MM:SS' (gone_at)."""
    if not value:
        return None
    try:
        moment = datetime.strptime(str(value)[:19], '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None
    return records.local_day(moment.replace(tzinfo=timezone.utc))


def days_between(first, last):
    day = first
    while day <= last:
        yield day
        day += timedelta(days=1)


def day_state(aggregate, sites):
    """(состояние, га) одних суток одного объекта. Правило -- в докстринге."""
    if aggregate is None:
        return UNKNOWN, 0.0
    reason = aggregate.get('reason')
    if reason == GPS_REASON_NO_MOTION:
        return IDLE, 0.0
    if reason:
        return UNKNOWN, 0.0
    counted = [s for s in sites if s.get('operator_label') != PASSAGE_LABEL]
    if counted:
        return WORK, round(sum(s.get('area_ha') or 0.0 for s in counted), 3)
    return IDLE, 0.0


class Application:
    """Заявка с тем, что из неё выводит сверка."""

    __slots__ = ('row', 'id', 'number', 'status', 'transport_id',
                 'work_type_id', 'created_day', 'completed_day',
                 'has_history', 'backdated', 'window', 'window_reason',
                 'gone', 'gone_day', 'alive_window')

    def __init__(self, row, lookback):
        self.row = row
        self.id = row['id']
        self.number = row['application_number']
        self.status = row['status']
        self.transport_id = row['transport_id']
        self.work_type_id = row['work_type_id']
        self.created_day = parse_day(row['created_day'])
        self.completed_day = parse_day(row['completed_day'])
        self.has_history = row['history_updated_at'] is not None
        self.backdated = (self.status == records.STATUS_COMPLETED
                          and self.has_history
                          and row['initial_status'] == records.STATUS_COMPLETED)
        self.gone = bool(row.get('gone_at'))
        self.gone_day = stamp_day(row.get('gone_at'))
        alive_window, alive_reason = self._window(lookback)
        # Окно, которое было бы у заявки, не удали её agro-work: по нему
        # удалённая заявка остаётся в списке своего периода.
        self.alive_window = alive_window
        if self.gone:
            # [REASON]: ответ владельца на вопрос 11 (28.09) -- удалённая в
            # agro-work заявка работу не покрывает, как отменённая. Окна у
            # неё нет, вердикта нет, причина названа.
            self.window, self.window_reason = None, R_GONE
        else:
            self.window, self.window_reason = alive_window, alive_reason

    def _window(self, lookback):
        """(окно (первые сутки, последние сутки) или None, причина)."""
        if self.status == records.STATUS_CANCELLED:
            return None, R_CANCELLED
        if self.status in records.OPEN_STATUSES:
            return None, R_OPEN
        if self.status != records.STATUS_COMPLETED:
            return None, R_UNKNOWN_STATUS
        if not self.has_history:
            return None, R_NO_HISTORY
        if self.backdated:
            if lookback is None:
                return None, R_BACKDATED
            entry = self.created_day
            return (entry - timedelta(days=lookback), entry), None
        if self.completed_day is None:
            return None, R_NO_CLOSE_DATE
        if self.completed_day < self.created_day:
            return None, R_BAD_WINDOW
        return (self.created_day, self.completed_day), None

    @property
    def is_open(self):
        return self.status in records.OPEN_STATUSES

    @property
    def listed_span(self):
        """Какие сутки заявка «занимает» в списке периода."""
        if self.window:
            return self.window
        if self.gone:
            if self.alive_window:
                return self.alive_window
            return self.created_day, max(self.gone_day or self.created_day,
                                         self.created_day)
        if self.status in records.OPEN_STATUSES:
            return self.created_day, date.max
        moment = records.parse_moment(self.row.get('cancelled_at'))
        cancelled = records.local_day(moment) if moment else self.created_day
        return self.created_day, max(cancelled, self.created_day)

    def could_cover(self, day, lookback):
        """Может ли заявка с НЕИЗВЕСТНЫМ окном покрыть эти сутки.

        Окно неизвестно, если нет истории: заявка могла оказаться обычной
        (окно с дня создания, то есть сутки не раньше него) или заведённой
        задним числом (N суток до ввода). Вместе -- сутки не раньше, чем за N
        до создания; при неутверждённом N -- любые.
        """
        if lookback is None:
            return True
        return day >= self.created_day - timedelta(days=lookback)


class Reconciliation:
    """Сверка за период [date_from, date_to]. Всё читается один раз."""

    def __init__(self, con, date_from, date_to, org_ids=None,
                 lookback=_DEFAULT, today=None):
        self.date_from = date_from
        self.date_to = date_to
        self.org_ids = set(org_ids) if org_ids is not None else None
        self.lookback = (BACKDATED_LOOKBACK_DAYS if lookback is _DEFAULT
                         else lookback)
        self.today = today or date.today()
        self._load(con)

    # --- чтение ------------------------------------------------------------------

    def _load(self, con):
        self.methods = {r[0]: r[1] for r in con.execute(
            'SELECT id, method FROM agro_work_work_types')}
        self.work_type_names = {r[0]: r[1] for r in con.execute(
            'SELECT id, name FROM agro_work_work_types')}
        self.orgs = {r[0]: r[1] for r in con.execute(
            'SELECT id, name FROM organizations')}
        self.equipment = {}
        for row in con.execute('SELECT id, name, plate, category, '
                               'organization_id FROM equipment'):
            self.equipment[row[0]] = {'id': row[0], 'name': row[1] or '',
                                      'plate': row[2] or '',
                                      'category': row[3] or '',
                                      'organization_id': row[4]}
        self.transports = {}
        self.transports_by_equipment = defaultdict(list)
        for row in con.execute('SELECT id, plate_number, equipment_id, '
                               'match_status, company_name, category_name '
                               'FROM agro_work_transports'):
            item = {'id': row[0], 'plate_number': row[1],
                    'equipment_id': row[2], 'match_status': row[3],
                    'company_name': row[4], 'category_name': row[5]}
            self.transports[row[0]] = item
            if row[2] is not None:
                self.transports_by_equipment[row[2]].append(row[0])
        self.wialon_by_equipment = defaultdict(list)
        self.equipment_by_wialon = defaultdict(set)
        for equipment_id, wialon_id in con.execute(
                'SELECT equipment_id, wialon_id FROM vialon_mappings '
                'WHERE wialon_id IS NOT NULL AND equipment_id IS NOT NULL '
                'AND (skip IS NULL OR skip = 0)'):
            if wialon_id not in self.wialon_by_equipment[equipment_id]:
                self.wialon_by_equipment[equipment_id].append(int(wialon_id))
            self.equipment_by_wialon[int(wialon_id)].add(equipment_id)
        self.excluded = excluded_units(con)

        columns = ('id', 'application_number', 'status', 'transport_id',
                   'work_type_id', 'work_type_name', 'unit', 'volume',
                   'company_name', 'plate_number', 'created_day',
                   'completed_day', 'history_updated_at', 'initial_status',
                   'cancelled_at', 'gone_at', 'is_active', 'created_at')
        self.applications = []
        self.apps_by_transport = defaultdict(list)
        for values in con.execute('SELECT %s FROM agro_work_applications'
                                  % ', '.join(columns)):
            app = Application(dict(zip(columns, values)), self.lookback)
            self.applications.append(app)
            self.apps_by_transport[app.transport_id].append(app)

        # Диапазон суток GPS: период плюс окна заявок, которые в него попали,
        # -- в обе стороны: окно заявки, закрытой после конца периода, тоже
        # нужно целиком, иначе её вердикт считался бы по обрезку.
        first, last = self.date_from, self.date_to
        for app in self.applications:
            if app.window and self.in_period(app):
                first = min(first, app.window[0])
                last = max(last, app.window[1])
        last = min(last, self.today)
        self.gps = {}
        self.gps_first = first
        aggregates = {}
        for wialon_id, day, reason in con.execute(
                'SELECT wialon_id, work_date, reason FROM gps_daily_aggregates '
                'WHERE work_date BETWEEN ? AND ?',
                (first.isoformat(), last.isoformat())):
            aggregates[(int(wialon_id), parse_day(day))] = {'reason': reason}
        sites = defaultdict(list)
        for wialon_id, day, area, label in con.execute(
                'SELECT wialon_id, work_date, area_ha, operator_label FROM '
                'gps_work_polygons WHERE work_date BETWEEN ? AND ?',
                (first.isoformat(), last.isoformat())):
            sites[(int(wialon_id), parse_day(day))].append(
                {'area_ha': area, 'operator_label': label})
        for key, aggregate in aggregates.items():
            self.gps[key] = day_state(aggregate, sites.get(key, []))

    def in_period(self, app):
        start, end = app.listed_span
        return start <= self.date_to and end >= self.date_from

    # --- машина ----------------------------------------------------------------

    def equipment_in_scope(self, equipment_id):
        if self.org_ids is None:
            return True
        item = self.equipment.get(equipment_id)
        return item is not None and item['organization_id'] in self.org_ids

    def machine_of(self, transport_id):
        """(equipment_id, wialon_id, причина). Причина None -- машина годна."""
        transport = self.transports.get(transport_id)
        if transport is None or transport['equipment_id'] is None:
            return None, None, R_NOT_MATCHED
        equipment_id = transport['equipment_id']
        units = self.wialon_by_equipment.get(equipment_id, [])
        if not units:
            return equipment_id, None, R_NO_WIALON
        if len(units) > 1:
            return equipment_id, None, R_MANY_OBJECTS
        if units[0] in self.excluded:
            return equipment_id, units[0], R_EXCLUDED
        return equipment_id, units[0], None

    def state(self, wialon_id, day):
        if day > self.today:
            return UNKNOWN, 0.0
        return self.gps.get((wialon_id, day), (UNKNOWN, 0.0))

    # --- заявка -> работа ---------------------------------------------------------

    def forward_row(self, app):
        equipment_id, wialon_id, machine_reason = self.machine_of(app.transport_id)
        row = {'app': app, 'window': app.window, 'equipment_id': equipment_id,
               'wialon_id': wialon_id, 'verdict': V_NONE, 'reason': None,
               'work_days': [], 'unknown_days': 0, 'gps_ha': 0.0,
               'method': self.methods.get(app.work_type_id),
               'volume': parse_volume(app.row.get('volume')),
               'volume_verdict': None, 'volume_reason': None,
               'volume_deviation': None, 'volume_share': None,
               'volume_neighbours': [], 'volume_group': None}
        if app.window_reason:
            row['reason'] = app.window_reason
            return row
        method = row['method']
        if method != methods.METHOD_GA:
            row['reason'] = METHOD_REASON.get(method, R_METHOD_UNMARKED)
            return row
        if machine_reason:
            row['reason'] = machine_reason
            return row
        unknown = 0
        for day in days_between(*app.window):
            state, hectares = self.state(wialon_id, day)
            if state == WORK:
                row['work_days'].append(day)
                row['gps_ha'] = round(row['gps_ha'] + hectares, 3)
            elif state == UNKNOWN:
                unknown += 1
        row['unknown_days'] = unknown
        if row['work_days']:
            row['verdict'] = V_WORK
            self.volume_check(app, row)
        elif unknown == 0:
            row['verdict'] = V_NO_WORK
        else:
            row['reason'] = R_NO_GPS
        return row

    # --- объём против GPS (U2) ------------------------------------------------------

    def live_apps_of(self, equipment_id):
        """Живые заявки всех машин agro-work, связанных с этой техникой.

        Отменённая и удалённая в agro-work работу не покрывают (ответы 4 и
        11 владельца) -- их здесь нет.
        """
        apps = []
        for transport_id in self.transports_by_equipment.get(equipment_id, []):
            apps.extend(a for a in self.apps_by_transport.get(transport_id, [])
                        if a.status != records.STATUS_CANCELLED and not a.gone)
        return apps

    def may_cover(self, app, day):
        """Могла ли живая заявка покрыть работу этих суток.

        [REASON]: тот же критерий, что у покрытия суток (`coverage`): своё
        окно; открытая -- с дня создания (срока у открытой заявки нет,
        владелец, 07.10); неизвестное окно -- `could_cover`; задним числом
        при неутверждённом N -- сутки не позже ввода. Два разных критерия
        сделали бы одни и те же сутки «покрытыми» для одной стороны сверки
        и «ничьими» для другой. Совпадение держит тест.
        """
        if app.window:
            return app.window[0] <= day <= app.window[1]
        if app.status in records.OPEN_STATUSES:
            return app.created_day <= day
        if app.window_reason in UNKNOWN_WINDOW_REASONS:
            return app.could_cover(day, self.lookback)
        if app.window_reason == R_BACKDATED:
            return app.created_day >= day
        return False

    def volume_check(self, app, row):
        """Объём заявки против гектаров GPS её окна. Только «работа была».

        Ставит либо светофор (volume_verdict, расхождение, доля), либо
        причину, почему сверить нельзя, -- ровно одно из двух.
        """
        if app.row.get('unit') != UNIT_HECTARE:
            row['volume_reason'] = VR_NOT_HECTARE
            return
        if row['volume'] is None:
            row['volume_reason'] = VR_NO_VOLUME
            return
        if row['unknown_days']:
            # [REASON]: сутки «неизвестно» -- это не «работы не было»: по ним
            # гектары не посчитаны, и «по GPS меньше, чем в заявке» было бы
            # ложным обвинением.
            row['volume_reason'] = VR_DAYS_UNKNOWN
            return
        window_days = list(days_between(*app.window))
        neighbours = [other for other in self.live_apps_of(row['equipment_id'])
                      if other is not app
                      and any(self.may_cover(other, day) for day in window_days)]
        if neighbours:
            # [REASON]: гектары суток, которые могла покрыть и другая заявка,
            # к одной из них не отнести без правила распределения, а его
            # владелец не давал -- светофора нет, есть номера и, если можно,
            # общие числа цепочки.
            neighbours.sort(key=lambda a: (a.created_day, a.number))
            row['volume_neighbours'] = neighbours
            if any(a.status in records.OPEN_STATUSES for a in neighbours):
                row['volume_reason'] = VR_OPEN
            elif any(a.window is None for a in neighbours):
                row['volume_reason'] = VR_WINDOW_UNKNOWN
            else:
                row['volume_reason'] = VR_OVERLAP
                row['volume_group'] = self.volume_group(app, row)
            return
        verdict, deviation, share = tolerance.verdict_for_ga(row['volume'],
                                                             row['gps_ha'])
        row['volume_verdict'] = verdict
        row['volume_deviation'] = round(deviation, 3)
        row['volume_share'] = share

    def volume_group(self, app, row):
        """Общие числа цепочки заявок с пересекающимися окнами, или None.

        Цепочка -- заявки той же машины, связанные пересечением окон. Числа
        показываются, только если каждая заявка цепочки сравнима (метод
        «гектары», единица «гектар», объём есть), ни одни сутки общего окна не
        могла покрыть заявка без окна и все сутки общего окна известны.
        """
        apps = [a for a in self.live_apps_of(row['equipment_id']) if a.window]
        members = {app.id: app}
        todo = [app]
        while todo:
            current = todo.pop()
            for other in apps:
                if other.id in members:
                    continue
                if (other.window[0] <= current.window[1]
                        and current.window[0] <= other.window[1]):
                    members[other.id] = other
                    todo.append(other)
        first = min(a.window[0] for a in members.values())
        last = max(a.window[1] for a in members.values())
        total = 0.0
        for member in members.values():
            volume = parse_volume(member.row.get('volume'))
            if (self.methods.get(member.work_type_id) != methods.METHOD_GA
                    or member.row.get('unit') != UNIT_HECTARE
                    or volume is None):
                return None
            total += volume
        days = list(days_between(first, last))
        for other in self.live_apps_of(row['equipment_id']):
            if other.id not in members and not other.window and any(
                    self.may_cover(other, day) for day in days):
                return None
        hectares = 0.0
        for day in days:
            state, area = self.state(row['wialon_id'], day)
            if state == UNKNOWN:
                return None
            if state == WORK:
                hectares += area
        ordered = sorted(members.values(), key=lambda a: (a.created_day, a.number))
        return {'apps': ordered, 'first': first, 'last': last,
                'volume': round(total, 3), 'gps_ha': round(hectares, 3)}

    def forward_rows(self):
        rows = []
        for app in self.applications:
            if not self.in_period(app):
                continue
            row = self.forward_row(app)
            equipment_id = row['equipment_id']
            if equipment_id is None:
                # Машина не сопоставлена -- организации у нас нет, и видит
                # такую заявку только тот, кому видны все организации.
                if self.org_ids is not None:
                    continue
            elif not self.equipment_in_scope(equipment_id):
                continue
            rows.append(row)
        rows.sort(key=lambda r: (r['app'].created_day, r['app'].number))
        return rows

    # --- работа -> заявка -----------------------------------------------------------

    def coverage(self, equipment_id, day):
        """(покрытие, причина, покрывающие заявки) для суток с работой."""
        transports = self.transports_by_equipment.get(equipment_id, [])
        if not transports:
            return C_NONE, R_NOT_IN_AGRO, []
        if len(transports) > 1:
            return C_NONE, R_MANY_AGRO, []
        # Ни отменённая, ни удалённая в agro-work заявка работу не покрывает
        # (ответ 4 и ответ 11 владельца) -- и не откладывает вердикт.
        apps = [a for a in self.apps_by_transport.get(transports[0], [])
                if a.status != records.STATUS_CANCELLED and not a.gone]
        covering = [a for a in apps if a.window
                    and a.window[0] <= day <= a.window[1]]
        if covering:
            return C_COVERED, None, covering
        opened = [a for a in apps if a.status in records.OPEN_STATUSES
                  and a.created_day <= day]
        if opened:
            return C_NONE, R_OPEN_COVERS, opened
        unknown = [a for a in apps if a.window is None
                   and a.window_reason in UNKNOWN_WINDOW_REASONS
                   and a.could_cover(day, self.lookback)]
        if unknown:
            return C_NONE, R_WINDOW_UNKNOWN, unknown
        backdated = [a for a in apps if a.window_reason == R_BACKDATED
                     and a.created_day >= day]
        if backdated:
            return C_NONE, R_MAYBE_BACKDATED, backdated
        return C_UNCOVERED, None, []

    def reverse_rows(self):
        """Машино-сутки периода с работой по GPS и их покрытие заявками."""
        rows = []
        last = min(self.date_to, self.today)
        for equipment_id, units in sorted(self.wialon_by_equipment.items()):
            if equipment_id not in self.equipment:
                continue
            if not self.equipment_in_scope(equipment_id):
                continue
            live = [u for u in units if u not in self.excluded]
            if not live:
                continue
            for day in days_between(self.date_from, last):
                states = [self.state(u, day) for u in live]
                worked = [(u, ha) for u, (state, ha) in zip(live, states)
                          if state == WORK]
                if not worked:
                    continue
                row = {'equipment_id': equipment_id, 'day': day,
                       'wialon_id': worked[0][0] if len(live) == 1 else None,
                       'gps_ha': round(sum(ha for _, ha in worked), 3)
                       if len(live) == 1 else None,
                       'coverage': C_NONE, 'reason': None, 'apps': [],
                       'late_app': None, 'late_days': None,
                       'after': None, 'later_app': None, 'later_days': None}
                if len(live) > 1:
                    # [REASON]: у машины несколько объектов Wialon -- сверка
                    # нарядов GPS называет это причиной, а не складывает и не
                    # выбирает; здесь так же.
                    row['reason'] = R_MANY_OBJECTS
                else:
                    coverage, reason, apps = self.coverage(equipment_id, day)
                    row.update({'coverage': coverage, 'reason': reason,
                                'apps': apps})
                    if coverage == C_UNCOVERED:
                        late = self.late_backdated(equipment_id, day)
                        if late:
                            row['late_app'], row['late_days'] = late
                        after, app, days = self.what_came_after(
                            equipment_id, day, late)
                        row['after'] = after
                        if after in (A_ORDINARY, A_UNCLEAR):
                            row['later_app'], row['later_days'] = app, days
                rows.append(row)
        return rows

    def late_backdated(self, equipment_id, day):
        """(заявка, суток) -- ближайшая поздняя заявка задним числом, или None.

        Заявка той же машины, заведённая сразу «выполненной» позже суток
        работы. Сутки «без заявки» она не покрыла -- значит, введена позже,
        чем на N суток, -- и вердикт не меняет; её только называют.
        """
        transports = self.transports_by_equipment.get(equipment_id, [])
        if len(transports) != 1:
            return None
        # [REASON]: только заявки, заведённые задним числом (сразу
        # «выполнено», есть история): о них решение владельца 30.09. Обычная
        # заявка, созданная после работы, покрывает с дня создания
        # (правило 4) -- её «опоздание» здесь не выдумывается. Окно у заявки
        # задним числом есть, только пока она не удалена в agro-work (ответ
        # 11) и N утверждено; без окна она поздней не называется.
        late = [a for a in self.apps_by_transport.get(transports[0], [])
                if a.backdated and a.window and a.created_day > day]
        if not late:
            return None
        app = min(late, key=lambda a: (a.created_day, a.number))
        return app, (app.created_day - day).days

    def what_came_after(self, equipment_id, day, late=_DEFAULT):
        """(что было потом, заявка, суток) у суток «работа без заявки» (B5).

        Поздняя заявка задним числом (B4) -- первой: из-за неё сутки и так
        названы. Иначе -- ближайшая заявка той же машины, созданная позже
        суток, не отменённая, не удалённая в agro-work и не заведённая
        задним числом. `late` -- уже найденный ответ late_backdated, чтобы не
        искать дважды.
        """
        if late is _DEFAULT:
            late = self.late_backdated(equipment_id, day)
        if late:
            return A_BACKDATED, late[0], late[1]
        transports = self.transports_by_equipment.get(equipment_id, [])
        if len(transports) != 1:
            return None, None, None
        # [REASON]: отменённая и удалённая в agro-work заявка работу не
        # покрывает (ответы владельца 4 и 11) -- значит, и заведённой работой
        # её не считать. Заявка задним числом -- забота B4: с окном она уже
        # названа поздней, без окна (N не утверждено) сутки «без заявки» до
        # неё не бывает -- они без вердикта.
        later = [a for a in self.apps_by_transport.get(transports[0], [])
                 if a.created_day > day and not a.gone and not a.backdated
                 and a.status != records.STATUS_CANCELLED]
        # [REASON]: «обычная» -- только та, о которой это известно: открытая
        # или закрытая с историей статусов, где начальный статус не
        # «выполнено». Без истории или с незнакомым статусом порядок ввода
        # неизвестен; такая заявка не делает сутки ни «обычной позже», ни
        # «не заведена совсем» -- отдельная часть, названная как есть.
        known = [a for a in later if a.status in records.OPEN_STATUSES
                 or (a.status == records.STATUS_COMPLETED and a.has_history)]
        pool, kind = (known, A_ORDINARY) if known else (later, A_UNCLEAR)
        if not pool:
            return A_NOT_ENTERED, None, None
        app = min(pool, key=lambda a: (a.created_day, a.number))
        return kind, app, (app.created_day - day).days

    def open_applications(self):
        """Открытые заявки периода, от самой старой: суток с ввода."""
        rows = []
        for app in self.applications:
            if app.status not in records.OPEN_STATUSES or app.gone:
                continue
            if not self.in_period(app):
                continue
            equipment_id = self.machine_of(app.transport_id)[0]
            if equipment_id is None:
                # Как в «заявка -> работа»: без сопоставленной машины
                # организации у нас нет, такую видит только тот, кому видны
                # все организации.
                if self.org_ids is not None:
                    continue
            elif not self.equipment_in_scope(equipment_id):
                continue
            rows.append({'app': app, 'equipment_id': equipment_id,
                         'days_open': (self.today - app.created_day).days})
        rows.sort(key=lambda r: (-r['days_open'], r['app'].number))
        return rows

    def orphan_work_days(self):
        """Объекто-сутки с работой у объектов без машины в справочнике."""
        known = set(self.equipment_by_wialon)
        count = 0
        for (wialon_id, day), (state, _) in self.gps.items():
            if (state == WORK and wialon_id not in known
                    and wialon_id not in self.excluded
                    and self.date_from <= day <= self.date_to):
                count += 1
        return count

    # --- свод -------------------------------------------------------------------------

    def group_of(self, equipment_id):
        item = self.equipment.get(equipment_id) if equipment_id else None
        if item is None:
            return (None, None)
        return (item['organization_id'], item['category'])

    def summary(self, forward=None, reverse=None):
        """{(организация, категория): счётчики}, плюс итог под ключом 'all'."""
        forward = self.forward_rows() if forward is None else forward
        reverse = self.reverse_rows() if reverse is None else reverse
        groups = defaultdict(Counter)
        machines = defaultdict(set)
        for row in forward:
            key = self.group_of(row['equipment_id'])
            counter = groups[key]
            counter['applications'] += 1
            if row['verdict'] == V_NONE:
                counter['app_' + V_NONE] += 1
                counter['app_reason_' + row['reason']] += 1
            else:
                counter['app_' + row['verdict']] += 1
            # [REASON]: своё имя, а не `key`: `key` -- строка «Свода», по ней
            # считаются машины строки. В выпуске v1.25 светофор занял `key`, и
            # машина заявки без суток работы по GPS уходила из своей строки
            # (на рабочей базе 152 по строкам при «Итого» 246).
            volume = volume_key(row)
            if volume:
                counter[VOL + volume] += 1
                if volume == VOL_NONE:
                    counter[VOL_REASON + row['volume_reason']] += 1
            if row['equipment_id']:
                machines[key].add(row['equipment_id'])
        for row in reverse:
            key = self.group_of(row['equipment_id'])
            counter = groups[key]
            counter['work_days'] += 1
            if row['coverage'] == C_NONE:
                counter['day_' + C_NONE] += 1
                counter['day_reason_' + row['reason']] += 1
            else:
                counter['day_' + row['coverage']] += 1
            if row.get('late_app') is not None:
                counter[DAY_LATE] += 1
            if row.get('after'):
                counter[DAY_AFTER + row['after']] += 1
            machines[key].add(row['equipment_id'])
        total = Counter()
        for key, counter in groups.items():
            counter['machines'] = len(machines[key])
            total.update(counter)
        total['machines'] = len(set().union(*machines.values())) if machines else 0
        return dict(groups), total

    def machine_days(self, equipment_id):
        """Сутки периода одной машины: состояние GPS и заявки на эти сутки."""
        units = [u for u in self.wialon_by_equipment.get(equipment_id, [])]
        transports = self.transports_by_equipment.get(equipment_id, [])
        apps = []
        for transport_id in transports:
            apps.extend(self.apps_by_transport.get(transport_id, []))
        out = []
        for day in days_between(self.date_from, self.date_to):
            states = [(u, self.state(u, day)) for u in units]
            on_day = []
            for app in apps:
                start, end = app.listed_span
                if app.window:
                    start, end = app.window
                if start <= day <= end:
                    on_day.append(app)
            coverage = None
            late = None
            after = None
            if any(state == WORK for _, (state, _) in states) and len(units) == 1 \
                    and units[0] not in self.excluded:
                coverage = self.coverage(equipment_id, day)
                if coverage[0] == C_UNCOVERED:
                    late = self.late_backdated(equipment_id, day)
                    after = self.what_came_after(equipment_id, day, late)
            out.append({'day': day, 'states': states, 'apps': on_day,
                        'coverage': coverage, 'late': late, 'after': after})
        return out


def volume_key(row):
    """Светофор объёма строки «заявка -> работа» или VOL_NONE; None -- не
    сравнивается вовсе (вердикт не «работа была»)."""
    if row['verdict'] != V_WORK:
        return None
    return row['volume_verdict'] or VOL_NONE


def measure_lags(con, today=None):
    """Сколько суток от последнего дня работы по GPS до отметки «Выполнено».

    Для предложения владельцу числа N (вопрос 4). Меряется на ОБЫЧНЫХ
    закрытых заявках (не заведённых сразу выполненными), у которых машина
    годна для сверки и в окне есть хотя бы одни сутки с работой. Заявка, у
    которой после последнего дня работы в окне есть сутки «неизвестно», в
    замер не входит: работа могла идти и в них, и лаг был бы завышен.

    Выборки: все такие заявки; только заявки с единицей HECTARE из API (это
    фильтр замера, не метод); только размеченные методом «гектары», если
    разметка есть. Решение -- за владельцем, числа -- рядом.
    """
    today = today or date.today()
    first = date(2000, 1, 1)
    ctx = Reconciliation(con, first, today, lookback=None, today=today)
    samples = {'all': [], 'unit_hectare': [], 'method_ga': []}
    excluded = Counter()
    for app in ctx.applications:
        if app.backdated or app.window is None:
            continue
        equipment_id, wialon_id, reason = ctx.machine_of(app.transport_id)
        if reason:
            excluded[reason] += 1
            continue
        states = [(day, ctx.state(wialon_id, day)[0])
                  for day in days_between(*app.window)]
        work_days = [day for day, state in states if state == WORK]
        if not work_days:
            excluded['net_raboty_v_okne'] += 1
            continue
        last_work = work_days[-1]
        if any(state == UNKNOWN for day, state in states if day > last_work):
            excluded['neizvestnye_sutki_posle_raboty'] += 1
            continue
        lag = (app.window[1] - last_work).days
        samples['all'].append(lag)
        if app.row.get('unit') == UNIT_HECTARE:
            samples['unit_hectare'].append(lag)
        if ctx.methods.get(app.work_type_id) == methods.METHOD_GA:
            samples['method_ga'].append(lag)
    return {name: describe(values) for name, values in samples.items()}, excluded


def percentile(sorted_values, share):
    """Ближайший ранг: значение, не меньше которого `share` выборки."""
    if not sorted_values:
        return None
    rank = max(1, -(-int(round(share * 1000)) * len(sorted_values) // 1000))
    return sorted_values[min(rank, len(sorted_values)) - 1]


def describe(values):
    ordered = sorted(values)
    out = {'n': len(ordered), 'median': percentile(ordered, 0.5),
           'p75': percentile(ordered, 0.75), 'p90': percentile(ordered, 0.9),
           'p95': percentile(ordered, 0.95),
           'max': ordered[-1] if ordered else None,
           'histogram': dict(sorted(Counter(ordered).items()))}
    # Доля заявок, которую покрыло бы окно «день ввода и N дней до него».
    out['covered_by_n'] = {n: (sum(1 for v in ordered if v <= n) / len(ordered))
                           if ordered else None for n in range(0, 15)}
    return out
