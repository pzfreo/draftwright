"""Drawing quality for draftwright-rust: per part, items off the sheet, repeated dimensions,
overlapping views, and ink overlaps, from `draftwright plan` and `render`. Writes baseline.json
in <out dir> and prints a table with totals. Random pins: --seed S --pins N;
--require-go enforces the per-part 1.5x ink / no new off-sheet items bar.

The maintainer's priority (2026-10-09) is to drive these to zero before adding features; see
docs/quality-2026-10-09.md for the baseline and the parts it is measured on.

Repeated spans use placed linear arrow tips, model-mm tolerance 1e-6 for ends and values,
and ignore lane and label. PMI face-pair near misses are included. Use
--require-no-repeated-span to enforce zero; baseline.json retains the groups' item IDs.

Usage: python3 -I tools/quality.py <draftwright binary> <out dir> <step files...>
(the parts: tools/quality_parts.txt, paths relative to $DRAFTWRIGHT_CORPUS)"""
import argparse, collections, hashlib, json, subprocess, sys, os, re, math
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

INK = {"annotation_ink_overlap", "annotation_over_view", "annotation_line_over_view",
       "annotation_leader_crossing", "annotation_outside_area", "label_centerline_overlap",
       "leader_centerline_overlap", "label_section_furniture_overlap",
       "oblique_witness_crossing", "radius_leader_target_mismatch",
       "radius_leader_target_unverifiable"}


# Ordered item pairs, like annotation_leader_crossing itself. A pair can meet
# several kinds; do not add these columns to recover the old warning count.
CROSSING_KEYS = ('leader_leader', 'leader_text', 'leader_dimension_line',
                 'leader_extension_line', 'leader_arrow', 'dimension_dimension')


def segments_cross(a, b):
    """The planner's proper-interior crossing predicate (leaders::segments_cross)."""
    def side(p, q, r):
        return (q[0]-p[0])*(r[1]-p[1]) - (q[1]-p[1])*(r[0]-p[0])
    def opposite(x, y):
        return (x > 1e-9 and y < -1e-9) or (x < -1e-9 and y > 1e-9)
    return opposite(side(*b, a[0]), side(*b, a[1])) and opposite(side(*a, b[0]), side(*a, b[1]))


def lines_meet(a, b):
    """Planner contact: proper crossing, or within 1 mm for at least 0.5 mm."""
    if segments_cross(a, b):
        return True
    d = [a[1][k]-a[0][k] for k in (0, 1)]
    length = math.hypot(*d)
    if length < 1e-9:
        return False
    u = [v/length for v in d]
    q = [[p[k]-a[0][k] for k in (0, 1)] for p in b]
    if any(abs(p[0]*u[1]-p[1]*u[0]) > 1.0 for p in q):
        return False
    ends = sorted(sum(p[k]*u[k] for k in (0, 1)) for p in q)
    return min(ends[1], length) - max(ends[0], 0.0) >= 0.5


def line_through_label(line, box):
    """placement::crosses uses an open box and a nonzero clipped length."""
    lo, hi = 0., 1.
    for k in (0, 1):
        d = line[1][k] - line[0][k]
        if d == 0.:
            if not box[k] < line[0][k] < box[k+2]:
                return False
        else:
            ends = sorted(((box[k]-line[0][k])/d, (box[k+2]-line[0][k])/d))
            lo, hi = max(lo, ends[0]), min(hi, ends[1])
    return hi-lo > 1e-9


def crossing_instances(plan):
    """Read additive metadata, or measure old origin/main plans for --record.

    Builders place the dimension stroke at lines[2]; lines[:2] and any appended
    clipped continuations are extensions. Callout lines are shaft/shoulder.
    Arrow shafts have their own array. Preserve the original ordered-pair warning
    semantics, counting each kind once per pair, and each linear/linear pair once.
    """
    result = {k: [] for k in CROSSING_KEYS}
    dims = {d['id']: d for d in plan['dimensions'] if d.get('placed')}
    # gdt::proxy exposes datum/frame leaders to the same collision pass. Keep
    # these in old-plan recording too, including #141's default datum leaders.
    dims.update({g['id']: {**g, 'kind': 'callout'} for g in plan.get('gdt', []) if g.get('placed')})
    for r in plan.get('requirements', []):
        placed = r.get('placed')
        if placed:
            # A datum symbol records its glyph box; a surface-texture symbol its label_box.
            box = placed.get('glyph') or placed.get('label_box') or placed['box']
            dims[r['id']] = {**r, 'kind': 'symbol', 'view': '', 'placed': {
                'lines': placed['strokes'], 'arrows': [], 'label_box': box}}
    for p in plan.get('problems', []):
        if p['code'] not in ('annotation_leader_crossing', 'annotation_ink_overlap'):
            continue
        a, b = (dims.get(i) for i in p['subjects'][:2])
        if p['code'] == 'annotation_ink_overlap':
            leader = p.get('ink_is_leader')
            if leader is None:
                if not a or not b:
                    raise ValueError(f"missing collision geometry: {p['subjects']}")
                pa, pb = a['placed'], b['placed']
                leader = a['kind'] == 'callout' and any(line_through_label(l, pb['label_box']) for l in pa['lines'])
            if leader:
                result['leader_text'].append(p['subjects'][:2])
            continue
        kinds = p.get('crossing_kinds')
        if kinds is None:
            if not a or not b:
                raise ValueError(f"missing collision geometry: {p['subjects']}")
            pa, pb = a['placed'], b['placed']
            kinds = set()
            for i, line in enumerate(pb['lines']):
                if any(lines_meet(s, line) for s in pa['lines']):
                    kinds.add('leader' if b['kind'] == 'callout' else
                              'extension_line' if ((b['kind'] == 'linear' and i != 2) or
                                                   (b['kind'] == 'angular' and i < 2)) else 'dimension_line')
            if any(lines_meet(s, t) for s in pa['lines'] for t in pb.get('arrows', [])):
                kinds.add('arrow')
        for kind in kinds:
            result['leader_' + kind].append(p['subjects'][:2])
    linear = [d for d in dims.values() if d['kind'] == 'linear' and len(d['placed']['lines']) >= 3]
    sheets = {v['id']: v.get('sheet', 1) for v in plan.get('views', []) if 'id' in v}
    # annotation_overlap deliberately suppresses annotation_ink_overlap for the
    # same neighbourhood. That deduplication must not hide a leader through text
    # from the professional-drawing gate. Count each directed pair once.
    text = {tuple(pair) for pair in result['leader_text']}
    for a in dims.values():
        if a['kind'] != 'callout':
            continue
        for b in dims.values():
            if (a['id'] != b['id'] and sheets.get(a['view'], 1) == sheets.get(b['view'], 1)
                    and any(line_through_label(line, b['placed']['label_box'])
                            for line in a['placed']['lines'])):
                text.add((a['id'], b['id']))
    result['leader_text'] = [list(pair) for pair in sorted(text)]
    for i, a in enumerate(linear):
        for b in linear[i+1:]:
            if sheets.get(a['view'], 1) == sheets.get(b['view'], 1) and segments_cross(
                    a['placed']['lines'][2], b['placed']['lines'][2]):
                result['dimension_dimension'].append([a['id'], b['id']])
    return result


