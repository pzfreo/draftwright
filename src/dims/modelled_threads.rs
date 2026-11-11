//! Physical threads get one designation; conventional ink depicts their extent.
use super::*;
use crate::modelled_threads::{Thread, threads};

pub(super) const PLANNER: Planner = Planner {
    kinds: &["modelled_thread"],
    wants,
};
fn wants(cx: &Context) -> (Vec<Want>, Vec<Problem>) {
    let mut out = Vec::new();
    let mut problems = Vec::new();
    for f in cx
        .analysis
        .features
        .iter()
        .filter(|f| f.kind == "modelled_thread")
    {
        let t = match Thread::read(f) {
            Ok(t) => t,
            Err(why) => {
                problems.push(not_depicted(f, why));
                continue;
            }
        };
        let k = crate::bridge::holes::principal(t.axis).unwrap_or_else(|| {
            (0..3)
                .max_by(|&a, &b| t.axis[a].abs().total_cmp(&t.axis[b].abs()))
                // Invariant: The iterator is over the fixed three coordinate axes.
                .expect("three coordinates")
        });
        let (text, standard) = t.designation();
        if !standard {
            problems.push(unmatched(f));
        }
        let at = t.at((t.span[0] + t.span[1]) / 2.);
        let sites = (0..3)
            .filter(|&j| j != k)
            .flat_map(|j| {
                [-1., 1.].map(move |side| {
                    let radial = [0, 1, 2].map(|i| f64::from(i == j) - t.axis[j] * t.axis[i]);
                    // Invariant: j excludes the dominant axis k, so this radial component is nonzero.
                    let radial = haecceity::geom::unit(radial).expect("transverse coordinate");
                    (
                        haecceity::geom::add(at, haecceity::geom::scale(radial, side * t.crest())),
                        0.,
                    )
                })
            })
            .collect();
        let mut views = super::envelope::profile_views(k);
        // Separate coaxial occurrences must remain distinguishable by axial
        // position; end-on leaders would otherwise share exactly the same rim.
        let coaxial = threads(cx.analysis).any(|other| {
            other.feature.id != f.id
                && haecceity::geom::dot(other.axis, t.axis).abs() > 1. - 1e-8
                && haecceity::geom::norm(haecceity::geom::cross(
                    haecceity::geom::sub(other.on, t.on),
                    t.axis,
                )) < 1e-6
        });
        if t.internal && !coaxial {
            views.insert(0, end_on(k));
        }
        out.push(Want { units: cx.spec.units,
            id:want_id(&f.id,"thread"), views,value:t.major,
            terms:vec![text],
            measurements:vec![Measurement {feature:f.id.clone(),parameter:"thread".into(),value:t.major},Measurement {feature:f.id.clone(),parameter:"thread.length".into(),value:t.span[1]-t.span[0]}],
            basis:"ISO 6410; designation inferred from a unique ISO 261 diameter/pitch match; thread extent is depicted; trimmed span retained only as measurement evidence; features/<id>/modelled_thread overrides depiction and designation".into(),
            shape:Shape::Callout {sites,arcs:Vec::new()},turning:None,
        });
    }
    (out, problems)
}
/// A thread recognition refused is no hole, boss or thread on the sheet (#218): the reader
/// leaves out holes on its faces, so the gap is reported here, against those faces, until a
/// declared feature or a thread (PMI or authored) covers them.
pub(crate) fn refused(a: &Analysis, s: &Spec) -> Vec<Problem> {
    let Some(r) = &a.recognition else {
        return Vec::new();
    };
    r.refusals
        .iter()
        .enumerate()
        .filter(|(_, r)| r.subtype == "ModelledThreadRefusal")
        .filter(|(_, r)| {
            !a.features.iter().any(|f| {
                f.id.starts_with("f:declared:") && f.faces.iter().any(|i| r.faces.contains(i))
            }) && !threaded(a, s, &r.faces)
        })
        .map(|(i, r)| {
            let reason = r
                .parameters
                .get("reason")
                .and_then(Value::as_str)
                .unwrap_or("unstated")
                .replace('_', " ");
            Problem {
                id: format!("p:modelled_thread_refused:recognition:refusal:{i}"),
                code: "modelled_thread_refused".into(),
                severity: "warning".into(),
                message: format!(
                    "Recognition refused a modelled thread on {} faces ({reason}), so its size and designation are not stated and its modelled edges remain drawn. Declare the feature in the Spec's feature_declarations and state its thread (features/<id>/thread).",
                    r.faces.len()
                ),
                refs: vec![
                    format!("recognition:refusal:{i}"),
                    format!("faces {:?}", r.faces),
                ],
                ..Default::default()
            }
        })
        .collect()
}
/// Whether a PMI or Spec-authored thread (merged into the PMI) sits on any of `faces`.
fn threaded(a: &Analysis, s: &Spec, faces: &[usize]) -> bool {
    match &a.pmi {
        Some(crate::pmi::Reading::Read(r)) if s.pmi != crate::model::PmiMode::Ignore => r
            .records
            .iter()
            .any(|r| r.kind.ends_with("thread") && r.faces.iter().any(|i| faces.contains(i))),
        _ => false,
    }
}
fn not_depicted(f: &crate::bridge::Feature, why: &str) -> Problem {
    Problem {
        id: format!("p:modelled_thread_not_depicted:{}", f.id),
        code: "modelled_thread_not_depicted".into(),
        severity: "info".into(),
        message: format!(
            "ISO 6410 conventional thread depiction is unavailable: {why}. The source modelled edges remain drawn."
        ),
        subjects: vec![f.id.clone()],
        ..Default::default()
    }
}
fn unmatched(f: &crate::bridge::Feature) -> Problem {
    Problem {id:format!("p:thread_designation_measured:{}",f.id),code:"thread_designation_measured".into(),severity:"info".into(),message:"No unique ISO 261 diameter/pitch match within the documented inference tolerance; the callout states measured diameter and pitch. Set features/<id>/modelled_thread/designation to author the intended designation.".into(),subjects:vec![f.id.clone()],..Default::default()}
}

