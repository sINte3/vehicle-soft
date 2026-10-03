# -*- coding: utf-8 -*-
"""Стенд блоков пилота карточек: процесс, который держит базу площадки.

Запускается подставным Stop-Service службы площадки
(tests/card_pilot_blocks_harness.ps1): база остаётся занятой после остановки,
и блок B1 обязан встать на check_db_lock до второй копии и до замены.
"""
import sqlite3
import sys
import time

con = sqlite3.connect(sys.argv[1])
con.execute('PRAGMA locking_mode=EXCLUSIVE')
con.execute('BEGIN EXCLUSIVE')
open(sys.argv[2], 'w').write('held')
time.sleep(600)
