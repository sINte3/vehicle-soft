#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/ux/inventory.py -- izmerimyy inventar frontend-sloya Vehicle Soft.

Chitatel. Razbiraet fayly, NE importiruet app: `app = create_app()` vyzyvaet
db.create_all() na importe, poetomu lyuboy skript s `from app import app` --
pisatel, a diagnostika pisatelem byt ne dolzhna (pravilo proekta).

Sobiraet:

  1. TOKENS       -- vse --vs-* iz :root: obyavleny, skolko raz ispolzuyutsya
                     v CSS i v shablonakh, i kakie ne ispolzuyutsya nigde.
  2. COLOR VALUES -- gruppirovka tokenov PO ZNACHENIYU, ne po imeni. Raznye
                     semanticheskie imena mogut ukazyvat na odin cvet -- eto
                     vidno tolko po znacheniyu (pravilo proekta).
  3. HARDCODED    -- cveta, zapisannye hex/rgb pryamo v shablonakh i v CSS vne
                     bloka :root, to est mimo tokenov.
  4. CLASSES      -- chastota vs-* klassov; otdelno klassy tablic.
  5. INLINE       -- stroki <style> i <script> vnutri shablonov.
  6. GLYPHS       -- emodzi i piktogrammy v razmetke.
  7. BREAKPOINTS  -- vse znacheniya @media po vsem faylam.
  8. ADOPTION     -- kategoriya kazhdogo shablona po otnosheniyu k sisteme.

Determinizm: vyvod otsortirovan, vremennykh metok net. Dva zapuska podryad na
odnom kommite dayut pobaytovo odinakovyy JSON -- eto kriteriy vykhoda fazy P1.

Ispolzovanie proverki po granice slova: `L_initial` sovpadaet s
`L_initial_missing` pri poiske podstrokoy, poetomu vezde \\b i otricatelnyy
prosmotr na `-`/`_` (pravilo proekta).

Zapusk:
  python tools/ux/inventory.py
  python tools/ux/inventory.py --out docs/ux/10-repo-inventory.json
