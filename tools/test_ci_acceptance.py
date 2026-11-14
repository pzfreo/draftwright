"""Acceptance gates must reject bad/incomplete evidence; no renderer mocks or extra packages."""
import copy
import json
import os
import importlib.util
import re
import shutil
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / 'tools/ci_acceptance.py'
KEYS = ['off_sheet', 'repeated_label', 'repeated_measure', 'views_overlap',
        'view_on_title_block', 'ink', 'repeated_span', 'line_through_feature',
        'hidden_on_visible', 'section_hidden']
NAMES = [Path(p).stem for p in (ROOT / 'tools/quality_parts.txt').read_text().splitlines()]


class Acceptance(unittest.TestCase):
    def check(self, kind, result, baseline=None, extra=()):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'result.json'
            path.write_text(json.dumps(result))
            args = [sys.executable, '-I', str(RUNNER), '--check-results', kind, '--results', str(path), *extra]
            if baseline is not None:
                ref = Path(tmp) / 'baseline.json'
                ref.write_text(json.dumps({'metrics': KEYS, 'parts': baseline}))
                args += ['--baseline', str(ref)]
            return subprocess.run(args, cwd=ROOT, capture_output=True, text=True)

    def quality(self):
        return {name: dict.fromkeys(KEYS, 0) for name in NAMES}

    def test_quality_accepts_equal_and_improved_cells(self):
        before, after = self.quality(), self.quality()
        before[NAMES[0]]['ink'] = 1
        for actual in (before, after):
            result = self.check('quality', actual, before)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_quality_rejects_every_regressed_or_missing_column(self):
        before = self.quality()
        for key in KEYS:
            for missing in (False, True):
                after = copy.deepcopy(before)
                if missing:
                    del after[NAMES[0]][key]
                else:
                    after[NAMES[0]][key] = 1
                result = self.check('quality', after, before)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(key, result.stderr)

    def test_quality_rejects_missing_parts_and_errors(self):
        before = self.quality()
        for error in (False, True):
            after = copy.deepcopy(before)
            if error:
                after[NAMES[0]] = {'error': 'render failed'}
            else:
                del after[NAMES[0]]
            result = self.check('quality', after, before)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn('incomplete' if not error else 'render failed', result.stderr)

    def test_survey_requires_all_68_parts_and_zero_repeated_spans(self):
        names = [Path(p).stem for p in (ROOT / 'tools/repeated_span_parts.txt').read_text().splitlines()]
        names += [Path(p.name[:-3]).stem for p in (ROOT / 'tests/fixtures/pmi/step').glob('*.gz')]
        result = {name: {'repeated_span': 0} for name in names}
        self.assertEqual(len(result), 68)
        self.assertEqual(self.check('survey', result).returncode, 0)
        for bad in (dict(list(result.items())[1:]), {**result, names[0]: {'repeated_span': 1}},
                    {**result, names[0]: {'error': 'render failed'}}):
            checked = self.check('survey', bad)
            self.assertEqual(checked.returncode, 1, checked.stderr)
            self.assertIn('acceptance:', checked.stderr)

    def pins(self):
        parts = {name: {'ids': ['d:a'], 'unpinned': {'ink': 2, 'off_sheet': 0, 'off_sheet_items': []},
                       'pinned': {'ink': 3, 'off_sheet': 0, 'off_sheet_items': []}, 'go': True}
                 for name in NAMES}
        return {'seed': 1, 'pins': 1, 'go': True, 'parts': parts}

    def test_pin_bar_is_enforced_even_with_forged_go(self):
        good = self.pins()
        self.assertEqual(self.check('pins', good, self.quality()).returncode, 1,
                         'unpinned metrics must match the supplied same-run baseline')
        before = {n: p['unpinned'] for n, p in good['parts'].items()}
        self.assertEqual(self.check('pins', good, before).returncode, 0)
        for change in ({'ink': 4}, {'off_sheet': 1, 'off_sheet_items': ['d:a']}):
            bad = copy.deepcopy(good)
            bad['parts'][NAMES[0]]['pinned'].update(change)
            result = self.check('pins', bad, before)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn('pin bar', result.stderr)
        bad = copy.deepcopy(good)
        bad['parts'][NAMES[0]]['unpinned']['ink'] = 0
        bad_before = {n: p['unpinned'] for n, p in bad['parts'].items()}
        self.assertIn('pin bar', self.check('pins', bad, bad_before).stderr)

    def test_pin_reports_require_complete_parts_and_requested_ids(self):
        good = self.pins()
        before = {n: p['unpinned'] for n, p in good['parts'].items()}
        for missing_part in (False, True):
            bad = copy.deepcopy(good)
            if missing_part:
                del bad['parts'][NAMES[0]]
            else:
                bad['parts'][NAMES[0]]['ids'] = []
            result = self.check('pins', bad, before)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn('incomplete', result.stderr)

    def test_mcp_requires_seven_unique_successful_tasks(self):
        tasks = json.loads((ROOT / 'tools/mcp-eval/tasks.json').read_text())['tasks']
        rows = [{'task': t['id'], 'success': True, 'failed_conditions': []} for t in tasks]
        report = {'results': rows, 'summary': {'tasks': 7, 'succeeded': 7}}
        self.assertEqual(self.check('mcp', report).returncode, 0)
        for bad_rows in (rows[:-1], rows[:-1] + [rows[0]],
                         [{**rows[0], 'success': False}] + rows[1:]):
            result = self.check('mcp', {**report, 'results': bad_rows})
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn('acceptance:', result.stderr)

    def test_ci_calls_local_runner_and_always_uploads_evidence(self):
        workflow = (ROOT / '.github/workflows/ci.yml').read_text()
        for required in ('quality-pins', 'quality-gate', 'mcp', 'tools/ci_acceptance.py',
                         'if: always()', 'acceptance-inputs.tar.gz', 'timeout-minutes: ${{ matrix.timeout }}'):
            self.assertIn(required, workflow)
        self.assertNotIn('continue-on-error', workflow)

    def runner(self):
        spec = importlib.util.spec_from_file_location('ci_acceptance', RUNNER)
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        return runner

    def test_quality_shards_partition_every_measurement(self):
        runner = self.runner()
        tasks = runner.quality_tasks()
        self.assertEqual(len(tasks), len(set(tasks)))
        self.assertEqual(sum(kind == 'quality' for kind, _ in tasks), 30)
        self.assertEqual(sum(kind.startswith('pins-') for kind, _ in tasks), 270)
        self.assertEqual({kind for kind, _ in tasks if kind.startswith('pins-')},
                         {'pins-' + cell for cell in runner.PIN_CELLS})
        self.assertGreater(sum(kind == 'ap242' for kind, _ in tasks), 0)
        for n in (1, 2, 7, 12):
            shards = [runner.shard_tasks(k, n) for k in range(1, n + 1)]
            self.assertEqual(sorted(t for shard in shards for t in shard), sorted(tasks))

    def test_hosted_quality_shards_cover_every_task_and_the_gate_needs_them(self):
        workflow = (ROOT / '.github/workflows/ci.yml').read_text()
        job = workflow[workflow.index('  quality-pins:'):workflow.index('  quality-gate:')]
        setup = workflow.split('  changes:', 1)[1].split('  lint:', 1)[0]
        matrices = re.findall(r"echo 'quality_shards=(\[[^\]]*\])'", setup)
        self.assertEqual([json.loads(v) for v in matrices], [[1, 2, 3, 4], [1, 2]])
        self.assertIn('shard: ${{ fromJSON(needs.changes.outputs.quality_shards) }}', job)
        self.assertIn('--shard ${{ matrix.shard }}/${{ needs.changes.outputs.quality_count }}', job)
        self.assertIn('--tier ${{ needs.changes.outputs.tier }}', job)
        self.assertIn('name: acceptance-quality-pins-${{ matrix.shard }}-${{ needs.changes.outputs.quality_count }}', job)
        self.assertIn('name: quality-gate-input-${{ matrix.shard }}-${{ needs.changes.outputs.quality_count }}', job)
        gate = workflow[workflow.index('  quality-gate:'):]
        self.assertIn('needs: [changes, binary, quality-pins]', gate[:450])
        self.assertIn('gate_args=(--require-go)', gate)
        self.assertIn('if [ "$TIER" = pr ]; then gate_args=(); fi', gate)
        self.assertIn('--job quality-gate', gate)
        self.assertIn('--require-go', gate)
        self.assertIn('pattern: quality-gate-input-*', gate)

    def fake_shards(self, runner, tmp, n, pinned=None, tier="full"):
        """Run every shard with a stand-in renderer; the gate's subprocess checks are patched."""
        def one(f, dw, out, spec=None, timeout=600, review=True):
            name = os.path.basename(str(f)).split('.')[0]
            Path(out).mkdir(parents=True, exist_ok=True)
            row = dict.fromkeys(KEYS, 0)
            row.update(ink=2, off_sheet_items=[])
            if spec:
                row.update(pinned or {})
            (Path(out) / f'{name}.plan.json').write_text('{}')
            return name, row
        plan = SimpleNamespace(returncode=0, stdout=json.dumps({'dimensions': [
            {'id': f'd:{i}', 'placed': {}} for i in range(8)], 'views': []}))
        def inputs(corpus, directory):
            return [Path(tmp) / 'survey' / f'{name}.step' for name in sorted(runner.survey_names())]
        with patch.object(runner.quality, 'one', side_effect=one), \
                patch.object(runner.survey, 'inputs', side_effect=inputs), \
                patch.object(runner.subprocess, 'run', return_value=plan):
            for k in range(1, n + 1):
                out = Path(tmp) / f'shard-{k}'
                out.mkdir()
                run = runner.Run(SimpleNamespace(binary=self.binary(tmp), corpus=Path(tmp), shard=(k, n), tier=tier), out)
                run.execute('quality-pins')
                self.assertTrue(all(step['passed'] for step in run.steps), run.steps)

    def binary(self, tmp, content=b'draftwright'):
        path = Path(tmp) / 'draftwright'
        path.write_bytes(content)
        return path

    def gate(self, runner, tmp, shards_dir, name='gate', tier='full'):
        baseline = Path(tmp) / 'committed.json'
        baseline.write_text(json.dumps({'metrics': KEYS, 'parts': {
            n: {**dict.fromkeys(KEYS, 0), 'ink': 2} for n in NAMES}}))
        out = Path(tmp) / name
        out.mkdir()
        run = runner.Run(SimpleNamespace(binary=Path(tmp) / 'draftwright', baseline=baseline,
                                         shards_dir=shards_dir, tier=tier), out)
        with patch.object(run, 'step', return_value=True) as step, \
                patch.object(runner.subprocess, 'run', return_value=SimpleNamespace(returncode=1)):
            run.execute('quality-gate')
        return run, [c.args[0] for c in step.call_args_list], out

    def test_quality_gate_merges_shards_and_enforces_every_pin_cell(self):
        runner = self.runner()
        with tempfile.TemporaryDirectory() as tmp:
            self.fake_shards(runner, tmp, 3)
            run, steps, out = self.gate(runner, tmp, Path(tmp))
            self.assertTrue(all(s['passed'] for s in run.steps), run.steps)
            self.assertEqual(steps, ['leader-crossings', 'leader-routes', 'label-lane-locality'])
            names = [s['name'] for s in run.steps]
            self.assertEqual(names[:3], ['shards', 'quality', 'ap242-quality'])
            self.assertEqual(names[-10:], ['survey'] + ['pins-' + c for c in runner.PIN_CELLS])
            self.assertEqual(len(json.loads((out / 'survey/baseline.json').read_text())), 68)
            self.assertEqual(len(json.loads((out / 'quality/baseline.json').read_text())), 30)
            for cell in runner.PIN_CELLS:
                report = json.loads((out / f'pins-{cell}/random-pins.json').read_text())
                self.assertEqual(len(report['parts']), 30)
                self.assertTrue(all(len(p['ids']) == int(cell[2]) for p in report['parts'].values()))
            # A shard of another binary fails the gate before any comparison.
            self.binary(tmp, b'rebuilt')
            run, steps, _ = self.gate(runner, tmp, Path(tmp), 'gate-other-binary')
            self.assertIn('different binary', run.steps[0]['error'])
            self.assertEqual(steps, [])
            self.binary(tmp, b'draftwright')
            # A missing shard fails the gate before any comparison.
            shutil.rmtree(Path(tmp) / 'shard-2')
            run, steps, _ = self.gate(runner, tmp, Path(tmp), 'gate-again')
            self.assertFalse(run.steps[0]['passed'])
            self.assertIn('incomplete', run.steps[0]['error'])
            self.assertEqual(steps, [])

    def test_quality_gate_rejects_a_pin_regression(self):
        runner = self.runner()
        with tempfile.TemporaryDirectory() as tmp:
            self.fake_shards(runner, tmp, 2, pinned={'ink': 4})
            run, _, _ = self.gate(runner, tmp, Path(tmp))
            failed = {s['name']: s.get('error', '') for s in run.steps if not s['passed']}
            self.assertEqual(set(failed), {'pins-' + c for c in runner.PIN_CELLS})
            self.assertTrue(all('pin bar' in e for e in failed.values()))

    def test_pr_quality_tasks_keep_every_unpinned_drawing_and_full_survey(self):
        runner = self.runner()
        pr = runner.quality_tasks('pr')
        full = runner.quality_tasks()
        self.assertEqual(len(pr), 68)
        self.assertEqual(pr, [t for t in full if not t[0].startswith('pins-')])
        self.assertEqual(len(full) - len(pr), 270)
        self.assertEqual({name for _, name in pr}, runner.survey_names())
        groups = [runner.shard_tasks(k, 2, 'pr') for k in (1, 2)]
        self.assertEqual(sorted(t for g in groups for t in g), sorted(pr))

    def test_pr_gate_enforces_drawing_checks_and_full_gate_refuses_pr_evidence(self):
        runner = self.runner()
        with tempfile.TemporaryDirectory() as tmp:
            self.fake_shards(runner, tmp, 2, tier='pr')
            run, steps, out = self.gate(runner, tmp, Path(tmp), tier='pr')
            self.assertTrue(all(s['passed'] for s in run.steps), run.steps)
            self.assertEqual(steps, ['leader-crossings', 'leader-routes', 'label-lane-locality'])
            self.assertEqual([s['name'] for s in run.steps],
                             ['shards', 'quality', 'ap242-quality', 'reject-deliberate-regression', 'survey'])
            self.assertEqual(len(json.loads((out / 'survey/baseline.json').read_text())), 68)
            self.assertEqual(len(json.loads((out / 'quality/baseline.json').read_text())), 30)
            full, calls, _ = self.gate(runner, tmp, Path(tmp), 'full-refuses-pr')
            self.assertIn('tier differs', full.steps[0]['error'])
            self.assertEqual(calls, [])
            # A drawing regression still fails the PR gate.
            path = Path(tmp) / 'shard-1/shard.json'
            report = json.loads(path.read_text())
            name = next(iter(report['results']['quality']))
            report['results']['quality'][name]['ink'] = 999
            path.write_text(json.dumps(report))
            bad, _, _ = self.gate(runner, tmp, Path(tmp), 'bad-pr', tier='pr')
            self.assertTrue(any(s['name'] == 'quality' and not s['passed'] for s in bad.steps))

    def test_pr_cannot_claim_to_require_the_full_pin_grid(self):
        result = subprocess.run([sys.executable, '-I', str(RUNNER), '--job', 'quality-gate',
                                 '--tier', 'pr', '--require-go'], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('full random-pin grid', result.stderr)

    def test_survey_budgets_allow_observed_hosted_slowdown(self):
        runner = self.runner()
        run = runner.Run(SimpleNamespace(binary=Path('binary')), Path('out'))
        with patch.object(run, 'step') as step:
            run.execute('repeated-spans')
        command = step.call_args.args[1]
        self.assertEqual(command[command.index('--timeout') + 1], 3600)
        self.assertEqual(step.call_args.kwargs['timeout'], 6600)
        workflow = (ROOT / '.github/workflows/ci.yml').read_text()
        # CI surveys inside quality-pins (with this per-call budget) and judges in quality-gate.
        self.assertNotIn('--job repeated-spans', workflow)

    def test_ci_runs_plan_budget_with_release_binary(self):
        runner = self.runner()
        self.assertIn('plan-time', runner.JOBS)
        run = runner.Run(SimpleNamespace(binary=Path('binary'), corpus=Path('corpus')), Path('out'))
        with patch.object(run, 'step') as step:
            run.execute('plan-time')
        command = step.call_args.args[1]
        self.assertIn(ROOT / 'tools/check_plan_time.py', command)
        self.assertEqual(command[command.index('--binary') + 1], Path('binary'))
        self.assertEqual(command[command.index('--corpus') + 1], Path('corpus'))
        self.assertNotIn('--record', command)
        workflow = (ROOT / '.github/workflows/ci.yml').read_text()
        self.assertIn('--job plan-time', workflow)

    def test_plan_budget_gates_instructions_and_reports_without_a_counter(self):
        spec = importlib.util.spec_from_file_location('plan_time', ROOT / 'tools/check_plan_time.py')
        plan_time = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plan_time)
        names = self.runner().survey_names()
        parts = {p: {'instructions': 100, 'seconds': 1} for p in names}
        budget = {'format': plan_time.FORMAT, 'instructions': {'darwin-arm64': 6800}}
        self.assertTrue(plan_time.verdict(parts, budget, 'darwin-arm64')['passed'])
        slower = {p: {'instructions': 116, 'seconds': 1} for p in names}
        self.assertFalse(plan_time.verdict(slower, budget, 'darwin-arm64')['passed'])
        lowered = {**budget, 'instructions': {'darwin-arm64': 6800 * .7}}
        self.assertFalse(plan_time.verdict(parts, lowered, 'darwin-arm64')['passed'])
        # No figure for the platform, or no counter: report only, never fail.
        for key, rows in (('linux-x86_64', parts),
                          ('darwin-arm64', {p: {'instructions': None, 'seconds': 9} for p in names})):
            report = plan_time.verdict(rows, budget, key)
            self.assertEqual((report['gated'], report['passed']), (False, True))
        with self.assertRaises(ValueError):
            plan_time.verdict(dict(list(parts.items())[1:]), budget, 'darwin-arm64')
        recorded = json.loads((ROOT / 'tools/plan-time-budget.json').read_text())
        self.assertEqual(recorded['format'], plan_time.FORMAT)
        self.assertIn('darwin-arm64', recorded['instructions'])

    @unittest.skipUnless(sys.platform == 'darwin', 'retired instructions are read on Darwin')
    def test_instruction_count_is_the_whole_run_of_a_paused_child(self):
        spec = importlib.util.spec_from_file_location('plan_time', ROOT / 'tools/check_plan_time.py')
        plan_time = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plan_time)
        # Capture the actual counted child, without requiring a process-list service.
        # Function locals avoid per-process randomized global-dictionary lookup costs.
        # Keep enough work to pause three times, with the same whole-run bound below.
        marker = f'paused-{os.getpid()}'
        command = [sys.executable, '-I', '-c',
                   f'def work():\n s = 0\n for i in range(100_000_000): s += i  # {marker}\nwork()']
        whole, _ = plan_time.counted(command)
        found = {}
        counting = threading.Thread(target=lambda: found.update(paused=plan_time.counted(command)[0]))
        started = threading.Event()
        spawn = plan_time.subprocess.Popen

        def counted_child(*args, **kwargs):
            child = spawn(*args, **kwargs)
            found['pid'] = child.pid
            started.set()
            return child

        with patch.object(plan_time.subprocess, 'Popen', side_effect=counted_child):
            counting.start()
            self.assertTrue(started.wait(5), 'counted child did not start')
            pid = found['pid']
            # As a load governor does: the child paused and continued while it runs.
            for _ in range(3):
                time.sleep(0.2)
                os.kill(pid, signal.SIGSTOP)
                time.sleep(0.2)
                os.kill(pid, signal.SIGCONT)
            counting.join()
        self.assertGreater(found['paused'], 0.9 * whole)

    def test_ci_runs_render_tail_budget_with_release_binary(self):
        runner = self.runner()
        self.assertIn('render-tail', runner.JOBS)
        run = runner.Run(SimpleNamespace(binary=Path('binary'), corpus=Path('corpus')), Path('out'))
        with patch.object(run, 'step') as step:
            run.execute('render-tail')
        command = step.call_args.args[1]
        self.assertIn(ROOT / 'tools/check_render_tail.py', command)
        self.assertEqual(command[command.index('--binary') + 1], Path('binary'))
        self.assertEqual(command[command.index('--corpus') + 1], Path('corpus'))
        self.assertNotIn('--record', command)
        workflow = (ROOT / '.github/workflows/ci.yml').read_text()
        self.assertIn('--job render-tail', workflow)

    def test_render_tail_gates_each_part_on_instructions_or_processor_time(self):
        spec = importlib.util.spec_from_file_location('render_tail', ROOT / 'tools/check_render_tail.py')
        tail = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tail)
        names = sorted(tail.PARTS)
        self.assertEqual(names, ['cgb243', 'threaded_connector_109'])
        budget = {'format': tail.FORMAT, 'instructions': {'darwin-arm64': dict.fromkeys(names, 1000)},
                  'cpu_seconds_ceiling': dict.fromkeys(names, 100)}

        def rows(instructions, cpu):
            return {p: {'instructions': instructions, 'cpu_seconds': cpu, 'seconds': 1} for p in names}

        self.assertTrue(tail.verdict(rows(1100, 999), budget, 'darwin-arm64')['passed'])
        # One part past its figure fails, whatever the other does.
        slower = rows(1000, 1)
        slower['cgb243']['instructions'] = 1151
        report = tail.verdict(slower, budget, 'darwin-arm64')
        self.assertFalse(report['passed'])
        self.assertIn('cgb243', report['failures'][0])
        # No counter or no figure: each part is held to its processor-time ceiling instead.
        for key, measured in (('linux-x86_64', rows(10**9, 99)), ('darwin-arm64', rows(None, 99))):
            report = tail.verdict(measured, budget, key)
            self.assertEqual((report['gated_on'], report['passed']), ('cpu_seconds', True))
        hot = rows(None, 99)
        hot['threaded_connector_109']['cpu_seconds'] = 101
        self.assertFalse(tail.verdict(hot, budget, 'linux-x86_64')['passed'])
        with self.assertRaises(ValueError):
            tail.verdict({'cgb243': rows(1, 1)['cgb243']}, budget, 'darwin-arm64')
        recorded = json.loads((ROOT / 'tools/render-tail-budget.json').read_text())
        self.assertEqual(recorded['format'], tail.FORMAT)
        self.assertEqual(sorted(recorded['instructions']['darwin-arm64']), names)
        self.assertEqual(sorted(recorded['cpu_seconds_ceiling']), names)

    def test_require_go_rejects_partial_or_unrelated_jobs(self):
        for options in (['--job', 'mcp'], ['--job', 'quality-pins', '--shard', '1/9']):
            command = [sys.executable, '-I', str(RUNNER), '--require-go', *options]
            checked = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(checked.returncode, 2)
            self.assertIn('full random-pin grid', checked.stderr)

    def test_local_timeout_overrides_are_forwarded_without_changing_ci_defaults(self):
        runner = self.runner()
        run = runner.Run(SimpleNamespace(binary=Path('binary'), corpus=Path('corpus'),
                                        part_timeout=14400, survey_shard=(1, 4)), Path('out'))
        with patch.object(run, 'step') as step:
            run.execute('repeated-spans')
        command = step.call_args.args[1]
        self.assertEqual(command[command.index('--timeout') + 1], 14400)
        run = runner.Run(SimpleNamespace(binary=Path('binary'), survey_shard=(1, 4)), Path('out'))
        with patch.object(run, 'step') as step:
            run.execute('repeated-spans')
        command = step.call_args.args[1]
        self.assertEqual(command[command.index('--timeout') + 1], 3600)

    def test_instruction_gate_checks_total_and_each_part_within_one_percent(self):
        spec = importlib.util.spec_from_file_location('instructions', ROOT / 'tools/check_plan_instructions.py')
        instructions = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(instructions)
        self.assertTrue(instructions.assess({'a': {'base': 100, 'head': 55}})['passed'])
        self.assertFalse(instructions.assess({'a': {'base': 100, 'head': 56}})['passed'])
        self.assertTrue(instructions.assess({'a': {'base': 10000, 'head': 100},
                                             'b': {'base': 1000, 'head': 1010}})['passed'])
        report = instructions.assess({'a': {'base': 10000, 'head': 100}, 'b': {'base': 1000, 'head': 1011}})
        self.assertLess(report['ratio'], .55)
        self.assertFalse(report['passed'])
        self.assertEqual(set(report['worse_parts']), {'b'})
        with self.assertRaises(ValueError):
            instructions.assess({'a': {'base': 100, 'head': 0}})

    def test_survey_shards_partition_the_68_parts(self):
        runner = self.runner()
        names = runner.survey_names()
        slices = [runner.survey.in_shard(names, k, 4) for k in range(1, 5)]
        self.assertEqual(sorted(sum(slices, [])), sorted(names))
        self.assertTrue(all(15 <= len(s) <= 17 for s in slices))
        result = {name: {'repeated_span': 0} for name in slices[1]}
        self.assertEqual(self.check('survey', result, extra=['--survey-shard', '2/4']).returncode, 0)
        for bad in (dict(list(result.items())[1:]), {**result, slices[0][0]: {'repeated_span': 0}}):
            checked = self.check('survey', bad, extra=['--survey-shard', '2/4'])
            self.assertEqual(checked.returncode, 1, checked.stderr)
        run = runner.Run(SimpleNamespace(binary=Path('binary'), survey_shard=(3, 4)), Path('out'))
        with patch.object(run, 'step') as step:
            run.execute('repeated-spans')
        command = step.call_args.args[1]
        self.assertEqual(command[command.index('--shard') + 1], '3/4')

    def test_part_timeout_retains_error_evidence(self):
        quality = self.runner().quality
        with tempfile.TemporaryDirectory() as tmp:
            for operation in ('render', 'plan'):
                expired = subprocess.TimeoutExpired(['binary', operation], 3600)
                outputs = [expired] if operation == 'render' else [SimpleNamespace(), expired]
                with patch.object(quality.subprocess, 'run', side_effect=outputs) as child:
                    name, result = quality.one('slow.step', 'binary', tmp, timeout=3600)
                self.assertEqual(name, 'slow')
                self.assertIn('3600', result['error'])
                self.assertIn(operation, result['error'])
                self.assertTrue(all(c.kwargs['timeout'] == 3600 for c in child.call_args_list))

    def test_survey_writes_all_part_errors_after_timeouts(self):
        spec = importlib.util.spec_from_file_location('survey', ROOT / 'tools/survey_repeated_spans.py')
        survey = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(survey)
        corpus = os.environ.get('DRAFTWRIGHT_CORPUS')
        if not corpus:
            self.skipTest('DRAFTWRIGHT_CORPUS required for full survey error evidence')
        with tempfile.TemporaryDirectory() as tmp:
            expired = subprocess.TimeoutExpired(['binary', 'render'], 1)
            with patch.object(sys, 'argv', ['survey', '--timeout', '1', 'binary', tmp]), \
                    patch.object(survey.quality.subprocess, 'run', side_effect=expired), \
                    patch.object(survey.quality, 'table'):
                self.assertEqual(survey.main(), 1)
            results = json.loads((Path(tmp) / 'baseline.json').read_text())
            self.assertEqual(len(results), 68)
            self.assertTrue(all('timed out' in row['error'] for row in results.values()))

    def test_failed_subprocess_reports_exit_before_missing_results(self):
        runner = self.runner()
        with tempfile.TemporaryDirectory() as tmp:
            run = runner.Run(None, Path(tmp))
            self.assertFalse(run.step('survey', [sys.executable, '-c', 'raise SystemExit(1)'],
                                      'survey', Path(tmp) / 'missing.json'))
            self.assertEqual(run.steps[0]['exit_code'], 1)
            self.assertEqual(run.steps[0]['error'], 'survey exited 1; see survey.log')

    def test_mcp_requires_readable_judge_documents_and_rendered_artifacts(self):
        tasks = json.loads((ROOT / 'tools/mcp-eval/tasks.json').read_text())['tasks']
        report = {'results': [{'task': t['id'], 'success': True, 'failed_conditions': []} for t in tasks],
                  'summary': {'tasks': 7, 'succeeded': 7}}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            result = path / 'mcp.json'
            result.write_text(json.dumps(report))
            for task in tasks:
                directory = path / task['id']
                directory.mkdir()
                for name in ('spec', 'plan', 'baseline'):
                    (directory / (name + '.json')).write_text('{}')
                (directory / 'sheet.png').write_bytes(b'\x89PNG\r\n\x1a\nartifact')
            command = [sys.executable, '-I', str(RUNNER), '--check-results', 'mcp', '--results',
                       str(result), '--documents-dir', str(path)]
            checked = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            (path / tasks[0]['id'] / 'sheet.png').unlink()
            checked = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(checked.returncode, 1, checked.stderr)
            self.assertIn('sheet.png', checked.stderr)

    def test_missing_fixtures_fail_before_starting_mcp(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run([sys.executable, '-I', str(RUNNER), '--job', 'mcp',
                                     '--binary', sys.executable, '--corpus', tmp,
                                     '--out', str(Path(tmp) / 'out')],
                                    cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn('missing fixture', result.stderr)
            evidence = json.loads((Path(tmp) / 'out/summary.json').read_text())
            self.assertFalse(evidence['passed'])


if __name__ == '__main__':
    unittest.main()
