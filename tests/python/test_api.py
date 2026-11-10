"""Run against an installed wheel: python -m unittest discover -s tests/python -v."""
import importlib
import importlib.util
import json
import os
import re
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

import draftwright

ROOT = Path(__file__).resolve().parents[2]
PLATE = ROOT / "tests/fixtures/parts/plate.step"


class PythonAPI(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def command(self, *args, check=True):
        return subprocess.run(args, check=check, capture_output=True, text=True, timeout=60)

    def spec(self, drawing):
        return json.loads(self.command("draftwright", "spec", str(drawing)).stdout)

    def assert_files(self, paths):
        self.assertIsInstance(paths, tuple)
        self.assertEqual(len(paths), 2)
        self.assertTrue(all(isinstance(path, str) for path in paths))
        svg, pdf = map(Path, paths)
        self.assertTrue(svg.read_text().startswith("<svg") or "<svg" in svg.read_text())
        self.assertTrue(pdf.read_bytes().startswith(b"%PDF-"))
        self.assertFalse(svg.with_suffix(".dxf").exists())
        return svg, pdf

    def test_path_and_keyword_mapping(self):
        title = dict(title="Plate", number="D-42", material="Steel", drawn_by="Author",
                     date="2026-11-10", revision="B", company="Example", approved_by="Reviewer",
                     tolerance="ISO 2768-m", document_type="PART", sheet="1/1")
        svg, pdf = self.assert_files(draftwright.make_drawing(
            PLATE, out=self.directory / "plate.pdf", scale=0.5, page="A3",
            projection="first", zones=True, frame=True, **title,
        ))
        self.assertEqual(svg, self.directory / "plate.svg")
        self.assertEqual(pdf, self.directory / "plate.pdf")
        spec = self.spec(svg)
        for key, value in title.items():
            self.assertEqual(spec["sheet"]["title"][key], value, key)
        self.assertEqual(spec["sheet"]["scale"], "1:2")
        self.assertEqual(spec["sheet"]["size"], "A3")
        self.assertEqual(spec["sheet"]["projection"], "first")
        self.assertTrue(spec["sheet"]["zones"])

    def test_default_destination_and_old_positional_arguments(self):
        step = self.directory / "input.step"
        shutil.copyfile(PLATE, step)
        svg, pdf = self.assert_files(draftwright.make_drawing(str(step)))
        self.assertEqual(svg, step.with_suffix(".svg"))
        self.assertEqual(pdf, step.with_suffix(".pdf"))
        defaults = self.spec(svg)["sheet"]
        self.assertEqual(defaults["title"]["number"], "DWG-001")
        self.assertEqual(defaults["title"]["revision"], "A")
        self.assertFalse(defaults["zones"])
        self.assert_files(draftwright.make_drawing(step, str(self.directory / "positional"), "T", "N"))

    def test_dimension_and_pmi_options_map_to_spec(self):
        for mode, expected in (("off", "ignore"), ("report", "ignore"), ("annotate", "first")):
            with self.subTest(pmi=mode):
                svg, _ = self.assert_files(draftwright.make_drawing(
                    PLATE, out=self.directory / mode, auto_dims=False, pmi=mode,
                ))
                spec = self.spec(svg)
                self.assertEqual(spec["dimension_source"], "authored")
                self.assertFalse(spec["sheet"]["centre_lines"])
                self.assertFalse(spec["sheet"]["centre_marks"])
                self.assertEqual(spec.get("pmi", "first"), expected)

    def test_unsupported_non_defaults_are_named_before_io(self):
        for name, value in dict(
            detail_view=False, pmi="unknown", assembly=True,
            scale_policy="strict", reproducible=False, framed_recognition=True,
            text_position="above", text_orientation="horizontal", projection_symbol=False,
            margin_left=10, margin_right=10, margin_top=10, margin_bottom=10,
            title_block_width=100, leader_region="exterior", annotation_layout="compare",
            page=(210, 297),
        ).items():
            with self.subTest(name=name), self.assertRaisesRegex(TypeError, f"{name!r}.*not supported in 0.6"):
                draftwright.make_drawing("does-not-exist.step", **{name: value})
        with self.assertRaisesRegex(TypeError, "never_existed"):
            draftwright.make_drawing(PLATE, never_existed=True)

    def test_unsupported_defaults_are_accepted(self):
        self.assert_files(draftwright.make_drawing(
            PLATE, out=self.directory / "defaults", auto_dims=True, detail_view=True,
            pmi=None, assembly=None, scale_policy="fallback", reproducible=True,
            framed_recognition=False, text_position="inline", text_orientation="aligned",
            projection_symbol=True, margin_left=None, margin_right=None, margin_top=None,
            margin_bottom=None, title_block_width=None, leader_region="auto",
            annotation_layout="demand-guided",
        ))

    def test_rust_errors_have_public_exception_class(self):
        for options in ({}, {"scale": 0}, {"projection": "sideways"}):
            with self.subTest(options=options), self.assertRaises(draftwright.DraftwrightError) as raised:
                draftwright.make_drawing("does-not-exist.step", **options)
            self.assertTrue(str(raised.exception))
        self.assertEqual(draftwright.DraftwrightError.__module__, "draftwright")

    def test_every_removed_public_name_and_unknown_names(self):
        from draftwright._legacy import OLD_ALL
        for name in OLD_ALL - {"make_drawing"}:
            with self.subTest(name=name), self.assertRaises(AttributeError) as raised:
                getattr(draftwright, name)
            message = str(raised.exception)
            for text in (name, "0.4 API", "0.6 does not include", "draftwright<0.6", "Spec", "docs/model.md"):
                self.assertIn(text, message)
        with self.assertRaisesRegex(AttributeError, "has no attribute 'never_existed'"):
            draftwright.never_existed
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("draftwright.never_existed")

    def test_old_submodules_have_the_same_migration_error(self):
        for module in ("sheet", "builder", "model.ir", "audit", "sheet_emit", "evaluation.step_analysis"):
            with self.subTest(module=module), self.assertRaisesRegex(AttributeError, "0.4 API.*draftwright<0.6.*Spec"):
                importlib.import_module("draftwright." + module)
        with self.assertRaisesRegex(AttributeError, "draftwright<0.6"):
            exec("from draftwright.sheet import Sheet", {})
        with self.assertRaisesRegex(AttributeError, "draftwright<0.6"):
            exec("from draftwright import Sheet", {})

    def test_import_and_path_use_do_not_load_build123d(self):
        result = self.command(sys.executable, "-c", "import draftwright, sys; assert 'build123d' not in sys.modules")
        self.assertEqual(result.returncode, 0)

    def test_version_matches_cargo(self):
        expected = re.search(r'^version = "([^"\n]+)"', (ROOT / "Cargo.toml").read_text(), re.MULTILINE).group(1)
        self.assertEqual(draftwright.__version__, expected)

    def test_entry_points_version_completions_and_exit_codes(self):
        for command in ("draftwright", "draftwright-mcp"):
            version = self.command(command, "--version").stdout
            self.assertIn(draftwright.__version__, version)
            self.assertIn("quiddity-rust", version)
        completion = self.command("draftwright", "completions", "bash").stdout
        self.assertIn("draftwright", completion)
        self.assertNotIn("_native", completion)
        self.assertEqual(self.command("draftwright", "render", check=False).returncode, 2)
        self.assertEqual(self.command("draftwright", "render", "missing.step", "-o", "unused", check=False).returncode, 1)

    @unittest.skipUnless(importlib.util.find_spec("build123d"), "build123d is optional")
    def test_build123d_shape_and_source(self):
        from build123d import Box, import_step
        self.assert_files(draftwright.make_drawing(Box(30, 20, 5), out=self.directory / "shape"))
        self.assert_files(draftwright.make_drawing(
            import_step(PLATE), source=PLATE, out=self.directory / "source",
        ))
        with self.assertRaises(draftwright.DraftwrightError):
            draftwright.make_drawing(Box(30, 20, 5), source="missing.step", out=self.directory / "bad")
        # Shape defaults must survive cleanup of the temporary STEP directory.
        code = "from build123d import Box; from draftwright import make_drawing; print(make_drawing(Box(30,20,5)))"
        subprocess.run([sys.executable, "-c", code], cwd=self.directory, check=True,
                       capture_output=True, text=True, timeout=60)
        self.assertTrue((self.directory / "drawing.svg").exists())
        self.assertTrue((self.directory / "drawing.pdf").exists())

    @unittest.skipIf(os.name == "nt", "POSIX SIGINT test; Windows console uses CTRL_C_EVENT")
    def test_ctrl_c_stops_render_and_mcp_stdio(self):
        # Start a render that is much longer than the signal delay, plus an idle server
        # whose stdin remains open. The marker ensures Python has installed SIG_DFL.
        for entry, args in (
            ("main", ["draftwright", "render", str(PLATE.with_name("scattered_holes.step")), "-o", str(self.directory / "interrupt")]),
            ("mcp", ["draftwright-mcp"]),
        ):
            with self.subTest(entry=entry):
                native = "cli" if entry == "main" else "mcp"
                code = (
                    "import sys\nfrom draftwright import _cli, _native\n"
                    f"original = _native.{native}\n"
                    "def ready(args):\n    print('ready', flush=True)\n    return original(args)\n"
                    f"_native.{native} = ready\nsys.argv = {args!r}\n"
                    f"sys.exit(_cli.{entry}())\n"
                )
                process = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    self.assertEqual(process.stdout.readline().strip(), "ready")
                    time.sleep(0.02)
                    process.send_signal(signal.SIGINT)
                    process.communicate(timeout=5)
                    self.assertEqual(process.returncode, -signal.SIGINT)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.communicate()


if __name__ == "__main__":
    unittest.main()
