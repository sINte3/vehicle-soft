# -*- coding: utf-8 -*-
"""drone_works_upload.py -- DRONE-WORKS-UPLOAD-001, the layer under the screen.

The dispatchers' books used to enter the ledger only through
tools/import_drone_works.py, run by the owner from a PowerShell prompt with a
hand-written manifest. Flight data arrives automatically every night; the
books still arrive by hand once a month. This module is what the routes call
so that the same code path is testable without a browser.

**IT IS A SHELL AROUND THE EXISTING IMPORTER, NOT A SECOND IMPORTER.** Every
parsing decision -- header detection, column mapping, payment blocks, dates,
prices, duplicates, operator resolution, the INSERT -- belongs to
tools/import_drone_works.py and is called, never re-implemented. That module's
parse logic was written against one book out of 28, shipped wrong, and was
then repaired against 24 real header variants; it produces 904 works /
15 878.64 ha / 713 customers from the real corpus with zero rejected rows. A
second implementation of any part of it would be a second thing to keep right.

No routes here. drones.py owns those.
"""

import hashlib
import json
import os
import sys
import time

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tools.import_drone_works import (  # noqa: E402
    Directory,
    ImportPreconditionError,
    _connect,
    _require_received_kind,
    _require_tables,
    apply_rows,
    collect,
    resolve,
    summarize,
    write_report,
)

# The books live next to the database, never inside the source tree and never
# under app.instance_path. Production and staging then separate automatically,
# because their database paths differ.
BOOKS_DIR_NAME = 'drone_work_books'

PREVIEW_FILE = 'preview.json'
REPORT_FILE = 'report.txt'

# Limits. The route checks the request size before the body is read; these
# two are checked per batch and per file once Werkzeug has parsed the form.
MAX_FILES_PER_BATCH = 12

# [REASON]: DRONE-WORKS-UPLOAD-002. 25 MB rejected two of the twenty-eight
# real dispatcher books -- «Агрокластер Дрон маълумот Март.xlsx» is 78 958 503
# bytes for THREE rows and «Имомов Бехзод Пешку ПТЗ Апрель.xlsx» is 39 340 234
# for twenty. The weight is embedded images, not data: the size of a book says
# nothing about how much of it is a ledger, so a limit sized from the row
# count is the wrong limit. 100 MiB clears the largest book observed with room
# for a bigger one.
MAX_FILE_BYTES = 100 * 1024 * 1024

# [REASON]: 240 MiB is EXACTLY 80 % of app.py's MAX_CONTENT_LENGTH
# (300 * 1024 * 1024 = 314 572 800 bytes), and it is capped by that and by
# nothing else. Above 80 % the multipart envelope -- boundaries, per-part
# headers, the period and note fields -- can push a batch that this module
# would have accepted over Werkzeug's global limit, and Werkzeug answers 413
# before any view runs: the operator would get a bare error page instead of
# the bilingual refusal below, naming no file and no rule. MAX_CONTENT_LENGTH
# is shared with every other module and is deliberately NOT raised to make a
# larger batch fit.
MAX_UPLOAD_BYTES = 240 * 1024 * 1024

MAX_NAME_LENGTH = 150

# [REASON]: the four tables tools/import_drone_works.py reads and writes.
# Spelled out here rather than imported from main(), which builds the same
# tuple inline; if one grows a fifth table the run fails loudly on both paths.
REQUIRED_TABLES = ('drone_works', 'drone_customers', 'drone_customer_aliases',
                   'drone_operators')

# Characters Windows forbids in a file name, plus the POSIX separator. Control
# characters are handled separately.
FORBIDDEN_NAME_CHARS = '\\/:*?"<>|'

_HASH_CHUNK = 1024 * 1024


class UploadRejected(Exception):
    """A refusal the uploader is shown, in both languages.

    [REASON]: bilingual at the point of refusal, not at the point of display.
    The message names the file and the rule it broke, and the route only has
    to pick a language -- so the two translations cannot drift apart in a
    template nobody re-reads.
    """

    def __init__(self, message_ru, message_uz):
        super(UploadRejected, self).__init__(message_ru)
        self.message_ru = message_ru
        self.message_uz = message_uz

    def text(self, is_ru):
        return self.message_ru if is_ru else self.message_uz


# ─── Where the books live ────────────────────────────────────────────────────

