"""Evidence-backed slices for the real sloped-profile case study (#909)."""

from collections import Counter
from pathlib import Path

import pytest
from build123d import import_step
from quiddity import RaisedPad, recognise_rectangular_pads

from draftwright import Drawing, build_drawing
from draftwright.drawing import feature_key
from draftwright.model import PadFeature
from draftwright.model.compiled import compile_dimensions
from draftwright.sheet_emit import generate_sheet_script

_ISSUE_909 = Path(__file__).parent / "fixtures" / "issue_909_basic_part_design_017_body.step"


def test_touching_lower_ledges_do_not_hide_the_case_studys_raised_pad():
    """Only ledges at the recovered local base identify a nested staircase."""
    part = import_step(str(_ISSUE_909))

    assert recognise_rectangular_pads(part) == [RaisedPad(-15.5, 15.5, 8.0, 24.7, 13.0, 20.0)]


def test_case_study_pad_reaches_the_drawing_with_complete_owned_footprint(monkeypatch):
    import draftwright.builder as builder

    build_once = builder._build_drawing_once
    attempts = []

    def capture_attempt(*args, **kwargs):
        candidate = build_once(*args, **kwargs)
        attempts.append(candidate)
        return candidate

    monkeypatch.setattr(builder, "_build_drawing_once", capture_attempt)
    drawing = build_drawing(_ISSUE_909)
    # The angle and location requirements exceed the selected A4 corridor even
    # after the height detail succeeds. Recovery must keep searching for the
    # still-missing shoulder, rather than treating any detail as completion.
    cramped = attempts[0]
    assert (cramped.page_w, cramped.page_h) == (297, 210)
    assert "detail_a" in cramped.views
    assert any(issue.code == "step_position_dropped" for issue in cramped.lint())
    assert (drawing.page_w, drawing.page_h) == (420, 297)
    assert drawing.scale_decision["status"] == "automatic_replanned"
    assert drawing.scale_decision["attempts"][-1]["reason"] == "page_escalation_after_detail"
    source = RaisedPad(-15.5, 15.5, 8.0, 24.7, 13.0, 20.0)

    recognition = drawing.recognition()
    assert recognition is not None
    assert recognition.pads == (source,)
    kinds = Counter(feature.kind for feature in drawing.model().features)
    assert kinds["pad"] == kinds["envelope"] == kinds["step_level"] == 1

    pad = next(feature for feature in drawing.model().features if isinstance(feature, PadFeature))
    assert (pad.lo, pad.hi, pad.w_center, pad.width, pad.z0, pad.z1) == (
        -15.5,
        15.5,
        16.35,
        16.7,
        13.0,
        20.0,
    )
    owned = drawing.annotations_of(pad)
    assert {name: owned[name].label for name in ("m_pad0_width", "m_pad0_length", "m_locx0")} == {
        "m_pad0_width": "16.7",
        "m_pad0_length": "31",
        "m_locx0": "26.5",
    }
    assert {
        key["parameter_id"]
        for name in ("m_pad0_width", "m_pad0_length", "m_locx0")
        for key in drawing.measurement_keys(name)
    } == {
        "pad_width.length",
        "pad_length.length",
        "location_pad.location",
    }
    detail_labels = Counter(
        annotation.label
        for name, annotation in drawing.iter_annotations()
        if name.startswith("dim_detail_a_step")
    )
    main_labels = Counter(
        annotation.label
        for name, annotation in drawing.iter_annotations()
        if name.startswith("dim_step")
    )
    assert main_labels == Counter({"21": 1})
    assert detail_labels == Counter({"26": 1})
    assert not main_labels & detail_labels
    shoulder = drawing.get_annotation("dim_shoulder_y0")
    assert shoulder.label == "21"
    assert drawing.measurement_keys("dim_shoulder_y0") == [
        {
            "feature": feature_key(
                next(f for f in drawing.model().features if f.kind == "step_level")
            ),
            "parameter_id": "step_position.length",
        }
    ]
    assert Counter(
        annotation.label
        for _, annotation in drawing.iter_annotations()
        if hasattr(annotation, "measured_angle")
    ) == Counter({"51.1°": 1, "128.9°": 1})
    # An angle label over a blank region inside the view extents is legitimate;
    # every diagnostic must describe that informational condition only.
    # `nominal_rounded` joins it: this part's sloped profile gives lengths that are not
    # round at one decimal place, so the sheet prints nominals a few microns off the model
    # and now says so (#1600). Also informational, and the assertion below still holds the
    # line that matters — nothing here is a warning or an error.
    assert all(
        issue.severity == "info"
        and issue.code in {"view_annotation_inside_extents", "nominal_rounded"}
        for issue in drawing.lint()
    )


def test_case_study_detail_rungs_keep_their_compiled_measurement_identity():
    drawing = build_drawing(_ISSUE_909)
    step = next(feature for feature in drawing.model().features if feature.kind == "step_level")
    detail_names = {name for name in drawing.annotations() if name.startswith("dim_detail_a_step")}
    ladder = compile_dimensions(drawing.model()).ladder("step_height")
    assert ladder is not None
    rung_by_label = {rung.final_label: rung for rung in ladder.rungs}

    assert detail_names
    for name in detail_names:
        rung = rung_by_label[drawing.get_annotation(name).label]
        assert rung.id is not None
        assert drawing.measurement_keys(name) == [
            {
                "feature": feature_key(rung.id.feature),
                "parameter_id": rung.id.parameter,
            }
        ]
    assert detail_names <= set(drawing.annotations_of(step))

    removed = set(drawing.drop(step))
    assert detail_names <= removed
    assert not detail_names & set(drawing.annotations())


@pytest.mark.parametrize(
    ("name_prefix", "parameter_id"),
    [("m_pad", "pad_length.length"), ("m_locx", "location_pad.location")],
)
def test_removing_a_required_pad_dimension_reports_the_real_requirement(name_prefix, parameter_id):
    drawing = build_drawing(_ISSUE_909, detail_view=True)
    pad = next(feature for feature in drawing.model().features if isinstance(feature, PadFeature))
    owned = drawing.annotations_of(pad)
    name = next(
        name
        for name in owned
        if name.startswith(name_prefix)
        and any(key["parameter_id"] == parameter_id for key in drawing.measurement_keys(name))
    )
    drawing.remove(name)

    issues = [issue for issue in drawing.lint() if issue.code == "pad_footprint_not_defined"]
    assert len(issues) == 1
    assert "1 rectangular raised pad" in issues[0].message


def test_case_study_pad_survives_the_generated_declaration(tmp_path, monkeypatch):
    direct = build_drawing(_ISSUE_909)
    captured = {}
    monkeypatch.setattr(
        Drawing,
        "export",
        lambda self, *args, **kwargs: captured.setdefault("drawing", self),
    )
    script_path = generate_sheet_script(
        str(_ISSUE_909), out=str(tmp_path / "issue_909"), title="ISSUE 909"
    )
    source = Path(script_path).read_text(encoding="utf-8")
    assert source.count("sheet.pad(") == 1
    exec(compile(source, script_path, "exec"), {})  # noqa: S102 - exercise generated source
    scripted = captured["drawing"]

    def pads(drawing):
        return [feature for feature in drawing.model().features if isinstance(feature, PadFeature)]

    assert pads(scripted) == pads(direct)
    assert {
        name: scripted.get_annotation(name).label
        for name in scripted.annotations()
        if name.startswith("m_pad")
    } == {
        name: direct.get_annotation(name).label
        for name in direct.annotations()
        if name.startswith("m_pad")
    }
