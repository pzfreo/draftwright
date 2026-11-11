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
        self.manifest("crates/draftwright-py/Cargo.toml", "0.6.0")
        self.git("init", "-q")
        self.git("config", "user.name", "Wheel guard test")
        self.git("config", "user.email", "wheel-test@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "tag.gpgsign", "false")
        self.git("add", "Cargo.toml", "crates")
        self.git("commit", "-qm", "Release fixture")
        self.git("tag", "v0.6.0")
        self.git("tag", "v0.6.1")
        self.commit = self.git("rev-parse", "HEAD").stdout.strip()
        self.provenance = self.root / "provenance"
        self.provenance.mkdir()
        self.wheel("linux", "0.6.0")
        self.wheel("macos", "0.6.0")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, check=True,
                              capture_output=True, text=True)

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

        (self.provenance / f"draftwright-0.6.0-py3-none-{platform}.whl.commit").write_text(
            self.commit + "\n"
        )

    def run_guard(self, tag="v0.6.0", dist=None, provenance=None):
        return subprocess.run(
            [sys.executable, str(GUARD), "--tag", tag, "--root", str(self.root),
             "--dist", str(dist or self.dist), "--provenance", str(provenance or self.provenance)],
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

    def test_annotated_tag_match(self):
        self.git("tag", "-f", "-a", "v0.6.0", "-m", "Release")
        result = self.run_guard()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_tag(self):
        self.refused(self.run_guard("v0.6.2"), "refs/tags/v0.6.2^{commit}")

    def test_checkout_is_not_the_tag_commit(self):
        (self.root / "source.txt").write_text("newer main code")
        self.git("add", "source.txt")
        self.git("commit", "-qm", "Newer main")
        self.refused(self.run_guard(), "checked-out commit")

    def test_one_wheel_built_from_the_wrong_commit(self):
        (self.provenance / "draftwright-0.6.0-py3-none-macos.whl.commit").write_text("0" * 40)
        self.refused(self.run_guard(), "built commit")

    def test_missing_build_provenance(self):
        empty = self.root / "no-provenance"
        empty.mkdir()
        self.refused(self.run_guard(provenance=empty), "missing build provenance")

    def test_tag_mismatch(self):
        self.refused(self.run_guard("v0.6.1"), "Cargo.toml 0.6.0: they must agree")

    def test_each_cargo_mismatch(self):
        for manifest in ("Cargo.toml", "crates/draftwright-wasm/Cargo.toml", "crates/draftwright-py/Cargo.toml"):
            with self.subTest(manifest=manifest):
                self.manifest(manifest, "0.6.1")
                self.refused(self.run_guard(), f"{manifest} 0.6.1: they must agree")
                self.manifest(manifest, "0.6.0")

    def test_one_mismatched_wheel_among_matching_wheels(self):
        self.wheel("macos", "0.6.1")
        self.refused(self.run_guard(), "['0.6.1']: they must agree")

    def test_malformed_tag_refused(self):
        # Pre-release tags are accepted since alpha publishing (vX.Y.Z-alpha.N etc.); every
        # other shape is still refused, including PEP 440 spellings in the tag.
        for tag in ("", "0.6.0", "v0.6", "v0.6.0rc1", "v0.6.0a1", "v00.6.0", "v0.6.0-alpha",
                    "v0.6.0-alpha.0", "v0.6.0-dev.1", "v0.6.0-alpha.01", "v0.6.0-ALPHA.1"):
            with self.subTest(tag=tag):
                self.refused(self.run_guard(tag), "expected vX.Y.Z")

    def prerelease(self, cargo, wheel, tag):
        for manifest in ("Cargo.toml", "crates/draftwright-wasm/Cargo.toml", "crates/draftwright-py/Cargo.toml"):
            self.manifest(manifest, cargo)
        self.git("add", "Cargo.toml", "crates")
        self.git("commit", "-qm", "Pre-release fixture")
        self.git("tag", tag)
        self.commit = self.git("rev-parse", "HEAD").stdout.strip()
        for platform in ("linux", "macos"):
            self.wheel(platform, wheel)

    def test_prerelease_match(self):
        for cargo, wheel in (("0.6.0-alpha.1", "0.6.0a1"), ("0.6.0-beta.2", "0.6.0b2"), ("0.6.0-rc.3", "0.6.0rc3")):
            with self.subTest(cargo=cargo):
                self.setUp()
                self.prerelease(cargo, wheel, f"v{cargo}")
                result = self.run_guard(f"v{cargo}")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"2 wheel(s) {wheel}: they agree", result.stdout)

    def test_prerelease_wheel_must_use_pep440(self):
        # A wheel spelled like the Cargo version, or a different pre-release, is refused.
        for wheel in ("0.6.0-alpha.1", "0.6.0a2", "0.6.0"):
            with self.subTest(wheel=wheel):
                self.setUp()
                self.prerelease("0.6.0-alpha.1", wheel, "v0.6.0-alpha.1")
                self.refused(self.run_guard("v0.6.0-alpha.1"), "they must agree")

    def test_prerelease_cargo_mismatch(self):
        self.prerelease("0.6.0-alpha.1", "0.6.0a1", "v0.6.0-alpha.1")
        self.refused(self.run_guard("v0.6.0-alpha.2"), "does not exist")

    def test_matching_old_release_cannot_start_pypi_publishing(self):
        for manifest in ("Cargo.toml", "crates/draftwright-wasm/Cargo.toml", "crates/draftwright-py/Cargo.toml"):
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
