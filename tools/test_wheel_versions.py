"""Exercise the publish guard as a command, including all version mismatch directions."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile


GUARD = Path(__file__).with_name("check_wheel_versions.py").resolve()


class WheelVersions(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.dist = self.root / "dist"
        self.dist.mkdir()
        self.manifest("Cargo.toml", "0.6.0")
        self.manifest("crates/draftwright-wasm/Cargo.toml", "0.6.0")
        self.wheel("linux", "0.6.0")
        self.wheel("macos", "0.6.0")

    def manifest(self, name, version):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'[package]\nversion = "{version}"\n')

    def wheel(self, platform, version, name="draftwright"):
        with zipfile.ZipFile(self.dist / f"draftwright-0.6.0-py3-none-{platform}.whl", "w") as wheel:
            wheel.writestr(
                "draftwright-0.6.0.dist-info/METADATA",
                f"Metadata-Version: 2.4\nName: {name}\nVersion: {version}\n\n",
            )

    def run_guard(self, tag="v0.6.0", dist=None):
        return subprocess.run(
            [sys.executable, str(GUARD), "--tag", tag, "--root", str(self.root),
             "--dist", str(dist or self.dist)],
            capture_output=True, text=True,
        )

    def refused(self, result, message):
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(message, result.stderr)
        self.assertIn("::error::", result.stderr)

    def test_match(self):
        result = self.run_guard()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("2 wheel(s) 0.6.0: they agree", result.stdout)

    def test_tag_mismatch(self):
        self.refused(self.run_guard("v0.6.1"), "Cargo.toml 0.6.0: they must agree")

    def test_each_cargo_mismatch(self):
        for manifest in ("Cargo.toml", "crates/draftwright-wasm/Cargo.toml"):
            with self.subTest(manifest=manifest):
                self.manifest(manifest, "0.6.1")
                self.refused(self.run_guard(), f"{manifest} 0.6.1: they must agree")
                self.manifest(manifest, "0.6.0")

    def test_one_mismatched_wheel_among_matching_wheels(self):
        self.wheel("macos", "0.6.1")
        self.refused(self.run_guard(), "['0.6.1']: they must agree")

    def test_plain_tag_required(self):
        for tag in ("", "0.6.0", "v0.6", "v0.6.0rc1", "v0.6.0-alpha.1", "v00.6.0"):
            with self.subTest(tag=tag):
                self.refused(self.run_guard(tag), "expected vX.Y.Z")

    def test_matching_old_release_cannot_start_pypi_publishing(self):
        for manifest in ("Cargo.toml", "crates/draftwright-wasm/Cargo.toml"):
            self.manifest(manifest, "0.2.15")
        for platform in ("linux", "macos"):
            self.wheel(platform, "0.2.15")
        self.refused(self.run_guard("v0.2.15"), "PyPI publishing begins with 0.6.0")

    def test_no_wheels(self):
        empty = self.root / "empty"
        empty.mkdir()
        self.refused(self.run_guard(dist=empty), "no wheels to publish")

    def test_wrong_package(self):
        self.wheel("macos", "0.6.0", name="other")
        self.refused(self.run_guard(), "expected package draftwright")

    def test_missing_metadata(self):
        with zipfile.ZipFile(self.dist / "empty.whl", "w"):
            pass
        self.refused(self.run_guard(), "expected exactly one wheel METADATA")


if __name__ == "__main__":
    unittest.main()
