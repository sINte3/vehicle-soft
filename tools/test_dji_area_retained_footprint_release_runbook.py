# -*- coding: utf-8 -*-
"""Проверка блоков docs/DJI_AREA_RETAINED_FOOTPRINT_RELEASE_RUNBOOK.md.

[REASON]: это первые блоки проекта, которые останавливают службы PRODUCTION,
пишут в его базу и двигают его код. Свойства ниже ломаются молча -- ни
`py_compile`, ни глаз при чтении диффа их не ловят:

  * блок вставляется вербатим: `& { ... }`, только ASCII, плейсхолдеров и `&&`
    нет, python с пробелом в пути зовётся через `&`, каждый `throw` стоит за
    проверкой на своей строке, за каждым вызовом инструмента читается
    `$LASTEXITCODE`;
  * хост, каталог, база, службы и порт -- ровно production, и площадка не
    названа нигде: блок, перепутавший окружение, должен остановиться, а не
    работать «почти правильно»;
  * пин модели (тег rc1 и отпечаток) тот же, что в остальных ранбуках,
    отпечаток равен настоящему, и ревизия доказана до первой остановки
    службы; R1 и R2 исполняют ровно тег ВЫПУСКА, и он содержит тег модели;
  * R1 и R2 пишут только через `tools/dji_area_retained_release_closeout.py`
    (он сам не пишет без проверенной копии), R1 никогда не применяет переход;
    исторический оракул блоками не читается;
  * R2 двигает код production только `git merge --ff-only` ровно на коммит
    тега выпуска: без pull, reset, clean и checkout, прежний HEAD -- предок,
    дерево чистое, миграций в дельте нет, ни один неотслеживаемый файл не
    пропал; службы поднимаются в `finally`;
  * R3 применяет только утверждённый список -- тот же, что в оракуле, -- и
    только если свежий оценщик называет ровно его; пересчёта периода целиком
    в нём нет;
  * числа и коды возврата в тексте равны оракулу и константам инструментов;
    названные файлы, подкоманды и флаги существуют.

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
from tools import dji_area_retained_release_closeout as closeout  # noqa: E402

RUNBOOK = os.path.join(REPO_ROOT, 'docs',
                       'DJI_AREA_RETAINED_FOOTPRINT_RELEASE_RUNBOOK.md')
HOLDOUT_RUNBOOK = os.path.join(REPO_ROOT, 'docs',
                               'DJI_AREA_SIMPLIFY_001_RUNBOOK.md')
ORACLE = os.path.join(REPO_ROOT, 'docs',
                      'DJI_AREA_SEPTEMBER_2026_RETAINED_FOOTPRINT_ORACLE.json')
PY_PATH = r'C:\Program Files\Python314\python.exe'
SERVICES = "$services = @('TransportReport', 'TransportBot', 'TransportBot003')"
CLOSEOUT = 'tools\\dji_area_retained_release_closeout.py'
RELEASE_TAG = 'dji-area-retained-release-closeout-002'


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


def subcommand_flags(parser):
    """{подкоманда: флаги} парсера с подкомандами."""
    out = {}
    for action in parser._actions:
        for name, sub in (getattr(action, 'choices', None) or {}).items():
            if hasattr(sub, '_actions'):
                out[name] = parser_flags(sub)
    return out


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

    def test_nothing_is_written_without_a_copy_of_the_database(self):
        # [REASON]: R1 и R2 пишут только через инструмент закрытия, и копию
        # делает он сам (backup_transport_db.py + integrity_check + сверка с
        # живой базой, иначе ни одной записи -- это держит его самотест).
        # Блоку остаётся передать каталог копий и не писать мимо инструмента.
        for block in blocks():
            if CLOSEOUT in block:
                self.assertIn('--backup-dir $backup', block)
                self.assertNotIn('tools\\dji_area_recalc.py', block)
            else:
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
        commands = subcommand_flags(closeout.build_parser())
        seen = 0
        for block in blocks():
            for line in block.splitlines():
                match = re.search(r'tools\\(\w+\.py)(.*)$', line)
                if not match:
                    continue
                flags = re.findall(r'(--[a-z][a-z-]+)', match.group(2))
                if match.group(1) == 'dji_area_retained_release_closeout.py':
                    # Подкоманда -- первое слово после имени файла.
                    command = match.group(2).split()[0]
                    self.assertIn(command, commands, line)
                    for flag in flags:
                        self.assertIn(flag, commands[command], line)
                    seen += 1
                    continue
                if known.get(match.group(1)) is None:
                    continue
                for flag in flags:
                    self.assertIn(flag, known[match.group(1)], line)
        self.assertEqual(seen, 2)


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

    def test_r1_and_r2_run_exactly_the_release_that_holds_the_model(self):
        # [REASON]: тег rc1 -- пин проверенной МОДЕЛИ, и он не
        # пересоздаётся. Инструмента закрытия в нём нет, поэтому R1 и R2
        # исполняют тег ВЫПУСКА -- и только если тот содержит тег модели.
        for block in (block_r1(), block_r2()):
            self.assertIn("$ReleaseTag  = '%s'" % RELEASE_TAG, block)
            for guard in ('"refs/tags/${ReleaseTag}:refs/tags/${ReleaseTag}"',
                          'if (-not $release) { throw',
                          'merge-base --is-ancestor $pinned $release',
                          'if ($headSha -ne $release) { throw',
                          'if ($dirty.Count -gt 0) { throw'):
                self.assertLess(pos(block, guard), pos(block, 'Stop-Service'),
                                guard)
        self.assertNotEqual(RELEASE_TAG, re.search(
            r"\$ExpectedTag = '([\w.-]+)'", block_r1()).group(1))

    def test_r3_runs_a_production_tree_that_contains_the_model(self):
        block = block_r3()
        self.assertIn('merge-base --is-ancestor $pinned $headSha', block)
        self.assertIn('if ($changed.Count -gt 0) { throw', block)
        self.assertLess(pos(block, 'merge-base --is-ancestor'),
                        pos(block, 'Stop-Service'))


class BlockR1(unittest.TestCase):

    def test_it_writes_only_through_the_tool_and_never_the_transition(self):
        block = block_r1()
        self.assertNotIn('--apply', block)
        self.assertNotIn('tools\\dji_area_recalc.py', block)
        self.assertNotIn('merge --ff-only', block)
        assert_order(self, block, [
            'Stop-Service', 'tools\\check_db_lock.py',
            CLOSEOUT + ' r1 --db $db --oracle $oracle --out $out '
            '--backup-dir $backup --raw-snapshot $rawSnap '
            '--baseline-root $prod',
            '$r1 = $LASTEXITCODE', 'if ($r1 -ne 0) { throw',
            '} finally {', 'Restart-Service', "($site + '/login')",
            "($site + '/drones/area-control')", "Write-Host 'PRE-APPLY PASS'"])

    def test_the_tool_is_tested_before_a_service_stops(self):
        for block in (block_r1(), block_r2()):
            self.assertLess(pos(block, '& $py tools\\test_dji_area_retained_'
                                       'release_closeout.py'),
                            pos(block, 'Stop-Service'))

    def test_the_code_production_runs_now_is_the_baseline(self):
        # R1 идёт ДО деплоя: нормализованные строки обязан видеть
        # `unchanged` код, который сейчас стоит на production. В R2 это уже
        # тот же код, что и у инструмента.
        self.assertIn('--baseline-root $prod', block_r1())
        self.assertNotIn('--baseline-root', block_r2())

    def test_one_raw_snapshot_serves_r1_and_r2(self):
        snap = "$rawSnap  = 'C:\\VehicleSoft_Retained_Footprint_Release\\" \
               "raw_before.json'"
        for block in (block_r1(), block_r2()):
            self.assertIn(snap, block)
            self.assertIn('--raw-snapshot $rawSnap', block)

    def test_an_earlier_run_is_kept_not_removed(self):
        for block in (block_r1(), block_r2()):
            self.assertIn("if (Test-Path -LiteralPath $out) { Move-Item "
                          "-LiteralPath $out -Destination ($out + '_before_' "
                          "+ $stamp) }", block)
            for line in block.splitlines():
                if 'Remove-Item' in line:
                    self.assertIn('Remove-Item -LiteralPath $src -Recurse',
                                  line)


class BlockR2(unittest.TestCase):

    def test_it_needs_a_passed_r1_of_the_same_model(self):
        block = block_r2()
        for guard in ('if (-not (Test-Path -LiteralPath $rawSnap)) { throw',
                      'if (-not (Test-Path -LiteralPath $r1Verdict)) { throw',
                      "if (($r1.verdict -ne 'PASS') -or ($r1.phase -ne "
                      "'r1')) { throw",
                      'if ($r1.code_fingerprint -ne $ExpectedFingerprint) '
                      '{ throw'):
            self.assertLess(pos(block, guard), pos(block, 'Stop-Service'),
                            guard)
        self.assertIn("$r1Verdict = 'C:\\VehicleSoft_Retained_Footprint_"
                      "Release\\closeout_r1\\closeout_verdict.json'", block)

    def test_the_deploy_is_a_controlled_fast_forward(self):
        block = block_r2()
        # Ни одна команда, способная потерять файлы или историю production.
        self.assertIsNone(re.search(
            r'\bgit\b[^\n]*\b(pull|reset|clean|stash|rebase|push|switch|'
            r'restore)\b', block))
        self.assertIsNone(re.search(r'git -C \$prod checkout', block))
        for line in block.splitlines():
            if 'Remove-Item' in line or 'Move-Item' in line:
                self.assertNotIn('$prod', line)
                self.assertNotIn('$db', line)
        merges = [ln for ln in block.splitlines() if 'merge --ff-only' in ln
                  and not ln.strip().startswith('if (')]
        self.assertEqual([ln.strip() for ln in merges],
                         ['& git -C $prod merge --ff-only $release'])
        for guard in ('if ($prodRelease -ne $release) { throw',
                      'merge-base --is-ancestor $headBefore $release',
                      'if ($changed.Count -gt 0) { throw',
                      "Where-Object { $_ -match '^migrate_' }",
                      'if ($migrations.Count -gt 0) { throw'):
            self.assertLess(pos(block, guard), pos(block, 'Stop-Service'),
                            guard)

    def test_the_deploy_happens_with_the_services_down_and_is_verified(self):
        assert_order(self, block_r2(), [
            'Stop-Service', 'tools\\check_db_lock.py', '$untrackedBefore = ',
            '& git -C $prod merge --ff-only $release',
            'if ($headAfter -ne $release) { throw',
            '$untrackedAfter = ', 'if ($lost.Count -gt 0) { throw',
            'Set-Location $prod', '$fpProd = ',
            CLOSEOUT + ' r2 --db $db --oracle $oracle --out $out '
            '--backup-dir $backup --raw-snapshot $rawSnap '
            '--r1-verdict $r1Verdict',
            '$r2 = $LASTEXITCODE', 'if ($r2 -ne 0) { throw',
            '} finally {', 'Restart-Service', "($site + '/login')",
            "($site + '/drones/area-control')",
            "Write-Host 'POST-APPLY PASS'"])

    def test_the_transition_is_applied_by_the_tool_from_the_oracle(self):
        # Номера перехода блок не несёт: инструмент берёт их из файла
        # оракула и применяет только после нового PRE-APPLY.
        block = block_r2()
        self.assertNotIn('--apply', block)
        doc = oracle()
        for fid in (doc['transition']['expected_rewrites']
                    + doc['transition']['must_stay_review']):
            self.assertNotIn(str(fid), block)


class BlockR3(unittest.TestCase):

    def test_the_approved_list_is_the_production_evaluation(self):
        block = block_r3()
        literal = re.search(r'\$approved = @\(([\d, ]+)\)', block).group(1)
        approved = [int(x) for x in literal.split(',')]
        self.assertEqual(sorted(approved), sorted(
            oracle()['production_evaluation']['final_candidates']))
        self.assertEqual(len(approved), 28)

    def test_it_needs_the_post_apply_verdict_of_r2(self):
        block = block_r3()
        self.assertIn("$r2Verdict = 'C:\\VehicleSoft_Retained_Footprint_"
                      "Release\\closeout_r2\\post_apply\\"
                      "area_control_acceptance.json'", block)

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
        self.assertIn('`{"unchanged": %d}`' % writes, text)
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
        self.assertEqual((closeout.EXIT_PASS, closeout.EXIT_USAGE,
                          closeout.EXIT_NO_DATABASE, closeout.EXIT_STOP),
                         (0, 1, 2, 3))
        for phrase in ('инструмент закрытия — 0 PASS, 1 ошибка аргументов, '
                       '2 базы нет, 3 STOP',
                       'приёмка — 0 PASS, 1 ошибка аргументов, 2 базы нет, '
                       '3 FAIL', 'оценщик — 0, 3 база изменилась во время '
                       'чтения, 4 нарушен контроль', '3 RAW изменён либо '
                       'billable не пуст'):
            self.assertIn(phrase, text.replace('\n', ' '))

    def test_the_historical_oracle_stays_named_as_history(self):
        text = read()
        self.assertIn('`docs/DJI_AREA_SEPTEMBER_2026_ORACLE.json`', text)
        self.assertIn('не меняется ни на байт', text)

    def test_the_release_tag_is_the_one_the_order_names(self):
        text = ' '.join(read().split())
        self.assertIn('`%s`' % RELEASE_TAG, text)
        # Тег модели не пересоздаётся: новый тег -- пин выпуска.
        self.assertIn('`dji-area-retained-footprint-001-rc1` не трогается',
                      text)


if __name__ == '__main__':
    unittest.main()
