"""Offline installer tests; TEST_INSTALL_SHELL selects sh/dash without installer hooks."""

import errno
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
        for name in ("sh", "cat", "mkdir", "mktemp", "rm", "rmdir", "awk", "tar", "gzip",
                     "chmod", "cp", "mv", "sha256sum", "shasum"):
            command = shutil.which(os.environ.get('TEST_INSTALL_SHELL', 'sh') if name == 'sh' else name)
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
if '--help' in sys.argv:
    print('--https-only' if os.environ.get('TEST_WGET_HTTPS_SUPPORT', '1') == '1' else '-S -O')
    sys.exit(0)
url = sys.argv[-1]
latest = os.environ.get('TEST_LATEST_VERSION', 'v0.6.0')
if '-o' in sys.argv:
    assert '--tlsv1.2' in sys.argv and sys.argv[sys.argv.index('--proto') + 1] == '=https'
else:
    assert ('--https-only' in sys.argv) == (os.environ.get('TEST_WGET_HTTPS_SUPPORT', '1') == '1')
with open(os.environ['TEST_DOWNLOAD_LOG'], 'a') as log:
    log.write(url + '\\n')
if os.environ.get('TEST_WGET_DNS_FAILURE'):
    sys.stderr.write('wget: unable to resolve host address github.com\\n')
    sys.exit(4)
if url.endswith('/latest'):
    if '-S' in sys.argv:
        sys.stderr.write('  HTTP/1.1 302 Found\\n  Location: RELEASES/tag/' + latest + '\\r\\n  HTTP/1.1 200 OK\\n')
    else:
        print('RELEASES/tag/' + latest, end='')
