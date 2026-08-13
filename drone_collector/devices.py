# -*- coding: utf-8 -*-
"""drone_collector/devices.py -- DRONE-BODYCODE-001: снять вылеты ПО КАЖДОМУ
БОРТУ, чтобы связь «вылет -> планер» была прочитана, а не выведена из сумм.

Зачем это существует. Привязка вылета к машине идёт по строке ника, а ники в
DJI переезжали между планерами. Октябрь-2025 и март-2026 удалось решить
сверкой помесячных сумм (единственная раскладка), а сентябрь-2025 -- нет: там
раскладки целых написаний по бортам НЕ СУЩЕСТВУЕТ, значит одно написание
носили два разных борта внутри месяца. Додумывать нельзя, надо прочитать.

Серийника планера в ответе списка вылетов нет -- проверено на проде, 32 179
вылетов, 35 полей payload. Зато у сайта есть ФИЛЬТР ПО УСТРОЙСТВУ, а
устройство опознаётся серийником и переименование его не трогает. Поэтому
режим делает ровно одно: для каждого устройства ставит фильтр и проходит его
страницы, записывая `id` каждого вылета рядом с именем устройства. Дальше
`id` -- это `drone_flights.dji_flight_id`, и привязка становится прочитанной
фактом, без всяких сумм.

ЭТО ЧИТАТЕЛЬ. Ни одной записи в базу приложения, ни одного POST в
/drones/api/flight_sync. Всё, что режим делает наружу, -- кладёт файлы в
каталог --out.

Что кладётся в --out:
  flights_by_device.csv   dji_flight_id;device;nickname;started_utc;area_ha
  summary.json            по каждому устройству: вылетов, гектаров, страниц
  raw/<устройство>.json   сырые тела ответов, как их прислал сайт

[REASON]: сырые тела сохраняются ВСЕГДА. Разбор полей может ошибиться, а
сырой ответ разобрать заново можно; повторный прогон по сайту стоит часа и
зависит от живой сессии. Это то же правило, по которому drone_flights хранит
raw_json.

ОГРАНИЧЕНИЯ, УНАСЛЕДОВАННЫЕ ОТ САЙТА (см. browser.py, они проверены живьём):
  * DJI подписывает каждый запрос своим WASM -- URL править нельзя, период и
    фильтр ставятся только родными контролами страницы;
  * размер страницы максимум 50, сотни нет;
  * успех -- это `code: 0` внутри тела, а не HTTP 200;
  * `Other Regions` в меню -- переключатель страны, случайный клик молча даёт
    ноль.

СЕЛЕКТОРЫ ФИЛЬТРА НАПИСАНЫ ВСЛЕПУЮ. Сайт из среды разработки недоступен, и
сохранённого DOM панели фильтра нет -- в дампе 2026-07-31 её никто не
открывал. Поэтому:
  * каждый селектор -- константа ниже, с запасными вариантами;
  * при первом открытии панель ЛОГИРУЕТСЯ целиком (обрезанная), чтобы по
    журналу первого прогона селекторы можно было поправить в одном месте, а
    не подбирать наугад;
  * режим проверяет себя: сумма вылетов по всем устройствам сравнивается с
    числом вылетов без фильтра за тот же период, и расхождение печатается как
    ОШИБКА, а не прячется.

Запуск (браузер откроется от имени пользователя, у которого сохранена сессия):

  cd C:\\transport-report
  drone_collector\\.venv\\Scripts\\python.exe -m drone_collector.devices --from 2025-09-01 --to 2025-09-30 --out C:\\qa\\dji_sept

[REASON]: запускается ПИТОНОМ СБОРЩИКА, не приложения. Playwright и Chromium
стоят только в drone_collector\\.venv; в "C:\\Program Files\\Python314" их нет,
и запуск оттуда падает на импорте playwright.

Коды возврата:
  0  снято, сумма по устройствам сошлась с прогоном без фильтра
  1  ошибка настройки или каталога
  2  сессия отсутствует или истекла
  3  период не подтвердился
  4  обход страниц не завершился хотя бы для одного устройства
  8  список устройств не прочитан (селектор фильтра не сработал)
  9  снято, но сумма по устройствам НЕ сошлась -- данные есть, доверять им
     нельзя без разбора журнала
"""

import argparse
import csv
import json
import os
import sys

