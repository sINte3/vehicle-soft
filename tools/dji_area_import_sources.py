# -*- coding: utf-8 -*-
"""tools/dji_area_import_sources.py -- импорт сохранённых источников DJI
(доказательственный/тестовый импорт) в таблицы `dji_*`.

ЧТО ЭТО. Перенос уже захваченных тел ответов DJI из форензик-архива в
хранилище ревизий: карточки вылетов, маршруты (protobuf), телеметрия V4,
airlines-метаданные, снимки каталога полей и байты геометрий. Каждая
ревизия помечается `is_evidence_import=1` и `capture_run_id`
`evidence-import:<имя каталога>`; путь и SHA256 исходного файла ложатся в
`request_context_json`. Это НЕ живой сбор: живой сбор идёт через
`drone_collector --sources` и `/drones/api/source_sync`.

ЧЕГО ЗДЕСЬ НЕТ. Никаких классификаций из исследования: ни CSV с вердиктами,
ни «подтверждённых дублей». Импортируются только тела источников; статусы
считает `tools/dji_area_recalc.py` из них. Отсутствующее V4 остаётся
отсутствующим -- ничего не выдумывается.

Ожидаемая раскладка каталога (multiday_202608):

  raw/list/YYYY-MM-DD.json          -- дневные захваты списка (обёртка + flights[])
  cards/<id>.json                   -- карточка (JSON, пересериализован)
  cards/<id>.route.bin              -- маршрут (raw protobuf)
  v4/<id>/airline_v4.bin            -- V4 (raw protobuf)
  v4/<id>/flight_record_card.json   -- карточка (raw HTTP body) -- приоритетнее cards/
  v4/<id>/route.bin                 -- маршрут (raw) -- приоритетнее cards/ (чистый)
  v4/<id>/airlines.json             -- airlines (подписи уже REDACTED; URL режется до пути)
  lands/page_NNN.json               -- снимок каталога полей

  --geometry-dir <historical>/raw/geometry   -- <land uuid>.geometry.json
  --lands-dir <historical>/raw/lands_20260907 -- второй снимок (частичный)

Запуск (служба остановлена либо база -- копия):

  & "C:\\Program Files\\Python314\\python.exe" tools\\dji_area_import_sources.py --root C:\\VehicleSoft_DJI_Forensics\\multiday_202608 --db instance\\transport.db --dry-run
  & "C:\\Program Files\\Python314\\python.exe" tools\\dji_area_import_sources.py --root ... --geometry-dir ... --lands-dir ... --apply

Коды возврата: 0 выполнено; 1 ошибка; 2 база не найдена. Консоль -- ASCII.
"""

import argparse
import glob
import hashlib
import json
import os
import re
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dji_area import evidence as ev  # noqa: E402
from dji_area import store  # noqa: E402

DEFAULT_DB = os.path.join(ROOT, 'instance', 'transport.db')
EXIT_OK = 0
EXIT_USAGE = 1
EXIT_NO_DATABASE = 2

_URL_QUERY = re.compile(r'\?.*$')


def build_parser():
    parser = argparse.ArgumentParser(
        prog='dji_area_import_sources.py',
        description='Import preserved DJI source bodies (evidence import) '
                    'into the dji_* provenance tables.')
    parser.add_argument('--root', required=True, metavar='DIR',
                        help='forensic capture root (multiday layout)')
    parser.add_argument('--geometry-dir', metavar='DIR',
                        help='directory with <land uuid>.geometry.json bytes')
    parser.add_argument('--lands-dir', action='append', default=[],
                        metavar='DIR', help='extra catalog snapshot page dir')
    parser.add_argument('--db', dest='db_path', default=DEFAULT_DB)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--flight-id', dest='flight_ids', action='append',
                        type=int)
    parser.add_argument('--skip-v4', action='store_true')
    parser.add_argument('--skip-lands', action='store_true')
    parser.add_argument('--limit', type=int, default=0,
                        help='import at most N flights (smoke runs)')
    parser.add_argument('--quiet', action='store_true')
    return parser


def _mtime(path):
    return datetime.utcfromtimestamp(os.path.getmtime(path)).replace(
        microsecond=0)


def _read(path):
    with open(path, 'rb') as fh:
        return fh.read()


def _run_id(root):
    return 'evidence-import:%s' % os.path.basename(os.path.normpath(root))


def _context(path, extra=None):
    body = _read(path)
    ctx = {'source_path': os.path.abspath(path),
           'source_sha256': hashlib.sha256(body).hexdigest(),
           'association': 'directory'}
    if extra:
        ctx.update(extra)
    return ctx


