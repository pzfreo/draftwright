"""Regression checks for disabled autodiscovery and compiled inventory drift."""
from pathlib import Path
import re
import tempfile
import unittest

import check_test_binaries as guard
import test_shards


class TestBinaryGuard(unittest.TestCase):
    def fixture(self, root):
        (root / 'tests/common').mkdir(parents=True)
        (root / 'tests/group').mkdir()
        (root / 'Cargo.toml').write_text('''[package]
name = "fixture"
autotests = false
[[test]]
name = "group"
path = "tests/group/main.rs"
''')
        (root / 'tests/group/main.rs').write_text('''
#[path = "../cases.rs"]
mod cases;
''')
        (root / 'tests/cases.rs').write_text('''
#[path = "common/support.rs"]
mod support;
// mod ghost;
const TEXT: &str = "mod ghost; #[test] fn ghost() {}";
const BYTES: &[u8] = br#"{"quoted": "mod ghost; #[test] fn ghost() {}"}"#;
const CONTINUED: &str = "first \\\n+second { mod ghost; }";
#[test]
fn first() { let json = r#"{ mod ghost; }"#; }
mod nested {
    #[test]
    fn second() {}
}
part_cases!(parts; alpha => "a.step", beta => "b.step");
pmi_cases!(pmi);
quality_cases!(quality);
''')
        (root / 'tests/common/support.rs').write_text('''
macro_rules! pmi_files {
    ($emit:ident) => { $emit!(one => "one.step", two => "two.step"); };
}
macro_rules! quality_files {
    ($emit:ident) => { $emit!(q1 => "q/1.step"); };
}
''')

    def test_walks_modules_and_generated_tests_without_reading_comments_or_strings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            expected, reached = guard.inventory(root)
            self.assertEqual(expected, {'group': {
                'cases::first', 'cases::nested::second', 'cases::parts::alpha',
                'cases::parts::beta', 'cases::pmi::one', 'cases::pmi::two', 'cases::quality::q1'}})
            self.assertEqual(len(reached), 3)

    def test_new_unreferenced_file_fails_even_in_a_nested_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.fixture(root)
            (root / 'tests/common/forgotten.rs').write_text('#[test] fn lost() {}')
            with self.assertRaisesRegex(ValueError, 'orphan.*forgotten.rs'):
                guard.inventory(root)

    def test_count_drop_or_replacement_fails(self):
        expected = {'group': {'cases::first', 'cases::second'}}
        def listing(names):
            return {'rust-suites': {'id': {
                'kind': 'test', 'binary-name': 'group',
                'testcases': {name: {} for name in names}}}}
        guard.check_list(expected, listing(expected['group']))
        for names in ({'cases::first'}, {'cases::first', 'cases::replacement'}):
            with self.assertRaisesRegex(ValueError, 'missing=.*cases::second'):
                guard.check_list(expected, listing(names))

    def test_repository_and_ci_keep_both_guards_enabled(self):
        expected, _ = guard.inventory()
        self.assertEqual(set(expected), {
            'core', 'dimensions', 'rendering', 'audits', 'session', 'multi_sheet_latency',
            'issue_drafting', 'issue_trust', 'steerability'})
        workflow = (guard.ROOT / '.github/workflows/ci.yml').read_text()
        lint, rest = workflow.split('  lint:', 1)[1].split('  build:', 1)
        harness = rest.split('  test:', 1)[1].split('  acceptance:', 1)[0]
        self.assertIn('python3 tools/check_test_binaries.py', lint)
        self.assertIn('--ignore-default-filter --message-format json', harness)
        self.assertIn('python3 tools/check_test_binaries.py --nextest-list', harness)
        self.assertIn('cargo nextest list --archive-file nextest-archive.tar.zst', harness)
        manifest = guard.tomllib.loads((guard.ROOT / 'Cargo.toml').read_text())
        self.assertFalse(manifest['package']['autotests'])

    def test_duration_shards_partition_the_listed_tests(self):
        def case(status):
            return {'filter-match': {'status': status}}
        listing = {'rust-suites': {
            'a': {'binary-id': 'a', 'testcases': {f't{i}': case('matches') for i in range(40)}},
            'b': {'binary-id': 'b', 'testcases': {'slow': case('matches'), 'left_out': case('mismatch')}}}}
        tests = test_shards.listed(listing)
        self.assertEqual(len(tests), 41)
        self.assertNotIn(('b', 'left_out'), tests)
        durations = {'b slow': 100.0, 'a t0': 50.0, 'a t1': 1.0}
        for n in (1, 3, 7):
            shards, load = test_shards.assign(tests, durations, n)
            self.assertEqual(sorted(t for shard in shards for t in shard), tests)
            self.assertEqual(shards, test_shards.assign(list(reversed(tests)), durations, n)[0])
        shards, _ = test_shards.assign(tests, durations, 3)
        self.assertEqual(len([s for s in shards if {('b', 'slow'), ('a', 't0')} & set(s)]), 2)
        self.assertEqual(test_shards.filterset([('b', 'slow'), ('a', 't1'), ('a', 't0')]),
                         '(binary_id(=a) & (test(=t0) | test(=t1))) | (binary_id(=b) & (test(=slow)))')

    def test_shard_priorities_start_long_tests_first(self):
        config = test_shards.priorities([('a', 'slow'), ('a', 'quick'), ('b', 'huge'), ('b', 'mid')],
                                        {'a slow': 100.0, 'a quick': 9.9, 'b huge': 900.0, 'b mid': 101.0})
        parsed = guard.tomllib.loads(config)['profile']['ci']['overrides']
        self.assertEqual([(o['priority'], o['filter']) for o in parsed], [
            (99, '(binary_id(=b) & (test(=huge)))'),
            (25, '(binary_id(=a) & (test(=slow))) | (binary_id(=b) & (test(=mid)))')])

    def test_ci_runs_every_duration_shard(self):
        workflow = (guard.ROOT / '.github/workflows/ci.yml').read_text()
        job = workflow.split('  test:', 1)[1].split('\n  # ', 1)[0].split('\n  quality-pins:', 1)[0]
        shards = re.search(r'shard: \[([^\]]*)\]', job).group(1).split(', ')
        self.assertEqual(shards[-1], 'harness')
        n = len(shards) - 1
        self.assertEqual(shards[:-1], [f'{k}/{n}' for k in range(1, n + 1)])
        self.assertIn('tools/test_shards.py --list nextest-list.json --shard ${{ matrix.shard }}', job)
        self.assertIn('--profile ci -E "$filter"', job)
        self.assertIn('--tool-config-file "test-shards:$PWD/shard-priorities.toml"', job)
        self.assertNotIn('--partition', job)


if __name__ == '__main__':
    unittest.main()
