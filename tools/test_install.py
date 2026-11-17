"""Offline installer regression tests; fake download commands need no installer hook."""

import hashlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest


INSTALLER = Path(__file__).resolve().parents[1] / "install.sh"
RELEASES = "https://github.com/pzfreo/draftwright/releases"


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="draftwright-installer-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "commands"
        self.bin.mkdir()
        self.dest = self.root / "install with spaces"
        self.assets = self.root / "assets"
        self.assets.mkdir()
        self.log = self.root / "downloads"
        self.env = {
            **os.environ,
            "PATH": str(self.bin),
            "HOME": str(self.root),
            "DRAFTWRIGHT_INSTALL_DIR": str(self.dest),
            "TEST_ASSETS": str(self.assets),
            "TEST_DOWNLOAD_LOG": str(self.log),
            "TMPDIR": str(self.root),
        }
        self.env.pop("DRAFTWRIGHT_VERSION", None)
        # An isolated PATH lets tests exercise missing tools and wget-only installs.
        for name in ("sh", "cat", "mkdir", "mktemp", "rm", "awk", "tar", "gzip",
                     "chmod", "mv", "sha256sum", "shasum"):
            command = shutil.which(name)
            if command:
                (self.bin / name).symlink_to(command)
        self.command("uname", """
import os, sys
print(os.environ.get('TEST_OS', 'Linux') if sys.argv[1] == '-s'
      else os.environ.get('TEST_ARCH', 'x86_64'))
""")
        self.command("getconf", "import os; print(os.environ.get('TEST_GLIBC', 'glibc 2.35'))")
        self.command("ldd", "import os; print(os.environ.get('TEST_LDD', 'ldd (GNU libc) 2.36'))")
        self.command("sysctl", "import os; print(os.environ.get('TEST_APPLE_ARM64', '0'))")
        downloader = """
import os, pathlib, shutil, sys
url = sys.argv[-1]
with open(os.environ['TEST_DOWNLOAD_LOG'], 'a') as log:
    log.write(url + '\\n')
if url.endswith('/latest'):
    if '--server-response' in sys.argv:
        sys.stderr.write('  HTTP/1.1 302 Found\\n  Location: RELEASES/tag/v0.6.0\\r\\n  HTTP/1.1 200 OK\\n')
    else:
        print('RELEASES/tag/v0.6.0', end='')
else:
    flag = '-O' if '-O' in sys.argv else '-o'
    dest = sys.argv[sys.argv.index(flag) + 1]
    source = pathlib.Path(os.environ['TEST_ASSETS']) / url.rsplit('/', 1)[1]
    code = os.environ.get('TEST_MUSL_HTTP_ERROR', '') if '-linux-musl.' in url else ''
    if code or not source.exists():
        code = code or '404'
        if flag == '-O':
            sys.stderr.write('  HTTP/1.1 ' + code + ' Error\\n')
        else:
            print(code, end='')
        sys.exit(22 if flag == '-o' else 8)
    shutil.copyfile(source, dest)
    if flag == '-o':
        print('200', end='')
""".replace("RELEASES", RELEASES)
        self.command("curl", downloader)
        self.command("wget", downloader)
        for target in ("x86_64-unknown-linux-gnu", "aarch64-apple-darwin"):
            self.archive(target)

    def command(self, name, body):
        path = self.bin / name
        path.write_text(f"#!{sys.executable}\n{body}\n")
        path.chmod(0o755)

    def archive(self, target, missing_mcp=False):
        name = f"draftwright-v0.6.0-{target}"
        asset = self.assets / f"{name}.tar.gz"
        with tarfile.open(asset, "w:gz") as tar:
            for binary in ("draftwright", "draftwright-mcp", "README.md"):
                if missing_mcp and binary == "draftwright-mcp":
                    continue
                content = f"#!/bin/sh\nprintf '{binary} 0.6.0\\n'\n".encode()
                info = tarfile.TarInfo(f"{name}/{binary}")
                info.size = len(content)
                info.mode = 0o644  # Installer must make both binaries executable.
                tar.addfile(info, io.BytesIO(content))
        digest = hashlib.sha256(asset.read_bytes()).hexdigest()
        asset.with_suffix(".gz.sha256").write_text(f"{digest}  {asset.name}\n")
        return asset

    def run_installer(self, *args, **env):
        return subprocess.run(
            [str(self.bin / "sh"), str(INSTALLER), *args],
            env={**self.env, **env}, text=True, capture_output=True, timeout=20,
        )

    def assert_installed(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("draftwright 0.6.0", result.stdout)
        self.assertIn("Add ", result.stdout)
        self.assertIn("draftwright completions zsh  > ~/.zfunc/_draftwright", result.stdout)
        self.assertEqual(sorted(p.name for p in self.dest.iterdir()), ["draftwright", "draftwright-mcp"])
        for binary in ("draftwright", "draftwright-mcp"):
            self.assertTrue(os.access(self.dest / binary, os.X_OK))

    def seed_install(self):
        self.dest.mkdir()
        for binary in ("draftwright", "draftwright-mcp"):
            (self.dest / binary).write_text("previous installation")

    def assert_preserved(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(sorted(p.name for p in self.dest.iterdir()), ["draftwright", "draftwright-mcp"])
        for path in self.dest.iterdir():
            self.assertEqual(path.read_text(), "previous installation")

    def test_latest(self):
        self.assert_installed(self.run_installer())
        urls = self.log.read_text().splitlines()
        self.assertEqual(urls[0], f"{RELEASES}/latest")
        self.assertEqual(len(urls), 4)
        self.assertTrue(urls[1].endswith('-x86_64-unknown-linux-musl.tar.gz'))
        self.assertTrue(urls[2].endswith('-x86_64-unknown-linux-gnu.tar.gz'))
        self.assertEqual(urls[3], urls[2] + ".sha256")
        self.assertIn('falling back', self.run_installer().stdout)

    def test_pinned_versions_replace_existing_binaries(self):
        self.seed_install()
        for version in ("v0.6.0", "0.6.0"):
            with self.subTest(version=version):
                self.assert_installed(self.run_installer(DRAFTWRIGHT_VERSION=version))
        self.assertNotIn("/latest", self.log.read_text())

    def test_wget_and_shasum_fallbacks_on_mac(self):
        (self.bin / "curl").unlink()
        (self.bin / "sha256sum").unlink(missing_ok=True)
        self.assertTrue((self.bin / "shasum").exists(), "shasum is required for this test")
        self.assert_installed(self.run_installer(TEST_OS="Darwin", TEST_ARCH="arm64"))

    def test_checksum_mismatch_preserves_existing_binaries(self):
        self.seed_install()
        checksum = self.assets / "draftwright-v0.6.0-x86_64-unknown-linux-gnu.tar.gz.sha256"
        checksum.write_text("0" * 64 + "  " + checksum.name.removesuffix(".sha256") + "\n")
        result = self.run_installer()
        self.assert_preserved(result)
        self.assertIn("Checksum mismatch", result.stderr)

    def test_wrong_manifest_filename_is_rejected(self):
        self.seed_install()
        checksum = self.assets / "draftwright-v0.6.0-x86_64-unknown-linux-gnu.tar.gz.sha256"
        checksum.write_text(hashlib.sha256(b"").hexdigest() + "  /dev/null\n")
        result = self.run_installer()
        self.assert_preserved(result)
        self.assertIn("Invalid checksum file", result.stderr)

    def test_missing_binary_preserves_existing_binaries(self):
        self.seed_install()
        self.archive("x86_64-unknown-linux-gnu", missing_mcp=True)
        self.assert_preserved(self.run_installer())

    def test_unsupported_platforms(self):
        for os_name, arch in (("Linux", "armv7l"), ("MINGW64_NT", "x86_64"),
                              ("FreeBSD", "amd64")):
            with self.subTest(platform=f"{os_name}/{arch}"):
                result = self.run_installer(TEST_OS=os_name, TEST_ARCH=arch)
                self.assertNotEqual(result.returncode, 0)
                for text in ("Unsupported platform", RELEASES, "uv tool install draftwright", "cargo install draftwright"):
                    self.assertIn(text, result.stderr)
                self.assertFalse(self.dest.exists())

    def test_old_and_unknown_libc(self):
        for libc, ldd in (("glibc 2.34", ""), ("glibc 2.9", ""),
                          ("", "musl libc (x86_64) 1.2.5")):
            with self.subTest(libc=libc, ldd=ldd):
                result = self.run_installer(TEST_GLIBC=libc, TEST_LDD=ldd)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("glibc >= 2.35", result.stderr)
                self.assertIn("uv tool install draftwright", result.stderr)
                self.assertFalse(self.dest.exists())

    def test_ldd_fallback(self):
        self.assert_installed(self.run_installer(TEST_GLIBC="", TEST_LDD="ldd (Debian GLIBC 2.36-9) 2.36"))

    def test_static_linux_without_glibc(self):
        for arch, target in (("x86_64", "x86_64-unknown-linux-musl"),
                             ("aarch64", "aarch64-unknown-linux-musl")):
            with self.subTest(arch=arch):
                self.archive(target)
                self.assert_installed(self.run_installer(TEST_ARCH=arch, TEST_GLIBC="", TEST_LDD="musl libc 1.2.5"))

    def test_intel_mac_and_rosetta(self):
        self.archive("x86_64-apple-darwin")
        for arm64, target in (("0", "x86_64-apple-darwin"), ("1", "aarch64-apple-darwin")):
            with self.subTest(arm64=arm64):
                self.log.unlink(missing_ok=True)
                self.assert_installed(self.run_installer(TEST_OS="Darwin", TEST_ARCH="x86_64", TEST_APPLE_ARM64=arm64))
                self.assertIn(f'-{target}.tar.gz', self.log.read_text())

    def test_old_release_missing_targets(self):
        for os_name, arch, target in (("Darwin", "x86_64", "x86_64-apple-darwin"),
                                      ("Linux", "aarch64", "aarch64-unknown-linux-musl")):
            with self.subTest(target=target):
                result = self.run_installer(TEST_OS=os_name, TEST_ARCH=arch)
                self.assertNotEqual(result.returncode, 0)
                for text in (f'No archive for {target}', 'v0.6.0', RELEASES, 'uv tool install draftwright'):
                    self.assertIn(text, result.stderr)
                self.assertFalse(self.dest.exists())

    def test_network_errors_never_fall_back(self):
        for code in ('403', '500', '000'):
            with self.subTest(code=code):
                self.log.unlink(missing_ok=True)
                result = self.run_installer(TEST_MUSL_HTTP_ERROR=code)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('-linux-gnu', self.log.read_text())
                self.assertFalse(self.dest.exists())

    def test_static_checksum_failure_never_falls_back(self):
        self.seed_install()
        asset = self.archive('x86_64-unknown-linux-musl')
        asset.write_bytes(asset.read_bytes() + b'tampered')
        result = self.run_installer()
        self.assert_preserved(result)
        self.assertIn('Checksum mismatch', result.stderr)
        self.assertNotIn('-linux-gnu', self.log.read_text())

    def test_help_and_unknown_flag(self):
        self.assertEqual(self.run_installer("--help").returncode, 0)
        self.assertNotEqual(self.run_installer("--unknown").returncode, 0)
        self.assertFalse(self.dest.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
