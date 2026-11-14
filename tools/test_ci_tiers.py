"""Fast-tier boundaries, CLI behavior, coverage assertion and workflow tier wiring."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

import test_shards as shards

ROOT = Path(__file__).resolve().parents[1]


class CITiers(unittest.TestCase):
    def setUp(self):
        self.tests = [('b', name) for name in ['new', 'zero', 'quick', 'boundary', 'slow']]
        self.durations = {'b zero': 0, 'b quick': 9.9, 'b boundary': 10, 'b slow': 10.1}

    def test_inclusive_boundary_and_unknown_always_run(self):
        self.assertEqual(shards.select(self.tests, self.durations, 10), self.tests[:-1])
        self.assertEqual(shards.select(self.tests, self.durations, 0), self.tests[:2])
        self.assertEqual(shards.select(self.tests, self.durations), self.tests)
        self.assertEqual(shards.select(self.tests, {}, 0), self.tests)

    def test_tiers_partition_balanced_shards_and_preserve_new_tests(self):
        with contextlib.redirect_stderr(io.StringIO()) as output:
            shards.check_tiers(self.tests, self.durations, 10)
        self.assertIn('PR: 4 tests', output.getvalue())
        self.assertIn('slow: 1 tests', output.getvalue())
        fast = shards.select(self.tests, self.durations, 10)
        assigned, _ = shards.assign(fast, self.durations, 2)
        self.assertEqual(sorted(t for group in assigned for t in group), sorted(fast))
        self.assertIn('test(=new)', shards.priorities(fast, self.durations))

    def test_coverage_guard_detects_dropped_duplicated_or_misfiltered_tests(self):
        with self.assertRaises(ValueError):
            shards.check_tiers(self.tests + self.tests[:1], self.durations, 10)
        real = shards.assign
        def dropped(tests, durations, n):
            groups, load = real(tests, durations, n)
            groups[0] = groups[0][1:]
            return groups, load
        with patch.object(shards, 'assign', dropped), self.assertRaises(ValueError):
            shards.check_tiers(self.tests, self.durations, 10)
        with patch.object(shards, 'select', return_value=self.tests), self.assertRaises(ValueError):
            shards.check_tiers(self.tests, self.durations, 10)

    def test_cli_filter_and_tier_check(self):
        listing = {'rust-suites': {'b': {'binary-id': 'b', 'testcases': {
            t: {'filter-match': {'status': 'matches'}} for _, t in self.tests}}}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'list.json').write_text(json.dumps(listing))
            durations = path / 'durations.json'
            durations.write_text(json.dumps(self.durations))
            # Exercise the real CLI parser and main path with an isolated duration record.
            for extra in [['--shard', '1/1'], ['--check-tiers']]:
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

    def test_invalid_duration_limit_rejected(self):
        for value in ['-1', 'nan', 'inf', 'nonsense']:
            result = subprocess.run([sys.executable, str(ROOT / 'tools/test_shards.py'),
                                     '--max-seconds', value], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)

    def test_setup_shell_emits_full_and_pr_matrices_and_quality_tiers(self):
        workflow = (ROOT / '.github/workflows/ci.yml').read_text()
        setup = workflow.split('  changes:', 1)[1].split('  lint:', 1)[0]
        script = textwrap.dedent(setup.split('        run: |\n', 1)[1])
        for full in (True, False):
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / 'outputs'
                subprocess.run(['bash', '-eu', '-c', script], check=True,
                               env={**os.environ, 'FULL': str(full).lower(), 'GITHUB_OUTPUT': str(output)})
                values = dict(line.split('=', 1) for line in output.read_text().splitlines())
            self.assertEqual(values['full'], str(full).lower())
            self.assertEqual(values['tier'], 'full' if full else 'pr')
            n = 10 if full else 2
            self.assertEqual(json.loads(values['shards']), [f'{k}/{n}' for k in range(1, n + 1)] + ['harness'])
            q = 4 if full else 2
            self.assertEqual(json.loads(values['quality_shards']), list(range(1, q + 1)))
            self.assertEqual(values['quality_count'], str(q))

    def test_unrelated_labels_do_not_start_or_cancel_an_active_pr_run(self):
        workflow = (ROOT / '.github/workflows/ci.yml').read_text()
        condition = "github.event.action != 'labeled' && github.event.action != 'unlabeled' || github.event.label.name == 'full-ci'"
        setup = workflow.split('  changes:', 1)[1].split('  lint:', 1)[0]
        self.assertIn('if: ' + condition, setup)
        concurrency = workflow.split('concurrency:', 1)[1].split('env:', 1)[0]
        self.assertIn('(' + condition + ') && github.event.pull_request.number || github.run_id', concurrency)

    def test_workflow_keeps_full_main_and_all_drawing_acceptance_on_prs(self):
        workflow = (ROOT / '.github/workflows/ci.yml').read_text()
        setup = workflow.split('  changes:', 1)[1].split('  lint:', 1)[0]
        self.assertIn("github.event_name != 'pull_request' || contains(github.event.pull_request.labels.*.name, 'full-ci')", setup)
        self.assertIn('types: [opened, synchronize, reopened, labeled, unlabeled]', workflow)
        self.assertNotIn('release_only', workflow)
        job = workflow.split('  test:', 1)[1].split('  quality-pins:', 1)[0]
        self.assertIn('FULL: ${{ needs.changes.outputs.full }}', job)
        self.assertIn('tier_args=()\n          if [ "$FULL" != true ]; then tier_args=(--max-seconds 10); fi', job)
        self.assertIn('"${tier_args[@]}"', job)
        self.assertIn('--max-seconds 10 --check-tiers', workflow.split('  build:', 1)[1].split('  binary:', 1)[0])
        for name in ['quality-pins', 'quality-gate']:
            job = workflow.split(f'  {name}:', 1)[1].split('    steps:', 1)[0]
            self.assertNotIn('needs.changes.outputs.full', job)
        for name in ['acceptance', 'wasm']:
            job = workflow.split(f'  {name}:', 1)[1].split('    steps:', 1)[0]
            self.assertIn("if: needs.changes.outputs.full == 'true'", job)


if __name__ == '__main__':
    unittest.main()
