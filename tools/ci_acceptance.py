"""Local/CI acceptance using one existing release binary; run from the repository root.

DRAFTWRIGHT_CORPUS=... python3 -I tools/ci_acceptance.py --out target/acceptance
No build, fixture fallback, or baseline update is performed. Each job keeps its evidence.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
KEYS = ['off_sheet', 'repeated_label', 'repeated_measure', 'views_overlap',
        'view_on_title_block', 'ink', 'repeated_span', 'line_through_feature',
        'hidden_on_visible', 'section_hidden']
JOBS = ('quality-pins', 'quality-gate', 'repeated-spans', 'mcp', 'plan-time', 'render-tail')
PIN_CELLS = tuple(f'{seed}-{pins}' for seed in (1, 2, 3) for pins in (1, 3, 8))
# Seconds for one quality measurement (render + plan + analyse) of each part on the
# development machine (2026-10-22). They only balance the quality-pins shards: every
# task runs in exactly one shard whatever the weights, and unlisted parts weigh 10.
WEIGHTS = {
    'nist_ftc_08_asme1_rc': 99, 'nist_ctc_02_asme1_rc': 81, 'cgb203': 55, 'nist_ctc_04_asme1_rd': 30,
    'nist_ftc_10_asme1_rb': 27, 'cgb207': 27, 'nist_ftc_09_asme1_rd': 21, 'nist_ctc_05_asme1_rd': 21,
    'nist_ftc_06_asme1_rd': 17, 'nist_ftc_07_asme1_rd': 14, 'cgb241': 13, 'cgb245': 11,
    'nist_ctc_01_asme1_rd': 9, 'nist_ctc_03_asme1_rc': 4, '10020': 3, 'string_post': 2.4,
    'sm-hanger': 2.2, 'nist_ctc_02_asme1_ap242-e2': 91, 'nist_ftc_10_asme1_ap242-e2': 32,
    'nist_ftc_07_asme1_ap242-e2': 16, 'nist_ctc_01_asme1_ap242-e1': 5,
    # Survey-only parts (review off), timed on a loaded machine and scaled to the above.
    'nist_stc_09_asme1_ap242-e4': 77, 'nist_stc_10_asme1_ap242-e2': 51, 'nist_stc_06_asme1_ap242-e3': 37,
    'bracket_positions': 29, 'hw_every_kind': 14, 'spool_fits': 11, 'nist_ctc_01_merge': 9,
    '10190': 9, 'hw_spool_fits_intent': 8, 'hw_thread_replaced': 8, 'flanged_spool_132': 7,
    'hw_thread_removed': 7,
}
CORPUS_REV = '6d544e060cd0eeb4442d2196e5df757b5a15ea55'
spec = importlib.util.spec_from_file_location('quality', ROOT / 'tools/quality.py')
quality = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quality)
spec = importlib.util.spec_from_file_location('survey', ROOT / 'tools/survey_repeated_spans.py')
survey = importlib.util.module_from_spec(spec)
spec.loader.exec_module(survey)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def manifest(name):
    files = (ROOT / 'tools' / name).read_text().splitlines()
    require(len(files) == len(set(files)), f'duplicate fixture in {name}')
    return files


def names(files):
    return {Path(p).stem for p in files}


def survey_names():
    corpus = manifest('repeated_span_parts.txt')
    fixtures = sorted((ROOT / 'tests/fixtures/pmi/step').glob('*.gz'))
    require(len(corpus) == 45 and len(fixtures) == 23, 'incomplete survey fixture manifest (45 + 23 required)')
    result = names(corpus) | names(p.name[:-3] for p in fixtures)
    require(len(result) == 68, 'incomplete/ambiguous 68-part survey')
    return result


def ap242_fixtures():
    step = ROOT / 'tests/fixtures/pmi/step'
    return sorted({*step.glob('nist_ctc_0*_ap242*.gz'), *step.glob('nist_ftc_*_ap242*.gz')})


def quality_tasks(tier="full"):
    """Every measurement of the quality-pins job (PR omits only the random-pin cells): each quality part unpinned, each part in
    each of the nine random-pin cells, each AP242 CTC/FTC source, and the rest of the
    repeated-span survey (its verdict reads the plan, so the unpinned quality and AP242
    measurements serve the survey for their parts)."""
    parts = sorted(names(manifest('quality_parts.txt')))
    ap242 = [Path(f.stem).stem for f in ap242_fixtures()]
    rest = sorted(survey_names() - set(parts) - set(ap242))
    require(tier in ('pr', 'full'), f'unknown quality tier: {tier}')
    pins = [(f'pins-{c}', p) for c in PIN_CELLS for p in parts] if tier == 'full' else []
    return ([('quality', p) for p in parts] + pins
            + [('ap242', p) for p in ap242] + [('survey', p) for p in rest])


def shard_tasks(k, n, tier="full"):
    """The K-th of N shards of quality_tasks(): heaviest task first to the least-loaded shard.
    Deterministic, and the N shards partition the tasks."""
    load, shards = [0.0] * n, [[] for _ in range(n)]
    for task in sorted(quality_tasks(tier), key=lambda t: (-WEIGHTS.get(t[1], 10), t)):
        i = min(range(n), key=lambda j: (load[j], j))
        load[i] += WEIGHTS.get(task[1], 10)
        shards[i].append(task)
    return shards[k - 1]


def complete(actual, expected):
    require(set(actual) == expected, f'incomplete part set: missing {sorted(expected - set(actual))}, '
            f'unexpected {sorted(set(actual) - expected)}')
    for name, row in actual.items():
        require('error' not in row, f'{name}: {row.get("error")}')


def count(row, key, part):
    require(key in row and type(row[key]) is int and row[key] >= 0,
            f'{part}: missing/invalid {key}')
    return row[key]


def validate(kind, actual, baseline=None, shard=(1, 1)):
    expected = names(manifest('quality_parts.txt'))
    require(len(expected) == 30, 'incomplete quality manifest (30 required)')
    if kind == 'quality':
        require(baseline['metrics'] == KEYS, 'incomplete baseline metric list')
        reference = baseline['parts']
        complete(reference, expected)
        complete(actual, expected)
        for part in sorted(expected):
            for key in KEYS:
                before, after = count(reference[part], key, part), count(actual[part], key, part)
                require(after <= before, f'{part}: {key} regressed {before} -> {after}')
        return {'parts': 30, 'cells': 300,
                'before': {k: sum(p[k] for p in reference.values()) for k in KEYS},
                'after': {k: sum(p[k] for p in actual.values()) for k in KEYS}}
    if kind == 'survey':
        complete(actual, set(survey.in_shard(survey_names(), *shard)))
        for part, row in actual.items():
            require(count(row, 'repeated_span', part) == 0, f'{part}: repeated_span must be zero')
        return {'parts': len(actual), 'of': 63, 'shard': '/'.join(map(str, shard)), 'repeated_span': 0}
    if kind == 'pins':
        require(actual['seed'] in (1, 2, 3) and actual['pins'] in (1, 3, 8), 'unexpected pin grid cell')
        complete(actual['parts'], expected)
        cell = f"{actual['seed']}-{actual['pins']}"
        known = {(f['cell'], f['part']): f['reason']
                 for f in read(ROOT / 'tools/known-pin-failures.json')['failures']}
        reference = baseline.get('parts', baseline)
        complete(reference, expected)
        for part, row in actual['parts'].items():
            ids = row['ids']
            # A part with fewer drawable candidates than the cell asks for pins all of them.
            wanted = min(actual['pins'], row.get('candidates', actual['pins']))
            require(len(ids) == wanted and len(set(ids)) == len(ids), f'{part}: incomplete selected pins')
            require(row['unpinned'] == reference[part], f'{part}: unpinned baseline differs from same-run evidence')
            for variant in ('unpinned', 'pinned'):
                for key in ('ink', 'off_sheet'):
                    count(row[variant], key, part)
            if (cell, part) in known:
                # Listed in tools/known-pin-failures.json (alpha.13 only): reported, not required.
                if row['go'] is not True:
                    print(f"pins-{cell}: {part}: known NO-GO: {known[(cell, part)]}", flush=True)
                else:
                    print(f"pins-{cell}: {part}: listed known failure is GO; remove it", flush=True)
                continue
            require(quality.pin_bar(row['unpinned'], row['pinned']), f'{part}: pin bar failed')
            require(row['go'] is True, f'{part}: NO-GO')
        require(actual['go'] is True or all(
            row['go'] is True or (cell, part) in known for part, row in actual['parts'].items()),
            'pin grid NO-GO')
        return {'parts': 30, 'seed': actual['seed'], 'pins': actual['pins'],
                'ink': sum(p['pinned']['ink'] for p in actual['parts'].values()),
                'off_sheet': sum(p['pinned']['off_sheet'] for p in actual['parts'].values())}
    tasks = read(ROOT / 'tools/mcp-eval/tasks.json')['tasks']
    expected_tasks = {t['id'] for t in tasks}
    rows = actual['results']
    require(len(expected_tasks) == len(rows) == 7 and {r['task'] for r in rows} == expected_tasks,
            'incomplete/duplicate seven-task MCP run')
    require(all(r['success'] is True and not r.get('failure') and not r['failed_conditions'] for r in rows),
            'MCP task failed')
    require(actual['summary']['tasks'] == actual['summary']['succeeded'] == 7, 'incomplete MCP summary')
    return actual['summary']


def mcp_documents(directory):
    for task in read(ROOT / 'tools/mcp-eval/tasks.json')['tasks']:
        path = directory / task['id']
        for name in ('spec', 'plan', 'baseline'):
            read(path / (name + '.json'))
        image = (path / 'sheet.png').read_bytes()
        require(len(image) > 8 and image[:8] == b'\x89PNG\r\n\x1a\n',
                f'{task["id"]}: missing/invalid rendered PNG artifact')


class Run:
    def __init__(self, args, out):
        self.args, self.out, self.steps = args, out, []
        self.metrics = {}

    def step(self, label, argv, kind=None, result=None, baseline=None, timeout=1500):
        timeout = max(timeout, getattr(self.args, 'step_timeout', None) or 0)
        start = time.monotonic()
        record = {'name': label, 'command': [str(a) for a in argv]}
        try:
            with (self.out / (label + '.log')).open('w') as log:
                child = subprocess.run(record['command'], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                       timeout=timeout)
            record['exit_code'] = child.returncode
            require(child.returncode == 0, f'{label} exited {child.returncode}; see {label}.log')
            if kind:
                record['metrics'] = validate(kind, read(result), read(baseline) if baseline else None,
                                             getattr(self.args, 'survey_shard', (1, 1)))
                if kind == 'mcp':
                    mcp_documents(self.out / 'mcp-documents')
                self.metrics[label] = record['metrics']
            record['passed'] = True
        except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
            record.update(passed=False, error=str(error))
        record['wall_seconds'] = round(time.monotonic() - start, 3)
        self.steps.append(record)
        print(f'{label}: {"PASS" if record["passed"] else "FAIL"} ({record["wall_seconds"]}s)', flush=True)
        return record['passed']

    def verify(self, label, kind, actual, baseline):
        """Validate in-process evidence and record it like a step."""
        record = {'name': label}
        try:
            record['metrics'] = self.metrics[label] = validate(kind, actual, baseline)
            record['passed'] = True
        except (ValueError, KeyError, TypeError) as error:
            record.update(passed=False, error=str(error))
        self.steps.append(record)
        print(f'{label}: {"PASS" if record["passed"] else "FAIL"}', flush=True)
        return record['passed']

    def quality_shard(self, k, n):
        """Measure this shard's tasks; quality-gate compares them once every shard is in."""
        import gzip
        import threading
        from concurrent.futures import ThreadPoolExecutor
        args, out, binary = self.args, self.out, self.args.binary
        timeout = getattr(args, 'part_timeout', None) or 600
        files = {Path(f).stem: args.corpus / f for f in manifest('quality_parts.txt')}
        (out / 'ap242-inputs').mkdir(exist_ok=True)
        for fixture in ap242_fixtures():
            files[Path(fixture.stem).stem] = out / 'ap242-inputs' / fixture.stem
            files[Path(fixture.stem).stem].write_bytes(gzip.decompress(fixture.read_bytes()))
        try:
            for path in survey.inputs(args.corpus, out / 'survey-inputs'):
                files.setdefault(path.stem, path)
        except AssertionError as error:
            self.steps.append({'name': 'survey-inputs', 'passed': False, 'error': str(error)})
            return
        tier = getattr(args, 'tier', 'full')
        tasks = shard_tasks(k, n, tier)
        plans, lock = {}, threading.Lock()

        def unpinned(name):
            # One unpinned plan per part chooses the pins of every cell in this shard,
            # as quality.py's pinned_one plans the part before choosing its pins.
            with lock:
                cell = plans.setdefault(name, [threading.Lock(), None])
            with cell[0]:
                if cell[1] is None:
                    p = subprocess.run([binary, 'plan', files[name]], capture_output=True, text=True,
                                       timeout=timeout)
                    if p.returncode:
                        raise ValueError(p.stderr or p.stdout)
                    cell[1] = json.loads(p.stdout)
            return cell[1]

        def measure(task):
            kind, name = task
            try:
                if kind == 'quality':
                    return quality.one(files[name], binary, out / 'quality', timeout=timeout)[1]
                if kind == 'ap242':
                    return quality.one(files[name], binary, out / 'ap242', timeout=timeout)[1]
                if kind == 'survey':
                    # The survey's own per-call budget; review feeds only quality cells. The 34
                    # survey parts that are also quality or AP242 tasks run with review on and a
                    # 600 s budget instead; the survey verdict reads only their plans.
                    return quality.one(files[name], binary, out / 'survey', review=False,
                                       timeout=getattr(args, 'part_timeout', None) or 3600)[1]
                seed, pins = map(int, kind[5:].split('-'))
                spec, ids, candidates = quality.random_pins(unpinned(name), name, seed, pins)
                path = out / kind / 'pinned' / f'{name}.spec.json'
                path.parent.mkdir(parents=True, exist_ok=True)
                write(path, spec)
                after = quality.one(files[name], binary, out / kind / 'pinned', path, timeout=timeout)[1]
                return ({'error': after['error']} if 'error' in after
                        else {'ids': ids, 'candidates': candidates, 'pinned': after})
            except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
                return {'error': str(error)}

        start = time.monotonic()
        order = sorted(tasks, key=lambda t: -WEIGHTS.get(t[1], 10))
        with ThreadPoolExecutor(4) as pool:
            rows = dict(zip(order, pool.map(measure, order)))
        results = {}
        for (kind, name), row in rows.items():
            results.setdefault(kind, {})[name] = row
        write(out / 'shard.json', {'tier': tier, 'shard': [k, n], 'tasks': [list(t) for t in tasks], 'results': results,
                                   'binary_sha256': hashlib.sha256(Path(binary).read_bytes()).hexdigest()})
        failed = sorted(f'{kind}/{name}: {row["error"]}' for (kind, name), row in rows.items() if 'error' in row)
        record = {'name': f'quality-shard-{k}-{n}', 'tasks': len(tasks), 'passed': not failed,
                  'wall_seconds': round(time.monotonic() - start, 3)}
        if failed:
            record['error'] = '; '.join(failed)[:2000]
        self.steps.append(record)
        print(f'{record["name"]}: {"PASS" if record["passed"] else "FAIL"} ({len(tasks)} tasks, '
              f'{record["wall_seconds"]}s)', flush=True)

    def quality_gate(self, shards_dir):
        """Merge every quality-pins shard, then run the same-binary comparisons once."""
        import shutil
        args, out, binary = self.args, self.out, self.args.binary
        py = [sys.executable, '-I']
        tier = getattr(args, 'tier', 'full')
        record = {'name': 'shards', 'tier': tier}
        try:
            found = [(path.parent, read(path)) for path in sorted(Path(shards_dir).glob('**/shard.json'))]
            require(found, f'no quality-pins shard evidence under {shards_dir}')
            require(all(s.get('tier', 'full') == tier for _, s in found),
                    'shard tier differs from the requested gate tier')
            n = found[0][1]['shard'][1]
            seen = sorted(tuple(s['shard']) for _, s in found)
            require(seen == [(k, n) for k in range(1, n + 1)], f'incomplete/duplicate shards: {seen}')
            binary_sha256 = hashlib.sha256(Path(binary).read_bytes()).hexdigest()
            require(all(s.get('binary_sha256') == binary_sha256 for _, s in found),
                    'a shard measured a different binary')
            merged = {}
            for _, s in found:
                k = s['shard'][0]
                require([tuple(t) for t in s['tasks']] == shard_tasks(k, n, tier), f'shard {k}/{n} ran other tasks')
                for kind, name in shard_tasks(k, n, tier):
                    merged.setdefault(kind, {})[name] = s['results'][kind][name]
            expected = quality_tasks(tier)
            require(sum(map(len, merged.values())) == len(expected) and
                    all(name in merged.get(kind, {}) for kind, name in expected), 'shards miss a task')
            record.update(passed=True, shards=n, tasks=len(expected))
        except (OSError, ValueError, KeyError, TypeError) as error:
            record.update(passed=False, error=str(error))
        self.steps.append(record)
        detail = f' ({record["shards"]} shards, {record["tasks"]} tasks)' if record['passed'] else ''
        print(f'shards: {"PASS" if record["passed"] else "FAIL"}{detail}', flush=True)
        if not record['passed']:
            return
        quality_out = out / 'quality'
        quality_out.mkdir()
        for directory, _ in found:
            if (directory / 'quality').is_dir():
                shutil.copytree(directory / 'quality', quality_out, dirs_exist_ok=True)
        fresh = quality_out / 'baseline.json'
        write(fresh, merged['quality'])
        committed = read(args.baseline)
        self.verify('quality', 'quality', merged['quality'], committed)
        # AP242 source PMI is a crossing ratchet too. Keep its renders separate
        # from the fixed thirty-part quality/pin inventory, then share saved plans.
        ap242_errors = sorted(f'{name}: {row["error"]}' for name, row in merged['ap242'].items() if 'error' in row)
        self.steps.append({'name': 'ap242-quality', 'passed': not ap242_errors,
                           **({'error': '; '.join(ap242_errors)} if ap242_errors else {})})
        for directory, _ in found:
            for plan in sorted((directory / 'ap242').glob('*.plan.json')):
                (quality_out / plan.name).write_bytes(plan.read_bytes())
        self.step('leader-crossings', [*py, ROOT / 'tools/check_leader_crossings.py', quality_out,
                  '--report', out / 'leader-crossings.json'])
        self.step('leader-routes', [*py, ROOT / 'tools/check_leader_routes.py', quality_out,
                  '--report', out / 'leader-routes.json'])
        # The deliberate failure uses a real measured report, without changing the accepted baseline.
        bad = read(fresh)
        reference = committed['parts']
        part = sorted(reference)[0]
        bad[part]['ink'] = reference[part]['ink'] + 1
        write(out / 'deliberate-regression.json', bad)
        command = [*py, Path(__file__), '--check-results', 'quality', '--results',
                   out / 'deliberate-regression.json', '--baseline', args.baseline]
        with (out / 'deliberate-rejection.log').open('w') as log:
            rejected = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
        self.steps.append({'name': 'reject-deliberate-regression', 'passed': rejected.returncode == 1,
                           'observed_exit_code': rejected.returncode, 'expected_exit_code': 1})
        self.step('label-lane-locality', [*py, ROOT / 'tools/check_label_lanes.py',
                  '--current', quality_out, '--after-binary', binary])
        # The repeated-span survey: no placed linear span repeats on any of its 68 parts.
        verdicts = read(ROOT / 'tests/fixtures/known_repeated_spans.json')
        surveyed = {**merged['quality'], **merged['ap242'], **merged['survey']}
        (out / 'survey').mkdir()
        write(out / 'survey' / 'baseline.json', surveyed)
        if all(v['verdict'] == 'rust-correct' and v['evidence'].strip() for v in verdicts):
            self.verify('survey', 'survey', surveyed, None)
        else:
            self.steps.append({'name': 'survey', 'passed': False, 'error': 'unreviewed repeated-span verdict'})
        baseline = merged['quality']
        for cell in (PIN_CELLS if tier == 'full' else ()):
            seed, pins = map(int, cell.split('-'))
            parts = {}
            for name, row in merged[f'pins-{cell}'].items():
                before = baseline[name]
                if 'error' in before or 'error' in row:
                    parts[name] = {'error': before.get('error') or row['error'], 'go': False}
                    continue
                after = row['pinned']
                parts[name] = {'ids': row['ids'], 'candidates': row['candidates'],
                               'unpinned': before, 'pinned': after,
                               'ink_delta': after['ink'] - before['ink'],
                               'off_sheet_delta': after['off_sheet'] - before['off_sheet'],
                               'go': quality.pin_bar(before, after)}
            report = quality.pin_report(parts, seed, pins)
            (out / f'pins-{cell}').mkdir()
            write(out / f'pins-{cell}' / 'random-pins.json', report)
            self.verify(f'pins-{cell}', 'pins', report, baseline)

    def execute(self, job):
        args, out = self.args, self.out
        binary = args.binary
        py = [sys.executable, '-I']
        part_timeout = getattr(args, 'part_timeout', None)
        quality_args = ['--timeout', part_timeout] if part_timeout else []
        if job == 'quality-pins':
            self.quality_shard(*getattr(args, 'shard', (1, 1)))
        elif job == 'quality-gate':
            self.quality_gate(getattr(args, 'shards_dir', None) or out.parent)
        elif job == 'repeated-spans':
            survey_out = out / 'survey'
            k, n = getattr(args, 'survey_shard', (1, 1))
            self.step('survey', [*py, ROOT / 'tools/survey_repeated_spans.py',
                      '--timeout', part_timeout or 3600, '--shard', f'{k}/{n}', binary, survey_out],
                      'survey', survey_out / 'baseline.json', timeout=6600)
        elif job == 'plan-time':
            self.step('plan-time', [*py, ROOT / 'tools/check_plan_time.py',
                      '--binary', binary, '--corpus', args.corpus, '--out', out / 'plan-time.json'])
        elif job == 'render-tail':
            self.step('render-tail', [*py, ROOT / 'tools/check_render_tail.py',
                      '--binary', binary, '--corpus', args.corpus, '--out', out / 'render-tail.json'])
        else:
            self.step('python-quality-tests', [*py, ROOT / 'tools/test_quality.py'])
            self.step('acceptance-tests', [*py, ROOT / 'tools/test_ci_acceptance.py'])
            self.step('node-client-tests', ['node', '--test', 'tools/mcp-eval/test-client.mjs'])
            self.step('mcp', ['node', 'tools/mcp-eval/run-scripted.mjs', '--binary', binary,
                      '--out', out / 'mcp.json', '--documents-dir', out / 'mcp-documents'],
                      'mcp', out / 'mcp.json')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job', choices=(*JOBS, 'all'), action='append',
                        help='Run this job; repeat to run several in turn in one runner (default: all)')
    parser.add_argument('--shard', type=survey.shard_arg, default=(1, 1), metavar='K/N',
                        help='Measure one of N shards of the quality-pins tasks (quality-gate compares them)')
    parser.add_argument('--tier', choices=('full', 'pr'), default='full',
                        help='PR: all unpinned drawing checks and survey; full also requires all pin cells')
    parser.add_argument('--shards-dir', type=Path,
                        help='quality-gate: directory holding every quality-pins shard (default: --out)')
    parser.add_argument('--require-go', action='store_true',
                        help='Assert that this run enforces the full nine-cell random-pin grid: rejected '
                             'unless the full tier runs --job all or quality-gate')
    parser.add_argument('--survey-shard', type=survey.shard_arg, default=(1, 1), metavar='K/N',
                        help='Run one of N slices of the repeated-span survey')
    parser.add_argument('--part-timeout', type=int, help='Override local quality/survey operation timeouts')
    parser.add_argument('--step-timeout', type=int, help='Extend local job-step wall timeouts')
    parser.add_argument('--binary', type=Path, default=Path('target/release/draftwright'))
    parser.add_argument('--corpus', type=Path, default=os.environ.get('DRAFTWRIGHT_CORPUS'))
    parser.add_argument('--out', type=Path, default=Path('target/acceptance'))
    parser.add_argument('--baseline', type=Path, default=ROOT / 'tools/ci-quality-baseline.json')
    parser.add_argument('--check-results', choices=('quality', 'survey', 'pins', 'mcp'))
    parser.add_argument('--results', type=Path)
    parser.add_argument('--documents-dir', type=Path, help='Check saved MCP judge documents and PNGs')
    args = parser.parse_args()
    # quality-gate holds the survey verdict, so `all` leaves the standalone survey out.
    everything = [job for job in JOBS if job != 'repeated-spans']
    jobs = everything if not args.job or 'all' in args.job else list(dict.fromkeys(args.job))
    args.job = 'all' if not args.job or 'all' in args.job else args.job[0] if len(jobs) == 1 else 'several'
    if any(t is not None and t <= 0 for t in (args.part_timeout, args.step_timeout)):
        parser.error('timeouts must be positive')
    if args.tier == 'pr' and (args.job not in ('quality-pins', 'quality-gate') or args.check_results):
        parser.error('--tier pr requires --job quality-pins or quality-gate')
    if args.require_go and (args.tier != 'full' or args.job not in ('all', 'quality-gate') or args.check_results):
        parser.error('--require-go requires the full random-pin grid: --job all or quality-gate')
    if args.shard != (1, 1) and args.job != 'quality-pins':
        parser.error('--shard requires --job quality-pins')
    if args.shards_dir and args.job != 'quality-gate':
        parser.error('--shards-dir requires --job quality-gate')
    if args.survey_shard != (1, 1) and args.job != 'repeated-spans' and args.check_results != 'survey':
        parser.error('--survey-shard requires --job repeated-spans')
    if args.check_results:
        try:
            print(json.dumps(validate(args.check_results, read(args.results), read(args.baseline),
                                      args.survey_shard), sort_keys=True))
            if args.documents_dir:
                mcp_documents(args.documents_dir)
            return 0
        except (OSError, ValueError, KeyError, TypeError) as error:
            print(f'acceptance: {error}', file=sys.stderr)
            return 1
    args.out = args.out.resolve()
    require(not args.out.exists() or not any(args.out.iterdir()), 'use a fresh/empty output directory')
    args.out.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    summary = {'format': 'draftwright.ci-acceptance/1', 'passed': False, 'jobs': {},
               'tier': args.tier, 'corpus_revision': CORPUS_REV, 'corpus_revision_note': 'CI checkout pin; local files hashed below'}
    try:
        args.binary = args.binary.resolve()
        args.baseline = args.baseline.resolve()
        if args.shards_dir:
            args.shards_dir = args.shards_dir.resolve()
        require(args.binary.is_file() and os.access(args.binary, os.X_OK), 'release binary missing or not executable')
        require(args.corpus is not None, 'DRAFTWRIGHT_CORPUS or --corpus is required')
        args.corpus = args.corpus.resolve()
        os.environ['DRAFTWRIGHT_CORPUS'] = str(args.corpus)
        required = [args.corpus / p for p in manifest('repeated_span_parts.txt')]
        required += [ROOT / 'tests/fixtures/parts/mounting_plate.step']
        required += sorted((ROOT / 'tests/fixtures/pmi/step').glob('*.gz'))
        required += [ROOT / 'tools/mcp-eval/features' / (name + '.features.json')
                     for name in ('plate', '10127', 'GRM-03_thumbwheel_drive_screw', 'nist_ctc_01_asme1_rd')]
        survey_names()
        for path in required:
            require(path.is_file(), f'missing fixture: {path}')
        digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
        summary['binary_sha256'] = digest(args.binary)
        summary['fixture_sha256'] = {str(p.relative_to(args.corpus) if p.is_relative_to(args.corpus)
                                         else p.relative_to(ROOT)): digest(p) for p in required}
        for job in jobs:
            out = args.out / job
            out.mkdir()
            run = Run(args, out)
            began = time.monotonic()
            run.execute(job)
            result = {'passed': all(s['passed'] for s in run.steps), 'steps': run.steps,
                      'wall_seconds': round(time.monotonic() - began, 3)}
            write(out / 'job.json', result)
            summary['jobs'][job] = result
        require(digest(args.binary) == summary['binary_sha256'], 'binary changed during acceptance')
        summary['passed'] = all(j['passed'] for j in summary['jobs'].values())
    except (OSError, ValueError, KeyError, TypeError) as error:
        summary['error'] = str(error)
        print(f'acceptance: {error}', file=sys.stderr)
    summary['wall_seconds'] = round(time.monotonic() - start, 3)
    write(args.out / 'summary.json', summary)
    print(f'acceptance: {"PASS" if summary["passed"] else "FAIL"} ({summary["wall_seconds"]}s)', flush=True)
    return 0 if summary['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
