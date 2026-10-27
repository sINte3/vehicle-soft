# -*- coding: utf-8 -*-
"""Книга xlsx для тестов выгрузок 1С -- без сторонних библиотек.

Пишет то, что читают инструменты GPS-34 и GPS-35: первый лист через
`workbook.xml` и его связи, общие или встроенные строки, числа как есть
(серийная дата Excel -- тоже число).
"""
import zipfile
from xml.sax.saxutils import escape


def _cell(ref, value, shared, inline):
    if isinstance(value, float):
        return '<c r="%s"><v>%r</v></c>' % (ref, value)
    if inline:
        return '<c r="%s" t="inlineStr"><is><t>%s</t></is></c>' % (ref, escape(value))
    if value not in shared:
        shared.append(value)
    return '<c r="%s" t="s"><v>%d</v></c>' % (ref, shared.index(value))


def write_xlsx(path, rows, inline=False):
    """Первая строка -- заголовок. None -- пустая ячейка (её нет в XML)."""
    shared = []
    body = []
    for r, row in enumerate(rows, start=1):
        cells = ''.join(_cell('%s%d' % (chr(65 + c), r), value, shared, inline)
                        for c, value in enumerate(row) if value is not None)
        body.append('<row r="%d">%s</row>' % (r, cells))
    main = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    rel = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    with zipfile.ZipFile(path, 'w') as book:
        book.writestr('[Content_Types].xml', '<?xml version="1.0"?><Types/>')
        book.writestr('xl/workbook.xml',
                      '<workbook xmlns="%s" xmlns:r="%s"><sheets>'
                      '<sheet name="TDSheet" sheetId="1" r:id="rId7"/></sheets>'
                      '</workbook>' % (main, rel))
        book.writestr('xl/_rels/workbook.xml.rels',
                      '<Relationships xmlns="http://schemas.openxmlformats.org/'
                      'package/2006/relationships"><Relationship Id="rId7" '
                      'Target="worksheets/data.xml" Type="ws"/></Relationships>')
        book.writestr('xl/worksheets/data.xml',
                      '<worksheet xmlns="%s"><sheetData>%s</sheetData></worksheet>'
                      % (main, ''.join(body)))
        if shared:
            book.writestr('xl/sharedStrings.xml', '<sst xmlns="%s">%s</sst>' % (
                main, ''.join('<si><t>%s</t></si>' % escape(s) for s in shared)))
