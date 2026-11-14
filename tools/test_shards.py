#!/usr/bin/env python3
"""Duration-balanced nextest shards for CI.

nextest's hash partition ignores durations, and a few corpus tests take minutes, so hash shards
of the same suite differed by more than two to one (CI run 39813975347: 34 to 76 test-minutes).
This assigns every test the `ci` profile runs to one of N shards, longest first to the
least-loaded shard, using the durations recorded in tools/test-durations.json; a test with no
record weighs the median. The assignment is a deterministic function of the archive's test list,
so the N shards partition it; stale durations only cost balance, never coverage.

  cargo nextest list --archive-file A --profile ci --message-format json > list.json
  python3 tools/test_shards.py --list list.json --shard 3/8   # prints the shard's filterset

--priorities PATH also writes a nextest tool config (pass it as --tool-config-file) that
starts the shard's tests longest first, and unrecorded tests before them: a 270 s test that
starts 460 s into its shard sets the shard's wall time. The repository's own `ci` overrides (the exclusive latency gates)
take precedence over it; it takes precedence over the `default` profile's hand-kept order.

--max-seconds 10 selects recorded tests of at most 10 seconds AND all unrecorded tests,
then balances that tier as usual. Harness binaries remain in CI's separate whole-binary job.
--check-tiers compares independently evaluated nextest listings of the two PR filters
(--pr-list, repeated) plus recorded slow tests with the full nextest listing (--list).
It never derives the expected full inventory from the PR selection predicate.

Refresh the record from the JUnit reports of a CI run's test shards (artifacts test-junit-*):
  python3 tools/test_shards.py --record junit-dir
"""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import statistics
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
DURATIONS = ROOT / 'tools/test-durations.json'


def shard_arg(text):
    k, n = map(int, text.split('/'))
    if not 1 <= k <= n:
        raise argparse.ArgumentTypeError('shard must be K/N with 1 <= K <= N')
    return k, n


def listed(listing):
    """(binary-id, test name) of every test the listing's filters run."""
    return sorted((suite['binary-id'], name)
                  for suite in listing['rust-suites'].values()
                  for name, case in suite['testcases'].items()
                  if case['filter-match']['status'] == 'matches')


def seconds_arg(text):
    value = float(text)
    if not math.isfinite(value) or value < 0:
        raise argparse.ArgumentTypeError('seconds must be finite and nonnegative')
    return value


def select(tests, durations, max_seconds=None):
    """New/unrecorded tests always run, even when the fast tier is requested."""
    return [test for test in tests if max_seconds is None
            or durations.get(f'{test[0]} {test[1]}', 0) <= max_seconds]


def check_tiers(tests, pr_tests, durations, max_seconds):
    """Compare actual nextest PR-filter matches against an independently listed full suite.

    Derive slow identities from the duration record, not by inverting select(). Nextest,
    rather than this script, evaluates the generated filters in the --pr-list inventories.
    """
    full = set(tests)
    slow = {tuple(key.split(' ', 1)) for key, seconds in durations.items()
            if seconds > max_seconds} & full
    if (len(full) != len(tests) or set(pr_tests) & slow
            or Counter(pr_tests) + Counter(slow) != Counter(tests)):
        raise ValueError('evaluated PR filters plus recorded slow tests must partition the full nextest list exactly once')
    for label, tier in [('PR', pr_tests), ('slow', slow), ('main', tests)]:
        minutes = sum(durations.get(f'{b} {t}', 0) for b, t in tier) / 60
        unknown = sum(f'{b} {t}' not in durations for b, t in tier)
        print(f'{label}: {len(tier)} tests, {minutes:.2f} recorded test-minutes, '
              f'{unknown} unrecorded', file=sys.stderr)


