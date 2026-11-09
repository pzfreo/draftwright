"""Isolated H3 planning latency gate, alternating baseline and candidate processes.

Usage: python3 -I tools/bench_label_lanes.py before-binary after-binary out-dir
Use the pre-H3 binary as before; no Cargo process should run during measurement.
"""
import argparse
import gzip
import json
from pathlib import Path
import statistics
import subprocess
import time

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('before', type=Path)
parser.add_argument('after', type=Path)
parser.add_argument('out', type=Path)
parser.add_argument('--runs', type=int, default=3)
parser.add_argument('--max-ratio', type=float, default=1.25)
args = parser.parse_args()
assert args.runs >= 3, 'at least three paired measurements required'
args.out.mkdir(parents=True, exist_ok=True)
fixture = Path(__file__).resolve().parents[1] / 'tests/fixtures/pmi/step/nist_stc_09_asme1_ap242-e4.stp.gz'
step = args.out / 'stc09.stp'
step.write_bytes(gzip.decompress(fixture.read_bytes()))
spec = args.out / 'text-edit.spec.json'
spec.write_text(json.dumps({'dimensions': {'d:f:slot:f102/slot_length.length': {'tolerance': '±0.1'}}}))
report = {}
for case, extra in [('default', []), ('text-edit', ['--spec', str(spec.resolve())])]:
    timings = {'before': [], 'after': []}
    plans = {}
    for run in range(args.runs):
        order = ['before', 'after'] if run % 2 == 0 else ['after', 'before']
        for name in order:
            start = time.monotonic()
            result = subprocess.run([str(getattr(args, name).resolve()), 'plan', str(step.resolve()), *extra],
                                    capture_output=True, check=True)
            timings[name].append(time.monotonic() - start)
            assert 'dimensions' in json.loads(result.stdout), result.stderr
            assert name not in plans or plans[name] == result.stdout, (case, name, 'nondeterministic plan')
            plans[name] = result.stdout
    for name, plan in plans.items():
        (args.out / f'{case}-{name}.plan.json').write_bytes(plan)
    medians = {name: statistics.median(values) for name, values in timings.items()}
    report[case] = {'seconds': timings, 'medians': medians,
                    'ratio': medians['after'] / medians['before']}
    print(case, json.dumps(report[case]), flush=True)
(args.out / 'latency.json').write_text(json.dumps(report, indent=2) + '\n')
assert all(r['ratio'] <= args.max_ratio for r in report.values()), 'planning latency regressed'
