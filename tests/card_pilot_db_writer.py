# -*- coding: utf-8 -*-
"""Стенд блоков пилота карточек: одна запись в базу площадки.

Подставной Stop-Service (последняя запись службы перед остановкой: её
видит только вторая копия B1) или Start-Service (база изменилась при пуске:
B1 обязан это заметить отпечатком).
"""
import sqlite3
import sys

con = sqlite3.connect(sys.argv[1])
con.execute('UPDATE drone_flights SET area_ha = area_ha + 0.5 '
            'WHERE rowid = (SELECT MIN(rowid) FROM drone_flights)')
con.commit()
con.close()
