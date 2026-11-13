"""Go/no-go instrument contracts; run with python3 -I tools/test_quality.py."""
import importlib.util
import pathlib
from pathlib import Path
import unittest
from unittest.mock import patch
import json
import subprocess
import tempfile
import contextlib
import io
import sys

spec = importlib.util.spec_from_file_location('quality', pathlib.Path(__file__).with_name('quality.py'))
quality = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quality)


route_spec = importlib.util.spec_from_file_location('leader_routes', Path(__file__).with_name('check_leader_routes.py'))
routes = importlib.util.module_from_spec(route_spec)
route_spec.loader.exec_module(routes)


class QualityRecording(unittest.TestCase):
    def test_complete_matrix_records_actual_counts_and_paper_choices(self):
        with tempfile.TemporaryDirectory() as directory:
            path, binary = Path(directory) / 'baseline.json', Path(directory) / 'binary'
            binary.write_bytes(b'test executable')
            path.write_text(json.dumps({'format': 'draftwright.ci-quality-baseline/1',
                                        'parts': {'p': {'ink': 0}}, 'metrics': ['ink']}))
            with patch.object(quality.subprocess, 'check_output', return_value='revision\n'):
                quality.record_baseline(path, {'p': dict(ink=7, sheet='A3', scale='1:5', sheets=2)}, binary)
            recorded = json.loads(path.read_text())
            self.assertEqual(recorded['parts'], {'p': {'ink': 7}})
            self.assertEqual(recorded['sheet_choices'], {'p': ['A3', '1:5', 2]})

    def test_incomplete_matrix_is_refused_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'baseline.json'
            original = json.dumps({'format': 'draftwright.ci-quality-baseline/1',
                                   'parts': {'p': {'ink': 0}}, 'metrics': ['ink']})
            path.write_text(original)
            for measurements in [{}, {'p': {'error': 'failed'}}, {'p': {'ink': -1}}]:
                with self.assertRaises(ValueError):
                    quality.record_baseline(path, measurements, Path(directory) / 'unused')
                self.assertEqual(path.read_text(), original)


class LeaderRoutes(unittest.TestCase):
    def row(self, **changes):
        row = dict(length=10., run=0., route=[[0., 0.], [6., 6.]], label=[7., 5., 12., 8.],
                   hidden=0, section=0, cutting_plane=0)
        return dict(row, **changes)

    def test_length_crossings_and_collinear_runs_are_independent_vetoes(self):
        before = {'part': {'callout': self.row()}}
        for changes in (dict(length=12.01), dict(hidden=1), dict(section=1),
                        dict(cutting_plane=1), dict(run=2.01, route=[[0., 0.], [0., 10.]])):
            self.assertTrue(routes.check(before, {'part': {'callout': self.row(**changes)}}))
        self.assertFalse(routes.check(before, {'part': {'callout': self.row(length=12.)}}))

    def test_inventory_can_evolve_but_new_routes_get_the_run_guard(self):
        before = {'part': {'old': self.row()}}
        self.assertFalse(routes.check(before, {'part': {'new': self.row()}}))
        self.assertTrue(routes.check(before, {'part': {'new': self.row(run=3.)}}))

    def test_cutting_planes_use_resolved_strokes_for_any_axis(self):
        with tempfile.TemporaryDirectory() as tmp:
            svg = Path(tmp) / 'sheet.svg'
            svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
            view = dict(id='v:front', at=[0., 0.], line_work=[dict(kind='cutting_plane',
                        curves=[{'Line': [[3., -1.], [3., 7.]]}])])
            placed = dict(lines=[[[0., 0.], [6., 6.]], [[6., 6.], [9., 6.]]],
                          label_box=[10., 5., 15., 8.], arrows=[])
            plan = dict(views=[view], dimensions=[dict(id='radius', kind='callout',
                        view='v:front', placed=placed)])
            self.assertEqual(routes.measures(plan, svg, {})['radius']['cutting_plane'], 1)

    def test_a_moved_callout_may_not_gain_text_head_or_outline_contact(self):
        base = self.row(text_extension=0, head_extension=0, elbow_outline=False, label_outline=False)
        before = {'part': {'callout': base}}
        moved = dict(route=[[0., 0.], [5., 7.]])
        for key, value in (('text_extension', 1), ('head_extension', 1),
                           ('elbow_outline', True), ('label_outline', True)):
            changed = dict(base, **moved, **{key: value})
            self.assertTrue(routes.check(before, {'part': {'callout': changed}}), key)
            # An unmoved callout keeps its existing contact without failing.
            self.assertFalse(routes.check({'part': {'callout': dict(base, **{key: value})}},
                                          {'part': {'callout': dict(base, **{key: value})}}))
        self.assertFalse(routes.check(before, {'part': {'callout': dict(base, **moved)}}))

    def test_text_and_arrowhead_contacts_are_measured_from_the_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            svg = Path(tmp) / 'sheet.svg'
            svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"><g id="part">'
                           '<line x1="0" y1="-5" x2="0" y2="20"/></g></svg>')
            view = dict(id='v:front', at=[0., 0.], line_work=[])
            # A linear dimension whose second extension line runs 0.5 mm under the label
            # and through the arrowhead.
            linear = dict(id='linear', kind='linear', view='v:front', placed=dict(
                lines=[[[20., 0.], [20., 30.]], [[6., 4.5], [30., 4.5]], [[20., 20.], [30., 20.]]],
                arrows=[[[20., 20.], [21., 20.]], [[30., 20.], [29., 20.]]], label_box=[0, 0, 0, 0]))
            callout = dict(id='callout', kind='callout', view='v:front', placed=dict(
                lines=[[[6., 4.], [10., 8.]], [[10., 8.], [13., 8.]]],
                arrows=[[[6., 4.], [8., 6.]]], label_box=[14., 5., 19., 8.]))
            m = routes.measures(dict(views=[view], dimensions=[linear, callout]), svg, {})['callout']
            self.assertEqual((m['text_extension'], m['head_extension']), (1, 1))
            self.assertFalse(m['elbow_outline'])
            self.assertFalse(m['label_outline'])
            callout['placed']['lines'][0][1] = [0.5, 8.]
            m = routes.measures(dict(views=[view], dimensions=[linear, callout]), svg, {})['callout']
            self.assertTrue(m['elbow_outline'])

    def test_existing_runs_are_reported_and_cannot_grow_or_move(self):
        before = {'part': {'callout': self.row(run=3.)}}
        self.assertFalse(routes.check(before, before))
        for changes in (dict(run=3.01), dict(label=[8., 5., 13., 8.])):
            self.assertTrue(routes.check(before, {'part': {'callout': self.row(**dict(dict(run=3.), **changes))}}))


