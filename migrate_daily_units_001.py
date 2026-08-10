# -*- coding: utf-8 -*-
"""Migration DAILY_UNITS_001 -- upravlyaemyy spravochnik edinic dnevnogo vvoda.

Owner decision (2026-08-09, docs/ux/30-design-decisions.md, DD-004): edinica
izmereniya v dnevnom vvode perestaet byt svobodnoy strokoy i stanovitsya
vyborom iz upravlyaemogo dvuyazychnogo spravochnika. Nakoplennye znacheniya
NE perepisyvayutsya -- ryadom poyavlyaetsya privedennoe znachenie, po kotoromu
schitayut otchety.

Pochemu eto nuzhno. V daily_records.unit nakopilos 15 napisaniy primerno
shesti edinic (ga/Ga, soat/Soat, m-soat/M-soat/Motosoat, Reys/reys/Reyys/
1 Reys, tn/km + tonna/km), plyus odna zapis dlinoy 253 simvola, kuda vpisan
abzac instrukcii. SQLite schitaet 'ga' i 'Ga' raznymi klyuchami, poetomu
lyuboy otchet s gruppirovkoy po edinice raznosit ikh po raznym strokam.

Sozdaet:
  - tablicu daily_record_units (code / name_ru / name_uz / is_active /
    sort_order / created_at) s UNIQUE-indeksom po code -- forma povtoryaet
    spare_part_units;
  - shest bazovyh strok spravochnika (INSERT OR IGNORE po unikalnomu code);
  - kolonku daily_records.unit_code -- PRIVEDENNOE znachenie.

Zapolnyaet unit_code po karte sootvetstviy. Znachenie, kotorogo v karte net,
poluchaet NULL: eto sostoyanie "ne raspoznano", i ono soznatelno ne
avtoregistriruetsya kak novaya edinica.

[REASON]: migraciya spare_part_units avtoregistriruet neznakomye legaci-
znacheniya, chtoby istoricheskoe ne stalo nevybiraemym. Zdes tak delat
NELZYA: sredi znacheniy est abzac teksta na 253 simvola, i avtoregistraciya
zavela by ego v spravochnik kak edinicu izmereniya. Poetomu sopostavlenie
idet po TOCHNOMU sovpadeniyu posle normalizacii i tolko dlya strok koroche
MAX_UNIT_LEN -- prefiksnoe sravnenie sopostavilo by tot samyy abzac, on
nachinaetsya so slov "m-soat budem schitat s...".

NE menyaet:
  - ni odnogo znacheniya v daily_records.unit -- istoricheskie snimki;
  - ni odnoy drugoy tablicy.

Bezopasna i idempotentna:
  - CREATE TABLE / INDEX IF NOT EXISTS, INSERT OR IGNORE;
  - PRAGMA-guard pered ALTER: v SQLite net ADD COLUMN IF NOT EXISTS;
  - odna tranzakciya; postusloviya proveryayutsya DO zapisi v reestr;
  - registriruetsya v schema_migrations i propuskaetsya pri povtore.

Zapusk (sluzhba dolzhna byt OSTANOVLENA -- SQLite ne terpit dvuh pisateley):

  cd C:\\transport-report
  .\\nssm.exe stop TransportReport
  & "C:\\Program Files\\Python314\\python.exe" migrate_daily_units_001.py
  .\\nssm.exe start TransportReport

Otkat razdelno, koda i dannyh:

  KOD   -- git revert kommita s etim faylom i s pravkoy models.py.
           Kolonka i tablica ostanutsya v baze; prilozheniyu oni ne meshayut,
           potomu chto nikto k nim ne obrashchaetsya.
  DANNYE -- otkatyvat nechego: ni odna sushchestvuyushchaya kolonka ne
           izmenena. Chtoby ubrat sledy polnostyu:
             DELETE FROM schema_migrations WHERE name = 'DAILY_UNITS_001';
             DROP TABLE daily_record_units;
             UPDATE daily_records SET unit_code = NULL;
           Kolonku unit_code SQLite udalyaet tolko cherez perestroyku
           tablicy; ostavit ee pustoy bezopasno i deshevle.

Kody vyhoda: 0 -- primeneno ili uzhe bylo primeneno; 1 -- predusloviye ne
vypolneno, tranzakciya otkachena, v reestre pusto; 2 -- baza ne naydena.
"""

