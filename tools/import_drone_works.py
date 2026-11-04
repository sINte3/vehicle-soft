#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/import_drone_works.py -- DRONE-WORKS-001 import of the dispatchers'
Excel books into drone_works / drone_customers / drone_customer_aliases.

Since September 2025 the whole commercial side of the drone operation has
lived in Excel files on somebody's laptop: about 700 rows, 526 farms,
12 767 ha, eleven months. This tool moves them into the database. The flight
tables, the ingest endpoint and the collector are not touched by it.

--dry-run IS THE DEFAULT. Running without --apply parses, reports and writes
nothing, so it is safe to point at a production database. The real first run
is the owner's, and the summary is printed so it can be compared against a
prediction written beforehand.

Rules this tool obeys, each of which cost a wrong number when the real files
were parsed by hand on 2026-08-03:

  a) A DATA ROW CAN LOOK LIKE A HEADER. The «Изоҳ» column contains sentences
     mentioning «майдон», so a header detector that searches for that word
     alone matched every data row and parsed whole sheets as empty --
     understating September by 1 548 ha. A row is a header only when ALL
     THREE hold: the first cell is exactly '№', some cell in the first four
     contains «ФХ номи», and some cell in the first six contains «Майдон» OR
     «Мадон» (the typo is in the real file).

  b) JUNK BELOW THE TOTALS ROW. After «Жами сумма» some sheets continue with
     rows like 'Доллар | 12220'; read as hectares that is 12 220 ha in one
     row. A data row must start with the dispatchers' own row counter (a
     number) and carry 0 < area_ha <= 1000.

  c) TWO PAYMENT BLOCKS PER SHEET, in either order, marked by lines reading
     «Справка (<month> ойи)» and «Нақд (<month> ойи)». The block a row falls
     in determines payment_type. Internal work appears as its own block
     («Тизим корхонаси») or as rows whose customer is a holding subdivision.

  d) DUPLICATE SHEETS AND DUPLICATE FILES. One file carries «свод ичи
     (Беҳзод)» and «свод ичи » with identical content; two other files
     overlap partially. Rows matching on
     (customer_raw, area_ha, work_date_from, amount) across the whole run are
     imported once and every skipped row is listed with BOTH provenances.

  e) DATE FORMATS. A datetime; '23-24.03.2026'; '13,16,17.05.2026';
     '27,30.03.2026'; '08-09.092025' (the dot is missing); empty. Ranges
     become from/to, comma lists become min/max, the original always stays in
     date_raw. A form that cannot be parsed keeps date_raw with both dates
     NULL and is counted and listed -- never dropped.

  f) PRICES ARE NOT CONSTANT AND ARE NEVER SUBSTITUTED. 200 000 so'm/ha
     dominates but nine other values occur, and the internal tariff is quoted
     as 76 458 on one sheet and 85 633 on another. An empty price cell is
     stored as NULL. It is NEVER computed from amount / area and never
     defaulted: a price this tool invented is indistinguishable from a price
     the farm agreed. The suggestion belongs to the manual entry form.

  g) SHEET TITLES. «свод ичи (Фурқат)», «свод ичи Шохрух», «свод ичи » --
     with brackets, without, sometimes empty. operator_raw comes from the
     title; when the title carries no name the file stem is used instead and
     the report says so for every such sheet.

  h) «Иш хаки» -- the operator's wage -- IS NOT IMPORTED. That is operator
     sub-accounting and was explicitly deferred by the owner.

Console output is ASCII only (NSSM / Windows cp1251 log safety). Everything
that has to name a farm, an operator or a sheet goes into the UTF-8 report
file written next to the manifest (or wherever --report points), because the
names are Cyrillic and the console cannot be trusted to carry them.

Safety:
  - stdlib sqlite3 only, never `from app import app`: create_app() calls
    db.create_all() at import time and would turn this into a writer even in
    --dry-run;
  - refuses to run and exits 2 when the database file is missing --
    sqlite3.connect() would otherwise create an empty one;
  - --dry-run opens the database read-only through a file: URI with mode=ro,
    so a mistake cannot write;
  - --apply writes inside ONE transaction and rolls the whole run back on any
    error; nothing is written outside drone_works, drone_customers and
    drone_customer_aliases.

Usage:

  python tools/import_drone_works.py --dir <folder> \\
      --manifest tools/drone_works_manifest.txt

  python tools/import_drone_works.py --dir <folder> \\
      --manifest tools/drone_works_manifest.txt --apply --batch 2026-08-first

On Windows (PowerShell has no &&, one command per line):

  cd C:\\transport-report
  & "C:\\Program Files\\Python314\\python.exe" tools\\import_drone_works.py --dir C:\\drone-books --manifest tools\\drone_works_manifest.txt
"""

import argparse
import csv
import datetime
import os
import re
import sqlite3
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from plate_norm import normalize_plate  # noqa: E402

DEFAULT_DB_PATH = os.path.join(REPO_ROOT, 'instance', 'transport.db')

PAYMENT_CASH = 'cash'
PAYMENT_TRANSFER = 'transfer'
PAYMENT_INTERNAL = 'internal'
# [REASON]: owner's decision of 2026-08-04 -- see parse_sheet. Roughly two
# fifths of the real corpus sits in sheets with no payment block at all.
PAYMENT_UNKNOWN = 'unknown'
PAYMENT_TYPES = (PAYMENT_CASH, PAYMENT_TRANSFER, PAYMENT_INTERNAL,
                 PAYMENT_UNKNOWN)
# --default-payment may not be used to declare rows unknown: that is what
# happens anyway, and offering it as a choice would suggest it does something.
PAYMENT_OVERRIDE_TYPES = (PAYMENT_CASH, PAYMENT_TRANSFER, PAYMENT_INTERNAL)

RECEIVED_KIND_RECEIVED = 'received'
RECEIVED_KIND_OPERATOR_DUE = 'operator_due'

# A single job is never larger than this. See trap (b).
MAX_AREA_HA = 1000.0

# What the owner's hand parser measured over all 28 books on 2026-08-04,
# grouped by the MANIFEST month. Printed next to the actual figures so the run
# can be compared against a prediction written beforehand instead of being
# read as "some numbers came out".
#
# [REASON]: April is the anchor and the only line corroborated by anything
# other than the same hand parser -- 6 388.83 ha agrees with our own DJI
# flight data for April 2026 (6 379.24 ha over 6 403 flights) to 0.15 %. The
# monthly split for September and October WILL differ here, because this tool
# groups by the row's own date and one book straddles the two months; the
# total is the firm number and the monthly rows are indicative. These figures
# come from a parser that has been wrong twice already: a disagreement is a
# finding to report, not a number to force.
EXPECTED_BY_MONTH = (
    ('2025-09', 378, 4937.73),
    ('2025-10', 28, 790.90),
    ('2026-03', 46, 1378.48),
    ('2026-04', 331, 6388.83),
    ('2026-05', 106, 2179.72),
    # [REASON]: 2026-06 — не прогноз владельца 2026-08-04 (июньских книг
    # тогда не существовало), а факт предпросмотра загрузок 2026-08-11:
    # «ИЮНЬ Агрокластер» 24 строки / 820.30 га + «Сервис … Июнь» 70 / 1 182.34.
    ('2026-06', 94, 2002.64),
    ('2026-07', 6, 62.73),
)
EXPECTED_TOTAL_ROWS = 895
EXPECTED_TOTAL_AREA = 15738.39
EXPECTED_ANCHOR_MONTH = '2026-04'


DATE_KIND_EXACT = 'date'      # one real day, from a datetime cell or dd.mm.yyyy
DATE_KIND_SPAN = 'span'       # a text range or comma list over several days
DATE_KIND_NONE = 'none'       # the cell was empty
DATE_KIND_UNPARSED = 'unparsed'  # a form this tool refuses to guess at


# ─── Normalisation ───────────────────────────────────────────────────────────

# [REASON]: an UZBEK ORTHOGRAPHIC fold applied BEFORE plate_norm's homoglyph
# fold, not instead of it. plate_norm.normalize_plate() folds the twelve
# Cyrillic/Latin homoglyph pairs, upper-cases and strips punctuation, and it
# is called here rather than reimplemented. What it deliberately does not do
# is fold the Uzbek letters, and the dispatchers write the same man as
# «Фурқат» and «Фуркат», «Беҳзод» and «Бехзод». Without this pass those are
# two different people. The target letters are the plain Cyrillic ones, so
# the result still passes through the homoglyph table unchanged in meaning.
_UZ_FOLD = {
    'Қ': 'К', 'Ғ': 'Г', 'Ҳ': 'Х', 'Ў': 'У', 'Ҷ': 'Ж', 'Ҝ': 'К',
    'қ': 'к', 'ғ': 'г', 'ҳ': 'х', 'ў': 'у', 'ҷ': 'ж',
    'Ъ': '', 'ъ': '', 'Ь': '', 'ь': '', 'Ё': 'Е', 'ё': 'е',
    '\u0451': 'е',
}
_UZ_TABLE = {ord(k): (v or None) for k, v in _UZ_FOLD.items()}

_WS_RE = re.compile(r'\s+')


def uz_fold(value):
    """Uzbek-letter fold, lower-cased, whitespace collapsed. Keyword matching."""
    if value is None:
        return ''
    return _WS_RE.sub(' ', str(value).translate(_UZ_TABLE).lower()).strip()


def name_key(value):
    """Comparison key for a person's name: Uzbek fold, then plate_norm."""
    return normalize_plate(str(value).translate(_UZ_TABLE)) if value else ''


def name_tokens(value):
    """Set of normalised word tokens of a name. Empty set for empty input.

    Tokenised BEFORE normalisation, because normalize_plate strips spaces:
    the dispatchers write «Шохрух» where the directory holds «Хамроев
    Шохрух», and a whole-string comparison could never match the two.
    """
    if not value:
        return set()
    tokens = set()
    for part in _WS_RE.split(str(value).strip()):
        key = name_key(part)
        if key:
            tokens.add(key)
    return tokens


def customer_key(value):
    """normalized_key of a farm spelling: case-folded, whitespace-collapsed.

    [REASON]: exactly what the task specifies for drone_customer_aliases, and
    deliberately NOT the Uzbek fold used for operator names. This key is
    stored in the database and the aliases screen has to be able to reproduce
    it by eye; a fold nobody can see would make two visibly different rows
    collide and look like a bug.
    """
    if value is None:
        return ''
    return _WS_RE.sub(' ', str(value)).strip().casefold()


# [REASON]: FIN-RECON-002, owner's decision of 27.10.2026. «Фукаро» is Uzbek
# for «citizen»: the dispatchers write it for any client without a legal
# entity, so the same spelling stands for DIFFERENT people. A card (or an
# alias) for it turns them into one debtor whose payments offset each other's
# debts -- on the June books 11 works / 55.85 ha / 11 170 000 were about to
# land on card #589 that way. A row whose WHOLE normalised name is one of
# these keys is stored with drone_customer_id NULL and payer_unidentified = 1,
# whatever alias the directory holds for it, and no card is created.
#
# EXACT keys, never a substring or a fuzzy match: «Жамшид (фукаро)» and
# «Фукаро (Халимов Ж)» name a person and keep resolving through their alias.
# The list is deliberately narrow and grows only by the owner's decision and
# a code change with a test -- «Ромитан т. Фукаро» is the owner's own second
# example. Spellings with a district in brackets («Фукаро (Шофиркон т.)») or
# «Ромитан Фукаро» already have cards; whether they are generic too is an
# open question for the owner and they are NOT listed here.
GENERIC_PAYER_KEYS = frozenset((
    'фукаро',
    'ромитан т. фукаро',
))


def is_generic_payer(customer_raw):
    """True when the whole spelling names a citizen only generically."""
    return customer_key(customer_raw) in GENERIC_PAYER_KEYS


# [REASON]: FIN-RECON-003, July 2026. «Когон Фукаро» (Kogon + «citizen», no
# personal name) is written once, 6 ha, in «Дрон Ғиждувон ПТЗ Июль.xlsx /
# свод ичи (Шахзод)» row 20. It is not in GENERIC_PAYER_KEYS, so the import
# would make it a NEW 'external' card -- and every later «Когон Фукаро» would
# land on that one card, the single-debtor fault #589 had. Whether it is
# generic is the OWNER's call: it has the shape of «Ромитан Фукаро», which the
# list above deliberately leaves open. So this list does not classify. A key
# here, with no alias in the directory, is never turned into a card by the
# importer itself: apply_rows() refuses while one is present, and the owner
# decides -- an alias to a named person's card through the decision table, or
# the key moves to GENERIC_PAYER_KEYS with a test. Exact keys, like the list
# above: «Ахмад (фукаро)» and every other spelling with a person's name in it
# are untouched.
PAYER_DECISION_PENDING_KEYS = frozenset((
    'когон фукаро',
))


def is_payer_decision_pending(key):
    """True when a customer key waits for the owner. Compared after the
    Uzbek fold: «Когон Фуқаро» is held like «Когон Фукаро». Holding refuses
    to decide, so a wider match is the safe side; GENERIC_PAYER_KEYS, which
    DOES decide, stays exact."""
    folded = uz_fold(key)
    return any(folded == uz_fold(k) for k in PAYER_DECISION_PENDING_KEYS)

# The word itself, after the Uzbek fold («фуқаро» and «фукаро» alike). Used
# only to LIST new cards whose spelling contains it, for a human to look at;
# it never decides anything.
GENERIC_PAYER_WORD = 'фукаро'


def has_generic_payer_word(customer_raw):
    """True when «фукаро» is a whole word of the spelling. Report only."""
    words = re.split(r'[\W_]+', uz_fold(customer_raw))
    return GENERIC_PAYER_WORD in words


def _text(value):
    if value is None:
        return ''
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _number(value):
    """float(value) or None. Accepts '1 234,56', '1 234.56', '200 000 сум'."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, (datetime.date, datetime.datetime)):
        return None
    text = str(value).strip()
    if not text:
        return None
    # Non-breaking spaces and thin spaces are what Excel puts in a thousands
    # separator when the value was typed as text.
    text = text.replace('\xa0', '').replace('\u202f', '').replace(' ', '')
    text = text.replace(',', '.')
    match = re.match(r'^-?\d+(\.\d+)?', text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _int_counter(value):
    """The dispatchers' own row counter: an integer, nothing else."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    text = _text(value)
    if re.match(r'^\d+$', text):
        return int(text)
    return None


# ─── Trap (a): header detection ──────────────────────────────────────────────

HEADER_CUSTOMER_MARK = uz_fold('ФХ номи')
HEADER_AREA_MARKS = (uz_fold('Майдон'), uz_fold('Мадон'))


def is_header_row(cells):
    """True only when all three conditions hold. See trap (a).

    A header detector that merely searches for «майдон» matches every data
    row whose «Изоҳ» tells a story about a field, and the sheet parses as
    empty. tools/test_import_drone_works.py monkeypatches this function with
    that naive version and asserts the wrong number appears -- a guard with
    no failing negative control is not covered.
    """
    if not cells:
        return False
    if _text(cells[0]) != '№':
        return False
    if not any(HEADER_CUSTOMER_MARK in uz_fold(c) for c in cells[:4]):
        return False
    return any(mark in uz_fold(c)
               for c in cells[:6] for mark in HEADER_AREA_MARKS)


# ─── Sheet selection ─────────────────────────────────────────────────────────

DETAIL_SHEET_PREFIX = uz_fold('свод ичи')


