# -*- coding: utf-8 -*-
"""drone_collector/area_study.py -- DJI-AREA-48H: чем `new_work_area`
отличается от полезно обработанной земли.

    python -m drone_collector.main --area-48h          # живой прогон
    python tools/dji_area_48h.py --replay <private>    # пересчёт без браузера

ЗАЧЕМ ЭТОТ МОДУЛЬ СУЩЕСТВУЕТ

У спора о площадях два практических вопроса:

1. почему `new_work_area` расходится с фактически обработанной площадью и
   почему одно и то же значение повторяется в нескольких строках вылетов;
2. как посчитать полезно обработанную УНИКАЛЬНУЮ площадь внутри контура поля.

Рабочая гипотеза трека (docs/tracks/drones.md §0*) -- что `new_work_area` это
длина учтённого пути, умноженная на ширину захвата, а не полигон покрытой
земли. Гипотеза не проверена: в телеметрии до сих пор не было геометрии.
Теперь она есть -- `route-decode-2` достаёт маршрут, -- и разницу можно не
предполагать, а измерить.

ЧТО СЧИТАЕТСЯ

    useful_area = area( union( buffer(work_segments, spray_width / 2) )
                        INTERSECT field_polygon )

Полосы ОБЪЕДИНЯЮТСЯ, а не складываются: два прохода по одной земле дают одну
площадь. Результат обрезается контуром: движение вне поля полезной площадью не
становится. Маршруты нескольких вылетов одной работы объединяются ДО измерения.

ПОЧЕМУ РАСТР, А НЕ ВЕКТОРНАЯ БУЛЕВА ГЕОМЕТРИЯ

`shapely` в проекте нет, а тащить её сюда нельзя дважды: устав запрещает
добавлять зависимости без нужды, и `drone_collector/` -- отдельный процесс со
своим venv, который на CPython 3.14 под Windows должен ставиться готовым
колесом. Писать же свой клиппер многоугольников (Мартинес--Руэда, Грайнер--
Хорман) -- это ровно тот «новый фреймворк», который задание запрещает: сотни
строк, вырожденные случаи и молчаливо неверная площадь на самопересечении.

Растр даёт ровно то, что нужно этой задаче, и даёт честно: клетка либо покрыта,
либо нет, поэтому объединение и пересечение получаются сами и повторный проход
физически не может посчитаться дважды. Цена -- дискретизация, и она НЕ
замалчивается: каждая площадь считается на шаге `cell` и на шаге `2 * cell`, а
разница между ними публикуется как собственная погрешность метода. Число без
объявленной погрешности в этом тракте опаснее, чем отсутствие числа.

[REASON]: сравнение идёт с деньгами. Метод, который выдаёт уверенные 12.34 га
и умалчивает, что при вдвое другом шаге вышло бы 12.9, -- это тот же дефект,
что «отчёт вместо доказательства», только записанный в коде.

ДВА УРОВНЯ ФАЙЛОВ

`out/area_48h/private/` -- координаты, настоящие ID вылетов, значения
неизвестных полей, геометрия контуров. Каталог исключён из git и наружу не
выходит НИКОГДА. Сырых тел ответов и подписанных ссылок нет и там.

`out/area_48h/DJI_AREA_48H_SHAREABLE.{json,md}` -- только агрегаты, причины,
площади, разницы, счётчики и итоговый статус. Вылеты названы `FLIGHT-001`.
Безопасность отчёта не декларируется, а ПРОВЕРЯЕТСЯ: `assert_shareable`
получает список настоящих строк из приватного слоя и отказывается писать
отчёт, если хоть одна из них в нём нашлась. Отрицательный контроль на эту
проверку есть в тестах.

ЧЕГО ЗДЕСЬ НЕТ

Ни одной таблицы БД, ни одной миграции, ни одного эндпоинта, ни одной
страницы. Модуль не обращается к Vehicle Soft, ничего не кладёт в очередь и
ничего не начисляет. Это исследование, а не подсистема.
"""

import json
import math
import re

from pathlib import Path

from drone_collector.route_ui_probe import (RouteUiProbe,
                                            safe_exception_name)

# Версия связки «формат приватного снимка + правила расчёта». Меняется, когда
# меняется СМЫСЛ числа, а не когда правится комментарий.
STUDY_VERSION = 'area-48h-1'

# Метки итогового решения. Ровно одна из них попадает в отчёт.
USE_SPRAY_STATE_CLIPPED_UNION = 'USE_SPRAY_STATE_CLIPPED_UNION'
USE_IN_CONTOUR_WORK_PASS_UNION = 'USE_IN_CONTOUR_WORK_PASS_UNION_AS_ESTIMATE'
SOURCE_INSUFFICIENT = 'SOURCE_INSUFFICIENT_FOR_USEFUL_AREA'

# Статусы отдельного вывода.
PROVEN = 'PROVEN'
SUPPORTED = 'SUPPORTED'
NOT_PROVEN = 'NOT_PROVEN'
DISPROVED = 'DISPROVED'

# Ширина не известна -- площадь не считается. Не подставляется ни медиана, ни
# паспортная ширина, ни ширина соседнего вылета.
DATA_UNAVAILABLE = 'DATA_UNAVAILABLE'

# Тот же радиус Земли, что и в `geometry.ring_area_m2`. [REASON]: два способа
# посчитать площадь одного контура ОБЯЗАНЫ давать одно число, иначе сверка
# «наш растр против сферической формулы» ловила бы разницу радиусов, а не
# ошибку. На этом стоит тест.
EARTH_RADIUS_M = 6378137.0

# 1 гектар = 15 му ровно (docs/tracks/drones.md §3).
MU_PER_HECTARE = 15.0


class AreaStudyError(Exception):
    """Расчёт невозможен. Никогда не поднимается ради «странного» значения --
    только там, где иначе пришлось бы выдумать число."""


# ─── Настройки расчёта ───────────────────────────────────────────────────────

class StudyParams(object):
    """Пороги расчёта. Все до одного попадают в оба отчёта.

    [REASON]: порог, которого нет в отчёте, -- это скрытое допущение. Через
    месяц никто не вспомнит, что «рабочим проходом» считался отрезок от 20 м,
    и число будет прочитано как измеренное, а не как посчитанное по правилу.
    """

    __slots__ = ('cell_m', 'max_grid_cells', 'gap_m', 'min_step_m',
                 'turn_deg', 'min_pass_m', 'contour_margin_m',
                 'inside_share_min')

    def __init__(self, cell_m=0.5, max_grid_cells=24000000, gap_m=60.0,
                 min_step_m=0.05, turn_deg=25.0, min_pass_m=20.0,
                 contour_margin_m=25.0, inside_share_min=0.5):
        self.cell_m = float(cell_m)
        self.max_grid_cells = int(max_grid_cells)
        self.gap_m = float(gap_m)
        self.min_step_m = float(min_step_m)
        self.turn_deg = float(turn_deg)
        self.min_pass_m = float(min_pass_m)
        self.contour_margin_m = float(contour_margin_m)
        self.inside_share_min = float(inside_share_min)

    def as_dict(self):
        return {
            'cell_m': self.cell_m,
            'max_grid_cells': self.max_grid_cells,
            'gap_m': self.gap_m,
            'min_step_m': self.min_step_m,
            'turn_deg': self.turn_deg,
            'min_pass_m': self.min_pass_m,
            'contour_margin_m': self.contour_margin_m,
            'inside_share_min': self.inside_share_min,
        }


DEFAULT_PARAMS = StudyParams()


# ─── Проекция ────────────────────────────────────────────────────────────────

class LocalPlane(object):
    """Местная касательная плоскость. Метры, не градусы.

    x -- на восток, y -- на север, начало в (lat0, lon0). На поле в несколько
    километров искажение площади порядка сотых долей процента, и оно на два
    порядка меньше дискретизации растра.

    [REASON]: считать площадь в градусах нельзя вовсе -- на широте Бухары
    градус долготы это 85 км, а градус широты 111 км, и «квадратный градус»
    не площадь. Порядок аргументов ВСЮДУ (lat, lon): перестановка даёт
    геометрически правдоподобную, но чужую фигуру, и её не поймает ни одна
    проверка на конечность. На это стоит отдельный тест.
    """

    __slots__ = ('lat0', 'lon0', '_kx', '_ky')

    def __init__(self, lat0, lon0):
        self.lat0 = float(lat0)
        self.lon0 = float(lon0)
        self._ky = EARTH_RADIUS_M * math.pi / 180.0
        self._kx = self._ky * math.cos(math.radians(self.lat0))

    def xy(self, lat, lon):
        return ((float(lon) - self.lon0) * self._kx,
                (float(lat) - self.lat0) * self._ky)

    def latlon(self, x, y):
        return (y / self._ky + self.lat0, x / self._kx + self.lon0)

    def project(self, points):
        """[(lat, lon)] -> [(x, y)]."""
        return [self.xy(lat, lon) for lat, lon in points]

    def as_dict(self):
        return {'kind': 'local-tangent-plane',
                'origin_is_private': True,
                'metres_per_degree_north': round(self._ky, 3),
                'metres_per_degree_east': round(self._kx, 3)}


def plane_for(points):
    """Плоскость с началом в середине облака точек. Отказ, если точек нет."""
    usable = [(lat, lon) for lat, lon in points
              if _finite(lat) and _finite(lon)
              and -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0]
    if not usable:
        raise AreaStudyError('no usable WGS84 point to anchor the projection')
    lat0 = (min(p[0] for p in usable) + max(p[0] for p in usable)) / 2.0
    lon0 = (min(p[1] for p in usable) + max(p[1] for p in usable)) / 2.0
    return LocalPlane(lat0, lon0)


def _finite(value):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value))


# ─── Растр покрытия ──────────────────────────────────────────────────────────

