"""Activity observes the shared pipeline; cancellation never publishes partial ink."""

import json

import pytest
from build123d import Box, Cylinder, Pos
from typer.testing import CliRunner

from draftwright import BuildCancelled, build_drawing, observe_build
from draftwright.progress import stage


def _part():
    return Box(20, 20, 5) - Pos(5, 0, 0) * Cylinder(4, 5)


def _ink(drawing):
    return [
        (name, type(item).__name__, getattr(item, "label", None), getattr(item, "tip", None))
        for name, item in drawing.iter_annotations()
    ]


def test_observer_preserves_ink_and_records_nested_stage_times():
    baseline = build_drawing(_part(), scale=2)
    events = []
    with observe_build(events.append):
        observed = build_drawing(_part(), scale=2)
        summary = observed.lint_summary()
    assert _ink(observed) == _ink(baseline)
    assert summary == baseline.lint_summary()
    starts = [event for event in events if event.phase == "started"]
    assert {"build", "analysis", "recognition", "projection", "repair", "lint"} <= {
        event.stage[-1] for event in starts
    }
    assert any(event.stage[-1].startswith("placement.") for event in starts)
    assert any(
        event.phase == "resumed" and event.stage == ("build", "assemble") for event in events
    )
    assert all(event.elapsed_seconds >= event.stage_seconds >= 0 for event in events)
    assert [event.elapsed_seconds for event in events] == sorted(
        event.elapsed_seconds for event in events
    )
    json.dumps([event.to_dict() for event in events], allow_nan=False)


@pytest.mark.parametrize("seam", ["_validated_face_mesh", "_fixed_blockers"])
def test_cancellation_is_checked_inside_repeated_leader_work(monkeypatch, seam):
    import draftwright.annotations.leaders as leaders

    original = getattr(leaders, seam)
    entered = []

    def request(*args, **kwargs):
        entered.append(seam)
        control.cancel("inside repeated leader work")
        return original(*args, **kwargs)

    monkeypatch.setattr(leaders, seam, request)
    with pytest.raises(BuildCancelled) as caught, observe_build(lambda event: None) as control:
        build_drawing(_part(), scale=2)
    assert entered == [seam], (
        "the requested checkpoint must terminate this work on its first entry"
    )
    assert caught.value.completed_result is None
    assert caught.value.diagnostic["details"]["reason"] == "inside repeated leader work"
    assert any(name.startswith("placement.") for name in caught.value.diagnostic["stage"])


def test_cancel_at_publication_can_return_only_that_completed_build():
    def observe(event):
        if event.stage == ("build",) and event.phase == "finished":
            control.cancel()

    with pytest.raises(BuildCancelled) as caught, observe_build(observe) as control:
        build_drawing(_part(), scale=2)
    result = caught.value.completed_result
    assert result is not None and result.annotations()
    assert not [issue for issue in result.lint() if issue.severity == "error"]


def test_later_build_cancellation_never_offers_previous_part():
    cancel = False

    def observe(event):
        if cancel and event.phase == "started" and event.stage[-1] == "analysis":
            control.cancel()

    with observe_build(observe) as control:
        first = build_drawing(_part(), scale=2)
        cancel = True
        with pytest.raises(BuildCancelled) as caught:
            build_drawing(Box(40, 30, 15), scale=2)
    assert first.annotations() and caught.value.completed_result is None


def test_observer_failure_is_disabled_without_changing_drawing(caplog):
    calls = []

    def broken(event):
        calls.append(event)
        raise RuntimeError("observer failed")

    with observe_build(broken):
        drawing = build_drawing(_part(), scale=2)
    assert len(calls) == 1 and drawing.annotations()
    assert "notifications disabled" in caplog.text


@pytest.mark.parametrize("error", [ValueError, KeyboardInterrupt])
def test_failed_and_cancelled_stages_are_not_reported_finished(error):
    events = []
    expected = BuildCancelled if error is KeyboardInterrupt else ValueError
    with pytest.raises(expected) as caught, observe_build(events.append), stage("test"):
        raise error("interrupt test")
    assert [event.phase for event in events] == [
        "started",
        "cancelled" if error is KeyboardInterrupt else "failed",
    ]
    if error is KeyboardInterrupt:
        assert caught.value.diagnostic["stage"] == ["test"]


