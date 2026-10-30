# -*- coding: utf-8 -*-
"""Печать PDF-документов по исследованию зимнего разбрасывания удобрений.

Запуск (из корня репозитория):

    python docs/research/drone-winter-spreading/build_docs.py

Пишет в docs/research/drone-winter-spreading/pdf/ восемь файлов -- по два
языка (ru -- русский, uz -- узбекский кириллицей) на четыре документа:
подробная калькуляция, краткая калькуляция, коммерческое предложение и
справка для руководства по госпрограммам. Все числа берутся из модели
build_model.py (единый тариф и минимальный заказ -- из её параметров), так
что после пилота достаточно поправить параметры и перепечатать.

После печати скрипт сам проверяет каждый PDF: шрифт встроен (FontFile2),
текст извлекается, узбекские буквы на месте. Вывод в консоль только ASCII.

Зависимости: reportlab (уже в requirements.txt проекта), pypdf для проверки
(если его нет -- проверка пропускается с сообщением). Шрифты DejaVu Sans
ищутся в системной папке и рядом с spare_parts_pdf.py.
"""
from __future__ import annotations

import os
import sys

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, 'pdf')
sys.path.insert(0, HERE)
import build_model as bm  # noqa: E402

FONT_DIRS = ['/usr/share/fonts/truetype/dejavu', os.path.join(HERE, '..', '..', '..', 'static', 'fonts')]


def register_fonts():
    for d in FONT_DIRS:
        reg = os.path.join(d, 'DejaVuSans.ttf')
        bold = os.path.join(d, 'DejaVuSans-Bold.ttf')
        if os.path.exists(reg) and os.path.exists(bold):
            pdfmetrics.registerFont(TTFont('DejaVuSans', reg))
            pdfmetrics.registerFont(TTFont('DejaVuSans-Bold', bold))
            return
    raise SystemExit('DejaVu fonts not found; install fonts-dejavu or put the TTFs next to spare_parts_pdf.py')


register_fonts()

BASE = ParagraphStyle('base', fontName='DejaVuSans', fontSize=10, leading=14, alignment=TA_LEFT)
SMALL = ParagraphStyle('small', parent=BASE, fontSize=8.5, leading=11.5, textColor=colors.HexColor('#444444'))
H1 = ParagraphStyle('h1', parent=BASE, fontName='DejaVuSans-Bold', fontSize=16, leading=20, spaceAfter=4 * mm)
H2 = ParagraphStyle('h2', parent=BASE, fontName='DejaVuSans-Bold', fontSize=12, leading=16, spaceBefore=4 * mm, spaceAfter=2 * mm)
CELL = ParagraphStyle('cell', parent=BASE, fontSize=9, leading=12)
CELL_B = ParagraphStyle('cellb', parent=CELL, fontName='DejaVuSans-Bold')
BIG = ParagraphStyle('big', parent=BASE, fontName='DejaVuSans-Bold', fontSize=13, leading=18)


def P(text, style=BASE):
    return Paragraph(text, style)


def fmt(n, digits=0):
    """1800000 -> '1 800 000'; 1.8 -> '1,8'."""
    if digits == 0:
        return '{:,.0f}'.format(round(n)).replace(',', ' ')
    return ('{:,.%df}' % digits).format(n).replace(',', ' ').replace('.', ',')


def k(n):
    """Округление до тысяч для текста: 288857 -> '289 000'."""
    return fmt(round(n / 1000.0) * 1000)


def table(rows, col_widths, header=True, bold_last=False, font_size=9):
    data = []
    for i, row in enumerate(rows):
        style = CELL_B if (header and i == 0) or (bold_last and i == len(rows) - 1) else CELL
        data.append([P(str(c), style) if not isinstance(c, Paragraph) else c for c in row])
    t = Table(data, colWidths=col_widths, repeatRows=1 if header else 0)
    ts = [('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#999999')),
          ('VALIGN', (0, 0), (-1, -1), 'TOP'),
          ('LEFTPADDING', (0, 0), (-1, -1), 4), ('RIGHTPADDING', (0, 0), (-1, -1), 4),
          ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3)]
    if header:
        ts.append(('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#E8EEF7')))
    if bold_last:
        ts.append(('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#F3F3F3')))
    t.setStyle(TableStyle(ts))
    return t


def bullets(items, style=BASE):
    return [P('•  ' + it, style) for it in items]


# ---------------------------------------------------------------------------
# Числа из модели
# ---------------------------------------------------------------------------
def numbers():
    p = bm.params_dict()
    n = {}
    n['p'] = p
    b = bm.compute(p, 250, {})
    n['b'] = b
    n['b150'] = bm.compute(p, 150, {})
    n['b100'] = bm.compute(p, 100, {})
    n['b200'] = bm.compute(p, 200, {})
    n['b400'] = bm.compute(p, 400, {})
    n['liq'] = bm.compute_liquid(p)
    n['farmer'] = bm.compute_farmer(p, b)
    n['season'] = bm.compute_season(p, b)
    n['unified'] = p['unified_tariff_per_ton']
    n['min_t'] = p['min_order_t']
    n['usd'] = p['usd_rate']
    # расходы дня
    n['pilot'] = p['pilot_day']
    n['helpers'] = p['helpers_count'] * p['helper_day']
    n['vehicle_fuel'] = p['vehicle_km'] * p['diesel_l_100km'] / 100.0 * p['diesel_price']
    n['vehicle_wear'] = p['vehicle_km'] * p['vehicle_wear_km']
    n['vehicle'] = n['vehicle_fuel'] + n['vehicle_wear']
    n['per_diem'] = p['per_diem_day']
    n['other'] = p['other_day']
    n['batt_day'] = b['battery_cost_per_flight'] * b['flights_per_day']
    n['fuel_day'] = b['fuel_per_flight'] * b['flights_per_day']
    n['wear_day'] = b['wear_per_flight'] * b['flights_per_day']
    n['spreader_day'] = b['spreader_per_day']
    n['total_day'] = b['total_cost_per_day']
    n['batt_full_charge'] = p['battery_price_usd'] * p['usd_rate'] / p['battery_cycles']
    n['petrol_l_day'] = b['fuel_per_flight'] / p['petrol_price'] * b['flights_per_day']
    n['flight_hours_day'] = b['flight_min'] * b['flights_per_day'] / 60.0
    n['direct_t'] = b['c1_per_kg'] * 1000.0
    n['target_t'] = b['tariff_per_ton']
    n['overhead_t'] = n['unified'] * p['overhead_pct']
    n['profit_t'] = n['unified'] - n['direct_t'] - n['overhead_t']
    n['profit_t_pct'] = n['profit_t'] / n['unified'] * 100.0
    n['rev_day'] = b['t_per_day'] * n['unified']
    n['overhead_day'] = n['rev_day'] * p['overhead_pct']
    n['profit_day'] = n['rev_day'] - n['total_day'] - n['overhead_day']
    # что если
    def per_t(**m):
        return bm.compute(p, 250, m)['c1_per_kg'] * 1000.0
    n['wi_eff2'] = per_t(m_eff=2.0 / 3.0)
    n['wi_eff4'] = per_t(m_eff=4.0 / 3.0)
    n['wi_flow26'] = per_t(m_flow=26.0 / 30.0)
    n['wi_flow37'] = per_t(m_flow=37.0 / 30.0)
    n['wi_reload3'] = per_t(m_reload=2.0)
    n['wi_payload50'] = per_t(m_payload=50.0 / 45.0)
    n['wi_pess'] = bm.compute(p, 250, {'m_eff': 2.0 / 3.0, 'm_reload': 2.0, 'm_overhead': 4.0 / 3.0, 'm_payload': 40.0 / 45.0})['c1_per_kg'] * 1000.0
    n['wi_opt'] = bm.compute(p, 250, {'m_eff': 1.5, 'm_reload': 2.0 / 3.0, 'm_overhead': 0.8, 'm_payload': 50.0 / 45.0})['c1_per_kg'] * 1000.0
    s = n['season']
    n['season_t'] = s['s_ha_total'] * 250 / 1000.0
    n['vat_price'] = n['unified'] * 1.12
    return n