class ScheduleInk(unittest.TestCase):
    def test_schedule_furniture_is_counted_against_edges_and_labels(self):
        plan = {'dimensions': [], 'balloons': [
            {'id': 'hole_schedule_plan/tag/A1', 'label_box': [2, 2, 5, 5]}]}
        self.assertEqual(quality.schedule_ink(plan, [([0, 3], [8, 3])]), 1)
        self.assertEqual(quality.schedule_ink(plan, [([0, 6], [8, 6])]), 0)
        plan['dimensions'] = [{'id': 'dimension', 'placed': {'label_box': [4, 4, 7, 7]}}]
        self.assertEqual(quality.schedule_ink(plan, []), 1)
        plan['dimensions'] = [{'id': 'dimension', 'placed': {'label_box': [9, 9, 11, 11],
                              'lines': [[[0, 3], [8, 3]]]}}]
        self.assertEqual(quality.schedule_ink(plan, []), 1)
        plan['balloons'][0]['id'] = 'hole_schedule_plan/origin'
        self.assertEqual(quality.schedule_ink(plan, []), 1)

        plan['dimensions'] = []
        plan['gdt'] = [{'id': 'frame', 'placed': {'label_box': [3, 3, 6, 6]}}]
        self.assertEqual(quality.schedule_ink(plan, []), 1)

    def test_serialized_table_and_note_boxes_are_obstacles(self):
        plan = {'dimensions': [], 'balloons': [
            {'id': 'hole_schedule_plan/origin', 'label_box': [2, 2, 5, 5]}],
            'tables': [{'id': 'table', 'placed': {'box': [3, 3, 7, 7]}}]}
        self.assertEqual(quality.schedule_ink(plan, []), 1)
        plan['tables'] = []
        plan['notes_block'] = {'box': [3, 3, 7, 7]}
        self.assertEqual(quality.schedule_ink(plan, []), 1)


class LeaderExtensionSheets(unittest.TestCase):
    def test_coincident_ink_only_contacts_on_its_own_sheet(self):
        plan = {'views': [{'id': 'front', 'sheet': 1}, {'id': 'plan', 'sheet': 2}],
                'dimensions': [
                    {'id': 'linear', 'view': 'front', 'placed': {
                        'lines': [[[10, 10], [10, 30]], [[20, 10], [20, 30]],
                                  [[10, 25], [20, 25]]], 'arrows': [[], []]}},
                    {'id': 'callout', 'view': 'plan', 'kind': 'callout', 'placed': {
                        'lines': [[[5, 20], [15, 20]]], 'label_box': [5, 18, 9, 22]}}]}
        self.assertEqual(quality.leader_on_extension(plan), 0)
        plan['views'][1]['sheet'] = 1
        self.assertEqual(quality.leader_on_extension(plan), 1)


class TextOffSpan(unittest.TestCase):
    def dimension(self, id_='a', box=(16, 51, 21, 53), ends=(10, 30), view='v:front', vertical=False):
        point = lambda x, y: [y, x] if vertical else [x, y]
        return {'id': id_, 'kind': 'linear', 'view': view, 'placed': {
            'rotation': 90 if vertical else 0,
            'arrows': [[point(x, 50), point(x, 50)] for x in ends],
            'lines': [[point(x, 40), point(x, 54)] for x in ends],
            'label_box': [box[1], box[0], box[3], box[2]] if vertical else list(box)}}

    def test_inside_and_adjacent_are_allowed_but_detached_is_counted(self):
        for vertical in (False, True):
            for box, expected in [((16,51,21,53), []), ((33.7,51,38.7,53), []),
                                  ((35.69,51,40.7,53), []), ((35.7,51,40.7,53), ['a']),
                                  ((0,51,4,53), ['a'])]:
                self.assertEqual(quality.text_off_span({'dimensions': [self.dimension(box=box, vertical=vertical)]}), expected)

    def test_every_extension_segment_is_checked(self):
        a = self.dimension(box=(33, 51, 38, 53))
        b = self.dimension('b', ends=(32, 60), box=(40, 51, 45, 53))
        b['placed']['lines'] = [[[32, 40], [32, 45]], [[60, 40], [60, 45]],
                                [[32, 50], [60, 50]], [[32, 49], [32, 54]]]
        self.assertEqual(quality.text_off_span({'dimensions': [a, b]}), ['a'])

    def test_the_old_outside_gap_of_arrow_plus_3_counts(self):
        # The planner used to park outside text exactly ARROW + 3 mm past the tip (10020 4.5).
        a = self.dimension(box=(35.7, 51, 40.7, 53))
        self.assertEqual(quality.text_off_span({'dimensions': [a]}), ['a'])
        a['placed']['label_box'][0] -= 0.01
        self.assertEqual(quality.text_off_span({'dimensions': [a]}), [])

    def test_foreign_extension_between_label_and_span_in_same_view(self):
        for vertical in (False, True):
            a = self.dimension(box=(33,51,38,53), vertical=vertical)
            b = self.dimension('b', ends=(32,60), box=(40,51,45,53), vertical=vertical)
            self.assertEqual(quality.text_off_span({'dimensions': [a,b]}), ['a'])
            b['view'] = 'v:plan'
            self.assertEqual(quality.text_off_span({'dimensions': [a,b]}), [])

    def test_remote_extension_and_non_linear_ink_do_not_count(self):
        a = self.dimension(box=(33,51,38,53))
        b = self.dimension('b', ends=(32,60), box=(40,51,45,53))
        for line in b['placed']['lines']:
            for p in line: p[1] += 30
        self.assertEqual(quality.text_off_span({'dimensions': [a,b]}), [])
        b['kind'] = 'callout'
        self.assertEqual(quality.text_off_span({'dimensions': [a,b]}), [])