def crossing_counts(plan):
    return {k: len(v) for k, v in crossing_instances(plan).items()}


def line_through_features(plan):
    """Leader/feature pairs, plus extension lines (once even if several features are crossed)."""
    leaders, extensions = set(), set()
    for p in plan.get("problems", []):
        subjects = p.get("subjects", [])
        if p["code"] == "leader_through_feature":
            leaders.add(tuple(subjects))
        elif p["code"] == "extension_through_feature":
            extensions.add((subjects[0], p.get("id", "").rsplit(":line:", 1)[-1]))
    return len(leaders) + len(extensions)


def interior_labels(plan):
    """Drawn labels with the planner's proven interior candidate provenance."""
    return sum(d.get('placed') is not None and d.get('region') == 'interior'
               for d in plan['dimensions'])


def sheet_escalations(plan):
    """Count selected larger sheets, independently of scale-only recovery trials."""
    size = plan.get('sheet', {}).get('size', '')
    for problem in plan.get('problems', []):
        if problem['code'] == 'layout_replanned':
            start = re.match(r'The sheet was re-planned: (A[0-4]) at ', problem.get('message', ''))
            if start and re.fullmatch(r'A[0-4]', size) and int(size[1]) < int(start[1][1]):
                return 1
    return 0


def segment_box(line, b):
    """Whether the segment *line* meets the box *b* (Liang-Barsky clip)."""
    lo, hi = 0.0, 1.0
    for k in range(2):
        d = line[1][k] - line[0][k]
        if abs(d) < 1e-9:
            if line[0][k] < b[k] or line[0][k] > b[k + 2]:
                return False
        else:
            t1, t2 = (b[k] - line[0][k]) / d, (b[k + 2] - line[0][k]) / d
            lo, hi = max(lo, min(t1, t2)), min(hi, max(t1, t2))
    return lo <= hi


def leader_on_extension(plan, pad=1.0):
    """Callout/linear pairs where the callout's leader, or its label within a 1 mm pad, touches
    the linear dimension's extension line (itself grown by the pad): the maintainer's decision
    of 2026-10-12, measured as the placer does (`dims::leaders`)."""
    extensions, leaders = [], []
    pages = {v["id"]: v.get("sheet", 1) for v in plan.get("views", []) if "id" in v}
    for d in plan.get("dimensions", []):
        placed = d.get("placed") or {}
        lines, arrows = placed.get("lines") or [], placed.get("arrows") or []
        if len(lines) >= 3 and len(arrows) == 2:
            extensions += [(d["id"], pages.get(d.get("view"), 1), line) for line in lines[:2]]
        elif d.get("kind") == "callout" and lines and placed.get("label_box"):
            leaders.append((d["id"], pages.get(d.get("view"), 1), lines, placed["label_box"]))
    pairs = set()
    for name, page, lines, b in leaders:
        padded = [b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad]
        for other, other_page, e in extensions:
            if other == name or other_page != page:
                continue
            grown = [min(e[0][0], e[1][0]) - pad, min(e[0][1], e[1][1]) - pad,
                     max(e[0][0], e[1][0]) + pad, max(e[0][1], e[1][1]) + pad]
            if segment_box(e, padded) or any(segment_box(l, grown) for l in lines):
                pairs.add((name, other))
    return len(pairs)


def overlap(a, b):
    return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]


def text_off_span(plan):
    """Linear dimensions whose label is off its own span (paper mm): not between its arrow tips,
    and either at least ARROW + 3 mm (2.7 + 3) from the nearer tip, or with another linear
    dimension's extension line in the same view between it and the span. The planner's own
    outside place is ARROW + 1 mm from the tip; its old one, exactly ARROW + 3 mm, counts."""
    linear = [d for d in plan['dimensions'] if d.get('kind') == 'linear'
              and d.get('placed') and len(d['placed'].get('arrows', [])) == 2]
    bad = []
    for d in linear:
        p = d['placed']; k = 0 if p['rotation'] % 180 == 0 else 1
        lo, hi = sorted(a[0][k] for a in p['arrows'])
        b = p['label_box']
        if b[k] >= lo - 1e-6 and b[k + 2] <= hi + 1e-6:
            continue
        gap = max(lo - b[k + 2], b[k] - hi, 0)
        t = p['arrows'][0][0][1-k]
        normal = (min(t, b[1-k]), max(t, b[3-k]))
        foreign = any(abs(a[k]-z[k]) <= 1e-6 and ((b[k] + 1e-6 < a[k] < lo - 1e-6)
                      or (hi + 1e-6 < a[k] < b[k + 2] - 1e-6))
                      and min(a[1-k], z[1-k]) <= normal[1]
                      and max(a[1-k], z[1-k]) >= normal[0]
                      for q in linear if q['id'] != d['id'] and q['view'] == d['view']
                      for i, (a, z) in enumerate(q['placed']['lines']) if i != 2)
        if gap >= 2.7 + 3.0 - 1e-6 or foreign:
            bad.append(d['id'])
    return sorted(bad)


