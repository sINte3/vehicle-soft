#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/verify_drone_ui_fix_002.py -- the cross-cutting gate X-1..X-5.

Run once, at the end, against the commit this task started from:

  python tools/verify_drone_ui_fix_002.py --base c1dbf5a

  X-1  every drones.py function this task does not name has the SAME AST
       hash as at the base -- all ten *_xlsx functions, _drone_works_totals,
       _drone_work_month_expr, _drone_work_outstanding_expr,
       drone_group_number. Compared on the AST, not the text, so a comment
       or a reflow cannot mask a change and cannot raise a false alarm.
  X-2  every touched Jinja template still parses, and its <div> balance is
       unchanged. A stray </div> is invisible in a diff and in a screenshot.
  X-3  no inline event handler in a touched template receives a |tojson value
       inside a DOUBLE-quoted attribute. Found three times in this project;
       the filter escapes < > & and the apostrophe but NOT the double quote,
       so the parser ends the attribute early and the handler silently never
       binds.
  X-4  every Uzbek string added by this task is Cyrillic by code point, with
       a negative control proving the scanner fires on planted Latin.
  X-5  every CSS class used by the touched templates exists in
       design-system.css.

Exit code 1 on any failure. Console output is ASCII only.
"""
import argparse
import ast
import hashlib
import os
import re
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# [REASON]: named one by one rather than discovered, so that a function
# RENAMED out of the list cannot silently stop being checked.
PINNED = (
    '_drone_xlsx_safe', '_drone_xlsx_rate', '_drone_xlsx_styler',
    '_drone_xlsx_response', 'summary_xlsx', 'flights_xlsx',
    '_drone_works_xlsx_response', 'works_reports_xlsx', 'works_debt_xlsx',
    'works_xlsx',
    '_drone_works_totals', '_drone_work_month_expr',
    '_drone_work_outstanding_expr', 'drone_group_number',
)

TOUCHED_TEMPLATES = (
    'templates/drones/works.html',
    'templates/drones/works_reports.html',
    'templates/drones/works_debts.html',
    'templates/drones/summary.html',
)

CSS_PATH = 'static/css/design-system.css'

# Latin is allowed only in product and brand names -- CLAUDE.md.
ALLOWED_LATIN = ('Excel', 'DJI', 'RFID', 'Topaz', 'PDF')

failures = []


def fail(check, message):
    failures.append('%s: %s' % (check, message))
    print('  FAIL %s -- %s' % (check, message))


def show(base, path):
    return subprocess.check_output(['git', 'show', '%s:%s' % (base, path)],
                                   cwd=REPO_ROOT).decode('utf-8')


def read(path):
    with open(os.path.join(REPO_ROOT, path), encoding='utf-8') as handle:
        return handle.read()


def function_hashes(source, names):
    """{name: sha1 of the function's AST dump}. Missing names are reported."""
    tree = ast.parse(source)
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in names:
                dumped = ast.dump(node, annotate_fields=True,
                                  include_attributes=False)
                found[node.name] = hashlib.sha1(
                    dumped.encode('utf-8')).hexdigest()
    return found


def check_x1(base):
    print('X-1  AST hashes of the functions this task does not name')
    before = function_hashes(show(base, 'drones.py'), set(PINNED))
    after = function_hashes(read('drones.py'), set(PINNED))
    for name in PINNED:
        if name not in before:
            fail('X-1', '%s not found at %s' % (name, base))
        elif name not in after:
            fail('X-1', '%s no longer exists' % name)
        elif before[name] != after[name]:
            fail('X-1', '%s CHANGED (%s -> %s)'
                 % (name, before[name][:12], after[name][:12]))
    checked = len([n for n in PINNED if n in before and n in after])
    print('     %d/%d functions compared, %d differ'
          % (checked, len(PINNED),
             len([f for f in failures if f.startswith('X-1')])))


# Jinja comments are stripped first: {# ... #} may legitimately hold markup.
JINJA_COMMENT = re.compile(r'\{#.*?#\}', re.S)


def div_balance(source):
    stripped = JINJA_COMMENT.sub('', source)
    return stripped.count('<div'), stripped.count('</div>')


def check_x2(base):
    print('X-2  templates parse and their <div> balance is unchanged')
    try:
        import jinja2
    except ImportError:
        fail('X-2', 'jinja2 not installed')
        return
    env = jinja2.Environment()
    for path in TOUCHED_TEMPLATES:
        source = read(path)
        try:
            env.parse(source)
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            fail('X-2', '%s does not parse: %s' % (path, exc))
            continue
        opened, closed = div_balance(source)
        was_open, was_closed = div_balance(show(base, path))
        if opened != closed:
            fail('X-2', '%s unbalanced: %d <div> vs %d </div>'
                 % (path, opened, closed))
        elif was_open != was_closed:
            fail('X-2', '%s was ALREADY unbalanced at %s' % (path, base))
        else:
            print('     ok   %-40s %d pairs (was %d)'
                  % (path, opened, was_open))


# The defect class, exactly: a handler attribute opened with a DOUBLE quote
# whose value reaches a tojson filter before the attribute closes.
TOJSON_IN_DQ_ATTR = re.compile(r'\bon[a-z]+\s*=\s*"[^"]*\|\s*tojson')


def check_x3():
    print('X-3  no |tojson inside a double-quoted inline handler')
    for path in TOUCHED_TEMPLATES:
        source = read(path)
        hits = TOJSON_IN_DQ_ATTR.findall(source)
        if hits:
            fail('X-3', '%s: %d occurrence(s)' % (path, len(hits)))
        else:
            print('     ok   %s' % path)
    # The control: the scanner must fire on the defect it claims to find.
    planted = '<button onclick="go(  {{ rows|tojson }} )">x</button>'
    if not TOJSON_IN_DQ_ATTR.search(planted):
        fail('X-3', 'the scanner does not fire on a planted defect')
    else:
        print('     ok   scanner fires on a planted double-quoted tojson')


UZ_HALF = re.compile(r"if is_ru else '((?:[^'\\]|\\.)*)'")
CYRILLIC = re.compile(r'[Ѐ-ӿ]')


def latin_runs(text):
    for word in ALLOWED_LATIN:
        text = text.replace(word, ' ')
    return re.findall(r'[A-Za-z]+', text)


def check_x4(base):
    print('X-4  Uzbek strings added by this task are Cyrillic by code point')
    scanned = 0
    for path in TOUCHED_TEMPLATES:
        before = set(UZ_HALF.findall(show(base, path)))
        for text in UZ_HALF.findall(read(path)):
            if text in before:
                continue
            scanned += 1
            runs = latin_runs(text)
            if runs:
                fail('X-4', '%s: Latin in %r -> %s' % (path, text, runs))
    # The Uzbek half of the drones.py labels this task touched.
    for uz in ('Буюртмачи кўрсатилмаган', 'Буюртмачи аниқланмаган',
               'сумма кўрсатилмаган'):
        scanned += 1
        if not CYRILLIC.search(uz) or latin_runs(uz):
            fail('X-4', 'not Cyrillic: %r' % uz)
    if scanned == 0:
        fail('X-4', 'nothing scanned -- a scan over an empty list proves '
                    'nothing')
    else:
        print('     %d new Uzbek string(s) scanned' % scanned)
    # The control.
    if latin_runs('Буюртмачи aniqlanmagan'):
        print('     ok   scanner fires on a planted Latin string')
    else:
        fail('X-4', 'the scanner does not fire on planted Latin')


CLASS_ATTR = re.compile(r'class="([^"{}]*)"')


def check_x5():
    print('X-5  every CSS class used by the touched templates is defined')
    css = read(CSS_PATH)
    defined = set(re.findall(r'\.([A-Za-z][\w-]*)', css))
    missing = {}
    for path in TOUCHED_TEMPLATES:
        for attr in CLASS_ATTR.findall(read(path)):
            for name in attr.split():
                if not name.startswith('vs-') and not name.startswith('is-'):
                    continue
                if name not in defined:
                    missing.setdefault(path, set()).add(name)
    if missing:
        for path, names in sorted(missing.items()):
            fail('X-5', '%s uses undefined %s' % (path, sorted(names)))
    else:
        print('     ok   0 missing across %d template(s)'
              % len(TOUCHED_TEMPLATES))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True,
                        help='the commit this task started from')
    args = parser.parse_args()

    print('DRONE-UI-FIX-002 cross-cutting gate, base=%s' % args.base)
    print('')
    check_x1(args.base)
    check_x2(args.base)
    check_x3()
    check_x4(args.base)
    check_x5()
    print('')
    if failures:
        print('RESULT: FAIL -- %d finding(s)' % len(failures))
        return 1
    print('RESULT: PASS -- X-1..X-5 all clean')
    return 0


if __name__ == '__main__':
    sys.exit(main())
