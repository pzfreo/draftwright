//! A publication-only permutation: placement, routes, paper and identities stay legacy.
use super::*;

const BASIS: &str = "; tags numbered spatially after layout (rows within 0.001 model mm, then columns from the origin)";
const SAME_ROW: f64 = 1e-3;

pub(crate) fn coordinate_name(t: &HoleTable, tag: &str, axis: &str) -> String {
    format!(
        "table coordinate {tag} {axis} in the {} view",
        t.view
            .as_deref()
            .unwrap_or("v:plan")
            .trim_start_matches("v:")
    )
}

/// The two generated prose consumers: hole-mark attachment bases and PMI findings.
/// Match a complete, view-qualified reference, never a bare tag or a stable ID.
/// Apply the permutation simultaneously so C1 -> C2 -> C3 cannot cascade.
fn prose(plan: &mut Plan, reverse: bool, only: Option<&BTreeSet<String>>) {
    let mut edits = Vec::new();
    for t in plan.hole_table.iter().chain(&plan.hole_tables) {
        if only.is_some_and(|ids| !ids.contains(&t.id)) {
            continue;
        }
        let view = t
            .view
            .as_deref()
            .unwrap_or("v:plan")
            .trim_start_matches("v:");
        for row in &t.rows {
            if row.key() == row.tag {
                continue;
            }
            let (old, new) = if reverse {
                (row.tag.as_str(), row.key())
            } else {
                (row.key(), row.tag.as_str())
            };
            edits.push((
                format!("hole table tag {old} in the {view} view"),
                format!("hole table tag {new} in the {view} view"),
            ));
            for axis in ["x", "y", "z"] {
                edits.push((coordinate_name(t, old, axis), coordinate_name(t, new, axis)));
            }
        }
    }
    for text in plan
        .gdt
        .iter_mut()
        .map(|g| &mut g.basis)
        .chain(plan.problems.iter_mut().map(|p| &mut p.message))
    {
        let mut matches: Vec<_> = edits
            .iter()
            .flat_map(|(old, new)| {
                text.match_indices(old)
                    .map(move |(i, _)| (i, old.len(), new))
            })
            .collect();
        matches.sort_by_key(|(i, len, _)| (*i, std::cmp::Reverse(*len)));
        if matches.is_empty() {
            continue;
        }
        let mut next = String::new();
        let mut start = 0;
        for (i, len, new) in matches {
            if i < start {
                continue;
            }
            next.push_str(&text[start..i]);
            next.push_str(new);
            start = i + len;
        }
        next.push_str(&text[start..]);
        *text = next;
    }
}

fn family(tag: &str) -> &str {
    tag.find(|c: char| c.is_ascii_digit())
        .map_or("", |i| &tag[..i])
}

fn width(tag: &str) -> f64 {
    let ink = crate::dims::label_ink(&[tag.into()], crate::dims::font());
    (ink.max[0] - ink.min[0]).max(tag.len() as f64 * crate::dims::TEXT * 0.6)
}

fn tag_box(b: &Balloon) -> Rect {
    let w = width(&b.tag);
    [
        b.at[0] - w / 2.,
        b.at[1] - crate::dims::TEXT / 2.,
        b.at[0] + w / 2.,
        b.at[1] + crate::dims::TEXT / 2.,
    ]
}

/// Rewrite cells in their existing slots, without calling the table layouter.
fn cells(t: &mut HoleTable) {
    let headers = if t.view.is_some() { 2 } else { 1 };
    let mut rows = t.rows.iter();
    for b in &mut t.blocks {
        for (r, cell) in b.rows.iter_mut().enumerate().skip(headers) {
            if let Some(row) = rows.next() {
                cell.clone_from(&row.cells);
                if let Some(p) = &mut b.placed {
                    for (c, text) in cell.iter().enumerate() {
                        if text.trim().is_empty() {
                            p.cells_at[r][c] = None;
                        } else if p.cells_at[r][c].is_none() {
                            p.cells_at[r][c] = Some([
                                (p.columns[c] + p.columns[c + 1]) / 2.,
                                p.cells[3] - (r as f64 + 0.5) * ROW,
                            ]);
                        }
                    }
                }
            }
        }
    }
}