class Grid(object):
    """Маска покрытия на прямоугольной сетке в метрах.

    Одна клетка -- один байт. Клетка считается покрытой, когда покрыт её
    ЦЕНТР. Отсюда и вся погрешность метода: она ограничена примерно
    `cell * perimeter / 2` и потому объявляется, а не оценивается на глаз.

    [REASON]: `bytearray` вместо множества клеток. Множество на десяти
    миллионах клеток это гигабайты и минуты; байтовый массив позволяет
    закрашивать целый отрезок строки одним присваиванием среза, и именно это
    делает пересчёт на двух шагах сетки посильным.
    """

    __slots__ = ('x0', 'y0', 'cell', 'nx', 'ny', 'mask')

    def __init__(self, x0, y0, x1, y1, cell):
        if not (_finite(x0) and _finite(y0) and _finite(x1) and _finite(y1)):
            raise AreaStudyError('the grid extent is not finite')
        if not _finite(cell) or cell <= 0:
            raise AreaStudyError('the grid cell must be a positive number')
        self.cell = float(cell)
        self.x0 = float(x0)
        self.y0 = float(y0)
        self.nx = max(1, int(math.ceil((x1 - x0) / self.cell)) + 1)
        self.ny = max(1, int(math.ceil((y1 - y0) / self.cell)) + 1)
        self.mask = bytearray(self.nx * self.ny)

    @property
    def cells(self):
        return self.nx * self.ny

    # [REASON]: центр клетки лежит в её СЕРЕДИНЕ, а не в углу. При центре в
    # углу граница фигуры, попавшая ровно на сетку, засчитывается с обеих
    # сторон сразу, и полоса 100 x 10 м на шаге 0.1 м даёт 1011 м2 вместо
    # 1000 -- систематическое завышение на целую строку и целый столбец,
    # одинаковое при верном и неверном коде и потому невидимое для проверки
    # «площадь примерно та». При центре в середине та же полоса даёт ровно
    # 1000 м2, а смещение метода становится знакопеременным, а не
    # односторонним.
    def _ix(self, x):
        return int(math.floor((x - self.x0) / self.cell))

    def _iy(self, y):
        return int(math.floor((y - self.y0) / self.cell))

    def _cx(self, ix):
        return self.x0 + (ix + 0.5) * self.cell

    def _cy(self, iy):
        return self.y0 + (iy + 0.5) * self.cell

    def fill_row(self, iy, xlo, xhi):
        """Закрасить в строке `iy` клетки, чьи центры лежат в [xlo, xhi]."""
        if iy < 0 or iy >= self.ny or xhi < xlo:
            return
        first = int(math.ceil((xlo - self.x0) / self.cell - 0.5 - 1e-9))
        last = int(math.floor((xhi - self.x0) / self.cell - 0.5 + 1e-9))
        if last < first:
            return
        first = max(first, 0)
        last = min(last, self.nx - 1)
        if last < first:
            return
        base = iy * self.nx
        self.mask[base + first:base + last + 1] = b'\x01' * (last - first + 1)

    def count(self):
        return self.nx * self.ny - self.mask.count(0)

    def area_m2(self):
        return self.count() * self.cell * self.cell

    def _same_extent(self, other):
        return (other.nx == self.nx and other.ny == self.ny
                and other.cell == self.cell
                and other.x0 == self.x0 and other.y0 == self.y0)

    def intersected(self, other):
        """Новая маска -- пересечение двух. Сетки обязаны совпадать.

        [REASON]: пересечение считается одним побитовым И над целым числом, а
        не циклом по клеткам. На поле в десять гектаров это миллионы клеток, и
        цикл на Python превращал бы каждый пересчёт в минуты -- то есть
        контроль на двух шагах сетки, ради которого всё и затевалось, стало бы
        соблазнительно не делать. Каждый байт маски равен 0 или 1, поэтому
        побайтовое И над числом даёт ровно поклеточное пересечение.
        """
        if not self._same_extent(other):
            raise AreaStudyError('two grids of different extents cannot be '
                                 'intersected')
        out = Grid.__new__(Grid)
        out.x0, out.y0, out.cell = self.x0, self.y0, self.cell
        out.nx, out.ny = self.nx, self.ny
        size = len(self.mask)
        merged = (int.from_bytes(self.mask, 'big')
                  & int.from_bytes(other.mask, 'big'))
        out.mask = bytearray(merged.to_bytes(size, 'big'))
        return out

    def union_with(self, other):
        """Новая маска -- объединение двух. Сетки обязаны совпадать."""
        if not self._same_extent(other):
            raise AreaStudyError('two grids of different extents cannot be '
                                 'united')
        out = Grid.__new__(Grid)
        out.x0, out.y0, out.cell = self.x0, self.y0, self.cell
        out.nx, out.ny = self.nx, self.ny
        size = len(self.mask)
        merged = (int.from_bytes(self.mask, 'big')
                  | int.from_bytes(other.mask, 'big'))
        out.mask = bytearray(merged.to_bytes(size, 'big'))
        return out

    def add_disk(self, cx, cy, radius):
        """Круг радиуса `radius` вокруг точки."""
        if radius <= 0 or not _finite(radius):
            raise AreaStudyError('the swath radius must be positive')
        iy_lo = max(0, self._iy(cy - radius - self.cell))
        iy_hi = min(self.ny - 1, self._iy(cy + radius + self.cell))
        rr = radius * radius
        for iy in range(iy_lo, iy_hi + 1):
            gap = self._cy(iy) - cy
            if abs(gap) > radius:
                continue
            half = math.sqrt(max(0.0, rr - gap * gap))
            self.fill_row(iy, cx - half, cx + half)

    def add_convex(self, corners):
        """Выпуклый многоугольник, заданный вершинами по обходу."""
        ys = [point[1] for point in corners]
        iy_lo = max(0, self._iy(min(ys) - self.cell))
        iy_hi = min(self.ny - 1, self._iy(max(ys) + self.cell))
        for iy in range(iy_lo, iy_hi + 1):
            span = _convex_row_span(corners, self._cy(iy))
            if span is not None:
                self.fill_row(iy, span[0], span[1])

    def add_band(self, ax, ay, bx, by, radius):
        """Полоса вдоль отрезка с ПЛОСКИМИ торцами.

        [REASON]: торцы плоские, а не круглые. Круглый торец дорисовывает
        полукруг радиусом в полширины ЗА концом отрезка -- земли, по которой
        дрон не проходил. На прямой в 100 м при ширине 10 м это 78.5 м2, почти
        восемь процентов выдуманной площади, и на маршруте из двадцати
        проходов ошибка не сокращается, а накапливается. Стыки между соседними
        отрезками закругляются отдельно (`add_disk` в `paint_track`) -- там
        круг заполняет настоящий клин на повороте, а не выходит наружу.
        """
        length = math.hypot(bx - ax, by - ay)
        if length <= 0:
            return
        nx_ = -(by - ay) / length * radius
        ny_ = (bx - ax) / length * radius
        self.add_convex(((ax + nx_, ay + ny_), (bx + nx_, by + ny_),
                         (bx - nx_, by - ny_), (ax - nx_, ay - ny_)))

    def paint_track(self, segments, radius):
        """Полоса вдоль цепочки отрезков: прямоугольники плюс круги на стыках.

        Стык закругляется только там, где отрезки ДЕЙСТВИТЕЛЬНО соседние
        (`index` подряд): иначе круг вырос бы на конце прохода, оборванного
        разрывом записи или границей контура, то есть ровно там, где торец
        обязан остаться плоским.
        """
        previous = None
        for segment in segments:
            self.add_band(segment.ax, segment.ay, segment.bx, segment.by,
                          radius)
            if previous is not None and previous.index + 1 == segment.index:
                self.add_disk(segment.ax, segment.ay, radius)
            previous = segment

    def add_polygon(self, rings):
        """Закрасить многоугольник с дырами по правилу чётности.

        `rings` -- [[(x, y), ...], ...]; первое кольцо внешнее, остальные дыры.
        Правило чётности само исключает дыры, поэтому отдельного вычитания
        нет: клетка внутри дыры пересекает нечётное число рёбер дважды.
        """
        edges = []
        for ring in rings:
            points = list(ring)
            if len(points) < 3:
                continue
            if points[0] != points[-1]:
                points.append(points[0])
            for index in range(len(points) - 1):
                x1, y1 = points[index]
                x2, y2 = points[index + 1]
                if y1 == y2:
                    continue
                edges.append((x1, y1, x2, y2))
        if not edges:
            return
        ylo = min(min(e[1], e[3]) for e in edges)
        yhi = max(max(e[1], e[3]) for e in edges)
        iy_lo = max(0, self._iy(ylo))
        iy_hi = min(self.ny - 1, self._iy(yhi))
        for iy in range(iy_lo, iy_hi + 1):
            yc = self._cy(iy)
            crossings = []
            for x1, y1, x2, y2 in edges:
                # Полуоткрытое правило [ymin, ymax): вершина, лежащая ровно на
                # строке, считается один раз, а не дважды и не ноль раз.
                if (y1 <= yc < y2) or (y2 <= yc < y1):
                    crossings.append(x1 + (yc - y1) * (x2 - x1) / (y2 - y1))
            if len(crossings) < 2:
                continue
            crossings.sort()
            for index in range(0, len(crossings) - 1, 2):
                self.fill_row(iy, crossings[index], crossings[index + 1])


def _convex_row_span(corners, yc):
    """(xmin, xmax) пересечения горизонтали `yc` с выпуклым многоугольником."""
    xs = []
    count = len(corners)
    for index in range(count):
        x1, y1 = corners[index]
        x2, y2 = corners[(index + 1) % count]
        if y1 == y2:
            if y1 == yc:
                xs.extend((x1, x2))
            continue
        if (y1 <= yc <= y2) or (y2 <= yc <= y1):
            xs.append(x1 + (yc - y1) * (x2 - x1) / (y2 - y1))
    if not xs:
        return None
    return (min(xs), max(xs))


def bounds_of(points, margin=0.0):
    """(x0, y0, x1, y1) с запасом. Отказ на пустом списке."""
    if not points:
        raise AreaStudyError('an empty point set has no bounds')
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs) - margin, min(ys) - margin,
            max(xs) + margin, max(ys) + margin)


def choose_cell(extent, preferred, max_cells):
    """Шаг сетки, при котором она помещается в потолок клеток.

    Возвращает (шаг, огрублён ли). Шаг УДВАИВАЕТСЯ, пока сетка не влезет:
    отказать было бы честно, но бесполезно, а посчитать молча на другом шаге
    -- нечестно. Поэтому огрубление объявляется в обоих отчётах.
    """
    x0, y0, x1, y1 = extent
    cell = float(preferred)
    coarsened = False
    for _ in range(24):
        nx = max(1, int(math.ceil((x1 - x0) / cell)) + 1)
        ny = max(1, int(math.ceil((y1 - y0) / cell)) + 1)
        if nx * ny <= max_cells:
            return cell, coarsened
        cell *= 2.0
        coarsened = True
    raise AreaStudyError('the area does not fit any reasonable grid')


# ─── Разбор маршрута на участки ──────────────────────────────────────────────

# Причины, по которым отрезок маршрута НЕ считается рабочим. Названы словами и
# считаются поимённо: «исключено 412 отрезков» без разбивки ничего не
# доказывает и ничего не позволяет оспорить.
SEG_WORK = 'WORK'
SEG_GAP = 'GAP'                    # прыжок длиннее `gap_m` -- разрыв записи
SEG_STANDSTILL = 'STANDSTILL'      # две точки в одном месте
SEG_OUTSIDE = 'OUTSIDE_CONTOUR'    # хотя бы один конец вне контура
SEG_SHORT_RUN = 'SHORT_RUN'        # соединение, разворот, подход, возврат

SEGMENT_REASONS = (SEG_WORK, SEG_GAP, SEG_STANDSTILL, SEG_OUTSIDE,
                   SEG_SHORT_RUN)


class Segment(object):
    """Отрезок маршрута между двумя соседними точками."""

    __slots__ = ('index', 'ax', 'ay', 'bx', 'by', 'length', 'bearing',
                 'reason', 'run_id', 'inside')

    def __init__(self, index, ax, ay, bx, by):
        self.index = index
        self.ax, self.ay, self.bx, self.by = ax, ay, bx, by
        self.length = math.hypot(bx - ax, by - ay)
        self.bearing = (math.degrees(math.atan2(bx - ax, by - ay)) % 360.0
                        if self.length > 0 else None)
        self.reason = None
        self.run_id = None
        self.inside = None

    @property
    def is_work(self):
        return self.reason == SEG_WORK


def _bearing_delta(first, second):
    """Разница курсов в градусах, 0..180. `None` у отрезка нулевой длины."""
    if first is None or second is None:
        return None
    delta = abs(first - second) % 360.0
    return delta if delta <= 180.0 else 360.0 - delta


def point_in_rings(x, y, rings):
    """Точка внутри многоугольника с дырами. Правило чётности.

    [REASON]: тот же критерий, что закрашивает растр контура, и намеренно тот
    же. Классификация отрезка и обрезка площади обязаны согласовываться: иначе
    отрезок объявлен внутренним, а его полоса при пересечении с контуром
    исчезает, и «полезная площадь» получается нулевой при непустой работе.
    """
    inside = False
    for ring in rings:
        points = list(ring)
        if len(points) < 3:
            continue
        if points[0] != points[-1]:
            points.append(points[0])
        for index in range(len(points) - 1):
            x1, y1 = points[index]
            x2, y2 = points[index + 1]
            if (y1 > y) != (y2 > y):
                cross = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
                if cross > x:
                    inside = not inside
    return inside


def classify_segments(points_xy, params=DEFAULT_PARAMS, rings=None):
    """[(x, y)] -> [Segment] с проставленной причиной.

    Порядок правил -- от жёсткого к мягкому, и он важен:

    1. разрыв записи (`GAP`) -- никогда не работа. Между двумя точками в
       трёхстах метрах друг от друга дрон, возможно, и работал, но МЫ этого не
       знаем, и полоса шириной семь метров на триста метров была бы
       выдуманной площадью;
    2. стояние на месте -- курса нет, полоса вырождается;
    3. вне контура -- подход, возврат, перелёт между полями;
    4. остальное собирается в ПРОХОДЫ по согласованности курса, и проход
       короче `min_pass_m` рабочим не считается: так отсеиваются развороты и
       короткие соединения между полосами.

    Признака работы насоса в источнике нет, поэтому «рабочий» здесь -- это
    вывод геометрии, а не наблюдение. Отчёт обязан называть его расчётным.
    """
    segments = []
    for index in range(len(points_xy) - 1):
        ax, ay = points_xy[index]
        bx, by = points_xy[index + 1]
        segments.append(Segment(index, ax, ay, bx, by))

    for segment in segments:
        if rings is not None:
            segment.inside = (point_in_rings(segment.ax, segment.ay, rings)
                              and point_in_rings(segment.bx, segment.by,
                                                 rings))
        if segment.length > params.gap_m:
            segment.reason = SEG_GAP
        elif segment.length < params.min_step_m:
            segment.reason = SEG_STANDSTILL
        elif rings is not None and not segment.inside:
            segment.reason = SEG_OUTSIDE

    # Проходы: подряд идущие ещё не отвергнутые отрезки с согласованным курсом.
    runs = []
    current = []
    previous = None
    for segment in segments:
        if segment.reason is not None:
            if current:
                runs.append(current)
                current = []
            previous = None
            continue
        delta = _bearing_delta(previous.bearing if previous else None,
                              segment.bearing)
        if previous is not None and delta is not None and delta > params.turn_deg:
            runs.append(current)
            current = []
        current.append(segment)
        previous = segment
    if current:
        runs.append(current)

    for run_id, run in enumerate(runs):
        total = sum(segment.length for segment in run)
        reason = SEG_WORK if total >= params.min_pass_m else SEG_SHORT_RUN
        for segment in run:
            segment.reason = reason
            segment.run_id = run_id

    for segment in segments:
        if segment.reason is None:          # пустой прогон не оставляет дыр
            segment.reason = SEG_SHORT_RUN
    return segments


