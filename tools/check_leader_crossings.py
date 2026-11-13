"""Per-part crossing ratchet on 30 quality drawings and the AP242 CTC/FTC drawings; no placement changes.

Record from origin/main quality.py drawings with this single command (also after
#141 merges, if this stream merges second):
  python3 -I tools/check_leader_crossings.py /tmp/main-quality --record tools/leader-crossings-baseline.json --reason 'review reason'
Generate /tmp/main-quality with quality.py and an origin/main release binary first.
The geometry fallback classifies old plans without requiring the new problem fields.
CI's quality-gate job compares fresh drawings to this record; zero stays zero,
nonzero never increases, independently for each part and each of the six kinds.
Counts retain the existing ordered-pair semantics; multi-kind pairs count in each
applicable column, not in a partition of the old warning total. Leader-through-text
also reads placed geometry, so annotation_overlap warning deduplication cannot hide it.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('quality', ROOT / 'tools/quality.py')
quality = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quality)
FORMAT = 'draftwright.leader-crossings/1'


def fixture_paths():
    return sorted({* (ROOT / 'tests/fixtures/pmi/step').glob('nist_ctc_0*_ap242*.gz'),
                   * (ROOT / 'tests/fixtures/pmi/step').glob('nist_ftc_*_ap242*.gz')})


def part_names():
    files = (ROOT / 'tools/quality_parts.txt').read_text().splitlines()
    names = [Path(p).stem for p in files]
    if len(names) != 30 or len(set(names)) != 30:
        raise ValueError('incomplete/ambiguous thirty-part manifest')
    names += [Path(p.stem).stem for p in fixture_paths()]
    if len(names) != len(set(names)) or not fixture_paths():
        raise ValueError('incomplete/ambiguous AP242 crossing manifest')
    return names


def collect(directory):
    return {name: quality.crossing_counts(json.loads((directory / (name + '.plan.json')).read_text()))
            for name in part_names()}


def check(before, after):
    if set(before) != set(after) or set(after) != set(part_names()):
        raise ValueError('incomplete quality/AP242 inventory')
    failures = []
    for part in sorted(before):
        for kind in quality.CROSSING_KEYS:
            a, b = before[part].get(kind), after[part].get(kind)
            if type(a) is not int or type(b) is not int or min(a, b) < 0:
                raise ValueError(f'{part}: missing/invalid {kind}')
            if b > a:
                failures.append(f'{part}: {kind} regressed {a} -> {b}')
    return failures


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('drawings', type=Path)
    p.add_argument('--baseline', type=Path, default=ROOT / 'tools/leader-crossings-baseline.json')
    p.add_argument('--record', type=Path)
    p.add_argument('--reason', help='Required review reason for --record')
    p.add_argument('--base-ref', default='origin/main', help='Source revision of drawings when recording')
    p.add_argument('--report', type=Path)
    args = p.parse_args()
    if args.record and (not args.reason or not args.reason.strip()):
        p.error('--record requires a nonempty --reason')
    if args.reason and not args.record:
        p.error('--reason requires --record')
    parts = collect(args.drawings)
    if args.record:
        revision = subprocess.check_output(['git', 'rev-parse', args.base_ref], cwd=ROOT, text=True).strip()
        args.record.write_text(json.dumps(dict(format=FORMAT, base=revision,
            reason=args.reason.strip(), parts=parts), indent=2, sort_keys=True) + '\n')
        return 0
    baseline = json.loads(args.baseline.read_text())
    if baseline.get('format') != FORMAT:
        raise ValueError('incompatible crossing baseline')
    failures = check(baseline['parts'], parts)
    report = dict(parts=parts, totals={k: sum(r[k] for r in parts.values()) for k in quality.CROSSING_KEYS}, failures=failures)
    if args.report:
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    print('\n'.join(failures) if failures else f'PASS: {len(parts)} parts × 6 crossing kinds; zero where zero, never more')
    return bool(failures)


if __name__ == '__main__':
    raise SystemExit(main())