fn belongs(b: &Balloon, t: &HoleTable) -> bool {
    b.certifying
        && b.view == t.view.as_deref().unwrap_or("v:plan")
        && if t.view.is_some() {
            b.id.starts_with(&format!("{}/tag/", t.id))
        } else {
            b.id.starts_with("balloon_plan_")
        }
}

fn names(t: &HoleTable, balloons: &mut [Balloon]) {
    let map: BTreeMap<_, _> = t.rows.iter().map(|r| (r.key(), r.tag.as_str())).collect();
    for b in balloons.iter_mut().filter(|b| belongs(b, t)) {
        if !b
            .features
            .iter()
            .any(|f| t.rows.iter().any(|r| &r.feature == f))
        {
            continue;
        }
        // Keep the compound tag's legacy member order and transported vertical bounds.
        let label = b
            .tag
            .split('/')
            .map(|s| map.get(s).copied().unwrap_or(s))
            .collect::<Vec<_>>()
            .join("/");
        let delta = (width(&label) - width(&b.tag)) / 2.;
        if delta != 0.
            && let Some(bounds) = &mut b.label_box
        {
            b.label_box_before = Some(*bounds);
            bounds[0] -= delta;
            bounds[2] += delta;
        }
        b.tag = label;
    }
}

/// Public recover(plan(...)) must feed the original presentation back into the solver.
pub(crate) fn restore(plan: &mut Plan) {
    prose(plan, true, None);
    for t in plan.hole_table.iter_mut().chain(&mut plan.hole_tables) {
        if !t.rows.iter().any(|r| r.tag_id.is_some()) {
            continue;
        }
        let reverse: BTreeMap<_, _> = t
            .rows
            .iter()
            .map(|r| (r.tag.clone(), r.key().to_string()))
            .collect();
        for b in plan.balloons.iter_mut().filter(|b| {
            belongs(b, t)
                && b.features
                    .iter()
                    .any(|f| t.rows.iter().any(|r| &r.feature == f))
        }) {
            b.tag = b
                .tag
                .split('/')
                .map(|s| reverse.get(s).map_or(s, String::as_str))
                .collect::<Vec<_>>()
                .join("/");
            if let Some(bounds) = b.label_box_before.take() {
                b.label_box = Some(bounds);
            }
        }
        for r in &mut t.rows {
            if let Some(id) = r.tag_id.take() {
                r.tag = id;
                r.cells[0] = r.tag.clone();
            }
        }
        let order = std::mem::take(&mut t.row_order_before);
        if !order.is_empty() {
            t.rows
                .sort_by_key(|row| order.iter().position(|id| id == row.key()));
        }
        cells(t);
        t.basis = t.basis.replace(BASIS, "").replace("; tags numbered spatially after layout (rows within 0.000001 model mm, then columns from the origin)", "");
    }
    plan.problems
        .retain(|p| p.code != "hole_table_tag_order_retained");
}

fn reading_order(ordered: &mut [(usize, f64, f64)], rows: &[HoleRow]) {
    ordered.sort_by(|a, b| {
        a.2.total_cmp(&b.2)
            .then_with(|| a.1.total_cmp(&b.1))
            .then_with(|| rows[a.0].feature.cmp(&rows[b.0].feature))
    });
    // Anchor each row at its lowest Y: no non-transitive pairwise tolerance comparator.
    let mut first = 0;
    while first < ordered.len() {
        let end = (first + 1..ordered.len())
            .find(|&j| ordered[j].2 - ordered[first].2 > SAME_ROW)
            .unwrap_or(ordered.len());
        ordered[first..end].sort_by(|a, b| {
            a.1.total_cmp(&b.1)
                .then_with(|| a.2.total_cmp(&b.2))
                .then_with(|| rows[a.0].feature.cmp(&rows[b.0].feature))
        });
        first = end;
    }
}

