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
scripts/pr-check --quick      # smoke tier plus every static PR gate
scripts/pr-check              # final preflight: full coverage plus changed-line gate
```

For a full local run, spread it across cores with
`uv run pytest -n auto --dist loadscope`. See [CLAUDE.md](CLAUDE.md) for the test
tiers and the architecture overview, and `docs/adr/` for the design decisions
behind layout, scaling, and annotation placement — please read the relevant ADRs
before changing those areas.

### Coverage

CI measures line and branch coverage on the full fast tier and enforces the
`[tool.coverage.report] fail_under` floor in `pyproject.toml`. The canonical
Linux/Python 3.13 job uploads to Codecov and retains the XML plus browsable HTML
reports for 14 days. The other OS/Python jobs run the same tests without redundant
coverage instrumentation. To reproduce the canonical run locally:

```
uv run --python 3.13 pytest tests/ -n auto --dist loadscope \
  --cov=src/draftwright --cov-report=term-missing \
  --cov-report=xml --cov-report=html:htmlcov
```

The baseline recorded for #825 on Linux/Python 3.13 was **92.05% combined
line-and-branch coverage** (93.90% statements and 86.89% branches); the initial
floor is 90%. Coverage thresholds are a ratchet: raise the floor after the canonical
job remains above the proposed value, and do not lower it to accommodate an untested
change.

`scripts/pr-check` additionally requires 93% line coverage over changed source lines,
compared with `origin/main` by default. Set `BASE_REF=upstream/main` for a fork, or when
the branch has another base. Stage new files under `src/` first: Git does not include
untracked files in a diff, so the command refuses to certify them invisibly.

This local check catches low changed-line coverage before the remote matrix. It is not
Codecov parity: branch partials and the project-coverage ratchet remain remote gates.

### Evidence-gated slices

Recognition, completeness, placement, and compiler work starts from a vertical slice,
not an architecture phase. Before production code, record:

1. a real or minimal fixture and its exact current false result;
2. the desired user-visible and semantic result;
3. the smallest contract being tested;
4. a mutation that must make the proposed guard fail;
5. the affected automatic, declared, generated-script, and repeated-lint paths;
6. work deliberately left outside the slice.

If two consecutive adversarial review rounds invalidate the same core association
strategy, stop extending that approach. Preserve its fixtures, reclassify it as discovery,
and split the missing upstream contract. A green suite is necessary, but it does not
establish a semantic claim until the named mutation makes the guard fail.

Test helpers follow the same evidence rule. Extract a structural assertion only after
two slices use the same contract. Physical grouping, applicability, and correspondence
keys stay family-specific unless a failing fixture proves otherwise.

## Pull requests

- Branch off `main` and open a PR with a clear description of **why**.
- Keep changes focused; add a fixture, semantic oracle, and targeted mutation for new
  behaviour rather than testing presentation proxies.
- Use the PR template to record the slice and its stop condition.
- Run `scripts/pr-check --quick` while iterating and `scripts/pr-check` before the final push.

Questions or commercial-licensing enquiries: pzfreo@gmail.com.