from drone_collector import browser as browser_module
from drone_collector.browser import (BrowserError, FlightCollector,
                                     PeriodVerificationFailed, SessionExpired,
                                     dedupe_flights, pages_seen)
from drone_collector.config import ConfigError, load_config
from drone_collector.logging_setup import setup_logging
from drone_collector.session import SessionMissing
from drone_collector.window import parse_date, format_date

EXIT_OK = 0
EXIT_CONFIG = 1
EXIT_SESSION = 2
EXIT_PERIOD = 3
EXIT_PAGINATION = 4
EXIT_NO_DEVICES = 8
EXIT_MISMATCH = 9

# ─── Селекторы панели фильтра (правятся ЗДЕСЬ и больше нигде) ───────────────
#
# Написаны по снимкам кабинета от 2026-08-13: кнопка `Filter` справа сверху,
# панель с заголовками `Task Type`, `Task Mode`, `Device`, `Team/Member`,
# галочкой `Display 0 Field Area Records` и кнопками `Clear` / `OK`.

SELECTOR_FILTER_BUTTON = ("button:has-text('Filter'), "
                          "[class*='filter']:has-text('Filter')")

# Панель Ant Design рисуется в портал на body, поэтому селектор общедокументный
# и с отсечением скрытых копий -- ровно как у контрола размера страницы.
SELECTOR_FILTER_PANEL = (".ant-popover:not(.ant-popover-hidden) "
                         ".ant-popover-inner, "
                         ".ant-dropdown:not(.ant-dropdown-hidden), "
                         ".ant-modal-content")

# Выпадающий список устройства: ближайший ant-select ПОСЛЕ подписи `Device`.
# Ось following в XPath -- то немногое, что не зависит от классов сборки.
XPATH_DEVICE_SELECT = ("xpath=//*[normalize-space(text())='Device']"
                       "/following::*[contains(@class,'ant-select')][1]")

# Опции выпадающего списка -- в портале, как и у размера страницы.
SELECTOR_DEVICE_OPTIONS = (".ant-select-dropdown:not(.ant-select-dropdown-hidden) "
                           ".ant-select-item-option")
SELECTOR_DEVICE_OPTIONS_FALLBACK = '.ant-select-item-option'

# Крестик на выбранном устройстве и кнопки панели.
SELECTOR_DEVICE_CHIP_REMOVE = '.ant-select-selection-item-remove'
SELECTOR_FILTER_OK = "button:has-text('OK')"
SELECTOR_FILTER_CLEAR = "button:has-text('Clear')"

# [REASON]: галочка `Display 0 Field Area Records` должна СТОЯТЬ. Без неё
# сайт прячет вылеты с нулевой площадью, а в нашей базе они есть -- и сверка
# «сумма по устройствам = прогон без фильтра» разошлась бы на них, указав на
# несуществующую ошибку фильтрации.
SELECTOR_ZERO_AREA_CHECKBOX = ("label:has-text('Display 0 Field Area Records') "
                               "input[type='checkbox'], "
                               "input[type='checkbox']")
TEXT_ZERO_AREA = 'Display 0 Field Area Records'

# Сколько символов панели писать в журнал при первом открытии.
PANEL_DUMP_CHARS = 4000

# Пауза после применения фильтра, прежде чем ждать ответ.
FILTER_SETTLE_MS = 1200


class DeviceListUnavailable(BrowserError):
    """Панель фильтра открылась, но список устройств не прочитан."""


