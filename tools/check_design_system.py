#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/check_design_system.py -- pregrada ot entropii dizayn-sistemy.

Zachem
------
Izmereno v P1: 17 odnorazovykh klassov tablic pri 104 tablicakh, 213 cvetov
mimo tokenov, 13 breykpointov, 4 pary tokenov s odinakovym znacheniem, 2249
strok CSS vnutri shablonov, 227 piktogramm v razmetke. Ni odin iz etikh
defektov ne poyavilsya srazu -- oni nakaplivalis inkrement za inkrementom,
potomu chto nichto etogo ne zapreshchalo.

Uzhe byl sluchay, kogda klass defekta opoznali i pochinili odin ekzemplyar,
no proverku ne avtomatizirovali: kommentariy UI-ACCENT-DUP-001 v
design-system.css opisyvaet, kak --vs-info okazalsya raven --vs-primary.
Tot sluchay ispravili -- chetyre ostavshiesya pary dozhili do P1.

Kak ustroeno: HRAPOVIK
---------------------
Chekery, trebuyushchie nulya, na kodovoy baze s dolgom ne rabotayut: oni
padayut vsegda i ikh otklyuchayut. Etot fiksiruet TEKUSHCHIY uroven v
docs/ux/design-system-baseline.json i razreshaet emu tolko UMENSHATSYA.

  - stalo bolshe -> vykhod 1, nazvano, gde imenno;
  - stalo menshe -> vykhod 0 i podskazka obnovit bazu;
  - bez izmeneniy -> vykhod 0.

Bazovyy uroven schitaetsya POFAYLOVO tam, gde eto imeet smysl: inache mozhno
ubrat pyat narusheniy v odnom shablone i dobavit pyat v drugom, i summa ne
izmenitsya.

Chto proveryaetsya
-----------------
  1. table-class    odnorazovyy klass tablicy vmesto vs-table
  2. hardcoded-color cvet hex/rgb v shablone vmesto tokena
  3. breakpoint     znachenie @media vne obyavlennoy shkaly
  4. token-dup      dva tokena s odnim znacheniem (sverka PO ZNACHENIYU)
  5. inline-style   stroki vnutri <style> v shablone
  6. glyph          emodzi/piktogramma v razmetke vmesto ikonki
  7. t-key-missing  t('...') s klyuchom, kotorogo net v translations.py:
                    t vernet sam klyuch, i podpis ostanetsya na yazyke
                    ishodnika v oboih interfeysah
  8. raw-text       kirillicheskiy tekst pryamo v razmetke, mimo t() i mimo
                    vetki `{% if is_ru %}`: cherez slovar on ne prohodit
                    voobshche
  9. raw-scroll     `style="...overflow-x..."` vmesto klassa .vs-table-scroll:
                    takaya oblast prokruchivaetsya myshyu, no s klaviatury v
                    nee ne popast (axe scrollable-region-focusable). Proverka
                    STATICHESKAYA namerenno: axe vidit narushenie, tolko esli
                    tablica deystvitelno perepolnena, a eto zavisit ot dannyh
                    -- iz vosmi takih obertok progon na staging nashel odnu

Pravila proekta, soblyudennye zdes
----------------------------------
  - stdlib, bez Flask: import app vyzval by db.create_all() na importe;
  - vyvod v konsol tolko ASCII (Windows cp1251 v zhurnalakh sluzhby);
  - proverka imeni tokena idet po granice slova s otricatelnym prosmotrom na
    defis: poisk podstrokoy vret, `--vs-r` soderzhitsya v `--vs-r-lg`.

Zapusk:
  python tools/check_design_system.py
  python tools/check_design_system.py --update-baseline
  python tools/check_design_system.py --path templates/fuel