class RepeatedSpans(unittest.TestCase):
    def dimension(self, id_, ends=(10, 30), lane=0, rotation=0, value=20, feature=None):
        tips = [[x, lane] if rotation == 0 else [lane, x] for x in ends]
        return {'id': id_, 'kind': 'linear', 'view': 'v:plan', 'value': value,
                'label': str(value), 'measurements': [{'feature': feature or id_,
                                                     'parameter': 'linear'}],
                'placed': {'rotation': rotation,
                           'arrows': [[tip, tip] for tip in tips]}}

    def groups(self, dims, scale='1:1'):
        return quality.repeated_spans({'sheet': {'scale': scale}, 'dimensions': dims})

    def test_span_ignores_lane_label_and_witness_order(self):
        a = self.dimension('a')
        b = self.dimension('b', ends=(30, 10), lane=70)
        b['label'] = '20 ±0.1'
        self.assertEqual(self.groups([b, a]), [{'kind': 'span', 'items': ['a', 'b']}])

    def test_tolerance_is_in_model_mm_without_rounding_buckets(self):
        for scale, factor in [('1:5', 0.2), ('5:1', 5)]:
            a = self.dimension('a', ends=(10 * factor, 30 * factor))
            b = self.dimension('b', ends=((10 + 0.9e-6) * factor, 30 * factor))
            self.assertEqual(len(self.groups([a, b], scale)), 1)
            b = self.dimension('b', ends=((10 + 1.1e-6) * factor, 30 * factor))
            self.assertEqual(self.groups([a, b], scale), [])

    def test_distinct_axes_values_views_and_unplaced_items_do_not_repeat(self):
        a = self.dimension('a')
        for b in [self.dimension('b', rotation=90), self.dimension('b', value=21),
                  dict(self.dimension('b'), view='v:front'),
                  dict(self.dimension('b'), placed=None),
                  dict(self.dimension('b'), kind='callout')]:
            self.assertEqual(self.groups([a, b]), [])

    def test_same_face_pair_at_different_stations_is_a_near_miss(self):
        a = self.dimension('a', feature='pmi:dimension.linear:f2,8')
        b = self.dimension('b', ends=(40, 60), feature='pmi:dimension.linear.basic:f2,8')
        self.assertEqual(self.groups([a, b]), [{'kind': 'feature_pair', 'items': ['a', 'b']}])
        b['measurements'][0]['feature'] = 'pmi:dimension.linear:f3,8'
        self.assertEqual(self.groups([a, b]), [])

    def test_three_dimensions_count_as_two_extras_in_one_group(self):
        dims = [self.dimension(id_, lane=i * 7) for i, id_ in enumerate('cba')]
        self.assertEqual(self.groups(dims), [{'kind': 'span', 'items': ['a', 'b', 'c']}])

    def test_geometric_noise_in_values_is_not_a_different_requirement(self):
        a = self.dimension('a')
        b = self.dimension('b', value=20 + 1e-9)
        self.assertEqual(len(self.groups([a, b])), 1)
        b['value'] = 20 + 2e-6
        self.assertEqual(self.groups([a, b]), [])

    def test_face_indices_in_different_parts_are_not_the_same_pair(self):
        a = self.dimension('a', feature='pmi:dimension.linear:p0:f2,8')
        b = self.dimension('b', ends=(40, 60), feature='pmi:dimension.linear:p1:f2,8')
        self.assertEqual(self.groups([a, b]), [])

    def test_cli_can_enforce_zero_repeated_spans(self):
        metrics = {k: 0 for k in ('off_sheet', 'repeated_label', 'repeated_measure',
                                 'views_overlap', 'view_on_title_block', 'ink', 'repeated_span',
                                 'line_through_feature', 'hidden_on_visible', 'section_hidden',
                                 'claimed_faces', 'faces', 'unplanned_features', 'text_off_span')}
        with tempfile.TemporaryDirectory() as out:
            baseline = pathlib.Path(out) / 'source.json'
            for repeated in (0, 1):
                metrics['repeated_span'] = repeated
                baseline.write_text(json.dumps({'part': metrics}))
                argv = ['quality.py', '--require-no-repeated-span', '--baseline', str(baseline),
                        'dw', out, 'part.step']
                with patch.object(sys, 'argv', argv), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(quality.main(), repeated)


