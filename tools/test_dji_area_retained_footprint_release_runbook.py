# -*- coding: utf-8 -*-
"""Проверка блоков docs/DJI_AREA_RETAINED_FOOTPRINT_RELEASE_RUNBOOK.md.

[REASON]: это первые блоки проекта, которые останавливают службы PRODUCTION и
пишут в его базу. Свойства ниже ломаются молча -- ни `py_compile`, ни глаз при
чтении диффа их не ловят:

  * блок вставляется вербатим: `& { ... }`, только ASCII, плейсхолдеров и `&&`
    нет, python с пробелом в пути зовётся через `&`, каждый `throw` стоит за
    проверкой на своей строке, за каждым вызовом инструмента читается
    `$LASTEXITCODE`;
  * хост, каталог, база, службы и порт -- ровно production, и площадка не
    названа нигде: блок, перепутавший окружение, должен остановиться, а не
    работать «почти правильно»;
  * пин (тег и отпечаток) тот же, что в остальных ранбуках, отпечаток равен
    настоящему, и ревизия доказана до первой остановки службы;
  * R1 ничего не пишет и доказывает это хешем; PRE-APPLY идёт по НОВОМУ
    оракулу, исторический оракул блоками не читается;
  * R2 применяет ровно `expected_rewrites` оракула (`--flight-id`, номера из
    файла, а не из текста блока) и только после повторного PRE-APPLY; затем
    второй прогон, POST-APPLY с `--apply-summary`, сторож RAW; службы
    поднимаются в `finally`;
  * R3 применяет только утверждённый список -- тот же, что в оракуле, -- и
    только если свежий оценщик называет ровно его; пересчёта периода целиком
    в нём нет;
  * числа и коды возврата в тексте равны оракулу и константам инструментов;
    названные файлы и флаги существуют.

Stdlib. Запуск:  python tools\\test_dji_area_retained_footprint_release_runbook.py
"""

import io
import json
import os
import re
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tools import check_db_lock  # noqa: E402
from tools import dji_area_control_acceptance as acceptance  # noqa: E402
from tools import dji_area_footprint_calibration as evaluator  # noqa: E402
from tools import dji_area_holdout as holdout  # noqa: E402
from tools import dji_area_raw_guard as raw_guard  # noqa: E402
from tools import dji_area_recalc as recalc  # noqa: E402

RUNBOOK = os.path.join(REPO_ROOT, 'docs',
                       'DJI_AREA_RETAINED_FOOTPRINT_RELEASE_RUNBOOK.md')
HOLDOUT_RUNBOOK = os.path.join(REPO_ROOT, 'docs',
                               'DJI_AREA_SIMPLIFY_001_RUNBOOK.md')
ORACLE = os.path.join(REPO_ROOT, 'docs',
                      'DJI_AREA_SEPTEMBER_2026_RETAINED_FOOTPRINT_ORACLE.json')
PY_PATH = r'C:\Program Files\Python314\python.exe'
SERVICES = "$services = @('TransportReport', 'TransportBot', 'TransportBot003')"


def read(path=RUNBOOK):
    with io.open(path, encoding='utf-8') as fh:
        return fh.read().replace('\r\n', '\n')


def blocks():
    return re.findall(r'```powershell\n(.*?)```', read(), re.S)


def one(marker):
    found = [b for b in blocks() if marker in b]
    if len(found) != 1:
        raise AssertionError('%d block(s) carry %r' % (len(found), marker))
    return found[0]


def block_r1():
    return one("Write-Host 'PRE-APPLY PASS'")


def block_r2():
    return one("Write-Host 'POST-APPLY PASS'")


def block_r3():
    return one("Write-Host 'HISTORICAL APPLY PASS'")


def oracle():
    with io.open(ORACLE, encoding='utf-8') as fh:
        return json.load(fh)


def pos(block, needle):
    index = block.find(needle)
    if index < 0:
        raise AssertionError('%r is not in the block' % needle)
    return index


def assert_order(case, block, order):
    places = [pos(block, needle) for needle in order]
    case.assertEqual(places, sorted(places), order)


