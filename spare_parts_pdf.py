# -*- coding: utf-8 -*-
"""Write-off act PDF generation (SPARE-STAGE2, Task 5).

Kept out of spare_parts.py so the reportlab dependency and layout code do not
bloat the route module.

[REASON]: reportlab chosen after the Task-0 smoke test (installs as a pure
Python wheel; its pillow/charset-normalizer dependencies ship prebuilt
Windows cp314 wheels, so nothing compiles on the production Python 3.14).
Fonts are the bundled DejaVu Sans files under static/fonts/ — full Cyrillic
coverage including the Uzbek letters ў қ ғ ҳ — because relying on a Windows
system font being present is exactly the kind of environment-dependent
assumption that breaks remotely-debugged deployments.
"""

import os
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_FONT_DIR = os.path.join(_MODULE_DIR, 'static', 'fonts')

_fonts_registered = False


def _register_fonts():
    """Register the bundled DejaVu fonts once per process."""
    global _fonts_registered
    if _fonts_registered:
        return
    pdfmetrics.registerFont(
        TTFont('DejaVuSans', os.path.join(_FONT_DIR, 'DejaVuSans.ttf')))
    pdfmetrics.registerFont(
        TTFont('DejaVuSans-Bold', os.path.join(_FONT_DIR, 'DejaVuSans-Bold.ttf')))
    _fonts_registered = True


def _fmt_money(value):
    """1234567.0 -> '1 234 567' (same style as the fmt_sum Jinja filter)."""
    if value is None:
        return '—'
    return '{:,.0f}'.format(float(value)).replace(',', ' ')


def _fmt_qty(value):
    """Trim trailing zeros: 3.0 -> '3', 2.5 -> '2.5'."""
    if value is None:
        return '—'
    value = float(value)
    if value == int(value):
        return str(int(value))
    return ('{:.3f}'.format(value)).rstrip('0').rstrip('.')


# Single-language act (internal accounting document): whichever language the
# issuing user had active at generation time is used for the whole document.
_L = {
    'ru': {
        'title': 'АКТ СПИСАНИЯ ЗАПЧАСТЕЙ',
        'organization': 'Организация',
        'warehouse': 'Склад',
        'no_warehouse': '— (без склада, позиции без SKU)',
        'request': 'Заявка',
        'equipment': 'Техника',
        'date': 'Дата выдачи',
        'col_no': '№',
        'col_name': 'Наименование',
        'col_qty': 'Кол-во',
        'col_unit': 'Ед.',
        'col_price': 'Цена, сум',
        'col_total': 'Сумма, сум',
        'grand_total': 'ИТОГО',
        'issued_by': 'Выдал',
        'received_by': 'Получил',
        'signature': 'подпись',
        'full_name': 'Ф.И.О.',
        'no_sku_note': 'Позиции без SKU выданы без списания со складского учёта.',
    },
    'uz': {
        'title': 'ЭҲТИЁТ ҚИСМЛАРНИ ҲИСОБДАН ЧИҚАРИШ ДАЛОЛАТНОМАСИ',
        'organization': 'Ташкилот',
        'warehouse': 'Омбор',
        'no_warehouse': '— (омборсиз, SKUсиз позициялар)',
        'request': 'Сўров',
        'equipment': 'Техника',
        'date': 'Берилган сана',
        'col_no': '№',
        'col_name': 'Номи',
        'col_qty': 'Миқдор',
        'col_unit': 'Ўлчов',
        'col_price': 'Нарх, сўм',
        'col_total': 'Сумма, сўм',
        'grand_total': 'ЖАМИ',
        'issued_by': 'Берди',
        'received_by': 'Қабул қилди',
        'signature': 'имзо',
        'full_name': 'Ф.И.Ш.',
        'no_sku_note': 'SKUсиз позициялар омбор ҳисобидан чиқарилмасдан берилди.',
    },
}


