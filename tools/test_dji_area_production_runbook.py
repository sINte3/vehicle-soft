# -*- coding: utf-8 -*-
"""Проверка блоков docs/DJI_AREA_PRODUCTION_RUNBOOK.md.

[REASON]: блоки вставляются в консоль ВЕРБАТИМ; один останавливает службу
площадки и переключает её ревизию, другой ходит в кабинет DJI. Свойства ниже
ломаются молча -- ни `py_compile`, ни глаз при чтении диффа их не ловят:

  * плейсхолдеров нет, `&&` нет, python с пробелом в пути зовётся через `&`,
    каждый `throw` стоит за проверкой на своей строке, блок целиком обёрнут в
    `& { ... }`, внутри только ASCII;
  * production не назван нигде: ни каталог, ни служба, ни порт, ни задача;
  * пин ревизии (тег и отпечаток) тот же, что в holdout-ранбуке, и отпечаток
    равен НАСТОЯЩЕМУ отпечатку кода -- иначе пин тихо устаревает;
  * блок A принимает сентябрь ДО переключения ревизии и не разворачивает ничего
    при провале приёмки; базу он только читает и доказывает это хешем;
  * блок W доказывает, что приёмник -- площадка, и что площадка отвечает на
    манифест, ДО первого обращения к кабинету; порог остановки стоит на обоих
    вызовах и не выше 50; сбора по всему парку в блоке нет;
  * блок S делает копию до записи, требует `unchanged` от второго пересчёта,
    сверяет RAW со снимком блока A и поднимает службу в `finally`;
  * числа приёмки в тексте равны числам оракула, коды возврата -- константам;
  * названные файлы репозитория существуют.

Stdlib. Запуск:  python tools\\test_dji_area_production_runbook.py
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

from tools import dji_area_control_acceptance as acceptance  # noqa: E402
from tools import dji_area_daily as daily  # noqa: E402
from tools import dji_area_holdout as holdout  # noqa: E402
from tools import dji_area_raw_guard as raw_guard  # noqa: E402

RUNBOOK = os.path.join(REPO_ROOT, 'docs', 'DJI_AREA_PRODUCTION_RUNBOOK.md')
HOLDOUT_RUNBOOK = os.path.join(REPO_ROOT, 'docs',
                               'DJI_AREA_SIMPLIFY_001_RUNBOOK.md')
ORACLE = os.path.join(REPO_ROOT, 'docs', 'DJI_AREA_SEPTEMBER_2026_ORACLE.json')
BRANCH = 'claude/dji-area-productionization-001'
PY_PATH = r'C:\Program Files\Python314\python.exe'


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


def block_a():
    return one('NOTHING WAS DEPLOYED')


def block_w():
    return one('--skip-recalc')


def block_s():
    return one('--expect-unchanged')


def block_rollback():
    return one('ROLLED BACK TO')


def block_repeat():
    return one('--days 3\nWrite-Host')


def pos(block, needle):
    index = block.find(needle)
    if index < 0:
        raise AssertionError('%r is not in the block' % needle)
    return index


class EveryBlock(unittest.TestCase):

    def test_every_block_is_recognised_and_none_is_a_stray(self):
        self.assertEqual(len(blocks()), 5)
        named = [block_a(), block_w(), block_s(), block_rollback(),
                 block_repeat()]
        self.assertEqual(len(set(named)), 5)

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

    def test_no_placeholders_survive(self):
        for block in blocks():
            self.assertIsNone(re.search(r'<[^>\n]{1,60}>', block), block[:80])
            self.assertNotIn('...', block)
            self.assertNotIn('TODO', block)

    def test_powershell_has_no_and_and(self):
        for block in blocks():
            self.assertNotIn('&&', block)

    def test_the_console_sees_ascii_only(self):
        for block in blocks():
            bad = sorted(set(ch for ch in block if ord(ch) > 127))
            self.assertEqual(bad, [], block[:60])

    def test_python_is_called_through_ampersand(self):
        callers = [b for b in blocks() if '$py' in b]
        # Блок отката python не зовёт вовсе: ему нужны только git и служба.
        self.assertEqual(len(callers), len(blocks()) - 1)
        for block in callers:
            self.assertIn("'%s'" % PY_PATH, block)
            call = re.compile(r'(\$py|\$cpy)\s+(?=(-m\b|\w+\\))')
            calls = list(call.finditer(block))
            self.assertTrue(calls)
            for match in calls:
                self.assertTrue(block[:match.start()].endswith('& '),
                                block[max(0, match.start() - 60):
                                      match.end() + 30])

    def test_every_throw_is_guarded_on_its_own_line(self):
        for block in blocks():
            for line in block.splitlines():
                if 'throw' in line:
                    self.assertRegex(line.strip(), r'^if \(', line)

    def test_every_native_call_that_can_fail_is_checked(self):
        # [REASON]: при `$ErrorActionPreference = 'Continue'` упавший python
        # блок не останавливает. За каждым вызовом инструмента следующая
        # строка обязана прочитать `$LASTEXITCODE` -- сразу либо в переменную.
        for block in blocks():
            lines = block.splitlines()
            for number, line in enumerate(lines):
                stripped = line.strip()
                if not re.match(r'& \$c?py (tools\\|-m )', stripped):
                    continue
                following = lines[number + 1].strip()
                self.assertIn('$LASTEXITCODE', following,
                              '%s\n  -> %s' % (stripped, following))

    def test_production_is_not_named_anywhere(self):
        for block in blocks():
            self.assertNotIn(':5050', block)
            self.assertNotIn('DroneCollectorDaily', block)
            for match in re.finditer(r'transport-report[\w-]*', block):
                self.assertEqual(match.group(0), 'transport-report-staging')
            for match in re.finditer(r"'(TransportReport\w*)'", block):
                self.assertEqual(match.group(1), 'TransportReportStaging')

    def test_the_service_is_restarted_never_just_started(self):
        for block in blocks():
            self.assertNotIn('Start-Service', block)
            if 'Stop-Service' in block:
                self.assertLess(pos(block, '} finally {'),
                                pos(block, 'Restart-Service -Name $service'))
                self.assertIn('START IT BY HAND', block)

    def test_git_is_paged_off(self):
        for block in blocks():
            for line in block.splitlines():
                if re.search(r'\bgit\b.*\blog\b', line):
                    self.assertIn('--no-pager', line)

    def test_every_repo_file_the_blocks_run_is_in_the_worktree(self):
        named = set()
        for block in blocks():
            named.update(re.findall(r'(tools\\\w+\.py)', block))
            named.update(re.findall(r'(docs\\\w+\.(?:json|md))', block))
            for modules in re.findall(r'-m unittest ([\w. ]+)', block):
                named.update(m.replace('.', os.sep) + '.py'
                             for m in modules.split())
            named.update(m.replace('.', os.sep) + '.py' for m in re.findall(
                r'-m (drone_collector\.\w+)', block))
        self.assertGreater(len(named), 8)
        for rel in sorted(named):
            self.assertTrue(os.path.exists(os.path.join(
                REPO_ROOT, rel.replace('\\', os.sep))), rel)


class ThePin(unittest.TestCase):

    def pinned(self):
        return (block_a(), block_w(), block_s())

    def test_the_fingerprint_is_the_real_one(self):
        real = holdout.code_fingerprint()
        for block in self.pinned():
            self.assertIn("$ExpectedFingerprint = '%s'" % real, block)

    def test_the_pin_is_the_same_one_the_holdout_runbook_names(self):
        other = read(HOLDOUT_RUNBOOK)
        tags = set(re.findall(r"\$ExpectedTag = '([\w.-]+)'", other))
        self.assertEqual(len(tags), 1)
        for block in self.pinned():
            self.assertIn("$ExpectedTag = '%s'" % tags.pop(), block)
            tags = set(re.findall(r"\$ExpectedTag = '([\w.-]+)'", other))
        self.assertIn("$branch  = '%s'" % BRANCH, other)
        self.assertIn("$branch  = '%s'" % BRANCH, block_a())
        self.assertIn("$branch   = '%s'" % BRANCH, block_w())

    def test_the_revision_is_proven_before_anything_is_touched(self):
        for block, first_contact in ((block_a(), 'Stop-Service'),
                                     (block_w(), '& $cpy '),
                                     (block_s(), 'Stop-Service')):
            for guard in ('rev-parse --verify --quiet "$ExpectedTag^{commit}"',
                          'if (-not $pinned) { throw',
                          'if ($headSha -ne $pinned) { throw',
                          'if ($fpFound.Count -ne 1) { throw',
                          'if ($fp -ne $ExpectedFingerprint) { throw'):
                self.assertLess(pos(block, guard), pos(block, first_contact),
                                guard)

    def test_an_unreviewed_working_tree_is_refused(self):
        for block in (block_a(), block_w()):
            self.assertLess(pos(block, 'if ($dirty.Count -gt 0) { throw'),
                            pos(block, '$fpFound'))
        for block in (block_a(), block_s()):
            self.assertLess(pos(block, 'if ($changed.Count -gt 0) { throw'),
                            pos(block, 'Stop-Service'))

    def test_the_fingerprint_parser_is_the_strict_one(self):
        strict = (r"Select-String -Pattern '^\s*CODE FINGERPRINT\s*:\s*"
                  r"([0-9a-f]{64})\s*$'")
        for block in self.pinned():
            self.assertIn(strict, block)


class BlockA(unittest.TestCase):

    def test_it_refuses_another_host_before_anything_else(self):
        block = block_a()
        self.assertLess(pos(block, 'if ((hostname) -ne $expectedHost) { throw'),
                        pos(block, '& git'))

    def test_september_is_accepted_before_the_revision_is_switched(self):
        block = block_a()
        order = ['Stop-Service', 'Copy-Item -LiteralPath $db',
                 '$before = (Get-FileHash', 'tools\\dji_area_recalc.py --db',
                 'tools\\dji_area_control_acceptance.py --db',
                 '$after = (Get-FileHash',
                 'if ($before -ne $after) { throw',
                 'if ($acc -ne 0) { throw', 'checkout --quiet --detach $sha',
                 '} finally {']
        places = [pos(block, needle) for needle in order]
        self.assertEqual(places, sorted(places), order)

    def test_the_acceptance_is_read_only_and_says_so(self):
        block = block_a()
        self.assertIn('--dry-run', block)
        self.assertNotIn('--apply', block)
        self.assertIn('--recalc-summary', block)
        self.assertIn('docs\\DJI_AREA_SEPTEMBER_2026_ORACLE.json', block)

    def test_the_rollback_memo_is_not_overwritten_by_a_second_run(self):
        block = block_a()
        self.assertIn('if ($beforeHead -ne $sha) { Set-Content -LiteralPath '
                      '$memo', block)
        self.assertEqual(block.count('Set-Content'), 1)
        self.assertIn("$memo    = 'C:\\VehicleSoft_Area_Staging\\"
                      "before_head.txt'", block_rollback())

    def test_staging_is_moved_forward_only(self):
        block = block_a()
        self.assertLess(pos(block, 'merge-base --is-ancestor $beforeHead $sha'),
                        pos(block, 'Stop-Service'))

    def test_the_new_endpoint_must_fail_closed_after_the_deploy(self):
        block = block_a()
        self.assertLess(pos(block, 'Restart-Service'),
                        pos(block, '/drones/api/area_capture_manifest'))
        self.assertIn('if ($noToken -ne 401) { throw', block)
        self.assertIn("if ($anonBody -notmatch 'vs-login-form') { throw", block)
        # Токена в блоке нет вовсе: проверяется именно отказ без него.
        self.assertNotIn('DRONE_API_TOKEN', block)

    def test_the_raw_snapshot_is_taken_once(self):
        block = block_a()
        self.assertIn('if (-not (Test-Path -LiteralPath $rawSnap)) { & $py '
                      'tools\\dji_area_raw_guard.py --db $db --save $rawSnap }',
                      block)


class BlockW(unittest.TestCase):

    def test_the_receiver_is_staging_before_the_collector_runs(self):
        block = block_w()
        for guard in ("$_.Line -notmatch ':5051'",
                      'if ($notStaging.Count -gt 0) { throw',
                      "$env:VEHICLE_SOFT_BASE_URL -notmatch ':5051'"):
            self.assertLess(pos(block, guard), pos(block, '& $cpy '), guard)

    def test_the_manifest_answers_before_dji_is_contacted(self):
        block = block_w()
        self.assertLess(pos(block, '-m drone_collector.area_manifest'),
                        pos(block, 'tools\\dji_area_daily.py'))
        self.assertLess(pos(block, 'if ($pre -ne 0) { throw'),
                        pos(block, 'tools\\dji_area_daily.py'))

    def test_the_stop_threshold_guards_both_calls_and_is_not_raised(self):
        block = block_w()
        value = int(re.search(r'\$StopAbove = (\d+)', block).group(1))
        self.assertLessEqual(value, daily.DEFAULT_STOP_ABOVE)
        self.assertEqual(block.count('--stop-above $StopAbove'), 2)
        self.assertIn('if ($rc -eq 4) { throw', block)

    def test_there_is_no_full_fleet_capture(self):
        block = block_w()
        # Сборщик зовётся только через цикл и клиент манифеста: прямого
        # `--sources` с периодом в блоке нет.
        self.assertNotIn('drone_collector.main', block)
        self.assertNotIn('--sources', block)
        self.assertIn('--skip-recalc --from $day --to $day', block)

    def test_the_window_is_one_day_not_three(self):
        block = block_w()
        self.assertIn('.AddHours(5).AddDays(-1)', block)
        self.assertNotIn('--days', block)

    def test_the_token_value_is_never_read_from_the_file_or_printed(self):
        block = block_w()
        # `.env` копируется целиком: значение токена из файла не вычитывается.
        self.assertNotIn('Get-Content -LiteralPath $envF', block)
        self.assertNotIn("Pattern '^\\s*DRONE_API_TOKEN", block)
        for line in block.splitlines():
            if 'Write-Host' in line or 'Write-Output' in line:
                self.assertNotIn('$savedToken', line)
                self.assertNotIn('$env:DRONE_API_TOKEN', line)
        # Переменная окружения трогается ровно дважды: сохранить и вернуть.
        self.assertEqual(block.count('$env:DRONE_API_TOKEN'), 2)

    def test_an_inherited_process_token_cannot_silence_the_copied_env(self):
        # [REASON]: живая квалификация 21.09.2026 остановилась ровно здесь. В
        # консоли рабочей машины остался DRONE_API_TOKEN от прежней работы, а
        # `drone_collector/config.py` НАМЕРЕННО отдаёт приоритет окружению
        # процесса над `.env` (докстринг `load_dotenv_file`: «Existing
        # environment variables win over the file»), чтобы задача планировщика
        # побеждала устаревший файл. Приоритет правильный и не меняется,
        # поэтому блок снимает переменную у себя -- и обязан вернуть её.
        block = block_w()
        order = ['$hadToken = Test-Path env:DRONE_API_TOKEN',
                 'if ($hadToken) { $savedToken = $env:DRONE_API_TOKEN }',
                 'if ($hadToken) { Remove-Item env:DRONE_API_TOKEN }',
                 'if ($hadToken -and (Test-Path env:DRONE_API_TOKEN)) { throw',
                 'try {',
                 '& $cpy ',
                 '} finally {',
                 'if ($hadToken) { $env:DRONE_API_TOKEN = $savedToken }']
        places = [pos(block, needle) for needle in order]
        self.assertEqual(places, sorted(places), order)

    def test_the_removal_is_proven_not_assumed(self):
        # Снять переменную мало: без проверки блок ушёл бы в кабинет с чужим
        # токеном и получил бы 401 уже после первого обращения к DJI.
        block = block_w()
        self.assertLess(
            pos(block, 'if ($hadToken -and (Test-Path env:DRONE_API_TOKEN)) '
                       '{ throw'),
            pos(block, '& $cpy '))

    def test_the_saved_value_does_not_outlive_the_block(self):
        block = block_w()
        tail = block[pos(block, '} finally {'):]
        self.assertIn('$savedToken = $null', tail)
        self.assertLess(tail.index('$env:DRONE_API_TOKEN = $savedToken'),
                        tail.index('$savedToken = $null'))

    def test_no_other_block_touches_the_token(self):
        # Правка узкая: она про рабочую машину, где живёт сборщик.
        mine = block_w()
        for block in blocks():
            if block != mine:
                self.assertNotIn('DRONE_API_TOKEN', block)

    def test_incomplete_sources_are_a_result_not_a_crash(self):
        block = block_w()
        self.assertIn('if (($rc -ne 0) -and ($rc -ne 5)) { throw', block)


class BlockS(unittest.TestCase):

    def test_it_runs_the_deployed_revision_only(self):
        block = block_s()
        self.assertIn('$headSha = (& git -C $staging rev-parse HEAD)', block)
        self.assertNotIn('git clone', block)

    def test_the_order_is_backup_recalc_twice_gate_guard_acceptance(self):
        block = block_s()
        order = ['Stop-Service', 'Copy-Item -LiteralPath $db',
                 "--recalc-only --days 3 --work-dir (Join-Path $out 'run1')",
                 '--recalc-only --days 3 --expect-unchanged',
                 'if ($second -eq 6) { throw',
                 'tools\\dji_area_idempotence_gate.py --summary',
                 'tools\\dji_area_raw_guard.py --db $db --compare $rawSnap',
                 'if ($raw -ne 0) { throw',
                 'tools\\dji_area_control_acceptance.py --db',
                 'if ($acc -ne 0) { throw', '} finally {']
        places = [pos(block, needle) for needle in order]
        self.assertEqual(places, sorted(places), order)

    def test_it_never_contacts_dji(self):
        block = block_s()
        for word in ('drone_collector', '--skip-recalc', '$cpy', '--sources'):
            self.assertNotIn(word, block)

    def test_it_needs_the_snapshot_block_a_wrote(self):
        block = block_s()
        self.assertLess(pos(block, 'if (-not (Test-Path -LiteralPath $rawSnap))'
                                   ' { throw'), pos(block, 'Stop-Service'))
        self.assertIn("$rawSnap = 'C:\\VehicleSoft_Area_Staging\\"
                      "raw_before.json'", block)
        self.assertIn("$rawSnap = 'C:\\VehicleSoft_Area_Staging\\"
                      "raw_before.json'", block_a())


class TheTextAgreesWithTheCode(unittest.TestCase):

    def test_the_acceptance_table_is_the_oracle(self):
        with io.open(ORACLE, encoding='utf-8') as fh:
            expected = json.load(fh)['expected']
        text = read()
        for key in ('raw_ha', 'excluded_ha', 'after_ha', 'review_ha'):
            self.assertIn('%.4f' % expected[key], text, key)
        for key in ('records', 'excluded_records', 'review_records',
                    'structural_candidates', 'chains_shown',
                    'proven_structural', 'proven_by_control_only',
                    'review_application_with_flat_counter'):
            self.assertRegex(text, r'(?<!\d)%d(?!\d)' % expected[key], key)
        self.assertIn('`{"unchanged": %d}`' % expected['records'], text)

    def test_the_live_qualification_numbers_add_up(self):
        # [REASON]: числа квалификации 21.09.2026 ниоткуда не выводятся -- это
        # запись живого прогона, и repo их не может пересчитать. Проверить
        # можно ровно одно, и это стоит проверять: чтобы запись не
        # противоречила сама себе после правки документа.
        text = read()
        ids, candidates, controls = [int(x) for x in re.search(
            r'\*\*(\d+) идентификатора = (\d+) кандидатов \+ (\d+)\s+'
            r'контрольных\*\*', text).groups()]
        self.assertEqual(ids, candidates + controls)
        envelopes, per_id, kinds = [int(x) for x in re.search(
            r'Отправлено (\d+) конвертов источников \((\d+) × (\d+)\)',
            text).groups()]
        self.assertEqual(envelopes, per_id * kinds)
        self.assertEqual(per_id, ids)
        square_metres, hectares = re.search(
            r'RAW\s+([\d ]+) м² \(([\d,]+) га\)', text).groups()
        self.assertAlmostEqual(int(square_metres.replace(' ', '')) / 10000.0,
                               float(hectares.replace(',', '.')), places=4)

    def test_the_exit_code_table_is_the_tools_own(self):
        # [REASON]: таблица ищется в СВОЁМ разделе, а не во всём файле.
        # DRONE-AREA-CONTROL-V2-MEGA добавил циклу код 7 и рядом появляется
        # инструмент backfill со своими кодами; строка «| 8 |» чужой таблицы
        # иначе смешалась бы с этой, и тест падал бы на правильном тексте
        # (или, хуже, прятал бы пропуск кода цикла за чужой строкой).
        text = read()
        head = '### Коды возврата цикла'
        self.assertIn(head, text)
        section = text.split(head, 1)[1].split('\n#', 1)[0]
        table = dict((int(code), meaning) for code, meaning in re.findall(
            r'^\| (\d) \| ([^|]+) \|', section, re.M))
        self.assertEqual(sorted(table), [
            daily.EXIT_OK, daily.EXIT_USAGE, daily.EXIT_NO_DATABASE,
            daily.EXIT_STEP_FAILED, daily.EXIT_MANIFEST_TOO_LARGE,
            daily.EXIT_SOURCES_INCOMPLETE, daily.EXIT_NOT_IDEMPOTENT,
            daily.EXIT_BUSY])
        self.assertIn('--stop-above', table[daily.EXIT_MANIFEST_TOO_LARGE])
        self.assertIn('--expect-unchanged', table[daily.EXIT_NOT_IDEMPOTENT])
        # Блок E: пятый -- потеря КАНДИДАТА, а потеря контроля -- код 0.
        self.assertIn('кандидат', table[daily.EXIT_CANDIDATE_EVIDENCE_MISSING])
        self.assertIn(daily.OUTCOME_WARNINGS, table[daily.EXIT_OK])
        self.assertIn('блокировк', table[daily.EXIT_BUSY])
        self.assertEqual((acceptance.EXIT_PASS, acceptance.EXIT_FAIL), (0, 3))
        self.assertEqual((raw_guard.EXIT_OK, raw_guard.EXIT_VIOLATED), (0, 3))

    def test_the_flags_the_blocks_pass_exist(self):
        parser = daily.build_parser()
        known = set(opt for action in parser._actions
                    for opt in action.option_strings)
        for block in blocks():
            for line in block.splitlines():
                if 'dji_area_daily.py' in line:
                    for flag in re.findall(r'(--[a-z-]+)', line):
                        self.assertIn(flag, known, line)


if __name__ == '__main__':
    unittest.main()
