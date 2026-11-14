# Contributing to draftwright

Contributions are welcome: bug reports, fixes, features and documentation.

## Contributor License Agreement

draftwright is released under the AGPL-3.0 and is also offered under separate
commercial terms by the copyright owner. So that contributions can be included
in both, every contributor agrees to the
[Contributor License Agreement](CLA.md). You keep the copyright to your work;
the CLA grants a licence, not an assignment.

On your first pull request a bot asks you to sign by replying:

> I have read the CLA Document and I hereby sign the CLA

Pull requests cannot merge until the CLA is signed.

## Test files and data

Add only files you created yourself or whose licence allows redistribution, and record each
file's source and licence beside it (a fixture README or NOTICE entry). Never add customer,
confidential or NDA-covered files, or geometry extracted from them, to the repository, to
issues or to pull requests. For a bug seen on such a part, share a reduced synthetic part that
reproduces it instead.

## Releasing

The repository is `pzfreo/draftwright`; `pzfreo/draftwright-python` is the earlier
Python implementation, being retired.

The current release is 0.6.0-alpha.2; 0.6.0 is in alpha (pre-release). The release commit
updates the native, WASM and Python-extension Cargo versions together, `Cargo.lock`,
README status and CHANGELOG. Wheel packaging lives in the root
`pyproject.toml`, using maturin's `bindings = "pyo3"`, the unpublished
`crates/draftwright-py` manifest and a dynamic Cargo-derived version. The mixed
package lives in `python/draftwright`; the extension is `draftwright._native`
(abi3, Python 3.9+). PyO3 must remain outside the published native crate and WASM
dependency graphs.
The Cargo versions remain at the current release until that coordinated release commit.
There is no separate wheel version to bump: release tag `vX.Y.Z`, Cargo version `X.Y.Z`
and every wheel's METADATA version `X.Y.Z` must agree. PyPI publishing uses 0.6.0 and its pre-releases.

Run formatting, strict release workspace/all-target Clippy, library tests and the full
required-corpus workspace nextest suite before releasing. Build the local wheel with
`RUSTC_WRAPPER= CARGO_BUILD_JOBS=6 uv tool run maturin build --release --locked`, install it
in a clean venv, and run `python -m unittest discover -s tests/python -v`.
Also run those tests with build123d installed to cover shape export and `source=`. No build outputs are committed.

Publishing a GitHub release drives the existing CLI archive, browser WASM and crates.io
workflows. `.github/workflows/publish.yml` additionally builds and smoke-tests mixed Python/Rust wheels
for macOS Intel/Apple silicon, manylinux 2_28 x86_64/aarch64 and Windows x86_64. We omit an
sdist: these installs should not unexpectedly require Rust on unsupported platforms.
The wheel workflow also runs for relevant main pushes and PRs; a manual run on main can
request publishing with `publish: true` and an explicit `tag`, such as `v0.6.0`.
Dispatch and release builds check out that tag, including in the publish job. Pre-release
tags must agree with the GitHub release's pre-release flag; pre-release wheels are published
too. Each wheel artifact includes its build commit in a separate
provenance directory; this directory is checked but never uploaded to PyPI.
`tools/check_wheel_versions.py` checks that the tag exists, HEAD and every wheel's build
commit match the tag's commit, and all three Cargo manifests and all downloaded wheels agree
before the trusted-publishing action runs. A missing/invalid tag, absent wheels or provenance,
or any commit/version mismatch fails the job. Versions before 0.6.0 are ineligible for PyPI publication.
`python3 tools/test_wheel_versions.py -v` exercises the guard's
success and refusal paths, including annotated tags. Publishing runs for the same tag are
serialized; existing PyPI files are skipped on reruns. No version mapping is used.

Trusted publishing uses owner `pzfreo`, repository `draftwright`, workflow `publish.yml`,
environment `pypi`. The existing trusted publisher should match after the rename — check it
on pypi.org. The publish action obtains an OIDC token (`id-token: write`); no stored token or
TestPyPI job is used. Build jobs have only read access. A local check must never publish.
