# -*- coding: utf-8 -*-
"""Из ответа API agro-work -- в строки наших таблиц. Только stdlib.

СПИСОК РАЗРЕШЁННЫХ ПОЛЕЙ, А НЕ ФИЛЬТР ЗАПРЕЩЁННЫХ
Каждая запись собирается поимённо из тех полей, которые нужны сверке. Всё
остальное из ответа не читается вовсе -- в том числе то, что владелец 28.09
запретил хранить (вопрос 6): `farm_info.owner_name`, `farm_info.owner_phone`,
`transport_info.driver_name`, `created_by_name`, `updated_by_name`. Фильтр
«вырезать запрещённое» пропустил бы поле, которое завтра появится в ответе
под новым именем; список разрешённого -- нет.

Название хозяйства `farm_info.name` не берётся тоже -- ответ владельца на
вопрос 8 (28.09): не хранить. Не берутся и поля, которые сверке не нужны, но
могут нести персональные данные: `comment` (свободный текст операторов),
`driver_phone` реестра машин, `created_by`/`updated_by`.

ДАТЫ. `created_at`, `updated_at` и `changed_at` хранятся строкой ровно как
пришли, а сутки выводятся переводом в UTC+5. Строка без пояса -- отказ, а не
догадка: сутки, посчитанные не в том поясе, сдвигают вечерние заявки на
соседний день молча.
"""

import json
import re
from datetime import datetime

from plate_norm import normalize_plate

from . import config

UUID_RE = re.compile(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-'
                     r'[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')

STATUS_PENDING = 'PENDING'
STATUS_IN_PROGRESS = 'IN_PROGRESS'
STATUS_COMPLETED = 'COMPLETED'
STATUS_CANCELLED = 'CANCELLED'
OPEN_STATUSES = (STATUS_PENDING, STATUS_IN_PROGRESS)
CLOSED_STATUSES = (STATUS_COMPLETED, STATUS_CANCELLED)

ACTION_CREATED = 'created'

# Поля заявки, которые импорт сравнивает и пишет в журнал при изменении.
# `id` -- ключ, в сравнение не входит.
APPLICATION_FIELDS = (
    'application_number', 'company_id', 'company_key', 'company_name',
    'transport_id', 'plate_number', 'farm_id', 'work_type_id',
    'work_type_name', 'unit', 'volume', 'unit_price', 'total_amount',
    'payment_type', 'with_fuel', 'status', 'start_time', 'end_time',
    'is_active', 'created_at', 'updated_at', 'created_day')

TRANSPORT_FIELDS = ('plate_number', 'plate_norm', 'brand_name', 'model',
                    'category_name', 'company_id', 'company_name')

WORK_TYPE_FIELDS = ('name', 'unit', 'unit_display')

MAX_CHANGED_FIELDS = 30


class Rejected(ValueError):
    """Строку хранить нельзя. Текст -- ASCII, без значений полей."""


def _text(value, limit):
    """Строка без краевых пробелов, не длиннее limit; пустое -- None."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:limit]


def _number_text(value):
    """Число как строка, как его отдал API: «7.00» остаётся «7.00».

    [REASON]: DRF отдаёт десятичные поля строкой. Храня строку, журнал
    изменений сравнивает ровно то, что пришло, и не записывает «правку»
    7.0 -> 7.00, которой в agro-work не было.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return repr(value)
    return _text(value, 40)


