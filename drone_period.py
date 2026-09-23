# -*- coding: utf-8 -*-
"""drone_period.py -- общий фильтр «дата+время» модуля Дроны.

DRONE-AREA-CONTROL-V2-MEGA, блок C. Одна семантика периода на все отчёты
модуля, у которых строка несёт МОМЕНТ (вылет DJI, запись площади DJI):

* пользователь выбирает «Дата/время с» и «Дата/время по» с точностью до
  минуты, в бизнес-поясе UTC+5 (Asia/Tashkent, без летнего времени);
* по умолчанию время -- 00:00 и 23:59, то есть целые дни;
* хранение -- UTC, поэтому границы сдвигаются на 5 часов;
* конец периода -- ПОЛУОТКРЫТЫЙ: «по 23:59» значит «< 23:59 + 1 минута».

[REASON]: полуоткрытая граница, а не «<= 23:59». Вылеты записаны с
секундами; запись в 23:59:41 при закрытой границе «<= 23:59:00» выпала бы из
отчёта молча. Прежний код отчётов вылетов брал «<= 23:59:59.999999» -- верно
для целого дня, но при минутной точности граница обязана быть
«меньше следующей минуты».

[REASON]: границы в SQL передаются СТРОКОЙ из 19 символов, а не datetime.
SQLAlchemy кладёт datetime в SQLite как 'YYYY-MM-DD HH:MM:SS.ffffff', а
строки, записанные stdlib-писателями (`dji_area/store.py`), -- без дробной
части. Сравнение строк у самой секунды тогда врёт: '... 19:00:00' >=
'... 19:00:00.000000' ложно, и запись ровно в 00:00 местного пропадала бы.
Строка без дробной части сравнивается верно с обоими форматами -- так уже
делают `dji_area/pipeline.py` и `drone_coverage_recalc.py`.

Даты сохраняют прежнюю семантику модуля, на которую опираются тесты:
отсутствие ключа -- окно по умолчанию отчёта, пустой ключ -- «без границы»,
неразборчивая дата -- «без границы». Время новое и строгое: неразборчивое
время не угадывается, а называется ошибкой и заменяется значением по
умолчанию с предупреждением.

Здесь нет Flask и базы: модуль импортируют и страницы, и тесты.
"""

import re
from datetime import date, datetime, time, timedelta

BUSINESS_UTC_OFFSET = timedelta(hours=5)
DEFAULT_TIME_FROM = time(0, 0)
DEFAULT_TIME_TO = time(23, 59)
ONE_MINUTE = timedelta(minutes=1)

KEY_DATE_FROM = 'date_from'
KEY_DATE_TO = 'date_to'
KEY_TIME_FROM = 'time_from'
KEY_TIME_TO = 'time_to'

_TIME_RE = re.compile(r'^([01]\d|2[0-3]):([0-5]\d)(?::([0-5]\d))?$')

WARN_TIME_FROM = 'time_from'
WARN_TIME_TO = 'time_to'
WARN_TIME_FROM_UNANCHORED = 'time_from_unanchored'
WARN_TIME_TO_UNANCHORED = 'time_to_unanchored'

MESSAGES = {
    'inverted': ('Начало периода позже его конца — за такой период записей '
                 'нет. Проверьте дату и время.',
                 'Давр бошланиши тугашидан кейин — бундай даврда ёзувлар йўқ. '
                 'Сана ва вақтни текширинг.'),
    WARN_TIME_FROM: ('Время «с» указано неверно; использовано 00:00.',
                     '«дан» вақти нотўғри; 00:00 ишлатилди.'),
    WARN_TIME_TO: ('Время «по» указано неверно; использовано 23:59.',
                   '«гача» вақти нотўғри; 23:59 ишлатилди.'),
    WARN_TIME_FROM_UNANCHORED: (
        'Время «с» не применено: у начала периода нет даты.',
        '«дан» вақти қўлланмади: давр бошида сана йўқ.'),
    WARN_TIME_TO_UNANCHORED: (
        'Время «по» не применено: у конца периода нет даты.',
        '«гача» вақти қўлланмади: давр охирида сана йўқ.'),
}