def parser_flags(parser):
    return set(opt for action in parser._actions
               for opt in action.option_strings)


class EveryBlock(unittest.TestCase):

    def test_exactly_the_three_blocks(self):
        self.assertEqual(len(blocks()), 3)
        self.assertEqual(len({block_r1(), block_r2(), block_r3()}), 3)

    def test_a_throw_stops_the_whole_paste_not_one_line(self):
        for block in blocks():
            lines = [ln for ln in block.strip().splitlines() if ln.strip()]
            self.assertEqual(lines[0], '& {')
            self.assertEqual(lines[-1], '}')
            depth = 0
            for number, line in enumerate(lines):
                depth += line.count('{') - line.count('}')
                if number < len(lines) - 1:
                    self.assertGreater(depth, 0, line)
            self.assertEqual(depth, 0)

    def test_no_placeholders_no_and_and_ascii_only(self):
        for block in blocks():
            self.assertIsNone(re.search(r'<[^>\n]{1,60}>', block), block[:80])
            self.assertNotIn('...', block)
            self.assertNotIn('TODO', block)
            self.assertNotIn('&&', block)
            self.assertEqual(sorted(set(ch for ch in block if ord(ch) > 127)),
                             [], block[:60])

    def test_python_is_called_through_ampersand_and_checked(self):
        for block in blocks():
            self.assertIn("$py       = '%s'" % PY_PATH, block)
            lines = block.splitlines()
            calls = 0
            for number, line in enumerate(lines):
                stripped = line.strip()
                if re.search(r'(?<!& )\$py (tools\\|-m )', stripped) \
                        and not stripped.startswith('if ('):
                    self.fail('python without & : %s' % stripped)
                if not re.match(r'& \$py (tools\\|-m )', stripped):
                    continue
                calls += 1
                self.assertIn('$LASTEXITCODE', lines[number + 1],
                              '%s\n  -> %s' % (stripped, lines[number + 1]))
            self.assertGreater(calls, 5)

    def test_every_throw_is_guarded_on_its_own_line(self):
        for block in blocks():
            for line in block.splitlines():
                if 'throw' in line:
                    self.assertRegex(line.strip(), r'^if \(', line)

    def test_git_is_paged_off(self):
        for block in blocks():
            for line in block.splitlines():
                if re.search(r'\bgit\b.*\blog\b', line):
                    self.assertIn('--no-pager', line)

    def test_it_is_production_and_only_production(self):
        for block in blocks():
            self.assertIn("$expectedHost = 'srv-yoqsh'", block)
            self.assertIn("$prod     = 'C:\\transport-report'", block)
            self.assertIn("if ($prod -ne 'C:\\transport-report') { throw",
                          block)
            self.assertIn(SERVICES, block)
            for word in ('transport-report-staging', 'TransportReportStaging',
                         ':5051', 'VehicleSoft_Area_Staging'):
                self.assertNotIn(word, block)
            for match in re.finditer(r'transport-report[\w-]*', block):
                self.assertEqual(match.group(0), 'transport-report')
            for match in re.finditer(r':(\d{4})\b', block):
                self.assertEqual(match.group(1), '5050')

    def test_the_host_is_checked_before_anything_else(self):
        for block in blocks():
            first = pos(block, 'if ((hostname) -ne $expectedHost) { throw')
            for contact in ('& git', '& $py', 'Stop-Service'):
                self.assertLess(first, pos(block, contact), contact)

    def test_services_stop_only_inside_try_and_come_back_in_finally(self):
        for block in blocks():
            self.assertNotIn('Start-Service', block)
            self.assertLess(pos(block, 'try {'), pos(block, 'Stop-Service'))
            self.assertLess(pos(block, '} finally {'),
                            pos(block, 'Restart-Service -Name $name'))
            self.assertLess(pos(block, 'Stop-Service'),
                            pos(block, '} finally {'))
            self.assertIn('START THEM BY HAND', block)
            self.assertIn('if ($lock -eq 2) { throw', block)
            self.assertLess(pos(block, 'Copy-Item -LiteralPath $db'),
                            pos(block, 'tools\\dji_area_recalc.py'))

    def test_the_historical_oracle_is_never_read(self):
        for block in blocks():
            self.assertNotIn('DJI_AREA_SEPTEMBER_2026_ORACLE.json', block)
            self.assertIn("$oracle   = 'docs\\DJI_AREA_SEPTEMBER_2026_"
                          "RETAINED_FOOTPRINT_ORACLE.json'", block)

    def test_every_named_repo_file_exists(self):
        named = set()
        for block in blocks():
            named.update(re.findall(r'(tools\\\w+\.py)', block))
            named.update(re.findall(r'(docs\\\w+\.(?:json|md))', block))
            for modules in re.findall(r'-m unittest ([\w. ]+)', block):
                named.update(m.replace('.', os.sep) + '.py'
                             for m in modules.split())
        self.assertGreater(len(named), 6)
        for rel in sorted(named):
            self.assertTrue(os.path.exists(os.path.join(
                REPO_ROOT, rel.replace('\\', os.sep))), rel)

    def test_every_flag_a_block_passes_exists(self):
        known = {
            'dji_area_recalc.py': parser_flags(recalc.build_parser()),
            'dji_area_footprint_calibration.py':
                parser_flags(evaluator.build_parser()),
            'dji_area_holdout.py': None,
        }
        # Приёмка, сторож RAW и проверка блокировки строят парсер внутри
        # main(): флаги берутся из их собственного --help.
        for module, name in ((acceptance, 'dji_area_control_acceptance.py'),
                             (raw_guard, 'dji_area_raw_guard.py'),
                             (check_db_lock, 'check_db_lock.py')):
            out = io.StringIO()
            saved = sys.stdout
            sys.stdout = out
            try:
                module.main(['--help'])
            except SystemExit:
                pass
            finally:
                sys.stdout = saved
            known[name] = set(re.findall(r'(--[a-z][a-z-]+)', out.getvalue()))
        for block in blocks():
            for line in block.splitlines():
                match = re.search(r'tools\\(\w+\.py)(.*)$', line)
                if not match or known.get(match.group(1)) is None:
                    continue
                for flag in re.findall(r'(--[a-z][a-z-]+)', match.group(2)):
                    self.assertIn(flag, known[match.group(1)], line)


