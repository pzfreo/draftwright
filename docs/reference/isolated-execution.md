# Isolated CAD-to-drawing execution

Use a separate drawing environment when a CAD project already has its own dependencies.
Export a STEP file from CAD and run Draftwright against that file. Installing into the CAD
project's environment can change its build123d and OCP dependencies; isolation lets each
application resolve its own supported stack.

## Create the drawing environment

Choose a Python version supported by the Draftwright release you install. Use a **new**
directory for the environment. These commands are for macOS/Linux; substitute the selected
Python executable for `python3`:

```sh
python3 -m venv /path/to/drawing-venv
/path/to/drawing-venv/bin/python -m pip install draftwright
/path/to/drawing-venv/bin/python -m pip check
/path/to/drawing-venv/bin/draftwright --version
```

On Windows, use `py -3.12 -m venv C:\drawing-venv` (or another supported installed version),
then `C:\drawing-venv\Scripts\python.exe` and `C:\drawing-venv\Scripts\draftwright.exe`
in place of the Unix executable paths. Activation is optional because the commands name the
intended environment explicitly. Do not run a bare `pip install` from the CAD project's shell
and assume it targets this new environment.

Let the selected release's package metadata resolve its dependencies. Do not copy the field
report's historical `build123d<0.11` advice into every environment or manually replace an OCP
distribution to force resolution. Supported stacks can differ by Python version. If resolution
fails, retain the Python/platform and resolver error, and check the release's supported Python
versions and available wheels before choosing a different fresh environment.

## Inspect the interpreter that will execute the drawing

Save the following as `environment_check.py` outside your source packages. Run it with the
same Python executable that will run your drawing script. It prints the interpreter first,
installed distribution versions and actual imported module locations. A null distribution
version means that distribution is absent; OCP distribution names are alternatives, so a null
entry alone is not a failure.

<!-- issue-1538-environment-example -->
```python
import importlib
from importlib.metadata import PackageNotFoundError, version
import json
import platform
import sys

print(json.dumps({"interpreter": sys.executable, "python": platform.python_version(),
                  "platform": platform.platform()}), flush=True)
for name in ("draftwright", "build123d", "quiddity", "build123d-drafting-helpers",
             "cadquery-ocp", "cadquery-ocp-novtk", "cadquery-ocp-proxy"):
    try:
        installed = version(name)
    except PackageNotFoundError:
        installed = None
    print(json.dumps({"distribution": name, "version": installed}), flush=True)

failed = False
for name in ("OCP", "build123d", "build123d_drafting", "quiddity", "draftwright"):
    print(json.dumps({"importing": name}), flush=True)
    try:
        module = importlib.import_module(name)
    except Exception as exc:
        failed = True
        print(json.dumps({"module": name, "error": type(exc).__name__,
                          "detail": str(exc)}), flush=True)
    else:
        print(json.dumps({"module": name, "loaded_from": getattr(module, "__file__", None)}),
              flush=True)
print(json.dumps({"imports_ok": not failed}), flush=True)
raise SystemExit(1 if failed else 0)
```
<!-- /issue-1538-environment-example -->

```sh
/path/to/drawing-venv/bin/python environment_check.py
/path/to/drawing-venv/bin/python -m pip check
/path/to/drawing-venv/bin/python -m pip freeze > generation-requirements.txt
```

Check both command exit statuses. `imports_ok=true` means these imports completed; `pip check`
checks declared dependency compatibility. Neither proves that every native-kernel operation,
drawing or export will work. A native process crash may stop the probe before its final record;
retain its last `importing` line and process exit status. Installed metadata alone is not proof
of importability, and a successful `--version` is only a CLI metadata check.

For `ModuleNotFoundError`, check the reported interpreter and the missing module named in
`detail`: a missing dependency is different from a missing Draftwright installation. Other
import failures can indicate an incompatible native package or a local file shadowing a
package; the printed module locations help identify the latter. Capture the diagnostics before
changing anything. Recreate a dedicated drawing environment if needed instead of repairing the
CAD project's environment opportunistically.

## Run the drawing against the exported STEP

Copy the STEP into a dedicated drawing output directory, then use the explicit executable
and output prefix (the default prefix is relative to the current working directory):

```sh
/path/to/drawing-venv/bin/draftwright /path/to/drawing-output/part.step \
  --out /path/to/drawing-output/part --format pdf,svg
```

Alternatively run your authored generator with
`/path/to/drawing-venv/bin/python draw_part.py`. Keep its inputs and the generation-time
`generation-requirements.txt` with the outputs. Review the resulting drawing and diagnostics;
installation success is not a completeness or manufacturing-release claim. See
[reports](reports.md) and [generated artifacts](generated-artifacts.md).

## CAD MCP execution is a separate environment

A terminal environment and a connected CAD MCP worker can use different interpreters.
Installing Draftwright in the terminal does not establish that an MCP `execute` call can
import it. Use the server's advertised version/capability tool first, then a minimal permitted
`import draftwright` through that same execution tool. A sandbox rejection is distinct from
`ModuleNotFoundError`; retain the original error rather than treating either as evidence of a
Draftwright core defect.

In the #1529 follow-up, build123d-mcp 0.3.84 advertised Draftwright as importable, while its
connected `execute` worker returned `ModuleNotFoundError`. Its version response did not expose
the worker interpreter or Draftwright availability. This is tracked in
[build123d-mcp#475](https://github.com/pzfreo/build123d-mcp/issues/475); it is evidence about the
tested server, not a claim about every MCP deployment. Its optional Draftwright installation
policy is recorded in [build123d-mcp#465](https://github.com/pzfreo/build123d-mcp/issues/465).

If a worker does not expose its interpreter, use its deployment configuration/operator
inspection; do not infer it from a similarly named process or from your terminal's Python.
The local probe above is intended for a normal Python process, not a restricted execution
sandbox. Do not disable sandbox protections or install dependencies into the CAD worker as an
automatic fallback. The isolated STEP workflow above remains available while server capability
reporting is corrected upstream.