def is_detail_sheet(title):
    """True only for the per-job detail sheets. Everything else is skipped.

    [REASON]: the 2026-08-04 dry run over the real books offered rows from
    «олинмаган пуллар» and «свод фх» as well as from «свод ичи». They were all
    rejected at the time for a different reason -- no payment block -- which
    hid the problem; once that rejection is lifted they would be IMPORTED, and
    they are duplicates of rows already present in the detail sheet
    («Бахром Хайрулло фх, 33.5 га» is in both) plus «ЖАМИ:» total lines. The
    dedup would catch some of them and silently pick whichever file listing
    reached it first, which is not a decision a tool should make.

    The rule is the sheet TITLE, not its shape: «ОБШИЙ СВОД» and its suffixed
    variants, «олинмаган пуллар», «свод фх», «Свод1», «Расход» and
    «карпаратив почта» are summaries and registers, and the payment split
    lives only in «ОБШИЙ СВОД». A detail sheet may name its operator in
    brackets, after a space, or not at all -- «свод ичи (Фурқат)»,
    «свод ичи Шохрух», «свод ичи » -- and all three are imported.
    """
    return uz_fold(title).startswith(DETAIL_SHEET_PREFIX)


# ─── Column mapping ──────────────────────────────────────────────────────────

# EXACT normalised phrases, not fragments.
#
# [REASON]: the first version matched header fragments, and on the real books
# «суммаси» hit «Хизмат кўрсатиш суммаси» -- the PRICE -- and stored it as the
# amount, while «Жами сумма (обьём)», the real amount, was never mapped at all.
# Every one of the twenty-four layouts reported `amount->3 ... НЕ ОПОЗНАНЫ
# КОЛОНКИ: price`, and the run's amount total came out eleven times smaller
# than its received total, which is arithmetically impossible. Two headers also
# differ by two letters: «Хизмат кўрсатиш СУМмаси» is the price and «Хизмат
# кўрсатиш САНаси» is a date, and seven layouts carry the second.
#
# Within a field the phrases are in PREFERENCE order: the first one present in
# the sheet wins, and any other match is reported rather than silently dropped.
# Across fields the phrases are matched longest-first, so a longer phrase can
# never be shadowed by a shorter one contained in it.
FIELD_PHRASES = (
    ('customer', ('ФХ номи',)),
    ('area', ('Майдон (га)', 'Мадон (га)')),
    ('price', ('Хизмат кўрсатиш суммаси',)),
    ('amount', ('Жами сумма (обьём)', 'Жами сумма', 'Сумма (без НДС)')),
    ('received', ('Кирим қилинган', 'Кирим қилинган (Фактура)',
                  'Кирим қилиши керак')),
    ('operator_due', ('Оператор топшириши керак',)),
    ('date', ('Дрон ишлаган сана', 'Кирим қилинган сана', 'Сана',
              'Хизмат кўрсатиш санаси')),
    ('note', ('*Изоҳ', '*Изоҳ / Йуналиш')),
    ('row_operator', ('Дрон бошқарувчи оператор',)),
)

# Expense columns. ALL of them present in a sheet are summed into other_costs;
# six of the twenty-four layouts carry two at once.
EXPENSE_PHRASES = ('Бошка харажатлар', 'Транспорт харажати', 'ЁММ харажати',
                   'Ёкилги ва бошка харажатлар')
# [REASON]: the twenty-fifth layout, FIN-RECON-002. «Дрон Ғиждувон ПТЗ
# Июнь.xlsx / свод ичи (Усмон)» carries «Ёкилги ва бошка харажатлар» next to
# «Бошка харажатлар»; unmapped, the dry run of 2026-10-22 stored 3 500 000 of
# the 6 425 000 the book spends on 13 rows. The scan of all 34 books of
# 2025-09..2026-06 found this header in that one sheet only, so no row already
# in drone_works was read without it.
# [REASON]: the vehicle plate follows the header in brackets --
# «Транспорт харажати (Мерс 80 L 422 MA)», «(Газель 80 120 FBA)», «Газел» --
# so this one phrase is matched as a PREFIX. It is the only prefix rule there
# is; everything else is exact, because a prefix rule is how «суммаси» ate the
# price column in the first place.
EXPENSE_PREFIXES = ('Транспорт харажати',)

# Headers that exist, are understood, and are deliberately not imported.
#   Иш хаки              -- the operator's wage, sub-accounting, deferred
#   Сумма ( НДС)         -- the VAT part; «Сумма (без НДС)» is the amount
#   Ишлаган контур рақами -- the field-contour number, no consumer yet
#   №                    -- the dispatchers' row counter, read positionally
#                           as cells[0] by the data-row guard, never a field
IGNORED_PHRASES = ('Иш хаки', 'Сумма ( НДС)', 'Ишлаган контур рақами', '№')

# Without these two a sheet cannot be read at all: there is no job without a
# place to read the farm and the area from. Everything else degrades to NULL
# and is reported per sheet.
REQUIRED_FIELDS = ('customer', 'area')
OPTIONAL_FIELDS = ('price', 'amount', 'received', 'date', 'note')

_ZERO_WIDTH = dict.fromkeys(
    [0x00ad, 0x200b, 0x200c, 0x200d, 0xfeff], None)


def header_key(value):
    """Normalised comparison form of a header cell.

    Whitespace of every kind -- including the double space in «Дрон  ишлаган
    сана», embedded newlines and non-breaking spaces -- collapses to one
    space; then trim, lower-case and the same Uzbek-letter fold uz_fold()
    already applies to names. Zero-width characters are removed outright:
    they are invisible in Excel and would make two identical-looking headers
    compare unequal with nothing on screen to explain it.
    """
    if value is None:
        return ''
    text = str(value).translate(_ZERO_WIDTH)
    text = text.replace('\xa0', ' ').replace(' ', ' ')
    return uz_fold(text)


def _build_phrase_index():
    """[(key, field)] over every phrase, longest key first."""
    entries = []
    for field, phrases in FIELD_PHRASES:
        for phrase in phrases:
            entries.append((header_key(phrase), field))
    for phrase in EXPENSE_PHRASES:
        entries.append((header_key(phrase), 'expense'))
    for phrase in IGNORED_PHRASES:
        entries.append((header_key(phrase), 'ignored'))
    entries.sort(key=lambda pair: len(pair[0]), reverse=True)
    return entries


PHRASE_INDEX = _build_phrase_index()
EXPENSE_PREFIX_KEYS = tuple(sorted((header_key(p) for p in EXPENSE_PREFIXES),
                                   key=len, reverse=True))
# Preference order inside a field, as a lookup: phrase key -> its position.
FIELD_PREFERENCE = {}
for _field, _phrases in FIELD_PHRASES:
    for _position, _phrase in enumerate(_phrases):
        FIELD_PREFERENCE[(_field, header_key(_phrase))] = _position


class ColumnMap(object):
    """What one header row resolved to. Everything the report prints."""

    def __init__(self):
        self.fields = {}          # field -> column index
        self.chosen = {}          # field -> the header text that won
        self.expenses = []        # [(index, header)] -- ALL of them, summed
        self.unmatched = []       # [(index, header)] -- named in the report
        self.ignored = []         # [(index, header)] -- known, not imported
        self.shadowed = []        # [(field, index, header)] -- second match

    def get(self, field, default=None):
        return self.fields.get(field, default)

    def __contains__(self, field):
        return field in self.fields

    def __bool__(self):
        return bool(self.fields)

    __nonzero__ = __bool__

    @property
    def received_kind(self):
        """Which header the figure in received_amount came from, or None.

        [REASON]: «Кирим қилинган» is money already handed in; «Оператор
        топшириши керак» is what the operator still owes. They sit in the same
        position and both equal amount - other_costs, so a sheet carrying only
        the second must not be read as collections. When a sheet carries both,
        the actual receipt wins and the other column is reported as shadowed.
        """
        if 'received' in self.fields:
            return 'received'
        if 'operator_due' in self.fields:
            return 'operator_due'
        return None

    @property
    def received_index(self):
        if 'received' in self.fields:
            return self.fields['received']
        return self.fields.get('operator_due')

    def missing_fields(self):
        return [f for f in OPTIONAL_FIELDS
                if f not in self.fields
                and not (f == 'received' and 'operator_due' in self.fields)]


def map_columns(header_cells):
    """Resolve one header row into a ColumnMap. Exact phrases only.

    [REASON]: NO POSITIONAL FALLBACK, and no fragment matching. Guessing that
    the column after the area is the price is exactly what produced a run
    whose amount column held prices. A header that matches nothing keeps its
    field NULL and is NAMED in the report with its sheet -- there were 24
    layouts and there may be a 25th, and the report is how the 25th gets
    found instead of quietly mis-parsed.
    """
    mapping = ColumnMap()
    for index, cell in enumerate(header_cells):
        key = header_key(cell)
        if not key:
            continue
        header = _text(cell)
        field = None
        for phrase_key, candidate in PHRASE_INDEX:
            if key == phrase_key:
                field = candidate
                break
        if field is None:
            for prefix in EXPENSE_PREFIX_KEYS:
                if key.startswith(prefix):
                    field = 'expense'
                    break
        if field is None:
            mapping.unmatched.append((index, header))
            continue
        if field == 'ignored':
            mapping.ignored.append((index, header))
            continue
        if field == 'expense':
            mapping.expenses.append((index, header))
            continue
        if field in mapping.fields:
            # A second column matched the same field. Keep the one whose
            # phrase is earlier in that field's preference list, and report
            # the loser rather than dropping it in silence.
            old_key = header_key(mapping.chosen[field])
            old_rank = FIELD_PREFERENCE.get((field, old_key), 99)
            new_rank = FIELD_PREFERENCE.get((field, key), 99)
            if new_rank < old_rank:
                mapping.shadowed.append(
                    (field, mapping.fields[field], mapping.chosen[field]))
                mapping.fields[field] = index
                mapping.chosen[field] = header
            else:
                mapping.shadowed.append((field, index, header))
            continue
        mapping.fields[field] = index
        mapping.chosen[field] = header
    return mapping


# ─── Trap (c) and (h): block markers ─────────────────────────────────────────

MARK_TRANSFER = uz_fold('Справка')
MARK_CASH = uz_fold('Нақд')
MARK_INTERNAL = uz_fold('Тизим корхонаси')
MARK_WAGE = uz_fold('Иш хаки')


def is_wage_marker(cells):
    """True for «Иш хаки» -- the operator's wage. Never imported, trap (h)."""
    return any(MARK_WAGE in uz_fold(c) for c in cells)


# FIN-RECON import safeguards; neither rule alters historical records.
def is_crossperiod_wage_carryover(row):
    """Hold older work entered in a later book explicitly for wage settlement."""
    when = row.get('work_date_from')
    month = row.get('period_month')
    if when is None or not month:
        return False
    start = datetime.date(int(month[:4]), int(month[5:7]), 1)
    note = uz_fold(row.get('note'))
    return when < start and MARK_WAGE in note


def source_file_fold(name):
    """Filename equivalence is for collision detection, not automatic aliasing."""
    return re.sub(r'[\s_]+', ' ', str(name or '')).strip().casefold()


def filename_alias_conflicts(con, rows):
    """Reject unsafe --apply when names differ but job provenance is the same."""
    indexed = {}
    for fname, sheet, number, area in con.execute(
            'SELECT source_file, source_sheet, source_row, area_ha '
            'FROM drone_works WHERE source_file IS NOT NULL '
            'AND source_row IS NOT NULL'):
        if number is None or area is None:
            continue
        try:
            rownum = int(number)
        except (ValueError, TypeError):
            continue
        key = (source_file_fold(fname), sheet, rownum)
        indexed.setdefault(key, []).append((fname, float(area)))
    conflicts = []
    for row in rows:
        key = (source_file_fold(row['source_file']), row['source_sheet'],
               int(row['source_row']))
        for fname, area in indexed.get(key, ()):
            if fname == row['source_file']:
                continue
            if abs(float(row['area_ha']) - area) < 0.00001:
                conflicts.append((fname, row['source_file'],
                                  row['source_sheet'], row['source_row']))
    return conflicts


def payment_marker(cells):
    """'cash' / 'transfer' / 'internal' / None for a block-separator row.

    A sheet may start with either block, so there is no default: rows that
    appear before any marker are REJECTED with a reason and listed, never
    assigned a payment type this tool made up. --default-payment is the
    owner's explicit override for that case and is reported loudly.
    """
    folded = ' '.join(uz_fold(c) for c in cells if _text(c))
    if not folded:
        return None
    if MARK_INTERNAL in folded:
        return PAYMENT_INTERNAL
    if MARK_TRANSFER in folded:
        return PAYMENT_TRANSFER
    if MARK_CASH in folded:
        return PAYMENT_CASH
    return None


# ─── Trap (b): what counts as a data row ─────────────────────────────────────

def area_in_range(area):
    """0 < area <= 1000. See trap (b): 'Доллар | 12220' below the totals row."""
    return area is not None and 0 < area <= MAX_AREA_HA


# ─── FIN-RECON-003: a job row whose «№» cell is empty ────────────────────────
#
# [REASON]: «Дрон Ғиждувон ПТЗ Июль.xlsx / свод ичи (Усмон)» numbers its rows
# 1..16 and then stops: rows 11 and 35..52 carry a farm, an area, a price, an
# amount and a date, and an empty «№». The book's own «Жами сумма» lines add
# them in (114.06 and 352.06 ha). The data-row rule of trap (b) demanded the
# counter, so all nineteen -- 189.25 ha -- were dropped by a bare `continue`
# and the run printed «rows rejected: 0». The counter is the dispatcher's
# typing, not a property of the job: it is never stored, never part of any
# identity (provenance is the physical Excel row), and it repeats inside one
# sheet («2, 2» in Фаррух, «13, 15, 16, 18» twice in Шахзод).
#
# A row without the counter is a job only on positive evidence, because the
# counter was what kept the totals line and the junk below it out:
#   - it is not a totals line («Жами …», «ИТОГО», «Всего» as a whole first
#     word in a cell up to the area column -- «Жамила фх» is a farm);
#   - it names a farm in the customer column;
#   - its area is in range, as for any job;
#   - and the book says something about the job beyond name and area: a
#     date, a price or an amount.
# Anything else that carries job data and matches no rule is not skipped: it
# is listed as UNPARSED_CANDIDATE and apply_rows() refuses until it is read.

TOTAL_LABEL_WORDS = ('жами', 'итого', 'всего')


def _first_word(value):
    for word in re.split(r'[\W_]+', uz_fold(value)):
        if word:
            return word
    return ''


def total_label(cells, mapping):
    """The totals label of the row, or ''. Cells up to the area column.

    «Жами сумма:» stands in the customer column and «ИТОГО:» in the area
    column of the July books; the fixtures carry «Жами сумма» in column A.
    """
    limit = 3
    if mapping is not None and mapping.get('area') is not None:
        limit = mapping.get('area') + 1
    for value in cells[:limit]:
        text = _text(value)
        if text and _first_word(text) in TOTAL_LABEL_WORDS:
            return text
    return ''


def _cell_at(cells, index):
    if index is None or index >= len(cells):
        return None
    return cells[index]


def _farm_name(cells, mapping):
    """The farm column as text, or ''. A NUMBER there is not a farm name:
    the July layout puts trap (b)'s «Доллар | 12220» exactly there."""
    value = _cell_at(cells, mapping.get('customer'))
    if isinstance(value, str):
        return value.strip()
    return ''