# ---------------------------------------------------------------------------
# Документ 1: подробная калькуляция
# ---------------------------------------------------------------------------
def calc_detailed(lang, n):
    p, b = n['p'], n['b']
    ru = lang == 'ru'
    f = []
    f.append(P('Сколько стоит разбросать удобрение дроном. Подробная калькуляция' if ru else
               'Ўғитни дрон билан сепиш қанчага тушади. Батафсил ҳисоб-китоб', H1))
    f.append(P(('Расчёт на 30.10.2026, до пробных полётов. Дрон DJI Agras T40 с баком-разбрасывателем. '
                'Калькуляция отвечает на три вопроса: сколько дрон успевает за день, сколько нам стоит этот день '
                'и какая цена покрывает расходы и даёт прибыль. В конце — что надо перепроверить в поле.') if ru else
               ('Ҳисоб 30.10.2026 ҳолатига, синов парвозларидан олдин. Дрон — сепиш баки ўрнатилган DJI Agras T40. '
                'Ҳисоб-китоб учта саволга жавоб беради: дрон бир кунда қанча иш қилади, бу кун бизга қанчага тушади '
                'ва қандай нарх харажатларни қоплаб, фойда беради. Охирида — далада нимани қайта текшириш керак.'), BASE))
    f.append(Spacer(1, 3 * mm))

    # 1. Исходные данные
    f.append(P('1. Исходные данные' if ru else '1. Дастлабки маълумотлар', H2))
    rows = [[('Что' if ru else 'Нима'), ('Сколько' if ru else 'Қанча'), ('Откуда' if ru else 'Қаердан')],
            [('Загрузка бака за один вылет' if ru else 'Бир парвозда бакка юкланадиган ўғит'), '%s кг' % fmt(p['payload_kg']),
             ('Бак 70 л, до 50 кг по паспорту; берём 45 кг с запасом' if ru else 'Бак 70 л, паспорт бўйича 50 кг гача; эҳтиёт учун 45 кг олинди')],
            [('Ширина прохода' if ru else 'Қатор кенглиги'), '%s м' % fmt(p['swath_eff_m']),
             ('Ваша нормативная таблица' if ru else 'Сизнинг норматив жадвалингиз')],
            [('Подача гранул из бака' if ru else 'Доналарнинг бакдан узатилиши'), '%s кг/мин' % fmt(p['flow_kg_min']),
             ('Из таблицы: карбамид 26, селитра 37; берём среднее. Это главное узкое место' if ru else
              'Жадвалдан: карбамид 26, селитра 37; ўртачаси олинди. Бу — асосий чеклов')],
            [('Норма внесения для примера' if ru else 'Мисол учун сепиш меъёри'), '250 кг/га',
             ('Ваша оценка; агроном задаёт свою' if ru else 'Сизнинг баҳоингиз; агроном ўзиникини белгилайди')],
            [('Чистое время работы в день' if ru else 'Кунлик соф иш вақти'), '%s ч' % fmt(p['eff_hours_per_day']),
             ('Остальное уходит на переезды, погоду, ожидание. Так выходит и летом' if ru else
              'Қолгани йўл, об-ҳаво ва кутишга кетади. Ёзда ҳам шундай чиқади')],
            [('Рабочих дней за сезон' if ru else 'Мавсумдаги иш кунлари'), '%s' % fmt(p['days_per_season']),
             ('Февраль — середина марта, минус выходные и непогода' if ru else 'Февраль — март ўрталари, дам олиш ва ёмон об-ҳаво чиқариб ташланган')],
            [('Курс доллара' if ru else 'Доллар курси'), '%s сум' % fmt(p['usd_rate']), ('ЦБ, конец октября 2026' if ru else 'Марказий банк, 2026 йил октябрь охири')]]
    f.append(table(rows, [60 * mm, 32 * mm, 83 * mm]))

    # 2. Сколько успевает
    f.append(P('2. Сколько дрон успевает за день' if ru else '2. Дрон бир кунда қанча иш қилади', H2))
    items_ru = [
        'Один вылет: разбрасывание %s кг при %s кг/мин — %s мин; взлёт, подлёт, посадка — %s мин; засыпка нового мешка и смена батареи — %s мин. Полный круг — <b>%s мин</b>.'
        % (fmt(p['payload_kg']), fmt(p['flow_kg_min']), fmt(b['spread_min_per_flight'], 1), fmt(p['overhead_min'], 1), fmt(p['reload_min'], 1), fmt(b['cycle_min'], 1)),
        'За час: 60 / %s = %s вылетов × %s кг = <b>%s кг</b>.' % (fmt(b['cycle_min'], 1), fmt(b['flights_per_hour'], 1), fmt(p['payload_kg']), fmt(b['kg_per_hour'])),
        'За день: %s ч × %s кг = <b>%s кг = %s тонны</b>, это %s вылетов.' % (fmt(p['eff_hours_per_day']), fmt(b['kg_per_hour']), fmt(b['kg_per_hour'] * p['eff_hours_per_day']), fmt(b['t_per_day'], 1), fmt(b['flights_per_day'])),
        'В гектарах: при 250 кг/га — %s га, при 150 кг/га — %s га. Тонны за день одни и те же при любой норме, меняются только гектары. Поэтому дальше считаем на тонну.'
        % (fmt(b['ha_per_day'], 1), fmt(n['b150']['ha_per_day'], 1)),
        'Скорость над полем при 250 кг/га получается %s км/ч — как в вашей таблице (8–11 км/ч). Быстрее не даёт подача гранул.' % fmt(b['speed_eff'] * 3.6, 0),
    ]
    items_uz = [
        'Бир парвоз: %s кг ўғитни %s кг/мин тезликда сепиш — %s мин; кўтарилиш, далага учиб бориш, қўниш — %s мин; янги қопни тўкиш ва батареяни алмаштириш — %s мин. Тўлиқ айланма — <b>%s мин</b>.'
        % (fmt(p['payload_kg']), fmt(p['flow_kg_min']), fmt(b['spread_min_per_flight'], 1), fmt(p['overhead_min'], 1), fmt(p['reload_min'], 1), fmt(b['cycle_min'], 1)),
        'Бир соатда: 60 / %s = %s парвоз × %s кг = <b>%s кг</b>.' % (fmt(b['cycle_min'], 1), fmt(b['flights_per_hour'], 1), fmt(p['payload_kg']), fmt(b['kg_per_hour'])),
        'Бир кунда: %s соат × %s кг = <b>%s кг = %s тонна</b>, бу %s парвоз.' % (fmt(p['eff_hours_per_day']), fmt(b['kg_per_hour']), fmt(b['kg_per_hour'] * p['eff_hours_per_day']), fmt(b['t_per_day'], 1), fmt(b['flights_per_day'])),
        'Гектарда: 250 кг/га бўлса — %s га, 150 кг/га бўлса — %s га. Кунлик тонна ҳар қандай меъёрда бир хил, фақат гектар ўзгаради. Шу сабабли кейинги ҳисоб тоннага.'
        % (fmt(b['ha_per_day'], 1), fmt(n['b150']['ha_per_day'], 1)),
        '250 кг/га меъёрда дала устидаги тезлик %s км/соат чиқади — жадвалингиздагидек (8–11 км/соат). Доналар узатилиши бундан тезроқ ишлашга йўл қўймайди.' % fmt(b['speed_eff'] * 3.6, 0),
    ]
    f.extend(bullets(items_ru if ru else items_uz))

    # 3. Расходы дня
    f.append(P('3. Расходы за один рабочий день одного дрона' if ru else '3. Бир дроннинг бир иш кунидаги харажатлари', H2))
    rows = [[('Статья' if ru else 'Харажат'), ('Сум' if ru else 'Сўм'), ('Как посчитано' if ru else 'Қандай ҳисобланган')],
            [('Пилот' if ru else 'Учувчи (оператор)'), k(n['pilot']), ('Оплата за день работы' if ru else 'Бир иш куни учун ҳақ')],
            [('Два помощника на засыпке' if ru else 'Қоп тўкишда икки ёрдамчи'), k(n['helpers']),
             ('2 × %s. Через их руки за день проходит %s тонны мешков' % (fmt(p['helper_day']), fmt(b['t_per_day'], 1)) if ru else
              '2 × %s. Бир кунда қўлларидан %s тонна қоп ўтади' % (fmt(p['helper_day']), fmt(b['t_per_day'], 1)))],
            [('Машина сопровождения' if ru else 'Ҳамроҳ машина'), k(n['vehicle']),
             ('%s км в день: дизель %s л × %s = %s, износ %s сум/км = %s' % (fmt(p['vehicle_km']), fmt(p['vehicle_km'] * p['diesel_l_100km'] / 100), fmt(p['diesel_price']), fmt(n['vehicle_fuel']), fmt(p['vehicle_wear_km']), fmt(n['vehicle_wear'])) if ru else
              'Кунига %s км: дизель %s л × %s = %s, ейилиш %s сўм/км = %s' % (fmt(p['vehicle_km']), fmt(p['vehicle_km'] * p['diesel_l_100km'] / 100), fmt(p['diesel_price']), fmt(n['vehicle_fuel']), fmt(p['vehicle_wear_km']), fmt(n['vehicle_wear'])))],
            [('Питание и проживание экипажа' if ru else 'Экипаж овқати ва турар жойи'), fmt(n['per_diem']), ''],
            [('Прочее: генератор, связь, промывка техники' if ru else 'Бошқа: генератор, алоқа, техникани ювиш'), fmt(n['other']), ''],
            [('Износ батарей' if ru else 'Батареяларнинг ейилиши'), k(n['batt_day']),
             ('Батарея %s $ = %s сум на %s зарядок = %s за полную зарядку; за день %s полных зарядок' % (fmt(p['battery_price_usd']), fmt(p['battery_price_usd'] * p['usd_rate']), fmt(p['battery_cycles']), fmt(n['batt_full_charge']), fmt(b['charges_per_day'], 1)) if ru else
              'Батарея %s $ = %s сўм, %s марта зарядланади = бир тўлиқ зарядка %s; кунига %s тўлиқ зарядка' % (fmt(p['battery_price_usd']), fmt(p['battery_price_usd'] * p['usd_rate']), fmt(p['battery_cycles']), fmt(n['batt_full_charge']), fmt(b['charges_per_day'], 1)))],
            [('Бензин для генератора' if ru else 'Генератор учун бензин'), k(n['fuel_day']),
             ('%s л × %s сум' % (fmt(n['petrol_l_day'], 1), fmt(p['petrol_price'])) if ru else '%s л × %s сўм' % (fmt(n['petrol_l_day'], 1), fmt(p['petrol_price'])))],
            [('Износ дрона: откладываем на ремонт' if ru else 'Дроннинг ейилиши: таъмирга ажратма'), k(n['wear_day']),
             ('%s $ за лётный час плюс 50 %% за пыль и коррозию удобрений; %s лётных часа в день' % (fmt(p['drone_wear_usd_h']), fmt(n['flight_hours_day'], 1)) if ru else
              'Ҳар учиш соати учун %s $ ва ўғит чанги ва коррозияси учун 50 %% устама; кунига %s учиш соати' % (fmt(p['drone_wear_usd_h']), fmt(n['flight_hours_day'], 1)))],
            [('Бак-разбрасыватель: откладываем на замену' if ru else 'Сепиш баки: янгисига ажратма'), k(n['spreader_day']),
             ('%s $ = %s сум на %s сезона по %s дней' % (fmt(p['spreader_price_usd']), fmt(p['spreader_price_usd'] * p['usd_rate']), fmt(p['spreader_life_seasons']), fmt(p['days_per_season'])) if ru else
              '%s $ = %s сўм, %s мавсум × %s кун' % (fmt(p['spreader_price_usd']), fmt(p['spreader_price_usd'] * p['usd_rate']), fmt(p['spreader_life_seasons']), fmt(p['days_per_season'])))],
            [('<b>Итого за день</b>' if ru else '<b>Кунлик жами</b>'), '<b>≈ %s</b>' % k(n['total_day']), '']]
    f.append(table(rows, [52 * mm, 24 * mm, 99 * mm], bold_last=True))
    f.append(Spacer(1, 2 * mm))
    f.append(P(('Чего здесь нет: амортизации самого дрона. Парк куплен для летней работы и окупается летом; зимой считаем только то, что '
                'тратится из-за зимней работы. Если захотите отнести на зиму часть стоимости дрона, себестоимость тонны вырастет примерно на пятую часть.') if ru else
               ('Бу ерда нима йўқ: дроннинг ўз амортизацияси. Парк ёзги иш учун олинган ва ёзда ўзини қоплайди; қишда фақат қишки иш туфайли '
                'кетадиган харажатларни ҳисоблаймиз. Агар дрон нархининг бир қисмини қишга ўтказмоқчи бўлсангиз, тонна таннархи тахминан бешдан бирга ошади.'), SMALL))

    # 4. Себестоимость
    f.append(P('4. Себестоимость' if ru else '4. Таннарх', H2))
    items_ru = [
        'Расходы дня делим на тонны дня: %s / %s т = <b>около %s сум за тонну</b>, или %s сум за килограмм.' % (k(n['total_day']), fmt(b['t_per_day'], 1), k(n['direct_t']), fmt(b['c1_per_kg'])),
        'На гектар (справочно): при 150 кг/га — %s, при 200 — %s, при 250 — %s, при 400 — %s сум.' % (k(n['b150']['direct_cost_per_ha']), k(n['b200']['direct_cost_per_ha']), k(b['direct_cost_per_ha']), k(n['b400']['direct_cost_per_ha'])),
        'Для сравнения: опрыскивание по этой же схеме расходов стоит нам около %s сум/га при цене %s. Разбрасывание дороже в %s раза на гектар при 250 кг/га — потому что дрон дольше висит над полем и в четыре-пять раз чаще садится за новым мешком.'
        % (k(n['liq']['liq_direct_per_ha']), fmt(p['liquid_tariff_external']), fmt(b['direct_cost_per_ha'] / n['liq']['liq_direct_per_ha'], 1)),
    ]
    items_uz = [
        'Кунлик харажатни кунлик тоннага бўламиз: %s / %s т = <b>бир тонна тахминан %s сўм</b>, яъни бир килограмм %s сўм.' % (k(n['total_day']), fmt(b['t_per_day'], 1), k(n['direct_t']), fmt(b['c1_per_kg'])),
        'Гектарга (маълумот учун): 150 кг/га бўлса — %s, 200 бўлса — %s, 250 бўлса — %s, 400 бўлса — %s сўм.' % (k(n['b150']['direct_cost_per_ha']), k(n['b200']['direct_cost_per_ha']), k(b['direct_cost_per_ha']), k(n['b400']['direct_cost_per_ha'])),
        'Солиштириш учун: пуркаш шу харажат схемасида бизга гектарига тахминан %s сўмга тушади, нархи %s. 250 кг/га меъёрда сепиш гектарига %s баравар қиммат — чунки дрон дала устида узоқроқ туради ва янги қоп учун тўрт-беш баравар кўп қўнади.'
        % (k(n['liq']['liq_direct_per_ha']), fmt(p['liquid_tariff_external']), fmt(b['direct_cost_per_ha'] / n['liq']['liq_direct_per_ha'], 1)),
    ]
    f.extend(bullets(items_ru if ru else items_uz))

    # 5. Цена
    f.append(P('5. Цена для фермера' if ru else '5. Фермер учун нарх', H2))
    items_ru = [
        'К себестоимости добавляем расходы конторы (диспетчер, бухгалтерия, страховка) — %s %% от выручки, и прибыль — %s %% от выручки.' % (fmt(p['overhead_pct'] * 100), fmt(p['target_margin_pct'] * 100)),
        'Цена = себестоимость / (1 − 0,%s − 0,%s) = %s / 0,%s = %s сум за тонну. Округляем: <b>%s сум за тонну</b> — одна цена для селитры, карбамида и аммофоса.'
        % (fmt(p['overhead_pct'] * 100), fmt(p['target_margin_pct'] * 100), k(n['direct_t']), fmt((1 - p['overhead_pct'] - p['target_margin_pct']) * 100), k(n['target_t']), fmt(n['unified'])),
        'Что остаётся с тонны: выручка %s − себестоимость %s − расходы конторы %s = <b>прибыль %s сум (%s %%)</b>.' % (fmt(n['unified']), k(n['direct_t']), fmt(n['overhead_t']), k(n['profit_t']), fmt(n['profit_t_pct'], 0)),
        'В день один дрон: выручка %s, расходы %s, конторе %s, прибыль около %s сум.' % (fmt(n['rev_day']), k(n['total_day']), fmt(n['overhead_day']), k(n['profit_day'])),
        'Минимальный заказ — %s тонны (%s сум). Меньше брать невыгодно: один день экипажа стоит %s сум, а мелкий заказ ещё и съедает день переездом.' % (fmt(n['min_t']), fmt(n['unified'] * n['min_t']), fmt(b['crew_day'])),
    ]
    items_uz = [
        'Таннархга идора харажатлари (диспетчер, бухгалтерия, суғурта) — тушумнинг %s %% и ва фойда — тушумнинг %s %% и қўшилади.' % (fmt(p['overhead_pct'] * 100), fmt(p['target_margin_pct'] * 100)),
        'Нарх = таннарх / (1 − 0,%s − 0,%s) = %s / 0,%s = бир тонна %s сўм. Яхлитлаймиз: <b>бир тонна %s сўм</b> — селитра, карбамид ва аммофос учун бир хил нарх.'
        % (fmt(p['overhead_pct'] * 100), fmt(p['target_margin_pct'] * 100), k(n['direct_t']), fmt((1 - p['overhead_pct'] - p['target_margin_pct']) * 100), k(n['target_t']), fmt(n['unified'])),
        'Бир тоннадан қоладигани: тушум %s − таннарх %s − идора харажати %s = <b>фойда %s сўм (%s %%)</b>.' % (fmt(n['unified']), k(n['direct_t']), fmt(n['overhead_t']), k(n['profit_t']), fmt(n['profit_t_pct'], 0)),
        'Бир дрон бир кунда: тушум %s, харажат %s, идорага %s, фойда тахминан %s сўм.' % (fmt(n['rev_day']), k(n['total_day']), fmt(n['overhead_day']), k(n['profit_day'])),
        'Энг кам буюртма — %s тонна (%s сўм). Камига бориш фойдасиз: экипажнинг бир куни %s сўм туради, майда буюртма эса кунни йўлга сарфлайди.' % (fmt(n['min_t']), fmt(n['unified'] * n['min_t']), fmt(b['crew_day'])),
    ]
    f.extend(bullets(items_ru if ru else items_uz))
    f.append(Spacer(1, 2 * mm))
    rows = [[('Норма, кг/га' if ru else 'Меъёр, кг/га'), ('Цена гектара при %s сум/т' % fmt(n['unified']) if ru else 'Гектар нархи, %s сўм/т бўлганда' % fmt(n['unified'])),
             ('Себестоимость гектара' if ru else 'Гектар таннархи'), ('Остаётся нам после расходов конторы' if ru else 'Идора харажатидан кейин бизга қоладигани')]]
    for rate, bb in ((100, n['b100']), (150, n['b150']), (200, n['b200']), (250, b), (400, n['b400'])):
        price = n['unified'] * rate / 1000.0
        rows.append([str(rate), fmt(price), k(bb['direct_cost_per_ha']), '%s (%s %%)' % (k(price - bb['direct_cost_per_ha'] - price * p['overhead_pct']), fmt((price - bb['direct_cost_per_ha'] - price * p['overhead_pct']) / price * 100, 0))])
    f.append(table(rows, [28 * mm, 52 * mm, 42 * mm, 53 * mm]))
    f.append(Spacer(1, 2 * mm))
    f.append(P(('Почему одна цена за тонну, а не за гектар. Дрон за день вносит одинаковые тонны при любой норме, значит и расходы у нас '
                'идут на тонны. Если брать за гектар, то при 400 кг/га мы разоримся, а при 100 кг/га фермер переплатит. Одна цена за тонну — '
                'честно для обоих, понятно фермеру (он знает, сколько мешков привёз) и не оставляет оператору выбора, по какой «ступени» отчитаться.') if ru else
               ('Нега гектарга эмас, тоннага бир нарх. Дрон бир кунда ҳар қандай меъёрда бир хил тонна сепади, демак бизнинг харажат ҳам тоннага кетади. '
                'Гектарга олсак, 400 кг/га меъёрда биз зарар кўрамиз, 100 кг/га меъёрда фермер ортиқча тўлайди. Тоннага бир нарх — икки тараф учун ҳам адолатли, '
                'фермерга тушунарли (қанча қоп олиб келганини билади) ва операторга ҳисоботда қайси «поғона»ни танлашга йўл қолдирмайди.'), BASE))
    f.append(P(('Налоги в расчёте не выделены. Если цена для фермера указывается с НДС 12 %%, то с НДС это %s сум за тонну; если без — '
                'уточните у бухгалтерии, как это считается по опрыскиванию сейчас.' % fmt(n['vat_price'])) if ru else
               ('Солиқлар ҳисобда алоҳида кўрсатилмаган. Агар фермер учун нарх ҚҚС 12 %% билан кўрсатилса, ҚҚС билан бир тонна %s сўм бўлади; '
                'ҚҚСсиз бўлса — ҳозир пуркашда қандай ҳисобланишини бухгалтериядан аниқланг.' % fmt(n['vat_price'])), SMALL))

    # 6. Сезон
    f.append(P('6. Что даёт сезон' if ru else '6. Мавсум нима беради', H2))
    s = n['season']
    rows = [[('Показатель' if ru else 'Кўрсаткич'), ('Сум' if ru else 'Сўм'), ('Как посчитано' if ru else 'Қандай ҳисобланган')],
            [('Внесено за сезон' if ru else 'Мавсумда сепилган ўғит'), '%s т' % fmt(n['season_t']), ('5 дронов × %s дней × %s т' % (fmt(p['days_per_season']), fmt(b['t_per_day'], 1)) if ru else '5 дрон × %s кун × %s т' % (fmt(p['days_per_season']), fmt(b['t_per_day'], 1)))],
            [('Выручка' if ru else 'Тушум'), k(s['s_revenue']), '%s т × %s' % (fmt(n['season_t']), fmt(n['unified']))],
            [('Расходы дней работы' if ru else 'Иш кунлари харажати'), k(s['s_direct']), ('150 дней × %s' % k(n['total_day']) if ru else '150 кун × %s' % k(n['total_day']))],
            [('Расходы конторы' if ru else 'Идора харажатлари'), k(s['s_overhead']), ('10 % от выручки' if ru else 'тушумнинг 10 % и')],
            [('Разовые расходы' if ru else 'Бир марталик харажатлар'), k(s['s_one_time']),
             ('4 бака по %s $ = %s + обучение и оснастка %s' % (fmt(p['spreader_price_usd']), fmt(4 * p['spreader_price_usd'] * p['usd_rate']), fmt(p['season_one_time_other'])) if ru else
              '4 бак × %s $ = %s + ўқитиш ва жиҳозлаш %s' % (fmt(p['spreader_price_usd']), fmt(4 * p['spreader_price_usd'] * p['usd_rate']), fmt(p['season_one_time_other'])))],
            [('<b>Остаётся за сезон</b>' if ru else '<b>Мавсум якунида қоладигани</b>'), '<b>≈ %s</b>' % k(s['s_result']), ('около %s $' % fmt(s['s_result_usd']) if ru else 'тахминан %s $' % fmt(s['s_result_usd']))]]
    f.append(table(rows, [52 * mm, 34 * mm, 89 * mm], bold_last=True))
    f.append(Spacer(1, 2 * mm))
    f.append(P(('Это немного: один летний месяц опрыскивания даёт около 1,4 млрд сум выручки. Зимняя работа нужна, чтобы техника и люди не стояли, '
                'а не чтобы заработать. Во второй сезон без разовых расходов остаётся около %s млн.' % fmt(s['s_margin_before_one_time'] / 1e6, 0)) if ru else
               ('Бу кўп эмас: ёзги бир ой пуркаш тахминан 1,4 млрд сўм тушум беради. Қишки иш техника ва одамлар бекор турмаслиги учун керак, '
                'катта фойда учун эмас. Иккинчи мавсумда бир марталик харажатларсиз тахминан %s млн қолади.' % fmt(s['s_margin_before_one_time'] / 1e6, 0)), BASE))

    # 7. От чего цена может уехать
    f.append(P('7. От чего себестоимость тонны может уехать' if ru else '7. Тонна таннархи нимадан ўзгариши мумкин', H2))
    rows = [[('Если…' if ru else 'Агар…'), ('Себестоимость тонны' if ru else 'Тонна таннархи'), ('Вместо' if ru else 'Ўрнига')],
            [('чистой работы не 3, а 2 часа в день' if ru else 'соф иш куни 3 эмас, 2 соат бўлса'), k(n['wi_eff2']), k(n['direct_t'])],
            [('чистой работы 4 часа' if ru else 'соф иш 4 соат бўлса'), k(n['wi_eff4']), k(n['direct_t'])],
            [('подача карбамида 26 кг/мин' if ru else 'карбамид узатилиши 26 кг/мин бўлса'), k(n['wi_flow26']), k(n['direct_t'])],
            [('подача селитры 37 кг/мин' if ru else 'селитра узатилиши 37 кг/мин бўлса'), k(n['wi_flow37']), k(n['direct_t'])],
            [('засыпка и смена батареи 3 мин вместо 1,5' if ru else 'қоп тўкиш ва батарея алмаштириш 1,5 эмас, 3 мин бўлса'), k(n['wi_reload3']), k(n['direct_t'])],
            [('в бак грузим 50 кг вместо 45' if ru else 'бакка 45 эмас, 50 кг юкласак'), k(n['wi_payload50']), k(n['direct_t'])],
            [('всё плохо сразу: 2 часа, засыпка 3 мин, бак 40 кг' if ru else 'ҳаммаси ёмон: 2 соат, тўкиш 3 мин, бак 40 кг'), k(n['wi_pess']), k(n['direct_t'])],
            [('всё хорошо сразу: 4,5 часа, засыпка 1 мин, бак 50 кг' if ru else 'ҳаммаси яхши: 4,5 соат, тўкиш 1 мин, бак 50 кг'), k(n['wi_opt']), k(n['direct_t'])]]
    f.append(table(rows, [95 * mm, 40 * mm, 40 * mm]))
    f.append(Spacer(1, 2 * mm))
    f.append(P(('Сильнее всего на цену влияют часы работы в день и подача гранул. Цены батарей и бензина меняют её на один-два процента.') if ru else
               ('Нархга энг кўп кунлик иш соати ва доналар узатилиши таъсир қилади. Батарея ва бензин нархи уни бир-икки фоизгина ўзгартиради.'), BASE))

    # 8. Что проверить
    f.append(P('8. Что проверить пробными полётами (2–3 дня, один дрон, своё поле)' if ru else '8. Синов парвозларида нимани текшириш (2–3 кун, бир дрон, ўз даламиз)', H2))
    items_ru = [
        'Подача: сколько килограммов за минуту уходит из бака при рабочей заслонке — отдельно карбамид и селитра.',
        'Время круга по секундомеру: разбрасывание, взлёт-посадка, засыпка и смена батареи.',
        'Сколько часов за день дрон реально летал и куда ушли остальные часы.',
        'Сколько процентов батареи съедает один вылет, сколько бензина сжёг генератор за день.',
        'Ровно ли ложится удобрение: лотки через метр поперёк прохода, взвесить.',
        'После промывки — нет ли следов коррозии и пыли в моторах.',
        'Потом поправить числа в книге model.xlsx (жёлтые ячейки) и перепечатать эти документы.',
    ]
    items_uz = [
        'Узатилиш: ишчи тўсиқда бакдан бир минутда қанча килограмм чиқади — карбамид ва селитра алоҳида.',
        'Айланма вақти секундомер билан: сепиш, кўтарилиш-қўниш, қоп тўкиш ва батарея алмаштириш.',
        'Дрон бир кунда ростдан қанча соат учди ва қолган соатлар қаерга кетди.',
        'Бир парвоз батареянинг неча фоизини олади, генератор кунига қанча бензин ёқди.',
        'Ўғит текис тушадими: қатор кесимида ҳар метрда тоғора, тортиб кўриш.',
        'Ювишдан кейин — моторларда коррозия ва чанг излари йўқми.',
        'Кейин model.xlsx китобидаги рақамларни (сариқ катаклар) тўғрилаб, бу ҳужжатларни қайта чоп этиш.',
    ]
    f.extend(bullets(items_ru if ru else items_uz))
    return f