def segment_totals(segments):
    """{причина: {'segments': n, 'length_m': L}} -- по всем причинам сразу."""
    totals = {reason: {'segments': 0, 'length_m': 0.0}
              for reason in SEGMENT_REASONS}
    for segment in segments:
        bucket = totals.setdefault(segment.reason,
                                   {'segments': 0, 'length_m': 0.0})
        bucket['segments'] += 1
        bucket['length_m'] += segment.length
    for bucket in totals.values():
        bucket['length_m'] = round(bucket['length_m'], 2)
    return totals


# ─── Площади ─────────────────────────────────────────────────────────────────

class CoverageResult(object):
    """Площади одного расчёта на ОДНОМ шаге сетки."""

    __slots__ = ('cell_m', 'coarsened', 'swath_all_ha', 'swath_work_ha',
                 'clipped_all_ha', 'clipped_work_ha', 'contour_ha',
                 'grid_cells')

    def __init__(self, **kwargs):
        for name in self.__slots__:
            setattr(self, name, kwargs.get(name))

    def as_dict(self):
        return {name: getattr(self, name) for name in self.__slots__}


def _ha(value):
    return None if value is None else round(value / 10000.0, 4)


def coverage_once(tracks, rings, params, cell):
    """Одна прогонка растра на заданном шаге.

    `tracks` -- [(отрезки, половина ширины захвата в метрах или None)]. Список,
    а не один маршрут: части одной работы кладутся на ОДНУ сетку, и потому их
    перекрытие физически не может посчитаться дважды. Вылет без ширины в
    полосу не превращается вовсе -- ни своей, ни соседской.
    """
    # Разрыв записи и стояние на месте полосой не становятся: в первом случае
    # мы не знаем, где дрон был, во втором он никуда не двигался.
    painted = [([segment for segment in segments
                 if segment.reason not in (SEG_STANDSTILL, SEG_GAP)], half)
               for segments, half in tracks]

    # [REASON]: рамка сетки строится РОВНО по тому, что будет закрашено, плюс
    # запас на полширины. Не по всем точкам маршрута: перелёт с редкими
    # точками растянул бы сетку на километры и заставил огрубить шаг там, где
    # мерить нечего. И не по меньшему множеству, чем закрашиваемое: `fill_row`
    # молча обрезает всё, что вышло за сетку, и полоса, не влезшая в рамку,
    # исчезла бы из площади без единого слова.
    cloud = []
    for segments, _half in painted:
        for segment in segments:
            cloud.append((segment.ax, segment.ay))
            cloud.append((segment.bx, segment.by))
    for ring in (rings or ()):
        cloud.extend(ring)
    widths = [half for _segments, half in painted if half]
    margin = (max(widths) if widths else 0.0) + 4.0 * cell
    if cloud:
        extent = bounds_of(cloud, margin=margin)
    else:
        # [REASON]: вырожденная сетка, а не ранний возврат. Закрашивать нечего
        # -- у вылета, состоящего из одного разрыва записи, полоса и правда
        # нулевая, и сказать «0.0 га» тут честно. Ранний возврат отдавал бы
        # `None`, то есть «не измерено», и «дрон никуда не летел» стало бы
        # неотличимо от «ширины нет». Это два разных ответа владельцу.
        extent = (0.0, 0.0, cell, cell)
    cell, coarsened = choose_cell(extent, cell, params.max_grid_cells)

    all_grid = Grid(extent[0], extent[1], extent[2], extent[3], cell)
    work_grid = Grid(extent[0], extent[1], extent[2], extent[3], cell)
    any_width = False
    for segments, half in painted:
        if not half:
            continue
        any_width = True
        all_grid.paint_track(segments, half)
        work_grid.paint_track([segment for segment in segments
                               if segment.is_work], half)

    contour_grid = None
    if rings:
        contour_grid = Grid(extent[0], extent[1], extent[2], extent[3], cell)
        contour_grid.add_polygon(rings)

    clipped_all = clipped_work = None
    if contour_grid is not None and any_width:
        clipped_all = all_grid.intersected(contour_grid).area_m2()
        clipped_work = work_grid.intersected(contour_grid).area_m2()

    return CoverageResult(
        cell_m=cell,
        coarsened=coarsened,
        swath_all_ha=_ha(all_grid.area_m2()) if any_width else None,
        swath_work_ha=_ha(work_grid.area_m2()) if any_width else None,
        clipped_all_ha=_ha(clipped_all),
        clipped_work_ha=_ha(clipped_work),
        contour_ha=(_ha(contour_grid.area_m2())
                    if contour_grid is not None else None),
        grid_cells=all_grid.cells)


def relative_gap_percent(fine, coarse):
    """|fine - coarse| / fine * 100, или None."""
    if fine is None or coarse is None or not _finite(fine) or fine == 0:
        return None
    return round(abs(fine - coarse) / abs(fine) * 100.0, 3)


def coverage_with_uncertainty(tracks, rings, params=DEFAULT_PARAMS):
    """Площади на шаге `cell` и на `2 * cell`, плюс расхождение между ними.

    [REASON]: контроль, который даёт один и тот же ответ при верном и неверном
    коде, проверкой не является -- и число без погрешности читается как
    точное. Второй прогон на вдвое грубой сетке стоит четверти первого и
    ловит именно тот случай, когда площадь определяется дискретизацией, а не
    геометрией: у здоровой фигуры расхождение доли процента, у выродившейся --
    десятки.
    """
    fine = coverage_once(tracks, rings, params, params.cell_m)
    coarse = coverage_once(tracks, rings, params, params.cell_m * 2.0)
    uncertainty = {}
    for name in ('swath_all_ha', 'swath_work_ha', 'clipped_all_ha',
                 'clipped_work_ha', 'contour_ha'):
        uncertainty[name] = relative_gap_percent(getattr(fine, name),
                                                 getattr(coarse, name))
    return fine, coarse, uncertainty


# ─── Третье поле точки: только приватный слой ────────────────────────────────

# [REASON]: значения неизвестных полей -- ПРИВАТНЫЕ данные, и в безопасный
# отчёт из них не попадает ни одно. Наружу идут только производные признаки:
# сколько различных значений, меняется ли поле вдоль маршрута, сколько раз
# переключается. По ним нельзя восстановить ни значение, ни координату, но
# можно ответить на вопрос задания -- постоянное поле, монотонное, дискретное
# или переключающееся.
PATTERN_CONSTANT = 'CONSTANT'
PATTERN_MONOTONIC = 'MONOTONIC'
PATTERN_DISCRETE_FEW = 'DISCRETE_FEW'
PATTERN_SWITCHING = 'SWITCHING'
PATTERN_MANY_VALUES = 'MANY_VALUES'
PATTERN_NOT_NUMERIC = 'NOT_NUMERIC'

# Сколько различных значений ещё считается «дискретным малым набором».
DISCRETE_MAX_VALUES = 8


def _wire_value(wire, raw):
    """Число для varint/fixed, длина для bytes. Приватный слой."""
    import struct as _struct
    if wire == 0:
        return int(raw)
    if wire == 1:
        return _struct.unpack('<q', raw)[0]
    if wire == 5:
        return _struct.unpack('<i', raw)[0]
    return None


def unknown_point_values(raw_body):
    """{flight_id: {'number:wire': [значение, ...]}} из СЫРОГО тела ответа.

    Собственный проход по wire-формату, а не правка `route_decode`.

    [REASON]: `route-decode-2` намеренно НЕ отдаёт значения неизвестных полей
    -- в этом весь смысл `UNKNOWN_SEMANTICS`, и ослаблять доказанный декодер
    ради исследования нельзя. Значения нужны ровно здесь, ровно на один шаг, и
    выходят они ровно в приватный файл. Поэтому чтение живёт в модуле, который
    и отвечает за разделение уровней, а не в модуле, который кормит отчёт.
    """
    from drone_collector.route_decode import (FIELD_FLIGHT_ID, FIELD_PAYLOAD,
                                              FIELD_POINT, FIELD_ROUTE,
                                              SUB_LAT, SUB_LNG, WIRE_BYTES,
                                              WIRE_VARINT, walk)
    out = {}
    payload = None
    for number, wire, value in walk(bytes(raw_body)):
        if number == FIELD_PAYLOAD and wire == WIRE_BYTES:
            payload = value
    if payload is None:
        return out
    for number, wire, value in walk(payload):
        if number != FIELD_ROUTE or wire != WIRE_BYTES:
            continue
        flight_id = None
        series = {}
        for num, wr, val in walk(value):
            if num == FIELD_FLIGHT_ID and wr == WIRE_VARINT:
                flight_id = val
            elif num == FIELD_POINT and wr == WIRE_BYTES:
                for pnum, pwire, praw in walk(val):
                    if pnum in (SUB_LAT, SUB_LNG):
                        continue
                    key = '%d:%d' % (pnum, pwire)
                    series.setdefault(key, []).append(_wire_value(pwire, praw))
        if flight_id is not None:
            out[flight_id] = series
    return out


def describe_series(values):
    """Безопасный портрет числового ряда. Ни одного значения наружу."""
    numbers = [value for value in values if isinstance(value, int)]
    if not numbers:
        return {'count': len(values), 'pattern': PATTERN_NOT_NUMERIC,
                'distinct': None, 'changes': None,
                'transitions': None, 'monotonic_non_decreasing': None}
    distinct = len(set(numbers))
    transitions = sum(1 for index in range(1, len(numbers))
                      if numbers[index] != numbers[index - 1])
    non_decreasing = all(numbers[index] >= numbers[index - 1]
                         for index in range(1, len(numbers)))
    if distinct == 1:
        pattern = PATTERN_CONSTANT
    elif non_decreasing and distinct > DISCRETE_MAX_VALUES:
        pattern = PATTERN_MONOTONIC
    elif distinct <= DISCRETE_MAX_VALUES:
        pattern = (PATTERN_SWITCHING if transitions > distinct
                   else PATTERN_DISCRETE_FEW)
    else:
        pattern = PATTERN_MANY_VALUES
    return {'count': len(numbers), 'pattern': pattern, 'distinct': distinct,
            'changes': transitions > 0, 'transitions': transitions,
            'monotonic_non_decreasing': non_decreasing}


def agreement_with_work(values, segments):
    """Насколько ряд согласован с геометрическим делением на работу и холостое.

    Возвращает долю совпадений лучшего ДВУЗНАЧНОГО разбиения ряда (значение
    равно самому частому / не равно) с признаком `is_work` соседнего отрезка,
    и `None`, если ряд не двузначный или отрезков нет.

    [REASON]: это не доказательство и в решении оно одно ничего не решает.
    Согласие 0.99 -- повод искать подтверждение в кабинете DJI; согласие 0.55
    -- сразу закрывает вопрос. Само по себе высокое согласие остаётся
    корреляцией: и «поле включённого насоса», и «поле режима автопилота», и
    «поле принадлежности к плану маршрута» дали бы её одинаково.
    """
    numbers = [value for value in values if isinstance(value, int)]
    if len(set(numbers)) != 2 or not segments:
        return None
    pairs = min(len(numbers) - 1, len(segments))
    if pairs <= 0:
        return None
    best = None
    for candidate in sorted(set(numbers)):
        hits = 0
        for index in range(pairs):
            marked = numbers[index] == candidate
            if marked == segments[index].is_work:
                hits += 1
        share = hits / float(pairs)
        best = share if best is None else max(best, share)
    return round(best, 4)


# ─── Группировка вылетов в одну работу ───────────────────────────────────────

# Основание группировки. ТОЛЬКО пространственное: один день, одна машина и
# действительно пересекающиеся (или соседние) маршруты.
#
# [REASON]: `mission_uuid` больше не группирует. Он не доказан как
# идентификатор задания (см. `RouteRecord.mission_uuid`), а группировка по нему
# делала недоказанное поле САМЫМ СИЛЬНЫМ правилом -- и повтор площади внутри
# такой группы получал статус PROVEN. Это замыкание на себя: недоказанная
# посылка выдавала доказанный вывод, и ложная причина расхождения выглядела бы
# установленной. Два маршрута в разных концах района с одинаковым
# `mission_uuid` объединялись бы в одну полезную площадь, которой не существует.
GROUP_SPATIAL = 'same_machine_day_and_overlapping_routes'
GROUP_SINGLE = 'single_flight'

