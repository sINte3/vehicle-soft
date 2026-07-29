#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/check_templates.py -- standalone checker for the four template
defect classes this project has actually been bitten by.

Checks every file under <repo>/templates/**/*.html (or one file via --path):

  1. jinja-parse     BLOCKING  jinja2.Environment().parse(source) raises.
  2. div-balance     BLOCKING  count of '<div' differs from count of
                               '</div>'. Occurrences inside {# ... #}
                               comments are ignored.
  3. tojson-attr     BLOCKING  '|tojson' inside a double-quoted attribute
                               value. The tojson filter escapes < > & and
                               the apostrophe but NOT the double quote, and
                               it returns Markup, so autoescaping does not
                               apply to its output: the HTML parser
                               terminates the attribute at the first quote
                               coming from tojson and the handler silently
                               fails to bind. Use single-quoted attributes
                               or data-* attributes instead.
  4. set-before-use  WARNING   a name bound by {% set NAME %} is used inside
                               {{ ... }} on a line strictly before the set.
                               Before its declaration the name renders as a
                               silent empty string; py_compile and importing
                               the module do not catch it. The detection is
                               heuristic, so this check NEVER blocks.
                               Suppress one line with the inline comment
                               {# noqa: set-before-use #}.

Exit code: 1 when at least one BLOCKING finding exists, 0 otherwise
(warnings alone never fail the run), 2 on usage errors.

Rules honoured here:
  - stdlib + jinja2 only; no Flask import. Importing app runs create_app(),
    which calls db.create_all() -- a checker must never be a writer.
  - console output is ASCII only (NSSM/Windows cp1251 log safety); any
    non-ASCII text from a template is backslash-escaped before printing.

Usage:
  python tools/check_templates.py
  python tools/check_templates.py --path templates/report.html
"""

import argparse
import collections
import os
import re
import sys

try:
    import jinja2
except ImportError:  # pragma: no cover - CI installs jinja2 explicitly
    print('ERROR: jinja2 is required to run this checker (pip install jinja2).')
    sys.exit(2)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES_DIR = os.path.join(REPO_ROOT, 'templates')

BLOCKING = 'BLOCKING'
WARNING = 'WARNING'

NOQA_SET_BEFORE_USE = 'noqa: set-before-use'

Finding = collections.namedtuple(
    'Finding', ['path', 'line', 'severity', 'check', 'message'])

_COMMENT_RE = re.compile(r'\{#.*?#\}', re.S)
_OPEN_DIV_RE = re.compile(r'<div(?=[\s>/])', re.I)
_CLOSE_DIV_RE = re.compile(r'</div\s*>', re.I)
# [REASON]: the minimum pattern required by the task spec -- an attribute
# value opened with a double quote whose content mentions tojson before the
# closing double quote. [^"] deliberately matches newlines so multi-line
# attribute values are caught too.
_TOJSON_ATTR_RE = re.compile(r'=\s*"[^"]*tojson[^"]*"')
_EXPR_RE = re.compile(r'\{\{.*?\}\}', re.S)
_STRING_LITERAL_RE = re.compile(r"'[^']*'|\"[^\"]*\"", re.S)
# {% set NAME = ... %} and the block form {% set NAME %}...{% endset %}.
# The lookahead excludes attribute targets such as {% set ns.attr = 1 %}.
_SET_DECL_RE = re.compile(
    r'\{%-?\s*set\s+'
    r'([A-Za-z_][A-Za-z0-9_]*(?:\s*,\s*[A-Za-z_][A-Za-z0-9_]*)*)'
    r'\s*(?=[=%-])')
_FOR_DECL_RE = re.compile(r'\{%-?\s*for\s+(.+?)\s+in\s', re.S)
_MACRO_DECL_RE = re.compile(
    r'\{%-?\s*macro\s+([A-Za-z_][A-Za-z0-9_]*)\s*\((.*?)\)', re.S)
_WITH_DECL_RE = re.compile(r'\{%-?\s*with\s+(.*?)%\}', re.S)
_IDENT_RE = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')


def _ascii(text):
    """Return text with every non-ASCII character backslash-escaped."""
    return str(text).encode('ascii', 'backslashreplace').decode('ascii')


def _strip_comments(source):
    """Blank {# ... #} comments while preserving line numbers."""
    return _COMMENT_RE.sub(lambda m: '\n' * m.group(0).count('\n'), source)


def _line_of(text, pos):
    return text.count('\n', 0, pos) + 1


def _check_parse(path, source):
    env = jinja2.Environment()
    try:
        env.parse(source)
    except jinja2.TemplateSyntaxError as exc:
        return [Finding(path, exc.lineno or 0, BLOCKING, 'jinja-parse',
                        _ascii(exc.message or exc))]
    except Exception as exc:  # defensive: any parser failure is a finding
        return [Finding(path, 0, BLOCKING, 'jinja-parse', _ascii(exc))]
    return []


def _check_divs(path, stripped):
    opens = len(_OPEN_DIV_RE.findall(stripped))
    closes = len(_CLOSE_DIV_RE.findall(stripped))
    if opens != closes:
        return [Finding(path, 0, BLOCKING, 'div-balance',
                        'unbalanced <div>: %d opening vs %d closing '
                        '(jinja comments ignored)' % (opens, closes))]
    return []


def _check_tojson(path, stripped):
    findings = []
    for m in _TOJSON_ATTR_RE.finditer(stripped):
        snippet = m.group(0)
        if len(snippet) > 70:
            snippet = snippet[:67] + '...'
        findings.append(Finding(
            path, _line_of(stripped, m.start()), BLOCKING, 'tojson-attr',
            'tojson inside a double-quoted attribute value: %s'
            % _ascii(snippet)))
    return findings


def _blank_string_literals(expr):
    """Blank quoted literals inside an expression, preserving offsets."""
    return _STRING_LITERAL_RE.sub(
        lambda m: re.sub(r'[^\n]', ' ', m.group(0)), expr)


def _check_set_before_use(path, source, stripped):
    noqa_lines = set()
    for lineno, line in enumerate(source.splitlines(), start=1):
        if NOQA_SET_BEFORE_USE in line:
            noqa_lines.add(lineno)

    declared = {}
    for m in _SET_DECL_RE.finditer(stripped):
        for name in re.split(r'\s*,\s*', m.group(1)):
            if name and name not in declared:
                declared[name] = _line_of(stripped, m.start())

    # Names bound by {% for %}, {% macro %} (name and parameters) and
    # {% with %} are legitimate before any later {% set %} -- exclude them.
    excluded = set()
    for m in _FOR_DECL_RE.finditer(stripped):
        excluded.update(_IDENT_RE.findall(m.group(1)))
    for m in _MACRO_DECL_RE.finditer(stripped):
        excluded.add(m.group(1))
        excluded.update(_IDENT_RE.findall(m.group(2)))
    for m in _WITH_DECL_RE.finditer(stripped):
        excluded.update(
            re.findall(r'([A-Za-z_][A-Za-z0-9_]*)\s*=', m.group(1)))

    tracked = {name: line for name, line in declared.items()
               if name not in excluded}
    if not tracked:
        return []

    findings = []
    seen = set()
    for m in _EXPR_RE.finditer(stripped):
        body = _blank_string_literals(m.group(0))
        for name, decl_line in tracked.items():
            for use in re.finditer(r'(?<![\w.])%s\b' % re.escape(name), body):
                use_line = _line_of(stripped, m.start() + use.start())
                if use_line >= decl_line:
                    continue  # only strictly-before uses are suspicious
                if use_line in noqa_lines:
                    continue
                key = (name, use_line)
                if key in seen:
                    continue
                seen.add(key)
                findings.append(Finding(
                    path, use_line, WARNING, 'set-before-use',
                    "'%s' is used here but its {%% set %%} is on line %d; "
                    'before the set it renders as an empty string'
                    % (name, decl_line)))
    return sorted(findings, key=lambda f: f.line)


def check_file(path):
    """Run all four checks on one template file. Returns a list of Finding."""
    with open(path, 'r', encoding='utf-8', errors='replace') as fh:
        source = fh.read()
    stripped = _strip_comments(source)
    findings = []
    findings += _check_parse(path, source)
    findings += _check_divs(path, stripped)
    findings += _check_tojson(path, stripped)
    findings += _check_set_before_use(path, source, stripped)
    return findings


def iter_template_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for filename in sorted(filenames):
            if filename.endswith('.html'):
                yield os.path.join(dirpath, filename)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Check templates for the four known defect classes.')
    parser.add_argument(
        '--path',
        help='check a single template file instead of walking templates/')
    args = parser.parse_args(argv)

    if args.path:
        if not os.path.isfile(args.path):
            print('ERROR: no such file: %s' % _ascii(args.path))
            return 2
        files = [args.path]
    else:
        if not os.path.isdir(TEMPLATES_DIR):
            print('ERROR: templates directory not found at %s'
                  % _ascii(TEMPLATES_DIR))
            return 2
        files = list(iter_template_files(TEMPLATES_DIR))

    blocking = 0
    warnings = 0
    for path in files:
        for finding in check_file(path):
            rel = os.path.relpath(finding.path, REPO_ROOT)
            print('%s:%d: %s %s: %s' % (_ascii(rel), finding.line,
                                        finding.severity, finding.check,
                                        finding.message))
            if finding.severity == BLOCKING:
                blocking += 1
            else:
                warnings += 1

    print('Summary: %d file(s) checked, %d blocking finding(s), '
          '%d warning(s).' % (len(files), blocking, warnings))
    return 1 if blocking else 0


if __name__ == '__main__':
    sys.exit(main())