class InteriorLabels(unittest.TestCase):
    def test_counts_only_drawn_interior_candidates(self):
        self.assertEqual(quality.interior_labels({'dimensions': [
            {'placed': {}, 'region': 'interior'},
            {'placed': None, 'region': 'interior'},
            {'placed': {}, 'region': None},
            {'placed': {}, 'basis': 'some interior words'},
        ]}), 1)


class SheetEscalations(unittest.TestCase):
    def test_counts_larger_sheets_but_not_a_scale_retry(self):
        for size, expected in [('A3', 0), ('A2', 1)]:
            plan = {'sheet': {'size': size}, 'problems': [{
                'code': 'layout_replanned', 'message': 'The sheet was re-planned: A3 at 1:1 has a collision; larger standard sheets were tried.'}]}
            self.assertEqual(quality.sheet_escalations(plan), expected)


class CentreLineInk(unittest.TestCase):
    def test_a_real_label_centreline_contact_counts_as_ink(self):
        plan = {'sheet': {'scale': '1:1'}, 'dimensions': [], 'problems': [],
                'completeness': {'claimed_faces': 0, 'faces': 0, 'unplanned_features': 0},
                'views': [{'line_work': [{'kind': 'cross'}]}]}
        for code in ('label_centerline_overlap', 'leader_centerline_overlap',
                     'label_section_furniture_overlap', 'oblique_witness_crossing',
                     'radius_leader_target_mismatch', 'radius_leader_target_unverifiable'):
            with self.subTest(code=code):
                replies = [subprocess.CompletedProcess([], 0, '{}', ''),
                    subprocess.CompletedProcess([], 0, json.dumps(plan), ''),
                    subprocess.CompletedProcess([], 0, json.dumps({'structural': [] if code.startswith('radius_leader_target_') else [
                        {'code': code, 'annotation': 'd:a'}], 'lint': [{'code': code}] if code.startswith('radius_leader_target_') else []}), '')]
                with tempfile.TemporaryDirectory() as out, patch.object(quality.subprocess, 'run', side_effect=replies):
                    (Path(out) / 'part.svg').write_text('<svg/>')
                    _, result = quality.one('part.step', 'dw', out)
                self.assertEqual(result['ink'], 1)
                self.assertEqual(result['ink_by_code'], {code: 1})

    def test_unverifiable_radius_info_is_retained_without_counting_as_bad_ink(self):
        plan = {'sheet': {'scale': '1:1'}, 'dimensions': [], 'problems': [],
                'completeness': {'claimed_faces': 0, 'faces': 0, 'unplanned_features': 0},
                'views': [{'line_work': [{'kind': 'cross'}]}]}
        finding = {'code': 'radius_leader_target_unverifiable', 'severity': 'info',
                   'annotation': 'd:r', 'evidence': 'no proven end-on section'}
        replies = [subprocess.CompletedProcess([], 0, '{}', ''),
                   subprocess.CompletedProcess([], 0, json.dumps(plan), ''),
                   subprocess.CompletedProcess([], 0, json.dumps({'lint': [finding], 'structural': []}), '')]
        with tempfile.TemporaryDirectory() as out, patch.object(quality.subprocess, 'run', side_effect=replies):
            (Path(out) / 'part.svg').write_text('<svg/>')
            _, result = quality.one('part.step', 'dw', out)
            self.assertEqual(json.loads((Path(out) / 'part.review.json').read_text())['lint'], [finding])
        self.assertEqual(result['ink'], 0)
        self.assertEqual(result['ink_by_code'], {})


class FeatureCrossings(unittest.TestCase):
    def test_counts_plan_feature_pairs_and_extension_lines(self):
        plan = {'sheet': {'scale': '1:1'}, 'dimensions': [], 'views': [],
                'completeness': {'claimed_faces': 0, 'faces': 0, 'unplanned_features': 0},
                'problems': [{'code': 'leader_through_feature', 'subjects': ['d:a', 'f:b']},
                             {'code': 'leader_through_feature', 'subjects': ['d:a', 'f:c']},
                             {'code': 'extension_through_feature', 'id': 'p:d:e|f:b:line:0', 'subjects': ['d:e', 'f:b']},
                             {'code': 'extension_through_feature', 'id': 'p:d:e|f:c:line:0', 'subjects': ['d:e', 'f:c']},
                             {'code': 'extension_through_feature', 'id': 'p:d:e|f:c:line:1', 'subjects': ['d:e', 'f:c']},
                             {'code': 'leader_through_feature', 'subjects': ['d:a', 'f:b']}]}
        replies = [subprocess.CompletedProcess([], 0, '{}', ''),
                   subprocess.CompletedProcess([], 0, json.dumps(plan), '')]
        with tempfile.TemporaryDirectory() as out, patch.object(quality.subprocess, 'run', side_effect=replies), patch.object(quality, 'furniture_boxes', return_value=[]):
            (Path(out) / 'part.svg').write_text('<svg/>')
            _, result = quality.one('part.step', 'dw', out)
        self.assertEqual(result['line_through_feature'], 4)

    def test_status_headers_select_shared_columns_and_reject_a_regression(self):
        checker = Path(__file__).with_name('check_quality_table.py')
        old = 'part off_sheet repeated_lab repeated_mea views_overla view_on_titl ink\np 0 0 0 0 0 1\nTOTAL 0 0 0 0 0 1\n'
        new = 'part ink line_through off_sheet\np 1 2 0\nTOTAL 1 2 0\n'
        with tempfile.TemporaryDirectory() as out:
            status, baseline = Path(out) / 'status.md', Path(out) / 'baseline.json'
            baseline.write_text(json.dumps({'p': {'off_sheet': 0, 'repeated_label': 0,
                'repeated_measure': 0, 'views_overlap': 0, 'view_on_title_block': 0,
                'ink': 1, 'line_through_feature': 3}}))
            for table, expected in [(old, 0), (new.replace("p 1 2 0", "p 1 3 0"), 0), (new, 1)]:
                status.write_text(table)
                result = subprocess.run([sys.executable, '-I', str(checker), str(status), str(baseline)], capture_output=True)
                self.assertEqual(result.returncode, expected, result.stderr.decode())


