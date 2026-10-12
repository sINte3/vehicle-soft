# -*- coding: utf-8 -*-
"""fin_recon/contract.py -- строгий контракт `snapshot.json` (fin-recon-snapshot/1).

Проверка идёт ДО транзакции и целиком: возвращается полный список проблем,
а не первая. Снимок, у которого есть хоть одна проблема, не импортируется.

Что держится здесь:

* состав ключей фиксирован на каждом уровне: лишний ключ -- ошибка, а не
  тихо проигнорированное поле, которое потом никто не увидит;
* деньги и гектары -- строки с десятичной записью (`"1234567.89"`), не
  числа JSON: float теряет копейки при первом же сложении. Число вместо
  строки -- ошибка;
* неизвестное -- `null` с доказательным типом UNKNOWN, и только так: число
  при UNKNOWN и `null` при любом другом типе -- ошибка. Ноль на месте
  неизвестного контракт пропустить не может;
* каждая ссылка разрешается: работа -- на оператора и книгу, выплата -- на
  оператора и решение, межмесячная связь -- на работу того же месяца работы;
* объявленные контрольные числа снимка (`metrics` с `check`) пересчитываются
  по строкам и обязаны совпасть до копейки: 38/7/39, суммы начислений и
  выплат, площади, вердикты Vehicle Soft, тождества DJI. Ложная сумма --
  отказ. Обязательные контроли перечислены в REQUIRED_CHECKS: снимок без них
  не принимается вовсе;
* работа другого календарного месяца внутри месяца книги обязана иметь
  межмесячную связь, и наоборот -- молча такая работа не пройдёт.

Модуль чистый: ни базы, ни файлов, ни Flask.
"""

import hashlib
import json
import re

from decimal import Decimal, InvalidOperation

import fin_recon as fr

_MONTH = re.compile(fr.MONTH_RE)
_DATE = re.compile(fr.DATE_RE)
_SHA = re.compile(fr.SHA256_RE)
_KEY = re.compile(fr.KEY_RE)
_METRIC_KEY = re.compile(r'^[A-Z][A-Z0-9_]{0,39}$')
_ANOMALY_ID = re.compile(r'^A\d{2,3}$')
_DECIMAL = re.compile(r'^-?\d+(\.\d+)?$')
_CELL = re.compile(r'^[A-Z]{1,3}[1-9]\d{0,6}$')

MAX_TEXT = 20000

# Знаков после запятой: деньги -- до тийина, гектары -- как в экспорте DJI.
PLACES = {'UZS': 2, 'ha': 8, 'count': 0, 'flights': 0}

TOP_KEYS = ('schema', 'header', 'sources', 'metrics', 'operators', 'payouts',
            'works', 'anomalies', 'decisions', 'cross_period_links', 'dji',
            'vs')
HEADER_KEYS = ('period', 'version', 'supersedes', 'revision_reason',
               'status', 'owner_approved_at', 'financially_closed_at',
               'title', 'currency', 'compiled_at', 'producer',
               'normalization_version', 'money_rounding',
               'evidence_boundaries', 'status_note')
SOURCE_KEYS = ('key', 'role', 'name', 'sha256', 'bytes', 'container',
               'member', 'in_package', 'note')
METRIC_KEYS = ('key', 'group', 'label', 'semantic', 'unit', 'value',
               'evidence', 'check', 'identity', 'note')
OPERATOR_KEYS = ('key', 'name', 'organization', 'area', 'accrued',
                 'recognized_total', 'difference', 'basis', 'limitations')
AREA_KEYS = ('total_ha', 'breakdown')
BREAKDOWN_KEYS = ('work_month', 'block', 'area_ha')
ACCRUED_KEYS = ('amount', 'evidence', 'source_value', 'ref')
REF_KEYS = ('source', 'sheet', 'cell', 'formula')
PAYOUT_KEYS = ('key', 'operator', 'kind', 'amount', 'evidence',
               'record_date', 'text', 'ref', 'decision', 'note')
WORK_KEYS = ('key', 'operator', 'source', 'sheet', 'row', 'part', 'refs',
             'farm', 'block', 'block_label', 'area_ha', 'area_evidence',
             'area_source_value', 'date_text', 'work_dates', 'work_month',
             'ledger_month', 'salary_status', 'vs_verdict',
             'vs_verdict_label', 'vs_db_rows', 'vs_db_area_ha', 'vs_note',
             'anomalies')
ANOMALY_KEYS = ('id', 'priority', 'status', 'group', 'finding', 'rule',
                'operators', 'source_address')
DECISION_KEYS = ('key', 'kind', 'topic', 'rule', 'confirmation',
                 'decided_on', 'detail', 'source', 'anomalies', 'values')
VALUE_KEYS = ('label', 'semantic', 'unit', 'value', 'evidence', 'note')
LINK_KEYS = ('key', 'work', 'work_month', 'ledger_month', 'accrual_month',
             'area_ha', 'salary_amount', 'salary_evidence',
             'payout_record_date', 'anomalies', 'policy', 'note')
DJI_KEYS = ('period_from', 'period_to', 'captured_at', 'source', 'flights',
            'raw_ha', 'accepted_ha', 'excluded_ha', 'excluded_auto_ha',
            'excluded_admin_ha', 'open_review', 'pending_v4',
            'unattributed_ha', 'unattributed_flights', 'single_candidate_ha',
            'single_candidate_flights', 'multi_candidate_ha',
            'multi_candidate_flights', 'attribution_source',
            'algorithm_versions', 'decisions', 'semantics_note', 'note')
DJI_DECISION_KEYS = ('flight_id', 'flight_date', 'drone', 'raw_ha', 'action',
                     'accepted_after_ha', 'comment', 'decided_at',
                     'decided_by')
VS_KEYS = ('export_source', 'extract_log_sha256', 'db_copy_sha256',
           'db_copy_label', 'compared_at', 'register_rows', 'db_rows',
           'equal', 'expected_only', 'unexpected', 'missing_in_db',
           'db_not_in_register', 'db_area_ha')

