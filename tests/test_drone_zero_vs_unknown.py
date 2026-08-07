# -*- coding: utf-8 -*-
"""DRONE-ZERO-VS-UNKNOWN-001 -- «ноль» и «не записано» перестают быть одним
пикселем в трёх колонках и в третьей корзине долгов.

  A  the counters              _drone_work_cut() also counts missing received
  B  the shared macro          one money_cell(), two templates, `with context`
  C  the two screens           three columns obey the rule of §2
  D  the third bucket          «долг неизвестен» leaves «долга нет»
  E  the workbooks             an unknown figure is an EMPTY cell, never 0

What these assertions can and cannot prove:

  * Every negative control here re-runs the REAL pre-commit code, loaded out
    of git at BASE_COMMIT as a second module object, against the SAME fixture
    rows -- not a paraphrase of it. Where git cannot produce that commit the
    control skips loudly instead of passing quietly; a skipped control is not
    a passed one and the PR body says so.
  * B-2 is the `with context` check. It is written so that deleting
    `with context` from either import makes it fail, and that was verified by
    deleting it once -- see the PR body for the raw output.
  * Nothing here was run against production. The production figures quoted in
    the task (904 jobs, 852 with a received figure, 52 without) are the task's
    numbers; this branch has no access to that database.

Run:
  python -m unittest tests.test_drone_zero_vs_unknown -v
"""
import importlib.util
import io
import os
import re
import subprocess
import sys
import tempfile
import unittest

from tests.harness import app, reset_db, create_admin, login
from models import db, DroneCustomer, DroneCustomerAlias, DroneOperator, \
    DroneWork, User

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The merge base this task was written against, named in the task itself.
# The negative controls load drones.py OUT OF THIS COMMIT and run it against
# the same fixture, so «the pre-commit tree renders 0» is measured and not
# asserted from memory.
BASE_COMMIT = '116e15a'


# ── the fixture ──────────────────────────────────────────────────────────────
# Six customer groups, chosen so that every case the three columns and the
# three buckets have to tell apart is present exactly once.
#
#   group          jobs  amount           received        no_amount no_received
#   ALL_NULL         2   NULL, NULL       NULL, NULL          2         2
#   MIXED            2   NULL, 5 000 000  1 000 000,          1         0
#                                          2 000 000
#   NONE_NULL        2   3 000 000,       3 000 000,          0         0
#                        4 000 000        4 000 000
#   NO_RECEIVED      2   6 000 000,       NULL, NULL          0         2
#                        7 000 000
#   UNSTATED  (svc) 1    NULL             NULL                1         1
#   UNRESOLVED(svc) 1    1 000 000        NULL                0         1
ALL_NULL = 'Бухоро Агрокластер Заминлари МЧЖ'
MIXED = 'Дўстлик ФХ'
NONE_NULL = 'Пешку АМТ'
NO_RECEIVED = 'Миробод АМТ'
UNSTATED_LABEL_RU = 'Заказчик не указан'
UNRESOLVED_LABEL_RU = 'Заказчик не определён'
UNSTATED_LABEL_UZ = 'Буюртмачи кўрсатилмаган'
UNRESOLVED_LABEL_UZ = 'Буюртмачи аниқланмаган'

# (amount, received) per job, in group order.
GROUPS = (
    (ALL_NULL, ((None, None), (None, None))),
    (MIXED, ((None, 1000000.0), (5000000.0, 2000000.0))),
    (NONE_NULL, ((3000000.0, 3000000.0), (4000000.0, 4000000.0))),
    (NO_RECEIVED, ((6000000.0, None), (7000000.0, None))),
)

EXPECTED = {
    #             jobs amount     received   no_amount no_received
    ALL_NULL: (2, 0.0, 0.0, 2, 2),
    MIXED: (2, 5000000.0, 3000000.0, 1, 0),
    NONE_NULL: (2, 7000000.0, 7000000.0, 0, 0),
    NO_RECEIVED: (2, 13000000.0, 0.0, 0, 2),
    UNSTATED_LABEL_RU: (1, 0.0, 0.0, 1, 1),
    UNRESOLVED_LABEL_RU: (1, 1000000.0, 0.0, 0, 1),
}

TOTAL_JOBS = 10
TOTAL_AMOUNT = 26000000.0
TOTAL_RECEIVED = 10000000.0
TOTAL_OUTSTANDING = 16000000.0
TOTAL_NO_AMOUNT = 4
TOTAL_NO_RECEIVED = 6