fn permute(analysis: &Analysis, plan: &Plan, t: &mut HoleTable) -> bool {
    t.row_order_before = t.rows.iter().map(|row| row.key().to_owned()).collect();
    let Some(view) = plan
        .views
        .iter()
        .find(|v| v.id == t.view.as_deref().unwrap_or("v:plan"))
    else {
        return false;
    };
    let frame = Frame::new(analysis, view);
    let origin = frame.view.map(t.origin.unwrap_or(analysis.envelope[0]));
    let read: BTreeMap<_, _> = holes::read(analysis)
        .into_iter()
        .filter_map(|(f, h)| Some((f.id.as_str(), h.ok()?)))
        .collect();
    let mut groups: BTreeMap<String, Vec<usize>> = BTreeMap::new();
    for (i, r) in t.rows.iter().enumerate() {
        groups.entry(family(&r.tag).into()).or_default().push(i);
    }
    let mut changed = false;
    for slots in groups.values() {
        let mut ordered = Vec::new();
        for &i in slots {
            let Some(h) = read.get(t.rows[i].feature.as_str()) else {
                return false;
            };
            let p = frame.view.map(h.at);
            ordered.push((i, p[0] - origin[0], p[1] - origin[1]));
        }
        reading_order(&mut ordered, &t.rows);
        let rows: Vec<_> = ordered.iter().map(|&(i, _, _)| t.rows[i].clone()).collect();
        let labels: Vec<_> = slots.iter().map(|&i| t.rows[i].tag.clone()).collect();
        for ((&i, mut row), label) in slots.iter().zip(rows).zip(labels) {
            changed |= row.tag != label;
            row.tag_id = (row.tag != label).then(|| row.tag.clone());
            row.tag = label;
            row.cells[0] = row.tag.clone();
            t.rows[i] = row;
        }
    }
    changed
}

