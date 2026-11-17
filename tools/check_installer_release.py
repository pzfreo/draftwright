"""Run the Unix installer and assert success/refusal from the published asset list.

Old glibc and missing targets must refuse v0.6.0, then succeed when the newer
GNU / Intel assets are published. Alpine must refuse. A refusal is never a skip.
"""

import argparse
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import tempfile
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version")
    args = parser.parse_args()
    version = args.version
    release = "tags/" + version if version else "latest"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "draftwright-installer-test"}
    if os.environ.get("GH_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GH_TOKEN"]
    request = urllib.request.Request(
        "https://api.github.com/repos/pzfreo/draftwright/releases/" + release, headers=headers,
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        metadata = json.load(response)
    tag = metadata["tag_name"]
    assets = {asset["name"] for asset in metadata["assets"]}

    system, arch = platform.system(), platform.machine()
    if system == "Darwin":
        arm64 = arch in ("arm64", "aarch64") or subprocess.run(
            ["sysctl", "-n", "hw.optional.arm64"], capture_output=True, text=True,
        ).stdout.strip() == "1"
        target = "aarch64-apple-darwin" if arm64 else "x86_64-apple-darwin"
    elif system == "Linux":
        target = {"x86_64": "x86_64", "aarch64": "aarch64"}[arch] + "-unknown-linux-gnu"
    else:
        raise AssertionError("This smoke test is for Unix")

    def has_asset(triple):
        return "draftwright-{}-{}.tar.gz".format(tag, triple) in assets

    if system == "Linux" and arch == "x86_64":
        assert has_asset(target), "Release has no x86_64 GNU Linux archive"
    expected_error = None
    if system == "Linux":
        try:
            libc = os.confstr("CS_GNU_LIBC_VERSION")
        except (ValueError, OSError):
            libc = None
        if not libc or not libc.startswith("glibc "):
            expected_error = "No glibc detected"
        else:
            glibc = tuple(int(part) for part in libc.split()[1].split(".")[:2])
            match = re.match(r"^v(\d+)\.(\d+)\.(\d+)", tag)
            assert match, "Unrecognized release version: " + tag
            floor = (2, 17) if tuple(int(part) for part in match.groups()) > (0, 6, 0) else (2, 35)
            if glibc < floor:
                expected_error = "glibc >= {}.{}".format(*floor)
    if not expected_error and not has_asset(target):
        expected_error = "No archive for {} in {}".format(target, tag)
    print("Published {} assets: {}".format(tag, ", ".join(sorted(assets))), flush=True)
    print("Expected: " + ("refusal: " + expected_error if expected_error else "successful install"), flush=True)

    installer = Path(__file__).resolve().parents[1] / "install.sh"
    with tempfile.TemporaryDirectory(prefix="draftwright-smoke-") as temp:
        destination = Path(temp) / "bin"
        env = {**os.environ, "DRAFTWRIGHT_INSTALL_DIR": str(destination), "TMPDIR": temp}
        env.pop("DRAFTWRIGHT_VERSION", None)
        if version:
            env["DRAFTWRIGHT_VERSION"] = version
        result = subprocess.run(["sh", str(installer)], env=env, text=True, capture_output=True, timeout=180)
        print(result.stdout, end="", flush=True)
        print(result.stderr, end="", flush=True)
        if expected_error:
            assert result.returncode != 0, "Installer unexpectedly succeeded"
            assert expected_error in result.stderr, "Wrong refusal: " + result.stderr
            assert not destination.exists(), "Refusal left an installation directory"
        else:
            assert result.returncode == 0, "Installer failed"
            assert sorted(path.name for path in destination.iterdir()) == ["draftwright", "draftwright-mcp"]
            for binary in ("draftwright", "draftwright-mcp"):
                output = subprocess.check_output([str(destination / binary), "--version"], text=True)
                print(output, end="", flush=True)
                assert re.match(r"^{} {}(?:\s|$)".format(binary, re.escape(tag[1:])), output), output
        assert not list(Path(temp).glob("draftwright-install.*")), "Download staging was not cleaned"
    print("PASS: published asset list and installer behavior agree", flush=True)


if __name__ == "__main__":
    main()
