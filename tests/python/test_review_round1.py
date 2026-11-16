"""Independent review regressions: Python convenience data stays Python-facing."""
import json
from importlib.metadata import PackageNotFoundError
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import draftwright
from draftwright._build123d import check_version, _version_check

try:
    import build123d as bd
except ModuleNotFoundError as error:
    if error.name != 'build123d':
        raise
    bd = None

ROOT = Path(__file__).resolve().parents[2]


class VersionGuard(unittest.TestCase):
    def setUp(self):
        _version_check.cache_clear()
        self.addCleanup(_version_check.cache_clear)

    def test_missing_metadata_is_a_cached_draftwright_error(self):
        with patch('draftwright._build123d.version', side_effect=PackageNotFoundError('build123d')) as version:
            for _ in range(3):
                with self.assertRaisesRegex(draftwright.DraftwrightError, r'build123d>=0\.10.*metadata.*not found'):
                    check_version()
            self.assertEqual(version.call_count, 1)

    def test_successful_check_is_cached(self):
        with patch('draftwright._build123d.version', return_value='0.13.0') as version:
            for _ in range(3):
                self.assertEqual(check_version(), (0, 13, 0))
            self.assertEqual(version.call_count, 1)


class PackReportBoundary(unittest.TestCase):
    def test_python_findings_extend_but_do_not_modify_native_report_or_cli_json(self):
        fixture = ROOT / 'tests/fixtures/assembly/pack-small.step.gz'
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            pack = draftwright.Pack(fixture)
            pack.render(directory / 'python', formats=('json',))
            subprocess.run(['draftwright', 'pack', str(fixture), '-o', str(directory / 'cli'),
                            '--formats', 'json'], check=True, capture_output=True)
            python_json = (directory / 'python.json').read_bytes()
            self.assertEqual(python_json, (directory / 'cli/pack.json').read_bytes())
            native = json.loads(python_json)
            self.assertEqual(pack.report, native)
            count = sum(len(row['problems']) for row in native['rows'])
            self.assertGreater(count, 0)
            self.assertEqual(len(pack.findings), len(native['findings']) + count)
            self.assertEqual(pack.findings[:len(native['findings'])], native['findings'])
            self.assertTrue(all('part' not in finding for finding in native['findings']))
            for row in native['rows']:
                for problem in row['problems']:
                    self.assertTrue(any(f.get('part') == row['name'] and
                                        f['code'] == problem['code'] and
                                        f['severity'] == problem['severity'] and
                                        problem['message'] in f['message'] and
                                        set(f) == {'part', 'products', 'code', 'severity', 'message'}
                                        for f in pack.findings))


@unittest.skipIf(bd is None, 'build123d is optional')
class HoleDepth(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def test_cross_hole_splits_bore_but_not_its_full_depth(self):
        tap = bd.Cylinder(2.5, 30)
        cross = bd.Rot(0, 90, 0) * bd.Cylinder(4, 30)
        part = draftwright.Part(bd.Box(30, 20, 30) - tap - cross, 'split')
        hole = part.hole(tap)
        self.assertGreater(len(hole.bores), 1)
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        spans = [BRepAdaptor_Surface(part._faces[i].wrapped) for i in hole.bores]
        self.assertTrue(all(s.LastVParameter() - s.FirstVParameter() < 20 for s in spans))
        hole.thread('M6', depth=20)
        records = json.loads(part._geometry().inspect(part._pmi))['records']
        record = next(r for r in records if r['kind'] == 'internal_thread')
        self.assertIn(['minimum full thread', 20.0], record['fields'])
        with self.assertRaisesRegex(ValueError, "exceeds the hole's depth"):
            hole.thread('M6', depth=31)

    def test_partial_thread_on_a_through_hole(self):
        tap = bd.Cylinder(2.5, 18)
        part = draftwright.Part(bd.Box(30, 20, 18) - tap, 'through')
        hole = part.hole(tap).thread('M6', depth=12)
        drawing = part.drawing()
        paths = drawing.render(self.directory / 'through', formats=('pdf',))
        from pypdf import PdfReader
        text = ' '.join(page.extract_text() for page in PdfReader(paths[0]).pages)
        self.assertIn('M6-6H ↧ 12', text)
        self.assertNotIn('M6-6H THRU', text)
        self.assertFalse([p for p in drawing.problems if p['code'] in ('pmi_not_lowered', 'pmi_not_rendered')])
        hole.thread('M6', depth=18)
        with self.assertRaisesRegex(ValueError, "exceeds the hole's depth"):
            hole.thread('M6', depth=19)

    def test_opposed_blind_holes_do_not_combine_across_a_solid_web(self):
        upper = bd.Pos(0, 0, 11) * bd.Cylinder(2.5, 8)
        lower = bd.Pos(0, 0, -11) * bd.Cylinder(2.5, 8)
        cutters = upper + lower
        part = draftwright.Part(bd.Box(30, 20, 30) - cutters, 'opposed')
        holes = part.holes(cutters).thread('M6', depth=6)
        with self.assertRaisesRegex(ValueError, "exceeds the hole's depth"):
            holes.thread('M6', depth=12)
