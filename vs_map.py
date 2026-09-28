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
             ключ; бесплатно -- 2 000 000 плиток в месяц. Чёткий (до 30-60 см),
             но снимок обновляется раз в месяцы-годы.
  fresh      Sentinel-2 через Copernicus Data Space Ecosystem -- ТОЛЬКО когда
             владелец положил идентификатор конфигурации в
             instance/copernicus_instance_id.txt. 10 м на пиксель, зато снимок
             раз в 2-5 суток; дата снимка подписывается под картой.

[REASON]: спутника без ключа нет намеренно. Проверено 28.09.2026, какой
спутник можно брать без учётной записи: плитки Google через Leaflet вне
условий Google; Esri требует регистрации и ключа (с 2022 года прежний адрес
без ключа -- вне условий); сервис EOX Sentinel-2 бесплатен только для
некоммерческого использования, и разрешение там 10 м. Взять любой из них по
умолчанию значило бы принять чужие условия за владельца. Поэтому подложка по
умолчанию -- OSM, а спутник включается файлом ключа, и экран пишет, почему
спутника нет. Свежий Sentinel-2 берётся не у EOX, а у самого Copernicus: его
данные открыты для любого использования, а сервис бесплатен в пределах квоты
учётной записи.

Ключ не печатается и не пишется в журналы. В страницу он попадает -- так
устроены браузерные ключи; от чужого использования его защищает ограничение
по адресу сайта в кабинете Esri, а не секретность.
"""

import os
import re

from datetime import date, timedelta

ROOT = os.path.dirname(os.path.abspath(__file__))
ESRI_KEY_FILE = os.path.join(ROOT, 'instance', 'esri_api_key.txt')

# ── Свежий снимок: Sentinel-2 через Copernicus Data Space Ecosystem ─────────
#
# [REASON]: владелец 28.09.2026 -- «спутниковая подложка нужна обязательно и
# желательно с самыми свежими снимками». Чёткие подложки (Esri, Google,
# Mapbox) обновляются раз в месяцы-годы: Esri берёт у Maxar обновления раз в
# год, 60 см и крупнее вне городов. Свежесть в ДНЯХ бесплатно даёт только
# Sentinel-2: 10 м на пиксель, снимок раз в 2-5 суток, данные Copernicus
# открыты для любого использования, включая коммерческое. Поэтому подложек
# две: чёткая, но старая (Esri) и свежая, но в 10 м (Sentinel-2), -- и
# человек переключает их на той же карте.
#
# Сервис -- Sentinel Hub в Copernicus Data Space Ecosystem: бесплатная
# учётная запись даёт 10 000 единиц обработки в месяц. Экран карты -- около
# 1 Мп плиток, то есть порядка 4-5 единиц за открытие со свежим слоем: около
# двух тысяч открытий в месяц. Запросы идут из браузера по идентификатору
# «конфигурации» (instance) -- его, как и ключ Esri, видно в странице;
# защищает его то, что расход ограничен квотой учётной записи.
COPERNICUS_INSTANCE_FILE = os.path.join(ROOT, 'instance',
                                        'copernicus_instance_id.txt')
COPERNICUS_WMS_URL = 'https://sh.dataspace.copernicus.eu/ogc/wms/'
COPERNICUS_WFS_URL = 'https://sh.dataspace.copernicus.eu/ogc/wfs/'
# Слой истинных цветов в стандартных шаблонах конфигурации. Другой -- второй
# строкой файла идентификатора.
SENTINEL_LAYER = 'TRUE_COLOR'
# Тип данных для WFS: Sentinel-2 L2A -- тот же уровень, что у слоя.
SENTINEL_WFS_TYPE = 'DSS2'
# [REASON]: облачность снимка задана на весь квадрат Sentinel-2 (100 км), а не
# на поле; 30% отсекает пасмурные дни и оставляет те, где облако ушло в угол
# квадрата. Порог про картинку, а не про учёт: на числа он не влияет.
SENTINEL_MAX_CLOUD = 30
# [REASON]: окно дат вокруг суток работы. Назад -- месяц: в пасмурную неделю
# найдётся снимок раньше. Вперёд -- две недели: снимок ПОСЛЕ работы
# показывает её результат (вспаханное, засеянное), и из окна берётся самый
# свежий. Для вчерашних суток окно кончается сегодня -- это и есть «самый
# свежий снимок».
SENTINEL_DAYS_BEFORE = 30
SENTINEL_DAYS_AFTER = 15

_INSTANCE_RE = re.compile(r'^[A-Za-z0-9-]{8,64}$')
_LAYER_RE = re.compile(r'^[A-Za-z0-9_-]{1,64}$')

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


def copernicus_instance(path=None):
    """(идентификатор конфигурации, слой) из файла, или ('', '') без него.

    Первая непустая строка -- идентификатор, вторая (необязательная) -- имя
    слоя, если в конфигурации он называется не TRUE_COLOR. Что не похоже на
    идентификатор (пробелы, кириллица, лишний текст при копировании) --
    свежий слой не включается, а страница открывается как без него.
    """
    try:
        with open(path or COPERNICUS_INSTANCE_FILE, encoding='utf-8-sig') as handle:
            lines = [line.strip() for line in handle if line.strip()]
    except OSError:
        return '', ''
    if not lines or not _INSTANCE_RE.match(lines[0]):
        return '', ''
    layer = lines[1] if len(lines) > 1 else SENTINEL_LAYER
    if not _LAYER_RE.match(layer):
        return '', ''
    return lines[0], layer


def sentinel_window(day, today=None):
    """(с, по) -- окно дат снимка для суток `day`; «по» не позже сегодня."""
    today = today or date.today()
    start = day - timedelta(days=SENTINEL_DAYS_BEFORE)
    end = min(day + timedelta(days=SENTINEL_DAYS_AFTER), today)
    if end < start:
        end = start
    return start, end


def fresh_layer(is_ru, instance, layer, day, today=None):
    """Подложка «свежий снимок»: WMS Sentinel Hub и запрос дат через WFS."""
    start, end = sentinel_window(day, today)
    window = '%s/%s' % (start.isoformat(), end.isoformat())
    period = '%s–%s' % (start.strftime('%d.%m.%Y'), end.strftime('%d.%m.%Y'))
    return {
        'key': 'fresh', 'kind': 'wms',
        'title': ('Свежий снимок (Sentinel-2, 10 м)' if is_ru
                  else 'Янги сурат (Sentinel-2, 10 м)'),
        'url': COPERNICUS_WMS_URL + instance,
        'wms': {'layers': layer, 'format': 'image/jpeg', 'version': '1.3.0',
                'time': window, 'maxcc': SENTINEL_MAX_CLOUD,
                'priority': 'mostRecent', 'showlogo': 'false'},
        'dates': {'url': COPERNICUS_WFS_URL + instance,
                  'typename': SENTINEL_WFS_TYPE, 'time': window,
                  'maxcc': SENTINEL_MAX_CLOUD},
        # [REASON]: подписи готовятся здесь, на языке интерфейса: vs-map.js
        # только подставляет дату и сам ничего не переводит.
        'notes': {
            'found': ('Свежий снимок Sentinel-2 — от {date}; включается в '
                      'переключателе слоёв карты' if is_ru else
                      'Янги Sentinel-2 сурати — {date} санадаги; харита '
                      'қатламлари алмаштиргичида ёқилади'),
            'none': ('Безоблачного снимка Sentinel-2 за %s нет' % period
                     if is_ru else
                     '%s давомида булутсиз Sentinel-2 сурати йўқ' % period),
            'unknown': ('Дату снимка Sentinel-2 узнать не удалось — показан '
                        'самый свежий за %s' % period if is_ru else
                        'Sentinel-2 сурати санасини аниқлаб бўлмади — %s '
                        'давомидаги энг янгиси кўрсатилган' % period)},
        'attribution': ('Contains modified Copernicus Sentinel data %d | '
                        '<a href="https://dataspace.copernicus.eu">Copernicus '
                        'Data Space Ecosystem</a>' % end.year),
        'maxZoom': MAX_ZOOM}


def base_layers(is_ru, key_path=None, instance_path=None, day=None,
                today=None):
    """Подложки для vs-map.js: сначала та, что открывается по умолчанию.

    [REASON]: по умолчанию -- чёткая Esri, если она есть: на масштабе поля
    10-метровый снимок размыт, и трек с участками на нём читаются хуже. Свежий
    Sentinel-2 -- в один щелчок, и его дата стоит под картой. Каждое открытие
    свежего слоя тратит квоту Copernicus, чёткий -- нет; поэтому свежий
    становится первым только там, где чёткого нет.
    """
    layers = []
    key = esri_key(key_path)
    if key:
        layers.append({'key': 'satellite',
                       'title': ('Спутник (чёткий, Esri)' if is_ru
                                 else 'Спутник (тиниқ, Esri)'),
                       'url': ESRI_IMAGERY_URL + key,
                       'attribution': ESRI_ATTRIBUTION,
                       'maxZoom': MAX_ZOOM})
    instance, layer = copernicus_instance(instance_path)
    if instance and day is not None:
        layers.append(fresh_layer(is_ru, instance, layer, day, today))
    layers.append({'key': 'map',
                   'title': 'Карта' if is_ru else 'Харита',
                   'url': OSM_URL,
                   'attribution': OSM_ATTRIBUTION,
                   'maxZoom': MAX_ZOOM})
    return layers


def satellite_configured(key_path=None, instance_path=None):
    """Есть ли хоть одна спутниковая подложка -- чёткая или свежая."""
    return bool(esri_key(key_path)) or bool(copernicus_instance(instance_path)[0])
