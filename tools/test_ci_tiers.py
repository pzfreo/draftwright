"""Fast-tier boundaries, independently evaluated inventories, and parsed workflow wiring."""
import contextlib
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

import test_shards as shards

ROOT = Path(__file__).resolve().parents[1]


def listing(tests):
    suites = {}
    for binary, name in tests:
        suites.setdefault(binary, {'binary-id': binary, 'testcases': {}})['testcases'][name] = {
            'filter-match': {'status': 'matches'}}
    return {'rust-suites': suites}


class CITiers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())
        # PyYAML uses YAML 1.1, where the unquoted GitHub key `on` is a boolean.
        cls.events = cls.workflow.get('on', cls.workflow.get(True))
        cls.jobs = cls.workflow['jobs']

    def setUp(self):
        self.tests = [('b', name) for name in ['new', 'zero', 'quick', 'boundary', 'slow']]
        self.durations = {'b zero': 0, 'b quick': 9.9, 'b boundary': 10, 'b slow': 10.1}
        # Model nextest's independently evaluated filter results, not select()'s output.
        self.pr_tests = [('b', 'new'), ('b', 'zero'), ('b', 'quick'), ('b', 'boundary')]

    def test_inclusive_boundary_and_unknown_always_run(self):
        self.assertEqual(shards.select(self.tests, self.durations, 10), self.pr_tests)
        self.assertEqual(shards.select(self.tests, self.durations, 0), self.tests[:2])
        self.assertEqual(shards.select(self.tests, self.durations), self.tests)
        self.assertEqual(shards.select(self.tests, {}, 0), self.tests)

    def test_independent_nextest_results_cover_full_inventory(self):
        # The checker must not use the PR selector to manufacture its own expected result.
        with patch.object(shards, 'select', side_effect=AssertionError('selector reused')), \
                contextlib.redirect_stderr(io.StringIO()) as output:
            shards.check_tiers(self.tests, self.pr_tests, self.durations, 10)
        self.assertIn('PR: 4 tests', output.getvalue())
        self.assertIn('slow: 1 tests', output.getvalue())
        assigned, _ = shards.assign(self.pr_tests, self.durations, 2)
        self.assertEqual(sorted(t for group in assigned for t in group), sorted(self.pr_tests))
        self.assertIn('test(=new)', shards.priorities(self.pr_tests, self.durations))

    def test_coverage_rejects_dropped_unknown_boundary_duplicate_slow_or_extra_matches(self):
        invalid = [self.pr_tests[1:], self.pr_tests[:-1], self.pr_tests + self.pr_tests[:1],
                   self.pr_tests + [('b', 'slow')], self.pr_tests + [('b', 'absent')]]
        for actual in invalid:
            with self.subTest(actual=actual), self.assertRaises(ValueError):
                shards.check_tiers(self.tests, actual, self.durations, 10)
        with self.assertRaises(ValueError):
            shards.check_tiers(self.tests + self.tests[:1], self.pr_tests, self.durations, 10)

    def test_cli_filter_and_independently_listed_tier_check(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'list.json').write_text(json.dumps(listing(self.tests)))
            (path / 'pr1.json').write_text(json.dumps(listing(self.pr_tests[:2])))
            (path / 'pr2.json').write_text(json.dumps(listing(self.pr_tests[2:])))
            durations = path / 'durations.json'
            durations.write_text(json.dumps(self.durations))
            for extra in [['--shard', '1/1'], ['--check-tiers', '--pr-list', str(path / 'pr1.json'),
                                              '--pr-list', str(path / 'pr2.json')]]:
                argv = ['test_shards.py', '--list', str(path / 'list.json'), '--max-seconds', '10', *extra]
                with patch.object(sys, 'argv', argv), patch.object(shards, 'DURATIONS', durations), \
                        contextlib.redirect_stdout(io.StringIO()) as out, \
                        contextlib.redirect_stderr(io.StringIO()) as err:
                    shards.main()
                if '--shard' in extra:
                    self.assertIn('test(=boundary)', out.getvalue())
                    self.assertIn('test(=new)', out.getvalue())
                    self.assertNotIn('test(=slow)', out.getvalue())
                else:
                    self.assertIn('main: 5 tests', err.getvalue())
            (path / 'pr2.json').write_text(json.dumps(listing(self.pr_tests[2:-1])))
            with patch.object(sys, 'argv', argv), patch.object(shards, 'DURATIONS', durations), \
                    self.assertRaises(ValueError):
                shards.main()

    def test_invalid_duration_limit_rejected(self):
        for value in ['-1', 'nan', 'inf', 'nonsense']:
            result = subprocess.run([sys.executable, str(ROOT / 'tools/test_shards.py'),
                                     '--max-seconds', value], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)

    def test_setup_shell_emits_full_and_pr_matrices_and_quality_tiers(self):
        setup = self.jobs['changes']
        tier = next(step for step in setup['steps'] if step.get('id') == 'tier')
        self.assertEqual(tier['env']['FULL'], "${{ github.event_name != 'pull_request' || contains(github.event.pull_request.labels.*.name, 'full-ci') }}")
        for full in (True, False):
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / 'outputs'
                subprocess.run(['bash', '-eu', '-c', tier['run']], check=True,
                               env={**os.environ, 'FULL': str(full).lower(), 'GITHUB_OUTPUT': str(output)})
                values = dict(line.split('=', 1) for line in output.read_text().splitlines())
            self.assertEqual(values['full'], str(full).lower())
            self.assertEqual(values['tier'], 'full' if full else 'pr')
            n, q = (10, 4) if full else (2, 2)
            self.assertEqual(json.loads(values['shards']), [f'{k}/{n}' for k in range(1, n + 1)] + ['harness'])
            self.assertEqual(json.loads(values['quality_shards']), list(range(1, q + 1)))
            self.assertEqual(values['quality_count'], str(q))

    def test_unrelated_labels_do_not_start_or_cancel_an_active_pr_run(self):
        condition = "github.event.action != 'labeled' && github.event.action != 'unlabeled' || github.event.label.name == 'full-ci'"
        self.assertEqual(self.jobs['changes']['if'], condition)
        self.assertEqual(self.workflow['concurrency']['group'],
                         "ci-${{ github.event_name == 'pull_request' && (" + condition + ") && github.event.pull_request.number || github.run_id }}")
        self.assertEqual(self.events['pull_request']['types'],
                         ['opened', 'synchronize', 'reopened', 'labeled', 'unlabeled'])

    def test_wasm_changes_select_cheap_pr_checks_without_browser_parity(self):
        step = next(s for s in self.jobs['changes']['steps'] if s.get('id') == 'wasm')
        self.assertEqual(step['if'], "github.event_name == 'pull_request'")
        self.assertEqual(step['env'], {'BASE_REF': '${{ github.event.pull_request.base.sha }}',
                                       'HEAD_REF': '${{ github.event.pull_request.head.sha }}'})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.check_output(['git', *args], cwd=root, text=True).strip()
            git('init', '-q')
            git('config', 'user.name', 'CI test')
            git('config', 'user.email', 'ci@example.invalid')
            git('commit', '--allow-empty', '-qm', 'base')
            for file, changed in [('README.md', False), ('crates/draftwright-wasm/src/lib.rs', True),
                                  ('crates/draftwright-wasm/api.d.ts', True),
                                  ('tools/wasm-parity.test.mjs', True), ('src/api.rs', True),
                                  ('CONTRIBUTING.md', False)]:
                before = git('rev-parse', 'HEAD')
                path = root / file
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('fixture change')
                git('add', file)
                git('commit', '-qm', 'change')
                output = root / 'outputs'
                output.unlink(missing_ok=True)
                env = {**os.environ, 'BASE_REF': before, 'HEAD_REF': git('rev-parse', 'HEAD'),
                       'GITHUB_OUTPUT': str(output)}
                subprocess.run(['bash', '-eu', '-c', step['run']], cwd=root, env=env, check=True)
                self.assertEqual(output.read_text(), f'changed={str(changed).lower()}\n', file)
            output.unlink()
            env['BASE_REF'] = 'missing-commit'
            result = subprocess.run(['bash', '-eu', '-c', step['run']], cwd=root, env=env,
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output.exists())
        safeguard = next(s for s in self.jobs['lint']['steps'] if s.get('name') == 'PR WASM safeguards')
        self.assertEqual(safeguard['if'], "github.event_name == 'pull_request' && needs.changes.outputs.wasm_changed == 'true'")
        self.assertEqual(shlex.split(safeguard['run']),
                         ['python3', '-m', 'unittest', 'discover', '-s', 'tools', '-p', 'test_wasm_safeguards.py'])
        self.assertEqual(self.jobs['wasm']['if'], "needs.changes.outputs.full == 'true'")

    def test_workflow_preserves_tiers_and_independent_build_check(self):
        job = self.jobs['test']
        self.assertEqual(job['strategy']['matrix']['shard'], '${{ fromJSON(needs.changes.outputs.shards) }}')
        step = next(s for s in job['steps'] if s.get('if') == "matrix.shard != 'harness'" and 'run' in s)
        self.assertEqual(step['shell'], 'bash')
        self.assertEqual(step['env']['FULL'], '${{ needs.changes.outputs.full }}')
        check = next(s for s in self.jobs['build']['steps']
                     if s.get('name') == 'Assert PR and slow tiers cover the full main inventory')
        commands = [shlex.split(line) for line in check['run'].replace('\\\n', '').splitlines()
                    if line.strip().startswith(('python3 ', 'cargo '))]
        checker = next(c for c in commands if '--check-tiers' in c)
        self.assertEqual([checker[i + 1] for i, token in enumerate(checker) if token == '--pr-list'],
                         ['pr-list-1.json', 'pr-list-2.json'])
        lists = [c for c in commands if c[:3] == ['cargo', 'nextest', 'list']]
        self.assertEqual(len(lists), 2)
        self.assertNotIn('-E', lists[0])
        self.assertEqual(lists[1][lists[1].index('-E') + 1], '$filter')
        self.assertNotIn('if', self.jobs['quality-pins'])
        self.assertEqual(self.jobs['quality-gate']['if'], "${{ !cancelled() && needs.binary.result == 'success' }}")
        for name in ['acceptance', 'wasm']:
            self.assertEqual(self.jobs[name]['if'], "needs.changes.outputs.full == 'true'")
        test_commands = [shlex.split(s['run']) for s in self.jobs['lint']['steps'] if 'run' in s]
        self.assertTrue(any('test_ci_acceptance.py' in c for c in test_commands))


if __name__ == '__main__':
    unittest.main()
