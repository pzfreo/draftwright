# Entry points

## Build a drawing

::: draftwright.builder.build_drawing

## Build and export in one call

::: draftwright.builder.make_drawing

## Inspect a STEP file without drawing it

::: draftwright.inspection.inspect_step

## Observe progress and request cancellation

`observe_build()` reports activity from the existing pipeline. It works around automatic
`build_drawing()` calls, `Sheet.build()`, and subsequent lint, repair, deferred edits and
`Drawing.export()` calls. It does not select views, change search limits or relax requirements.

```python
from draftwright import BuildCancelled, build_drawing, observe_build

try:
    with observe_build(lambda event: print(event.to_dict())) as control:
        drawing = build_drawing("part.step")
        drawing.export("out", formats=("pdf",))
        # A UI or another thread can call control.cancel("user request").
except BuildCancelled as error:
    print(error.diagnostic)
```

Each immutable event carries a stage path, phase, elapsed seconds for the observation context
and current stage, and details such as projection view or the existing retry/budget reason.
There is no estimated percentage. Callbacks run synchronously; keep them short. Ordinary
callback exceptions disable notifications with a warning so a broken observer does not abort
the drawing. Use `control.cancel()` to stop deliberately. Observation scopes are context-local;
enter the context in the thread doing the build and pass its controller to the cancelling UI.

Cancellation is cooperative. Checkpoints run between stages and during repeated leader
candidate/mesh work. A native CAD call must return before a Python checkpoint can execute;
this API supplies no hard kernel timeout. Ctrl-C inside the context raises the same
`BuildCancelled`, a `KeyboardInterrupt` subclass carrying a JSON-serializable diagnostic.
Cancelled and failed stages are distinct from finished stages.

No internal search candidate is returned on cancellation. If the public build has finished
and cancellation arrives at its publication boundary, `error.completed_result` contains that
normal mutable Drawing; otherwise it is `None`. It is not a snapshot or a manufacturing
approval. During a later export cancellation the caller already owns `drawing`; no earlier
build is attached to the exception. Deferred edits roll back interrupted placement; repair
rolls back a provisional change if its critique is interrupted. Already accepted edits and
files written before an export interruption are not undone.

The direct-render CLI displays a live stage and elapsed time on stderr in a terminal.
When redirected it emits no live controls; `--verbose` emits plain stage events to stderr.
`--no-progress` disables both forms. Output paths remain on stdout. Ctrl-C prints the
cancellation diagnostic to stderr and exits with code 130. `--script` generation does not use
this display.


## CLI output destinations

The CLI defaults to writing beside the supplied STEP input. For example,
`draftwright /parts/frame.step --format all` writes `/parts/frame.svg`, `.dxf`, `.pdf`,
`.png`, and `.draftwright.json`. `--script` writes `/parts/frame.py` and its recognition
inspection sidecar; replay uses the destination selected when the script was generated and
writes `/parts/frame.draftwright-assessment.json` after a successful build/export. See
[generated-script replay assessment](replay-assessment.md) for why the two JSON files have
separate schemas and authority.

Use `--out-dir .` to write into the current working directory, `--out-dir drawings` to
create/use a destination directory, or `--out drawings/revised-frame` to choose a prefix.
`--out` and `--out-dir` are mutually exclusive. Relative overrides are resolved at invocation.
For a live object script (`module:part` or `file.py:part`), the default is `drawing.py` in the
working directory; `--out-dir` places that basename in the requested directory.

This changes the CLI default for STEP inputs outside the working directory. Python API
output defaults are unchanged. Every CLI prints its written artifact paths on stdout.