class DeviceSweepCollector(FlightCollector):
    """FlightCollector плюс фильтр по устройству.

    Всё, что касается сессии, периода, размера страницы и обхода страниц,
    берётся у родителя без изменений: это код, проверенный живыми прогонами.
    Здесь добавлено ровно одно -- вождение панели фильтра.
    """

    def __init__(self, cfg, logger=None):
        FlightCollector.__init__(self, cfg, logger=logger)
        self._panel_dumped = False

    # -- панель фильтра -------------------------------------------------------

    def open_filter(self):
        """Открыть панель фильтра и вернуть её локатор."""
        button = self._page.locator(SELECTOR_FILTER_BUTTON).first
        button.click(timeout=self.cfg.page_timeout_ms)
        self._page.wait_for_timeout(self.cfg.settle_ms)
        panel = self._page.locator(SELECTOR_FILTER_PANEL).first
        if not self._panel_dumped:
            self._panel_dumped = True
            try:
                html = panel.evaluate('node => node.outerHTML')
            except Exception as exc:
                html = '(panel HTML unavailable: %s)' % exc
            # [REASON]: селекторы этой панели написаны вслепую. Один дамп в
            # журнале первого прогона стоит дешевле, чем ещё один круг
            # «попробуйте, не сработало, попробуйте иначе».
            self.log.info('Filter panel markup (first %d chars): %s',
                          PANEL_DUMP_CHARS, (html or '')[:PANEL_DUMP_CHARS])
        return panel

    def ensure_zero_area_records(self, panel):
        """Галочка `Display 0 Field Area Records` должна стоять."""
        try:
            box = panel.locator("input[type='checkbox']").first
            if not box.is_checked(timeout=5000):
                box.click()
                self.log.info('Checked %r', TEXT_ZERO_AREA)
            else:
                self.log.info('%r is already checked', TEXT_ZERO_AREA)
        except Exception as exc:
            self.log.warning('Could not verify %r (%s); counts may exclude '
                             'zero-area flights', TEXT_ZERO_AREA, exc)

    def _device_select(self, panel):
        return panel.locator(XPATH_DEVICE_SELECT).first

    def read_device_options(self, panel):
        """Имена устройств из выпадающего списка фильтра."""
        select = self._device_select(panel)
        select.click(timeout=self.cfg.page_timeout_ms)
        self._page.wait_for_timeout(self.cfg.settle_ms)
        options = self._page.locator(SELECTOR_DEVICE_OPTIONS)
        count = options.count()
        if not count:
            options = self._page.locator(SELECTOR_DEVICE_OPTIONS_FALLBACK)
            count = options.count()
        names = []
        for index in range(count):
            text = (options.nth(index).inner_text() or '').strip()
            if text and text not in names:
                names.append(text)
        # Закрыть список, не выбирая ничего.
        self._page.keyboard.press('Escape')
        self._page.wait_for_timeout(300)
        if not names:
            raise DeviceListUnavailable(
                'the Device dropdown produced no options -- correct '
                'XPATH_DEVICE_SELECT / SELECTOR_DEVICE_OPTIONS in '
                'drone_collector/devices.py using the panel markup logged above')
        self.log.info('Filter offers %d device(s): %s', len(names),
                      ', '.join(names))
        return names

    def select_device(self, panel, name):
        """Выбрать устройство по точному имени."""
        select = self._device_select(panel)
        select.click(timeout=self.cfg.page_timeout_ms)
        self._page.wait_for_timeout(400)
        option = self._page.locator(
            "%s:has-text(%s)" % (SELECTOR_DEVICE_OPTIONS, json.dumps(name))
        ).first
        option.click(timeout=self.cfg.page_timeout_ms)
        self._page.keyboard.press('Escape')
        self._page.wait_for_timeout(300)

    def clear_device(self, panel):
        """Снять выбранное устройство, оставив период и прочее как есть."""
        try:
            select = self._device_select(panel)
            removes = select.locator(SELECTOR_DEVICE_CHIP_REMOVE)
            for _ in range(removes.count()):
                removes.first.click(timeout=5000)
                self._page.wait_for_timeout(200)
        except Exception as exc:
            self.log.warning('Could not clear the device chip (%s); falling '
                             'back to the Clear button', exc)
            try:
                panel.locator(SELECTOR_FILTER_CLEAR).first.click(timeout=5000)
            except Exception as exc2:
                self.log.warning('Clear button not usable either (%s)', exc2)

    def apply_filter(self, panel):
        panel.locator(SELECTOR_FILTER_OK).first.click(
            timeout=self.cfg.page_timeout_ms)
        self._page.wait_for_timeout(FILTER_SETTLE_MS)

    # -- один борт ------------------------------------------------------------

    def collect_one_device(self, name, expected_from_ms, expected_to_ms):
        """Поставить фильтр на устройство и обойти его страницы."""
        panel = self.open_filter()
        self.ensure_zero_area_records(panel)
        self.clear_device(panel)
        self.select_device(panel, name)

        # [REASON]: захваченное до применения фильтра принадлежит другому
        # набору вылетов. Родитель делает ровно так же при смене периода:
        # смешать их значило бы приписать борту чужие вылеты и не заметить.
        self._captured = []
        self.apply_filter(panel)

        if not self._wait_for_matching_capture(expected_from_ms,
                                               expected_to_ms,
                                               browser_module.FIRST_CAPTURE_TIMEOUT_MS):
            raise BrowserError(
                'no flight list for device %r within %d ms (rejected: %s)'
                % (name, browser_module.FIRST_CAPTURE_TIMEOUT_MS,
                   self.rejected or 'none'))

        complete, total_pages, clicks = self.paginate(expected_from_ms,
                                                      expected_to_ms)
        raw_bodies = [page.body for page in self._captured]
        flights = []
        for page in self._captured:
            flights.extend(page.flights)
        unique, self_duplicates = dedupe_flights(flights)
        self.log.info('Device %r: %d flight(s) over %d page(s), complete=%s',
                      name, len(unique), len(pages_seen(self._captured)),
                      complete)
        return {
            'device': name,
            'flights': unique,
            'raw_bodies': raw_bodies,
            'pages': len(pages_seen(self._captured)),
            'total_pages': total_pages,
            'clicks': clicks,
            'complete': complete,
            'self_duplicates': self_duplicates,
        }


