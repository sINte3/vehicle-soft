# -*- coding: utf-8 -*-
"""dji_area/v4.py -- декодер и сводка телеметрии DJI V4 (openapi.FlightRecordDetail).

Файл ``airline_v4`` -- protobuf wire format без схемы. Номера полей взяты из
публичной source map фронтенда SmartFarm (``flight-proto.js``, сообщение
``openapi.FlightData``) и подтверждены на 1396 августовских файлах в
исследовании; здесь они именуются только там, где это подтверждено.

ГЛАВНОЕ ПРАВИЛО ЭТОГО МОДУЛЯ: **присутствие поля на проводе отличается от
protobuf-значения по умолчанию.** Кадр, в котором поле 9 (состояние счётчика
площади) не закодировано, НЕ несёт нуля -- он не несёт ничего. Декодер отдаёт
словарь, в котором ключ есть только у закодированного поля, а сводка считает
отдельно ``counter_observed_delta`` (разность присутствующих концов) и
``counter_zero_default_delta`` (разность с подстановкой нуля). Второе --
диагностика, никогда не production-источник площади: случай 685264927
(первые 10 кадров без счётчика, затем константа 13.61) даёт 9073 м²
«новой работы» из ничего.

Единицы: поле 9 -- float32 в собственных единицах счётчика (кандидат -- му),
масштаб 2000/3 м² на единицу, наблюдаемый квант 0.01 единицы ≈ 6.667 м².

Здесь нет ввода-вывода, Flask и базы; только stdlib и ``walk``.
"""

import math
import struct

from drone_collector.route_decode import RouteDecodeError, walk

from dji_area import V4_PARSER_VERSION  # noqa: F401  (re-exported for callers)

# ─── Масштаб счётчика ────────────────────────────────────────────────────────

# м² на одну единицу поля 9. 1 му = 2000/3 м² (15 му = 1 га = 10 000 м²).
COUNTER_M2_PER_NATIVE = 2000.0 / 3.0
# Наблюдаемый квант счётчика в собственных единицах.
COUNTER_QUANTUM_NATIVE = 0.01
COUNTER_QUANTUM_M2 = COUNTER_QUANTUM_NATIVE * COUNTER_M2_PER_NATIVE

# [REASON]: float32 хранит 14.43 как 14.430000305...; сравнение «тот же
# квант» ведётся по округлению к 0.01, а равенство концов -- по битам. Порог
# ниже нужен только для отсечения численного шума при поиске отрицательных
# шагов; реальный спад счётчика -- не меньше кванта.
_NEGATIVE_STEP_EPS = 1e-6

# ─── Номера полей ────────────────────────────────────────────────────────────

TOP_COUNT = 1        # varint; кандидат «число кадров сессии», НЕ flight id
TOP_FRAME = 2        # repeated message
TOP_USAGE_TYPE = 3   # varint

F_TIME_MS = 1        # varint, unix ms
F_POSITION = 2       # {1: lng double, 2: lat double}
F_VELOCITY = 3       # {1: vx, 2: vy, 3: vz} float32
F_HEIGHT = 4         # float32
F_WORK_MODE = 5      # varint
F_SPRAY_WIDTH = 6    # float32, м
F_SPRAY = 7          # {1: flag/type, 2: flowSpeed, 3: residual, 4: side, 10: sprayedCap}
F_SOW = 8
F_AREA_STATE = 9     # float32, состояние счётчика площади (native)
F_DELIVERY = 10
F_STATUS = 11        # {2: batt, 3: relAlt, 4: rtk, 5: posMode, 6: windDir, 7: windSpeed, 8: rtkSat}

S_LNG = 1
S_LAT = 2
S_VX = 1
S_VY = 2
S_VZ = 3
S_SPRAY_FLAG = 1
S_FLOW = 2
S_RESIDUAL = 3
S_SPRAY_SIDE = 4
S_QUANTITY = 10

WIRE_VARINT = 0
WIRE_FIXED64 = 1
WIRE_BYTES = 2
WIRE_FIXED32 = 5

# Замороженные диагностики окна (спецификация §3, п. 3). Не пороги качества
# работы -- пороги применимости V4 к интервалу записи.
WINDOW_MIN_FRAMES = 2
WINDOW_MAX_OFFSET_S = 1.01
WINDOW_MAX_DT_S = 1.0

