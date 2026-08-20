# -*- coding: utf-8 -*-
"""GPS-8 -- ретенция точек: удаление помесячных файлов старше срока.

ЗАЧЕМ ФАЙЛОМ, А НЕ ЗАПРОСОМ
Около миллиона точек в сутки, тридцать миллионов в месяц (решение G3). В базе
приложения ретенция была бы `DELETE` на десятки миллионов строк и `VACUUM`
поверх файла, из которого служба читает. Здесь это `os.remove` и ноль работы.

СУХОЙ ПРОГОН ПО УМОЛЧАНИЮ. Скрипт ничего не удаляет, пока человек не напишет
`--apply` руками. Устав запрещает удалять продовые данные автоматически —
никогда, ни одной операцией, — и это правило сильнее удобства: расписания у
этого скрипта нет и не должно быть.

ЧТО ОН ТРОГАЕТ И ЧЕГО НЕ ТРОГАЕТ
Только файлы вида `gps_points_YYYYMM.db` и их спутники `-wal`/`-shm`. Ни
`transport.db`, ни `gps_collector_state.db` (там watermark, он обязан пережить
удаление месяца), ни файл с похожим именем, который завёл кто-то другой.

ТЕКУЩИЙ МЕСЯЦ НЕ УДАЛЯЕТСЯ НИКОГДА, какой бы срок ни назвали: в него прямо
сейчас пишет коллектор.

Запуск (PowerShell, по одной команде на строку):

  cd C:\\transport-report

  & "C:\\Program Files\\Python314\\python.exe" tools\\gps_retention.py

  & "C:\\Program Files\\Python314\\python.exe" tools\\gps_retention.py --apply

Первая команда только показывает, что будет удалено. Вывод в консоль — ASCII.
"""

import argparse
import os
import re
import sys

from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gps_collector import config, storage                          # noqa: E402

# [REASON]: имя разбирается СТРОГО. Поиск подстрокой "gps_points" зацепил бы
# и `gps_points_backup.db`, и чью-нибудь выгрузку с похожим именем -- а
# удаление здесь необратимо.
NAME = re.compile(r'^gps_points_(\d{4})(\d{2})\.db$')
SIDECARS = ('-wal', '-shm')

# Решение G3. Срок хранения точек; агрегаты и полигоны живут в transport.db
# вечно и от этого числа не зависят.
RETENTION_DAYS = 90


def month_end(year, month):
    """Последний день месяца. Считается арифметикой по годам и месяцам.

    [REASON]: не "первое число плюс 30 дней". Такой счёт промахивается на
    феврале и на 31-дневных месяцах, и промах здесь стоит удалённого месяца
    точек.
    """
    if month == 12:
        return date(year + 1, 1, 1)
    return date(year, month + 1, 1)


def plan(folder, days=RETENTION_DAYS, today=None):
    """Что удалить и что оставить. Возвращает (к_удалению, оставить).

    Каждый элемент -- (месяц, путь, байт). Месяц удаляется, только когда
    ВЕСЬ он старше срока: файл за июль сносится не раньше, чем истечёт срок
    для 31 июля.
    """
    today = today or date.today()
    current = today.strftime('%Y%m')
    doomed, kept = [], []
    for name in sorted(os.listdir(folder) if os.path.isdir(folder) else []):
        match = NAME.match(name)
        if not match:
            continue
        year, month = int(match.group(1)), int(match.group(2))
        path = os.path.join(folder, name)
        entry = ('%04d%02d' % (year, month), path, os.path.getsize(path))
        # [REASON]: в текущий месяц прямо сейчас пишет коллектор. Никакой
        # срок не должен позволить его удалить -- ошибка в одном аргументе
        # стоила бы суток истории.
        if entry[0] >= current:
            kept.append(entry)
            continue
        age = (today - month_end(year, month)).days
        (doomed if age >= days else kept).append(entry)
    return doomed, kept


def remove(path, log=print):
    """Удалить файл и его спутников WAL. Возвращает число удалённых файлов."""
    removed = 0
    for suffix in ('',) + SIDECARS:
        target = path + suffix
        if os.path.exists(target):
            os.remove(target)
            removed += 1
    log('  udalen %s (faylov: %d)' % (os.path.basename(path), removed))
    return removed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--dir', default=None,
                        help='where the point files live (default: instance/)')
    parser.add_argument('--days', type=int, default=RETENTION_DAYS,
                        help='keep months younger than this many days (90)')
    parser.add_argument('--apply', action='store_true',
                        help='actually delete; without it nothing is removed')
    args = parser.parse_args(argv)

    folder = args.dir or config.points_dir()
    if not os.path.isdir(folder):
        sys.stderr.write('ERROR: folder not found: %s\n' % folder)
        return 2
    if args.days < 1:
        # [REASON]: --days 0 означало бы "снести всё, кроме текущего месяца".
        # Такого намерения у ретенции нет, а опечатка в один символ есть.
        sys.stderr.write('ERROR: --days must be at least 1\n')
        return 2

    doomed, kept = plan(folder, days=args.days)
    print('folder   : %s' % folder)
    print('retention: %d days' % args.days)
    print('keep     : %d file(s), %.1f MB'
          % (len(kept), sum(size for _, _, size in kept) / 1048576.0))
    for month, path, size in kept:
        print('  %s  %8.1f MB  %s' % (month, size / 1048576.0,
                                      os.path.basename(path)))
    print('delete   : %d file(s), %.1f MB'
          % (len(doomed), sum(size for _, _, size in doomed) / 1048576.0))
    for month, path, size in doomed:
        print('  %s  %8.1f MB  %s' % (month, size / 1048576.0,
                                      os.path.basename(path)))

    if not doomed:
        print('\nnothing to delete')
        return 0
    if not args.apply:
        print('\ndry run: nothing was deleted. Add --apply to really delete.')
        return 0

    # Watermark лежит отдельно и переживает удаление месяца по построению;
    # проверяем это прямо здесь, а не полагаемся на память.
    state = storage.state_path(folder)
    if os.path.exists(state) and any(
            os.path.abspath(path) == os.path.abspath(state)
            for _, path, _ in doomed):
        sys.stderr.write('ERROR: refusing to delete the watermark file\n')
        return 1

    total = 0
    for _, path, _ in doomed:
        total += remove(path)
    print('\ndeleted %d file(s) for %d month(s)' % (total, len(doomed)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