"""

import argparse
import collections
import json
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES = os.path.join(REPO_ROOT, 'templates')
CSS_PATH = os.path.join(REPO_ROOT, 'static', 'css', 'design-system.css')
BASELINE = os.path.join(REPO_ROOT, 'docs', 'ux', 'design-system-baseline.json')

# Obyavlennaya shkala breykpointov. Vsyo ostalnoe -- narushenie.
# [REASON]: znacheniya sovpadayut s matricey vyeportov obhoda
# (docs/ux/40-baseline-crawl-spec.md), chtoby snimki popadali VNUTR vetki
# CSS, a ne na ee granicu.
ALLOWED_BREAKPOINTS = {1280, 1024, 768, 480}

# Klass tablicy, razreshennyy sistemoy: vs-table i ego modifikatory.
TABLE_CLASS_OK = re.compile(r'^vs-table(\s+(is-[a-z0-9-]+|vs-num))*$')

RE_STYLE = re.compile(r'<style[^>]*>(.*?)</style>', re.S | re.I)
RE_TABLE = re.compile(r'<table([^>]*)>', re.I)
RE_CLASS = re.compile(r'class\s*=\s*"([^"]*)"')
RE_MEDIA = re.compile(r'@media[^{]*?\(\s*(?:min|max)-width\s*:\s*(\d+)px\s*\)')
RE_HEX = re.compile(r'#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b')
RE_RGB = re.compile(r'rgba?\(\s*\d+\s*,\s*\d+\s*,\s*\d+')
RE_JINJA_COMMENT = re.compile(r'\{#.*?#\}', re.S)
RE_CSS_COMMENT = re.compile(r'/\*.*?\*/', re.S)
RE_ROOT = re.compile(r':root\s*\{(.*?)\}', re.S)
RE_DECL = re.compile(r'(--[a-z0-9-]+)\s*:\s*([^;]+);')
RE_VAR_REF = re.compile(r'var\(\s*(--[a-z0-9-]+)\s*\)')
RE_GLYPH = re.compile('[\U0001F000-\U0001FAFF☀-➿⬀-⯿]')
# Sobstvennaya oblast prokrutki, sdelannaya inline-stilem vmesto klassa.
# Lovitsya imenno atribut style, a ne <style>-blok: vnutri CSS `overflow-x`
# zakonen i vstrechaetsya v samoy dizayn-sisteme.
RE_RAW_SCROLL = re.compile(r'style\s*=\s*"[^"]*overflow(?:-x)?\s*:\s*(?:auto|scroll)',
                           re.I)

CHECKS = ('table-class', 'hardcoded-color', 'breakpoint',
          'token-dup', 'inline-style', 'glyph', 't-key-missing', 'raw-text',
          'raw-scroll')

# [REASON]: proverka t-key-missing zakryvaet TOLKO vyzovy t(). Stroka,
# napisannaya pryamo v razmetke, ne prohodit cherez slovar voobshche i
# ostaetsya na yazyke ishodnika v OBOIH interfeysah. Progon na staging
# (UI-P7) nashel imenno eto: ekran smeny vremennogo parolya byl celikom
# russkim, a /profile, /wialon i /wialon/report pokazyvali uzbekskiy tekst
# russkomu polzovatelyu. Ni odna sushchestvovavshaya proverka etogo ne videla.
#
# Schitayutsya SLOVA iz treh i bolee kirillicheskih bukv: edinicy ("l", "ga",
# "DT", "sum") yazyka ne imeyut, i vklyuchat ih znachilo by utopit otchet v
# shume. Vetka `{% if is_ru %}...{% endif %}` vyrezaetsya celikom -- eto
# shtatnyy sposob dvuyazychiya v etom proekte, a ne narushenie.
RE_LANG_BRANCH = re.compile(
    r'\{%-?\s*if\s+[^%]*\b(?:is_ru|lang)\b.*?\{%-?\s*endif\s*-?%\}', re.S)
RE_SCRIPT_BLOCK = re.compile(r'<script[^>]*>.*?</script>', re.S | re.I)
RE_JINJA_EXPR = re.compile(r'\{\{.*?\}\}|\{%.*?%\}', re.S)
RE_HTML_TAG = re.compile(r'<[^>]*>', re.S)
RE_CYR_WORD = re.compile(r'[А-яЁёЎў'
                         r'ҚқҒғҲҳ]{3,}')

# [REASON]: t(key) pri otsutствii klyucha v slovare vozvrashchaet SAM KLYUCH.
# Otkaz molchalivyy: v shablone stoit vyzov t(), stranica rendersya bez
# oshibki, a na ekrane ostaetsya ishodnaya stroka -- to est russkiy tekst v
# uzbekskom interfeyse ili naoborot. Naydeno vladelcem na ekrane Vialon
# (shest podpisey), Jinja-parsingom i testami ne lovitsya voobshche.
RE_T_CALL = re.compile(r"""\bt\(\s*(['"])(.*?)\1\s*\)""", re.S)
TRANSLATIONS_PATH = os.path.join(REPO_ROOT, 'translations.py')


def load_translations():
    """Prochitat TRANS bez importa prilozheniya.

    [REASON]: translations.py -- chistye dannye bez importov, no ego nelzya
    razobrat ast.literal_eval: fayl rasshiryaetsya blokami TRANS[...].update().
    Poetomu ispolnyaem imenno ego, v otdelnom prostranstve imen, i nikogda ne
    trogaem app.py -- import app vyzval by db.create_all() na importe.
    """
    if not os.path.isfile(TRANSLATIONS_PATH):
        return None
    namespace = {}
    with open(TRANSLATIONS_PATH, encoding='utf-8') as handle:
        code = compile(handle.read(), TRANSLATIONS_PATH, 'exec')
    exec(code, namespace)  # noqa: S102 - dannye proekta, ne vvod polzovatelya
    trans = namespace.get('TRANS')
    if not isinstance(trans, dict):
        return None
    return trans


def unescape_jinja_literal(text):
    """Privesti tekst literala k tomu, chto uvidit t() vo vremya rendera.

    [REASON]: Jinja razbiraet \\n i \\" vnutri strokovogo literala, poetomu
    klyuch v ISHODNIKE i klyuch v SLOVARE otlichayutsya na eti escape.
    Bez etogo shagi proverka dala by pyat lozhnyh srabatyvaniy na workload.html
    i tri na daily_entry.html -- i ee by otklyuchili.
    """
    return (text.replace('\\n', '\n').replace('\\t', '\t')
                .replace('\\"', '"').replace("\\'", "'"))


def ascii_safe(text):
    return str(text).encode('ascii', 'backslashreplace').decode('ascii')


def read(path):
    with open(path, 'r', encoding='utf-8', errors='replace') as handle:
        return handle.read()


def rel(path):
    return os.path.relpath(path, REPO_ROOT).replace(os.sep, '/')


def norm_color(value):
    value = value.strip().lower()
    if value.startswith('#') and len(value) == 4:
        return '#' + ''.join(ch * 2 for ch in value[1:])
    return re.sub(r'\s+', '', value)


def template_paths(root):
    out = []
    for base, _dirs, files in os.walk(root):
        for name in sorted(files):
            if name.endswith('.html'):
                out.append(os.path.join(base, name))
    return sorted(out)


def scan_templates(paths, trans=None):
    """Poschitat narusheniya pofaylovo. Vozvrashchaet {check: {file: count}}."""
    counts = {check: {} for check in CHECKS}
    detail = collections.defaultdict(list)
    uz = (trans or {}).get('uz', {})
    ru = (trans or {}).get('ru', {})

    for path in paths:
        body = RE_JINJA_COMMENT.sub('', read(path))
        key = rel(path)

        bad_tables = 0
        for attrs in RE_TABLE.findall(body):
            match = RE_CLASS.search(attrs)
            cls = match.group(1).strip() if match else ''
            if not TABLE_CLASS_OK.match(cls):
                bad_tables += 1
                detail['table-class'].append((key, cls or '(no class)'))
        if bad_tables:
            counts['table-class'][key] = bad_tables

        colors = RE_HEX.findall(body) + RE_RGB.findall(body)
        if colors:
            counts['hardcoded-color'][key] = len(colors)
            for c in sorted({norm_color(x) for x in colors}):
                detail['hardcoded-color'].append((key, c))

        bad_bp = [int(v) for v in RE_MEDIA.findall(body)
                  if int(v) not in ALLOWED_BREAKPOINTS]
        if bad_bp:
            counts['breakpoint'][key] = len(bad_bp)
            for v in sorted(set(bad_bp)):
                detail['breakpoint'].append((key, '%dpx' % v))

        style_lines = sum(len(m.splitlines()) for m in RE_STYLE.findall(body))
        if style_lines:
            counts['inline-style'][key] = style_lines

        glyphs = RE_GLYPH.findall(body)
        if glyphs:
            counts['glyph'][key] = len(glyphs)

        raw_scroll = RE_RAW_SCROLL.findall(body)
        if raw_scroll:
            counts['raw-scroll'][key] = len(raw_scroll)
            detail['raw-scroll'].append((key, 'style=... overflow -> .vs-table-scroll'))

        # [REASON]: schitaem tolko kogda slovar deystvitelno prochitan. Pri
        # trans=None pustye uz/ru dali by "vse klyuchi otsutstvuyut" -- to est
        # proverka pokazala by odin i tot zhe otvet pri vernom i nevernom
        # kode, a eto ne proverka.
        if trans:
            missing = 0
            for match in RE_T_CALL.finditer(body):
                literal = unescape_jinja_literal(match.group(2))
                if literal not in uz or literal not in ru:
                    missing += 1
                    detail['t-key-missing'].append((key, literal))
            if missing:
                counts['t-key-missing'][key] = missing

        raw = raw_text_fragments(read(path))
        if raw:
            counts['raw-text'][key] = len(raw)
            for fragment in raw:
                detail['raw-text'].append((key, fragment))

    return counts, detail


def raw_text_fragments(source):
    """Kirillicheskiy tekst, napisannyy pryamo v razmetke.

    Vyrezayutsya po ocheredi: kommentarii, <style>, <script>, vetki
    `{% if is_ru %}...{% endif %}` (shtatnoe dvuyazychie), lyubye vyrazheniya
    Jinja i sami tegi. Chto ostalos -- vidimyy tekst stranicy, kotoryy nikogda
    ne prohodit cherez slovar.
    """
    body = RE_JINJA_COMMENT.sub(' ', source)
    body = RE_STYLE.sub(' ', body)
    body = RE_SCRIPT_BLOCK.sub(' ', body)
    body = RE_LANG_BRANCH.sub(' ', body)
    body = RE_JINJA_EXPR.sub(' ', body)
    body = RE_HTML_TAG.sub('\x00', body)
    out = []
    for chunk in body.split('\x00'):
        text = ' '.join(chunk.split())
        if text and RE_CYR_WORD.search(text):
            out.append(text[:60])
    return out


def scan_css(css_text):
    """Narusheniya v samoy dizayn-sisteme."""
    counts = {}
    detail = collections.defaultdict(list)
    body = RE_CSS_COMMENT.sub('', css_text)
    key = rel(CSS_PATH)

    bad_bp = [int(v) for v in RE_MEDIA.findall(body)
              if int(v) not in ALLOWED_BREAKPOINTS]
    if bad_bp:
        counts['breakpoint'] = {key: len(bad_bp)}
        for v in sorted(set(bad_bp)):
            detail['breakpoint'].append((key, '%dpx' % v))

    # [REASON]: sverka PO ZNACHENIYU, a ne po imeni. Raznye semanticheskie
    # imena mogut ukazyvat na odin cvet, i po imenam eto nevidimo -- imenno
    # tak --vs-bg okazalsya raven --vs-border-3, i granica poverkh fona
    # stranicy perestala byt vidimoy.
    declared = {}
    for block in RE_ROOT.findall(css_text):
        for name, value in RE_DECL.findall(block):
            declared[name] = value.strip()

    # [REASON]: posle perehoda na dva urovnya (DD-023) roli ssylayutsya na
    # palitru cherez var(--p-*), i sravnenie literalnyh znacheniy perestalo
    # by videt dubli VOOBSHCHE -- proverka, nashedshaya --vs-bg = --vs-border-3,
    # molcha prekratila by rabotat. Poetomu ssylki razreshayutsya do konechnogo
    # znacheniya, s zashchitoy ot cikla.
    def resolve(name, depth=0):
        value = declared.get(name, '')
        ref = RE_VAR_REF.fullmatch(value.strip())
        if ref and depth < 8:
            return resolve(ref.group(1), depth + 1)
        return value.strip()

    by_value = collections.defaultdict(list)
    for name, raw in declared.items():
        # Sravnivayutsya tolko ROLI: sovpadenie dvuh znacheniy vnutri samoy
        # palitry ozhidaemo i o nem soobshchat ne nado.
        if name.startswith('--p-'):
            continue
        # [REASON]: psevdonim, obyavlennyy kak var(--vs-*), sovpadaet po
        # znacheniyu PO POSTROENIYU -- imenno v etom rabota sloya
        # sovmestimosti (--info: var(--vs-info) i tak dalee). Soobshchat o nem
        # -- shum, kotoryy pryachet nastoyashchie sovpadeniya: bez etogo
        # otseva ih 15 vmesto odnogo.
        ref = RE_VAR_REF.fullmatch(raw.strip())
        if ref and ref.group(1).startswith('--vs-'):
            continue
        value = resolve(name)
        if RE_HEX.fullmatch(value) or value.lower().startswith('rgb'):
            by_value[norm_color(value)].append(name)
    dups = {v: sorted(names) for v, names in by_value.items() if len(names) > 1}
    if dups:
        counts['token-dup'] = {key: len(dups)}
        for value, names in sorted(dups.items()):
            detail['token-dup'].append((key, '%s = %s' % (value, ', '.join(names))))

    # Cveta mimo tokenov vne bloka :root.
    outside = RE_ROOT.sub('', body)
    colors = RE_HEX.findall(outside) + RE_RGB.findall(outside)
    if colors:
        counts['hardcoded-color'] = {key: len(colors)}

    return counts, detail


def merge(a, b):
    out = {check: dict(a.get(check, {})) for check in CHECKS}
    for check, files in b.items():
        out.setdefault(check, {}).update(files)
    return out


def load_baseline():
    if not os.path.isfile(BASELINE):
        return None
    with open(BASELINE, encoding='utf-8') as handle:
        return json.load(handle)


def save_baseline(counts):
    os.makedirs(os.path.dirname(BASELINE), exist_ok=True)
    payload = {'schema_version': 1,
               'note': 'Ratchet baseline. Counts may only decrease. '
                       'Regenerate with tools/check_design_system.py --update-baseline',
               'counts': {c: dict(sorted(counts.get(c, {}).items())) for c in CHECKS}}
    with open(BASELINE, 'w', encoding='utf-8') as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1, sort_keys=True)
        handle.write('\n')


def compare(current, baseline):
    """Vernut (regressii, uluchsheniya). Regressiya -- lyuboy rost."""
    regressions, improvements = [], []
    for check in CHECKS:
        cur = current.get(check, {})
        base = baseline.get(check, {})
        for key in sorted(set(cur) | set(base)):
            c, b = cur.get(key, 0), base.get(key, 0)
            if c > b:
                regressions.append((check, key, b, c))
            elif c < b:
                improvements.append((check, key, b, c))
    return regressions, improvements


def main():
    parser = argparse.ArgumentParser(
        description='Design-system ratchet: counts may only decrease.')
    parser.add_argument('--path', default=None,
                        help='limit template scan to this directory')
    parser.add_argument('--update-baseline', action='store_true')
    parser.add_argument('--detail', action='store_true',
                        help='print offending values, not only counts')
    args = parser.parse_args()

    if not os.path.isfile(CSS_PATH):
        print('ERROR: design-system.css not found')
        return 2
    root = os.path.join(REPO_ROOT, args.path) if args.path else TEMPLATES
    if not os.path.isdir(root):
        print('ERROR: not a directory: ' + ascii_safe(root))
        return 2

    trans = load_translations()
    if trans is None:
        print('ERROR: translations.py not readable; t-key-missing cannot run')
        return 2
    paths = template_paths(root)
    tpl_counts, tpl_detail = scan_templates(paths, trans)
    css_counts, css_detail = scan_css(read(CSS_PATH))
    current = merge(tpl_counts, css_counts)
    detail = collections.defaultdict(list)
    for source in (tpl_detail, css_detail):
        for check, rows in source.items():
            detail[check].extend(rows)

    totals = {check: sum(current.get(check, {}).values()) for check in CHECKS}
    print('scanned %d templates + design-system.css' % len(paths))
    for check in CHECKS:
        print('  %-16s %5d in %d file(s)' % (
            check, totals[check], len(current.get(check, {}))))

    if args.detail:
        for check in CHECKS:
            rows = detail.get(check, [])
            if not rows:
                continue
            print('\n--- %s ---' % check)
            for key, what in rows[:60]:
                print('  %-46s %s' % (ascii_safe(key), ascii_safe(what)))
            if len(rows) > 60:
                print('  ... %d more' % (len(rows) - 60))

    if args.update_baseline:
        if args.path:
            print('\nERROR: --update-baseline requires a full scan; drop --path')
            return 2
        save_baseline(current)
        print('\nbaseline written: %s' % rel(BASELINE))
        return 0

    baseline = load_baseline()
    if baseline is None:
        print('\nNo baseline yet. Create it with --update-baseline.')
        return 0

    regressions, improvements = compare(current, baseline.get('counts', {}))

    if improvements:
        print('\nIMPROVED (%d):' % len(improvements))
        for check, key, before, after in improvements[:25]:
            print('  %-16s %-44s %d -> %d' % (check, ascii_safe(key), before, after))
        if len(improvements) > 25:
            print('  ... %d more' % (len(improvements) - 25))
        print('  Run --update-baseline to lock the improvement in.')

    if regressions:
        print('\nBLOCKING -- design-system regressions (%d):' % len(regressions))
        for check, key, before, after in regressions:
            print('  %-16s %-44s %d -> %d' % (check, ascii_safe(key), before, after))
        print('\nThe ratchet only turns one way. Either fix the new occurrences,')
        print('or, if the increase is deliberate, say so in the pull request and')
        print('re-run with --update-baseline as a separate commit.')
        return 1

    print('\nOK: no design-system regressions.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
