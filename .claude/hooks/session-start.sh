#!/bin/bash
# Podgotovka sredy i brifing dlya novoy sessii Claude Code.
#
# [REASON]: bez etogo kazhdaya oblachnaya sessiya tratila pervye hody na to,
# chtoby vyyasnit odno i to zhe: postavleny li zavisimosti, otkryt li gate,
# zanyata li ploshchadka, chto stoit na prode. Vse chetyre otveta chitayutsya
# iz repozitoriya za sekundu -- znachit, ih dolzhna davat sreda, a ne agent.
#
# Vyvod tolko ASCII: on popadaet v kontekst agenta i v zhurnaly, gde kodirovka
# ne garantirovana.
set -euo pipefail

cd "${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"

# ---------------------------------------------------------------- zavisimosti
# Stavim TOLKO v udalennoy srede: na mashine razrabotchika svoy venv, i lezt
# v nego skriptom zapuska sessii nelzya.
if [ "${CLAUDE_CODE_REMOTE:-}" = "true" ]; then
  if ! python3 -c 'import flask, jinja2, openpyxl, reportlab' >/dev/null 2>&1; then
    echo "setup   : installing python requirements"
    python3 -m pip install --quiet --disable-pip-version-check -r requirements.txt || {
      echo "setup   : WARNING pip install failed -- tests may not run"; }
  fi
  # Playwright nuzhen tolko instrumentam tools/ux. Brauzery v obraze uzhe est,
  # `playwright install` zapuskat NELZYA.
  if [ -f tools/ux/package.json ] && [ ! -d tools/ux/node_modules ]; then
    echo "setup   : installing tools/ux node packages"
    (cd tools/ux && npm install --no-audit --no-fund --silent) || {
      echo "setup   : WARNING npm install failed -- UX probes unavailable"; }
  fi
fi

# ------------------------------------------------------------------- brifing
python3 - <<'PY'
import io, os, re, subprocess


def sh(*args):
    try:
        return subprocess.run(args, capture_output=True, text=True,
                              timeout=20).stdout.strip()
    except Exception:
        return ''


def read(path):
    try:
        return io.open(path, encoding='utf-8').read()
    except OSError:
        return ''


SEPARATOR = re.compile(r'^\|[\s\-:|]+\|$')


def section(text, anchor):
    """Stroki DANNYH tablicy posle yakorya, bez shapki i razdelitelya.

    [REASON]: yakorem byvaet i zagolovok razdela (`## ...`), i sama stroka
    shapki tablicy. Schitat fiksirovannoe chislo strok nelzya -- imenno na
    etom stroka pro ploshchadku propadala molcha, i brifing vyglyadel
    ispravnym. Poetomu shapka i razdelitel otbrasyvayutsya po vidu.
    """
    i = text.find(anchor)
    if i < 0:
        return []
    body = text[i + len(anchor):]
    end = body.find('\n---')
    body = body[:end] if end > 0 else body
    rows = [l.strip() for l in body.split('\n') if l.strip().startswith('|')]
    rows = [r for r in rows if not SEPARATOR.match(r)]
    if rows and anchor.startswith('#'):
        rows = rows[1:]      # yakor byl zagolovkom -- pervaya stroka eto shapka
    return rows


def cells(row):
    return [c.strip().strip('`') for c in row.split('|')[1:-1]]


branch = sh('git', 'branch', '--show-current') or '(detached)'
head = sh('git', 'rev-parse', '--short', 'HEAD')

gate = section(read('docs/RELEASE_GATE.md'), '## Открытые пункты')
staging = section(read('docs/STAGING.md'), '| Занято | Трек |')
deployed = section(read('docs/DEPLOYED.md'), '## Состояние площадок')

print('')
print('=' * 72)
print('Vehicle Soft -- session briefing (docs/AGENT_SESSION.md)')
print('=' * 72)
print('branch  : %s @ %s' % (branch, head))

if gate:
    tracks = ', '.join(r.split('|')[1].strip() for r in gate)
    print('gate    : %d OPEN item(s) [%s] -- PRODUCTION DEPLOY IS CLOSED'
          % (len(gate), tracks))
else:
    print('gate    : empty -- production deploy is open')

if staging:
    c = cells(staging[0])
    if c and c[0].lower().startswith('да'):
        print('staging : TAKEN by track %s since %s (%s)' % (c[1], c[2], c[3][:48]))
    else:
        print('staging : free')
else:
    print('staging : UNKNOWN -- docs/STAGING.md not parsed, check it by hand')

for row in deployed:
    c = cells(row)
    if c and c[0] == 'production':
        print('prod    : %s %s (registry %s)' % (c[1], c[2], c[4]))

deps = 'ready' if os.system(
    'python3 -c "import flask, jinja2, openpyxl" >/dev/null 2>&1') == 0 else 'MISSING'
ux = 'ready' if os.path.isdir('tools/ux/node_modules') else 'absent'
print('deps    : python %s | tools/ux node %s' % (deps, ux))

print('')
print('Before reporting anything as done, all of these must be clean:')
print('  python -m compileall -q .')
print('  python tools/check_templates.py && python tools/test_check_templates.py')
print('  python tools/check_design_system.py && python tools/test_check_design_system.py')
print('  python -m unittest discover -s tests')
print('')
print('Rules: CLAUDE.md (charter) -> docs/AGENT_SESSION.md (how a session works)')
print('       -> docs/tracks/<track>.md (state of the track you were given)')
print('=' * 72)
print('')
PY
