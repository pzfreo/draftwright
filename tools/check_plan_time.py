"""Plan-cost budget for "draftwright plan" over the 70-part repeated-spans survey.

DRAFTWRIGHT_CORPUS=... python3 -I tools/check_plan_time.py --out target/plan-time.json

The gate counts retired instructions, not wall time: the count does not depend on
load, core type or runner speed, so a fixed budget holds anywhere the same
instruction set runs. Each part is planned once and its instructions are read
from the kernel (Darwin proc_pid_rusage). The run fails when the survey total is
more than MARGIN above the figure tools/plan-time-budget.json records for this
platform. Where no counter or no recorded figure exists (GitHub's Linux runners),
the job measures and reports wall time only and does not fail: wall time on a
shared runner is too noisy to gate on. Use --record deliberately after a
performance change, on the platform whose figure you are updating.
"""
import argparse
import ctypes
import gzip
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
FORMAT = 'draftwright.plan-instructions-budget/1'
MARGIN = 0.15


def platform_key():
    return f'{sys.platform}-{platform.machine()}'


def counted(command):
    """Run command; return (retired instructions or None, stdout bytes)."""
    if sys.platform != 'darwin':
        reply = subprocess.run(command, capture_output=True, check=True, timeout=3600)
        return None, reply.stdout
    library = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
    # rusage_info_v4: 16-byte UUID, then uint64 fields; ri_instructions is word 31.
    usage = (ctypes.c_uint64 * 37)()
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as error:
        child = subprocess.Popen(command, stdout=output, stderr=error)
        try:
            # Darwin's waitid also reports a child stopped (a load governor's SIGSTOP pauses
            # long runs), which read the count of a run cut short; wait for its exit itself.
            while True:
                info = os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOWAIT | os.WNOHANG)
                if info and info.si_code in (os.CLD_EXITED, os.CLD_KILLED, os.CLD_DUMPED):
                    break
                time.sleep(0.05)
            result = library.proc_pid_rusage(child.pid, 4, ctypes.byref(usage))
        finally:
            status = child.wait()
        output.seek(0)
        error.seek(0)
        if status or result:
            raise ValueError(f'{command}: exit={status}, rusage={result}, '
                             f'errno={ctypes.get_errno()}: {error.read().decode()}')
        return usage[31] or None, output.read()


def inputs(corpus, directory):
    files = [corpus / p for p in (ROOT / 'tools/repeated_span_parts.txt').read_text().splitlines()]
    fixtures = sorted((ROOT / 'tests/fixtures/pmi/step').glob('*.gz'))
    if len(files) != 47 or len(fixtures) != 23:
        raise ValueError('incomplete survey (47 corpus + 23 AP242 required)')
    for fixture in fixtures:
        path = directory / fixture.name[:-3]
        path.write_bytes(gzip.decompress(fixture.read_bytes()))
        files.append(path)
    if len({p.stem for p in files}) != 70 or not all(p.is_file() for p in files):
        raise ValueError('missing/ambiguous survey input')
    return sorted(files, key=lambda p: p.stem)


def verdict(parts, budget, key):
    """Gate on the instruction total when every part was counted and a figure exists."""
    if len(parts) != 70:
        raise ValueError('incomplete 70-part report')
    counts = [row['instructions'] for row in parts.values()]
    figure = budget.get('instructions', {}).get(key)
    if budget.get('format') != FORMAT:
        raise ValueError('incompatible plan budget')
    if figure is None or any(c is None for c in counts):
        return {'gated': False, 'passed': True, 'limit': None,
                'reason': f'no instruction counter or recorded figure for {key}; informational'}
    total = sum(counts)
    limit = figure * (1 + MARGIN)
    return {'gated': True, 'passed': total <= limit, 'limit': limit, 'instructions_total': total,
            'ratio_to_budget': total / figure}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, default=ROOT / 'target/release/draftwright')
    parser.add_argument('--corpus', type=Path, default=os.environ.get('DRAFTWRIGHT_CORPUS'))
    parser.add_argument('--budget', type=Path, default=ROOT / 'tools/plan-time-budget.json')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--record', action='store_true')
    args = parser.parse_args()
    if args.corpus is None:
        parser.error('corpus required')
    key = platform_key()
    parts = {}
    with tempfile.TemporaryDirectory(prefix='draftwright-plan-time-') as tmp:
        for part in inputs(args.corpus, Path(tmp)):
            start = time.perf_counter()
            instructions, reply = counted([str(args.binary), 'plan', str(part)])
            seconds = time.perf_counter() - start
            if 'dimensions' not in json.loads(reply):
                raise ValueError(f'{part}: not a plan')
            parts[part.stem] = {'instructions': instructions, 'seconds': seconds}
            print(f'{part.stem}: {instructions} instructions, {seconds:.3f}s', flush=True)
    budget = json.loads(args.budget.read_text())
    if args.record:
        if any(row['instructions'] is None for row in parts.values()):
            parser.error(f'no instruction counter on {key}; nothing to record')
        budget.setdefault('instructions', {})[key] = sum(r['instructions'] for r in parts.values())
        args.budget.write_text(json.dumps(budget, indent=2, sort_keys=True) + '\n')
    report = {'platform': key, 'parts': parts, 'budget': budget,
              'total_seconds': sum(r['seconds'] for r in parts.values()),
              **verdict(parts, budget, key)}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    if report['gated']:
        print(f'{report["instructions_total"]} instructions; limit {report["limit"]:.0f} '
              f'({report["ratio_to_budget"]:.3f} of budget); wall {report["total_seconds"]:.1f}s: '
              + ('PASS' if report['passed'] else 'FAIL'))
    else:
        print(f'wall {report["total_seconds"]:.1f}s; {report["reason"]}')
    return int(not report['passed'])


if __name__ == '__main__':
    raise SystemExit(main())