# ---------------------------------------------------------------------------
# Документ 2: краткая калькуляция
# ---------------------------------------------------------------------------
def calc_short(lang, n):
    p, b, s = n['p'], n['b'], n['season']
    ru = lang == 'ru'
    f = []
    f.append(P('Разбрасывание удобрений дроном — краткая калькуляция' if ru else 'Ўғитни дрон билан сепиш — қисқача ҳисоб-китоб', H1))
    f.append(P(('DJI Agras T40 с баком-разбрасывателем. Расчёт на 30.10.2026, до пробных полётов.' if ru else
               'Сепиш баки ўрнатилган DJI Agras T40. Ҳисоб 30.10.2026 ҳолатига, синов парвозларидан олдин.'), SMALL))
    f.append(Spacer(1, 3 * mm))
    f.append(P('За один день один дрон' if ru else 'Бир дрон бир кунда', H2))
    rows = [[('Вылетов' if ru else 'Парвозлар'), ('Внесено' if ru else 'Сепилган ўғит'), ('Это гектаров при 250 кг/га' if ru else '250 кг/га меъёрда бу гектар'), ('При 150 кг/га' if ru else '150 кг/га меъёрда')],
            [fmt(b['flights_per_day']), '%s т' % fmt(b['t_per_day'], 1), '%s га' % fmt(b['ha_per_day'], 1), '%s га' % fmt(n['b150']['ha_per_day'], 1)]]
    f.append(table(rows, [35 * mm, 40 * mm, 55 * mm, 45 * mm]))
    f.append(P(('Один круг — %s минуты: %s мин разбрасывание, %s мин взлёт и посадка, %s мин засыпка и смена батареи. Чистой работы %s часа в день.'
                % (fmt(b['cycle_min'], 1), fmt(b['spread_min_per_flight'], 1), fmt(p['overhead_min'], 1), fmt(p['reload_min'], 1), fmt(p['eff_hours_per_day']))) if ru else
               ('Бир айланма — %s минут: %s мин сепиш, %s мин кўтарилиш ва қўниш, %s мин қоп тўкиш ва батарея алмаштириш. Кунига %s соат соф иш.'
                % (fmt(b['cycle_min'], 1), fmt(b['spread_min_per_flight'], 1), fmt(p['overhead_min'], 1), fmt(p['reload_min'], 1), fmt(p['eff_hours_per_day']))), SMALL))

    f.append(P('Расходы за день' if ru else 'Кунлик харажатлар', H2))
    rows = [[('Статья' if ru else 'Харажат'), ('Сум' if ru else 'Сўм')],
            [('Пилот и два помощника' if ru else 'Учувчи ва икки ёрдамчи'), k(n['pilot'] + n['helpers'])],
            [('Машина, питание, прочее' if ru else 'Машина, овқат, бошқа'), k(n['vehicle'] + n['per_diem'] + n['other'])],
            [('Батареи, бензин генератора, износ дрона' if ru else 'Батареялар, генератор бензини, дрон ейилиши'), k(n['batt_day'] + n['fuel_day'] + n['wear_day'])],
            [('Бак-разбрасыватель (на замену)' if ru else 'Сепиш баки (янгисига)'), k(n['spreader_day'])],
            [('<b>Итого</b>' if ru else '<b>Жами</b>'), '<b>≈ %s</b>' % k(n['total_day'])]]
    f.append(table(rows, [110 * mm, 65 * mm], bold_last=True))

    f.append(P('Себестоимость и цена' if ru else 'Таннарх ва нарх', H2))
    rows = [['', ('За тонну' if ru else 'Бир тонна'), ('За кг' if ru else 'Бир кг')],
            [('Себестоимость (расходы дня / тонны дня)' if ru else 'Таннарх (кунлик харажат / кунлик тонна)'), k(n['direct_t']), fmt(b['c1_per_kg'])],
            [('+ расходы конторы 10 % и прибыль 25 % от выручки' if ru else '+ идора харажати 10 % ва фойда 25 % (тушумдан)'), k(n['target_t']), fmt(n['target_t'] / 1000)],
            [('<b>Единая цена для фермера (округлённо)</b>' if ru else '<b>Фермер учун ягона нарх (яхлитланган)</b>'), '<b>%s</b>' % fmt(n['unified']), '<b>%s</b>' % fmt(n['unified'] / 1000)],
            [('Прибыль с тонны после расходов конторы' if ru else 'Идора харажатидан кейин бир тоннадан фойда'), '%s (%s %%)' % (k(n['profit_t']), fmt(n['profit_t_pct'], 0)), fmt(n['profit_t'] / 1000)]]
    f.append(table(rows, [105 * mm, 40 * mm, 30 * mm]))
    f.append(Spacer(1, 2 * mm))
    rows = [[('Норма, кг/га' if ru else 'Меъёр, кг/га'), '150', '200', '250', '400'],
            [('Цена гектара, сум' if ru else 'Гектар нархи, сўм'), fmt(n['unified'] * 0.15), fmt(n['unified'] * 0.2), fmt(n['unified'] * 0.25), fmt(n['unified'] * 0.4)],
            [('Себестоимость гектара' if ru else 'Гектар таннархи'), k(n['b150']['direct_cost_per_ha']), k(n['b200']['direct_cost_per_ha']), k(b['direct_cost_per_ha']), k(n['b400']['direct_cost_per_ha'])]]
    f.append(table(rows, [55 * mm, 30 * mm, 30 * mm, 30 * mm, 30 * mm]))
    f.append(P(('Минимальный заказ — %s тонны (%s сум). Налоги в расчёте не выделены.' % (fmt(n['min_t']), fmt(n['unified'] * n['min_t']))) if ru else
               ('Энг кам буюртма — %s тонна (%s сўм). Солиқлар ҳисобда алоҳида кўрсатилмаган.' % (fmt(n['min_t']), fmt(n['unified'] * n['min_t']))), SMALL))

    f.append(P('Сезон: 5 дронов, %s дней' % fmt(p['days_per_season']) if ru else 'Мавсум: 5 дрон, %s кун' % fmt(p['days_per_season']), H2))
    rows = [[('Внесено' if ru else 'Сепилган'), ('Выручка' if ru else 'Тушум'), ('Все расходы' if ru else 'Барча харажат'), ('Разовые: 4 бака, обучение' if ru else 'Бир марталик: 4 бак, ўқитиш'), ('Остаётся' if ru else 'Қоладигани')],
            ['%s т' % fmt(n['season_t']), k(s['s_revenue']), k(s['s_direct'] + s['s_overhead']), k(s['s_one_time']), '<b>≈ %s</b>' % k(s['s_result'])]]
    f.append(table(rows, [28 * mm, 36 * mm, 36 * mm, 42 * mm, 33 * mm]))
    f.append(P(('Для сравнения: летний месяц опрыскивания — около 1,4 млрд сум выручки. Зимняя работа — чтобы не стоять, а не чтобы заработать.') if ru else
               ('Солиштириш учун: ёзги бир ой пуркаш — тахминан 1,4 млрд сўм тушум. Қишки иш бекор турмаслик учун, катта фойда учун эмас.'), SMALL))

    f.append(P('Что сильнее всего меняет цену' if ru else 'Нархни энг кўп нима ўзгартиради', H2))
    f.extend(bullets([
        ('Часы работы в день: 2 часа вместо 3 — себестоимость тонны %s вместо %s.' % (k(n['wi_eff2']), k(n['direct_t']))) if ru else
        ('Кунлик иш соати: 3 эмас, 2 соат бўлса — тонна таннархи %s ўрнига %s.' % (k(n['direct_t']), k(n['wi_eff2']))),
        ('Подача гранул: карбамид 26 кг/мин — %s; селитра 37 кг/мин — %s.' % (k(n['wi_flow26']), k(n['wi_flow37']))) if ru else
        ('Доналар узатилиши: карбамид 26 кг/мин — %s; селитра 37 кг/мин — %s.' % (k(n['wi_flow26']), k(n['wi_flow37']))),
        ('Всё это измеряется за 2–3 дня пробных полётов на своём поле. До них цену не печатать.') if ru else
        ('Буларнинг ҳаммаси ўз даламизда 2–3 кунлик синов парвозида ўлчанади. Унгача нархни чоп этмаслик керак.'),
    ]))
    return f


