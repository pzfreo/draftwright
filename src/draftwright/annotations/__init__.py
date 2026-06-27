"""Annotation passes (#138 / ADR 0005, P5): the split annotate.py capabilities.

Each module holds one drafting capability as `(dwg, a, ...)` pass functions;
`annotate._auto_annotate` orchestrates them. Submodules import only `_core`/
`projection`/build123d — never `annotate`/`make_drawing` — so the DAG stays acyclic.
"""