def flight_row(flight, device):
    """Одна строка CSV из сырого вылета. Единицы не пересчитываются, кроме м2."""
    area = flight.get('new_work_area')
    try:
        area_ha = float(area) / 10000.0
    except (TypeError, ValueError):
        area_ha = ''
    return {
        'dji_flight_id': flight.get('id'),
        'device': device,
        'nickname': flight.get('nickname'),
        'started_utc': flight.get('start_timestamp'),
        'area_ha': ('%.4f' % area_ha) if area_ha != '' else '',
    }


def write_outputs(out_dir, per_device, log):
    """CSV, summary.json и сырые тела. Возвращает путь к CSV."""
    raw_dir = os.path.join(out_dir, 'raw')
    os.makedirs(raw_dir, exist_ok=True)

    csv_path = os.path.join(out_dir, 'flights_by_device.csv')
    seen_ids = {}
    duplicates = []
    with open(csv_path, 'w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(
            handle, delimiter=';',
            fieldnames=['dji_flight_id', 'device', 'nickname', 'started_utc',
                        'area_ha'])
        writer.writeheader()
        for item in per_device:
            for flight in item['flights']:
                row = flight_row(flight, item['device'])
                fid = row['dji_flight_id']
                if fid in seen_ids and seen_ids[fid] != item['device']:
                    # [REASON]: один вылет не может принадлежать двум бортам.
                    # Такое означало бы, что фильтр не применился, и молча
                    # проглотить это нельзя -- иначе привязка будет уверенной
                    # и неверной.
                    duplicates.append((fid, seen_ids[fid], item['device']))
                seen_ids[fid] = item['device']
                writer.writerow(row)

    summary = {
        'devices': [
            {
                'device': item['device'],
                'flights': len(item['flights']),
                'pages': item['pages'],
                'total_pages': item['total_pages'],
                'complete': item['complete'],
                'self_duplicates': item['self_duplicates'],
            }
            for item in per_device
        ],
        'flights_total': sum(len(item['flights']) for item in per_device),
        'cross_device_duplicates': [
            {'dji_flight_id': fid, 'first': a, 'second': b}
            for fid, a, b in duplicates
        ],
    }
    with open(os.path.join(out_dir, 'summary.json'), 'w',
              encoding='utf-8') as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=1)

    for item in per_device:
        safe = ''.join(ch if ch.isalnum() or ch in '-_' else '_'
                       for ch in item['device']) or 'device'
        with open(os.path.join(raw_dir, safe + '.json'), 'w',
                  encoding='utf-8') as handle:
            json.dump(item['raw_bodies'], handle, ensure_ascii=False)

    if duplicates:
        log.error('%d flight(s) came back under more than one device -- the '
                  'filter did not apply on at least one pass. See '
                  'summary.json.', len(duplicates))
    log.info('Wrote %s (%d flights) and raw bodies for %d device(s)',
             csv_path, summary['flights_total'], len(per_device))
    return csv_path, summary


