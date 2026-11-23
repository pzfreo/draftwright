# Native recess regressions

Array, grid and curved-grid are byte copies of quiddity-rust's captured Python
section-recess fixtures (09a1faf2f58f8e05, 29cd4ef2681bfe08, 9a5f38a8c0fbe938).
They are the parts used by Python quiddity/draftwright's pattern tests. Tuner is
Python draftwright's tests/fixtures/tuner_jig_blind_obround_pockets.step (#837).
No recogniser or STEP reconstruction runs at test time.

`tuner.json.gz` is the unchanged Python quiddity 0.4.0.dev0 recognition wrapper,
captured with tools/recognise.py using /Users/paul/repos/quiddity-python/.venv/bin/python.
It retains Python's working frame and its original pattern member order.