fn blocked(
    analysis: &Analysis,
    before: &Plan,
    after: &Plan,
    t: &HoleTable,
) -> Result<(), &'static str> {
    let old_table = before
        .hole_table
        .iter()
        .chain(&before.hole_tables)
        .find(|a| a.id == t.id)
        .ok_or("the original table is missing")?;
    if t.rows.len() != old_table.rows.len()
        || t.rows.iter().any(|r| {
            !old_table
                .rows
                .iter()
                .any(|old| old.key() == r.key() && old.feature == r.feature)
        })
    {
        return Err("an original table row is missing");
    }
    let view = after
        .views
        .iter()
        .find(|v| v.id == t.view.as_deref().unwrap_or("v:plan"))
        .ok_or("the tag's carrying view is missing")?;
    let page = std::iter::once(&after.sheet)
        .chain(&after.sheets)
        .find(|s| s.number == view.sheet)
        .ok_or("the tag's carrying sheet is missing")?
        .page;
    if t.rows.iter().any(|row| {
        !after
            .balloons
            .iter()
            .any(|b| belongs(b, t) && b.features.contains(&row.feature))
    }) {
        return Err("a table row's certifying tag is missing");
    }
    for b in after.balloons.iter().filter(|b| {
        belongs(b, t)
            && b.features
                .iter()
                .any(|f| t.rows.iter().any(|r| &r.feature == f))
    }) {
        let old = before
            .balloons
            .iter()
            .find(|a| a.id == b.id)
            .ok_or("the original tag is missing")?;
        let (a, z) = (
            old.label_box.unwrap_or_else(|| tag_box(old)),
            b.label_box.unwrap_or_else(|| tag_box(b)),
        );
        if z[2] - z[0] <= a[2] - a[0] + 1e-9 {
            continue;
        }
        let sheet = view.sheet;
        if z[0] < crate::layout::FRAME
            || z[1] < crate::layout::FRAME
            || z[2] > page[0] - crate::layout::FRAME
            || z[3] > page[1] - crate::layout::FRAME
        {
            return Err("a wider tag would cross the sheet frame");
        }
        if b.radius > 0.
            && ((z[2] - z[0]) / 2. >= b.radius - RING || (z[3] - z[1]) / 2. >= b.radius - RING)
        {
            return Err("a wider tag would cross its fixed ring");
        }
        if crate::dims::inspection::tag_blocker(after, sheet, &b.id, z).is_some()
            || b.leader.is_some_and(|l| crosses(l, z))
        {
            return Err("a wider tag would collide with fixed annotation ink or a leader");
        }
        if after.views.iter().any(|v| {
            v.id != b.view && v.sheet == sheet && overlap(z, Frame::new(analysis, v).outline)
        }) {
            return Err("a wider tag would collide with another view");
        }
        let frame = Frame::new(analysis, view);
        let read: BTreeMap<_, _> = holes::read(analysis)
            .into_iter()
            .filter_map(|(f, h)| Some((f.id.as_str(), h.ok()?)))
            .collect();
        let (edges, curved) = model_ink(analysis, view, &frame, &read);
        if edges.iter().any(|l| crosses(*l, z))
            || curved.iter().any(|q| overlap(z, *q) && !within(z, *q))
            || read.values().any(|h| {
                h.direction
                    .iter()
                    .zip(view.direction)
                    .map(|(a, b)| a * b)
                    .sum::<f64>()
                    .abs()
                    > 0.999
                    && {
                        let c = frame.page(h.at);
                        let r = h.opening_diameter() * frame.s / 2.;
                        rim_gap(z, c, r) <= RING / 2.
                            && [z[0], z[2]]
                                .into_iter()
                                .flat_map(|x| [z[1], z[3]].map(move |y| (x - c[0]).hypot(y - c[1])))
                                .any(|d| d >= r - RING / 2.)
                    }
            })
        {
            return Err("a wider tag would collide with the projected part");
        }
    }
    if t.blocks.is_empty() {
        return Err("the fixed table blocks are missing");
    }
    for block in &t.blocks {
        if let Some(p) = &block.placed {
            for row in block.rows.iter().skip(if t.view.is_some() { 2 } else { 1 }) {
                for (c, text) in row.iter().enumerate() {
                    if p.columns.len() <= c + 1 {
                        return Err("the fixed column bounds are missing");
                    }
                    let ink = if t.view.is_some() && c == 1 {
                        size_ink(text, crate::dims::font())
                    } else {
                        tables::text_ink(text, crate::dims::font())
                    };
                    if ink.max[0] - ink.min[0]
                        > p.columns[c + 1] - p.columns[c] - 2. * tables::PAD + 1e-6
                    {
                        return Err("a reordered cell would collide with its fixed column rules");
                    }
                }
            }
        } else {
            return Err("the fixed table block placement is missing");
        }
    }
    // A row-bound inspection leader is already routed. Moving its carrier is not relabelling.
    if t.rows.iter().zip(&old_table.rows).any(|(a, b)| {
        a.key() != b.key()
            && after
                .inspection_balloons
                .iter()
                .any(|ring| ring.item == format!("{}/{}", t.id, b.key()))
    }) {
        return Err("row reordering would detach a fixed inspection leader from its requirement");
    }
    Ok(())
}

