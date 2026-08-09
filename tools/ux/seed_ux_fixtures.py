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

    rng = random.Random(SEED)
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
                try:
                    obj = model(**kwargs)
                    db.session.add(obj)
                    db.session.flush()
                    made += 1
                except Exception:
                    db.session.rollback()
                    continue

            if made:
                db.session.commit()
                created[table.name] = made
                pks = [r[0] for r in db.session.execute(
                    sa.select(list(table.primary_key.columns)[0]).limit(60))]
                pk_pool[table.name] = pks

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