def _flag(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return 1 if value else 0
    text = str(value).strip().lower()
    if text in ('true', '1', 'yes'):
        return 1
    if text in ('false', '0', 'no'):
        return 0
    return None


def parse_moment(value):
    """ISO 8601 С ПОЯСОМ -> datetime с поясом; иначе None."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith('Z') or text.endswith('z'):
        text = text[:-1] + '+00:00'
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None or moment.utcoffset() is None:
        return None
    return moment


def local_day(moment):
    """Местные сутки (UTC+5) момента с поясом."""
    return moment.astimezone(config.TZ).date()


def _moment(row, key):
    value = row.get(key)
    moment = parse_moment(value)
    if moment is None:
        raise Rejected('%s is missing or has no time zone' % key)
    return value.strip(), moment


def _info(row, key):
    value = row.get(key)
    return value if isinstance(value, dict) else {}


def application_record(row):
    """Строка agro_work_applications из строки списка `/applications/`."""
    if not isinstance(row, dict):
        raise Rejected('row is not an object')
    app_id = row.get('id')
    if not isinstance(app_id, str) or not UUID_RE.match(app_id.strip()):
        raise Rejected('id is missing or is not a UUID')
    number = _text(row.get('application_number'), 40)
    if not number:
        raise Rejected('application_number is missing')
    status = _text(row.get('status'), 20)
    if not status:
        raise Rejected('status is missing')
    created_text, created = _moment(row, 'created_at')
    updated_text, _ = _moment(row, 'updated_at')
    transport_info = _info(row, 'transport_info')
    work_type_info = _info(row, 'work_type_info')
    return {
        'id': app_id.strip().lower(),
        'application_number': number,
        'company_id': _text(row.get('company'), 36),
        'company_key': _text(row.get('company_key'), 10),
        'company_name': _text(row.get('company_name'), 300),
        'transport_id': _text(row.get('transport'), 36),
        'plate_number': _text(transport_info.get('plate_number'), 40),
        'farm_id': _text(row.get('farm'), 36),
        'work_type_id': _text(row.get('work_type'), 36),
        'work_type_name': _text(work_type_info.get('name'), 300),
        'unit': _text(work_type_info.get('unit'), 20),
        'volume': _number_text(row.get('volume')),
        'unit_price': _number_text(row.get('unit_price_snapshot')),
        'total_amount': _number_text(row.get('total_amount')),
        'payment_type': _text(row.get('payment_type'), 20),
        'with_fuel': _flag(row.get('with_fuel')),
        'status': status,
        'start_time': _text(row.get('start_time'), 40),
        'end_time': _text(row.get('end_time'), 40),
        'is_active': _flag(row.get('is_active')),
        'created_at': created_text,
        'updated_at': updated_text,
        'created_day': local_day(created).isoformat(),
    }


def transport_from_registry(row):
    """Строка agro_work_transports из строки реестра `/transports/`.

    Водитель и его телефон в реестре есть у всех машин -- и не берутся.
    """
    if not isinstance(row, dict):
        raise Rejected('row is not an object')
    transport_id = _text(row.get('id'), 36)
    if not transport_id:
        raise Rejected('id is missing')
    plate = _text(row.get('plate_number'), 40)
    if not plate:
        raise Rejected('plate_number is missing')
    return {
        'id': transport_id,
        'plate_number': plate,
        'plate_norm': normalize_plate(plate),
        'brand_name': _text(row.get('brand_name'), 120),
        'model': _text(row.get('model'), 120),
        'category_name': _text(row.get('category_name'), 120),
        'company_id': _text(row.get('company'), 36),
        'company_name': _text(row.get('company_name'), 300),
    }


def transport_from_application(row):
    """Машина, какой её видит заявка -- для машин, которых нет в реестре.

    [REASON]: предприятие машины здесь НЕ берётся из заявки: машина одного
    предприятия может работать по заявке другого, и записать чужое
    предприятие машине значило бы угадать. Его знает только реестр.
    """
    info = _info(row, 'transport_info')
    transport_id = _text(row.get('transport'), 36)
    plate = _text(info.get('plate_number'), 40)
    if not transport_id or not plate:
        return None
    return {
        'id': transport_id,
        'plate_number': plate,
        'plate_norm': normalize_plate(plate),
        'brand_name': _text(info.get('brand_name'), 120),
        'model': _text(info.get('model'), 120),
        'category_name': _text(info.get('category_name'), 120),
        'company_id': None,
        'company_name': None,
    }


def work_type_record(row):
    """Строка agro_work_work_types из `/work-types/`. Метод сюда не входит:
    его ставит владелец (B2), импорт его не знает и не трогает."""
    if not isinstance(row, dict):
        raise Rejected('row is not an object')
    work_type_id = _text(row.get('id'), 36)
    name = _text(row.get('name'), 300)
    if not work_type_id or not name:
        raise Rejected('id or name is missing')
    return {
        'id': work_type_id,
        'name': name,
        'unit': _text(row.get('unit'), 20),
        'unit_display': _text(row.get('unit_display'), 60),
    }


def work_type_from_application(row):
    info = _info(row, 'work_type_info')
    work_type_id = _text(row.get('work_type'), 36)
    name = _text(info.get('name'), 300)
    if not work_type_id or not name:
        return None
    return {
        'id': work_type_id,
        'name': name,
        'unit': _text(info.get('unit'), 20),
        'unit_display': _text(info.get('unit_display'), 60),
    }


def changed_field_names(changes):
    """ИМЕНА изменённых полей события истории, без значений.

    [REASON]: в `changes` лежат старое и новое значение. Сменённый фермер
    принёс бы к нам оба имени, сменённая машина -- водителей. Имена полей
    говорят, ЧТО правили после создания (B0: объём, сумма, цена, машина), и
    этого для журнала достаточно.
    """
    names = []
    if isinstance(changes, dict):
        names = [k for k in changes if isinstance(k, str)]
    elif isinstance(changes, list):
        for item in changes:
            if isinstance(item, str):
                names.append(item)
            elif isinstance(item, dict):
                for key in ('field', 'name', 'key'):
                    if isinstance(item.get(key), str):
                        names.append(item[key])
                        break
    clean = sorted({name.strip()[:60] for name in names if name.strip()})
    return clean[:MAX_CHANGED_FIELDS]


def history_event(event):
    """Одно событие истории статусов в строку agro_work_status_events."""
    if not isinstance(event, dict):
        raise Rejected('event is not an object')
    changed_text, _ = _moment(event, 'changed_at')
    fields = changed_field_names(event.get('changes'))
    return {
        'action': _text(event.get('action'), 30) or '',
        'old_status': _text(event.get('old_status'), 20) or '',
        'new_status': _text(event.get('new_status'), 20) or '',
        'changed_at': changed_text,
        'changed_fields': json.dumps(fields, ensure_ascii=False) if fields else None,
    }


def derive_dates(events):
    """(начальный статус, момент выполнения, сутки выполнения, момент отмены).

    Считается по ВСЕМ сохранённым событиям заявки:
      - начальный статус -- `new_status` события `created`; нет такого
        события -- неизвестен (None), а не «в процессе»;
      - выполнение -- ПОСЛЕДНИЙ переход в COMPLETED. [REASON]: заявку,
        которую вернули в работу и закрыли снова, закрыло второе закрытие:
        работа по ней шла и между ними;
      - отмена -- последний переход в CANCELLED.
    """
    parsed = []
    for event in events:
        moment = parse_moment(event.get('changed_at'))
        if moment is None:
            continue
        parsed.append((moment, event))
    parsed.sort(key=lambda pair: pair[0])
    initial = None
    completed = cancelled = None
    for moment, event in parsed:
        if event.get('action') == ACTION_CREATED and initial is None:
            initial = event.get('new_status') or None
        if event.get('new_status') == STATUS_COMPLETED:
            completed = (moment, event.get('changed_at'))
        if event.get('new_status') == STATUS_CANCELLED:
            cancelled = (moment, event.get('changed_at'))
    return (initial,
            completed[1] if completed else None,
            local_day(completed[0]).isoformat() if completed else None,
            cancelled[1] if cancelled else None)
