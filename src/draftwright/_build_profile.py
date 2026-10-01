"""Pytest plugin and runner support for reproducible build-cost profiles (#1307)."""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from time import perf_counter
from typing import Any

_MIN_REPORT_SECONDS = 0.005
_STATE: _ProfileState | None = None
_CURRENT_NODEID = "<collection>"


@dataclass
class _ProfileState:
    output_dir: Path
    worker_id: str
    collected: int | None = None
    calls: dict[tuple[str, str], list[float]] = field(
        default_factory=lambda: defaultdict(lambda: [0, 0.0])
    )
    phases: list[dict[str, Any]] = field(default_factory=list)

    def record_call(self, seam: str, seconds: float) -> None:
        aggregate = self.calls[(_CURRENT_NODEID, seam)]
        aggregate[0] += 1
        aggregate[1] += seconds

    def write(self, exitstatus: int) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        safe_worker = re.sub(r"[^A-Za-z0-9_.-]", "_", self.worker_id)
        path = self.output_dir / f"profile-{safe_worker}.json"
        payload = {
            "worker_id": self.worker_id,
            "collected": self.collected,
            "exitstatus": exitstatus,
            "calls": [
                {
                    "nodeid": nodeid,
                    "seam": seam,
                    "count": int(values[0]),
                    "seconds": values[1],
                }
                for (nodeid, seam), values in sorted(self.calls.items())
            ],
            "phases": self.phases,
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path


def _timed(seam: str, function: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(function)
    def wrapper(*args, **kwargs):
        started = perf_counter()
        try:
            return function(*args, **kwargs)
        finally:
            assert _STATE is not None
            _STATE.record_call(seam, perf_counter() - started)

    return wrapper


def pytest_addoption(parser) -> None:
    group = parser.getgroup("draftwright-build-profile")
    group.addoption(
        "--build-profile-dir",
        help="write one Draftwright build profile JSON file per pytest-xdist worker",
    )


def pytest_configure(config) -> None:
    global _STATE
    output = config.getoption("--build-profile-dir")
    if not output:
        return

    worker_input = getattr(config, "workerinput", None)
    # Under xdist the controller executes no tests; worker processes write the useful files.
    if worker_input is None and getattr(config.option, "numprocesses", None):
        return

    worker_id = worker_input.get("workerid", "worker") if worker_input else "main"
    _STATE = _ProfileState(Path(output).resolve(), worker_id)

    import draftwright.builder as builder
    import draftwright.sheet as sheet

    draftwright = sys.modules["draftwright"]
    builder_public = _timed("builder.build_drawing", builder.build_drawing)
    builder.build_drawing = builder_public
    # The public lazy package attribute and Sheet's import-time binding otherwise bypass a
    # patch installed only on builder.py. That omission made the first #1307 census a lower
    # bound, so all three bindings are explicit here.
    setattr(draftwright, "build_drawing", builder_public)  # noqa: B010 - lazy package attribute
    sheet.build_drawing = _timed("sheet.build_drawing", sheet.build_drawing)
    builder._build_drawing_once = _timed(
        "builder._build_drawing_once", builder._build_drawing_once
    )


def pytest_collection_finish(session) -> None:
    if _STATE is not None:
        _STATE.collected = len(session.items)


def pytest_runtest_logstart(nodeid, location) -> None:
    del location
    global _CURRENT_NODEID
    _CURRENT_NODEID = nodeid


def pytest_runtest_logfinish(nodeid, location) -> None:
    del nodeid, location
    global _CURRENT_NODEID
    _CURRENT_NODEID = "<between-tests>"


def pytest_runtest_logreport(report) -> None:
    if _STATE is not None and report.duration >= _MIN_REPORT_SECONDS:
        _STATE.phases.append(
            {
                "nodeid": report.nodeid,
                "phase": report.when,
                "seconds": report.duration,
                "outcome": report.outcome,
            }
        )


def pytest_sessionfinish(session, exitstatus) -> None:
    del session
    if _STATE is not None:
        _STATE.write(int(exitstatus))


def build_pytest_command(output_dir: Path, pytest_args: list[str]) -> list[str]:
    """Return argv without joining *pytest_args* into a shell-expanded module string."""
    return [
        "uv",
        "run",
        "pytest",
        "-p",
        "draftwright._build_profile",
        f"--build-profile-dir={output_dir}",
        *pytest_args,
    ]


def summarise_profiles(output_dir: Path, expected_collected: int) -> dict[str, Any]:
    paths = sorted(output_dir.glob("profile-*.json"))
    if not paths:
        raise ValueError(f"no worker profiles were written to {output_dir}")

    by_seam: dict[str, list[float]] = defaultdict(lambda: [0, 0.0])
    workers = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        collected = payload.get("collected")
        if collected != expected_collected:
            raise ValueError(
                f"{path.name} collected {collected!r}, expected {expected_collected}; "
                "refusing to report a successful empty or partial profile"
            )
        workers.append(payload["worker_id"])
        for call in payload["calls"]:
            aggregate = by_seam[call["seam"]]
            aggregate[0] += call["count"]
            aggregate[1] += call["seconds"]

    return {
        "expected_collected": expected_collected,
        "workers": workers,
        "seams": {
            seam: {"count": int(values[0]), "seconds": values[1]}
            for seam, values in sorted(by_seam.items())
        },
    }