# ---------------------------------------------------------------------------
# Документ 3: коммерческое предложение
# ---------------------------------------------------------------------------
def kp(lang, n):
    p = n['p']
    ru = lang == 'ru'
    f = []
    f.append(P('Коммерческое предложение' if ru else 'Тижорий таклиф', H1))
    f.append(P(('<b>Подкормка озимой пшеницы гранулированными удобрениями с агродрона DJI Agras T40</b><br/>Сезон: февраль — март 2027. Бухарская область.') if ru else
               ('<b>Кузги буғдойни DJI Agras T40 агродрони билан донадор ўғитлар билан озиқлантириш</b><br/>Мавсум: 2027 йил февраль — март. Бухоро вилояти.'), BASE))
    f.append(Spacer(1, 3 * mm))
    f.append(P('Что мы делаем' if ru else 'Биз нима қиламиз', H2))
    f.append(P(('Вносим селитру, карбамид или аммофос по вашим посевам озимой пшеницы дроном с системой разбрасывания. Норму (кг/га) и удобрение '
                'задаёт ваш агроном — дрон вносит ровно столько, сколько задано, с шагом проходов 7 метров и контролем веса в баке.') if ru else
               ('Сизнинг кузги буғдой майдонларингизга селитра, карбамид ёки аммофосни сепиш тизимли дрон билан берамиз. Меъёрни (кг/га) ва ўғит турини '
                'сизнинг агрономингиз белгилайди — дрон айнан белгиланган миқдорни 7 метрлик қатор қадами ва бак тарозиси назорати билан сепади.'), BASE))
    f.append(P('Почему дроном' if ru else 'Нега дрон билан', H2))
    f.extend(bullets([
        '<b>Вовремя.</b> Работаем по влажной и талой почве в феврале–марте, когда трактор режет колею или не может войти в поле.' if ru else
        '<b>Ўз вақтида.</b> Февраль–мартда нам ва эриган тупроқда, трактор из қолдирган ёки далага кира олмаган пайтда ишлаймиз.',
        '<b>Без потерь посевов.</b> Нет колеи, нет вытоптанных полос.' if ru else
        '<b>Экинга зарарсиз.</b> Ғилдирак изи ва босилган йўлаклар қолмайди.',
        '<b>Ровно.</b> Подача по весовому датчику, маршрут по спутнику, норма одинакова по всему полю.' if ru else
        '<b>Текис.</b> Сепиш тарози датчиги бўйича, йўналиш йўлдош бўйича, меъёр бутун дала бўйлаб бир хил.',
        '<b>Прозрачно.</b> По каждому полю — отчёт: дата, гектары, килограммы, карта вылетов.' if ru else
        '<b>Очиқ ҳисоб.</b> Ҳар бир дала бўйича ҳисобот: сана, гектар, килограмм, парвозлар харитаси.',
    ]))
    f.append(P('Цена' if ru else 'Нарх', H2))
    f.append(P(('<b>%s сум за тонну внесённого удобрения.</b> Одна цена для любого гранулированного удобрения. Без стоимости удобрения — его закупаете вы.'
                % fmt(n['unified'])) if ru else
               ('<b>Сепилган ҳар бир тонна ўғит учун %s сўм.</b> Ҳар қандай донадор ўғит учун бир хил нарх. Ўғит нархи кирмайди — ўғитни ўзингиз оласиз.'
                % fmt(n['unified'])), BIG))
    f.append(Spacer(1, 2 * mm))
    rows = [[('Если норма' if ru else 'Агар меъёр'), ('150 кг/га'), ('200 кг/га'), ('250 кг/га')],
            [('то гектар стоит' if ru else 'гектар нархи'), '%s сум' % fmt(n['unified'] * 0.15) if ru else '%s сўм' % fmt(n['unified'] * 0.15),
             '%s сум' % fmt(n['unified'] * 0.2) if ru else '%s сўм' % fmt(n['unified'] * 0.2), '%s сум' % fmt(n['unified'] * 0.25) if ru else '%s сўм' % fmt(n['unified'] * 0.25)]]
    f.append(table(rows, [45 * mm, 43 * mm, 43 * mm, 44 * mm]))
    f.append(Spacer(1, 2 * mm))
    f.extend(bullets([
        ('Минимальный заказ — %s тонны удобрения за один выезд (это 10 га при 200 кг/га).' % fmt(n['min_t'])) if ru else
        ('Энг кам буюртма — бир чиқишда %s тонна ўғит (200 кг/га меъёрда бу 10 га).' % fmt(n['min_t'])),
        'Считаем по мешкам, принятым на поле: пересчитываем вместе при приёмке и по окончании, подписываем акт. Вес по датчику дрона и карта вылетов прилагаются к акту.' if ru else
        'Ҳисоб далада қабул қилинган қоплар бўйича: қабулда ва иш тугагач биргаликда санаймиз, далолатнома имзолаймиз. Дрон датчиги бўйича вазн ва парвозлар харитаси далолатномага илова қилинади.',
        'Оплата — наличными, перечислением или по справке с печатью, по договорённости.' if ru else
        'Тўлов — нақд, ўтказма ёки муҳрли маълумотнома бўйича, келишув асосида.',
    ]))
    f.append(P('Условия' if ru else 'Шартлар', H2))
    f.extend(bullets([
        'Удобрение вы привозите к краю поля в мешках: сухое, гранулированное (гранулы 1–5 мм), без слежавшихся комков. Сырое и пылящее удобрение к внесению не берём.' if ru else
        'Ўғитни дала четига қопларда сиз олиб келасиз: қуруқ, донадор (дона 1–5 мм), қотиб қолган бўлаксиз. Нам ва чангийдиган ўғитни сепишга олмаймиз.',
        'Работаем в светлое время при температуре выше 0 °C, ветре до 6 м/с и без дождя. В непогоду переносим на ближайший день.' if ru else
        'Ёруғ вақтда, ҳарорат 0 °C дан юқори, шамол 6 м/с гача ва ёмғирсиз об-ҳавода ишлаймиз. Ёмон об-ҳавода яқин кунга кўчирамиз.',
        'Вы показываете границы поля и даёте подъезд для машины сопровождения.' if ru else
        'Сиз дала чегарасини кўрсатасиз ва ҳамроҳ машина учун йўл берасиз.',
        'Выезд в радиусе 50 км от Бухары включён в цену.' if ru else
        'Бухородан 50 км радиусда чиқиш нархга киритилган.',
        'Заявки принимаем с 1 декабря 2026, очередь — по дате заявки.' if ru else
        'Буюртмалар 2026 йил 1 декабрдан қабул қилинади, навбат — буюртма санаси бўйича.',
    ]))
    f.append(P('Также для озимой пшеницы' if ru else 'Кузги буғдой учун яна', H2))
    f.append(P(('Опрыскивание дроном (гербициды, жидкие подкормки) в ноябре и феврале–марте — <b>%s сум за гектар</b>, препарат ваш.' % fmt(p['liquid_tariff_external'])) if ru else
               ('Дрон билан пуркаш (гербицидлар, суюқ озиқлантириш) ноябрда ва февраль–мартда — <b>гектарига %s сўм</b>, препарат сизники.' % fmt(p['liquid_tariff_external'])), BASE))
    f.append(Spacer(1, 4 * mm))
    f.append(P('Как заказать' if ru else 'Қандай буюртма бериш', H2))
    f.append(P(('Позвоните или напишите, назовите поле, площадь, удобрение и норму. Мы назовём день и приедем на осмотр.<br/><br/>'
                'Организация: ____________________________<br/>Ответственный: ____________________________<br/>Телефон: ____________________________<br/>Telegram: ____________________________') if ru else
               ('Қўнғироқ қилинг ёки ёзинг: далани, майдонни, ўғит турини ва меъёрни айтинг. Биз кунни айтамиз ва кўриб чиқишга келамиз.<br/><br/>'
                'Ташкилот: ____________________________<br/>Масъул шахс: ____________________________<br/>Телефон: ____________________________<br/>Telegram: ____________________________'), BASE))
    f.append(Spacer(1, 3 * mm))
    f.append(P(('Цены действуют на сезон февраль — март 2027 и уточняются после пробных полётов.' if ru else
               'Нархлар 2027 йил февраль — март мавсумига амал қилади ва синов парвозларидан кейин аниқлаштирилади.'), SMALL))
    return f