def seed():
    """Build the fixture in the disposable test database."""
    reset_db()
    with app.app_context():
        operators = {}
        for key, name in (('op1', 'Файзуллаев Фурқат'),
                          ('op2', 'Хамроев Шохрух')):
            row = DroneOperator(full_name=name, subdivision_name='Гарден')
            db.session.add(row)
            db.session.flush()
            operators[key] = row.id

        def add(amount, received, customer_id, customer_raw, operator):
            db.session.add(DroneWork(
                period_month='2026-04',
                drone_customer_id=customer_id,
                customer_raw=customer_raw,
                drone_operator_id=operators[operator],
                operator_raw=operator,
                area_ha=10.0, amount=amount, received_amount=received,
                payment_type='cash', subdivision_name='Гарден',
                source_file='fixture.xlsx', source_sheet='свод ичи',
                import_batch='zero-vs-unknown'))

        for index, (name, jobs) in enumerate(GROUPS):
            customer = DroneCustomer(name=name)
            db.session.add(customer)
            db.session.flush()
            db.session.add(DroneCustomerAlias(
                raw_name=name, normalized_key=name.casefold(),
                drone_customer_id=customer.id, is_active=True))
            operator = 'op1' if index < 2 else 'op2'
            for amount, received in jobs:
                add(amount, received, customer.id, name, operator)

        # [REASON]: the two service rows are DIFFERENT facts and the cut keeps
        # them apart -- «имени в ведомости не было» (the holding's own land,
        # customer_raw = '') versus «написание есть, алиаса нет». Both have to
        # be in the fixture, because both are ordinary groups as far as the
        # three columns and the three buckets are concerned, and a fixture
        # that only exercised named customers would never render them.
        add(None, None, None, '', 'op2')
        add(1000000.0, None, None, 'Хўжалик номаълум', 'op2')
        db.session.commit()


def set_language(user_id, lang):
    with app.app_context():
        user = db.session.get(User, user_id)
        user.language = lang
        db.session.commit()


def report_data(lang='ru'):
    """The structure both screens and both workbooks are built from."""
    import drones
    from flask import g
    from werkzeug.datastructures import MultiDict
    with app.test_request_context('/drones/works/reports'):
        g.lang = lang
        return drones._drone_works_report_data(
            drones._drone_work_conditions(
                drones._drone_works_filters_from_args(MultiDict())))


def by_label(cut):
    """{label: row} over the ordinary rows AND the service rows of a cut."""
    return {r['label']: r for r in cut['rows'] + list(cut['services'])}


_CELL_RE = re.compile(r'<td[^>]*>(.*?)</td>', re.S)


def cut_cells(body, title, column):
    """{row label: one <td> as rendered} for one table of a rendered page.

    column is the 0-based index inside the row. The report cuts run
    label, jobs, area, amount, received, outstanding, share; the debt tables
    run label, jobs, amount, received, outstanding.
    """
    after = body.split(title, 1)[1]
    tbody = after.split('<tbody>')[1].split('</tbody>')[0]
    cells = {}
    for row in tbody.split('<tr'):
        tds = _CELL_RE.findall(row)
        if len(tds) <= column:
            continue
        label = text_of(tds[0])
        cells[label] = tds[column]
    return cells


# ── the pre-commit tree, loaded out of git ───────────────────────────────────
_PRE_COMMIT_CACHE = {}