class ThePin(unittest.TestCase):

    def test_the_fingerprint_is_the_real_one(self):
        real = holdout.code_fingerprint()
        for block in blocks():
            self.assertIn("$ExpectedFingerprint = '%s'" % real, block)

    def test_the_tag_is_the_one_the_other_runbooks_pin(self):
        tags = set(re.findall(r"\$ExpectedTag = '([\w.-]+)'",
                              read(HOLDOUT_RUNBOOK)))
        self.assertEqual(len(tags), 1)
        for block in blocks():
            self.assertIn("$ExpectedTag = '%s'" % tags.pop(), block)
            tags = set(re.findall(r"\$ExpectedTag = '([\w.-]+)'",
                                  read(HOLDOUT_RUNBOOK)))

    def test_the_strict_fingerprint_parser(self):
        strict = (r"Select-String -Pattern '^\s*CODE FINGERPRINT\s*:\s*"
                  r"([0-9a-f]{64})\s*$'")
        for block in blocks():
            self.assertIn(strict, block)

    def test_the_revision_is_proven_before_a_service_stops(self):
        for block in blocks():
            for guard in ('"refs/tags/${ExpectedTag}:refs/tags/${ExpectedTag}"',
                          'if (-not $pinned) { throw',
                          'if ($fpFound.Count -ne 1) { throw',
                          'if ($fp -ne $ExpectedFingerprint) { throw'):
                self.assertLess(pos(block, guard), pos(block, 'Stop-Service'),
                                guard)

    def test_r1_runs_exactly_the_tag_and_r2_r3_a_tree_that_contains_it(self):
        r1 = block_r1()
        self.assertIn('if ($headSha -ne $pinned) { throw', r1)
        self.assertIn('if ($dirty.Count -gt 0) { throw', r1)
        for block in (block_r2(), block_r3()):
            self.assertIn('merge-base --is-ancestor $pinned $headSha', block)
            self.assertIn('if ($changed.Count -gt 0) { throw', block)
            self.assertLess(pos(block, 'merge-base --is-ancestor'),
                            pos(block, 'Stop-Service'))


