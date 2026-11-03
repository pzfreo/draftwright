#!/usr/bin/env python3
"""Warn when the sibling's latest published release uses a different kernel pin.

Includes prereleases: GitHub's /releases/latest endpoint excludes the alpha releases.
Network, manifest and release-edit errors are warnings too, never release failures.
"""
import argparse
import base64
import json
from functools import lru_cache
import os
import re
from pathlib import Path
import subprocess
import shutil
import tempfile
import tomllib
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
SIBLING = "pzfreo/specify-core-rust"


def warning(message):
    escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::warning::{escaped}")


def github_json(path):
    # gh can use an existing login; urllib also works on public repos without gh/auth.
    if shutil.which("gh"):
        env = os.environ.copy()
        if token := env.get("GITHUB_TOKEN"):
            env["GH_TOKEN"] = token
        try:
            return json.loads(subprocess.check_output(
                ["gh", "api", path], text=True, env=env, stderr=subprocess.PIPE, timeout=30,
            ))
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass  # An installed but unsigned-in gh must not prevent anonymous public reads.
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "draftwright-kernel-pin-check"}
    if token := os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(f"https://api.github.com/{path}", headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def latest_release(repo):
    releases = []
    page = 1
    while True:
        batch = github_json(f"repos/{repo}/releases?per_page=100&page={page}")
        releases.extend(r for r in batch if not r["draft"] and r.get("published_at"))
        if len(batch) < 100:
            break
        page += 1
    if not releases:
        raise ValueError(f"{repo} has no published releases")
    return max(releases, key=lambda r: r["published_at"])["tag_name"]


def kernel_pins(manifest):
    data = tomllib.loads(manifest)
    dependencies = data.get("dependencies", {})
    workspace = data.get("workspace", {}).get("dependencies", {})
    pins = {}
    for name in ("haecceity", "quiddity"):
        dep = dependencies.get(name, workspace.get(name))
        if isinstance(dep, dict) and dep.get("workspace"):
            dep = workspace.get(name)
        repo = dep.get("git", "").removesuffix(".git").rstrip("/") if isinstance(dep, dict) else ""
        # pzfreo/quiddity-rust was renamed pzfreo/quiddity (2026-11-03); GitHub redirects the old
        # name, and released sibling manifests may still use it.
        if repo not in ("https://github.com/pzfreo/quiddity", "https://github.com/pzfreo/quiddity-rust"):
            raise ValueError(f"{name} must use the pzfreo/quiddity git dependency")
        kinds = [kind for kind in ("tag", "rev") if dep.get(kind)]
        if len(kinds) != 1:
            raise ValueError(f"{name} must have exactly one tag or rev")
        kind = kinds[0]
        pins[name] = ("https://github.com/pzfreo/quiddity", kind, dep[kind])
    return pins


def describe(pins):
    return "; ".join(f"{name}: {kind} {value}" for name, (_, kind, value) in pins.items())


@lru_cache(maxsize=None)
def resolve_pin(repository, kind, value):
    if kind == "rev" and re.fullmatch(r"[0-9a-fA-F]{40}", value):
        return repository, value.lower()
    match = re.fullmatch(r"https://github.com/([^/]+/[^/]+)", repository)
    if not match:
        raise ValueError(f"cannot resolve kernel repository {repository}")
    commit = github_json(f"repos/{match[1]}/commits/{urllib.parse.quote(value, safe='')}")
    return repository, commit["sha"]


def append_release_note(repo, tag, note):
    env = os.environ.copy()
    if token := env.get("GITHUB_TOKEN"):
        env["GH_TOKEN"] = token
    body = subprocess.check_output(
        ["gh", "release", "view", tag, "--repo", repo, "--json", "body", "--jq", ".body"],
        text=True, env=env, timeout=30,
    ).rstrip("\n")
    # Replace our previous line on workflow retries; preserve the maintainer's other notes.
    lines = [line for line in body.splitlines() if not line.startswith("Kernel note:")]
    body = "\n".join(lines).rstrip() + "\n\n" + note + "\n"
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "release-notes.md"
        path.write_text(body)
        subprocess.run(["gh", "release", "edit", tag, "--repo", repo, "--notes-file", str(path)],
                       check=True, env=env, timeout=30)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / "Cargo.toml")
    parser.add_argument("--sibling", default=SIBLING)
    parser.add_argument("--release", help="on mismatch, append a Kernel note to this release tag")
    parser.add_argument("--repo", default="pzfreo/draftwright-rust", help="repository for --release")
    args = parser.parse_args()
    try:
        local = kernel_pins(args.manifest.read_text())
        tag = latest_release(args.sibling)
        ref = urllib.parse.quote(tag, safe="")
        content = github_json(f"repos/{args.sibling}/contents/Cargo.toml?ref={ref}")
        sibling = kernel_pins(base64.b64decode(content["content"]).decode())
        print(f"draftwright-rust: {describe(local)}")
        print(f"{args.sibling} latest release {tag}: {describe(sibling)}")
        local_commits = {name: resolve_pin(*pin) for name, pin in local.items()}
        sibling_commits = {name: resolve_pin(*pin) for name, pin in sibling.items()}
        if local_commits == sibling_commits and len(set(local_commits.values())) == 1:
            print("Quiddity pins match (haecceity and quiddity in both packages): "
                  + local_commits["quiddity"][1])
        else:
            note = (f"Kernel note: draftwright-rust uses {describe(local)}; "
                    f"{args.sibling} {tag} uses {describe(sibling)}. "
                    "Different quiddity pins may change STEP face numbering.")
            warning(note)
            if args.release:
                append_release_note(args.repo, args.release, note)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError,
            urllib.error.URLError) as error:
        warning(f"Could not complete quiddity pin check: {error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
