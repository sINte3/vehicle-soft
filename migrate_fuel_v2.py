"""
migrate_fuel_v2.py
Создаёт новые таблицы АЗС модуля и заполняет справочник складов/АЗС.
Запускать ОДИН РАЗ на сервере:
    python migrate_fuel_v2.py
"""

import sys
import os

# Adjust path if needed
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app
from models import (
    db, Organization,
    FuelWarehouse, FuelStation2,
    FuelInitialBalance, FuelReceipt2,
    FuelTransaction2, FuelSyncLog2,
)

# ── Данные из Excel: склад → список АЗС ──────────────────────────────
SEED_DATA = [
    {
        'warehouse': 'Заминлари',
        'stations': [
            {'name': 'Мехнат Рохат', 'topaz_id': 812001},
        ],
    },
    {
        'warehouse': 'Чорва',
        'stations': [
            {'name': 'Чорва АЗС', 'topaz_id': 898121},
        ],
    },
    {
        'warehouse': 'Мирзачул ПТЗ',
        'stations': [
            {'name': 'Пахтазор',       'topaz_id': 935491},
            {'name': 'Исузу Бензовоз', 'topaz_id': 812301},
            {'name': 'Жиззах МТП',     'topaz_id': 935521},
        ],
    },
    {
        'warehouse': 'Когон ПТЗ',
        'stations': [
            {'name': 'Каган АЗС',  'topaz_id': 812011},
            {'name': 'К.Бозор ПТМ','topaz_id': 935541},
            {'name': 'Уртачул',    'topaz_id': 935501},
        ],
    },
    {
        'warehouse': 'Гиждувон',
        'stations': [
            {'name': 'Гиждувон ПТЗ',  'topaz_id': 935531},
            {'name': 'Пахтаобод ПТМ', 'topaz_id': 898141},
            {'name': 'Кумушкент ПТМ', 'topaz_id': 898131},
            {'name': 'Рохкент ПТМ',   'topaz_id': 935471},
            {'name': 'Харгуш ПТМ',    'topaz_id': 935511},
        ],
    },
    {
        'warehouse': 'Гарден',
        'stations': [
            {'name': 'Вобкент ПТМ', 'topaz_id': 895101},
        ],
    },
    {
        'warehouse': 'Пешку ПТЗ',
        'stations': [
            {'name': 'Пешку ПТЗ',   'topaz_id': 825261},
            {'name': 'Варохшо ПТМ', 'topaz_id': 898111},
        ],
    },
    {
        'warehouse': 'Пешку Сервис',
        'stations': [
            {'name': 'Пешку МТП', 'topaz_id': 812021},
        ],
    },
    {
        'warehouse': 'Шофиркон ПТЗ',
        'stations': [
            {'name': 'Шофиркон ПТЗ', 'topaz_id': 825271},
            {'name': 'Ш.Рашидов ПТМ','topaz_id': 935551},
        ],
    },
    {
        'warehouse': 'Пахтасаноаттранс',
        'stations': [
            {'name': 'Транс 1', 'topaz_id': 811971},
            {'name': 'Транс 2', 'topaz_id': 825241},
        ],
    },
]


def migrate():
    app = create_app()
    with app.app_context():
        print('Creating new fuel tables...')
        db.create_all()
        print('OK')

        # Проверяем, уже засеяно ли
        if FuelWarehouse.query.count() > 0:
            print('Warehouses already exist (%d). Checking for missing stations...' %
                  FuelWarehouse.query.count())
        else:
            print('Seeding warehouses and stations...')

        # Маппинг названий организаций → id
        org_map = {o.name: o.id for o in Organization.query.all()}
        # Нечёткое совпадение (by partial name)
        org_map_lower = {k.lower(): v for k, v in org_map.items()}

        def find_org_id(wh_name):
            # Try exact match first
            if wh_name in org_map:
                return org_map[wh_name]
            # Try lower case
            low = wh_name.lower()
            if low in org_map_lower:
                return org_map_lower[low]
            # Try partial match
            for org_name, org_id in org_map.items():
                if wh_name.lower() in org_name.lower() or org_name.lower() in wh_name.lower():
                    return org_id
            return None

        wh_created = 0
        st_created = 0
        st_skipped = 0

        for entry in SEED_DATA:
            wh_name = entry['warehouse']

            # Find or create warehouse
            wh = FuelWarehouse.query.filter_by(name=wh_name).first()
            if not wh:
                org_id = find_org_id(wh_name)
                wh = FuelWarehouse(name=wh_name, organization_id=org_id)
                db.session.add(wh)
                db.session.flush()   # get wh.id
                wh_created += 1
                print(f'  + Warehouse: {wh_name} (org_id={org_id})')

            for st_data in entry['stations']:
                existing = FuelStation2.query.filter_by(topaz_id=st_data['topaz_id']).first()
                if existing:
                    # Update warehouse if wrong
                    if existing.warehouse_id != wh.id:
                        print(f'  ~ Fixing station {st_data["name"]} warehouse')
                        existing.warehouse_id = wh.id
                    st_skipped += 1
                else:
                    st = FuelStation2(
                        name=st_data['name'],
                        topaz_id=st_data['topaz_id'],
                        warehouse_id=wh.id,
                        is_active=True,
                    )
                    db.session.add(st)
                    st_created += 1
                    print(f'    + Station: {st_data["name"]} (topaz_id={st_data["topaz_id"]})')

        db.session.commit()

        print()
        print('Migration complete:')
        print(f'  Warehouses created: {wh_created}')
        print(f'  Stations created:   {st_created}')
        print(f'  Stations existing:  {st_skipped}')
        print()
        print('Total warehouses:', FuelWarehouse.query.count())
        print('Total stations:  ', FuelStation2.query.count())
        print()
        print('Next step: set initial balances at /fuel/initial-balance')


if __name__ == '__main__':
    migrate()