def has_work_data(cells, mapping):
    """A farm name, or anything at all in the area column.

    [REASON]: the candidate test, deliberately NOT the acceptance test: it
    ignores the counter altogether. A row with job data that no rule accepts
    or names must surface, which is the whole point of this function. A job
    is hectares for somebody, so the signs are the farm and the area; a price,
    an amount or a date alone is not one -- trap (b)'s «Доллар | 12220» puts
    its number in the date column of the fixture and is not a job. The area
    column counts with ANY value, text included: next to a farm or a «№» a
    word where the hectares belong is a job the parser cannot read; alone it
    is a note, and parse_sheet() lists it under its own name.
    """
    if _farm_name(cells, mapping):
        return True
    value = _cell_at(cells, mapping.get('area'))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return True
    return bool(_text(value))


def counterless_job_evidence(cells, mapping, area):
    """(True, '') when a row with an empty «№» is a job; else (False, why)."""
    if total_label(cells, mapping):
        return False, 'totals line'
    if not _farm_name(cells, mapping):
        return False, 'no row number and no farm name'
    if not area_in_range(area):
        return False, 'no row number and no area in range'
    _f, _t, kind = parse_date_cell(_cell_at(cells, mapping.get('date')))
    if kind in (DATE_KIND_EXACT, DATE_KIND_SPAN):
        return True, ''
    if _number(_cell_at(cells, mapping.get('price'))) is not None:
        return True, ''
    if _number(_cell_at(cells, mapping.get('amount'))) is not None:
        return True, ''
    return False, 'no row number; farm and area only, no date, price or amount'


def _numbered_with_numbers(cells):
    """A counter in column A and at least one more number in the row."""
    if _int_counter(cells[0] if cells else None) is None:
        return False
    return any(isinstance(c, (int, float)) and not isinstance(c, bool)
               for c in cells[1:])


def payment_when_no_block():
    """What a row with no payment block above it becomes. None = reject it.

    [REASON]: a named decision rather than a literal, because it has changed
    twice and each change was a decision, not a refactor.
      2026-08-04, DRONE-WORKS-IMPORT-FIX-001 section 4: rejection -> 'unknown'.
        Rejection had cost 360 real rows.
      2026-08-11, DRONE-CASH-DEFAULT-001, decision of the owner, quoted:
        «во всех книгах диспетчеров, если тип оплаты не стоит Справка или
        перечисление или иное - то это считается наличка».
        'unknown' -> PAYMENT_CASH.

    This is a BUSINESS RULE, not a guess the tool is entitled to make on its
    own: the books simply do not mark cash, because cash is what a row is
    unless it says otherwise. 383 of 913 rows in the September-October books
    carried no marker and sat under a label nobody could act on.

    The negative control in tools/test_import_drone_works.py swaps in the
    version that returns None and watches the rows disappear again.
    """
    return PAYMENT_CASH


# ─── Trap (e): dates ─────────────────────────────────────────────────────────

# '23-24.03.2026', '13,16,17.05.2026', '27,30.03.2026', '08-09.092025'.
# The dot between month and year is optional -- it is genuinely missing in one
# real file. Day part: digits, commas, dashes and spaces.
_DATE_TEXT_RE = re.compile(
    r'^\s*([\d]{1,2}(?:\s*[,\-]\s*[\d]{1,2})*)\s*\.\s*(\d{1,2})\s*\.?\s*'
    r'(\d{4})\s*$')


def parse_date_cell(value):
    """(date_from, date_to, kind). Never raises, never guesses.

    kind is one of DATE_KIND_EXACT / DATE_KIND_SPAN / DATE_KIND_NONE /
    DATE_KIND_UNPARSED. An unparsed form keeps its raw text and both dates
    NULL; the report counts and lists every one of them.
    """
    if value is None:
        return None, None, DATE_KIND_NONE
    if isinstance(value, datetime.datetime):
        return value.date(), value.date(), DATE_KIND_EXACT
    if isinstance(value, datetime.date):
        return value, value, DATE_KIND_EXACT
    text = _text(value)
    if not text:
        return None, None, DATE_KIND_NONE

    match = _DATE_TEXT_RE.match(text)
    if not match:
        return None, None, DATE_KIND_UNPARSED

    day_part, month_s, year_s = match.groups()
    try:
        month = int(month_s)
        year = int(year_s)
    except ValueError:
        return None, None, DATE_KIND_UNPARSED

    days = []
    for chunk in day_part.split(','):
        chunk = chunk.strip()
        if not chunk:
            return None, None, DATE_KIND_UNPARSED
        if '-' in chunk:
            bounds = [p.strip() for p in chunk.split('-')]
            if len(bounds) != 2 or not all(b.isdigit() for b in bounds):
                return None, None, DATE_KIND_UNPARSED
            low, high = int(bounds[0]), int(bounds[1])
            # [REASON]: a descending range means the span crosses a month
            # boundary ('30-01.04'). min/max would read it as the whole month.
            # Refusing is the honest outcome -- the row is listed, not dropped.
            if low > high:
                return None, None, DATE_KIND_UNPARSED
            days.extend([low, high])
        else:
            if not chunk.isdigit():
                return None, None, DATE_KIND_UNPARSED
            days.append(int(chunk))
    if not days:
        return None, None, DATE_KIND_UNPARSED

    try:
        date_from = datetime.date(year, month, min(days))
        date_to = datetime.date(year, month, max(days))
    except ValueError:
        return None, None, DATE_KIND_UNPARSED

    kind = DATE_KIND_EXACT if date_from == date_to else DATE_KIND_SPAN
    return date_from, date_to, kind


# ─── Trap (f): the price is never computed ───────────────────────────────────

def resolve_price(price_cell, amount, area):
    """The price EXACTLY as written, or None. amount and area are ignored.

    They are parameters only so the negative control in
    tools/test_import_drone_works.py can swap in the tempting version that
    returns amount / area and watch invented prices appear.
    """
    return _number(price_cell)


# ─── Trap (g): operator from the sheet title ─────────────────────────────────

_TITLE_PREFIX_RE = re.compile(r'^\s*свод\s*ичи\s*', re.IGNORECASE)
_BRACKETS_RE = re.compile(r'[()\[\]]')


def operator_from_sheet_title(title, file_stem):
    """(operator_raw, took_it_from_the_file_name).

    «свод ичи (Фурқат)» -> 'Фурқат'; «свод ичи Шохрух» -> 'Шохрух';
    «свод ичи » -> the file stem, flagged, and named in the report.
    """
    raw = _BRACKETS_RE.sub(' ', _TITLE_PREFIX_RE.sub('', _text(title)))
    raw = _WS_RE.sub(' ', raw).strip()
    if raw:
        return raw, False
    return _WS_RE.sub(' ', _text(file_stem)).strip(), True


# ─── Trap (d): duplicates ────────────────────────────────────────────────────

def duplicate_key(row):
    """Coarse numeric candidate fingerprint; not proof of a duplicate."""
    return (customer_key(row['customer_raw']),
            round(float(row['area_ha']), 4),
            row['work_date_from'],
            None if row['amount'] is None else round(float(row['amount']), 4))


def proven_duplicate_work(first, later):
    """Only suppress proven mirrors; keep other operators and same-sheet jobs."""
    if (first['source_file'] == later['source_file']
            and first['source_sheet'] == later['source_sheet']):
        return False
    if first['period_month'] != later['period_month']:
        return False
    if first['payment_type'] != later['payment_type']:
        return False
    if first['source_file'] != later['source_file']:
        for field in ('date_raw', 'note', 'received_amount',
                      'received_kind', 'other_costs', 'price_per_ha'):
            if first.get(field) != later.get(field):
                return False
    a = name_tokens(first['operator_raw'])
    b = name_tokens(later['operator_raw'])
    if not a or not b:
        return False
    if a == b:
        return True
    # A mirrored unlabelled sheet may inherit the filename as an operator.
    return (first['source_file'] == later['source_file']
            and ('file' in (first['operator_source'], later['operator_source']))
            and (a < b or b < a))


# ─── The manifest ────────────────────────────────────────────────────────────

_MANIFEST_RE = re.compile(r'^(.*\S)\s+(\d{4}-\d{2})\s*$')


def read_manifest(path):
    """{file name: 'YYYY-MM'}. Two columns, '#' starts a comment.

    The file name may contain spaces, so the LAST whitespace-separated field
    is the period and everything before it is the name.
    """
    mapping = {}
    problems = []
    with open(path, encoding='utf-8-sig') as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.split('#', 1)[0].rstrip()
            if not line.strip():
                continue
            match = _MANIFEST_RE.match(line)
            if not match:
                problems.append((lineno, line.strip()))
                continue
            mapping[match.group(1).strip()] = match.group(2)
    return mapping, problems


# ─── Reading one workbook ────────────────────────────────────────────────────

class SheetReport(object):
    """What one sheet turned out to be. Everything the report file prints."""

    def __init__(self, file_name, sheet_name):
        self.file_name = file_name
        self.sheet_name = sheet_name
        self.operator_raw = ''
        self.operator_from_file_name = False
        self.header_row = None
        self.column_map = None
        self.header_cells = []
        self.missing_fields = []
        self.rows = 0
        self.area = 0.0
        self.rejections = []
        self.wage_rows = 0
        self.blocks = []
        # DRONE-WORKS-IMPORT-FIX-001
        self.skipped_reason = None       # non-detail sheets, see is_detail_sheet
        self.operator_from_row = 0       # rows resolved from the row column
        self.operator_from_sheet = 0     # rows resolved from the sheet title
        self.unknown_payment_rows = 0
        self.empty_customer_rows = 0
        # FIN-RECON-003. Every non-empty row lands in exactly one class; the
        # report prints the bridge and the classes must add up to the rows.
        self.row_classes = dict.fromkeys(ROW_CLASSES, 0)
        self.counterless_rows = 0        # jobs accepted with an empty «№»
        self.unparsed = []               # [(excel_row, reason, cells)]
        # The book's own «Жами …» lines against what was read above them:
        # [(excel_row, label, book_area, block_area, sheet_area, verdict)]
        self.total_checks = []


# The classes of a non-empty row of a detail sheet, in report order.
#   job / job_no_number  -- accepted as a job (before the run-wide dedup)
#   wage                 -- a job-shaped row of an «Иш хаки» block, trap (h)
#   rejected             -- job data, refused by a NAMED rule (area out of
#                           range, no payment block), listed with the reason
#   unparsed             -- job data that no rule accepts or names: listed as
#                           UNPARSED_CANDIDATE, and --apply refuses
#   totals               -- «Жами …» / «ИТОГО» lines
#   header, block_marker -- the header row; «Справка», «Нақд», «Иш хаки» lines
#   empty_numbered       -- a counter and nothing else (the empty template)
#   no_job_data          -- text or numbers outside the job columns
#   above_header         -- the title, the phone, anything before the header
ROW_CLASSES = ('job', 'job_no_number', 'wage', 'rejected', 'unparsed',
               'totals', 'header', 'block_marker', 'empty_numbered',
               'no_job_data', 'above_header')
CANDIDATE_CLASSES = ('job', 'job_no_number', 'wage', 'rejected', 'unparsed')
TOTAL_TOLERANCE_HA = 0.005


def _row_cells(row):
    return list(row)