def generate_write_off_act_pdf(act, dest_path, lang='ru', unit_labels=None):
    """Render one SparePartWriteOffAct (with .items loaded) to dest_path.

    Designed as a real printable paper document: header, items table with a
    grand total, and physical signature lines at the bottom. Raises on any
    failure — the caller runs inside the issue transaction and must roll the
    whole issue action back if the act cannot be produced.

    [REASON]: RE-SP-010 — unit_labels is an optional {code: localized word}
    map (built by the caller, which has DB access; this module has none).
    A code missing from the map renders as the raw stored code — display
    translation only, snapshots stay untouched.
    """
    unit_labels = unit_labels or {}
    _register_fonts()
    labels = _L['uz' if lang == 'uz' else 'ru']

    title_style = ParagraphStyle('act_title', fontName='DejaVuSans-Bold',
                                 fontSize=14, leading=18, alignment=1)
    number_style = ParagraphStyle('act_number', fontName='DejaVuSans-Bold',
                                  fontSize=12, leading=16, alignment=1)
    meta_style = ParagraphStyle('act_meta', fontName='DejaVuSans',
                                fontSize=10, leading=15)
    cell_style = ParagraphStyle('act_cell', fontName='DejaVuSans',
                                fontSize=9, leading=12)
    note_style = ParagraphStyle('act_note', fontName='DejaVuSans',
                                fontSize=8.5, leading=12,
                                textColor=colors.HexColor('#555555'))

    doc = SimpleDocTemplate(
        dest_path, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title='{} {}'.format(labels['title'], act.act_number),
        author='Vehicle Soft',
    )

    story = [
        Paragraph(labels['title'], title_style),
        Paragraph('№ {}'.format(act.act_number), number_style),
        Spacer(1, 6 * mm),
    ]

    # [REASON]: SP-F-005 — Paragraph() parses its text as XML-like markup, so
    # every DYNAMIC value interpolated into one must be escaped or an
    # operator-typed name like '<b>test</b>' (or a broken tag that crashes the
    # parser) would be interpreted as markup. The fixed application-controlled
    # fragments (<b>...</b> labels, the SKU <br/>/<font> line below) are NOT
    # escaped — they are the intended formatting.
    org_name = escape(act.organization.name) if act.organization else '—'
    warehouse_name = (escape(act.warehouse.name) if act.warehouse
                      else labels['no_warehouse'])
    req = act.request
    equipment = req.equipment if req else None
    eq_label = '—'
    if equipment is not None:
        eq_label = escape(
            equipment.name + (' — ' + equipment.plate if equipment.plate else ''))
    issued_date = act.issued_date.strftime('%d.%m.%Y') if act.issued_date else '—'

    meta_rows = [
        (labels['organization'], org_name),
        (labels['warehouse'], warehouse_name),
        (labels['request'], '#{}'.format(act.request_id)),
        (labels['equipment'], eq_label),
        (labels['date'], issued_date),
    ]
    for key, value in meta_rows:
        story.append(Paragraph(
            '<b>{}:</b> {}'.format(key, value), meta_style))
    story.append(Spacer(1, 6 * mm))

    # Items table: №, name (+SKU line), qty, unit, price, total.
    head = [labels['col_no'], labels['col_name'], labels['col_qty'],
            labels['col_unit'], labels['col_price'], labels['col_total']]
    data = [head]
    grand_total = 0.0
    has_total = False
    for idx, item in enumerate(act.items, start=1):
        # [REASON]: CYCLE-2-3 Part 7 — display-time Uzbek alias: only the
        # uz rendering with a non-empty catalog name_uz overrides the
        # snapshotted item.name; the stored snapshot itself is never
        # rewritten and every other case renders it byte-identical.
        display_name = item.name
        if lang == 'uz':
            req_item = getattr(item, 'request_item', None)
            part = getattr(req_item, 'spare_part', None) if req_item else None
            if part is not None and (getattr(part, 'name_uz', '') or '').strip():
                display_name = part.name_uz
        # [REASON]: SP-F-005 — name and sku_label are escaped SEPARATELY, then
        # combined, so the application's own <br/>/<font> markup keeps working.
        name_html = escape(display_name)
        if item.sku_label:
            name_html += '<br/><font size="7.5" color="#555555">SKU: {}</font>'.format(
                escape(item.sku_label))
        if item.total is not None:
            grand_total += float(item.total)
            has_total = True
        data.append([
            str(idx),
            Paragraph(name_html, cell_style),
            _fmt_qty(item.quantity),
            unit_labels.get(item.unit, item.unit) or '—',
            _fmt_money(item.price),
            _fmt_money(item.total),
        ])
    data.append(['', labels['grand_total'], '', '', '',
                 _fmt_money(grand_total if has_total else None)])

    table = Table(data, colWidths=[10 * mm, 72 * mm, 18 * mm, 16 * mm,
                                   28 * mm, 30 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), 'DejaVuSans-Bold'),
        ('FONTNAME', (0, 1), (-1, -2), 'DejaVuSans'),
        ('FONTNAME', (0, -1), (-1, -1), 'DejaVuSans-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#E8EFE8')),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#999999')),
        ('ALIGN', (2, 1), (-1, -1), 'RIGHT'),
        ('ALIGN', (0, 0), (0, -1), 'CENTER'),
        ('ALIGN', (2, 0), (-1, 0), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    story.append(table)

    if any(not item.sku_label for item in act.items):
        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph(labels['no_sku_note'], note_style))

    # Signature block: issuer name printed, receiver left blank — both with
    # physical signature lines (this is a paper document by requirement).
    # [REASON]: SP-F-005 — issuer_name (and item.unit above) are deliberately
    # NOT escaped: they go into plain Table cell strings, which reportlab
    # draws verbatim without XML parsing; escaping would corrupt legitimate
    # '&' characters into visible '&amp;'. Only Paragraph() values need it.
    issuer_name = ''
    if act.issuer:
        issuer_name = act.issuer.full_name or act.issuer.username or ''
    story.append(Spacer(1, 14 * mm))
    sig_data = [
        ['{}:'.format(labels['issued_by']), '_______________________',
         issuer_name or '_______________________'],
        ['', '({})'.format(labels['signature']),
         '({})'.format(labels['full_name'])],
        ['', '', ''],
        ['{}:'.format(labels['received_by']), '_______________________',
         '_______________________'],
        ['', '({})'.format(labels['signature']),
         '({})'.format(labels['full_name'])],
    ]
    sig_table = Table(sig_data, colWidths=[32 * mm, 62 * mm, 62 * mm])
    sig_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), 'DejaVuSans'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('FONTSIZE', (1, 1), (-1, 1), 7.5),
        ('FONTSIZE', (1, 4), (-1, 4), 7.5),
        ('TEXTCOLOR', (1, 1), (-1, 1), colors.HexColor('#777777')),
        ('TEXTCOLOR', (1, 4), (-1, 4), colors.HexColor('#777777')),
        ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
        ('TOPPADDING', (0, 0), (-1, -1), 1),
    ]))
    story.append(sig_table)

    doc.build(story)
    return dest_path