# Совпадение `mission_uuid` внутри пространственной группы -- ПРИЗНАК, а не
# основание. Записывается рядом с группой и в отчёте называется своим именем.
MISSION_SHARED = 'SHARED'
MISSION_MIXED = 'MIXED'
MISSION_ABSENT = 'ABSENT'

def _bbox(points):
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    return (min(lats), min(lons), max(lats), max(lons))


def _bbox_overlap(first, second, margin_deg):
    return not (first[2] + margin_deg < second[0]
                or second[2] + margin_deg < first[0]
                or first[3] + margin_deg < second[1]
                or second[3] + margin_deg < first[1])


def mission_state(flights):
    """SHARED / MIXED / ABSENT -- что `mission_uuid` говорит о группе.

    Это описание, а не решение: ни одна площадь от этого значения не зависит.
    """
    values = {flight.get('mission_uuid') for flight in flights
              if flight.get('mission_uuid')}
    if not values:
        return MISSION_ABSENT
    if len(values) == 1 and all(flight.get('mission_uuid')
                                for flight in flights):
        return MISSION_SHARED
    return MISSION_MIXED


def group_flights(flights, margin_deg=0.0015):
    """[(основание, [вылет, ...])] -- вылеты, которые считаются одной работой.

    Правило ровно одно и оно пространственное: одна машина, один день и
    пересекающиеся (или соседние в пределах `margin_deg`) рамки маршрутов.
    Вылет, ни с кем не пересёкшийся, остаётся сам по себе.

    `mission_uuid` в группировке НЕ участвует. Он читается отдельно
    (`mission_state`) и живёт в отчёте как признак: совпал он у членов группы
    или нет. Объединять по нему два разнесённых маршрута нельзя -- это выдало
    бы полезную площадь, которой не существует, на основании поля, семантика
    которого не доказана.

    [REASON]: группировка сделана ДО измерения площади, а не после. Сложить
    площади частей одной работы -- это ровно та ошибка, которую задание просит
    объяснить: перекрытие двух частей посчиталось бы дважды.
    """
    buckets = {}
    for flight in flights:
        key = (flight.get('nickname'), flight.get('day'))
        buckets.setdefault(key, []).append(flight)

    groups = []
    for key in sorted(buckets, key=lambda item: (str(item[0]), str(item[1]))):
        members = sorted(buckets[key],
                         key=lambda item: (item.get('start_ms') or 0,
                                           item.get('flight_id') or 0))
        clusters = []
        for flight in members:
            points = flight.get('points') or []
            box = _bbox(points) if points else None
            placed = False
            for cluster in clusters:
                if box is None or cluster['box'] is None:
                    continue
                if _bbox_overlap(box, cluster['box'], margin_deg):
                    cluster['members'].append(flight)
                    cluster['box'] = (min(box[0], cluster['box'][0]),
                                      min(box[1], cluster['box'][1]),
                                      max(box[2], cluster['box'][2]),
                                      max(box[3], cluster['box'][3]))
                    placed = True
                    break
            if not placed:
                clusters.append({'box': box, 'members': [flight]})
        for cluster in clusters:
            basis = (GROUP_SPATIAL if len(cluster['members']) > 1
                     else GROUP_SINGLE)
            groups.append((basis, cluster['members']))
    return groups


# ─── Отбор контуров ──────────────────────────────────────────────────────────

def candidate_contours(nodes, points, margin_deg=0.0015, limit=8):
    """Узлы справочника, чья рамка накрывает точки маршрута.

    Возвращает список (uuid, доля точек внутри рамки), отсортированный по
    убыванию доли. Отбор идёт по СЫРЫМ узлам -- подписанная ссылка невыбранного
    контура не попадает даже в объект в памяти, ровно как в `geometry.py`.
    """
    if not points:
        return []
    scored = []
    for node in nodes or ():
        if not isinstance(node, dict):
            continue
        box = node.get('bbox')
        if not isinstance(box, dict):
            continue
        upper = box.get('upperRight') or {}
        lower = box.get('downLeft') or {}
        try:
            north = float(upper['lat'])
            east = float(upper['lng'])
            south = float(lower['lat'])
            west = float(lower['lng'])
        except (KeyError, TypeError, ValueError):
            continue
        hits = sum(1 for lat, lon in points
                   if south - margin_deg <= lat <= north + margin_deg
                   and west - margin_deg <= lon <= east + margin_deg)
        if hits:
            scored.append((hits / float(len(points)), node.get('uuid')))
    scored.sort(key=lambda item: (-item[0], str(item[1])))
    return [(uuid, round(share, 4)) for share, uuid in scored[:limit]]


# ─── Выбор контура по НАСТОЯЩЕМУ полигону ────────────────────────────────────

CONTOUR_MATCHED = 'CONTOUR_MATCHED'
CONTOUR_AMBIGUOUS = 'CONTOUR_AMBIGUOUS'
CONTOUR_NOT_MATCHED = 'CONTOUR_NOT_MATCHED'
CONTOUR_NOT_OFFERED = 'CONTOUR_NOT_OFFERED'

# Какая доля точек маршрута обязана лежать ВНУТРИ полигона, чтобы контур вообще
# считался подходящим.
#
# [REASON]: порог записан числом и попадает в оба отчёта. Без него «лучший из
# восьми» побеждал бы всегда -- даже когда внутри полигона лежит одна точка из
# тысячи, то есть когда маршрут просто прошёл над углом чужого поля.
CONTOUR_MIN_POINT_SHARE = 0.30

# Насколько первый обязан оторваться от второго, чтобы выбор считался
# однозначным: по доле точек и, вторично, по длине маршрута внутри полигона.
CONTOUR_TIE_SHARE = 0.05
CONTOUR_TIE_LENGTH = 0.05


def _inside_scores(plane, points, rings):
    """(доля точек внутри, длина маршрута внутри в метрах)."""
    projected = plane.project(points)
    flags = [point_in_rings(x, y, rings) for x, y in projected]
    share = (sum(1 for flag in flags if flag) / float(len(flags))
             if flags else 0.0)
    length = 0.0
    for index in range(len(projected) - 1):
        if flags[index] and flags[index + 1]:
            length += math.hypot(projected[index + 1][0] - projected[index][0],
                                 projected[index + 1][1] - projected[index][1])
    return share, length


def choose_contour(plane, points, candidates, params=None):
    """Контур работы -- по НАСТОЯЩЕМУ полигону, а не по прямоугольной рамке.

    Рамки соседних полей пересекаются сплошь и рядом, и «первый кандидат с
    наибольшей долей точек в рамке» -- это запросто соседнее или объемлющее
    поле. Обрезка по чужому полигону дала бы уверенную неверную площадь, ничем
    себя не выдав: число выглядело бы правдоподобным.

    Поэтому рамка отбирает только КОРОТКИЙ СПИСОК, а решает геометрия:

    1. у каждого кандидата с годным полигоном считается доля точек маршрута
       внутри и длина маршрута внутри;
    2. победитель обязан взять порог `CONTOUR_MIN_POINT_SHARE`;
    3. первый обязан оторваться от второго -- по доле, а при равенстве долей
       по длине внутри;
    4. если оба признака в пределах допуска, это `CONTOUR_AMBIGUOUS`, и контур
       НЕ назначается: посчитать площадь по одному из двух одинаково
       подходящих полей значило бы бросить монету и напечатать результат как
       измерение.

    Порядок uuid не участвует в решении НИГДЕ. Он используется только как
    последний ключ сортировки для устойчивого вывода, и до него дело доходит
    лишь тогда, когда решение уже объявлено неоднозначным.

    Битая геометрия одного кандидата не мешает проверить остальных: причина
    отказа записывается, и разбор идёт дальше.
    """
    params = params or DEFAULT_PARAMS
    offered = [item for item in (candidates or ()) if item.get('geojson')]
    scores = []
    for candidate in offered:
        rings, area_ha, reasons = rings_from_geojson(candidate['geojson'],
                                                     plane)
        if reasons or not rings:
            scores.append({'uuid': candidate.get('uuid'), 'share': None,
                           'length_inside_m': None, 'area_ha': None,
                           'rings': None,
                           'rejected_because': reasons or ['no ring']})
            continue
        share, length = _inside_scores(plane, points, rings)
        scores.append({'uuid': candidate.get('uuid'), 'share': round(share, 4),
                       'length_inside_m': round(length, 2),
                       'area_ha': area_ha, 'rings': rings,
                       'rejected_because': []})

    usable = [item for item in scores if item['rings'] is not None]
    ranked = sorted(usable, key=lambda item: (-item['share'],
                                              -item['length_inside_m'],
                                              str(item['uuid'])))
    outcome = {
        'status': CONTOUR_NOT_OFFERED, 'uuid': None, 'rings': None,
        'area_ha': None, 'candidates_offered': len(offered),
        'candidates_usable': len(usable), 'share_inside': None,
        'runner_up_share_inside': None, 'unambiguous': False,
        'scores': scores, 'reason': 'no candidate contour was offered',
    }
    if not offered:
        return outcome
    if not ranked:
        outcome['status'] = CONTOUR_NOT_MATCHED
        outcome['reason'] = ('every candidate polygon was rejected as unusable '
                             'geometry')
        return outcome

    best = ranked[0]
    second = ranked[1] if len(ranked) > 1 else None
    outcome['share_inside'] = best['share']
    outcome['runner_up_share_inside'] = second['share'] if second else None

    if best['share'] < CONTOUR_MIN_POINT_SHARE:
        outcome['status'] = CONTOUR_NOT_MATCHED
        outcome['reason'] = ('the best candidate holds %.1f %% of the route '
                             'points, below the %.0f %% floor'
                             % (best['share'] * 100.0,
                                CONTOUR_MIN_POINT_SHARE * 100.0))
        return outcome

    if second is not None:
        share_gap = best['share'] - second['share']
        if share_gap <= CONTOUR_TIE_SHARE:
            longest = max(best['length_inside_m'], second['length_inside_m'])
            length_gap = (abs(best['length_inside_m']
                              - second['length_inside_m']) / longest
                          if longest > 0 else 0.0)
            if length_gap <= CONTOUR_TIE_LENGTH:
                outcome['status'] = CONTOUR_AMBIGUOUS
                outcome['reason'] = (
                    'two candidates are indistinguishable: the point shares '
                    'differ by %.1f pp and the route length inside by %.1f %%; '
                    'no contour is assigned and no clipped area is computed'
                    % (share_gap * 100.0, length_gap * 100.0))
                return outcome
            if (best['length_inside_m'] < second['length_inside_m']
                    and second['share'] >= CONTOUR_MIN_POINT_SHARE):
                # Доли в пределах допуска -- решает длина внутри. Порог доли
                # при этом обязан взять и ПОБЕДИТЕЛЬ: иначе вторичный признак
                # протаскивал бы кандидата, которого первичный уже отверг.
                best, second = second, best
                outcome['share_inside'] = best['share']
                outcome['runner_up_share_inside'] = second['share']

    outcome['status'] = CONTOUR_MATCHED
    outcome['uuid'] = best['uuid']
    outcome['rings'] = best['rings']
    outcome['area_ha'] = best['area_ha']
    outcome['unambiguous'] = True
    outcome['reason'] = ('the winning polygon holds %.1f %% of the route '
                         'points and %.0f m of route inside it'
                         % (best['share'] * 100.0, best['length_inside_m']))
    return outcome


def rings_from_geojson(document, plane):
    """(кольца в метрах, площадь по сферической формуле в га, причины отказа).

    Кольца берутся `geometry.extract_shapes`, а годность проверяет
    `geometry.describe_geometry` -- тем же кодом, что и приёмник контуров.

    [REASON]: самопересекающийся или битый полигон НЕ используется молча.
    Несимметричная восьмёрка проходит проверку площади и даёт уверенное
    неверное число -- этот дефект трек уже ловил на ревью PR #107.
    """
    from drone_collector.geometry import describe_geometry, extract_shapes
    description, reasons = describe_geometry(document)
    if reasons:
        return None, None, reasons
    rings = []
    for _kind, outer, holes, _properties in extract_shapes(document):
        rings.append([plane.xy(position[1], position[0])
                      for position in outer])
        for hole in holes:
            rings.append([plane.xy(position[1], position[0])
                          for position in hole])
    if not rings:
        return None, None, ['no ring survived extraction']
    return rings, description.get('area_ha'), []


# ─── Разбор одной работы ─────────────────────────────────────────────────────

WIDTH_OK = 'OK'
WIDTH_ABSENT = DATA_UNAVAILABLE


def flight_label(index):
    return 'FLIGHT-%03d' % index


def _f4(value):
    return None if value is None else round(float(value), 4)