def test_deferred_edit_rolls_back_when_inner_work_is_cancelled(monkeypatch):
    import draftwright.annotations.leaders as leaders

    d = build_drawing(_part(), scale=2)
    (feature,) = [f for f in d.model().features if f.kind == "hole"]
    d.drop(feature)
    before = _ink(d)
    original = leaders._validated_face_mesh

    def request(*args, **kwargs):
        control.cancel()
        return original(*args, **kwargs)

    monkeypatch.setattr(leaders, "_validated_face_mesh", request)
    with pytest.raises(BuildCancelled), observe_build(lambda event: None) as control, d.deferred():
        d.callout(feature)
    assert _ink(d) == before


def test_cli_keyboard_interrupt_uses_structured_stderr(monkeypatch, tmp_path):
    import draftwright.builder as builder
    from draftwright.cli import app

    def interrupt(*args, **kwargs):
        with stage("recognition"):
            raise KeyboardInterrupt()

    monkeypatch.setattr(builder, "build_drawing", interrupt)
    source = tmp_path / "part.step"
    source.write_text("stub; intercepted before CAD")
    result = CliRunner().invoke(app, [str(source), "--no-report"])
    assert result.exit_code == 130
    assert result.stdout == ""
    assert '"phase": "cancelled"' in result.stderr
    assert '"recognition"' in result.stderr


def test_cancel_during_repair_validation_restores_unjudged_candidate(monkeypatch):
    from draftwright.linting import LintIssue
    from draftwright.progress import checkpoint

    drawing = build_drawing(Box(20, 30, 5), scale=2)
    original = list(drawing.iter_annotations())
    dimension = drawing.get_annotation("m_env_width")
    issue = LintIssue(
        severity="warning",
        code="dim_inside_part",
        message=f"Dim '{dimension.label}': annotation bbox overlaps part outline",
    )
    calls = []

    def lint(**kwargs):
        calls.append(1)
        if len(calls) == 1:
            return [issue]
        assert drawing.get_annotation("m_env_width") is not dimension
        control.cancel("before repair candidate validation")
        checkpoint()

    monkeypatch.setattr(drawing, "lint", lint)
    with pytest.raises(BuildCancelled), observe_build(lambda event: None) as control:
        drawing.repair(max_iter=1)
    assert calls == [1, 1]
    assert list(drawing.iter_annotations()) == original
    assert drawing.get_annotation("m_env_width") is dimension


@pytest.mark.parametrize(
    "options,visible",
    [([], False), (["--verbose"], True), (["--verbose", "--no-progress"], False)],
)
def test_cli_progress_is_pipe_safe_and_stdout_contains_only_paths(
    monkeypatch, tmp_path, options, visible
):
    from types import SimpleNamespace

    import draftwright.builder as builder
    from draftwright.cli import app

    def export(**kwargs):
        with stage("export"):
            return {"svg": "result.svg"}

    def build(**kwargs):
        with stage("recognition"):
            return SimpleNamespace(export=export)

    monkeypatch.setattr(builder, "build_drawing", build)
    source = tmp_path / "part.step"
    source.write_text("stub; intercepted before CAD")
    result = CliRunner().invoke(app, [str(source), "--format", "svg", "--no-report", *options])
    assert result.exit_code == 0, result.output
    assert result.stdout == "result.svg\n"
    assert ("recognition: started" in result.stderr) is visible
    assert ("export: finished" in result.stderr) is visible
    assert "\x1b" not in result.stderr


def test_nested_observers_restore_outer_scope_and_thread_cancel():
    from threading import Thread

    from draftwright.progress import checkpoint

    outer, inner = [], []
    with observe_build(outer.append):
        with observe_build(inner.append) as control:
            worker = Thread(target=control.cancel, args=("worker request",))
            worker.start()
            worker.join()
            with pytest.raises(BuildCancelled) as caught, stage("inner"):
                pytest.fail("a pending cancellation must stop before work starts")
        with stage("outer"):
            checkpoint()
    assert caught.value.diagnostic["details"]["reason"] == "worker request"
    assert [event.phase for event in inner] == ["cancelled"]
    assert [event.stage for event in outer] == [("outer",), ("outer",)]
    checkpoint()  # The cancelled inner context must not leak into ordinary later work.


def test_terminal_progress_never_redirects_output_paths(monkeypatch, capsys):
    from io import StringIO

    import rich.console

    from draftwright.cli import _progress_display

    terminal_sink = StringIO()
    console = rich.console.Console(file=terminal_sink, force_terminal=True, color_system=None)
    assert console.is_terminal
    monkeypatch.setattr(rich.console, "Console", lambda **kwargs: console)
    with _progress_display(verbose=False):
        with stage("recognition"):
            print("result.svg")
    assert capsys.readouterr().out == "result.svg\n"
    assert "result.svg" not in terminal_sink.getvalue()
