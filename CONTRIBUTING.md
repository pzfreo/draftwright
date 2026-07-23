# Contributing to draftwright

Thanks for your interest in improving draftwright. Contributions of all kinds —
bug reports, fixes, features, and documentation — are welcome.

## Contributor License Agreement

draftwright is dual-licensed: it is released under the AGPL-3.0 and also offered
under separate commercial terms. So that contributions can be included in **both**
releases, all contributors must agree to the
[Contributor License Agreement (CLA.md)](CLA.md).

You keep the copyright to your work — the CLA grants a licence, not an
assignment. **By opening a pull request you indicate your agreement to the CLA**
for that and all future contributions. Please read it before submitting.

## Development

draftwright uses [`uv`](https://github.com/astral-sh/uv) for environment and
dependency management.

```
uv sync                       # install dependencies
uv run pytest -m smoke        # quick "did I break something obvious" check (~30 s)
uv run pytest                 # full fast tier
```

For a full local run, spread it across cores with
`uv run pytest -n auto --dist loadscope`. See [CLAUDE.md](CLAUDE.md) for the test
tiers and the architecture overview, and `docs/adr/` for the design decisions
behind layout, scaling, and annotation placement — please read the relevant ADRs
before changing those areas.

### Coverage

CI measures line and branch coverage on the full fast tier and enforces the
`[tool.coverage.report] fail_under` floor in `pyproject.toml` on every supported
OS/Python combination. The canonical Linux/Python 3.12 job uploads to Codecov and
retains the XML plus browsable HTML reports for 14 days. To reproduce that run locally:

```
uv run pytest tests/ -n auto --dist loadscope \
  --cov=src/draftwright --cov-report=term-missing \
  --cov-report=xml --cov-report=html:htmlcov
```

The baseline recorded for #825 on Linux/Python 3.13 was **92.05% combined
line-and-branch coverage** (93.90% statements and 86.89% branches); the initial
floor is 90%. Coverage thresholds are a ratchet: raise the floor after the lowest
result across the supported CI matrix remains above the proposed value, and do not
lower it to accommodate an untested change.

## Pull requests

- Branch off `main` and open a PR with a clear description of **why**.
- Keep changes focused; add tests for new behaviour.
- Make sure the fast test tier passes locally before pushing.

Questions or commercial-licensing enquiries: pzfreo@gmail.com.
