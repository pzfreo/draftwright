//! Planning integration for independently read rectangular through-notch geometry.
use crate::lint::rectangular_notches::{axes, location_evidence_from_notes, note_id};
pub(crate) use crate::lint::rectangular_notches::{read, tag};
use crate::model::Analysis;

pub(crate) fn notes(a: &Analysis, spec: &crate::model::Spec) -> Vec<crate::notes::NoteSpec> {
    if crate::dims::pmi_policy::strict(a, spec)
        || spec.dimension_source == crate::model::DimensionSource::Authored
    {
        return Vec::new();
    }
    a.features
        .iter()
        .filter(|f| f.kind == "rectangular_through_notch")
        .filter(|f| !spec.annotations.get(&note_id(f)).is_some_and(|s| s.omit))
        .filter_map(|f| {
            let n = read(a, f).ok()?;
            let [x, y] = axes(&n);
            Some(crate::notes::NoteSpec {
                id: note_id(f),
                text: format!(
                    "NOTCH {} MOUTH CENTRE: {} FROM LEFT; {} FROM BOTTOM IN {} VIEW.",
                    tag(f),
                    crate::dims::fmt(n.mouth[x] - a.envelope[0][x]),
                    crate::dims::fmt(n.mouth[y] - a.envelope[0][y]),
                    crate::dims::end_on(n.axis).to_uppercase()
                ),
            })
        })
        .collect()
}

pub(crate) fn note_provenance(a: &Analysis, notes: &mut [crate::notes::PlannedNote]) {
    for f in a
        .features
        .iter()
        .filter(|f| f.kind == "rectangular_through_notch")
    {
        if let Ok(n) = read(a, f)
            && let Some(note) = notes.iter_mut().find(|note| note.id == note_id(f))
        {
            note.default = true;
            note.basis = "tagged rectangular through-notch mouth centre, from the carrying view's left and bottom envelope extents".into();
            note.measurements = axes(&n)
                .into_iter()
                .map(|k| crate::dims::Measurement {
                    feature: f.id.clone(),
                    parameter: format!("location.length.{}", crate::lint::location::AXES[k]),
                    value: n.mouth[k] - a.envelope[0][k],
                })
                .collect();
        }
    }
}

pub(crate) fn planned_location_evidence(
    a: &Analysis,
    plan: &crate::model::Plan,
    marks: &[crate::lint::Mark],
) -> Vec<crate::lint::Mark> {
    location_evidence_from_notes(a, marks, |id| {
        Some(
            plan.notes
                .iter()
                .find(|note| note.id == id && note.placed.is_some())?
                .rows
                .join(" "),
        )
    })
}
