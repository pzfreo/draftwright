"""Refuse Linux binaries whose ELF symbol versions exceed the release's glibc floor."""

import argparse
import re
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-version", required=True)
    parser.add_argument("binaries", nargs="+")
    args = parser.parse_args()
    floor = tuple(int(part) for part in args.max_version.split("."))
    for binary in args.binaries:
        versions = subprocess.check_output(["readelf", "--version-info", binary], text=True)
        symbols = subprocess.check_output(["objdump", "-T", binary], text=True)
        required = set(re.findall(r"\bGLIBC_[A-Za-z0-9_.]+", versions + symbols))
        if not required:
            raise SystemExit("{}: no GLIBC symbol versions found".format(binary))
        excessive = []
        for version in sorted(required):
            match = re.fullmatch(r"GLIBC_(\d+(?:\.\d+)+)", version)
            if not match or tuple(int(part) for part in match[1].split(".")) > floor:
                excessive.append(version)
        if excessive:
            print(symbols, end="")
            raise SystemExit("{}: exceeds glibc {}: {}".format(binary, args.max_version, ", ".join(excessive)))
        highest = max(required, key=lambda version: tuple(int(part) for part in version[6:].split(".")))
        print("{}: highest required version {} (ceiling {})".format(binary, highest, args.max_version))


if __name__ == "__main__":
    main()