def parse_sheet(ws, file_name, sheet_name, period_month, subdivision,
                default_payment=None, formula_cells=None):
    """Parse one worksheet. Returns (rows, SheetReport).

    rows are plain dicts, not yet resolved against the database and not yet
    de-duplicated: both of those are decisions about the whole run.
    """
    report = SheetReport(file_name, sheet_name)
    stem = os.path.splitext(file_name)[0]
    report.operator_raw, report.operator_from_file_name = (
        operator_from_sheet_title(sheet_name, stem))

    rows = []
    mapping = None
    current_payment = default_payment
    in_wage_block = False
    # FIN-RECON-003: see ROW_CLASSES and counterless_job_evidence().
    usable_header_seen = False
    numbered_above_header = []   # listed only if no usable header ever comes
    block_area = 0.0             # jobs since the last header, marker or total
    sheet_area = 0.0             # jobs since the top of the sheet

    def classify(name):
        report.row_classes[name] += 1

    for excel_row, raw in enumerate(ws.iter_rows(values_only=True), 1):
        cells = _row_cells(raw)
        if not any(_text(c) for c in cells):
            continue

        if is_header_row(cells):
            classify('header')
            mapping = map_columns(cells)
            report.header_row = excel_row
            report.header_cells = [_text(c) for c in cells]
            report.column_map = mapping
            report.missing_fields = mapping.missing_fields()
            missing_required = [f for f in REQUIRED_FIELDS if f not in mapping]
            if missing_required:
                report.rejections.append(
                    (excel_row, 'header without %s column'
                     % '/'.join(missing_required), cells))
                mapping = None
            else:
                usable_header_seen = True
            block_area = 0.0
            continue

        counter = _int_counter(cells[0] if cells else None)
        area = (_number(cells[mapping.get('area')])
                if mapping is not None and 'area' in mapping
                and mapping.get('area') < len(cells)
                else None)
        looks_like_data = counter is not None and area_in_range(area)
        counter_missing = False
        why_not = ''
        if not looks_like_data and counter is None and mapping is not None:
            looks_like_data, why_not = counterless_job_evidence(
                cells, mapping, area)
            counter_missing = looks_like_data

        if not looks_like_data:
            if is_wage_marker(cells):
                classify('block_marker')
                in_wage_block = True
                report.blocks.append((excel_row, 'wage'))
                block_area = 0.0
                continue
            marker = payment_marker(cells)
            if marker is not None:
                classify('block_marker')
                current_payment = marker
                in_wage_block = False
                report.blocks.append((excel_row, marker))
                block_area = 0.0
                continue
            # Junk, totals rows and everything else that is not a job.
            if counter is not None and area is not None:
                # Trap (b) proper: it has a counter and a number where the
                # area lives, but the number cannot be a single job.
                classify('rejected')
                report.rejections.append(
                    (excel_row, 'area out of range (%.2f)' % area, cells))
                continue
            # [REASON]: FIN-RECON-003. Until 03.11.2026 every row reaching
            # this point was dropped by a bare `continue`, and nineteen real
            # jobs went with it. Now each one is named: a class for the
            # bridge, or UNPARSED_CANDIDATE when it carries job data that no
            # rule accepts -- and --apply refuses while one exists.
            if mapping is None:
                if not usable_header_seen and report.header_row is None:
                    classify('above_header')
                    if _numbered_with_numbers(cells):
                        numbered_above_header.append((excel_row, cells))
                elif _numbered_with_numbers(cells) and not in_wage_block:
                    classify('unparsed')
                    report.unparsed.append(
                        (excel_row, 'numbered row under a header without '
                         'the farm or area column', cells))
                else:
                    classify('no_job_data')
                continue
            label = total_label(cells, mapping)
            if label:
                classify('totals')
                if area is not None:
                    if abs(area - block_area) <= TOTAL_TOLERANCE_HA:
                        verdict = 'block'
                    elif abs(area - sheet_area) <= TOTAL_TOLERANCE_HA:
                        verdict = 'sheet'
                    else:
                        verdict = 'MISMATCH'
                    report.total_checks.append(
                        (excel_row, label, area, block_area, sheet_area,
                         verdict))
                block_area = 0.0
                continue
            if not has_work_data(cells, mapping):
                classify('empty_numbered' if counter is not None
                         else 'no_job_data')
                continue
            if in_wage_block:
                # Trap (h): nothing in an «Иш хаки» block is a job. Counted
                # in the bridge only; wage_rows keeps its old meaning.
                classify('wage')
                continue
            if area is not None and not area_in_range(area):
                classify('rejected')
                report.rejections.append(
                    (excel_row, 'area out of range (%.2f)' % area, cells))
                continue
            if (counter is None and area is None
                    and not _farm_name(cells, mapping)):
                # [REASON]: words in the area column and nothing else of a
                # job -- no farm, no «№». In «Имомов Бехзод Пешку ПТЗ.xlsx»
                # it is «Кеча топширган» (cash handed over), under the
                # totals. Named and listed like trap (b)'s «Доллар | 12220»,
                # not a blocker: there is no job in it to lose.
                classify('rejected')
                report.rejections.append(
                    (excel_row, 'note in the area column (no farm, no row '
                     'number)', cells))
                continue
            if counter is not None:
                area_text = _text(_cell_at(cells, mapping.get('area')))
                why_not = ('numbered row whose area cell is not a number '
                           '(%s)' % area_text if area_text
                           else 'numbered row without an area')
            classify('unparsed')
            report.unparsed.append((excel_row, why_not, cells))
            continue

        # Mentioning wages in a job note must not discard the job.
        customer_index = mapping.get('customer') if mapping is not None else None
        wage_customer = (customer_index is not None
                         and customer_index < len(cells)
                         and uz_fold(cells[customer_index]) == MARK_WAGE)
        if in_wage_block or wage_customer:
            classify('wage')
            report.wage_rows += 1
            continue

        if mapping is None:
            classify('rejected')
            report.rejections.append(
                (excel_row, 'data row before any recognised header', cells))
            continue

        def cell(field):
            index = mapping.get(field)
            if index is None or index >= len(cells):
                return None
            return cells[index]

        def cell_at(index):
            if index is None or index >= len(cells):
                return None
            return cells[index]

        # [REASON]: an EMPTY customer is data, not an error. All nine internal
        # jobs in «Агрокластер Дрон маълумот МАЙ.xlsx / свод ичи (Сайфулло)»
        # carry no farm name and the internal tariff 85 632.96: on the
        # holding's own land nobody writes one down. Rejecting them was why
        # the first run reported «internal 0 rows». The row is kept with an
        # empty customer_raw, no drone_customers row and no alias are created,
        # and the reports show «Заказчик не указан» separately from «Заказчик
        # не определён» -- missing data and unmatched data are different facts.
        customer_raw = _text(cell('customer'))
        if not customer_raw:
            report.empty_customer_rows += 1

        payment = current_payment
        # Internal work also appears as ordinary rows whose customer IS a
        # holding subdivision -- see trap (c).
        if customer_raw and MARK_INTERNAL in uz_fold(customer_raw):
            payment = PAYMENT_INTERNAL
        if payment is None:
            # [REASON]: owner's decision of 2026-08-04. The September and
            # October detail sheets carry no «Справка (…ойи)» / «Нақд (…ойи)»
            # markers at all -- the split lives only in the «ОБШИЙ СВОД»
            # summary sheets, which are not imported. Rejecting those rows
            # threw away 360 jobs with their hectares, customers and dates
            # over the one field that was missing. 'unknown' keeps the row and
            # names the gap; the owner sets the type on the works screen.
            payment = payment_when_no_block()
            if payment is None:
                classify('rejected')
                report.rejections.append(
                    (excel_row, 'no payment block above this row', cells))
                continue
            report.unknown_payment_rows += 1

        amount = _number(cell('amount'))
        date_cell = cell('date')
        date_from, date_to, date_kind = parse_date_cell(date_cell)

        # [REASON]: other_costs is the SUM of every expense column present.
        # Six of the twenty-four layouts carry two at once, e.g. «ЁММ
        # харажати» together with «Транспорт харажати (Мерс 80 L 422 MA)».
        # Taking only the first would understate the costs of exactly those
        # sheets and nothing would say so.
        other_costs = None
        for index, _header in mapping.expenses:
            value = _number(cell_at(index))
            if value is not None:
                other_costs = value if other_costs is None else other_costs + value

        # [REASON]: a per-row «Дрон бошқарувчи оператор» beats the sheet
        # title. It is the more specific source and it is written in full --
        # «Қодиров Нурали» rather than «Нурали» -- which removes the
        # «Шахзод» / «Шохрух» ambiguity for those rows outright.
        operator_raw = report.operator_raw
        operator_source = 'file' if report.operator_from_file_name else 'sheet'
        row_operator = _text(cell('row_operator'))
        if row_operator:
            operator_raw = row_operator
            operator_source = 'row'
            report.operator_from_row += 1
        else:
            report.operator_from_sheet += 1

        formula_no_cache = False
        if formula_cells:
            checked = [mapping.get('amount'), mapping.get('price'),
                       mapping.received_index]
            checked.extend(index for index, _h in mapping.expenses)
            for index in checked:
                if index is None:
                    continue
                if ((excel_row, index) in formula_cells
                        and cell_at(index) is None):
                    formula_no_cache = True

        rows.append({
            'source_file': file_name,
            'source_sheet': sheet_name,
            'source_row': excel_row,
            'period_month': period_month,
            'customer_raw': customer_raw,
            'area_ha': area,
            'price_per_ha': resolve_price(cell('price'), amount, area),
            'amount': amount,
            'other_costs': other_costs,
            'received_amount': _number(cell_at(mapping.received_index)),
            'received_kind': mapping.received_kind,
            'payment_type': payment,
            'date_raw': _text(date_cell) or None,
            'work_date_from': date_from,
            'work_date_to': date_to,
            'date_kind': date_kind,
            'operator_raw': operator_raw,
            'operator_source': operator_source,
            'subdivision_name': subdivision,
            'note': _text(cell('note')) or None,
            'formula_no_cache': formula_no_cache,
            # FIN-RECON-003: the dispatcher's «№» as typed, for the report
            # only. Never stored, never part of an identity -- provenance is
            # source_file / source_sheet / source_row, the physical row.
            'row_counter': counter,
            'counter_missing': counter_missing,
        })
        classify('job_no_number' if counter_missing else 'job')
        if counter_missing:
            report.counterless_rows += 1
        report.rows += 1
        report.area += area
        block_area += area
        sheet_area += area

    # A «свод ичи» sheet whose header was never recognised gave no rows at
    # all; the numbered rows it holds are named instead of vanishing.
    if not usable_header_seen:
        for excel_row, cells in numbered_above_header:
            report.row_classes['above_header'] -= 1
            report.row_classes['unparsed'] += 1
            report.unparsed.append(
                (excel_row, 'detail sheet without a recognised header', cells))

    return rows, report


# ─── DRONE-SVODKA-READER-001: листы «СВОДКА …» книги Достон АКА ─────────────

SVODKA_SHEET_PREFIX = uz_fold('СВОДКА') + ' '

# Русские имена месяцев, как их пишут в титулах «СВОДКА Сентябрь».
SVODKA_MONTHS = {uz_fold(name): number for number, name in enumerate(
    ('Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль',
     'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'), 1)}


def is_svodka_sheet(title):
    """True для «СВОДКА <месяц>». Пробел в префиксе — часть правила:
    голая «Сводка» и «Свод1» под него не подпадают и пропускаются как
    раньше."""
    return uz_fold(title).startswith(SVODKA_SHEET_PREFIX)


def svodka_sheet_period(title, file_period):
    """'СВОДКА Сентябрь' + период файла '2025-09' -> '2025-09'; None = отказ.

    [REASON]: месяц берётся из титула ЛИСТА, потому что одна книга несёт два
    месяца, а манифест — один период на файл. Год — из манифеста: в титуле
    его нет, а выдуманный год хуже отказа. Лист с неузнанным месяцем
    пропускается с названной причиной, не гадается.
    """
    tail = uz_fold(title)[len(SVODKA_SHEET_PREFIX):].strip()
    month = SVODKA_MONTHS.get(tail)
    if month is None:
        return None
    return '%s-%02d' % (file_period[:4], month)


def _svodka_operator(texts):
    """«Дрон Рухиллоев Сайфулло» из верхнего свода -> ФИО, или ''.

    [REASON]: титул листа — «СВОДКА Сентябрь», имени в нём нет, а файловая
    заглушка operator_from_sheet_title дала бы operator_raw
    «Дрон_маълумот_Достон_АКА_АГРОКЛАСТЕР». Полное имя стоит в ячейке
    «Дрон <ФИО>» верхнего свода; телефон может быть в той же ячейке или в
    соседней, поэтому на него полагаться нельзя. Правило: после «Дрон » —
    два-четыре слова, каждое с заглавной, без цифр. Ложные соседи того же
    свода им не проходят: «Дрон бошқарувчи оператор» — строчные, «ДРОН
    РАКАМИ: 64TBL…» — цифры, «Дронларнинг нақд…» — нет пробела после
    «Дрон». Запасной путь — «Оператор: С.Рухиллоев», имя после двоеточия.
    """
    fallback = ''
    for text in texts:
        folded = uz_fold(text)
        if folded.startswith('оператор:') and not fallback:
            fallback = text.split(':', 1)[1].strip()
        if not folded.startswith('дрон '):
            continue
        tail = text.split('+', 1)[0][4:].strip()
        words = tail.split()
        if (2 <= len(words) <= 4
                and not any(ch.isdigit() for ch in tail)
                and all(w[:1].isupper() for w in words)):
            return ' '.join(words)
    return fallback


def _is_svodka_transfer_header(cells):
    """Шапка блока «Справка»: «№ | Корхона номи | …».

    is_header_row её не узнаёт — там нет «ФХ номи», — и без этой проверки
    строки справки читались бы КАССОВОЙ раскладкой: «Хизмат кўрсатиш
    суммаси» (цена 75 040) ложилась в сумму, а «Жами сумма» (настоящая
    сумма) — в затраты. Ровно так лист и ломался под parse_sheet.
    """
    if _text(cells[0] if cells else None) != '№':
        return False
    return any(uz_fold(_text(c)) == uz_fold('Корхона номи')
               for c in cells[:4])


def parse_svodka_sheet(ws, file_name, sheet_name, period_month, subdivision,
                       formula_cells=None):
    """Лист «СВОДКА <месяц>». Возвращает (rows, SheetReport) как parse_sheet.

    [REASON]: DRONE-SVODKA-READER-001, решение владельца 2026-08-11: «прими
    в работу данные сентября и октября 2025 года» из книги
    «Дрон_маълумот_Достон_АКА_АГРОКЛАСТЕР.xlsx». У этой книги — единственной
    из сорока пяти просмотренных — сводный блок с «ЖАМИ СУММА:» стоит ВЫШЕ
    детальной таблицы, а блок «Справка» несёт собственную шапку. Отдельный
    читатель включается только по титулу «СВОДКА …»; 28 обычных книг идут
    прежним parse_sheet, и их числа не двигаются.

    Устройство листа:
      — верхний свод (оператор, его личные расходы, «ЖАМИ СУММА:») — не
        работы; всё до первой узнанной шапки пропускается;
      — кассовая таблица: стандартная шапка с «ФХ номи», payment=cash.
        Дата — «Кирим қилинган сана», день СДАЧИ ДЕНЕГ, не полёта; за
        неимением другой даты пишется как дата работы, это записано в
        docs/tracks/drones.md;
      — строка «Жами (нақд)» несёт счётчик и площадь в диапазоне
        («66 | Жами (нақд) | 826.8») — без явного стопа она импортировалась
        бы работой на 827 га; блок закрывает заказчик на «жами»;
      — «Справка»: шапка «№ | Корхона номи | Майдон (га) | Хизмат кўрсатиш
        санаси | Хизмат кўрсатиш суммаси | Жами сумма», payment=transfer,
        получено не ведётся — канал ждёт банковский реестр.
    """
    report = SheetReport(file_name, sheet_name)
    report.operator_raw = ''
    report.operator_from_file_name = False

    rows = []
    mapping = None
    current_payment = None

    for excel_row, raw in enumerate(ws.iter_rows(values_only=True), 1):
        cells = _row_cells(raw)
        texts = [_text(c) for c in cells]
        if not any(texts):
            continue

        if not report.operator_raw:
            operator = _svodka_operator(texts)
            if operator:
                report.operator_raw = operator

        if is_header_row(cells):
            mapping = map_columns(cells)
            if 'note' not in mapping:
                # «Изох» без звёздочки есть только в этой книге; правка
                # общего FIELD_PHRASES зацепила бы 28 обычных книг.
                for index, text in enumerate(texts):
                    if uz_fold(text) == uz_fold('Изох'):
                        mapping.fields['note'] = index
                        mapping.chosen['note'] = text
                        break
            current_payment = PAYMENT_CASH
            report.blocks.append((excel_row, PAYMENT_CASH))
            if report.column_map is None:
                report.header_row = excel_row
                report.header_cells = texts
                report.column_map = mapping
                report.missing_fields = mapping.missing_fields()
            continue

        if _is_svodka_transfer_header(cells):
            mapping = map_columns(cells)
            for index, text in enumerate(texts):
                if uz_fold(text) == uz_fold('Корхона номи'):
                    mapping.fields['customer'] = index
                    mapping.chosen['customer'] = text
                    break
            current_payment = PAYMENT_TRANSFER
            report.blocks.append((excel_row, PAYMENT_TRANSFER))
            if report.column_map is not None:
                report.column_map.unmatched.extend(mapping.unmatched)
            continue

        if mapping is None:
            # Весь верхний свод: личные расходы оператора — не работы.
            continue

        customer_index = mapping.get('customer')
        customer_raw = (_text(cells[customer_index])
                        if customer_index is not None
                        and customer_index < len(cells) else '')

        # Стоп блока: «Жами (нақд)» / «Жами:». Строка проходит оба теста
        # формы (счётчик 66, площадь 826.8 в диапазоне) — различает её
        # только заказчик.
        if customer_raw and uz_fold(customer_raw).startswith('жами'):
            mapping = None
            current_payment = None
            continue

        counter = _int_counter(cells[0] if cells else None)
        area = (_number(cells[mapping.get('area')])
                if 'area' in mapping and mapping.get('area') < len(cells)
                else None)
        if counter is None or not area_in_range(area):
            if counter is not None and area is not None:
                report.rejections.append(
                    (excel_row, 'area out of range (%.2f)' % area, cells))
            continue

        def cell(field):
            index = mapping.get(field)
            if index is None or index >= len(cells):
                return None
            return cells[index]

        def cell_at(index):
            if index is None or index >= len(cells):
                return None
            return cells[index]

        if not customer_raw:
            report.empty_customer_rows += 1

        amount = _number(cell('amount'))
        date_cell = cell('date')
        date_from, date_to, date_kind = parse_date_cell(date_cell)

        other_costs = None
        for index, _header in mapping.expenses:
            value = _number(cell_at(index))
            if value is not None:
                other_costs = (value if other_costs is None
                               else other_costs + value)

        report.operator_from_sheet += 1

        formula_no_cache = False
        if formula_cells:
            checked = [mapping.get('amount'), mapping.get('price'),
                       mapping.received_index]
            checked.extend(index for index, _h in mapping.expenses)
            for index in checked:
                if index is None:
                    continue
                if ((excel_row, index) in formula_cells
                        and cell_at(index) is None):
                    formula_no_cache = True

        rows.append({
            'source_file': file_name,
            'source_sheet': sheet_name,
            'source_row': excel_row,
            'period_month': period_month,
            'customer_raw': customer_raw,
            'area_ha': area,
            'price_per_ha': resolve_price(cell('price'), amount, area),
            'amount': amount,
            'other_costs': other_costs,
            'received_amount': _number(cell_at(mapping.received_index)),
            'received_kind': mapping.received_kind,
            'payment_type': current_payment,
            'date_raw': _text(date_cell) or None,
            'work_date_from': date_from,
            'work_date_to': date_to,
            'date_kind': date_kind,
            'operator_raw': report.operator_raw,
            'operator_source': 'sheet',
            'subdivision_name': subdivision,
            'note': _text(cell('note')) or None,
            'formula_no_cache': formula_no_cache,
        })
        report.rows += 1
        report.area += area

    return rows, report