def build_parser():
    parser = argparse.ArgumentParser(
        prog='python -m drone_collector.devices',
        description='DRONE-BODYCODE-001: capture flights per DJI device so '
                    'each flight can be tied to the airframe that flew it. '
                    'Reads the site, writes files, sends nothing.')
    parser.add_argument('--from', dest='date_from', required=True,
                        metavar='YYYY-MM-DD')
    parser.add_argument('--to', dest='date_to', required=True,
                        metavar='YYYY-MM-DD')
    parser.add_argument('--out', required=True,
                        help='directory for the CSV, summary and raw bodies')
    parser.add_argument('--device', action='append', default=[],
                        help='sweep only this device (repeatable); default is '
                             'every device the filter offers')
    parser.add_argument('--list-devices', action='store_true',
                        help='print the device list and exit without sweeping')
    return parser


def run(args, cfg, log):
    date_from = parse_date(args.date_from)
    date_to = parse_date(args.date_to)
    os.makedirs(args.out, exist_ok=True)

    with DeviceSweepCollector(cfg, logger=log) as collector:
        collector.open_records()
        expected_from, expected_to = collector.set_period(date_from, date_to)
        collector.maximise_page_size(expected_from, expected_to)

        # Прогон без фильтра -- контрольное число, с которым потом
        # сравнивается сумма по устройствам.
        control_complete, control_total_pages, _clicks = collector.paginate(
            expected_from, expected_to)
        control_raw = []
        for page in collector.captured:
            control_raw.extend(page.flights)
        control_unique, _dupes = dedupe_flights(control_raw)
        log.info('Unfiltered control: %d flight(s) over %d page(s), '
                 'complete=%s', len(control_unique), control_total_pages,
                 control_complete)

        panel = collector.open_filter()
        collector.ensure_zero_area_records(panel)
        devices = collector.read_device_options(panel)
        collector._page.keyboard.press('Escape')
        if args.list_devices:
            print('Devices offered by the filter:')
            for name in devices:
                print('  %s' % name)
            return EXIT_OK

        wanted = args.device or devices
        missing = [name for name in wanted if name not in devices]
        if missing:
            log.warning('Requested device(s) not offered by the filter: %s',
                        ', '.join(missing))

        per_device = []
        incomplete = []
        for name in wanted:
            if name not in devices:
                continue
            item = collector.collect_one_device(name, expected_from,
                                                expected_to)
            per_device.append(item)
            if not item['complete']:
                incomplete.append(name)

    _csv_path, summary = write_outputs(args.out, per_device, log)

    swept = summary['flights_total']
    control = len(control_unique)
    log.info('Control %d flight(s) unfiltered vs %d summed over %d device(s)',
             control, swept, len(per_device))
    if incomplete:
        log.error('Page walk incomplete for: %s', ', '.join(incomplete))
        return EXIT_PAGINATION
    if summary['cross_device_duplicates']:
        return EXIT_MISMATCH
    if args.device:
        log.info('Only %d of %d devices were requested, so the control total '
                 'is not expected to match.', len(per_device), len(devices))
        return EXIT_OK
    if swept != control:
        log.error('MISMATCH: the devices add up to %d flights, the unfiltered '
                  'window has %d. The data is written, but do not trust the '
                  'attribution until this is explained.', swept, control)
        return EXIT_MISMATCH
    log.info('OK: every flight of the window is accounted for by exactly one '
             'device.')
    return EXIT_OK


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(require_ingest=False)
    except ConfigError as exc:
        sys.stderr.write('ERROR: %s\n' % exc)
        return EXIT_CONFIG
    log = setup_logging(cfg.log_dir, name='devices')
    log.info('Device sweep %s .. %s -> %s',
             format_date(parse_date(args.date_from)),
             format_date(parse_date(args.date_to)), args.out)
    try:
        return run(args, cfg, log)
    except SessionMissing as exc:
        log.error('%s', exc)
        return EXIT_SESSION
    except SessionExpired as exc:
        log.error('%s', exc)
        return EXIT_SESSION
    except PeriodVerificationFailed as exc:
        log.error('%s', exc)
        return EXIT_PERIOD
    except DeviceListUnavailable as exc:
        log.error('%s', exc)
        return EXIT_NO_DEVICES
    except BrowserError as exc:
        log.error('%s', exc)
        return EXIT_PAGINATION


if __name__ == '__main__':
    sys.exit(main())
