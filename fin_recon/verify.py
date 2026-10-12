# -*- coding: utf-8 -*-
"""fin_recon/verify.py -- сверка базы с пакетом. Только чтение.

Снимок в базе считается тем же, что в пакете, только если совпало всё:

* хеш содержимого в строке снимка, в печати и в пакете;
* хеш `snapshot_json`, сохранённого при импорте;
* хеш снимка, собранного обратно ИЗ СТРОК таблиц (а не из snapshot_json) --
  это и есть доказательство хранения без потерь;
* число строк каждой таблицы -- печать, база и пакет;
* статус «сверен с исключениями», пустые поля утверждения и закрытия.

Приватные ожидания месяца (`expectations/*.json`) сверяются с числами,
ПЕРЕСЧИТАННЫМИ по строкам базы, а не с числами, которые снимок объявил о
себе сам: снимок не может подтвердить сам себя.
"""

from decimal import Decimal, InvalidOperation

import fin_recon as fr
from fin_recon import contract
from fin_recon import schema
from fin_recon import store


class VerifySchemaMissing(Exception):
    pass


def _num(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        try:
            return Decimal(value)
        except InvalidOperation:
            return None
    return None


def _same(expected, actual):
    """Равенство ожидания и факта: числа -- как Decimal, прочее -- как есть."""
    a, b = _num(expected), _num(actual)
    if a is not None and b is not None:
        return a == b
    return expected == actual


def _text(value):
    if isinstance(value, Decimal):
        return contract.decimal_text(value)
    return repr(value)


def actual_value(snapshot, name):
    """Значение для ключа ожидания: check:, metric:, header:, dji:, vs:, links."""
    if name.startswith('check:'):
        return contract.derived_checks(snapshot).get(name[6:], KeyError)
    if name.startswith('metric:'):
        for m in snapshot.get('metrics') or []:
            if m.get('key') == name[7:]:
                return m.get('value')
        return KeyError
    if name.startswith('header:'):
        return (snapshot.get('header') or {}).get(name[7:], KeyError)
    if name.startswith('dji:'):
        return (snapshot.get('dji') or {}).get(name[4:], KeyError)
    if name.startswith('vs:'):
        return (snapshot.get('vs') or {}).get(name[3:], KeyError)
    if name == 'links':
        return sorted(
            ({'work_month': x['work_month'], 'area_ha': x['area_ha'],
              'salary_amount': x['salary_amount']}
             for x in snapshot.get('cross_period_links') or []),
            key=lambda x: x['work_month'])
    return KeyError


def compare_expectations(snapshot, expect):
    """[(ключ, ожидание, факт, совпало)] по всем ключам ожиданий."""
    out = []
    for name in sorted(expect):
        want = expect[name]
        got = actual_value(snapshot, name)
        if got is KeyError:
            out.append((name, want, 'NO SUCH VALUE', False))
            continue
        if name == 'links':
            want_list = sorted(want, key=lambda x: x['work_month']) \
                if isinstance(want, list) else want
            ok = isinstance(want_list, list) and len(want_list) == len(got) \
                and all(sorted(w) == sorted(g) and
                        all(_same(w[k], g[k]) for k in w)
                        for w, g in zip(want_list, got))
            out.append((name, want, got, ok))
            continue
        out.append((name, want, got, _same(want, got)))
    return out


def verify_db(con, pkg, expectations=None):
    """Отчёт сверки: {'ok': bool, 'checks': [(имя, ожидание, факт, ok)], ...}."""
    if schema.missing_tables(con):
        raise VerifySchemaMissing('fin_recon tables are missing (migration %s '
                                  'not applied)' % fr.MIGRATION_ID)
    checks = []

    def check(name, expected, actual):
        checks.append((name, expected, actual, expected == actual))

    meta = store.find_snapshot(con, pkg.period, pkg.version)
    if meta is None:
        checks.append(('snapshot present', '%s v%d' % (pkg.period, pkg.version),
                       'absent', False))
        return {'ok': False, 'checks': checks, 'snapshot': None,
                'controls': {}}
    sid = meta['id']
    check('content_sha256 (snapshot row)', pkg.content_sha256,
          meta['content_sha256'])
    check('snapshot_file_sha256', pkg.snapshot_file_sha256,
          meta['snapshot_file_sha256'])
    check('manifest_sha256', pkg.manifest_sha256, meta['manifest_sha256'])
    seal = store.seal_of(con, sid)
    check('sealed', True, seal is not None)
    expected_counts = store.expected_row_counts(pkg.snapshot)
    actual_counts = store.row_counts(con, sid)
    check('row counts (database)', expected_counts, actual_counts)
    if seal is not None:
        check('content_sha256 (seal)', pkg.content_sha256,
              seal['content_sha256'])
        check('row counts (seal)', expected_counts, seal['row_counts'])
    stored = store.stored_snapshot_json(con, sid)
    check('content_sha256 (stored snapshot_json)', pkg.content_sha256,
          contract.content_sha256(stored))
    rebuilt = store.load_snapshot(con, sid)
    check('content_sha256 (rebuilt from rows)', pkg.content_sha256,
          contract.content_sha256(rebuilt))
    check('status', fr.STATUS_RECONCILED_WITH_EXCEPTIONS, meta['status'])
    check('owner_approved_at', None, meta['owner_approved_at'])
    check('financially_closed_at', None, meta['financially_closed_at'])
    problems = contract.validate(rebuilt)
    check('contract of the rebuilt snapshot', [], problems)
    controls = contract.derived_checks(rebuilt)
    expectation_rows = []
    if expectations is not None:
        if expectations.get('period') != pkg.period or \
                expectations.get('version') != pkg.version:
            checks.append(('expectations period/version',
                           '%s v%s' % (pkg.period, pkg.version),
                           '%s v%s' % (expectations.get('period'),
                                       expectations.get('version')), False))
        expectation_rows = compare_expectations(rebuilt,
                                                expectations['expect'])
    ok = all(c[3] for c in checks) and all(e[3] for e in expectation_rows)
    return {'ok': ok, 'checks': checks, 'expectations': expectation_rows,
            'snapshot': meta, 'controls': controls}


__all__ = ['verify_db', 'compare_expectations', 'actual_value',
           'VerifySchemaMissing', '_text']