class Linework(unittest.TestCase):
    def measure(self, visible, hidden, views=None, width=0):
        views = views or [{'id': 'v:front', 'area': [-20, -20, 20, 20]}]
        with tempfile.TemporaryDirectory() as out:
            path = pathlib.Path(out) / 'sheet.svg'
            path.write_text(f'<svg><g id="part" stroke-width="{width}">{visible}</g><g id="hidden">{hidden}</g></svg>')
            return quality.svg_linework(path, {'views': views})

    def test_page_length_and_distance_thresholds(self):
        visible = '<line x1="0" y1="0" x2="10" y2="0"/>'
        for length, distance, count in [(2, .14, 1), (2, .16, 0), (.9, 0, 0)]:
            hidden = f'<path d="M 0 {distance} L {length} {distance}"/>'
            self.assertEqual(self.measure(visible, hidden)['hidden_on_visible'], count)

    def test_visible_stroke_width_is_ink(self):
        visible = '<line x1="0" y1="0" x2="10" y2="0"/>'
        for offset, count in ((.39,1),(.41,0)):
            hidden = f'<line x1="0" y1="{offset}" x2="10" y2="{offset}"/>'
            self.assertEqual(self.measure(visible, hidden, width=.5)['hidden_on_visible'], count)

    def test_arc_coincidence_and_partial_overlap(self):
        self.assertEqual(self.measure('<circle cx="0" cy="0" r="5"/>',
                         '<path d="M 5 0 A 5 5 0 0 1 0 5"/>')['hidden_on_visible'], 1)
        self.assertEqual(self.measure('<line x1="0" y1="0" x2="3" y2="0"/>',
                         '<line x1="2" y1="0" x2="7" y2="0"/>')['hidden_on_visible'], 1)

    def test_section_counts_elements_and_separates_views(self):
        views = [{'id': 'v:front', 'area': [0,0,10,10]},
                 {'id': 'v:section_aa', 'area': [20,0,30,10], 'cut': {'normal': [0,1,0]}}]
        hidden = '<line x1="21" y1="1" x2="29" y2="1"/>'
        visible = '<line x1="1" y1="1" x2="9" y2="1"/>'
        self.assertEqual(self.measure(visible, hidden, views),
                         {'hidden_on_visible': 0, 'section_hidden': 1})

    def test_checker_reads_header_and_rejects_each_linework_regression(self):
        checker = pathlib.Path(__file__).with_name('check_quality_table.py')
        with tempfile.TemporaryDirectory() as out:
            status, baseline = [pathlib.Path(out) / x for x in ('status.md', 'baseline.json')]
            status.write_text('part off_sheet repeated_lab section_hidd hidden_on_vi\npart1 0 0 0 0\nTOTAL 0 0 0 0\n')
            for key in ('section_hidden', 'hidden_on_visible', 'off_sheet'):
                baseline.write_text(json.dumps({'part1': {'section_hidden': 0, 'hidden_on_visible': 0,
                                                          'off_sheet': 0, 'repeated_label': 0, key: 1}}))
                self.assertNotEqual(subprocess.run([sys.executable, '-I', str(checker), str(status),
                                                   str(baseline)], capture_output=True).returncode, 0)
            baseline.write_text(json.dumps({'part1': {'section_hidden': 0, 'hidden_on_visible': 0, 'off_sheet': 0, 'repeated_label': 0}}))
            self.assertEqual(subprocess.run([sys.executable, '-I', str(checker), str(status),
                                            str(baseline)], capture_output=True).returncode, 0)


class Completeness(unittest.TestCase):
    def test_quality_records_face_counts_and_unplanned_features(self):
        plan = {'sheet': {'scale': '1:1'}, 'views': [], 'dimensions': [],
                'completeness': {'claimed_faces': 3, 'faces': 52, 'unplanned_features': 7}}
        replies = [subprocess.CompletedProcess([], 0, '{}', ''),
                   subprocess.CompletedProcess([], 0, json.dumps(plan), '')]
        with tempfile.TemporaryDirectory() as out, patch.object(quality.subprocess, 'run', side_effect=replies), patch.object(quality, 'svg_linework', return_value={}):
            _, result = quality.one('part.step', 'dw', out)
        self.assertEqual(result['claimed_faces'], 3)
        self.assertEqual(result['faces'], 52)
        self.assertEqual(result['unplanned_features'], 7)

    def test_missing_completeness_is_unavailable_instead_of_perfect(self):
        plan = {'sheet': {'scale': '1:1'}, 'views': [], 'dimensions': []}
        replies = [subprocess.CompletedProcess([], 0, '{}', ''),
                   subprocess.CompletedProcess([], 0, json.dumps(plan), '')]
        with tempfile.TemporaryDirectory() as out, patch.object(quality.subprocess, 'run', side_effect=replies), patch.object(quality, 'svg_linework', return_value={}):
            _, result = quality.one('part.step', 'dw', out)
        self.assertIn('error', result)

    def test_checker_rejects_less_coverage_more_unplanned_or_changed_denominator(self):
        checker = Path(__file__).with_name('check_quality_table.py')
        with tempfile.TemporaryDirectory() as out:
            status, baseline = Path(out) / 'status.md', Path(out) / 'baseline.json'
            status.write_text('part ink claimed_face faces unplanned_fe\np 0 3 52 7\nTOTAL 0 3 52 7\n')
            for claimed, faces, unplanned, code in [(3,52,7,0),(4,52,6,0),(2,52,7,1),(3,52,8,1),(3,53,7,1)]:
                baseline.write_text(json.dumps({'p': {'ink': 0, 'claimed_faces': claimed,
                                                      'faces': faces, 'unplanned_features': unplanned}}))
                result = subprocess.run([sys.executable, '-I', str(checker), str(status), str(baseline)], capture_output=True)
                self.assertEqual(result.returncode, code, result.stderr.decode())