class BlockR1(unittest.TestCase):

    def test_it_writes_nothing_and_proves_it(self):
        block = block_r1()
        self.assertNotIn('--apply', block)
        assert_order(self, block, [
            'Stop-Service', 'tools\\check_db_lock.py',
            'Copy-Item -LiteralPath $db', '$before = (Get-FileHash',
            'tools\\dji_area_raw_guard.py --db $db --save $rawSnap',
            'tools\\dji_area_footprint_calibration.py --db $db',
            'tools\\dji_area_recalc.py --db $db --from $from --to $to '
            '--dry-run',
            'tools\\dji_area_control_acceptance.py --db $db --oracle $oracle '
            '--phase pre-apply',
            '$after = (Get-FileHash',
            'if ($before -ne $after) { throw',
            'if ($ev -ne 0) { throw', 'if ($acc -ne 0) { throw',
            '} finally {', 'Restart-Service'])

    def test_the_dry_run_rows_feed_the_acceptance(self):
        block = block_r1()
        self.assertIn("--json (Join-Path $out 'pre.json') --rows (Join-Path "
                      "$out 'pre_rows.json')", block)
        self.assertIn("--recalc-summary (Join-Path $out 'pre.json') "
                      "--recalc-rows (Join-Path $out 'pre_rows.json')", block)
        self.assertIn('--evaluate-rule', block)

    def test_the_raw_snapshot_is_taken_once(self):
        self.assertIn('if (-not (Test-Path -LiteralPath $rawSnap)) { & $py '
                      'tools\\dji_area_raw_guard.py --db $db --save $rawSnap }',
                      block_r1())


class BlockR2(unittest.TestCase):

    def test_it_needs_a_passed_r1(self):
        block = block_r2()
        for guard in ('if (-not (Test-Path -LiteralPath $rawSnap)) { throw',
                      'if (-not (Test-Path -LiteralPath $r1Verdict)) { throw',
                      "if (($r1.verdict -ne 'PASS') -or ($r1.phase -ne "
                      "'pre-apply')) { throw"):
            self.assertLess(pos(block, guard), pos(block, 'Stop-Service'),
                            guard)

    def test_pre_apply_again_then_exactly_the_rewrites_then_post_apply(self):
        assert_order(self, block_r2(), [
            'Stop-Service', 'Copy-Item -LiteralPath $db',
            '--dry-run --quiet --json (Join-Path $out \'pre.json\')',
            '--phase pre-apply', 'if ($pre -ne 0) { throw',
            '--apply --quiet --json (Join-Path $out \'apply.json\') @idArgs',
            '--dry-run --quiet --json (Join-Path $out \'second.json\')',
            '--phase post-apply --recalc-summary (Join-Path $out '
            '\'second.json\') --apply-summary (Join-Path $out '
            '\'apply.json\')',
            'tools\\dji_area_raw_guard.py --db $db --compare $rawSnap',
            'if ($post -ne 0) { throw', 'if ($raw -ne 0) { throw',
            '} finally {', 'Restart-Service', "($site + '/login')",
            "($site + '/drones/area-control')"])

    def test_the_apply_is_targeted_by_the_oracle_not_by_the_text(self):
        block = block_r2()
        applies = [line for line in block.splitlines()
                   if re.search(r'--apply(?![-\w])', line)]
        self.assertEqual(len(applies), 1)
        self.assertIn('@idArgs', applies[0])
        self.assertIn('ConvertFrom-Json).transition.expected_rewrites)',
                      block)
        for fid in oracle()['transition']['expected_rewrites']:
            self.assertNotIn(str(fid), block)


