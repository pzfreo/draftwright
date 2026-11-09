# Contributing to draftwright-rust

Contributions are welcome: bug reports, fixes, features and documentation.

## Contributor License Agreement

draftwright-rust is released under the AGPL-3.0 and is also offered under separate
commercial terms by the copyright owner. So that contributions can be included
in both, every contributor agrees to the
[Contributor License Agreement](CLA.md). You keep the copyright to your work;
the CLA grants a licence, not an assignment.

On your first pull request a bot asks you to sign by replying:

> I have read the CLA Document and I hereby sign the CLA

Pull requests cannot merge until the CLA is signed.

## Releasing

The repository is `pzfreo/draftwright`; the archived Python implementation is
`pzfreo/draftwright-python`.

The next release is 0.6.0. The release commit updates the native and WASM Cargo versions
together, `Cargo.lock`, README status and CHANGELOG. Wheel packaging lives in the root
`pyproject.toml`, using maturin's `bindings = "bin"` and dynamic Cargo-derived version.
The Cargo versions remain at the current release until that coordinated release commit.
There is no separate wheel version to bump: release tag `vX.Y.Z`, Cargo version `X.Y.Z`
and every wheel's METADATA version `X.Y.Z` must agree. PyPI publishing begins with 0.6.0.

Run formatting, strict release workspace/all-target Clippy, library tests and the full
required-corpus workspace nextest suite before releasing. Build the local wheel with
`RUSTC_WRAPPER= CARGO_BUILD_JOBS=6 uv tool run maturin build --release --locked`, install it
in a clean venv, and check both commands and a fixture render. No build outputs are committed.

Publishing a GitHub release drives the existing CLI archive, browser WASM and crates.io
workflows. `.github/workflows/publish.yml` additionally builds and smoke-tests CLI-only wheels
for macOS Intel/Apple silicon, manylinux 2_28 x86_64/aarch64 and Windows x86_64. We omit an
sdist: these installs should not unexpectedly require Rust on unsupported platforms.
The wheel workflow also runs for relevant main pushes and PRs; a manual run on main can
request publishing with `publish: true` and an explicit `tag`, such as `v0.6.0`.
`tools/check_wheel_versions.py` checks both Cargo manifests and all downloaded wheels
before the trusted-publishing action runs; a missing/invalid tag, absent wheels or any version
mismatch fails the job. Versions before 0.6.0 are ineligible for PyPI publication.
`python3 tools/test_wheel_versions.py -v` exercises the guard's
success and refusal paths. No version mapping is used.

Trusted publishing uses owner `pzfreo`, repository `draftwright`, workflow `publish.yml`,
environment `pypi`. The existing PyPI trusted publisher matches without any PyPI change. The publish action obtains an OIDC token (`id-token: write`); no stored token or
TestPyPI job is used. Build jobs have only read access. A local check must never publish.
