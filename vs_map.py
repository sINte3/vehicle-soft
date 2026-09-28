# -*- coding: utf-8 -*-
"""vs_map.py -- серверная половина общей карты программы (static/js/vs-map.js).

ОДНА КАРТА НА ВСЮ ПРОГРАММУ
GPS (трек, участки), Дроны (покрытие, поля) и будущие рейсы (посещения
геозон) показывают одно и то же: контуры полей, треки и полигоны на одной
земле, а справочник контуров `field_contours` у треков уже общий. Поэтому
компонент один: `static/vendor/leaflet/` (библиотека, в репозитории, без CDN),
`static/js/vs-map.js` (разметка -> карта), `static/css/vs-map.css` (стили
токенами дизайн-системы) и этот модуль -- какие подложки есть и откуда они.
Согласие владельца на Leaflet -- 28.09.2026, запись в
docs/tracks/gps-plan-fakt.md, раздел «Карта: одна на всю программу».

ПОДЛОЖКИ И ИХ УСЛОВИЯ
Плитки грузит БРАУЗЕР оператора из интернета; сервер в интернет не ходит.

  map        OpenStreetMap. Есть всегда. Политика плиток OSM допускает
             просмотр человеком с подписью «© OpenStreetMap»; массовая
             выгрузка и офлайн запрещены -- компонент ничего такого не делает.
  satellite  Esri World Imagery -- ТОЛЬКО когда владелец положил ключ в
             instance/esri_api_key.txt. Esri требует учётную запись ArcGIS и
             ключ; бесплатно -- 2 000 000 плиток в месяц.

[REASON]: спутника без ключа нет намеренно. Проверено 28.09.2026, какой
спутник можно брать без учётной записи: плитки Google через Leaflet вне
условий Google; Esri требует регистрации и ключа (с 2022 года прежний адрес
без ключа -- вне условий); сервис EOX Sentinel-2 бесплатен только для
некоммерческого использования, и разрешение там 10 м. Взять любой из них по
умолчанию значило бы принять чужие условия за владельца. Поэтому подложка по
умолчанию -- OSM, а спутник включается файлом ключа, и экран пишет, почему
спутника нет.

Ключ не печатается и не пишется в журналы. В страницу он попадает -- так
устроены браузерные ключи; от чужого использования его защищает ограничение
по адресу сайта в кабинете Esri, а не секретность.
"""

import os

ROOT = os.path.dirname(os.path.abspath(__file__))
ESRI_KEY_FILE = os.path.join(ROOT, 'instance', 'esri_api_key.txt')

OSM_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
OSM_ATTRIBUTION = ('&copy; <a href="https://www.openstreetmap.org/copyright">'
                   'OpenStreetMap</a>')

# [REASON]: адрес World Imagery для ключа -- ibasemaps-api, а не прежний
# server.arcgisonline.com: второй работает без ключа и потому вне условий Esri.
ESRI_IMAGERY_URL = ('https://ibasemaps-api.arcgis.com/arcgis/rest/services/'
                    'World_Imagery/MapServer/tile/{z}/{y}/{x}?token=')
ESRI_ATTRIBUTION = ('Powered by <a href="https://www.esri.com">Esri</a> | '
                    'Esri, Maxar, Earthstar Geographics, and the GIS User '
                    'Community')

# [REASON]: 19 -- уровень, до которого World Imagery и OSM отдают плитки по
# Бухарской области; ближе карта растягивала бы последний уровень, а не
# показывала новое.
MAX_ZOOM = 19


def esri_key(path=None):
    """Ключ Esri из файла, или '' если файла нет. Никогда не печатается.

    Первая непустая строка; BOM Блокнота снимается. Строка с пробелом внутри
    или кириллицей -- не ключ (скопировали лишнее), и спутник не включается,
    а не ломает страницу.
    """
    try:
        with open(path or ESRI_KEY_FILE, encoding='utf-8-sig') as handle:
            for line in handle:
                value = line.strip()
                if value:
                    break
            else:
                return ''
    except OSError:
        return ''
    if not value.isascii() or any(ch.isspace() for ch in value):
        return ''
    return value


def base_layers(is_ru, key_path=None):
    """Подложки для vs-map.js: сначала та, что открывается по умолчанию."""
    layers = []
    key = esri_key(key_path)
    if key:
        layers.append({'key': 'satellite',
                       'title': 'Спутник' if is_ru else 'Сунъий йўлдош',
                       'url': ESRI_IMAGERY_URL + key,
                       'attribution': ESRI_ATTRIBUTION,
                       'maxZoom': MAX_ZOOM})
    layers.append({'key': 'map',
                   'title': 'Карта' if is_ru else 'Харита',
                   'url': OSM_URL,
                   'attribution': OSM_ATTRIBUTION,
                   'maxZoom': MAX_ZOOM})
    return layers


def satellite_configured(key_path=None):
    return bool(esri_key(key_path))