# Порог скорости для диагностики «движущегося применения», как в исследовании.
# Не порог физической полезности работы.
MOVING_APPLICATION_MIN_SPEED_MPS = 1.0


class V4DecodeError(ValueError):
    """Тело не разбирается как V4. Только для структуры, не для значений."""


def _f32(raw):
    if len(raw) != 4:
        raise V4DecodeError('fixed32 value of %d bytes' % len(raw))
    return struct.unpack('<f', raw)[0]


def _f64(raw):
    if len(raw) != 8:
        raise V4DecodeError('fixed64 value of %d bytes' % len(raw))
    return struct.unpack('<d', raw)[0]


def _signed32(value):
    """varint как знаковое 32-битное (расходомер шлёт отрицательные)."""
    if not isinstance(value, int):
        return value
    if (1 << 31) <= value < (1 << 32):
        return value - (1 << 32)
    if value >= (1 << 63):
        return value - (1 << 64)
    return value


def _walk(buf):
    try:
        return walk(buf)
    except RouteDecodeError as exc:
        raise V4DecodeError(str(exc))


def _first_of(fields, number, warnings):
    """Первое вхождение поля; повтор регистрируется, не отбрасывается молча."""
    found = [(wire, value) for num, wire, value in fields if num == number]
    if not found:
        return None
    if len(found) > 1:
        warnings.add('REPEATED_FIELD_%d' % number)
    return found[0]


def decode_frame(raw, warnings):
    """Один кадр -> словарь. Ключ есть ТОЛЬКО у закодированного поля.

    Значение поля 9 отдаётся дважды: ``area_native`` (float32 как Python
    float) и ``area_bits`` (4 байта в hex) -- биты хранятся, чтобы равенство
    концов и повтор расчёта не зависели от округления.
    """
    frame = {}
    fields = _walk(raw)
    seen_numbers = set()
    for number, wire, value in fields:
        seen_numbers.add(number)
    for number, wire, value in fields:
        if number == F_TIME_MS and wire == WIRE_VARINT:
            frame.setdefault('t', value)
        elif number == F_POSITION and wire == WIRE_BYTES:
            sub = _walk(value)
            lng = _first_of(sub, S_LNG, warnings)
            lat = _first_of(sub, S_LAT, warnings)
            if lng is not None and lng[0] == WIRE_FIXED64:
                frame.setdefault('lng', _f64(lng[1]))
            if lat is not None and lat[0] == WIRE_FIXED64:
                frame.setdefault('lat', _f64(lat[1]))
        elif number == F_VELOCITY and wire == WIRE_BYTES:
            sub = _walk(value)
            for key, sub_number in (('vx', S_VX), ('vy', S_VY), ('vz', S_VZ)):
                item = _first_of(sub, sub_number, warnings)
                if item is not None and item[0] == WIRE_FIXED32:
                    frame.setdefault(key, _f32(item[1]))
        elif number == F_HEIGHT and wire == WIRE_FIXED32:
            frame.setdefault('height', _f32(value))
        elif number == F_WORK_MODE and wire == WIRE_VARINT:
            frame.setdefault('mode', value)
        elif number == F_SPRAY_WIDTH and wire == WIRE_FIXED32:
            frame.setdefault('width', _f32(value))
        elif number == F_SPRAY and wire == WIRE_BYTES:
            sub = _walk(value)
            frame.setdefault('spray_block', True)
            for key, sub_number in (('spray_flag', S_SPRAY_FLAG),
                                    ('flow', S_FLOW),
                                    ('residual', S_RESIDUAL),
                                    ('spray_side', S_SPRAY_SIDE),
                                    ('quantity', S_QUANTITY)):
                item = _first_of(sub, sub_number, warnings)
                if item is None:
                    continue
                if item[0] == WIRE_VARINT:
                    frame.setdefault(key, _signed32(item[1]))
                elif item[0] == WIRE_FIXED32:
                    frame.setdefault(key, _f32(item[1]))
        elif number == F_SOW:
            frame.setdefault('sow', True)
        elif number == F_AREA_STATE and wire == WIRE_FIXED32:
            if 'area_native' in frame:
                warnings.add('REPEATED_FIELD_9')
                continue
            frame['area_native'] = _f32(value)
            frame['area_bits'] = bytes(value).hex()
        elif number == F_DELIVERY:
            frame.setdefault('delivery', True)
        elif number == F_STATUS and wire == WIRE_BYTES:
            sub = _walk(value)
            for key, sub_number, kind in (('battery', 2, 'int'),
                                          ('rel_alt', 3, 'f32'),
                                          ('rtk', 4, 'text'),
                                          ('pos_mode', 5, 'text'),
                                          ('wind_dir', 6, 'int'),
                                          ('wind_speed', 7, 'int'),
                                          ('rtk_sat', 8, 'int')):
                item = _first_of(sub, sub_number, warnings)
                if item is None:
                    continue
                if kind == 'int' and item[0] == WIRE_VARINT:
                    frame.setdefault(key, item[1])
                elif kind == 'f32' and item[0] == WIRE_FIXED32:
                    frame.setdefault(key, _f32(item[1]))
                elif kind == 'text' and item[0] == WIRE_BYTES:
                    frame.setdefault(key, item[1].decode('utf-8', 'replace'))
        else:
            # Неизвестное поле: номер сохраняется, значение -- нет.
            frame.setdefault('other', set()).add(number)
            warnings.add('UNKNOWN_FRAME_FIELD_%d' % number)
    return frame