class BlockR3(unittest.TestCase):

    def test_the_approved_list_is_the_production_evaluation(self):
        block = block_r3()
        literal = re.search(r'\$approved = @\(([\d, ]+)\)', block).group(1)
        approved = [int(x) for x in literal.split(',')]
        self.assertEqual(sorted(approved), sorted(
            oracle()['production_evaluation']['final_candidates']))
        self.assertEqual(len(approved), 28)

    def test_nothing_is_recalculated_before_the_list_is_proven(self):
        block = block_r3()
        self.assertLess(pos(block, 'if (($r2v.verdict -ne \'PASS\')'),
                        pos(block, 'Stop-Service'))
        assert_order(self, block, [
            '--evaluate-rule', 'Compare-Object',
            'if ($drift.Count -gt 0) { throw',
            'tools\\dji_area_recalc.py --db $db --from $from3 --to $to3 '
            '--dry-run',
            'calc_writes.would_write -ne $todo.Count)) { throw',
            '--apply --quiet', 'calc_writes.new -ne $todo.Count) { throw',
            "--json (Join-Path $out 'second.json') @idArgs",
            'calc_writes.unchanged -ne $todo.Count) { throw',
            "--out (Join-Path $out 'evaluation_after') --evaluate-rule",
            'tools\\dji_area_raw_guard.py --db $db --compare $rawSnap3',
            'if ($ev2 -ne 0) { throw', 'if ($raw -ne 0) { throw',
            '} finally {'])

    def test_there_is_no_period_wide_recalculation(self):
        for line in block_r3().splitlines():
            if 'tools\\dji_area_recalc.py' in line:
                self.assertIn('@idArgs', line)


class TheTextAgreesWithTheOracle(unittest.TestCase):

    def test_the_acceptance_table_is_the_oracle(self):
        doc = oracle()
        expected = doc['expected']
        text = read()
        for key in ('raw_ha', 'excluded_ha', 'after_ha', 'review_ha'):
            self.assertIn('%.4f' % expected[key], text, key)
        for key in ('records', 'excluded_records', 'review_records',
                    'structural_candidates', 'chains_shown',
                    'proven_structural', 'proven_by_control_only',
                    'review_application_with_flat_counter'):
            self.assertRegex(text, r'(?<!\d)%d(?!\d)' % expected[key], key)
        writes = len(doc['transition']['expected_rewrites'])
        self.assertIn('`{"unchanged": %d, "would_write": %d}`'
                      % (expected['records'] - writes, writes), text)
        self.assertIn('`{"unchanged": %d}`' % expected['records'], text)
        self.assertIn('`{"new": %d}`' % writes, text)
        for fid in doc['transition']['expected_rewrites'] \
                + doc['transition']['must_stay_review']:
            self.assertIn(str(fid), text)

    def test_the_exit_codes_are_the_tools_own(self):
        text = read()
        self.assertEqual((acceptance.EXIT_PASS, acceptance.EXIT_USAGE,
                          acceptance.EXIT_NO_DATABASE, acceptance.EXIT_FAIL),
                         (0, 1, 2, 3))
        self.assertEqual((evaluator.EXIT_OK, evaluator.EXIT_DB_CHANGED,
                          evaluator.EXIT_CONTROL_VIOLATED), (0, 3, 4))
        self.assertEqual((raw_guard.EXIT_OK, raw_guard.EXIT_VIOLATED), (0, 3))
        for phrase in ('приёмка — 0 PASS, 1 ошибка аргументов, 2 базы нет, '
                       '3 FAIL', 'оценщик — 0, 3 база изменилась во время '
                       'чтения, 4 нарушен контроль', '3 RAW изменён либо '
                       'billable не пуст'):
            self.assertIn(phrase, text.replace('\n', ' '))

    def test_the_historical_oracle_stays_named_as_history(self):
        text = read()
        self.assertIn('`docs/DJI_AREA_SEPTEMBER_2026_ORACLE.json`', text)
        self.assertIn('не меняется ни на байт', text)


if __name__ == '__main__':
    unittest.main()