import os
import sqlite3
import sys

import migration_utils

MIGRATION_ID = 'DAILY_UNITS_001'
DESCRIPTION = ('Managed bilingual unit directory for daily entry: '
               'daily_record_units + daily_records.unit_code (normalised '
               'value). Historical daily_records.unit is never rewritten.')

# (code, name_ru, name_uz, sort_order)
# Uzbekskie podpisi podtverzhdeny vladelcem 2026-08-09.
BASELINE_UNITS = [
    ('ga',     'га',      'га',       10),
    ('soat',   'час',     'соат',     20),
    ('m_soat', 'моточас', 'мотосоат', 30),
    ('reys',   'рейс',    'рейс',     40),
    ('tn_km',  'тн/км',   'тн/км',    50),
    ('km',     'км',      'км',       60),
]

# [REASON]: sopostavlenie TOCHNOE, po normalizovannoy stroke celikom.
# Prefiksnoe sravnenie sopostavilo by zapis na 253 simvola, nachinayushchuyusya
# so slov "m-soat budem schitat s 08:00-18:00", s edinicey m_soat.
ALIASES = {
    'га': 'ga', 'гектар': 'ga', 'ga': 'ga',
    'соат': 'soat', 'час': 'soat', 'часов': 'soat', 'soat': 'soat',
    'м-соат': 'm_soat', 'мотосоат': 'm_soat', 'моточас': 'm_soat',
    'м/соат': 'm_soat', 'm-soat': 'm_soat', 'm_soat': 'm_soat',
    'рейс': 'reys', 'реййс': 'reys', '1 рейс': 'reys', 'reys': 'reys',
    'тн/км': 'tn_km', 'тонна/км': 'tn_km', 'тонно-километр': 'tn_km',
    'tn/km': 'tn_km', 'tn_km': 'tn_km',
    'км': 'km', 'километр': 'km', 'km': 'km',
}