class V4Decoded(object):
    """Разобранный файл V4: верхние поля и кадры в порядке следования."""

    __slots__ = ('top_count', 'usage_type', 'frames', 'warnings',
                 'top_other_fields', 'size_bytes')

    def __init__(self, top_count=None, usage_type=None, frames=None,
                 warnings=None, top_other_fields=None, size_bytes=0):
        self.top_count = top_count
        self.usage_type = usage_type
        self.frames = frames if frames is not None else []
        self.warnings = warnings if warnings is not None else set()
        self.top_other_fields = (top_other_fields
                                 if top_other_fields is not None else set())
        self.size_bytes = size_bytes


def decode_v4(buf):
    """Разобрать тело V4. Отказ -- V4DecodeError, никогда «что-то похожее»."""
    if not isinstance(buf, (bytes, bytearray, memoryview)):
        raise V4DecodeError('body is not bytes')
    buf = bytes(buf)
    if not buf:
        raise V4DecodeError('empty body')
    warnings = set()
    decoded = V4Decoded(size_bytes=len(buf), warnings=warnings)
    for number, wire, value in _walk(buf):
        if number == TOP_COUNT and wire == WIRE_VARINT:
            if decoded.top_count is not None:
                warnings.add('REPEATED_TOP_COUNT')
            else:
                decoded.top_count = value
        elif number == TOP_FRAME and wire == WIRE_BYTES:
            decoded.frames.append(decode_frame(value, warnings))
        elif number == TOP_USAGE_TYPE and wire == WIRE_VARINT:
            decoded.usage_type = value
        else:
            decoded.top_other_fields.add(number)
            warnings.add('UNKNOWN_TOP_FIELD_%d' % number)
    return decoded


# ─── Сводка ──────────────────────────────────────────────────────────────────

def _speed(frame):
    vx = frame.get('vx')
    vy = frame.get('vy')
    if vx is None or vy is None:
        return None
    return math.hypot(vx, vy)


def _distance_m(a, b):
    if any(k not in a or k not in b for k in ('lat', 'lng')):
        return None
    mid = math.radians((a['lat'] + b['lat']) / 2.0)
    dx = math.radians(b['lng'] - a['lng']) * math.cos(mid) * 6371000.0
    dy = math.radians(b['lat'] - a['lat']) * 6371000.0
    return math.hypot(dx, dy)


