# -*- coding: utf-8 -*-
"""agro-work -- копия боевой базы для проверки импорта до релиза.

ЗАЧЕМ
Прод-деплой закрыт гейтом, пока таблица «Открытые пункты» не пуста, а
площадку занимает другой трек. Проверить импорт agro-work на настоящих
данных до релиза можно только на КОПИИ: отдельная папка с клоном ветки
(`C:\\VehicleSoft_AgroWork`), в её `instance\\transport.db` -- копия боевой
базы, и миграция с импортом работают там. Боевая база при этом не меняется
ни на байт.

КАК
Онлайн-копия SQLite (`Connection.backup`), как у backup_transport_db.py:
согласованный снимок даже при работающей службе и включённом WAL. Источник
открывается `mode=ro` -- записать в него SQLite не даст сам.

ОТКАЗЫ, А НЕ ДОГАДКИ
  * источник и приёмник -- один файл;
  * приёмник лежит в папке `transport-report` (прод или площадка): копия
    для проверки не имеет права оказаться на месте боевой базы;
  * приёмник уже есть -- в нём может лежать то, что наимпортировали раньше;
    заменить его можно только явным `--replace`.

Запуск (PowerShell, одна строка):

  & "C:\\Program Files\\Python314\\python.exe" tools\\agro_work_copy_db.py --from C:\\transport-report\\instance\\transport.db --to C:\\VehicleSoft_AgroWork\\instance\\transport.db

Вывод в консоль -- ASCII.
"""

import argparse
import os
import sqlite3
import sys

FORBIDDEN_FOLDER = 'transport-report'


def _norm(path):
    return os.path.normcase(os.path.abspath(path))


def refuse_reason(source, target, replace):
    """Почему копировать нельзя, или None."""
    if not os.path.isfile(source):
        return 'source database not found: %s' % source
    if _norm(source) == _norm(target):
        return 'source and target are the same file'
    parts = [p.lower() for p in _norm(target).replace('\\', '/').split('/')]
    if any(part.startswith(FORBIDDEN_FOLDER) for part in parts):
        # [REASON]: C:\transport-report -- прод, C:\transport-report-staging
        # -- площадка. Проверочная копия, записанная туда, заменила бы живую
        # базу; такой путь не опечатка, которую стоит исполнить.
        return ('target is inside a %s folder - a check copy must never land '
                'next to a live database' % FORBIDDEN_FOLDER)
    if os.path.exists(target) and not replace:
        return ('target already exists: %s - it may hold an earlier import; '
                'add --replace to overwrite it' % target)
    return None


def copy(source, target):
    """Онлайн-копия. Возвращает (байт в копии, integrity_check копии)."""
    folder = os.path.dirname(os.path.abspath(target))
    os.makedirs(folder, exist_ok=True)
    tmp = target + '.part'
    if os.path.exists(tmp):
        os.remove(tmp)
    src = sqlite3.connect('file:%s?mode=ro' % os.path.abspath(source)
                          .replace('\\', '/'), uri=True, timeout=60)
    try:
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst)
            check = dst.execute('PRAGMA integrity_check').fetchone()[0]
        finally:
            dst.close()
    finally:
        src.close()
    # [REASON]: копия пишется во временный файл и встаёт на место одним
    # переименованием: оборванная копия не выглядит готовой базой.
    os.replace(tmp, target)
    return os.path.getsize(target), check


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--from', dest='source', required=True)
    parser.add_argument('--to', dest='target', required=True)
    parser.add_argument('--replace', action='store_true')
    args = parser.parse_args(argv)
    reason = refuse_reason(args.source, args.target, args.replace)
    if reason:
        sys.stderr.write('ERROR: %s\n' % reason.encode('ascii', 'replace')
                         .decode('ascii'))
        return 2
    size, check = copy(args.source, args.target)
    print('copied %d bytes to %s' % (size, args.target.encode(
        'ascii', 'replace').decode('ascii')))
    print('integrity of the copy: %s' % check)
    return 0 if check == 'ok' else 1


if __name__ == '__main__':
    sys.exit(main())