def airlines_paths(path):
    """Пути (без query) из airlines.json; подписи остаются снаружи."""
    try:
        doc = json.loads(_read(path).decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return None
    airline = ((doc.get('data') or {}).get('airline') or {})
    out = {'code': doc.get('code'), 'status': doc.get('status')}
    for key in ('file_v4_url', 'std_detail_url', 'std_summary_url'):
        value = airline.get(key)
        if isinstance(value, str) and value:
            out[key + '_path'] = _URL_QUERY.sub('', value.split('//', 1)[-1])
        else:
            out[key + '_path'] = None
    return out


def flights_from_lists(root, flight_ids=None):
    """{flight_id: (record, day, day_file)} из дневных захватов списка."""
    out = {}
    for path in sorted(glob.glob(os.path.join(root, 'raw', 'list', '*.json'))):
        try:
            doc = json.loads(_read(path).decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            continue
        for rec in doc.get('flights') or []:
            fid = rec.get('id')
            if not isinstance(fid, int):
                continue
            if flight_ids and fid not in flight_ids:
                continue
            out[fid] = (rec, doc.get('date'), path)
    return out


def import_flight_sources(con, root_dir, store_root, fid, list_entry,
                          run_id, skip_v4, counters, write):
    now = store.utcnow()
    common = dict(provider=ev.PROVIDER_ACCOUNT_DEFAULT, capture_run_id=run_id,
                  is_evidence_import=True, now=now)

    def put(source_type, body, captured_at, context, parser_version=None,
            schema_version=None, api_status=None):
        if not write:
            counters['would_store:' + source_type] += 1
            return None
        rev_id, created = store.upsert_source_revision(
            con, store_root, source_type, body, flight_id=fid,
            captured_at_utc=captured_at, request_context=context,
            parser_version=parser_version, schema_version=schema_version,
            api_status=api_status, **common)
        counters[('new:' if created else 'seen:') + source_type] += 1
        return rev_id

    # list record: канонический JSON записи из дневного файла
    if list_entry is not None:
        rec, day, day_file = list_entry
        put(ev.SOURCE_LIST, ev.canonical_record_bytes(rec), _mtime(day_file),
            {'source_path': os.path.abspath(day_file), 'list_day': day,
             'association': 'day_file'},
            schema_version='list-record-canonical-json')

    v4_dir = os.path.join(root_dir, 'v4', str(fid))
    card_raw = os.path.join(v4_dir, 'flight_record_card.json')
    card_json = os.path.join(root_dir, 'cards', '%d.json' % fid)
    if os.path.exists(card_raw):
        body = _read(card_raw)
        put(ev.SOURCE_CARD, body, _mtime(card_raw), _context(card_raw),
            schema_version='raw-http-body', api_status=_api_code(body))
    elif os.path.exists(card_json):
        body = _read(card_json)
        put(ev.SOURCE_CARD, body, _mtime(card_json), _context(card_json),
            schema_version='reserialized-json', api_status=_api_code(body))

    route_raw = os.path.join(v4_dir, 'route.bin')
    route_card = os.path.join(root_dir, 'cards', '%d.route.bin' % fid)
    for path, note in ((route_raw, 'full-page-capture'),
                       (route_card, 'in-spa-capture')):
        if os.path.exists(path):
            body = _read(path)
            put(ev.SOURCE_ROUTE, body, _mtime(path),
                _context(path, {'capture_mode': note}),
                parser_version='route-decode-2')
            break

    if not skip_v4:
        air = os.path.join(v4_dir, 'airlines.json')
        v4_path_hint = None
        if os.path.exists(air):
            paths = airlines_paths(air)
            if paths is not None:
                v4_path_hint = paths.get('file_v4_url_path')
                body = json.dumps(paths, ensure_ascii=False, sort_keys=True,
                                  separators=(',', ':')).encode('utf-8')
                put(ev.SOURCE_AIRLINES, body, _mtime(air),
                    _context(air, {'urls': 'stripped-to-path'}),
                    schema_version='airlines-paths-only',
                    api_status=paths.get('code'))
        v4_bin = os.path.join(v4_dir, 'airline_v4.bin')
        if os.path.exists(v4_bin):
            body = _read(v4_bin)
            ctx = _context(v4_bin)
            if v4_path_hint:
                ctx['path'] = v4_path_hint
                ctx['association'] = 'url_path'
            put(ev.SOURCE_V4, body, _mtime(v4_bin), ctx,
                parser_version='v4-parse-1')

    if write:
        store.refresh_flight_evidence(con, store_root, fid, now=now)
        counters['evidence_refreshed'] += 1


def _api_code(body):
    try:
        doc = json.loads(body.decode('utf-8'))
        return doc.get('code')
    except (ValueError, UnicodeDecodeError, AttributeError):
        return None


def import_land_snapshot(con, pages_dir, run_id, counters, write, label):
    pages = sorted(glob.glob(os.path.join(pages_dir, 'page_*.json')))
    if not pages:
        return None
    nodes = []
    expected = None
    digest = hashlib.sha256()
    for path in pages:
        body = _read(path)
        digest.update(hashlib.sha256(body).digest())
        try:
            doc = json.loads(body.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            counters['land_pages_unreadable'] += 1
            continue
        lands = ((doc.get('data') or {}).get('lands') or {})
        if expected is None and isinstance(lands.get('totalCount'), int):
            expected = lands['totalCount']
        for edge in lands.get('edges') or []:
            node = (edge or {}).get('node')
            if isinstance(node, dict):
                nodes.append(node)
    seen_uuid = set()
    unique_nodes = []
    for node in nodes:
        uuid = node.get('uuid')
        if uuid in seen_uuid:
            continue
        seen_uuid.add(uuid)
        unique_nodes.append(node)
    counters['land_nodes:' + label] = len(unique_nodes)
    counters['land_expected:' + label] = expected if expected is not None else -1
    if not write:
        return None
    captured = _mtime(pages[-1])
    snapshot_id = store.create_land_snapshot(
        con, captured, capture_run_id=run_id,
        scope={'pages_dir': os.path.abspath(pages_dir), 'pages': len(pages),
               'label': label},
        expected_count=expected, is_evidence_import=True)
    for node in unique_nodes:
        try:
            parsed = ev.parse_land_node(node)
        except ValueError:
            counters['land_nodes_invalid'] += 1
            continue
        state, _rid = store.upsert_land_revision(con, snapshot_id, parsed)
        counters['land_revision_' + state] += 1
    complete = (expected is not None and len(unique_nodes) >= expected)
    store.finalize_land_snapshot(con, snapshot_id, len(unique_nodes),
                                 complete, digest.hexdigest())
    return snapshot_id


def import_geometries(con, geometry_dir, counters, write):
    files = sorted(glob.glob(os.path.join(geometry_dir, '*.geometry.json')))
    counters['geometry_files'] = len(files)
    if not write:
        return
    md5_by_uuid = {}
    for row in con.execute('SELECT land_uuid, geometry_md5 FROM '
                           'dji_land_revisions WHERE geometry_md5 IS NOT NULL'):
        md5_by_uuid.setdefault(row['land_uuid'], set()).add(row['geometry_md5'])
    for path in files:
        uuid = os.path.basename(path).split('.')[0]
        body = _read(path)
        md5 = ev.geometry_md5_of(body)
        expected = None
        if md5 in md5_by_uuid.get(uuid, ()):
            expected = md5
        try:
            state, _gid = store.upsert_land_geometry(
                con, body, expected_md5=expected)
        except store.StoreError:
            counters['geometry_md5_mismatch'] += 1
            continue
        counters['geometry_' + state] += 1
        if expected is None:
            counters['geometry_unverified_by_uuid'] += 1
    counters['geometry_verified_by_holder'] = store.verify_geometry_holders(con)


def main(argv=None):
    from collections import Counter
    args = build_parser().parse_args(argv)
    if args.dry_run == args.apply:
        print('ERROR: choose exactly one of --dry-run / --apply')
        return EXIT_USAGE
    if not os.path.exists(args.db_path):
        print('ERROR: database not found at %s - refusing to run.' % args.db_path)
        return EXIT_NO_DATABASE
    if not os.path.isdir(args.root):
        print('ERROR: root directory not found: %s' % args.root)
        return EXIT_USAGE
    write = bool(args.apply)
    counters = Counter()
    run_id = _run_id(args.root)
    con = store.connect(args.db_path)
    try:
        store.require_tables(con)
        listed = flights_from_lists(args.root, set(args.flight_ids)
                                    if args.flight_ids else None)
        fids = sorted(listed)
        if not fids and args.flight_ids:
            fids = sorted(args.flight_ids)
        if args.limit:
            fids = fids[:args.limit]
        print('flights to import: %d (run %s)' % (len(fids), run_id))
        if write:
            store.begin_immediate(con)
        for n, fid in enumerate(fids):
            import_flight_sources(con, args.root, store.source_root(args.db_path),
                                  fid, listed.get(fid), run_id, args.skip_v4,
                                  counters, write)
            if write and n % 500 == 499:
                con.execute('COMMIT')
                store.begin_immediate(con)
                if not args.quiet:
                    print('  ... %d/%d' % (n + 1, len(fids)))
        if not args.skip_lands:
            lands_dir = os.path.join(args.root, 'lands')
            if os.path.isdir(lands_dir):
                import_land_snapshot(con, lands_dir, run_id, counters, write,
                                     os.path.basename(args.root))
            for extra in args.lands_dir:
                import_land_snapshot(con, extra, run_id, counters, write,
                                     os.path.basename(os.path.normpath(extra)))
            if args.geometry_dir:
                import_geometries(con, args.geometry_dir, counters, write)
        if write:
            con.execute('COMMIT')
    except store.StoreError as exc:
        if write:
            con.rollback()
        print('ERROR: %s' % exc)
        return EXIT_USAGE
    finally:
        con.close()
    print('DJI SOURCE IMPORT %s' % ('APPLY' if write else 'DRY-RUN'))
    for key in sorted(counters):
        print('  %-36s %s' % (key, counters[key]))
    if not write:
        print('Nothing was written. Re-run with --apply to store the sources.')
    return EXIT_OK


if __name__ == '__main__':
    sys.exit(main())