def analyse_group(basis, flights, candidates, params=DEFAULT_PARAMS):
    """Одна логическая работа -> словарь измерений.

    `candidates` -- контуры-кандидаты этой работы, отобранные по рамке:
    [{'uuid', 'geojson', 'name', 'field_serial', 'total_area_mu'}]. Какой из
    них настоящий, решает `choose_contour` по полигону, а не по рамке и не по
    порядку. При неоднозначности контур не назначается вовсе и площадь внутри
    контура не считается.

    `flights` -- приватные записи вылетов (см. `read_capture`).
    """
    all_points = []
    for flight in flights:
        all_points.extend(flight.get('points') or [])
    if not all_points:
        raise AreaStudyError('the work group carries no route point')
    plane = plane_for(all_points)

    choice = choose_contour(plane, all_points, candidates, params)
    rings = choice['rings']
    contour_ha_spherical = choice['area_ha']
    contour_rejected = sorted({reason for item in choice['scores']
                               for reason in item['rejected_because']})
    contour = None
    if choice['uuid'] is not None:
        for item in (candidates or ()):
            if item.get('uuid') == choice['uuid']:
                contour = item
                break

    tracks = []
    per_flight = []
    for flight in flights:
        points_xy = plane.project(flight.get('points') or [])
        segments = classify_segments(points_xy, params, rings)
        width = flight.get('spray_width_m')
        usable = _finite(width) and width > 0
        half = (width / 2.0) if usable else None
        tracks.append((segments, half))
        alone = coverage_once([(segments, half)], rings, params, params.cell_m)
        per_flight.append({
            'flight': flight,
            'segments': segments,
            'half_width_m': half,
            'width_status': WIDTH_OK if usable else WIDTH_ABSENT,
            'alone': alone,
            'points_xy': points_xy,
        })

    fine, coarse, uncertainty = coverage_with_uncertainty(tracks, rings,
                                                          params)

    areas = [flight.get('work_area_m2') for flight in flights
             if _finite(flight.get('work_area_m2'))]
    ordered = sorted(flights, key=lambda item: (item.get('start_ms') or 0,
                                                item.get('flight_id') or 0))
    ordered_areas = [flight.get('work_area_m2') for flight in ordered
                     if _finite(flight.get('work_area_m2'))]
    seen = {}
    for value in areas:
        seen[value] = seen.get(value, 0) + 1
    duplicates = sum(count - 1 for count in seen.values() if count > 1)

    sum_alone = sum(item['alone'].swath_all_ha or 0.0 for item in per_flight)

    return {
        'basis': basis,
        'mission_state': mission_state(flights),
        'flights': per_flight,
        'plane': plane,
        'rings': rings,
        'contour': contour,
        'contour_choice': choice,
        'contour_rejected_because': contour_rejected,
        'contour_ha_spherical': _f4(contour_ha_spherical),
        'fine': fine,
        'coarse': coarse,
        'uncertainty': uncertainty,
        'dji': {
            'flights': len(flights),
            'distinct_flight_ids': len({flight.get('flight_id')
                                        for flight in flights}),
            'sum_ha': _f4(sum(areas) / 10000.0) if areas else None,
            'max_ha': _f4(max(areas) / 10000.0) if areas else None,
            'last_ha': (_f4(ordered_areas[-1] / 10000.0)
                        if ordered_areas else None),
            'repeated_values': duplicates,
            'non_decreasing_in_time': (
                all(ordered_areas[i] >= ordered_areas[i - 1]
                    for i in range(1, len(ordered_areas)))
                if len(ordered_areas) > 1 else None),
            'mission_uuid_present': any(bool(flight.get('mission_uuid'))
                                        for flight in flights),
        },
        'sum_of_independent_swaths_ha': _f4(sum_alone),
        'flights_without_width': sum(1 for item in per_flight
                                     if item['width_status'] != WIDTH_OK),
    }


# ─── Выводы ──────────────────────────────────────────────────────────────────

# Идентификаторы выводов. Каждый получает ровно один статус и своё число.
F_REPEAT_WITHIN_WORK = 'SAME_AREA_REPEATS_WITHIN_ONE_WORK'
F_CUMULATIVE = 'NEW_WORK_AREA_LOOKS_CUMULATIVE'
F_SUM_OVERSTATES = 'PLAIN_ROW_SUM_OVERSTATES'
F_OVERLAP = 'REMAINDER_EXPLAINED_BY_SWATH_OVERLAP'
F_OUTSIDE = 'REMAINDER_EXPLAINED_BY_MOVEMENT_OUTSIDE_CONTOUR'
F_NO_WIDTH = 'REMAINDER_EXPLAINED_BY_MISSING_WIDTH'
F_LARGER_WORK = 'NEW_WORK_AREA_DESCRIBES_MORE_THAN_ITS_OWN_FLIGHT'

FINDING_QUESTIONS = {
    F_REPEAT_WITHIN_WORK: 'Повторяется ли одна площадь у нескольких частей '
                          'одного задания?',
    F_CUMULATIVE: 'Похоже ли new_work_area на накопительную площадь?',
    F_SUM_OVERSTATES: 'Завышает ли обычное суммирование строк результат?',
    F_OVERLAP: 'Объясняется ли остаток перекрытием полос?',
    F_OUTSIDE: 'Объясняется ли остаток движением вне контура?',
    F_NO_WIDTH: 'Объясняется ли остаток отсутствием ширины?',
    F_LARGER_WORK: 'Относится ли new_work_area к большей работе, а не к '
                   'этому короткому вылету?',
}


def _finding(key, status, evidence, note):
    return {'id': key, 'question': FINDING_QUESTIONS[key], 'status': status,
            'evidence': evidence, 'note': note}


def _share(part, whole):
    if part is None or whole is None or whole == 0:
        return None
    return round(part / float(whole) * 100.0, 2)


def derive_findings(groups):
    """Семь проверок задания -> семь выводов со статусами.

    Ни один статус не пишется руками: каждый выводится из чисел выборки по
    записанному здесь правилу. Гипотезы не объединяются -- у каждой свой
    статус и своё основание.
    """
    findings = []
    measured = [group for group in groups
                if group['fine'].swath_all_ha is not None]

    # 1. Повтор площади внутри одной работы.
    #
    # [REASON]: здесь ДВА разных утверждения, и раньше они были склеены в одно.
    # Первое -- «одинаковое значение действительно стоит в нескольких строках»
    # -- наблюдается прямо в данных и потому может быть PROVEN. Второе -- «эти
    # строки суть части одной работы» -- вывод из группировки, и выше него
    # SUPPORTED статуса быть не может: группировка пространственная, а
    # `mission_uuid`, который мог бы её подтвердить, сам не доказан как
    # идентификатор задания. Общий статус вывода равен статусу СЛАБОГО из двух:
    # иначе наблюдаемый факт вытягивал бы недоказанное утверждение за собой.
    multi = [group for group in groups if len(group['flights']) > 1]
    repeats_inside = [group for group in multi
                      if group['dji']['repeated_values'] > 0]
    shared_mission = [group for group in repeats_inside
                      if group['mission_state'] == MISSION_SHARED]
    all_values = [flight['flight'].get('work_area_m2')
                  for group in groups for flight in group['flights']]
    finite_values = [value for value in all_values if _finite(value)]
    repeats_anywhere = len(finite_values) - len(set(finite_values))
    rows_repeating = sum(group['dji']['repeated_values']
                         for group in repeats_inside)

    value_repeat_status = PROVEN if repeats_anywhere else DISPROVED
    if repeats_inside:
        status = SUPPORTED
        note = ('ПОВТОР ЗНАЧЕНИЯ -- наблюдаемый факт: %d лишн(яя) строк(и) с '
                'уже встреченным значением площади внутри %d работ(ы). СВЯЗЬ '
                'СТРОК С ОДНОЙ РАБОТОЙ -- вывод, не факт: группы собраны по '
                'машине, дню и перекрытию маршрутов. Совпадение mission_uuid '
                'есть у %d из них, но семантика поля не доказана и статуса '
                'не повышает'
                % (rows_repeating, len(repeats_inside), len(shared_mission)))
    elif not multi:
        status = NOT_PROVEN
        note = ('в выборке нет ни одной работы из нескольких вылетов; повторов '
                'значения по всей выборке %d' % repeats_anywhere)
    elif repeats_anywhere:
        status = NOT_PROVEN
        note = ('одинаковые значения в выборке есть (%d), но не внутри одной '
                'пространственной группы' % repeats_anywhere)
    else:
        status = DISPROVED
        note = 'в выборке нет ни одного повторяющегося значения площади'
    findings.append(_finding(
        F_REPEAT_WITHIN_WORK, status,
        {'multi_flight_works': len(multi),
         'works_with_repeats': len(repeats_inside),
         'repeated_rows_inside_works': rows_repeating,
         'repeats_anywhere_in_sample': repeats_anywhere,
         'value_repetition_is_observed_fact': value_repeat_status,
         'one_work_link_is_inferred_not_observed': status,
         'works_where_mission_uuid_is_shared': len(shared_mission),
         'mission_uuid_semantics': NOT_PROVEN},
        note))

    # 2. Накопительная площадь.
    cumulative_wins = per_flight_wins = 0
    for group in multi:
        union = group['fine'].swath_all_ha
        totals = group['dji']
        if union is None or totals['sum_ha'] is None or totals['last_ha'] is None:
            continue
        if abs(totals['last_ha'] - union) < abs(totals['sum_ha'] - union):
            cumulative_wins += 1
        elif abs(totals['sum_ha'] - union) < abs(totals['last_ha'] - union):
            per_flight_wins += 1
    if cumulative_wins and not per_flight_wins:
        status = SUPPORTED
        note = ('у %d работ(ы) последняя строка ближе к измеренному '
                'объединению, чем сумма строк' % cumulative_wins)
    elif per_flight_wins and not cumulative_wins:
        status = DISPROVED
        note = ('у %d работ(ы) сумма строк ближе к измеренному объединению, '
                'чем последняя строка: значение относится к своему вылету'
                % per_flight_wins)
    elif cumulative_wins or per_flight_wins:
        status = NOT_PROVEN
        note = ('выборка расходится: накопительная трактовка выигрывает у %d '
                'работ, повылетная у %d' % (cumulative_wins, per_flight_wins))
    else:
        status = NOT_PROVEN
        note = 'нет ни одной работы из нескольких вылетов с измеренной площадью'
    findings.append(_finding(
        F_CUMULATIVE, status,
        {'works_where_last_row_fits_better': cumulative_wins,
         'works_where_row_sum_fits_better': per_flight_wins},
        note))

    # 3. Завышает ли суммирование строк.
    overstating = strongly = understating = 0
    excess = []
    for group in measured:
        useful = (group['fine'].clipped_work_ha
                  if group['fine'].clipped_work_ha is not None
                  else group['fine'].swath_work_ha)
        total = group['dji']['sum_ha']
        if useful is None or total is None or useful == 0:
            continue
        tolerance = max(1.0, (group['uncertainty'].get('swath_work_ha') or 0.0))
        gap = (total - useful) / useful * 100.0
        excess.append(round(gap, 2))
        if gap > 3.0 * tolerance:
            strongly += 1
            overstating += 1
        elif gap > tolerance:
            overstating += 1
        elif gap < -tolerance:
            understating += 1
    if strongly and not understating:
        status = PROVEN
    elif overstating and not understating:
        status = SUPPORTED
    elif understating and not overstating:
        status = DISPROVED
    elif overstating or understating:
        status = NOT_PROVEN
    else:
        status = NOT_PROVEN
    findings.append(_finding(
        F_SUM_OVERSTATES, status,
        {'works_measured': len(excess),
         'works_where_row_sum_exceeds_useful': overstating,
         'works_exceeding_by_more_than_three_uncertainties': strongly,
         'works_where_row_sum_is_below_useful': understating,
         'excess_percent_per_work': excess},
        'превышение суммы строк над полезной площадью, процентов на работу'),
    )

    # 4..6. Из чего складывается остаток.
    overlap_shares = []
    outside_shares = []
    # [REASON]: ширина считается по ВСЕМ работам, а не только по измеренным.
    # Работа, у которой ширины нет вовсе, площади не имеет и в `measured` не
    # попадает -- то есть именно тот случай, ради которого этот вывод и
    # существует, выпадал из его же счётчика, и «ни у кого нет ширины»
    # выходило как «нечего измерить».
    width_total = sum(len(group['flights']) for group in groups)
    width_missing = sum(group['flights_without_width'] for group in groups)
    for group in measured:
        union = group['fine'].swath_all_ha
        independent = group['sum_of_independent_swaths_ha']
        clipped = group['fine'].clipped_all_ha
        total = group['dji']['sum_ha']
        if total and union is not None:
            remainder = total - (clipped if clipped is not None else union)
            if remainder and remainder > 0:
                if independent is not None:
                    overlap_shares.append(
                        _share(max(0.0, independent - union), remainder))
                if clipped is not None:
                    outside_shares.append(
                        _share(max(0.0, union - clipped), remainder))

    def _bucket(shares, key, yes_note, no_note):
        useful = [value for value in shares if value is not None]
        median = _median(useful)
        if not useful:
            status = NOT_PROVEN
            note = 'нечего измерить: остаток не положителен или не измерен'
        elif median >= 50.0:
            status = SUPPORTED
            note = yes_note % median
        elif median >= 10.0:
            status = NOT_PROVEN
            note = ('вклад есть, но он не главный: медиана %.1f %% остатка'
                    % median)
        else:
            status = DISPROVED
            note = no_note % median
        return _finding(key, status,
                        {'works_measured': len(useful),
                         'median_share_of_remainder_percent': median,
                         'share_per_work_percent': useful},
                        note)

    findings.append(_bucket(
        overlap_shares, F_OVERLAP,
        'перекрытие полос объясняет медианно %.1f %% остатка',
        'перекрытие полос объясняет лишь %.1f %% остатка'))
    findings.append(_bucket(
        outside_shares, F_OUTSIDE,
        'движение вне контура объясняет медианно %.1f %% остатка',
        'движение вне контура объясняет лишь %.1f %% остатка'))

    if width_total and width_missing:
        status = SUPPORTED if width_missing * 2 >= width_total else NOT_PROVEN
        note = ('у %d из %d вылетов выборки ширины захвата нет; их полезная '
                'площадь не считается вовсе и остаётся %s'
                % (width_missing, width_total, DATA_UNAVAILABLE))
    elif width_total:
        status = DISPROVED
        note = 'ширина захвата есть у всех вылетов выборки'
    else:
        status = NOT_PROVEN
        note = 'выборка пуста'
    findings.append(_finding(
        F_NO_WIDTH, status,
        {'flights': width_total, 'flights_without_width': width_missing},
        note))

    # 7. Площадь строки больше собственного маршрута вылета.
    beyond = 0
    checked = 0
    ratios = []
    for group in measured:
        for item in group['flights']:
            own = item['alone'].swath_all_ha
            declared = item['flight'].get('work_area_m2')
            if own is None or own <= 0 or not _finite(declared):
                continue
            checked += 1
            ratio = (declared / 10000.0) / own
            ratios.append(round(ratio, 3))
            if ratio > 1.05:
                beyond += 1
    if checked and beyond == checked:
        status = PROVEN
        note = ('у всех %d вылетов площадь строки больше площади полосы всего '
                'их собственного маршрута' % checked)
    elif beyond:
        status = SUPPORTED
        note = ('у %d из %d вылетов площадь строки больше площади полосы '
                'всего их собственного маршрута' % (beyond, checked))
    elif checked:
        status = DISPROVED
        note = ('ни у одного из %d вылетов площадь строки не превышает полосу '
                'собственного маршрута' % checked)
    else:
        status = NOT_PROVEN
        note = 'нечего сравнить: ширина или маршрут отсутствуют'
    findings.append(_finding(
        F_LARGER_WORK, status,
        {'flights_checked': checked, 'flights_above_own_route': beyond,
         'row_area_over_own_swath_ratio': ratios},
        note))
    return findings