class RandomPins(unittest.TestCase):
    def plan(self):
        return {'dimensions': [{'id': 'd:a/length', 'placed': {}, 'state': {'kind': 'shown'}},
                               {'id': 'd:covered', 'placed': None}],
                'gdt': [{'id': 'pmi:a', 'placed': {}, 'state': {'kind': 'shown'}}],
                'views': [{'id': 'v:front', 'area': [0, 0, 10, 10]},
                          {'id': 'v:plan', 'area': [0, 20, 10, 30]}]}

    def test_selection_is_repeatable_and_pins_all_supported_kinds(self):
        plan = self.plan()
        spec, ids, candidates = quality.random_pins(plan, 'part', 1, 3)
        self.assertEqual((spec, ids, candidates), quality.random_pins(plan, 'part', 1, 3))
        self.assertEqual(candidates, 3)
        self.assertEqual(set(ids), {'d:a/length', 'pmi:a', 'v:plan'})
        self.assertTrue(spec['dimensions']['d:a/length']['place']['pinned'])
        self.assertTrue(spec['annotations']['pmi:a']['place']['pinned'])
        view = spec['views']['plan']['place']
        self.assertEqual(view, {'relative_to': 'front', 'side': 'above', 'gap': 10,
                                'align': 'projection', 'pinned': True})
        self.assertNotIn('at', view)

    def test_fewer_candidates_than_pins_pins_every_candidate(self):
        spec, ids, candidates = quality.random_pins(self.plan(), 'part', 1, 8)
        self.assertEqual((sorted(ids), candidates), (['d:a/length', 'pmi:a', 'v:plan'], 3))
        self.assertEqual(len(spec['dimensions']) + len(spec['annotations']) + len(spec['views']), 3)

    def test_selection_is_independent_of_plan_order_and_nested_for_n(self):
        plan = self.plan()
        one = quality.random_pins(plan, 'part', 2, 1)[1]
        plan['dimensions'].reverse()
        three = quality.random_pins(plan, 'part', 2, 3)[1]
        self.assertEqual(one, three[:1])

    def test_view_pins_use_a_root_on_their_own_sheet(self):
        plan = self.plan()
        plan['views'] = [
            dict(id='v:front', sheet=1, area=[10,10,30,30]),
            dict(id='v:side', sheet=1, area=[40,10,60,30]),
            dict(id='v:plan', sheet=2, area=[10,10,30,30]),
            dict(id='v:iso', sheet=2, area=[10,40,30,60]),
        ]
        spec, ids, _ = quality.random_pins(plan, 'part', 1, 4)
        self.assertEqual(len(ids),4)
        pages = {v['id'][2:]:v['sheet'] for v in plan['views']}
        for name, item in spec.get('views',{}).items():
            self.assertEqual(pages[name],pages[item['place']['relative_to']])
        if 'side' in spec.get('views',{}):
            self.assertEqual(spec['views']['side']['place']['align'],'projection')

    def test_pin_geometry_ignores_numeric_json_spelling_and_negative_zero(self):
        self.assertEqual(quality.pin_geometry(dict(value=1, placed=dict(lines=[[[0, 1], [2, 3]]]))),
                         quality.pin_geometry(dict(value=1.0, placed=dict(lines=[[[-0.0, 1.0], [2.0, 3.0]]]))))

    def test_pin_selection_ignores_every_output_id_and_input_order(self):
        import copy
        plan = self.plan()
        plan['dimensions'] = [
            dict(id=f'd:old:{n}', kind='linear', value=10+n, view='v:front',
                 placed=dict(lines=[[[n, 0], [n, 10]]], label_box=[n, 0, n+1, 1]))
            for n in range(12)]
        renamed = copy.deepcopy(plan)
        renames = {d['id']: f'd:renamed:{100-n}' for n, d in enumerate(renamed['dimensions'])}
        renames.update({'pmi:a': 'pmi:renamed'})
        for d in renamed['dimensions'] + renamed['gdt']:
            d['id'] = renames[d['id']]
        renamed['dimensions'].reverse()
        renamed['gdt'].reverse()
        renamed['views'].reverse()
        for seed in (1, 2, 3):
            for count in (1, 3, 8):
                expected = quality.random_pins(plan, 'part', seed, count)[1]
                actual = quality.random_pins(renamed, 'part', seed, count)[1]
                self.assertEqual(actual, [renames.get(i, i) for i in expected])

    def test_off_sheet_measurement_includes_pinned_views(self):
        plan = self.plan()
        plan['sheet'] = {'scale': '1:1'}
        plan['completeness'] = {'claimed_faces': 0, 'faces': 0, 'unplanned_features': 0}
        plan['dimensions'] = []
        render = {'problems': [
            {'code': 'annotation_off_sheet', 'subjects': ['d:a/length']},
            {'code': 'pinned_view_off_sheet', 'subjects': ['v:plan']}]}
        replies = [subprocess.CompletedProcess([], 0, json.dumps(render), ''),
                   subprocess.CompletedProcess([], 0, json.dumps(plan), '')]
        with tempfile.TemporaryDirectory() as out, patch.object(quality.subprocess, 'run', side_effect=replies), patch.object(quality, 'svg_linework', return_value={'hidden_on_visible': 0, 'section_hidden': 0}):
            _, result = quality.one('part.step', 'dw', out)
        self.assertEqual(result['off_sheet'], 2)
        self.assertEqual(result['off_sheet_items'], ['d:a/length', 'v:plan'])

    def test_bar_is_per_part_and_zero_ink_means_zero(self):
        self.assertTrue(quality.pin_bar({'ink': 2, 'off_sheet': 0}, {'ink': 3, 'off_sheet': 0}))
        self.assertFalse(quality.pin_bar({'ink': 0, 'off_sheet': 0}, {'ink': 1, 'off_sheet': 0}))
        self.assertFalse(quality.pin_bar({'ink': 10, 'off_sheet': 0}, {'ink': 0, 'off_sheet': 1}))
        self.assertFalse(quality.pin_bar({'ink': 2, 'off_sheet': 0}, {'ink': 4, 'off_sheet': 0}))
        self.assertFalse(quality.pin_bar({'ink': 2, 'off_sheet': 1, 'off_sheet_items': ['a']},
                                        {'ink': 2, 'off_sheet': 1, 'off_sheet_items': ['b']}))