/// The ISO 261 coarse sizes a part's title, product or file name gives as `M<d>` (`M3 x 12`,
/// `Hex-Standoff-M3-12mm`), as a fastener names its thread. `binding.part` is the file stem, or
/// for an assembly member its product name, the stem then being `source_stem`.
fn named_sizes(a: &Analysis, s: &Spec) -> Vec<f64> {
    let names = [
        s.sheet.title.get("title").map(String::as_str),
        a.product_name.as_deref(),
        Some(a.binding.part.as_str()),
        a.source_stem.as_deref(),
    ];
    let mut sizes = Vec::new();
    for token in names
        .into_iter()
        .flatten()
        .flat_map(|n| n.split(|c: char| !c.is_ascii_alphanumeric() && c != '.'))
    {
        let Some(rest) = token.strip_prefix(['M', 'm']) else {
            continue;
        };
        let end = rest
            .find(|c: char| !c.is_ascii_digit() && c != '.')
            .unwrap_or(rest.len());
        // `M3`, `M3x12`; not `mm`, `M3a` or `Model`.
        if !(rest[end..].is_empty() || rest[end..].starts_with(['x', 'X'])) {
            continue;
        }
        if let Ok(d) = rest[..end].parse::<f64>()
            && crate::spec_notes::COARSE.iter().any(|(c, _)| *c == d)
        {
            sizes.push(d);
        }
    }
    sizes
}

/// A fastener named by a metric size whose unthreaded hole or boss is at exactly that major
/// diameter is probably a thread modelled smooth (#218). Its measured callout is kept, since no
/// thread is proved, and the doubt is noted as info: a plain bore (a dowel hole) is equally
/// possible, so nothing is asked of it. Authoring the feature's `thread`, or a PMI thread on its
/// faces, settles it.
pub(crate) fn possible_threads(a: &Analysis, s: &Spec) -> Vec<Problem> {
    let fmt = |v| crate::dims::fmt_in(v, s.units);
    let sizes = named_sizes(a, s);
    let patterns = a
        .recognition
        .as_ref()
        .map_or(&[][..], |r| r.patterns.as_slice());
    a.features
        .iter()
        .filter(|f| matches!(f.kind.as_str(), "hole" | "boss"))
        .filter(|f| s.features.get(&f.id).and_then(|f| f.thread.as_ref()).is_none())
        .filter(|f| !threaded(a, s, &f.faces))
        .filter_map(|f| {
            let d = f.parameters.get("diameter")?.as_f64()?;
            let size = sizes.iter().find(|&&n| (n - d).abs() <= 0.02 + 1e-10)?;
            // A patterned hole's thread is set once, on its pattern.
            let owner = patterns
                .iter()
                .find(|p| p.members.contains(&f.id))
                .map_or(&f.id, |p| &p.id);
            Some(Problem {
                id: format!("p:possible_thread:{}", f.id),
                code: "possible_thread".into(),
                severity: "info".into(),
                message: format!(
                    "{} is at the major diameter of the M{} this part is named for, but no thread is modelled or recognised, so the sheet states it plain. If it is threaded, set features/{}/thread to its designation.",
                    crate::wording::capital(&crate::wording::feature(f)),
                    fmt(*size),
                    owner
                ),
                subjects: vec![f.id.clone()],
                ..Default::default()
            })
        })
        .collect()
}