def pre_commit_drones():
    """drones.py as of BASE_COMMIT, imported as a SECOND module object.

    [REASON]: a negative control that quotes the old code into the test file
    proves only that the quote differs. This loads the real pre-commit module
    and runs it against the same rows in the same session, so «the pre-commit
    tree renders 0 here» is a measurement.

    The module builds its own Blueprint('drones') object at import time; it is
    never registered on the app, so the live blueprint is untouched. models.db
    is shared, which is exactly what makes the comparison meaningful.

    Returns None when git cannot produce the blob (a shallow clone, a tarball
    export). Callers skip in that case -- loudly, never silently passing.
    """
    if 'module' in _PRE_COMMIT_CACHE:
        return _PRE_COMMIT_CACHE['module']
    _PRE_COMMIT_CACHE['module'] = None
    try:
        source = subprocess.check_output(
            ['git', 'show', '%s:drones.py' % BASE_COMMIT],
            cwd=REPO_ROOT, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        return None
    handle, path = tempfile.mkstemp(prefix='drones_pre_', suffix='.py')
    with os.fdopen(handle, 'wb') as fh:
        fh.write(source)
    spec = importlib.util.spec_from_file_location('drones_pre_commit', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules['drones_pre_commit'] = module
    spec.loader.exec_module(module)
    _PRE_COMMIT_CACHE['module'] = module
    return module


class PreCommitMixin(object):
    def pre_commit(self):
        module = pre_commit_drones()
        if module is None:
            self.skipTest('git cannot produce %s:drones.py -- the negative '
                          'control did NOT run' % BASE_COMMIT)
        return module

    def pre_commit_report_data(self, lang='ru'):
        module = self.pre_commit()
        from flask import g
        from werkzeug.datastructures import MultiDict
        with app.test_request_context('/drones/works/reports'):
            g.lang = lang
            return module._drone_works_report_data(
                module._drone_work_conditions(
                    module._drone_works_filters_from_args(MultiDict())))


# ---------------------------------------------------------------------------
# A  commit 1 -- the counters
# ---------------------------------------------------------------------------

class TestA1Counters(unittest.TestCase):
    """A-1: no_amount and no_received on every row, service row and total."""

    @classmethod
    def setUpClass(cls):
        seed()

    def setUp(self):
        self.cut = report_data()['by_customer']
        self.rows = by_label(self.cut)

    def test_a1_every_group_carries_both_counters(self):
        for label, (jobs, amount, received, no_amount, no_received) in \
                EXPECTED.items():
            with self.subTest(group=label):
                row = self.rows[label]
                self.assertEqual(jobs, row['jobs'])
                self.assertAlmostEqual(amount, row['amount'], places=2)
                self.assertAlmostEqual(received, row['received'], places=2)
                self.assertEqual(no_amount, row['no_amount'])
                self.assertEqual(no_received, row['no_received'])

    def test_a1_the_four_cases_really_are_four_different_cases(self):
        """A fixture where two cases coincide proves nothing about either."""
        shapes = {(self.rows[label]['no_amount'] == self.rows[label]['jobs'],
                   self.rows[label]['no_received'] == self.rows[label]['jobs'],
                   bool(self.rows[label]['no_amount']))
                  for label in (ALL_NULL, MIXED, NONE_NULL, NO_RECEIVED)}
        self.assertEqual(4, len(shapes), shapes)

    def test_a1_the_service_rows_carry_them_too(self):
        services = {r['label']: r for r in self.cut['services']}
        self.assertEqual({UNSTATED_LABEL_RU, UNRESOLVED_LABEL_RU},
                         set(services))
        self.assertEqual(1, services[UNSTATED_LABEL_RU]['no_received'])
        self.assertEqual(1, services[UNSTATED_LABEL_RU]['no_amount'])
        self.assertEqual(1, services[UNRESOLVED_LABEL_RU]['no_received'])
        self.assertEqual(0, services[UNRESOLVED_LABEL_RU]['no_amount'])

    def test_a1_the_total_carries_them(self):
        total = self.cut['total']
        self.assertEqual(TOTAL_JOBS, total['jobs'])
        self.assertEqual(TOTAL_NO_AMOUNT, total['no_amount'])
        self.assertEqual(TOTAL_NO_RECEIVED, total['no_received'])


class TestA2CountersAddUp(unittest.TestCase):
    """A-2: a counter that does not add up is measuring something else."""

    CUTS = ('by_customer', 'by_operator', 'by_subdivision', 'by_month',
            'by_payment')

    @classmethod
    def setUpClass(cls):
        seed()

    def setUp(self):
        self.data = report_data()

    def test_a2_each_total_equals_the_sum_over_its_groups(self):
        for name in self.CUTS:
            with self.subTest(cut=name):
                cut = self.data[name]
                everything = cut['rows'] + list(cut['services'])
                self.assertEqual(cut['total']['no_amount'],
                                 sum(r['no_amount'] for r in everything))
                self.assertEqual(cut['total']['no_received'],
                                 sum(r['no_received'] for r in everything))

    def test_a2_every_cut_agrees_on_the_two_counts(self):
        counts = {name: (self.data[name]['total']['no_amount'],
                         self.data[name]['total']['no_received'])
                  for name in self.CUTS}
        self.assertEqual(
            {name: (TOTAL_NO_AMOUNT, TOTAL_NO_RECEIVED) for name in counts},
            counts)

    def test_a2_the_money_totals_are_untouched_by_the_new_column(self):
        """Adding a counter must not disturb the arithmetic it sits beside."""
        for name in self.CUTS:
            with self.subTest(cut=name):
                total = self.data[name]['total']
                self.assertAlmostEqual(TOTAL_AMOUNT, total['amount'], places=2)
                self.assertAlmostEqual(TOTAL_RECEIVED, total['received'],
                                       places=2)
                self.assertAlmostEqual(TOTAL_OUTSTANDING,
                                       total['outstanding'], places=2)
                self.assertTrue(self.data[name]['reconciled'], name)

    def test_a2_rest_carries_both_counters(self):
        """`rest` is assembled by naming its keys, so a counter reaches it
        only if somebody named it. no_amount never was."""
        rest = self.data['debt_by_customer']['rest']
        self.assertIsNotNone(rest)
        self.assertIn('no_amount', rest)
        self.assertIn('no_received', rest)


class TestA3NegativeControl(PreCommitMixin, unittest.TestCase):
    """A-3: the same assertions fail against the real pre-commit module."""

    @classmethod
    def setUpClass(cls):
        seed()

    def test_a3_pre_commit_rows_have_no_no_received(self):
        cut = self.pre_commit_report_data()['by_customer']
        row = by_label(cut)[ALL_NULL]
        self.assertEqual(2, row['no_amount'], 'the fixture is the same one')
        with self.assertRaises(KeyError):
            row['no_received']

    def test_a3_pre_commit_total_has_no_no_received(self):
        cut = self.pre_commit_report_data()['by_customer']
        with self.assertRaises(KeyError):
            cut['total']['no_received']

    def test_a3_pre_commit_rest_drops_the_counter(self):
        rest = self.pre_commit_report_data()['debt_by_customer']['rest']
        self.assertIsNotNone(rest, 'the settled bucket exists pre-commit')
        self.assertNotIn('no_amount', rest)
        self.assertNotIn('no_received', rest)

    def test_a3_the_control_is_reading_the_other_module(self):
        """Guard against the control silently importing the live module."""
        import drones
        module = self.pre_commit()
        self.assertIsNot(module, drones)
        self.assertNotEqual(module.__file__, drones.__file__)


# ---------------------------------------------------------------------------
# B  commit 2 -- one macro, shared by two templates
# ---------------------------------------------------------------------------

PARTIAL = 'templates/drones/_money_cell.html'
REPORTS_TEMPLATE = 'templates/drones/works_reports.html'
DEBTS_TEMPLATE = 'templates/drones/works_debts.html'
MACRO_TEMPLATES = (REPORTS_TEMPLATE, DEBTS_TEMPLATE)

IMPORT_LINE = ("{% from 'drones/_money_cell.html' import money_cell "
               "with context %}")


def read_template(relative_path):
    with io.open(os.path.join(REPO_ROOT, relative_path), encoding='utf-8') as f:
        return f.read()


def render_macro(call, lang='ru', with_context=True):
    """Render one money_cell() call through the REAL application Jinja env.

    with_context=False reproduces exactly the mistake §4 of the task warns
    about, so the check that catches it can be shown to catch it.
    """
    from flask import g
    source = ("{%% set is_ru = (lang == 'ru') %%}"
              "{%% from 'drones/_money_cell.html' import money_cell%s %%}"
              "%s") % (' with context' if with_context else '', call)
    with app.test_request_context('/drones/works/reports'):
        g.lang = lang
        return app.jinja_env.from_string(source).render(lang=lang)


# [REASON]: NBSP is NOT whitespace here. str.split() eats U+00A0, so the
# obvious ' '.join(text.split()) turns «5\xa0000\xa0000» into «5 000 000» and
# every assertion that looks for the grouped number then matches the ungrouped
# one -- a check that gives the same answer for right and wrong code. The
# grouping separator IS the vs_num filter's output and has to survive.
_WS_RE = re.compile(r'[^\S\xa0]+')
_NOTE_RE = re.compile(r'<div class="vs-muted"[^>]*>(.*?)</div>', re.S)


def text_of(html):
    return _WS_RE.sub(' ', re.sub(r'<[^>]+>', ' ', html)).strip()


def note_text(rendered):
    """The muted note's WORDING alone: «сумма не указана: 1» -> the words.

    Returns '' when there is no note at all and when the note rendered with
    an empty wording -- the two ways this can be broken, kept apart by the
    caller asserting which one it expects.
    """
    match = _NOTE_RE.search(rendered)
    if match is None:
        return ''
    return _WS_RE.sub(' ', match.group(1)).rsplit(':', 1)[0].strip()


class TestB1TheFourCombinations(unittest.TestCase):
    """B-1: the partial rendered directly, one case per row of §2's table."""

    def test_b1_missing_equals_jobs_is_a_dash(self):
        out = render_macro("{{ money_cell(0, 2, 2, 'amount') }}")
        self.assertEqual('—', text_of(out))
        self.assertNotIn('0', text_of(out))

    def test_b1_partial_with_a_note_shows_the_value_and_the_count(self):
        out = render_macro("{{ money_cell(5000000, 2, 1, 'amount') }}")
        self.assertIn('5\xa0000\xa0000', out)
        self.assertIn('сумма не указана', text_of(out))
        self.assertTrue(text_of(out).endswith('1'), text_of(out))

    def test_b1_partial_without_a_note_shows_the_value_only(self):
        """«Не получено» keys off no_amount and gets no note of its own."""
        out = render_macro("{{ money_cell(5000000, 2, 1) }}")
        self.assertIn('5\xa0000\xa0000', out)
        self.assertNotIn('vs-muted', out)
        self.assertEqual('5\xa0000\xa0000', text_of(out))

    def test_b1_missing_zero_shows_the_value_only(self):
        out = render_macro("{{ money_cell(7000000, 2, 0, 'amount') }}")
        self.assertIn('7\xa0000\xa0000', out)
        self.assertNotIn('vs-muted', out)

    def test_b1_the_four_renderings_are_four(self):
        rendered = {text_of(render_macro(call)) for call in (
            "{{ money_cell(0, 2, 2, 'amount') }}",
            "{{ money_cell(5000000, 2, 1, 'amount') }}",
            "{{ money_cell(5000000, 2, 1) }}",
            "{{ money_cell(7000000, 2, 0, 'amount') }}")}
        self.assertEqual(4, len(rendered), rendered)

    def test_b1_a_partial_group_is_never_a_dash(self):
        """Two of six missing is a lower bound with a count, not a dash."""
        out = render_macro("{{ money_cell(12000000, 6, 2, 'amount') }}")
        self.assertNotEqual('—', text_of(out))
        self.assertIn('12\xa0000\xa0000', out)
        self.assertTrue(text_of(out).endswith('2'), text_of(out))

    def test_b1_zero_jobs_is_not_a_dash(self):
        """jobs == 0 and missing == 0 are equal but mean «nothing here»."""
        self.assertEqual('0', text_of(render_macro(
            "{{ money_cell(0, 0, 0, 'amount') }}")))


class TestB2WithContextIsLoadBearing(unittest.TestCase):
    """B-2: the note is non-empty in both languages, and empty without
    `with context`.

    [REASON]: the note text is selected by a DICTIONARY LOOKUP on `lang`
    rather than by `if is_ru`. With a ternary, a macro imported without
    context evaluates `lang == 'ru'` to False and silently renders the Uzbek
    branch -- non-empty, plausible, and wrong in Russian. The lookup makes the
    same mistake produce an empty string, which is what these assertions can
    see. Verified by deleting `with context` once; the raw output is in the
    PR body.
    """

    NOTE_CALL = "{{ money_cell(5000000, 2, 1, '%s') }}"

    def test_b2_the_note_is_non_empty_in_both_languages(self):
        for lang in ('ru', 'uz'):
            for note in ('amount', 'received'):
                with self.subTest(lang=lang, note=note):
                    wording = note_text(
                        render_macro(self.NOTE_CALL % note, lang=lang))
                    self.assertTrue(wording,
                                    'empty note for %s/%s' % (lang, note))
                    self.assertNotIn(':', wording)

    def test_b2_the_four_notes_are_four_distinct_strings(self):
        seen = {note_text(render_macro(self.NOTE_CALL % note, lang=lang))
                for lang in ('ru', 'uz') for note in ('amount', 'received')}
        self.assertEqual(4, len(seen), seen)

    def test_b2_without_with_context_the_note_is_empty(self):
        """The negative control for the control: this is the failure mode."""
        for lang in ('ru', 'uz'):
            with self.subTest(lang=lang):
                rendered = render_macro(self.NOTE_CALL % 'amount', lang=lang,
                                        with_context=False)
                self.assertIn('vs-muted', rendered,
                              'the note div is still emitted -- only its '
                              'wording is lost')
                self.assertEqual('', note_text(rendered),
                                 'the check cannot tell the two cases apart')
                # the value itself still renders -- only the wording is lost,
                # which is why nothing else on the page would look wrong
                self.assertIn('5\xa0000\xa0000', rendered)

    def test_b2_the_note_is_non_empty_rendered_through_the_real_page(self):
        """Not the macro in isolation -- the page, through the real route.

        The MIXED group is the only one with 0 < no_amount < jobs, so its
        «Сумма» cell is the one carrying a note. Commit 3 extends this to the
        debts page and to «Получено» (C-4).
        """
        seed()
        admin = create_admin('b2_admin')
        for lang, title in (('ru', 'По заказчикам'),
                            ('uz', 'Буюртмачилар бўйича')):
            with self.subTest(lang=lang):
                set_language(admin, lang)
                client = app.test_client()
                login(client, admin)
                body = client.get(
                    '/drones/works/reports').data.decode('utf-8')
                cell = cut_cells(body, title, 3)[MIXED]
                self.assertIn('vs-muted', cell, 'no note rendered at all')
                self.assertTrue(note_text(cell),
                                'the note rendered with an EMPTY wording -- '
                                'this is what a missing `with context` looks '
                                'like on the page')

    def test_b2_both_templates_import_with_context(self):
        for path in MACRO_TEMPLATES:
            with self.subTest(template=path):
                source = read_template(path)
                # assertTrue, not assertIn: assertIn would dump the whole
                # template into the failure message.
                self.assertTrue(IMPORT_LINE in source,
                                '%s does not import money_cell WITH CONTEXT'
                                % path)
                self.assertEqual(
                    1, source.count("import money_cell"),
                    'exactly one import of the macro per template')

    def test_b2_the_note_wording_lives_only_in_the_partial(self):
        """One owner for the wording, or the two spellings come back."""
        for path in MACRO_TEMPLATES:
            with self.subTest(template=path):
                self.assertNotIn('сумма не указана', read_template(path))
                self.assertNotIn('сумма кўрсатилмаган', read_template(path))

    def test_b2_the_partial_selects_by_lookup_not_by_a_ternary(self):
        """The property B-2 rests on, asserted where it can be seen.

        A ternary on is_ru would make test_b2_without_with_context_the_note
        _is_empty pass by accident in Uzbek and fail to protect Russian.
        """
        source = read_template(PARTIAL)
        macro_body = source.split('{% macro money_cell', 1)[1]
        self.assertIn(".get(lang, {})", macro_body)
        self.assertNotIn('if is_ru', macro_body)


class TestB3TheOldMacroIsGone(unittest.TestCase):
    """B-3: works_reports.html no longer defines its own amount_cell."""

    DECL = re.compile(r'\{%-?\s*macro\s+([A-Za-z_][A-Za-z0-9_]*)')

    def test_b3_no_template_declares_amount_cell(self):
        offenders = []
        for root, _dirs, files in os.walk(os.path.join(REPO_ROOT,
                                                       'templates')):
            for name in files:
                if not name.endswith('.html'):
                    continue
                path = os.path.join(root, name)
                with io.open(path, encoding='utf-8') as fh:
                    for macro in self.DECL.findall(fh.read()):
                        if macro == 'amount_cell':
                            offenders.append(os.path.relpath(path, REPO_ROOT))
        self.assertEqual([], offenders)

    def test_b3_nothing_calls_amount_cell_any_more(self):
        for path in MACRO_TEMPLATES:
            with self.subTest(template=path):
                self.assertNotIn('amount_cell(', read_template(path))

    def test_b3_money_cell_is_declared_exactly_once_in_the_repo(self):
        declarations = []
        for root, _dirs, files in os.walk(os.path.join(REPO_ROOT,
                                                       'templates')):
            for name in files:
                if not name.endswith('.html'):
                    continue
                path = os.path.join(root, name)
                with io.open(path, encoding='utf-8') as fh:
                    if 'money_cell' in self.DECL.findall(fh.read()):
                        declarations.append(os.path.relpath(path, REPO_ROOT))
        self.assertEqual([PARTIAL], declarations)


# ---------------------------------------------------------------------------
# C  commit 3 -- the two screens
# ---------------------------------------------------------------------------

# (page, table title RU, table title UZ, column index of «Сумма»)
# Report cuts:  label jobs area amount received outstanding share
# Debt tables:  label jobs      amount received outstanding
PAGES = (
    ('/drones/works/reports', 'По заказчикам', 'Буюртмачилар бўйича', 3),
    ('/drones/works/debts', 'Долги — по заказчикам',
     'Қарзлар — буюртмачилар бўйича', 2),
)


class ScreenMixin(object):
    @classmethod
    def setUpClass(cls):
        seed()
        cls.admin = create_admin(cls.__name__.lower()[:24])
        set_language(cls.admin, 'ru')

    def page(self, url, lang='ru'):
        set_language(self.admin, lang)
        client = app.test_client()
        login(client, self.admin)
        response = client.get(url)
        self.assertEqual(200, response.status_code, url)
        return response.data.decode('utf-8')

    def money_columns(self, url, title, first, lang='ru'):
        """{label: (amount cell, received cell, outstanding cell)}."""
        body = self.page(url, lang=lang)
        columns = [cut_cells(body, title, first + offset)
                   for offset in (0, 1, 2)]
        return {label: tuple(column[label] for column in columns)
                for label in columns[0]}


class TestC1TheRuleOnBothScreens(ScreenMixin, unittest.TestCase):
    """C-1: the rule of §2, in all three columns, on both pages.

    ORDERING, stated rather than adapted to: §5's C-1 asks for the all-NULL
    group to be asserted on BOTH pages at this commit. On the debts page it
    is not a row yet -- it has outstanding == 0, so _drone_work_debt_cut()
    folds it into «Долга нет (остальные)», which is the defect commit 4
    removes. Asserting it here would mean writing a test that only passes two
    commits later and leaving this tree red. So the all-NULL group is checked
    on the reports page here and on the debts page in D-1, and
    test_c1_the_debts_page_still_hides_it_at_this_commit records exactly where
    it currently sits -- that assertion INVERTS in commit 4 and is rewritten
    there.
    """

    def test_c1_amount_and_outstanding_are_dashes_received_follows_its_own(
            self):
        cells = self.money_columns('/drones/works/reports', 'По заказчикам', 3)
        amount, received, outstanding = cells[ALL_NULL]
        self.assertEqual('—', text_of(amount))
        self.assertEqual('—', text_of(outstanding),
                         'a debt computed from an unknown amount is unknown, '
                         'not zero')
        # ALL_NULL also has every received NULL, so «Получено» is a dash here
        # by its OWN counter.
        self.assertEqual('—', text_of(received))

    def test_c1_the_debts_page_still_hides_it_at_this_commit(self):
        """Where the all-NULL group sits BEFORE commit 4: inside «Долга нет».

        Its three columns are not dashes there, because the row it is folded
        into also holds a fully-priced group. That is the sentence commit 4
        fixes: the page says this farm owes nothing.
        """
        cells = self.money_columns('/drones/works/debts',
                                   'Долги — по заказчикам', 2)
        self.assertNotIn(ALL_NULL, cells)
        rest = [label for label in cells if 'Долга нет' in label]
        self.assertEqual(1, len(rest), cells.keys())
        amount, _received, outstanding = cells[rest[0]]
        self.assertNotEqual('—', text_of(outstanding))
        # commit 1's fix reaching the screen: `rest` now carries no_amount,
        # so the folded row at least admits three of its five jobs have none.
        self.assertEqual('сумма не указана', note_text(amount))

    def test_c1_received_keys_off_its_own_counter_not_off_no_amount(self):
        """NO_RECEIVED: amounts known, received all NULL.

        «Сумма» prints its sum, «Получено» is a dash, «Не получено» prints
        the debt -- the amount is known, so the debt is known.
        """
        for url, title, _uz, first in PAGES:
            with self.subTest(page=url):
                amount, received, outstanding = \
                    self.money_columns(url, title, first)[NO_RECEIVED]
                self.assertIn('13\xa0000\xa0000', amount)
                self.assertEqual('—', text_of(received))
                self.assertIn('13\xa0000\xa0000', outstanding)

    def test_c1_outstanding_keys_off_no_amount_not_off_its_own(self):
        """UNSTATED: one job, no amount, no received.

        Proves «Не получено» reads no_amount: its own value is 0.0 and a
        column keyed off «is it zero» would print 0 here. On the reports page
        for the same reason as above -- the debts page folds it until
        commit 4.
        """
        cells = self.money_columns('/drones/works/reports', 'По заказчикам', 3)
        label = [k for k in cells if UNSTATED_LABEL_RU in k][0]
        amount, received, outstanding = cells[label]
        self.assertEqual('—', text_of(amount))
        self.assertEqual('—', text_of(received))
        self.assertEqual('—', text_of(outstanding))

    def test_c1_a_partial_group_shows_its_sum_and_the_count(self):
        for url, title, _uz, first in PAGES:
            with self.subTest(page=url):
                amount, received, outstanding = \
                    self.money_columns(url, title, first)[MIXED]
                self.assertIn('5\xa0000\xa0000', amount)
                self.assertEqual('сумма не указана', note_text(amount))
                # 0 of 2 missing -> a plain sum, no note
                self.assertIn('3\xa0000\xa0000', received)
                self.assertEqual('', note_text(received))
                # «Не получено» never carries a note of its own
                self.assertIn('2\xa0000\xa0000', outstanding)
                self.assertEqual('', note_text(outstanding))

    def test_c1_a_fully_known_group_is_three_plain_numbers(self):
        for url, title, _uz, first in PAGES:
            with self.subTest(page=url):
                cells = self.money_columns(url, title, first)
                if NONE_NULL not in cells:
                    # settled groups live under «Долга нет» on the debts page
                    continue
                for cell in cells[NONE_NULL]:
                    self.assertNotIn('vs-muted', cell)
                    self.assertNotEqual('—', text_of(cell))

    def test_c1_the_received_note_renders_where_it_belongs(self):
        """0 < no_received < jobs on a cut that mixes the groups.

        by_operator puts NONE_NULL (0 of 2 missing) and NO_RECEIVED (2 of 2)
        into op2 together with the two service jobs, so op2 is partial.
        """
        cells = self.money_columns('/drones/works/reports',
                                   'По операторам', 3)
        partial = [c for label, c in cells.items()
                   if note_text(c[1])]
        self.assertTrue(partial, 'no «Получено» note rendered anywhere')
        self.assertEqual({'получено не указано'},
                         {note_text(c[1]) for c in partial})


class TestC2NegativeControl(ScreenMixin, PreCommitMixin, unittest.TestCase):
    """C-2: the pre-commit tree prints 0 in «Не получено» on both pages."""

    def test_c2_the_group_really_does_have_zero_outstanding(self):
        """The data has not changed -- only what the screens say about it."""
        row = by_label(self.pre_commit_report_data()['by_customer'])[ALL_NULL]
        self.assertEqual(0.0, row['amount'])
        self.assertEqual(0.0, row['received'])
        self.assertEqual(0.0, row['outstanding'])
        self.assertEqual(2, row['jobs'])

    def test_c2_pre_commit_markup_prints_that_zero_as_zero(self):
        """The exact expressions the two templates carried at 116e15a,
        rendered through the REAL vs_num filter."""
        row = by_label(self.pre_commit_report_data()['by_customer'])[ALL_NULL]
        for column in ('received', 'outstanding'):
            with self.subTest(column=column):
                pre_fix = "{{ '%%.0f'|format(cell.%s)|vs_num }}" % column
                with app.app_context():
                    rendered = app.jinja_env.from_string(pre_fix).render(
                        cell=row)
                self.assertEqual('0', rendered.strip())

    def test_c2_the_pre_commit_expression_really_is_what_shipped(self):
        """Quoting an expression proves nothing unless it is THE expression."""
        for path, names in ((REPORTS_TEMPLATE,
                             ('r.received', 'r.outstanding')),
                            (DEBTS_TEMPLATE, ('r.amount', 'r.received',
                                              'r.outstanding'))):
            source = subprocess.check_output(
                ['git', 'show', '%s:%s' % (BASE_COMMIT, path)],
                cwd=REPO_ROOT).decode('utf-8')
            for name in names:
                with self.subTest(template=path, cell=name):
                    self.assertIn("{{ '%%.0f'|format(%s)|vs_num }}" % name,
                                  source)

    def test_c2_and_the_current_reports_page_does_not(self):
        cells = self.money_columns('/drones/works/reports', 'По заказчикам', 3)
        self.assertEqual('—', text_of(cells[ALL_NULL][2]))
        self.assertNotIn('0', text_of(cells[ALL_NULL][2]))

    def test_c2_the_debts_page_gets_its_own_control_in_d4(self):
        """Named so the gap is visible rather than implied.

        On the debts page the all-NULL group is not a row until commit 4, so
        there is no “Не получено” cell of its own to compare against the
        pre-commit 0. D-4 is that control.
        """
        cells = self.money_columns('/drones/works/debts',
                                   'Долги — по заказчикам', 2)
        self.assertNotIn(ALL_NULL, cells)


class TestC3DivBalance(unittest.TestCase):
    """C-3: report the counts, do not assert they are equal.

    The macro legitimately adds a muted <div> per partial group, and moving
    the amount cell out removed one; asserting equality would only be true by
    coincidence. tools/check_templates.py is what enforces BALANCE (open ==
    close) per file, and it is a blocking CI check.
    """

    OPEN_RE = re.compile(r'<div(?=[\s>/])', re.I)
    CLOSE_RE = re.compile(r'</div\s*>', re.I)
    COMMENT_RE = re.compile(r'\{#.*?#\}', re.S)

    def counts(self, source):
        stripped = self.COMMENT_RE.sub('', source)
        return (len(self.OPEN_RE.findall(stripped)),
                len(self.CLOSE_RE.findall(stripped)))

    def test_c3_both_templates_are_balanced_now(self):
        for path in MACRO_TEMPLATES + (PARTIAL,):
            with self.subTest(template=path):
                opened, closed = self.counts(read_template(path))
                self.assertEqual(opened, closed, path)

    def test_c3_report_the_before_and_after(self):
        """Recorded, not asserted equal. The numbers go in the PR body."""
        reported = {}
        for path in MACRO_TEMPLATES:
            before = subprocess.check_output(
                ['git', 'show', '%s:%s' % (BASE_COMMIT, path)],
                cwd=REPO_ROOT).decode('utf-8')
            reported[path] = (self.counts(before),
                              self.counts(read_template(path)))
        self.assertEqual(
            {REPORTS_TEMPLATE: ((41, 41), (40, 40)),
             DEBTS_TEMPLATE: ((39, 39), (39, 39))},
            reported)


class TestC4BothLanguages(ScreenMixin, unittest.TestCase):
    """C-4: RU and UZ, both pages, no empty label in the three columns."""

    def test_c4_no_money_cell_renders_empty_in_either_language(self):
        for url, title_ru, title_uz, first in PAGES:
            for lang, title in (('ru', title_ru), ('uz', title_uz)):
                with self.subTest(page=url, lang=lang):
                    cells = self.money_columns(url, title, first, lang=lang)
                    self.assertTrue(cells, 'the table was not found at all')
                    for label, three in cells.items():
                        for index, cell in enumerate(three):
                            self.assertTrue(
                                text_of(cell),
                                'empty cell: %s / %s / column %d'
                                % (url, label, first + index))

    def test_c4_every_note_that_renders_has_wording(self):
        for url, title_ru, title_uz, first in PAGES:
            for lang, title in (('ru', title_ru), ('uz', title_uz)):
                with self.subTest(page=url, lang=lang):
                    for label, cells in self.money_columns(
                            url, title, first, lang=lang).items():
                        for cell in cells:
                            if 'vs-muted' not in cell:
                                continue
                            self.assertTrue(note_text(cell),
                                            'empty wording: %s / %s'
                                            % (url, label))

    def test_c4_the_uzbek_pages_really_are_uzbek(self):
        """Guards against the language switch silently not switching."""
        for url, _ru, title_uz, _first in PAGES:
            with self.subTest(page=url):
                self.assertIn(title_uz, self.page(url, lang='uz'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