def assign(tests, durations, n):
    known = [durations[f'{b} {t}'] for b, t in tests if f'{b} {t}' in durations]
    default = statistics.median(known) if known else 1.0
    weight = {test: durations.get(f'{test[0]} {test[1]}', default) for test in tests}
    load, shards = [0.0] * n, [[] for _ in range(n)]
    for test in sorted(tests, key=lambda t: (-weight[t], t)):
        i = min(range(n), key=lambda j: (load[j], j))
        load[i] += weight[test]
        shards[i].append(test)
    return shards, load


def filterset(tests):
    by_binary = {}
    for binary, name in sorted(tests):
        by_binary.setdefault(binary, []).append(name)
    return ' | '.join(f'(binary_id(={b}) & (' + ' | '.join(f'test(={t})' for t in names) + '))'
                      for b, names in by_binary.items())


def priorities(tests, durations):
    """A nextest tool config ranking the tests of 10 s or more by recorded duration. A test with
    no record (one added since the record, or from the base branch a pull request merges with)
    starts first: it may be long, and a long test started last sets the shard's wall time."""
    buckets = {}
    for test in tests:
        seconds = durations.get(f'{test[0]} {test[1]}')
        if seconds is None or seconds >= 10:
            buckets.setdefault(99 if seconds is None else min(99, round(seconds / 4)), []).append(test)
    return ''.join(f"[[profile.ci.overrides]]\nfilter = '{filterset(group)}'\npriority = {p}\n\n"
                   for p, group in sorted(buckets.items(), reverse=True))


def record(directory):
    durations = {}
    for path in sorted(Path(directory).glob('**/*.xml')):
        for case in ET.parse(path).getroot().iter('testcase'):
            durations[f'{case.get("classname")} {case.get("name")}'] = round(float(case.get('time')), 1)
    if not durations:
        raise SystemExit(f'no JUnit test cases under {directory}')
    DURATIONS.write_text(json.dumps(durations, indent=0, sort_keys=True) + '\n')
    print(f'recorded {len(durations)} durations')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--list', type=Path, help='nextest list --message-format json output')
    parser.add_argument('--shard', type=shard_arg, metavar='K/N')
    parser.add_argument('--priorities', type=Path, metavar='TOML',
                        help='Also write the shard\'s longest-first nextest tool config here')
    parser.add_argument('--max-seconds', type=seconds_arg, metavar='SECONDS',
                        help='Keep tests at or below this duration, plus every unrecorded test')
    parser.add_argument('--pr-list', type=Path, action='append', default=[], metavar='JSON',
                        help='Independently evaluated nextest PR filter listing; repeat for each shard')
    parser.add_argument('--check-tiers', action='store_true',
                        help='Assert PR + slow = full main inventory (requires --max-seconds)')
    parser.add_argument('--record', type=Path, metavar='JUNIT_DIR')
    args = parser.parse_args()
    if args.record:
        return record(args.record)
    if args.check_tiers:
        if not args.list or not args.pr_list or args.max_seconds is None or args.shard:
            parser.error('--check-tiers requires --list, --pr-list and --max-seconds, without --shard')
        return check_tiers(listed(json.loads(args.list.read_text())),
                           [test for path in args.pr_list for test in listed(json.loads(path.read_text()))],
                           json.loads(DURATIONS.read_text()), args.max_seconds)
    if not (args.list and args.shard):
        parser.error('give --list and --shard, or --record')
    tests = listed(json.loads(args.list.read_text()))
    k, n = args.shard
    durations = json.loads(DURATIONS.read_text())
    tests = select(tests, durations, args.max_seconds)
    shards, load = assign(tests, durations, n)
    if not shards[k - 1]:
        raise SystemExit(f'shard {k}/{n} has no tests')
    print(f'shard {k}/{n}: {len(shards[k - 1])} of {len(tests)} tests, '
          f'{load[k - 1] / 60:.1f} of {sum(load) / 60:.1f} estimated test-minutes '
          '(unrecorded tests weigh the median)', file=sys.stderr)
    if args.priorities:
        args.priorities.write_text(priorities(shards[k - 1], durations))
    print(filterset(shards[k - 1]))


if __name__ == '__main__':
    raise SystemExit(main())