/// Called only at the outer public boundary, after every layout/routing/reporting pass.
pub(crate) fn finish(analysis: &Analysis, plan: &mut Plan) {
    let count = plan.hole_tables.len() + usize::from(plan.hole_table.is_some());
    if count == 0 {
        return;
    }
    // Read the same corrected features as the completed solver, without replanning.
    let effective = crate::spec_gdt::effective(analysis, &plan.spec)
        .unwrap_or(std::borrow::Cow::Borrowed(analysis));
    let analysis = effective.as_ref();
    let mut changed_tables = BTreeSet::new();
    for i in 0..count {
        let table = plan
            .hole_table
            .iter()
            .chain(&plan.hole_tables)
            .nth(i)
            .unwrap();
        if table.rows.iter().any(|r| r.tag_id.is_some()) {
            continue;
        }
        let mut table = table.clone();
        if !permute(analysis, plan, &mut table) {
            continue;
        }
        let mut trial = plan.clone();
        names(&table, &mut trial.balloons);
        cells(&mut table);
        *trial
            .hole_table
            .iter_mut()
            .chain(&mut trial.hole_tables)
            .nth(i)
            .expect("table cloned from this index") = table.clone();
        if let Err(why) = blocked(analysis, plan, &trial, &table) {
            plan.problems.push(Problem {
                id: format!("p:hole_table_tag_order_retained:{}", table.id),
                code: "hole_table_tag_order_retained".into(),
                severity: "info".into(),
                message: format!(
                    "The hole table keeps its legacy numbering: {why}. No item was moved."
                ),
                subjects: vec![table.id],
                ..Default::default()
            });
        } else {
            changed_tables.insert(table.id.clone());
            trial
                .hole_table
                .iter_mut()
                .chain(&mut trial.hole_tables)
                .nth(i)
                .expect("table cloned from this index")
                .basis += BASIS;
            *plan = trial;
        }
    }
    prose(plan, false, Some(&changed_tables));
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::OnceLock;

    fn fixture() -> &'static (Analysis, Plan) {
        static FIXTURE: OnceLock<(Analysis, Plan)> = OnceLock::new();
        FIXTURE.get_or_init(|| {
            let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("tests/fixtures/steerability/perforated-plate.step.gz");
            let (_, a) = crate::model::analyse_native(&path).unwrap();
            let s = serde_json::from_str(include_str!(
                "../../tests/fixtures/steerability/perforated-plate.spec.json"
            ))
            .unwrap();
            let mut p = crate::model::plan_recovered(&a, &s).unwrap();
            restore(&mut p);
            (a, p)
        })
    }

    fn value<T: serde::Serialize>(x: &T) -> serde_json::Value {
        serde_json::to_value(x).unwrap()
    }

    #[test]
    fn generated_prose_names_the_printed_tag_without_cascading_or_changing_ids() {
        let (a, original) = fixture();
        let mut p = original.clone();
        let table = &p.hole_tables[0];
        for row in &table.rows {
            p.problems.push(Problem {
                id: format!("test:{}", row.feature),
                message: format!(
                    "{}; stable cell {}/{}",
                    coordinate_name(table, &row.tag, "x"),
                    table.id,
                    row.key()
                ),
                ..Default::default()
            });
            p.gdt.push(serde_json::from_value(serde_json::json!({
                "id": format!("test:{}", row.feature), "kind":"label", "view":"v:plan",
                "side":"above", "source":"test", "faces":[], "cells":[], "label":"", "default":true,
                "basis":format!("over its hole table tag {} in the plan view", row.tag), "state":{"kind":"shown"}
            })).unwrap());
        }
        let before = value(&p);
        finish(a, &mut p);
        let table = &p.hole_tables[0];
        assert!(table.rows.iter().any(|r| r.tag_id.is_some()));
        for row in &table.rows {
            let tag = p
                .balloons
                .iter()
                .find(|b| b.certifying && b.features == [row.feature.clone()])
                .unwrap();
            assert_eq!(tag.tag, row.tag);
            let id = format!("test:{}", row.feature);
            let message = &p.problems.iter().find(|q| q.id == id).unwrap().message;
            assert_eq!(
                message,
                &format!(
                    "{}; stable cell {}/{}",
                    coordinate_name(table, &tag.tag, "x"),
                    table.id,
                    row.key()
                )
            );
            assert_eq!(
                p.gdt.iter().find(|g| g.id == id).unwrap().basis,
                format!("over its hole table tag {} in the plan view", tag.tag)
            );
        }
        let published = value(&p);
        finish(a, &mut p);
        assert_eq!(
            value(&p),
            published,
            "publishing twice cannot permute prose twice"
        );
        restore(&mut p);
        assert_eq!(value(&p), before);
    }

    #[test]
    fn missing_collision_evidence_keeps_legacy_numbering() {
        let (a, before) = fixture();
        let mut table = before.hole_tables[0].clone();
        assert!(permute(a, before, &mut table));
        for missing in [
            "table",
            "row",
            "view",
            "sheet",
            "tag",
            "original tag",
            "block",
        ] {
            let mut original = before.clone();
            let mut after = before.clone();
            let mut t = table.clone();
            names(&t, &mut after.balloons);
            match missing {
                "table" => original.hole_tables.clear(),
                "row" => original.hole_tables[0].rows.clear(),
                "view" => after.views.clear(),
                "sheet" => after.views.iter_mut().for_each(|v| v.sheet = 99),
                "tag" => after.balloons.clear(),
                "original tag" => original.balloons.clear(),
                "block" => t.blocks.clear(),
                _ => unreachable!(),
            }
            assert!(blocked(a, &original, &after, &t).is_err(), "{missing}");
        }
        let mut p = before.clone();
        p.balloons.clear();
        finish(a, &mut p);
        assert_eq!(p.hole_tables, before.hole_tables);
        assert!(p.problems.iter().any(|q| q.code == "hole_table_tag_order_retained" && q.message.contains("missing")));
    }

    #[test]
    fn recovery_restores_recorded_order_even_when_it_is_not_feature_or_tag_order() {
        let (a, original) = fixture();
        let mut original = original.clone();
        original.hole_tables[0].rows.rotate_left(2);
        cells(&mut original.hole_tables[0]);
        let mut p = original.clone();
        let mut table = p.hole_tables[0].clone();
        assert!(permute(a, &p, &mut table));
        assert_eq!(
            table.row_order_before,
            original.hole_tables[0]
                .rows
                .iter()
                .map(|r| r.key().to_string())
                .collect::<Vec<_>>()
        );
        names(&table, &mut p.balloons);
        cells(&mut table);
        p.hole_tables[0] = table;
        p = serde_json::from_value(value(&p)).unwrap();
        restore(&mut p);
        assert_eq!(value(&p), value(&original));
    }

    #[test]
    fn public_recover_round_trips_other_hole_tables_including_a_renumbered_one() {
        let mut renumbered = 0;
        for file in [
            "parts/scattered_holes.step",
            "recognition/issues/counterbored_block.step",
        ] {
            let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("tests/fixtures")
                .join(file);
            let (_, a) = crate::model::analyse_native(&path).unwrap();
            let spec = crate::model::Spec {
                hole_table: Setting::Always,
                ..Default::default()
            };
            // The public scheduled-plan API installs tables before recovery.
            let planned = crate::model::plan_escalated(&a, &spec).unwrap();
            assert!(!planned.hole_tables.is_empty(), "{file}");
            renumbered += planned
                .hole_tables
                .iter()
                .filter(|t| !t.row_order_before.is_empty())
                .count();
            let transported: Plan = serde_json::from_value(value(&planned)).unwrap();
            let mut legacy = transported.clone();
            restore(&mut legacy);
            let expected = crate::model::recover(&a, &spec, legacy).unwrap();
            let recovered = crate::model::recover(&a, &spec, transported).unwrap();
            assert_eq!(
                value(&recovered),
                value(&expected),
                "{file}: recover(plan(...))"
            );
        }
        assert!(renumbered > 0);
    }

    #[test]
    fn sub_micron_row_noise_does_not_override_printed_column_order() {
        let (_, p) = fixture();
        let mut points = [(0, 20., 0.), (1, 10., 0.0005), (2, 0., 0.002)];
        reading_order(&mut points, &p.hole_tables[0].rows);
        assert_eq!(points.map(|p| p.0), [1, 0, 2]);
    }

    #[test]
    fn saved_presentation_metadata_matches_the_plan_schema() {
        let (a, original) = fixture();
        let mut p = original.clone();
        finish(a, &mut p);
        let schema = serde_json::from_str(include_str!("../../schemas/plan.json")).unwrap();
        assert!(p.hole_tables.iter().any(|t| !t.row_order_before.is_empty()));
        assert!(p.balloons.iter().any(|b| b.label_box_before.is_some()));
        crate::mcp::validate(&schema, &value(&p)).unwrap();
        let mut broken = value(&p);
        broken["hole_tables"][0]["rows"][0]["tag_id"] = serde_json::json!(7);
        assert!(crate::mcp::validate(&schema, &broken).is_err());
        let mut broken = value(&p);
        broken["balloons"][0]["label_box_before"] = serde_json::json!([1., 2.]);
        assert!(crate::mcp::validate(&schema, &broken).is_err());
    }

    #[test]
    fn final_labels_preserve_geometry_routes_and_identities_and_recover_legacy() {
        let (a, original) = fixture();
        let mut p = original.clone();
        finish(a, &mut p);
        assert!(
            p.hole_tables
                .iter()
                .any(|t| t.rows.iter().any(|r| r.tag_id.is_some())),
            "spatial labels must actually be applied"
        );
        assert_eq!(value(&p.sheet), value(&original.sheet));
        assert_eq!(value(&p.views), value(&original.views));
        assert_eq!(value(&p.dimensions), value(&original.dimensions));
        assert_eq!(value(&p.gdt), value(&original.gdt));
        assert_eq!(
            value(&p.inspection_balloons),
            value(&original.inspection_balloons)
        );
        for (b, old) in p.balloons.iter().zip(&original.balloons) {
            assert_eq!(
                (&b.id, &b.features, b.at, b.radius, b.leader),
                (&old.id, &old.features, old.at, old.radius, old.leader)
            );
        }
        for (t, old) in p.hole_tables.iter().zip(&original.hole_tables) {
            for row in &t.rows {
                let previous = old.rows.iter().find(|r| r.feature == row.feature).unwrap();
                assert_eq!(row.key(), previous.key());
                assert_eq!(row.measurements, previous.measurements);
            }
            for (b, old) in t.blocks.iter().zip(&old.blocks) {
                assert_eq!(value(&b.placed), value(&old.placed));
            }
        }
        p = serde_json::from_value(value(&p)).unwrap();
        restore(&mut p);
        assert_eq!(value(&p), value(original));
    }

    #[test]
    fn equal_width_relabelling_keeps_transported_bounds_bit_for_bit() {
        let (a, original) = fixture();
        let mut p = original.clone();
        let marker = p.balloons.iter_mut().find(|b| b.tag == "C5").unwrap();
        let id = marker.id.clone();
        let bounds = marker.label_box.as_mut().unwrap();
        // Translation can leave a stored bound one ULP from at +/- half-height (FTC10).
        bounds[3] = f64::from_bits(bounds[3].to_bits() - 1);
        let before = *bounds;
        finish(a, &mut p);
        let marker = p.balloons.iter().find(|b| b.id == id).unwrap();
        assert_ne!(marker.tag, "C5");
        assert_eq!(marker.tag.len(), 2);
        assert_eq!(marker.label_box, Some(before));
        assert!(marker.label_box_before.is_none());
    }

    #[test]
    fn a_wider_tag_at_the_sheet_frame_rolls_back_the_whole_table() {
        let (a, original) = fixture();
        let mut p = original.clone();
        let mut t = p.hole_tables[0].clone();
        assert!(permute(a, &p, &mut t));
        let mut candidate = p.balloons.clone();
        names(&t, &mut candidate);
        let i = candidate
            .iter()
            .zip(&p.balloons)
            .position(|(z, b)| tag_box(z)[2] - tag_box(z)[0] > tag_box(b)[2] - tag_box(b)[0] + 1e-6)
            .unwrap();
        let width = tag_box(&p.balloons[i])[2] - tag_box(&p.balloons[i])[0];
        p.balloons[i].at[0] = p.sheet.page[0] - crate::layout::FRAME - width / 2. - 0.05;
        p.balloons[i].label_box = Some(tag_box(&p.balloons[i]));
        let before = p.clone();
        finish(a, &mut p);
        assert_eq!(p.hole_tables, before.hole_tables);
        assert_eq!(p.balloons, before.balloons);
        let info = p
            .problems
            .iter()
            .find(|p| p.code == "hole_table_tag_order_retained")
            .unwrap();
        assert_eq!(info.severity, "info");
        assert!(info.message.contains("sheet frame"));
    }
}