def _median(values):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def summarize_v4(decoded, record_start_ts=None, record_end_ts=None):
    """Сводка одного файла V4. Чистая функция, детерминирована.

    ``record_start_ts`` / ``record_end_ts`` -- unix-секунды записи (list/
    card); нужны для смещений концов окна. Без них смещения ``None``, и окно
    не может быть признано GOOD (личность и полнота интервала не проверены).

    Возвращает словарь с плоскими ключами -- ровно то, что ложится в
    ``dji_v4_summaries``. Отсутствующее -- ``None``, никогда 0.
    """
    frames = decoded.frames
    n = len(frames)
    warnings = set(decoded.warnings)

    times = [f.get('t') for f in frames]
    if any(t is None for t in times):
        warnings.add('FRAME_WITHOUT_TIME')
    t_known = [t for t in times if t is not None]
    t_first = t_known[0] if t_known else None
    t_last = t_known[-1] if t_known else None
    span_s = ((t_last - t_first) / 1000.0
              if t_first is not None and t_last is not None else None)

    dts = []
    nonpositive_dt = 0
    dt_over_limit = 0
    for a, b in zip(times, times[1:]):
        if a is None or b is None:
            nonpositive_dt += 1
            continue
        dt = (b - a) / 1000.0
        dts.append(dt)
        if dt <= 0:
            nonpositive_dt += 1
        elif dt > WINDOW_MAX_DT_S:
            dt_over_limit += 1

    start_offset = (t_first / 1000.0 - record_start_ts
                    if t_first is not None and record_start_ts is not None
                    else None)
    end_offset = (t_last / 1000.0 - record_end_ts
                  if t_last is not None and record_end_ts is not None
                  else None)

    # ── Канал счётчика: присутствие отдельно от значения ──────────────────
    present_idx = [i for i, f in enumerate(frames) if 'area_native' in f]
    counter_present = len(present_idx)
    first_i = present_idx[0] if present_idx else None
    last_i = present_idx[-1] if present_idx else None
    leading_omitted = first_i if first_i is not None else n
    trailing_omitted = (n - 1 - last_i) if last_i is not None else n
    interior_omitted = ((last_i - first_i + 1) - counter_present
                        if first_i is not None else 0)

    natives = [frames[i]['area_native'] for i in present_idx]
    steps = [b - a for a, b in zip(natives, natives[1:])]
    negative_steps = sum(1 for s in steps if s < -_NEGATIVE_STEP_EPS)
    positive_steps = sum(1 for s in steps if s > _NEGATIVE_STEP_EPS)
    positive_sum = sum(s for s in steps if s > 0)
    max_jump = max(steps) if steps else None
    max_drop = min(steps) if steps else None
    quantization_err = (max(abs(v * 100.0 - round(v * 100.0)) for v in natives)
                        if natives else None)

    observed_delta = (natives[-1] - natives[0]) if natives else None
    # Zero-default: значение отсутствующего поля читается как 0 -- ровно то,
    # что делает protobuf-парсер по умолчанию. ТОЛЬКО диагностика.
    first_default = frames[0].get('area_native', 0.0) if n else None
    last_default = frames[-1].get('area_native', 0.0) if n else None
    zero_default_delta = ((last_default - first_default)
                          if n else None)

    # ── Каналы применения ────────────────────────────────────────────────
    spray_block_frames = sum(1 for f in frames if f.get('spray_block'))
    flag_frames = sum(1 for f in frames if (f.get('spray_flag') or 0) > 0)
    flow_frames = sum(1 for f in frames if (f.get('flow') or 0) > 0)
    application_frames = sum(1 for f in frames
                             if (f.get('spray_flag') or 0) > 0
                             or (f.get('flow') or 0) > 0)
    flow_values = [f['flow'] for f in frames if 'flow' in f]
    flow_max = max(flow_values) if flow_values else None
    quantities = [f['quantity'] for f in frames if 'quantity' in f]
    quantity_delta = (quantities[-1] - quantities[0]) if quantities else None

    moving_frames = 0
    moving_distance = 0.0
    gps_frames = 0
    gps_jumps = 0
    speeds = []
    for i, f in enumerate(frames):
        if 'lat' in f and 'lng' in f:
            gps_frames += 1
        s = _speed(f)
        if s is not None:
            speeds.append(s)
        if i > 0:
            d = _distance_m(frames[i - 1], f)
            if d is not None and d > 100.0:
                gps_jumps += 1
            if (f.get('spray_flag') or 0) > 0 and s is not None \
                    and s > MOVING_APPLICATION_MIN_SPEED_MPS:
                moving_frames += 1
                if d is not None:
                    moving_distance += d

    widths = [f['width'] for f in frames if 'width' in f and f['width'] > 0]
    modes = {}
    for f in frames:
        key = f.get('mode')
        modes[key] = modes.get(key, 0) + 1

    if decoded.top_count is not None and decoded.top_count != n:
        warnings.add('TOP_COUNT_NE_FRAMES')

    return {
        'parser_version': V4_PARSER_VERSION,
        'size_bytes': decoded.size_bytes,
        'frame_count': n,
        'top_count': decoded.top_count,
        'usage_type': decoded.usage_type,
        't_first_ms': t_first,
        't_last_ms': t_last,
        'span_s': span_s,
        'start_offset_s': start_offset,
        'end_offset_s': end_offset,
        'dt_min_s': min(dts) if dts else None,
        'dt_max_s': max(dts) if dts else None,
        'dt_median_s': _median(dts),
        'nonpositive_dt_count': nonpositive_dt,
        'dt_over_limit_count': dt_over_limit,
        'frames_with_position': gps_frames,
        'frames_with_mode': sum(1 for f in frames if 'mode' in f),
        'frames_with_width': sum(1 for f in frames if 'width' in f),
        'frames_with_spray_block': spray_block_frames,
        'frames_with_flow': sum(1 for f in frames if 'flow' in f),
        'frames_with_quantity': len(quantities),
        'counter_present_frames': counter_present,
        'counter_first_encoded_native': natives[0] if natives else None,
        'counter_first_encoded_bits': (frames[first_i]['area_bits']
                                       if first_i is not None else None),
        'counter_last_encoded_native': natives[-1] if natives else None,
        'counter_last_encoded_bits': (frames[last_i]['area_bits']
                                      if last_i is not None else None),
        'counter_first_encoded_at_ms': (frames[first_i].get('t')
                                        if first_i is not None else None),
        'counter_last_encoded_at_ms': (frames[last_i].get('t')
                                       if last_i is not None else None),
        'counter_first_frame_index': first_i,
        'counter_last_frame_index': last_i,
        'counter_leading_omitted_frames': leading_omitted,
        'counter_trailing_omitted_frames': trailing_omitted,
        'counter_interior_omitted_frames': interior_omitted,
        'counter_min_native': min(natives) if natives else None,
        'counter_max_native': max(natives) if natives else None,
        'counter_negative_steps': negative_steps,
        'counter_positive_steps': positive_steps,
        'counter_max_jump_native': max_jump,
        'counter_max_drop_native': max_drop,
        'counter_positive_sum_native': positive_sum if natives else None,
        'counter_quantization_max_error': quantization_err,
        'counter_observed_delta_native': observed_delta,
        'counter_observed_delta_m2': (observed_delta * COUNTER_M2_PER_NATIVE
                                      if observed_delta is not None else None),
        'counter_zero_default_delta_native': zero_default_delta,
        'counter_zero_default_delta_m2': (
            zero_default_delta * COUNTER_M2_PER_NATIVE
            if zero_default_delta is not None else None),
        'application_flag_frames': flag_frames,
        'flow_positive_frames': flow_frames,
        'application_frames': application_frames,
        'flow_max': flow_max,
        'quantity_first': quantities[0] if quantities else None,
        'quantity_last': quantities[-1] if quantities else None,
        'quantity_delta': quantity_delta,
        'moving_application_frames': moving_frames,
        'moving_application_distance_m': round(moving_distance, 1),
        'gps_jump_over_100m': gps_jumps,
        'speed_max_mps': max(speeds) if speeds else None,
        'width_positive_frames': len(widths),
        'width_min': min(widths) if widths else None,
        'width_max': max(widths) if widths else None,
        'mode_histogram': {str(k): v for k, v in sorted(
            modes.items(), key=lambda kv: (kv[0] is None, kv[0]))},
        'warnings': sorted(warnings),
    }


