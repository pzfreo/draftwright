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
import xml.etree.ElementTree as ET

import draftwright

ROOT = Path(__file__).resolve().parents[2]
PLATE = ROOT / "tests/fixtures/parts/plate.step"
PMI = ROOT / "tests/fixtures/pmi/step/bolt_thread_knurl.step.gz"


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
        for mode, expected in ((None, "ignore"), ("off", "ignore"), ("report", "ignore"), ("annotate", "first")):
            with self.subTest(pmi=mode):
                svg, _ = self.assert_files(draftwright.make_drawing(
                    # A no-PMI part intentionally canonicalizes inactive policies to
                    # `first` in embedded Specs. Use real PMI to verify this mapping.
                    PMI,
                    out=self.directory / str(mode), auto_dims=False, pmi=mode,
                ))
                spec = self.spec(svg)
                self.assertEqual(spec["dimension_source"], "authored")
                self.assertFalse(spec["sheet"]["centre_lines"])
                self.assertFalse(spec["sheet"]["centre_marks"])
                self.assertEqual(spec.get("pmi", "first"), expected)

    def test_default_pmi_matches_off_and_report_and_annotate_opts_in(self):
        # Compare actual output from this engine, not fixed hashes or text counts.
        for auto_dims in (True, False):
            drawings = {}
            for mode in (None, "off", "report", "annotate"):
                options = {} if mode is None else {"pmi": mode}
                paths = draftwright.make_drawing(
                    PMI, out=self.directory / f"{auto_dims}-{mode}",
                    auto_dims=auto_dims, **options,
                )
                drawings[mode] = tuple(Path(path).read_bytes() for path in paths)
            with self.subTest(auto_dims=auto_dims):
                self.assertEqual(drawings[None], drawings["off"])
                self.assertEqual(drawings[None], drawings["report"])
                self.assertNotEqual(drawings[None][0], drawings["annotate"][0])
                self.assertNotEqual(drawings[None][1], drawings["annotate"][1])
                def visible_text(svg):
                    return tuple("".join(node.itertext()) for node in ET.fromstring(svg).iter()
                                 if node.tag.rsplit("}", 1)[-1] == "text")
                self.assertNotEqual(visible_text(drawings[None][0]), visible_text(drawings["annotate"][0]))

    def test_step_path_source_is_read_and_missing_source_errors(self):
        self.assert_files(draftwright.make_drawing(
            PLATE, source=PLATE, out=self.directory / "path-source",
        ))
        with self.assertRaisesRegex(draftwright.DraftwrightError, "missing-source.step"):
            draftwright.make_drawing(
                PLATE, source=self.directory / "missing-source.step", out=self.directory / "bad",
            )

    def test_iso_page_names_are_case_insensitive_and_invalid_values_are_named(self):
        for page in ("a3", " A3 ", "a4"):
            with self.subTest(page=page):
                svg, _ = self.assert_files(draftwright.make_drawing(
                    PLATE, page=page, out=self.directory / page.strip(),
                ))
                self.assertEqual(self.spec(svg)["sheet"]["size"], page.strip().upper())
        for page in ("LETTER", "420x297", (420, 297)):
            with self.subTest(page=page), self.assertRaises(TypeError) as raised:
                draftwright.make_drawing("missing.step", page=page)
            self.assertIn(f"page value {page!r}", str(raised.exception))
            self.assertIn("not supported in 0.6", str(raised.exception))

    def test_unsupported_non_defaults_are_named_before_io(self):
        for name, value in dict(
            detail_view=False, pmi="unknown", assembly=True,
            scale_policy="strict", reproducible=False, framed_recognition=True,
            text_position="above", text_orientation="horizontal", projection_symbol=False,
            margin_left=10, margin_right=10, margin_top=10, margin_bottom=10,
            title_block_width=100, leader_region="exterior", annotation_layout="compare",
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
        from draftwright._legacy import OLD_MODULES
        self.assertEqual(len(OLD_MODULES), 88)
        self.assertTrue({"fonts", "recognition", "annotate", "_core"} <= OLD_MODULES)
        for module in sorted(OLD_MODULES - {"builder"}) + ["model.ir", "evaluation.step_analysis"]:
            with self.subTest(module=module), self.assertRaisesRegex(ImportError, "0.4 API.*draftwright<0.6.*Spec"):
                importlib.import_module("draftwright." + module)
        with self.assertRaisesRegex(ImportError, "draftwright<0.6"):
            exec("from draftwright.sheet import Sheet", {})

    def test_removed_names_preserve_import_fallbacks_and_attribute_probes(self):
        from draftwright._legacy import OLD_ALL
        for name in OLD_ALL - {"make_drawing"}:
            with self.subTest(name=name):
                self.assertFalse(hasattr(draftwright, name))
                with self.assertRaises(AttributeError) as attribute:
                    getattr(draftwright, name)
                with self.assertRaises(ImportError) as imported:
                    exec(f"from draftwright import {name}", {})
                self.assertEqual(str(attribute.exception), str(imported.exception))
        namespace = {}
        exec("try:\n from draftwright import build_drawing\nexcept ImportError:\n fallback = True", namespace)
        self.assertTrue(namespace["fallback"])
        namespace = {}
        exec("try:\n import draftwright.sheet\nexcept ImportError:\n fallback = True", namespace)
        self.assertTrue(namespace["fallback"])
        self.assertFalse(hasattr(draftwright, "Sheet"))

        # CPython 3.9 supports the requested dual base. CPython >=3.10 rejects
        # that layout; attribute/import boundaries must preserve each protocol.
        from draftwright._legacy import AttributeMigrationError, ImportMigrationError
        self.assertTrue(issubclass(AttributeMigrationError, AttributeError))
        self.assertTrue(issubclass(ImportMigrationError, ImportError))
        try:
            type("DualErrorProbe", (ImportError, AttributeError), {})
        except TypeError:
            self.assertIsNot(AttributeMigrationError, ImportMigrationError)
        else:
            self.assertIs(AttributeMigrationError, ImportMigrationError)
            self.assertTrue(issubclass(AttributeMigrationError, ImportError))
            self.assertTrue(issubclass(AttributeMigrationError, AttributeError))

    def test_builder_reexports_supported_function_and_refuses_removed_names(self):
        from draftwright.builder import make_drawing
        self.assertIs(make_drawing, draftwright.make_drawing)
        self.assert_files(make_drawing(PLATE, out=self.directory / "builder"))
        builder = importlib.import_module("draftwright.builder")
        self.assertFalse(hasattr(builder, "build_drawing"))
        with self.assertRaisesRegex(AttributeError, "draftwright.builder.build_drawing.*0.4 API"):
            builder.build_drawing
        with self.assertRaisesRegex(ImportError, "draftwright.builder.build_drawing.*0.4 API"):
            exec("from draftwright.builder import build_drawing", {})

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
        self.assertEqual(self.command("draftwright", "render", "missing.step", "-o", "unused", check=False).returncode, 2)
        self.assertEqual(self.command(
            "draftwright", "render", str(PLATE), "-o", str(self.directory / "absent" / "out"), check=False,
        ).returncode, 1)

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
                    if entry == "mcp":
                        process.stdin.write('{"jsonrpc":"2.0","id":1,"method":"ping"}\n')
                        process.stdin.flush()
                        self.assertEqual(json.loads(process.stdout.readline())["result"], {})
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