def parse_date(value):
    """ISO-дата либо None. Неразборчивое -- None (прежняя семантика модуля)."""
    if not value:
        return None
    try:
        return datetime.strptime(str(value).strip(), '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None


def parse_time(value, default):
    """(time, ok). Пустое -- значение по умолчанию и ok. Секунды отбрасываются:
    точность интерфейса -- минута."""
    text = (value or '').strip() if isinstance(value, str) else ''
    if not text:
        return default, True
    match = _TIME_RE.match(text)
    if not match:
        return default, False
    return time(int(match.group(1)), int(match.group(2))), True


def fmt_time(value):
    return value.strftime('%H:%M') if value is not None else ''


def to_utc(local_dt):
    return None if local_dt is None else local_dt - BUSINESS_UTC_OFFSET


def to_local(utc_dt):
    return None if utc_dt is None else utc_dt + BUSINESS_UTC_OFFSET


def sql_text(dt):
    """19-символьная строка для сравнения с TEXT-колонкой SQLite."""
    return None if dt is None else dt.strftime('%Y-%m-%d %H:%M:%S')


def _get(args, key):
    getter = getattr(args, 'get', None)
    return getter(key) if getter is not None else None


def _has(args, key):
    try:
        return key in args
    except TypeError:
        return False


def parse(args, default_window=None, with_time=True):
    """Период из query-string.

    ``default_window`` -- (date_from, date_to), подставляемые, когда НИ ОДНОГО
    ключа даты в запросе нет; None -- «за всё время». ``with_time=False`` --
    для источников, у строки которых нет времени суток (целый местный день):
    время тогда не читается и в интерфейсе не показывается.

    Возвращает словарь; ключи дат совпадают с прежними фильтрами модуля,
    поэтому словарь вливается в них без переименований.
    """
    has_date_args = _has(args, KEY_DATE_FROM) or _has(args, KEY_DATE_TO)
    date_from_s = (_get(args, KEY_DATE_FROM) or '').strip()
    date_to_s = (_get(args, KEY_DATE_TO) or '').strip()
    date_from = parse_date(date_from_s)
    date_to = parse_date(date_to_s)
    if not has_date_args and default_window is not None:
        date_from, date_to = default_window
        date_from_s = date_from.isoformat() if date_from else ''
        date_to_s = date_to.isoformat() if date_to else ''

    warnings = []
    if with_time:
        time_from, ok_from = parse_time(_get(args, KEY_TIME_FROM),
                                        DEFAULT_TIME_FROM)
        time_to, ok_to = parse_time(_get(args, KEY_TIME_TO), DEFAULT_TIME_TO)
        if not ok_from:
            warnings.append(WARN_TIME_FROM)
        if not ok_to:
            warnings.append(WARN_TIME_TO)
    else:
        time_from, time_to = DEFAULT_TIME_FROM, DEFAULT_TIME_TO
    # [REASON]: время привязано к дате своей стороны. Без даты оно ничего не
    # ограничивает -- и говорится об этом прямо, а не молча: иначе поле
    # «по 10:00» стояло бы в форме, а в отчёт попадали бы вылеты 15:00.
    if with_time and time_from != DEFAULT_TIME_FROM and date_from is None:
        warnings.append(WARN_TIME_FROM_UNANCHORED)
    if with_time and time_to != DEFAULT_TIME_TO and date_to is None:
        warnings.append(WARN_TIME_TO_UNANCHORED)

    out = {
        'date_from': date_from,
        'date_to': date_to,
        'date_from_s': date_from_s,
        'date_to_s': date_to_s,
        'has_date_args': has_date_args,
        'time_from': time_from,
        'time_to': time_to,
        'with_time': with_time,
        'period_warnings': warnings,
    }
    out.update(derive(out))
    return out


def derive(filters):
    """Производные границы из date_from/date_to/time_from/time_to.

    Ключей времени может не быть: словарь, собранный вручную (так делают
    тесты и старые вызовы), читается как «целые дни».
    """
    date_from = filters.get('date_from')
    date_to = filters.get('date_to')
    time_from = filters.get('time_from') or DEFAULT_TIME_FROM
    time_to = filters.get('time_to') or DEFAULT_TIME_TO
    start_local = (datetime.combine(date_from, time_from)
                   if isinstance(date_from, date) else None)
    end_local = (datetime.combine(date_to, time_to)
                 if isinstance(date_to, date) else None)
    end_excl_local = end_local + ONE_MINUTE if end_local else None
    inverted = bool(start_local and end_excl_local
                    and start_local >= end_excl_local)
    return {
        'time_from_s': fmt_time(time_from),
        'time_to_s': fmt_time(time_to),
        'time_is_default': (time_from == DEFAULT_TIME_FROM
                            and time_to == DEFAULT_TIME_TO),
        'start_local': start_local,
        'end_local': end_local,
        'utc_start': to_utc(start_local),
        'utc_end_excl': to_utc(end_excl_local),
        'period_inverted': inverted,
    }


def utc_bounds(filters):
    """(utc_start, utc_end_excl) -- любая из границ может быть None."""
    bounds = derive(filters)
    return bounds['utc_start'], bounds['utc_end_excl']


def contains_utc(filters, utc_dt):
    """Попадает ли UTC-момент в период: [start, end_excl)."""
    if utc_dt is None:
        return False
    start, end_excl = utc_bounds(filters)
    if start is not None and utc_dt < start:
        return False
    if end_excl is not None and utc_dt >= end_excl:
        return False
    return True


def report_dates(filters):
    """(date_from, date_to) отчётных дней UTC+5, покрывающих период.

    Отчётный день записи -- день её НАЧАЛА в UTC+5, поэтому время внутри дня
    не расширяет диапазон дат: грубый индексный фильтр по дате остаётся
    точным надмножеством, а минуты уточняются по моменту начала.
    """
    return filters.get('date_from'), filters.get('date_to')


def link_args(filters):
    """Ключи времени для ссылок и выгрузок -- только отличные от умолчания.

    [REASON]: даты ссылкам по-прежнему передают построители каждого отчёта
    (у них разные правила «пустой ключ против отсутствующего»). Время
    добавляется поверх и только когда оно не 00:00/23:59: старые ссылки и
    имена файлов при целых днях остаются байт в байт прежними.
    """
    out = {}
    if not filters.get('with_time', True):
        return out
    time_from = filters.get('time_from') or DEFAULT_TIME_FROM
    time_to = filters.get('time_to') or DEFAULT_TIME_TO
    if time_from != DEFAULT_TIME_FROM:
        out[KEY_TIME_FROM] = fmt_time(time_from)
    if time_to != DEFAULT_TIME_TO:
        out[KEY_TIME_TO] = fmt_time(time_to)
    return out


def echo(filters):
    """(с, по) текстом для строки «Период» выгрузки: 'YYYY-MM-DD HH:MM'.

    Пустая граница -- пустая строка («без границы»).
    """
    bounds = derive(filters)
    start, end = bounds['start_local'], bounds['end_local']
    return (start.strftime('%Y-%m-%d %H:%M') if start else '',
            end.strftime('%Y-%m-%d %H:%M') if end else '')


def filename_part(filters):
    """Суффикс имени файла: '' при целых днях, иначе '_HHMM-HHMM'.

    Двоеточие в имени файла Windows не допускает, поэтому минуты без него.
    """
    if not filters.get('with_time', True):
        return ''
    # Только время, которое действительно ограничивает: у стороны без даты
    # его нет, и имя файла не должно его заявлять.
    time_from = filters.get('time_from') or DEFAULT_TIME_FROM
    time_to = filters.get('time_to') or DEFAULT_TIME_TO
    if not isinstance(filters.get('date_from'), date):
        time_from = DEFAULT_TIME_FROM
    if not isinstance(filters.get('date_to'), date):
        time_to = DEFAULT_TIME_TO
    if time_from == DEFAULT_TIME_FROM and time_to == DEFAULT_TIME_TO:
        return ''
    return '_%s-%s' % (time_from.strftime('%H%M'), time_to.strftime('%H%M'))


def label(filters, lang):
    """Период словами для заголовка экрана."""
    bounds = derive(filters)
    start, end = bounds['start_local'], bounds['end_local']
    fmt = '%d.%m.%Y %H:%M'
    if start is None and end is None:
        return 'за всё время' if lang == 'ru' else 'бутун давр учун'
    left = start.strftime(fmt) if start else '…'
    right = end.strftime(fmt) if end else '…'
    return '%s — %s' % (left, right)


def messages(filters, lang):
    """Предупреждения периода для показа пользователю."""
    out = []
    for code in filters.get('period_warnings') or ():
        pair = MESSAGES.get(code)
        if pair:
            out.append(pair[0] if lang == 'ru' else pair[1])
    if filters.get('period_inverted'):
        pair = MESSAGES['inverted']
        out.append(pair[0] if lang == 'ru' else pair[1])
    return out
