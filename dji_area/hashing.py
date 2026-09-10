# -*- coding: utf-8 -*-
"""dji_area/hashing.py -- отпечаток входа расчёта и канонический JSON.

``calculation_input_hash`` = SHA-256 канонического JSON из: SHA источников
(list/card/route/V4), версия алгоритма, конфигурация (пороги, список
ненадёжных бортов, ревизия способности канала), часовой пояс отчёта,
идентификаторы соседних записей структурного экрана и пересечений. Повтор
расчёта тех же входов детерминирован; иной hash -> новая версия строки
(append), старая не переписывается.
"""

import hashlib
import json

from dji_area import (AREA_ALGORITHM_VERSION, CHANNEL_CAPABILITY_REVISION,
                      FIELD_RESOLVER_VERSION, REPORT_TIMEZONE,
                      STRUCTURAL_RULE_VERSION, V4_PARSER_VERSION)
from dji_area.resolver import (AGREEMENT_ABS_M2, AGREEMENT_REL,
                               KNOWN_UNRELIABLE_APPLICATION_HARDWARE,
                               PARTIAL_MAX_SHARE, PARTIAL_MIN_RAW_M2)
from dji_area.v4 import (WINDOW_MAX_DT_S, WINDOW_MAX_OFFSET_S,
                         WINDOW_MIN_FRAMES)


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), default=str)


def sha256_text(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def resolver_config_snapshot():
    """Конфигурация, от которой зависит решение. Меняется -> меняется hash."""
    return {
        'area_algorithm_version': AREA_ALGORITHM_VERSION,
        'structural_rule_version': STRUCTURAL_RULE_VERSION,
        'channel_capability_revision': CHANNEL_CAPABILITY_REVISION,
        # [REASON]: тело V4 неизменяемо, но СМЫСЛ, извлечённый из тех же байтов,
        # задаёт парсер. Без версии парсера исправление карты полей protobuf не
        # меняет ни SHA источников, ни конфигурацию -- отпечаток совпадает,
        # store.upsert находит строку по (flight_id, версия, hash) и отвечает
        # `unchanged`, оставляя в базе выводы старого парсера навсегда.
        'v4_parser_version': V4_PARSER_VERSION,
        'report_timezone': REPORT_TIMEZONE,
        'agreement_abs_m2': AGREEMENT_ABS_M2,
        'agreement_rel': AGREEMENT_REL,
        'partial_max_share': PARTIAL_MAX_SHARE,
        'partial_min_raw_m2': PARTIAL_MIN_RAW_M2,
        'window_min_frames': WINDOW_MIN_FRAMES,
        'window_max_offset_s': WINDOW_MAX_OFFSET_S,
        'window_max_dt_s': WINDOW_MAX_DT_S,
        'known_unreliable_hardware': sorted(
            KNOWN_UNRELIABLE_APPLICATION_HARDWARE),
    }


def calculation_input_hash(source_shas, neighbours, channel_evidence,
                           extra=None):
    """SHA-256 входа расчёта площади одной записи.

    ``source_shas`` -- {'list': sha|None, 'card': ..., 'route': ..., 'v4': ...}
    ``neighbours`` -- отсортированный список (flight_id, sha) записей борта,
    участвовавших в структурном экране и проверке пересечений
    ``channel_evidence`` -- bool: есть ли у борта в периоде flag/flow evidence
    """
    payload = {
        'config': resolver_config_snapshot(),
        'sources': source_shas,
        'neighbours': neighbours,
        'channel_evidence': bool(channel_evidence),
        'extra': extra or {},
    }
    return sha256_text(canonical_json(payload))


def field_input_hash(geometry_key_raw, catalog_state_sha, route_sha=None,
                     lineage_keys=None):
    payload = {
        'field_resolver_version': FIELD_RESOLVER_VERSION,
        'geometry_key_raw': geometry_key_raw,
        'catalog_state_sha': catalog_state_sha,
        'route_sha': route_sha,
        'lineage_keys': sorted(lineage_keys or []),
    }
    return sha256_text(canonical_json(payload))