class SheetFill(unittest.TestCase):
    def test_fill_is_measured_and_printed_as_percent(self):
        plan = {'sheet': {'scale': '1:1', 'size': 'A4', 'fill': .99, 'page': [120, 120], 'title_block': [80, 10, 110, 50]},
                'dimensions': [], 'views': [{'id': 'v:front', 'at': [45, 45], 'scale': 1, 'up': [0, 1, 0], 'direction': [0, 0, 1]}],
                'completeness': {'claimed_faces': 0, 'faces': 0, 'unplanned_features': 0}}
        replies = [subprocess.CompletedProcess([], 0, '{}', ''),
                   subprocess.CompletedProcess([], 0, json.dumps(plan), ''),
                   subprocess.CompletedProcess([], 0, json.dumps({'envelope': [[0,0,0],[50,50,10]]}), '')]
        with tempfile.TemporaryDirectory() as out, patch.object(quality.subprocess, 'run', side_effect=replies), patch.object(quality, 'furniture_boxes', return_value=[]):
            (Path(out) / 'part.svg').write_text('<svg/>')
            _, result = quality.one('part.step', 'dw', out)
        self.assertAlmostEqual(result['fill'], 100 * 52 * 52 / (10000 - 30 * 32), places=2)
        self.assertEqual((result['sheet'], result['scale']), ('A4', '1:1'))
        with contextlib.redirect_stdout(io.StringIO()) as output:
            quality.table({'part': result})
        self.assertIn('fill', output.getvalue())


class MultiSheetMeasurements(unittest.TestCase):
    def test_coincident_boxes_on_different_pages_are_not_overlaps(self):
        plan = dict(sheet=dict(title_block=[80,10,110,30]),
                    sheets=[dict(number=2,title_block=[80,10,110,30])],
                    views=[dict(id='v:front',area=[10,10,30,30]),
                           dict(id='v:plan',sheet=2,area=[10,10,30,30])])
        self.assertEqual(quality.view_overlap_counts(plan),(0,0))
        plan['views'][1]['sheet'] = 1
        self.assertEqual(quality.view_overlap_counts(plan),(1,0))
        plan['views'][1]['sheet'] = 2
        plan['views'][1]['area'] = [85,12,95,20]
        self.assertEqual(quality.view_overlap_counts(plan),(0,1))

    def test_svg_metrics_include_every_page_with_local_view_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            svg = Path(directory)/'part.svg'
            text = '<svg xmlns="http://www.w3.org/2000/svg"><g id="part" stroke-width="0.5"><line x1="10" y1="20" x2="30" y2="20"/></g><g id="hidden"><line x1="10" y1="20" x2="30" y2="20"/></g></svg>'
            svg.write_text(text)
            Path(quality.sheet_svg(str(svg),2)).write_text(text)
            plan = dict(sheet={},sheets=[dict(number=2)],dimensions=[],views=[
                dict(id='v:front',area=[5,5,35,35]),
                dict(id='v:section_aa',sheet=2,cut={},area=[5,5,35,35])])
            plan['views'][1]['cut'] = {'label':'A'}
            result = quality.svg_linework(str(svg),plan)
            self.assertEqual(result['hidden_on_visible'],2)
            self.assertEqual(result['section_hidden'],1)
            local = quality.local_sheet_plan(plan,plan['sheets'][0])
            self.assertEqual([v['id'] for v in local['views']],['v:section_aa'])
            self.assertEqual(len(plan['views']),2)