# ---------------------------------------------------------------------------
# Документ 4: справка для руководства по госпрограммам
# ---------------------------------------------------------------------------
def gov_brief(lang, n):
    ru = lang == 'ru'
    f = []
    f.append(P('Госпрограммы, под которые можно продать зимнюю работу дронов' if ru else 'Дронларнинг қишки ишини сотиш мумкин бўлган давлат дастурлари', H1))
    f.append(P(('Справка для руководства. 30.10.2026. Собрано по открытым источникам: сайты госорганов, lex.uz, новости. Телефоны и номера документов '
                'перед обращением сверить — страницы читались в пересказе, не напрямую.') if ru else
               ('Раҳбарият учун маълумотнома. 30.10.2026. Очиқ манбалардан тўпланган: давлат органлари сайтлари, lex.uz, янгиликлар. Телефон ва ҳужжат '
                'рақамларини мурожаатдан олдин текшириш керак — саҳифалар бевосита эмас, баён орқали ўқилган.'), SMALL))
    f.append(Spacer(1, 3 * mm))
    f.append(P('Главное' if ru else 'Асосийси', H2))
    f.extend(bullets([
        '<b>Пастбища.</b> Постановление Президента ПП-322 от 31.10.2025 запустило программу восстановления пастбищ. В её описании прямо названы «высев семян дронами» и семеноводство пустынных растений. Цели: 620 тыс. га в 2026, ещё 3,7 млн га в 2027; 627 тыс. га отдают махаллям на 49 лет; 63 млн долларов от Азиатского банка развития. Ведёт Минсельхоз.' if ru else
        '<b>Яйловлар.</b> Президентнинг 31.10.2025 йилдаги ПҚ-322 қарори яйловларни тиклаш дастурини бошлади. Унинг таърифида «уруғни дронлар билан сепиш» ва чўл ўсимликлари уруғчилиги тўғридан-тўғри айтилган. Мақсад: 2026 йилда 620 минг га, 2027 йилда яна 3,7 млн га; 627 минг га маҳаллаларга 49 йилга берилади; Осиё тараққиёт банкидан 63 млн доллар. Қишлоқ хўжалиги вазирлиги олиб боради.',
        '<b>Леса и «Яшил макон».</b> Агентство по лесам и зелёным зонам, борьбе с опустыниванием (бывшее лесное агентство) и Бухарское гослесхозяйство. В области заложено 58 тыс. га защитных лесов в пустыне; проект «Яшил белбоғ» — 250 тыс. га в Кызылкуме и Каракумах, первая очередь 109 тыс. га вдоль Аму-Бухарского канала. Осенний сезон посадок в Бухаре — с 5 ноября (указ УП-206 от 23.09.2026).' if ru else
        '<b>Ўрмонлар ва «Яшил макон».</b> Ўрмон ва яшил ҳудудларни кўпайтириш, чўлланишга қарши курашиш агентлиги (аввалги ўрмон агентлиги) ва Бухоро давлат ўрмон хўжалиги. Вилоятда чўлда 58 минг га ҳимоя ўрмонзори белгиланган; «Яшил белбоғ» лойиҳаси — Қизилқум ва Қорақумда 250 минг га, биринчи босқич Аму-Бухоро канали бўйлаб 109 минг га. Бухорода кузги экиш мавсуми — 5 ноябрдан (23.09.2026 йилдаги ПФ-206 фармони).',
        '<b>Международные проекты рядом.</b> ГЭФ/ФАО «Устойчивое управление лесами и пастбищами» (3,8 млн долларов) работает в Жондорском районе Бухары и в Нурате. ПРООН «Зелёный Арал» — посадки саксаула в Аралкуме. Всемирный банк RESILAND (153 млн долларов) — шесть областей, Бухары среди них нет.' if ru else
        '<b>Яқиндаги халқаро лойиҳалар.</b> ГЭФ/ФАО «Ўрмон ва яйловларни барқарор бошқариш» (3,8 млн доллар) Бухоронинг Жондор туманида ва Нуротада ишлайди. БМТТД «Яшил Орол» — Оролқумда саксовул экиш. Жаҳон банки RESILAND (153 млн доллар) — олти вилоят, Бухоро улар ичида йўқ.',
        '<b>Канал продажи.</b> По указу УП-130 от 12.08.2025 заявки, договоры и оплата дрон-услуг для сельхозорганизаций идут через Агропортал Минсельхоза; разрешения на полёты — годовые, на согласованные территории. Нам нужно зарегистрироваться как поставщику услуг.' if ru else
        '<b>Сотиш канали.</b> 12.08.2025 йилдаги ПФ-130 фармонига кўра қишлоқ хўжалиги ташкилотлари учун дрон хизматларига ариза, шартнома ва тўлов Қишлоқ хўжалиги вазирлигининг Агропортали орқали ўтади; парвоз рухсатномалари — йиллик, келишилган ҳудудларга. Биз хизмат кўрсатувчи сифатида рўйхатдан ўтишимиз керак.',
        '<b>Защита растений.</b> Агентство по карантину и защите растений с марта ведёт обработки против саранчи (план 2026 — 651,8 тыс. га по стране, Бухара в списке). Цены госзаказа низкие; возможен субподряд на труднодоступных песках.' if ru else
        '<b>Ўсимликлар ҳимояси.</b> Ўсимликлар карантини ва ҳимояси агентлиги мартдан чигирткага қарши ишлов беради (2026 йил режаси — мамлакат бўйича 651,8 минг га, Бухоро рўйхатда). Давлат буюртмаси нархлари паст; қийин етиб бориладиган қумларда субпудрат мумкин.',
    ]))
    f.append(P('Таблица программ' if ru else 'Дастурлар жадвали', H2))
    if ru:
        rows = [['Программа и документ', 'Кто отвечает', 'Что там для нас', 'Куда обращаться'],
                ['Восстановление пастбищ. ПП-322 от 31.10.2025; презентация Президенту 13.10.2026', 'Минсельхоз, хокимияты, махалли; база данных пастбищ — институт «Уздаверлойиха»; деньги АБР', 'Посев семян изеня, саксаула, черкеза дроном на участках махаллей и пользователей пастбищ. Сезон ноябрь–март', 'Областное управление сельского хозяйства Бухары (тел. 65 225-13-45 по справочнику); Бухарский центр семеноводства пустынно-пастбищных растений; институт «Уздаверлойиха»'],
                ['Лесоразведение и «Яшил макон». УП-206 от 23.09.2026; план лесов 2026–2030 (1,27 млн га)', 'Агентство по лесам и зелёным зонам (телефон доверия 71 207-88-77); 66 лесхозов; Бухарское гослесхозяйство; хокимият', '58 тыс. га защитных лесов в пустыне; «Яшил белбоғ» 250 тыс. га; подсев там, куда не летит самолёт. Сезон ноябрь–февраль и весна', 'Агентство (1010); хокимият области (65 222-22-22); приёмные районов: Караулбазар 65 364-10-00, Каракуль 65 565-10-00, Пешку 65 353-10-00'],
                ['ГЭФ/ФАО: леса и пастбища в засушливых районах, 3,8 млн $', 'ФАО (офис в Ташкенте), Минсельхоз, Агентство', 'Пилотные площадки в Жондоре и Нурате; показать дроновый посев на пилоте проекта', 'Представительство ФАО в Узбекистане; группа управления проектом'],
                ['ПРООН «Зелёный Арал»; Всемирный банк RESILAND (153 млн $)', 'ПРООН; Агентство по лесам (проектная группа ВБ)', 'Посев саксаула в Аралкуме; лоты по лесопосадкам в Кашкадарье, Самарканде и др.', 'Порталы закупок ПРООН (UNGM) и Всемирного банка (STEP)'],
                ['Агропортал Минсельхоза. УП-130 от 12.08.2025; правила утверждены в июле 2026', 'Минсельхоз; центр дрон-услуг при «Уздаверлойиха» (100+ дронов, по два мобильных комплекса на область)', 'Регистрация поставщиком дрон-услуг; субподряд у госцентра в пик; расширение территорий в разрешениях', 'Агропортал; институт «Уздаверлойиха»; облуправление сельского хозяйства'],
                ['Господдержка агросектора. ПКМ-206 от 28.04.2026', 'Минсельхоз; Агентство по платежам в аграрной сфере', 'Субсидии на технику, саженцы, шпалеры, автопилоты; дроны прямо не названы — спросить', 'Агентство по платежам в аграрной сфере'],
                ['Защита растений: саранча, план 2026 — 651,8 тыс. га', 'Агентство по карантину и защите растений, Бухарское управление (с МЧС)', 'Субподряд на обработку песков и неудобий в марте–мае', 'Бухарское управление агентства через хокимият; портал госзакупок'],
                ['Правила полётов. ПКМ-658 от 15.11.2022; новый указ о дронах ожидался к лету 2026', 'Узавиация, Мининфоком, Минобороны, Минтранс', 'Проверить, что годовые разрешения покрывают пустыню, лесхозы и работы для госорганов', 'Узавиация']]
    else:
        rows = [['Дастур ва ҳужжат', 'Ким жавоб беради', 'Биз учун нима бор', 'Қаерга мурожаат қилиш'],
                ['Яйловларни тиклаш. 31.10.2025 йилдаги ПҚ-322; Президентга тақдимот 13.10.2026', 'Қишлоқ хўжалиги вазирлиги, ҳокимликлар, маҳаллалар; яйловлар базаси — «Ўздаверлойиҳа» институти; ОТБ маблағи', 'Маҳалла ва яйлов фойдаланувчилари ерларида изен, саксовул, черкез уруғини дрон билан сепиш. Мавсум ноябрь–март', 'Бухоро вилояти қишлоқ хўжалиги бошқармаси (маълумотнома бўйича тел. 65 225-13-45); Бухоро чўл яйлов ўсимликлари уруғчилик маркази; «Ўздаверлойиҳа» институти'],
                ['Ўрмон барпо этиш ва «Яшил макон». 23.09.2026 йилдаги ПФ-206; 2026–2030 ўрмон режаси (1,27 млн га)', 'Ўрмон ва яшил ҳудудлар агентлиги (ишонч телефони 71 207-88-77); 66 ўрмон хўжалиги; Бухоро давлат ўрмон хўжалиги; ҳокимлик', 'Чўлда 58 минг га ҳимоя ўрмонзори; «Яшил белбоғ» 250 минг га; самолёт учмайдиган жойларда уруғ сепиш. Мавсум ноябрь–февраль ва баҳор', 'Агентлик (1010); вилоят ҳокимлиги (65 222-22-22); туман қабулхоналари: Қоровулбозор 65 364-10-00, Қоракўл 65 565-10-00, Пешку 65 353-10-00'],
                ['ГЭФ/ФАО: қурғоқчил ҳудудларда ўрмон ва яйловлар, 3,8 млн $', 'ФАО (Тошкентдаги идора), Қишлоқ хўжалиги вазирлиги, Агентлик', 'Жондор ва Нуротадаги синов майдонлари; лойиҳа синовида дрон билан уруғ сепишни кўрсатиш', 'ФАОнинг Ўзбекистондаги ваколатхонаси; лойиҳа бошқаруви гуруҳи'],
                ['БМТТД «Яшил Орол»; Жаҳон банки RESILAND (153 млн $)', 'БМТТД; Ўрмон агентлиги (ЖБ лойиҳа гуруҳи)', 'Оролқумда саксовул экиш; Қашқадарё, Самарқанд ва бошқа вилоятларда ўрмон экиш лотлари', 'БМТТД (UNGM) ва Жаҳон банки (STEP) харид порталлари'],
                ['Қишлоқ хўжалиги вазирлиги Агропортали. 12.08.2025 йилдаги ПФ-130; қоидалар 2026 йил июлда тасдиқланган', 'Вазирлик; «Ўздаверлойиҳа» қошидаги дрон хизматлари маркази (100+ дрон, ҳар вилоятда икки мобил гуруҳ)', 'Дрон хизматлари етказиб берувчиси сифатида рўйхатдан ўтиш; авж палласида давлат марказидан субпудрат; рухсатномалардаги ҳудудларни кенгайтириш', 'Агропортал; «Ўздаверлойиҳа» институти; вилоят қишлоқ хўжалиги бошқармаси'],
                ['Аграр соҳани давлат қўллаб-қувватлаши. 28.04.2026 йилдаги ВМҚ-206', 'Вазирлик; Аграр соҳада тўловлар агентлиги', 'Техника, кўчат, шпалера, автопилотга субсидиялар; дронлар тўғридан-тўғри айтилмаган — сўраш керак', 'Аграр соҳада тўловлар агентлиги'],
                ['Ўсимликлар ҳимояси: чигиртка, 2026 режаси — 651,8 минг га', 'Ўсимликлар карантини ва ҳимояси агентлиги, Бухоро бошқармаси (ФВВ билан)', 'Март–майда қум ва ноқулай жойларда ишлов бериш субпудрати', 'Агентликнинг Бухоро бошқармаси — ҳокимлик орқали; давлат харидлари портали'],
                ['Парвоз қоидалари. 15.11.2022 йилдаги ВМҚ-658; дронлар тўғрисида янги фармон 2026 йил ёзига кутилган эди', 'Ўзавиация, Рақамли технологиялар вазирлиги, Мудофаа, Транспорт вазирликлари', 'Йиллик рухсатномалар чўл, ўрмон хўжаликлари ва давлат органлари учун ишларни қамраб олишини текшириш', 'Ўзавиация']]
    f.append(table(rows, [44 * mm, 40 * mm, 48 * mm, 43 * mm], font_size=8))
    f.append(P('Что нужно подготовить' if ru else 'Нимани тайёрлаш керак', H2))
    f.extend(bullets([
        'Профиль исполнителя: 15 дронов T40, бак-разбрасыватель, экипажи; статистика сезона из нашей программы учёта (гектары и вылеты по месяцам); действующие разрешения на полёты.' if ru else
        'Ижрочи профили: 15 та T40 дрон, сепиш баки, экипажлар; ҳисоб дастуримиздан мавсум статистикаси (ойлар бўйича гектар ва парвозлар); амалдаги парвоз рухсатномалари.',
        'Предложение показательного посева 20–50 га за свой счёт с отчётом по карте полётов — снимает вопрос «а вы умеете?» без конкурса.' if ru else
        '20–50 га намунали экишни ўз ҳисобимиздан парвоз харитаси бўйича ҳисобот билан таклиф қилиш — «қила оласизми?» саволини танловсиз ёпади.',
        'Прайс: удобрения — одна цена за тонну; посев семян — за гектар; опрыскивание — за гектар.' if ru else
        'Нархнома: ўғит — тоннага бир нарх; уруғ сепиш — гектарга; пуркаш — гектарга.',
        'Регистрации: Агропортал, портал госзакупок xarid.uzex.uz (юрлицо, электронная подпись), порталы ПРООН и Всемирного банка.' if ru else
        'Рўйхатдан ўтиш: Агропортал, давлат харидлари портали xarid.uzex.uz (юридик шахс, электрон имзо), БМТТД ва Жаҳон банки порталлари.',
        'Проверка семян в баке: течёт ли лёгкое семя саксаула и изеня через малую заслонку, нужно ли дражирование.' if ru else
        'Уруғни бакда синаш: саксовул ва изеннинг енгил уруғи кичик тўсиқдан ўтадими, дражирлаш керакми.',
    ]))
    f.append(P('Первые шаги' if ru else 'Биринчи қадамлар', H2))
    f.extend(bullets([
        'Ноябрь: звонок в Агентство по лесам (1010) и в хокимият за контактом Бухарского гослесхозяйства; письма с предложением пилота в гослесхозяйство, облуправление сельского хозяйства и в проект ФАО (Жондор).' if ru else
        'Ноябрь: Ўрмон агентлигига (1010) ва ҳокимликка Бухоро давлат ўрмон хўжалиги алоқаси учун қўнғироқ; ўрмон хўжалигига, вилоят қишлоқ хўжалиги бошқармасига ва ФАО лойиҳасига (Жондор) синов таклифи билан хатлар.',
        'Ноябрь: пробные полёты на своём поле, тест семян в баке, регистрация на Агропортале и портале госзакупок.' if ru else
        'Ноябрь: ўз даламизда синов парвозлари, уруғни бакда синаш, Агропортал ва давлат харидлари порталида рўйхатдан ўтиш.',
        'Ноябрь–декабрь: показательный посев у лесхоза или на площадке ФАО в сезон осенних посадок.' if ru else
        'Ноябрь–декабрь: кузги экиш мавсумида ўрмон хўжалиги ёки ФАО майдонида намунали экиш.',
        'Декабрь–январь: договоры на февраль–март; заявки на лоты лесхозов и программы пастбищ.' if ru else
        'Декабрь–январь: февраль–март учун шартномалар; ўрмон хўжаликлари ва яйлов дастури лотларига аризалар.',
    ]))
    f.append(P('Риски' if ru else 'Хавфлар', H2))
    f.extend(bullets([
        'Госзаказ платит по сметным нормам, а не по нашей калькуляции, и платит медленно. Прежде чем входить в лот, узнать ставку сметы.' if ru else
        'Давлат буюртмаси бизнинг ҳисобимиз бўйича эмас, смета нормалари бўйича ва секин тўлайди. Лотга киришдан олдин смета ставкасини аниқлаш керак.',
        'У Минсельхоза свой парк: 100+ дронов и мобильные группы в областях. Предлагать субподряд, а не конкуренцию.' if ru else
        'Вазирликнинг ўз парки бор: 100+ дрон ва вилоятларда мобил гуруҳлар. Рақобат эмас, субпудрат таклиф қилиш.',
        'Разрешения на полёты выданы на согласованные территории; пустыня и лесхозы в них, скорее всего, не входят.' if ru else
        'Парвоз рухсатномалари келишилган ҳудудларга берилган; чўл ва ўрмон хўжаликлари уларга, эҳтимол, кирмайди.',
        'Лёгкие семена саксаула могут не течь через заслонку без дражирования.' if ru else
        'Саксовулнинг енгил уруғи дражирлашсиз тўсиқдан ўтмаслиги мумкин.',
    ]))
    return f