def _formula_cells(path):
    """{(row, col0) : formula} for cells that hold a formula.

    [REASON]: the workbook is read with data_only=True, which returns the
    value Excel cached the last time it saved the file. A file produced by a
    tool that never calculated -- or edited and closed without saving --
    hands back None for every formula, and an amount column of NULLs looks
    exactly like a book where nobody filled the amounts in. The second load
    exists only to be able to SAY which it was.
    """
    from openpyxl import load_workbook
    found = {}
    wb = load_workbook(path, data_only=False, read_only=True)
    try:
        for ws in wb.worksheets:
            for row_index, row in enumerate(ws.iter_rows(values_only=True), 1):
                for col_index, value in enumerate(row):
                    if isinstance(value, str) and value.startswith('='):
                        found[(ws.title, row_index, col_index)] = value
    finally:
        wb.close()
    return found


# ─── Resolution against the database ─────────────────────────────────────────

class ImportPreconditionError(Exception):
    """A precondition the caller can report. The CLI turns it into exit 2.

    [REASON]: DRONE-WORKS-UPLOAD-001. These three checks used to print and
    then kill the process with code 2. From a shell that is exactly right.
    Inside a Waitress worker thread it raises SystemExit on the REQUEST
    thread instead: the thread dies, the client gets a bare 500, and the
    message -- which is the entire content of the failure -- goes to a
    console nobody is watching and into no log the operator reads. Raising
    lets the web layer show the same sentence on the screen; main() catches
    it, prints the same text and returns 2, so the shell behaviour does not
    move. Deliberately no process exit anywhere outside __main__.
    """


def _connect(db_path, read_only):
    if not os.path.exists(db_path):
        # [REASON]: sqlite3.connect(path) creates an empty database when the
        # file is missing. A tool that silently creates one and reports
        # "0 operators, 0 customers" is worse than one that refuses.
        raise ImportPreconditionError(
            'ERROR: database not found at %s - refusing to run.' % db_path)
    if read_only:
        uri = 'file:%s?mode=ro' % path_to_uri(db_path)
        con = sqlite3.connect(uri, uri=True)
    else:
        con = sqlite3.connect(db_path)
    con.execute('PRAGMA busy_timeout=30000')
    return con


def path_to_uri(path):
    return path.replace('?', '%3f').replace('#', '%23').replace('\\', '/')