/// Apply intent and PMI precedence after the immutable automatic wants memo.
pub(super) fn apply(a: &Analysis, s: &Spec, out: &mut Vec<Want>, problems: &mut Vec<Problem>) {
    let manufacturing = if s.pmi == crate::model::PmiMode::Ignore
        || !a.features.iter().any(|f| f.kind == "modelled_thread")
    {
        crate::pmi_lowering::Manufacturing::default()
    } else {
        crate::pmi_lowering::manufacturing(a)
    };
    for t in threads(a) {
        let setting = s
            .features
            .get(&t.feature.id)
            .and_then(|f| f.modelled_thread.as_ref());
        if !t.enabled(s) || s.sheet.thread_depiction == Some(false) {
            problems.push(not_depicted(
                t.feature,
                "conventional depiction is disabled by Spec",
            ));
        }
        let id = want_id(&t.feature.id, "thread");
        let source = if s.pmi == crate::model::PmiMode::Ignore {
            false
        } else {
            match &a.pmi {
                Some(crate::pmi::Reading::Read(r)) => r.records.iter().any(|r| {
                    r.kind.ends_with("thread")
                        && r.faces.iter().any(|i| t.feature.faces.contains(i))
                        && manufacturing.owned.iter().any(|o| {
                            o.source == r.source_id
                                && out.iter().any(|w| {
                                    w.measurements
                                        .iter()
                                        .any(|m| m.feature == o.id && m.parameter == "thread")
                                })
                        })
                }),
                _ => false,
            }
        };
        if source {
            problems.retain(|p| p.id != format!("p:thread_designation_measured:{}", t.feature.id));
            out.retain(|w| w.id != id);
            continue;
        }
        if let Some(text) = setting.and_then(|s| s.designation.as_ref()) {
            if let Some(w) = out.iter_mut().find(|w| w.id == id) {
                w.terms = vec![text.clone()];
                w.basis =
                    "ISO 6410; designation authored verbatim in Spec; thread extent is depicted"
                        .into();
            }
            problems.retain(|p| p.id != format!("p:thread_designation_measured:{}", t.feature.id));
        }
    }
}

/// Use the same circular-rim and generator attachment contract as other
/// cylindrical callouts. Four fixed mid-span points unnecessarily constrain
/// the shared router and can force leaders across neighbouring callouts.
pub(super) fn route_profile(a: &Analysis, w: &mut Want, view: &str) {
    if !matches!(w.shape, Shape::Callout { .. }) {
        return;
    }
    let Some(t) = w
        .measurements
        .iter()
        .filter(|m| m.parameter == "thread")
        .find_map(|m| {
            a.features
                .iter()
                .find(|f| f.id == m.feature && f.kind == "modelled_thread")
                .and_then(|f| Thread::read(f).ok())
        })
    else {
        return;
    };
    let Some((_, toward)) = TOWARD.iter().find(|(name, _)| *name == view) else {
        return;
    };
    let along = haecceity::geom::dot(t.axis, *toward);
    let sites = if along.abs() > 1. - 1e-6 {
        vec![(t.at((t.span[0] + t.span[1]) / 2.), t.crest())]
    } else {
        // Invariant: The end-on branch was excluded, so the normalized axis cross view is nonzero.
        let radial = haecceity::geom::unit(haecceity::geom::cross(t.axis, *toward)).unwrap();
        [t.span[0], (t.span[0] + t.span[1]) / 2., t.span[1]]
            .into_iter()
            .flat_map(|z| {
                [-1., 1.].map(move |side| {
                    (
                        haecceity::geom::add(
                            t.at(z),
                            haecceity::geom::scale(radial, side * t.crest()),
                        ),
                        0.,
                    )
                })
            })
            .collect()
    };
    w.shape = Shape::Callout {
        sites,
        arcs: Vec::new(),
    };
}
