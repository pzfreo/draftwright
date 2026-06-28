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

## Pull requests

- Branch off `main` and open a PR with a clear description of **why**.
- Keep changes focused; add tests for new behaviour.
- Make sure the fast test tier passes locally before pushing.

Questions or commercial-licensing enquiries: pzfreo@gmail.com.