"""

import argparse
import collections
import json
import os
import re
import sys

# [REASON]: skript lezhit v tools/ux/, poetomu do kornya repozitoriya TRI
# urovnya, a ne dva kak u skriptov v tools/. Oshibka v odin uroven daet
# "design-system.css not found" pri sushchestvuyushchem fayle.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TEMPLATES = os.path.join(REPO_ROOT, 'templates')
CSS_PATH = os.path.join(REPO_ROOT, 'static', 'css', 'design-system.css')

# --- regexes -----------------------------------------------------------------
RE_ROOT_BLOCK = re.compile(r':root\s*\{(.*?)\}', re.S)
RE_DECL = re.compile(r'(--[a-z0-9-]+)\s*:\s*([^;]+);')
RE_STYLE = re.compile(r'<style[^>]*>(.*?)</style>', re.S | re.I)
RE_SCRIPT = re.compile(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', re.S | re.I)
RE_TABLE = re.compile(r'<table([^>]*)>', re.I)
RE_CLASS_ATTR = re.compile(r'class\s*=\s*"([^"]*)"')
# [REASON]: otricatelnyy prosmotr nazad na `-` obyazatelen. Bez nego `\b`
# srabatyvaet vnutri `var(--vs-fs-xl)` mezhdu defisom i `v`, i imena tokenov
# popadayut v spisok klassov -- schetchik razdvaetsya pochti na chislo tokenov.
# Eto tot samyy klass oshibki, o kotorom preduprezhdaet CLAUDE.md: proverka
# podstrokoy vret.
RE_VS_CLASS = re.compile(r'(?<!-)\bvs-[a-z0-9]+(?:-[a-z0-9]+)*\b')
RE_MEDIA = re.compile(r'@media[^{]*?\(\s*(min|max)-width\s*:\s*(\d+)px\s*\)')
RE_HEX = re.compile(r'#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})\b')
RE_RGB = re.compile(r'rgba?\(\s*\d+\s*,\s*\d+\s*,\s*\d+')
RE_COMMENT_CSS = re.compile(r'/\*.*?\*/', re.S)
RE_JINJA_COMMENT = re.compile(r'\{#.*?#\}', re.S)
# Piktogrammy i emodzi. Strelki (2190-21FF) uchityvayutsya otdelno: chast iz
# nikh -- tipografskiy znak, a ne ikonka, i zamena u nikh raznaya.
RE_EMOJI = re.compile(
    '[\U0001F000-\U0001FAFF☀-➿⬀-⯿️←-⇿]')
RE_ARROW = re.compile('[←-⇿]')

NAMED_COLORS = {'white', 'black', 'transparent', 'inherit', 'currentcolor', 'none'}


def read(path):
    with open(path, 'r', encoding='utf-8', errors='replace') as handle:
        return handle.read()


def rel(path):
    return os.path.relpath(path, REPO_ROOT).replace(os.sep, '/')


def template_paths():
    out = []
    for root, _dirs, files in os.walk(TEMPLATES):
        for name in files:
            if name.endswith('.html'):
                out.append(os.path.join(root, name))
    return sorted(out)


def word_uses(name, text):
    """Skolko raz imya vstrechaetsya kak CELOE imya.

    [REASON]: poisk podstrokoy vret -- `--vs-r` soderzhitsya v `--vs-r-lg`.
    Otricatelnyy prosmotr na `-` posle imeni otsekaet takie sovpadeniya.
    """
    return len(re.findall(re.escape(name) + r'(?![a-z0-9-])', text))


def norm_color(value):
    """Privesti cvet k sravnimomu vidu. 3-znachnyy hex razvorachivaetsya."""
    value = value.strip().lower()
    if value.startswith('#') and len(value) == 4:
        return '#' + ''.join(ch * 2 for ch in value[1:])
    return re.sub(r'\s+', '', value)


def collect_tokens(css, templates_blob):
    root_blocks = RE_ROOT_BLOCK.findall(css)
    declared = {}
    for block in root_blocks:
        for name, value in RE_DECL.findall(block):
            declared[name] = value.strip()

    css_body = RE_COMMENT_CSS.sub('', css)
    tokens = {}
    for name, value in sorted(declared.items()):
        in_css = word_uses(name, css_body) - 1  # minus sobstvennoe obyavlenie
        in_tpl = word_uses(name, templates_blob)
        tokens[name] = {
            'value': value,
            'uses_in_css': max(in_css, 0),
            'uses_in_templates': in_tpl,
        }
    return tokens


def color_collisions(tokens):
    """Tokeny, ukazyvayushchie na odno znachenie. Sravnenie PO ZNACHENIYU."""
    by_value = collections.defaultdict(list)
    for name, info in tokens.items():
        value = info['value']
        if RE_HEX.fullmatch(value.strip()) or value.strip().lower().startswith('rgb'):
            by_value[norm_color(value)].append(name)
    return {v: sorted(names) for v, names in sorted(by_value.items())
            if len(names) > 1}


def hardcoded_colors(css, tpl_files):
    """Cveta mimo tokenov: v shablonakh i v CSS vne :root."""
    css_wo_comments = RE_COMMENT_CSS.sub('', css)
    css_wo_root = RE_ROOT_BLOCK.sub('', css_wo_comments)
    css_hits = collections.Counter(
        norm_color(c) for c in RE_HEX.findall(css_wo_root))
    css_hits.update(norm_color(c) + ')' for c in RE_RGB.findall(css_wo_root))

    tpl_hits = collections.Counter()
    per_file = {}
    for path in tpl_files:
        body = RE_JINJA_COMMENT.sub('', read(path))
        found = [norm_color(c) for c in RE_HEX.findall(body)]
        found += [norm_color(c) + ')' for c in RE_RGB.findall(body)]
        if found:
            per_file[rel(path)] = len(found)
            tpl_hits.update(found)
    return {
        'in_css_outside_root_total': sum(css_hits.values()),
        'in_css_outside_root_distinct': len(css_hits),
        'in_css_top': dict(sorted(css_hits.most_common(15))),
        'in_templates_total': sum(tpl_hits.values()),
        'in_templates_distinct': len(tpl_hits),
        'in_templates_top': dict(sorted(tpl_hits.most_common(15))),
        'in_templates_per_file': dict(sorted(per_file.items())),
    }


def classify(path, body, vs_classes, css_vars, ux_vars, legacy_vars):
    if vs_classes >= 20:
        return 'A_components'
    if vs_classes > 0 or css_vars > 0:
        return 'B_mixed'
    if ux_vars > 0:
        return 'C_ux_island'
    if legacy_vars > 0:
        return 'D_legacy_vars'
    return 'E_no_signal'


def main():
    parser = argparse.ArgumentParser(description='Vehicle Soft frontend inventory.')
    parser.add_argument('--out', default='docs/ux/10-repo-inventory.json')
    args = parser.parse_args()

    if not os.path.isfile(CSS_PATH):
        print('ERROR: design-system.css not found')
        return 2

    css = read(CSS_PATH)
    tpl_files = template_paths()
    if not tpl_files:
        print('ERROR: no templates found')
        return 2

    bodies = {path: read(path) for path in tpl_files}
    blob = '\n'.join(bodies.values())

    tokens = collect_tokens(css, blob)
    unused = sorted(n for n, i in tokens.items()
                    if i['uses_in_css'] == 0 and i['uses_in_templates'] == 0)

    vs_class_freq = collections.Counter(RE_VS_CLASS.findall(css))
    vs_class_freq.update(RE_VS_CLASS.findall(blob))

    table_classes = collections.Counter()
    inline_css = inline_js = 0
    glyphs = collections.Counter()
    arrows = 0
    per_template = {}
    breakpoints = collections.Counter()

    for path, body in bodies.items():
        clean = RE_JINJA_COMMENT.sub('', body)
        st = sum(len(m.splitlines()) for m in RE_STYLE.findall(clean))
        sc = sum(len(m.splitlines()) for m in RE_SCRIPT.findall(clean))
        inline_css += st
        inline_js += sc

        for attrs in RE_TABLE.findall(clean):
            match = RE_CLASS_ATTR.search(attrs)
            table_classes[match.group(1).strip() if match else '(no class)'] += 1

        found = RE_EMOJI.findall(clean)
        glyphs.update(found)
        arrows += len(RE_ARROW.findall(clean))

        for _kind, value in RE_MEDIA.findall(clean):
            breakpoints[int(value)] += 1

        vs_cls = len(RE_VS_CLASS.findall(clean))
        vs_var = len(re.findall(r'--vs-[a-z0-9-]+', clean))
        ux_var = len(re.findall(r'--ux-[a-z0-9-]+', clean))
        legacy = len(re.findall(
            r'--(?:surface|border|text2|text-muted|muted|bg)(?![a-z0-9-])', clean))
        per_template[rel(path)] = {
            'lines': len(body.splitlines()),
            'inline_css_lines': st,
            'inline_js_lines': sc,
            'vs_classes': vs_cls,
            'vs_vars': vs_var,
            'ux_vars': ux_var,
            'legacy_vars': legacy,
            'glyphs': len(found),
            'category': classify(path, clean, vs_cls, vs_var, ux_var, legacy),
        }

    for _kind, value in RE_MEDIA.findall(RE_COMMENT_CSS.sub('', css)):
        breakpoints[int(value)] += 1

    # Odnorazovye klassy tablic: vse, krome semeystva vs-table i pustogo.
    oneoff = {c: n for c, n in table_classes.items()
              if c != '(no class)' and not c.startswith('vs-table')}

    categories = collections.Counter(v['category'] for v in per_template.values())

    report = {
        'schema_version': 1,
        'templates': {
            'count': len(tpl_files),
            'total_lines': sum(v['lines'] for v in per_template.values()),
            'inline_css_lines': inline_css,
            'inline_js_lines': inline_js,
            'per_template': dict(sorted(per_template.items())),
        },
        'design_system_css': {
            'lines': len(css.splitlines()),
            'tokens_declared': len(tokens),
            'tokens_unused': unused,
            'tokens': dict(sorted(tokens.items())),
            'duplicate_color_values': color_collisions(tokens),
        },
        'adoption': dict(sorted(categories.items())),
        'classes': {
            'vs_class_distinct': len(vs_class_freq),
            'vs_class_freq': dict(sorted(vs_class_freq.items())),
        },
        'tables': {
            'total': sum(table_classes.values()),
            'by_class': dict(sorted(table_classes.items())),
            'oneoff_classes': dict(sorted(oneoff.items())),
            'oneoff_count': len(oneoff),
        },
        'glyphs': {
            'total': sum(glyphs.values()),
            'distinct': len(glyphs),
            'arrows_subset': arrows,
            'by_glyph': dict(sorted(glyphs.items(), key=lambda kv: (-kv[1], kv[0]))),
        },
        'breakpoints': dict(sorted(breakpoints.items())),
        'hardcoded_colors': hardcoded_colors(css, tpl_files),
    }

    out_path = os.path.join(REPO_ROOT, args.out) if not os.path.isabs(args.out) else args.out
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=1, sort_keys=True)
        handle.write('\n')

    # Konsol -- tolko ASCII.
    print('templates              : %d (%d lines)' % (
        report['templates']['count'], report['templates']['total_lines']))
    print('inline css / js lines  : %d / %d' % (inline_css, inline_js))
    print('design-system.css      : %d lines, %d tokens, %d unused' % (
        report['design_system_css']['lines'], len(tokens), len(unused)))
    print('duplicate color values : %d' % len(report['design_system_css']['duplicate_color_values']))
    print('vs-* classes distinct  : %d' % report['classes']['vs_class_distinct'])
    print('tables                 : %d total, %d one-off classes' % (
        report['tables']['total'], report['tables']['oneoff_count']))
    print('glyphs in markup       : %d (%d distinct, %d arrows)' % (
        report['glyphs']['total'], report['glyphs']['distinct'], arrows))
    print('breakpoints distinct   : %d' % len(report['breakpoints']))
    print('hardcoded colors       : %d in css outside :root, %d in templates' % (
        report['hardcoded_colors']['in_css_outside_root_total'],
        report['hardcoded_colors']['in_templates_total']))
    print('adoption               : ' + ', '.join(
        '%s=%d' % (k, v) for k, v in sorted(categories.items())))
    print('written: %s' % rel(out_path))
    return 0


if __name__ == '__main__':
    sys.exit(main())