def _median(values):
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return round(ordered[middle], 2)
    return round((ordered[middle - 1] + ordered[middle]) / 2.0, 2)


def choose_status(groups, findings, spray_state_proved=False):
    """Ровно один итоговый статус из трёх разрешённых.

    `spray_state_proved` НИКОГДА не выводится этим модулем сам. Признак
    включённого распыления считается доказанным только по независимому
    подтверждению -- имени поля в коде кабинета DJI, связи с отображением,
    управляемому переключению или однозначной корреляции с известными
    рабочими и холостыми участками. Корреляция, посчитанная здесь же на тех же
    данных, таким подтверждением не является, и превращать её в статус
    автоматически значило бы получить желаемый вывод из собственной гипотезы.
    """
    if spray_state_proved:
        return USE_SPRAY_STATE_CLIPPED_UNION, 'признак распыления доказан'
    with_width = [group for group in groups
                  if group['fine'].swath_work_ha is not None]
    with_contour = [group for group in with_width
                    if group['fine'].clipped_work_ha is not None]
    if not with_width:
        return (SOURCE_INSUFFICIENT,
                'ни у одного вылета выборки нет пригодной ширины захвата')
    if not with_contour:
        return (SOURCE_INSUFFICIENT,
                'ни одна работа выборки не связана с годным контуром поля')
    productive = sum(1 for group in with_contour
                     if (group['fine'].clipped_work_ha or 0) > 0)
    if not productive:
        return (SOURCE_INSUFFICIENT,
                'рабочие проходы внутри контура не выделяются: полезная '
                'площадь везде нулевая')
    return (USE_IN_CONTOUR_WORK_PASS_UNION,
            'ширина, маршрут и контур позволяют выделить рабочие проходы; '
            'признак распыления не доказан, поэтому результат называется '
            'расчётной полезной площадью')


# ─── Два уровня файлов ───────────────────────────────────────────────────────

STUDY_DIR_NAME = 'area_48h'
PRIVATE_DIR_NAME = 'private'
SHAREABLE_JSON = 'DJI_AREA_48H_SHAREABLE.json'
SHAREABLE_MD = 'DJI_AREA_48H_SHAREABLE.md'
CAPTURE_NAME = 'capture.json'

_UUID = re.compile(r'\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}'
                   r'-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b')

# Целое такого порядка -- это идентификатор вылета или метка времени в
# миллисекундах. В безопасном отчёте ни того, ни другого быть не может.
MAX_SAFE_INTEGER = 10 ** 9


class ShareableLeak(Exception):
    """В безопасном отчёте нашлось то, чего в нём быть не может."""


def _walk_values(node, path='$'):
    if isinstance(node, dict):
        for key in node:
            yield (path + '.' + str(key), key)
            for item in _walk_values(node[key], path + '.' + str(key)):
                yield item
    elif isinstance(node, (list, tuple)):
        for index, item in enumerate(node):
            for found in _walk_values(item, '%s[%d]' % (path, index)):
                yield found
    else:
        yield (path, node)


def private_strings(capture):
    """Всё, что из приватного снимка не имеет права попасть в отчёт.

    Строки собираются из НАСТОЯЩЕГО материала прогона: идентификаторы вылетов,
    uuid заданий и контуров, серийные номера, ники, имена, адреса и координаты
    в нескольких точностях округления.

    [REASON]: это и есть отрицательный контроль на утечку, а не декларация.
    Проверка «отчёт не содержит слова cookie» одинаково проходит и на чистом
    отчёте, и на отчёте с настоящими координатами -- то есть проверкой не
    является. Проверка на конкретные строки этого прогона различает эти два
    случая, и на неё стоит тест, который подкладывает настоящий ID в отчёт и
    требует отказа.
    """
    found = set()

    def add(value):
        # [REASON]: у числовых строк порог выше. Значение неизвестного поля
        # вроде `1305` совпало бы с числом точек в отчёте, и защита от утечки
        # отказалась бы писать чистый отчёт -- то есть сорвала бы прогон,
        # ради которого владелец ходил в кабинет. Идентификаторы вылетов
        # (девять цифр), метки времени (тринадцать) и координаты, которые
        # форматируются минимум с тремя знаками после точки, через этот порог
        # проходят; короткие числа, по которым ничего не восстановить, нет.
        if value is None:
            return
        text = str(value).strip()
        numeric = text.replace('-', '', 1).replace('.', '', 1).isdigit()
        if len(text) >= (6 if numeric else 4):
            found.add(text)

    for flight in capture.get('flights') or ():
        add(flight.get('flight_id'))
        add(flight.get('mission_uuid'))
        add(flight.get('nickname'))
        add(flight.get('hardware_id'))
        add(flight.get('flyer_name'))
        add(flight.get('team_name'))
        add(flight.get('location'))
        add(flight.get('start_ms'))
        add(flight.get('end_ms'))
        for lat, lon in (flight.get('points') or ())[:4000]:
            for digits in (3, 4, 5, 6):
                add('%.*f' % (digits, lat))
                add('%.*f' % (digits, lon))
        for series in (flight.get('unknown_values') or {}).values():
            for value in series[:200]:
                add(value)
    for contour in capture.get('contours') or ():
        add(contour.get('uuid'))
        add(contour.get('field_serial'))
        add(contour.get('name'))
    return found


def assert_shareable(document, forbidden=()):
    """Отказ, если в безопасном отчёте есть приватное. Пишем только после.

    Три независимых контроля, и ни один не покрывает другой:

    1. маркеры секретов -- тот же список, что защищает очередь;
    2. форма -- uuid и целые числа порядка идентификатора вылета;
    3. содержимое -- конкретные строки ЭТОГО прогона.
    """
    from drone_collector.outbox import find_secret_markers
    text = json.dumps(document, ensure_ascii=False, sort_keys=True)
    markers = find_secret_markers(text)
    if markers:
        raise ShareableLeak('the shareable report carries %s'
                            % ', '.join(markers))
    found = _UUID.search(text)
    if found:
        raise ShareableLeak('the shareable report carries a uuid')
    for path, value in _walk_values(document):
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and abs(value) >= MAX_SAFE_INTEGER:
            raise ShareableLeak('the shareable report carries an integer of '
                                'identifier size at %s' % path)
    for secret in forbidden:
        if secret and secret in text:
            # Само значение НЕ печатается: сообщение уходит в лог.
            raise ShareableLeak('the shareable report repeats a private value '
                                'of %d characters' % len(secret))
    return True


def build_private(capture, groups, findings, status, reason,
                  params=DEFAULT_PARAMS):
    """Приватный разбор. Остаётся на машине владельца и никуда не уходит."""
    works = []
    for index, group in enumerate(groups, start=1):
        choice = group['contour_choice']
        works.append({
            'work': 'WORK-%03d' % index,
            'basis': group['basis'],
            'mission_state': group['mission_state'],
            'contour_uuid': (group['contour'] or {}).get('uuid'),
            'contour_name': (group['contour'] or {}).get('name'),
            'contour_status': choice['status'],
            'contour_reason': choice['reason'],
            'contour_candidates': [
                {'uuid': item['uuid'], 'share_inside': item['share'],
                 'length_inside_m': item['length_inside_m'],
                 'area_ha': item['area_ha'],
                 'rejected_because': item['rejected_because']}
                for item in choice['scores']],
            'dji': group['dji'],
            'fine': group['fine'].as_dict(),
            'coarse': group['coarse'].as_dict(),
            'uncertainty': group['uncertainty'],
            'flights': [{
                'flight_id': item['flight'].get('flight_id'),
                'nickname': item['flight'].get('nickname'),
                'mission_uuid': item['flight'].get('mission_uuid'),
                'spray_width_m': item['flight'].get('spray_width_m'),
                'width_status': item['width_status'],
                'work_area_m2': item['flight'].get('work_area_m2'),
                'points': len(item['flight'].get('points') or []),
                'segments': segment_totals(item['segments']),
                'alone': item['alone'].as_dict(),
                'unknown_fields': {
                    key: dict(describe_series(values),
                              agreement_with_work=agreement_with_work(
                                  values, item['segments']))
                    for key, values in
                    (item['flight'].get('unknown_values') or {}).items()},
            } for item in group['flights']],
        })
    return {
        'study_version': STUDY_VERSION,
        'private': True,
        'never_share_this_file': True,
        'day': capture.get('day'),
        'decoder_version': capture.get('decoder_version'),
        'params': params.as_dict(),
        'status': status,
        'status_reason': reason,
        'findings': findings,
        'works': works,
    }


