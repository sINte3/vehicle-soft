#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/ux/seed_ux_fixtures.py -- napolnenie ODNORAZOVOY bazy dannymi
realistichnoy FORMY dlya vizualnogo audita.

Chto eto i chem ne yavlyaetsya
-----------------------------
Eto generator fikstur dlya SNIMKOV EKRANA. On napolnyaet bazu strokami toy zhe
DLINY i togo zhe nabora znacheniy, chto na boevoy baze -- potomu chto shirina
kolonki opredelyaetsya dlinoy stroki, a korotkie vydumannye podpisi pryachut
rovno te defekty, kotorye ishchet audit. Dliny i slovari beryutsya iz
docs/ux/10-metrics/ux_metrics.json (tolko schetchiki i dliny, bez znacheniy
poley).

On NE sozdaet delovo korrektnyh dannyh. Ostatki zapchastey pishutsya napryamuyu,
minuya _apply_inventory_movement; summy i daty ne soglasovany mezhdu soboy.
Dlya renderinga etogo dostatochno, dlya cheloveka -- net.

Poetomu deystvuet ZHESTKAYA GVARDIYA: skript otkazyvaetsya rabotat, esli fayl
bazy lezhit ne vo vremennom kataloge. Napravit ego na instance/transport.db,
na staging ili na production nevozmozhno -- ne po dogovorennosti, a po kodu.