else:
    flag = '-O' if '-O' in sys.argv else '-o'
    dest = sys.argv[sys.argv.index(flag) + 1]
    source = pathlib.Path(os.environ['TEST_ASSETS']) / url.rsplit('/', 1)[1]
    code = os.environ.get('TEST_HTTP_ERROR', '') if '-linux-gnu.' in url else ''
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
        for target in ("x86_64-unknown-linux-gnu", "aarch64-unknown-linux-gnu"):
            self.archive(target, version="v0.6.1")

    def command(self, name, body):
        path = self.bin / name
        path.write_text(f"#!{sys.executable}\n{body}\n")
        path.chmod(0o755)

    def archive(self, target, missing_mcp=False, version="v0.6.0"):
        name = f"draftwright-{version}-{target}"
        asset = self.assets / f"{name}.tar.gz"
        with tarfile.open(asset, "w:gz") as tar:
            for binary in ("draftwright", "draftwright-mcp", "README.md"):
                if missing_mcp and binary == "draftwright-mcp":
                    continue
                content = f"#!/bin/sh\nprintf '{binary} {version[1:]}\\n'\n".encode()
                info = tarfile.TarInfo(f"{name}/{binary}")
                info.size = len(content)
                info.mode = 0o644  # Installer must make both binaries executable.
                info.uid = 1001 if os.geteuid() != 1001 else 2002
                info.gid = info.uid
                info.uname = info.gname = 'release-runner'
                tar.addfile(info, io.BytesIO(content))
        digest = hashlib.sha256(asset.read_bytes()).hexdigest()
        asset.with_suffix(".gz.sha256").write_text(f"{digest}  {asset.name}\n")
        return asset

    def run_installer(self, *args, **env):
        result = subprocess.run(
            [str(self.bin / "sh"), str(INSTALLER), *args],
            env={**self.env, **env}, text=True, capture_output=True, timeout=20,
        )
        self.assertEqual(list(self.root.glob('draftwright-install.*')), [], 'Download staging was not cleaned')
        return result

    def assert_installed(self, result, version="0.6.0"):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("draftwright " + version, result.stdout)
        self.assertIn("Add ", result.stdout)
        self.assertIn("draftwright completions zsh  > ~/.zfunc/_draftwright", result.stdout)
        self.assertEqual(sorted(p.name for p in self.dest.iterdir()), ["draftwright", "draftwright-mcp"])
        for binary in ("draftwright", "draftwright-mcp"):
            self.assertTrue(os.access(self.dest / binary, os.X_OK))
            self.assertEqual((self.dest / binary).stat().st_uid, os.geteuid())

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
        self.assertEqual(len(urls), 3)
        self.assertTrue(urls[1].endswith('-x86_64-unknown-linux-gnu.tar.gz'))
        self.assertEqual(urls[2], urls[1] + ".sha256")
        self.assertNotIn('musl', self.log.read_text())

    def test_pinned_versions_replace_existing_binaries(self):
        self.seed_install()
        for version in ("v0.6.0", "0.6.0"):
            with self.subTest(version=version):
                self.assert_installed(self.run_installer(DRAFTWRIGHT_VERSION=version))
        self.assertNotIn("/latest", self.log.read_text())

    def test_latest_new_release_uses_new_floor(self):
        for downloader in ('curl', 'wget'):
            with self.subTest(downloader=downloader):
                if downloader == 'wget':
                    (self.bin / 'curl').unlink()
                self.log.unlink(missing_ok=True)
                self.assert_installed(self.run_installer(TEST_LATEST_VERSION='v0.6.1',
                                      TEST_GLIBC='glibc 2.17'), version='0.6.1')
                urls = self.log.read_text().splitlines()
                self.assertEqual(len(urls), 3)
                self.assertEqual(urls[0], RELEASES + '/latest')
                self.assertIn('/download/v0.6.1/', urls[1])

    def test_wget_and_shasum_fallbacks_on_mac(self):
        (self.bin / "curl").unlink()
        (self.bin / "sha256sum").unlink(missing_ok=True)
        self.assertTrue((self.bin / "shasum").exists(), "shasum is required for this test")
        self.assert_installed(self.run_installer(TEST_OS="Darwin", TEST_ARCH="arm64"))

    def test_wget_old_release_gnu(self):
        (self.bin / "curl").unlink()
        result = self.run_installer()
        self.assert_installed(result)
        self.assertNotIn('falling back', result.stdout)

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

    def test_refusal_removes_only_new_empty_install_directory(self):
        self.archive('x86_64-unknown-linux-gnu', missing_mcp=True)
        for suffix in ('', '///'):
            with self.subTest(suffix=suffix):
                result = self.run_installer(DRAFTWRIGHT_INSTALL_DIR=str(self.dest) + suffix)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.dest.exists(), 'New empty install directory was left behind')
        self.dest.mkdir()
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.dest.is_dir(), 'Pre-existing empty directory was removed')
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_refusal_keeps_new_directory_if_another_file_was_added(self):
        (self.bin / 'tar').unlink()
        self.command('tar', """
import os, pathlib, sys
(pathlib.Path(os.environ['DRAFTWRIGHT_INSTALL_DIR']) / 'keep').write_text('another file')
sys.exit(1)
""")
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual([p.name for p in self.dest.iterdir()], ['keep'])
        self.assertEqual((self.dest / 'keep').read_text(), 'another file')

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
        for libc, ldd in (("glibc 2.34", ""), ("glibc 2.9", ""), ("", "")):
            with self.subTest(libc=libc, ldd=ldd):
                result = self.run_installer(TEST_GLIBC=libc, TEST_LDD=ldd)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("glibc >= 2.35" if libc else "No glibc detected", result.stderr)
                self.assertIn("uv tool install draftwright", result.stderr)
                self.assertFalse(self.dest.exists())

    def test_ldd_fallback(self):
        self.assert_installed(self.run_installer(TEST_GLIBC="", TEST_LDD="ldd (Debian GLIBC 2.36-9) 2.36"))

    def test_linux_without_glibc_refuses(self):
        for arch in ("x86_64", "aarch64"):
            with self.subTest(arch=arch):
                result = self.run_installer(TEST_ARCH=arch, TEST_GLIBC="", TEST_LDD="musl libc 1.2.5")
                self.assertNotEqual(result.returncode, 0)
                for text in ('No glibc detected', 'Alpine/musl', 'uv tool install draftwright', 'cargo install draftwright'):
                    self.assertIn(text, result.stderr)
                self.assertFalse(self.dest.exists())
                self.assertFalse(self.log.exists(), 'No-glibc refusal should precede network requests')

    def test_new_linux_release_floor_on_both_architectures(self):
        for arch, target in (("x86_64", "x86_64-unknown-linux-gnu"),
                             ("aarch64", "aarch64-unknown-linux-gnu")):
            for libc in ('glibc 2.17', 'glibc 2.28', 'glibc 2.31'):
                with self.subTest(arch=arch, libc=libc):
                    self.log.unlink(missing_ok=True)
                    self.assert_installed(self.run_installer(TEST_ARCH=arch, TEST_GLIBC=libc,
                                          DRAFTWRIGHT_VERSION='v0.6.1'), version='0.6.1')
                    urls = self.log.read_text().splitlines()
                    self.assertEqual(len(urls), 2)
                    self.assertIn(f'-{target}.tar.gz', urls[0])
                    self.assertEqual(urls[1], urls[0] + '.sha256')
        result = self.run_installer(TEST_GLIBC='glibc 2.16', DRAFTWRIGHT_VERSION='v0.6.1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('glibc >= 2.17', result.stderr)

    def test_release_floor_version_boundaries(self):
        for version in ('v0.5.9', 'v0.6.0-alpha.1', 'v0.6.0', 'v0.6.0+build.1'):
            with self.subTest(version=version):
                result = self.run_installer(DRAFTWRIGHT_VERSION=version, TEST_GLIBC='glibc 2.17')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('glibc >= 2.35', result.stderr)
                self.assertFalse(self.dest.exists())
        for version in ('0.6.1', 'v0.6.1-alpha.1', 'v0.7.0', 'v0.10.0', 'v1.0.0'):
            with self.subTest(version=version):
                tag = version if version.startswith('v') else 'v' + version
                self.archive('x86_64-unknown-linux-gnu', version=tag)
                self.assert_installed(self.run_installer(DRAFTWRIGHT_VERSION=version,
                                      TEST_GLIBC='glibc 2.17'), version=tag[1:])

    def test_intel_mac_and_rosetta(self):
        self.archive("x86_64-apple-darwin")
        for arm64, target in (("0", "x86_64-apple-darwin"), ("1", "aarch64-apple-darwin")):
            with self.subTest(arm64=arm64):
                self.log.unlink(missing_ok=True)
                self.assert_installed(self.run_installer(TEST_OS="Darwin", TEST_ARCH="x86_64", TEST_APPLE_ARM64=arm64))
                self.assertIn(f'-{target}.tar.gz', self.log.read_text())

    def test_old_release_missing_targets(self):
        for os_name, arch, target in (("Darwin", "x86_64", "x86_64-apple-darwin"),
                                      ("Linux", "aarch64", "aarch64-unknown-linux-gnu")):
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
                result = self.run_installer(TEST_HTTP_ERROR=code)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(len(self.log.read_text().splitlines()), 2)
                self.assertIn('Could not download', result.stderr)
                self.assertFalse(self.dest.exists())

    def test_new_linux_checksum_failure_never_falls_back(self):
        self.seed_install()
        asset = self.archive('aarch64-unknown-linux-gnu', version='v0.6.1')
        asset.write_bytes(asset.read_bytes() + b'tampered')
        result = self.run_installer(TEST_ARCH='aarch64', DRAFTWRIGHT_VERSION='v0.6.1', TEST_GLIBC='glibc 2.17')
        self.assert_preserved(result)
        self.assertIn('Checksum mismatch', result.stderr)
        self.assertEqual(len(self.log.read_text().splitlines()), 2)
        self.assertNotIn('x86_64', self.log.read_text())

    def test_missing_gnu_checksum_never_falls_back(self):
        self.seed_install()
        asset = self.archive('aarch64-unknown-linux-gnu', version='v0.6.1')
        asset.with_suffix('.gz.sha256').unlink()
        result = self.run_installer(TEST_ARCH='aarch64', DRAFTWRIGHT_VERSION='v0.6.1', TEST_GLIBC='glibc 2.17')
        self.assert_preserved(result)
        self.assertIn('Could not download the checksum', result.stderr)
        self.assertEqual(len(self.log.read_text().splitlines()), 2)
        self.assertNotIn('x86_64', self.log.read_text())

    def test_help_and_unknown_flag(self):
        self.assertEqual(self.run_installer("--help").returncode, 0)
        self.assertNotEqual(self.run_installer("--unknown").returncode, 0)
        self.assertFalse(self.dest.exists())

    def test_archive_owner_is_not_restored(self):
        self.assert_installed(self.run_installer())

    def test_truncated_pipe_performs_no_work(self):
        source = INSTALLER.read_text()
        self.assertEqual(source.splitlines()[-1], 'main "$@"')
        body_end = source.rfind('\nmain "$@"')
        for end in (source.index('os=$(uname'), source.index('download()'), source.index('tar -xzof'), body_end):
            with self.subTest(end=end):
                subprocess.run([str(self.bin / 'sh')], input=source[:end], env=self.env,
                               text=True, capture_output=True, timeout=20)
                self.assertFalse(self.log.exists())
                self.assertFalse(self.dest.exists())
                self.assertEqual(list(self.root.glob('draftwright-install.*')), [])

    def test_second_destination_directory_preserves_first(self):
        self.dest.mkdir()
        (self.dest / 'draftwright').write_text('previous installation')
        (self.dest / 'draftwright-mcp').mkdir()
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Destination is a directory', result.stderr)
        self.assertEqual((self.dest / 'draftwright').read_text(), 'previous installation')

    def test_second_rename_failure_rolls_back_first(self):
        self.seed_install()
        (self.bin / 'mv').unlink()
        real_mv = shutil.which('mv')
        self.command('mv', f"""
import subprocess, sys
if sys.argv[-1].endswith('/draftwright-mcp'):
    sys.stderr.write('mv: simulated rename denial\\n')
    sys.exit(1)
sys.exit(subprocess.call([{real_mv!r}, *sys.argv[1:]]))
""")
        result = self.run_installer()
        self.assert_preserved(result)
        self.assertIn('close any MCP client using draftwright-mcp', result.stderr)

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux executable write-lock semantics')
    def test_running_mcp_refused_before_replacing_cli(self):
        self.seed_install()
        mcp = self.dest / 'draftwright-mcp'
        shutil.copyfile(shutil.which('sleep'), mcp)
        mcp.chmod(0o755)
        previous = mcp.read_bytes()
        # Ubuntu's uutils sleep dispatches by executable name. Run a sleep-named
        # hard link so it stays running while locking the destination's inode.
        sleeper = self.root / 'sleep'
        os.link(mcp, sleeper)
        process = subprocess.Popen([str(sleeper), '60'])
        try:
            self.assertIsNone(process.poll(), 'The busy-executable fixture exited')
            with self.assertRaises(OSError) as busy:
                with mcp.open('ab'):
                    pass
            self.assertEqual(busy.exception.errno, errno.ETXTBSY)
            result = self.run_installer()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('close any MCP client using draftwright-mcp', result.stderr)
            self.assertEqual((self.dest / 'draftwright').read_text(), 'previous installation')
            self.assertEqual(mcp.read_bytes(), previous)
            self.assertIsNone(process.poll(), 'The MCP must stay busy throughout installation')
        finally:
            process.terminate()
            process.wait(timeout=10)

    def test_wget_failure_reason_and_optional_https_flag(self):
        (self.bin / 'curl').unlink()
        for support in ('0', '1'):
            with self.subTest(https_only=support):
                self.assert_installed(self.run_installer(TEST_WGET_HTTPS_SUPPORT=support))
        for version in ('', 'v0.6.0'):
            with self.subTest(version=version):
                result = self.run_installer(DRAFTWRIGHT_VERSION=version, TEST_WGET_DNS_FAILURE='1')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('unable to resolve host address', result.stderr)
        result = self.run_installer(DRAFTWRIGHT_VERSION='v0.6.0', TEST_HTTP_ERROR='500')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('HTTP/1.1 500 Error', result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
