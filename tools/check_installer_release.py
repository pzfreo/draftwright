"""Run the Unix installer and assert success/refusal from the published asset list.

In particular, old glibc and Intel Mac must refuse v0.6.0, then succeed when
their static / Intel assets are published. An expected refusal is never a skip.
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
        target = {"x86_64": "x86_64", "aarch64": "aarch64"}[arch] + "-unknown-linux-musl"
    else:
        raise AssertionError("This smoke test is for Unix")

    def has_asset(triple):
        return "draftwright-{}-{}.tar.gz".format(tag, triple) in assets

    expected_error = None
    if not has_asset(target):
        if system == "Linux" and arch == "x86_64":
            assert has_asset("x86_64-unknown-linux-gnu"), "Release has neither x86_64 Linux asset"
            libc = os.confstr("CS_GNU_LIBC_VERSION")
            glibc = tuple(int(part) for part in libc.split()[1].split(".")[:2])
            if glibc < (2, 35):
                expected_error = "glibc >= 2.35"
        else:
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
