"""Evidence-backed slices for the real sloped-profile case study (#909)."""

from collections import Counter
from pathlib import Path

from build123d import import_step

from draftwright import Drawing, build_drawing
from draftwright.model import PadFeature
from draftwright.recognition import RaisedPad, recognise_rectangular_pads
from draftwright.sheet_emit import generate_sheet_script

_ISSUE_909 = Path(__file__).parent / "fixtures" / "issue_909_basic_part_design_017_body.step"


def test_touching_lower_ledges_do_not_hide_the_case_studys_raised_pad():
    """Shared edges are not the positive-area overlap that identifies a staircase."""
    part = import_step(str(_ISSUE_909))

    assert recognise_rectangular_pads(part) == [RaisedPad(-15.5, 15.5, 8.0, 24.7, 13.0, 20.0)]


def test_case_study_pad_reaches_the_drawing_with_complete_owned_footprint():
    drawing = build_drawing(_ISSUE_909, detail_view=True)
    source = RaisedPad(-15.5, 15.5, 8.0, 24.7, 13.0, 20.0)

    recognition = drawing.recognition()
    assert recognition is not None
    assert recognition.pads == (source,)
    assert Counter(feature.kind for feature in drawing.model().features) == Counter(
        {"pad": 1, "envelope": 1, "step_level": 1}
    )

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
    assert {name: annotation.label for name, annotation in owned.items()} == {
        "m_pad0_width": "16.7",
        "m_pad0_length": "31",
        "m_locx0": "26.5",
        "m_locy0": "29.4",
    }
    assert {key["parameter_id"] for name in owned for key in drawing.measurement_keys(name)} == {
        "pad_width.length",
        "pad_length.length",
        "location_pad.location",
    }
    assert drawing.get_annotation("dim_detail_a_step1").label == "26"
    assert drawing.lint() == []


def test_removing_the_recovered_pad_size_reports_the_real_requirement():
    drawing = build_drawing(_ISSUE_909, detail_view=True)
    drawing.remove("m_pad0_length")

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
        if name.startswith(("m_pad", "m_loc"))
    } == {
        name: direct.get_annotation(name).label
        for name in direct.annotations()
        if name.startswith(("m_pad", "m_loc"))
    }