# Stroka dlinnee etogo edinicey izmereniya byt ne mozhet: samaya dlinnaya
# nastoyashchaya -- 'тонно-километр', 14 simvolov.
MAX_UNIT_LEN = 30

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS daily_record_units (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    code       TEXT    NOT NULL,
    name_ru    TEXT    NOT NULL,
    name_uz    TEXT    NOT NULL,
    is_active  INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 100,
    created_at TEXT
)
"""
CREATE_INDEX_SQL = ("CREATE UNIQUE INDEX IF NOT EXISTS "
                    "uq_daily_record_units_code ON daily_record_units(code)")


def normalize(value):
    """Privesti znachenie k vidu, v kotorom ego ishchut v karte."""
    if value is None:
        return ''
    text = ' '.join(str(value).split()).strip().lower()
    return text if len(text) <= MAX_UNIT_LEN else ''


def column_exists(con, table, column):
    """[REASON]: v SQLite net ADD COLUMN IF NOT EXISTS -- proveryaem sami."""
    cur = con.execute('PRAGMA table_info("%s")' % table)
    return any(row[1] == column for row in cur.fetchall())


def table_exists(con, table):
    cur = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cur.fetchone() is not None


def main():
    db_path = migration_utils.DB_PATH
    if not os.path.exists(db_path):
        print('ERROR: database not found at %s' %
              db_path.encode('ascii', 'backslashreplace').decode())
        print('Run migrations from C:\\transport-report with instance\\transport.db present.')
        return 2

    migration_utils.ensure_schema_migrations_table()
    if migration_utils.is_migration_applied(MIGRATION_ID):
        print('Migration %s already applied. Skipping.' % MIGRATION_ID)
        return 0

    con = sqlite3.connect(db_path)
    try:
        con.isolation_level = None
        con.execute('BEGIN')

        if not table_exists(con, 'daily_records'):
            con.execute('ROLLBACK')
            print('ERROR: precondition failed -- table daily_records is missing.')
            print('Nothing was changed and nothing was recorded.')
            return 1

        con.execute(CREATE_TABLE_SQL)
        con.execute(CREATE_INDEX_SQL)

        for code, name_ru, name_uz, order in BASELINE_UNITS:
            con.execute(
                'INSERT OR IGNORE INTO daily_record_units '
                '(code, name_ru, name_uz, is_active, sort_order, created_at) '
                "VALUES (?, ?, ?, 1, ?, datetime('now'))",
                (code, name_ru, name_uz, order))

        if not column_exists(con, 'daily_records', 'unit_code'):
            con.execute('ALTER TABLE daily_records ADD COLUMN unit_code TEXT')

        # Zapolnenie privedennogo znacheniya. Kazhdoe razlichnoe ishodnoe
        # znachenie obrabatyvaetsya odin raz, a ne po stroke.
        cur = con.execute(
            'SELECT DISTINCT unit FROM daily_records '
            "WHERE unit IS NOT NULL AND unit <> ''")
        distinct = [row[0] for row in cur.fetchall()]

        mapped, unmapped = {}, []
        for raw in distinct:
            code = ALIASES.get(normalize(raw))
            if code:
                mapped.setdefault(code, []).append(raw)
            else:
                unmapped.append(raw)

        updated = 0
        for code, raws in sorted(mapped.items()):
            placeholders = ','.join('?' for _ in raws)
            cur = con.execute(
                'UPDATE daily_records SET unit_code = ? '
                'WHERE unit IN (%s)' % placeholders, [code] + raws)
            updated += cur.rowcount

        # --- POSTUSLOVIYA. Proveryayutsya DO zapisi v reestr. ---
        problems = []

        cur = con.execute('SELECT COUNT(*) FROM daily_record_units')
        seeded = cur.fetchone()[0]
        if seeded < len(BASELINE_UNITS):
            problems.append('directory holds %d rows, expected at least %d'
                            % (seeded, len(BASELINE_UNITS)))

        if not column_exists(con, 'daily_records', 'unit_code'):
            problems.append('column daily_records.unit_code was not created')

        # Ni odno ishodnoe znachenie ne moglo izmenitsya.
        cur = con.execute(
            "SELECT COUNT(*) FROM daily_records WHERE unit_code IS NOT NULL "
            "AND (unit IS NULL OR unit = '')")
        if cur.fetchone()[0]:
            problems.append('rows carry a normalised code with no source value')

        # Kazhdyy zapisannyy kod obyazan sushchestvovat v spravochnike.
        cur = con.execute(
            'SELECT COUNT(*) FROM daily_records d WHERE d.unit_code IS NOT NULL '
            'AND d.unit_code NOT IN (SELECT code FROM daily_record_units)')
        if cur.fetchone()[0]:
            problems.append('some normalised codes are not present in the directory')

        if problems:
            con.execute('ROLLBACK')
            print('ERROR: postcondition failed, transaction rolled back:')
            for line in problems:
                print('  - ' + line)
            print('Nothing was recorded in schema_migrations.')
            return 1

        con.execute('COMMIT')
    finally:
        con.close()

    checksum = migration_utils.migration_checksum(os.path.abspath(__file__))
    migration_utils.record_migration(MIGRATION_ID, DESCRIPTION, checksum)

    print('Migration %s applied successfully.' % MIGRATION_ID)
    print('  directory rows : %d' % seeded)
    print('  distinct source values seen : %d' % len(distinct))
    print('  rows given a normalised code: %d' % updated)
    if unmapped:
        print('  UNRECOGNISED source values  : %d (left without a code, by design)'
              % len(unmapped))
        for raw in unmapped:
            shown = str(raw)[:60].encode('ascii', 'backslashreplace').decode()
            print('    - %s%s' % (shown, '...' if len(str(raw)) > 60 else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())