def repeated_spans(plan):
    """Placed linear statements, independent of lane and label, in model millimetres.

    Arrow tips retain the witness stations even with outside arrows or a broken line.
    PMI identities also prove a face pair: equal values on that pair at different
    stations are reported separately for review, never inferred from equal labels.
    """
    numerator, denominator = map(float, plan['sheet']['scale'].split(':'))
    scale = numerator / denominator
    spans = []
    for d in sorted(plan['dimensions'], key=lambda d: d['id']):
        placed = d.get('placed')
        if d.get('kind') != 'linear' or not placed or len(placed.get('arrows', [])) != 2:
            continue
        axis = 0 if placed['rotation'] % 180 == 0 else 1
        ends = sorted(a[0][axis] / scale for a in placed['arrows'])
        faces = set()
        for m in d.get('measurements', []):
            if m['feature'].startswith('pmi:'):
                match = re.search(r':f([0-9,]+)(?=:|$)', m['feature'])
                if match:
                    pair = tuple(sorted(map(int, match[1].split(','))))
                    if len(pair) == 2:
                        part = re.search(r':p([0-9]+)(?=:|$)', m['feature'])
                        faces.add((part[1] if part else None, pair))
        spans.append((d, axis, ends, faces))
    groups = []
    for d, axis, ends, faces in spans:
        matches = []
        for i, group in enumerate(groups):
            for other, k, stations, pair in group['members']:
                if d['view'] != other['view'] or axis != k or abs(d['value'] - other['value']) > 1e-6:
                    continue
                same = all(abs(a - b) <= 1e-6 for a, b in zip(ends, stations))
                if same or faces & pair:
                    matches.append((i, 'span' if same else 'feature_pair'))
                    break
        if matches:
            first, kind = matches[0]
            groups[first]['members'].append((d, axis, ends, faces))
            if kind == 'feature_pair':
                groups[first]['kind'] = kind
            for i, kind in reversed(matches[1:]):
                groups[first]['members'].extend(groups[i]['members'])
                if kind == 'feature_pair' or groups[i]['kind'] == 'feature_pair':
                    groups[first]['kind'] = 'feature_pair'
                del groups[i]
        else:
            groups.append({'kind': 'span', 'members': [(d, axis, ends, faces)]})
    return sorted(({'kind': g['kind'], 'items': sorted(d['id'] for d, *_ in g['members'])}
                   for g in groups if len(g['members']) > 1), key=lambda g: g['items'])


def schedule_ink(plan, segments):
    """Count schedule labels hit by visible/hidden edges, labels or annotation strokes.

    Label boxes are resolved by the planner with the drawing font. Each marker counts once,
    including origin glyphs; coaxial rows share one marker. Furniture receives no exemption.
    """
    def crosses(a, b, box):
        lo, hi = 0., 1.
        for k in (0, 1):
            d = b[k] - a[k]
            if abs(d) < 1e-12:
                if a[k] < box[k] or a[k] > box[k + 2]: return False
            else:
                x, y = (box[k] - a[k]) / d, (box[k + 2] - a[k]) / d
                lo, hi = max(lo, min(x, y)), min(hi, max(x, y))
        return lo < hi
    markers = [b for b in plan.get('balloons', [])
               if b['id'].startswith('hole_schedule_') and b.get('label_box')]
    labels = [(d['id'], d['placed']['label_box']) for d in plan.get('dimensions', []) if d.get('placed')]
    labels += [(b['id'], b['label_box']) for b in markers]
    labels += [(g['id'], g['placed']['label_box']) for g in plan.get('gdt', []) if g.get('placed')]
    tables = plan.get('tables', []) + [b for t in plan.get('hole_tables', []) for b in t['blocks']]
    labels += [(t['id'], t['placed']['box']) for t in tables if t.get('placed')]
    if plan.get('notes_block'):
        labels.append(('notes', plan['notes_block']['box']))
    if plan.get('sheet', {}).get('title_block'):
        labels.append(('title_block', plan['sheet']['title_block']))
    strokes = [(d['id'], l) for d in plan.get('dimensions', []) if d.get('placed')
               for l in d['placed'].get('lines', []) + d['placed'].get('arrows', [])]
    strokes += [(b['id'], b['leader']) for b in markers if b.get('leader')]
    strokes += [(g['id'], line) for g in plan.get('gdt', []) if g.get('placed')
                for line in g['placed'].get('strokes', []) + g['placed'].get('lines', []) + g['placed'].get('arrows', [])]
    return sum(any(overlap(b['label_box'], box) for id_, box in labels if id_ != b['id'])
               or any(crosses(a, c, b['label_box']) for a, c in segments)
               or any(crosses(*line, b['label_box']) for id_, line in strokes if id_ != b['id'])
               for b in markers)


def local_sheet_plan(plan, sheet):
    """Use the renderer's ownership rules when measuring separate physical pages."""
    number = sheet.get('number', 1)
    local = {**plan, 'sheet': sheet, 'sheets': []}
    local['views'] = [v for v in plan['views'] if v.get('sheet', 1) == number]
    owners = {v['id'] for v in local['views']}
    for key in ('dimensions', 'gdt', 'balloons'):
        local[key] = [d for d in plan.get(key, []) if d.get('view') in owners]
    for key in ('captions', 'tables', 'hole_tables'):
        local[key] = [t for t in plan.get(key, []) if t.get('sheet', 1) == number]
    table = plan.get('hole_table')
    local['hole_table'] = table if table and table.get('sheet', 1) == number else None
    if number != 1:
        for key in ('notes', 'requirements'):
            local[key] = []
        for key in ('notes_block', 'radius_notes_block', 'radius_table'):
            local[key] = None
    return local


def sheet_svg(path, number):
    stem, extension = os.path.splitext(path)
    return path if number == 1 else f'{stem}.sheet{number}{extension}'


def view_overlap_counts(plan):
    overlaps, title = 0, 0
    for sheet in [plan['sheet'], *plan.get('sheets', [])]:
        number = sheet.get('number', 1)
        boxes = [v['area'] for v in plan['views'] if v.get('area') and v.get('sheet', 1) == number]
        overlaps += sum(overlap(a,b) for i,a in enumerate(boxes) for b in boxes[i+1:])
        if sheet.get('title_block'):
            title += sum(overlap(b,sheet['title_block']) for b in boxes)
    return overlaps, title


def svg_linework(path, plan):
    if not plan.get('sheets'):
        return _svg_linework_page(path, plan)
    counts = collections.Counter()
    for sheet in [plan['sheet'], *plan['sheets']]:
        local = local_sheet_plan(plan, sheet)
        counts.update(_svg_linework_page(sheet_svg(path,sheet.get('number',1)),local))
    return dict(counts)