def _require_tables(con, tables):
    have = {row[0] for row in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    missing = [t for t in tables if t not in have]
    if missing:
        raise ImportPreconditionError(
            'ERROR: table(s) missing: %s. Run migrate_drones_works_001.py '
            'first.' % ', '.join(missing))


def _require_received_kind(con):
    """Refuse to run against a database that predates DRONES_WORKS_002.

    [REASON]: without the column the INSERT raises inside the transaction on
    --apply, after the whole corpus has been parsed and reported. Checking it
    up front means --dry-run says so too, which is where somebody is actually
    looking.
    """
    columns = {row[1] for row in con.execute('PRAGMA table_info(drone_works)')}
    if 'received_kind' not in columns:
        raise ImportPreconditionError(
            'ERROR: drone_works.received_kind is missing. Run '
            'migrate_drones_works_002.py first.')


def _require_payer_unidentified(con):
    """Refuse to run against a database that predates the «Фукаро» column.

    [REASON]: same reason as _require_received_kind -- the INSERT names the
    column, and a dry run that passes followed by an --apply that raises is
    the worst place to learn it.
    """
    columns = {row[1] for row in con.execute('PRAGMA table_info(drone_works)')}
    if 'payer_unidentified' not in columns:
        raise ImportPreconditionError(
            'ERROR: drone_works.payer_unidentified is missing. Run '
            'migrate_drones_works_payer_unidentified_001.py first.')


class Directory(object):
    """Operators, subdivisions and customer aliases, read once."""

    def __init__(self, con):
        self.operators = []
        for row in con.execute('SELECT id, full_name, subdivision_name '
                               'FROM drone_operators'):
            self.operators.append({
                'id': row[0],
                'full_name': row[1],
                'subdivision_name': row[2],
                'tokens': name_tokens(row[1]),
            })
        self.subdivisions = set()
        for table in ('drone_operators', 'drone_units'):
            for row in con.execute('SELECT DISTINCT subdivision_name FROM %s '
                                   'WHERE subdivision_name IS NOT NULL '
                                   "AND subdivision_name <> ''" % table):
                self.subdivisions.add(row[0])
        self.aliases = {}
        # [REASON]: is_active IS NOT 0, never = 1. The column is
        # BOOLEAN DEFAULT 1 and a hand-inserted row can hold NULL, which
        # '= 1' silently drops -- turning a known spelling into a new
        # duplicate customer. The same bug was already caught once in
        # _drone_nickname_maps().
        for row in con.execute(
                'SELECT normalized_key, drone_customer_id '
                'FROM drone_customer_aliases '
                'WHERE is_active IS NOT 0'):
            self.aliases[row[0]] = row[1]
        self.customer_names = {}
        for row in con.execute('SELECT id, name FROM drone_customers'):
            self.customer_names[row[0]] = row[1]
        self.existing_provenance = set()
        for row in con.execute('SELECT source_file, source_sheet, source_row '
                               'FROM drone_works WHERE source_file IS NOT NULL'):
            self.existing_provenance.add((row[0], row[1], row[2]))

    def subdivision_for_file(self, file_name):
        """The subdivision named in the file name, or None. Data-driven.

        The candidate list comes from the two subdivision columns already in
        the database, so nothing is hardcoded here: a subdivision the fleet
        directory has never heard of cannot be invented by this tool.

        Two passes, DRONE-CLOSE-SUBDIV-001. First the whole subdivision
        name as before, longest match wins. Only when no whole name occurs
        does the second pass run: single words that discriminate exactly one
        subdivision, derived from the same directory set. «Гарден Дрон
        маълумот Апрель.xlsx» carries no whole fleet name, but «Гарден»
        occurs in exactly one of them.
        """
        folded = uz_fold(file_name)
        best = None
        for name in self.subdivisions:
            key = uz_fold(name)
            if key and key in folded:
                if best is None or len(key) > len(uz_fold(best)):
                    best = name
        if best is not None:
            return best
        return self._subdivision_by_token(folded)

    @staticmethod
    def _split_words(folded_value):
        """Words of an uz_fold()-ed string.

        [REASON]: the dispatchers' file names join words with underscores
        («Ғиждувон_ПТЗ_Дрон_маълумот_Октябрь.xlsx»), and `_` is a \\w
        character, so a plain \\W split would keep «ғиждувон_птз» glued into
        one token that matches nothing. Split on non-word AND underscore.
        Whole-word comparison, never substring: the charter's own example of
        the substring lie is `L_initial` matching `L_initial_missing`.
        """
        return [w for w in re.split(r'[\W_]+', folded_value) if w]

    @staticmethod
    def discriminating_tokens(subdivisions):
        """Map word -> the single subdivision that word identifies.

        Derived from the fleet directory, never typed by hand: split every
        subdivision name into words, drop every word occurring in more than
        one name. On the current fleet of seven that keeps «Сервис»,
        «Гарден», «Когон», «Пешку», «Шофиркон», «Ғиждувон» — and nothing at
        all for «Бухоро Агрокластер», because both its words also occur in
        two other names. A book called «Агрокластер Дрон маълумот МАЙ.xlsx»
        therefore stays unresolved, which is the right answer: «Агрокластер»
        genuinely does not identify anybody. If the fleet gains a subdivision
        tomorrow, the tokens change with it and no code moves.
        """
        owners = {}
        for name in subdivisions:
            for word in set(Directory._split_words(uz_fold(name))):
                owners.setdefault(word, set()).add(name)
        return {word: next(iter(names))
                for word, names in owners.items() if len(names) == 1}

    def _subdivision_by_token(self, folded_file_name):
        """Second pass of subdivision_for_file: discriminating words only.

        Tokens of exactly one subdivision present -> that subdivision.
        Zero, or tokens of two different subdivisions -> None. It never
        guesses and never falls through to a weaker rule.
        """
        tokens = self.discriminating_tokens(self.subdivisions)
        words = set(self._split_words(folded_file_name))
        hits = {tokens[w] for w in words if w in tokens}
        if len(hits) == 1:
            return next(iter(hits))
        return None

    def resolve_operator(self, operator_raw, subdivision):
        """(operator id or None, reason). Never guesses between two people."""
        tokens = name_tokens(operator_raw)
        if not tokens:
            return None, 'empty operator'
        candidates = [op for op in self.operators
                      if op['tokens'] and tokens <= op['tokens']]
        if len(candidates) == 1:
            return candidates[0]['id'], 'matched'
        if not candidates:
            return None, 'no operator matches'
        if subdivision:
            key = uz_fold(subdivision)
            narrowed = [op for op in candidates
                        if uz_fold(op['subdivision_name'] or '') == key]
            if len(narrowed) == 1:
                return narrowed[0]['id'], 'matched by subdivision'
        # [REASON]: «Шахзод» is Kholmurodov Shakhzod in the Ghijduvon files
        # and Boltaev Shakhzod in the Servis files; «Шохрух» is Khamroev in
        # Kogon and Fayzullaev in Garden. When the subdivision cannot separate
        # them the row keeps drone_operator_id NULL and is listed. Never guess.
        return None, ('ambiguous between %d operators'
                      % len(candidates))


# ─── The run ─────────────────────────────────────────────────────────────────

class RunResult(object):
    def __init__(self):
        self.files_seen = []
        self.files_skipped_no_manifest = []
        self.sheets = []
        self.rows = []
        self.duplicates = []
        self.suspected_duplicates = []  # candidates retained, not dropped
        self.quarantined_rows = []  # old wage carryovers for manual review
        self.filename_alias_conflicts = []  # unsafe provenance aliases
        self.rejections = []
        self.manifest_problems = []
        self.created_customers = []
        self.matched_customers = 0
        self.unresolved_operators = {}
        self.matched_operators = 0
        self.already_in_db = 0
        self.month_outliers = {}
        self.formula_no_cache = 0
        self.wage_rows = 0
        # DRONE-WORKS-IMPORT-FIX-001
        self.skipped_sheets = []          # [(file, sheet title)]
        self.unmatched_headers = {}       # normalised header -> [(file, sheet)]
        self.unknown_payment_rows = 0
        self.empty_customer_rows = 0
        self.operator_from_row = 0
        self.operator_from_sheet = 0
        self.received_kinds = {}          # kind -> row count
        # FIN-RECON-002: rows whose payer is a citizen named generically
        self.payer_unidentified_rows = 0
        # FIN-RECON-003
        self.unparsed = []          # [(file, sheet, row, reason, preview)]
        self.row_classes = dict.fromkeys(ROW_CLASSES, 0)
        self.bridge_sheets = 0      # «свод ичи» sheets the bridge covers
        self.svodka_sheets = 0      # «СВОДКА …» sheets: own reader, no bridge
        self.svodka_rows = 0        # rows those sheets gave, before dedup
        self.total_checks = []      # [(file, sheet) + SheetReport.total_checks]
        self.schema_warnings = []   # dry run only; --apply refuses instead
        self.payer_decision_pending_rows = 0
        self.payer_word_new_cards = []   # [(key, spelling)] -- report only


def collect(directory_path, manifest, dirs_files, dir_obj, default_payment):
    """Parse every manifest-listed file in the directory. Pure reading."""
    from openpyxl import load_workbook

    result = RunResult()
    seen_keys = {}

    for file_name in dirs_files:
        if file_name not in manifest:
            result.files_skipped_no_manifest.append(file_name)
            continue
        period_month = manifest[file_name]
        full = os.path.join(directory_path, file_name)
        subdivision = dir_obj.subdivision_for_file(file_name)
        formulas = _formula_cells(full)
        wb = load_workbook(full, data_only=True, read_only=True)
        try:
            result.files_seen.append((file_name, period_month, subdivision,
                                      len(wb.worksheets)))
            for ws in wb.worksheets:
                # [REASON]: gate everything else. «олинмаган пуллар» and
                # «свод фх» repeat rows that already live in «свод ичи», and
                # «ОБШИЙ СВОД» is a summary whose «ЖАМИ:» lines are not jobs.
                # DRONE-SVODKA-READER-001: листы «СВОДКА <месяц>» книги
                # Достон АКА — вторая узнаваемая форма; месяц берётся из
                # титула листа, год — из периода файла.
                svodka_period = None
                if not is_detail_sheet(ws.title):
                    if is_svodka_sheet(ws.title):
                        svodka_period = svodka_sheet_period(ws.title,
                                                            period_month)
                    if svodka_period is None:
                        skipped = SheetReport(file_name, ws.title)
                        if is_svodka_sheet(ws.title):
                            skipped.skipped_reason = (
                                'svodka sheet with unrecognised month in '
                                'the title')
                        else:
                            skipped.skipped_reason = (
                                'not a detail sheet (title does not start '
                                'with "свод ичи")')
                        result.sheets.append(skipped)
                        result.skipped_sheets.append((file_name, ws.title))
                        continue
                sheet_formulas = {(r, c) for (title, r, c) in formulas
                                  if title == ws.title}
                if svodka_period is not None:
                    rows, report = parse_svodka_sheet(
                        ws, file_name, ws.title, svodka_period, subdivision,
                        formula_cells=sheet_formulas)
                else:
                    rows, report = parse_sheet(
                        ws, file_name, ws.title, period_month, subdivision,
                        default_payment=default_payment,
                        formula_cells=sheet_formulas)
                result.sheets.append(report)
                if svodka_period is not None:
                    result.svodka_sheets += 1
                    result.svodka_rows += len(rows)
                else:
                    result.bridge_sheets += 1
                    for name, count in report.row_classes.items():
                        result.row_classes[name] += count
                    for excel_row, reason, cells in report.unparsed:
                        result.unparsed.append(
                            (file_name, ws.title, excel_row, reason,
                             ' | '.join(_text(c) for c in cells)[:200]))
                    for check in report.total_checks:
                        result.total_checks.append((file_name, ws.title)
                                                   + tuple(check))
                result.wage_rows += report.wage_rows
                result.unknown_payment_rows += report.unknown_payment_rows
                result.empty_customer_rows += report.empty_customer_rows
                result.operator_from_row += report.operator_from_row
                result.operator_from_sheet += report.operator_from_sheet
                if report.column_map is not None:
                    for _index, header in report.column_map.unmatched:
                        result.unmatched_headers.setdefault(
                            header, []).append((file_name, ws.title))
                for excel_row, reason, cells in report.rejections:
                    result.rejections.append(
                        (file_name, ws.title, excel_row, reason,
                         ' | '.join(_text(c) for c in cells)[:200]))
                for row in rows:
                    if is_crossperiod_wage_carryover(row):
                        result.quarantined_rows.append(row)
                        continue
                    if row['formula_no_cache']:
                        result.formula_no_cache += 1
                    key = duplicate_key(row)
                    group = seen_keys.setdefault(key, []) if key is not None else []
                    first = next((candidate for candidate in group
                                  if proven_duplicate_work(candidate, row)),
                                 None)
                    if first is not None:
                        result.duplicates.append((first, row))
                        continue
                    if group:
                        result.suspected_duplicates.append((group[0], row))
                    group.append(row)
                    result.rows.append(row)
                    if row['received_kind']:
                        result.received_kinds[row['received_kind']] = (
                            result.received_kinds.get(row['received_kind'], 0)
                            + 1)
                    if (row['work_date_from'] is not None
                            and row['work_date_from'].strftime('%Y-%m')
                            != period_month):
                        result.month_outliers.setdefault(file_name, set()).add(
                            row['work_date_from'].strftime('%Y-%m'))
        finally:
            wb.close()
    return result


def resolve(result, dir_obj):
    """Fill drone_operator_id / drone_customer_id. Mutates result.rows."""
    pending_customers = {}
    for row in result.rows:
        operator_id, reason = dir_obj.resolve_operator(row['operator_raw'],
                                                       row['subdivision_name'])
        row['drone_operator_id'] = operator_id
        if operator_id is None:
            key = (row['operator_raw'], reason)
            result.unresolved_operators[key] = (
                result.unresolved_operators.get(key, 0) + 1)
        else:
            result.matched_operators += 1

        # [REASON]: an EMPTY customer creates NOTHING. A drone_customers row
        # named '' would be a farm called nothing, it would collect every
        # nameless internal job under one meaningless heading, and its alias
        # would then swallow every future nameless row. The work row keeps
        # customer_raw = '' and drone_customer_id NULL, and the reports say
        # «Заказчик не указан» -- which is the fact.
        row['payer_unidentified'] = False
        row['payer_decision_pending'] = False
        if not row['customer_raw']:
            row['drone_customer_id'] = None
            row['customer_is_new'] = False
            continue

        key = customer_key(row['customer_raw'])
        # [REASON]: BEFORE the alias lookup. «фукаро» has an alias to card
        # #589 in the production directory; consulting it first is exactly
        # how eleven different citizens would become one debtor. See
        # GENERIC_PAYER_KEYS.
        if key in GENERIC_PAYER_KEYS:
            row['drone_customer_id'] = None
            row['customer_is_new'] = False
            row['payer_unidentified'] = True
            result.payer_unidentified_rows += 1
            continue
        customer_id = dir_obj.aliases.get(key)
        if customer_id is not None:
            row['drone_customer_id'] = customer_id
            row['customer_is_new'] = False
            result.matched_customers += 1
        else:
            row['drone_customer_id'] = None
            row['customer_is_new'] = True
            # [REASON]: AFTER the alias lookup, unlike the generic list: an
            # alias the owner gave this spelling through the decision table is
            # a decision and is followed. Only the card the importer would
            # make up on its own is held. The key stays in created_customers
            # so tools/drone_import_alias_candidates.py keeps reconciling;
            # apply_rows() is what refuses. See PAYER_DECISION_PENDING_KEYS.
            if is_payer_decision_pending(key):
                row['payer_decision_pending'] = True
                result.payer_decision_pending_rows += 1
            if key not in pending_customers:
                pending_customers[key] = row['customer_raw']
    result.created_customers = sorted(pending_customers.items(),
                                      key=lambda kv: kv[1])
    # Cards that WILL be made and carry the word -- the held ones are listed
    # under PAYER_DECISION_REQUIRED instead, not twice.
    result.payer_word_new_cards = [
        (key, name) for key, name in result.created_customers
        if has_generic_payer_word(name)
        and not is_payer_decision_pending(key)]

    result.already_in_db = sum(
        1 for row in result.rows
        if (row['source_file'], row['source_sheet'], row['source_row'])
        in dir_obj.existing_provenance)
    return result


# ─── Writing ─────────────────────────────────────────────────────────────────

def apply_blockers(result):
    """Why this parsed run must not be written, as ASCII lines; [] = none.

    [REASON]: FIN-RECON-003, fail-closed. A row that carries job data and
    matches no rule is unknown data, not a skipped line, and a spelling on
    PAYER_DECISION_PENDING_KEYS waits for the owner; writing around either
    would leave a ledger that looks complete and is not. Checked inside
    apply_rows(), so the CLI and the upload screen refuse alike. Rows refused
    by a NAMED rule (area out of range, «Иш хаки») do not block: they are
    decided and listed, as before.
    """
    blockers = []
    unparsed = getattr(result, 'unparsed', None) or []
    if unparsed:
        blockers.append('UNPARSED_CANDIDATE: %d row(s) carry job data that '
                        'no rule accepts' % len(unparsed))
    pending = [key for key, _name in result.created_customers
               if is_payer_decision_pending(key)]
    if pending:
        blockers.append('PAYER_DECISION_REQUIRED: %d spelling(s) of '
                        'PAYER_DECISION_PENDING_KEYS would become a new card'
                        % len(pending))
    return blockers


def apply_rows(con, result, batch):
    """Insert customers, aliases and works inside ONE transaction."""
    blockers = apply_blockers(result)
    if blockers:
        raise ImportPreconditionError(
            'ERROR APPLY_REFUSED: %s. Nothing written.' % '; '.join(blockers))
    now = datetime.datetime.utcnow().isoformat()
    cur = con.cursor()
    cur.execute('BEGIN')
    created = 0
    for key, raw_name in result.created_customers:
        cur.execute('SELECT drone_customer_id FROM drone_customer_aliases '
                    'WHERE normalized_key = ?', (key,))
        found = cur.fetchone()
        if found:
            customer_id = found[0]
        else:
            cur.execute(
                'INSERT INTO drone_customers (name, customer_type, is_active, '
                'note, created_at, updated_at) VALUES (?, ?, 1, ?, ?, ?)',
                (raw_name, 'external',
                 'import %s' % batch, now, now))
            customer_id = cur.lastrowid
            cur.execute(
                'INSERT INTO drone_customer_aliases (raw_name, '
                'normalized_key, drone_customer_id, is_active, created_at) '
                'VALUES (?, ?, ?, 1, ?)',
                (raw_name, key, customer_id, now))
            created += 1
        for row in result.rows:
            # An empty customer_raw normalises to '' and must never be
            # attached to a customer created for some other spelling.
            if (row['customer_raw'] and not row.get('payer_unidentified')
                    and customer_key(row['customer_raw']) == key):
                row['drone_customer_id'] = customer_id

    inserted = 0
    skipped = 0
    for row in result.rows:
        cur.execute('SELECT id FROM drone_works WHERE source_file = ? AND '
                    'source_sheet = ? AND source_row = ?',
                    (row['source_file'], row['source_sheet'],
                     row['source_row']))
        if cur.fetchone():
            skipped += 1
            continue
        cur.execute(
            'INSERT INTO drone_works (period_month, work_date_from, '
            'work_date_to, date_raw, drone_operator_id, operator_raw, '
            'drone_customer_id, customer_raw, area_ha, price_per_ha, amount, '
            'other_costs, received_amount, received_kind, payment_type, '
            'subdivision_name, source_file, source_sheet, source_row, '
            'import_batch, note, created_at, payer_unidentified) VALUES (?, '
            '?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '
            '?)',
            (row['period_month'],
             row['work_date_from'].isoformat() if row['work_date_from'] else None,
             row['work_date_to'].isoformat() if row['work_date_to'] else None,
             row['date_raw'], row['drone_operator_id'], row['operator_raw'],
             row['drone_customer_id'], row['customer_raw'], row['area_ha'],
             row['price_per_ha'], row['amount'], row['other_costs'],
             row['received_amount'], row['received_kind'],
             row['payment_type'],
             row['subdivision_name'], row['source_file'], row['source_sheet'],
             row['source_row'], batch, row['note'], now,
             # NULL, not 0, on an ordinary row: the same value every row
             # imported before the column existed carries.
             1 if row.get('payer_unidentified') else None))
        inserted += 1
    con.commit()
    return created, inserted, skipped


# ─── Reporting ───────────────────────────────────────────────────────────────

def _ascii(text):
    return str(text).encode('ascii', 'backslashreplace').decode('ascii')


def summarize(result):
    """Numbers only -- safe for an ASCII console."""
    rows = result.rows
    kinds = {DATE_KIND_EXACT: 0, DATE_KIND_SPAN: 0, DATE_KIND_NONE: 0,
             DATE_KIND_UNPARSED: 0}
    kind_area = dict.fromkeys(kinds, 0.0)
    payments = dict.fromkeys(PAYMENT_TYPES, 0)
    payment_area = dict.fromkeys(PAYMENT_TYPES, 0.0)
    months = {}
    manifest_months = {}
    customers = set()
    area = 0.0
    amount = 0.0
    received = 0.0
    other = 0.0
    priced = 0
    counterless = 0
    counterless_area = 0.0
    for row in rows:
        if row.get('counter_missing'):
            counterless += 1
            counterless_area += row['area_ha']
        kinds[row['date_kind']] += 1
        kind_area[row['date_kind']] += row['area_ha']
        payments[row['payment_type']] += 1
        payment_area[row['payment_type']] += row['area_ha']
        month = (row['work_date_from'].strftime('%Y-%m')
                 if row['work_date_from'] else row['period_month'])
        bucket = months.setdefault(month, [0, 0.0])
        bucket[0] += 1
        bucket[1] += row['area_ha']
        # The same rows keyed by the MANIFEST month, which is what the
        # prediction in EXPECTED_BY_MONTH is grouped by. Both are printed:
        # comparing a date-grouped actual against a manifest-grouped
        # expectation is how a straddling book looks like a defect.
        bucket = manifest_months.setdefault(row['period_month'], [0, 0.0])
        bucket[0] += 1
        bucket[1] += row['area_ha']
        if row['customer_raw']:
            customers.add(customer_key(row['customer_raw']))
        area += row['area_ha']
        amount += row['amount'] or 0.0
        received += row['received_amount'] or 0.0
        other += row['other_costs'] or 0.0
        if row['price_per_ha'] is not None:
            priced += 1
    return {
        'files': len(result.files_seen),
        'sheets': len(result.sheets),
        'rows': len(rows),
        'area': area,
        'amount': amount,
        'received': received,
        'other_costs': other,
        'customers': len(customers),
        'priced': priced,
        'kinds': kinds,
        'kind_area': kind_area,
        'payments': payments,
        'payment_area': payment_area,
        'months': months,
        'manifest_months': manifest_months,
        'counterless': counterless,
        'counterless_area': counterless_area,
    }


def apply_gate(result):
    """Everything that would stop --apply of this run, ASCII. [] = none.

    The dry run prints it as APPLY_PERMITTED, separately from what was read:
    a database without a column is a reason not to WRITE, never a reason to
    read the books differently (FIN-RECON-003, the 30.10.2026 snapshot).
    """
    gate = ['SCHEMA: %s' % _ascii(w) for w in result.schema_warnings]
    gate.extend(apply_blockers(result))
    if result.filename_alias_conflicts:
        gate.append('UNSAFE_FILENAME_PROVENANCE: %d'
                    % len(result.filename_alias_conflicts))
    return gate


def row_bridge(result):
    """The numeric bridge of the detail sheets, as a dict of counts.

    parsed  = job + job_no_number            (rows parse_sheet accepted)
    rows    = parsed + svodka_rows - duplicates - quarantined
    balanced is the identity checked, not assumed.
    """
    classes = result.row_classes
    parsed = classes['job'] + classes['job_no_number']
    candidates = sum(classes[name] for name in CANDIDATE_CLASSES)
    expected_rows = (parsed + result.svodka_rows - len(result.duplicates)
                     - len(result.quarantined_rows))
    return {
        'candidates': candidates,
        'parsed': parsed,
        'parsed_no_number': classes['job_no_number'],
        'svodka_rows': result.svodka_rows,
        'duplicates': len(result.duplicates),
        'quarantined': len(result.quarantined_rows),
        'rows': len(result.rows),
        'balanced': expected_rows == len(result.rows),
    }


def _csv_value(value):
    if value is None:
        return ''
    if isinstance(value, bool):
        return '1' if value else '0'
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    return str(value)


ROWS_CSV_COLUMNS = (
    'source_key', 'source_file', 'source_sheet', 'source_row', 'status',
    'reason', 'row_counter', 'counter_missing', 'payment_type',
    'period_month', 'date_raw', 'work_date_from', 'work_date_to',
    'date_kind', 'operator_raw', 'operator_source', 'drone_operator_id',
    'customer_raw', 'customer_class', 'drone_customer_id', 'area_ha',
    'price_per_ha', 'amount', 'other_costs', 'received_amount',
    'received_kind', 'formula_no_cache')


def customer_class(row):
    """How resolve() left the payer of an accepted row. Report only."""
    if not row.get('customer_raw'):
        return 'EMPTY'
    if row.get('payer_unidentified'):
        return 'GENERIC_PAYER'
    if row.get('payer_decision_pending'):
        return 'PAYER_DECISION_REQUIRED'
    if row.get('customer_is_new'):
        return 'NEW'
    if row.get('drone_customer_id') is not None:
        return 'MATCHED'
    return ''


def write_rows_csv(path, result):
    """One line per physical row that carries job data, in source order.

    [REASON]: FIN-RECON-003. The reconciliation is done by key
    («файл!лист!строка») against a registry built by someone else; a report
    of totals cannot show WHICH row is missing. Accepted rows carry the parsed
    fields; a duplicate or a quarantined row carries them too, with the
    reason; a rejected or UNPARSED_CANDIDATE row carries the key and the
    reason only. No note column and no raw preview: the books' notes hold
    names and telephone numbers, and this file is meant to travel.
    UTF-8 with BOM so Excel opens the Cyrillic as text.
    """
    lines = []

    def emit(file_name, sheet, excel_row, status, reason, row=None):
        record = dict.fromkeys(ROWS_CSV_COLUMNS, '')
        record.update({
            'source_key': '%s!%s!%d' % (file_name, sheet, excel_row),
            'source_file': file_name, 'source_sheet': sheet,
            'source_row': str(excel_row), 'status': status,
            'reason': reason})
        if row is not None:
            for column in ROWS_CSV_COLUMNS[6:]:
                if column == 'customer_class':
                    continue
                record[column] = _csv_value(row.get(column))
            if status == 'ACCEPTED':
                record['customer_class'] = customer_class(row)
        lines.append(((file_name, sheet, excel_row), record))

    for row in result.rows:
        emit(row['source_file'], row['source_sheet'], row['source_row'],
             'ACCEPTED', 'job without a row number' if
             row.get('counter_missing') else '', row)
    for first, second in result.duplicates:
        emit(second['source_file'], second['source_sheet'],
             second['source_row'], 'DUPLICATE_SKIPPED',
             'same work as %s!%s!%d' % (first['source_file'],
                                         first['source_sheet'],
                                         first['source_row']), second)
    for row in result.quarantined_rows:
        emit(row['source_file'], row['source_sheet'], row['source_row'],
             'WAGE_CARRYOVER_QUARANTINED', 'cross-period wage carryover', row)
    for file_name, sheet, excel_row, reason, _preview in result.rejections:
        emit(file_name, sheet, excel_row, 'REJECTED', reason)
    for file_name, sheet, excel_row, reason, _preview in result.unparsed:
        emit(file_name, sheet, excel_row, 'UNPARSED_CANDIDATE', reason)

    lines.sort(key=lambda item: item[0])
    with open(path, 'w', encoding='utf-8-sig', newline='') as fh:
        writer = csv.writer(fh, lineterminator='\n')
        writer.writerow(ROWS_CSV_COLUMNS)
        for _key, record in lines:
            writer.writerow([record[column] for column in ROWS_CSV_COLUMNS])
    return len(lines)


def print_console(result, stats, args, batch):
    print('=' * 72)
    print('DRONE-WORKS-001 import  --  %s'
          % ('APPLY' if args.apply else 'DRY RUN (nothing written)'))
    print('=' * 72)
    print('directory   : %s' % _ascii(args.dir))
    print('manifest    : %s' % _ascii(args.manifest))
    print('database    : %s' % _ascii(args.db))
    print('batch       : %s' % _ascii(batch))
    print('report file : %s' % _ascii(args.report))
    print('')
    print('files parsed             : %d' % stats['files'])
    print('files skipped (no entry) : %d' % len(result.files_skipped_no_manifest))
    print('sheets                   : %d' % stats['sheets'])
    print('rows accepted            : %d' % stats['rows'])
    print('hectares                 : %.2f' % stats['area'])
    print('amount total             : %.2f' % stats['amount'])
    print('received total           : %.2f' % stats['received'])
    print('other costs total        : %.2f' % stats['other_costs'])
    print('distinct customers       : %d' % stats['customers'])
    print('rows with a price cell   : %d' % stats['priced'])
    print('')
    print('by payment type:')
    for payment in PAYMENT_TYPES:
        print('  %-9s %5d rows  %10.2f ha'
              % (payment, stats['payments'][payment],
                 stats['payment_area'][payment]))
    print('')
    print('by date kind:')
    for kind in (DATE_KIND_EXACT, DATE_KIND_SPAN, DATE_KIND_NONE,
                 DATE_KIND_UNPARSED):
        print('  %-9s %5d rows  %10.2f ha'
              % (kind, stats['kinds'][kind], stats['kind_area'][kind]))
    print('')
    print('by month (real date when present, manifest month otherwise):')
    for month in sorted(stats['months']):
        count, month_area = stats['months'][month]
        print('  %-8s %5d rows  %10.2f ha' % (month, count, month_area))
    print('')
    print('by MANIFEST month, against the prediction of 2026-08-04 for the')
    print('28 REAL books -- a run over any other corpus differs wholesale:')
    print('  %-8s %6s %11s | %6s %11s | %s'
          % ('month', 'rows', 'ha', 'exp.', 'exp. ha', 'delta ha'))
    expected = dict((m, (r, a)) for m, r, a in EXPECTED_BY_MONTH)
    for month in sorted(set(stats['manifest_months']) | set(expected)):
        count, month_area = stats['manifest_months'].get(month, (0, 0.0))
        exp_rows, exp_area = expected.get(month, (0, 0.0))
        delta = month_area - exp_area
        print('  %-8s %6d %11.2f | %6d %11.2f | %+10.2f'
              % (month, count, month_area, exp_rows, exp_area, delta))
    total_delta = stats['area'] - EXPECTED_TOTAL_AREA
    total_pct = (total_delta * 100.0 / EXPECTED_TOTAL_AREA
                 if EXPECTED_TOTAL_AREA else 0.0)
    print('  %-8s %6d %11.2f | %6d %11.2f | %+10.2f (%+.2f%%)'
          % ('TOTAL', stats['rows'], stats['area'], EXPECTED_TOTAL_ROWS,
             EXPECTED_TOTAL_AREA, total_delta, total_pct))
    print('  The monthly rows are INDICATIVE: this tool groups a dated row by')
    print('  its own date and one book straddles September and October. The')
    print('  total is the firm number. %s is the anchor -- it agrees with our'
          % EXPECTED_ANCHOR_MONTH)
    print('  own DJI flight data to 0.15 %. A disagreement is a finding to')
    print('  report, not a number to force.')
    print('')
    print('customers matched to an alias : %d' % result.matched_customers)
    print('customers to be created       : %d' % len(result.created_customers))
    print('payer a citizen, unidentified : %d rows (no card)'
          % result.payer_unidentified_rows)
    print('operator resolved rows        : %d' % result.matched_operators)
    print('operator UNRESOLVED rows      : %d'
          % sum(result.unresolved_operators.values()))
    print('duplicate rows skipped        : %d' % len(result.duplicates))
    print('same-number pairs retained    : %d' % len(result.suspected_duplicates))
    print('wage carryovers quarantined   : %d' % len(result.quarantined_rows))
    print('unsafe filename aliases       : %d' % len(result.filename_alias_conflicts))
    print('rows already in the database  : %d' % result.already_in_db)
    print('rows rejected                 : %d' % len(result.rejections))
    print('wage rows ignored (Ish haki)  : %d' % result.wage_rows)
    print('formula cells with no cache   : %d' % result.formula_no_cache)
    print('')
    # FIN-RECON-003: the bridge and the gate. Counts only, ASCII.
    bridge = row_bridge(result)
    print('row bridge, %d detail sheets (%d svodka sheets read separately,'
          ' not bridged):' % (result.bridge_sheets, result.svodka_sheets))
    for name in ROW_CLASSES:
        print('  %-15s : %d' % (name, result.row_classes[name]))
    print('  candidate rows with job data   : %d' % bridge['candidates'])
    print('  parsed as jobs                 : %d (without "No": %d, %.2f ha)'
          % (bridge['parsed'], bridge['parsed_no_number'],
             stats.get('counterless_area', 0.0)))
    print('  + svodka rows                  : %d' % bridge['svodka_rows'])
    print('  - duplicates skipped           : %d' % bridge['duplicates'])
    print('  - wage carryovers quarantined  : %d' % bridge['quarantined'])
    print('  = rows accepted                : %d  balanced: %s'
          % (bridge['rows'], 'YES' if bridge['balanced'] else 'NO'))
    print('UNPARSED_CANDIDATE rows          : %d' % len(result.unparsed))
    mismatched = [c for c in result.total_checks if c[-1] == 'MISMATCH']
    print('book totals checked              : %d (MISMATCH %d)'
          % (len(result.total_checks), len(mismatched)))
    print('payer decision pending (rows)    : %d'
          % result.payer_decision_pending_rows)
    print('new cards whose name has "fukaro": %d'
          % len(result.payer_word_new_cards))
    for warning in result.schema_warnings:
        print('SCHEMA_WARNING: %s' % _ascii(warning))
    gate = apply_gate(result)
    print('APPLY_PERMITTED: %s' % ('YES' if not gate else 'NO'))
    for line in gate:
        print('  %s' % _ascii(line))
    print('')
    print('sheets skipped (not "svod ichi") : %d' % len(result.skipped_sheets))
    print('headers matching no known phrase : %d distinct'
          % len(result.unmatched_headers))
    print('rows with no payment block above : %d (imported as cash)'
          % result.unknown_payment_rows)
    print('rows with an empty customer      : %d' % result.empty_customer_rows)
    print('operator taken from the row cell : %d' % result.operator_from_row)
    print('operator taken from sheet/file   : %d' % result.operator_from_sheet)
    for kind in (RECEIVED_KIND_RECEIVED, RECEIVED_KIND_OPERATOR_DUE):
        print('received figure of kind %-13s: %d'
              % (kind, result.received_kinds.get(kind, 0)))
    if result.month_outliers:
        print('')
        print('files whose dated rows fall outside the manifest month: %d'
              % len(result.month_outliers))
        print('  (their dated rows keep their own dates; only undated rows')
        print('   inherit the manifest month -- see the report file)')
    print('')
    print('Cyrillic detail (farms, operators, sheets, every skipped and')
    print('rejected row) is in the UTF-8 report file above.')


def _write_fin_recon_003_sections(add, result, stats):
    """Bridge, UNPARSED_CANDIDATE, book totals, payer hold, apply gate."""
    bridge = row_bridge(result)
    add('')
    add('МОСТ СТРОК ДЕТАЛЬНЫХ ЛИСТОВ «свод ичи»: листов %d' % result.bridge_sheets)
    add('  Каждая непустая строка листа попадает ровно в один класс.')
    names = {
        'job': 'работа с «№»', 'job_no_number': 'работа без «№»',
        'wage': 'строка блока «Иш хаки»',
        'rejected': 'отклонена по названному правилу',
        'unparsed': 'UNPARSED_CANDIDATE — данные работы без правила',
        'totals': 'строка итога «Жами …» / «ИТОГО»', 'header': 'шапка',
        'block_marker': 'метка блока (Справка, Нақд, Иш хаки)',
        'empty_numbered': 'только номер, без данных',
        'no_job_data': 'нет данных в колонках работы',
        'above_header': 'выше шапки'}
    for name in ROW_CLASSES:
        add('  %-16s %5d  %s' % (name, result.row_classes[name], names[name]))
    add('  кандидатов с данными работы: %d' % bridge['candidates'])
    add('  разобрано как работы: %d (из них без «№»: %d, %.2f га)'
        % (bridge['parsed'], bridge['parsed_no_number'],
           stats.get('counterless_area', 0.0)))
    add('  + строк листов «СВОДКА»: %d (свой читатель, в мост не входят)'
        % bridge['svodka_rows'])
    add('  − дублей: %d; − карантин переносов зарплаты: %d'
        % (bridge['duplicates'], bridge['quarantined']))
    add('  = принято строк: %d; мост сходится: %s'
        % (bridge['rows'], 'ДА' if bridge['balanced'] else 'НЕТ'))
    add('  По листам (работа / без «№» / зарплата / отклонено / UNPARSED):')
    for sheet in result.sheets:
        if sheet.skipped_reason or not any(sheet.row_classes.values()):
            continue
        c = sheet.row_classes
        add('    %s / %s: %d / %d / %d / %d / %d'
            % (sheet.file_name, sheet.sheet_name, c['job'], c['job_no_number'],
               c['wage'], c['rejected'], c['unparsed']))

    counterless = [r for r in result.rows if r.get('counter_missing')]
    add('')
    add('РАБОТЫ, ПРИНЯТЫЕ БЕЗ НОМЕРА «№»: %d строк, %.2f га'
        % (len(counterless), sum(r['area_ha'] for r in counterless)))
    add('  Номер — набор диспетчера, не признак работы; в идентичность не')
    add('  входит. Принято только по доказательству: хозяйство, площадь и')
    add('  дата, цена или сумма; строка итога работой не бывает.')
    for row in counterless:
        add('  - %s / %s / строка %d: «%s» %.2f га, %s, сумма %s'
            % (row['source_file'], row['source_sheet'], row['source_row'],
               row['customer_raw'], row['area_ha'],
               row['work_date_from'] or row['date_raw'] or 'без даты',
               '%.2f' % row['amount'] if row['amount'] is not None
               else '—'))

    add('')
    add('UNPARSED_CANDIDATE — СТРОКИ С ДАННЫМИ РАБОТЫ БЕЗ ПРАВИЛА: %d'
        % len(result.unparsed))
    add('  Запись (--apply и загрузка через экран) ОТКАЗАНА, пока список')
    add('  не пуст: строку нужно прочитать и решить, а не пропустить.')
    for file_name, sheet_name, excel_row, reason, preview in result.unparsed:
        add('  - %s / %s / строка %d: %s'
            % (file_name, sheet_name, excel_row, reason))
        add('      %s' % preview)

    add('')
    mismatched = [c for c in result.total_checks if c[-1] == 'MISMATCH']
    add('ИТОГИ КНИГИ «Жами …» ПРОТИВ ПРОЧИТАННОГО: проверено %d, '
        'расхождений %d' % (len(result.total_checks), len(mismatched)))
    add('  block — сходится с работами с последней шапки, метки или итога;')
    add('  sheet — с работами с начала листа. Допуск %.3f га.'
        % TOTAL_TOLERANCE_HA)
    for (file_name, sheet_name, excel_row, label, book_area, block_area,
         sheet_area, verdict) in result.total_checks:
        add('  - %s / %s / строка %d «%s»: книга %.2f, блок %.2f, лист %.2f'
            ' — %s' % (file_name, sheet_name, excel_row, label, book_area,
                       block_area, sheet_area, verdict))

    add('')
    add('ПЛАТЕЛЬЩИК ЖДЁТ РЕШЕНИЯ ВЛАДЕЛЬЦА (PAYER_DECISION_REQUIRED): %d строк'
        % result.payer_decision_pending_rows)
    add('  Написание из PAYER_DECISION_PENDING_KEYS без алиаса: карточка НЕ')
    add('  заводится, запись отказана. Решение — алиас в таблице решений или')
    add('  перенос ключа в GENERIC_PAYER_KEYS изменением кода.')
    for key, name in result.created_customers:
        if is_payer_decision_pending(key):
            add('  - «%s»' % name)
    add('НОВЫЕ КАРТОЧКИ, В ИМЕНИ КОТОРЫХ ЕСТЬ СЛОВО «фукаро»: %d'
        % len(result.payer_word_new_cards))
    add('  Только для глаз: «Ахмад (фукаро)» — названный гражданин, решение')
    add('  владельца — своя карточка. Ничего не блокирует.')
    for _key, name in result.payer_word_new_cards:
        add('  - «%s»' % name)

    add('')
    gate = apply_gate(result)
    add('ЗАПИСЬ РАЗРЕШЕНА (APPLY_PERMITTED): %s' % ('ДА' if not gate else 'НЕТ'))
    for warning in result.schema_warnings:
        add('  СХЕМА: %s' % warning)
    for line in gate:
        if not line.startswith('SCHEMA: '):
            add('  %s' % line)
    if result.schema_warnings:
        add('  Предупреждение схемы не меняет прочитанное: строки выше')
        add('  разобраны полностью; база без колонки не даёт только ЗАПИСАТЬ.')
    if result.svodka_sheets:
        add('  Листы «СВОДКА» (%d) читаются своим читателем и в мост не входят.'
            % result.svodka_sheets)


def write_report(path, result, stats, args, batch, include_prediction=True):
    """The UTF-8 report file. include_prediction guards ONE block.

    [REASON]: DRONE-WORKS-UPLOAD-001. EXPECTED_BY_MONTH is the owner's hand
    parse of all 28 books on 2026-08-04 -- a one-time historical anchor for
    the one-time run over the whole corpus. In a monthly upload of one or two
    books through the screen it would report a shortfall of hundreds of rows
    against a total that has nothing to do with what was uploaded, which is
    worse than printing nothing. The CLI passes nothing and keeps the default,
    so its output stays byte-identical; the web path passes False.
    """
    lines = []
    add = lines.append
    add('DRONE-WORKS-001 — отчёт импорта')
    add('Режим: %s' % ('ЗАПИСЬ (--apply)' if args.apply
                       else 'СУХОЙ ПРОГОН (ничего не записано)'))
    add('Каталог: %s' % args.dir)
    add('Манифест: %s' % args.manifest)
    add('База: %s' % args.db)
    add('Партия импорта: %s' % batch)
    add('')
    add('ИТОГИ')
    add('  файлов разобрано: %d' % stats['files'])
    add('  листов: %d' % stats['sheets'])
    add('  строк принято: %d' % stats['rows'])
    add('  гектаров: %.2f' % stats['area'])
    add('  сумма: %.2f' % stats['amount'])
    add('  получено: %.2f' % stats['received'])
    add('  прочие расходы: %.2f' % stats['other_costs'])
    add('  различных написаний заказчика: %d' % stats['customers'])
    add('')
    add('ПО ТИПАМ ОПЛАТЫ')
    for payment in PAYMENT_TYPES:
        add('  %-9s %5d строк  %10.2f га'
            % (payment, stats['payments'][payment],
               stats['payment_area'][payment]))
    add('')
    add('ПО ВИДУ ДАТЫ')
    for kind in (DATE_KIND_EXACT, DATE_KIND_SPAN, DATE_KIND_NONE,
                 DATE_KIND_UNPARSED):
        add('  %-9s %5d строк  %10.2f га'
            % (kind, stats['kinds'][kind], stats['kind_area'][kind]))
    add('')
    add('ПО МЕСЯЦАМ')
    for month in sorted(stats['months']):
        count, month_area = stats['months'][month]
        add('  %-8s %5d строк  %10.2f га' % (month, count, month_area))

    add('')
    add('ФАЙЛЫ')
    for file_name, period, subdivision, sheets in result.files_seen:
        add('  %s — период %s, подразделение %s, листов %d'
            % (file_name, period, subdivision or '(не определено)', sheets))
    if result.files_skipped_no_manifest:
        add('')
        add('ФАЙЛЫ БЕЗ ЗАПИСИ В МАНИФЕСТЕ — НЕ ОБРАБОТАНЫ')
        add('  Период не угадывается: угаданный период поставил бы сентябрьскую')
        add('  работу в март. Добавьте строку в манифест и повторите прогон.')
        for file_name in result.files_skipped_no_manifest:
            add('  - %s' % file_name)
    if result.manifest_problems:
        add('')
        add('СТРОКИ МАНИФЕСТА, КОТОРЫЕ НЕ РАЗОБРАНЫ')
        for lineno, text in result.manifest_problems:
            add('  - строка %d: %s' % (lineno, text))

    if include_prediction:
        add('')
        add('ПО МЕСЯЦАМ МАНИФЕСТА, ПРОТИВ ПРЕДСКАЗАНИЯ ОТ 2026-08-04')
        add('  Помесячные строки ОРИЕНТИРОВОЧНЫ: инструмент группирует')
        add('  датированную строку по её собственной дате, а одна книга лежит')
        add('  между сентябрём и октябрём. Твёрдое число — итог. Апрель —')
        add('  якорь: 6 388.83 га сходятся с нашими же данными DJI за апрель')
        add('  2026 (6 379.24 га на 6 403 вылетах) до 0.15 %.')
        _expected = dict((m, (r, a)) for m, r, a in EXPECTED_BY_MONTH)
        for month in sorted(set(stats['manifest_months']) | set(_expected)):
            count, month_area = stats['manifest_months'].get(month, (0, 0.0))
            exp_rows, exp_area = _expected.get(month, (0, 0.0))
            add('  %-8s %5d строк %11.2f га | ожидалось %5d %11.2f | %+.2f'
                % (month, count, month_area, exp_rows, exp_area,
                   month_area - exp_area))
        add('  ИТОГО    %5d строк %11.2f га | ожидалось %5d %11.2f | %+.2f'
            % (stats['rows'], stats['area'], EXPECTED_TOTAL_ROWS,
               EXPECTED_TOTAL_AREA, stats['area'] - EXPECTED_TOTAL_AREA))

    add('')
    add('ПРОПУЩЕННЫЕ ЛИСТЫ: %d' % len(result.skipped_sheets))
    add('  Импортируются только листы, чьё название начинается на «свод ичи».')
    add('  «ОБШИЙ СВОД», «олинмаган пуллар», «свод фх» и подобные — это')
    add('  сводки и реестры: их строки повторяют уже импортированные из')
    add('  «свод ичи», а строки «ЖАМИ:» вообще не являются работами.')
    for file_name, sheet_name in result.skipped_sheets:
        add('  - %s / %s' % (file_name, sheet_name))

    add('')
    add('ЗАГОЛОВКИ, НЕ СОВПАВШИЕ НИ С ОДНОЙ ИЗВЕСТНОЙ ФРАЗОЙ: %d'
        % len(result.unmatched_headers))
    add('  Поле остаётся NULL, строка импортируется. Настоящих раскладок')
    add('  двадцать четыре; может появиться двадцать пятая — вот она.')
    for header, places in sorted(result.unmatched_headers.items()):
        add('  - «%s» — %d раз(а)' % (header, len(places)))
        for file_name, sheet_name in places[:5]:
            add('      %s / %s' % (file_name, sheet_name))
        if len(places) > 5:
            add('      ... и ещё %d' % (len(places) - 5))

    add('')
    add('ЛИСТЫ')
    for sheet in result.sheets:
        add('  %s / %s' % (sheet.file_name, sheet.sheet_name))
        if sheet.skipped_reason:
            add('    ПРОПУЩЕН: %s' % sheet.skipped_reason)
            continue
        add('    оператор: %s%s'
            % (sheet.operator_raw or '(пусто)',
               ' — ВЗЯТ ИЗ ИМЕНИ ФАЙЛА, в заголовке листа имени нет'
               if sheet.operator_from_file_name else ''))
        add('    строк: %d, гектаров: %.2f' % (sheet.rows, sheet.area))
        if sheet.operator_from_row:
            add('    оператор из колонки строки: %d, из названия листа: %d'
                % (sheet.operator_from_row, sheet.operator_from_sheet))
        if sheet.header_row:
            add('    шапка в строке %d: %s'
                % (sheet.header_row, ' | '.join(sheet.header_cells)))
            add('    колонки: %s'
                % ', '.join('%s->%d («%s»)'
                            % (field, index,
                               sheet.column_map.chosen.get(field, ''))
                            for field, index
                            in sorted(sheet.column_map.fields.items())))
            if sheet.column_map.expenses:
                add('    расходы суммируются из: %s'
                    % ', '.join('«%s» (кол. %d)' % (header, index)
                                for index, header
                                in sheet.column_map.expenses))
            if sheet.column_map.received_kind:
                add('    «получено» прочитано как: %s'
                    % sheet.column_map.received_kind)
            if sheet.column_map.shadowed:
                add('    ВТОРОЕ СОВПАДЕНИЕ, не использовано: %s'
                    % ', '.join('%s кол. %d («%s»)' % (f, i, h)
                                for f, i, h in sheet.column_map.shadowed))
            if sheet.column_map.unmatched:
                add('    ЗАГОЛОВКИ БЕЗ СООТВЕТСТВИЯ: %s'
                    % ', '.join('«%s» (кол. %d)' % (h, i)
                                for i, h in sheet.column_map.unmatched))
            if sheet.missing_fields:
                add('    НЕ ОПОЗНАНЫ КОЛОНКИ: %s — эти поля записаны как NULL'
                    % ', '.join(sheet.missing_fields))
        else:
            add('    шапка не найдена — лист не дал ни одной строки')
        if sheet.blocks:
            add('    блоки: %s'
                % ', '.join('%d:%s' % (r, b) for r, b in sheet.blocks))
        if sheet.wage_rows:
            add('    строк «Иш хаки» пропущено: %d' % sheet.wage_rows)
        if sheet.unknown_payment_rows:
            add('    строк без блока оплаты (взяли умолчание — наличка): %d'
                % sheet.unknown_payment_rows)
        if sheet.empty_customer_rows:
            add('    строк без имени заказчика: %d'
                % sheet.empty_customer_rows)

    add('')
    add('ДУБЛИ, ПРОПУЩЕННЫЕ В ЭТОМ ПРОГОНЕ: %d' % len(result.duplicates))
    add('  Совпали числа И установлена идентичность оператора;')
    add('  неоднозначные совпадения сохранены отдельно.')
    add('  показаны: ни одна строка не склеена молча и не посчитана дважды.')
    for first, second in result.duplicates:
        add('  - «%s» %.2f га %s сумма %s'
            % (second['customer_raw'], second['area_ha'],
               second['work_date_from'] or 'без даты',
               second['amount'] if second['amount'] is not None else '—'))
        add('      принято: %s / %s / строка %d'
            % (first['source_file'], first['source_sheet'],
               first['source_row']))
        add('      пропущено: %s / %s / строка %d'
            % (second['source_file'], second['source_sheet'],
               second['source_row']))

    add('')
    add('ПОХОЖИЕ ПО ЧИСЛАМ ПАРЫ, СОХРАНЕННЫЕ ДЛЯ ПРОВЕРКИ: %d'
        % len(result.suspected_duplicates))
    for first, second in result.suspected_duplicates:
        add('  - отдельные записи: %s / %s / строка %d'
            % (first['source_file'], first['source_sheet'], first['source_row']))
        add('    + %s / %s / строка %d'
            % (second['source_file'], second['source_sheet'], second['source_row']))
    add('')
    add('МЕЖМЕСЯЧНЫЕ ПЕРЕНОСЫ ЗАРПЛАТЫ: КАРАНТИН / НЕТ В drone_works: %d'
        % len(result.quarantined_rows))
    for row in result.quarantined_rows:
        add('  %s / %s / row %d / period %s / date %s / area %.4f / amount %s'
            % (row['source_file'], row['source_sheet'], row['source_row'],
               row['period_month'], row['date_raw'], row['area_ha'],
               row['amount'] if row['amount'] is not None else 'NULL'))
        add('    ПРИМЕЧАНИЕ: %s' % (row.get('note') or ''))
    add('НЕБЕЗОПАСНЫЕ ВАРИАНТЫ ИМЁН ФАЙЛОВ (APPLY ОСТАНОВЛЕН): %d'
        % len(result.filename_alias_conflicts))
    for old, new, sheet, source_row in result.filename_alias_conflicts:
        add('  %s -> %s / %s / row %d' % (old, new, sheet, source_row))
    add('')
    add('СТРОКИ, ОТКЛОНЁННЫЕ ПРИ РАЗБОРЕ: %d' % len(result.rejections))
    for file_name, sheet_name, excel_row, reason, preview in result.rejections:
        add('  - %s / %s / строка %d: %s'
            % (file_name, sheet_name, excel_row, reason))
        add('      %s' % preview)

    unparsed = [r for r in result.rows if r['date_kind'] == DATE_KIND_UNPARSED]
    add('')
    add('ДАТЫ, КОТОРЫЕ НЕ РАЗОБРАНЫ: %d' % len(unparsed))
    add('  Строки СОХРАНЕНЫ: date_raw заполнен, обе даты NULL. Не потеряны.')
    for row in unparsed:
        add('  - %s / %s / строка %d: «%s»'
            % (row['source_file'], row['source_sheet'], row['source_row'],
               row['date_raw']))

    add('')
    add('ОПЕРАТОРЫ, КОТОРЫЕ НЕ ОПРЕДЕЛЕНЫ')
    add('  drone_operator_id остаётся NULL. Строка не отклоняется.')
    for (raw, reason), count in sorted(result.unresolved_operators.items(),
                                       key=lambda kv: -kv[1]):
        add('  - «%s»: %s, строк %d' % (raw, reason, count))

    add('')
    add('ЗАКАЗЧИКИ')
    add('  сопоставлено с алиасом: %d строк' % result.matched_customers)
    add('  будет заведено новых: %d' % len(result.created_customers))
    for key, raw_name in result.created_customers:
        add('  + %s' % raw_name)
    add('  плательщик-гражданин не установлен («Фукаро»): %d строк -- '
        'без карточки' % result.payer_unidentified_rows)
    _write_fin_recon_003_sections(add, result, stats)

    if result.month_outliers:
        add('')
        add('ФАЙЛЫ, ЧЬИ ДАТИРОВАННЫЕ СТРОКИ ВЫХОДЯТ ЗА МЕСЯЦ МАНИФЕСТА')
        add('  Датированные строки сохраняют СВОИ даты; месяц манифеста')
        add('  наследуют только строки без даты.')
        for file_name, months in sorted(result.month_outliers.items()):
            add('  - %s: встречены месяцы %s'
                % (file_name, ', '.join(sorted(months))))

    if result.formula_no_cache:
        add('')
        add('ЯЧЕЙКИ С ФОРМУЛОЙ БЕЗ СОХРАНЁННОГО ЗНАЧЕНИЯ: %d строк'
            % result.formula_no_cache)
        add('  Файл сохранён программой, которая не считала формулы.')
        add('  Откройте книгу в Excel, пересчитайте, сохраните и повторите.')

    with open(path, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(lines) + '\n')


# ─── Entry point ─────────────────────────────────────────────────────────────

def build_parser():
    parser = argparse.ArgumentParser(
        description='Import the dispatchers\' drone books into drone_works.')
    parser.add_argument('--dir', required=True,
                        help='directory of .xlsx files')
    parser.add_argument('--manifest', required=True,
                        help='file -> YYYY-MM mapping')
    parser.add_argument('--db', default=DEFAULT_DB_PATH,
                        help='SQLite database (default instance/transport.db)')
    parser.add_argument('--dry-run', action='store_true', default=True,
                        help='DEFAULT. Parse, report, write nothing.')
    parser.add_argument('--apply', action='store_true',
                        help='actually write')
    parser.add_argument('--batch', default=None, help='import_batch value')
    parser.add_argument('--report', default=None,
                        help='UTF-8 report file (default next to the manifest)')
    parser.add_argument('--default-payment', choices=PAYMENT_OVERRIDE_TYPES,
                        default=None,
                        help='payment type for rows appearing before any '
                             "block marker. Off by default: such rows get "
                             "'unknown', which is reported and is fixable on "
                             'the works screen.')
    parser.add_argument('--rows-csv', default=None,
                        help='UTF-8 CSV, one line per physical row that '
                             'carries job data: accepted, duplicate, '
                             'quarantined, rejected or UNPARSED_CANDIDATE')
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.apply:
        args.dry_run = False
    if not os.path.isdir(args.dir):
        print('ERROR: not a directory: %s' % _ascii(args.dir))
        return 2
    if not os.path.isfile(args.manifest):
        print('ERROR: manifest not found: %s' % _ascii(args.manifest))
        return 2
    if args.report is None:
        args.report = os.path.join(os.path.dirname(
            os.path.abspath(args.manifest)), 'drone_works_import_report.txt')
    batch = args.batch or ('import_%s' % datetime.datetime.now()
                           .strftime('%Y%m%d_%H%M%S'))

    manifest, manifest_problems = read_manifest(args.manifest)
    files = sorted(f for f in os.listdir(args.dir)
                   if f.lower().endswith('.xlsx') and not f.startswith('~$'))

    try:
        con = _connect(args.db, read_only=not args.apply)
        try:
            _require_tables(con, ('drone_works', 'drone_customers',
                                  'drone_customer_aliases', 'drone_operators'))
            # [REASON]: FIN-RECON-003. A missing column is a reason not to
            # WRITE, never a reason not to READ: the 30.10.2026 snapshot
            # lacks payer_unidentified, and refusing the dry run there hid
            # what the books contain behind a schema message. --apply still
            # refuses exactly as before (same text, exit 2); the dry run
            # reads the books and prints the refusal as APPLY_PERMITTED: NO.
            # The upload screen calls the two checks itself and is unchanged.
            schema_warnings = []
            for check in (_require_received_kind,
                          _require_payer_unidentified):
                try:
                    check(con)
                except ImportPreconditionError as exc:
                    if args.apply:
                        raise
                    schema_warnings.append(str(exc))
            dir_obj = Directory(con)
            result = collect(args.dir, manifest, files, dir_obj,
                             args.default_payment)
            result.schema_warnings = schema_warnings
            result.manifest_problems = manifest_problems
            resolve(result, dir_obj)
            result.filename_alias_conflicts = filename_alias_conflicts(
                con, result.rows)
            stats = summarize(result)
            if args.apply and result.filename_alias_conflicts:
                raise ImportPreconditionError(
                    "ERROR UNSAFE_FILENAME_PROVENANCE: %d old/new name "
                    "collisions. Nothing written. Reconcile exact "
                    "source_file provenance in an approved migration first."
                    % len(result.filename_alias_conflicts))

            if args.apply:
                created, inserted, skipped = apply_rows(con, result, batch)
            else:
                created = inserted = skipped = 0
        except Exception:
            try:
                con.rollback()
            except sqlite3.Error:
                pass
            raise
        finally:
            con.close()
    except ImportPreconditionError as exc:
        # [REASON]: the same text on stdout and the same exit code the three
        # checks produced when they killed the process themselves. From a
        # shell nothing about this tool's behaviour has moved; what moved is
        # that a caller which is not a shell now gets an exception it can
        # show instead of a dead thread.
        print(str(exc))
        return 2

    write_report(args.report, result, stats, args, batch)
    if args.rows_csv:
        write_rows_csv(args.rows_csv, result)
    print_console(result, stats, args, batch)
    if args.apply:
        print('')
        print('WRITTEN: %d customers created, %d works inserted, '
              '%d already present and skipped.' % (created, inserted, skipped))
    else:
        print('')
        print('DRY RUN: nothing was written. Re-run with --apply to write.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
