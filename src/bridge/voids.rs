//! Compatibility with quiddity 0.4.5: a cavity's skin can also be published as holes.
//! Keep the cavity and its physical bounds, and remove the contradictory opening claims.
use std::collections::BTreeSet;

use haecceity::Part;
use serde_json::json;

use super::{Feature, HolePattern};

pub(crate) fn reconcile(part: &Part, features: &mut Vec<Feature>, patterns: &mut Vec<HolePattern>) {
    let mut claimed = BTreeSet::new();
    for f in features.iter_mut().filter(|f| f.kind == "interior_void") {
        claimed.extend(f.faces.iter().copied());
        if let Some((&first, rest)) = f.faces.split_first() {
            let mut bounds = part.face_bounds(first);
            for &face in rest {
                let b = part.face_bounds(face);
                for k in 0..3 {
                    bounds.min[k] = bounds.min[k].min(b.min[k]);
                    bounds.max[k] = bounds.max[k].max(b.max[k]);
                }
            }
            // Derived in the file's frame, not the recogniser's optional working frame.
            f.parameters
                .insert("draftwright_bounds".into(), json!([bounds.min, bounds.max]));
        }
    }
    if claimed.is_empty() {
        return;
    }
    let removed: BTreeSet<_> = features
        .iter()
        .filter(|f| f.kind == "hole" && f.faces.iter().any(|face| claimed.contains(face)))
        .map(|f| f.id.clone())
        .collect();
    // A pattern with a missing member no longer proves its count, pitch or centre.
    // Any genuine open holes in a mixed pattern remain independently dimensionable.
    patterns.retain(|p| !p.members.iter().any(|id| removed.contains(id)));
    features.retain(|f| {
        !(removed.contains(&f.id)
            || f.kind == "hole_pattern" && f.faces.iter().any(|face| claimed.contains(face)))
    });
    for f in features.iter_mut().filter(|f| f.kind == "hole") {
        if !patterns.iter().any(|p| p.members.contains(&f.id)) {
            f.fingerprint.pattern = None;
        }
    }
}
