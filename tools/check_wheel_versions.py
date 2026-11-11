"""Refuse publication unless the release tag, Cargo packages and every wheel agree."""
import argparse
from email.parser import BytesParser
from pathlib import Path
import re
import subprocess
import sys
import tomllib
import zipfile


MANIFESTS = ("Cargo.toml", "crates/draftwright-wasm/Cargo.toml", "crates/draftwright-py/Cargo.toml")
# A release tag: vX.Y.Z, or a pre-release vX.Y.Z-alpha.N, -beta.N or -rc.N (N from 1).
TAG = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-(alpha|beta|rc)\.([1-9][0-9]*))?")
# Cargo's semver pre-release, as PEP 440 and maturin spell it in the wheel.
PEP440 = {"alpha": "a", "beta": "b", "rc": "rc"}


def git_commit(root: Path, reference: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--verify", reference], cwd=root,
            stderr=subprocess.PIPE, text=True,
        ).strip()
    except subprocess.CalledProcessError as error:
        raise ValueError(f"git reference {reference!r} does not exist or is not a commit") from error


def check_versions(tag: str, root: Path, dist: Path, provenance: Path) -> str:
    match = TAG.fullmatch(tag)
    if not match:
        raise ValueError(f"tag {tag!r}: expected vX.Y.Z or vX.Y.Z-alpha.N, -beta.N, -rc.N")
    major, minor, patch, stage, number = match.groups()
    if (int(major), int(minor), int(patch)) < (0, 6, 0):
        raise ValueError(f"tag {tag}: PyPI publishing begins with 0.6.0")
    version = tag[1:]
    wheel_version = f"{major}.{minor}.{patch}" + (f"{PEP440[stage]}{number}" if stage else "")
    tag_commit = git_commit(root, f"refs/tags/{tag}^{{commit}}")
    head = git_commit(root, "HEAD^{commit}")
    if head != tag_commit:
        raise ValueError(f"checked-out commit {head} does not match tag {tag} ({tag_commit})")
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
        if package.get_all("Version") != [wheel_version]:
            raise ValueError(
                f"tag {tag}, wheel {wheel.name} {package.get_all('Version')}: they must agree"
            )
        recorded = provenance / (wheel.name + ".commit")
        if not recorded.is_file():
            raise ValueError(f"{wheel.name}: missing build provenance {recorded}")
        built_commit = recorded.read_text().strip()
        if built_commit != tag_commit:
            raise ValueError(
                f"wheel {wheel.name} built commit {built_commit!r} does not match tag {tag} ({tag_commit})"
            )
    return f"tag {tag} ({tag_commit}), Cargo {version}, {len(wheels)} wheel(s) {wheel_version}: they agree"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--provenance", type=Path)
    args = parser.parse_args()
    try:
        print(check_versions(args.tag, args.root, args.dist,
                             args.provenance or args.dist.parent / "provenance"))
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