class CrossingKinds(unittest.TestCase):
    def test_regression_recorders_require_a_review_reason_before_writing(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'baseline.json'
            target.write_text('unchanged')
            for tool, source in [('check_leader_crossings.py', ['unused']),
                                 ('check_label_lanes.py', ['--current', 'unused'])]:
                for reason in ([], ['--reason', '   ']):
                    result = subprocess.run([sys.executable, '-I', str(root / 'tools' / tool),
                                             *source, '--record', str(target), *reason],
                                            capture_output=True, text=True)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertIn('nonempty --reason', result.stderr)
                    self.assertEqual(target.read_text(), 'unchanged')

    def test_old_geometry_matches_additive_metadata_and_counts_each_kind_once(self):
        far = [[20, 20], [30, 20]]
        vertical = [[5, -5], [5, 5]]
        def item(id_, kind, lines, arrows=()):
            return dict(id=id_, kind=kind, view='v:plan', placed=dict(lines=lines, arrows=list(arrows), label_box=[40,40,45,45]))
        a = item('a', 'callout', [[[0,0], [10,0]]])
        b = item('b', 'linear', [vertical, far, vertical, vertical], [vertical])
        b['placed']['label_box'] = [4,-1,6,1]
        crossing = dict(code='annotation_leader_crossing', subjects=['a','b'])
        ink = dict(code='annotation_ink_overlap', subjects=['a','b'])
        plan = dict(dimensions=[a,b], problems=[crossing,ink])
        old = quality.crossing_counts(plan)
        self.assertEqual(old, dict(leader_leader=0, leader_text=1, leader_dimension_line=1,
                                  leader_extension_line=1, leader_arrow=1, dimension_dimension=0))
        crossing['crossing_kinds'] = ['dimension_line', 'extension_line', 'arrow']
        ink['ink_is_leader'] = True
        self.assertEqual(quality.crossing_counts(plan), old)
        ink['ink_is_leader'] = False
        a['kind'] = 'linear'
        self.assertEqual(quality.crossing_counts(plan)['leader_text'], 0)
        self.assertFalse(quality.line_through_label([[0,1],[10,1]], [4,-1,6,1]))

    def test_overlap_warning_deduplication_cannot_hide_leader_through_text(self):
        leader = dict(id='leader', kind='callout', view='v:plan', placed=dict(
            lines=[[[0,0],[10,0]]], arrows=[], label_box=[0,-1,8,1]))
        label = dict(id='label', kind='linear', view='v:plan', placed=dict(
            lines=[], arrows=[], label_box=[4,-1,12,1]))
        plan = dict(dimensions=[leader,label], problems=[dict(
            code='annotation_overlap', subjects=['leader','label'])])
        self.assertEqual(quality.crossing_instances(plan)['leader_text'], [['leader','label']])
        # Different sheets are separate drawings even when their coordinates coincide.
        label['view'] = 'v:other'
        plan['views'] = [dict(id='v:plan', sheet=1), dict(id='v:other', sheet=2)]
        self.assertEqual(quality.crossing_counts(plan)['leader_text'], 0)

    def test_datum_proxies_are_counted_in_old_and_new_plans(self):
        placed = dict(lines=[[[0,0],[10,0]]], arrows=[], label_box=[20,20,25,25])
        datum = dict(id='pmi:datum.A', kind='datum_feature', view='v:plan', placed=placed)
        peer = dict(id='pmi:frame.B', kind='feature_control_frame', view='v:plan', placed={
            **placed, 'lines': [[[5,-5],[5,5]]], 'label_box': [4,-1,6,1]})
        crossing = dict(code='annotation_leader_crossing', subjects=[datum['id'],peer['id']])
        ink = dict(code='annotation_ink_overlap', subjects=crossing['subjects'])
        plan = dict(dimensions=[], gdt=[datum,peer], problems=[crossing,ink])
        old = quality.crossing_counts(plan)
        self.assertEqual((old['leader_leader'], old['leader_text']), (1,1))
        crossing['crossing_kinds'] = ['leader']
        ink['ink_is_leader'] = True
        self.assertEqual(quality.crossing_counts(plan), old)

    def test_requirement_symbols_read_either_box(self):
        # Datum symbols record `glyph`; surface-texture symbols record `label_box` instead.
        leader = dict(id='leader', kind='callout', view='', placed=dict(
            lines=[[[0,0],[10,0]]], arrows=[], label_box=[30,30,32,32]))
        finish = dict(id='pmi:finish', placed=dict(box=[2,-2,8,2], label_box=[2,-1,8,1],
                                                    strokes=[], text_at=[2,0]))
        datum = dict(id='pmi:datum', placed=dict(glyph=[40,40,45,45], strokes=[]))
        plan = dict(dimensions=[leader], requirements=[finish, datum], problems=[])
        self.assertEqual(quality.crossing_instances(plan)['leader_text'], [['leader','pmi:finish']])

    def test_crossing_gate_rejects_each_kind_per_part_and_incomplete_evidence(self):
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location('crossings', root / 'tools/check_leader_crossings.py')
        gate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gate)
        names = gate.part_names()
        self.assertTrue(any('_ap242' in n for n in names))
        before = {n: dict.fromkeys(quality.CROSSING_KEYS, 0) for n in names}
        before[names[0]]['leader_leader'] = 2
        self.assertEqual(gate.check(before, before), [])
        for kind in quality.CROSSING_KEYS:
            after = {n: dict(r) for n,r in before.items()}
            after[names[0]][kind] += 1
            self.assertIn(kind, gate.check(before, after)[0])
            del after[names[0]][kind]
            with self.assertRaises(ValueError):
                gate.check(before, after)
        with self.assertRaises(ValueError):
            gate.check(before, {})
        improved = {n: dict(r) for n,r in before.items()}
        improved[names[0]]['leader_leader'] = 1
        self.assertEqual(gate.check(before, improved), [])

if __name__ == '__main__':
    unittest.main()