Zapusk otdelno ne trebuetsya: ego vyzyvaet tools/ux/serve_ephemeral.py.
"""

import datetime
import json
import os
import random
import sys
import zlib
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
METRICS = os.path.join(REPO_ROOT, 'docs', 'ux', '10-metrics', 'ux_metrics.json')

# [REASON]: fiksirovannoe zerno -- povtornyy zapusk daet te zhe fiksтury, inache
# skrinshoty rashodyatsya mezhdu progonami i vizualnyy diff stanovitsya shumom.
SEED = 20260809

# [REASON]: "segodnya" beretsya odin raz na zapusk. Fikstury privyazany k nemu,
# poetomu skrinshoty odnogo dnya sravnimy mezhdu soboy; sravnenie mezhdu raznymi
# dnyami trebuet peresnyatiya bazisa -- eto zafiksirovano v specifikacii obhoda.
TODAY = datetime.date.today()

# Slova dlya nabora strok. Russkiy i uzbekskaya KIRILLICA -- latinica v
# interfeysnyh strokah proektom zapreshchena, krome nazvaniy brendov.
RU_WORDS = ['Трактор', 'Комбайн', 'Прицеп', 'Сеялка', 'Культиватор', 'Плуг',
            'Опрыскиватель', 'Погрузчик', 'Самосвал', 'Борона', 'Косилка',
            'Разбрасыватель', 'Каток', 'Жатка', 'Экскаватор']
UZ_WORDS = ['Трактор', 'Комбайн', 'Тиркама', 'Сеялка', 'Культиватор', 'Плуг',
            'Пуркагич', 'Юклагич', 'Ағдаргич', 'Борона', 'Ўроқ',
            'Сочгич', 'Ғaltak', 'Жатка', 'Экскаватор']
ORG_WORDS = ['Бухоро', 'Агрокластер', 'Ғиждувон', 'Когон', 'Вобкент',
             'Пешку', 'Ромитан', 'Шофиркон', 'Жондор', 'Қоракўл']
BRANDS = ['Bosch', 'МТЗ-82.1', 'Amazone', 'Lemken', 'Case IH', 'John Deere',
          'DJI Agras T40', 'Kverneland']

_metrics_cache = None


def metrics():
    global _metrics_cache
    if _metrics_cache is None:
        if os.path.isfile(METRICS):
            with open(METRICS, encoding='utf-8') as handle:
                _metrics_cache = json.load(handle)
        else:
            _metrics_cache = {'text_lengths': {}, 'enums': {}, 'volumes': {}}
    return _metrics_cache


def guard_disposable(db_path):
    """Otkazatsya rabotat vezde, krome vremennogo kataloga.

    [REASON]: sider pishet napryamuyu, minuya invarianty (ostatok zapchastey
    vedetsya tolko cherez _apply_inventory_movement). Zapusk na lyuboy realnoy
    baze isportil by ee tiho. Proverka po real-puti, chtoby simlink ne obhodil.
    """
    real = os.path.realpath(db_path)
    tmp = os.path.realpath(tempfile.gettempdir())
    if not real.startswith(tmp + os.sep):
        print('REFUSING: database is not inside the temp directory.')
        print('  database : %s' % real.encode('ascii', 'backslashreplace').decode())
        print('  temp dir : %s' % tmp)
        print('This seeder bypasses business invariants and may only ever')
        print('touch a disposable database. Nothing was written.')
        sys.exit(2)


def _target_len(table, column, default=18):
    """Rabochaya dlina stroki: p90 s boevoy bazy, inache default."""
    stat = metrics().get('text_lengths', {}).get(table, {}).get(column)
    if isinstance(stat, dict) and stat.get('p90'):
        return int(stat['p90'])
    return default


def _long_len(table, column):
    """Dlina 'khudshego sluchaya': max s boevoy bazy. Imenno on lomaet kolonki."""
    stat = metrics().get('text_lengths', {}).get(table, {}).get(column)
    if isinstance(stat, dict) and stat.get('max'):
        return int(stat['max'])
    return 0


def _enum_values(table, column):
    vals = metrics().get('enums', {}).get(table, {}).get(column)
    return sorted(vals) if isinstance(vals, dict) and vals else None


def make_text(rng, length, uz=False, seed_words=None):
    """Sobrat stroku zadannoy dliny iz osmyslennyh slov, ne 'lorem ipsum'."""
    words = list(seed_words or (UZ_WORDS if uz else RU_WORDS))
    rng.shuffle(words)
    out = []
    total = 0
    i = 0
    while total < length:
        word = words[i % len(words)]
        i += 1
        out.append(word)
        total = len(' '.join(out))
        if i > 40:
            break
    text = ' '.join(out)
    if len(text) > length and length > 3:
        text = text[:length].rstrip()
    return text or words[0]


def value_for(rng, table, col, index):
    """Znachenie dlya kolonki: po tipu, imeni i metrikam boevoy bazy."""
    import sqlalchemy as sa

    name = col.name
    ctype = col.type

    if isinstance(ctype, sa.Boolean):
        return True if name.startswith('is_') else bool(index % 2)
    if isinstance(ctype, (sa.Integer, sa.BigInteger, sa.SmallInteger)):
        return index + 1
    if isinstance(ctype, (sa.Float, sa.Numeric)):
        # [REASON]: velichiny poryadka 15 878.64 -- imenno na perenose takoy
        # summy vnutri kartochki proekt uzhe lovil defekt (DRONE-UI-CARD-WRAP).
        return round(rng.uniform(0.5, 15878.64), 2)
    # [REASON]: daty raspolagayutsya OT segodnya nazad, a ne v fiksirovannom
    # proshlom. Ekrany po umolchaniyu pokazyvayut tekushchiy den, i fikstury v
    # proshlom davali by pustoy dashbord -- to est artefakt sidera vyglyadel by
    # kak pustoe sostoyanie produkta i portil by vyvody audita.
    if isinstance(ctype, sa.Date):
        return TODAY - datetime.timedelta(days=index % 30)
    if isinstance(ctype, sa.DateTime):
        return (datetime.datetime.combine(TODAY, datetime.time(8, 0))
                - datetime.timedelta(days=index % 30, hours=index % 9))

    enum_vals = _enum_values(table, name)
    if enum_vals:
        return enum_vals[index % len(enum_vals)]

    if 'password' in name or 'hash' in name:
        return 'x' * 32
    if name.endswith('_json'):
        return '{}'
    if name in ('lang', 'language'):
        return ['uz', 'ru'][index % 2]
    if 'plate' in name:
        return '01%s%03d%s%s' % (chr(65 + index % 26), index % 1000,
                                chr(65 + index % 26), chr(65 + (index + 3) % 26))

    uz = name.endswith('_uz')
    words = ORG_WORDS if ('org' in table or 'organiz' in name) else None
    if 'brand' in name or 'model' in name or 'article' in name:
        return BRANDS[index % len(BRANDS)]

    # Kazhdaya desyataya stroka -- khudshiy sluchay dliny s boevoy bazy.
    # Imenno on pokazyvaet, gde kolonka rassypaetsya.
    hard = _long_len(table, name)
    length = hard if (hard and index % 10 == 7) else _target_len(table, name)
    return make_text(rng, length, uz=uz, seed_words=words)


def seed_daily_units(db):
    """Postavit spravochnik edinic dnevnogo vvoda v ego NASTOYASHCHIY vid.

    [REASON]: obshchiy generator zapolnyaet lyubuyu tablicu strokami nuzhnoy
    DLINY, i dlya daily_record_units eto dalo by shest-chetyrnadcat vydumannyh
    'edinic' vrode 'ташкилот бригада'. Poyavivsheesya v P4 pole vybora edinicy
    otrisovalo by imenno ih, i kanonicheskiy ekran snimalsya by s musorom v
    samom pole, radi kotorogo shag delalsya. Spravochnik malenkiy i zakrytyy --
    ego stavim yavno, tem zhe naborom, chto migraciya DAILY_UNITS_001.

    Poputno soglasuem daily_records: unit i unit_code dolzhny byt paroy, inache
    ekran pokazhet kod bez podpisi. Odna stroka OSTAVLENA nerapoznannoy -- eto
    zakonnoe sostoyanie po DD-004, i ono dolzhno popast v bazis kadrov, a ne
    prityatsya.
    """
    from models import DailyRecord

    rows = [('ga', 'га', 'га', 10), ('soat', 'час', 'соат', 20),
            ('m_soat', 'моточас', 'мотосоат', 30), ('reys', 'рейс', 'рейс', 40),
            ('tn_km', 'тн/км', 'тн/км', 50), ('km', 'км', 'км', 60)]

    # [REASON]: tot zhe fayl fikstur zapuskaetsya i protiv STAROGO dereva --
    # kadry "bylo" dlya gate P4 snimayutsya s koda do treka, i dannye na oboih
    # storonah obyazany sovpadat, inache raznica fikstur vyglyadela by kak
    # ulучshenie interfeysa. Do DAILY_UNITS_001 ni modeli, ni kolonki net;
    # togda stavim tolko daily_records.unit i vyhodim.
    try:
        from models import DailyRecordUnit
    except ImportError:
        DailyRecordUnit = None

    if DailyRecordUnit is not None:
        DailyRecordUnit.query.delete()
        for code, name_ru, name_uz, order in rows:
            db.session.add(DailyRecordUnit(code=code, name_ru=name_ru, name_uz=name_uz,
                                           is_active=True, sort_order=order))
        db.session.commit()
    has_code = hasattr(DailyRecord, 'unit_code')

    # Ta samaya zapis iz PR-050: v pole edinicy vpisan abzac instrukcii.
    paragraph = ('м-соат будем считать с 08:00-18:00 если техника работала '
                 'весь день то ставим 10 моточасов а если меньше то пишем '
                 'сколько фактически отработала по путевому листу')
    records = DailyRecord.query.order_by(DailyRecord.id).all()
    for i, rec in enumerate(records):
        if i % 7 == 6:
            unit, code = paragraph, None
        elif rec.status == 'idle':
            unit, code = '', None
        else:
            code, unit = rows[i % len(rows)][0], rows[i % len(rows)][1]
        rec.unit = unit
        if has_code:
            rec.unit_code = code
    db.session.commit()
    return len(rows) if DailyRecordUnit is not None else 0


def seed_closed_set_statuses(db):
    """Postavit statusam znacheniya iz ih NASTOYASHCHIH naborov.

    [REASON]: eti kolonki -- svobodnyy VARCHAR, no prilozhenie obrashchaetsya
    k nim kak k klyuchu slovarya: status_labels[req.status][lang]. Obshchiy
    generator kladet tuda stroku nuzhnoy DLINY ('Каток Опр'), i ekran
    spare_part_detail padaet s 500 -- 'dict object has no attribute'.
    Proverено: do etoy pravki marshrut /spare-parts/<id> otdaval 500 na
    OBOIH ekzemplyarah, i v bazis kadrov P1 on prosto ne popal. Bez etogo
    kanonicheskiy ekran nevozmozhno ni snyat, ni proverit.

    Enum-kolonki obshchiy generator uzhe zapolnyaet pravilno (_enum_values);
    zdes tolko te nabory, kotorye zhivut v slovaryah Python, a ne v skheme.
    """
    from models import SparePartRequest, SparePartRequestItem, SparePartStatusHistory

    request_statuses = ['draft', 'submitted', 'returned_for_revision',
                        'approved', 'rejected', 'cancelled', 'issued']
    price_statuses = ['pending', 'confirmed', 'rejected', 'returned']

    touched = 0
    for i, req in enumerate(SparePartRequest.query.order_by(SparePartRequest.id).all()):
        req.status = request_statuses[i % len(request_statuses)]
        touched += 1
    for i, item in enumerate(SparePartRequestItem.query.order_by(SparePartRequestItem.id).all()):
        item.price_status = price_statuses[i % len(price_statuses)]
    # Zhurnal statusov chitaetsya toy zhe kartoy podpisey na ekrane zayavki.
    for i, ev in enumerate(SparePartStatusHistory.query.order_by(SparePartStatusHistory.id).all()):
        ev.old_status = request_statuses[i % len(request_statuses)]
        ev.new_status = request_statuses[(i + 1) % len(request_statuses)]
    db.session.commit()
    return touched


def concentrate_equipment(db, target_orgs=2):
    """Sobrat tehniku v neskolko organizaciy vmesto ravnomernoy razmazki.

    [REASON]: obshchiy generator razdaet vneshnie klyuchi po krugu, i 24
    edinicy tehniki lozhatsya po odnoy na 22 organizacii. Ekran dnevnogo vvoda
    pri etom snimaetsya s ODNOY kartochkoy, hotya po metrikam boevoy bazy on
    neset 83 % vsey raboty cheloveka i pokazyvaet desyatki mashin srazu.
    Kadr s odnoy kartochkoy ne pokazyvaet ni plotnosti, ni gruppirovki po
    kategoriyam, ni togo, kak vedet sebya lipkaya polka sohraneniya pri
    dlinnom spiske -- to est ne pokazyvaet imenno togo, radi chego ekran
    vzyat etalonnym.

    Kategorii tozhe raskladyvayutsya po realnomu naboru: bez etogo vse mashiny
    popadayut v odnu gruppu i razdel po kategoriyam ne viden.
    """
    from models import CATEGORIES, Equipment, Organization

    org_ids = [o.id for o in Organization.query.order_by(Organization.id).limit(target_orgs).all()]
    if not org_ids:
        return 0
    cat_codes = list(CATEGORIES.keys())
    items = Equipment.query.order_by(Equipment.id).all()
    for i, eq in enumerate(items):
        # Pervaya organizaciya poluchaet bolshinstvo: nuzhen odin PLOTNYY
        # ekran, a ne dva srednih.
        eq.organization_id = org_ids[1 % len(org_ids)] if i % 4 == 3 else org_ids[0]
        eq.category = cat_codes[i % len(cat_codes)]
        eq.is_active = True
    db.session.commit()
    return len(items)


def ordered_models(db):
    """Modeli v poryadke zavisimostey: roditel ranshe rebenka."""
    models = [m for m in db.Model.registry.mappers]
    by_table = {m.local_table.name: m for m in models if m.local_table is not None}
    done, order = set(), []

    def visit(mapper, stack):
        table = mapper.local_table
        if table is None or table.name in done or table.name in stack:
            return
        stack = stack | {table.name}
        for fk in table.foreign_keys:
            parent = by_table.get(fk.column.table.name)
            if parent is not None and parent is not mapper:
                visit(parent, stack)
        done.add(table.name)
        order.append(mapper)

    for mapper in sorted(models, key=lambda m: m.local_table.name if m.local_table is not None else ''):
        visit(mapper, frozenset())
    return order


def seed(app, db, rows_per_table=14, verbose=True):
    """Napolnit bazu. Vozvrashchaet slovar {tablica: skolko strok sozdano}."""
    import sqlalchemy as sa
    from models import User, ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER, ROLE_MECHANIC

    created = {}

    with app.app_context():
        db_path = db.engine.url.database
        guard_disposable(db_path)

        pk_pool = {}

        for mapper in ordered_models(db):
            table = mapper.local_table
            model = mapper.class_
            if table is None or table.name == 'users':
                continue

            # [REASON]: zerno beretsya OT IMENI TABLICY, a ne odno na ves
            # progon. Obshchiy rng, protyanutyy cherez vse tablicy, delaet
            # znacheniya kazhdoy tablicy zavisimymi ot TOGO, SKOLKO tablic
            # bylo do nee: derevo bez odnoy modeli (naprimer bez
            # daily_record_units, kotoroy do DAILY_UNITS_001 ne
            # sushchestvovalo) sdvigaet vse posleduyushchie vyborki, i dva
            # ekzemplyara napolnyayutsya RAZNYMI dannymi pri odinakovom SEED.
            # Na sravnenii "bylo / stalo" eto chitaetsya kak izmenenie
            # interfeysa: imenno tak i vyshlo v pervom progone P4.
            # crc32, a ne vstroennyy hash(): hash strok randomiziruetsya
            # mezhdu zapuskami Python, i determinizm byl by mnimym.
            rng = random.Random(SEED ^ zlib.crc32(table.name.encode('utf-8')))

            made = 0
            for i in range(rows_per_table):
                kwargs = {}
                ok = True
                for col in table.columns:
                    if col.primary_key and isinstance(col.type, sa.Integer):
                        continue
                    fks = list(col.foreign_keys)
                    if fks:
                        parent_table = fks[0].column.table.name
                        pool = pk_pool.get(parent_table)
                        if pool:
                            kwargs[col.name] = pool[i % len(pool)]
                        elif col.nullable:
                            kwargs[col.name] = None
                        else:
                            ok = False
                            break
                        continue
                    if col.nullable and i % 5 == 4:
                        kwargs[col.name] = None   # NULL-y tozhe nado uvidet
                        continue
                    try:
                        kwargs[col.name] = value_for(rng, table.name, col, i)
                    except Exception:
                        ok = False
                        break
                if not ok:
                    continue
                # [REASON]: SAVEPOINT, a ne rollback vsey tranzakcii. Do etogo
                # neudacha ODNOY stroki otkatyvala VSE uzhe nakoplennye stroki
                # etoy tablicy, a schetchik `made` prodolzhal ih schitat --
                # to est vral. Popadet tablica v bazu ili ostanetsya pustoy,
                # zaviselo ot togo, povezlo li POSLEDNEY stroke: esli padala
                # ona, commit nizhe sohranyal nichego.
                # Tak i sluchilos so spare_part_write_off_acts posle perehoda
                # na potablichnoe zerno: act_number UNIQUE, tri kollizii, i
                # poslednyaya iz nih na poslednem ryadu -- tablica opustela,
                # a obhod poteryal dva marshruta. Molcha.
                try:
                    with db.session.begin_nested():
                        db.session.add(model(**kwargs))
                        db.session.flush()
                except Exception:
                    continue

            db.session.commit()
            # Schitaem po BAZE, a ne po schetchiku popytok: schetchik ne znaet
            # pro otkaty i ne otlichaet "sozdano" ot "probovali sozdat".
            pk_col = list(table.primary_key.columns)[0]
            pks = [r[0] for r in db.session.execute(sa.select(pk_col).limit(60))]
            made = db.session.execute(
                sa.select(sa.func.count()).select_from(table)).scalar() or 0
            if made:
                created[table.name] = made
                pk_pool[table.name] = pks

        concentrate_equipment(db)
        created['daily_record_units'] = seed_daily_units(db)
        seed_closed_set_statuses(db)

        # Polzovateli -- otdelno i yavno: nuzhny vse chetyre roli, chtoby snyat
        # sostoyaniya "net prav", i predskazuemye loginy dlya obhoda.
        made_users = []
        # [REASON]: yazyk interfeysa beretsya iz uchetnoy zapisi
        # (g.lang = current_user.language), a ne iz query-parametra. Poetomu
        # progon na uzbekskom trebuet OTDELNOGO polzovatelya, a ne flaga u
        # kraulera; i chtoby snyat na uzbekskom ves obhod, vklyuchaya admin-
        # ekrany, nuzhen imenno administrator s language='uz'.
        for username, role, lang in (
                ('ux_admin', ROLE_ADMIN, 'ru'),
                ('ux_admin_uz', ROLE_ADMIN, 'uz'),
                ('ux_operator', ROLE_OPERATOR, 'uz'),
                ('ux_viewer', ROLE_VIEWER, 'uz'),
                ('ux_mechanic', ROLE_MECHANIC, 'uz')):
            user = User(username=username, role=role, language=lang,
                        full_name=make_text(rng, 24, uz=(lang == 'uz')),
                        is_active_user=True)
            user.set_password('ux-audit-local')
            db.session.add(user)
            made_users.append(username)
        db.session.commit()
        created['users'] = len(made_users)

    if verbose:
        total = sum(created.values())
        print('seeded %d tables, %d rows' % (len(created), total))
    return created


if __name__ == '__main__':
    print('This module is called by tools/ux/serve_ephemeral.py, not directly.')
    sys.exit(2)