def db_path_from_uri(uri):
    """The SQLite file behind SQLALCHEMY_DATABASE_URI.

    Everything in this increment writes through stdlib sqlite3, so a non
    SQLite URI is not a degraded mode -- it is a different product, and
    saying so beats failing later inside apply_rows().
    """
    prefix = 'sqlite:///'
    if not (uri or '').startswith(prefix):
        raise UploadRejected(
            'Загрузка ведомостей работает только с базой SQLite. '
            'Текущая база другого типа.',
            'Ведомостларни юклаш фақат SQLite базаси билан ишлайди. '
            'Ҳозирги база бошқа турда.')
    return uri[len(prefix):]


def books_root_for_db(db_path):
    """<directory of the SQLite file>/drone_work_books.

    [REASON]: derived from the DATABASE, not from app.instance_path. The two
    happen to coincide today, but the database path is what distinguishes
    production from staging; deriving the books root from anything else would
    let a staging refresh point at production's files.
    """
    return os.path.join(os.path.dirname(os.path.abspath(db_path)),
                        BOOKS_DIR_NAME)


def ensure_books_root(books_root):
    """Create the books root on first use. Returns it."""
    if not os.path.isdir(books_root):
        os.makedirs(books_root)
    return books_root


def batch_path_for(books_root, storage_dir):
    return os.path.join(books_root, str(storage_dir))


# ─── File name validation ────────────────────────────────────────────────────

def validate_upload_name(name):
    """Raise UploadRejected unless the name may be stored verbatim.

    **DO NOT USE werkzeug.utils.secure_filename HERE.** It strips non-ASCII,
    and every real book is named in Cyrillic -- «Гиждувон 04.2026.xlsx» comes
    back as «04.2026.xlsx» or worse. The original file name is load-bearing in
    three separate places:

      1. Directory.subdivision_for_file() finds the subdivision by searching
         the file name for a known subdivision name;
      2. operator_from_sheet_title() falls back to the file stem when the
         sheet title carries no operator name -- «свод ичи » is a real title;
      3. source_file is one third of the provenance key
         (source_file, source_sheet, source_row), which is what makes a
         repeated import insert zero rows instead of a second copy of the
         book.

    A renamed file is a different book as far as the database is concerned.
    So the rule is REJECT, NEVER SANITISE: a name this function cannot accept
    goes back to the uploader to be fixed, because silently changing it would
    change what the row means.
    """
    raw = name or ''
    stripped = raw.strip()
    if not stripped:
        raise UploadRejected('Пустое имя файла.', 'Файл номи бўш.')
    # [REASON]: rejected rather than trimmed. Windows silently drops trailing
    # spaces and dots when creating a file, so a name with one would be stored
    # under a DIFFERENT name than the one recorded in files_json, and
    # verify_unchanged() would then refuse every apply with a missing file.
    if raw != stripped:
        raise UploadRejected(
            'Имя файла «%s» начинается или заканчивается пробелом.' % raw,
            '«%s» файл номи бўш жой билан бошланади ёки тугайди.' % raw)
    if len(raw) > MAX_NAME_LENGTH:
        raise UploadRejected(
            'Имя файла длиннее %d символов: «%s».' % (MAX_NAME_LENGTH, raw),
            'Файл номи %d белгидан узун: «%s».' % (MAX_NAME_LENGTH, raw))
    for char in raw:
        if ord(char) < 32 or ord(char) == 127:
            raise UploadRejected(
                'В имени файла «%s» есть управляющий символ.' % raw,
                '«%s» файл номида бошқарув белгиси бор.' % raw)
    bad = [c for c in FORBIDDEN_NAME_CHARS if c in raw]
    if bad:
        raise UploadRejected(
            'В имени файла «%s» есть недопустимые символы: %s'
            % (raw, ' '.join(bad)),
            '«%s» файл номида рухсат этилмаган белгилар бор: %s'
            % (raw, ' '.join(bad)))
    # [REASON]: belt and braces after the separator rule above. os.path
    # answers for the platform the code runs on, and the server is Windows
    # while the checks may be run anywhere; '..\\evil.xlsx' is not a
    # traversal on POSIX and is one on Windows.
    if os.path.basename(raw) != raw or os.path.dirname(raw):
        raise UploadRejected(
            'Имя файла «%s» содержит путь.' % raw,
            '«%s» файл номи йўлни ўз ичига олади.' % raw)
    if raw in ('.', '..'):
        raise UploadRejected('Недопустимое имя файла «%s».' % raw,
                             '«%s» — яроқсиз файл номи.' % raw)
    if raw.startswith('~$'):
        raise UploadRejected(
            'Файл «%s» — временный файл блокировки Excel, а не ведомость.'
            % raw,
            '«%s» — Excel’нинг вақтинчалик қулф файли, ведомость эмас.' % raw)
    if not raw.lower().endswith('.xlsx'):
        raise UploadRejected(
            'Файл «%s» не .xlsx. Принимаются только книги Excel.' % raw,
            '«%s» файли .xlsx эмас. Фақат Excel китоблари қабул қилинади.'
            % raw)
    return raw