def build_shareable(capture, groups, findings, status, reason,
                    params=DEFAULT_PARAMS, notes=None):
    """Безопасный отчёт. Только агрегаты, причины, площади и счётчики."""
    labels = {}
    counter = 0
    works = []
    for index, group in enumerate(groups, start=1):
        rows = []
        for item in group['flights']:
            counter += 1
            key = item['flight'].get('flight_id')
            labels[key] = flight_label(counter)
            unknown = {}
            for name, values in sorted(
                    (item['flight'].get('unknown_values') or {}).items()):
                number, wire = name.split(':')
                described = describe_series(values)
                unknown['field_%s_wire_%s' % (number, wire)] = {
                    'field_number': int(number),
                    'wire_type': int(wire),
                    'points_with_the_field': described['count'],
                    'distinct_values': described['distinct'],
                    'changes_along_the_route': described['changes'],
                    'transitions': described['transitions'],
                    'monotonic_non_decreasing':
                        described['monotonic_non_decreasing'],
                    'pattern': described['pattern'],
                    'agreement_with_geometric_work_split':
                        agreement_with_work(values, item['segments']),
                    'semantics': 'UNKNOWN_SEMANTICS',
                }
            rows.append({
                'flight': labels[key],
                'points': len(item['flight'].get('points') or []),
                'width_status': item['width_status'],
                'spray_width_m': (item['flight'].get('spray_width_m')
                                  if item['width_status'] == WIDTH_OK
                                  else None),
                'dji_row_area_ha': _f4(
                    (item['flight'].get('work_area_m2') or 0) / 10000.0
                    if _finite(item['flight'].get('work_area_m2')) else None),
                'own_route_swath_ha': item['alone'].swath_all_ha,
                'segments': segment_totals(item['segments']),
                'unknown_point_fields': unknown,
            })
        fine = group['fine']
        choice = group['contour_choice']
        works.append({
            'work': 'WORK-%03d' % index,
            'grouping_basis': group['basis'],
            'mission_identifier_state': group['mission_state'],
            'mission_identifier_semantics': NOT_PROVEN,
            'flights': [row['flight'] for row in rows],
            'flight_count': len(rows),
            'distinct_dji_flight_ids': group['dji']['distinct_flight_ids'],
            'dji_row_area_sum_ha': group['dji']['sum_ha'],
            'dji_row_area_max_ha': group['dji']['max_ha'],
            'dji_row_area_last_ha': group['dji']['last_ha'],
            'dji_row_area_repeated_values': group['dji']['repeated_values'],
            'dji_row_area_non_decreasing_in_time':
                group['dji']['non_decreasing_in_time'],
            'whole_route_swath_union_ha': fine.swath_all_ha,
            'whole_route_swath_clipped_to_contour_ha': fine.clipped_all_ha,
            'work_pass_union_ha': fine.swath_work_ha,
            'work_pass_union_clipped_to_contour_ha': fine.clipped_work_ha,
            'sum_of_independent_flight_swaths_ha':
                group['sum_of_independent_swaths_ha'],
            'contour_area_ha_raster': fine.contour_ha,
            'contour_area_ha_spherical': group['contour_ha_spherical'],
            'contour_rejected_because': group['contour_rejected_because'],
            'contour_status': choice['status'],
            'contour_reason': choice['reason'],
            'contour_candidates_offered': choice['candidates_offered'],
            'contour_candidates_usable': choice['candidates_usable'],
            'contour_point_share_inside': choice['share_inside'],
            'contour_runner_up_share_inside': choice['runner_up_share_inside'],
            'contour_choice_unambiguous': choice['unambiguous'],
            'contour_matched': bool(group['rings']),
            'flights_without_width': group['flights_without_width'],
            'grid_cell_m': fine.cell_m,
            'grid_was_coarsened': fine.coarsened,
            'discretisation_uncertainty_percent': group['uncertainty'],
            'rows': rows,
        })
    return {
        'report': 'DJI-AREA-48H',
        'study_version': STUDY_VERSION,
        'decoder_version': capture.get('decoder_version'),
        'day': capture.get('day'),
        'contains_no_flight_ids': True,
        'contains_no_coordinates': True,
        'contains_no_unknown_field_values': True,
        'nothing_was_sent_to_vehicle_soft': True,
        'no_database_table_was_created_or_changed': True,
        'params': params.as_dict(),
        'method': ('useful_area = area(union(buffer(work_segments, '
                   'spray_width / 2)) INTERSECT field_polygon) / 10000'),
        'geometry_engine': ('deterministic raster, cell-centre sampling; every '
                            'area is computed at cell and at 2*cell and the '
                            'gap between them is published as the method own '
                            'uncertainty'),
        'segment_reasons': list(SEGMENT_REASONS),
        'flights_total': counter,
        'works_total': len(works),
        'flights_rejected_wrong_day': capture.get('flights_rejected_wrong_day'),
        'study_day_requested': capture.get('study_day_requested'),
        'live_run_confirmed': (capture.get('live_run') or {}).get('confirmed'),
        'live_run_not_confirmed_because':
            list((capture.get('live_run') or {}).get('reasons') or ()),
        'findings': findings,
        'final_status': status,
        'final_status_reason': reason,
        'spray_state_proof': 'NOT_ESTABLISHED',
        'notes': list(notes or ()),
        'works': works,
    }


def _cell(value):
    if value is None:
        return '-'
    if isinstance(value, bool):
        return 'да' if value else 'нет'
    if isinstance(value, float):
        return ('%.4f' % value).rstrip('0').rstrip('.')
    return str(value)


def render_markdown(document):
    """Безопасный отчёт словами. Ровно те же числа, что в JSON."""
    lines = []
    add = lines.append
    add('# DJI-AREA-48H -- безопасный отчёт')
    add('')
    add('День выборки: **%s**. Декодер: `%s`. Версия расчёта: `%s`.'
        % (document.get('day') or '-', document.get('decoder_version') or '-',
           document.get('study_version')))
    add('')
    add('Настоящих идентификаторов вылетов, координат, uuid, тел ответов и '
        'значений неизвестных полей в этом файле нет. Вылеты обозначены '
        'метками `FLIGHT-NNN`.')
    add('')
    add('## Итог')
    add('')
    add('**%s**' % document.get('final_status'))
    add('')
    add(document.get('final_status_reason') or '')
    add('')
    add('Признак распыления: **%s**.' % document.get('spray_state_proof'))
    add('')
    confirmed = document.get('live_run_confirmed')
    if confirmed is None:
        add('Живой прогон: пересчёт с диска, приговор прогона в снимке не '
            'записан.')
    elif confirmed:
        add('Живой прогон: **подтверждён** по всем обязательным признакам.')
    else:
        add('Живой прогон: **НЕ подтверждён**. Числа ниже читать как '
            'предварительные.')
        for reason in document.get('live_run_not_confirmed_because') or ():
            add('- %s' % reason)
    add('')
    add('Запрошенный день: **%s**. Вылетов другого дня отброшено: **%s**.'
        % (document.get('study_day_requested') or '-',
           _cell(document.get('flights_rejected_wrong_day'))))
    add('')
    add('## Метод')
    add('')
    add('```')
    add(document.get('method') or '')
    add('```')
    add('')
    add(document.get('geometry_engine') or '')
    add('')
    add('Пороги расчёта: ' + ', '.join(
        '`%s = %s`' % (key, _cell(value))
        for key, value in sorted((document.get('params') or {}).items())))
    add('')
    add('## Выводы')
    add('')
    add('| Вопрос | Статус | Основание |')
    add('|---|---|---|')
    for finding in document.get('findings') or ():
        add('| %s | `%s` | %s |' % (finding['question'], finding['status'],
                                    finding['note']))
    add('')
    add('## Работы')
    add('')
    add('| Работа | Вылетов | Сумма строк, га | Макс, га | Последняя, га | '
        'Повторов | Полоса маршрута, га | В контуре, га | Рабочие проходы в '
        'контуре, га | Контур, га | Погрешность, % |')
    add('|---|---|---|---|---|---|---|---|---|---|---|')
    for work in document.get('works') or ():
        uncertainty = (work.get('discretisation_uncertainty_percent')
                       or {}).get('clipped_work_ha')
        add('| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |' % (
            work['work'], work['flight_count'],
            _cell(work['dji_row_area_sum_ha']),
            _cell(work['dji_row_area_max_ha']),
            _cell(work['dji_row_area_last_ha']),
            _cell(work['dji_row_area_repeated_values']),
            _cell(work['whole_route_swath_union_ha']),
            _cell(work['whole_route_swath_clipped_to_contour_ha']),
            _cell(work['work_pass_union_clipped_to_contour_ha']),
            _cell(work['contour_area_ha_raster']),
            _cell(uncertainty)))
    add('')
    add('## Контур и идентификатор задания')
    add('')
    add('Контур выбирается по доле точек маршрута внутри НАСТОЯЩЕГО полигона, '
        'а не по прямоугольной рамке. При неоднозначности площадь внутри '
        'контура не считается вовсе.')
    add('')
    add('| Работа | Основание группы | mission_uuid | Кандидатов | Годных | '
        'Доля внутри | У второго | Однозначно | Статус |')
    add('|---|---|---|---|---|---|---|---|---|')
    for work in document.get('works') or ():
        add('| %s | `%s` | `%s` | %s | %s | %s | %s | %s | `%s` |' % (
            work['work'], work['grouping_basis'],
            work.get('mission_identifier_state'),
            _cell(work.get('contour_candidates_offered')),
            _cell(work.get('contour_candidates_usable')),
            _cell(work.get('contour_point_share_inside')),
            _cell(work.get('contour_runner_up_share_inside')),
            _cell(work.get('contour_choice_unambiguous')),
            work.get('contour_status')))
    add('')
    add('Семантика `mission_uuid` остаётся `NOT_PROVEN`: поле не доказано как '
        'идентификатор задания, в группировке не участвует и ни на одну '
        'площадь не влияет.')
    add('')
    add('## Вылеты')
    add('')
    add('| Вылет | Работа | Точек | Ширина | Строка DJI, га | '
        'Полоса своего маршрута, га | Рабочая длина, м | Вне контура, м | '
        'Разрывы, м |')
    add('|---|---|---|---|---|---|---|---|---|')
    for work in document.get('works') or ():
        for row in work.get('rows') or ():
            segments = row.get('segments') or {}
            add('| %s | %s | %s | %s | %s | %s | %s | %s | %s |' % (
                row['flight'], work['work'], row['points'],
                _cell(row['spray_width_m']) if row['width_status'] == 'OK'
                else row['width_status'],
                _cell(row['dji_row_area_ha']),
                _cell(row['own_route_swath_ha']),
                _cell((segments.get(SEG_WORK) or {}).get('length_m')),
                _cell((segments.get(SEG_OUTSIDE) or {}).get('length_m')),
                _cell((segments.get(SEG_GAP) or {}).get('length_m'))))
    add('')
    add('## Неизвестные поля точки')
    add('')
    add('Значения не публикуются. Публикуется только форма ряда.')
    add('')
    add('| Вылет | Поле | Wire | Точек | Различных | Меняется | Переключений '
        '| Не убывает | Портрет | Согласие с геометрией | Смысл |')
    add('|---|---|---|---|---|---|---|---|---|---|---|')
    for work in document.get('works') or ():
        for row in work.get('rows') or ():
            for name in sorted(row.get('unknown_point_fields') or {}):
                field = row['unknown_point_fields'][name]
                add('| %s | %s | %s | %s | %s | %s | %s | %s | `%s` | %s | '
                    '`%s` |' % (
                        row['flight'], field['field_number'],
                        field['wire_type'], field['points_with_the_field'],
                        _cell(field['distinct_values']),
                        _cell(field['changes_along_the_route']),
                        _cell(field['transitions']),
                        _cell(field['monotonic_non_decreasing']),
                        field['pattern'],
                        _cell(field['agreement_with_geometric_work_split']),
                        field['semantics']))
    add('')
    if document.get('notes'):
        add('## Замечания прогона')
        add('')
        for note in document['notes']:
            add('- %s' % note)
        add('')
    add('---')
    add('')
    add('Production и staging не затрагивались. В Vehicle Soft ничего не '
        'отправлялось. Таблиц не создавалось, миграций не применялось, '
        'начисления и подтверждённые гектары не менялись.')
    return '\n'.join(lines) + '\n'


# ─── Приговор живому прогону ─────────────────────────────────────────────────

# Коды выхода исследования. Совпадают с кодами `main.py` намеренно, и на это
# совпадение стоит тест: два места, называющие один исход разными числами, --
# это ровно тот дефект, который приводит к ложному PASS.
EXIT_STUDY_OK = 0
EXIT_STUDY_EMPTY = 6
EXIT_STUDY_UNCONFIRMED = 13