# ---------------------------------------------------------------------------
# Печать и проверка
# ---------------------------------------------------------------------------
DOCS = [('calc_detailed', calc_detailed), ('calc_short', calc_short), ('kp', kp), ('gov_brief', gov_brief)]


def build_all():
    os.makedirs(OUT_DIR, exist_ok=True)
    n = numbers()
    written = []
    for name, fn in DOCS:
        for lang in ('ru', 'uz'):
            path = os.path.join(OUT_DIR, '%s_%s.pdf' % (name, lang))
            doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                                    topMargin=16 * mm, bottomMargin=16 * mm,
                                    title='%s_%s' % (name, lang), author='Buxoro Agroklaster')
            doc.build(fn(lang, n))
            written.append(path)
    return written


def verify(paths):
    try:
        from pypdf import PdfReader
    except Exception:
        print('VERIFY: pypdf not available, skipped')
        return 0
    bad = 0
    for path in paths:
        raw = open(path, 'rb').read()
        emb = b'/FontFile2' in raw
        reader = PdfReader(path)
        text = ''.join((pg.extract_text() or '') for pg in reader.pages)
        uz = path.endswith('_uz.pdf')
        glyphs_ok = (not uz) or all(ch in text for ch in 'ўғқҳ')
        # слово «сум/сўм» обязано быть в калькуляциях и КП; справка по программам считает в долларах
        needs_sum = not os.path.basename(path).startswith('gov_brief')
        sum_ok = (not needs_sum) or (('сўм' in text) if uz else ('сум' in text))
        ok = emb and glyphs_ok and sum_ok and len(reader.pages) >= 1
        bad += 0 if ok else 1
        print('%s: pages=%d fonts_embedded=%s uz_glyphs=%s text=%d chars %s' % (
            os.path.basename(path), len(reader.pages), emb, glyphs_ok, len(text), 'OK' if ok else 'FAIL'))
    print('VERIFY: %d file(s), %d failed' % (len(paths), bad))
    return 1 if bad else 0


if __name__ == '__main__':
    files = build_all()
    for f_ in files:
        print('written: %s' % f_)
    sys.exit(verify(files))