# [REASON]: эти контроли снимок обязан объявить, и импортёр их пересчитывает.
# Без них «38/7/39 не совпали -- отказ» было бы обещанием, которое снимок мог
# не дать проверить, просто не объявив числа.
REQUIRED_CHECKS = ('WORKS_COUNT', 'OPERATORS_COUNT', 'ANOMALIES_COUNT',
                   'BOOK_AREA_SUM', 'ACCRUED_SUM', 'PAYOUT_TEXT_SUM',
                   'PAYOUT_RECOGNIZED_SUM', 'DIFFERENCE_SUM')


class ContractError(Exception):
    """Снимок нарушает контракт. problems -- полный список нарушений."""

    def __init__(self, problems):
        self.problems = list(problems)
        super(ContractError, self).__init__(
            '%d contract problem(s)' % len(self.problems))


def canonical_bytes(obj):
    """Канонический JSON: ключи по порядку, без пробелов, UTF-8."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')


def content_sha256(obj):
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()


def dec(text):
    """Decimal из строки контракта (вызывать после проверки формата)."""
    return Decimal(text)


def decimal_text(value):
    """Decimal -> строка без экспоненты (для сообщений и сумм)."""
    if value is None:
        return None
    text = format(value, 'f')
    return text


class _Checker(object):

    def __init__(self, snapshot):
        self.s = snapshot
        self.problems = []

    # ── примитивы ──────────────────────────────────────────────────────
    def bad(self, where, message):
        self.problems.append('%s: %s' % (where, message))

    def keys(self, where, obj, expected):
        if not isinstance(obj, dict):
            self.bad(where, 'must be an object')
            return False
        extra = sorted(set(obj) - set(expected))
        missing = [k for k in expected if k not in obj]
        if extra:
            self.bad(where, 'unexpected key(s) %s' % ', '.join(extra))
        if missing:
            self.bad(where, 'missing key(s) %s' % ', '.join(missing))
        return not extra and not missing

    def text(self, where, value, required=True):
        if value is None:
            if required:
                self.bad(where, 'is required')
            return
        if not isinstance(value, str):
            self.bad(where, 'must be a string')
            return
        if required and not value.strip():
            self.bad(where, 'must not be empty')
        if len(value) > MAX_TEXT:
            self.bad(where, 'longer than %d characters' % MAX_TEXT)
        if '\x00' in value:
            self.bad(where, 'contains a NUL character')

    def integer(self, where, value, minimum=0, nullable=False):
        if value is None and nullable:
            return
        if isinstance(value, bool) or not isinstance(value, int):
            self.bad(where, 'must be an integer')
            return
        if value < minimum:
            self.bad(where, 'must be >= %d' % minimum)

    def decimal(self, where, value, unit, nullable=False, positive=False):
        """Проверить десятичную строку; вернуть Decimal или None."""
        if value is None:
            if not nullable:
                self.bad(where, 'is required (a decimal string)')
            return None
        if not isinstance(value, str) or not _DECIMAL.match(value):
            self.bad(where, 'must be a decimal string like "123.45", got %r'
                     % (value,))
            return None
        places = len(value.split('.', 1)[1]) if '.' in value else 0
        if places > PLACES[unit]:
            self.bad(where, 'has %d decimal places, at most %d allowed for %s'
                     % (places, PLACES[unit], unit))
        number = Decimal(value)
        if positive and number <= 0:
            self.bad(where, 'must be > 0')
        return number

    def evidence_pair(self, where, value, evidence, unit):
        """UNKNOWN <=> null. Вернуть Decimal или None."""
        if evidence not in fr.EVIDENCE_KINDS:
            self.bad(where, 'evidence %r is not one of %s'
                     % (evidence, ', '.join(fr.EVIDENCE_KINDS)))
            return None
        if evidence == fr.EVIDENCE_UNKNOWN:
            if value is not None:
                self.bad(where, 'evidence UNKNOWN requires value null, got %r '
                         '(unknown is not zero)' % (value,))
            return None
        if value is None:
            self.bad(where, 'value null requires evidence UNKNOWN, got %s'
                     % evidence)
            return None
        return self.decimal(where, value, unit)

    def month(self, where, value, nullable=False):
        if value is None and nullable:
            return
        if not isinstance(value, str) or not _MONTH.match(value):
            self.bad(where, 'must be a month YYYY-MM, got %r' % (value,))

    def date(self, where, value, nullable=False):
        if value is None and nullable:
            return
        if not isinstance(value, str) or not _DATE.match(value):
            self.bad(where, 'must be a date YYYY-MM-DD, got %r' % (value,))

    def key(self, where, value, pattern=_KEY):
        if not isinstance(value, str) or not pattern.match(value):
            self.bad(where, 'bad key %r' % (value,))
            return False
        return True

    def key_list(self, where, value, known):
        if not isinstance(value, list):
            self.bad(where, 'must be a list')
            return
        if len(set(value)) != len(value):
            self.bad(where, 'has duplicates')
        for item in value:
            if item not in known:
                self.bad(where, 'unknown reference %r' % (item,))

    def unique(self, where, items, field):
        seen = set()
        for item in items:
            value = item.get(field) if isinstance(item, dict) else None
            if value in seen:
                self.bad(where, 'duplicate %s %r' % (field, value))
            seen.add(value)
        return seen

    def as_list(self, where, value):
        if not isinstance(value, list):
            self.bad(where, 'must be a list')
            return []
        return value

    # ── разделы ────────────────────────────────────────────────────────
    def run(self):
        s = self.s
        if not self.keys('snapshot', s, TOP_KEYS):
            if not isinstance(s, dict):
                return self.problems
        if s.get('schema') != fr.SNAPSHOT_SCHEMA:
            self.bad('schema', 'must be %r' % fr.SNAPSHOT_SCHEMA)
        header = s.get('header') or {}
        self.check_header(header)
        self.period = header.get('period')
        sources = self.as_list('sources', s.get('sources'))
        self.check_sources(sources)
        anomalies = self.as_list('anomalies', s.get('anomalies'))
        operators = self.as_list('operators', s.get('operators'))
        self.operator_keys = self.unique('operators', operators, 'key')
        self.anomaly_ids = self.unique('anomalies', anomalies, 'id')
        decisions = self.as_list('decisions', s.get('decisions'))
        self.decision_keys = self.unique('decisions', decisions, 'key')
        self.check_anomalies(anomalies)
        works = self.as_list('works', s.get('works'))
        self.check_works(works)
        payouts = self.as_list('payouts', s.get('payouts'))
        self.check_payouts(payouts)
        self.check_operators(operators, works, payouts)
        self.check_decisions(decisions)
        links = self.as_list('cross_period_links', s.get('cross_period_links'))
        self.check_links(links, works)
        self.check_dji(s.get('dji'))
        self.check_vs(s.get('vs'), works)
        self.check_metrics(self.as_list('metrics', s.get('metrics')))
        return self.problems

    def check_header(self, h):
        if not self.keys('header', h, HEADER_KEYS):
            return
        self.month('header.period', h.get('period'))
        self.integer('header.version', h.get('version'), minimum=1)
        version = h.get('version')
        sup = h.get('supersedes')
        if version == 1:
            if sup is not None:
                self.bad('header.supersedes', 'must be null for version 1')
            if h.get('revision_reason') is not None:
                self.bad('header.revision_reason',
                         'must be null for version 1')
        elif isinstance(version, int) and not isinstance(version, bool):
            if self.keys('header.supersedes', sup, ('version',
                                                    'content_sha256')):
                if sup.get('version') != version - 1:
                    self.bad('header.supersedes.version',
                             'must be %d' % (version - 1))
                if not isinstance(sup.get('content_sha256'), str) or \
                        not _SHA.match(sup.get('content_sha256')):
                    self.bad('header.supersedes.content_sha256',
                             'must be a lowercase SHA-256')
            self.text('header.revision_reason', h.get('revision_reason'))
        if h.get('status') not in fr.STATUSES:
            self.bad('header.status', 'must be one of %s'
                     % ', '.join(fr.STATUSES))
        if h.get('owner_approved_at') is not None:
            self.bad('header.owner_approved_at',
                     'must be null: approval is not part of this snapshot')
        if h.get('financially_closed_at') is not None:
            self.bad('header.financially_closed_at',
                     'must be null: financial close is not part of this '
                     'snapshot')
        for field in ('title', 'compiled_at', 'producer',
                      'normalization_version', 'money_rounding',
                      'status_note'):
            self.text('header.%s' % field, h.get(field))
        if h.get('currency') != 'UZS':
            self.bad('header.currency', 'must be UZS')
        bounds = self.as_list('header.evidence_boundaries',
                              h.get('evidence_boundaries'))
        if not bounds:
            self.bad('header.evidence_boundaries', 'must not be empty')
        for i, item in enumerate(bounds):
            self.text('header.evidence_boundaries[%d]' % i, item)

    def check_sources(self, sources):
        self.source_keys = self.unique('sources', sources, 'key')
        self.sources = {}
        for i, src in enumerate(sources):
            where = 'sources[%d]' % i
            if not self.keys(where, src, SOURCE_KEYS):
                continue
            self.key(where + '.key', src.get('key'))
            if src.get('role') not in fr.SOURCE_ROLES:
                self.bad(where + '.role', 'unknown role %r' % (src.get('role'),))
            self.text(where + '.name', src.get('name'))
            # [REASON]: хеш может отсутствовать только у внешнего файла,
            # которого нет в пакете (большой оригинал хранит владелец): его
            # размер и имя известны, хеш -- нет, и выдумывать его нельзя.
            if src.get('sha256') is None and src.get('role') == 'EXTERNAL':
                pass
            elif not isinstance(src.get('sha256'), str) or \
                    not _SHA.match(src.get('sha256')):
                self.bad(where + '.sha256', 'must be a lowercase SHA-256')
            self.integer(where + '.bytes', src.get('bytes'), nullable=True)
            if not isinstance(src.get('in_package'), bool):
                self.bad(where + '.in_package', 'must be true or false')
            if src.get('role') == 'EXTERNAL' and src.get('in_package'):
                self.bad(where, 'an EXTERNAL source cannot be in the package')
            container = src.get('container')
            if container is not None:
                if container not in self.source_keys or \
                        container == src.get('key'):
                    self.bad(where + '.container',
                             'unknown container %r' % (container,))
                self.text(where + '.member', src.get('member'))
            elif src.get('member') is not None:
                self.bad(where + '.member', 'requires a container')
            self.text(where + '.note', src.get('note'), required=False)
            self.sources[src.get('key')] = src

    def check_ref(self, where, ref, nullable=True):
        if ref is None:
            if not nullable:
                self.bad(where, 'is required')
            return
        if not self.keys(where, ref, REF_KEYS):
            return
        if ref.get('source') not in self.source_keys:
            self.bad(where + '.source', 'unknown source %r'
                     % (ref.get('source'),))
        self.text(where + '.sheet', ref.get('sheet'))
        cell = ref.get('cell')
        if not isinstance(cell, str) or not _CELL.match(cell):
            self.bad(where + '.cell', 'must be a cell address like D34, got %r'
                     % (cell,))
        self.text(where + '.formula', ref.get('formula'), required=False)

    def check_anomalies(self, anomalies):
        for i, a in enumerate(anomalies):
            where = 'anomalies[%d]' % i
            if not self.keys(where, a, ANOMALY_KEYS):
                continue
            self.key(where + '.id', a.get('id'), _ANOMALY_ID)
            if a.get('priority') not in fr.ANOMALY_PRIORITIES:
                self.bad(where + '.priority', 'must be one of %s'
                         % ', '.join(fr.ANOMALY_PRIORITIES))
            if a.get('status') not in fr.ANOMALY_STATUSES:
                self.bad(where + '.status',
                         'status %r is not one of the register statuses'
                         % (a.get('status'),))
            for field in ('finding', 'rule'):
                self.text(where + '.' + field, a.get(field))
            for field in ('group', 'source_address'):
                self.text(where + '.' + field, a.get(field), required=False)
            self.key_list(where + '.operators', a.get('operators'),
                          self.operator_keys)

    def check_works(self, works):
        self.works = {}
        self.unique('works', works, 'key')
        places = set()
        for i, w in enumerate(works):
            where = 'works[%d]' % i
            if not self.keys(where, w, WORK_KEYS):
                continue
            self.key(where + '.key', w.get('key'))
            if w.get('operator') not in self.operator_keys:
                self.bad(where + '.operator', 'unknown operator %r'
                         % (w.get('operator'),))
            src = self.sources.get(w.get('source'))
            if src is None:
                self.bad(where + '.source', 'unknown source %r'
                         % (w.get('source'),))
            elif src.get('role') != 'BOOK_PRIMARY':
                self.bad(where + '.source', 'must be a BOOK_PRIMARY source')
            self.text(where + '.sheet', w.get('sheet'))
            self.integer(where + '.row', w.get('row'), minimum=1)
            self.text(where + '.part', w.get('part'), required=False)
            place = (w.get('source'), w.get('sheet'), w.get('row'),
                     w.get('part'))
            if place in places:
                self.bad(where, 'the same source row is listed twice')
            places.add(place)
            refs = w.get('refs')
            if not isinstance(refs, dict) or not refs:
                self.bad(where + '.refs', 'must be a non-empty object')
            else:
                for name, cell in sorted(refs.items()):
                    if not isinstance(cell, str) or not _CELL.match(cell):
                        self.bad(where + '.refs.' + str(name),
                                 'must be a cell address, got %r' % (cell,))
            self.text(where + '.farm', w.get('farm'), required=False)
            if w.get('block') not in fr.BLOCKS:
                self.bad(where + '.block', 'must be one of %s'
                         % ', '.join(fr.BLOCKS))
            self.text(where + '.block_label', w.get('block_label'))
            area = self.decimal(where + '.area_ha', w.get('area_ha'), 'ha',
                                positive=True)
            if w.get('area_evidence') not in (fr.EVIDENCE_BOOK_NUMBER,
                                              fr.EVIDENCE_BOOK_FORMULA):
                self.bad(where + '.area_evidence',
                         'must be BOOK_NUMBER or BOOK_FORMULA')
            self.text(where + '.area_source_value', w.get('area_source_value'),
                      required=False)
            self.text(where + '.date_text', w.get('date_text'),
                      required=False)
            dates = w.get('work_dates')
            if not isinstance(dates, list):
                self.bad(where + '.work_dates', 'must be a list')
                dates = []
            for j, d in enumerate(dates):
                self.date('%s.work_dates[%d]' % (where, j), d)
            if dates != sorted(set(d for d in dates if isinstance(d, str))):
                self.bad(where + '.work_dates', 'must be sorted and unique')
            self.month(where + '.work_month', w.get('work_month'),
                       nullable=True)
            months = {d[:7] for d in dates if isinstance(d, str)}
            if w.get('work_month') is not None and months and \
                    w.get('work_month') not in months:
                self.bad(where + '.work_month',
                         'is not the month of any work date')
            if dates and w.get('work_month') is None and len(months) == 1:
                self.bad(where + '.work_month',
                         'is null although all work dates are in one month')
            if w.get('ledger_month') != self.period:
                self.bad(where + '.ledger_month',
                         'must equal header.period %r' % (self.period,))
            if w.get('salary_status') not in fr.SALARY_STATUSES:
                self.bad(where + '.salary_status', 'must be one of %s'
                         % ', '.join(fr.SALARY_STATUSES))
            verdict = w.get('vs_verdict')
            if verdict is None:
                for field in ('vs_verdict_label', 'vs_db_rows',
                              'vs_db_area_ha', 'vs_note'):
                    if w.get(field) is not None:
                        self.bad(where + '.' + field,
                                 'must be null when vs_verdict is null')
            else:
                if verdict not in fr.VS_VERDICTS:
                    self.bad(where + '.vs_verdict', 'must be one of %s'
                             % ', '.join(fr.VS_VERDICTS))
                self.text(where + '.vs_verdict_label',
                          w.get('vs_verdict_label'))
                self.integer(where + '.vs_db_rows', w.get('vs_db_rows'))
                rows = w.get('vs_db_rows')
                if rows == 0:
                    if w.get('vs_db_area_ha') is not None:
                        self.bad(where + '.vs_db_area_ha',
                                 'must be null when the row is missing in the '
                                 'database')
                else:
                    self.decimal(where + '.vs_db_area_ha',
                                 w.get('vs_db_area_ha'), 'ha')
                self.text(where + '.vs_note', w.get('vs_note'),
                          required=False)
            self.key_list(where + '.anomalies', w.get('anomalies'),
                          self.anomaly_ids)
            self.works[w.get('key')] = (w, area)

    def check_payouts(self, payouts):
        self.unique('payouts', payouts, 'key')
        self.payouts = []
        for i, p in enumerate(payouts):
            where = 'payouts[%d]' % i
            if not self.keys(where, p, PAYOUT_KEYS):
                continue
            self.key(where + '.key', p.get('key'))
            if p.get('operator') not in self.operator_keys:
                self.bad(where + '.operator', 'unknown operator %r'
                         % (p.get('operator'),))
            kind = p.get('kind')
            if kind not in fr.PAYOUT_KINDS:
                self.bad(where + '.kind', 'must be one of %s'
                         % ', '.join(fr.PAYOUT_KINDS))
            elif p.get('evidence') != fr.PAYOUT_EVIDENCE[kind]:
                self.bad(where + '.evidence', '%s requires evidence %s'
                         % (kind, fr.PAYOUT_EVIDENCE[kind]))
            amount = self.decimal(where + '.amount', p.get('amount'), 'UZS',
                                  positive=True)
            self.date(where + '.record_date', p.get('record_date'),
                      nullable=True)
            if kind == fr.PAYOUT_BOOK_TEXT:
                self.text(where + '.text', p.get('text'))
                self.check_ref(where + '.ref', p.get('ref'), nullable=False)
            else:
                self.text(where + '.text', p.get('text'), required=False)
                self.check_ref(where + '.ref', p.get('ref'))
            decision = p.get('decision')
            if kind == fr.PAYOUT_OWNER_RECOGNIZED:
                if decision not in self.decision_keys:
                    self.bad(where + '.decision',
                             'an owner-recognized payout must name an owner '
                             'decision, got %r' % (decision,))
            elif decision is not None and decision not in self.decision_keys:
                self.bad(where + '.decision', 'unknown decision %r'
                         % (decision,))
            self.text(where + '.note', p.get('note'), required=False)
            self.payouts.append((p, amount))

    def check_operators(self, operators, works, payouts):
        self.accrued = {}
        for i, op in enumerate(operators):
            where = 'operators[%d]' % i
            if not self.keys(where, op, OPERATOR_KEYS):
                continue
            key = op.get('key')
            self.key(where + '.key', key)
            self.text(where + '.name', op.get('name'))
            self.text(where + '.organization', op.get('organization'),
                      required=False)
            self.text(where + '.basis', op.get('basis'))
            self.text(where + '.limitations', op.get('limitations'),
                      required=False)
            own_works = [(w, a) for (w, a) in self.works.values()
                         if w.get('operator') == key]
            own_payouts = [(p, a) for (p, a) in self.payouts
                           if p.get('operator') == key]
            if not own_works and not own_payouts:
                self.bad(where, 'operator has neither works nor payouts')
            area = op.get('area')
            if self.keys(where + '.area', area, AREA_KEYS):
                total = self.decimal(where + '.area.total_ha',
                                     area.get('total_ha'), 'ha')
                declared = {}
                for j, part in enumerate(self.as_list(where + '.area.breakdown',
                                                      area.get('breakdown'))):
                    pw = '%s.area.breakdown[%d]' % (where, j)
                    if not self.keys(pw, part, BREAKDOWN_KEYS):
                        continue
                    self.month(pw + '.work_month', part.get('work_month'))
                    if part.get('block') not in fr.BLOCKS:
                        self.bad(pw + '.block', 'must be one of %s'
                                 % ', '.join(fr.BLOCKS))
                    value = self.decimal(pw + '.area_ha', part.get('area_ha'),
                                         'ha', positive=True)
                    slot = (part.get('work_month'), part.get('block'))
                    if slot in declared:
                        self.bad(pw, 'duplicate breakdown slot %r' % (slot,))
                    declared[slot] = value
                derived = {}
                for w, a in own_works:
                    if a is None:
                        continue
                    slot = (w.get('work_month'), w.get('block'))
                    derived[slot] = derived.get(slot, Decimal(0)) + a
                if None not in declared.values() and declared != derived:
                    self.bad(where + '.area.breakdown',
                             'does not match the works of the operator: '
                             'declared %s, works give %s'
                             % (_slots(declared), _slots(derived)))
                works_total = sum((a for _w, a in own_works if a is not None),
                                  Decimal(0))
                if total is not None and total != works_total:
                    self.bad(where + '.area.total_ha',
                             'is %s, the works of the operator sum to %s'
                             % (decimal_text(total), decimal_text(works_total)))
            accrued = op.get('accrued')
            amount = None
            if self.keys(where + '.accrued', accrued, ACCRUED_KEYS):
                amount = self.evidence_pair(where + '.accrued',
                                            accrued.get('amount'),
                                            accrued.get('evidence'), 'UZS')
                self.text(where + '.accrued.source_value',
                          accrued.get('source_value'), required=False)
                self.check_ref(where + '.accrued.ref', accrued.get('ref'))
            self.accrued[key] = amount
            recognized = self.decimal(where + '.recognized_total',
                                      op.get('recognized_total'), 'UZS')
            paid = sum((a for _p, a in own_payouts if a is not None),
                       Decimal(0))
            if recognized is not None and recognized != paid:
                self.bad(where + '.recognized_total',
                         'is %s, the payouts of the operator sum to %s'
                         % (decimal_text(recognized), decimal_text(paid)))
            difference = op.get('difference')
            if amount is None:
                if difference is not None:
                    self.bad(where + '.difference',
                             'must be null when the accrual is unknown')
            else:
                diff = self.decimal(where + '.difference', difference, 'UZS')
                if diff is not None and recognized is not None and \
                        diff != amount - recognized:
                    self.bad(where + '.difference',
                             'is %s, accrued minus recognized is %s'
                             % (decimal_text(diff),
                                decimal_text(amount - recognized)))

    def check_decisions(self, decisions):
        for i, d in enumerate(decisions):
            where = 'decisions[%d]' % i
            if not self.keys(where, d, DECISION_KEYS):
                continue
            self.key(where + '.key', d.get('key'))
            if d.get('kind') not in fr.DECISION_KINDS:
                self.bad(where + '.kind', 'must be one of %s'
                         % ', '.join(fr.DECISION_KINDS))
            for field in ('topic', 'rule'):
                self.text(where + '.' + field, d.get(field))
            for field in ('confirmation', 'detail'):
                self.text(where + '.' + field, d.get(field), required=False)
            self.date(where + '.decided_on', d.get('decided_on'),
                      nullable=True)
            if d.get('source') is not None and \
                    d.get('source') not in self.source_keys:
                self.bad(where + '.source', 'unknown source %r'
                         % (d.get('source'),))
            self.key_list(where + '.anomalies', d.get('anomalies'),
                          self.anomaly_ids)
            for j, v in enumerate(self.as_list(where + '.values',
                                               d.get('values'))):
                self.check_value('%s.values[%d]' % (where, j), v)

    def check_value(self, where, v):
        if not self.keys(where, v, VALUE_KEYS):
            return
        self.text(where + '.label', v.get('label'))
        if v.get('semantic') not in fr.SEMANTIC_KINDS:
            self.bad(where + '.semantic', 'unknown semantic %r'
                     % (v.get('semantic'),))
        unit = v.get('unit')
        if unit not in fr.UNITS:
            self.bad(where + '.unit', 'unknown unit %r' % (unit,))
            return
        self.evidence_pair(where, v.get('value'), v.get('evidence'), unit)
        self.text(where + '.note', v.get('note'), required=False)

    def check_links(self, links, works):
        self.unique('cross_period_links', links, 'key')
        linked = {}
        for i, link in enumerate(links):
            where = 'cross_period_links[%d]' % i
            if not self.keys(where, link, LINK_KEYS):
                continue
            self.key(where + '.key', link.get('key'))
            entry = self.works.get(link.get('work'))
            if entry is None:
                self.bad(where + '.work', 'unknown work %r'
                         % (link.get('work'),))
                continue
            work, area = entry
            if link.get('work') in linked:
                self.bad(where + '.work', 'work %r is linked twice'
                         % (link.get('work'),))
            linked[link.get('work')] = link
            self.month(where + '.work_month', link.get('work_month'))
            if link.get('work_month') != work.get('work_month'):
                self.bad(where + '.work_month',
                         'must equal the work month of %s' % link.get('work'))
            if link.get('ledger_month') != self.period:
                self.bad(where + '.ledger_month', 'must equal header.period')
            if link.get('work_month') == link.get('ledger_month'):
                self.bad(where, 'a cross-period link needs a work month '
                         'different from the ledger month')
            self.month(where + '.accrual_month', link.get('accrual_month'),
                       nullable=True)
            link_area = self.decimal(where + '.area_ha', link.get('area_ha'),
                                     'ha', positive=True)
            if link_area is not None and area is not None and \
                    link_area != area:
                self.bad(where + '.area_ha', 'must equal the area of %s'
                         % link.get('work'))
            self.evidence_pair(where + '.salary', link.get('salary_amount'),
                               link.get('salary_evidence'), 'UZS')
            self.date(where + '.payout_record_date',
                      link.get('payout_record_date'), nullable=True)
            self.key_list(where + '.anomalies', link.get('anomalies'),
                          self.anomaly_ids)
            self.text(where + '.policy', link.get('policy'))
            self.text(where + '.note', link.get('note'), required=False)
        # [REASON]: работа другого месяца внутри книги месяца -- ровно та
        # точка, где появляется двойной учёт при импорте соседнего месяца.
        # Без связи она прошла бы в снимок молча.
        for key, (work, _area) in sorted(self.works.items(),
                                         key=lambda kv: str(kv[0])):
            month = work.get('work_month')
            if month is not None and month != self.period and \
                    key not in linked:
                self.bad('works.%s' % key,
                         'work month %s differs from the ledger month but no '
                         'cross-period link names it' % month)

    def check_dji(self, dji):
        if not self.keys('dji', dji, DJI_KEYS):
            return
        self.date('dji.period_from', dji.get('period_from'))
        self.date('dji.period_to', dji.get('period_to'))
        self.text('dji.captured_at', dji.get('captured_at'))
        src = self.sources.get(dji.get('source'))
        if src is None or src.get('role') not in ('DJI_EXPORT_AFTER',
                                                  'DJI_EXPORT_BEFORE'):
            self.bad('dji.source', 'must name a DJI export source')
        self.integer('dji.flights', dji.get('flights'))
        self.integer('dji.open_review', dji.get('open_review'))
        self.integer('dji.pending_v4', dji.get('pending_v4'))
        raw = self.decimal('dji.raw_ha', dji.get('raw_ha'), 'ha')
        accepted = self.decimal('dji.accepted_ha', dji.get('accepted_ha'),
                                'ha')
        excluded = self.decimal('dji.excluded_ha', dji.get('excluded_ha'),
                                'ha')
        auto = self.decimal('dji.excluded_auto_ha',
                            dji.get('excluded_auto_ha'), 'ha')
        admin = self.decimal('dji.excluded_admin_ha',
                             dji.get('excluded_admin_ha'), 'ha')
        if None not in (raw, accepted, excluded) and accepted != raw - excluded:
            self.bad('dji.accepted_ha', 'is %s, raw minus excluded is %s'
                     % (decimal_text(accepted), decimal_text(raw - excluded)))
        if None not in (excluded, auto, admin) and excluded != auto + admin:
            self.bad('dji.excluded_ha', 'is %s, automatic plus admin is %s'
                     % (decimal_text(excluded), decimal_text(auto + admin)))
        baskets = []
        for name in ('unattributed', 'single_candidate', 'multi_candidate'):
            ha = self.decimal('dji.%s_ha' % name, dji.get(name + '_ha'), 'ha',
                              nullable=True)
            self.integer('dji.%s_flights' % name, dji.get(name + '_flights'),
                         nullable=True)
            baskets.append((ha, dji.get(name + '_flights')))
        if all(b[0] is not None for b in baskets):
            if raw is not None and sum(b[0] for b in baskets) != raw:
                self.bad('dji', 'attribution baskets sum to %s ha, raw is %s'
                         % (decimal_text(sum(b[0] for b in baskets)),
                            decimal_text(raw)))
            if all(isinstance(b[1], int) for b in baskets) and \
                    isinstance(dji.get('flights'), int) and \
                    sum(b[1] for b in baskets) != dji.get('flights'):
                self.bad('dji', 'attribution baskets hold %d flights, the '
                         'period has %d' % (sum(b[1] for b in baskets),
                                            dji.get('flights')))
            if dji.get('attribution_source') not in self.source_keys:
                self.bad('dji.attribution_source', 'unknown source %r'
                         % (dji.get('attribution_source'),))
        versions = dji.get('algorithm_versions')
        if not isinstance(versions, dict):
            self.bad('dji.algorithm_versions', 'must be an object')
        else:
            for name, value in sorted(versions.items()):
                self.text('dji.algorithm_versions.%s' % name, value)
        admin_sum = Decimal(0)
        decisions = self.as_list('dji.decisions', dji.get('decisions'))
        for i, d in enumerate(decisions):
            where = 'dji.decisions[%d]' % i
            if not self.keys(where, d, DJI_DECISION_KEYS):
                continue
            if not isinstance(d.get('flight_id'), str) or \
                    not d.get('flight_id').isdigit():
                self.bad(where + '.flight_id', 'must be a digit string')
            self.date(where + '.flight_date', d.get('flight_date'))
            for field in ('drone', 'action', 'decided_at', 'decided_by'):
                self.text(where + '.' + field, d.get(field))
            self.text(where + '.comment', d.get('comment'), required=False)
            d_raw = self.decimal(where + '.raw_ha', d.get('raw_ha'), 'ha')
            d_after = self.decimal(where + '.accepted_after_ha',
                                   d.get('accepted_after_ha'), 'ha')
            if d_raw is not None and d_after is not None:
                if d_after > d_raw:
                    self.bad(where, 'accepted after a decision exceeds RAW')
                admin_sum += d_raw - d_after
        if admin is not None and decisions and admin_sum != admin:
            self.bad('dji.excluded_admin_ha', 'is %s, the decisions exclude %s'
                     % (decimal_text(admin), decimal_text(admin_sum)))
        self.text('dji.semantics_note', dji.get('semantics_note'))
        self.text('dji.note', dji.get('note'), required=False)

    def check_vs(self, vs, works):
        if vs is None:
            if any(w.get('vs_verdict') is not None
                   for w, _a in self.works.values()):
                self.bad('vs', 'is null but works carry Vehicle Soft verdicts')
            return
        if not self.keys('vs', vs, VS_KEYS):
            return
        if vs.get('export_source') not in self.source_keys:
            self.bad('vs.export_source', 'unknown source %r'
                     % (vs.get('export_source'),))
        for field in ('extract_log_sha256', 'db_copy_sha256'):
            if not isinstance(vs.get(field), str) or \
                    not _SHA.match(vs.get(field)):
                self.bad('vs.' + field, 'must be a lowercase SHA-256')
        for field in ('db_copy_label', 'compared_at'):
            self.text('vs.' + field, vs.get(field))
        for field in ('register_rows', 'db_rows', 'equal', 'expected_only',
                      'unexpected', 'missing_in_db', 'db_not_in_register'):
            self.integer('vs.' + field, vs.get(field))
        self.decimal('vs.db_area_ha', vs.get('db_area_ha'), 'ha')
        derived = vs_counts(self.s)
        for field, value in sorted(derived.items()):
            declared = vs.get(field)
            if field == 'db_area_ha':
                if isinstance(declared, str) and _DECIMAL.match(declared) and \
                        Decimal(declared) != value:
                    self.bad('vs.db_area_ha', 'is %s, the works give %s'
                             % (declared, decimal_text(value)))
            elif declared != value:
                self.bad('vs.' + field, 'is %r, the works give %r'
                         % (declared, value))

    def check_metrics(self, metrics):
        self.unique('metrics', metrics, 'key')
        by_key = {}
        for i, m in enumerate(metrics):
            where = 'metrics[%d]' % i
            if not self.keys(where, m, METRIC_KEYS):
                continue
            self.key(where + '.key', m.get('key'), _METRIC_KEY)
            self.text(where + '.group', m.get('group'))
            self.text(where + '.label', m.get('label'))
            self.text(where + '.note', m.get('note'), required=False)
            if m.get('semantic') not in fr.SEMANTIC_KINDS:
                self.bad(where + '.semantic', 'unknown semantic %r'
                         % (m.get('semantic'),))
            unit = m.get('unit')
            if unit not in fr.UNITS:
                self.bad(where + '.unit', 'unknown unit %r' % (unit,))
                continue
            value = self.evidence_pair(where, m.get('value'),
                                       m.get('evidence'), unit)
            by_key[m.get('key')] = (m, value)
        computed = derived_checks(self.s)
        declared_checks = {}
        for key, (m, value) in sorted(by_key.items()):
            check = m.get('check')
            if check is None:
                continue
            if check not in computed:
                self.bad('metrics.%s.check' % key, 'unknown check rule %r'
                         % (check,))
                continue
            if check in declared_checks:
                self.bad('metrics.%s.check' % key, 'check %s is declared '
                         'twice' % check)
            declared_checks[check] = key
            expected = computed[check]
            if value is None or expected is None or value != expected:
                self.bad('metrics.%s' % key,
                         'declares %s, the rows give %s (check %s)'
                         % (m.get('value'), decimal_text(expected), check))
        for check in REQUIRED_CHECKS:
            if check not in declared_checks:
                self.bad('metrics', 'required control %s is not declared'
                         % check)
        for key, (m, value) in sorted(by_key.items()):
            identity = m.get('identity')
            if identity is None:
                continue
            where = 'metrics.%s.identity' % key
            if not self.keys(where, identity, ('plus', 'minus')):
                continue
            total = Decimal(0)
            ok = True
            for sign, names in ((1, identity.get('plus')),
                                (-1, identity.get('minus'))):
                if not isinstance(names, list):
                    self.bad(where, 'plus and minus must be lists')
                    ok = False
                    continue
                for name in names:
                    other = by_key.get(name)
                    if other is None or other[1] is None:
                        self.bad(where, 'refers to %r, which has no value'
                                 % (name,))
                        ok = False
                        continue
                    if other[0].get('unit') != m.get('unit'):
                        self.bad(where, 'mixes units (%s)' % name)
                        ok = False
                    total += sign * other[1]
            if ok and value is not None and total != value:
                self.bad('metrics.%s' % key, 'is %s, its identity gives %s'
                         % (m.get('value'), decimal_text(total)))


def _slots(mapping):
    return ', '.join('%s/%s=%s' % (k[0], k[1], decimal_text(v))
                     for k, v in sorted(mapping.items(),
                                        key=lambda kv: (str(kv[0][0]),
                                                        str(kv[0][1]))))


def _sum(values):
    return sum((Decimal(v) for v in values if v is not None), Decimal(0))


def vs_counts(snapshot):
    """Счётчики сверки Vehicle Soft, выведенные из строк работ."""
    works = [w for w in snapshot.get('works') or [] if isinstance(w, dict)]
    compared = [w for w in works if w.get('vs_verdict') is not None]
    db_rows = 0
    for w in compared:
        rows = w.get('vs_db_rows')
        if isinstance(rows, int) and not isinstance(rows, bool):
            db_rows += rows
    return {
        'register_rows': len(compared),
        'db_rows': db_rows,
        'equal': sum(1 for w in compared if w.get('vs_verdict') == fr.VS_MATCH),
        'expected_only': sum(1 for w in compared
                             if w.get('vs_verdict') == fr.VS_EXPECTED),
        'unexpected': sum(1 for w in compared
                          if w.get('vs_verdict') == fr.VS_UNEXPECTED),
        'missing_in_db': sum(1 for w in compared if w.get('vs_db_rows') == 0),
        'db_area_ha': _sum(w.get('vs_db_area_ha') for w in compared
                           if isinstance(w.get('vs_db_area_ha'), str)
                           and _DECIMAL.match(w.get('vs_db_area_ha'))),
    }


def _decimal_or_none(value):
    if isinstance(value, str) and _DECIMAL.match(value):
        return Decimal(value)
    return None


def derived_checks(snapshot):
    """{check rule: Decimal} -- контрольные числа, пересчитанные по строкам.

    Один источник для контракта (объявленное = пересчитанное), для сверки
    базы с пакетом и для приватных ожиданий месяца.
    """
    period = (snapshot.get('header') or {}).get('period')
    works = [w for w in snapshot.get('works') or [] if isinstance(w, dict)]
    operators = [o for o in snapshot.get('operators') or []
                 if isinstance(o, dict)]
    payouts = [p for p in snapshot.get('payouts') or [] if isinstance(p, dict)]
    anomalies = [a for a in snapshot.get('anomalies') or []
                 if isinstance(a, dict)]
    links = [x for x in snapshot.get('cross_period_links') or []
             if isinstance(x, dict)]
    dji = snapshot.get('dji') if isinstance(snapshot.get('dji'), dict) else {}

    def area(rows):
        return _sum(w.get('area_ha') for w in rows
                    if _decimal_or_none(w.get('area_ha')) is not None)

    def money(rows):
        return _sum(p.get('amount') for p in rows
                    if _decimal_or_none(p.get('amount')) is not None)

    accrued = []
    for o in operators:
        acc = o.get('accrued') if isinstance(o.get('accrued'), dict) else {}
        accrued.append(_decimal_or_none(acc.get('amount')))
    accrued_sum = sum((a for a in accrued if a is not None), Decimal(0))
    text_payouts = [p for p in payouts if p.get('kind') == fr.PAYOUT_BOOK_TEXT]
    owner_payouts = [p for p in payouts
                     if p.get('kind') == fr.PAYOUT_OWNER_RECOGNIZED]
    in_period = [w for w in works if w.get('work_month') == period]
    other = [w for w in works if w.get('work_month') != period]
    unknown_salary = [w for w in works
                      if w.get('salary_status') == fr.SALARY_UNKNOWN]
    vs = vs_counts(snapshot)
    out = {
        'WORKS_COUNT': Decimal(len(works)),
        'WORKS_PERIOD_COUNT': Decimal(len(in_period)),
        'WORKS_OTHER_MONTHS_COUNT': Decimal(len(other)),
        'OPERATORS_COUNT': Decimal(len(operators)),
        'ANOMALIES_COUNT': Decimal(len(anomalies)),
        'ANOMALIES_OPEN_COUNT': Decimal(sum(
            1 for a in anomalies
            if fr.ANOMALY_STATUSES.get(a.get('status')) == 'OPEN')),
        'CROSS_LINKS_COUNT': Decimal(len(links)),
        'BOOK_AREA_SUM': area(works),
        'BOOK_AREA_PERIOD_SUM': area(in_period),
        'BOOK_AREA_OTHER_MONTHS_SUM': area(other),
        'ACCRUED_SUM': accrued_sum,
        'ACCRUED_UNKNOWN_OPERATORS': Decimal(sum(1 for a in accrued
                                                 if a is None)),
        'PAYOUT_TEXT_SUM': money(text_payouts),
        'PAYOUT_OWNER_SUM': money(owner_payouts),
        'PAYOUT_RECOGNIZED_SUM': money(payouts),
        'DIFFERENCE_SUM': accrued_sum - money(payouts),
        'UNKNOWN_SALARY_AREA_SUM': area(unknown_salary),
        'UNKNOWN_SALARY_WORKS_COUNT': Decimal(len(unknown_salary)),
        'VS_REGISTER_ROWS': Decimal(vs['register_rows']),
        'VS_DB_ROWS': Decimal(vs['db_rows']),
        'VS_EQUAL': Decimal(vs['equal']),
        'VS_EXPECTED': Decimal(vs['expected_only']),
        'VS_UNEXPECTED': Decimal(vs['unexpected']),
        'VS_MISSING': Decimal(vs['missing_in_db']),
        'VS_DB_AREA_SUM': vs['db_area_ha'],
    }
    for name, field in (('DJI_RAW', 'raw_ha'), ('DJI_ACCEPTED', 'accepted_ha'),
                        ('DJI_EXCLUDED', 'excluded_ha'),
                        ('DJI_UNATTRIBUTED', 'unattributed_ha'),
                        ('DJI_SINGLE_CANDIDATE', 'single_candidate_ha')):
        out[name] = _decimal_or_none(dji.get(field))
    for name, field in (('DJI_FLIGHTS', 'flights'),
                        ('DJI_OPEN_REVIEW', 'open_review'),
                        ('DJI_PENDING_V4', 'pending_v4')):
        value = dji.get(field)
        out[name] = (Decimal(value) if isinstance(value, int)
                     and not isinstance(value, bool) else None)
    decisions = dji.get('decisions')
    out['DJI_ADMIN_DECISIONS'] = (Decimal(len(decisions))
                                  if isinstance(decisions, list) else None)
    return out


def validate(snapshot):
    """Список проблем снимка (пустой -- снимок годен)."""
    return _Checker(snapshot).run()


def require_valid(snapshot):
    problems = validate(snapshot)
    if problems:
        raise ContractError(problems)
    return snapshot
