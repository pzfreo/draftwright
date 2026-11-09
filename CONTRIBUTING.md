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

This change must merge only after the repository rename to `pzfreo/draftwright`; until then
its new GitHub links reach the old Python repository. The Python repository moves to
`pzfreo/draftwright-python` and is archived separately.

The release commit updates the native and WASM Cargo versions together, `Cargo.lock`, README
status and CHANGELOG. Keep the engine and tags in the existing 0.2.x series and preserve the
0.2.24 Spec migration window. Wheel packaging lives in the root `pyproject.toml`, using
maturin's `bindings = "bin"` and dynamic Cargo-derived version: there is currently no duplicated
pyproject version to bump. Include any future explicit pyproject version update in that same
release commit once publishing is enabled. PyPI publishing is pending.

Run formatting, strict release workspace/all-target Clippy, library tests and the full
required-corpus workspace nextest suite before releasing. Build the local wheel with
`RUSTC_WRAPPER= CARGO_BUILD_JOBS=6 uv tool run maturin build --release --locked`, install it
in a clean venv, and check both commands and a fixture render. No build outputs are committed.

Publishing a GitHub release drives the existing CLI archive, browser WASM and crates.io
workflows. `.github/workflows/publish.yml` additionally builds and smoke-tests CLI-only wheels
for macOS Intel/Apple silicon, manylinux 2_28 x86_64/aarch64 and Windows x86_64. We omit an
sdist: these installs should not unexpectedly require Rust on unsupported platforms.
The wheel workflow also runs for relevant main pushes and PRs; a manual run on main can
request publishing with `publish: true`. The PyPI publish job deliberately fails closed at its
TODO guard on both publish triggers; removing that refusal requires the maintainer's version
agreement decision and a guard checking the wheel metadata. The release tag must still equal
the Cargo engine version, as in `crates.yml`.

Trusted publishing uses owner `pzfreo`, repository `draftwright`, workflow `publish.yml`,
environment `pypi`. After the rename the existing PyPI trusted publisher matches without any
PyPI change. The publish action obtains an OIDC token (`id-token: write`); no stored token or
TestPyPI job is used. Build jobs have only read access. A local check must never publish.