def stream_size(item):
    """Bytes in an uploaded file, without reading it into memory.

    Werkzeug has already spooled the whole request body by the time a view
    runs, so this is a seek, not a read.
    """
    stream = item.stream
    position = stream.tell()
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(position)
    return size


def validate_batch(files):
    """Every rule that can be checked BEFORE anything is created on disk.

    Returns [(name, size)]. Raises UploadRejected on the first problem: count,
    duplicate name inside the batch, name shape, empty file, per-file size.
    """
    if not files:
        raise UploadRejected('Ни одного файла не выбрано.',
                             'Ҳеч қандай файл танланмаган.')
    if len(files) > MAX_FILES_PER_BATCH:
        raise UploadRejected(
            'За один раз можно загрузить не больше %d файлов, выбрано %d.'
            % (MAX_FILES_PER_BATCH, len(files)),
            'Бир вақтда %d тагача файл юклаш мумкин, %d та танланган.'
            % (MAX_FILES_PER_BATCH, len(files)))

    checked = []
    seen = set()
    total = 0
    for item in files:
        name = validate_upload_name(getattr(item, 'filename', None))
        # [REASON]: each batch gets its own fresh directory, so a collision
        # inside the batch is the only collision possible. Overwriting would
        # silently drop one of the two books; refusing asks the uploader
        # which one they meant.
        if name in seen:
            raise UploadRejected(
                'Файл «%s» выбран дважды.' % name,
                '«%s» файли икки марта танланган.' % name)
        seen.add(name)
        size = stream_size(item)
        if size <= 0:
            raise UploadRejected('Файл «%s» пуст.' % name,
                                 '«%s» файли бўш.' % name)
        if size > MAX_FILE_BYTES:
            raise UploadRejected(
                'Файл «%s» больше %d МБ.'
                % (name, MAX_FILE_BYTES // (1024 * 1024)),
                '«%s» файли %d МБ дан катта.'
                % (name, MAX_FILE_BYTES // (1024 * 1024)))
        total += size
        checked.append((name, size))
    if total > MAX_UPLOAD_BYTES:
        raise UploadRejected(
            'Загрузка целиком больше %d МБ.'
            % (MAX_UPLOAD_BYTES // (1024 * 1024)),
            'Юклама умумий ҳажми %d МБ дан катта.'
            % (MAX_UPLOAD_BYTES // (1024 * 1024)))
    return checked


# ─── Storing and re-checking the files ───────────────────────────────────────

def store_batch(books_root, batch_dir_name, files):
    """Write the files under their ORIGINAL names. Returns files_meta.

    [{'name': …, 'sha256': …, 'size': …}] in upload order. The directory is
    created fresh and must not already exist: a batch owns its directory, and
    reusing one would let a second upload overwrite the evidence behind an
    apply that already happened.
    """
    batch_path = batch_path_for(ensure_books_root(books_root), batch_dir_name)
    os.makedirs(batch_path)
    meta = []
    for item in files:
        name = validate_upload_name(getattr(item, 'filename', None))
        digest = hashlib.sha256()
        size = 0
        destination = os.path.join(batch_path, name)
        item.stream.seek(0)
        with open(destination, 'wb') as fh:
            while True:
                chunk = item.stream.read(_HASH_CHUNK)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
                fh.write(chunk)
        meta.append({'name': name, 'sha256': digest.hexdigest(),
                     'size': size})
    return meta


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as fh:
        while True:
            chunk = fh.read(_HASH_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def verify_unchanged(batch_path, files_meta):
    """Re-hash every stored file. Raise UploadRejected on any difference.

    [REASON]: between the preview and the confirmation the operator has seen
    numbers and agreed to them. If the bytes behind those numbers moved --
    somebody replaced a file in the batch directory, a copy was interrupted,
    a disk lied -- the agreement was to something else. Refusing costs one
    re-upload; writing would put rows nobody approved into the ledger.
    """
    for entry in files_meta:
        name = entry['name']
        path = os.path.join(batch_path, name)
        if not os.path.isfile(path):
            raise UploadRejected(
                'Файл «%s» пропал из каталога партии. Импорт отменён.' % name,
                '«%s» файли партия каталогидан йўқолган. Импорт бекор '
                'қилинди.' % name)
        size = os.path.getsize(path)
        digest = sha256_of(path)
        if digest != entry['sha256'] or size != entry['size']:
            raise UploadRejected(
                'Файл «%s» изменился после предпросмотра '
                '(было %d байт / %s, стало %d байт / %s). Ничего не '
                'записано: загрузите ведомость заново.'
                % (name, entry['size'], entry['sha256'][:12], size,
                   digest[:12]),
                '«%s» файли кўриб чиқишдан кейин ўзгарган '
                '(%d байт / %s эди, %d байт / %s бўлди). Ҳеч нарса '
                'ёзилмади: ведомостни қайтадан юкланг.'
                % (name, entry['size'], entry['sha256'][:12], size,
                   digest[:12]))
    return True


# ─── The run ─────────────────────────────────────────────────────────────────

class _ReportArgs(object):
    """The four attributes write_report() and print_console() read.

    There is no argparse namespace on this path and there is no manifest
    file, so the header of the report says so instead of naming one.
    """

    def __init__(self, apply, dir, manifest, db):
        self.apply = apply
        self.dir = dir
        self.manifest = manifest
        self.db = db


# The header line write_report() ends its own block on. The parse time is
# inserted directly after it.
_REPORT_HEADER_ANCHOR = 'Партия импорта: '
PARSE_SECONDS_LABEL = 'Разбор: '


def format_parse_seconds(seconds):
    """The one rendering of a parse duration, used by the report and the JSON.

    One decimal, and one place that decides that. A duration printed to six
    digits invites the reader to compare two runs that differ by jitter.
    """
    return round(float(seconds), 1)


def add_parse_seconds_to_report(report_path, seconds):
    """Insert «Разбор: N.N с» into the header of a report already written.

    [REASON]: the header is composed by write_report() in
    tools/import_drone_works.py, which the CLI shares. Adding the line there
    would change the output of every hand run from a PowerShell prompt, and
    that output is the artifact the owner compares between corpus runs. So
    the web path adds its own line to its own copy afterwards, and the
    importer stays byte-identical -- and out of this diff.

    Never raises. By the time this runs the parse is finished and, on the
    apply path, the rows are already committed; failing here would mark a
    batch broken over a diagnostic file. A report whose anchor moved gets the
    line under the title instead, which is still the header.
    """
    try:
        with open(report_path, encoding='utf-8') as fh:
            lines = fh.read().split('\n')
    except (IOError, OSError):
        return False
    position = 1
    for index, line in enumerate(lines):
        if line.startswith(_REPORT_HEADER_ANCHOR):
            position = index + 1
            break
    lines.insert(position, '%s%.1f с' % (PARSE_SECONDS_LABEL,
                                         format_parse_seconds(seconds)))
    try:
        with open(report_path, 'w', encoding='utf-8') as fh:
            fh.write('\n'.join(lines))
    except (IOError, OSError):
        return False
    return True


def run_import(db_path, batch_path, files_meta, period_month, apply, batch,
               report_path):
    """Parse (and optionally write) one stored batch. (result, stats, counts).

    CONCURRENCY, AND IT IS NOT OPTIONAL. The web process already holds
    SQLAlchemy connections to this same SQLite file. WAL allows many readers
    with one writer, but two writers block each other for the whole
    busy_timeout and then fail. Before this function opens the READ-WRITE
    connection (apply=True) the caller MUST have committed or rolled back the
    ORM session, and MUST perform no ORM write for the rest of the request.
    The route honours that: it commits the journal row, then calls this, then
    writes the journal update through a session that was clean at the moment
    the sqlite3 connection closed. apply=False opens the database read-only
    through a file: URI with mode=ro and cannot conflict with anything.

    default_payment is always None here. The CLI flag exists because about
    two fifths of the real corpus sits in sheets with no payment block; such
    rows become 'unknown', which is reported and fixable on the works screen.
    It is deliberately not exposed on the form: a payment type this tool made
    up is indistinguishable from one the dispatcher wrote down.
    """
    # The manifest exists only in memory: a form cannot know the file names
    # before the upload happens, so one month is chosen on the form and
    # applied to every file in the batch. period_month governs ONLY rows with
    # no parseable date -- a dated row is bucketed by its own date, and
    # RunResult.month_outliers reports the ones that fall outside.
    manifest = dict((entry['name'], period_month) for entry in files_meta)
    files = sorted(manifest)

    con = _connect(db_path, read_only=not apply)
    try:
        _require_tables(con, REQUIRED_TABLES)
        _require_received_kind(con)
        dir_obj = Directory(con)
        # [REASON]: time.monotonic, never time.time. The wall clock on this
        # server is corrected by the domain and steps both ways; a parse that
        # straddled a correction would report a negative duration or a
        # minute-long one, and the operator would believe it. The span covers
        # exactly the parse -- openpyxl reading the books, plus the resolve
        # and the summary that walk what it produced. apply_rows() is outside
        # it on purpose: this number answers «how long will the browser sit
        # there before the preview appears», which is the dry pass.
        parse_started = time.monotonic()
        result = collect(batch_path, manifest, files, dir_obj, None)
        resolve(result, dir_obj)
        stats = summarize(result)
        parse_seconds = time.monotonic() - parse_started
        if apply:
            created, inserted, skipped = apply_rows(con, result, batch)
        else:
            created = inserted = skipped = 0
    except Exception:
        try:
            con.rollback()
        except Exception:
            pass
        raise
    finally:
        con.close()

    args = _ReportArgs(
        apply=apply, dir=batch_path, db=db_path,
        manifest='(не используется: период задан на форме — %s)'
                 % period_month)
    # include_prediction=False: EXPECTED_BY_MONTH is the owner's hand parse of
    # all 28 books on 2026-08-04. Against a two-book monthly upload it would
    # print a shortfall of hundreds of rows against a total that has nothing
    # to do with what was uploaded.
    write_report(report_path, result, stats, args, batch,
                 include_prediction=False)
    add_parse_seconds_to_report(report_path, parse_seconds)

    counts = {
        'customers_created': created,
        'works_inserted': inserted,
        'works_skipped_existing': skipped,
        'rows_rejected': len(result.rejections),
        'rows_duplicate': len(result.duplicates),
        'parse_seconds': parse_seconds,
    }
    return result, stats, counts


# ─── The preview snapshot ────────────────────────────────────────────────────

def _iso(value):
    return value.isoformat() if value is not None else None


def _round(value, places=2):
    return None if value is None else round(float(value), places)


def preview_snapshot(result, stats, parse_seconds):
    """Everything the preview screen renders, as plain JSON-able data.

    parse_seconds is DERIVED and belongs here rather than in a column of
    drone_work_imports: it is measured once, never queried, never aggregated,
    and adding a column for it would cost a migration to store a number no
    report will ever group by. preview.json already exists per batch.

    [REASON]: the snapshot is written once at upload time and the GET reads
    it back. A preview that re-parsed on every GET would make a refresh cost
    a full openpyxl pass over up to twelve books, and -- worse -- two GETs
    could disagree if anything under the batch directory moved. The apply
    then compares its own fresh parse against this file and refuses if the
    two differ, which is only possible if something nobody understands has
    happened.
    """
    per_file = {}
    for file_name, period, subdivision, sheets in result.files_seen:
        per_file[file_name] = {
            'name': file_name,
            'period_month': period,
            'subdivision': subdivision,
            'sheets': sheets,
            'detail_sheets': 0,
            'rows': 0,
            'area': 0.0,
        }
    for sheet in result.sheets:
        entry = per_file.get(sheet.file_name)
        if entry is None or sheet.skipped_reason:
            continue
        entry['detail_sheets'] += 1
        entry['rows'] += sheet.rows
        entry['area'] += sheet.area
    for entry in per_file.values():
        entry['area'] = _round(entry['area'])

    declared = sorted(set(period for _f, period, _s, _n in result.files_seen))
    months = []
    for month in sorted(stats['months']):
        count, area = stats['months'][month]
        months.append({'month': month, 'rows': count, 'area': _round(area),
                       'outside_declared_month': month not in declared})

    return {
        'declared_months': declared,
        'files': [per_file[name] for name in sorted(per_file)],
        'parse_seconds': format_parse_seconds(parse_seconds),
        'totals': {
            'files': stats['files'],
            'sheets': stats['sheets'],
            'rows': stats['rows'],
            'area': _round(stats['area']),
            'amount': _round(stats['amount']),
            'received': _round(stats['received']),
            'other_costs': _round(stats['other_costs']),
            'customers': stats['customers'],
            'priced': stats['priced'],
        },
        # The two numbers the apply re-checks against a fresh parse.
        'rows': stats['rows'],
        'area': _round(stats['area']),
        'payments': [{'type': key, 'rows': stats['payments'][key],
                      'area': _round(stats['payment_area'][key])}
                     for key in sorted(stats['payments'])],
        'date_kinds': [{'kind': key, 'rows': stats['kinds'][key],
                        'area': _round(stats['kind_area'][key])}
                       for key in sorted(stats['kinds'])],
        'months': months,
        'month_outliers': [{'file': name, 'months': sorted(found)}
                           for name, found
                           in sorted(result.month_outliers.items())],
        'customers_matched': result.matched_customers,
        'customers_to_create': [name for _key, name
                                in result.created_customers],
        'operators_matched': result.matched_operators,
        'operators_unresolved': [
            {'operator': raw, 'reason': reason, 'rows': count}
            for (raw, reason), count
            in sorted(result.unresolved_operators.items(),
                      key=lambda kv: (-kv[1], kv[0][0]))],
        'duplicates': [
            {'customer': second['customer_raw'],
             'area': _round(second['area_ha']),
             'date': _iso(second['work_date_from']),
             'amount': _round(second['amount']),
             'accepted': {'file': first['source_file'],
                          'sheet': first['source_sheet'],
                          'row': first['source_row']},
             'skipped': {'file': second['source_file'],
                         'sheet': second['source_sheet'],
                         'row': second['source_row']}}
            for first, second in result.duplicates],
        'rejections': [
            {'file': file_name, 'sheet': sheet_name, 'row': excel_row,
             'reason': reason, 'cells': preview}
            for file_name, sheet_name, excel_row, reason, preview
            in result.rejections],
        'skipped_sheets': [{'file': file_name, 'sheet': sheet_name}
                           for file_name, sheet_name
                           in result.skipped_sheets],
        'unmatched_headers': [
            {'header': header, 'count': len(places),
             'places': ['%s / %s' % (f, s) for f, s in places[:5]]}
            for header, places in sorted(result.unmatched_headers.items())],
        'unparsed_dates': [
            {'file': row['source_file'], 'sheet': row['source_sheet'],
             'row': row['source_row'], 'raw': row['date_raw']}
            for row in result.rows if row['date_kind'] == 'unparsed'],
        'already_in_db': result.already_in_db,
        'wage_rows': result.wage_rows,
        'unknown_payment_rows': result.unknown_payment_rows,
        'empty_customer_rows': result.empty_customer_rows,
        'operator_from_row': result.operator_from_row,
        'operator_from_sheet': result.operator_from_sheet,
        'received_kinds': dict(result.received_kinds),
        'formula_no_cache': result.formula_no_cache,
        'files_skipped_no_manifest': list(result.files_skipped_no_manifest),
    }


def write_preview(batch_path, snapshot):
    path = os.path.join(batch_path, PREVIEW_FILE)
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(snapshot, fh, ensure_ascii=False, indent=1, sort_keys=True)
    return path


def read_preview(batch_path):
    with open(os.path.join(batch_path, PREVIEW_FILE), encoding='utf-8') as fh:
        return json.load(fh)


__all__ = [
    'BOOKS_DIR_NAME', 'MAX_FILES_PER_BATCH', 'MAX_FILE_BYTES',
    'MAX_UPLOAD_BYTES', 'MAX_NAME_LENGTH', 'PARSE_SECONDS_LABEL',
    'PREVIEW_FILE', 'REPORT_FILE',
    'ImportPreconditionError', 'UploadRejected',
    'add_parse_seconds_to_report', 'batch_path_for', 'books_root_for_db',
    'db_path_from_uri', 'ensure_books_root', 'format_parse_seconds',
    'preview_snapshot', 'read_preview', 'run_import',
    'sha256_of', 'store_batch', 'stream_size', 'validate_batch',
    'validate_upload_name', 'verify_unchanged', 'write_preview',
]
