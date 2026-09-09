"""One isolated profiling run of the original field-report automatic input."""

import argparse
import cProfile
import hashlib
import json
import logging
import os
import platform
import pstats
import subprocess
import sys
import time
from contextlib import nullcontext
from importlib.metadata import packages_distributions, version
from pathlib import Path

from draftwright import build_drawing

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("source", type=Path)
parser.add_argument("output", type=Path)
parser.add_argument("--observe", action="store_true")
parser.add_argument("--permissive-diagnostic", action="store_true")
parser.add_argument("--cancel-at", help="Request cooperative cancellation on this started stage")
args = parser.parse_args()
if args.cancel_at and not args.observe:
    parser.error("--cancel-at requires --observe")
output = args.output
output.mkdir(exist_ok=False)
source = args.source
assert (
    hashlib.sha256(source.read_bytes()).hexdigest()
    == "7d5c95e5367d2d3cbc418d628b5d418a3018b1ed9e6dbdb73337c417496146e2"
)
metadata = {
    "input": str(source),
    "input_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    "python": sys.version,
    "platform": platform.platform(),
    "processor": platform.processor(),
    "kernel_distributions": {
        name: version(name) for name in packages_distributions().get("OCP", ())
    },
    "logical_cpus": os.cpu_count(),
    "cpu_brand": subprocess.check_output(
        ["sysctl", "-n", "machdep.cpu.brand_string"], text=True
    ).strip()
    if sys.platform == "darwin"
    else platform.processor(),
    "instrumentation": "cProfile adds overhead; cumulative function times overlap and must not be summed",
    "packages": {
        name: version(name)
        for name in ("draftwright", "quiddity", "build123d", "build123d-drafting-helpers")
    },
    "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
    "stages": [],
    "options": {
        "observe": args.observe,
        "cancel_at": args.cancel_at,
        "scale_policy": "permissive" if args.permissive_diagnostic else "fallback",
    },
    "working_diff_sha256": hashlib.sha256(
        subprocess.check_output(["git", "diff", "HEAD"])
    ).hexdigest(),
}
(output / "environment.json").write_text(json.dumps(metadata, indent=2))
logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("draftwright").setLevel(logging.INFO)
logger = logging.getLogger("draftwright.profile")


def measure(stage, function):
    profiler = cProfile.Profile()
    started = time.perf_counter()
    logger.info("PROFILE START %s", stage)
    status = "interrupted_or_failed"
    try:
        profiler.enable()
        result = function()
        status = "completed"
        return result
    finally:
        profiler.disable()
        elapsed = time.perf_counter() - started
        profiler.dump_stats(str(output / f"{stage}.pstats"))
        with (output / f"{stage}-cumulative.txt").open("w") as stream:
            pstats.Stats(profiler, stream=stream).strip_dirs().sort_stats(
                "cumulative"
            ).print_stats(80)
        metadata["stages"].append({"name": stage, "elapsed_seconds": elapsed, "status": status})
        (output / "environment.json").write_text(json.dumps(metadata, indent=2))
        logger.info("PROFILE END %s %.3fs %s", stage, elapsed, status)


def observe(event):
    with (output / "progress.jsonl").open("a") as stream:
        stream.write(json.dumps(event.to_dict()) + "\n")
    if args.cancel_at and event.phase == "started" and event.stage[-1] == args.cancel_at:
        control.cancel("profile cancellation probe")


try:
    context = nullcontext()
    if args.observe:
        from draftwright import observe_build

        context = observe_build(observe)
    with context as control:
        drawing = measure(
            "build",
            lambda: build_drawing(
                str(source),
                title="THREE-KEY FRAME - TITANIUM REVIEW",
                number="WK-TI-001",
                material="TITANIUM - GRADE TBD",
                tolerance="SEE REVIEW NOTES",
                page="A2",
                scale=2,
                projection="third",
                date="2026-09-08",
                revision="P1",
                reproducible=True,
                scale_policy="permissive" if args.permissive_diagnostic else "fallback",
                trace=str(output / "solve.trace.json"),
            ),
        )
        summary = measure("lint", drawing.lint_summary)
        (output / "lint-summary.json").write_text(json.dumps(summary, indent=2))
        for name in ("scale_decision", "page_decision", "section_decision"):
            (output / f"{name}.json").write_text(
                json.dumps(getattr(drawing, name, None), indent=2)
            )
        paths = measure(
            "export", lambda: drawing.export(str(output / "frame"), formats=("svg", "pdf", "dxf"))
        )
        (output / "artifacts.json").write_text(
            json.dumps(
                {
                    key: {"path": str(path), "bytes": Path(path).stat().st_size}
                    for key, path in paths.items()
                },
                indent=2,
            )
        )
except BaseException as exc:
    (output / "failure.json").write_text(
        json.dumps(
            {
                "exception": type(exc).__name__,
                "message": str(exc),
                "decision": getattr(exc, "decision", None),
                "diagnostic": getattr(exc, "diagnostic", None),
                "note": "No completed validated candidate is claimed.",
            },
            indent=2,
        )
    )
    raise