def _svg_linework_page(path, plan):
    """Count SVG edge elements (line/path/ellipse), in page mm, per placed view.

    Distance is to visible ink (half the SVG stroke width), plus 0.15 mm; dashes are
    measured as their underlying path. Curves are flattened to 0.002 mm chord
    error. Isometric edges are included, although their rendering is unchanged.
    """
    def flatten(at, end, f, depth=0):
        middle = f(0.5)
        flat = all(math.dist(f(t), [at[k]+t*(end[k]-at[k]) for k in (0,1)]) <= 0.002
                   for t in (.25,.5,.75))
        if depth >= 20 or flat:
            return [at, end]
        return (flatten(at, middle, lambda t: f(t / 2), depth + 1)[:-1]
                + flatten(middle, end, lambda t: f((t + 1) / 2), depth + 1))

    def points(element):
        tag = element.tag.rsplit('}', 1)[-1]
        a = element.attrib
        if tag == 'line':
            return [(float(a['x1']), float(a['y1'])), (float(a['x2']), float(a['y2']))]
        if tag in ('circle', 'ellipse'):
            rx, ry = float(a.get('rx', a.get('r', 0))), float(a.get('ry', a.get('r', 0)))
            cx, cy = float(a['cx']), float(a['cy'])
            f = lambda t: [cx + rx * math.cos(t * 2 * math.pi), cy + ry * math.sin(t * 2 * math.pi)]
            return sum((flatten(f(i/4), f((i+1)/4), lambda t, i=i: f((i+t)/4))[:-1]
                        for i in range(4)), []) + [f(1)]
        if tag != 'path':
            return []
        tokens = re.findall(r'[A-Za-z]|[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?', a['d'])
        out, at, i = [], [0, 0], 0
        while i < len(tokens):
            command = tokens[i]; i += 1
            n = {'M': 2, 'L': 2, 'Q': 4, 'C': 6, 'A': 7, 'Z': 0}[command]
            values = list(map(float, tokens[i:i+n])); i += n
            if command == 'Z':
                out.append(out[0]); continue
            end = values[-2:]
            if command in ('M', 'L'):
                out.append(end)
            elif command in ('Q', 'C'):
                poles = [at] + [values[k:k+2] for k in range(0, n, 2)]
                def bezier(t):
                    q = poles
                    while len(q) > 1:
                        q = [[(1-t)*a[k]+t*b[k] for k in (0,1)] for a,b in zip(q,q[1:])]
                    return q[0]
                out.extend(flatten(at, end, bezier)[1:])
            else:
                rx, ry, rotation, large, sweep = values[:5]
                phi = math.radians(rotation); c, s = math.cos(phi), math.sin(phi)
                dx, dy = (at[0]-end[0])/2, (at[1]-end[1])/2
                x, y = c*dx+s*dy, -s*dx+c*dy
                factor = max(1, math.sqrt((x/rx)**2+(y/ry)**2))
                rx *= factor; ry *= factor
                k = math.sqrt(max(0, (rx*ry)**2-(rx*y)**2-(ry*x)**2)
                              / max(1e-30, (rx*y)**2+(ry*x)**2))
                if bool(large) == bool(sweep): k = -k
                ux, uy = k*rx*y/ry, -k*ry*x/rx
                cx, cy = (at[0]+end[0])/2+c*ux-s*uy, (at[1]+end[1])/2+s*ux+c*uy
                start = math.atan2((y-uy)/ry, (x-ux)/rx)
                stop = math.atan2((-y-uy)/ry, (-x-ux)/rx)
                angle = (stop-start) % (2*math.pi)
                if not sweep: angle -= 2*math.pi
                f = lambda t: [cx+c*rx*math.cos(start+angle*t)-s*ry*math.sin(start+angle*t),
                               cy+s*rx*math.cos(start+angle*t)+c*ry*math.sin(start+angle*t)]
                steps = max(1, math.ceil(abs(angle)/(math.pi/2)))
                for j in range(steps):
                    out.extend(flatten(f(j/steps), f((j+1)/steps),
                                       lambda t, j=j: f((j+t)/steps))[1:])
            at = end
        return out

    root = ET.parse(path).getroot()
    layers = {g.attrib.get('id'): [points(e) for e in g]
              for g in root.iter() if g.attrib.get('id') in ('part', 'hidden')}
    visible = layers.get('part', [])
    tolerance = .15 + next((float(g.attrib.get('stroke-width', 0))/2
                            for g in root.iter() if g.attrib.get('id') == 'part'), 0)
    counts = {'hidden_on_visible': 0, 'section_hidden': 0}
    if any(b['id'].startswith('hole_schedule_') for b in plan.get('balloons', [])):
        counts['schedule_ink'] = schedule_ink(plan, [s for curves in layers.values() for curve in curves for s in zip(curve, curve[1:])])
    def view_of(p):
        return next((v for v in plan['views'] if v.get('area') and
                     v['area'][0] <= p[0] <= v['area'][2] and
                     v['area'][1] <= p[1] <= v['area'][3]), None)
    by_view = collections.defaultdict(list)
    for curve in visible:
        if len(curve) >= 2:
            view = view_of(curve[len(curve)//2])
            if view:
                by_view[view['id']].extend(zip(curve, curve[1:]))
    grids = {}
    def cells(a, b, pad=0):
        return ((x,y) for x in range(math.floor((min(a[0],b[0])-pad)/2), math.floor((max(a[0],b[0])+pad)/2)+1)
                for y in range(math.floor((min(a[1],b[1])-pad)/2), math.floor((max(a[1],b[1])+pad)/2)+1))
    for view, segments in by_view.items():
        grid = collections.defaultdict(set)
        for i, (a,b) in enumerate(segments):
            for cell in cells(a,b,tolerance): grid[cell].add(i)
        grids[view] = grid
    for curve in layers.get('hidden', []):
        if len(curve) < 2: continue
        view = view_of(curve[len(curve)//2])
        if not view: continue
        counts['section_hidden'] += bool(view.get('cut'))
        if sum(math.dist(a,b) for a,b in zip(curve,curve[1:])) < 1-1e-9:
            continue
        segments = by_view[view['id']]; grid = grids.get(view['id'], {})
        length = 0
        for a,b in zip(curve,curve[1:]):
            candidates = set().union(*(grid.get(cell,set()) for cell in cells(a,b)))
            intervals = []
            vx, vy = b[0]-a[0], b[1]-a[1]
            vv = vx*vx+vy*vy
            if not vv: continue
            for i in candidates:
                c,d = segments[i]
                if any(max(a[k],b[k]) < min(c[k],d[k])-tolerance or
                       min(a[k],b[k]) > max(c[k],d[k])+tolerance for k in (0,1)):
                    continue
                wx, wy = d[0]-c[0], d[1]-c[1]
                ww = wx*wx+wy*wy
                def clip(value, slope, lower, upper, lo, hi):
                    if abs(slope) < 1e-15:
                        return (lo,hi) if lower <= value <= upper else (0,0)
                    u,v = (lower-value)/slope, (upper-value)/slope
                    return max(lo,min(u,v)), min(hi,max(u,v))
                if ww:
                    ax, ay = a[0]-c[0], a[1]-c[1]
                    lo,hi = clip(ax*wx+ay*wy,vx*wx+vy*wy,0,ww,0,1)
                    band = tolerance*math.sqrt(ww)
                    lo,hi = clip(ax*wy-ay*wx,vx*wy-vy*wx,-band,band,lo,hi)
                    if hi>lo: intervals.append((lo,hi))
                for end in (c,d):
                    ax,ay = a[0]-end[0],a[1]-end[1]
                    dot = ax*vx+ay*vy
                    disc = tolerance**2-ax*ax-ay*ay+dot*dot/vv
                    if disc>=0:
                        half = math.sqrt(disc/vv)
                        lo,hi = max(0,-dot/vv-half),min(1,-dot/vv+half)
                        if hi>lo: intervals.append((lo,hi))
            stop = 0
            for lo,hi in sorted(intervals):
                if hi>stop:
                    length += max(0,hi-max(lo,stop))*math.sqrt(vv)
                    stop = hi
            if length >= 1-1e-9: break
        counts['hidden_on_visible'] += length >= 1-1e-9
    return counts


def text_size(text, font, size):
    """Measure bundled TrueType glyph boxes and advances without a font dependency."""
    import struct
    from pathlib import Path
    data = (Path(__file__).resolve().parent.parent / 'fonts' / font).read_bytes()
    u16 = lambda offset: struct.unpack_from('>H', data, offset)[0]
    i16 = lambda offset: struct.unpack_from('>h', data, offset)[0]
    u32 = lambda offset: struct.unpack_from('>I', data, offset)[0]
    tables = {data[at:at+4].decode(): u32(at+8)
              for at in range(12, 12 + 16*u16(4), 16)}
    cmap = tables['cmap']
    offsets = [cmap + u32(at+4) for at in range(cmap+4, cmap+4+8*u16(cmap+2), 8)]
    sub = next(at for at in offsets if u16(at) == 4)
    count = u16(sub+6)//2
    ends = sub+14; starts = ends+2*count+2
    deltas = starts+2*count; ranges = deltas+2*count
    def glyph(char):
        code = ord(char)
        for k in range(count):
            if u16(starts+2*k) <= code <= u16(ends+2*k):
                delta = i16(deltas+2*k); offset = u16(ranges+2*k)
                value = u16(ranges+2*k+offset+2*(code-u16(starts+2*k))) if offset else code
                return (value+delta)&65535 if value else 0
        return 0
    long_loca = i16(tables['head']+50)
    def location(g):
        return u32(tables['loca']+4*g) if long_loca else 2*u16(tables['loca']+2*g)
    pen = 0; boxes = []
    metrics = u16(tables['hhea']+34)
    for char in text:
        g = glyph(char); start = location(g)
        if start != location(g+1):
            at = tables['glyf']+start
            boxes.append([pen+i16(at+2), i16(at+4), pen+i16(at+6), i16(at+8)])
        pen += u16(tables['hmtx']+4*min(g,metrics-1))
    scale = size/u16(tables['head']+18)
    return [(max(b[k+2] for b in boxes)-min(b[k] for b in boxes))*scale
            for k in range(2)] if boxes else [0,0]


def furniture_boxes(a, p):
    def text_box(text, font, size, at):
        w,h = text_size(text,font,size)
        return [at[0]-w/2,at[1]-h/2,at[0]+w/2,at[1]+h/2]
    tb = p['sheet']['title_block']; scale = 'SCALE '+p['sheet']['scale']
    w,h = text_size(scale,'IBMPlexSansCondensed-Regular.ttf',3)
    boxes = [[tb[2]-19-w,tb[1]+35,tb[2]-19,tb[1]+35+h]]
    for v in p['views']:
        cut = v.get('cut')
        if not cut: continue
        extent = [hi-lo for lo,hi in zip(*a['envelope'])]
        half = sum(abs(x)*length for x,length in zip(v['up'],extent))*v['scale']/2
        boxes.append(text_box('SECTION '+cut['label']+'–'+cut['label'],
                              'IBMPlexMono-Regular.ttf',3,[v['at'][0],v['at'][1]-half-7]))
        m = next((m for m in p['views'] if m['id']==cut['marked_in']), None)
        if m:
            # Cutting-plane ink is already inside its marked view's footprint;
            # captions extend 7 mm beyond the envelope at each end.
            width = sum(abs(x)*length for x,length in zip(
                [m['up'][1]*m['direction'][2]-m['up'][2]*m['direction'][1],
                 m['up'][2]*m['direction'][0]-m['up'][0]*m['direction'][2],
                 m['up'][0]*m['direction'][1]-m['up'][1]*m['direction'][0]],extent))*m['scale']
            centre = [(lo+hi)/2 for lo,hi in zip(*a['envelope'])]
            y = m['at'][1] + m['scale']*sum(u*(o-c) for u,o,c in zip(m['up'],cut['origin'],centre))
            for x in [m['at'][0]-width/2-7,m['at'][0]+width/2+7]:
                boxes.append(text_box(cut['label'],'IBMPlexMono-Regular.ttf',3,[x,y+4.2]))
            boxes.append([m['at'][0]-width/2-4-0.9, y-6.75,
                          m['at'][0]+width/2+4+0.9,y])
    return boxes


def union_area(boxes):
    xs = sorted({x for b in boxes for x in (b[0], b[2])})
    area = 0
    for left, right in zip(xs, xs[1:]):
        ys = sorted((b[1], b[3]) for b in boxes if b[0] < right and b[2] > left)
        end, height = -math.inf, 0
        for bottom, top in ys:
            height += max(0, top - max(bottom, end))
            end = max(end, top)
        area += (right - left) * height
    return area


def footprint_fill(a, p, furniture=()):
    boxes = []
    def add(box, points=()):
        b = list(box)
        for x, y in points:
            b = [min(b[0], x), min(b[1], y), max(b[2], x), max(b[3], y)]
        boxes.append(b)
    size = [hi-lo for lo, hi in zip(*a['envelope'])]
    for v in p['views']:
        x, y = v['at']; s = v['scale']
        if v['id'] == 'v:iso':
            b = a['iso_bounds']; add([x+s*b[0], y+s*b[1], x+s*b[2], y+s*b[3]])
        else:
            # Principal and section directions have orthogonal unit basis vectors.
            up, toward = v['up'], v['direction']
            right = [up[1]*toward[2]-up[2]*toward[1], up[2]*toward[0]-up[0]*toward[2],
                     up[0]*toward[1]-up[1]*toward[0]]
            bounds = v.get('detail', {}).get('bounds', a['envelope'])
            view_size = [hi-lo for lo,hi in zip(*bounds)]
            w, h = (sum(abs(d[k])*view_size[k] for k in range(3))*s for d in (right, up))
            add([x-w/2, y-h/2, x+w/2, y+h/2])
    def arrow_points(arrows):
        result=[]
        for tip,tail in arrows:
            normal=[tail[1]-tip[1], tip[0]-tail[0]]
            k=.45/max(1e-12,math.hypot(*normal))
            result.extend([tip,[tail[0]+k*normal[0],tail[1]+k*normal[1]],
                           [tail[0]-k*normal[0],tail[1]-k*normal[1]]])
        return result
    for d in p['dimensions']:
        q = d.get('placed')
        if not q: continue
        points = [r for line in q['lines'] + q['arrows'] for r in line]
        points.extend(arrow_points(q['arrows']))
        add(q['label_box'], points)
    for g in p.get('gdt', []):
        q = g.get('placed')
        if q:
            add(q['label_box'], [r for line in q['lines']+q['strokes']+q['fills'] for r in line]+arrow_points(q['arrows']))
    if p.get('notes_block'): add(p['notes_block']['box'])
    for t in p.get('tables', []) + (p.get('hole_table') or {}).get('blocks', []):
        if t.get('placed'): add(t['placed']['box'])
    for b in p.get('balloons', []):
        x,y = b['at']; r = b['radius']; add([x-r,y-r,x+r,y+r], b.get('leader') or [])
    for r in p.get('requirements', []):
        q = r.get('placed')
        if q:
            b=q['box']; add(q['label_box'], [[b[0],b[1]], [b[2],b[3]]])
    # Projection furniture is resolved geometric ink, independent of sheet.fill.
    symbol = p['sheet'].get('projection_symbol')
    if symbol:
        points = []
        for curve in symbol['curves']:
            if 'Line' in curve:
                points.extend(curve['Line'])
            elif 'Conic' in curve:
                c = curve['Conic']; x,y = c['centre']; rx,ry = c['radii']
                points.extend([[x-rx,y-ry], [x+rx,y+ry]])
        if points:
            xs,ys = zip(*points); add([min(xs),min(ys),max(xs),max(ys)])
    boxes.extend(furniture)
    w,h = p['sheet']['page']; tb=list(p['sheet']['title_block']); tb[3]=tb[1]+32
    clipped = [[max(10,b[0]-1),max(10,b[1]-1),min(w-10,b[2]+1),min(h-10,b[3]+1)] for b in boxes]
    clipped = [b for b in clipped if b[0]<b[2] and b[1]<b[3]]
    in_title = [[max(b[0],tb[0]),max(b[1],tb[1]),min(b[2],tb[2]),min(b[3],tb[3])] for b in clipped]
    in_title = [b for b in in_title if b[0]<b[2] and b[1]<b[3]]
    return (union_area(clipped)-union_area(in_title))/((w-20)*(h-20)-(tb[2]-tb[0])*(tb[3]-tb[1]))


def one(f, dw, out, spec=None, timeout=600, review=True):
    name = os.path.basename(f).split(".")[0]
    os.makedirs(out, exist_ok=True)
    extra = ["--spec", spec] if spec else []
    try:
        r = subprocess.run([dw, "render", f, "-o", f"{out}/{name}", "--formats", "svg", *extra], capture_output=True, text=True, timeout=timeout)
        p = subprocess.run([dw, "plan", f, *extra], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        return name, {"error": str(error)}
    try:
        rend, plan = json.loads(r.stdout), json.loads(p.stdout)
    except Exception:
        return name, {"error": (r.stdout + r.stderr)[:200]}
    if r.returncode or p.returncode or "dimensions" not in plan or "error" in rend:
        return name, {"error": json.dumps(plan.get("error", plan))[:200]}
    if "completeness" not in plan:
        return name, {"error": "Plan has no completeness counts; rebuild draftwright before measuring quality"}
    with open(f"{out}/{name}.plan.json", "w") as output:
        json.dump(plan, output, indent=1, sort_keys=True)
    with open(f"{out}/{name}.render.json", "w") as output:
        json.dump(rend, output, indent=1, sort_keys=True)
    if plan['sheet'].get('page'):
        analysis = subprocess.run([dw, "analyse", f], capture_output=True, text=True, timeout=timeout)
        if analysis.returncode:
            return name, {"error": analysis.stderr[:200]}
        a = json.loads(analysis.stdout)
        with open(f"{out}/{name}.analysis.json", "w") as output:
            json.dump(a, output, indent=1, sort_keys=True)
        fill = footprint_fill(a, plan, furniture_boxes(a, plan))
    else:
        fill = 0.0
    codes = collections.Counter(x["code"] for x in rend.get("problems", []))
    # Render reports planning problems. Centre-line contacts are judged on actual drawn labels.
    if review and any(v.get("line_work") or v.get("cut") for v in plan["views"]):
        try:
            q = subprocess.run([dw, "review", f, *extra], capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as error:
            return name, {"error": str(error)}
        try:
            review = json.loads(q.stdout)
        except ValueError:
            return name, {"error": (q.stdout + q.stderr)[:200]}
        if q.returncode or "structural" not in review:
            return name, {"error": json.dumps(review)[:200]}
        for code in ("label_centerline_overlap", "leader_centerline_overlap",
                     "label_section_furniture_overlap", "oblique_witness_crossing"):
            codes[code] = max(codes[code], sum(i["code"] == code for i in review["structural"]))
        for code in ("radius_leader_target_mismatch", "radius_leader_target_unverifiable"):
            # Unreadable radius sections are now informational (#145), not proven bad
            # ink. Keep every finding in review.json; existing warning/error counts stay.
            codes[code] = max(codes[code], sum(i["code"] == code and not (
                code == "radius_leader_target_unverifiable" and i.get("severity") == "info")
                for i in review.get("lint", [])))
        with open(f"{out}/{name}.review.json", "w") as output:
            json.dump({k: review[k] for k in ("lint", "structural")}, output, indent=1, sort_keys=True)
    # Repeated dimensions: the same measured quantity (feature, parameter) stated more than once,
    # and, separately, the same label on the same span in the same view (a drawn duplicate).
    meas = collections.Counter()
    spans = collections.Counter()
    for d in plan["dimensions"]:
        for m in d.get("measurements", []):
            meas[(m.get("feature"), m.get("parameter"))] += 1
        pl = d.get("placed") or {}
        lines = pl.get("lines") or []
        view = d["view"]
        if lines:
            ends = sorted((round(x, 1), round(y, 1)) for l in lines[:2] for (x, y) in l)
            spans[(view, d.get("label"), tuple(ends))] += 1
    labels = collections.Counter(
        (d["view"], d.get("label"), (d.get("placed") or {}).get("rotation", 0))
        for d in plan["dimensions"] if d.get("kind") == "linear")
    rep_label = sum(n - 1 for n in labels.values() if n > 1)
    rep_meas = sum(n - 1 for n in meas.values() if n > 1)
    rep_span = sum(n - 1 for n in spans.values() if n > 1)
    repeated = repeated_spans(plan)
    view_over, view_tb = view_overlap_counts(plan)
    linework = svg_linework(f"{out}/{name}.svg", plan)
    return name, {
        "off_sheet": codes.get("annotation_off_sheet", 0) + codes.get("pinned_view_off_sheet", 0),
        "off_sheet_items": sorted({subject for problem in rend.get("problems", [])
                                   if problem["code"] in {"annotation_off_sheet", "pinned_view_off_sheet"}
                                   for subject in problem.get("subjects", [])}),
        "repeated_measure": rep_meas, "repeated_drawn": rep_span, "repeated_label": rep_label,
        "repeated_span": sum(len(g['items']) - 1 for g in repeated),
        "repeated_span_groups": repeated,
        "views_overlap": view_over, "view_on_title_block": view_tb,
        "text_off_span": len(text_off_span(plan)), "text_off_span_items": text_off_span(plan),
        "ink": sum(codes[c] for c in INK) + linework.get("schedule_ink", 0), "ink_by_code": {**{c: codes[c] for c in INK if codes[c]},
                         **({"schedule_ink": linework["schedule_ink"]} if linework.get("schedule_ink") else {})},
        "line_through_feature": line_through_features(plan),
        "leader_on_extension": leader_on_extension(plan),
        **crossing_counts(plan),
        "dimensions": len(plan["dimensions"]),
        "sheet": plan["sheet"].get("size"), "scale": plan["sheet"].get("scale"),
        "sheets": 1 + len(plan.get("sheets", [])),
        "fill": round(100 * fill, 2),
        "interior_labels": interior_labels(plan),
        "outer_labels": sum(d.get("placed") is not None for d in plan["dimensions"]) - interior_labels(plan),
        "sheet_escalations": sheet_escalations(plan),
        **{key: plan["completeness"][key]
           for key in ("claimed_faces", "faces", "unplanned_features")},
        **linework,
    }


def table(res):
    keys = ["off_sheet", "repeated_label", "repeated_measure", "views_overlap", "view_on_title_block", "ink", "repeated_span", "line_through_feature", "hidden_on_visible", "section_hidden", "claimed_faces", "faces", "unplanned_features", "text_off_span", "interior_labels", "fill", "leader_on_extension"]
    print(f"{'part':28}" + "".join(f"{k[:12]:>13}" for k in keys))
    tot = collections.Counter(); bad = collections.Counter()
    for n, v in sorted(res.items()):
        if "error" in v:
            print(f"{n:28} ERROR {v['error'][:80]}"); continue
        print(f"{n[:28]:28}" + "".join(f"{v.get(k, 0):13}" for k in keys))
        for k in keys:
            tot[k] += v.get(k, 0); bad[k] += v.get(k, 0) > 0
    tot['fill'] = round(tot['fill'] / max(1, sum('error' not in v for v in res.values())), 2)
    print(f"{'TOTAL':28}" + "".join(f"{tot[k]:13}" for k in keys))
    print(f"{'parts affected':28}" + "".join(f"{bad[k]:13}" for k in keys), f"of {sum('error' not in v for v in res.values())}")

    print("Crossings (ordered pairs; multi-kind pairs count in each applicable column):")
    print(f"{'part':32}" + ''.join(f'{k:>23}' for k in CROSSING_KEYS))
    for name, row in sorted(res.items()):
        if 'error' not in row:
            print(f'{name:32}' + ''.join(f'{row.get(k, 0):23}' for k in CROSSING_KEYS))
    print(f"{'TOTAL':32}" + ''.join(f"{sum(r.get(k, 0) for r in res.values() if 'error' not in r):23}" for k in CROSSING_KEYS))


def pin_geometry(item, view=None):
    """Sampling/order facts, deliberately excluding identity, labels and provenance.

    Projected witnesses, arrows and glyph bounds distinguish coincident values;
    the camera distinguishes the same interval seen in different projections.
    Quantize paper geometry below the instrument's 1e-6 mm tolerance so JSON
    float spelling (including negative zero) cannot reshuffle the sample.
    """
    def numbers(v):
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return round(v * 1_000_000_000)
        if isinstance(v, list):
            return [numbers(x) for x in v]
        return None
    placed = item.get('placed') or {}
    geometry = {k: numbers(placed[k]) for k in (
        'at', 'box', 'glyph', 'label_at', 'label_box', 'rotation',
        'lines', 'arrows', 'strokes',
    ) if k in placed}
    camera = view or item
    return json.dumps([
        item.get('kind'), numbers(item.get('value')),
        sorted(numbers(m.get('value')) for m in item.get('measurements', [])
               if isinstance(m.get('value'), (int, float))),
        numbers(camera.get('direction')), numbers(camera.get('up')),
        numbers(camera.get('area')), numbers(camera.get('scale')), geometry,
    ], sort_keys=True, separators=(',', ':'))


def random_pins(plan, name, seed, count):
    """Bare annotation pins; view pins use the nearest legal relation to a fixed root."""
    by_view = {v['id']: v for v in plan['views']}
    candidates = [(d["id"], "dimensions", {"pinned": True},
                   pin_geometry(d, by_view.get(d.get('view'))))
                  for d in plan["dimensions"] if d.get("placed") is not None]
    candidates += [(g["id"], "annotations", {"pinned": True},
                    pin_geometry(g, by_view.get(g.get('view'))))
                   for g in plan.get("gdt", []) if g.get("placed") is not None]
    # Front-facing geometry is the conventional root; camera/position facts
    # resolve the other views, independently of their output names.
    views = sorted(plan["views"], key=lambda v: (
        v.get('sheet', 1), v.get('direction') != [0, -1, 0],
        v.get('area', []), pin_geometry(v)))
    roots = {}
    for v in views:
        number = v.get("sheet", 1)
        if number not in roots:
            roots[number] = v
            continue
        anchor = roots[number]
        b = anchor["area"]
        a = v["area"]
        dx, dy = (a[0]+a[2]-b[0]-b[2])/2, (a[1]+a[3]-b[1]-b[3])/2
        if abs(dx) > abs(dy):
            side, gap, k = ("right", a[0]-b[2], 1) if dx > 0 else ("left", b[0]-a[2], 1)
        else:
            side, gap, k = ("above", a[1]-b[3], 0) if dy > 0 else ("below", b[1]-a[3], 0)
        offsets = {"middle": (b[k]+b[k+2]-a[k]-a[k+2])/2, "start": b[k]-a[k],
                   "end": b[k+2]-a[k+2]}
        primary = {"v:front", "v:plan", "v:side"}
        align = "projection" if v["id"] in primary and anchor["id"] in primary else min(offsets, key=lambda slot: abs(offsets[slot]))
        candidates.append((v["id"], "views", {"relative_to": anchor["id"][2:], "side": side,
                           "gap": max(0, min(1000, gap)), "align": align, "pinned": True},
                           pin_geometry(v)))
    candidates.sort(key=lambda c: (hashlib.sha256(f"{seed}:{name}:{c[1]}:{c[3]}".encode()).digest(), c[1], c[3]))
    # A part with fewer drawable candidates than requested pins every one of them: removing a
    # redundant dimension (cgb203's repeated `180`, 2026-10-27) must not turn a pin cell into an
    # error, and pinning all candidates is at least as demanding as pinning `count` of them.
    count = min(count, len(candidates))
    spec = {"schema": "draftwright.spec/3"}
    for id_, kind, place, _ in candidates[:count]:
        spec.setdefault(kind, {})[id_[2:] if kind == "views" else id_] = {"place": place}
    return spec, [c[0] for c in candidates[:count]], len(candidates)


def pin_bar(before, after):
    no_new = after["off_sheet"] <= before["off_sheet"]
    if "off_sheet_items" in before and "off_sheet_items" in after:
        no_new = not (set(after["off_sheet_items"]) - set(before["off_sheet_items"]))
    return after["ink"] <= 1.5 * before["ink"] and no_new


def pinned_one(f, args, baseline):
    name = os.path.basename(f).split(".")[0]
    try:
        if "error" in baseline[name]:
            raise ValueError(baseline[name]["error"])
        p = subprocess.run([args.dw, "plan", f], capture_output=True, text=True, timeout=args.timeout)
        if p.returncode:
            raise ValueError(p.stderr or p.stdout)
        spec, ids, candidates = random_pins(json.loads(p.stdout), name, args.seed, args.pins)
        path = f"{args.out}/pinned/{name}.spec.json"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as output:
            json.dump(spec, output, indent=1, sort_keys=True)
        _, after = one(f, args.dw, f"{args.out}/pinned", path, timeout=args.timeout)
        if "error" in after:
            raise ValueError(after["error"])
        before = baseline[name]
        return name, {"ids": ids, "candidates": candidates, "unpinned": before, "pinned": after,
                      "ink_delta": after["ink"]-before["ink"],
                      "off_sheet_delta": after["off_sheet"]-before["off_sheet"],
                      "go": pin_bar(before, after)}
    except (ValueError, KeyError, subprocess.TimeoutExpired) as error:
        return name, {"error": str(error), "go": False}


def pin_report(parts, seed, count):
    total = {variant: {key: sum(p[variant][key] for p in parts.values() if "error" not in p)
                       for key in ("ink", "off_sheet")} for variant in ("unpinned", "pinned")}
    go = all(p["go"] for p in parts.values())
    print(f"seed={seed} N={count}: part                         ink before/after off-sheet before/after go")
    for name, p in sorted(parts.items()):
        if "error" in p:
            print(f"{name:48} ERROR {p['error']}")
        else:
            b, a = p["unpinned"], p["pinned"]
            print(f"{name:48} {b['ink']:5}/{a['ink']:<5} {b['off_sheet']:5}/{a['off_sheet']:<5} {'GO' if p['go'] else 'NO-GO'}")
    b, a = total["unpinned"], total["pinned"]
    print(f"TOTAL {b['ink']}/{a['ink']} ink; {b['off_sheet']}/{a['off_sheet']} off-sheet; {'GO' if go else 'NO-GO'} ({sum(not p['go'] for p in parts.values())} failed parts)")
    return {"seed": seed, "pins": count, "parts": parts, "total": total, "go": go}


def record_baseline(path, measurements, binary):
    """Explicitly record a complete measured quality matrix and its paper choices."""
    with open(path) as source:
        record = json.load(source)
    if record.get('format') != 'draftwright.ci-quality-baseline/1':
        raise ValueError('record target must be the existing CI quality baseline')
    if set(record['parts']) != set(measurements):
        raise ValueError('record requires the complete baseline part set')
    for name, row in measurements.items():
        if 'error' in row or any(type(row.get(k)) is not int or row[k] < 0 for k in record['metrics']):
            raise ValueError(f'{name}: incomplete/invalid measured quality row')
    record['parts'] = {name: {k: row[k] for k in record['metrics']}
                       for name, row in measurements.items()}
    record['sheet_choices'] = {name: [row['sheet'], row['scale'], row['sheets']]
                              for name, row in measurements.items()}
    record['source_commit'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    record['source'] = 'Recorded from the complete quality.py --record audit'
    with open(binary, 'rb') as source:
        record['verified_binary_sha256'] = hashlib.sha256(source.read()).hexdigest()
    with open(path, 'w') as output:
        json.dump(record, output, indent=2, sort_keys=True)
        output.write('\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=600, help="Per-operation wall timeout (seconds)")
    parser.add_argument("--record", help="Explicitly replace this CI quality baseline from all measured parts")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--pins", type=int)
    parser.add_argument("--baseline", help="Reuse this unpinned baseline JSON (same binary and parts)")
    parser.add_argument("--require-go", action="store_true", help="Exit 1 if any part fails the pin bar")
    parser.add_argument("--require-no-repeated-span", action="store_true", help="Exit 1 if any placed linear span repeats")
    parser.add_argument("dw")
    parser.add_argument("out")
    parser.add_argument("files", nargs="+")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if (args.seed is None) != (args.pins is None) or (args.pins is not None and args.pins < 1):
        parser.error("provide both --seed and --pins (N >= 1)")
    if args.record and args.pins is not None:
        parser.error("--record requires an unpinned quality audit")
    os.makedirs(args.out, exist_ok=True)
    if args.baseline:
        with open(args.baseline) as source:
            res = json.load(source)
        if set(res) != {os.path.basename(f).split(".")[0] for f in args.files}:
            parser.error("baseline parts must equal the requested parts")
    else:
        with ThreadPoolExecutor(4) as ex:
            res = dict(ex.map(lambda f: one(f, args.dw, args.out, timeout=args.timeout), args.files))
    with open(f"{args.out}/baseline.json", "w") as output:
        json.dump(res, output, indent=1, sort_keys=True)
    if args.pins is not None:
        with ThreadPoolExecutor(4) as ex:
            parts = dict(ex.map(lambda f: pinned_one(f, args, res), args.files))
        report = pin_report(parts, args.seed, args.pins)
        with open(f"{args.out}/random-pins.json", "w") as output:
            json.dump(report, output, indent=1, sort_keys=True)
        return int(any("error" in p for p in parts.values()) or (args.require_go and not report["go"]))
    if args.record:
        try:
            record_baseline(args.record, res, args.dw)
        except (OSError, ValueError, KeyError) as error:
            parser.error(str(error))
    table(res)
    return int(any("error" in v for v in res.values())
               or (args.require_no_repeated_span and any(v['repeated_span'] for v in res.values())))


if __name__ == "__main__":
    sys.exit(main())
