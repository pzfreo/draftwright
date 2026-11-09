"""Refuse publication unless the release tag, Cargo packages and every wheel agree."""
import argparse
from email.parser import BytesParser
from pathlib import Path
import re
import sys
import tomllib
import zipfile


MANIFESTS = ("Cargo.toml", "crates/draftwright-wasm/Cargo.toml")
STABLE_TAG = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")


def check_versions(tag: str, root: Path, dist: Path) -> str:
    match = STABLE_TAG.fullmatch(tag)
    if not match:
        raise ValueError(f"tag {tag!r}: expected vX.Y.Z")
    if tuple(map(int, match.groups())) < (0, 6, 0):
        raise ValueError(f"tag {tag}: PyPI publishing begins with 0.6.0")
    version = tag[1:]
    for manifest in MANIFESTS:
        with (root / manifest).open("rb") as source:
            actual = tomllib.load(source)["package"]["version"]
        if actual != version:
            raise ValueError(f"tag {tag}, {manifest} {actual}: they must agree")

    wheels = sorted(dist.glob("*.whl"))
    if not wheels:
        raise ValueError(f"{dist}: no wheels to publish")
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as archive:
            metadata = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA")]
            if len(metadata) != 1:
                raise ValueError(f"{wheel.name}: expected exactly one wheel METADATA")
            package = BytesParser().parsebytes(archive.read(metadata[0]))
        if package.get_all("Name") != ["draftwright"]:
            raise ValueError(f"{wheel.name}: expected package draftwright")
        if package.get_all("Version") != [version]:
            raise ValueError(
                f"tag {tag}, wheel {wheel.name} {package.get_all('Version')}: they must agree"
            )
    return f"tag {tag}, Cargo {version}, {len(wheels)} wheel(s) {version}: they agree"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    args = parser.parse_args()
    try:
        print(check_versions(args.tag, args.root, args.dist))
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