# ─── Оконные гейты ───────────────────────────────────────────────────────────

WINDOW_GOOD = 'GOOD'
WINDOW_INCOMPLETE = 'INCOMPLETE'
WINDOW_CHANNEL_MISSING = 'CHANNEL_MISSING'
WINDOW_NONMONOTONE = 'NONMONOTONE'
WINDOW_IDENTITY_ERROR = 'IDENTITY_ERROR'
WINDOW_BASELINE_UNKNOWN = 'BASELINE_UNKNOWN'

BASELINE_ENCODED_AT_BOUNDARY = 'ENCODED_AT_BOUNDARY'
BASELINE_VERIFIED_CONTINUITY = 'VERIFIED_CONTINUITY'
BASELINE_UNVERIFIED_OMISSION = 'UNVERIFIED_OMISSION'
BASELINE_UNKNOWN = 'UNKNOWN'


def evaluate_window(summary, identity_ok=True):
    """Замороженные гейты окна -> (window_quality, baseline_status, reasons).

    Порядок важен и намеренный:

    1. IDENTITY_ERROR -- чужой файл не может ничего доказать об интервале;
    2. CHANNEL_MISSING -- поле 9 не закодировано ни в одном кадре;
    3. INCOMPLETE -- <2 кадров, неположительный span, смещение концов
       > 1.01 с (или его нельзя проверить), dt <= 0 или > 1 с;
    4. NONMONOTONE -- счётчик убывал;
    5. GOOD.

    Baseline: ENCODED_AT_BOUNDARY, если счётчик закодирован в первом кадре;
    UNVERIFIED_OMISSION при ведущем пропуске; VERIFIED_CONTINUITY здесь не
    выставляется никогда -- доказательство непрерывности сессии в этой версии
    не реализовано, и metadata-равенство его не заменяет.

    Ничего не подрезается, не дополняется и не экстраполируется: гейт только
    называет причину.
    """
    reasons = []
    n = summary.get('frame_count') or 0
    present = summary.get('counter_present_frames') or 0

    if not identity_ok:
        return WINDOW_IDENTITY_ERROR, BASELINE_UNKNOWN, ['V4_IDENTITY_MISMATCH']

    if present == 0:
        baseline = BASELINE_UNKNOWN
        reasons.append('AREA_CHANNEL_ABSENT')
        # Полнота окна всё равно проверяется -- она говорит о файле, а не о
        # канале, и ложится в причины рядом.
        _incomplete_reasons(summary, reasons)
        return WINDOW_CHANNEL_MISSING, baseline, reasons

    baseline = (BASELINE_ENCODED_AT_BOUNDARY
                if (summary.get('counter_leading_omitted_frames') or 0) == 0
                else BASELINE_UNVERIFIED_OMISSION)

    if n < WINDOW_MIN_FRAMES:
        reasons.append('FEWER_THAN_2_FRAMES')
    _incomplete_reasons(summary, reasons)
    if reasons:
        return WINDOW_INCOMPLETE, baseline, reasons

    if (summary.get('counter_negative_steps') or 0) > 0:
        return WINDOW_NONMONOTONE, baseline, ['COUNTER_NEGATIVE_STEP']

    return WINDOW_GOOD, baseline, []