def live_run_verdict(operator_answered, drain_completed, observations,
                     confirmed, skipped_over_cap, observation_errors,
                     capture_errors, pending_route_requests,
                     route_requests_failed, id_sets_matched,
                     flights_of_study_day, flights_rejected_wrong_day=0,
                     study_day=None):
    """Подтверждён ли живой прогон. Ровно одна функция, ровно одно решение.

    [REASON]: раньше достаточно было ОДНОГО декодированного маршрута -- и
    прогон возвращал 0, а PowerShell печатал `AREA48H=PASS`, даже если
    оператор не подтвердил карту, drain истёк по таймауту, слушатель спотыкался
    на ответах, часть ответов осталась неподтверждённой или в выборку попал
    чужой день. Успешный прогон и наполовину состоявшийся давали ОДИН и тот же
    признак, а признак, одинаковый в двух разных случаях, признаком не
    является. Здесь перечислено всё, что обязано выполниться ОДНОВРЕМЕННО.

    Причины возвращаются безопасными строками: ни одного идентификатора, ни
    одной координаты, только счётчики и имена условий.
    """
    reasons = []
    if not operator_answered:
        reasons.append('the operator never confirmed the map view')
    if not drain_completed:
        reasons.append('route traffic had not settled when the browser closed')
    if observation_errors:
        reasons.append('%d response(s) could not be read by the listener'
                       % observation_errors)
    if capture_errors:
        reasons.append('%d observed response(s) could not be captured'
                       % capture_errors)
    if pending_route_requests:
        reasons.append('%d route request(s) were still unfinished'
                       % pending_route_requests)
    if route_requests_failed:
        reasons.append('%d route request(s) failed before their body arrived'
                       % route_requests_failed)
    if skipped_over_cap:
        reasons.append('%d observation(s) were dropped by the cap'
                       % skipped_over_cap)
    if confirmed < 1:
        reasons.append('not one route POST was confirmed')
    elif confirmed != observations:
        reasons.append('%d of %d route response(s) are not confirmed'
                       % (observations - confirmed, observations))
    if not id_sets_matched:
        reasons.append('the requested and returned flight-id sets did not '
                       'match on every response')
    if flights_of_study_day < 1:
        reasons.append('not one route of %s was captured'
                       % (study_day or 'the requested day'))
    return {
        'confirmed': not reasons,
        'reasons': reasons,
        'day_requested': study_day,
        'flights_of_study_day': flights_of_study_day,
        'flights_rejected_wrong_day': flights_rejected_wrong_day,
    }


def study_exit_code(any_route_captured, verdict):
    """Код выхода исследовательского прогона.

    0 -- только когда захвачено хоть что-то И приговор подтверждён.
    6 -- не захвачено вообще ничего.
    13 -- захвачено, но прогон не подтверждён; приватный снимок сохранён, и
    пересчитать его можно через `--replay`, не идя в кабинет второй раз.
    """
    if not any_route_captured:
        return EXIT_STUDY_EMPTY
    return EXIT_STUDY_OK if verdict['confirmed'] else EXIT_STUDY_UNCONFIRMED


def split_by_day(flights, study_day):
    """(вылеты нужного дня, сколько отброшено). Дни не смешиваются.

    [REASON]: маршрут другого дня -- это чужая работа на, возможно, чужом поле.
    Попав в ту же группу, он растянул бы рамку, притянул бы чужой контур и
    добавил бы площадь, которой в спорном дне нет. Отброшенные считаются
    числом: счётчик безопасен, идентификаторы наружу не идут.
    """
    kept = [flight for flight in flights if flight.get('day') == study_day]
    return kept, len(flights) - len(kept)


# ─── Приватный снимок ────────────────────────────────────────────────────────

def study_dir(out_dir):
    return Path(out_dir) / STUDY_DIR_NAME


def private_dir(out_dir):
    return study_dir(out_dir) / PRIVATE_DIR_NAME


def record_to_capture(record, day=None, contour_uuid=None, unknown=None):
    """RouteRecord -> приватная запись вылета. Сырого тела здесь нет."""
    return {
        'flight_id': record.flight_id,
        'nickname': record.nickname,
        'hardware_id': record.hardware_id,
        'flyer_name': record.flyer_name,
        'team_name': record.team_name,
        'location': record.location,
        'mission_uuid': record.mission_uuid,
        'mode_name': record.mode_name,
        'drone_type': record.drone_type,
        'start_ms': record.start_ms,
        'end_ms': record.end_ms,
        'duration_ms': record.duration_ms,
        'work_area_m2': record.work_area_m2,
        'spray_width_m': (record.spray_width_m
                          if record.spray_width_known else None),
        'spray_width_raw': record.spray_width_m,
        'takeoff': list(record.takeoff) if record.takeoff else None,
        'points': [list(point) for point in record.points],
        'unknown_values': unknown or {},
        'day': day or day_of(record.start_ms),
        'contour_uuid': contour_uuid,
    }


def day_of(start_ms, tz_offset_hours=5):
    """Календарный день вылета в местной зоне. `None`, если времени нет.

    [REASON]: наивная метка не годится -- вылет в 21:30 по Бухаре это уже
    следующие сутки UTC, и группировка «одна машина, один день» тихо разрезала
    бы вечернюю работу надвое. Смещение задаётся явно и записывается в снимок.
    """
    if not isinstance(start_ms, int) or isinstance(start_ms, bool):
        return None
    from datetime import datetime, timedelta, timezone
    moment = datetime.fromtimestamp(start_ms / 1000.0, tz=timezone.utc)
    return (moment + timedelta(hours=tz_offset_hours)).strftime('%Y-%m-%d')


def write_capture(out_dir, capture):
    """Приватный снимок на диск. Каталог исключён из git."""
    target = private_dir(out_dir) / CAPTURE_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('w', encoding='utf-8') as handle:
        json.dump(capture, handle, ensure_ascii=False)
    return target


def read_capture(path):
    with Path(path).open('r', encoding='utf-8') as handle:
        return json.load(handle)


def archive_existing(out_dir):
    """Отодвинуть прошлый безопасный отчёт. Возвращает список переименований.

    [REASON]: не удалить и не перезаписать. Отчёт прошлого прогона -- это
    свидетельство, и оно должно пережить прогон, который его заменяет: иначе
    сравнить «было / стало» будет уже нечем.
    """
    from datetime import datetime, timezone
    moved = []
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    for name in (SHAREABLE_JSON, SHAREABLE_MD):
        source = study_dir(out_dir) / name
        if source.exists():
            target = source.with_name('%s.%s.bak' % (name, stamp))
            source.replace(target)
            moved.append(str(target))
    return moved


# ─── Прогон разбора ──────────────────────────────────────────────────────────

def run_study(capture, params=DEFAULT_PARAMS, notes=None,
              spray_state_proved=False):
    """Приватный снимок -> (приватный документ, безопасный документ).

    Ничего не пишет на диск и ничего никуда не отправляет: только считает.
    Отделено от записи намеренно -- так весь расчёт проверяется тестом без
    файловой системы, а запись проверяется отдельно.
    """
    flights = list(capture.get('flights') or ())
    contours = {contour.get('uuid'): contour
                for contour in (capture.get('contours') or ())}
    groups = []
    problems = list(notes or ())
    if capture.get('live_run') and not capture['live_run'].get('confirmed'):
        # [REASON]: неподтверждённый живой прогон не перестаёт быть
        # неподтверждённым оттого, что его пересчитали. Оговорка едет вместе с
        # числами, иначе отчёт из `--replay` читался бы как результат чистого
        # прогона.
        problems.append('LIVE RUN NOT CONFIRMED: '
                        + '; '.join(capture['live_run'].get('reasons') or ()))
    if capture.get('flights_rejected_wrong_day'):
        problems.append('%d flight(s) of another day were left out of the '
                        'sample' % capture['flights_rejected_wrong_day'])
    for basis, members in group_flights(flights):
        # Кандидаты собираются со ВСЕХ вылетов работы: рамка отбирала их
        # повылетно, а решать надо по маршруту всей работы сразу.
        wanted = []
        seen = set()
        for flight in members:
            for uuid in (flight.get('contour_candidates') or ()):
                if uuid in contours and uuid not in seen:
                    seen.add(uuid)
                    wanted.append(contours[uuid])
        try:
            groups.append(analyse_group(basis, members, wanted, params))
        except AreaStudyError as exc:
            problems.append('одна работа не посчитана: %s' % exc)
    groups.sort(key=lambda group: (-(group['dji']['sum_ha'] or 0.0),
                                   group['basis']))
    findings = derive_findings(groups)
    status, reason = choose_status(groups, findings, spray_state_proved)
    for group in groups:
        for reject in group['contour_rejected_because']:
            problems.append('контур отвергнут: %s' % reject)
        if group['fine'].coarsened:
            problems.append('сетка огрублена до %.2f м, чтобы уложиться в '
                            'потолок клеток' % group['fine'].cell_m)
    private = build_private(capture, groups, findings, status, reason, params)
    shareable = build_shareable(capture, groups, findings, status, reason,
                                params, notes=problems)
    return private, shareable


def write_reports(out_dir, capture, private, shareable):
    """Записать оба уровня. Безопасный -- только после проверки на утечку."""
    assert_shareable(shareable, private_strings(capture))
    private_target = private_dir(out_dir) / 'analysis.json'
    private_target.parent.mkdir(parents=True, exist_ok=True)
    with private_target.open('w', encoding='utf-8') as handle:
        json.dump(private, handle, ensure_ascii=False, indent=2)

    json_target = study_dir(out_dir) / SHAREABLE_JSON
    json_target.parent.mkdir(parents=True, exist_ok=True)
    with json_target.open('w', encoding='utf-8') as handle:
        json.dump(shareable, handle, ensure_ascii=False, indent=2)

    md_target = study_dir(out_dir) / SHAREABLE_MD
    text = render_markdown(shareable)
    for secret in private_strings(capture):
        if secret and secret in text:
            raise ShareableLeak('the markdown report repeats a private value '
                                'of %d characters' % len(secret))
    with md_target.open('w', encoding='utf-8') as handle:
        handle.write(text)
    return {'private': str(private_target), 'json': str(json_target),
            'md': str(md_target)}


# ─── Живой захват ────────────────────────────────────────────────────────────

class AreaCapture(RouteUiProbe):
    """Слушатель, который вдобавок к наблюдению ЗАПОМИНАЕТ разобранные
    маршруты -- в памяти, приватно и без сырого тела.

    Наследник `RouteUiProbe`, а не вторая реализация: жизненный цикл запроса в
    кабинете DJI уже стоил треку двух живых прогонов (`TargetClosedError` на
    всех пяти ответах; запрос, невидимый для drain), и написать этот цикл
    заново значило бы написать заново и эти дефекты. Точка расширения ровно
    одна -- `_decode_ids`, которому сырое тело достаётся уже после
    классификации и потолка размера.

    [REASON]: сбой ЗАХВАТА не смеет уронить НАБЛЮДЕНИЕ. Наблюдение -- это
    подтверждение живого прогона, ради которого владелец сидит у браузера;
    исследование площадей вторично. Поэтому исключение здесь считается и
    называется по типу, а наблюдение продолжается.
    """

    def __init__(self, logger=None, expected_origin=None, clock=None):
        RouteUiProbe.__init__(self, logger=logger,
                              expected_origin=expected_origin, clock=clock)
        self.routes = {}
        self.capture_errors = 0
        self.bodies_captured = 0

    def _decode_ids(self, raw):
        outcome = RouteUiProbe._decode_ids(self, raw)
        try:
            self._stash(raw)
        except Exception as exc:
            self.capture_errors += 1
            self.log.warning('The route body was observed but could not be '
                             'captured for the area study (%s); %d such '
                             'failure(s) so far',
                             safe_exception_name(exc), self.capture_errors)
        return outcome

    def _stash(self, raw):
        from drone_collector.route_decode import decode_route_response
        decoded = decode_route_response(raw)
        if not decoded.is_ok:
            return
        self.bodies_captured += 1
        unknown = unknown_point_values(raw)
        for record in decoded.routes:
            if record.flight_id is None:
                continue
            if record.flight_id in self.routes:
                # Тот же вылет во втором ответе -- не второй вылет.
                continue
            self.routes[record.flight_id] = record_to_capture(
                record, unknown=unknown.get(record.flight_id) or {})

    def captured_flights(self):
        """Вылеты в устойчивом порядке: по времени старта, затем по id."""
        return sorted(self.routes.values(),
                      key=lambda item: (item.get('start_ms') or 0,
                                        item.get('flight_id') or 0))


# Спорный день выборки. Записан константой, а не спрятан в инструкции: отчёт
# должен уметь сказать, какой день просили открыть, даже если открыли другой.
STUDY_DAY = '2026-06-05'

# Что человек делает руками. Только ASCII: строки печатаются в консоль
# PowerShell, где кодовая страница -- не наша забота и не наша гарантия.
PROMPT_LINES = (
    '',
    '=== DJI-AREA-48H: one live run, driven by hand ===',
    '',
    'In the browser window that just opened:',
    '  1. open Task History (flight records);',
    '  2. choose the day %s;' % STUDY_DAY,
    '  3. switch to the MAP view;',
    '  4. wait until the routes are actually drawn on the map;',
    '  5. come back here and press Enter.',
    '',
    'The cabinet signs its own request. This tool only listens: it does not',
    'initiate the route POST, does not reproduce the signature, and sends',
    'nothing to Vehicle Soft.',
    '',
)