def _incomplete_reasons(summary, reasons):
    span = summary.get('span_s')
    if span is None or span <= 0:
        reasons.append('NONPOSITIVE_SPAN')
    for key, tag in (('start_offset_s', 'START_OFFSET'),
                     ('end_offset_s', 'END_OFFSET')):
        value = summary.get(key)
        if value is None:
            reasons.append(tag + '_UNCHECKABLE')
        elif abs(value) > WINDOW_MAX_OFFSET_S:
            reasons.append(tag + '_OVER_1_01S')
    if (summary.get('nonpositive_dt_count') or 0) > 0:
        reasons.append('NONPOSITIVE_DT')
    if (summary.get('dt_over_limit_count') or 0) > 0:
        reasons.append('DT_GAP_OVER_1S')


# ─── Присутствие концов ──────────────────────────────────────────────────────

def counter_endpoints_at_boundary(summary):
    """(начало закодировано в кадре 0, конец закодирован в последнем кадре)."""
    present = summary.get('counter_present_frames') or 0
    if present == 0:
        return False, False
    return ((summary.get('counter_leading_omitted_frames') or 0) == 0,
            (summary.get('counter_trailing_omitted_frames') or 0) == 0)


def native_to_m2(native):
    return None if native is None else native * COUNTER_M2_PER_NATIVE


def is_exactly_flat(summary):
    """Концы равны по БИТАМ float32 -- native equality zero, не resolution-bin."""
    a = summary.get('counter_first_encoded_bits')
    b = summary.get('counter_last_encoded_bits')
    return a is not None and a == b
